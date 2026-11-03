#!/usr/bin/env python3
"""模块拆分器（split-module.v1）。

用途
----
把单个超大模块按「符号块」切成若干子模块，原路径保留为**再导出门面**
（或路由聚合器），保证 ``from server.x import y`` 全部旧导入不变。

为什么不用手工切割
------------------
手切最容易漏两件事：① 子块引用了别处的模块级常量/函数；② 路由文件里的
``router`` 对象被复制成多个实例导致路由丢失。本工具用 ``symtable`` 精确
计算每个子块的全局引用，再按「谁定义谁被导入」自动补 import。

拆分规则
--------
* 顶层 ``import`` / ``from ... import`` 视为**共享头部**，逐份复制；
* 其余顶层节点按 ``body`` 下标区间分配给各片，顺序保持原文；
* ``__all__`` 单独抽出，写在门面里；
* ``replicate`` 列出的节点（如 ``router = APIRouter(...)``）在每个片各来一份；
* 每片顶部加 ``from <owner> import a, b`` 兜住跨片引用。

用法
----
    python tools/split_module.py --list            # 列出已登记拆分计划
    python tools/split_module.py --dry strategies  # 打印切片计划与新增 import
    python tools/split_module.py --apply strategies
"""
from __future__ import annotations

import argparse
import ast
import builtins
import os
import sys
import symtable
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_BUILTINS = set(dir(builtins))
#: 由导入机制注入的模块级名字，不属于「未解析引用」
_MODULE_DUNDERS = {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__path__", "__dict__", "__all__",
}


