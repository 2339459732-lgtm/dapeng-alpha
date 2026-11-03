"""M3 ⑤ 板块关联图谱：成分股 Jaccard 重叠 + 收益相关性聚类。

数据源（均已落盘，无网络）：
- `data/sector_map.json`    成分股（Jaccard）
- `data/sector_returns.json` 板块日收益（相关性）

要点：
- **层级混排**：`白酒Ⅲ ⊂ 白酒Ⅱ ⊂ 食品饮料` 这类父子板块 Jaccard 天然很高，
  必须标 `same_lineage: true`，不能当作"真实联动"。
- 板块日收益原始相关被**共同市场因子**主导（p50≈0.53），故聚类用**去市场因子**
  后的残差相关（每日横截面减去当日全板块均值）；邻居查询同时给 raw corr 与 demeaned corr。
- 样本 < 30 → `insufficient_samples`，不给系数。
"""

import json
import logging
import os
import threading
import time
from itertools import combinations
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join(_PROJECT_ROOT, "data", "sector_map.json")
RETURNS_PATH = os.path.join(_PROJECT_ROOT, "data", "sector_returns.json")

MIN_SAMPLES = 30
JACCARD_MIN = 0.10          # 图里保留的最小 Jaccard（更小的等于没有重叠）
DEFAULT_CORR_EDGE = 0.60    # 邻居给 corr 边的最小（去市场因子）阈值
DEFAULT_CLUSTER_THRESHOLD = 0.70   # 聚类连通阈值（去市场因子）
DEFAULT_TOPK = 5            # 每个节点最多保留的最强 K 条 corr 边（防毛球）
_AS_OF: Optional[str] = None

_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_TTL = 300
_lock = threading.Lock()


def _read_json(path: str) -> Optional[dict]:
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning("sector_graph read %s failed: %s: %s", path, type(e).__name__, e)
        return None


def _pearson_matrix(M: np.ndarray) -> np.ndarray:
    """行向量两两 Pearson 相关（处理 NaN：不足两点的组合置 NaN）。"""
    n = M.shape[0]
    # 用 pandas 风格：numpy 直接 corrcoef 会因 NaN 扩散，这里逐行 groupby 复杂度高；
    # 由于板块收益矩阵缺失极少（仅少数板块首日），先用列均值填补再算，并记录有效样本数。
    Mf = np.where(np.isnan(M), np.nanmean(M, axis=1, keepdims=True), M)
    valid = (~np.isnan(M)).sum(axis=1)
    C = np.corrcoef(Mf)
    # 有效样本数 < MIN_SAMPLES 的板块 → 相关系数置 NaN（不给系数）
    bad = np.where(valid < MIN_SAMPLES)[0]
    for i in bad:
        C[i, :] = np.nan
        C[:, i] = np.nan
    return C, valid


