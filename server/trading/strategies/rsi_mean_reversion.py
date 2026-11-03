"""内置策略3：RSI 均值回归（超卖买入 / 超买卖出 / 固定止损兜底）

触发条件：
- 买入 strategy_buy：RSI(period=14) < oversold(30)，且（可选）收盘价站在 trend_ma(60)
  长期均线之上——超卖但趋势未破，博均值回归，避免接下跌飞刀；
- 卖出 strategy_sell：RSI > overbought(70)（仅持仓中判定，超买兑现）；
- 兜底止损 rsi_stop：持仓中收盘价 < 成本 × (1 - stop_pct)（默认 -8%）。

指标复用 server.strategies 的 compute_rsi / compute_ma 纯函数。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from server.strategies import compute_ma, compute_rsi

from ..models import Signal
from .base import Strategy, StrategyContext, chg_pct_of


class RsiMeanReversionStrategy(Strategy):
    id = "rsi_mean_reversion"
    name = "RSI均值回归"
    default_params = {
        "period": 14, "oversold": 30, "overbought": 70,
        "trend_ma": 60,       # 长期趋势过滤；0 = 关闭过滤
        "stop_pct": 8.0,      # 固定止损 %；0 = 关闭
    }

    def warmup(self, params: Dict[str, Any]) -> int:
        p = {**self.default_params, **(params or {})}
        return max(int(p["period"]) + 2, int(p["trend_ma"]) + 2 if int(p["trend_ma"]) > 0 else 0)

    def evaluate(
        self, code: str, klines: List[Dict[str, Any]], ctx: StrategyContext
    ) -> Optional[Signal]:
        params = self.merged_params(ctx)
        period = int(params["period"])
        oversold, overbought = float(params["oversold"]), float(params["overbought"])
        trend_ma = int(params["trend_ma"])
        stop_pct = float(params["stop_pct"])

        need = max(period + 1, trend_ma + 1 if trend_ma > 0 else 0)
        if not klines or len(klines) < need:
            return None

        closes = np.array([float(k.get("c", 0) or 0) for k in klines])
        price = float(closes[-1])
        if price <= 0:
            return None
        chg_pct = chg_pct_of(klines, ctx)
        name = (ctx.quote or {}).get("name", "") if ctx.quote else ""

        rsi = compute_rsi(closes, period=period)
        if rsi is None:
            return None
        # 前一根 RSI（判"上穿超卖线"的回升确认；序列末两位窗口计算）
        rsi_prev = compute_rsi(closes[:-1], period=period) if len(closes) > period + 1 else None

        holding = ctx.position is not None and ctx.position.qty > 0

        # ---- 固定止损兜底（持仓中，优先级最高）----
        if holding and stop_pct > 0:
            cost = float(ctx.position.avg_cost or 0)
            if cost > 0 and price < cost * (1 - stop_pct / 100.0):
                return self.make_signal(
                    code, "rsi_stop", price, chg_pct,
                    detail={
                        "label": "RSI止损", "stop_pct": stop_pct,
                        "desc": f"收盘{price:.2f} 跌破成本{cost:.2f} 的 {stop_pct}% 止损线",
                    },
                    name=name, now=ctx.now,
                )

        # ---- 超买卖出（持仓中）----
        if holding and rsi > overbought:
            return self.make_signal(
                code, "strategy_sell", price, chg_pct,
                detail={
                    "label": "超买卖出", "rsi": rsi, "overbought": overbought,
                    "desc": f"RSI{period}={rsi:.1f} 超过超买线 {overbought}，兑现收益",
                },
                name=name, now=ctx.now,
            )

        # ---- 超卖回升买入（未持仓）：RSI 自下而上穿越超卖线，确认反弹动能 ----
        crossed_up = (rsi_prev is not None and rsi_prev < oversold <= rsi)
        dipped = rsi < oversold  # 仍在超卖区（交叉数据不足时的宽松入口）
        if not holding and (crossed_up or (rsi_prev is None and dipped)):
            if trend_ma > 0:
                ma = compute_ma(klines, [trend_ma])[f"ma{trend_ma}"]
                if len(ma) < 1 or ma[-1] <= 0:
                    return None
                if price < ma[-1] * 0.95:
                    return None  # 深度跌破长期均线（>5%）的超卖不接，防接飞刀
            label = "超卖回升买入" if crossed_up else "超卖买入"
            return self.make_signal(
                code, "strategy_buy", price, chg_pct,
                detail={
                    "label": label, "rsi": rsi, "rsi_prev": rsi_prev, "oversold": oversold,
                    "desc": (f"RSI{period} 由 {rsi_prev:.1f} 回升穿越超卖线 {oversold}（现 {rsi:.1f}）"
                             if crossed_up else f"RSI{period}={rsi:.1f} 低于超卖线 {oversold}")
                            + (f"，价格未深度跌破 MA{trend_ma}" if trend_ma > 0 else ""),
                },
                name=name, now=ctx.now,
            )
        return None