# ----------------------------------------------------------------------
#  拆分计划（唯一事实源；新增拆分在此登记）
# ----------------------------------------------------------------------
#  每项：
#    path       原模块路径
#    module     Python 模块名
#    summary    门面 docstring
#    mode       "facade"（再导出） | "router"（include_router 聚合）
#    replicate  需要在每个片复制的节点下标（router 定义）
#    parts      [(module, start, end, title)]，区间为 body 下标，左闭右开
SPECS: Dict[str, dict] = {
    "strategies": {
        "path": "server/strategies.py",
        "module": "server.strategies",
        "summary": "策略信号与评分（门面：实现见 strategies_scoring / _engine / _patterns）",
        "mode": "facade",
        "replicate": [],
        "parts": [
            ("server/strategies_scoring.py", 0, 19,
             "信号结构体、三维评分与技术指标原语（RSI / BOLL / KDJ / ATR / 回撤 / 波动 / VaR）"),
            ("server/strategies_engine.py", 19, 21,
             "策略总装：compute_strategy 与买入建议生成"),
            ("server/strategies_patterns.py", 21, 32,
             "形态与战法（均线 / MACD / 多周期 / 量价 / 涨停 / 龙头 / 反转）"),
        ],
    },
    "indicators": {
        "path": "server/indicators.py",
        "module": "server.indicators",
        "summary": "技术指标计算与注册表（门面：实现见 indicators_base / _trend / _volume / _registry）",
        "mode": "facade",
        "replicate": [],
        "parts": [
            ("server/indicators_base.py", 0, 21,
             "基础工具箱：类型别名、取值适配、rolling/EMA/Wilder、TR 与 DMI 原语"),
            ("server/indicators_trend.py", 21, 25,
             "趋势类指标：supertrend / ichimoku / donchian / keltner"),
            ("server/indicators_volume.py", 25, 36,
             "量价与震荡类指标：vwap / adx / aroon / obv / mfi / atr / stoch / cci / chop / wr"),
            ("server/indicators_registry.py", 36, 59,
             "指标注册表与统一入口：ParamSpec / IndicatorSpec / compute / 目录"),
        ],
    },
    "automation": {
        "path": "server/routers/automation.py",
        "module": "server.routers.automation",
        "summary": "自动化交易路由聚合（配置 / 持仓 / 策略 / 下单，实现见 automation_* 子路由）",
        "mode": "router",
        "replicate": [2],
        "parts": [
            ("server/routers/automation_common.py", 0, 19,
             "共享层：常量、校验工具、响应封装与请求模型"),
            ("server/routers/automation_config.py", 19, 33,
             "配置与账户视图：config / signals / orders / positions / review"),
            ("server/routers/automation_trade.py", 33, 54,
             "交易动作：策略开关与回测、熔断、手工下单、条件单、审计与风控健康"),
        ],
    },
    "admin": {
        "path": "server/routers/admin.py",
        "module": "server.routers.admin",
        "summary": "管理后台路由聚合（看板 / 缓存 / 日志 / 调用统计，实现见 admin_* 子路由）",
        "mode": "router",
        "replicate": [8],
        "parts": [
            ("server/routers/admin_common.py", 0, 16,
             "共享层：CPU 采样、管理员鉴权、文件路径与日志/用户读写"),
            ("server/routers/admin_core.py", 16, 28,
             "运营看板：总览、用户、订单、系统状态与健康、营收趋势、板块热力"),
            ("server/routers/admin_ops.py", 28, 37,
             "运维操作：缓存管理、API 健康、日志与数据导出"),
            ("server/routers/admin_stats.py", 37, 50,
             "统计与通知：站内通知、调用统计、错误排行"),
        ],
    },
    "kline": {
        "path": "server/routers/kline.py",
        "module": "server.routers.kline",
        "summary": "K 线路由聚合（K 线 / 分时 / 共振 / 指标，实现见 kline_* 子路由）",
        "mode": "router",
        "replicate": [1],
        "parts": [
            ("server/routers/kline_common.py", 0, 9,
             "共享层：缓存时长、K 线响应构造与空响应"),
            ("server/routers/kline_series.py", 9, 29,
             "K 线与分时：日/周/月线、实时报价、分时逐笔"),
            ("server/routers/kline_analysis.py", 29, 47,
             "共振与指标：多周期共振、指标目录与批量计算"),
        ],
    },
    "qlib_factor_engine": {
        "path": "server/qlib_factor_engine.py",
        "module": "server.qlib_factor_engine",
        "summary": "因子引擎门面（实现见 qlib_factor_lib / _stats / _engine_impl）",
        "mode": "facade",
        "replicate": [],
        "parts": [
            ("server/qlib_factor_lib.py", 0, 20,
             "因子定义与标度：动量 / 特质波动 / 换手流动性"),
            ("server/qlib_factor_stats.py", 20, 39,
             "统计工具：排名、Pearson/Spearman、IC 汇总、分位表、面板装配"),
            ("server/qlib_factor_engine_impl.py", 39, 46,
             "QlibFactorEngine：筛选、IC 检验、分层回测、因子组合与结果整形"),
        ],
    },
    "stable_market_provider": {
        "path": "server/stable_market_provider.py",
        "module": "server.stable_market_provider",
        "summary": "稳定行情提供器门面（实现见 stable_cache / _em / _sector / _provider_impl）",
        "mode": "facade",
        "replicate": [],
        "parts": [
            ("server/stable_cache.py", 0, 15,
             "缓存与磁盘兜底：进程缓存、磁盘读写、陈旧标记"),
            ("server/stable_em.py", 15, 48,
             "东财通道与熔断：push2 抓取、失败计数、后台探测"),
            ("server/stable_sector.py", 48, 54,
             "板块数据：龙虎榜参数、周期收益、腾讯聚合"),
            ("server/stable_provider_impl.py", 54, 56,
             "StableAStockDataProvider：对外统一取数接口"),
        ],
    },
    "market_coverage": {
        "path": "server/market_coverage.py",
        "module": "server.market_coverage",
        "summary": "全市场覆盖门面（实现见 market_coverage_fetch / _sources / _macro / _payload / _worker）",
        "mode": "facade",
        "replicate": [],
        "parts": [
            ("server/market_coverage_fetch.py", 0, 29,
             "配置与基础工具：常量、原子写、HTTP 抓取、交易时段与涨跌停阈值"),
            ("server/market_coverage_sources.py", 29, 37,
             "行情与基本面源：新浪全 A 分页、东财季度基本面、全球指数"),
            ("server/market_coverage_macro.py", 37, 55,
             "宏观源：美债曲线、NY Fed、ECB、世界银行"),
            ("server/market_coverage_payload.py", 55, 61,
             "口径汇总：宽度、基本面摘要、行业摘要、coverage 载荷"),
            ("server/market_coverage_worker.py", 61, 70,
             "后台采集与反补：volume backfill、coverage worker 生命周期"),
        ],
    },
    "fundamental": {
        "path": "server/fundamental.py",
        "module": "server.fundamental",
        "summary": "基本面门面（实现见 fundamental_periods / _metrics / _snapshot / _valuation）",
        "mode": "facade",
        "replicate": [],
        "parts": [
            ("server/fundamental_periods.py", 0, 27,
             "报告期工具：季度键、TTM、滞后判定、功能开关"),
            ("server/fundamental_metrics.py", 27, 35,
             "指标计算：单季/ TTM 取值、分位、卡片装配"),
            ("server/fundamental_snapshot.py", 35, 37,
             "快照装配：六卡 + 分位 + 覆盖统计"),
            ("server/fundamental_valuation.py", 37, 40,
             "估值序列：历史分位、陈旧判定"),
        ],
    },
    "decision_experts": {
        "path": "server/decision_experts.py",
        "module": "server.decision_experts",
        "summary": "六专家研判门面（实现见 decision_experts_base / _global / _ledger / _aggregate）",
        "mode": "facade",
        "replicate": [],
        "parts": [
            ("server/decision_experts_base.py", 0, 14,
             "共享基座与 E1–E3：工具函数、趋势、轮动、资金专家"),
            ("server/decision_experts_global.py", 14, 25,
             "E4–E6 与两轮互评：全球、情绪、反方审计、研判会"),
            ("server/decision_experts_ledger.py", 25, 31,
             "协作与账本：联合研判、命中记录、账本结算"),
            ("server/decision_experts_aggregate.py", 31, 34,
             "共识聚合与面板出口：加权共识、否决复核、run_expert_panel"),
        ],
    },
}


