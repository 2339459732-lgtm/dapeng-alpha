# 系统护城河 · 运行与安全加固

> 目标：让 DAPENG Alpha 从"能跑"变成"坏了能自愈、数据能恢复、边界能守住、行为能观测"。
> 本文件是护城河的设计与运行手册；代码落在 `server/infra/`、`tools/`。
> 记录时间 2026-10-24（AXON v39 / 星系仪器图谱同期）。

## 1 · 护城河的五道墙

| 层 | 问题 | 方案 | 落点 |
|---|---|---|---|
| 边界 | 页面依赖公网 CDN，断网即残；响应无安全头；接口可被刷 | 资源本地化 vendor、严格 CSP/安全头、按 IP 限流、请求体上限 | `static/vendor/`、`server/infra/security.py` |
| 配置 | 参数散落 env 与代码，错配到启动后才发现 | 集中 `Settings` + 启动期校验，`/readyz` 暴露问题 | `server/infra/settings.py` |
| 观测 | 只有 `/api/health` 一个粗粒度探针，无日志文件 | `/healthz`、`/readyz`、`/api/system/status`、`/api/system/integrity` + 滚动日志 | `server/infra/health.py`、`logging_setup.py` |
| 恢复 | 只有代码，没有状态备份；DB 复制无一致性保证 | SQLite backup API 一致性备份 + 清单 sha256 + 14 份留存 | `server/infra/backup.py`、`backups/` |
| 部署 | 进程挂了没人拉起；无自动启动 | 已有 `scripts/server_watchdog.py`（子进程守护）+ 本仓库 watchdog/安装脚本 | `tools/watchdog.ps1`、`tools/install-moat.ps1` |

## 2 · 安全边界

### 2.1 已内置的安全响应头

`x-content-type-options: nosniff`、`x-frame-options: SAMEORIGIN`、`referrer-policy: no-referrer`、
`permissions-policy` 关闭摄像头/麦克风/定位/支付、`cross-origin-opener-policy: same-origin`、
CSP：

```
default-src 'self'; img-src 'self' data: blob:; font-src 'self' data:;
style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline';
connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'
```

- 页面全部自包含（内联脚本/样式），因此允许 `'unsafe-inline'`；**不允许任何外部源**。
- 四个主页面在严格 CSP 下实测无控制台错误（plate-graph / project-docs / dashboard / admin）。

### 2.2 去 CDN 依赖（admin.html）

原先 `admin.html` 引用三处公网资源，断网/被投毒即影响后台：

| 原地址 | 现地址 | 大小 |
|---|---|---|
| `https://cdn.tailwindcss.com` | `vendor/tailwind-cdn.js` | 407 KB |
| `cdnjs .../font-awesome/6.4.0/css/all.min.css` | `vendor/fontawesome-all.min.css` + `vendor/webfonts/*` | 102 KB + 8 字体 |
| `cdnjs .../vanilla-tilt/1.8.0/vanilla-tilt.min.js` | `vendor/vanilla-tilt.min.js` | 8.8 KB |

替换前备份：`static/admin.html.bak-20261024-185037`。页面实测：Tailwind 运行时正常、Font Awesome 字体生效。

### 2.3 限流与体积上限

- `/api/*` 每 IP **600 次/分钟**（`MOAT_RATE_LIMIT`），滑动窗口；超限返回 `429` + `Retry-After`；
- `/api/stream`（SSE）不计入限流窗口；
- 请求体上限 **2 MiB**（`MOAT_MAX_BODY`），超限 `413`；
- 实测：650 次快速请求 → 597×200 + 53×429，随后自动恢复。

## 3 · 配置与启动校验

```ini
HOST=127.0.0.1          # 非回环监听必须配 MOAT_ADMIN_TOKEN，否则 /readyz 报 config 问题
PORT=8900
MOAT_RATE_LIMIT=600
MOAT_MAX_BODY=2097152
MOAT_ADMIN_TOKEN=       # 可选；设置后 /api/system/backup 需要 X-Admin-Token
MOAT_SECURITY=1
MOAT_BACKUP_KEEP=14
MOAT_LOG=1
```

