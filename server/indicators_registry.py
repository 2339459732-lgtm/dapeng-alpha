"""指标注册表与统一入口：ParamSpec / IndicatorSpec / compute / 目录"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import numpy as np

from server.indicators_base import PaneMap, PaneSeries, _to_float, extract_ohlcv
from server.indicators_trend import donchian, ichimoku, keltner, supertrend
from server.indicators_volume import (
    adx,
    aroon,
    atr,
    cci,
    choppiness,
    di,
    mfi,
    obv,
    stoch,
    vwap,
    williams_r,
)

@dataclass(frozen=True)
class ParamSpec:
    """单个指标参数的规格（前端参数弹层直接读这个渲染）。"""

    name: str
    type: str            # "int" | "float"
    default: float
    min: float
    max: float
    unit: str
    desc: str

    def as_dict(self) -> Dict[str, Any]:
        """序列化为前端可直接消费的字典。"""
        return {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "min": self.min,
            "max": self.max,
            "unit": self.unit,
            "desc": self.desc,
        }

    def coerce(self, raw: Any) -> float:
        """把外部传入值夹到合法区间并按类型取整。非法输入退回默认值。"""
        try:
            val = float(raw)
        except (TypeError, ValueError):
            val = float(self.default)
        if not math.isfinite(val):
            val = float(self.default)
        val = max(float(self.min), min(float(self.max), val))
        return round(val) if self.type == "int" else val


@dataclass(frozen=True)
class IndicatorSpec:
    """单个指标的完整规格。"""

    name: str
    label: str
    overlay: bool
    panes: Tuple[str, ...]
    primary: str
    params: Tuple[ParamSpec, ...]
    func: Callable[..., PaneMap]
    min_bars: Callable[[Dict[str, float]], int]
    value_range: Tuple[Optional[float], Optional[float]]
    desc: str
    #: 出现在标签里的参数名（空 = 全部）。派生参数（如 ichimoku 的 shift）不入标签。
    label_params: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        """序列化为前端可直接消费的字典（含 params schema）。"""
        return {
            "name": self.name,
            "label": self.label,
            "overlay": self.overlay,
            "panes": list(self.panes),
            "primary": self.primary,
            "value_range": list(self.value_range),
            "min_bars": int(self.min_bars(self.default_params())),
            "desc": self.desc,
            "params": {p.name: p.as_dict() for p in self.params},
        }

    def default_params(self) -> Dict[str, float]:
        """默认参数值字典。"""
        return {p.name: p.default for p in self.params}

    def normalize_params(self, raw: Optional[Mapping[str, Any]] = None,
                         positional: Optional[Sequence[float]] = None) -> Dict[str, float]:
        """把外部参数归一成合法值字典。

        Args:
            raw: 具名字典（优先）。
            positional: 位置参数列表（``params="10,3.0"`` 形态），按声明顺序填充，
                个数不足的部分保留默认值；个数超出则忽略多余项。

        Returns:
            完整参数字典（键 = 全部声明参数）。
        """
        out = self.default_params()
        if raw:
            for p in self.params:
                if p.name in raw:
                    out[p.name] = p.coerce(raw[p.name])
        elif positional:
            for idx, p in enumerate(self.params):
                if idx < len(positional):
                    out[p.name] = p.coerce(positional[idx])
        return out


def _min_period(factor: int = 1, extra: int = 0) -> Callable[[Dict[str, float]], int]:
    """生成 ``min_bars`` 计算器：``ceil(factor*period) + extra``。"""

    def _fn(params: Dict[str, float]) -> int:
        period = int(params.get("period", 14) or 14)
        return int(math.ceil(factor * period)) + extra

    return _fn


def _min_supertrend(params: Dict[str, float]) -> int:
    return int(params.get("period", 10)) + 1


def _min_ichimoku(params: Dict[str, float]) -> int:
    return int(max(params.get("tenkan", 9), params.get("kijun", 26),
                   params.get("senkou_b", 52)))


def _min_keltner(params: Dict[str, float]) -> int:
    return int(max(params.get("period", 20), params.get("atr_period", 20)))


def _min_vwap(params: Dict[str, float]) -> int:  # noqa: ARG001 - 接口统一
    return 1


def _min_obv(params: Dict[str, float]) -> int:  # noqa: ARG001 - 接口统一
    return 2


def _min_stoch(params: Dict[str, float]) -> int:
    return int(params.get("k_period", 9)) + int(params.get("d_period", 3)) - 1


def _min_aroon(params: Dict[str, float]) -> int:
    return int(params.get("period", 25)) + 1


def _min_adx(params: Dict[str, float]) -> int:
    return 2 * int(params.get("period", 14))


def _p(name: str, type_: str, default: float, lo: float, hi: float,
       unit: str, desc: str) -> ParamSpec:
    """``ParamSpec`` 简写构造器（仅用于下方注册表，减少噪音）。"""
    return ParamSpec(name=name, type=type_, default=default, min=lo, max=hi,
                     unit=unit, desc=desc)


_PERIOD14 = (_p("period", "int", 14, 2, 200, "根", "计算窗口根数"),)


INDICATOR_REGISTRY: Dict[str, IndicatorSpec] = {
    # ---------- 主图叠加（overlay=True） ----------
    "supertrend": IndicatorSpec(
        name="supertrend",
        label="SuperTrend(10,3.0)",
        overlay=True,
        panes=("st_line", "st_dir"),
        primary="st_line",
        params=(
            _p("period", "int", 10, 2, 100, "根", "ATR 周期"),
            _p("mult", "float", 3.0, 0.5, 10.0, "倍", "ATR 倍数"),
        ),
        func=supertrend,
        min_bars=_min_supertrend,
        value_range=(None, None),
        desc="ATR 通道趋势线：价格在上方为多头，跌破翻转。叠主图，别入副图（价格量纲）。",
    ),
    "ichimoku": IndicatorSpec(
        name="ichimoku",
        label="一目均衡表(9,26,52)",
        overlay=True,
        panes=("tenkan", "kijun", "senkou_a", "senkou_b", "chikou"),
        primary="tenkan",
        params=(
            _p("tenkan", "int", 9, 2, 100, "根", "转换线周期"),
            _p("kijun", "int", 26, 2, 200, "根", "基准线周期"),
            _p("senkou_b", "int", 52, 2, 400, "根", "先行带 B 周期"),
            _p("shift", "int", 26, 0, 200, "根", "先行带前移/迟行线后移根数"),
        ),
        func=ichimoku,
        min_bars=_min_ichimoku,
        value_range=(None, None),
        desc="一目均衡表：云带（先行 A/B）+ 转换/基准线 + 迟行线，叠主图。",
        label_params=("tenkan", "kijun", "senkou_b"),
    ),
    "donchian": IndicatorSpec(
        name="donchian",
        label="唐奇安通道(20)",
        overlay=True,
        panes=("upper", "middle", "lower"),
        primary="middle",
        params=(_p("period", "int", 20, 2, 250, "根", "通道周期"),),
        func=donchian,
        min_bars=_min_period(1, 0),
        value_range=(None, None),
        desc="N 日最高/最低通道，突破上轨为多头信号（海龟法则）。",
    ),
    "keltner": IndicatorSpec(
        name="keltner",
        label="肯特纳通道(20,2.0)",
        overlay=True,
        panes=("upper", "middle", "lower"),
        primary="middle",
        params=(
            _p("period", "int", 20, 2, 250, "根", "中轨 EMA 周期"),
            _p("mult", "float", 2.0, 0.5, 6.0, "倍", "ATR 倍数"),
            _p("atr_period", "int", 20, 2, 250, "根", "ATR 周期"),
        ),
        func=keltner,
        min_bars=_min_keltner,
        value_range=(None, None),
        desc="EMA 中轨 ± ATR 通道，对跳空比布林带稳健。",
        label_params=("period", "mult"),
    ),
    "vwap": IndicatorSpec(
        name="vwap",
        label="VWAP",
        overlay=True,
        panes=("vwap",),
        primary="vwap",
        params=(),
        func=vwap,
        min_bars=_min_vwap,
        value_range=(None, None),
        desc="成交量加权均价：盘中按当日累计量额重置，日线为整段锚定累计。",
    ),
    # ---------- 独立副图（overlay=False） ----------
    "adx": IndicatorSpec(
        name="adx",
        label="ADX(14)",
        overlay=False,
        panes=("adx", "plus_di", "minus_di"),
        primary="adx",
        params=_PERIOD14,
        func=adx,
        min_bars=_min_adx,
        value_range=(0.0, 100.0),
        desc="趋势强度 + 方向：ADX>25 趋势成型，方向看 +DI 与 -DI 相对位置。",
    ),
    "aroon": IndicatorSpec(
        name="aroon",
        label="Aroon(25)",
        overlay=False,
        panes=("aroon_up", "aroon_down", "aroon_osc"),
        primary="aroon_up",
        params=(_p("period", "int", 25, 2, 250, "根", "回溯窗口根数"),),
        func=aroon,
        min_bars=_min_aroon,
        value_range=(0.0, 100.0),
        desc="新高/新低距离：Up 高 Down 低为强多头，Oscillator = Up - Down。",
    ),
    "obv": IndicatorSpec(
        name="obv",
        label="OBV",
        overlay=False,
        panes=("obv",),
        primary="obv",
        params=(),
        func=obv,
        min_bars=_min_obv,
        value_range=(None, None),
        desc="能量潮：涨日加量、跌日减量的累计值，看量与价是否背离。量纲大，必须独立副图。",
    ),
    "mfi": IndicatorSpec(
        name="mfi",
        label="MFI(14)",
        overlay=False,
        panes=("mfi",),
        primary="mfi",
        params=_PERIOD14,
        func=mfi,
        min_bars=_min_period(1, 1),
        value_range=(0.0, 100.0),
        desc="带量的 RSI：20 以下超卖、80 以上超买。",
    ),
    "atr": IndicatorSpec(
        name="atr",
        label="ATR(14)",
        overlay=False,
        panes=("atr",),
        primary="atr",
        params=_PERIOD14,
        func=atr,
        min_bars=_min_period(1, 0),
        value_range=(0.0, None),
        desc="真实波幅（Wilder 平滑）：止损位与仓位测算基准。",
    ),
    "stoch": IndicatorSpec(
        name="stoch",
        label="KDJ(9,3,3)",
        overlay=False,
        panes=("k", "d", "j"),
        primary="k",
        params=(
            _p("k_period", "int", 9, 2, 100, "根", "K 值窗口"),
            _p("d_period", "int", 3, 1, 50, "根", "D 值平滑窗口"),
        ),
        func=stoch,
        min_bars=_min_stoch,
        value_range=(0.0, 100.0),
        desc="随机指标：K/D 判超买超卖，J 极值提示反转。",
    ),
    "cci": IndicatorSpec(
        name="cci",
        label="CCI(20)",
        overlay=False,
        panes=("cci",),
        primary="cci",
        params=(_p("period", "int", 20, 2, 250, "根", "计算窗口根数"),),
        func=cci,
        min_bars=_min_period(2, -1),   # 平均绝对偏差需在 MA 之后再平滑一个窗口
        value_range=(None, None),
        desc="顺势指标：±100 为强弱分界，±200 为极端区。",
    ),
    "choppiness": IndicatorSpec(
        name="choppiness",
        label="CHOP(14)",
        overlay=False,
        panes=("chop",),
        primary="chop",
        params=_PERIOD14,
        func=choppiness,
        min_bars=_min_period(1, 0),
        value_range=(0.0, 100.0),
        desc="震荡指数：>61.8 震荡市（不宜追趋势），<38.2 强趋势。",
    ),
    "williams_r": IndicatorSpec(
        name="williams_r",
        label="Williams %R(14)",
        overlay=False,
        panes=("wr",),
        primary="wr",
        params=_PERIOD14,
        func=williams_r,
        min_bars=_min_period(1, 0),
        value_range=(-100.0, 0.0),
        desc="威廉指标：-20 以上超买、-80 以下超卖（与 KDJ 互补）。",
    ),
    "di": IndicatorSpec(
        name="di",
        label="DI+/DI-(14)",
        overlay=False,
        panes=("plus_di", "minus_di"),
        primary="plus_di",
        params=_PERIOD14,
        func=di,
        min_bars=_min_period(1, 0),
        value_range=(0.0, 100.0),
        desc="单 pane 双线方向指标：+DI 上穿 -DI 为多头。",
    ),
}


#: 任务口径的 12 个指标（注册顺序即前端下拉顺序）
INDICATOR_NAMES: Tuple[str, ...] = (
    "supertrend",
    "ichimoku",
    "donchian",
    "keltner",
    "vwap",
    "adx",
    "aroon",
    "obv",
    "mfi",
    "atr",
    "stoch",
    "cci",
)

#: 别名 -> 注册名（兼容设计文档 §3.4 的 indicator 取值与前端历史命名）
ALIAS_MAP: Dict[str, str] = {
    "kc": "keltner",
    "chop": "choppiness",
    "wr": "williams_r",
    "williams": "williams_r",
    "williamsr": "williams_r",
    "kdj": "stoch",
    "st": "supertrend",
    "supertrend_line": "supertrend",
}


def resolve_name(name: str) -> Optional[str]:
    """把外部传入的指标名归一为注册名；未知返回 None。"""
    if not name:
        return None
    key = str(name).strip().lower()
    if key in INDICATOR_REGISTRY:
        return key
    return ALIAS_MAP.get(key)


def list_registry(names: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """返回指标规格列表（供前端渲染下拉与参数弹层）。

    Args:
        names: 限定指标名（可含别名 / ``all``）；None 表示全量注册项。

    Returns:
        ``IndicatorSpec.as_dict()`` 列表。
    """
    if not names:
        keys: List[str] = list(INDICATOR_NAMES)
        keys += [k for k in INDICATOR_REGISTRY if k not in INDICATOR_NAMES]
    else:
        keys = []
        for raw in names:
            name = str(raw).strip().lower()
            if name in ("", "all", "*"):
                keys.extend([k for k in INDICATOR_REGISTRY if k not in keys])
                continue
            resolved = resolve_name(name)
            if resolved and resolved not in keys:
                keys.append(resolved)
    return [INDICATOR_REGISTRY[k].as_dict() for k in keys if k in INDICATOR_REGISTRY]


def parse_positional(params: Any) -> Optional[List[float]]:
    """把 ``"14"`` / ``"10,3.0"`` / ``[10, 3.0]`` 解析成位置参数列表。"""
    if params is None or params == "":
        return None
    if isinstance(params, (list, tuple)):
        return [_to_float(x) for x in params if math.isfinite(_to_float(x))]
    try:
        parts = [p for p in str(params).replace(";", ",").split(",") if p.strip() != ""]
        vals = [_to_float(p) for p in parts]
        vals = [v for v in vals if math.isfinite(v)]
        return vals or None
    except (TypeError, ValueError):
        return None


def compute(
    name: str,
    klines: Any,
    params: Optional[Mapping[str, Any]] = None,
    positional: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """计算单个指标，返回可直接进响应体的结构。

    Args:
        name: 指标名（可含别名，如 ``kc`` / ``chop`` / ``wr``）。
        klines: K 线序列（见 :func:`extract_ohlcv`）。
        params: 具名参数（优先）。
        positional: 位置参数（``params="10,3.0"`` 形态）。

    Returns:
        ``{name,label,overlay,panes,unresolved,params,params_schema,values,series,
        value_range,min_bars,bars,reason}``。

        * ``values``：**主 pane** 的序列（与输入等长），前端画单线用；
        * ``series``：全部 pane -> 序列，多线指标（adx/aroon/stoch/...）用它；
        * 数据不足时 ``values == []`` 且 ``reason`` 非空（**绝不返回等长 0 数组**）；
        * 未知指标名 ``unresolved=True``，其余字段为空。
    """
    resolved = resolve_name(name)
    if resolved is None:
        return {
            "name": str(name),
            "label": "",
            "overlay": False,
            "panes": [],
            "unresolved": True,
            "params": {},
            "params_schema": {},
            "values": [],
            "series": {},
            "value_range": [None, None],
            "min_bars": 0,
            "bars": 0,
            "reason": f"未知指标名 {name!r}",
        }

    spec = INDICATOR_REGISTRY[resolved]
    eff = spec.normalize_params(params, positional)
    bars = 0
    kdata = extract_ohlcv(klines)
    if kdata["c"].size:
        bars = int(kdata["c"].size)
    need = int(spec.min_bars(eff))

    base: Dict[str, Any] = {
        "name": resolved,
        "label": _label_with_params(spec, eff),
        "overlay": spec.overlay,
        "panes": list(spec.panes),
        "unresolved": False,
        "params": eff,
        "params_schema": {p.name: p.as_dict() for p in spec.params},
        "values": [],
        "series": {},
        "value_range": list(spec.value_range),
        "min_bars": need,
        "bars": bars,
        "reason": None,
    }

    if bars < need:
        base["reason"] = (
            f"{resolved} 需要至少 {need} 根 K 线（当前 {bars} 根），"
            f"前置样本不足，返回空序列而非 0 占位"
        )
        return base

    try:
        panes = spec.func(klines, **eff)
    except (TypeError, ValueError, FloatingPointError, ZeroDivisionError) as exc:
        base["reason"] = f"{resolved} 计算失败：{exc}"
        return base

    series: Dict[str, PaneSeries] = {}
    for pane in spec.panes:
        seq = panes.get(pane)
        series[pane] = list(seq) if seq is not None else []
    # 防御：计算函数返回长度异常时按缺数对齐，绝不补 0
    for pane, seq in series.items():
        if seq and len(seq) != bars:
            series[pane] = (seq + [None] * bars)[:bars] if len(seq) < bars else seq[:bars]

    base["series"] = series
    primary = series.get(spec.primary) or []
    base["values"] = primary
    if not primary or all(v is None for v in primary):
        # 与「数据不足」同等对待：values 给空数组，绝不把全 None/全 0 当成功
        base["values"] = []
        base["series"] = {pane: [] for pane in spec.panes}
        base["reason"] = (
            f"{resolved} 在 {bars} 根窗口内无有效值（可能全程横盘导致分母为 0），"
            f"返回空序列而非 0 占位"
        )
    return base


def _label_with_params(spec: IndicatorSpec, eff: Mapping[str, float]) -> str:
    """生成带实际参数的标签，如 ``ADX(14)`` / ``SuperTrend(10,3.0)``。

    只展示 ``spec.label_params`` 声明的参数（空 = 全部）；派生参数
    （如 ichimoku 的 ``shift`` 默认等于 ``kijun``）不进标签，避免 ``(9,26,52,26)`` 这种噪音。
    """
    if not spec.params:
        return spec.label
    wanted = spec.label_params or tuple(p.name for p in spec.params)
    parts: List[str] = []
    for p in spec.params:
        if p.name not in wanted:
            continue
        val = eff.get(p.name, p.default)
        if p.type == "int":
            parts.append(str(int(val)))
        else:
            text = f"{float(val):g}"
            parts.append(text if "." in text else f"{text}.0")
    base = spec.label.split("(")[0]
    return f"{base}({','.join(parts)})" if parts else base


#: 兼容旧调用名（外部脚本可能用 compute_indicator / _parse_positional）
compute_indicator = compute
_parse_positional = parse_positional
