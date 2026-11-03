#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""交割单/成交明细解析器（statement-parse.v1）。

券商与行情软件的导出列名五花八门（「买卖标志」vs「操作」vs「业务名称」，
「成交金额」vs「发生金额」），直接进分析脚本必然错列。这里做一件事：
**把任意来源的成交明细映射成一套固定字段**，后续复盘只认这套字段。

支持的输入：`.csv` / `.xlsx` / `.xls`（自动识别表头所在行）。
输出：`data/review/trades_normalized.csv`（UTF-8 BOM，Excel 可直接打开）。

用法：
    python scripts/review/parse_statement.py --input 交割单.xlsx
    python scripts/review/parse_statement.py --input 成交.csv --out data/review/my.csv
    python scripts/review/parse_statement.py --demo          # 生成示例数据并解析
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_OUT = os.path.join(ROOT, "data", "review", "trades_normalized.csv")

#: 目标字段（下游只认这些）
COLUMNS = [
    "trade_date", "trade_time", "code", "name", "side", "price", "qty", "amount",
    "commission", "stamp_tax", "transfer_fee", "other_fee", "fee_total",
    "order_id", "board_type", "plan_stop", "plan_target", "note",
]

#: 别名表：键是目标字段，值是该字段可能出现的列名（含常见券商写法）
ALIASES: Dict[str, List[str]] = {
    "trade_date": ["成交日期", "发生日期", "交易日期", "日期", "trade_date", "date"],
    "trade_time": ["成交时间", "发生时间", "委托时间", "时间", "trade_time", "time"],
    "code": ["证券代码", "股票代码", "代码", "证券编码", "code", "symbol"],
    "name": ["证券名称", "股票名称", "名称", "name"],
    "side": ["买卖标志", "买卖方向", "操作", "业务名称", "委托类别", "买卖", "side", "type"],
    "price": ["成交价格", "成交均价", "成交价", "价格", "price"],
    "qty": ["成交数量", "成交股数", "成交量", "数量", "qty", "volume"],
    "amount": ["成交金额", "发生金额", "金额", "amount"],
    "commission": ["佣金", "手续费", "交易手续费", "commission", "fee"],
    "stamp_tax": ["印花税", "stamp_tax"],
    "transfer_fee": ["过户费", "transfer_fee"],
    "other_fee": ["其他费", "附加费", "规费", "other_fee"],
    "order_id": ["成交编号", "委托编号", "合同编号", "order_id"],
    "board_type": ["板型", "板数", "首板/连板", "board_type"],
    "plan_stop": ["止损价", "计划止损", "plan_stop"],
    "plan_target": ["止盈价", "目标价", "计划止盈", "plan_target"],
    "note": ["备注", "摘要", "note"],
}


