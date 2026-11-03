# -*- coding: utf-8 -*-
"""从运行中服务的 /openapi.json 生成 docs/40-ops/API-REFERENCE.md

用法（项目根目录）：
    curl -s --noproxy '*' http://127.0.0.1:8900/openapi.json -o artifacts/openapi.json
    python tools/gen_api_doc.py

设计原则：文档由真实路由生成，不手工维护 —— 代码变了重跑一次即可，不会脱节。
"""
import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "artifacts" / "openapi.json"
OUT = ROOT / "docs" / "40-ops" / "API-REFERENCE.md"

GROUPS = [
    ("实时推送 (SSE)", ["stream"]),
    ("行情与 K 线", ["quotes", "kline", "multi-period-resonance", "realtime", "debug-kline", "chart-analysis"]),
    ("市场大盘与板块", ["market-dashboard", "market", "market-overview", "market-sentiment", "hot-stocks",
                        "lhb", "sector-ranking", "sector-map", "sector-linkage", "sector-graph",
                        "sector-clusters", "sector-stocks", "sectors", "fund-flow", "north-holding"]),
    ("选股与筛选", ["screen", "scan", "search", "strategy-lab", "stocklist"]),
    ("智能分析与全球市场", ["intelligent", "global", "news", "finance", "report", "consensus",
                            "profile", "analyze", "agents"]),
    ("自动化交易", ["automation"]),
    ("复盘与策略迭代", ["review", "evolution"]),
    ("AI 主脑", ["brain"]),
    ("回测与量化任务", ["quant"]),
    ("预警与监控", ["alerts"]),
    ("后台管理", ["admin"]),
    ("支付", ["payment"]),
    ("系统", ["health"]),
]

AUTO_EN = re.compile(r"^([A-Z][a-z]+|[A-Z]{2,})( [A-Za-z]+){0,4}$")


def group_of(path: str) -> str:
    m = re.match(r"^/api/([a-z0-9\-]+)", path)
    key = m.group(1) if m else ""
    for name, keys in GROUPS:
        if key in keys:
            return name
    return "其他"


def main() -> None:
    spec = json.loads(SRC.read_text(encoding="utf-8"))
    paths = spec["paths"]
    buckets = collections.OrderedDict((g, []) for g, _ in GROUPS)
    buckets["其他"] = []

    for p in sorted(paths):
        for method in ("get", "post", "put", "delete", "patch"):
            op = paths[p].get(method)
            if not op:
                continue
            desc = (op.get("description") or "").strip()
            summary = (op.get("summary") or "").strip()
            text = ""
            if desc:
                for line in desc.split("\n"):
                    if line.strip():
                        text = line.strip()
                        break
            if not text:
                text = summary if (summary and not AUTO_EN.match(summary)) else "—"
            text = text.rstrip("。")
            params = op.get("parameters") or []
            q = [x["name"] for x in params if x.get("in") == "query"]
            pnote = (", ".join(q[:6]) + ("…" if len(q) > 6 else "")) if q else ""
            if op.get("requestBody"):
                pnote = (pnote + " + " if pnote else "") + "body"
            buckets[group_of(p)].append((method.upper(), p, text, pnote))

    total = sum(len(v) for v in buckets.values())
    L = ["# DAPENG Alpha 接口参考\n\n",
         "> 本文件由**运行中服务**的 `/openapi.json` 直接生成，不手工维护，因此不会与代码脱节。重新生成：\n",
         "> ```bash\n> curl -s --noproxy '*' http://127.0.0.1:8900/openapi.json -o artifacts/openapi.json\n"
         "> python tools/gen_api_doc.py\n> ```\n\n",
         f"**端点总数** {total} · **路径数** {len(paths)} · "
         f"**服务版本** {spec.get('info', {}).get('version', '-')}\n\n",
         "## 一、通用约定\n\n",
         '所有业务接口统一返回**三键信封**：\n\n```json\n{ "ok": true, "data": { ... }, "message": null }\n```\n\n',
         "| 约定 | 说明 |\n|---|---|\n",
         "| 失败形态 | `ok=false`、`data=null`、`message` 为可直接展示的中文原因 |\n",
         "| 数据红线 | 取不到就是 `null` + `reason`，**绝不用 `0` 冒充真实值** |\n",
         "| 降级自证 | 响应带 `degraded: true` 与 `reason` / `source`，前端显示来源徽章 |\n",
         "| 真实数据优先 | 真实 → 缓存 → 多源降级 → 如实报不可用；不允许编造 |\n",
         "| 本地调试 | `curl` 必加 `--noproxy '*'`，否则被系统代理劫持成 502 |\n",
         "| 涨跌配色 | 涨=红、跌=绿（A 股惯例）|\n",
         "\n## 二、端点总览\n\n| 分组 | 端点数 |\n|---|---:|\n"]
    for g, items in buckets.items():
        if items:
            L.append(f"| {g} | {len(items)} |\n")
    L.append("\n")
    for g, items in buckets.items():
        if not items:
            continue
        L.append(f"\n## {g}\n\n| 方法 | 路径 | 说明 | 主要参数 |\n|---|---|---|---|\n")
        for method, p, text, pnote in items:
            s = text.replace("|", "\\|").replace("\n", " ")
            if len(s) > 100:
                s = s[:98] + "…"
            L.append(f"| `{method}` | `{p}` | {s} | {pnote} |\n")
        L.append("\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(L), encoding="utf-8")
    print(f"written {OUT}  endpoints={total}")


if __name__ == "__main__":
    main()
