# -*- coding: utf-8 -*-
"""ETF 双动量轮动端点（P1-3 / T17）：``GET /api/etf/rotation``。

契约（design §2.4 + T17 七条硬纪律）
-----------------------------------
``{ok, data, source, degraded, message, as_of, age, reason}``，其中 ``data`` 至少含：

    as_of, params, universe[], current_holding[], target_holding[], action,
    momentum_rank[], metrics{total_return, annual_return, max_drawdown, sharpe,
    calmar, volatility, turnover, trades}, benchmark{equal_weight_return,
    vs_equal_weight}, plateau, samples, annual_ok, reason, warnings[]

实现要点
--------
- 取数：``fetch_tencent_kline`` → 失败回退 ``fetch_kline_eastmoney``；
  每只单独缓存 ``etf_kline_v1:{code}``（TTL 600s），``refresh=true`` 绕过缓存；
- **某只取不到就剔除并告警**：不向前值填充、不补 0、不用陈旧数据冒充当日行情；
- 全部参数透传给 ``server.etf_rotation``，本层**不算任何指标**（纯编排 + 取数）；
- 样本 <252 交易日：年化口径全 null + ``reason``（由 ``etf_rotation`` 保证），
  本层只负责如实透出，不「美化」成 0。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Query

from server import etf_rotation as rot
from server.cache import cache
from server.eastmoney import fetch_kline_eastmoney
from server.tencent_kline import fetch_tencent_kline
from server.core.timeutil import now_iso as _now_iso

logger = logging.getLogger(__name__)
# 路径前缀 /api 由 main.py 的 include_router(prefix="/api") 提供，此处只给 /etf
router = APIRouter(prefix="/etf", tags=["etf"])

ETF_CACHE_TTL = 600.0           # 单只 ETF 日 K 缓存 10 分钟
KLINE_LIMIT = 300               # 参与回测的日 K 根数（≈14 个月）
MAX_POOL_SIZE = 12
MIN_LIMIT_BARS = 30             # 少于此根数的标的一律剔除（连动量都算不出）
_CODE_RE = re.compile(r"^(sh|sz|bj)\d{6}$")


def _fail(reason: str, **extra: Any) -> Dict[str, Any]:
    """统一失败信封：``ok:false + data:null + reason``（绝不返回空结果当成功）。"""
    payload: Dict[str, Any] = {
        "ok": False,
        "data": None,
        "message": reason,
        "source": "etf_rotation",
        "as_of": None,
        "age": None,
        "degraded": True,
        "reason": reason,
    }
    payload.update(extra)
    return payload


def _parse_pool(pool: Optional[str]) -> List[str]:
    """解析 ``pool``：逗号分隔代码；非法格式跳过（由调用方汇总成告警）。"""
    if not pool:
        return [item["code"] for item in rot.DEFAULT_POOL]
    codes: List[str] = []
    for token in str(pool).split(","):
        code = token.strip().lower()
        if not code:
            continue
        if code not in codes:
            codes.append(code)
    return codes[:MAX_POOL_SIZE]


def _name_of(code: str) -> Optional[str]:
    """默认池内的中文名；池外代码不编造名称（返回 None，前端显示代码）。"""
    for item in rot.DEFAULT_POOL:
        if item["code"] == code:
            return item["name"]
    return None


async def _fetch_bars(code: str) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """取单只 ETF 日 K：腾讯优先，东财兜底。返回 ``(bars, source)``。"""
    try:
        bars = await fetch_tencent_kline(code, period="day", limit=KLINE_LIMIT, timeout=6.0)
        if bars:
            return bars, "tencent"
    except Exception as exc:  # noqa: BLE001 - 任取数失败都进入兜底
        logger.debug("etf tencent kline failed %s: %s", code, exc)
    try:
        bars = await fetch_kline_eastmoney(code, period="day", limit=KLINE_LIMIT, timeout=4.0)
        if bars:
            return bars, "eastmoney"
    except Exception as exc:  # noqa: BLE001
        logger.debug("etf eastmoney kline failed %s: %s", code, exc)
    return None, "none"


async def _load_bars(code: str, refresh: bool) -> Tuple[Optional[List[Dict[str, Any]]], str, Optional[float]]:
    """带缓存的取数。返回 ``(bars, source, age_seconds)``；``age`` 为 None 表示本次新取。"""
    cache_key = f"etf_kline_v1:{code}"

    if refresh:
        bars, source = await _fetch_bars(code)
        if bars:
            cache.set(cache_key, {"bars": bars, "source": source}, ETF_CACHE_TTL)
        return bars, source, None

    async def _fetch() -> Optional[Dict[str, Any]]:
        bars, source = await _fetch_bars(code)
        if not bars:
            return None
        return {"bars": bars, "source": source}

    payload = await cache.get_or_fetch(cache_key, _fetch, ETF_CACHE_TTL)
    if not payload:
        return None, "none", None
    entry = cache.get_entry(cache_key) or {}
    age = entry.get("age")
    return payload.get("bars"), payload.get("source", "cache"), (float(age) if age is not None else None)


def _normalize_warnings(items: Any) -> List[str]:
    """把 ``[{code, reason}]`` 归一成**中文可读**字符串列表。"""
    out: List[str] = []
    for item in items or []:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append(text)
            continue
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not reason:
            continue
        if code in ("", "*"):
            out.append(reason)
        else:
            name = _name_of(code)
            label = f"{code}({name})" if name else code
            out.append(f"{label}：{reason}")
    # 去重但保序（同一原因可能来自多个入口）
    seen = set()
    unique: List[str] = []
    for text in out:
        if text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


@router.get("/rotation")
async def etf_rotation(
    pool: Optional[str] = Query(None, description="逗号分隔 ETF 代码，默认内置 4 只宽基"),
    lookback: int = Query(20, ge=2, le=250, description="动量回看天数"),
    top: int = Query(2, ge=1, le=6, description="目标持仓数"),
    cost_bps: float = Query(5.0, ge=0.0, le=100.0, description="单边换手成本（基点）"),
    rebalance_days: int = Query(20, ge=1, le=250, description="调仓间隔（交易日）"),
    refresh: bool = Query(False, description="true 绕过缓存重新取数"),
    buffer_slots: int = Query(1, ge=0, le=5, description="换仓缓冲位数"),
) -> Dict[str, Any]:
    """ETF 双动量轮动：当前/目标持仓 + 动量排名 + 参数平原。"""
    requested = _parse_pool(pool)
    warnings: List[str] = []

    if not requested:
        return _fail("pool 为空：请传入至少一个 ETF 代码，或使用默认池")

    valid_codes: List[str] = []
    for code in requested:
        if _CODE_RE.match(code):
            valid_codes.append(code)
        else:
            warnings.append(f"代码格式非法，已忽略：{code}（应形如 sh510300）")
    if not valid_codes:
        return _fail("pool 中没有任何合法 ETF 代码（应形如 sh510300 / sz159915）")

    # ---- 取数：失败即剔除，绝不填充 ----
    bars_by_code: Dict[str, List[Dict[str, Any]]] = {}
    universe: List[Dict[str, Any]] = []
    sources: List[str] = []
    ages: List[float] = []

    for code in valid_codes:
        bars, source, age = await _load_bars(code, refresh)
        if age is not None:
            ages.append(age)
        if not bars:
            warnings.append(f"{code}：行情取数失败（腾讯与东财均无返回），已从本轮股票池剔除")
            continue
        if len(bars) < MIN_LIMIT_BARS:
            warnings.append(
                f"{code}：可用日 K 仅 {len(bars)} 根，少于 {MIN_LIMIT_BARS} 根，"
                f"无法计算动量，已从本轮股票池剔除")
            continue
        bars_by_code[code] = bars
        sources.append(source)
        universe.append({
            "code": code,
            "name": _name_of(code) or code,
            "bars": len(bars),
            "first_date": str(bars[0].get("d") or ""),
            "last_date": str(bars[-1].get("d") or ""),
            "source": source,
        })

    if not bars_by_code:
        return _fail("股票池内所有标的均无可用行情，本轮无法生成信号", warnings=warnings)

    # ---- 纯逻辑层：信号 + 回测 + 基准 + 参数平原 ----
    params = {
        "lookback": int(lookback),
        "top_n": int(top),
        "cost_bps": float(cost_bps),
        "rebalance_days": int(rebalance_days),
        "buffer_slots": int(buffer_slots),
    }
    snapshot = rot.rotation_snapshot(bars_by_code, params)
    warnings.extend(_normalize_warnings(snapshot.get("warnings")))

    benchmark_raw = rot.equal_weight_benchmark(bars_by_code, start_index=int(lookback))
    total_return = snapshot["metrics"].get("total_return")
    equal_weight = benchmark_raw.get("equal_weight_return")
    vs_equal_weight = None
    if total_return is not None and equal_weight is not None:
        vs_equal_weight = round(float(total_return) - float(equal_weight), 10)

    benchmark = {
        "equal_weight_return": equal_weight,
        "vs_equal_weight": vs_equal_weight,
        "samples": benchmark_raw.get("samples"),
        "per_code": benchmark_raw.get("per_code"),
        "basis": "等权买入持有（同一批标的、同一时间窗、不调仓、不计成本）",
        "reason": benchmark_raw.get("reason"),
    }

    plateau = rot.parameter_plateau(
        bars_by_code,
        list(rot.DEFAULT_LOOKBACKS),
        list(rot.DEFAULT_TOP_NS),
        cost_bps=float(cost_bps),
        rebalance_days=int(rebalance_days),
        buffer_slots=int(buffer_slots),
    )

    # 样本不足一年的说明性告警由 etf_rotation 以 reason 形式给出（含具体样本数），
    # 此处不重复堆叠同义文案，避免 warnings 里出现两条几乎相同的话。

    data: Dict[str, Any] = {
        "as_of": snapshot.get("as_of"),
        "params": params,
        "universe": universe,
        "current_holding": snapshot.get("current_holding") or [],
        "target_holding": snapshot.get("target_holding") or [],
        "action": snapshot.get("action"),
        "momentum_rank": snapshot.get("momentum_rank") or [],
        "missing": snapshot.get("missing") or [],
        "metrics": snapshot.get("metrics") or {},
        "benchmark": benchmark,
        "plateau": plateau,
        "equity": snapshot.get("equity") or [],
        "trades": snapshot.get("trades") or [],
        "samples": snapshot.get("samples"),
        "annual_ok": bool(snapshot.get("annual_ok")),
        "reason": snapshot.get("reason"),
        "warnings": warnings,
        "names": {item["code"]: item["name"] for item in universe},
    }

    degraded = bool(warnings) or not data["annual_ok"]
    return {
        "ok": True,
        "data": data,
        "message": "ok" if not warnings else f"ok（{len(warnings)} 条告警）",
        "source": "etf_rotation + " + "+".join(sorted(set(sources))) if sources else "etf_rotation",
        "as_of": data["as_of"],
        # age：本轮命中的最旧一份 K 线缓存年龄（秒）；refresh=true 或全部新取时为 None
        "age": (None if refresh or not ages else round(max(ages), 3)),
        "degraded": degraded,
        "reason": (warnings[0] if warnings else None),
    }
