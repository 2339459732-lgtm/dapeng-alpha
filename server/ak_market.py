"""
DAPENG Alpha - AkShare 宏观、行业、资金、公告与行情综合数据引擎 (V15.0)
结合 AkShare 开源规范与直连容灾，支持：
1. 实时行情与指数监控
2. 行业板块强弱与资金流向
3. 个股与全市场公告/研报/新闻
4. 宏观数据（GDP, CPI, PMI, LPR 利率）
5. 完整数据质量与来源追踪元数据
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
import requests
from server.core.numbers import safe_float as _safe_float

logger = logging.getLogger(__name__)

# 内存缓存结构，避免高频击穿
_CACHE_STORE: Dict[str, Dict[str, Any]] = {}

def _get_cache(key: str, ttl: int = 30) -> Optional[Any]:
    if key in _CACHE_STORE:
        item = _CACHE_STORE[key]
        if time.time() - item["ts"] < ttl:
            return item["data"]
    return None

def _set_cache(key: str, data: Any):
    _CACHE_STORE[key] = {"data": data, "ts": time.time()}


def _opt_float(val) -> Optional[float]:
    """转可选浮点：缺失 / "-" / "--" / NaN / ±inf / 非数 → None（如实表示"无数据"，不写假零）。

    用于**行情字段**（价格/涨跌幅/成交额/净流入…）：这类字段取不到时必须返回 None，
    返回 0.0 会被前端当真实值渲染成「价格 0」「净流入 0」。
    """
    if val is None or val == "" or val == "-" or val == "--":
        return None
    try:
        f = float(val)
    except (ValueError, TypeError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _opt_int(val) -> Optional[int]:
    """转可选整数：缺失 / NaN / 非数 → None（如实表示"无数据"，不写假零）。"""
    if val is None or val == "" or val == "-" or val == "--":
        return None
    try:
        f = float(val)
    except (ValueError, TypeError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return int(f)


class AkShareDataHub:
    """AkShare 风格全量金融数据中枢"""

    @classmethod
    async def get_market_spot(cls) -> Dict[str, Any]:
        """沪深A股行情快照 —— 复用 /api/market-dashboard 的「多板块样本」取价口径。

        旧实现走东财 clist `fid=f3&po=1`（按涨跌幅倒序），上游只回约 100 行（全是
        涨幅榜前 100），用它算"全市场涨跌分布"必然 up=100/down=0，与
        /api/market-dashboard 的真实样本广度自相矛盾。现改为复用 dashboard 的同源
        腾讯批量取价，口径统一；它仍属"系统覆盖的沪深A股样本"，如实标注，不冒充
        交易所全量。取不到时返回 ok:false + reason（不再用涨幅榜前 N 冒充全市场）。
        """
        cache_key = "ak_market_spot"
        cached = _get_cache(cache_key, ttl=15)
        if cached:
            return cached

        try:
            from server.services.market_dashboard import build_dashboard as _build_dashboard
            dash = await _build_dashboard() or {}
            quotes = dash.get("quotes") or []
            if not quotes:
                return {
                    "ok": False,
                    "source": "tencent_quotes (market-dashboard 同源样本)",
                    "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "total": 0,
                    "data": [],
                    "status": "error",
                    "reason": "样本行情暂不可得（腾讯行情不可达或全样本无有效报价）",
                }
            results = [
                {
                    "code": q.get("code"),
                    "symbol": str(q.get("code", ""))[2:],
                    "name": q.get("name"),
                    "price": _opt_float(q.get("price")),
                    "chg": _opt_float(q.get("chg")),
                    "amount": _opt_float(q.get("amount")),
                    "turnover": _opt_float(q.get("turnover")),
                }
                for q in quotes
            ]
            res = {
                "ok": True,
                "source": "tencent_quotes (market-dashboard 同源样本)",
                "scope": dash.get("scope") or "系统覆盖的沪深A股样本（不含ETF/港股）",
                "coverage": dash.get("coverage"),
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total": len(results),
                "data": results,
                "status": "normal",
            }
            _set_cache(cache_key, res)
            return res
        except Exception as e:
            logger.error("market spot (dashboard 同源样本) failed: %s", e)
            return {
                "ok": False,
                "source": "tencent_quotes (market-dashboard 同源样本)",
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total": 0,
                "data": [],
                "status": "error",
                "reason": f"样本行情不可得: {e}",
            }

    @classmethod
    async def get_north_flow(cls) -> Dict[str, Any]:
        """沪深港通资金流向（北向沪股通/深股通 + 南向港股通）。

        数据来源：AkShare ``stock_hsgt_fund_flow_summary_em``。

        ⚠️ 重要事实：北向资金「成交净买额」自 2024-08 起交易所已**停止实时披露**，
        接口固定返回 0。因此本方法除净买额外，额外返回**仍然真实可得**的
        成分股涨跌分布（上涨/持平/下跌家数）与相关指数表现，并给出
        ``disclosed`` 标记，避免前端把 0 误读为「资金零流入」。
        """
        cache_key = "ak_north_flow"
        cached = _get_cache(cache_key, ttl=120)
        if cached:
            return cached

        def _fetch():
            # akshare 首次导入较慢（数秒），放到线程池内惰性导入
            import akshare as ak
            return ak.stock_hsgt_fund_flow_summary_em()

        try:
            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(None, _fetch)
            rows = df.to_dict("records")

            hgt: Dict[str, Any] = {}
            sgt: Dict[str, Any] = {}
            south: list = []
            trade_date = ""
            for r in rows:
                board = str(r.get("板块", "") or "")
                # 扫全表取首个非空交易日（个别行可能为空）
                td = str(r.get("交易日", "") or "").strip()
                if not trade_date and td:
                    trade_date = td
                item = {
                    "board": board,
                    "net_buy": _opt_float(r.get("成交净买额")),      # 亿元
                    "net_inflow": _opt_float(r.get("资金净流入")),
                    # 港股通行的「上涨数/下跌数」在该源里为 NaN → None（不写假零）；
                    # 沪股通/深股通为真实数值
                    "up": _opt_int(r.get("上涨数")),
                    "flat": _opt_int(r.get("持平数")),
                    "down": _opt_int(r.get("下跌数")),
                    "index_name": str(r.get("相关指数", "") or ""),
                    "index_chg": _opt_float(r.get("指数涨跌幅")),
                }
                if board == "沪股通":
                    hgt = item
                elif board == "深股通":
                    sgt = item
                elif board.startswith("港股通"):
                    south.append(item)

            # 前端 app.js 既有约定：total/hgt/sgt 单位为「万元」，故由亿元 ×10000。
            # 取不到一律 None（**绝不写 0**，0 会被渲染成「净买额 0 元」的假零）
            _hgt_raw = _opt_float(hgt.get("net_buy"))
            _sgt_raw = _opt_float(sgt.get("net_buy"))
            hgt_net = (_hgt_raw * 10000) if _hgt_raw is not None else None
            sgt_net = (_sgt_raw * 10000) if _sgt_raw is not None else None
            disclosed = (hgt_net is not None and hgt_net != 0.0) or (sgt_net is not None and sgt_net != 0.0)
            net_note = "北向资金成交净买额自 2024-08 起停止实时披露，改以成分股涨跌分布与相关指数表现呈现"
            # 未披露时严禁返回 0.0（前端会渲染成「净买额 0 元」的假零）→ null + note 说明
            _both_known = (hgt_net is not None and sgt_net is not None)
            total_out = (hgt_net + sgt_net) if (disclosed and _both_known) else None
            hgt_out = hgt_net if disclosed else None
            sgt_out = sgt_net if disclosed else None
            # 交易日：恒有值时用于标注「这是哪天的分布」；确实缺失则 null + reason（不臆造日期）
            trade_date_out = trade_date or None
            trade_date_reason = None if trade_date_out else "上游未返回交易日，无法标注分布所属交易日"

            res = {
                "ok": True,
                "source": "AkShare / 沪深港通资金流向",
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data": {
                    "trade_date": trade_date_out,
                    "trade_date_reason": trade_date_reason,
                    "total": total_out,
                    "hgt": hgt_out,
                    "sgt": sgt_out,
                    "disclosed": disclosed,
                    "note": "" if disclosed else net_note,
                    "hgt_up": hgt.get("up", None),
                    "hgt_flat": hgt.get("flat", None),
                    "hgt_down": hgt.get("down", None),
                    "sgt_up": sgt.get("up", None),
                    "sgt_flat": sgt.get("flat", None),
                    "sgt_down": sgt.get("down", None),
                    "indexes": [
                        {"name": hgt.get("index_name") or "上证指数", "chg": hgt.get("index_chg", 0.0)},
                        {"name": sgt.get("index_name") or "深证成指", "chg": sgt.get("index_chg", 0.0)},
                    ],
                    # 上游源特征：港股通(沪)/港股通(深) 的「资金净流入」与涨跌分布
                    # 取自同一口径（恒生指数成分聚合），故两行相同，仅「成交净买额」不同。
                    # 如实说明，避免被误读为占位/重复赋值。
                    "south_note": ("港股通(沪)/(深) 的「资金净流入」与「上涨/持平/下跌」"
                                   "来自上游同一口径（恒生指数成分聚合），两行数值相同属源特征，"
                                   "非占位或重复赋值；仅「成交净买额」不同。"),
                    "south": south,
                },
                "status": "normal",
            }
            _set_cache(cache_key, res)
            return res
        except Exception as e:
            logger.error("AkShare north flow fetch failed: %s", e)
            return {
                "ok": False,
                "source": "AkShare / 沪深港通资金流向",
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data": None,
                "status": "error",
                "message": str(e),
            }

    @classmethod
    async def get_stock_fund_flow(cls, codes: List[str]) -> Optional[List[Dict[str, Any]]]:
        """按代码批量获取个股资金流（主力/超大单/大单/中单/小单）。

        走东财 ``ulist.np`` 单次批量请求（与 get_market_spot 同一数据源与鉴权串），
        实测 8 只约 3.4s。字段：f62=主力净流入, f66=超大单, f72=大单,
        f78=中单, f84=小单（单位：元）。

        取数失败返回 **None**（而非空列表），以便调用方区分「成功但无数据」
        与「失败」，避免再次出现 ok:true 掩盖失败的老问题。
        """
        if not codes:
            return []

        def _secid(c: str) -> Optional[str]:
            c = str(c or "").strip()
            if c.startswith("sh"):
                return "1." + c[2:]
            if c.startswith("sz"):
                return "0." + c[2:]
            return None

        secids = ",".join(filter(None, (_secid(c) for c in codes)))
        if not secids:
            return None

        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {
            "ut": "bd1d9ddb040897f352c2991005d397bc",
            "fltt": "2", "invt": "2", "np": "1",
            "secids": secids,
            "fields": "f12,f14,f2,f3,f62,f66,f72,f78,f84",
        }
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None, lambda: requests.get(url, params=params, timeout=8)
            )
            rows = ((resp.json().get("data") or {}).get("diff")) or []
            out: List[Dict[str, Any]] = []
            for r in rows:
                sym = str(r.get("f12", "") or "")
                if not sym:
                    continue
                main = _opt_float(r.get("f62"))
                out.append({
                    "symbol": sym,
                    "code": ("sh" if sym.startswith(("6", "9")) else "sz") + sym,
                    "name": r.get("f14", sym),
                    "price": _opt_float(r.get("f2")),
                    "chg": _opt_float(r.get("f3")),
                    "main": main,
                    "super_large": _opt_float(r.get("f66")),
                    "large": _opt_float(r.get("f72")),
                    "medium": _opt_float(r.get("f78")),
                    "small": _opt_float(r.get("f84")),
                    "net_amount": main,   # sentiment.js 兼容字段
                })
            return out
        except Exception as e:
            logger.error("EastMoney stock fund flow fetch failed: %s", e)
            return None

    @classmethod
    async def get_index_spot(cls) -> Dict[str, Any]:
        """主流宽基/风格指数实时行情——改走「腾讯行情」可达通道。

        背景（实测，2026-10）：原主源东财 push2（``push2.eastmoney.com`` /
        ``N.push2.eastmoney.com``）当前被持续阻断——代理与非代理均返回
        ``HTTP 502 Bad Gateway`` / ``ReadTimeout`` / ``RemoteDisconnected``，
        AkShare 的 ``stock_zh_index_spot_em`` 同源同样失败。故按项目约定
        「主源不可达就用可达通道自建」，改由既有腾讯取价链路
        ``server.realtime.fetch_tencent_quotes`` 取指数点位/涨跌幅（真实值）。

        重要：**全市场涨跌家数当前不可得**——腾讯/新浪指数行情均不含该字段
        （腾讯 88 字段中无全市场涨跌家数；新浪 ``getHQNodeStockCount`` 仅返回
        挂牌总数 "5573"，无涨跌分布），东财 push2 的 f104/f105/f106 又因阻断取不到。
        按铁律：该字段返回 ``null`` + ``reason``，**不做任何推算**。
        """
        cache_key = "ak_index_spot"
        cached = _get_cache(cache_key, ttl=15)
        if cached:
            return cached

        # 宽基 + 风格指数（腾讯行情代码）
        index_codes = [
            "sh000001", "sz399001", "sz399006",
            "sh000300", "sh000905", "sh000852", "sh000688",
        ]
        breadth_reason = (
            "全市场涨跌家数暂不可得：东财 push2 持续阻断（HTTP 502），"
            "腾讯/新浪指数行情不含涨跌家数字段——按项目约定返回 null，不做推算"
        )
        try:
            from server.realtime import fetch_tencent_quotes
            quotes = await fetch_tencent_quotes(index_codes, timeout=6.0)
        except Exception as e:  # pragma: no cover - 防御性
            logger.error("Tencent index fetch failed: %s", e)
            quotes = []

        indices: List[Dict[str, Any]] = []
        for q in (quotes or []):
            price = _safe_float(q.get("price"))
            if price <= 0:
                continue
            # 成交额：腾讯原始字段为「万元」。盘前/非交易时段该字段原值为 "0"
            # （实测原始响应 fields 成交额位 = "0"/"0.00"，非取数失败），
            # 为避免前端把 0 当成真实成交额展示（假零），此处如实返回 null + reason。
            amount_wan = _opt_float(q.get("amount"))
            amount = round(amount_wan * 1e4, 2) if (amount_wan is not None and amount_wan > 0) else None
            indices.append({
                "code": q.get("code"),
                "name": q.get("name"),
                "price": price,
                "chg": _opt_float(q.get("chg")),   # 取不到 → null（不写 0.00%，那是假零）
                "amount": amount,   # 元；无成交额 → null（不写假零）
                "amount_reason": None if amount is not None else
                                 "盘前/非交易时段无成交额（腾讯原始成交额字段为 0，非取数失败）",
                "up_count": None,        # 不可得 → null（不推算）
                "down_count": None,
                "flat_count": None,
                "breadth_reason": breadth_reason,
            })

        if not indices:
            res = {
                "ok": False,
                "source": "腾讯行情 (qt.gtimg.cn)",
                "aggregated": False,
                "degraded": True,
                "breadth_available": False,
                "breadth_reason": breadth_reason,
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data": [],
                "status": "error",
                "message": "腾讯行情不可达，指数数据暂不可用",
            }
            return res

        res = {
            "ok": True,
            "source": "腾讯行情 (qt.gtimg.cn)",
            "aggregated": False,      # 指数点位为腾讯实时原始值，未经聚合推导
            "degraded": True,         # 涨跌家数缺失（子字段降级）
            "breadth_available": False,
            "breadth_reason": breadth_reason,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": indices,
            "total": len(indices),
            "status": "partial",
        }
        _set_cache(cache_key, res)
        return res

    @classmethod
    def _fetch_eastmoney_macro(cls) -> Dict[str, Optional[Dict[str, Any]]]:
        """从东财数据中心拉取真实宏观指标（PMI / GDP / CPI），网络异常逐项降级。

        性能约束：三项指标**并发抓取**（每项超时 5s）。
        旧实现串行 → 三项全超时需 15s，且调用方在 async 上下文里直接调用它，
        会把事件循环阻塞满 15s（同时 SSE 推流与其它 API 一起卡）。见 `get_macro_data()` 的
        `run_in_executor` 卸载。
        """
        headers = {"Referer": "https://data.eastmoney.com/", "User-Agent": "Mozilla/5.0"}
        reports = {
            "pmi": ("RPT_ECONOMY_PMI", "MAKE_INDEX", "MAKE_SAME"),
            "gdp": ("RPT_ECONOMY_GDP", "SUM_SAME", None),
            "cpi": ("RPT_ECONOMY_CPI", "NATIONAL_SAME", None),
        }
        out: Dict[str, Optional[Dict[str, Any]]] = {k: None for k in reports}

        def _fetch_one(key: str, report: str, val_field: str, pct_field: Optional[str]):
            try:
                url = (f"https://datacenter-web.eastmoney.com/api/data/v1/get?"
                       f"reportName={report}&columns=ALL&pageSize=1&sortColumns=REPORT_DATE&sortTypes=-1&pageNumber=1")
                resp = requests.get(url, headers=headers, timeout=5)
                data = (resp.json().get("result") or {}).get("data") or []
                if not data:
                    return key, None
                row = data[0]
                value = _safe_float(row.get(val_field))
                if value <= 0:
                    return key, None
                pct = None
                if pct_field is not None:
                    pv = _safe_float(row.get(pct_field))
                    pct = pv if abs(pv) > 0 else None
                return key, {"period": str(row.get("TIME") or row.get("REPORT_DATE") or ""),
                             "value": value, "pct": pct}
            except Exception as exc:
                logger.debug("eastmoney macro %s failed: %s", key, exc)
                return key, None

        # 并发抓取：三项互不依赖，串行只会把最坏耗时相加
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=len(reports)) as pool:
            futures = [pool.submit(_fetch_one, k, r, v, p) for k, (r, v, p) in reports.items()]
            for fut in _cf.as_completed(futures):
                try:
                    k, item = fut.result()
                    out[k] = item
                except Exception as exc:  # pragma: no cover - 防御式
                    logger.debug("eastmoney macro future failed: %s", exc)
        return out

    @classmethod
    async def get_macro_data(cls) -> Dict[str, Any]:
        """宏观核心指标（PMI / CPI / GDP / LPR）——真实数据优先，缺失项一律 null + reason。

        红线：实时源不可用时**不再填示例值**。旧实现会给出「PMI 50.4%/扩张区间」等编造数
        （desc 虽带“示例值”字样，但正文区仍被当真实数据渲染），且顶层 source 把真实来源与
        “降级示例”混写成同一字符串。现在：
        - 单指标取不到 → value/period/status/trend 全 null + reason；
        - 全部指标都取不到 → 顶层 ok:false + reason（且不写缓存，便于恢复）；
        - 顶层 source 只写真实来源，不再与“降级示例”混写。
        """
        cache_key = "ak_macro_data"
        cached = _get_cache(cache_key, ttl=600)  # 宏观数据缓存10分钟
        if cached:
            return cached

        # 卸载到线程池：本方法内是阻塞的 requests.get，直接在 async 里调用会阻塞事件循环
        # （实测串行版最坏 15s，期间 SSE 推流与所有其它 API 一起卡死）。
        loop = asyncio.get_running_loop()
        real = await loop.run_in_executor(None, cls._fetch_eastmoney_macro)  # {pmi, gdp, cpi}，取不到为 None
        pmi, gdp, cpi = real.get("pmi"), real.get("gdp"), real.get("cpi")

        def _fmt(name: str, value: float) -> str:
            if "同比" in name or "CPI" in name:
                return f"{value:+.1f}%"
            if name.endswith("PMI"):
                return f"{value:.1f}"
            return f"{value:.2f}"

        def _real_item(name: str, real_val: Dict[str, Any], status_true: str, desc: str) -> Dict[str, Any]:
            value = real_val["value"]
            trend = "up" if value >= 0 else "dn"
            status = ("扩张区间" if value >= 50 else "收缩区间") if name.endswith("PMI") else status_true
            return {"name": name, "value": _fmt(name, value), "status": status,
                    "period": real_val["period"], "trend": trend, "desc": desc,
                    "source": "realtime", "reason": None}

        def _missing(name: str, desc: str,
                     reason: str = "上游未返回该指标（EastMoney Datacenter 无数据）") -> Dict[str, Any]:
            return {"name": name, "value": None, "status": None, "period": None,
                    "trend": None, "desc": desc, "source": "unavailable", "reason": reason}

        indicators = [
            _real_item("中国制造业PMI", {"value": pmi["value"], "period": pmi["period"]},
                       "扩张区间", "制造业采购经理指数（国家统计局口径）") if pmi
            else _missing("中国制造业PMI", "制造业采购经理指数（国家统计局口径）"),
            _real_item("CPI 同比", {"value": cpi["value"], "period": cpi["period"]},
                       "物价水平", "全国居民消费价格同比") if cpi
            else _missing("CPI 同比", "全国居民消费价格同比"),
            _real_item("GDP 同比增速", {"value": gdp["value"], "period": gdp["period"]},
                       "平稳增长", "国内生产总值同比增速") if gdp
            else _missing("GDP 同比增速", "国内生产总值同比增速"),
            # LPR 无可用实时源（旧实现即硬编码）→ 恒 null + 明确 reason，不填示例值
            _missing("1年期 LPR 利率", "贷款市场报价利率（1年期）",
                     "LPR 无可用实时源：未接入全国银行间同业拆借中心数据，不填示例值"),
            _missing("5年期以上 LPR", "贷款市场报价利率（5年期以上）",
                     "LPR 无可用实时源：未接入全国银行间同业拆借中心数据，不填示例值"),
        ]

        real_count = sum(1 for m in indicators if m.get("source") == "realtime")
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if real_count == 0:
            # 全部不可用：ok:false + reason；不写缓存，便于上游恢复后即时转好
            return {
                "ok": False,
                "source": "EastMoney Datacenter",
                "reason": "宏观实时源不可用：EastMoney Datacenter 未返回 PMI/GDP/CPI 任一指标；"
                          "LPR 亦无可用实时源（不填示例值）",
                "data": None,
                "update_time": update_time,
                "status": "error",
            }

        res = {
            "ok": True,
            "source": "EastMoney Datacenter",
            "degraded": real_count < len(indicators),
            "reason": None if real_count == len(indicators) else
                      f"{len(indicators) - real_count} 项指标实时源不可用，已返回 null（未填示例值）",
            "data": indicators,
            "data_source": "realtime" if real_count == len(indicators) else "partial",
            "realtime_count": real_count,
            "update_time": update_time,
            "status": "normal" if real_count == len(indicators) else "partial",
        }
        _set_cache(cache_key, res)
        return res

    @classmethod
    async def _fetch_sina_news(cls, limit: int) -> Optional[Dict[str, Any]]:
        """新浪财经滚动新闻备用源（东财失效/空数据时降级）"""
        try:
            url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num={limit}&page=1"
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(url, headers={"Referer": "https://finance.sina.com.cn/"}, timeout=4),
            )
            items = (resp.json().get("result") or {}).get("data") or []
            news_list = []
            for item in items:
                ts = _opt_float(item.get("ctime"))
                news_list.append({
                    "title": item.get("title", ""),
                    # 时间戳缺失 → None（前端显示 --），不写空串冒充
                    "time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else None,
                    "source": item.get("media_name") or "新浪财经",
                    "url": item.get("url", ""),
                    "digest": item.get("summary", "") or "",
                    "tag": "政策/要闻" if any(k in item.get("title", "") for k in ("政策", "央行", "国务院", "证监会")) else "市场动态",
                })
            if not news_list:
                return None
            return {
                "ok": True,
                "source": "Sina Finance Roll Stream (fallback)",
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total": len(news_list),
                "data": news_list,
                "status": "normal",
            }
        except Exception as e:
            logger.warning(f"Sina news fallback failed: {e}")
            return None

    @classmethod
    async def get_notices_and_news(cls, limit: int = 15) -> Dict[str, Any]:
        """获取市场重大公告、政策资讯与舆情情报"""
        cache_key = f"ak_notices_news_{limit}"
        cached = _get_cache(cache_key, ttl=60)
        if cached:
            return cached

        url = "http://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
        params = {
            "client": "web",
            "biz": "web_news_col",
            "column": "102", # 财经要闻
            "order": "1",
            "page_index": "1",
            "page_size": str(limit),
            "req_trace": str(int(time.time()*1000))
        }
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: requests.get(url, params=params, timeout=4))
            items = resp.json().get("data", {}).get("list", [])
            
            news_list = []
            for item in items:
                news_list.append({
                    "title": item.get("title", ""),
                    "time": item.get("showTime", ""),
                    "source": item.get("source", "财经即时"),
                    "url": item.get("url", ""),
                    "digest": item.get("digest", ""),
                    "tag": "政策/要闻" if "政策" in item.get("title", "") or "央行" in item.get("title", "") else "市场动态"
                })

            if not news_list:
                fallback = await cls._fetch_sina_news(limit)
                if fallback:
                    _set_cache(cache_key, fallback)
                    return fallback

            res = {
                "ok": True,
                "source": "AkShare / Financial News & Notice Stream",
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total": len(news_list),
                "data": news_list,
                "status": "normal"
            }
            _set_cache(cache_key, res)
            return res
        except Exception as e:
            logger.warning(f"News fetch failed: {e}")
            fallback = await cls._fetch_sina_news(limit)
            if fallback:
                _set_cache(cache_key, fallback)
                return fallback
            return {
                "ok": False,
                "source": "AkShare News Engine",
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total": 0,
                "data": [],
                "status": "degraded",
                "message": str(e)
            }


ak_hub = AkShareDataHub()
