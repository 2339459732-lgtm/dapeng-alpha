# AXON 图谱数据总表 · GRAPH DATA

> 由 `plate-graph.html` 内嵌 `__AXON__` 数据块生成（2026-10-26T14:30:00+08:00），与 `static/docs-graph.json` 扫描同一批文档。
> 改文档后：重跑 `python tools/gen_docs_graph.py` → 同步内嵌块 → 重生成本表（本表自身的行数字节随重生微漂，属已知特性）。
> 口径同源：[DATA-DICTIONARY.md](DATA-DICTIONARY.md)。

## 一、全图口径（互引按文档对去重）

| 指标 | 值 | 指标 | 值 |
|---|---|---|---|
| 文档 | 36 | 关系（边 / 去重对） | 100 / 96 |
| 密度 | 0.1524 | 平均度 | 5.33 |
| 最高度 | 24 | 直径 | 5 |
| 连通块 | 1（最大 36） | 孤立 | 0 |
| 模块度 | 0.2699 | 群落 | 4 |

## 二、板块（层级）配置

| 编号 | 板块 | 份数 | 行数 | 体积 | 板块内 / 跨板块对 | 桥接 | 枢纽 | 最近修订 |
|---|---|---|---|---|---|---|---|---|
| RT | 根目录 | 2 | 1 491 | 127 KB | 1 / 8 | 1 | 项目总览（8 引） | 2026-10-25 |
| 00 | 战略与计划 | 5 | 1 136 | 78 KB | 7 / 38 | 1 | 文档地图（24 引） | 2026-10-24 |
| 20 | 产品需求 | 6 | 2 266 | 180 KB | 5 / 18 | 0 | OpenBB 需求（10 引） | 2026-10-19 |
| 10 | 架构与设计 | 14 | 8 317 | 467 KB | 15 / 28 | 1 | OpenBB 设计（11 引） | 2026-10-25 |
| 30 | 数据字典 | 2 | 407 | 29 KB | 1 / 18 | 1 | 数据口径（19 引） | 2026-10-26 |
| 40 | 运维与交付 | 4 | 736 | 45 KB | 3 / 8 | 1 | 部署运维（7 引） | 2026-10-24 |
| 90 | 归档 | 3 | 856 | 41 KB | 2 / 6 | 0 | v4.0 报告（5 引） | 2026-10-16 |

## 三、可信层级分布

| 层级 | 含义 | 份数 |
|---|---|---|
| 真相源 | 描述系统当前如何工作，冲突以它为准 | 7 |
| 生效计划 | 描述后续要做什么，不代表已实现 | 9 |
| 历史设计 | 某个版本当时的设计，仅作演进记录 | 12 |
| 归档 | 过期 / 已废弃，仅考古 | 3 |
| 索引台账 | 导航与变更记录，不对应单一模块 | 5 |

## 四、文档清单（按板块与被引度排序）

