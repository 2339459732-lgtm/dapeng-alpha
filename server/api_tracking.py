"""API Call Tracking Middleware + SQLite Async Writer.

Zero-intrusion middleware that records all /api/* requests to SQLite.
Uses asyncio.Queue for non-blocking batch writes (every 100 records or 1 second).

Architecture: docs/architecture-tab-admin.md §5.1-5.3
"""

import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from server.config import DB_DIR

logger = logging.getLogger(__name__)


def _ensure_logging() -> None:
    """确保本模块 INFO 日志可见（uvicorn 未配置 root logger，否则被丢弃）。"""
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


_ensure_logging()

# ── Middleware Configuration ──────────────────────────────────────

EXCLUDED_PATHS = frozenset({
    "/api/health",       # heartbeat — don't track
    "/api/admin",        # admin self-API — avoid recursive tracking
    "/admin",            # admin static page
    "/docs",             # Swagger UI
    "/openapi.json",     # OpenAPI schema
    "/favicon.ico",      # static resource
    "/redoc",            # ReDoc docs
})

SENSITIVE_PARAM_KEYS = frozenset({
    "token", "password", "key", "secret", "apikey", "api_key",
})

# 统计口径：超过该阈值的耗时不具代表性（请求挂起/进程挂起/连接存活时长），
# 记入 is_outlier=1（**保留原值可审计**），但不计入任何平均/分位统计。
MAX_REASONABLE_RESPONSE_MS = 600_000.0   # 10 分钟


def _should_track(path: str) -> bool:
    """Return True if the given URL path should be tracked by the middleware."""
    if not path.startswith("/api/"):
        return False
    for excluded in EXCLUDED_PATHS:
        if path.startswith(excluded):
            return False
    return True


def _sanitize_params(query_string: str) -> str:
    """Mask sensitive query-parameter values and truncate to 200 chars."""
    if not query_string:
        return ""
    params = parse_qs(query_string)
    sanitized = {}
    for k, v in params.items():
        if k.lower() in SENSITIVE_PARAM_KEYS:
            sanitized[k] = "***"
        else:
            sanitized[k] = v[0] if v else ""
    result = "&".join(f"{k}={val}" for k, val in sanitized.items())
    return result[:200]


def _is_success(status_code: int) -> bool:
    """Return True for 2xx status codes."""
    return 200 <= status_code < 300


# ── CallLogWriter: Async Buffered SQLite Writer ──────────────────

