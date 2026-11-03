"""策略引擎子包（Track B）- Strategy 抽象基类 + StrategyContext + Signal 输出协议

架构蓝图 §7.1：evaluate 为纯函数式评估——输入日K（含盘中未成型的当日 bar），
输出信号或 None。不得自行拉数据/写库——可单测、可回测复用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..models import Position, Signal, now_local_iso


@dataclass
class StrategyContext:
    """策略评估上下文（§7.1）"""
    quote: Optional[Dict[str, Any]] = None    # 当日实时快照（盘中轮有，收盘轮可无）
    position: Optional[Position] = None       # 当前模拟持仓（供止损/退出判定）
    params: Dict[str, Any] = field(default_factory=dict)  # 该策略实例参数
    now: Optional[datetime] = None            # 评估时刻（回测时为 bar 日期）

    def param(self, key: str, default: Any) -> Any:
        """取参数：实例参数优先，缺省回退默认值"""
        return self.params.get(key, default)


class Strategy(ABC):
    """Track B 策略抽象基类"""

    id: str = ""                # "ma_cross_atr" / "n_day_breakout"
    name: str = ""              # 中文名
    default_params: Dict[str, Any] = {}

    @abstractmethod
    def evaluate(
        self, code: str, klines: List[Dict[str, Any]], ctx: StrategyContext
    ) -> Optional[Signal]:
        """纯函数式评估：输入日K，输出信号或 None"""
        raise NotImplementedError

    # --------------------------------------------------------
    # 工具方法
    # --------------------------------------------------------
    def merged_params(self, ctx: StrategyContext) -> Dict[str, Any]:
        """默认参数 + 实例参数合并"""
        merged = dict(self.default_params)
        merged.update(ctx.params or {})
        return merged

    def make_signal(
        self,
        code: str,
        signal_type: str,
        price: float,
        chg_pct: float = 0.0,
        detail: Optional[Dict[str, Any]] = None,
        name: str = "",
        now: Optional[datetime] = None,
        score: Optional[int] = None,
    ) -> Signal:
        """构造统一 Signal（source 自动带 strategy:<id> 命名空间）"""
        from ..models import strategy_source

        ts = now.strftime("%Y-%m-%dT%H:%M:%S") if now else now_local_iso()
        return Signal(
            code=code,
            name=name or code,
            signal_type=signal_type,
            source=strategy_source(self.id),
            price=round(float(price), 3),
            chg_pct=round(float(chg_pct), 2),
            score=score,
            detail=detail or {},
            ts=ts,
        )


def chg_pct_of(klines: List[Dict[str, Any]], ctx: StrategyContext) -> float:
    """涨跌幅 %：优先实时快照，否则用最近两根收盘推算"""
    if ctx.quote and ctx.quote.get("chg") is not None:
        try:
            return float(ctx.quote["chg"])
        except (TypeError, ValueError):
            pass
    if len(klines) >= 2:
        prev = float(klines[-2].get("c", 0) or 0)
        cur = float(klines[-1].get("c", 0) or 0)
        if prev > 0:
            return (cur - prev) / prev * 100
    return 0.0
