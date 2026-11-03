# 开源部署手册：初始化、实时链路、持久化与访问控制

> 面向「拿到仓库后从零跑起来」和「公网安全部署」两种场景。
> 相关文档：[`AUTH.md`](../10-architecture/AUTH.md)（鉴权实现）、[`SECURITY.md`](../../SECURITY.md)（威胁模型）、
> [`DATA-DICTIONARY.md`](../30-data/DATA-DICTIONARY.md)（字段口径）。

---

## 0. 一页速览

| 项目 | 结论 |
|---|---|
| 代码许可 | Apache-2.0（`LICENSE`） |
| 运行形态 | 单进程 FastAPI + 原生前端，无构建步骤 |
| 默认监听 | `127.0.0.1:8900` |
| 访问控制 | **强制私钥**，服务端会话，会话级 Cookie；关浏览器即失效 |
| 实时数据 | 腾讯/新浪/东财/akshare → QuoteHub（1.5s）→ 快照（55s）→ SSE（30s） |
| 持久化 | `data/*.json` 快照 + SQLite + 日志；`data/` 不入库 |
| 最小复现 | Python 3.11 + `requirements.txt`，或 `docker compose up` |

---

## 1. 开源仓库与网址

### 1.1 仓库怎么发

本目录是**独立 git 仓库**（不是父工作区的子目录）。发布到 GitHub/Gitee：

```bash
cd dapeng-water
git remote add origin git@github.com:<你的账号>/dapeng-alpha.git
git push -u origin main
```

> 仓库已包含：`LICENSE`、`README.md`、`SECURITY.md`、`CONTRIBUTING.md`、
> `.env.example`、`.github/workflows/ci.yml`（pytest + gitleaks）。
> `.gitignore` 对 `data/` 是白名单制，**真实持仓/用户表/密钥不会入库**。

### 1.2 部署后的网址结构

| 路径 | 内容 | 是否需要私钥 |
|---|---|---|
| `/` | 首页（产品介绍） | 是 |
| `/dashboard.html` | 主控台（事实/判断/决策） | 是 |
| `/lhb-review.html` | 龙虎榜复盘 | 是 |
| `/project-docs.html` | 在线文档站 | 是 |
| `/admin.html` | 管理后台 | 是 |
| `/login` | 登录页 | 否 |
| `/api/health` | 健康检查（仅缓存计数） | 否 |
| `/api/*` 其余 | 业务接口 | 是 |

---

## 2. 目录结构

```text
dapeng-water/
├─ server/                    后端（FastAPI）
│  ├─ main.py                 应用装配：中间件顺序、路由注册、启动护栏
│  ├─ routers/                接口层（decision / market / kline / auth / lhb ...）
│  ├─ infra/                  auth（会话）/ settings / security（限流）/ health / logging
│  ├─ decision_*.py           决策层：快照、专家、分流、指标
│  ├─ market_*.py             数据层：覆盖、扫描、板块、情绪
│  ├─ lhb_*.py                龙虎榜抓取 / 归因 / 聚合 / 因素库
│  ├─ strategy_profile.py     8 分制候选池（独立纸面档案）
│  └─ providers / core / agents
├─ static/                    前端（原生 HTML/CSS/JS，无构建）
│  ├─ dashboard.html          主控台
│  ├─ login.html              登录页（自包含）
│  ├─ js/session-guard.js     会话守卫：401 回登录页
│  └─ css/ js/ docs-graph.json
├─ data/                      运行期目录（不入库，仅 5 个配置放行）
│  ├─ brain_config.json       主脑权重与阈值
│  ├─ decision_config.json    决策参数
│  ├─ decision_routing.json   分流规则
│  ├─ lhb_config.json         龙虎榜席位/因素配置
│  ├─ strategy_profile.json   8 分制策略档案
│  ├─ trading.db              模拟交易/持仓（SQLite）
│  ├─ market_coverage_latest.json  全市场快照（运行时生成）
│  └─ cache/ lhb_review/ review/
├─ docs/                      文档（架构/数据/运维/归档）
├─ scripts/                   启动、看门狗、验收脚本
├─ tests/                     单元与契约测试（517 项）
├─ tools/                     架构守卫、文档图谱、接口文档生成
├─ Dockerfile / docker-compose.yml
└─ .env.example  .gitignore  LICENSE  README.md  SECURITY.md
```

---

## 3. 初始化配置

### 3.1 首次启动会发生什么

1. 读取 `.env`（`server/main.py::_load_dotenv`），校验 `REQUIRE_ACCESS_KEY`：
   缺失或密钥 <32 位 → **直接拒绝启动**，不会以无密钥状态跑起来。
