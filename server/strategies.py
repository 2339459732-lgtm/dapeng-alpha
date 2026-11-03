"""策略信号与评分（门面：实现见 strategies_scoring / _engine / _patterns）"""

from server.strategies_scoring import (
    Signal,
    StrategyResult,
    _extract_closes,
    _extract_highs,
    _extract_lows,
    _extract_opens,
    _extract_volumes,
    breakout_score,
    compute_atr,
    compute_boll,
    compute_kdj,
    compute_ma_cross,
    compute_max_drawdown,
    compute_rsi,
    compute_var_95,
    compute_volatility,
    logger,
    momentum_score,
    volume_divergence_score,
)
from server.strategies_engine import compute_strategy, generate_buy_recommendation
from server.strategies_patterns import (
    bottom_reversal,
    compute_ma,
    compute_macd,
    compute_macd_detail,
    compute_multi_timeframe,
    compute_volume_detail,
    dragon_head_strategy,
    limit_up_strategy,
    ma_bullish_arrangement,
    momentum_reversal,
    strong_pullback,
)
