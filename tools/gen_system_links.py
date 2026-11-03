#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""系统 ↔ 设计 双向索引生成器

## 解决什么问题

文档图谱只描述「文档之间的引用」，读者看完一篇架构文档后，无法从图上知道
「这篇写的是哪个模块、对应哪些接口」，也无法从模块反查「哪些文档在讲我」。
设计与实现因此是两套孤立的信息。

本脚本把两侧真实数据连起来，产出 `static/system-links.json`：

```
文档 --(正文里提到的 server/xxx.py)--> 模块 --(路由注册/端点定义)--> API 端点
```

## 只连真实存在的东西（铁律）

- 文档里提到的模块路径，**必须在磁盘上存在**，否则记为 `missing` 并在输出中带上，
  UI 可以红字提示「文档引用了不存在的模块」——这本身就是有价值的一致性检查。
- 文档里提到的 `/api/...` 路径，**必须能在 OpenAPI 里找到**（连不上服务时降级为
  只做字符串采集，并标 `endpoint_source=docs_only`，绝不假装校验过）。
- 模块的导入关系用 AST 解析，不猜。

## 用法

    python tools/gen_system_links.py            # 连得上的服务就校验端点
    python tools/gen_system_links.py --no-api   # 离线：不校验端点存在性
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import urllib.request
from datetime import datetime
from typing import Dict, List, Set

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
SERVER_DIR = os.path.join(ROOT, "server")
GRAPH_PATH = os.path.join(ROOT, "static", "docs-graph.json")
OUT_PATH = os.path.join(ROOT, "static", "system-links.json")

MODULE_RE = re.compile(r"server/([a-z0-9_]+)\.py")
# 端点：/api/xxx，允许路径参数占位；排除明显的占位写法
ENDPOINT_RE = re.compile(r"(/api/[a-z0-9][a-z0-9/_-]*)")


def list_doc_files() -> List[str]:
    out = []
    for dirpath, _dirnames, filenames in os.walk(DOCS_DIR):
        for fn in filenames:
            if fn.endswith(".md"):
                out.append(os.path.join(dirpath, fn))
    # 根目录两篇也算（README / CHANGELOG）
    for fn in ("README.md", "CHANGELOG.md"):
        p = os.path.join(ROOT, fn)
        if os.path.exists(p):
            out.append(p)
    return sorted(out)


def doc_id(path: str) -> str:
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    return rel


def scan_server_modules() -> Dict[str, Dict]:
    """扫描 server/*.py：行数、导入的其他模块、自身路由前缀（尽力）。"""
    mods: Dict[str, Dict] = {}
    if not os.path.isdir(SERVER_DIR):
        return mods
    for fn in sorted(os.listdir(SERVER_DIR)):
        if not fn.endswith(".py") or fn == "__init__.py":
            continue
        name = fn[:-3]
        path = os.path.join(SERVER_DIR, fn)
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        entry = {"file": rel, "lines": 0, "imports": [], "endpoints": []}
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
            entry["lines"] = src.count("\n") + 1
            tree = ast.parse(src)
            imports: Set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("server"):
                    imports.add(node.module.split(".")[-1])
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name.startswith("server"):
                            imports.add(a.name.split(".")[-1])
            entry["imports"] = sorted(x for x in imports if x != name)
        except (SyntaxError, OSError):
            pass
        mods[name] = entry
    return mods


def scan_routers() -> Dict[str, List[str]]:
    """从 server/routers/*.py 抓取端点路径（静态装饰器），并记录所属文件。"""
    out: Dict[str, List[str]] = {}
    rdir = os.path.join(SERVER_DIR, "routers")
    if not os.path.isdir(rdir):
        return out
    for fn in sorted(os.listdir(rdir)):
        if not fn.endswith(".py") or fn == "__init__.py":
            continue
        path = os.path.join(rdir, fn)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
        except OSError:
            continue
        paths = set()
        for m in re.finditer(r'@router\.(get|post|put|delete)\(\s*["\']([^"\']+)["\']', src):
            p = m.group(2)
            if not p.startswith("/"):
                p = "/" + p
            paths.add(p)
        out["server/routers/" + fn] = sorted(paths)
    return out


