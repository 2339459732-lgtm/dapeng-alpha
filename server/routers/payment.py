"""支付路由 — 微信支付 + 支付宝"""

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from server.payment.service import (
    create_order,
    check_order_status,
    handle_payment_callback,
    PaymentGateway,
)
from server.payment.config import PAYMENT_MODE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payment", tags=["payment"])


class CreateOrderRequest(BaseModel):
    plan: str = Field(..., pattern="^(pro|family)$")
    gateway: str = Field(..., pattern="^(wechat|alipay)$")
    user_name: str = ""
    user_phone: str = ""
    elder_count: int = 1


class SimulatePaymentRequest(BaseModel):
    order_id: str


@router.post("/create")
async def create_payment_order(req: CreateOrderRequest):
    """创建支付订单 → 返回支付参数"""
    try:
        gateway = PaymentGateway(req.gateway)
    except ValueError:
        raise HTTPException(400, f"不支持的支付方式: {req.gateway}")

    result = await create_order(
        plan=req.plan,
        gateway=gateway,
        user_name=req.user_name,
        user_phone=req.user_phone,
        elder_count=req.elder_count,
    )

    if not result.get("success"):
        raise HTTPException(400, result.get("message", "订单创建失败"))

    return {"ok": True, "data": result}


@router.get("/status/{order_id}")
async def get_order_status(order_id: str):
    """轮询订单支付状态"""
    try:
        result = await check_order_status(order_id)
        return {"ok": True, "data": result}
    except Exception as e:
        logger.error("Check order status failed: %s | %s", order_id, e)
        raise HTTPException(500, f"查询订单状态失败: {str(e)}")


@router.post("/simulate-pay")
async def simulate_payment(req: SimulatePaymentRequest):
    """沙箱模式：模拟支付成功（仅开发测试用）"""
    if PAYMENT_MODE != "sandbox":
        raise HTTPException(status_code=403, detail="正式支付模式已禁用模拟支付")
    result = await handle_payment_callback(
        order_id=req.order_id,
        gateway=PaymentGateway.WECHAT,
        gateway_order_id=f"sandbox_sim_{req.order_id}",
    )
    return {"ok": result["success"], "data": result}


# ===== 微信支付回调 =====

@router.post("/wechat/notify")
async def wechat_notify():
    """微信支付异步通知（正式验签接入前拒绝处理，避免伪造支付成功）。"""
    if PAYMENT_MODE != "sandbox":
        raise HTTPException(status_code=501, detail="微信支付回调验签尚未配置，已拒绝处理")
    return {"code": "SUCCESS", "message": "沙箱模式不处理真实回调"}


# ===== 支付宝回调 =====

@router.post("/alipay/notify")
async def alipay_notify():
    """支付宝异步通知（正式验签接入前拒绝处理，避免伪造支付成功）。"""
    if PAYMENT_MODE != "sandbox":
        raise HTTPException(status_code=501, detail="支付宝回调验签尚未配置，已拒绝处理")
    return "success"


@router.get("/alipay/return")
async def alipay_return(
    out_trade_no: str = Query(""),
    total_amount: str = Query(""),
    sandbox: str = Query("0"),
):
    """支付宝同步跳转（用户支付完成后跳回）"""
    if sandbox == "1" and PAYMENT_MODE == "sandbox":
        # 仅由服务端沙箱配置决定，不能信任客户端参数
        result = await handle_payment_callback(
            order_id=out_trade_no,
            gateway=PaymentGateway.ALIPAY,
        )
        if result.get("success"):
            return RedirectResponse(url=f"/payment-result?success=1&code={result.get('family_code','')}")
    return RedirectResponse(url="/payment-result?success=0")
