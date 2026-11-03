# -*- coding: utf-8 -*-
"""生成 static/api-reference.html —— 可搜索的接口目录（数据来自运行中服务的 openapi.json）

用法（项目根目录）：
    curl -s --noproxy '*' http://127.0.0.1:8900/openapi.json -o artifacts/openapi.json
    python tools/gen_api_page.py

产物自包含（数据内嵌），不依赖任何外部 CDN；样式复用 static/css/dashboard.css 的设计变量。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "artifacts" / "openapi.json"
OUT = ROOT / "static" / "api-reference.html"

GROUPS = [
    ("实时推送", ["stream"]),
    ("行情与K线", ["quotes", "kline", "multi-period-resonance", "realtime", "debug-kline", "chart-analysis"]),
    ("市场与板块", ["market-dashboard", "market", "market-overview", "market-sentiment", "hot-stocks",
                    "lhb", "sector-ranking", "sector-map", "sector-linkage", "sector-graph",
                    "sector-clusters", "sector-stocks", "sectors", "fund-flow", "north-holding"]),
    ("选股筛选", ["screen", "scan", "search", "strategy-lab", "stocklist"]),
    ("智能与全球", ["intelligent", "global", "news", "finance", "report", "consensus",
                    "profile", "analyze", "agents"]),
    ("自动化交易", ["automation"]),
    ("复盘迭代", ["review", "evolution"]),
    ("AI主脑", ["brain"]),
    ("回测量化", ["quant"]),
    ("预警监控", ["alerts"]),
    ("后台管理", ["admin"]),
    ("支付", ["payment"]),
    ("系统", ["health"]),
]

AUTO_EN = re.compile(r"^([A-Z][a-z]+|[A-Z]{2,})( [A-Za-z]+){0,4}$")


def group_of(path: str):
    m = re.match(r"^/api/([a-z0-9\-]+)", path)
    key = m.group(1) if m else ""
    for i, (name, keys) in enumerate(GROUPS):
        if key in keys:
            return name, i
    return "其他", len(GROUPS)


def build_rows(spec):
    rows = []
    for p in sorted(spec["paths"]):
        for method in ("get", "post", "put", "delete", "patch"):
            op = spec["paths"][p].get(method)
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
                text = summary if (summary and not AUTO_EN.match(summary)) else ""
            # HTML 页不渲染 Markdown：去掉反引号/加粗标记，避免出现 ``?topics=...`` 这种裸语法
            text = text.replace("`", "").replace("**", "").rstrip("。")
            params = [x["name"] for x in (op.get("parameters") or []) if x.get("in") == "query"]
            if op.get("requestBody"):
                params.append("body")
            gname, gidx = group_of(p)
            rows.append({
                "m": method.upper(),
                "p": p,
                "d": text,
                "q": params,
                "g": gname,
                "gi": gidx,
            })
    # 默认按「功能分组顺序 → 路径」排列，比纯字母序更符合阅读习惯
    rows.sort(key=lambda r: (r["gi"], r["p"], r["m"]))
    return rows


HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>接口目录 · DAPENG Alpha</title>
<link rel="stylesheet" href="css/dashboard.css?v=__CSSV__">
<style>
  body{margin:0;background:var(--bg);color:var(--text);font-family:var(--font-sans,-apple-system,'Segoe UI','Microsoft YaHei',sans-serif)}
  .api-wrap{max-width:1180px;margin:0 auto;padding:22px 20px 60px}
  .api-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px}
  .api-title{font-size:20px;font-weight:700;letter-spacing:.3px}
  .api-sub{color:var(--muted);font-size:12px;margin-bottom:18px;line-height:1.7}
  .api-sub code{background:rgba(148,163,184,.12);padding:1px 5px;border-radius:4px;font-size:11px}
  .api-back{margin-left:auto;font-size:12px;color:var(--cyan);text-decoration:none;border:1px solid rgba(6,182,212,.3);
    padding:5px 10px;border-radius:7px}
  .api-back:hover{background:rgba(6,182,212,.1)}
  .api-stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
  .api-stat{background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);border-radius:9px;
    padding:8px 14px;min-width:88px}
  .api-stat b{display:block;font-size:18px;font-family:'SF Mono','Cascadia Code',monospace;line-height:1.3}
  .api-stat span{font-size:10.5px;color:var(--muted)}
  .api-toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px;position:sticky;top:0;
    background:linear-gradient(180deg,var(--bg) 70%,transparent);padding:10px 0;z-index:5}
  .api-search{flex:1 1 280px;min-width:220px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.09);
    border-radius:8px;color:var(--text);font-size:13px;padding:8px 12px;outline:none}
  .api-search:focus{border-color:rgba(6,182,212,.45);background:rgba(6,182,212,.05)}
  .api-chips{display:flex;gap:6px;flex-wrap:wrap}
  .api-chip{font-size:11.5px;padding:5px 10px;border-radius:999px;cursor:pointer;user-select:none;
    background:rgba(148,163,184,.08);border:1px solid rgba(148,163,184,.18);color:var(--text2);transition:all .15s}
  .api-chip:hover{background:rgba(148,163,184,.14)}
  .api-chip.on{background:rgba(6,182,212,.14);border-color:rgba(6,182,212,.4);color:var(--cyan)}
  .api-chip em{font-style:normal;opacity:.6;font-size:10.5px;margin-left:3px}
  .api-count{font-size:11.5px;color:var(--muted);margin-left:auto;white-space:nowrap}
  table.api-table{width:100%;border-collapse:collapse;font-size:12.5px}
  table.api-table th{text-align:left;font-size:10.5px;font-weight:600;color:var(--muted);text-transform:uppercase;
    letter-spacing:.6px;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.08);position:sticky;top:56px;
    background:var(--bg);z-index:4}
  table.api-table td{padding:9px 10px;border-bottom:1px solid rgba(255,255,255,.045);vertical-align:top}
  table.api-table tr:hover td{background:rgba(6,182,212,.04)}
  .api-m{font-family:'SF Mono','Cascadia Code',monospace;font-size:10.5px;font-weight:700;padding:2px 7px;
    border-radius:5px;white-space:nowrap;display:inline-block;min-width:44px;text-align:center}
  .api-m.GET{background:rgba(6,182,212,.13);color:var(--cyan);border:1px solid rgba(6,182,212,.28)}
  .api-m.POST{background:rgba(245,158,11,.13);color:#f59e0b;border:1px solid rgba(245,158,11,.28)}
  .api-m.DELETE{background:rgba(148,163,184,.14);color:var(--text2);border:1px solid rgba(148,163,184,.25)}
  .api-m.PUT,.api-m.PATCH{background:rgba(139,92,246,.14);color:#a78bfa;border:1px solid rgba(139,92,246,.3)}
  .api-path{font-family:'SF Mono','Cascadia Code',monospace;font-size:12px;color:var(--text);word-break:break-all;cursor:pointer}
  .api-path:hover{color:var(--cyan)}
  .api-desc{color:var(--text2);line-height:1.55}
  .api-desc .none{color:var(--muted);opacity:.7}
  .api-q{font-family:'SF Mono','Cascadia Code',monospace;font-size:11px;color:var(--muted);word-break:break-all}
  .api-empty{text-align:center;color:var(--muted);padding:40px 0;font-size:13px}
  .api-legend{margin-top:26px;font-size:11.5px;color:var(--muted);line-height:1.9;
    border-top:1px solid rgba(255,255,255,.06);padding-top:14px}
  .api-legend b{color:var(--text2)}
  .api-toast{position:fixed;left:50%;bottom:28px;transform:translate(-50%,14px);background:rgba(6,182,212,.16);
    border:1px solid rgba(6,182,212,.4);color:var(--cyan);font-size:12px;padding:7px 14px;border-radius:8px;
    opacity:0;transition:all .2s;pointer-events:none;z-index:99}
  .api-toast.on{opacity:1;transform:translate(-50%,0)}
  @media(max-width:760px){
    table.api-table th:nth-child(4),table.api-table td:nth-child(4){display:none}
    .api-wrap{padding:16px 12px 40px}
  }
</style>
</head>
<body>
<div class="api-wrap">
  <div class="api-top">
    <div class="api-title">接口目录</div>
    <a class="api-back" href="/project-docs.html">📘 项目文档</a>
    <a class="api-back" href="/dashboard.html" style="margin-left:8px">◀ 返回看板</a>
  </div>
  <div class="api-sub">
    数据来自运行中服务的 <code>/openapi.json</code>，共 <b id="api-total">0</b> 个端点 / <b id="api-paths">0</b> 条路径 —— 由
    <code>tools/gen_api_page.py</code> 生成，不手工维护，因此不会与代码脱节。<br>
    统一三键信封 <code>{ ok, data, message }</code>；取不到值一律 <code>null</code> + <code>reason</code>，绝不用 <code>0</code> 冒充。
  </div>

  <div class="api-stats" id="api-stats"></div>

  <div class="api-toolbar">
    <input class="api-search" id="api-search" type="search" placeholder="搜索路径或说明（如 kline / 复盘 / 下单）…" autocomplete="off">
    <div class="api-chips" id="api-chips"></div>
    <div class="api-count" id="api-count"></div>
  </div>

  <table class="api-table">
    <thead><tr><th style="width:70px">方法</th><th style="width:38%">路径</th><th>说明</th><th style="width:22%">参数</th></tr></thead>
    <tbody id="api-body"></tbody>
  </table>
  <div class="api-empty" id="api-empty" style="display:none">没有匹配的接口，换个关键词试试</div>

  <div class="api-legend">
    <b>通用约定</b><br>
    · <b>GET</b> 查询 / <b>POST</b> 变更 / <b>DELETE</b> 撤销 · 点击路径可复制<br>
    · 降级响应带 <code>degraded:true</code> 与 <code>reason</code>，前端显示来源徽章；取不到数据不编造<br>
    · 本地调试：<code>curl --noproxy '*' http://127.0.0.1:__PORT__/api/health</code><br>
    · 涨=红、跌=绿；买入键红、卖出键绿
  </div>
</div>
<div class="api-toast" id="api-toast">已复制</div>

<script>
var API_ROWS = __DATA__;
(function () {
  var rows = API_ROWS, activeGroup = '全部', keyword = '';
  var body = document.getElementById('api-body');
  var chips = document.getElementById('api-chips');
  var countEl = document.getElementById('api-count');
  var emptyEl = document.getElementById('api-empty');
  var searchEl = document.getElementById('api-search');
  var toast = document.getElementById('api-toast');

  document.getElementById('api-total').textContent = rows.length;
  document.getElementById('api-paths').textContent = new Set(rows.map(function (r) { return r.p; })).size;

  // 分组 chip + 统计（按功能分组定义顺序，而非路径字母序）
  var groupMap = {};
  rows.forEach(function (r) { if (!groupMap[r.g]) groupMap[r.g] = r.gi; });
  var groups = Object.keys(groupMap).sort(function (a, b) { return groupMap[a] - groupMap[b]; });
  var stats = document.getElementById('api-stats');
  var methods = {};
  rows.forEach(function (r) { methods[r.m] = (methods[r.m] || 0) + 1; });
  var statHtml = '';
  Object.keys(methods).sort().forEach(function (m) {
    statHtml += '<div class="api-stat"><b>' + methods[m] + '</b><span>' + m + '</span></div>';
  });
  statHtml += '<div class="api-stat"><b>' + groups.length + '</b><span>功能分组</span></div>';
  stats.innerHTML = statHtml;

  function buildChips() {
    var html = '<div class="api-chip on" data-g="全部">全部<em>' + rows.length + '</em></div>';
    groups.forEach(function (g) {
      var n = rows.filter(function (r) { return r.g === g; }).length;
      html += '<div class="api-chip" data-g="' + g + '">' + g + '<em>' + n + '</em></div>';
    });
    chips.innerHTML = html;
    chips.querySelectorAll('.api-chip').forEach(function (el) {
      el.addEventListener('click', function () {
        chips.querySelectorAll('.api-chip').forEach(function (x) { x.classList.remove('on'); });
        el.classList.add('on');
        activeGroup = el.getAttribute('data-g');
        render();
      });
    });
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function render() {
    var kw = keyword.trim().toLowerCase();
    var list = rows.filter(function (r) {
      if (activeGroup !== '全部' && r.g !== activeGroup) return false;
      if (!kw) return true;
      return (r.p + ' ' + r.d + ' ' + r.q.join(' ')).toLowerCase().indexOf(kw) >= 0;
    });
    var html = list.map(function (r) {
      var q = r.q && r.q.length ? esc(r.q.join(', ')) : '<span style="opacity:.35">—</span>';
      var d = r.d ? esc(r.d) : '<span class="none">—（代码未提供描述）</span>';
      return '<tr><td><span class="api-m ' + r.m + '">' + r.m + '</span></td>'
        + '<td><span class="api-path" data-path="' + esc(r.p) + '" title="点击复制">' + esc(r.p) + '</span></td>'
        + '<td class="api-desc">' + d + '</td>'
        + '<td class="api-q">' + q + '</td></tr>';
    }).join('');
    body.innerHTML = html;
    emptyEl.style.display = list.length ? 'none' : '';
    countEl.textContent = '显示 ' + list.length + ' / ' + rows.length;
    body.querySelectorAll('.api-path').forEach(function (el) {
      el.addEventListener('click', function () {
        var p = el.getAttribute('data-path');
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(p).then(showToast, function () { });
        } else { showToast(); }
      });
    });
  }

  var t;
  function showToast() {
    toast.classList.add('on');
    clearTimeout(t);
    t = setTimeout(function () { toast.classList.remove('on'); }, 1200);
  }

  searchEl.addEventListener('input', function () { keyword = this.value; render(); });
  buildChips();
  render();
})();
</script>
</body>
</html>
"""


def main() -> None:
    spec = json.loads(SRC.read_text(encoding="utf-8"))
    rows = build_rows(spec)
    # 读取 dashboard.css 当前版本号，保持跨页一致
    cssv = "68"
    m = re.search(r"dashboard\.css\?v=(\d+)", (ROOT / "static" / "dashboard.html").read_text(encoding="utf-8"))
    if m:
        cssv = m.group(1)
    data = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = (HTML.replace("__DATA__", data)
                .replace("__CSSV__", cssv)
                .replace("__PORT__", "8900"))
    OUT.write_text(html, encoding="utf-8")
    print(f"written {OUT}  endpoints={len(rows)}  css_v={cssv}")


if __name__ == "__main__":
    main()
