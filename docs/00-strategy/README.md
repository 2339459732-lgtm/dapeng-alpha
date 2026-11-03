# 文档地图（Document Map）

> `docs/` 下同时存在**三类性质不同**的文档。读之前先看这里，判断"该信哪一篇"。
> 在线访问：`http://localhost:8900/docs/00-strategy/README.md`（`/docs` 为 FastAPI 静态挂载）。

---

## 一、现状文档（描述"系统当前如何工作"——必须与实现一致）

> **这几篇是"真相源"。任何冲突以它们为准。**

| 文档 | 作用 | 入口 |
|---|---|---|
| **MASTER-PLAN-2026Q4.md** | 总纲：战略定位 / 北极星指标 / **M1~M9 目标与验收结论** / 路线图 / 数据真实性红线 / **已知限制** | [`MASTER-PLAN-2026Q4.md`](MASTER-PLAN-2026Q4.md) |
| **DATA-DICTIONARY.md** | 数据口径字典：数据源清单与可用性 / 四级降级链 / 字段单位与来源标注 / **数据源陷阱** / 板块层级 / 配色铁律 / **已废弃数据源** | [`../30-data/DATA-DICTIONARY.md`](../30-data/DATA-DICTIONARY.md) |
| **auto-trading-architecture.md** | 自动交易架构唯一权威蓝图（含 `store.py` 启动顺序约束） | [`../10-architecture/auto-trading-architecture.md`](../10-architecture/auto-trading-architecture.md) |
| **DECISION-HUB.md** | 主脑中心的三层结构（事实 / 判断 / 决策）：互斥切换、hash 路由、情绪单一口径、轮询收敛 | [`../10-architecture/DECISION-HUB.md`](../10-architecture/DECISION-HUB.md) |
| **A-SHARE-REALTIME-DECISION-DESK.md** | 决策台架构规范：分层与进程模型、三层职责边界与数据契约、资源图与闸门、配置升级/回滚、延迟预算与实测、扩展路径、A1–A8 验收标准 | [`../10-architecture/A-SHARE-REALTIME-DECISION-DESK.md`](../10-architecture/A-SHARE-REALTIME-DECISION-DESK.md) |
| **DECISION-ROUTING.md** | 决策分流：多市场指标接入与字段定义、指标→条件映射与权重、优先级与阈值、时效要求、降级兜底、交付物与校验方式 | [`../10-architecture/DECISION-ROUTING.md`](../10-architecture/DECISION-ROUTING.md) |
| **LAYER-SYNC-CONTRACT.md** | 层间同步契约：三层同步的内容范围 / 字段规范 / 更新频率 / 传递方式，全市场覆盖与时效实测，`lineage.v1` 结论溯源与 S1–S9 验收 | [`../10-architecture/LAYER-SYNC-CONTRACT.md`](../10-architecture/LAYER-SYNC-CONTRACT.md) |
| **LAYER-CROSSCHECK.md** | 三层对证与阈值集中配置：九项检查、五档状态语义、可比率与 blocker、`brain-config.v1` 配置项 | [`../10-architecture/LAYER-CROSSCHECK.md`](../10-architecture/LAYER-CROSSCHECK.md) |
| **DEPLOYMENT.md** | 部署与运维：启动 / 解释器自动探测 / 看门狗 / 端口 / 自愈 | [`../40-ops/DEPLOYMENT.md`](../40-ops/DEPLOYMENT.md) |

---

## 二、计划文档（描述"后续要做什么"——仍在生效的规划，非现状也非历史）

> 这些是**方向与计划**，不代表已实现。当前实现状态以上面的现状文档为准。

| 文档 | 作用 | 入口 |
|---|---|---|
| **DECISION-CLOSED-LOOP-PLAN-2026Q4.md** | 决策闭环整体规划：动态调查数据契约 / 六专家主脑 / 情绪六维 / 三系统闭环与优先级 | [`DECISION-CLOSED-LOOP-PLAN-2026Q4.md`](DECISION-CLOSED-LOOP-PLAN-2026Q4.md) |
| **product-roadmap-2026.md** | 产品路线图（Phase 1/2/3 任务勾选） | [`product-roadmap-2026.md`](product-roadmap-2026.md) |
| **capability-evolution-plan-2026.md** | 交易能力持续提升体系（Phase 0-4） | [`capability-evolution-plan-2026.md`](capability-evolution-plan-2026.md) |
| **OPEN_SOURCE_INTEGRATION_PLAN.md** | 开源能力融合规划（5+ 项目核验 / 分阶段） | [`OPEN_SOURCE_INTEGRATION_PLAN.md`](OPEN_SOURCE_INTEGRATION_PLAN.md) |

