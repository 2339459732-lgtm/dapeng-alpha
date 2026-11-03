"""实时快照视图模型（quote-view.v1）。

职责：把行情行（上游原始结构）转换成对外统一的 quote 字段。
纯函数、无 IO、无路由依赖，供 SSE 路由与行情中枢共同复用，
避免 `quote_hub ↔ routers.stream` 的循环依赖。

红线：取不到就是 None，绝不用 0 冒充实时价。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


def market_open(now: Optional[datetime] = None) -> bool:
    """A 股交易时段判定（工作日 09:30-11:30 / 13:00-15:00）。

    仅用于「降频与状态标注」，不用于隐藏数据——休市时仍如实推送末次快照并标 market_closed。
    """
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return (570 <= minutes <= 690) or (780 <= minutes <= 900)   # 9:30-11:30 / 13:00-15:00


def bar_ts(now: datetime, period: str) -> str:
    """报价所属 K 线 bar 的时间戳：日/周/月=当日日期；分钟=该周期起点。"""
    if period in ("day", "week", "month"):
        return now.strftime("%Y-%m-%d")
    try:
        n = int(period)
    except (TypeError, ValueError):
        n = 1
    floored = now.replace(minute=(now.minute // n) * n, second=0, microsecond=0)
    return floored.strftime("%Y-%m-%dT%H:%M:%S")


def opt_num(v: Any) -> Optional[float]:
    """行情数值：缺失/0/NaN/±inf 一律 None（红线：绝不用 0 冒充取不到）。"""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return n if n > 0 else None


def build_quote(code: str, row: Optional[Dict[str, Any]], period: str,
                 now: datetime) -> Dict[str, Any]:
    """单只标的实时快照（供 K 线最后一根追加/更新）。取不到字段一律 null。"""
    price = opt_num((row or {}).get("price"))
    return {
        "code": code,
        "name": (row or {}).get("name") or None,
        "price": price,
        "open": opt_num((row or {}).get("open")),
        "high": opt_num((row or {}).get("high")),
        "low": opt_num((row or {}).get("low")),
        "prev_close": opt_num((row or {}).get("yclose")),
        "chg_pct": (lambda v: None if v is None else round(v, 3))(opt_num_any((row or {}).get("chg"))),
        "volume": opt_num((row or {}).get("volume")),        # 手
        "amount": opt_num((row or {}).get("amount")),        # 万元
        "turnover": opt_num((row or {}).get("turnover")),
        "pe": opt_num((row or {}).get("pe")),
        "ts": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "bar_ts": bar_ts(now, period),
        "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
        "market_closed": not market_open(now),
        "valid": price is not None,
        # 取不到时不给 0：由前端显示「实时暂不可用」而非假价格
        "reason": None if price is not None else "无有效实时价（休市、停牌或上游不可达）",
    }


def opt_num_any(v: Any) -> Optional[float]:
    """涨跌幅可为负/零，故单独处理：不可解析即 None，0 是合法值。"""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return n


