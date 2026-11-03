"""自动交易核心包 - SQLite 持久化层（唯一读写入口）

架构蓝图 §4：data/trading.db，5 张表 signals/orders/positions/config/audit_log。
- PRAGMA journal_mode=WAL; synchronous=NORMAL
- 单写者连接 + 模块级 asyncio.Lock；所有写操作串行
- 其他模块禁止自行 sqlite3.connect

测试可用环境变量 TRADING_DB_PATH 覆盖数据库路径。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import Position, now_local_iso
from . import marks

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "trading.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    source TEXT NOT NULL,
    price REAL,
    chg_pct REAL,
    score INTEGER,
    detail_json TEXT,
    pushed INTEGER NOT NULL DEFAULT 0,
    push_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_signals_code_ts ON signals (code, ts DESC);
CREATE INDEX IF NOT EXISTS idx_signals_type_ts ON signals (signal_type, ts DESC);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals (ts DESC);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    price REAL NOT NULL,
    amount REAL NOT NULL,
    gateway TEXT NOT NULL,
    status TEXT NOT NULL,
    fail_reason TEXT,
    signal_id TEXT REFERENCES signals(id),
    strategy_id TEXT,
    ref_price REAL,
    commission REAL,
    stamp_tax REAL,
    transfer_fee REAL,
    slippage_cost REAL,
    total_cost REAL,
    is_drill INTEGER NOT NULL DEFAULT 0,
    exit_reason TEXT,
    decision_json TEXT,
    source TEXT NOT NULL DEFAULT 'signal',
    req_qty INTEGER NOT NULL DEFAULT 0,
    req_amount REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_orders_code_ts ON orders (code, ts DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);
CREATE INDEX IF NOT EXISTS idx_orders_ts ON orders (ts DESC);

CREATE TABLE IF NOT EXISTS positions (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    qty INTEGER NOT NULL,
    avg_cost REAL NOT NULL,
    last_price REAL,
    gateway TEXT NOT NULL DEFAULT 'paper',
    updated_at TEXT NOT NULL,
    strategy_id TEXT,
    buy_date TEXT,
    sellable_qty INTEGER NOT NULL DEFAULT 0,
    peak_price REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_positions_gateway ON positions (gateway);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT,
    result TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action_ts ON audit_log (action, ts DESC);

CREATE TABLE IF NOT EXISTS conditional_orders (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 0,
    trigger_type TEXT NOT NULL,          -- price / time
    trigger_op TEXT,                     -- >= / <= （price 用）
    trigger_price REAL,                  -- price 用
    trigger_at TEXT,                     -- time 用（本地 ISO）
    status TEXT NOT NULL DEFAULT 'pending',   -- pending / triggered / canceled / expired
    note TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    result_order_id TEXT,
    triggered_ts TEXT,
    triggered_price REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_cond_status ON conditional_orders (status, ts DESC);
"""

# 向后兼容迁移：老库缺列时以 ALTER TABLE ADD COLUMN 补齐（旧数据不丢、可读）
_MIGRATIONS = {
    "orders": {
        "ref_price": "REAL",
        "commission": "REAL",
        "stamp_tax": "REAL",
        "transfer_fee": "REAL",
        "slippage_cost": "REAL",
        "total_cost": "REAL",
        "is_drill": "INTEGER NOT NULL DEFAULT 0",
        "exit_reason": "TEXT",
        "decision_json": "TEXT",
        "source": "TEXT NOT NULL DEFAULT 'signal'",
        "req_qty": "INTEGER NOT NULL DEFAULT 0",
        "req_amount": "REAL NOT NULL DEFAULT 0",
    },
    "positions": {
        "strategy_id": "TEXT",
        "buy_date": "TEXT",
        "sellable_qty": "INTEGER NOT NULL DEFAULT 0",
        "peak_price": "REAL NOT NULL DEFAULT 0",
    },
}


