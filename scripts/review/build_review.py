#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""打板复盘引擎（board-review.v1）：成交明细 → 逐笔回合 → 日报 / 台账 / 看板。

三步：
1. **回合匹配**：按标的做 FIFO，把买入批次与卖出配对，算出开平价、持有时间、净盈亏；
2. **执行判定**：有计划的按计划比对（止损/止盈），没计划的按结果归类并标注「未设计划」；
3. **风格统计**：入场时段、板型、单笔金额、加减仓节奏、离场原因、胜率盈亏比。

输出三种形态（同一份数据，口径一致）：
    reports/daily/打板复盘_YYYY-MM-DD.md     逐日复盘
    reports/打板复盘_总览.md                  跨日汇总与风格画像
    reports/打板复盘台账_<起止>.xlsx          可筛选台账（明细/回合/日汇总/风格）
    reports/打板复盘_<起止>.html              可视化看板（自包含，无外链）

用法：
    python scripts/review/build_review.py --trades data/review/trades_normalized.csv
    python scripts/review/build_review.py --trades ... --demo     # 示例数据（报告会打标）
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORT_DIR = os.path.join(ROOT, "reports")
DEMO_BANNER = "示例数据 · 仅用于验证解析与报表管线，指标不代表任何真实交易者"


# ---------------------------------------------------------------------------
# 1. 回合匹配（FIFO）
# ---------------------------------------------------------------------------
@dataclass
class Lot:
    date: str
    time: str
    price: float
    qty: int
    fee: float           # 该批次买入费用（按数量摊到剩余股数）
    board_type: str
    plan_stop: Optional[float]
    plan_target: Optional[float]
    remaining: int = 0


