"""自动交易核心包 - 全部数据模型（dataclass + 枚举）

定义跨层传递的全部数据结构与状态机枚举，无任何业务逻辑。
对应架构蓝图 §1.2 / §4 / §6.2。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


def now_local_iso() -> str:
    """本地时间 ISO 8601 字符串（YYYY-MM-DDTHH:MM:SS），落库统一格式"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def new_id(prefix: str) -> str:
    """生成带前缀的短 uuid：sig_ / ord_ + uuid4 hex(12)"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ============================================================
# 枚举
# ============================================================
class GateState(str, Enum):
    """门禁状态机（每策略独立），§6.2"""
    DRAFT = "draft"
    BACKTESTED = "backtested"
    PAPER_VALIDATING = "paper_validating"
    PAPER_PASSED = "paper_passed"
    LIVE_ENABLED = "live_enabled"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    """订单状态机。

    统计口径：复盘/迭代/北极星指标**只认 `filled`**（见 review.py `_round_trips`、
    iterator.py `round_trips_with_entry` 的 `status=='filled'` 过滤）。`partial` /
    `partial_canceled` / `pending` 仅为真实通道与条件单预留，**不影响任何统计**。
    """
    PENDING = "pending"              # 已提交未成交（条件单待触发 / 真通道挂单）
    FILLED = "filled"                # 全部成交（唯一计入统计的状态）
    PARTIAL = "partial"              # 部分成交
    PARTIAL_CANCELED = "partial_canceled"  # 部分成交后撤余（保留已成交部分）
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"          # 未成交撤单


class PushStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    SUPPRESSED_COOLDOWN = "suppressed_cooldown"
    RATE_LIMITED = "rate_limited"
    DIGEST = "digest"
    FAILED = "failed"
    DISABLED = "disabled"


# 信号类型（signals.signal_type 合法值，§4.1）
SIGNAL_TYPES = {
    "score", "momentum", "breakout", "macd_gold", "macd_dead", "chg_spike",
    "ma_cross_gold", "ma_cross_dead",
    "strategy_buy", "strategy_sell", "atr_stop",
    # 各策略的止损/退出信号（均为卖出方向，须能触发自动平仓）
    "nb_stop", "ma_fixed_stop", "macd_stop", "rsi_stop", "vol_stop",
    # 风控级止盈（与止损对称，由持仓守卫在扫描轮触发，均为卖出方向）
    "take_profit", "trailing_stop",
    # 演练强制平仓（无策略出场信号时，制造闭合样本用；exit_reason=drill_forced）
    "drill_exit",
}

# 买入类信号（可触发自动下单的方向判定）
BUY_SIGNAL_TYPES = {"strategy_buy"}
# 卖出类信号（策略止盈/止损/退出 + 风控级止盈，均触发自动平仓）
SELL_SIGNAL_TYPES = {
    "strategy_sell", "atr_stop",
    "nb_stop", "ma_fixed_stop", "macd_stop", "rsi_stop", "vol_stop",
    "take_profit", "trailing_stop", "drill_exit",
}

# 卖出信号的出场归因（orders.exit_reason）——复盘据此区分平仓原因，避免混淆
EXIT_REASON_BY_SIGNAL = {
    "take_profit": "take_profit",       # 风控级固定止盈
    "trailing_stop": "trailing_stop",   # 风控级移动止盈
    "atr_stop": "trailing_stop",        # 策略 ATR 移动止损
    "nb_stop": "fixed_stop",            # N日突破 固定止损
    "ma_fixed_stop": "fixed_stop",      # 均线策略 固定止损
    "macd_stop": "fixed_stop",          # MACD 固定止损
    "rsi_stop": "fixed_stop",           # RSI 固定止损
    "vol_stop": "fixed_stop",           # 放量突破 固定止损
    "strategy_sell": "signal",          # 策略信号出场（金叉死叉/破位/超买等）
    "drill_exit": "drill_forced",       # 演练强制平仓（非策略出场，复盘须区分）
}


def exit_reason_of(signal_type: str) -> Optional[str]:
    """信号类型 → 出场归因；买入类返回 None"""
    return EXIT_REASON_BY_SIGNAL.get(signal_type)


# ============================================================
# 初始本金 —— 单一真相源（禁止在别处再写死 1_000_000）
# ============================================================
# 全系统（paper 虚拟盘 / 回测 / iterator 最大回撤分母）初始本金只在此处定义一次。
# 运行时以虚拟盘账户 `paper_account.initial_cash` 为准；本常量仅作缺省回退。
DEFAULT_INITIAL_CASH: float = 1_000_000.0

PANEL_SOURCE = "panel"  # Track A 冷却命名空间


def strategy_source(strategy_id: str) -> str:
    """Track B 信号来源命名空间：strategy:<strategy_id>"""
    return f"strategy:{strategy_id}"


def parse_strategy_source(source: str) -> Optional[str]:
    """从 source 解析 strategy_id；非策略源返回 None"""
    if source.startswith("strategy:"):
        return source.split(":", 1)[1]
    return None


# ============================================================
# 信号
# ============================================================
@dataclass
class Signal:
    """统一信号协议（Track A / Track B 同一结构），§3 ③"""
    code: str
    name: str
    signal_type: str
    source: str                      # panel / strategy:<id>
    price: float = 0.0
    chg_pct: float = 0.0
    score: Optional[int] = None
    detail: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("sig"))
    ts: str = field(default_factory=now_local_iso)
    pushed: bool = False
    push_status: str = PushStatus.PENDING.value

    def to_row(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "code": self.code,
            "name": self.name,
            "signal_type": self.signal_type,
            "source": self.source,
            "price": self.price,
            "chg_pct": self.chg_pct,
            "score": self.score,
            "detail": self.detail,
            "pushed": self.pushed,
            "push_status": self.push_status,
        }


# ============================================================
# 订单
# ============================================================
@dataclass
class OrderRequest:
    """下单请求（risk.check_order 的输入）

    qty=0 表示由风控按金额上限自动换算；side=sell 且 qty=0 表示全部平仓。
    allow_t0=True 仅用于虚拟盘演练（paper_drill）强制平仓：绕过 T+1 可卖数量限制，
    并在审计中留痕；正常下单路径必须为 False，保持 T+1 真实性。
    """
    code: str
    name: str
    side: str                        # buy / sell
    price: float                     # 参考价（现价）
    qty: int = 0
    signal_id: Optional[str] = None
    strategy_id: Optional[str] = None
    gateway: str = "paper"           # paper / live
    allow_t0: bool = False           # 演练强制平仓绕过 T+1（默认 False）
    is_drill: bool = False           # 演练造数标记（由 drill 上下文自动置位）
    exit_reason: Optional[str] = None  # 卖出出场归因（take_profit/trailing_stop/fixed_stop/signal/drill_forced）
    sizing_weight: float = 1.0       # 仓位权重（决策闸 confidence×波动率倒数，0.1~1.0）
    decision: Optional[Dict[str, Any]] = None  # 决策闸四维理由（落 orders.decision_json）
    source: str = "signal"           # 订单来源：signal / manual / conditional / drill


@dataclass
class OrderResult:
    """下单结果（risk/gateway 的输出，orders 表落库依据）

    成本字段单位统一为「元」（人民币）：
    - ref_price     成交前行情参考价
    - price         含滑点后的成交价 = ref_price × (1 ± slippage_bps/10000)
    - amount        成交额 = price × qty
    - commission    佣金（≥ commission_min）
    - stamp_tax     印花税（仅卖出收取）
    - transfer_fee  过户费（买卖均收）
    - slippage_cost 滑点成本 = |price − ref_price| × qty
    - total_cost    买入=资金流出合计（amount+commission+transfer_fee）；
                    卖出=资金净流入（amount−commission−stamp_tax−transfer_fee）
    """
    ok: bool
    status: str                      # filled / rejected / failed / cancelled
    code: str = ""
    name: str = ""
    side: str = ""
    qty: int = 0
    price: float = 0.0
    amount: float = 0.0
    gateway: str = "paper"
    fail_reason: Optional[str] = None
    signal_id: Optional[str] = None
    strategy_id: Optional[str] = None
    order_id: str = field(default_factory=lambda: new_id("ord"))
    checks: Dict[str, Any] = field(default_factory=dict)  # 风控 9 关卡判定明细
    # ---- 成交成本明细（元）----
    ref_price: float = 0.0
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0
    slippage_cost: float = 0.0
    total_cost: float = 0.0
    # ---- 溯源与归因 ----
    is_drill: bool = False            # 演练造数（非真实信号交易），用于统计排除
    exit_reason: Optional[str] = None  # 卖出出场归因（买入为 None）
    decision_json: Optional[Dict[str, Any]] = None  # 决策闸四维理由（可追溯）
    source: str = "signal"            # 订单来源：signal / manual / conditional / drill
    req_qty: int = 0                  # 用户请求数量（换算前；被拦下也不丢，供复盘回放）
    req_amount: float = 0.0           # 请求名义金额 = req_qty × 参考价

    def to_row(self) -> Dict[str, Any]:
        return {
            "id": self.order_id,
            "ts": now_local_iso(),
            "code": self.code,
            "name": self.name,
            "side": self.side,
            "qty": self.qty,
            "price": self.price,
            "amount": self.amount,
            "gateway": self.gateway,
            "status": self.status,
            "fail_reason": self.fail_reason,
            "signal_id": self.signal_id,
            "strategy_id": self.strategy_id,
            "ref_price": self.ref_price,
            "commission": self.commission,
            "stamp_tax": self.stamp_tax,
            "transfer_fee": self.transfer_fee,
            "slippage_cost": self.slippage_cost,
            "total_cost": self.total_cost,
            "is_drill": 1 if self.is_drill else 0,
            "exit_reason": self.exit_reason,
            "decision_json": self.decision_json,
            "source": self.source,
            "req_qty": int(self.req_qty or 0),
            "req_amount": round(float(self.req_amount or 0.0), 2),
        }


# ============================================================
# 持仓
# ============================================================
@dataclass
class Position:
    """模拟持仓（positions 表）

    - buy_date     最近一次买入的本地日期 YYYY-MM-DD（T+1 可卖数量判定）
    - sellable_qty 当日可卖数量（T+1：当日买入的部分不可卖）
    - peak_price   持仓期间最高价（移动止盈 trailing_stop_pct 回撤基准）
    - strategy_id  建仓策略 id（风控级止盈/止损触发时的信号来源）
    """
    code: str
    name: str
    qty: int
    avg_cost: float
    last_price: float = 0.0
    gateway: str = "paper"
    updated_at: str = field(default_factory=now_local_iso)
    strategy_id: Optional[str] = None
    buy_date: str = ""
    sellable_qty: int = 0
    peak_price: float = 0.0

    @property
    def pnl_pct(self) -> float:
        """浮动盈亏 %（基于 last_price）"""
        if self.avg_cost <= 0 or self.last_price <= 0:
            return 0.0
        return round((self.last_price - self.avg_cost) / self.avg_cost * 100, 2)


# ============================================================
# 策略实例规格（config KV "strategies" 元素）
# ============================================================
@dataclass
class StrategySpec:
    """策略参数与门禁状态机，§4.4 strategies key"""
    strategy_id: str
    kind: str
    params: Dict[str, Any] = field(default_factory=dict)
    gate_state: str = GateState.DRAFT.value
    last_backtest: Optional[Dict[str, Any]] = None
    paper_days_done: int = 0
    paper_days_required: int = 5
    enabled_codes: List[str] = field(default_factory=list)
    live_enabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "kind": self.kind,
            "params": self.params,
            "gate_state": self.gate_state,
            "last_backtest": self.last_backtest,
            "paper_days_done": self.paper_days_done,
            "paper_days_required": self.paper_days_required,
            "enabled_codes": self.enabled_codes,
            "live_enabled": self.live_enabled,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StrategySpec":
        return cls(
            strategy_id=d.get("strategy_id", ""),
            kind=d.get("kind", d.get("strategy_id", "")),
            params=dict(d.get("params") or {}),
            gate_state=d.get("gate_state", GateState.DRAFT.value),
            last_backtest=d.get("last_backtest"),
            paper_days_done=int(d.get("paper_days_done", 0)),
            paper_days_required=int(d.get("paper_days_required", 5)),
            enabled_codes=list(d.get("enabled_codes") or []),
            live_enabled=bool(d.get("live_enabled", False)),
        )


# ============================================================
# 自动化配置（config KV "automation"）
# ============================================================
@dataclass
class AutomationConfig:
    """AutomationConfig 全量，§5.1"""
    switches: Dict[str, bool] = field(default_factory=lambda: {
        "notify": True, "paper": False, "live": False,
    })
    scope: Dict[str, Any] = field(default_factory=lambda: {
        "mode": "pool", "watchlist": [],
    })
    trigger: Dict[str, Any] = field(default_factory=lambda: {
        "score_threshold": 80,
        "signal_types": ["momentum", "breakout", "macd_gold", "macd_dead"],
        "chg_pct_threshold": 3.0,
    })
    cooldown_minutes: int = 30
    quiet: Dict[str, bool] = field(default_factory=lambda: {
        "trading_only": True, "premarket_digest": False,
    })
    email: Dict[str, Any] = field(default_factory=lambda: {
        "recipient": "", "per_stock_per_day": 5, "global_per_hour": 20,
    })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "switches": self.switches,
            "scope": self.scope,
            "trigger": self.trigger,
            "cooldown_minutes": self.cooldown_minutes,
            "quiet": self.quiet,
            "email": self.email,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "AutomationConfig":
        cfg = cls()
        if not d:
            return cfg
        for key in ("switches", "scope", "trigger", "quiet", "email"):
            if isinstance(d.get(key), dict):
                getattr(cfg, key).update(d[key])
        if d.get("cooldown_minutes") is not None:
            cfg.cooldown_minutes = int(d["cooldown_minutes"])
        # 实盘开关只能走 live-switch 专用端点，此处强制不回读
        cfg.switches["live"] = False
        return cfg


# ============================================================
# 风控配置与运行态（config KV "risk" / "risk_state"）
# ============================================================
@dataclass
class RiskConfig:
    """RiskConfig，§4.4 risk key / §6.1 默认值

    成交成本模型（单位：元；费率比例）：
    - slippage_bps       滑点（基点，1bp=0.01%）；买 +、卖 −
    - commission_rate    佣金率（万2.5 = 0.00025）
    - commission_min     单笔最低佣金（元）
    - stamp_tax_rate     印花税率（仅卖出收取）
    - transfer_fee_rate  过户费率（沪深均收）
    止盈（与止损对称，**默认关闭** 0=不启用；须显式开启，避免对既有策略静默注入
    原本不存在的出场规则、破坏策略历史可比性与复盘归因）：
    - take_profit_pct    固定止盈：浮盈 ≥ 该比例触发卖出
    - trailing_stop_pct  移动止盈：自持仓期间最高价回撤 ≥ 该比例触发卖出
    真实约束校验开关（数据不可靠时自动降级 + warning）：
    - check_price_limit  涨跌停校验（涨停拒买 / 跌停拒卖）
    - check_suspension   停牌校验（无量无额即停牌）
    """
    max_amount_per_order: float = 10000.0
    max_amount_per_day: float = 30000.0
    max_positions: int = 5
    whitelist: List[str] = field(default_factory=list)  # 空 = 全部只提醒（最保守）
    breaker_fail_threshold: int = 3
    daily_loss_limit_pct: float = 5.0
    live_master_switch: bool = False  # 持久化默认 false，进程重启不漂移
    # ---- 成交成本模型 ----
    slippage_bps: float = 5.0
    commission_rate: float = 0.00025
    commission_min: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    # ---- 止盈（0 = 关闭；默认关闭，须显式开启）----
    take_profit_pct: float = 0.0
    trailing_stop_pct: float = 0.0
    # ---- 真实约束校验开关 ----
    check_price_limit: bool = True
    check_suspension: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_amount_per_order": self.max_amount_per_order,
            "max_amount_per_day": self.max_amount_per_day,
            "max_positions": self.max_positions,
            "whitelist": self.whitelist,
            "breaker_fail_threshold": self.breaker_fail_threshold,
            "daily_loss_limit_pct": self.daily_loss_limit_pct,
            "live_master_switch": self.live_master_switch,
            "slippage_bps": self.slippage_bps,
            "commission_rate": self.commission_rate,
            "commission_min": self.commission_min,
            "stamp_tax_rate": self.stamp_tax_rate,
            "transfer_fee_rate": self.transfer_fee_rate,
            "take_profit_pct": self.take_profit_pct,
            "trailing_stop_pct": self.trailing_stop_pct,
            "check_price_limit": self.check_price_limit,
            "check_suspension": self.check_suspension,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "RiskConfig":
        cfg = cls()
        if not d:
            return cfg
        for key in ("max_amount_per_order", "max_amount_per_day", "daily_loss_limit_pct",
                    "slippage_bps", "commission_rate", "commission_min",
                    "stamp_tax_rate", "transfer_fee_rate",
                    "take_profit_pct", "trailing_stop_pct"):
            if d.get(key) is not None:
                setattr(cfg, key, float(d[key]))
        for key in ("max_positions", "breaker_fail_threshold"):
            if d.get(key) is not None:
                setattr(cfg, key, int(d[key]))
        if d.get("whitelist") is not None:
            cfg.whitelist = list(d["whitelist"])
        if d.get("live_master_switch") is not None:
            cfg.live_master_switch = bool(d["live_master_switch"])
        for key in ("check_price_limit", "check_suspension"):
            if d.get(key) is not None:
                setattr(cfg, key, bool(d[key]))
        return cfg


@dataclass
class RiskState:
    """运行态计数器（config KV "risk_state"），重启可恢复"""
    consecutive_failures: int = 0
    day_date: str = ""               # 本地日切标记 YYYY-MM-DD
    day_amount: float = 0.0          # 当日已成交金额
    day_pnl_pct: float = 0.0         # 当日浮动盈亏 %（收盘扫描更新）
    day_start_equity: float = 0.0    # 日初模拟账户权益（日亏熔断基准）
    breaker_tripped: bool = False
    breaker_tripped_at: Optional[str] = None
    smtp_alert: Optional[str] = None  # 邮件通道异常标记（状态灯红色告警）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "consecutive_failures": self.consecutive_failures,
            "day_date": self.day_date,
            "day_amount": self.day_amount,
            "day_pnl_pct": self.day_pnl_pct,
            "day_start_equity": self.day_start_equity,
            "breaker_tripped": self.breaker_tripped,
            "breaker_tripped_at": self.breaker_tripped_at,
            "smtp_alert": self.smtp_alert,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "RiskState":
        st = cls()
        if not d:
            return st
        st.consecutive_failures = int(d.get("consecutive_failures", 0))
        st.day_date = d.get("day_date", "")
        st.day_amount = float(d.get("day_amount", 0.0))
        st.day_pnl_pct = float(d.get("day_pnl_pct", 0.0))
        st.day_start_equity = float(d.get("day_start_equity", 0.0))
        st.breaker_tripped = bool(d.get("breaker_tripped", False))
        st.breaker_tripped_at = d.get("breaker_tripped_at")
        st.smtp_alert = d.get("smtp_alert")
        return st

    def rollover_if_new_day(self, today: str) -> None:
        """本地日切：新的一天清零当日计数"""
        if self.day_date != today:
            self.day_date = today
            self.day_amount = 0.0
            self.day_pnl_pct = 0.0
