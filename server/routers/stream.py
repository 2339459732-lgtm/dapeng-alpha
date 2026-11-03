# -*- coding: utf-8 -*-
"""DAPENG Alpha 行情与信号实时推送（SSE）。

新增端点（不塞进 admin.py / quant.py，避免与他人改动冲突）：
  GET /api/stream?topics=market,signals   合并订阅（默认 market,signals）
  GET /api/stream/market                   市场级：指数快照 / 涨跌家数 breadth / 板块排行前 N / 外部环境摘要
  GET /api/stream/signals                  信号级：新信号 / 下单-成交-拒单 / 持仓变动 / 账户快照
  GET /api/stream/stats                    订阅数与上游取数计数（验收证据用）

设计约束（对齐本项目信封约定与团队红线）：
- 使用 ``fastapi.responses.StreamingResponse`` + ``media_type="text/event-stream"``。
- 每个事件都带 ``event:`` 名称与 ``data:`` JSON，JSON 内含
  ``source`` / ``updated_at`` / ``degraded`` 溯源字段。
- 心跳：空闲 ``HEARTBEAT_SECONDS`` 秒发送 SSE 注释行 ``: ping``，防代理断连。
- 单连接队列 ``maxsize=MAX_QUEUE``；满时丢最旧并计数（日志与 /stats 可见）。
- **单一共享发布任务**：所有客户端共享同一个后台发布协程，上游刷新次数与
  连接数无关（2 个客户端 ≠ 2 倍上游请求）。发布任务只从服务自身缓存/内存/本地
  SQLite 读取；缓存缺失时推 ``data=null`` + ``reason``，绝不因推送触发上游请求。
- 多客户端各自独立队列；单个客户端异常/慢消费不影响其他订阅者。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from server.cache import cache
from server.quote_hub import quote_hub
from server.core.timeutil import now_iso as _now_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stream", tags=["stream"])

# ──────────────────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────────────────
MARKET_CACHE_KEY = "market_dashboard:v3"   # 与 routers/market_dashboard.py 同源
TICK_SECONDS = 3.0                          # 发布循环节拍
POLL_SECONDS = 1.0                          # 单连接断开探测粒度（≤2s，空闲也不漏检）
HEARTBEAT_SECONDS = 15.0                    # SSE 心跳间隔（空闲时）
MAX_QUEUE = 100                             # 单连接队列上限
SECTOR_TOP_N = 8                            # 板块排行前 N
SIGNAL_PAGE = 20                            # 每次轮询信号的条数
ORDER_PAGE = 20                             # 每次轮询订单的条数
SEEN_LIMIT = 800                            # 去重集合上限
QUOTES_TICK_SECONDS = 3.0                   # 盘中报价推送节拍
QUOTES_CLOSED_SECONDS = 30.0                # 休市/收盘节拍（低频，但仍如实推送末次快照）
MAX_QUOTE_CODES = 20                        # 单连接最多订阅标的数（防滥用）
QUOTE_FAIL_THRESHOLD = 3                    # 上游连续失败达到该轮次 → 推 quotes.unavailable

VALID_TOPICS = ("market", "signals", "quotes")
_CODE_RE = None  # 延迟导入，避免与 trading 模块循环依赖


def _norm_codes(raw: str):
    """解析 ?codes=sh600519,sz000858 → 去重小写合法代码列表（最多 MAX_QUOTE_CODES）。"""
    import re
    global _CODE_RE
    if _CODE_RE is None:
        _CODE_RE = re.compile(r"^(sh|sz|hk)\d{5,6}$")
    out, seen = [], set()
    for c in (raw or "").split(","):
        c = c.strip().lower()
        if _CODE_RE.match(c) and c not in seen:
            seen.add(c)
            out.append(c)
        if len(out) >= MAX_QUOTE_CODES:
            break
    return out


def _envelope(
    topic: str,
    event: str,
    data: Any,
    source: Optional[str],
    updated_at: Optional[str],
    degraded: bool,
    reason: Optional[str] = None,
    seq: Optional[int] = None,
) -> Dict[str, Any]:
    """统一事件信封：data JSON 内含 source / updated_at / degraded 溯源字段。

    ``seq``：进程内全局单调递增序号（仅行情类事件携带），前端据此丢弃
    seq 回退或重复的乱序/重放事件。
    """
    return {
        "topic": topic,
        "event": event,
        "seq": seq,
        "source": source or "unavailable",
        "updated_at": updated_at or _now_iso(),
        "degraded": bool(degraded),
        "reason": reason,
        "server_time": _now_iso(),
        "data": data,
    }


def _sse(event_name: str, payload: Dict[str, Any]) -> str:
    """序列化为一条 SSE 消息（event + data）。"""
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ──────────────────────────────────────────────────────────
# 外部环境（linkage）只读摘要 —— 从内存缓存读取，不触发计算/上游
# ──────────────────────────────────────────────────────────
def _read_linkage_summary() -> Dict[str, Any]:
    """读取 global_linkage 内存缓存（只读）。缺失返回 available=False + reason。"""
    data: Optional[Dict[str, Any]] = None
    try:
        from server import global_linkage as gl
        data = gl._linkage_cache.get("data")  # noqa: SLF001 —— 只读内存缓存，不触发上游
    except Exception as exc:  # pragma: no cover - 防御式
        logger.debug("read linkage cache failed: %s", exc)
        data = None

    if not data:
        return {
            "available": False,
            "reason": "外部环境尚未计算（global_linkage 内存缓存为空）",
            "scorecard": None,
        }
    scorecard = data.get("scorecard") or {}
    items = scorecard.get("items") or []
    return {
        "available": True,
        "as_of_date": data.get("as_of_date"),
        "generated_at": data.get("generated_at"),
        "composite_score": scorecard.get("composite_score"),
        "stance": scorecard.get("stance"),
        "components": scorecard.get("components"),
        "top_items": items[:5],
    }


def _norm_period(raw: Optional[str]) -> str:
    """归一化 K 线周期：day/week/month 原样；分钟类（60/30/15/5）返回数字串。"""
    p = (raw or "day").strip().lower()
    if p in ("day", "week", "month"):
        return p
    try:
        n = int(p)
    except (TypeError, ValueError):
        return "day"
    return str(n) if n > 0 else "day"


# 行情视图模型已下沉到 services/quote_view.py（纯函数，无路由依赖）；
# 这里保留同名别名，避免影响本模块内部与历史引用。
from server.services.quote_view import (  # noqa: E402
    bar_ts as _bar_ts,
    build_quote as _build_quote,
    market_open as _market_open,
    opt_num as _opt_num,
    opt_num_any as _opt_num_any,
)


def _market_signature(payload: Dict[str, Any], updated_at: str) -> str:
    """市场快照变更指纹：用于去重，避免同内容反复推送。"""
    breadth = payload.get("breadth") or {}
    coverage = payload.get("coverage") or {}
    index_chgs = [item.get("chg") for item in (payload.get("indices") or [])]
    return "|".join([
        str(updated_at),
        str(breadth.get("up")), str(breadth.get("down")), str(breadth.get("limit_up")),
        str(coverage.get("valid")),
        ",".join(str(x) for x in index_chgs),
    ])


# ──────────────────────────────────────────────────────────
# 订阅者
# ──────────────────────────────────────────────────────────
class _Subscriber:
    """单个 SSE 连接：拥有独立队列与丢弃计数。"""

    __slots__ = ("sid", "topics", "queue", "dropped", "codes", "period")

    def __init__(self, sid: int, topics: Set[str], codes: Optional[List[str]] = None,
                 period: str = "day") -> None:
        self.sid = sid
        self.topics = topics
        self.codes = list(codes or [])
        self.period = period or "day"
        self.queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=MAX_QUEUE)
        self.dropped = 0


# ──────────────────────────────────────────────────────────
# 发布中枢
# ──────────────────────────────────────────────────────────
class StreamHub:
    """行情/信号推送中枢：单一共享发布任务 + 每连接独立队列。

    上游刷新只发生在**唯一**的发布协程内，且仅在缓存缺失时；因此上游取数
    次数与订阅连接数无关（``/api/stream/stats`` 可证）。
    """

    def __init__(self) -> None:
        self._subs: Dict[int, _Subscriber] = {}
        self._next_id = 0
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._last_events: Dict[str, Dict[str, Any]] = {}
        self._last_market_sig: Optional[str] = None
        self._signals_sig: Optional[str] = None
        self._signals_initialized = False
        self._seen_signal_ids: Set[str] = set()
        self._seen_order_ids: Set[str] = set()
        self._seq = 0                     # 全局单调事件序号（行情类事件用）
        self._quote_fail_streak = 0       # 报价上游连续失败轮次
        self._quote_unavailable = False   # 当前是否处于「已宣告不可用」状态
        self.stats: Dict[str, Any] = {
            "market_cache_hits": 0,          # 命中缓存（未触发上游）
            "market_refresh_invocations": 0,  # 缓存缺失时触发的共享刷新（上游次数来源）
            "signal_polls": 0,                # 信号轮询次数（本地 SQLite）
            "quote_polls": 0,                 # 报价发布轮次
            "quote_upstream_calls": 0,        # 报价上游取数次数（与订阅数无关的共享刷新）
            "clients_total": 0,               # 累计连接数（历史总数，**非在线数**；在线数看 active_clients）
            "clients_total_ever": 0,          # 同上，语义明确的别名
            "active_clients": 0,              # 当前在线订阅数（每次 stats 快照动态计算）
            "events_broadcast": 0,            # 累计广播事件数
            "events_dropped": 0,              # 累计丢弃事件数（队列满）
            "started_at": None,
        }

    # ---------- 订阅管理 ----------
    async def subscribe(self, topics: Set[str], codes: Optional[List[str]] = None,
                        period: str = "day") -> _Subscriber:
        async with self._lock:
            self._next_id += 1
            sub = _Subscriber(self._next_id, topics, codes, period)
            # 预热：把最近一次快照直接放进新连接队列，连接即可见当前状态。
            # quotes 主题的「最近事件」是全局单槽（不分 codes/period），必须过滤，
            # 否则新连接会收到其它标的的旧事件（串码）。
            for topic in topics:
                ev = self._last_events.get(topic)
                if ev is None:
                    continue
                if topic == "quotes" and not self._preheat_matches(sub, ev):
                    continue
                self._put(sub, ev)
            self._subs[sub.sid] = sub
            self.stats["clients_total"] += 1        # 累计连接数（历史总数）
            self.stats["clients_total_ever"] += 1   # 语义明确别名，同上
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run())
                self.stats["started_at"] = _now_iso()
            logger.info("SSE subscribe: sid=%s topics=%s codes=%s active=%d",
                        sub.sid, sorted(topics), sub.codes, len(self._subs))
        return sub

    @staticmethod
    def _preheat_matches(sub: _Subscriber, ev: Dict[str, Any]) -> bool:
        """quotes 预热事件是否属于该订阅：本订阅 codes 非空 + 事件 codes 全为子集 + period 相同。

        不满足则跳过预热（客户端 3s 内会收到自己的首包，不会造成空白），
        避免把其它标的的旧报价/降级事件塞给新连接。
        """
        if not sub.codes:
            return False
        data = ev.get("data") or {}
        codes = data.get("codes")
        if not isinstance(codes, list) or not codes:
            return False                      # 无 codes 明细 → 无法确认归属，不预热
        sub_set = {str(c).lower() for c in sub.codes}
        for c in codes:
            if str(c).lower() not in sub_set:
                return False
        return _norm_period(data.get("period") or "day") == _norm_period(sub.period)

    async def unsubscribe(self, sub: _Subscriber) -> None:
        async with self._lock:
            self._subs.pop(sub.sid, None)
            if not self._subs and self._task is not None and not self._task.done():
                self._task.cancel()
                self._task = None
            logger.info("SSE unsubscribe: sid=%s active=%d dropped=%d",
                        sub.sid, len(self._subs), sub.dropped)

    @property
    def active_clients(self) -> int:
        return len(self._subs)

    def next_seq(self) -> int:
        """进程内全局单调递增事件序号（心跳与行情事件共用一条序列）。"""
        self._seq += 1
        return self._seq

    def stats_snapshot(self) -> Dict[str, Any]:
        return {
            **self.stats,
            "active_clients": len(self._subs),
            "seq": self._seq,
            "quote_fail_streak": self._quote_fail_streak,
            "quote_unavailable": self._quote_unavailable,
            "quote_hub": quote_hub.stats_snapshot(),
            "subscribers": [
                {"sid": s.sid, "topics": sorted(s.topics), "dropped": s.dropped, "queued": s.queue.qsize()}
                for s in self._subs.values()
            ],
        }

    # ---------- 队列写入 ----------
    def _put(self, sub: _Subscriber, event: Dict[str, Any]) -> None:
        try:
            sub.queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            # 丢最旧，保最新
            try:
                sub.queue.get_nowait()
                sub.dropped += 1
                self.stats["events_dropped"] += 1
            except asyncio.QueueEmpty:  # pragma: no cover - 竞态保护
                pass
            logger.warning("SSE queue full: sid=%s 丢弃最旧事件（累计 %d）", sub.sid, sub.dropped)
        try:
            sub.queue.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover - 竞态保护
            pass

    def _broadcast(self, topic: str, event: Dict[str, Any]) -> None:
        """广播到所有订阅该 topic 的连接；单个异常不影响其他订阅者。"""
        self.stats["events_broadcast"] += 1
        for sub in list(self._subs.values()):
            if topic in sub.topics:
                try:
                    self._put(sub, event)
                except Exception:  # pragma: no cover - 防御式
                    logger.warning("SSE broadcast to sid=%s failed", sub.sid, exc_info=True)

    def _has_topic(self, topic: str) -> bool:
        return any(topic in s.topics for s in self._subs.values())

    # ---------- 发布循环（market 与 signals 各自独立，互不阻塞） ----------
    async def _run(self) -> None:
        logger.info("SSE hub publish loops started")
        try:
            # 三个 topic 各有一个共享发布协程：任一 topic 的慢取数不阻塞其它 topic
            await asyncio.gather(self._market_loop(), self._signals_loop(), self._quotes_loop())
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - 防御式
            logger.warning("SSE hub publish loop crashed", exc_info=True)
        finally:
            logger.info("SSE hub publish loops stopped")

    async def _market_loop(self) -> None:
        """市场级共享发布循环：所有 market 订阅者共享（上游取数与连接数无关）。"""
        while self._subs:
            if self._has_topic("market"):
                try:
                    await self._publish_market()
                except Exception:  # pragma: no cover - 防御式
                    logger.warning("SSE market publish failed", exc_info=True)
                await asyncio.sleep(TICK_SECONDS)
            else:
                await asyncio.sleep(1.0)

    async def _signals_loop(self) -> None:
        """信号级共享发布循环：本地 SQLite 读取，慢取数不影响 market。"""
        while self._subs:
            if self._has_topic("signals"):
                try:
                    await self._publish_signals()
                except Exception:  # pragma: no cover - 防御式
                    logger.warning("SSE signals publish failed", exc_info=True)
                await asyncio.sleep(TICK_SECONDS)
            else:
                await asyncio.sleep(1.0)

    async def _quotes_loop(self) -> None:
        """报价级共享发布循环：盘中 3s、休市降频 30s（休市也如实推末次快照，不断流）。"""
        while self._subs:
            if self._has_topic("quotes"):
                open_now = _market_open()
                try:
                    await self._publish_quotes()
                except Exception:  # pragma: no cover - 防御式
                    logger.warning("SSE quotes publish failed", exc_info=True)
                await asyncio.sleep(QUOTES_TICK_SECONDS if open_now else QUOTES_CLOSED_SECONDS)
            else:
                await asyncio.sleep(1.0)

    # ---------- quotes（个股实时报价，供 K 线最后一根动态更新） ----------
    async def _publish_quotes(self) -> None:
        """所有 quotes 订阅者共享一次上游取数（取数次数与连接数无关）。"""
        subs = [s for s in list(self._subs.values()) if "quotes" in s.topics]
        if not subs:
            return
        codes: List[str] = []
        for s in subs:
            for c in s.codes:
                if c not in codes:
                    codes.append(c)
        if not codes:
            event = _envelope(
                "quotes", "quotes.tick", {"quotes": [], "codes": [], "market_open": _market_open()},
                "none", _now_iso(), True, "未指定 codes 参数，无法订阅报价",
            )
            for s in subs:
                self._put(s, event)
            return

        # 统一报价中枢：TTL 快照 + single-flight —— 消费者再多也只打一次上游，
        # 且同一时刻 kline/quote 与 SSE 拿到的是同一份价（一致性）。
        # 计数口径：用 snapshot_ex 返回的**本轮真实上游次数**，
        # 不读全局计数器前后差（否则窗口内其它消费者的增量会被误记，边界还会双记）。
        try:
            snap, upstream_delta = await quote_hub.snapshot_ex(codes)
        except Exception as exc:  # pragma: no cover - 防御式
            logger.warning("quote_hub snapshot failed: %s", exc)
            snap, upstream_delta = {}, 0
        # 两个计数在同一个同步段内自增（中间无 await），避免 /stats 读到
        # 「polls 已加、upstream 未加」的中间态，保证稳定窗口内两数一致。
        self.stats["quote_polls"] += 1
        if upstream_delta > 0:
            self.stats["quote_upstream_calls"] += upstream_delta

        now = datetime.now()
        valid_codes = [c for c in codes if (snap.get(c) or {}).get("price") is not None]
        hard_fail = not valid_codes          # 全部标的都没有有效价 → 本轮算失败

        recovered = False
        if hard_fail:
            self._quote_fail_streak += 1
        else:
            recovered = self._quote_unavailable
            self._quote_fail_streak = 0

        if hard_fail:
            reason: Optional[str] = (
                f"实时行情源连续失败 {self._quote_fail_streak} 次，本轮无可信报价"
                if self._quote_fail_streak >= QUOTE_FAIL_THRESHOLD
                else "实时行情源本次未返回有效报价"
            )
        elif len(valid_codes) < len(codes):
            miss = [c for c in codes if c not in valid_codes]
            reason = f"部分标的无有效实时价：{','.join(miss)}"
        else:
            reason = None
        degraded = hard_fail or len(valid_codes) < len(codes)
        source = "tencent" if valid_codes else "unavailable"

        # 事件类型：连续失败≥3 推 quotes.unavailable；恢复后先推一条 quotes.snapshot 供重建
        if hard_fail and self._quote_fail_streak >= QUOTE_FAIL_THRESHOLD:
            event_name = "quotes.unavailable"
            self._quote_unavailable = True
        elif recovered:
            event_name = "quotes.snapshot"
            self._quote_unavailable = False
        else:
            event_name = "quotes.tick"

        seq = self.next_seq()
        # 按订阅周期分组构造 bar_ts（日线=今日，分钟线=当前周期起点），只对同周期连接下发
        grouped: Dict[str, List[_Subscriber]] = {}
        for s in subs:
            grouped.setdefault(_norm_period(s.period), []).append(s)
        for period, slist in grouped.items():
            quotes: List[Dict[str, Any]] = []
            for c in codes:
                q = snap.get(c)
                if not q:
                    continue
                item = dict(q)                       # 浅拷贝：不污染中枢缓存
                item["bar_ts"] = _bar_ts(now, period)  # 按订阅周期对齐 bar 时间戳
                quotes.append(item)
            payload = {
                "quotes": quotes,
                "codes": codes,
                "period": period,
                "market_open": _market_open(now),
                "fail_streak": self._quote_fail_streak,
            }
            updated_at = quotes[0]["ts"] if quotes else _now_iso()
            event = _envelope("quotes", event_name, payload, source, updated_at,
                              degraded, reason, seq=seq)
            self._last_events["quotes"] = event
            for s in slist:
                self._put(s, event)

    # ---------- market ----------
    async def _publish_market(self, force_emit: bool = False) -> None:
        """市场级推送：从缓存读取；缓存缺失时由共享发布任务统一刷新一次。"""
        data: Optional[Dict[str, Any]] = None
        source: Optional[str] = None
        reason: Optional[str] = None
        degraded = False

        cached = cache.get(MARKET_CACHE_KEY)
        if cached is not None:
            self.stats["market_cache_hits"] += 1
            data = cached
            source = cached.get("source") or "cache:market_dashboard"
        else:
            # 唯一的上游刷新点：整个进程共享，与连接数无关
            self.stats["market_refresh_invocations"] += 1
            try:
                from server.routers import market_dashboard as md
                env = await md.get_market_dashboard()
            except Exception as exc:  # pragma: no cover - 防御式
                env = None
                reason = f"大盘缓存刷新异常：{exc}"
            if env and env.get("ok") and env.get("data"):
                data = env["data"]
                source = data.get("source") or "market-dashboard"
            else:
                degraded = True
                reason = reason or (env or {}).get("message") or "大盘缓存不可用"

        if data is None:
            degraded = True
            reason = reason or "大盘缓存为空（尚无成功快照落缓存）"
            payload: Any = None
            updated_at = _now_iso()
        else:
            # 浅拷贝后附加派生字段，避免污染缓存中的 dashboard 快照
            payload = dict(data)
            payload["sectors_top"] = (data.get("sectors") or [])[:SECTOR_TOP_N]
            payload["external_env"] = _read_linkage_summary()
            updated_at = data.get("as_of") or _now_iso()
            if data.get("stale"):
                degraded = True
                reason = reason or "上游停更或本次取数失败，沿用上一次成功快照"

        event = _envelope("market", "market.snapshot", payload, source, updated_at, degraded, reason)
        signature = _market_signature(payload or {}, updated_at)
        if force_emit or signature != self._last_market_sig:
            self._last_market_sig = signature
            self._last_events["market"] = event
            self._broadcast("market", event)

    # ---------- signals ----------
    async def _publish_signals(self, force_emit: bool = False) -> None:
        """信号/交易推送：从本地 SQLite 读取；只读内存/DB，不触发上游。"""
        self.stats["signal_polls"] += 1

        try:
            from server.trading import trading_facade
            await trading_facade.ensure_ready()
            store = trading_facade.store
        except Exception as exc:
            event = _envelope(
                "signals", "signals.unavailable", None,
                "trading_store", _now_iso(), True, f"交易存储未就绪：{exc}",
            )
            self._broadcast("signals", event)
            return

        reason: Optional[str] = None
        degraded = False

        try:
            _, signals = await store.list_signals(limit=SIGNAL_PAGE)
        except Exception as exc:  # pragma: no cover - 防御式
            signals = []
            degraded = True
            reason = f"信号读取失败：{exc}"

        try:
            _, orders = await store.list_orders(limit=ORDER_PAGE)
        except Exception as exc:  # pragma: no cover - 防御式
            orders = []
            degraded = True
            reason = f"订单读取失败：{exc}"

        try:
            positions = await store.list_positions("paper")
        except Exception as exc:  # pragma: no cover - 防御式
            positions = []
            degraded = True
            reason = f"持仓读取失败：{exc}"

        pos_view = [
            {
                "code": p.code, "name": p.name, "qty": p.qty,
                "avg_cost": p.avg_cost, "last_price": p.last_price,
                "pnl_pct": p.pnl_pct, "gateway": p.gateway,
                "strategy_id": p.strategy_id, "buy_date": p.buy_date,
                "updated_at": p.updated_at,
            }
            for p in positions
        ]

        # ---- 去重差分 ----
        new_signals: List[Dict[str, Any]] = []
        for s in reversed(signals):  # 由旧到新
            sid = s.get("id")
            if sid and sid not in self._seen_signal_ids:
                self._seen_signal_ids.add(sid)
                new_signals.append(s)
        self._prune(self._seen_signal_ids)

        new_orders: List[Dict[str, Any]] = []
        for o in reversed(orders):
            oid = o.get("id")
            if oid and oid not in self._seen_order_ids:
                self._seen_order_ids.add(oid)
                new_orders.append(o)
        self._prune(self._seen_order_ids)

        pos_sig = "|".join(sorted(
            f"{p['code']}:{p['qty']}:{round(float(p['avg_cost']), 3)}:{round(float(p['last_price'] or 0), 3)}"
            for p in pos_view
        ))
        newest_sig_id = signals[0].get("id") if signals else None
        newest_order_id = orders[0].get("id") if orders else None
        newest_ts = (signals[0].get("ts") if signals else None) or (orders[0].get("ts") if orders else None)

        changed = (
            force_emit
            or not self._signals_initialized
            or pos_sig != (self._signals_sig or "")
            or bool(new_signals)
            or bool(new_orders)
        )
        if not changed:
            return

        snapshot = {
            "signals": [_compact_signal(s) for s in signals[:SIGNAL_PAGE]],
            "orders": [_compact_order(o) for o in orders[:ORDER_PAGE]],
            "positions": pos_view,
            "counts": {
                "signals": len(signals),
                "orders": len(orders),
                "positions": len(pos_view),
            },
            "new_signal_ids": [s.get("id") for s in new_signals] if self._signals_initialized else [],
            "new_order_ids": [o.get("id") for o in new_orders] if self._signals_initialized else [],
        }
        self._signals_sig = pos_sig + "|" + str(newest_sig_id) + "|" + str(newest_order_id)

        snapshot_event = _envelope(
            "signals", "signals.snapshot", snapshot,
            "trading.sqlite", newest_ts or _now_iso(), degraded, reason,
        )
        self._last_events["signals"] = snapshot_event
        self._broadcast("signals", snapshot_event)

        # 增量离散事件（首轮只发快照，避免历史刷屏）
        if self._signals_initialized:
            for s in new_signals:
                self._broadcast("signals", _envelope(
                    "signals", "signal.new", _compact_signal(s),
                    "trading.sqlite", s.get("ts") or _now_iso(), False, None,
                ))
            for o in new_orders:
                self._broadcast("signals", _envelope(
                    "signals", "order.new", _compact_order(o),
                    "trading.sqlite", o.get("ts") or _now_iso(),
                    o.get("status") in ("rejected", "failed"),
                    o.get("fail_reason"),
                ))
        self._signals_initialized = True

    @staticmethod
    def _prune(id_set: Set[str]) -> None:
        if len(id_set) > SEEN_LIMIT:
            for _ in range(len(id_set) - SEEN_LIMIT):
                id_set.pop()


def _compact_signal(s: Dict[str, Any]) -> Dict[str, Any]:
    detail = s.get("detail") or {}
    return {
        "id": s.get("id"), "ts": s.get("ts"), "code": s.get("code"), "name": s.get("name"),
        "signal_type": s.get("signal_type"), "source": s.get("source"),
        "price": s.get("price"), "chg_pct": s.get("chg_pct"), "score": s.get("score"),
        "pushed": s.get("pushed"), "push_status": s.get("push_status"),
        "detail": {"label": detail.get("label"), "desc": detail.get("desc")},
    }


def _compact_order(o: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": o.get("id"), "ts": o.get("ts"), "code": o.get("code"), "name": o.get("name"),
        "side": o.get("side"), "qty": o.get("qty"), "price": o.get("price"),
        "amount": o.get("amount"), "gateway": o.get("gateway"), "status": o.get("status"),
        "fail_reason": o.get("fail_reason"), "strategy_id": o.get("strategy_id"),
        "is_drill": o.get("is_drill"), "exit_reason": o.get("exit_reason"),
    }


# 全局中枢单例
hub = StreamHub()


# ──────────────────────────────────────────────────────────
# SSE 生成器
# ──────────────────────────────────────────────────────────
def _parse_topics(raw: str) -> Set[str]:
    topics = {t.strip().lower() for t in (raw or "").split(",") if t.strip()}
    topics &= set(VALID_TOPICS)
    return topics or {"market", "signals"}


async def _sse_generator(request: Request, topics: Set[str],
                         codes: Optional[List[str]] = None, period: str = "day"):
    sub = await hub.subscribe(topics, codes, period)
    loop = asyncio.get_running_loop()
    last_ping = loop.time()
    try:
        # 提示浏览器断线 5s 后重连；并立即下发一条 connected 事件（含订阅 id + 溯源字段）
        yield "retry: 5000\n\n"
        yield _sse("connected", _envelope(
            "meta", "connected",
            {"topics": sorted(topics), "sid": sub.sid, "codes": sub.codes, "period": sub.period},
            "stream", _now_iso(), False, None,
        ))
        while True:
            # 断开探测：以 POLL_SECONDS 粒度轮询，**不依赖是否有事件可发**
            # （空闲订阅者也能在 ≤POLL_SECONDS 内被发现断开，订阅表不会只增不减）
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                # 空闲心跳：保留 SSE 注释行（保持连接、不产生 JS 事件），
                # **同时**再发一条命名 ping 事件——注释行不会触发 EventSource 事件，
                # 前端 watchdog 只有收到命名事件才能刷新 lastEventAt（假死检测依据）。
                now = loop.time()
                if now - last_ping >= HEARTBEAT_SECONDS:
                    last_ping = now
                    yield ": ping\n\n"
                    yield _sse("ping", {"ts": _now_iso(), "seq": hub.next_seq()})
                continue
            last_ping = loop.time()
            yield _sse(event.get("event", "message"), event)
    except asyncio.CancelledError:  # 客户端断开 / 服务关闭
        raise
    except Exception:  # 下游写失败（客户端已走）——不视为服务错误
        logger.info("SSE 连接关闭（sid=%s）：下游写入失败", sub.sid)
    finally:
        # 客户端断开：注销队列，避免订阅泄漏（active_clients 同步回落）
        await hub.unsubscribe(sub)


def _sse_response(request: Request, topics: Set[str],
                  codes: Optional[List[str]] = None, period: str = "day") -> StreamingResponse:
    return StreamingResponse(
        _sse_generator(request, topics, codes, period),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ──────────────────────────────────────────────────────────
# 路由
# ──────────────────────────────────────────────────────────
@router.get("")
async def stream_merged(request: Request, topics: str = "market,signals",
                        codes: str = "", period: str = "day") -> StreamingResponse:
    """合并订阅：``?topics=market,signals,quotes&codes=sh600519,sz000858&period=day``。

    quotes 主题按 codes 订阅个股实时报价（最多 20 只）；period 决定 bar_ts 对齐粒度。
    """
    return _sse_response(request, _parse_topics(topics), _norm_codes(codes), _norm_period(period))


@router.get("/market")
async def stream_market(request: Request) -> StreamingResponse:
    """市场级推送：指数快照 / 涨跌家数 / 板块排行前 N / 外部环境摘要。"""
    return _sse_response(request, {"market"})


@router.get("/quotes")
async def stream_quotes(request: Request, codes: str = "",
                        period: str = "day") -> StreamingResponse:
    """个股实时报价推送（K 线最后一根动态更新用）：``?codes=sh600519&period=day``。"""
    return _sse_response(request, {"quotes"}, _norm_codes(codes), _norm_period(period))


@router.get("/signals")
async def stream_signals(request: Request) -> StreamingResponse:
    """信号级推送：新信号 / 下单-成交-拒单 / 持仓变动。"""
    return _sse_response(request, {"signals"})


@router.get("/stats")
async def stream_stats() -> Dict[str, Any]:
    """订阅数与上游取数计数（验收证据：证明连接数不放大上游请求）。"""
    return {"ok": True, "data": hub.stats_snapshot()}