@dataclass
class RoundTrip:
    code: str
    name: str
    entry_date: str
    entry_time: str
    entry_price: float
    exit_date: str
    exit_time: str
    exit_price: float
    qty: int
    buy_amount: float
    sell_amount: float
    fee: float
    board_type: str
    plan_stop: Optional[float]
    plan_target: Optional[float]
    status: str = "closed"          # closed / open
    tags: List[str] = field(default_factory=list)

    @property
    def gross(self) -> float:
        return round((self.exit_price - self.entry_price) * self.qty, 2)

    @property
    def net(self) -> float:
        return round(self.gross - self.fee, 2) if self.status == "closed" else 0.0

    @property
    def ret_pct(self) -> float:
        cost = self.entry_price * self.qty
        return round(self.net / cost * 100, 2) if cost else 0.0

    @property
    def hold_minutes(self) -> Optional[int]:
        if self.status != "closed" or not self.entry_time or not self.exit_time:
            return None
        try:
            start = datetime.fromisoformat(f"{self.entry_date}T{self.entry_time}")
            end = datetime.fromisoformat(f"{self.exit_date}T{self.exit_time}")
            return max(0, int((end - start).total_seconds() // 60))
        except ValueError:
            return None

    @property
    def hold_label(self) -> str:
        if self.status != "closed":
            return "持仓中"
        if self.entry_date != self.exit_date:
            days = (datetime.fromisoformat(self.exit_date) - datetime.fromisoformat(self.entry_date)).days
            return f"隔夜 {days} 天"
        minutes = self.hold_minutes
        return f"日内 {minutes} 分钟" if minutes is not None else "日内"


def classify_exit(trip: RoundTrip) -> Tuple[str, str]:
    """返回 (离场类型, 判定依据)。优先比计划，没有计划才按结果归类。"""
    if trip.status != "closed":
        return "未平仓", "—"
    has_plan = bool(trip.plan_stop or trip.plan_target)
    if trip.plan_stop and trip.exit_price <= trip.plan_stop:
        return "止损（按计划触发）", f"卖出 {trip.exit_price} ≤ 计划止损 {trip.plan_stop}"
    if trip.plan_target and trip.exit_price >= trip.plan_target:
        return "止盈（按计划触发）", f"卖出 {trip.exit_price} ≥ 计划止盈 {trip.plan_target}"
    if has_plan:
        # 有计划但从区间内离场：这是主动决策，不是"没设计划"。
        # 以前这里会掉到下面的 -5%/+7% 规则，把有计划的单子误标成未设计划。
        return "区间离场（计划未触发）", f"卖出 {trip.exit_price} 落在止损 {trip.plan_stop} 与止盈 {trip.plan_target} 之间"
    if trip.ret_pct <= -5:
        return "被动止损（未设计划）", f"收益 {trip.ret_pct}% ≤ -5%"
    if trip.ret_pct >= 7:
        return "止盈（未设计划）", f"收益 {trip.ret_pct}% ≥ 7%"
    return "其他离场", f"收益 {trip.ret_pct}%"


def match_round_trips(trades: pd.DataFrame) -> Tuple[List[RoundTrip], List[RoundTrip]]:
    """FIFO 配对。返回 (已平仓回合, 未平仓回合)。"""
    trips: List[RoundTrip] = []
    open_lots: Dict[str, List[Lot]] = {}
    ordered = trades.sort_values(["trade_date", "trade_time"]).to_dict("records")
    for row in ordered:
        code = row["code"]
        if row["side"] == "buy":
            lot = Lot(date=row["trade_date"], time=row.get("trade_time") or "",
                      price=float(row["price"]), qty=int(row["qty"]),
                      fee=float(row.get("fee_total") or 0), board_type=str(row.get("board_type") or ""),
                      plan_stop=row.get("plan_stop") if pd.notna(row.get("plan_stop")) else None,
                      plan_target=row.get("plan_target") if pd.notna(row.get("plan_target")) else None)
            lot.remaining = lot.qty
            open_lots.setdefault(code, []).append(lot)
            continue

        need = int(row["qty"])
        sell_fee = float(row.get("fee_total") or 0)
        lots = open_lots.get(code, [])
        while need > 0 and lots:
            lot = lots[0]
            take = min(need, lot.remaining)
            buy_fee = round(lot.fee * take / lot.qty, 2) if lot.qty else 0.0
            fee = round(buy_fee + (sell_fee * take / max(1, int(row["qty"]))), 2)
            trips.append(RoundTrip(
                code=code, name=row.get("name") or lot.board_type,
                entry_date=lot.date, entry_time=lot.time, entry_price=lot.price,
                exit_date=row["trade_date"], exit_time=row.get("trade_time") or "",
                exit_price=float(row["price"]), qty=take,
                buy_amount=round(lot.price * take, 2), sell_amount=round(float(row["price"]) * take, 2),
                fee=fee, board_type=lot.board_type,
                plan_stop=lot.plan_stop, plan_target=lot.plan_target,
            ))
            lot.remaining -= take
            need -= take
            if lot.remaining <= 0:
                lots.pop(0)
        # 卖出多于持仓（T+0 不允许，或数据不全）：不硬配，留给数据校验提示

    opens: List[RoundTrip] = []
    for code, lots in open_lots.items():
        for lot in lots:
            if lot.remaining > 0:
                opens.append(RoundTrip(
                    code=code, name="", entry_date=lot.date, entry_time=lot.time,
                    entry_price=lot.price, exit_date="", exit_time="", exit_price=0.0,
                    qty=lot.remaining, buy_amount=round(lot.price * lot.remaining, 2),
                    sell_amount=0.0, fee=round(lot.fee * lot.remaining / lot.qty, 2) if lot.qty else 0.0,
                    board_type=lot.board_type, plan_stop=lot.plan_stop,
                    plan_target=lot.plan_target, status="open",
                ))
    trips.sort(key=lambda t: (t.exit_date or "9999", t.exit_time or ""))
    opens.sort(key=lambda t: (t.entry_date, t.entry_time))
    return trips, opens


# ---------------------------------------------------------------------------
# 2. 统计
# ---------------------------------------------------------------------------
def _pct(part: int, total: int) -> float:
    return round(part / total * 100, 1) if total else 0.0


def _period(time_text: str) -> str:
    if not time_text:
        return "未知"
    try:
        hh, mm = int(time_text[:2]), int(time_text[3:5])
    except ValueError:
        return "未知"
    minutes = hh * 60 + mm
    if minutes < 570:
        return "开盘前"
    if minutes <= 600:
        return "9:30-10:00"
    if minutes <= 630:
        return "10:00-10:30"
    if minutes <= 690:
        return "10:30-11:30"
    if minutes <= 780:
        return "13:00-14:00"
    if minutes <= 870:
        return "14:00-14:30"
    return "尾盘 14:30+"


def daily_summary(trades: pd.DataFrame, trips: List[RoundTrip], opens: List[RoundTrip]) -> pd.DataFrame:
    dates = sorted(set(trades["trade_date"].tolist()))
    rows: List[Dict[str, Any]] = []
    for date in dates:
        day_buys = trades[(trades["trade_date"] == date) & (trades["side"] == "buy")]
        closed = [t for t in trips if t.exit_date == date]
        opened_bought = [t for t in trips if t.entry_date == date] + [t for t in opens if t.entry_date == date]
        win = [t for t in closed if t.net > 0]
        loss = [t for t in closed if t.net <= 0]
        rows.append({
            "交易日": date,
            "买入笔数": int(len(day_buys)),
            "买入金额": round(float(day_buys["amount"].sum()), 2),
            "买入标的数": int(day_buys["code"].nunique()),
            "平仓笔数": len(closed),
            "当日已实现盈亏": round(sum(t.net for t in closed), 2),
            "当日胜率%": _pct(len(win), len(closed)),
            "平均单笔收益%": round(sum(t.ret_pct for t in closed) / len(closed), 2) if closed else 0.0,
            "最大单笔盈利": round(max((t.net for t in closed), default=0.0), 2),
            "最大单笔亏损": round(min((t.net for t in closed), default=0.0), 2),
            "当日开仓标的": "、".join(sorted({t.code for t in opened_bought})) or "—",
            "持仓中笔数": len([t for t in opens if t.entry_date <= date]),
        })
    return pd.DataFrame(rows)


def style_profile(trades: pd.DataFrame, trips: List[RoundTrip]) -> Dict[str, Any]:
    buys = trades[trades["side"] == "buy"]
    closed = [t for t in trips if t.status == "closed"]
    wins = [t for t in closed if t.net > 0]
    losses = [t for t in closed if t.net <= 0]
    avg_win = sum(t.net for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.net for t in losses) / len(losses) if losses else 0.0
    win_rate = len(wins) / len(closed) if closed else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss if closed else 0.0
    plan_cov = len([b for b in buys.to_dict("records") if b.get("plan_stop") or b.get("plan_target")]) / len(buys) if len(buys) else 0.0
    kinds = [classify_exit(t)[0] for t in closed]
    stop_hit = [k for k in kinds if k.startswith("止损（按计划")]
    target_hit = [k for k in kinds if k.startswith("止盈（按计划")]
    in_range = [k for k in kinds if k.startswith("区间离场")]
    unplanned = [k for k in kinds if "未设计划" in k]
    plan_exec_rate = round((len(stop_hit) + len(target_hit) + len(in_range)) / len(closed) * 100, 1) if closed else None

    prefix = buys["code"].str[:2].value_counts().to_dict()
    board = buys["board_type"].fillna("").replace("", "未标注").value_counts().to_dict()
    period = buys["trade_time"].fillna("").map(_period).value_counts().to_dict()
    exit_types = pd.Series([classify_exit(t)[0] for t in closed]).value_counts().to_dict() if closed else {}

    # 加减仓：同一标的同一交易日的买入次数
    per_day = buys.groupby(["trade_date", "code"]).size()
    scale_in = int((per_day >= 2).sum())
    size = buys["amount"].astype(float)
    return {
        "买入笔数": int(len(buys)),
        "平仓笔数": len(closed),
        "胜率%": round(win_rate * 100, 1),
        "平均盈利": round(avg_win, 2),
        "平均亏损": round(avg_loss, 2),
        "盈亏比": round(avg_win / abs(avg_loss), 2) if avg_loss else None,
        "单笔期望": round(expectancy, 2),
        "净盈亏": round(sum(t.net for t in closed), 2),
        "累计收益率%": round(sum(t.net for t in closed) / buys["amount"].sum() * 100, 2) if len(buys) and buys["amount"].sum() else None,
        "计划覆盖率%": round(plan_cov * 100, 1),
        "计划执行率%": plan_exec_rate,
        "计划触发": {"止损触发": len(stop_hit), "止盈触发": len(target_hit),
                     "区间离场": len(in_range), "无计划离场": len(unplanned)},
        "平均单笔金额": round(float(size.mean()), 2) if len(size) else None,
        "单笔金额分位": {
            "P25": round(float(size.quantile(0.25)), 2),
            "P50": round(float(size.quantile(0.50)), 2),
            "P75": round(float(size.quantile(0.75)), 2),
            "最大": round(float(size.max()), 2),
        } if len(size) else {},
        "同标的加仓次数": scale_in,
        "板型分布": board,
        "入场时段": period,
        "市场分布": prefix,
        "离场类型": exit_types,
        "平均持仓": _avg_hold(closed),
    }


def _avg_hold(closed: List[RoundTrip]) -> str:
    if not closed:
        return "—"
    same_day = [t for t in closed if t.entry_date == t.exit_date]
    if len(same_day) == len(closed):
        mins = [t.hold_minutes for t in same_day if t.hold_minutes is not None]
        return f"日内平均 {round(sum(mins) / len(mins))} 分钟" if mins else "日内"
    overnight = len(closed) - len(same_day)
    return f"日内 {len(same_day)} 笔 / 隔夜 {overnight} 笔"


def equity_curve(trips: List[RoundTrip]) -> List[Tuple[str, float]]:
    curve: Dict[str, float] = {}
    total = 0.0
    for trip in sorted([t for t in trips if t.status == "closed"], key=lambda t: (t.exit_date, t.exit_time)):
        total += trip.net
        curve[trip.exit_date] = round(total, 2)
    return sorted(curve.items())


# ---------------------------------------------------------------------------
# 3. 渲染
# ---------------------------------------------------------------------------
def _kind_counts(trips: List[RoundTrip]) -> str:
    """把一批回合按离场类型计数，避免同一标的的多批次把整句重复多遍。"""
    if not trips:
        return ""
    counts: Dict[str, int] = {}
    for trip in trips:
        kind, _ = classify_exit(trip)
        counts[kind] = counts.get(kind, 0) + 1
    return "、".join(f"{kind} {num} 笔" for kind, num in sorted(counts.items(), key=lambda kv: -kv[1]))

def _fmt(value: Any, dash: str = "—") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return dash
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _money(value: float) -> str:
    return f"{value:+,.2f}"


def render_markdown(date: str, trades: pd.DataFrame, trips: List[RoundTrip],
                    opens: List[RoundTrip], demo: bool) -> str:
    day_buys = trades[(trades["trade_date"] == date) & (trades["side"] == "buy")]
    day_sells = trades[(trades["trade_date"] == date) & (trades["side"] == "sell")]
    opened = [t for t in trips if t.entry_date == date] + [t for t in opens if t.entry_date == date]
    closed = [t for t in trips if t.exit_date == date]
    realized = sum(t.net for t in closed)
    wins = len([t for t in closed if t.net > 0])

    lines: List[str] = []
    if demo:
        lines += [f"> ⚠️ **{DEMO_BANNER}**", ""]
    lines += [f"# 打板复盘 · {date}", ""]
    lines += [
        "## 一、当日概览", "",
        "| 指标 | 数值 | 指标 | 数值 |",
        "|---|---|---|---|",
        f"| 买入笔数 | {len(day_buys)} | 买入金额 | {_fmt(round(float(day_buys['amount'].sum()), 2))} |",
        f"| 买入标的 | {day_buys['code'].nunique()} | 卖出笔数 | {len(day_sells)} |",
        f"| 当日平仓 | {len(closed)} 笔 | 已实现盈亏 | {_money(realized)} |",
        f"| 当日胜率 | {_pct(wins, len(closed))}% | 持仓中 | {len([t for t in opens if t.entry_date <= date])} 笔 |",
        "",
    ]

    lines += ["## 二、逐笔买入（含费用与计划）", ""]
    if day_buys.empty:
        lines += ["当日无买入。", ""]
    else:
        lines += ["| 时间 | 代码 | 名称 | 价格 | 数量 | 成交额 | 费用 | 板型 | 计划止损 | 计划止盈 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for row in day_buys.to_dict("records"):
            lines.append("| {t} | {c} | {n} | {p} | {q} | {a} | {f} | {b} | {s} | {g} |".format(
                t=row.get("trade_time") or "—", c=row["code"], n=row.get("name") or "—",
                p=_fmt(float(row["price"])), q=int(row["qty"]), a=_fmt(float(row["amount"])),
                f=_fmt(float(row.get("fee_total") or 0)), b=row.get("board_type") or "未标注",
                s=_fmt(row.get("plan_stop")), g=_fmt(row.get("plan_target"))))
        lines.append("")

    lines += ["## 三、逐笔回合（买入→卖出，含止损止盈执行）", ""]
    if not opened:
        lines += ["当日无开仓回合。", ""]
    else:
        lines += ["| 买入 | 卖出 | 代码 | 数量 | 开平价 | 平仓价 | 净盈亏 | 收益率 | 持有 | 计划/执行 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for trip in opened:
            kind, why = classify_exit(trip)
            buy_at = f"{trip.entry_date} {trip.entry_time}"
            sell_at = f"{trip.exit_date} {trip.exit_time}" if trip.status == "closed" else "持仓中"
            plan = f"止损{_fmt(trip.plan_stop)} / 止盈{_fmt(trip.plan_target)}" if (trip.plan_stop or trip.plan_target) else "未设"
            lines.append("| {b} | {s} | {c} | {q} | {ep} | {xp} | {net} | {ret}% | {hold} | {plan}<br>{kind} |".format(
                b=buy_at, s=sell_at, c=trip.code, q=trip.qty,
                ep=_fmt(trip.entry_price), xp=_fmt(trip.exit_price) if trip.status == "closed" else "—",
                net=_money(trip.net) if trip.status == "closed" else "—",
                ret=trip.ret_pct if trip.status == "closed" else "—",
                hold=trip.hold_label, plan=plan, kind=kind))
        lines.append("")

    if not day_buys.empty:
        merged = pd.concat([day_buys, day_sells])
        buy_amt = float(day_buys["amount"].sum())
        fee = float(merged["fee_total"].sum())
        lines += ["## 四、当日交割单明细（成交 + 费用）", "",
                  "| 时间 | 代码 | 名称 | 方向 | 价格 | 数量 | 成交额 | 佣金 | 印花税 | 过户费 | 其他 | 费用合计 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for row in merged.sort_values(["trade_time"]).to_dict("records"):
            lines.append("| {t} | {c} | {n} | {d} | {p} | {q} | {a} | {cm} | {st} | {tr} | {ot} | {ft} |".format(
                t=row.get("trade_time") or "—", c=row["code"], n=row.get("name") or "—",
                d="买入" if row["side"] == "buy" else "卖出", p=_fmt(float(row["price"])),
                q=int(row["qty"]), a=_fmt(float(row["amount"])),
                cm=_fmt(row.get("commission")), st=_fmt(row.get("stamp_tax")),
                tr=_fmt(row.get("transfer_fee")), ot=_fmt(row.get("other_fee")),
                ft=_fmt(float(row.get("fee_total") or 0))))
        lines += ["", f"合计：买入 {_fmt(buy_amt)} 元 · 费用 {_fmt(fee)} 元 · 费用占成交额 {round(fee / max(buy_amt, 1) * 100, 3)}%", ""]

    lines += ["## 五、风格与执行小结", ""]
    if opened:
        periods = pd.Series([_period(t.entry_time) for t in opened]).value_counts().to_dict()
        boards = pd.Series([t.board_type or "未标注" for t in opened]).value_counts().to_dict()
        sizes = [t.entry_price * t.qty for t in opened]
        lines += [
            f"- **入场时段**：" + "、".join(f"{k} {v} 笔" for k, v in periods.items()),
            f"- **板型**：" + "、".join(f"{k} {v} 笔" for k, v in boards.items()),
            f"- **单笔金额**：中位 {_fmt(round(float(pd.Series(sizes).median()), 2))} 元，最大 {_fmt(round(max(sizes), 2))} 元",
            f"- **离场执行**：" + (_kind_counts([t for t in opened if t.status == "closed"])
                              or "均未平仓，无法评估执行"),
            "",
        ]
    else:
        lines += ["当日无开仓，不做风格判断。", ""]

    lines += ["---", "",
              "口径说明：净盈亏 =（平仓价−开仓价）×数量 − 买卖双边费用；收益率 = 净盈亏 / 开仓成本；",
              "打板属性优先取「板型」列，未提供时标注为未标注，不做价格猜测。", ""]
    return "\n".join(lines)


def render_overview(trades: pd.DataFrame, trips: List[RoundTrip], opens: List[RoundTrip],
                    daily: pd.DataFrame, style: Dict[str, Any], demo: bool) -> str:
    lines: List[str] = []
    if demo:
        lines += [f"> ⚠️ **{DEMO_BANNER}**", ""]
    dates = daily["交易日"].tolist()
    lines += ["# 打板复盘 · 总览", "",
              f"数据范围：{dates[0]} ~ {dates[-1]}（{len(dates)} 个交易日）", "",
              "## 一、总体结果", "",
              "| 指标 | 数值 |", "|---|---|",
              f"| 买入笔数 | {style['买入笔数']} |",
              f"| 平仓笔数 | {style['平仓笔数']} |",
              f"| 胜率 | {style['胜率%']}% |",
              f"| 平均盈利 / 平均亏损 | {_fmt(style['平均盈利'])} / {_fmt(style['平均亏损'])} |",
              f"| 盈亏比 | {_fmt(style['盈亏比'])} |",
              f"| 单笔期望 | {_fmt(style['单笔期望'])} |",
              f"| 净盈亏 | {_money(style['净盈亏'])} |",
              f"| 累计收益率 | {_fmt(style['累计收益率%'])}% |",
              f"| 计划覆盖率 | {style['计划覆盖率%']}% |",
              f"| 计划执行率 | {style['计划执行率%']}% |",
              f"| 平均持仓 | {style['平均持仓']} |",
              ""]
    lines += ["## 二、逐日汇总", "",
              "| 日期 | 买入笔数 | 买入金额 | 平仓 | 已实现盈亏 | 胜率% | 平均收益% |",
              "|---|---|---|---|---|---|---|"]
    for row in daily.to_dict("records"):
        lines.append("| {d} | {b} | {a} | {c} | {p} | {w} | {r} |".format(
            d=row["交易日"], b=row["买入笔数"], a=_fmt(row["买入金额"]), c=row["平仓笔数"],
            p=_money(row["当日已实现盈亏"]), w=row["当日胜率%"], r=row["平均单笔收益%"]))
    lines += ["", "## 三、风格画像", ""]
    lines += [
        f"- **市场分布**：" + "、".join(f"{k} {v} 笔" for k, v in style["市场分布"].items()),
        f"- **板型偏好**：" + "、".join(f"{k} {v} 笔" for k, v in style["板型分布"].items()),
        f"- **入场时段**：" + "、".join(f"{k} {v} 笔" for k, v in style["入场时段"].items()),
        f"- **单笔金额**：P25 {_fmt(style['单笔金额分位'].get('P25'))} / P50 {_fmt(style['单笔金额分位'].get('P50'))} / P75 {_fmt(style['单笔金额分位'].get('P75'))}",
        f"- **同标的加仓**：{style['同标的加仓次数']} 次（同一交易日同标的多次买入）",
        f"- **离场类型**：" + ("、".join(f"{k} {v} 笔" for k, v in style["离场类型"].items()) or "—"),
        "",
        "## 四、策略有效性评估", "",
    ]
    pf = None
    if style["平均亏损"]:
        pf = round(style["平均盈利"] * style["胜率%"] / (abs(style["平均亏损"]) * (100 - style["胜率%"])), 2) if style["胜率%"] not in (100.0, 0.0) else None
    lines += [
        f"- 单笔期望 **{_fmt(style['单笔期望'])} 元**：" + ("为正，重复执行有正收益" if (style["单笔期望"] or 0) > 0 else "为负，按当前执行方式长期会亏损"),
        f"- 盈亏比 **{_fmt(style['盈亏比'])}**、胜率 **{style['胜率%']}%**" + (f"、盈利因子 **{pf}**" if pf else ""),
        f"- 计划覆盖率 **{style['计划覆盖率%']}%**：" + ("多数交易有预设计划，执行可比对" if style["计划覆盖率%"] >= 60 else "多数交易没有预先写止损/止盈，事后归因容易失真"),
        f"- 计划执行率 **{style['计划执行率%']}%**："
        + "、".join(f"{k} {v} 笔" for k, v in (style.get("计划触发") or {}).items())
        + "（执行率=离场落在计划框架内的比例）",
        "",
        "---", "",
        "口径：按标的 FIFO 配对；净盈亏已扣双边费用；未平仓单不计入盈亏，只计持仓。", "",
    ]
    return "\n".join(lines)


def render_html(trades: pd.DataFrame, trips: List[RoundTrip], opens: List[RoundTrip],
                daily: pd.DataFrame, style: Dict[str, Any], demo: bool, span: str) -> str:
    # 用极简内联样式，避免任何外部依赖（离线可开）
    curve = equity_curve(trips)
    max_abs = max([abs(v) for _, v in curve], default=1) or 1
    points = []
    width, height = 640, 160
    for idx, (_, value) in enumerate(curve):
        x = 20 + idx * (width - 40) / max(1, len(curve) - 1)
        y = height / 2 - value / max_abs * (height / 2 - 12)
        points.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(points)

    def bar_rows(data: Dict[str, Any], total: int) -> str:
        out = []
        for key, value in list(data.items())[:8]:
            pct = round(value / total * 100, 1) if total else 0
            out.append(
                f'<div class="bar"><span class="bar-k">{html.escape(str(key))}</span>'
                f'<span class="bar-t"><i style="width:{pct}%"></i></span>'
                f'<span class="bar-v">{value}｜{pct}%</span></div>')
        return "".join(out) or '<p class="muted">无数据</p>'

    rows = []
    for trip in sorted(opens + trips, key=lambda t: (t.entry_date, t.entry_time)):
        kind, why = classify_exit(trip)
        rows.append(
            f"<tr><td>{trip.entry_date} {trip.entry_time}</td><td>{trip.code}</td>"
            f"<td class='{'up' if trip.net > 0 else 'down' if trip.net < 0 else ''}'>"
            f"{_fmt(trip.entry_price)}</td><td>{_fmt(trip.exit_price) if trip.status == 'closed' else '持仓中'}</td>"
            f"<td>{trip.qty}</td>"
            f"<td class='{'up' if trip.net > 0 else 'down' if trip.net < 0 else ''}'>{_money(trip.net) if trip.status == 'closed' else '—'}</td>"
            f"<td>{trip.ret_pct if trip.status == 'closed' else '—'}</td><td>{trip.hold_label}</td>"
            f"<td title='{html.escape(why)}'>{html.escape(kind)}</td></tr>")

    daily_rows = "".join(
        f"<tr><td>{r['交易日']}</td><td>{r['买入笔数']}</td><td>{_fmt(r['买入金额'])}</td>"
        f"<td>{r['平仓笔数']}</td><td class='{'up' if r['当日已实现盈亏'] > 0 else 'down' if r['当日已实现盈亏'] < 0 else ''}'>"
        f"{_money(r['当日已实现盈亏'])}</td><td>{r['当日胜率%']}%</td><td>{r['平均单笔收益%']}%</td></tr>"
        for r in daily.to_dict("records"))

    banner = (f'<div class="banner">{DEMO_BANNER}</div>' if demo else "")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>打板复盘 · {html.escape(span)}</title>
<style>
:root{{--bg:#0f1216;--panel:#161b22;--line:#262d36;--text:#e6edf3;--muted:#8b949e;--up:#f85149;--down:#3fb950;--accent:#d2a86a}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:28px 20px 60px}}
h1{{font-size:20px;font-weight:600;margin:0 0 4px}}
h2{{font-size:15px;font-weight:600;margin:28px 0 10px;color:var(--accent)}}
.meta{{color:var(--muted);font-size:12px;margin-bottom:16px}}
.banner{{background:#3a2a12;border:1px solid #6b4e1f;color:#f0c987;padding:8px 12px;border-radius:6px;font-size:12px;margin-bottom:16px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px}}
.card b{{display:block;font-size:18px;font-weight:600;margin-top:4px}}
.card span{{color:var(--muted);font-size:12px}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden;font-size:12.5px}}
th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{color:var(--muted);font-weight:500;background:#12171d}}
tr:last-child td{{border-bottom:none}}
.up{{color:var(--up)}} .down{{color:var(--down)}}
.muted{{color:var(--muted)}}
.bar{{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:12px}}
.bar-k{{width:110px;color:var(--muted)}} .bar-t{{flex:1;height:8px;background:#12171d;border-radius:4px;overflow:hidden}}
.bar-t i{{display:block;height:100%;background:var(--accent);border-radius:4px}}
.bar-v{{width:110px;text-align:right;color:var(--muted)}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
@media(max-width:720px){{.two{{grid-template-columns:1fr}}}}
.foot{{color:var(--muted);font-size:11.5px;margin-top:28px;border-top:1px solid var(--line);padding-top:12px}}
</style></head><body><div class="wrap">
{banner}
<h1>打板复盘分析</h1>
<div class="meta">区间 {html.escape(span)} ｜ 买入 {style['买入笔数']} 笔 ｜ 平仓 {style['平仓笔数']} 笔 ｜ 生成于 {datetime.now():%Y-%m-%d %H:%M}</div>

<div class="cards">
  <div class="card"><span>净盈亏</span><b class="{'up' if style['净盈亏'] > 0 else 'down'}">{_money(style['净盈亏'])}</b></div>
  <div class="card"><span>胜率</span><b>{style['胜率%']}%</b></div>
  <div class="card"><span>盈亏比</span><b>{_fmt(style['盈亏比'])}</b></div>
  <div class="card"><span>单笔期望</span><b class="{'up' if (style['单笔期望'] or 0) > 0 else 'down'}">{_fmt(style['单笔期望'])}</b></div>
  <div class="card"><span>计划覆盖率</span><b>{style['计划覆盖率%']}%</b></div>
  <div class="card"><span>平均持仓</span><b style="font-size:14px">{style['平均持仓']}</b></div>
</div>

<h2>累计已实现盈亏</h2>
<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img">
  <line x1="20" y1="{height/2}" x2="{width-20}" y2="{height/2}" stroke="#262d36" stroke-width="1"/>
  <polyline points="{polyline}" fill="none" stroke="#d2a86a" stroke-width="2"/>
</svg>
<div class="meta">按平仓日累计（元）；零轴以上为盈利。</div>

<h2>逐日汇总</h2>
<table><thead><tr><th>交易日</th><th>买入笔数</th><th>买入金额</th><th>平仓</th><th>已实现盈亏</th><th>胜率</th><th>平均收益</th></tr></thead>
<tbody>{daily_rows}</tbody></table>

<h2>逐笔回合</h2>
<table><thead><tr><th>买入</th><th>代码</th><th>开平价</th><th>平仓价</th><th>数量</th><th>净盈亏</th><th>收益率%</th><th>持有</th><th>离场执行</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="9" class="muted">无回合</td></tr>'}</tbody></table>

<div class="two">
  <div><h2>入场时段分布</h2>{bar_rows(style['入场时段'], max(1, style['买入笔数']))}</div>
  <div><h2>板型分布</h2>{bar_rows(style['板型分布'], max(1, style['买入笔数']))}</div>
</div>
<div class="two">
  <div><h2>离场类型</h2>{bar_rows(style['离场类型'], max(1, style['平仓笔数']))}</div>
  <div><h2>单笔金额分位（相对最大值）</h2>{bar_rows({'P25': style['单笔金额分位'].get('P25') or 0, 'P50': style['单笔金额分位'].get('P50') or 0, 'P75': style['单笔金额分位'].get('P75') or 0}, max(1, int(style['单笔金额分位'].get('最大') or 1)))}</div>
</div>

<div class="foot">
口径：按标的 FIFO 配对；净盈亏 =（平仓价−开仓价）×数量 − 双边费用；收益率 = 净盈亏/开仓成本；
未平仓不计盈亏。打板属性取「板型」列，未提供则标注未标注，不做价格猜测。
</div>
</div></body></html>"""


def build_excel(trades: pd.DataFrame, trips: List[RoundTrip], opens: List[RoundTrip],
                daily: pd.DataFrame, style: Dict[str, Any], path: str) -> None:
    trip_rows = []
    for trip in sorted(opens + trips, key=lambda t: (t.entry_date, t.entry_time)):
        kind, why = classify_exit(trip)
        trip_rows.append({
            "买入日期": trip.entry_date, "买入时间": trip.entry_time, "代码": trip.code,
            "名称": trip.name or "", "开仓价": trip.entry_price, "数量": trip.qty,
            "开仓金额": trip.buy_amount, "卖出日期": trip.exit_date, "卖出时间": trip.exit_time,
            "平仓价": trip.exit_price if trip.status == "closed" else None,
            "卖出金额": trip.sell_amount, "费用": trip.fee,
            "毛利": trip.gross if trip.status == "closed" else None,
            "净盈亏": trip.net if trip.status == "closed" else None,
            "收益率%": trip.ret_pct if trip.status == "closed" else None,
            "持有": trip.hold_label, "板型": trip.board_type or "未标注",
            "计划止损": trip.plan_stop, "计划止盈": trip.plan_target,
            "离场类型": kind, "判定依据": why, "状态": "已平" if trip.status == "closed" else "持仓",
        })
    style_rows = [{"指标": key, "数值": (json.dumps(val, ensure_ascii=False) if isinstance(val, dict) else val)}
                  for key, val in style.items()]
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        trades.to_excel(writer, sheet_name="成交明细", index=False)
        pd.DataFrame(trip_rows).to_excel(writer, sheet_name="逐笔回合", index=False)
        daily.to_excel(writer, sheet_name="每日汇总", index=False)
        pd.DataFrame(style_rows).to_excel(writer, sheet_name="风格统计", index=False)
        pd.DataFrame([
            {"口径": "净盈亏", "说明": "（平仓价−开仓价）×数量 − 买卖双边费用"},
            {"口径": "收益率", "说明": "净盈亏 / 开仓成本"},
            {"口径": "配对", "说明": "按标的 FIFO，卖出优先冲抵最早批次"},
            {"口径": "止损止盈", "说明": "优先与计划价比对；无计划时按 ≤-5% / ≥+7% 归类并标注未设计划"},
            {"口径": "未平仓", "说明": "只计持仓，不计盈亏"},
            {"口径": "费用", "说明": "优先用成交明细里的费用列；缺失时按佣金万2.5(最低5元)+印花税千0.5(卖出)+过户费十万分之一估算"},
        ]).to_excel(writer, sheet_name="口径说明", index=False)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="打板复盘：成交明细 → 回合 → 日报/台账/看板")
    parser.add_argument("--trades", default=os.path.join(ROOT, "data", "review", "trades_normalized.csv"))
    parser.add_argument("--out", default=REPORT_DIR)
    parser.add_argument("--demo", action="store_true", help="按示例数据打标输出")
    args = parser.parse_args(argv)

    if not os.path.exists(args.trades):
        print(f"未找到成交明细：{args.trades}\n先运行：python scripts/review/parse_statement.py --input <交割单>")
        return 2
    trades = pd.read_csv(args.trades, dtype={"trade_time": str, "board_type": str})
    if trades.empty:
        print("成交明细为空")
        return 2

    trips, opens = match_round_trips(trades)
    daily = daily_summary(trades, trips, opens)
    style = style_profile(trades, trips)
    dates = daily["交易日"].tolist()
    span = f"{dates[0]} ~ {dates[-1]}"

    os.makedirs(os.path.join(args.out, "daily"), exist_ok=True)
    for date in dates:
        md = render_markdown(date, trades, trips, opens, args.demo)
        suffix = "_DEMO" if args.demo else ""
        with open(os.path.join(args.out, "daily", f"打板复盘_{date}{suffix}.md"), "w", encoding="utf-8") as fh:
            fh.write(md)
    with open(os.path.join(args.out, f"打板复盘_总览{'_DEMO' if args.demo else ''}.md"), "w", encoding="utf-8") as fh:
        fh.write(render_overview(trades, trips, opens, daily, style, args.demo))
    excel_name = f"打板复盘台账_{dates[0]}_{dates[-1]}{'_DEMO' if args.demo else ''}.xlsx"
    build_excel(trades, trips, opens, daily, style, os.path.join(args.out, excel_name))
    html_name = f"打板复盘_{dates[0]}_{dates[-1]}{'_DEMO' if args.demo else ''}.html"
    with open(os.path.join(args.out, html_name), "w", encoding="utf-8") as fh:
        fh.write(render_html(trades, trips, opens, daily, style, args.demo, span))

    print(f"✓ 日报 {len(dates)} 份 → {os.path.join(args.out, 'daily')}")
    print(f"✓ 总览 → {os.path.join(args.out, '打板复盘_总览' + ('_DEMO' if args.demo else '') + '.md')}")
    print(f"✓ 台账 → {os.path.join(args.out, excel_name)}")
    print(f"✓ 看板 → {os.path.join(args.out, html_name)}")
    print(f"  回合：已平 {len(trips)} / 持仓 {len(opens)}｜胜率 {style['胜率%']}%｜净盈亏 {style['净盈亏']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
