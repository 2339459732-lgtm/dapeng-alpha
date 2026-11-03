"""成交成本模型（A 股）—— 滑点 / 佣金 / 印花税 / 过户费

纯函数、无副作用、无 IO，便于单测与手算对齐。货币单位统一为「元」。

规则（与主理人验收口径一致）：
- 买入成交价 = 现价 × (1 + slippage_bps / 10000)
- 卖出成交价 = 现价 × (1 − slippage_bps / 10000)
- 佣金 = max(成交额 × commission_rate, commission_min)
- 印花税 = 成交额 × stamp_tax_rate（**仅卖出**收取）
- 过户费 = 成交额 × transfer_fee_rate（沪深均收）
- 买入总成本 = 成交额 + 佣金 + 过户费        （资金流出）
- 卖出净收入 = 成交额 − 佣金 − 印花税 − 过户费（资金流入）

成交价保留 3 位小数（低价股/高滑点下精度足够，避免四舍五入到分导致
"手算对不上"）；金额字段统一保留 2 位小数（分）。
"""

from __future__ import annotations

from typing import Any, Dict

from .models import RiskConfig

_EPS = 1e-9


def _round2(x: float) -> float:
    """四舍五入到分（2 位小数），消除二进制浮点残差"""
    return round(float(x) + _EPS, 2)


def _round3(x: float) -> float:
    """成交价保留 3 位小数"""
    return round(float(x) + _EPS, 3)


def _commission(amount: float, cfg: RiskConfig) -> float:
    """佣金：按费率计算，但不低于最低佣金（commission_min）"""
    fee = amount * float(cfg.commission_rate)
    fee = _round2(fee)
    if float(cfg.commission_min) > 0 and fee < float(cfg.commission_min):
        return _round2(float(cfg.commission_min))
    return fee


def compute_buy(price: float, qty: int, cfg: RiskConfig) -> Dict[str, Any]:
    """买入成交成本明细。price = 参考现价（未含滑点）。"""
    ref = float(price)
    fill = _round3(ref * (1.0 + float(cfg.slippage_bps) / 10000.0))
    amount = _round2(fill * int(qty))
    commission = _commission(amount, cfg)
    transfer_fee = _round2(amount * float(cfg.transfer_fee_rate))
    stamp_tax = 0.0
    total_cost = _round2(amount + commission + transfer_fee)
    slippage_cost = _round2(abs(fill - ref) * int(qty))
    return {
        "side": "buy",
        "ref_price": ref,
        "fill_price": fill,
        "qty": int(qty),
        "amount": amount,
        "commission": commission,
        "stamp_tax": stamp_tax,
        "transfer_fee": transfer_fee,
        "slippage_cost": slippage_cost,
        "total_cost": total_cost,
    }


def compute_sell(price: float, qty: int, cfg: RiskConfig) -> Dict[str, Any]:
    """卖出成交成本明细。total_cost 为**净收入**（已扣全部费用）。"""
    ref = float(price)
    fill = _round3(ref * (1.0 - float(cfg.slippage_bps) / 10000.0))
    amount = _round2(fill * int(qty))
    commission = _commission(amount, cfg)
    stamp_tax = _round2(amount * float(cfg.stamp_tax_rate))
    transfer_fee = _round2(amount * float(cfg.transfer_fee_rate))
    net = _round2(amount - commission - stamp_tax - transfer_fee)
    slippage_cost = _round2(abs(fill - ref) * int(qty))
    return {
        "side": "sell",
        "ref_price": ref,
        "fill_price": fill,
        "qty": int(qty),
        "amount": amount,
        "commission": commission,
        "stamp_tax": stamp_tax,
        "transfer_fee": transfer_fee,
        "slippage_cost": slippage_cost,
        "total_cost": net,
    }


def unit_cost(row: Dict[str, Any]) -> float:
    """订单行 → 每股全含成本（买入）/每股净收入（卖出）。

    向后兼容：历史订单无 total_cost 字段（NULL）时退化为 price（不含费用），
    使旧数据仍可参与复盘配对，不会丢单。
    """
    qty = int(row.get("qty") or 0)
    if qty <= 0:
        return 0.0
    tc = row.get("total_cost")
    if tc is None:
        return float(row.get("price") or 0.0)
    return float(tc) / qty
