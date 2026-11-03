# DAPENG Alpha · 策略选股量化系统

> 面向个人投资者的 **A 股「可解释决策辅助系统」** —— 把行情、板块、资金、全球市场、因子与策略信号收敛成**可追溯的决策依据**，再由自动化交易中心在**严格风控**下执行，并用复盘闭环反哺策略。

个人量化工具，不构成投资建议。系统只做研究与纸面验证，**不接真实券商通道**。

---

## 快速开始

```bash
# 1. 启动（启动脚本会自动探测可用 Python 解释器：优先项目 .venv，回退系统 Python 3.11）
start.bat

# 2. 访问
#    首页      http://localhost:8900/index.html
#    看板      http://localhost:8900/dashboard.html
#    管理后台  http://localhost:8900/admin.html
#    项目文档  http://localhost:8900/project-docs.html
```

> 服务默认监听 `127.0.0.1:8900`。持久服务由看门狗（`scripts/server_watchdog.py`）守护，
> 崩溃自动退避重启。部署与运维细节见 [`docs/40-ops/DEPLOYMENT.md`](docs/40-ops/DEPLOYMENT.md)。

---

## 开源与访问密钥

本项目以 **Apache-2.0** 开源。原则是：**代码公开，运行实例不公开**。
访问采用**私钥 + 服务端会话**：私钥登录后下发会话级 Cookie（关浏览器即失效），
登出/撤销/重启都会让会话立即作废。

```bash
# 1) 生成私钥（≥32 位随机串，推荐 48 字节）
python -c "import secrets;print(secrets.token_urlsafe(48))"

# 2) 配置环境
cp .env.example .env
#   ACCESS_KEY=<上一步输出>
#   REQUIRE_ACCESS_KEY=1        # 缺失或过弱直接拒绝启动
#   HOST=0.0.0.0                # 只在本机用则保持 127.0.0.1
#   MOAT_ADMIN_TOKEN=<另一个随机串>

# 3) 启动
python -m uvicorn server.main:app --host 127.0.0.1 --port 8900
#    或：docker compose up -d --build
```

| 变量 | 作用 | 默认 |
|---|---|---|
| `ACCESS_KEY` / `ACCESS_KEY_FILE` | 访问私钥（环境变量或文件） | 空 |
| `REQUIRE_ACCESS_KEY` | 1 = 缺失/弱私钥拒绝启动（生产必开） | 0 |
| `SESSION_IDLE_MINUTES` | 空闲超时（无请求即失效） | 30 |
| `SESSION_TTL_HOURS` | 会话绝对上限 | 8 |
| `PAGE_CLOSE_LOGOUT` | 1 = 关闭标签页也登出 | 0 |
| `ACCESS_IP_ALLOWLIST` | IP 白名单（CIDR），配置后登录页也不对外 | 空 |
| `COOKIE_SECURE` | HTTPS 部署置 1 | 0 |

**生效范围**：所有页面与 `/api/*` 都需要私钥；只有 `/login`、`/api/auth/{login,logout,status}`、
`/api/health` 公开。没有「本机免登录」「内网白名单免登录」之类的旁路。

完整说明（初始化、实时链路、持久化、目录结构、启动方式、验证命令）见
[`docs/40-ops/OPEN-SOURCE-DEPLOY.md`](docs/40-ops/OPEN-SOURCE-DEPLOY.md)；
威胁模型与部署清单见 [`SECURITY.md`](SECURITY.md)；实现取舍见
[`docs/10-architecture/AUTH.md`](docs/10-architecture/AUTH.md)。

### 维护私钥（不改口令）

```bash
python scripts/ops/key_status.py          # 指纹/长度/是否泄露，不显示明文
python scripts/ops/scan_secrets.py        # 扫描已跟踪文件中的凭据
python scripts/ops/install_hooks.py       # 装 pre-commit，提交前自动拦截
python scripts/ops/export_opensource.py   # 生成只含 HEAD 的发布包并复检
```

规则：私钥只存在于 `.env`（或 `ACCESS_KEY_FILE` 指定的 secret 文件），
任何进入仓库的文件都不得包含真实私钥。详见
[`docs/40-ops/KEY-MANAGEMENT.md`](docs/40-ops/KEY-MANAGEMENT.md)；
第三方许可证见 [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md)。

### 自检

```bash
python scripts/verify/checkup.py            # 测试 + 架构守卫 + 运行态体检 + 性能预算
python scripts/verify/checkup.py --offline  # 服务未启动时只跑前两项
```

> 开启鉴权后，审计与性能脚本会自动读取 `ACCESS_KEY` 登录，不需要手工改脚本。

### 数据与隐私

`data/` 是**运行期目录**：行情快照、复盘结果、交易库、用户表都由程序生成，默认全部不入库
（`.gitignore` 采用白名单，只放行 4 个纯配置文件）。因此 clone 后首次启动会自动重建，
不会带上任何人的真实持仓或账户信息。

---

## 目录导航

| 路径 | 内容 |
|---|---|
| `server/` | FastAPI 后端（路由 / 交易闭环 / 数据源适配层 / AI 主脑） |
| `static/` | 前端（原生 HTML + CSS + JS，无构建工具） |
| `docs/` | 文档体系（见下） |
| `scripts/` | 启动、看门狗、测试脚本 |
| `data/` | SQLite、运行时缓存与**离线构建的数据资产**（见下） |

### 数据文件与构建脚本

