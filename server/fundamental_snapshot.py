"""快照装配：六卡 + 分位 + 覆盖统计"""

from __future__ import annotations
import asyncio
import logging
import math
import os
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from server.cache import cache  # noqa: F401  —— 取数缓存由 Fetcher 层使用，此处保持依赖可见
from server.providers.akshare_fundamental import (
    BAIDU_INDICATORS,
    dividend_fetcher,
    financial_abstract_fetcher,
    prefixed_code,
    symbol_of,
    valuation_fetcher,
)
from server.providers.base import SourceResult

from server.fundamental_metrics import _card, _pct_rank, _ratio_pct, _unavailable_card, ttm_value
from server.fundamental_periods import (
    CARD_KEYS,
    FEATURE_FLAG_KEY,
    MAX_ENVELOPE_REASON_CHARS,
    MIN_PERCENTILE_SAMPLE,
    PERIOD_STALE_ALLOWANCE_QUARTERS,
    VALUATION_STALE_DAYS,
    _clip,
    is_period_stale,
    logger,
    period_cn_label,
    period_iso,
    period_quarter_label,
    quarters_behind,
)
from server.fundamental_valuation import _is_daily_stale, _round_or_none

# ----------------------------------------------------------------------
#  行情（现价 + 名称）：统一走 QuoteHub（B4 边界）
# ----------------------------------------------------------------------
async def _quote_snapshot(prefixed: str) -> Dict[str, Any]:
    """从报价中枢取现价/名称；失败返回空 dict（不抛异常）。"""
    try:
        from server.quote_hub import quote_hub

        quote = await quote_hub.get(prefixed)
        return quote if isinstance(quote, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("fundamental: 行情快照不可用 code=%s: %s", prefixed, exc)
        return {}


# ----------------------------------------------------------------------
#  主入口 1：/api/finance
# ----------------------------------------------------------------------
async def get_fundamental_snapshot(code: str,
                                   price: Optional[float] = None,
                                   report_type: Optional[str] = None) -> Dict[str, Any]:
    """财务基本面快照（6 卡 + 报告期元信息）。

    Args:
        code: 标的代码（``sh600519`` / ``600519`` / ``600519.SH``）。
        price: 现价；``None`` 时自动从 QuoteHub 取（股息率需要）。
        report_type: 兼容旧参数 ``type``，本期仅回显。

    Returns:
        dict: §3.1/§3.2 统一信封 ``{ok,data,message,source,as_of,age,degraded,reason}``。

    Notes:
        - ``ok:true`` 只代表「请求被正确处理」；某卡无值是合法的（``value:null+reason``）。
        - 6 卡全部无值时 ``ok:false``（数据源整体不可用），但 ``data.cards`` 仍给出
          6 张 ``value:null`` 的卡，前端可稳定渲染「不可用 + 原因」。
    """
    prefixed = prefixed_code(code)
    if not prefixed:
        return {
            "ok": False,
            "data": None,
            "message": "股票代码非法",
            "source": None,
            "as_of": None,
            "age": None,
            "degraded": True,
            "reason": f"无法从 {code!r} 解析合法 A 股代码（需 6 位数字，可带 sh/sz/bj 前缀）",
        }
    symbol = symbol_of(code) or ""

    # 三路数据源 + 报价并发取数（QuoteHub 走腾讯行情，不占 AkShare 并发闸门）。
    abstract_res, valuation_res, dividend_res, quote = await asyncio.gather(
        financial_abstract_fetcher.fetch(code=prefixed),
        valuation_fetcher.fetch(code=prefixed, years=5, indicators=("pe_ttm", "pb")),
        dividend_fetcher.fetch(code=prefixed),
        _quote_snapshot(prefixed),
        return_exceptions=False,
    )

    quote_price = price
    if quote_price is None:
        raw_price = quote.get("price")
        try:
            quote_price = float(raw_price) if raw_price is not None else None
        except (TypeError, ValueError):
            quote_price = None
    name = quote.get("name") or None

    cards: Dict[str, Dict[str, Any]] = {}
    reasons: List[str] = []
    stale_basis_period = (
        f"报告期落后 >{PERIOD_STALE_ALLOWANCE_QUARTERS + 1} 个季度判为陈旧"
        f"（容忍最近 1 个尚未到披露截止日的季度）"
    )
    stale_basis_daily = f"末值距今 >{VALUATION_STALE_DAYS} 天判为陈旧（日频序列）"

    # ---------------- 财务 4 卡 ----------------
    report_period: Optional[str] = None
    report_stale: Optional[bool] = None
    behind: Optional[int] = None
    if not abstract_res.available:
        reason = abstract_res.reason or "新浪财务摘要不可用"
        for key in ("roe_ttm", "revenue_yoy", "profit_yoy"):
            cards[key] = _unavailable_card(key, reason, source=abstract_res.source,
                                           stale_basis=stale_basis_period)
        reasons.append(reason)
        fin_ctx: Dict[str, Any] = {}
    else:
        payload = abstract_res.data or {}
        periods: List[str] = list(payload.get("periods") or [])
        metrics: Dict[str, Dict[str, Optional[float]]] = payload.get("metrics") or {}
        report_period = payload.get("latest_period")
        if not report_period:
            reason = "新浪财务摘要未能确定最新报告期"
            for key in ("roe_ttm", "revenue_yoy", "profit_yoy"):
                cards[key] = _unavailable_card(key, reason, source=abstract_res.source,
                                               stale_basis=stale_basis_period)
            reasons.append(reason)
            fin_ctx = {}
        else:
            report_stale, behind = is_period_stale(report_period)
            eps_ttm = ttm_value(metrics.get("eps_basic") or {}, report_period)
            revenue_ttm = ttm_value(metrics.get("revenue") or {}, report_period)
            profit_ttm = ttm_value(metrics.get("net_profit_parent") or {}, report_period)
            bps_series = metrics.get("bps") or {}
            bps = bps_series.get(report_period)
            roe_reported = (metrics.get("roe_reported") or {}).get(report_period)
            revenue_yoy = (metrics.get("revenue_yoy") or {}).get(report_period)
            profit_yoy = (metrics.get("profit_yoy") or {}).get(report_period)

            # ROE_TTM = EPS_TTM / 最新每股净资产
            roe_ttm = _ratio_pct(eps_ttm, bps)
            fin_ctx = {
                "eps_ttm": eps_ttm,
                "revenue_ttm": revenue_ttm,
                "profit_ttm": profit_ttm,
                "bps": bps,
                "roe_reported": roe_reported,
            }

            as_of_iso = period_iso(report_period)
            label = period_quarter_label(report_period)

            if roe_ttm is None:
                detail = []
                if eps_ttm is None:
                    detail.append("EPS_TTM 不可聚合（季度序列不完整，未插值）")
                if bps is None:
                    detail.append("每股净资产缺失")
                cards["roe_ttm"] = _unavailable_card(
                    "roe_ttm", "ROE_TTM 不可计算：" + "；".join(detail),
                    source=abstract_res.source, stale_basis=stale_basis_period)
                reasons.append(cards["roe_ttm"]["reason"])
            else:
                cards["roe_ttm"] = _card(
                    roe_ttm, "roe_ttm",
                    source=abstract_res.source,
                    as_of=as_of_iso,
                    period_label=label,
                    degraded=bool(abstract_res.degraded),
                    reason=abstract_res.reason,
                    latest_period_is_stale=report_stale,
                    stale_basis=stale_basis_period,
                    formula="EPS_TTM（滚动 4 季）/ 最新每股净资产 × 100",
                    extra={"roe_reported": roe_reported,
                           "roe_reported_period": label,
                           "roe_reported_basis": "报告期累计口径（H1/前三季非年度值）",
                           "eps_ttm": round(eps_ttm, 4) if eps_ttm is not None else None,
                           "bps": round(bps, 4) if bps is not None else None},
                )

            for key, value, label_cn in (
                ("revenue_yoy", revenue_yoy, "营业总收入增长率"),
                ("profit_yoy", profit_yoy, "归属母公司净利润增长率"),
            ):
                if value is None:
                    cards[key] = _unavailable_card(
                        key, f"新浪财务摘要未提供『{label_cn}』（{report_period}）",
                        source=abstract_res.source, stale_basis=stale_basis_period)
                    reasons.append(cards[key]["reason"])
                else:
                    cards[key] = _card(
                        value, key,
                        source=f"{abstract_res.source}·{label_cn}",
                        as_of=as_of_iso,
                        period_label=label,
                        degraded=bool(abstract_res.degraded),
                        reason=abstract_res.reason,
                        latest_period_is_stale=report_stale,
                        stale_basis=stale_basis_period,
                        formula=f"财报原值（{label_cn}，报告期同比口径，未做 TTM 重构）",
                    )

    # ---------------- 估值 2 卡 ----------------
    val_ctx: Dict[str, Any] = {}
    if not valuation_res.available:
        # 全指标失败时 valuation_res.reason 是聚合文案；逐卡优先取 extra.failed 的
        # 分项原因，避免「pb 卡里显示 pe_ttm 的失败原因」这种误导。
        per_key_failed = (valuation_res.extra or {}).get("failed") or {}
        for key in ("pe_ttm", "pb"):
            reason = (per_key_failed.get(key) or valuation_res.reason
                      or "百度估值不可用")
            cards[key] = _unavailable_card(key, reason, source=valuation_res.source,
                                           stale_basis=stale_basis_daily)
            reasons.append(reason)
    else:
        payload = valuation_res.data or {}
        series_map: Dict[str, List[Dict[str, Any]]] = payload.get("series") or {}
        last_map: Dict[str, Dict[str, Any]] = payload.get("last") or {}
        failed: Dict[str, str] = payload.get("failed") or {}
        for key in ("pe_ttm", "pb"):
            rows = series_map.get(key) or []
            last = last_map.get(key) or {}
            value = last.get("value")
            if not rows or value is None:
                reason = failed.get(key) or f"百度未返回 {BAIDU_INDICATORS.get(key, key)} 序列"
                cards[key] = _unavailable_card(key, reason, source=valuation_res.source,
                                               stale_basis=stale_basis_daily)
                reasons.append(reason)
                continue
            last_date = str(last.get("date") or "")[:10]
            vals = [row.get("value") for row in rows]
            percentile = _pct_rank(vals, value) if len(rows) >= MIN_PERCENTILE_SAMPLE else None
            daily_stale = _is_daily_stale(last_date)
            card = _card(
                value, key,
                source=f"{valuation_res.source}·{BAIDU_INDICATORS.get(key, key)}",
                as_of=last_date or None,
                period_label=None,
                degraded=bool(failed),
                reason=("；".join(f"{k} 不可用（{v}）" for k, v in failed.items())
                        if failed else None),
                latest_period_is_stale=daily_stale,
                stale_basis=stale_basis_daily,
                formula=f"{BAIDU_INDICATORS.get(key, key)} 日频序列末值；分位={key} 在近 5 年序列中的百分位",
                extra={"sample": len(rows),
                       "window_start": rows[0].get("date"),
                       "window_end": last_date,
                       "percentile_basis": f"近 5 年 {len(rows)} 个交易日"},
            )
            card["percentile_5y"] = percentile
            cards[key] = card
            val_ctx[key] = {
                "value": value, "as_of": last_date,
                "percentile_5y": percentile, "sample": len(rows),
            }

    # ---------------- 股息率 1 卡 ----------------
    div_ctx: Dict[str, Any] = {}
    if not dividend_res.available:
        reason = dividend_res.reason or "巨潮分红不可用"
        cards["dividend_yield"] = _unavailable_card(
            "dividend_yield", reason, source=dividend_res.source,
            stale_basis="近 12 个月已除权现金分红 ÷ 现价")
        reasons.append(reason)
    else:
        payload = dividend_res.data or {}
        per_share = payload.get("ttm_per_share")
        last_ex = payload.get("last_ex_date")
        window = f"{payload.get('window_start')} ~ {payload.get('window_end')}"
        div_ctx = {
            "per_share_ttm": per_share,
            "records_ttm": payload.get("ttm_records"),
            "window": window,
        }
        if per_share is None:
            cards["dividend_yield"] = _unavailable_card(
                "dividend_yield",
                f"近 12 个月（{window}）无已除权现金分红记录，该口径下股息率不可计算",
                source=dividend_res.source,
                stale_basis="近 12 个月已除权现金分红 ÷ 现价")
            reasons.append(cards["dividend_yield"]["reason"])
        elif quote_price is None:
            cards["dividend_yield"] = _unavailable_card(
                "dividend_yield",
                f"现价不可得，股息率无法计算（已取到近 12 个月分红 {per_share:.4f} 元/股）",
                source=dividend_res.source,
                stale_basis="近 12 个月已除权现金分红 ÷ 现价")
            reasons.append(cards["dividend_yield"]["reason"])
        else:
            value = per_share / quote_price * 100.0
            cards["dividend_yield"] = _card(
                value, "dividend_yield",
                source=f"{dividend_res.source} + 现价",
                as_of=last_ex,
                period_label=None,
                degraded=False,
                reason=None,
                latest_period_is_stale=None,
                stale_basis="近 12 个月已除权现金分红 ÷ 现价",
                formula="Σ 近 12 个月已除权现金分红（元/股）÷ 现价 × 100",
                extra={"per_share_ttm": round(per_share, 4),
                       "dividend_count_ttm": payload.get("ttm_records"),
                       "window": window,
                       "price": round(quote_price, 4),
                       "price_source": quote.get("source") or "腾讯行情（QuoteHub）"},
            )

    # ---------------- 信封 ----------------
    ordered_cards = {key: cards[key] for key in CARD_KEYS if key in cards}
    available = [key for key, card in ordered_cards.items() if card.get("value") is not None]
    missing = [key for key in ordered_cards if key not in available]

    fetch_stamps = [
        float(res.extra.get("fetched_at"))
        for res in (abstract_res, valuation_res, dividend_res)
        if res.extra.get("fetched_at")
    ]
    age = round(time.time() - min(fetch_stamps), 1) if fetch_stamps else None

    source_parts: List[str] = []
    for res in (abstract_res, valuation_res, dividend_res):
        label = res.source
        if label and res.available and label not in source_parts:
            source_parts.append(label)
    envelope_source = " + ".join(source_parts) if source_parts else None

    ok = bool(available)
    degraded = bool(missing) or bool(
        abstract_res.degraded or valuation_res.degraded or dividend_res.degraded
    )

    if not missing:
        message = "ok"
        reason = None
    elif available:
        message = "部分指标不可用"
        reason = f"{len(missing)}/{len(ordered_cards)} 项不可用：" + "、".join(missing)
    else:
        message = "数据源不可用"
        # 统一 reason：先点名「哪个源不可用」，再给首个具体原因（截断），
        # 逐卡细节仍在 data.cards[*].reason 里，不重复堆叠进信封。
        unreachable = []
        for res in (abstract_res, valuation_res, dividend_res):
            if not res.available and res.source and res.source not in unreachable:
                unreachable.append(res.source)
        reason = (
            f"6/{len(ordered_cards)} 项不可用："
            f"{('、'.join(unreachable) or '全部数据源')} 均不可用"
        )
        first_detail = next((item for item in reasons if item), None)
        if first_detail:
            reason += f"；首个原因：{_clip(first_detail, 150)}"
        reason = _clip(reason, MAX_ENVELOPE_REASON_CHARS)

    as_of = period_iso(report_period) if report_period else None
    data: Dict[str, Any] = {
        "code": prefixed,
        "symbol": symbol,
        "name": name,
        "report_period": report_period,
        "report_period_iso": as_of,
        "report_period_label": period_cn_label(report_period) if report_period else None,
        "report_period_quarter": period_quarter_label(report_period) if report_period else None,
        "is_latest_period": (not report_stale) if report_stale is not None else None,
        "latest_period_is_stale": report_stale,
        # 契约字段：已扣掉「最近 1 个尚未到披露截止日的季度」后的落后季度数
        "period_lag_quarters": behind,
        # 原始口径：报告期距当前已过去多少个季度（不做任何容忍扣减）
        "period_quarters_elapsed": (
            quarters_behind(report_period) if report_period else None
        ),
        "stale_rule": (
            f"报告期落后 >{PERIOD_STALE_ALLOWANCE_QUARTERS + 1} 个季度判为陈旧"
            f"（容忍最近 {PERIOD_STALE_ALLOWANCE_QUARTERS} 个尚未到披露截止日的季度）"
        ),
        "cards": ordered_cards,
        "availability": {
            "available": available,
            "missing": missing,
            "available_count": len(available),
            "total": len(ordered_cards),
        },
        "ttm": {
            "basis": "滚动 4 季聚合（累计值差分，任一季度缺失则整体置 null，不插值）",
            "revenue_ttm": _round_or_none(fin_ctx.get("revenue_ttm"), 2),
            "net_profit_ttm": _round_or_none(fin_ctx.get("profit_ttm"), 2),
            "eps_ttm": _round_or_none(fin_ctx.get("eps_ttm"), 4),
            "roe_ttm_pct": _round_or_none(ordered_cards.get("roe_ttm", {}).get("value"), 4),
        },
        "bps": _round_or_none(fin_ctx.get("bps"), 4),
        "extra_available_periods": (
            int(abstract_res.data.get("available_periods") or 0) - 1
            if abstract_res.available and isinstance(abstract_res.data, dict) else None
        ),
        "valuation": val_ctx or None,
        "dividend": div_ctx or None,
        "report_type": report_type,
        "flag": FEATURE_FLAG_KEY,
    }

    return {
        "ok": ok,
        "data": data,
        "message": message,
        "source": envelope_source,
        "as_of": as_of,
        "age": age,
        "degraded": degraded,
        "reason": reason,
    }
