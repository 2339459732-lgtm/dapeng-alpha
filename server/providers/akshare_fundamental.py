# -*- coding: utf-8 -*-
"""DAPENG Alpha — AkShare 基本面三 Fetcher（新浪 / 百度 / 巨潮）。

## 为什么必须显式绕开 ``*_em``（东财）接口

本机实测（2026-10-19，服务实际解释器 Python311 + akshare 1.18.59）：

===============================  ==========================================  ========
探测项                            结果                                        结论
===============================  ==========================================  ========
``qt.gtimg.cn``（腾讯行情）        200 OK                                      ✅ 可用
``push2.eastmoney.com``（东财）    403                                         ❌ 不可用
``stock_individual_info_em``     JSONDecodeError（403 返回 HTML）             ❌ 禁止使用
``stock_financial_abstract``     ``(80, 105)``：80 指标 × 104 报告期（新浪）   ✅ 主链路
``stock_zh_valuation_baidu``     914 行日频 PE_TTM / PB（百度）               ✅ 主链路
``stock_dividend_cninfo``        31 行分红记录（巨潮）                        ✅ 主链路
``stock_fhps_em``                ChunkedEncodingError                        ❌ 禁止使用
===============================  ==========================================  ========

因此本模块**只用新浪 / 百度 / 巨潮三条非东财链路**，且全文不存在任何 ``*_em`` 调用。
若误用东财系接口，结果会是一个「全 null 的 ``ok:false``」，与「westock 已废弃」
一模一样的失败，只是换了原因。

## 缺数纪律

三个 Fetcher 都**不会抛异常**：超时/异常/空结果统一返回
``SourceResult(data=None, degraded=True, reason=<具体原因>)``。
**绝不返回 0 或空列表冒充数据**（见 ``base.SourceResult`` 文档）。
"""

from __future__ import annotations

import logging
import math
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from server.cache import cache
from server.providers.base import Fetcher, SourceResult, register

logger = logging.getLogger(__name__)

# 失败结果的负缓存时长（秒）：避免上游抖动时每个请求都白等 8s
NEGATIVE_TTL: float = 120.0

# 财务宽表保留的报告期数（4 季 TTM + 去年同期 TTM + 缓冲，12 期足够）
KEEP_PERIODS: int = 12

_SYMBOL_RE = re.compile(r"(\d{6})")
_PERIOD_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

# 报告期 mmdd → 季度序号
_QUARTER_OF_MMDD: Dict[str, int] = {"0331": 1, "0630": 2, "0930": 3, "1231": 4}
_MMDD_OF_QUARTER: Dict[int, str] = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}

# 财务宽表：卡片/聚合所需的 (选项, 指标) 定位表
FIN_METRICS: Dict[str, Tuple[str, str]] = {
    "net_profit_parent": ("常用指标", "归母净利润"),
    "revenue": ("常用指标", "营业总收入"),
    "equity_total": ("常用指标", "股东权益合计(净资产)"),
    "eps_basic": ("常用指标", "基本每股收益"),
    "bps": ("常用指标", "每股净资产"),
    "roe_reported": ("常用指标", "净资产收益率(ROE)"),
    "roa_reported": ("常用指标", "总资产报酬率(ROA)"),
    "gross_margin": ("常用指标", "毛利率"),
    "revenue_yoy": ("成长能力", "营业总收入增长率"),
    "profit_yoy": ("成长能力", "归属母公司净利润增长率"),
}

# 百度估值指标名映射（Fetcher 内部键 → 百度 indicator 参数）
BAIDU_INDICATORS: Dict[str, str] = {
    "pe_ttm": "市盈率(TTM)",
    "pb": "市净率",
    "pe_static": "市盈率(静)",
    "market_cap": "总市值",
}

# years → 百度 period 参数
_BAIDU_PERIODS: Tuple[Tuple[int, str], ...] = (
    (1, "近一年"),
    (3, "近三年"),
    (5, "近五年"),
    (10, "近十年"),
)


