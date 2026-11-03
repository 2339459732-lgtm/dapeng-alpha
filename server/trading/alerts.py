"""自动交易核心包 - 邮件服务（M2）

架构蓝图 §8：
- 标准库 smtplib.SMTP_SSL("smtp.qq.com", 465) + email.mime；发送放 asyncio.to_thread
- 凭证 QQ_EMAIL_ACCOUNT / QQ_SMTP_AUTH_CODE 只从 os.environ 读；
  代码/配置/数据库/日志中绝不出现授权码
- 缺失时 notify 降级"仅记录不发送"（log-only）+ gate 端点 smtp.ok=false
- 同窗聚合：同轮同标的多信号合并一封；限流：单标的 ≤5 封/日、全局 ≤20 封/小时，
  超限进摘要队列，窗口结束后合并发摘要邮件；计数器存 config 表 email_counters
- 失败指数退避重试 1s→4s→16s 共 3 次；仍败：push_status=failed + audit mail_failed
  + risk_state.smtp_alert（状态灯红色告警）
"""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple

from .models import PushStatus, Signal, now_local_iso
from .store import TradingStore

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465
_RETRY_DELAYS = (1, 4, 16)  # 指数退避重试 ×3

_PANEL_URL = "http://localhost:8900/dashboard.html"
_COMPLIANCE = "本邮件仅为信号提示，供参考，不构成投资建议；据此操作风险自负。"


