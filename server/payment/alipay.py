"""支付宝支付 — 电脑网站支付 / 扫码支付

支付宝开放平台文档：https://opendocs.alipay.com/open/

流程：
1. 商户后台创建订单 → 支付宝返回支付页面 URL
2. 用户跳转支付宝完成支付（或扫码）
3. 支付宝同步跳回 return_url + 异步通知 notify_url
4. 商户验签确认 → 完成交易
"""

import json
import logging
import time
import uuid
from typing import Optional
from urllib.parse import urlencode

import httpx

from server.payment.config import ALIPAY_CONFIG, PAYMENT_MODE

logger = logging.getLogger(__name__)


class Alipay:
    """支付宝支付"""

    def __init__(self):
        self.config = ALIPAY_CONFIG
        self.mode = PAYMENT_MODE

    def is_available(self) -> bool:
        """检查是否已配置支付宝"""
        if self.mode == "sandbox":
            return True
        return bool(self.config["app_id"] and self.config["private_key_path"])

    async def create_page_pay_order(
        self,
        order_id: str,
        amount_yuan: float,  # 金额（元）
        subject: str,
        body: str = "",
        expire_minutes: int = 30,
    ) -> dict:
        """创建电脑网站支付订单 → 返回支付页面 URL

        用户浏览器跳转到该URL即可完成支付
        """
        if self.mode == "sandbox":
            return await self._sandbox_create(order_id, amount_yuan, subject, body)

        if not self.is_available():
            return {"success": False, "message": "支付宝未配置，请设置商户参数", "order_id": order_id}

        try:
            # 支付宝网关
            url = "https://openapi.alipay.com/gateway.do"

            biz_content = {
                "out_trade_no": order_id,
                "product_code": "FAST_INSTANT_TRADE_PAY",
                "total_amount": f"{amount_yuan:.2f}",
                "subject": subject,
                "body": body or subject,
                "timeout_express": f"{expire_minutes}m",
            }

            params = {
                "app_id": self.config["app_id"],
                "method": "alipay.trade.page.pay",
                "format": "JSON",
                "charset": "utf-8",
                "sign_type": self.config["sign_type"],
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "version": "1.0",
                "notify_url": self.config["notify_url"],
                "return_url": self.config["return_url"],
                "biz_content": json.dumps(biz_content, ensure_ascii=False),
            }

            # 生成签名（简化版，生产需要 RSA256 签名）
            sign = self._build_sign(params)
            params["sign"] = sign

            pay_url = f"{url}?{urlencode(params)}"

            return {
                "success": True,
                "pay_url": pay_url,
                "order_id": order_id,
                "amount_yuan": amount_yuan,
                "message": "支付宝订单创建成功",
            }

        except Exception as e:
            logger.error("Alipay error: %s", e)
            return {"success": False, "message": f"支付宝异常: {str(e)}", "order_id": order_id}

    async def query_order(self, order_id: str) -> dict:
        """查询订单状态"""
        if self.mode == "sandbox":
            return await self._sandbox_query(order_id)

        try:
            url = "https://openapi.alipay.com/gateway.do"
            biz_content = {"out_trade_no": order_id}

            params = {
                "app_id": self.config["app_id"],
                "method": "alipay.trade.query",
                "format": "JSON",
                "charset": "utf-8",
                "sign_type": self.config["sign_type"],
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "version": "1.0",
                "biz_content": json.dumps(biz_content, ensure_ascii=False),
            }

            sign = self._build_sign(params)
            params["sign"] = sign

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, data=params)

            if resp.status_code == 200:
                data = resp.json()
                alipay_resp = data.get("alipay_trade_query_response", {})
                trade_status = alipay_resp.get("trade_status", "")
                return {
                    "success": alipay_resp.get("code") == "10000",
                    "paid": trade_status == "TRADE_SUCCESS",
                    "trade_status": trade_status,
                    "trade_no": alipay_resp.get("trade_no", ""),
                    "order_id": order_id,
                }
            return {"success": False, "paid": False, "order_id": order_id, "trade_status": "UNKNOWN"}

        except Exception as e:
            logger.error("Alipay query error: %s", e)
            return {"success": False, "paid": False, "order_id": order_id, "error": str(e)}

    def verify_notify(self, params: dict) -> bool:
        """验证支付宝异步通知签名"""
        if self.mode == "sandbox":
            return True
        # TODO: 实现 RSA256 签名验证
        return True

    def _build_sign(self, params: dict) -> str:
        """构建签名"""
        if self.mode == "sandbox":
            return "sandbox_sign_" + uuid.uuid4().hex[:8]
        # TODO: 加载商户私钥，对参数字符串做 RSA256 签名
        return "TODO_REAL_SIGN"

    async def _sandbox_create(self, order_id: str, amount_yuan: float, subject: str, body: str) -> dict:
        """沙箱模式：模拟创建订单"""
        import asyncio
        await asyncio.sleep(0.3)

        return {
            "success": True,
            "pay_url": f"{ALIPAY_CONFIG['return_url']}?out_trade_no={order_id}&total_amount={amount_yuan:.2f}&sandbox=1",
            "order_id": order_id,
            "amount_yuan": amount_yuan,
            "message": "[沙箱] 支付宝订单模拟创建成功",
            "sandbox": True,
        }

    async def _sandbox_query(self, order_id: str) -> dict:
        """沙箱模式：查询"""
        return {
            "success": True,
            "paid": True,
            "trade_status": "TRADE_SUCCESS",
            "trade_no": f"sandbox_alipay_{order_id}",
            "order_id": order_id,
            "sandbox": True,
        }


# 单例
alipay_client = Alipay()
