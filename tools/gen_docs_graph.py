#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成「文档关系图谱」数据：``static/docs-graph.json``（纯 stdlib，可重复运行）。

消费者
------
``static/project-docs.html`` + ``static/js/docs-graph.js``（ECharts 力导向图）。

设计原则：**只输出有实据的连线，绝不编造关联**
    · ``link``      —— 文档正文里**显式**的 markdown 链接（``line`` 字段可回溯源文件行号）
    · ``data_link`` —— ``static/js/project-docs-data.js`` 里 ``groups[]`` 的**登记分组**
不根据「正文出现同一个词」推断关联（会造出假连线）。

节点范围
--------
1. ``docs/**/*.md``（全部）
2. 项目根 ``README.md`` / ``CHANGELOG.md``（入口节点，``kind=entry``）
3. ``project-docs-data.js`` 中登记了 ``path:`` 的条目
   —— 补齐「文档站已登记」的节点；文件不存在时 ``missing: true``（不编造 bytes/lines）

``kind`` 判定规则（优先级从上到下，先命中先返回）
-------------------------------------------------
1. ``docs/90-archive/`` 下 → ``archive``（过期/已废弃，仅考古）
2. 根目录 ``README.md`` / ``CHANGELOG.md`` → ``entry``（全站入口节点）
3. 文件名为 ``README.md``（非根）→ ``readme``（目录导读 / 文档地图）
4. 文件名含 ``prd`` → ``prd``（需求文档）
5. 文件名含 ``checklist`` → ``spec``（清单 / 规格，不是架构蓝图）
6. 目录 ``10-architecture`` → ``design``（架构 / 设计）
7. 目录 ``30-data`` → ``data``（数据口径）
8. 目录 ``40-ops`` → ``ops``（部署与运维）
9. 其余（含 ``00-strategy``）→ ``spec``（战略 / 规划规范）

``data_link`` 的推导（超边折叠）
-------------------------------
``project-docs-data.js`` 用 ``groups[].items[]`` 明确把若干文档登记在同一分组，
这是**显式事实**（不是内容推断）。为把「一组」折成两两连线，取组内**定义顺序第一**
的已存在节点作锚点，与其余成员各连一条；边上的 ``group`` 字段原样保留 data.js 的
分组名，前端可直接展示归属。

用法
----
    python tools/gen_docs_graph.py                  # 生成/刷新
    python tools/gen_docs_graph.py --quiet          # 只打印一行摘要
    python tools/gen_docs_graph.py --out other.json # 自定义输出
    python tools/gen_docs_graph.py --force          # 兼容旧 CLI，等同默认行为
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ----------------------------------------------------------------------------
# 路径常量
# ----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
DATA_JS = ROOT / "static" / "js" / "project-docs-data.js"
DEFAULT_OUT = ROOT / "static" / "docs-graph.json"

# 以脚本方式运行（python tools/gen_docs_graph.py）时 sys.path[0] 是 tools/，
# 而不是项目根，于是 `import tools.graph_layout` 会 ModuleNotFoundError。
# 这里显式把项目根补进搜索路径，让「脚本方式」与「模块方式」两种调用都能导入。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ROOT_MD_FILES = ("README.md", "CHANGELOG.md")
SUMMARY_MAX_CHARS = 120

#: 分组顺序：root 在前（入口），其余按信息架构目录号天然有序
GROUP_ORDER = ("root", "docs", "00-strategy", "10-architecture", "20-prd",
               "30-data", "40-ops", "90-archive", "unknown")

#: 分组中文名（与 static/js/docs-graph.js 的 GROUP_LABEL_FALLBACK 保持一致）
GROUP_LABELS = {
    "root": "根目录",
    "docs": "docs 根",
    "00-strategy": "战略与计划",
    "10-architecture": "架构与设计",
    "20-prd": "产品需求",
    "30-data": "数据字典",
    "40-ops": "运维与交付",
    "90-archive": "归档",
    "unknown": "未知",
}


# ----------------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------------
def _rel(path: Path) -> str:
    """绝对路径 → 仓库相对**正斜杠**路径（跨平台，前端直接当 id）。"""
    return path.relative_to(ROOT).as_posix()


def _group_of(rel_path: str) -> str:
    """一级目录即分组；根目录 md 归 ``root``，``docs`` 直下文件归 ``docs``。"""
    parts = rel_path.split("/")
    if len(parts) == 1:
        return "root"
    if parts[0] != "docs":
        return parts[0]
    if len(parts) == 2:
        return "docs"
    return parts[1]


