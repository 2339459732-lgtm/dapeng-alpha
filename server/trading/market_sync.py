"""市场同步（market sync）——A 股交易时段、涨跌停带、市况状态与决策阈值联动。

设计原则
--------
1. **纯函数、无副作用**：不访问网络、不读写数据库，便于单测与复用；
2. **保守缺省**：无法判断时返回 ``neutral`` 与 0 调整，绝不擅自放宽风控；
3. **合规措辞**：只描述"信号参考"，不构成投资建议。

供 ``/api/automation/market-sync`` 与 ``/api/automation/risk/health`` 调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

# A 股交易日时段（不含节假日日历；节假日由上层数据源判断，这里只做时段切分）
_SESSIONS: List[Tuple[time, time, str, str]] = [
    (time(9, 15), time(9, 25), "call_auction", "开盘集合竞价"),
    (time(9, 25), time(9, 30), "pre_open", "集合竞价撮合"),
    (time(9, 30), time(11, 30), "morning", "上午连续竞价"),
    (time(11, 30), time(13, 0), "lunch", "午间休市"),
    (time(13, 0), time(14, 57), "afternoon", "下午连续竞价"),
    (time(14, 57), time(15, 0), "close_auction", "收盘集合竞价"),
]

CN_OFFSET = timedelta(hours=8)


def _cn_now(now: Optional[datetime] = None) -> datetime:
    """当前中国时间（naive）。传入带时区的 datetime 会先转换。"""
    if now is None:
        return datetime.utcnow() + CN_OFFSET
    if now.tzinfo is not None:
        return now.astimezone(tz=None).replace(tzinfo=None)
    return now


def session(now: Optional[datetime] = None) -> Dict[str, Any]:
    """返回当前 A 股时段信息。

    字段：phase/label/is_weekday/is_trading/can_order/can_cancel/now/next。
    ``can_order`` 仅表示"处于可申报时段"，不代表订单一定通过风控。
    """
    dt = _cn_now(now)
    is_weekday = dt.weekday() < 5
    t = dt.time()
    phase, label = "closed", "非交易时段"
    for start, end, key, text in _SESSIONS:
        if start <= t < end:
            phase, label = key, text
            break
    can_order = is_weekday and phase in {"call_auction", "morning", "afternoon", "close_auction"}
    can_cancel = is_weekday and phase in {"call_auction", "pre_open", "morning", "afternoon", "close_auction"}
    # 下一个时段边界（用于"距开盘/收盘"提示）
    nxt = None
    for start, end, key, text in _SESSIONS:
        if t < start:
            nxt = {"at": datetime.combine(dt.date(), start).isoformat(timespec="minutes"), "label": text}
            break
    if nxt is None and not is_weekday:
        nxt = None
    return {
        "phase": phase,
        "label": label,
        "is_weekday": is_weekday,
        "is_trading": can_order,
        "can_order": can_order,
        "can_cancel": can_cancel,
        "now": dt.isoformat(timespec="seconds"),
        "next": nxt,
        "note": "交易时段仅代表可申报；成交仍以风控与撮合结果为准。",
    }


def board_ratio(code: str, name: str = "") -> float:
    """按板块返回涨跌停比例。

    - 科创板（sh688）/ 创业板（sz300、sz301）：20%
    - 北交所（bj8xx、bj4xx、bj920）：30%
    - ST / *ST（主板）：5%
    - 其余主板：10%
    """
    c = (code or "").lower().strip()
    nm = (name or "").upper().replace(" ", "")
    if c.startswith(("sh688", "sz300", "sz301")):
        return 0.20
    if c.startswith(("bj8", "bj4", "bj920")):
        return 0.30
    if "ST" in nm:
        return 0.05
    return 0.10


def price_band(prev_close: float, code: str, name: str = "") -> Dict[str, Any]:
    """计算涨停/跌停参考价（四舍五入到 0.01）。"""
    try:
        prev = float(prev_close)
    except (TypeError, ValueError):
        return {"available": False, "reason": "昨收价不可用"}
    if prev <= 0:
        return {"available": False, "reason": "昨收价非正"}
    ratio = board_ratio(code, name)
    up = round(prev * (1 + ratio), 2)
    down = round(prev * (1 - ratio), 2)
    return {
        "available": True,
        "prev_close": round(prev, 3),
        "ratio": ratio,
        "limit_up": up,
        "limit_down": down,
        "band_pct": round(ratio * 100, 2),
    }


REGIME_LABEL = {"risk_on": "偏强", "neutral": "中性", "risk_off": "偏弱"}


def regime(index_pct: Optional[float] = None, breadth: Optional[float] = None) -> Dict[str, Any]:
    """由指数涨跌幅与上涨家数占比推断市况，并给出决策阈值调整。

    - ``risk_off``：指数 ≤ -1.5% 或 宽度 < 0.30 → 阈值 +8（更严格）
    - ``risk_on`` ：指数 ≥ +1.0% 且 宽度 > 0.60 → 阈值 -5（更积极）
    - 其它：neutral，不调整
    """
    ip = None if index_pct is None else float(index_pct)
    bd = None if breadth is None else float(breadth)
    state = "neutral"
    if ip is not None:
        if ip <= -1.5:
            state = "risk_off"
        elif ip >= 1.0 and (bd is None or bd > 0.6):
            state = "risk_on"
    if bd is not None and bd < 0.30:
        state = "risk_off"
    delta = {"risk_off": 8, "neutral": 0, "risk_on": -5}[state]
    return {
        "state": state,
        "label": REGIME_LABEL[state],
        "index_pct": ip,
        "breadth": bd,
        "threshold_delta": delta,
        "reason": (
            "指数走弱/市场宽度不足，建议提高信号阈值、降低仓位"
            if state == "risk_off"
            else "指数与宽度偏强，可适度放宽阈值（仍以风控为准）"
            if state == "risk_on"
            else "市况中性，维持既有阈值"
        ),
    }


def adjust_threshold(base: float, rg: Dict[str, Any]) -> Dict[str, Any]:
    """把市况调整叠加到基础阈值上，限定在 [10, 98]。"""
    try:
        b = float(base)
    except (TypeError, ValueError):
        b = 80.0
    delta = int(rg.get("threshold_delta") or 0)
    return {"base": b, "delta": delta, "effective": max(10.0, min(98.0, b + delta))}


def decision_context(
    config_threshold: float = 80.0,
    index_pct: Optional[float] = None,
    breadth: Optional[float] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """决策上下文：时段 + 市况 + 阈值，供前端/接口展示。"""
    sess = session(now)
    rg = regime(index_pct, breadth)
    th = adjust_threshold(config_threshold, rg)
    return {
        "session": sess,
        "regime": rg,
        "threshold": th,
        "usable": bool(sess["can_order"]),
        "note": "阈值调整只影响信号筛选参考，不改变风控硬约束。",
    }


@dataclass
class Check:
    """风控体检单条结果。"""

    key: str
    label: str
    level: str  # ok | warn | fail | info
    passed: bool
    value: str = ""
    detail: str = ""
    fix: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "level": self.level,
            "pass": self.passed,
            "value": self.value,
            "detail": self.detail,
            "fix": self.fix,
            **({"extra": self.extra} if self.extra else {}),
        }


def summarize_checks(checks: List[Check]) -> Dict[str, Any]:
    """汇总体检结论：fail → blocked，warn → warn，否则 ok。score 为通过权重。"""
    fails = [c for c in checks if c.level == "fail"]
    warns = [c for c in checks if c.level == "warn"]
    total = len([c for c in checks if c.level in {"ok", "warn", "fail"}]) or 1
    passed = len([c for c in checks if c.passed])
    verdict = "blocked" if fails else ("warn" if warns else "ok")
    return {
        "verdict": verdict,
        "score": round(passed / total * 100),
        "fail_count": len(fails),
        "warn_count": len(warns),
        "checks": [c.as_dict() for c in checks],
        "issues": [c.as_dict() for c in checks if c.level in {"warn", "fail"}],
    }
