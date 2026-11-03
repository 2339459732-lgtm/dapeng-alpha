"""统一支付服务 — 订单管理 + 支付网关调度"""

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from server.config import DB_DIR, PRICING
from server.payment.wechat import wechat_pay
from server.payment.alipay import alipay_client
from server.payment.config import PAYMENT_MODE

logger = logging.getLogger(__name__)

ORDERS_FILE = DB_DIR / "payment_orders.json"


class PaymentGateway(str, Enum):
    WECHAT = "wechat"
    ALIPAY = "alipay"


class OrderStatus(str, Enum):
    PENDING = "pending"       # 待支付
    PAID = "paid"             # 已支付
    EXPIRED = "expired"       # 已过期
    REFUNDED = "refunded"     # 已退款
    CANCELLED = "cancelled"   # 已取消


def _load_orders() -> dict:
    if ORDERS_FILE.exists():
        try:
            return json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_orders(data: dict):
    ORDERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def create_order(
    plan: str,
    gateway: PaymentGateway,
    user_name: str = "",
    user_phone: str = "",
    elder_count: int = 1,
) -> dict:
    """创建支付订单"""
    order_id = f"AFG{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"

    plan_info = PRICING.get(plan, PRICING["pro"])
    amount_fen = plan_info["price_fen"]
    plan_name = "家庭守护" if plan == "family" else "月度体验"

    description = f"防诈骗守护 - {plan_name} - {'年' if plan == 'family' else '月'}付"

    # 创建订单记录
    order = {
        "order_id": order_id,
        "plan": plan,
        "gateway": gateway.value,
        "amount_fen": amount_fen,
        "plan_name": plan_name,
        "description": description,
        "user_name": user_name,
        "user_phone": user_phone,
        "elder_count": elder_count,
        "status": OrderStatus.PENDING.value,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "paid_at": None,
        "gateway_order_id": None,
        "mode": PAYMENT_MODE,
    }

    # 调用支付网关
    if gateway == PaymentGateway.WECHAT:
        result = await wechat_pay.create_native_order(order_id, amount_fen, description)
        if result.get("success"):
            order["code_url"] = result.get("code_url")
            order["sandbox_code"] = result.get("sandbox_code")
        else:
            return {"success": False, "message": result.get("message", "微信支付创建失败"), "order_id": order_id}

    elif gateway == PaymentGateway.ALIPAY:
        amount_yuan = amount_fen / 100.0
        result = await alipay_client.create_page_pay_order(order_id, amount_yuan, description)
        if result.get("success"):
            order["pay_url"] = result.get("pay_url")
        else:
            return {"success": False, "message": result.get("message", "支付宝创建失败"), "order_id": order_id}

    # 保存订单
    orders = _load_orders()
    orders[order_id] = order
    _save_orders(orders)

    logger.info("Order created: %s | %s | %d fen | %s", order_id, gateway.value, amount_fen, plan)

    return {
        "success": True,
        "order_id": order_id,
        "gateway": gateway.value,
        "amount_fen": amount_fen,
        "plan": plan,
        "code_url": order.get("code_url"),      # 微信扫码URL
        "pay_url": order.get("pay_url"),        # 支付宝跳转URL
        "sandbox_code": order.get("sandbox_code"),
        "sandbox": PAYMENT_MODE == "sandbox",
        "message": "订单创建成功，请完成支付",
    }


async def handle_payment_callback(order_id: str, gateway: PaymentGateway, gateway_order_id: str = "") -> dict:
    """处理支付回调 — 更新订单状态 + 创建订阅"""
    orders = _load_orders()
    order = orders.get(order_id)

    if not order:
        logger.warning("Callback for unknown order: %s", order_id)
        return {"success": False, "message": "订单不存在"}

    if order["status"] == OrderStatus.PAID.value:
        return {"success": True, "message": "订单已处理", "order_id": order_id}

    # 验证支付状态
    if gateway == PaymentGateway.WECHAT:
        query = await wechat_pay.query_order(order_id)
    else:
        query = await alipay_client.query_order(order_id)

    if not query.get("paid"):
        return {"success": False, "message": "支付未完成", "order_id": order_id}

    # 更新订单状态
    order["status"] = OrderStatus.PAID.value
    order["paid_at"] = datetime.now(timezone.utc).isoformat()
    order["gateway_order_id"] = gateway_order_id or query.get("transaction_id") or query.get("trade_no", "")
    orders[order_id] = order
    _save_orders(orders)

    # 创建订阅用户
    users_file = DB_DIR / "users.json"
    users = {}
    if users_file.exists():
        try:
            users = json.loads(users_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    user_id = str(uuid.uuid4())[:8]
    family_code = str(uuid.uuid4())[:6].upper()

    users[user_id] = {
        "id": user_id,
        "family_code": family_code,
        "plan": order["plan"],
        "guardian_name": order.get("user_name", ""),
        "guardian_phone": order.get("user_phone", ""),
        "elder_count": order.get("elder_count", 1),
        "price_fen": order["amount_fen"],
        "queries_per_day": PRICING.get(order["plan"], PRICING["pro"])["queries_per_day"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "active",
        "checks_today": 0,
        "checks_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "order_id": order_id,
        "gateway": gateway.value,
    }
    users_file.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("Payment completed: %s | user=%s | family=%s | plan=%s",
                order_id, user_id, family_code, order["plan"])

    return {
        "success": True,
        "message": "支付成功，订阅已激活",
        "order_id": order_id,
        "user_id": user_id,
        "family_code": family_code,
        "plan": order["plan"],
        "share_link": f"/guardian?code={family_code}",
    }


async def check_order_status(order_id: str) -> dict:
    """查询订单支付状态"""
    orders = _load_orders()
    order = orders.get(order_id)

    if not order:
        return {"success": False, "message": "订单不存在"}

    if order["status"] == OrderStatus.PAID.value:
        return {
            "success": True,
            "paid": True,
            "order_id": order_id,
            "message": "已支付",
        }

    # 查询支付网关
    gateway = PaymentGateway(order["gateway"])
    if gateway == PaymentGateway.WECHAT:
        query = await wechat_pay.query_order(order_id)
    else:
        query = await alipay_client.query_order(order_id)

    if query.get("paid"):
        # 自动补单
        return await handle_payment_callback(order_id, gateway, query.get("transaction_id", ""))

    return {
        "success": True,
        "paid": False,
        "order_id": order_id,
        "message": "待支付",
    }
