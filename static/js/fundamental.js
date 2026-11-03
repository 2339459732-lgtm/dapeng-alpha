/**
 * DAPENG Alpha — 基本面 6 卡 + 估值分位条（T09 / P0-1 前端）
 * ================================================================
 * 消费后端（契约见 docs/10-architecture/openbb-dsa-integration-design.md §3.2 / §3.3）：
 *   GET /api/finance?code=             → 6 卡 + 报告期元信息（统一信封）
 *   GET /api/finance/valuation?code=   → 日频估值序列 + 窗口分位
 *   GET /api/meta/capability?keys=     → 能力角标事实源（按 key 的 state）
 *
 * 三条渲染纪律（不可妥协）：
 *  1. **缺数 ≠ 0 ≠ 占位符**。取不到就渲染「不可用 · 具体原因」，绝不渲染 0、
 *     也绝不用占位符糊过去——占位符无法区分「加载中」和「确实没有」。
 *  2. **三种态严格分离**：加载中（loading）/ 缺数（unavailable，带原因）/
 *     请求失败（error，可重试）。三者视觉与文案都不同。
 *  3. **方向性颜色只给方向性指标**：营收同比 / 净利同比 才用涨红跌绿；
 *     PE / PB / ROE / 股息率 是估值与盈利水平，高低不是「涨跌」，一律中性色。
 *
 * 角标状态机（§5.5 四态）：
 *  realtime 绿●实时 / cached 黄◐缓存 / closed 灰○收盘 / stale 橙◑延迟 / unavailable 红✕不可用
 *  财务是季频数据 → 正常态即 `closed`（灰），**不是** realtime。
 */

