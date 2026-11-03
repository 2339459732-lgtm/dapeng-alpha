#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""decision-lineage.v1 —— 结论到上游依据的可解析链路。

为什么需要它
------------
「结论可追溯」如果只做到「每层都有个 trace_id」，那是**串了号**，不是可追溯：
拿到一个仓位结论，仍然回答不了「它依据的是哪一版事实、哪几个字段、什么时候的数据、
中途有没有降级」。本模块把这条链做成可查询、可校验、可回放的结构：

    coverage → packet → sentiment → sentiment_flow → survey → experts → crosscheck → memo

每个节点给出四件事：
1. `version`  数据契约版本（能判断口径有没有换过）；
2. `as_of`    数据时点（能判断是不是同一条时间轴）；
3. `digest`   关键字段的指纹（能判断依据有没有被悄悄改过）；
4. `facts`    用于下游结论的那几个字段值（能直接读出「依据是什么」）。

`verify()` 再做三项体检：节点是否缺失、`as_of` 是否逆序、`trace_id` 是否串号。
结论（memo）自带 `citations`，即使溯源索引被清空，也能从结论本身反查依据版本与指纹。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

LINEAGE_VERSION = "lineage.v1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RING_PATH = os.path.join(ROOT, "data", "lineage.json")

#: 链路顺序（从最上游到最终结论）；缺任何一个都会记入 gaps
CHAIN: List[str] = [
    "coverage", "packet", "sentiment", "sentiment_flow",
    "survey", "experts", "crosscheck", "memo",
]

#: 每条边传输的契约名 —— 与 docs/10-architecture/LAYER-SYNC-CONTRACT.md 一一对应
EDGES: List[Dict[str, str]] = [
    {"from": "coverage", "to": "packet", "contract": "market-coverage.v1 → decision-snapshot.v1",
     "fields": "quotes(price/chg/amount/turnover) · breadth · fundamentals"},
    {"from": "packet", "to": "sentiment", "contract": "decision-snapshot.v1 → sentiment.v1",
     "fields": "breadth.up_ratio/limit_up · indices.chg · turnover.amount"},
    {"from": "sentiment", "to": "sentiment_flow", "contract": "sentiment.v1 → sentiment-flow.v1",
     "fields": "score · dimensions[6] · coverage"},
    {"from": "packet", "to": "survey", "contract": "decision-snapshot.v1 + sentiment-flow.v1 → survey-pipeline.v1",
     "fields": "facts(up/down/limit/amount/scope/as_of) · flow.delta/z20 · scan.rotation"},
    {"from": "sentiment_flow", "to": "survey", "contract": "sentiment-flow.v1 → survey-pipeline.v1",
     "fields": "level · band · turning_point · drivers"},
    {"from": "survey", "to": "experts", "contract": "survey-pipeline.v1 → expert-panel.v1",
     "fields": "regime.status/score · divergence · alerts"},
    {"from": "experts", "to": "memo", "contract": "expert-panel.v1 → brain-memo.v1",
     "fields": "consensus.stance/score/disagreement/veto · position_cap"},
    {"from": "memo", "to": "crosscheck", "contract": "三层快照 → layer-crosscheck.v1",
     "fields": "sentiment 三方同源 · breadth · turnover · limit_up · freshness"},
]

