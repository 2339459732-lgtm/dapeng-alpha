"""DAPENG Alpha v5.0 - 市场数据 API 路由

提供 14 个 GET 端点，封装 WestockClient 的 MCP 数据源方法，
统一使用 cache.get_or_fetch() 进行缓存，返回 {"ok": True, "data": {...}} 格式。

财务基本面（P0-1）已切换到 AkShare 三源链路（新浪/百度/巨潮），见
``server/fundamental.py`` 与 ``docs/10-architecture/openbb-dsa-integration-design.md`` §3.2/§3.3。
"""

import logging
from fastapi import APIRouter, Query
from typing import Optional
import asyncio
from datetime import datetime

from server.cache import cache
from server.config import (
    CACHE_TTL_FUND_FLOW,
    CACHE_TTL_SECTOR_RANK,
    CACHE_TTL_SECTOR_STOCKS,
    CACHE_TTL_LHB,
    CACHE_TTL_MARKET_OVERVIEW,
    CACHE_TTL_HOT,
    CACHE_TTL_PROFILE,
    CACHE_TTL_FINANCE,
    CACHE_TTL_REPORT,
    CACHE_TTL_NORTH,
    CACHE_TTL_NEWS,
    get_sector_stocks,
)
from server.stock_pool import stock_pool
from server.westock import westock
from server.stable_market_provider import stable_provider, push2_breaker_state
from server.ak_market import ak_hub

# 财务基本面 v2（AkShare 三源链路）。延迟失败不阻断服务启动：
# 若 import 失败，端点仍可如实返回 ok:false + reason，而不是让整个 app 起不来。
try:
    from server.fundamental import (
        get_fundamental_snapshot,
        get_valuation_history,
        is_finance_v2_enabled,
    )
    _FUNDAMENTAL_IMPORT_ERROR: Optional[str] = None
except Exception as _fundamental_import_exc:  # noqa: BLE001
    get_fundamental_snapshot = None            # type: ignore[assignment]
    get_valuation_history = None               # type: ignore[assignment]
    is_finance_v2_enabled = lambda: False      # type: ignore[assignment]
    _FUNDAMENTAL_IMPORT_ERROR = (
        f"{type(_fundamental_import_exc).__name__}: {_fundamental_import_exc}"
    )

logger = logging.getLogger(__name__)

router = APIRouter()


_WESTOCK_DOWN_REASON = "westock CLI 链路已废弃（已迁移 MCP），数据源未接入"


def _westock_unavailable(reason: str = _WESTOCK_DOWN_REASON):
    """统一「数据源未接入」信封：ok:false + data:null + source:unavailable + reason。"""
    return {"ok": False, "data": None, "source": "unavailable", "reason": reason}




def _envelope_source(rows):
    """信封来源必须等于数据真实来源，绝不写死。

    参数 rows: 行记录列表（可为 None）。返回 (source, degraded)：
      - 任一行带 synthetic=True → 本地样本降级，degraded=True；
      - 任一行带 aggregated=True（腾讯成分股聚合口径）→ degraded=True，
        source 取行级 source（"腾讯行情聚合（东财板块源不可用）"）；
      - 任一行带 stale=True（真实来源但为历史快照）→ degraded=True，
        source 直接取行级 source（已内含历史日期）；
      - 无数据 → ("无数据", True)；
      - 否则取行级 source（无则 "未知来源"），degraded=False。

    说明：行级 source 是诚实的（兜底行为 "系统快照"，历史快照行会写明日期，
    聚合行会写明腾讯口径），信封不得再把它覆盖成假的。
    """
    rows = rows or []
    synthetic = any(isinstance(r, dict) and r.get("synthetic") for r in rows)
    if synthetic:
        return "系统快照（本地样本，非实时行情）", True
    if not rows:
        return "无数据", True
    src = next((r.get("source") for r in rows
                if isinstance(r, dict) and r.get("source")), None)
    src = src or "未知来源"
    aggregated = any(isinstance(r, dict) and r.get("aggregated") for r in rows)
    if aggregated:
        return src, True
    stale = any(isinstance(r, dict) and r.get("stale") for r in rows)
    if stale:
        # 真实来源但数据陈旧：source 已含历史日期，仍需置 degraded 提示
        return src, True
    return src, False


