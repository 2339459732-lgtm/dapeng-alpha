"""DAPENG Alpha v4.0 - 腾讯财经实时行情接口
https://qt.gtimg.cn - 毫秒级响应，无需注册
"""

import asyncio
import logging
import re
import urllib.request
from typing import Dict, List, Optional

from server.symbols import numeric_code

logger = logging.getLogger(__name__)

# 腾讯行情URL
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={codes}"

# 字段映射：腾讯~分隔字段 → 标准字段
FIELD_MAP = {
    1: "name",
    2: "code",
    3: "price",        # 当前价
    4: "yclose",       # 昨收
    5: "open",         # 今开
    31: "change",      # 涨跌额
    32: "chg",         # 涨跌幅%
    33: "high",        # 最高
    34: "low",         # 最低
    36: "volume",      # 成交量(手)
    37: "amount",      # 成交额(万元)
    38: "turnover",    # 换手率%
    39: "pe",          # 市盈率
    43: "amplitude",   # 振幅%
    44: "float_mv",    # 流通市值
    45: "total_mv",    # 总市值
}


def _parse_tencent_line(line: str) -> Optional[Dict]:
    """解析腾讯行情单行数据
    格式: v_sh000001="1~上证指数~000001~4068.57~..."
    """
    line = line.strip()
    if not line or not line.startswith('v_'):
        return None
    if '="' not in line:
        return None
    try:
        # 格式: v_sh600519="51~贵州茅台~sh600519~..."
        # 从 =" 后面取数据
        data_start = line.index('="') + 2
        data_str = line[data_start:].rstrip('";')
        fields = data_str.split('~')
        code = fields[2] if len(fields) > 2 else ""
        if not code:
            return None

        result = {"code": code, "name": fields[1] if len(fields) > 1 else code, "source": "tencent", "amount_unit": "wan_cny"}
        for idx, key in FIELD_MAP.items():
            if idx >= len(fields) or not fields[idx]:
                if key == "code" or key == "name":
                    continue  # 已处理
                result[key] = 0.0
                continue
            # code 和 name 保持字符串，其他转 float
            if key in ("code", "name"):
                continue  # 已在上面处理
            try:
                result[key] = float(fields[idx])
            except ValueError:
                result[key] = fields[idx]

        return result
    except Exception as e:
        logger.debug("腾讯行情解析失败: %s", str(e)[:80])
        return None


def _numeric_code(code: str) -> str:
    """去掉市场前缀，返回纯数字代码：sh600519 → 600519，hk00700 → 00700。

    v5.5 起实现统一收敛到 server/symbols.py，此处保留薄包装仅为兼容
    既有调用点（本项目内多处 import 了这个私有名）。新代码请直接
    ``from server.symbols import numeric_code``。
    """
    return numeric_code(code)


def fetch_tencent_quotes_sync(codes: List[str], timeout: float = 5.0) -> List[Dict]:
    """同步获取腾讯实时行情（批量）"""
    if not codes:
        return []

    # 构建请求代码 → 数字代码映射（腾讯返回不带市场前缀）
    #
    # 原实现用 c.lstrip('shsz')：lstrip 是「按字符集逐个剥离」，不是去前缀。
    #   'hk00700' → 剥掉 'h'，'k' 不在集合里停下 → 'k00700'（错）
    #   'bj430047' → 'b' 不在集合里 → 'bj430047'（错）
    # 腾讯实际返回的是 '00700' / '430047'，于是港股与北交所的行情永远匹配不上，
    # 表现为「价格恒为 0 却被计入 valid_quotes」。改为按已知市场前缀精确剥离。
    code_map = {}
    for c in codes:
        numeric = _numeric_code(c)
        code_map[numeric] = c

    url = TENCENT_QUOTE_URL.format(codes=",".join(codes))
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.qq.com/",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # 腾讯返回GBK编码
            raw = resp.read()
            try:
                text = raw.decode("gbk")
            except Exception:
                text = raw.decode("utf-8", errors="replace")

        results = []
        for line in text.strip().split("\n"):
            result = _parse_tencent_line(line)
            if result:
                raw_code = result.get("code", "")
                # 用原始请求代码替换腾讯返回的短代码
                if raw_code in code_map:
                    result["code"] = code_map[raw_code]
                results.append(result)

        return results

    except urllib.error.URLError as e:
        logger.warning("腾讯行情网络错误: %s", str(e)[:100])
        return []
    except TimeoutError:
        logger.warning("腾讯行情超时")
        return []
    except Exception as e:
        logger.warning("腾讯行情获取失败: %s", str(e)[:100])
        return []


async def fetch_tencent_quotes(codes: List[str], timeout: float = 5.0) -> List[Dict]:
    """异步获取腾讯实时行情，保留原始请求代码作为code字段"""
    return await asyncio.to_thread(fetch_tencent_quotes_sync, codes, timeout)


async def fetch_single_quote(code: str) -> Optional[Dict]:
    """获取单只股票实时行情"""
    results = await fetch_tencent_quotes([code])
    return results[0] if results else None
