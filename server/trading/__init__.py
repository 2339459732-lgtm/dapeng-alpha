"""自动交易核心包 - 包门面

导出全局单例 trading_facade，供 main.py 与路由装配（架构蓝图 §1.1）。

装配顺序：store → alerts → paper（注册进网关注册表）→ risk → monitor。
main.py 仅 3 行挂载：
    trading_task = asyncio.create_task(trading_facade.start())   # lifespan 启动段
    trading_task.cancel()                                        # lifespan 关闭段
    app.include_router(automation.router, prefix="/api/automation")
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from . import gateway as gateway_registry
from .alerts import EmailAlerter
from .loop_scheduler import LoopScheduler
from .models import GateState
from .monitor import SignalMonitor
from .paper import PaperGateway
from .risk import RiskManager
from .store import TradingStore
from .strategies import list_all as list_all_strategies

logger = logging.getLogger(__name__)


class TradingFacade:
    """自动交易总装门面：持有全部单例，供路由与 main.py 使用"""

    def __init__(self) -> None:
        self.store = TradingStore()
        self.alerter: Optional[EmailAlerter] = None
        self.paper: Optional[PaperGateway] = None
        self.risk: Optional[RiskManager] = None
        self.monitor: Optional[SignalMonitor] = None
        self.loop: Optional[LoopScheduler] = None
        self._ready = False
        self._started = False

    async def ensure_ready(self) -> None:
        """初始化全部组件（幂等）。路由层调用以保证未走 lifespan 时也可用。"""
        if self._ready:
            return
        await self.store.init()
        await self._seed_default_strategies()
        self.alerter = EmailAlerter(self.store)
        self.paper = PaperGateway(self.store)
        gateway_registry.register_gateway(self.paper)
        self.risk = RiskManager(self.store, self.alerter)
        self.monitor = SignalMonitor(self.store, self.alerter, self.risk)
        self.loop = LoopScheduler(self.store)
        self._ready = True
        logger.info("TradingFacade: 组件装配完成（broker_adapter=%s）",
                    gateway_registry.broker_adapter())

    async def _seed_default_strategies(self) -> None:
        """播种/迁移内置策略配置：首次全量播种（draft 状态，默认参数）；
        已有库则增量补种新注册策略（不动既有策略的参数与门闸状态）"""
        specs = await self.store.kv_get("strategies")
        existing_ids = {s.get("strategy_id") for s in (specs or [])}
        if specs is None:
            specs = []
        added = []
        for st in list_all_strategies():
            if st.id in existing_ids:
                continue
            specs.append({
                "strategy_id": st.id,
                "kind": st.id,
                "params": dict(st.default_params),
                "gate_state": GateState.DRAFT.value,
                "last_backtest": None,
                "paper_days_done": 0,
                "paper_days_required": 5,
                "enabled_codes": [],
                "live_enabled": False,
            })
            added.append(st.id)
        if added:
            await self.store.kv_set("strategies", specs)
            logger.info("TradingFacade: 播种/补种内置策略 %d 条：%s", len(added), added)

    async def start(self) -> None:
        """lifespan 启动入口：装配 + 并发跑监控循环与闭环调度（被 cancel 时优雅收尾）"""
        if self._started:
            return
        self._started = True
        await self.ensure_ready()
        assert self.monitor is not None and self.loop is not None
        try:
            await asyncio.gather(self.monitor.run(), self.loop.run())
        except asyncio.CancelledError:
            raise
        finally:
            await self._graceful_stop()

    async def _graceful_stop(self) -> None:
        """优雅停止：flush 邮件摘要队列、停闭环调度、关闭 store 连接（§2.4）"""
        if self.loop is not None:
            await self.loop.shutdown()
        if self.monitor is not None:
            await self.monitor.shutdown()
        try:
            await self.store.close()
        except Exception:
            logger.warning("TradingFacade: store 关闭异常", exc_info=True)


# 全局单例（main.py 与 routers/automation.py 装配入口）
trading_facade = TradingFacade()

# 把存储实例注册给基础设施端口：engine/data 层通过 server.infra.kv 读写，
# 不再反向依赖 brain 或 trading，消除 brain ↔ experts 的循环依赖。
from server.infra import kv as _kv_port  # noqa: E402

_kv_port.bind_store(lambda: trading_facade.store)