@router.get("/fund-flow")
async def get_fund_flow(code: str = Query(..., description="股票代码")):
    """个股资金流向（东财批量接口单只查询）。

    历史实现委托 westock CLI，该 CLI 已不可用（skill 迁移至 MCP），
    导致恒定返回 null 且以 ok:true 掩盖。现改用 AkShare/东财数据源。
    """
    async def _fetch():
        return await ak_hub.get_stock_fund_flow([code])

    rows = await cache.get_or_fetch(f"fund_flow_v2:{code}", _fetch, CACHE_TTL_FUND_FLOW)
    if rows is None:
        return {"ok": False, "data": None, "message": "个股资金流取数失败（数据源不可用或超时）"}
    return {"ok": True, "data": rows[0] if rows else None}


@router.get("/fund-flow/sector")
async def get_sector_fund_flow(sector: str = Query(..., description="板块key")):
    """板块资金流向汇总（东财个股资金流批量聚合）。

    返回结构设计为**同时满足两个既有前端消费方**：
      - `app.js` loadSectorFundFlow 需要顶层 super_large/large/medium/small/main
      - `sentiment.js` loadFundFlow 需要 summary.net_inflow/sh_inflow/sz_inflow/consecutive_days

    注意：sh_inflow/sz_inflow 是**沪市/深市**按代码前缀的聚合，不是沪股通/深股通，
    前端标签须如实标注。取数失败返回 ok:false（而非 ok:true+空数组）。
    """
    stocks = get_sector_stocks(sector)
    codes = [s[0] for s in stocks[:20]]

    async def _fetch():
        return await ak_hub.get_stock_fund_flow(codes)

    flow_key = f"sector_flow_v2:{sector}"
    entry = None
    try:
        entry = cache.get_entry(flow_key)
    except Exception:  # noqa: BLE001
        entry = None
    served_stale = False
    cached_fail = None
    if entry and entry.get("value"):
        val = entry["value"]
        if isinstance(val, dict) and val.get("__fail__"):
            # 负缓存命中：上游当前不可用时，20 秒内直接如实返回同一个原因，
            # 不再每次都付 3 秒超时（实测失败路径原本每次 3.2–3.7s）
            return {"ok": False, "data": None, "sector": sector,
                    "message": val.get("message") or "板块资金流取数失败",
                    "cached_failure": True, "retry_after": 20}
        # 有旧值先给旧值：实测冷启动 3.5s，用户不该为此白屏
        rows = val
        served_stale = bool(entry.get("expired"))
        if served_stale:
            _kick_refresh("flow:" + sector, flow_key, CACHE_TTL_FUND_FLOW, _fetch)
    else:
        rows = await cache.get_or_fetch(flow_key, _fetch, CACHE_TTL_FUND_FLOW)

    if rows is None:
        cache.set(flow_key, {"__fail__": True, "message": "板块资金流取数失败（数据源不可用或超时）"}, 20)
        return {
            "ok": False,
            "data": None,
            "sector": sector,
            "message": "板块资金流取数失败（数据源不可用或超时）",
            "retry_after": 20,
        }
    if not rows:
        cache.set(flow_key, {"__fail__": True, "message": f"板块 {sector} 无可用成分股资金流数据"}, 20)
        return {
            "ok": False,
            "data": None,
            "sector": sector,
            "message": f"板块 {sector} 无可用成分股资金流数据",
            "retry_after": 20,
        }

    def _sum(key: str, only=None) -> float:
        return round(sum(r.get(key, 0.0) for r in rows if (only is None or only(r))), 2)

    is_sh = lambda r: str(r.get("code", "")).startswith("sh")
    is_sz = lambda r: str(r.get("code", "")).startswith("sz")

    data = {
        "sector": sector,
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(rows),
        # app.js 需要的顶层聚合字段（单位：元）
        "main": _sum("main"),
        "super_large": _sum("super_large"),
        "large": _sum("large"),
        "medium": _sum("medium"),
        "small": _sum("small"),
        # sentiment.js 需要的 summary
        "summary": {
            "net_inflow": _sum("main"),
            "sh_inflow": _sum("main", is_sh),
            "sz_inflow": _sum("main", is_sz),
            "consecutive_days": None,   # 无连续净流入数据时如实置 None，前端应隐藏该项
            "scope": "沪深两市按代码前缀聚合（非沪股通/深股通）",
        },
        "stocks": rows,
    }
    return {"ok": True, "source": "东方财富 / 个股资金流", "data": data,
            "stale": served_stale,
            "stale_note": ("资金流为上一轮快照，后台正在刷新" if served_stale else None)}