class CallLogWriter:
    """Asynchronous buffered writer for API call logs.

    Uses asyncio.Queue as a buffer and a background task to batch INSERT
    into SQLite every BATCH_SIZE records or every FLUSH_INTERVAL seconds.
    A daily cleanup task removes records older than RETENTION_DAYS.
    """

    BATCH_SIZE: int = 100
    FLUSH_INTERVAL: float = 1.0
    IDLE_FLUSH_TIMEOUT: float = 5.0   # 空队列时的等待上限（降低空转唤醒；有数据仍即时 flush）
    RETENTION_DAYS: int = 90
    CLEANUP_INTERVAL_SECONDS: int = 86400          # 清理周期
    LAST_CLEANUP_KEY: str = "api_calls_last_cleanup"  # KV 游标键

    def __init__(self):
        self._db_path: str = str(DB_DIR / "api_calls.db")
        self._queue: asyncio.Queue = None
        self._flush_task: asyncio.Task = None
        self._cleanup_task: asyncio.Task = None

    async def start(self) -> None:
        """Initialize DB schema and launch background tasks.

        Called during FastAPI lifespan startup.
        """
        self._queue = asyncio.Queue()
        self._init_db()
        self._flush_task = asyncio.create_task(self._flush_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("CallLogWriter started — db=%s", self._db_path)

    async def stop(self) -> None:
        """Flush remaining records and cancel background tasks.

        Called during FastAPI lifespan shutdown.
        """
        await self._flush_remaining()
        for task in (self._flush_task, self._cleanup_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("CallLogWriter stopped")

    def enqueue(self, record: dict) -> None:
        """Non-blocking enqueue of a call-record dict.

        Called from the middleware dispatch — completes in <0.1 ms.
        """
        if self._queue is not None:
            self._queue.put_nowait(record)

    def _init_db(self) -> None:
        """Create the api_call_logs table and indexes if they don't exist."""
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_call_logs (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint         TEXT NOT NULL,
                method           TEXT NOT NULL,
                status_code      INTEGER NOT NULL,
                response_time_ms REAL,                       -- 可空：流式/长连接不计服务器耗时
                is_success       INTEGER NOT NULL,
                timestamp        TEXT NOT NULL,
                params_summary   TEXT,
                is_streaming     INTEGER NOT NULL DEFAULT 0, -- SSE/长连接（存活时长≠处理耗时）
                is_outlier       INTEGER NOT NULL DEFAULT 0  -- 量级异常（保留原值，统计排除）
            )
            """
        )
        self._migrate_schema(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_call_logs_timestamp "
            "ON api_call_logs(timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_call_logs_endpoint "
            "ON api_call_logs(endpoint)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_call_logs_endpoint_ts "
            "ON api_call_logs(endpoint, timestamp)"
        )
        # KV 表：持久化后台任务的游标（如 last_cleanup），使重启不清零
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.commit()
        conn.close()

    def _migrate_schema(self, conn) -> None:
        """老库补列 + 放开 response_time_ms 的 NOT NULL + 回填离群标注。

        - 补 `is_streaming` / `is_outlier` 两列；
        - 若 `response_time_ms` 仍为 NOT NULL，则原表重建为**可空**（保留全部数据与 id）；
        - 对历史异常样本（> MAX_REASONABLE_RESPONSE_MS）标 `is_outlier=1`（**不删除**）。
        """
        cols = {r[1] for r in conn.execute("PRAGMA table_info(api_call_logs)")}
        for name in ("is_streaming", "is_outlier"):
            if name not in cols:
                conn.execute(
                    f"ALTER TABLE api_call_logs ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0"
                )

        info = {r[1]: r for r in conn.execute("PRAGMA table_info(api_call_logs)")}
        rt = info.get("response_time_ms")
        if rt is not None and rt[3] == 1:  # PRAGMA notnull == 1
            conn.execute("ALTER TABLE api_call_logs RENAME TO api_call_logs_legacy")
            conn.execute(
                """
                CREATE TABLE api_call_logs (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint         TEXT NOT NULL,
                    method           TEXT NOT NULL,
                    status_code      INTEGER NOT NULL,
                    response_time_ms REAL,
                    is_success       INTEGER NOT NULL,
                    timestamp        TEXT NOT NULL,
                    params_summary   TEXT,
                    is_streaming     INTEGER NOT NULL DEFAULT 0,
                    is_outlier       INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                INSERT INTO api_call_logs
                    (id, endpoint, method, status_code, response_time_ms, is_success,
                     timestamp, params_summary, is_streaming, is_outlier)
                SELECT id, endpoint, method, status_code, response_time_ms, is_success,
                       timestamp, params_summary, is_streaming, is_outlier
                FROM api_call_logs_legacy
                """
            )
            conn.execute("DROP TABLE api_call_logs_legacy")
            logger.info("CallLogWriter: 迁移 api_call_logs（response_time_ms → 可空）")

        n = conn.execute(
            "UPDATE api_call_logs SET is_outlier = 1 "
            "WHERE is_outlier = 0 AND response_time_ms IS NOT NULL AND response_time_ms > ?",
            (MAX_REASONABLE_RESPONSE_MS,),
        ).rowcount
        if n:
            logger.info("CallLogWriter: 标注 %d 条历史离群耗时样本（is_outlier=1，保留原值）", n)
        conn.commit()

    async def _flush_loop(self) -> None:
        """Background consumer: batch INSERT every BATCH_SIZE or FLUSH_INTERVAL."""
        while True:
            batch = []
            try:
                first = await asyncio.wait_for(
                    self._queue.get(), timeout=self.IDLE_FLUSH_TIMEOUT
                )
                batch.append(first)
                while len(batch) < self.BATCH_SIZE:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
            except asyncio.TimeoutError:
                pass  # 队列空闲超时——无数据则无需 flush（有数据时 get() 会即时返回）

            if batch:
                await self._batch_insert(batch)

    async def _flush_remaining(self) -> None:
        """Drain any remaining records in the queue during shutdown."""
        if self._queue is None:
            return
        batch = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if batch:
            await self._batch_insert(batch)

    async def _batch_insert(self, records: list) -> None:
        """Execute batch INSERT in a thread pool to avoid blocking the event loop."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._do_insert, records)

    def _do_insert(self, records: list) -> None:
        """Synchronous batch INSERT (executed inside the thread pool)."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.executemany(
                "INSERT INTO api_call_logs "
                "(endpoint, method, status_code, response_time_ms, "
                "is_success, timestamp, params_summary, is_streaming, is_outlier) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        r["endpoint"],
                        r["method"],
                        r["status_code"],
                        r.get("response_time_ms"),
                        1 if r["is_success"] else 0,
                        r["timestamp"],
                        r["params_summary"],
                        1 if r.get("is_streaming") else 0,
                        1 if r.get("is_outlier") else 0,
                    )
                    for r in records
                ],
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.error("Batch insert failed (%d records): %s", len(records), exc)

    async def _cleanup_loop(self) -> None:
        """周期性清理：**启动即判定一次**，之后每 CLEANUP_INTERVAL 复查。

        修复：原实现「先 sleep(86400) 再清理」→ 每次重启把 24h 计时清零，
        清理从未执行（DB 单调增长）。现在 last_cleanup 持久化在 DB 的 meta(KV) 表，
        重启不再清零；若距上次清理已超 CLEANUP_INTERVAL（或从未清理）则**补跑**。
        """
        while True:
            try:
                await self._run_cleanup_if_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.error("CallLogWriter cleanup loop error", exc_info=True)
            await asyncio.sleep(self.CLEANUP_INTERVAL_SECONDS)

    async def _run_cleanup_if_due(self) -> None:
        """到点（或从未清理）才真正清理，并把时间戳写回 KV。"""
        loop = asyncio.get_event_loop()
        last_raw = await loop.run_in_executor(None, self._kv_get, self.LAST_CLEANUP_KEY)
        now = time.time()
        if last_raw is not None:
            try:
                elapsed = now - float(last_raw)
                if elapsed < self.CLEANUP_INTERVAL_SECONDS:
                    logger.info(
                        "CallLogWriter cleanup: 距上次 %.0fs < %ds，跳过",
                        elapsed, self.CLEANUP_INTERVAL_SECONDS,
                    )
                    return
            except (TypeError, ValueError):
                pass
        await loop.run_in_executor(None, self._do_cleanup)
        await loop.run_in_executor(None, self._kv_set, self.LAST_CLEANUP_KEY, str(now))

    def _kv_get(self, key: str):
        try:
            conn = sqlite3.connect(self._db_path)
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            conn.close()
            return row[0] if row else None
        except Exception as exc:  # noqa: BLE001
            logger.error("meta kv_get failed: %s", exc)
            return None

    def _kv_set(self, key: str, value: str) -> None:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()
            conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.error("meta kv_set failed: %s", exc)

    def _do_cleanup(self) -> None:
        """Synchronous DELETE of stale records."""
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=self.RETENTION_DAYS)
            ).isoformat()
            conn = sqlite3.connect(self._db_path)
            deleted = conn.execute(
                "DELETE FROM api_call_logs WHERE timestamp < ?", (cutoff,)
            ).rowcount
            conn.commit()
            conn.close()
            logger.info("CallLogWriter cleanup: deleted %d old records", deleted)
        except Exception as exc:
            logger.error("Cleanup failed: %s", exc)


# Module-level singleton — imported by main.py and admin.py
call_log_writer = CallLogWriter()


# ── APITrackingMiddleware ─────────────────────────────────────────

class APITrackingMiddleware(BaseHTTPMiddleware):
    """HTTP middleware that tracks /api/* calls and enqueues them to CallLogWriter.

    Registered in main.py via ``app.add_middleware(APITrackingMiddleware)``.
    Requests matching EXCLUDED_PATHS are passed through without tracking.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip tracking for excluded paths
        if not _should_track(path):
            return await call_next(request)

        start_time = time.time()

        try:
            response: Response = await call_next(request)
        except Exception:
            # Record the failed request, then re-raise
            ms = round((time.time() - start_time) * 1000, 2)
            call_log_writer.enqueue({
                "endpoint": path,
                "method": request.method,
                "status_code": 500,
                "response_time_ms": ms,
                "is_success": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "params_summary": _sanitize_params(str(request.url.query)),
                "is_streaming": False,
                "is_outlier": ms > MAX_REASONABLE_RESPONSE_MS,
            })
            raise

        # 流式/长连接（SSE、导出等）：记录的是"连接存活时长"，不是服务器处理耗时，
        # 故 response_time_ms 记 NULL 并标 is_streaming=1，避免污染平均/分位统计。
        content_type = (response.headers.get("content-type") or "").lower()
        is_streaming = content_type.startswith("text/event-stream")
        if is_streaming:
            response_ms = None
            is_outlier = False
        else:
            ms = round((time.time() - start_time) * 1000, 2)
            response_ms = ms
            is_outlier = ms > MAX_REASONABLE_RESPONSE_MS

        call_log_writer.enqueue({
            "endpoint": path,
            "method": request.method,
            "status_code": response.status_code,
            "response_time_ms": response_ms,
            "is_success": _is_success(response.status_code),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "params_summary": _sanitize_params(str(request.url.query)),
            "is_streaming": is_streaming,
            "is_outlier": is_outlier,
        })

        return response
