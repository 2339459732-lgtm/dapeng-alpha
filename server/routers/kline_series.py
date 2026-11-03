"""K 线与分时：日/周/月线、实时报价、分时逐笔"""

import asyncio
import copy
import json as _json
import logging
import time
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Query
from server import indicators as indicators_mod
from server.cache import cache
from server.config import CACHE_TTL_KLINE, KLINE_LIMIT, find_stock_name
from server.strategies import (
    compute_atr, compute_kdj, compute_ma, compute_ma_cross,
    compute_macd, compute_rsi, compute_strategy,
)
from server.tencent_kline import fetch_tencent_kline
from server.eastmoney import fetch_kline_eastmoney
from server.westock import westock
from server.quote_hub import quote_hub
from server.routers.stream import _bar_ts, _build_quote, _market_open, _norm_period
from server.symbols import MINUTE_PERIODS

from server.routers.kline_common import (
    KLINE_LIMIT_MAX,
    KLINE_LIMIT_MIN,
    _build_kline_response,
    _empty_kline_response,
    _ttl_for_period,
    logger,
)

router = APIRouter()


@router.get("/kline")
async def get_kline(
    code: str = Query(..., description="股票代码，如 sh600703"),
    period: Optional[str] = Query("day", description="周期: day/week/month/60/30/15/5"),
    limit: Optional[int] = Query(
        None,
        ge=KLINE_LIMIT_MIN,
        le=KLINE_LIMIT_MAX,
        description="返回K线根数（30-800）。不传则用服务端默认 KLINE_LIMIT；超出范围返回 422。",
    ),
    fresh: Optional[bool] = Query(
        False, description="true=绕过缓存直取最新（实时对接用，分钟级默认已是短缓存）",
    ),
):
    """
    获取 K 线数据 + MA/MACD/策略指标。

    v4.5 优化：数据源优先级
    1. 内存缓存
    2. 腾讯HTTP K线 API（毫秒级，最稳定）
    3. westock CLI（Node.js 子进程，Windows备用）

    limit：可选，30–800；不传沿用 config.KLINE_LIMIT（默认行为不变）。
    响应内 bars/limit 标注「实际返回根数」与「生效上限」，不做静默截断。
    """
    effective_limit = KLINE_LIMIT if limit is None else int(limit)
    # 缓存键包含 limit，避免「120 根的响应」被「请求 250 根」的调用命中
    cache_key = f"kline:{code}:{period}:{effective_limit}"
    # 仅默认长度复用跨模块共享的原始K线缓存(raw_kline，供 quotes 预取复用)；
    # 自定义长度独立成链，避免把不同长度的数据互相污染
    raw_key = f"raw_kline:{code}:{period}"
    use_shared_raw = (effective_limit == KLINE_LIMIT)
    # 分钟级周期用短缓存（盘中实时对接），日/周/月用长缓存
    ttl = _ttl_for_period(period or "day")

    # 使用 get_or_fetch 防止缓存击穿
    async def _fetch():
        try:
            tdx_used = False
            # 1. 优先复用 quotes 缓存的原始 K 线数据（仅默认长度）
            klines = cache.get(raw_key) if use_shared_raw else None
            logger.debug("kline cache hit for %s: %s (shared=%s)", raw_key, bool(klines), use_shared_raw)

            # 2. 缓存没有，优先腾讯HTTP代理（毫秒级，高可用）
            if not klines:
                logger.info("Fetching kline from Tencent HTTP: %s %s limit=%d", code, period, effective_limit)
                try:
                    klines = await fetch_tencent_kline(code, period=period, limit=effective_limit, timeout=4.0)
                    if klines and use_shared_raw:
                        cache.set(raw_key, klines, ttl)
                    if klines:
                        logger.debug("kline from Tencent HTTP: %s %s (%d bars)", code, period, len(klines))
                except Exception as e:
                    logger.debug("Tencent HTTP kline exception for %s: %s", code, str(e))

            # 3. 腾讯HTTP失败，fallback 到 东方财富 HTTP
            if not klines:
                logger.info("Tencent HTTP kline failed for %s, trying Eastmoney HTTP fallback", code)
                try:
                    klines = await fetch_kline_eastmoney(code, period=period, limit=effective_limit, timeout=3.0)
                except Exception as e:
                    logger.debug("Eastmoney HTTP fetch exception for %s: %s", code, str(e))
                    klines = None
                if klines:
                    if use_shared_raw:
                        cache.set(raw_key, klines, ttl)
                    logger.debug("kline from Eastmoney HTTP: %s %s (%d bars)", code, period, len(klines))

            # 4. 全部HTTP失败，fallback 到 CLI
            if not klines:
                logger.info("HTTP kline failed for %s, trying CLI fallback", code)
                try:
                    klines = await westock.kline(code, period=period)
                    if klines:
                        if use_shared_raw:
                            cache.set(raw_key, klines, ttl)
                        logger.info("kline from CLI: %s %s (%d bars)", code, period, len(klines))
                except Exception as e:
                    logger.error("CLI kline exception for %s: %s", code, str(e))

            # 5. 在线源全部失败，最后尝试通达信本地 vipdoc（离线兜底）
            if not klines:
                try:
                    from server import tdx_local
                    tdx_period = "day" if (period or "day") in ("day", "d", "daily") else ("1m" if str(period) == "1" else f"{period}m")
                    tdx = await asyncio.to_thread(tdx_local.kline, code, tdx_period, effective_limit)
                    if tdx.get("ok") and tdx.get("bars"):
                        klines = [
                            {
                                "d": b.get("date"),
                                "o": b.get("open"),
                                "c": b.get("close"),
                                "h": b.get("high"),
                                "l": b.get("low"),
                                "v": b.get("volume"),
                                "a": b.get("amount"),
                            }
                            for b in tdx["bars"]
                        ]
                        tdx_used = True
                        logger.info("kline from TDX local vipdoc: %s %s (%d bars)", code, period, len(klines))
                except Exception as e:
                    logger.debug("TDX local kline exception for %s: %s", code, str(e))

            if not klines:
                logger.warning("All kline sources failed for %s %s", code, period)
                return None

            resp = _build_kline_response(code, period, klines)
            if tdx_used:
                resp["source"] = "TDX Local (vipdoc)"
                resp["data_quality"] = "offline_fallback"
            resp["bars"] = len(klines)          # 实际返回根数（不静默截断，显式标注）
            resp["limit"] = effective_limit     # 生效上限
            return resp
        except Exception as e:
            logger.error("kline error for %s: %s", code, str(e))
            import traceback
            logger.error("kline traceback: %s", traceback.format_exc())
            return None

    if fresh:
        # 实时对接：绕过缓存直取最新，并回写缓存供后续命中
        data = await _fetch()
        if data is not None:
            cache.set(cache_key, data, ttl)
    else:
        data = await cache.get_or_fetch(cache_key, _fetch, ttl)

    if data is None:
        logger.warning("kline API returning empty for %s %s", code, period)
        return {"ok": True, "data": _empty_kline_response(code, period, effective_limit)}

    return {"ok": True, "data": data}


