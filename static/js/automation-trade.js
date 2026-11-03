/* ================================================================
 * 交易台（阶段三）- static/js/automation-trade.js
 * 独立于 automation.js（避免与 SSE 工程师并发编辑冲突）：
 *   - 手动下单（走 POST /api/automation/orders，回显成交价/费用明细 / 被拒关卡+reason）
 *   - 撤单（未成交可撤；已成交按钮置灰并提示原因）
 *   - 条件单（价格/时间触发：创建 / 列表 / 取消 / 是否已触发）
 *   - 决策质量面板（成交决策 vs 被拒决策，含"如果当时买了会怎样" outcome_ret_pct）
 *   - 订单来源标注（source 徽标 + decision_json 四维理由悬浮）
 * 约束：不改 app.js；window.appState 只读；文案一律"信号提示，供参考"。
 * 仅需 dashboard.html 提供容器 <div id="trade-desk-root"></div> 与本脚本一行引用。
 * ================================================================ */
(function () {
  'use strict';

  var API = '/api/automation';
  var CODE_RE = /^(sh|sz|hk)\d{5,6}$/;
  var state = { tab: 'manual', orders: [], conds: [], dq: [], lastResult: null, toast: null };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmt(n, d) {
    var x = Number(n);
    return isFinite(x) ? x.toFixed(d == null ? 2 : d) : '-';
  }
  function api(path, method, body) {
    var opt = { method: method || 'GET', headers: { 'Content-Type': 'application/json' } };
    if (body) opt.body = JSON.stringify(body);
    return fetch(API + path, opt).then(function (r) {
      return r.json().catch(function () { return { ok: false, message: 'HTTP ' + r.status }; });
    });
  }

  // ---------- 样式（自包含，注入一次）----------
  var CSS = ''
    + '#trade-desk-root{}'
    + '.td-fab{position:fixed;right:22px;bottom:80px;z-index:9998;background:#c0392b;color:#fff;border:0;'
    + 'border-radius:22px;padding:10px 16px;font-size:13px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.35)}'
    + '.td-overlay{position:fixed;inset:0;background:rgba(8,10,16,.72);z-index:9999;display:none}'
    + '.td-overlay.on{display:flex;align-items:center;justify-content:center}'
    + '.td-panel{width:min(940px,94vw);max-height:88vh;overflow:auto;background:#12161f;color:#dfe6f0;'
    + 'border:1px solid #2a3242;border-radius:12px;padding:16px 18px;font:13px/1.5 system-ui,"Microsoft YaHei",sans-serif}'
    + '.td-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}'
    + '.td-h b{font-size:15px}'
    + '.td-x{background:transparent;border:1px solid #444;color:#aaa;border-radius:6px;cursor:pointer;padding:3px 9px}'
    + '.td-tabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}'
    + '.td-tab{background:#1b2130;border:1px solid #2a3242;color:#9fb0c8;border-radius:6px;padding:6px 12px;cursor:pointer}'
    + '.td-tab.on{background:#c0392b;border-color:#c0392b;color:#fff}'
    + '.td-row{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-bottom:10px}'
    + '.td-row label{display:flex;flex-direction:column;gap:3px;color:#9fb0c8;font-size:12px}'
    + '.td-row input,.td-row select{background:#0d1117;border:1px solid #2a3242;color:#dfe6f0;border-radius:6px;padding:6px 8px;min-width:110px}'
    + '.td-btn{background:#2c6;border:0;color:#04210f;font-weight:600;border-radius:6px;padding:8px 14px;cursor:pointer}'
    + '.td-btn.ghost{background:#243048;color:#cbd6e8;font-weight:400}'
    + '.td-btn.danger{background:#7a2020;color:#ffd7d7;font-weight:400}'
    + '.td-btn[disabled]{opacity:.45;cursor:not-allowed}'
    + 'table.td{width:100%;border-collapse:collapse;font-size:12px}'
    + 'table.td th,table.td td{border-bottom:1px solid #232b3a;padding:6px 8px;text-align:left;white-space:nowrap}'
    + 'table.td th{color:#8ea2bd;font-weight:500}'
    + '.td-badge{display:inline-block;border-radius:4px;padding:1px 6px;font-size:11px}'
    + '.badge-manual{background:#2b4a7a;color:#cfe0ff}.badge-signal{background:#2f3a2b;color:#cfe8c0}'
    + '.badge-conditional{background:#4a3a2b;color:#ffd9b0}.badge-filled{background:#264a2c;color:#c7f0cf}'
    + '.badge-rejected{background:#4a2626;color:#ffc9c9}.badge-cancelled{background:#3a3a3a;color:#ccc}'
    + '.td-body{margin-top:6px}'
    + '.td-res{margin-top:10px;border:1px solid #2a3242;border-radius:8px;padding:10px;background:#0e131b}'
    + '.td-res.ok{border-color:#2c6}.td-res.bad{border-color:#a33}'
    + '.td-cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}'
    + '.td-col h4{margin:0 0 6px;color:#9fb0c8;font-weight:600}'
    + '.td-toast{position:fixed;right:22px;bottom:22px;z-index:10000;background:#1b2130;color:#e6edf7;'
    + 'border:1px solid #2a3242;border-radius:8px;padding:10px 14px;box-shadow:0 6px 18px rgba(0,0,0,.4)}'
    + '.td-toast.err{border-color:#a33}';

  function injectCss() {
    if (document.getElementById('td-style')) return;
    var s = document.createElement('style');
    s.id = 'td-style'; s.textContent = CSS;
    document.head.appendChild(s);
  }

  function toast(msg, isErr) {
    var root = host(); if (!root) return;
    var old = root.querySelector('.td-toast'); if (old) old.remove();
    var t = document.createElement('div');
    t.className = 'td-toast' + (isErr ? ' err' : '');
    t.textContent = msg;
    root.appendChild(t);
    setTimeout(function () { t.remove(); }, 4200);
  }

  function host() { return document.getElementById('trade-desk-root'); }

  function srcBadge(src) {
    var m = { manual: ['手动', 'badge-manual'], signal: ['信号', 'badge-signal'], conditional: ['条件单', 'badge-conditional'] };
    var b = m[src] || [src || '?', 'badge-cancelled'];
    return '<span class="td-badge ' + b[1] + '">' + esc(b[0]) + '</span>';
  }
  function statusBadge(st) {
    var m = { filled: 'badge-filled', rejected: 'badge-rejected', cancelled: 'badge-cancelled', partial: 'badge-conditional', partial_canceled: 'badge-cancelled', pending: 'badge-conditional' };
    return '<span class="td-badge ' + (m[st] || 'badge-cancelled') + '">' + esc(st) + '</span>';
  }
  function decisionTitle(dj) {
    if (!dj) return '';
    try {
      return '决策闸：allow=' + dj.allow + ' confidence=' + dj.confidence + '\n' +
        'verdict: ' + (dj.verdict_reason || '') + '\n\n' + JSON.stringify(dj.reasons, null, 1);
    } catch (e) { return ''; }
  }

  // ---------- 渲染 ----------
  function render() {
    var r = host(); if (!r) return;
    var tabs = [['manual', '手动下单'], ['orders', '订单与撤单'], ['cond', '条件单'], ['dq', '决策质量']];
    var html = ''
      + '<button class="td-fab" id="td-fab">交易台</button>'
      + '<div class="td-overlay" id="td-overlay"><div class="td-panel">'
      + '<div class="td-h"><b>交易台（信号提示，供参考）</b><button class="td-x" id="td-close">关闭 ✕</button></div>'
      + '<div class="td-tabs">' + tabs.map(function (t) {
        return '<button class="td-tab' + (state.tab === t[0] ? ' on' : '') + '" data-tab="' + t[0] + '">' + t[1] + '</button>';
      }).join('') + '</div>'
      + '<div class="td-body" id="td-body">' + bodyHtml() + '</div>'
      + '</div></div>';
    r.innerHTML = html;
    bind();
  }

  function bodyHtml() {
    if (state.tab === 'manual') return manualHtml();
    if (state.tab === 'orders') return ordersHtml();
    if (state.tab === 'cond') return condHtml();
    return dqHtml();
  }

  function manualHtml() {
    var res = state.lastResult;
    var resHtml = '';
    if (res) {
      if (res.ok) {
        resHtml = '<div class="td-res ok"><b>成交：</b>' + esc(res.code) + ' ' + esc(res.side) + ' '
          + '数量 ' + esc(res.qty) + ' @ ' + fmt(res.price, 3) + ' 元 → 成交额 ' + fmt(res.amount) + ' 元'
          + '<br><b>费用明细：</b>佣金 ' + fmt(res.commission) + ' ｜ 印花税 ' + fmt(res.stamp_tax)
          + ' ｜ 过户费 ' + fmt(res.transfer_fee) + ' ｜ 滑点成本 ' + fmt(res.slippage_cost)
          + ' ｜ 净额 ' + fmt(res.total_cost) + ' 元'
          + '<br><span style="color:#8ea2bd">决策置信度 ' + (res.decision ? fmt(res.decision.confidence, 3) : '-')
          + '（' + esc(res.decision ? res.decision.verdict_reason : '') + '）</span></div>';
      } else {
        var g = res.rejected_gate;
        resHtml = '<div class="td-res bad"><b>被拒：</b>' + esc(res.fail_reason || '下单被拒')
          + (g ? '<br><b>关卡：</b>' + esc(g.gate) + ' —— ' + esc(g.reason) : '')
          + (res.decision ? '<br><span style="color:#8ea2bd">决策闸：confidence=' + fmt(res.decision.confidence, 3)
            + ' ' + esc(res.decision.verdict_reason || '') + '</span>' : '')
          + '</div>';
      }
    }
    return ''
      + '<div class="td-row">'
      + '<label>标的代码<input id="td-m-code" placeholder="sh600519"></label>'
      + '<label>方向<select id="td-m-side"><option value="buy">买入</option><option value="sell">卖出</option></select></label>'
      + '<label>数量（股，0=自动）<input id="td-m-qty" type="number" min="0" step="100" value="0"></label>'
      + '<label>价格（留空取现价）<input id="td-m-price" type="number" step="0.001" value=""></label>'
      + '<label title="勾选后该单标记为演练/测试单，不计入真实统计与晋级降级">演练单<input id="td-m-drill" type="checkbox" style="margin-left:6px"></label>'
      + '<button class="td-btn" id="td-m-submit">提交下单</button>'
      + '<button class="td-btn ghost" id="td-m-price-fill">取现价</button>'
      + '</div>'
      + '<div style="color:#8ea2bd;margin-bottom:8px">手动单与信号单共用决策闸 + 风控 9 关（白名单/额度/涨跌停/停牌/T+1 全保留）。</div>'
      + resHtml;
  }

  function ordersHtml() {
    var rows = state.orders || [];
    if (!rows.length) return '<div style="color:#8ea2bd">暂无订单</div>';
    var head = '<tr><th>时间</th><th>来源</th><th>代码</th><th>方向</th><th>状态</th><th>数量</th><th>价格</th><th>金额</th><th>理由/决策</th><th>操作</th></tr>';
    var body = rows.map(function (o) {
      var canCancel = (o.status !== 'filled' && o.status !== 'rejected' && o.status !== 'cancelled' && o.status !== 'partial_canceled');
      var op = canCancel
        ? '<button class="td-btn danger" data-cancel-order="' + esc(o.id) + '">撤单</button>'
        : '<button class="td-btn danger" disabled title="' + (o.status === 'filled' ? '已成交，不可撤单' : '状态 ' + esc(o.status) + '，不可撤单') + '">撤单</button>';
      var reason = o.fail_reason ? esc(o.fail_reason) : (o.exit_reason ? '出场:' + esc(o.exit_reason) : '');
      var dj = o.decision_json;
      var djTitle = decisionTitle(dj);
      var djSum = dj ? '<span title="' + esc(djTitle) + '" style="cursor:help;color:#9fb0c8">闸 ' + (dj.allow ? '✅' : '⛔')
        + ' conf=' + fmt(dj.confidence, 2) + '</span>' : '';
      var qtyCell = String(o.qty);
      // 被风控在「数量换算之前」拦下的单：qty 会是 0，但 req_qty 保留了用户请求的数量。
      // 直接显示 0 会误导成「你要买 0 股」，因此优先呈现请求值并标注。
      if ((o.req_qty || 0) > 0 && (o.qty || 0) === 0) {
        qtyCell = '<span title="被拦下前用户请求的数量（未成交）">' + esc(o.req_qty) + '<i style="color:#8ea2bd"> 请求</i></span>';
      }
      return '<tr><td>' + esc((o.ts || '').slice(5)) + '</td><td>' + srcBadge(o.source) + '</td><td>' + esc(o.code)
        + '</td><td>' + esc(o.side) + '</td><td>' + statusBadge(o.status) + '</td><td>' + qtyCell
        + '</td><td>' + fmt(o.price, 3) + '</td><td>' + fmt(o.amount) + '</td><td>' + reason + ' ' + djSum + '</td><td>' + op + '</td></tr>';
    }).join('');
    return '<div class="td-row"><button class="td-btn ghost" id="td-o-refresh">刷新</button>'
      + '<span style="color:#8ea2bd">已成交/已拒单的撤单按钮置灰并提示原因</span></div>'
      + '<table class="td">' + head + body + '</table>';
  }

  function condHtml() {
    var rows = state.conds || [];
    var list = rows.length ? ('<table class="td"><tr><th>代码</th><th>方向</th><th>数量</th><th>触发</th><th>状态</th><th>已触发价</th><th>操作</th></tr>'
      + rows.map(function (c) {
        var trig = c.trigger_type === 'price' ? (c.trigger_price + ' ' + (c.trigger_op || '')) : ('@' + (c.trigger_at || ''));
        var op = c.status === 'pending'
          ? '<button class="td-btn danger" data-cancel-cond="' + esc(c.id) + '">取消</button>'
          : '<button class="td-btn danger" disabled title="状态 ' + esc(c.status) + '">取消</button>';
        return '<tr><td>' + esc(c.code) + '</td><td>' + esc(c.side) + '</td><td>' + esc(c.qty) + '</td><td>' + esc(trig)
          + '</td><td>' + statusBadge(c.status) + '</td><td>' + (c.triggered_price != null ? fmt(c.triggered_price, 3) : '-') + '</td><td>' + op + '</td></tr>';
      }).join('') + '</table>') : '<div style="color:#8ea2bd">暂无条件单</div>';
    return ''
      + '<div class="td-row">'
      + '<label>标的<input id="td-c-code" placeholder="sh600519"></label>'
      + '<label>方向<select id="td-c-side"><option value="buy">买入</option><option value="sell">卖出</option></select></label>'
      + '<label>数量<input id="td-c-qty" type="number" min="0" step="100" value="100"></label>'
      + '<label>触发类型<select id="td-c-type"><option value="price">价格</option><option value="time">时间</option></select></label>'
      + '<label>价格条件<select id="td-c-op"><option value=">=">≥</option><option value="<=">≤</option></select></label>'
      + '<label>触发价<input id="td-c-price" type="number" step="0.001"></label>'
      + '<label>触发时间<input id="td-c-at" placeholder="2026-10-20T09:35:00"></label>'
      + '<button class="td-btn" id="td-c-submit">创建条件单</button>'
      + '</div>'
      + '<div style="color:#8ea2bd;margin-bottom:8px">由盘中轮询触发；触发后仍走风控+决策闸（不是后门）。</div>'
      + list;
  }

  function dqHtml() {
    var items = state.dq || [];
    function box(title, filt) {
      var xs = items.filter(filt);
      if (!xs.length) return '<div class="td-col"><h4>' + title + '</h4><div style="color:#8ea2bd">无</div></div>';
      return '<div class="td-col"><h4>' + title + '（' + xs.length + '）</h4><table class="td"><tr><th>代码</th><th>侧</th><th>置信度</th><th>结果%</th><th>判定</th></tr>'
        + xs.map(function (x) {
          return '<tr title="' + esc(x.verdict_reason || '') + '"><td>' + esc(x.code) + (x.is_drill ? ' 🧪' : '') + '</td><td>' + esc(x.side)
            + '</td><td>' + fmt(x.confidence, 2) + '</td><td>' + fmt(x.outcome_ret_pct) + '</td><td>' + esc(x.verdict) + '</td></tr>';
        }).join('') + '</table></div>';
    }
    return '<div style="color:#8ea2bd;margin-bottom:8px">成交决策 vs 被拒决策——被拒决策的"结果%"即"如果当时买了会怎样"（gate_correct=拒对了；gate_too_strict=闸太严）。🧪=演练/测试样本。</div>'
      + '<div class="td-cols">'
      + box('成交决策 (allow=true)', function (x) { return x.allow; })
      + box('被拒决策 (allow=false)', function (x) { return !x.allow; })
      + '</div>';
  }

  // ---------- 交互 ----------
  function bind() {
    var r = host(); if (!r) return;
    var fab = r.querySelector('#td-fab');
    if (fab) fab.onclick = function () { r.querySelector('#td-overlay').classList.add('on'); };
    var cl = r.querySelector('#td-close');
    if (cl) cl.onclick = function () { r.querySelector('#td-overlay').classList.remove('on'); };
    r.querySelectorAll('.td-tab').forEach(function (b) {
      b.onclick = function () { state.tab = b.getAttribute('data-tab'); refreshTab(); };
    });
    // manual
    var sub = r.querySelector('#td-m-submit');
    if (sub) sub.onclick = submitManual;
    var pf = r.querySelector('#td-m-price-fill');
    if (pf) pf.onclick = fillPrice;
    // orders
    var orf = r.querySelector('#td-o-refresh');
    if (orf) orf.onclick = function () { loadOrders().then(refreshTab); };
    r.querySelectorAll('[data-cancel-order]').forEach(function (b) {
      b.onclick = function () { cancelOrder(b.getAttribute('data-cancel-order')); };
    });
    // cond
    var cs = r.querySelector('#td-c-submit');
    if (cs) cs.onclick = submitCond;
    r.querySelectorAll('[data-cancel-cond]').forEach(function (b) {
      b.onclick = function () { cancelCond(b.getAttribute('data-cancel-cond')); };
    });
  }

  function refreshTab() {
    if (state.tab === 'orders') return loadOrders().then(paint);
    if (state.tab === 'dq') return loadDq().then(paint);
    if (state.tab === 'cond') return loadConds().then(paint);
    paint();
  }
  function paint() {
    var b = host().querySelector('#td-body'); if (b) b.innerHTML = bodyHtml();
    bind();
  }

  function loadOrders() {
    return api('/orders?limit=50').then(function (r) {
      state.orders = (r && r.data && r.data.items) || [];
    });
  }
  function loadConds() {
    return api('/conditional-orders').then(function (r) {
      state.conds = (r && r.data && r.data.items) || [];
    });
  }
  function loadDq() {
    return api('/decision-quality').then(function (r) {
      state.dq = ((r && r.data && r.data.items) || []).slice().reverse();
    });
  }

  function fillPrice() {
    var code = (host().querySelector('#td-m-code').value || '').trim().toLowerCase();
    if (!CODE_RE.test(code)) { toast('代码格式非法（sh600519 形式）', true); return; }
    fetch('/api/realtime?codes=' + encodeURIComponent(code)).then(function (r) { return r.json(); }).then(function (j) {
      var q = (j && j.data && j.data[code]) || {};
      if (q.price) { host().querySelector('#td-m-price').value = q.price; toast('现价 ' + q.price); }
      else toast('取价失败', true);
    }).catch(function () { toast('取价失败', true); });
  }

  function submitManual() {
    var r = host();
    var code = (r.querySelector('#td-m-code').value || '').trim().toLowerCase();
    if (!CODE_RE.test(code)) { toast('代码格式非法（sh600519 形式）', true); return; }
    var body = {
      code: code,
      side: r.querySelector('#td-m-side').value,
      qty: parseInt(r.querySelector('#td-m-qty').value, 10) || 0,
      price: parseFloat(r.querySelector('#td-m-price').value) || 0,
      // 演练/测试单显式标记：勾选后该单 is_drill=1，一律排除出真实统计与策略晋级降级。
      // 不勾选则为真实单（服务端在演练上下文开启时仍会自动打标）。
      is_drill: !!(r.querySelector('#td-m-drill') && r.querySelector('#td-m-drill').checked),
    };
    api('/orders', 'POST', body).then(function (res) {
      // 信封级失败（ok:false 且 data 为空，如「无法获取有效现价」「代码格式非法」）：
      // 必须把服务端的 message 如实透出到结果面板 + toast。
      // 旧实现只读 res.data → data 为 null 时面板空白、toast 显示「被拒：」后一片空白，等于把错误吞掉。
      if (!res || res.ok === false || !res.data) {
        var msg = (res && (res.message || res.reason)) || '下单失败（服务端未返回原因）';
        state.lastResult = { ok: false, status: 'rejected', fail_reason: msg };
        paint();
        toast('下单失败：' + msg, true);
        loadOrders();
        return;
      }
      state.lastResult = res.data;
      paint();
      toast(res.data.ok ? '下单成功' : ('被拒：' + (res.data.fail_reason || '未返回拒绝原因')), !res.data.ok);
      loadOrders();
    });
  }

  function cancelOrder(id) {
    api('/orders/' + encodeURIComponent(id), 'DELETE').then(function (res) {
      toast(res.ok ? (res.message || '已撤单') : (res.message || '撤单失败'), !res.ok);
      loadOrders().then(paint);
    });
  }

  function submitCond() {
    var r = host();
    var code = (r.querySelector('#td-c-code').value || '').trim().toLowerCase();
    if (!CODE_RE.test(code)) { toast('代码格式非法（sh600519 形式）', true); return; }
    var type = r.querySelector('#td-c-type').value;
    var body = {
      code: code, side: r.querySelector('#td-c-side').value,
      qty: parseInt(r.querySelector('#td-c-qty').value, 10) || 0, trigger_type: type,
    };
    if (type === 'price') {
      body.trigger_op = r.querySelector('#td-c-op').value;
      body.trigger_price = parseFloat(r.querySelector('#td-c-price').value);
      if (!isFinite(body.trigger_price)) { toast('请填触发价', true); return; }
    } else {
      body.trigger_at = (r.querySelector('#td-c-at').value || '').trim();
      if (!body.trigger_at) { toast('请填触发时间', true); return; }
    }
    api('/conditional-orders', 'POST', body).then(function (res) {
      toast(res.ok ? '条件单已创建' : (res.message || '创建失败'), !res.ok);
      loadConds().then(paint);
    });
  }
  function cancelCond(id) {
    api('/conditional-orders/' + encodeURIComponent(id), 'DELETE').then(function (res) {
      toast(res.ok ? '条件单已取消' : (res.message || '取消失败'), !res.ok);
      loadConds().then(paint);
    });
  }

  window.toggleTradeDesk = function () {
    var r = host(); if (!r) return;
    injectCss();
    if (!r.querySelector('.td-overlay')) { render(); }
    var ov = r.querySelector('#td-overlay');
    ov.classList.toggle('on');
    if (ov.classList.contains('on')) refreshTab();
  };

  function init() {
    injectCss();
    render();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
