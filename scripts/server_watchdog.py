#!/usr/bin/env python3
"""DAPENG Alpha 服务看门狗。

职责：
1. 以 subprocess 启动 uvicorn（cwd=项目根目录，解释器自动探测，见 resolve_python）；
2. 子进程异常退出时按递增退避自动重启（5s -> 10s -> 30s -> 60s 封顶），
   子进程稳定运行超过 STABLE_SECONDS 后退避重置；
3. 单实例保护：启动时原子独占守卫端口 127.0.0.1:8899，失败即干净退出
   （业务端口 8900 的探测仅作辅助日志，不据此退出）；
4. 心跳自愈：后台线程每 HEALTH_INTERVAL 秒请求 /api/health，连续
   HEALTH_FAIL_THRESHOLD 次失败即判定「进程活着但服务卡死」，杀掉子进程
   交给主循环重启（仅有进程退出检测时，卡死会一直挂着没人管）；
5. 优雅退出：收到 SIGTERM / SIGINT(CTRL_C) 时先终止子进程再退出。

日志：
- logs/watchdog.log  看门狗自身日志（含重启计数与原因）
- logs/server.log    uvicorn 的 stdout/stderr（追加模式）

本脚本不修改 server/ 下任何业务代码。
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 常量配置
# ---------------------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def _load_env_file() -> None:
    """读项目 .env，补进 os.environ（已存在的环境变量优先）。

    看门狗是独立进程，不经过 FastAPI 的 dotenv 加载；不读 .env 的话 HOST/PORT
    会退回写死的 127.0.0.1，表现为「改了 .env 却还只能本机访问」。
    """
    path = PROJECT_ROOT / ".env"
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


_load_env_file()

# ---------------------------------------------------------------------------
# 解释器解析：不再写死单一路径，启动时按候选列表自动探测 + 实跑验证
# ---------------------------------------------------------------------------

HOST: str = (os.getenv("HOST") or "127.0.0.1").strip() or "127.0.0.1"
PORT: int = int((os.getenv("PORT") or "8900").strip() or 8900)
# 单实例守卫只绑回环：它只是互斥锁，不该跟着业务地址暴露到局域网
GUARD_HOST: str = "127.0.0.1"
# 单实例原子互斥守卫端口（与业务端口 8900 区分，避免干扰服务本身）
GUARD_PORT: int = 8899
# 用户级目录从环境变量推导，不写死机器路径
_HOME: Path = Path.home()
_LOCALAPPDATA: Path = Path(os.environ.get("LOCALAPPDATA") or (_HOME / "AppData" / "Local"))
_WORKBUDDY_HOME: Path = _HOME / ".workbuddy"
# 历史 workbuddy 托管 env（可能已被清理，仅作候选之一）
LEGACY_ENV_PYTHON: Path = (
    _WORKBUDDY_HOME / "binaries" / "python" / "envs" / "default" / "Scripts" / "python.exe"
)
# 系统 Python 3.11（常见安装位置，仅作候选）
SYSTEM_PY311: Path = _LOCALAPPDATA / "Programs" / "Python" / "Python311" / "python.exe"
# 托管运行时目录：其下各版本子目录的 python.exe 也纳入探测
PYTHON_VERSIONS_DIR: Path = _WORKBUDDY_HOME / "binaries" / "python" / "versions"

LOG_DIR: Path = PROJECT_ROOT / "logs"
WATCHDOG_LOG: Path = LOG_DIR / "watchdog.log"
SERVER_LOG: Path = LOG_DIR / "server.log"
# server.log 大小上限，超过则单备份轮转为 server.log.old，防止无限膨胀
SERVER_LOG_MAX_BYTES: int = 5 * 1024 * 1024
SERVER_LOG_BAK: Path = LOG_DIR / "server.log.old"

# uvicorn 启动命令在启动时基于探测到的解释器构造（见 _build_uvicorn_cmd）。

# 递增退避（秒），封顶 60s
BACKOFF_STEPS = [5, 10, 30, 60]
# 子进程存活超过该时长视为稳定，退避重置
STABLE_SECONDS: float = 120.0
# terminate 后等待子进程优雅退出的秒数，超时则 kill
STOP_TIMEOUT: float = 15.0
# 子进程存活轮询间隔
POLL_INTERVAL: float = 0.5

# ── 心跳自愈 ──
# 探测间隔（秒）：60s 一次，对服务压力可忽略
HEALTH_INTERVAL: float = float((os.getenv("HEALTH_INTERVAL") or "60").strip() or 60)
# 单次探测超时（秒）：健康检查本身很快，超过说明服务已无响应
HEALTH_TIMEOUT: float = float((os.getenv("HEALTH_TIMEOUT") or "10").strip() or 10)
# 连续失败阈值：3 次 × 60s ≈ 3 分钟无响应才动手，避免扫描高峰误杀
HEALTH_FAIL_THRESHOLD: int = int((os.getenv("HEALTH_FAIL_THRESHOLD") or "3").strip() or 3)
# 启动宽限期（秒）：冷启动要加载行情与覆盖数据，期间不判失败
HEALTH_GRACE: float = float((os.getenv("HEALTH_GRACE") or "180").strip() or 180)

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------

_stop_event = threading.Event()
_child: Optional[subprocess.Popen] = None
_child_lock = threading.Lock()
# 子进程启动时刻：心跳线程据此跳过冷启动宽限期
_child_started_at: float = 0.0


def _setup_logger() -> logging.Logger:
    """初始化看门狗日志（滚动文件 + 控制台，pythonw 下无控制台则跳过）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("dapeng.watchdog")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = RotatingFileHandler(
        WATCHDOG_LOG,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # pythonw.exe 下 sys.stdout/sys.stderr 为 None，直接跳过控制台输出
    if sys.stdout is not None:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    return logger


def _port_in_use(host: str, port: int, timeout: float = 1.5) -> bool:
    """检测 host:port 是否已被监听（仅用于辅助日志，不作为单实例唯一依据）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _iter_python_candidates() -> list[Path]:
    """按优先级产出待探测的解释器候选路径（自动去重）。

    顺序：
    1. 项目自带 .venv（未来预留）；
    2. 历史 workbuddy 托管 env；
    3. 当前进程解释器 sys.executable（若本身可用）；
    4. 系统 Python 3.11（当前唯一可用）；
    5. 托管运行时目录下各版本子目录的 python.exe。
    """
    candidates: list[Path] = []

    def _add(path: Optional[Path]) -> None:
        if path is None:
            return
        try:
            candidate = Path(path)
        except (TypeError, ValueError):
            return
        if candidate not in candidates:
            candidates.append(candidate)

    _add(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
    _add(LEGACY_ENV_PYTHON)
    if sys.executable:
        exe = Path(sys.executable)
        # 若当前进程是 pythonw.exe，优先改用同目录的 python.exe 作为子进程解释器：
        # 子进程为控制台程序时 CREATE_NO_WINDOW 才能真正抑制控制台窗口；
        # 同时便于 stop.bat 按 "python.exe + uvicorn + server.main" 特征精确匹配。
        if exe.name.lower() == "pythonw.exe":
            _add(exe.with_name("python.exe"))
        _add(exe)
    _add(SYSTEM_PY311)
    try:
        if PYTHON_VERSIONS_DIR.is_dir():
            for child in sorted(PYTHON_VERSIONS_DIR.glob("*")):
                _add(child / "python.exe")
    except OSError:
        pass

    return candidates


def _verify_python(candidate: Path) -> bool:
    """实跑验证候选解释器能否 import fastapi/uvicorn。"""
    if not candidate.exists():
        return False
    try:
        result = subprocess.run(
            [str(candidate), "-c", "import fastapi, uvicorn"],
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def resolve_python(logger: logging.Logger) -> Optional[Path]:
    """按优先级探测并返回第一个通过验证的解释器；全部失败返回 None。

    每个候选都会实跑一次 `python -c "import fastapi, uvicorn"`，返回第一个
    returncode == 0 的候选。全部失败时记录清晰错误日志（列出试过的路径与
    各自失败原因），由调用方决定退出，绝不静默失败。
    """
    tried: list[tuple[Path, str]] = []
    for candidate in _iter_python_candidates():
        if not candidate.exists():
            tried.append((candidate, "路径不存在"))
            continue
        if _verify_python(candidate):
            logger.info("选用解释器：%s", candidate)
            return candidate
        tried.append((candidate, "无法 import fastapi/uvicorn"))

    logger.error("未找到可用解释器（需能 import fastapi, uvicorn），已尝试以下候选：")
    for path, reason in tried:
        logger.error("  - %s : %s", path, reason)
    return None


def _build_uvicorn_cmd(python: Path) -> list[str]:
    """构造 uvicorn 启动命令（python 由启动时自动探测得到）。"""
    return [
        str(python),
        "-m",
        "uvicorn",
        "server.main:app",
        "--host",
        HOST,
        "--port",
        str(PORT),
    ]


def _acquire_instance_lock(logger: logging.Logger) -> Optional[socket.socket]:
    """原子互斥：尝试独占绑定守卫端口 GUARD_PORT。

    绑定成功则返回持有的 socket（调用方须保活到进程退出，否则锁会被释放）；
    绑定失败说明已有实例在运行，返回 None。相比 TOCTOU 端口探测，bind 本身
    是原子操作，可彻底避免两个看门狗同时判定"空闲"而并存。
    """
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        guard.bind((GUARD_HOST, GUARD_PORT))
        # backlog 给足：守卫 socket 不 accept，任何 connect 探测都会排在队列里，
        # backlog=1 时第二次探测就会被拒，外部监控会误判「看门狗已死」。
        guard.listen(16)
        logger.info("已获取单实例守卫锁 %s:%d", HOST, GUARD_PORT)
        return guard
    except OSError as exc:
        logger.warning(
            "守卫端口 %s:%d 绑定失败，疑似已有看门狗实例在运行：%s",
            HOST,
            GUARD_PORT,
            exc,
        )
        guard.close()
        return None


def _install_signal_handlers(logger: logging.Logger) -> None:
    """注册 SIGTERM / SIGINT 处理，置位停止事件（Windows 下注册安全）。"""

    def _handler(signum: int, _frame: object) -> None:
        logger.info("收到退出信号 signum=%s，准备停止...", signum)
        _stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (OSError, ValueError, RuntimeError):
            # 某些平台/线程环境下注册失败不影响主流程
            pass


def _open_server_log():
    """打开 logs/server.log 前做大小轮转（单备份），防止请求日志无限膨胀。

    超过 SERVER_LOG_MAX_BYTES 时：先删除已存在的 server.log.old，
    再把当前 server.log 轮转为 server.log.old，最后以追加模式打开新文件。
    轮转失败（如文件被占用）不阻断启动。
    """
    try:
        if SERVER_LOG.exists() and SERVER_LOG.stat().st_size >= SERVER_LOG_MAX_BYTES:
            if SERVER_LOG_BAK.exists():
                SERVER_LOG_BAK.unlink()
            os.replace(SERVER_LOG, SERVER_LOG_BAK)
    except OSError:
        pass
    return open(SERVER_LOG, "ab")  # noqa: SIM115 - 生命周期跟随子进程


def _probe_health(timeout: float = HEALTH_TIMEOUT) -> tuple[bool, str]:
    """请求本机 /api/health，返回 (是否健康, 说明)。

    只认 HTTP 200 且 body 里 ok 为真；连接被拒、超时、5xx、返回 HTML
    （典型是卡在中间件或反代异常页）都算不健康。
    """
    url = f"http://127.0.0.1:{PORT}/api/health"
    req = urllib.request.Request(url, headers={"User-Agent": "dapeng-watchdog/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            raw = resp.read(4096)
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # URLError / 超时 / 连接重置
        return False, f"{type(exc).__name__}: {exc}"
    try:
        payload = json.loads(raw.decode("utf-8", "ignore"))
    except ValueError:
        return False, "响应非 JSON（可能命中了异常页）"
    if not isinstance(payload, dict) or not payload.get("ok"):
        return False, "ok=false"
    return True, "ok"


def _heartbeat_loop(logger: logging.Logger) -> None:
    """后台心跳：连续失败达阈值就杀掉子进程，让主循环重新拉起。

    设计取舍：
    - 只在子进程存在且已过宽限期时探测，避免冷启动阶段自己把自己判死；
    - 用「连续 N 次失败」而非单次失败，避开全市场扫描时的瞬时卡顿；
    - 探测失败但进程已退出时不重复 kill，交给主循环处理。
    """
    fails = 0
    while not _stop_event.wait(HEALTH_INTERVAL):
        with _child_lock:
            proc = _child
        if proc is None or proc.poll() is not None:
            fails = 0
            continue
        if time.time() - _child_started_at < HEALTH_GRACE:
            continue

        ok, detail = _probe_health()
        if ok:
            if fails:
                logger.info("心跳恢复正常（此前连续失败 %d 次）", fails)
            fails = 0
            continue

        fails += 1
        logger.warning(
            "心跳失败 %d/%d：%s", fails, HEALTH_FAIL_THRESHOLD, detail
        )
        if fails < HEALTH_FAIL_THRESHOLD:
            continue

        logger.error(
            "连续 %d 次心跳失败，判定服务卡死（pid=%d 仍在运行），强制重启",
            fails,
            proc.pid,
        )
        fails = 0
        try:
            proc.kill()
        except OSError:
            pass


def _start_heartbeat(logger: logging.Logger) -> threading.Thread:
    thread = threading.Thread(
        target=_heartbeat_loop, args=(logger,), name="dapeng-heartbeat", daemon=True
    )
    thread.start()
    return thread


def _start_child(logger: logging.Logger, python: Path) -> subprocess.Popen:
    """启动 uvicorn 子进程，stdout/stderr 追加写入 logs/server.log。"""
    server_log = _open_server_log()
    # 父进程可能是无控制台的 pythonw.exe；若不给控制台子进程加 CREATE_NO_WINDOW，
    # Windows 会自动新建一个可见控制台窗口，用户关闭该窗口会使子进程收到
    # CTRL_CLOSE_EVENT 被杀死（退出码 0xC000013A / STATUS_CONTROL_C_EXIT）。
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(
            _build_uvicorn_cmd(python),
            cwd=str(PROJECT_ROOT),
            stdout=server_log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
    except Exception:
        server_log.close()
        raise

    with _child_lock:
        global _child, _child_started_at
        _child = proc
        _child_started_at = time.time()

    logger.info("uvicorn 已启动 pid=%d，日志 -> %s", proc.pid, SERVER_LOG)
    return proc


def _terminate_child(logger: logging.Logger) -> None:
    """优雅终止当前子进程：先 terminate，超时后 kill。"""
    with _child_lock:
        proc = _child
        _child = None

    if proc is None or proc.poll() is not None:
        return

    logger.info("正在终止 uvicorn pid=%d ...", proc.pid)
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=STOP_TIMEOUT)
        logger.info("uvicorn pid=%d 已优雅退出", proc.pid)
    except subprocess.TimeoutExpired:
        logger.warning("uvicorn pid=%d 未在 %.0fs 内退出，强制 kill", proc.pid, STOP_TIMEOUT)
        try:
            proc.kill()
            proc.wait(timeout=5)
        except OSError:
            pass


def _run_child_until_exit(logger: logging.Logger, proc: subprocess.Popen) -> int:
    """等待子进程退出；若期间收到停止事件则先终止子进程。"""
    while proc.poll() is None:
        if _stop_event.wait(POLL_INTERVAL):
            _terminate_child(logger)
            break
    returncode = proc.poll()
    if returncode is None:  # 理论上不会发生，保底
        returncode = -1
    return returncode


def main() -> int:
    logger = _setup_logger()
    logger.info("=" * 60)
    logger.info("DAPENG Alpha 看门狗启动 pid=%d", _get_own_pid())

    # 单实例保护：原子互斥锁（守卫端口 8899）。
    # bind 为原子操作，两个看门狗并发启动时后到者必然失败并干净退出。
    # guard_socket 持有到 main() 结束（进程退出）以确保锁不被提前释放。
    guard_socket = _acquire_instance_lock(logger)
    if guard_socket is None:
        logger.warning(
            "已有 DAPENG 看门狗实例在运行（守卫端口 %d 被占用），本进程退出。",
            GUARD_PORT,
        )
        return 0

    # 辅助日志：业务端口 8900 若已被监听仅作提示，不再据此退出（由守卫锁负责单实例）。
    if _port_in_use(HOST, PORT):
        logger.info("提示：%s:%d 当前已被监听。", HOST, PORT)

    # 解释器自动探测：全部候选失败则记录清晰错误并退出，绝不静默失败。
    python = resolve_python(logger)
    if python is None:
        logger.error("解释器探测失败，看门狗退出。")
        guard_socket.close()
        return 1

    _install_signal_handlers(logger)
    _start_heartbeat(logger)
    logger.info(
        "心跳自愈已启用：每 %.0fs 探测 %s，连续 %d 次失败重启（冷启动宽限 %.0fs）",
        HEALTH_INTERVAL,
        f"127.0.0.1:{PORT}/api/health",
        HEALTH_FAIL_THRESHOLD,
        HEALTH_GRACE,
    )

    restart_count = 0
    backoff_idx = 0

    while not _stop_event.is_set():
        started_at = time.time()
        try:
            proc = _start_child(logger, python)
        except Exception as exc:  # 启动失败也按崩溃处理，退避重试
            restart_count += 1
            delay = BACKOFF_STEPS[min(backoff_idx, len(BACKOFF_STEPS) - 1)]
            backoff_idx += 1
            logger.error(
                "uvicorn 启动失败：%s（第 %d 次重启，%ds 后重试）",
                exc,
                restart_count,
                delay,
            )
            if _stop_event.wait(delay):
                break
            continue

        returncode = _run_child_until_exit(logger, proc)

        if _stop_event.is_set():
            break

        uptime = time.time() - started_at
        restart_count += 1
        if uptime >= STABLE_SECONDS:
            backoff_idx = 0  # 稳定运行过，退避重置
        delay = BACKOFF_STEPS[min(backoff_idx, len(BACKOFF_STEPS) - 1)]
        backoff_idx += 1
        logger.warning(
            "uvicorn 异常退出 code=%s（存活 %.1fs），第 %d 次重启，%ds 后拉起",
            returncode,
            uptime,
            restart_count,
            delay,
        )
        if _stop_event.wait(delay):
            break

    _terminate_child(logger)
    guard_socket.close()
    logger.info("看门狗退出，累计重启 %d 次", restart_count)
    return 0


def _get_own_pid() -> int:
    return os.getpid()


if __name__ == "__main__":
    sys.exit(main())
