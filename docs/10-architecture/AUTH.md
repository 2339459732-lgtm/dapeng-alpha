# 访问密钥鉴权（access-key.v2）

> 代码：`server/infra/auth.py`（会话与中间件）、`server/routers/auth.py`（登录接口与审计）、
> `static/login.html`（登录页）、`static/js/session-guard.js`（401 回登录页）。
> 测试：`tests/test_auth.py`（28 项）；浏览器验收：`scripts/verify/_verify_session.mjs`（11 项）。

## 一、为什么是「私钥 + 服务端会话」

定位是「个人研究工具 + 少数朋友共用」，不是 SaaS。v1 用过 HMAC 签名 Cookie，
但留下两个缺口：会话关不掉、弱口令挡不住。v2 改为**服务端会话**，直接解决这两点。

| 方案 | 结论 | 原因 |
|---|---|---|
| 私钥 + 服务端会话 | ✅ 采用 | 可撤销、会话级 Cookie、关浏览器即失效 |
| 无状态签名 Cookie（v1） | ❌ 已废弃 | 关浏览器不失效，踢人只能换 key |
| 账号密码 + 邀请码 | ❌ | 找回密码、防爆破、多用户管理都是长期负担 |
| 网关 Basic Auth | 备选 | 应用零改动，但无登录页、脚本需带 header |

**核心取舍**：牺牲「重启不掉线」换「可撤销 + 强制重登」。会话存进程内存，
重启即全员下线——对单租户工具这是可接受、甚至更安全的默认。

## 二、会话格式与校验

```
Cookie: dapeng_sid = <32 字节随机串>
服务端: _SESSIONS[sid] = {created, last_seen, ip, ua_hash}
```

- Cookie **不写 `Max-Age`/`Expires`** → 会话级，浏览器关闭即清除；
- 校验：会话存在 + 未超空闲窗口（默认 30 分钟）+ 未超绝对上限（默认 8 小时）；
- 每次请求刷新 `last_seen`；超过 `MAX_SESSIONS=32` 淘汰最久未活动的一条；
- 登出删除单条；`revoke-all` 清空全部；**重启清空全部**。

Cookie 属性：`HttpOnly`、`SameSite=Lax`、`Path=/`；HTTPS 或 `COOKIE_SECURE=1` 时加 `Secure`。

## 三、请求处理顺序

```
请求
 │
 ├─ MoatMiddleware（最外层）      路径卫生 / 请求体上限 / 全局限流 / 安全响应头
 │
 ├─ AccessKeyMiddleware
 │    ├─ IP 白名单校验 → 不在名单 403
 │    ├─ 写操作来源校验（Origin vs Host）→ 不通过 403
 │    ├─ 鉴权未开启（AUTH_DISABLED=1）→ 放行（仅测试）
 │    ├─ 公开路径 → 放行
 │    ├─ 会话有效 → 放行
 │    ├─ /api/* → 401 JSON（带 WWW-Authenticate: AccessKey）
 │    └─ 页面 → 302 /login?next=<原路径>
 │
 └─ 业务路由 / 静态文件
```

公开路径只有：`/login`、`/login.html`、`/api/auth/{login,logout,status}`、
`/api/health`、`/favicon.ico`。健康检查只回缓存计数，不含路径与配置。

## 四、防护墙的六层

| 层 | 机制 | 防什么 |
|---|---|---|
| 1 | **IP 白名单** `ACCESS_IP_ALLOWLIST`（支持 CIDR） | 扫描器/陌生 IP |
| 2 | `hmac.compare_digest` 常量时间比较 | 时序侧信道猜私钥 |
| 3 | 登录 10 次/分钟/IP + **单 IP 15 分钟错 5 次锁 15 分钟** | 快打 + 慢速穷举 |
| 4 | 全局失败闸门 60 次/10 分钟 | 分布式爆破兜底 |
| 5 | Origin 校验 + SameSite=Lax | CSRF |
| 6 | **强私钥强制**：`REQUIRE_ACCESS_KEY=1` 时缺失/短于 32 位/低熵 → 拒绝启动 | 弱口令与「忘了配 key」 |

辅助：
- `TRUST_PROXY` / `TRUSTED_PROXIES` 控制是否采信 `X-Forwarded-For`（默认不采信）；
- `logs/auth.log` 审计登录成功/失败/限流/锁定（JSONL，5MB 轮转，**不记录私钥**）；
- 前端 `session-guard.js` 遇到 401 自动回登录页，不让用户停留在「加载失败」。

## 五、配置项

