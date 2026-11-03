"""滚动文件日志（护城河 · 观测层）

- 写 logs/app.log（10 MiB × 5 份）
- 只挂一个文件 handler，避免 uvicorn reload / 多入口重复写
- MOAT_LOG=0 可关闭
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from server.infra.settings import get_settings

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    s = get_settings()
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO) if isinstance(level, str) else level)

    if s.log_to_file:
        try:
            s.logs_dir.mkdir(parents=True, exist_ok=True)
            fmt = logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            fh = RotatingFileHandler(
                s.logs_dir / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            fh.setFormatter(fmt)
            fh.setLevel(root.level)
            root.addHandler(fh)
        except OSError:
            pass

    _CONFIGURED = True