class TradingStore:
    """SQLite 唯一读写入口：原子事务、建表迁移、分页查询、配置 KV 存取"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.environ.get("TRADING_DB_PATH") or str(_DEFAULT_DB_PATH)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    async def init(self) -> None:
        """建库建表 + 迁移 + WAL（幂等、并发安全，可重复调用）

        P0 顺序约束（历史上曾致 init 抛 sqlite3.OperationalError: no such column: is_drill，
        触发 watchdog 连挂）：
          _SCHEMA **只允许**定义"基列"及其索引，绝不能包含任何引用"迁移列"（如 is_drill、
          exit_reason、positions.peak_price…）的 CREATE INDEX——否则在老库上 executescript
          会因该列尚不存在而抛错，且 init 建表在建列之前，服务整体不可用。
          依赖迁移列的索引必须放到 _migrate 之后由 _ensure_indexes 创建。
        并发：整个初始化持 self._lock，避免并发调用时半初始化连接被读取。
        """
        async with self._lock:
            if self._conn is not None:
                return
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                # 1) 建表（含基列 + 基列索引）；不含任何迁移列索引
                conn.executescript(_SCHEMA)
                # 2) 老库补列（幂等，ALTER TABLE ADD COLUMN，不丢数据）
                self._migrate(conn)
                # 3) 依赖迁移列的索引（此时列已保证存在）
                self._ensure_indexes(conn)
                conn.commit()
            except Exception:
                conn.close()
                raise
            self._conn = conn
            logger.info("TradingStore initialized: %s", self.db_path)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """向后兼容迁移：为老库补列（幂等）。旧数据保留、可读，不重建表。"""
        for table, cols in _MIGRATIONS.items():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue  # 表不存在（理论不会，建表脚本已保证）→ 跳过
            for col, decl in cols.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    logger.info("TradingStore: 迁移 %s 新增列 %s %s", table, col, decl)

    @staticmethod
    def _ensure_indexes(conn: sqlite3.Connection) -> None:
        """创建依赖"迁移列"的索引（必须在 _migrate 之后调用）。

        这些索引不能写进 _SCHEMA：老库上该列尚不存在，executescript 会直接抛错。
        """
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_drill ON orders (is_drill)")

    async def close(self) -> None:
        """关闭连接（进程退出前调用）"""
        async with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("TradingStore not initialized: call await store.init() first")
        return self._conn

    # --------------------------------------------------------
    # signals
    # --------------------------------------------------------
    async def insert_signal(self, row: Dict[str, Any]) -> None:
        async with self._lock:
            conn = self._require_conn()
            conn.execute(
                """INSERT INTO signals
                   (id, ts, code, name, signal_type, source, price, chg_pct, score,
                    detail_json, pushed, push_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["id"], row["ts"], row["code"], row["name"], row["signal_type"],
                    row["source"], row.get("price"), row.get("chg_pct"), row.get("score"),
                    json.dumps(row.get("detail") or {}, ensure_ascii=False),
                    1 if row.get("pushed") else 0, row.get("push_status", "pending"),
                ),
            )
            conn.commit()

    async def update_signal_push(self, signal_id: str, pushed: bool, push_status: str) -> None:
        async with self._lock:
            conn = self._require_conn()
            conn.execute(
                "UPDATE signals SET pushed=?, push_status=? WHERE id=?",
                (1 if pushed else 0, push_status, signal_id),
            )
            conn.commit()

    async def list_signals(
        self, code: Optional[str] = None, limit: int = 100, offset: int = 0
    ) -> Tuple[int, List[Dict[str, Any]]]:
        async with self._lock:
            conn = self._require_conn()
            where, args = "WHERE code = ?", [code] if code else []
            if not code:
                where = ""
            total = conn.execute(f"SELECT COUNT(*) FROM signals {where}", args).fetchone()[0]
            rows = conn.execute(
                f"""SELECT * FROM signals {where}
                    ORDER BY ts DESC LIMIT ? OFFSET ?""",
                [*args, limit, offset],
            ).fetchall()
        return total, [self._signal_row_to_dict(r) for r in rows]

    @staticmethod
    def _signal_row_to_dict(r: sqlite3.Row) -> Dict[str, Any]:
        try:
            detail = json.loads(r["detail_json"]) if r["detail_json"] else {}
        except (ValueError, TypeError):
            detail = {}
        return {
            "id": r["id"], "ts": r["ts"], "code": r["code"], "name": r["name"],
            "signal_type": r["signal_type"], "source": r["source"],
            "price": r["price"], "chg_pct": r["chg_pct"], "score": r["score"],
            "detail": detail, "pushed": bool(r["pushed"]), "push_status": r["push_status"],
        }

    async def cooldown_map(self) -> Dict[Tuple[str, str, str], str]:
        """冷却状态重建：(code, signal_type, source) -> 最后触发 ts（§4 冷却不建表）"""
        async with self._lock:
            conn = self._require_conn()
            rows = conn.execute(
                "SELECT code, signal_type, source, MAX(ts) AS max_ts FROM signals GROUP BY 1,2,3"
            ).fetchall()
        return {(r["code"], r["signal_type"], r["source"]): r["max_ts"] for r in rows}

    # --------------------------------------------------------
    # orders
    # --------------------------------------------------------
    async def insert_order(self, row: Dict[str, Any]) -> None:
        async with self._lock:
            conn = self._require_conn()
            # 演练自动打标：调用方未显式给 is_drill 时，读取 drill 上下文（不依赖记得传参）
            is_drill = row.get("is_drill")
            if is_drill is None:
                is_drill = 1 if marks.drill_active() else 0
            conn.execute(
                """INSERT INTO orders
                   (id, ts, code, name, side, qty, price, amount, gateway,
                    status, fail_reason, signal_id, strategy_id,
                    ref_price, commission, stamp_tax, transfer_fee, slippage_cost, total_cost,
                    is_drill, exit_reason, decision_json, source,
                    req_qty, req_amount)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["id"], row["ts"], row["code"], row["name"], row["side"],
                    row["qty"], row["price"], row["amount"], row["gateway"],
                    row["status"], row.get("fail_reason"), row.get("signal_id"),
                    row.get("strategy_id"),
                    row.get("ref_price"), row.get("commission"), row.get("stamp_tax"),
                    row.get("transfer_fee"), row.get("slippage_cost"), row.get("total_cost"),
                    1 if is_drill else 0, row.get("exit_reason"),
                    json.dumps(row.get("decision_json"), ensure_ascii=False)
                    if row.get("decision_json") is not None else None,
                    row.get("source") or "signal",
                    # 用户请求数量/金额（换算前）。被拦下时 qty 可能为 0，但这两列保留原始意图，
                    # 供复盘回放「如果当时买了会怎样」；缺省 0 表示调用方未指定数量（按额度自动换算）。
                    int(row.get("req_qty") or 0),
                    float(row.get("req_amount") or 0.0),
                ),
            )
            conn.commit()

    async def mark_orders_as_drill(self, ts_from: str, ts_to: Optional[str] = None) -> int:
        """回填历史演练订单标记（一次性数据修复）：[ts_from, ts_to] 区间内标记 is_drill=1。

        仅用于把本次联调中已产生的演练订单补标；返回受影响行数。属 store 唯一写入口。
        """
        async with self._lock:
            conn = self._require_conn()
            if ts_to:
                cur = conn.execute(
                    "UPDATE orders SET is_drill=1 WHERE ts>=? AND ts<=? AND is_drill=0",
                    (ts_from, ts_to),
                )
            else:
                cur = conn.execute(
                    "UPDATE orders SET is_drill=1 WHERE ts>=? AND is_drill=0", (ts_from,)
                )
            conn.commit()
            return cur.rowcount

    @staticmethod
    def _order_row(r: sqlite3.Row) -> Dict[str, Any]:
        d = dict(r)
        dj = d.get("decision_json")
        if isinstance(dj, str) and dj:
            try:
                d["decision_json"] = json.loads(dj)
            except (ValueError, TypeError):
                pass  # 保留原始字符串（不掩盖坏数据）
        return d

    async def list_orders(
        self, code: Optional[str] = None, limit: int = 100, offset: int = 0
    ) -> Tuple[int, List[Dict[str, Any]]]:
        async with self._lock:
            conn = self._require_conn()
            if code:
                total = conn.execute("SELECT COUNT(*) FROM orders WHERE code=?", (code,)).fetchone()[0]
                rows = conn.execute(
                    "SELECT * FROM orders WHERE code=? ORDER BY ts DESC LIMIT ? OFFSET ?",
                    (code, limit, offset),
                ).fetchall()
            else:
                total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
                rows = conn.execute(
                    "SELECT * FROM orders ORDER BY ts DESC LIMIT ? OFFSET ?", (limit, offset)
                ).fetchall()
        return total, [self._order_row(r) for r in rows]

    async def last_filled_order_ts(self, code: str, side: str, gateway: str) -> Optional[str]:
        """同标的同方向最近一笔成交时间（风控关卡 9 下单冷却用）"""
        async with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                """SELECT MAX(ts) AS max_ts FROM orders
                   WHERE code=? AND side=? AND gateway=? AND status='filled'""",
                (code, side, gateway),
            ).fetchone()
        return row["max_ts"] if row and row["max_ts"] else None

    async def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            conn = self._require_conn()
            r = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        return self._order_row(r) if r else None

    async def cancel_order(self, order_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """撤单：仅允许撤"未成交"单（status 非 filled/rejected/cancelled/partial_canceled）。

        返回 (ok, message, order)。已成交/已拒绝 → ok=False + 明确原因（不静默成功）。
        """
        async with self._lock:
            conn = self._require_conn()
            r = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if r is None:
                return False, "订单不存在", None
            status = r["status"]
            if status in ("filled", "partial_canceled"):
                return False, f"订单状态 {status}，已成交不可撤单", self._order_row(r)
            if status in ("rejected", "cancelled", "failed"):
                return False, f"订单状态 {status}，无可撤内容", self._order_row(r)
            # pending / partial → 撤单
            new_status = "partial_canceled" if status == "partial" else "cancelled"
            conn.execute("UPDATE orders SET status=? WHERE id=?", (new_status, order_id))
            conn.commit()
            r2 = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        return True, f"已撤单（{status}→{new_status}）", self._order_row(r2)

    # --------------------------------------------------------
    # conditional_orders（条件单：价格触发 / 时间触发）
    # --------------------------------------------------------
    async def insert_conditional(self, row: Dict[str, Any]) -> None:
        async with self._lock:
            conn = self._require_conn()
            conn.execute(
                """INSERT INTO conditional_orders
                   (id, ts, code, name, side, qty, trigger_type, trigger_op, trigger_price,
                    trigger_at, status, note, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["id"], row["ts"], row["code"], row.get("name"), row["side"],
                    int(row.get("qty") or 0), row["trigger_type"], row.get("trigger_op"),
                    row.get("trigger_price"), row.get("trigger_at"),
                    row.get("status", "pending"), row.get("note"), row.get("source", "manual"),
                ),
            )
            conn.commit()

    async def list_conditionals(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        async with self._lock:
            conn = self._require_conn()
            if status:
                rows = conn.execute(
                    "SELECT * FROM conditional_orders WHERE status=? ORDER BY ts DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM conditional_orders ORDER BY ts DESC LIMIT 500").fetchall()
        return [dict(r) for r in rows]

    async def get_conditional(self, cid: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            conn = self._require_conn()
            r = conn.execute("SELECT * FROM conditional_orders WHERE id=?", (cid,)).fetchone()
        return dict(r) if r else None

    async def cancel_conditional(self, cid: str) -> Tuple[bool, str]:
        """取消条件单：仅 pending 可撤。"""
        async with self._lock:
            conn = self._require_conn()
            r = conn.execute("SELECT status FROM conditional_orders WHERE id=?", (cid,)).fetchone()
            if r is None:
                return False, "条件单不存在"
            if r["status"] != "pending":
                return False, f"条件单状态 {r['status']}，不可取消"
            conn.execute("UPDATE conditional_orders SET status='canceled' WHERE id=?", (cid,))
            conn.commit()
        return True, "条件单已取消"

    async def mark_conditional_triggered(self, cid: str, result_order_id: str,
                                         triggered_ts: str, triggered_price: float) -> None:
        async with self._lock:
            conn = self._require_conn()
            conn.execute(
                """UPDATE conditional_orders
                   SET status='triggered', result_order_id=?, triggered_ts=?, triggered_price=?
                   WHERE id=?""",
                (result_order_id, triggered_ts, float(triggered_price or 0.0), cid),
            )
            conn.commit()

    async def expire_conditional(self, cid: str, note: str) -> None:
        async with self._lock:
            conn = self._require_conn()
            conn.execute("UPDATE conditional_orders SET status='expired', note=? WHERE id=?",
                         (note, cid))
            conn.commit()

    # --------------------------------------------------------
    # positions
    # --------------------------------------------------------
    async def get_position(self, code: str, gateway: str = "paper") -> Optional[Position]:
        async with self._lock:
            conn = self._require_conn()
            r = conn.execute(
                "SELECT * FROM positions WHERE code=? AND gateway=?", (code, gateway)
            ).fetchone()
        return self._pos_row(r) if r else None

    async def list_positions(self, gateway: str = "paper") -> List[Position]:
        async with self._lock:
            conn = self._require_conn()
            rows = conn.execute(
                "SELECT * FROM positions WHERE gateway=? ORDER BY updated_at DESC", (gateway,)
            ).fetchall()
        return [self._pos_row(r) for r in rows]

    async def upsert_position(self, pos: Position) -> None:
        async with self._lock:
            conn = self._require_conn()
            conn.execute(
                """INSERT INTO positions
                   (code, name, qty, avg_cost, last_price, gateway, updated_at,
                    strategy_id, buy_date, sellable_qty, peak_price)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(code) DO UPDATE SET
                     name=excluded.name, qty=excluded.qty, avg_cost=excluded.avg_cost,
                     last_price=excluded.last_price, gateway=excluded.gateway,
                     updated_at=excluded.updated_at, strategy_id=excluded.strategy_id,
                     buy_date=excluded.buy_date, sellable_qty=excluded.sellable_qty,
                     peak_price=excluded.peak_price""",
                (pos.code, pos.name, pos.qty, pos.avg_cost, pos.last_price,
                 pos.gateway, now_local_iso(), pos.strategy_id, pos.buy_date,
                 int(pos.sellable_qty or 0), float(pos.peak_price or 0.0)),
            )
            conn.commit()

    async def delete_position(self, code: str, gateway: str = "paper") -> None:
        async with self._lock:
            conn = self._require_conn()
            conn.execute("DELETE FROM positions WHERE code=? AND gateway=?", (code, gateway))
            conn.commit()

    @staticmethod
    def _pos_row(r: sqlite3.Row) -> Position:
        keys = r.keys()
        return Position(
            code=r["code"], name=r["name"], qty=int(r["qty"]),
            avg_cost=float(r["avg_cost"]), last_price=float(r["last_price"] or 0.0),
            gateway=r["gateway"], updated_at=r["updated_at"],
            strategy_id=(r["strategy_id"] if "strategy_id" in keys else None),
            buy_date=(r["buy_date"] if "buy_date" in keys else "") or "",
            sellable_qty=(int(r["sellable_qty"] or 0) if "sellable_qty" in keys else 0),
            peak_price=(float(r["peak_price"] or 0.0) if "peak_price" in keys else 0.0),
        )

    # --------------------------------------------------------
    # config KV
    # --------------------------------------------------------
    async def kv_get(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            conn = self._require_conn()
            r = conn.execute("SELECT value_json FROM config WHERE key=?", (key,)).fetchone()
        if r is None:
            return default
        try:
            return json.loads(r["value_json"])
        except (ValueError, TypeError):
            return default

    async def kv_set(self, key: str, value: Any) -> None:
        async with self._lock:
            conn = self._require_conn()
            conn.execute(
                """INSERT INTO config (key, value_json, updated_at) VALUES (?,?,?)
                   ON CONFLICT(key) DO UPDATE SET
                     value_json=excluded.value_json, updated_at=excluded.updated_at""",
                (key, json.dumps(value, ensure_ascii=False), now_local_iso()),
            )
            conn.commit()

    async def kv_updated_at(self, key: str) -> Optional[str]:
        """读 config 表 updated_at 列（配置新鲜度透出用）。"""
        async with self._lock:
            conn = self._require_conn()
            r = conn.execute("SELECT updated_at FROM config WHERE key=?", (key,)).fetchone()
        return r["updated_at"] if r else None

    # --------------------------------------------------------
    # audit_log
    # --------------------------------------------------------
    async def audit(
        self, actor: str, action: str, payload: Optional[Dict[str, Any]] = None, result: str = "ok"
    ) -> None:
        """全量审计（§4.5）。授权码等敏感信息禁止进入 payload。"""
        async with self._lock:
            conn = self._require_conn()
            conn.execute(
                "INSERT INTO audit_log (ts, actor, action, payload_json, result) VALUES (?,?,?,?,?)",
                (
                    now_local_iso(), actor, action,
                    json.dumps(payload or {}, ensure_ascii=False), result,
                ),
            )
            conn.commit()

    async def list_audit(
        self, action: Optional[str] = None, actor: Optional[str] = None,
        result: Optional[str] = None, limit: int = 100, offset: int = 0,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """审计日志查询（供 GET /api/automation/audit 透出留痕）。

        返回 (total, items)；items 按 ts DESC, id DESC 排列，payload_json 已解析为 dict。
        """
        where, params = [], []
        if action:
            where.append("action=?")
            params.append(action)
        if actor:
            where.append("actor=?")
            params.append(actor)
        if result:
            where.append("result=?")
            params.append(result)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        async with self._lock:
            conn = self._require_conn()
            total = conn.execute(f"SELECT COUNT(*) AS c FROM audit_log{clause}", params).fetchone()["c"]
            rows = conn.execute(
                f"SELECT * FROM audit_log{clause} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                (*params, int(limit), int(offset)),
            ).fetchall()
        items: List[Dict[str, Any]] = []
        for r in rows:
            try:
                payload = json.loads(r["payload_json"] or "{}")
            except (ValueError, TypeError):
                payload = {}
            items.append({
                "id": r["id"], "ts": r["ts"], "actor": r["actor"],
                "action": r["action"], "payload": payload, "result": r["result"],
            })
        return int(total), items
