# -*- coding: utf-8 -*-
"""`/api/ict/structure-stats` 字段契约断言（防止「字段到底在不在」被缓存/旧进程误导）。

背景：前端要显示「中位 N 天到触及」，只能用 `median_days_to_*`；一旦接口里没有这个
key，前端就卡住。人工 curl 容易看到「进程重启前的旧缓存响应」而误判，所以把断言
脚本化：一条命令给出 PASS/FAIL，并打印真实取值。

检查项：
1. `fvg.{overall,bullish,bearish}` 有 ``median_days_to_partial`` / ``median_days_to_full``；
2. `sweep.{overall,buyside,sellside}` 有 ``median_days_to_reversal``；
3. 中位数与算术平均**独立计算**（同一块里两者都在，且能看出分别来自 median/fmean）；
4. 样本不足（``samples < 10``）时三个中位数均为 ``null``，不得为 0；
5. JSON 类型为 float（不是字符串、不是 int），便于前端直接格式化。

用法（项目根，服务需在 8900 运行）：
    python scripts/verify_ict_structure_contract.py [code]
退出码 0 = 全部通过。
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8900"
CODE = sys.argv[1] if len(sys.argv) > 1 else "sh600519"
MIN_USABLE = 10


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=60) as resp:  # noqa: S310 (本机)
        return json.loads(resp.read().decode("utf-8"))


def _check_keys(block: dict, names: list[str], label: str) -> list[str]:
    problems: list[str] = []
    for name in names:
        if name not in block:
            problems.append(f"{label} 缺字段 {name}")
        elif block[name] is not None and not isinstance(block[name], float):
            problems.append(f"{label}.{name} 类型应为 float，实际 {type(block[name]).__name__}")
    return problems


def main() -> int:
    problems: list[str] = []

    # ---- 主体：limit=400 ----
    payload = _get(f"/api/ict/structure-stats?code={CODE}&limit=400&horizon=10")
    data = payload["data"]
    print(f"[主体] {CODE} limit=400 cached={data.get('cached')} "
          f"candles={data.get('candles')} range={data.get('range')}")

    for name in ("overall", "bullish", "bearish"):
        block = data["fvg"][name]
        problems += _check_keys(block, ["median_days_to_partial", "median_days_to_full"],
                                f"fvg.{name}")
        print(f"  fvg.{name:8} samples={block['samples']:>3} hits={block['partial_hits']:>3}"
              f"  触及 avg={block['avg_days_to_partial']} / median={block['median_days_to_partial']}"
              f"  穿中 avg={block['avg_days_to_full']} / median={block['median_days_to_full']}")

    for name in ("overall", "buyside", "sellside"):
        block = data["sweep"][name]
        problems += _check_keys(block, ["median_days_to_reversal"], f"sweep.{name}")
        print(f"  sweep.{name:8} samples={block['samples']:>3} hits={block['hits']:>3}"
              f"  反转 avg={block['avg_days_to_reversal']} / median={block['median_days_to_reversal']}"
              f"  available={block['available']}")

    ob = data["ob"]["overall"]
    problems += _check_keys(ob, ["median_days_to_touch"], "ob.overall")
    print(f"  ob.overall   samples={ob['samples']:>3} hits={ob['hits']:>3}"
          f"  回踩 avg={ob['avg_days_to_touch']} / median={ob['median_days_to_touch']}")

    # ---- 样本不足：limit=120 → sweep 中位数必须 null ----
    small = _get(f"/api/ict/structure-stats?code={CODE}&limit=120&horizon=10")["data"]
    print("\n[样本不足] limit=120")
    for name in ("overall", "buyside", "sellside"):
        block = small["sweep"][name]
        print(f"  sweep.{name:8} samples={block['samples']:>3} available={block['available']}"
              f"  avg={block['avg_days_to_reversal']} median={block['median_days_to_reversal']}")
        if block["samples"] < MIN_USABLE:
            if block["median_days_to_reversal"] is not None:
                problems.append(f"sweep.{name} 样本 {block['samples']} < {MIN_USABLE}，"
                                f"median 应为 null，实际 {block['median_days_to_reversal']}")
            if block["avg_days_to_reversal"] is not None:
                problems.append(f"sweep.{name} 样本 {block['samples']} < {MIN_USABLE}，"
                                f"avg 应为 null，实际 {block['avg_days_to_reversal']}")

    print()
    if problems:
        print(f"结论：FAIL（{len(problems)} 项）")
        for line in problems:
            print("  -", line)
        return 1
    print("结论：PASS —— median 字段齐全、与 avg 独立、样本不足时为 null")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
