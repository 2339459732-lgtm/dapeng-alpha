# 系统架构与数据连接优化方案（2026 Q4）

> 范围：`server/` 全部 Python 模块、路由层、数据层、研判引擎与前端数据契约。
> 方法：先用 `tools/arch_report.py` 做静态测绘（AST 解析，不跑业务），再按依赖方向治理。
> 本文档与代码同步维护；任何结构调整都要更新第 3 节问题清单与第 8 节验收记录。

---

## 1. 现状测绘（2026-11-02）

| 指标 | 数值 |
|---|---|
| Python 模块 | 127 个 |
| 代码行数 | 44,937 行 |
| 函数 | 1,462 个 |
| HTTP 路由 | 178 个 |
| 分层分布 | infra 12 · providers 3 · core 8 · data 20 · engine 47 · api 37 |
| 服务启动导入耗时 | 1.59s（`server.main` 全量 import） |
| 大盘接口耗时 | 冷启动 1.15s（真实抓取）/ 热缓存 10ms |

测绘命令：

```bash
python tools/arch_report.py          # 人读摘要
python tools/arch_report.py --json   # 机器可读
python tools/arch_report.py --check  # 违规退出码 1（可挂 CI）
```

---

## 2. 目标架构与边界

### 2.1 分层与依赖方向

```
┌──────────────────────────────────────────────┐
│  L4 API      server/routers/*                │  只做参数校验、鉴权、响应封装
├──────────────────────────────────────────────┤
│  L3 Engine   brain / experts / survey / ict  │  研判、评分、策略
├──────────────────────────────────────────────┤
│  L2 Data     coverage / scanner / snapshot   │  采集、派生、快照
│              services/*（共享领域服务）        │
├──────────────────────────────────────────────┤
│  L1 Core     纯计算：indicators / models      │  无 IO、无全局状态
├──────────────────────────────────────────────┤
│  L0 Infra    cache / kv / data_bus / config  │  基础设施端口
└──────────────────────────────────────────────┘
```

**硬规则（由 `tests/test_architecture.py` 强制执行）**

1. 依赖只能向下：`api → engine → data → core → infra`，禁止反向。
2. 禁止模块级循环依赖；确需回调时用端口（如 `server.infra.kv`）。
3. `server/services/*` 是共享内核，**永远不允许 import `server.routers`**。
4. 路由文件只放端点与响应封装，业务逻辑下沉 services；单个路由文件目标 ≤ 250 行。
5. 纯计算模块不得 import 数据采集模块。

### 2.2 命名与异常规范

| 项 | 规范 | 现状 |
|---|---|---|
| 公开函数 | 动词开头、无前导下划线：`build_dashboard` / `get_snapshot` | 新代码遵守；`_build_dashboard` 作为兼容别名保留 |
| 私有函数 | 单下划线，仅模块内使用 | 存量较多，P1 逐步收敛 |
| 数值解析 | 统一 `to_float`（缺失/NaN/inf → None），禁止用 0 冒充 | 现有 `_finite` / `_safe_float` 语义不一致 |
| 异常 | 边界层捕获并降级，领域层允许抛出；禁止裸 `except` | 当前裸 except = 0 |
| 降级 | 必须带 `reason`，禁止静默返回空结构 | 决策链路已遵守 |

### 2.3 数据流转（决策闭环）

```
外部源 ──► market_coverage（采集/校验）
              │
              ▼
        market_scanner（派生：横截面/轮动/资金流）
              │
              ▼
        decision_snapshot（MarketPacket，单一事实源）
              │                 │
              ▼                 ▼
        sentiment.v1       decision_experts（六专家 + 互评）
              └──────► experts ──► 决策台（SSE 推送）
                                   │
                                   ▼
                           expert_ledger / 校准
```

数据总线（`server/data_bus.py`）负责：资源注册、级联失效、事件推送、链路年龄。
缓存（`server/cache.py`）是唯一存储层，总线不持有数据。

---

## 3. 问题清单

> 编号用于跨文档引用；「状态」为本文档撰写时的真实状态。

