"""StableAStockDataProvider：对外统一取数接口"""

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

from server.stable_cache import (
    _get_cache,
    _load_sector_periods,
    _mark_stale,
    _num_or_none,
    _read_disk_cache,
    _sector_level_map,
    _set_cache,
    _write_disk_cache,
    logger,
)
from server.stable_em import (
    _EM_DATACENTER_NODES,
    _EM_DATACENTER_PATH,
    _TENCENT_AGG_CACHE_TTL,
    _em_get_json,
    _pull_push2,
)
from server.stable_sector import (
    _EM_LHB_STALE_DAYS,
    _em_lhb_params,
    _is_stale_date,
    _tencent_sector_aggregate,
)

class StableAStockDataProvider:
    """稳定直接的 A 股金融数据提供器"""

    @classmethod
    async def get_hot_stocks(cls, limit: int = 20) -> List[Dict[str, Any]]:
        """获取真实市场热门股榜单"""
        cache_key = f"hot_stocks_real_{limit}"
        cached = _get_cache(cache_key, ttl=20)
        if cached:
            return cached

        # 东方财富实时人气/活跃度聚合（按换手率与成交额复合排序）
        params = {
            "pn": "1", "pz": str(max(limit, 30)), "po": "1", "np": "1",
            "ut": "bd1d9ddb040897f352c2991005d397bc",
            "fltt": "2", "invt": "2", "fid": "f8",  # 按换手率与热度排序
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f12,f14,f2,f3,f4,f5,f6,f8,f9,f10,f100,f15,f16,f17,f18"
        }
        try:
            json_data = await _pull_push2(params)
            if json_data:
                data_obj = json_data.get("data") if isinstance(json_data, dict) else None
                rows = data_obj.get("diff", []) if isinstance(data_obj, dict) else []
                if rows:
                    results = []
                    for idx, r in enumerate(rows[:limit]):
                        code = str(r.get("f12", ""))
                        if not code:
                            continue
                        full_code = ("sh" if code.startswith("6") or code.startswith("9") else "sz") + code
                        chg = float(r.get("f3", 0) or 0)
                        turnover = float(r.get("f8", 0) or 0)
                        amount = float(r.get("f6", 0) or 0)
                        price = float(r.get("f2", 0) or 0)
                        
                        # 动态生成具体筛选依据/热度标签
                        if turnover > 15:
                            hot_reason = f"换手率达{turnover:.1f}% 活跃游资与机构博弈"
                        elif chg > 7:
                            hot_reason = f"日内大涨{chg:.1f}% 强势突破多头排列"
                        elif amount > 3e9:
                            hot_reason = f"成交破{(amount/1e8):.1f}亿 核心龙头承接力强"
                        elif chg > 3:
                            hot_reason = f"量价齐升 均线多头趋势加速"
                        elif turnover > 8:
                            hot_reason = f"高活跃换手 筹码充分换手洗盘"
                        else:
                            hot_reason = "行业主线龙头 资金关注度居前"

                        status = "涨停" if chg >= 9.8 else "大涨" if chg >= 5.0 else "领涨" if chg > 0 else "回调"

                        results.append({
                            "rank": idx + 1,
                            "code": full_code,
                            "symbol": code,
                            "name": r.get("f14", code),
                            "price": price,
                            "chg": chg,
                            "change_pct": chg,
                            "turnover": turnover,
                            "amount": amount,
                            "pe": float(r.get("f9", 0) or 0),
                            "sector": r.get("f100", "科技制造"),
                            "reason": hot_reason,
                            "status": status,
                            "hot_score": round(max(50.0, min(99.0, 98.0 - idx * 2.2 + (chg * 0.5))), 1),
                            "source": "东方财富实时人气榜",
                            "synthetic": False,
                            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        })
                    
                    if results:
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        _set_cache(cache_key, results)
                        _write_disk_cache(f"hot_stocks_{limit}", results, ts)
                        return results
        except Exception as e:
            logger.warning(f"Fetch real hot stocks failed: {e}")

        # 降级链第①级：回放最近一次"真实"热门股（标记 stale + 历史时间）
        disk_rows, disk_ts = _read_disk_cache(f"hot_stocks_{limit}")
        if disk_rows:
            logger.info(
                f"hot_stocks: network failed, replay disk cache fetch_ts={disk_ts}"
            )
            return _mark_stale(disk_rows, "东方财富实时人气榜", disk_ts)

        # 降级链第②级：无真实缓存，才用硬编码本地样本（synthetic/degraded=True）
        cur_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fallback = [
            {"rank": 1, "code": "sh688041", "name": "海光信息", "price": 124.50, "chg": 4.85, "change_pct": 4.85, "turnover": 8.45, "amount": 3840000000, "sector": "半导体", "reason": "国产算力龙头，智算中心需求强劲", "status": "领涨", "hot_score": 96.5, "source": "系统快照", "synthetic": True, "degraded": True, "updated_at": cur_time},
            {"rank": 2, "code": "sz002475", "name": "立讯精密", "price": 38.60, "chg": 3.42, "change_pct": 3.42, "turnover": 6.12, "amount": 2520000000, "sector": "消费电子", "reason": "AI终端换机周期开启，出海订单增长", "status": "领涨", "hot_score": 93.8, "source": "系统快照", "synthetic": True, "degraded": True, "updated_at": cur_time},
            {"rank": 3, "code": "sz300750", "name": "宁德时代", "price": 215.50, "chg": 2.18, "change_pct": 2.18, "turnover": 4.30, "amount": 4120000000, "sector": "电力设备", "reason": "固态电池研发提速，储能景气度向上", "status": "领涨", "hot_score": 91.2, "source": "系统快照", "synthetic": True, "degraded": True, "updated_at": cur_time},
            {"rank": 4, "code": "sh600519", "name": "贵州茅台", "price": 1680.00, "chg": 1.25, "change_pct": 1.25, "turnover": 1.15, "amount": 5600000000, "sector": "消费", "reason": "白酒核心资产估值修复，股息率具备吸引力", "status": "稳健", "hot_score": 89.6, "source": "系统快照", "synthetic": True, "degraded": True, "updated_at": cur_time},
            {"rank": 5, "code": "sz000063", "name": "中兴通讯", "price": 32.10, "chg": 2.75, "change_pct": 2.75, "turnover": 5.80, "amount": 1850000000, "sector": "通信", "reason": "智算网络算力互联核心供应商，订单充沛", "status": "领涨", "hot_score": 87.4, "source": "系统快照", "synthetic": True, "degraded": True, "updated_at": cur_time}
        ]
        return fallback

    @classmethod
    async def get_lhb_data(cls, date_str: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取真实龙虎榜明细数据"""
        cache_key = f"lhb_real_{date_str or 'today'}"
        cached = _get_cache(cache_key, ttl=60)
        if cached:
            return cached

        # 拉取东方财富龙虎榜（HTTPS 多节点；多列排序保证只取最近交易日，绝不落全历史）
        try:
            loop = asyncio.get_event_loop()
            json_data = await loop.run_in_executor(
                None,
                lambda: _em_get_json(
                    _EM_DATACENTER_PATH,
                    _em_lhb_params(date_str),
                    _EM_DATACENTER_NODES,
                ),
            )
            if json_data:
                result_obj = json_data.get("result") if isinstance(json_data, dict) else None
                data = result_obj.get("data", []) if isinstance(result_obj, dict) else []
                if data:
                    # ---- 陈旧性护栏：以返回数据中的最大交易日为准 ----
                    max_date_str = max(
                        (str(r.get("TRADE_DATE", ""))[:10] for r in data), default=""
                    )
                    stale = _is_stale_date(max_date_str)
                    if stale:
                        logger.warning(
                            f"LHB stale data: latest TRADE_DATE={max_date_str} "
                            f"(> {_EM_LHB_STALE_DAYS}d) -> mark stale + degraded"
                        )
                    record_source = (
                        f"东方财富龙虎榜数据中心（历史快照 {max_date_str}）"
                        if stale else "东方财富龙虎榜数据中心"
                    )
                    lhb_list = []
                    for idx, r in enumerate(data):
                        code = str(r.get("SECURITY_CODE", ""))
                        if not code:
                            continue
                        full_code = ("sh" if code.startswith("6") or code.startswith("9") else "sz") + code
                        buy_amt = float(r.get("BILLBOARD_BUY_AMT", 0) or 0)
                        sell_amt = float(r.get("BILLBOARD_SELL_AMT", 0) or 0)
                        net_amt = float(r.get("BILLBOARD_NET_AMT", 0) or (buy_amt - sell_amt))
                        # 席位性质：直接采用东财真实字段 EXPLAIN（如"普通席位卖出，成功率45.01%"），
                        # 不再编造营业部名称；机构参与与否亦由该真实文本推导。
                        seat_explain = str(r.get("EXPLAIN", "") or "")
                        inst_count = 1 if "机构" in seat_explain else 0

                        lhb_list.append({
                            "rank": idx + 1,
                            "code": full_code,
                            "symbol": code,
                            "name": r.get("SECURITY_NAME_ABBR", code),
                            "date": str(r.get("TRADE_DATE", datetime.now().strftime("%Y-%m-%d")))[:10],
                            "price": float(r.get("CLOSE_PRICE", 0) or 0),
                            "chg": float(r.get("CHANGE_RATE", 0) or 0),
                            "change_pct": float(r.get("CHANGE_RATE", 0) or 0),
                            "buy_amount": buy_amt,
                            "sell_amount": sell_amt,
                            "net_amount": net_amt,
                            "net_buy": net_amt,
                            "inst_count": inst_count,
                            "reason": r.get("EXPLANATION", "日涨幅偏离值达7%的证券"),
                            "branch_top": seat_explain,
                            "source": record_source,
                            "synthetic": False,
                            "stale": stale,
                            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        })
                    
                    if lhb_list:
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        _set_cache(cache_key, lhb_list)
                        _write_disk_cache("lhb", lhb_list, ts)
                        return lhb_list
        except Exception as e:
            logger.warning(f"Fetch real LHB failed: {e}")

        # 降级链第①级：回放最近一次"真实"龙虎榜（标记 stale + 历史时间）
        disk_rows, disk_ts = _read_disk_cache("lhb")
        if disk_rows:
            logger.info(f"lhb: network failed, replay disk cache fetch_ts={disk_ts}")
            return _mark_stale(disk_rows, "东方财富龙虎榜数据中心", disk_ts)

        # 降级链第②级：无真实缓存，才用硬编码本地样本（synthetic/degraded=True）
        cur_date = date_str or datetime.now().strftime("%Y-%m-%d")
        _now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fallback_lhb = [
            {"rank": 1, "code": "sh688041", "name": "海光信息", "date": cur_date, "price": 124.50, "chg": 4.85, "buy_amount": 284000000, "sell_amount": 62000000, "net_amount": 222000000, "net_buy": 222000000, "inst_count": 3, "reason": "连续三个交易日内收盘价格涨幅偏离值累计达20%", "branch_top": "中信证券北京呼家楼 + 3家机构专用席位", "source": "系统快照", "synthetic": True, "degraded": True, "updated_at": _now_str},
            {"rank": 2, "code": "sz002475", "name": "立讯精密", "date": cur_date, "price": 38.60, "chg": 3.42, "buy_amount": 195000000, "sell_amount": 48000000, "net_amount": 147000000, "net_buy": 147000000, "inst_count": 2, "reason": "日涨幅偏离值达到7%的前五只证券", "branch_top": "华鑫证券上海分公司 + 深股通专用", "source": "系统快照", "synthetic": True, "degraded": True, "updated_at": _now_str},
            {"rank": 3, "code": "sz300750", "name": "宁德时代", "date": cur_date, "price": 215.50, "chg": 2.18, "buy_amount": 320000000, "sell_amount": 180000000, "net_amount": 140000000, "net_buy": 140000000, "inst_count": 4, "reason": "深股通席位大单净买入", "branch_top": "深股通专用 + 三家机构专用席位", "source": "系统快照", "synthetic": True, "degraded": True, "updated_at": _now_str},
            {"rank": 4, "code": "sz000063", "name": "中兴通讯", "date": cur_date, "price": 32.10, "chg": 2.75, "buy_amount": 165000000, "sell_amount": 72000000, "net_amount": 93000000, "net_buy": 93000000, "inst_count": 1, "reason": "日换手率达到20%的前五只证券", "branch_top": "国泰君安上海江苏路 + 机构专用", "source": "系统快照", "synthetic": True, "degraded": True, "updated_at": _now_str}
        ]
        return fallback_lhb

    @classmethod
    async def get_sector_ranking_data(cls) -> List[Dict[str, Any]]:
        """获取真实行业板块综合排行榜"""
        cache_key = "sector_ranking_real"
        cached = _get_cache(cache_key, ttl=30)
        if cached:
            return cached

        base_params = {
            "po": "1", "np": "1",
            "ut": "bd1d9ddb040897f352c2991005d397bc",
            "fltt": "2", "invt": "2", "fid": "f12",  # 稳定排序键：避免 fid=f3 翻页错位/丢数据
            "fs": "m:90+t:2+f:!50",  # 行业板块
            # f62=主力净流入额(元)、f184=主力净占比(%) 为东财真实资金流字段
            # f9=PE(TTM) f20=总市值 f21=流通市值 f23=PB f128=领涨股名 f136=领涨股涨跌幅
            "fields": ("f12,f14,f2,f3,f4,f6,f8,f9,f20,f21,f23,f62,f184,"
                       "f104,f105,f106,f128,f136,f140"),
        }
        try:
            loop = asyncio.get_event_loop()
            # 全量宇宙：分页取回全部行业板块（东财 clist 单页上限 100）
            data: List[Dict[str, Any]] = []
            universe_total = None
            for pn in range(1, 8):
                page_params = {**base_params, "pn": str(pn), "pz": "100"}
                json_data = await _pull_push2(page_params)
                obj = json_data.get("data") if isinstance(json_data, dict) else None
                rows = obj.get("diff") if isinstance(obj, dict) else None
                if not rows:
                    break
                if universe_total is None:
                    universe_total = obj.get("total")
                data.extend(rows)
                if len(rows) < 100 or (universe_total and len(data) >= int(universe_total)):
                    break
            if data:
                    # 多周期收益：仅读离线缓存 data/sector_periods.json（离线构建），
                    # 请求路径绝不逐个拉 K 线；缓存缺失 → null + period_returns_cache_missing
                    periods_map = _load_sector_periods()
                    level_map = _sector_level_map()
                    latest_trade_date = max(
                        (e.get("last_date") for e in periods_map.values()
                         if e.get("last_date")),
                        default=None,
                    )

                    sectors = []
                    for idx, item in enumerate(data):
                        code = str(item.get("f12", ""))
                        chg = float(item.get("f3", 0) or 0)
                        amt = float(item.get("f6", 0) or 0)
                        up_cnt = int(item.get("f104", 0) or 0)
                        dn_cnt = int(item.get("f105", 0) or 0)
                        flat_cnt = int(item.get("f106", 0) or 0)
                        lead = item.get("f140") or item.get("f128") or "--"
                        lead_name = item.get("f128") or None
                        lead_chg = _num_or_none(item.get("f136"))
                        pe = _num_or_none(item.get("f9"))
                        pb = _num_or_none(item.get("f23"))
                        market_cap = _num_or_none(item.get("f20"))
                        float_cap = _num_or_none(item.get("f21"))
                        turnover = _num_or_none(item.get("f8"))

                        # 主力净流入额：东财真实字段 f62（单位：元）。取不到 → None（绝不推算）
                        nf_raw = item.get("f62")
                        net_flow = None
                        if nf_raw is not None and nf_raw not in ("-", ""):
                            try:
                                net_flow = float(nf_raw)
                            except (ValueError, TypeError):
                                net_flow = None
                        net_flow_source = (
                            "eastmoney_fundflow" if net_flow is not None else "unavailable"
                        )

                        name = item.get("f14", "")
                        level = level_map.get(code)
                        if level is None:
                            level = infer_level(name)

                        pr = periods_map.get(code) or {}
                        period_source = pr.get("source") or "unavailable"
                        # 多周期收益质量：ok / short_history(<61根) / stale(落后最新交易日) / missing
                        if not pr:
                            pr_quality = "missing"
                        elif (pr.get("bars_n") or 0) < 61:
                            pr_quality = "short_history"
                        elif latest_trade_date and pr.get("last_date") != latest_trade_date:
                            pr_quality = "stale"
                        else:
                            pr_quality = "ok"

                        sectors.append({
                            "rank": idx + 1,
                            "code": code,
                            "name": name,
                            "level": level,
                            "chg": chg,
                            "change_pct": chg,
                            "chg_pct": chg,
                            "amount": amt,
                            "turnover": turnover,
                            "turnover_reason": (None if turnover is not None
                                                else "eastmoney_f8_unavailable"),
                            "pe": pe,
                            "pe_reason": None if pe is not None else "eastmoney_f9_unavailable",
                            "pb": pb,
                            "pb_reason": (None if pb is not None
                                          else "eastmoney_board_pb_placeholder"),
                            "market_cap": market_cap,
                            "market_cap_reason": (None if market_cap is not None
                                                  else "eastmoney_f20_unavailable"),
                            "float_cap": float_cap,
                            "float_cap_reason": (None if float_cap is not None
                                                 else "eastmoney_f21_unavailable"),
                            "lead_stock": lead,
                            "leading_stock": lead,
                            "lead_stock_name": lead_name,
                            "lead_stock_chg": lead_chg,
                            "up_count": up_cnt,
                            "down_count": dn_cnt,
                            "flat_count": flat_cnt,
                            "count": up_cnt + dn_cnt + flat_cnt,
                            "member_count": up_cnt + dn_cnt + flat_cnt,
                            "net_flow": net_flow,
                            "net_flow_source": net_flow_source,
                            "net_flow_reason": (None if net_flow is not None
                                                else "eastmoney_f62_unavailable"),
                            "5日%": pr.get("d5", pr.get("5")),
                            "20日%": pr.get("d20", pr.get("20")),
                            "60日%": pr.get("d60", pr.get("60")),
                            "period_returns_source": period_source,
                            "period_returns_reason": (None if pr
                                                      else "period_returns_cache_missing"),
                            "period_returns_updated_at": pr.get("updated_at"),
                            "period_returns_quality": pr_quality,
                            "period_returns_bars_n": pr.get("bars_n"),
                            "period_returns_last_date": pr.get("last_date"),
                            "field_sources": {
                                "net_flow": net_flow_source,
                                "period_returns": period_source,
                                "pe": "eastmoney_f9" if pe is not None else "unavailable",
                                "pb": "eastmoney_f23" if pb is not None else "unavailable",
                                "market_cap": ("eastmoney_f20" if market_cap is not None
                                               else "unavailable"),
                                "float_cap": ("eastmoney_f21" if float_cap is not None
                                              else "unavailable"),
                                "turnover": ("eastmoney_f8" if turnover is not None
                                             else "unavailable"),
                                "lead_stock": "eastmoney_f140" if lead else "unavailable",
                                "lead_stock_chg": ("eastmoney_f136" if lead_chg is not None
                                                   else "unavailable"),
                            },
                            "source": "东方财富行业板块实时行情",
                            "synthetic": False,
                            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        })
                    
                    if sectors:
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        _set_cache(cache_key, sectors)
                        _write_disk_cache("sector_ranking", sectors, ts)
                        return sectors
        except Exception as e:
            logger.warning(f"Sector ranking real failed: {e}")

        # 降级链第①级：回放最近一次"真实"板块数据（标记 stale + 历史时间）
        disk_rows, disk_ts = _read_disk_cache("sector_ranking")
        if disk_rows:
            logger.info(
                f"sector_ranking: network failed, replay disk cache fetch_ts={disk_ts}"
            )
            return _mark_stale(disk_rows, "东方财富行业板块实时行情", disk_ts)

        # 降级链第③级：腾讯成分股行情聚合（真实聚合口径，非东财原生）
        agg = _get_cache("sector_ranking_tencent", ttl=_TENCENT_AGG_CACHE_TTL)
        if not agg:
            agg = await _tencent_sector_aggregate()
            if agg:
                _set_cache("sector_ranking_tencent", agg)
        if agg:
            return agg
        agg = await _tencent_sector_aggregate()
        if agg:
            return agg

        # 降级链第④级：连腾讯也不可用，才用硬编码本地样本（synthetic/degraded=True）
        from server.config import SECTORS
        cur_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fallback_sec = []
        idx = 1
        for key, s in SECTORS.items():
            if key in {"etf", "hk_stocks"}:
                continue
            stocks_len = len(s.get("stocks", []))
            fallback_sec.append({
                "rank": idx,
                "code": key,
                "name": s["name"],
                "level": infer_level(s["name"]),
                "chg": 1.85,
                "change_pct": 1.85,
                "chg_pct": 1.85,
                "amount": 28400000000,
                "turnover": 3.45,
                "lead_stock": s["stocks"][0][1] if s.get("stocks") else "--",
                "leading_stock": s["stocks"][0][1] if s.get("stocks") else "--",
                "up_count": max(1, stocks_len - 2),
                "down_count": 2,
                "flat_count": 0,
                "count": stocks_len,
                "net_flow": 1250000000,
                "net_flow_source": "local_sample",
                "5日%": 4.5,
                "20日%": 8.2,
                "60日%": 15.6,
                "period_returns_source": "local_sample",
                "source": "系统快照",
                "synthetic": True,
                "degraded": True,
                "updated_at": cur_time
            })
            idx += 1
        return fallback_sec


stable_provider = StableAStockDataProvider()