`Settings` 在启动时校验 static 目录、docs-graph.json、非回环监听令牌、限流阈值、备份留存，问题进入
`/readyz` 的 `checks.config.issues`，不再"带病启动"。

## 4 · 观测端点

| 端点 | 用途 | 失败表现 |
|---|---|---|
| `GET /healthz` | 进程存活（watchdog 用，不含业务依赖） | 进程不在 → 连接失败 |
| `GET /readyz` | 就绪：static / docs-graph / 磁盘 / 配置 | 任一失败 → **503** + `checks` |
| `GET /api/system/status` | 运行态：版本、uptime、PID、限流统计、图谱摘要、磁盘、备份、日志、缓存 | 200 |
| `GET /api/system/integrity` | 数据完整性：docs-graph sha256/节点数/链路 schema + 关键静态资源 | 失败 → **503** |
| `POST /api/system/backup` | 手动备份（配置令牌后需 `X-Admin-Token`） | 200/401/500 |

所有响应带 `x-request-id`（透传或生成），日志与前端排障可对齐。

## 5 · 备份与恢复

- 命令：`python -m server.infra.backup`（也可 `POST /api/system/backup`）；
- 内容：`static/docs-graph.json`、`data/trading.db`、`data/api_calls.db`、`static/admin.html`；
- SQLite 用 `sqlite3.Connection.backup()` 复制，WAL 下也一致；
- 产物：`backups/dapeng-backup-YYYYmmdd-HHMMSS.zip` + `backups/latest.json`（含每个文件 sha256）；
- 留存：默认 14 份，超出按 mtime 清理；
- 恢复：停服 → 解压 zip 覆盖对应路径 → 启动 → `GET /api/system/integrity` 校验 sha256/节点数。

## 6 · 部署与自愈

| 组件 | 说明 |
|---|---|
| `scripts/server_watchdog.py` | 既有守护：以子进程方式跑 uvicorn，异常退出按 5→10→30→60s 退避重启，单实例守卫端口 8899 |
| `DAPENG-Alpha-Service` 计划任务 | 登录时启动上面的守护（当前状态：Running） |
| `tools/watchdog.ps1` | HTTP 层探活：**连续 3 次** `/healthz` 失败才考虑重启，120s 冷却；检测到 `server_watchdog.py` 时不抢重启（避免双拉起）；常驻模式兼每日备份 |
| `DAPENG-Alpha-Watchdog` 计划任务 | 每分钟执行 `watchdog.ps1 -Once`（Ready） |
| `DAPENG-Alpha-Backup` 计划任务 | 每天 03:10 执行 `python -m server.infra.backup`（Ready） |
| `tools/install-moat.ps1` | 一键注册/卸载上述任务；无管理员权限时降级为当前用户启动文件夹方案 |

验证：`powershell -NoProfile -ExecutionPolicy Bypass -File tools\watchdog.ps1 -Once` → `healthy (port 8900)`，退出码 0。

## 7 · 一键体检

```bash
python tools/check_data_sources.py
```

当前 **40/40 PASS**：包含 34/93 图谱、4 页面、8 增强资源、9 自动化接口、2 后台接口、
4 护城河端点、4 安全头、4 vendor 资源、admin 无外链、备份清单、2 个运维脚本。

## 8 · 已知边界与后续

1. 进程内限流是单机方案，多实例部署需要换成 Redis/网关层限流；
2. 计划任务需要管理员权限的部分（登录自启）已在无权限时降级为启动文件夹方案；
3. `.env` 不进备份包（避免密钥扩散），密钥请另行保管；
4. 后续可加：Prometheus 指标导出、Sentry 式错误聚合、磁盘水位告警、备份异地同步。

## 9 · 相关文档

- [DEPLOYMENT.md](DEPLOYMENT.md)：部署形态、看门狗与自愈——护城河第 5 道墙的运行手册；
- [SYSTEM-WORKFLOW.md](../10-architecture/SYSTEM-WORKFLOW.md)：模块地图与接口清单，
  `/healthz`、`/readyz`、`/api/system/*` 端点在其中的位置。

