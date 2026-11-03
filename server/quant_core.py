# -*- coding: utf-8 -*-
"""
quant_core.py
A股量化回测核心策略模块。
将原始脚本 main() 重构为 run_backtest(config, log_callback)，
所有 print 替换为 log_callback 调用，返回结果 dict。
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import numpy as np

# 量化回测专用依赖 — 延迟加载，仅在被调用时才导入
# 若缺少依赖，调用 run_backtest 时会给出明确提示
_pandas = None
_yfinance = None
_tushare = None
_lightgbm = None
_ta_sma = None
_ta_ema = None
_ta_macd = None
_ta_rsi = None
_ta_bb = None
_ta_atr = None

def _ensure_quant_deps():
    """延迟加载量化回测所需依赖，缺失时抛出清晰错误。"""
    global _pandas, _yfinance, _tushare, _lightgbm
    global _ta_sma, _ta_ema, _ta_macd, _ta_rsi, _ta_bb, _ta_atr
    import sys

    if _pandas is None:
        try:
            import pandas as pd
            _pandas = pd
            sys.modules[__name__].__dict__["pd"] = pd
        except ImportError:
            raise ImportError(
                "量化回测需要 pandas，请执行: pip install pandas>=2.0.0"
            )

    if _yfinance is None:
        try:
            import yfinance as yf
            _yfinance = yf
            sys.modules[__name__].__dict__["yf"] = yf
        except ImportError:
            raise ImportError(
                "量化回测需要 yfinance，请执行: pip install yfinance>=0.2.0"
            )

    if _tushare is None:
        try:
            import tushare as ts
            _tushare = ts
            sys.modules[__name__].__dict__["ts"] = ts
        except ImportError:
            raise ImportError(
                "量化回测需要 tushare，请执行: pip install tushare>=1.4.0"
            )

    if _lightgbm is None:
        try:
            import lightgbm as lgb
            _lightgbm = lgb
            sys.modules[__name__].__dict__["lgb"] = lgb
        except ImportError:
            raise ImportError(
                "量化回测需要 lightgbm，请执行: pip install lightgbm>=4.0.0"
            )

    if _ta_sma is None:
        try:
            from ta.trend import SMAIndicator, EMAIndicator, MACD
            from ta.momentum import RSIIndicator
            from ta.volatility import BollingerBands, AverageTrueRange
            _ta_sma = SMAIndicator
            _ta_ema = EMAIndicator
            _ta_macd = MACD
            _ta_rsi = RSIIndicator
            _ta_bb = BollingerBands
            _ta_atr = AverageTrueRange
        except ImportError:
            raise ImportError(
                "量化回测需要 ta，请执行: pip install ta>=0.11.0"
            )


# ===================== 默认配置常量 =====================
DEFAULT_CONFIG = {
    "TUSHARE_TOKEN": "YOUR_TUSHARE_TOKEN",
    "PRICE_LOW": 5.0,
    "PRICE_HIGH": 36.0,
    "INDEX_CODES": ["000300.SH", "000905.SH"],
    "MAX_STOCKS": 80,
    "START": "2018-01-01",
    "END": None,
    "HORIZON": 10,
    "BUY_TH": 0.02,
    "SELL_TH": -0.02,
    "MIN_PROBA": 0.55,
    "THETA_THRESHOLD": 0.10,
    "BETA_SIZE": 0.30,
    "TRAIN_MIN_YEARS": 3,
    "STEP_DAYS": 20,
    "TOPK": 5,
    "TCOST": 0.001,
    "SEED": 42,
}


def get_universe_from_indexes(
    pro,
    index_codes: list,
    price_low: float,
    price_high: float,
    start_date: str,
    end_date: str,
    max_stocks: int = 80,
    log_callback=None,
) -> list:
    """从指数成分股中筛选满足价格条件的股票池。

    Args:
        pro: Tushare pro_api 实例。
        index_codes: 指数代码列表。
        price_low: 最低价格过滤。
        price_high: 最高价格过滤。
        start_date: 开始日期（YYYYMMDD 或 YYYY-MM-DD）。
        end_date: 结束日期（YYYYMMDD 或 YYYY-MM-DD），可为 None。
        max_stocks: 股票池最大数量。
        log_callback: 日志回调函数，签名 (msg: str) -> None。

    Returns:
        满足条件的股票代码列表。
    """
    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    # 统一日期格式为 YYYYMMDD
    sd = start_date.replace("-", "")
    ed = end_date.replace("-", "") if end_date else pd.Timestamp.today().strftime("%Y%m%d")

    stocks: set = set()
    for idx in index_codes:
        try:
            w = pro.index_weight(index_code=idx, start_date=sd, end_date=ed)
            if w is not None and len(w) > 0:
                code_col = None
                for c in ["con_code", "ts_code", "index_code"]:
                    if c in w.columns:
                        code_col = c
                        break
                if code_col is None:
                    code_col = w.columns[0]
                stocks.update(w[code_col].astype(str).tolist())
        except Exception as e:
            log(f"[WARN] index_weight failed for {idx}: {e}")

    stocks_list = sorted(list(stocks))
    if not stocks_list:
        raise RuntimeError("股票池为空：请检查 Tushare 权限/接口或 INDEX_CODES。")

    keep = []
    for code in stocks_list:
        try:
            df = pro.daily(ts_code=code, start_date=sd, end_date=ed)
            if df is None or df.empty:
                continue
            df = df.sort_values("trade_date")
            last_close = float(df.iloc[-1]["close"])
            if price_low <= last_close <= price_high:
                keep.append(code)
        except Exception:
            continue

    keep = sorted(list(set(keep)))
    if len(keep) > max_stocks:
        keep = keep[:max_stocks]
    return keep


def fetch_stock_ohlcv(pro, ts_code: str, start: str, end: str) -> "pd.DataFrame":
    """从 Tushare 拉取单只股票的日线 OHLCV 数据。

    Args:
        pro: Tushare pro_api 实例。
        ts_code: 股票代码（如 000001.SZ）。
        start: 开始日期，格式 YYYY-MM-DD。
        end: 结束日期，格式 YYYY-MM-DD 或 None。

    Returns:
        包含 [trade_date, open, high, low, close, volume] 列的 DataFrame。
        若无数据则返回 None。
    """
    sd = start.replace("-", "")
    ed = end.replace("-", "") if end else None

    df = pro.daily(ts_code=ts_code, start_date=sd, end_date=ed)
    if df is None or df.empty:
        return None

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
    df = df.sort_values("trade_date")
    df = df.rename(columns={"vol": "volume"})

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df[["trade_date", "open", "high", "low", "close", "volume"]].dropna()


def load_global_features(start: str, end: str) -> "pd.DataFrame":
    """通过 yfinance 下载全球市场特征数据并构造风险因子。

    Args:
        start: 开始日期，格式 YYYY-MM-DD。
        end: 结束日期，格式 YYYY-MM-DD 或 None。

    Returns:
        以日期为索引的全球特征 DataFrame。
    """
    end_ = end if end else pd.Timestamp.today().strftime("%Y-%m-%d")

    tickers = {
        "SPY": "SPY",
        "QQQ": "QQQ",
        "VIXY": "VIXY",
        "USDCNY": "USDCNY=X",
        "GLD": "GLD",
        "USO": "USO",
    }

    raw = yf.download(
        list(tickers.values()),
        start=start,
        end=end_,
        auto_adjust=True,
        progress=False,
    )
    if raw is None or raw.empty:
        raise RuntimeError("全球数据下载失败：请检查网络或yfinance可用性。")

    if isinstance(raw.columns, pd.MultiIndex):
        close_df = raw["Close"].copy()
    else:
        close_df = raw[["Close"]].copy()
        close_df.columns = list(tickers.values())

    close_df = close_df.dropna(how="all")
    close_df.index = pd.to_datetime(close_df.index)
    close_df = close_df.rename(columns={v: k for k, v in tickers.items()})

    g = pd.DataFrame(index=close_df.index)
    g["spy_ret_5"] = close_df["SPY"].pct_change(5)
    g["spy_ret_20"] = close_df["SPY"].pct_change(20)
    g["qqq_ret_20"] = close_df["QQQ"].pct_change(20)
    g["vixy_level"] = close_df["VIXY"]
    g["vixy_ret_5"] = close_df["VIXY"].pct_change(5)
    g["usdcny_ret_10"] = close_df["USDCNY"].pct_change(10)
    g["gld_ret_20"] = close_df["GLD"].pct_change(20)
    g["uso_ret_20"] = close_df["USO"].pct_change(20)

    roll = 180
    z_vixy = (g["vixy_level"] - g["vixy_level"].rolling(roll).mean()) / (
        g["vixy_level"].rolling(roll).std() + 1e-9
    )
    z_spy = (g["spy_ret_20"] - g["spy_ret_20"].rolling(roll).mean()) / (
        g["spy_ret_20"].rolling(roll).std() + 1e-9
    )
    z_qqq = (g["qqq_ret_20"] - g["qqq_ret_20"].rolling(roll).mean()) / (
        g["qqq_ret_20"].rolling(roll).std() + 1e-9
    )
    z_usdcny = (g["usdcny_ret_10"] - g["usdcny_ret_10"].rolling(roll).mean()) / (
        g["usdcny_ret_10"].rolling(roll).std() + 1e-9
    )
    z_gld = (g["gld_ret_20"] - g["gld_ret_20"].rolling(roll).mean()) / (
        g["gld_ret_20"].rolling(roll).std() + 1e-9
    )

    g["risk_score"] = (
        0.45 * z_vixy
        - 0.25 * z_spy
        - 0.15 * z_qqq
        + 0.20 * z_usdcny
        + 0.15 * z_gld
    )
    g["risk_off_strength"] = (
        g["risk_score"] - g["risk_score"].rolling(roll).mean()
    ) / (g["risk_score"].rolling(roll).std() + 1e-9)
    g["risk_off_strength"] = g["risk_off_strength"].clip(-2, 2)
    return g


def make_stock_features(
    df: pd.DataFrame,
    horizon: int,
    buy_th: float,
    sell_th: float,
):
    """为单只股票构造技术指标特征。

    Args:
        df: 包含 OHLCV 列的 DataFrame（含 trade_date 列）。
        horizon: 预测周期（天）。
        buy_th: 买入信号阈值（正收益）。
        sell_th: 卖出信号阈值（负收益）。

    Returns:
        (feat_df, date_index, feature_cols) 三元组。
    """
    # 防御：本函数是公开入口，可能被直接调用（不经过 run_backtest）。
    # _ensure_quant_deps() 幂等（内部判 _ta_sma is None），重复调用无副作用。
    _ensure_quant_deps()

    d = df.copy()
    d = d.set_index("trade_date")

    d["ret_1"] = d["close"].pct_change(1)
    d["ret_1_next"] = d["close"].shift(-1) / d["close"] - 1.0
    d["logret_1"] = np.log(d["close"]).diff(1)

    # 指标计算统一走 _ensure_quant_deps() 里延迟导入并挂到模块级的 _ta_* 句柄。
    # 原先这里裸用 SMAIndicator/EMAIndicator/MACD/RSIIndicator/BollingerBands/AverageTrueRange，
    # 但这些名字只存在于 _ensure_quant_deps() 的局部作用域，模块级从未绑定 → 一执行即 NameError。
    d["sma_10"] = _ta_sma(d["close"], window=10).sma_indicator()
    d["sma_30"] = _ta_sma(d["close"], window=30).sma_indicator()
    d["ema_10"] = _ta_ema(d["close"], window=10).ema_indicator()
    d["ema_30"] = _ta_ema(d["close"], window=30).ema_indicator()

    d["close_sma10_ratio"] = d["close"] / d["sma_10"] - 1.0
    d["close_sma30_ratio"] = d["close"] / d["sma_30"] - 1.0
    d["ema_diff_ratio"] = d["ema_10"] / d["ema_30"] - 1.0

    d["rsi_14"] = _ta_rsi(d["close"], window=14).rsi()

    # ta 库的参数名是 window_sign（不是 window_signals）—— 这是修复 NameError 后暴露的第二处错参
    macd_obj = _ta_macd(d["close"], window_slow=26, window_fast=12, window_sign=9)
    d["macd"] = macd_obj.macd()
    d["macd_signal"] = macd_obj.macd_signal()
    d["macd_hist"] = macd_obj.macd_diff()

    bb = _ta_bb(d["close"], window=20, window_dev=2)
    d["bb_width"] = (bb.bollinger_hband() - bb.bollinger_lband()) / (
        bb.bollinger_mavg() + 1e-9
    )
    d["close_bb_ratio"] = d["close"] / (bb.bollinger_mavg() + 1e-9) - 1.0

    d["atr_14"] = _ta_atr(
        high=d["high"], low=d["low"], close=d["close"], window=14
    ).average_true_range()

    d["vol_20"] = d["ret_1"].rolling(20).std()
    d["vol_60"] = d["ret_1"].rolling(60).std()
    d["vol_chg_1"] = d["volume"].pct_change(1)

    d["fwd_ret_H"] = d["close"].shift(-horizon) / d["close"] - 1.0
    d["y_cls"] = 1
    d.loc[d["fwd_ret_H"] > buy_th, "y_cls"] = 2
    d.loc[d["fwd_ret_H"] < sell_th, "y_cls"] = 0

    d["close_today"] = d["close"]

    feature_cols = [
        "ret_1",
        "logret_1",
        "close_sma10_ratio",
        "close_sma30_ratio",
        "ema_diff_ratio",
        "rsi_14",
        "macd",
        "macd_signal",
        "macd_hist",
        "bb_width",
        "close_bb_ratio",
        "atr_14",
        "vol_20",
        "vol_60",
        "vol_chg_1",
        "close_today",
        "ret_1_next",
        "fwd_ret_H",
    ]
    return d.reset_index(), d.index, feature_cols


def build_panel(
    universe: list,
    pro,
    start: str,
    end: str,
    global_df: pd.DataFrame,
    horizon: int,
    buy_th: float,
    sell_th: float,
    log_callback=None,
) -> pd.DataFrame:
    """为股票池中每只股票构造特征面板，并合并全球因子。

    Args:
        universe: 股票代码列表。
        pro: Tushare pro_api 实例。
        start: 开始日期，格式 YYYY-MM-DD。
        end: 结束日期，格式 YYYY-MM-DD 或 None。
        global_df: load_global_features 返回的全球特征 DataFrame。
        horizon: 预测周期（天）。
        buy_th: 买入阈值。
        sell_th: 卖出阈值。
        log_callback: 日志回调函数。

    Returns:
        合并后的多股票特征面板 DataFrame。
    """
    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    panels = []
    for i, code in enumerate(universe, 1):
        msg = f"[{i}/{len(universe)}] fetching {code} ..."
        log(msg)

        df = fetch_stock_ohlcv(pro, code, start, end)
        if df is None or df.empty:
            continue

        feat_df, _, _ = make_stock_features(df, horizon, buy_th, sell_th)
        feat_df["trade_date"] = pd.to_datetime(feat_df["trade_date"])

        g = global_df.reindex(feat_df["trade_date"]).ffill().reset_index(drop=True)
        feat_df = pd.concat(
            [feat_df.reset_index(drop=True), g.reset_index(drop=True)], axis=1
        )
        feat_df["ts_code"] = code
        panels.append(feat_df)

    if not panels:
        raise RuntimeError("没有成功构建任何股票面板。")

    panel = pd.concat(panels, ignore_index=True)
    panel = panel.dropna(
        subset=["y_cls", "ret_1_next", "close_today", "atr_14", "rsi_14"]
    )
    return panel


def walk_forward_backtest(
    panel: pd.DataFrame,
    feature_train_cols: list,
    train_min_years: int,
    step_days: int,
    cfg: dict,
    log_callback=None,
) -> dict:
    """Walk-forward 回测主循环。

    Args:
        panel: 多股票特征面板。
        feature_train_cols: 用于训练/预测的特征列名列表。
        train_min_years: 最少训练年数。
        step_days: 每隔多少天重训练一次模型。
        cfg: 配置字典，包含 HORIZON、MIN_PROBA 等参数。
        log_callback: 日志回调函数。

    Returns:
        包含净值曲线、夏普、最大回撤等指标的结果 dict。
    """
    horizon = cfg.get("HORIZON", 10)
    min_proba = cfg.get("MIN_PROBA", 0.55)
    theta_threshold = cfg.get("THETA_THRESHOLD", 0.10)
    topk = cfg.get("TOPK", 5)
    tcost = cfg.get("TCOST", 0.001)
    price_low = cfg.get("PRICE_LOW", 5.0)
    price_high = cfg.get("PRICE_HIGH", 36.0)
    seed = cfg.get("SEED", 42)

    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    panel = panel.copy()
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel = panel.sort_values(["trade_date", "ts_code"])

    all_dates = np.array(sorted(panel["trade_date"].unique()))
    trading_days_per_year = 252
    start_idx = int(train_min_years * trading_days_per_year)

    if start_idx >= len(all_dates) - horizon - 2:
        raise ValueError("数据太短，请增大历史数据范围或减小TRAIN_MIN_YEARS。")

    model = None
    equity = [1.0]
    prev_weights: dict = {}
    daily_port_rets = []
    dates_used = []

    total_steps = len(all_dates) - 1 - start_idx

    for i in range(start_idx, len(all_dates) - 1):
        cur_date = all_dates[i]
        dates_used.append(str(cur_date)[:10])

        # 定期重训练
        if (i - start_idx) % step_days == 0 or model is None:
            train_df = panel[panel["trade_date"] < cur_date]
            x_train = train_df[feature_train_cols].values
            y_train = train_df["y_cls"].astype(int).values

            model = lgb.LGBMClassifier(
                objective="multiclass",
                num_class=3,
                n_estimators=800,
                learning_rate=0.03,
                subsample=0.9,
                colsample_bytree=0.9,
                max_depth=-1,
                reg_lambda=1.5,
                random_state=seed,
                verbose=-1,
            )
            model.fit(x_train, y_train)
            log(
                f"[MODEL] 更新模型 @ {str(cur_date)[:10]}, 训练样本: {len(x_train)}"
            )

        day_df = panel[panel["trade_date"] == cur_date].copy()
        if day_df.empty:
            daily_port_rets.append(0.0)
            equity.append(equity[-1])
            continue

        day_df = day_df[
            (day_df["close_today"] >= price_low)
            & (day_df["close_today"] <= price_high)
        ]
        if day_df.empty:
            daily_port_rets.append(0.0)
            equity.append(equity[-1])
            continue

        day_df["risk_off"] = day_df["risk_off_strength"].clip(lower=-2, upper=2)
        risk_off_factor = (day_df["risk_off"].mean() + 2) / 4
        min_proba_adj = min_proba + theta_threshold * risk_off_factor

        x_day = day_df[feature_train_cols].values
        proba = model.predict_proba(x_day)
        day_df = day_df.copy()
        day_df["proba_buy"] = proba[:, 2]

        candidates = day_df[day_df["proba_buy"] >= min_proba_adj].copy()
        if candidates.empty:
            new_weights: dict = {}
        else:
            candidates = candidates.sort_values("proba_buy", ascending=False).head(topk)
            w = 1.0 / len(candidates)
            new_weights = {row.ts_code: w for row in candidates.itertuples()}

        ret_map = dict(zip(day_df["ts_code"], day_df["ret_1_next"]))
        portfolio_ret = sum(
            w_val * float(ret_map.get(code, 0.0))
            for code, w_val in new_weights.items()
        )

        keys = set(prev_weights.keys()) | set(new_weights.keys())
        turnover = sum(
            abs(new_weights.get(k, 0.0) - prev_weights.get(k, 0.0)) for k in keys
        )
        portfolio_ret -= tcost * turnover

        daily_port_rets.append(portfolio_ret)
        equity.append(equity[-1] * (1.0 + portfolio_ret))
        prev_weights = new_weights

        progress = int((i - start_idx + 1) / total_steps * 100)
        if (i - start_idx) % 20 == 0:
            log(
                f"[PROGRESS:{progress}] {str(cur_date)[:10]} 净值={equity[-1]:.4f}"
            )

    equity_arr = np.array(equity)
    rets_arr = np.array(daily_port_rets)

    sharpe = (
        (np.mean(rets_arr) / (np.std(rets_arr) + 1e-12)) * np.sqrt(252)
        if np.std(rets_arr) > 1e-12
        else 0.0
    )
    peak = np.maximum.accumulate(equity_arr)
    dd = equity_arr / peak - 1.0
    max_dd = float(dd.min())

    log("\n=== 回测完成 ===")
    log(f"最终净值: {equity_arr[-1]:.4f}")
    log(f"年化夏普: {sharpe:.3f}")
    log(f"最大回撤: {max_dd * 100:.2f}%")

    return {
        "equity_curve": equity_arr.tolist(),
        "dates": dates_used,
        "daily_returns": rets_arr.tolist(),
        "sharpe": round(float(sharpe), 4),
        "max_dd": round(max_dd, 4),
        "final_nav": round(float(equity_arr[-1]), 4),
    }


def run_backtest(config: dict, log_callback=None) -> dict:
    """主入口，供 server.py 调用。

    Args:
        config: 配置参数字典，支持如下键：
            TUSHARE_TOKEN, INDEX_CODES, PRICE_LOW, PRICE_HIGH, MAX_STOCKS,
            START, END, HORIZON, BUY_TH, SELL_TH, MIN_PROBA, THETA_THRESHOLD,
            TRAIN_MIN_YEARS, STEP_DAYS, TOPK, TCOST, SEED。
        log_callback: 日志回调函数 (msg: str) -> None。

    Returns:
        回测结果 dict，包含 equity_curve、dates、sharpe、max_dd、final_nav 等。
    """
    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    # 延迟加载依赖（缺失时会抛出清晰错误，而非模块级崩溃）
    _ensure_quant_deps()

    # 从配置中读取参数（带默认值）
    tushare_token: str = config.get("TUSHARE_TOKEN", "")
    index_codes: list = config.get("INDEX_CODES", ["000300.SH", "000905.SH"])
    price_low: float = float(config.get("PRICE_LOW", 5.0))
    price_high: float = float(config.get("PRICE_HIGH", 36.0))
    max_stocks: int = int(config.get("MAX_STOCKS", 80))
    start: str = config.get("START", "2018-01-01")
    end = config.get("END", None)
    horizon: int = int(config.get("HORIZON", 10))
    buy_th: float = float(config.get("BUY_TH", 0.02))
    sell_th: float = float(config.get("SELL_TH", -0.02))
    train_min_years: int = int(config.get("TRAIN_MIN_YEARS", 3))
    step_days: int = int(config.get("STEP_DAYS", 20))

    # 初始化 Tushare
    ts.set_token(tushare_token)
    pro = ts.pro_api()

    log("[STEP 1/4] 下载全球特征数据...")
    global_df = load_global_features(start, end)

    log("[STEP 2/4] 构建股票池（指数成分+价格过滤）...")
    universe = get_universe_from_indexes(
        pro=pro,
        index_codes=index_codes,
        price_low=price_low,
        price_high=price_high,
        start_date=start,
        end_date=end if end else pd.Timestamp.today().strftime("%Y-%m-%d"),
        max_stocks=max_stocks,
        log_callback=log_callback,
    )
    log(f"[INFO] 股票池大小: {len(universe)}")

    log("[STEP 3/4] 构建多股票面板...")
    panel = build_panel(
        universe=universe,
        pro=pro,
        start=start,
        end=end,
        global_df=global_df,
        horizon=horizon,
        buy_th=buy_th,
        sell_th=sell_th,
        log_callback=log_callback,
    )
    log(f"[INFO] 面板行数: {len(panel)}")

    feature_train_cols = [
        "ret_1",
        "logret_1",
        "close_sma10_ratio",
        "close_sma30_ratio",
        "ema_diff_ratio",
        "rsi_14",
        "macd",
        "macd_signal",
        "macd_hist",
        "bb_width",
        "close_bb_ratio",
        "atr_14",
        "vol_20",
        "vol_60",
        "vol_chg_1",
        "risk_score",
        "risk_off_strength",
    ]

    for c in feature_train_cols:
        if c not in panel.columns:
            raise RuntimeError(f"缺少特征列: {c}")

    log("[STEP 4/4] Walk-forward 回测中...")
    result = walk_forward_backtest(
        panel=panel,
        feature_train_cols=feature_train_cols,
        train_min_years=train_min_years,
        step_days=step_days,
        cfg=config,
        log_callback=log_callback,
    )
    return result
