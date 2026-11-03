"""主脑能力契约（brain-capability.v1）。

为什么需要一层「能力声明」
--------------------------
主脑（``server/brain.py``）此前只有**调度**：什么时间跑哪个任务。但「主脑能算什么、
能决策什么、每项能力依赖哪些数据、数据不到位时退化成什么样、下一阶段要提升到什么水平」
散落在各任务函数里，既无法被前端展示，也无法被测试守卫。

本模块把这三件事显式化：

1. **计算能力（compute）**：把原始数据变成指标/状态，不产出交易结论；
2. **决策能力（decide）**：把指标/状态变成结论（研判、共识、仓位上限、备忘录）；
3. **协作关系（graph）**：能力之间、能力与数据资源之间的有向依赖，单向、可拓扑排序。

约定
----
* 能力 ``inputs`` 用的是 **data_bus 资源键**（``coverage`` / ``packet`` / ``sentiment`` /
  ``sentiment_flow`` / ``scan`` / ``survey`` / ``experts``），与总线注册名一致；
* ``metrics[].target`` 是**下一阶段的提升目标**，不是当前值；当前值由 ``report()``
  按数据可用性给出 ``ok`` / ``degraded`` / ``blocked``；
* 纯声明 + 纯函数，不 import 任何 IO 模块，便于单测与前端直接消费。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

BRAIN_VERSION = "brain.v2"
CAPABILITY_VERSION = "brain-capability.v1"

#: 主脑在一次闭环里允许的立场
STANCES = ("attack", "balanced", "defend", "observe")


@dataclass(frozen=True)
class Capability:
    """一项能力：算什么 / 依赖谁 / 产出什么 / 退化策略 / 提升目标。"""

    id: str
    name: str
    kind: str                     # compute | decide
    layer: str                    # 数据层 / 状态层 / 研判层 / 决策层
    module: str
    inputs: Sequence[str] = ()    # data_bus 资源键
    outputs: Sequence[str] = ()
    metrics: Sequence[Dict[str, Any]] = ()
    fallback: str = ""
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "layer": self.layer,
            "module": self.module,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "metrics": [dict(m) for m in self.metrics],
            "fallback": self.fallback,
            "notes": self.notes,
        }


# ----------------------------------------------------------------------
#  能力清单（唯一事实源）
# ----------------------------------------------------------------------
CAPABILITIES: Sequence[Capability] = (
    # ---------------- 计算能力 ----------------
    Capability(
        id="breadth_stats", name="全市场宽度统计", kind="compute", layer="数据层",
        module="market_coverage / market_scanner.build_cross_section",
        inputs=("coverage",), outputs=("cross_section", "breadth"),
        metrics=(
            {"name": "有效报价覆盖率", "target": "≥ 92%", "why": "样本不足时宽度代表性下降"},
            {"name": "涨跌停口径", "target": "主板 10% / 创业科创 20% / ST 5%", "why": "统一阈值避免误判"},
        ),
        fallback="全市场缓存未就绪 → 回退核心样本，并置 data_scope=core_sample",
    ),
    Capability(
        id="sentiment_score", name="六维情绪评分", kind="compute", layer="状态层",
        module="decision_snapshot.build_sentiment_snapshot",
        inputs=("packet",), outputs=("sentiment.v1",),
        metrics=(
            {"name": "有效维覆盖", "target": "≥ 85%", "why": "低于 70% 不出总分"},
            {"name": "量能维可用", "target": "20 个交易日成交额齐备", "why": "当前靠回填，历史不足即缺维"},
        ),
        fallback="缺失维度按剩余权重重归一；覆盖 < 70% 返回 null + message",
    ),
    Capability(
        id="sentiment_flow", name="情绪追踪（变化/分位/拐点）", kind="compute", layer="状态层",
        module="sentiment_tracker.build_flow",
        inputs=("sentiment",), outputs=("sentiment-flow.v1",),
        metrics=(
            {"name": "拐点 T+1 命中率", "target": "≥ 60%", "why": "拐点是可交易信号，必须可复盘"},
            {"name": "采样密度", "target": "≥ 20 点/交易日", "why": "斜率与 z 分位需要足够样本"},
            {"name": "驱动归因可解释", "target": "Top3 维度贡献占比 ≥ 70%", "why": "解释不了的变化不可信"},
        ),
        fallback="样本 < 5 点：斜率/分位置 null 并降置信，不编造方向",
    ),
    Capability(
        id="sector_rotation", name="板块轮动与关联", kind="compute", layer="数据层",
        module="market_scanner.build_rotation",
        inputs=("coverage", "scan"), outputs=("rotation[]", "sector_linkage[]"),
        metrics=(
            {"name": "板块覆盖", "target": "≥ 120 个行业/概念", "why": "覆盖不足会漏掉主線"},
            {"name": "动量窗口", "target": "1 / 5 / 20 日齐备", "why": "单窗口无法区分脉冲与趋势"},
        ),
        fallback="sector_history 不足 → signal=baseline，并在 payload 标注",
    ),
    Capability(
        id="money_flow", name="资金流向", kind="compute", layer="数据层",
        module="market_scanner.build_money_flow_proxy",
        inputs=("coverage",), outputs=("money_flow",),
        metrics=(
            {"name": "实测通道占比", "target": "≥ 50%（其余标注 proxy）", "why": "代理口径不能当实测用"},
            {"name": "频控退避", "target": "连续 2 次失败即退避 1800s", "why": "避免被封禁"},
        ),
        fallback="东财被频控 → 注意力代理，字段 mode=proxy 显式标注",
    ),
    Capability(
        id="global_linkage", name="全球联动与宏观", kind="compute", layer="数据层",
        module="market_coverage / global_linkage",
        inputs=("coverage",), outputs=("global", "macro"),
        metrics=(
            {"name": "指数覆盖", "target": "17 个主要指数 + 8 国代理", "why": "单市场视角会误判外部冲击"},
            {"name": "宏观时效", "target": "日频 ≤ 24h / 年度 ≤ 1 季", "why": "过期宏观会污染风险判断"},
        ),
        fallback="单源失败不影响其他源；缺项在 coverage 中标注",
    ),
    Capability(
        id="fundamental_valuation", name="基本面与估值分位", kind="compute", layer="数据层",
        module="fundamental / providers.akshare_fundamental",
        inputs=("coverage",), outputs=("fundamental", "valuation"),
        metrics=(
            {"name": "季度基本面覆盖", "target": "≥ 94%", "why": "分位需要横截面样本"},
            {"name": "分位最小样本", "target": "≥ 30", "why": "样本太少分位无意义"},
        ),
        fallback="报告期滞后 → 标注 is_period_stale，不给分位",
    ),
    Capability(
        id="factor_ic", name="因子 IC 与分层", kind="compute", layer="研判层",
        module="qlib_factor_engine",
        inputs=("coverage", "packet"), outputs=("factor_ic", "quantile_table"),
        metrics=(
            {"name": "有效截面", "target": "≥ 300 只/期", "why": "截面太小 IC 不稳定"},
            {"name": "IC 期数", "target": "≥ 20 期", "why": "少于 20 期不报显著性"},
        ),
        fallback="样本不足 → ok=false + reason，不输出伪 IC",
    ),

    # ---------------- 决策能力 ----------------
    Capability(
        id="regime_diagnosis", name="市场状态诊断", kind="decide", layer="研判层",
        module="market_survey_engine._diagnose_regime",
        inputs=("packet", "coverage"), outputs=("regime", "divergence"),
        metrics=(
            {"name": "状态切换识别", "target": "切换当日必须显式标注", "why": "状态漂移是最大回撤来源"},
            {"name": "结构-情绪背离检出", "target": "背离日 100% 出预警", "why": "背离往往先于转向"},
        ),
        fallback="行情不可得 → status=数据不足，confidence=0",
    ),
    Capability(
        id="survey_synthesis", name="调查结论生成", kind="decide", layer="决策层",
        module="market_survey_engine._ai_synthesis",
        inputs=("survey", "sentiment_flow"), outputs=("synthesis",),
        metrics=(
            {"name": "LLM 可用率", "target": "≥ 95%", "why": "不可用时必须规则兜底而非空结论"},
            {"name": "结论含证据", "target": "100% 引用真实口径", "why": "禁止虚构数据"},
        ),
        fallback="无 API Key / 失败 → 规则模板，ai_powered=false",
    ),
    Capability(
        id="expert_panel", name="六专家研判", kind="decide", layer="研判层",
        module="decision_experts.run_expert_panel",
        inputs=("packet", "sentiment", "scan"), outputs=("brain-panel.v3",),
        metrics=(
            {"name": "互评完成率", "target": "100%（两轮匿名）", "why": "未互评的共识不可信"},
            {"name": "结论可执行率", "target": "≥ 70% actionable", "why": "长期无结论等于没有系统"},
        ),
        fallback="数据质量不足 → stance=neutral、position_cap=0，显式说明",
    ),
    Capability(
        id="dissent_review", name="分歧与否决复核", kind="decide", layer="研判层",
        module="decision_experts_aggregate.aggregate_expert_opinions",
        inputs=("experts",), outputs=("consensus", "vetoes", "lifted_vetoes"),
        metrics=(
            {"name": "否决复核率", "target": "100% 给出撤销/维持理由", "why": "否决不能被静默吞掉"},
            {"name": "分歧阈值", "target": "> 0.55 时仓位上限 ≤ 20%", "why": "重大分歧下重仓是纪律问题"},
        ),
        fallback="无专家可用 → consensus=0、actionable=false",
    ),
    Capability(
        id="risk_budget", name="仓位与风险预算", kind="decide", layer="决策层",
        module="decision_experts / trading.risk",
        inputs=("experts", "sentiment_flow"), outputs=("position_cap",),
        metrics=(
            {"name": "上限约束", "target": "情绪降温 ×0.7 / 背离 ×0.8", "why": "风险预算必须随状态调整"},
            {"name": "硬顶", "target": "≤ 80%（无 veto 时）", "why": "单一系统不给满仓"},
        ),
        fallback="情绪不可用 → 沿用专家 min(cap)，并标注依据缺失",
    ),
    Capability(
        id="memo_compose", name="决策备忘录", kind="decide", layer="决策层",
        module="brain_capabilities.compose_memo",
        inputs=("survey", "sentiment_flow", "experts"), outputs=("brain-memo.v1",),
        metrics=(
            {"name": "要素完整度", "target": "立场/理由/动作/风险/失效条件 5 项齐备", "why": "缺一项就无法复盘"},
            {"name": "可追溯", "target": "trace_id 与输入快照一一对应", "why": "结论必须能回放"},
        ),
        fallback="任一输入缺失 → 立场降为 observe，并写明缺口",
    ),
    Capability(
        id="ledger_calibration", name="命中账本与校准", kind="decide", layer="决策层",
        module="decision_experts_ledger",
        inputs=("experts", "packet"), outputs=("calibration",),
        metrics=(
            {"name": "T+1 结算率", "target": "≥ 95%", "why": "不结算就无法校准"},
            {"name": "专家权重校准", "target": "按 20 日命中率滚动", "why": "固定权重会被市场风格惩罚"},
        ),
        fallback="无历史 → 输出空账本，不伪造命中率",
    ),
)


def capability_map() -> Dict[str, Capability]:
    return {cap.id: cap for cap in CAPABILITIES}


def capability_graph() -> Dict[str, Any]:
    """能力依赖图：compute → decide 的单向边 + 数据资源边。"""
    nodes = [cap.as_dict() for cap in CAPABILITIES]
    edges: List[Dict[str, str]] = []
    for cap in CAPABILITIES:
        for res in cap.inputs:
            edges.append({"from": f"data:{res}", "to": f"cap:{cap.id}", "kind": "data"})
    # 计算能力 → 决策能力的显式依赖（按输入资源的匹配推导，保持声明式）
    produced: Dict[str, str] = {}
    for cap in CAPABILITIES:
        if cap.kind == "compute":
            for out in cap.outputs:
                produced[out.split(".")[0]] = cap.id
    for cap in CAPABILITIES:
        if cap.kind != "decide":
            continue
        for res in cap.inputs:
            src = produced.get(res)
            if src:
                edges.append({"from": f"cap:{src}", "to": f"cap:{cap.id}", "kind": "derive"})
    return {
        "version": CAPABILITY_VERSION,
        "brain_version": BRAIN_VERSION,
        "count": len(nodes),
        "compute": sum(1 for c in CAPABILITIES if c.kind == "compute"),
        "decide": sum(1 for c in CAPABILITIES if c.kind == "decide"),
        "layers": sorted({cap.layer for cap in CAPABILITIES}),
        "nodes": nodes,
        "edges": edges,
        "legend": {
            "data": "数据资源 → 能力（输入）",
            "derive": "计算能力 → 决策能力（派生）",
        },
    }


def report(resources: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按数据可用性评估每项能力的当前状态。

    ``resources`` 形如 ``{"coverage": {"fresh": True}, "sentiment": {"fresh": False}}``；
    缺省视为未知（不判死），只有显式 ``fresh=False`` 或 ``missing`` 才降级。
    """
    resources = resources or {}
    rows: List[Dict[str, Any]] = []
    counts = {"ok": 0, "degraded": 0, "blocked": 0}
    for cap in CAPABILITIES:
        blockers: List[str] = []
        degraded: List[str] = []
        for res in cap.inputs:
            state = resources.get(res)
            if state is None:
                continue
            if state.get("missing"):
                blockers.append(f"{res} 缺失")
            elif state.get("fresh") is False:
                degraded.append(f"{res} 过期")
        if blockers:
            status = "blocked"
        elif degraded:
            status = "degraded"
        else:
            status = "ok"
        counts[status] += 1
        rows.append({
            "id": cap.id,
            "name": cap.name,
            "kind": cap.kind,
            "layer": cap.layer,
            "status": status,
            "blockers": blockers + degraded,
            "fallback": cap.fallback,
        })
    total = len(rows) or 1
    return {
        "version": CAPABILITY_VERSION,
        "brain_version": BRAIN_VERSION,
        "counts": counts,
        "available_ratio": round((counts["ok"] + counts["degraded"] * 0.5) / total, 3),
        "capabilities": rows,
        "resources": sorted({res for cap in CAPABILITIES for res in cap.inputs}),
    }