class EmailAlerter:
    """把 Signal 变成邮件：模板渲染、双限流、同窗聚合、退避重试、失败告警标记"""

    def __init__(self, store: TradingStore):
        self._store = store
        self._counters: Dict[str, Any] = {}
        self._digest_queue: List[Dict[str, Any]] = []  # 超限信号待摘要
        self._last_error: Optional[str] = None
        self._loaded = False

    # --------------------------------------------------------
    # 凭证与状态
    # --------------------------------------------------------
    @staticmethod
    def _credentials() -> Tuple[Optional[str], Optional[str]]:
        """只从环境变量读凭证；授权码不落库/不入日志"""
        account = os.environ.get("QQ_EMAIL_ACCOUNT") or None
        auth_code = os.environ.get("QQ_SMTP_AUTH_CODE") or None
        return account, auth_code

    @staticmethod
    def _resolve_recipient(to: Optional[str]) -> Optional[str]:
        """收件人回退：未配置 email.recipient 时默认发给发件账户自己（QQ 自发自收，
        避免"凭证齐全但收件人为空"导致通知静默丢失）"""
        if to and str(to).strip():
            return str(to).strip()
        account, _ = EmailAlerter._credentials()
        return account

    @property
    def configured(self) -> bool:
        account, auth_code = self._credentials()
        return bool(account and auth_code)

    def smtp_state(self) -> Dict[str, Any]:
        """gate 端点 smtp 状态：ok = 凭证齐备且最近一次发送无失败"""
        return {
            "ok": self.configured and self._last_error is None,
            "configured": self.configured,
            "last_error": self._last_error,
        }

    async def _ensure_counters(self) -> None:
        if self._loaded:
            return
        self._counters = await self._store.kv_get("email_counters", default={}) or {}
        self._loaded = True

    async def _save_counters(self) -> None:
        await self._store.kv_set("email_counters", self._counters)

    # --------------------------------------------------------
    # 限流（单标的 5/日，全局 20/时；重启可恢复）
    # --------------------------------------------------------
    def _rate_check(self, code: str, per_stock_per_day: int, global_per_hour: int) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        hour = datetime.now().strftime("%Y-%m-%dT%H")
        if self._counters.get("day") != today:
            self._counters["day"] = today
            self._counters["per_stock"] = {}
        if self._counters.get("hour") != hour:
            self._counters["hour"] = hour
            self._counters["global_count"] = 0
        per_stock = self._counters.setdefault("per_stock", {})
        if int(per_stock.get(code, 0)) >= per_stock_per_day:
            return False
        if int(self._counters.get("global_count", 0)) >= global_per_hour:
            return False
        return True

    def _rate_consume(self, code: str, n: int = 1) -> None:
        per_stock = self._counters.setdefault("per_stock", {})
        per_stock[code] = int(per_stock.get(code, 0)) + n
        self._counters["global_count"] = int(self._counters.get("global_count", 0)) + n

    # --------------------------------------------------------
    # 对外：信号邮件（同窗聚合入口，monitor 每轮按 code 分组调用）
    # --------------------------------------------------------
    async def send_signal_batch(
        self,
        code: str,
        name: str,
        signals: List[Signal],
        recipient: str,
        per_stock_per_day: int = 5,
        global_per_hour: int = 20,
    ) -> str:
        """同一扫描窗内同标的多信号合并一封（§8 同窗聚合）。

        返回该批次推送状态：sent / disabled / rate_limited / failed。
        调用方（monitor）据此回写 signals.push_status。
        """
        if not signals:
            return PushStatus.PENDING.value
        await self._ensure_counters()

        # 降级：凭证缺失 → 仅落库不发送（§11-2），不抛异常
        if not self.configured:
            logger.info("邮件通道未配置（QQ_EMAIL_ACCOUNT/QQ_SMTP_AUTH_CODE），"
                        "信号仅落库：%s %s ×%d", code, name, len(signals))
            return PushStatus.DISABLED.value

        recipient = self._resolve_recipient(recipient)
        if not recipient:
            logger.info("未配置收件邮箱，信号仅落库：%s %s ×%d", code, name, len(signals))
            return PushStatus.DISABLED.value

        # 双限流：超限进摘要队列
        if not self._rate_check(code, per_stock_per_day, global_per_hour):
            for sig in signals:
                self._digest_queue.append({
                    "code": code, "name": name, "ts": sig.ts,
                    "signal_type": sig.signal_type, "price": sig.price,
                    "chg_pct": sig.chg_pct, "detail": sig.detail,
                })
            logger.info("限流：%s %s ×%d 转入摘要队列（积压 %d）",
                        code, name, len(signals), len(self._digest_queue))
            await self._save_counters()
            return PushStatus.RATE_LIMITED.value

        subject, body = self._render_signal_mail(code, name, signals)
        ok = await self._send_with_retry(recipient, subject, body)
        if ok:
            self._rate_consume(code, 1)
            await self._save_counters()
            await self._store.audit("monitor", "mail_sent", {
                "to": recipient, "code": code, "count": len(signals),
            })
            return PushStatus.SENT.value

        await self._store.audit("monitor", "mail_failed", {
            "to": recipient, "code": code, "count": len(signals),
            "error": self._last_error,
        }, result="error")
        await self._mark_smtp_alert(self._last_error)
        return PushStatus.FAILED.value

    # --------------------------------------------------------
    # 摘要邮件（限流积压 + 可选盘前摘要）
    # --------------------------------------------------------
    async def flush_digest(self, recipient: str, reason: str = "窗口结束") -> bool:
        """超限积压信号合并发一封摘要邮件"""
        await self._ensure_counters()
        if not self._digest_queue:
            return True
        if not self.configured or not recipient:
            # 降级模式：清空队列即可（信号均已落库）
            self._digest_queue.clear()
            return True

        recipient = self._resolve_recipient(recipient)
        if not recipient:
            self._digest_queue.clear()
            return True

        items = self._digest_queue[:]
        lines = [f"【信号提示】摘要（{reason}），共 {len(items)} 条限流信号：", ""]
        for it in items:
            label = (it.get("detail") or {}).get("label", it.get("signal_type", ""))
            lines.append(
                f"- {it['ts']} {it['name']}({it['code']}) {label} "
                f"价格 {it.get('price')} 涨跌幅 {it.get('chg_pct')}%"
            )
        lines += ["", f"面板查看：{_PANEL_URL}", "", _COMPLIANCE]
        ok = await self._send_with_retry(recipient, f"【信号提示】限流摘要 {len(items)} 条", "\n".join(lines))
        if ok:
            self._digest_queue.clear()
            await self._store.audit("monitor", "mail_sent", {
                "to": recipient, "digest": True, "count": len(items),
            })
        else:
            await self._mark_smtp_alert(self._last_error)
        return ok

    # --------------------------------------------------------
    # 通用纯文本邮件（熔断告警 / 实盘开关 / 复位通知等）
    # --------------------------------------------------------
    async def send_plain(self, to: str, subject: str, body: str) -> bool:
        """系统通知邮件；降级模式下仅记录日志，返回 False 但不抛异常"""
        to = self._resolve_recipient(to)
        if not self.configured or not to:
            logger.info("邮件通道不可用，通知仅记录：%s", subject)
            return False
        ok = await self._send_with_retry(to, subject, f"{body}\n\n{_COMPLIANCE}")
        if not ok:
            await self._mark_smtp_alert(self._last_error)
        return ok

    async def send_test_email(self, to: str) -> Tuple[bool, str]:
        """测试邮件（§5.12）：供用户验证授权码；返回 (sent, message)"""
        if not self.configured:
            return False, "未配置 QQ_EMAIL_ACCOUNT / QQ_SMTP_AUTH_CODE 环境变量"
        if not to:
            return False, "缺少收件邮箱"
        body = (f"这是一封自动化交易系统的测试邮件。\n"
                f"发送时间：{now_local_iso()}\n\n{_COMPLIANCE}")
        ok = await self._send_with_retry(to, "【信号提示】测试邮件", body)
        if ok:
            await self._store.audit("api", "mail_sent", {"to": to, "test": True})
            return True, "发送成功"
        return False, f"SMTP 发送失败：{self._last_error}"

    # --------------------------------------------------------
    # 渲染与发送
    # --------------------------------------------------------
    def _render_signal_mail(self, code: str, name: str, signals: List[Signal]) -> Tuple[str, str]:
        """模板渲染（§8）：主题 【信号提示】贵州茅台 触发3个信号"""
        display = name or code
        if len(signals) == 1:
            label = (signals[0].detail or {}).get("label", signals[0].signal_type)
            subject = f"【信号提示】{display} {label}"
        else:
            subject = f"【信号提示】{display} 触发{len(signals)}个信号"

        lines = [f"标的：{display}（{code}）", ""]
        for sig in signals:
            label = (sig.detail or {}).get("label", sig.signal_type)
            desc = (sig.detail or {}).get("desc", "")
            score_txt = f" 评分 {sig.score}" if sig.score is not None else ""
            lines.append(f"◆ {label}{score_txt}")
            lines.append(f"  当前价：{sig.price}  涨跌幅：{sig.chg_pct:+.2f}%")
            lines.append(f"  触发时间：{sig.ts}")
            if desc:
                lines.append(f"  说明：{desc}")
            lines.append("")
        lines.append(f"面板查看：{_PANEL_URL}")
        lines.append("")
        lines.append(_COMPLIANCE)
        return subject, "\n".join(lines)

    async def _send_with_retry(self, to: str, subject: str, body: str) -> bool:
        """指数退避重试 1s→4s→16s 共 3 次；仍失败置 last_error"""
        for attempt, delay in enumerate([0, *_RETRY_DELAYS]):
            if delay:
                await asyncio.sleep(delay)
            try:
                await asyncio.to_thread(self._send_smtp_sync, to, subject, body)
                self._last_error = None
                return True
            except Exception as e:  # SMTP 异常文案不含授权码
                self._last_error = f"{type(e).__name__}: {str(e)[:200]}"
                logger.warning("邮件发送失败（第 %d 次）：%s", attempt + 1, self._last_error)
        return False

    def _send_smtp_sync(self, to: str, subject: str, body: str) -> None:
        """同步 SMTP 发送（在 asyncio.to_thread 中运行）"""
        account, auth_code = self._credentials()
        if not account or not auth_code:
            raise RuntimeError("SMTP 凭证未配置")
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = account
        msg["To"] = to
        msg["Subject"] = Header(subject, "utf-8")
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.login(account, auth_code)
            smtp.sendmail(account, [to], msg.as_string())

    async def _mark_smtp_alert(self, error: Optional[str]) -> None:
        """失败告警标记：risk_state.smtp_alert（状态灯红色告警）"""
        risk_state = await self._store.kv_get("risk_state", default={}) or {}
        risk_state["smtp_alert"] = error or "SMTP 发送失败"
        await self._store.kv_set("risk_state", risk_state)