# ----------------------------------------------------------------------
#  源码切片
# ----------------------------------------------------------------------
class ModuleSource:
    """把一个模块拆成「共享头部 + 顶层节点」两部分的只读视图。"""

    def __init__(self, rel_path: str):
        self.rel_path = rel_path
        self.path = os.path.join(ROOT, rel_path)
        with open(self.path, encoding="utf-8") as fh:
            self.source = fh.read()
        self.tree = ast.parse(self.source)
        self.lines: List[str] = self.source.splitlines(keepends=True)
        self.imports: List[ast.stmt] = []
        self.body: List[ast.stmt] = []
        self.all_node: Optional[ast.stmt] = None
        self.doc_node: Optional[ast.stmt] = None
        if (self.tree.body and isinstance(self.tree.body[0], ast.Expr)
                and isinstance(self.tree.body[0].value, ast.Constant)
                and isinstance(self.tree.body[0].value.value, str)):
            self.doc_node = self.tree.body[0]
        for node in self.tree.body:
            if node is self.doc_node:
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.imports.append(node)
            elif isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "__all__" for t in node.targets
            ):
                self.all_node = node
            else:
                self.body.append(node)

    # -- 行号 ---------------------------------------------------------
    @staticmethod
    def _start(node: ast.stmt) -> int:
        decos = getattr(node, "decorator_list", None) or []
        return min([node.lineno] + [d.lineno for d in decos])

    def segment(self, node: ast.stmt) -> str:
        return "".join(self.lines[self._start(node) - 1: node.end_lineno])

    def run_text(self, nodes: Sequence[ast.stmt]) -> str:
        """取出连续节点源码，含节点之间的空行与注释。"""
        if not nodes:
            return ""
        first, last = nodes[0], nodes[-1]
        start = self._start(first)
        # 向前吃掉紧邻的注释块（属于下一个符号的说明）
        while start > 1 and self.lines[start - 2].lstrip().startswith("#"):
            start -= 1
        text = "".join(self.lines[start - 1: last.end_lineno])
        return text.rstrip() + "\n"

    def header_text(self) -> str:
        return "".join(self.segment(node) for node in self.imports)

    def header_names(self) -> set:
        names = set()
        for node in self.imports:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            else:
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    names.add(alias.asname or alias.name)
        return names


