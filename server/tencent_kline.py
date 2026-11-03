"""DAPENG Alpha v4.6 - 腾讯财经K线HTTP接口（urllib版）

v4.5 → v4.6 变更：
- 移除 httpx，改用 asyncio.to_thread + urllib (稳定性优先)
- 与 realtime.py 保持一致的网络栈

API 端点：
- 日/周K: https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
- 分钟K:  https://ifzq.gtimg.cn/appstock/app/kline/mkline
"""

import asyncio
import json as stdjson
import logging
import urllib.request
from typing import Any, Dict, List, Optional

from server.symbols import is_hk, numeric_code, tencent_supports_minute_kline

logger = logging.getLogger(__name__)

# ================================================================
#  URL 与周期映射
# ================================================================

# 日/周/月K线：前复权 API（升级为高可用代理节点）
FQKLINE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param={code},{period},,,{limit},qfq"

# 分钟K线（60分/30分/15分/5分）：非复权 API（升级为高可用代理节点）
MKLINE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline?param={code},{period},,{limit}"

# 周期映射：内部 → 腾讯参数
FQKLINE_PERIODS = {"day": "day", "week": "week", "month": "month"}
# 1 分钟（m1）用于分时兜底：实测 mkline 支持 m1，返回 [{d:YYYYMMDDHHMM, o,c,h,l,v}]
MKLINE_PERIODS = {"1": "m1", "60": "m60", "30": "m30", "15": "m15", "5": "m5"}

# K线数据key映射（fqkline返回）：周期 → JSON中的key
# 股票返回 qfqday/qfqweek/qfqmonth（前复权），指数返回 day/week/month（裸key）
FQKLINE_KEY = {"day": "qfqday", "week": "qfqweek", "month": "qfqmonth"}
FQKLINE_INDEX_KEY = {"day": "day", "week": "week", "month": "month"}

# 分钟K线key映射
MKLINE_KEY = {"1": "m1", "60": "m60", "30": "m30", "15": "m15", "5": "m5"}

# 请求头
_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.qq.com/",
    "Accept": "application/json",
}


def _is_index(code: str) -> bool:
    """判断是否为指数代码"""
    return code.startswith("sh000") or code.startswith("sh399") or code.startswith("sz399") or code.startswith("sz139")


def _kline_keys(code: str, period: str) -> tuple:
    """返回 (首选 key, 备用 key)。

    腾讯 fqkline 对**股票**返回前复权 key（qfqday/qfqweek/qfqmonth），
    对**指数**与**港股**返回裸 key（day/week/month）——实测港股
    ``hk00700`` 的数据段只有 ``day``，没有 ``qfqday``；港股无复权概念。
    历史上靠「取不到就换另一个 key」的兜底蒙对了，这里显式判定，
    避免港股每次都先白打一次 miss 日志。
    """
    if _is_index(code) or is_hk(code):
        return FQKLINE_INDEX_KEY.get(period), FQKLINE_KEY.get(period)
    return FQKLINE_KEY.get(period), FQKLINE_INDEX_KEY.get(period)


