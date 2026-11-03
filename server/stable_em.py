"""东财通道与熔断：push2 抓取、失败计数、后台探测"""

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
import requests
from server.sector_map import infer_level

from server.stable_cache import logger

# =====================================================================
#  东财取数加固（V16.6）：多节点 HTTPS 候选 + 桌面 Chrome 请求头 + 短超时轮询
# =====================================================================
# 说明：东财 push2.* / datacenter-web.* 在部分网络下 HTTP 被重置、且单节点
# 可能 502，故统一改为 HTTPS 并依次尝试多个节点；全部失败才回落到本地样本。
_EM_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}
# push2 实时行情节点候选（依次尝试）
_EM_PUSH2_NODES: List[str] = [
    "https://push2.eastmoney.com",
    "https://82.push2.eastmoney.com",
    "https://90.push2.eastmoney.com",
]
# 数据中心节点候选（龙虎榜等）
_EM_DATACENTER_NODES: List[str] = [
    "https://datacenter-web.eastmoney.com",
]
_EM_PUSH2_PATH = "/api/qt/clist/get"
_EM_DATACENTER_PATH = "/api/data/v1/get"
# 板块日K（多周期收益）节点
_EM_PUSH2HIS_NODES: List[str] = [
    "https://push2his.eastmoney.com",
]
_EM_KLINE_PATH = "/api/qt/stock/kline/get"
# 单节点超时（秒）；依次尝试，全失败再走兜底
_EM_NODE_TIMEOUT = 6

# ---- 板块多周期收益（真实日K聚合，替代旧的 chg*系数 推算）----
_BOARD_RETURN_PERIODS = (5, 20, 60)
# 板块日K并发抓取的时间预算与并发度（超预算的板块多周期收益置 None）
_BOARD_RETURNS_BUDGET = 8.0
_BOARD_RETURNS_WORKERS = 8

# ---- ③ 级降级：腾讯成分股行情聚合（东财板块源不可用时）----
# 分片大小：实测 448 只成分股按 224/片（2 片并发）约 3.7s，恰好压在 5s 预算内
_TENCENT_QUOTE_CHUNK = 224
_TENCENT_AGG_SOURCE = "腾讯行情聚合（东财板块源不可用）"
_TENCENT_AGG_NOTE = (
    "由腾讯成分股实时行情聚合，与东财板块指数量纲/口径可能不同；"
    "腾讯无资金流字段，net_flow 与多周期收益不可用"
)

# 龙虎榜报表字段（东财已将字段名由 BUY_AMT/SELL_AMT/NET_AMT 变更为 BILLBOARD_*）
# EXPLAIN = 东财真实"席位性质"文本（如"普通席位卖出，成功率45.01%"），用于 branch_top/机构判定
_EM_LHB_COLUMNS = (
    "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,CLOSE_PRICE,CHANGE_RATE,"
    "EXPLANATION,EXPLAIN,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,"
    "BILLBOARD_NET_AMT,BILLBOARD_DEAL_AMT"
)


def _em_get_json(
    path: str, params: Dict[str, Any], nodes: List[str]
) -> Optional[Dict[str, Any]]:
    """依次尝试东财多节点(HTTPS)，返回首个可解析的 JSON dict。

    每个节点使用统一桌面 Chrome 请求头与短超时；失败时以 `logger.warning`
    记录**具体节点与异常类型**；全部节点失败返回 None（由调用方决定是否兜底）。
    """
    for node in nodes:
        url = node.rstrip("/") + path
        try:
            resp = requests.get(
                url, params=params, headers=_EM_HEADERS, timeout=_EM_NODE_TIMEOUT
            )
            if resp.status_code != 200:
                logger.warning(
                    f"EastMoney node {node} HTTP {resp.status_code} for {path}"
                )
                continue
            payload = resp.json()
            if isinstance(payload, dict):
                return payload
            logger.warning(
                f"EastMoney node {node} returned non-dict payload for {path}"
            )
        except Exception as e:  # noqa: BLE001 - 任意网络异常都记录并尝试下一节点
            logger.warning(
                f"EastMoney node {node} failed for {path}: {type(e).__name__}: {e}"
            )
    logger.warning(f"EastMoney all nodes unreachable for {path}: {nodes}")
    return None


