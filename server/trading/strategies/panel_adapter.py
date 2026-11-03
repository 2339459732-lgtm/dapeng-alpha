"""Track A 适配层：只读复用 server.strategies.compute_strategy 的输出

架构蓝图 §7.2：把 StrategyResult 的评分/信号映射为统一 Signal，
不改 strategies.py、不改 app.js；与面板看到的结果同源同值。

触发维度（§3 ③）：
- 评分阈值：combined_score ≥ trigger.score_threshold（默认 80）→ signal_type=score
- 信号类型：compute_strategy 输出中属于 trigger.signal_types 的信号原样映射
- MACD 金叉死叉：compute_strategy 不产生 macd 类信号（既有代码事实），
  此处只读复用同一模块的纯函数 compute_macd 判定末根 bar DIF/DEA 穿越，
  映射为 macd_gold / macd_dead
- 涨跌幅异动：|实时涨跌幅| ≥ trigger.chg_pct_threshold（默认 3%）→ chg_spike
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from server.strategies import compute_macd, compute_strategy

from ..models import PANEL_SOURCE, Signal, now_local_iso
from .base import StrategyContext
from server.core.numbers import safe_float as _safe_float

logger = logging.getLogger(__name__)

# compute_strategy 可能产出、允许透传的信号类型（既有代码事实，§4.1 枚举子集）
_PASSTHROUGH_TYPES = {
    "momentum", "breakout", "volume_divergence",
    "ma_cross_gold", "ma_cross_dead",
    "ma_bullish", "pullback", "limit_up",
    "bottom_reversal", "dragon_head", "momentum_reversal",
}


class PanelSignalAdapter:
    """面板信号适配器（Track A）：永不下单，只发邮件提醒"""

    id = "panel"

    def evaluate(
        self,
        code: str,
        name: str,
        klines: List[Dict[str, Any]],
        ctx: StrategyContext,
        trigger: Optional[Dict[str, Any]] = None,
    ) -> List[Signal]:
        """评估单只标的，返回 0..N 个统一 Signal（source=panel）"""
        trigger = trigger or {}
        score_threshold = int(trigger.get("score_threshold", 80))
        signal_types = set(trigger.get("signal_types") or [])
        chg_threshold = float(trigger.get("chg_pct_threshold", 3.0))

        if not klines or len(klines) < 5:
            return []

        try:
            result = compute_strategy(klines)
        except Exception:
            logger.warning("panel_adapter: compute_strategy 失败 %s", code, exc_info=True)
            return []

        quote = ctx.quote or {}
        price = _safe_float(quote.get("price")) or _safe_float(klines[-1].get("c"))
        chg_pct = _safe_float(quote.get("chg"))
        ts = now_local_iso()
        out: List[Signal] = []

        def _mk(signal_type: str, score: Optional[int], detail: Dict[str, Any]) -> Signal:
            return Signal(
                code=code, name=name or code, signal_type=signal_type,
                source=PANEL_SOURCE, price=round(price, 3), chg_pct=round(chg_pct, 2),
                score=score, detail=detail, ts=ts,
            )

        # ① 评分阈值
        if result.combined_score >= score_threshold:
            out.append(_mk("score", result.combined_score, {
                "label": "高评分",
                "desc": f"综合评分{result.combined_score}≥{score_threshold}",
                "total_score": result.total_score,
            }))

        # ② 面板信号透传（按配置的 signal_types 过滤）
        for sig in result.signals:
            if sig.type in signal_types and sig.type in _PASSTHROUGH_TYPES:
                out.append(_mk(sig.type, sig.score, {"label": sig.label, "desc": sig.desc}))

        # ③ MACD 金叉/死叉（末根 bar DIF/DEA 穿越）
        if "macd_gold" in signal_types or "macd_dead" in signal_types:
            macd_type = _detect_macd_cross(klines)
            if macd_type and macd_type in signal_types:
                label = "MACD金叉" if macd_type == "macd_gold" else "MACD死叉"
                out.append(_mk(macd_type, None, {"label": label, "desc": f"DIF{'上' if macd_type == 'macd_gold' else '下'}穿DEA"}))

        # ④ 涨跌幅异动（需实时快照）
        if ctx.quote and abs(chg_pct) >= chg_threshold:
            out.append(_mk("chg_spike", None, {
                "label": "涨跌幅异动",
                "desc": f"涨跌幅{chg_pct:+.2f}%（阈值±{chg_threshold}%）",
            }))

        return out


def _detect_macd_cross(klines: List[Dict[str, Any]]) -> Optional[str]:
    """末根 bar MACD DIF/DEA 穿越判定 → "macd_gold" / "macd_dead" / None"""
    try:
        macd = compute_macd(klines)
        dif, dea = macd.get("dif") or [], macd.get("dea") or []
        if len(dif) < 2 or len(dea) < 2:
            return None
        if dif[-2] <= dea[-2] and dif[-1] > dea[-1]:
            return "macd_gold"
        if dif[-2] >= dea[-2] and dif[-1] < dea[-1]:
            return "macd_dead"
    except Exception:
        logger.warning("panel_adapter: compute_macd 失败", exc_info=True)
    return None