2. 创建 `data/`、`logs/`、`backups/` 目录；`data/` 下只有 5 个配置文件随仓库提供，
   其余（快照、SQLite、复盘结果）由程序生成。
3. 启动后台任务：
   - `coverage_worker`：启动 8 秒后拉全市场覆盖，交易时段每 **120s**、非交易时段每 **1800s**；
   - `brain`：龙虎榜复盘在收盘后窗口（15:35–23:00）幂等生成；
   - `quote_hub`：首次请求时惰性拉行情。
4. 静态页面无需构建，直接由 `StaticFiles` 提供。

> **首屏数据为空是正常的**：全市场快照要等 worker 第一轮完成（约 10–60 秒）。
> 决策台的「候选池」会显示「全市场快照尚未就绪」，而不是空候选。

### 3.2 配置文件分工

| 文件 | 管什么 | 改完如何生效 |
|---|---|---|
| `.env` | 密钥、会话、监听、限流、模型 Key | 重启进程 |
| `data/brain_config.json` | 主脑权重、跨层校验阈值、情绪参数 | mtime 热重载 |
| `data/decision_config.json` | 决策参数（仓位、闸门） | 热重载 |
| `data/decision_routing.json` | 分流规则（行情状态 → 仓位上限） | 热重载 |
| `data/lhb_config.json` | 席位名单、五维阈值、买卖因素规则 | 热重载 |
| `data/strategy_profile.json` | 8 分制：资金/止损/目标/评分门槛/预筛 | 热重载（缓存 180s） |

### 3.3 面板与数据的对应关系

| 面板区块 | 数据来源 | 刷新方式 |
|---|---|---|
| 决策结论 / 决策卡 | `/api/decision/snapshot` + `/api/decision/experts` | 进入页面拉一次 + SSE |
| 事实层（热度/资金/扫描/轮动） | `/api/market-dashboard`、`/api/decision/scan` | 60s 轮询 + SSE |
| 判断层（六专家/情报） | `/api/decision/experts`、`/api/brain/intel` | 60s 轮询 + SSE |
| 决策层（候选池/持仓/龙虎榜） | `/api/decision/strategy-profile`、`/api/automation/positions`、`/api/decision/lhb-review` | 候选池 120s 缓存、龙虎榜只拉一次 |
| 数据与溯源 | `/api/data-sources/health` | 30s |

---

## 4. 数据实时更新与持久化

### 4.1 实时链路

```text
腾讯行情 / 新浪 / 东财 push2 / akshare
        │  ① 拉取（带超时、重试、降级）
        ▼
   QuoteHub（TTL 1.5s，single-flight，防击穿）
        │  ② 组装
        ▼
   决策快照 packet（缓存 55s；无全市场覆盖时 5s）
        │  ③ 派生
        ├─ 情绪打分 / 六专家研判 / 分流规则
        ├─ 全市场扫描（横截面、轮动、资金流）
        └─ 8 分制候选池（预筛 → 拉日线 → 评分，缓存 180s）
        │  ④ 推送
        ▼
   SSE `/api/decision/stream?interval=30` → 前端增量重绘
```

**关键约定**：

- 行情只有一条入口（QuoteHub），页面不直接打上游；
- 上游失败时按「最近快照 → 陈旧降级 → 不可用 + reason」三级处理，**不编造数据**；
- 每个接口都带 `as_of` / `source` / `quality`，前端据此显示新鲜度。

### 4.2 刷新节奏

| 数据 | 频率 | 位置 |
|---|---|---|
| 实时报价 | 1.5s TTL，按需拉取 | QuoteHub |
| 决策快照 | 55s 缓存 | `decision_snapshot` |
| 全市场覆盖 | 交易时段 120s / 非交易 1800s | `coverage_worker` |
| SSE 推送 | 30s 一轮 | `/api/decision/stream` |
| 龙虎榜复盘 | 收盘后 15:35–23:00 幂等生成 | `brain` 任务 |
| 8 分制候选池 | 180s 缓存 | `strategy_profile` |
| 研判链路 | 按需（约 3–8s），各段自带缓存 | `/api/decision/pipeline` |

### 4.2.1 递进式研判链路

`GET /api/decision/pipeline` 把五段串起来，每段都返回 `sample / output / scope / degraded / reason`：

```text
数据源接入 → 市场广度（全 A 股） → 板块筛选（行业 Top5） → 个股筛选（预筛/日线/8 分制） → 研判团与主脑
                                                                        │
                                              ┌─────────────────────────┴─────────────────────────┐
                                              ▼                                                   ▼
                                  intraday：盘中清单（可买/观察/放弃/待开盘）      post_close：盘后复盘（漏斗 + 表现 + 偏差）
```

