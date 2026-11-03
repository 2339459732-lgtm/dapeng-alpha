/* ================================================================
 * 自动化中心（M4）- static/js/automation.js
 * 架构蓝图 §9：四 Tab 浮层（配置/信号历史/订单流水/模拟持仓）
 * + 侧边栏迷你状态点（轮询 /api/automation/gate，30s/次）
 * 约束：app.js 零改动；window.appState 只读；文案一律"信号提示，供参考"
 * ================================================================ */
(function () {
  'use strict';

  var API = '/api/automation';
  var GATE_POLL_MS = 30000;
  var PAGE_SIZE = 100;
  var LIVE_CONFIRM_TEXT = '确认开启实盘交易';

  var S = {
    visible: false,
    tab: 'config',
    config: null,
    gate: null,
    configError: '',
    selectedStrategy: '',
    signals: { items: [], total: 0, code: '', loading: false },
    orders: { items: [], total: 0, code: '', loading: false },
    audit: { items: [], total: 0, action: '', result: '', loading: false },
    gateTimer: null,
    liveConfirmOpen: false,
    // 实时推送（SSE）：信号/订单/持仓变动实时刷新，失败自动降级
    stream: null,
    streamDegraded: false,
    streamSid: null
  };

  // 策略参数表单 schema（与后端 default_params 对齐）
  var PARAM_SCHEMAS = {
    ma_cross_atr: [
      { key: 'fast', label: '快线 MA', def: 5, step: 1 },
      { key: 'slow', label: '慢线 MA', def: 20, step: 1 },
      { key: 'atr_period', label: 'ATR 周期', def: 14, step: 1 },
      { key: 'atr_mult', label: 'ATR 倍数', def: 2.0, step: 0.1 },
      { key: 'fixed_stop_pct', label: '固定止损 %（0=关）', def: 10.0, step: 0.5 },
      { key: 'vol_confirm', label: '金叉量能确认 ×（0=关）', def: 0.0, step: 0.1 }
    ],
    n_day_breakout: [
      { key: 'entry_days', label: '突破天数 N', def: 20, step: 1 },
      { key: 'exit_days', label: '退出天数 M', def: 10, step: 1 },
      { key: 'fixed_stop_pct', label: '固定止损 %（0=关）', def: 10.0, step: 0.5 },
      { key: 'vol_mult', label: '突破量能确认 ×（0=关）', def: 0.0, step: 0.1 }
    ],
    rsi_mean_reversion: [
      { key: 'period', label: 'RSI 周期', def: 14, step: 1 },
      { key: 'oversold', label: '超卖线', def: 30, step: 1 },
      { key: 'overbought', label: '超买线', def: 70, step: 1 },
      { key: 'trend_ma', label: '趋势过滤 MA（0=关）', def: 60, step: 5 },
      { key: 'stop_pct', label: '固定止损 %（0=关）', def: 8.0, step: 0.5 }
    ],
    macd_trend: [
      { key: 'fast', label: '快线 EMA', def: 12, step: 1 },
      { key: 'slow', label: '慢线 EMA', def: 26, step: 1 },
      { key: 'signal', label: '信号线 DEA', def: 9, step: 1 },
      { key: 'low_cross_only', label: '仅低位金叉（1/0）', def: 1, step: 1 },
      { key: 'stop_pct', label: '固定止损 %（0=关）', def: 10.0, step: 0.5 }
    ],
    volume_breakout: [
      { key: 'entry_days', label: '突破天数 N', def: 20, step: 1 },
      { key: 'exit_days', label: '退出天数 M', def: 10, step: 1 },
      { key: 'vol_mult', label: '放量倍数', def: 1.8, step: 0.1 },
      { key: 'vol_ma', label: '均量周期', def: 20, step: 1 },
      { key: 'stop_pct', label: '固定止损 %（0=关）', def: 8.0, step: 0.5 }
    ]
  };
  var STRATEGY_NAMES = {
    ma_cross_atr: '均线交叉+ATR止损', n_day_breakout: 'N日突破',
    rsi_mean_reversion: 'RSI均值回归', macd_trend: 'MACD趋势', volume_breakout: '放量突破'
  };
  var SIGNAL_TYPE_LABELS = {
    score: '高评分', momentum: '动量', breakout: '突破',
    macd_gold: 'MACD金叉', macd_dead: 'MACD死叉', chg_spike: '涨跌幅异动',
    ma_cross_gold: 'MA金叉', ma_cross_dead: 'MA死叉',
    volume_divergence: '量价背离', ma_bullish: '多头排列', pullback: '回踩',
    limit_up: '涨停', bottom_reversal: '底部反转', dragon_head: '龙头',
    momentum_reversal: '动量反转',
    strategy_buy: '策略买入信号', strategy_sell: '策略卖出信号', atr_stop: 'ATR止损信号'
  };
  // 可配置进 trigger.signal_types 的合法值（13 类，与后端 panel_adapter 产出一致）：
  // 面板透传 11 类 + MACD 金叉/死叉。score/chg_spike 由各自阈值触发，不在开关内。
  var CONFIGURABLE_SIGNAL_TYPES = [
    'momentum', 'breakout', 'volume_divergence', 'ma_cross_gold', 'ma_cross_dead',
    'ma_bullish', 'pullback', 'limit_up', 'bottom_reversal', 'dragon_head',
    'momentum_reversal', 'macd_gold', 'macd_dead'
  ];
  // 留痕动作中文名（GET /audit 的 action 字段）
  var AUDIT_ACTION_LABELS = {
    decision_allow: '决策闸·通过', decision_deny: '决策闸·拒绝',
    order_filled: '订单成交', order_failed: '订单失败',
    order_submit: '订单提交', order_rejected: '风控拒绝', order_manual_submit: '手动下单',
    order_canceled: '撤单', conditional_created: '条件单创建',
    conditional_triggered: '条件单触发', conditional_canceled: '条件单取消',
    conditional_expired: '条件单过期', config_updated: '配置变更',
    live_switch: '实盘开关', breaker_tripped: '熔断触发', breaker_reset: '熔断复位',
    iteration_run: '策略迭代', daily_routine_done: '日结完成'
  };
  var PUSH_STATUS_LABELS = {
    pending: ['待处理', 'muted'], sent: ['已推送', 'green'],
    disabled: ['通道未配置', 'muted'], rate_limited: ['限流进摘要', 'gold'],
    digest: ['摘要已发', 'green'], failed: ['推送失败', 'red'],
    suppressed_cooldown: ['冷却抑制', 'muted']
  };
  var ORDER_STATUS_LABELS = {
    filled: ['已成交', 'green'], rejected: ['风控拒绝', 'gold'],
    failed: ['失败', 'red'], cancelled: ['已撤销', 'muted']
  };
  var GATE_STATE_LABELS = {
    draft: ['草稿', 'muted'], backtested: ['已回测', 'blue'],
    paper_validating: ['模拟验证中', 'gold'], paper_passed: ['模拟已通过', 'green'],
    live_enabled: ['实盘已启用', 'red']
  };

  // ------------------------------------------------------------
  // 工具
  // ------------------------------------------------------------
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmtNum(v, digits) {
    var n = Number(v);
    if (!isFinite(n)) return '-';
    return n.toLocaleString('zh-CN', { maximumFractionDigits: digits == null ? 2 : digits });
  }
  function fmtPct(v, digits) {
    var n = Number(v);
    if (!isFinite(n)) return '-';
    return (n > 0 ? '+' : '') + n.toFixed(digits == null ? 2 : digits) + '%';
  }
  function fmtSignedPct01(v) { // 0.21 → +21.0%
    var n = Number(v);
    if (!isFinite(n)) return '-';
    return (n > 0 ? '+' : '') + (n * 100).toFixed(1) + '%';
  }
  function pnlClass(v) { return Number(v) >= 0 ? 'red' : 'green'; } // A股配色：涨红跌绿

  // 成本/止盈字段显示助手
  //   ri(raw, def)：原样显示整数/数值，未配置时显示接口/默认值
  //   wan(rate, defWan)：费率(如 0.00025) → "万分之"数值(2.5)，供输入框按万分之展示
  function ri(v, def) { return (v === null || v === undefined || v === '') ? def : v; }
  function wan(rate, defWan) {
    var n = Number(rate);
    if (!isFinite(n)) return defWan;
    return Math.round(n * 10000 * 10000) / 10000; // 0.00025 → 2.5
  }
  function readChecked(id, def) {
    var el = $(id);
    return el ? !!el.checked : def;
  }

  // 保存助手：数值输入「留空/非法」→ 不提交该字段（保持服务端原值），并记入 skipped。
  // 用户红线：绝不把空输入悄悄替换成默认值写库（原实现为 `|| 80` / `|| 10000` 等）。
  function numField(id, label, skipped, divBy) {
    var el = $(id);
    var raw = el ? String(el.value).trim() : '';
    var n = raw === '' ? NaN : parseFloat(raw);
    if (!isFinite(n)) { if (el) skipped.push(label); return undefined; }
    return divBy ? n / divBy : n;
  }
  function dropUndefined(obj) {
    Object.keys(obj).forEach(function (k) { if (obj[k] === undefined) delete obj[k]; });
    return obj;
  }
  function skipNote(skipped) {
    return (skipped && skipped.length) ? '（留空已保持原值：' + skipped.join('、') + '）' : '';
  }

  function toast(msg, isErr) {
    var host = $('automation-panel');
    if (!host) return;
    var t = document.createElement('div');
    t.className = 'auto-toast' + (isErr ? ' err' : '');
    t.textContent = msg;
    host.appendChild(t);
    setTimeout(function () { t.classList.add('show'); }, 10);
    setTimeout(function () {
      t.classList.remove('show');
      setTimeout(function () { t.remove(); }, 300);
    }, 3200);
  }

  function api(path, method, body) {
    var opts = { method: method || 'GET', headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    return fetch(API + path, opts).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (j) {
        return {
          status: r.status,
          ok: !!(j && j.ok),
          data: j && j.data,
          message: (j && j.message) || ('HTTP ' + r.status)
        };
      });
    }).catch(function (e) {
      return { status: 0, ok: false, data: null, message: '网络错误：' + e.message };
    });
  }

  function currentCode() {
    var c = (window.appState && window.appState.activeCode) || '';
    return /^(sh|sz|hk)\d{5,6}$/.test(c) ? c : 'sh600519';
  }

  // ------------------------------------------------------------
  // 浮层开关
  // ------------------------------------------------------------
  window.toggleAutomationPanel = function () {
    var panel = $('automation-panel');
    if (!panel) return;
    S.visible = !S.visible;
    panel.style.display = S.visible ? 'flex' : 'none';
    if (S.visible) {
      refreshAll().then(function () { renderConfigTab(); });
      switchAutomationTab(S.tab || 'config');
    }
  };

  window.switchAutomationTab = function (tab) {
    S.tab = tab;
    var tabs = document.querySelectorAll('#automation-panel .full-overlay-tab');
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].classList.toggle('active', tabs[i].getAttribute('data-tab') === tab);
    }
    var pages = ['config', 'signals', 'orders', 'positions', 'review', 'audit'];
    for (var j = 0; j < pages.length; j++) {
      var el = $('auto-tab-' + pages[j]);
      if (el) el.style.display = pages[j] === tab ? '' : 'none';
    }
    if (tab === 'signals') loadSignals(true);
    else if (tab === 'orders') loadOrders(true);
    else if (tab === 'positions') loadPositions();
    else if (tab === 'config') renderConfigTab();
    else if (tab === 'review') loadReview();
    else if (tab === 'audit') loadAudit(true);
  };

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && S.visible) window.toggleAutomationPanel();
  });

  // ------------------------------------------------------------
  // 数据加载
  // ------------------------------------------------------------
  function refreshAll() {
    return Promise.all([
      api('/config').then(function (r) {
        if (r.ok) { S.config = r.data; S.configError = ''; }
        else if (!S.config) S.configError = r.message || '配置接口不可用';
      }),
      api('/gate').then(function (r) {
        if (r.ok) { S.gate = r.data; }
        else if (!S.gate) S.configError = S.configError || r.message || '门禁接口不可用';
      })
    ]).then(function () { renderStatusDot(); });
  }

  // ------------------------------------------------------------
  // 状态灯（侧边栏迷你点 + 配置 Tab 大灯）
  // ------------------------------------------------------------
  function statusLevel() {
    var g = S.gate, c = S.config;
    if (!g || !c) return 'gray';
    if (g.breaker && g.breaker.tripped) return 'red';
    if (g.monitor && g.monitor.consecutive_errors > 0) return 'red';
    if (g.smtp && g.smtp.configured && !g.smtp.ok) return 'red';
    if (g.live_master_switch) return 'green';
    if (c.switches && c.switches.paper) return 'green';
    if (c.switches && c.switches.notify) return 'yellow';
    return 'gray';
  }

  function statusText(level) {
    if (!S.gate || !S.config) return '数据不可用（接口未响应）';
    switch (level) {
      case 'green':
        return (S.gate.live_master_switch) ? '实盘通道（待接入券商）' : '模拟交易运行中';
      case 'yellow': return '仅信号提醒（邮件）';
      case 'red': return '熔断/异常：请检查';
      default: return '自动化已关闭';
    }
  }

  function renderStatusDot() {
    var dot = $('automation-status-dot');
    if (!dot) return;
    var level = statusLevel();
    dot.className = 'auto-status-dot dot-' + level;
    dot.title = '自动化状态：' + statusText(level);
  }

  // ------------------------------------------------------------
  // 配置 Tab 渲染
  // ------------------------------------------------------------
  function renderConfigTab() {
    var host = $('auto-tab-config');
    if (!host) return;
    if (!S.config || !S.gate) {
      host.innerHTML = S.configError
        ? '<div class="auto-card"><div class="auto-empty txt-red">配置加载失败：' + esc(S.configError)
          + '</div><div class="auto-actions"><button class="auto-btn" id="auto-cfg-retry">重试</button></div></div>'
        : '<div class="auto-card"><div class="auto-empty">配置加载中…</div></div>';
      var rb = $('auto-cfg-retry');
      if (rb) rb.addEventListener('click', function () {
        S.configError = '';
        renderConfigTab();
        refreshAll().then(renderConfigTab);
      });
      return;
    }
    var c = S.config, g = S.gate;
    var level = statusLevel();
    var html = '';

    // ── 熔断红色横幅 ──
    if (g.breaker && g.breaker.tripped) {
      html += '<div class="auto-breaker-banner">'
        + '<span>⚠ 连败熔断已触发（' + esc(g.breaker.tripped_at || '') + '），自动交易已全部停止，须人工复位。</span>'
        + '<button class="auto-btn auto-btn-danger" data-action="reset-breaker">重置并恢复</button>'
        + '</div>';
    }

    // ── 状态行：网关灯 + 模式说明 + monitor/smtp ──
    var mon = g.monitor || {};
    var smtp = g.smtp || {};
    var smtpTxt = smtp.configured
      ? (smtp.ok ? '<span class="txt-green">SMTP 正常</span>' : '<span class="txt-red">SMTP 失败：' + esc(smtp.last_error || '') + '</span>')
      : '<span class="txt-gold">SMTP 未配置（仅记录不发送）</span>';
    html += '<div class="auto-card auto-status-row">'
      + '<span class="auto-lamp lamp-' + level + '"></span>'
      + '<div class="auto-status-text"><strong>' + esc(statusText(level)) + '</strong>'
      + '<div class="auto-sub">监控循环：' + (mon.running ? '<span class="txt-green">运行中</span>' : '<span class="txt-muted">未运行</span>')
      + ' ｜ 最近扫描：' + esc(mon.last_scan_at || '-')
      + ' ｜ 连续异常：' + (mon.consecutive_errors || 0)
      + ' ｜ ' + smtpTxt + '</div>'
      + (mon.last_error ? '<div class="auto-sub txt-red">最近错误：' + esc(mon.last_error) + '</div>' : '')
      + '</div></div>';

    // ── 自主虚拟盘演练（一键初始化 + 跑一轮完整买卖复盘） ──
    html += '<div class="auto-card"><div class="auto-card-title">自主虚拟盘演练</div>'
      + '<div class="auto-sub">一键初始化虚拟盘（开启模拟开关、白名单覆盖全池、为已回测/验证中策略启用默认标的），或跑一轮完整「买入 → 自动平仓 → 单笔复盘 → 迭代决策」闭环。</div>'
      + '<div class="auto-actions">'
      + '<button class="auto-btn auto-btn-green" data-action="init-paper">一键初始化虚拟盘</button>'
      + '<button class="auto-btn" data-action="run-cycle">跑一轮自主买卖+复盘</button>'
      + '</div></div>';

    // ── 三档开关行 ──
    var liveMaster = !!g.live_master_switch;
    var anyPaperPassed = (g.strategies || []).some(function (s) { return s.gate_state === 'paper_passed'; });
    var liveDisabled = !anyPaperPassed || (g.breaker && g.breaker.tripped);
    html += '<div class="auto-card"><div class="auto-card-title">三档开关</div>'
      + '<div class="auto-switch-row">'
      + switchHtml('notify', '邮件提醒', c.switches.notify, false)
      + switchHtml('paper', '模拟交易', c.switches.paper, false)
      + switchHtml('live', '实盘交易', liveMaster, liveDisabled)
      + '</div>'
      + '<div class="auto-sub">实盘开关' + (liveDisabled ? '置灰：需至少一个策略模拟通过（paper_passed）且未熔断' : '开启需二次确认') + '；信号提示，供参考。</div>'
      + '<div id="auto-live-confirm-wrap"></div>'
      + '</div>';

    // ── 监控与触发条件 ──
    var t = c.trigger || {};
    var signalTypeChecks = CONFIGURABLE_SIGNAL_TYPES;
    html += '<div class="auto-card"><div class="auto-card-title">监控与触发条件</div>'
      + '<div class="auto-form-grid">'
      + '<label>监控范围</label><select id="auto-scope-mode">'
      + '<option value="pool"' + (c.scope.mode === 'pool' ? ' selected' : '') + '>全部股池</option>'
      + '<option value="watchlist"' + (c.scope.mode === 'watchlist' ? ' selected' : '') + '>自选清单</option></select>'
      + '<label>自选清单</label><input id="auto-watchlist" placeholder="sh600519,sz000858（逗号分隔）" value="' + esc((c.scope.watchlist || []).join(',')) + '">'
      + '<label>评分阈值</label><input id="auto-score-th" type="number" min="0" max="100" step="5" value="' + esc(t.score_threshold) + '">'
      + '<label>涨跌幅异动±%</label><input id="auto-chg-th" type="number" min="0.5" max="20" step="0.5" value="' + esc(t.chg_pct_threshold) + '">'
      + '<label>信号冷却</label><select id="auto-cooldown">'
      + [15, 30, 60, 120].map(function (m) {
        return '<option value="' + m + '"' + (Number(c.cooldown_minutes) === m ? ' selected' : '') + '>' + m + ' 分钟</option>';
      }).join('') + '</select>'
      + '<label>信号类型</label><div class="auto-checks">'
      + signalTypeChecks.map(function (tp) {
        var on = (t.signal_types || []).indexOf(tp) >= 0;
        return '<label class="auto-check"><input type="checkbox" class="auto-signal-type" value="' + tp + '"' + (on ? ' checked' : '') + '> ' + (SIGNAL_TYPE_LABELS[tp] || tp) + '</label>';
      }).join('') + '</div>'
      + '</div>'
      + '<div class="auto-actions"><button class="auto-btn" data-action="save-monitor">保存监控设置</button></div>'
      + '</div>';

    // ── 邮件通道 ──
    var em = c.email || {};
    html += '<div class="auto-card"><div class="auto-card-title">邮件通道（QQ SMTP）</div>'
      + '<div class="auto-form-grid">'
      + '<label>收件邮箱</label><input id="auto-email-to" type="email" placeholder="user@qq.com" value="' + esc(em.recipient || '') + '">'
      + '<label>单标的上限</label><input id="auto-email-per-stock" type="number" min="1" max="50" value="' + esc(em.per_stock_per_day || 5) + '">'
      + '<label>全局每小时</label><input id="auto-email-global" type="number" min="1" max="200" value="' + esc(em.global_per_hour || 20) + '">'
      + '</div>'
      + '<div class="auto-actions">'
      + '<button class="auto-btn" data-action="save-email">保存邮件设置</button>'
      + '<button class="auto-btn auto-btn-ghost" data-action="test-mail">发送测试邮件</button>'
      + '<span class="auto-sub">' + (smtp.configured ? '通道凭证已配置' : '授权码未配置时邮件仅落库不发送（信号提示，供参考）') + '</span>'
      + '</div></div>';

    // ── 风控参数 ──
    var rk = c.risk || {};
    html += '<div class="auto-card"><div class="auto-card-title">风控参数</div>'
      + '<div class="auto-form-grid">'
      + '<label>单笔限额(元)</label><input id="auto-risk-per-order" type="number" min="1000" step="1000" value="' + esc(rk.max_amount_per_order) + '">'
      + '<label>单日限额(元)</label><input id="auto-risk-per-day" type="number" min="1000" step="1000" value="' + esc(rk.max_amount_per_day) + '">'
      + '<label>持仓数上限</label><input id="auto-risk-max-pos" type="number" min="1" max="50" value="' + esc(rk.max_positions) + '">'
      + '<label>连败熔断</label><input id="auto-risk-breaker" type="number" min="1" max="10" value="' + esc(rk.breaker_fail_threshold) + '">'
      + '<label>日亏熔断%</label><input id="auto-risk-dayloss" type="number" min="1" max="50" step="0.5" value="' + esc(rk.daily_loss_limit_pct) + '">'
      + '<label>白名单</label><input id="auto-risk-whitelist" placeholder="留空=全部只提醒不下单" value="' + esc((rk.whitelist || []).join(',')) + '">'
      + '</div>'
      + '<div class="auto-actions"><button class="auto-btn" data-action="save-risk">保存风控参数</button></div>'
      + '</div>';

    // ── 交易成本与出场（成交成本模型 + 止盈 + 真实约束开关）──
    html += '<div class="auto-card"><div class="auto-card-title">交易成本与出场</div>'
      + '<div class="auto-form-grid">'
      + '<label>滑点 bps（1bp=0.01%，默认5）</label><input id="auto-cost-slip" type="number" min="0" step="1" value="' + esc(ri(rk.slippage_bps, 5)) + '">'
      + '<label>佣金率 万分之（万2.5填2.5，默认2.5）</label><input id="auto-cost-comm-rate" type="number" min="0" step="0.1" value="' + esc(wan(rk.commission_rate, 2.5)) + '">'
      + '<label>最低佣金 元/笔（默认5）</label><input id="auto-cost-comm-min" type="number" min="0" step="1" value="' + esc(ri(rk.commission_min, 5)) + '">'
      + '<label>印花税 万分之（仅卖出，默认5）</label><input id="auto-cost-stamp" type="number" min="0" step="0.1" value="' + esc(wan(rk.stamp_tax_rate, 5)) + '">'
      + '<label>过户费 万分之（买卖均收，默认0.1）</label><input id="auto-cost-transfer" type="number" min="0" step="0.01" value="' + esc(wan(rk.transfer_fee_rate, 0.1)) + '">'
      + '<label>固定止盈 %（0=关闭，默认0）</label><input id="auto-cost-tp" type="number" min="0" step="1" value="' + esc(ri(rk.take_profit_pct, 0)) + '">'
      + '<label>移动止盈 %（自最高价回撤，0=关闭，默认0）</label><input id="auto-cost-ts" type="number" min="0" step="1" value="' + esc(ri(rk.trailing_stop_pct, 0)) + '">'
      + '<label>涨跌停校验（涨停拒买/跌停拒卖）</label><div class="auto-checks"><label class="auto-check"><input id="auto-cost-limit" type="checkbox"' + (rk.check_price_limit === false ? '' : ' checked') + '> 启用</label></div>'
      + '<label>停牌校验（无量无额视为停牌并拒单）</label><div class="auto-checks"><label class="auto-check"><input id="auto-cost-suspend" type="checkbox"' + (rk.check_suspension === false ? '' : ' checked') + '> 启用</label></div>'
      + '</div>'
      + '<div class="auto-actions"><button class="auto-btn" data-action="save-cost">保存成本与出场参数</button>'
      + '<span class="auto-sub">成交价按滑点上浮/下浮，佣金≥最低值，印花税仅卖出，过户费双向收。</span></div>'
      + '</div>';

    // ── 决策闸参数（四维权重 / 置信度阈值 / 波动目标 / 条件单过期）──
    var dg = c.decision_gate || {};
    var gw = dg.weights || {};
    var up = c.updated_at || {};
    html += '<div class="auto-card"><div class="auto-card-title">决策闸参数（买入必过：板块 + 估值 + 事件 + 技术）</div>'
      + '<div class="auto-sub">四维加权得分 ≥ 置信度阈值的买入信号才放行；卖出永不拦截。'
      + '改动即时生效（缓存 30 秒内刷新），逐项校验、非法值忽略。'
      + (up.decision_gate ? ' ｜ 上次修改 ' + esc(up.decision_gate) : ' ｜ 尚未自定义（使用默认值）')
      + '</div>'
      + '<div class="auto-form-grid">'
      + '<label>板块权重</label><input id="auto-gate-w-sector" type="number" min="0" max="1" step="0.05" value="' + esc(gw.sector == null ? 0.35 : gw.sector) + '">'
      + '<label>估值权重</label><input id="auto-gate-w-valuation" type="number" min="0" max="1" step="0.05" value="' + esc(gw.valuation == null ? 0.2 : gw.valuation) + '">'
      + '<label>事件权重</label><input id="auto-gate-w-events" type="number" min="0" max="1" step="0.05" value="' + esc(gw.events == null ? 0.15 : gw.events) + '">'
      + '<label>技术权重</label><input id="auto-gate-w-technical" type="number" min="0" max="1" step="0.05" value="' + esc(gw.technical == null ? 0.3 : gw.technical) + '">'
      + '<label>置信度阈值（0~1）</label><input id="auto-gate-threshold" type="number" min="0.1" max="0.95" step="0.05" value="' + esc(dg.allow_threshold == null ? 0.5 : dg.allow_threshold) + '">'
      + '<label>目标日波动 %（仓位反比基准）</label><input id="auto-gate-vol" type="number" min="0.5" max="10" step="0.5" value="' + esc(dg.target_daily_vol_pct == null ? 2.0 : dg.target_daily_vol_pct) + '">'
      + '<label>条件单过期天数（0=不过期）</label><input id="auto-gate-expire" type="number" min="0" max="365" step="1" value="' + esc(c.conditional_expire_days == null ? 30 : c.conditional_expire_days) + '">'
      + '</div>'
      + '<div class="auto-actions"><button class="auto-btn" data-action="save-gate">保存决策闸参数</button>'
      + '<span class="auto-sub">权重不必加总为 1（系统按可得维度重新归一化）；某维数据不可得时自动跳过并在决策留痕中标注原因。</span></div>'
      + '</div>';

    // ── 策略区 ──
    html += renderStrategyCard();

    host.innerHTML = html;
    bindConfigActions(host);
    renderLiveConfirmWrap();
  }

  // 策略显示名：后端 display_name（Strategy.name）为唯一真相源，未返回时回退本地字典
  function strategyLabel(spec) {
    if (!spec) return '';
    return spec.display_name || spec.name || STRATEGY_NAMES[spec.strategy_id] || spec.strategy_id || '';
  }

  function switchHtml(key, label, on, disabled) {    return '<label class="auto-switch' + (disabled ? ' disabled' : '') + '" title="' + (disabled ? '需至少一个策略 paper_passed' : '') + '">'
      + '<input type="checkbox" data-switch="' + key + '"' + (on ? ' checked' : '') + (disabled ? ' disabled' : '') + '>'
      + '<span class="auto-switch-pill"></span><span class="auto-switch-label">' + label + '</span></label>';
  }

  function renderStrategyCard() {
    var c = S.config, g = S.gate;
    var specs = (c && c.strategies) || [];
    if (!specs.length) return '<div class="auto-card"><div class="auto-card-title">策略</div><div class="auto-empty">暂无策略</div></div>';
    if (!S.selectedStrategy || !specs.some(function (s) { return s.strategy_id === S.selectedStrategy; })) {
      S.selectedStrategy = specs[0].strategy_id;
    }
    var spec = null, i;
    for (i = 0; i < specs.length; i++) if (specs[i].strategy_id === S.selectedStrategy) spec = specs[i];
    var gateSpec = ((g && g.strategies) || []).filter(function (s) { return s.strategy_id === spec.strategy_id; })[0] || {};
    var gateState = gateSpec.gate_state || spec.gate_state || 'draft';
    var gsLabel = GATE_STATE_LABELS[gateState] || [gateState, 'muted'];
    var schema = PARAM_SCHEMAS[spec.strategy_id] || [];
    var params = spec.params || {};

    var html = '<div class="auto-card"><div class="auto-card-title">策略</div>'
      + '<div class="auto-form-grid">'
      + '<label>选择策略</label><select id="auto-strategy-select">'
      + specs.map(function (s) {
        return '<option value="' + esc(s.strategy_id) + '"' + (s.strategy_id === S.selectedStrategy ? ' selected' : '') + '>'
          + esc(strategyLabel(s)) + '</option>';
      }).join('') + '</select>'
      + '<label>门禁状态</label><div><span class="auto-badge badge-' + gsLabel[1] + '">' + gsLabel[0] + '</span>'
      + (gateState === 'paper_validating'
        ? ' <span class="auto-sub">模拟进度 ' + (spec.paper_days_done || 0) + '/' + (spec.paper_days_required || 5) + ' 交易日</span>'
        : '')
      + '</div>';

    // 参数表单
    for (i = 0; i < schema.length; i++) {
      var p = schema[i];
      var v = params[p.key] != null ? params[p.key] : p.def;
      html += '<label>' + esc(p.label) + '</label><input class="auto-param" data-key="' + p.key
        + '" type="number" step="' + p.step + '" value="' + esc(v) + '">';
    }
    html += '<label>回测标的</label><input id="auto-bt-code" value="' + esc(currentCode()) + '" placeholder="sh600519">'
      + '<label>启用标的</label><input id="auto-enable-codes" placeholder="sh600519,sz000858" value="' + esc((spec.enabled_codes || []).join(',')) + '">'
      + '</div>';

    // 回测结果摘要条
    var bt = spec.last_backtest || gateSpec.backtest;
    html += '<div id="auto-bt-result" class="auto-bt-result"' + (bt ? '' : ' style="display:none"') + '>'
      + (bt ? btResultHtml(bt) : '') + '</div>';

    // 操作按钮行
    var canPaper = ['backtested', 'paper_validating', 'paper_passed'].indexOf(gateState) >= 0;
    var canConfirm = gateState === 'paper_validating';
    var canLive = gateState === 'paper_passed';
    html += '<div class="auto-actions">'
      + '<button class="auto-btn" data-action="run-backtest">运行回测</button>'
      + '<button class="auto-btn auto-btn-ghost" data-action="save-params">保存参数</button>'
      + '<button class="auto-btn auto-btn-green" data-action="enable-paper"' + (canPaper && gateState !== 'paper_validating' ? '' : ' disabled') + '>启用模拟</button>'
      + (canConfirm ? '<button class="auto-btn auto-btn-green" data-action="paper-confirm">确认模拟通过</button>' : '')
      + '<button class="auto-btn auto-btn-danger" data-action="enable-live"' + (canLive ? '' : ' disabled')
      + ' title="' + (canLive ? '需实盘总开关已开启' : '需模拟通过（paper_passed）') + '">启用实盘</button>'
      + '</div>';
    if (gateSpec.block_reason) {
      html += '<div class="auto-sub txt-gold">实盘阻塞：' + esc(gateSpec.block_reason) + '</div>';
    }
    html += '<div class="auto-sub">参数任何修改将回退门禁为草稿（回测/模拟结论作废，重走全链）。信号提示，供参考。</div>'
      + '</div>';
    return html;
  }

  function btResultHtml(bt) {
    return '<span class="auto-bt-item">胜率 <strong>' + fmtPct((bt.win_rate || 0) * 100, 1) + '</strong></span>'
      + '<span class="auto-bt-item">年化 <strong>' + fmtSignedPct01(bt.annual_return) + '</strong></span>'
      + '<span class="auto-bt-item">最大回撤 <strong>' + fmtSignedPct01(bt.max_drawdown) + '</strong></span>'
      + '<span class="auto-bt-item">交易 <strong>' + esc(bt.total_trades != null ? bt.total_trades : '-') + ' 次</strong></span>'
      + '<span class="auto-bt-item">盈亏比 <strong>' + esc(bt.profit_factor != null ? bt.profit_factor : '-') + '</strong></span>'
      + (bt.code ? '<span class="auto-bt-item auto-sub">' + esc(bt.code) + (bt.passed_at ? ' ｜ ' + esc(bt.passed_at) : '') + '</span>' : '');
  }

  // ------------------------------------------------------------
  // 配置 Tab 交互
  // ------------------------------------------------------------
  function bindConfigActions(host) {
    // 三档开关
    var switches = host.querySelectorAll('[data-switch]');
    for (var i = 0; i < switches.length; i++) {
      switches[i].addEventListener('change', onSwitchChange);
    }
    // 按钮
    host.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-action]');
      if (!btn || btn.disabled) return;
      var action = btn.getAttribute('data-action');
      btn.disabled = true;
      var done = function () { btn.disabled = false; };
      handleAction(action).then(done, done);
    });
    // 策略切换
    var sel = $('auto-strategy-select');
    if (sel) sel.addEventListener('change', function () {
      S.selectedStrategy = sel.value;
      renderConfigTab();
    });
  }

  function onSwitchChange(e) {
    var key = e.target.getAttribute('data-switch');
    var on = e.target.checked;
    if (key === 'live') {
      e.target.checked = !on; // 先还原，走二次确认/关闭流程
      if (on) {
        S.liveConfirmOpen = true;
        renderLiveConfirmWrap();
      } else {
        doLiveSwitch(false, '', '');
      }
      return;
    }
    var body = { switches: {} };
    body.switches[key] = on;
    api('/config', 'POST', body).then(function (r) {
      if (!r.ok) { toast(r.message, true); e.target.checked = !on; }
      else toast(on ? '已开启' : '已关闭');
      refreshAll().then(renderConfigTab);
    });
  }

  function renderLiveConfirmWrap() {
    var wrap = $('auto-live-confirm-wrap');
    if (!wrap) return;
    if (!S.liveConfirmOpen) { wrap.innerHTML = ''; return; }
    var passed = ((S.gate && S.gate.strategies) || []).filter(function (s) { return s.gate_state === 'paper_passed'; });
    wrap.innerHTML = '<div class="auto-live-confirm">'
      + '<div class="auto-live-warn">⚠ 实盘交易将按策略信号自动下单，可能产生真实亏损。请准确输入「' + LIVE_CONFIRM_TEXT + '」以确认。信号提示，供参考。</div>'
      + '<div class="auto-live-confirm-row">'
      + '<select id="auto-live-strategy">'
      + (passed.length ? passed.map(function (s) {
        return '<option value="' + esc(s.strategy_id) + '">' + esc(strategyLabel(s)) + '</option>';
      }).join('') : '<option value="">（无 paper_passed 策略）</option>')
      + '</select>'
      + '<input id="auto-live-confirm-text" placeholder="' + LIVE_CONFIRM_TEXT + '">'
      + '<button class="auto-btn auto-btn-danger" data-live-confirm="1">确认开启</button>'
      + '<button class="auto-btn auto-btn-ghost" data-live-confirm="0">取消</button>'
      + '</div></div>';
    wrap.querySelector('[data-live-confirm="1"]').addEventListener('click', function () {
      var sid = ($('auto-live-strategy') || {}).value || '';
      var text = ($('auto-live-confirm-text') || {}).value || '';
      doLiveSwitch(true, text, sid);
    });
    wrap.querySelector('[data-live-confirm="0"]').addEventListener('click', function () {
      S.liveConfirmOpen = false;
      renderLiveConfirmWrap();
    });
  }

  function doLiveSwitch(on, confirmText, strategyId) {
    api('/gate/live-switch', 'POST', { on: on, confirm_text: confirmText, strategy_id: strategyId }).then(function (r) {
      if (r.ok) {
        S.liveConfirmOpen = false;
        toast(r.message || (on ? '实盘已开启' : '实盘已关闭'));
      } else {
        toast(r.message, true);
      }
      refreshAll().then(renderConfigTab);
    });
  }

  function readParams() {
    var params = {};
    var inputs = document.querySelectorAll('#auto-tab-config .auto-param');
    for (var i = 0; i < inputs.length; i++) {
      var v = parseFloat(inputs[i].value);
      if (isFinite(v)) params[inputs[i].getAttribute('data-key')] = v;
    }
    return params;
  }

  function parseCodes(raw) {
    return String(raw || '').split(/[,，\s]+/).map(function (s) {
      return s.trim().toLowerCase();
    }).filter(function (s) { return /^(sh|sz|hk)\d{5,6}$/.test(s); });
  }

  function handleAction(action) {
    switch (action) {
      case 'reset-breaker':
        return api('/risk/reset-breaker', 'POST', {}).then(function (r) {
          toast(r.ok ? '熔断已复位' : r.message, !r.ok);
          return refreshAll().then(renderConfigTab);
        });
      case 'init-paper':
        return api('/init-paper', 'POST', {}).then(function (r) {
          if (r.ok && r.data) {
            toast('虚拟盘已初始化：' + (r.data.enabled_strategies || []).length + ' 个策略启用、白名单 ' + (r.data.whitelist_size || 0) + ' 只');
          } else {
            toast(r.message || '初始化失败', true);
          }
          return refreshAll().then(renderConfigTab);
        });
      case 'run-cycle':
        toast('正在跑一轮自主虚拟盘买卖 + 复盘…');
        return api('/run-cycle', 'POST', {}).then(function (r) {
          var d = r.data || {};
          if (r.ok) {
            toast('一轮完成：买入 ' + (d.buys || []).length + ' 笔 / 平仓 ' + (d.sells || []).length + ' 笔 / 新增复盘 ' + (d.new_reviews || 0) + ' 条');
          } else {
            toast(r.message || '演练执行失败', true);
          }
          return refreshAll().then(renderConfigTab);
        });
      case 'save-monitor': {
        var types = [];
        var checks = document.querySelectorAll('#auto-tab-config .auto-signal-type');
        for (var i = 0; i < checks.length; i++) if (checks[i].checked) types.push(checks[i].value);
        var sk = [];
        var payload = dropUndefined({
          scope: { mode: $('auto-scope-mode').value, watchlist: parseCodes($('auto-watchlist').value) },
          trigger: dropUndefined({
            score_threshold: numField('auto-score-th', '触发分数阈值', sk),
            signal_types: types,
            chg_pct_threshold: numField('auto-chg-th', '涨跌幅阈值', sk)
          }),
          cooldown_minutes: numField('auto-cooldown', '冷静期(分钟)', sk)
        });
        return api('/config', 'POST', payload).then(function (r) {
          toast(r.ok ? ('监控设置已保存' + skipNote(sk)) : r.message, !r.ok);
          return refreshAll().then(renderConfigTab);
        });
      }
      case 'save-email': {
        var ske = [];
        return api('/config', 'POST', {
          email: dropUndefined({
            recipient: $('auto-email-to').value.trim(),
            per_stock_per_day: numField('auto-email-per-stock', '单标的上限', ske),
            global_per_hour: numField('auto-email-global', '全局每小时', ske)
          })
        }).then(function (r) {
          toast(r.ok ? ('邮件设置已保存' + skipNote(ske)) : r.message, !r.ok);
          return refreshAll().then(renderConfigTab);
        });
      }
      case 'test-mail':
        return api('/notify/test', 'POST', { to: $('auto-email-to').value.trim() }).then(function (r) {
          if (r.ok && r.data && r.data.sent) toast('测试邮件已发送');
          else toast(r.message || '未发送（通道未配置时仅记录）', true);
          return refreshAll().then(renderConfigTab);
        });
      case 'save-risk': {
        var skr = [];
        var risk = dropUndefined({
          max_amount_per_order: numField('auto-risk-per-order', '单笔限额', skr),
          max_amount_per_day: numField('auto-risk-per-day', '单日限额', skr),
          max_positions: numField('auto-risk-max-pos', '持仓数上限', skr),
          breaker_fail_threshold: numField('auto-risk-breaker', '连败熔断', skr),
          daily_loss_limit_pct: numField('auto-risk-dayloss', '日亏熔断%', skr),
          whitelist: parseCodes($('auto-risk-whitelist').value)
        });
        return api('/config', 'POST', { risk: risk }).then(function (r) {
          toast(r.ok ? ('风控参数已保存' + skipNote(skr)) : r.message, !r.ok);
          return refreshAll().then(renderConfigTab);
        });
      }
      case 'save-cost': {
        // 万分之输入 → 原始费率（÷10000）；止盈 % 直传（0=关闭，允许 0）；
        // 留空字段不提交（保持服务端原值），不再用默认值顶替空输入
        var skc = [];
        var cost = dropUndefined({
          slippage_bps: numField('auto-cost-slip', '滑点bps', skc),
          commission_rate: numField('auto-cost-comm-rate', '佣金率', skc, 10000),
          commission_min: numField('auto-cost-comm-min', '最低佣金', skc),
          stamp_tax_rate: numField('auto-cost-stamp', '印花税', skc, 10000),
          transfer_fee_rate: numField('auto-cost-transfer', '过户费', skc, 10000),
          take_profit_pct: numField('auto-cost-tp', '固定止盈%', skc),
          trailing_stop_pct: numField('auto-cost-ts', '移动止盈%', skc),
          check_price_limit: readChecked('auto-cost-limit', true),
          check_suspension: readChecked('auto-cost-suspend', true)
        });
        if (Object.keys(cost).length === 0) { toast('无有效输入，未提交', true); return Promise.resolve(); }
        return api('/config', 'POST', { risk: cost }).then(function (r) {
          toast(r.ok ? ('成本与出场参数已保存' + skipNote(skc)) : r.message, !r.ok);
          return refreshAll().then(renderConfigTab);
        });
      }
      case 'save-gate': {
        var skg = [];
        var gateBody = {
          decision_gate: dropUndefined({
            weights: dropUndefined({
              sector: numField('auto-gate-w-sector', '板块权重', skg),
              valuation: numField('auto-gate-w-valuation', '估值权重', skg),
              events: numField('auto-gate-w-events', '事件权重', skg),
              technical: numField('auto-gate-w-technical', '技术权重', skg)
            }),
            allow_threshold: numField('auto-gate-threshold', '置信度阈值', skg),
            target_daily_vol_pct: numField('auto-gate-vol', '目标日波动%', skg)
          }),
          conditional_expire_days: numField('auto-gate-expire', '条件单过期天数', skg)
        };
        // 清掉空的 weights（全部留空时不下发该子对象）
        if (gateBody.decision_gate && Object.keys(gateBody.decision_gate.weights).length === 0) {
          delete gateBody.decision_gate.weights;
        }
        if (gateBody.decision_gate && Object.keys(gateBody.decision_gate).length === 0) {
          delete gateBody.decision_gate;
        }
        if (Object.keys(gateBody).length === 0) { toast('无有效输入，未提交', true); return Promise.resolve(); }
        return api('/config', 'POST', gateBody).then(function (r) {
          toast(r.ok ? ('决策闸参数已保存' + skipNote(skg)) : r.message, !r.ok);
          return refreshAll().then(renderConfigTab);
        });
      }
      case 'save-params':
        return api('/config', 'POST', {
          strategies: [{ strategy_id: S.selectedStrategy, params: readParams() }]
        }).then(function (r) {
          toast(r.ok ? '参数已保存（门禁已回退草稿，需重新回测）' : r.message, !r.ok);
          return refreshAll().then(renderConfigTab);
        });
      case 'run-backtest':
        return runBacktest();
      case 'enable-paper':
        return api('/strategy/enable', 'POST', {
          strategy_id: S.selectedStrategy, target: 'paper',
          codes: parseCodes(($('auto-enable-codes') || {}).value)
        }).then(function (r) {
          toast(r.ok ? (r.message || '已启用模拟验证') : r.message, !r.ok);
          return refreshAll().then(renderConfigTab);
        });
      case 'paper-confirm':
        return api('/strategy/paper/confirm', 'POST', { strategy_id: S.selectedStrategy }).then(function (r) {
          toast(r.ok ? '已确认模拟通过' : r.message, !r.ok);
          return refreshAll().then(renderConfigTab);
        });
      case 'enable-live':
        return api('/strategy/enable', 'POST', {
          strategy_id: S.selectedStrategy, target: 'live',
          codes: parseCodes(($('auto-enable-codes') || {}).value)
        }).then(function (r) {
          toast(r.ok ? '已启用实盘' : r.message, !r.ok);
          return refreshAll().then(renderConfigTab);
        });
      default:
        return Promise.resolve();
    }
  }

  function runBacktest() {
    var host = $('auto-bt-result');
    if (host) { host.style.display = ''; host.innerHTML = '<span class="auto-sub">回测运行中（拉取日K并逐日重放）…</span>'; }
    var code = (($('auto-bt-code') || {}).value || '').trim().toLowerCase() || currentCode();
    return api('/strategy/backtest', 'POST', {
      strategy_id: S.selectedStrategy, code: code, params: readParams(), days: 250
    }).then(function (r) {
      if (r.ok && r.data) {
        if (host) host.innerHTML = btResultHtml(r.data);
        toast('回测完成，策略已进入"已回测"状态');
      } else {
        if (host) host.innerHTML = '<span class="txt-red">' + esc(r.message) + '</span>';
        toast(r.message, true);
      }
      return refreshAll().then(function () {
        // 保留结果条（renderConfigTab 会用 last_backtest 重绘）
        renderConfigTab();
        var host2 = $('auto-bt-result');
        if (host2 && r.ok && r.data) { host2.style.display = ''; host2.innerHTML = btResultHtml(r.data); }
      });
    });
  }

  // ------------------------------------------------------------
  // 信号历史 Tab
  // ------------------------------------------------------------
  function loadSignals(reset) {
    var st = S.signals;
    if (st.loading) return Promise.resolve();
    var keepScroll = reset ? 0 : (($('auto-signals-scroll') || {}).scrollTop || 0);
    if (reset) { st.items = []; st.total = 0; }
    st.loading = true;
    var q = '?limit=' + PAGE_SIZE + '&offset=' + st.items.length;
    if (st.code) q += '&code=' + encodeURIComponent(st.code);
    var host = $('auto-tab-signals');
    if (reset && host && !st.items.length) host.innerHTML = '<div class="auto-empty">信号加载中…</div>';
    return api('/signals' + q).then(function (r) {
      st.loading = false;
      if (!r.ok || !r.data) {
        if (host) {
          host.innerHTML = '<div class="auto-card"><div class="auto-empty txt-red">信号加载失败：' + esc(r.message || '接口不可用')
            + '</div><div class="auto-actions"><button class="auto-btn" id="auto-signal-retry">重试</button></div></div>';
          var rb = $('auto-signal-retry');
          if (rb) rb.addEventListener('click', function () { loadSignals(true); });
        }
        return;
      }
      st.total = r.data.total || 0;
      st.items = st.items.concat(r.data.items || []);
      renderSignals();
      var sc = $('auto-signals-scroll');
      if (sc && keepScroll) sc.scrollTop = keepScroll;
    }, function (e) {
      st.loading = false;
      toast('信号加载异常：' + (e && e.message || e), true);
    });
  }

  function renderSignals() {
    var host = $('auto-tab-signals');
    if (!host) return;
    var st = S.signals;
    var html = '<div class="auto-filter-row">'
      + '<input id="auto-signal-code" placeholder="按标的筛选（如 sh600519）" value="' + esc(st.code) + '">'
      + '<button class="auto-btn" id="auto-signal-filter">筛选</button>'
      + '<button class="auto-btn auto-btn-ghost" id="auto-signal-clear">清除</button>'
      + '<span class="auto-sub">共 ' + st.total + ' 条</span>'
      + '<button class="auto-btn auto-btn-ghost" id="auto-signal-refresh">刷新</button>'
      + '</div>'
      + '<div class="auto-table-scroll" id="auto-signals-scroll"><div class="data-table-wrap"><table class="data-table">'
      + '<thead><tr><th>时间</th><th>标的</th><th>信号</th><th class="col-num">价格</th><th class="col-num">涨跌幅</th><th class="col-num">评分</th><th>来源</th><th>推送状态</th></tr></thead>'
      + '<tbody id="auto-signals-tbody">';
    html += st.items.map(signalRowHtml).join('');
    html += '</tbody></table>'
      + (st.items.length === 0 ? '<div class="auto-empty">暂无信号记录（盘中监控触发后落库）</div>' : '')
      + (st.items.length < st.total ? '<div class="auto-empty">下拉加载更多…</div>' : '')
      + '</div></div>';
    host.innerHTML = html;

    $('auto-signal-filter').addEventListener('click', function () {
      S.signals.code = $('auto-signal-code').value.trim().toLowerCase();
      loadSignals(true);
    });
    $('auto-signal-code').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { S.signals.code = e.target.value.trim().toLowerCase(); loadSignals(true); }
    });
    $('auto-signal-clear').addEventListener('click', function () { S.signals.code = ''; loadSignals(true); });
    $('auto-signal-refresh').addEventListener('click', function () { loadSignals(true); });
    $('auto-signals-scroll').addEventListener('scroll', function () {
      var el = $('auto-signals-scroll');
      if (el && el.scrollTop + el.clientHeight >= el.scrollHeight - 60) loadSignals(false);
    });
  }

  function signalRowHtml(s) {
    var typeLabel = (s.detail && s.detail.label) || SIGNAL_TYPE_LABELS[s.signal_type] || s.signal_type;
    var typeDesc = (s.detail && s.detail.desc) || '';
    var ps = PUSH_STATUS_LABELS[s.push_status] || [s.push_status, 'muted'];
    var src = s.source === 'panel' ? '面板' : ('策略:' + esc(s.source.replace('strategy:', '')));
    return '<tr title="' + esc(typeDesc) + '">'
      + '<td>' + esc(s.ts) + '</td>'
      + '<td>' + esc(s.name) + '<span class="auto-code">' + esc(s.code) + '</span></td>'
      + '<td>' + esc(typeLabel) + '</td>'
      + '<td class="col-num">' + fmtNum(s.price) + '</td>'
      + '<td class="col-num txt-' + pnlClass(s.chg_pct) + '">' + fmtPct(s.chg_pct) + '</td>'
      + '<td class="col-num">' + (s.score != null ? esc(s.score) : '-') + '</td>'
      + '<td>' + src + '</td>'
      + '<td><span class="auto-badge badge-' + ps[1] + '">' + ps[0] + '</span></td>'
      + '</tr>';
  }

  // ------------------------------------------------------------
  // 订单流水 Tab
  // ------------------------------------------------------------
  function loadOrders(reset) {
    var st = S.orders;
    if (st.loading) return Promise.resolve();
    var keepScroll = reset ? 0 : (($('auto-orders-scroll') || {}).scrollTop || 0);
    if (reset) { st.items = []; st.total = 0; }
    st.loading = true;
    var q = '?limit=' + PAGE_SIZE + '&offset=' + st.items.length;
    if (st.code) q += '&code=' + encodeURIComponent(st.code);
    var host = $('auto-tab-orders');
    if (reset && host && !st.items.length) host.innerHTML = '<div class="auto-empty">订单加载中…</div>';
    return api('/orders' + q).then(function (r) {
      st.loading = false;
      if (!r.ok || !r.data) {
        if (host) {
          host.innerHTML = '<div class="auto-card"><div class="auto-empty txt-red">订单加载失败：' + esc(r.message || '接口不可用')
            + '</div><div class="auto-actions"><button class="auto-btn" id="auto-order-retry">重试</button></div></div>';
          var rb = $('auto-order-retry');
          if (rb) rb.addEventListener('click', function () { loadOrders(true); });
        }
        return;
      }
      st.total = r.data.total || 0;
      st.items = st.items.concat(r.data.items || []);
      renderOrders();
      var sc = $('auto-orders-scroll');
      if (sc && keepScroll) sc.scrollTop = keepScroll;
    }, function (e) {
      st.loading = false;
      toast('订单加载异常：' + (e && e.message || e), true);
    });
  }

  function renderOrders() {
    var host = $('auto-tab-orders');
    if (!host) return;
    var st = S.orders;
    var html = '<div class="auto-filter-row">'
      + '<input id="auto-order-code" placeholder="按标的筛选（如 sh600519）" value="' + esc(st.code) + '">'
      + '<button class="auto-btn" id="auto-order-filter">筛选</button>'
      + '<button class="auto-btn auto-btn-ghost" id="auto-order-clear">清除</button>'
      + '<span class="auto-sub">共 ' + st.total + ' 条</span>'
      + '<button class="auto-btn auto-btn-ghost" id="auto-order-refresh">刷新</button>'
      + '</div>'
      + '<div class="auto-table-scroll" id="auto-orders-scroll"><div class="data-table-wrap"><table class="data-table">'
      + '<thead><tr><th>时间</th><th>标的</th><th>方向</th><th class="col-num">数量</th><th class="col-num">价格</th><th class="col-num">金额</th><th>网关</th><th>状态</th><th>失败原因</th></tr></thead>'
      + '<tbody id="auto-orders-tbody">';
    html += st.items.map(orderRowHtml).join('');
    html += '</tbody></table>'
      + (st.items.length === 0 ? '<div class="auto-empty">暂无订单（风控拒绝与成交都会记录在此）</div>' : '')
      + (st.items.length < st.total ? '<div class="auto-empty">下拉加载更多…</div>' : '')
      + '</div></div>';
    host.innerHTML = html;

    $('auto-order-filter').addEventListener('click', function () {
      S.orders.code = $('auto-order-code').value.trim().toLowerCase();
      loadOrders(true);
    });
    $('auto-order-code').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { S.orders.code = e.target.value.trim().toLowerCase(); loadOrders(true); }
    });
    $('auto-order-clear').addEventListener('click', function () { S.orders.code = ''; loadOrders(true); });
    $('auto-order-refresh').addEventListener('click', function () { loadOrders(true); });
    $('auto-orders-scroll').addEventListener('scroll', function () {
      var el = $('auto-orders-scroll');
      if (el && el.scrollTop + el.clientHeight >= el.scrollHeight - 60) loadOrders(false);
    });
  }

  function orderRowHtml(o) {
    var st = ORDER_STATUS_LABELS[o.status] || [o.status, 'muted'];
    var failed = o.status === 'failed' || o.status === 'rejected';
    return '<tr class="' + (failed ? 'auto-row-failed' : '') + '"' + (o.fail_reason ? ' title="' + esc(o.fail_reason) + '"' : '') + '>'
      + '<td>' + esc(o.ts) + '</td>'
      + '<td>' + esc(o.name) + '<span class="auto-code">' + esc(o.code) + '</span></td>'
      + '<td class="txt-' + (o.side === 'buy' ? 'red' : 'green') + '">' + (o.side === 'buy' ? '买入' : '卖出') + '</td>'
      + '<td class="col-num">' + fmtNum(o.qty, 0) + '</td>'
      + '<td class="col-num">' + fmtNum(o.price) + '</td>'
      + '<td class="col-num">' + fmtNum(o.amount) + '</td>'
      + '<td>' + esc(o.gateway)
      + (o.source ? ' <span class="auto-badge badge-muted">' + esc(o.source === 'manual' ? '手动' : (o.source === 'signal' ? '信号' : o.source)) + '</span>' : '')
      + '</td>'
      + '<td><span class="auto-badge badge-' + st[1] + '">' + st[0] + '</span></td>'
      + '<td class="auto-reason">' + esc(o.fail_reason || '-') + '</td>'
      + '</tr>';
  }

  // ------------------------------------------------------------
  // 模拟持仓 Tab
  // ------------------------------------------------------------
  function loadPositions() {
    var host = $('auto-tab-positions');
    if (host) host.innerHTML = '<div class="auto-empty">加载中…</div>';
    return api('/positions').then(function (r) {
      if (!r.ok) { if (host) host.innerHTML = '<div class="auto-empty txt-red">' + esc(r.message) + '</div>'; return; }
      renderPositions(r.data);
    });
  }

  function renderPositions(d) {
    var host = $('auto-tab-positions');
    if (!host) return;
    var items = d.items || [];
    var validating = ((S.gate && S.gate.strategies) || []).filter(function (s) { return s.gate_state === 'paper_validating'; });

    var html = '<div class="auto-pos-summary">'
      + summaryCard('总权益', d.total_equity == null ? '--' : ('¥' + fmtNum(d.total_equity)))
      + summaryCard('初始资金', d.initial_cash == null ? '--' : ('¥' + fmtNum(d.initial_cash, 0)))
      + summaryCard('可用资金', d.cash == null ? '--' : ('¥' + fmtNum(d.cash)))
      + summaryCard('持仓市值', d.market_value == null ? '--' : ('¥' + fmtNum(d.market_value)))
      + summaryCard('模拟验证进度', d.paper_days_done == null ? '--'
        : (d.paper_days_done + '/' + (d.paper_days_required == null ? '--' : d.paper_days_required) + ' 交易日'))
      + '</div>';
    // 自证徽章：本页为本地虚拟盘（paper）模拟数据，便于与真实行情/账户区分
    html += '<div class="auto-sub" style="margin:-2px 0 8px">数据口径：'
      + '<span class="auto-badge badge-' + (d.gateway === 'paper' || d.source === 'paper_sim' ? 'gold' : 'muted') + '">'
      + (d.gateway === 'paper' || d.source === 'paper_sim' ? '本地虚拟盘（paper）模拟数据' : '来源未知')
      + '</span>'
      + (d.trading_days == null ? '' : ' ｜ 已累计交易日 ' + d.trading_days + ' 天')
      + ' ｜ 实时行情来自腾讯，账户与订单为本地模拟</div>';

    if (validating.length) {
      html += '<div class="auto-card"><div class="auto-card-title">模拟验证进度</div>';
      for (var i = 0; i < validating.length; i++) {
        var s = validating[i];
        var pct = Math.min(100, Math.round((s.paper_days_done / Math.max(1, s.paper_days_required)) * 100));
        html += '<div class="auto-progress-row"><span class="auto-progress-name">'
          + esc(strategyLabel(s)) + '</span>'
          + '<div class="auto-progress"><i style="width:' + pct + '%"></i></div>'
          + '<span class="auto-sub">' + s.paper_days_done + '/' + s.paper_days_required + '</span></div>';
      }
      html += '</div>';
    }

    html += '<div class="data-table-wrap"><table class="data-table">'
      + '<thead><tr><th>标的</th><th class="col-num">持仓数量</th><th class="col-num">平均成本</th><th class="col-num">最新价</th><th class="col-num">浮动盈亏</th></tr></thead>'
      + '<tbody>';
    if (!items.length) {
      html += '</tbody></table><div class="auto-empty">暂无模拟持仓（策略信号过风控后自动建仓）</div></div>';
    } else {
      html += items.map(function (p) {
        return '<tr><td>' + esc(p.name) + '<span class="auto-code">' + esc(p.code) + '</span></td>'
          + '<td class="col-num">' + fmtNum(p.qty, 0) + '</td>'
          + '<td class="col-num">' + fmtNum(p.avg_cost, 3) + '</td>'
          + '<td class="col-num">' + fmtNum(p.last_price) + '</td>'
          + '<td class="col-num txt-' + pnlClass(p.pnl_pct) + '">' + fmtPct(p.pnl_pct) + '</td></tr>';
      }).join('') + '</tbody></table></div>';
    }
    html += '<div class="auto-actions" style="margin-top:10px"><button class="auto-btn auto-btn-ghost" id="auto-pos-refresh">刷新</button></div>';
    host.innerHTML = html;
    $('auto-pos-refresh').addEventListener('click', loadPositions);
  }

  function summaryCard(label, value) {
    return '<div class="auto-sum-card"><span>' + esc(label) + '</span><strong>' + esc(value) + '</strong></div>';
  }

  // ------------------------------------------------------------
  // 复盘 Tab（北极星指标仪表盘）— v2 新增
  // 数据：/api/review/summary + /api/review/equity + /api/review/weekly
  // 目标口径：90 天纸面收益跑赢沪深300 ≥+3pct，每笔交易 100% 可回溯
  // ------------------------------------------------------------
  var RV = {
    summary: null, equity: null, weekly: null, evo: null,
    tradeReviews: [], weeklyList: [], days: 90, error: '', loading: false
  };

  function apiReview(path) {
    return fetch('/api/review' + path).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (j) {
        return { ok: !!(j && j.ok), data: j && j.data, message: (j && j.message) || ('HTTP ' + r.status) };
      });
    }).catch(function (e) { return { ok: false, data: null, message: '网络错误：' + e.message }; });
  }

  // 闭环进化 API（/api/evolution/*）— v3 新增
  function apiEvo(path, method) {
    return fetch('/api/evolution' + path, { method: method || 'GET' }).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (j) {
        return { ok: !!(j && j.ok), data: j && j.data, message: j && j.message };
      });
    }).catch(function () { return { ok: false, data: null }; });
  }

  function loadReview() {
    if (RV.loading) return;
    RV.loading = true;
    var host = $('auto-tab-review');
    if (host && !RV.summary) {
      host.innerHTML = '<div class="auto-empty">复盘数据加载中…</div>';
    }
    var days = RV.days || 90;
    Promise.all([
      apiReview('/summary?days=' + days),
      apiReview('/equity?days=' + days),
      apiReview('/weekly'),
      apiEvo('/status'),
      apiEvo('/iterations?limit=15'),
      apiEvo('/trade-reviews?limit=15'),
      apiEvo('/library'),
      apiEvo('/proposals?limit=15'),
      apiReview('/trade-reviews?limit=200'),
      apiReview('/weekly-list?limit=12')
    ]).then(function (rs) {
      RV.loading = false;
      RV.error = rs[0].ok ? '' : (rs[0].message || '复盘接口不可用');
      if (rs[0].ok) RV.summary = rs[0].data;
      if (rs[1].ok) RV.equity = rs[1].data;
      if (rs[2].ok) RV.weekly = rs[2].data;
      RV.tradeReviews = rs[8].ok ? ((rs[8].data && rs[8].data.items) || []) : [];
      RV.weeklyList = rs[9].ok ? ((rs[9].data && rs[9].data.items) || []) : [];
      RV.evo = {
        status: rs[3].ok ? rs[3].data : null,
        iterations: rs[4].ok ? (rs[4].data.items || []) : [],
        reviews: rs[5].ok ? (rs[5].data.items || []) : [],
        library: rs[6].ok ? (rs[6].data.items || []) : [],
        proposals: rs[7].ok ? (rs[7].data.items || []) : []
      };
      renderReviewTab();
    }, function (e) {
      RV.loading = false;
      RV.error = '复盘数据加载异常：' + (e && e.message ? e.message : e);
      renderReviewTab();
    });
  }

  function rvStatusBadge(st) {
    if (st === 'pass') return '<span class="rv-badge rv-pass">达标</span>';
    if (st === 'fail') return '<span class="rv-badge rv-fail">未达标</span>';
    return '<span class="rv-badge rv-na">数据累积中</span>';
  }

  function rvFmt(v, unit) {
    if (v === null || v === undefined) return '--';
    if (unit === 'pct' || unit === '%') return (v > 0 ? '+' : '') + v + (unit === 'pct' ? 'pct' : '%');
    return String(v) + (unit || '');
  }

  function renderReviewTab() {
    var host = $('auto-tab-review');
    if (!host) return;
    var s = RV.summary;
    if (!s) {
      host.innerHTML = '<div class="auto-card"><div class="auto-empty txt-red">'
        + esc(RV.error || '复盘数据不可用，请稍后刷新')
        + '</div><div class="auto-actions"><button class="auto-btn" id="auto-rv-retry">重试</button></div></div>';
      var rb = $('auto-rv-retry');
      if (rb) rb.addEventListener('click', function () { RV.error = ''; loadReview(); });
      return;
    }

    var days = s.window_days || RV.days || 90;
    var eq = s.equity || {};
    var bm = s.benchmark || {};
    var q = s.quality || {};
    var pr = s.process || {};
    var rk = s.risk || {};

    // 0. 头部：数据截止时间 + 统计窗口切换（可配，不再写死 90 天）
    var headerHtml = '<div class="auto-card"><div class="auto-card-title">复盘控制台</div>'
      + '<div class="auto-filter-row">'
      + '<span class="auto-sub">数据截止 ' + esc(s.as_of || '--')
      + ' ｜ 统计窗口 ' + days + ' 天 ｜ 净值点 ' + (rk.curve_points == null ? '--' : rk.curve_points) + ' 个</span>'
      + '<span style="flex:1"></span>'
      + [7, 30, 90, 180, 365].map(function (d) {
        return '<button class="auto-btn ' + (Number(days) === d ? '' : 'auto-btn-ghost') + '" data-rv-days="' + d + '">' + d + '天</button>';
      }).join('')
      + '</div>'
      + (q.include_drill ? '' : '<div class="auto-sub">口径：排除演练单 ' + (q.drill_trades_excluded || 0) + ' 笔；盈亏为净口径（已扣佣金/印花税/过户费/滑点）。</div>')
      + '</div>';

    // 1. 北极星目标卡
    var targetsHtml = (s.targets || []).map(function (t) {
      return '<div class="rv-target">'
        + '<div class="rv-target-head"><span class="rv-target-label">' + esc(t.label) + '</span>' + rvStatusBadge(t.status) + '</div>'
        + '<div class="rv-target-value">' + esc(rvFmt(t.value, t.unit))
        + '<small>目标 ' + esc(t.target) + '</small></div>'
        + '</div>';
    }).join('');

    // 2. 权益概览（6 卡）
    var sumHtml = '<div class="auto-pos-summary">'
      + summaryCard('当前权益', eq.now == null ? '--' : ('¥' + fmtNum(eq.now, 0)))
      + summaryCard('总收益', eq.total_return_pct == null ? '--' : fmtPct(eq.total_return_pct))
      + summaryCard('超额收益(' + days + '天)', s.excess_return_pct == null ? '--' : fmtPct(s.excess_return_pct))
      + summaryCard('最大回撤', rk.maxdd_pct == null ? '--' : (rk.maxdd_pct + '%'))
      + summaryCard('基准(' + (bm.name || '沪深300') + ')' + days + '天', bm.return_pct == null ? '--' : fmtPct(bm.return_pct))
      + summaryCard('闭合交易', q.trades == null ? '--' : (q.trades + ' 笔'))
      + '</div>';

    // 3. 绩效指标（胜率 / 盈亏比 / 夏普 / 年化波动 / 成本 / 出场）
    //    红线：null 一律显示 --，绝不渲染为 0（全赢时 PF 亦为 null，非 0）
    var sharpeTip = rk.sharpe_reason ? ' title="' + esc(rk.sharpe_reason) + '"' : '';
    var pfTip = (q.profit_factor == null && q.trades > 0)
      ? ' title="无亏损交易（gross_loss=0），盈亏比不适用"' : '';
    var perfHtml = '<div class="auto-card"><div class="auto-card-title">绩效指标（净口径）</div>'
      + '<div class="auto-pos-summary">'
      + summaryCard('胜率', q.win_rate_pct == null ? '--' : (q.win_rate_pct + '%'))
      + summaryCard('盈亏比 PF', q.profit_factor == null ? '--' : q.profit_factor)
      + summaryCard('夏普比率', rk.sharpe == null ? '--' : rk.sharpe)
      + summaryCard('年化波动', rk.annual_vol_pct == null ? '--' : (rk.annual_vol_pct + '%'))
      + summaryCard('交易成本合计', q.fees_total == null ? '--' : ('¥' + fmtNum(q.fees_total, 0)))
      + summaryCard('交易可回溯率', pr.traceability_pct == null ? '--' : (pr.traceability_pct + '%'))
      + '</div>'
      + '<div class="auto-sub">毛盈亏 ' + (q.pnl_gross_total == null ? '--' : ('¥' + fmtNum(q.pnl_gross_total, 0)))
      + ' ｜ 净盈亏 ' + (q.pnl_net_total == null ? '--' : ('¥' + fmtNum(q.pnl_net_total, 0)))
      + ' ｜ 样本' + (q.stats_ready ? '已达标（≥5 笔，指标可判读）' : '不足 5 笔，胜率/盈亏比仅供参考')
      + (rk.sharpe_reason ? ' ｜ 夏普：' + esc(rk.sharpe_reason) : '')
      + (q.profit_factor == null && q.trades > 0 && !rk.sharpe_reason ? ' ｜ 盈亏比：无亏损样本，不适用' : '')
      + '</div></div>';

    // 4. 出场原因分布（归因：赚在哪、亏在哪）
    var erKeys = Object.keys(q.exit_reasons || {});
    var erTotal = erKeys.reduce(function (a, k) { return a + (q.exit_reasons[k] || 0); }, 0);
    var EXIT_LABELS = {
      take_profit: '固定止盈', trailing_stop: '移动止盈', fixed_stop: '固定止损',
      signal: '策略信号出场', drill_forced: '演练强制平仓', manual: '手动卖出',
      conditional: '条件单触发', unlabeled: '未标注'
    };
    var erHtml = '<div class="auto-card"><div class="auto-card-title">出场归因分布</div>';
    if (!erKeys.length) {
      erHtml += '<div class="auto-empty">暂无闭合交易，出场归因待积累</div>';
    } else {
      erHtml += '<div class="rv-week-grid">' + erKeys.sort(function (a, b) {
        return q.exit_reasons[b] - q.exit_reasons[a];
      }).map(function (k) {
        var n = q.exit_reasons[k] || 0;
        var pct = erTotal ? Math.round(n / erTotal * 100) : 0;
        return summaryCard(EXIT_LABELS[k] || k, n + ' 笔 · ' + pct + '%');
      }).join('') + '</div>';
    }
    erHtml += '</div>';

    // 5. 净值对比曲线
    var chartHtml = '<div class="auto-card"><div class="auto-card-title">净值曲线 vs ' + esc(bm.name || '基准') + '（' + days + ' 天归一化）</div>'
      + '<canvas id="rv-chart" class="rv-chart" height="180"></canvas>'
      + '<div class="rv-legend"><span class="rv-lg rv-lg-eq">— 纸面净值</span><span class="rv-lg rv-lg-bm">— ' + esc(bm.name || '沪深300') + '</span></div></div>';

    // 6. 交易明细回放（FIFO 配对，含 MFE/MAE 与归因标签）——用户诉求「交易明细可回放」
    var trs = RV.tradeReviews || [];
    var trRows = trs.length ? trs.map(function (r) {
      var win = Number(r.pnl) >= 0;
      var tags = (r.tags || []).map(function (t) { return '<span class="rv-tag">' + esc(t) + '</span>'; }).join('');
      var mfe = r.mfe_pct == null ? '--' : fmtPct(r.mfe_pct);
      var mae = r.mae_pct == null ? '--' : fmtPct(r.mae_pct);
      var eff = r.exit_efficiency == null ? '--' : (Math.round(r.exit_efficiency * 100) + '%');
      return '<tr title="' + esc(r.conclusion || '') + '">'
        + '<td>' + esc((r.buy_ts || '').slice(5, 16)) + '</td>'
        + '<td>' + esc((r.sell_ts || '').slice(5, 16)) + '</td>'
        + '<td>' + esc(r.name || '') + '<span class="auto-code">' + esc(r.code) + '</span></td>'
        + '<td class="col-num">' + (r.holding_days == null ? '--' : r.holding_days + '天') + '</td>'
        + '<td class="col-num">' + fmtNum(r.buy_price, 3) + ' → ' + fmtNum(r.sell_price, 3) + '</td>'
        + '<td class="col-num txt-' + (win ? 'red' : 'green') + '">' + fmtPct(r.pnl_pct) + '</td>'
        + '<td class="col-num txt-' + (win ? 'red' : 'green') + '">' + (r.pnl == null ? '--' : ('¥' + fmtNum(r.pnl, 0))) + '</td>'
        + '<td class="col-num">' + mfe + ' / ' + mae + '</td>'
        + '<td class="col-num">' + eff + '</td>'
        + '<td>' + (tags || '<span class="auto-sub">--</span>') + '</td>'
        + '</tr>';
    }).join('') : '';
    var replayHtml = '<div class="auto-card"><div class="auto-card-title">交易明细回放（含 MFE/MAE 与归因）</div>'
      + (trs.length
        ? '<div class="auto-table-scroll" style="max-height:340px"><div class="data-table-wrap"><table class="data-table">'
          + '<thead><tr><th>买入</th><th>卖出</th><th>标的</th><th class="col-num">持股</th><th class="col-num">价格</th>'
          + '<th class="col-num">收益率</th><th class="col-num">净盈亏</th><th class="col-num">MFE/MAE</th>'
          + '<th class="col-num">出场效率</th><th>归因标签</th></tr></thead><tbody>'
          + trRows + '</tbody></table></div></div>'
          + '<div class="auto-sub">MFE=持仓期最大浮盈，MAE=最大浮亏，出场效率=实际收益/最大浮盈（越接近 100% 说明卖点越好）。</div>'
        : '<div class="auto-empty">暂无闭合交易复盘——首笔模拟卖出后自动归因</div>')
      + '</div>';

    // 7. 本周复盘 + 历史周报
    var w = RV.weekly || {};
    var weekHtml = '<div class="auto-card"><div class="auto-card-title">本周复盘（' + esc(w.week || '--') + '）</div>'
      + '<div class="rv-week-grid">'
      + summaryCard('闭合交易', w.trades == null ? '--' : (w.trades + ' 笔'))
      + summaryCard('周盈亏', w.pnl == null ? '--' : ('¥' + fmtNum(w.pnl, 0)))
      + summaryCard('周胜率', w.win_rate_pct === null || w.win_rate_pct === undefined ? '--' : w.win_rate_pct + '%')
      + summaryCard('权益周变化', w.equity_change_pct == null ? '--' : fmtPct(w.equity_change_pct))
      + summaryCard('基准周变化', w.benchmark_change_pct == null ? '--' : fmtPct(w.benchmark_change_pct))
      + '</div>'
      + '<div class="rv-narrative">' + esc(w.narrative || '周报生成中…') + '</div>'
      + (RV.weeklyList.length > 1
        ? '<div class="auto-sub" style="margin-top:8px">历史周报：' + RV.weeklyList.map(function (r) {
          return esc(r.week || '') + '（' + (r.trades || 0) + ' 笔 / ' + (r.pnl == null ? '--' : '¥' + fmtNum(r.pnl, 0)) + '）';
        }).join('　｜　') + '</div>'
        : '')
      + '</div>';

    host.innerHTML =
      headerHtml
      + '<div class="auto-card rv-northstar"><div class="auto-card-title">🎯 北极星目标：' + days + ' 天纸面收益持续跑赢沪深300，且每笔交易 100% 可回溯</div>'
      + '<div class="rv-targets">' + targetsHtml + '</div>'
      + '<div class="rv-note">' + esc(s.note || '') + '</div></div>'
      + sumHtml + perfHtml + erHtml + chartHtml + replayHtml + weekHtml
      + renderEvolutionBoard()
      + '<div class="auto-actions" style="margin-top:10px">'
      + '<button class="auto-btn auto-btn-ghost" id="auto-rv-refresh">刷新复盘</button>'
      + '<button class="auto-btn auto-btn-ghost" id="auto-rv-run">立即跑一轮闭环</button>'
      + '</div>';

    var dayBtns = host.querySelectorAll('[data-rv-days]');
    for (var di = 0; di < dayBtns.length; di++) {
      dayBtns[di].addEventListener('click', function () {
        RV.days = Number(this.getAttribute('data-rv-days')) || 90;
        RV.summary = null;
        loadReview();
      });
    }
    $('auto-rv-refresh').addEventListener('click', function () {
      RV.summary = null; loadReview();
    });
    $('auto-rv-run').addEventListener('click', function () {
      var btn = this;
      btn.disabled = true; btn.textContent = '闭环运行中…';
      apiEvo('/run?full=true', 'POST').then(function (r) {
        btn.disabled = false; btn.textContent = '立即跑一轮闭环';
        if (r.ok) { RV.summary = null; loadReview(); }
        else { toast((r && r.message) || '闭环运行失败', true); }
      });
    });
    bindProposalButtons();
    drawReviewChart();
  }

  // ------------------------------------------------------------
  // 闭环看板（v3 新增）：执行→复盘→积累→优化 四环可视化
  // ------------------------------------------------------------
  var EVO_ACTION_META = {
    promote: ['晋级', 'rv-pass'], demote: ['降级', 'rv-fail'],
    retire: ['退役', 'rv-fail'], propose: ['调参提案', 'rv-na'],
    apply: ['应用提案', 'rv-pass'], reject: ['拒绝提案', 'rv-na']
  };

  function evoActionBadge(action) {
    var m = EVO_ACTION_META[action] || [action, 'rv-na'];
    return '<span class="rv-badge ' + m[1] + '">' + esc(m[0]) + '</span>';
  }

  function renderEvolutionBoard() {
    var evo = RV.evo || {};
    var st = evo.status || {};
    var loop = st.loop || {};
    var counts = st.counts || {};
    var lastResult = loop.last_result || {};

    // A. 闭环状态卡
    var statusHtml = '<div class="auto-card rv-loop"><div class="auto-card-title">🔄 自动化闭环（执行 → 复盘 → 积累 → 优化，无人值守）</div>'
      + '<div class="rv-week-grid">'
      + summaryCard('最近日结', loop.last_run_at || '尚未运行')
      + summaryCard('累计日结', String(loop.total_runs || 0) + ' 轮')
      + summaryCard('单笔复盘', String(counts.trade_reviews || 0) + ' 条')
      + summaryCard('策略库已验证', String(counts.validated_strategies || 0) + ' 个')
      + '</div>'
      + (lastResult.actions && lastResult.actions.length
        ? '<div class="rv-note">最近一轮动作：' + esc(lastResult.actions.join('、')) + '</div>'
        : '<div class="rv-note">闭环每日收盘后自动运行；指标未触发动作时保持观察，不盲目调参。</div>')
      + '</div>';

    // B. 迭代记录卡
    var items = evo.iterations || [];
    var iterRows = items.length ? items.map(function (it) {
      var m = it.metrics || {};
      return '<div class="rv-evo-row">'
        + '<div class="rv-evo-head">' + evoActionBadge(it.action)
        + '<strong>' + esc(it.strategy_id || '') + '</strong>'
        + '<span class="rv-evo-ts">' + esc(it.ts || '') + '</span></div>'
        + '<div class="rv-evo-detail">' + esc(it.detail || '') + '</div>'
        + (m.trades !== undefined && m.trades !== null
          ? '<div class="rv-evo-metrics">样本 ' + m.trades + ' 笔 · 胜率 ' + (m.win_rate_pct === null ? '--' : m.win_rate_pct + '%')
            + ' · PF ' + (m.profit_factor === null ? '--' : m.profit_factor)
            + ' · 回撤 ' + (m.maxdd_pct === null ? '--' : m.maxdd_pct + '%') + '</div>'
          : '')
        + '</div>';
    }).join('') : '<div class="auto-empty">暂无迭代动作——策略指标未触发晋级/降级/调参条件时，闭环保持观察。</div>';
    var iterHtml = '<div class="auto-card"><div class="auto-card-title">⚙️ 迭代记录（动态优化）</div>' + iterRows + '</div>';

    // C. 单笔交易复盘卡
    var reviews = evo.reviews || [];
    var reviewRows = reviews.length ? reviews.map(function (r) {
      var win = r.verdict === 'win';
      var tags = (r.tags || []).map(function (t) { return '<span class="rv-tag">' + esc(t) + '</span>'; }).join('');
      return '<div class="rv-evo-row">'
        + '<div class="rv-evo-head"><span class="rv-badge ' + (win ? 'rv-win' : 'rv-loss') + '">' + (win ? '盈利' : '亏损') + '</span>'
        + '<strong>' + esc(r.name || r.code) + '</strong>'
        + '<span class="rv-evo-ts">' + esc(fmtPct(r.pnl_pct)) + ' · ¥' + fmtNum(r.pnl, 0) + '</span></div>'
        + '<div class="rv-evo-detail">' + esc(r.conclusion || '') + '</div>'
        + (tags ? '<div class="rv-evo-metrics">' + tags + '</div>' : '')
        + '</div>';
    }).join('') : '<div class="auto-empty">暂无闭合交易复盘——首笔模拟卖出后，系统自动归因成败原因。</div>';
    var reviewHtml = '<div class="auto-card"><div class="auto-card-title">🔍 单笔交易复盘（自动归因）</div>' + reviewRows + '</div>';

    // D. 策略库卡（策略积累）
    var lib = evo.library || [];
    var libRows = lib.length ? lib.map(function (e) {
      var latest = (e.versions || [])[(e.versions || []).length - 1] || {};
      var pm = latest.paper_metrics || {};
      return '<div class="rv-evo-row">'
        + '<div class="rv-evo-head"><span class="rv-badge ' + (e.status === 'validated' ? 'rv-pass' : 'rv-na') + '">'
        + (e.status === 'validated' ? '已验证' : '观察中') + '</span>'
        + '<strong>' + esc(e.name || e.strategy_id) + '</strong>'
        + '<span class="rv-evo-ts">版本谱系 v' + (e.lineage || 0) + '</span></div>'
        + (latest.params
          ? '<div class="rv-evo-detail">沉淀参数：' + esc(JSON.stringify(latest.params))
            + (pm.win_rate_pct !== undefined && pm.win_rate_pct !== null
              ? ' · 验证期胜率 ' + pm.win_rate_pct + '% · PF ' + pm.profit_factor : '') + '</div>'
          : '<div class="rv-evo-detail">等待首次模拟验证通过后沉淀参数快照。</div>')
        + '</div>';
    }).join('') : '<div class="auto-empty">策略库待沉淀——策略通过模拟验证（paper_passed）时自动入库。</div>';
    var libHtml = '<div class="auto-card"><div class="auto-card-title">📚 策略库（有效策略沉淀）</div>' + libRows + '</div>';

    // E. 调参提案卡（pending 可人工干预）
    var proposals = evo.proposals || [];
    var STATUS_LABEL = { pending: '待验证', applied: '已应用', rejected_auto: '验证未过', rejected_manual: '人工拒绝', awaiting_codes: '等待标的', expired: '已过期' };
    var propRows = proposals.length ? proposals.map(function (p) {
      var stLabel = STATUS_LABEL[p.status] || p.status;
      var stCls = p.status === 'applied' ? 'rv-pass' : (p.status === 'pending' ? 'rv-na' : 'rv-fail');
      var btns = p.status === 'pending'
        ? '<div class="rv-evo-btns"><button class="auto-btn rv-btn-mini" data-prop-apply="' + esc(p.id) + '">立即应用</button>'
          + '<button class="auto-btn auto-btn-ghost rv-btn-mini" data-prop-reject="' + esc(p.id) + '">否决</button></div>'
        : '';
      return '<div class="rv-evo-row">'
        + '<div class="rv-evo-head"><span class="rv-badge ' + stCls + '">' + esc(stLabel) + '</span>'
        + '<strong>' + esc(p.strategy_id) + '</strong>'
        + '<span class="rv-evo-ts">' + esc(p.param) + '：' + esc(String(p.old_value)) + ' → ' + esc(String(p.new_value)) + '</span></div>'
        + '<div class="rv-evo-detail">' + esc(p.reason || '') + '</div>' + btns
        + '</div>';
    }).join('') : '<div class="auto-empty">暂无调参提案——策略指标"差一点达标"时，闭环自动生成单变量微调提案并双轨验证。</div>';
    var propHtml = '<div class="auto-card"><div class="auto-card-title">🧪 调参提案（单变量 ±20%，双轨验证）</div>' + propRows + '</div>';

    return statusHtml + iterHtml + reviewHtml + libHtml + propHtml;
  }

  function bindProposalButtons() {
    var host = $('auto-tab-review');
    if (!host) return;
    host.querySelectorAll('[data-prop-apply]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        btn.disabled = true;
        apiEvo('/proposals/' + btn.getAttribute('data-prop-apply') + '/apply', 'POST').then(function (r) {
          if (!r.ok) toast((r && r.message) || '应用失败', true);
          RV.summary = null; loadReview();
        });
      });
    });
    host.querySelectorAll('[data-prop-reject]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        btn.disabled = true;
        apiEvo('/proposals/' + btn.getAttribute('data-prop-reject') + '/reject', 'POST').then(function () {
          RV.summary = null; loadReview();
        });
      });
    });
  }

  function drawReviewChart() {
    var cv = $('rv-chart');
    var d = RV.equity;
    if (!cv || !d) return;
    var eqV = (d.equity && d.equity.values) || [];
    var bmV = (d.benchmark && d.benchmark.values) || [];
    if (!eqV.length && !bmV.length) return;

    var dpr = window.devicePixelRatio || 1;
    var W = cv.clientWidth || cv.parentElement.clientWidth || 600;
    var H = 180;
    cv.width = W * dpr; cv.height = H * dpr;
    cv.style.height = H + 'px';
    var ctx = cv.getContext('2d');
    ctx.scale(dpr, dpr);

    var padL = 42, padR = 12, padT = 12, padB = 22;
    var all = eqV.concat(bmV);
    var lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
    if (hi - lo < 0.001) { hi += 0.005; lo -= 0.005; }
    var span = hi - lo;
    lo -= span * 0.06; hi += span * 0.06;

    function x(i, n) { return padL + (W - padL - padR) * (n <= 1 ? 0.5 : i / (n - 1)); }
    function y(v) { return padT + (H - padT - padB) * (1 - (v - lo) / (hi - lo)); }

    var styles = getComputedStyle(document.body);
    var colGrid = 'rgba(148,163,184,0.15)';
    var colText = styles.getPropertyValue('--text2') || '#8b93a7';
    var colEq = '#22d3ee';
    var colBm = '#f59e0b';

    // 网格 + 纵轴刻度
    ctx.strokeStyle = colGrid; ctx.fillStyle = colText; ctx.font = '10px monospace'; ctx.lineWidth = 1;
    for (var g = 0; g <= 4; g++) {
      var gv = lo + (hi - lo) * g / 4;
      ctx.beginPath(); ctx.moveTo(padL, y(gv)); ctx.lineTo(W - padR, y(gv)); ctx.stroke();
      ctx.fillText(((gv - 1) * 100).toFixed(1) + '%', 4, y(gv) + 3);
    }
    // 基准线 1.0
    ctx.setLineDash([3, 4]);
    ctx.beginPath(); ctx.moveTo(padL, y(1)); ctx.lineTo(W - padR, y(1)); ctx.stroke();
    ctx.setLineDash([]);

    function line(vals, color) {
      if (vals.length < 2) return;
      ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.beginPath();
      for (var i = 0; i < vals.length; i++) {
        var px = x(i, vals.length), py = y(vals[i]);
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.stroke();
    }
    line(bmV, colBm);
    line(eqV, colEq);

    // 端点日期
    var bDates = (d.benchmark && d.benchmark.dates) || [];
    ctx.fillStyle = colText;
    if (bDates.length) {
      ctx.fillText(bDates[0].slice(5), padL, H - 6);
      var last = bDates[bDates.length - 1].slice(5);
      ctx.fillText(last, W - padR - ctx.measureText(last).width, H - 6);
    }
  }

  window.addEventListener('resize', function () {
    if (S.visible && S.tab === 'review') drawReviewChart();
  });

  // ------------------------------------------------------------
  // 留痕 Tab（操作全程可追溯：决策闸判定 / 下单 / 拒单 / 撤单 / 配置变更）
  // 数据：GET /api/automation/audit?action&result&limit&offset
  // ------------------------------------------------------------
  var AUDIT_FILTER_ACTIONS = [
    'decision_allow', 'decision_deny', 'order_filled', 'order_rejected',
    'order_failed', 'order_manual_submit', 'order_canceled',
    'conditional_created', 'conditional_triggered', 'conditional_expired',
    'config_updated'
  ];

  function loadAudit(reset) {
    var st = S.audit;
    if (st.loading) return Promise.resolve();
    if (reset) { st.items = []; st.total = 0; }
    st.loading = true;
    var host = $('auto-tab-audit');
    if (host && reset) host.innerHTML = '<div class="auto-empty">留痕加载中…</div>';
    var q = '?limit=100&offset=' + st.items.length;
    if (st.action) q += '&action=' + encodeURIComponent(st.action);
    if (st.result) q += '&result=' + encodeURIComponent(st.result);
    return api('/audit' + q).then(function (r) {
      st.loading = false;
      if (!r.ok || !r.data) {
        if (host) {
          host.innerHTML = '<div class="auto-card"><div class="auto-empty txt-red">留痕加载失败：' + esc(r.message || '接口不可用')
            + '</div><div class="auto-actions"><button class="auto-btn" id="auto-audit-retry">重试</button></div></div>';
          var rb = $('auto-audit-retry');
          if (rb) rb.addEventListener('click', function () { loadAudit(true); });
        }
        return;
      }
      st.total = r.data.total || 0;
      st.items = st.items.concat(r.data.items || []);
      renderAudit();
    }, function (e) {
      st.loading = false;
      if (host) host.innerHTML = '<div class="auto-empty txt-red">留痕加载异常：' + esc(e && e.message || e) + '</div>';
    });
  }

  function auditPayloadHtml(p) {
    if (!p || typeof p !== 'object') return '<span class="auto-sub">--</span>';
    var keys = Object.keys(p);
    if (!keys.length) return '<span class="auto-sub">--</span>';
    var parts = [];
    keys.slice(0, 8).forEach(function (k) {
      var v = p[k];
      if (v === null || v === undefined) return;
      if (typeof v === 'object') {
        if (k === 'reasons') {
          parts.push('四维：' + Object.keys(v).map(function (dk) {
            var sv = v[dk] && v[dk].score;
            return dk + (sv == null ? '=--' : '=' + sv);
          }).join(' / '));
        } else {
          parts.push(k + '=' + JSON.stringify(v).slice(0, 120));
        }
      } else {
        parts.push(k + '=' + String(v).slice(0, 160));
      }
    });
    return '<span class="auto-sub">' + esc(parts.join(' ｜ ')) + '</span>';
  }

  function renderAudit() {
    var host = $('auto-tab-audit');
    if (!host) return;
    var st = S.audit;
    var html = '<div class="auto-card"><div class="auto-card-title">操作留痕（决策与交易全程可追溯）</div>'
      + '<div class="auto-filter-row">'
      + '<select id="auto-audit-action"><option value="">全部动作</option>'
      + AUDIT_FILTER_ACTIONS.map(function (a) {
        return '<option value="' + a + '"' + (st.action === a ? ' selected' : '') + '>'
          + esc(AUDIT_ACTION_LABELS[a] || a) + '</option>';
      }).join('') + '</select>'
      + '<select id="auto-audit-result"><option value="">全部结果</option>'
      + ['ok', 'denied', 'error'].map(function (x) {
        return '<option value="' + x + '"' + (st.result === x ? ' selected' : '') + '>' + x + '</option>';
      }).join('') + '</select>'
      + '<span class="auto-sub">共 ' + st.total + ' 条</span>'
      + '<span style="flex:1"></span>'
      + '<button class="auto-btn auto-btn-ghost" id="auto-audit-refresh">刷新</button>'
      + '</div>';
    html += '<div class="auto-table-scroll"><div class="data-table-wrap"><table class="data-table">'
      + '<thead><tr><th>时间</th><th>主体</th><th>动作</th><th>结果</th><th>详情</th></tr></thead><tbody>';
    if (!st.items.length) {
      html += '</tbody></table><div class="auto-empty">暂无留痕记录（决策闸判定与订单动作会自动记入）</div></div></div>';
    } else {
      html += st.items.map(function (it) {
        var resCls = it.result === 'ok' ? 'badge-green' : (it.result === 'denied' ? 'badge-gold' : 'badge-red');
        return '<tr>'
          + '<td>' + esc((it.ts || '').replace('T', ' ')) + '</td>'
          + '<td>' + esc(it.actor || '') + '</td>'
          + '<td>' + esc(AUDIT_ACTION_LABELS[it.action] || it.action)
          + '<span class="auto-code">' + esc(it.action) + '</span></td>'
          + '<td><span class="auto-badge ' + resCls + '">' + esc(it.result || '--') + '</span></td>'
          + '<td>' + auditPayloadHtml(it.payload) + '</td>'
          + '</tr>';
      }).join('');
      html += '</tbody></table></div></div>';
      if (st.items.length < st.total) {
        html += '<div class="auto-actions"><button class="auto-btn auto-btn-ghost" id="auto-audit-more">加载更多（已 '
          + st.items.length + '/' + st.total + '）</button></div>';
      }
    }
    html += '</div>';
    host.innerHTML = html;

    $('auto-audit-action').addEventListener('change', function () {
      S.audit.action = this.value; loadAudit(true);
    });
    $('auto-audit-result').addEventListener('change', function () {
      S.audit.result = this.value; loadAudit(true);
    });
    $('auto-audit-refresh').addEventListener('click', function () { loadAudit(true); });
    var more = $('auto-audit-more');
    if (more) more.addEventListener('click', function () { loadAudit(false); });
  }

  // ------------------------------------------------------------
  // 实时推送（SSE）：信号/订单/持仓变动实时刷新，失败降级为轮询
  // ------------------------------------------------------------
  var _streamRefreshTimer = null;
  var _streamPendingTab = '';

  function ensureStreamNote() {
    var host = $('automation-panel');
    if (!host) return null;
    var note = $('auto-stream-note');
    if (!note) {
      note = document.createElement('div');
      note.id = 'auto-stream-note';
      note.style.cssText = 'position:absolute;left:12px;bottom:12px;padding:6px 12px;border-radius:8px;' +
        'font-size:12px;z-index:20;background:rgba(245,158,11,0.15);' +
        'border:1px solid rgba(245,158,11,0.4);color:#f59e0b;display:none';
      host.appendChild(note);
    }
    return note;
  }

  function setStreamNote(text) {
    var note = ensureStreamNote();
    if (!note) return;
    if (!text) { note.style.display = 'none'; return; }
    note.textContent = text;
    note.style.display = '';
  }

  function scheduleAutoRefresh(tab) {
    _streamPendingTab = tab;
    if (_streamRefreshTimer) return;
    _streamRefreshTimer = setTimeout(function () {
      _streamRefreshTimer = null;
      var t = _streamPendingTab;
      _streamPendingTab = '';
      if (!S.visible) return;
      if (t === 'signals' && S.tab === 'signals') loadSignals(true);
      else if (t === 'orders' && S.tab === 'orders') loadOrders(true);
      else if (t === 'positions' && S.tab === 'positions') loadPositions();
    }, 800);
  }

  function initAutoStream() {
    if (typeof window.DashboardStream === 'undefined') {
      setStreamNote('推送组件未加载：当前仅手动刷新与 30s 状态轮询');
      startFallbackPolling();
      return;
    }
    S.stream = window.DashboardStream.create({
      url: '/api/stream/signals',
      onOpen: function () {
        S.streamDegraded = false;
        stopFallbackPolling();
        setStreamNote('');
      },
      onEvent: function (name, payload) {
        // 数据降级判据：每个事件信封都带 degraded/reason（真实降级走这里，
        // 而非 signals.unavailable —— 后者仅在交易存储未就绪时才会发出）
        if (payload && payload.degraded) {
          setStreamNote('数据降级：' + (payload.reason || '上游数据不完整'));
        } else if (S.streamDegraded !== true) {
          setStreamNote('');
        }
        if (name === 'connected') {
          S.streamSid = (payload && payload.data && payload.data.sid) || null;
          return;
        }
        if (name === 'signal.new') scheduleAutoRefresh('signals');
        else if (name === 'order.new') scheduleAutoRefresh('orders');
        else if (name === 'signals.snapshot') scheduleAutoRefresh(S.tab);
        else if (name === 'signals.unavailable') {
          setStreamNote('交易存储未就绪：' + (payload && payload.reason ? payload.reason : '--'));
        }
      },
      onDegrade: function (reason) {
        S.streamDegraded = true;
        setStreamNote('推送已断开（' + reason + '）：已回落 30s 轮询，列表仍可手动刷新');
        startFallbackPolling();
      }
    });
  }

  // 降级兜底：SSE 不可用时按 30s 轮询当前列表页（页面隐藏时自动停）
  var _fallbackTimer = null;
  function startFallbackPolling() {
    if (_fallbackTimer) return;
    _fallbackTimer = setInterval(function () {
      if (document.hidden || !S.visible) return;
      if (S.tab === 'signals') loadSignals(true);
      else if (S.tab === 'orders') loadOrders(true);
      else if (S.tab === 'positions') loadPositions();
      else if (S.tab === 'audit') loadAudit(true);
    }, 30000);
  }
  function stopFallbackPolling() {
    if (_fallbackTimer) { clearInterval(_fallbackTimer); _fallbackTimer = null; }
  }

  // 页面隐藏即断开推送（不让后台标签页常驻 SSE），回到前台重建连接
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      if (S.stream && S.stream.stop) { S.stream.stop(); S.stream = null; }
      stopFallbackPolling();
    } else if (!S.stream) {
      initAutoStream();
    }
  });

  // ------------------------------------------------------------
  // 启动：状态点轮询（30s/次，面板关闭期间也运行）
  // ------------------------------------------------------------
  function startPolling() {
    refreshAll();
    initAutoStream();
    if (S.gateTimer) clearInterval(S.gateTimer);
    S.gateTimer = setInterval(function () {
      Promise.all([
        api('/config').then(function (r) { if (r.ok) S.config = r.data; }),
        api('/gate').then(function (r) { if (r.ok) S.gate = r.data; })
      ]).then(function () {
        renderStatusDot();
        // 面板打开时仅定点刷新状态敏感区域，不打断用户输入
        if (S.visible && S.tab === 'config') {
          var g = S.gate || {};
          if (g.breaker && g.breaker.tripped && !document.querySelector('.auto-breaker-banner')) {
            renderConfigTab();
          }
        }
      });
    }, GATE_POLL_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startPolling);
  } else {
    startPolling();
  }
})();
