"""
DAPENG Alpha - 全球金融市场对 A 股传导建模 (M2)

本模块只使用**实测可达**的数据通道：腾讯日K（proxy.finance.qq.com，
复用 server.tencent_kline.fetch_tencent_kline）+ 腾讯/新浪实时行情。

提供：
  1) 覆盖全球资产实时快照（见 server.global_assets）
  2) 领先滞后相关：外部市场日收益 vs 沪深300 日收益，lag 0..3，输出 n
  3) 开盘缺口传导：隔夜外盘收益 → 次日沪深300 开盘缺口（相关 + 方向命中率 + n）
  4) 每日影响打分卡：每项 利好/利空/中性 + 分数 + 权重 + 证据，综合分可追溯

红线（项目铁律）：
  - 样本不足（n < MIN_SAMPLES）→ 返回 null + insufficient_samples，不给编造系数
  - 经验判断显式标 evidence_type="heuristic"，不与实测混同
  - 取不到即 available:false + reason
  - 绝无「当日涨幅 × 写死系数」这类推算
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from server.stable_market_provider import _read_disk_cache, _write_disk_cache
from server.tencent_kline import fetch_tencent_kline

logger = logging.getLogger(__name__)


def _ensure_logging() -> None:
    """确保本模块 INFO 日志可见（uvicorn 未配置 root logger，否则会被丢弃）。"""
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


_ensure_logging()

BENCHMARK = {"key": "csi300", "name": "沪深300", "tencent": "sh000300"}

# 有日线历史的外部市场（腾讯日K实测可达）→ 参与相关性/缺口传导建模
HISTORY_SYMBOLS: Dict[str, Dict[str, str]] = {
    "djia": {"name": "道琼斯", "tencent": "usDJI", "market": "us", "session": "overnight"},
    "ixic": {"name": "纳斯达克", "tencent": "usIXIC", "market": "us", "session": "overnight"},
    "ndx": {"name": "纳斯达克100", "tencent": "usNDX", "market": "us", "session": "overnight"},
    "spx": {"name": "标普500", "tencent": "usINX", "market": "us", "session": "overnight"},
    "vix": {"name": "VIX波动率", "tencent": "usVIX", "market": "us", "session": "overnight"},
    "hsi": {"name": "恒生指数", "tencent": "hkHSI", "market": "hk", "session": "intraday"},
    "hstech": {"name": "恒生科技", "tencent": "hkHSTECH", "market": "hk", "session": "intraday"},
}

LAGS = (0, 1, 2, 3)
MIN_SAMPLES = 30
HISTORY_LIMIT = 320
# linkage 结果内存缓存（日线级别结论，5 分钟内不必重算）
_LINKAGE_TTL = 300
_linkage_cache: Dict[str, Any] = {"ts": 0.0, "data": None}

# heuristic 打分卡权重（无量纲、显式标注为经验规则；实测项用相关强度作权重）
_HEUR_WEIGHTS: Dict[str, float] = {
    "sox": 0.18, "a50": 0.18, "brent": 0.10, "dxy": 0.12,
    "gold": 0.08, "usdcnh": 0.12, "vix": 0.12,
}
# 经验方向规则：符号 = "+1 表示该标的上涨对 A 股利好"。0 表示不作方向判断。
_HEUR_RULES: Dict[str, Tuple[int, str]] = {
    "sox": (1, "费半上涨→A股半导体产业链风险偏好上升"),
    "a50": (1, "A50期货上涨→次日A股偏多（离岸代理）"),
    "dxy": (-1, "美元指数上涨→新兴市场资金承压"),
    "vix": (-1, "VIX上涨→全球风险偏好下降"),
    "usdcnh": (-1, "离岸人民币贬值（数值上行）→A股承压"),
    "gold": (-1, "金价大涨多由避险驱动→风险资产承压"),
    "brent": (0, "油价对A股方向不确定，不作方向判断"),
}


# =====================================================================
#  历史序列：腾讯日K + data/cache 原子落盘积累
# =====================================================================
async def _load_history(key: str, tencent_code: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """加载/更新某标的日线历史（增量合并后原子落盘）。

    返回 (bars, fetch_ts)。bars 为 [{d, o, c}]；fetch_ts 为本次落盘时间。
    第二次调用若已有缓存则命中（仅增量补齐），并记录日志。
    """
    cache_key = f"global_hist_{key}"
    cached, cached_ts = _read_disk_cache(cache_key)
    bars = cached if isinstance(cached, list) else []

    fresh = await fetch_tencent_kline(tencent_code, "day", HISTORY_LIMIT)
    if not fresh:
        if bars:
            logger.info("global history %s: kline fetch empty, use disk cache ts=%s n=%d",
                        key, cached_ts, len(bars))
        return bars, cached_ts

    merged: Dict[str, Dict[str, Any]] = {}
    for b in bars:
        if isinstance(b, dict) and b.get("d"):
            merged[b["d"]] = {"d": b["d"], "o": b.get("o"), "c": b.get("c")}
    for b in fresh:
        merged[b["d"]] = {"d": b["d"], "o": b.get("o"), "c": b.get("c")}
    merged_list = [merged[d] for d in sorted(merged)][-HISTORY_LIMIT:]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if cached:
        logger.info("global history %s: HIT disk cache (was ts=%s n=%d) then merged -> n=%d",
                    key, cached_ts, len(bars), len(merged_list))
    _write_disk_cache(cache_key, merged_list, ts)
    return merged_list, ts


def _returns(bars: List[Dict[str, Any]]) -> Dict[str, float]:
    """日收益率 c[t]/c[t-1]-1，按日期索引。"""
    out: Dict[str, float] = {}
    prev_c = None
    for b in bars:
        c = b.get("c")
        if prev_c and c:
            out[b["d"]] = c / prev_c - 1.0
        prev_c = c if c else prev_c
    return out


def _opens(bars: List[Dict[str, Any]]) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """日期 → (开, 前收)（用于开盘缺口）。"""
    out = {}
    prev_c = None
    for b in bars:
        out[b["d"]] = (b.get("o"), prev_c)
        if b.get("c"):
            prev_c = b["c"]
    return out


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / (sxx ** 0.5 * syy ** 0.5)


def _lagged_correlations(ext_ret: Dict[str, float], a_ret: Dict[str, float]) -> Dict[str, Any]:
    """对 lag 0..3 计算 corr(ext[t], a[t+lag])，返回各 lag 的 corr 与 n，及最优领先期。"""
    a_dates = sorted(a_ret)
    per_lag: Dict[str, Dict[str, Any]] = {}
    best_lag, best_abs = None, -1.0
    for lag in LAGS:
        xs, ys = [], []
        for i in range(len(a_dates) - lag):
            d0 = a_dates[i]
            d1 = a_dates[i + lag]
            if d0 in ext_ret and d1 in a_ret:
                xs.append(ext_ret[d0])
                ys.append(a_ret[d1])
        n = len(xs)
        corr = _pearson(xs, ys) if n >= 2 else None
        sufficient = n >= MIN_SAMPLES
        per_lag[str(lag)] = {
            "lag": lag,
            "corr": round(corr, 6) if (corr is not None and sufficient) else None,
            "sample_size": n,
            "status": "ok" if sufficient else "insufficient_samples",
        }
        if corr is not None and sufficient and abs(corr) > best_abs:
            best_abs, best_lag = abs(corr), lag
    return {
        "by_lag": per_lag,
        "lead_days": best_lag,
        "best_abs_corr": round(best_abs, 6) if best_lag is not None else None,
        "status": "ok" if best_lag is not None else "insufficient_samples",
    }


def _gap_transmission(ext_ret: Dict[str, float], a_bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """隔夜外盘收益 → 次日沪深300开盘缺口：corr + 方向命中率 + n。"""
    opens = _opens(a_bars)
    a_dates = sorted(opens)
    xs, ys = [], []
    for i in range(1, len(a_dates)):
        d_prev, d_cur = a_dates[i - 1], a_dates[i]
        o, pc = opens[d_cur]
        if not o or not pc:
            continue
        if d_prev in ext_ret:
            xs.append(ext_ret[d_prev])
            ys.append(o / pc - 1.0)
    n = len(xs)
    sufficient = n >= MIN_SAMPLES
    corr = _pearson(xs, ys) if n >= 2 else None
    hits = sum(1 for x, y in zip(xs, ys) if (x > 0) == (y > 0))
    return {
        "corr": round(corr, 6) if (corr is not None and sufficient) else None,
        "direction_hit_rate": round(hits / n, 4) if (sufficient and n) else None,
        "sample_size": n,
        "method": "ext_ret[t-1] vs csi300_open_gap[t]",
        "status": "ok" if sufficient else "insufficient_samples",
    }


async def _build_series() -> Dict[str, Any]:
    """加载沪深300与各外部市场历史（并发），返回对齐所需的收益序列。"""
    keys = list(HISTORY_SYMBOLS.keys())
    results = await asyncio.gather(
        _load_history(BENCHMARK["key"], BENCHMARK["tencent"]),
        *[_load_history(k, HISTORY_SYMBOLS[k]["tencent"]) for k in keys],
    )
    bench_bars, bench_ts = results[0]
    a_ret = _returns(bench_bars)
    series: Dict[str, Any] = {}
    for idx, key in enumerate(keys):
        bars, ts = results[idx + 1]
        meta = HISTORY_SYMBOLS[key]
        series[key] = {
            "name": meta["name"], "tencent": meta["tencent"], "market": meta["market"],
            "session": meta["session"],
            "returns": _returns(bars),
            "bars_n": len(bars), "last_date": bars[-1]["d"] if bars else None,
            "fetch_ts": ts,
        }
    return {
        "benchmark": {"key": BENCHMARK["key"], "name": BENCHMARK["name"],
                      "tencent": BENCHMARK["tencent"], "bars": bench_bars,
                      "returns": a_ret, "fetch_ts": bench_ts},
        "series": series,
    }


async def get_linkage(force: bool = False) -> Dict[str, Any]:
    """完整传导建模结果（领先滞后 + 缺口传导 + 打分卡）。"""
    if not force and _linkage_cache["data"] is not None and time.time() - _linkage_cache["ts"] < _LINKAGE_TTL:
        return _linkage_cache["data"]
    data = await _build_series()
    a_ret = data["benchmark"]["returns"]
    a_bars = data["benchmark"]["bars"]
    as_of = a_bars[-1]["d"] if a_bars else None

    linkage: Dict[str, Any] = {}
    for key, s in data["series"].items():
        corr = _lagged_correlations(s["returns"], a_ret)
        gap = _gap_transmission(s["returns"], a_bars)
        rets = s["returns"]
        latest_ret = round(rets[max(rets)], 6) if rets else None
        linkage[key] = {
            "name": s["name"], "tencent": s["tencent"], "market": s["market"],
            "session": s["session"], "history_n": s["bars_n"], "last_date": s["last_date"],
            "lagged_correlation": corr,
            "gap_transmission": gap,
            "latest_ret": latest_ret,
            "method": "pearson corr on daily returns (Tencent daily K)",
            "as_of_date": as_of,
        }

    result = {
        "ok": True,
        "as_of_date": as_of,
        "benchmark": {"key": BENCHMARK["key"], "name": BENCHMARK["name"],
                      "tencent": BENCHMARK["tencent"]},
        "min_samples": MIN_SAMPLES,
        "linkage": linkage,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _linkage_cache["ts"] = time.time()
    _linkage_cache["data"] = result
    return result


def build_scorecard(linkage: Dict[str, Any], assets: Dict[str, Any]) -> Dict[str, Any]:
    """由 linkage + 实时行情生成"外部环境对A股"打分卡（综合分可追溯到分项）。

    实测项：方向取该外部市场最新日收益符号，权重∝领先滞后相关强度，evidence 含 symbol/corr/lag/n。
    经验项：无日线历史但有实时行情的标的，显式 evidence_type="heuristic"。
    """
    items: List[Dict[str, Any]] = []
    notes: List[Dict[str, Any]] = []

    quote_index: Dict[str, Dict[str, Any]] = {}
    for _cat, arr in (assets.get("categories") or {}).items():
        for it in arr:
            if it.get("available"):
                quote_index[it["key"]] = it

    def _add_heuristic(key: str, name: str, extra: Dict[str, Any]) -> None:
        q = quote_index.get(key)
        w = _HEUR_WEIGHTS.get(key, 0.0)
        sign, rule = _HEUR_RULES.get(key, (0, "no_directional_rule"))
        if sign == 0:
            # 不作方向判断 → 不进 items，仅记 notes（composite 与 items 语义一致）
            notes.append({
                "key": key, "name": name, "reason": "no_directional_rule", "rule": rule,
                "evidence": {**extra, "symbol": (q or {}).get("code"),
                             "source": (q or {}).get("source"), "chg": (q or {}).get("chg"),
                             "note": "此项不作方向判断，不计入 composite"},
            })
            return
        if not q or q.get("chg") is None:
            notes.append({"key": key, "name": name, "reason": "no_realtime_quote",
                          "evidence": extra})
            return
        chg = q["chg"]
        raw_dir = 1 if chg > 0 else -1 if chg < 0 else 0
        score = round(sign * raw_dir * w, 4)
        items.append({
            "key": key, "name": name,
            "stance": "利好" if score > 0.02 else "利空" if score < -0.02 else "中性",
            "score": score, "weight": w, "evidence_type": "heuristic",
            "evidence": {"symbol": q.get("code"), "source": q.get("source"), "chg": chg,
                         "rule": rule, "rule_sign": sign, **extra},
        })

    handled = set()

    # ① linkage 各市场：历史充足 → measured；样本不足 → heuristic 降级（无第二个开关）
    for key, info in (linkage.get("linkage") or {}).items():
        handled.add(key)
        lc = info.get("lagged_correlation") or {}
        if lc.get("status") != "ok":
            n0 = ((lc.get("by_lag") or {}).get("0") or {}).get("sample_size")
            _add_heuristic(key, info["name"], {
                "history_status": "insufficient_samples",
                "history_sample_size": n0,
                "quote_kind": "realtime",
            })
            continue
        lead = lc["lead_days"]
        corr = (lc["by_lag"][str(lead)] or {}).get("corr")
        n = (lc["by_lag"][str(lead)] or {}).get("sample_size")
        latest = info.get("latest_ret")
        if latest is None:
            stance, score = "中性", 0.0
        else:
            weight = abs(corr) if corr else 0.0
            score = round((1.0 if latest > 0 else -1.0) * weight, 4)
            stance = "利好" if score > 0.02 else "利空" if score < -0.02 else "中性"
        items.append({
            "key": key, "name": info["name"], "stance": stance,
            "score": score, "weight": round(abs(corr) if corr else 0.0, 4),
            "evidence_type": "measured",
            "evidence": {"symbol": info["tencent"], "corr": corr, "lead_days": lead,
                         "sample_size": n, "latest_ret": latest},
        })

    # ② 其余经验标的（在 _HEUR_WEIGHTS 中、且未被①处理）
    for key in _HEUR_WEIGHTS:
        if key in handled:
            continue
        q = quote_index.get(key)
        if not q:
            continue
        _add_heuristic(key, q.get("name") or key, {"quote_kind": "realtime"})

    measured_score = round(sum(x["score"] for x in items if x["evidence_type"] == "measured"), 4)
    heuristic_score = round(sum(x["score"] for x in items if x["evidence_type"] == "heuristic"), 4)
    composite = round(measured_score + heuristic_score, 4)
    return {
        "composite_score": composite,
        "composite_definition": ("Σ(分项 score)；实测项=方向×领先滞后相关强度；"
                                 "经验项=方向×规则符号×经验权重；notes 项不计分"),
        "components": {"measured": measured_score, "heuristic": heuristic_score},
        "stance": "利好" if composite > 0.05 else "利空" if composite < -0.05 else "中性",
        "items": items,
        "notes": notes,
    }


async def get_linkage_with_scorecard(force: bool = False) -> Dict[str, Any]:
    """linkage 结果 + 打分卡（含各外部市场最新日收益方向）。"""
    result = await get_linkage(force=force)
    from server.global_assets import get_global_assets
    assets = await get_global_assets()
    result["scorecard"] = build_scorecard(result, assets)
    result["scorecard"]["as_of_date"] = result.get("as_of_date")
    return result