def _compute() -> Dict[str, Any]:
    sm = _read_json(MAP_PATH)
    sr = _read_json(RETURNS_PATH)
    if not sm or not sm.get("sectors"):
        return {"ok": False, "reason": "sector_map_missing"}
    if not sr or not sr.get("dates") or not sr.get("series"):
        return {"ok": False, "reason": "sector_returns_missing"}

    sectors = sm["sectors"]
    dates: List[str] = sr["dates"]
    series: Dict[str, List[Optional[float]]] = sr["series"]
    as_of = dates[-1] if dates else None
    global _AS_OF
    _AS_OF = as_of

    codes = [c for c in sectors if sectors[c].get("constituents")]
    name_of = {c: (sectors[c].get("name") or c) for c in codes}
    level_of = {c: sectors[c].get("level") for c in codes}
    setsets = {c: set(sectors[c]["constituents"]) for c in codes}

    # ---- 成分股重叠（Jaccard）----
    overlap_edges: List[Dict[str, Any]] = []
    neighbors_overlap: Dict[str, List[Dict[str, Any]]] = {c: [] for c in codes}
    for a, b in combinations(codes, 2):
        A, B = setsets[a], setsets[b]
        inter = len(A & B)
        if not inter:
            continue
        union = len(A | B)
        jac = inter / union if union else 0.0
        same_lineage = bool(A <= B or B <= A)   # 非严格包含（含成分股完全相同）= 父子/同族 lineage
        # 邻居列表：只要共享成分股就收录（排序时按 Jaccard）
        neighbors_overlap[a].append({"b": b, "name": name_of[b], "level": level_of[b],
                                     "value": round(jac, 6), "shared_count": inter,
                                     "sample_size": union, "same_lineage": same_lineage})
        neighbors_overlap[b].append({"b": a, "name": name_of[a], "level": level_of[a],
                                     "value": round(jac, 6), "shared_count": inter,
                                     "sample_size": union, "same_lineage": same_lineage})
        # 图谱边：仅保留 Jaccard >= 阈值（防毛球）
        if jac < JACCARD_MIN:
            continue
        overlap_edges.append({"a": a, "b": b, "type": "overlap", "value": round(jac, 6),
                              "shared_count": inter, "sample_size": union,
                              "same_lineage": same_lineage, "as_of": as_of})

    # ---- 收益相关性（raw + 去市场因子）----
    ret_codes = [c for c in codes if c in series]
    idx = {c: i for i, c in enumerate(ret_codes)}
    M = np.array([[np.nan if v is None else v for v in series[c]] for c in ret_codes], dtype=float) \
        if ret_codes else np.zeros((0, len(dates)))
    raw_corr, valid_n = _pearson_matrix(M) if ret_codes else (np.zeros((0, 0)), np.zeros(0))
    if ret_codes:
        D = M - np.nanmean(M, axis=0, keepdims=True)   # 去当日横截面均值（市场因子）
        dem_corr, _ = _pearson_matrix(D)
    else:
        dem_corr = np.zeros((0, 0))

    n_rc = len(ret_codes)

    def _corr_neighbors(code: str, top: int) -> List[Dict[str, Any]]:
        if code not in idx:
            return []
        i = idx[code]
        rows = []
        for j in range(n_rc):
            if j == i:
                continue
            rv = raw_corr[i, j]
            dv = dem_corr[i, j]
            if np.isnan(dv) or dv < DEFAULT_CORR_EDGE:
                continue
            rows.append({
                "b": ret_codes[j], "name": name_of.get(ret_codes[j]),
                "level": level_of.get(ret_codes[j]),
                "value": round(float(dv), 6),           # 去市场因子相关（主序）
                "corr_demeaned": round(float(dv), 6),
                "corr_raw": (None if np.isnan(rv) else round(float(rv), 6)),
                "sample_size": int(valid_n[i]),
                "same_lineage": bool(setsets[code] < setsets[ret_codes[j]]
                                     or setsets[ret_codes[j]] < setsets[code])
                if (code in setsets and ret_codes[j] in setsets) else False,
            })
        rows.sort(key=lambda x: x["value"], reverse=True)
        return rows[:top]

    # ---- 聚类（去市场因子：阈值 + 每节点 topK，连通分量）----
    adj: Dict[int, List[tuple]] = {}
    if n_rc:
        for i in range(n_rc):
            cand = []
            for j in range(n_rc):
                if i == j:
                    continue
                dv = dem_corr[i, j]
                if not np.isnan(dv) and dv >= DEFAULT_CLUSTER_THRESHOLD:
                    cand.append((float(dv), j))
            cand.sort(reverse=True)
            for dv, j in cand[:DEFAULT_TOPK]:
                adj.setdefault(i, []).append((dv, j))
    # 连通分量
    seen = set()
    clusters_raw: List[List[int]] = []
    for i in range(n_rc):
        if i in seen:
            continue
        stack, comp = [i], []
        seen.add(i)
        while stack:
            u = stack.pop()
            comp.append(u)
            for _dv, v in adj.get(u, []):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        clusters_raw.append(comp)

    clusters = []
    for comp in clusters_raw:
        members = [ret_codes[i] for i in comp]
        pair_vals = []
        for x, y in combinations(comp, 2):
            dv = dem_corr[x, y]
            if not np.isnan(dv):
                pair_vals.append(float(dv))
        clusters.append({
            "size": len(members),
            "members": [{"code": c, "name": name_of.get(c), "level": level_of.get(c)}
                        for c in members],
            "mean_corr_demeaned": round(float(np.mean(pair_vals)), 6) if pair_vals else None,
            "sample_size": int(valid_n[comp[0]]) if comp else 0,
            "as_of": as_of,
        })
    clusters.sort(key=lambda c: c["size"], reverse=True)

    data = {
        "ok": True,
        "as_of_date": as_of,
        "min_samples": MIN_SAMPLES,
        "jaccard_min": JACCARD_MIN,
        "corr_edge_threshold": DEFAULT_CORR_EDGE,
        "cluster_threshold": DEFAULT_CLUSTER_THRESHOLD,
        "cluster_topk": DEFAULT_TOPK,
        "sectors_count": len(codes),
        "overlap_edges_count": len(overlap_edges),
        "clusters_count": len(clusters),
        "same_lineage_pairs": sum(1 for e in overlap_edges if e["same_lineage"]),
        "names": name_of,
        "levels": level_of,
        "_overlap_neighbors": neighbors_overlap,
        "_ret_codes": ret_codes,
        "_idx": idx,
        "_setmap": setsets,
        "_raw_corr": raw_corr,
        "_dem_corr": dem_corr,
        "_valid_n": valid_n,
        "_clusters": clusters,
        "_overlap_edges": overlap_edges,
    }
    return data


