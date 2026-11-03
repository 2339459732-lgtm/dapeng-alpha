#!/usr/bin/env python3
"""架构测绘与守卫（arch-report.v1）。

用途：
1. 生成服务端模块清单（行数 / 函数数 / 路由数 / 依赖）；
2. 检测 import 环、跨层反向依赖、超大模块、重复函数名、裸 except；
3. 输出 JSON 与可读文本，供架构评审与 CI 守卫使用。

用法：
    python tools/arch_report.py            # 人类可读摘要
    python tools/arch_report.py --json     # 机器可读
    python tools/arch_report.py --check    # 违规时退出码 1（可挂 CI）
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server")

# 分层约定：数字越小越底层；上层可依赖下层，反向依赖视为违规。
LAYERS = {
    "infra": 0,        # cache / health / 日志等基础设施
    "providers": 0,    # 外部数据供应商适配
    "core": 1,         # 纯计算、模型、配置
    "data": 2,         # 采集与派生数据（coverage / scanner / snapshot）
    "engine": 3,       # 研判引擎（experts / survey / brain）
    "api": 4,          # 路由层
}

# 模块 → 层（按前缀/文件名归类，未列出的归 core）
LAYER_OF = {
    "cache": "infra", "data_bus": "infra", "config": "infra", "llm": "infra",
    "market_coverage": "data", "market_scanner": "data", "decision_snapshot": "data",
    "global_assets": "data", "global_linkage": "data", "sector_map": "data",
    "sector_graph": "data", "sector_linkage": "data", "sector_resolve": "data",
    "ak_market": "data", "eastmoney": "data", "fundamental": "data", "realtime": "data",
    "decision_experts": "engine", "market_survey_engine": "engine", "brain": "engine",
    "brain_capabilities": "engine", "sentiment_tracker": "data",
    "layer_crosscheck": "engine", "brain_config": "infra",
    "ict_model": "engine", "ict_history": "engine", "quant_core": "engine",
    "qlib_factor_engine": "engine", "risk_metrics": "engine", "chan_engine": "engine",
    "etf_rotation": "engine", "datasources": "data", "quote_hub": "data",
    # 决策指标与分流：聚合 data 层产出、供 engine 层消费 → 与 engine 同层
    "decision_metrics": "engine", "decision_routing": "engine",
    # 龙虎榜复盘：盘后离线归因，只读上游 + 落盘，不进入实时决策链路 → 与 engine 同层
    "lhb_review": "engine", "lhb_fetch": "engine", "lhb_insight": "engine",
    "lhb_factors": "engine",
    "strategy_profile": "engine",
    "strategy_review": "engine",
    "decision_pipeline": "engine",
    "main": "api", "routers": "api", "payment": "api", "agents": "engine",
    "trading": "engine", "strategies": "engine", "infra": "infra", "providers": "providers",
    "stable_market_provider": "data", "api_tracking": "infra", "quote_hub": "data",
    # 模块拆分后的子片（2026-11-02 P2）：与母模块同层
    "stable_cache": "data", "stable_em": "data",
    "stable_sector": "data", "stable_provider_impl": "data",
    "qlib_factor_lib": "engine", "qlib_factor_stats": "engine",
    "qlib_factor_engine_impl": "engine",
    # 归类修正（2026-11-02）：以下模块职责与层不匹配，按实际依赖方向归类
    "services": "data",          # 领域服务层：供路由与下层共用
    "stock_pool": "data",        # 股票池是数据装配，不是纯计算
    "ict_history": "core",       # 纯数学回测统计（无 IO），与 ict_model 同级
}

# 已识别但尚未修复的反向依赖：必须登记原因与计划阶段，--check 不计为失败。
# 当前为空：2026-11-02 已完成全部依赖治理。新增豁免必须同时写修复计划。
KNOWN_VIOLATIONS: Dict[Tuple[str, str], str] = {}

LARGE_MODULE_LINES = 900


def layer_of(module: str) -> str:
    """把 server.x.y 归一成包名 x 再查层；未知包按 core 处理。

    ``x_yyy`` 形式的拆分片默认继承母模块 ``x`` 的层，避免拆分即降层。
    """
    name = module
    if name.startswith("server."):
        name = name[len("server."):]
    head = name.split(".")[0]
    if head in LAYER_OF:
        return LAYER_OF[head]
    parents = [key for key in LAYER_OF if head.startswith(key + "_")]
    if parents:
        return LAYER_OF[max(parents, key=len)]
    return "core"


def iter_py_files() -> List[str]:
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(SERVER):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                files.append(os.path.join(dirpath, name))
    return sorted(files)


def module_name(path: str) -> str:
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    if rel.endswith("/__init__.py"):
        rel = rel[: -len("/__init__.py")]
    elif rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace("/", ".")


def analyze() -> Dict[str, Any]:
    modules: Dict[str, Dict[str, Any]] = {}
    imports: Dict[str, Set[str]] = defaultdict(set)
    all_functions: Dict[str, List[str]] = defaultdict(list)
    findings: Dict[str, List[Dict[str, Any]]] = {
        "cycles": [], "layer_violations": [], "large_modules": [],
        "duplicate_functions": [], "bare_except": [], "todo": [], "known_violations": [],
    }

    for path in iter_py_files():
        mod = module_name(path)
        try:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            tree = ast.parse(source)
        except (OSError, SyntaxError) as exc:
            findings["bare_except"].append({"module": mod, "line": 0, "note": f"解析失败：{exc}"})
            continue

        lines = source.count("\n") + 1
        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        routes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and any(isinstance(d, ast.Call) and getattr(getattr(d, "func", None), "attr", "") in
                          ("get", "post", "put", "delete", "patch") for d in n.decorator_list)]
        for fn in funcs:
            all_functions[fn.name].append(mod)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("server"):
                    imports[mod].add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("server"):
                        imports[mod].add(alias.name)
            elif isinstance(node, ast.ExceptHandler) and node.type is None:
                findings["bare_except"].append({"module": mod, "line": node.lineno})
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.strip()
                if text.startswith("TODO") or text.startswith("FIXME") or text.startswith("XXX"):
                    findings["todo"].append({"module": mod, "line": node.lineno, "text": text[:80]})

        modules[mod] = {
            "path": os.path.relpath(path, ROOT).replace(os.sep, "/"),
            "lines": lines, "functions": len(funcs), "routes": len(routes),
            "layer": layer_of(mod),
        }
        if lines > LARGE_MODULE_LINES:
            findings["large_modules"].append({"module": mod, "lines": lines})

    # 依赖环（只统计 server 内部依赖）
    def dfs(node: str, stack: List[str], seen: Set[str]) -> None:
        for dep in imports.get(node, ()):
            if dep in stack:
                cycle = stack[stack.index(dep):] + [dep]
                key = "→".join(sorted(set(cycle)))
                if key not in seen:
                    seen.add(key)
                    findings["cycles"].append({"cycle": cycle})
                continue
            if dep in stack or dep not in modules:
                continue
            dfs(dep, stack + [dep], seen)

    seen_cycles: Set[str] = set()
    for mod in modules:
        dfs(mod, [mod], seen_cycles)

    # 跨层反向依赖
    for mod, deps in imports.items():
        upper = LAYERS.get(layer_of(mod), 1)
        for dep in deps:
            dep_layer = LAYERS.get(layer_of(dep.replace("server.", "")), 1)
            if dep_layer > upper:
                entry = {
                    "module": mod, "layer": layer_of(mod),
                    "depends_on": dep, "dep_layer": layer_of(dep.replace("server.", "")),
                }
                reason = KNOWN_VIOLATIONS.get((mod, dep))
                if reason:
                    entry["known"] = True
                    entry["plan"] = reason
                    findings["known_violations"].append(entry)
                else:
                    findings["layer_violations"].append(entry)

    # 同名函数（>=3 处，且非 __init__/测试）
    for name, mods in all_functions.items():
        if name.startswith("__") or len(mods) < 3:
            continue
        uniq = sorted(set(mods))
        if len(uniq) >= 3 and name not in ("get", "post", "main", "run", "load", "save"):
            findings["duplicate_functions"].append({"name": name, "count": len(uniq), "modules": uniq[:6]})

    findings["duplicate_functions"].sort(key=lambda x: -x["count"])
    findings["layer_violations"].sort(key=lambda x: (x["module"], x["depends_on"]))

    total_lines = sum(m["lines"] for m in modules.values())
    total_routes = sum(m["routes"] for m in modules.values())
    return {
        "version": "arch-report.v1",
        "root": ROOT,
        "summary": {
            "modules": len(modules),
            "lines": total_lines,
            "functions": sum(m["functions"] for m in modules.values()),
            "routes": total_routes,
            "by_layer": dict(Counter(m["layer"] for m in modules.values())),
        },
        "modules": modules,
        "imports": {k: sorted(v) for k, v in sorted(imports.items())},
        "findings": findings,
    }


def render(report: Dict[str, Any]) -> str:
    s = report["summary"]
    f = report["findings"]
    lines = [
        "架构测绘报告",
        "=" * 52,
        f"模块 {s['modules']} 个 | 代码 {s['lines']} 行 | 函数 {s['functions']} 个 | 路由 {s['routes']} 个",
        f"分层分布：{s['by_layer']}",
        "",
        f"依赖环：{len(f['cycles'])}",
        *[f"  - {' → '.join(c['cycle'])}" for c in f["cycles"][:8]],
        "",
        f"跨层反向依赖：{len(f['layer_violations'])}（另有 {len(f.get('known_violations', []))} 条已登记待修）",
        *[f"  - {v['module']}({v['layer']}) → {v['depends_on']}({v['dep_layer']})"
          for v in f["layer_violations"][:12]],
        *[f"  · 待修：{v['module']} → {v['depends_on']}（{v['plan']}）"
          for v in f.get("known_violations", [])],
        "",
        f"超大模块（>{LARGE_MODULE_LINES} 行）：{len(f['large_modules'])}",
        *[f"  - {m['module']} ({m['lines']} 行)" for m in f["large_modules"]],
        "",
        f"同名函数（≥3 模块）：{len(f['duplicate_functions'])}",
        *[f"  - {d['name']} × {d['count']}（{', '.join(d['modules'][:3])}…）"
          for d in f["duplicate_functions"][:10]],
        "",
        f"裸 except：{len(f['bare_except'])}",
        *[f"  - {b['module']}:{b['line']}" for b in f["bare_except"][:10]],
        "",
        f"TODO/FIXME：{len(f['todo'])}",
        *[f"  - {t['module']}:{t['line']} {t['text']}" for t in f["todo"][:8]],
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--check", action="store_true", help="存在违规时返回非 0")
    args = parser.parse_args()

    report = analyze()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render(report))

    if args.check:
        f = report["findings"]
        bad = len(f["cycles"]) + len(f["layer_violations"]) + len(f["bare_except"])
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
