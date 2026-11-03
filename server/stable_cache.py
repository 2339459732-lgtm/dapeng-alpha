"""缓存与磁盘兜底：进程缓存、磁盘读写、陈旧标记"""

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
import requests
from server.sector_map import infer_level

logger = logging.getLogger(__name__)


def _ensure_logging() -> None:
    """确保本模块的 INFO 日志可见。

    uvicorn 未配置 root logger，Python 会退化到 lastResort 处理器（仅 WARNING+），
    导致 logger.info 被静默丢弃。这里为本模块挂一个 stderr handler 并置 INFO，
    使"缓存命中/回放"等 info 级别记录可落盘到 logs/server.log。
    """
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


_ensure_logging()

# 内存缓存
_DATA_CACHE: Dict[str, Dict[str, Any]] = {}

def _get_cache(key: str, ttl: int = 30) -> Optional[Any]:
    if key in _DATA_CACHE:
        item = _DATA_CACHE[key]
        if time.time() - item["ts"] < ttl:
            return item["data"]
    return None

def _set_cache(key: str, data: Any):
    _DATA_CACHE[key] = {"data": data, "ts": time.time()}

# =====================================================================
#  最近一次"真实数据"原子落盘缓存（降级链第二级）
#  - 真实取数成功 → 原子落盘（写临时文件 + os.replace），带 fetch_ts
#  - 取数失败 → 优先回放磁盘里的最近一次真实数据，并逐条标 stale/stale_from
#  - 连磁盘缓存都没有 → 才使用硬编码本地样本
# =====================================================================
_DISK_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache"
)
_DATA_DIR = os.path.dirname(_DISK_CACHE_DIR)   # <项目根>/data


def _num_or_none(val: Any) -> Optional[float]:
    """数值化：非数值/占位符（'-'、'--'、''）→ None（**绝不返回 0 冒充**）。"""
    if val is None:
        return None
    if isinstance(val, str) and val.strip() in ("", "-", "--"):
        return None
    try:
        f = float(val)
        return f if f == f else None
    except (ValueError, TypeError):
        return None


def _load_sector_periods() -> Dict[str, Dict[str, Any]]:
    """读离线构建的 data/sector_periods.json（M3 ③）。缺失返回 {}。"""
    path = os.path.join(_DATA_DIR, "sector_periods.json")
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        periods = payload.get("periods")
        return periods if isinstance(periods, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"sector_periods read failed: {type(e).__name__}: {e}")
        return {}


def _sector_level_map() -> Dict[str, int]:
    """从 data/sector_map.json 读 board code → level（缺失返回 {}）。"""
    try:
        from server.sector_map import load_sector_map
        data = load_sector_map()
        if not data:
            return {}
        out: Dict[str, int] = {}
        for k, v in (data.get("sectors") or {}).items():
            if isinstance(v, dict) and v.get("level") is not None:
                out[k] = v["level"]
        return out
    except Exception:  # noqa: BLE001
        return {}


def _disk_cache_path(key: str) -> str:
    return os.path.join(_DISK_CACHE_DIR, f"{key}_last.json")


def _write_disk_cache(key: str, rows: List[Dict[str, Any]], fetch_ts: str) -> bool:
    """原子落盘最近一次真实数据：先写临时文件再 os.replace。

    任一步失败都不影响主流程（记 warning 并返回 False）；os.replace 在同一
    文件系统上是原子操作，因此不会出现"读到写了一半的文件"。
    """
    if not rows:
        return False
    path = _disk_cache_path(key)
    tmp = f"{path}.tmp{os.getpid()}"
    try:
        os.makedirs(_DISK_CACHE_DIR, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fetch_ts": fetch_ts, "rows": rows}, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # 原子替换
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"disk cache write failed [{key}]: {type(e).__name__}: {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def _read_disk_cache(key: str):
    """读取最近一次真实数据，返回 (rows, fetch_ts)；无则 (None, None)。"""
    path = _disk_cache_path(key)
    try:
        if not os.path.exists(path):
            return None, None
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload.get("rows")
        ts = payload.get("fetch_ts")
        if isinstance(rows, list) and rows:
            return rows, ts
    except Exception as e:  # noqa: BLE001
        logger.warning(f"disk cache read failed [{key}]: {type(e).__name__}: {e}")
    return None, None


def _mark_stale(rows: List[Dict[str, Any]], source_base: str, ts: str) -> List[Dict[str, Any]]:
    """把一份真实数据标成"历史快照"：逐条 stale=True + stale_from=ts，来源带时间戳。"""
    out: List[Dict[str, Any]] = []
    stamped = f"{source_base}（历史快照 {ts}）"
    for r in rows:
        rr = dict(r) if isinstance(r, dict) else {"value": r}
        rr["synthetic"] = False
        rr["stale"] = True
        rr["stale_from"] = ts
        rr["source"] = stamped
        out.append(rr)
    return out
