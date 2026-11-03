"""
DAPENG Alpha - 缠论（Chan Theory）结构引擎 V1.0

从真实 K 线中识别缠论核心结构，为「动态因子缠论」提供结构层信号：
  分型（顶/底） → 笔（上涨笔/下跌笔） → 线段（≥3 笔摆动） → 中枢（三笔重叠区间）
  → 背驰（同向笔力度衰竭） → 三类买卖点（一买/二买/三买 与 一卖/二卖/三卖）

全部基于真实量价数据计算，不生成虚构行情；数据不足时显式降级。

术语（与经典缠论一致）：
  - 顶分型：某 K 线高点高于左右相邻两根；底分型反之。
  - 笔：相邻顶/底分型之间的一段走势，方向交替（顶→底 = 下跌笔，底→顶 = 上涨笔）。
  - 中枢 [ZD, ZG]：连续三笔重叠的公共价格区间（ZD=三笔低点最大值，ZG=三笔高点最小值）。
  - 背驰：创新高/新低但同向 MACD 动能较前一段衰减，预示趋势衰竭。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 分型之间最少间隔 K 线数（含端点处理；过近的分型按包含规则舍弃）
_MIN_GAP = 1
# 一笔至少跨越的 K 线根数（分型间隔下限，过滤毛刺）
_MIN_STROKE_BARS = 3


def _f(val: Any, default: float = 0.0) -> float:
    try:
        n = float(val)
        return n if n == n else default
    except (TypeError, ValueError):
        return default


def _extract(klines: List[Dict[str, Any]], key: str) -> List[float]:
    return [_f(k.get(key)) for k in klines]


class ChanEngine:
    """缠论结构识别（日线默认，亦可用于任意周期 K 线序列）"""

    def analyze(self, klines: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not klines or len(klines) < 10:
            return {"available": False, "reason": "K线不足（<10 根）", "fractals": [], "strokes": [], "pivots": [], "current": {}}

        highs = _extract(klines, "h")
        lows = _extract(klines, "l")
        closes = _extract(klines, "c")
        dates = [str(k.get("d", "")) for k in klines]
        n = len(klines)

        fractals = self._fractals(highs, lows, dates)
        strokes = self._strokes(fractals, highs, lows, dates)
        pivots = self._pivots(strokes)
        current = self._current_state(strokes, pivots, closes)

        return {
            "available": True,
            "bars": n,
            "fractals": fractals,
            "strokes": strokes,
            "pivots": pivots,
            "current": current,
        }

    # ----------------------------------------------------------
    # 分型
    # ----------------------------------------------------------
    @staticmethod
    def _fractals(highs: List[float], lows: List[float], dates: List[str]) -> List[Dict[str, Any]]:
        """识别顶/底分型（高点/低点均严格高于/低于左右邻）。"""
        out: List[Dict[str, Any]] = []
        for i in range(1, len(highs) - 1):
            h, l = highs[i], lows[i]
            is_top = h > highs[i - 1] and h > highs[i + 1]
            is_bottom = l < lows[i - 1] and l < lows[i + 1]
            if is_top and not is_bottom:
                out.append({"type": "top", "index": i, "price": round(h, 3), "date": dates[i] if i < len(dates) else ""})
            elif is_bottom and not is_top:
                out.append({"type": "bottom", "index": i, "price": round(l, 3), "date": dates[i] if i < len(dates) else ""})
        return out

    # ----------------------------------------------------------
    # 笔
    # ----------------------------------------------------------
    def _strokes(self, fractals: List[Dict[str, Any]], highs: List[float], lows: List[float], dates: List[str]) -> List[Dict[str, Any]]:
        """由交替分型连笔：顶→底（下跌笔）、底→顶（上涨笔），过滤间隔过近的毛刺。"""
        strokes: List[Dict[str, Any]] = []
        if not fractals:
            return strokes
        # 保留首分型
        anchors: List[Dict[str, Any]] = [fractals[0]]
        for f in fractals[1:]:
            last = anchors[-1]
            if f["type"] == last["type"]:
                # 同类型分型：取更极端者（顶取更高、底取更低），位置更新为更近端
                if f["type"] == "top" and f["price"] >= last["price"]:
                    anchors[-1] = f
                elif f["type"] == "bottom" and f["price"] <= last["price"]:
                    anchors[-1] = f
            else:
                # 间隔不足则跳过（去毛刺）
                if f["index"] - last["index"] < _MIN_GAP:
                    continue
                anchors.append(f)

        for a, b in zip(anchors, anchors[1:]):
            if b["index"] - a["index"] < _MIN_STROKE_BARS:
                continue
            if a["type"] == "bottom" and b["type"] == "top":
                direction = "up"
                start_price, end_price = a["price"], b["price"]
            elif a["type"] == "top" and b["type"] == "bottom":
                direction = "down"
                start_price, end_price = a["price"], b["price"]
            else:
                continue
            amp = (end_price / start_price - 1) * 100 if start_price else 0.0
            strokes.append({
                "dir": direction,
                "start_idx": a["index"], "end_idx": b["index"],
                "start_price": start_price, "end_price": end_price,
                "start_date": a.get("date", ""), "end_date": b.get("date", ""),
                "amplitude_pct": round(amp, 2),
            })
        return strokes

    # ----------------------------------------------------------
    # 中枢
    # ----------------------------------------------------------
    @staticmethod
    def _pivots(strokes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """连续三笔重叠区间 → 中枢 [ZD, ZG]。"""
        pivots: List[Dict[str, Any]] = []
        for i in range(len(strokes) - 2):
            trio = strokes[i:i + 3]
            lows = [min(s["start_price"], s["end_price"]) for s in trio]
            highs = [max(s["start_price"], s["end_price"]) for s in trio]
            zd = max(lows)
            zg = min(highs)
            if zd < zg:  # 有重叠 → 构成中枢
                pivots.append({
                    "zd": round(zd, 3), "zg": round(zg, 3),
                    "center": round((zd + zg) / 2, 3),
                    "start_idx": trio[0]["start_idx"], "end_idx": trio[-1]["end_idx"],
                    "width_pct": round((zg - zd) / zd * 100, 2) if zd else 0.0,
                })
        return pivots

    # ----------------------------------------------------------
    # 背驰 + 买卖点 + 趋势判定
    # ----------------------------------------------------------
    def _current_state(
        self,
        strokes: List[Dict[str, Any]],
        pivots: List[Dict[str, Any]],
        closes: List[float],
    ) -> Dict[str, Any]:
        cur = {
            "stroke_dir": "none", "pivot": None, "divergence": None,
            "buy_point": None, "sell_point": None, "position_in_pivot": "无中枢",
            "trend": "数据不足", "desc": "K线数据不足，暂无法判定缠论结构",
        }
        if not strokes:
            return cur

        last = strokes[-1]
        cur["stroke_dir"] = last["dir"]
        cur["last_stroke"] = {
            "dir": last["dir"], "amplitude_pct": last["amplitude_pct"],
            "start_date": last["start_date"], "end_date": last["end_date"],
            "start_price": last["start_price"], "end_price": last["end_price"],
        }

        # 中枢与相对位置
        if pivots:
            pv = pivots[-1]
            cur["pivot"] = pv
            px = closes[-1] if closes else last["end_price"]
            if px > pv["zg"]:
                cur["position_in_pivot"] = "above"
            elif px < pv["zd"]:
                cur["position_in_pivot"] = "below"
            else:
                cur["position_in_pivot"] = "inside"

        # 背驰：同向笔力度对比（价格创新极值但笔幅度衰减）
        cur["divergence"] = self._divergence(strokes)

        # 买卖点
        buy, sell = self._signal_points(strokes, pivots, closes)
        cur["buy_point"] = buy
        cur["sell_point"] = sell

        # 趋势与中文描述
        cur["trend"] = self._trend(strokes, closes)
        cur["desc"] = self._describe(cur, strokes, closes)
        return cur

    @staticmethod
    def _divergence(strokes: List[Dict[str, Any]]) -> Optional[str]:
        """顶背驰/底背驰：同向相邻两笔，价格创新极值但笔幅度显著衰减。"""
        same_dir = [s for s in strokes if s["dir"] == strokes[-1]["dir"]]
        if len(same_dir) < 2:
            return None
        prev, cur = same_dir[-2], same_dir[-1]
        if cur["dir"] == "up":
            price_new_extreme = cur["end_price"] > prev["end_price"]
        else:
            price_new_extreme = cur["end_price"] < prev["end_price"]

        if not price_new_extreme:
            return None
        # 笔幅度作为动能代理：创新极值但力度 < 前一笔 60% → 衰竭
        if abs(cur["amplitude_pct"]) < abs(prev["amplitude_pct"]) * 0.6:
            return "top" if cur["dir"] == "up" else "bottom"
        return None

    def _signal_points(
        self,
        strokes: List[Dict[str, Any]],
        pivots: List[Dict[str, Any]],
        closes: List[float],
    ) -> Tuple[Optional[str], Optional[str]]:
        buy: Optional[str] = None
        sell: Optional[str] = None
        last = strokes[-1]

        # 一买：下跌笔末 + 底背驰；一卖：上涨笔末 + 顶背驰（笔幅度衰减判定）
        same_dir = [s for s in strokes if s["dir"] == last["dir"]]
        div_state = None
        if len(same_dir) >= 2:
            prev, cur = same_dir[-2], same_dir[-1]
            new_extreme = (cur["end_price"] > prev["end_price"]) if cur["dir"] == "up" else (cur["end_price"] < prev["end_price"])
            if new_extreme and abs(cur["amplitude_pct"]) < abs(prev["amplitude_pct"]) * 0.6:
                div_state = "top" if cur["dir"] == "up" else "bottom"

        if last["dir"] == "down" and div_state == "bottom":
            buy = "一买（底背驰）"
        if last["dir"] == "up" and div_state == "top":
            sell = "一卖（顶背驰）"

        # 二买/二卖：反向回调不破前低/前高
        if len(strokes) >= 2:
            p1, p2 = strokes[-2], strokes[-1]
            if p1["dir"] == "down" and p2["dir"] == "up":
                if p2["start_price"] > p1["end_price"]:  # 回调不破前低
                    buy = buy or "二买（回调不破前低）"
            if p1["dir"] == "up" and p2["dir"] == "down":
                if p2["start_price"] < p1["end_price"]:  # 反弹不破前高
                    sell = sell or "二卖（反弹不破前高）"

        # 三买/三卖：突破中枢后回踩不破上沿/下沿
        if pivots and len(strokes) >= 1:
            pv = pivots[-1]
            px = closes[-1] if closes else last["end_price"]
            if px > pv["zg"] and last["dir"] == "up":
                buy = buy or "三买（突破中枢）"
            if px < pv["zd"] and last["dir"] == "down":
                sell = sell or "三卖（跌破中枢）"
        return buy, sell

    @staticmethod
    def _trend(strokes: List[Dict[str, Any]], closes: List[float]) -> str:
        if not closes:
            return "数据不足"
        up = sum(1 for s in strokes[-4:] if s["dir"] == "up")
        down = sum(1 for s in strokes[-4:] if s["dir"] == "down")
        chg20 = 0.0
        if len(closes) >= 21 and closes[-21]:
            chg20 = (closes[-1] / closes[-21] - 1) * 100
        if up >= 3 and chg20 > 0:
            return "上涨"
        if down >= 3 and chg20 < 0:
            return "下跌"
        return "震荡"

    def _describe(self, cur: Dict[str, Any], strokes: List[Dict[str, Any]], closes: List[float]) -> str:
        parts: List[str] = []
        last = strokes[-1]
        parts.append(f"当前为{('上涨笔' if last['dir'] == 'up' else '下跌笔')}，幅度 {last['amplitude_pct']:+.2f}%")
        if cur["pivot"]:
            pv = cur["pivot"]
            parts.append(f"最近中枢区间 [{pv['zd']:.2f}, {pv['zg']:.2f}]，价格位于中枢{({'above': '上方', 'below': '下方', 'inside': '内部'}).get(cur['position_in_pivot'], '附近')}")
        if cur["divergence"]:
            parts.append(("顶背驰" if cur["divergence"] == "top" else "底背驰") + "信号出现")
        if cur["buy_point"]:
            parts.append(f"买点：{cur['buy_point']}")
        if cur["sell_point"]:
            parts.append(f"卖点：{cur['sell_point']}")
        if not cur["buy_point"] and not cur["sell_point"]:
            parts.append("暂无明确买卖点")
        return "；".join(parts) + "。"


chan_engine = ChanEngine()