#: 参与指纹的字段（少而关键：多了噪声大，少了不可回放）
_FACTS: Dict[str, Any] = {
    "coverage": lambda p: {
        "total": _dig(p, "ashare", "total_universe"),
        "valid": _dig(p, "ashare", "quotes", "valid"),
        "limit_up": _dig(p, "ashare", "quotes", "limit_up"),
        "amount": _dig(p, "ashare", "quotes", "amount"),
        "level": _dig(p, "ashare", "quality", "level"),
    },
    # 注意路径：快照把市场数据放在 market 下（早期写成顶层 breadth 会取到 None，
    # 指纹就变成常量，看起来"稳定"其实什么都没抓到）
    "packet": lambda p: {
        "trace": p.get("trace_id"),
        "up_ratio": _dig(p, "market", "breadth", "up_ratio"),
        "limit_up": _dig(p, "market", "breadth", "limit_up"),
        "valid": _dig(p, "market", "breadth", "valid"),
        "amount": _dig(p, "market", "turnover", "amount"),
        "build_ms": _dig(p, "chain", "build_ms"),
        "evidence": len(p.get("evidence") or []),
    },
    "sentiment": lambda p: {
        "score": p.get("score"),
        "band": p.get("band") or _dig(p, "regime", "key"),
        "coverage": p.get("coverage"),
        "dims": [round(float(d.get("score") or 0), 1) for d in (p.get("dimensions") or [])][:6],
    },
    "sentiment_flow": lambda p: {
        "level": p.get("level"),
        "band": p.get("band"),
        "delta_short": p.get("delta_short"),
        "slope": p.get("slope_per_hour"),
        "turning": _dig(p, "turning_point", "detected"),
    },
    "survey": lambda p: {
        "regime": _dig(p, "regime", "status"),
        "score": _dig(p, "regime", "score"),
        "divergence": _dig(p, "divergence", "detected"),
        "facts": p.get("facts"),
        "alerts": len(p.get("alerts") or []),
    },
    "experts": lambda p: {
        "stance": _dig(p, "consensus", "stance"),
        "score": _dig(p, "consensus", "score"),
        "disagreement": _dig(p, "consensus", "disagreement"),
        "veto": bool(_dig(p, "consensus", "veto")),
        # 判断层是否真的进了研判团：对齐度与 survey 时点一起入指纹，
        # 否则「专家结论变了」看不出是数据变了还是多接了一层
        "survey_alignment": _dig(p, "consensus", "survey_alignment"),
        "survey_as_of": _dig(p, "inputs", "survey", "as_of"),
    },
    "crosscheck": lambda p: {
        "ok": p.get("ok"),
        "score": p.get("score"),
        "counts": p.get("counts"),
        "ratio": p.get("comparable_ratio"),
    },
    "memo": lambda p: {
        "stance": p.get("stance"),
        "cap": p.get("position_cap"),
        "conf": p.get("confidence"),
    },
}