- **降级可见**：任一段不可用时 `status=degraded`，并列进 `degraded_stages`，页面顶部红字提示原因；
- **盘中判定**：开盘涨幅、现价与昨收、量比；VWAP 未接入时写进 `missing`，不假装满足；
- **盘后复盘**：候选日快照落 `data/strategy_profile/YYYY-MM-DD.json`，用下一交易日日线评估
  是否触及目标/止损、收盘收益与胜率；0 候选时解释「市场分 / 涨停跌停家数」，不只回空数组。

### 4.3 持久化清单

| 数据 | 位置 | 生命周期 |
|---|---|---|
| 全市场快照 | `data/market_coverage_latest.json` | 覆盖写；重启后作为兜底读取（受 `max_age` 约束） |
| 龙虎榜复盘 | `data/lhb_review/YYYY-MM-DD.json` + `latest.json` | 按日累积，不自动清理 |
| 模拟交易/持仓 | `data/trading.db`（SQLite WAL） | 长期；备份用 `/api/system/backup` |
| 决策轨迹 | `data/lineage.json`、`data/layer_crosscheck*.json` | 覆盖/追加 |
| 情绪与轮动历史 | `data/sentiment_history.json`、`sector_history.json` | 滚动追加 |
| 登录审计 | `logs/auth.log`（JSONL，5MB 轮转） | 只记 IP/动作，不含密钥 |
| 运行日志 | `logs/server.log`、`logs/watchdog.log` | 5MB 轮转 |

**重启后的行为**：内存缓存清空 → 快照按 55s 重建；全市场快照从磁盘兜底读取，
不会退回 447 只核心样本；**所有登录会话失效**（有意为之，不留长期票据）。

---

## 5. 启动方式

### 5.1 直接运行（开发）

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -c "import secrets;print(secrets.token_urlsafe(48))"   # 填入 ACCESS_KEY
python -m uvicorn server.main:app --host 127.0.0.1 --port 8900
```

### 5.2 带看门狗（长期运行，Windows）

```bat
start.bat            :: 前台启动 + 打开浏览器
start_silent.bat     :: 无窗口，日志写 server.log
python scripts\server_watchdog.py   :: 崩溃退避重启（内置每日备份）
```

### 5.3 Docker（推荐用于服务器）

```bash
cp .env.example .env        # 填 ACCESS_KEY / MOAT_ADMIN_TOKEN
docker compose up -d --build
# 访问 http://127.0.0.1:8900 → 跳转 /login
```

`docker-compose.yml` 把 `data/`、`logs/` 挂到宿主机；密钥通过 `.env` 注入，
也可改用 `ACCESS_KEY_FILE=/run/secrets/dapeng_key` + compose `secrets`。

---

## 5.4 让其他电脑访问

| 场景 | 做法 | 前置条件 |
|---|---|---|
| 同一局域网 | `.env` 设 `HOST=0.0.0.0` 与 `ALLOWED_HOSTS=<局域网IP>`，放行 TCP 8900 | **管理员权限**添加防火墙规则 |
| 公网（推荐反代） | Caddy/Nginx HTTPS 反代到 `127.0.0.1:8900` | 域名 + 证书；`COOKIE_SECURE=1`、`TRUST_PROXY=1` |
| 临时演示 | Cloudflare Tunnel / ngrok 等出站隧道 | 隧道客户端账号；等于把实例暴露到公网，务必先加固私钥 |

Windows 放行端口（**以管理员身份**打开终端执行）：

```text
New-NetFirewallRule -DisplayName 'DAPENG Alpha 8900' -Direction Inbound -Protocol TCP -LocalPort 8900 -Action Allow -Profile Any
```

> 网络被识别为「公用」时入站默认拒绝；上面 `-Profile Any` 可同时覆盖公用/专用。
> 验证：另一台电脑访问 `http://<主机局域网IP>:8900/`，应跳转 `/login`。
> 若返回 400（Host 校验失败），把该 IP 加进 `.env` 的 `ALLOWED_HOSTS` 后重启。
> 若返回 400（Host 校验失败），把该 IP 加进 `.env` 的 `ALLOWED_HOSTS` 后重启。

## 6. 访问控制：私钥登录

### 6.1 生效范围

| 对象 | 是否受保护 |
|---|---|
| 所有 HTML 页面（含 `/docs` 静态文档） | ✅ 未登录 302 → `/login` |
| 所有 `/api/*`（除下表） | ✅ 未登录 401 JSON |
| `/api/auth/{login,logout,status}`、`/api/health`、`/login` | ❌ 公开（登录所需/监控所需） |
| 静态 JS/CSS/图片 | ✅ 受保护（未登录访问会被 302） |
| 健康检查内容 | 仅缓存计数，不含路径与配置 |

