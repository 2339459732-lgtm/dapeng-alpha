# -*- coding: utf-8 -*-
"""ICT 结构统计快照 —— 用于「前视偏差修复前/后」对比（只读，不写业务表）。

用法（项目根）：
    python scripts\\ict_stats_snapshot.py artifacts\\ict_stats_before_lookahead_fix.json

产出：
- ``detail``：指定 3 只标的的完整 structure-stats 载荷（逐字段可比）
- ``cross_section``：20 只主流标的的 FVG/Sweep 关键指标（判断横截面结论是否成立）
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import ict_model, ict_structure          # noqa: E402
from server.tencent_kline import fetch_tencent_kline  # noqa: E402

DETAIL_CODES = ("sh600519", "sz000858", "sh601318")
CROSS_CODES = (
    "sh600519", "sz000858", "sh600276", "sh601012", "sz300750",
    "sz002594", "sh600036", "sh601318", "sz000001", "sh600030",
    "sh601899", "sz002230", "sh688981", "sz300059", "sz000651",
    "sh601088", "sh600900", "sz002415", "sh603288", "sh600887",
)
LIMIT = 400
HORIZON = 10


async def _payload(code: str) -> Dict[str, Any]:
    bars = await fetch_tencent_kline(code, period="day", limit=LIMIT, timeout=10.0)
    candles = ict_model.parse_kline_rows(bars[-LIMIT:])
    return ict_structure.build_structure_stats(candles, None, horizon=HORIZON)


async def main(out_path: str) -> int:
    detail: Dict[str, Any] = {}
    for code in DETAIL_CODES:
        detail[code] = await _payload(code)
        print(f"[detail] {code} ok")

    cross: List[Dict[str, Any]] = []
    for code in CROSS_CODES:
        data = await _payload(code)
        fvg = data["fvg"]["overall"]
        sweep = data["sweep"]["overall"]
        edge = data["edge"]
        cross.append({
            "code": code,
            "fvg_samples": fvg["samples"],
            "fvg_partial_pct": fvg["partial_fill_rate_pct"],
            "fvg_partial_base_pct": fvg["baseline_partial_pct"],
            "fvg_partial_edge_pp": edge["fvg_partial_fill_vs_baseline_pp"],
            "fvg_full_edge_pp": edge["fvg_full_fill_vs_baseline_pp"],
            "sweep_samples": sweep["samples"],
            "sweep_detected": sweep["detected"],
            "sweep_rate_pct": sweep["reversal_rate_pct"],
            "sweep_base_pct": sweep["baseline_reversal_pct"],
            "sweep_edge_pp": edge["sweep_reversal_vs_baseline_pp"],
        })
        print(f"[cross] {code} sweep_n={sweep['samples']} edge={edge['sweep_reversal_vs_baseline_pp']}")

    payload = {"limit": LIMIT, "horizon": HORIZON,
               "detail": detail, "cross_section": cross}
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 {target}")
    return 0


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "artifacts/ict_stats_snapshot.json"
    raise SystemExit(asyncio.run(main(out)))
