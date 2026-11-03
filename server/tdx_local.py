"""通达信本地盘后数据读取器（vipdoc）
- 适用：电脑安装「通达信金融终端」并下载盘后数据后，/vipdoc/{sh,sz,bj}/lday/*.day、minline/*.lc1|*.lc5
- 优点：完全离线可用，不依赖东财/腾讯接口；适合宿舍断网、热点离开时用历史数据继续分析
- 说明：本模块只读本地文件，不修改通达信目录；未安装时 /api/tdx/status 返回 found=false
"""
from __future__ import annotations

import os
import struct
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_CANDIDATES = [
    r"D:\new_tdx",
    r"D:\通达信",
    r"D:\tdx",
    r"D:\Tdx",
    r"C:\new_tdx",
    r"C:\通达信",
    r"D:\Program Files\new_tdx",
    r"C:\Program Files\new_tdx",
    r"D:\L\new_tdx",
]


def find_home() -> Optional[Path]:
    env = os.environ.get("TDX_HOME") or os.environ.get("TDX_PATH")
    if env:
        p = Path(env)
        if (p / "vipdoc").is_dir():
            return p
    for c in _CANDIDATES:
        p = Path(c)
        if (p / "vipdoc").is_dir():
            return p
    return None


def _market_dir(code: str) -> str:
    c = code.lower()
    if c.startswith("sh"):
        return "sh"
    if c.startswith("sz"):
        return "sz"
    if c.startswith("bj"):
        return "bj"
    # 纯数字按交易所段推断
    if c.startswith(("6", "5", "9")):
        return "sh"
    return "sz"


def _parse_day(raw: bytes) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    n = len(raw) // 32
    for i in range(n):
        rec = raw[i * 32 : (i + 1) * 32]
        d, o, h, l, c, amount, vol, _ = struct.unpack("<IIIIIfII", rec)
        year, month, day = d // 10000, (d // 100) % 100, d % 100
        try:
            dt = date(year, month, day)
        except ValueError:
            continue
        out.append(
            {
                "date": dt.isoformat(),
                "open": round(o / 100, 2),
                "high": round(h / 100, 2),
                "low": round(l / 100, 2),
                "close": round(c / 100, 2),
                "amount": round(float(amount), 2),
                "volume": int(vol),
            }
        )
    return out


def _parse_minute(raw: bytes) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    n = len(raw) // 32
    for i in range(n):
        rec = raw[i * 32 : (i + 1) * 32]
        date_i, time_i, o, h, l, c, amount, vol, _ = struct.unpack("<HHfffffII", rec)
        year = 2004 + (date_i >> 11)
        month = (date_i >> 7) & 0x0F
        day = (date_i >> 2) & 0x1F
        hour = time_i // 60
        minute = time_i % 60
        try:
            dt = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
        except Exception:
            continue
        out.append(
            {
                "date": dt,
                "open": round(o, 3),
                "high": round(h, 3),
                "low": round(l, 3),
                "close": round(c, 3),
                "amount": round(float(amount), 2),
                "volume": int(vol),
            }
        )
    return out


def status() -> Dict[str, Any]:
    home = find_home()
    if not home:
        return {
            "ok": True,
            "found": False,
            "hint": "未检测到通达信 vipdoc 目录。安装通达信金融终端 → 盘后数据下载后，设置环境变量 TDX_HOME 指向安装目录即可。",
            "candidates": _CANDIDATES,
        }
    counts = {}
    latest = None
    for m in ("sh", "sz", "bj"):
        d = home / "vipdoc" / m / "lday"
        files = list(d.glob("*.day")) if d.is_dir() else []
        counts[m] = len(files)
        if files:
            mt = max(f.stat().st_mtime for f in files)
            if latest is None or mt > latest:
                latest = mt
    return {
        "ok": True,
        "found": True,
        "home": str(home),
        "counts": counts,
        "latest_mtime": latest,
    }


def kline(code: str, period: str = "day", limit: int = 250) -> Dict[str, Any]:
    home = find_home()
    if not home:
        return {"ok": False, "error": "tdx_not_found", "hint": "未检测到通达信数据目录，请安装并设置 TDX_HOME"}
    code = code.strip().lower()
    if not code.startswith(("sh", "sz", "bj")):
        code = _market_dir(code or "sz") + code
    market = _market_dir(code)
    if period in ("day", "d", "daily"):
        path = home / "vipdoc" / market / "lday" / f"{code}.day"
        parser = _parse_day
    else:
        ext = "lc1" if period in ("1m", "min1", "1") else "lc5" if period in ("5m", "min5", "5") else "lc1"
        path = home / "vipdoc" / market / "minline" / f"{code}.{ext}"
        parser = _parse_minute
    if not path.is_file():
        return {"ok": False, "error": "file_not_found", "path": str(path), "code": code, "period": period}
    raw = path.read_bytes()
    bars = parser(raw)
    bars = bars[-max(1, min(limit, 5000)) :]
    return {
        "ok": True,
        "code": code,
        "period": period,
        "count": len(bars),
        "source": "tdx_local",
        "path": str(path),
        "bars": bars,
    }
