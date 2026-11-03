# -*- coding: utf-8 -*-
"""
DAPENG Alpha v5.0 - 东方财富数据源模块（urllib 零依赖）

提供东方财富免费 API 的行情和 K 线数据，作为腾讯数据源的备选方案。

API 端点：
- 实时行情: https://push2.eastmoney.com/api/qt/stock/get
- K线数据:   https://push2his.eastmoney.com/api/qt/stock/kline/get

与 tencent_kline.py / realtime.py 接口风格保持一致。
"""

import asyncio
import json as stdjson
import logging
import time
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ================================================================
#  API 端点
# ================================================================

QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

# 请求头
_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json",
}

# 市场代码前缀映射：内部代码 → 东方财富 secid
# 东方财富 secid 格式：{market}.{code}，market = 0(深圳)/1(上海)/116(港股)/105(美股)
_MARKET_MAP = {
    "sh": "1",
    "sz": "0",
    "hk": "116",
    "us": "105",
}

# K线周期映射
_KLINE_PERIOD_MAP = {
    "day": "101",
    "week": "102",
    "month": "103",
    "60": "60",
    "30": "30",
    "15": "15",
    "5": "5",
}


# ================================================================
#  代码转换
# ================================================================

def _to_secid(code: str) -> str:
    """将内部代码格式转为东方财富 secid。

    示例:
        sh600519  → 1.600519
        sz000001  → 0.000001
        hk00700   → 116.00700
        usAAPL    → 105.AAPL
    """
    if code.startswith("sh"):
        return f"1.{code[2:]}"
    elif code.startswith("sz"):
        return f"0.{code[2:]}"
    elif code.startswith("hk"):
        return f"116.{code[2:]}"
    elif code.startswith("us"):
        return f"105.{code[2:]}"
    return code


def _normalize_code(code: str) -> str:
    """补全代码为标准格式（带市场前缀）。

    示例:
        600519  → sh600519
        000001  → sz000001
        00700   → hk00700
    """
    code = code.strip()
    if code.startswith(("sh", "sz", "hk", "us")):
        return code
    # 6 位数字：沪市 6 开头，深市 0/3 开头
    if len(code) == 6 and code.isdigit():
        if code.startswith("6"):
            return f"sh{code}"
        else:
            return f"sz{code}"
    # 5 位数字：港股
    if len(code) == 5 and code.isdigit():
        return f"hk{code}"
    return code


# ================================================================
#  网络请求
# ================================================================

def _http_get(url: str, timeout: float = 5.0) -> Optional[str]:
    """同步 HTTP GET，返回响应文本。"""
    try:
        req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug("Eastmoney HTTP GET failed: %s | %s", url[:120], str(e))
        return None


async def _fetch_json(url: str, timeout: float = 5.0) -> Optional[dict]:
    """异步获取 JSON 数据。"""
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, _http_get, url, timeout)
    if not text:
        return None
    try:
        return stdjson.loads(text)
    except stdjson.JSONDecodeError as e:
        logger.debug("Eastmoney JSON decode failed: %s", str(e))
        return None


# ================================================================
#  实时行情
# ================================================================

async def fetch_quotes(codes: List[str], timeout: float = 5.0) -> List[Dict[str, Any]]:
    """批量获取实时行情（东方财富）。

    Args:
        codes: 股票代码列表，如 ['sh600519', 'sz000001']
        timeout: 超时秒数

    Returns:
        行情数据列表，每个元素包含 price/name/chg/high/low/open/volume 等字段
    """
    if not codes:
        return []

    secids = [_to_secid(_normalize_code(c)) for c in codes]
    secid_str = ",".join(secids)

    url = (
        f"{QUOTE_URL}?"
        f"secid={secid_str}&"
        f"fields=f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20&"
        f"ut=fa5e0373d7bc4ca6b18a2e60a27f73f6"
    )

    data = await _fetch_json(url, timeout)
    if not data or not isinstance(data, dict):
        return []

    data_section = data.get("data")
    if not isinstance(data_section, dict):
        return []

    result = []
    raw_list = data_section.get("diff", [])
    if raw_list is None:
        raw_list = []
    # 单条返回时可能是 dict
    if isinstance(raw_list, dict):
        raw_list = [raw_list]

    for item in raw_list:
        try:
            result.append({
                "code": item.get("f12", ""),
                "name": item.get("f14", ""),
                "source": "eastmoney",
                "amount_unit": "cny",
                "price": item.get("f2", 0),
                "chg": item.get("f3", 0),       # 涨跌幅%
                "change": item.get("f4", 0),     # 涨跌额
                "high": item.get("f15", 0),
                "low": item.get("f16", 0),
                "open": item.get("f17", 0),
                "yclose": item.get("f18", 0),
                "volume": item.get("f5", 0),     # 成交量(手)
                "amount": item.get("f6", 0),     # 成交额
                "turnover": item.get("f8", 0),   # 换手率%
                "pe": item.get("f9", 0),         # 市盈率
                "total_mv": item.get("f20", 0),  # 总市值
            })
        except Exception:
            continue

    return result


