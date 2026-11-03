"""构建 data/sector_map.json（M3）。

独立离线脚本（非请求路径）：
  拉东财行业板块全量清单 → 逐个取成分股 → 组装 板块索引 + 股票反查索引 → 原子落盘。

用法：
  python scripts/build_sector_map.py [--force] [--ttl-hours 24] [--concurrency 5]

特性：
  - 并发 4~6；单板块失败重试一次；部分成功也落盘（失败进 `failed` 带 reason）
  - 每 25 个板块打印一行进度
  - 幂等：已有且未过期（--ttl-hours 内）的板块默认跳过；--force 全量重建
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

from server.sector_map import (  # noqa: E402
    MAP_PATH,
    atomic_write_json,
    infer_level,
    load_sector_map,
    normalize_code,
)

_NODES = [
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
_SOURCE = "eastmoney_push2_clist"


def _em_clist(params: dict, timeout: float = 10.0, attempts: int = 4) -> dict:
    """东财 push2 clist 多节点容灾取数（含瞬时失败重试退避）。"""
    qs = urllib.parse.urlencode(params)
    last = "unknown"
    for att in range(attempts):
        for node in _NODES:
            try:
                req = urllib.request.Request(f"{node}/api/qt/clist/get?{qs}", headers=_HEADERS)
                with _OPENER.open(req, timeout=timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8", "ignore"))
                data = payload.get("data")
                if isinstance(data, dict) and data.get("diff") is not None:
                    return payload
                last = "no_diff"
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}:{str(e)[:60]}"
        time.sleep(1.0 + att)
    raise RuntimeError(last)


_PAGE_SIZE = 100          # 东财 clist 单页上限（实测 pz>100 会被截到 100）
_MAX_PAGES = 30           # 安全上限


def fetch_board_list() -> dict:
    """东财行业板块全量清单 {BKcode: name}（分页，pz 上限 100）。"""
    out: dict = {}
    total = None
    pn = 1
    while pn <= _MAX_PAGES:
        try:
            payload = _em_clist({
                "pn": str(pn), "pz": str(_PAGE_SIZE), "po": "1", "np": "1",
                "fltt": "2", "invt": "2", "fid": "f12",  # 按代码稳定排序，避免翻页错位
                "fs": "m:90+t:2+f:!50", "fields": "f12,f14",
            })
        except Exception:  # noqa: BLE001  末页/瞬时失败 → 结束分页，保留已收集
            break
        data = payload.get("data") or {}
        if total is None:
            total = data.get("total")
        rows = data.get("diff") or []
        if not rows:
            break
        for row in rows:
            code = row.get("f12")
            if code:
                out[str(code)] = str(row.get("f14") or "")
        if len(rows) < _PAGE_SIZE or (total and len(out) >= int(total)):
            break
        pn += 1
    return out


def fetch_constituents(bk_code: str, retries: int = 1) -> list:
    """板块成分股（带前缀代码列表，分页）。失败重试 retries 次后抛错。"""
    last_exc = None
    for _attempt in range(retries + 1):
        try:
            codes: list = []
            seen = set()
            total = None
            pn = 1
            while pn <= _MAX_PAGES:
                payload = _em_clist({
                    "pn": str(pn), "pz": str(_PAGE_SIZE), "po": "1", "np": "1",
                    "fltt": "2", "invt": "2", "fid": "f12",  # 稳定排序
                    "fs": f"b:{bk_code}+f:!50", "fields": "f12,f14",
                })
                data = payload.get("data") or {}
                if total is None:
                    total = data.get("total")
                rows = data.get("diff") or []
                if not rows:
                    break
                for r in rows:
                    code = normalize_code(r.get("f12"))
                    if code and code not in seen:
                        seen.add(code)
                        codes.append(code)
                if len(rows) < _PAGE_SIZE or (total and len(codes) >= int(total)):
                    break
                pn += 1
            return codes
        except Exception as e:  # noqa: BLE001
            last_exc = e
            time.sleep(1.0)
    raise last_exc


def _is_fresh(entry: dict, now: float, ttl_hours: float) -> bool:
    try:
        ts = datetime.strptime(entry.get("updated_at", ""), "%Y-%m-%d %H:%M:%S").timestamp()
        return (now - ts) < ttl_hours * 3600
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="构建 data/sector_map.json")
    ap.add_argument("--force", action="store_true", help="全量重建（忽略已有缓存）")
    ap.add_argument("--ttl-hours", type=float, default=24.0, help="已有板块的复用有效期")
    ap.add_argument("--concurrency", type=int, default=5, help="并发度（建议 4~6）")
    args = ap.parse_args()

    t0 = time.time()
    print("[1/3] 拉取东财行业板块清单 ...", flush=True)
    try:
        boards = fetch_board_list()
    except Exception as e:  # noqa: BLE001
        print("❌ 拉取板块清单失败: %s" % e)
        return 2
    total = len(boards)
    print("      板块清单: %d 个 (source=%s)" % (total, _SOURCE), flush=True)

    existing = load_sector_map() or {}
    existing_sectors = existing.get("sectors") or {}
    now = time.time()
    pending = {
        bk: nm for bk, nm in boards.items()
        if args.force or bk not in existing_sectors or not _is_fresh(existing_sectors[bk], now, args.ttl_hours)
    }
    skipped = total - len(pending)
    print("[2/3] 待构建 %d，跳过(已有未过期) %d，并发 %d" % (
        len(pending), skipped, args.concurrency), flush=True)

    sectors = dict(existing_sectors)
    failed = []
    done = 0
    lock = threading.Lock()

    def _work(item):
        bk, nm = item
        try:
            return bk, nm, fetch_constituents(bk), None
        except Exception as e:  # noqa: BLE001
            return bk, nm, None, f"{type(e).__name__}: {str(e)[:80]}"

    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        futures = {ex.submit(_work, it): it for it in pending.items()}
        for fut in as_completed(futures):
            bk, nm, cons, err = fut.result()
            with lock:
                done += 1
                if err:
                    failed.append({"code": bk, "reason": err})
                else:
                    sectors[bk] = {"name": nm, "constituents": cons,
                                   "level": infer_level(nm),
                                   "source": _SOURCE, "updated_at": ts_str}
                if done % 25 == 0 or done == len(pending):
                    print("      进度 %d/%d  失败 %d  (%.0fs)" % (
                        done, len(pending), len(failed), time.time() - t0), flush=True)

    # 反向索引：股票 → [板块...]
    print("[3/4] 组装反查索引 ...", flush=True)
    stock_index: dict = {}
    for bk, entry in sectors.items():
        for c in entry.get("constituents") or []:
            stock_index.setdefault(c, [])
            if bk not in stock_index[c]:
                stock_index[c].append(bk)

    # 统一补齐/刷新 level（对"跳过复用"的旧条目同样生效，保证落盘即带 level）
    print("[4/4] 写入 level 并原子落盘 ...", flush=True)
    for _bk, entry in sectors.items():
        entry["level"] = infer_level(entry.get("name"))

    available = len([bk for bk in boards if bk in sectors])
    payload = {
        "updated_at": ts_str,
        "source": _SOURCE,
        "coverage": {"available": available, "total": total},
        "sectors": sectors,
        "stock_index": stock_index,
        "failed": failed,
    }
    ok = atomic_write_json(MAP_PATH, payload)
    if not ok:
        print("❌ 落盘失败")
        return 3
    print("✅ 落盘 %s" % MAP_PATH, flush=True)
    print("   板块=%d 覆盖=%d/%d 股票索引=%d 失败=%d 耗时=%.1fs" % (
        len(sectors), available, total, len(stock_index), len(failed), time.time() - t0), flush=True)
    if failed:
        print("   failed 前 5 条:")
        for f in failed[:5]:
            print("     - %s: %s" % (f["code"], f["reason"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