_bg_refreshing = set()


def _kick_refresh(tag: str, key: str, ttl: int, fetch_fn) -> None:
    """通用 stale-while-revalidate 后台刷新：同一 tag 同时只跑一个。

    背景：东财通道被阻断时，首次拉取要 3.5–12 秒（多 host 重试）。若等用户请求
    同步付这笔钱，就是白屏。有旧值时先给旧值（标 stale），后台补新值。
    """
    if tag in _bg_refreshing:
        return
    _bg_refreshing.add(tag)

    async def _run():
        try:
            from server.cache import cache as _c
            data = await fetch_fn()
            if data:
                _c.set(key, data, ttl)
        except Exception as exc:  # noqa: BLE001
            logger.info("background refresh %s failed: %s", tag, exc)
        finally:
            _bg_refreshing.discard(tag)

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        _bg_refreshing.discard(tag)


_ranking_refreshing = set()


def _kick_sector_ranking_refresh(fetch_fn) -> None:
    """后台刷新板块排行缓存（同一时刻只跑一个，避免并发打爆上游）。"""
    if "ranking" in _ranking_refreshing:
        return
    _ranking_refreshing.add("ranking")

    async def _run():
        try:
            from server.cache import cache as _c
            data = await fetch_fn()
            if isinstance(data, list) and data:
                _c.set("sector_ranking_v2", data, CACHE_TTL_SECTOR_RANK)
        except Exception as exc:  # noqa: BLE001
            logger.info("sector ranking background refresh failed: %s", exc)
        finally:
            _ranking_refreshing.discard("ranking")

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        _ranking_refreshing.discard("ranking")


@router.get("/sector-ranking")
async def get_sector_ranking(
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(50, ge=5, le=200, description="每页条数"),
    level: str = Query("0", description="层级过滤：0=大类(默认) / 2 / 3 / all"),
):
    """板块排行（全量宇宙 + 层级过滤 + 全量排序后分页）。

    - 默认 `level=0`（大类）排行，避免 Ⅱ/Ⅲ 子级与父子同名板块混榜造成"假象"
    - `level=all` 时每条带 `level`，并附 `note` 说明跨层级不可直接比较强弱
    - 排序一律在**选定层级的全量**上进行，再按 limit 截取
    """
    async def _fetch():
        res = await stable_provider.get_sector_ranking_data()
        if res:
            return res
        return await westock.sector_ranking()

    # ── stale-while-revalidate ────────────────────────────────────────────
    # 实测：缓存过期后同步拉取要 11.7–19 秒（东财被阻断 → 多 host 重试 → CLI），
    # 而 TTL 只有 60s，等于每分钟都有人卡十几秒。改为：
    #   · 缓存里有值（即使已过期）→ 立刻返回，标 stale=true，后台异步刷新
    #   · 缓存空（冷启动）→ 才同步拉取
    # 这样延迟从「十几秒」降到「毫秒级」，且不牺牲数据新鲜度（下一分钟即更新）。
    entry = None
    try:
        entry = cache.get_entry("sector_ranking_v2")
    except Exception:  # noqa: BLE001
        entry = None
    served_stale = False
    if entry and entry.get("value"):
        all_data = entry["value"]
        served_stale = bool(entry.get("expired"))
        if served_stale:
            _kick_sector_ranking_refresh(_fetch)
    else:
        all_data = await cache.get_or_fetch("sector_ranking_v2", _fetch, CACHE_TTL_SECTOR_RANK) or []
    if not isinstance(all_data, list):
        all_data = []

    # 层级过滤（默认大类 level=0）
    lv = str(level).strip().lower()
    if lv == "all":
        selected = list(all_data)
    else:
        try:
            want = int(lv)
        except ValueError:
            want = 0
        selected = [r for r in all_data if int(r.get("level", 0)) == want]

    # 在选定层级的**全量**上排序，再截取
    selected.sort(key=lambda r: float(r.get("chg") or 0), reverse=True)
    for i, r in enumerate(selected):
        r["rank"] = i + 1

    total = len(selected)
    start = (page - 1) * limit
    paged = selected[start:start + limit]

    source, degraded = _envelope_source(all_data)
    aggregated = any(isinstance(r, dict) and r.get("aggregated") for r in all_data)

    payload = {
        "ok": True,
        "source": source,
        "degraded": degraded,
        "aggregated": aggregated,
        "breaker": push2_breaker_state(),
        "level": lv,
        "stale": served_stale,
        "stale_note": ("榜单为上一轮快照，后台正在刷新" if served_stale else None),
        "universe_total": len(all_data),
        "total": total,
        "page": page,
        "limit": limit,
        "data": paged if (page > 1 or limit < total) else selected,
        "rows": paged,
    }
    if lv == "all":
        payload["note"] = "当前为跨层级(大类+Ⅱ+Ⅲ)混合榜，各层级不可直接比较强弱，请按 level 分组解读"
    return payload