**没有例外通道**：不存在「本机免登录」「内网免登录」「调试开关」。
`AUTH_DISABLED=1` 仅用于测试，且非回环监听时会被启动护栏和体检同时判为故障。

### 6.2 会话生命周期

```text
输入私钥 ──► 校验（常量时间）──► 创建服务端会话 sid
                                   │
              Set-Cookie: dapeng_sid=<随机32字节>; HttpOnly; SameSite=Lax
              （无 Max-Age / Expires → 关浏览器即清除）
                                   │
        每次请求校验：存在 + 未空闲超时(30min) + 未超绝对上限(8h)
                                   │
        退出 / revoke-all / 重启 ──► 服务端删除，旧 sid 立即 401
```

- **关浏览器 → 重新输入私钥**：Cookie 是会话级，浏览器不会持久化；
- 关闭标签页是否退出：默认否（浏览器会话仍在）；设 `PAGE_CLOSE_LOGOUT=1` 后，
  前端在 `pagehide` 时调用登出，标签页关闭也会撤销服务端会话；
- 单 IP 15 分钟错 5 次锁 15 分钟；全局 60 次/10 分钟暂停登录；
- 登录审计写 `logs/auth.log`，可用 `POST /api/auth/revoke-all` 一键下线全部设备。

### 6.3 验证方式（可直接复制执行）

```bash
# 1) 未登录：页面 302、API 401
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://127.0.0.1:8900/dashboard.html
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8900/api/decision/sentiment

# 2) 弱口令被拒（应 401）
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"key":"071215"}' http://127.0.0.1:8900/api/auth/login

# 3) 正确私钥：拿到会话级 Cookie（Set-Cookie 里没有 Max-Age/Expires）
curl -s -D - -o /dev/null -c /tmp/ck.txt -X POST -H 'Content-Type: application/json' \
  -d "{\"key\":\"$ACCESS_KEY\"}" http://127.0.0.1:8900/api/auth/login | grep -i set-cookie

# 4) 带会话访问：200
curl -s -b /tmp/ck.txt -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8900/api/decision/sentiment

# 5) 退出后旧 Cookie 立即失效：401
curl -s -b /tmp/ck.txt -X POST http://127.0.0.1:8900/api/auth/logout
curl -s -b /tmp/ck.txt -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8900/api/decision/sentiment
```

浏览器端一键验收（会真实关闭会话）：

```bash
ACCESS_KEY=... node scripts/verify/_verify_session.mjs
```

覆盖 11 项：新会话被挡、弱口令被拒、私钥登录、Cookie 无过期、刷新保持、
**关闭浏览器会话后必须重新认证**、退出后失效、旧 sid 重放 401。

---

## 7. 可复现部署清单

- [ ] Python 3.11 / Docker 24+
- [ ] `cp .env.example .env`，填 `ACCESS_KEY`（≥32 位随机）与 `MOAT_ADMIN_TOKEN`
- [ ] `REQUIRE_ACCESS_KEY=1`（生产必开）；如用短私钥，同时设 `ACCESS_KEY_MIN_LEN=12` 及以上
- [ ] 公网：`HOST=0.0.0.0`、HTTPS 反代、`COOKIE_SECURE=1`、`TRUST_PROXY=1`
- [ ] `ACCESS_IP_ALLOWLIST` 按需收紧（支持 CIDR）
- [ ] 挂载 `data/`、`logs/` 到持久卷；定期备份 `trading.db`
- [ ] 启动后跑 `python scripts/verify/checkup.py`（测试 + 架构 + 体检 + 性能）
- [ ] 跑 `node scripts/verify/_verify_session.mjs`（会话级验收）
- [ ] 确认 `/api/health` 200、`/dashboard.html` 302、弱口令 401

---

## 8. 已知边界

| 边界 | 说明 |
|---|---|
| 会话存内存 | 重启即全员下线；多实例部署需改为共享存储（Redis） |
| 标签页关闭 | 默认不撤销（浏览器会话仍在）；需 `PAGE_CLOSE_LOGOUT=1` |
| 单租户 | 一个私钥对应一个身份，没有多用户与权限分级 |
| 数据源 | 依赖腾讯/新浪/东财/akshare 的公开接口，可能限频或变更 |
| 风险标记 | 8 分制的「有雷」目前是 ST/退市 + 业绩预亏代理，未解析减持/立案公告 |
| 首次启动 | 全市场快照需 10–60 秒，期间候选池返回「尚未就绪」而非空候选 |