| ID | 问题 | 证据 | 影响 | 优先级 | 状态 |
|---|---|---|---|---|---|
| P01 | 数据层反向依赖路由层：5 处 `from server.routers.market_dashboard import _build_dashboard` | `ak_market.py:96`、`decision_snapshot.py:103`、`market_coverage.py:877`、`market_survey_engine.py:45`、`qlib_factor_engine.py:353` | 数据层无法独立复用；改路由可能打断采集 | P0 | ✅ 已修复 |
| P02 | `brain ↔ decision_experts ↔ decision_snapshot` 循环：为读写 KV 直接 import `dapeng_brain` | `decision_experts.py:937`、`decision_snapshot.py:133` | 模块级环，导入顺序敏感，难以单测 | P0 | ✅ 已修复 |
| P03 | `quote_hub ↔ routers.stream` 循环：`quote_hub` 延迟 import `_build_quote` | `quote_hub.py:210` | 同上，且行情视图逻辑散落路由层 | P0 | ✅ 已修复 |
| P04 | 路由层承载业务逻辑：`routers/market_dashboard.py` 390 行，1 个端点 + 8 个数据构造函数 | 同文件 | 路由不可复用、测试需起 HTTP | P0 | ✅ 已修复（抽 services） |
| P05 | 超大模块：10 个模块 > 900 行（`strategies` 1563、`indicators` 1541、`routers/automation` 1341、`routers/admin` 1280、`routers/kline` 1215、`qlib_factor_engine` 1209、`stable_market_provider` 1089、`market_coverage` 1071、`fundamental` 1021、`decision_experts` 1019） | arch_report | 认知负担高、合并冲突频繁、单测粒度粗 | P2 | ✅ 已修复（2026-11-02：门面 + 分片，>900 行模块 10 → 0，最大 694 行） |
| P06 | 辅助函数重复：`_now_iso` ×9、`_finite` ×6、`_safe_float` ×6、`_clamp` ×4、`_fetch` ×9 | arch_report | 语义漂移（有的返回 0，有的返回 None） | P1 | ✅ 已修复（数值/时间/clamp 全部收敛到 `server/core`；`_fetch` 属不同 HTTP 客户端，保留） |
| P07 | 单例/装配分散：KV 通过 trading_facade 隐式获取 | `trading/__init__.py:114` | 新模块容易绕过端口直接 import | P1 | ✅ 已修复（`infra.kv` 端口 + 装配期 bind；`review_service` 归入交易域） |
| P08 | 测试基建不一致：部分测试用 `asyncio.get_event_loop()`，与 `asyncio.run` 混用时随机失败 | `tests/test_data_bus.py` | 测试顺序敏感 | P1 | ✅ 已修复 |
| P09 | 命名不一致：`_build_dashboard` / `build_dashboard` 并存，`_finite` 与 `_safe_float` 语义不同 | 多文件 | 阅读成本、易误用 | P1 | ✅ 已修复（`to_float` / `safe_float` 二分；旧名仅作 import 别名） |
| P10 | 工程卫生：项目根目录堆积 20+ 个临时脚本（`_verify_*.mjs`、`_audit_*.py`、`_patch_*.py`） | `ls` 根目录 | 交付物与调试脚本混杂 | P3 | ✅ 已修复（2026-11-02：归档到 `scripts/verify/` 与 `_archive/`，见 `REPO-HYGIENE.md`） |

---

## 4. 优化目标

| 目标 | 可量化标准 |
|---|---|
| G1 依赖单向 | import 环 = 0；未登记反向依赖 = 0 |
| G2 路由变薄 | 路由文件只含端点；业务函数 0 个；单文件 ≤ 250 行 |
| G3 模块可控 | > 900 行模块数量从 10 降到 ≤ 3 |
| G4 契约稳定 | 端点路径与响应字段不变；新增字段只追加 |
| G5 可观测 | 架构报告可在 CI 执行；违规即失败 |
| G6 可维护 | 数值解析/时间格式化/错误响应 各只有一处实现 |

---

## 5. 分阶段实施

