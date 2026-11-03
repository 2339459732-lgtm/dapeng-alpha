"""
DAPENG Alpha - 全球资产行情中枢 (V2.0)

V2.0 变更：数据源从被阻断的东财 push2 切换到**实测可用**的
腾讯行情(qt.gtimg.cn) + 新浪行情(hq.sinajs.cn)，多源容灾。

各符号均经实测确认（2026-10-14）：
  腾讯：usDJI/usIXIC/usNDX/usINX/usVIX、hkHSI/hkHSTECH ✅
  新浪：gb_$dji/gb_ixic/gb_$inx/gb_$sox、int_nikkei/int_ftse/int_hangseng、
        hf_GC/hf_SI/hf_CL/hf_OIL/hf_CHA50CFD、fx_susdcnh、DINIW ✅
  取不到的（DAX / 纳指期货 / 美债10Y）显式 available:false，绝不虚构。
缓存 60 秒。
"""

import asyncio
import logging
import re
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
from server.core.numbers import to_float as _safe_float

logger = logging.getLogger(__name__)

_SINA_URL = "https://hq.sinajs.cn/list={codes}"
_SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn/",
}
_TENCENT_URL = "https://qt.gtimg.cn/q={codes}"
_TENCENT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.qq.com/",
}
# 直连 opener（绕过环境代理）：实测腾讯/新浪直连可达
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_CACHE_TTL = 60
_cache: Dict[str, Any] = {"ts": 0.0, "data": None}

# 资产注册表：category → [asset]
# 每个 asset：key/name/unit + tencent 或 sina 符号（二者其一或both）。
# 无符号的标的（dax/nq_fut/ust10y）保留占位并标记 unavailable。
GLOBAL_ASSETS: Dict[str, List[Dict[str, str]]] = {
    "us": [
        {"key": "djia", "name": "道琼斯", "unit": "点", "tencent": "usDJI", "sina": "gb_$dji"},
        {"key": "ixic", "name": "纳斯达克", "unit": "点", "tencent": "usIXIC", "sina": "gb_ixic"},
        {"key": "ndx", "name": "纳斯达克100", "unit": "点", "tencent": "usNDX"},
        {"key": "spx", "name": "标普500", "unit": "点", "tencent": "usINX", "sina": "gb_$inx"},
        {"key": "vix", "name": "VIX波动率", "unit": "点", "tencent": "usVIX"},
        {"key": "sox", "name": "费城半导体SOX", "unit": "点", "sina": "gb_$sox"},
    ],
    "hk": [
        {"key": "hsi", "name": "恒生指数", "unit": "点", "tencent": "hkHSI", "sina": "int_hangseng"},
        {"key": "hstech", "name": "恒生科技", "unit": "点", "tencent": "hkHSTECH"},
    ],
    "asia": [
        {"key": "n225", "name": "日经225", "unit": "点", "sina": "int_nikkei"},
    ],
    "europe": [
        {"key": "ftse", "name": "英国富时100", "unit": "点", "sina": "int_ftse"},
        {"key": "dax", "name": "德国DAX30", "unit": "点", "sina": "int_dax"},
    ],
    "commodity": [
        {"key": "gold", "name": "COMEX黄金", "unit": "美元/盎司", "sina": "hf_GC"},
        {"key": "silver", "name": "COMEX白银", "unit": "美元/盎司", "sina": "hf_SI"},
        {"key": "wti", "name": "WTI原油", "unit": "美元/桶", "sina": "hf_CL"},
        {"key": "brent", "name": "布伦特原油", "unit": "美元/桶", "sina": "hf_OIL"},
    ],
    "forex": [
        {"key": "dxy", "name": "美元指数DXY", "unit": "点", "sina": "DINIW"},
        {"key": "usdcnh", "name": "美元/离岸人民币", "unit": "汇率", "sina": "fx_susdcnh"},
    ],
    "futures": [
        {"key": "a50", "name": "富时中国A50期货", "unit": "点", "sina": "hf_CHA50CFD"},
        {"key": "nq_fut", "name": "纳指期货", "unit": "点"},
    ],
    "rates": [
        {"key": "ust10y", "name": "美债10年期收益率", "unit": "%"},
    ],
}

CATEGORY_NAMES = {
    "us": "美股", "hk": "港股", "asia": "亚太", "europe": "欧洲",
    "commodity": "大宗商品", "forex": "外汇", "futures": "股指期货", "rates": "利率",
}


def _parse_sina_line(symbol: str, value: str) -> Optional[Tuple[Optional[float], Optional[float], Optional[str]]]:
    """按符号前缀解析新浪行情 → (price, chg_pct, name)；字段基据见返回值注释。

    - gb_* : US 股/指  [0]=名称 [1]=价 [2]=涨跌幅%
    - int_*: 海外指数   [0]=名称 [1]=价 [3]=涨跌幅%
    - hf_* : 外盘期货   [0]=价 [13]=名称 ; 涨跌幅由 [0] 与 [7](昨结) 计算
    - fx_* : 外汇       [8]=价 [10]=涨跌幅% [9]=名称
    - DINIW: 美元指数   [1]=价 [9]=名称 (无可靠涨跌幅 → None)
    """
    fields = value.split(",")
    if symbol.startswith("gb_"):
        if len(fields) < 3:
            return None
        return _safe_float(fields[1]), _safe_float(fields[2]), (fields[0] or None)
    if symbol.startswith("int_"):
        if len(fields) < 4:
            return None
        return _safe_float(fields[1]), _safe_float(fields[3]), (fields[0] or None)
    if symbol.startswith("hf_"):
        if len(fields) < 8:
            return None
        price = _safe_float(fields[0])
        prev = _safe_float(fields[7])
        chg = round((price - prev) / prev * 100.0, 4) if (price and prev) else None
        nm = fields[13] if len(fields) > 13 else None
        return price, chg, nm
    if symbol.startswith("fx_"):
        if len(fields) < 11:
            return None
        return _safe_float(fields[8]), _safe_float(fields[10]), (fields[9] or None)
    if symbol == "DINIW":
        if len(fields) < 10:
            return None
        return _safe_float(fields[1]), None, (fields[9] or None)
    return None


