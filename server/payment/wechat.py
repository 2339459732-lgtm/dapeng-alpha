"""微信支付 Native Pay — 扫码支付

微信支付 API v3 文档：https://pay.weixin.qq.com/wiki/doc/apiv3/

Native Pay 流程：
1. 商户后台创建订单 → 微信支付返回 code_url
2. 商户将 code_url 生成二维码展示给用户
3. 用户扫码支付 → 微信支付异步通知商户后台
4. 商户后台查询订单确认 → 完成交易
"""

import hashlib
import json
import logging
import time
import uuid
from typing import Optional

import httpx

from server.payment.config import WECHAT_CONFIG, PAYMENT_MODE

logger = logging.getLogger(__name__)


class WeChatPay:
    """微信支付 Native 扫码支付"""

    def __init__(self):
        self.config = WECHAT_CONFIG
        self.mode = PAYMENT_MODE

    def is_available(self) -> bool:
        """检查是否已配置微信支付"""
        if self.mode == "sandbox":
            return True
        return bool(self.config["mchid"] and self.config["api_v3_key"])

    async def create_native_order(
        self,
        order_id: str,
        amount_fen: int,  # 金额（分）
        description: str,
        expire_minutes: int = 30,
    ) -> dict:
        """创建 Native Pay 订单 → 返回 code_url 用于生成二维码

        Returns:
            {
                "success": True/False,
                "code_url": "weixin://wxpay/bizpayurl?pr=...",  # 用于生成二维码
                "order_id": "...",
                "amount_fen": ...,
                "message": "...",
            }
        """
        if self.mode == "sandbox":
            return await self._sandbox_create(order_id, amount_fen, description)

        if not self.is_available():
            return {"success": False, "message": "微信支付未配置，请设置商户参数", "order_id": order_id}

        try:
            # 微信支付 API v3 Native 下单
            url = "https://api.mch.weixin.qq.com/v3/pay/transactions/native"
            body = {
                "appid": self.config["appid"],
                "mchid": self.config["mchid"],
                "description": description,
                "out_trade_no": order_id,
                "time_expire": self._get_expire_time(expire_minutes),
                "notify_url": self.config["notify_url"],
                "amount": {"total": amount_fen, "currency": "CNY"},
            }

            headers = self._build_headers("POST", url, body)

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=body, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": True,
                    "code_url": data.get("code_url"),
                    "order_id": order_id,
                    "amount_fen": amount_fen,
                    "message": "订单创建成功",
                }
            else:
                logger.error("WeChat Pay error: %s %s", resp.status_code, resp.text)
                return {"success": False, "message": f"微信支付下单失败: {resp.text[:200]}", "order_id": order_id}

        except Exception as e:
            logger.error("WeChat Pay exception: %s", e)
            return {"success": False, "message": f"微信支付异常: {str(e)}", "order_id": order_id}

    async def query_order(self, order_id: str) -> dict:
        """查询订单状态"""
        if self.mode == "sandbox":
            return await self._sandbox_query(order_id)

        try:
            url = f"https://api.mch.weixin.qq.com/v3/pay/transactions/out-trade-no/{order_id}"
            params = {"mchid": self.config["mchid"]}
            headers = self._build_headers("GET", url, params, method="GET")

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                trade_state = data.get("trade_state", "")
                return {
                    "success": True,
                    "paid": trade_state == "SUCCESS",
                    "trade_state": trade_state,
                    "transaction_id": data.get("transaction_id", ""),
                    "order_id": order_id,
                }
            return {"success": False, "paid": False, "order_id": order_id, "trade_state": "UNKNOWN"}

        except Exception as e:
            logger.error("WeChat query error: %s", e)
            return {"success": False, "paid": False, "order_id": order_id, "error": str(e)}

    def _build_headers(self, method: str, url: str, body, method_override: str = "POST") -> dict:
        """构建微信支付 API v3 签名头"""
        # 完整签名实现需要商户私钥，这里提供框架
        # 生产环境使用 wechatpay-apache-httpclient 或手动实现 RSA 签名
        nonce = uuid.uuid4().hex[:32]
        timestamp = str(int(time.time()))
        # TODO: 实现完整的 RSA256 签名
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"WECHATPAY2-SHA256-RSA2048 mchid=\"{self.config['mchid']}\",nonce_str=\"{nonce}\",timestamp=\"{timestamp}\",serial_no=\"{self.config['serial_no']}\",signature=\"TODO\"",
        }

    async def _sandbox_create(self, order_id: str, amount_fen: int, description: str) -> dict:
        """沙箱模式：模拟创建订单"""
        # 模拟网络延迟
        import asyncio
        await asyncio.sleep(0.3)

        sandbox_code = f"sandbox_wechat_{order_id}"
        return {
            "success": True,
            "code_url": f"weixin://wxpay/bizpayurl?pr={sandbox_code}",
            "order_id": order_id,
            "amount_fen": amount_fen,
            "message": "[沙箱] 微信支付订单模拟创建成功",
            "sandbox": True,
            "sandbox_code": sandbox_code,
        }

    async def _sandbox_query(self, order_id: str) -> dict:
        """沙箱模式：查询（自动标记为已支付）"""
        return {
            "success": True,
            "paid": True,
            "trade_state": "SUCCESS",
            "transaction_id": f"sandbox_txn_{order_id}",
            "order_id": order_id,
            "sandbox": True,
        }

    @staticmethod
    def _get_expire_time(minutes: int) -> str:
        """生成过期时间 RFC3339 格式"""
        from datetime import datetime, timedelta, timezone
        expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        return expire.strftime("%Y-%m-%dT%H:%M:%S+08:00")


# 单例
wechat_pay = WeChatPay()