### P0 · 测绘与守卫（2026-11-02，已完成）

**步骤**

1. 编写 `tools/arch_report.py`：AST 解析模块/函数/路由/依赖，输出分层分布、环、反向依赖、超大模块、重复函数、裸 except、TODO。
2. 修正测绘本身的归类错误（`server.` 前缀导致全部误判为 core）。
3. 新增 `tests/test_architecture.py` 7 项断言：无环、无未登记反向依赖、已知项必须带计划、services 不得依赖 routers、dashboard 单一实现、KV 端口降级、行情视图不补 0。

**验收**

- `python tools/arch_report.py --check` 退出码 0。
- `pytest tests/test_architecture.py` 全绿。
- 报告能稳定输出 127 模块 / 178 路由的真实数字。

**预期效果**：结构问题从「靠记忆」变成「可测量、可回归」。

---

### P1 · 依赖治理与共享内核（2026-11-02 完成）

**步骤**

1. ✅ 抽取 `server/services/market_dashboard.py`：`build_dashboard()` / `get_dashboard()` / `last_good()`；路由保留 `_build_dashboard` 别名。
2. ✅ 新增 `server/infra/kv.py`：KV 端口 + 装配期绑定；`brain` 委托、`experts`/`snapshot` 直连端口。
3. ✅ 抽取 `server/services/quote_view.py`：`build_quote` / `bar_ts` / `market_open` / `opt_num`；`stream` 保留别名，`quote_hub` 改用服务。
4. ✅ `trading/loop_scheduler.py → routers.review`：复盘装配与周报下沉 `server/trading/review_service.py`，路由与调度器共同依赖（归入交易域，避免 services 跨层）。
5. ✅ 合并重复辅助函数：新增 `server/core/numbers.py`（`to_float` / `safe_float` / `to_int` / `clamp`）与 `server/core/timeutil.py`（`now_iso` / `iso_of`），
   迁移 19 个模块、约 120 处调用；旧名以 import 别名保留，调用点零改动。

**验收**

- 环 = 0、反向依赖 = 0（loop_scheduler 项已清零，白名单为空）。
- `def _finite` / `_now_iso` / `_safe_float` / `_clamp` 本地定义 **19 → 0**（全部指向 `server/core`）。
- 全量测试 **243 项通过**（新增 20 项 core 工具契约）。
- `/api/market-dashboard` 热缓存 **9.3–10ms**；`server.main` 导入 **1.54s**（改造前 1.59s）。

**预期效果**：数据层可脱离 HTTP 独立复用；研判引擎可单测；循环依赖清零后导入顺序不再敏感。

---

### P2 · 模块瘦身（已完成 2026-11-02）

**实际做法**（与初版计划的差异：不新增 `services/portfolio.py`，改为门面 + 分片）

所有拆分由 `tools/split_module.py` 机械执行并登记计划，禁止手工剪切：

| 母模块 | 行数 | 分片 | 单片最大 |
|---|---|---|---|
| `strategies` | 1562 | `strategies_scoring` / `_engine` / `_patterns` | 608 |
| `indicators` | 1540 | `indicators_base` / `_trend` / `_volume` / `_registry` | 581 |
| `routers/automation` | 1341 | `automation_common` / `_config` / `_trade`（聚合器） | 685 |
| `routers/admin` | 1279 | `admin_common` / `_core` / `_ops` / `_stats`（聚合器） | 586 |
| `routers/kline` | 1214 | `kline_common` / `_series` / `_analysis`（聚合器） | 592 |
| `qlib_factor_engine` | 1208 | `qlib_factor_lib` / `_stats` / `_engine_impl` | 694 |
| `stable_market_provider` | 1088 | `stable_cache` / `_em` / `_sector` / `_provider_impl` | 462 |
| `market_coverage` | 1070 | `market_coverage_fetch` / `_sources` / `_macro` / `_payload` / `_worker` | 302 |
| `fundamental` | 1020 | `fundamental_periods` / `_metrics` / `_snapshot` / `_valuation` | 420 |
| `decision_experts` | 1018 | `decision_experts_base` / `_global` / `_ledger` / `_aggregate` | 336 |

