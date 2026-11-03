"""内置策略1：MA 金叉买入/死叉卖出 + ATR 移动止损 + 固定止损兜底

架构蓝图 §7.3 ma_cross_atr：
- MA(fast=5) 上穿 MA(slow=20) → strategy_buy；下穿 → strategy_sell
- 持仓中收盘价 < 最高收盘 − atr_mult(2.0)×ATR(14) → atr_stop（移动止损，卖出）
- v2 完善：固定止损兜底 fixed_stop_pct(10%)（ATR 止损在低波动股可能过宽，
  成本价固定百分比止损作最后防线）；可选金叉量能确认 vol_confirm（默认 0=关闭，
  >0 时要求金叉当日成交量 ≥ vol_confirm×20 日均量，过滤无量假金叉）
指标只读复用 server.strategies 的 compute_ma / compute_atr 纯函数。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from server.strategies import compute_atr, compute_ma

from ..models import Signal
from .base import Strategy, StrategyContext, chg_pct_of

# 移动止损"最高收盘"回望窗口（根），避免全历史极值导致止损过宽
_TRAILING_PEAK_LOOKBACK = 60


class MaCrossAtrStrategy(Strategy):
    id = "ma_cross_atr"
    name = "均线交叉+ATR止损"
    default_params = {"fast": 5, "slow": 20, "atr_period": 14, "atr_mult": 2.0,
                      "fixed_stop_pct": 10.0, "vol_confirm": 0.0}

    def warmup(self, params: Dict[str, Any]) -> int:
        """回测预热长度：慢线 + ATR 周期 + 2"""
        p = {**self.default_params, **(params or {})}
        return int(p["slow"]) + int(p["atr_period"]) + 2

    def evaluate(
        self, code: str, klines: List[Dict[str, Any]], ctx: StrategyContext
    ) -> Optional[Signal]:
        params = self.merged_params(ctx)
        fast, slow = int(params["fast"]), int(params["slow"])
        atr_period, atr_mult = int(params["atr_period"]), float(params["atr_mult"])
        fixed_stop_pct = float(params.get("fixed_stop_pct", 10.0))
        vol_confirm = float(params.get("vol_confirm", 0.0))

        if not klines or len(klines) < slow + 2:
            return None

        ma = compute_ma(klines, [fast, slow])
        ma_fast, ma_slow = ma[f"ma{fast}"], ma[f"ma{slow}"]
        if len(ma_fast) < 2 or len(ma_slow) < 2:
            return None
        # compute_ma 前 period-1 根为 0.0 占位，需排除
        if ma_fast[-2] <= 0 or ma_slow[-2] <= 0 or ma_fast[-1] <= 0 or ma_slow[-1] <= 0:
            return None

        closes = np.array([float(k.get("c", 0) or 0) for k in klines])
        price = float(closes[-1])
        chg_pct = chg_pct_of(klines, ctx)
        name = (ctx.quote or {}).get("name", "") if ctx.quote else ""

        # ---- MA 交叉（最后一根 bar 上发生的穿越）----
        prev_diff = ma_fast[-2] - ma_slow[-2]
        curr_diff = ma_fast[-1] - ma_slow[-1]
        if prev_diff <= 0 and curr_diff > 0:
            # v2：可选金叉量能确认（vol_confirm>0 时要求放量金叉）
            if vol_confirm > 0:
                vols = np.array([float(k.get("v", 0) or 0) for k in klines])
                if len(vols) >= 21:
                    avg_vol = float(np.mean(vols[-21:-1]))
                    if avg_vol > 0 and float(vols[-1]) < vol_confirm * avg_vol:
                        return None  # 无量金叉过滤
            return self.make_signal(
                code, "strategy_buy", price, chg_pct,
                detail={
                    "label": "金叉买入", "fast": fast, "slow": slow,
                    "desc": f"MA{fast}上穿MA{slow}（{ma_fast[-1]:.2f}/{ma_slow[-1]:.2f}）",
                },
                name=name, now=ctx.now,
            )
        if prev_diff >= 0 and curr_diff < 0:
            return self.make_signal(
                code, "strategy_sell", price, chg_pct,
                detail={
                    "label": "死叉卖出", "fast": fast, "slow": slow,
                    "desc": f"MA{fast}下穿MA{slow}（{ma_fast[-1]:.2f}/{ma_slow[-1]:.2f}）",
                },
                name=name, now=ctx.now,
            )

        # ---- 固定止损兜底（仅持仓中；ATR 止损在低波动股可能过宽，作最后防线）----
        if ctx.position is not None and ctx.position.qty > 0 and fixed_stop_pct > 0:
            cost = float(ctx.position.avg_cost or 0)
            if cost > 0 and price < cost * (1 - fixed_stop_pct / 100.0):
                return self.make_signal(
                    code, "ma_fixed_stop", price, chg_pct,
                    detail={
                        "label": "固定止损", "fixed_stop_pct": fixed_stop_pct,
                        "desc": f"收盘{price:.2f} 跌破成本{cost:.2f} 的 {fixed_stop_pct}% 止损线",
                    },
                    name=name, now=ctx.now,
                )

        # ---- ATR 移动止损（仅持仓中判定）----
        if ctx.position is not None and ctx.position.qty > 0:
            atr, _stop, _pct = compute_atr(klines, period=atr_period)
            if atr > 0:
                peak = float(np.max(closes[-_TRAILING_PEAK_LOOKBACK:]))
                trailing_stop = peak - atr_mult * atr
                if price < trailing_stop:
                    return self.make_signal(
                        code, "atr_stop", price, chg_pct,
                        detail={
                            "label": "ATR移动止损",
                            "desc": f"收盘{price:.2f} < 最高收盘{peak:.2f} - {atr_mult}×ATR{atr:.2f}（{trailing_stop:.2f}）",
                            "atr": round(atr, 3), "peak": round(peak, 3),
                            "trailing_stop": round(trailing_stop, 3),
                        },
                        name=name, now=ctx.now,
                    )
        return None
