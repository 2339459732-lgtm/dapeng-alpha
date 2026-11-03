# ADR-002：超大模块按「门面 + 分片」拆分

- 状态：已采纳（2026-11-02）
- 决策者：架构治理轮次 P2
- 影响面：`server/` 下 10 个 >900 行模块、`tools/split_module.py`、`tools/arch_report.py`

## 背景

P1 结束时仍有 10 个模块超过 900 行（最大 `strategies.py` 1562 行）：

```
strategies 1562 · indicators 1540 · routers/automation 1341 · routers/admin 1279
routers/kline 1214 · qlib_factor_engine 1208 · stable_market_provider 1088
market_coverage 1070 · fundamental 1020 · decision_experts 1018
```

问题不在行数本身，而在：① 单文件内聚了「结构体 + 计算 + 装配 + 出口」四类职责；
② 改动一处必须在 1500 行里找上下文；③ code review 无法按职责分批。

## 决策

采用**门面 + 分片**（facade + shards），而不是直接改成包：

- 新增 `server/<name>_<aspect>.py` 若干分片，每片一个内聚职责；
- 原路径 `server/<name>.py` 保留为**只做再导出**的门面，
  `from server.strategies import compute_ma` 等既有导入全部不变；
- 路由文件的门面改成**聚合器**：`router = APIRouter()` +
  `router.include_router(子路由.router)`，`main.py` 的注册语句不动。

拆分由 `tools/split_module.py` 机械执行（可复现，不是手工剪切）：

1. 顶层 `import` 视为共享头部，逐片复制；
2. 其余顶层节点按「下标区间」分配，顺序与原文一致；
3. 用 `symtable` 算出每片的全局引用，自动补 `from <owner> import ...`；
4. `from __future__ import annotations` 会让注解变成字符串、`symtable` 看不见，
   因此**额外 AST 扫描注解**（并按函数作用域扣除局部导入）——这一步漏了就会
   出现 `ConfigUpdateBody` 未导入、`app.openapi()` 抛 `PydanticUserError`；
5. 拆分片之间若成环（例如 `signals ↔ tech`），工具直接报错并要求合并区间，
   不生成无法导入的代码。

## 关键取舍

**为什么不改成 Python 包（`server/strategies/__init__.py`）**
改成包需要移动文件、处理 `__init__` 相对导入，且 `arch_report` 的模块名会从
`server.strategies` 变 `server.strategies.<sub>`，分层映射与历史文档全部要改。
门面方案的文件布局变化最小。

**monkeypatch 语义变了（重要）**
门面只做再导出，`server.decision_experts.EXPERT_LEDGER_PATH = x` 不会再影响
分片里的同名全局。规则：**可变路径/开关必须 patch 实现模块**
（`server.decision_experts_ledger`）。`tests/test_decision_loop.py` 已按此修正，
并因此发现了一个真实缺陷：`get_latest_expert_panel()` 用了 `kv_get` 但只在
另一个函数里局部导入，调用即 `NameError`（该函数此前无调用方，未暴露）。

## 后果

- 超大模块（>900 行）**10 → 0**；模块数 131 → 168；总代码行 44958 → 46849
  （+4.2%，来自每片重复的 import 头与门面再导出，属可接受开销）。
- 依赖环 0、跨层反向依赖 0（`arch_report --check` 通过）。
- 路由表 182 条 method+path **逐条一致**（排序 diff 为空）。
- 全量测试 243 → 253 项通过；新增「门面不得再长出实现函数」守卫
  （`tests/test_contracts.py::test_facades_are_reexport_only`），防止拆分回潮。

## 相关

- `tools/split_module.py`（拆分器与计划登记）
- `docs/10-architecture/REPO-HYGIENE.md`（脚本归档与守卫入口）
