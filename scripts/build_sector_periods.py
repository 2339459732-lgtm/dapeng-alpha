"""构建 data/sector_periods.json（M3 ③）：板块指数日K → 5/20/60 日收益。

独立离线脚本（非请求路径）。请求路径只读本文件，缺失 → null + period_returns_cache_missing。

口径：`d_n = close[-1] / close[-1-n] - 1`（即"今日收盘 / n 个交易日前收盘 - 1"，含当日）。
      d20 用 `close[t]/close[t-20]-1`，即 Python 索引 `close[-1]/close[-21]-1`。

用法：
  python scripts/build_sector_periods.py [--force] [--ttl-hours 6] [--concurrency 5] [--dump BK1305]
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.sector_map import atomic_write_json, load_sector_map  # noqa: E402

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERIODS_PATH = os.path.join(_PROJECT_ROOT, "data", "sector_periods.json")
RETURNS_PATH = os.path.join(_PROJECT_ROOT, "data", "sector_returns.json")

_KLINE_HOST = "https://push2his.eastmoney.com"
_KLINE_PATH = "/api/qt/stock/kline/get"
_PUSH2_NODES = [
    "https://push2.eastmoney.com",
    "https://82.push2.eastmoney.com",
    "https://90.push2.eastmoney.com",
]
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_SOURCE = "eastmoney_kline"
_KLINE_LIMIT = 70            # 需覆盖 60 日：>=61 根
_PAGE_SIZE = 100


def _board_list() -> dict:
    """板块清单 {BKcode: name}：优先读 sector_map.json，缺失时在线分页兜底。"""
    sm = load_sector_map()
    if sm and sm.get("sectors"):
        return {k: (v.get("name") or "") for k, v in sm["sectors"].items()}
    out, total, pn = {}, None, 1
    while pn <= 8:
        qs = urllib.parse.urlencode({
            "pn": str(pn), "pz": str(_PAGE_SIZE), "po": "1", "np": "1",
            "fltt": "2", "invt": "2", "fid": "f12", "fs": "m:90+t:2+f:!50",
            "fields": "f12,f14",
        })
        payload = None
        for node in _PUSH2_NODES:
            try:
                with _OPENER.open(urllib.request.Request(f"{node}/api/qt/clist/get?{qs}",
                                                        headers=_HEADERS), timeout=10) as r:
                    payload = json.loads(r.read().decode("utf-8", "ignore"))
                break
            except Exception:  # noqa: BLE001
                payload = None
        obj = (payload or {}).get("data") if isinstance(payload, dict) else None
        rows = obj.get("diff") if isinstance(obj, dict) else None
        if not rows:
            break
        if total is None:
            total = obj.get("total")
        for row in rows:
            if row.get("f12"):
                out[str(row["f12"])] = str(row.get("f14") or "")
        if len(rows) < _PAGE_SIZE or (total and len(out) >= int(total)):
            break
        pn += 1
    return out


def fetch_board_closes(bk_code: str, retries: int = 1):
    """拉板块指数日K → (klines, closes)。失败重试 retries 次后抛错。"""
    qs = urllib.parse.urlencode({
        "secid": f"90.{bk_code}", "klt": "101", "fqt": "1", "lmt": str(_KLINE_LIMIT),
        "fields1": "f1,f2", "fields2": "f51,f53", "end": "20500101",
    })
    last_exc = None
    for _attempt in range(retries + 1):
        try:
            with _OPENER.open(urllib.request.Request(f"{_KLINE_HOST}{_KLINE_PATH}?{qs}",
                                                     headers=_HEADERS), timeout=10) as r:
                payload = json.loads(r.read().decode("utf-8", "ignore"))
            klines = (payload.get("data") or {}).get("klines") or []
            if not klines:
                raise RuntimeError("no_klines")
            closes = []
            for line in klines:
                parts = str(line).split(",")
                if len(parts) >= 2:
                    try:
                        closes.append(float(parts[1]))
                    except (ValueError, TypeError):
                        pass
            return klines, closes
        except Exception as e:  # noqa: BLE001
            last_exc = e
            time.sleep(1.0)
    raise last_exc


def calc_periods(closes):
    """d_n = close[-1]/close[-1-n]-1（%）。不足 n+1 根 → None（绝不推算）。"""
    out = {}
    for n in (5, 20, 60):
        if len(closes) > n and closes[-1 - n]:
            out["d%d" % n] = round((closes[-1] / closes[-1 - n] - 1.0) * 100.0, 4)
        else:
            out["d%d" % n] = None
    return out


def _is_fresh(entry: dict, now: float, ttl_hours: float) -> bool:
    try:
        ts = datetime.strptime(entry.get("updated_at", ""), "%Y-%m-%d %H:%M:%S").timestamp()
        return (now - ts) < ttl_hours * 3600
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="构建 data/sector_periods.json")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--ttl-hours", type=float, default=6.0)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--dump", default=None, help="打印某板块日线收盘序列与收益（用于复算核对）")
    args = ap.parse_args()

    if args.dump:
        klines, closes = fetch_board_closes(args.dump)
        print("板块 %s  日线 %d 根  首=%s  末=%s" % (args.dump, len(closes), klines[0], klines[-1]))
        print("closes =", json.dumps(closes))
        print("periods =", json.dumps(calc_periods(closes)))
        return 0

    t0 = time.time()
    print("[1/3] 读取板块清单 ...", flush=True)
    boards = _board_list()
    total = len(boards)
    print("      板块 %d 个" % total, flush=True)
    if not total:
        print("❌ 无板块清单")
        return 2

    existing = {}
    if os.path.exists(PERIODS_PATH):
        try:
            with open(PERIODS_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f).get("periods") or {}
        except Exception:  # noqa: BLE001
            existing = {}
    now = time.time()
    pending = {bk: nm for bk, nm in boards.items()
               if args.force or bk not in existing or not _is_fresh(existing[bk], now, args.ttl_hours)}
    print("[2/3] 待构建 %d，跳过(已有未过期) %d，并发 %d" % (
        len(pending), total - len(pending), args.concurrency), flush=True)

    periods = dict(existing)
    failed = []
    done = 0
    lock = threading.Lock()

    def _work(item):
        bk = item
        try:
            klines, closes = fetch_board_closes(bk)
            dates = [str(k).split(",")[0] for k in klines]
            res = calc_periods(closes)
            res["source"] = _SOURCE
            res["bars_n"] = len(closes)
            res["last_date"] = dates[-1] if dates else None
            res["updated_at"] = ts_str
            rets = {}
            for i in range(1, len(closes)):
                if closes[i - 1]:
                    rets[dates[i]] = round(closes[i] / closes[i - 1] - 1.0, 6)
            return bk, res, dates, rets, None
        except Exception as e:  # noqa: BLE001
            return bk, None, None, None, f"{type(e).__name__}: {str(e)[:80]}"

    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    series_raw = {}
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        futures = {ex.submit(_work, bk): bk for bk in pending}
        for fut in as_completed(futures):
            bk, res, dates, rets, err = fut.result()
            with lock:
                done += 1
                if err:
                    failed.append({"code": bk, "reason": err})
                else:
                    periods[bk] = res
                    series_raw[bk] = rets
                if done % 25 == 0 or done == len(pending):
                    print("      进度 %d/%d  失败 %d  (%.0fs)" % (
                        done, len(pending), len(failed), time.time() - t0), flush=True)

    available = len([bk for bk in boards if bk in periods])
    payload = {
        "updated_at": ts_str,
        "source": _SOURCE,
        "period_method": "d_n = close[-1]/close[-1-n]-1 (含当日)",
        "coverage": {"available": available, "total": total},
        "periods": periods,
        "failed": failed,
    }
    print("[3/4] 原子落盘 sector_periods.json ...", flush=True)
    if not atomic_write_json(PERIODS_PATH, payload):
        print("❌ 落盘失败")
        return 3

    # 日收益序列（共享日期轴 + 逐板块对齐），供 ⑥ 金融属性敏感度使用
    print("[4/4] 原子落盘 sector_returns.json ...", flush=True)
    if series_raw:
        axis = sorted({d for rets in series_raw.values() for d in rets})
        series = {bk: [rets.get(d) for d in axis] for bk, rets in series_raw.items()}
    else:
        axis, series = [], {}
    returns_payload = {
        "updated_at": ts_str,
        "source": _SOURCE,
        "return_method": "ret[d] = close[d]/close[d-1]-1",
        "dates": axis,
        "series": series,
        "coverage": {"available": len(series), "total": total},
    }
    atomic_write_json(RETURNS_PATH, returns_payload)

    print("✅ 落盘 %s" % PERIODS_PATH, flush=True)
    print("   覆盖=%d/%d 失败=%d 耗时=%.1fs" % (available, total, len(failed), time.time() - t0), flush=True)
    print("   日收益序列: 板块=%d 日期轴=%d 根 -> %s" % (len(series), len(axis), RETURNS_PATH), flush=True)
    if failed:
        print("   failed 前 5 条:")
        for f in failed[:5]:
            print("     - %s: %s" % (f["code"], f["reason"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
