"""内置策略4：MACD 趋势（金叉买入 / 死叉卖出 / 固定止损兜底）

触发条件：
- 买入 strategy_buy：DIF 上穿 DEA（金叉）。low_cross_only=1（默认）时仅接受
  DIF 仍处零轴下方的低位金叉——过滤高位钝化假信号；
- 卖出 strategy_sell：DIF 下穿 DEA（死叉）；
- 兜底止损 macd_stop：持仓中收盘价 < 成本 × (1 - stop_pct)（默认 -10%）。

指标复用 server.strategies.compute_macd（EMA 全序列，取末两根判穿越）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from server.strategies import compute_macd

from ..models import Signal
from .base import Strategy, StrategyContext, chg_pct_of


class MacdTrendStrategy(Strategy):
    id = "macd_trend"
    name = "MACD趋势"
    default_params = {
        "fast": 12, "slow": 26, "signal": 9,
        "low_cross_only": 1,   # 1=仅低位金叉（DIF<0）；0=任意金叉
        "stop_pct": 10.0,      # 固定止损 %；0 = 关闭
    }

    def warmup(self, params: Dict[str, Any]) -> int:
        p = {**self.default_params, **(params or {})}
        return int(p["slow"]) + int(p["signal"]) + 2

    def evaluate(
        self, code: str, klines: List[Dict[str, Any]], ctx: StrategyContext
    ) -> Optional[Signal]:
        params = self.merged_params(ctx)
        fast, slow, sig_n = int(params["fast"]), int(params["slow"]), int(params["signal"])
        low_cross_only = int(params["low_cross_only"])
        stop_pct = float(params["stop_pct"])

        if not klines or len(klines) < slow + sig_n + 1:
            return None

        closes = np.array([float(k.get("c", 0) or 0) for k in klines])
        price = float(closes[-1])
        if price <= 0:
            return None
        chg_pct = chg_pct_of(klines, ctx)
        name = (ctx.quote or {}).get("name", "") if ctx.quote else ""

        macd = compute_macd(klines, fast=fast, slow=slow, signal=sig_n)
        dif, dea = macd["dif"], macd["dea"]
        if len(dif) < 2 or len(dea) < 2:
            return None

        holding = ctx.position is not None and ctx.position.qty > 0

        # ---- 固定止损兜底（持仓中，优先级最高）----
        if holding and stop_pct > 0:
            cost = float(ctx.position.avg_cost or 0)
            if cost > 0 and price < cost * (1 - stop_pct / 100.0):
                return self.make_signal(
                    code, "macd_stop", price, chg_pct,
                    detail={
                        "label": "MACD止损", "stop_pct": stop_pct,
                        "desc": f"收盘{price:.2f} 跌破成本{cost:.2f} 的 {stop_pct}% 止损线",
                    },
                    name=name, now=ctx.now,
                )

        prev_diff = dif[-2] - dea[-2]
        curr_diff = dif[-1] - dea[-1]

        # ---- 金叉买入（未持仓；可选仅低位）----
        if not holding and prev_diff <= 0 and curr_diff > 0:
            if low_cross_only and dif[-1] >= 0:
                return None  # 高位金叉钝化过滤
            return self.make_signal(
                code, "strategy_buy", price, chg_pct,
                detail={
                    "label": "MACD金叉", "dif": dif[-1], "dea": dea[-1],
                    "desc": f"DIF({dif[-1]:.3f}) 上穿 DEA({dea[-1]:.3f})"
                            + ("（低位金叉）" if dif[-1] < 0 else ""),
                },
                name=name, now=ctx.now,
            )

        # ---- 死叉卖出（持仓中）----
        if holding and prev_diff >= 0 and curr_diff < 0:
            return self.make_signal(
                code, "strategy_sell", price, chg_pct,
                detail={
                    "label": "MACD死叉", "dif": dif[-1], "dea": dea[-1],
                    "desc": f"DIF({dif[-1]:.3f}) 下穿 DEA({dea[-1]:.3f})",
                },
                name=name, now=ctx.now,
            )
        return None