# ----------------------------------------------------------------------
#  代码规范化
# ----------------------------------------------------------------------
def symbol_of(code: Any) -> Optional[str]:
    """从任意写法（``sh600519`` / ``600519`` / ``600519.SH``）提取 6 位代码。"""
    match = _SYMBOL_RE.search(str(code or ""))
    return match.group(1) if match else None


def prefixed_code(code: Any) -> Optional[str]:
    """规范化为带市场前缀的小写代码（``sh600519`` / ``sz000001`` / ``bj430047``）。

    无前缀时按 A 股代码段推断：6/9 → 沪，0/3 → 深，4/8 → 北交所。
    """
    raw = str(code or "").strip().lower()
    symbol = symbol_of(raw)
    if not symbol:
        return None
    if raw[:2] in ("sh", "sz", "bj"):
        return raw[:2] + symbol
    head = symbol[0]
    if head in "69":
        return "sh" + symbol
    if head in "03":
        return "sz" + symbol
    if head in "48":
        return "bj" + symbol
    return None


def baidu_period(years: int) -> str:
    """把年数映射为百度接口的 period 参数。"""
    try:
        want = int(years)
    except (TypeError, ValueError):
        want = 5
    for limit, label in _BAIDU_PERIODS:
        if want <= limit:
            return label
    return "近十年"


def _period_key(period: str) -> Optional[Tuple[int, int]]:
    """``"20260630"`` → ``(2026, 2)``。非法格式返回 ``None``。"""
    match = _PERIOD_RE.match(str(period or ""))
    if not match:
        return None
    year = int(match.group(1))
    quarter = _QUARTER_OF_MMDD.get(match.group(2) + match.group(3))
    if quarter is None:
        return None
    return year, quarter