路由文件的门面变成**聚合器**（`router.include_router(子路由.router)`），`main.py` 的
`app.include_router(automation_router.router, prefix="/api/automation")` 一行未改。

**拆分器踩到的三个坑**（都已修进工具，见 ADR-002）

1. `from __future__ import annotations` 让注解成为字符串，`symtable` 不记为引用 →
   分片漏掉 `ConfigUpdateBody`，`app.openapi()` 抛 `PydanticUserError`，`/docs` 500。
   工具增加 AST 注解扫描，并按函数作用域扣除局部导入（`market_sync` 这类误报）。
2. 分片成环（`signals ↔ tech`、`engine_impl ↔ output`）会让门面导入失败。
   工具改为**检测到环即报错并提示合并区间**。
3. 门面若把「头部导入进来的名字」（`Any` / `logger` / `dataclass`）也再导出，
   会与各分片的 owner 解析冲突。门面只导出**本片定义**的名字。

**验收结果**

| 项 | 目标 | 实测 |
|---|---|---|
| 单文件行数 | ≤ 900 | 最大 **694**（`qlib_factor_engine_impl`） |
| > 900 行模块 | ≤ 3 | **0** |
| 端点与字段 | 完全不变 | 182 条 method+path 逐条一致（排序 diff 为空），并冻结为 `tests/data/routes_baseline.json` |
| 全量测试 | 通过 | 243 → **253 项通过** |
| 冷启动导入 | +10% 以内 | `server.main` 导入 1.54s → **1.59/1.64/1.61s**（3 次实测中位 1.61s，+4.5%，来自门面再导出） |
| 代码总量 | —— | 44958 → **46343 行**（+3.1%，含 contracts 层；每片重复 import 头 + 门面导出的净开销） |
| 依赖关系 | 不新增环/反向依赖 | 环 **0**、跨层反向依赖 **0**（`arch_report --check` 退出码 0）；模块 131 → **170**、函数 1444 → 1443、同名函数 19 → 18 |

**兼容性代价（必须知道）**

门面只做再导出，因此 `server.decision_experts.EXPERT_LEDGER_PATH = x` 不再影响
分片内的同名全局。规则：**可变路径/开关 patch 实现模块**
（`server.decision_experts_ledger`）。修正 `tests/test_decision_loop.py` 时顺带
发现真实缺陷：`get_latest_expert_panel()` 用了 `kv_get` 但只在另一函数里局部导入，
调用即 `NameError`——已把 `kv_get` / `kv_set` 提到模块级。

**预期效果**：合并冲突按域收敛；单测可按域运行；新成员按「分片标题」定位。

---

### P3 · 契约与错误处理统一（已完成 2026-11-02）

**实际做法**

1. ✅ 新增 `server/contracts/envelope.py`：`SCHEMA_VERSION="1.0"`、`ErrorCode`
   （`bad_request` / `not_found` / `upstream_timeout` / `data_degraded` …）、
   `code_for_status()`、`ok()`、`fail(code, message, detail)`、`degraded_payload()`。
2. ✅ 响应头 `X-Schema-Version: 1.0` 注入全局中间件（payload 零改动即可灰度）。
3. ✅ 3 份同构 `_ok` / `_fail` 改为契约层别名：`automation_common`、`evolution`、`review`。
4. ⏸ `etf` / `ict` 的**领域降级信封**（`source/as_of/age/degraded`）暂不并入，
   仅复用 `degraded_payload` 的字段约定——契约层不猜业务语义。
5. ⏸ `server/core/errors.py`（领域异常基类 → HTTP 映射）顺延到下一轮：
   当前 178 路由的失败路径以显式 `_fail(...)` 为主，异常上抛集中在全局中间件，
   引入异常体系属新增能力，不属本次收敛范围。

**验收结果**

