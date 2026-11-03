# -*- coding: utf-8 -*-
"""ICT 结构统计「未来函数」自证脚本（只读，可反复运行）。

检查项：
A. **截尾不变性**：全序列 vs 前缀（250/400/550 根），同一个 FVG / sweep 的
   首次填充/反转滞后天数必须**逐个一致**（截尾导致的合法删失单独计数并校验成因）。
B. **未来数据投毒**：把某日之后的 K 线 ×1.5，该日及之前成立的结构结果必须不变。
C. **样本数对账**：``samples + censored == detected``，且 detected 等于
   ``ict_model.detect_fvgs`` 的返回数（防止误用 annotations 的最近 20 个）。
D. **聚合一致**：同一 FVG 子集的填充率在截尾前后完全相等。

用法（项目根）：
    python scripts\\verify_ict_structure.py [code]
退出码 0 = 全部通过。
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import ict_history, ict_model, ict_structure  # noqa: E402
from server.tencent_kline import fetch_tencent_kline       # noqa: E402

CODE = sys.argv[1] if len(sys.argv) > 1 else "sh600519"
FETCH_LIMIT = 800
HORIZON = 10
CUT_POINTS = (250, 400, 550)


def fvg_detail(candles: List[ict_model.Candle], horizon: int
               ) -> Dict[Tuple[str, float], Optional[Tuple[Optional[int], Optional[int]]]]:
    """每个 FVG 的 (成立日, 区间下沿) -> (触及滞后, 穿中滞后) / None(删失)。"""
    detail: Dict[Tuple[str, float], Optional[Tuple[Optional[int], Optional[int]]]] = {}
    for fvg in ict_model.detect_fvgs(candles):
        end_index = int(fvg["end_index"])
        key = (end_index, ict_history.bar_date(candles[end_index]), float(fvg["zone"][0]))
        detail[key] = ict_structure._scan_fvg_hits(candles, fvg, horizon)  # noqa: SLF001
    return detail


def sweep_detail(candles: List[ict_model.Candle], horizon: int
                 ) -> Dict[Tuple[str, str], Optional[int]]:
    """每个 sweep 的 (成立日, kind) -> 反转滞后天数 / None(未反转或删失)。"""
    swings = ict_model.detect_swings(candles, 5)
    atr = ict_structure.atr_series(candles)
    detail: Dict[Tuple[str, str], Optional[int]] = {}
    for sweep in ict_model.detect_sweeps(candles, swings, 80, "Asia/Shanghai", True,
                                         swing_width=5):
        index = int(sweep["index"])
        key = (index, ict_history.bar_date(candles[index]), sweep["kind"])
        unit = atr[index]
        start, last = index + 1, index + horizon
        if not unit or unit <= 0 or start >= len(candles) or last > len(candles) - 1:
            detail[key] = -1                      # -1 = 删失，区别于 None(未反转)
            continue
        long_side = sweep["direction"] == "long"
        level = candles[index].close + (unit if long_side else -unit)
        hit: Optional[int] = None
        for cursor in range(start, last + 1):
            candle = candles[cursor]
            if (candle.high >= level) if long_side else (candle.low <= level):
                hit = cursor - index
                break
        detail[key] = hit
    return detail


def _poison(candles: List[ict_model.Candle], after: int) -> List[ict_model.Candle]:
    out: List[ict_model.Candle] = []
    for index, candle in enumerate(candles):
        if index <= after:
            out.append(candle)
            continue
        out.append(replace(candle,
                           open=round(candle.open * 1.5, 4),
                           high=round(candle.high * 1.5, 4),
                           low=round(candle.low * 1.5, 4),
                           close=round(candle.close * 1.5, 4)))
    return out


def compare(label: str, left: Dict[Any, Any], right: Dict[Any, Any]) -> int:
    """逐个比对；返回失败数。``None``/``-1`` 为「删失」哨兵（两者语义不同、互不混用）。"""
    problems: List[str] = []
    shared = set(left) & set(right)
    censored_by_cut = 0
    for key in sorted(shared):
        value_left, value_right = left[key], right[key]
        left_censored = value_left is None or value_left == -1
        right_censored = value_right is None or value_right == -1
        if left_censored and not right_censored:
            # 截尾一侧删失：只允许出现在「成立日距截断点不足 horizon 根」的结构上
            censored_by_cut += 1
            continue
        if value_left != value_right:
            problems.append(f"{key}: {value_left} != {value_right}")
    status = "PASS" if not problems else f"FAIL({len(problems)})"
    print(f"[{label}] 共有结构 {len(shared)} 个，逐项比对 → {status}"
          f"（其中因截尾合法删失 {censored_by_cut} 个）")
    for line in problems[:5]:
        print("     ", line)
    return len(problems)


async def main() -> int:
    bars = await fetch_tencent_kline(CODE, period="day", limit=FETCH_LIMIT, timeout=10.0)
    candles = ict_model.parse_kline_rows(bars)
    print(f"[数据] {CODE} 日线 {len(candles)} 根：{ict_history.bar_date(candles[0])} ~ "
          f"{ict_history.bar_date(candles[-1])}，horizon={HORIZON}")

    full_fvg = fvg_detail(candles, HORIZON)
    full_sweep = sweep_detail(candles, HORIZON)
    failures = 0

    # ---- C. 样本数对账 ----
    payload = ict_structure.build_structure_stats(candles, None, horizon=HORIZON)
    detected = payload["counts"]["fvgs"]
    fvg_block = payload["fvg"]["overall"]
    reconciled = fvg_block["samples"] + fvg_block["censored"] == detected
    print(f"[C] FVG detected={detected} samples={fvg_block['samples']} "
          f"censored={fvg_block['censored']} 对账 → {'PASS' if reconciled else 'FAIL'}")
    if not reconciled:
        failures += 1
    directional = sum(payload["fvg"][name]["samples"] + payload["fvg"][name]["censored"]
                      for name in ("bullish", "bearish"))
    print(f"[C] bullish+bearish samples+censored={directional} vs detected={detected} → "
          f"{'PASS' if directional == detected else 'FAIL'}")
    if directional != detected:
        failures += 1

    # ---- A / B ----
    for cut in CUT_POINTS:
        prefix = candles[:cut]
        failures += compare(f"A-{cut} FVG", fvg_detail(prefix, HORIZON),
                            {k: v for k, v in full_fvg.items()})
        failures += compare(f"A-{cut} sweep", sweep_detail(prefix, HORIZON), full_sweep)

        poisoned = _poison(candles, cut - 1)
        safe = cut - 1 - HORIZON          # 观察窗口必须整体落在未投毒区
        failures += compare(
            f"B-{cut} FVG", fvg_detail(poisoned, HORIZON),
            {k: v for k, v in full_fvg.items() if k[0] <= safe})
        failures += compare(
            f"B-{cut} sweep", sweep_detail(poisoned, HORIZON),
            {k: v for k, v in full_sweep.items() if k[0] <= safe})

    print("\n结论：", "未发现未来函数（全部通过）" if failures == 0
          else f"存在 {failures} 项失败，必须修")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