async def fetch_single_quote(code: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """获取单只股票实时行情。"""
    quotes = await fetch_quotes([code], timeout)
    return quotes[0] if quotes else None


# ================================================================
#  K线数据
# ================================================================

async def fetch_kline(
    code: str,
    period: str = "day",
    limit: int = 120,
    timeout: float = 8.0,
) -> List[Dict[str, Any]]:
    """获取 K 线数据（东方财富）。"""
    code = _normalize_code(code)
    secid = _to_secid(code)
    period_key = _KLINE_PERIOD_MAP.get(period, "101")

    url = (
        f"{KLINE_URL}?"
        f"secid={secid}&"
        f"klt={period_key}&"
        f"fqt=1&"                    # 前复权
        f"lmt={limit}&"
        f"fields1=f1,f2,f3,f4,f5,f6&"
        f"fields2=f51,f52,f53,f54,f55,f56,f57&"
        f"ut=fa5e0373d7bc4ca6b18a2e60a27f73f6"
    )

    data = await _fetch_json(url, timeout)
    if not data or "data" not in data:
        return []

    klines_raw = data.get("data", {}).get("klines", [])
    if not klines_raw:
        return []

    result = []
    for line in klines_raw:
        # 格式: "2024-01-15,100.00,102.50,99.00,101.80,123456,1240000.00,1.50"
        parts = line.split(",")
        if len(parts) < 7:
            continue
        try:
            date = parts[0]
            open_price = float(parts[1])
            close_price = float(parts[2])
            high_price = float(parts[3])
            low_price = float(parts[4])
            volume = float(parts[5])
            amount = float(parts[6])
            result.append({
                "d": date, "o": open_price, "c": close_price,
                "h": high_price, "l": low_price, "v": volume,
                "a": amount, "source": "eastmoney",
                # 保留长字段，兼容已有调用方
                "date": date, "open": open_price, "close": close_price,
                "high": high_price, "low": low_price,
                "volume": volume, "amount": amount,
            })
        except (ValueError, IndexError):
            continue

    return result


# 别名兼容
fetch_kline_eastmoney = fetch_kline


async def fetch_quotes_multi(
    codes: List[str],
    timeout: float = 5.0,
    prefer_eastmoney: bool = False,
) -> List[Dict[str, Any]]:
    """多数据源行情获取，主源失败时降级到备选源。

    Args:
        codes: 股票代码列表
        timeout: 单次超时
        prefer_eastmoney: True 表示优先东方财富（默认腾讯优先）

    Returns:
        行情数据列表；若所有源均失败返回空列表
    """
    from server.realtime import fetch_tencent_quotes

    primary_src = "eastmoney" if prefer_eastmoney else "tencent"
    fallback_src = "tencent" if prefer_eastmoney else "eastmoney"

    # 主数据源
    try:
        if primary_src == "tencent":
            results = await fetch_tencent_quotes(codes, timeout)
        else:
            results = await fetch_quotes(codes, timeout)

        if results:
            for item in results:
                item.setdefault("source", primary_src)
                item.setdefault("amount_unit", "cny" if primary_src == "eastmoney" else "wan_cny")
            return results
    except Exception as e:
        logger.info("Primary source %s failed: %s, falling back to %s",
                     primary_src, str(e), fallback_src)

    # 备选降级
    try:
        if fallback_src == "tencent":
            results = await fetch_tencent_quotes(codes, timeout)
        else:
            results = await fetch_quotes(codes, timeout)
        for item in results or []:
            item.setdefault("source", fallback_src)
            item.setdefault("amount_unit", "cny" if fallback_src == "eastmoney" else "wan_cny")
        return results
    except Exception as e:
        logger.warning("Fallback source %s also failed: %s", fallback_src, str(e))

    return []
