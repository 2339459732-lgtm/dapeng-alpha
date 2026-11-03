"""全市场与沪深港通资金流向路由。

历史说明：本模块原先 `from server.ak_market import ak_market`，但该单例
**并不存在**（ak_market.py 实际只导出 `ak_hub = AkShareDataHub()`），
因此一旦被 main.py 挂载就会 ImportError —— 这也是它长期未被注册、
导致前端 `/api/market/north` 恒定 404 的根因。此处改用真实的 `ak_hub`。
"""
import logging
from fastapi import APIRouter, Query

from server.ak_market import ak_hub

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/all")
async def get_full_market(
    sort_by: str = Query("chg", description="排序字段: chg, amount, turnover"),
    limit: int = Query(50, ge=1, le=500, description="返回数量"),
):
    """全市场 A 股实时排行（AkShare / 东方财富 数据源）"""
    res = await ak_hub.get_market_spot()
    if not res.get("ok"):
        return {
            "ok": False,
            "data": [],
            "status": res.get("status", "error"),
            "message": res.get("message", "全市场行情取数失败"),
        }

    rows = list(res.get("data") or [])
    rows.sort(key=lambda x: (x.get(sort_by) if x.get(sort_by) is not None else -999), reverse=True)
    return {
        "ok": True,
        "source": res.get("source"),
        "update_time": res.get("update_time"),
        "total": len(rows),
        "data": rows[:limit],
    }


@router.get("/north")
async def get_north_data():
    """沪深港通资金流向（北向沪股通/深股通 + 南向港股通）。

    注意：北向「成交净买额」自 2024-08 起已停止实时披露，data.disclosed
    为 False 时前端应改为呈现成分股涨跌分布与相关指数表现。
    """
    return await ak_hub.get_north_flow()