---

## 三、历史设计（描述"某版本当时的设计"——仅作演进记录，勿据此开发）

> 均已在文件顶部加"历史设计 / 状态更新"横幅。正文保留原样。

### 架构设计（`../10-architecture/`）
- [`architecture-v54-sidebar-pro.md`](../10-architecture/architecture-v54-sidebar-pro.md)（v5.4）
- [`architecture-sidebar-tools.md`](../10-architecture/architecture-sidebar-tools.md)（v5.3）
- [`architecture-tab-admin.md`](../10-architecture/architecture-tab-admin.md)（v5.1）
- [`architecture-westock-mcp.md`](../10-architecture/architecture-westock-mcp.md)（v5.0，含已废弃 westock）
- [`system_design.md`](../10-architecture/system_design.md)（早期 2026-05 基线）
- 图：`class-diagram*.mermaid` / `sequence-diagram*.mermaid`（早期 / v5.4 / westock 接入）

### 增量 PRD（`../20-prd/`）
- [`auto-trading-prd.md`](../20-prd/auto-trading-prd.md)（自动化交易需求）
- [`incremental-prd-v54-sidebar-pro.md`](../20-prd/incremental-prd-v54-sidebar-pro.md)（v5.4）
- [`incremental-prd-sidebar-tools.md`](../20-prd/incremental-prd-sidebar-tools.md)（v5.3）
- [`incremental-prd-tab-admin.md`](../20-prd/incremental-prd-tab-admin.md)（v5.1）
- [`incremental-prd-westock-mcp.md`](../20-prd/incremental-prd-westock-mcp.md)（v5.0，含已废弃 westock）

---

## 四、归档（`../90-archive/`——过期 / 已废弃，仅考古）

- [`HANDOFF-v5.4.md`](../90-archive/HANDOFF-v5.4.md)（版本已落后）
- [`SYSTEM-OPTIMIZATION-v4.0.md`](../90-archive/SYSTEM-OPTIMIZATION-v4.0.md)（已被 MASTER-PLAN 取代）
- [`auto-trading-arch-v0-deprecated.md`](../90-archive/auto-trading-arch-v0-deprecated.md)（已废弃草案）

---

## 四·五、界面设计记录（`../10-architecture/`）

> 描述"界面当前如何设计与维护"的现状记录，改动会同步更新版本表。

| 文档 | 作用 | 入口 |
|---|---|---|
| **AXON-GRAPH-DESIGN.md** | 文档关系图版（AXON）与自动化中心驾驶舱的设计编码、交互模型、三维视角、版本记录与同步流程 | [`AXON-GRAPH-DESIGN.md`](../10-architecture/AXON-GRAPH-DESIGN.md) |
| **UI-VISUALIZATION-ROADMAP.md** | 界面与可视化路线：3D 立体 360°、数据源登记、节奏原则与验收基线 | [UI-VISUALIZATION-ROADMAP.md](../10-architecture/UI-VISUALIZATION-ROADMAP.md) |

---

## 五、一眼判定规则

| 你看到 | 属于 | 该不该信 |
|---|---|---|
| 顶部有 **📋 规划文档（living）** | 计划文档 | 读方向，**实现状态看现状文档** |
| 顶部有 **📎 历史设计 / 状态更新** | 历史设计 | **仅作演进记录，勿据此开发** |
| 顶部有 **⚠️ 已归档** | 归档 | 仅考古 |
| 无任何标记 | 现状文档 | **以它为准** |

> 既有"现状文档"与"历史设计"矛盾时，一律以**现状文档**为准；发现矛盾请按"任何错误直接执行修正"的原则当场改掉。

- [Impeccable 设计接入](../10-architecture/IMPECCABLE-INTEGRATION.md)：技能本地化、检测器脚本与当前基线判定。
---

## 2026 Q4 战略文件

- [产品战略与路线图](PRODUCT-STRATEGY-2026Q4.md)：定位、护城河、P0–P3 路线、指标与预算。
- [竞品参考：ai1foo 股先生](REFERENCE-AI1FOO.md)：学习任务化/专家化/基准化，不照搬荐股平台。
- [数据平台与数据源治理](../10-architecture/DATA-PLATFORM.md)
- [云服务器部署与成本](../40-ops/CLOUD-DEPLOYMENT.md)
