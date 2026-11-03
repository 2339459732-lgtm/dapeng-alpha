"""
DAPENG Brain — 自主进化 AI 主脑 (V1.0)

平台智能中枢，负责任务调度、策略优化与持续学习。
架构借鉴 Hermes Agent（Nous Research, MIT）的四大核心模式并原生实现：
1. Cron 任务调度   — 内置交易时段感知的周期任务表（市场调查/日结闭环/记忆蒸馏）
2. 分层持久记忆   — 工作记忆(运行态) + 洞察记忆(brain_insights) + 经验记忆(brain_lessons)
3. 学习闭环       — 调查→研判→记忆→蒸馏→经验回注下一轮研判提示词，持续自我改进
4. 技能注册表     — 复用平台已有 Agent 注册表与策略库（策略即技能，迭代引擎负责进化）

与既有四环闭环的关系：闭环调度器（loop_scheduler）负责「执行→复盘→积累→优化」，
主脑负责「何时触发、触发后如何理解结果、把理解沉淀为可复用经验」。

v2（2026-11-02）：新增两条对外能力——
* ``capabilities()``：读 ``brain_capabilities`` 的声明式能力表，按总线新鲜度给出
  每项能力 ok / degraded / blocked，回答「主脑现在能算什么、不能算什么」；
* ``run_cycle()``：一次贯通「情绪追踪 → 市场调查 → 六专家 → 决策备忘录」，
  共享 trace_id，产出 brain-cycle.v1，并把结论沉淀进洞察记忆。
所有任务幂等：daily_loop 依赖 loop_state.last_daily_date 防重；调查引擎自带 10 分钟缓存。
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)

_TICK_SECONDS = 20
_TASK_TIMEOUT = 180
_INSIGHTS_KEEP = 30
_LESSONS_KEEP = 20
_KV_INSIGHTS = "brain_insights"
_KV_LESSONS = "brain_lessons"
_KV_STATE = "brain_state"
_KV_CYCLE = "brain_cycle_latest"
#: 闭环期间钉住快照的缓存寿命（秒）。要覆盖「上游慢时一轮闭环的最坏耗时」，
#: 同时不能太长，否则页面会长时间看到同一份旧数据。
CYCLE_PIN_TTL = 300


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _is_weekday() -> bool:
    return datetime.now().weekday() < 5


def _hhmm() -> tuple[int, int]:
    now = datetime.now()
    return now.hour, now.minute


def _in_window(start: tuple[int, int], end: tuple[int, int]) -> bool:
    return start <= _hhmm() <= end


class BrainTask:
    """主脑任务定义 + 运行态"""

    def __init__(
        self,
        task_id: str,
        name: str,
        interval_sec: int,
        fn: Callable[[], Coroutine[Any, Any, str]],
        window: Optional[tuple[tuple[int, int], tuple[int, int]]] = None,
        weekdays_only: bool = True,
    ):
        self.id = task_id
        self.name = name
        self.base_interval = interval_sec
        self.interval_sec = interval_sec
        self.fn = fn
        self.window = window
        self.weekdays_only = weekdays_only
        self.last_run: Optional[str] = None
        self.last_status: str = "pending"  # pending/ok/error/running
        self.last_summary: str = ""
        self.run_count = 0
        self.error_count = 0
        self.running = False

    def due(self) -> bool:
        if self.running:
            return False
        if self.weekdays_only and not _is_weekday():
            return False
        if self.window and not _in_window(*self.window):
            return False
        if self.last_run is None:
            return True
        elapsed = time.time() - self._last_run_ts
        return elapsed >= self.interval_sec

    _last_run_ts: float = 0.0

    def mark_start(self) -> None:
        self.running = True
        self.last_status = "running"

    def mark_done(self, ok: bool, summary: str) -> None:
        self.running = False
        self._last_run_ts = time.time()
        self.last_run = _now_str()
        self.last_status = "ok" if ok else "error"
        self.last_summary = summary[:200]
        self.run_count += 1
        if not ok:
            self.error_count += 1

    def next_due_in(self) -> Optional[int]:
        if self.window and not _in_window(*self.window):
            return None  # 窗口外：等下一交易时段
        if self.weekdays_only and not _is_weekday():
            return None
        if self._last_run_ts == 0.0:
            return 0
        remain = int(self.interval_sec - (time.time() - self._last_run_ts))
        return max(0, remain)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "interval_sec": self.interval_sec,
            "base_interval_sec": self.base_interval,
            "dynamic_interval": self.interval_sec != self.base_interval,
            "last_run": self.last_run,
            "last_status": self.last_status,
            "last_summary": self.last_summary,
            "run_count": self.run_count,
            "error_count": self.error_count,
            "next_due_in_sec": self.next_due_in(),
            "window": f"{self.window[0][0]:02d}:{self.window[0][1]:02d}-{self.window[1][0]:02d}:{self.window[1][1]:02d}" if self.window else "全天",
        }


class DAPENGBrain:
    """自主进化 AI 主脑"""

    def __init__(self) -> None:
        self._running = False
        self._started_at: Optional[str] = None
        self._start_ts = 0.0
        self._ticks = 0
        # 龙虎榜任务的重试状态（仅内存，重启即清零；记录当日是否已过重试窗口）
        self._lhb_fails: Dict[str, Any] = {}
        self.tasks: Dict[str, BrainTask] = {
            "market_survey": BrainTask(
                "market_survey", "动态市场调查", 1800, self._task_market_survey,
                window=((8, 45), (15, 30)),
            ),
            "factor_chan": BrainTask(
                "factor_chan", "动态因子缠论", 1800, self._task_factor_chan,
                window=((8, 45), (15, 30)),
            ),
            "expert_panel": BrainTask(
                "expert_panel", "六专家研判", 900, self._task_expert_panel,
                window=((8, 45), (15, 30)),
            ),
            "daily_loop": BrainTask(
                "daily_loop", "日结闭环（复盘+迭代）", 300, self._task_daily_loop,
                window=((15, 9), (23, 59)),
            ),
            "memory_distill": BrainTask(
                "memory_distill", "经验记忆蒸馏", 21600, self._task_memory_distill,
                window=None, weekdays_only=False,
            ),
            "intel_scan": BrainTask(
                "intel_scan", "情报扫描评估", 1800, self._task_intel_scan,
                # 原为 7:00-23:00 且 weekdays_only=False（周末/深夜持续外部轮询+LLM）。
                # 收敛到交易日盘前~盘后；盘后在 _task_intel_scan 内降频。
                window=((8, 30), (16, 0)), weekdays_only=True,
            ),
            "lhb_review": BrainTask(
                "lhb_review", "龙虎榜复盘", 1800, self._task_lhb_review,
                # 龙虎榜营业部明细通常在 18:00 后才补齐，15:35 先跑一次拿涨停池，
                # 之后每 30 分钟重试，直到拿到当日完整席位数据（幂等，可重复覆盖）。
                window=((15, 35), (23, 0)), weekdays_only=True,
            ),
        }

    # --------------------------------------------------------------
    # 记忆层（KV 持久化，失败静默降级为仅内存）
    # --------------------------------------------------------------

    async def _kv_get(self, key: str, default: Any) -> Any:
        """键值读取：委托 infra.kv，避免 engine 反向依赖 trading。"""
        from server.infra.kv import kv_get

        return await kv_get(key, default)

    async def _kv_set(self, key: str, value: Any) -> None:
        from server.infra.kv import kv_set

        await kv_set(key, value)

    # --------------------------------------------------------------
    # 主循环
    # --------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._started_at = _now_str()
        self._start_ts = time.time()
        logger.info("DAPENG Brain 主脑已启动（%d 个调度任务）", len(self.tasks))
        await asyncio.sleep(10)  # 等待 facade/store 就绪
        while self._running:
            self._ticks += 1
            for task in self.tasks.values():
                if task.due():
                    asyncio.create_task(self._run_guarded(task))
            await asyncio.sleep(_TICK_SECONDS)

    async def stop(self) -> None:
        self._running = False

    async def _run_guarded(self, task: BrainTask) -> None:
        task.mark_start()
        try:
            summary = await asyncio.wait_for(task.fn(), timeout=_TASK_TIMEOUT)
            task.mark_done(True, summary or "完成")
            logger.info("brain task %s ok: %s", task.id, task.last_summary)
        except asyncio.TimeoutError:
            task.mark_done(False, f"超时（{_TASK_TIMEOUT}s）")
            logger.warning("brain task %s timeout", task.id)
        except Exception as exc:
            task.mark_done(False, f"{type(exc).__name__}: {exc}")
            logger.warning("brain task %s failed: %s", task.id, exc)
        await self._persist_state()

    async def run_task_now(self, task_id: str) -> Dict[str, Any]:
        task = self.tasks.get(task_id)
        if not task:
            return {"ok": False, "message": f"未知任务 {task_id}"}
        if task.running:
            return {"ok": False, "message": "任务正在运行中"}
        await self._run_guarded(task)
        return {"ok": task.last_status == "ok", "task": task.to_dict()}

    async def capabilities(self) -> Dict[str, Any]:
        """主脑能力矩阵：声明式能力表 + 实时数据可用性。"""
        from server.brain_capabilities import capability_graph, report

        states: Dict[str, Any] = {}
        try:
            from server.data_bus import bus

            snap = bus.snapshot()
            for item in snap.get("chain") or []:
                states[item["key"]] = {"fresh": bool(item.get("fresh"))}
        except Exception as exc:
            logger.debug("brain capabilities: bus unavailable: %s", exc)
        matrix = report(states)
        matrix["graph"] = capability_graph()
        matrix["tasks"] = {tid: t.to_dict() for tid, t in self.tasks.items()}
        matrix["running"] = self._running
        return matrix

    async def _lineage_nodes(self, packet: Any, flow: Any, survey: Any, experts: Any,
                             crosscheck: Any) -> Dict[str, Any]:
        """收集链路各节点的**缓存态**产物，供 lineage 记账。

        `packet` 由调用方在闭环开头冻结后传进来，避免这里再取一次时拿到另一版快照
        （实测出现过：闭环内 packet 过期重建，链条上挂了两代 trace_id）。
        取不到就留空，链路里显示 `gaps`，不用上一层结果顶替下一层。
        """
        from server.decision_snapshot import get_sentiment_snapshot
        from server.market_coverage_payload import get_market_coverage

        nodes: Dict[str, Any] = {"packet": packet if isinstance(packet, dict) else None}

        async def safe(key: str, coro) -> None:
            try:
                value = await coro
                nodes[key] = value if isinstance(value, dict) else None
            except Exception as exc:
                logger.debug("lineage node %s unavailable: %s", key, exc)
                nodes[key] = None

        await safe("coverage", get_market_coverage())
        await safe("sentiment", get_sentiment_snapshot())
        nodes["sentiment_flow"] = flow if isinstance(flow, dict) else None
        nodes["survey"] = survey if isinstance(survey, dict) else None
        nodes["experts"] = experts if isinstance(experts, dict) else None
        nodes["crosscheck"] = crosscheck if isinstance(crosscheck, dict) else None
        return nodes

    async def run_cycle(self, force: bool = False) -> Dict[str, Any]:
        """一次贯通闭环：情绪追踪 → 市场调查 → 六专家 → 决策备忘录。

        与定时任务的区别：这是**按需**的同步链路，调用方拿到的是同一条 trace_id
        下的完整推理链，可直接用于复盘与前端展示。
        """
        from server.brain_capabilities import compose_memo
        from server.decision_experts import run_expert_panel
        from server.market_survey_engine import market_survey_engine
        from server.sentiment_tracker import get_sentiment_flow

        started = time.perf_counter()
        marks: List[Dict[str, Any]] = []

        async def step(key: str, coro) -> Any:
            t0 = time.perf_counter()
            try:
                value = await coro
            except Exception as exc:  # 单步失败不打断整链，缺口写进 memo.gaps
                logger.warning("brain cycle %s failed: %s", key, exc)
                value = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
            marks.append({"key": key, "ms": round((time.perf_counter() - t0) * 1000, 1),
                          "ok": bool(value.get("ok")) if isinstance(value, dict) else False})
            return value

        from server.layer_crosscheck import run as run_crosscheck
        from server.decision_routing import current as routing_now
        from server.decision_snapshot import get_market_packet, get_sentiment_snapshot

        # 先冻结一份快照并**钉住**：整轮闭环只能依据同一份事实。
        # packet 常规 TTL 55s，而上游慢时构建就要 15–26s，不钉住的话下游任何一次 force
        # 都会重建出新一代快照，同一条决策链上会挂两三代 trace_id（实测出现过）。
        packet = await step("packet", get_market_packet(force=force))
        try:
            from server.decision_snapshot import pin_packet

            pin_packet(packet, ttl=CYCLE_PIN_TTL, owner="cycle")
        except Exception as exc:
            logger.debug("brain cycle: pin packet failed: %s", exc)
        flow = await step("sentiment_flow", get_sentiment_flow(force=force))
        survey = await step("survey", market_survey_engine.get_market_survey(force=force))
        # 研判团必须吃判断层结论：否则「三层贯通」只到数据管道，没进决策
        experts = await step("experts", run_expert_panel(force=force, survey=survey))
        # 三层对证：拿三份快照互相比对，冲突会影响备忘录置信与风险
        crosscheck = await step("crosscheck", run_crosscheck(force=force))
        if isinstance(crosscheck, dict) and packet.get("trace_id"):
            # 对证比的就是这一轮的三层快照，给它打上同一个 trace 才可反查
            crosscheck["trace_id"] = packet.get("trace_id")
        # 决策分流：把趋势/资金/买手率/情绪压成一条环境判定，作为仓位闸门
        routing = await step("routing", routing_now())
        memo = compose_memo(survey, flow, experts, now=_now_str(),
                            crosscheck=crosscheck, routing=routing)

        # 溯源：把上游各层产物整理成一条可解析的链路，并把「引用清单」写进结论本体。
        from server import lineage as lineage_mod

        upstream = await self._lineage_nodes(packet, flow, survey, experts, crosscheck)
        trace_id = memo.get("trace_id")
        record = lineage_mod.build(trace_id, dict(upstream))
        memo["citations"] = lineage_mod.citations(record)
        # gaps 只报「上游」缺了谁：memo 自己还没入链，不能算缺
        upstream_gaps = [gap for gap in record["gaps"] if gap != "memo"]
        memo["lineage"] = {
            "version": lineage_mod.LINEAGE_VERSION,
            "trace_id": trace_id,
            # 这里放的是「上游链路是否完整」，不是「结论是否成立」——
            # 缺一层也要照常出结论，但必须让读的人知道缺了哪一层。
            "chain_ok": not upstream_gaps,
            "consistent": bool(record.get("consistent")),
            "gaps": upstream_gaps,
        }
        upstream["memo"] = memo
        record = lineage_mod.build(trace_id, upstream)
        lineage_mod.record(record)
        # 解钉：闭环结束就放开，别让页面在接下来的几分钟里一直看到同一份旧快照
        try:
            from server.decision_snapshot import unpin_packet

            unpin_packet(owner="cycle")
        except Exception as exc:
            logger.debug("brain cycle: unpin failed: %s", exc)

        cycle = {
            "ok": memo.get("ok", False),
            "version": "brain-cycle.v1",
            "trace_id": memo.get("trace_id"),
            "as_of": memo.get("as_of"),
            "generated_at": _now_str(),
            "memo": memo,
            "sentiment": {
                "level": (flow or {}).get("level"),
                "regime": ((flow or {}).get("regime") or {}).get("key"),
                "turning": bool(((flow or {}).get("turning_point") or {}).get("detected")),
            },
            "survey": {
                "regime": ((survey or {}).get("regime") or {}).get("status"),
                "divergence": bool(((survey or {}).get("divergence") or {}).get("detected")),
                "alerts": len((survey or {}).get("alerts") or []),
            },
            "experts": {
                "stance": (((experts or {}).get("consensus") or {}).get("stance")),
                "score": (((experts or {}).get("consensus") or {}).get("score")),
                "disagreement": (((experts or {}).get("consensus") or {}).get("disagreement")),
            },
            "crosscheck": {
                "ok": (crosscheck or {}).get("ok"),
                "score": (crosscheck or {}).get("score"),
                "counts": (crosscheck or {}).get("counts"),
                "comparable_ratio": (crosscheck or {}).get("comparable_ratio"),
                "conflicts": (crosscheck or {}).get("conflicts") or [],
            },
            "routing": {
                "route_id": (routing or {}).get("route_id"),
                "label": (routing or {}).get("label"),
                "bias": (routing or {}).get("bias"),
                "composite": (routing or {}).get("composite"),
                "cap_max": ((routing or {}).get("action") or {}).get("cap_max"),
                "coverage": (routing or {}).get("coverage"),
                "degraded": (routing or {}).get("degraded"),
            },
            "lineage": {
                "version": record.get("version"),
                "trace_id": record.get("trace_id"),
                "ok": record.get("ok"),
                "consistent": record.get("consistent"),
                "gaps": record.get("gaps"),
                "checks": record.get("checks"),
                "nodes": [
                    {"key": n["key"], "present": n["present"], "version": n["version"],
                     "as_of": n["as_of"], "digest": n["digest"]}
                    for n in record.get("nodes") or []
                ],
            },
            "stages": marks,
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
        }

        try:
            await self._kv_set(_KV_CYCLE, cycle)
            insights = await self._kv_get(_KV_INSIGHTS, [])
            insights.append({
                "ts": _now_str(),
                "source": "cycle",
                "regime": cycle["survey"]["regime"],
                "sentiment": cycle["sentiment"]["level"],
                "stance": memo.get("stance_label"),
                "cap": memo.get("position_cap"),
                "summary": memo.get("summary"),
            })
            await self._kv_set(_KV_INSIGHTS, insights[-_INSIGHTS_KEEP:])
            await self._persist_state()
        except Exception as exc:
            logger.debug("brain cycle persist skipped: %s", exc)
        return cycle

    async def alignment(self, refresh: bool = False, light: bool = True) -> Dict[str, Any]:
        """三层对证视图：数据配置 + 能力图 + 最近一次对证结果。

        ``light=True`` 只比对事实层与判断层（不跑六专家），用于页面上的常驻小条；
        需要完整三层时传 ``light=False``。
        """
        from server.brain_config import layer_labels, validate as validate_config
        from server.brain_capabilities import capability_graph
        from server.layer_crosscheck import read_latest, run as run_crosscheck

        config = validate_config()
        crosscheck = None if refresh else read_latest(light=light)
        if crosscheck is None:
            crosscheck = await run_crosscheck(light=light, force=refresh)

        graph = capability_graph()
        return {
            # 契约约定：ok 表示「接口调用成功」，不是业务结论。
            # 对证是否通过放在 healthy / crosscheck.ok，否则消费方会把「有冲突」当请求失败。
            "ok": True,
            "healthy": bool(crosscheck.get("ok")),
            "version": "brain-alignment.v1",
            "generated_at": _now_str(),
            "config": config,
            "layers": layer_labels(),
            "capabilities": {
                "total": graph["count"],
                "compute": graph["compute"],
                "decide": graph["decide"],
                "layers": graph["layers"],
            },
            "crosscheck": crosscheck,
        }

    async def last_cycle(self) -> Dict[str, Any]:
        return await self._kv_get(_KV_CYCLE, {"ok": False, "message": "尚未运行闭环"})

    async def _persist_state(self) -> None:
        await self._kv_set(_KV_STATE, {
            "started_at": self._started_at,
            "ticks": self._ticks,
            "tasks": {tid: t.to_dict() for tid, t in self.tasks.items()},
            "saved_at": _now_str(),
        })

    # --------------------------------------------------------------
    # 内置任务
    # --------------------------------------------------------------

    async def _task_market_survey(self) -> str:
        """动态市场调查 + 状态感知动态调频 + LLM 研判"""
        from server.market_survey_engine import market_survey_engine

        survey = await market_survey_engine.get_market_survey()
        regime = survey.get("regime") or {}
        score = regime.get("score")

        # 动态调频：极端行情（强势或偏弱）加密调查，平静市恢复基准频率
        task = self.tasks["market_survey"]
        if score is not None and (score >= 68 or score < 42):
            task.interval_sec = 900
        else:
            task.interval_sec = task.base_interval

        await self._advise(survey)
        return f"{regime.get('status', '--')}（情绪分 {score if score is not None else '--'}），异动 {len(survey.get('alerts') or [])} 条"

    async def _task_daily_loop(self) -> str:
        """触发日结闭环；从迭代结果中自动提炼经验（学习闭环）"""
        from server.trading import trading_facade

        result = await trading_facade.loop.run_daily_routine(trigger="brain")
        if not isinstance(result, dict):
            return "闭环已执行（无返回）"
        if result.get("skipped"):
            return f"跳过：{result.get('reason', '今日已执行')}"

        actions = result.get("actions") or []
        lessons = await self._kv_get(_KV_LESSONS, [])
        for action in actions[:5]:
            atype = action.get("action", "?")
            sid = action.get("strategy_id", "?")
            reason = action.get("reason", "")
            lessons.append({
                "ts": _now_str(), "source": "daily_loop",
                "lesson": f"策略 {sid} 被{atype}：{reason}",
            })
        if actions:
            await self._kv_set(_KV_LESSONS, lessons[-_LESSONS_KEEP:])
        return f"闭环完成：复盘 {result.get('new_reviews', 0)} 笔，迭代动作 {len(actions)} 个"

    async def _task_lhb_review(self) -> str:
        """收盘后龙虎榜复盘：拉席位明细 → 归因 → 落盘（供 /api/decision/lhb-review 查询）。

        幂等设计：同一交易日重复执行只是覆盖同一份 JSON，不会产生重复记录。
        营业部明细披露有滞后（通常 18:00~19:00 才齐），所以 15:35 起每 30 分钟重试。

        两道止损闸门：

        * **19:00 后拿到过结果就不再重跑**——19:00 之后拿到的就是当日最终版。
          只按「记录数 ≥30」判断的话，一个清淡日子只有 20 条记录，会一路重跑到 23:00。
        * **过了配置里的 `retry_window`（默认 19:30）仍无数据就收工**，
          而不是「连续失败 N 次就停」。这里刻意不用失败计数：交易日 15:35 第一次跑时
          数据本来就还没披露，按次数计会在 16:35 就永久放弃，
          等 18:00 数据补齐时反而不跑了——把正常等待误判成异常。
        """
        from server import lhb_review

        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        cfg = lhb_review.config()
        retry_until = str((cfg.get("schedule") or {}).get("retry_window") or "19:30")
        now_hm = now.strftime("%H:%M")

        state = self._lhb_fails
        if state.get("date") != today:
            state.clear()
            state["date"] = today

        cached = lhb_review.load(today)
        if isinstance(cached, dict) and cached.get("ok"):
            records = (cached.get("summary") or {}).get("records") or 0
            stamp = str(cached.get("generated_at") or "")
            done_after_close = stamp >= f"{today}T19:00:00"
            if records >= 30 or (done_after_close and now.hour >= 19):
                return f"{today} 龙虎榜复盘已就绪（{records} 条），本轮跳过"

        if state.get("closed"):
            return f"{today} 已过重试窗口（{retry_until}）仍无龙虎榜数据，今日不再尝试"

        report = await lhb_review.build(date=today)
        if not report.get("ok"):
            if now_hm >= retry_until:
                state["closed"] = True
                return (f"{today} 已过重试窗口（{retry_until}）仍无龙虎榜数据"
                        f"（非交易日或当日无榜），今日不再尝试")
            return (f"{today} 龙虎榜尚未披露（{now_hm}），"
                    f"窗口内继续重试至 {retry_until}：{report.get('message')}")
        summary = report.get("summary") or {}
        return (f"{today} 龙虎榜复盘完成：{summary.get('records', 0)} 条 / "
                f"{summary.get('stocks', 0)} 只票，"
                f"主因分布 {summary.get('by_motive')}")

    async def _task_memory_distill(self) -> str:
        """经验蒸馏：把近期洞察+调查历史浓缩为长期经验（Hermes /learn 模式）"""
        insights = await self._kv_get(_KV_INSIGHTS, [])
        if not insights:
            return "暂无洞察可蒸馏"
        from server.market_survey_engine import load_survey_history
        history = load_survey_history(limit=10)
        regimes = [h.get("regime") for h in history if h.get("regime")]
        dominant = max(set(regimes), key=regimes.count) if regimes else None

        lessons = await self._kv_get(_KV_LESSONS, [])
        if dominant and len(regimes) >= 3:
            text = f"近 {len(regimes)} 次市场调查的主导状态为「{dominant}」，策略评估应以此市场环境为主要参照"
            if not any(l.get("lesson") == text for l in lessons):
                lessons.append({"ts": _now_str(), "source": "distill", "lesson": text})
        # 清理过期洞察引用，保持记忆健康
        await self._kv_set(_KV_LESSONS, lessons[-_LESSONS_KEEP:])
        return f"经验库现有 {len(lessons[-_LESSONS_KEEP:])} 条，主导市场状态 {dominant or '未知'}"

    async def _task_intel_scan(self) -> str:
        """情报扫描：世界局势/地缘/重大事件 → 市场影响评估 → 写入主脑记忆"""
        from server.agents.intel_agent import scan_and_persist

        # 盘后降频：15:00 收盘后外部轮询价值下降，间隔放宽到 1h；盘中恢复基准 30min
        task = self.tasks["intel_scan"]
        task.interval_sec = 3600 if time.localtime().tm_hour >= 15 else task.base_interval

        payload = await scan_and_persist(self._kv_get, self._kv_set)
        return f"风险 {payload['risk_level']}（{payload['risk_score']}分），命中情报 {payload['news_matched']} 条"

    async def _task_expert_panel(self) -> str:
        """六专家独立研判与主脑聚合；规则引擎离线可运行，不依赖 LLM。"""
        from server.decision_experts import run_expert_panel

        result = await run_expert_panel(force=False)
        if not result.get("ok"):
            return f"专家研判暂无结论：{result.get('message', '输入不足')}"
        consensus = result.get("consensus") or {}
        return (
            f"共识 {consensus.get('score'):+.2f} / 分歧 {consensus.get('disagreement'):.2f}，"
            f"结论 {consensus.get('label')}，仓位上限 {consensus.get('position_cap')}%"
        )

    async def _task_factor_chan(self) -> str:
        """动态因子缠论：真实选股 + 缠论买卖点，写入洞察供策略研判"""
        from server.qlib_factor_engine import qlib_engine
        screen = await qlib_engine.dynamic_screen(limit=8)
        items = screen.get("items") or []
        if not items:
            return "动态因子缠论：实时行情不可用"

        buys = [i for i in items if i.get("chan", {}).get("buy_point")]
        sells = [i for i in items if i.get("chan", {}).get("sell_point")]
        lead = items[0]
        insights = await self._kv_get(_KV_INSIGHTS, [])
        insights.append({
            "ts": _now_str(), "source": "factor_chan",
            "regime": None, "score": None,
            "text": (
                f"动态因子缠论：Top1 {lead['name']}({lead['code']}) 综合分 {lead['score']}，"
                f"缠论{lead['chan'].get('buy_point') or lead['chan'].get('sell_point') or '无明确点'}；"
                f"全池 {len(items)} 只，买点 {len(buys)} 个、卖点 {len(sells)} 个。"
            )[:200],
        })
        await self._kv_set(_KV_INSIGHTS, insights[-_INSIGHTS_KEEP:])
        return f"动态因子缠论：Top {lead['name']} {lead['score']} 分，买点 {len(buys)} / 卖点 {len(sells)}"

    async def _advise(self, survey: Dict[str, Any]) -> None:
        """LLM 策略研判：结合历史经验生成洞察；无 LLM 时规则兜底，确保始终产出有效研判。"""
        regime = survey.get("regime") or {}
        lessons = await self._kv_get(_KV_LESSONS, [])
        insights = await self._kv_get(_KV_INSIGHTS, [])
        intel = await self._kv_get("brain_intel", None)
        prev_regime = insights[-1].get("regime") if insights else None

        rotation = survey.get("rotation") or []
        rotation_text = "；".join(f"{r.get('sector')}{r.get('stage')}" for r in rotation[:3]) or "暂无"
        intel_text = "暂无情报评估"
        if intel and intel.get("brief"):
            intel_text = f"风险{intel.get('risk_level')}({intel.get('risk_score')}分)：{intel.get('brief', '')[:80]}"

        # 尝试 LLM 生成
        text: Optional[str] = None
        try:
            from server.llm import get_llm_client
            client = get_llm_client()
            if client.api_key:
                lessons_text = "；".join(l.get("lesson", "")[:50] for l in lessons[-5:]) or "暂无"
                prompt = f"""你是 DAPENG Alpha 量化交易平台的 AI 主脑，负责把市场调查结果转化为策略层面的注意方向。