def _kind_of(rel_path: str, group: str) -> str:
    """文档性质（规则见模块 docstring，顺序敏感）。"""
    name = rel_path.rsplit("/", 1)[-1].lower()
    if group == "90-archive":
        return "archive"
    if rel_path in ROOT_MD_FILES:
        return "entry"
    if name == "readme.md":
        return "readme"
    if "prd" in name:
        return "prd"
    if "checklist" in name:
        return "spec"
    if group == "10-architecture":
        return "design"
    if group == "30-data":
        return "data"
    if group == "40-ops":
        return "ops"
    return "spec"


_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MD_MARKS_RE = re.compile(r"[*_`>#|]+")


def _read_text(path: Path) -> str:
    """读文本：utf-8(BOM) → utf-8 → gbk → latin-1 兜底（不因编码失败丢节点）。"""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _extract_title(text: str, fallback: str) -> str:
    """正文第一个 ``# `` 一级标题；取不到用文件名（去扩展名）。"""
    m = _HEADING_RE.search(text)
    if not m:
        return fallback
    title = _INLINE_LINK_RE.sub(r"\1", m.group(1))
    title = _MD_MARKS_RE.sub("", title).strip()
    return title or fallback


def _extract_summary(text: str) -> str:
    """首个非空段落（跳过标题/代码块/分隔线/表格行/引用块），去 md 标记后截 120 字。"""
    cleaned = _FENCE_RE.sub("\n", text)
    paragraphs: List[List[str]] = []
    buf: List[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            if buf:
                paragraphs.append(buf)
                buf = []
            continue
        if line.startswith(("#", "---", "|", ">", "```")):
            if buf:
                paragraphs.append(buf)
                buf = []
            continue
        buf.append(line)
    if buf:
        paragraphs.append(buf)

    for para in paragraphs:
        joined = " ".join(para)
        joined = _INLINE_LINK_RE.sub(r"\1", joined)
        joined = _HTML_TAG_RE.sub("", joined)
        joined = _MD_MARKS_RE.sub(" ", joined)
        joined = re.sub(r"\s+", " ", joined).strip()
        if len(joined) >= 8:  # 过短的行（页码、单词）不当摘要
            return joined[:SUMMARY_MAX_CHARS]
    return ""


# ----------------------------------------------------------------------------
# 扫描：磁盘 md
# ----------------------------------------------------------------------------
def scan_md_files() -> List[str]:
    """``docs/**/*.md`` + 项目根入口 md → 仓库相对正斜杠路径（去重排序）。"""
    found: List[str] = []
    if DOCS_DIR.is_dir():
        for p in DOCS_DIR.rglob("*.md"):
            if p.is_file():
                found.append(_rel(p))
    for name in ROOT_MD_FILES:
        if (ROOT / name).is_file():
            found.append(name)
    return sorted(set(found))


# ----------------------------------------------------------------------------
# 扫描：project-docs-data.js 的登记条目
# ----------------------------------------------------------------------------
_CHANGELOG_SOURCES_RE = re.compile(r"changelogSources\s*:\s*\{", re.MULTILINE)
_GROUP_BLOCK_RE = re.compile(
    r"\{\s*name:\s*'((?:[^'\\]|\\.)*)',\s*items:\s*\[(.*?)\]\s*\}",
    re.DOTALL,
)
_ITEM_RE = re.compile(r"\{[^{}]*?path:\s*'((?:[^'\\]|\\.)*)'[^{}]*?\}", re.DOTALL)
_ITEM_NAME_RE = re.compile(r"name:\s*'((?:[^'\\]|\\.)*)'")


def scan_registered() -> List[Dict[str, str]]:
    """解析 ``changelogSources.groups[].items[]`` 里登记的 ``path``。

    :return: ``[{'group': 分组名, 'path': 仓库相对路径, 'label': 条目显示名}]``
    """
    if not DATA_JS.is_file():
        return []
    text = _read_text(DATA_JS)

    m = _CHANGELOG_SOURCES_RE.search(text)
    if not m:
        return []
    body = text[m.end():]
    end = body.find("global.DAPENG_DOCS")
    if end > 0:
        body = body[:end]

    items: List[Dict[str, str]] = []
    for gm in _GROUP_BLOCK_RE.finditer(body):
        group_name = gm.group(1).strip()
        block = gm.group(2)
        for im in _ITEM_RE.finditer(block):
            path = im.group(1).strip().replace("\\", "/")
            if not path:
                continue
            name_m = _ITEM_NAME_RE.search(im.group(0))
            label = name_m.group(1).strip() if name_m else path.rsplit("/", 1)[-1]
            items.append({"group": group_name, "path": path, "label": label})
    return items


# ----------------------------------------------------------------------------
# 节点
# ----------------------------------------------------------------------------
def build_node(rel_path: str, registered: bool, label: Optional[str]) -> Dict[str, Any]:
    """构造节点；``missing`` 由文件是否存在决定，缺失时绝不编造大小/行数。"""
    abs_path = ROOT / rel_path
    group = _group_of(rel_path)
    name = rel_path.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    base = {
        "id": rel_path,
        "name": name,
        "group": group,
        "group_label": GROUP_LABELS.get(group, group),
        "kind": _kind_of(rel_path, group),
        "registered": registered,
        "label": label or stem,
    }

    if not abs_path.is_file():
        base.update({
            "title": stem,
            "bytes": 0,
            "lines": 0,
            "updated": None,
            "missing": True,
            "summary": "",
        })
        return base

    text = _read_text(abs_path)
    stat = abs_path.stat()
    base.update({
        "title": _extract_title(text, stem),
        "bytes": stat.st_size,
        "lines": (text.count("\n") + (0 if text.endswith("\n") else 1)) if text else 0,
        "updated": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d"),
        "missing": False,
        "summary": _extract_summary(text),
    })
    return base


# ----------------------------------------------------------------------------
# 连线
# ----------------------------------------------------------------------------
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
_SKIP_PREFIX = ("http://", "https://", "mailto:", "#", "tel:", "javascript:", "data:")


def _resolve_target(source_id: str, target: str) -> Optional[str]:
    """markdown 链接目标 → 仓库相对正斜杠路径；外链/越界/未解析返回 None。"""
    raw = target.strip()
    if not raw or raw.lower().startswith(_SKIP_PREFIX):
        return None
    raw = raw.split("#", 1)[0].split("?", 1)[0]
    if not raw:
        return None
    candidate = (ROOT / raw.lstrip("/")) if raw.startswith("/") \
        else ((ROOT / source_id).parent / raw)
    try:
        resolved = candidate.resolve()
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        return None


_FENCE_LINE_RE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")


def iter_md_link_candidates(source_id: str) -> Iterable[Tuple[int, "re.Match[str]"]]:
    """逐行产出 ``(行号, 正则匹配)``，已剔除「不该算链接」的内容：

    · 代码围栏（``` / ~~~）内的整行
    · 行内代码片段（`` `...` ``）里的伪链接
    · 外链（http/https/mailto/tel/data:）、纯锚点（``#``）—— 由 ``_resolve_target`` 过滤

    代码围栏与行内代码的统一处理，是为了让 ``link`` 边与 ``dangling`` 判定同源，
    不然「文档里贴了一段示例 markdown」会被当成真实链接。
    """
    abs_path = ROOT / source_id
    if not abs_path.is_file():
        return
    in_fence = False
    for line_no, line in enumerate(_read_text(abs_path).splitlines(), start=1):
        if _FENCE_LINE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        clean = _INLINE_CODE_RE.sub("", line)
        for m in _MD_LINK_RE.finditer(clean):
            yield line_no, m


def build_links(node_ids: Iterable[str]) -> List[Dict[str, Any]]:
    """逐篇扫描显式 markdown 链接，产出 ``link`` 边（带 ``line`` 行号）。"""
    ids = {i for i in node_ids}
    links: List[Dict[str, Any]] = []
    seen = set()
    for source_id in sorted(ids):
        if not source_id.lower().endswith(".md"):
            continue
        for line_no, m in iter_md_link_candidates(source_id):
            target_id = _resolve_target(source_id, m.group(2))
            if not target_id or target_id not in ids or target_id == source_id:
                continue
            key = (source_id, target_id)
            if key in seen:
                continue
            seen.add(key)
            links.append({
                "source": source_id,
                "target": target_id,
                "type": "link",
                "line": line_no,
                "group": None,
            })
    return links


def build_dangling(node_ids: Iterable[str]) -> List[Dict[str, Any]]:
    """收集**悬空引用**：markdown 链到的目标「既不是图谱节点、也不在磁盘上」。

    这是这份产物的健康信号——文档改名 / 目录重构之后，图谱会静默少一条边，
    没有这个数组就分不清「边消失了」和「边从来没存在过」。

    · 只收**仓库内**路径（外链、纯锚点、`#` 片段由 ``_resolve_target`` 过滤掉）
    · 自引用不计（``target == source``）
    · 按 ``(source, target, line)`` 去重
    · 已经在节点集里的目标不算悬空（即使它 ``missing=true``，那是「登记了但缺文件」，
      由 ``stats.missing`` 表达，两套语义不混）
    """
    ids = {i for i in node_ids}
    out: List[Dict[str, Any]] = []
    seen = set()
    for source_id in sorted(ids):
        if not source_id.lower().endswith(".md"):
            continue
        for line_no, m in iter_md_link_candidates(source_id):
            raw_target = m.group(2)
            target_id = _resolve_target(source_id, raw_target)
            if not target_id or target_id == source_id:
                continue
            if target_id in ids:
                continue
            if (ROOT / target_id).exists():
                continue  # 磁盘上有，只是没进图（如未登记的 .mermaid / 资源文件）
            key = (source_id, target_id, line_no)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "source": source_id,
                "line": line_no,
                "target": target_id,
                "raw": raw_target,
            })
    out.sort(key=lambda d: (d["source"], d["line"], d["target"]))
    return out


