"""DAPENG Alpha v5.5 - 动态全市场股票池管理器（三层池架构 + 全市场实时扩展）

三层架构：
  Layer 1 — base_pool:   静态核心池，来自 config.py SECTORS 配置
  Layer 2 — sector_pool: 板块成分股与行业股票
  Layer 3 — dynamic_pool: 全市场动态股票池（从 AkShare/EastMoney 实时全量注入 5000+ A股代码与名称）

所有层合并去重后提供统一查询接口，支持全市场代码/名称精准查找与分页。
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class StockPoolManager:
    """三层动态股票池管理器"""

    def __init__(self) -> None:
        self._base_pool: Dict[str, str] = {}        # code -> name（静态配置）
        self._sector_pool: Dict[str, str] = {}      # code -> name（板块成分股）
        self._dynamic_pool: Dict[str, dict] = {}     # code -> {name, source, added_at, sector, price, chg}
        self._all_codes: List[str] = []
        self._initialized: bool = False

    async def initialize(self) -> None:
        """启动时初始化：加载静态池 + 异步预热全市场真实股票（不阻塞启动）"""
        from server.config import find_stock_name, get_all_stock_codes

        # Layer 1: 静态配置池
        for code in get_all_stock_codes():
            self._base_pool[code] = find_stock_name(code)
        self._rebuild_all_codes()
        self._initialized = True
        logger.info("StockPool initialized: %d base stocks", len(self._base_pool))

        # 异步从全市场接口注入 5000+ A 股
        asyncio.create_task(self._preload_full_market_stocks())

    async def _preload_full_market_stocks(self) -> None:
        """从 AkShare/EastMoney 实时获取全市场 5000+ 只 A 股股票清单并注入股票池"""
        try:
            from server.ak_market import ak_hub
            logger.info("Preloading full A-share market stocks into StockPool...")
            spot_res = await ak_hub.get_market_spot()
            if spot_res and spot_res.get("data"):
                count = 0
                for item in spot_res["data"]:
                    code = item.get("code")
                    name = item.get("name")
                    if code and name:
                        self._sector_pool[code] = name
                        self._dynamic_pool[code] = {
                            "name": name,
                            "source": "akshare_full_market",
                            "added_at": datetime.now().isoformat(),
                            "price": item.get("price", 0),
                            "chg": item.get("chg", 0),
                            "turnover": item.get("turnover", 0),
                            "amount": item.get("amount", 0),
                            "pe": item.get("pe", 0),
                        }
                        count += 1
                self._rebuild_all_codes()
                logger.info(
                    "StockPool successfully preloaded %d full market stocks. Total universe: %d",
                    count, len(self._all_codes)
                )
        except Exception as e:
            logger.warning("Preload full market stocks failed: %s", e)

    async def add_dynamic_stocks(self, codes: List[str], source: str) -> None:
        """动态加入股票（龙虎榜/热门股等来源）"""
        now = datetime.now().isoformat()
        for code in codes:
            if code not in self._all_codes:
                self._dynamic_pool[code] = {
                    "name": self.get_stock_name(code),
                    "source": source,
                    "added_at": now,
                }
        self._rebuild_all_codes()

    def _rebuild_all_codes(self) -> None:
        """重建合并去重的代码列表"""
        all_codes = (
            set(self._base_pool.keys())
            | set(self._sector_pool.keys())
            | set(self._dynamic_pool.keys())
        )
        self._all_codes = sorted(all_codes)

    def get_all_codes(self) -> List[str]:
        """获取所有合并去重后的股票代码"""
        return self._all_codes

    def get_stock_name(self, code: str) -> str:
        """获取股票名称（按 base → sector → dynamic → config 顺序查找）"""
        if code in self._base_pool:
            return self._base_pool[code]
        if code in self._sector_pool:
            return self._sector_pool[code]
        if code in self._dynamic_pool:
            name = self._dynamic_pool[code].get("name")
            if name:
                return name
        from server.config import find_stock_name
        return find_stock_name(code)

    def search_stocks(self, keyword: str, limit: int = 50) -> List[dict]:
        """在全量股票池中搜索匹配代码或名称的股票"""
        kw = keyword.lower().strip()
        if not kw:
            return []
        
        results = []
        # 优先前缀匹配，再包含匹配
        exact_matches = []
        prefix_matches = []
        contain_matches = []

        from server.config import find_stock_sector, get_sector_name

        for code in self._all_codes:
            name = self.get_stock_name(code)
            clean_code = code.replace("sh", "").replace("sz", "").replace("bj", "")
            
            sector_key = find_stock_sector(code)
            sector_name = get_sector_name(sector_key) if sector_key else ""

            item = {
                "code": code,
                "symbol": clean_code,
                "name": name,
                "sector": sector_key,
                "sector_name": sector_name
            }

            if kw == code.lower() or kw == clean_code or kw == name:
                exact_matches.append(item)
            elif code.lower().startswith(kw) or clean_code.startswith(kw) or name.startswith(kw):
                prefix_matches.append(item)
            elif kw in code.lower() or kw in clean_code or kw in name:
                contain_matches.append(item)

        results = (exact_matches + prefix_matches + contain_matches)[:limit]
        return results

    def get_pool_stats(self) -> dict:
        """获取股票池统计信息"""
        return {
            "base_count": len(self._base_pool),
            "sector_count": len(self._sector_pool),
            "dynamic_count": len(self._dynamic_pool),
            "total_count": len(self._all_codes),
            "initialized": self._initialized,
        }


# 全局实例
stock_pool = StockPoolManager()