# ----------------------------------------------------------------------
#  全局引用分析
# ----------------------------------------------------------------------
def annotation_names(tree: ast.AST) -> set:
    """收集**必须在模块级可见**的注解名字。

    ``from __future__ import annotations`` 会把注解变成字符串，
    ``symtable`` 不再把它们记为全局引用；但运行时（FastAPI/pydantic 解析
    签名）仍需要这些名字在本模块可见，因此必须单独扫一遍。

    函数体内的局部导入（例如 ``from server.trading import market_sync``）会让
    该函数里的 ``market_sync.Check`` 注解无需模块级绑定，故按作用域扣除。
    """
    names: set = set()

    def collect(node: Optional[ast.AST], out: set) -> None:
        if node is None:
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                collect(ast.parse(node.value, mode="eval").body, out)
            except SyntaxError:
                return
            return
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                out.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                cur: ast.AST = sub
                while isinstance(cur, ast.Attribute):
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    out.add(cur.id)

    def local_bound(fn: ast.AST) -> set:
        bound: set = set()
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                bound.add(sub.id)
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                for alias in sub.names:
                    if alias.name != "*":
                        bound.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(sub, ast.arg):
                bound.add(sub.arg)
            elif isinstance(sub, ast.ExceptHandler) and sub.name:
                bound.add(sub.name)
        return bound

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            anns: set = set()
            args = node.args
            for arg in (list(args.posonlyargs) + list(args.args)
                        + list(args.kwonlyargs) + [args.vararg, args.kwarg]):
                if arg is not None:
                    collect(arg.annotation, anns)
            collect(node.returns, anns)
            names |= (anns - local_bound(node))
        elif isinstance(node, ast.AnnAssign):
            # 模块级 / 类级注解：作用域内绑定同样扣除
            anns = set()
            collect(node.annotation, anns)
            scope = node
            names |= (anns - local_bound(scope))
    return names


def module_scope_report(src: str, filename: str) -> Tuple[set, set]:
    """返回 (模块级绑定的名字, 需要从外部引入的名字)。"""
    table = symtable.symtable(src, filename, "exec")
    bound = {
        s.get_name()
        for s in table.get_symbols()
        if s.is_assigned() or s.is_imported() or s.is_namespace()
    }
    refs: set = set()

    def walk(scope: "symtable.SymbolTable") -> None:
        for sym in scope.get_symbols():
            if not sym.is_referenced():
                continue
            if scope is table:
                if not (sym.is_assigned() or sym.is_imported() or sym.is_namespace()):
                    refs.add(sym.get_name())
            elif sym.is_global():
                refs.add(sym.get_name())
        for child in scope.get_children():
            walk(child)

    walk(table)
    refs |= annotation_names(ast.parse(src))
    return bound, refs - bound - _BUILTINS - _MODULE_DUNDERS


def _find_cycle(graph: Dict[str, set], modname) -> List[str]:
    """在片依赖图上找一条环（返回片路径列表，空表示无环）。"""
    color: Dict[str, int] = {}
    stack: List[str] = []
    found: List[str] = []

    def dfs(node: str) -> bool:
        color[node] = 1
        stack.append(node)
        for nxt in graph.get(node, ()):  # 邻居是模块名，需反查路径
            rel = next((r for r in graph if modname(r) == nxt), None)
            if rel is None:
                continue
            if color.get(rel) == 1:
                idx = stack.index(rel)
                found.extend(stack[idx:])
                return True
            if color.get(rel, 0) == 0 and dfs(rel):
                return True
        stack.pop()
        color[node] = 2
        return False

    for rel in graph:
        if color.get(rel, 0) == 0 and dfs(rel):
            return found
    return []


