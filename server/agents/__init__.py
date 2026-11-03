"""DAPENG Alpha v4.0 Agent 架构 - Agent 注册表"""
from typing import Dict, List, Optional, Type
from enum import Enum

class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"
    DISABLED = "disabled"

class AgentInfo:
    def __init__(self, name: str, description: str, enabled: bool = True):
        self.name = name
        self.description = description
        self.enabled = enabled
        self.status = AgentStatus.IDLE
        self.last_run = None
        self.error_msg = None

    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "status": self.status.value,
            "last_run": self.last_run,
            "error_msg": self.error_msg,
        }


# ============================================================
# Agent 注册表
# ============================================================
AGENT_REGISTRY: Dict[str, AgentInfo] = {

    # 选股 Agent - 封装 scan.py 的扫描逻辑
    "stock_screener": AgentInfo(
        name="选股Agent",
        description="全市场智能选股扫描，策略评分筛选优质标的",
        enabled=True,
    ),

    # 分析 Agent - 调用 DeepSeek V4 Pro 生成深度分析
    "stock_analyzer": AgentInfo(
        name="分析Agent",
        description="AI深度分析个股，基于DeepSeek V4 Pro生成投资策略",
        enabled=True,
    ),

    # 新闻分析 Agent - 获取个股新闻/公告，进行情绪分析
    "news_analyzer": AgentInfo(
        name="新闻分析Agent",
        description="获取个股新闻公告，情绪分析，提取关键事件标签",
        enabled=True,
    ),

    # 金融分析 Agent - 整合宏观数据+板块资金流向
    "finance_analyzer": AgentInfo(
        name="金融分析Agent",
        description="整合宏观数据(GDP/CPI/PMI)和板块资金流向，输出市场情绪评分",
        enabled=True,
    ),

    # 回测 Agent - 验证策略有效性
    "backtest_runner": AgentInfo(
        name="回测Agent",
        description="策略回测引擎，计算收益率/最大回撤/夏普比率",
        enabled=True,
    ),

    # 监控 Agent - 定时检查行情，触发告警
    "alert_agent": AgentInfo(
        name="监控Agent",
        description="定时监控自选股行情，触发价格/技术指标告警",
        enabled=False,  # 默认关闭，需配置告警条件
    ),

    # 股票池管理 Agent - 动态更新股票池
    "stock_pool_manager": AgentInfo(
        name="股票池管理Agent",
        description="动态更新股票池，根据市场热点调整优先级",
        enabled=True,
    ),
}


def get_agent(name: str) -> Optional[AgentInfo]:
    """获取 Agent 信息"""
    return AGENT_REGISTRY.get(name)


def list_agents() -> List[dict]:
    """列出所有 Agent 状态"""
    return [agent.to_dict() for agent in AGENT_REGISTRY.values()]


def enable_agent(name: str):
    """启用 Agent"""
    agent = get_agent(name)
    if agent:
        agent.enabled = True
        return True
    return False


def disable_agent(name: str):
    """禁用 Agent"""
    agent = get_agent(name)
    if agent:
        agent.enabled = False
        return True
    return False


def update_agent_status(name: str, status: AgentStatus, error_msg: str = None):
    """更新 Agent 状态"""
    agent = get_agent(name)
    if agent:
        agent.status = status
        if status == AgentStatus.RUNNING:
            import datetime
            agent.last_run = datetime.datetime.now().isoformat()
        if error_msg:
            agent.error_msg = error_msg
        return True
    return False