def _http_get_json(url: str, timeout: float = 15.0) -> Optional[dict]:
    """同步HTTP GET请求，返回JSON字典（超时15秒）"""
    try:
        req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return stdjson.loads(raw.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        logger.warning("HTTP %d: %s", e.code, url[:80])
    except urllib.error.URLError as e:
        logger.warning("URL error: %s", str(e)[:80])
    except TimeoutError:
        logger.warning("Timeout (15s): %s", url[:80])
    except stdjson.JSONDecodeError:
        logger.warning("JSON decode error: %s", url[:80])
    except Exception as e:
        logger.warning("HTTP error: %s: %s", url[:80], str(e)[:80])
    return None


def _parse_fqkline(raw: dict, code: str, period: str) -> Optional[List[Dict[str, Any]]]:
    """解析日/周K线JSON响应

    返回格式：[{d:日期, o:开盘, c:收盘, h:最高, l:最低, v:成交量}, ...]
    """
    if raw.get("code") != 0:
        logger.warning("fqkline code != 0 for %s: %s", code, raw.get("code"))
        return None

    data_section = raw.get("data", {})
    if not isinstance(data_section, dict):
        logger.warning("fqkline data段类型异常 %s: %s", code, type(data_section).__name__)
        return None

    # 找到该股票的K线数据
    stock_data = data_section.get(code)
    if stock_data is None:
        # 上游偶尔以纯数字 code 作为 key；用统一的前缀剥离，不用 lstrip
        stock_data = data_section.get(numeric_code(code))
    if stock_data is None:
        logger.warning("fqkline 找不到 %s 的数据段, keys=%s", code, list(data_section.keys())[:5])
        return None

    if not isinstance(stock_data, dict):
        logger.warning("fqkline stock_data 不是 dict: %s", type(stock_data).__name__)
        return None

    # 股票用 qfqday/qfqweek，指数与港股用裸 day/week/month
    kline_key, alt_key = _kline_keys(code, period)

    if not kline_key:
        logger.warning("fqkline 未知周期: %s", period)
        return None

    raw_klines = stock_data.get(kline_key)

    if not raw_klines:
        # 降级尝试另一个key
        if alt_key and alt_key != kline_key:
            raw_klines = stock_data.get(alt_key)

    if not raw_klines or not isinstance(raw_klines, list):
        logger.warning("fqkline %s 无K线数据 (key=%s)", code, kline_key)
        return None

    return _parse_kline_list(raw_klines)


def _parse_mkline(raw: dict, code: str, period: str) -> Optional[List[Dict[str, Any]]]:
    """解析分钟K线JSON响应

    格式：[[datetime, open, close, high, low, volume, {}, amount], ...]
    """
    if raw.get("code") != 0:
        return None

    data_section = raw.get("data", {})
    if not isinstance(data_section, dict):
        return None

    stock_data = data_section.get(code)
    if stock_data is None:
        stock_data = data_section.get(numeric_code(code))
    if stock_data is None:
        return None

    kline_key = MKLINE_KEY.get(period)
    if not kline_key:
        return None

    raw_klines = stock_data.get(kline_key)
    if not raw_klines or not isinstance(raw_klines, list):
        return None

    return _parse_kline_list(raw_klines)


def _parse_kline_list(raw_klines: list) -> List[Dict[str, Any]]:
    """解析K线列表，统一格式

    输入格式：[date, open, close, high, low, volume, ...]
    返回：[{d, o, c, h, l, v}, ...]
    """
    klines = []
    for item in raw_klines:
        try:
            if len(item) < 6:
                continue
            d = str(item[0]).strip()
            if not d:
                continue
            o = float(item[1])
            c = float(item[2])
            h = float(item[3])
            l_val = float(item[4])
            v = float(item[5])
            klines.append({
                "d": d,
                "o": round(o, 4),
                "c": round(c, 4),
                "h": round(h, 4),
                "l": round(l_val, 4),
                "v": v,
            })
        except (ValueError, TypeError, IndexError) as e:
            logger.debug("K线解析跳过: %s: %s", item[:4], e)
            continue

    return klines if klines else None


def _fetch_kline_sync(code: str, period: str, limit: int, timeout: float) -> Optional[List[Dict[str, Any]]]:
    """同步获取K线（在 asyncio.to_thread 中运行）"""
    if period in FQKLINE_PERIODS:
        tencent_period = FQKLINE_PERIODS[period]
        url = FQKLINE_URL.format(code=code, period=tencent_period, limit=limit)
        raw = _http_get_json(url, timeout)
        if raw is None:
            return None
        return _parse_fqkline(raw, code, period)

    elif period in MKLINE_PERIODS:
        # 腾讯 mkline 对港股返回 code=-1（无 data 段）。提前短路，省掉一次
        # 必然失败的网络往返，让路由层直接降级到东财备用源。
        # 注意：这只是「腾讯不支持」，不是「港股没有分钟线」——东财能供。
        if not tencent_supports_minute_kline(code):
            logger.info("mkline 跳过：%s 腾讯不提供港股分钟级K线，转东财备用源", code)
            return None
        tencent_period = MKLINE_PERIODS[period]
        url = MKLINE_URL.format(code=code, period=tencent_period, limit=limit)
        raw = _http_get_json(url, timeout)
        if raw is None:
            return None
        return _parse_mkline(raw, code, period)

    else:
        logger.warning("未知K线周期: %s", period)
        return None


async def fetch_tencent_kline(
    code: str,
    period: str = "day",
    limit: int = 100,
    timeout: float = 8.0,
) -> Optional[List[Dict[str, Any]]]:
    """异步获取腾讯K线数据（urllib + asyncio.to_thread）

    自动选择数据源：
    - day/week/month → fqkline API（前复权，股票 qfq* / 指数裸 key）
    - 60/30/15/5 → mkline API（分钟线，非复权）
    """
    return await asyncio.to_thread(_fetch_kline_sync, code, period, limit, timeout)