@router.get("/sector-map/stock/{code}")
async def get_stock_sector_map(code: str):
    """反查某股票所属板块列表（读 data/sector_map.json，M3）。

    - 每条带 `level`（Ⅲ→3 / Ⅱ→2 / Ⅰ→1 / 无后缀→0 大类），并按 level 升序返回，
      避免把"大类 + Ⅱ + Ⅲ"误读成"精确并列"
    - 映射尚未构建时返回 ok:false / data:null + "映射尚未构建"，
      **绝不在请求路径里现拉**（构建由 scripts/build_sector_map.py 离线完成）。
    """
    from server.sector_map import get_stock_sectors_detailed, load_sector_map
    data = load_sector_map()
    if data is None:
        return {"ok": False, "data": None, "message": "映射尚未构建",
                "hint": "python scripts/build_sector_map.py"}
    sectors = get_stock_sectors_detailed(code) or []
    return {
        "ok": True,
        "code": code,
        "count": len(sectors),
        "sectors": sectors,
        "codes": [s["code"] for s in sectors],
        "levels": sorted({s["level"] for s in sectors}),
        "source": data.get("source"),
        "updated_at": data.get("updated_at"),
        "coverage": data.get("coverage"),
    }


@router.get("/sector-linkage")
async def get_sector_linkage_api(
    code: Optional[str] = Query(None, description="板块 code；给定时返回该板块全量敏感度"),
    top: int = Query(50, ge=1, le=496, description="返回排行条数"),
):
    """板块金融属性敏感度（板块日收益 × M2 外部因子日收益）。

    - 每条证据带 `symbol / corr / sample_size / lead_days / as_of`
    - 样本 < 30 → `insufficient_samples`，不给系数
    - 数据来自离线 `data/sector_returns.json` + M2 盘缓存，无网络、纯 CPU
    """
    from server.sector_linkage import get_sector_linkage
    res = await asyncio.to_thread(get_sector_linkage, code, top)
    # 信封统一：历史上本端点把内容平铺在顶层（无 data），而 /api/kline 等用 {ok,data}，
    # 前端要为两类写两套取值。这里补一个 data 别名（内容同顶层），旧代码零改动，
    # 新代码统一读 data。属于**加字段**，不破坏任何既有消费方。
    if isinstance(res, dict) and "data" not in res:
        res["data"] = {k: v for k, v in res.items() if k not in ("ok", "data")}
    return res


