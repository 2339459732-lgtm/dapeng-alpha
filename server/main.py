"""DAPENG Alpha 策略选股系统 - FastAPI 入口

优化点：
- 启动时后台预热K线数据（不缓存不完整的行情）
- 健康检查接口
- 自动加载 .env 环境变量（v4.0 安全优化）
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

# ---- 行情/资讯数据源直连：绕过系统代理 ----
# 本机浏览器可能配置了代理（如 127.0.0.1:10809）用于访问境外站点，
# 但腾讯/东财/AkShare 等境内数据源必须直连，否则会被代理拖慢或直接失败。
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

# ---- 加载 .env 文件（零依赖）----
def _load_dotenv():
    """从项目根目录 .env 文件加载环境变量"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key and key not in os.environ:
                    os.environ[key] = value

_load_dotenv()
# ---- .env 加载完毕 ----

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from server.cache import cache
from server.config import CACHE_TTL_KLINE, KLINE_LIMIT, PRELOAD_DELAY, PRELOAD_SECTORS, get_sector_stocks
from server.routers import analyze, global_market, kline, quotes, scan, search, realtime
from server.routers import agents as agents_router
from server.routers import payment as payment_router
from server.routers import admin as admin_router
from server.routers import quant as quant_router
from server.routers import market_data as market_data_router
from server.routers import market as market_router  # 全市场排行 + 沪深港通资金流向（历史上从未挂载）
from server.routers import market_dashboard as market_dashboard_router
from server.routers import strategy_lab as strategy_lab_router
from server.routers import intelligent_router
from server.routers import automation as automation_router
from server.routers import review as review_router
from server.routers import evolution as evolution_router
from server.routers import brain as brain_router
from server.routers import chart_analysis as chart_analysis_router
from server.routers import stream as stream_router  # 行情/信号实时推送（SSE）
from server.routers import meta as meta_router  # T01：统一能力元信息（/api/meta/capability）
from server.routers import risk as risk_router  # T04：组合风险视图（/api/risk/portfolio）
from server.routers import ict as ict_router  # ICT 模型扫描（/api/ict/scan）
from server.routers import etf as etf_router  # P1-3：ETF 双动量轮动（/api/etf/rotation）
from server.routers import themes as themes_router  # 决策台：板块趋势 / 字辈题材雷达（/api/themes）
from server.routers import tdx as tdx_router  # 通达信本地数据（vipdoc，离线）
from server.routers import datasources as datasources_router  # 数据源健康检查（/api/data-sources）
from server.routers import decision as decision_router  # 决策闭环：快照/情绪/专家/全市场覆盖
from server.api_tracking import APITrackingMiddleware, call_log_writer

# 护城河：配置 / 日志 / 安全中间件 / 系统端点
from server.infra.settings import get_settings
from server.infra.logging_setup import setup_logging
from server.infra.security import MoatMiddleware, set_moat_middleware
from server.infra.auth import AccessKeyMiddleware
from server.infra import auth as auth_module
from server.routers import auth as auth_router
from server.infra.health import router as system_router
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from server.contracts.envelope import SCHEMA_HEADER, SCHEMA_VERSION

logger = logging.getLogger(__name__)

setup_logging()


