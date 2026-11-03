# DAPENG Alpha · 部署与运维

> 版本：v1.0 ｜ 日期：2026-10-14 ｜ 适用代码基线：v5.9
> 本文档登记**启动方式、守护机制、端口、停止与故障自愈**。相关历史问题见文档站「问题与解决方案」（I-09/I-10/I-11）。

---

## 1. 部署形态

**单机自部署**（明确排除多用户 / 商业化）。服务只监听本机，无登录鉴权（边界依赖"单机"这一前提）。

- 监听地址：`127.0.0.1:8900`（`.env` 中 `HOST` 与注释已固化）
- 静态资源：由 FastAPI `StaticFiles` 挂载 `static/`
- 数据库：`data/trading.db`（SQLite WAL）

---

## 2. 启动方式

| 入口 | 用途 |
|---|---|
| `start.bat` | 推荐：一键启动（GBK + CRLF 编码，避免中文乱码） |
| `start_silent.bat` | 静默启动 |
| `start_server.py` | Python 直接启动 |
| `scripts/server_watchdog.py` | 看门狗守护启动（生产路径） |
| `start-dapeng.vbs` | 以 `pythonw` 无窗体独立启动并注册到系统 Startup（开机自启） |

### 解释器自动探测

5 个启动入口在启动时按候选列表**逐个实跑验证**（`python -c "import fastapi, uvicorn"`），
自动选用第一个可用解释器，**不再写死 venv 绝对路径**：

```
候选顺序：项目 .venv → workbuddy envs/default → sys.executable → 系统 Python 3.11 → versions\*
全部失败则记录清晰错误并退出
```

> 历史背景：看门狗曾写死 `…\binaries\python\envs\default\Scripts\python.exe`，
> 该路径在 `envs` 目录被清理后失效，导致"开机后网页打不开"（I-09）。
> 现改为自动探测，实测 `start-dapeng.vbs` 拉起后 `/api/health` 返回 ok、`/dashboard.html` 200。

---

## 3. 守护与自愈

`scripts/server_watchdog.py`：

- 守护 uvicorn，子进程崩溃后**退避重启**：5s → 60s 封顶；稳定运行 120s 后**重置**退避。
- 子进程以 `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` 启动（仅 Windows），
  避免弹出控制台窗口——窗口被关闭等价于发送 `CTRL_CLOSE`，会导致进程以 `0xC000013A` 退出（I-10）。
- **原子单实例**：以独占守卫端口 **8899** 的 socket bind 保证只有一个看门狗实例，
  替代存在 TOCTOU 问题的端口探测；8900 端口探测降级为辅助日志。
- `SIGTERM` 优雅退出；日志滚动。

---

## 4. 停止

`stop.bat`：按特征匹配进程 + `netstat` 反查 8900 占用 PID 兜底。
（历史坑：脚本曾用 `chcp 65001` 而文件是 GBK 字节，中文提示乱码；已对齐为 GBK + CRLF。）

---

## 5. 运行约束与注意事项

1. **沙箱内无法留下跨会话存活进程**：持久服务必须由用户在沙箱外（Startup）启动。
2. **改 Python 需重启 uvicorn**：新增 / 修改 router 后必须重启，否则内存中无对应路由，表现为 404（I-14）。
3. **改 HTML/CSS/JS 无需重启**：但必须递增 `?v=N` 版本号，否则浏览器继续用旧代码（I-08）。
4. **测试本机服务须绕过系统代理**：`curl` 要加 `--noproxy "*"`，
   否则系统代理（历史上为 `127.0.0.1:60430`）会拦截 localhost 请求并返回 502 假阳性（I-07/I-11）。

---

## 6. 健康检查

```bash
curl --noproxy "*" http://127.0.0.1:8900/api/health
```

- 返回 `{"ok":true,...}` 表示服务正常。
- 返回体中的 `cache` 块是缓存运行态：`expired_now` 长期大于 0 说明后台回收任务没跑，
  `cleanup_task_running` 应为 `true`，`seconds_since_cleanup` 应在 30s 量级。
