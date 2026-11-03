"""DAPENG Alpha v4.0 - 监控 Agent（alert_agent）

定时检查行情，触发价格/技术指标告警。
支持：价格突破、涨跌幅告警、量能异常、技术指标信号。

配置通过 config.AGENT_CONFIG 读取。
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from server.agents import AgentStatus, update_agent_status
from server.config import AGENT_CONFIG, get_all_stock_codes, get_sector_stocks
from server.westock import westock

logger = logging.getLogger(__name__)

# 告警条件模板（可扩展）
_DEFAULT_ALERT_RULES = [
    {"type": "price_breakout", "desc": "价格突破前高"},
    {"type": "price_breakdown", "desc": "价格跌破前低"},
    {"type": "change_pct_up", "threshold": 5.0, "desc": "涨幅超5%"},
    {"type": "change_pct_down", "threshold": -5.0, "desc": "跌幅超-5%"},
    {"type": "volume_spike", "desc": "成交量异常放大"},
]


class AlertAgent:
    """监控 Agent - 定时轮询行情，触发告警"""

    def __init__(self):
        self.enabled = False
        self.poll_interval = AGENT_CONFIG.get("agent_poll_interval", 60)
        self.watch_list: List[str] = []  # 监控股票代码列表
        self.alert_rules: List[Dict] = _DEFAULT_ALERT_RULES.copy()
        self.alert_history: List[Dict] = []
        self.max_history = 100
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._running = False

    async def start(self):
        """启动监控循环"""
        if self._running:
            logger.warning("AlertAgent: 已在运行")
            return
        self.enabled = True
        self._running = True
        update_agent_status("alert_agent", AgentStatus.RUNNING)
        logger.info("AlertAgent: 启动，轮询间隔 %ds", self.poll_interval)
        await self._run_loop()

    async def stop(self):
        """停止监控"""
        self._running = False
        self.enabled = False
        if self._task and not self._task.done():
            self._task.cancel()
        update_agent_status("alert_agent", AgentStatus.IDLE)
        logger.info("AlertAgent: 已停止")

    async def _run_loop(self):
        """主循环（含交易时段门控）。

        修复：原来 24/7 每 poll_interval 打 network（batch_quotes），无 session 判断。
        现在仅 **盘前/盘中** 真正轮询；午休/收盘后/周末拉长间隔休眠（不打网络）。
        时段判定复用 server.trading.scheduler.session_phase，不自造一套。
        """
        from server.trading import scheduler

        idle_sleep = max(self.poll_interval, 900)  # 非交易时段 15min
        while self._running:
            phase = scheduler.session_phase(datetime.now())
            active = phase in ("premarket", "morning", "afternoon")
            if active:
                try:
                    await self._check_once()
                except Exception as e:
                    logger.error("AlertAgent: 循环异常 %s", str(e))
                    update_agent_status("alert_agent", AgentStatus.ERROR, error_msg=str(e))
            else:
                logger.debug("AlertAgent: 非交易时段(%s)，休眠 %ds", phase, idle_sleep)

            await asyncio.sleep(self.poll_interval if active else idle_sleep)

    async def _check_once(self):
        """执行一次全量检查"""
        # 如果没有配置监控列表，使用默认（全部股票的前20只）
        if not self.watch_list:
            all_codes = get_all_stock_codes()  # 返回 List[str]
            self.watch_list = all_codes[:20]

        codes = self.watch_list
        try:
            quotes_list = await westock.batch_quotes(codes)
            # batch_quotes 返回 List[Dict]
            quotes_map = {q.get("code", ""): q for q in quotes_list if q}
        except Exception as e:
            logger.warning("AlertAgent: 获取行情失败 %s", str(e))
            return

        for code, data in quotes_map.items():
            if not data:
                continue
            try:
                alerts = self._check_alerts(code, data)
                for alert in alerts:
                    await self._trigger_alert(alert)
            except Exception as e:
                logger.warning("AlertAgent: 检查 %s 失败 %s", code, str(e))

    def _check_alerts(self, code: str, data: Dict) -> List[Dict]:
        """检查单只股票是否触发告警"""
        alerts = []
        change_pct = float(data.get("chg", 0))
        price = float(data.get("price", 0))
        volume = float(data.get("volume", 0))

        for rule in self.alert_rules:
            rule_type = rule.get("type", "")

            if rule_type == "change_pct_up":
                threshold = rule.get("threshold", 5.0)
                if change_pct >= threshold:
                    alerts.append({
                        "code": code,
                        "type": rule_type,
                        "desc": rule.get("desc", f"涨幅超{threshold}%"),
                        "value": change_pct,
                        "price": price,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })

            elif rule_type == "change_pct_down":
                threshold = rule.get("threshold", -5.0)
                if change_pct <= threshold:
                    alerts.append({
                        "code": code,
                        "type": rule_type,
                        "desc": rule.get("desc", f"跌幅超{threshold}%"),
                        "value": change_pct,
                        "price": price,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })

            # 可扩展更多规则...

        return alerts

    async def _trigger_alert(self, alert: Dict):
        """触发告警（推送到消息队列 + 记录历史）"""
        logger.info("🚨 ALERT [%s] %s：%s (涨跌幅 %+.2f%%)",
                    alert["code"], alert["type"], alert["desc"], alert["value"])

        # 记录历史
        self.alert_history.append(alert)
        if len(self.alert_history) > self.max_history:
            self.alert_history = self.alert_history[-self.max_history:]

        # 推送到消息队列（供其他 Agent 消费）
        try:
            from server.agents.orchestrator import push_to_queue
            await push_to_queue({"type": "alert", "data": alert})
        except Exception:
            pass  # 队列未初始化时忽略

    def add_watch_stock(self, code: str):
        """添加监控股票"""
        if code not in self.watch_list:
            self.watch_list.append(code)
            logger.info("AlertAgent: 添加监控 %s", code)

    def remove_watch_stock(self, code: str):
        """移除监控股票"""
        if code in self.watch_list:
            self.watch_list.remove(code)
            logger.info("AlertAgent: 移除监控 %s", code)

    def get_recent_alerts(self, limit: int = 20) -> List[Dict]:
        """获取最近告警"""
        return self.alert_history[-limit:][::-1]


# 全局监控 Agent 实例
_alert_agent: Optional[AlertAgent] = None


def get_alert_agent() -> AlertAgent:
    """获取全局 AlertAgent 实例"""
    global _alert_agent
    if _alert_agent is None:
        _alert_agent = AlertAgent()
    return _alert_agent
