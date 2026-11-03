# 仓库卫生与提交前守卫（repo-hygiene.v1）

> 生效日期：2026-11-02 · 对应架构治理轮次 P4

## 一、目录约定

| 目录 | 放什么 | 不许放什么 |
|---|---|---|
| `server/` | 运行时 Python 包 | 一次性脚本、临时 json |
| `scripts/` | 长期可复用的运维/构建脚本 | 一次性调试脚本 |
| `scripts/verify/` | Playwright 验收脚本（可反复跑） | 一次性截图产物 |
| `tools/` | 架构工具（测绘、拆分） | 业务逻辑 |
| `tests/` | pytest 用例与冻结基线（`tests/data/`） | 手工调试代码 |
| `_archive/patches/` | 已应用的补丁脚本（只留档，不再运行） | 待执行脚本 |
| `_archive/tmp/` | 开发期临时产物（截图、dump、中间 json） | 任何被代码引用的文件 |
| `docs/10-architecture/` | 架构文档与 ADR | 生成物 |
| `static/` | 浏览器直取的资源（html/css/js/json） | **任何 `.bak-*` 备份**——static 是 Web 根目录，备份会变成可访问的公开文件 |

**根目录只允许**：配置文件（`.env` / `.gitignore` / `requirements.txt` /
`launch.bat` / `kill_server.py`）、入口页面（`*.html`）、`README.md`、
`CHANGELOG.md`、`server/ static/ docs/ scripts/ tools/ tests/ data/`。

## 二、2026-11-02 归档记录（旧路径 → 新路径）

| 旧路径 | 新路径 | 说明 |
|---|---|---|
| `_verify_*.mjs`、`_shot_*.mjs`、`_final_check.mjs` | `scripts/verify/` | Playwright 验收脚本；`CHANGELOG.md` 中提到的 `_verify_galaxy_v40.mjs` 等即在此目录 |
| `_test_cache.py` | `scripts/verify/_test_cache.py` | 缓存治理断言脚本 |
| `_patch_*.py`、`_moat_patch.py` | `_archive/patches/` | 已应用的补丁，只留档 |
| `_audit_undefined.py` | `_archive/patches/` | 一次性审计脚本 |
| `_backup/split/*.py` | `_archive/split-2026-11-02/` | P2 拆分前的 10 个原文件（回滚用） |
| `_chk.json`、`_e.json`、`_routes_*.txt`、`proc_dump.txt`、`screenshot*.png` | `_archive/tmp/` | 中间产物 |
| `server" && cp …`（`U+F022` 私用区字符目录） | `_archive/tmp/bad-shell-dir/` | 历史命令拼接事故留下的目录树 |
| `static/**/*.bak-*`（61 个，12MB） | `_archive/static-backups-2026-11-02/static/…` | 历次改版留下的整文件备份；`static/` 是 Web 根目录，这些文件此前可被直接访问（如 `/dashboard.html.bak-v85`）。代码零引用，仅留档 |
| 根目录 `_survey.json`、`_e.json`、`_s.json`、`_x.json`、`_md.json`、`_al.json`、`_shot_crosscheck.png` | `_archive/tmp/` | 对证调试期的 curl 探针输出与截图，均为一次性产物 |

> `nul` 是 Windows 设备名残留，`trash` 接口拒删（`ERROR \\.\nul: TargetedRoot`），
> 已在 `.gitignore` 中忽略，不影响构建。

## 三、提交前守卫

唯一入口：

```bash
python scripts/ci_guard.py          # 架构测绘 + 拆分计划 + 全量测试
python scripts/ci_guard.py --fast   # 只跑架构测绘 + 拆分计划
```

三道闸门：

| 闸门 | 命令 | 失败含义 |
|---|---|---|
| 架构测绘 | `tools/arch_report.py --check` | 出现依赖环 / 跨层反向依赖 / 新增 >900 行模块 |
| 拆分计划 | `tools/split_module.py --list` | 拆分计划腐坏（原文件被改得对不上区间） |
| 全量测试 | `pytest tests -q` | 含路由快照（`tests/data/routes_baseline.json`）与 `app.openapi()` 可生成性 |

**CI 接入方式**：把 `python scripts/ci_guard.py` 作为流水线唯一命令，
避免本地判定与流水线判定漂移。

### 需要运行中服务的额外闸门（不进 ci_guard）

```bash
python scripts/verify/perf_budget.py --gate --concurrency 8   # 延迟预算 + 并发（§4.1 of A-SHARE-REALTIME-DECISION-DESK.md）
python scripts/brain_config_admin.py validate                 # 配置语义校验（预检，不写盘）
```

两个都不适合放进 `ci_guard`：前者要求 8900 在跑且有真实上游，后者会读真实配置文件。
它们属于**发布前**闸门，不是**提交前**闸门。

### 路由快照怎么更新

改了路由就必须显式更新基线，不允许「顺手让它过」：

```bash
UPDATE_ROUTES=1 python -m pytest tests/test_contracts.py -q
```

## 四、日常规则

1. **新增一次性脚本**：写进 `_archive/tmp/`，不要放根目录；稳定下来的才提到 `scripts/`。
2. **新增 >900 行模块**：`arch_report --check` 会失败。要么拆，要么在
   `docs/10-architecture/SYSTEM-OPTIMIZATION-2026Q4.md` 登记豁免理由。
3. **改路由**：必须同步 `tests/data/routes_baseline.json`，并在 CHANGELOG 记一行。
4. **改响应信封**：只改 `server/contracts/envelope.py`，并同步 ADR-001 与
   `tests/test_contracts.py`。
5. **拆分模块**：只允许走 `tools/split_module.py`（登记计划 → `--apply`），
   不允许手工剪切——手切必漏跨片 import 与注解类型。
6. **改版本前留备份**：写到 `_archive/tmp/`，不要写进 `static/`；`static/` 下的
   `.bak-*` 会被当成公开资源直接访问。
7. **改主脑参数**：不要手改 `data/brain_config.json` 后直接上线，走
   `python scripts/brain_config_admin.py apply --set <path>=<value> --note "<理由>"`；
   它有预检、原子写入、sha256 留痕和 `rollback`，手改没有这些。
