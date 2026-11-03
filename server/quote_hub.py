# -*- coding: utf-8 -*-
"""DAPENG Alpha — 统一报价中枢（TTL 快照 + single-flight + 陈旧降级）。

设计目标（对齐团队红线）：
- **同一 code 在同一时刻只有一份价**：所有消费方（``/api/kline/quote``、SSE ``quotes``、
  分时校准）都经此中枢取数，因此消费者再多，同一时间窗内只打一次上游。
- **single-flight**：同一 code 的并发请求只允许一个上游请求在飞，其余 await 同一个
  future，避免并发打爆上游、也避免同一时刻出现两个不同价。
- **真实数据 > 如实降级 > 不编造**：上游失败时沿用上一份有效快照并标 ``stale``；
  距上次成功超过 ``STALE_MAX`` 秒后 ``price`` 置 ``None`` + ``reason``，
  **绝不用 0 或旧价冒充实时**。

字段与 ``/api/kline/quote`` 现有 quote 完全一致（code/name/price/open/high/low/
prev_close/chg_pct/volume/amount/turnover/pe/ts/bar_ts/as_of/market_closed/valid/reason），
仅新增 ``stale``（bool）与 ``stale_age``（秒）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 快照新鲜窗口（秒）：窗口内直接命中缓存，**不触发上游**
DEFAULT_MAX_AGE = 1.5
# 陈旧上限（秒）：超过则视为失效，price=None + reason
STALE_MAX = 30.0
# 单次等待共享请求的最长时间（秒），防止上游卡死拖垮调用方
WAIT_TIMEOUT = 8.0


class QuoteHub:
    """报价中枢：进程内单例，供所有消费方共享。"""

    def __init__(self, max_age: float = DEFAULT_MAX_AGE,
                 stale_max: float = STALE_MAX) -> None:
        self._max_age = float(max_age)
        self._stale_max = float(stale_max)
        # code -> (最近一次成功取数的时间戳, 腾讯原始行情行)
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        # code -> 正在飞的上游请求（single-flight）
        self._inflight: Dict[str, "asyncio.Future[Optional[Dict[str, Any]]]"] = {}
        self.stats: Dict[str, Any] = {
            "upstream_calls": 0,          # 实际请求上游的次数（一次批量记 1）
            "cache_hits": 0,              # 命中新鲜窗口的 code 次数
            "stale_served": 0,            # 以陈旧快照（>max_age 且 <=stale_max）应答的次数
            "expired": 0,                 # 超过 stale_max 被判失效（price=None）的次数
            "codes_cached": 0,            # 当前缓存中的 code 数
            "last_error": None,           # 最近一次上游错误
            "last_ok_at": None,           # 最近一次成功时间
            "consecutive_failures": 0,    # 连续失败轮次（上游）
        }

    # ------------------------------------------------------------------
    #  对外接口
    # ------------------------------------------------------------------
    async def snapshot(self, codes: List[str], max_age: Optional[float] = None
                       ) -> Dict[str, Dict[str, Any]]:
        """批量取报价快照（签名保持不变）。

        Returns:
            ``{code: quote}``。每个 quote 含 ``stale`` / ``stale_age`` 字段。
        """
        out, _delta = await self.snapshot_ex(codes, max_age=max_age)
        return out

    async def snapshot_ex(self, codes: List[str], max_age: Optional[float] = None
                          ) -> Tuple[Dict[str, Dict[str, Any]], int]:
        """批量取报价快照，并返回**本轮真实上游请求次数**。

        与 ``snapshot()`` 的区别：额外返回 ``delta``（0 或 1），表示本次调用
        是否真的打了上游。调用方（如 SSE 发布循环）应累加该 delta 而不是去读
        全局计数器的前后差——否则落在 ``await snapshot()`` 窗口内的其它消费者
        （REST 轮询）增量会被错误记到自己头上，窗口边界还会双记。

        Args:
            codes: 标的代码列表（sh600519 / sz000858 …，大小写不敏感）。
            max_age: 覆盖默认新鲜窗口（秒）；None 用实例默认值。

        Returns:
            ``(quotes, upstream_delta)``。
        """
        wanted: List[str] = []
        for raw in codes or []:
            c = str(raw or "").strip().lower()
            if c and c not in wanted:
                wanted.append(c)
        if not wanted:
            return {}, 0

        window = self._max_age if max_age is None else float(max_age)
        now = time.time()
        out: Dict[str, Dict[str, Any]] = {}
        missing: List[str] = []

        for c in wanted:
            ent = self._cache.get(c)
            if ent is not None and (now - ent[0]) <= window:
                self.stats["cache_hits"] += 1
                out[c] = self._compose(c, ent[1], ent[0])
            else:
                missing.append(c)

        delta = 0
        if missing:
            fetched, delta = await self._fetch(missing)
            out.update(fetched)

        self.stats["codes_cached"] = len(self._cache)
        return out, delta

    async def get(self, code: str, max_age: Optional[float] = None
                  ) -> Optional[Dict[str, Any]]:
        """单只标的快照（便捷方法）。"""
        snap = await self.snapshot([code], max_age=max_age)
        return snap.get(str(code or "").strip().lower())

    # ------------------------------------------------------------------
    #  上游取数（single-flight）
    # ------------------------------------------------------------------
    async def _fetch(self, codes: List[str]) -> Tuple[Dict[str, Dict[str, Any]], int]:
        """取数并返回 ``(quotes, upstream_calls)``。

        ``upstream_calls`` 是本轮**由本次调用真正发起**的上游请求次数（0/1）；
        等待他人 single-flight 结果的 code 不计入（它们没有产生上游流量）。
        """
        loop = asyncio.get_running_loop()
        shared: Dict[str, "asyncio.Future[Optional[Dict[str, Any]]]"] = {}
        to_request: List[str] = []

        for c in codes:
            fut = self._inflight.get(c)
            if fut is not None and not fut.done():
                shared[c] = fut          # 已有请求在飞：共享结果，不再打上游
            else:
                fut = loop.create_future()
                self._inflight[c] = fut
                shared[c] = fut
                to_request.append(c)

        calls = 0
        if to_request:
            rows: List[Dict[str, Any]] = []
            error: Optional[str] = None
            try:
                # 延迟导入：避免与 server.routers.stream 形成模块级循环依赖
                from server.realtime import fetch_tencent_quotes
                self.stats["upstream_calls"] += 1
                calls += 1
                rows = await fetch_tencent_quotes(to_request, timeout=4.0) or []
                if not rows:
                    error = "上游返回空数据"
            except Exception as exc:  # noqa: BLE001 —— 上游任何异常都降级，不抛出
                error = f"{type(exc).__name__}: {exc}"
                logger.warning("quote_hub 上游取数失败: %s", error)

            rowmap: Dict[str, Dict[str, Any]] = {}
            for r in rows:
                if not r:
                    continue
                key = str(r.get("code") or "").strip().lower()
                if key:
                    rowmap[key] = r

            now_ts = time.time()
            if rowmap:
                self.stats["last_ok_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.stats["consecutive_failures"] = 0
                self.stats["last_error"] = None
            else:
                self.stats["last_error"] = error or "上游无有效行情"
                self.stats["consecutive_failures"] += 1

            for c in to_request:
                row = rowmap.get(c)
                if row:
                    # 只缓存成功行情行；失败不覆盖旧快照（保留上一份有效价）
                    self._cache[c] = (now_ts, row)
                fut = self._inflight.pop(c, None)
                if fut is not None and not fut.done():
                    fut.set_result(row)

        out: Dict[str, Dict[str, Any]] = {}
        for c, fut in shared.items():
            row: Optional[Dict[str, Any]] = None
            try:
                row = await asyncio.wait_for(asyncio.shield(fut), timeout=WAIT_TIMEOUT)
            except Exception:  # noqa: BLE001 —— 超时/取消都按「本次无新数据」处理
                row = None
            ent = self._cache.get(c)
            if ent is not None:
                out[c] = self._compose(c, ent[1], ent[0])
            else:
                out[c] = self._compose(
                    c, None, None,
                    reason="行情源不可达，且无历史快照",
                )
        return out, calls

    # ------------------------------------------------------------------
    #  快照组装（复用 stream._build_quote，保持字段结构唯一真相）
    # ------------------------------------------------------------------
    def _compose(self, code: str, row: Optional[Dict[str, Any]],
                 ts: Optional[float], reason: Optional[str] = None
                 ) -> Dict[str, Any]:
        from server.services.quote_view import build_quote

        now = datetime.now()
        quote = build_quote(code, row, "day", now)
        age: Optional[float] = None
        if ts is not None:
            age = max(0.0, time.time() - ts)

        if row is None or age is None:
            quote["stale"] = True
            quote["stale_age"] = None
            quote["valid"] = False
            quote["reason"] = reason or quote.get("reason") or "尚无成功快照"
            return quote

        if age > self._stale_max:
            # 超过陈旧上限：如实置空，不用旧价冒充实时
            self.stats["expired"] += 1
            quote.update({
                "price": None,
                "chg_pct": None,
                "valid": False,
                "stale": True,
                "stale_age": round(age, 1),
                "reason": f"行情源 {int(self._stale_max)} 秒无更新",
            })
            return quote

        is_stale = age > self._max_age
        if is_stale:
            self.stats["stale_served"] += 1
        quote["stale"] = is_stale
        quote["stale_age"] = round(age, 1)
        return quote

    def cached_meta(self, code: str) -> Optional[Dict[str, Any]]:
        """只读：某 code 的缓存快照元信息（**不触发上游**）。

        供 /api/automation/positions 标注价格新鲜度用；未缓存返回 None。
        """
        c = str(code or "").strip().lower()
        ent = self._cache.get(c)
        if ent is None:
            return None
        age = max(0.0, time.time() - ent[0])
        row = ent[1] or {}
        try:
            price = float(row.get("price"))
        except (TypeError, ValueError):
            price = None
        if price is not None and price <= 0:
            price = None
        return {
            "code": c,
            "price": price,
            "age": round(age, 3),
            "stale": age > self._max_age,
            "stale_age": round(age, 3),
            "expired": age > self._stale_max,
            "source": "腾讯实时（QuoteHub）",
        }

    # ------------------------------------------------------------------
    #  自检 / 诊断
    # ------------------------------------------------------------------
    def stats_snapshot(self) -> Dict[str, Any]:
        """计数器快照（供 ``/api/stream/stats`` 与诊断使用）。"""
        return {
            **self.stats,
            "codes_cached": len(self._cache),
            "inflight": len(self._inflight),
            "ttl_seconds": self._max_age,
            "stale_max_seconds": self._stale_max,
            "cached_codes": sorted(self._cache.keys()),
        }


# 全局单例
quote_hub = QuoteHub()
