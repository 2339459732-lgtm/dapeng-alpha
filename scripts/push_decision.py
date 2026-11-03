# -*- coding: utf-8 -*-
"""把决策台摘要推送到手机（邮件，走 .env 里的 QQ SMTP）。

用法：
    python scripts/push_decision.py            # 只打印，不发邮件
    python scripts/push_decision.py --send     # 发送到配置的邮箱（默认发给自己）
    python scripts/push_decision.py --send --to someone@example.com
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
import urllib.request
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path

BASE = "http://127.0.0.1:8900"
ROOT = Path(__file__).resolve().parent.parent


def load_env() -> dict:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items() if k.startswith(("QQ_", "SMTP_"))})
    return env


def get(path: str) -> dict:
    try:
        with urllib.request.urlopen(BASE + path, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def pct(v, digits=2):
    try:
        n = float(v)
        return f"{n:+.{digits}f}%"
    except Exception:
        return "--"


def amt(v):
    try:
        n = float(v)
    except Exception:
        return "--"
    if abs(n) >= 1e12:
        return f"{n/1e12:.2f} 万亿"
    if abs(n) >= 1e8:
        return f"{n/1e8:.2f} 亿"
    if abs(n) >= 1e4:
        return f"{n/1e4:.1f} 万"
    return f"{n:.0f}"


def build_text() -> str:
    md = get("/api/market-dashboard")
    th = get("/api/themes/overview")
    intel = get("/api/brain/intel")
    review = get("/api/review/summary")
    d = md.get("data") if md.get("ok") else None
    lines = []
    lines.append("【DAPENG 决策推送】")
    if d:
        j = d.get("judgment") or {}
        score = j.get("score")
        risk = None if score is None else round(100 - float(score))
        stance = "防守" if risk is not None and risk >= 62 else ("进攻" if risk is not None and risk <= 38 else "均衡")
        b = d.get("breadth") or {}
        lines.append(f"结论：{stance}（风险指数 {risk if risk is not None else '--'}/100，市场评分 {score if score is not None else '--'}）")
        lines.append(f"市场：上涨 {b.get('up', '--')} / 下跌 {b.get('down', '--')}，涨停 {b.get('limit_up', '--')} / 跌停 {b.get('limit_down', '--')}，成交额 {amt((d.get('turnover') or {}).get('amount'))}")
        lines.append(f"建议仓位上限：≤ {30 if risk is not None and risk >= 62 else (70 if risk is not None and risk <= 38 else 50)}%")
        if j.get("text"):
            lines.append("解读：" + j["text"])
    if intel.get("ok") and intel.get("latest"):
        it = intel["latest"]
        lines.append(f"情报：风险 {it.get('risk_level', '--')} / {it.get('risk_score', '--')}")
        if it.get("brief"):
            lines.append("要点：" + it["brief"][:120])
    if th.get("ok"):
        zodiac = th.get("zodiac") or []
        if zodiac:
            lines.append("字辈：" + " ".join(f"{g['char']}×{g['count']}" for g in zodiac[:6]))
        sectors = (th.get("sectors") or [])[:4]
        if sectors:
            lines.append("板块：" + "、".join(f"{s['name']}{pct(s.get('avg_chg'))}" for s in sectors))
    if review.get("ok") and review.get("data"):
        eq = review["data"].get("equity") or {}
        lines.append(f"账户：{amt(eq.get('now'))}（区间 {pct(eq.get('total_return_pct'))}）")
    lines.append("完整面板：http://127.0.0.1:8900/mobile.html（手机需能访问电脑）")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--to")
    args = ap.parse_args()
    text = build_text()
    print(text)
    if not args.send:
        print("\n（dry-run，未发送；加 --send 发送邮件）")
        return 0
    env = load_env()
    account = env.get("QQ_EMAIL_ACCOUNT")
    auth = env.get("QQ_SMTP_AUTH_CODE")
    to = args.to or env.get("PUSH_TO") or account
    if not account or not auth or not to:
        print("缺少 QQ_EMAIL_ACCOUNT / QQ_SMTP_AUTH_CODE", file=sys.stderr)
        return 2
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = Header("DAPENG 决策推送 · " + __import__("datetime").datetime.now().strftime("%m-%d %H:%M"), "utf-8")
    msg["From"] = account
    msg["To"] = to
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.qq.com", 465, context=ctx, timeout=25) as s:
        s.login(account, auth)
        s.sendmail(account, [to], msg.as_string())
    print("已发送到", to)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