def _import_line(module: str, names: Sequence[str]) -> str:
    names = sorted(names)
    if len(names) == 1:
        return f"from {module} import {names[0]}\n"
    if sum(len(n) for n in names) + 2 * len(names) < 70:
        return f"from {module} import {', '.join(names)}\n"
    body = "".join(f"    {n},\n" for n in names)
    return f"from {module} import (\n{body})\n"


def _docstring(text: str) -> str:
    return f'"""{text}"""\n\n'


def _dedupe_doc(src: str) -> str:
    """去掉原模块的 docstring（头部 import 里不含 docstring，故通常无需处理）。"""
    return src


# ----------------------------------------------------------------------
#  拆分执行
# ----------------------------------------------------------------------
def plan(spec_name: str) -> dict:
    spec = SPECS[spec_name]
    src = ModuleSource(spec["path"])
    total = len(src.body)
    covered: List[int] = []
    for _mod, start, end, _title in spec["parts"]:
        if not (0 <= start < end <= total):
            raise SystemExit(f"[{spec_name}] 区间越界：{start}..{end}（body 共 {total} 项）")
        covered.extend(range(start, end))
    dup = [i for i in covered if covered.count(i) > 1]
    if dup:
        raise SystemExit(f"[{spec_name}] 节点重复分配：{sorted(set(dup))}")
    missing = [i for i in range(total) if i not in covered]
    if missing:
        names = [f"{i}:{type(src.body[i]).__name__}@{src.body[i].lineno}" for i in missing]
        raise SystemExit(f"[{spec_name}] 存在未分配节点：{names}")
    return {"spec": spec, "src": src, "total": total}


