/* DAPENG Alpha · 决策台 v1
 * 盘前决策 / 盘中监控 / 盘后复盘，一屏汇总：世界局势、市场温度、资金板块、持仓风险、事件情报。
 * 依赖现有只读 API，不修改后端；所有数据带时间戳与降级标记。
 */
(function () {
  'use strict';

  /* 单一数据入口：慢源独立超时，避免题材/数据源健康拖住整屏。
     市场调查也在此加载，不再由第二个轮询器重复请求同一批市场接口。 */
  var ENDPOINTS = {
    market: { url: '/api/market-dashboard', timeout: 20000, label: '市场快照' },
    north: { url: '/api/market/north', timeout: 15000, label: '北向结构' },
    globalAssets: { url: '/api/global/assets', timeout: 18000, label: '全球资产' },
    linkage: { url: '/api/global/linkage', timeout: 22000, label: '全球联动' },
    intel: { url: '/api/brain/intel', timeout: 12000, label: '事件情报' },
    news: { url: '/api/news/market?limit=8', timeout: 18000, label: '市场要闻' },
    review: { url: '/api/review/summary', timeout: 12000, label: '复盘摘要' },
    weekly: { url: '/api/review/weekly', timeout: 12000, label: '每周复盘' },
    positions: { url: '/api/automation/positions', timeout: 12000, label: '模拟持仓' },
    riskHealth: { url: '/api/automation/risk/health', timeout: 12000, label: '风控体检' },
    decisionQuality: { url: '/api/automation/decision-quality', timeout: 12000, label: '决策质量' },
    themes: { url: '/api/themes/overview', timeout: 45000, label: '板块题材' },
    datasources: { url: '/api/data-sources/health', timeout: 30000, label: '数据源健康' },
    survey: { url: '/api/intelligent/market/survey', timeout: 45000, label: '市场调查' },
    sentiment: { url: '/api/decision/sentiment', timeout: 30000, label: '统一情绪' },
    experts: { url: '/api/decision/experts', timeout: 45000, label: '六专家研判' },
    coverage: { url: '/api/decision/coverage', timeout: 75000, label: '全市场覆盖' },
    scan: { url: '/api/decision/scan', timeout: 30000, label: '全市场扫描' }
  };

  var MODE_KEY = 'dapeng-decision-mode';
  var PLAN_PREFIX = 'dapeng-decision-plan:';
  var REVIEW_PREFIX = 'dapeng-decision-review:';
  var CACHE_MS = 30000;

  var state = {
    built: false,
    loading: false,
    loadedAt: 0,
    mode: 'auto',
    stream: null,
    streamStatus: 'off',
    lastEventAt: 0,
    renderQueued: false,
    data: {},
    resources: {},
    requestId: 0,
    plan: null,
    drafts: { plan: '', review: '' },
    timer: null
  };

  function $(id) { return document.getElementById(id); }
  function esc(v) {
    if (v == null) return '';
    return String(v).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }
  function num(v) {
    /* null / 空串表示「未取得」，不能按 JSON 的隐式规则变成 0 ——
       否则缺失行情会被画成 0.00，并在加权时给出错误结论。 */
    if (v == null || v === '') return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }
  function fmt(v, d) { var n = num(v); if (n == null) return '--'; return n.toFixed(d == null ? 2 : d); }
  function pct(v, d) { var n = num(v); if (n == null) return '--'; return (n > 0 ? '+' : '') + n.toFixed(d == null ? 2 : d) + '%'; }
  function amt(v) {
    var n = num(v); if (n == null) return '--';
    if (Math.abs(n) >= 1e12) return (n / 1e12).toFixed(2) + ' 万亿';
    if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + ' 亿';
    if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + ' 万';
    return String(Math.round(n));
  }
  function chgClass(v) { var n = num(v); if (n == null) return ''; return n > 0 ? 'up-text' : n < 0 ? 'down-text' : 'muted-text'; }
  function today() {
    var d = new Date();
    var p = function (x) { return (x < 10 ? '0' : '') + x; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  }
  function nowHM() { var d = new Date(); return (d.getHours() < 10 ? '0' : '') + d.getHours() + ':' + (d.getMinutes() < 10 ? '0' : '') + d.getMinutes(); }
  function guessMode() {
    var d = new Date();
    var mins = d.getHours() * 60 + d.getMinutes();
    if (mins < 9 * 60 + 25) return 'pre';
    if (mins <= 15 * 60) return 'intra';
    return 'post';
  }
  function activeMode() {
    return state.mode === 'auto' ? guessMode() : state.mode;
  }
  function hs(v) { try { return localStorage.getItem(v); } catch (e) { return null; } }
  function hset(k, v) { try { localStorage.setItem(k, v); } catch (e) { } }

  function jget(url, timeout) {
    var ms = timeout || 15000;
    var ctl = new AbortController();
    var timer = setTimeout(function () { ctl.abort(); }, ms);
    return fetch(url, { signal: ctl.signal, cache: 'no-store', headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        return r.json().catch(function () { throw new Error('HTTP ' + r.status + ' · 非 JSON 响应'); }).then(function (payload) {
          if (!r.ok) throw new Error('HTTP ' + r.status + ((payload && (payload.message || payload.detail)) ? ' · ' + (payload.message || payload.detail) : ''));
          if (payload && payload.ok === false) throw new Error(payload.message || payload.error || payload.reason || '接口返回不可用');
          return payload;
        });
      })
      .finally(function () { clearTimeout(timer); });
  }

  async function loadAll(force) {
    if (state.loading) return;
    if (!force && state.loadedAt && Date.now() - state.loadedAt < CACHE_MS) { render(); return; }
    state.loading = true;
    state.requestId += 1;
    var requestId = state.requestId;
    var hasData = Object.keys(state.data).length > 0;
    if (!hasData) renderLoading();
    else render();

    var keys = Object.keys(ENDPOINTS);
    keys.forEach(function (key) {
      state.resources[key] = { status: 'loading', label: ENDPOINTS[key].label };
    });
    await Promise.all(keys.map(function (key) {
      var cfg = ENDPOINTS[key];
      return jget(cfg.url, cfg.timeout).then(function (payload) {
        if (requestId !== state.requestId) return;
        state.data[key] = payload;
        state.resources[key] = { status: 'ok', label: cfg.label, at: Date.now() };
      }).catch(function (error) {
        if (requestId !== state.requestId) return;
        var hasPrevious = !!state.data[key];
        state.resources[key] = {
          status: hasPrevious ? 'stale' : 'error', label: cfg.label,
          error: String(error && error.message || error), at: Date.now()
        };
      }).finally(function () {
        if (requestId !== state.requestId) return;
        /* 渐进渲染：快速源先到先显示，慢源不再阻塞其他卡片。 */
        render();
      });
    }));
    if (requestId !== state.requestId) return;
    state.loadedAt = Date.now();
    state.loading = false;
    render();
    /* 龙虎榜复盘不进轮询：只拉一次，之后靠独立页面看完整内容。 */
    loadLhbOnce();
  }

  /* ---------------- 构建骨架 ---------------- */
  function build() {
    var host = $('decision-mount');
    if (!host) return;
    host.innerHTML =
      '<div class="dd-head">' +
        '<div class="dd-head-title"><span class="dd-eyebrow">DAPENG / DECISION OPS</span><strong>决策台 <b>DECISION DESK</b></strong>' +
          '<span>把市场状态翻译成仓位、方向与执行纪律</span></div>' +
        '<div class="dd-head-controls">' +
          '<div class="dd-mode" role="tablist" aria-label="决策阶段">' +
            '<button data-act="mode" data-mode="auto">自动</button>' +
            '<button data-act="mode" data-mode="pre">盘前</button>' +
            '<button data-act="mode" data-mode="intra">盘中</button>' +
            '<button data-act="mode" data-mode="post">盘后</button>' +
          '</div>' +
          '<div class="dd-meta"><span class="dd-chip" id="dd-updated">--</span><span class="dd-chip" id="dd-quality">--</span></div>' +
          '<button class="dd-btn primary" id="dd-refresh" data-act="refresh"><span class="dd-refresh-icon">↻</span> 同步数据</button>' +
        '</div>' +
      '</div>' +
      '<div class="dd-pulse" id="dd-pulse" aria-live="polite"></div>' +
      '<div class="dd-grid" id="dd-grid"></div>' +
      '<div id="dd-overlay" class="dd-overlay" hidden>' +
        '<div class="dd-overlay-panel">' +
          '<div class="dd-overlay-head"><div><strong id="dd-overlay-title">--</strong><span id="dd-overlay-sub"></span></div>' +
          '<button class="dd-btn ghost" data-act="close-overlay">关闭</button></div>' +
          '<div id="dd-overlay-body"></div>' +
        '</div>' +
      '</div>';
    state.built = true;
    host.addEventListener('click', onClick);
    updateModeButtons();
  }

  function onClick(e) {
    if (e.target && e.target.id === 'dd-overlay') { closeOverlay(); return; }
    var btn = e.target.closest('[data-act]');
    if (!btn) return;
    var act = btn.getAttribute('data-act');
    if (act === 'refresh') { loadAll(true); return; }
    if (act === 'mode') {
      state.mode = btn.getAttribute('data-mode');
      hset(MODE_KEY, state.mode);
      updateModeButtons();
      render();
      return;
    }
    // 卡片上的「大盘纵览 / 完整调查」不再是跨工作区跳转，而是切到本中心的对应分层
    if (act === 'crosscheck-refresh') { loadAlignment(true); return; }
    if (act === 'open-market') { if (typeof switchHubSegment === 'function') switchHubSegment('fact'); return; }
    if (act === 'open-survey') { if (typeof switchHubSegment === 'function') switchHubSegment('survey'); return; }
    if (act === 'open-sentiment') { if (typeof switchHubSegment === 'function') switchHubSegment('fact'); return; }
    if (act === 'open-stocks') { if (typeof switchWorkspace === 'function') switchWorkspace('stocks'); return; }
    if (act === 'open-sector') {
      var key = btn.getAttribute('data-key');
      if (!key) return;
      if (typeof switchWorkspace === 'function') switchWorkspace('stocks');
      if (typeof switchSector === 'function') switchSector(key);
      else if (typeof selectSector === 'function') selectSector(key);
      return;
    }
    if (act === 'close-overlay') { closeOverlay(); return; }
    if (act === 'open-board') { openBoard(btn.getAttribute('data-code')); return; }
    if (act === 'open-char') { openChar(btn.getAttribute('data-char')); return; }
    if (act === 'pick-stock') {
      var code = btn.getAttribute('data-code'), nm = btn.getAttribute('data-name');
      closeOverlay();
      if (typeof switchWorkspace === 'function') switchWorkspace('stocks');
      if (typeof selectStock === 'function') selectStock(code, nm);
      return;
    }
    if (act === 'open-automation') {
      if (typeof toggleAutomationPanel === 'function') toggleAutomationPanel();
      return;
    }
    if (act === 'copy-plan') { copyPlan(); return; }
    if (act === 'save-plan') { savePlan(); return; }
    if (act === 'save-review') { saveReview(); return; }
    if (act === 'load-plan') { loadPlan(btn.getAttribute('data-date')); return; }
  }

  function updateModeButtons() {
    var host = $('decision-mount');
    if (!host) return;
    var m = activeMode();
    host.querySelectorAll('[data-act="mode"]').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-mode') === state.mode || (state.mode === 'auto' && b.getAttribute('data-mode') === 'auto'));
    });
    var chip = $('dd-mode-label');
    if (chip) chip.textContent = m === 'pre' ? '盘前' : m === 'intra' ? '盘中' : '盘后';
  }

  /* ---------------- 结论计算 ---------------- */
  function computeVerdict() {
    var d = state.data;
    var md = d.market && d.market.ok ? d.market.data : null;
    var link = d.linkage && d.linkage.ok ? d.linkage : null;
    var risk = d.riskHealth && d.riskHealth.ok ? d.riskHealth.data : null;
    var sentiment = d.sentiment && d.sentiment.ok ? d.sentiment : null;
    var panel = d.experts && d.experts.ok ? d.experts : null;
    var cov = d.coverage && d.coverage.ok ? d.coverage : null;
    var fullBreadth = cov && cov.ashare && cov.ashare.quotes && cov.ashare.quotes.valid ? cov.ashare.quotes : null;
    var breadth = fullBreadth || (md && md.breadth ? md.breadth : {});
    var up = num(breadth.up), down = num(breadth.down), flat = num(breadth.flat);
    var total = (up || 0) + (down || 0) + (flat || 0);
    var upRatio = total > 0 ? (up / total) * 100 : null;
    var marketScore = md && md.judgment ? num(md.judgment.score) : null;
    var sentScore = sentiment ? num(sentiment.score) : null;
    var score = sentScore != null ? sentScore : marketScore;
    var lscore = link && link.scorecard ? num(link.scorecard.composite_score) : null;
    var riskScore = risk ? num(risk.score) : null;

    var points = 0, max = 0, reasons = [];
    function add(p, m, txt) { points += p; max += m; if (txt) reasons.push(txt); }
    if (score != null) {
      var scoreSrc = sentScore != null ? '统一情绪 sentiment.v1' : '市场综合评分';
      add(score, 100, scoreSrc + ' ' + score + '/100（' + ((sentiment && sentiment.status) || (md && md.judgment && md.judgment.regime) || '未知') + '）');
    }
    if (upRatio != null) { add(upRatio, 100, '上涨家数占比 ' + upRatio.toFixed(1) + '%'); }
    if (lscore != null) {
      var norm = Math.max(0, Math.min(100, 50 + lscore * 12));
      add(norm, 100, '全球联动评分 ' + lscore.toFixed(2) + '（' + (link.scorecard.stance || '中性') + '）');
    }
    if (riskScore != null) { add(riskScore, 100, '风控体检 ' + riskScore + '/100（' + (risk.verdict || '未知') + '）'); }
    if (num(breadth.limit_up) != null && num(breadth.limit_down) != null) {
      var lu = num(breadth.limit_up), ld = num(breadth.limit_down);
      var s = ld > lu ? 25 : 75;
      add(s, 100, '涨停 ' + lu + ' 家 / 跌停 ' + ld + ' 家');
    }
    var riskIndex = max > 0 ? Math.round(100 - (points / max) * 100) : null;

    var stance = 'neutral', label = '待确认', color = 'var(--muted)', cap = null;
    if (riskIndex != null) {
      if (riskIndex >= 62) { stance = 'defend'; label = '防守'; color = 'var(--red)'; cap = 30; }
      else if (riskIndex <= 38) { stance = 'attack'; label = '进攻'; color = 'var(--green)'; cap = 70; }
      else { stance = 'neutral'; label = '均衡'; color = 'var(--gold)'; cap = 50; }
    }
    if (md && md.risk_tags && md.risk_tags.length) {
      md.risk_tags.slice(0, 3).forEach(function (t) { reasons.push((t.level === 'high' ? '⚠ ' : '· ') + t.text); });
    }
    var con = panel && panel.consensus;
    if (con && con.actionable) {
      stance = con.stance === 'attack' ? 'attack' : con.stance === 'defend' ? 'defend' : 'neutral';
      label = con.label || label;
      color = stance === 'attack' ? 'var(--green)' : stance === 'defend' ? 'var(--red)' : 'var(--gold)';
      if (con.position_cap != null) cap = con.position_cap;
      reasons.push('主脑六专家共识 ' + fmt(con.score, 2) + '，分歧 ' + fmt(con.disagreement, 2) + '，结论「' + (con.label || '--') + '」');
      if (con.veto && con.veto_reasons && con.veto_reasons.length) {
        reasons.push('风控否决：' + (con.veto_reasons[0].reason || '存在硬否决'));
      }
    }
    if (sentiment && sentiment.risk_flags && sentiment.risk_flags.length) {
      sentiment.risk_flags.slice(0, 2).forEach(function (flag) { reasons.push('情绪旗标：' + (flag.text || flag.code)); });
    }
    return {
      riskIndex: riskIndex, stance: stance, label: label, color: color, cap: cap,
      reasons: reasons, score: score, upRatio: upRatio, lscore: lscore,
      breadth: breadth, md: md, risk: risk, sentiment: sentiment, panel: panel
    };
  }

  function renderLoading() {
    var grid = $('dd-grid');
    if (!grid) return;
    grid.innerHTML = '<div class="dd-card span12"><div class="dd-skeleton" style="width:40%"></div>' +
      '<div class="dd-skeleton" style="width:80%"></div><div class="dd-skeleton" style="width:65%"></div>' +
      '<div class="dd-skeleton" style="width:72%"></div></div>';
  }

  /* ---------------- 各卡片渲染 ---------------- */
  function card(title, en, body, span, link) {
    /* link 支持两种：{ act } 走页面内动作，{ href } 走真实跳转（跨页模块用）。 */
    var action = '';
    if (link) {
      action = link.href
        ? '<a class="dd-link" href="' + esc(link.href) + '">' + esc(link.text) + ' →</a>'
        : '<button class="dd-link" data-act="' + esc(link.act) + '">' + esc(link.text) + ' →</button>';
    }
    return '<article class="dd-card' + (span ? ' ' + span : '') + '">' +
      '<div class="dd-card-head"><strong>' + title + '</strong><em>' + en + '</em>' +
      action +
      '</div>' + body + '</article>';
  }

  function badge(text, kind) { return '<span class="dd-badge ' + (kind || '') + '">' + esc(text) + '</span>'; }

  function shortTime(value) {
    if (!value) return '--';
    var s = String(value);
    return s.length >= 16 ? s.slice(11, 16) : s;
  }

  function captureDrafts() {
    var p = $('dd-plan-note'), r = $('dd-review-note');
    if (p) state.drafts.plan = p.value;
    if (r) state.drafts.review = r.value;
  }

  /* 快照一致性：优先看 trace_id（同一份 MarketPacket）。
     不同 trace 时退一步看数据时点：180 秒内视为同一行情窗口（专家团 TTL 300s，
     比快照 55s 长，稳态下会有一次快照的代差），超出才标「快照时差」。 */
  function snapshotConsistency() {
    var se = state.data.sentiment, ex = state.data.experts;
    var t1 = se && se.ok ? se.trace_id : null;
    var t2 = ex && ex.ok ? ex.trace_id : null;
    if (!t1 || !t2) return 'unknown';
    if (t1 === t2) return 'aligned';
    var a = Date.parse((se && se.as_of) || ''), b = Date.parse((ex && ex.as_of) || '');
    if (isFinite(a) && isFinite(b) && Math.abs(a - b) <= 180000) return 'window';
    return 'skewed';
  }

  function renderPulse() {
    var host = $('dd-pulse');
    if (!host) return;
    var d = state.data;
    var md = d.market && d.market.ok ? d.market.data : null;
    var survey = d.survey && d.survey.ok ? d.survey : null;
    var sentiment = d.sentiment && d.sentiment.ok ? d.sentiment : null;
    var cov = d.coverage && d.coverage.ok ? d.coverage : null;
    var full = cov && cov.ashare ? cov.ashare : null;
    if (!md && !survey && !sentiment) {
      host.innerHTML = '<span class="dd-pulse-label">MARKET PULSE</span><span class="dd-pulse-loading">' + (state.loading ? '正在建立数据连接…' : '市场快照暂不可用') + '</span>';
      return;
    }
    var idx = null;
    ((md && md.indices) || []).forEach(function (item) { if (item.code === 'sh000001') idx = item; });
    var b = (full && full.quotes) || (md && md.breadth) || {};
    var turnover = full && full.quotes && full.quotes.amount != null ? amt(full.quotes.amount) : (md && md.turnover ? amt(md.turnover.amount) : '--');
    var regime = survey && survey.regime ? survey.regime : null;
    var coverage = full && full.quality ? num(full.quality.quote_coverage) : (md && md.coverage ? num(md.coverage.ratio) : null);
    var items = '';
    if (idx) {
      var hasIndex = num(idx.price) > 0;
      items += '<div class="dd-pulse-item"><span>上证指数</span><b>' + (hasIndex ? fmt(idx.price, 2) : '--') + '</b><em class="' + (hasIndex ? chgClass(idx.chg) : 'muted-text') + '">' + (hasIndex ? pct(idx.chg) : '暂无行情') + '</em></div>';
    }
    if (b.up != null) items += '<div class="dd-pulse-item"><span>市场宽度</span><b class="up-text">' + b.up + '</b><i>/</i><b class="down-text">' + b.down + '</b></div>';
    if (b.limit_up != null) items += '<div class="dd-pulse-item"><span>涨停 / 跌停</span><b>' + b.limit_up + ' / ' + b.limit_down + '</b></div>';
    items += '<div class="dd-pulse-item dd-pulse-turnover"><span>样本成交</span><b>' + turnover + '</b></div>';
    if (sentiment && num(sentiment.score) != null) items += '<div class="dd-pulse-item dd-pulse-sentiment"><span>情绪 v1</span><b>' + fmt(sentiment.score, 1) + '</b><em>' + esc(sentiment.status || '--') + '</em></div>';
    if (regime) items += '<div class="dd-pulse-item dd-pulse-regime"><span>市场状态</span><b>' + esc(regime.status || '--') + '</b><em>' + (regime.confidence == null ? '' : '置信 ' + regime.confidence + '%') + '</em></div>';
    host.innerHTML = '<span class="dd-pulse-label">MARKET PULSE</span>' + items +
      '<span class="dd-pulse-fresh"><i class="' + (state.loading ? 'is-loading' : '') + '"></i>' + (state.loading ? '同步中' : '快照 ' + shortTime(md && md.as_of)) + (coverage == null ? '' : ' · 覆盖 ' + coverage + '%') + (turnover === '--' ? '' : ' · 成交 ' + turnover) + '</span>' +
      '<span class="dd-pulse-sync ' + (state.streamStatus === 'live' ? 'is-live' : '') + '" title="' + (state.streamStatus === 'live' ? '数据总线已连接，刷新即时推送' : 'SSE 未连接，使用 60 秒轮询兜底') + '">' +
        (state.streamStatus === 'live' ? '● 实时' : state.streamStatus === 'reconnect' ? '○ 重连中' : '○ 轮询') +
        (snapshotConsistency() === 'skewed' ? ' · 快照时差' : snapshotConsistency() === 'aligned' ? ' · 同快照'
          : snapshotConsistency() === 'window' ? ' · 同窗口' : '') +
      '</span>' +
      '<button class="dd-pulse-go" data-act="open-survey">判断层 →</button>';
  }

  function render() {
    var host = $('decision-mount');
    if (!host) return;
    var grid = $('dd-grid');
    if (!grid) return;
    captureDrafts();
    var d = state.data;
    var md = d.market && d.market.ok ? d.market.data : null;
    var v = computeVerdict();
    var mode = activeMode();

    var keys = Object.keys(ENDPOINTS);
    var okCount = 0, badCount = 0, pendingCount = 0, errors = [];
    keys.forEach(function (key) {
      var r = state.resources[key] || {};
      if (r.status === 'ok') okCount += 1;
      else if (r.status === 'error' || r.status === 'stale') { badCount += 1; errors.push((ENDPOINTS[key].label || key) + ': ' + (r.error || '不可用')); }
      else pendingCount += 1;
    });
    var updated = $('dd-updated');
    if (updated) {
      updated.textContent = '行情 ' + shortTime(md && md.as_of) + ' · 本地 ' + nowHM()
        + (state.streamStatus === 'live' ? ' · 实时推送' : state.streamStatus === 'reconnect' ? ' · 重连中' : '');
      updated.title = state.streamStatus === 'live'
        ? '已订阅数据总线：情绪 / 专家团 / 扫描刷新即时推送'
        : '轮询模式：每 60 秒同步一次；SSE 断开时自动重连';
    }
    var quality = $('dd-quality');
    if (quality) {
      /* 「通道通了」不等于「内容可用」：指数/情绪缺失时单独降级，避免误报数据质量优。 */
      var marketIncomplete = !!md && (md.stale || (md.data_quality && (md.data_quality.level !== 'good' || md.data_quality.judgment_available === false)));
      quality.className = 'dd-chip ' + ((badCount || marketIncomplete) ? 'warn' : (pendingCount ? '' : 'ok'));
      quality.textContent = state.loading ? '接入 ' + okCount + '/' + keys.length + (badCount ? ' · ' + badCount + ' 异常' : '')
        : (badCount ? badCount + ' 源异常 · ' + okCount + ' 正常'
          : (!md ? '行情未返回 · 通道 ' + okCount + '/' + keys.length
            : (md.stale ? '历史快照 · 通道 ' + okCount + '/' + keys.length
              : (marketIncomplete ? '通道 ' + okCount + '/' + keys.length + ' · 样本 ' + (num(md.coverage && md.coverage.ratio) == null ? '--' : num(md.coverage.ratio) + '%') : '数据已接通 ' + okCount + '/' + keys.length))));
      var qualityNotes = errors.slice();
      if (md && md.data_quality && md.data_quality.message) qualityNotes.push(md.data_quality.message);
      if (md && md.warning) qualityNotes.push(md.warning);
      quality.title = qualityNotes.join('\n') || '全部数据通道正常';
    }
    var refresh = $('dd-refresh');
    if (refresh) {
      refresh.disabled = state.loading;
      refresh.innerHTML = '<span class="dd-refresh-icon">↻</span> ' + (state.loading ? '同步中' : '同步数据');
    }
    renderPulse();

    var html = '';
    html += renderVerdict(v, mode);
    html += renderExperts();
    html += renderWorld();
    html += renderHeat(md, v);
    html += renderCapital(md, d);
    html += renderCoverage();
    html += renderScan();
    html += renderRotation();
    html += renderMoneyFlow();
    html += renderSentiment();
    html += renderSectorTrends();
    html += renderCharRadar();
    html += renderHoldings(d);
    html += renderIntel(d);
    html += renderLhb(d);
    html += renderReview(d);
    html += renderPlan(v, mode);
    html += renderDataSources();
    grid.innerHTML = html;

    renderHistory();
    var area = $('dd-plan-note');
    if (area) area.value = state.drafts.plan || (state.plan && state.plan.note) || '';
    var rv = $('dd-review-note');
    if (rv) {
      var reviewNote = state.drafts.review;
      if (!reviewNote) {
        var saved = hs(REVIEW_PREFIX + today());
        if (saved) { try { reviewNote = JSON.parse(saved).note || ''; } catch (e) { } }
      }
      rv.value = reviewNote || '';
    }
  }

  function renderVerdict(v, mode) {
    var ringColor = v.riskIndex == null ? 'var(--muted)' : v.riskIndex >= 62 ? 'var(--red)' : v.riskIndex <= 38 ? 'var(--green)' : 'var(--gold)';
    var pct = v.riskIndex == null ? 0 : v.riskIndex;
    var stanceClass = v.stance === 'attack' ? 'attack' : v.stance === 'defend' ? 'defend' : 'neutral';
    var body = '<div class="dd-verdict">' +
      '<div class="dd-score-ring" style="--pct:' + pct + ';--ring-color:' + ringColor + '"><span>' + (v.riskIndex == null ? '--' : v.riskIndex) + '<small>风险指数</small></span></div>' +
      '<div class="dd-verdict-main">' +
        '<div class="dd-stance ' + stanceClass + '">' + v.label + ' <small>' + (mode === 'pre' ? '盘前计划' : mode === 'intra' ? '盘中纪律' : '盘后复盘') + '</small></div>' +
        '<div class="dd-reasons">' + v.reasons.slice(0, 5).map(function (r) { return '<div><i>·</i>' + esc(r) + '</div>'; }).join('') + '</div>' +
      '</div>' +
      '<div class="dd-stat-grid" style="flex:1;min-width:220px">' +
        stat('建议仓位上限', v.cap == null ? '--' : '≤ ' + v.cap + '%', 'cap') +
        stat(v.sentiment ? '统一情绪 v1' : '情绪评分', v.score == null ? '--' : v.score + '/100', '') +
        stat('专家共识', (v.panel && v.panel.consensus && v.panel.consensus.label) ? v.panel.consensus.label : '--', '') +
        stat('上涨占比', v.upRatio == null ? '--' : v.upRatio.toFixed(1) + '%', v.upRatio != null && v.upRatio < 30 ? 'bad' : '') +
      '</div></div>' +
      '<div class="dd-note">结论由市场评分、市场宽度、全球联动、涨跌停结构与风控体检加权得出；风险指数越高越应收缩仓位。仅供参考，不构成投资建议。</div>';
    return card('今日决策结论', 'VERDICT', body, 'span12');
  }

  function stat(label, value, kind) {
    return '<div class="dd-stat ' + (kind || '') + '"><b>' + esc(value) + '</b><span>' + esc(label) + '</span></div>';
  }

  function evidenceValue(item) {
    if (!item) return '--';
    var value = item.value;
    if (value == null) return '--';
    if (typeof value === 'number') value = Math.abs(value) >= 1000 ? Math.round(value).toLocaleString('zh-CN') : Math.round(value * 100) / 100;
    return String(value) + (item.unit || '');
  }

  function renderExperts() {
    var panel = state.data.experts && state.data.experts.ok ? state.data.experts : null;
    if (!panel || !(panel.experts || []).length) {
      return card('主脑专家团', 'EXPERT PANEL', '<div class="dd-empty">专家研判加载中 / 暂不可用</div>', 'span12');
    }
    var con = panel.consensus || {};
    var dl = panel.deliberation || null;
    var cv = (dl && dl.cross_validation) || null;
    var stanceCls = con.stance === 'attack' ? 'attack' : con.stance === 'defend' ? 'defend' : 'neutral';
    var body = '<div class="dd-expert-consensus">' +
      '<div class="dd-consensus-main"><span>六专家共识</span><b class="' + stanceCls + '">' + esc(con.label || '--') + '</b>' +
      '<em class="' + stanceCls + '">' + (num(con.score) == null ? '--' : (num(con.score) > 0 ? '+' : '') + fmt(con.score, 2)) + '</em></div>' +
      '<div class="dd-consensus-item"><span>分歧指数</span><b>' + fmt(con.disagreement, 2) + '</b></div>' +
      '<div class="dd-consensus-item"><span>仓位上限</span><b>' + (con.position_cap == null ? '--' : '≤ ' + con.position_cap + '%') + '</b></div>' +
      '<div class="dd-consensus-item"><span>置信度</span><b>' + (num(con.confidence) == null ? '--' : Math.round(num(con.confidence) * 100) + '%') + '</b></div>' +
      '<div class="dd-consensus-item"><span>数据质量</span><b>' + (num(con.data_quality) == null ? '--' : Math.round(num(con.data_quality) * 100) + '%') + '</b></div>' +
      (con.veto ? '<span class="dd-badge bad">风控否决</span>' : cv ? '<span class="dd-badge good">两轮互评</span>' : '<span class="dd-badge good">规则 v1</span>') +
      '</div>';
    if (cv) {
      body += '<div class="dd-delib">' +
        '<div class="dd-delib-flow">' +
        '<span class="dd-delib-step">① 独立研判</span><i>→</i>' +
        '<span class="dd-delib-step">② 匿名互评</span><i>→</i>' +
        '<span class="dd-delib-step">③ 反方质询</span><i>→</i>' +
        '<span class="dd-delib-step">④ 主脑汇总</span>' +
        '</div>' +
        '<div class="dd-delib-stats">' +
        '<span class="good">认同 ' + (cv.endorse || 0) + '</span>' +
        '<span class="warn">修正 ' + (cv.revise || 0) + '</span>' +
        '<span class="bad">坚持 ' + (cv.uphold || 0) + '</span>' +
        '<span class="muted-text">一致率 ' + (num(cv.agreement_rate) == null ? '--' : Math.round(num(cv.agreement_rate) * 100) + '%') + '</span>' +
        '</div></div>';
    }
    var verdictMap = {};
    (dl && dl.reviews || []).forEach(function (r) { verdictMap[r.expert_id] = r; });
    body += '<div class="dd-expert-grid">';
    (panel.experts || []).forEach(function (item) {
      var cls = item.stance === 'attack' ? 'attack' : item.stance === 'defend' ? 'defend' : 'neutral';
      var ev = (item.evidence || [])[0];
      var rv = verdictMap[item.expert_id];
      var rvBadge = rv ? '<span class="dd-rv ' + rv.verdict + '" title="' + esc(rv.reason || '') + '">' +
        (rv.verdict === 'endorse' ? '认同' : rv.verdict === 'revise' ? '修正 ' + (num(rv.confidence_delta) > 0 ? '+' : '') + fmt(rv.confidence_delta, 2) : '坚持') +
        '</span>' : '';
      var changed = rv && rv.verdict === 'revise' && rv.self_stance !== item.stance;
      body += '<article class="dd-expert-tile ' + cls + (item.veto ? ' is-veto' : '') + '">' +
        '<div class="dd-expert-hd"><span>' + esc(item.expert_id) + '</span><b>' + esc(item.name) + '</b>' +
        rvBadge + '<em>' + Math.round((num(item.confidence) || 0) * 100) + '%</em></div>' +
        '<p title="' + esc(item.thesis) + '">' + esc(item.thesis) + '</p>' +
        '<div class="dd-expert-meta"><span>' + (item.stance === 'attack' ? '进攻' : item.stance === 'defend' ? '防守' : '中性') + ' · ≤' + (item.position_cap == null ? '--' : item.position_cap + '%') + '</span>' +
        '<i>' + esc((ev && ev.metric) || '') + ' ' + esc(evidenceValue(ev)) + '</i></div>' +
        (changed ? '<div class="dd-expert-shift">首轮 ' + (rv.self_stance === 'attack' ? '进攻' : rv.self_stance === 'defend' ? '防守' : '中性') + ' → 互评后修正</div>' : '') +
        (item.veto ? '<div class="dd-expert-veto">' + esc(item.veto_reason || '风控否决') + '</div>' : '') +
        '</article>';
    });
    body += '</div>';
    if (dl && (dl.audit_queries || []).length) {
      body += '<div class="dd-delib-queries"><b>反方质询 · E6</b>' + dl.audit_queries.slice(0, 4).map(function (q) {
        return '<div><span>' + esc(q.to_alias || '') + ' ' + esc(q.to_expert || '') + '</span>' + esc(q.question || '') + '</div>';
      }).join('') + '</div>';
    }
    if ((panel.minority || []).length) {
      body += '<div class="dd-expert-dissent"><b>保留异议</b>' + panel.minority.map(function (m) {
        return '<span>' + esc(m.name) + ' · ' + (m.stance === 'attack' ? '进攻' : m.stance === 'defend' ? '防守' : '中性') + '</span>';
      }).join('') + '</div>';
    }
    body += '<div class="dd-note">' + (dl
      ? '规则专家 v2：六份意见基于同一快照独立生成后进行匿名两轮互评，修正需反对票信心超过支持票 1.15 倍，主脑再按置信度加权汇总。'
      : '规则专家 v1：六份意见基于同一快照独立生成，再由主脑按置信度聚合。') +
      (panel.as_of ? ' 快照 ' + esc(shortTime(panel.as_of)) : '') +
      (panel.chain && panel.chain.packet_age_s != null ? ' · 链路 ' + fmt(panel.chain.packet_age_s, 0) + 's' : '') + '</div>';
    return card('主脑专家团 · 六视角交叉验证', 'EXPERT PANEL', body, 'span12', { act: 'open-survey', text: '判断层' });
  }

  function renderCoverage() {
    var cov = state.data.coverage && state.data.coverage.ok ? state.data.coverage : null;
    if (!cov) return card('全市场与全球宏观', 'DATA COVERAGE', '<div class="dd-empty">全市场覆盖加载中 / 暂不可用</div>', 'span12');
    var a = cov.ashare || {}, q = a.quotes || {}, f = a.fundamentals || {}, g = cov.global || {}, m = cov.macro || {};
    var industries = (a.industries || []).slice(0, 6);
    var indices = (g.indices || []).filter(function (x) { return x.price != null; }).slice(0, 6);
    var sectors = (g.industries || []).filter(function (x) { return x.available; }).slice(0, 6);
    var rates = (m.global || []).filter(function (x) { return x.value != null && (x.frequency === 'daily' || x.key === 'ust_10y_2y'); }).slice(0, 5);
    var annual = (m.global || []).filter(function (x) { return x.value != null && x.frequency === 'annual'; }).slice(0, 6);
    var body = '<div class="dd-cover-stats">' +
      stat('有效报价', (q.valid == null ? '--' : q.valid) + ' / ' + (a.total_universe == null ? '--' : a.total_universe), '') +
      stat('PE / PB 中位', (f.pe_median == null ? '--' : fmt(f.pe_median, 1)) + ' / ' + (f.pb_median == null ? '--' : fmt(f.pb_median, 2)), '') +
      stat('ROE 中位', f.roe_median == null ? '--' : fmt(f.roe_median, 1) + '%', f.roe_median != null && f.roe_median < 0 ? 'bad' : '') +
      stat('总市值', f.market_cap_yi == null ? '--' : (f.market_cap_yi / 1e4).toFixed(1) + ' 万亿', '') +
      '</div>';
    body += '<div class="dd-cover-grid">';
    body += '<section class="dd-cover-col"><h5>A 股内部结构</h5>' +
      '<div class="dd-cover-row"><span>涨跌家数</span><b class="up-text">' + (q.up == null ? '--' : q.up) + '</b><i>/</i><b class="down-text">' + (q.down == null ? '--' : q.down) + '</b></div>' +
      '<div class="dd-cover-row"><span>涨停 / 跌停</span><b>' + (q.limit_up == null ? '--' : q.limit_up) + ' / ' + (q.limit_down == null ? '--' : q.limit_down) + '</b></div>' +
      '<div class="dd-cover-row"><span>中位涨跌</span><b class="' + chgClass(q.median_chg) + '">' + pct(q.median_chg) + '</b></div>' +
      '<div class="dd-cover-row"><span>样本成交</span><b>' + amt(q.amount) + '</b></div>' +
      '<div class="dd-cover-sub">行业基本面（按成交额）</div>' +
      industries.map(function (s) { return '<div class="dd-cover-line"><span>' + esc(s.name) + '</span><em class="' + chgClass(s.avg_chg) + '">' + pct(s.avg_chg) + '</em><i>ROE ' + (s.median_roe == null ? '--' : fmt(s.median_roe, 1) + '%') + '</i></div>'; }).join('') +
      '</section>';
    body += '<section class="dd-cover-col"><h5>全球指数 / 行业</h5>' +
      indices.map(function (x) { return '<div class="dd-cover-line"><span>' + esc(x.name) + '</span><em class="' + chgClass(x.chg) + '">' + pct(x.chg) + '</em><i>' + fmt(x.price, x.price > 1000 ? 0 : 2) + '</i></div>'; }).join('') +
      '<div class="dd-cover-sub">行业风险偏好代理</div>' +
      sectors.map(function (x) { return '<div class="dd-cover-line"><span>' + esc(x.name) + '</span><em class="' + chgClass(x.chg) + '">' + pct(x.chg) + '</em><i>' + esc(x.symbol) + '</i></div>'; }).join('') +
      '</section>';
    body += '<section class="dd-cover-col"><h5>全球宏观</h5>' +
      rates.map(function (x) { return '<div class="dd-cover-line"><span>' + esc(x.label) + '</span><b>' + fmt(x.value, 2) + '</b><i>' + esc(x.period || '') + '</i></div>'; }).join('') +
      '<div class="dd-cover-sub">年度宏观（含发布滞后）</div>' +
      annual.map(function (x) { return '<div class="dd-cover-line"><span>' + esc(x.label) + '</span><b>' + fmt(x.value, 1) + '%</b><i>' + esc(x.period || '') + '</i></div>'; }).join('') +
      '</section></div>';
    body += '<div class="dd-cover-sources">' + (cov.sources || []).slice(0, 8).map(function (s) {
      return '<span class="' + (s.status === 'ok' ? 'ok' : 'warn') + '" title="' + esc(s.auth || '') + '">' + esc(s.label) + ' · ' + (s.records == null ? '--' : s.records) + '</span>';
    }).join('') + '</div>';
    body += '<div class="dd-note">A 股报价 ' + (a.quality ? esc(a.quality.quote_coverage) : '--') + '% / 基本面覆盖 ' + (a.quality ? esc(a.quality.financial_coverage) : '--') + '%。国家 ETF 与行业 ETF 为风险偏好代理，不冒充当地官方指数。</div>';
    return card('全市场与全球宏观 · DATA COVERAGE', 'DATA COVERAGE', body, 'span12', { act: 'open-survey', text: '判断层' });
  }

  function renderWorld() {
    var d = state.data;
    var assets = d.globalAssets && d.globalAssets.ok ? d.globalAssets : null;
    var link = d.linkage && d.linkage.ok ? d.linkage : null;
    var body = '';
    if (link && link.scorecard) {
      var sc = link.scorecard;
      var kind = sc.composite_score < -1 ? 'bad' : sc.composite_score > 1 ? 'good' : 'warn';
      body += '<div class="dd-row"><span class="nm">全球联动评分</span><span class="vl">' + fmt(sc.composite_score, 2) + '</span>' + badge(sc.stance || '中性', kind) + '</div>';
      (sc.items || []).slice(0, 3).forEach(function (it) {
        body += '<div class="dd-row"><span class="nm">' + esc(it.name) + '</span><span class="vl ' + chgClass(it.score) + '">' + fmt(it.score, 2) + '</span><span class="tag">' + esc(it.stance || '') + '</span></div>';
      });
    }
    if (assets && assets.categories) {
      var pick = ['us', 'asia', 'commodity', 'forex'];
      var names = assets.category_names || {};
      pick.forEach(function (k) {
        var list = assets.categories[k] || [];
        list.slice(0, 1).forEach(function (it) {
          if (!it.available) return;
          body += '<div class="dd-row"><span class="nm">' + esc(it.name) + ' <span class="muted-text" style="font-size:10px">' + esc(names[k] || k) + '</span></span>' +
            '<span class="vl">' + fmt(it.price, it.price > 1000 ? 0 : 2) + '</span><span class="vl ' + chgClass(it.chg) + '">' + pct(it.chg, 2) + '</span></div>';
        });
      });
    }
    if (!body) body = '<div class="dd-empty">全球资产数据暂不可用</div>';
    body += '<div class="dd-note">联动评分为隔夜外盘对 A 股的领先相关加权结果，负值表示拖累。</div>';
    return card('世界局势 / 隔夜外盘', 'GLOBAL', body, 'span4', { act: 'open-market', text: '事实层' });
  }

  function renderSentiment() {
    var se = state.data.sentiment && state.data.sentiment.ok ? state.data.sentiment : null;
    if (!se) return card('市场情绪 v1', 'SENTIMENT V1', '<div class="dd-empty">统一情绪加载中 / 暂不可用</div>', 'span12');
    var scopeBadge = se.data_scope === 'full_a_share'
      ? '<span class="dd-badge good" title="' + esc(se.scope_label || '') + '">全市场口径</span>'
      : se.data_scope === 'core_sample'
        ? '<span class="dd-badge warn" title="' + esc(se.scope_label || '') + '">核心样本</span>'
        : '';
    var body = '<div class="dd-cover-stats">' +
      stat('情绪总分', se.score == null ? '--' : fmt(se.score, 1), se.score != null && se.score < 30 ? 'bad' : '') +
      stat('状态', se.status || '--', '') +
      stat('维度覆盖', Math.round((num(se.coverage) || 0) * 100) + '%', '') +
      stat('置信度', se.confidence_level || '--', '') +
      '</div>';
    if (scopeBadge) body += '<div class="dd-sent-scope">' + scopeBadge + '<span>' + esc(se.scope_label || '') + '</span></div>';
    if ((se.risk_flags || []).length) {
      body += '<div class="dd-flags">' + se.risk_flags.map(function (f) {
        return '<span class="' + (f.level === 'high' ? 'bad' : 'warn') + '">' + esc(f.text) + '</span>';
      }).join('') + '</div>';
    }
    body += '<div class="dd-note">sentiment.v1 统一口径；六维明细与走势见事实层，决策层只保留判断所需摘要。' +
      (se.message ? ' ' + esc(se.message) : '') + '</div>';
    return card('情绪摘要 · 决策口径', 'SENTIMENT', body, 'span6', { act: 'open-sentiment', text: '事实层 · 六维' });
  }

  function renderHeat(md, v) {
    var cov = state.data.coverage && state.data.coverage.ok ? state.data.coverage : null;
    var b = (cov && cov.ashare && cov.ashare.quotes) || (md && md.breadth) || {};
    var up = num(b.up) || 0, down = num(b.down) || 0, flat = num(b.flat) || 0;
    var total = up + down + flat;
    var body = '';
    if (total > 0) {
      body += '<div class="dd-bar"><div class="dd-bar-label"><span class="up-text">上涨 ' + up + '</span><span class="muted-text">平 ' + flat + '</span><span class="down-text">下跌 ' + down + '</span></div>' +
        '<div class="dd-bar-track"><div class="dd-bar-up" style="width:' + (up / total * 100) + '%"></div>' +
        '<div class="dd-bar-flat" style="width:' + (flat / total * 100) + '%"></div>' +
        '<div class="dd-bar-down" style="width:' + (down / total * 100) + '%"></div></div></div>';
    }
    body += '<div class="dd-stat-grid">' +
      stat('涨停', b.limit_up == null ? '--' : b.limit_up + ' 家', num(b.limit_up) > 0 && num(b.limit_up) < num(b.limit_down) ? 'bad' : 'good') +
      stat('跌停', b.limit_down == null ? '--' : b.limit_down + ' 家', num(b.limit_down) > num(b.limit_up) ? 'bad' : '') +
      stat('成交额', b.amount != null ? amt(b.amount) : (md && md.turnover ? amt(md.turnover.amount) : '--'), '') +
      stat('中位涨跌', b.median_chg == null ? '--' : pct(b.median_chg), b.median_chg < 0 ? 'bad' : 'good') +
      '</div>';
    var idx = (md && md.indices) || [];
    if (idx.length) {
      body += '<div class="dd-list">' + idx.slice(0, 4).map(function (i) {
        var hasQuote = num(i.price) > 0;
        return '<div class="dd-row"><span class="nm">' + esc(i.name) + '</span><span class="vl">' + (hasQuote ? fmt(i.price, 2) : '--') + '</span><span class="vl ' + chgClass(i.chg) + '">' + (hasQuote ? pct(i.chg) : '暂无行情') + '</span></div>';
      }).join('') + '</div>';
    }
    if (!md) body += '<div class="dd-empty">大盘数据暂不可用</div>';
    return card('市场温度', 'BREADTH', body, 'span4', { act: 'open-market', text: '事实层' });
  }

  function renderCapital(md, d) {
    var north = d.north && d.north.ok ? d.north.data : null;
    var sectors = [];
    if (md && md.sectors) {
      sectors = md.sectors.slice().sort(function (a, b) { return (num(b.avg_chg) || 0) - (num(a.avg_chg) || 0); }).slice(0, 6);
    }
    var body = '';
    if (north) {
      body += '<div class="dd-stat-grid">' +
        stat('沪股通上涨', north.hgt_up == null ? '--' : north.hgt_up, north.hgt_up > north.hgt_down ? 'good' : 'bad') +
        stat('沪股通下跌', north.hgt_down == null ? '--' : north.hgt_down, 'bad') +
        stat('深股通上涨', north.sgt_up == null ? '--' : north.sgt_up, north.sgt_up > north.sgt_down ? 'good' : 'bad') +
        stat('披露状态', north.disclosed ? '实时' : '受限', north.disclosed ? 'good' : 'warn') +
        '</div>';
    }
    if (sectors.length) {
      body += '<div class="dd-list"><div class="dd-bar-label" style="margin:4px 0 2px"><span>强势板块</span><span class="muted-text">涨跌幅 / 成交额</span></div>' +
        sectors.slice(0, 5).map(function (s) {
          var chg = num(s.avg_chg);
          return '<div class="dd-row dd-row-link" data-act="open-sector" data-key="' + esc(s.key) + '" title="在策略选股中打开该板块"><span class="nm">' + esc(s.icon || '') + ' ' + esc(s.name) + '</span><span class="vl ' + chgClass(chg) + '">' + pct(chg) + '</span><span class="muted-text" style="font-size:10px">' + amt(s.amount) + '</span></div>';
        }).join('') + '</div>';
    }
    if (!body) body = '<div class="dd-empty">资金与板块数据暂不可用</div>';
    body += '<div class="dd-note">' + esc(north && north.note ? north.note : '北向资金自 2024-08 起不再实时披露，以成分股涨跌结构观察。') + '</div>';
    return card('资金流向 / 板块强弱', 'CAPITAL', body, 'span4', { act: 'open-market', text: '事实层' });
  }

  /* ---------------- 全市场扫描（market-scanner.v1） ---------------- */
  function renderScan() {
    var sc = state.data.scan && state.data.scan.ok ? state.data.scan : null;
    if (!sc) return card('全市场扫描', 'MARKET SCAN', '<div class="dd-empty">扫描层加载中 / 暂不可用</div>', 'span4');
    var c = sc.cross_section || {};
    var body = '';
    if (!c.available) {
      body = '<div class="dd-empty">' + esc(c.reason || '横截面样本不足') + '</div>';
    } else {
      var strength = num(c.strength_score);
      var sCls = strength == null ? '' : (strength >= 60 ? 'good' : strength <= 40 ? 'bad' : 'warn');
      body += '<div class="dd-stat-grid">' +
        stat('强弱分', strength == null ? '--' : fmt(strength, 1), sCls) +
        stat('中位涨跌', c.median_chg == null ? '--' : pct(c.median_chg), num(c.median_chg) > 0 ? 'up' : num(c.median_chg) < 0 ? 'down' : '') +
        stat('涨停', c.limit_up == null ? '--' : c.limit_up + ' 家', num(c.limit_up) > 0 ? 'up' : '') +
        stat('跌停', c.limit_down == null ? '--' : c.limit_down + ' 家', num(c.limit_down) > 0 ? 'down' : '') +
        '</div>';

      /* 涨幅分布：6 档直方条（红=涨，绿=跌） */
      var dist = c.distribution || {};
      var order = ['gt_5', 'p2_5', 'p0_2', 'n2_0', 'n5_2', 'lt_n5'];
      var maxPct = 0;
      order.forEach(function (k) { var p = num((dist[k] || {}).pct); if (p != null && p > maxPct) maxPct = p; });
      body += '<div class="dd-scan-dist">' + order.map(function (k) {
        var item = dist[k] || {};
        var p = num(item.pct) || 0;
        var isUp = k.charAt(0) !== 'n' && k !== 'lt_n5';
        var w = maxPct > 0 ? Math.max(2, p / maxPct * 100) : 0;
        return '<div class="dd-scan-dist-row"><span class="lb">' + esc(item.label || k) + '</span>' +
          '<span class="tk"><i class="' + (isUp ? 'up' : 'down') + '" style="width:' + w + '%"></i></span>' +
          '<span class="pc">' + fmt(p, 1) + '%</span></div>';
      }).join('') + '</div>';

      var cap = c.cap_tiers || {};
      var tv = c.turnover || {};
      body += '<div class="dd-list" style="margin-top:6px">' +
        '<div class="dd-row"><span class="nm">市值分化 大/中/小</span><span class="vl">' +
          [['big_over_1000yi', '大'], ['mid_100_1000yi', '中'], ['small_under_100yi', '小']].map(function (t) {
            var v = num((cap[t[0]] || {}).avg_chg);
            return '<em class="' + chgClass(v) + '" style="font-style:normal;margin-left:6px">' + t[1] + ' ' + (v == null ? '--' : pct(v)) + '</em>';
          }).join('') + '</span></div>' +
        '<div class="dd-row"><span class="nm">换手 &gt;10% / 5-10%</span><span class="vl">' +
          (tv.high_pct_over_10 == null ? '--' : tv.high_pct_over_10) + ' / ' + (tv.active_5_10 == null ? '--' : tv.active_5_10) + ' 只</span></div>' +
        '</div>';
    }
    var chain = sc.chain || {};
    body += '<div class="dd-note">全 A 横截面 · 样本 ' + (c.sample == null ? '--' : c.sample) + ' 只' +
      (chain.build_ms != null ? ' · 派生 ' + Math.round(chain.build_ms) + 'ms' : '') +
      ' · ' + esc(shortTime(sc.as_of)) + '</div>';
    return card('全市场扫描 · 横截面结构', 'MARKET SCAN', body, 'span4', { act: 'open-market', text: '事实层' });
  }

  /* ---------------- 板块轮动（排名变化 + 动量） ---------------- */
  function renderRotation() {
    var sc = state.data.scan && state.data.scan.ok ? state.data.scan : null;
    var rot = sc && sc.rotation;
    if (!rot || !rot.available) {
      return card('板块轮动', 'ROTATION', '<div class="dd-empty">' + esc((rot && rot.reason) || '轮动数据加载中 / 暂不可用') + '</div>', 'span4');
    }
    var inds = (rot.industries || []).slice(0, 6);
    var rising = 0;
    (rot.industries || []).forEach(function (x) { if ((num(x.avg_chg) || 0) > 0) rising += 1; });
    var sig = rot.signal || 'baseline';
    var sigCls = sig === 'continuity' ? 'good' : sig === 'churn' ? 'warn' : '';
    var body = '<div class="dd-stat-grid">' +
      stat('轮动信号', rot.signal_label || '--', sigCls) +
      stat('上涨行业', (rot.industries || []).length ? Math.round(rising / rot.industries.length * 100) + '%' : '--', rising * 2 > (rot.industries || []).length ? 'good' : 'bad') +
      stat('历史窗口', (rot.history_days == null ? '--' : rot.history_days) + ' 日', num(rot.history_days) >= 5 ? 'good' : 'warn') +
      '</div>';
    body += '<div class="dd-list" style="margin-top:6px">' +
      inds.map(function (x) {
        var chg = num(x.avg_chg);
        var rc = num(x.rank_change);
        var arrow = rc == null ? '<em class="dd-rk flat">新</em>'
          : rc > 0 ? '<em class="dd-rk up">↑' + rc + '</em>'
            : rc < 0 ? '<em class="dd-rk down">↓' + Math.abs(rc) + '</em>'
              : '<em class="dd-rk flat">—</em>';
        return '<div class="dd-row"><span class="nm">' + esc(x.name) + '</span>' + arrow +
          '<span class="vl ' + chgClass(chg) + '">' + (chg == null ? '--' : pct(chg)) + '</span>' +
          '<span class="muted-text" style="font-size:10px;min-width:44px;text-align:right">' + (num(x.amount_share) == null ? '--' : fmt(x.amount_share, 1) + '%') + '</span></div>';
      }).join('') + '</div>';
    body += '<div class="dd-note">排名变化相对上一交易日（正=上升）· ' + esc(rot.momentum_note || '') + '</div>';
    return card('板块轮动 · 主线与排名迁移', 'ROTATION', body, 'span4', { act: 'open-market', text: '事实层' });
  }

  /* ---------------- 资金流向（东财实测 / 注意力代理） ---------------- */
  function renderMoneyFlow() {
    var sc = state.data.scan && state.data.scan.ok ? state.data.scan : null;
    var mf = sc && sc.money_flow;
    if (!mf) return card('资金流向', 'MONEY FLOW', '<div class="dd-empty">资金流数据加载中 / 暂不可用</div>', 'span4');
    var isReal = mf.mode === 'eastmoney';
    var body = '<div class="dd-stat-grid">' +
      stat('通道', isReal ? '东财实测' : '注意力代理', isReal ? 'good' : 'warn') +
      (isReal
        ? stat('净流入合计', mf.net_total_yi == null ? '--' : fmt(mf.net_total_yi, 1) + ' 亿', num(mf.net_total_yi) >= 0 ? 'up' : 'down') +
          stat('流入/流出行业', (mf.industries || []).filter(function (x) { return (num(x.main_net_yi) || 0) > 0; }).length + ' / ' + (mf.industries || []).filter(function (x) { return (num(x.main_net_yi) || 0) < 0; }).length, '')
        : stat('净迁移', mf.net_attention == null ? '--' : fmt(mf.net_attention, 2) + '%', num(mf.net_attention) >= 0 ? 'up' : 'down') +
          stat('基线', (mf.baseline_days == null ? '--' : mf.baseline_days) + ' 日', num(mf.baseline_days) >= 1 ? '' : 'warn')) +
      '</div>';
    if (isReal) {
      var inflow = (mf.top_inflow || []).slice(0, 4);
      var outflow = (mf.top_outflow || []).slice(0, 2);
      body += '<div class="dd-list" style="margin-top:6px">' + inflow.map(function (x) {
        return '<div class="dd-row"><span class="nm">' + esc(x.name) + '</span><span class="vl up-text">' + (num(x.main_net_yi) == null ? '--' : fmt(x.main_net_yi, 1) + ' 亿') + '</span><span class="muted-text" style="font-size:10px">' + (num(x.chg) == null ? '' : pct(x.chg)) + '</span></div>';
      }).join('') + outflow.map(function (x) {
        return '<div class="dd-row"><span class="nm">' + esc(x.name) + '</span><span class="vl down-text">' + (num(x.main_net_yi) == null ? '--' : fmt(x.main_net_yi, 1) + ' 亿') + '</span><span class="muted-text" style="font-size:10px">' + (num(x.chg) == null ? '' : pct(x.chg)) + '</span></div>';
      }).join('') + '</div>';
    } else {
      var shifts = (mf.attention_shifts || []).slice(0, 5);
      if (shifts.length) {
        body += '<div class="dd-list" style="margin-top:6px">' + shifts.map(function (x) {
          var d = num(x.share_delta);
          return '<div class="dd-row"><span class="nm">' + esc(x.name) + '</span><span class="vl ' + (d >= 0 ? 'up-text' : 'down-text') + '">占比 ' + (d == null ? '--' : (d > 0 ? '+' : '') + fmt(d, 2) + 'pp') + '</span><span class="muted-text" style="font-size:10px">' + (num(x.chg) == null ? '' : pct(x.chg)) + '</span></div>';
        }).join('') + '</div>';
      } else {
        var tops = (mf.current_share_top || []).slice(0, 5);
        if (tops.length) {
          body += '<div class="dd-list" style="margin-top:6px">' + tops.map(function (x) {
            return '<div class="dd-row"><span class="nm">' + esc(x.name) + '</span><span class="vl">份额 ' + (num(x.amount_share) == null ? '--' : fmt(x.amount_share, 1) + '%') + '</span><span class="muted-text" style="font-size:10px">' + (num(x.chg) == null ? '' : pct(x.chg)) + '</span></div>';
          }).join('') + '</div>';
        } else {
          body += '<div class="dd-empty">代理基线尚未建立（需昨日板块快照）</div>';
        }
      }
    }
    var mfNote = mf.note || '';
    if (!isReal && !(mf.attention_shifts || []).length) {
      mfNote = '首日基线：展示当前成交份额（存量关注度），明日切换为占比迁移。' + mfNote;
    }
    body += '<div class="dd-note">' + esc(mfNote) + '</div>';
    return card('资金流向 · 主力净额 / 注意力迁移', 'MONEY FLOW', body, 'span4', { act: 'open-market', text: '事实层' });
  }

  function renderHoldings(d) {
    var pos = d.positions && d.positions.ok ? d.positions.data : null;
    var risk = d.riskHealth && d.riskHealth.ok ? d.riskHealth.data : null;
    var body = '';
    if (pos) {
      body += '<div class="dd-stat-grid">' +
        stat('总权益', amt(pos.total_equity), '') +
        stat('现金', amt(pos.cash), '') +
        stat('持仓市值', amt(pos.market_value), '') +
        stat('持仓数', (pos.items || []).length, '') +
        '</div>';
      if ((pos.items || []).length === 0) body += '<div class="dd-empty" style="padding:10px">当前空仓（纸面账户）</div>';
    }
    if (risk) {
      var checks = (risk.checks || []).filter(function (c) { return !c.pass; }).slice(0, 3);
      body += '<div class="dd-list"><div class="dd-row"><span class="nm">风控体检</span><span class="vl">' + (risk.score == null ? '--' : risk.score + '/100') + '</span>' + badge(risk.verdict || '未知', risk.verdict === 'pass' ? 'good' : risk.verdict === 'fail' ? 'bad' : 'warn') + '</div>';
      checks.forEach(function (c) {
        body += '<div class="dd-row"><span class="nm">' + esc(c.label) + '</span><span class="vl warn">' + esc(c.value == null ? '异常' : c.value) + '</span></div>';
      });
      body += '</div>';
    }
    if (!body) body = '<div class="dd-empty">持仓与风控数据暂不可用</div>';
    return card('持仓与风险', 'PORTFOLIO', body, 'span4', { act: 'open-automation', text: '自动化中心' });
  }

  function renderIntel(d) {
    var intel = d.intel && d.intel.ok ? d.intel.latest : null;
    var news = d.news && d.news.ok ? (d.news.data || []) : [];
    var body = '';
    if (intel) {
      body += '<div class="dd-row"><span class="nm">情报风险</span><span class="vl">' + (intel.risk_level || '--') + ' / ' + (intel.risk_score == null ? '--' : intel.risk_score) + '</span>' +
        badge(intel.ai_powered ? 'AI 已分析' : '规则引擎', intel.risk_score <= 30 ? 'good' : intel.risk_score <= 60 ? 'warn' : 'bad') + '</div>';
      if (intel.brief) body += '<div class="dd-sub">' + esc(intel.brief) + '</div>';
      (intel.headlines || []).slice(0, 2).forEach(function (h) { body += '<div class="dd-row"><span class="nm">' + esc(h) + '</span></div>'; });
      (intel.watch_points || []).slice(0, 2).forEach(function (w) { body += '<div class="dd-row"><span class="nm">👁 ' + esc(w) + '</span></div>'; });
    }
    if (news.length) {
      body += '<div class="dd-bar-label" style="margin-top:4px"><span>市场要闻</span><span class="muted-text">' + esc(d.news.source || '') + '</span></div>';
      news.slice(0, 4).forEach(function (n) {
        body += '<div class="dd-row"><span class="nm" title="' + esc(n.title) + '">' + esc(n.title) + '</span><span class="tag">' + esc(n.tag || '要闻') + '</span></div>';
      });
    }
    if (!body) body = '<div class="dd-empty">情报与要闻数据暂不可用</div>';
    return card('事件情报 / 世界局势', 'INTEL', body, 'span8', { act: 'open-market', text: '事实层' });
  }

  /* ---------------- 龙虎榜复盘（跨页模块的入口卡） ----------------
   * 不放进 ENDPOINTS：那份报告将近 100KB，每 60 秒跟着轮询拉一遍不划算。
   * 这里只在首次进入决策层时拉一次，之后点卡片跳转到独立页面看完整内容。
   */
  var lhbTried = false;

  function loadLhbOnce() {
    if (lhbTried) return;
    lhbTried = true;
    jget('/api/decision/lhb-review', 20000).then(function (payload) {
      state.data.lhb = payload;
      render();
    }).catch(function () {
      state.data.lhb = { ok: false, message: '龙虎榜复盘接口不可用' };
      render();
    });
  }

  function renderLhb(d) {
    var r = d.lhb || null;
    var link = { href: '/lhb-review.html', text: '查看完整复盘' };
    if (!r) {
      return card('龙虎榜复盘', 'LHB REVIEW',
        '<div class="dd-empty">正在读取最近一份复盘…</div>', 'span4', link);
    }
    if (!r.ok) {
      return card('龙虎榜复盘', 'LHB REVIEW',
        '<div class="dd-empty">' + esc(r.message || '该日暂无复盘')
        + '<br><span class="muted-text" style="font-size:10px">收盘后自动生成，营业部明细通常 18:00–19:00 补齐</span></div>',
        'span4', link);
    }
    var s = r.summary || {};
    var ins = r.insight || null;
    var byMotive = s.by_motive || {};
    var body = '<div class="dd-row"><span class="nm">' + esc(r.date || '--') + '</span>'
      + '<span class="vl">' + (s.records || 0) + ' 条 / ' + (s.stocks || 0) + ' 只</span>'
      + '<span class="muted-text" style="font-size:10px">买 ' + (s.buy_records || 0)
      + ' · 卖 ' + (s.sell_records || 0) + '</span></div>';

    var tops = Object.keys(byMotive).sort(function (a, b) { return byMotive[b] - byMotive[a]; }).slice(0, 4);
    if (tops.length) {
      body += '<div class="dd-row"><span class="nm">主因分布</span><span class="vl" style="font-size:11px">'
        + tops.map(function (k) { return esc(k) + ' ' + byMotive[k]; }).join(' · ') + '</span></div>';
    }
    // 热门因素：买卖两侧命中最多的因素各取第一名，直接回答「今天按什么理由买/卖」
    var fst = (ins && ins.factor_stats) || {};
    var fTopBuy = (fst.buy || [])[0], fTopSell = (fst.sell || [])[0];
    if (fTopBuy || fTopSell) {
      body += '<div class="dd-row"><span class="nm">热门因素</span><span class="vl" style="font-size:11px">'
        + (fTopBuy ? '买·' + esc(fTopBuy.label) + ' ' + fTopBuy.count : '')
        + (fTopBuy && fTopSell ? ' · ' : '')
        + (fTopSell ? '卖·' + esc(fTopSell.label) + ' ' + fTopSell.count : '')
        + '</span></div>';
    }
    if (ins && ins.totals) {
      body += '<div class="dd-row"><span class="nm">合力 / 对峙</span><span class="vl">'
        + ins.totals.consensus_buy + ' / ' + ins.totals.conflict + ' 只</span>'
        + '<span class="muted-text" style="font-size:10px">封板时刻级 ' + (s.seal_time_inferred || 0) + ' 条</span></div>';
    }
    // 卡片里放两条要点：「最大单笔」看极端动作，「最强合力」看共识方向，
    // 这两条合起来最能让读者判断当天资金的整体倾向。
    (ins && ins.highlights || []).slice(0, 2).forEach(function (h) {
      body += '<div class="dd-row"><span class="nm" style="min-width:auto">' + esc(h.title) + '</span></div>'
        + '<div class="dd-note" style="margin-top:2px">' + esc(h.detail) + '</div>';
    });
    return card('龙虎榜复盘', 'LHB REVIEW', body, 'span4', link);
  }

  function renderReview(d) {
    var r = d.review && d.review.ok ? d.review.data : null;
    var w = d.weekly && d.weekly.ok ? d.weekly.data : null;
    var body = '';
    if (r) {
      var eq = r.equity || {}, risk = r.risk || {}, q = r.quality || {};
      body += '<div class="dd-stat-grid">' +
        stat('账户权益', amt(eq.now), '') +
        stat('区间收益', eq.total_return_pct == null ? '--' : pct(eq.total_return_pct), num(eq.total_return_pct) >= 0 ? 'good' : 'bad') +
        stat('超额收益', r.excess_return_pct == null ? '--' : pct(r.excess_return_pct), num(r.excess_return_pct) >= 0 ? 'good' : 'bad') +
        stat('最大回撤', risk.maxdd_pct == null ? '--' : pct(-Math.abs(risk.maxdd_pct)), 'bad') +
        '</div>';
      body += '<div class="dd-row"><span class="nm">闭合交易 / 胜率</span><span class="vl">' + (q.trades == null ? '--' : q.trades) + ' 笔 / ' + (q.win_rate_pct == null ? '--' : q.win_rate_pct + '%') + '</span></div>';
      body += '<div class="dd-row"><span class="nm">流程可追溯</span><span class="vl">' + (r.process && r.process.traceability_pct != null ? r.process.traceability_pct + '%' : '--') + '</span></div>';
    }
    if (w) {
      body += '<div class="dd-row"><span class="nm">本周（' + esc(w.week) + '）</span><span class="vl ' + chgClass(w.pnl) + '">' + (w.pnl == null ? '--' : amt(w.pnl)) + '</span><span class="muted-text" style="font-size:10px">成交 ' + (w.trades == null ? '--' : w.trades) + ' 笔</span></div>';
    }
    var dq = d.decisionQuality && d.decisionQuality.ok ? d.decisionQuality.data : null;
    if (dq) {
      var all = dq.items || [];
      var real = all.filter(function (item) { return !item.is_drill && item.outcome_ret_pct != null; });
      var positive = real.filter(function (item) { return num(item.outcome_ret_pct) > 0; }).length;
      var drills = all.length - real.length;
      body += '<div class="dd-quality-block"><div><span>真实决策样本</span><b>' + real.length + '</b></div>' +
        '<div><span>正向结果</span><b>' + (real.length ? Math.round(positive / real.length * 100) + '%' : '--') + '</b></div>' +
        '<small>' + (real.length ? '按已记录结果计算，不代表未来收益' : '样本不足' + (drills ? ' · 已剔除 ' + drills + ' 条演练/无结果记录' : '')) + '</small></div>';
    }
    if (!body) body = '<div class="dd-empty">复盘数据暂不可用</div>';
    body += '<div class="dd-note">复盘口径：纸面账户与交易留痕；未产生闭合交易时胜率/盈亏比为空。</div>';
    return card('盘后复盘摘要', 'REVIEW', body, 'span4', { act: 'open-automation', text: '复盘详情' });
  }


  /* ---------------- 题材雷达 / 板块趋势 ---------------- */
  function themeData() {
    var t = state.data.themes;
    return t && t.ok ? t : null;
  }

  function renderSectorTrends() {
    var t = themeData();
    var body = '';
    if (!t) {
      body = '<div class="dd-empty">板块趋势数据加载中 / 暂不可用</div>';
    } else {
      body += '<div class="dd-note">历史收益截至 ' + esc(t.board_snapshot_as_of || '-') + '；今日涨跌为实时快照按成分股聚合</div>';
      body += '<div class="dd-trend-list">';
      (t.sectors || []).slice(0, 8).forEach(function (s) {
        var stage = String(s.stage || '');
        var cls = stage.indexOf('退潮') >= 0 || stage.indexOf('回落') >= 0 ? 'bad' : stage.indexOf('分歧') >= 0 ? 'warn' : 'good';
        body += '<div class="dd-trend-row dd-row-link" data-act="open-board" data-code="' + esc(s.code) + '" title="点击查看成分股">' +
          '<span class="dd-trend-name">' + esc(s.name) + ' <em>L' + esc(s.level == null ? '-' : s.level) + '</em></span>' +
          '<span class="dd-trend-stage ' + cls + '">' + esc(s.stage) + '</span>' +
          '<span class="vl ' + chgClass(s.avg_chg) + '">' + pct(s.avg_chg) + '</span>' +
          '<span class="vl muted-text">5日 ' + pct(s.d5) + '</span>' +
          '<span class="vl muted-text">20日 ' + pct(s.d20) + '</span>' +
          '<span class="vl muted-text">' + fmt(s.up_ratio, 0) + '%涨</span>' +
          '<span class="dd-trend-leaders">' + (s.leaders || []).map(function (l) { return esc(l.name); }).join(' · ') + '</span>' +
          '</div>';
      });
      body += '</div>';
      var sectors = t.sectors || [];
      if (sectors.length) {
        var stages = [
          { key: '加速', match: function (s) { return String(s.stage).indexOf('加速') >= 0 || String(s.stage).indexOf('主升') >= 0; } },
          { key: '启动', match: function (s) { return String(s.stage).indexOf('启动') >= 0; } },
          { key: '震荡', match: function (s) { return String(s.stage).indexOf('震荡') >= 0 || String(s.stage).indexOf('横盘') >= 0; } },
          { key: '分歧', match: function (s) { return String(s.stage).indexOf('分歧') >= 0; } },
          { key: '退潮', match: function (s) { return String(s.stage).indexOf('退潮') >= 0 || String(s.stage).indexOf('回落') >= 0; } }
        ];
        var counts = stages.map(function (group) { return sectors.filter(group.match).length; });
        var counted = counts.reduce(function (a, b) { return a + b; }, 0);
        if (counted < sectors.length) counts[2] += sectors.length - counted;
        body += '<div class="dd-stage-strip"><span class="dd-stage-title">阶段结构</span><div class="dd-stage-bar">';
        stages.forEach(function (group, index) {
          if (!counts[index]) return;
          body += '<i class="is-' + index + '" style="flex:' + counts[index] + '" title="' + group.key + ' ' + counts[index] + ' 个"></i>';
        });
        body += '</div><div class="dd-stage-legend">';
        stages.forEach(function (group, index) {
          body += '<span><i class="is-' + index + '"></i>' + group.key + ' <b>' + counts[index] + '</b></span>';
        });
        body += '<em>共 ' + sectors.length + ' 个板块</em></div></div>';
      }
      if ((t.sectors || []).length === 0) body += '<div class="dd-empty">暂无板块数据</div>';
    }
    return card('板块趋势分析', 'SECTOR TREND', body, 'span8', { act: 'open-market', text: '事实层' });
  }

  function renderCharRadar() {
    var t = themeData();
    var body = '';
    if (!t) {
      body = '<div class="dd-empty">题材雷达加载中 / 暂不可用</div>';
    } else {
      body += '<div class="dd-char-grid">';
      (t.zodiac || []).forEach(function (g) {
        body += '<button class="dd-char" data-act="open-char" data-char="' + esc(g.char) + '" title="点击查看成分股">' +
          '<b>' + esc(g.char) + '</b><span>' + g.count + '只</span>' +
          '<em class="' + chgClass(g.avg_chg) + '">' + pct(g.avg_chg) + '</em>' +
          '<i>' + esc(g.stage) + ' · 热' + g.heat + '</i></button>';
      });
      body += '</div>';
      if ((t.discovered || []).length) {
        body += '<div class="dd-bar-label" style="margin-top:6px"><span>自主发现（高位股异常聚集）</span><span class="muted-text">lift</span></div>';
        (t.discovered || []).slice(0, 4).forEach(function (g) {
          body += '<div class="dd-row dd-row-link" data-act="open-char" data-char="' + esc(g.char) + '">' +
            '<span class="nm">' + esc(g.char) + ' <span class="muted-text" style="font-size:10px">' + g.count + '只</span></span>' +
            '<span class="vl ' + chgClass(g.avg_chg) + '">' + pct(g.avg_chg) + '</span>' +
            '<span class="tag">×' + fmt(g.lift, 1) + '</span></div>';
        });
      }
      if (!(t.zodiac || []).length) body += '<div class="dd-empty">当前样本中无生肖字标的</div>';
    }
    body += '<div class="dd-note">2026 丙午马年 → 2027 丁未羊年：跨年生肖字（羊/马）为当前观察重点；点字辈看成分股，再点个股进入策略选股。</div>';
    return card('题材炒作 · 字辈雷达', 'THEME RADAR', body, 'span4', { act: 'open-stocks', text: '策略选股' });
  }

  function openOverlay(title, subtitle, loading) {
    var ov = $('dd-overlay');
    if (!ov) return;
    ov.hidden = false;
    var t = $('dd-overlay-title'), s = $('dd-overlay-sub'), b = $('dd-overlay-body');
    if (t) t.textContent = title;
    if (s) s.textContent = subtitle || '';
    if (b) b.innerHTML = loading ? '<div class="dd-empty">加载中…</div>' : '';
  }

  function closeOverlay() {
    var ov = $('dd-overlay');
    if (ov) ov.hidden = true;
  }

  function stockRows(list) {
    return '<div class="dd-stock-list">' + (list || []).map(function (s) {
      return '<div class="dd-row dd-row-link" data-act="pick-stock" data-code="' + esc(s.code) + '" data-name="' + esc(s.name) + '" title="在策略选股打开">' +
        '<span class="nm">' + esc(s.name) + ' <span class="muted-text" style="font-size:10px">' + esc(s.code) + '</span></span>' +
        '<span class="vl">' + fmt(s.price, 2) + '</span>' +
        '<span class="vl ' + chgClass(s.chg) + '">' + pct(s.chg) + '</span>' +
        '<span class="muted-text" style="font-size:10px">' + amt(s.amount) + '</span></div>';
    }).join('') + '</div>';
  }

  async function openBoard(code) {
    openOverlay('板块成分股', code, true);
    try {
      var j = await jget('/api/themes/board/' + encodeURIComponent(code));
      if (!j.ok) throw new Error(j.error || '加载失败');
      var head = '<div class="dd-overlay-stats">' + esc(j.name) + ' · ' + j.count + ' 只 · 5日 ' + pct(j.d5) + ' · 20日 ' + pct(j.d20) + '</div>';
      var b = $('dd-overlay-body');
      if (b) b.innerHTML = head + stockRows(j.stocks);
    } catch (e) {
      var bb = $('dd-overlay-body');
      if (bb) bb.innerHTML = '<div class="dd-empty">加载失败：' + esc(e.message) + '</div>';
    }
  }

  async function openChar(ch) {
    openOverlay('字辈成分股', ch, true);
    try {
      var j = await jget('/api/themes/char/' + encodeURIComponent(ch));
      if (!j.ok) throw new Error(j.error || '加载失败');
      var head = '<div class="dd-overlay-stats">“' + esc(ch) + '”字辈 · 共 ' + j.count + ' 只</div>';
      var b = $('dd-overlay-body');
      if (b) b.innerHTML = head + stockRows(j.stocks);
    } catch (e) {
      var bb = $('dd-overlay-body');
      if (bb) bb.innerHTML = '<div class="dd-empty">加载失败：' + esc(e.message) + '</div>';
    }
  }


  function renderDataSources() {
    var ds = state.data.datasources;
    var body = '';
    if (!ds || !ds.sources) {
      body = '<div class="dd-empty">数据源健康数据加载中 / 暂不可用</div>';
    } else {
      body += '<div class="dd-ds-grid">';
      (ds.sources || []).forEach(function (x) {
        var cls = x.status === 'ok' ? 'good' : 'warn';
        body += '<div class="dd-ds ' + cls + '" title="' + esc(x.note || '') + '">' +
          '<b>' + esc(x.label) + '</b>' +
          '<span>' + (x.status === 'ok' ? '正常' : '降级') + ' · ' + (x.latency_ms == null ? '--' : x.latency_ms + 'ms') + '</span>' +
          '<i>' + (x.critical ? '核心' : '可选') + '</i></div>';
      });
      body += '</div>';
      var summary = ds.summary || {};
      body += '<div class="dd-note">核心源：腾讯/新浪；降级源自动切换缓存或替代源。生成时间 ' + esc(ds.as_of || '-') +
        ' · 正常 ' + (summary.ok == null ? '--' : summary.ok) + ' / 降级 ' + (summary.degraded == null ? '--' : summary.degraded) +
        ' · 核心链路 ' + (summary.critical_ok ? '可用' : '异常') + '。</div>';
    }
    return card('数据源健康', 'DATA SOURCES', body, 'span12', { act: 'refresh', text: '刷新' });
  }

  function renderPlan(v, mode) {
    var isPost = mode === 'post';
    var checklist = buildChecklist(v);
    var passCount = checklist.filter(function (c) { return c.state === 'pass'; }).length;
    var body = '';
    body += '<div class="dd-sub">自动检查 ' + checklist.length + ' 项，通过 ' + passCount + ' 项。' + (isPost ? '盘后请录入实际执行与偏差。' : '确认后生成今日计划；计划会保存到本机。') + '</div>';
    body += '<div class="dd-check-list">' + checklist.map(function (c) {
      return '<div class="dd-check"><div class="ico ' + c.state + '">' + (c.state === 'pass' ? '✓' : c.state === 'warn' ? '!' : '×') + '</div>' +
        '<div class="txt"><b>' + esc(c.label) + '</b><span>' + esc(c.value) + '</span></div></div>';
    }).join('') + '</div>';
    body += '<div class="dd-plan">' +
      '<div style="display:flex;gap:8px;align-items:center"><strong style="font-size:12px">' + (isPost ? '盘后复盘记录' : '今日交易计划') + '</strong>' +
      '<span class="muted-text" style="font-size:10px">保存在本机浏览器</span></div>' +
      '<textarea id="' + (isPost ? 'dd-review-note' : 'dd-plan-note') + '" placeholder="' + (isPost ? '记录：实际执行、偏差原因、明日改进…' : '记录：计划仓位、关注板块、触发条件、止损纪律…') + '"></textarea>' +
      '<div class="dd-plan-actions">' +
        '<button class="dd-btn primary" data-act="' + (isPost ? 'save-review' : 'save-plan') + '">' + (isPost ? '保存复盘' : '生成并保存计划') + '</button>' +
        '<button class="dd-btn ghost" data-act="copy-plan">复制到剪贴板</button>' +
        '<span class="dd-plan-saved" id="dd-plan-saved">' + (state.plan && state.plan.savedAt ? '已保存 ' + esc(state.plan.savedAt) : '') + '</span>' +
      '</div>' +
      '<div class="dd-history" id="dd-history"></div>' +
      '</div>';
    return card(isPost ? '盘后复盘 / 执行偏差' : '决策卡 / 交易计划', 'PLAN', body, 'span8');
  }

  function buildChecklist(v) {
    var d = state.data;
    var md = d.market && d.market.ok ? d.market.data : null;
    var b = (md && md.breadth) || {};
    var items = [];
    function it(label, value, state) { items.push({ label: label, value: value, state: state }); }
    if (v.lscore != null) it('隔夜外盘', '全球联动 ' + v.lscore.toFixed(2) + '（' + (v.reasons[2] ? '' : '') + '）', v.lscore <= -1.5 ? 'fail' : v.lscore >= 0 ? 'pass' : 'warn');
    if (v.upRatio != null) it('市场宽度', '上涨占比 ' + v.upRatio.toFixed(1) + '%', v.upRatio >= 45 ? 'pass' : v.upRatio >= 30 ? 'warn' : 'fail');
    if (num(b.limit_up) != null && num(b.limit_down) != null) it('涨跌停结构', '涨停 ' + b.limit_up + ' / 跌停 ' + b.limit_down, num(b.limit_up) > num(b.limit_down) ? 'pass' : 'fail');
    if (md && md.turnover) it('成交额', amt(md.turnover.amount), num(md.turnover.amount) >= 8e11 ? 'pass' : 'warn');
    var north = d.north && d.north.ok ? d.north.data : null;
    if (north) {
      var up = num(north.hgt_up), down = num(north.hgt_down);
      it('资金面（北向结构）', north.disclosed ? '已披露' : '披露受限，以上涨/下跌家数观察', north.disclosed ? 'pass' : (up > down ? 'warn' : 'fail'));
    }
    var risk = d.riskHealth && d.riskHealth.ok ? d.riskHealth.data : null;
    if (risk) it('风控体检', '评分 ' + (risk.score == null ? '--' : risk.score) + ' / ' + (risk.verdict || '未知'), num(risk.score) >= 70 ? 'pass' : num(risk.score) >= 50 ? 'warn' : 'fail');
    var intel = d.intel && d.intel.latest;
    if (intel && intel.risk_score != null) it('事件风险', '情报风险 ' + intel.risk_score + '（' + (intel.risk_level || '--') + '）', intel.risk_score <= 30 ? 'pass' : intel.risk_score <= 60 ? 'warn' : 'fail');
    it('仓位纪律', '建议上限 ≤ ' + v.cap + '%', 'warn');
    return items;
  }

  function planText(v, mode) {
    var d = state.data;
    var checklist = buildChecklist(v);
    var md = d.market && d.market.ok ? d.market.data : null;
    var sectors = (md && md.opportunities) || [];
    var lines = [];
    lines.push('【' + (mode === 'post' ? '盘后复盘' : '盘前决策') + '】' + today() + ' ' + nowHM());
    lines.push('结论：' + v.label + '（风险指数 ' + (v.riskIndex == null ? '--' : v.riskIndex) + '/100），建议仓位上限 ≤ ' + v.cap + '%');
    lines.push('核心依据：');
    v.reasons.slice(0, 5).forEach(function (r) { lines.push('  - ' + r); });
    if (sectors.length) lines.push('关注板块：' + sectors.slice(0, 4).join('、'));
    lines.push('检查清单：');
    checklist.forEach(function (c) { lines.push('  [' + (c.state === 'pass' ? '通过' : c.state === 'warn' ? '关注' : '不通过') + '] ' + c.label + '：' + c.value); });
    lines.push('执行纪律：单笔风险 ≤ 1%，总仓位 ≤ ' + v.cap + '%，触发止损立即执行，不逆势加仓。');
    var note = $('dd-plan-note') || $('dd-review-note');
    if (note && note.value.trim()) lines.push('补充记录：' + note.value.trim());
    return lines.join('\n');
  }

  function savePlan() {
    var v = computeVerdict();
    var mode = activeMode();
    var note = $('dd-plan-note') || $('dd-review-note');
    var text = planText(v, mode);
    var rec = { date: today(), mode: mode, riskIndex: v.riskIndex, stance: v.label, cap: v.cap, text: text, note: note ? note.value : '', savedAt: nowHM() };
    hset(PLAN_PREFIX + rec.date, JSON.stringify(rec));
    state.plan = rec;
    var saved = $('dd-plan-saved');
    if (saved) saved.textContent = '已保存 ' + rec.savedAt;
    renderHistory();
    toast('决策卡已保存', '计划已写入本机（' + rec.date + '）');
  }

  function saveReview() {
    var note = $('dd-review-note');
    if (!note) return;
    var v = computeVerdict();
    var rec = { date: today(), note: note.value, riskIndex: v.riskIndex, savedAt: nowHM() };
    hset(REVIEW_PREFIX + rec.date, JSON.stringify(rec));
    toast('复盘已保存', '盘后记录已写入本机');
  }

  function copyPlan() {
    var v = computeVerdict();
    var text = planText(v, activeMode());
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { toast('已复制', '决策卡文本已复制到剪贴板'); }, function () { fallbackCopy(text); });
    } else { fallbackCopy(text); }
  }
  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); toast('已复制', '决策卡文本已复制'); } catch (e) { toast('复制失败', '请手动选择文本'); }
    document.body.removeChild(ta);
  }

  function loadPlan(date) {
    var raw = hs(PLAN_PREFIX + date);
    if (!raw) return;
    try {
      var rec = JSON.parse(raw);
      state.plan = rec;
      var note = $('dd-plan-note');
      if (note) note.value = rec.note || '';
      var saved = $('dd-plan-saved');
      if (saved) saved.textContent = '已载入 ' + esc(rec.savedAt || date);
      toast('已载入计划', date + ' 的决策卡');
    } catch (e) { }
  }

  function renderHistory() {
    var host = $('dd-history');
    if (!host) return;
    var keys = [];
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.indexOf(PLAN_PREFIX) === 0) keys.push(k.slice(PLAN_PREFIX.length));
      }
    } catch (e) { }
    keys.sort().reverse();
    if (!keys.length) { host.innerHTML = '<span class="muted-text" style="font-size:10px">暂无历史计划</span>'; return; }
    host.innerHTML = keys.slice(0, 6).map(function (date) {
      return '<button data-act="load-plan" data-date="' + esc(date) + '">' + esc(date) + '</button>';
    }).join('');
  }

  function toast(title, body) {
    if (typeof showToast === 'function') { showToast(title, body); return; }
    var el = document.createElement('div');
    el.textContent = title + '：' + body;
    el.style.cssText = 'position:fixed;right:20px;bottom:24px;z-index:9999;background:rgba(10,20,40,.95);border:1px solid rgba(6,182,212,.4);color:#e6f6ff;padding:10px 14px;border-radius:10px;font-size:12px;box-shadow:0 8px 30px rgba(0,0,0,.4)';
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 2600);
  }

  /* ---------------- 实时通道（data-bus SSE） ---------------- */
  var SSE_KEYS = ['sentiment', 'experts', 'scan'];

  function scheduleRender() {
    if (state.renderQueued) return;
    state.renderQueued = true;
    setTimeout(function () { state.renderQueued = false; render(); }, 120);
  }

  function startStream() {
    if (state.stream || typeof EventSource === 'undefined') return;
    try {
      state.stream = new EventSource('/api/decision/stream?interval=30');
    } catch (e) {
      state.streamStatus = 'off';
      return;
    }
    state.streamStatus = 'connecting';
    state.stream.onopen = function () { state.streamStatus = 'live'; scheduleRender(); };
    SSE_KEYS.forEach(function (key) {
      state.stream.addEventListener(key, function (evt) {
        try {
          var msg = JSON.parse(evt.data);
          var payload = msg && msg.payload;
          if (!payload) return;
          state.data[key] = payload;
          state.resources[key] = { status: 'ok', label: ENDPOINTS[key].label, at: Date.now(), via: 'stream' };
          state.streamStatus = 'live';
          state.lastEventAt = Date.now();
          scheduleRender();
        } catch (e) { /* 单帧解析失败不打断连接 */ }
      });
    });
    state.stream.addEventListener('invalidate', function (evt) {
      try {
        var msg = JSON.parse(evt.data);
        (msg.keys || []).forEach(function (k) {
          if (state.resources[k]) state.resources[k].rebuilding = true;
        });
        scheduleRender();
      } catch (e) { }
    });
    state.stream.onerror = function () {
      /* EventSource 自带指数退避重连；轮询仍在跑，作为兜底不会断数据 */
      state.streamStatus = 'reconnect';
      scheduleRender();
    };
  }

  function stopStream() {
    if (state.stream) { try { state.stream.close(); } catch (e) { } }
    state.stream = null;
    state.streamStatus = 'off';
  }

  /* ---------------- 外部接口 ---------------- */
  // ------------------------------------------------------------------
  //  三层对证条：事实 / 判断 / 决策 对同一指标是否给出同一答案
  //  数据来自 /api/brain/alignment（后端做逐项比对），前端只负责展示。
  // ------------------------------------------------------------------
  var CC_LABEL = { pass: '一致', warn: '偏差', conflict: '冲突', incomparable: '不可比', unavailable: '缺数据' };

  function renderCrosscheck(payload) {
    var host = $('dd-crosscheck');
    if (!host) return;
    var a = payload || {};
    var cc = a.crosscheck || {};
    var checks = cc.checks || [];
    if (!checks.length) { host.innerHTML = ''; return; }

    var counts = cc.counts || {};
    var hasConflict = (cc.conflicts || []).length > 0;
    var state = cc.ok ? ((counts.warn || 0) > 0 ? 'warn' : 'pass') : (hasConflict ? 'fail' : 'warn');
    var inc = counts.incomparable || 0;
    var passN = counts.pass || 0;
    // 刻意不写「6/8」这种分数：不可比项按 1.0 计分，分数会与通过数互相打脸，
    // 所以逐类给绝对值，一致性另列（不可比不参与打分，见 LAYER-CROSSCHECK.md）。
    var parts = ['一致 ' + passN];
    if (counts.warn) parts.push('偏差 ' + counts.warn);
    if (counts.conflict) parts.push('冲突 ' + counts.conflict);
    if (inc) parts.push('不可比 ' + inc);
    if (counts.unavailable) parts.push('缺数据 ' + counts.unavailable);
    var leadTitle = '共 ' + (cc.total || 0) + ' 项：'
      + parts.join('、')
      + (inc ? '（异量纲 / 分类体系不同，不参与打分）' : '')
      + ((cc.blockers || []).length ? '；阻塞：' + cc.blockers.join(',') : '');
    var headText = '三层对证 ' + parts.join(' · ') +
      ' · 一致性 ' + Math.round((cc.score || 0) * 100) + '%';
    var head = '<span class="dd-cc-lead" data-state="' + state + '" title="' + esc(leadTitle) + '">' +
      '<i class="dd-cc-dot"></i>' + esc(headText) + '</span>';

    var chips = checks.map(function (c) {
      var short = String(c.name || '').replace(/（[^）]*）/g, '');
      var unit = c.id === 'freshness' ? 's' : '';
      var delta = (c.delta == null) ? '' : ('<b>' + (c.delta > 0 ? '+' : '') + c.delta + unit + '</b>');
      return '<span class="dd-cc-chip" data-state="' + esc(c.status) + '" title="' +
        esc(short + '：' + (c.note || '') + (c.hint ? '｜建议：' + c.hint : '')) + '">' +
        esc(short) + (delta ? ' ' + delta : '') +
        '<em style="font-style:normal;opacity:.65">' + esc(CC_LABEL[c.status] || c.status) + '</em></span>';
    }).join('');

    var cfg = a.config || {};
    var cfgNote = cfg.ok
      ? '<span class="dd-cc-note">配置 brain-config.v1 已校验</span>'
      : '<span class="dd-cc-note" style="color:var(--gold)">配置有 ' + (cfg.issues || []).length + ' 项回落默认值</span>';

    host.innerHTML = head + chips + cfgNote +
      '<button class="dd-cc-link" data-act="crosscheck-refresh">重新对证</button>';
  }

  async function loadAlignment(force) {
    try {
      // 小条走 light（事实↔判断，不触发六专家）；完整三层对证由闭环与接口按需触发
      var payload = await jget('/api/brain/alignment?light=true' + (force ? '&refresh=true' : ''), 20000);
      renderCrosscheck(payload);
    } catch (e) {
      var host = $('dd-crosscheck');
      if (host) host.innerHTML = '<span class="dd-cc-note">三层对证暂不可用：' + esc(String(e && e.message || e)) + '</span>';
    }
  }

  function initDecisionDesk() {
    if (!state.built) {
      var saved = hs(MODE_KEY);
      if (saved === 'pre' || saved === 'intra' || saved === 'post' || saved === 'auto') state.mode = saved;
      try { var rawPlan = hs(PLAN_PREFIX + today()); if (rawPlan) state.plan = JSON.parse(rawPlan); } catch (e) { }
      build();
    }
    var host = $('decision-mount');
    if (!host) return;
    host.classList.add('dd-active');
    loadAll(false);
    loadAlignment(false);
    startStream();
    if (!state.timer) {
      state.timer = setInterval(function () {
        var view = $('decision-dashboard-view');
        if (!document.hidden && view && view.style.display !== 'none') {
          loadAll(false);
          loadAlignment(false);
        }
      }, 60000);
    }
    if (!state.eventsBound) {
      state.eventsBound = true;
      window.addEventListener('online', function () { loadAll(true); });
      document.addEventListener('visibilitychange', function () {
        if (document.hidden) return;
        var view = $('decision-dashboard-view');
        if (view && view.style.display !== 'none') loadAll(false);
      });
      document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') closeOverlay();
      });
    }
  }

  window.initDecisionDesk = initDecisionDesk;
  window.DecisionDesk = {
    state: state, refresh: loadAll, computeVerdict: computeVerdict,
    startStream: startStream, stopStream: stopStream,
  };
  // 页面加载后预热一次，保证切换过去立即有数据
  document.addEventListener('DOMContentLoaded', function () {
    var saved = hs(MODE_KEY);
    if (saved) state.mode = saved;
  });
})();

/* v2：原 v71-v73 独立轮询已合并到上方统一数据管线。
   这样可消除重复请求、limit=3 的固定 422，以及两套快照时间不一致的问题。 */