@router.get("/kline/quote")
async def get_kline_quote(
    code: str = Query(..., description="股票代码，如 sh600703"),
    period: Optional[str] = Query("day", description="周期: day/week/month/60/30/15/5（决定 bar_ts 对齐粒度）"),
):
    """个股实时快照（K 线实时对接的轮询兜底接口，与 SSE `quotes.tick` 同结构）。

    数据统一经 `quote_hub` 取数：与 SSE quotes 同一份缓存，因此
    「同一时刻两处价格必然一致」，且轮询与推送不会各自放大上游请求。

    字段取不到一律 `null` + `reason`，**绝不用 0 冒充**（0 会被前端当真实价渲染）。
    `bar_ts` 为该报价所属 K 线 bar 的时间戳，前端据此判断「更新最后一根」还是「追加新 bar」。
    """
    c = (code or "").strip().lower()
    if not c:
        return {"ok": False, "data": None, "message": "缺少股票代码"}
    per = _norm_period(period)
    now = datetime.now()
    try:
        quote = await quote_hub.get(c)
    except Exception as exc:  # pragma: no cover - 防御式
        logger.warning("kline/quote fetch failed for %s: %s", c, exc)
        quote = None
    if not quote:
        # 中枢都取不到（尚未成功过任何一次）：仍返回结构完整的降级对象
        quote = _build_quote(c, None, per, now)
        quote["stale"] = True
        quote["stale_age"] = None
    quote = dict(quote)
    quote["bar_ts"] = _bar_ts(now, per)   # 按请求周期对齐（中枢按日线构造）
    return {
        "ok": True,
        "data": {
            **quote,
            "period": per,
            "market_open": _market_open(now),
            "source": "tencent" if quote.get("price") is not None else None,
            "degraded": quote.get("price") is None,
            "reason": quote.get("reason"),
        },
    }