| 变量 | 默认 | 说明 |
|---|---|---|
| `ACCESS_KEY` | 空 | 访问私钥，≥32 位随机串 |
| `ACCESS_KEY_FILE` | 空 | 从文件读取私钥（Docker/K8s secret 挂载），优先级低于 `ACCESS_KEY` |
| `REQUIRE_ACCESS_KEY` | 0 | 1 = 缺失/过弱直接拒绝启动（生产必开） |
| `AUTH_DISABLED` | 0 | 1 = 关闭鉴权，仅测试；非回环监听时体检判故障 |
| `SESSION_IDLE_MINUTES` | 30 | 空闲超时（5–240） |
| `SESSION_TTL_HOURS` | 8 | 绝对上限（1–24） |
| `PAGE_CLOSE_LOGOUT` | 0 | 1 = 关闭标签页也登出（刷新可能触发重登） |
| `COOKIE_SECURE` | 0 | HTTPS 部署置 1 |
| `TRUST_PROXY` | 0 | 反代后置 1 才采信 XFF |
| `TRUSTED_PROXIES` | 空 | 逗号分隔；对端在此列表时采信 XFF |
| `ALLOWED_ORIGINS` | 空 | 额外允许的写请求来源 |
| `ACCESS_IP_ALLOWLIST` | 空 | IP 白名单，支持 CIDR；配置后连登录页都不对外 |
| `ALLOW_INSECURE_BIND` | 0 | 跳过「非回环必须有密钥」的启动拦截（不建议） |

## 六、失败模式与排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 每次开浏览器都要重登 | 会话 Cookie（设计如此） | 正常；想少登几次可调大 `SESSION_IDLE_MINUTES` |
| 刷新页面就掉线 | `PAGE_CLOSE_LOGOUT=1` 且 pagehide 触发 | 关掉该开关，或接受此行为 |
| 登录成功但立刻又回登录页 | 反代未透传 `Set-Cookie`，或 HTTPS 下设了 `COOKIE_SECURE=0` | 检查反代与 `Host` |
| 所有人共用一个限流额度 | 反代后未设 `TRUST_PROXY` | 设 `TRUST_PROXY=1` 或 `TRUSTED_PROXIES` |
| 重启后全部掉线 | 会话存内存 | 预期行为 |
| 启动直接报错退出 | 未配私钥 / 私钥 <32 位 / `HOST` 非回环 | 按报错提示生成并写入 `.env` |
| 登录页 404 | `static/login.html` 缺失 | 检查部署包 |
| 突然全部 403「不在白名单」 | `ACCESS_IP_ALLOWLIST` 不含本机 IP | 加入出口 IP，或清空变量重启 |
| 错 5 次后一直 429 | 单 IP 锁定 15 分钟 | 等待解锁，或重启清空计数 |

## 七、验收

```bash
# 单测：强度、会话过期/撤销、Cookie 无 Max-Age、限流、来源校验、审计脱敏
python -m pytest tests/test_auth.py -q

# 浏览器端到端（真实关闭会话，11 项）
ACCESS_KEY=<key> node scripts/verify/_verify_session.mjs
```

浏览器验收覆盖：新会话被挡 → 弱口令被拒 → 私钥登录 → Cookie 无过期 →
刷新保持 → **关闭浏览器会话后重新认证** → 退出失效 → 旧 sid 重放 401。

## 八、开了鉴权之后，自检工具怎么跑

鉴权一开，审计与性能脚本会被 401 挡住，于是「体检一片红」被误读成系统坏了。
所有验证脚本都会**自动登录**：

| 脚本 | 方式 |
|---|---|
| `scripts/verify/system_audit.py` | `_auth.auth_headers()` 读私钥（环境变量或 `.env`），登录后带 Cookie |
| `scripts/verify/perf_budget.py` | 同上 |
| `scripts/verify/_verify_*.mjs` / `_shot_*.mjs` | `_login.loginContext()` 用 `context.request` 登录，Cookie 自动共享给页面 |

```bash
python scripts/verify/checkup.py            # pytest + 架构守卫 + 体检 + 性能
python scripts/verify/checkup.py --offline  # 服务未起时只跑前两项
```

## 九、维护手册

| 场景 | 操作 |
|---|---|
| 换私钥 | 改 `.env`/secret 文件 → 重启；全部会话立即失效 |
| 设备丢失 | `POST /api/auth/revoke-all`，再换私钥 |
| 查谁在登 | `tail -f logs/auth.log`（JSONL：时间/IP/动作） |
| 疑似爆破 | 大量 `login_failed` / `ip_locked`；换私钥并收紧 `ACCESS_IP_ALLOWLIST` |
| 忘了私钥 | 读服务器 `.env` 或 secret 文件；丢失则生成新私钥重启 |
| 本地调试不想登录 | `AUTH_DISABLED=1` + `HOST=127.0.0.1`，**不要**用于部署 |
