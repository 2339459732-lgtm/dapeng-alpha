"""M3 ⑥ 板块「金融属性敏感度」：板块日收益 × M2 外部因子日收益。

- 板块日收益序列：`data/sector_returns.json`（由 scripts/build_sector_periods.py 落盘）
- 外部因子日收益：复用 M2 的盘缓存 `data/cache/global_hist_<key>_last.json`
  （`server.global_linkage.HISTORY_SYMBOLS`：djia/ixic/ndx/spx/vix/hsi/hstech）

对每个板块 × 每个因子，算 lag 0..3 的相关系数（corr(因子[t], 板块[t+lag])），
取 |corr| 最大者为其 lead_days；**样本 < MIN_SAMPLES → insufficient_samples，不给系数**。
结果内存缓存 300s；纯 CPU（numpy），无网络。
"""

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np

from server.sector_map import load_sector_map
from server.stable_market_provider import _read_disk_cache

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RETURNS_PATH = os.path.join(_PROJECT_ROOT, "data", "sector_returns.json")

MIN_SAMPLES = 30
LAGS = (0, 1, 2, 3)
_METHOD = "pearson corr on daily returns (sector index return vs external factor return)"

# 外部因子：key → (M2 缓存 key 后缀, tencent 符号, 中文名)
FACTORS: Dict[str, Dict[str, str]] = {
    "djia": {"symbol": "usDJI", "name": "道琼斯"},
    "ixic": {"symbol": "usIXIC", "name": "纳斯达克"},
    "ndx": {"symbol": "usNDX", "name": "纳斯达克100"},
    "spx": {"symbol": "usINX", "name": "标普500"},
    "vix": {"symbol": "usVIX", "name": "VIX波动率"},
    "hsi": {"symbol": "hkHSI", "name": "恒生指数"},
    "hstech": {"symbol": "hkHSTECH", "name": "恒生科技"},
}

_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_TTL = 300
_lock = threading.Lock()


def _external_returns() -> Dict[str, Dict[str, float]]:
    """从 M2 盘缓存读外部因子日收益 {factor: {date: ret}}（无网络）。"""
    out: Dict[str, Dict[str, float]] = {}
    for key in FACTORS:
        rows, _ts = _read_disk_cache(f"global_hist_{key}")
        if not isinstance(rows, list) or not rows:
            continue
        rets: Dict[str, float] = {}
        prev = None
        for b in rows:
            d = b.get("d") if isinstance(b, dict) else None
            c = b.get("c") if isinstance(b, dict) else None
            if prev and c:
                rets[d] = c / prev - 1.0
            if c:
                prev = c
        if rets:
            out[key] = rets
    return out


