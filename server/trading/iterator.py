"""闭环核心件 ② —— 策略迭代引擎（StrategyIterator）

闭环定位：执行交易 → 复盘分析 →【策略积累 + 动态优化】

决策矩阵（docs/capability-evolution-plan-2026.md §1.2b 的工程实现）：

  样本：该策略闭合交易数 n（FIFO 配对，仅模拟单）
  指标：胜率 / 盈亏比 / 策略级回撤（累计盈亏曲线最大回撤占初始资金 %）

  ✅ 晋级 promote   paper_validating 且模拟天数达标 且 n≥5 且 胜率≥55 且 PF≥1.5 且 回撤≤10
                   → paper_passed + 策略库沉淀（被验证有效的参数快照）
  ⬇ 降级 demote    paper_validating|paper_passed 且 n≥5 且 (胜率<45 或 PF<1.2 或 回撤>10)
                   → draft + 停用标的；连续 2 次降级 → retired（退役）
  🔧 调参 propose  n≥5 且 指标"差一点达标"（45≤胜率<55 或 1.2≤PF<1.5）
                   → 单变量 ±20% 提案，先入 param_proposals（pending）
  提案生命周期：pending →（下轮）双轨验证：全样本回测 + 末 20% 样本外复测，
                   新参数双双不差 → applied（自动改参数→回退 draft→自动回测
                   →backtested→自动重回 paper_validating，标的自动恢复）；
                   否则 rejected_auto。人工可经 API 提前 apply / reject。

铁律（与规划一致）：晋级严格、降级保守、调参必须过门闸全流程重验，
任何动作写 audit_log + iteration_log，全程无人工也可闭环。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from server.tencent_kline import fetch_tencent_kline

from .backtest import run_backtest
from .models import DEFAULT_INITIAL_CASH, GateState
from .review_engine import round_trips_with_entry, TRADE_REVIEWS_KEY
from .store import TradingStore
from .strategies import get as get_strategy, has as has_strategy

logger = logging.getLogger(__name__)

ITERATION_LOG_KEY = "iteration_log"
PROPOSALS_KEY = "param_proposals"
LIBRARY_KEY = "strategy_library"

# ---- 决策矩阵阈值（与规划 §1.2b 一致）----
MIN_TRADES = 5
WIN_PASS, WIN_FAIL = 55.0, 45.0
PF_PASS, PF_FAIL = 1.5, 1.2
MAXDD_PASS = 10.0            # 策略级回撤 ≤10%（占初始资金）
DEMOTE_RETIRE_STRIKES = 2    # 连续降级次数 → 退役
TUNE_STEP = 0.20             # 单次调参幅度 ±20%（规划上限 ±30% 取保守档）
PROPOSAL_COOLDOWN_DAYS = 7   # 同策略两次提案最小间隔
# 初始本金不在本文件定义（单一真相源：models.DEFAULT_INITIAL_CASH）。
# 运行时以虚拟盘账户 paper_account.initial_cash 为准，由 run() 读取后传入
# strategy_metrics()；仅在账户未初始化时回退到该缺省值。
_MAX_LOG = 200
_MAX_LIBRARY = 50


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# 策略级指标
# ============================================================

def strategy_metrics(trips: List[Dict[str, Any]],
                     initial_cash: Optional[float] = None) -> Dict[str, Any]:
    """由该策略的闭合交易列表计算决策矩阵指标

    initial_cash: 计算最大回撤百分比的分母（**必须传虚拟盘账户的真实初始本金**，
    否则当账户本金 ≠ 缺省 100 万时回撤百分比会被系统性算错）。
    未传时回退到 models.DEFAULT_INITIAL_CASH。
    """
    n = len(trips)
    wins = [t for t in trips if t["pnl"] > 0]
    gross_profit = sum(t["pnl"] for t in trips if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in trips if t["pnl"] < 0))
    win_rate = round(len(wins) / n * 100, 1) if n else None
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (None if n else None)

    # 策略级回撤：累计盈亏曲线的最大峰谷差 / 实际初始资金
    curve, cum = [], 0.0
    for t in sorted(trips, key=lambda x: x.get("sell_ts") or ""):
        cum += t["pnl"]
        curve.append(cum)
    peak, mdd = 0.0, 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    base = initial_cash if (initial_cash and initial_cash > 0) else DEFAULT_INITIAL_CASH
    maxdd_pct = round(abs(mdd) / base * 100, 2)

    return {"trades": n, "win_rate_pct": win_rate, "profit_factor": pf,
            "total_pnl": round(sum(t["pnl"] for t in trips), 2),
            "maxdd_pct": maxdd_pct, "sample_ready": n >= MIN_TRADES,
            "maxdd_base_cash": base}


def _verdict(m: Dict[str, Any]) -> str:
    """pass / mediocre / fail / na（样本不足）"""
    if not m["sample_ready"]:
        return "na"
    wr, pf, dd = m["win_rate_pct"], m["profit_factor"], m["maxdd_pct"]
    if (wr is not None and wr < WIN_FAIL) or (pf is not None and pf < PF_FAIL) or dd > MAXDD_PASS:
        return "fail"
    pass_wr = wr is not None and wr >= WIN_PASS
    pass_pf = pf is not None and pf >= PF_PASS
    if pass_wr and pass_pf and dd <= MAXDD_PASS:
        return "pass"
    return "mediocre"


# ============================================================
# 迭代引擎
# ============================================================

class StrategyIterator:
    def __init__(self, store: TradingStore):
        self._store = store

    # --------------------------------------------------------
    # 主入口：每日收盘后调用；full=True（周五/手动）加跑调参与退役检查
    # --------------------------------------------------------
    async def run(self, full: bool = False, trigger: str = "auto") -> Dict[str, Any]:
        specs = await self._store.kv_get("strategies", default=[]) or []
        _, orders = await self._store.list_orders(limit=10000)
        # 演练造数订单不参与策略指标/晋级降级（用假交易评估真策略会污染决策）
        real_orders = [o for o in orders if not o.get("is_drill")]
        drill_excluded = len(orders) - len(real_orders)
        all_trips = round_trips_with_entry(real_orders)

        # 最大回撤的分母必须是虚拟盘账户的真实初始本金（与 paper.py 的 _ACCOUNT_KEY 同一 KV）
        _acc = await self._store.kv_get("paper_account") or {}
        try:
            initial_cash = float(_acc.get("initial_cash") or 0) or None
        except (TypeError, ValueError):
            initial_cash = None

        actions: List[Dict[str, Any]] = []
        changed = False
        for spec in specs:
            sid = spec.get("strategy_id", "")
            if not has_strategy(sid):
                continue
            trips = [t for t in all_trips if t.get("strategy_id") == sid]
            m = strategy_metrics(trips, initial_cash=initial_cash)
            verdict = _verdict(m)
            gate = spec.get("gate_state", GateState.DRAFT.value)

            try:
                if gate in (GateState.PAPER_VALIDATING.value, GateState.PAPER_PASSED.value):
                    if verdict == "fail":
                        actions.append(await self._demote(spec, m, trigger))
                        changed = True
                    elif (gate == GateState.PAPER_VALIDATING.value and verdict == "pass"
                          and int(spec.get("paper_days_done", 0)) >= int(spec.get("paper_days_required", 5))):
                        actions.append(await self._promote(spec, m, trigger))
                        changed = True
                    elif full and verdict == "mediocre":
                        act = await self._maybe_propose(spec, m, trigger)
                        if act:
                            actions.append(act)
                            changed = True
                elif full and gate == GateState.DRAFT.value and not spec.get("retired"):
                    # 长期零信号且曾被降级 → 退役（防沉默复活）
                    if int(spec.get("demote_count", 0)) >= DEMOTE_RETIRE_STRIKES:
                        spec["retired"] = True
                        actions.append(self._log(spec, "retire", gate, gate, m,
                                                 "连续降级达上限，标记退役"))
                        changed = True
            except Exception:
                logger.error("StrategyIterator: 处理策略 %s 失败", sid, exc_info=True)

        if changed:
            await self._store.kv_set("strategies", specs)

        # 提案验证/应用（每轮都跑，给人工否决留出窗口期）
        proposal_actions = await self._process_proposals(specs, trigger)
        actions.extend(proposal_actions)

        await self._store.audit("loop", "iteration_run", {
            "trigger": trigger, "full": full,
            "orders_excluded_drill": drill_excluded,
            "actions": [{"strategy_id": a.get("strategy_id"), "action": a.get("action")} for a in actions],
        })
        return {"actions": actions, "strategies_checked": len(specs),
                "orders_excluded_drill": drill_excluded}

    # --------------------------------------------------------
    # 动作：晋级（+ 策略库沉淀）
    # --------------------------------------------------------
    async def _promote(self, spec: Dict[str, Any], m: Dict[str, Any], trigger: str) -> Dict[str, Any]:
        sid = spec["strategy_id"]
        old = spec.get("gate_state")
        spec["gate_state"] = GateState.PAPER_PASSED.value
        await self._store.audit("loop", "gate_advanced", {
            "strategy_id": sid, "via": f"iterator:{trigger}",
            "from": old, "to": GateState.PAPER_PASSED.value, "metrics": m,
        })
        await library_sediment(self._store, spec, m, event="promoted")
        return self._log(spec, "promote", old, GateState.PAPER_PASSED.value, m,
                         "模拟指标达标，自动晋级并沉淀策略库")

    # --------------------------------------------------------
    # 动作：降级（连续降级 → 退役）
    # --------------------------------------------------------
    async def _demote(self, spec: Dict[str, Any], m: Dict[str, Any], trigger: str) -> Dict[str, Any]:
        sid = spec["strategy_id"]
        old = spec.get("gate_state")
        spec["gate_state"] = GateState.DRAFT.value
        # 停用但保留标的快照，供调参提案验证与日后恢复
        if spec.get("enabled_codes"):
            spec["suspended_codes"] = list(spec.get("enabled_codes") or [])
            spec["enabled_codes"] = []
        spec["paper_days_done"] = 0
        spec["demote_count"] = int(spec.get("demote_count", 0)) + 1
        retired = spec["demote_count"] >= DEMOTE_RETIRE_STRIKES
        if retired:
            spec["retired"] = True
        await self._store.audit("loop", "gate_demoted", {
            "strategy_id": sid, "via": f"iterator:{trigger}",
            "from": old, "metrics": m, "demote_count": spec["demote_count"],
            "retired": retired,
        }, result="warn")
        await library_sediment(self._store, spec, m, event="demoted")
        detail = f"模拟指标失败（胜率 {m['win_rate_pct']}%、PF {m['profit_factor']}、回撤 {m['maxdd_pct']}%），自动降级"
        if retired:
            detail += "；连续降级达上限，标记退役"
        return self._log(spec, "retire" if retired else "demote", old, GateState.DRAFT.value, m, detail)

    # --------------------------------------------------------
    # 动作：调参提案（单变量 ±20%，冷却 7 天）
    # --------------------------------------------------------
    async def _maybe_propose(self, spec: Dict[str, Any], m: Dict[str, Any], trigger: str) -> Optional[Dict[str, Any]]:
        sid = spec["strategy_id"]
        proposals: List[Dict[str, Any]] = await self._store.kv_get(PROPOSALS_KEY, []) or []
        if any(p.get("strategy_id") == sid and p.get("status") == "pending" for p in proposals):
            return None
        recent = [p for p in proposals if p.get("strategy_id") == sid and p.get("created_at", "") >=
                  (datetime.now() - timedelta(days=PROPOSAL_COOLDOWN_DAYS)).strftime("%Y-%m-%d")]
        if recent:
            return None

        candidate = None
        basis: Optional[str] = None
        clamp_note: Optional[str] = None
        # 优先：读 trade_reviews 的归因 tags 做**定向**调整（用户要的"自我复盘→优化"）
        tags = await self._strategy_tags(sid)
        tag_cand = self._tag_driven_candidate(spec, tags)
        if tag_cand is not None:
            candidate = tag_cand[:3]
            basis = tag_cand[3]
            clamp_note = tag_cand[4]
        else:
            candidate = self._next_param_candidate(spec)
        if candidate is None:
            return None
        param, old_v, new_v = candidate

        pid = uuid.uuid4().hex[:12]
        if basis:
            reason = f"复盘归因『{basis}』（{tags.get(basis)} 次）→ 定向调整 {param} {old_v} → {new_v}"
            if clamp_note:
                reason += f"；{clamp_note}"
        else:
            reason = (f"指标未达标（胜率 {m['win_rate_pct']}%、PF {m['profit_factor']}、"
                      f"回撤 {m['maxdd_pct']}%），单变量微调提案")
        proposals.append({
            "id": pid, "strategy_id": sid, "param": param,
            "old_value": old_v, "new_value": new_v,
            "reason": reason,
            "attribution": ({"tag": basis, "tag_count": tags.get(basis), "param": param,
                             "direction": "上调" if new_v > old_v else "下调",
                             "clamp_note": clamp_note} if basis else None),
            "metrics_at_proposal": m, "status": "pending",
            "created_at": _now(), "trigger": trigger,
        })
        await self._store.kv_set(PROPOSALS_KEY, proposals[-100:])
        await self._store.audit("loop", "param_proposed", {
            "strategy_id": sid, "proposal_id": pid, "param": param,
            "old": old_v, "new": new_v,
        })
        return self._log(spec, "propose", spec.get("gate_state"), spec.get("gate_state"), m,
                         f"提案：{param} {old_v} → {new_v}（待双轨验证）")

    async def _strategy_tags(self, sid: str) -> Dict[str, int]:
        """统计该策略近期复盘归因 tags 频次（trade_reviews）。"""
        reviews: List[Dict[str, Any]] = await self._store.kv_get(TRADE_REVIEWS_KEY, []) or []
        counts: Dict[str, int] = {}
        for r in reviews[-60:]:
            if r.get("strategy_id") != sid:
                continue
            for t in (r.get("tags") or []):
                counts[str(t)] = counts.get(str(t), 0) + 1
        return counts

    @staticmethod
    def _tag_driven_candidate(spec: Dict[str, Any],
                              tags: Dict[str, int]) -> Optional[Tuple[str, Any, Any, str, Optional[str]]]:
        """按归因 tag 选出**定向**参数调整 (param, old, new, tag, clamp_note)；无匹配 → None。

        tag → 参数与方向（在策略已有数值参数里挑第一个命中的）：
          止盈偏早→放宽出场(atr_mult/entry_days/exit_days 上调)；
          浮盈回吐→收紧移动止盈(atr_mult 下调)；
          止损被洗/波动超限→放宽止损(fixed_stop_pct/stop_pct/atr_mult 上调)；
          快进快出→拉长持有(entry_days 上调)；长线持有→缩短持有(exit_days 下调)。
        clamp_note：当调整被边界钳住时如实说明（避免看起来"完全按归因调整了"）。
        """
        rules = {
            "止盈偏早": (("atr_mult", "entry_days", "exit_days"), +1),
            "浮盈回吐": (("atr_mult", "atr_period"), -1),
            "止损被洗": (("fixed_stop_pct", "stop_pct", "atr_mult"), +1),
            "波动超限": (("fixed_stop_pct", "stop_pct"), +1),
            "快进快出": (("entry_days",), +1),
            "长线持有": (("exit_days",), -1),
        }
        strategy = get_strategy(spec["strategy_id"])
        params = dict(strategy.default_params)
        params.update(spec.get("params") or {})
        for tag, cnt in sorted(tags.items(), key=lambda kv: -kv[1]):
            rule = rules.get(tag)
            if not rule:
                continue
            keys, direction = rule
            for key in keys:
                if key not in params or not isinstance(params[key], (int, float)):
                    continue
                old_v = params[key]
                raw = old_v * (1 + TUNE_STEP * direction)
                clamp_note: Optional[str] = None
                if isinstance(old_v, int):
                    new_v = int(round(raw))
                    if new_v == old_v:
                        new_v = old_v + direction
                    bounded = max(1, min(new_v, 200))
                    if bounded != new_v:
                        clamp_note = f"已按边界夹紧（{key} ∈ [1,200]）"
                    new_v = bounded
                else:
                    bounded = round(max(0.1, min(raw, 10.0)), 2)
                    if raw > bounded + 1e-9:
                        clamp_note = f"已按上限夹紧（{key} 上限 {bounded}）"
                    elif raw < bounded - 1e-9:
                        clamp_note = f"已按下限夹紧（{key} 下限 {bounded}）"
                    new_v = bounded
                if new_v == old_v:
                    continue
                probe = dict(params)
                probe[key] = new_v
                if "fast" in probe and "slow" in probe and probe["fast"] >= probe["slow"]:
                    continue
                return key, old_v, new_v, tag, clamp_note
        return None

    @staticmethod
    def _next_param_candidate(spec: Dict[str, Any]) -> Optional[Tuple[str, Any, Any]]:
        """轮转挑选一个数值参数 ±20%（int 至少 ±1），含 fast<slow 类常识护栏"""
        sid = spec["strategy_id"]
        strategy = get_strategy(sid)
        params = dict(strategy.default_params)
        params.update(spec.get("params") or {})
        keys = sorted(k for k, v in params.items() if isinstance(v, (int, float)))
        if not keys:
            return None
        cursor = int(spec.get("tune_cursor", 0))
        direction = 1 if int(spec.get("tune_dir", 0)) % 2 == 0 else -1
        for offset in range(len(keys)):
            key = keys[(cursor + offset) % len(keys)]
            old_v = params[key]
            raw = old_v * (1 + TUNE_STEP * direction)
            if isinstance(old_v, int):
                new_v: Any = int(round(raw))
                if new_v == old_v:
                    new_v = old_v + direction
                new_v = max(1, min(new_v, 200))
            else:
                new_v = round(max(0.1, min(raw, 10.0)), 2)
                if new_v == old_v:
                    continue
            # 常识护栏：均线类 fast 必须 < slow
            probe = dict(params)
            probe[key] = new_v
            if "fast" in probe and "slow" in probe and probe["fast"] >= probe["slow"]:
                continue
            spec["tune_cursor"] = cursor + offset + 1
            spec["tune_dir"] = int(spec.get("tune_dir", 0)) + 1
            return key, old_v, new_v
        return None

    # --------------------------------------------------------
    # 提案验证与应用（双轨：全样本 + 末 20% 样本外）
    # --------------------------------------------------------
    async def _process_proposals(self, specs: List[Dict[str, Any]], trigger: str) -> List[Dict[str, Any]]:
        proposals: List[Dict[str, Any]] = await self._store.kv_get(PROPOSALS_KEY, []) or []
        actions: List[Dict[str, Any]] = []
        changed = False
        specs_changed = False
        for p in proposals:
            if p.get("status") != "pending":
                continue
            spec = next((s for s in specs if s.get("strategy_id") == p.get("strategy_id")), None)
            if spec is None:
                p["status"] = "expired"
                changed = True
                continue
            codes = spec.get("enabled_codes") or spec.get("suspended_codes") or []
            if not codes:
                p["status"] = "awaiting_codes"
                changed = True
                continue
            try:
                outcome = await self._validate_proposal(spec, p, codes[0])
            except Exception:
                logger.warning("StrategyIterator: 提案 %s 验证异常", p.get("id"), exc_info=True)
                continue
            if outcome == "applied":
                specs_changed = True
                actions.append(self._log(spec, "apply", spec.get("gate_state"),
                                         spec.get("gate_state"), p.get("metrics_at_proposal") or {},
                                         f"提案通过双轨验证：{p['param']} {p['old_value']} → {p['new_value']}，"
                                         f"已自动改参并重回模拟验证"))
            elif outcome == "rejected_auto":
                actions.append(self._log(spec, "reject", spec.get("gate_state"),
                                         spec.get("gate_state"), p.get("metrics_at_proposal") or {},
                                         f"提案未过双轨验证，自动拒绝：{p['param']} {p['old_value']} → {p['new_value']}"))
            changed = True
        if changed:
            await self._store.kv_set(PROPOSALS_KEY, proposals[-100:])
        if specs_changed:
            await self._store.kv_set("strategies", specs)
        return actions

    async def _validate_proposal(self, spec: Dict[str, Any], p: Dict[str, Any], code: str) -> str:
        """验证并应用/拒绝提案。返回 applied / rejected_auto"""
        sid = spec["strategy_id"]
        strategy = get_strategy(sid)
        klines = await fetch_tencent_kline(code, period="day", limit=320)
        klines = [k for k in (klines or []) if isinstance(k, dict) and "c" in k and "o" in k]
        if len(klines) < 120:
            p["status"] = "awaiting_codes"
            p["note"] = "K线不足，暂缓验证"
            return "deferred"

        old_params = dict(strategy.default_params)
        old_params.update(spec.get("params") or {})
        new_params = dict(old_params)
        new_params[p["param"]] = p["new_value"]

        oos_n = max(40, len(klines) // 5)
        full, oos = klines, klines[-oos_n:]
        try:
            old_full = run_backtest(strategy, code, full, old_params)
            new_full = run_backtest(strategy, code, full, new_params)
            old_oos = run_backtest(strategy, code, oos, old_params)
            new_oos = run_backtest(strategy, code, oos, new_params)
        except ValueError as e:
            p["status"] = "rejected_auto"
            p["note"] = f"回测失败：{e}"
            return "rejected_auto"

        def score(m: Dict[str, Any]) -> float:
            return (m.get("win_rate") or 0.0) * (m.get("profit_factor") or 0.0)

        p["validation"] = {
            "code": code,
            "old_full": {k: old_full[k] for k in ("win_rate", "profit_factor", "max_drawdown", "total_trades")},
            "new_full": {k: new_full[k] for k in ("win_rate", "profit_factor", "max_drawdown", "total_trades")},
            "old_oos": {k: old_oos[k] for k in ("win_rate", "profit_factor", "max_drawdown", "total_trades")},
            "new_oos": {k: new_oos[k] for k in ("win_rate", "profit_factor", "max_drawdown", "total_trades")},
        }
        # 样本外作证能力检查：OOS 窗口交易 <3 笔时 0≥0 的平凡通过会放进噪声调参，
        # 此时改用更严格的全样本门槛（+5% 边际改善才算数）——防过拟合纪律 §1.2c
        oos_ready = max(new_oos.get("total_trades") or 0, old_oos.get("total_trades") or 0) >= 3
        if oos_ready:
            accept = (score(new_full) > score(old_full)
                      and score(new_oos) >= score(old_oos) * 0.9)
        else:
            accept = score(new_full) > score(old_full) * 1.05
            p["note"] = "样本外交易不足 3 笔无法作证，按更严格全样本门槛（改善 >5%）判定"

        if not accept:
            p["status"] = "rejected_auto"
            p["resolved_at"] = _now()
            await self._store.audit("loop", "param_rejected", {
                "strategy_id": sid, "proposal_id": p["id"], "validation": p["validation"],
            }, result="denied")
            return "rejected_auto"

        # ---- 应用：改参数（门禁规则→回退 draft）→ 自动回测 → 自动重回模拟验证 ----
        spec["params"] = new_params
        spec["gate_state"] = GateState.DRAFT.value
        spec["paper_days_done"] = 0
        spec["demote_count"] = 0      # 参数已换代，降级计数清零（新参数生命周期重新开始）
        spec["gate_state"] = GateState.BACKTESTED.value
        spec["last_backtest"] = {
            "ts": _now(), "code": code, "days": len(klines),
            "metrics": {k: new_full[k] for k in
                        ("win_rate", "annual_return", "max_drawdown", "total_trades", "profit_factor")},
            "via": "iterator_auto",
        }
        # 自动重回模拟验证（恢复标的）
        restored = spec.get("suspended_codes") or spec.get("enabled_codes") or [code]
        spec["enabled_codes"] = restored
        spec["suspended_codes"] = []
        spec["gate_state"] = GateState.PAPER_VALIDATING.value
        p["status"] = "applied"
        p["resolved_at"] = _now()
        await self._store.audit("loop", "param_applied", {
            "strategy_id": sid, "proposal_id": p["id"], "param": p["param"],
            "old": p["old_value"], "new": p["new_value"],
            "restored_codes": restored, "backtest": spec["last_backtest"]["metrics"],
        })
        return "applied"

    # --------------------------------------------------------
    # 迭代日志
    # --------------------------------------------------------
    def _log(self, spec: Dict[str, Any], action: str, from_state: Optional[str],
             to_state: Optional[str], metrics: Dict[str, Any], detail: str) -> Dict[str, Any]:
        entry = {
            "ts": _now(), "strategy_id": spec.get("strategy_id"), "action": action,
            "from_state": from_state, "to_state": to_state,
            "metrics": {k: metrics.get(k) for k in
                        ("trades", "win_rate_pct", "profit_factor", "maxdd_pct", "total_pnl")},
            "detail": detail,
        }
        # 异步落 KV 由调用方统一处理（_flush_log）
        self._pending_logs = getattr(self, "_pending_logs", [])
        self._pending_logs.append(entry)
        return entry

    async def flush_logs(self) -> None:
        """把本轮累积的迭代日志写入 KV（run 结束后调用）"""
        logs = getattr(self, "_pending_logs", [])
        if not logs:
            return
        existing: List[Dict[str, Any]] = await self._store.kv_get(ITERATION_LOG_KEY, []) or []
        existing.extend(logs)
        await self._store.kv_set(ITERATION_LOG_KEY, existing[-_MAX_LOG:])
        self._pending_logs = []


# ============================================================
# 策略库沉淀（闭环第 3 环：策略积累）
# ============================================================

async def library_sediment(store: TradingStore, spec: Dict[str, Any],
                           metrics: Dict[str, Any], event: str) -> None:
    """把策略的关键状态变化沉淀进策略库：晋级时记录"被验证有效"的参数快照，
    降级时记录失败原因，形成可复用、可追溯的版本谱系。"""
    sid = spec.get("strategy_id", "")
    library: List[Dict[str, Any]] = await store.kv_get(LIBRARY_KEY, []) or []
    entry = next((e for e in library if e.get("strategy_id") == sid), None)
    if entry is None:
        try:
            name = get_strategy(sid).name
        except KeyError:
            name = sid
        entry = {"strategy_id": sid, "name": name, "lineage": 0,
                 "status": "candidate", "versions": [], "history": []}
        library.append(entry)

    if event == "promoted":
        entry["lineage"] = int(entry.get("lineage", 0)) + 1
        entry["status"] = "validated"
        entry["versions"].append({
            "version": entry["lineage"], "params": dict(spec.get("params") or {}),
            "promoted_at": _now(),
            "paper_metrics": {k: metrics.get(k) for k in
                              ("trades", "win_rate_pct", "profit_factor", "maxdd_pct", "total_pnl")},
            "backtest_metrics": (spec.get("last_backtest") or {}).get("metrics"),
        })
        entry["versions"] = entry["versions"][-20:]
        entry["history"].append({"ts": _now(), "event": "promoted",
                                 "detail": f"模拟验证通过（胜率 {metrics.get('win_rate_pct')}%、"
                                           f"PF {metrics.get('profit_factor')}），参数快照入库"})
    elif event == "demoted":
        entry["status"] = "demoted"
        entry["history"].append({"ts": _now(), "event": "demoted",
                                 "detail": f"指标失败降级（胜率 {metrics.get('win_rate_pct')}%、"
                                           f"PF {metrics.get('profit_factor')}、回撤 {metrics.get('maxdd_pct')}%）"})
    entry["history"] = entry["history"][-50:]
    await store.kv_set(LIBRARY_KEY, library[-_MAX_LIBRARY:])


# ============================================================
# 人工干预入口（evolution 路由调用）
# ============================================================

async def reject_proposal(store: TradingStore, proposal_id: str) -> Tuple[bool, str]:
    proposals: List[Dict[str, Any]] = await store.kv_get(PROPOSALS_KEY, []) or []
    p = next((x for x in proposals if x.get("id") == proposal_id), None)
    if p is None:
        return False, "提案不存在"
    if p.get("status") != "pending":
        return False, f"提案当前状态 {p.get('status')}，仅 pending 可拒绝"
    p["status"] = "rejected_manual"
    p["resolved_at"] = _now()
    await store.kv_set(PROPOSALS_KEY, proposals[-100:])
    await store.audit("api", "param_rejected", {"proposal_id": proposal_id, "via": "manual"})
    return True, "已拒绝该调参提案"


async def force_apply_proposal(store: TradingStore, proposal_id: str) -> Tuple[bool, str]:
    """人工立即应用：跳过下轮等待，直接走同一套双轨验证+应用流程"""
    proposals: List[Dict[str, Any]] = await store.kv_get(PROPOSALS_KEY, []) or []
    p = next((x for x in proposals if x.get("id") == proposal_id), None)
    if p is None:
        return False, "提案不存在"
    if p.get("status") != "pending":
        return False, f"提案当前状态 {p.get('status')}，仅 pending 可应用"
    specs = await store.kv_get("strategies", default=[]) or []
    spec = next((s for s in specs if s.get("strategy_id") == p.get("strategy_id")), None)
    if spec is None:
        return False, "策略配置不存在"
    codes = spec.get("enabled_codes") or spec.get("suspended_codes") or []
    if not codes:
        return False, "策略无可用标的，无法验证"
    it = StrategyIterator(store)
    outcome = await it._validate_proposal(spec, p, codes[0])
    await store.kv_set(PROPOSALS_KEY, proposals[-100:])
    await store.kv_set("strategies", specs)
    if outcome == "applied":
        return True, "提案已通过双轨验证并应用，策略已重回模拟验证"
    if outcome == "deferred":
        return False, "K线数据不足，暂缓验证（下轮自动重试）"
    return False, "提案未通过双轨验证，已自动拒绝"
