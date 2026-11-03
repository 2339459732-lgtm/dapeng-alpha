# -*- coding: utf-8 -*-
"""quant_core 指标链路冒烟测试（修复后必须有真实输出，不接受"能导入"）"""
import os, sys, math, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np

from server import quant_core as qc

random.seed(7)
np.random.seed(7)

N = 90
dates = pd.date_range("2024-01-01", periods=N, freq="B")
price = 20.0
rows = []
for i, dt in enumerate(dates):
    price *= 1 + random.uniform(-0.02, 0.022)
    o = price * (1 + random.uniform(-0.01, 0.01))
    h = max(o, price) * (1 + random.uniform(0, 0.012))
    l = min(o, price) * (1 - random.uniform(0, 0.012))
    v = random.uniform(1e6, 6e6)
    rows.append({"trade_date": dt.strftime("%Y-%m-%d"),
                 "open": round(o, 2), "high": round(h, 2),
                 "low": round(l, 2), "close": round(price, 2),
                 "volume": round(v, 0)})

df = pd.DataFrame(rows)
print("[1] 合成 OHLCV: %d 行, 列=%s" % (len(df), list(df.columns)))

feat, idx, cols = qc.make_stock_features(df, horizon=5, buy_th=0.03, sell_th=-0.03)
print("[2] make_stock_features 返回: feat.shape=%s  date_index=%d  特征列=%d"
      % (feat.shape, len(idx), len(cols)))

must = ["sma_10", "sma_30", "ema_10", "ema_30", "rsi_14",
        "macd", "macd_signal", "macd_hist", "bb_width",
        "close_bb_ratio", "atr_14", "vol_20", "ret_1"]
print("[3] 关键列非空统计:")
bad = []
for c in must:
    if c not in feat.columns:
        print("    %-16s 缺失!" % c)
        bad.append(c)
        continue
    nn = int(feat[c].notna().sum())
    tail = feat[c].dropna()
    sample = round(float(tail.iloc[-1]), 5) if len(tail) else None
    flag = "OK " if nn > 0 else "全NaN!"
    if nn == 0:
        bad.append(c)
    print("    %-16s %s 非空=%3d/%3d  末值=%s" % (c, flag, nn, len(feat), sample))

print("[4] 结论: %s" % ("全部关键列有效" if not bad else ("异常列 -> %s" % bad)))
sys.exit(0 if not bad else 1)
