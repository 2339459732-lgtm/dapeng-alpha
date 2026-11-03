# 访问密钥鉴权（access-key.v1）

> 代码：`server/infra/auth.py`（签名/校验/中间件）、`server/routers/auth.py`（登录接口与审计）、
> `static/login.html`（登录页）。测试：`tests/test_auth.py`（29 项）、
> `scripts/verify/_verify_auth.mjs`（真实浏览器 8 项）。

## 一、为什么是「单口令 + 签名 Cookie」

这套系统的定位是「个人研究工具 + 少数朋友共用」，不是 SaaS。

| 方案 | 结论 | 原因 |
|---|---|---|
| 单口令 + 签名 Cookie | ✅ 采用 | 零依赖、零数据库、约 250 行；重启不掉线 |
| 多密钥可撤销 | 备选 | 要维护密钥表与吊销逻辑，收益是「能单独踢人」 |
| 账号密码 + 邀请码 | ❌ | 找回密码、防爆破、会话管理都是长期负担 |
| 网关 Basic Auth | 备选 | 应用零改动，但应用内没有登录页，脚本调用要带 header |

**核心取舍**：牺牲「按人撤销」换「零状态」。要踢人就换 `ACCESS_KEY`——所有旧会话
因签名密钥变化立即失效，这正是单租户场景想要的行为。

## 二、会话格式与校验

```
Cookie: dapeng_session = v1.<exp_unix>.<hmac_sha256_hex>
```

签名密钥：

```
SESSION_SECRET 存在 → 直接用
否则               → sha256("dapeng-session|" + ACCESS_KEY)
```

校验顺序（任一失败即拒绝）：
1. 三段式且版本前缀为 `v1`；
2. `exp` 可解析且 > 当前时间；
3. 重算 HMAC 与第三段 `hmac.compare_digest` 相等。

因为密钥由 `ACCESS_KEY` 派生，**轮换口令 = 全部会话作废**，不需要额外的黑名单。

Cookie 属性：`HttpOnly`、`SameSite=Lax`、`Path=/`、`Max-Age=SESSION_TTL_HOURS`；
HTTPS 或 `COOKIE_SECURE=1` 时加 `Secure`。

## 三、请求处理顺序

```
请求
 │
 ├─ MoatMiddleware（最外层）      路径卫生 / 请求体上限 / 全局限流 / 安全响应头
 │
 ├─ AccessKeyMiddleware
 │    ├─ 写操作来源校验（Origin vs Host）→ 不通过 403
 │    ├─ 未配置 ACCESS_KEY → 直接放行（本地开发）
 │    ├─ 公开路径 → 放行
 │    ├─ 会话有效 → 放行
 │    ├─ /api/* → 401 JSON（带 WWW-Authenticate: AccessKey）
 │    └─ 页面 → 302 /login?next=<原路径>
 │
 └─ 业务路由 / 静态文件
```

公开路径只有：`/login`、`/login.html`、`/api/auth/{login,logout,status}`、
`/api/health`、`/favicon.ico`。健康检查只回缓存计数，不含路径与配置。

## 四、防护墙的五层

| 层 | 机制 | 防什么 |
|---|---|---|
| 1 | `hmac.compare_digest` 常量时间比较 | 时序侧信道猜口令 |
| 2 | 登录接口 10 次/分钟/IP | 单点爆破 |
| 3 | 全局失败闸门 60 次/10 分钟 | 分布式爆破兜底（换 IP 也暂停） |
| 4 | Origin 校验 + SameSite=Lax | CSRF（跨站表单与跨站 fetch） |
| 5 | 非回环监听 + 无密钥 → 拒绝启动 | 「改了 HOST 忘了配 key」的致命误配 |

辅助措施：
- `TRUST_PROXY` / `TRUSTED_PROXIES` 控制是否采信 `X-Forwarded-For`（默认不采信，防伪造限流键）；
- 口令 < 16 字符时启动告警，`/api/health` 的 `checks.config.issues` 也会列出；
- `logs/auth.log` 审计登录成功/失败/限流（JSONL，5MB 轮转，**不记录密钥**）。

## 五、配置项