| 项 | 目标 | 实测 |
|---|---|---|
| 4xx/5xx 含 `code` + `message` | 是 | 迁移的 3 个路由全部满足；错误码在枚举内（`ErrorCode` 常量） |
| 前端零改动 | 是 | 只追加 `code` / `schema_version`，`ok` / `data` / `message` 形状不变 |
| 契约测试 | 冻结快照 | `tests/test_contracts.py` 10 项：信封字段、状态码映射、路由快照、OpenAPI 可生成、门面纯度 |
| 版本可见 | 是 | 所有响应头带 `X-Schema-Version: 1.0` |

**预期效果**：排障从「看字符串」变「看错误码」；版本升级可灰度。

---

### P4 · 工程卫生与文档同步（已完成 2026-11-02）

**实际做法**

1. ✅ 根目录临时脚本归档：`_verify_*.mjs` / `_shot_*.mjs` / `_test_cache.py` →
   `scripts/verify/`；`_patch_*.py` / `_audit_undefined.py` → `_archive/patches/`；
   截图与中间 json → `_archive/tmp/`；拆分前原件 → `_archive/split-2026-11-02/`。
   完整映射表见 `docs/10-architecture/REPO-HYGIENE.md` §二。
2. ✅ 另清掉一个历史事故：根目录下名为 `server" && cp C:…` 的**目录树**
   （Windows 私用区字符，`U+F022`），内含 `payment/` 的误拷贝副本。
3. ✅ ADR 补齐：`ADR-001-contract-envelope.md`、`ADR-002-module-split-facade.md`。
4. ✅ 守卫入口 `scripts/ci_guard.py`：`arch_report --check` + `split_module --list` +
   `pytest tests -q`，任一失败退出非 0（CI 只接这一条命令，避免双份判定）。
5. ✅ `.gitignore` 补 `_routes_*.txt` / `_chk.json` / `_e.json` / `_backup/` /
   `.pytest_cache/` / `artifacts/` 等开发期产物。
6. ⏸ `nul`（Windows 设备名残留）无法被 trash 接口删除（`ERROR \\.\nul: TargetedRoot`），
   已在 `.gitignore` 忽略。

**验收结果**

| 项 | 目标 | 实测 |
|---|---|---|
| 根目录临时文件 | 0 | `_*.py` / `_*.mjs` / `_*.json` / `_*.txt` = **0** |
| 守卫可跑 | 是 | `python scripts/ci_guard.py` 三道闸门全 PASS |
| 文档同步 | 是 | 本文档 P01–P10 状态、ADR ×2、REPO-HYGIENE、CHANGELOG 均已更新 |

---

## 6. 向后兼容约束

1. **端点不变**：不删除、不改名、不改 HTTP 方法；新增能力只追加端点。
2. **字段只增不减**：响应字段名与语义保持不变；需要改语义时新增字段并保留旧字段一个版本。
3. **兼容别名**：`routers/market_dashboard.py` 保留 `_build_dashboard`、`CACHE_KEY`、`CACHE_TTL`；
   `routers/stream.py` 保留 `_build_quote`、`_bar_ts`、`_market_open`、`_opt_num`、`_opt_num_any`。
4. **缓存键不变**：`market_dashboard:v3` 等键名不改，避免重启后缓存击穿。
5. **数据库不变**：KV / 交易表结构不动；新增字段走迁移脚本，不在启动时隐式改表。
6. **前端零改动**：所有拆分与抽取不得要求前端同步发版。

---

## 7. 性能影响与预算

| 项 | 改造前 | 改造后 | 预算 |
|---|---|---|---|
| `server.main` 导入 | 1.59s | **1.54s**（消除重复定义后略降） | +5% 以内 |
| `/api/market-dashboard` 热缓存 | 10ms | 10ms | ±10% |
| `/api/market-dashboard` 冷启动 | 1.15s | 1.15s | ±10% |
| 依赖环 | 3 | 0 | 必须为 0 |
| 反向依赖 | 10 | 0（1 已登记） | 必须为 0 |

约束：

- 抽取只做「移动 + 别名」，不引入额外序列化、不复制数据。
- 服务层函数保持无全局可变状态（`_LAST_GOOD_DASHBOARD` 为既有设计，仍由单点持有）。
- 拆分模块不得增加启动时的重复 import；共享常量集中在 core。

