"""内置策略5：放量突破（量价齐升买入 / 缩量破位卖出 / 固定止损兜底）

触发条件：
- 买入 strategy_buy：收盘价创 entry_days(20) 日新高（不含当日），且当日成交量
  ≥ vol_mult(1.8) × vol_ma(20) 日均量——突破必须有量能配合，过滤无量假突破；
- 卖出 strategy_sell：收盘价跌破 exit_days(10) 日最低（不含当日）；
- 兜底止损 vol_stop：持仓中收盘价 < 成本 × (1 - stop_pct)（默认 -8%）。

与 n_day_breakout（纯价格海龟）的差异：本策略把成交量作为硬触发条件。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from ..models import Signal
from .base import Strategy, StrategyContext, chg_pct_of


class VolumeBreakoutStrategy(Strategy):
    id = "volume_breakout"
    name = "放量突破"
    default_params = {
        "entry_days": 20, "exit_days": 10,
        "vol_mult": 1.8, "vol_ma": 20,
        "stop_pct": 8.0,       # 固定止损 %；0 = 关闭
    }

    def warmup(self, params: Dict[str, Any]) -> int:
        p = {**self.default_params, **(params or {})}
        return max(int(p["entry_days"]), int(p["exit_days"]), int(p["vol_ma"])) + 2

    def evaluate(
        self, code: str, klines: List[Dict[str, Any]], ctx: StrategyContext
    ) -> Optional[Signal]:
        params = self.merged_params(ctx)
        entry_days, exit_days = int(params["entry_days"]), int(params["exit_days"])
        vol_mult, vol_ma = float(params["vol_mult"]), int(params["vol_ma"])
        stop_pct = float(params["stop_pct"])

        need = max(entry_days, exit_days, vol_ma) + 1
        if not klines or len(klines) < need:
            return None

        closes = np.array([float(k.get("c", 0) or 0) for k in klines])
        vols = np.array([float(k.get("v", 0) or 0) for k in klines])
        price = float(closes[-1])
        if price <= 0:
            return None
        chg_pct = chg_pct_of(klines, ctx)
        name = (ctx.quote or {}).get("name", "") if ctx.quote else ""

        holding = ctx.position is not None and ctx.position.qty > 0

        # ---- 固定止损兜底（持仓中，优先级最高）----
        if holding and stop_pct > 0:
            cost = float(ctx.position.avg_cost or 0)
            if cost > 0 and price < cost * (1 - stop_pct / 100.0):
                return self.make_signal(
                    code, "vol_stop", price, chg_pct,
                    detail={
                        "label": "放量突破止损", "stop_pct": stop_pct,
                        "desc": f"收盘{price:.2f} 跌破成本{cost:.2f} 的 {stop_pct}% 止损线",
                    },
                    name=name, now=ctx.now,
                )

        prev_entry_high = float(np.max(closes[-(entry_days + 1):-1]))
        prev_exit_low = float(np.min(closes[-(exit_days + 1):-1]))

        # ---- 放量新高买入（未持仓）----
        if not holding and price > prev_entry_high:
            avg_vol = float(np.mean(vols[-(vol_ma + 1):-1])) if vol_ma > 0 else 0.0
            cur_vol = float(vols[-1])
            if avg_vol > 0 and cur_vol < vol_mult * avg_vol:
                return None  # 无量突破过滤
            ratio = round(cur_vol / avg_vol, 2) if avg_vol > 0 else None
            return self.make_signal(
                code, "strategy_buy", price, chg_pct,
                detail={
                    "label": "放量突破", "entry_days": entry_days,
                    "vol_ratio": ratio, "vol_mult": vol_mult,
                    "desc": f"收盘{price:.2f}创{entry_days}日新高（前高{prev_entry_high:.2f}），"
                            f"成交量 {ratio}× 均量（≥{vol_mult}×）",
                },
                name=name, now=ctx.now,
            )

        # ---- 破位卖出（持仓中）----
        if holding and price < prev_exit_low:
            return self.make_signal(
                code, "strategy_sell", price, chg_pct,
                detail={
                    "label": "破位卖出", "exit_days": exit_days,
                    "desc": f"收盘{price:.2f}跌破{exit_days}日最低（{prev_exit_low:.2f}）",
                },
                name=name, now=ctx.now,
            )
        return None
