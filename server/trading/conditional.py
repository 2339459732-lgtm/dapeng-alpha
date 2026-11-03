"""条件单（价格触发 / 时间触发）—— 落库 + 轮询触发

- 条件单落 `conditional_orders` 表（status=pending/triggered/canceled/expired）。
- 由 `SignalMonitor` 盘中轮询（conditional.poll）；**触发后仍走同一套风控 9 关 + 决策闸**
  （条件单不是绕过风控的后门），订单标 `source="conditional"`。
- 触发留痕：triggered_ts / triggered_price / result_order_id。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from server.realtime import fetch_tencent_quotes

from .models import OrderRequest, now_local_iso

logger = logging.getLogger(__name__)

# 条件单自动过期：pending 超过该天数仍未触发 → expired（可由 KV "conditional_expire_days" 覆盖）
DEFAULT_EXPIRE_DAYS = 30


def _parse_ts(s: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


async def sweep_expired(store, now: Optional[datetime] = None) -> List[str]:
    """把超过有效期仍 pending 的条件单标记为 expired 并留痕。返回过期 id 列表。"""
    now = now or datetime.now()
    try:
        expire_days = int(await store.kv_get("conditional_expire_days", DEFAULT_EXPIRE_DAYS)
                          or DEFAULT_EXPIRE_DAYS)
    except (TypeError, ValueError):
        expire_days = DEFAULT_EXPIRE_DAYS
    if expire_days <= 0:
        return []
    expired: List[str] = []
    for c in await store.list_conditionals("pending"):
        created = _parse_ts(c.get("ts") or c.get("created_at"))
        if created is None or (now - created).days < expire_days:
            continue
        note = f"超过 {expire_days} 天未触发，自动过期"
        await store.expire_conditional(c["id"], note)
        await store.audit("monitor", "conditional_expired", {
            "conditional_id": c["id"], "code": c.get("code"), "side": c.get("side"),
            "trigger_type": c.get("trigger_type"), "created_ts": c.get("ts"),
            "expire_days": expire_days,
        })
        expired.append(c["id"])
    return expired


def triggered(cond: Dict[str, Any], price: Optional[float], now: datetime) -> Optional[bool]:
    """判断条件是否满足：True/False；数据不足或类型未知 → None。"""
    tt = cond.get("trigger_type")
    if tt == "price":
        tp = cond.get("trigger_price")
        op = cond.get("trigger_op")
        if price is None or tp is None:
            return None
        if op == ">=":
            return price >= float(tp)
        if op == "<=":
            return price <= float(tp)
        return None
    if tt == "time":
        at = cond.get("trigger_at")
        if not at:
            return None
        return now.strftime("%Y-%m-%dT%H:%M:%S") >= str(at)
    return None


def build_order(cond: Dict[str, Any], price: float) -> OrderRequest:
    side = cond.get("side") or "buy"
    return OrderRequest(
        code=cond["code"], name=cond.get("name") or "", side=side, price=float(price or 0),
        qty=int(cond.get("qty") or 0), gateway="paper", source="conditional",
        exit_reason=("conditional" if side == "sell" else None),
    )


async def poll(store, risk, cfg, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """扫描 pending 条件单，满足即触发下单（走风控+决策闸）。返回本轮触发记录。"""
    now = now or datetime.now()
    await sweep_expired(store, now)
    conds = await store.list_conditionals("pending")
    if not conds:
        return []
    codes = sorted({c["code"] for c in conds if c.get("code")})
    quotes = await fetch_tencent_quotes(codes, timeout=5.0)
    qmap = {q.get("code"): q for q in quotes if q and q.get("code")}
    fired: List[Dict[str, Any]] = []
    for c in conds:
        q = qmap.get(c["code"])
        price = float((q or {}).get("price") or 0) if q else None
        try:
            hit = triggered(c, price if (price and price > 0) else None, now)
        except Exception:
            logger.warning("conditional: 条件判定异常 %s", c.get("id"), exc_info=True)
            continue
        if hit is not True:
            continue
        order = build_order(c, price or 0)
        try:
            res = await risk.execute_order(order, cfg)
        except Exception as e:
            logger.error("conditional: 触发下单异常 %s: %s", c.get("id"), e, exc_info=True)
            res = None
        order_id = res.order_id if res is not None else ""
        await store.mark_conditional_triggered(c["id"], order_id, now_local_iso(), price or 0.0)
        await store.audit("monitor", "conditional_triggered", {
            "conditional_id": c["id"], "code": c["code"], "side": c.get("side"),
            "trigger_type": c.get("trigger_type"), "trigger_price": c.get("trigger_price"),
            "trigger_at": c.get("trigger_at"), "price": price,
            "order_id": order_id, "order_status": (res.status if res is not None else "error"),
        })
        fired.append({
            "conditional_id": c["id"], "code": c["code"], "side": c.get("side"),
            "price": price, "order_id": order_id,
            "order_status": (res.status if res is not None else "error"),
        })
    return fired
