"""DAPENG Alpha v4.0 - Agent 调度器（Orchestrator）

统一管理所有 Agent 的注册、调度、状态。
支持：
- 定时调度（asyncio.Task）
- 优先级队列（asyncio.Queue）
- Agent 间数据传递
- REST API 控制和查询
"""

import asyncio
import logging
from datetime import datetime
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from server.agents import AGENT_REGISTRY, AgentStatus, update_agent_status

logger = logging.getLogger(__name__)


# ============================================================
# 任务调度数据结构
# ============================================================

class TaskPriority(IntEnum):
    """任务优先级"""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


@dataclass
class AgentTask:
    """调度器任务"""
    agent_name: str
    func: Callable
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.NORMAL
    timeout: float = 60.0
    task_id: str = ""

    def __post_init__(self):
        if not self.task_id:
            self.task_id = f"{self.agent_name}_{datetime.now().strftime('%H%M%S%f')}"


class Orchestrator:
    """Agent 调度器 - 管理任务队列和执行"""

    def __init__(self):
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=200)
        self._results: Dict[str, Any] = {}
        self._running = False

    async def submit(self, task: AgentTask) -> Any:
        """提交任务并等待结果"""
        try:
            result = await asyncio.wait_for(
                task.func(*task.args, **task.kwargs),
                timeout=task.timeout
            )
            self._results[task.task_id] = result
            return result
        except asyncio.TimeoutError:
            logger.error("Orchestrator: 任务 %s 超时（%.0fs）", task.task_id, task.timeout)
            return {"error": f"任务超时（{task.timeout:.0f}s）"}
        except Exception as e:
            logger.error("Orchestrator: 任务 %s 失败: %s", task.task_id, str(e))
            return {"error": str(e)}

    async def submit_async(self, task: AgentTask) -> str:
        """提交任务到队列（不等待结果）"""
        priority_key = -int(task.priority)
        await self._queue.put((priority_key, task))
        return task.task_id

    def get_result(self, task_id: str) -> Optional[Any]:
        """获取任务结果"""
        return self._results.get(task_id)


# 全局调度器实例
_orchestrator: Optional[Orchestrator] = None


def get_orchestrator() -> Orchestrator:
    """获取全局调度器实例"""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator

# Agent 调度队列
_task_map: Dict[str, asyncio.Task] = {}  # type: ignore[type-arg]
_message_queue: Optional[asyncio.Queue] = None


async def start_agent(name: str, coro: Callable[[], Any], run_in_background: bool = True) -> bool:
    """
    启动一个 Agent

    Args:
        name: Agent 名称（需在 AGENT_REGISTRY 中注册）
        coro: Agent 的异步函数（通常是循环任务）
        run_in_background: 是否后台运行

    Returns:
        是否成功启动
    """
    agent_info = AGENT_REGISTRY.get(name)
    if not agent_info:
        logger.error("start_agent: Agent %s 未注册", name)
        return False

    if not agent_info.enabled:
        logger.warning("start_agent: Agent %s 已被禁用，跳过启动", name)
        return False

    if name in _task_map and not _get_task(name).done():
        logger.warning("start_agent: Agent %s 已在运行", name)
        return False

    try:
        loop = asyncio.get_event_loop()
        task = loop.create_task(coro())
        _task_map[name] = task
        update_agent_status(name, AgentStatus.RUNNING)
        logger.info("Agent %s 已启动", name)
        return True
    except Exception as e:
        logger.error("start_agent: 启动 %s 失败: %s", name, str(e))
        update_agent_status(name, AgentStatus.ERROR, error_msg=str(e))
        return False


def stop_agent(name: str) -> bool:
    """
    停止一个 Agent

    Returns:
        是否成功停止
    """
    task = _task_map.get(name)
    if not task or task.done():
        logger.warning("stop_agent: Agent %s 未运行", name)
        return False

    try:
        task.cancel()
        update_agent_status(name, AgentStatus.IDLE)
        logger.info("Agent %s 已停止", name)
        return True
    except Exception as e:
        logger.error("stop_agent: 停止 %s 失败: %s", name, str(e))
        return False


def get_agent_status(name: str) -> Optional[Dict[str, Any]]:
    """获取 Agent 运行状态（含任务是否存活）"""
    agent_info = AGENT_REGISTRY.get(name)
    if not agent_info:
        return None

    task = _task_map.get(name)
    is_alive = task is not None and not task.done()

    return {
        **agent_info.to_dict(),
        "is_alive": is_alive,
        "task_done": task.done() if task else None,
        "task_exception": str(task.exception()) if task and task.done() and task.exception() else None,
    }


def list_all_status() -> List[Dict[str, Any]]:
    """列出所有 Agent 运行状态"""
    return [get_agent_status(name) for name in AGENT_REGISTRY.keys()]


async def push_to_queue(data: Any):
    """向消息队列推送数据（Agent 间通信）"""
    global _message_queue
    if _message_queue is None:
        _message_queue = asyncio.Queue(maxsize=100)
    try:
        await _message_queue.put(data)
    except asyncio.QueueFull:
        logger.warning("Agent 消息队列已满，丢弃数据")


async def pop_from_queue() -> Any:
    """从消息队列取出数据（Agent 间通信）"""
    global _message_queue
    if _message_queue is None:
        _message_queue = asyncio.Queue(maxsize=100)
    return await _message_queue.get()


def _get_task(name: str) -> Optional[asyncio.Task]:  # type: ignore[type-arg]
    """获取 Agent 的 asyncio Task"""
    task = _task_map.get(name)
    if task is not None and task.done():
        # 清理已完成的任务
        del _task_map[name]
        return None
    return task


async def start_all_enabled():
    """启动所有已启用的 Agent"""
    from server.agents import AGENT_REGISTRY

    started = []
    for name, agent_info in AGENT_REGISTRY.items():
        if agent_info.enabled:
            # 每个 Agent 需要自己定义 `create_task()` 工厂函数
            # 这里只记录需要启动，具体启动由各 Agent 模块自己负责
            started.append(name)

    logger.info("待启动 Agent: %s", ", " .join(started))
    return started


async def stop_all():
    """停止所有运行中的 Agent"""
    names = list(_task_map.keys())
    for name in names:
        stop_agent(name)
    logger.info("已停止全部 Agent（%d 个）", len(names))


async def health_check() -> Dict[str, Any]:
    """
    Agent 系统健康检查

    Returns:
        {
            "ok": bool,
            "total": int,
            "running": int,
            "error": int,
            "disabled": int,
            "details": [...],
        }
    """
    total = 0
    running = 0
    error = 0
    disabled = 0
    details = []

    for name in AGENT_REGISTRY.keys():
        total += 1
        status = get_agent_status(name)
        if status:
            details.append(status)
            if status["status"] == "running":
                running += 1
            elif status["status"] == "error":
                error += 1
            elif status["status"] == "disabled":
                disabled += 1

    ok = error == 0
    return {
        "ok": ok,
        "total": total,
        "running": running,
        "error": error,
        "disabled": disabled,
        "details": details,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
