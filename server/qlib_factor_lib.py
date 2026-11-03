"""因子定义与标度：动量 / 特质波动 / 换手流动性"""

from __future__ import annotations
import logging
import math
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
import numpy as np
from server.cache import cache
from server.chan_engine import chan_engine
from server.tencent_kline import fetch_tencent_kline
from server.core.numbers import safe_float as _finite

logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# 常量
# ------------------------------------------------------------

# 动态选股覆盖范围：成交额 Top N + 涨跌异动 Top N（去重）
_UNIVERSE_LIQUID = 14
_UNIVERSE_MOVERS = 6

_CACHE_TTL_SCREEN = 60       # 动态选股结果缓存 60s（随行情实时刷新）
_KLINE_LIMIT = 260           # 日K历史深度（≈1 年交易日，满足 30 日窗口 + IC 序列）
_FORWARD_DAYS = 1            # 前瞻收益周期（下一交易日）

_MIN_CROSS_SECTION = 5       # 每期截面有效样本下限（低于则不计算该期 IC）
_MIN_IC_PERIODS = 10         # 有效 IC 期数下限（低于则 IC/IR 返回 null）
_MIN_QUANT_SAMPLES = 20      # 分位分组池化样本下限
_MIN_BACKTEST_PERIODS = 3    # 回测最少期数

# 因子值归一化展示尺度（仅用于 0~100 展示分，raw 值为真实值）
_FACTOR_SCALE = {
    "Alpha_Mom_20": 0.12,
    "Alpha_Vol_Std": 0.03,
    "Alpha_Liq_Turnover": 0.60,
}
# 因子方向（+1：值越大越好；-1：值越小越好），仅影响展示分与多头选股
_FACTOR_DIR = {
    "Alpha_Mom_20": 1.0,
    "Alpha_Vol_Std": -1.0,
    "Alpha_Liq_Turnover": 1.0,
}


# ============================================================
# 因子计算函数（输入为单只股票对齐后的数组，输出逐日因子值序列，NaN 表示该日不可用）
# 每个函数签名为 (closes, volumes, dates, market_ret_by_date) -> np.ndarray
# ============================================================

def _f_mom20(closes: np.ndarray, volumes: np.ndarray,
             dates: List[str], mret: Dict[str, float], window: int = 20) -> np.ndarray:
    """20日动量：Close[t]/Close[t-20]-1。"""
    n = len(closes)
    out = np.full(n, np.nan)
    for i in range(window, n):
        base = closes[i - window]
        if base > 0 and closes[i] > 0:
            out[i] = closes[i] / base - 1.0
    return out


def _f_idio_vol(closes: np.ndarray, volumes: np.ndarray,
                dates: List[str], mret: Dict[str, float], window: int = 30) -> np.ndarray:
    """特质波动率：近 30 日「市场调整后收益」(r_i - r_m) 的标准差。

    市场收益取自基准指数日K（按日期对齐）；样本不足 80% 则该日不可用。
    """
    n = len(closes)
    out = np.full(n, np.nan)
    rets = np.full(n, np.nan)
    for i in range(1, n):
        if closes[i - 1] > 0 and closes[i] > 0:
            rets[i] = closes[i] / closes[i - 1] - 1.0
    excess = np.full(n, np.nan)
    for i in range(1, n):
        rm = mret.get(dates[i])
        if np.isfinite(rets[i]) and rm is not None:
            excess[i] = rets[i] - rm
    need = int(window * 0.8)
    for i in range(window, n):
        w = excess[i - window + 1:i + 1]
        w = w[np.isfinite(w)]
        if len(w) >= need:
            out[i] = float(np.std(w, ddof=1))
    return out


def _f_liq_turnover(closes: np.ndarray, volumes: np.ndarray,
                    dates: List[str], mret: Dict[str, float],
                    short: int = 5, long: int = 30) -> np.ndarray:
    """异常换手（成交量收缩/放大）代理：MA(Volume,5)/MA(Volume,30)-1。

    注：无流通股本数据，无法计算真实换手率，此处以成交量均线比作为流动性代理，
    formula_desc 已如实标注为「成交量」，不做换手率冒充。
    """
    n = len(volumes)
    out = np.full(n, np.nan)
    for i in range(long - 1, n):
        s = float(np.mean(volumes[i - short + 1:i + 1]))
        l = float(np.mean(volumes[i - long + 1:i + 1]))
        if l > 0:
            out[i] = s / l - 1.0
    return out


# 因子计算函数注册表（供引擎内部调用）
_FACTOR_COMPUTE: Dict[str, Callable[..., np.ndarray]] = {
    "Alpha_Mom_20": _f_mom20,
    "Alpha_Vol_Std": _f_idio_vol,
    "Alpha_Liq_Turnover": _f_liq_turnover,
}