@router.get("/sector-graph")
async def get_sector_graph_api(
    code: str = Query(..., description="板块 code"),
    top: int = Query(10, ge=1, le=50, description="返回邻居数"),
):
    """单板块邻居：成分股 Jaccard 重叠 + 收益相关（去市场因子）。

    每条边带 `{b, type, value, shared_count, sample_size, same_lineage, as_of}`；
    父子/同族板块（成分股为包含关系，含完全相同）标 `same_lineage:true`，避免误读为"真实联动"。
    """
    from server.sector_graph import get_neighbors
    from server.sector_resolve import is_stale
    res = await asyncio.to_thread(get_neighbors, code, top)
    # 时间字段统一：历史上这里叫 as_of_date，其它端点叫 as_of，消费方要写两套兼容。
    # 两个都给（值相同），并补 stale —— 离线产物超过 4 天即如实标注。
    if isinstance(res, dict):
        d = res.get("as_of_date") or res.get("as_of")
        res["as_of_date"] = d
        res["as_of"] = d
        res["stale"] = is_stale(d) if d else False
        if "data" not in res:
            res["data"] = {k: v for k, v in res.items() if k not in ("ok", "data")}
    return res


@router.get("/sector-clusters")
async def get_sector_clusters_api():
    """同涨同跌簇（去市场因子相关 + 阈值 + topK 连通分量），每簇带成员/均相关/样本量/as_of。"""
    from server.sector_graph import get_clusters
    from server.sector_resolve import is_stale
    res = await asyncio.to_thread(get_clusters)
    if isinstance(res, dict):
        d = res.get("as_of_date") or res.get("as_of")
        res["as_of_date"] = d
        res["as_of"] = d
        res["stale"] = is_stale(d) if d else False
        if "data" not in res:
            res["data"] = {k: v for k, v in res.items() if k not in ("ok", "data")}
    return res


