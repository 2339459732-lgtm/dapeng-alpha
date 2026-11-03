/**
 * DAPENG Alpha - K线+震荡指标自动分析总结面板 (v1)
 * 数据源：GET /api/analyze/chart-summary?code=X&period=day
 * 由 chart.js renderKline 分发器联动触发；60s 服务端缓存。
 */
(function () {
  'use strict';

  var _lastKey = '';
  var _loading = false;

  var LEVEL_META = {
    bullish:     { label: '偏多', cls: 'bullish' },
    bearish:     { label: '偏空', cls: 'bearish' },
    risk:        { label: '风险', cls: 'risk' },
    opportunity: { label: '机会', cls: 'opportunity' },
    neutral:     { label: '中性', cls: 'neutral' },
  };

  function el(id) { return document.getElementById(id); }

  function render(payload, code, period) {
    var strip = el('chart-analysis-strip');
    if (!strip) return;
    strip.hidden = false;

    var stateEl = el('cas-state');
    var meta = LEVEL_META[payload.level] || LEVEL_META.neutral;
    if (stateEl) {
      stateEl.textContent = payload.state || '--';
      stateEl.className = 'cas-state ' + meta.cls;
    }
    var metaEl = el('cas-meta');
    if (metaEl) {
      metaEl.textContent = code + ' · ' + (period === 'day' ? '日K' : period === 'week' ? '周K' : period + '分') +
        ' · ' + (payload.bars || 0) + '根' +
        ' · ' + (payload.ai_powered ? 'DeepSeek AI' : '规则引擎') +
        ' · ' + (payload.update_time || '').slice(5, 16);
    }
    var summaryEl = el('cas-summary');
    if (summaryEl) summaryEl.textContent = payload.summary || '--';

    var reasonsEl = el('cas-reasons');
    if (reasonsEl) {
      var ind = payload.indicators || {};
      var chips = [];
      if (ind.rsi != null) chips.push('RSI ' + ind.rsi);
      if (ind.kdj) chips.push('KDJ ' + ind.kdj.k + '/' + ind.kdj.d + '/' + ind.kdj.j);
      if (ind.macd) chips.push('MACD动能' + ind.macd.momentum);
      if (ind.ma) chips.push('均线' + ind.ma.alignment);
      if (ind.boll) chips.push('布林位 ' + Math.round((ind.boll.position || 0) * 100) + '%');
      reasonsEl.innerHTML =
        '<div class="cas-chips">' + chips.map(function (c) { return '<span>' + c + '</span>'; }).join('') + '</div>' +
        (payload.reasons || []).map(function (r) { return '<div class="cas-reason">· ' + escapeHtml(r) + '</div>'; }).join('');
    }
  }

  function escapeHtml(text) {
    return String(text == null ? '' : text).replace(/[<>&"']/g, function (c) {
      return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function renderError(code, message) {
    var strip = el('chart-analysis-strip');
    if (!strip) return;
    strip.hidden = false;
    var stateEl = el('cas-state');
    if (stateEl) { stateEl.textContent = '分析不可用'; stateEl.className = 'cas-state neutral'; }
    var metaEl = el('cas-meta');
    if (metaEl) metaEl.textContent = code || '';
    var summaryEl = el('cas-summary');
    if (summaryEl) summaryEl.textContent = message || '分析数据暂不可用';
    var reasonsEl = el('cas-reasons');
    if (reasonsEl) reasonsEl.innerHTML = '';
  }

  window.loadChartSummary = async function (code, period) {
    period = period || 'day';
    var key = code + ':' + period;
    if (_loading && key === _lastKey) return;
    _lastKey = key;
    _loading = true;
    try {
      var resp = await fetch('/api/analyze/chart-summary?code=' + encodeURIComponent(code) + '&period=' + encodeURIComponent(period));
      var payload = await resp.json();
      if (payload && payload.ok) {
        render(payload, code, period);
      } else {
        renderError(code, (payload && payload.message) || '分析数据暂不可用');
      }
    } catch (e) {
      renderError(code, '分析接口异常: ' + e.message);
    } finally {
      _loading = false;
    }
  };
})();