| 变量 | 默认 | 说明 |
|---|---|---|
| `ACCESS_KEY` | 空 | 为空则关闭鉴权；非回环监听时强制要求 |
| `SESSION_TTL_HOURS` | 72 | 1–720，超出自动夹紧 |
| `SESSION_SECRET` | 派生 | 设置后轮换 `ACCESS_KEY` 不作废旧会话 |
| `COOKIE_SECURE` | 0 | HTTPS 部署置 1 |
| `TRUST_PROXY` | 0 | 反代后置 1 才采信 XFF |
| `TRUSTED_PROXIES` | 空 | 逗号分隔；对端在此列表时采信 XFF |
| `ALLOWED_ORIGINS` | 空 | 额外允许的写请求来源（如独立前端域名） |
| `ALLOW_INSECURE_BIND` | 0 | 置 1 可跳过「非回环必须有密钥」的启动拦截（不建议） |

## 六、失败模式与排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 登录成功但立刻又回登录页 | HTTPS 下设了 `COOKIE_SECURE=0`（Cookie 无 Secure 仍可用）或反代丢失 Cookie | 检查反代是否透传 `Set-Cookie`；确认 `Host` 未被改写 |
| 所有人共用一个限流额度 | 反代后未设 `TRUST_PROXY` | 设 `TRUST_PROXY=1` 或 `TRUSTED_PROXIES` |
| 重启后全部掉线 | 未设 `SESSION_SECRET` 且 `ACCESS_KEY` 变了 | 属预期；固定 `SESSION_SECRET` 可避免 |
| 启动直接报错退出 | `HOST` 非回环且无 `ACCESS_KEY` | 配密钥，或设 `ALLOW_INSECURE_BIND=1` |
| 登录页 404 | `static/login.html` 缺失 | 检查部署包是否包含 static |

## 七、验收

```bash
# 单测（含签名伪造、过期、轮换、限流、来源校验、审计脱敏）
python -m pytest tests/test_auth.py -q

# 端到端（需先起一个带密钥的实例）
ACCESS_KEY=<key> PORT=8911 python -m uvicorn server.main:app --port 8911
ACCESS_KEY=<key> AUTH_BASE=http://127.0.0.1:8911 node scripts/verify/_verify_auth.mjs
```

浏览器验收覆盖：未登录重定向 → 错误密钥就地报错 → 正确密钥进入 → 刷新保持 →
退出后重新要求登录 → 无 JS 运行时错误。

## 八、开了鉴权之后，自检工具怎么跑

这是上线后最容易踩的坑：鉴权一开，审计与性能脚本全被 401 挡在门外，于是「体检报告
一片红」被误读成系统坏了。现在所有验证脚本都会**自动登录**：

| 脚本 | 方式 |
|---|---|
| `scripts/verify/system_audit.py` | `_auth.auth_headers()` 读 `ACCESS_KEY`（环境变量或 `.env`），登录后带 Cookie |
| `scripts/verify/perf_budget.py` | 同上 |
| `scripts/verify/_verify_*.mjs` / `_shot_*.mjs` | `_login.loginContext()` 用 `context.request` 登录，Cookie 自动共享给页面 |
| `scripts/verify/_verify_auth.mjs` | 本身就在验登录流程，直接用 `ACCESS_KEY` |

未配置密钥的实例，助手返回空，脚本行为与以前完全一致。

一键体检（推荐每次改完都跑）：

```bash
python scripts/verify/checkup.py            # pytest + 架构守卫 + 运行态体检 + 性能预算
python scripts/verify/checkup.py --offline  # 只跑前两项（服务没起时）
```

## 九、维护手册

| 场景 | 操作 |
|---|---|
| 换密钥 | 改 `.env` → 重启；会话全失效，需重新登录 |
| 有人离职/设备丢失 | 同上；若设了 `SESSION_SECRET`，先删掉再轮换 |
| 查谁在登 | `tail -f logs/auth.log`（JSONL：时间/IP/动作） |
| 疑似爆破 | 日志出现大量 `login_failed` / `rate_limited`；换密钥并把反代限流也打开 |
| 忘了密钥 | 读服务器 `.env`；实在丢失就换新密钥重启 |
| 想回到无鉴权本机模式 | 清空 `ACCESS_KEY` 且 `HOST=127.0.0.1` |
