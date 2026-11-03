"""运营看板：总览、用户、订单、系统状态与健康、营收趋势、板块热力"""

import csv
import io
import json
import logging
import os
import platform
import psutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
import hmac
from server.cache import cache
from server.config import SECTORS, DB_DIR, PRICING
from server.payment.service import _load_orders, OrderStatus

from server.routers.admin_common import (
    USERS_FILE,
    _add_log,
    _load_users,
    logger,
    require_admin_access,
    sample_process_cpu_percent,
)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_access)],
)


@router.get("/dashboard")
async def admin_dashboard():
    """后台仪表盘概览数据"""
    # 用户统计
    users = _load_users()
    total_users = len(users)
    active_users = len([u for u in users.values() if u.get("status") == "active"])
    pro_users = len([u for u in users.values() if u.get("plan") in ("pro", "premium")])

    # 订单统计
    orders = _load_orders()
    total_orders = len(orders)
    paid_orders = [o for o in orders.values() if o.get("status") == OrderStatus.PAID.value]
    pending_orders = [o for o in orders.values() if o.get("status") == OrderStatus.PENDING.value]
    total_revenue_fen = sum(o.get("amount_fen", 0) for o in paid_orders)

    # 今日数据
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_orders = [o for o in orders.values() if o.get("created_at", "").startswith(today)]
    today_revenue = sum(o.get("amount_fen", 0) for o in today_orders if o.get("status") == OrderStatus.PAID.value)

    # 板块统计
    sector_stats = []
    for key, val in SECTORS.items():
        sector_stats.append({
            "key": key,
            "name": val["name"],
            "icon": val["icon"],
            "stock_count": len(val["stocks"]),
        })

    # 系统信息
    process = psutil.Process(os.getpid())
    system_info = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
        "memory_used_pct": psutil.virtual_memory().percent,
        "process_memory_mb": round(process.memory_info().rss / (1024**2), 1),
        "uptime_seconds": round((datetime.now() - datetime.fromtimestamp(process.create_time())).total_seconds()),
        "cache_size": cache.size,
    }

    return {
        "ok": True,
        "data": {
            "users": {
                "total": total_users,
                "active": active_users,
                "pro": pro_users,
            },
            "orders": {
                "total": total_orders,
                "paid": len(paid_orders),
                "pending": len(pending_orders),
                "total_revenue_fen": total_revenue_fen,
                "total_revenue_yuan": round(total_revenue_fen / 100, 2),
                "today_orders": len(today_orders),
                "today_revenue_fen": today_revenue,
                "today_revenue_yuan": round(today_revenue / 100, 2),
            },
            "sectors": {
                "total": len(SECTORS),
                "total_stocks": sum(len(v["stocks"]) for v in SECTORS.values()),
                "list": sector_stats[:10],  # 前10个
            },
            "system": system_info,
        },
    }


# ===== 用户管理 =====

@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    plan: Optional[str] = Query(None),
):
    """用户列表（分页+筛选）"""
    users = _load_users()
    items = list(users.values())

    if status:
        items = [u for u in items if u.get("status") == status]
    if plan:
        items = [u for u in items if u.get("plan") == plan]

    total = len(items)
    start = (page - 1) * size
    end = start + size

    def _mask_phone(value: Any) -> str:
        """列表接口不回传完整手机号：号码属于个人信息，列表页只需要能核对。
        需要完整号码时走本机运维（直接读 data/users.json），不经 HTTP。"""
        text = str(value or "")
        if len(text) < 7:
            return "***" if text else ""
        return f"{text[:3]}****{text[-4:]}"

    page_items = []
    for item in items[start:end]:
        row = dict(item)
        if "guardian_phone" in row:
            row["guardian_phone"] = _mask_phone(row.get("guardian_phone"))
        page_items.append(row)

    return {
        "ok": True,
        "data": {
            "total": total,
            "page": page,
            "size": size,
            "items": page_items,
        },
    }


