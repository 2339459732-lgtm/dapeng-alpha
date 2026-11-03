"""真实约束校验 — 涨跌停价 / 停牌 / T+1 可卖数量

设计原则（主理人要求）：
- **数据可靠才校验**：优先使用行情源自带的涨停/跌停价（腾讯行情 idx 47/48，
  已含创业板/科创板 20%、ST 5% 等真实规则），拿不到再按昨收 × 涨跌幅上限推算；
  两条路都拿不到 → 返回 unknown，调用方**如实降级为不校验 + warning**，绝不用
  错误规则假装校验。
- **停牌**：以"当日成交量=0 且成交额=0 且现价>0"为判据（成交量为 0 = 全天无成交）。
  无法取得量额数据 → unknown（降级）。
- 本模块独立于 server.realtime，自包含（不改动禁止清单内的文件），避免与其他工程师冲突。
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_TENCENT_URL = "https://qt.gtimg.cn/q={code}"
_CACHE_TTL = 30.0  # 秒；避免高清仓扫描轮重复打行情
_cache: Dict[str, tuple] = {}


def limit_pct(code: str, name: str = "") -> float:
    """按板块/ST 规则给出涨跌幅上限（%）。仅在行情源未提供涨跌停价时作为推算回退。

    - ST / *ST：5%
    - 创业板（sz300/sz301）、科创板（sh688）：20%
    - 北交所（bj 开头）：30%（当前代码体系未接入，留作扩展）
    - 其余主板：10%
    """
    up = (name or "").upper()
    if "ST" in up:
        return 5.0
    if code.startswith(("sz30", "sh688")):
        return 20.0
    if code.startswith("bj"):
        return 30.0
    return 10.0


def _parse_tencent(code: str, text: str) -> Optional[Dict[str, Any]]:
    """解析腾讯原始行情行，返回含涨停/跌停价的快照"""
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line.startswith("v_") or '="' not in line:
            continue
        data = line[line.index('="') + 2:].rstrip('";')
        f = data.split("~")
        if len(f) < 49:
            continue
        try:
            price = float(f[3] or 0)
            yclose = float(f[4] or 0)
            volume = float(f[6] or 0)      # 成交量（手）
            amount = float(f[37] or 0)     # 成交额（万元）
            limit_up = float(f[47] or 0)
            limit_down = float(f[48] or 0)
        except (ValueError, TypeError):
            continue
        name = f[1] if len(f) > 1 else ""
        quote_time = f[30] if len(f) > 30 else ""
        return {
            "code": code, "name": name, "price": price, "yclose": yclose,
            "volume": volume, "amount": amount,
            "limit_up": limit_up, "limit_down": limit_down,
            "quote_time": quote_time, "limit_source": "provider",
        }
    return None


def _fetch_raw_sync(code: str, timeout: float) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(
            _TENCENT_URL.format(code=code),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com/"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        try:
            text = raw.decode("gbk")
        except Exception:
            text = raw.decode("utf-8", errors="replace")
        return _parse_tencent(code, text)
    except Exception as e:  # 网络异常 → 返回 None，由调用方降级
        logger.debug("constraints: 腾讯原始行情获取失败 %s: %s", code, str(e)[:100])
        return None


def _derive_limits(snap: Dict[str, Any]) -> Dict[str, Any]:
    """行情源未带涨跌停价时，按昨收 × 上限推算（标注 limit_source=derived）"""
    yclose = float(snap.get("yclose") or 0)
    if yclose <= 0:
        return snap
    pct = limit_pct(snap.get("code", ""), snap.get("name", ""))
    snap["limit_up"] = round(yclose * (1 + pct / 100.0), 2)
    snap["limit_down"] = round(yclose * (1 - pct / 100.0), 2)
    snap["limit_source"] = "derived"
    return snap


async def fetch_market_snapshot(code: str, timeout: float = 5.0,
                                use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """获取行情快照（含涨跌停价 / 量额）。失败返回 None（调用方降级）。"""
    now = time.monotonic()
    if use_cache:
        hit = _cache.get(code)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    snap = await asyncio.to_thread(_fetch_raw_sync, code, timeout)
    if snap is not None and float(snap.get("limit_up") or 0) <= 0:
        snap = _derive_limits(snap)
    if snap is not None:
        _cache[code] = (now, snap)
    return snap


def is_suspended(snap: Optional[Dict[str, Any]]) -> Optional[bool]:
    """停牌判定：成交量=0 且成交额=0 且现价>0 → True；数据缺失 → None（未知）"""
    if not snap:
        return None
    price = float(snap.get("price") or 0)
    vol = snap.get("volume")
    amt = snap.get("amount")
    if price <= 0 or vol is None or amt is None:
        return None
    return float(vol) <= 0 and float(amt) <= 0


def check_price_limit(side: str, price: float, snap: Optional[Dict[str, Any]]) -> Optional[str]:
    """涨跌停校验。返回拒绝原因字符串；None 表示通过；空串 "" 表示数据不足(降级)。

    - 买入：现价 ≥ 涨停价 → 拒绝（涨停买不进）
    - 卖出：现价 ≤ 跌停价 → 拒绝（跌停卖不出）
    """
    if not snap:
        return ""
    lu = float(snap.get("limit_up") or 0)
    ld = float(snap.get("limit_down") or 0)
    p = float(price or 0)
    tol = 0.001
    if side == "buy":
        if lu <= 0:
            return ""
        if p >= lu - tol:
            return f"涨停价 {lu:.2f}，买单无法成交（现价 {p:.2f}）"
    elif side == "sell":
        if ld <= 0:
            return ""
        if p <= ld + tol:
            return f"跌停价 {ld:.2f}，卖单无法成交（现价 {p:.2f}）"
    return None


def quote_time_is_stale(snap: Optional[Dict[str, Any]]) -> bool:
    """行情时间戳是否陈旧（非当日）——用于停牌辅助提示，非硬拒绝依据"""
    if not snap:
        return False
    qt = str(snap.get("quote_time") or "")
    if len(qt) < 8 or not qt[:8].isdigit():
        return False
    try:
        d = datetime.strptime(qt[:8], "%Y%m%d").date()
    except ValueError:
        return False
    return d != datetime.now().date()
