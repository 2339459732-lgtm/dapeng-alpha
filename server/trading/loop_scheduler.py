"""闭环核心件 ③ —— 闭环调度器（LoopScheduler）

闭环定位：把四环串成无人值守的持续循环
  执行交易（monitor/paper 已有）→ 复盘分析（review_engine）→
  策略积累（library_sediment）→ 动态优化（iterator）→ 回到执行

节拍：
  - 每个工作日 15:08-23:59 且当日未跑 → 跑一轮日结例程（收盘扫描 15:05-15:10
    由 monitor 负责并推进交易日，本调度器在其后接力，不重复 advance_trading_day）；
  - 周五的日结例程自动加跑：全量迭代（调参提案+退役检查）+ 周度复盘报告；
  - 状态持久化 KV 'loop_state'（last_daily_date），重启不重复跑；
  - 循环体全量 try/except 吞异常，连续失败指数退避（复用 scheduler.backoff_sleep_seconds）。

manual 入口：evolution 路由 POST /api/evolution/run 可随时手动触发同一例程。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from . import scheduler
from .iterator import StrategyIterator
from .review_engine import TradeReviewer, current_equity, record_snapshot, score_decisions
from .store import TradingStore

logger = logging.getLogger(__name__)

LOOP_STATE_KEY = "loop_state"
# 日结窗口：收盘扫描（15:05-15:10）之后接力，避开重叠
_DAILY_WINDOW_START = (15, 8)


class LoopScheduler:
    """无人值守闭环：日结快照 → 单笔复盘 → 迭代决策 →（周五）周报"""

    def __init__(self, store: TradingStore):
        self._store = store
        self._running = False
        self.last_error: Optional[str] = None
        self.consecutive_errors = 0

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    async def run(self) -> None:
        self._running = True
        logger.info("LoopScheduler: 启动（闭环日结例程待命）")
        try:
            while self._running:
                sleep_s = scheduler.SCAN_INTERVAL_SECONDS
                try:
                    await self._tick()
                    self.consecutive_errors = 0
                    self.last_error = None
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.consecutive_errors += 1
                    self.last_error = f"{type(e).__name__}: {str(e)[:200]}"
                    logger.error("LoopScheduler: 本轮异常（连续 %d 次）%s",
                                 self.consecutive_errors, self.last_error, exc_info=True)
                    sleep_s = scheduler.backoff_sleep_seconds(self.consecutive_errors)
                await asyncio.sleep(sleep_s)
        finally:
            self._running = False

    async def shutdown(self) -> None:
        self._running = False

    def runtime_state(self) -> Dict[str, Any]:
        return {"running": self._running, "consecutive_errors": self.consecutive_errors,
                "last_error": self.last_error}

    async def _tick(self) -> None:
        now = datetime.now()
        if now.weekday() >= 5:
            return
        if (now.hour, now.minute) < _DAILY_WINDOW_START:
            return
        state = await self._store.kv_get(LOOP_STATE_KEY, {}) or {}
        if state.get("last_daily_date") == now.strftime("%Y-%m-%d"):
            return
        await self.run_daily_routine(trigger="auto", full=(now.weekday() == 4))

    # --------------------------------------------------------
    # 日结例程（自动 + 手动共用；幂等，同日重跑覆盖）
    # --------------------------------------------------------
    async def run_daily_routine(self, trigger: str = "auto", full: Optional[bool] = None) -> Dict[str, Any]:
        now = datetime.now()
        if full is None:
            full = now.weekday() == 4
        today = now.strftime("%Y-%m-%d")
        result: Dict[str, Any] = {"date": today, "trigger": trigger, "full": full,
                                  "started_at": now.strftime("%H:%M:%S")}

        # ① 权益快照（复盘数据源）
        eq = await current_equity(self._store)
        await record_snapshot(self._store, eq["equity"])
        result["equity"] = round(eq["equity"], 2)

        # ② 单笔交易复盘（新闭合交易自动归因）
        reviewer = TradeReviewer(self._store)
        result["trade_reviews"] = await reviewer.review_new_trades()

        # ②b 决策质量评分（含被闸拒绝的决策；信号强度 vs 实际结果）
        try:
            result["decision_quality"] = await score_decisions(self._store)
        except Exception:
            logger.warning("LoopScheduler: 决策质量评分失败", exc_info=True)
            result["decision_quality"] = {"new": 0, "total": 0}

        # ③ 迭代决策（晋级/降级/调参提案/提案验证应用）+ 策略库沉淀
        iterator = StrategyIterator(self._store)
        result["iteration"] = await iterator.run(full=full, trigger=trigger)
        await iterator.flush_logs()

        # ④ 周五：周度复盘报告（复用 review 路由同一逻辑与 KV 口径）
        if full:
            try:
                from server.trading.review_service import weekly_report
                report = await weekly_report()
                result["weekly_report"] = (report or {}).get("week")
            except Exception:
                logger.warning("LoopScheduler: 周报生成失败", exc_info=True)
                result["weekly_report"] = None

        # ⑤ 状态与审计
        state = await self._store.kv_get(LOOP_STATE_KEY, {}) or {}
        state.update({
            "last_daily_date": today,
            "last_run_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "last_trigger": trigger,
            "total_runs": int(state.get("total_runs", 0)) + 1,
            "last_result": {
                "equity": result["equity"],
                "new_reviews": result["trade_reviews"].get("new_reviews", 0),
                "actions": [a.get("action") for a in result["iteration"].get("actions", [])],
                "weekly_report": result.get("weekly_report"),
            },
        })
        await self._store.kv_set(LOOP_STATE_KEY, state)
        await self._store.audit("loop", "daily_routine_done", {
            "date": today, "trigger": trigger, "full": full,
            "new_reviews": result["trade_reviews"].get("new_reviews", 0),
            "actions": len(result["iteration"].get("actions", [])),
        })
        logger.info("LoopScheduler: 日结例程完成 %s (trigger=%s, full=%s, 动作 %d)",
                    today, trigger, full, len(result["iteration"].get("actions", [])))
        return result
