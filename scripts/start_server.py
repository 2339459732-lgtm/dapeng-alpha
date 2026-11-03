#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通用启动入口：读取 PORT 环境变量并监听 0.0.0.0。

为什么需要它：多数托管平台（含本项目的部署沙箱）只注入 `PORT`，不会替你展开
shell 变量。把 `--port $PORT` 写进启动命令，遇到不经过 shell 的执行器就会变成
`Option '--port' requires an argument`。这里用 Python 读环境变量，跨平台一致。

用法：
    PORT=8900 python scripts/start_server.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 以脚本所在位置的上一级为项目根：无论从仓库根还是 deploy/ 下启动，
# 都能 import 到 server 包（不依赖当前工作目录）。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        import uvicorn
    except ImportError:
        print("缺少 uvicorn，请先 pip install -r requirements.txt", file=sys.stderr)
        return 1

    port = int((os.getenv("PORT") or "8900").strip() or 8900)
    host = (os.getenv("BIND_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    print(f"[start] uvicorn server.main:app on {host}:{port}", flush=True)
    uvicorn.run("server.main:app", host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
