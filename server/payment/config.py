"""支付配置 — 微信支付 + 支付宝

使用方式：
1. 沙箱模式（默认）— 模拟支付，无需真实商户号，方便开发测试
2. 正式模式 — 填入真实商户参数，切换到生产环境

获取商户参数：
- 微信支付：https://pay.weixin.qq.com → 商户平台 → API安全 → 设置APIv3密钥
- 支付宝：https://open.alipay.com → 开发者中心 → 获取APPID/私钥/公钥
"""

import os
from pathlib import Path

# ===== 沙箱/正式模式 =====
# 设为 "sandbox" 使用模拟支付，"production" 使用真实支付。
# 默认保留 sandbox 仅用于本地开发；生产部署必须显式设置 PAYMENT_MODE=production。
PAYMENT_MODE = os.getenv("PAYMENT_MODE", "sandbox").strip().lower()
if PAYMENT_MODE not in {"sandbox", "production"}:
    PAYMENT_MODE = "sandbox"

# ===== 服务器域名（回调地址） =====
# 部署到线上后改为真实域名
BASE_URL = os.getenv("BASE_URL", "http://localhost:8901").rstrip("/")

# ===== 微信支付配置 =====
WECHAT_CONFIG = {
    # 商户号
    "mchid": os.getenv("WECHAT_MCHID", ""),
    # API v3 密钥（32位）
    "api_v3_key": os.getenv("WECHAT_API_V3_KEY", ""),
    # 商户API证书序列号
    "serial_no": os.getenv("WECHAT_SERIAL_NO", ""),
    # 商户API私钥路径（PEM格式）
    "private_key_path": os.getenv("WECHAT_PRIVATE_KEY_PATH", ""),
    # 应用APPID（公众号/小程序）
    "appid": os.getenv("WECHAT_APPID", ""),
    # 回调地址
    "notify_url": f"{BASE_URL}/api/payment/wechat/notify",
}

# ===== 支付宝配置 =====
ALIPAY_CONFIG = {
    # 应用APPID
    "app_id": os.getenv("ALIPAY_APP_ID", ""),
    # 商户私钥路径（PEM格式）
    "private_key_path": os.getenv("ALIPAY_PRIVATE_KEY_PATH", ""),
    # 支付宝公钥路径
    "alipay_public_key_path": os.getenv("ALIPAY_PUBLIC_KEY_PATH", ""),
    # 回调地址
    "notify_url": f"{BASE_URL}/api/payment/alipay/notify",
    "return_url": f"{BASE_URL}/payment-result",
    # 签名方式
    "sign_type": "RSA2",
}

# ===== 导出统一配置 =====
PAYMENT_CONFIG = {
    "mode": PAYMENT_MODE,
    "base_url": BASE_URL,
    "wechat": WECHAT_CONFIG,
    "alipay": ALIPAY_CONFIG,
}
