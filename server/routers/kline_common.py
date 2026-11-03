"""共享层：缓存时长、K 线响应构造与空响应"""

import asyncio
import copy
import json as _json
import logging
import time
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Query
from server import indicators as indicators_mod
from server.cache import cache
from server.config import CACHE_TTL_KLINE, KLINE_LIMIT, find_stock_name
from server.strategies import (
    compute_atr, compute_kdj, compute_ma, compute_ma_cross,
    compute_macd, compute_rsi, compute_strategy,
)
from server.tencent_kline import fetch_tencent_kline
from server.eastmoney import fetch_kline_eastmoney
from server.westock import westock
from server.quote_hub import quote_hub
from server.routers.stream import _bar_ts, _build_quote, _market_open, _norm_period
from server.symbols import MINUTE_PERIODS

logger = logging.getLogger(__name__)
router = APIRouter()

# 分钟内周期缓存极短（盘中实时对接），日线及以上沿用长缓存（收盘后不再变化）
INTRADAY_PERIODS = {"1", "5", "15", "30", "60"}
CACHE_TTL_INTRADAY = 15


def _ttl_for_period(period: str) -> int:
    """按周期选择缓存 TTL：分钟级 15s（实时），日/周/月 600s。"""
    return CACHE_TTL_INTRADAY if _norm_period(period) in INTRADAY_PERIODS else CACHE_TTL_KLINE


def _build_kline_response(code: str, period: str, klines: list) -> dict:
    """根据K线原始数据构建完整响应（含MA/MACD/策略指标与多维依据）"""
    ma_data = compute_ma(klines)
    macd_data = compute_macd(klines)
    ma_cross = compute_ma_cross(klines)
    atr14, stop_loss, position_pct = compute_atr(klines)
    strategy_result = compute_strategy(klines)
    rsi_val = strategy_result.rsi

    signals_list = [
        {"type": s.type, "label": s.label, "score": s.score, "desc": s.desc}
        for s in strategy_result.signals
    ]

    return {
        "code": code,
        "name": find_stock_name(code),
        "period": period,
        "source": "Tencent / Eastmoney Hybrid",
        "klines": klines,
        "ma": ma_data,
        "macd": macd_data,
        "strategy": {
            "ma_cross": ma_cross,
            "atr14": round(atr14, 4),
            "stop_loss": round(stop_loss, 2),
            "position_pct": position_pct,
            "rsi": rsi_val,
            "total_score": strategy_result.total_score,
            "momentum_score": strategy_result.momentum_score,
            "momentum_desc": strategy_result.momentum_desc,
            "breakout_score": strategy_result.breakout_score,
            "breakout_desc": strategy_result.breakout_desc,
            "volume_score": strategy_result.volume_score,
            "volume_desc": strategy_result.volume_desc,
            "trend_score": strategy_result.trend_score,
            "risk_score": strategy_result.risk_score,
            "signals": signals_list,
            "ma_bullish_score": strategy_result.ma_bullish_score,
            "pullback_score": strategy_result.pullback_score,
            "limit_up_score": strategy_result.limit_up_score,
            "bottom_reversal_score": strategy_result.bottom_reversal_score,
            "dragon_head_score": strategy_result.dragon_head_score,
            "momentum_reversal_score": strategy_result.momentum_reversal_score,
            "combined_score": strategy_result.combined_score,
        },
    }


def _empty_kline_response(code: str, period: str, limit: int = KLINE_LIMIT) -> dict:
    """空K线响应

    v5.5：补 ``reason`` 字段。此前上游全失败时返回 ``klines: []`` 但不说
    为什么，前端只能画一个空白面板；港股分钟线是高频触发场景（腾讯不供、
    东财限流时就真的没有），必须让用户看到原因而不是面对空白。
    """
    from server.symbols import minute_kline_caveat

    if str(period or "day") in MINUTE_PERIODS:
        reason = minute_kline_caveat(code) or "该周期K线数据源当前不可用（已尝试：腾讯 / 东财 / 备用CLI）"
    else:
        reason = "K线数据源当前不可用（已尝试：腾讯 / 东财 / 备用CLI）"

    return {
        "code": code,
        "name": find_stock_name(code),
        "period": period,
        "bars": 0,
        "limit": limit,
        "source": "",
        "reason": reason,
        "klines": [],
        "ma": {"ma5": [], "ma10": [], "ma20": []},
        "macd": {"dif": [], "dea": [], "hist": []},
        "strategy": {
            "ma_cross": None,
            "atr14": 0,
            "stop_loss": 0,
            "position_pct": 0,
            "rsi": None,
        },
    }


# K 线返回根数：默认沿用服务端配置；允许 30–800 显式覆盖
# （上限 800：实测上游对更大值不可靠——请求 2000 仅返回 640 根；下限 30：保证 MA/MACD 可算）
KLINE_LIMIT_MIN = 30
KLINE_LIMIT_MAX = 800