async def _preload_data():
    """后台预热：提前获取热门板块的K线数据

    v4.5 优化：使用腾讯HTTP（毫秒级），每批10只并发
    """
    await asyncio.sleep(PRELOAD_DELAY)
    from server.tencent_kline import fetch_tencent_kline

    for sector_key in PRELOAD_SECTORS:
        try:
            logger.info("Preloading klines for sector: %s", sector_key)
            codes = [stock[0] for stock in get_sector_stocks(sector_key)]

            batch_size = 10
            cached_count = 0
            for i in range(0, len(codes), batch_size):
                batch = codes[i:i + batch_size]
                tasks = [fetch_tencent_kline(code, period="day", limit=KLINE_LIMIT) for code in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for j, code in enumerate(batch):
                    klines = results[j]
                    if klines and not isinstance(klines, Exception):
                        cache.set(f"raw_kline:{code}:day", klines, CACHE_TTL_KLINE)
                        cached_count += 1

            logger.info("Preloaded sector %s: %d/%d klines cached",
                       sector_key, cached_count, len(codes))
        except Exception as e:
            logger.warning("Preload failed for %s: %s", sector_key, str(e))


async def _start_core_agents():
    """v4.0: 自动启动核心监控Agent"""
    await asyncio.sleep(8)  # 等服务完全就绪
    try:
        from server.agents.orchestrator import start_agent
        from server.agents.alert_agent import get_alert_agent

        # 启动告警Agent（监控自选股异动）
        alert = get_alert_agent()
        loop = asyncio.get_running_loop()
        loop.create_task(alert.start())
        logger.info("Core agents started")
    except Exception as e:
        logger.warning("Agent startup failed: %s", str(e))


async def _preload_stock_pool():
    """v5.0: 后台预热动态股票池（三层架构）"""
    await asyncio.sleep(PRELOAD_DELAY + 2)
    try:
        from server.stock_pool import stock_pool
        await stock_pool.initialize()
        logger.info("Stock pool initialized: %s", stock_pool.get_pool_stats())
    except Exception as e:
        logger.warning("Stock pool preload failed: %s", str(e))


def _log_task_exception(task: "asyncio.Task") -> None:
    """后台任务兜异常：任务异常退出时记录错误，避免 'Task exception was never retrieved'
    以及任务静默死亡（如 trading_facade.start() 初始化失败导致交易链路整体不可用）。"""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error("后台任务异常退出：%s", exc, exc_info=exc)


async def _warm_sector_ranking() -> None:
    """启动预热板块排行：失败不抛、只留痕（预热是优化，不是依赖）。"""
    try:
        from server.stable_market_provider import stable_provider
        from server.cache import cache
        from server.config import CACHE_TTL_SECTOR_RANK
        data = await stable_provider.get_sector_ranking_data()
        if isinstance(data, list) and data:
            cache.set("sector_ranking_v2", data, CACHE_TTL_SECTOR_RANK)
            logger.info("sector ranking prewarmed: %d sectors", len(data))
        else:
            logger.info("sector ranking prewarm returned no data (will fall back at request time)")
    except Exception as exc:  # noqa: BLE001
        logger.info("sector ranking prewarm failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理 — v4.0 增强：预热 + Agent自动启动"""
    # 启动后：API调用追踪写入器 + 后台预热K线 + 自动启动核心Agent + 股票池预热
    await call_log_writer.start()
    preload_task = asyncio.create_task(_preload_data())
    agents_task = asyncio.create_task(_start_core_agents())
    pool_task = asyncio.create_task(_preload_stock_pool())
    from server.trading import trading_facade
    trading_task = asyncio.create_task(trading_facade.start())
    # AI 主脑（任务调度 + 策略优化 + 持续学习）
    from server.brain import dapeng_brain
    brain_task = asyncio.create_task(dapeng_brain.start())
    # 缓存后台回收：与写入解耦，空闲期也能清掉过期条目（否则过期条目长期占容量）
    from server.cache import cache
    cache_task = cache.start_background_cleanup()
    # 板块排行预热：push2 被阻断时首次同步拉取要 11–19 秒（多 host 重试），
    # 若等用户点开板块页才付这笔钱，首屏就是十几秒白屏。启动后台跑一次，
    # 并让熔断器先打开，之后所有请求都是毫秒级 + 快照兜底。
    sector_task = asyncio.create_task(_warm_sector_ranking())
    # 决策台题材雷达预热：全市场新浪快照约 5-6 秒，启动后台拉一次，避免首开白屏
    themes_task = asyncio.create_task(asyncio.to_thread(themes_router.warm_themes_cache))
    # 全市场行情/基本面与全球宏观：后台持续刷新（交易时段 120s，非交易时段 30min）
    # 全局数据总线：注册决策闭环资源图（coverage → packet → sentiment/scan → experts），
    # 供级联失效、SSE 事件推送与 /api/decision/bus 观测使用。
    try:
        from server.data_bus import register_default_resources

        register_default_resources()
    except Exception as exc:
        logging.getLogger(__name__).warning("data bus registration failed: %s", exc)

    from server.market_coverage import start_coverage_worker, stop_coverage_worker
    coverage_task = start_coverage_worker()
    # 任一后台任务异常退出都要留痕（否则任务静默死亡、上层无感知）
    for _t in (preload_task, agents_task, pool_task, trading_task, brain_task, sector_task, coverage_task):
        _t.add_done_callback(_log_task_exception)
    if cache_task is not None:
        cache_task.add_done_callback(_log_task_exception)
    yield
    # 关闭前：先停缓存回收，再清理其余任务
    try:
        await cache.stop_background_cleanup()
    except Exception:
        pass
    # 关闭前：清理所有任务
    await dapeng_brain.stop()
    await stop_coverage_worker()
    for task in [preload_task, agents_task, pool_task, trading_task, brain_task, sector_task]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    # 停止API调用追踪写入器（flush剩余记录）
    await call_log_writer.stop()
    # 停止所有运行中的Agent
    try:
        from server.agents.orchestrator import stop_all
        await stop_all()
    except Exception:
        pass


app = FastAPI(title="DAPENG Alpha", version="4.0", lifespan=lifespan)

# CORS 中间件：控制面板同源运行，不向任意网站开放管理请求
_cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:8900,http://127.0.0.1:8900",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Token"],
)


# ── 缓存控制中间件 ──
# HTML 文件: no-store（每次都拉最新）
# JS/CSS:   no-cache（可缓存但使用前必须 revalidate）
# 图片/字体: max-age=3600（1小时缓存）
class NoCacheHTMLMiddleware(BaseHTTPMiddleware):
    """防止 HTML/JS/CSS 被浏览器强缓存，确保用户始终看到最新版本"""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        url_path = request.url.path

        if url_path.endswith('.html') or url_path == '/' or url_path == '':
            # HTML 文件完全不缓存
            response.headers['Cache-Control'] = 'no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        elif url_path.endswith('.js') or url_path.endswith('.css'):
            # JS/CSS 允许缓存但必须 revalidate
            response.headers['Cache-Control'] = 'no-cache, must-revalidate'
        elif any(url_path.endswith(ext) for ext in ('.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.woff', '.woff2', '.ttf')):
            # 静态资源缓存1小时
            response.headers['Cache-Control'] = 'public, max-age=3600'

        return response


app.add_middleware(NoCacheHTMLMiddleware)


# ── 安全响应头 ──
# 目的不是「凑齐 OWASP 清单」，而是补齐这三类真实风险：
#   1. MIME 嗅探导致上传/静态文件被当脚本执行 → nosniff
#   2. 页面被第三方站点 iframe 套壳点击（本地管理台尤其怕） → frame-ancestors/X-Frame-Options
#   3. 跳转/引用把本地地址带到外站 → Referrer-Policy
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """统一注入基础安全头。CSP 暂不启用：本系统页面大量使用内联脚本与样式，
    一条不精确的 CSP 会直接打白，且无法在无前端构建的环境里验证。"""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        return response


app.add_middleware(SecurityHeadersMiddleware)

# ── Host 头校验（防 DNS 重绑定）──
# 服务只监听 127.0.0.1，但浏览器里的恶意页面可以把自有域名解析到 127.0.0.1 再发请求：
# 连接确实来自本机，`request.client.host` 也是 127.0.0.1，仅靠「本机来源」判断会被绕过。
# 校验 Host 头能挡住这类请求（它们带的 Host 是攻击者域名）。
_allowed_hosts = [
    host.strip()
    for host in os.getenv(
        "ALLOWED_HOSTS",
        "127.0.0.1,localhost,[::1],testserver",
    ).split(",")
    if host.strip()
]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

# 文本/JSON 压缩（>1KB 才压，避免小响应反而变大）
app.add_middleware(GZipMiddleware, minimum_size=1024)

# API调用追踪中间件 — 零侵入记录所有 /api/* 请求
app.add_middleware(APITrackingMiddleware)

# 注册路由
app.include_router(quotes.router, prefix="/api")
app.include_router(kline.router, prefix="/api")
app.include_router(global_market.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(analyze.router, prefix="/api")
app.include_router(scan.router, prefix="/api")
# market_data 先于 agents 注册：否则 /api/news/market 被 agents 的
# /api/news/{code} 抢先匹配（且对不存在标的编造情绪）。
app.include_router(market_data_router.router, prefix="/api")
app.include_router(agents_router.router, prefix="/api")
app.include_router(realtime.router, prefix="/api")
app.include_router(payment_router.router)
app.include_router(admin_router.router)
app.include_router(quant_router.router)
app.include_router(market_dashboard_router.router)
app.include_router(strategy_lab_router.router)
app.include_router(intelligent_router.router)
app.include_router(automation_router.router, prefix="/api/automation")
app.include_router(review_router.router, prefix="/api")
app.include_router(evolution_router.router, prefix="/api")
app.include_router(brain_router.router)
# 决策闭环：/api/decision/snapshot|sentiment|experts|coverage|universe|coverage/stream
app.include_router(decision_router.router)
app.include_router(chart_analysis_router.router)  # 路由自带 /api/analyze 前缀
# 修复：前端 app.js 长期请求 /api/market/north 却恒定 404 —— 该路由模块此前
# 从未被注册（且旧版 import 了不存在的单例，挂载即崩）。现已修正并挂载。
app.include_router(market_router.router, prefix="/api/market")
# 行情与信号实时推送（SSE）：/api/stream、/api/stream/market、/api/stream/signals、/api/stream/stats
app.include_router(stream_router.router)
# 统一能力元信息（T01）：/api/meta/capability —— 前端角标唯一事实源，只读缓存不触发上游
app.include_router(meta_router.router)
# 组合风险视图（T04）：/api/risk/portfolio —— β/R²/特质风险 + 夏普/索提诺/Omega/CVaR95
app.include_router(risk_router.router)
# ICT 模型（第一阶段：只读扫描 + 注解，不接决策闸）
app.include_router(ict_router.router)
# ETF 双动量轮动（P1-3）：/api/etf/rotation —— 当前/目标持仓 + 动量排名 + 参数平原
app.include_router(etf_router.router, prefix="/api")
# 决策台题材雷达：/api/themes/overview /board/{code} /char/{char}
app.include_router(themes_router.router)
# 通达信本地盘后数据（离线）：/api/tdx/status /kline
app.include_router(tdx_router.router)
# 数据源健康检查：/api/data-sources/health
app.include_router(datasources_router.router)

# 护城河：/healthz /readyz /api/system/*（存活、就绪、状态、完整性、备份）
app.include_router(system_router)


@app.get("/api/health")
async def health_check():
    """健康检查接口

    ``cache_size`` 保留原字段（兼容既有调用方）；``cache`` 提供完整运行态指标，
    其中 ``expired_now`` 若长期偏高说明后台回收任务未运行。
    """
    st = cache.stats()
    return {
        "ok": True,
        "version": "4.0",
        "cache_size": st["size"],
        "cache": st,
    }


@app.get("/api/sectors")
async def get_sectors():
    """返回所有板块元数据，包含股票数量。

    信封统一：既保留顶层 ``sectors``（既有消费方在用），也提供 ``data`` 别名，
    让所有板块相关端点都能按 ``{ok, data}`` 统一读取。属于**加字段**，不破坏旧代码。
    """
    from server.config import SECTORS
    items = [
        {
            "key": key,
            "name": data["name"],
            "icon": data["icon"],
            "stock_count": len(data["stocks"]),
        }
        for key, data in SECTORS.items()
    ]
    return {"ok": True, "sectors": items, "data": items, "count": len(items)}


@app.get("/api/debug-kline")
async def debug_kline(code: str = "sh600519"):
    """开发调试接口：仅 DEBUG=1 时启用，生产环境不暴露堆栈。"""
    if os.getenv("DEBUG", "0").lower() not in {"1", "true", "yes"}:
        return JSONResponse(status_code=404, content={"ok": False, "message": "接口不存在"})

    from server.tencent_kline import fetch_tencent_kline
    try:
        klines = await fetch_tencent_kline(code, period="day", limit=10)
        if klines:
            return {"ok": True, "count": len(klines), "first": klines[0], "last": klines[-1]}
        return {"ok": True, "count": 0, "message": "未返回K线数据"}
    except Exception:
        logger.exception("debug-kline failed for %s", code)
        return JSONResponse(status_code=500, content={"ok": False, "message": "调试请求失败，请查看服务端日志"})


# 静态文件挂载（顺序敏感：Starlette 按注册顺序匹配，catch-all "/" 必须最后注册）
# 目录改为基于项目根动态解析的绝对路径，避免 cwd 变化导致 404
# （与 server/config.py、server/westock.py 的历史缺陷同型）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATIC_DIR = _PROJECT_ROOT / "static"
_DOCS_DIR = _PROJECT_ROOT / "docs"

# /docs 必须在 catch-all "/" 之前注册，否则会被 "/" 吃掉 → 恒定 404

# ── 护城河安全中间件（纯 ASGI，最后注册 = 最外层，保证所有响应都有安全头） ──
def _moat_factory(app, settings=None):
    mw = MoatMiddleware(app, settings=settings)
    set_moat_middleware(mw)
    return mw


app.add_middleware(_moat_factory, settings=get_settings())

# ── 部署护栏：非回环监听必须配访问密钥 ──
# 开源后最容易出的事故是「把 HOST 改成 0.0.0.0 忘了配 key」。这里直接拒绝启动，
# 比在日志里写一行警告可靠得多。确有特殊场景（内网沙箱）可显式设 ALLOW_INSECURE_BIND=1。
_boot_settings = get_settings()
# 统一护栏：要求强密钥时，缺失/过弱直接拒绝启动（REQUIRE_ACCESS_KEY=1）。
auth_module.startup_guard()
if not _boot_settings.bind_local_only and not auth_module.auth_enabled():
    if os.getenv("ALLOW_INSECURE_BIND", "").strip() != "1":
        raise RuntimeError(
            "拒绝启动：HOST 监听非回环地址但鉴权未开启。\n"
            "  请在 .env 配置 ACCESS_KEY（≥32 位随机串），或移除 AUTH_DISABLED=1。\n"
            "  生成命令：python -c \"import secrets;print(secrets.token_urlsafe(48))\""
        )
if auth_module.auth_enabled():
    _weak = auth_module.key_strength_issue()
    if _weak:
        logger.warning("访问密钥强度不足：%s", _weak)

# 访问密钥闸门：未配置 ACCESS_KEY 时自动关闭（本地开发/测试无感）。
# 注册在 moat 之前 → moat 在外层先做路径卫生与限流，再做鉴权判定。
app.add_middleware(AccessKeyMiddleware)
app.include_router(auth_router.router)
app.include_router(auth_router.login_page)

app.mount("/docs", StaticFiles(directory=str(_DOCS_DIR), html=True), name="docs-files")
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")


@app.middleware("http")
async def global_error_handler(request: Request, call_next):
    """全局错误处理中间件 — 捕获所有未处理的异常"""
    import time
    start_time = time.time()

    try:
        response = await call_next(request)

        # 记录API请求日志
        process_time = time.time() - start_time
        logger.info("API: %s %s | %d | %.3fs",
                   request.method, request.url.path, response.status_code, process_time)

        # 信封版本对外可见（contract-envelope.v1），客户端据此做兼容判断
        response.headers[SCHEMA_HEADER] = SCHEMA_VERSION
        return response
    except Exception as e:
        process_time = time.time() - start_time
        logger.error("API Error: %s %s | %.3fs | %s",
                    request.method, request.url.path, process_time, str(e), exc_info=True)
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"ok": False, "data": None, "message": "服务器内部错误，请稍后重试",
                     "code": "internal_error", "schema_version": SCHEMA_VERSION},
            headers={SCHEMA_HEADER: SCHEMA_VERSION},
        )
