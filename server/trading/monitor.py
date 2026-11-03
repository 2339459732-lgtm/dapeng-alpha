"""自动交易核心包 - 信号监控循环（asyncio 后台任务）

架构蓝图 §2 / §3：
- 盘中 60s/轮；15:05 收盘扫描一轮全量；可选 9:15 盘前摘要；非交易时段空转
- 数据流：批量行情 → 双轨评估 → 冷却去重（30min，重启自动恢复）→ 落库 → 双路分发
  （⑤a 邮件提醒；⑤b 仅 strategy 源信号过风控自动下单）
- 崩溃自愈：循环体全量 try/except 吞异常继续；连续失败 ≥5 指数退避（60s→上限 600s）
- 单轮 asyncio.wait_for(90s) 兜底，超时本轮作废
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from server.cache import cache
from server.config import CACHE_TTL_KLINE, KLINE_LIMIT, find_stock_name, get_all_stock_codes
from server.realtime import fetch_single_quote, fetch_tencent_quotes
from server.tencent_kline import fetch_tencent_kline

from . import scheduler
from .alerts import EmailAlerter
from .models import (
    AutomationConfig,
    Position,
    PushStatus,
    Signal,
    now_local_iso,
    parse_strategy_source,
    strategy_source,
)
from .store import TradingStore
from .strategies import get as get_strategy, list_enabled as list_enabled_strategies
from .strategies.base import StrategyContext
from .strategies.panel_adapter import PanelSignalAdapter

logger = logging.getLogger(__name__)

# 单次邮件推送的等待上限（秒）。必须显著小于 scheduler.SCAN_ROUND_TIMEOUT_SECONDS，
# 否则一封邮件的网络抖动就会吃光整轮扫描预算，让本轮剩余标的全部漏扫。
NOTIFY_TIMEOUT_SECONDS = 8.0


class SignalMonitor:
    """信号监控主循环：拉行情 → 评估 → 冷却去重 → 分发"""

    def __init__(
        self,
        store: TradingStore,
        alerter: Optional[EmailAlerter] = None,
        risk: Any = None,  # RiskManager，M3 接入
    ):
        self._store = store
        self._alerter = alerter
        self._risk = risk
        self._panel = PanelSignalAdapter()

        self._running = False
        self._cooldown: Dict[tuple, datetime] = {}  # (code, signal_type, source) -> 最后触发时刻
        self._close_scanned_date = ""   # 收盘扫描每日一次
        self._digest_flushed_hour = ""  # 摘要邮件每小时最多一封

        # 状态灯数据源（gate 端点 monitor 字段）
        self.last_scan_at: Optional[str] = None
        self.last_error: Optional[str] = None
        self.consecutive_errors = 0

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    async def run(self) -> None:
        """主循环（随 lifespan 启动；task.cancel() 停止）"""
        self._running = True
        await self._rebuild_cooldown()
        logger.info("SignalMonitor: 启动，冷却映射 %d 条", len(self._cooldown))
        try:
            while self._running:
                sleep_s = scheduler.SCAN_INTERVAL_SECONDS
                try:
                    await self._tick()
                    if self.consecutive_errors:
                        logger.info("SignalMonitor: 恢复正常，清零连续失败计数")
                    self.consecutive_errors = 0
                    self.last_error = None
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.consecutive_errors += 1
                    self.last_error = f"{type(e).__name__}: {str(e)[:200]}"
                    logger.error("SignalMonitor: 本轮异常（连续 %d 次）%s",
                                 self.consecutive_errors, self.last_error, exc_info=True)
                    sleep_s = scheduler.backoff_sleep_seconds(self.consecutive_errors)
                await asyncio.sleep(sleep_s)
        finally:
            self._running = False

    async def shutdown(self) -> None:
        """优雅停止：flush 邮件摘要队列（§2.4）"""
        self._running = False
        if self._alerter is not None:
            try:
                cfg = await self._load_config()
                await self._alerter.flush_digest(cfg.email.get("recipient", ""), reason="服务停止")
            except Exception:
                logger.warning("SignalMonitor: 停止时 flush 摘要失败", exc_info=True)

    def runtime_state(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "last_scan_at": self.last_scan_at,
            "consecutive_errors": self.consecutive_errors,
            "last_error": self.last_error,
        }

    async def _rebuild_cooldown(self) -> None:
        """冷却状态从 signals 表 MAX(ts) 派生（重启自动恢复，§3 ④）"""
        raw = await self._store.cooldown_map()
        self._cooldown = {
            key: datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
            for key, ts in raw.items() if ts
        }

    # --------------------------------------------------------
    # 节拍
    # --------------------------------------------------------
    async def _tick(self) -> None:
        now = datetime.now()
        phase = scheduler.session_phase(now)
        cfg = await self._load_config()

        # 三档开关全关 → 空转
        if not (cfg.switches.get("notify") or cfg.switches.get("paper") or cfg.switches.get("live")):
            return

        if scheduler.is_trading_phase(phase):
            await asyncio.wait_for(self._scan_round(now, cfg, is_close_scan=False),
                                   timeout=scheduler.SCAN_ROUND_TIMEOUT_SECONDS)
            # 条件单轮询（价格/时间触发）——触发后仍走风控+决策闸
            if (cfg.switches.get("paper") or cfg.switches.get("live")) and self._risk is not None:
                try:
                    await self._poll_conditionals(now, cfg)
                except Exception:
                    logger.error("SignalMonitor: 条件单轮询失败", exc_info=True)
        elif phase == "premarket" and cfg.quiet.get("premarket_digest") and self._alerter:
            # 可选盘前摘要（9:15-9:30 一轮，默认关闭）
            date_key = now.strftime("%Y-%m-%d")
            if self._close_scanned_date != f"premarket:{date_key}":
                self._close_scanned_date = f"premarket:{date_key}"
                await self._alerter.flush_digest(cfg.email.get("recipient", ""), reason="盘前摘要")
        elif scheduler.is_close_scan_window(now):
            date_key = now.strftime("%Y-%m-%d")
            if self._close_scanned_date != date_key:
                self._close_scanned_date = date_key
                await asyncio.wait_for(self._scan_round(now, cfg, is_close_scan=True),
                                       timeout=scheduler.SCAN_ROUND_TIMEOUT_SECONDS)
        # 其余时段（午休/闭市）：不扫描不推送

        # 摘要队列整点 flush（每小时最多一封）
        if self._alerter is not None:
            hour_key = now.strftime("%Y-%m-%dT%H")
            if self._digest_flushed_hour != hour_key:
                self._digest_flushed_hour = hour_key
                await self._alerter.flush_digest(cfg.email.get("recipient", ""), reason="整点摘要")

    async def _load_config(self) -> AutomationConfig:
        return AutomationConfig.from_dict(await self._store.kv_get("automation"))

    async def _poll_conditionals(self, now: datetime, cfg: AutomationConfig) -> None:
        """轮询 pending 条件单：满足即触发（走风控+决策闸），并留痕。"""
        from . import conditional
        fired = await conditional.poll(self._store, self._risk, cfg, now=now)
        if fired:
            logger.info("SignalMonitor: 条件单触发 %d 笔 %s", len(fired), fired)

    # --------------------------------------------------------
    # 扫描轮
    # --------------------------------------------------------
    async def _prefetch_klines(self, codes: List[str]) -> tuple:
        """受控并发预取日K（缓存优先）。返回 (klines_map, 已完成数)。

        - `asyncio.Semaphore` 限并发（默认 6），避免瞬时打满出口；
        - 到 `SCAN_PREFETCH_BUDGET_SECONDS` 仍未返回的任务取消，但**已完成结果
          照常返回用于本轮判定**（迟到不弃），避免整轮 TimeoutError 全部作废；
        - 逐只失败只丢该只，不影响整体（返回 None 由调用方跳过）。
        """
        if not codes:
            return {}, 0
        concurrency = max(1, int(scheduler.SCAN_KLINE_CONCURRENCY))
        budget = float(scheduler.SCAN_PREFETCH_BUDGET_SECONDS)
        sem = asyncio.Semaphore(concurrency)

        async def _one(code: str):
            async with sem:
                try:
                    k = await cache.get_or_fetch(
                        f"raw_kline:{code}:day",
                        lambda c=code: fetch_tencent_kline(c, period="day", limit=KLINE_LIMIT),
                        CACHE_TTL_KLINE,
                    )
                    return code, k
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - 单只失败不影响整体
                    logger.debug("SignalMonitor: 预取K线失败 %s", code, exc_info=True)
                    return code, None

        tasks = [asyncio.ensure_future(_one(c)) for c in codes]
        done, pending = await asyncio.wait(tasks, timeout=budget)
        klines_map: Dict[str, Any] = {}
        for t in done:
            try:
                code, k = t.result()
            except Exception:  # noqa: BLE001
                continue
            if k:
                klines_map[code] = k
        for t in pending:
            t.cancel()
        if pending:
            logger.warning("SignalMonitor: K线预取超预算，已完成 %d/%d，取消 %d",
                           len(klines_map), len(codes), len(pending))
        return klines_map, len(klines_map)

    async def _scan_round(self, now: datetime, cfg: AutomationConfig, is_close_scan: bool) -> None:
        round_start = now_local_iso()
        specs = await self._store.kv_get("strategies", default=[]) or []
        enabled_strategies = list_enabled_strategies(specs) if cfg.switches.get("paper") or cfg.switches.get("live") else []

        # ① 监控范围：Track A 按 scope；Track B 按各策略 enabled_codes
        if cfg.scope.get("mode") == "watchlist":
            track_a_codes = [c for c in (cfg.scope.get("watchlist") or []) if isinstance(c, str)]
        else:
            track_a_codes = get_all_stock_codes()
        spec_by_id = {s.get("strategy_id"): s for s in specs}
        track_b_codes = set()
        for st in enabled_strategies:
            track_b_codes.update(spec_by_id.get(st.id, {}).get("enabled_codes") or [])

        all_codes = sorted(set(track_a_codes) | track_b_codes)
        track_a_set = set(track_a_codes)
        if not all_codes:
            return

        # ② 批量行情（1 次请求，5s 超时）
        quotes_list = await fetch_tencent_quotes(all_codes, timeout=5.0)
        quotes = {q.get("code"): q for q in quotes_list if q and q.get("code")}

        # ③ K线预取（受控并发 + 预取预算；已完成部分迟到不弃）
        klines_map, fetched_n = await self._prefetch_klines(all_codes)
        logger.info("SignalMonitor: K线预取 %d/%d（并发 %d，预算 %ds）",
                    fetched_n, len(all_codes),
                    scheduler.SCAN_KLINE_CONCURRENCY, scheduler.SCAN_PREFETCH_BUDGET_SECONDS)

        inserted = 0
        for code in all_codes:
            try:
                quote = quotes.get(code)
                # ③ K线（取自预取；未取到即跳过，语义同改前 `if not klines: continue`）
                klines = klines_map.get(code)
                if not klines:
                    continue
                name = (quote or {}).get("name") or find_stock_name(code)

                fired: List[Signal] = []
                # ④a Track A：面板轨（开关 notify；永不下单）
                if cfg.switches.get("notify") and code in track_a_set:
                    ctx = StrategyContext(quote=quote, position=None, params={}, now=now)
                    fired.extend(self._panel.evaluate(code, name, klines, ctx, cfg.trigger))

                # ④b Track B：引擎轨（仅对已启用标的；可过风控下单）
                for st in enabled_strategies:
                    spec = spec_by_id.get(st.id) or {}
                    if code not in (spec.get("enabled_codes") or []):
                        continue
                    position = await self._store.get_position(code, "paper")
                    ctx = StrategyContext(
                        quote=quote, position=position,
                        params=spec.get("params") or st.default_params, now=now,
                    )
                    sig = st.evaluate(code, klines, ctx)
                    if sig is not None:
                        sig.name = name
                        fired.append(sig)

                # ⑤ 冷却去重 → 落库 → 双路分发
                await self._dispatch(code, name, fired, now, cfg)
                inserted += sum(1 for _ in fired)  # 计数仅日志用
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("SignalMonitor: 扫描 %s 失败", code, exc_info=True)

        self.last_scan_at = now_local_iso()
        logger.info("SignalMonitor: %s 扫描完成，标的 %d，信号 %d",
                    "收盘" if is_close_scan else "盘中", len(all_codes), inserted)

        # ⑤c 持仓守卫：风控级止盈（固定止盈 / 移动止盈），与止损同构，每轮扫描评估
        if self._risk is not None:
            try:
                await self._position_guard(cfg, quotes)
            except Exception:
                logger.error("SignalMonitor: 持仓止盈守卫异常", exc_info=True)

        # 收盘扫描后：模拟交易日推进 + 持仓市价刷新 + 日亏统计
        if is_close_scan and self._risk is not None:
            try:
                await self._risk.on_close_scan()
            except Exception:
                logger.error("SignalMonitor: 收盘后处理失败", exc_info=True)

    # --------------------------------------------------------
    # 持仓守卫：风控级止盈（与止损对称，扫描轮触发）
    # --------------------------------------------------------
    async def _position_guard(
        self, cfg: AutomationConfig, quotes: Dict[str, Dict[str, Any]]
    ) -> None:
        """对每笔持仓评估固定止盈/移动止盈，触发即走风控卖出。

        - 固定止盈 take_profit_pct：现价 ≥ 成本 ×(1+tp%)
        - 移动止盈 trailing_stop_pct：现价 ≤ 持仓期间最高价 ×(1−ts%)
        最高价在每轮扫描刷新，落 positions.peak_price（回撤计算依据）。
        """
        risk_cfg = await self._risk.load_risk_config()  # type: ignore[union-attr]
        tp = float(risk_cfg.take_profit_pct or 0.0)
        ts = float(risk_cfg.trailing_stop_pct or 0.0)
        if tp <= 0 and ts <= 0:
            return
        positions = await self._store.list_positions("paper")
        if not positions:
            return
        fallback_sid = await self._first_enabled_strategy_id()
        for pos in positions:
            if pos.qty <= 0:
                continue
            quote = quotes.get(pos.code) or await fetch_single_quote(pos.code)
            price = float((quote or {}).get("price") or 0)
            if price <= 0:
                continue
            # 更新持仓期间最高价（移动止盈基准）
            peak = max(float(pos.peak_price or 0.0), price)
            if peak != float(pos.peak_price or 0.0):
                pos.peak_price = price if peak == price else peak
                pos.last_price = price
                await self._store.upsert_position(pos)
            cost = float(pos.avg_cost or 0.0)
            if cost <= 0:
                continue
            gain = round((price - cost) / cost * 100, 2)
            sig: Optional[Signal] = None
            if tp > 0 and price >= cost * (1 + tp / 100.0):
                sig = Signal(
                    code=pos.code, name=pos.name, signal_type="take_profit",
                    source=strategy_source(pos.strategy_id or fallback_sid or ""),
                    price=price, chg_pct=gain,
                    detail={"label": "固定止盈", "take_profit_pct": tp,
                            "cost": round(cost, 3), "price": price,
                            "desc": f"现价{price:.2f} 达成本{cost:.2f} 的 +{tp}% 止盈线（浮盈{gain:+.2f}%）"},
                )
            elif ts > 0 and peak > 0 and price <= peak * (1 - ts / 100.0):
                drawdown = round((price - peak) / peak * 100, 2)
                sig = Signal(
                    code=pos.code, name=pos.name, signal_type="trailing_stop",
                    source=strategy_source(pos.strategy_id or fallback_sid or ""),
                    price=price, chg_pct=gain,
                    detail={"label": "移动止盈", "trailing_stop_pct": ts,
                            "peak": round(peak, 3), "price": price, "drawdown_pct": drawdown,
                            "desc": f"现价{price:.2f} 自最高价{peak:.2f} 回撤{drawdown:.2f}%（阈值-{ts}%）"},
                )
            if sig is not None and parse_strategy_source(sig.source) is not None:
                try:
                    await self._risk.execute_signal(sig, cfg)  # type: ignore[union-attr]
                except Exception:
                    logger.error("SignalMonitor: 止盈信号 %s 下单异常", pos.code, exc_info=True)

    async def _first_enabled_strategy_id(self) -> Optional[str]:
        """取首个已启用策略 id（持仓缺 strategy_id 时的止盈信号来源兜底）"""
        specs = await self._store.kv_get("strategies", default=[]) or []
        for sid in list_enabled_strategies(specs):
            return sid.id
        for spec in specs:
            if spec.get("strategy_id"):
                return spec["strategy_id"]
        return None

    # --------------------------------------------------------
    # 冷却与分发
    # --------------------------------------------------------
    def _in_cooldown(self, sig: Signal, now: datetime, cooldown_minutes: int) -> bool:
        key = (sig.code, sig.signal_type, sig.source)
        last = self._cooldown.get(key)
        return last is not None and now - last < timedelta(minutes=cooldown_minutes)

    async def _dispatch(
        self, code: str, name: str, fired: List[Signal], now: datetime, cfg: AutomationConfig
    ) -> None:
        fresh: List[Signal] = []
        for sig in fired:
            # ④ 冷却去重（同 code+signal_type+source 独立冷却；冷却期内丢弃不落库）
            if self._in_cooldown(sig, now, cfg.cooldown_minutes):
                continue
            self._cooldown[(sig.code, sig.signal_type, sig.source)] = now
            await self._store.insert_signal(sig.to_row())
            await self._store.audit("monitor", "signal_fired", {
                "id": sig.id, "code": sig.code, "type": sig.signal_type, "source": sig.source,
            })
            fresh.append(sig)

        if not fresh:
            return

        # ⑤a 邮件提醒（notify 开关；同窗聚合：同 code 多信号并一封）
        # 关键：SMTP 走 to_thread + 重试，单次最长可达数十秒。若不加超时，
        # 一封邮件的网络抖动就会撑爆整轮扫描的 wait_for 预算（SCAN_ROUND_TIMEOUT_SECONDS），
        # 导致本轮剩余标的全部未扫描 + 触发退避 —— 通知慢不该拖垮监控。
        if cfg.switches.get("notify") and self._alerter is not None:
            try:
                status = await asyncio.wait_for(
                    self._alerter.send_signal_batch(
                        code, name, fresh,
                        recipient=cfg.email.get("recipient", ""),
                        per_stock_per_day=int(cfg.email.get("per_stock_per_day", 5)),
                        global_per_hour=int(cfg.email.get("global_per_hour", 20)),
                    ),
                    timeout=NOTIFY_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                status = PushStatus.FAILED.value
                logger.warning(
                    "SignalMonitor: %s 邮件推送超时（>%.0fs），本轮不再等待",
                    code, NOTIFY_TIMEOUT_SECONDS,
                )
            except Exception:
                status = PushStatus.FAILED.value
                logger.warning("SignalMonitor: %s 邮件推送异常", code, exc_info=True)
            pushed = status == PushStatus.SENT.value
            for sig in fresh:
                await self._store.update_signal_push(sig.id, pushed, status)

        # ⑤b 自动下单（仅 strategy 源信号 + paper/live 开关打开；panel 源永不下单）
        if self._risk is not None:
            for sig in fresh:
                if parse_strategy_source(sig.source) is None:
                    continue
                if not (cfg.switches.get("paper") or cfg.switches.get("live")):
                    continue
                try:
                    await self._risk.execute_signal(sig, cfg)
                except Exception:
                    logger.error("SignalMonitor: 信号 %s 下单分发异常", sig.id, exc_info=True)