def _load_sector_returns() -> Optional[Dict[str, Any]]:
    try:
        if not os.path.exists(RETURNS_PATH):
            return None
        with open(RETURNS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("dates") or not data.get("series"):
            return None
        return data
    except Exception as e:  # noqa: BLE001
        logger.warning("sector_returns read failed: %s: %s", type(e).__name__, e)
        return None


def _pearson(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    if x.size < 2:
        return None
    sx = x.std()
    sy = y.std()
    if sx == 0 or sy == 0:
        return None
    return float(((x - x.mean()) * (y - y.mean())).mean() / (sx * sy))


def _lag_corr(ext_ret: Dict[str, float], dates: List[str],
              sect_arr: np.ndarray) -> Dict[str, Any]:
    """对 lag 0..3 计算 corr(因子[t], 板块[t+lag])，返回各 lag 与最优领先期。"""
    n = len(dates)
    ext_vec = np.array([ext_ret.get(d, np.nan) for d in dates], dtype=float)
    per_lag: Dict[str, Any] = {}
    best_lag, best_abs = None, -1.0
    for lag in LAGS:
        if n <= lag:
            per_lag[str(lag)] = {"corr": None, "sample_size": 0, "status": "insufficient_samples"}
            continue
        x = ext_vec[:n - lag]
        y = sect_arr[lag:]
        mask = ~np.isnan(x) & ~np.isnan(y)
        cnt = int(mask.sum())
        corr = _pearson(x[mask], y[mask]) if cnt >= 2 else None
        ok = cnt >= MIN_SAMPLES
        per_lag[str(lag)] = {
            "corr": round(corr, 6) if (corr is not None and ok) else None,
            "sample_size": cnt,
            "status": "ok" if ok else "insufficient_samples",
        }
        if corr is not None and ok and abs(corr) > best_abs:
            best_abs, best_lag = abs(corr), lag
    return {
        "by_lag": per_lag,
        "lead_days": best_lag,
        "best_abs_corr": round(best_abs, 6) if best_lag is not None else None,
        "status": "ok" if best_lag is not None else "insufficient_samples",
    }


def compute_linkage() -> Dict[str, Any]:
    """计算所有板块 × 外部因子的敏感度（内存缓存）。"""
    with _lock:
        if _CACHE["data"] is not None and time.time() - _CACHE["ts"] < _TTL:
            return _CACHE["data"]

    sr = _load_sector_returns()
    if sr is None:
        return {"ok": False, "data": None, "message": "板块收益序列尚未构建",
                "hint": "python scripts/build_sector_periods.py --force"}
    dates: List[str] = sr["dates"]
    series: Dict[str, List[Optional[float]]] = sr["series"]
    ext = _external_returns()
    smap = load_sector_map() or {}
    smap_sectors = smap.get("sectors") or {}

    # 板块回报矩阵（行=板块，列=dates）
    codes = [c for c in series if series.get(c)]
    mat = np.array([[np.nan if v is None else v for v in series[c]] for c in codes],
                   dtype=float) if codes else np.zeros((0, len(dates)))

    factors_meta = [{"key": k, "name": v["name"], "symbol": v["symbol"],
                     "available": k in ext} for k, v in FACTORS.items()]

    sectors_out: Dict[str, Any] = {}
    ranking: List[Dict[str, Any]] = []
    for i, code in enumerate(codes):
        entry = smap_sectors.get(code) or {}
        name = entry.get("name") or code
        level = entry.get("level")
        sents = []
        for fkey, meta in FACTORS.items():
            if fkey not in ext:
                sents.append({"factor": fkey, "name": meta["name"], "symbol": meta["symbol"],
                              "corr": None, "sample_size": 0, "lead_days": None,
                              "status": "factor_unavailable", "as_of": dates[-1] if dates else None})
                continue
            res = _lag_corr(ext[fkey], dates, mat[i])
            sents.append({
                "factor": fkey, "name": meta["name"], "symbol": meta["symbol"],
                "corr": (res["by_lag"][str(res["lead_days"])]["corr"]
                         if res["lead_days"] is not None else None),
                "sample_size": (res["by_lag"][str(res["lead_days"])]["sample_size"]
                                if res["lead_days"] is not None else
                                (res["by_lag"]["0"]["sample_size"] if res["by_lag"] else 0)),
                "lead_days": res["lead_days"],
                "status": res["status"],
                "by_lag": res["by_lag"],
                "as_of": dates[-1] if dates else None,
            })
        # 最强因子（仅 ok 项）
        ok_sents = [s for s in sents if s["status"] == "ok" and s["corr"] is not None]
        top = max(ok_sents, key=lambda s: abs(s["corr"])) if ok_sents else None
        sectors_out[code] = {"name": name, "level": level, "sensitivities": sents}
        ranking.append({
            "code": code, "name": name, "level": level,
            "top_factor": top["factor"] if top else None,
            "top_factor_name": top["name"] if top else None,
            "top_corr": top["corr"] if top else None,
            "top_sample_size": top["sample_size"] if top else None,
            "top_lead_days": top["lead_days"] if top else None,
            "status": "ok" if top else "insufficient_samples",
        })

    ranking.sort(key=lambda r: abs(r["top_corr"]) if r["top_corr"] is not None else -1, reverse=True)
    result = {
        "ok": True,
        "as_of_date": dates[-1] if dates else None,
        "min_samples": MIN_SAMPLES,
        "method": _METHOD,
        "factors": factors_meta,
        "sectors_count": len(codes),
        "ranking": ranking,
        "sectors": sectors_out,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with _lock:
        _CACHE["ts"] = time.time()
        _CACHE["data"] = result
    return result


def get_sector_linkage(code: Optional[str] = None, top: int = 50) -> Dict[str, Any]:
    """对外入口：code 给单个板块全量敏感度；否则给 top-N 排行。"""
    data = compute_linkage()
    if not data.get("ok"):
        return data
    if code:
        entry = (data.get("sectors") or {}).get(code)
        if entry is None:
            return {"ok": False, "message": "板块不存在或收益序列缺失", "code": code}
        return {
            "ok": True, "code": code, "name": entry["name"], "level": entry["level"],
            "as_of_date": data["as_of_date"], "min_samples": data["min_samples"],
            "method": data["method"], "factors": data["factors"],
            "sensitivities": entry["sensitivities"],
            "generated_at": data["generated_at"],
        }
    return {
        "ok": True, "as_of_date": data["as_of_date"], "min_samples": data["min_samples"],
        "method": data["method"], "factors": data["factors"],
        "sectors_count": data["sectors_count"],
        "ranking": data["ranking"][:max(1, top)],
        "generated_at": data["generated_at"],
    }
