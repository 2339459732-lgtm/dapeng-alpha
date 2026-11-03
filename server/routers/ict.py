# -*- coding: utf-8 -*-
"""ICT 模型端点。

第一阶段 `GET /api/ict/scan`：
    {ok, data:{code, period, status, direction, reason, gates[], evidence{...},
               candidate{...}, parameters, score, score_basis, annotations{...}},
     message, as_of, source, degraded, reason}

第二阶段 `GET /api/ict/history`（滚动回测统计，只读）：
    {ok, data:{code, period, candles, scanned_from, scanned_to, params,
               params_signature, events{confirmed,invalidated}, confirmed_events[],
               stats{...}, baseline{...}, verdict{available,note}, warnings[]},
     message, as_of, source, degraded, reason}
    逐日滚动扫描（第 i 次只看 candles[0:i+1]，**严禁未来函数**）→ 事件去重 →
    +1/+3/+5/+10 收益统计 + 同窗口随机持有基准 → 保守结论判定。
    计算细节与口径全部在 ``server/ict_history.py``，本文件只负责取数/缓存/信封。

设计约束：
- **只做展示与统计**：不接入决策闸，不改任何交易链路（不写 orders / audit_log）；
- K 线取数复用 ``raw_kline:{code}:{period}`` 共享缓存（**只读**），**不新开上游源**；
  根数不够时才落到独立键 ``ict_hist_kline:...``，绝不把更长序列写回 ``raw_kline``
  （沿用 ``routers/kline.py`` 的既定语数，避免污染 /api/kline 的默认根数）；
- 全量结果缓存：日线 120s、分钟 30s；回测统计 600s（``cache.get_or_fetch_ex``，复用 T01）；
- 开关关闭 → ``ok:false`` + 明确 reason（**不**静默走默认值）；
- 参数：``DEFAULT_PARAMS`` ← KV ``ict_config`` ← 查询参数，逐层覆盖。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Query

from server import ict_history, ict_model, ict_structure
from server.cache import cache
from server.config import ICT_DEFAULTS, feature_enabled
from server.tencent_kline import fetch_tencent_kline
from server.trading import trading_facade
from server.core.timeutil import now_iso as _now_iso

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ict", tags=["ict"])

ICT_CACHE_TTL_DAILY = 120.0     # 日线扫描结果缓存（秒）
ICT_CACHE_TTL_INTRADAY = 30.0   # 分钟级扫描结果缓存（秒）
ICT_HISTORY_CACHE_TTL = 600.0   # 滚动回测统计缓存（秒）：日 K 收盘后序列不变
ICT_KLINE_CACHE_TTL = 900.0     # ICT 专用 K 线缓存（秒），独立键，不污染 raw_kline
ICT_STRUCTURE_CACHE_TTL = 600.0  # 结构级统计缓存（秒），同上
DAILY_PERIODS = {"day", "week", "month"}
VALID_PERIODS = {"day", "week", "month", "60", "30", "15", "5", "1"}
MAX_LIMIT = 800
MIN_LIMIT = 80                  # 低于该根数直接 ok:false（结构识别无统计意义）
ICT_CONFIG_KV = "ict_config"


def _fail(reason: str, **extra: Any) -> Dict[str, Any]:
    """统一失败信封（ok:false + data:null + reason，绝不返回空结果当成功）。"""
    payload = {
        "ok": False,
        "data": None,
        "message": reason,
        "source": "ict_model",
        "as_of": None,
        "age": None,
        "degraded": True,
        "reason": reason,
    }
    payload.update(extra)
    return payload


async def _load_params(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """参数三层合并：DEFAULT ← KV ``ict_config`` ← 查询参数。"""
    params: Dict[str, Any] = dict(ICT_DEFAULTS)
    try:
        await trading_facade.ensure_ready()
        kv = await trading_facade.store.kv_get(ICT_CONFIG_KV, {}) or {}
        if isinstance(kv, dict):
            for key, value in kv.items():
                if key in params and value is not None:
                    params[key] = value
    except Exception as exc:  # pragma: no cover - KV 不可用不应阻断扫描
        logger.warning("读取 ict_config 失败，使用默认参数：%s", exc)
    for key, value in (overrides or {}).items():
        if value is not None and key in params:
            params[key] = value
    # 类型归一（KV 里可能是字符串）
    for key in ("swing_width", "sweep_lookback", "setup_lookahead", "atr_period"):
        try:
            params[key] = int(params[key])
        except (TypeError, ValueError):
            params[key] = ICT_DEFAULTS[key]
    for key in ("displacement_body_multiple", "displacement_atr_multiple", "minimum_rr"):
        try:
            params[key] = float(params[key])
        except (TypeError, ValueError):
            params[key] = ICT_DEFAULTS[key]
    params["session_timezone"] = str(params.get("session_timezone") or "Asia/Shanghai")
    return params


async def _load_ict_klines(code: str, period: str, limit: int, *, kind: str
                           ) -> List[Dict[str, Any]]:
    """ICT 专用 K 线取数：**只读**共享缓存，绝不写回 ``raw_kline``。

    为什么不能写回 ``raw_kline:{code}:{period}``：该键是 ``/api/kline`` 默认根数的
    数据源（``routers/kline.py`` 在 ``limit == KLINE_LIMIT`` 时直接读它）。ICT 扫描
    请求 300/800 根，一旦写回，``/api/kline?code=...``（不传 limit）会从 121 根被顶到
    301/801 根 —— 跨模块行为污染（``kline.py`` 里有同样的注释约定）。因此：

    1. 共享缓存够长 → 直接切片复用，一个上游请求都不打；
    2. 不够长 → 落独立键 ``ict_{kind}_kline:{code}:{period}:{limit}``（single-flight）；
    3. 上游失败 → 回落到共享缓存的更短序列，按实际根数继续（不补 0、不编造）。

    Args:
        kind: ``scan``（/api/ict/scan）或 ``hist``（/api/ict/history 与 structure-stats）。
            不同用途分键，避免「300 根扫描结果」与「400 根回测序列」互相顶掉。
    """
    needed = int(limit)
    shared = cache.get(f"raw_kline:{code}:{period}")
    if shared and len(shared) >= needed:
        logger.debug("ict/%s reuse raw_kline: %s %s (%d bars)", kind, code, period, len(shared))
        return list(shared[-needed:])

    own_key = f"ict_{kind}_kline:{code}:{period}:{needed}"

    async def _fetch() -> Optional[List[Dict[str, Any]]]:
        try:
            bars = await fetch_tencent_kline(code, period=period, limit=needed, timeout=8.0)
        except Exception as exc:  # noqa: BLE001 —— 上游失败不阻断，交给上层报 ok:false
            logger.warning("ict/%s 取 K 线失败 %s %s: %s", kind, code, period, exc)
            return None
        return list(bars) if bars else None

    bars, _meta = await cache.get_or_fetch_ex(own_key, _fetch, ICT_KLINE_CACHE_TTL)
    if not bars and shared:
        # 上游失败但共享缓存有更短序列：按实际可用根数继续（不静默补 0）
        logger.info("ict/%s 上游失败，回落到共享缓存 %s %s (%d bars)",
                    kind, code, period, len(shared))
        bars = list(shared)
    result = list(bars or [])
    if needed and len(result) > needed:
        result = result[-needed:]
    return result


async def _load_klines(code: str, period: str, limit: int) -> List[Dict[str, Any]]:
    """``/api/ict/scan`` 的 K 线（独立键 ``ict_scan_kline:*``，只读 raw_kline）。"""
    return await _load_ict_klines(code, period, limit, kind="scan")


async def _load_history_klines(code: str, period: str, limit: int) -> List[Dict[str, Any]]:
    """``/history`` 与 ``/structure-stats`` 的 K 线（独立键 ``ict_hist_kline:*``）。"""
    return await _load_ict_klines(code, period, limit, kind="hist")


def _params_signature(params: Dict[str, Any]) -> str:
    text = json.dumps({k: params[k] for k in sorted(params)}, ensure_ascii=False, default=str)
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:8]


def _normalize_code(code: Any) -> Optional[str]:
    """股票代码归一 + 校验：``SH600519 `` → ``sh600519``；非法返回 ``None``。

    规则与 /api/ict/scan 首发时一致（市场前缀 sh/sz/bj/hk + 纯数字）。
    """
    text = str(code or "").strip().lower()
    if not text or len(text) < 7 or not text[2:].isdigit():
        return None
    if text[:2] not in ("sh", "sz", "bj", "hk"):
        return None
    return text


def _parse_horizons(raw: Any) -> Tuple[List[int], Optional[str]]:
    """把 ``"1,3,5,10"`` 解析为升序去重的 horizon 列表。

    Returns:
        ``(horizons, error)``：``error`` 非 None 时 ``horizons`` 为空列表。
    """
    text = str(raw or "").replace(";", ",").replace(" ", ",").replace("，", ",")
    items = [seg for seg in text.split(",") if seg.strip()]
    if not items:
        return [], "horizons 不能为空（示例：1,3,5,10）"
    values: List[int] = []
    for segment in items:
        try:
            value = int(float(segment))
        except (TypeError, ValueError):
            return [], f"horizons 含非法值：{segment}（需为 1~{ict_history.MAX_HORIZON} 的整数）"
        if value <= 0 or value > ict_history.MAX_HORIZON:
            return [], f"horizons 超出范围：{value}（需为 1~{ict_history.MAX_HORIZON}）"
        if value not in values:
            values.append(value)
    if len(values) > 6:
        return [], "horizons 最多 6 个（每个 horizon 都要算一遍全样本基准，过多无意义）"
    return sorted(values), None


@router.get("/scan")
async def ict_scan(
    code: str = Query(..., description="标的代码，如 sh600519"),
    period: str = Query("day", description="周期：day/week/month/60/30/15/5"),
    direction: str = Query("auto", description="auto / long / short"),
    # 注意：下界故意放宽到 1，让「根数不足」走 ok:false + reason 的业务失败信封，
    # 而不是被 FastAPI 的 422 参数校验拦掉（验收 5 要求可读的失败原因）。
    limit: int = Query(300, ge=1, le=MAX_LIMIT, description="参与扫描的 K 线根数"),
    swing_width: Optional[int] = Query(None, ge=1, le=20, description="覆盖摆动点宽度"),
    minimum_rr: Optional[float] = Query(None, gt=0, le=20, description="覆盖最小盈亏比"),
) -> Dict[str, Any]:
    """ICT 九阶段证据链 + 六道机械门扫描（日线/分钟级）。"""
    if not feature_enabled("ict_model_enabled"):
        return _fail("ICT 模型未启用（ict_model_enabled=false）。"
                     "开启方式：设置环境变量 ICT_MODEL_ENABLED=1 后重启服务。")

    c = _normalize_code(code)
    if c is None:
        return _fail("股票代码格式非法（应为 sh600519 / sz000858）")
    per = str(period or "day").strip().lower()
    if per not in VALID_PERIODS:
        return _fail(f"不支持的周期：{per}（可选：{','.join(sorted(VALID_PERIODS))}）")
    dr = str(direction or "auto").strip().lower()
    if dr not in ("auto", "long", "short"):
        return _fail("direction 必须是 auto / long / short")

    # 参数（含 KV 覆盖）
    params = await _load_params({"swing_width": swing_width, "minimum_rr": minimum_rr})

    bars = await _load_klines(c, per, int(limit))
    if len(bars) < MIN_LIMIT:
        return _fail(f"K 线不足：需要至少 {MIN_LIMIT} 根，实际 {len(bars)} 根"
                     f"（可调大 limit 或换更长周期）")

    is_daily = per in DAILY_PERIODS
    ttl = ICT_CACHE_TTL_DAILY if is_daily else ICT_CACHE_TTL_INTRADAY
    cache_key = f"ict:scan:{c}:{per}:{dr}:{int(limit)}:{_params_signature(params)}"

    async def _compute() -> Optional[Dict[str, Any]]:
        try:
            candles = ict_model.parse_kline_rows(bars)
            return ict_model.scan(candles, params=params, direction=dr, is_daily=is_daily)
        except ValueError as exc:
            logger.info("ICT 扫描跳过 %s %s: %s", c, per, exc)
            return None
        except Exception as exc:  # pragma: no cover - 防御式
            logger.warning("ICT 扫描异常 %s %s: %s", c, per, exc)
            return None

    data, meta = await cache.get_or_fetch_ex(cache_key, _compute, ttl)
    if data is None:
        return _fail(f"ICT 扫描失败：K 线结构不可解析（共 {len(bars)} 根）")

    payload = dict(data)
    payload.update({
        "code": c,
        "period": per,
        "cached": bool(meta.get("age") is not None and meta.get("age") > 0),
        "params_source": "query>kv>default",
    })
    return {
        "ok": True,
        "data": payload,
        "message": "ok",
        "source": "ict_model（移植自 ict-trading-model/MIT）+ 腾讯K线",
        "as_of": payload.get("last_candle") or _now_iso(),
        "age": meta.get("age"),
        "degraded": False,
        "reason": None,
    }


# ----------------------------------------------------------------------
#  第二阶段：滚动回测统计（只读，不改交易路径）
# ----------------------------------------------------------------------
@router.get("/history")
async def ict_history_endpoint(
    code: str = Query(..., description="标的代码，如 sh600519"),
    period: str = Query("day", description="周期：day/week/month（horizons 以该周期 bar 数计）"),
    limit: int = Query(400, ge=1, le=MAX_LIMIT,
                       description="回测使用的 K 线根数（默认 400，上限 800）"),
    horizons: str = Query("1,3,5,10", description="持有天数（交易日），逗号分隔，最多 6 个"),
    swing_width: Optional[int] = Query(None, ge=1, le=20, description="覆盖摆动点宽度"),
    minimum_rr: Optional[float] = Query(None, gt=0, le=20, description="覆盖最小盈亏比"),
) -> Dict[str, Any]:
    """ICT 滚动回测统计：逐日滚动扫描 → 事件去重 → 收益分布 + 随机持有基准。

    **严禁未来函数**：第 ``i`` 次扫描只把 ``candles[0:i+1]`` 交给 ``ict_model.scan``
    （细节见 ``server/ict_history.py`` 模块文档）。

    诚信约定：样本不足时 ``stats`` 中对应 horizon 为 ``null``/``samples=0``，
    ``verdict.note`` 如实写明「不具参考价值」，**绝不**为了让统计有结果而放宽六道门。
    """
    if not feature_enabled("ict_model_enabled"):
        return _fail("ICT 模型未启用（ict_model_enabled=false）。"
                     "开启方式：设置环境变量 ICT_MODEL_ENABLED=1 后重启服务。")

    c = _normalize_code(code)
    if c is None:
        return _fail("股票代码格式非法（应为 sh600519 / sz000858）")
    per = str(period or "day").strip().lower()
    if per not in DAILY_PERIODS:
        # 日线族才谈得上「持有 N 个交易日」；分钟级滚动回测语义不同，留待独立任务。
        return _fail(f"滚动回测目前只支持日线族周期：{'/'.join(sorted(DAILY_PERIODS))}"
                     f"（收到 {per}）；horizons 以交易日计")

    horizon_list, horizon_error = _parse_horizons(horizons)
    if horizon_error:
        return _fail(horizon_error)

    wanted = int(limit)
    if wanted < MIN_LIMIT:
        return _fail(f"limit 至少 {MIN_LIMIT} 根（少于该根数结构识别无统计意义），收到 {wanted}")

    params = await _load_params({"swing_width": swing_width, "minimum_rr": minimum_rr})
    signature = _params_signature(params)
    horizon_key = "-".join(str(h) for h in horizon_list)

    bars = await _load_history_klines(c, per, wanted)
    if len(bars) < MIN_LIMIT:
        return _fail(f"K 线不足：需要至少 {MIN_LIMIT} 根，实际 {len(bars)} 根"
                     f"（可调大 limit 或换更长周期）")

    cache_key = f"ict:history:{c}:{per}:{wanted}:{signature}:{horizon_key}"

    async def _compute() -> Optional[Dict[str, Any]]:
        try:
            candles = ict_model.parse_kline_rows(bars)
        except ValueError as exc:
            logger.info("ICT 回测跳过 %s %s: %s", c, per, exc)
            return None
        try:
            # 320 次滚动扫描约 0.4s：放线程池，避免阻塞事件循环（/api/health 不能被拖住）
            return await asyncio.to_thread(
                ict_history.build_history, candles, params, tuple(horizon_list),
            )
        except ValueError as exc:
            logger.info("ICT 回测参数/样本不足 %s %s: %s", c, per, exc)
            return None
        except Exception as exc:  # pragma: no cover - 防御式
            logger.warning("ICT 回测异常 %s %s: %s", c, per, exc)
            return None

    data, meta = await cache.get_or_fetch_ex(cache_key, _compute, ICT_HISTORY_CACHE_TTL)
    if data is None:
        return _fail(f"ICT 回测失败：K 线结构不可解析（共 {len(bars)} 根）")

    payload: Dict[str, Any] = {
        "code": c,
        "period": per,
        "candles": data["candles"],
        "scanned_from": data["scanned_from"],
        "scanned_to": data["scanned_to"],
        "scanned_days": data["scanned_days"],
        "params": data["params"],
        "params_signature": signature,
        "horizons": data["horizons"],
        "events": data["events"],
        "status_days": data["status_days"],
        "confirmed_events": data["confirmed_events"],
        "stats": data["stats"],
        "baseline": data["baseline"],
        "verdict": data["verdict"],
        "no_lookahead": data["no_lookahead"],
        "scan_ms": data["scan_ms"],
        "warnings": data["warnings"],
        "cached": bool(meta.get("age") is not None and meta.get("age") > 0),
        "params_source": "query>kv>default",
    }
    if per != "day":
        payload["warnings"] = list(payload["warnings"]) + [
            f"当前周期为 {per}：horizons 表示「该周期的后续 bar 数」，不是交易日。"
        ]

    return {
        "ok": True,
        "data": payload,
        "message": "ok",
        "source": "ict_model（移植自 ict-trading-model/MIT）+ 腾讯K线 + 本地滚动回测",
        "as_of": payload["scanned_to"],
        "age": meta.get("age"),
        "degraded": bool(meta.get("degraded")),
        "reason": meta.get("reason"),
    }


# ----------------------------------------------------------------------
#  第三阶段：结构级统计（FVG 填充 / sweep 反转 / OB 回踩 + 波动率匹配基准）
# ----------------------------------------------------------------------
@router.get("/structure-stats")
async def ict_structure_stats(
    code: str = Query(..., description="标的代码，如 sh600519"),
    period: str = Query("day", description="周期：day/week/month"),
    limit: int = Query(400, ge=1, le=MAX_LIMIT, description="参与统计的 K 线根数"),
    horizon: int = Query(ict_structure.DEFAULT_HORIZON, ge=1,
                         le=ict_structure.MAX_HORIZON, description="观察窗口（bar 数）"),
    reversal_atr_mult: float = Query(ict_structure.DEFAULT_REVERSAL_ATR_MULT, gt=0, le=10,
                                     description="sweep 反转幅度阈值（× ATR14）"),
    swing_width: Optional[int] = Query(None, ge=1, le=20, description="覆盖摆动点宽度"),
    sweep_lookback: Optional[int] = Query(None, ge=1, le=250, description="覆盖扫单回看窗口"),
) -> Dict[str, Any]:
    """ICT 结构级统计：FVG 填充率 / sweep 反转率 / OB 回踩率 + 波动率匹配基准。

    统计对象是**结构本身**（不是六道门全过的 confirmed）。原因：实测 20 只主流 A 股
    400 根日线，confirmed 出现 0~3 次，样本量永远不足；而 FVG/sweep 每天都有
    （茅台 400 根：106 个 FVG / 21 个 sweep），才具备形成正向反馈的样本基础。

    未来函数纪律：FVG/sweep 只在**成立日之后**的 K 线里找填充/反转；未来不足以覆盖
    ``horizon`` 的样本计入 ``censored`` 剔除（**不**按「未填充」处理，否则填充率虚高）。
    """
    if not feature_enabled("ict_model_enabled"):
        return _fail("ICT 模型未启用（ict_model_enabled=false）。"
                     "开启方式：设置环境变量 ICT_MODEL_ENABLED=1 后重启服务。")

    c = _normalize_code(code)
    if c is None:
        return _fail("股票代码格式非法（应为 sh600519 / sz000858）")
    per = str(period or "day").strip().lower()
    if per not in DAILY_PERIODS:
        return _fail(f"结构统计目前只支持日线族周期：{'/'.join(sorted(DAILY_PERIODS))}（收到 {per}）")

    wanted = int(limit)
    if wanted < MIN_LIMIT:
        return _fail(f"limit 至少 {MIN_LIMIT} 根（少于该根数结构识别无统计意义），收到 {wanted}")

    params = await _load_params({"swing_width": swing_width, "sweep_lookback": sweep_lookback})
    signature = _params_signature(params)

    bars = await _load_history_klines(c, per, wanted)
    if len(bars) < MIN_LIMIT:
        return _fail(f"K 线不足：需要至少 {MIN_LIMIT} 根，实际 {len(bars)} 根"
                     f"（可调大 limit 或换更长周期）")

    cache_key = (f"ict:structure:{c}:{per}:{wanted}:{int(horizon)}:"
                 f"{float(reversal_atr_mult)}:{signature}")

    async def _compute() -> Optional[Dict[str, Any]]:
        try:
            candles = ict_model.parse_kline_rows(bars)
        except ValueError as exc:
            logger.info("ICT 结构统计跳过 %s %s: %s", c, per, exc)
            return None
        try:
            return await asyncio.to_thread(
                ict_structure.build_structure_stats, candles, params,
                horizon=int(horizon), reversal_atr_mult=float(reversal_atr_mult),
                is_daily=per in DAILY_PERIODS,
            )
        except ValueError as exc:
            logger.info("ICT 结构统计参数非法 %s %s: %s", c, per, exc)
            return None
        except Exception as exc:  # pragma: no cover - 防御式
            logger.warning("ICT 结构统计异常 %s %s: %s", c, per, exc)
            return None

    data, meta = await cache.get_or_fetch_ex(cache_key, _compute, ICT_STRUCTURE_CACHE_TTL)
    if data is None:
        return _fail(f"结构统计失败：K 线结构不可解析（共 {len(bars)} 根）")

    payload: Dict[str, Any] = {
        "code": c,
        "period": per,
        "candles": data["candles"],
        "range": data["range"],
        "horizon": data["horizon"],
        "params": data["params"],
        "params_signature": signature,
        "counts": data["counts"],
        "fvg": data["fvg"],
        "sweep": data["sweep"],
        "ob": data["ob"],
        "baseline": data["baseline"],
        "edge": data["edge"],
        "verdict": data["verdict"],
        "no_lookahead": data["no_lookahead"],
        "metrics_ms": data["metrics_ms"],
        "warnings": data["warnings"],
        "cached": bool(meta.get("age") is not None and meta.get("age") > 0),
        "params_source": "query>kv>default",
    }
    if per != "day":
        payload["warnings"] = list(payload["warnings"]) + [
            f"当前周期为 {per}：horizon / 天数均以「该周期的 bar 数」计，不是交易日。"
        ]

    return {
        "ok": True,
        "data": payload,
        "message": "ok",
        "source": "ict_model 检测（移植自 ict-trading-model/MIT）+ 腾讯K线 + 本地结构统计",
        "as_of": payload["range"]["to"],
        "age": meta.get("age"),
        "degraded": bool(meta.get("degraded")),
        "reason": meta.get("reason"),
    }