---

## 8. 本轮实施记录与验收

**已实施**

| 项 | 文件 | 说明 |
|---|---|---|
| 架构测绘工具 | `tools/arch_report.py`（新增） | AST 测绘 + 分层规则 + 已知违规白名单 |
| 架构守卫测试 | `tests/test_architecture.py`（新增，7 项） | 环/反向依赖/services 边界/契约/降级 |
| 大盘服务下沉 | `server/services/market_dashboard.py`（新增）；`routers/market_dashboard.py`（390 → 30 行） | 5 处调用方改为依赖服务 |
| KV 端口 | `server/infra/kv.py`（新增）；`trading/__init__.py`、`brain.py`、`decision_experts.py`、`decision_snapshot.py` | 消除 3 个环中的 2 个 |
| 行情视图服务 | `server/services/quote_view.py`（新增）；`routers/stream.py`、`quote_hub.py` | 消除 `quote_hub ↔ stream` 环 |

**第二批（P1 收尾，2026-11-02）**

| 项 | 文件 | 说明 |
|---|---|---|
| 复盘服务 | `server/trading/review_service.py`（新增）；`routers/review.py`、`routers/automation.py`、`trading/loop_scheduler.py` | 权益/配对/基准/周报下沉；解除最后 1 条反向依赖 |
| 数值内核 | `server/core/numbers.py`（新增） | `to_float` / `safe_float` / `to_int` / `clamp`，统一缺失语义 |
| 时间内核 | `server/core/timeutil.py`（新增） | `now_iso` / `iso_of`，统一时区与精度 |
| 迁移 | 19 个模块、约 120 处调用 | 旧名以 import 别名保留，调用点零改动 |
| 契约测试 | `tests/test_core_utils.py`（新增 20 项） | 空值/bool/NaN/inf/时区/精度全覆盖 |

**验收结果**

| 项 | 结果 |
|---|---|
| 依赖环 | 3 → **0** |
| 未登记反向依赖 | 10 → **0**（1 条已登记，带 P2 计划） |
| 全量测试 | 216 → **223 项通过**（新增 7 项架构断言） |
| 接口回归 | `/api/market-dashboard`、`/api/decision/snapshot|sentiment|experts|coverage|scan|bus`、`/api/stream/market|stats` 全部 200 |
| 性能 | 热缓存 10ms；冷启动 1.15s；`server.main` 导入 1.59s（与改造前持平） |
| 兼容 | 路由别名保留，端点与字段未变，前端无需改动 |

---

## 9. 风险与回滚

| 风险 | 触发条件 | 处置 |
|---|---|---|
| 服务层抽取引入导入错误 | 启动失败 / ImportError | 回滚对应文件：路由恢复原实现（git 无提交时使用 `backups/` 或本文档附带的别名方案） |
| KV 端口未装配导致专家团不缓存 | `trading` 未导入 | `kv_get` 返回默认值，功能降级但可用；启动日志出现 `kv store unavailable` |
| 服务层与路由双份实现漂移 | 有人复制回路由 | 架构测试 `test_dashboard_service_is_shared_source` 断言同一对象 |
| 拆分导致响应字段变化 | P2 拆分时 | 用路由快照（`tests/data/routes_baseline.json`）对比，字段只增不减；`test_openapi_generates` 兜住注解解析失败 |
| 门面 monkeypatch 失效 | 测试或外部脚本改模块级可变路径 | 改实现模块（如 `server.decision_experts_ledger`）；已在 ADR-002 写明 |

---

## 10. 维护约定

1. 新增模块先定层（api/engine/data/core/infra），再写 import。
2. 需要跨层回调时用端口（参考 `infra/kv.py`），不允许反向 import。
3. 提交前跑 `python scripts/ci_guard.py`（架构测绘 + 拆分计划 + 全量测试）；改路由必须同步 `tests/data/routes_baseline.json`。
4. 本文档第 3 节状态列必须与代码同步；已修复项标注日期与验证方式。
