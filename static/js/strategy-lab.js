/**
 * DAPENG Alpha - 策略实验室前端
 * 借鉴模块化回测与 QuantStats 风格指标展示，使用本项目原创接口与 ECharts 渲染。
 */
(function () {
  'use strict';

  var requestVersion = 0;
  var equityChart = null;

  function num(value, fallback) {
    var n = Number(value);
    return Number.isFinite(n) ? n : (fallback || 0);
  }

  function toneClass(tone) {
    if (tone === 'bullish') return 'up';
    if (tone === 'bearish') return 'dn';
    return 'neutral';
  }

  function metricClass(value, inverse) {
    var n = num(value);
    var good = inverse ? n <= 15 : n >= 0;
    return good ? 'up' : 'dn';
  }

  function renderError(panel, message, code) {
    panel.innerHTML = '<div class="osl-state error"><strong>策略评估暂不可用</strong><span>' +
      escapeHtml(message || '请求失败，请稍后重试') + '</span>' +
      '<button class="osl-btn" onclick="loadStrategyLab(\'' + encodeURIComponent(code || '') + '\', true)">重新加载</button></div>';
  }

  function renderStrategyLab(data, panel) {
    var regime = data.regime || {};
    var risk = data.risk || {};
    var strategies = Array.isArray(data.strategies) ? data.strategies : [];
    var safeName = escapeHtml(data.name || data.code || '--');
    var safeCode = escapeHtml(data.code || '--');

    var cards = strategies.length ? strategies.map(function (item, index) {
      return '<article class="lab-card" data-strategy-index="' + index + '">' +
        '<div class="lab-card-head"><div><strong>' + escapeHtml(item.label || item.key || '策略') + '</strong>' +
        '<span>' + escapeHtml(item.grade || '--') + '</span></div><em>' + escapeHtml(item.description || '') + '</em></div>' +
        '<div class="lab-metrics">' +
          '<div><span>总收益</span><strong class="' + metricClass(item.total_return) + '">' + num(item.total_return).toFixed(2) + '%</strong></div>' +
          '<div><span>最大回撤</span><strong class="' + metricClass(item.max_drawdown, true) + '">' + num(item.max_drawdown).toFixed(2) + '%</strong></div>' +
          '<div><span>胜率</span><strong>' + num(item.win_rate).toFixed(1) + '%</strong></div>' +
          '<div><span>夏普</span><strong>' + num(item.sharpe).toFixed(2) + '</strong></div>' +
          '<div><span>盈亏比</span><strong>' + num(item.profit_factor).toFixed(2) + '</strong></div>' +
          '<div><span>交易数</span><strong>' + Math.max(0, Math.round(num(item.trades))) + '</strong></div>' +
        '</div></article>';
    }).join('') : '<div class="osl-state"><strong>历史数据不足</strong><span>至少需要 65 根有效 K 线才能完成策略评估。</span></div>';

    panel.innerHTML = '<div class="osl-toolbar">' +
      '<div><div class="osl-heading">' + safeName + ' <small>' + safeCode + '</small></div>' +
      '<div class="osl-subheading">' + escapeHtml(data.period === 'week' ? '周线' : '日线') + ' · ' + num(data.bars) + ' 根K线 · 规则策略历史评估</div></div>' +
      '<div class="osl-actions"><button class="osl-btn" onclick="loadStrategyLab(window.appState.activeCode, true)">刷新评估</button></div></div>' +
      '<div class="lab-regime ' + toneClass(regime.tone) + '"><div><span>当前环境</span><strong>' + escapeHtml(regime.label || '未知') + '</strong></div><p>' + escapeHtml(regime.detail || '') + '</p></div>' +
      '<div class="lab-risk-grid">' +
        '<div><span>ATR</span><strong>' + num(risk.atr).toFixed(3) + '</strong></div>' +
        '<div><span>参考止损</span><strong>' + (num(risk.stop_loss) > 0 ? num(risk.stop_loss).toFixed(2) : '--') + '</strong></div>' +
        '<div><span>建议仓位</span><strong>' + num(risk.position_pct).toFixed(0) + '%</strong></div>' +
        '<div><span>RSI</span><strong>' + (risk.rsi == null ? '--' : num(risk.rsi).toFixed(1)) + '</strong></div>' +
        '<div><span>MACD柱</span><strong class="' + metricClass(risk.macd_hist) + '">' + num(risk.macd_hist).toFixed(4) + '</strong></div>' +
      '</div>' +
      '<div class="lab-grid">' + cards + '</div>' +
      '<div class="osl-section-title">策略资金曲线（起点 100）</div><div id="strategy-equity-chart" class="strategy-equity-chart"></div>' +
      '<div class="osl-disclaimer">' + escapeHtml(data.disclaimer || '历史规则模拟不代表未来收益。') + '</div>';

    requestAnimationFrame(function () { renderEquityChart(strategies); });
  }

  function renderEquityChart(strategies) {
    var el = document.getElementById('strategy-equity-chart');
    if (!el || typeof echarts === 'undefined') return;
    if (equityChart && !equityChart.isDisposed()) equityChart.dispose();
    equityChart = echarts.init(el, null, { renderer: 'canvas' });

    var maxLen = Math.max.apply(null, [1].concat(strategies.map(function (s) { return Array.isArray(s.equity) ? s.equity.length : 0; })));
    var colors = ['#3b82f6', '#06b6d4', '#a855f7'];
    var textColor = getComputedStyle(document.documentElement).getPropertyValue('--text2').trim() || '#8b9ec2';
    var gridColor = 'rgba(128,148,184,.10)';
    equityChart.setOption({
      animationDuration: 350,
      tooltip: { trigger: 'axis', valueFormatter: function (v) { return num(v).toFixed(2); } },
      legend: { top: 0, textStyle: { color: textColor, fontSize: 10 } },
      grid: { left: 46, right: 18, top: 34, bottom: 28 },
      xAxis: { type: 'category', data: Array.from({length: maxLen}, function (_, i) { return i; }), axisLabel: { color: textColor, fontSize: 9 }, axisLine: { lineStyle: { color: gridColor } } },
      yAxis: { type: 'value', scale: true, axisLabel: { color: textColor, fontSize: 9 }, splitLine: { lineStyle: { color: gridColor } } },
      series: strategies.map(function (item, index) {
        return { name: item.label || item.key, type: 'line', smooth: true, showSymbol: false, connectNulls: false, data: Array.isArray(item.equity) ? item.equity : [], lineStyle: { width: 2, color: colors[index % colors.length] }, itemStyle: { color: colors[index % colors.length] } };
      })
    });
  }

  async function loadStrategyLab(code, force) {
    var panel = document.getElementById('strategy-lab-panel');
    if (!panel) return;
    code = code || (window.appState && window.appState.activeCode);
    if (!code) {
      panel.innerHTML = '<div class="osl-state"><strong>请选择股票</strong><span>选择股票后可进行规则策略历史评估。</span></div>';
      return;
    }

    var period = window.appState && window.appState.period === 'week' ? 'week' : 'day';
    var cacheKey = code + ':' + period;
    window._strategyLabCache = window._strategyLabCache || {};
    if (!force && window._strategyLabCache[cacheKey]) {
      renderStrategyLab(window._strategyLabCache[cacheKey], panel);
      return;
    }

    var version = ++requestVersion;
    panel.innerHTML = '<div class="osl-state loading"><div class="osl-spinner"></div><strong>正在评估策略</strong><span>读取历史K线并计算收益、回撤与胜率...</span></div>';
    try {
      var data = typeof fetchStrategyLab === 'function' ? await fetchStrategyLab(code, period) : null;
      if (version !== requestVersion) return;
      if (!data) return renderError(panel, '接口无响应或数据源暂不可用', code);
      window._strategyLabCache[cacheKey] = data;
      renderStrategyLab(data, panel);
    } catch (error) {
      if (version !== requestVersion) return;
      renderError(panel, error && error.message, code);
    }
  }

  window.loadStrategyLab = loadStrategyLab;
  window.addEventListener('resize', function () {
    if (equityChart && !equityChart.isDisposed()) equityChart.resize();
  });
})();
