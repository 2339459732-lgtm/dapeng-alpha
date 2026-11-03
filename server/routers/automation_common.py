"""共享层：常量、校验工具、响应封装与请求模型"""

from __future__ import annotations
import asyncio
import copy
import logging
import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from server.cache import cache
from server.config import CACHE_TTL_KLINE, KLINE_LIMIT
from server.tencent_kline import fetch_tencent_kline
from server.trading import trading_facade
from server.trading.backtest import run_backtest
from server.trading.models import (
    AutomationConfig,
    GateState,
    OrderRequest,
    OrderStatus,
    RiskConfig,
    new_id,
    now_local_iso,
)
from server.trading.risk import CONFIRM_TEXT
from server.trading.strategies import get as get_strategy, has as has_strategy
from server.trading.strategies.panel_adapter import _PASSTHROUGH_TYPES
from server.trading.store import TradingStore
from server.trading import decision_gate
from server.quote_hub import quote_hub
from server import risk_metrics
from server.trading import review_service
from server.routers.risk import (
    DEFAULT_BENCHMARK as RISK_BENCHMARK,
    cached_portfolio_risk,
    norm_benchmark,
    risk_cache_key,
)
from server.contracts.envelope import fail, ok

# trigger.signal_types 合法白名单（e5，与 panel_adapter 实际产出一致）：
# 透传 13 型 + MACD 穿越 2 型 + 评分/异动 2 型；策略内部买卖型（strategy_buy 等）
# 由策略引擎产生，不属于信号类型开关的可配置域。
_VALID_SIGNAL_TYPES = _PASSTHROUGH_TYPES | {"macd_gold", "macd_dead", "score", "chg_spike"}

logger = logging.getLogger(__name__)

router = APIRouter()

_CODE_RE = re.compile(r"^(sh|sz|hk)\d{5,6}$")


# ============================================================
# 工具
# ============================================================
#: 统一信封来自 contracts（contract-envelope.v1）；此处只留本地别名，调用点不变
_ok = ok
_fail = fail


async def _store() -> TradingStore:
    await trading_facade.ensure_ready()
    return trading_facade.store


def _valid_code(code: str) -> bool:
    return bool(isinstance(code, str) and _CODE_RE.match(code))


def _normalize_codes(codes: List[str]) -> List[str]:
    out, seen = [], set()
    for c in codes or []:
        if isinstance(c, str):
            c = c.strip().lower()
            if _valid_code(c) and c not in seen:
                seen.add(c)
                out.append(c)
    return out


async def _strategy_specs() -> List[Dict[str, Any]]:
    store = await _store()
    return await store.kv_get("strategies", default=[]) or []


async def _save_strategy_specs(specs: List[Dict[str, Any]]) -> None:
    store = await _store()
    await store.kv_set("strategies", specs)


# ============================================================
# 请求体
# ============================================================
class ConfigUpdateBody(BaseModel):
    switches: Optional[Dict[str, Any]] = None
    scope: Optional[Dict[str, Any]] = None
    trigger: Optional[Dict[str, Any]] = None
    cooldown_minutes: Optional[int] = None
    quiet: Optional[Dict[str, Any]] = None
    email: Optional[Dict[str, Any]] = None
    risk: Optional[Dict[str, Any]] = None
    strategies: Optional[List[Dict[str, Any]]] = None
    # 决策闸参数（四维权重/置信度阈值/波动目标等，逐项校验合并）
    decision_gate: Optional[Dict[str, Any]] = None
    # 条件单自动过期天数（<=0 表示不过期）
    conditional_expire_days: Optional[int] = None


class LiveSwitchBody(BaseModel):
    on: bool
    confirm_text: str = ""
    strategy_id: str = ""


class BacktestBody(BaseModel):
    strategy_id: str
    code: str
    params: Optional[Dict[str, Any]] = None
    days: int = Field(default=250, ge=60, le=800)


class EnableBody(BaseModel):
    strategy_id: str
    target: str                       # paper / live
    codes: List[str] = Field(default_factory=list)


class PaperConfirmBody(BaseModel):
    strategy_id: str


class TestMailBody(BaseModel):
    to: Optional[str] = None


class ManualOrderBody(BaseModel):
    code: str
    side: str                        # buy / sell
    qty: int = 0                     # 0 = 买入按额度换算 / 卖出全部平仓
    price: float = 0.0               # 参考价（<=0 时取实时现价）
    name: str = ""
    # None = 调用方未表态 → 交由服务端演练上下文（marks.drill_active()）自动判定；
    # True = 显式演练/测试单（一律排除出真实统计与策略晋级降级）。
    is_drill: Optional[bool] = None


class ConditionalOrderBody(BaseModel):
    code: str
    side: str                        # buy / sell
    qty: int = 0
    trigger_type: str                # price / time
    trigger_op: Optional[str] = None       # price 用：>= / <=
    trigger_price: Optional[float] = None  # price 用
    trigger_at: Optional[str] = None       # time 用（本地 ISO YYYY-MM-DDTHH:MM:SS）
    name: str = ""