def build(spec_name: str, verbose: bool = False) -> Dict[str, str]:
    """生成 {相对路径: 源码}，不落盘。"""
    info = plan(spec_name)
    spec, src = info["spec"], info["src"]
    replicate = [src.body[i] for i in spec.get("replicate", [])]

    replicate_idx = sorted(set(spec.get("replicate", [])))

    def assemble(start: int, end: int) -> str:
        """把 [start, end) 与复制节点合并成若干连续段，逐段取源码。"""
        idx = sorted(set(range(start, end)) | set(replicate_idx))
        runs: List[List[int]] = []
        for i in idx:
            if runs and i == runs[-1][-1] + 1:
                runs[-1].append(i)
            else:
                runs.append([i])
        chunks = [src.run_text([src.body[i] for i in run]).rstrip("\n") + "\n" for run in runs]
        return "\n\n".join(chunks)

    # 第一轮：不带跨片 import 的裸片，用于统计各自定义了哪些名字
    drafts: Dict[str, dict] = {}
    for mod, start, end, title in spec["parts"]:
        body_text = assemble(start, end)
        draft = _docstring(title) + src.header_text() + "\n" + body_text
        bound, _ = module_scope_report(draft, mod)
        drafts[mod] = {"body": body_text, "bound": bound, "title": title}

    def modname(rel: str) -> str:
        return rel[:-3].replace("/", ".") if rel.endswith(".py") else rel.replace("/", ".")

    owner: Dict[str, str] = {}
    for rel, info_ in drafts.items():
        for name in info_["bound"]:
            owner.setdefault(name, modname(rel))

    warnings: List[str] = []
    outputs: Dict[str, str] = {}
    exported: Dict[str, List[str]] = {}
    needs: Dict[str, Dict[str, List[str]]] = {}

    header_names = src.header_names()
    for rel, info_ in drafts.items():
        mod = modname(rel)
        head = _docstring(info_["title"]) + src.header_text() + "\n"
        probe = head + info_["body"]
        _bound, refs = module_scope_report(probe, rel)
        need: Dict[str, List[str]] = {}
        for name in refs:
            target = owner.get(name)
            if target is None or target == mod:
                if target is None:
                    warnings.append(f"{mod}: 未解析的外部名字 {name}")
                continue
            need.setdefault(target, []).append(name)
        needs[rel] = need
        extra = "".join(_import_line(t, sorted(v)) for t, v in sorted(need.items()))
        text = probe if not extra else (
            _docstring(info_["title"]) + src.header_text() + "\n" + extra + "\n" + info_["body"]
        )
        outputs[rel] = text
        # 只导出本片**定义**的名字：头部 import 进来的类型/模块不算（否则门面会重复导出）
        exported[rel] = sorted(n for n in info_["bound"] if n not in header_names)

    cycle = _find_cycle({rel: set(modname(t) for t in need) for rel, need in needs.items()}, modname)
    if cycle:
        names = " -> ".join(cycle + [cycle[0]])
        raise SystemExit(
            f"[{spec_name}] 拆分片之间存在循环依赖：{names}\n"
            "  处理方式：在 SPECS 中把成环的相邻片合并为一个片（区间取并集）。"
        )

    # 门面 / 聚合器
    facade_imports = []
    for rel in drafts:
        names = [n for n in exported[rel] if n != "router"]
        if names:
            facade_imports.append(_import_line(modname(rel), names))
    facade = _docstring(spec["summary"]) + "".join(facade_imports)
    if src.all_node is not None:
        facade += "\n" + src.segment(src.all_node).rstrip() + "\n"

    if spec["mode"] == "router":
        agg = _docstring(spec["summary"])
        agg += "from fastapi import APIRouter\n\n"
        agg += "".join(
            f"from {modname(rel)} import router as _r{i}\n" for i, rel in enumerate(drafts)
        )
        agg += "\nrouter = APIRouter()\n"
        agg += "".join(f"router.include_router(_r{i})\n" for i in range(len(drafts)))
        # 保留旧的直接导入（handler 名）可用
        reexport = []
        for rel in drafts:
            names = [n for n in exported[rel] if n != "router"]
            if names:
                reexport.append(_import_line(modname(rel), names))
        if reexport:
            agg += "\n" + "".join(reexport)
        agg += "\n__all__ = [\"router\"]\n"
        outputs[spec["path"]] = agg
    else:
        outputs[spec["path"]] = facade

    if verbose:
        print(f"== {spec_name} -> {spec['path']} ({info['total']} 个顶层节点)")
        for rel, info_ in drafts.items():
            lines = outputs[rel].count("\n")
            print(f"   {rel:48s} {lines:5d} 行  {len(info_['bound']):3d} 符号  {info_['title']}")
        print(f"   {spec['path']:48s} {outputs[spec['path']].count(chr(10)):5d} 行（门面）")
        for line in warnings:
            print("   ! " + line)
    return outputs


def apply(spec_name: str, verbose: bool = False) -> List[str]:
    outputs = build(spec_name, verbose=verbose)
    written = []
    for rel, text in outputs.items():
        target = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        written.append(rel)
    return written


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="按符号块拆分超大模块")
    ap.add_argument("spec", nargs="?", help="拆分计划名，留空配合 --list")
    ap.add_argument("--list", action="store_true", help="列出已登记计划")
    ap.add_argument("--apply", action="store_true", help="落盘（默认 dry-run）")
    ap.add_argument("--all", action="store_true", help="依次执行全部计划")
    args = ap.parse_args(argv)

    if args.list:
        for name, spec in SPECS.items():
            print(f"{name:26s} {spec['path']:38s} {len(spec['parts'])} 片  [{spec['mode']}]")
        return 0

    targets = list(SPECS) if args.all else ([args.spec] if args.spec else [])
    if not targets:
        ap.print_help()
        return 2
    for name in targets:
        if name not in SPECS:
            print(f"未知计划：{name}", file=sys.stderr)
            return 2
        if args.apply:
            files = apply(name, verbose=True)
            print(f"   写入 {len(files)} 个文件")
        else:
            build(name, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