# =====================================================================
#  push2 短期熔断（不是永久禁用）：连续失败 N 次 → 冷却 C 秒 → 半开重试
#    - closed    : 正常，请求内真实探测
#    - open      : 冷却期内跳过探测（直接进 ②/③ 级），不发任何网络请求
#    - half_open : 冷却到期 → **后台异步**探测一次（不在用户请求里等结果），
#                  成功→closed，失败→重新 open。绝不永久关闭 ① 级。
#  半开探测非阻塞 + 单飞（_probe_inflight）防堆积；状态变量用锁保护。
# =====================================================================
_BREAKER_FAIL_THRESHOLD = 3     # 连续失败次数阈值
# 冷却必须 > 路由缓存 TTL（CACHE_TTL_SECTOR_RANK=60s），否则每次缓存到期都与
# 冷却到期重合、必然走 half_open 探测，熔断就永远"跳不过"探测。取 120s。
_BREAKER_COOLDOWN = 120.0       # 冷却秒数
# ③ 级聚合结果的内存缓存 TTL：必须 > 熔断冷却(120s)+路由缓存(60s)，否则冷却到期
# 那次请求会因聚合缓存过期而重新拉 448 只成分股（~6s）。取 300s（降级口径，可接受）
_TENCENT_AGG_CACHE_TTL = 300

_breaker_lock = threading.Lock()   # 保护下面全部熔断状态（并发安全）
_breaker_state = "closed"          # closed | open | half_open
_breaker_fail_count = 0
_breaker_open_until = 0.0
_probe_inflight = False            # 是否有后台探测在飞（防堆积）
_probe_task = None                 # 保活后台任务引用，防被 GC


def _fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def push2_breaker_state() -> str:
    """对外可观测状态：'closed' | 'open'（冷却中）| 'half_open'（后台探测在飞）。"""
    with _breaker_lock:
        state = _breaker_state
    return state if state in ("open", "half_open") else "closed"


def _breaker_success() -> None:
    global _breaker_state, _breaker_fail_count
    with _breaker_lock:
        was = _breaker_state
        _breaker_state = "closed"
        _breaker_fail_count = 0
    if was != "closed":
        logger.info("push2 breaker CLOSED (recovered): was=%s", was)


def _breaker_failure() -> None:
    global _breaker_state, _breaker_fail_count, _breaker_open_until
    opened = False
    with _breaker_lock:
        _breaker_fail_count += 1
        fc = _breaker_fail_count
        was = _breaker_state
        opened = (was == "half_open") or (fc >= _BREAKER_FAIL_THRESHOLD)
        if opened:
            _breaker_state = "open"
            _breaker_open_until = time.time() + _BREAKER_COOLDOWN
            until = _breaker_open_until
    if opened:
        logger.info(
            "push2 breaker OPEN: fail_count=%d, cooldown=%.0fs, open_until=%s",
            fc, _BREAKER_COOLDOWN, _fmt_ts(until),
        )


def _breaker_maybe_launch_probe(params: Dict[str, Any]) -> bool:
    """在事件循环线程内决定本次请求是否真实探测。

    True ：closed —— 当前请求内同步探测（run_in_executor）。
    False：open/half_open —— 跳过探测（直接进 ②/③ 级）；若冷却刚到期且无在飞
           探测，则**启动一次后台探测**（非阻塞），当前请求不等结果。
    """
    global _breaker_state, _probe_inflight
    launch = False
    fc = 0
    with _breaker_lock:
        if _breaker_state == "closed":
            return True
        if _breaker_state == "open" and time.time() >= _breaker_open_until:
            if not _probe_inflight:
                _probe_inflight = True
                _breaker_state = "half_open"
                launch = True
                fc = _breaker_fail_count
    if launch:
        logger.info(
            "push2 breaker HALF_OPEN: cooldown elapsed (fail_count=%d), background probe",
            fc,
        )
        _schedule_background_probe(params)
    return False


def _schedule_background_probe(params: Dict[str, Any]) -> None:
    """用 asyncio.create_task 后台发起一次探测（不阻塞当前请求）。"""
    global _probe_task, _probe_inflight
    try:
        _probe_task = asyncio.get_event_loop().create_task(_background_probe(params))
    except RuntimeError as e:  # 无运行中的事件循环（理论不应发生）
        logger.warning("push2 background probe not scheduled: %s", e)
        with _breaker_lock:
            _probe_inflight = False


async def _background_probe(params: Dict[str, Any]) -> None:
    """后台半开探测：成功→关闭熔断；失败→重新进入冷却。均只记日志。"""
    global _probe_inflight
    loop = asyncio.get_event_loop()
    payload = None
    try:
        payload = await loop.run_in_executor(
            None, lambda: _em_get_json(_EM_PUSH2_PATH, params, _EM_PUSH2_NODES)
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("push2 background probe error: %s: %s", type(e).__name__, e)
        payload = None
    with _breaker_lock:
        _probe_inflight = False
    if payload is not None:
        _breaker_success()
    else:
        _breaker_failure()


async def _pull_push2(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """带熔断的 push2 取数（异步）。冷却/半开期直接返回 None，绝不阻塞等待探测。"""
    if not _breaker_maybe_launch_probe(params):
        return None
    loop = asyncio.get_event_loop()
    payload = await loop.run_in_executor(
        None, lambda: _em_get_json(_EM_PUSH2_PATH, params, _EM_PUSH2_NODES)
    )
    if payload is not None:
        _breaker_success()
    else:
        _breaker_failure()
    return payload
