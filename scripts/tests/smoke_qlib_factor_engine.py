# -*- coding: utf-8 -*-
"""qlib_factor_engine 真实性与参数接线验证（合成数据，确定性）

验证目标：
  1. 参数真生效：weights / selected_factor_ids / start_date 改变 → 结果改变
  2. IC/IR 可独立复算（误差 < 1e-6）
  3. Q1~Q5 真实分组（样本数 / 因子区间 / 组内未来收益均值）
  4. 数据不足 → null + reason（不编造）
  5. meta.available_factor_ids == 引擎实际可算非 null 的因子集合
"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np

from server import qlib_factor_engine as qfe

rng = np.random.default_rng(20240607)


def _make_klines(days, base, seed_shift):
    """生成一只股票的真实格式日K（d/o/c/h/l/v）。"""
    r = np.random.default_rng(1000 + seed_shift)
    dates = []
    y, m, d = 2024, 1, 1
    import datetime as _dt
    cur = _dt.date(2024, 1, 1)
    while len(dates) < days:
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y-%m-%d"))
        cur += _dt.timedelta(days=1)
    price = base
    rows = []
    for ds in dates:
        price *= (1 + r.normal(0, 0.018))
        o = price * (1 + r.normal(0, 0.004))
        h = max(o, price) * (1 + abs(r.normal(0, 0.005)))
        l = min(o, price) * (1 - abs(r.normal(0, 0.005)))
        v = float(r.uniform(1e6, 5e6))
        rows.append({"d": ds, "o": round(o, 3), "c": round(price, 3),
                     "h": round(h, 3), "l": round(l, 3), "v": v})
    return rows


N_STOCKS = 12
DAYS = 130
STOCKS = []
for i in range(N_STOCKS):
    code = "sh%06d" % (600000 + i * 7)
    STOCKS.append({"code": code, "name": "S%02d" % i,
                   "price": 10.0, "chg": 0.0, "amount": 1e8 + i * 1e7,
                   "klines": _make_klines(DAYS, 8.0 + i, i)})

BENCH = _make_klines(DAYS, 3000.0, 999)


async def _patched_load_universe():
    return STOCKS


async def _patched_fetch_kline(code, period="day", limit=100, timeout=8.0):
    if code == "sh000300":
        return BENCH
    return []


qfe._load_universe = _patched_load_universe
qfe.fetch_tencent_kline = _patched_fetch_kline


def _pearson_ref(x, y):
    xs = [v for v in x]
    ys = [v for v in y]
    m = [(i) for i in range(len(xs)) if xs[i] is not None and ys[i] is not None]
    if len(m) < 3:
        return None
    a = np.array([xs[i] for i in m], float)
    b = np.array([ys[i] for i in m], float)
    if a.std() < 1e-12 or b.std() < 1e-12:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _spearman_ref(x, y):
    m = [i for i in range(len(x)) if x[i] is not None and y[i] is not None]
    if len(m) < 3:
        return None
    a = np.array([x[i] for i in m], float)
    b = np.array([y[i] for i in m], float)
    ar = np.argsort(np.argsort(a)).astype(float)
    br = np.argsort(np.argsort(b)).astype(float)
    if ar.std() < 1e-12 or br.std() < 1e-12:
        return None
    return float(np.corrcoef(ar, br)[0, 1])


async def main():
    print("=" * 70)
    print("A. meta.available_factor_ids vs 引擎可算集合")
    meta = qfe.qlib_engine.get_meta()
    print("   meta.available_factor_ids =", meta["available_factor_ids"])
    print("   engine.computable_factor_ids =", qfe.computable_factor_ids())
    assert meta["available_factor_ids"] == qfe.computable_factor_ids(), "元数据与实算不一致!"
    print("   -> 一致 OK")

    print("=" * 70)
    print("B. 基线回测（默认权重 / 全可用因子 / 全区间）")
    base = await qfe.qlib_engine.evaluate_factor_combination(
        selected_factor_ids=["Alpha_Mom_20", "Alpha_Vol_Std", "Alpha_Liq_Turnover"],
        top_k=8, start_date="2024-01-01", end_date="2024-12-31", include_debug=True)
    assert base["ok"], base.get("message")
    m = base["metrics"]
    print("   IC=%.6f  IR=%s  sharpe=%s  ann=%.2f%%  maxDD=%.2f%%  NAV_last=%.4f"
          % (m["combined_ic"], m["combined_ir"], m["sharpe_ratio"],
             m["annual_return"], m["max_drawdown"], base["chart_data"]["strategy"][-1]))
    print("   factor_stats IC/IR:")
    for fid, st in base["factor_stats"].items():
        print("     %-26s ic=%-10s rank_ic=%-10s ir=%-8s periods=%d status=%s"
              % (fid, st["ic"], st["rank_ic"], st["ir"], st["ic_periods"], st["status"]))

    print("=" * 70)
    print("C. IC_t 时间序列（前 12 期）+ 独立复算比对")
    ic_series = base["ic_series"]
    dbg = base["debug"]["composite_ic_input"]
    print("   n_periods =", len(ic_series))
    max_err = 0.0
    max_rerr = 0.0
    for i, row in enumerate(ic_series[:12]):
        d = row["date"]
        rec = next(r for r in dbg if r["date"] == d)
        ic_ref = _pearson_ref(rec["factor"], rec["forward_return"])
        ric_ref = _spearman_ref(rec["factor"], rec["forward_return"])
        err = abs((ic_ref or 0) - (row["ic"] or 0))
        rerr = abs((ric_ref or 0) - (row["rank_ic"] or 0))
        max_err = max(max_err, err)
        max_rerr = max(max_rerr, rerr)
        print("     %s  ic=%s  rank_ic=%s  n=%d  | 复算 ic=%.6f rank_ic=%.6f"
              % (d, row["ic"], row["rank_ic"], row["n"],
                 ic_ref if ic_ref is not None else float("nan"),
                 ric_ref if ric_ref is not None else float("nan")))
    print("   max |IC 复算误差| = %.3e ; max |rank_IC 复算误差| = %.3e" % (max_err, max_rerr))
    assert max_err < 1e-6 and max_rerr < 1e-6, "IC 复算误差过大!"

    print("=" * 70)
    print("D. Q1~Q5 真实分组（样本数 / 因子区间 / 组内未来收益均值）")
    tot = 0
    for g in base["quantiles"]:
        tot += g["sample_count"]
        print("   %s  n=%-4d factor∈[%.6f, %.6f]  mean_fwd=%.4f%%  annual=%.2f%%"
              % (g["group"], g["sample_count"], g["factor_low"], g["factor_high"],
                 g["mean_return"], g["annual_return"]))
    # 因子区间应单调递增（真实按因子值排序分组）
    lows = [g["factor_low"] for g in base["quantiles"]]
    his = [g["factor_high"] for g in base["quantiles"]]
    assert all(lows[i] <= his[i] for i in range(5)), "分组区间异常"
    assert all(his[i] <= lows[i + 1] for i in range(4)), "Q1~Q5 未按因子值单调分组!"
    print("   total pooled samples =", tot, " -> 真实分组 OK")

    print("=" * 70)
    print("E. 参数真生效对比")
    # E1 weights
    w2 = await qfe.qlib_engine.evaluate_factor_combination(
        selected_factor_ids=["Alpha_Mom_20", "Alpha_Vol_Std", "Alpha_Liq_Turnover"],
        weights={"Alpha_Mom_20": 1.0, "Alpha_Vol_Std": 0.0, "Alpha_Liq_Turnover": 0.0},
        top_k=8, start_date="2024-01-01", end_date="2024-12-31")
    print("   [weights] base  IC=%.6f NAVlast=%.6f | 只留动量 IC=%.6f NAVlast=%.6f"
          % (m["combined_ic"], base["chart_data"]["strategy"][-1],
             w2["metrics"]["combined_ic"], w2["chart_data"]["strategy"][-1]))
    assert w2["params"]["weights"] != base["params"]["weights"], "weights 未回传"
    w_changed = (w2["metrics"]["combined_ic"] != m["combined_ic"]) or \
                (w2["chart_data"]["strategy"][-1] != base["chart_data"]["strategy"][-1])
    print("   weights 生效:", w_changed)
    assert w_changed, "改 weights 结果未变!"

    # E2 selected factors
    s2 = await qfe.qlib_engine.evaluate_factor_combination(
        selected_factor_ids=["Alpha_Liq_Turnover"], top_k=8,
        start_date="2024-01-01", end_date="2024-12-31")
    print("   [factors] base IC=%.6f NAVlast=%.6f | 仅量能收缩 IC=%.6f NAVlast=%.6f"
          % (m["combined_ic"], base["chart_data"]["strategy"][-1],
             s2["metrics"]["combined_ic"], s2["chart_data"]["strategy"][-1]))
    s_changed = (s2["metrics"]["combined_ic"] != m["combined_ic"])
    print("   selected_factor_ids 生效:", s_changed)
    assert s_changed, "改 selected_factor_ids 结果未变!"

    # E3 start_date
    d2 = await qfe.qlib_engine.evaluate_factor_combination(
        selected_factor_ids=["Alpha_Mom_20", "Alpha_Vol_Std", "Alpha_Liq_Turnover"],
        top_k=8, start_date="2024-04-01", end_date="2024-12-31")
    print("   [dates] base n=%d NAVlast=%.6f | start=2024-04-01 n=%d NAVlast=%.6f"
          % (m["sample_days"], base["chart_data"]["strategy"][-1],
             d2["metrics"]["sample_days"], d2["chart_data"]["strategy"][-1]))
    d_changed = (d2["metrics"]["sample_days"] != m["sample_days"])
    print("   start_date 生效:", d_changed, "| 区间日期首末:", d2["chart_data"]["dates"][0],
          "~", d2["chart_data"]["dates"][-1])
    assert d_changed, "改 start_date 结果未变!"

    print("   param_warnings(base) =", base["param_warnings"])
    bad_w = await qfe.qlib_engine.evaluate_factor_combination(
        selected_factor_ids=["Alpha_Quality_ROE", "Alpha_OrderFlow_Imbalance", "Alpha_Mom_20"],
        weights={"Alpha_Quality_ROE": 1.0},
        top_k=8, start_date="2024-01-01", end_date="2024-12-31")
    print("   param_warnings(含不可用因子) =")
    for w in bad_w["param_warnings"]:
        print("     -", w)

    print("=" * 70)
    print("F. 数据不足 → null + reason（不编造）")
    # 只给 5 根 K 线
    tiny = [{"code": s["code"], "name": s["name"], "price": 10.0, "chg": 0.0,
             "amount": 1e8, "klines": s["klines"][:5]} for s in STOCKS]

    async def _tiny_universe():
        return tiny

    qfe._load_universe = _tiny_universe
    short = await qfe.qlib_engine.evaluate_factor_combination(
        selected_factor_ids=["Alpha_Mom_20"], top_k=5,
        start_date="2024-01-01", end_date="2024-12-31")
    print("   ok =", short["ok"], "| message =", short.get("message"))
    print("   metrics.combined_ic =", short["metrics"]["combined_ic"],
          "| ic_reason =", short["metrics"]["ic_reason"])
    print("   metrics.sharpe_ratio =", short["metrics"]["sharpe_ratio"])
    assert short["metrics"]["combined_ic"] is None, "数据不足却给出 IC 假数!"
    assert short["metrics"]["sharpe_ratio"] is None
    qfe._load_universe = _patched_load_universe

    print("=" * 70)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
