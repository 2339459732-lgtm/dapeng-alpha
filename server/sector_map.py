"""M3 板块映射（sector_map）读写与反查。

职责：
- 提供 `data/sector_map.json` 的原子落盘 / 读取（读取按 mtime 缓存）
- 提供"股票 → 所属板块列表"与"板块 → 成分股"的反查

约束（团队铁律）：
- 文件不存在时相关函数返回 None，调用方须返回 `null + "映射尚未构建"`，
  **绝不在请求路径里现拉**（构建由 scripts/build_sector_map.py 离线完成）。
- 数据来源必须在文件内自证（`source` / 每板块 `source` / `updated_at` / `failed`）。
"""

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join(_PROJECT_ROOT, "data", "sector_map.json")

_read_cache: Dict[str, Any] = {"mtime": 0.0, "data": None}
_lock = threading.Lock()


def normalize_code(raw: Any) -> str:
    """把裸代码/带前缀代码统一为 `sh600519` / `sz000001` / `bj8xxxxx` 形式。"""
    c = str(raw or "").strip().lower()
    if not c:
        return ""
    if c.startswith(("sh", "sz", "bj")):
        return c
    if c.startswith(("6", "9")):
        return "sh" + c
    if c.startswith(("4", "8")):
        return "bj" + c
    return "sz" + c


def infer_level(name: Any) -> int:
    """按板块名后缀推断层级：Ⅲ→3、Ⅱ→2、Ⅰ→1、无后缀→0（大类/未分级）。

    东财行业板块用全角罗马数字 Ⅰ(U+2160)/Ⅱ(U+2161)/Ⅲ(U+2162)；同时兼容 ASCII
    `I/II/III`（先匹配长串，避免 `III` 被 `II` 误吞）。
    """
    n = str(name or "").strip()
    if not n:
        return 0
    if n.endswith("Ⅲ") or n.endswith("III"):
        return 3
    if n.endswith("Ⅱ") or n.endswith("II"):
        return 2
    if n.endswith("Ⅰ") or n.endswith("I"):
        return 1
    return 0


def atomic_write_json(path: str, payload: Any) -> bool:
    """原子落盘：写 `.tmp` → flush+fsync → os.replace；失败清理临时文件。"""
    tmp = f"{path}.tmp{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("sector_map atomic write failed: %s: %s", type(e).__name__, e)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def load_sector_map() -> Optional[Dict[str, Any]]:
    """读取 `data/sector_map.json`（按 mtime 缓存）。文件不存在/损坏返回 None。"""
    try:
        if not os.path.exists(MAP_PATH):
            return None
        mtime = os.path.getmtime(MAP_PATH)
        with _lock:
            if _read_cache["data"] is not None and _read_cache["mtime"] == mtime:
                return _read_cache["data"]
        with open(MAP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        with _lock:
            _read_cache["mtime"] = mtime
            _read_cache["data"] = data
        return data
    except Exception as e:  # noqa: BLE001
        logger.warning("sector_map read failed: %s: %s", type(e).__name__, e)
        return None


def get_stock_sectors_detailed(code: str) -> Optional[List[Dict[str, Any]]]:
    """反查明细：[{code, name, level}]，按 level 升序（大类 → 细分）。

    层级混排必须显式暴露：同一只票常同时属于"大类(level 0) + Ⅱ(2) + Ⅲ(3)"，
    调用方据此知道是"层级包含"而非"精确并列"。
    """
    data = load_sector_map()
    if data is None:
        return None
    codes = (data.get("stock_index") or {}).get(normalize_code(code), [])
    sectors = data.get("sectors") or {}
    out: List[Dict[str, Any]] = []
    for s in codes:
        entry = sectors.get(s) or {}
        name = entry.get("name")
        level = entry.get("level")
        if level is None:
            level = infer_level(name)
        out.append({"code": s, "name": name, "level": level})
    out.sort(key=lambda x: (x.get("level", 0), x.get("code") or ""))
    return out

