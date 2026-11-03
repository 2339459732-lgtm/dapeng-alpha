"""策略引擎子包（Track B）- 策略注册表 register / get / list_enabled

架构蓝图 §7.2：strategies/__init__.py 注册表 register(cls)；
P1+ 新策略放子包即插即用。
"""

from __future__ import annotations

import logging
from typing import Dict, List, Type

from .base import Strategy, StrategyContext

logger = logging.getLogger(__name__)

_REGISTRY: Dict[str, Type[Strategy]] = {}
_INSTANCES: Dict[str, Strategy] = {}


def register(cls: Type[Strategy]) -> Type[Strategy]:
    """注册策略类（可作装饰器）"""
    if not cls.id:
        raise ValueError(f"策略类 {cls.__name__} 缺少 id")
    _REGISTRY[cls.id] = cls
    _INSTANCES.pop(cls.id, None)
    return cls


def get(strategy_id: str) -> Strategy:
    """按 id 取策略实例（单例）"""
    if strategy_id not in _REGISTRY:
        raise KeyError(f"未注册的策略: {strategy_id}")
    if strategy_id not in _INSTANCES:
        _INSTANCES[strategy_id] = _REGISTRY[strategy_id]()
    return _INSTANCES[strategy_id]


def has(strategy_id: str) -> bool:
    return strategy_id in _REGISTRY


def list_all() -> List[Strategy]:
    """全部已注册策略实例"""
    return [get(sid) for sid in sorted(_REGISTRY)]


def list_enabled(specs: List[dict]) -> List[Strategy]:
    """按门禁状态列出"已启用"策略：gate_state ∈ {paper_validating, paper_passed, live_enabled}"""
    active = {"paper_validating", "paper_passed", "live_enabled"}
    out: List[Strategy] = []
    for spec in specs:
        sid = spec.get("strategy_id", "")
        if spec.get("gate_state") in active and sid in _REGISTRY:
            out.append(get(sid))
    return out


# ---- 内置策略注册（Track B）----
from .ma_cross_atr import MaCrossAtrStrategy            # noqa: E402
from .n_day_breakout import NDayBreakoutStrategy        # noqa: E402
from .rsi_mean_reversion import RsiMeanReversionStrategy  # noqa: E402
from .macd_trend import MacdTrendStrategy                # noqa: E402
from .volume_breakout import VolumeBreakoutStrategy      # noqa: E402

register(MaCrossAtrStrategy)
register(NDayBreakoutStrategy)
register(RsiMeanReversionStrategy)
register(MacdTrendStrategy)
register(VolumeBreakoutStrategy)