def _dig(payload: Any, *path: str) -> Any:
    node = payload
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _facts_of(key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    probe = _FACTS.get(key)
    if probe is None or not isinstance(payload, dict):
        return {}
    try:
        return probe(payload)
    except Exception as exc:  # 单节点结构异常不应炸掉整条链
        logger.debug("lineage facts %s failed: %s", key, exc)
        return {}


def _digest(facts: Dict[str, Any]) -> str:
    blob = json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _node(key: str, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    present = isinstance(payload, dict) and bool(payload)
    # 缺失节点不要生成一堆 None 的 facts：那是「看起来有依据」，比空更糟
    facts = _facts_of(key, payload or {}) if present else {}
    return {
        "key": key,
        "present": present,
        "version": (payload or {}).get("version"),
        # as_of = 数据时点（业务时间）；built_at = 生产时间（构建/生成时间）。
        # 判断「上下游顺序」必须用 built_at：as_of 各层语义不同（报价时点/快照时点/
        # 计算时点），拿它比大小会把正常链路误判成时间倒挂。
        "as_of": (payload or {}).get("as_of"),
        "built_at": (payload or {}).get("generated_at") or (payload or {}).get("fetched_at"),
        "trace_id": (payload or {}).get("trace_id"),
        "ok": (payload or {}).get("ok"),
        "digest": _digest(facts) if present else None,
        "facts": facts,
    }


#: 参与「生产顺序」校验的节点（同一条闭环内按序生产）。
#: coverage 不在内：它由后台 worker 生产，随时可能被重建，时间晚于本轮快照是正常的；
#: 把它拉进顺序校验只会制造假告警。
ORDERED_NODES: List[str] = [
    "packet", "sentiment", "sentiment_flow", "survey", "experts", "crosscheck", "memo",
]


def _ts(value: Any):
    """把时间解析成**本地朴素** datetime；兼容 `T`/空格分隔与带/不带时区。

    两个坑都踩过：字符串直接比大小时 `'T' > ' '` 会误判；带时区与不带时区的
    datetime 直接相减会抛 `TypeError`（把整条闭环打成 500）。统一到本地时间再比。
    """
    if not value:
        return None
    text = str(value).strip().replace(" ", "T", 1)
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def build(trace_id: Optional[str], nodes: Dict[str, Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    """把一次闭环的各层产物整理成一条可解析的链路记录（纯函数，不落盘）。"""
    ordered = [_node(key, nodes.get(key)) for key in CHAIN]
    gaps = [item["key"] for item in ordered if not item["present"]]
    # 只对本轮链条内的节点校验生产顺序：来自另一份快照的节点（trace 不同）
    # 位置本身没有意义，拿它比顺序只会得出假告警。
    marks = [(item["key"], _ts(item["built_at"])) for item in ordered
             if item["key"] in ORDERED_NODES and _ts(item["built_at"])
             and (not item["trace_id"] or not trace_id or item["trace_id"] == trace_id)]
    out_of_order: List[Dict[str, Any]] = []
    check_error = None
    try:
        for (prev_key, prev), (cur_key, cur) in zip(marks, marks[1:]):
            if cur < prev:
                out_of_order.append({"from": prev_key, "from_built_at": str(prev),
                                     "to": cur_key, "to_built_at": str(cur)})
    except Exception as exc:  # 留痕不该因为一次比较失败把主链路打成 500
        check_error = f"{type(exc).__name__}: {exc}"
        logger.warning("lineage ordering check failed: %s", exc)
    trace_mismatch = [
        {"key": item["key"], "trace_id": item["trace_id"]}
        for item in ordered
        if item["trace_id"] and trace_id and item["trace_id"] != trace_id
    ]
    return {
        "version": LINEAGE_VERSION,
        "trace_id": trace_id,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "nodes": ordered,
        "edges": EDGES,
        "gaps": gaps,
        "checks": {
            "complete": not gaps,
            "time_ordered": not out_of_order and check_error is None,
            "trace_consistent": not trace_mismatch,
            "out_of_order": out_of_order,
            "trace_mismatch": trace_mismatch,
            "check_error": check_error,
        },
        # ok = 链路结构可用（节点齐、顺序对）。
        # trace 不一致不否证链路，它表示「某个节点的证据来自另一份快照」，
        # 单独用 consistent 表达，并在 citations 里逐条标注 same_trace。
        "ok": not gaps and not out_of_order and check_error is None,
        "consistent": not trace_mismatch,
    }


def citations(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """给结论附带的引用清单：只留「结论能反查依据」所需的字段。

    `same_trace=false` 表示这条依据来自**另一份快照**（当轮缓存错位/并发刷新）。
    不隐藏它：读者据此判断这条依据的可信度，而不是以为所有依据都同源。
    """
    trace = record.get("trace_id")
    return [
        {"node": item["key"], "version": item["version"], "as_of": item["as_of"],
         "digest": item["digest"],
         "same_trace": bool(not item["trace_id"] or not trace or item["trace_id"] == trace)}
        for item in record.get("nodes") or []
        if item.get("present") and item["key"] != "memo"
    ]


# ---------------------------------------------------------------------------
# 落盘环形：结论生成后写入，按 trace_id 反查
# ---------------------------------------------------------------------------
_KEEP = 60


def _read() -> List[Dict[str, Any]]:
    try:
        with open(RING_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write(rows: List[Dict[str, Any]]) -> None:
    tmp = RING_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rows[-_KEEP:], fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, RING_PATH)


def record(record_payload: Dict[str, Any]) -> Dict[str, Any]:
    """写入环形索引（同 trace_id 覆盖）。文件不可写时只告警，不影响主链路。"""
    if not record_payload.get("trace_id"):
        return record_payload
    try:
        rows = [r for r in _read() if r.get("trace_id") != record_payload["trace_id"]]
        rows.append(record_payload)
        _write(rows)
    except Exception as exc:
        logger.warning("lineage persist failed: %s", exc)
    return record_payload


def get(trace_id: str) -> Optional[Dict[str, Any]]:
    for row in reversed(_read()):
        if row.get("trace_id") == trace_id:
            return row
    return None


def recent(limit: int = 10) -> List[Dict[str, Any]]:
    rows = _read()
    return list(reversed(rows[-max(1, min(60, int(limit))):]))


__all__ = [
    "LINEAGE_VERSION", "CHAIN", "EDGES", "RING_PATH",
    "build", "citations", "record", "get", "recent",
]