def _norm(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _pick(df: pd.DataFrame, field: str) -> Optional[str]:
    """在 DataFrame 列名里找该字段对应的实际列（先精确再包含）。"""
    lookup = {_norm(col): col for col in df.columns}
    for alias in ALIASES[field]:
        key = _norm(alias)
        if key in lookup:
            return lookup[key]
    for alias in ALIASES[field]:
        key = _norm(alias)
        for norm_col, col in lookup.items():
            if key and key in norm_col:
                return col
    return None


def _side(value: Any) -> Optional[str]:
    text = _norm(value)
    if not text:
        return None
    if "买" in text or text in ("b", "buy", "1"):
        return "buy"
    if "卖" in text or text in ("s", "sell", "2"):
        return "sell"
    return None


def _code(value: Any) -> Optional[str]:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 6:
        return None
    if digits.startswith(("6", "9")):
        return "sh" + digits
    if digits.startswith(("0", "2", "3")):
        return "sz" + digits
    return "bj" + digits


def _date(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:len(fmt) + 2].strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


def _time(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}:{match.group(3) or '00'}"
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 6:
        return f"{digits[:2]}:{digits[2:4]}:{digits[4:6]}"
    return None


def _num(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().replace(",", "").replace("¥", "").replace("元", "")
    if text in ("", "-", "--"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_table(path: str) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        # header=None 再自己找表头：券商导出常带 3~6 行标题/账号信息
        raw = pd.read_excel(path, header=None, dtype=str)
        header_row = 0
        for idx in range(min(12, len(raw))):
            row = [_norm(x) for x in raw.iloc[idx].tolist()]
            hits = sum(1 for alias in ("证券代码", "成交日期", "成交数量", "买卖") if any(alias in cell for cell in row))
            if hits >= 2:
                header_row = idx
                break
        header = raw.iloc[header_row].tolist()
        df = raw.iloc[header_row + 1:].copy()
        df.columns = [str(h) for h in header]
        return df.reset_index(drop=True)
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            return pd.read_csv(path, dtype=str, encoding=enc, skiprows=_guess_skiprows(path, enc))
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"无法识别文件编码：{path}")


def _guess_skiprows(path: str, encoding: str) -> int:
    with open(path, "r", encoding=encoding, errors="replace") as fh:
        for idx, line in enumerate(fh):
            if idx >= 12:
                break
            if sum(1 for key in ("证券代码", "成交日期", "成交数量", "买卖") if key in line) >= 2:
                return idx
    return 0


def normalize(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    mapping = {field: _pick(df, field) for field in ALIASES}
    missing = [field for field in ("trade_date", "code", "side", "price", "qty") if not mapping[field]]
    if missing:
        raise SystemExit(
            "缺少必要列：" + "、".join(missing)
            + "\n实际表头：" + " | ".join(str(c) for c in df.columns[:20])
            + "\n提示：可在 parse_statement.py 的 ALIASES 里补上你的列名。"
        )

    rows: List[Dict[str, Any]] = []
    skipped = 0
    for _, src in df.iterrows():
        code = _code(src.get(mapping["code"]))
        side = _side(src.get(mapping["side"]))
        price = _num(src.get(mapping["price"]))
        qty = _num(src.get(mapping["qty"]))
        date = _date(src.get(mapping["trade_date"]))
        if not (code and side and price and qty and date):
            skipped += 1
            continue
        amount = _num(src.get(mapping["amount"])) if mapping["amount"] else None
        amount = amount if amount is not None else round(price * qty, 2)
        commission = _num(src.get(mapping["commission"])) if mapping["commission"] else None
        stamp = _num(src.get(mapping["stamp_tax"])) if mapping["stamp_tax"] else None
        transfer = _num(src.get(mapping["transfer_fee"])) if mapping["transfer_fee"] else None
        other = _num(src.get(mapping["other_fee"])) if mapping["other_fee"] else None
        fee_total = sum(v for v in (commission, stamp, transfer, other) if v)
        if not fee_total:
            # 没给费用时按市场规则估算，报告里会标注「费用为估算」
            fee_total = round(max(5.0, amount * 0.00025) + (amount * 0.0005 if side == "sell" else 0)
                              + amount * 0.00001, 2)
        rows.append({
            "trade_date": date,
            "trade_time": _time(src.get(mapping["trade_time"])) if mapping["trade_time"] else None,
            "code": code,
            "name": str(src.get(mapping["name"]) or "").strip() if mapping["name"] else "",
            "side": side, "price": round(price, 3), "qty": int(qty), "amount": round(amount, 2),
            "commission": commission, "stamp_tax": stamp, "transfer_fee": transfer,
            "other_fee": other, "fee_total": round(fee_total, 2),
            "order_id": str(src.get(mapping["order_id"]) or "").strip() if mapping["order_id"] else "",
            "board_type": str(src.get(mapping["board_type"]) or "").strip() if mapping["board_type"] else "",
            "plan_stop": _num(src.get(mapping["plan_stop"])) if mapping["plan_stop"] else None,
            "plan_target": _num(src.get(mapping["plan_target"])) if mapping["plan_target"] else None,
            "note": str(src.get(mapping["note"]) or "").strip() if mapping["note"] else "",
        })

    out = pd.DataFrame(rows, columns=COLUMNS)
    if not out.empty:
        out = out.sort_values(["trade_date", "trade_time", "code"], na_position="last")
    meta = {
        "rows": len(out), "skipped": skipped,
        "columns_found": {k: v for k, v in mapping.items() if v},
        "fee_estimated": bool(rows) and all(r.get("commission") is None for r in rows),
        "dates": sorted(out["trade_date"].unique().tolist()) if not out.empty else [],
    }
    return out, meta


def _demo_frame() -> pd.DataFrame:
    """示例数据：**仅用于验证解析与报表管线**，代码与成交均为构造，不可当真实复盘。"""
    rows = [
        ("2026-10-30", "09:31:12", "300123", "示例科技A", "买入", 12.86, 30000, "首板", 12.10, 15.20),
        ("2026-10-30", "10:02:40", "300123", "示例科技A", "买入", 13.00, 20000, "首板", 12.10, 15.20),
        ("2026-10-31", "09:30:58", "300123", "示例科技A", "卖出", 14.62, 50000, "", None, None),
        ("2026-10-30", "09:45:03", "600456", "示例材料B", "买入", 21.30, 15000, "二板", 19.60, 24.00),
        ("2026-10-31", "09:38:20", "600456", "示例材料B", "卖出", 20.15, 15000, "", None, None),
        ("2026-10-31", "10:12:35", "002789", "示例智造C", "买入", 8.42, 40000, "首板", 7.90, 9.90),
        ("2026-11-03", "09:33:10", "002789", "示例智造C", "卖出", 9.18, 40000, "", None, None),
    ]
    df = pd.DataFrame(rows, columns=["成交日期", "成交时间", "证券代码", "证券名称", "买卖标志",
                                     "成交价格", "成交数量", "板型", "止损价", "止盈价"])
    df["佣金"] = None
    return df


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="交割单/成交明细 → 统一字段")
    parser.add_argument("--input", help="CSV/XLSX 路径")
    parser.add_argument("--out", default=DEFAULT_OUT, help="输出去向")
    parser.add_argument("--demo", action="store_true", help="生成示例数据并解析（仅验证管线）")
    args = parser.parse_args(argv)

    if args.demo:
        df = _demo_frame()
        out, meta = normalize(df)
        args.out = os.path.join(ROOT, "data", "review", "sample_statement_normalized.csv")
    elif args.input:
        if not os.path.exists(args.input):
            print(f"文件不存在：{args.input}")
            return 2
        out, meta = normalize(read_table(args.input))
    else:
        parser.print_help()
        return 2

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    out.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"已写入 {args.out}")
    print(f"  成交行 {meta['rows']}（跳过 {meta['skipped']} 行无效/非成交）")
    print(f"  覆盖交易日：{', '.join(meta['dates']) or '无'}")
    print("  识别到的列：" + "、".join(f"{k}←{v}" for k, v in meta["columns_found"].items()))
    if meta["fee_estimated"]:
        print("  ⚠ 未提供费用列，已按佣金万 2.5（最低 5 元）+ 印花税千 0.5（仅卖出）+ 过户费十万分之一估算")
    return 0 if meta["rows"] else 1


if __name__ == "__main__":
    sys.exit(main())
