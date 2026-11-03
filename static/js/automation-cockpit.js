/**
 * 自动化中心 · 交易能力驾驶舱 (AXON Cockpit)
 * ---------------------------------------------------------------------------
 * 只读增强层：不修改 automation.js / automation-trade.js，仅复用现有接口
 *   /api/automation/{gate,config,positions,orders,signals,review/metrics}
 *   /api/review/equity
 * 在 #automation-body 顶部挂一块「交易能力」面板：KPI、执行链路、权益曲线、
 * 策略门控、拒单原因、额度使用；所有区块点击可跳到对应 tab。
 */
(function () {
  'use strict';

  var API = '/api/automation';
  var TTL = 15000;
  var cache = null, cacheAt = 0, loading = false;
  var mounted = false;
  var root = null;
  var obs = null;

  function get(url) {
    return fetch(url, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (b) { return b && (b.ok === true || b.data) ? (b.data || b) : null; })
      .catch(function () { return null; });
  }
  function pick(o, k, d) { return o && o[k] != null ? o[k] : d; }
  function num(v, d) { var n = Number(v); return isFinite(n) ? n : (d || 0); }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function todayStr(ts) {
    var t = ts ? new Date(ts) : new Date();
    return t.getFullYear() + '-' + pad2(t.getMonth() + 1) + '-' + pad2(t.getDate());
  }
  function pct(v, digits) { return (num(v) * 100).toFixed(digits == null ? 1 : digits) + '%'; }
  function wan(v) { var n = num(v); return n >= 1e8 ? (n / 1e8).toFixed(2) + '亿' : n >= 1e4 ? (n / 1e4).toFixed(1) + '万' : Math.round(n).toString(); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

  function load(force) {
    if (!force && cache && Date.now() - cacheAt < TTL) return Promise.resolve(cache);
    if (loading) return Promise.resolve(cache);
    loading = true;
    var urls = [
      API + '/gate',
      API + '/config',
      API + '/positions',
      API + '/orders?limit=200',
      API + '/signals?limit=500',
      API + '/review/metrics?days=30',
      '/api/review/equity',
      API + '/risk/health',
      API + '/market-sync'
    ];
    return Promise.all(urls.map(get)).then(function (res) {
      loading = false;
      cache = compute(res);
      cacheAt = Date.now();
      return cache;
    });
  }

  function compute(res) {
    var gate = res[0] || {}, cfg = res[1] || {}, pos = res[2] || {};
    var orders = (res[3] && res[3].items) || [];
    var signals = (res[4] && res[4].items) || [];
    var metrics = res[5] || {}, equity = res[6] || {};
    var health = res[7] || {}, mkt = res[8] || {};

    var now = new Date();
    var today = todayStr(now);

    /* ── 信号 ── */
    var sigToday = signals.filter(function (s) { return String(s.ts || '').indexOf(today) === 0; });
    var sigTotal = (res[4] && res[4].total) || signals.length;
    var sigCapped = signals.length >= 500; // 接口单次上限 500
    var sigAvgScore = sigToday.length ? Math.round(sigToday.reduce(function (a, s) { return a + num(s.score); }, 0) / sigToday.length) : 0;

    /* ── 订单与成交 ── */
    var byStatus = {};
    orders.forEach(function (o) { byStatus[o.status] = (byStatus[o.status] || 0) + 1; });
    var filled = byStatus.filled || 0;
    var rejected = byStatus.rejected || 0;
    var decided = filled + rejected;
    var fillRate = decided ? filled / decided : null;
    var ordersToday = orders.filter(function (o) { return String(o.ts || o.created_at || '').indexOf(today) === 0; });

    /* ── 拒单原因 ── */
    var reasons = {};
    orders.forEach(function (o) {
      if (o.status !== 'rejected' || !o.fail_reason) return;
      reasons[o.fail_reason] = (reasons[o.fail_reason] || 0) + 1;
    });
    var reasonList = Object.keys(reasons).map(function (k) { return { text: k, n: reasons[k] }; })
      .sort(function (a, b) { return b.n - a.n; }).slice(0, 3);

    /* ── 持仓与仓位 ── */
    var items = pos.items || [];
    var totalEquity = num(pos.total_equity, num(pos.cash) + num(pos.market_value));
    var positionUse = totalEquity > 0 ? num(pos.market_value) / totalEquity : 0;
    var riskCfg = cfg.risk || {};
    var maxPositions = num(riskCfg.max_positions, 0);

    /* ── 风控额度 ── */
    var amtToday = ordersToday.reduce(function (a, o) { return a + num(o.amount); }, 0);
    var maxAmtDay = num(riskCfg.max_amount_per_day, 0);
    var maxAmtOrder = num(riskCfg.max_amount_per_order, 0);
    var maxOrderToday = ordersToday.reduce(function (a, o) { return Math.max(a, num(o.amount)); }, 0);

    /* ── 复盘 ── */
    var dd = metrics.max_drawdown;
    var sampleDays = num(metrics.sample_days, 0);
    var sampleReq = num(metrics.sample_required, 60);

    /* ── 权益曲线 ── */
    var eq = (equity.equity && equity.equity.values) || [];
    var eqDates = (equity.equity && equity.equity.dates) || [];

    /* ── 策略 ── */
    var strategies = (gate.strategies || []).map(function (s) {
      var bt = s.backtest || {};
      return {
        id: s.strategy_id, name: s.name || s.strategy_id,
        state: s.gate_state || 'draft',
        paperDone: num(s.paper_days_done), paperReq: num(s.paper_days_required, 5),
        winRate: bt.win_rate, annual: bt.annual_return, dd: bt.max_drawdown,
        canLive: !!s.can_enable_live, reason: s.block_reason || ''
      };
    });

    return {
      at: now,
      liveSwitch: !!gate.live_master_switch,
      broker: gate.broker_adapter || null,
      paperDone: num(pos.paper_days_done), paperReq: num(pos.paper_days_required, 5),
      sigTotal: sigTotal, sigToday: sigToday.length, sigCapped: sigCapped, sigAvgScore: sigAvgScore,
      ordersTotal: orders.length, ordersToday: ordersToday.length,
      filled: filled, rejected: rejected, fillRate: fillRate,
      refusals: reasonList,
      positions: items.length, maxPositions: maxPositions,
      positionUse: positionUse,
      cash: num(pos.cash), equity: totalEquity, marketValue: num(pos.market_value),
      amtToday: amtToday, maxAmtDay: maxAmtDay, maxAmtOrder: maxAmtOrder, maxOrderToday: maxOrderToday,
      drawdown: dd, sampleDays: sampleDays, sampleReq: sampleReq,
      cfgs: {
        cooldown: num(cfg.cooldown_minutes, 0),
        threshold: num((cfg.trigger || {}).score_threshold, 0),
        maxPositions: maxPositions,
        paper: !!(cfg.switches && cfg.switches.paper),
        live: !!(cfg.switches && cfg.switches.live)
      },
      eq: eq, eqDates: eqDates, eqLast: eq.length ? eq[eq.length - 1] : null,
      strategies: strategies,
      health: {
        verdict: health.verdict || 'unknown',
        score: health.score == null ? null : health.score,
        issues: (health.issues || []).slice(0, 4),
        fails: health.fail_count || 0,
        warns: health.warn_count || 0,
      },
      market: {
        phase: (mkt.session || {}).phase || '',
        phaseLabel: (mkt.session || {}).label || '',
        canOrder: !!(mkt.session || {}).can_order,
        regime: (mkt.regime || {}).label || '',
        regimeState: (mkt.regime || {}).state || '',
        threshold: mkt.threshold || null,
        next: (mkt.session || {}).next || null,
      }
    };
  }

  /* ── 渲染工具 ─────────────────────────────────────────────────────────── */
  function countUp(el, to, digits) {
    var from = Number(el.getAttribute('data-v') || 0);
    el.setAttribute('data-v', to);
    if (!isFinite(from) || from === to) { el.textContent = digits ? Number(to).toFixed(digits) : Math.round(to); return; }
    var t0 = performance.now(), dur = 520;
    if (el._raf) cancelAnimationFrame(el._raf);
    function step(t) {
      var k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      var v = from + (to - from) * e;
      el.textContent = digits ? v.toFixed(digits) : Math.round(v).toLocaleString('en-US');
      if (k < 1) el._raf = requestAnimationFrame(step);
    }
    el._raf = requestAnimationFrame(step);
  }

  function kpiTile(o) {
    return '<button type="button" class="axc-kpi" data-tab="' + o.tab + '" title="' + esc(o.title || '') + '">' +
      '<span class="axc-kpi-k">' + o.k + '</span>' +
      '<span class="axc-kpi-v"><b id="' + o.id + '">—</b>' + (o.unit ? '<span>' + o.unit + '</span>' : '') + '</span>' +
      '<span class="axc-kpi-s ' + (o.tone || '') + '">' + o.sub + '</span>' +
      (o.bar != null ? '<span class="axc-kpi-bar"><i style="width:' + clamp(o.bar, 0, 1) * 100 + '%"></i></span>' : '') +
      '</button>';
  }

  function render(m) {
    if (!root) return;
    var ddTone = (m.drawdown != null && m.drawdown < -0.1) ? 'is-bad' : 'is-gold';
    var fillTone = m.fillRate == null ? '' : (m.fillRate >= 0.8 ? 'is-good' : (m.fillRate < 0.6 ? 'is-bad' : ''));
    var liveChip = m.liveSwitch
      ? '<span class="axc-chip is-live"><i></i>实盘 · 已开</span>'
      : '<span class="axc-chip is-off"><i></i>实盘 · 关闭</span>';
    var paperChip = '<span class="axc-chip' + (m.paperDone >= m.paperReq ? ' is-live' : '') + '"><i></i>模拟 ' + m.paperDone + ' / ' + m.paperReq + ' 日</span>';
    var brokerChip = m.broker ? '<span class="axc-chip is-live"><i></i>' + esc(m.broker) + '</span>' : '';
    var timeChip = '<span class="axc-chip"><i></i>数据 ' + pad2(m.at.getHours()) + ':' + pad2(m.at.getMinutes()) + '</span>';
    var hv = m.health.verdict;
    var vTxt = hv === 'ok' ? '风控 · 正常' : hv === 'blocked' ? '风控 · 阻断' : hv === 'warn' ? '风控 · 待办' : '风控 · 未知';
    var healthChip = '<span class="axc-chip ' + (hv === 'ok' ? 'is-live' : hv === 'blocked' ? 'is-bad' : 'is-warn') + '" title="' + esc((m.health.issues[0] || {}).fix || '') + '"><i></i>' + vTxt + (m.health.score == null ? '' : ' ' + m.health.score + '分') + '</span>';
    var phaseChip = '<span class="axc-chip ' + (m.market.canOrder ? 'is-live' : 'is-off') + '"><i></i>' + esc(m.market.phaseLabel || '时段未知') + '</span>';
    var th = m.market.threshold;
    var thChip = th ? '<span class="axc-chip" title="市况 ' + esc(m.market.regime) + '；阈值调整 ' + (th.delta >= 0 ? '+' : '') + th.delta + '"><i></i>阈值 ' + Math.round(th.base) + (th.delta ? ' → ' + Math.round(th.effective) : '') + '</span>' : '';

    var stored = null;
    try { stored = localStorage.getItem('axc-collapsed'); } catch (e) {}
    var collapsed = stored === null ? true : stored === '1';
    root.classList.toggle('is-collapsed', collapsed);
    var html = '';
    html += '<div class="axc-head">' +
      '<h3 class="axc-title">交易能力<em>TRADING CAPABILITY</em></h3>' +
      '<span class="axc-rule"></span>' +
      liveChip + paperChip + healthChip + phaseChip + thChip + timeChip +
      '<button type="button" class="axc-refresh" data-collapse title="展开全部指标 / 收起为紧凑条">' + (collapsed ? '详情' : '收起') + '</button>' +
      '<button type="button" class="axc-refresh" data-refresh>刷新</button>' +
      '</div>';

    html += '<div class="axc-kpis">';
    html += kpiTile({
      k: '今日信号', id: 'axc-k-sig', tab: 'signals', unit: '条',
      sub: '均分 ' + m.sigAvgScore + ' · 累计 ' + m.sigTotal + (m.sigCapped ? ' · 接口上限 500' : ''),
      title: '今日产生的交易信号数量；均分为信号强度（0-100）。信号接口单次最多 500 条，超出以 + 表示'
    });
    html += kpiTile({
      k: '成交率', id: 'axc-k-fill', tab: 'orders', unit: '%', tone: fillTone,
      title: '已成交 /（已成交 + 拒单），不含撤单',
      sub: '成交 ' + m.filled + ' · 拒单 ' + m.rejected,
      bar: m.fillRate == null ? 0 : m.fillRate
    });
    html += kpiTile({
      k: '持仓 · 上限', id: 'axc-k-pos', tab: 'positions', unit: '只',
      sub: '上限 ' + (m.maxPositions || '—') + ' 只',
      title: '当前持仓只数 / 配置允许的最大持仓数',
      bar: m.maxPositions ? m.positions / m.maxPositions : 0
    });
    html += kpiTile({
      k: '仓位使用率', id: 'axc-k-use', tab: 'positions', unit: '%',
      sub: '市值 ' + wan(m.marketValue) + ' · 现金 ' + wan(m.cash),
      title: '持仓市值 / 总权益',
      bar: m.positionUse
    });
    html += kpiTile({
      k: '最大回撤', id: 'axc-k-dd', tab: 'review', unit: '%', tone: ddTone,
      sub: '样本 ' + m.sampleDays + '/' + m.sampleReq + ' 日',
      title: '近 30 日权益最大回撤（相对峰值）；样本不足时仅供参考'
    });
    html += kpiTile({
      k: '单日额度', id: 'axc-k-quota', tab: 'config', unit: '%',
      sub: '已用 ' + wan(m.amtToday) + ' / ' + wan(m.maxAmtDay),
      title: '今日下单金额 / 配置的单日金额上限',
      bar: m.maxAmtDay ? m.amtToday / m.maxAmtDay : 0
    });
    html += '</div>';

    /* 执行链路 */
    var stages = [
      { k: '信号', v: m.sigToday + (m.sigCapped ? '+' : ''), s: '今日触发', tab: 'signals', state: m.sigToday ? 'done' : '' },
      { k: '决策闸', v: m.strategies.length, s: m.strategies.filter(function (x) { return x.canLive; }).length + ' 可上实盘', tab: 'config', state: 'done' },
      { k: '风控', v: m.rejected, s: '拦截/拒单', tab: 'orders', state: m.rejected ? 'alert' : '' },
      { k: '下单', v: m.ordersToday, s: '今日委托', tab: 'orders', state: m.ordersToday ? 'done' : '' },
      { k: '成交', v: m.filled, s: '累计成交', tab: 'orders', state: m.filled ? 'done' : '' },
      { k: '复盘', v: m.sampleDays, s: '样本日', tab: 'review', state: 'done' }
    ];
    html += '<div class="axc-flow"><div class="axc-flow-h"><b>执行链路</b><em>PIPELINE</em><span>点任一环节进入对应面板</span></div><div class="axc-stages">';
    stages.forEach(function (st, i) {
      if (i) html += '<span class="axc-stage-arrow"></span>';
      html += '<button type="button" class="axc-stage ' + st.state + '" data-tab="' + st.tab + '" title="' + st.k + '：' + st.s + '">' +
        '<i></i><span class="axc-stage-k">' + st.k + '</span><span class="axc-stage-v">' + st.v + '</span>' +
        '<span class="axc-stage-s">' + st.s + '</span></button>';
    });
    html += '</div></div>';

    /* 权益曲线 + 策略 */
    html += '<div class="axc-grid">';
    html += '<div class="axc-box"><div class="axc-box-h"><b>权益曲线</b><em>EQUITY</em><span>' + (m.eqLast != null ? '净值 ' + num(m.eqLast).toFixed(4) : '—') + '</span></div>' + sparkline(m) + '</div>';

    html += '<div class="axc-box"><div class="axc-box-h"><b>策略门控</b><em>GATE</em><span>' + m.strategies.length + ' 个策略</span></div>';
    html += '<div class="axc-strat">';
    if (!m.strategies.length) html += '<div class="axc-empty">暂无策略配置</div>';
    m.strategies.forEach(function (s) {
      var stateCls = s.state === 'live' ? 'is-live' : (s.state === 'paper' ? 'is-paper' : 'is-draft');
      var wr = s.winRate == null ? '—' : pct(s.winRate, 0);
      html += '<div class="axc-srow ' + stateCls + '" title="' + esc(s.name + ' · ' + s.state + (s.reason ? ' · ' + s.reason : '')) + '">' +
        '<span class="axc-sdot"></span>' +
        '<span class="axc-sname">' + esc(s.name) + '</span>' +
        '<span class="axc-sprog"><i style="width:' + clamp(s.paperReq ? s.paperDone / s.paperReq : 0, 0, 1) * 100 + '%"></i></span>' +
        '<span class="axc-sval">' + wr + '</span></div>';
    });
    html += '</div>';

    /* 拒单 + 额度 */
    html += '<div class="axc-box-h" style="margin-top:12px"><b>拒单原因</b><em>REJECTED</em><span>累计 ' + m.rejected + '</span></div>';
    if (m.refusals.length) {
      html += '<div class="axc-refusals">';
      m.refusals.forEach(function (r) {
        html += '<div class="axc-refusal" title="' + esc(r.text) + '"><i></i><span>' + esc(r.text) + '</span><b>' + r.n + '</b></div>';
      });
      html += '</div>';
    } else {
      html += '<div class="axc-empty">暂无拒单记录</div>';
    }

    html += '<div class="axc-quota">' +
      '<div class="axc-quota-row"><span>单笔 / 上限</span><span class="axc-quota-bar"><i style="width:' + clamp(m.maxAmtOrder ? m.maxOrderToday / m.maxAmtOrder : 0, 0, 1) * 100 + '%"></i></span><b>' + wan(m.maxOrderToday) + ' / ' + wan(m.maxAmtOrder) + '</b></div>' +
      '</div>';

    html += '</div></div>';

    /* 风控体检 + 市场同步（展开态） */
    html += '<div class="axc-box axc-risk"><div class="axc-box-h"><b>风控体检</b><em>RISK HEALTH</em><span>' +
      (m.health.score == null ? '—' : m.health.score + ' 分') + ' · ' +
      (m.health.verdict === 'ok' ? '通过' : m.health.verdict === 'blocked' ? '存在阻断' : '有 ' + m.health.warns + ' 项待办') + '</span></div>';
    if (!m.health.issues.length) {
      html += '<div class="axc-empty">各道风控关卡均已通过（白名单 / 额度 / 持仓 / 熔断 / 策略门禁）。</div>';
    } else {
      html += '<div class="axc-risks">';
      m.health.issues.forEach(function (it) {
        html += '<div class="axc-risk-row lv-' + esc(it.level || 'warn') + '" title="' + esc(it.detail || '') + '">' +
          '<span class="axc-risk-k">' + esc(it.label || '') + '</span>' +
          '<span class="axc-risk-v">' + esc(it.value || '') + '</span>' +
          '<span class="axc-risk-fix">' + esc(it.fix || '') + '</span></div>';
      });
      html += '</div>';
    }
    var nextTxt = m.market.next ? ('下一时段 ' + (m.market.next.label || '') + ' ' + String(m.market.next.at || '').slice(11, 16)) : '';
    html += '<div class="axc-market"><span>市场同步</span><b>' + esc(m.market.phaseLabel || '—') + '</b>' +
      (m.market.regime ? '<span>市况</span><b>' + esc(m.market.regime) + '</b>' : '') +
      (m.market.threshold ? '<span>信号阈值</span><b>' + Math.round(m.market.threshold.base) + (m.market.threshold.delta ? ' → ' + Math.round(m.market.threshold.effective) : '') + '</b>' : '') +
      (nextTxt ? '<i>' + esc(nextTxt) + '</i>' : '') + '</div>';
    html += '</div>';

    root.innerHTML = html;

    /* 数字滚动 */
    countUp(document.getElementById('axc-k-sig'), m.sigToday);
    if (m.sigCapped) {
      var se = document.getElementById('axc-k-sig');
      se.textContent = m.sigToday + '+';
      clearTimeout(se._t);
      se._t = setTimeout(function () { se.textContent = m.sigToday + '+'; }, 560);
    }
    if (m.fillRate != null) { var fe = document.getElementById('axc-k-fill'); fe.textContent = (m.fillRate * 100).toFixed(0); }
    else document.getElementById('axc-k-fill').textContent = '—';
    countUp(document.getElementById('axc-k-pos'), m.positions);
    var ue = document.getElementById('axc-k-use'); ue.textContent = (m.positionUse * 100).toFixed(0);
    var de = document.getElementById('axc-k-dd'); de.textContent = m.drawdown == null ? '—' : (num(m.drawdown) * 100).toFixed(1);
    var qe = document.getElementById('axc-k-quota'); qe.textContent = m.maxAmtDay ? Math.round(m.amtToday / m.maxAmtDay * 100) : '—';

    /* 点击跳转 */
    root.querySelectorAll('[data-tab]').forEach(function (el) {
      el.addEventListener('click', function () {
        var t = el.getAttribute('data-tab');
        if (typeof window.switchAutomationTab === 'function') window.switchAutomationTab(t);
      });
    });
    var rb = root.querySelector('[data-refresh]');
    if (rb) rb.addEventListener('click', function () { reload(rb); });
    var cb = root.querySelector('[data-collapse]');
    if (cb) cb.addEventListener('click', function () {
      var next = !root.classList.contains('is-collapsed');
      root.classList.toggle('is-collapsed', next);
      cb.textContent = next ? '详情' : '收起';
      try { localStorage.setItem('axc-collapsed', next ? '1' : '0'); } catch (e) {}
    });
  }

  function sparkline(m) {
    var vals = m.eq || [];
    if (vals.length < 2) return '<div class="axc-empty">样本不足，暂无权益曲线</div>';
    var W = 560, H = 92, P = 8;
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    var pad = Math.max(1e-5, (max - min) * 0.22);
    var lo = Math.min(min - pad, min), hi = Math.max(max + pad, max);
    var span = Math.max(1e-6, hi - lo);
    var pts = vals.map(function (v, i) {
      var x = P + (W - P * 2) * (i / (vals.length - 1));
      var y = H - P - ((v - lo) / span) * (H - P * 2);
      return [x, y];
    });
    var line = pts.map(function (p, i) { return (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' ');
    var area = line + ' L' + pts[pts.length - 1][0].toFixed(1) + ' ' + (H - P) + ' L' + pts[0][0].toFixed(1) + ' ' + (H - P) + ' Z';
    var last = pts[pts.length - 1];
    var firstDate = m.eqDates[0] || '', lastDate = m.eqDates[m.eqDates.length - 1] || '';
    var loVal = Math.min.apply(null, vals), hiVal = Math.max.apply(null, vals);
    return '<div class="axc-eq-wrap">' +
      '<svg class="axc-eq" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" aria-label="权益曲线">' +
      '<defs><linearGradient id="axc-eq-grad" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="rgba(214,176,132,.22)"/><stop offset="100%" stop-color="rgba(214,176,132,0)"/></linearGradient></defs>' +
      '<line class="eq-hair" x1="' + P + '" y1="' + (H - P) + '" x2="' + (W - P) + '" y2="' + (H - P) + '"/>' +
      '<path class="eq-area" d="' + area + '"/>' +
      '<path class="eq-line" d="' + line + '"/>' +
      '<circle class="eq-dot" cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="2.6"/>' +
      '</svg>' +
      '<div class="axc-eq-x"><span>' + esc(firstDate.slice(5)) + '</span>' +
      '<span>区间 ' + num(loVal).toFixed(4) + ' – ' + num(hiVal).toFixed(4) + '</span>' +
      '<span>' + esc(lastDate.slice(5)) + '</span></div>' +
      '</div>';
  }

  function reload(btn) {
    if (btn) { btn.classList.add('is-busy'); btn.textContent = '读取中'; }
    load(true).then(function (m) {
      render(m);
      if (btn && btn.isConnected) { btn.classList.remove('is-busy'); btn.textContent = '刷新'; }
    });
  }

  function wireRefresh() {
    var panel = document.getElementById('automation-panel');
    if (!panel || panel.__axcWired) return;
    panel.__axcWired = true;
    panel.addEventListener('click', function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      if (t.closest('.full-overlay-tab')) { setTimeout(function () { load(true).then(function (m) { if (m) render(m); }); }, 900); return; }
      if (t.closest('#axc-cockpit')) return;
      if (t.closest('button, .auto-btn, .toggle-switch, input[type="checkbox"]')) {
        setTimeout(function () { load(true).then(function (m) { if (m) render(m); }); }, 1800);
      }
    }, true);
    setInterval(function () {
      if (document.hidden) return;
      if (panel.style.display === 'none') return;
      load(false).then(function (m) { if (m) render(m); });
    }, 30000);
  }

  function mount() {
    var body = document.getElementById('automation-body');
    if (!body || document.getElementById('axc-cockpit')) return;
    root = document.createElement('section');
    root.id = 'axc-cockpit';
    root.className = 'axc';
    root.setAttribute('aria-label', '交易能力驾驶舱');
    root.innerHTML = '<div class="axc-empty">读取交易能力数据…</div>';
    body.insertBefore(root, body.firstChild);
    mounted = true;
    load(false).then(render);
    wireRefresh();
    window.automationCockpit = { refresh: function () { load(true).then(render); }, root: root };

    /* 面板打开时刷新数据（display 由 automation.js 切换） */
    var panel = document.getElementById('automation-panel');
    if (panel && 'MutationObserver' in window) {
      obs = new MutationObserver(function () {
        if (panel.style.display !== 'none') load(false).then(function (m) { if (m) render(m); });
      });
      obs.observe(panel, { attributes: true, attributeFilter: ['style'] });
    }
  }

  function boot() {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
    /* automation.js 可能延迟创建 body 内容，兜底再试一次 */
    setTimeout(mount, 1200);
  }
  boot();
})();
