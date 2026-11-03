# -*- coding: utf-8 -*-
"""ICT 参数运行期覆盖的验证辅助脚本（走 store API，不直连 sqlite）。

用法:
    python tools/ict_kv_set.py set '{"swing_width": 2}'
    python tools/ict_kv_set.py del
    python tools/ict_kv_set.py get
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.trading.store import TradingStore  # noqa: E402

KEY = "ict_config"


async def _run(action: str, payload: str) -> None:
    store = TradingStore()
    await store.init()
    try:
        if action == "set":
            value = json.loads(payload)
            await store.kv_set(KEY, value)
            print(f"[set] {KEY} = {json.dumps(value, ensure_ascii=False)}")
        elif action == "del":
            # 无 kv_delete：写空字典等价于「无覆盖」（_load_params 只合并存在的键）
            await store.kv_set(KEY, {})
            print(f"[del] {KEY} = {{}}（清空覆盖）")
        print(f"[get] {KEY} = {json.dumps(await store.kv_get(KEY, None), ensure_ascii=False)}")
    finally:
        await store.close()


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "get"
    payload = sys.argv[2] if len(sys.argv) > 2 else "{}"
    asyncio.run(_run(action, payload))