# ----------------------------------------------------------------------
#  决策备忘录（纯函数，便于回放与单测）
# ----------------------------------------------------------------------
_STANCE_LABEL = {"attack": "进攻", "balanced": "均衡", "defend": "防守", "observe": "观察"}


def _clamp_int(value: float, low: int, high: int) -> int:
    return int(max(low, min(high, value)))


def compose_memo(
    survey: Optional[Dict[str, Any]],
    flow: Optional[Dict[str, Any]],
    experts: Optional[Dict[str, Any]],
    now: Optional[str] = None,
    crosscheck: Optional[Dict[str, Any]] = None,
    routing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把「调查 + 情绪追踪 + 六专家」合成为一份可复盘的决策备忘录。

    合成规则（显式、可测）：
    * 立场：专家共识的 stance 为准；不可执行时降为 observe；
    * 仓位：共识 position_cap 为基准，情绪降温 ×0.7、结构-情绪背离 ×0.8，硬顶 80%；
    * 置信：共识置信 × 情绪置信修正 × 背离惩罚；
    * 失效条件：专家失效条件 + 情绪反转阈值；
    * 跨层对证：三层数据冲突时置信 ×0.85 并写入风险（冲突的结论不该满配）。
    """
    survey = survey or {}
    flow = flow or {}
    experts = experts or {}
    consensus = experts.get("consensus") or {}
    regime = flow.get("regime") or {}
    survey_regime = survey.get("regime") or {}
    divergence = survey.get("divergence") or {}

    gaps: List[str] = []
    if not survey.get("ok"):
        gaps.append("市场调查不可用")
    if not flow.get("ok"):
        gaps.append("情绪追踪不可用")
    if not consensus:
        gaps.append("专家共识不可用")

    base_stance = consensus.get("stance") or "neutral"
    actionable = bool(consensus.get("actionable")) and not gaps
    stance = base_stance if actionable else "observe"
    if stance == "neutral":
        stance = "balanced"
    if stance not in STANCES:
        stance = "observe"

    cap = float(consensus.get("position_cap") or 0)
    adjustments: List[Dict[str, Any]] = []
    if regime.get("key") in ("cooling", "cooling_from_hot", "weakening"):
        cap *= 0.7
        adjustments.append({"factor": 0.7, "reason": f"情绪{regime.get('label')}，压缩风险预算"})
    if divergence.get("detected"):
        cap *= 0.8
        adjustments.append({"factor": 0.8, "reason": "结构-情绪背离，降低方向性暴露"})
    if consensus.get("veto"):
        cap = 0
        adjustments.append({"factor": 0.0, "reason": "存在风控否决"})
    if stance == "observe":
        cap = 0
    # 分流闸门：只收紧、不放大。环境不允许进攻时，连方向都要降级——
    # 一个 10% 仓位的「进攻」结论在语义上是自相矛盾的。
    routing_block: Dict[str, Any] = {"available": bool(routing)}
    routing_risk_text: Optional[str] = None
    if routing:
        route_cap = (routing.get("action") or {}).get("cap_max")
        if isinstance(route_cap, int):
            if route_cap < cap:
                adjustments.append({"factor": f"routing:{route_cap}",
                                    "reason": f"分流「{routing.get('label')}」限制仓位上限 {route_cap}%"})
                cap = route_cap
        route_stance = (routing.get("action") or {}).get("stance")
        if route_stance == "defend" and stance == "attack":
            adjustments.append({"factor": "routing:stance",
                                "reason": "分流判定防御环境，进攻结论降级为中性"})
            stance = "neutral"
        if routing.get("degraded"):
            # risks 列表在后面才初始化，这里先记文本，统一在构建 risks 时落库
            routing_risk_text = "分流降级：" + "；".join(
                routing.get("degraded_reasons") or ["指标不足"])
        routing_block = {
            "available": True,
            "route_id": routing.get("route_id"),
            "label": routing.get("label"),
            "stance": route_stance,
            "cap_max": route_cap,
            "bias": routing.get("bias"),
            "composite": routing.get("composite"),
            "coverage": routing.get("coverage"),
            "degraded": bool(routing.get("degraded")),
            "degraded_reasons": routing.get("degraded_reasons") or [],
            "note": (routing.get("action") or {}).get("note"),
        }
    cap = _clamp_int(cap, 0, 80)

    conf = float(consensus.get("confidence") or 0)
    flow_conf = flow.get("confidence")
    if isinstance(flow_conf, (int, float)):
        conf *= 0.5 + 0.5 * float(flow_conf)
    if divergence.get("detected"):
        conf *= 0.85
    layer_conflicts = (crosscheck or {}).get("conflicts") or []
    if layer_conflicts:
        conf *= 0.85
    conf = round(max(0.0, min(1.0, conf)), 3)

    thesis: List[Dict[str, Any]] = []
    if survey_regime:
        thesis.append({
            "source": "市场结构",
            "text": f"{survey_regime.get('status')}：{survey_regime.get('main_driver') or '--'}",
        })
    if regime:
        thesis.append({
            "source": "情绪追踪",
            "text": f"{flow.get('level')} 分（{(flow.get('band') or {}).get('label')}），"
                    f"{regime.get('label')}；近端 {flow.get('delta_short')}，"
                    f"斜率 {flow.get('slope_per_hour')}",
        })
    if consensus:
        thesis.append({
            "source": "六专家",
            "text": consensus.get("label") or "--",
            "detail": experts.get("summary"),
        })

    actions: List[Dict[str, Any]] = []
    if stance == "attack":
        actions = [
            {"target": "主线板块", "action": "顺势加仓", "horizon": "1-3 日",
             "reason": "结构向上且情绪升温"},
            {"target": "持仓", "action": f"仓位上限 {cap}%", "horizon": "日内", "reason": "风险预算"},
        ]
    elif stance == "defend":
        actions = [
            {"target": "高 β 持仓", "action": "减仓 / 对冲", "horizon": "1-3 日",
             "reason": "结构偏弱或存在否决"},
            {"target": "现金", "action": f"保留至多 {100 - cap}%", "horizon": "日内", "reason": "防守"},
        ]
    elif stance == "balanced":
        actions = [
            {"target": "组合", "action": f"维持 {cap}% 以内，不追高", "horizon": "1-3 日",
             "reason": "方向不明，等确认"},
        ]
    else:
        actions = [
            {"target": "全部", "action": "只观察，不开新仓", "horizon": "直至数据恢复",
             "reason": "；".join(gaps) or "共识不可执行"},
        ]

    risks: List[Dict[str, Any]] = []
    if routing_risk_text:
        risks.append({"code": "routing_degraded", "text": routing_risk_text})
    for flag in (flow.get("flags") or [])[:3]:
        risks.append({"code": flag.get("code"), "text": flag.get("text")})
    for alert in (survey.get("alerts") or [])[:3]:
        if alert.get("level") == "high":
            risks.append({"code": alert.get("code") or "alert", "text": alert.get("desc") or alert.get("name")})
    for veto in (consensus.get("veto_reasons") or [])[:2]:
        risks.append({"code": "veto", "text": f"{veto.get('expert_id')}：{veto.get('reason')}"})
    for conflict in layer_conflicts[:2]:
        risks.append({"code": "layer_conflict",
                      "text": f"三层对证冲突：{conflict.get('name')} — {conflict.get('note')}"})
    if crosscheck and crosscheck.get("comparable_ratio", 1) < 0.5:
        risks.append({"code": "crosscheck_thin",
                      "text": f"对证可比率仅 {round(float(crosscheck.get('comparable_ratio') or 0) * 100)}%，结论依据不足"})

    invalidation: List[str] = []
    for item in (experts.get("invalidation") or [])[:3]:
        if item:
            invalidation.append(str(item))
    level = flow.get("level")
    if isinstance(level, (int, float)):
        invalidation.append(f"情绪总分跌破 {max(0, int(level) - 8)}（当前 {level}）")
    if regime.get("key"):
        invalidation.append(f"情绪状态由「{regime.get('label')}」切换")

    return {
        "ok": not gaps and bool(consensus),
        "version": "brain-memo.v1",
        "trace_id": experts.get("trace_id") or flow.get("trace_id") or survey.get("trace_id"),
        "as_of": experts.get("as_of") or flow.get("as_of") or survey.get("as_of"),
        "generated_at": now,
        "stance": stance,
        "stance_label": _STANCE_LABEL.get(stance, stance),
        "position_cap": cap,
        "confidence": conf,
        "thesis": thesis,
        "actions": actions,
        "risks": risks,
        "invalidation": invalidation,
        "adjustments": adjustments,
        "gaps": gaps,
        "inputs": {
            "survey": {"available": bool(survey.get("ok")),
                       "regime": survey_regime.get("status"),
                       "divergence": bool(divergence.get("detected"))},
            "sentiment_flow": {"available": bool(flow.get("ok")),
                               "level": level,
                               "regime": regime.get("key")},
            "experts": {"available": bool(consensus),
                        "consensus": consensus.get("score"),
                        "disagreement": consensus.get("disagreement"),
                        "veto": bool(consensus.get("veto"))},
            "crosscheck": {
                "available": bool(crosscheck),
                "ok": (crosscheck or {}).get("ok"),
                "score": (crosscheck or {}).get("score"),
                "conflicts": len(layer_conflicts),
                "comparable_ratio": (crosscheck or {}).get("comparable_ratio"),
            },
        },
        "routing": routing_block,
        "evidence": [t.get("text") for t in thesis if t.get("text")],
        "summary": (
            f"{_STANCE_LABEL.get(stance, stance)}｜仓位上限 {cap}%｜置信 {conf:.2f}"
            + ("｜" + "；".join(gaps) if gaps else "")
        ),
    }


__all__ = [
    "BRAIN_VERSION",
    "CAPABILITY_VERSION",
    "STANCES",
    "Capability",
    "CAPABILITIES",
    "capability_map",
    "capability_graph",
    "report",
    "compose_memo",
]