@router.get("/users/{user_id}")
async def get_user(user_id: str):
    """获取单个用户详情"""
    users = _load_users()
    user = users.get(user_id)
    if not user:
        return {"ok": False, "message": "用户不存在"}
    return {"ok": True, "data": user}


@router.post("/users/{user_id}/toggle")
async def toggle_user_status(user_id: str):
    """启用/禁用用户"""
    users = _load_users()
    user = users.get(user_id)
    if not user:
        return {"ok": False, "message": "用户不存在"}

    user["status"] = "inactive" if user.get("status") == "active" else "active"
    users[user_id] = user
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")

    _add_log("USER_TOGGLE", f"用户 {user.get('guardian_name', user_id)} 状态变更为 {user['status']}", "admin")

    return {"ok": True, "data": {"user_id": user_id, "status": user["status"]}}


# ===== 订单管理 =====

@router.get("/orders")
async def list_orders(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    gateway: Optional[str] = Query(None),
):
    """订单列表（分页+筛选）"""
    orders = _load_orders()
    items = list(orders.values())

    # 按创建时间倒序
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    if status:
        items = [o for o in items if o.get("status") == status]
    if gateway:
        items = [o for o in items if o.get("gateway") == gateway]

    total = len(items)
    start = (page - 1) * size
    end = start + size

    return {
        "ok": True,
        "data": {
            "total": total,
            "page": page,
            "size": size,
            "items": items[start:end],
        },
    }


@router.post("/orders/{order_id}/refund")
async def refund_order(order_id: str):
    """退款订单"""
    orders = _load_orders()
    order = orders.get(order_id)
    if not order:
        return {"ok": False, "message": "订单不存在"}

    if order.get("status") != OrderStatus.PAID.value:
        return {"ok": False, "message": "只能退款已支付的订单"}

    order["status"] = OrderStatus.REFUNDED.value
    order["refunded_at"] = datetime.now(timezone.utc).isoformat()
    orders[order_id] = order
    from server.payment.service import _save_orders
    _save_orders(orders)

    _add_log("ORDER_REFUND", f"订单 {order_id} 已退款 {order.get('amount_fen', 0)/100:.2f}元", "admin")

    return {"ok": True, "data": {"order_id": order_id, "status": "refunded"}}


# ===== 系统监控 =====

@router.get("/system")
async def system_status():
    """系统运行状态"""
    process = psutil.Process(os.getpid())

    # CPU：非阻塞采样（interval=None + 时间闸），基线未建立时返回 None
    # 旧实现 process.cpu_percent(interval=0.5) 会阻塞事件循环 500ms 且可能返回假 0
    cpu_pct = sample_process_cpu_percent()

    # 内存
    mem_info = process.memory_info()
    vm = psutil.virtual_memory()

    # 磁盘：Windows 使用系统盘，其他系统使用根目录
    disk_path = os.path.abspath(os.sep)
    if os.name == "nt":
        disk_path = os.environ.get("SystemDrive", "C:") + os.sep
    disk = psutil.disk_usage(disk_path)

    # 网络
    try:
        net = psutil.net_io_counters()
        net_info = {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
        }
    except Exception:
        net_info = None

    return {
        "ok": True,
        "data": {
            "cpu_percent": cpu_pct,
            "cpu_scope": "process",  # 口径声明：本进程占用（非整机），前端不得当整机 CPU 显示
            "cpu_reason": None if cpu_pct is not None else "CPU 采样基线尚未建立（首次调用），下次刷新可得",
            "memory": {
                "process_rss_mb": round(mem_info.rss / (1024**2), 1),
                "process_vms_mb": round(mem_info.vms / (1024**2), 1),
                "system_total_gb": round(vm.total / (1024**3), 1),
                "system_used_pct": vm.percent,
                "system_available_gb": round(vm.available / (1024**3), 1),
            },
            "disk": {
                "total_gb": round(disk.total / (1024**3), 1),
                "used_gb": round(disk.used / (1024**3), 1),
                "free_gb": round(disk.free / (1024**3), 1),
                "used_pct": disk.percent,
            },
            "network": net_info,
            "cache": {
                "size": cache.size,
            },
            "process": {
                "pid": os.getpid(),
                "threads": process.num_threads(),
                "create_time": datetime.fromtimestamp(process.create_time()).isoformat(),
            },
        },
    }