(function () {
  'use strict';

  // ================================================================
  //  常量
  // ================================================================
  var T_FINANCE = 35000;    // /api/finance 冷启动实测 10–18s，超时给足 35s
  var T_VALUATION = 25000;  // /api/finance/valuation 冷启动实测 9–12s
  var T_CAPABILITY = 8000;
  var TTL_RENDER_CACHE = 60000;   // 同一标的 60s 内重复切 Tab 不重复请求
  var TTL_CAPABILITY = 30000;

  /** 六卡定义（顺序即渲染顺序） */
  var CARD_SPECS = [
    { key: 'pe_ttm',        label: '市盈率 PE_TTM', unit: '倍', digits: 2, directional: false,
      hint: '百度日频序列末值；分位＝当前值在近 5 年序列中的百分位' },
    { key: 'pb',            label: '市净率 PB',     unit: '倍', digits: 2, directional: false,
      hint: '百度日频序列末值；分位＝当前值在近 5 年序列中的百分位' },
    { key: 'roe_ttm',       label: 'ROE (TTM)',     unit: '%',  digits: 2, directional: false,
      hint: 'EPS_TTM（滚动 4 季）÷ 最新每股净资产 × 100' },
    { key: 'revenue_yoy',   label: '营收同比',       unit: '%',  digits: 2, directional: true,
      hint: '财报原值（营业总收入增长率，报告期同比口径）' },
    { key: 'profit_yoy',    label: '净利同比',       unit: '%',  digits: 2, directional: true,
      hint: '财报原值（归属母公司净利润增长率，报告期同比口径）' },
    { key: 'dividend_yield', label: '股息率',        unit: '%',  digits: 2, directional: false,
      hint: 'Σ 近 12 个月已除权现金分红（元/股）÷ 现价 × 100' }
  ];

  /** 角标四态视觉（state → 符号 / class） */
  var BADGE_STATES = {
    realtime:    { mark: '\u25CF', cls: 'fund-badge-realtime', text: '实时' },
    cached:      { mark: '\u25D0', cls: 'fund-badge-cached',   text: '缓存' },
    closed:      { mark: '\u25CB', cls: 'fund-badge-closed',   text: '收盘' },
    stale:       { mark: '\u25D1', cls: 'fund-badge-stale',    text: '延迟' },
    unavailable: { mark: '\u2715', cls: 'fund-badge-unavail',  text: '不可用' }
  };

  // ================================================================
  //  模块内状态
  // ================================================================
  var _renderCache = {};        // code -> { finance:{ts,json}, valuation:{ts,json} }
  var _capabilityCache = null;  // {ts, data}
  // 切标的竞态令牌：**两个区块各自独立**（此前共用一个 _token，导致
  // renderValuationBar 一发起就把 fundamental 的请求判成"过期"而静默丢弃，
  // 6 卡永久停在骨架屏 —— T09 根因）。
  var _tokenFund = 0;
  var _tokenVal = 0;
  // 6 卡加载态计时/看门狗（用户必须能看到"还在跑"或明确的失败+重试）
  var _load = { code: '', startedAt: 0, ticker: 0, watchdog: 0 };
  // 同一标的的在途请求（复用，避免轮询/重复触发反复把 UI 打回骨架屏）
  var _inflightFund = { code: '', promise: null };
  // 最后一次被要求渲染的标的（守卫以此为准：paint 只能写"当前目标"的 DOM）
  var _targetCode = '';

  // ================================================================
  //  小工具
  // ================================================================
  function _esc(text) {
    if (typeof window.escapeHtml === 'function') return window.escapeHtml(text);
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function _isNum(value) {
    return value !== null && value !== undefined && isFinite(Number(value));
  }

  /** 数值格式化：非有限值返回 null（由调用方渲染「不可用」，绝不返回 0） */
  function _fmt(value, digits) {
    if (!_isNum(value)) return null;
    return Number(value).toFixed(digits);
  }

  /** 大额金额（元 → 亿元/万亿元），仅用于口径明细 */
  function _fmtAmount(value) {
    if (!_isNum(value)) return null;
    var n = Number(value);
    var abs = Math.abs(n);
    if (abs >= 1e12) return (n / 1e12).toFixed(2) + ' 万亿';
    if (abs >= 1e8) return (n / 1e8).toFixed(2) + ' 亿';
    if (abs >= 1e4) return (n / 1e4).toFixed(2) + ' 万';
    return n.toFixed(2);
  }

  function _cacheSlot(code) {
    if (!_renderCache[code]) _renderCache[code] = {};
    return _renderCache[code];
  }

  /**
   * 拉取统一信封 —— **必须保留 ok:false 的响应体**。
   * 不用 api.js 的 requestEnvelope：它把 ok:false 折成 null，会丢掉 reason，
   * 而 reason 正是「不可用」态唯一可展示的事实。
   */
  async function _fetchEnvelope(url, timeoutMs) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, timeoutMs);
    try {
      var resp = await fetch(url, { signal: controller.signal, headers: { 'Accept': 'application/json' } });
      if (!resp.ok) {
        return { _transport_error: 'HTTP ' + resp.status + '（' + url + '）' };
      }
      return await resp.json();
    } catch (err) {
      var msg = (err && err.name === 'AbortError')
        ? '请求超时（' + Math.round(timeoutMs / 1000) + 's）'
        : '网络异常：' + ((err && err.message) || String(err));
      return { _transport_error: msg };
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * 能力角标事实源：/api/meta/capability?keys=finance,valuation
   * 只取**每个 key 的 state**，绝不使用信封级 degraded（它长期为 true，
   * 因为 timeshare/flow/news 等域还没人取过数，用它整体染红会误伤本区块）。
   */
  async function _capability() {
    var now = Date.now();
    if (_capabilityCache && (now - _capabilityCache.ts) < TTL_CAPABILITY) {
      return _capabilityCache.data;
    }
    var json = await _fetchEnvelope('/api/meta/capability?keys=finance,valuation', T_CAPABILITY);
    var data = (json && json.ok && json.data) ? json.data : null;
    _capabilityCache = { ts: now, data: data };
    return data;
  }

  /** 角标 HTML */
  function _badge(state, label, reason) {
    var def = BADGE_STATES[state] || BADGE_STATES.closed;
    var text = label || def.text;
    var title = reason ? ('原因：' + reason) : text;
    return '<span class="fund-badge ' + def.cls + '" title="' + _esc(title) + '">' +
      '<span class="fund-badge-mark">' + def.mark + '</span>' + _esc(text) + '</span>';
  }

  /**
   * 解析本区块角标状态：优先用 metadata 的 per-key state；
   * 拿不到 metadata 时按本响应的 ok / age 就地推导（保守取 closed / unavailable）。
   */
  function _resolveState(cap, key, envelope) {
    var entry = cap && cap[key];
    if (entry && BADGE_STATES[entry.state]) {
      return { state: entry.state, label: entry.label, reason: entry.reason };
    }
    if (!envelope || envelope.ok === false) {
      return { state: 'unavailable', label: '不可用', reason: (envelope && envelope.reason) || null };
    }
    return { state: 'closed', label: '收盘' + (envelope.as_of ? ' ' + envelope.as_of : ''), reason: null };
  }

  // ================================================================
  //  容器
  // ================================================================
  function _block() { return document.getElementById('fundamental-block'); }
  function _valBar() { return document.getElementById('valuation-bar'); }

  /**
   * 更新浮层标题（打开状态下显示「基本面 · 股票名」），
   * 未打开时不碰标题，避免干扰其它 Tab。
   */
  function _syncOverlayTitle(name) {
    var overlay = document.getElementById('kline-overlay');
    if (!overlay || !overlay.classList.contains('visible')) return;
    if (window.appState && window.appState.activeOverlayTab !== 'fundamental') return;
    var title = document.getElementById('kline-overlay-title');
    if (title) title.textContent = name ? ('基本面 · ' + name) : '基本面';
  }

  // ================================================================
  //  加载态 / 失败态渲染（与「缺数」严格区分）
  // ================================================================
  function _renderLoading(code) {
    var block = _block();
    if (block) {
      block.innerHTML =
        '<div class="fund-head"><div class="fund-title">基本面 <span class="fund-code">' + _esc(code) + '</span></div>' +
        '<div class="fund-head-meta"><span class="fund-loading-tag">取数中…</span>' +
        '<span class="fund-loading-elapsed" id="fund-loading-elapsed">已等待 0 秒</span></div></div>' +
        '<div class="fund-grid">' + CARD_SPECS.map(function (spec) {
          return '<div class="fund-card fund-card-skeleton">' +
            '<div class="fund-card-label">' + _esc(spec.label) + '</div>' +
            '<div class="fund-skeleton-line"></div>' +
            '<div class="fund-skeleton-line short"></div></div>';
        }).join('') + '</div>' +
        '<div class="fund-note">首次取数需访问新浪/百度/巨潮三个源（实测 10–18 秒），之后命中 6 小时缓存。</div>';
    }
    var bar = _valBar();
    if (bar) {
      bar.innerHTML = '<div class="fund-val-head">估值分位（近 5 年）<span class="fund-loading-tag">取数中…</span></div>' +
        '<div class="fund-skeleton-line"></div>';
    }
  }

  /** 该标的是否仍是"当前渲染目标"（以最后一次 renderFundamental 入参为准） */
  function _isTarget(code) {
    if (!_targetCode) return true;
    return String(code) === String(_targetCode);
  }

  /** 停止加载态计时与看门狗 */
  function _stopLoading() {
    if (_load.ticker) { clearInterval(_load.ticker); _load.ticker = 0; }
    if (_load.watchdog) { clearTimeout(_load.watchdog); _load.watchdog = 0; }
    _load.code = '';
    _load.startedAt = 0;
  }

  /** 进入加载态：计时器每秒刷新"已等待 N 秒"，看门狗在 T_FINANCE+3s 兜底失败态 */
  function _startLoading(code) {
    _stopLoading();
    _load.code = code;
    _load.startedAt = Date.now();
    _renderLoading(code);
    _load.ticker = setInterval(function () {
      var el = document.getElementById('fund-loading-elapsed');
      if (!el) return;
      var sec = Math.floor((Date.now() - _load.startedAt) / 1000);
      el.textContent = '已等待 ' + sec + ' 秒';
    }, 1000);
    _load.watchdog = setTimeout(function () {
      if (_load.code !== code) return;          // 已经渲染完/被替换
      if (!_isTarget(code)) { _stopLoading(); return; }
      _stopLoading();
      _renderTransportError(code,
        '取数超时（' + Math.round((T_FINANCE + 3000) / 1000) + ' 秒未返回），可点击下方「重新加载」重试',
        'fundamental');
    }, T_FINANCE + 3000);
  }

  /** 结果代次过期但标的已变：用**最新** activeCode 补一次渲染，绝不留下 loading */
  function _renderLatestFund() {
    var latest = _targetCode || (window.appState && window.appState.activeCode) || '';
    if (!latest) return;
    if (window.appState && window.appState.activeOverlayTab &&
        window.appState.activeOverlayTab !== 'fundamental') return;
    setTimeout(function () { renderFundamental(latest); }, 0);
  }

  function _renderTransportError(code, message, which) {
    var html = '<div class="fund-error">' +
      '<div class="fund-error-title">' + _esc(which) + '请求失败</div>' +
      '<div class="fund-error-reason">' + _esc(message) + '</div>' +
      '<button type="button" class="fund-retry" data-fund-retry="' + _esc(which) + '">重新加载</button>' +
      '</div>';
    var target = which === 'valuation' ? _valBar() : _block();
    if (target) target.innerHTML = html;
  }

  // ================================================================
  //  主渲染：基本面 6 卡
  // ================================================================
  /**
   * @param {string} code 标的代码（sh600519）
   * @param {Object} [opts] {force:true} 强制忽略前端短缓存
   */
  async function renderFundamental(code, opts) {
    opts = opts || {};
    var block = _block();
    if (!block) return;
    if (!code) {
      block.innerHTML = '<div class="fund-empty">未选中标的：请先在左侧选股列表点选一只股票。</div>';
      return;
    }

    _targetCode = code;
    var slot = _cacheSlot(code);
    var cached = slot.finance;
    if (!opts.force && cached && (Date.now() - cached.ts) < TTL_RENDER_CACHE) {
      _stopLoading();
      _paintFundamental(code, cached.json, null);
      return;
    }

    // 同一标的已在取数中：复用同一请求。
    // 页面智能刷新会周期性重触发当前 Tab 的 loadFn，若每次都新建请求，
    // UI 会反复回到骨架屏、看门狗也反复重置 —— 看起来就是"永久取数中"。
    if (!opts.force && _inflightFund.code === code && _inflightFund.promise) {
      return _inflightFund.promise;
    }

    var myToken = ++_tokenFund;
    _startLoading(code);

    var task = (async function () {
      var json = await _fetchEnvelope('/api/finance?code=' + encodeURIComponent(code), T_FINANCE);

      // 竞态处理：
      //  - 代次过期但当前仍是同一标的 + 基本面 tab → 结果依然有效，继续渲染；
      //  - 已切到别的标的 → 丢弃本次结果，但**必须**用最新 activeCode 补渲染，
      //    否则最新标的会永远停在骨架屏。
      if (myToken !== _tokenFund && !_isTarget(code)) {
        if (_load.code === code) _stopLoading();
        _renderLatestFund();
        return;
      }

      if (json && json._transport_error) {
        _stopLoading();
        _renderTransportError(code, json._transport_error, 'fundamental');
        if (!_isTarget(code)) _renderLatestFund();
        return;
      }

      _stopLoading();
      slot.finance = { ts: Date.now(), json: json };
      await _paintFundamental(code, json, myToken);
    })();

    _inflightFund = { code: code, promise: task };
    try {
      return await task;
    } finally {
      if (_inflightFund.promise === task) _inflightFund = { code: '', promise: null };
    }
  }

  /** 依据后端信封绘制 6 卡 */
  async function _paintFundamental(code, json, token) {
    var block = _block();
    if (!block) return;

    var cap = await _capability();
    // 守卫：await 期间可能已切标的/发起新代次 —— 旧结果不得覆盖新 UI（T09 二次修复：
    // 之前"bj430047 的加载态被上一只股票的迟到 paint 覆盖"就是这里缺少守卫）
    if (token != null && token !== _tokenFund) return;
    if (!_isTarget(code)) return;      // 已切到别的标的：旧结果不得写 DOM
    var envelopeState = _resolveState(cap, 'finance', json);
    var data = (json && json.data) || null;

    // ---- 整块不可用（ok:false 且无 data.cards）：统一原因 + 重试 ----
    if (!data || !data.cards) {
      var reason = (json && json.reason) || '数据源未返回任何卡片';
      block.innerHTML =
        '<div class="fund-head"><div class="fund-title">基本面 <span class="fund-code">' + _esc(code) + '</span></div>' +
        '<div class="fund-head-meta">' + _badge('unavailable', '不可用', reason) + '</div></div>' +
        '<div class="fund-error">' +
        '<div class="fund-error-title">6 张卡全部不可用</div>' +
        '<div class="fund-error-reason">' + _esc(reason) + '</div>' +
        '<button type="button" class="fund-retry" data-fund-retry="fundamental">重新加载</button>' +
        '</div>' +
        '<div class="fund-note">这里不显示 0：0 是一个合法数值，用 0 冒充缺数会让「贵/便宜」判断彻底反向。</div>';
      _syncOverlayTitle(null);
      return;
    }

    var name = data.name || code;
    _syncOverlayTitle(name);

    var staleEnvelope = false;
    var html = '';

    // ---- 头部：标的 + 报告期 + 可获取数 + 角标 ----
    var reportLabel = data.report_period_label || '报告期未知';
    var reportQuarter = data.report_period_quarter ? ('（' + data.report_period_quarter + '）') : '';
    var availCount = (data.availability && data.availability.available_count) || 0;
    var totalCount = (data.availability && data.availability.total) || CARD_SPECS.length;

    html += '<div class="fund-head">' +
      '<div class="fund-title">' + _esc(name) + ' <span class="fund-code">' + _esc(data.code || code) + '</span></div>' +
      '<div class="fund-head-meta">' +
        '<span class="fund-meta-item">报告期 ' + _esc(reportLabel) + _esc(reportQuarter) + '</span>' +
        '<span class="fund-meta-item">可获取 ' + availCount + '/' + totalCount + '</span>' +
        _badge(envelopeState.state, envelopeState.label, envelopeState.reason) +
      '</div></div>';

    // ---- 报告期陈旧警示（只读后端字段，前端不自己算） ----
    if (data.latest_period_is_stale === true || (data.period_lag_quarters != null && data.period_lag_quarters > 1)) {
      staleEnvelope = true;
      html += '<div class="fund-stale-banner">' +
        '<span class="fund-warn">数据可能过期</span>' +
        '报告期 ' + _esc(reportLabel) + ' 落后当前时点 ' + _esc(String(data.period_lag_quarters)) +
        ' 个季度（含 1 个季度披露宽限）。' + _esc(data.stale_rule || '') +
        '</div>';
    }

    // ---- 6 张卡 ----
    html += '<div class="fund-grid">';
    CARD_SPECS.forEach(function (spec) {
      html += _cardHtml(spec, data.cards[spec.key], data, staleEnvelope);
    });
    html += '</div>';

    // ---- 6 卡全不可用：给出总原因 + 「重新加载」自救入口 ----
    // （此前只在"整块无 data.cards"时才有重试按钮；逐卡 unavailable 的情况
    //   用户只能干瞪眼 —— T09 验收要求"不可用 + 原因 + 重新加载"两态都成立）
    if (availCount === 0) {
      var reasons = [];
      CARD_SPECS.forEach(function (spec) {
        var c = data.cards[spec.key] || {};
        if (c.reason && reasons.indexOf(c.reason) < 0) reasons.push(c.reason);
      });
      html += '<div class="fund-error">' +
        '<div class="fund-error-title">6 张卡全部不可用</div>' +
        '<div class="fund-error-reason">' + _esc(reasons.join('；') || '数据源未返回任何可用字段') + '</div>' +
        '<button type="button" class="fund-retry" data-fund-retry="fundamental">重新加载</button>' +
        '</div>';
    }

    // ---- 口径与明细（可折叠，保证每个数字都可追溯） ----
    html += _detailsHtml(data);

    block.innerHTML = html;
  }

  /** 单卡 HTML */
  function _cardHtml(spec, card, data, staleEnvelope) {
    card = card || {};
    var valueHtml;
    var colorStyle = '';

    if (_isNum(card.value)) {
      if (spec.directional) {
        // 只有方向性指标用涨红跌绿；0 视作中性
        var v = Number(card.value);
        if (v > 0) colorStyle = ' style="color:var(--red)"';
        else if (v < 0) colorStyle = ' style="color:var(--green)"';
        else colorStyle = ' style="color:var(--text)"';
      } else {
        // 估值/盈利水平不是「涨跌」，一律中性色（禁止红绿）
        colorStyle = ' style="color:var(--text)"';
      }
      valueHtml = '<span class="fund-value"' + colorStyle + '>' +
        _esc(_fmt(card.value, spec.digits)) + '</span>' +
        '<span class="fund-unit">' + _esc(card.unit || spec.unit) + '</span>';
    } else {
      valueHtml = '<span class="fund-value fund-value-missing">不可用</span>';
    }

    // 报告期：财务卡用 period_label（2026Q2），估值卡用 as_of（交易日）
    // 缺失时写「未知」而不是占位符——占位符看不出是缺数还是加载中。
    var periodText = card.period_label || card.as_of || '未知';
    var periodNote = '';
    if (card.latest_period_is_stale === true) {
      periodNote = '<span class="fund-warn">数据可能过期</span>';
    } else if (card.latest_period_is_stale === false) {
      periodNote = '<span class="fund-fresh">最新期</span>';
    } else if (card.as_of) {
      periodNote = '<span class="fund-fresh">最新交易日</span>';
    }

    var metaLine = '<span class="fund-card-period">报告期 ' + _esc(periodText) + '</span>' + periodNote;
    if (staleEnvelope && card.latest_period_is_stale !== true && card.period_label) {
      metaLine += '<span class="fund-warn">数据可能过期</span>';
    }

    var srcLine = card.source
      ? '<span class="fund-card-src">来源 ' + _esc(card.source) + '</span>'
      : '<span class="fund-card-src fund-card-src-none">来源 未取得</span>';

    var extraLine = '';
    if (_isNum(card.percentile_5y)) {
      extraLine = '<span class="fund-card-pct">近 5 年分位 ' + _esc(_fmt(card.percentile_5y, 2)) + '%' +
        (card.extra && card.extra.sample ? '（' + _esc(String(card.extra.sample)) + ' 个交易日）' : '') + '</span>';
    }

    // 缺数：显式「不可用 · 原因」，不写 0、不写占位符
    var unavailLine = '';
    if (!_isNum(card.value)) {
      var reason = card.reason || '数据源未返回该指标';
      unavailLine = '<div class="fund-card-unavail" title="' + _esc(reason) + '">不可用 · ' + _esc(reason) + '</div>';
    }

    var hint = card.formula || spec.hint || '';

    return '<div class="fund-card" data-key="' + _esc(spec.key) + '">' +
      '<div class="fund-card-head">' +
        '<span class="fund-card-label" title="' + _esc(hint) + '">' + _esc(spec.label) + '</span>' +
      '</div>' +
      '<div class="fund-card-value-row">' + valueHtml + '</div>' +
      '<div class="fund-card-meta">' + metaLine + '</div>' +
      srcLine +
      extraLine +
      unavailLine +
      '</div>';
  }

  /** 「口径与明细」折叠块：把后端算好的 TTM 聚合与每股数据摊开，可自行复核 */
  function _detailsHtml(data) {
    var ttm = data.ttm || {};
    var rows = [];
    function push(label, value) {
      if (value === null || value === undefined) return;
      rows.push('<div class="fund-detail-row"><span>' + _esc(label) + '</span><b>' + _esc(value) + '</b></div>');
    }
    push('TTM 口径', ttm.basis || null);
    push('TTM 营业总收入', _fmtAmount(ttm.revenue_ttm));
    push('TTM 归母净利润', _fmtAmount(ttm.net_profit_ttm));
    push('TTM 每股收益 EPS', _isNum(ttm.eps_ttm) ? (_fmt(ttm.eps_ttm, 4) + ' 元') : null);
    push('每股净资产 BPS', _isNum(data.bps) ? (_fmt(data.bps, 4) + ' 元') : null);
    push('报告期原始值', data.report_period || null);
    push('报告期季度标签', data.report_period_quarter || null);
    push('数据年龄', (data && typeof data.age === 'number') ? (data.age + ' 秒') : null);
    if (data.dividend && _isNum(data.dividend.per_share_ttm)) {
      push('近 12 个月每股分红', _fmt(data.dividend.per_share_ttm, 4) + ' 元（' +
        (data.dividend.records_ttm != null ? data.dividend.records_ttm : 0) + ' 次）');
      push('分红统计窗口', data.dividend.window || null);
    }
    if (!rows.length) return '';
    return '<details class="fund-details"><summary>口径与明细（可复核）</summary>' +
      rows.join('') + '</details>';
  }

  // ================================================================
  //  主渲染：估值分位条
  // ================================================================
  /**
   * @param {string} code 标的代码
   * @param {Object} [opts] {force:true}
   */
  async function renderValuationBar(code, opts) {
    opts = opts || {};
    var bar = _valBar();
    if (!bar) return;
    if (!code) {
      bar.innerHTML = '<div class="fund-empty">未选中标的：分位条需要标的价格与估值序列。</div>';
      return;
    }

    var myToken = ++_tokenVal;
    var slot = _cacheSlot(code);
    var cached = slot.valuation;
    if (!opts.force && cached && (Date.now() - cached.ts) < TTL_RENDER_CACHE) {
      await _paintValuation(code, cached.json, myToken);
      return;
    }

    bar.innerHTML = '<div class="fund-val-head">估值分位（近 5 年）<span class="fund-loading-tag">取数中…</span></div>' +
      '<div class="fund-skeleton-line"></div>';

    var json = await _fetchEnvelope('/api/finance/valuation?code=' + encodeURIComponent(code), T_VALUATION);
    if (myToken !== _tokenVal) return;

    if (json && json._transport_error) {
      _renderTransportError(code, json._transport_error, 'valuation');
      return;
    }
    slot.valuation = { ts: Date.now(), json: json };
    await _paintValuation(code, json, myToken);
  }

  /** 在 0–100 的**分位刻度**上求某个数值的百分位（与后端 current_pct 同口径：≤ 计数占比） */
  function _rankPct(series, value) {
    if (!series || !series.length || !_isNum(value)) return null;
    var count = 0;
    var valid = 0;
    for (var i = 0; i < series.length; i++) {
      var v = series[i] && series[i].value;
      if (!_isNum(v)) continue;
      valid++;
      if (Number(v) <= Number(value)) count++;
    }
    if (!valid) return null;
    return count / valid * 100;
  }

  async function _paintValuation(code, json, token) {
    var bar = _valBar();
    if (!bar) return;

    var cap = await _capability();
    if (token != null && token !== _tokenVal) return;   // 旧代次不得覆盖新 UI
    if (!_isTarget(code)) return;
    var state = _resolveState(cap, 'valuation', json);
    var data = (json && json.data) || null;

    if (!data || !data.percentile) {
      var reason = (json && json.reason) || '估值序列不可用';
      bar.innerHTML = '<div class="fund-val-head">估值分位（近 5 年）' +
        _badge('unavailable', '不可用', reason) + '</div>' +
        '<div class="fund-error"><div class="fund-error-reason">不可用 · ' + _esc(reason) + '</div>' +
        '<button type="button" class="fund-retry" data-fund-retry="valuation">重新加载</button></div>';
      return;
    }

    var indicators = data.indicators || [];
    var rowsHtml = '';

    indicators.forEach(function (key) {
      var p = data.percentile[key] || {};
      var series = data.series[key] || [];
      var label = p.label || key;
      var pct = _isNum(p.current_pct) ? Number(p.current_pct) : null;

      var trackHtml = '';
      if (pct === null) {
        trackHtml = '<div class="fund-val-track fund-val-track-empty"></div>';
      } else {
        var ticks = '';
        [['p10', p.p10], ['p50', p.p50], ['p90', p.p90]].forEach(function (pair) {
          var tp = _rankPct(series, pair[1]);
          if (tp === null) return;
          ticks += '<span class="fund-val-tick" style="left:' + tp.toFixed(2) + '%" title="' +
            _esc(pair[0] + ' = ' + _fmt(pair[1], 2)) + '"></span>';
        });
        trackHtml = '<div class="fund-val-track">' +
          '<div class="fund-val-zone-low"></div>' +
          ticks +
          '<div class="fund-val-marker" style="left:' + pct.toFixed(2) + '%" title="当前位置 ' + pct.toFixed(2) + '%"></div>' +
          '</div>';
      }

      var footBits = [];
      if (_isNum(p.current)) footBits.push('当前 ' + _fmt(p.current, 2));
      if (_isNum(p.p10)) footBits.push('p10 ' + _fmt(p.p10, 2));
      if (_isNum(p.p50)) footBits.push('p50 ' + _fmt(p.p50, 2));
      if (_isNum(p.p90)) footBits.push('p90 ' + _fmt(p.p90, 2));
      if (_isNum(p.min)) footBits.push('最低 ' + _fmt(p.min, 2));
      if (_isNum(p.max)) footBits.push('最高 ' + _fmt(p.max, 2));

      rowsHtml += '<div class="fund-val-row">' +
        '<div class="fund-val-name">' + _esc(label) + '</div>' +
        '<div class="fund-val-body">' + trackHtml +
          '<div class="fund-val-foot">' +
            '<span class="fund-val-pct">' + (pct === null
              ? '无法给出分位（' + _esc(p.reason || '样本不足') + '）'
              : ('5 年分位 ' + pct.toFixed(2) + '%')) + '</span>' +
            '<span class="fund-val-sample">' + footBits.join(' · ') +
              (p.sample ? (' · 样本 ' + p.sample + ' 条') : '') + '</span>' +
            (p.window_start ? ('<span class="fund-val-window">区间 ' + _esc(p.window_start) +
              ' ~ ' + _esc(p.window_end) + '</span>') : '') +
          '</div>' +
        '</div>' +
      '</div>';
    });

    var headExtra = (data.years ? ('近 ' + data.years + ' 年') : '近 5 年');
    bar.innerHTML =
      '<div class="fund-val-head">估值分位（' + _esc(headExtra) + '）' +
        _badge(state.state, state.label, state.reason) +
        '<span class="fund-val-hint">分位越低越便宜（0 = 窗口最低，100 = 窗口最高）；刻度为「分位」而非线性数值轴</span>' +
      '</div>' +
      (rowsHtml || '<div class="fund-empty">后端未返回任何估值指标序列。</div>') +
      (data.missing && data.missing.length
        ? ('<div class="fund-note">未返回的指标：' + _esc(data.missing.join('、')) +
           (data.reason_by_indicator && Object.keys(data.reason_by_indicator).length
             ? ('（原因：' + _esc(Object.keys(data.reason_by_indicator).map(function (k) {
                 return k + ' ' + data.reason_by_indicator[k];
               }).join('；')) + '）')
             : '') + '</div>')
        : '') +
      '<div class="fund-note">' + _esc(data.scale_note || '') + '</div>';
  }

  // ================================================================
  //  重试按钮（事件委托，避免内联 onclick 逃逸 CSP / 作用域）
  // ================================================================
  document.addEventListener('click', function (e) {
    var btn = e.target && e.target.closest ? e.target.closest('[data-fund-retry]') : null;
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    var which = btn.getAttribute('data-fund-retry');
    var code = (window.appState && window.appState.activeCode) || null;
    if (!code) return;
    if (which === 'valuation') renderValuationBar(code, { force: true });
    else renderFundamental(code, { force: true });
  });

  // ================================================================
  //  对外接口
  // ================================================================
  window.renderFundamental = renderFundamental;
  window.renderValuationBar = renderValuationBar;

  /** 调试用：清掉前端短缓存（改后端后可手动强制刷新） */
  window.__fundamentalResetCache = function () {
    _renderCache = {};
    _capabilityCache = null;
  };

  // 供 QA 断言：不依赖 DOM 也能看到 6 卡规格
  window.__fundamentalCardSpecs = CARD_SPECS.map(function (s) { return s.key; });
})();
