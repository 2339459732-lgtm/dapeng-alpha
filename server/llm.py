"""DAPENG Alpha v4.0 - DeepSeek V4 Pro LLM Client

使用 Python 内置 urllib.request，零外部依赖。
通过 asyncio.to_thread 实现异步调用。
v4.0 优化：指数退避重试 + 连接复用 + 日志脱敏
"""

import json
import logging
import re
import time
import urllib.request
import urllib.error
import asyncio
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

from server.config import AGENT_CONFIG


class LLMClient:
    """DeepSeek V4 Pro LLM Client — 零外部依赖版本"""

    def __init__(self):
        self.api_key = AGENT_CONFIG.get("deepseek_api_key", "")
        self.base_url = AGENT_CONFIG.get("deepseek_base_url", "https://api.deepseek.com/v1")
        self.model = AGENT_CONFIG.get("deepseek_model", "deepseek-chat")
        self.timeout = AGENT_CONFIG.get("deepseek_timeout", 30)

    def _chat_sync(self, prompt: str, system_prompt: Optional[str] = None, max_tokens: int = 1000, retries: int = 3) -> Optional[str]:
        """同步 HTTP 调用 DeepSeek API（在线程池中执行）

        指数退避重试：1s → 2s → 4s
        """
        if not self.api_key:
            logger.warning("DeepSeek API Key 未配置")
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "top_p": 0.9,
        }).encode("utf-8")

        url = f"{self.base_url}/chat/completions"

        last_error = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
                    result = json.loads(body)
                    if "choices" in result and len(result["choices"]) > 0:
                        content = result["choices"][0]["message"]["content"]
                        logger.info("DeepSeek API 成功, 生成 %d 字符 (model=%s)", len(content), self.model)
                        return content
                    else:
                        logger.error("DeepSeek API 返回格式异常: %s", body[:200])
                        return None

            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
                last_error = f"HTTP {e.code}: {error_body[:200]}"
                if e.code == 429:  # Rate limit → 退避重试
                    wait = 2 ** attempt
                    logger.warning("DeepSeek API 限流, %ds后重试 (%d/%d)", wait, attempt + 1, retries)
                    time.sleep(wait)
                    continue
                elif e.code >= 500:  # 服务器错误 → 重试
                    wait = 2 ** attempt
                    logger.warning("DeepSeek API 服务端错误 %d, %ds后重试", e.code, wait)
                    time.sleep(wait)
                    continue
                else:
                    break  # 4xx 不重试

            except urllib.error.URLError as e:
                last_error = f"网络错误: {str(e)}"
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.warning("DeepSeek API 网络错误, %ds后重试 (%d/%d)", wait, attempt + 1, retries)
                    time.sleep(wait)
                    continue
            except TimeoutError:
                last_error = f"超时 ({self.timeout}s)"
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.warning("DeepSeek API 超时, %ds后重试", wait)
                    time.sleep(wait)
                    continue
            except Exception as e:
                last_error = str(e)
                break

        logger.error("DeepSeek API 调用失败(已重试%d次): %s", retries, last_error)
        return None

    async def chat(self, prompt: str, system_prompt: Optional[str] = None, max_tokens: int = 1000) -> Optional[str]:
        """异步调用 DeepSeek API"""
        return await asyncio.to_thread(self._chat_sync, prompt, system_prompt, max_tokens)

    async def generate_stock_analysis(
        self,
        code: str,
        name: str,
        current_price: float,
        change_pct: float,
        volume: float,
        ma5: float,
        ma10: float,
        ma20: float,
        macd_dif: float,
        macd_dea: float,
        macd_hist: float,
        rsi: float,
        kdj_k: float,
        kdj_d: float,
        kdj_j: float,
        strategy_score: int,
        signals: list,
        # v4.1 新增上下文
        boll_upper: float = 0,
        boll_mid: float = 0,
        boll_lower: float = 0,
        boll_position: float = 0.5,
        multi_timeframe: str = "中性",
        max_drawdown: float = 0,
        volatility: float = 0,
        volume_detail: str = "正常",
        atr14: float = 0,
        trend_desc: str = "",
        action_suggest: str = "观望",
        risk_factors: list = None,
    ) -> Optional[Dict[str, Any]]:
        """生成 DeepSeek V4 Pro 深度股票分析报告 — 作为最终决策者（v4.1增强版）"""

        signals_desc = "; ".join([s.get("label", "") for s in signals if isinstance(s, dict)])
        risk_desc = "; ".join(risk_factors) if risk_factors else "无特殊风险"

        prompt = f"""你是 DAPENG Alpha 量化系统的首席 AI 交易决策官（Chief AI Trading Officer）。
你拥有20年A股实战经验，擅长技术分析与量化策略的综合研判。
基于以下多维度指标和数据，做出最终的交易决策。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 股票信息
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 代码: {code}
- 名称: {name}
- 当前价: {current_price:.2f} 元
- 涨跌幅: {change_pct:+.2f}%
- 成交量: {volume:.0f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 趋势与均线
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- MA5: {ma5:.2f}, MA10: {ma10:.2f}, MA20: {ma20:.2f}
- 趋势描述: {trend_desc}
- 多时间框架: {multi_timeframe}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔧 动能指标
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- MACD DIF: {macd_dif:.4f}, DEA: {macd_dea:.4f}, 柱状: {macd_hist:.4f}
- RSI(14): {rsi:.1f}
- KDJ K: {kdj_k:.1f}, D: {kdj_d:.1f}, J: {kdj_j:.1f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📉 波动与通道
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 布林带 上轨: {boll_upper:.2f}, 中轨: {boll_mid:.2f}, 下轨: {boll_lower:.2f}
- 价格在布林带位置: {boll_position * 100:.0f}%（0%下轨, 100%上轨）
- ATR(14): {atr14:.4f}
- 年化波动率: {volatility:.1f}%
- 最大回撤: {max_drawdown:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 量化系统信号
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 综合策略评分: {strategy_score}/100
- 量化信号: {signals_desc if signals_desc else "无"}
- 量价关系: {volume_detail}
- 量化系统建议: {action_suggest}
- 风险因素: {risk_desc}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ 决策要求（严格遵守）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 当前价格 {current_price:.2f} 元是基准，所有价格字段基于此计算
2. entry_price.low 约当前价下方1-3%（保守买点）
3. entry_price.high 约当前价上方0-2%（追涨买点）
4. stop_loss 约当前价下方3-7%（根据ATR调整，ATR大则止损宽）
5. take_profit.first 约当前价上方5-10%（第一目标）
6. take_profit.second 约当前价上方10-20%（第二目标）
7. position_pct 建议仓位 0-100（根据评分和风险综合判断）
8. confidence 置信度 0-100（根据信号共振程度判断）
9. key_reason 必须引用具体数值（如"RSI=65未超买+MA金叉+评分78"）
10. risk_alert 必须指出最关键的1-2个风险点

请仅输出严格JSON，不要任何其他文字，不要markdown代码块标记：

{{
  "decision": "买入/持有/卖出/观望 四选一",
  "confidence": 85,
  "summary": "80字以内综合研判，包含趋势+信号+建议",
  "trend_judgment": "强势上涨/震荡偏多/横盘整理/震荡偏空/弱势下跌 五选一",
  "entry_price": {{"low": {current_price * 0.98:.2f}, "high": {current_price * 1.01:.2f}}},
  "stop_loss": {current_price * 0.94:.2f},
  "take_profit": {{"first": {current_price * 1.08:.2f}, "second": {current_price * 1.15:.2f}}},
  "position_pct": 30,
  "risk_alert": "最关键的风险提示（30字以内）",
  "timing": "短线/中线/长线 三选一",
  "key_reason": "决策核心理由，引用具体指标数值（40字以内）"
}}"""

        system_prompt = """你是 DAPENG Alpha 量化系统首席 AI 交易决策官。

## 核心职责
综合多维量化指标，做出高胜率交易决策。你不是简单的指标读数器，而是经验丰富的交易决策者。

## 决策原则
1. 量化评分为主，AI判断为辅：评分≥75可积极，≥60可参与，<40应回避
2. 技术共振优先：多指标同向信号 > 单一指标信号
3. 风险控制第一：宁可错过，不做错
4. 顺势而为：不逆大趋势操作
5. 仓位管理：高确定性可重仓(≥60%)，中等确定性轻仓(20-40%)，低确定性观望(≤10%)

## 输出规范
- 仅输出JSON，禁止任何额外文字
- 所有价格必须精确到两位小数
- 买入/卖出决策必须有明确的技术依据
- 观望也是一种有效决策"""

        try:
            result = await self.chat(prompt, system_prompt, max_tokens=600)
            if not result:
                return None

            json_match = re.search(r'\{[\s\S]*\}', result)
            if json_match:
                json_str = json_match.group(0)
                data = json.loads(json_str)
                logger.info("DeepSeek 决策完成: %s → %s (置信度 %s%%)", code, data.get("decision"), data.get("confidence"))
                return data
            else:
                logger.warning("LLM 返回无法解析为 JSON")
                return None

        except json.JSONDecodeError as e:
            logger.error("LLM JSON 解析失败: %s", str(e))
            return None
        except Exception as e:
            logger.error("DeepSeek 决策生成失败: %s", str(e))
            return None


_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取全局 LLM 客户端实例"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