- 手动回收过期缓存：`POST /api/admin/cache/purge-expired`（后台每 30s 也会自动跑）。
- 数据源健康度：`GET /api/health/datasources`（规划中，见 MASTER-PLAN §4.6）返回每个通道的
  `last_success_ts` / `last_error` / `consecutive_failures`。
- 2026-10-22 的缓存与系统漏洞修复明细见
  [CHANGELOG-2026-10-22.md](./CHANGELOG-2026-10-22.md)。

---

## 7. 日志

| 文件 | 内容 |
|---|---|
| `logs/server.log` | uvicorn 与业务日志（含数据源取数失败、降级记录） |
| `logs/watchdog.log` | 看门狗与子进程生命周期、崩溃重启记录 |

---

## 护城河（运行与安全加固）

部署已内置 [HARDENING-MOAT](HARDENING-MOAT.md) 描述的五道墙：

- **安全边界**：严格 CSP 与安全响应头、去 CDN（admin.html 资源全部本地化）、/api 600 次/分限流、2 MiB 请求体上限、路径卫生；
- **配置校验**：server/infra/settings.py 集中读取环境变量并在 /readyz 暴露问题；
- **观测**：/healthz、/readyz、/api/system/status、/api/system/integrity，响应带 x-request-id，日志滚动写 logs/app.log；
- **恢复**：python -m server.infra.backup（SQLite 一致性备份 + sha256 清单 + 14 份留存）；
- **自愈**：既有 scripts/server_watchdog.py 守护进程 + 	ools/watchdog.ps1 HTTP 探活 + 计划任务
  DAPENG-Alpha-Watchdog（每分钟）/ DAPENG-Alpha-Backup（每日 03:10）。

体检命令：python tools/check_data_sources.py（当前 40/40 PASS）。
体检命令：python tools/check_data_sources.py（当前 40/40 PASS）。

### 10.1 运行指标基线（2026-10-30 深夜实测快照）

| 指标 | 实测值 | 判定 |
|---|---|---|
| 缓存命中率 | 76.9%（5680/1705） | 健康（淘汰 0、pending 0） |
| 决策链延迟 | 信号 20ms · 风控 16ms · 复盘 17ms · 状态 31ms | 毫秒级 |
| 感知层 | 情绪 233ms · 行情 1204ms（缓存回温后） | 上游慢时走缓存 |
| 图谱 | 57 节点 / 136 边 | 与文档同步 |
| 前端治理 | 空闲 10s 暂停动画 + 星系跳帧降档 + 页面隐藏暂停 | 三层省电 |

> 快照脚本：logs/_run_snapshot.json（自动生成）；主题切换系统已按用户要求整体移除。

## 10 · 运行指标基线与巡检（2026-10-26 实测）

巡检 = 依次执行下列命令，对照基线判定：

| 项 | 命令 | 基线 | 异常处置 |
|---|---|---|---|
| 数据源体检 | `python tools/check_data_sources.py` | 40/40 PASS | 看 FAIL 项逐条修 |
| 缓存健康 | `curl /api/health` | 命中率 >60%、淘汰 ≈0、pending 0 | 持续 0 命中→查上游连通 |
| 决策链 | 8 端点（行情/情绪/信号/风控/复盘权益/复盘指标/同步/状态） | 全 200；信号/风控/复盘 <50ms；行情/情绪 <8s | 行情>10s 属上游慢，等待缓存回填，勿连续刷 |
| 前端质量 | `node _verify_galaxy_v40.mjs` | 全 PASS、控制台零错误 | 先看控制台输出定位 |
| 备份 | `backups/latest.json` | 当日 03:10 产物 | 手动 `python -m server.infra.backup` |

CPU/负载治理现状（前端已内置，无需配置）：页面隐藏自动暂停渲染；星系空闲 8 秒自动降至 ~30fps；
帧耗 时 EMA>24ms 自动降档（关底晕、缩点径）；任何交互即刻恢复满帧。

---

## 远程访问与离线运行

宿舍电脑/手机热点场景的网络与同步方案见 [REMOTE-ACCESS](REMOTE-ACCESS.md)：
自备网络（宽带/4G 路由器）、Tailscale/Cloudflare Tunnel、mobile.html 手机简报、
`scripts/push_decision.py` 邮件推送、通达信本地盘后数据兜底。