def build_data_links(
    node_ids: Iterable[str],
    groups: List[Tuple[str, List[str]]],
    existing: Optional[Iterable[Tuple[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """``data_link`` 边：data.js 把若干文档显式登记在同一 ``groups[]`` 分组。

    超边折叠：组内**定义顺序第一**的节点作锚点，与其余成员各连一条。
    ``existing`` 里已有的 (source, target) 对会被跳过（同一条关系不重复计两次，
    否则力导向图里会重复计算度数、边也会画两遍）。
    """
    ids = set(node_ids)
    skip = set(existing or ())
    links: List[Dict[str, Any]] = []
    seen = set()
    for group_name, members in groups:
        present = [m for m in members if m in ids]
        if len(present) < 2:
            continue
        anchor = present[0]
        for member in present[1:]:
            if member == anchor:
                continue
            key = (anchor, member)
            if key in seen or key in skip or (member, anchor) in skip:
                continue
            seen.add(key)
            links.append({
                "source": anchor,
                "target": member,
                "type": "data_link",
                "line": None,
                "group": group_name,
            })
    return links


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def generate(out_path: Path = DEFAULT_OUT,
             diagnostics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """扫描 → 组装 → 写 JSON，返回 payload（便于测试断言）。

    :param diagnostics: 传入 dict 时回填 ``isolated``（无边节点），仅用于命令行提示。
                        ``dangling``（悬空引用）是正式产物字段，见 payload 顶层。
    """
    nodes: Dict[str, Dict[str, Any]] = {
        rel: build_node(rel, False, None) for rel in scan_md_files()
    }

    # 登记分组：保持 data.js 的定义顺序（决定 data_link 的锚点）
    group_members: List[Tuple[str, List[str]]] = []
    group_index: Dict[str, int] = {}
    for item in scan_registered():
        rel_path = item["path"]
        if rel_path not in nodes:
            nodes[rel_path] = build_node(rel_path, True, item["label"])
        else:
            nodes[rel_path]["registered"] = True
            if item["label"]:
                nodes[rel_path]["label"] = item["label"]
        idx = group_index.get(item["group"])
        if idx is None:
            group_index[item["group"]] = len(group_members)
            group_members.append((item["group"], [rel_path]))
        elif rel_path not in group_members[idx][1]:
            group_members[idx][1].append(rel_path)

    node_list = sorted(
        nodes.values(),
        key=lambda n: (GROUP_ORDER.index(n["group"]) if n["group"] in GROUP_ORDER
                       else len(GROUP_ORDER), n["id"]),
    )

    links = build_links(nodes.keys())
    links.extend(build_data_links(nodes.keys(), group_members,
                                 existing={(e["source"], e["target"]) for e in links}))
    links.sort(key=lambda e: (e["type"], e["source"], e["target"]))

    # 悬空引用：链到「既不是节点、也不在磁盘」的目标 —— 独立的健康信号，
    # 刻意**不**做成 missing 节点（那会让不存在的文件进图、污染节点数与分组）
    dangling = build_dangling(nodes.keys())

    if diagnostics is not None:
        linked = {e["source"] for e in links} | {e["target"] for e in links}
        diagnostics["isolated"] = sorted(set(nodes.keys()) - linked)

    counts: Dict[str, int] = {}
    for n in node_list:
        counts[n["group"]] = counts.get(n["group"], 0) + 1
    ordered_counts = {
        g: counts[g] for g in sorted(
            counts,
            key=lambda x: (GROUP_ORDER.index(x) if x in GROUP_ORDER else len(GROUP_ORDER), x),
        )
    }

    payload: Dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "stats": {
            "nodes": len(node_list),
            "links": len(links),
            "missing": sum(1 for n in node_list if n["missing"]),
            "dangling": len(dangling),
            "groups": ordered_counts,
        },
        "groups": [{"key": g, "label": GROUP_LABELS.get(g, g)} for g in ordered_counts],
        "nodes": node_list,
        "links": links,
        # 悬空引用（additive）：markdown 链到、但目标既不在节点集也不在磁盘。
        # 空时必须是 []（不是 null），前端才好直接判。
        "dangling": dangling,
    }

    # ------------------------------------------------------------------
    # v5.5 图算法层：把「布局 / 中心性 / 社区发现」前置到 Python 计算。
    # 前端由此可以 layout:'none' 直接渲染，首屏不再跑 40 万次浮点运算，
    # 且坐标跨设备一致；同时获得 PageRank 尺寸、介数桥接标记、群落光环。
    # 失败时不阻断产物生成（图仍可渲染，只是退化为前端自算布局）。
    # ------------------------------------------------------------------
    try:
        from tools.graph_layout import enrich as _enrich_graph
        _enrich_graph(payload, GROUP_LABELS)
    except Exception as _exc:  # pragma: no cover - 仅在算法层异常时触发
        print(f"  [warn] 图算法层未生效（{type(_exc).__name__}: {_exc}），"
              f"前端将退回自算布局", file=sys.stderr)
        payload.setdefault("metrics", {})
        payload.setdefault("communities", [])
        payload.setdefault("layout", {})

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="生成项目文档关系图谱数据（static/docs-graph.json）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出路径（默认 static/docs-graph.json）")
    parser.add_argument("--quiet", action="store_true", help="只输出一行摘要")
    parser.add_argument("--force", action="store_true", help="兼容旧 CLI：默认即覆盖，此参数无副作用")
    args = parser.parse_args(argv)

    diag: Dict[str, Any] = {}
    payload = generate(Path(args.out), diag)
    stats = payload["stats"]

    if args.quiet:
        print(f"nodes={stats['nodes']} links={stats['links']} "
              f"missing={stats['missing']} dangling={stats['dangling']}")
        return 0

    print("=" * 56)
    print("  文档关系图谱数据已生成")
    print("=" * 56)
    print(f"  输出：{args.out}")
    print(f"  节点：{stats['nodes']}   连线：{stats['links']}   "
          f"缺失：{stats['missing']}   悬空引用：{stats['dangling']}")
    print("  分组：")
    for g, c in stats["groups"].items():
        print(f"    {g:<18} {c}")
    by_type: Dict[str, int] = {}
    for e in payload["links"]:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    print("  连线类型：" + "，".join(f"{k}={v}" for k, v in sorted(by_type.items())))
    missing = [n["id"] for n in payload["nodes"] if n["missing"]]
    print("  缺失节点：" + ("无" if not missing else ""))
    for m in missing:
        print(f"    - {m}")
    iso = diag.get("isolated") or []
    print(f"  孤立节点：{len(iso)}" + ("（图里会以悬浮样式区分）" if iso else ""))
    for i in iso:
        print(f"    - {i}")
    dang = payload.get("dangling") or []
    if dang:
        print(f"  悬空引用（链到既非节点、也不存在的文件）：{len(dang)}")
        for d in dang[:15]:
            print(f"    - {d['source']}:{d['line']}  原文 {d['raw']} → {d['target']}")
        if len(dang) > 15:
            print(f"    … 另有 {len(dang) - 15} 条")
    else:
        print("  悬空引用：无")
    return 0


if __name__ == "__main__":
    sys.exit(main())