# ===== 系统健康总览（本轮升级模块的状态：新鲜度 / 产物 / 交易引擎 / 推送 / 功耗与缓存）=====


def _read_json_meta(path: Path) -> tuple:
    """读取 JSON 产物 → (数据, 错误原因)。失败时返回 (None, reason)，绝不伪造。"""
    try:
        if not path.exists():
            return None, f"文件不存在：{path.name}"
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        return None, f"读取失败：{type(e).__name__} {str(e)[:120]}"


def _file_stat(path: Path) -> dict:
    """文件 mtime / 大小（不存在则 null + reason）"""
    try:
        st = path.stat()
        return {
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "size_bytes": st.st_size,
        }
    except Exception:
        return {"mtime": None, "size_bytes": None}


@router.get("/system-health")
async def system_health():
    """系统健康总览：把本轮升级新增模块的**真实状态**汇总给后台。

    每块都带来源与时间戳；取不到一律 null + reason，绝不填 0 或假数据。
    """
    data_dir = Path(DB_DIR)

    # ---------- ① 外部因子数据新鲜度（M2 全球传导）----------
    ext_cache = data_dir / "cache"
    factors = []
    for sym in ("csi300", "hsi", "hstech", "djia", "ixic", "ndx", "spx", "vix"):
        p = ext_cache / f"global_hist_{sym}_last.json"
        payload, err = _read_json_meta(p)
        if payload is None:
            factors.append({"symbol": sym, "last_date": None, "fetch_ts": None, "rows": None,
                            "stale": None, "reason": err})
            continue
        rows = payload.get("rows") or []
        # rows 形态为 [{'d': 'YYYY-MM-DD', 'o':…, 'c':…}, …]；兼容历史上出现过的 [date, …] 列表形态
        last_date = None
        if rows:
            tail = rows[-1]
            if isinstance(tail, dict):
                last_date = tail.get("d") or tail.get("date")
            elif isinstance(tail, (list, tuple)) and tail:
                last_date = tail[0]
            if last_date is not None:
                last_date = str(last_date)
        factors.append({
            "symbol": sym,
            "last_date": last_date,
            "fetch_ts": payload.get("fetch_ts"),
            "rows": len(rows),
            "reason": None if last_date else "缓存无数据行",
        })
    # 陈旧判定：以全部因子里最新的 last_date 为基准，落后即标 stale（不猜交易日历，
    # 只用组内相对新鲜度，避免节假日误判）。
    _dates = [x["last_date"] for x in factors if x.get("last_date")]
    _latest = max(_dates) if _dates else None
    for x in factors:
        if x.get("last_date") and _latest:
            x["stale"] = x["last_date"] != _latest
            x["staleness_days_vs_latest"] = None
        else:
            x["stale"] = None
    external_factors = {
        "source": "data/cache/global_hist_*_last.json",
        "count": len(factors),
        "available": sum(1 for x in factors if x.get("last_date")),
        "latest_last_date": _latest,
        "stale_count": sum(1 for x in factors if x.get("stale") is True),
        "items": factors,
    }

    # ---------- ② 离线构建产物（M3 板块体系）----------
    artifacts = []
    for name, counter in (("sector_map.json", "sectors"), ("sector_periods.json", "periods"),
                          ("sector_returns.json", None)):
        p = data_dir / name
        payload, err = _read_json_meta(p)
        st = _file_stat(p)
        if payload is None:
            artifacts.append({"file": name, **st, "count": None, "coverage": None, "reason": err})
            continue
        cnt = None
        if counter and isinstance(payload.get(counter), dict):
            cnt = len(payload[counter])
        elif isinstance(payload.get("periods"), dict):
            cnt = len(payload["periods"])
        elif not counter and isinstance(payload, dict):
            cnt = len(payload)
        artifacts.append({
            "file": name, **st, "count": cnt,
            "coverage": payload.get("coverage"),
            "updated_at": payload.get("updated_at"),
            "source": payload.get("source"),
            "reason": None,
        })
    offline_artifacts = {"source": "data/*.json（离线构建产物）", "items": artifacts}

    # ---------- ③ 交易引擎（虚拟盘 / 订单分布，演练与真实分开）----------
    trading_engine: dict = {"source": "server/trading（trading.sqlite，本地虚拟盘）"}
    try:
        store = None
        try:
            from server.trading import trading_facade as _facade  # type: ignore
            store = getattr(_facade, "store", None)
        except Exception:
            store = None
        if store is None:
            trading_engine.update({"available": False,
                                   "reason": "交易引擎未就绪（trading_facade.store 不可得）"})
        else:
            acc = await store.kv_get("paper_account") or {}
            _, orders = await store.list_orders(limit=20000)
            dist: dict = {}
            drill_count = 0
            real_count = 0
            for o in orders:
                key = f"{o.get('source') or 'signal'}/{o.get('status') or 'unknown'}"
                dist[key] = dist.get(key, 0) + 1
                if o.get("is_drill"):
                    drill_count += 1
                else:
                    real_count += 1
            trading_engine.update({
                "available": True,
                "initial_cash": acc.get("initial_cash"),
                "cash": acc.get("cash"),
                "positions": len(acc.get("positions") or {}) if isinstance(acc.get("positions"), dict) else None,
                "orders_total": len(orders),
                "orders_real": real_count,
                "orders_drill": drill_count,
                "distribution": dict(sorted(dist.items(), key=lambda kv: -kv[1])),
                "reason": None,
            })
    except Exception as e:
        trading_engine.update({"available": False,
                               "reason": f"交易引擎读取失败：{type(e).__name__} {str(e)[:120]}"})

    # ---------- ④ SSE 推送状态 ----------
    try:
        from server.routers.stream import hub as _hub
        sse = {"source": "/api/stream/stats（同一 hub 实例）", "available": True,
               "reason": None, **_hub.stats_snapshot()}
    except Exception as e:
        sse = {"source": "/api/stream/stats", "available": False,
               "reason": f"hub 不可得：{type(e).__name__} {str(e)[:120]}"}

    # ---------- ⑤ 功耗 / 缓存治理 ----------
    power_cache: dict = {"source": "data/api_calls.db + server.cache"}
    ap = data_dir / "api_calls.db"
    try:
        rows = None
        size = ap.stat().st_size if ap.exists() else None
        last_cleanup_raw = None
        if ap.exists():
            conn = sqlite3.connect(str(ap))
            try:
                # 真实表名是 api_call_logs（不是 api_calls）——写错会静默拿到 None
                rows = conn.execute("SELECT COUNT(*) FROM api_call_logs").fetchone()[0]
            except Exception as e:
                logger.warning("admin: api_call_logs 计数失败: %s", e)
                rows = None
            try:
                r = conn.execute("SELECT value FROM meta WHERE key=?",
                                 ("api_calls_last_cleanup",)).fetchone()
                last_cleanup_raw = r[0] if r else None
            except Exception:
                last_cleanup_raw = None
            finally:
                conn.close()
        # 游标是 epoch 秒，转成人可读时间；转换失败则原样返回并说明
        last_cleanup = None
        if last_cleanup_raw is not None:
            try:
                last_cleanup = datetime.fromtimestamp(float(last_cleanup_raw)).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                last_cleanup = str(last_cleanup_raw)
        power_cache.update({
            "api_calls_rows": rows,
            "api_calls_size_bytes": size,
            "api_calls_last_cleanup": last_cleanup,
            "api_calls_table": "api_call_logs",
            "api_calls_reason": None if rows is not None else "api_calls.db 不存在或 api_call_logs 表不可读",
        })
    except Exception as e:
        power_cache.update({"api_calls_rows": None, "api_calls_size_bytes": None,
                            "api_calls_last_cleanup": None,
                            "api_calls_reason": f"读取失败：{type(e).__name__} {str(e)[:120]}"})

    try:
        ttl_buckets = {"<=60s": 0, "60-600s": 0, ">600s": 0}
        expiring_soon = 0
        for e in cache.iter_entries():
            ttl = e["ttl"]
            if ttl is None:
                continue
            if ttl <= 60:
                ttl_buckets["<=60s"] += 1
            elif ttl <= 600:
                ttl_buckets["60-600s"] += 1
            else:
                ttl_buckets[">600s"] += 1
            if e["remaining_ttl"] <= 5:
                expiring_soon += 1
        st = cache.stats()
        power_cache.update({
            "cache_entries": st["size"],
            "cache_expired_now": st["expired_now"],
            "cache_ttl_buckets": ttl_buckets,
            "cache_expiring_within_5s": expiring_soon,
            "cache_hit_rate": st["hit_rate"],
            "cache_cleanup_running": st["cleanup_task_running"],
        })
    except Exception as e:
        power_cache.update({"cache_entries": None, "cache_ttl_buckets": None,
                            "cache_reason": f"缓存读取失败：{type(e).__name__} {str(e)[:120]}"})

    try:
        cpu_now = sample_process_cpu_percent()
        mem = psutil.virtual_memory()
        power_cache.update({
            "cpu_percent": cpu_now,
            "cpu_scope": "process",
            "cpu_reason": None if cpu_now is not None else "CPU 采样基线尚未建立（首次调用），下次刷新可得",
            "mem_process_rss_mb": round(psutil.Process(os.getpid()).memory_info().rss / (1024 ** 2), 1),
            "mem_system_used_pct": mem.percent,
        })
    except Exception as e:
        power_cache.update({"cpu_percent": None, "cpu_scope": "process",
                            "cpu_reason": f"采样失败：{type(e).__name__}", "mem_process_rss_mb": None,
                            "mem_system_used_pct": None})

    return {
        "ok": True,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": {
            "external_factors": external_factors,
            "offline_artifacts": offline_artifacts,
            "trading_engine": trading_engine,
            "sse": sse,
            "power_cache": power_cache,
        },
    }


# ===== 收入趋势 =====

@router.get("/revenue-trend")
async def revenue_trend(days: int = Query(30, ge=1, le=90)):
    """近N天收入趋势"""
    orders = _load_orders()
    paid = [o for o in orders.values() if o.get("status") == OrderStatus.PAID.value]

    trend = []
    for i in range(days - 1, -1, -1):
        from datetime import timedelta
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        day_orders = [o for o in paid if o.get("paid_at", "").startswith(d)]
        revenue = sum(o.get("amount_fen", 0) for o in day_orders)
        trend.append({
            "date": d,
            "orders": len(day_orders),
            "revenue_fen": revenue,
            "revenue_yuan": round(revenue / 100, 2),
        })

    return {"ok": True, "data": trend}


# ===== 板块热力 =====

@router.get("/sector-heatmap")
async def sector_heatmap():
    """板块访问热力数据"""
    result = []
    for key, val in SECTORS.items():
        cache_key = f"quotes_raw:{key}"
        cached = cache.get(cache_key)
        result.append({
            "key": key,
            "name": val["name"],
            "icon": val["icon"],
            "stock_count": len(val["stocks"]),
            "has_cache": cached is not None,
        })

    return {"ok": True, "data": result}