def _to_float(value: Any) -> Optional[float]:
    """把单元格值转成有限 float；``None`` / ``NaN`` / ``"--"`` / ``inf`` 一律返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return num


# ----------------------------------------------------------------------
#  Fetcher 1：财务摘要（新浪）
# ----------------------------------------------------------------------
class FinancialAbstractFetcher(Fetcher):
    """A 股财务摘要（新浪源，宽表 80 指标 × 104 报告期）。

    返回结构::

        {
          "symbol": "600519",
          "latest_period": "20260630",
          "available_periods": 104,
          "periods": ["20260630", "20260331", ...],   # 降序，最多 KEEP_PERIODS 期
          "metrics": {"revenue": {"20260630": 9.2e10, ...}, ...}
        }

    ``metrics`` 的键见 :data:`FIN_METRICS`。TTM 聚合由聚合层
    （``server/fundamental.py``）完成——本类只做「取数 + 结构规范化」。
    """

    name = "akshare_financial_abstract"
    source_label = "AkShare/新浪·财务摘要"
    ttl = 21600.0  # 6h：财报报告期一天至多更新一次

    # --------------------------------------------------------------
    async def fetch(self, code: str = "", **_: Any) -> SourceResult:
        symbol = symbol_of(code)
        if not symbol:
            return SourceResult.unavailable(
                self.source_label, f"无法从代码 {code!r} 解析 6 位股票代码"
            )

        cache_key = f"akfund:v1:abstract:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            result = SourceResult.from_dict(cached)
            result.extra["cache_hit"] = True
            return result

        result = await self.call_upstream(self._load_sync, symbol)
        if result.available:
            result.extra.setdefault("fetched_at", time.time())
            result.extra.setdefault("symbol", symbol)
        ttl = self.ttl if result.available else NEGATIVE_TTL
        cache.set(cache_key, result.to_dict(), ttl)
        return result

    # --------------------------------------------------------------
    def _load_sync(self, symbol: str) -> Dict[str, Any]:
        """同步取数 + 解析（在工作线程中执行）。"""
        import akshare as ak  # 延迟导入：缺失时由 call_upstream 收敛为 degraded

        try:
            frame = ak.stock_financial_abstract(symbol=symbol)
        except (KeyError, IndexError, TypeError) as exc:
            # 新浪对无数据标（如部分北交所代码）返回空响应/`None`，akshare 内部取列时报错
            raise ValueError(
                "新浪财务摘要对该标的返回空表（可能无财报披露或标的不在覆盖范围）"
            ) from exc
        return parse_financial_abstract(frame, symbol)


def parse_financial_abstract(frame: Any, symbol: str) -> Dict[str, Any]:
    """把新浪财务宽表解析为 ``{periods, latest_period, metrics}``。

    Args:
        frame: ``stock_financial_abstract`` 返回的 DataFrame
            （行=指标，列=``选项|指标|YYYYMMDD...``）。
        symbol: 6 位股票代码（写入结果，便于排查）。

    Raises:
        ValueError: 表为空或缺少必要列（由 ``call_upstream`` 收敛为 degraded）。
    """
    if frame is None or getattr(frame, "empty", True):
        raise ValueError("新浪财务摘要返回空表")
    columns = [str(col) for col in frame.columns]
    if "指标" not in columns:
        raise ValueError(f"新浪财务摘要列结构异常：缺少『指标』列，实得 {columns[:6]}")

    periods_all = sorted(
        [col for col in columns if _PERIOD_RE.match(col)], reverse=True
    )
    if not periods_all:
        raise ValueError("新浪财务摘要无任何报告期列（YYYYMMDD）")

    has_group = "选项" in columns
    # 建立 (选项, 指标) → 行号 与 指标 → 行号 两套索引
    exact_index: Dict[Tuple[str, str], int] = {}
    loose_index: Dict[str, int] = {}
    for pos in range(len(frame)):
        row = frame.iloc[pos]
        label = str(row["指标"]).strip()
        group = str(row["选项"]).strip() if has_group else ""
        exact_index.setdefault((group, label), pos)
        loose_index.setdefault(label, pos)

    metrics: Dict[str, Dict[str, Optional[float]]] = {}
    missing: List[str] = []
    for metric, (group, label) in FIN_METRICS.items():
        pos = exact_index.get((group, label))
        if pos is None:
            pos = loose_index.get(label)
        if pos is None:
            missing.append(f"{group}/{label}")
            metrics[metric] = {}
            continue
        row = frame.iloc[pos]
        metrics[metric] = {period: _to_float(row[period]) for period in periods_all}

    # 最新报告期：以「营收/归母净利/每股净资产/每股收益」四个核心字段中出现 >=2 个为准，
    # 避免某些标的最后一列表头存在但全 NaN（新浪偶发）。
    latest_period: Optional[str] = None
    core_keys = ("revenue", "net_profit_parent", "bps", "eps_basic")
    for period in periods_all:
        hits = sum(1 for key in core_keys if metrics.get(key, {}).get(period) is not None)
        if hits >= 2:
            latest_period = period
            break
    if latest_period is None:
        raise ValueError("新浪财务摘要所有报告期的核心字段均为空（无法确定最新报告期）")

    keep = periods_all[:KEEP_PERIODS]
    trimmed = {key: {p: series.get(p) for p in keep} for key, series in metrics.items()}

    return {
        "symbol": symbol,
        "latest_period": latest_period,
        "available_periods": len(periods_all),
        "periods": keep,
        "metrics": trimmed,
        "missing_rows": missing,
    }


# ----------------------------------------------------------------------
#  Fetcher 2：估值日频序列（百度）
# ----------------------------------------------------------------------
class ValuationHistoryFetcher(Fetcher):
    """PE_TTM / PB 日频估值序列（百度源，近五年 914 个交易日）。

    返回结构::

        {
          "symbol": "600519",
          "period_label": "近五年",
          "series": {"pe_ttm": [{"date": "2021-10-19", "value": 46.70}, ...], "pb": [...]},
          "last": {"pe_ttm": {"date": "2026-10-19", "value": 20.34}, "pb": {...}},
          "counts": {"pe_ttm": 914, "pb": 914},
          "failed": {"pb": "原因"}            # 仅分项失败时出现
        }

    ## 取数策略（重要）

    实测百度单指标一次调用耗时 **4.5–5.5s**，但**两路并发时会被互相拖慢到 >8s**
    （多次复现「两路同时超时」）。因此：

    1. **串行取数**：``pe_ttm`` → ``pb``，每路 8s 超时（满足「单次取数 ≤8s」硬约束），
       常态总耗时 ≈ 9.3s，且每路留有 ~40% 余量；
    2. **超时补一次**：超时项再串行补一次（重试预算 1 次），最坏 ≈ 16s；
    3. **按指标独立缓存**：``pe_ttm`` 与 ``pb`` 各存一份（成功 12h / 失败 120s），
       因此部分成功的结果只会在下次补取缺失的那一路，不会重复打两路。

    某一路失败**不影响**另一路：部分成功即返回，``degraded=True`` 并写明缺哪一路。
    """

    name = "akshare_valuation_baidu"
    source_label = "AkShare/百度·估值"
    ttl = 43200.0  # 12h：日频序列盘中不变

    def _indicator_cache_key(self, symbol: str, period_label: str, key: str) -> str:
        """单指标缓存键（含 symbol + 窗口 + 指标，互不覆盖）。"""
        return f"akfund:v1:valuation:{symbol}:{period_label}:{key}"

    # --------------------------------------------------------------
    async def fetch(self, code: str = "", years: int = 5,
                    indicators: Sequence[str] = ("pe_ttm", "pb"),
                    **_: Any) -> SourceResult:
        symbol = symbol_of(code)
        if not symbol:
            return SourceResult.unavailable(
                self.source_label, f"无法从代码 {code!r} 解析 6 位股票代码"
            )

        wanted: List[str] = []
        for key in indicators or ():
            norm = str(key or "").strip().lower()
            if norm and norm in BAIDU_INDICATORS and norm not in wanted:
                wanted.append(norm)
        if not wanted:
            return SourceResult.unavailable(
                self.source_label,
                f"未请求任何可用估值指标（支持：{', '.join(sorted(BAIDU_INDICATORS))}）",
            )

        period_label = baidu_period(years)
        series: Dict[str, List[Dict[str, Any]]] = {}
        last: Dict[str, Dict[str, Any]] = {}
        counts: Dict[str, int] = {}
        failed: Dict[str, str] = {}
        stamps: List[float] = []

        def absorb(key: str, payload: Optional[Dict[str, Any]]) -> bool:
            """把单指标结果并入聚合体；成功返回 True。"""
            if not payload:
                return False
            rows = payload.get("rows") or []
            if not rows:
                failed[key] = payload.get("reason") or "百度返回空序列"
                return False
            series[key] = rows
            last[key] = rows[-1]
            counts[key] = len(rows)
            stamp = payload.get("fetched_at")
            if stamp:
                stamps.append(float(stamp))
            return True

        # 1) 先读按指标缓存
        pending: List[str] = []
        for key in wanted:
            cached = cache.get(self._indicator_cache_key(symbol, period_label, key))
            if cached is None:
                pending.append(key)
                continue
            if not absorb(key, cached):
                failed.setdefault(key, (cached or {}).get("reason") or "取数失败")

        # 2) 串行补取缺失指标（各自 8s 超时）。
        #    为什么不用 asyncio.gather 并发：实测并发两路时百度会互相拖慢，
        #    多次出现「两路同时 8s 超时」；串行时单路稳定 4.5–5.5s，反而更可靠。
        #    串行成本约 9.3s（5 年窗口），与「一次请求拿全两路」的收益相比更划算。
        if pending:
            retry: List[str] = []
            for key in pending:
                res = await self.call_upstream(
                    self._load_one_sync, symbol, key, period_label
                )
                if self._settle(symbol, period_label, key, res, absorb,
                                failed) == "retry":
                    retry.append(key)

            # 3) 首轮超时项再补一次（应对上游延迟抖动）。
            #    重试预算固定为 1 次，把最坏耗时钉在 ≈ 8s + 8s = 16s；
            #    未重试到的指标保持 null + reason，且因「超时不写负缓存」下次请求会再补。
            retry_budget = 1
            for key in retry:
                if retry_budget <= 0:
                    failed.setdefault(
                        key,
                        f"{self.source_label} 首轮超时（{self.timeout:g}s），"
                        f"本轮重试预算已用尽，下次请求将重试",
                    )
                    continue
                retry_budget -= 1
                res = await self.call_upstream(
                    self._load_one_sync, symbol, key, period_label
                )
                if self._settle(symbol, period_label, key, res, absorb,
                                failed) != "ok":
                    failed.setdefault(
                        key, f"{self.source_label} 首轮 + 重试后仍失败"
                    )

        if not series:
            detail = "；".join(f"{key}: {msg}" for key, msg in failed.items())
            return SourceResult.unavailable(
                self.source_label,
                f"百度估值无可用序列（{detail or '全部指标取数失败'}）",
                failure="all_failed",
                failed=failed,
            )

        reason = None
        if failed:
            missing_desc = "；".join(
                f"{key} 不可用（{msg}）" for key, msg in failed.items()
            )
            reason = f"部分估值指标不可用：{missing_desc}"

        return SourceResult(
            data={
                "symbol": symbol,
                "period_label": period_label,
                "series": series,
                "last": last,
                "counts": counts,
                "failed": failed,
            },
            source=self.source_label,
            as_of=max((item.get("date") for item in last.values()), default=None),
            degraded=bool(failed),
            reason=reason,
            extra={
                "fetched_at": min(stamps) if stamps else time.time(),
                "symbol": symbol,
                "cache_hits": len(wanted) - len(pending),
            },
        )

    # --------------------------------------------------------------
    def _settle(self, symbol: str, period_label: str, key: str,
                res: Any, absorb: Any,
                failed: Optional[Dict[str, str]] = None) -> str:
        """整理单指标结果并写入按指标缓存。

        Args:
            symbol: 6 位代码。
            period_label: 百度窗口参数（如 ``近五年``）。
            key: 指标键（``pe_ttm`` / ``pb``）。
            res: ``call_upstream`` 的返回值（可能是异常对象）。
            absorb: 把成功 payload 并入聚合体的回调。
            failed: 失败原因登记表（``{指标键: 原因}``）；失败时写入，
                以便最终信封的 ``reason`` 能指名道姓。

        Returns:
            ``"ok"`` 成功；``"retry"`` 超时类（可串行重试，且不写缓存）；
            ``"failed"`` 真实失败（无覆盖/异常/空序列，已写 120s 负缓存）。
        """
        cache_key = self._indicator_cache_key(symbol, period_label, key)
        bucket = failed if failed is not None else {}

        if isinstance(res, BaseException):
            logger.warning("%s: %s 首轮异常 %s", self.name, key, res.__class__.__name__)
            return "retry"
        if res is None:
            return "retry"

        if res.available:
            payload = dict(res.data or {})
            if payload.get("rows"):
                payload["fetched_at"] = time.time()
                cache.set(cache_key, payload, self.ttl)
                absorb(key, payload)
                return "ok"
            reason = payload.get("reason") or "百度返回空序列"
            payload["reason"] = reason
            payload["fetched_at"] = time.time()
            cache.set(cache_key, payload, NEGATIVE_TTL)
            bucket[key] = reason
            return "failed"

        failure_kind = str((res.extra or {}).get("failure") or "")
        if failure_kind == "timeout":
            # 超时不写缓存：串行重试一次往往能成功（并发限流是常态）
            logger.warning("%s: %s 首轮超时，将串行重试一次", self.name, key)
            return "retry"

        reason = res.reason or "取数失败"
        cache.set(
            cache_key,
            {"rows": [], "reason": reason, "fetched_at": time.time()},
            NEGATIVE_TTL,
        )
        bucket[key] = reason
        return "failed"

    # --------------------------------------------------------------
    def _load_one_sync(self, symbol: str, indicator: str,
                       period_label: str) -> Dict[str, Any]:
        """取单个指标的日频序列（在工作线程中执行）。"""
        import akshare as ak

        param = BAIDU_INDICATORS[indicator]
        try:
            frame = ak.stock_zh_valuation_baidu(
                symbol=symbol, indicator=param, period=period_label
            )
        except KeyError as exc:
            # 百度对无覆盖标的返回缺 chartInfo 的 JSON
            raise ValueError(
                f"百度估值对该标的无覆盖（indicator={param}，chartInfo 缺失）"
            ) from exc

        if frame is None or getattr(frame, "empty", True):
            return {"rows": [], "indicator": indicator}

        columns = [str(col) for col in frame.columns]
        if "date" not in columns or "value" not in columns:
            raise ValueError(f"百度估值列结构异常：{columns[:5]}")

        rows: List[Dict[str, Any]] = []
        for pos in range(len(frame)):
            row = frame.iloc[pos]
            value = _to_float(row["value"])
            date = str(row["date"])[:10]
            if value is None or not _PERIOD_RE.match(date.replace("-", "") or ""):
                continue
            rows.append({"date": date, "value": round(value, 4)})
        rows.sort(key=lambda item: item["date"])
        return {"rows": rows, "indicator": indicator, "param": param}


# ----------------------------------------------------------------------
#  Fetcher 3：分红送配（巨潮）
# ----------------------------------------------------------------------
class DividendFetcher(Fetcher):
    """分红送配记录 + 近 12 个月每股现金分红合计（巨潮源）。

    返回结构::

        {
          "symbol": "600519",
          "ttm_per_share": 5.198,          # 近 12 个月已除权现金分红合计（元/股）
          "ttm_records": 2,
          "last_ex_date": "2026-06-26",
          "window_start": "2025-10-19",
          "window_end": "2026-10-19",
          "records": [{...}, ...]          # 最近 8 条，供前端展示明细
        }

    口径说明：``派息比例`` 单位为「元/10 股」，故 ``每股现金分红 = 派息比例 / 10``。
    只统计**现金分红**（``派息比例``），送股/转增不影响股息率。
    ``ttm_per_share`` 为 ``None`` 表示近 12 个月无现金分红记录（**不是 0**）。
    """

    name = "akshare_dividend_cninfo"
    source_label = "AkShare/巨潮·分红送配"
    ttl = 86400.0  # 24h：分红方案一天至多更新一次

    CASH_TYPES = ("年度分红", "中期分红", "季度分红", "特别分红", "股改分红")
    TTM_DAYS = 365

    # --------------------------------------------------------------
    async def fetch(self, code: str = "", **_: Any) -> SourceResult:
        symbol = symbol_of(code)
        if not symbol:
            return SourceResult.unavailable(
                self.source_label, f"无法从代码 {code!r} 解析 6 位股票代码"
            )

        cache_key = f"akfund:v1:dividend:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            result = SourceResult.from_dict(cached)
            result.extra["cache_hit"] = True
            return result

        result = await self.call_upstream(self._load_sync, symbol)
        if result.available:
            result.extra.setdefault("fetched_at", time.time())
        cache.set(cache_key, result.to_dict(),
                  self.ttl if result.available else NEGATIVE_TTL)
        return result

    # --------------------------------------------------------------
    def _load_sync(self, symbol: str) -> Dict[str, Any]:
        """同步取数 + 解析（在工作线程中执行）。"""
        import akshare as ak

        try:
            frame = ak.stock_dividend_cninfo(symbol=symbol)
        except (KeyError, IndexError, TypeError) as exc:
            # 无分红记录的标的：巨潮返回空响应，akshare 内部取列时报 KeyError。
            # 这是「无分红」而非「接口故障」，必须给出可读原因（值仍为 null，不是 0）。
            raise ValueError(
                "巨潮分红接口对该标的返回空表（通常表示该标的无分红记录）"
            ) from exc
        return parse_dividend(frame, symbol)


def parse_dividend(frame: Any, symbol: str) -> Dict[str, Any]:
    """解析巨潮分红表，算近 12 个月每股现金分红合计。

    Args:
        frame: ``stock_dividend_cninfo`` 返回的 DataFrame。
        symbol: 6 位股票代码。

    Raises:
        ValueError: 表为空或缺少必要列。
    """
    if frame is None or getattr(frame, "empty", True):
        raise ValueError("巨潮分红返回空表（该标的无分红记录）")
    columns = [str(col) for col in frame.columns]
    if "派息比例" not in columns:
        raise ValueError(f"巨潮分红列结构异常：缺少『派息比例』列，实得 {columns[:6]}")

    records: List[Dict[str, Any]] = []
    for pos in range(len(frame)):
        row = frame.iloc[pos]
        cash_per_10 = _to_float(row["派息比例"])
        ex_date_raw = row["除权日"] if "除权日" in columns else None
        ex_date = _normalize_date(ex_date_raw)
        kind = str(row["分红类型"]).strip() if "分红类型" in columns else ""
        plan = str(row["实施方案分红说明"]).strip() if "实施方案分红说明" in columns else ""
        report = str(row["报告时间"]).strip() if "报告时间" in columns else ""
        records.append({
            "kind": kind,
            "cash_per_10": cash_per_10,
            "cash_per_share": (round(cash_per_10 / 10.0, 6)
                               if cash_per_10 is not None else None),
            "ex_date": ex_date,
            "plan": plan,
            "report": report if report and report.lower() != "nan" else None,
        })

    dated = [item for item in records if item["ex_date"]]
    dated.sort(key=lambda item: item["ex_date"])

    today = time.time()
    window_start_ts = today - DividendFetcher.TTM_DAYS * 86400
    window_start = time.strftime("%Y-%m-%d", time.localtime(window_start_ts))
    window_end = time.strftime("%Y-%m-%d", time.localtime(today))

    ttm_items = []
    for item in dated:
        cash = item["cash_per_share"]
        if cash is None or cash <= 0:
            continue
        if item["kind"] and item["kind"] not in DividendFetcher.CASH_TYPES:
            continue
        stamp = _date_to_ts(item["ex_date"])
        if stamp is None:
            continue
        if window_start_ts <= stamp <= today + 86400:
            ttm_items.append(item)

    ttm_per_share = (round(sum(i["cash_per_share"] for i in ttm_items), 6)
                     if ttm_items else None)

    return {
        "symbol": symbol,
        "ttm_per_share": ttm_per_share,
        "ttm_records": len(ttm_items),
        "last_ex_date": dated[-1]["ex_date"] if dated else None,
        "window_start": window_start,
        "window_end": window_end,
        "records": [dict(item) for item in dated[-8:]][::-1],
    }


def _normalize_date(value: Any) -> Optional[str]:
    """把日期单元格规范化为 ``YYYY-MM-DD``；``NaT`` / ``None`` / 空串返回 ``None``。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "nat", "none", "-", "--"):
        return None
    match = re.match(r"^(\d{4})[-/]?(\d{2})[-/]?(\d{2})", text)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _date_to_ts(text: str) -> Optional[float]:
    """``YYYY-MM-DD`` → epoch 秒；非法返回 None。"""
    try:
        return time.mktime(time.strptime(str(text)[:10], "%Y-%m-%d"))
    except (ValueError, TypeError):
        return None


# ----------------------------------------------------------------------
#  注册（进程内单例）
# ----------------------------------------------------------------------
financial_abstract_fetcher = register(FinancialAbstractFetcher())
valuation_fetcher = register(ValuationHistoryFetcher())
dividend_fetcher = register(DividendFetcher())

__all__ = [
    "BAIDU_INDICATORS",
    "DividendFetcher",
    "FIN_METRICS",
    "FinancialAbstractFetcher",
    "ValuationHistoryFetcher",
    "baidu_period",
    "dividend_fetcher",
    "financial_abstract_fetcher",
    "parse_dividend",
    "parse_financial_abstract",
    "prefixed_code",
    "symbol_of",
    "valuation_fetcher",
]