def openapi_paths(base: str = "http://127.0.0.1:8900") -> Set[str]:
    """从运行中的服务取真实端点集合；失败返回空集合（调用方据此降级）。"""
    try:
        with urllib.request.urlopen(base + "/openapi.json", timeout=8) as r:
            doc = json.loads(r.read().decode("utf-8"))
        return set((doc.get("paths") or {}).keys())
    except Exception:  # noqa: BLE001
        return set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--no-api", action="store_true", help="不连服务，跳过端点存在性校验")
    args = ap.parse_args()

    graph = {}
    if os.path.exists(GRAPH_PATH):
        with open(GRAPH_PATH, "r", encoding="utf-8") as f:
            graph = json.load(f)
    graph_ids = [str(n.get("id")) for n in (graph.get("nodes") or [])]

    modules = scan_server_modules()
    routers = scan_routers()
    real_endpoints = set() if args.no_api else openapi_paths()
    endpoint_source = "openapi" if real_endpoints else "docs_only"

    existing_files = set()
    for _name, meta in modules.items():
        existing_files.add(meta["file"])
    # __init__.py 被扫描跳过，但它确实存在，不能算缺失
    if os.path.exists(os.path.join(SERVER_DIR, "__init__.py")):
        existing_files.add("server/__init__.py")

    # 文档 → 模块 / 端点
    doc_links: Dict[str, Dict] = {}
    module_docs: Dict[str, List[str]] = {}
    endpoint_docs: Dict[str, List[str]] = {}
    missing_modules: Dict[str, List[str]] = {}

    for path in list_doc_files():
        did = doc_id(path)
        if graph_ids and did not in graph_ids:
            # 只统计图谱里存在的文档，避免产生"孤儿"引用
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        mods: Dict[str, int] = {}
        planned: Set[str] = set()
        for m in MODULE_RE.finditer(text):
            mods[m.group(1)] = mods.get(m.group(1), 0) + 1
            # 上下文里出现「新增/新建/规划/待实现」→ 这是计划中的模块，不是断链。
            # 不区分的话，会把 PRD 里的规划项误报成"文档引用了不存在的模块"。
            ctx = text[max(0, m.start() - 60): m.end() + 60]
            if any(k in ctx for k in ("新增", "新建", "规划", "待实现", "TODO", "计划中")):
                planned.add(m.group(1))
        eps: Dict[str, int] = {}
        for m in ENDPOINT_RE.finditer(text):
            p = m.group(1).rstrip("/")
            if len(p) > 5:
                eps[p] = eps.get(p, 0) + 1

        missing = sorted(n for n in mods
                         if ("server/%s.py" % n) not in existing_files and n not in planned)
        if missing:
            missing_modules[did] = missing

        doc_links[did] = {
            "modules": [{"name": k, "mentions": v,
                         "exists": k in modules,
                         "planned": (k not in modules) and (k in planned)}
                        for k, v in sorted(mods.items(), key=lambda kv: -kv[1])],
            "endpoints": [{"path": k, "mentions": v,
                           "exists": (k in real_endpoints) if real_endpoints else None}
                          for k, v in sorted(eps.items(), key=lambda kv: -kv[1])],
        }
        for n in mods:
            module_docs.setdefault(n, []).append(did)
        for p in eps:
            endpoint_docs.setdefault(p, []).append(did)

    # 模块 → 由哪些 router 暴露端点（按 router 文件名与模块名的弱匹配 + 显式 import）
    for rel, paths in routers.items():
        fname = os.path.basename(rel)[:-3]
        target = modules.get(fname)
        if not target:
            continue
        target["endpoints"] = paths
        for p in paths:
            endpoint_docs.setdefault(p, []).append(target["file"])

    out = {
        "version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "endpoint_source": endpoint_source,
        "stats": {
            "modules": len(modules),
            "docs_linked": len(doc_links),
            "modules_referenced": len(module_docs),
            "endpoints_referenced": len(endpoint_docs),
            "docs_with_missing_module": len(missing_modules),
            "planned_modules": len({m["name"] for d in doc_links.values() for m in d["modules"] if m.get("planned")}),
            "real_endpoints": len(real_endpoints),
        },
        "modules": {
            k: {
                "file": v["file"],
                "lines": v["lines"],
                "imports": v["imports"],
                "endpoints": v["endpoints"],
                "docs": sorted(set(module_docs.get(k, []))),
            } for k, v in modules.items()
        },
        "doc_links": doc_links,
        "missing_modules": missing_modules,
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, args.out)

    s = out["stats"]
    print("系统↔设计 索引已生成：%s" % os.path.relpath(args.out, ROOT))
    print("  模块 %d / 被文档引用的模块 %d / 被引用的端点 %d"
          % (s["modules"], s["modules_referenced"], s["endpoints_referenced"]))
    print("  已链接文档 %d 篇；端点校验来源：%s（%d 个真实端点）"
          % (s["docs_linked"], out["endpoint_source"], s["real_endpoints"]))
    if missing_modules:
        print("  ⚠ 有 %d 篇文档引用了不存在的模块：" % len(missing_modules))
        for did, mods in list(missing_modules.items())[:5]:
            print("     %s → %s" % (did, ", ".join(mods)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
