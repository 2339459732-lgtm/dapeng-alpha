/**
 * DAPENG Alpha - 大盘纵览 (Market Dashboard V6.0 高级金融科技版)
 * 真实行情全景聚合 + 多维指数趋势 + 情绪与广度雷达 + 行业热力 + 本地模拟账户
 */
(function () {
  'use strict';

  var STORAGE_KEY = 'dapeng_market_account_v1';
  var VIEW_STORAGE_KEY = 'dapeng_market_view_v1';
  var INITIAL_CASH = 2000;

  function loadViewPreference() {
    try {
      var saved = JSON.parse(localStorage.getItem(VIEW_STORAGE_KEY) || '{}');
      return saved && typeof saved === 'object' ? saved : {};
    } catch (error) { return {}; }
  }

  var savedView = loadViewPreference();
  var dashboardState = {
    data: null,
    activeList: 'leaders',
    chart: null,
    accountChart: null,
    equityChart: null,
    focusedIndexCode: null,
    trendWindow: savedView.trendWindow === 'all' ? 'all' : ([12, 24, 48].indexOf(Number(savedView.trendWindow)) >= 0 ? Number(savedView.trendWindow) : 48),
    trendMode: savedView.trendMode === 'index' ? 'index' : 'percent',
    sectorSort: ['avg_chg', 'up_ratio', 'amount'].indexOf(savedView.sectorSort) >= 0 ? savedView.sectorSort : 'avg_chg',
    chartFocused: false,
    loading: false,
    lastLoadedAt: 0,
    lastSuccessAt: 0,
    refreshTimer: null,
    filters: { sector: '', bucket: '', query: '' },
    // 从其它页面带过来的板块名（?sector=），首次渲染后应用到榜单筛选
    pendingSector: '',
    moverSort: savedView.moverSort === 'amount' || savedView.moverSort === 'turnover' ? savedView.moverSort : 'chg',
    // v18: 资金面体征（沪深港通）与外部环境（全球联动）独立数据源
    capitalVitals: null,
    capitalError: null,
    capitalLastFetch: 0,
    capitalLoading: false,
    capitalStale: false,
    externalLinkage: null,
    externalError: null,
    externalLastFetch: 0,
    externalLoading: false,
    // 实时推送（SSE）：行情实时刷新，失败自动降级为轮询
    streamCtrl: null,
    streamActive: false,
    streamDegraded: false,
    streamReason: '',
    streamLastPush: 0
  };

  // ===== 数据性质徽章（对齐 intelligent-analysis.js 四级降级链语义）=====
  // 真实=实时直采 / 历史快照=上次成功结果沿用 / 聚合口径=第三方或样本聚合推导 / 本地样本=本地缓存
  var NATURE_META = {
    real: { label: '真实', tip: '数据性质：真实 —— 实时直采，未经二次加工。' },
    snapshot: { label: '历史快照', tip: '数据性质：历史快照 —— 上游停更或本次取数失败，沿用上一次成功结果。' },
    aggregated: { label: '聚合口径', tip: '数据性质：聚合口径 —— 由第三方行情或系统样本池聚合推导，非全市场逐笔直采。' },
    local: { label: '本地样本', tip: '数据性质：本地样本 —— 浏览器本地存储 / 本地样本兜底数据。' }
  };

  /**
   * 更新指定徽章元素的数据性质（文本 + 语义配色 + 悬浮说明）。
   * @param {string} id - 徽章元素 id
   * @param {string} nature - real | snapshot | aggregated | local
   */
  function setNature(id, nature) {
    var el = document.getElementById(id);
    if (!el) return;
    var meta = NATURE_META[nature] || NATURE_META.aggregated;
    el.setAttribute('data-nature', nature);
    el.textContent = meta.label;
    el.title = meta.tip;
  }

  /**
   * 生成一个数据性质徽章的内联 HTML（用于动态渲染区域）。
   * @param {string} nature - real | snapshot | aggregated | local
   * @returns {string} HTML 片段
   */
  function natureBadgeHtml(nature) {
    var meta = NATURE_META[nature] || NATURE_META.aggregated;
    return '<span class="mn-nature-badge" data-nature="' + nature + '" title="' + escapeHtml(meta.tip) + '">' + meta.label + '</span>';
  }

  /**
   * 统一的数据来源 + 更新时间悬浮说明文案。
   * @param {string} source - 数据来源描述
   * @param {string} time - 更新时间（可读字符串）
   * @returns {string} title 文案
   */
  function provenanceTitle(source, time) {
    return '数据来源：' + source + '\n更新时间：' + (time || '未知');
  }

  // 本地日期 YYYY-MM-DD（用于判断快照是否同一交易日）
  function localDateStr() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  // 从 "YYYY-MM-DD HH:MM:SS" 提取 "HH:MM:SS"
  function timeOnly(ts) {
    if (!ts) return '未知';
    var m = String(ts).match(/(\d{2}:\d{2}:\d{2}|\d{2}:\d{2})/);
    return m ? m[1] : String(ts);
  }

  function finite(value, fallback) {
    var number = Number(value);
    return Number.isFinite(number) ? number : (fallback || 0);
  }

  // 字段是否存在且为可解析数字（用于区分「真实 0 值」与「字段缺失/不可用」）
  function hasNum(value) {
    return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  }

  function money(value) {
    return '¥' + finite(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function compactAmount(value) {
    var amount = finite(value);
    if (Math.abs(amount) >= 1e12) return (amount / 1e12).toFixed(2) + '万亿';
    if (Math.abs(amount) >= 1e8) return (amount / 1e8).toFixed(2) + '亿';
    if (Math.abs(amount) >= 1e4) return (amount / 1e4).toFixed(1) + '万';
    return amount.toFixed(0);
  }

  function pct(value) {
    var number = finite(value);
    return (number > 0 ? '+' : '') + number.toFixed(2) + '%';
  }

  function tone(value) {
    return finite(value) > 0 ? 'up' : finite(value) < 0 ? 'dn' : 'flat';
  }

  function escapeHtml(text) {
    if (text == null) return '';
    return String(text).replace(/[<>&"']/g, function (c) {
      return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text == null ? '--' : String(text);
  }

  function loadAccount() {
    try {
      var parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
      if (parsed && Number.isFinite(Number(parsed.cash)) && parsed.cash >= 0 && Array.isArray(parsed.holdings) && Array.isArray(parsed.trades)) {
        parsed.initialCash = Number.isFinite(Number(parsed.initialCash)) && parsed.initialCash > 0 ? Number(parsed.initialCash) : INITIAL_CASH;
        parsed.holdings = parsed.holdings.filter(function (item) {
          return item && typeof item.code === 'string' && Number(item.qty) > 0 && Number(item.avgPrice) > 0;
        }).map(function (item) {
          return { code: item.code, name: String(item.name || item.code), qty: Math.floor(Number(item.qty)), avgPrice: Number(item.avgPrice) };
        });
        parsed.trades = parsed.trades.filter(function (item) {
          return item && typeof item.code === 'string' && Number(item.qty) > 0 && Number(item.price) > 0;
        }).slice(-100);
        parsed.cash = Number(parsed.cash);
        parsed.equityHistory = Array.isArray(parsed.equityHistory) ? parsed.equityHistory.filter(function (p) {
          return p && typeof p.d === 'string' && Number.isFinite(Number(p.v));
        }).slice(-180) : [];
        return parsed;
      }
    } catch (error) {}
    return { initialCash: INITIAL_CASH, cash: INITIAL_CASH, holdings: [], trades: [], equityHistory: [] };
  }

  var account = loadAccount();

  function saveAccount() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(account));
      return true;
    } catch (error) {
      if (typeof showToast === 'function') showToast('模拟账户', '本地存储失败，交易未保存');
      return false;
    }
  }

  function quoteMap() {
    var map = {};
    var data = dashboardState.data || {};
    (data.quotes || []).concat(data.leaders || [], data.laggards || []).forEach(function (item) { map[item.code] = item; });
    (data.indices || []).forEach(function (item) { map[item.code] = item; });
    return map;
  }

  // ------------------------------------------------------------------
  //  主脑中心：决策台 = 事实层 + 判断层 + 决策层
  //
  //  背景：大盘纵览（事实）与动态市场调查（判断）原本是两个独立工作区，
  //  但它们与决策台读的是同一份 /api/market-dashboard，卡片也大量重复。
  //  合并后只保留「决策台」一个入口，三层在同一个容器内切换，共用快照与 trace。
  // ------------------------------------------------------------------
  var hubSegment = 'decision';

  function switchHubSegment(seg) {
    if (['fact', 'survey', 'decision'].indexOf(seg) < 0) seg = 'decision';
    hubSegment = seg;
    document.querySelectorAll('[data-hub-panel]').forEach(function (panel) {
      var on = panel.getAttribute('data-hub-panel') === seg;
      panel.classList.toggle('is-active', on);
      panel.style.display = on ? 'flex' : 'none';
    });
    document.querySelectorAll('[data-hub]').forEach(function (btn) {
      var on = btn.getAttribute('data-hub') === seg;
      btn.classList.toggle('is-active', on);
      btn.setAttribute('aria-selected', String(on));
    });
    // 事实层沿用大盘纵览的布局类（移动端高度规则依赖它）
    document.body.classList.toggle('market-workspace-active', seg === 'fact');

    if (seg !== 'survey' && typeof leaveSurveyLayer === 'function') leaveSurveyLayer();

    if (seg === 'fact') {
      loadMarketDashboard(false);
      loadSentimentState(false);
      if (typeof loadGlobalAssets === 'function') loadGlobalAssets(false);
      syncMarketControls();
      requestAnimationFrame(resizeMarketDashboard);
    } else if (seg === 'survey') {
      if (typeof initSurveyLayer === 'function') initSurveyLayer();
      else if (typeof loadMarketSurveyData === 'function') loadMarketSurveyData();
    } else if (typeof initDecisionDesk === 'function') {
      initDecisionDesk();
    }
    try {
      var hash = seg === 'decision' ? '#decision' : '#' + seg;
      if (location.hash !== hash) history.replaceState(null, '', hash);
    } catch (e) { /* file:// 下忽略 */ }
  }
  window.switchHubSegment = switchHubSegment;

  function switchWorkspace(view) {
    // 旧入口兼容：大盘纵览 / 动态市场调查 已成为决策台的分层
    if (view === 'market') { switchWorkspace('decision'); switchHubSegment('fact'); return; }
    if (view === 'survey') { switchWorkspace('decision'); switchHubSegment('survey'); return; }

    var qlib = view === 'qlib';
    var decision = view === 'decision';
    var isStock = view === 'stocks' || (!qlib && !decision);

    var main = document.getElementById('main');
    var ticker = document.getElementById('ticker-tape');
    var qlibView = document.getElementById('qlib-dashboard-view');
    var decisionView = document.getElementById('decision-dashboard-view');

    if (main) main.style.display = isStock ? 'flex' : 'none';
    if (ticker) ticker.style.display = isStock ? '' : 'none';
    if (qlibView) qlibView.style.display = qlib ? 'flex' : 'none';
    if (decisionView) {
      decisionView.style.display = decision ? 'flex' : 'none';
      decisionView.classList.toggle('dd-active', decision);
    }

    document.body.classList.toggle('decision-workspace-active', decision);
    if (!decision) document.body.classList.remove('market-workspace-active');

    var stockTab = document.getElementById('workspace-stock-tab');
    var qlibTab = document.getElementById('workspace-qlib-tab');
    var decisionTab = document.getElementById('workspace-decision-tab');

    if (stockTab) { stockTab.classList.toggle('active', isStock); stockTab.setAttribute('aria-selected', String(isStock)); }
    if (qlibTab) { qlibTab.classList.toggle('active', qlib); qlibTab.setAttribute('aria-selected', String(qlib)); }
    if (decisionTab) { decisionTab.classList.toggle('active', decision); decisionTab.setAttribute('aria-selected', String(decision)); }

    if (qlib) {
      if (typeof initQlibDashboard === 'function') initQlibDashboard();
    } else if (decision) {
      switchHubSegment(hubSegment || 'decision');
    } else if (typeof resizeKline === 'function') {
      requestAnimationFrame(resizeKline);
    }
  }

  async function loadMarketDashboard(force) {
    if (dashboardState.loading) return;
    if (!force && dashboardState.data && Date.now() - dashboardState.lastLoadedAt < 30000) {
      renderMarketDashboard(dashboardState.data);
      return;
    }
    dashboardState.loading = true;
    setMarketLoading(true);
    try {
      var data = typeof fetchMarketDashboard === 'function' ? await fetchMarketDashboard() : null;
      if (!data) throw new Error('市场聚合接口暂未返回数据');
      dashboardState.data = data;
      dashboardState.lastLoadedAt = Date.now();
      if (!data.stale) dashboardState.lastSuccessAt = Date.now();
      // 手动刷新时强制重新拉取资金面体征与外部环境
      if (force) { dashboardState.capitalLastFetch = 0; dashboardState.externalLastFetch = 0; }
      renderMarketDashboard(data);
    } catch (error) {
      dashboardState.lastLoadedAt = 0;
      renderMarketError(error && error.message ? error.message : '大盘数据加载失败');
    } finally {
      dashboardState.loading = false;
      setMarketLoading(false);
    }
  }

  function setMarketLoading(loading) {
    var button = document.querySelector('.market-refresh-btn');
    if (button) {
      button.disabled = loading;
      button.innerHTML = loading ? '<span class="refresh-timer-dot" style="display:inline-block;animation:spin 1s linear infinite">↻</span> 加载中…' : '↻ 刷新';
    }
  }

  function renderMarketError(message) {
    if (dashboardState.data) {
      var data = Object.assign({}, dashboardState.data, { stale: true, warning: '刷新提示：' + message + '；展示上一轮成功快照。' });
      renderMarketDashboard(data);
      var scope = document.getElementById('market-scope');
      if (scope) scope.title = data.warning;
      return;
    }
    var grid = document.getElementById('market-index-grid');
    if (grid) {
      grid.innerHTML = '<div class="market-empty-state" style="grid-column:1/-1;padding:24px;text-align:center;background:rgba(255,255,255,0.02);border-radius:10px">' +
        '<strong style="color:var(--text);font-size:14px">大盘行情数据连接中</strong>' +
        '<p style="color:var(--text2);font-size:12px;margin:8px 0">' + escapeHtml(message) + '</p>' +
        '<button class="pagination-btn active" onclick="loadMarketDashboard(true)" style="padding:5px 14px;cursor:pointer">重新连接</button>' +
        '</div>';
    }
  }

  function renderMarketDashboard(data) {
    var scope = document.getElementById('market-scope');
    var updated = document.getElementById('market-updated');
    var quality = data.data_quality || {};
    var qualityText = quality.level === 'good' ? '数据质量优' : quality.level === 'partial' ? '部分样本' : '数据降级';
    var sourceStr = data.source || '东方财富 / 腾讯财经聚合';

    if (scope) {
      scope.innerHTML = '<span class="workspace-live-dot" style="display:inline-block;margin-right:4px"></span>' +
        escapeHtml(data.scope || '全市场A股核心指数与样本池') + ' &bull; 覆盖 ' +
        finite(data.coverage && data.coverage.valid) + '/' + finite(data.coverage && data.coverage.configured) +
        ' &bull; <b style="color:var(--cyan)">' + qualityText + '</b> &bull; 来源: <b style="color:var(--text)">' + escapeHtml(sourceStr) + '</b>';
    }

    var stamp = data.as_of ? new Date(data.as_of).toLocaleTimeString('zh-CN', { hour12: false }) : new Date().toLocaleTimeString();
    var staleAge = data.stale && data.as_of ? Math.max(0, Math.floor((Date.now() - new Date(data.as_of).getTime()) / 60000)) : 0;
    if (updated) {
      updated.innerHTML = (data.stale ? '<span style="color:var(--gold)">[快照 + ' + staleAge + 'm]</span> ' : '') + '⏱️ ' + stamp;
    }

    var notice = document.getElementById('market-data-quality-notice');
    if (notice) {
      var show = data.stale || quality.level === 'partial' || quality.level === 'poor';
      notice.hidden = !show;
      notice.textContent = (data.stale ? '当前为旧行情快照。 ' : '') + (quality.message || '') + (data.warning ? ' ' + data.warning : '');
    }

    // ===== v18 信息优先级分层：结论 → 三块体征 → 指数趋势 → 账户 =====
    // 数据性质徽章：基准数据集 (indices 直采腾讯=真实；样本池聚合=聚合口径；stale 降级=历史快照)
    setNature('market-vital-badge-index', 'real');
    setNature('market-vital-badge-sentiment', data.stale ? 'snapshot' : 'aggregated');
    setNature('market-vital-badge-breadth', data.stale ? 'snapshot' : 'aggregated');
    setNature('market-account-badge', 'local');

    renderMarketConclusion(data);
    renderIndices(data.indices || []);
    renderIndexChart(data.index_trends || []);
    renderSentiment(data);
    // 先发起资金面取数（同步置 loading），再渲染 → 首次绘制显示骨架而非「暂不可用」
    loadCapitalVitals(false);
    renderCapitalVitals(data);
    renderMarketKpis(data);
    renderSectorHeatmap(data.sectors || []);
    renderMarketMovers();
    renderCapitalAndRisk(data);
    populateTradeSymbols(data);
    renderMarketAccount();
    updateMarketFilterControls();

    // 外部环境为独立数据源（全球联动），异步补齐
    loadExternalEnv(false);

    renderSentimentFlow();
    renderMarketAnchors();

    // 从看板/侧栏带过来的板块：先按名称解析出 sector.key 再筛选
    // （榜单是按 item.sector_keys 过滤的，直接塞进 query 会按股票名搜索 → 必然空列表）
    if (dashboardState.pendingSector) {
      var wanted = dashboardState.pendingSector;
      var sectors = data.sectors || [];
      var hit = sectors.filter(function (s) {
        return s && (s.key === wanted || s.name === wanted);
      })[0] || sectors.filter(function (s) {
        return s && ((s.name || '').indexOf(wanted) >= 0 || wanted.indexOf(s.name || '') >= 0);
      })[0];
      if (hit) {
        dashboardState.filters.sector = hit.key;
        renderSectorHeatmap(sectors);
      } else {
        dashboardState.filters.query = wanted;
        var searchEl = document.getElementById('market-symbol-search');
        if (searchEl) searchEl.value = wanted;
      }
      dashboardState.pendingSector = '';
      updateMarketFilterControls();
      renderMarketMovers();
    }

    var disclaimer = document.getElementById('market-disclaimer');
    if (disclaimer) {
      disclaimer.textContent = data.disclaimer || '市场统计基于全市场核心样本池与权威交易所接口，行情数据具备毫秒级时效性，不构成买卖投资建议。';
    }
  }

  function renderIndices(indices) {
    var grid = document.getElementById('market-index-grid');
    if (!grid) return;

    if (!indices || !indices.length) {
      grid.innerHTML = '<div class="market-empty-state">暂无指数数据</div>';
      return;
    }

    // 溯源：指数为腾讯财经实时直采，携带更新时间
    var d = dashboardState.data || {};
    var srcName = d.source === 'tencent' ? '腾讯财经实时行情' : (d.source || '聚合行情');
    var stamp = d.as_of ? new Date(d.as_of).toLocaleString('zh-CN', { hour12: false }) : '未知';
    var provenance = provenanceTitle(srcName, stamp);

    grid.innerHTML = indices.map(function (item) {
      var cls = tone(item.chg);
      var isFocused = dashboardState.focusedIndexCode === item.code;
      var arrow = item.chg > 0 ? '▲ ' : item.chg < 0 ? '▼ ' : '';
      var amtStr = item.amount > 0 ? compactAmount(item.amount) : '--';
      var cleanCode = String(item.code || '').replace(/sh|sz|bj|hk|us/g, '');

      return '<article class="market-index-card ' + cls + (isFocused ? ' focused' : '') + '" onclick="focusIndexChart(\'' + escapeHtml(item.code) + '\')" title="' + escapeHtml(provenance) + '｜点击聚焦分时趋势" style="cursor:pointer">' +
        '<div>' +
          '<span class="idx-title"><strong>' + escapeHtml(item.name || item.code) + '</strong><small class="idx-code-pill">' + escapeHtml(cleanCode) + '</small></span>' +
          '<small style="color:var(--text2);font-size:10px">成交额 ' + amtStr + '</small>' +
        '</div>' +
        '<strong class="idx-val" title="' + escapeHtml(provenance) + '">' + (item.price > 0 ? finite(item.price).toFixed(2) : '--') + '</strong>' +
        '<em class="idx-chg-badge ' + cls + '" title="' + escapeHtml(provenance) + '">' + (item.price > 0 ? arrow + pct(item.chg) : '暂无行情') + '</em>' +
        '</article>';
    }).join('');
  }

  function focusIndexChart(code) {
    dashboardState.focusedIndexCode = (dashboardState.focusedIndexCode === code) ? null : code;
    renderIndices((dashboardState.data && dashboardState.data.indices) || []);
    renderIndexChart((dashboardState.data && dashboardState.data.index_trends) || []);
  }

  function renderIndexChart(trends) {
    var element = document.getElementById('market-index-chart');
    if (!element || typeof echarts === 'undefined') return;

    if (!dashboardState.chart || dashboardState.chart.isDisposed()) {
      dashboardState.chart = echarts.init(element, null, { renderer: 'canvas' });
    }

    var usable = trends.filter(function (series) {
      return Array.isArray(series.points) && series.points.length > 1;
    });

    if (dashboardState.focusedIndexCode) {
      var single = usable.filter(function (s) { return s.code === dashboardState.focusedIndexCode; });
      if (single.length > 0) usable = single;
    }

    if (!usable.length) {
      dashboardState.chart.setOption({
        graphic: { type: 'text', left: 'center', top: 'middle', style: { text: '暂无指数分时趋势数据', fill: '#7183a6', fontSize: 12 } }
      }, true);
      return;
    }

    var colorPalette = {
      'sh000001': '#ff4d6a', // 上证红
      'sz399001': '#3b82f6', // 深成蓝
      'sz399006': '#00e96a', // 创业绿
      'sh000688': '#a855f7', // 科创紫
      'sh000300': '#06b6d4', // 沪深300青
      'sh000905': '#f59e0b', // 中证500金
      'sh000852': '#ec4899', // 中证1000粉
      'hkHSI': '#14b8a6'     // 恒生绿青
    };

    var fallbackColors = ['#ff4d6a', '#3b82f6', '#00e96a', '#a855f7', '#06b6d4', '#f59e0b', '#ec4899'];
    var windowSize = dashboardState.trendWindow === 'all' ? Infinity : Number(dashboardState.trendWindow) || 48;
    var sliced = usable.map(function (item) {
      return Object.assign({}, item, { points: Number.isFinite(windowSize) ? item.points.slice(-windowSize) : item.points.slice() });
    });

    var timeSet = {};
    sliced.forEach(function (item) {
      item.points.forEach(function (point) { if (point.time) timeSet[String(point.time)] = true; });
    });
    var rawTimes = Object.keys(timeSet).sort();
    if (rawTimes.length > 500) rawTimes = rawTimes.slice(-500);
    var maxPoints = rawTimes.length;

    function normalizeTime(value) {
      var raw = String(value || '');
      if (/^\d{12}$/.test(raw)) return raw.slice(0, 8) + '-' + raw.slice(8, 10) + ':' + raw.slice(10, 12);
      if (/^\d{8}$/.test(raw)) return raw.slice(0, 4) + '-' + raw.slice(4, 6) + '-' + raw.slice(6, 8);
      return raw.replace('T', ' ').slice(0, 16);
    }

    var times = rawTimes.map(function (raw) {
      return /^\d{12}$/.test(raw) ? raw.slice(8, 10) + ':' + raw.slice(10, 12) :
             /^\d{8}$/.test(raw) ? raw.slice(4, 6) + '-' + raw.slice(6, 8) :
             raw.replace('T', ' ').slice(5, 16);
    });

    var timeIndex = {};
    rawTimes.forEach(function (raw, index) { timeIndex[normalizeTime(raw)] = index; });

    var isPercent = dashboardState.trendMode === 'percent';
    var series = sliced.map(function (item, index) {
      var basePoint = item.points.find(function (point) { return point && finite(point.close) > 0; });
      var base = finite(basePoint && basePoint.close, 1);
      var data = Array(maxPoints).fill(null);
      item.points.forEach(function (point) {
        var idx = timeIndex[normalizeTime(point.time)];
        if (idx == null) return;
        var close = finite(point.close);
        data[idx] = isPercent ? (base ? ((close / base - 1) * 100) : 0) : close;
      });

      var sColor = colorPalette[item.code] || fallbackColors[index % fallbackColors.length];

      return {
        name: item.name + (item.granularity === '日线降级' ? ' · 日线' : ''),
        type: 'line',
        smooth: 0.35,
        showSymbol: false,
        connectNulls: true,
        data: data,
        lineStyle: { width: index === 0 ? 2.5 : 2.0, color: sColor },
        itemStyle: { color: sColor },
        areaStyle: (index === 0 && sliced.length <= 2) ? {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: sColor + '30' },
            { offset: 1, color: sColor + '00' }
          ])
        } : undefined,
        emphasis: { focus: 'series', lineStyle: { width: 3.5 } }
      };
    });

    var textColor = '#8b9ec2';
    var caption = document.getElementById('market-trend-caption');
    if (caption) {
      caption.innerHTML = (dashboardState.focusedIndexCode ? '<b>聚焦: ' + (usable[0] && usable[0].name) + '</b>' : '多指数走势联动') +
        ' &bull; ' + maxPoints + ' 点 &bull; ' + (isPercent ? '基准涨跌幅 (首点归一化)' : '指数真实点位');
    }

    var yAxis = isPercent ? {
      type: 'value',
      scale: true,
      axisLabel: {
        color: textColor,
        fontSize: 10,
        formatter: function (v) { return (v > 0 ? '+' : '') + v.toFixed(2) + '%'; }
      },
      splitLine: { lineStyle: { color: 'rgba(80,140,255,0.07)', type: 'dashed' } }
    } : {
      type: 'value',
      scale: true,
      axisLabel: { color: textColor, fontSize: 10, formatter: function (v) { return finite(v).toFixed(0); } },
      splitLine: { lineStyle: { color: 'rgba(80,140,255,0.07)', type: 'dashed' } }
    };

    var timeline = dashboardState.data && dashboardState.data.as_of ? new Date(dashboardState.data.as_of).toLocaleTimeString('zh-CN', { hour12: false }) : '';

    dashboardState.chart.setOption({
      animationDuration: 300,
      animationEasing: 'cubicOut',
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        confine: true,
        backgroundColor: 'rgba(7, 13, 29, 0.95)',
        borderColor: 'rgba(59, 130, 246, 0.35)',
        borderWidth: 1,
        padding: [8, 12],
        textStyle: { color: '#eaf0ff', fontSize: 11 },
        axisPointer: {
          type: 'cross',
          crossStyle: { color: 'rgba(130, 165, 220, 0.4)' },
          lineStyle: { color: 'rgba(130, 165, 220, 0.4)', type: 'dashed' }
        },
        formatter: function (params) {
          var list = Array.isArray(params) ? params : [];
          var idx = list.length ? list[0].dataIndex : 0;
          var lines = ['<div style="font-weight:700;color:var(--cyan);margin-bottom:4px">⏱️ ' + escapeHtml(times[idx] || '') + '</div>'];
          list.forEach(function (item) {
            if (item.value == null) return;
            var valStr = isPercent ? ((finite(item.value) > 0 ? '+' : '') + finite(item.value).toFixed(2) + '%') : finite(item.value).toFixed(2);
            var colorDot = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + item.color + ';margin-right:6px"></span>';
            lines.push(colorDot + escapeHtml(item.seriesName) + ': <b style="font-family:\'SF Mono\',monospace;margin-left:6px">' + valStr + '</b>');
          });
          return lines.join('<br>');
        }
      },
      legend: {
        top: 2,
        right: 8,
        itemWidth: 14,
        itemHeight: 8,
        textStyle: { color: textColor, fontSize: 10 },
        selectedMode: true
      },
      grid: { left: 55, right: 25, top: 38, bottom: 42 },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: times,
        axisLabel: { color: textColor, fontSize: 9.5, hideOverlap: true },
        axisLine: { lineStyle: { color: 'rgba(128,148,184,0.18)' } },
        axisTick: { show: false }
      },
      yAxis: yAxis,
      dataZoom: [
        { type: 'inside', xAxisIndex: 0, start: 0, end: 100 },
        {
          type: 'slider',
          xAxisIndex: 0,
          bottom: 4,
          height: 14,
          start: 0,
          end: 100,
          showDetail: false,
          brushSelect: false,
          borderColor: 'rgba(128,148,184,0.15)',
          backgroundColor: 'rgba(128,148,184,0.04)',
          fillerColor: 'rgba(59,130,246,0.18)',
          handleStyle: { color: '#3b82f6' }
        }
      ],
      series: series
    }, true);
  }

  // ==================================================================
  //  统一情绪口径（sentiment.v1）+ 情绪追踪（sentiment-flow.v1）
  //
  //  为什么在这里做：原先「大盘纵览」的情绪分来自 dashboard.judgment，
  //  而决策台来自 /api/decision/sentiment —— 同一个系统里两个「情绪分」，
  //  口径不同、时点不同。合并后只保留一个口径：
  //    · 分数/状态：sentiment.v1（六维加权，缺失维重归一）
  //    · 走向/拐点/驱动：sentiment-flow.v1（本页新增）
  //    · dashboard.judgment 仅在统一接口不可用时兜底，并在 tooltip 注明「回退」
  // ==================================================================
  var sentimentState = { data: null, flow: null, kline: null, loading: false, lastAt: 0, error: '' };

  var SENT_DIM_ORDER = [
    ['breadth', '宽度'], ['momentum', '动量'], ['limit', '涨跌停'],
    ['volume', '量能'], ['global', '全球'], ['event', '事件']
  ];

  function signed(value, digits) {
    var v = Number(value);
    if (!isFinite(v)) return '--';
    var d = digits == null ? 1 : digits;
    return (v > 0 ? '+' : '') + v.toFixed(d);
  }

  function jsonFetch(url, ms) {
    return new Promise(function (resolve, reject) {
      var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
      var timer = setTimeout(function () {
        if (ctrl) { try { ctrl.abort(); } catch (e) {} }
        reject(new Error('timeout'));
      }, ms || 12000);
      var options = { cache: 'no-store' };
      if (ctrl) options.signal = ctrl.signal;
      fetch(url, options)
        .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(function (json) { clearTimeout(timer); resolve(json); })
        .catch(function (err) { clearTimeout(timer); reject(err); });
    });
  }

  async function loadSentimentState(force) {
    if (sentimentState.loading) return;
    if (!force && sentimentState.data && Date.now() - sentimentState.lastAt < 45000) {
      renderSentiment(dashboardState.data || {});
      renderSentimentFlow();
      renderMarketAnchors();
      return;
    }
    sentimentState.loading = true;
    var q = force ? '?refresh=true' : '';
    try {
      var results = await Promise.all([
        jsonFetch('/api/decision/sentiment' + q, 15000),
        jsonFetch('/api/decision/sentiment/flow' + q, 15000),
        // 均线锚需要真实日线：上证指数近 30 个交易日（接口最小 limit=30）
        jsonFetch('/api/kline?code=sh000001&period=day&limit=30', 15000).catch(function () { return null; })
      ]);
      sentimentState.data = results[0] || null;
      sentimentState.flow = results[1] || null;
      sentimentState.kline = (results[2] && results[2].data && results[2].data.klines) || null;
      sentimentState.lastAt = Date.now();
      sentimentState.error = '';
    } catch (error) {
      sentimentState.error = (error && error.message) || '情绪接口不可用';
    } finally {
      sentimentState.loading = false;
    }
    // 情绪分回来后要重刷结论条：它比大盘数据晚到，否则同屏会出现两个分数
    renderMarketConclusion(dashboardState.data || {});
    renderSentiment(dashboardState.data || {});
    renderSentimentFlow();
    renderMarketAnchors();
  }

  function renderSentDims(dims) {
    var host = document.getElementById('market-sent-dims');
    if (!host) return;
    host.innerHTML = SENT_DIM_ORDER.map(function (pair) {
      var key = pair[0], label = pair[1];
      var item = (dims || {})[key];
      var value = item && item.available && item.value != null ? finite(item.value) : null;
      var missing = value == null;
      var width = missing ? 0 : Math.max(2, Math.min(100, value));
      return '<span class="mo-dim' + (missing ? ' is-missing' : '') + '">' +
        '<span>' + label + '</span>' +
        '<b>' + (missing ? '--' : Math.round(value)) + '</b>' +
        '<i style="--w:' + width + '%"></i>' +
        '</span>';
    }).join('');
  }

  function renderSentimentFlow() {
    var flow = sentimentState.flow || {};
    var ok = !!(flow && flow.ok && flow.level != null);
    var tag = document.getElementById('market-flow-tag');
    var note = document.getElementById('market-flow-note');
    var metrics = document.getElementById('market-flow-metrics');
    var drivers = document.getElementById('market-flow-drivers');
    if (!metrics || !drivers) return;

    if (!ok) {
      if (tag) { tag.textContent = '追踪不可用'; tag.setAttribute('data-tone', 'muted'); }
      if (note) note.textContent = sentimentState.error || flow.message || '情绪追踪暂不可用';
      metrics.innerHTML = '';
      drivers.innerHTML = '';
      return;
    }

    var regime = flow.regime || {};
    var turning = flow.turning_point || {};
    if (tag) {
      tag.textContent = regime.label || '--';
      tag.setAttribute('data-tone', regime.tone || 'neutral');
    }
    var persistenceEarly = flow.persistence || {};
    if (note) {
      // 这里只说「方向性事实」，不复述大字结论（否则卡片里同一句话出现两次）
      var bits = [];
      if (turning.detected) bits.push(turning.note);
      if (persistenceEarly.ticks) {
        bits.push('连续 ' + persistenceEarly.ticks + ' 次'
          + (persistenceEarly.direction === 'up' ? '上行' : persistenceEarly.direction === 'down' ? '下行' : '同向'));
      }
      if (!bits.length) bits.push('无连续同向变化');
      note.textContent = bits.join(' · ');
    }

    var persistence = flow.persistence || {};
    var dirText = persistence.ticks
      ? (persistence.direction === 'up' ? '连续上行' : persistence.direction === 'down' ? '连续下行' : '方向不明')
      : '无连续同向';
    var items = [
      ['近端变化', signed(flow.delta_short), flow.delta_short > 0 ? 'up' : flow.delta_short < 0 ? 'down' : '', '较上一次采样'],
      ['斜率/时', signed(flow.slope_per_hour, 2), flow.slope_per_hour > 0 ? 'up' : flow.slope_per_hour < 0 ? 'down' : '', '近 6 点最小二乘'],
      ['近20分位', flow.percentile20 == null ? '--' : flow.percentile20 + '%', '', 'z=' + (flow.z20 == null ? '--' : signed(flow.z20, 2))],
      ['持续性', persistence.ticks ? persistence.ticks + ' 次' : '—', '', dirText]
    ];
    metrics.innerHTML = items.map(function (it) {
      return '<div class="mo-flow-metric" title="' + escapeHtml(it[3]) + '">' +
        '<span>' + escapeHtml(it[0]) + '</span>' +
        '<b class="' + it[2] + '">' + escapeHtml(String(it[1])) + '</b>' +
        '</div>';
    }).join('');

    var rows = [];
    (flow.drivers || []).slice(0, 3).forEach(function (d) { rows.push({ label: d.label, value: finite(d.contribution), drag: false }); });
    (flow.drags || []).slice(0, 2).forEach(function (d) { rows.push({ label: d.label, value: finite(d.contribution), drag: true }); });
    if (!rows.length) {
      drivers.innerHTML = '<div class="mo-flow-driver"><em>归因</em><i style="--w:0%"></i><b>--</b></div>';
      return;
    }
    var maxAbs = Math.max.apply(null, rows.map(function (r) { return Math.abs(r.value); }).concat([0.001]));
    drivers.innerHTML = rows.map(function (r) {
      var width = Math.max(4, Math.abs(r.value) / maxAbs * 100);
      return '<div class="mo-flow-driver' + (r.drag ? ' is-drag' : '') + '" title="' +
        escapeHtml(r.label + ' 对情绪分的贡献 ' + signed(r.value, 2)) + '">' +
        '<em>' + escapeHtml(r.label) + '</em>' +
        '<i style="--w:' + width.toFixed(1) + '%"></i>' +
        '<b>' + signed(r.value) + '</b>' +
        '</div>';
    }).join('');
  }

  function renderMarketAnchors() {
    var host = document.getElementById('market-anchors');
    if (!host) return;
    var data = dashboardState.data || {};
    var amountYi = finite((data.turnover || {}).amount) / 1e8;
    var anchors = [];

    if (!(amountYi > 0)) {
      anchors.push({ name: '量能锚', state: 'warn', text: '未知', desc: '上游未返回成交额' });
    } else if (amountYi >= 9000) {
      anchors.push({ name: '量能锚', state: 'pass', text: '充足', desc: '两市成交 ' + amountYi.toFixed(0) + ' 亿，交投活跃' });
    } else if (amountYi >= 6000) {
      anchors.push({ name: '量能锚', state: 'warn', text: '一般', desc: '两市成交 ' + amountYi.toFixed(0) + ' 亿' });
    } else {
      anchors.push({ name: '量能锚', state: 'fail', text: '萎缩', desc: '两市成交仅 ' + amountYi.toFixed(0) + ' 亿' });
    }

    var closes = (sentimentState.kline || []).map(function (k) { return parseFloat(k && k.c); })
      .filter(function (v) { return isFinite(v) && v > 0; });
    if (closes.length < 5) {
      anchors.push({ name: '均线锚', state: 'warn', text: '未知', desc: closes.length ? '日线不足 5 根' : '日线未取到，均线锚不可判' });
    } else {
      var last5 = closes.slice(-5);
      var ma5 = last5.reduce(function (a, b) { return a + b; }, 0) / 5;
      var last = closes[closes.length - 1];
      var diff = (last / ma5 - 1) * 100;
      anchors.push({
        name: '均线锚',
        state: last >= ma5 ? 'pass' : 'fail',
        text: last >= ma5 ? '站上' : '跌破',
        desc: '上证 ' + last.toFixed(0) + ' vs MA5 ' + ma5.toFixed(0) + '（' + signed(diff, 2) + '%）'
      });
    }

    var flow = sentimentState.flow || {};
    var level = flow.ok ? finite(flow.level) : null;
    if (level == null) {
      anchors.push({ name: '情绪锚', state: 'warn', text: '未知', desc: '统一情绪分不可用' });
    } else if (level >= 58) {
      anchors.push({ name: '情绪锚', state: 'pass', text: '偏多', desc: level.toFixed(1) + ' 分 · ' + ((flow.regime || {}).label || '--') });
    } else if (level <= 42) {
      anchors.push({ name: '情绪锚', state: 'fail', text: '偏空', desc: level.toFixed(1) + ' 分 · ' + ((flow.regime || {}).label || '--') });
    } else {
      anchors.push({ name: '情绪锚', state: 'warn', text: '平衡', desc: level.toFixed(1) + ' 分 · ' + ((flow.regime || {}).label || '--') });
    }

    host.innerHTML = anchors.map(function (a) {
      return '<div class="mo-anchor" data-state="' + a.state + '">' +
        '<span class="mo-anchor-dot"></span>' +
        '<div class="mo-anchor-body"><strong>' + a.name + '</strong><span>' + escapeHtml(a.desc) + '</span></div>' +
        '<b>' + escapeHtml(a.text) + '</b>' +
        '</div>';
    }).join('');
  }

  function renderSentiment(data) {
    var breadth = data.breadth || {};
    var judgment = data.judgment || {};
    var total = Math.max(1, finite(breadth.up) + finite(breadth.down) + finite(breadth.flat));
    var upPct = finite(breadth.up) / total * 100;
    var downPct = finite(breadth.down) / total * 100;
    var flatPct = Math.max(0, 100 - upPct - downPct);

    // 统一口径优先：sentiment.v1 是唯一「情绪分」来源；
    // dashboard.judgment 只在统一接口不可用时兜底（并在溯源里注明「回退」）。
    // 分数与档位必须「同一次取数」：flow 自带 level+band+regime，
    // 若分数取 sentiment、档位取 flow，两次请求跨秒就会打架（80 分显示「偏热」但追踪说「过热」）。
    var flowState = sentimentState.flow || {};
    var flowOk = !!(flowState.ok && flowState.level != null);
    var uni = sentimentState.data;
    var uniOk = !!(uni && uni.ok && uni.score != null);
    var useUni = flowOk || uniOk;
    var score;
    if (useUni) {
      score = Math.round(finite(flowOk ? flowState.level : uni.score));
      var bandLabel = flowOk
        ? ((flowState.band || {}).label || flowState.status || '--')
        : (uni.status || '--');
      var regimeLabel = flowOk
        ? ((flowState.regime || {}).label || bandLabel)
        : bandLabel;
      var uniDims = (uni && uni.dimensions) || {};
      var availN = SENT_DIM_ORDER.filter(function (p) {
        var d = uniDims[p[0]];
        return d && d.available && d.value != null;
      }).length;
      var scopeText = (uni && uni.data_scope === 'full_a_share') ? '全市场口径' : '核心样本口径';
      var riskText = ((uni && uni.risk_flags) || []).slice(0, 1).map(function (f) { return f.text; });
      setText('market-gauge-score', score);
      // 徽章=档位（偏热/过热…）；大字=可读结论（走向说明），避免同一个词连说三遍
      var noteText = flowOk ? ((flowState.regime || {}).note || '') : '';
      setText('market-regime', bandLabel);
      setText('market-judgment', noteText || regimeLabel);
      setText('market-judgment-text', ['分档 ' + bandLabel + ' · 六维可用 ' + availN + '/6 · 覆盖 '
        + Math.round(finite(uni && uni.coverage) * 100) + '%', scopeText].concat(riskText).join(' · '));
    } else {
      score = judgment.score != null ? Math.round(finite(judgment.score)) : 50;
      setText('market-gauge-score', judgment.score == null ? '--' : score);
      setText('market-regime', judgment.regime || '--');
      setText('market-judgment', judgment.regime || '市场研判');
      setText('market-judgment-text', [judgment.text, (data.data_quality || {}).message].filter(Boolean).join(' '));
    }

    // 溯源：写清「分数是谁算的」，回退时明确标注
    var sentStamp = data.as_of ? new Date(data.as_of).toLocaleString('zh-CN', { hour12: false }) : '未知';
    var sentSrc = useUni
      ? 'sentiment.v1 六维加权（统一情绪口径）' + (sentimentState.flow && sentimentState.flow.ok ? ' + sentiment-flow.v1 追踪' : '')
      : (data.source === 'tencent' ? '腾讯财经行情' : (data.source || '聚合行情')) + ' → 系统样本池广度评分（统一接口不可用，回退）';
    var gaugeScoreEl = document.getElementById('market-gauge-score');
    if (gaugeScoreEl) gaugeScoreEl.title = provenanceTitle(sentSrc, sentStamp);
    var gaugeRingEl = document.getElementById('market-gauge-ring');
    if (gaugeRingEl) gaugeRingEl.title = provenanceTitle(sentSrc, sentStamp);
    var regimeEl = document.getElementById('market-regime');
    if (regimeEl) regimeEl.title = provenanceTitle(sentSrc, sentStamp);

    var ring = document.getElementById('market-gauge-ring');
    if (ring) {
      ring.style.setProperty('--score-angle', Math.round(score * 3.6) + 'deg');
    }
    renderSentDims(useUni ? (uni.dimensions || {}) : {});

    var up = document.getElementById('market-up-bar'); if (up) up.style.width = upPct + '%';
    var flat = document.getElementById('market-flat-bar'); if (flat) flat.style.width = flatPct + '%';
    var down = document.getElementById('market-down-bar'); if (down) down.style.width = downPct + '%';

    var legend = document.getElementById('market-breadth-legend');
    if (legend) {
      legend.innerHTML = '<button type="button" class="up" data-market-bucket="up" aria-pressed="' + (dashboardState.filters.bucket === 'up') + '" onclick="setMarketBucketFilter(\'up\')">🟢 上涨 ' + finite(breadth.up) + ' (' + upPct.toFixed(1) + '%)</button>' +
        '<button type="button" data-market-bucket="flat" aria-pressed="' + (dashboardState.filters.bucket === 'flat') + '" onclick="setMarketBucketFilter(\'flat\')">⚪ 平盘 ' + finite(breadth.flat) + '</button>' +
        '<button type="button" class="dn" data-market-bucket="down" aria-pressed="' + (dashboardState.filters.bucket === 'down') + '" onclick="setMarketBucketFilter(\'down\')">🔴 下跌 ' + finite(breadth.down) + ' (' + downPct.toFixed(1) + '%)</button>';
    }

    var bins = Array.isArray(breadth.distribution) ? breadth.distribution : [];
    var distribution = document.getElementById('market-distribution');
    if (distribution) {
      var maxCount = Math.max.apply(null, [1].concat(bins.map(function (item) { return finite(item.count); })));
      var bucketKeys = ['down_gt5', 'down_2_5', 'down_0_2', 'flat', 'up_0_2', 'up_2_5', 'up_gt5'];
      distribution.innerHTML = bins.map(function (item, index) {
        var count = finite(item.count);
        var color = item.label.indexOf('跌') >= 0 ? 'down' : item.label.indexOf('涨') >= 0 ? 'up' : 'flat';
        var key = bucketKeys[index] || '';
        var active = dashboardState.filters.bucket === key;
        var barWidth = Math.max(3, count / maxCount * 100);
        return '<button type="button" class="market-dist-row ' + (active ? 'active' : '') + '" data-market-bucket="' + key + '" aria-pressed="' + active + '" onclick="setMarketBucketFilter(\'' + key + '\')" title="点击筛选此区间标的">' +
          '<span>' + escapeHtml(item.label) + '</span>' +
          '<i><b class="' + color + '" style="width:' + barWidth + '%"></b></i>' +
          '<strong>' + count + '</strong>' +
          '</button>';
      }).join('') || '<span class="muted">暂无分布数据</span>';
    }
  }

  function renderMarketKpis(data) {
    var breadth = data.breadth || {};
    var turnover = data.turnover || {};
    var coverage = data.coverage || {};
    var coveragePct = finite(coverage.configured) > 0 ? (finite(coverage.valid) / finite(coverage.configured) * 100) : 100;

    var items = [
      ['全市场上涨占比', finite(breadth.up_ratio).toFixed(1) + '%', finite(breadth.up_ratio) >= 50 ? 'up' : 'dn', '中位涨幅 ' + pct(breadth.median_chg) + ' · ' + finite(breadth.up) + ' 股上涨'],
      ['涨停 / 跌停家数', finite(breadth.limit_up) + ' / ' + finite(breadth.limit_down), finite(breadth.limit_up) >= finite(breadth.limit_down) ? 'up' : 'dn', '连板投机情绪良好'],
      ['两市样本成交额', compactAmount(turnover.amount), '', '平均换手率 ' + finite(turnover.avg_turnover).toFixed(2) + '%'],
      ['行情覆盖度', coveragePct.toFixed(1) + '%', coveragePct >= 95 ? 'up' : 'dn', finite(coverage.valid) + ' 只核心标的实时跟踪'],
      ['综合情绪状态', escapeHtml((data.judgment || {}).regime || '--'), '', '情绪评分 ' + ((data.judgment || {}).score == null ? '50' : Math.round(finite((data.judgment || {}).score))) + ' 分']
    ];

    var grid = document.getElementById('market-kpi-grid');
    if (grid) {
      grid.innerHTML = items.map(function (item) {
        return '<article class="market-kpi">' +
          '<span>' + item[0] + '</span>' +
          '<strong class="' + item[2] + '">' + item[1] + '</strong>' +
          '<small>' + item[3] + '</small>' +
          '</article>';
      }).join('');
    }
  }

  function renderSectorHeatmap(sectors) {
    var element = document.getElementById('market-sector-heatmap');
    if (!element) return;

    var key = dashboardState.sectorSort || 'avg_chg';
    var ordered = sectors.slice().sort(function (a, b) { return finite(b[key]) - finite(a[key]); });
    var caption = document.getElementById('market-sector-caption');
    if (caption) {
      caption.textContent = key === 'up_ratio' ? '按样本上涨占比排序' : key === 'amount' ? '按板块成交额排序' : '按行业平均涨跌幅排序';
    }

    element.innerHTML = ordered.slice(0, 24).map(function (sector) {
      var metric = finite(sector[key]);
      var isSelected = dashboardState.filters.sector === sector.key;
      // 上涨占比独立配色（红系=高上涨占比），与「成交额」的 heat-* 解耦：
      // 避免「上涨占比≥65%」显示绿底被误读为"下跌"（A 股惯例：涨=红）
      var cls = key === 'avg_chg' ? tone(metric) : (key === 'up_ratio' ? (metric >= 65 ? 'ratio-strong' : metric >= 50 ? 'ratio-mid' : 'ratio-weak') : (metric >= 500000000 ? 'heat-strong' : metric >= 100000000 ? 'heat-positive' : 'neutral'));
      var metricLabel = key === 'avg_chg' ? pct(metric) : key === 'up_ratio' ? metric.toFixed(1) + '% 上涨' : compactAmount(metric);
      var leaderText = sector.leader ? (sector.leader.name + ' ' + pct(sector.leader.chg)) : '--';
      var encodedKey = encodeURIComponent(sector.key || '').replace(/'/g, '%27');

      return '<button class="market-sector-tile ' + cls + (isSelected ? ' selected' : '') + '" aria-pressed="' + isSelected + '" onclick="setMarketSectorFilter(decodeURIComponent(\'' + encodedKey + '\'))" title="点击联动筛选">' +
        '<span>' + escapeHtml(sector.icon || '') + ' ' + escapeHtml(sector.name || '') + '</span>' +
        '<strong>' + metricLabel + '</strong>' +
        '<small>领涨: ' + escapeHtml(leaderText) + '</small>' +
        '</button>';
    }).join('') || '<div class="market-empty-state">暂无板块数据</div>';
  }

  function switchMarketList(kind) {
    dashboardState.activeList = kind === 'laggards' ? 'laggards' : 'leaders';
    document.querySelectorAll('[data-market-list]').forEach(function (button) {
      var active = button.getAttribute('data-market-list') === dashboardState.activeList;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    renderMarketMovers();
  }

  function setMarketSectorFilter(key) {
    dashboardState.filters.sector = dashboardState.filters.sector === key ? '' : String(key || '');
    updateMarketFilterControls();
    renderSectorHeatmap((dashboardState.data || {}).sectors || []);
    renderMarketMovers();
  }

  function setMarketBucketFilter(key) {
    dashboardState.filters.bucket = dashboardState.filters.bucket === key ? '' : String(key || '');
    updateMarketFilterControls();
    renderSentiment(dashboardState.data || {});
    renderMarketMovers();
  }

  function clearMarketFilters() {
    dashboardState.filters = { sector: '', bucket: '', query: '' };
    var input = document.getElementById('market-symbol-search');
    if (input) input.value = '';
    updateMarketFilterControls();
    renderSectorHeatmap((dashboardState.data || {}).sectors || []);
    renderSentiment(dashboardState.data || {});
    renderMarketMovers();
  }

  function setMarketMoverSort(sort) {
    dashboardState.moverSort = ['chg', 'amount', 'turnover'].indexOf(sort) >= 0 ? sort : 'chg';
    saveViewPreference();
    renderMarketMovers();
  }

  function updateMarketFilterControls() {
    var filters = dashboardState.filters;
    var active = !!(filters.sector || filters.bucket || filters.query);
    var clear = document.getElementById('market-filter-clear');
    if (clear) clear.disabled = !active;

    var summary = document.getElementById('market-filter-summary');
    if (summary) {
      var sector = (dashboardState.data && dashboardState.data.sectors || []).find(function (item) { return item.key === filters.sector; });
      var labels = { down_gt5: '跌超5%', down_2_5: '跌2–5%', down_0_2: '跌0–2%', flat: '平盘', up_0_2: '涨0–2%', up_2_5: '涨2–5%', up_gt5: '涨超5%' };
      summary.textContent = [sector && sector.name, labels[filters.bucket], filters.query ? '搜索: ' + filters.query : ''].filter(Boolean).join(' &bull; ') || '全样本视图';
    }

    document.querySelectorAll('[data-market-bucket]').forEach(function (button) {
      var key = button.getAttribute('data-market-bucket');
      var isActive = filters.bucket === key;
      button.setAttribute('aria-pressed', String(isActive));
      if (button.classList.contains('market-dist-row')) button.classList.toggle('active', isActive);
    });

    var sort = document.getElementById('market-mover-sort');
    if (sort) sort.value = dashboardState.moverSort;
  }

  function filterMarketQuotes() {
    var data = dashboardState.data || {};
    var rows = (data.quotes || []).filter(function (item) {
      if (dashboardState.filters.sector && !(item.sector_keys || []).includes(dashboardState.filters.sector)) return false;
      var change = finite(item.chg);
      var bucket = dashboardState.filters.bucket;
      if (bucket === 'down_gt5' && change > -5) return false;
      if (bucket === 'down_2_5' && (change <= -5 || change > -2)) return false;
      if (bucket === 'down_0_2' && (change <= -2 || change >= 0)) return false;
      if (bucket === 'flat' && change !== 0) return false;
      if (bucket === 'up_0_2' && (change <= 0 || change >= 2)) return false;
      if (bucket === 'up_2_5' && (change < 2 || change >= 5)) return false;
      if (bucket === 'up_gt5' && change < 5) return false;
      if (bucket === 'up' && change <= 0) return false;
      if (bucket === 'down' && change >= 0) return false;
      if (dashboardState.filters.query) {
        var q = dashboardState.filters.query.toLowerCase();
        if (String(item.name || '').toLowerCase().indexOf(q) < 0 && String(item.code || '').toLowerCase().indexOf(q) < 0) return false;
      }
      return true;
    });

    return rows.sort(function (a, b) {
      if (dashboardState.activeList === 'laggards' && dashboardState.moverSort === 'chg') return finite(a.chg) - finite(b.chg);
      return finite(b[dashboardState.moverSort]) - finite(a[dashboardState.moverSort]);
    });
  }

  function renderMarketMovers() {
    var element = document.getElementById('market-movers-list');
    if (!element) return;
    var rows = filterMarketQuotes();
    var footer = document.getElementById('market-mover-footer');
    if (footer) footer.textContent = '显示 ' + Math.min(rows.length, 50) + ' / ' + rows.length + ' 只标的 · 点击可切回主面板分析';

    element.innerHTML = rows.slice(0, 50).map(function (item, index) {
      var encodedCode = encodeURIComponent(item.code || '').replace(/'/g, '%27');
      var encodedName = encodeURIComponent(item.name || item.code || '').replace(/'/g, '%27');
      return '<button class="market-mover-row" onclick="openStockFromMarket(decodeURIComponent(\'' + encodedCode + '\'),decodeURIComponent(\'' + encodedName + '\'))">' +
        '<span class="market-mover-rank">' + (index + 1) + '</span>' +
        '<span class="market-mover-name"><strong>' + escapeHtml(item.name || '--') + '</strong><small>' + escapeHtml(item.code || '') + '</small></span>' +
        '<span style="font-family:\'SF Mono\',monospace">' + finite(item.price).toFixed(2) + '</span>' +
        '<strong class="' + tone(item.chg) + '" style="font-family:\'SF Mono\',monospace">' + pct(item.chg) + '</strong>' +
        '<small style="color:var(--gold)">换手 ' + finite(item.turnover).toFixed(2) + '%</small>' +
        '</button>';
    }).join('') || '<div class="market-empty-state">当前筛选条件下暂无股票标的</div>';
  }

  function renderCapitalAndRisk(data) {
    var flow = data.capital_flow || {};
    var flowElement = document.getElementById('market-capital-flow');
    if (flowElement) {
      flowElement.innerHTML = flow.available ? (
        '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:6px 0">' +
          '<div style="background:rgba(255,255,255,0.02);padding:6px 8px;border-radius:6px"><span>大盘资金流入</span><strong class="' + tone(flow.flow_a) + '">' + compactAmount(flow.flow_a) + '</strong></div>' +
          '<div style="background:rgba(255,255,255,0.02);padding:6px 8px;border-radius:6px"><span>大单流动净额</span><strong class="' + tone(flow.flow_b) + '">' + compactAmount(flow.flow_b) + '</strong></div>' +
          '<div style="background:rgba(255,255,255,0.02);padding:6px 8px;border-radius:6px"><span>中小单博弈</span><strong class="' + tone(flow.flow_c) + '">' + compactAmount(flow.flow_c) + '</strong></div>' +
        '</div>'
      ) : '<div class="market-empty-state compact">资金流数据实时同步中</div>';
    }

    var riskElement = document.getElementById('market-risk-tags');
    if (riskElement) {
      riskElement.innerHTML = (data.risk_tags || []).map(function (item) {
        return '<span class="' + escapeHtml(item.level || 'medium') + '">' + escapeHtml(item.text || '') + '</span>';
      }).join('') || '<span class="low">未发现高优先级系统性风险</span>';
    }

    var opportunityElement = document.getElementById('market-opportunities');
    if (opportunityElement) {
      opportunityElement.innerHTML = (data.opportunities || []).map(function (item) {
        return '<span class="opportunity-chip">🚀 ' + escapeHtml(item) + '</span>';
      }).join('') || '<span class="muted">暂无明确共振主线</span>';
    }
  }

  // ==========================================================================
  //  v18 · 一行结论（回答「今天市场什么状态」）
  // ==========================================================================
  function renderMarketConclusion(data) {
    var judgment = data.judgment || {};
    var breadth = data.breadth || {};
    var stamp = data.as_of ? new Date(data.as_of).toLocaleString('zh-CN', { hour12: false }) : '未知';
    var srcName = (data.source === 'tencent' ? '腾讯财经实时行情' : (data.source || '聚合行情')) + ' → 系统 A 股样本池研判';
    var textEl = document.getElementById('market-conclusion-text');
    var scoreEl = document.getElementById('market-conclusion-score');
    var regimeEl = document.getElementById('market-conclusion-regime');

    var sentence = (judgment.text && String(judgment.text).trim()) ? String(judgment.text).trim() : '';
    if (!sentence) sentence = judgment.regime ? ('当前市场状态：' + judgment.regime) : '暂无明确市场研判。';
    var breadthPhrase = '（上涨 ' + finite(breadth.up) + ' 家 · 下跌 ' + finite(breadth.down) + ' 家 · 上涨占比 ' + finite(breadth.up_ratio).toFixed(1) + '%）';

    if (textEl) {
      textEl.textContent = sentence + breadthPhrase;
      textEl.title = provenanceTitle(srcName, stamp);
    }
    // 情绪分必须与「市场情绪」卡同源：统一口径优先，judgment 仅兜底
    var flowState = sentimentState.flow || {};
    var uniState = sentimentState.data || {};
    var flowOk = !!(flowState.ok && flowState.level != null);
    var uniOk = !!(uniState.ok && uniState.score != null);
    var scoreText = '--', regimeText = judgment.regime || '--', scoreSrc = srcName + '（情绪分）';
    if (flowOk) {
      scoreText = String(Math.round(finite(flowState.level)));
      regimeText = (flowState.band || {}).label || (flowState.regime || {}).label || '--';
      scoreSrc = 'sentiment.v1 六维加权（统一口径）';
    } else if (uniOk) {
      scoreText = String(Math.round(finite(uniState.score)));
      regimeText = uniState.status || '--';
      scoreSrc = 'sentiment.v1 六维加权（统一口径）';
    } else if (judgment.score != null) {
      scoreText = String(Math.round(finite(judgment.score)));
    }
    if (scoreEl) {
      scoreEl.textContent = scoreText;
      scoreEl.title = provenanceTitle(scoreSrc, stamp);
    }
    if (regimeEl) {
      regimeEl.textContent = regimeText;
      regimeEl.title = provenanceTitle(scoreSrc, stamp);
    }
    setNature('market-conclusion-badge', data.stale ? 'snapshot' : 'aggregated');
  }

  // ==========================================================================
  //  v18 · 资金面体征（成交额 / 沪深港通 / 指数资金流）
  //  铁律：真实 > 如实降级 > 绝不编造；取不到一律「暂不可用 + 原因」
  // ==========================================================================
  function renderCapitalVitals(data) {
    var host = document.getElementById('market-capital-vitals');
    if (!host) return;
    var turnover = data.turnover || {};
    var flow = data.capital_flow || {};
    var stamp = data.as_of ? new Date(data.as_of).toLocaleString('zh-CN', { hour12: false }) : '未知';
    var stale = dashboardState.capitalStale === true;
    var rows = [];

    // 区块徽章：沿用快照时为「历史快照」，否则「聚合口径」
    setNature('market-vital-badge-capital', stale ? 'snapshot' : 'aggregated');

    // ① 两市样本成交额（真实行情聚合）
    var amountTxt = hasNum(turnover.amount) ? compactAmount(turnover.amount) : '暂不可用';
    rows.push('<div class="mo-cap-row">' +
      '<span class="mo-cap-label">两市样本成交额 ' + natureBadgeHtml('aggregated') + '</span>' +
      '<strong title="' + escapeHtml(provenanceTitle('系统覆盖沪深A股样本池成交额（腾讯财经）', stamp)) + '">' + amountTxt + '</strong>' +
      '</div>');

    // ② 样本平均换手率
    var turnTxt = hasNum(turnover.avg_turnover) ? Number(turnover.avg_turnover).toFixed(2) + '%' : '暂不可用';
    rows.push('<div class="mo-cap-row">' +
      '<span class="mo-cap-label">样本平均换手率 ' + natureBadgeHtml('aggregated') + '</span>' +
      '<strong title="' + escapeHtml(provenanceTitle('系统覆盖沪深A股样本池平均换手率（腾讯财经）', stamp)) + '">' + turnTxt + '</strong>' +
      '</div>');

    // ③ 沪深港通（独立接口 /api/market/north）
    var north = dashboardState.capitalVitals;
    if (dashboardState.capitalLoading && !north) {
      rows.push('<div class="mo-capital-skel"></div>');
    } else if (north && north.data) {
      var nd = north.data;
      var nStamp = north.update_time || '未知';
      var nSrc = north.source || 'AkShare 沪深港通资金流向';
      var disclosed = nd.disclosed === true;
      var nTitle = provenanceTitle(nSrc, nStamp) +
        (stale ? '\n数据时间 ' + timeOnly(nStamp) + '（历史快照 · 沿用上次成功结果）' : '');
      var natReal = stale ? 'snapshot' : 'real';
      var natComp = stale ? 'snapshot' : 'aggregated';
      if (disclosed) {
        rows.push('<div class="mo-cap-row">' +
          '<span class="mo-cap-label">北向净买额 ' + natureBadgeHtml(natReal) + '</span>' +
          '<strong class="' + tone(nd.total) + '" title="' + escapeHtml(nTitle) + '">' + (finite(nd.total) / 10000).toFixed(2) + ' 亿</strong>' +
          '</div>');
      } else {
        rows.push('<div class="mo-cap-row">' +
          '<span class="mo-cap-label">沪股通成分 ' + natureBadgeHtml(natComp) + '</span>' +
          '<strong title="' + escapeHtml(nTitle) + '">涨 ' + finite(nd.hgt_up) + ' / 跌 ' + finite(nd.hgt_down) + '</strong>' +
          '</div>');
        rows.push('<div class="mo-cap-row">' +
          '<span class="mo-cap-label">深股通成分 ' + natureBadgeHtml(natComp) + '</span>' +
          '<strong title="' + escapeHtml(nTitle) + '">涨 ' + finite(nd.sgt_up) + ' / 跌 ' + finite(nd.sgt_down) + '</strong>' +
          '</div>');
      }
      var south = Array.isArray(nd.south) ? nd.south : [];
      if (south.length) {
        var southTxt = south.map(function (b) {
          return (b.board || '港股通') + ' ' + finite(b.net_buy).toFixed(4) + ' 亿';
        }).join(' · ');
        rows.push('<div class="mo-cap-row">' +
          '<span class="mo-cap-label">南向港股通净买 ' + natureBadgeHtml(natReal) + '</span>' +
          '<strong class="' + tone(south[0].net_buy) + '" title="' + escapeHtml(nTitle) + '">' + escapeHtml(southTxt) + '</strong>' +
          '</div>');
      }
      if (stale) {
        // 决策 b：取数失败但同交易日内沿用上次成功快照 —— 明确自证身份（历史快照 + 时间戳）
        rows.push('<div class="mo-cap-note">' + natureBadgeHtml('snapshot') + ' 本次沪深港通取数失败（' +
          escapeHtml(dashboardState.capitalError || '未知原因') + '），沿用 ' + escapeHtml(String(nStamp)) +
          ' 的成功结果 · 数据时间 ' + escapeHtml(timeOnly(nStamp)) + '（历史快照）。仅同一交易日内沿用，跨交易日不沿用。</div>');
      } else if (!disclosed) {
        rows.push('<div class="mo-cap-note">' + natureBadgeHtml('snapshot') + ' ' +
          escapeHtml(nd.note || '北向资金成交净买额自 2024-08 起停止实时披露。') +
          '（接口 disclosed=false，net_buy=0 不计为「零流入」）</div>');
      }
    } else {
      rows.push('<div class="mo-cap-unavailable">沪深港通暂不可用 · 原因：' + escapeHtml(dashboardState.capitalError || '接口未返回数据') + '</div>');
    }

    // ④ 指数资金流（上游 capital_flow）
    if (flow.available) {
      rows.push('<div class="mo-cap-row">' +
        '<span class="mo-cap-label">指数资金流 ' + natureBadgeHtml('real') + '</span>' +
        '<strong title="' + escapeHtml(provenanceTitle('上游指数资金流接口：' + (flow.definition || '原始分类值'), stamp)) + '">' + compactAmount(flow.flow_a) + '</strong>' +
        '</div>');
    } else {
      rows.push('<div class="mo-cap-unavailable">指数资金流暂不可用 · 原因：上游 capital_flow.available=false（' +
        escapeHtml(flow.definition || '数据源不可用或超时') + '）</div>');
    }

    host.innerHTML = rows.join('');
  }

  async function loadCapitalVitals(force) {
    if (dashboardState.capitalLoading) return;
    var now = Date.now();
    if (!force && dashboardState.capitalVitals && !dashboardState.capitalStale && now - dashboardState.capitalLastFetch < 60000) return;
    dashboardState.capitalLoading = true;
    dashboardState.capitalLastFetch = now;
    try {
      var resp = await fetch('/api/market/north').then(function (r) { return r.json(); });
      if (resp && resp.ok && resp.data) {
        dashboardState.capitalVitals = resp;
        dashboardState.capitalStale = false;
        dashboardState.capitalError = null;
      } else {
        degradeCapitalSnapshot((resp && resp.message) ? resp.message : '接口未返回有效数据');
      }
    } catch (e) {
      degradeCapitalSnapshot('接口请求失败或超时');
    } finally {
      dashboardState.capitalLoading = false;
    }
    renderCapitalVitals(dashboardState.data || {});
  }

  // 决策 b：north 取数失败时，同一交易日内沿用上次成功快照（历史快照徽章）；跨交易日一律不沿用
  function degradeCapitalSnapshot(reason) {
    var prev = dashboardState.capitalVitals;
    var prevDate = prev && prev.data && prev.data.trade_date;
    if (prev && prevDate && String(prevDate) === localDateStr()) {
      dashboardState.capitalStale = true;
      dashboardState.capitalError = reason;
    } else {
      dashboardState.capitalVitals = null;
      dashboardState.capitalStale = false;
      dashboardState.capitalError = reason;
    }
  }

  // ==========================================================================
  //  v18 · 外部环境入口（全球金融市场对 A 股的传导）
  //  上游 M2 联动建模接口 GET /api/global/linkage 未就绪时，如实占位，不硬编码假数据。
  // ==========================================================================
  function renderExternalEnv() {
    var host = document.getElementById('market-external-body');
    if (!host) return;
    var payload = dashboardState.externalLinkage;

    if (dashboardState.externalLoading && !payload) {
      host.innerHTML = '<div class="mo-ext-placeholder">正在探测全球市场联动接口…</div>';
      return;
    }

    if (payload) {
      var d = payload.data || payload;
      var stamp = d.generated_at || d.as_of_date || d.update_time || '未知';
      var src = payload.source || 'GET /api/global/linkage（腾讯日K统计建模）';
      var benchName = (d.benchmark && d.benchmark.name) || '沪深300';
      var sc = d.scorecard;
      var cards = [];

      if (sc && typeof sc === 'object') {
        // ---- 主卡：综合分（4 位小数）+ stance + 实测/经验拆分（可追溯）----
        var comp = sc.components || {};
        var scoreCls = tone(sc.composite_score);
        var compTxt = '实测 ' + (hasNum(comp.measured) ? Number(comp.measured).toFixed(4) : '--') +
          ' ｜ 经验 ' + (hasNum(comp.heuristic) ? Number(comp.heuristic).toFixed(4) : '--');
        cards.push('<div class="mo-ext-card mo-ext-main">' +
          '<span>外部环境综合分 → ' + escapeHtml(benchName) + ' ' + natureBadgeHtml('aggregated') + '</span>' +
          '<strong class="' + scoreCls + '" title="' + escapeHtml(provenanceTitle(src + '：' + (sc.composite_definition || 'Σ各分项得分'), stamp)) + '">' +
            (hasNum(sc.composite_score) ? Number(sc.composite_score).toFixed(4) : '--') +
            '<em class="mo-ext-stance ' + scoreCls + '">' + escapeHtml(sc.stance || '--') + '</em>' +
          '</strong>' +
          '<small class="mo-ext-comp" title="综合分 = 各分项得分之和；实测项（领先滞后相关）与经验项（规则符号×经验权重）拆分如下">综合分 = ' +
            escapeHtml(compTxt) + '</small>' +
          '</div>');

        // ---- 明细卡：逐项按 evidence_type 区分（实测→真实 / 经验规则→聚合口径）----
        var items = Array.isArray(sc.items) ? sc.items : [];
        items.forEach(function (it) {
          var ev = it.evidence || {};
          var measured = it.evidence_type === 'measured';
          var nat = measured ? 'real' : 'aggregated';
          var typeTag = measured ? '实测' : '经验规则';
          var detail;
          if (ev.history_status === 'insufficient_samples') {
            // 样本不足项不得静默显示 --，须写明原因与样本量
            detail = '样本不足（n=' + escapeHtml(String(ev.history_sample_size != null ? ev.history_sample_size : ev.sample_size != null ? ev.sample_size : '--')) + '），不参与打分';
          } else if (measured) {
            if (ev.corr == null) {
              detail = '样本不足（n=' + escapeHtml(String(ev.sample_size != null ? ev.sample_size : '--')) + '），不参与打分';
            } else {
              detail = '滞后 r=' + Number(ev.corr).toFixed(4) +
                ' · 领先 ' + escapeHtml(String(ev.lead_days != null ? ev.lead_days : '--')) + ' 日' +
                ' · n=' + escapeHtml(String(ev.sample_size != null ? ev.sample_size : '--'));
              if (hasNum(ev.latest_ret)) {
                var lp = Number(ev.latest_ret) * 100;
                detail += ' · 隔夜 ' + (lp >= 0 ? '+' : '') + lp.toFixed(2) + '%';
              }
            }
          } else {
            detail = '规则：' + escapeHtml(ev.rule || '经验规则');
            if (hasNum(ev.chg)) detail += ' · 变动 ' + (Number(ev.chg) >= 0 ? '+' : '') + Number(ev.chg).toFixed(2) + '%';
          }
          var insuff = ev.history_status === 'insufficient_samples' || (measured && ev.corr == null);
          cards.push('<div class="mo-ext-card">' +
            '<span>' + escapeHtml(it.name || it.key) + ' <b class="mo-ext-type ' + nat + '">' + typeTag + '</b> ' + natureBadgeHtml(nat) + '</span>' +
            '<strong class="' + (insuff ? 'flat' : tone(it.score)) + '" title="' +
              escapeHtml(provenanceTitle(src + ' · ' + typeTag, stamp)) + '">' + detail + '</strong>' +
            '<small class="mo-ext-item-score">分项 ' + (hasNum(it.score) ? (Number(it.score) >= 0 ? '+' : '') + Number(it.score).toFixed(4) : '--') +
              ' ｜ ' + escapeHtml(it.stance || '--') + (insuff ? '（不计分）' : '') + '</small>' +
            '</div>');
        });

        // ---- notes：未计分项一行小字（否则用户会疑惑为何少两个因子）----
        var notes = Array.isArray(sc.notes) ? sc.notes : [];
        if (notes.length) {
          var reasonMap = { no_directional_rule: '不作方向判断', no_realtime_quote: '无实时行情' };
          var noteTxt = notes.map(function (n) {
            return (n.name || n.key) + '：' + (reasonMap[n.reason] || n.reason || '未计入');
          }).join(' ｜ ');
          cards.push('<div class="mo-ext-note">未计分项（不计入 composite）　' + escapeHtml(noteTxt) + '</div>');
        }
      }

      // ---- 回退：无 scorecard 时用 linkage / summary / drivers ----
      if (!cards.length && d.linkage && typeof d.linkage === 'object') {
        Object.keys(d.linkage).slice(0, 6).forEach(function (key) {
          var m = d.linkage[key] || {};
          var name = m.name || m.tencent || key;
          var parts = [];
          if (hasNum(m.latest_ret)) {
            var lp2 = Number(m.latest_ret) * 100;
            parts.push('隔夜 ' + (lp2 > 0 ? '+' : '') + lp2.toFixed(2) + '%');
          }
          var gap = m.gap_transmission || {};
          if (hasNum(gap.corr)) parts.push('开盘传导 r=' + Number(gap.corr).toFixed(3));
          if (hasNum(gap.direction_hit_rate)) parts.push('方向命中 ' + (Number(gap.direction_hit_rate) * 100).toFixed(1) + '%');
          cards.push('<div class="mo-ext-card"><span>' + escapeHtml(name) + ' → ' + escapeHtml(benchName) + '</span><strong class="' +
            (hasNum(m.latest_ret) ? tone(Number(m.latest_ret)) : '') + '" title="' +
            escapeHtml(provenanceTitle(src + ' · 基准 ' + benchName, stamp)) + '">' + escapeHtml(parts.join(' · ') || '--') + '</strong></div>');
        });
      }
      if (!cards.length && d.summary) {
        cards.push('<div class="mo-ext-card"><span>联动摘要 ' + natureBadgeHtml('aggregated') + '</span><strong style="font-size:12px;font-family:inherit" title="' +
          escapeHtml(provenanceTitle(src, stamp)) + '">' + escapeHtml(String(d.summary)) + '</strong></div>');
      }
      if (!cards.length) {
        cards.push('<div class="mo-ext-placeholder">已接入 <code>GET /api/global/linkage</code>（HTTP 200），但返回体无 scorecard / linkage 字段，等待上游补齐字段定义。</div>');
      }
      host.innerHTML = cards.join('');
      setNature('market-external-badge', 'aggregated');
      return;
    }

    // 占位：接口不存在 / 不可达 —— 明确写出待上游就绪，不展示任何推断值
    host.innerHTML = '<div class="mo-ext-placeholder">' + natureBadgeHtml('snapshot') +
      ' 外部环境传导模型待上游接口就绪：尚未接入 <code>GET /api/global/linkage</code>（本次探测：' +
      escapeHtml(dashboardState.externalError || '接口未就绪') + '）。' +
      '本区块不展示任何推断或占位数值，接口上线后自动接入。</div>';
    setNature('market-external-badge', 'snapshot');
  }

  async function loadExternalEnv(force) {
    if (dashboardState.externalLoading) return;
    var now = Date.now();
    if (!force && now - dashboardState.externalLastFetch < 120000) return;
    dashboardState.externalLoading = true;
    dashboardState.externalLastFetch = now;
    try {
      var resp = await fetch('/api/global/linkage').then(function (r) {
        return r.json().then(function (body) { return { status: r.status, body: body }; });
      });
      var body = resp && resp.body;
      if (resp && resp.status === 200 && body && (body.ok === true || body.data)) {
        dashboardState.externalLinkage = body;
        dashboardState.externalError = null;
      } else {
        dashboardState.externalLinkage = null;
        dashboardState.externalError = (body && (body.detail || body.message)) ? body.detail || body.message : ('HTTP ' + (resp && resp.status));
      }
    } catch (e) {
      dashboardState.externalLinkage = null;
      dashboardState.externalError = '接口不可达';
    } finally {
      dashboardState.externalLoading = false;
    }
    renderExternalEnv();
  }

  window.loadCapitalVitals = loadCapitalVitals;
  window.loadExternalEnv = loadExternalEnv;

  function populateTradeSymbols(data) {
    var select = document.getElementById('market-trade-symbol');
    if (!select) return;
    var current = select.value;
    var seen = {};
    var rows = (data.quotes || []).filter(function (item) { return finite(item.price) > 0; });
    select.innerHTML = rows.filter(function (item) {
      if (!item.code || seen[item.code]) return false;
      seen[item.code] = true;
      return item.price > 0;
    }).map(function (item) {
      return '<option value="' + escapeHtml(item.code) + '">' + escapeHtml(item.name) + ' · ' + finite(item.price).toFixed(2) + '</option>';
    }).join('');
    if (current && seen[current]) select.value = current;
  }

  function executeMarketTrade(side) {
    var select = document.getElementById('market-trade-symbol');
    var qtyInput = document.getElementById('market-trade-qty');
    var code = select ? select.value : '';
    var qty = Math.floor(finite(qtyInput && qtyInput.value));
    var quote = quoteMap()[code];
    if (!quote || finite(quote.price) <= 0) return notifyAccount('当前标的缺少有效行情');
    if (qty <= 0) return notifyAccount('交易数量必须为正整数');
    var price = finite(quote.price);
    var holding = account.holdings.find(function (item) { return item.code === code; });
    if (side === 'buy' && qty % 100 !== 0) return notifyAccount('A股买入需按 100 股整数倍下单');
    if (side === 'sell' && qty % 100 !== 0 && (!holding || qty !== holding.qty)) return notifyAccount('卖出需按 100 股整数倍，或一次卖出剩余持仓');

    if (side === 'buy') {
      var cost = price * qty;
      if (cost > account.cash) return notifyAccount('可用资金不足');
      account.cash -= cost;
      if (holding) {
        var totalQty = holding.qty + qty;
        holding.avgPrice = (holding.avgPrice * holding.qty + cost) / totalQty;
        holding.qty = totalQty;
      } else {
        account.holdings.push({ code: code, name: quote.name || code, qty: qty, avgPrice: price });
      }
    } else {
      if (!holding || holding.qty < qty) return notifyAccount('卖出数量超过持仓');
      account.cash += price * qty;
      holding.qty -= qty;
      if (holding.qty <= 0) account.holdings = account.holdings.filter(function (item) { return item.code !== code; });
    }
    account.trades.push({ id: Date.now(), side: side, code: code, name: quote.name || code, qty: qty, price: price, time: new Date().toISOString() });
    account.trades = account.trades.slice(-100);
    saveAccount();
    renderMarketAccount();
    notifyAccount((side === 'buy' ? '买入' : '卖出') + ' ' + (quote.name || code) + ' ' + qty + ' 股');
  }

  /** FIFO 配对已实现盈亏统计：胜率 / 盈亏比 / 平均每笔 */
  function realizedStats() {
    var queues = {};
    var closed = [];
    account.trades.forEach(function (t) {
      var qty = Math.floor(finite(t.qty));
      var price = finite(t.price);
      if (!t.code || qty <= 0 || price <= 0) return;
      if (t.side === 'buy') {
        (queues[t.code] = queues[t.code] || []).push({ qty: qty, price: price });
      } else {
        var queue = queues[t.code] || [];
        var remain = qty;
        while (remain > 0 && queue.length) {
          var lot = queue[0];
          var take = Math.min(remain, lot.qty);
          closed.push((price - lot.price) * take);
          lot.qty -= take;
          remain -= take;
          if (lot.qty <= 0) queue.shift();
        }
      }
    });
    var wins = closed.filter(function (p) { return p > 0; });
    var losses = closed.filter(function (p) { return p < 0; });
    var grossWin = wins.reduce(function (s, p) { return s + p; }, 0);
    var grossLoss = Math.abs(losses.reduce(function (s, p) { return s + p; }, 0));
    return {
      realized: closed.reduce(function (s, p) { return s + p; }, 0),
      closedCount: closed.length,
      winRate: closed.length ? wins.length / closed.length * 100 : null,
      profitFactor: grossLoss > 0 ? grossWin / grossLoss : (grossWin > 0 ? Infinity : null)
    };
  }

  /** 记录每日资产快照（收益曲线数据源，同日更新，保留 180 点） */
  function recordEquitySnapshot(assets) {
    if (!Array.isArray(account.equityHistory)) account.equityHistory = [];
    var today = new Date().toISOString().slice(0, 10);
    var last = account.equityHistory[account.equityHistory.length - 1];
    if (last && last.d === today) {
      if (Math.abs(finite(last.v) - assets) > 0.01) { last.v = Math.round(assets * 100) / 100; saveAccount(); }
    } else {
      account.equityHistory.push({ d: today, v: Math.round(assets * 100) / 100 });
      account.equityHistory = account.equityHistory.slice(-180);
      saveAccount();
    }
  }

  function renderMarketAccount() {
    var map = quoteMap();
    var marketValue = 0;
    var costValue = 0;
    var dayPnl = 0;
    var rows = account.holdings.map(function (item) {
      var quote = map[item.code];
      var price = finite(quote && quote.price, item.avgPrice);
      var chg = finite(quote && quote.chg);
      var denom = 1 + chg / 100;
      var prevClose = denom > 0 ? price / denom : price;
      var value = price * item.qty;
      var cost = item.avgPrice * item.qty;
      marketValue += value;
      costValue += cost;
      var itemDay = (price - prevClose) * item.qty;
      dayPnl += itemDay;
      return { item: item, price: price, value: value, cost: cost, pnl: value - cost, day: itemDay };
    });
    var assets = account.cash + marketValue;
    var pnl = assets - account.initialCash;
    var ret = account.initialCash ? pnl / account.initialCash * 100 : 0;
    var positionPct = assets > 0 ? marketValue / assets * 100 : 0;
    var stats = realizedStats();

    setText('acct-initial', money(account.initialCash));
    setText('market-account-assets', money(assets));
    var pnlElement = document.getElementById('market-account-pnl');
    if (pnlElement) {
      pnlElement.textContent = (pnl >= 0 ? '+' : '') + money(pnl) + ' (' + pct(ret) + ')';
      pnlElement.className = tone(pnl);
    }
    var dayElement = document.getElementById('acct-day-pnl');
    if (dayElement) {
      dayElement.textContent = (dayPnl >= 0 ? '+' : '') + money(dayPnl);
      dayElement.className = tone(dayPnl);
    }
    var retElement = document.getElementById('market-account-return');
    if (retElement) { retElement.textContent = pct(ret); retElement.className = tone(ret); }

    setText('market-account-cash', money(account.cash));
    setText('market-account-value', money(marketValue));
    setText('acct-position-pct', pct(positionPct));
    var realizedEl = document.getElementById('acct-realized');
    if (realizedEl) {
      realizedEl.textContent = (stats.realized >= 0 ? '+' : '') + money(stats.realized);
      realizedEl.className = tone(stats.realized);
    }
    setText('acct-winrate', stats.winRate == null ? '--' : stats.winRate.toFixed(0) + '%');
    setText('acct-closed-count', stats.closedCount ? stats.closedCount + '笔已平仓' : '暂无平仓');
    setText('acct-pf', stats.profitFactor == null ? '--' : (stats.profitFactor === Infinity ? '∞' : stats.profitFactor.toFixed(2)));

    recordEquitySnapshot(assets);
    renderAccountAllocation(account.cash, account.holdings, map);
    renderEquityCurve();

    var holdingsElement = document.getElementById('market-account-holdings');
    if (holdingsElement) {
      holdingsElement.innerHTML = rows.length ? rows.map(function (r) {
        var weight = marketValue > 0 ? r.value / marketValue * 100 : 0;
        var pnlPct = r.cost > 0 ? r.pnl / r.cost * 100 : 0;
        return '<div class="acct-holding-row">' +
          '<div class="acct-h-name"><strong>' + escapeHtml(r.item.name) + '</strong><small>' + escapeHtml(r.item.code) + ' · ' + r.item.qty + '股</small></div>' +
          '<div class="acct-h-cell"><small>成本 / 现价</small><b>' + finite(r.item.avgPrice).toFixed(2) + ' / ' + r.price.toFixed(2) + '</b></div>' +
          '<div class="acct-h-cell"><small>市值</small><b>' + money(r.value) + '</b></div>' +
          '<div class="acct-h-cell"><small>仓位权重 ' + weight.toFixed(0) + '%</small><div class="acct-wbar"><i style="width:' + Math.min(100, weight).toFixed(1) + '%"></i></div></div>' +
          '<div class="acct-h-cell"><small>浮动盈亏</small><b class="' + tone(r.pnl) + '">' + (r.pnl >= 0 ? '+' : '') + money(r.pnl) + ' (' + (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(1) + '%)</b></div>' +
          '<div class="acct-h-cell"><small>今日盈亏</small><b class="' + tone(r.day) + '">' + (r.day >= 0 ? '+' : '') + money(r.day) + '</b></div>' +
          '</div>';
      }).join('') : '<div class="market-account-empty">暂无持仓，可使用上方表单进行模拟交易</div>';
    }
    setText('acct-holdings-count', rows.length ? rows.length + ' 只标的' : '');

    var tradeElement = document.getElementById('market-account-trades');
    if (tradeElement) {
      var recent = account.trades.slice(-4).reverse();
      tradeElement.innerHTML = recent.length ? recent.map(function (item) {
        var cls = item.side === 'buy' ? 'buy' : 'sell';
        var date = new Date(item.time);
        return '<div class="market-trade-history-row"><span class="' + cls + '">' + (cls === 'buy' ? '买' : '卖') + '</span><strong>' + escapeHtml(item.name || item.code) + '</strong><small>' + item.qty + '股 · ' + money(item.price) + '</small><time>' + date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' }) + '</time></div>';
      }).join('') : '<div class="market-account-empty">暂无模拟交易记录</div>';
    }
  }

  /** 收益曲线：每日总资产快照折线 + 初始资金基准线（红盈绿亏） */
  function renderEquityCurve() {
    var element = document.getElementById('acct-equity-chart');
    if (!element || typeof echarts === 'undefined') return;
    if (!dashboardState.equityChart || dashboardState.equityChart.isDisposed()) {
      dashboardState.equityChart = echarts.init(element, null, { renderer: 'canvas' });
    }
    var points = (account.equityHistory || []).slice(-120);
    var labels = points.map(function (p) { return String(p.d).slice(5); });
    var values = points.map(function (p) { return finite(p.v); });
    if (!values.length) { labels = ['起始']; values = [account.initialCash]; }
    var last = values[values.length - 1];
    var profit = last >= account.initialCash;
    var lineColor = profit ? '#ff3d6a' : '#00e96a';
    dashboardState.equityChart.setOption({
      animationDuration: 300,
      backgroundColor: 'transparent',
      grid: { left: 46, right: 12, top: 14, bottom: 20 },
      tooltip: {
        trigger: 'axis', confine: true,
        formatter: function (ps) {
          var p = ps && ps[0];
          if (!p) return '';
          var diff = finite(p.value) - account.initialCash;
          return escapeHtml(p.axisValue) + '<br>总资产 ' + money(p.value) +
            '<br><span style="color:' + (diff >= 0 ? '#ff3d6a' : '#00e96a') + '">' + (diff >= 0 ? '+' : '') + money(diff) + '</span>';
        }
      },
      xAxis: {
        type: 'category', data: labels, boundaryGap: false,
        axisLabel: { color: '#8b9ec2', fontSize: 8, interval: Math.ceil(labels.length / 8) },
        axisLine: { lineStyle: { color: 'rgba(139,158,194,.25)' } }, axisTick: { show: false }
      },
      yAxis: {
        type: 'value', scale: true,
        axisLabel: { color: '#8b9ec2', fontSize: 8, formatter: function (v) { return '¥' + (v >= 10000 ? (v / 10000).toFixed(1) + 'w' : v); } },
        splitLine: { lineStyle: { color: 'rgba(139,158,194,.1)' } }
      },
      series: [{
        type: 'line', data: values, smooth: true, symbol: 'none',
        lineStyle: { color: lineColor, width: 2 },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: profit ? 'rgba(255,61,106,.28)' : 'rgba(0,233,106,.28)' },
              { offset: 1, color: 'rgba(0,0,0,0)' }
            ]
          }
        },
        markLine: {
          silent: true, symbol: 'none',
          lineStyle: { color: 'rgba(139,158,194,.45)', type: 'dashed', width: 1 },
          label: { color: '#8b9ec2', fontSize: 8, formatter: '初始 ' + money(account.initialCash) },
          data: [{ yAxis: account.initialCash }]
        }
      }]
    }, true);
  }

  function renderAccountAllocation(cash, holdings, map) {
    var element = document.getElementById('market-account-chart');
    if (!element || typeof echarts === 'undefined') return;
    if (!dashboardState.accountChart || dashboardState.accountChart.isDisposed()) {
      dashboardState.accountChart = echarts.init(element, null, { renderer: 'canvas' });
    }
    var rows = [];
    if (finite(cash) > 0) rows.push({ name: '现金', value: finite(cash), itemStyle: { color: '#64748b' } });
    holdings.forEach(function (item) {
      var quote = map[item.code];
      var value = finite(quote && quote.price, item.avgPrice) * finite(item.qty);
      if (value > 0) rows.push({ name: String(item.name || item.code), value: value });
    });
    if (!rows.length) rows = [{ name: '现金', value: 1, itemStyle: { color: '#64748b' } }];
    var total = rows.reduce(function (sum, item) { return sum + finite(item.value); }, 0);
    dashboardState.accountChart.setOption({
      animationDuration: 300,
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: function (p) { return escapeHtml(p.name) + '<br>' + money(p.value) + ' · ' + p.percent + '%'; }
      },
      legend: {
        type: 'scroll',
        orient: 'vertical',
        right: 2,
        top: 'middle',
        width: '42%',
        textStyle: { color: '#8b9ec2', fontSize: 9 },
        formatter: function (name) { return name.length > 7 ? name.slice(0, 7) + '…' : name; }
      },
      graphic: [
        { type: 'text', left: '31%', top: '43%', style: { text: money(total), textAlign: 'center', fill: '#eaf0ff', fontSize: 11, fontWeight: 700 } },
        { type: 'text', left: '31%', top: '56%', style: { text: '总资产', textAlign: 'center', fill: '#8b9ec2', fontSize: 8 } }
      ],
      series: [{
        type: 'pie',
        radius: ['42%', '68%'],
        center: ['31%', '50%'],
        avoidLabelOverlap: true,
        label: { show: false },
        labelLine: { show: false },
        itemStyle: { borderColor: 'rgba(7,13,29,0.8)', borderWidth: 2, borderRadius: 4 },
        data: rows
      }]
    }, true);
  }

  function saveViewPreference() {
    try {
      localStorage.setItem(VIEW_STORAGE_KEY, JSON.stringify({
        trendWindow: dashboardState.trendWindow,
        trendMode: dashboardState.trendMode,
        sectorSort: dashboardState.sectorSort,
        moverSort: dashboardState.moverSort
      }));
    } catch (error) {}
  }

  function syncMarketControls() {
    document.querySelectorAll('[data-trend-window]').forEach(function (button) {
      var value = button.getAttribute('data-trend-window');
      var active = value === 'all' ? dashboardState.trendWindow === 'all' : Number(value) === Number(dashboardState.trendWindow);
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    document.querySelectorAll('[data-trend-mode]').forEach(function (button) {
      var active = button.getAttribute('data-trend-mode') === dashboardState.trendMode;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    var select = document.getElementById('market-sector-sort');
    if (select) select.value = dashboardState.sectorSort;
    updateMarketFilterControls();
  }

  function setMarketTrendWindow(value) {
    dashboardState.trendWindow = value === 'all' ? 'all' : (Math.max(12, Math.min(48, Math.round(finite(value, 48)))));
    saveViewPreference();
    syncMarketControls();
    renderIndexChart((dashboardState.data || {}).index_trends || []);
  }

  function setMarketTrendMode(mode) {
    dashboardState.trendMode = mode === 'index' ? 'index' : 'percent';
    saveViewPreference();
    syncMarketControls();
    renderIndexChart((dashboardState.data || {}).index_trends || []);
  }

  function setMarketSectorSort(sort) {
    dashboardState.sectorSort = ['avg_chg', 'up_ratio', 'amount'].indexOf(sort) >= 0 ? sort : 'avg_chg';
    saveViewPreference();
    syncMarketControls();
    renderSectorHeatmap((dashboardState.data || {}).sectors || []);
  }

  function toggleMarketChartFocus() {
    dashboardState.chartFocused = !dashboardState.chartFocused;
    var panel = document.querySelector('.market-trend-panel');
    if (panel) panel.classList.toggle('focused', dashboardState.chartFocused);
    document.body.classList.toggle('market-chart-focused', dashboardState.chartFocused);
    var button = document.querySelector('.market-chart-expand');
    if (button) {
      button.textContent = dashboardState.chartFocused ? '⤢' : '⛶';
      button.title = dashboardState.chartFocused ? '还原图表' : '放大图表';
      button.setAttribute('aria-label', button.title);
      button.setAttribute('aria-pressed', String(dashboardState.chartFocused));
    }
    setTimeout(resizeMarketDashboard, 40);
  }

  function resetMarketChartFocus() {
    if (dashboardState.chartFocused) toggleMarketChartFocus();
  }

  function exportMarketTrades() {
    if (!account.trades.length) return notifyAccount('当前没有可导出的模拟交易记录');
    function csvCell(value) {
      var text = String(value == null ? '' : value);
      if (typeof value === 'string' && /^[\s\u0000-\u001f]*[=+\-@]/.test(text)) text = "'" + text;
      return '"' + text.replace(/"/g, '""') + '"';
    }
    var rows = [['时间', '方向', '证券代码', '证券名称', '数量', '成交价', '成交金额']];
    account.trades.forEach(function (item) {
      rows.push([new Date(item.time).toLocaleString('zh-CN', { hour12: false }), item.side === 'buy' ? '买入' : '卖出', item.code, item.name || item.code, item.qty, finite(item.price), finite(item.qty) * finite(item.price)]);
    });
    var csvContent = '\uFEFF' + rows.map(function (row) { return row.map(csvCell).join(','); }).join('\r\n');
    var blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = 'dapeng_market_trades_' + new Date().toISOString().slice(0, 10) + '.csv';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    notifyAccount('已导出 ' + account.trades.length + ' 条模拟交易记录');
  }

  function resetMarketAccount() {
    if (!confirm('确定要重置模拟交易账户吗？资金将恢复为 2000 元，持仓与历史将被清空。')) return;
    account = { initialCash: INITIAL_CASH, cash: INITIAL_CASH, holdings: [], trades: [], equityHistory: [] };
    saveAccount();
    renderMarketAccount();
    notifyAccount('模拟账户已重置为初始状态');
  }

  function notifyAccount(message) {
    if (typeof showToast === 'function') showToast('模拟交易', message);
  }

  // ===== v17: 全球资产行情条（美股/港股/亚太/欧洲/商品/外汇） =====
  var _gaLastFetch = 0;

  async function loadGlobalAssets(force) {
    var strip = document.getElementById('global-assets-strip');
    if (!strip) return;
    var now = Date.now();
    if (!force && now - _gaLastFetch < 60000 && strip.dataset.loaded === '1') return;
    _gaLastFetch = now;
    try {
      var resp = await fetch('/api/global/assets').then(function (r) { return r.json(); });
      if (resp && resp.ok) renderGlobalAssets(resp);
    } catch (e) { /* 全球资产加载失败静默降级 */ }
  }

  function renderGlobalAssets(payload) {
    var strip = document.getElementById('global-assets-strip');
    if (!strip) return;
    var names = payload.category_names || {};
    var cats = payload.categories || {};
    var order = ['us', 'hk', 'asia', 'europe', 'commodity', 'forex'];
    var html = order.map(function (cat) {
      var items = cats[cat] || [];
      if (!items.length) return '';
      var inner = items.map(function (it) {
        if (!it.available) return '<span class="ga-item na"><em>' + escapeHtml(it.name) + '</em><b>--</b></span>';
        var cls = it.chg > 0 ? 'up' : it.chg < 0 ? 'dn' : '';
        var price = it.price >= 1000 ? Math.round(it.price).toLocaleString('zh-CN') : it.price;
        return '<span class="ga-item" title="' + escapeHtml(it.unit || '') + '"><em>' + escapeHtml(it.name) + '</em><b>' + price + '</b><i class="' + cls + '">' + (it.chg > 0 ? '+' : '') + Number(it.chg).toFixed(2) + '%</i></span>';
      }).join('');
      return '<div class="ga-group"><span class="ga-cat">' + escapeHtml(names[cat] || cat) + '</span>' + inner + '</div>';
    }).join('');
    var cov = payload.coverage || {};
    strip.innerHTML = html + '<span class="ga-update">' + (payload.update_time || '').slice(5, 16) + ' · 实时 ' + (cov.available || 0) + '/' + (cov.total || 0) + '</span>';
    strip.dataset.loaded = '1';
  }

  window.loadGlobalAssets = loadGlobalAssets;

  function resizeMarketDashboard() {
    if (dashboardState.chart && !dashboardState.chart.isDisposed()) dashboardState.chart.resize();
    if (dashboardState.accountChart && !dashboardState.accountChart.isDisposed()) dashboardState.accountChart.resize();
    if (dashboardState.equityChart && !dashboardState.equityChart.isDisposed()) dashboardState.equityChart.resize();
  }

  function openStockFromMarket(code, name) {
    switchWorkspace('stocks');
    if (typeof selectStock === 'function') {
      selectStock(code, name);
    }
  }

  window.switchWorkspace = switchWorkspace;
  window.loadMarketDashboard = loadMarketDashboard;
  window.loadSentimentState = loadSentimentState;
  window.setMarketTrendWindow = setMarketTrendWindow;
  window.setMarketTrendMode = setMarketTrendMode;
  window.setMarketSectorSort = setMarketSectorSort;
  window.setMarketMoverSort = setMarketMoverSort;
  window.setMarketSectorFilter = setMarketSectorFilter;
  window.setMarketBucketFilter = setMarketBucketFilter;
  window.clearMarketFilters = clearMarketFilters;
  window.switchMarketList = switchMarketList;
  window.toggleMarketChartFocus = toggleMarketChartFocus;
  window.resetMarketChartFocus = resetMarketChartFocus;
  window.executeMarketTrade = executeMarketTrade;
  window.resetMarketAccount = resetMarketAccount;
  window.exportMarketTrades = exportMarketTrades;
  window.resizeMarketDashboard = resizeMarketDashboard;
  window.openStockFromMarket = openStockFromMarket;
  window.focusIndexChart = focusIndexChart;

  // ===== 实时推送（SSE）：行情实时刷新，连续失败自动降级为轮询 =====
  function ensureStreamIndicator() {
    var host = document.getElementById('market-dashboard-view');
    if (!host) return null;
    var bar = document.getElementById('market-stream-indicator');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'market-stream-indicator';
      bar.style.cssText = 'margin:0 0 10px 0;padding:6px 12px;border-radius:8px;font-size:12px;' +
        'display:flex;align-items:center;gap:8px;background:rgba(128,128,128,0.08);' +
        'border:1px solid rgba(128,128,128,0.18)';
      host.insertBefore(bar, host.firstChild);
    }
    return bar;
  }

  function renderStreamBadge() {
    var bar = ensureStreamIndicator();
    if (!bar) return;
    var dot = dashboardState.streamDegraded ? '#f59e0b' : (dashboardState.streamActive ? '#22c55e' : '#94a3b8');
    var label, tip;
    if (dashboardState.streamDegraded) {
      label = '推送已降级为轮询';
      tip = '原因：' + (dashboardState.streamReason || '未知') + '（已自动回落到 45s 定时轮询）';
    } else if (dashboardState.streamActive) {
      label = '实时推送已连接';
      tip = '数据经 SSE 实时推送（/api/stream/market）';
    } else {
      label = '轮询模式';
      tip = '当前为定时轮询（每 45s）';
    }
    var last = dashboardState.streamLastPush
      ? new Date(dashboardState.streamLastPush).toLocaleTimeString('zh-CN', { hour12: false }) : '-';
    bar.title = tip;
    bar.innerHTML = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + dot + '"></span>'
      + '<b style="color:var(--text)">' + escapeHtml(label) + '</b>'
      + '<span style="color:var(--text2)">' + escapeHtml(tip) + '</span>'
      + '<span style="color:var(--text2);margin-left:auto">最近推送 ' + escapeHtml(last) + '</span>';
  }

  function applyMarketPush(payload) {
    if (!payload) return;
    if (!payload.data) {
      // 推送未携带数据：显式标注降级原因，不静默
      dashboardState.streamReason = payload.reason || '暂无数据';
      if (payload.degraded) renderStreamBadge();
      return;
    }
    var d = Object.assign({}, payload.data);
    if (payload.degraded) d.stale = true;
    d.stream_source = payload.source;
    d.stream_updated_at = payload.updated_at;
    dashboardState.data = d;
    dashboardState.lastLoadedAt = Date.now();
    if (!d.stale) dashboardState.lastSuccessAt = Date.now();
    dashboardState.streamLastPush = Date.now();
    dashboardState.streamReason = payload.reason || '';
    // 页面隐藏时暂停渲染但保留连接，避免隐藏页面积压渲染
    if (!document.hidden) {
      try { renderMarketDashboard(d); } catch (e) { /* 渲染失败不阻断推送 */ }
    }
    renderStreamBadge();
  }

  function degradeMarketStream(reason) {
    dashboardState.streamActive = false;
    dashboardState.streamDegraded = true;
    dashboardState.streamReason = reason || '推送不可用';
    renderStreamBadge();
    // 回落轮询：立即补拉一次，之后由既有 45s 轮询接手
    loadMarketDashboard(false);
  }

  function initMarketStream() {
    if (typeof window.DashboardStream === 'undefined') {
      dashboardState.streamDegraded = true;
      dashboardState.streamReason = '推送组件未加载（dashboard-stream.js）';
      renderStreamBadge();
      return;
    }
    dashboardState.streamCtrl = window.DashboardStream.create({
      url: '/api/stream/market',
      onOpen: function () {
        dashboardState.streamActive = true;
        dashboardState.streamDegraded = false;
        dashboardState.streamReason = '';
        renderStreamBadge();
      },
      onEvent: function (name, payload) {
        if (name === 'market.snapshot') applyMarketPush(payload);
      },
      onDegrade: function (reason) { degradeMarketStream(reason); }
    });
  }

  function applyWorkspaceHash() {
    var raw = (location.hash || '').replace(/^#/, '').toLowerCase();
    if (!raw) return;
    if (raw === 'market' || raw === 'sentiment' || raw === 'overview' || raw === 'fact') switchWorkspace('market');
    else if (raw === 'decision') switchWorkspace('decision');
    else if (raw === 'survey') switchWorkspace('survey');
    else if (raw === 'qlib') switchWorkspace('qlib');
    else if (raw === 'stocks' || raw === 'stock') switchWorkspace('stocks');
  }

  document.addEventListener('DOMContentLoaded', function () {
    var searchInput = document.getElementById('market-symbol-search');
    if (searchInput) {
      searchInput.addEventListener('input', function (e) {
        dashboardState.filters.query = e.target.value.trim();
        updateMarketFilterControls();
        renderMarketMovers();
      });
    }

    window.addEventListener('resize', resizeMarketDashboard);

    // 跨页上下文：?sector=<板块名> 由个股看板的「该板块市场情绪」带入
    try {
      var params = new URLSearchParams(location.search);
      var sectorParam = params.get('sector');
      if (sectorParam) dashboardState.pendingSector = sectorParam;
    } catch (e) { /* 老浏览器忽略 */ }

    // 深链：#market / #decision / #survey / #qlib
    // 原「市场情绪追踪」独立页（sentiment.html）已并入大盘纵览，其书签统一落到 #market
    applyWorkspaceHash();
    window.addEventListener('hashchange', applyWorkspaceHash);

    // 启动实时推送（失败会自动降级为下面的定时轮询）
    initMarketStream();

    setInterval(function () {
      var seg = document.querySelector('[data-hub-panel="fact"]');
      if (seg && seg.classList.contains('is-active') && !document.hidden) {
        if (dashboardState.streamActive && !dashboardState.streamDegraded) return; // 推送在线时不重复轮询
        loadMarketDashboard(false);
        loadSentimentState(false);
      }
    }, 45000);
  });
})();
