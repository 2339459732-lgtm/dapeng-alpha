# -*- coding: utf-8 -*-
"""`/api/ict/scan` 是否污染共享 K 线缓存 `raw_kline:*` 的确定性探针。

背景：`/api/kline` 默认根数直接读 `raw_kline:{code}:{period}`（见
`server/routers/kline.py`）。若 ICT scan 把自己取到的更long序列写回该键，
就会把全站 K 线的默认根数顶大——跨模块污染。

为什么用进程内探针而不是纯 HTTP：线上有全市场预热，池内标的的 `raw_kline`
几分钟内就被捂热（121 根），scan 走「缓存命中」分支不会覆写，污染被掩盖；
只有冷标的（池外/临时查询）才会中招。探针在独立进程里用空缓存复现该路径，
确定性、可重复。

判定：``POLLUTED`` = raw_kline 被写入（缺陷存在）；``CLEAN`` = 未写回。

用法（项目根）：
    python scripts/verify_ict_scan_cache_isolation.py [code]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.cache import cache                       # noqa: E402
from server.routers import ict as ict_router         # noqa: E402

CODE = (sys.argv[1] if len(sys.argv) > 1 else "sz000002").lower()
LIMIT = 800


def _len(value) -> object:
    return len(value) if isinstance(value, (list, tuple)) else None


async def _probe(kind: str) -> bool:
    """跑一种用途，返回是否污染共享键。"""
    loader = (ict_router._load_klines if kind == "scan"
              else ict_router._load_history_klines)          # noqa: SLF001
    cache.clear()
    bars = await loader(CODE, "day", LIMIT)
    raw = cache.get(f"raw_kline:{CODE}:day")
    own = cache.get(f"ict_{kind}_kline:{CODE}:day:{LIMIT}")
    print(f"[{kind}] 返回 {len(bars)} 根 | raw_kline → {_len(raw)} 根 | "
          f"ict_{kind}_kline → {_len(own)} 根")
    return bool(raw)


async def main() -> int:
    print(f"[探针] code={CODE} limit={LIMIT}，每组前清空缓存（模拟冷标的）")
    polluted_scan = await _probe("scan")
    polluted_hist = await _probe("hist")
    print()

    if polluted_scan or polluted_hist:
        which = "、".join(k for k, v in (("scan", polluted_scan), ("hist", polluted_hist)) if v)
        print(f"结论：POLLUTED —— {which} 写入了 raw_kline，"
              f"/api/kline 默认根数会被顶大")
        return 1
    print("结论：CLEAN —— scan / history/structure-stats 均未写 raw_kline"
          "（只读共享缓存，结果落独立键）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
