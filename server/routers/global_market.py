"""DAPENG Alpha 策略选股系统 - GET /api/global 全球市场接口

v4.5 优化：
- 腾讯HTTP获取指数行情（毫秒级）
- 使用 cache.get_or_fetch 防缓存击穿
"""

import asyncio
import logging
from typing import Any, Dict, List

from fastapi import APIRouter

from server.cache import cache
from server.config import CACHE_TTL_GLOBAL, GLOBAL_INDICES
from server.realtime import fetch_tencent_quotes

logger = logging.getLogger(__name__)
router = APIRouter()

async def _fetch_indices_batch() -> Dict[str, Dict[str, Any]]:
    """批量获取所有指数数据。

    - indices（A股宽基）：腾讯行情批量（可达）。
    - overseas（外盘）：复用 server.global_assets 的多源快照（腾讯+Sina）。
      旧码 us.IXIC / hf_GC 已废弃；取不到 → price:null + unavailable_reason（不再写 0）。
    """
    idx_codes = [info["code"] for info in GLOBAL_INDICES["indices"].values()]
    result = {
        "indices": {},
        "overseas": {},
    }

    # ---- A股宽基指数：腾讯批量 ----
    try:
        quotes = await fetch_tencent_quotes(idx_codes, timeout=5.0)
        quote_map = {q.get("code", ""): q for q in quotes}
        for key, info in GLOBAL_INDICES["indices"].items():
            q = quote_map.get(info["code"])
            if q and q.get("price", 0) > 0:
                result["indices"][key] = {
                    "code": info["code"], "name": info["name"],
                    "price": q.get("price"), "chg": q.get("chg"), "available": True,
                }
            else:
                result["indices"][key] = {
                    "code": info["code"], "name": info["name"],
                    "price": None, "chg": None, "available": False,
                    "unavailable_reason": "no_data_from_sources",
                }
    except Exception as e:
        logger.warning("A股指数批量获取失败: %s", str(e))
        for key, info in GLOBAL_INDICES["indices"].items():
            result["indices"][key] = {
                "code": info["code"], "name": info["name"],
                "price": None, "chg": None, "available": False,
                "unavailable_reason": f"fetch_error: {e}",
            }

    # ---- 外盘：复用 global_assets（多源，含 Sina 黄金），按 asset_key 映射 ----
    asset_map: Dict[str, Dict[str, Any]] = {}
    try:
        from server.global_assets import get_global_assets
        ga = await get_global_assets()
        for cat_items in (ga.get("categories") or {}).values():
            for it in cat_items or []:
                if it.get("key"):
                    asset_map[it["key"]] = it
    except Exception as e:
        logger.warning("global assets fetch failed: %s", str(e))

    for key, info in GLOBAL_INDICES["overseas"].items():
        asset_key = info.get("asset_key", key)
        it = asset_map.get(asset_key)
        if it and it.get("available") and it.get("price"):
            result["overseas"][key] = {
                "code": it.get("code") or info.get("code"),
                "name": info.get("name") or it.get("name"),
                "price": it.get("price"), "chg": it.get("chg"),
                "available": True, "source": it.get("source"),
            }
        else:
            result["overseas"][key] = {
                "code": info.get("code"), "name": info.get("name"),
                "price": None, "chg": None, "available": False,
                "unavailable_reason": (it or {}).get("unavailable_reason") or "no_data_from_sources",
            }

    return result


@router.get("/global")
async def get_global_market():
    """
    获取全球市场指数。

    优化：使用 cache.get_or_fetch 防止并发请求击穿缓存。
    v16 扩展：并入全球资产快照（美股/港股/亚太/欧洲/商品/外汇），保持原 indices/overseas 结构不变。
    """
    cache_key = "global:markets"

    async def _fetch():
        return await _fetch_indices_batch()

    data = await cache.get_or_fetch(cache_key, _fetch, CACHE_TTL_GLOBAL)
    if data is None:
        data = {"indices": {}, "overseas": {}}

    # 并入全球资产（失败不影响原结构）
    try:
        from server.global_assets import get_global_assets
        assets = await get_global_assets()
        data = {**data, "global_assets": assets}
    except Exception as exc:
        logger.warning("global assets merge failed: %s", exc)

    return {"ok": True, "data": data}


@router.get("/global/assets")
async def get_global_assets_api():
    """全球资产分类快照（美股/港股/亚太/欧洲/商品/外汇，真实实时，60s 缓存）"""
    from server.global_assets import get_global_assets
    return await get_global_assets()


@router.get("/global/linkage")
async def get_global_linkage(force: int = 0):
    """世界金融市场对 A 股传导建模：领先滞后相关 + 开盘缺口传导 + 每日影响打分卡。

    每项结论均带 sample_size / method / as_of_date；样本不足返回 null +
    insufficient_samples；经验项标 evidence_type="heuristic"。
    force=1 跳过内存缓存重算。
    """
    from server.global_linkage import get_linkage_with_scorecard
    return await get_linkage_with_scorecard(force=bool(force))