def _fetch_tencent_sync(symbols: List[str], timeout: float = 6.0) -> Dict[str, Tuple[Optional[float], Optional[float], Optional[str]]]:
    """按 `v_<symbol>` 前缀解析腾讯行情 → {symbol: (price, chg_pct, name)}。

    腾讯字段：1=名称 3=当前价 32=涨跌幅%。用请求符号作 key（腾讯返回的短码
    不带 us/hk 前缀，不能直接当 key）。
    """
    out: Dict[str, Tuple[Optional[float], Optional[float], Optional[str]]] = {}
    if not symbols:
        return out
    url = _TENCENT_URL.format(codes=",".join(symbols))
    try:
        req = urllib.request.Request(url, headers=_TENCENT_HEADERS)
        with _DIRECT_OPENER.open(req, timeout=timeout) as resp:
            text = resp.read().decode("gbk", errors="ignore")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tencent global assets fetch failed: %s", str(exc)[:100])
        return out
    for line in text.split("\n"):
        m = re.match(r'v_([^=]+)="(.*)";?\s*$', line.strip())
        if not m:
            continue
        sym, val = m.group(1), m.group(2)
        fields = val.split("~")
        if len(fields) < 33:
            continue
        price = _safe_float(fields[3])
        if price is None or price <= 0:
            continue
        out[sym] = (price, _safe_float(fields[32]), (fields[1] or None))
    return out


def _fetch_sina_sync(symbols: List[str], timeout: float = 6.0) -> Dict[str, Tuple[Optional[float], Optional[float], Optional[str]]]:
    if not symbols:
        return {}
    url = _SINA_URL.format(codes=",".join(symbols))
    try:
        req = urllib.request.Request(url, headers=_SINA_HEADERS)
        with _DIRECT_OPENER.open(req, timeout=timeout) as resp:
            text = resp.read().decode("gbk", errors="ignore")
    except Exception as exc:  # noqa: BLE001
        logger.warning("sina global assets fetch failed: %s", str(exc)[:100])
        return {}
    out: Dict[str, Tuple[Optional[float], Optional[float], Optional[str]]] = {}
    for line in text.split("\n"):
        m = re.match(r'var hq_str_([^=]+)="(.*)";?\s*$', line.strip())
        if not m:
            continue
        sym, val = m.group(1), m.group(2)
        if not val.strip():
            continue
        parsed = _parse_sina_line(sym, val)
        if parsed:
            out[sym] = parsed
    return out


async def get_global_assets(force: bool = False) -> Dict[str, Any]:
    """获取全类别全球资产快照（60s 缓存）；每标的带 source/available/unavailable_reason。"""
    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["ts"] < _CACHE_TTL:
        return _cache["data"]

    tx_codes: List[str] = []
    sina_symbols: List[str] = []
    for assets in GLOBAL_ASSETS.values():
        for a in assets:
            if a.get("tencent"):
                tx_codes.append(a["tencent"])
            if a.get("sina"):
                sina_symbols.append(a["sina"])

    tx_map = await asyncio.to_thread(_fetch_tencent_sync, tx_codes)
    sina_map = await asyncio.to_thread(_fetch_sina_sync, sina_symbols)

    categories: Dict[str, List[Dict[str, Any]]] = {}
    available_count = 0
    total_count = 0
    for cat, assets in GLOBAL_ASSETS.items():
        items = []
        for a in assets:
            total_count += 1
            price = chg = None
            name = a["name"]
            source = None
            # 优先腾讯（稳定直连），再新浪
            tq = tx_map.get(a.get("tencent", ""))
            if tq and tq[0]:
                price, chg, source = tq[0], tq[1], "tencent"
                if tq[2]:
                    name = tq[2]
            else:
                sq = sina_map.get(a.get("sina", ""))
                if sq and sq[0] is not None:
                    price, chg, source = sq[0], sq[1], "sina"
                    if sq[2]:
                        name = sq[2]
            available = price is not None and price > 0
            if available:
                available_count += 1
            items.append({
                "key": a["key"],
                "name": name,
                "code": a.get("tencent") or a.get("sina") or "",
                "symbols": {"tencent": a.get("tencent"), "sina": a.get("sina")},
                "unit": a["unit"],
                "price": price,
                "chg": chg,
                "available": available,
                "source": source,
                "unavailable_reason": None if available else (
                    "no_reachable_symbol" if not (a.get("tencent") or a.get("sina"))
                    else "no_data_from_sources"
                ),
            })
        categories[cat] = items

    payload = {
        "ok": True,
        "update_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "category_names": CATEGORY_NAMES,
        "categories": categories,
        "coverage": {"available": available_count, "total": total_count},
        "source": "tencent_qt+sina_hq",
        "degraded": available_count < total_count,
    }
    _cache["ts"] = now
    _cache["data"] = payload
    return payload

