"""自动交易核心包 - OrderGateway 抽象基类 + 网关注册表

架构蓝图 §1.2 / §11-1：注册表只挂 PaperGateway；
broker_adapter=null 时实盘物理不可达（D1 架构级保证）；
P1 拿到券商通道后新增适配器文件注册同名接口（qmt/ptrade/easytrader），上层零改动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .models import OrderRequest, OrderResult, Position


class OrderGateway(ABC):
    """下单网关统一接口：buy/sell/cancel/query_position/query_account"""

    id: str = ""

    @abstractmethod
    async def buy(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def sell(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def cancel(self, order_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def query_position(self, code: Optional[str] = None) -> List[Position]:
        raise NotImplementedError

    @abstractmethod
    async def query_account(self) -> Dict[str, Any]:
        raise NotImplementedError


_REGISTRY: Dict[str, OrderGateway] = {}


def register_gateway(gw: OrderGateway) -> None:
    """注册网关实例（P0 仅 PaperGateway）"""
    if not gw.id:
        raise ValueError("网关缺少 id")
    _REGISTRY[gw.id] = gw


def get_gateway(gateway_id: str) -> Optional[OrderGateway]:
    return _REGISTRY.get(gateway_id)


def current(target: str = "paper") -> Optional[OrderGateway]:
    """按目标取网关：paper → PaperGateway；live → 实盘适配器（当前不存在）"""
    if target == "live":
        return broker_adapter()
    return _REGISTRY.get("paper")


def broker_adapter() -> Optional[OrderGateway]:
    """实盘券商适配器（QMT/PTrade/easytrader P1 挂载点）。

    当前返回 None —— 实盘路径物理不可达（§6.3 三重防线之③）。
    """
    for gid in ("qmt", "ptrade", "easytrader"):
        if gid in _REGISTRY:
            return _REGISTRY[gid]
    return None
