"""基础工具箱：类型别名、取值适配、rolling/EMA/Wilder、TR 与 DMI 原语"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import numpy as np

# 一个 pane 的序列：与 K 线等长，缺数处为 None
PaneSeries = List[Optional[float]]
# 指标输出：pane 名 -> 序列
PaneMap = Dict[str, PaneSeries]

_EPS = 1e-12
NaN = float("nan")


# ============================================================
# 0. 输入归一化
# ============================================================

_ALIAS_KEYS = {
    "o": ("o", "open"),
    "h": ("h", "high"),
    "l": ("l", "low"),
    "c": ("c", "close", "price"),
    "v": ("v", "vol", "volume"),
}


def _pick(row: Mapping[str, Any], keys: Sequence[str]) -> Any:
    """按候选键顺序取第一个存在的值；都不存在返回 None。"""
    for k in keys:
        if k in row:
            return row[k]
    return None


def _to_float(value: Any) -> float:
    """宽松转 float；失败返回 NaN（由上层统一转 None）。"""
    if value is None:
        return NaN
    try:
        f = float(value)
    except (TypeError, ValueError):
        return NaN
    return f if math.isfinite(f) else NaN


def extract_ohlcv(klines: Any) -> Dict[str, np.ndarray]:
    """把多种形态的 K 线入参归一成 ``{o,h,l,c,v}`` 五个 float64 数组。

    支持三种形态：

    1. ``List[dict]``：键 ``o/h/l/c/v``（本项目上游口径），兼容
       ``open/high/low/close/volume``；
    2. ``Dict[str, Sequence]``：键名同上，值是等长序列；
    3. ``np.ndarray``：二维，列序 ``o,h,l,c,v``（允许 >5 列，多余列忽略）。

    Returns:
        字典，含 ``o/h/l/c/v`` 五个 ``np.ndarray``（等长、float64）；
        非法输入（None / 空 / 列数不足）返回长度 0 的五个空数组，
        调用方据此走「数据不足」分支，**不抛异常**。
    """
    empty = {
        "o": np.zeros(0, dtype=float),
        "h": np.zeros(0, dtype=float),
        "l": np.zeros(0, dtype=float),
        "c": np.zeros(0, dtype=float),
        "v": np.zeros(0, dtype=float),
    }
    if klines is None:
        return empty

    # 形态 3：numpy 二维数组
    if isinstance(klines, np.ndarray):
        arr = np.asarray(klines, dtype=float)
        if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] < 5:
            return empty
        return {
            "o": np.ascontiguousarray(arr[:, 0]),
            "h": np.ascontiguousarray(arr[:, 1]),
            "l": np.ascontiguousarray(arr[:, 2]),
            "c": np.ascontiguousarray(arr[:, 3]),
            "v": np.ascontiguousarray(arr[:, 4]),
        }

    # 形态 2：dict of sequences
    if isinstance(klines, Mapping):
        cols: Dict[str, List[float]] = {}
        for std, keys in _ALIAS_KEYS.items():
            src = None
            for k in keys:
                if k in klines:
                    src = klines[k]
                    break
            if src is None:
                cols[std] = []
                continue
            cols[std] = [_to_float(x) for x in src]
        lengths = {len(v) for v in cols.values() if v}
        if len(lengths) != 1:
            # 列缺失或长度不一致：按最短对齐，缺列整列为 NaN
            n = min(lengths) if lengths else 0
            cols = {k: (v[:n] if v else [NaN] * n) for k, v in cols.items()}
        return {k: np.asarray(v, dtype=float) for k, v in cols.items()}

    # 形态 1：list of dict
    if isinstance(klines, (list, tuple)):
        if len(klines) == 0:
            return empty
        out: Dict[str, List[float]] = {k: [] for k in ("o", "h", "l", "c", "v")}
        for row in klines:
            if not isinstance(row, Mapping):
                # 列表里混入非 dict：用 NaN 占位以保持等长（后续自然转 None）
                for k in out:
                    out[k].append(NaN)
                continue
            for std, keys in _ALIAS_KEYS.items():
                val = _pick(row, keys)
                # 成交量为空时按 0 参与「量」相关的加权（不影响价格为 None 的语义）
                out[std].append(_to_float(val))
        return {
            "o": np.asarray(out["o"], dtype=float),
            "h": np.asarray(out["h"], dtype=float),
            "l": np.asarray(out["l"], dtype=float),
            "c": np.asarray(out["c"], dtype=float),
            "v": np.asarray(out["v"], dtype=float),
        }

    return empty


def _session_keys(klines: Any, n: int) -> Optional[List[str]]:
    """提取每根 bar 的「交易日」键，用于 VWAP 的盘中重置。

    依次尝试 ``d`` / ``t`` / ``date`` / ``time`` 字段，取纯数字前 8 位
    （``202610191130`` -> ``20261019``）或前 10 字符（``2026-10-19 11:30`` -> ``2026-10-19``）。
    取不到（numpy 入参 / 无时间字段）返回 None，调用方退化为「整段锚定」。
    """
    if not isinstance(klines, (list, tuple)) or len(klines) != n or n == 0:
        return None
    keys: List[str] = []
    for row in klines:
        if not isinstance(row, Mapping):
            return None
        raw = _pick(row, ("d", "t", "date", "time"))
        if raw is None:
            return None
        s = str(raw).strip()
        if not s:
            return None
        digits = "".join(ch for ch in s if ch.isdigit())
        if len(digits) >= 8:
            keys.append(digits[:8])
        else:
            keys.append(s[:10])
    return keys


# ============================================================
# 1. 通用数学工具（缺数一律 NaN，最后由 _to_series 转 None）
# ============================================================


def _to_series(arr: np.ndarray) -> PaneSeries:
    """``np.ndarray``（含 NaN/inf）-> ``List[Optional[float]]``，非有限值转 None。"""
    out: PaneSeries = []
    for x in np.asarray(arr, dtype=float).tolist():
        fx = float(x)
        out.append(fx if math.isfinite(fx) else None)
    return out


def _window_view(arr: np.ndarray, period: int) -> Optional[np.ndarray]:
    """返回 ``(n-period+1, period)`` 的滑动窗口视图；长度不足返回 None。"""
    n = arr.size
    if period <= 0 or n < period:
        return None
    return np.lib.stride_tricks.sliding_window_view(arr, period)


def _rolling_mean(arr: np.ndarray, period: int) -> np.ndarray:
    """简单移动平均，前置 ``period-1`` 个为 NaN；窗口内含 NaN 则结果 NaN。"""
    out = np.full(arr.size, NaN)
    win = _window_view(arr, period)
    if win is None:
        return out
    out[period - 1:] = win.mean(axis=1)
    return out


def _rolling_sum(arr: np.ndarray, period: int) -> np.ndarray:
    """滚动求和，前置 ``period-1`` 个为 NaN。"""
    out = np.full(arr.size, NaN)
    win = _window_view(arr, period)
    if win is None:
        return out
    out[period - 1:] = win.sum(axis=1)
    return out


def _rolling_max(arr: np.ndarray, period: int) -> np.ndarray:
    """滚动最大值（含当前 bar），前置 ``period-1`` 个为 NaN。"""
    out = np.full(arr.size, NaN)
    win = _window_view(arr, period)
    if win is None:
        return out
    out[period - 1:] = win.max(axis=1)
    return out


def _rolling_min(arr: np.ndarray, period: int) -> np.ndarray:
    """滚动最小值（含当前 bar），前置 ``period-1`` 个为 NaN。"""
    out = np.full(arr.size, NaN)
    win = _window_view(arr, period)
    if win is None:
        return out
    out[period - 1:] = win.min(axis=1)
    return out


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    """指数移动平均（``alpha = 2/(period+1)``，以首值为种子，等同 Pine ``ta.ema``）。"""
    n = arr.size
    out = np.full(n, NaN)
    if n == 0 or period <= 0:
        return out
    alpha = 2.0 / (period + 1.0)
    prev = arr[0]
    out[0] = prev
    for i in range(1, n):
        prev = alpha * arr[i] + (1.0 - alpha) * prev
        out[i] = prev
    return out


def _wilder(arr: np.ndarray, period: int, start: int = 0) -> np.ndarray:
    """Wilder 平滑（RMA）：种子取首个「完整且有限」窗口的**均值**，其后递推。

    ``out[s+period-1] = mean(arr[s:s+period])``；
    ``out[i] = out[i-1] + (arr[i]-out[i-1])/period``。

    种子窗口从 ``start`` 起向后找第一个不含 NaN/inf 的窗口（DI 在「首窗口无方向运动」
    时会出现 0/0，直接把种子打成 NaN；跳过它而不是让整条序列作废）。
    与 ``pandas ewm(alpha=1/period, adjust=False)`` 在相同种子下逐点等价（单测对拍）。
    """
    n = arr.size
    out = np.full(n, NaN)
    if period <= 0 or start < 0:
        return out
    seed_at = -1
    for s in range(start, n - period + 1):
        if np.all(np.isfinite(arr[s:s + period])):
            seed_at = s
            break
    if seed_at < 0:
        return out
    prev = float(np.mean(arr[seed_at:seed_at + period]))
    out[seed_at + period - 1] = prev
    for i in range(seed_at + period, n):
        val = arr[i]
        if not math.isfinite(float(val)):
            # 脏数据不传播成 0：该点保持 NaN，但后续继续递推
            out[i] = prev
            continue
        prev = prev + (float(val) - prev) / period
        out[i] = prev
    return out


def _safe_div(num: np.ndarray, den: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """逐元素除法；分母为 0 / 非有限 -> NaN（禁止用 0 兜底）。"""
    out = np.full(num.size, NaN)
    good = np.isfinite(num) & np.isfinite(den) & (np.abs(den) > _EPS)
    if np.any(good):
        out[good] = num[good] / den[good] * scale
    return out


def _true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    """真实波幅 TR。

    第 0 根按 Pine ``ta.tr`` 约定取 ``high-low``（无前收），此后
    ``max(h-l, |h-c_prev|, |l-c_prev|)``。
    """
    n = c.size
    tr = np.full(n, NaN)
    if n == 0:
        return tr
    tr[0] = h[0] - l[0]
    if n > 1:
        prev_c = c[:-1]
        tr[1:] = np.maximum(
            h[1:] - l[1:],
            np.maximum(np.abs(h[1:] - prev_c), np.abs(l[1:] - prev_c)),
        )
    return tr


def _directional_movement(
    h: np.ndarray, l: np.ndarray
) -> Tuple[np.ndarray, np.ndarray]:
    """方向运动 +DM / -DM（Wilder 定义，第 0 根为 0）。

    ``up = h[i]-h[i-1]``，``down = l[i-1]-l[i]``；
    ``+DM = up if up>down and up>0 else 0``，``-DM = down if down>up and down>0 else 0``。
    """
    n = h.size
    plus = np.zeros(n, dtype=float)
    minus = np.zeros(n, dtype=float)
    if n < 2:
        return plus, minus
    up = np.diff(h)
    down = -np.diff(l)
    plus[1:] = np.where((up > down) & (up > 0), up, 0.0)
    minus[1:] = np.where((down > up) & (down > 0), down, 0.0)
    return plus, minus


def _dmi(
    h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """DMI 公共内核：返回 ``(plus_di, minus_di, dx, first_valid_index)``。

    ``+DI = 100 * RMA(+DM) / ATR``，``-DI = 100 * RMA(-DM) / ATR``，
    ``DX = 100 * |+DI - -DI| / (+DI + -DI)``。ADX / DI 两个指标共用。
    """
    tr = _true_range(h, l, c)
    plus_dm, minus_dm = _directional_movement(h, l)
    atr_s = _wilder(tr, period)
    p_dm_s = _wilder(plus_dm, period)
    n_dm_s = _wilder(minus_dm, period)
    plus_di = _safe_div(p_dm_s, atr_s, scale=100.0)
    minus_di = _safe_div(n_dm_s, atr_s, scale=100.0)
    di_sum = plus_di + minus_di
    dx = _safe_div(np.abs(plus_di - minus_di), di_sum, scale=100.0)
    first = period - 1
    return plus_di, minus_di, dx, first