# ================================================================
#  分时（timeshare）—— 当日 1 分钟走势
#  数据源按**实测**优先级（每个源均已 curl 验证，见实现注释）：
#    1) 腾讯 minute/query  : 返回 "HHMM 价格 累计成交量(手) 累计成交额(元)"
#                            → 均价 = 累计额 / (累计量×100)，精确，avg_price_estimated=false
#    2) 腾讯 m1 分钟 K 线  : 逐分钟 close + volume，无逐分钟成交额
#                            → 均价用 Σ(close×vol)/Σvol 推算，avg_price_estimated=true
#    3) 新浪 1 分钟 K 线   : 逐分钟 volume(股) + amount(元)
#                            → 均价 = Σamount / Σvolume，精确，avg_price_estimated=false
#  注：东方财富 klt=1（1 分钟）实测 HTTP 403，**不可用**，故未列入候选。
#  全部失败 → points:[] + degraded:true + reason，**一个点都不造**。
# ================================================================
TIMESHARE_TTL_OPEN = 5.0        # 盘中缓存 5s（低延迟，又不过度打上游）
TIMESHARE_TTL_CLOSED = 60.0     # 非盘中缓存 60s（收盘后当日分时不再变化）
TIMESHARE_LIMIT = 320           # m1 / 新浪拉取根数（全天 240 点 + 余量）
TENCENT_MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
SINA_M1_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData"
    "?symbol={code}&scale=1&ma=no&datalen={limit}"
)
_TIMESHARE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.qq.com/",
    "Accept": "application/json,text/plain,*/*",
}


def _fmt_hhmm(raw: Any) -> Optional[str]:
    """'0930' → '09:30'；非法返回 None。"""
    s = str(raw or "").strip()
    if len(s) != 4 or not s.isdigit():
        return None
    hh, mm = int(s[:2]), int(s[2:])
    if hh > 23 or mm > 59:
        return None
    return f"{hh:02d}:{mm:02d}"