@router.get("/lhb")
async def get_lhb(
    tab: str = Query("jg", description="龙虎榜tab: jg/yzb/yyb/gslmr/gslxw"),
    date: Optional[str] = Query(None, description="日期 YYYY-MM-DD"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(50, ge=5, le=100, description="每页条数"),
):
    """龙虎榜（真实数据源与日期解析 + 分页支持）"""
    cache_key = f"lhb_v2:{tab}:{date or 'today'}"

    async def _fetch():
        res = await stable_provider.get_lhb_data(date)
        if res:
            return res
        return await westock.lhb(tab, date)
        
    all_data = await cache.get_or_fetch(cache_key, _fetch, CACHE_TTL_LHB) or []
    
    total = len(all_data) if isinstance(all_data, list) else 0
    start = (page - 1) * limit
    paged = all_data[start:start + limit] if isinstance(all_data, list) else []

    # 信封来源依据数据真实来源推导（可能是东财实时，也可能是本地样本兜底）
    source, degraded = _envelope_source(all_data)

    return {
        "ok": True,
        "source": source,
        "degraded": degraded,
        "total": total,
        "page": page,
        "limit": limit,
        "data": paged if page > 1 or limit < total else all_data,
        "rows": paged
    }


@router.get("/market-overview")
async def get_market_overview():
    """市场涨跌分布 —— 复用 /api/market-dashboard 的多板块样本广度（同一实现，口径统一）。

    旧实现基于东财 clist 涨幅榜前 100 行统计 → up=100/down=0，与 dashboard 的真实
    样本广度自相矛盾（同一系统两套涨跌家数）。现直接复用 dashboard 的 breadth，两处
    口径一致；不可得时 ok:false + reason，不再用 ok:true 掩盖失败。
    """
    async def _fetch():
        from server.routers.market_dashboard import _build_dashboard
        try:
            dash = await _build_dashboard() or {}
        except Exception as e:
            logger.warning("market-overview: dashboard breadth failed: %s", str(e))
            return None
        breadth = dash.get("breadth") or {}
        if not breadth or not dash.get("coverage"):
            return None
        return {
            "up_count": breadth.get("up"),
            "down_count": breadth.get("down"),
            "flat_count": breadth.get("flat"),
            "limit_up": breadth.get("limit_up"),
            "limit_down": breadth.get("limit_down"),
            "total_amount": (dash.get("turnover") or {}).get("amount"),
            "scope": dash.get("scope"),
            "coverage": dash.get("coverage"),
            "sample": breadth.get("sample"),
            "degraded": breadth.get("degraded"),
            "reason": breadth.get("reason"),
            "basis": ("全 A 股（沪深京）覆盖样本广度" if not breadth.get("degraded")
                      else "全市场快照不可用，退回核心样本广度"),
        }

    data = await cache.get_or_fetch("market_overview_v3", _fetch, CACHE_TTL_MARKET_OVERVIEW)
    if not data:
        return {
            "ok": False,
            "source": "tencent_quotes (market-dashboard 同源样本)",
            "reason": "行情广度不可得：market-dashboard 样本行情暂不可用",
            "data": None,
        }
    return {"ok": True, "source": "market-coverage.v1+tencent_quotes", "data": data}


@router.get("/hot-stocks")
async def get_hot_stocks(
    type: str = Query("stock", description="类型: stock/fund/etf"),
    limit: int = Query(50, ge=1, le=100, description="数量"),
    page: int = Query(1, ge=1, description="页码"),
):
    """热门股（真实活跃榜与换手率聚合 + 分页支持）"""
    cache_key = f"hot_v2:{type}:{limit}"

    async def _fetch():
        res = await stable_provider.get_hot_stocks(limit)
        if res:
            return res
        return await westock.hot_stocks(type, limit)

    all_data = await cache.get_or_fetch(cache_key, _fetch, CACHE_TTL_HOT) or []
    
    total = len(all_data) if isinstance(all_data, list) else 0
    start = (page - 1) * limit
    paged = all_data[start:start + limit] if isinstance(all_data, list) else []

    # 信封来源依据数据真实来源推导（可能是东财实时，也可能是本地样本兜底）
    source, degraded = _envelope_source(all_data)

    return {
        "ok": True,
        "source": source,
        "degraded": degraded,
        "breaker": push2_breaker_state(),
        "total": total,
        "page": page,
        "limit": limit,
        "data": paged if page > 1 or limit < total else all_data,
        "rows": paged
    }


@router.get("/profile")
async def get_profile(code: str = Query(..., description="股票代码")):
    """个股简况（复用已有 stock_profile 方法）"""
    async def _fetch():
        return await westock.stock_profile(code)
    data = await cache.get_or_fetch(f"profile:{code}", _fetch, CACHE_TTL_PROFILE)
    return {"ok": True, "data": data} if data is not None else _westock_unavailable()


@router.get("/finance")
async def get_finance(
    code: str = Query(..., description="股票代码"),
    type: Optional[str] = Query(None, description="报表类型（本期仅作兼容回显，不参与取数）"),
):
    """财务基本面快照 —— 6 张指标卡（P0-1 主体）。

    契约见架构设计 §3.2。响应信封：``{ok, data, message, source, as_of, age, degraded, reason}``。

    6 卡：``pe_ttm`` / ``pb`` / ``roe_ttm`` / ``revenue_yoy`` / ``profit_yoy`` / ``dividend_yield``。

    纪律（硬约束）：
      - 取不到就是 ``value: null`` + 具体 ``reason``，**绝不返回 0**，**绝不用行业均值/
        上期值/推算值填充**；
      - ``ok: true`` 只表示「请求被正确处理」，不代表所有卡都有值；部分不可用时
        ``ok`` 仍为 ``true``，``degraded: true``；
      - 6 卡全部不可用时 ``ok: false``，但 ``data.cards`` 仍给出 6 张 ``value:null`` 的卡，
        便于前端稳定渲染「不可用 + 原因」。

    数据源：新浪财务摘要（ROE/营收同比/净利同比/每股净资产）+ 百度估值（PE_TTM/PB）
    + 巨潮分红（股息率）。**不使用任何东财 ``*_em`` 接口**（本机 403）。
    """
    if _FUNDAMENTAL_IMPORT_ERROR:
        return {
            "ok": False,
            "data": None,
            "message": "财务基本面模块不可用",
            "source": None,
            "as_of": None,
            "age": None,
            "degraded": True,
            "reason": f"server.fundamental 导入失败：{_FUNDAMENTAL_IMPORT_ERROR}",
        }

    if is_finance_v2_enabled():
        return await get_fundamental_snapshot(code=code, report_type=type)

    # ---------------------------------------------------------------
    # 回退旧逻辑（仅用于紧急止损：FEATURE_FLAGS["finance_v2_enabled"]=False）
    # 旧链路（westock CLI）已废弃，回退后仍会返回全 null —— 这就是 P0-1 要治的缺口。
    # ---------------------------------------------------------------
    logger.warning("finance_v2_enabled=False，/api/finance 走已废弃的 westock 旧链路")
    cache_key = f"finance:{code}:{type or 'default'}"

    async def _fetch():
        return await westock.finance(code, type)
    data = await cache.get_or_fetch(cache_key, _fetch, CACHE_TTL_FINANCE)
    if data is None:
        return _westock_unavailable()
    return {"ok": True, "data": data, "message": "ok（旧链路回退，字段可能全为 null）"}


@router.get("/finance/valuation")
async def get_finance_valuation(
    code: str = Query(..., description="股票代码"),
    years: int = Query(5, ge=1, le=10, description="历史窗口年数（1-10）"),
    indicators: str = Query("pe_ttm,pb", description="指标，逗号分隔：pe_ttm / pb"),
):
    """估值日频序列 + 窗口分位（契约见架构设计 §3.3）。

    返回 ``data.series = {"pe_ttm": [{date, value}, ...], "pb": [...]}``
    与 ``data.percentile = {"pe_ttm": {p10, p50, p90, current, current_pct, ...}}``。

    用途：
      - 卡片内的 5 年分位条（P0-1 的 A5 加值项）；
      - 为 ``Alpha_Value_EP`` 因子提供 PE_TTM 历史序列（解决缺口 D2）。

    分位口径：``current_pct`` = 窗口内「小于等于当前值的样本数 / 总样本数 × 100」，
    取值 0–100，越高越贵。样本 < 30 时不给分位并写明原因。
    """
    if _FUNDAMENTAL_IMPORT_ERROR:
        return {
            "ok": False,
            "data": None,
            "message": "财务基本面模块不可用",
            "source": None,
            "as_of": None,
            "age": None,
            "degraded": True,
            "reason": f"server.fundamental 导入失败：{_FUNDAMENTAL_IMPORT_ERROR}",
        }

    wanted = [item.strip() for item in str(indicators or "").split(",") if item.strip()]
    return await get_valuation_history(
        code=code, years=years, indicators=wanted or ["pe_ttm", "pb"]
    )



@router.get("/report")
async def get_report(
    code: str = Query(..., description="股票代码"),
    limit: int = Query(10, ge=1, le=50, description="数量"),
):
    """研报列表"""
    cache_key = f"report:{code}:{limit}"

    async def _fetch():
        return await westock.report(code, limit)
    data = await cache.get_or_fetch(cache_key, _fetch, CACHE_TTL_REPORT)
    return {"ok": True, "data": data} if data is not None else _westock_unavailable()


@router.get("/consensus")
async def get_consensus(code: str = Query(..., description="股票代码")):
    """机构评级 + 一致预期（合并返回）"""
    cache_key = f"consensus:{code}"

    async def _fetch():
        rating_data, consensus_data = await asyncio.gather(
            westock.rating(code),
            westock.consensus(code),
        )
        # 两个子源都无数据 → 视为未接入（返回 None 让外层给 ok:false + reason）
        if rating_data is None and consensus_data is None:
            return None
        return {
            "rating": rating_data,
            "consensus": consensus_data,
        }
    data = await cache.get_or_fetch(cache_key, _fetch, CACHE_TTL_REPORT)
    return {"ok": True, "data": data} if data is not None else _westock_unavailable()


@router.get("/north-holding")
async def get_north_holding(
    market: str = Query("sh", description="市场: sh(沪股通) / sz(深股通)"),
):
    """沪深股通成份股"""
    cache_key = f"north:{market}"

    async def _fetch():
        return await westock.north_holding(market)
    data = await cache.get_or_fetch(cache_key, _fetch, CACHE_TTL_NORTH)
    return {"ok": True, "data": data} if data is not None else _westock_unavailable()


@router.get("/news")
async def get_news(
    code: str = Query(..., description="股票代码"),
    limit: int = Query(10, ge=1, le=50, description="数量"),
):
    """个股新闻资讯（复用已有 get_news 方法）"""
    cache_key = f"news:{code}:{limit}"

    async def _fetch():
        return await westock.get_news(code, limit)
    data = await cache.get_or_fetch(cache_key, _fetch, CACHE_TTL_NEWS)
    return {"ok": True, "data": data} if data is not None else _westock_unavailable(
        "westock CLI 链路已废弃；个股新闻暂无接入源（市场级资讯见 /api/news/market）")


@router.get("/news/market")
async def get_market_news(
    limit: int = Query(20, ge=1, le=50, description="数量"),
):
    """市场资讯 —— 改用可达的 AkShare 要闻链路（东财，失效自动降级新浪）。

    原实现走 westock CLI（已废弃）→ 恒 null；现复用 /api/intelligent/ak/news 同一
    实现（真实可达）。不可得时 ok:false + reason，不用 ok:true 掩盖。
    """
    cache_key = f"market_news_ak:{limit}"

    async def _fetch():
        res = await ak_hub.get_notices_and_news(limit=limit)
        if not res or not res.get("ok"):
            return None
        return res.get("data") or []
    data = await cache.get_or_fetch(cache_key, _fetch, CACHE_TTL_NEWS)
    if data is None:
        return {"ok": False, "source": "unavailable", "data": None,
                "reason": "市场资讯不可得：AkShare 要闻链路（东财/新浪）暂不可用"}
    return {"ok": True, "source": "AkShare 要闻（东财，失效降级新浪）", "data": data}


@router.get("/sector-stocks")
async def get_sector_stocks_api(
    sector: str = Query(..., description="板块标识：config key（semiconductor）/ 东财码（BK0438）/ 中文名"),
):
    """板块成分股（统一解析三套板块标识）。

    修复背景（实测，2026-10-22）：
    - 原实现只认 ``config.SECTORS`` 的 26 个 key，``?sector=BK0438`` 直接返回
      ``unavailable``——而 ``data/sector_map.json`` 里 496 个板块本就有完整成分股；
    - 返回体只有 ``rows``/``data``，前端读的是 ``data.stocks``，于是结果被整体丢弃，
      永远退回本地股池过滤。

    现在：config key / BK 码 / 中文名 / 同花顺码 都能解析；``stocks``、``rows``、``data``
    三个字段同源同值，老前端不改也不会再拿到空；``as_of`` + ``stale`` 如实暴露离线快照年龄。
    """
    raw = (sector or "").strip()
    cache_key = f"sector_stocks_v3:{raw}"

    async def _fetch():
        from server.sector_resolve import resolve as resolve_sector, stale_note
        r = resolve_sector(raw)
        stocks = r.get("constituents") or []
        if not stocks and r.get("resolved_by") == "none":
            try:
                stocks = await westock.sector_constituents(raw) or []
            except Exception as exc:  # noqa: BLE001
                logger.info("sector_constituents(%s) failed: %s", raw, exc)
                stocks = []
            if stocks:
                r.update({"source": "实时成分股查询", "resolved_by": "realtime"})
        degraded = (r.get("source") or "").startswith("config")
        note = stale_note(r) or ("来自 config 静态股票列表，非实时成分股" if degraded else None)
        return {
            "sector": {
                "input": raw,
                "key": r.get("key"),
                "code": r.get("bk_code"),
                "name": r.get("name"),
                "level": r.get("level"),
            },
            "resolved_by": r.get("resolved_by"),
            "stocks": stocks,
            "source": r.get("source"),
            "degraded": bool(degraded),
            "as_of": r.get("as_of"),
            "stale": bool(r.get("stale")),
            "note": note,
            "reason": r.get("reason"),
        }

    data = await cache.get_or_fetch(cache_key, _fetch, CACHE_TTL_SECTOR_STOCKS)
    if not data:
        return {"ok": False, "source": "unavailable", "degraded": True,
                "reason": "成分股数据源不可用", "stocks": [], "rows": [], "data": [], "count": 0}
    stocks = data.get("stocks") or []
    return {
        "ok": True,
        "sector": data.get("sector"),
        "resolved_by": data.get("resolved_by"),
        "count": len(stocks),
        "stocks": stocks,
        "rows": stocks,      # 兼容：既有调用方按 rows 读
        "data": stocks,      # 兼容：api.js 的统一信封映射读 data
        "source": data.get("source"),
        "degraded": data.get("degraded", False),
        "as_of": data.get("as_of"),
        "stale": data.get("stale", False),
        "note": data.get("note"),
        "reason": data.get("reason"),
    }