| 板块 | 名称 | 编号 | 可信 | 类型 | 修订 | 被引 | 路径 |
|---|---|---|---|---|---|---|---|
| RT | 项目总览 | README | 索引台账 | 入口文件 | 2026-10-19 | 8 | README.md |
| RT | 更新日志 | CHANGELOG | 索引台账 | 入口文件 | 2026-10-25 | 2 | CHANGELOG.md |
| 00 | 文档地图 | DOC-MAP | 索引台账 | 索引 | 2026-10-24 | 24 | docs/00-strategy/README.md |
| 00 | 总体规划 | MASTER-PLAN | 真相源 | 规范 / 清单 | 2026-10-16 | 17 | docs/00-strategy/MASTER-PLAN-2026Q4.md |
| 00 | 开源融合 | OPEN-SOURCE | 生效计划 | 规范 / 清单 | 2026-10-16 | 4 | docs/00-strategy/OPEN_SOURCE_INTEGRATION_PLAN.md |
| 00 | 产品路线 | ROADMAP | 生效计划 | 规范 / 清单 | 2026-10-16 | 4 | docs/00-strategy/product-roadmap-2026.md |
| 00 | 能力进化 | CAPABILITY | 生效计划 | 规范 / 清单 | 2026-10-16 | 3 | docs/00-strategy/capability-evolution-plan-2026.md |
| 20 | OpenBB 需求 | OPENBB-PRD | 生效计划 | 需求文档 | 2026-10-19 | 10 | docs/20-prd/openbb-dsa-integration-prd.md |
| 20 | 自动交易 | PRD-AUTO | 历史设计 | 需求文档 | 2026-10-16 | 5 | docs/20-prd/auto-trading-prd.md |
| 20 | Tab 重构 | PRD-TAB | 历史设计 | 需求文档 | 2026-10-16 | 4 | docs/20-prd/incremental-prd-tab-admin.md |
| 20 | v5.4 侧栏 | PRD-V5.4 | 历史设计 | 需求文档 | 2026-10-16 | 4 | docs/20-prd/incremental-prd-v54-sidebar-pro.md |
| 20 | MCP 接入 | PRD-MCP | 历史设计 | 需求文档 | 2026-10-16 | 3 | docs/20-prd/incremental-prd-westock-mcp.md |
| 20 | 侧栏工具 | PRD-SIDEBAR | 历史设计 | 需求文档 | 2026-10-16 | 2 | docs/20-prd/incremental-prd-sidebar-tools.md |
| 10 | OpenBB 设计 | OPENBB-DESIGN | 生效计划 | 设计文档 | 2026-10-19 | 11 | docs/10-architecture/openbb-dsa-integration-design.md |
| 10 | 图谱设计 | AXON | 真相源 | 设计文档 | 2026-10-25 | 9 | docs/10-architecture/AXON-GRAPH-DESIGN.md |
| 10 | 系统工作流 | WORKFLOW | 真相源 | 设计文档 | 2026-10-19 | 5 | docs/10-architecture/SYSTEM-WORKFLOW.md |
| 10 | 界面路线 | UI-ROADMAP | 生效计划 | 设计文档 | 2026-10-23 | 5 | docs/10-architecture/UI-VISUALIZATION-ROADMAP.md |
| 10 | 侧栏架构 | ARCH-SIDEBAR | 历史设计 | 设计文档 | 2026-10-16 | 4 | docs/10-architecture/architecture-sidebar-tools.md |
| 10 | Tab 后台 | ARCH-TAB | 历史设计 | 设计文档 | 2026-10-16 | 4 | docs/10-architecture/architecture-tab-admin.md |
| 10 | v5.4 架构 | ARCH-V5.4 | 历史设计 | 设计文档 | 2026-10-16 | 4 | docs/10-architecture/architecture-v54-sidebar-pro.md |
| 10 | 交易架构 | ARCH-AUTO | 真相源 | 设计文档 | 2026-10-16 | 4 | docs/10-architecture/auto-trading-architecture.md |
| 10 | 设计接入 | IMPECCABLE | 生效计划 | 设计文档 | 2026-10-24 | 3 | docs/10-architecture/IMPECCABLE-INTEGRATION.md |
| 10 | MCP 架构 | ARCH-MCP | 历史设计 | 设计文档 | 2026-10-16 | 3 | docs/10-architecture/architecture-westock-mcp.md |
| 10 | 系统底稿 | SYS-DESIGN | 历史设计 | 设计文档 | 2026-10-16 | 3 | docs/10-architecture/system_design.md |
| 10 | v5.4 类图 | CLASS-v5.4 | 历史设计 | 设计文档 | 2026-07-07 | 1 | docs/10-architecture/class-diagram-v54.mermaid |
| 10 | 实施清单 | IMPL-LIST | 生效计划 | 规范 / 清单 | 2026-10-19 | 1 | docs/10-architecture/impl-checklist-openbb-dsa.md |
| 10 | v5.4 时序图 | SEQ-v5.4 | 历史设计 | 设计文档 | 2026-07-07 | 1 | docs/10-architecture/sequence-diagram-v54.mermaid |
| 30 | 数据口径 | DATA-DICT | 真相源 | 数据字典 | 2026-10-16 | 19 | docs/30-data/DATA-DICTIONARY.md |
| 30 | 图谱数据总表 | GRAPH-DATA | 索引台账 | 数据字典 | 2026-10-26 | 1 | docs/30-data/GRAPH-DATA.md |
| 40 | 部署运维 | DEPLOYMENT | 真相源 | 运维文档 | 2026-10-24 | 7 | docs/40-ops/DEPLOYMENT.md |
| 40 | 接口参考 | API-REF | 真相源 | 运维文档 | 2026-10-19 | 3 | docs/40-ops/API-REFERENCE.md |
| 40 | 修复总账 | LEDGER-10-22 | 索引台账 | 运维文档 | 2026-10-22 | 2 | docs/40-ops/CHANGELOG-2026-10-22.md |
| 40 | 系统加固 | MOAT | 生效计划 | 运维文档 | 2026-10-24 | 2 | docs/40-ops/HARDENING-MOAT.md |
| 90 | v4.0 报告 | OPT-v4.0 | 归档 | 归档 | 2026-10-14 | 5 | docs/90-archive/SYSTEM-OPTIMIZATION-v4.0.md |
| 90 | v5.4 交接 | HANDOFF-v5.4 | 归档 | 归档 | 2026-10-16 | 3 | docs/90-archive/HANDOFF-v5.4.md |
| 90 | 架构 v0 | ARCH-v0 | 归档 | 归档 | 2026-10-15 | 2 | docs/90-archive/auto-trading-arch-v0-deprecated.md |

## 五、群落

| 群落 | 规模 | 标签 | 锚点 |
|---|---|---|---|
| c0 | 11 | 产品需求 | 数据口径 |
| c1 | 9 | 架构与设计 | 文档地图 |
| c2 | 9 | 架构与设计 | OpenBB 设计 |
| c3 | 7 | 运维与交付 | 项目总览 |
