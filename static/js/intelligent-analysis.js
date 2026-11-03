/**
 * DAPENG Alpha — Qlib 因子挖掘与动态市场调查前端交互控制器 (V16.0)
 * 完美支持：
 * 1. Qlib 因子多选、IC/IR 分析、组合收益曲线 ECharts 绘制、五分位分层横向柱状图
 * 2. 宏观数据、板块轮动、盘中异动、财经要闻实时加载与自然语言总结
 * 3. 工作区视图切换 (Stocks, Market, Qlib, Survey)
 * 4. V16 界面升级：四维因子迷你进度条 / Top 组合表可排序 / 60s 自动轮询+倒计时 /
 *    异动预警筛选 chips / 新闻标签 tabs / 板块轮动阶段图例
 */

(function() {
  'use strict';

  var qlibState = {
    factors: [],                 // /qlib/meta 因子清单（含 available / formula_desc）
    selectedFactorIds: [],
    weights: {},                 // 因子 id -> 权重（真实参与综合分加权）
    benchmark: 'sh000300',
    topK: 8,
    startDate: '',
    endDate: '',
    factorStats: {},             // 上次回测返回的每因子实算 IC/rank_ic/ir
    navChart: null,
    quantileChart: null,      // 五分位分层横向柱状图
    lastResult: null,
    topSort: { key: 'score', dir: 'desc' },  // Top 组合表排序状态
    loading: false,
    metaLoaded: false
  };

  var surveyState = {
    loading: false,
    lastData: null,
    scatterChart: null,      // 板块轮动散点图实例
    selectedSector: null,    // 当前选中的板块（散点 ↔ 列表联动）
    sectorIndex: {},         // sector → 散点 dataIndex 映射（用于 dispatchAction）
    sectorStage: {},         // sector → 阶段（阶段图例 solo 高亮用）
    stageCounts: {},         // 阶段 → 板块数（图例计数）
    stageSolo: null,         // 当前 solo 高亮的阶段（null = 无）
    alerts: [],              // 异动预警原始数据
    alertFilter: 'all',      // 当前筛选：all | high | medium | low | type:xxx
    news: [],                // 新闻原始数据
    newsUnavailable: null,   // 非 null 表示新闻数据源故障（含原因），区别于「正常空列表」
    newsFilter: 'all'        // 当前标签：all | 标签名
  };

  // 60 秒自动轮询（调查视图可见 + 页面非隐藏时才计时）
  var SURVEY_POLL_SEC = 60;
  var surveyCountdownSec = SURVEY_POLL_SEC;
  var SURVEY_AUTOREFRESH_KEY = 'survey-autorefresh';
  var surveyAutoEnabled = true;     // 自动轮询开关（localStorage 持久化）
  var surveyTickTimer = null;       // 1s 倒计时定时器

  function escapeHtml(str) {
    return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ==========================================
  //  1. 工作区视图导航 (4合1工作台切换)
  // ==========================================
  /* 判断层（动态市场调查）的初始化钩子。
     工作区切换已由 market-dashboard.js 的统一实现接管（主脑中心三层），
     本文件不再重复定义 switchWorkspace——只暴露「进入判断层要做的事」。 */
  function initSurveyLayer() {
    brainEnsureInit();
    loadMarketSurveyData();
    loadSurveyIndexStrip();
    loadRotationFlow();
    loadHsgtBreadth();
    // 仅当用户开启了自动轮询才启动倒计时（开关状态持久化在 localStorage）
    updateSurveyAutoToggleUI();
    if (surveyAutoEnabled) startSurveyCountdown();
    // 视图由隐藏恢复后校正散点画布尺寸
    setTimeout(function () {
      if (surveyState.scatterChart && !surveyState.scatterChart.isDisposed()) {
        surveyState.scatterChart.resize();
      }
    }, 80);
    if (typeof loadGlobalAssets === 'function') { /* 事实层共用，不在此重复 */ }
  }
  window.initSurveyLayer = initSurveyLayer;

  function leaveSurveyLayer() {
    stopSurveyCountdown();
  }
  window.leaveSurveyLayer = leaveSurveyLayer;

  // ==========================================
  //  M7 · AI 主脑面板 —— 模块注册表 + 个性化布局
  //  单一事实来源：渲染顺序 / 设置项 / 开关全部由 BRAIN_MODULE_REGISTRY 生成
  // ==========================================
  var BRAIN_MODULES_KEY = 'dapeng_brain_modules';

  var BRAIN_NATURE_LABEL = {
    real: '真实',
    snapshot: '历史快照',
    aggregated: '聚合口径',
    local: '本地样本'
  };

  // 模块清单（defaultOrder 与 dashboard.html 中的 DOM 顺序一致）
  var BRAIN_MODULE_REGISTRY = [
    {
      id: 'index-strip', displayName: '主要指数实时行情', subtitle: '宽基指数涨跌与涨跌家数',
      resolve: function () { return document.getElementById('survey-index-strip'); },
      bodyEmptyId: 'survey-index-strip',
      dataSource: '腾讯行情 qt.gtimg.cn /api/intelligent/ak/index', refresh: '实时',
      nature: 'real', span: 12, closable: true, defaultOrder: 1, defaultCollapsed: false
    },
    {
      id: 'hero', displayName: '市场状态总览', subtitle: '结构分 / 情绪分 / 驱动要素',
      resolve: function () { return document.getElementById('survey-hero'); },
      bodyEmptyId: 'survey-hero-meta',
      dataSource: '全市场实时快照 /api/intelligent/market/survey', refresh: '60s',
      nature: 'real', span: 12, closable: true, defaultOrder: 2, defaultCollapsed: false
    },
    {
      id: 'brain', displayName: 'AI 主脑', subtitle: '研判 / 情报 / 内核运行态 / 任务调度',
      resolve: function () { return document.getElementById('brain-panel'); },
      bodyEmptyId: 'brain-kernel',
      dataSource: '主脑内核 + DeepSeek /api/brain/status', refresh: '60s',
      nature: 'real', span: 12, closable: true, defaultOrder: 3, defaultCollapsed: false
    },
    {
      id: 'macro', displayName: '宏观环境与 AI 综合评述', subtitle: '宏观指标 + 规则 / AI 评述',
      resolve: function () { return brainClosestPanel('survey-macro-grid'); },
      bodyEmptyId: 'survey-ai-synthesis',
      dataSource: 'AkShare 宏观 /api/intelligent/ak/macro', refresh: '60s',
      nature: 'real', span: 12, closable: true, defaultOrder: 4, defaultCollapsed: false
    },
    {
      id: 'rotation', displayName: '行业板块轮动', subtitle: '阶段识别 + 资金流入速率 + 龙头',
      resolve: function () { return brainClosestPanel('survey-rotation-legend'); },
      bodyEmptyId: 'survey-rotation-list',
      dataSource: '腾讯聚合板块行情 /api/intelligent/market/survey', refresh: '60s',
      nature: 'aggregated', span: 6, closable: true, defaultOrder: 5, defaultCollapsed: false
    },
    {
      id: 'alerts', displayName: '盘中大单与结构性异动预警', subtitle: '异动类型 / 级别 / 驱动要素',
      resolve: function () { return brainClosestPanel('survey-alerts-list'); },
      bodyEmptyId: 'survey-alerts-list',
      dataSource: '聚合行情异动检测 /api/intelligent/market/survey', refresh: '60s',
      nature: 'aggregated', span: 6, closable: true, defaultOrder: 6, defaultCollapsed: false
    },
    {
      id: 'rotation-flow', displayName: '板块资金迁移粒子图', subtitle: '相对强弱迁移 + 自动总结',
      resolve: function () { return document.getElementById('rot-flow-panel'); },
      bodyEmptyId: 'rotation-summary',
      dataSource: 'sector-ranking（东财聚合）/api/sector-ranking', refresh: '60s',
      nature: 'aggregated', span: 12, closable: true, defaultOrder: 7, defaultCollapsed: false
    },
    {
      id: 'news', displayName: '财经要闻、政策与公告', subtitle: '全网财经新闻流',
      resolve: function () { return brainClosestPanel('survey-news-list'); },
      bodyEmptyId: 'survey-news-list',
      dataSource: 'AkShare 要闻 /api/intelligent/ak/news', refresh: '60s',
      nature: 'real', span: 12, closable: true, defaultOrder: 8, defaultCollapsed: false
    },
    {
      // 独立区块：沪深港通「标的池」涨跌家数（聚合口径，非全市场）
      id: 'hsgt-breadth', displayName: '沪深港通成分股涨跌分布', subtitle: '沪深港通标的池涨跌家数（聚合口径）',
      resolve: function () { return document.getElementById('brain-mod-hsgt'); },
      bodyEmptyId: 'hsgt-breadth-body',
      dataSource: 'AkShare 沪深港通 /api/market/north', refresh: '60s',
      nature: 'aggregated', span: 12, closable: true, defaultOrder: 9, defaultCollapsed: false
    }
  ];

  // 三档布局预设（模块 id 子集 + 顺序）
  var BRAIN_PRESETS = {
    minimal: { label: '极简', ids: ['hero', 'brain', 'index-strip'] },
    standard: { label: '标准', ids: ['index-strip', 'hero', 'brain', 'macro', 'rotation', 'alerts', 'hsgt-breadth', 'news'] },
    full: { label: '全量', ids: ['index-strip', 'hero', 'brain', 'macro', 'rotation', 'alerts', 'rotation-flow', 'hsgt-breadth', 'news'] }
  };

  var brainState = {
    inited: false,
    container: null,
    nodes: {},          // id -> 栅格子元素（article 或 wrapper）
    config: null,
    dragId: null,
    autoCollapsed: {},  // id -> true（因取数失败自动折叠，恢复后自动展开，不写入持久化）
    settingsOpen: false
  };

  function gid(id) { return document.getElementById(id); }

  function brainClosestPanel(innerId) {
    var e = document.getElementById(innerId);
    return (e && e.closest) ? e.closest('.market-panel') : null;
  }

  function brainModuleById(id) {
    for (var i = 0; i < BRAIN_MODULE_REGISTRY.length; i++) {
      if (BRAIN_MODULE_REGISTRY[i].id === id) return BRAIN_MODULE_REGISTRY[i];
    }
    return null;
  }

  function _brainDefaultOrder() {
    return BRAIN_MODULE_REGISTRY.slice()
      .sort(function (a, b) { return a.defaultOrder - b.defaultOrder; })
      .map(function (m) { return m.id; });
  }

  function brainDefaultConfig() {
    var enabled = {}, collapsed = {};
    BRAIN_MODULE_REGISTRY.forEach(function (m) {
      enabled[m.id] = true;
      collapsed[m.id] = !!m.defaultCollapsed;
    });
    return { v: 1, preset: 'standard', order: _brainDefaultOrder(), enabled: enabled, collapsed: collapsed };
  }

  function brainReadConfig() {
    var base = brainDefaultConfig();
    var raw = null;
    try { raw = localStorage.getItem(BRAIN_MODULES_KEY); } catch (e) { raw = null; }
    if (!raw) return base;
    var cfg = null;
    try { cfg = JSON.parse(raw); } catch (e) { cfg = null; }
    if (!cfg || typeof cfg !== 'object') return base;
    // 合并：以默认结构为骨架，应用已保存值；忽略未知 id，新模块自动补入
    var ids = _brainDefaultOrder();
    var order = Array.isArray(cfg.order) ? cfg.order.filter(function (id) { return ids.indexOf(id) >= 0; }) : [];
    ids.forEach(function (id) { if (order.indexOf(id) < 0) order.push(id); });
    var enabled = {}, collapsed = {};
    BRAIN_MODULE_REGISTRY.forEach(function (m) {
      enabled[m.id] = (cfg.enabled && typeof cfg.enabled[m.id] === 'boolean') ? cfg.enabled[m.id] : true;
      collapsed[m.id] = (cfg.collapsed && typeof cfg.collapsed[m.id] === 'boolean') ? cfg.collapsed[m.id] : !!m.defaultCollapsed;
    });
    return { v: 1, preset: cfg.preset || 'custom', order: order, enabled: enabled, collapsed: collapsed };
  }

  function brainSaveConfig() {
    try { localStorage.setItem(BRAIN_MODULES_KEY, JSON.stringify(brainState.config)); } catch (e) { /* 忽略 */ }
  }

  function brainBuildHeader(mod, root, gridChild, hasHead) {
    var headEl;
    if (hasHead) {
      headEl = root.querySelector('.market-panel-head');
      headEl.classList.add('brain-mod-head');
      // 顺序：拖拽柄 → (原有标题) → 元信息 → 工具
      var grip = document.createElement('span');
      grip.className = 'bmh-grip';
      grip.title = '拖拽以调整顺序';
      grip.textContent = '⠿';
      headEl.insertBefore(grip, headEl.firstChild);
    } else {
      headEl = document.createElement('div');
      headEl.className = 'brain-mod-head';
      headEl.innerHTML =
        '<span class="bmh-grip" title="拖拽以调整顺序">⠿</span>' +
        '<strong class="bmh-title">' + escapeHtml(mod.displayName) + '</strong>' +
        '<span class="bmh-sub">' + escapeHtml(mod.subtitle) + '</span>';
      gridChild.insertBefore(headEl, gridChild.firstChild);
    }

    var meta = document.createElement('span');
    meta.className = 'bmh-meta';
    meta.innerHTML =
      '<span class="bm-chip bm-src" title="数据来源">' + escapeHtml(mod.dataSource) + '</span>' +
      '<span class="bm-chip bm-time" title="更新时间">更新于 --</span>' +
      '<span class="bm-nature n-' + mod.nature + '">' + escapeHtml(BRAIN_NATURE_LABEL[mod.nature] || mod.nature) + '</span>';
    headEl.appendChild(meta);

    var tools = document.createElement('span');
    tools.className = 'bm-tools';
    tools.innerHTML =
      '<button class="bm-btn bm-collapse" type="button" title="折叠 / 展开">▾</button>' +
      '<button class="bm-btn bm-close" type="button" title="关闭模块（可在布局设置中恢复）">✕</button>';
    headEl.appendChild(tools);

    var reason = document.createElement('span');
    reason.className = 'bm-reason';
    reason.hidden = true;
    headEl.appendChild(reason);

    // 事件绑定
    var gripEl = headEl.querySelector('.bmh-grip');
    tools.querySelector('.bm-collapse').addEventListener('click', function () { brainToggleCollapse(mod.id); });
    tools.querySelector('.bm-close').addEventListener('click', function () { brainToggleModule(mod.id); });

    gripEl.setAttribute('draggable', 'true');
    gripEl.addEventListener('dragstart', function (e) {
      brainState.dragId = mod.id;
      gridChild.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', mod.id); } catch (_) { /* 忽略 */ }
    });
    gripEl.addEventListener('dragend', function () {
      gridChild.classList.remove('dragging');
      brainState.dragId = null;
    });
    gridChild.addEventListener('dragover', function (e) {
      if (!brainState.dragId || brainState.dragId === mod.id) return;
      e.preventDefault();
      gridChild.classList.add('drag-over');
    });
    gridChild.addEventListener('dragleave', function () { gridChild.classList.remove('drag-over'); });
    gridChild.addEventListener('drop', function (e) {
      e.preventDefault();
      gridChild.classList.remove('drag-over');
      brainDropOn(mod.id);
    });
  }

  function brainBuildLayout() {
    var view = document.getElementById('survey-dashboard-view');
    if (!view) return false;
    var host = document.getElementById('brain-modules');
    if (!host) {
      host = document.createElement('div');
      host.id = 'brain-modules';
      host.className = 'brain-modules';
      var pageHead = view.querySelector('.market-page-head');
      if (pageHead) view.insertBefore(host, pageHead.nextSibling);
      else view.insertBefore(host, view.firstChild);
    }
    brainState.container = host;

    BRAIN_MODULE_REGISTRY.forEach(function (mod) {
      var root = mod.resolve();
      if (!root) return;
      var hasHead = !!root.querySelector('.market-panel-head');
      var gridChild;
      if (hasHead) {
        gridChild = root;
      } else {
        var wrap = document.createElement('div');
        wrap.className = 'brain-mod';
        root.parentNode.insertBefore(wrap, root);
        wrap.appendChild(root);
        gridChild = wrap;
      }
      gridChild.classList.add('brain-mod');
      if (mod.span === 6) gridChild.classList.add('span-6');
      gridChild.setAttribute('data-brain-mod', mod.id);
      brainBuildHeader(mod, root, gridChild, hasHead);
      if (gridChild.parentNode !== host) host.appendChild(gridChild);
      brainState.nodes[mod.id] = gridChild;
    });

    // 清掉被搬空的两列容器
    view.querySelectorAll('.survey-grid-split').forEach(function (s) {
      if (!s.children.length) s.parentNode.removeChild(s);
    });
    return true;
  }

  function brainInjectToolbar() {
    var view = document.getElementById('survey-dashboard-view');
    if (!view) return;
    var actions = view.querySelector('.market-page-head .market-head-actions');
    if (!actions || actions.querySelector('.brain-layout-btn')) return;
    var btn = document.createElement('button');
    btn.className = 'market-link-btn brain-layout-btn';
    btn.type = 'button';
    btn.title = '模块开关 / 排序 / 折叠 / 多档布局预设';
    btn.innerHTML = '⚙ 布局';
    btn.addEventListener('click', function () { brainToggleSettings(); });
    actions.insertBefore(btn, actions.firstChild);
  }

  function brainEnsureInit() {
    if (brainState.inited) return;
    if (!document.getElementById('survey-dashboard-view')) return;
    brainState.config = brainReadConfig();
    brainInjectToolbar();
    brainEnsureHsgtBlock();
    if (!brainBuildLayout()) return;
    brainState.inited = true;
    brainApplyConfig();
  }

  // 独立区块：沪深港通成分股涨跌分布（口径=沪深港通标的池聚合，明确区别于全市场宽度）
  function brainEnsureHsgtBlock() {
    var view = document.getElementById('survey-dashboard-view');
    if (!view || document.getElementById('brain-mod-hsgt')) return;
    var art = document.createElement('article');
    art.className = 'market-panel';
    art.id = 'brain-mod-hsgt';
    art.innerHTML =
      '<div class="market-panel-head">' +
        '<div><strong>沪深港通成分股涨跌分布</strong>' +
        '<span>沪股通 / 深股通标的池涨跌家数（聚合口径，非全市场）· 数据源 /api/market/north</span></div>' +
      '</div>' +
      '<div id="hsgt-breadth-body" class="hsgt-breadth">' +
        '<div class="skeleton skel-block" style="height:66px"></div>' +
      '</div>';
    view.appendChild(art);
  }

  async function loadHsgtBreadth() {
    var host = document.getElementById('hsgt-breadth-body');
    if (!host) return;
    try {
      var resp = await fetch('/api/market/north').then(function (r) { return r.json(); });
      if (!resp || !resp.ok || !resp.data) {
        brainMarkFailure('hsgt-breadth', '接口无数据 /api/market/north');
        return;
      }
      renderHsgtBreadth(resp.data);
      brainMarkOk('hsgt-breadth', (resp.update_time || '').slice(11) || _brainClock(), 'aggregated');
    } catch (e) {
      brainMarkFailure('hsgt-breadth', '接口不可用 /api/market/north（' + e.message + '）');
    }
  }

  // 仅渲染接口返回的原始计数，不计算任何派生指标（无比例/无合计）
  function renderHsgtBreadth(d) {
    var host = document.getElementById('hsgt-breadth-body');
    if (!host) return;
    function cell(label, v, cls) {
      return '<span class="hsgt-cell ' + cls + '"><i>' + label + '</i><b class="num">' + (v == null ? '--' : v) + '</b></span>';
    }
    function row(name, up, flat, down, idx) {
      var idxHtml = (idx && idx.name)
        ? '<span class="hsgt-idx">相关指数 ' + escapeHtml(idx.name) + ' ' +
          (Number(idx.chg) >= 0 ? '+' : '') + Number(idx.chg || 0).toFixed(2) + '%</span>'
        : '';
      return '<div class="hsgt-row">' +
               '<div class="hsgt-row-head"><strong>' + name + '</strong>' + idxHtml + '</div>' +
               '<div class="hsgt-cells">' +
                 cell('上涨', up, 'up') + cell('持平', flat, 'flat') + cell('下跌', down, 'dn') +
               '</div>' +
             '</div>';
    }
    var hu = d.hgt_up, hf = d.hgt_flat, hd = d.hgt_down;
    var su = d.sgt_up, sf = d.sgt_flat, sd = d.sgt_down;
    var idxs = d.indexes || [];
    function n(v) { return v == null ? '--' : v; }
    host.innerHTML =
      row('沪股通', hu, hf, hd, idxs[0]) +
      row('深股通', su, sf, sd, idxs[1]) +
      '<div class="hsgt-note">仅覆盖沪股通/深股通标的池（沪股通 上涨' + n(hu) + '/平' + n(hf) + '/跌' + n(hd) +
      '，深股通 上涨' + n(su) + '/平' + n(sf) + '/跌' + n(sd) +
      '，均为实测），非全市场涨跌家数；全市场口径暂不可得。</div>' +
      (d.trade_date ? '<div class="hsgt-date">交易日 ' + escapeHtml(d.trade_date) + '</div>' : '');
  }

  function brainApplyConfig() {
    var cfg = brainState.config, host = brainState.container;
    if (!cfg || !host) return;
    var seen = {};
    function place(id) {
      var el = brainState.nodes[id];
      if (!el) return;
      var mod = brainModuleById(id);
      var enabled = cfg.enabled[id] !== false;
      el.style.display = enabled ? '' : 'none';
      var collapsed = !!cfg.collapsed[id] || !!brainState.autoCollapsed[id];
      el.classList.toggle('is-collapsed', collapsed);
      var cb = el.querySelector('.bm-collapse');
      if (cb) cb.textContent = collapsed ? '▸' : '▾';
      if (mod && mod.span === 6) el.classList.add('span-6');
      host.appendChild(el);   // 按顺序重排 DOM
    }
    // 先按用户顺序排布，再兜底排布未列入 order 的模块（隐藏）
    cfg.order.forEach(function (id) { if (seen[id]) return; seen[id] = 1; place(id); });
    BRAIN_MODULE_REGISTRY.forEach(function (m) { if (seen[m.id]) return; seen[m.id] = 1; place(m.id); });
    // 布局变化后校正图表尺寸
    setTimeout(function () {
      if (surveyState.scatterChart && !surveyState.scatterChart.isDisposed()) surveyState.scatterChart.resize();
      if (rotFlowState.chart && !rotFlowState.chart.isDisposed()) rotFlowState.chart.resize();
    }, 60);
  }

  function brainSetNature(id, nature, updateTime) {
    var el = brainState.nodes[id];
    if (!el) return;
    var mod = brainModuleById(id);
    if (nature && BRAIN_NATURE_LABEL[nature]) {
      var nat = el.querySelector('.bm-nature');
      if (nat) { nat.className = 'bm-nature n-' + nature; nat.textContent = BRAIN_NATURE_LABEL[nature]; }
    }
    if (updateTime) {
      var t = el.querySelector('.bm-time');
      if (t) t.textContent = '更新于 ' + updateTime;
    } else if (!mod) { /* noop */ }
  }

  function brainMarkOk(id, updateTime, nature) {
    if (!brainState.inited) return;
    var el = brainState.nodes[id];
    if (!el) return;
    var reason = el.querySelector('.bm-reason');
    if (reason) { reason.hidden = true; reason.textContent = ''; }
    if (brainState.autoCollapsed[id]) {
      delete brainState.autoCollapsed[id];
      brainApplyConfig();
    }
    brainSetNature(id, nature || null, updateTime || null);
  }

  function brainMarkFailure(id, message) {
    if (!brainState.inited) return;
    var el = brainState.nodes[id];
    if (!el) return;
    var reason = el.querySelector('.bm-reason');
    if (reason) {
      reason.hidden = false;
      reason.textContent = '⚠ 数据不可用：' + message;
    }
    // 模块内同时给出空态提示（展开时可见）
    var mod = brainModuleById(id);
    if (mod && mod.bodyEmptyId) {
      var tgt = document.getElementById(mod.bodyEmptyId);
      if (tgt) tgt.innerHTML = '<div class="market-account-empty">' + escapeHtml(message) + '</div>';
    }
    brainState.autoCollapsed[id] = true;
    el.classList.add('is-collapsed');
    var cb = el.querySelector('.bm-collapse');
    if (cb) cb.textContent = '▸';
  }

  function brainToggleModule(id) {
    var cfg = brainState.config;
    if (!cfg) return;
    cfg.enabled[id] = !(cfg.enabled[id] !== false);
    cfg.preset = 'custom';
    brainSaveConfig();
    brainApplyConfig();
    brainRenderSettings();
    if (typeof showToast === 'function') {
      var m = brainModuleById(id);
      showToast('主脑布局', (m ? m.displayName : id) + (cfg.enabled[id] !== false ? ' 已显示' : ' 已隐藏'));
    }
  }

  function brainToggleCollapse(id) {
    var cfg = brainState.config;
    if (!cfg) return;
    cfg.collapsed[id] = !cfg.collapsed[id];
    if (brainState.autoCollapsed[id]) delete brainState.autoCollapsed[id];
    cfg.preset = cfg.preset === 'custom' ? 'custom' : cfg.preset;
    brainSaveConfig();
    brainApplyConfig();
    brainRenderSettings();
  }

  function brainDropOn(targetId) {
    var drag = brainState.dragId;
    if (!drag || drag === targetId) return;
    var order = brainState.config.order.slice();
    var from = order.indexOf(drag), to = order.indexOf(targetId);
    if (from < 0 || to < 0) return;
    order.splice(from, 1);
    order.splice(to, 0, drag);
    brainState.config.order = order;
    brainState.config.preset = 'custom';
    brainSaveConfig();
    brainApplyConfig();
    brainRenderSettings();
  }

  function brainSetPreset(name) {
    var preset = BRAIN_PRESETS[name];
    if (!preset) return;
    var cfg = brainDefaultConfig();
    cfg.preset = name;
    cfg.order = preset.ids.slice();
    BRAIN_MODULE_REGISTRY.forEach(function (m) {
      cfg.enabled[m.id] = preset.ids.indexOf(m.id) >= 0;
      cfg.collapsed[m.id] = false;
    });
    brainState.config = cfg;
    brainSaveConfig();
    brainApplyConfig();
    brainRenderSettings();
    if (typeof showToast === 'function') showToast('主脑布局', '已切换到「' + preset.label + '」预设');
  }

  function brainResetLayout() {
    try { localStorage.removeItem(BRAIN_MODULES_KEY); } catch (e) { /* 忽略 */ }
    brainState.config = brainDefaultConfig();
    brainApplyConfig();
    brainRenderSettings();
    if (typeof showToast === 'function') showToast('主脑布局', '已恢复出厂布局（并清除本地配置）');
  }

  function brainToggleSettings() {
    brainState.settingsOpen = !brainState.settingsOpen;
    brainRenderSettings();
  }

  function brainRenderSettings() {
    if (!brainState.inited) return;
    var panel = document.getElementById('brain-settings-panel');
    if (!panel) {
      panel = document.createElement('div');
      panel.id = 'brain-settings-panel';
      panel.className = 'brain-settings-panel';
      document.body.appendChild(panel);
    }
    panel.hidden = !brainState.settingsOpen;
    if (!brainState.settingsOpen) return;

    var cfg = brainState.config;
    var presetBtns = Object.keys(BRAIN_PRESETS).map(function (k) {
      return '<button type="button" class="bsp-preset' + (cfg.preset === k ? ' on' : '') +
        '" onclick="brainSetPreset(\'' + k + '\')">' + BRAIN_PRESETS[k].label + '</button>';
    }).join('');

    var items = cfg.order.map(function (id, i) {
      var m = brainModuleById(id);
      if (!m) return '';
      var en = cfg.enabled[id] !== false;
      var col = !!cfg.collapsed[id];
      return '<div class="bsp-item" data-bsp-mod="' + id + '">' +
               '<span class="bsp-grip" title="拖拽排序">⠿</span>' +
               '<input type="checkbox"' + (en ? ' checked' : '') + ' onchange="brainToggleModule(\'' + id + '\')" title="显示 / 隐藏">' +
               '<span class="bsp-name" title="' + escapeHtml(m.dataSource) + '">' + escapeHtml(m.displayName) + '</span>' +
               '<span class="bsp-nature n-' + m.nature + '">' + escapeHtml(BRAIN_NATURE_LABEL[m.nature] || m.nature) + '</span>' +
               '<button type="button" class="bsp-collapse-btn" onclick="brainToggleCollapse(\'' + id + '\')" title="折叠 / 展开">' + (col ? '展开' : '折叠') + '</button>' +
             '</div>';
    }).join('');

    panel.innerHTML =
      '<div class="bsp-head"><strong>🧠 主脑布局设置</strong><button type="button" class="bsp-close" onclick="brainToggleSettings()">✕</button></div>' +
      '<div class="bsp-section">布局预设</div>' +
      '<div class="bsp-presets">' + presetBtns + '</div>' +
      '<div class="bsp-section">模块开关 / 顺序 / 折叠（勾选后立即生效并持久化）</div>' +
      '<div class="bsp-list" id="bsp-list">' + items + '</div>' +
      '<div class="bsp-actions">' +
        '<button type="button" onclick="brainResetLayout()">恢复默认布局</button>' +
        '<button type="button" onclick="brainExpandAll()">全部展开</button>' +
      '</div>' +
      '<div class="bsp-hint">数据性质：真实 = 实时直采；历史快照 = 上次成功结果沿用；聚合口径 = 由第三方聚合行情推导；本地样本 = 本地缓存 / 样本兜底。</div>';

    brainBindSettingsDrag();
  }

  function brainExpandAll() {
    var cfg = brainState.config;
    if (!cfg) return;
    BRAIN_MODULE_REGISTRY.forEach(function (m) { cfg.collapsed[m.id] = false; });
    brainState.autoCollapsed = {};
    brainSaveConfig();
    brainApplyConfig();
    brainRenderSettings();
  }

  function brainBindSettingsDrag() {
    var list = document.getElementById('bsp-list');
    if (!list) return;
    var dragId = null;
    list.querySelectorAll('.bsp-item').forEach(function (row) {
      var id = row.getAttribute('data-bsp-mod');
      var grip = row.querySelector('.bsp-grip');
      grip.setAttribute('draggable', 'true');
      grip.addEventListener('dragstart', function (e) {
        dragId = id; row.style.opacity = '0.5';
        try { e.dataTransfer.setData('text/plain', id); } catch (_) { /* 忽略 */ }
      });
      grip.addEventListener('dragend', function () { row.style.opacity = ''; dragId = null; });
      row.addEventListener('dragover', function (e) { if (dragId && dragId !== id) e.preventDefault(); });
      row.addEventListener('drop', function (e) {
        e.preventDefault();
        if (!dragId || dragId === id) return;
        var order = brainState.config.order.slice();
        var from = order.indexOf(dragId), to = order.indexOf(id);
        if (from < 0 || to < 0) return;
        order.splice(from, 1); order.splice(to, 0, dragId);
        brainState.config.order = order;
        brainState.config.preset = 'custom';
        brainSaveConfig(); brainApplyConfig(); brainRenderSettings();
        dragId = null;
      });
    });
  }

  // ==========================================
  //  2. Qlib 因子挖掘与回测逻辑
  // ==========================================
  async function initQlibView() {
    if (!qlibState.metaLoaded) {
      try {
        var meta = await fetch('/api/intelligent/qlib/meta').then(function(r) { return r.json(); });
        if (meta && meta.ok && Array.isArray(meta.factors)) {
          qlibState.factors = meta.factors;
          var def = meta.defaults || {};
          qlibState.selectedFactorIds = (def.selected_factor_ids || []).slice();
          qlibState.weights = Object.assign({}, def.weights || {});
          qlibState.benchmark = def.benchmark || 'sh000300';
          qlibState.topK = def.top_k || 8;
          qlibState.startDate = def.start_date || '';
          qlibState.endDate = def.end_date || '';
          qlibState.metaLoaded = true;
        }
      } catch (e) {
        console.warn('Load qlib meta failed', e);
      }
    }
    if (qlibState.factors.length === 0) {
      try {
        var resp = await fetch('/api/intelligent/qlib/factors').then(function(r) { return r.json(); });
        if (resp && resp.ok && Array.isArray(resp.factors)) {
          qlibState.factors = resp.factors.map(function(f) {
            return {
              id: f.id, name: f.name, category: f.category,
              formula_desc: f.formula_desc || f.formula,
              available: f.available !== false,
              unavailable_reason: f.unavailable_reason || null,
              default_weight: f.default_weight || 0
            };
          });
          if (!qlibState.selectedFactorIds.length) {
            qlibState.selectedFactorIds = qlibState.factors
              .filter(function(f) { return f.available; }).map(function(f) { return f.id; });
          }
        }
      } catch (e2) {
        console.warn('Load qlib factors failed', e2);
      }
    }
    renderFactorChips();
    renderQlibParamBar();
    loadDynamicFactor();
    if (!qlibState.lastResult) {
      runQlibBacktest();
    } else {
      setTimeout(function() {
        if (qlibState.navChart && !qlibState.navChart.isDisposed()) qlibState.navChart.resize();
        if (qlibState.quantileChart && !qlibState.quantileChart.isDisposed()) qlibState.quantileChart.resize();
      }, 100);
    }
  }

  // 指标占位：后端在回测前不计算 IC/IR（返回 null），统一显示为「--」而非 "null"
  function statText(v) {
    if (v === null || v === undefined || v === '' ) return '--';
    if (typeof v === 'number' && !isFinite(v)) return '--';
    return String(v);
  }

  function statMissing(v) {
    return v === null || v === undefined || v === '' || (typeof v === 'number' && !isFinite(v));
  }

  // ---- V18 动效基础设施 ----
  function prefersReducedMotion() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  /**
   * KPI 数值滚动：从上次值平滑过渡到目标文本。
   * 保留原文本的前缀/符号/后缀与小数位（如 '+12.34%'、'-5.60%'、'0.42'），
   * 使用 tabular-nums 避免字宽抖动；连续调用会取消上一次动画（轮询刷新安全）。
   */
  function countUpKpi(el, targetText, opt) {
    if (!el) return;
    opt = opt || {};
    var m = String(targetText).match(/^([^0-9+-]*)([+-]?)(\d+(?:\.\d+)?)(.*)$/);
    if (!m || prefersReducedMotion()) { el.textContent = targetText; return; }

    var pre = m[1], sign = m[2], numStr = m[3], post = m[4];
    var to = parseFloat(numStr);
    if (!isFinite(to)) { el.textContent = targetText; return; }
    var dec = (numStr.split('.')[1] || '').length;
    var from = (typeof el._kpiVal === 'number') ? el._kpiVal : 0;
    el._kpiVal = to;

    if (el._kpiRaf) cancelAnimationFrame(el._kpiRaf);
    var dur = opt.duration || 620;
    var t0 = performance.now();
    function step(now) {
      var p = Math.min(1, (now - t0) / dur);
      var e = 1 - Math.pow(1 - p, 3);            // ease-out-cubic
      el.textContent = pre + sign + (from + (to - from) * e).toFixed(dec) + post;
      if (p < 1) {
        el._kpiRaf = requestAnimationFrame(step);
      } else {
        el.textContent = targetText;              // 收敛到精确目标，避免浮点尾差
        el._kpiRaf = null;
      }
    }
    el._kpiRaf = requestAnimationFrame(step);
  }

  // 骨架屏片段（复用既有 .skeleton 的 shimmer 动画）
  function skelCards(n, cls) {
    var out = '';
    for (var i = 0; i < n; i++) out += '<div class="skeleton ' + (cls || 'skel-card') + '"></div>';
    return out;
  }
  function skelLines(n) {
    var out = '';
    for (var i = 0; i < n; i++) out += '<div class="skeleton skel-line"></div>';
    return out;
  }

  function renderFactorChips() {
    var container = document.getElementById('qlib-factor-chips');
    if (!container) return;

    container.innerHTML = qlibState.factors.map(function(f) {
      var avail = (f.available !== false);
      var isSelected = qlibState.selectedFactorIds.indexOf(f.id) >= 0;
      var st = qlibState.factorStats[f.id] || {};
      var icVal = st.ic;           // 实算值或 undefined（未回测/不可用）
      var ricVal = st.rank_ic;
      var irVal = st.ir;
      var icNa = statMissing(icVal) ? ' na' : '';
      var ricNa = statMissing(ricVal) ? ' na' : '';
      var irNa = statMissing(irVal) ? ' na' : '';
      var icTitle = ' title="' + (avail ? '运行因子回测后由逐期截面实算' : ('不可用：' + escapeHtml(f.unavailable_reason || ''))) + '"';
      var cls = 'qlib-factor-card' + (isSelected ? ' selected' : '') + (avail ? '' : ' is-disabled');
      var onclick = avail ? ('onclick="toggleQlibFactor(\'' + f.id + '\')"') : '';
      var noteHtml = avail ? '' :
        '<div class="qfc-na-note">不可用：' + escapeHtml(f.unavailable_reason || '数据缺失') + '</div>';
      return '<div class="' + cls + '" ' + onclick + ' role="button" tabindex="0" aria-pressed="' + (isSelected ? 'true' : 'false') + '">' +
               '<div class="qfc-head">' +
                 '<strong class="qfc-name" title="' + escapeHtml(f.name) + '">' + escapeHtml(f.name) + '</strong>' +
                 '<span class="qfc-cat">' + escapeHtml(f.category || '') + '</span>' +
               '</div>' +
               '<div class="qfc-formula" title="' + escapeHtml(f.formula_desc || '') + '">' + escapeHtml(f.formula_desc || '') + '</div>' +
               '<div class="qfc-stats">' +
                 '<div class="fc-stat"><span class="fc-label">IC</span><b class="fc-val v-up' + icNa + '"' + icTitle + '>' + escapeHtml(statText(icVal)) + '</b></div>' +
                 '<div class="fc-stat"><span class="fc-label">Rank IC</span><b class="fc-val v-up' + ricNa + '"' + icTitle + '>' + escapeHtml(statText(ricVal)) + '</b></div>' +
                 '<div class="fc-stat"><span class="fc-label">IR</span><b class="fc-val v-gold' + irNa + '"' + icTitle + '>' + escapeHtml(statText(irVal)) + '</b></div>' +
               '</div>' +
               noteHtml +
             '</div>';
    }).join('');
  }

  // 参数控制条：真实可调的 权重 / 区间 / Top-K / 基准（改动即重跑）
  function renderQlibParamBar() {
    var host = document.getElementById('qlib-param-bar');
    if (!host) {
      var chips = document.getElementById('qlib-factor-chips');
      if (!chips || !chips.parentNode) return;
      host = document.createElement('div');
      host.id = 'qlib-param-bar';
      host.className = 'qlib-param-bar';
      host.style.cssText = 'display:flex;flex-wrap:wrap;gap:14px;align-items:flex-start;' +
        'padding:10px 12px;margin-top:8px;background:rgba(8,15,34,0.55);' +
        'border:1px solid rgba(80,140,255,0.15);border-radius:8px';
      chips.parentNode.insertBefore(host, chips.nextSibling);
    }
    var fOpts = qlibState.factors.filter(function(f) { return f.available !== false; });
    var bOpts = [{ id: 'sh000300', name: '沪深300' }, { id: 'sh000001', name: '上证指数' },
                 { id: 'sz399001', name: '深证成指' }, { id: 'sz399006', name: '创业板指' }];
    var weightRows = fOpts.map(function(f) {
      var w = (qlibState.weights[f.id] != null) ? qlibState.weights[f.id] : (f.default_weight || 0);
      var sel = qlibState.selectedFactorIds.indexOf(f.id) >= 0;
      return '<label style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--text)">' +
               '<input type="checkbox" class="qpb-wchk" data-fid="' + f.id + '"' + (sel ? ' checked' : '') + ' onchange="onQlibParamChange()">' +
               '<span title="' + escapeHtml(f.name) + '">' + escapeHtml(shortFactorName(f.name)) + '</span>' +
               '<input type="number" class="qpb-win" data-fid="' + f.id + '" min="0" max="1" step="0.05" value="' + w + '" onchange="onQlibParamChange()" style="width:56px">' +
             '</label>';
    }).join('');
    var bSelect = bOpts.map(function(b) {
      return '<option value="' + b.id + '"' + (b.id === qlibState.benchmark ? ' selected' : '') + '>' + b.name + '</option>';
    }).join('');
    var lbl = 'font-size:10px;color:var(--muted);display:block;margin-bottom:4px';
    var inp = 'background:rgba(0,0,0,0.3);border:1px solid rgba(80,140,255,0.2);color:var(--text);' +
      'border-radius:5px;padding:3px 6px;font-size:11px';
    host.innerHTML =
      '<div style="min-width:260px"><span style="' + lbl + '">因子权重 / 启用</span>' +
        '<div style="display:flex;flex-wrap:wrap;gap:10px">' + weightRows + '</div></div>' +
      '<div><span style="' + lbl + '">回测区间</span>' +
        '<input type="date" class="qpb-date" id="qlib-start-date" style="' + inp + '" value="' + (qlibState.startDate || '') + '" onchange="onQlibParamChange()">' +
        '<span style="color:var(--muted);margin:0 4px">~</span>' +
        '<input type="date" class="qpb-date" id="qlib-end-date" style="' + inp + '" value="' + (qlibState.endDate || '') + '" onchange="onQlibParamChange()">' +
      '</div>' +
      '<div><span style="' + lbl + '">基准 / Top-K</span>' +
        '<select class="qpb-select" id="qlib-benchmark" style="' + inp + '" onchange="onQlibParamChange()">' + bSelect + '</select>' +
        '<input type="number" class="qpb-topk" id="qlib-topk" min="1" max="20" style="' + inp + ';width:64px;margin-left:6px" value="' + (qlibState.topK || 8) + '" onchange="onQlibParamChange()">' +
      '</div>';
  }

  function onQlibParamChange() {
    var fOpts = qlibState.factors.filter(function(f) { return f.available !== false; });
    var sel = [];
    var w = {};
    fOpts.forEach(function(f) {
      var chk = document.querySelector('.qpb-wchk[data-fid="' + f.id + '"]');
      var winp = document.querySelector('.qpb-win[data-fid="' + f.id + '"]');
      if (chk && chk.checked) sel.push(f.id);
      if (winp) w[f.id] = parseFloat(winp.value) || 0;
    });
    if (!sel.length && fOpts.length) { sel = [fOpts[0].id]; }
    qlibState.selectedFactorIds = sel;
    qlibState.weights = w;
    var be = document.getElementById('qlib-benchmark');
    if (be) qlibState.benchmark = be.value;
    var tk = document.getElementById('qlib-topk');
    if (tk) qlibState.topK = parseInt(tk.value, 10) || 8;
    var sd = document.getElementById('qlib-start-date');
    if (sd) qlibState.startDate = sd.value;
    var ed = document.getElementById('qlib-end-date');
    if (ed) qlibState.endDate = ed.value;
    renderFactorChips();
    runQlibBacktest();
  }

  function toggleQlibFactor(factorId) {
    var f = null;
    qlibState.factors.forEach(function(x) { if (x.id === factorId) f = x; });
    if (f && f.available === false) {
      if (typeof showToast === 'function') showToast('Qlib 因子库', '该因子不可用：' + (f.unavailable_reason || '数据缺失'));
      return;
    }
    var idx = qlibState.selectedFactorIds.indexOf(factorId);
    if (idx >= 0) {
      if (qlibState.selectedFactorIds.length <= 1) {
        if (typeof showToast === 'function') showToast('Qlib 因子库', '至少需保留 1 个因子进行组合回测');
        return;
      }
      qlibState.selectedFactorIds.splice(idx, 1);
    } else {
      qlibState.selectedFactorIds.push(factorId);
    }
    renderFactorChips();
    renderQlibParamBar();
    runQlibBacktest();
  }

  async function runQlibBacktest() {
    if (qlibState.loading) return;
    qlibState.loading = true;
    var btn = document.getElementById('btn-run-qlib');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳ 计算回测中...';
    }

    try {
      var body = {
        factors: qlibState.selectedFactorIds,
        weights: qlibState.weights,
        benchmark: qlibState.benchmark || 'sh000300',
        top_k: qlibState.topK || 8
      };
      if (qlibState.startDate) body.start_date = qlibState.startDate;
      if (qlibState.endDate) body.end_date = qlibState.endDate;

      var resp = await fetch('/api/intelligent/qlib/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      }).then(function(r) { return r.json(); });

      if (resp && resp.ok) {
        qlibState.lastResult = resp;
        renderQlibResults(resp);
      } else if (resp) {
        renderQlibUnavailable(resp);
      }
    } catch (e) {
      console.error('Qlib backtest failed', e);
      if (typeof showToast === 'function') showToast('Qlib 回测', '回测计算异常: ' + e.message);
    } finally {
      qlibState.loading = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = '▶ 运行因子回测';
      }
    }
  }

  function renderQlibResults(data) {
    var m = data.metrics || {};
    // 缓存每因子实算 IC/rank_ic/ir，刷新因子卡
    if (data.factor_stats && typeof data.factor_stats === 'object') {
      qlibState.factorStats = data.factor_stats;
      renderFactorChips();
    }
    var elExcess = document.getElementById('qlib-kpi-excess');
    var elAnnual = document.getElementById('qlib-kpi-annual');
    var elSharpe = document.getElementById('qlib-kpi-sharpe');
    var elCalmar = document.getElementById('qlib-kpi-calmar');
    var elDD = document.getElementById('qlib-kpi-drawdown');
    var elVol = document.getElementById('qlib-kpi-vol');
    var elWin = document.getElementById('qlib-kpi-winrate');
    var elDays = document.getElementById('qlib-kpi-days');
    var elIc = document.getElementById('qlib-kpi-ic');
    var elIr = document.getElementById('qlib-kpi-ir');

    // V18：主数值（<strong>）走数字滚动；副文案（<small>）直接赋值。
    // statText 缺值返回「--」，countUpKpi 的数值正则不匹配时自动回落为纯文本。
    if (elExcess) countUpKpi(elExcess, statMissing(m.excess_return) ? '--' : ((Number(m.excess_return) >= 0 ? '+' : '') + m.excess_return + '%'));
    if (elSharpe) countUpKpi(elSharpe, statText(m.sharpe_ratio));
    if (elDD) countUpKpi(elDD, statMissing(m.max_drawdown) ? '--' : ('-' + m.max_drawdown + '%'));
    if (elWin) countUpKpi(elWin, statMissing(m.win_rate) ? '--' : (m.win_rate + '%'));
    if (elIc) countUpKpi(elIc, statText(m.combined_ic));
    if (elAnnual) elAnnual.textContent = '策略年化: ' + statText(m.annual_return) + '% | 基准: ' + statText(m.benchmark_return) + '%';
    if (elCalmar) elCalmar.textContent = '卡玛比率: ' + statText(m.calmar_ratio);
    if (elVol) elVol.textContent = '年化波动率: ' + statText(m.annual_volatility) + '%';
    if (elIr) elIr.textContent = '信息比率 IR: ' + statText(m.combined_ir) +
      (statMissing(m.combined_ir) ? '' : ' · IC期数 ' + (data.ic_series ? data.ic_series.length : 0));
    if (elDays) elDays.textContent = '样本天数: ' + statText(m.sample_days) + ' 天';

    // 参数告警 + 回测口径声明（诚实降级，不静默）
    renderQlibNotices(data);

    // Render ECharts NAV Curve
    renderQlibNavChart(data.chart_data);

    // Render Quantiles（横向柱状图，红涨绿跌）
    renderQlibQuantileChart(data.quantiles);

    // Render Top Stocks（可排序 + 因子迷你进度条）
    renderQlibTopTable(data.top_stocks);
  }

  function renderQlibUnavailable(data) {
    var wrap = document.getElementById('qlib-quantile-list');
    if (wrap) wrap.innerHTML = '<div class="market-account-empty">' + escapeHtml(data.message || '回测不可用') + '</div>';
    renderQlibNotices(data);
    if (typeof showToast === 'function') showToast('Qlib 回测', data.message || '当前数据不足以回测');
  }

  function renderQlibNotices(data) {
    var host = document.getElementById('qlib-notices');
    if (!host) {
      var views = document.getElementById('qlib-kpi-grid');
      if (!views || !views.parentNode) return;
      host = document.createElement('div');
      host.id = 'qlib-notices';
      host.className = 'qlib-notices';
      host.style.cssText = 'display:flex;flex-direction:column;gap:6px;margin:2px 0';
      views.parentNode.insertBefore(host, views);
    }
    var html = '';
    var warnings = data.param_warnings || [];
    var warnStyle = 'padding:8px 12px;border-radius:8px;font-size:11px;line-height:1.5;' +
      'background:rgba(255,170,60,0.08);border:1px solid rgba(255,170,60,0.28);color:#ffcf8a';
    var caveatStyle = 'padding:8px 12px;border-radius:8px;font-size:11px;line-height:1.5;' +
      'background:rgba(80,140,255,0.07);border:1px solid rgba(80,140,255,0.22);color:#b9c8ea';
    if (warnings.length) {
      html += '<div style="' + warnStyle + '"><strong>⚠ 参数提示</strong><ul style="margin:4px 0 0 16px;padding:0">' +
        warnings.map(function(w) { return '<li>' + escapeHtml(w) + '</li>'; }).join('') + '</ul></div>';
    }
    if (data.backtest_caveat) {
      html += '<div style="' + caveatStyle + '"><strong>ⓘ 回测口径声明</strong><div style="margin-top:3px">' + escapeHtml(data.backtest_caveat) + '</div></div>';
    }
    if (data.chart_data && data.chart_data.benchmark_reason) {
      html += '<div style="' + warnStyle + '"><strong>⚠ 基准不可用</strong><div style="margin-top:3px">' + escapeHtml(data.chart_data.benchmark_reason) + '（基准曲线置空，未写死净值）</div></div>';
    }
    host.innerHTML = html;
    host.style.display = html ? 'flex' : 'none';
  }

  // ==========================================
  //  2.2 五分位分层图 / Top 组合表（V16 可视化升级）
  // ==========================================
  function renderQlibQuantileChart(quantiles) {
    var wrap = document.getElementById('qlib-quantile-list');
    if (!wrap) return;

    if (!Array.isArray(quantiles) || !quantiles.length) {
      if (qlibState.quantileChart && !qlibState.quantileChart.isDisposed()) {
        qlibState.quantileChart.dispose();
        qlibState.quantileChart = null;
      }
      wrap.innerHTML = '<div class="market-account-empty">暂无五分位分层数据</div>';
      return;
    }

    if (typeof echarts === 'undefined') {
      // 降级：无 ECharts 环境时退回文本列表
      wrap.innerHTML = quantiles.map(function(q) {
        var v = (q.annual_return == null) ? 0 : parseFloat(q.annual_return);
        return '<div style="display:flex;justify-content:space-between;align-items:center;padding:7px 10px;background:rgba(0,0,0,0.3);border-radius:6px;border:1px solid rgba(80,140,255,0.08)">' +
                 '<span style="font-size:11px;font-weight:600;color:var(--text)">' + escapeHtml(q.group) + ' · n=' + (q.sample_count != null ? q.sample_count : '--') + '</span>' +
                 '<span style="font-family:\'SF Mono\',monospace;font-size:11px;font-weight:700;color:' + (v >= 0 ? 'var(--red)' : 'var(--green)') + '">' + (q.annual_return == null ? '--' : escapeHtml(String(q.annual_return)) + '%') + '</span>' +
               '</div>';
      }).join('');
      return;
    }

    // V18：仅在图表 DOM 不存在时重建容器，之后复用实例做合并更新以保留过渡动画
    var el = document.getElementById('qlib-quantile-chart');
    if (!el) {
      wrap.innerHTML = '<div id="qlib-quantile-chart" style="height:230px;width:100%"></div>';
      el = document.getElementById('qlib-quantile-chart');
      if (!el) return;
    }
    if (!qlibState.quantileChart || qlibState.quantileChart.isDisposed() ||
        qlibState.quantileChart.getDom() !== el) {
      if (qlibState.quantileChart && !qlibState.quantileChart.isDisposed()) qlibState.quantileChart.dispose();
      qlibState.quantileChart = echarts.init(el);
    }

    var groups = [];
    var vals = [];
    var counts = [];
    var ranges = [];
    quantiles.forEach(function(q) {
      groups.push(q.group);
      vals.push((q.annual_return == null) ? 0 : parseFloat(q.annual_return));
      counts.push(q.sample_count != null ? q.sample_count : '--');
      ranges.push((q.factor_low != null && q.factor_high != null)
        ? ('[' + Number(q.factor_low).toFixed(4) + ', ' + Number(q.factor_high).toFixed(4) + ']') : '--');
    });

    qlibState.quantileChart.setOption({
      animationDuration: 500,
      grid: { left: 8, right: 78, top: 10, bottom: 10, containLabel: true },
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(7,13,29,0.95)',
        borderColor: 'rgba(80,140,255,0.3)',
        textStyle: { color: '#eef2f7', fontSize: 11 },
        formatter: function(params) {
          var i = params.dataIndex;
          return escapeHtml(groups[i]) + '<br>年化收益：<b style="color:' + (vals[i] >= 0 ? '#ff6b87' : '#2ef0a0') + '">' + (vals[i] >= 0 ? '+' : '') + vals[i].toFixed(2) + '%</b><br>样本数：<b>' + escapeHtml(String(counts[i])) + '</b><br>因子区间：<b>' + escapeHtml(ranges[i]) + '</b>';
        }
      },
      xAxis: {
        type: 'value',
        axisLabel: { color: '#8b9ec2', fontSize: 9, formatter: function(v) { return v + '%'; } },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: 'rgba(80,140,255,0.08)', type: 'dashed' } }
      },
      yAxis: {
        type: 'category',
        data: groups,
        inverse: true,  // Q1 在顶部，与榜单阅读习惯一致
        axisLabel: { color: '#cdd7ee', fontSize: 10 },
        axisLine: { lineStyle: { color: 'rgba(80,140,255,0.15)' } },
        axisTick: { show: false }
      },
      series: [{
        type: 'bar',
        barWidth: 13,
        data: vals.map(function(v) {
          var pos = v >= 0;  // A股配色：红涨绿跌
          return {
            value: v,
            itemStyle: {
              borderRadius: pos ? [0, 4, 4, 0] : [4, 0, 0, 4],
              color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                { offset: 0, color: pos ? 'rgba(255,77,109,0.35)' : 'rgba(0,233,106,0.35)' },
                { offset: 1, color: pos ? '#ff4d6d' : '#00e96a' }
              ])
            },
            label: { color: pos ? '#ff6b87' : '#2ef0a0' }
          };
        }),
        label: {
          show: true,
          position: 'right',
          fontFamily: 'SF Mono, monospace',
          fontSize: 10,
          fontWeight: 700,
          formatter: function(params) {
            var i = params.dataIndex;
            return (vals[i] >= 0 ? '+' : '') + vals[i].toFixed(2) + '% · n=' + counts[i];
          }
        }
      }]
    });
  }

  // 四维因子迷你进度条（动态因子卡与 Top 组合表共用）
  function factorMiniHtml(label, val) {
    var has = (val != null && val !== '' && isFinite(Number(val)));
    var v = Number(val);
    var pct = has ? Math.max(0, Math.min(100, v)) : 0;
    return '<div class="factor-mini">' +
             '<span class="fm-label">' + escapeHtml(label) + '</span>' +
             '<div class="factor-bar"><i class="factor-bar-fill" style="width:' + pct.toFixed(0) + '%"></i></div>' +
             '<b class="fm-val' + (has ? '' : ' na') + '">' + (has ? v : '--') + '</b>' +
           '</div>';
  }

  // 操作信号排序权重：买入类 0 < 观望/持有 1 < 卖出/减仓 2
  function signalRank(s) {
    var t = String(s || '');
    if (t.indexOf('买') >= 0) return 0;
    if (t.indexOf('卖') >= 0 || t.indexOf('减') >= 0) return 2;
    return 1;
  }

  function sortQlibTop(key) {
    if (!qlibState.lastResult || !Array.isArray(qlibState.lastResult.top_stocks)) return;
    var st = qlibState.topSort;
    if (st.key === key) {
      st.dir = (st.dir === 'desc') ? 'asc' : 'desc';
    } else {
      st.key = key;
      st.dir = 'desc';
    }
    renderQlibTopTable(qlibState.lastResult.top_stocks);
  }

  // 由 factor_scores 列表渲染迷你进度条（只显示可用因子）
  function factorBarsHtml(list) {
    if (!Array.isArray(list) || !list.length) return '<span class="fm-na">--</span>';
    var avail = list.filter(function(f) { return f.available !== false && f.score != null; });
    if (!avail.length) return '<span class="fm-na">--</span>';
    return '<div class="factor-mini-grid">' + avail.map(function(f) {
      return factorMiniHtml(shortFactorName(f.name), f.score);
    }).join('') + '</div>';
  }

  function shortFactorName(name) {
    var n = String(name || '');
    return n.length > 6 ? n.substring(0, 6) : n;
  }

  function renderQlibTopTable(stocks) {
    var tbody = document.getElementById('qlib-top-stocks-tbody');
    if (!tbody) return;
    // 表头列名随因子库动态化（原写死「动量/突破/量价/均线」）
    var ths = document.querySelectorAll('#qlib-top-table thead th');
    if (ths && ths.length >= 5) {
      var names = qlibState.factors.filter(function(f) { return f.available !== false; })
        .map(function(f) { return shortFactorName(f.name); }).join('/');
      ths[4].textContent = '因子（' + (names || '--') + '）';
    }
    if (!Array.isArray(stocks) || !stocks.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:18px 0">暂无推荐组合数据</td></tr>';
      return;
    }

    var st = qlibState.topSort;
    var arr = stocks.slice();
    arr.sort(function(a, b) {
      if (st.key === 'signal') {
        var d = signalRank(a.signal) - signalRank(b.signal);
        if (d !== 0) return (st.dir === 'desc') ? d : -d;
        return (Number(b.score) || 0) - (Number(a.score) || 0);  // 同级按评分降序
      }
      var d2 = (Number(a.score) || 0) - (Number(b.score) || 0);
      return (st.dir === 'desc') ? -d2 : d2;
    });

    tbody.innerHTML = arr.map(function(s, idx) {
      var ch = s.chan || {};
      var barsHtml = factorBarsHtml(s.factor_scores);
      var chanHtml = (ch.trend || '--') + (ch.buy_point ? ' · ' + ch.buy_point : '') + (ch.sell_point ? ' · ' + ch.sell_point : '');
      var sig = String(s.signal || '--');

      return '<tr>' +
               '<td><span class="rank-badge rank-' + (idx + 1) + '">' + (idx + 1) + '</span></td>' +
               '<td style="font-family:\'SF Mono\',monospace">' + escapeHtml(s.code) + '</td>' +
               '<td><strong>' + escapeHtml(s.name || s.code) + '</strong></td>' +
               '<td style="font-weight:700;color:var(--green);font-family:\'SF Mono\',monospace">' + s.score + '</td>' +
               '<td>' + barsHtml + '</td>' +
               '<td style="font-size:10px;color:var(--purple)">' + escapeHtml(chanHtml) + '</td>' +
               '<td><span class="tag-status ' + (sig.indexOf('买') >= 0 ? 'status-buy' : sig.indexOf('卖') >= 0 ? 'status-warn' : 'status-ready') + '">' + escapeHtml(sig) + '</span></td>' +
             '</tr>';
    }).join('');

    // 同步表头排序指示器（▼ 降序 / ▲ 升序 / ↕ 未启用）
    ['score', 'signal'].forEach(function(k) {
      var ind = document.getElementById('qlib-sort-ind-' + k);
      if (!ind) return;
      if (st.key === k) {
        ind.innerHTML = (st.dir === 'desc') ? '&#9660;' : '&#9650;';
        ind.className = 'sort-ind on';
      } else {
        ind.innerHTML = '&#8645;';
        ind.className = 'sort-ind';
      }
    });
  }

  // ==========================================
  //  2.5 动态因子缠论实时选股
  // ==========================================
  async function loadDynamicFactor() {
    var host = document.getElementById('dynamic-factor-strip');
    if (!host) return;
    host.innerHTML = skelCards(6);
    try {
      var resp = await fetch('/api/intelligent/qlib/dynamic?limit=12').then(function(r) { return r.json(); });
      if (resp && resp.ok) {
        renderDynamicFactor(resp);
      } else {
        host.innerHTML = '<div class="market-account-empty">动态因子缠论暂不可用：' + escapeHtml((resp && resp.message) || '未知原因') + '</div>';
      }
    } catch (e) {
      host.innerHTML = '<div class="market-account-empty">动态因子缠论加载异常：' + escapeHtml(e.message) + '</div>';
    }
  }

  function renderDynamicFactor(data) {
    var host = document.getElementById('dynamic-factor-strip');
    var meta = document.getElementById('dynamic-factor-meta');
    if (!host) return;
    if (meta && data.update_time) {
      meta.textContent = '真实行情实时更新 · ' + data.update_time + ' · 覆盖 ' + (data.universe_size || 0) + ' 只流动性/异动标的';
    }
    var items = data.items || [];
    if (!items.length) {
      host.innerHTML = '<div class="market-account-empty">当前无有效选股结果</div>';
      return;
    }
    host.innerHTML = items.map(function(it) {
      var ch = it.chan || {};
      var hasBuy = !!ch.buy_point, hasSell = !!ch.sell_point;
      var badgeCls = hasBuy ? 'status-buy' : hasSell ? 'status-warn' : 'status-ready';
      var badgeTxt = hasBuy ? ch.buy_point : hasSell ? ch.sell_point : '观望';
      return '<div class="dyn-chan-card">' +
               '<div class="dyn-chan-head">' +
                 '<strong>' + escapeHtml(it.name) + '</strong>' +
                 '<span class="dyn-chan-score">' + it.score + '</span>' +
               '</div>' +
               '<div class="dfc-meta">' + escapeHtml(it.code) + ' · ' + escapeHtml(it.price) + ' · ' + (it.chg_pct >= 0 ? '+' : '') + it.chg_pct + '%</div>' +
               factorBarsHtml(it.factor_scores) +
               '<div class="dfc-foot">' +
                 '<span class="tag-status ' + badgeCls + '">' + escapeHtml(badgeTxt) + '</span>' +
                 '<span class="dfc-trend">' + escapeHtml(ch.trend || '--') + (ch.divergence ? (' · ' + (ch.divergence === 'bottom' ? '底背驰' : '顶背驰')) : '') + '</span>' +
               '</div>' +
             '</div>';
    }).join('');
  }

  function renderQlibNavChart(chartData) {
    var el = document.getElementById('qlib-nav-chart');
    if (!el || typeof echarts === 'undefined') return;

    // V18：复用实例做合并更新，让 ECharts 自行播放数据过渡动画。
    // 旧实现在每次刷新时 dispose + init —— 会把动画彻底打断，净值曲线硬跳变。
    if (!qlibState.navChart || qlibState.navChart.isDisposed() || qlibState.navChart.getDom() !== el) {
      if (qlibState.navChart && !qlibState.navChart.isDisposed()) qlibState.navChart.dispose();
      qlibState.navChart = echarts.init(el, null, { renderer: 'canvas' });
    }

    var dates = (chartData && chartData.dates) || [];
    var strat = (chartData && chartData.strategy) || [];
    var hasBench = !!(chartData && Array.isArray(chartData.benchmark) && chartData.benchmark.length);
    var bench = hasBench ? chartData.benchmark : [];
    var excess = (hasBench && chartData && Array.isArray(chartData.excess)) ? chartData.excess : [];

    var series = [
      {
        name: 'Qlib多因子策略',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: strat,
        lineStyle: { width: 2.2, color: '#00e96a' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(0,233,106,0.25)' },
            { offset: 1, color: 'rgba(0,233,106,0.01)' }
          ])
        }
      }
    ];
    // 基准不可用时（后端返回 null + reason）不绘制基准/超额线，前端如实展示"暂不可用"
    if (hasBench && bench.length === strat.length) {
      series.push({
        name: '基准指数',
        type: 'line', smooth: true, showSymbol: false, data: bench,
        lineStyle: { width: 1.5, color: '#8b9ec2', type: 'dashed' }
      });
      series.push({
        name: '纯超额Alpha',
        type: 'line', smooth: true, showSymbol: false, data: excess,
        lineStyle: { width: 1.8, color: '#a855f7' }
      });
    }

    var legendData = hasBench ? ['Qlib多因子策略', '基准指数', '纯超额Alpha'] : ['Qlib多因子策略'];

    var option = {
      animationDuration: 500,
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(7,13,29,0.95)',
        borderColor: 'rgba(80,140,255,0.3)',
        textStyle: { color: '#eef2f7', fontSize: 11 },
        valueFormatter: function(v) { return Number(v).toFixed(4); }
      },
      legend: {
        data: legendData,
        top: 2,
        right: 10,
        textStyle: { color: '#8b9ec2', fontSize: 10 }
      },
      grid: { left: 45, right: 20, top: 32, bottom: 54 },
      // 底部缩放条 + 内部滚轮缩放（长周期净值曲线可局部放大）
      dataZoom: [
        { type: 'inside', start: 0, end: 100 },
        {
          type: 'slider', height: 16, bottom: 6,
          borderColor: 'rgba(80,140,255,0.2)',
          backgroundColor: 'rgba(8,15,34,0.6)',
          fillerColor: 'rgba(6,182,212,0.14)',
          handleStyle: { color: '#06b6d4', borderColor: '#06b6d4' },
          moveHandleStyle: { color: 'rgba(6,182,212,0.5)' },
          textStyle: { color: '#8b9ec2', fontSize: 9 },
          dataBackground: {
            lineStyle: { color: 'rgba(80,140,255,0.35)' },
            areaStyle: { color: 'rgba(80,140,255,0.12)' }
          },
          selectedDataBackground: {
            lineStyle: { color: '#06b6d4' },
            areaStyle: { color: 'rgba(6,182,212,0.18)' }
          }
        }
      ],
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: dates,
        axisLabel: { color: '#8b9ec2', fontSize: 9, hideOverlap: true },
        axisLine: { lineStyle: { color: 'rgba(80,140,255,0.15)' } }
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { color: '#8b9ec2', fontSize: 9 },
        splitLine: { lineStyle: { color: 'rgba(80,140,255,0.08)', type: 'dashed' } }
      },
      series: series
    };

    qlibState.navChart.setOption(option);
  }

  // ==========================================
  //  3. 动态市场调查与板块轮动逻辑
  // ==========================================
  async function loadMarketSurveyData() {
    if (surveyState.loading) return;
    surveyState.loading = true;
    brainEnsureInit();

    try {
      var [surveyResp, macroResp, newsResp] = await Promise.all([
        fetch('/api/intelligent/market/survey').then(function(r) { return r.json(); }),
        fetch('/api/intelligent/ak/macro').then(function(r) { return r.json(); }),
        fetch('/api/intelligent/ak/news?limit=10').then(function(r) { return r.json(); })
      ]);

      if (surveyResp && surveyResp.ok) {
        renderSurveyData(surveyResp, macroResp, newsResp);
        loadHsgtBreadth();
      } else {
        brainMarkFailure('hero', '调查接口返回异常 /api/intelligent/market/survey');
        brainMarkFailure('rotation', '调查接口返回异常 /api/intelligent/market/survey');
        brainMarkFailure('alerts', '调查接口返回异常 /api/intelligent/market/survey');
      }
      loadBrainStatus();
    } catch (e) {
      console.error('Fetch survey data failed', e);
      brainMarkFailure('hero', '接口不可用 /api/intelligent/market/survey（' + e.message + '）');
      brainMarkFailure('rotation', '接口不可用 /api/intelligent/market/survey');
      brainMarkFailure('alerts', '接口不可用 /api/intelligent/market/survey');
      brainMarkFailure('macro', '接口不可用 /api/intelligent/ak/macro');
      brainMarkFailure('news', '接口不可用 /api/intelligent/ak/news');
      if (typeof showToast === 'function') showToast('市场调查', '数据加载异常: ' + e.message);
    } finally {
      surveyState.loading = false;
    }
  }

  function renderSurveyHero(survey) {
    var regime = survey.regime || {};
    var status = regime.status || '数据不足';
    var score = regime.score;
    var stateEl = document.getElementById('survey-hero-state');
    var scoreEl = document.getElementById('survey-hero-score');
    var metaEl = document.getElementById('survey-hero-meta');
    var srcEl = document.getElementById('survey-hero-source');

    // 状态着色：强势类→红，弱势类→绿，震荡/中性→蓝
    var stateCls = 'flat';
    if (/强势|上行/.test(status)) stateCls = 'strong';
    else if (/弱势|下行|偏弱/.test(status)) stateCls = 'weak';
    else if (/不足/.test(status)) stateCls = 'flat';

    if (stateEl) {
      stateEl.textContent = status;
      stateEl.className = 'survey-hero-state ' + stateCls;
    }
    if (scoreEl) {
      // 口径归位：regime.score 是**结构分**（由宽度/指数/涨跌停合成），
      // 与事实层 sentiment.v1 的「情绪分」不是同一量纲，不能都叫情绪分。
      var senti = survey.sentiment || {};
      var sentiText = (senti.available && senti.level != null)
        ? (' · 情绪分 ' + Math.round(senti.level) + (senti.band ? '（' + senti.band + '）' : ''))
        : '';
      var confText = (regime.confidence != null) ? (' · 置信度 ' + regime.confidence + '%') : '';
      scoreEl.textContent = (score != null ? ('结构分 ' + Math.round(score)) : '结构分 --') + sentiText + confText;
    }
    if (metaEl) {
      var parts = [];
      if (regime.liquidity_env) parts.push(regime.liquidity_env);
      if (regime.main_driver) parts.push(regime.main_driver);
      var alerts = survey.alerts || [];
      if (alerts.length) parts.push('异动 ' + alerts.length + ' 条');
      metaEl.textContent = parts.length ? parts.join(' · ') : '正在采集全市场实时数据…';
    }
    if (srcEl) {
      srcEl.textContent = (survey.ai_powered ? 'AI 生成' : '规则引擎') + (survey.data_source === 'realtime' ? ' · 实时数据' : ' · 数据降级');
    }
  }

  function renderSurveyData(survey, macro, news) {
    surveyState.lastData = survey;
    var timeEl = document.getElementById('survey-update-time');
    if (timeEl) timeEl.textContent = '更新于 ' + (survey.update_time || '--');

    // P0 市场状态总览 Hero
    renderSurveyHero(survey);
    // 头部数据性质：realtime → 真实；degraded → 历史快照（如实降级）
    brainMarkOk('hero', survey.update_time,
      survey.data_source === 'realtime' ? 'real' : 'snapshot');

    // 宏观指标卡（统一 .macro-card，红涨绿跌）
    //
    // 【纵深防御 · 用户红线「不许填补空数据」】
    // 后端 /api/intelligent/ak/macro 在实时源不可用时**不再产生** source==='fallback' 的
    // 示例数值（改为一律 null + reason）。前端这里做**防御性丢弃**：任何带降级标记
    // （source 为 fallback/example/sample/synthetic）的条目**直接剔除，不让它进入 DOM**，
    // 以免后端哪天回归时把编造的宏观读数（如「PMI 50.4%」）当成真实值画上屏。
    // 剔除后若无任何真实条目 → 走空态「数据源不可用 + 原因」，绝不显示数字。
    var macroGrid = document.getElementById('survey-macro-grid');
    var MACRO_DEGRADED_SOURCES = ['fallback', 'example', 'sample', 'synthetic'];
    var macroRows = (macro && macro.ok && Array.isArray(macro.data))
      ? macro.data.filter(function(item) {
          var s = String((item && item.source) || '').toLowerCase();
          return MACRO_DEGRADED_SOURCES.indexOf(s) < 0;
        })
      : [];
    if (macroGrid && macroRows.length) {
      macroGrid.innerHTML = macroRows.map(function(item) {
        var isUp = item.trend === 'up';
        return '<div class="macro-card">' +
                 '<div class="mc-name">' + escapeHtml(item.name) + '</div>' +
                 '<div class="mc-val-row">' +
                   '<span class="mc-val ' + (isUp ? 'v-up' : 'v-dn') + '">' + escapeHtml(item.value) + '</span>' +
                   '<span class="mc-badge mc-badge-real" title="来源：EastMoney Datacenter 实时">实时</span>' +
                 '</div>' +
                 '<div class="mc-desc">' + escapeHtml(item.status) + ' &bull; ' + escapeHtml(item.desc) + '</div>' +
               '</div>';
      }).join('');
      // 已剔除降级条目 → 剩下的都是实时源，nature 恒为 real。
      brainMarkOk('macro', macro.update_time || null, 'real');
    } else if (macroGrid) {
      // 无数据 / ok:false / 全部条目均为降级被剔除：明示「数据源不可用 + 原因」，不显示成「暂无」
      var macroWhy = (macro && (macro.reason || macro.message)) || '未返回数据';
      macroGrid.innerHTML = '<div class="market-account-empty">宏观数据源不可用：/api/intelligent/ak/macro（' +
        escapeHtml(macroWhy) + '）</div>';
      brainMarkFailure('macro', '数据源不可用 /api/intelligent/ak/macro（' + macroWhy + '）');
    } else {
      brainMarkFailure('macro', '数据源不可用 /api/intelligent/ak/macro');
    }

    // AI Natural Language Synthesis
    var synthEl = document.getElementById('survey-ai-synthesis');
    var statusEl = document.getElementById('survey-regime-status');
    if (synthEl) synthEl.textContent = survey.synthesis || '--';
    if (statusEl && survey.regime) {
      statusEl.textContent = '状态识别：' + survey.regime.status + ' (置信度: ' + survey.regime.confidence + '%)' +
        (survey.ai_powered ? ' · DeepSeek AI 生成' : ' · 规则引擎生成') +
        (survey.data_source === 'realtime' ? ' · 实时数据' : ' · 数据降级');
    }

    // Sector Rotation（散点图 + 联动列表）
    renderRotationScatter(survey.scatter, survey.rotation);
    var rotList = document.getElementById('survey-rotation-list');
    if (rotList && Array.isArray(survey.rotation)) {
      rotList.innerHTML = survey.rotation.map(function(r) {
        var stageCls = r.stage.indexOf('主升') >= 0 ? 'status-buy' : r.stage.indexOf('蓄势') >= 0 ? 'status-ready' : 'status-warn';
        return '<div class="rot-row" data-sector="' + escapeHtml(r.sector) + '" tabindex="0" role="button" title="点击联动散点图">' +
                 '<div class="rot-row-main">' +
                   '<strong class="rot-name">' + escapeHtml(r.sector) + '</strong>' +
                   '<span class="tag-status ' + stageCls + '">' + escapeHtml(r.stage) + '</span>' +
                 '</div>' +
                 '<div class="rot-flow">' + escapeHtml(r.flow_in) + '</div>' +
                 '<div class="rot-leader">龙头: ' + escapeHtml(r.leading_stock) + '</div>' +
                 (r.catalyst ? '<div class="rot-catalyst">' + escapeHtml(r.catalyst) + '</div>' : '') +
               '</div>';
      }).join('');
      // 行级交互：点击选中（联动散点），悬停高亮对应散点
      rotList.querySelectorAll('.rot-row').forEach(function(row) {
        row.addEventListener('click', function() {
          selectRotationSector(row.getAttribute('data-sector'), 'list');
        });
        row.addEventListener('keydown', function(ev) {
          if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); selectRotationSector(row.getAttribute('data-sector'), 'list'); }
        });
        row.addEventListener('mouseenter', function() {
          var idx = surveyState.sectorIndex[row.getAttribute('data-sector')];
          if (idx != null && surveyState.scatterChart) {
            surveyState.scatterChart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: idx });
          }
        });
        row.addEventListener('mouseleave', function() {
          var idx = surveyState.sectorIndex[row.getAttribute('data-sector')];
          if (idx != null && surveyState.scatterChart) {
            surveyState.scatterChart.dispatchAction({ type: 'downplay', seriesIndex: 0, dataIndex: idx });
          }
        });
      });
      // 数据刷新后恢复联动选中态
      if (surveyState.selectedSector) syncSelectionUI();
      brainMarkOk('rotation', survey.update_time, 'aggregated');
    } else {
      brainMarkFailure('rotation', '调查接口无板块轮动数据');
    }

    // 异动预警（筛选 chips + 计数 + 列表，客户端过滤）
    surveyState.alerts = Array.isArray(survey.alerts) ? survey.alerts : [];
    // 数据刷新后类型集合可能变化，失效的 type 筛选回落为「全部」
    if (surveyState.alertFilter.indexOf('type:') === 0) {
      var keepType = surveyState.alertFilter.slice(5);
      if (!surveyState.alerts.some(function(a) { return a.type === keepType; })) {
        surveyState.alertFilter = 'all';
      }
    }
    renderAlertChips();
    renderAlertList();
    brainMarkOk('alerts', survey.update_time, 'aggregated');

    // 财经要闻（标签 tabs + 列表，客户端过滤）
    // 区分「数据源故障（ok:false / 空数组 + reason）」与「正常空列表」：
    // 前者保留原因字符串，渲染时明示「数据源不可用 + 原因」，不当成「暂无」。
    if (news && news.ok && Array.isArray(news.data)) {
      surveyState.news = news.data;
      surveyState.newsUnavailable = null;
    } else {
      surveyState.news = [];
      surveyState.newsUnavailable = (news && (news.message || news.detail)) ||
        '数据源不可用 /api/intelligent/ak/news（返回 ok:false 或空数据）';
    }
    if (surveyState.newsFilter !== 'all' &&
        !surveyState.news.some(function(n) { return n.tag === surveyState.newsFilter; })) {
      surveyState.newsFilter = 'all';
    }
    // 先标记模块性质（brainMarkFailure 会写 bodyEmptyId），再渲染列表以保留更清晰的空态文案
    if (surveyState.news.length) brainMarkOk('news', (news && news.update_time) || null, 'real');
    else brainMarkFailure('news', surveyState.newsUnavailable || '数据源不可用 /api/intelligent/ak/news');
    renderNewsTabs();
    renderNewsList();

    // 顶部倒计时/开关状态同步
    updateSurveyAutoToggleUI();
    updateSurveyCountdownUI();
  }

  // ==========================================
  //  3.2 调查视图：自动轮询倒计时 / 筛选 chips / 阶段图例（V16）
  // ==========================================

  function _readAutoRefreshPref() {
    try {
      var v = localStorage.getItem(SURVEY_AUTOREFRESH_KEY);
      return v === null ? true : v === '1';
    } catch (e) {
      return true;   // 隐私模式下 localStorage 不可用，回落为默认开启
    }
  }

  function _writeAutoRefreshPref(on) {
    try { localStorage.setItem(SURVEY_AUTOREFRESH_KEY, on ? '1' : '0'); } catch (e) { /* 忽略 */ }
  }

  function _surveyViewActive() {
    // 合并进决策台后，判断层不再是独立视图：以分段是否激活为准
    var seg = document.querySelector('[data-hub-panel="survey"]');
    return !!seg && seg.classList.contains('is-active');
  }

  function updateSurveyAutoToggleUI() {
    var sw = document.getElementById('survey-autorefresh-toggle');
    if (!sw) return;
    sw.classList.toggle('on', surveyAutoEnabled);
    sw.setAttribute('aria-checked', surveyAutoEnabled ? 'true' : 'false');
  }

  function updateSurveyCountdownUI() {
    var el = document.getElementById('survey-countdown');
    if (!el) return;
    if (!surveyAutoEnabled) {
      el.className = 'survey-countdown paused';
      el.textContent = '已暂停';
      el.title = '自动轮询已关闭，点击「刷新调查」手动更新';
      return;
    }
    el.className = 'survey-countdown';
    el.textContent = surveyCountdownSec + 's';
    el.title = surveyCountdownSec + ' 秒后自动刷新调查数据';
  }

  // 启动倒计时：仅在调查视图可见且页面未隐藏时递减，到点静默刷新
  function startSurveyCountdown() {
    stopSurveyCountdown();
    surveyCountdownSec = SURVEY_POLL_SEC;
    updateSurveyCountdownUI();
    surveyTickTimer = setInterval(function() {
      if (!_surveyViewActive() || document.hidden) return;
      if (!surveyAutoEnabled) { updateSurveyCountdownUI(); return; }
      surveyCountdownSec -= 1;
      if (surveyCountdownSec <= 0) {
        surveyCountdownSec = SURVEY_POLL_SEC;
        loadMarketSurveyData();   // 内部有 loading 守卫，静默刷新不闪占位
        loadSurveyIndexStrip();
        loadRotationFlow();
      }
      updateSurveyCountdownUI();
    }, 1000);
  }

  function stopSurveyCountdown() {
    if (surveyTickTimer) {
      clearInterval(surveyTickTimer);
      surveyTickTimer = null;
    }
  }

  // 供 dashboard.html 的 onclick="toggleSurveyAutorefresh()" 调用
  function toggleSurveyAutorefresh() {
    surveyAutoEnabled = !surveyAutoEnabled;
    _writeAutoRefreshPref(surveyAutoEnabled);
    updateSurveyAutoToggleUI();
    if (surveyAutoEnabled) {
      surveyCountdownSec = SURVEY_POLL_SEC;
      if (_surveyViewActive()) startSurveyCountdown();
    } else {
      stopSurveyCountdown();
    }
    updateSurveyCountdownUI();
    if (typeof showToast === 'function') {
      showToast('市场调查', surveyAutoEnabled ? '已开启 60 秒自动刷新' : '已暂停自动刷新');
    }
  }

  // ---- 异动预警：级别 / 类型双维度筛选 ----
  function renderAlertChips() {
    var host = document.getElementById('survey-alert-filters');
    if (!host) return;
    var alerts = surveyState.alerts || [];
    var counts = { all: alerts.length, high: 0, medium: 0, low: 0 };
    var types = [];
    alerts.forEach(function(a) {
      if (counts[a.level] != null) counts[a.level] += 1;
      if (a.type && types.indexOf(a.type) < 0) types.push(a.type);
    });

    var levelDefs = [
      { key: 'all', label: '全部' },
      { key: 'high', label: '高风险' },
      { key: 'medium', label: '中风险' },
      { key: 'low', label: '低风险' }
    ];
    var html = levelDefs.map(function(l) {
      var on = surveyState.alertFilter === l.key;
      return '<button class="chip' + (on ? ' active' : '') + '" data-key="' + l.key + '" role="tab" aria-selected="' + (on ? 'true' : 'false') + '">' +
             l.label + '<b>' + counts[l.key] + '</b></button>';
    }).join('');
    html += types.map(function(t) {
      var key = 'type:' + t;
      var n = alerts.filter(function(a) { return a.type === t; }).length;
      var on = surveyState.alertFilter === key;
      return '<button class="chip' + (on ? ' active' : '') + '" data-key="' + escapeHtml(key) + '" role="tab" aria-selected="' + (on ? 'true' : 'false') + '">' +
             escapeHtml(t) + '<b>' + n + '</b></button>';
    }).join('');
    host.innerHTML = html;

    // 事件绑定而非内联 onclick，规避引号转义陷阱
    host.querySelectorAll('.chip').forEach(function(btn) {
      btn.addEventListener('click', function() { filterAlerts(btn.getAttribute('data-key')); });
    });
  }

  function filterAlerts(key) {
    surveyState.alertFilter = key || 'all';
    renderAlertChips();
    renderAlertList();
  }

  function _alertMatches(a) {
    var f = surveyState.alertFilter;
    if (f === 'all') return true;
    if (f.indexOf('type:') === 0) return a.type === f.slice(5);
    return a.level === f;
  }

  function renderAlertList() {
    var host = document.getElementById('survey-alerts-list');
    if (!host) return;
    var all = surveyState.alerts || [];
    var rows = all.filter(_alertMatches);
    var cntEl = document.getElementById('survey-alert-count');
    if (cntEl) cntEl.textContent = rows.length + ' 条' + (rows.length !== all.length ? ' / ' + all.length : '');
    if (!rows.length) {
      host.innerHTML = '<div class="market-account-empty">当前筛选条件下暂无异动预警</div>';
      return;
    }
    host.innerHTML = rows.map(function(a) {
      var lvl = (a.level === 'high' || a.level === 'medium' || a.level === 'low') ? a.level : 'low';
      return '<div class="alert-item lvl-' + lvl + '">' +
               '<div class="ai-top">' +
                 '<span class="ai-type">[' + escapeHtml(a.type) + '] ' + escapeHtml(a.name) + ' (' + escapeHtml(a.code) + ')</span>' +
                 '<span class="ai-time">' + escapeHtml(a.time) + '</span>' +
               '</div>' +
               '<div class="ai-desc">' + escapeHtml(a.desc) + '</div>' +
             '</div>';
    }).join('');
  }

  // ---- 财经要闻：按标签筛选 ----
  function renderNewsTabs() {
    var host = document.getElementById('survey-news-tabs');
    if (!host) return;
    var news = surveyState.news || [];
    var tags = [];
    news.forEach(function(n) { if (n.tag && tags.indexOf(n.tag) < 0) tags.push(n.tag); });

    var items = [{ key: 'all', label: '全部', n: news.length }].concat(tags.map(function(t) {
      return { key: t, label: t, n: news.filter(function(x) { return x.tag === t; }).length };
    }));
    host.innerHTML = items.map(function(it) {
      var on = surveyState.newsFilter === it.key;
      return '<button class="chip' + (on ? ' active' : '') + '" data-key="' + escapeHtml(it.key) + '" role="tab" aria-selected="' + (on ? 'true' : 'false') + '">' +
             escapeHtml(it.label) + '<b>' + it.n + '</b></button>';
    }).join('');

    host.querySelectorAll('.chip').forEach(function(btn) {
      btn.addEventListener('click', function() { filterNews(btn.getAttribute('data-key')); });
    });
  }

  function filterNews(key) {
    surveyState.newsFilter = key || 'all';
    renderNewsTabs();
    renderNewsList();
  }

  function renderNewsList() {
    var host = document.getElementById('survey-news-list');
    if (!host) return;
    // 数据源故障优先：明示「数据源不可用 + 原因」，不渲染成「该标签下暂无新闻」
    if (surveyState.newsUnavailable) {
      host.innerHTML = '<div class="market-account-empty">数据源不可用：' + escapeHtml(surveyState.newsUnavailable) + '</div>';
      return;
    }
    var all = surveyState.news || [];
    var f = surveyState.newsFilter;
    var rows = (f === 'all') ? all : all.filter(function(n) { return n.tag === f; });
    if (!rows.length) {
      host.innerHTML = '<div class="market-account-empty">该标签下暂无新闻</div>';
      return;
    }
    host.innerHTML = rows.map(function(n) {
      return '<div class="news-item">' +
               '<div class="ni-top">' +
                 '<span class="ni-tag">' + escapeHtml(n.tag) + ' &bull; ' + escapeHtml(n.source) + '</span>' +
                 '<span class="ni-time">' + escapeHtml(n.time) + '</span>' +
               '</div>' +
               '<a class="ni-title" href="' + (n.url || 'javascript:void(0)') + '" target="_blank" rel="noopener">' + escapeHtml(n.title) + '</a>' +
               (n.digest ? '<div class="ni-digest">' + escapeHtml(n.digest) + '</div>' : '') +
             '</div>';
    }).join('');
  }

  // ---- 板块轮动：阶段图例（点击 solo 高亮同阶段散点） ----
  function renderStageLegend() {
    var host = document.getElementById('survey-rotation-legend');
    if (!host) return;
    var counts = surveyState.stageCounts || {};
    var stages = Object.keys(counts);
    if (!stages.length) { host.innerHTML = ''; return; }
    host.innerHTML = stages.map(function(s) {
      var on = surveyState.stageSolo === s;
      return '<button class="stage-chip' + (on ? ' active' : '') + '" data-stage="' + escapeHtml(s) + '" style="--c:' + stageColor(s) + '" ' +
             'title="点击高亮该阶段板块，再次点击取消">' +
             '<i></i>' + escapeHtml(s) + '<b>' + counts[s] + '</b></button>';
    }).join('');

    host.querySelectorAll('.stage-chip').forEach(function(btn) {
      btn.addEventListener('click', function() { selectRotationStage(btn.getAttribute('data-stage')); });
    });
  }

  function selectRotationStage(stage) {
    surveyState.stageSolo = (surveyState.stageSolo === stage) ? null : stage;
    renderStageLegend();
    // 重绘散点以应用/取消淡化（原始数据已缓存于 surveyState）
    renderRotationScatter(surveyState.rawScatter, surveyState.rawRotation);
  }

  // ==========================================
  //  3.3 指数实时条（接入 /api/intelligent/ak/index）
  // ==========================================
  // 该端点此前后端可用但前端从未接线；含 6 个宽基指数 + 涨跌家数分布。
  async function loadSurveyIndexStrip() {
    var host = document.getElementById('survey-index-strip');
    if (!host) return;
    try {
      var resp = await fetch('/api/intelligent/ak/index').then(function (r) { return r.json(); });
      var rows = (resp && resp.ok && Array.isArray(resp.data)) ? resp.data : [];
      if (!rows.length) {
        var why = (resp && (resp.message || resp.breadth_reason)) || '接口返回空 / /api/intelligent/ak/index 不可达';
        host.innerHTML = '<div class="market-account-empty">指数数据源不可用：' + escapeHtml(why) + '</div>';
        brainMarkFailure('index-strip', '数据源不可用 /api/intelligent/ak/index');
        return;
      }
      var breadthReason = resp.breadth_reason || '全市场涨跌家数暂不可用';
      // 来源与「实时/缓存/聚合」性质标识（依据接口 source / aggregated / degraded / update_time）
      var natureLabel = resp.aggregated ? '聚合口径' : (resp.degraded ? '实时行情 · 广度降级' : '实时行情');
      var upTime = resp.update_time ? String(resp.update_time) : '';
      var timeLabel = upTime ? ('更新 ' + (upTime.length > 11 ? upTime.slice(11) : upTime)) : '';
      var headHtml = '<div class="ix-strip-src">' +
        '<span class="ix-strip-nature">' + escapeHtml(natureLabel) + '</span>' +
        '<span class="ix-strip-source">' + escapeHtml(resp.source || '--') + '</span>' +
        (timeLabel ? '<span class="ix-strip-time">' + escapeHtml(timeLabel) + '</span>' : '') +
      '</div>';
      host.innerHTML = headHtml + rows.map(function (ix) {
        var chg = Number(ix.chg) || 0;
        var cls = chg > 0 ? 'up' : (chg < 0 ? 'dn' : 'flat');
        var hasBreadth = (ix.up_count != null && ix.down_count != null);
        var breadthHtml = hasBreadth
          ? '<span class="ix-breadth">涨跌 <b class="up">' + ix.up_count + '</b> / <b class="dn">' + ix.down_count + '</b></span>'
          : '<span class="ix-breadth na" title="' + escapeHtml(ix.breadth_reason || breadthReason) + '">涨跌 <b class="na">--</b> / <b class="na">--</b> · 暂不可用</span>';
        return '<div class="ix-item">' +
                 '<span class="ix-name" title="' + escapeHtml(ix.name || '') + '">' + escapeHtml(ix.name || '') + '</span>' +
                 '<span class="ix-row">' +
                   '<b class="ix-price">' + (Number(ix.price) || 0).toFixed(2) + '</b>' +
                   '<span class="ix-chg ' + cls + '">' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%</span>' +
                 '</span>' +
                 breadthHtml +
               '</div>';
      }).join('');
      // 指数点位为真实行情（nature=real）；涨跌家数不可得已在行内如实标注。
      // 数据来源 chip 以接口返回的 source 为准（避免注册表文案与实算来源不一致）。
      brainMarkOk('index-strip', resp.update_time ? String(resp.update_time).slice(11) : _brainClock(), 'real');
      var srcChip = document.querySelector('[data-brain-mod=index-strip] .bm-src');
      if (srcChip && resp.source) srcChip.textContent = resp.source + ' /api/intelligent/ak/index';
      var nat = document.querySelector('[data-brain-mod=index-strip] .bm-nature');
      if (nat && resp.degraded) nat.title = breadthReason;
    } catch (e) {
      console.warn('Index strip fetch failed', e);
      host.innerHTML = '<div class="market-account-empty">指数数据加载失败</div>';
      brainMarkFailure('index-strip', '接口不可用 /api/intelligent/ak/index（' + e.message + '）');
    }
  }

  function _brainClock() {
    var d = new Date();
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  // ==========================================
  //  3.4 板块资金迁移粒子图 + 自动总结（V19）
  // ==========================================
  // 数据源：/api/sector-ranking（东方财富行业板块实时行情，含 net_flow/chg/amount）
  // 设计说明：该接口返回的是**涨幅榜前 50** 板块，行情普涨时净流为负的板块可能只有 1~2 个，
  // 因此粒子方向不能按「绝对正负」画，否则普涨日几乎无粒子。
  // 改为**以榜内净流入中位数为界的相对强弱迁移**，任何行情分布下都有明确语义，
  // 并在图例与坐标轴上如实标注中位数分界。
  var ROT_FLOW_MAX_NODES = 24;

  async function loadRotationFlow() {
    var host = document.getElementById('rotation-flow-chart');
    if (!host) return;
    try {
      var resp = await fetch('/api/sector-ranking?limit=50').then(function (r) { return r.json(); });
      var rows = (resp && resp.ok && Array.isArray(resp.data)) ? resp.data : [];
      renderRotationFlow(rows);
      renderRotationSummary(rows);
      var cnt = document.getElementById('rot-flow-count');
      if (cnt) cnt.textContent = rows.length ? (rows.length + ' 个板块') : '--';
      renderRotationFlowLegend();
      if (rows.length) brainMarkOk('rotation-flow', _brainClock(), 'aggregated');
      else brainMarkFailure('rotation-flow', '接口无数据 /api/sector-ranking');
    } catch (e) {
      console.warn('Rotation flow fetch failed', e);
      var s = document.getElementById('rotation-summary');
      if (s) s.innerHTML = '<div class="rs-title">自动总结</div><div class="rs-item" style="color:var(--muted)">板块数据加载失败，暂无法生成总结。</div>';
      brainMarkFailure('rotation-flow', '接口不可用 /api/sector-ranking（' + e.message + '）');
    }
  }

  function _rotFlowNodes(rows) {
    var valid = rows.filter(function (r) {
      return r && r.name && isFinite(Number(r.net_flow)) && isFinite(Number(r.chg));
    });
    // 按成交额取前 N，保证节点数量可控且都是活跃板块
    valid.sort(function (a, b) { return (Number(b.amount) || 0) - (Number(a.amount) || 0); });
    return valid.slice(0, ROT_FLOW_MAX_NODES).map(function (r) {
      return {
        name: String(r.name),
        flow: Number(r.net_flow) / 1e8,          // 亿元
        chg: Number(r.chg),
        amount: Number(r.amount) || 0,
        turnover: Number(r.turnover) || 0,
        up: Number(r.up_count) || 0,
        down: Number(r.down_count) || 0,
      };
    });
  }

  function _median(arr) {
    var a = arr.slice().sort(function (x, y) { return x - y; });
    if (!a.length) return 0;
    var m = Math.floor(a.length / 2);
    return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  }

  // ---- 粒子图状态（交互 / 性能 / 设备适配） ----
  var rotFlowState = {
    chart: null,
    rows: null,        // 最近一次数据，供重播复用
    nodes: [],         // 节点真实数据
    flows: [],         // 粒子流（含 from/to/diff）
    focus: null,       // 点击聚焦的板块名
    hover: null,       // 当前悬停的板块名
    bound: false,      // 事件是否已绑定
    buildSeries: null, // 由 renderRotationFlow 注入的序列构造器（供高亮复用）
    mobile: false,
  };

  function _rotFlowIsMobile() {
    return !!(window.matchMedia && window.matchMedia('(max-width: 768px)').matches);
  }

  // 粒子流按 |资金差额| 分 3 档：档位越高 → 轨迹越长、粒子越大越快、线越粗。
  // 这就是「密度与速度随强度变化」的实现方式（ECharts 的 effect 是序列级，无法逐条设参，
  // 故用分档成多个 lines 序列来近似逐条控制）。
  var ROT_FLOW_BUCKETS = [
    { id: 'strong', period: 3.2, sym: 5.6, trail: 0.60, width: 2.2, opacity: 0.42, core: 'rgba(255,77,109,.55)' },
    { id: 'mid',    period: 4.6, sym: 4.0, trail: 0.40, width: 1.4, opacity: 0.28, core: 'rgba(255,120,140,.36)' },
    { id: 'weak',   period: 6.4, sym: 2.8, trail: 0.24, width: 1.0, opacity: 0.18, core: 'rgba(255,150,170,.24)' },
  ];
  var _rotFlowSeq = 0;   // 递增序列号：重播时 seriesId 变化 → ECharts 重建 effect，粒子从起点重新流动

  function renderRotationFlow(rows) {
    var el = document.getElementById('rotation-flow-chart');
    if (!el || typeof echarts === 'undefined') return;
    var all = _rotFlowNodes(rows);
    if (!all.length) { el.innerHTML = '<div class="market-account-empty">暂无板块资金数据</div>'; return; }
    rotFlowState.rows = rows;

    // —— 设备与动效偏好适配 ——
    var isMobile = _rotFlowIsMobile();
    var reduceMotion = prefersReducedMotion();
    var glow = !isMobile && !reduceMotion;               // 光影：移动端或降级动效时关闭
    var maxNodes = isMobile ? 14 : ROT_FLOW_MAX_NODES;   // 移动端减少节点数
    var pairLimit = isMobile ? 5 : 8;                    // 移动端减少粒子流条数
    var nodes = all.slice(0, maxNodes);
    rotFlowState.mobile = isMobile;

    // 横轴对数刻度：净流入分布极度偏斜（中位数 ~8 亿、头部 150+ 亿），线性轴会把多数节点压在最左侧
    function _fx(f) { return Math.sign(f) * Math.log10(1 + Math.abs(f)); }
    function _fxInv(v) { return Math.sign(v) * (Math.pow(10, Math.abs(v)) - 1); }

    var med = _median(nodes.map(function (n) { return n.flow; }));

    /* 分组用「按净流入排序后的上下半区」，而不是「严格大于中位数」。
       原因（实测复现的缺陷）：后者在数值并列时会退化 ——
       盘后所有板块净流入同为 12.5 亿，此时 flow<median 的集合为空，
       pairN = min(8, 0, 24) = 0，粒子流一条都画不出来，
       表现为「粒子全部不显示 / 只显示一部分」。
       排序分组在任何分布（含大量并列）下都必然产出强弱两组。 */
    var byFlowDesc = nodes.slice().sort(function (a, b) { return b.flow - a.flow; });
    var half = Math.max(1, Math.floor(byFlowDesc.length / 2));
    var strong = byFlowDesc.slice(0, half);                       // 净流入最高的半区（降序）
    var weak = byFlowDesc.slice(-half).reverse();                 // 净流入最低的半区（升序，最弱在前）

    /* 数据退化检测：若全部板块净流入几乎相同，则不存在真实的资金迁移方向，
       此时不画粒子（画出来是伪信息），并如实给出原因。 */
    var flowMax = byFlowDesc[0].flow;
    var flowMin = byFlowDesc[byFlowDesc.length - 1].flow;
    var degenerate = (flowMax - flowMin) < Math.max(0.01, Math.abs(flowMax) * 0.005);
    rotFlowState.degenerate = degenerate;

    function sizeOf(amount) { return Math.max(12, Math.min(46, 8 + Math.sqrt(amount / 1e8) * 1.35)); }
    function stageOf(n) {
      if (n.chg >= 3) return '主升加速';
      if (n.chg > 0) return '震荡蓄势';
      if (n.chg === 0) return '横盘分化';
      return '调整回落';
    }

    // —— 节点：径向渐变 + 外发光（光球质感）——
    var nodeData = nodes.map(function (n) {
      var st = stageOf(n);
      var base = stageColor(st);
      return {
        name: n.name,
        value: [Number(_fx(n.flow).toFixed(4)), Number(n.chg.toFixed(2))],
        flow: n.flow, chg: n.chg, amount: n.amount, stage: st,
        symbolSize: sizeOf(n.amount),
        itemStyle: {
          color: new echarts.graphic.RadialGradient(0.36, 0.32, 0.92, [
            { offset: 0, color: 'rgba(255,255,255,.62)' },
            { offset: 0.42, color: base },
            { offset: 1, color: base }
          ]),
          opacity: 0.92,
          borderColor: 'rgba(255,255,255,.34)',
          borderWidth: 1,
          shadowBlur: glow ? 18 : 0,
          shadowColor: glow ? base : 'transparent'
        },
        label: {
          show: true, formatter: n.name, position: 'top', distance: 4, fontSize: 9.5,
          color: '#cdd7ee', textBorderColor: 'rgba(4,10,26,.85)', textBorderWidth: 2
        }
      };
    });

    /* 视觉去重叠（定义；实际在轴范围确定后调用，因为需要像素坐标）。
       多个板块可能落在同一或极近的（净流入, 涨跌幅）坐标上（退化行情下全部重叠），
       表现为「只看到一个球、其余被完全遮住」——这正是「部分粒子显示不全」的成因之一。
       这里按「像素距离」聚类后做环形错开：symbolOffset 只改渲染位置、不改数据值，
       环心对齐簇质心，因此聚类整体仍锚在原位置上，不会出现「线头悬空」。 */
    function applyNodeDeOverlap(list, xMin, xMax, yMin, yMax) {
      if (!el || !list.length) return;
      var W = el.clientWidth, H = el.clientHeight;
      var plotW = Math.max(60, W - 62 - 42), plotH = Math.max(60, H - 56 - 68);
      var xSpan = (xMax - xMin) || 1, ySpan = (yMax - yMin) || 1;
      // 预测每个节点在画布上的像素位置（grid 边距与 setOption 一致，线性映射精确）
      var pts = list.map(function (d) {
        return {
          x: 62 + (d.value[0] - xMin) / xSpan * plotW,
          y: 56 + (1 - (d.value[1] - yMin) / ySpan) * plotH,
          r: d.symbolSize / 2
        };
      });
      var used = [];
      var movable = [];
      var tgt = pts.map(function (p) { return { x: p.x, y: p.y }; });
      for (var i = 0; i < list.length; i++) { used[i] = false; movable[i] = false; }
      for (var s0 = 0; s0 < list.length; s0++) {
        if (used[s0]) continue;
        var cluster = [s0]; used[s0] = true;
        // 迭代扩张：与簇内任一成员中心距 < 0.85×(半径和) 即并入，可捕获「数值相近但不等」的近邻
        var grew = true;
        while (grew) {
          grew = false;
          for (var j = 0; j < list.length; j++) {
            if (used[j]) continue;
            for (var m = 0; m < cluster.length; m++) {
              var a = pts[j], b = pts[cluster[m]];
              var dx = a.x - b.x, dy = a.y - b.y;
              if (Math.sqrt(dx * dx + dy * dy) < 0.85 * (a.r + b.r)) {
                cluster.push(j); used[j] = true; grew = true; break;
              }
            }
          }
        }
        var n = cluster.length;
        if (n < 2) continue;
        var unit = [];   // 单位偏移（后续按各自 symbolSize 缩放）
        if (n <= 6) {
          // 少量重叠：单环，半径取「相邻节点恰好相切」(×1.04 留缝) —— 最紧凑，几乎看不出被人工挪动
          var u = 0.52 / Math.sin(Math.PI / n);
          for (var a1 = 0; a1 < n; a1++) {
            var th1 = (Math.PI * 2 * a1) / n - Math.PI / 2;
            unit.push([Math.cos(th1) * u, Math.sin(th1) * u]);
          }
        } else {
          /* 大量重叠（退化行情下 24 个板块同坐标）：同心环排布。
             环容量 1 / 6 / 12 / 18 / 24 由「相邻节点相切」反推，
             环半径按 1.05 个直径递增，切向与径向都不互相盖住。 */
          var caps = [1, 6, 12, 18, 24];
          var done = 0;
          for (var ri = 0; ri < caps.length && done < n; ri++) {
            var cnt = Math.min(caps[ri], n - done);
            var rad = ri === 0 ? 0 : ri * 1.05;
            for (var j2 = 0; j2 < cnt; j2++) {
              var th = cnt === 1 ? 0 : (Math.PI * 2 * j2) / cnt - Math.PI / 2 + ri * 0.42;
              unit.push([Math.cos(th) * rad, Math.sin(th) * rad]);
            }
            done += cnt;
          }
        }
        // 簇质心（环心），保证错开后整个簇仍锚在原位置
        var cx = 0, cy = 0;
        cluster.forEach(function (q) { cx += pts[q].x; cy += pts[q].y; });
        cx /= n; cy /= n;
        var k2 = isMobile ? 0.78 : 1;   // 移动端画布窄，整体收一档
        for (var q2 = 0; q2 < n; q2++) {
          var idx = cluster[q2];
          var s2 = list[idx].symbolSize * k2;
          tgt[idx] = { x: cx + unit[q2][0] * s2, y: cy + unit[q2][1] * s2 };
          movable[idx] = true;
        }
      }

      /* 松弛迭代：环形初值可能把节点推到相邻簇身上（退化行情下相邻行只隔几十像素），
         造成「修好一处、冒出另一处」。这里对可移动节点做若干轮斥力松弛，
         使任意两节点中心距 ≥ 1.02×(半径和)，即视觉上互不遮挡；
         非重叠的独立节点保持原位不动，真实分布不被扭曲。 */
      var padL = 62, padR = 42, padT = 56, padB = 68;
      for (var it = 0; it < 40; it++) {
        var moved = false;
        for (var p1 = 0; p1 < list.length; p1++) {
          for (var p2 = p1 + 1; p2 < list.length; p2++) {
            if (!movable[p1] && !movable[p2]) continue;
            var vx = tgt[p2].x - tgt[p1].x, vy = tgt[p2].y - tgt[p1].y;
            var dist = Math.sqrt(vx * vx + vy * vy);
            var minD = 1.02 * (pts[p1].r + pts[p2].r);
            if (dist >= minD) continue;
            if (dist < 0.001) {   // 完全重合：给一个确定性的分裂方向，避免除零
              var ang0 = (p1 * 2.39996) % (Math.PI * 2);
              vx = Math.cos(ang0); vy = Math.sin(ang0); dist = 1;
            }
            var ux = vx / dist, uy = vy / dist;
            var push = (minD - dist) / 2 + 0.35;
            var n1 = movable[p1] ? 1 : 0, n2 = movable[p2] ? 1 : 0;
            var tot = n1 + n2;
            if (n1) { tgt[p1].x -= ux * push * 2 * n1 / tot; tgt[p1].y -= uy * push * 2 * n1 / tot; }
            if (n2) { tgt[p2].x += ux * push * 2 * n2 / tot; tgt[p2].y += uy * push * 2 * n2 / tot; }
            moved = true;
          }
        }
        // 每轮把可移动节点钳回绘图区内，避免松弛把节点挤出视域
        for (var p3 = 0; p3 < list.length; p3++) {
          if (!movable[p3]) continue;
          var r3 = pts[p3].r;
          tgt[p3].x = Math.min(Math.max(tgt[p3].x, padL + r3 + 1), W - padR - r3 - 1);
          tgt[p3].y = Math.min(Math.max(tgt[p3].y, padT + r3 + 1), H - padB - r3 - 1);
        }
        if (!moved) break;
      }

      for (var q3 = 0; q3 < list.length; q3++) {
        if (!movable[q3]) continue;
        list[q3] = Object.assign({}, list[q3], {
          symbolOffset: [
            Number((tgt[q3].x - pts[q3].x).toFixed(2)),
            Number((tgt[q3].y - pts[q3].y).toFixed(2))
          ]
        });
      }
    }

    // —— 粒子流：按 |差额| 分档 ——
    var pairN = degenerate ? 0 : Math.min(pairLimit, weak.length, strong.length);
    var flows = [];
    for (var i = 0; i < pairN; i++) {
      var w = weak[i], s0 = strong[i];
      flows.push({
        from: w.name, to: s0.name,
        diff: s0.flow - w.flow,
        coords: [[Number(_fx(w.flow).toFixed(4)), Number(w.chg.toFixed(2))],
                 [Number(_fx(s0.flow).toFixed(4)), Number(s0.chg.toFixed(2))]]
      });
    }
    if (degenerate) {
      var host0 = document.getElementById('rot-flow-legend');
      if (host0) {
        host0.innerHTML = '<span class="ri warn">本轮板块净流入无差异（均为 ' + flowMax.toFixed(2) +
          ' 亿），不存在可辨的资金迁移方向，故不绘制粒子流</span>';
      }
    }
    var maxMag = Math.max.apply(null, [1].concat(flows.map(function (f) { return Math.abs(f.diff); })));
    var buckets = ROT_FLOW_BUCKETS.map(function (b) { return { cfg: b, items: [] }; });
    flows.forEach(function (f) {
      var r = Math.abs(f.diff) / maxMag;
      buckets[r >= 0.60 ? 0 : (r >= 0.30 ? 1 : 2)].items.push(f);
    });
    rotFlowState.nodes = nodes;
    rotFlowState.flows = flows;

    // —— 图表实例（复用，保留过渡动画）——
    var chart = rotFlowState.chart;
    if (!chart || chart.isDisposed() || chart.getDom() !== el) {
      if (chart && !chart.isDisposed()) chart.dispose();
      chart = echarts.init(el);
      rotFlowState.chart = chart;
      rotFlowState.bound = false;
    }
    var seq = ++_rotFlowSeq;

    // 序列构造器：highlightKey 非空时，只保留与该板块相关的粒子流，其余淡出并停粒子
    function buildSeries(highlightKey) {
      var lineSeries = buckets.map(function (b) {
        if (!b.items.length) return null;
        var relCount = 0;
        var data = b.items.map(function (f) {
          var related = !highlightKey || f.from === highlightKey || f.to === highlightKey;
          if (related) relCount++;
          return {
            coords: f.coords, from: f.from, to: f.to, diff: f.diff,
            lineStyle: highlightKey
              ? { opacity: related ? Math.min(1, b.cfg.opacity * 2.8) : b.cfg.opacity * 0.1 }
              : undefined
          };
        });
        return {
          id: 'rot-flow-lines-' + b.cfg.id + '-' + seq,
          name: b.cfg.id,
          type: 'lines',
          coordinateSystem: 'cartesian2d',
          polyline: false,
          zlevel: 1,
          /* clip:false —— 关键修复。
             curveness 会把贝塞尔控制点外扩，若沿用默认 clip:true，弧顶会被 grid 边界裁掉，
             表现为「粒子线画到一半突然消失、部分粒子看不到」。关闭裁剪后可完整绘制。 */
          clip: false,
          data: data,
          lineStyle: {
            // 渐变：弱端（资金流出方）偏绿 → 强端（资金流入方）偏红，直观表达迁移方向
            color: glow
              ? new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                  { offset: 0, color: 'rgba(0,233,106,.26)' },
                  { offset: 0.55, color: b.cfg.core },
                  { offset: 1, color: 'rgba(255,77,109,.58)' }
                ])
              : b.cfg.core,
            width: b.cfg.width,
            // 曲率 0.32 → 0.22：控制点外扩更少，配合 clip:false 保证弧顶完整且不过度溢出
            curveness: 0.22,
            opacity: b.cfg.opacity
          },
          emphasis: { lineStyle: { opacity: 1, width: b.cfg.width + 1.4 } },
          effect: {
            show: !reduceMotion && (!highlightKey || relCount > 0),
            period: b.cfg.period,
            trailLength: b.cfg.trail,
            symbol: 'circle',
            symbolSize: b.cfg.sym,
            color: '#ff8fa3',
            loop: true,
            delay: function (di) { return di * 220; }
          }
        };
      }).filter(Boolean);

      var focusIdx = highlightKey ? nodes.findIndex(function (n) { return n.name === highlightKey; }) : -1;
      var nodeItems = nodeData.map(function (d, di) {
        if (focusIdx < 0) return d;
        return {
          name: d.name, value: d.value, flow: d.flow, chg: d.chg, amount: d.amount, stage: d.stage,
          symbolSize: d.symbolSize,
          itemStyle: Object.assign({}, d.itemStyle, {
            opacity: di === focusIdx ? 1 : 0.34,
            shadowBlur: di === focusIdx && glow ? 26 : d.itemStyle.shadowBlur
          }),
          label: Object.assign({}, d.label, { fontSize: di === focusIdx ? 11 : 9, color: di === focusIdx ? '#ffffff' : '#9fb0cc' })
        };
      });

      // 仅对净流入绝对值 Top3 做涟漪（避免满屏闪光）；聚焦时只对聚焦节点涟漪
      var rippleSrc = focusIdx >= 0
        ? [nodeItems[focusIdx]]
        : nodeItems.slice().sort(function (a, b) { return Math.abs(b.flow) - Math.abs(a.flow); }).slice(0, isMobile ? 1 : 3);

      return lineSeries.concat([
        {
          id: 'rot-flow-nodes-' + seq,
          name: 'nodes',
          type: 'scatter',
          zlevel: 2,
          // clip:false —— 去重叠偏移后的贴边节点不再被 grid 边界裁切
          clip: false,
          data: nodeItems,
          cursor: 'pointer',
          // 只做隐藏不做位移：原 moveOverlap:'shiftY' 会把标签推开却不消失，密集区反而更乱
          labelLayout: { hideOverlap: true },
          emphasis: { scale: 1.25, itemStyle: { shadowBlur: 20, shadowColor: 'rgba(255,255,255,.5)', borderColor: '#fff', borderWidth: 2 } },
          markLine: {
            silent: true, symbol: 'none',
            lineStyle: { color: 'rgba(245,185,66,.5)', type: 'dashed', width: 1 },
            label: {
              formatter: '净流入中位数 ' + med.toFixed(2) + ' 亿',
              color: '#f5b942', fontSize: 9,
              position: 'insideEndTop',
              // 默认沿竖线旋转 90°，会压住节点；改为水平并加底色提升可读性
              rotate: 0,
              backgroundColor: 'rgba(8,15,34,.86)',
              padding: [2, 5],
              borderRadius: 3
            },
            data: [{ xAxis: Number(_fx(med).toFixed(4)) }]
          }
        },
        {
          id: 'rot-flow-ripple-' + seq,
          name: 'ripple',
          type: 'effectScatter',
          zlevel: 3,
          clip: false,
          data: rippleSrc,
          rippleEffect: { brushType: 'stroke', scale: isMobile ? 1.4 : 1.8, period: 4 },
          symbolSize: function (v, p2) { return (p2.data && p2.data.symbolSize) || 14; },
          itemStyle: { color: 'rgba(255,255,255,.06)', borderColor: 'rgba(255,255,255,.5)', borderWidth: 1 },
          label: { show: false },
          silent: true
        }
      ]);
    }
    rotFlowState.buildSeries = buildSeries;

    /* 轴范围按数据实测值留白。
       原先不设 min/max，ECharts 会取到「整刻度」（如 315），而数据最大仅 158 ——
       右半幅大片空白、左侧又拥挤；同时贴边节点会被 grid 裁到。
       改为按数据范围加 8%~18% 余量，并用 log 空间留白，保证端点与弧顶都在视域内。 */
    var _chgs = nodes.map(function (n) { return n.chg; });
    var _cMin = Math.min.apply(null, _chgs), _cMax = Math.max.apply(null, _chgs);
    var _cPad = Math.max(0.9, (_cMax - _cMin) * 0.24);
    var yMin = Math.floor((_cMin - _cPad) * 10) / 10;
    var yMax = Math.ceil((_cMax + _cPad) * 10) / 10;

    var _fxs = nodes.map(function (n) { return _fx(n.flow); });
    var _xMin = Math.min.apply(null, _fxs), _xMax = Math.max.apply(null, _fxs);
    var _xPad = Math.max(0.14, (_xMax - _xMin) * 0.11);
    var xMin = _xMin - _xPad, xMax = _xMax + _xPad;

    // 轴范围已知 → 可换算像素，执行视觉去重叠（不改数据值）
    applyNodeDeOverlap(nodeData, xMin, xMax, yMin, yMax);

    chart.setOption({
      animationDuration: 600,
      animationDurationUpdate: 700,
      grid: { left: 62, right: 42, top: 56, bottom: 68, containLabel: false },
      dataZoom: [
        // 滚轮缩放低值密集区；下方滑块支持拖拽
        { type: 'inside', xAxisIndex: 0, start: 0, end: 100, zoomOnMouseWheel: true },
        {
          type: 'slider', xAxisIndex: 0, height: 14, bottom: 8,
          borderColor: 'rgba(80,140,255,.2)', backgroundColor: 'rgba(8,15,34,.6)',
          fillerColor: 'rgba(168,85,247,.14)',
          handleStyle: { color: '#a855f7', borderColor: '#a855f7' },
          moveHandleStyle: { color: 'rgba(168,85,247,.5)' },
          textStyle: { color: '#8b9ec2', fontSize: 9 },
          dataBackground: { lineStyle: { color: 'rgba(80,140,255,.3)' }, areaStyle: { color: 'rgba(80,140,255,.1)' } },
          selectedDataBackground: { lineStyle: { color: '#a855f7' }, areaStyle: { color: 'rgba(168,85,247,.16)' } }
        }
      ],
      tooltip: {
        trigger: 'item',
        // confine:true —— 提示框贴边时会被画布裁掉（实测聚焦最右侧节点时，提示框右半截消失），
        // 限制在容器内即可保证任何位置都完整可见。
        confine: true,
        backgroundColor: 'rgba(8,15,34,.95)', borderColor: 'rgba(80,140,255,.35)', borderWidth: 1,
        textStyle: { color: '#e2e8f0', fontSize: 11 },
        extraCssText: 'max-width:260px;white-space:normal;border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.45)',
        formatter: function (p) {
          if (p.seriesType === 'lines') {
            var d = p.data || {};
            return '<div style="font-weight:800;margin-bottom:3px">资金迁移</div>' +
                   '<div style="font-family:SF Mono,monospace;line-height:1.7">' +
                   escapeHtml(d.from || '') + ' → ' + escapeHtml(d.to || '') + '<br>' +
                   '净流差额：<b style="color:#ff4d6d">' + (Number(d.diff) || 0).toFixed(2) + ' 亿</b>' +
                   '</div>';
          }
          var n = nodes.filter(function (x) { return x.name === p.name; })[0];
          if (!n) return escapeHtml(p.name || '');
          var related = flows.filter(function (f) { return f.from === n.name || f.to === n.name; }).length;
          return '<div style="font-weight:800;margin-bottom:3px">' + escapeHtml(n.name) + ' · ' + stageOf(n) + '</div>' +
                 '<div style="font-family:SF Mono,monospace;line-height:1.7">' +
                 '涨跌幅：<b style="color:' + (n.chg >= 0 ? '#ff4d6d' : '#00e96a') + '">' + (n.chg >= 0 ? '+' : '') + n.chg.toFixed(2) + '%</b><br>' +
                 '净流入：<b style="color:' + (n.flow >= 0 ? '#ff4d6d' : '#00e96a') + '">' + (n.flow >= 0 ? '+' : '') + n.flow.toFixed(2) + ' 亿</b><br>' +
                 '成交额：<b>' + (n.amount / 1e8).toFixed(1) + ' 亿</b><br>' +
                 '涨跌家数：<b style="color:#ff4d6d">' + n.up + '</b> / <b style="color:#00e96a">' + n.down + '</b>' +
                 (related ? '<br><span style="color:#a855f7">关联资金流 ' + related + ' 条（点击聚焦）</span>' : '') +
                 '</div>';
        }
      },
      xAxis: {
        type: 'value', name: '净流入 (亿元 · 对数刻度)', nameLocation: 'middle', nameGap: 30,
        min: xMin, max: xMax,
        nameTextStyle: { color: '#8fa3c8', fontSize: 10 },
        axisLine: { lineStyle: { color: 'rgba(120,150,200,.25)' } },
        axisTick: { show: true, inside: true, length: 4, lineStyle: { color: 'rgba(120,150,200,.28)' } },
        axisLabel: { color: '#8fa3c8', fontSize: 9, hideOverlap: true, formatter: function (v) { return _fxInv(v).toFixed(1); } },
        splitLine: { lineStyle: { color: 'rgba(120,150,200,.06)' } },
        splitArea: { show: true, areaStyle: { color: ['rgba(255,255,255,0)', 'rgba(255,255,255,.012)'] } }
      },
      yAxis: {
        type: 'value', name: '涨跌幅 %', nameTextStyle: { color: '#8fa3c8', fontSize: 10 },
        min: yMin, max: yMax,
        axisLine: { lineStyle: { color: 'rgba(120,150,200,.25)' } },
        axisTick: { show: true, inside: true, length: 4, lineStyle: { color: 'rgba(120,150,200,.28)' } },
        axisLabel: { color: '#8fa3c8', fontSize: 9 },
        splitLine: { lineStyle: { color: 'rgba(120,150,200,.06)' } },
        splitArea: { show: true, areaStyle: { color: ['rgba(255,255,255,0)', 'rgba(255,255,255,.012)'] } }
      },
      series: buildSeries(null)
    }, { replaceMerge: ['series'] });

    _rotFlowBindEvents(chart);
  }

  // ---- 事件绑定：悬停联动 / 点击聚焦 / 空白清除 ----
  function _rotFlowBindEvents(chart) {
    if (rotFlowState.bound) return;
    rotFlowState.bound = true;

    chart.on('mouseover', function (p) {
      if (p.seriesType !== 'scatter' && p.seriesType !== 'effectScatter') return;
      if (rotFlowState.focus) return;          // 已聚焦时不响应悬停，避免打架
      if (rotFlowState.hover === p.name) return;
      rotFlowState.hover = p.name;
      _rotFlowApplyState();
    });
    chart.on('mouseout', function () {
      if (!rotFlowState.hover) return;
      rotFlowState.hover = null;
      if (!rotFlowState.focus) _rotFlowApplyState();
    });
    chart.on('click', function (p) {
      if (p.seriesType !== 'scatter' && p.seriesType !== 'effectScatter') return;
      rotFlowState.focus = (rotFlowState.focus === p.name) ? null : p.name;
      rotFlowState.hover = null;
      _rotFlowApplyState();
      _rotFlowUpdateFocusUI();
    });
    // 点击空白处清除聚焦
    if (chart.getZr()) {
      chart.getZr().on('click', function (e) {
        if (!e.target && rotFlowState.focus) {
          rotFlowState.focus = null;
          _rotFlowApplyState();
          _rotFlowUpdateFocusUI();
        }
      });
    }
  }

  // 应用聚焦/悬停态：重建序列（数据量小，重建比 dispatchAction 更可控）
  function _rotFlowApplyState() {
    var chart = rotFlowState.chart;
    if (!chart || chart.isDisposed() || !rotFlowState.buildSeries) return;
    if (document.hidden) return;   // 页面不可见时不重绘，省性能
    var key = rotFlowState.focus || rotFlowState.hover;
    chart.setOption({ animationDurationUpdate: 0, series: rotFlowState.buildSeries(key) }, { replaceMerge: ['series'] });
  }

  function _rotFlowUpdateFocusUI() {
    var btn = document.getElementById('rot-flow-clear');
    var badge = document.getElementById('rot-flow-count');
    if (btn) btn.hidden = !rotFlowState.focus;
    if (badge && rotFlowState.focus) {
      badge.textContent = '聚焦：' + rotFlowState.focus;
      badge.title = '已聚焦该板块，仅显示其关联资金流';
    }
  }

  // 重播粒子：递增序列号触发 effect 重建
  function replayRotationFlow() {
    if (!rotFlowState.rows) return;
    renderRotationFlow(rotFlowState.rows);
    _rotFlowUpdateFocusUI();
    if (typeof showToast === 'function') showToast('粒子动画', '已重播资金迁移粒子流');
  }

  function clearRotationFlowFocus() {
    rotFlowState.focus = null;
    rotFlowState.hover = null;
    _rotFlowApplyState();
    _rotFlowUpdateFocusUI();
    var badge = document.getElementById('rot-flow-count');
    var rows = rotFlowState.rows || [];
    if (badge) badge.textContent = rows.length ? (rows.length + ' 个板块') : '--';
  }

  /* 节点配色图例：让读者能解码颜色语义（提升视觉层次与可读性） */
  function renderRotationFlowLegend() {
    var host = document.getElementById('rot-flow-legend');
    if (!host) return;
    var stages = ['主升加速', '震荡蓄势', '横盘分化', '调整回落'];
    var items = stages.map(function (s) {
      var c = stageColor(s);
      return '<span class="ri"><i style="background:' + c + ';color:' + c + '"></i>' + escapeHtml(s) + '</span>';
    }).join('');
    items += '<span class="ri hint">粒子：弱端&#8594;强端（绿&#8594;红）· 粒子大小/速度随资金差额递增</span>';
    items += '<span class="ri hint">分组：按净流入排序的上下半区（非严格中位数，避免并列时退化）</span>';
    items += '<span class="ri hint">虚线：榜内净流入中位数</span>';
    if (rotFlowState.degenerate) {
      host.innerHTML = '<span class="ri warn">本轮板块净流入无差异，不存在可辨的资金迁移方向，故不绘制粒子流</span>';
      return;
    }
    host.innerHTML = items;
  }

  function renderRotationSummary(rows) {
    var host = document.getElementById('rotation-summary');
    if (!host) return;
    var nodes = _rotFlowNodes(rows);
    if (!nodes.length) {
      host.innerHTML = '<div class="rs-title">自动总结</div><div class="rs-item" style="color:var(--muted)">暂无板块数据。' +
        '<div class="rs-meta"><span class="rs-badge rule">规则引擎</span></div></div>';
      return;
    }

    var med = _median(nodes.map(function (n) { return n.flow; }));
    var sorted = nodes.slice().sort(function (a, b) { return b.flow - a.flow; });
    var top3 = sorted.slice(0, 3);
    var bot3 = sorted.slice(-3).reverse();
    var totalFlow = nodes.reduce(function (s, n) { return s + n.flow; }, 0);
    var top3Flow = top3.reduce(function (s, n) { return s + n.flow; }, 0);
    var conc = Math.abs(totalFlow) > 1e-6 ? (top3Flow / totalFlow * 100) : 0;

    // 阶段结构（口径与粒子图一致）
    var stageCount = { '主升加速': 0, '震荡蓄势': 0, '横盘分化': 0, '调整回落': 0 };
    nodes.forEach(function (n) {
      if (n.chg >= 3) stageCount['主升加速']++;
      else if (n.chg > 0) stageCount['震荡蓄势']++;
      else if (n.chg === 0) stageCount['横盘分化']++;
      else stageCount['调整回落']++;
    });
    var dominant = Object.keys(stageCount).sort(function (a, b) { return stageCount[b] - stageCount[a]; })[0];

    // 量价背离：涨幅高于中位数、但净流入低于中位数 → 资金未跟随
    var chgMed = _median(nodes.map(function (n) { return n.chg; }));
    var diverge = nodes.filter(function (n) { return n.chg > chgMed && n.flow < med; })
                       .sort(function (a, b) { return b.chg - a.chg; }).slice(0, 2);

    var fmt = function (v) { return (v >= 0 ? '+' : '') + v.toFixed(2); };
    var cls = function (v) { return v >= 0 ? 'up' : 'dn'; };
    var items = [];

    items.push('资金集中度：净流入前三 <b class="up">' + escapeHtml(top3.map(function (n) { return n.name; }).join('、')) +
      '</b> 合计 <b class="up">' + fmt(top3Flow) + ' 亿</b>，占榜内净流入总额 <b class="cy">' + conc.toFixed(1) + '%</b>' +
      (conc >= 75 ? '，资金高度集中，呈明显抱团特征' : conc >= 45 ? '，资金相对集中' : '，资金分布较分散'));

    items.push('迁移方向：资金由 <b class="dn">' + escapeHtml(bot3.map(function (n) { return n.name; }).join('、')) +
      '</b>（净流 ' + fmt(bot3[0].flow) + ' 亿）流向 <b class="up">' + escapeHtml(top3[0].name) +
      '</b>（净流 ' + fmt(top3[0].flow) + ' 亿），强弱净流差 <b>' + (top3[0].flow - bot3[0].flow).toFixed(2) + ' 亿</b>');

    items.push('阶段结构：' + nodes.length + ' 个活跃板块中，<b class="cy">' + dominant + '</b> 最多（' + stageCount[dominant] +
      ' 个，占 ' + (stageCount[dominant] / nodes.length * 100).toFixed(0) + '%）；主升加速 ' + stageCount['主升加速'] +
      ' 个 · 震荡蓄势 ' + stageCount['震荡蓄势'] + ' 个 · 调整回落 ' + stageCount['调整回落'] + ' 个');

    if (diverge.length) {
      items.push('量价背离提示：<b class="up">' + escapeHtml(diverge.map(function (n) { return n.name + '（' + fmt(n.chg) + '%）'; }).join('、')) +
        '</b> 涨幅居前但净流入低于中位数，属缩量上涨、资金未跟随，追高需谨慎');
    } else {
      items.push('量价配合：涨幅居前板块的净流入普遍高于中位数，暂未发现明显缩量上涨的背离信号');
    }

    host.innerHTML =
      '<div class="rs-title">自动总结</div>' +
      '<div class="rs-list">' + items.map(function (t) { return '<div class="rs-item">' + t + '</div>'; }).join('') + '</div>' +
      '<div class="rs-meta">' +
        '<span class="rs-badge">板块轮动总结</span>' +
        '<span class="rs-badge rule">规则引擎 · 基于真实资金流计算</span>' +
        '<span class="rs-badge src">数据源 东方财富行业板块实时行情</span>' +
        '<span class="rs-badge src">' + escapeHtml((nodes[0] && rows[0] && rows[0].updated_at) || '') + '</span>' +
      '</div>';
  }

  // ==========================================
  //  3.5 板块轮动散点图（平滑过渡 + 双向联动）
  // ==========================================
  var STAGE_COLORS = {
    '主升加速': '#ff4d6d',   // 红 · 强势上行（A股红涨）
    '震荡蓄势': '#f5b942',   // 金 · 蓄势待发
    '横盘分化': '#508cff',   // 蓝 · 多空均衡
    '调整回落': '#00e96a'    // 绿 · 弱势回落（A股绿跌）
  };

  function stageColor(stage) { return STAGE_COLORS[stage] || '#508cff'; }

  function _ensureScatterChart() {
    var el = document.getElementById('survey-rotation-scatter');
    if (!el || typeof echarts === 'undefined') return null;
    if (!surveyState.scatterChart || surveyState.scatterChart.isDisposed()) {
      surveyState.scatterChart = echarts.init(el);
      // 点击空白处取消选中；点击散点选中并联动列表
      surveyState.scatterChart.on('click', function(params) {
        if (params.componentType === 'series' && params.data && params.data.id) {
          selectRotationSector(params.data.id, 'chart');
        } else {
          clearRotationSelection();
        }
      });
    } else {
      surveyState.scatterChart.resize(); // 视图尺寸变化后校正画布
    }
    return surveyState.scatterChart;
  }

  function _scatterSymbolSize(amountYi) {
    // 气泡面积映射成交额（开方线性），限制在 14~46px
    var a = Math.max(1, Number(amountYi) || 1);
    return Math.max(14, Math.min(46, 10 + Math.sqrt(a) * 3));
  }

  function renderRotationScatter(scatter, rotation) {
    var chart = _ensureScatterChart();
    if (!chart) return;
    // 缓存原始入参，供阶段图例 solo 高亮时重绘（避免图例点击丢失数据）
    surveyState.rawScatter = scatter;
    surveyState.rawRotation = rotation;
    var points = Array.isArray(scatter) && scatter.length ? scatter
               : (Array.isArray(rotation) ? rotation.map(function(r) {
                   return { sector: r.sector, stage: r.stage, avg_chg: r.avg_chg, up_ratio: r.up_ratio,
                            amount_yi: r.amount_yi, count: r.count, leader_name: '--', leader_chg: 0 };
                 }) : []);
    // sector → dataIndex 映射（悬停/选中 dispatchAction 用）+ 阶段分布统计（图例计数）
    surveyState.sectorIndex = {};
    surveyState.sectorStage = {};
    surveyState.stageCounts = {};
    points.forEach(function(p, i) {
      surveyState.sectorIndex[p.sector] = i;
      if (p.stage) {
        surveyState.sectorStage[p.sector] = p.stage;
        surveyState.stageCounts[p.stage] = (surveyState.stageCounts[p.stage] || 0) + 1;
      }
    });
    // 数据刷新后 solo 阶段可能已不存在，自动回落为「无 solo」
    if (surveyState.stageSolo && !surveyState.stageCounts[surveyState.stageSolo]) {
      surveyState.stageSolo = null;
    }
    renderStageLegend();

    var solo = surveyState.stageSolo;
    var data = points.map(function(p) {
      var dimmed = !!solo && p.stage !== solo;   // solo 高亮：非同阶段散点淡化
      return {
        id: p.sector,                       // 稳定键：数据刷新时按板块匹配，位置平滑过渡而非重绘跳变
        name: p.sector,
        value: [Number(p.avg_chg) || 0, Number(p.up_ratio) || 0],
        amountYi: p.amount_yi, count: p.count, stage: p.stage,
        leaderName: p.leader_name, leaderChg: p.leader_chg,
        symbolSize: _scatterSymbolSize(p.amount_yi),
        itemStyle: {
          color: stageColor(p.stage),
          opacity: dimmed ? 0.14 : 0.82,
          borderColor: dimmed ? 'rgba(255,255,255,0.12)' : 'rgba(255,255,255,0.35)',
          borderWidth: 1
        },
        label: { show: !dimmed, formatter: p.sector, position: 'top', distance: 5, fontSize: 10, color: '#cdd7ee', textBorderColor: 'rgba(4,10,26,0.85)', textBorderWidth: 2 }
      };
    });

    chart.setOption({
      animation: true,
      animationDuration: 450,
      animationDurationUpdate: 650,          // 数据更新时散点位置平滑迁移
      animationEasingUpdate: 'cubicOut',
      grid: { left: 46, right: 18, top: 26, bottom: 34 },
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(8,15,34,0.94)',
        borderColor: 'rgba(80,140,255,0.35)',
        borderWidth: 1,
        textStyle: { color: '#e2e8f0', fontSize: 11 },
        formatter: function(params) {
          var d = params.data;
          return '<div style="font-weight:800;font-size:12px;margin-bottom:3px;color:' + stageColor(d.stage) + '">' + escapeHtml(d.name) + ' · ' + escapeHtml(d.stage) + '</div>' +
                 '<div style="font-family:SF Mono,monospace;line-height:1.7">' +
                 '平均涨跌：<b style="color:' + (d.value[0] >= 0 ? '#ff4d6d' : '#00e96a') + '">' + (d.value[0] >= 0 ? '+' : '') + d.value[0].toFixed(2) + '%</b><br>' +
                 '上涨占比：<b>' + d.value[1].toFixed(1) + '%</b><br>' +
                 '成交额：<b>' + (Number(d.amountYi) || 0).toFixed(1) + ' 亿</b><br>' +
                 '样本数：<b>' + (d.count || 0) + ' 只</b><br>' +
                 '龙头：<b>' + escapeHtml(d.leaderName || '--') + '</b> (' + (d.leaderChg >= 0 ? '+' : '') + (Number(d.leaderChg) || 0).toFixed(1) + '%)' +
                 '</div>';
        }
      },
      xAxis: {
        type: 'value', name: '平均涨跌 %', nameTextStyle: { color: '#8fa3c8', fontSize: 10 },
        axisLine: { lineStyle: { color: 'rgba(120,150,200,0.25)' } },
        axisLabel: { color: '#8fa3c8', fontSize: 10, formatter: function(v) { return v > 0 ? '+' + v : v; } },
        splitLine: { lineStyle: { color: 'rgba(120,150,200,0.08)' } }
      },
      yAxis: {
        type: 'value', name: '上涨占比 %', min: 0, max: 100, nameTextStyle: { color: '#8fa3c8', fontSize: 10 },
        axisLine: { lineStyle: { color: 'rgba(120,150,200,0.25)' } },
        axisLabel: { color: '#8fa3c8', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(120,150,200,0.08)' } }
      },
      series: [{
        id: 'rotation-scatter',
        name: '板块轮动',
        type: 'scatter',
        data: data,
        cursor: 'pointer',
        emphasis: {
          scale: 1.35,
          itemStyle: { shadowBlur: 12, shadowColor: 'rgba(255,255,255,0.45)', borderColor: '#fff', borderWidth: 2, opacity: 1 }
        },
        selectedMode: 'single',
        select: {
          itemStyle: { borderColor: '#ffffff', borderWidth: 2.5, shadowBlur: 14, shadowColor: stageColor('主升加速'), opacity: 1 },
          label: { show: true, fontWeight: 800, color: '#ffffff' }
        },
        markLine: {
          silent: true, symbol: 'none',
          lineStyle: { color: 'rgba(140,165,205,0.35)', type: 'dashed', width: 1 },
          label: { show: false },
          data: [ { xAxis: 0 }, { yAxis: 50 } ]   // 四象限：右上强势 / 右下分化 / 左上回暖 / 左下走弱
        }
      }]
    }, { replaceMerge: ['series'] });
  }

  function selectRotationSector(sector, source) {
    if (!sector || sector === surveyState.selectedSector) { clearRotationSelection(); return; }
    surveyState.selectedSector = sector;
    syncSelectionUI(source);
    if (typeof showToast === 'function') {
      showToast('板块联动', '已选中「' + sector + '」：散点与列表已同步高亮');
    }
  }

  function clearRotationSelection() {
    surveyState.selectedSector = null;
    syncSelectionUI();
  }

  function syncSelectionUI(source) {
    var sector = surveyState.selectedSector;
    // 1) 散点选中态
    if (surveyState.scatterChart && !surveyState.scatterChart.isDisposed()) {
      surveyState.scatterChart.dispatchAction({ type: 'unselect', seriesIndex: 0 });
      var idx = surveyState.sectorIndex[sector];
      if (idx != null) {
        surveyState.scatterChart.dispatchAction({ type: 'select', seriesIndex: 0, dataIndex: idx });
        if (source !== 'chart') {
          surveyState.scatterChart.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: idx });
        }
      }
    }
    // 2) 列表行高亮 + 视野滚动
    var rotList = document.getElementById('survey-rotation-list');
    if (rotList) {
      rotList.querySelectorAll('.rot-row').forEach(function(row) {
        var active = row.getAttribute('data-sector') === sector;
        row.classList.toggle('active', active);
        if (active && source !== 'list') row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      });
    }
  }


  // ==========================================
  //  4. AI 主脑（DAPENG Brain）状态卡
  // ==========================================
  var TASK_STATUS_TEXT = { ok: '正常', error: '异常', running: '运行中', pending: '待运行' };

  function fmtNextDue(sec) {
    if (sec == null) return '下一交易时段';
    if (sec <= 0) return '即将执行';
    if (sec < 60) return sec + ' 秒后';
    return Math.round(sec / 60) + ' 分钟后';
  }

  async function loadBrainStatus() {
    try {
      var resp = await fetch('/api/brain/status').then(function (r) { return r.json(); });
      if (resp && resp.ok) {
        renderBrain(resp);
        brainMarkOk('brain', _brainClock(), 'real');
      } else {
        brainMarkFailure('brain', '接口返回异常 /api/brain/status');
      }
    } catch (e) {
      console.warn('brain status fetch failed', e);
      brainMarkFailure('brain', '接口不可用 /api/brain/status（' + e.message + '）');
    }
  }

  function renderBrain(status) {
    var badge = document.getElementById('brain-llm-badge');
    if (badge) {
      var llmOn = status.llm && status.llm.configured;
      badge.textContent = llmOn ? ('LLM 在线 · ' + (status.llm.model || '')) : 'LLM 未配置（规则模式）';
      badge.className = 'brain-llm-badge ' + (llmOn ? 'on' : 'off');
    }

    // P0 情报风险徽章（Hero 联动）
    var riskEl = document.getElementById('survey-hero-risk');
    if (riskEl && status.intel) {
      var rl = status.intel.risk_level || '';
      var rCls = rl === '高' ? 'r-high' : rl === '中' ? 'r-mid' : rl === '关注' ? 'r-watch' : 'r-low';
      riskEl.textContent = '情报风险 ' + (rl || '低') + (status.intel.risk_score != null ? (' · ' + status.intel.risk_score + '分') : '');
      riskEl.className = 'survey-hero-risk ' + rCls;
    } else if (riskEl) {
      riskEl.textContent = '情报风险 --';
      riskEl.className = 'survey-hero-risk r-none';
    }

    // P1 内核运行态
    var kernelEl = document.getElementById('brain-kernel');
    if (kernelEl) {
      var mem = status.memory || {};
      var uptime = status.uptime_sec || 0;
      var uptimeTxt = uptime >= 3600 ? Math.floor(uptime / 3600) + 'h' + Math.floor((uptime % 3600) / 60) + 'm' : Math.floor(uptime / 60) + 'm';
      kernelEl.innerHTML = '运行 <b>' + (status.running ? '中' : '停') + '</b> · 时长 <b>' + uptimeTxt + '</b> · 心跳 <b>' + (status.ticks || 0) + '</b><br>' +
        '洞察 <b>' + (mem.insights_count || 0) + '</b> 条 · 经验 <b>' + (mem.lessons_count || 0) + '</b> 条 · 调查档案 <b>' + ((status.survey_history || []).length) + '</b> 条';
    }

    var tasksEl = document.getElementById('brain-tasks');
    if (tasksEl && Array.isArray(status.tasks)) {
      tasksEl.innerHTML = status.tasks.map(function (t) {
        var cls = TASK_STATUS_TEXT[t.last_status] ? t.last_status : 'pending';
        return '<div class="brain-task-row">' +
          '<span class="bt-name">' + escapeHtml(t.name) + '</span>' +
          '<span class="bt-status ' + cls + '">' + (TASK_STATUS_TEXT[cls] || t.last_status) + '</span>' +
          '<span class="bt-meta">窗口 ' + escapeHtml(t.window) + ' · 上次 ' + escapeHtml(t.last_run || '未运行') +
          (t.last_summary ? ' · ' + escapeHtml(t.last_summary) : '') +
          ' · 下次约 ' + fmtNextDue(t.next_due_in_sec) + (t.dynamic_interval ? ' · 动态调频' : '') + '</span>' +
          '</div>';
      }).join('');
    }

    var insights = status.insights || [];
    var latest = insights.length ? insights[insights.length - 1] : null;
    var tsEl = document.getElementById('brain-insight-ts');
    var textEl = document.getElementById('brain-insight-text');
    if (latest) {
      if (tsEl) tsEl.innerHTML = escapeHtml(latest.ts || '') + (latest.regime_shift ? '<span class="brain-shift">⚡ 状态切换</span>' : '');
      if (textEl) textEl.textContent = latest.text || '';
    } else if (tsEl) {
      tsEl.textContent = '';
    }

    var lessonsEl = document.getElementById('brain-lessons');
    var lessons = status.lessons || [];
    if (lessonsEl) {
      lessonsEl.innerHTML = lessons.slice(-3).map(function (l) {
        return '<div class="brain-lesson">&#x1F4D8; ' + escapeHtml(l.lesson || '') + '</div>';
      }).join('');
    }

    // 情报评估区块（世界局势/地缘 → 市场影响）
    var intelEl = document.getElementById('brain-intel');
    if (intelEl) {
      var intel = status.intel;
      if (intel && intel.brief) {
        var riskCls = intel.risk_level === '高' ? 'high' : intel.risk_level === '中' ? 'mid' : intel.risk_level === '关注' ? 'watch' : 'low';
        var sectors = (intel.sectors || []).map(function (s) { return '<span>' + escapeHtml(s) + '</span>'; }).join('');

        // V17：补齐此前被丢弃的三个结构化情报字段
        //   channels（影响渠道）、global_snapshot（6 个全球资产）、headlines（命中新闻）
        //   后端 /api/brain/intel 一直在返回它们，旧前端只渲染了 risk/brief/sectors。
        var channels = (intel.channels || []).map(function (c) {
          return '<span class="bi-chan">' + escapeHtml(c) + '</span>';
        }).join('');

        var gsnap = intel.global_snapshot || {};
        var gkeys = Object.keys(gsnap);
        var globalHtml = gkeys.length
          ? '<div class="bi-section-title">全球市场快照</div><div class="bi-global">' +
            gkeys.map(function (k) {
              var g = gsnap[k] || {};
              var chg = Number(g.chg) || 0;
              return '<div class="bi-g-item">' +
                       '<span class="bi-g-name">' + escapeHtml(g.name || k) + '</span>' +
                       '<b class="bi-g-price">' + escapeHtml(String(g.price != null ? g.price : '--')) + '</b>' +
                       '<span class="bi-g-chg ' + (chg >= 0 ? 'up' : 'down') + '">' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%</span>' +
                     '</div>';
            }).join('') + '</div>'
          : '';

        var heads = intel.headlines || [];
        var headlinesHtml = heads.length
          ? '<div class="bi-section-title">扫描命中的关键新闻</div><div class="bi-headlines">' +
            heads.map(function (h) { return '<div class="bi-hl">' + escapeHtml(h) + '</div>'; }).join('') +
            '</div>'
          : '';

        intelEl.innerHTML =
          '<div class="bi-head"><span class="bi-risk ' + riskCls + '">情报风险 ' + escapeHtml(intel.risk_level || '--') + ' · ' + (intel.risk_score || 0) + '分</span>' +
          '<small>' + escapeHtml((intel.ts || '').slice(5, 16)) + ' · 扫描 ' + (intel.news_scanned || 0) + ' 条命中 ' + (intel.news_matched || 0) + ' 条' + (intel.ai_powered ? ' · AI 评估' : ' · 规则评估') + '</small></div>' +
          // 取数失败时后端会沿用上次的结构化字段并记录 carried_forward，此处如实标注
          ((intel.carried_forward || []).length
            ? '<div class="bi-carry">本次扫描取数不全，以下沿用上次扫描（' + escapeHtml(String(intel.carried_from_ts || '').slice(5, 16)) + '）：' + escapeHtml(intel.carried_forward.join('、')) + '</div>'
            : '') +
          '<div class="bi-brief">' + escapeHtml(intel.brief) + '</div>' +
          (channels ? '<div class="bi-section-title">影响渠道</div><div class="bi-channels">' + channels + '</div>' : '') +
          globalHtml +
          headlinesHtml +
          (sectors ? '<div class="bi-sectors">' + sectors + '</div>' : '') +
          ((intel.watch_points || []).length ? '<div class="bi-watch">观察点：' + escapeHtml(intel.watch_points.join('；')) + '</div>' : '');
      } else {
        intelEl.innerHTML = '<div class="bi-brief" style="color:var(--muted)">情报扫描尚未运行（主脑每 30 分钟自动扫描世界局势与地缘事件）</div>';
      }
    }
  }

  async function brainRunTask(taskId) {
    if (typeof showToast === 'function') showToast('AI 主脑', '已触发任务执行，请稍候…');
    try {
      var resp = await fetch('/api/brain/run/' + encodeURIComponent(taskId), { method: 'POST' }).then(function (r) { return r.json(); });
      if (resp && resp.ok) {
        if (typeof showToast === 'function') showToast('AI 主脑', '任务完成：' + ((resp.task && resp.task.last_summary) || 'OK'));
        loadBrainStatus();
        if (taskId === 'market_survey') loadMarketSurveyData();
      } else {
        if (typeof showToast === 'function') showToast('AI 主脑', '任务未成功：' + ((resp && resp.message) || (resp && resp.task && resp.task.last_summary) || '未知原因'));
        loadBrainStatus();
      }
    } catch (e) {
      if (typeof showToast === 'function') showToast('AI 主脑', '触发失败: ' + e.message);
    }
  }

  function toggleBrainTasks() {
    var tasks = document.getElementById('brain-tasks');
    var sched = document.querySelector('.brain-scheduler');
    if (!tasks) return;
    var hidden = tasks.hasAttribute('hidden');
    if (hidden) { tasks.removeAttribute('hidden'); }
    else { tasks.setAttribute('hidden', ''); }
    if (sched) sched.classList.toggle('open', hidden);
    var toggle = document.querySelector('.brain-scheduler-toggle');
    if (toggle) toggle.setAttribute('aria-expanded', hidden ? 'true' : 'false');
  }

  // Export to global scope
  window.toggleQlibFactor = toggleQlibFactor;
  window.runQlibBacktest = runQlibBacktest;
  window.loadDynamicFactor = loadDynamicFactor;
  window.onQlibParamChange = onQlibParamChange;
  // M7：AI 主脑面板个性化布局
  window.brainToggleSettings = brainToggleSettings;
  window.brainToggleModule = brainToggleModule;
  window.brainToggleCollapse = brainToggleCollapse;
  window.brainSetPreset = brainSetPreset;
  window.brainResetLayout = brainResetLayout;
  window.brainExpandAll = brainExpandAll;
  window.brainEnsureInit = brainEnsureInit;
  window.loadMarketSurveyData = loadMarketSurveyData;
  window.loadBrainStatus = loadBrainStatus;
  window.brainRunTask = brainRunTask;
  window.toggleBrainTasks = toggleBrainTasks;
  // V16 新增：Top 组合表排序 / 调查视图筛选与轮询（HTML 内联 onclick 需要挂到 window）
  window.sortQlibTop = sortQlibTop;
  window.toggleSurveyAutorefresh = toggleSurveyAutorefresh;
  window.filterAlerts = filterAlerts;
  window.filterNews = filterNews;
  window.selectRotationStage = selectRotationStage;
  window.loadSurveyIndexStrip = loadSurveyIndexStrip;
  window.loadRotationFlow = loadRotationFlow;
  window.replayRotationFlow = replayRotationFlow;
  window.clearRotationFlowFocus = clearRotationFlowFocus;

  // ---- 启动期初始化：读取自动轮询偏好 + 页面可见性联动 ----
  surveyAutoEnabled = _readAutoRefreshPref();
  updateSurveyAutoToggleUI();
  updateSurveyCountdownUI();

  document.addEventListener('visibilitychange', function() {
    if (!_surveyViewActive()) return;
    if (document.hidden) {
      stopSurveyCountdown();          // 页面切到后台即停表，回来再续
    } else if (surveyAutoEnabled) {
      surveyCountdownSec = SURVEY_POLL_SEC;
      startSurveyCountdown();
    }
    // 恢复到前台时补一次粒子态重绘（_rotFlowApplyState 在 hidden 时会跳过）
    _rotFlowApplyState();
  });

  /* 视口尺寸变化：不仅 resize 画布，还要按新宽度重跑一次渲染。
     原因：节点的「去重叠偏移」和坐标轴留白都是按当时的像素宽度算出来的，
     若只 resize 不重算，桌面↔移动端切换后偏移量会失配，重新出现互相遮挡。 */
  var _rotFlowResizeTimer = null;
  window.addEventListener('resize', function() {
    if (qlibState.navChart && !qlibState.navChart.isDisposed()) {
      qlibState.navChart.resize();
    }
    if (surveyState.scatterChart && !surveyState.scatterChart.isDisposed()) {
      surveyState.scatterChart.resize();
    }
    if (rotFlowState.chart && !rotFlowState.chart.isDisposed()) {
      rotFlowState.chart.resize();
    }
    if (_rotFlowResizeTimer) clearTimeout(_rotFlowResizeTimer);
    _rotFlowResizeTimer = setTimeout(function () {
      var host = document.getElementById('rotation-flow-chart');
      // 容器不可见（clientWidth 为 0）时不重算，等可见后由数据加载流程重新渲染
      if (!host || host.clientWidth < 40 || !rotFlowState.rows) return;
      renderRotationFlow(rotFlowState.rows);
    }, 260);
  });

})();
