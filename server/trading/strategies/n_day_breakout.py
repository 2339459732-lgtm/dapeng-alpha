"""内置策略2：N 日新高突破买入 / M 日低点退出 + 固定止损兜底

架构蓝图 §7.3 n_day_breakout：
- 收盘价创 entry_days(20) 日新高 → strategy_buy
- 跌破 exit_days(10) 日最低 → strategy_sell
- v2 完善：固定止损兜底 fixed_stop_pct(10%)（海龟出场窗口可能让单笔亏损过大，
  成本价固定百分比止损作最后防线）；可选突破量能确认 vol_mult（默认 0=关闭，
  >0 时要求突破当日成交量 ≥ vol_mult×20 日均量，过滤无量假突破）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from ..models import Signal
from .base import Strategy, StrategyContext, chg_pct_of


class NDayBreakoutStrategy(Strategy):
    id = "n_day_breakout"
    name = "N日突破"
    default_params = {"entry_days": 20, "exit_days": 10,
                      "fixed_stop_pct": 10.0, "vol_mult": 0.0}

    def warmup(self, params: Dict[str, Any]) -> int:
        p = {**self.default_params, **(params or {})}
        return max(int(p["entry_days"]), int(p["exit_days"])) + 2

    def evaluate(
        self, code: str, klines: List[Dict[str, Any]], ctx: StrategyContext
    ) -> Optional[Signal]:
        params = self.merged_params(ctx)
        entry_days, exit_days = int(params["entry_days"]), int(params["exit_days"])
        fixed_stop_pct = float(params.get("fixed_stop_pct", 10.0))
        vol_mult = float(params.get("vol_mult", 0.0))

        if not klines or len(klines) < max(entry_days, exit_days) + 1:
            return None

        closes = np.array([float(k.get("c", 0) or 0) for k in klines])
        price = float(closes[-1])
        if price <= 0:
            return None
        chg_pct = chg_pct_of(klines, ctx)
        name = (ctx.quote or {}).get("name", "") if ctx.quote else ""

        holding = ctx.position is not None and ctx.position.qty > 0

        # ---- 固定止损兜底（仅持仓中；海龟出场窗口可能让单笔亏损过大）----
        if holding and fixed_stop_pct > 0:
            cost = float(ctx.position.avg_cost or 0)
            if cost > 0 and price < cost * (1 - fixed_stop_pct / 100.0):
                return self.make_signal(
                    code, "nb_stop", price, chg_pct,
                    detail={
                        "label": "固定止损", "fixed_stop_pct": fixed_stop_pct,
                        "desc": f"收盘{price:.2f} 跌破成本{cost:.2f} 的 {fixed_stop_pct}% 止损线",
                    },
                    name=name, now=ctx.now,
                )

        # 前 N 日（不含当日）区间高低
        prev_entry_high = float(np.max(closes[-(entry_days + 1):-1]))
        prev_exit_low = float(np.min(closes[-(exit_days + 1):-1]))

        if price > prev_entry_high:
            # v2：可选突破量能确认（vol_mult>0 时要求放量突破）
            if vol_mult > 0:
                vols = np.array([float(k.get("v", 0) or 0) for k in klines])
                if len(vols) >= 21:
                    avg_vol = float(np.mean(vols[-21:-1]))
                    if avg_vol > 0 and float(vols[-1]) < vol_mult * avg_vol:
                        return None  # 无量突破过滤
            return self.make_signal(
                code, "strategy_buy", price, chg_pct,
                detail={
                    "label": "新高突破", "entry_days": entry_days,
                    "desc": f"收盘{price:.2f}创{entry_days}日新高（前高{prev_entry_high:.2f}）",
                },
                name=name, now=ctx.now,
            )
        if price < prev_exit_low:
            return self.make_signal(
                code, "strategy_sell", price, chg_pct,
                detail={
                    "label": "低点退出", "exit_days": exit_days,
                    "desc": f"收盘{price:.2f}跌破{exit_days}日最低（{prev_exit_low:.2f}）",
                },
                name=name, now=ctx.now,
            )
        return None
