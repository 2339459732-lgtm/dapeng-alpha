# -*- coding: utf-8 -*-
"""ICT 滚动回测「未来函数」自证脚本（只读，可反复运行）。

三道检查，任何一道失败即说明 ``/api/ict/history`` 混入了未来数据：

A. **截尾不变性**：同一序列取前缀 N 与 N+K（同一个起点），重叠日期上逐日的
   ``status / direction / score / passed_gates`` 必须**完全一致**。
B. **未来数据投毒**：把某日之后的收盘价全部 ×1.5（人为制造剧烈未来行情），
   该日及之前的逐日状态必须**一个字节都不变**。
C. **统计不变性**：截尾后的 ``stats/confirmed_events`` 在重叠日期范围内一致。

用法（项目根）：
    %LOCALAPPDATA%\\Programs\\Python\\Python311\\python.exe scripts\\verify_ict_history.py
退出码 0 = 全部通过，1 = 存在未来函数。
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import ict_history, ict_model            # noqa: E402
from server.tencent_kline import fetch_tencent_kline  # noqa: E402

CODE = sys.argv[1] if len(sys.argv) > 1 else "sh600519"
FETCH_LIMIT = 800
CUT_POINTS = (250, 400, 550)     # 均为 >= MIN_CANDLES 的索引边界


def _fingerprint(rows: List[Dict]) -> Dict[str, tuple]:
    """date -> (status, direction, score, passed_gates)，用于逐日比对。"""
    return {
        row["date"]: (row["status"], row["direction"], row["score"], row["passed_gates"])
        for row in rows
    }


def _diff(left: Dict[str, tuple], right: Dict[str, tuple]) -> List[str]:
    problems: List[str] = []
    for date in sorted(set(left) & set(right)):
        if left[date] != right[date]:
            problems.append(f"{date}: {left[date]} != {right[date]}")
    return problems


def _poison(candles: List[ict_model.Candle], after_index: int) -> List[ict_model.Candle]:
    """把 ``after_index`` 之后的 K 线整体放大 1.5 倍（制造不存在的未来行情）。"""
    out: List[ict_model.Candle] = []
    for index, candle in enumerate(candles):
        if index <= after_index:
            out.append(candle)
            continue
        out.append(replace(
            candle,
            open=round(candle.open * 1.5, 4),
            high=round(candle.high * 1.5, 4),
            low=round(candle.low * 1.5, 4),
            close=round(candle.close * 1.5, 4),
        ))
    return out


async def main() -> int:
    bars = await fetch_tencent_kline(CODE, period="day", limit=FETCH_LIMIT, timeout=10.0)
    candles = ict_model.parse_kline_rows(bars)
    print(f"[数据] {CODE} 日线 {len(candles)} 根：{candles[0].timestamp:%Y-%m-%d} ~ "
          f"{candles[-1].timestamp:%Y-%m-%d}")

    full = ict_history.rolling_scan(candles)
    full_print = _fingerprint(full)
    print(f"[A] 全量滚动扫描 {len(full)} 次（{full[0]['date']} ~ {full[-1]['date']}）")

    failures = 0

    # ---- A. 截尾不变性 ----
    for cut in CUT_POINTS:
        prefix = ict_history.rolling_scan(candles[:cut])
        problems = _diff(_fingerprint(prefix), full_print)
        status = "PASS" if not problems else f"FAIL({len(problems)})"
        print(f"[A] 截尾到 {cut} 根：重叠 {len(prefix)} 天逐日比对 → {status}")
        if problems:
            failures += 1
            for line in problems[:5]:
                print("     ", line)

    # ---- B. 未来数据投毒 ----
    for cut in CUT_POINTS:
        cut_date = ict_history.bar_date(candles[cut - 1])  # noqa: SLF001
        poisoned_rows = ict_history.rolling_scan(_poison(candles, cut - 1))
        left = {d: v for d, v in _fingerprint(poisoned_rows).items() if d <= cut_date}
        right = {d: v for d, v in full_print.items() if d <= cut_date}
        problems = _diff(left, right)
        status = "PASS" if not problems else f"FAIL({len(problems)})"
        print(f"[B] {cut} 根之后投毒 ×1.5：{cut_date} 及之前 {len(left)} 天状态 → {status}")
        if problems:
            failures += 1
            for line in problems[:5]:
                print("     ", line)

    # ---- C. 统计不变性（截尾 vs 全量，重叠事件必须一致） ----
    short_candles = candles[:400]
    short_payload = ict_history.build_history(short_candles, None, (1, 3, 5, 10))
    full_payload = ict_history.build_history(candles, None, (1, 3, 5, 10))
    short_events = {e["date"] for e in short_payload["confirmed_events"]}
    full_events = {e["date"] for e in full_payload["confirmed_events"]
                   if e["date"] <= short_payload["scanned_to"]}
    extra = short_events - full_events
    missing = full_events - short_events
    status = "PASS" if not extra and not missing else "FAIL"
    print(f"[C] 截尾 400 vs 全量 800：事件 {sorted(short_events)} vs {sorted(full_events)} → {status}")
    if extra or missing:
        failures += 1
        print(f"      多出={sorted(extra)} 缺失={sorted(missing)}")

    print("\n结论：", "未发现未来函数（三项全过）" if failures == 0
          else f"存在 {failures} 项失败，必须修")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