# ============================================================
# 因子库：单一事实来源（/qlib/factors、/qlib/meta、引擎计算全部读取此处）
# 声明 6 个因子，其中 3 个可用（有真实数据可算），3 个不可用（如实降级）。
# ============================================================

QLIB_FACTOR_LIBRARY: List[Dict[str, Any]] = [
    {
        "id": "Alpha_Mom_20", "name": "20日动量因子", "category": "Momentum",
        "formula": "Close[t]/Close[t-20]-1",
        "formula_desc": "20 日收盘价动量：Close[t]/Close[t-20]-1（日K前复权）",
        "available": True, "unavailable_reason": None,
        "direction": "higher_better", "default_weight": 0.4,
        "ic": None, "rank_ic": None, "ir": None,
        "desc": "捕捉中期价格趋势动量，在中强趋势行情中具备选股超额收益",
    },
    {
        "id": "Alpha_Vol_Std", "name": "特质波动率", "category": "Volatility",
        "formula": "Std(Return_i - Return_market, 30)",
        "formula_desc": "近 30 日「个股收益 - 基准指数收益」的标准差（市场调整后波动）",
        "available": True, "unavailable_reason": None,
        "direction": "lower_better", "default_weight": 0.3,
        "ic": None, "rank_ic": None, "ir": None,
        "desc": "低特质波动率股票具有更显著的风险调整后收益与防御韧性",
    },
    {
        "id": "Alpha_Liq_Turnover", "name": "成交量收缩因子", "category": "Liquidity",
        "formula": "MA(Volume,5)/MA(Volume,30)-1",
        "formula_desc": "5 日/30 日成交量均线比 -1（无流通股本，以成交量代理换手，非真实换手率）",
        "available": True, "unavailable_reason": None,
        "direction": "higher_better", "default_weight": 0.3,
        "ic": None, "rank_ic": None, "ir": None,
        "desc": "量能收缩/放大识别，寻找吸筹完毕进入主升浪的前瞻标的",
    },
    {
        "id": "Alpha_Value_EP", "name": "动态盈利收益率", "category": "Value",
        "formula": "1/PE（当前快照口径）；历史序列需 PE_TTM",
        "formula_desc": "1/PE 估值因子：当前快照 EP（1/实时PE）可用于决策闸；时间序列 IC 仍需 PE_TTM 历史",
        "available": False,
        "unavailable_reason": "时间序列因子不可用（无 PE_TTM 历史序列，无法构造截面 IC）；"
                              "已提供当前快照 EP（1/实时 PE，腾讯 field39，非 TTM）供决策闸消费，degraded=True",
        "degraded": True,
        "degraded_reason": "快照 EP 可用（degraded，非 PE_TTM）；因无历史序列，本引擎 IC/回测不计算",
        "direction": "higher_better", "default_weight": 0.0,
        "ic": None, "rank_ic": None, "ir": None,
        "desc": "低估值高盈利安全垫因子（快照 EP 供决策闸；历史序列缺失，如实降级）",
    },
    {
        "id": "Alpha_Quality_ROE", "name": "ROE 稳定性与成长", "category": "Quality",
        "formula": "ROE_TTM*(1+Delta_ROE_YoY)",
        "formula_desc": "ROE_TTM 及其同比变化",
        "available": False,
        "unavailable_reason": "缺少财务报表数据源（ROE_TTM 不可得），无法构造因子时间序列",
        "direction": "higher_better", "default_weight": 0.0,
        "ic": None, "rank_ic": None, "ir": None,
        "desc": "挑选具备护城河与持续高资本回报率的核心资产（当前数据源不可用，如实降级）",
    },
    {
        "id": "Alpha_OrderFlow_Imbalance", "name": "资金流向不平衡度", "category": "Microstructure",
        "formula": "(MainInflow-MainOutflow)/TotalAmount",
        "formula_desc": "主力资金净流入 / 成交额（逐笔/资金流数据）",
        "available": False,
        "unavailable_reason": "仅有当日资金流快照、无历史逐笔序列，无法构造截面时间序列 IC",
        "direction": "higher_better", "default_weight": 0.0,
        "ic": None, "rank_ic": None, "ir": None,
        "desc": "高频资金大单沉淀意愿（当前仅有快照数据，如实降级）",
    },
]

# 可用因子（引擎实际能算出非 null 值的因子）——与 meta.available_factor_ids 保持一致
_AVAILABLE_IDS: List[str] = [f["id"] for f in QLIB_FACTOR_LIBRARY if f.get("available")]
_FACTOR_BY_ID: Dict[str, Dict[str, Any]] = {f["id"]: f for f in QLIB_FACTOR_LIBRARY}

# 可选基准（真实指数日K）
QLIB_BENCHMARKS: List[Dict[str, str]] = [
    {"id": "sh000300", "name": "沪深300"},
    {"id": "sh000001", "name": "上证指数"},
    {"id": "sz399001", "name": "深证成指"},
    {"id": "sz399006", "name": "创业板指"},
]