def _ensure() -> Dict[str, Any]:
    with _lock:
        if _CACHE["data"] is not None and time.time() - _CACHE["ts"] < _TTL:
            return _CACHE["data"]
    data = _compute()
    with _lock:
        _CACHE["ts"] = time.time()
        _CACHE["data"] = data
    return data


def get_neighbors(code: str, top: int = 10,
                  edge_type: str = "both") -> Dict[str, Any]:
    """单板块邻居：成分股重叠（Jaccard）+ 收益相关（去市场因子）。"""
    data = _ensure()
    if not data.get("ok"):
        return {"ok": False, "data": None, "message": "图谱数据未就绪", "reason": data.get("reason")}
    names = data["names"]
    if code not in data["_overlap_neighbors"] and code not in data["_ret_codes"]:
        return {"ok": False, "message": "板块不存在", "code": code}

    overlap = sorted(data["_overlap_neighbors"].get(code, []),
                     key=lambda x: x["value"], reverse=True)[:top]
    corr = _corr_neighbors_for(data, code, top)
    out = {
        "ok": True, "code": code, "name": names.get(code),
        "level": data["levels"].get(code),
        "as_of_date": data["as_of_date"], "min_samples": data["min_samples"],
        "edge_thresholds": {"jaccard_min_for_graph": data["jaccard_min"],
                            "corr_demeaned_min_for_graph": data["corr_edge_threshold"],
                            "note": ("邻居列表按 value 取 top-N（不做硬阈值截断）；"
                                     "上述阈值仅用于图谱边/聚类")},
        "method": "overlap=Jaccard(constituents); corr=pearson on market-demeaned daily returns",
    }
    if edge_type in ("both", "overlap"):
        out["overlap_neighbors"] = [{"b": x["b"], "name": x["name"], "type": "overlap",
                                     "value": x["value"], "shared_count": x["shared_count"],
                                     "sample_size": x["sample_size"],
                                     "same_lineage": x["same_lineage"],
                                     "as_of": data["as_of_date"]} for x in overlap]
    if edge_type in ("both", "corr"):
        out["corr_neighbors"] = corr
    return out


def _corr_neighbors_for(data: Dict[str, Any], code: str, top: int) -> List[Dict[str, Any]]:
    """从缓存的 corr 矩阵取某板块的去市场因子相关邻居。"""
    matrix = data.get("_dem_corr")
    ret_codes = data["_ret_codes"]
    idx = data.get("_idx") or {}
    if matrix is None or code not in idx:
        return []
    i = idx[code]
    rows = []
    for j, other in enumerate(ret_codes):
        if j == i:
            continue
        dv = matrix[i, j]
        if np.isnan(dv):
            continue
        rows.append({
            "b": other, "name": data["names"].get(other), "level": data["levels"].get(other),
            "type": "corr", "value": round(float(dv), 6),
            "corr_demeaned": round(float(dv), 6),
            "corr_raw": (None if np.isnan(data["_raw_corr"][i, j])
                         else round(float(data["_raw_corr"][i, j]), 6)),
            "sample_size": int(data["_valid_n"][i]),
            "same_lineage": bool(data["_setmap"][code] <= data["_setmap"][other]
                                 or data["_setmap"][other] <= data["_setmap"][code]),
            "as_of": data["as_of_date"],
        })
    rows.sort(key=lambda x: x["value"], reverse=True)
    return rows[:top]


def get_clusters() -> Dict[str, Any]:
    """同涨同跌簇（去市场因子相关 + 阈值 + topK 连通分量）。"""
    data = _ensure()
    if not data.get("ok"):
        return {"ok": False, "data": None, "message": "图谱数据未就绪", "reason": data.get("reason")}
    return {
        "ok": True,
        "as_of_date": data["as_of_date"],
        "min_samples": data["min_samples"],
        "method": ("pearson on market-demeaned daily returns; "
                   "edge if corr>=threshold & within node topK; connected components"),
        "cluster_threshold": data["cluster_threshold"],
        "cluster_topk": data["cluster_topk"],
        "sectors_count": data["sectors_count"],
        "clusters_count": data["clusters_count"],
        "clusters": data["_clusters"],
    }