def _timeshare_date(raw: Any) -> str:
    """'20261019' → '2026-10-19'；已带连字符则原样返回。"""
    s = str(raw or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _timeshare_payload(
    code: str,
    points: List[Dict[str, Any]],
    *,
    date: str = "",
    prev_close: Optional[float] = None,
    source: str = "",
    avg_price_estimated: bool = False,
    degraded: bool = False,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """组装统一的分时响应体（字段与规格一致）。"""
    last_t = points[-1]["t"] if points else None
    as_of = f"{date} {last_t}" if (date and last_t) else None
    return {
        "code": code,
        "name": find_stock_name(code),
        "date": date or None,
        "prev_close": prev_close,
        "market_open": _market_open(),
        "as_of": as_of,
        "points": points,
        "source": source or None,
        "degraded": degraded,
        "reason": reason,
        "avg_price_estimated": avg_price_estimated,
    }


def _http_json(url: str, timeout: float = 6.0) -> Optional[Any]:
    """极简同步 HTTP GET（在 to_thread 中调用），返回解析后的 JSON。"""
    try:
        req = urllib.request.Request(url, headers=_TIMESHARE_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gbk", errors="replace")
        return _json.loads(text)
    except Exception as exc:  # noqa: BLE001 —— 取不到即降级，不向上抛
        logger.info("timeshare HTTP 失败 %s: %s", url[:90], str(exc)[:120])
        return None


async def _prev_close_from_hub(code: str) -> Optional[float]:
    """昨收：优先复用报价中枢（不额外打上游）。"""
    try:
        q = await quote_hub.get(code)
        if q and q.get("prev_close"):
            return float(q["prev_close"])
    except Exception:  # noqa: BLE001
        pass
    return None


async def _timeshare_tencent_minute(code: str) -> Optional[Dict[str, Any]]:
    """源 1：腾讯分时（精确均价 = 累计成交额 ÷ 累计成交量）。"""
    data = await asyncio.to_thread(_http_json, TENCENT_MINUTE_URL.format(code=code))
    if not isinstance(data, dict):
        return None
    node = (data.get("data") or {}).get(code) or {}
    inner = node.get("data") or {}
    rows = inner.get("data") or []
    if not isinstance(rows, list) or not rows:
        return None

    prev_close: Optional[float] = None
    qt = (node.get("qt") or {}).get(code)
    if isinstance(qt, list) and len(qt) > 4:
        try:
            prev_close = float(qt[4])
        except (TypeError, ValueError):
            prev_close = None

    points: List[Dict[str, Any]] = []
    prev_vol = 0.0
    prev_amt = 0.0
    for row in rows:
        parts = str(row).split()
        if len(parts) < 4:
            continue
        t = _fmt_hhmm(parts[0])
        if not t:
            continue
        try:
            price = float(parts[1])
            cum_vol = float(parts[2])
            cum_amt = float(parts[3])
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        vol = max(0.0, cum_vol - prev_vol)
        amt = max(0.0, cum_amt - prev_amt)
        prev_vol, prev_amt = cum_vol, cum_amt
        avg = round(cum_amt / (cum_vol * 100.0), 3) if cum_vol > 0 else None
        points.append({
            "t": t,
            "price": round(price, 3),
            "avg_price": avg,
            "volume": round(vol, 1),          # 手
            "amount": round(amt, 2),          # 元
        })

    if not points:
        return None
    return _timeshare_payload(
        code, points,
        date=_timeshare_date(inner.get("date")),
        prev_close=prev_close,
        source="tencent_minute",
        avg_price_estimated=False,
    )


def _bars_to_points(bars: List[Dict[str, Any]], today: str,
                    *, volume_scale: float, amount_scale: float,
                    use_bar_amount: bool) -> List[Dict[str, Any]]:
    """把 1 分钟 K 线转成分时点（含均价推算）。

    Args:
        bars: [{d, c, v, a?}]，d 形如 '202610191130' 或 '2026-10-19 11:30:00'
        today: 今日 'YYYYMMDD'，仅保留当日 bar
        volume_scale: 原始量 → 手（腾讯已是手=1；新浪是股=0.01）
        amount_scale: 原始额 → 元
        use_bar_amount: 是否使用逐 bar 成交额（新浪有，腾讯 m1 无）
    """
    points: List[Dict[str, Any]] = []
    cum_vol = 0.0      # 手
    cum_amt = 0.0      # 元
    for b in bars:
        d = str(b.get("d") or "")
        digits = "".join(ch for ch in d if ch.isdigit())
        if not digits.startswith(today) or len(digits) < 12:
            continue
        t = _fmt_hhmm(digits[8:12])
        if not t:
            continue
        try:
            price = float(b.get("c"))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        try:
            vol = float(b.get("v") or 0) * volume_scale
        except (TypeError, ValueError):
            vol = 0.0
        vol = max(0.0, vol)
        if use_bar_amount:
            try:
                amt = float(b.get("a") or 0) * amount_scale
            except (TypeError, ValueError):
                amt = price * vol * 100.0
        else:
            amt = price * vol * 100.0        # 无成交额字段：按 价×量 估算
        cum_vol += vol
        cum_amt += amt
        avg = round(cum_amt / (cum_vol * 100.0), 3) if cum_vol > 0 else None
        points.append({
            "t": t,
            "price": round(price, 3),
            "avg_price": avg,
            "volume": round(vol, 1),
            "amount": round(amt, 2),
        })
    return points


async def _timeshare_tencent_m1(code: str) -> Optional[Dict[str, Any]]:
    """源 2：腾讯 m1 分钟 K 线（无逐 bar 成交额 → 均价按 价×量 推算）。"""
    bars = await fetch_tencent_kline(code, period="1", limit=TIMESHARE_LIMIT, timeout=6.0)
    if not bars:
        return None
    today = datetime.now().strftime("%Y%m%d")
    points = _bars_to_points(bars, today, volume_scale=1.0, amount_scale=1.0,
                             use_bar_amount=False)
    if not points:
        return None
    return _timeshare_payload(
        code, points,
        date=datetime.now().strftime("%Y-%m-%d"),
        prev_close=await _prev_close_from_hub(code),
        source="kline_1m",
        avg_price_estimated=True,   # 均价由 价×量 推算，如实标注
    )


async def _timeshare_sina_m1(code: str) -> Optional[Dict[str, Any]]:
    """源 3：新浪 1 分钟 K 线（逐 bar 成交量+成交额 → 均价精确）。"""
    url = SINA_M1_URL.format(code=code, limit=TIMESHARE_LIMIT)
    text = await asyncio.to_thread(_http_json_text, url)
    if not text:
        return None
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        rows = _json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(rows, list) or not rows:
        return None
    bars = [{
        "d": r.get("day"),
        "c": r.get("close"),
        "v": r.get("volume"),
        "a": r.get("amount"),
    } for r in rows if isinstance(r, dict)]
    today = datetime.now().strftime("%Y%m%d")
    points = _bars_to_points(bars, today, volume_scale=0.01, amount_scale=1.0,
                             use_bar_amount=True)
    if not points:
        return None
    return _timeshare_payload(
        code, points,
        date=datetime.now().strftime("%Y-%m-%d"),
        prev_close=await _prev_close_from_hub(code),
        source="sina_1m",
        avg_price_estimated=False,
    )


def _http_json_text(url: str, timeout: float = 6.0) -> Optional[str]:
    """取纯文本（新浪返回 JSONP 包裹）。"""
    try:
        req = urllib.request.Request(url, headers=_TIMESHARE_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("gbk", errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.info("timeshare text 失败 %s: %s", url[:90], str(exc)[:120])
        return None


async def _load_timeshare(code: str) -> Dict[str, Any]:
    """按优先级逐个数据源尝试；全部失败则如实降级（points 为空）。"""
    errors: List[str] = []
    for name, loader in (
        ("tencent_minute", _timeshare_tencent_minute),
        ("kline_1m", _timeshare_tencent_m1),
        ("sina_1m", _timeshare_sina_m1),
    ):
        try:
            payload = await loader(code)
        except Exception as exc:  # noqa: BLE001 —— 单源异常不阻断后续源
            logger.warning("timeshare %s 异常 %s: %s", name, code, exc)
            payload = None
        if payload and payload.get("points"):
            return payload
        errors.append(name)

    return _timeshare_payload(
        code, [],
        date=datetime.now().strftime("%Y-%m-%d"),
        prev_close=await _prev_close_from_hub(code),
        source="unavailable",
        degraded=True,
        reason="分时数据源均不可用（已尝试：腾讯分时 / 腾讯1分钟 / 新浪1分钟）",
    )


@router.get("/timeshare")
async def get_timeshare(
    code: str = Query(..., description="股票代码，如 sh600519"),
):
    """当日分时走势（1 分钟粒度）。

    - 盘中缓存 5s、非盘中 60s；
    - 数据源优先级：腾讯分时（精确均价）→ 腾讯 m1（推算均价）→ 新浪 1m（精确均价）；
    - 全部失败时 ``points: []`` + ``degraded: true`` + ``reason``，**不造点**。
    """
    c = (code or "").strip().lower()
    if not c or len(c) < 7 or not c[2:].isdigit() or c[:2] not in ("sh", "sz", "hk"):
        return {"ok": False, "data": None,
                "message": "股票代码格式应为 sh600519 / sz000858 / hk00700"}

    ttl = TIMESHARE_TTL_OPEN if _market_open() else TIMESHARE_TTL_CLOSED
    data = await cache.get_or_fetch(f"timeshare:{c}", lambda: _load_timeshare(c), ttl)
    if data is None:
        data = _timeshare_payload(c, [], degraded=True, reason="分时数据暂不可用")
    return {"ok": True, "data": data}