【最新市场调查】状态：{regime.get('status')}（情绪分 {regime.get('score') or '--'}）；{regime.get('liquidity_env', '')}；板块：{rotation_text}
【情报评估（世界局势/地缘）】{intel_text}
【你的历史经验】{lessons_text}
【上一次研判的市场状态】{prev_regime or '首次'}

输出要求（中文，两段，共 120 字以内）：
1. 当前市场环境与情报背景下，趋势型策略与均值回归型策略哪类更应受重视，为什么（结合真实数据）
2. 若市场状态较上次发生切换，明确指出"状态切换：X→Y"及应对；若情报风险为中/高，给出一句风控提示；否则给出一句风险提示
禁止虚构数据，不要列表符号。"""
                try:
                    out = await client.chat(prompt, "你是量化平台的策略中台 AI，只基于给定数据发言。", max_tokens=300)
                    if out and len(out.strip()) >= 10:
                        text = out.strip()[:400]
                except Exception as exc:
                    logger.debug("brain advise llm failed: %s", exc)
        except Exception as exc:
            logger.debug("brain advise llm unavailable: %s", exc)

        # 规则兜底研判（无 Key / LLM 失败时仍产出有效结论）
        if not text:
            text = self._rule_advise(regime, intel_text, rotation_text, prev_regime)

        insights.append({
            "ts": _now_str(),
            "regime": regime.get("status"),
            "score": regime.get("score"),
            "regime_shift": bool(prev_regime and prev_regime != regime.get("status")),
            "text": text[:400],
            "ai_powered": True,
        })
        await self._kv_set(_KV_INSIGHTS, insights[-_INSIGHTS_KEEP:])

    @staticmethod
    def _rule_advise(regime: Dict[str, Any], intel_text: str, rotation_text: str, prev_regime: Optional[str]) -> str:
        """规则引擎研判（无 LLM 兜底）：基于真实情绪分 + 板块 + 情报给出可解释结论。"""
        status = regime.get("status", "数据不足")
        score = regime.get("score")
        liquidity = regime.get("liquidity_env", "") or "流动性未知"

        if score is not None and score >= 55:
            stance = "趋势型策略更应受重视，市场情绪偏强，宜顺势持有强势主线而非频繁反向博弈"
        elif score is not None and score < 42:
            stance = "均值回归与防御型策略更应受重视，情绪偏弱，宜降低仓位、等待超跌反弹信号"
        else:
            stance = "震荡市中趋势与均值回归需均衡配置，趋势策略注意止盈、反转策略等待箱体边界机会"

        shift = ""
        if prev_regime and prev_regime != status:
            shift = f"；状态切换：{prev_regime}→{status}，应及时校准策略参数"
        risk_hint = "风险提示：严格控仓、执行止损纪律"
        if "中" in intel_text or "高" in intel_text:
            risk_hint = "风控提示：全球情报风险处于中/高级别，建议降低总仓位并提高现金比例"

        return (f"当前市场状态「{status}」（情绪分 {score if score is not None else '--'}），{liquidity}，"
                f"相对活跃方向：{rotation_text}。{stance}{shift}。{risk_hint}。")[:400]

    # --------------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------------

    async def status(self) -> Dict[str, Any]:
        llm_configured = False
        model = None
        try:
            from server.llm import get_llm_client
            client = get_llm_client()
            llm_configured = bool(client.api_key)
            model = client.model
        except Exception:
            pass

        insights = await self._kv_get(_KV_INSIGHTS, [])
        lessons = await self._kv_get(_KV_LESSONS, [])
        intel = await self._kv_get("brain_intel", None)
        intel_history = await self._kv_get("brain_intel_history", [])
        from server.market_survey_engine import load_survey_history

        return {
            "ok": True,
            "running": self._running,
            "started_at": self._started_at,
            "uptime_sec": int(time.time() - self._start_ts) if self._start_ts else 0,
            "ticks": self._ticks,
            "llm": {"configured": llm_configured, "model": model},
            "tasks": [t.to_dict() for t in self.tasks.values()],
            "insights": insights[-5:],
            "lessons": lessons[-10:],
            "intel": intel,
            "intel_history": intel_history[-5:],
            "memory": {"insights_count": len(insights), "lessons_count": len(lessons)},
            "survey_history": load_survey_history(limit=5),
            "kernel": "DAPENG Brain V1.1（Hermes 模式：cron 调度 / 分层记忆 / 学习闭环 / 技能注册 + 情报融合）",
            "now": _now_str(),
        }


dapeng_brain = DAPENGBrain()
