"""自动交易核心包 - 交易时段判定 + 扫描节拍

架构蓝图 §2.3：仅按本地时间判定，工作日近似（无法定节假日日历库，
节假日空扫描无害：行情不更新 → 无新信号 → 天然静默）。
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Literal

SessionPhase = Literal["premarket", "morning", "lunch", "afternoon", "closed"]

# 盘中扫描间隔（与 CACHE_TTL_QUOTES=60 对齐，§2.2）
SCAN_INTERVAL_SECONDS = 60
# 单轮性能预算兜底：整轮超时作废（§2.2）
SCAN_ROUND_TIMEOUT_SECONDS = 90
# K 线预取：受控并发度与预算。K 线无批量接口，逐只串行在 ~484 只标的下
# 必然超过整轮预算（曾致每轮 TimeoutError 作废）。改为受控并发 + 预取预算，
# 到预算仍未完成的放弃、**已完成部分照常用于判定（迟到不弃）**。
# 实测（冷启动整池 484 只，腾讯单只 ~1.7s）：并发 6 → 253/484@70s；
# 并发 12 → 473/484@70s；并发 20 → 482/484@44.5s。取 16 兼顾覆盖与出口压力。
SCAN_KLINE_CONCURRENCY = 16
SCAN_PREFETCH_BUDGET_SECONDS = 70   # 必须 < SCAN_ROUND_TIMEOUT_SECONDS，给评估/分发留余量

_T_0915 = time(9, 15)
_T_0930 = time(9, 30)
_T_1130 = time(11, 30)
_T_1300 = time(13, 0)
_T_1500 = time(15, 0)
# 收盘扫描窗口：15:05 - 15:10（日线收定后跑日线级评估）
_CLOSE_SCAN_START = time(15, 5)
_CLOSE_SCAN_END = time(15, 10)


def session_phase(now: datetime) -> SessionPhase:
    """交易时段判定（§2.3）

    周一至周五 + 09:15-09:30 → premarket（可选摘要）
    09:30-11:30 → morning；11:30-13:00 → lunch（静默）
    13:00-15:00 → afternoon；其余 → closed
    周末一律 closed。
    """
    if now.weekday() >= 5:  # 周六=5 周日=6
        return "closed"
    t = now.time()
    if _T_0915 <= t < _T_0930:
        return "premarket"
    if _T_0930 <= t < _T_1130:
        return "morning"
    if _T_1130 <= t < _T_1300:
        return "lunch"
    if _T_1300 <= t < _T_1500:
        return "afternoon"
    return "closed"


def is_trading_phase(phase: SessionPhase) -> bool:
    """是否盘中（应跑 60s 轮扫描）"""
    return phase in ("morning", "afternoon")


def is_close_scan_window(now: datetime) -> bool:
    """是否收盘扫描窗口（工作日 15:05-15:10，每日一轮全量）"""
    if now.weekday() >= 5:
        return False
    return _CLOSE_SCAN_START <= now.time() < _CLOSE_SCAN_END


def backoff_sleep_seconds(consecutive_errors: int, base: int = SCAN_INTERVAL_SECONDS) -> int:
    """崩溃自愈退避（§2.5）：连续失败 ≥5 → 60s→120s→…→上限 600s，恢复后清零"""
    if consecutive_errors < 5:
        return base
    shift = min(consecutive_errors - 4, 4)  # 5→120, 6→240, 7→480, ≥8→600
    return min(600, base * (2 ** shift))