| 文件 | 内容 | 生成方式 |
|---|---|---|
| `data/sector_map.json` | **496 个行业板块** + **5657 只股票的反向索引**（`coverage{496/496}`，level 414/39/43） | `scripts/build_sector_map.py` |
| `data/sector_periods.json` | 板块多周期收益（5/20/60 日，公式 `d_n = close[-1]/close[-1-n] - 1`） | `scripts/build_sector_periods.py` |
| `data/sector_returns.json` | 板块日收益序列（关联图谱 / 相关性分析输入） | `scripts/build_sector_periods.py` |

> 两个构建脚本为**离线构建**（请求路径只读缓存，缓存缺失时接口返回 `period_returns_cache_missing`，不推算），
> 支持 `--force`（强制重建）/ `--ttl-hours N`（缓存有效期）/ `--concurrency N`（并发数）。

### 文档体系（`docs/`）

```
docs/
  00-strategy/      战略与规划     ← MASTER-PLAN-2026Q4.md（总纲，先读这份）
  10-architecture/  架构设计       ← 架构文档 + 类图 / 时序图（Mermaid）
  20-prd/           需求文档       ← 增量 PRD
  30-data/          数据口径字典   ← 数据源清单、字段单位与来源标注约定
  40-ops/           部署与运维     ← 启动 / 看门狗 / 端口 / 自愈
  90-archive/       归档           ← 过期报告与已废弃草案（仅考古）
  assets/           截图           ← 各阶段 UI 截图统一收纳
```

**入口**：[`docs/00-strategy/MASTER-PLAN-2026Q4.md`](docs/00-strategy/MASTER-PLAN-2026Q4.md) —— 九模块总体规划（战略定位 / 北极星指标 / M1-M9 模块目标与验收标准 / 路线图 / 数据真实性红线 / 已知限制）。

**决策闭环规划**：[`docs/00-strategy/DECISION-CLOSED-LOOP-PLAN-2026Q4.md`](docs/00-strategy/DECISION-CLOSED-LOOP-PLAN-2026Q4.md) —— 动态市场调查的数据类型 / 来源 / 频率 / 存储配置，六专家主脑的讨论、冲突与决策依据，情绪六维量化与展示，以及三者与决策台的 I/O、依赖和升级优先级。

**文档地图**：[`docs/00-strategy/README.md`](docs/00-strategy/README.md) —— 按「**现状 / 计划（living）/ 历史设计 / 归档**」四类导航全部文档，一眼判断"该信哪一篇"。

**数据口径与陷阱**：[`docs/30-data/DATA-DICTIONARY.md`](docs/30-data/DATA-DICTIONARY.md) —— 数据源清单与可用性、四级降级链、字段单位与来源标注约定、数据源陷阱清单、假零红线、板块层级约定、涨跌配色铁律、前端版本号规范、P0 迁移约束。

**系统工作流**：[`docs/10-architecture/SYSTEM-WORKFLOW.md`](docs/10-architecture/SYSTEM-WORKFLOW.md) —— 六条主链路（数据接入 → 选股信号 → 9 关风控+四维决策闸 → 成交 → 复盘迭代 → 实时推送）、存储落盘位置、接口↔链路映射、自检命令、已知边界。

**接口手册**：[`docs/40-ops/API-REFERENCE.md`](docs/40-ops/API-REFERENCE.md) —— **141 个端点**按 13 个功能分组。该文件由运行中服务的 `/openapi.json` 自动生成（`tools/gen_api_doc.py`），不手工维护，因此不会与代码脱节。在线可搜索版：`http://localhost:8900/api-reference.html`（数据由 `tools/gen_api_page.py` 生成）。

**开源能力接入（规划）**：[`docs/20-prd/openbb-dsa-integration-prd.md`](docs/20-prd/openbb-dsa-integration-prd.md) —— OpenBB（Apache-2.0）与 DailyStockAnalysis（MIT）的调研与增量 PRD：许可证结论、可复用能力 Top 8、数据/指标/可视化/交易四类缺口、P0 7 项 / P1 6 项 / P2 5 项、跨模块联动路径与优先级总表。

**在线文档站**：`http://localhost:8900/project-docs.html` —— 内容与版式分离，单一数据源 `static/js/project-docs-data.js`。

---

## 技术栈

- **后端**：FastAPI + uvicorn（`async/await` + `asyncio` 后台任务）
- **前端**：原生 HTML / CSS / JS + ECharts 5.x + KLineChart v10（无构建工具）
- **数据**：腾讯行情 / 新浪财经 / 东方财富（push2、datacenter-web）/ AkShare（可选适配层）
- **AI**：DeepSeek（`deepseek-chat`），无 Key 时规则模板兜底
- **存储**：SQLite（WAL）+ 进程内 TTLCache + 磁盘快照降级链

---

## 关键纪律

1. **数据真实度第一**：降级数据强制自证身份（`synthetic` / `stale` / `degraded`），禁止推算值冒充实测值。
2. **规则先于直觉**：自动交易必须「先回测 → 过风控门控 → 纸面验证」，改参数强制回退 `draft`。
3. **可回溯**：每笔成交能追到当时的策略信号 + AI 研判 + 风控快照。
4. **文档同步**：功能变更须同步更新 `static/js/project-docs-data.js` 的 `meta.uiVersion` / `updatedAt`。

---

## 更新日志

见 [`CHANGELOG.md`](CHANGELOG.md)（历史条目）与文档站「重要里程碑」章节（最新）。
