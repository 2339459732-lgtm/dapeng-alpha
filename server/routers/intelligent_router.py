"""
DAPENG Alpha - 融合 AkShare 与 Qlib 的智能综合 API 路由 (V15.0)
提供：
1. /api/ak/spot          → AkShare 全市场行情与宏观
2. /api/ak/index         → 宽基指数实时
3. /api/ak/macro         → 宏观经济数据
4. /api/ak/news          → 财经新闻与公告
5. /api/qlib/factors     → Qlib Alpha 因子库
6. /api/qlib/backtest    → 因子组合回测与风险指标
7. /api/market/survey    → 市场调查、轮动与异动预警
"""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from typing import List, Optional

from server.ak_market import ak_hub
from server.qlib_factor_engine import qlib_engine
from server.market_survey_engine import market_survey_engine

router = APIRouter(prefix="/api/intelligent", tags=["intelligent_analysis"])

@router.get("/ak/spot")
async def get_ak_spot():
    """获取 AkShare A 股全市场快照"""
    res = await ak_hub.get_market_spot()
    return JSONResponse(content=res)

@router.get("/ak/index")
async def get_ak_index():
    """获取主要指数实时快照"""
    res = await ak_hub.get_index_spot()
    return JSONResponse(content=res)

@router.get("/ak/macro")
async def get_ak_macro():
    """获取宏观经济数据"""
    res = await ak_hub.get_macro_data()
    return JSONResponse(content=res)

@router.get("/ak/news")
async def get_ak_news(limit: int = Query(15, ge=5, le=50)):
    """获取财经要闻与市场情报"""
    res = await ak_hub.get_notices_and_news(limit=limit)
    return JSONResponse(content=res)

@router.get("/qlib/factors")
async def get_qlib_factors():
    """获取动态因子缠论因子库（含真实公式、可用性与不可用原因）"""
    res = qlib_engine.get_factor_library()
    return JSONResponse(content=res)

@router.get("/qlib/meta")
async def get_qlib_meta():
    """获取因子回测面板元信息（因子/基准/区间/默认参数）。

    其中 available_factor_ids 与引擎实际可算出非 null 值的因子集合完全一致。
    """
    res = qlib_engine.get_meta()
    return JSONResponse(content=res)

@router.get("/qlib/dynamic")
async def get_qlib_dynamic(limit: int = Query(12, ge=1, le=30)):
    """动态因子缠论选股：真实行情+K线+缠论结构实时排名"""
    res = await qlib_engine.dynamic_screen(limit=limit)
    return JSONResponse(content=res)

@router.post("/qlib/backtest")
async def post_qlib_backtest(request: Request):
    """组合动态因子并执行真实逐期截面回测评估。

    支持参数：factors（选中因子）/ weights（因子权重）/ benchmark / top_k /
    start_date / end_date / days / include_debug。
    """
    body = await request.json()
    factors = body.get("factors")
    weights = body.get("weights")
    benchmark = body.get("benchmark", "sh000300")
    top_k = int(body.get("top_k", 8))
    start_date = body.get("start_date", "2024-01-01")
    end_date = body.get("end_date")
    days = int(body.get("days", 90))
    include_debug = bool(body.get("include_debug", False))

    res = await qlib_engine.evaluate_factor_combination(
        selected_factor_ids=factors,
        weights=weights,
        top_k=top_k,
        benchmark=benchmark,
        start_date=start_date,
        end_date=end_date,
        days=days,
        include_debug=include_debug,
    )
    return JSONResponse(content=res)

@router.get("/market/survey")
async def get_market_survey():
    """获取市场全景调查、板块轮动与异动监控"""
    res = await market_survey_engine.get_market_survey()
    return JSONResponse(content=res)
