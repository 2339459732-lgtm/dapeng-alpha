/**
 * DAPENG Alpha 策略选股系统 - 主入口 v5.4
 *
 * v5.4 升级：
 * - 板块分析面板完全重写（API 驱动 + 多周期 + 排行 + 详情 + 成分股 + 资金流）
 * - 选股器 5 条件 + 独立结果面板
 * - 智能选股行业 checkbox 组
 * - ESC 键优先级关闭最上层面板
 *
 * v3.0 升级：
 * - 左侧垂直导航栏替代板块选择器
 * - KPI卡片行展示板块统计
 * - 因子雷达图
 * - 选股信号排行表格
 * - 26个板块484只标的（含港股通）
 */

// ================================================================
//  方向配色（A 股惯例：红=涨/买/多/正，绿=跌/卖/空/负）
//  统一从 :root 主题变量读取（--red / --green），不写死 hex
// ================================================================
function dirColor(dir) {
  try {
    var v = (getComputedStyle(document.documentElement).getPropertyValue(dir === 'down' ? '--green' : '--red') || '').trim();
    return v || (dir === 'down' ? '#00e96a' : '#ff3d6a');
  } catch (e) { return dir === 'down' ? '#00e96a' : '#ff3d6a'; }
}
function dirRgba(dir, alpha) {
  var hex = String(dirColor(dir)).replace('#', '');
  if (hex.length === 3) hex = hex.charAt(0) + hex.charAt(0) + hex.charAt(1) + hex.charAt(1) + hex.charAt(2) + hex.charAt(2);
  var n = parseInt(hex, 16);
  if (hex.length !== 6 || isNaN(n)) return dirColor(dir);
  return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + alpha + ')';
}

// ================================================================
//  全局状态
// ================================================================
window.appState = {
  stocks: [],
  activeCode: '',
  activeName: '',
  activeSector: 'semiconductor',
  listFilter: 'all',
  // v7: 列表排序 / 信号筛选 / 置顶
  sortKey: 'score',        // 'score' | 'chg' | 'turnover' | 'volume_ratio' | 'code'
  sortDir: 'desc',         // 'desc' | 'asc'
  signalFilter: '',        // 当前激活的信号类型筛选（空 = 不过滤）
  pinnedStocks: safeJsonParse(localStorage.getItem('dapeng_pinned'), []) || [], // v7: 防 "null" 解析结果
  // 智能选股价格范围仅用于扫描结果，不影响普通股票列表
  priceMin: 0,
  priceMax: 200,
  period: 'day',
  klineCache: {},
  stockMap: {},
  refreshTimer: null,
  favoriteStocks: safeJsonParse(localStorage.getItem('dapeng_favorites'), []) || [], // v7: 防 "null" 解析结果
  screenMode: false,
  prevPrices: {},
  _klineVersion: 0,
  radarChart: null,
  signalTableSort: 'score',
  signalTableVisible: false,
  scanPanelVisible: false,
  scanData: null,
  activeTab: 'strategy',  // v5.0: K线区域Tab状态
  activeOverlayTab: null, // v5.1: 当前浮层显示的Tab名，null=无浮层
  // v5.4: 板块分析面板状态
  sectorRankingCache: null,       // 首次加载缓存的 31 板块排行数据
  sectorCurrentPeriod: '5',       // 当前周期 ('5'|'20'|'60'|'120'|'250')
  sectorSortOrder: 'desc',        // 排序方向 ('desc'|'asc')
  sectorSelectedKey: '',          // 当前选中的板块英文 key
  sectorSelectedName: '',         // 当前选中的板块中文名
};

// ================================================================
//  板块配置 — 26个板块（与 server/config.py 的 SECTORS 一一对应）
//  改动本数组时必须同步后端，否则前端 tab / 下拉框会漏板块
// ================================================================
const SECTORS = [
  { key: 'semiconductor', name: '半导体', icon: '<rect x="6" y="6" width="12" height="12" rx="1"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/>' },
  { key: 'new_energy', name: '新能源', icon: '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>' },
  { key: 'pharma', name: '医药', icon: '<path d="M9 2h6v6h6v6h-6v6H9v-6H3V8h6z"/>' },
  { key: 'consumer', name: '消费', icon: '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 002 1.61h9.72a2 2 0 002-1.61L23 6H6"/>' },
  { key: 'finance', name: '金融', icon: '<path d="M3 21h18M3 10h18M5 6l7-3 7 3M4 10v11M8 10v11M12 10v11M16 10v11M20 10v11"/>' },
  { key: 'military', name: '军工', icon: '<path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 00-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/>' },
  { key: 'ai_compute', name: 'AI算力', icon: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/>' },
  { key: 'photovoltaic', name: '光伏', icon: '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>' },
  { key: 'internet', name: '互联网', icon: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/>' },
  { key: 'real_estate', name: '房地产', icon: '<path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><path d="M9 22V12h6v10"/>' },
  { key: 'steel_material', name: '钢铁有色', icon: '<path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/>' },
  { key: 'auto', name: '汽车', icon: '<path d="M14 16H9m10 0h3v-3.15a1 1 0 00-.84-.99L16 11l-2.7-3.6a1 1 0 00-.8-.4H5.24a2 2 0 00-1.8 1.1L2 11v5h3"/><circle cx="6.5" cy="16.5" r="2.5"/><circle cx="16.5" cy="16.5" r="2.5"/>' },
  { key: 'power_equip', name: '电力设备', icon: '<rect x="2" y="7" width="16" height="10" rx="2"/><path d="M22 11v2"/><path d="M6 10v4M10 10v4"/>' },
  { key: 'telecom', name: '通信', icon: '<path d="M5 12.55a11 11 0 0114.08 0M1.42 9a16 16 0 0121.16 0M8.53 16.11a6 6 0 016.95 0"/><circle cx="12" cy="20" r="1"/>' },
  { key: 'etf', name: 'ETF基金', icon: '<path d="M12 20V10M18 20V4M6 20v-6"/>' },
  { key: 'cyber', name: '网络安全', icon: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>' },
  { key: 'consumer_electronics', name: '消费电子', icon: '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>' },
  { key: 'chemical', name: '化工新材料', icon: '<path d="M9 3h6v4l5 12a2 2 0 01-2 3H6a2 2 0 01-2-3l5-12V3z"/><path d="M7 14h10"/>' },
  { key: 'machinery', name: '机械设备', icon: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/>' },
  { key: 'environmental', name: '环保节能', icon: '<path d="M11 20A7 7 0 019.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10z"/><path d="M2 21c0-3 1.85-5.36 5.08-6"/>' },
  { key: 'textile', name: '纺织服装', icon: '<path d="M20.38 3.46L16 2a4 4 0 01-8 0L3.62 3.46a2 2 0 00-1.34 2.23l.58 3.47a1 1 0 00.99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 002-2V10h2.15a1 1 0 00.99-.84l.58-3.47a2 2 0 00-1.34-2.23z"/>' },
  { key: 'agriculture', name: '农业', icon: '<path d="M12 20V8M12 8c0-3-2-5-5-5M12 8c0-3 2-5 5-5M12 14c0-3-2-5-5-5M12 14c0-3 2-5 5-5"/>' },
  { key: 'transport', name: '交通运输', icon: '<rect x="1" y="3" width="15" height="13" rx="2"/><path d="M16 8h4l3 3v5h-7V8z"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>' },
  { key: 'building_materials', name: '建材', icon: '<rect x="3" y="4" width="6" height="6" rx="1"/><rect x="11" y="4" width="6" height="6" rx="1"/><rect x="7" y="12" width="6" height="6" rx="1"/><rect x="15" y="12" width="6" height="6" rx="1"/>' },
  // v5.5 补齐：后端 server/config.py 已有、前端此前缺失的 2 个板块
  { key: 'media_entertainment', name: '传媒娱乐', icon: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M10 9l5 3-5 3V9z"/>' },
  { key: 'hk_stocks', name: '港股通', icon: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/>' },
];

// ================================================================
//  v5.4: 板块中文名 → 英文 key 映射表
//  用于 API /api/sector-ranking 返回的中文名匹配到英文 key
// ================================================================
const SECTOR_NAME_TO_KEY = {
  '电子': 'semiconductor',
  '半导体': 'semiconductor',
  '电力设备': 'power_equip',
  '通信': 'telecom',
  '医药生物': 'pharma',
  '医药': 'pharma',
  '食品饮料': 'consumer',
  '消费': 'consumer',
  '银行': 'finance',
  '金融': 'finance',
  '国防军工': 'military',
  '军工': 'military',
  '计算机': 'ai_compute',
  'AI算力': 'ai_compute',
  '电力': 'photovoltaic',
  '光伏': 'photovoltaic',
  '传媒': 'media_entertainment',
  '传媒娱乐': 'media_entertainment',
  '文化传媒': 'media_entertainment',
  '互联网': 'internet',
  '房地产': 'real_estate',
  '钢铁': 'steel_material',
  '钢铁有色': 'steel_material',
  '汽车': 'auto',
  '消费电子': 'consumer_electronics',
  '化工新材料': 'chemical',
  '机械设备': 'machinery',
  '环保节能': 'environmental',
  '纺织服装': 'textile',
  '农业': 'agriculture',
  '交通运输': 'transport',
  '建材': 'building_materials',
  '网络安全': 'cyber',
  '新能源': 'new_energy',
  'ETF基金': 'etf',
  '港股通': 'hk_stocks',
  '港股': 'hk_stocks',
  '恒生': 'hk_stocks',
  '恒生指数': 'hk_stocks',
  '港股通标的': 'hk_stocks',
};

/**
 * v5.4: 解析板块英 key — 优先 API 返回的 key/code，其次查映射表，降级用中文名
 * @param {Object} sectorData - API 返回的单行板块数据
 * @returns {string} 英文 key
 */
function resolveSectorKey(sectorData) {
  if (sectorData.key) return sectorData.key;
  if (sectorData.code) return sectorData.code;
  var name = sectorData.name || sectorData['名称'] || '';
  if (SECTOR_NAME_TO_KEY[name]) return SECTOR_NAME_TO_KEY[name];
  console.warn('Sector key not found for:', name, '— using name as key');
  return name;
}

/**
 * v5.4: 周期 key → API 列名映射
 * '5' → '5日%', '20' → '20日%', etc.
 */
function _periodToColKey(period) {
  return period + '日%';
}

// ================================================================
//  K线区域 Tab 配置 — v5.0 MCP数据源集成
// ================================================================

/**
 * K线区域Tab配置 — 每个Tab对应一个懒加载函数
 * strategy tab不需要加载函数（已有策略面板逻辑）
 * 其余Tab在切换时触发对应数据加载
 */
const KLINE_TABS = {
  'strategy': { loadFn: null },
  'fund-flow': { loadFn: function(code) { if (typeof loadFundFlowTab === 'function') loadFundFlowTab(code); } },
  'profile': { loadFn: function(code) { if (typeof loadProfileTab === 'function') loadProfileTab(code); } },
  // v37（T09）: 基本面 6 卡 + 估值分位条，实现在 js/fundamental.js
  'fundamental': { loadFn: function(code) {
    if (typeof window.renderFundamental === 'function') window.renderFundamental(code);
    if (typeof window.renderValuationBar === 'function') window.renderValuationBar(code);
  } },
  'report': { loadFn: function(code) { if (typeof loadReportTab === 'function') loadReportTab(code); } },
  'north': { loadFn: function(code) { if (typeof loadNorthTab === 'function') loadNorthTab(code); } },
  'news': { loadFn: function(code) { if (typeof loadNewsTab === 'function') loadNewsTab(code); } },
  'strategy-lab': { loadFn: function(code) { if (typeof loadStrategyLab === 'function') loadStrategyLab(code); } },
  'portfolio': { loadFn: function() { if (typeof renderPortfolio === 'function') renderPortfolio(); } },
};

/**
 * Tab名称中文标签映射 — v5.1 浮层标题使用
 */
const TAB_LABELS = {
  'strategy': '策略评分',
  'fund-flow': '资金流向',
  'profile': '个股简况',
  'fundamental': '基本面',
  'report': '研报',
  'north': '北向资金',
  'news': '资讯',
  'strategy-lab': '策略实验室',
  'portfolio': '模拟持仓',
};

/**
 * 更新K线Tab按钮高亮状态 — v5.1
 * @param {string} activeTab - 当前激活的Tab名称
 */
function updateKlineTabButtons(activeTab) {
  document.querySelectorAll('#kline-tabs .ktab').forEach(function(btn) {
    btn.classList.toggle('active', btn.getAttribute('data-tab') === activeTab);
  });
}

/**
 * 隐藏K线Tab浮层 — v5.1
 * 移除浮层visible类，重置activeOverlayTab状态
 */
function hideKlineOverlay() {
  var currentTab = window.appState.activeOverlayTab;
  if (currentTab) restoreTabContent(currentTab);
  var overlay = document.getElementById('kline-overlay');
  if (overlay) overlay.classList.remove('visible');
  window.appState.activeOverlayTab = null;
  window.appState.activeTab = 'strategy';
  updateKlineTabButtons('strategy');
}

/**
 * 将Tab内容从浮层移回原始DOM位置 — v6.2
 * 防止 tab 内容面板因 DOM 移动丢失
 * @param {string} tabName - Tab名称
 */
function restoreTabContent(tabName) {
  var overlayBody = document.getElementById('kline-overlay-body');
  if (!overlayBody) return;
  var tabContent = document.getElementById('tab-' + tabName);
  if (!tabContent) return;
  // 如果 tab 内容在 overlay 内，移回原始位置（kline-tabs 下方）
  if (tabContent.parentElement === overlayBody) {
    var tabStash = document.getElementById('kline-tab-stash');
    if (tabStash) {
      tabStash.appendChild(tabContent);
      tabContent.style.display = 'none';
    }
  }
  overlayBody.innerHTML = '';
}

/**
 * 切换K线区域Tab — v5.1 浮层化改造
 *
 * 行为规则：
 * 1. 点击"策略评分" → 收起浮层，回到K线视图
 * 2. 点击当前已展开的同一Tab → 收起浮层
 * 3. 点击其他Tab → 展开/切换浮层，懒加载数据
 *
 * @param {string} tabName - Tab名称: strategy/fund-flow/profile/report/north/news
 */
function switchKlineTab(tabName) {
  if (!KLINE_TABS[tabName]) return;

  var overlay = document.getElementById('kline-overlay');
  var overlayTitle = document.getElementById('kline-overlay-title');
  var overlayBody = document.getElementById('kline-overlay-body');

  // 1. 策略评分Tab → 收起浮层，将当前Tab内容移回原位
  if (tabName === 'strategy') {
    if (window.appState.activeOverlayTab) {
      restoreTabContent(window.appState.activeOverlayTab);
    }
    hideKlineOverlay();
    window.appState.activeTab = 'strategy';
    updateKlineTabButtons('strategy');
    return;
  }

  // 2. 点击当前已展开的同一Tab → 收起浮层，将内容移回原位
  if (window.appState.activeOverlayTab === tabName) {
    restoreTabContent(tabName);
    hideKlineOverlay();
    window.appState.activeTab = 'strategy';
    updateKlineTabButtons('strategy');
    return;
  }

  // 3. 展开新Tab浮层
  window.appState.activeTab = tabName;
  window.appState.activeOverlayTab = tabName;

  // 设置浮层标题
  var tabLabel = TAB_LABELS[tabName] || tabName;
  if (overlayTitle) overlayTitle.textContent = tabLabel;

  // 将对应Tab内容面板移入浮层body（appendChild移动DOM，不丢失已渲染内容）
  var tabContent = document.getElementById('tab-' + tabName);
  if (tabContent && overlayBody) {
    overlayBody.innerHTML = '';
    overlayBody.appendChild(tabContent);
    tabContent.style.display = 'block';
  }

  // 显示浮层
  if (overlay) overlay.classList.add('visible');
  updateKlineTabButtons(tabName);

  // 懒加载数据
  var tabConfig = KLINE_TABS[tabName];
  if (tabConfig.loadFn && window.appState.activeCode) {
    tabConfig.loadFn(window.appState.activeCode);
  }
}

// ================================================================
//  v5.1: 浮层外部点击收起 + ESC键收起
// ================================================================
(function _initOverlayDismissHandlers() {
  function bindHandlers() {
    // v40: 在**捕获阶段**先把「这次点击是否落在浮层 / Tab 栏内」判定结果记下来。
    //
    // 为什么不能在冒泡阶段现场判定：浮层内的行点击处理器可能同步重渲染，
    // 把被点节点从文档树上摘掉（如 fundamental.js:671 的重试按钮委托，
    // 它注册在 document 上、且因脚本加载顺序早于 app.js 而先执行）。
    // 等本监听器在冒泡阶段跑到时，e.target 已是游离节点，
    // closest('#kline-overlay') 返回 null → 被误判成「点了浮层外部」→ 误关浮层。
    // 捕获阶段在该节点自身的处理器之前执行，此时祖先链完好，判定才可信。
    var _clickInsideOverlay = null;
    document.addEventListener('click', function(e) {
      var t = e.target;
      _clickInsideOverlay = !!(t && t.closest && (t.closest('#kline-overlay') || t.closest('#kline-tabs')));
    }, true);

    // 点击浮层外部收起（冒泡阶段执行，保留原有「被 stopPropagation 拦下就不收起」的语义）
    document.addEventListener('click', function(e) {
      var overlay = document.getElementById('kline-overlay');
      if (!overlay || !overlay.classList.contains('visible')) return;
      var inside = _clickInsideOverlay;
      if (inside === null) {
        // 兜底：捕获监听未触发（极端情况），退回即时判定，行为与改造前一致
        inside = !!(e.target.closest && (e.target.closest('#kline-overlay') || e.target.closest('#kline-tabs')));
      }
      if (!inside) {
        hideKlineOverlay();
        window.appState.activeTab = 'strategy';
        updateKlineTabButtons('strategy');
      }
      // 每次点击消费完就清空，不让一次判定结果跨事件存活。
      // 正常流程下捕获必先于冒泡、值必然被覆盖，这行是防御：
      // 万一出现「未经 document 捕获却走到这里」的路径，宁可退回 null 走兜底判定，
      // 也不要用上一次点击的陈旧结果去决定「是否收起浮层」。
      _clickInsideOverlay = null;
    });

    // ESC键收起浮层
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && window.appState.activeOverlayTab) {
        hideKlineOverlay();
        window.appState.activeTab = 'strategy';
        updateKlineTabButtons('strategy');
      }
    });
  }

  // DOM is already ready (scripts at end of body) or wait for DOMContentLoaded
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindHandlers);
  } else {
    bindHandlers();
  }
})();

// ================================================================
//  鼠标光晕跟踪（节能自适应 RAF 循环）
// ================================================================
function initMouseGlow() {
  const glow = document.getElementById('mouse-glow');
  if (!glow) return;

  let targetX = 0, targetY = 0;
  let currentX = 0, currentY = 0;
  let isRunning = false;

  function update() {
    if (document.hidden) {
      isRunning = false;
      return;
    }
    const dx = targetX - currentX;
    const dy = targetY - currentY;
    
    // 当移动距离极其微小时自动休眠，彻底避免 CPU 持续空转
    if (Math.abs(dx) < 0.25 && Math.abs(dy) < 0.25) {
      currentX = targetX;
      currentY = targetY;
      glow.style.transform = `translate3d(${currentX}px, ${currentY}px, 0) translate(-50%, -50%)`;
      isRunning = false;
      return;
    }

    currentX += dx * 0.15;
    currentY += dy * 0.15;
    glow.style.transform = `translate3d(${currentX}px, ${currentY}px, 0) translate(-50%, -50%)`;
    requestAnimationFrame(update);
  }

  document.addEventListener('mousemove', (e) => {
    targetX = e.clientX;
    targetY = e.clientY;
    if (!glow.classList.contains('active')) glow.classList.add('active');
    if (!isRunning) {
      isRunning = true;
      requestAnimationFrame(update);
    }
  }, { passive: true });

  document.addEventListener('mouseleave', () => {
    glow.classList.remove('active');
    isRunning = false;
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) isRunning = false;
  });
}

// ================================================================
//  涟漪效果
// ================================================================
function addRipple(e, el) {
  const rect = el.getBoundingClientRect();
  const ripple = document.createElement('span');
  ripple.className = 'ripple';
  // v7: 颜色继承行的涨跌方向（红涨绿跌），200px 圆形涟漪
  const chgEl = el.querySelector('.stock-chg');
  if (chgEl && chgEl.classList.contains('chg-dn')) ripple.classList.add('ripple-dn');
  const size = 200;
  ripple.style.width = ripple.style.height = size + 'px';
  ripple.style.left = (e.clientX - rect.left - size / 2) + 'px';
  ripple.style.top = (e.clientY - rect.top - size / 2) + 'px';
  el.appendChild(ripple);
  setTimeout(() => ripple.remove(), 550);
}

// ================================================================
//  Toast 通知
// ================================================================
let toastTimer = null;
function showToast(title, body) {
  const el = document.getElementById('signal-toast');
  if (!el) return;
  const titleEl = document.getElementById('toast-title');
  const bodyEl = document.getElementById('toast-body');
  if (titleEl) titleEl.textContent = title;
  if (bodyEl) bodyEl.textContent = body || '';
  el.classList.remove('show');
  void el.offsetWidth;
  el.classList.add('show');
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 4000);
}

// ================================================================
//  状态指示
// ================================================================
function setStatus(type) {
  const dot = document.getElementById('status-dot');
  if (!dot) return;
  if (type === 'loading') { dot.style.background = '#f59e0b'; dot.style.boxShadow = '0 0 8px #f59e0b'; }
  else if (type === 'error') { dot.style.background = '#ff3d6a'; dot.style.boxShadow = '0 0 8px #ff3d6a'; }
  else { dot.style.background = 'var(--green)'; dot.style.boxShadow = '0 0 8px var(--green)'; }
}

// ================================================================
//  时钟
// ================================================================
function updateClock() {
  const now = new Date();
  const h = String(now.getHours()).padStart(2, '0');
  const m = String(now.getMinutes()).padStart(2, '0');
  const s = String(now.getSeconds()).padStart(2, '0');
  const clockEl = document.getElementById('clock');
  if (clockEl) clockEl.textContent = `${h}:${m}:${s}`;
}

// ================================================================
//  左侧导航栏渲染
// ================================================================
function renderSectorBar() {
  const container = document.getElementById('sidebar-sectors');
  if (!container) return;
  const sectorCounts = window.appState._sectorCounts || {};
  container.innerHTML = SECTORS.map(s => {
    const count = sectorCounts[s.key];
    const countStr = count ? ` <span class="sector-count">${count}</span>` : '';
    return `<div class="sidebar-sector${s.key === window.appState.activeSector ? ' active' : ''}" onclick="switchSector('${s.key}')" title="${s.name}">
      <div class="sidebar-sector-icon"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${s.icon}</svg></div>
      <div class="sidebar-sector-name">${s.name}${countStr}</div>
    </div>`;
  }).join('');
}

/**
 * 从后端加载板块股票数量元数据
 */
async function loadSectorCounts() {
  try {
    const resp = await fetch('/api/sectors');
    if (!resp.ok) return;
    const json = await resp.json();
    if (json.ok && json.sectors) {
      const counts = {};
      for (const s of json.sectors) {
        counts[s.key] = s.stock_count;
      }
      window.appState._sectorCounts = counts;
      renderSectorBar();
    }
  } catch (e) {
    console.warn('loadSectorCounts failed:', e.message);
  }
}

/**
 * 切换板块
 */
var _lastSectorSwitch = 0;
async function switchSector(sectorKey) {
  // Throttle: 防止快速点击导致多次加载
  var now = Date.now();
  if (now - _lastSectorSwitch < 300) return;
  _lastSectorSwitch = now;

  window.appState.activeSector = sectorKey;
  window.appState.listFilter = 'all';
  window.appState.screenMode = false;

  renderSectorBar();

  const sectorName = SECTORS.find(s => s.key === sectorKey)?.name || sectorKey;
  const titleEl = document.getElementById('sector-title-name');
  if (titleEl) titleEl.textContent = sectorName;

  document.querySelectorAll('#left-panel .tab').forEach(t => {
    t.classList.toggle('active', t.getAttribute('data-filter') === 'all');
  });

  const screenPanel = document.getElementById('screen-panel');
  if (screenPanel) screenPanel.style.display = 'none';

  showStockListSkeleton();
  // 切换板块也用 fast 模式快速响应，3秒后后台补全策略评分
  await loadQuotes(true);

  // 切换板块后自动加载第一只股票的K线
  if (window.appState.stocks.length > 0) {
    const firstCode = window.appState.stocks[0].code;
    // 切板块是「换观察域」的用户动作，重置到新板块第一只是预期行为
    window.appState.activeCode = firstCode;
    window.appState.activeName = window.appState.stocks[0].name;
    renderStockList();
    await _ensureChartReady();
    // 必须带版本号：否则上一次 selectStock 的迟到 K 线响应会盖掉本板块的图，
    // 造成「activeCode=新板块第一只 / 图上还是旧标的」的分裂。
    window.appState._klineVersion++;
    loadAndRenderKline(firstCode, window.appState._klineVersion);  // 不await，异步加载K线
  }

  // 板块/标的变化后同步跨页入口的上下文参数
  _syncContextLinks();

  // 后台补全策略评分
  setTimeout(() => {
    fetchQuotes(window.appState.activeSector, false).then(data => {
      if (data && data.stocks && data.sector === window.appState.activeSector) {
        for (const s of data.stocks) {
          if (window.appState.stockMap[s.code]) {
            window.appState.stockMap[s.code].score = s.score;
            window.appState.stockMap[s.code].signals = s.signals;
          }
        }
        for (const s of data.stocks) { s.sector = window.appState.activeSector; }
        window.appState.stocks = data.stocks;
        renderStockList();
        updateKPICards();
      }
    }).catch(() => {});
  }, 2000);
}

// ================================================================
//  KPI 卡片更新
//  实现在下方 updateKPICards()（v10 版本，唯一写入方）。
//  注：此处原有一个同名 updateKPICards() 重复定义，被下方定义覆盖（JS 后者生效）
//  导致本处永不执行，且其独有写入（#kpi-amount-chg/#kpi-turnover-desc）长期落空——
//  已删除该重复定义，并把这两处写入并入下方唯一实现。
// ================================================================

// ================================================================
//  因子雷达图
// ================================================================
function initRadarChart() {
  const el = document.getElementById('radar-chart');
  if (!el) return;
  window.appState.radarChart = echarts.init(el, null, { renderer: 'canvas' });
  // 绘制默认占位五边形（所有维度=50），避免空白卡片
  updateRadarChart(50, 50, 50, 50, 50);
}

function updateRadarChart(momentumScore, breakoutScore, volumeScore, trendScore, riskScore) {
  const chart = window.appState.radarChart;
  if (!chart) return;

  const option = {
    backgroundColor: 'transparent',
    radar: {
      indicator: [
        { name: '动量', max: 100 },
        { name: '突破', max: 100 },
        { name: '量价', max: 100 },
        { name: '趋势', max: 100 },
        { name: '风险', max: 100 },
      ],
      shape: 'circle',
      splitNumber: 4,
      axisName: { color: '#8b9ec2', fontSize: 9 },
      splitLine: { lineStyle: { color: 'rgba(59,130,246,0.08)' } },
      splitArea: { areaStyle: { color: ['rgba(59,130,246,0.02)', 'rgba(59,130,246,0.04)'] } },
      axisLine: { lineStyle: { color: 'rgba(59,130,246,0.1)' } },
    },
    series: [{
      type: 'radar',
      data: [{
        value: [momentumScore, breakoutScore, volumeScore, trendScore, riskScore],
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(59,130,246,0.25)' },
            { offset: 1, color: 'rgba(6,182,212,0.08)' },
          ]),
        },
        lineStyle: { color: '#3b82f6', width: 1.5 },
        itemStyle: { color: '#06b6d4' },
        symbol: 'circle',
        symbolSize: 4,
      }],
    }],
    animation: true,
    animationDuration: 500,
  };

  chart.setOption(option, { notMerge: true });
}

// ================================================================
//  选股信号排行表格
// ================================================================
function toggleSignalTable() {
  const wrap = document.getElementById('signal-table-wrap');
  const toggle = document.getElementById('signal-table-toggle');
  if (!wrap || !toggle) return;

  window.appState.signalTableVisible = !window.appState.signalTableVisible;
  wrap.style.display = window.appState.signalTableVisible ? 'block' : 'none';
  toggle.style.display = window.appState.signalTableVisible ? 'none' : 'flex';

  if (window.appState.signalTableVisible) {
    renderSignalTable();
  }
}

function renderSignalTable() {
  const tbody = document.getElementById('signal-table-body');
  if (!tbody) return;

  let items = [...window.appState.stocks];
  const sortKey = window.appState.signalTableSort;

  if (sortKey === 'score') {
    items.sort((a, b) => (b.score || 0) - (a.score || 0));
  } else if (sortKey === 'chg') {
    items.sort((a, b) => (b.chg || 0) - (a.chg || 0));
  }

  tbody.innerHTML = items.map((item, idx) => {
    const chg = item.chg || 0;
    const price = item.price || 0;
    const score = item.score || 0;
    const chgCls = chg > 0 ? 'up' : chg < 0 ? 'dn' : 'flat';
    const scoreColor = score >= 70 ? 'var(--red)' : score >= 50 ? 'var(--gold)' : 'var(--green)';
    const safeName = escapeHtml(item.name || item.code);
    const safeCode = escapeHtml(item.code);
    const signals = (item.signals || []).slice(0, 3).map(s =>
      `<span class="stag ${escapeHtml(s.type)}">${escapeHtml(s.label)}</span>`
    ).join(' ');

    return `<tr onclick="selectStock('${safeCode}','${safeName}')" style="cursor:pointer">
      <td style="color:var(--muted)">${idx + 1}</td>
      <td style="font-weight:600">${safeName}</td>
      <td style="color:var(--muted);font-size:10px">${safeCode}</td>
      <td style="color:${scoreColor};font-weight:700;font-family:'Courier New',monospace">${score}</td>
      <td>${signals || '<span style="color:var(--muted);font-size:10px">--</span>'}</td>
      <td class="${chgCls}" style="font-family:'Courier New',monospace;font-weight:600">${chg > 0 ? '+' : ''}${chg.toFixed(2)}%</td>
      <td style="font-family:'Courier New',monospace">${price ? price.toFixed(2) : '--'}</td>
    </tr>`;
  }).join('');
}

function sortSignalTable(key) {
  window.appState.signalTableSort = key;
  renderSignalTable();
}

// ================================================================
//  骨架屏
// ================================================================
function showStockListSkeleton() {
  const list = document.getElementById('stock-list');
  if (!list) return;
  let html = '';
  for (let i = 0; i < 12; i++) {
    html += `
      <div class="stock-row skeleton" style="height: 52px; padding: 10px; display: flex; align-items: center; gap: 12px; opacity: 0.6;">
        <div class="shimmer" style="width: 20px; height: 16px; border-radius: 4px; background: rgba(255,255,255,0.05);"></div>
        <div style="flex: 1;">
          <div class="shimmer" style="width: 60px; height: 14px; border-radius: 4px; background: rgba(255,255,255,0.05); margin-bottom: 6px;"></div>
          <div class="shimmer" style="width: 40px; height: 10px; border-radius: 4px; background: rgba(255,255,255,0.05);"></div>
        </div>
        <div class="shimmer" style="width: 50px; height: 20px; border-radius: 4px; background: rgba(255,255,255,0.05);"></div>
        <div class="shimmer" style="width: 60px; height: 24px; border-radius: 4px; background: rgba(255,255,255,0.05);"></div>
      </div>
    `;
  }
  list.innerHTML = html;
}

// ================================================================
//  选中标的守卫（单一真相源）
//  背景：任何「刷新类」流程都不许无条件覆盖用户选中——历史上 app.js:3073
//  在每次行情刷新里把 activeCode 改成新列表第一只，但图表没重绘，导致
//  activeCode 与实际渲染标的永久分裂（连带 ICT / 指标 / 统计拿到错标的去请求）。
//  规则：
//    · 用户当前选中仍在新列表里 → 原样保留（只同步可能变了的名称）
//    · 已不在新列表里（如切板块 / 该股被移出股池）→ 回退到新列表第一只
//    · 新列表为空 → 不清空选中（刷新失败不该让用户丢掉正在看的标的）
// ================================================================

/**
 * 刷新列表后决定 activeCode 该保留还是回退。
 * @param {Array<Object>} stocks - 刷新后的新股池
 * @returns {{code: string, changed: boolean}} 刷新后的 activeCode，以及它是否发生了变化
 */
function _preserveActiveStock(stocks) {
  var list = Array.isArray(stocks) ? stocks : [];
  var prev = window.appState.activeCode || '';

  if (list.length === 0) {
    // 空列表（多为本次刷新失败）：保留原选中，避免图表/标的信息被清空
    return { code: prev, changed: false };
  }

  for (var i = 0; i < list.length; i++) {
    if (list[i] && list[i].code === prev) {
      // 仍在列表中：只同步名称，绝不改 activeCode
      if (list[i].name) window.appState.activeName = list[i].name;
      return { code: prev, changed: false };
    }
  }

  // 已不在列表中：回退到第一只
  window.appState.activeCode = list[0].code;
  window.appState.activeName = list[0].name;
  return { code: list[0].code, changed: prev !== list[0].code };
}

/**
 * 图上**真实渲染**的标的（只读 chart.js 的 _dpRenderCtx，不改它）。
 * chart.js 在 renderKline / renderKlinePro 里写入，是最可信的「图上是谁」。
 * @returns {string} 图上标的代码，未渲染过返回空串
 */
function _chartRenderedCode() {
  var ctx = window._dpRenderCtx;
  return (ctx && ctx.code) ? String(ctx.code) : '';
}

/**
 * 刷新后一致性守卫：activeCode 与图上标的**不一致**时才补一次重绘，
 * 一致则什么都不做（避免无谓请求与闪烁）。
 * @returns {boolean} 是否触发了重绘
 */
function _syncChartAfterRefresh() {
  var want = window.appState.activeCode || '';
  if (!want) return false;

  var onChart = _chartRenderedCode();
  if (!onChart) return false;          // 图表尚未渲染过（首屏）→ 交给 init 第二阶段
  if (onChart === want) return false;  // 已一致 → 不重绘

  if (typeof loadAndRenderKline !== 'function') return false;
  window.appState._klineVersion++;
  loadAndRenderKline(want, window.appState._klineVersion);
  updateKlineHeader();
  return true;
}

// ================================================================
//  行情加载与渲染
// ================================================================
async function loadQuotes(fast) {
  setStatus('loading');
  const data = await fetchQuotes(window.appState.activeSector, fast);
  if (!data) {
    setStatus('error');
    showToast('! 加载失败', '行情数据获取失败，请检查网络后刷新');
    // 显示空状态提示
    const list = document.getElementById('stock-list');
    if (list) {
      list.innerHTML = '<div class="error-empty"><div class="error-empty-icon">信</div><div class="error-empty-text">行情数据加载失败</div><div class="error-empty-hint">请检查网络连接或稍后重试</div><button class="retry-btn" onclick="refreshAll()">↻ 重试</button></div>';
    }
    return;
  }

  window.appState.stocks = data.stocks || [];

  // 注入 sector 字段，供 calculateSectorStats() 使用
  for (const stock of window.appState.stocks) {
    stock.sector = window.appState.activeSector;
  }

  window.appState.stockMap = {};
  for (const stock of window.appState.stocks) {
    window.appState.stockMap[stock.code] = stock;
  }

  if (window.appState.stocks.length > 0 && !window.appState.activeCode) {
    window.appState.activeCode = window.appState.stocks[0].code;
    window.appState.activeName = window.appState.stocks[0].name;
  }

  renderStockList();
  updateKlineHeader();
  updateKPICards();

  // 更新信号表格（如果展开）
  if (window.appState.signalTableVisible) {
    renderSignalTable();
  }

  setStatus('ok');
}

/**
 * 渲染股票列表 - 增量更新优化
 * v7: 支持 排序 / 信号标签筛选 / 置顶 / 空状态；顺序变化时自动全量重绘
 */
function renderStockList() {
  const list = document.getElementById('stock-list');
  if (!list) return;

  const filter = window.appState.listFilter;
  let items = [...(window.appState.stocks || [])];

  if (filter === 'up') items = items.filter(i => (i.chg || 0) > 0);
  else if (filter === 'dn') items = items.filter(i => (i.chg || 0) < 0);
  else if (filter === 'fav') items = items.filter(i => (window.appState.favoriteStocks || []).includes(i.code));

  // v7: 信号标签筛选（点击 meta 行的 .stag 标签触发）
  if (window.appState.signalFilter) {
    items = items.filter(i => (i.signals || []).some(s => s.type === window.appState.signalFilter));
  }

  // v7: 排序（置顶股票始终优先）
  items = sortStockItems(items);

  // v7: 空状态
  if (items.length === 0) {
    list.innerHTML = buildStockListEmpty(filter, window.appState.signalFilter);
    updateDistBar([]);
    return;
  }

  const scrollTop = list.scrollTop;

  // v7: 比较现有行与新行的顺序（含集合差异），顺序不同则全量重绘
  const existingSeq = Array.from(list.querySelectorAll('.stock-row'))
    .map(r => r.getAttribute('data-code')).join('|');
  const newSeq = items.map(i => i.code).join('|');

  if (existingSeq !== newSeq) {
    list.innerHTML = items.map((item, idx) => buildStockRow(item, idx)).join('');
  } else {
    items.forEach((item, idx) => {
      const row = list.querySelector(`.stock-row[data-code="${item.code}"]`);
      if (row) {
        updateStockRow(row, item, idx);
      }
    });
  }

  if (scrollTop > 0) list.scrollTop = scrollTop;
  drawAllSparklines(items);
  updateDistBar(items);
  // v10: 记录本轮排名，供下一轮 diff 出排名变化箭头
  if (!window.appState.prevRanking) window.appState.prevRanking = {};
  items.forEach((it, i) => { window.appState.prevRanking[it.code] = i; });
}

/**
 * v9: 涨跌分布微直方图（9 档 bin，红涨绿跌，柱高=数量占比）
 */
function updateDistBar(items){
  var bar = document.getElementById('dist-bar');
  if (!bar) return;
  if (!items || !items.length){
    bar.innerHTML = '';
    return;
  }
  var bins = [
    { min: -Infinity, max: -5,      cls: 'dn',   label: '跌幅 >5%' },
    { min: -5,        max: -3,      cls: 'dn',   label: '跌 3~5%' },
    { min: -3,        max: -1,      cls: 'dn',   label: '跌 1~3%' },
    { min: -1,        max: 0,       cls: 'dn',   label: '跌 0~1%' },
    { min: 0,         max: 0.0001,  cls: 'flat', label: '持平' },
    { min: 0.0001,    max: 1,       cls: 'up',   label: '涨 0~1%' },
    { min: 1,         max: 3,       cls: 'up',   label: '涨 1~3%' },
    { min: 3,         max: 5,       cls: 'up',   label: '涨 3~5%' },
    { min: 5,         max: Infinity, cls: 'up',  label: '涨幅 >5%' }
  ];
  var counts = bins.map(function(b){
    return items.filter(function(i){ var c = i.chg || 0; return c >= b.min && c < b.max; }).length;
  });
  var maxC = Math.max.apply(null, counts.concat([1]));
  bar.innerHTML = bins.map(function(b, i){
    var ratio = counts[i] / maxC;
    var h = counts[i] === 0 ? 2 : Math.max(8, Math.round(ratio * 100));
    // v10: 柱顶数字（仅较高柱显示，避免拥挤）
    var num = (counts[i] > 0 && ratio >= 0.5) ? '<span class="dist-num">' + counts[i] + '</span>' : '';
    return '<div class="dist-bin ' + b.cls + '" style="height:' + h + '%" title="' + b.label + '：' + counts[i] + ' 只">' + num + '</div>';
  }).join('');
}

/**
 * v7: 列表排序（置顶优先）
 */
function sortStockItems(items) {
  const key = window.appState.sortKey || 'score';
  const dir = window.appState.sortDir === 'asc' ? 1 : -1;
  const pinned = window.appState.pinnedStocks || [];
  const val = (i) => {
    switch (key) {
      case 'chg': return i.chg || 0;
      case 'turnover': return i.turnover || 0;
      case 'volume_ratio': return i.volume_ratio || 0;
      case 'code': return i.code || '';
      default: return i.score || 0;
    }
  };
  return items.sort((a, b) => {
    const pa = pinned.includes(a.code) ? 1 : 0;
    const pb = pinned.includes(b.code) ? 1 : 0;
    if (pa !== pb) return pb - pa; // 置顶优先
    const va = val(a);
    const vb = val(b);
    if (typeof va === 'string' || typeof vb === 'string') {
      return String(va).localeCompare(String(vb)) * dir;
    }
    if (va === vb) return 0;
    return (va > vb ? 1 : -1) * dir;
  });
}

/**
 * v7: 空状态（插画 + 提示语）
 */
function buildStockListEmpty(filter, signalFilter) {
  const isFav = filter === 'fav';
  const isSig = !!signalFilter;
  let title = '暂无股票数据';
  let hint = '请切换板块或刷新重试';
  if (isSig) {
    title = '该信号下暂无股票';
    hint = '再次点击标签可取消筛选，或切换其它板块';
  } else if (isFav) {
    title = '暂无自选股';
    hint = '悬停股票行，点击 ★ 即可加入自选';
  } else if (filter === 'up' || filter === 'dn') {
    title = '当前涨跌筛选无结果';
    hint = '试试切回「全部」标签或刷新数据';
  }
  const showBtn = !isFav && !isSig && filter !== 'up' && filter !== 'dn';
  return `
    <div class="stock-empty">
      <svg width="96" height="66" viewBox="0 0 96 66" fill="none" stroke="var(--muted)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="6" y="8" width="52" height="50" rx="6" stroke="var(--border2)"/>
        <path d="M14 44l9-10 8 6 12-16"/>
        <path d="M43 24h6v-6"/>
        <circle cx="68" cy="40" r="14" stroke="var(--blue)" opacity="0.7"/>
        <path d="M78 50l12 12" stroke="var(--blue)" opacity="0.7"/>
        <path d="M63 40h10M68 35v10" stroke="var(--blue)" opacity="0.5"/>
      </svg>
      <div class="stock-empty-title">${title}</div>
      <div class="stock-empty-hint">${hint}</div>
      ${showBtn ? '<button class="stock-empty-btn" onclick="refreshAll()">↻ 刷新数据</button>' : ''}
    </div>
  `;
}

/**
 * v7: 评分颜色（徽章 + 环形共用）
 */
function scoreColorOf(score) {
  // 评分=信号强度（方向性：强=红 / 弱=绿），与情绪仪表、.lab-regime 同惯例
  return score >= 80 ? 'var(--red)' : score >= 65 ? '#fb923c' : score >= 50 ? 'var(--gold)' : score >= 35 ? '#34d399' : 'var(--green)';
}
function scoreBadgeBg(score) {
  return score >= 80 ? 'rgba(255,61,106,0.18)' : score >= 65 ? 'rgba(251,146,60,0.16)'
    : score >= 50 ? 'rgba(245,158,11,0.16)' : score >= 35 ? 'rgba(52,211,153,0.16)' : 'rgba(0,233,106,0.16)';
}

function buildStockRow(item, idx) {
  const chg = item.chg || 0;
  const price = item.price || 0;
  const chgCls = chg > 0 ? 'chg-up' : chg < 0 ? 'chg-dn' : 'chg-flat';
  const priceCls = chg > 0 ? 'up' : chg < 0 ? 'dn' : '';
  const isActive = item.code === window.appState.activeCode;
  const safeName = escapeHtml(item.name || item.code);
  const safeCode = escapeHtml(item.code);
  const encodedCode = encodeURIComponent(item.code || '').replace(/'/g, '%27');
  const encodedName = encodeURIComponent(item.name || item.code || '').replace(/'/g, '%27');
  const favoriteLabel = isFavorite(item.code) ? '★' : '☆';
  const favoriteClass = isFavorite(item.code) ? ' is-favorite' : '';
  const inCompare = (window.appState.compareStocks || []).some(s => s.code === item.code);
  const isPinned = (window.appState.pinnedStocks || []).includes(item.code);

  // v8: 信号标签默认只显 1 个 + 折叠 +N（hover 行时 extra 区显示换手/量比）
  const sigs = item.signals || [];
  const activeFilter = window.appState.signalFilter;
  const tags = sigs.slice(0, 1).map(s =>
    `<div class="stag ${escapeHtml(s.type)}${activeFilter === s.type ? ' filter-active' : ''}" data-sigtype="${escapeHtml(s.type)}" title="点击筛选「${escapeHtml(s.label || s.type)}」信号" onclick="event.stopPropagation(); toggleSignalFilter('${escapeHtml(s.type)}')">${escapeHtml(s.label)}</div>`
  ).join('');
  const moreTags = sigs.length > 1
    ? `<div class="stag stag-more" data-sigtype="${escapeHtml(sigs[1].type)}" title="${escapeHtml(sigs.map(s => s.label || s.type).join('、'))}" onclick="event.stopPropagation(); toggleSignalFilter('${escapeHtml(sigs[1].type)}')">+${sigs.length - 1}</div>`
    : '';

  window.appState.prevPrices[item.code] = price;

  const score = item.score || 0;
  const scoreColor = scoreColorOf(score);
  const circumference = 2 * Math.PI * 18;
  const dashOffset = circumference * (1 - score / 100);

  // 排名奖牌（前3名特殊样式）
  const medalCls = idx === 0 ? ' medal-1' : idx === 1 ? ' medal-2' : idx === 2 ? ' medal-3' : '';

  // 涨跌幅箭头（A股惯例：红涨绿跌）
  const chgArrow = chg > 0 ? '▲' : chg < 0 ? '▼' : '—';
  const chgSign = chg > 0 ? '+' : '';

  // v7: 大涨大跌（|chg|>=5%）行级高亮；极端行情（>=9%）🔥 角标
  const isHot = Math.abs(chg) >= 5;
  const hotRowCls = isHot ? (chg > 0 ? ' hot-up' : ' hot-dn') : '';
  const hotBadgeCls = isHot ? ' chg-hot' : '';
  const isExtreme = Math.abs(chg) >= 9;
  const extremeHtml = isExtreme ? '<span class="chg-extreme" title="极端行情">🔥</span>' : '';

  // 元数据：code 始终显示；换手/量比改 hover 才显示（减少默认密度）
  const turnover = item.turnover || 0;
  const volumeRatio = item.volume_ratio || 0;
  const turnoverStr = turnover > 0 ? `换手 ${turnover.toFixed(1)}%` : '';
  const volRatioStr = volumeRatio > 0 ? `量比 ${volumeRatio.toFixed(2)}` : '';
  const extraHtml = (turnoverStr || volRatioStr)
    ? `<span class="stock-meta-extra"><span class="stock-sep">·</span><span class="stock-meta-val">${escapeHtml(turnoverStr || '')}</span>${volRatioStr ? `<span class="stock-sep">·</span><span class="stock-meta-val">${escapeHtml(volRatioStr)}</span>` : ''}</span>`
    : '';
  const metaHtml = `<span class="stock-code">${safeCode}</span>${extraHtml}`;

  // 工具提示
  const tip = `${safeName}（${safeCode}）\n现价: ${price ? price.toFixed(2) : '--'}\n涨跌: ${chgSign}${chg.toFixed(2)}%\n评分: ${score}\n双击加入对比 · 右键更多操作`;

  // v10: 排名变化箭头（对比上一轮排名；上升红/下降绿，A股语境）
  const prevRank = window.appState.prevRanking ? window.appState.prevRanking[item.code] : undefined;
  let rankDeltaHtml = '';
  if (prevRank !== undefined && prevRank !== idx) {
    rankDeltaHtml = idx < prevRank
      ? '<span class="rank-delta rank-up" title="排名上升">↑</span>'
      : '<span class="rank-delta rank-dn" title="排名下降">↓</span>';
  }

  // v10: 增加底部量能指示条
  const volBarWidth = Math.min((volumeRatio / 3) * 100, 100);
  const volBarHtml = `<div class="volume-bar-mini" style="width: ${volBarWidth}%"></div>`;

  return `
    <div class="stock-row${isActive ? ' active' : ''}${hotRowCls}"
         style="animation-delay:${Math.min(idx * 30, 400)}ms"
         onclick="selectStockWithRipple(event,decodeURIComponent('${encodedCode}'),decodeURIComponent('${encodedName}'))"
         ondblclick="event.stopPropagation(); addToCompare(decodeURIComponent('${encodedCode}'))"
         oncontextmenu="return showStockContextMenu(event,decodeURIComponent('${encodedCode}'),decodeURIComponent('${encodedName}'))"
         tabindex="0" role="option" aria-selected="${isActive}"
         data-code="${item.code}"
         data-idx="${idx}"
         title="${escapeHtml(tip)}">
      <span class="hot-band" aria-hidden="true"></span>
      <span class="row-active-arrow" aria-hidden="true"></span>
      <div class="stock-idx${medalCls}">${idx + 1}${rankDeltaHtml}</div>
      <div class="stock-info">
        <div class="stock-name">${isPinned ? '<span class="pin-mark" title="已置顶">📌</span>' : ''}<span class="stock-name-text">${safeName}</span></div>
        <div class="stock-meta">${metaHtml}${(tags || moreTags) ? `<span class="stock-sep">·</span>${tags}${moreTags}` : ''}<span class="stock-sep">·</span><span class="stock-score" style="color:${scoreColor}" title="策略评分 ${score}"><i class="score-dot-inner"></i>${score}</span></div>
      </div>
      <div class="stock-sparkline-wrap" title="近期走势">
        <canvas class="stock-sparkline" width="54" height="16" data-code="${item.code}"></canvas>
      </div>
      <div class="stock-right">
        <div class="stock-price ${priceCls}">${price ? price.toFixed(2) : '--'}</div>
        <div class="stock-chg ${chgCls}${hotBadgeCls}"><span class="chg-arrow">${chgArrow}</span>${chg ? chgSign + chg.toFixed(2) + '%' : '--'}${extremeHtml}</div>
      </div>
      <div class="row-tools">
        <button class="favorite-btn${favoriteClass}" title="${isFavorite(item.code) ? '移出自选' : '加入自选'}" aria-label="${isFavorite(item.code) ? '移出自选' : '加入自选'}" onclick="event.stopPropagation(); onFavoriteClick(this,decodeURIComponent('${encodedCode}'))">${favoriteLabel}</button>
        <button class="compare-add-btn${inCompare ? ' is-in-compare' : ''}" title="${inCompare ? '已在对比' : '加入对比 (双击行也可)'}" aria-label="加入对比" onclick="event.stopPropagation(); onCompareClick(this,decodeURIComponent('${encodedCode}'))">${inCompare ? '✓' : '+'}</button>
      </div>
      <div class="score-ring-container" title="策略评分 ${score}">
        <svg width="34" height="34" viewBox="0 0 42 42">
          <circle class="score-ring-bg" cx="21" cy="21" r="18"/>
          <circle class="score-ring-fill" cx="21" cy="21" r="18"
            stroke="${scoreColor}"
            stroke-dasharray="${circumference.toFixed(1)}"
            stroke-dashoffset="${dashOffset.toFixed(1)}"
            transform="rotate(-90 21 21)"/>
        </svg>
        <div class="score-ring-text" style="color:${scoreColor}">${score}</div>
      </div>
      ${volBarHtml}
    </div>
  `;
}

/**
 * v9: 价格数字平滑滚动（旧值 → 新值 rAF 补间）
 */
function rollPriceNumber(el, from, to, duration){
  if (el._rollRaf) cancelAnimationFrame(el._rollRaf);
  var start = performance.now();
  function frame(now){
    var t = Math.min((now - start) / duration, 1);
    var eased = 1 - Math.pow(1 - t, 3);
    var v = from + (to - from) * eased;
    el.textContent = v.toFixed(2);
    if (t < 1) el._rollRaf = requestAnimationFrame(frame);
    else { el.textContent = to.toFixed(2); el._rollRaf = null; }
  }
  el._rollRaf = requestAnimationFrame(frame);
}

function updateStockRow(row, item, idx) {
  const chg = item.chg || 0;
  const price = item.price || 0;
  const isActive = item.code === window.appState.activeCode;

  row.classList.toggle('active', isActive);
  row.setAttribute('aria-selected', isActive);

  // v7: 大涨大跌行级高亮
  row.classList.toggle('hot-up', chg >= 5);
  row.classList.toggle('hot-dn', chg <= -5);

  const idxEl = row.querySelector('.stock-idx');
  if (idxEl) idxEl.textContent = idx + 1;

  const prevPrice = window.appState.prevPrices[item.code];
  const priceEl = row.querySelector('.stock-price');
  const chgEl = row.querySelector('.stock-chg');

  window.appState.prevPrices[item.code] = price;

  const priceCls = chg > 0 ? 'up' : chg < 0 ? 'dn' : '';
  const chgCls = chg > 0 ? 'chg-up' : chg < 0 ? 'chg-dn' : 'chg-flat';

  if (priceEl) {
    priceEl.className = 'stock-price ' + priceCls;
    if (price && prevPrice && price !== prevPrice) {
      // v9: 数字平滑滚动 + 颜色脉冲
      rollPriceNumber(priceEl, prevPrice, price, 450);
      priceEl.classList.remove('price-flash-up', 'price-flash-dn');
      void priceEl.offsetWidth; // 重启动画
      priceEl.classList.add(price > prevPrice ? 'price-flash-up' : 'price-flash-dn');
      setTimeout(() => priceEl.classList.remove('price-flash-up', 'price-flash-dn'), 600);
    } else {
      priceEl.textContent = price ? price.toFixed(2) : '--';
    }
  }
  if (chgEl) {
    // v7: 保留箭头/极端角标结构（原来 textContent 会把箭头抹掉）
    const isHot = Math.abs(chg) >= 5;
    const isExtreme = Math.abs(chg) >= 9;
    chgEl.className = 'stock-chg ' + chgCls + (isHot ? ' chg-hot' : '');
    const arrow = chg > 0 ? '▲' : chg < 0 ? '▼' : '—';
    chgEl.innerHTML = `<span class="chg-arrow">${arrow}</span>${chg ? (chg > 0 ? '+' : '') + chg.toFixed(2) + '%' : '--'}${isExtreme ? '<span class="chg-extreme" title="极端行情">🔥</span>' : ''}`;
  }

  // v7: 内联评分徽章 + hover 环形评分
  const score = item.score || 0;
  const scoreColor = scoreColorOf(score);
  const scoreText = row.querySelector('.score-ring-text');
  if (scoreText) {
    scoreText.textContent = score;
    scoreText.style.color = scoreColor;
  }
  const scoreBadge = row.querySelector('.score-badge');
  if (scoreBadge) {
    scoreBadge.textContent = score;
    scoreBadge.style.color = scoreColor;
    scoreBadge.style.background = scoreBadgeBg(score);
  }

  const scoreRingFill = row.querySelector('.score-ring-fill');
  if (scoreRingFill) {
    const circumference = 2 * Math.PI * 18;
    const dashOffset = circumference * (1 - score / 100);
    scoreRingFill.setAttribute('stroke', scoreColor);
    scoreRingFill.setAttribute('stroke-dashoffset', dashOffset.toFixed(1));
  }

  const favoriteBtn = row.querySelector('.favorite-btn');
  if (favoriteBtn) {
    const favorite = isFavorite(item.code);
    favoriteBtn.textContent = favorite ? '★' : '☆';
    favoriteBtn.classList.toggle('is-favorite', favorite);
    favoriteBtn.title = favorite ? '移出自选' : '加入自选';
    favoriteBtn.setAttribute('aria-label', favorite ? '移出自选' : '加入自选');
  }

  // v7: 对比按钮状态同步
  const compareBtn = row.querySelector('.compare-add-btn');
  if (compareBtn) {
    const inCmp = (window.appState.compareStocks || []).some(s => s.code === item.code);
    compareBtn.textContent = inCmp ? '✓' : '+';
    compareBtn.classList.toggle('is-in-compare', inCmp);
    compareBtn.title = inCmp ? '已在对比' : '加入对比 (双击行也可)';
  }
}

/**
 * 绘制所有股票的迷你走势图
 */
function drawAllSparklines(items) {
  items.forEach(function(item) {
    var canvas = document.querySelector('.stock-sparkline[data-code="' + item.code + '"]');
    if (!canvas) return;

    // 从 K 线缓存获取收盘价数据
    var cacheKey = item.code + ':' + window.appState.period;
    var klineData = window.appState.klineCache[cacheKey];
    var prices = [];

    if (klineData && klineData.klines && klineData.klines.length > 0) {
      var klines = klineData.klines;
      var start = Math.max(0, klines.length - 8);
      for (var i = start; i < klines.length; i++) {
        prices.push(parseFloat(klines[i].close) || 0);
      }
    }

    if (prices.length < 2) {
      // 没有K线数据，用当前价和昨收模拟
      var price = item.price || 0;
      var yclose = item.yclose || price;
      prices = [yclose, price];
    }

    var isUp = (item.chg || 0) >= 0;
    drawSparkline(canvas, prices, isUp);
  });
}

/**
 * 在 canvas 上绘制迷你走势线 v9：渐变面积 + 末点发光 + A股红涨绿跌
 */
function drawSparkline(canvas, data, isUp) {
  if (!canvas || !data || data.length < 2) return;
  var ctx = canvas.getContext('2d');
  var w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  var min = Math.min.apply(null, data);
  var max = Math.max.apply(null, data);
  var range = max - min || 1;

  // A股惯例：涨红跌绿（与行内 chg 徽章保持一致）
  var redColor = getComputedStyle(document.documentElement).getPropertyValue('--red').trim() || '#ff3d6a';
  var greenColor = getComputedStyle(document.documentElement).getPropertyValue('--green').trim() || '#00e96a';
  var color = isUp ? redColor : greenColor;

  var points = data.map(function(v, i) {
    var x = (i / (data.length - 1)) * (w - 4) + 2;
    var y = h - ((v - min) / range) * (h - 5) - 2.5;
    return { x: x, y: y };
  });

  // 1. 渐变面积填充（颜色 → 透明）
  var grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, isUp ? 'rgba(255,61,106,0.30)' : 'rgba(0,233,106,0.30)');
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.beginPath();
  points.forEach(function(p, i) {
    if (i === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });
  ctx.lineTo(points[points.length - 1].x, h);
  ctx.lineTo(points[0].x, h);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // 2. 走势主线
  ctx.beginPath();
  points.forEach(function(p, i) {
    if (i === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.4;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.stroke();

  // 3. 末点发光圆点（当前趋势锚点）
  var last = points[points.length - 1];
  ctx.save();
  ctx.beginPath();
  ctx.arc(last.x, last.y, 3, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.shadowColor = color;
  ctx.shadowBlur = 6;
  ctx.fill();
  // 内芯亮点
  ctx.beginPath();
  ctx.arc(last.x, last.y, 1.2, 0, Math.PI * 2);
  ctx.fillStyle = '#fff';
  ctx.shadowBlur = 0;
  ctx.fill();
  ctx.restore();
}

/**
 * v9: 悬停数据卡（股票全景玻璃卡）
 * 行 hover 800ms 后浮出；移出 200ms 后隐藏；点击行立即隐藏
 */
(function initStockHoverCard(){
  var card = null, enterTimer = null, hideTimer = null, currentRow = null;

  function ensureCard(){
    if (card) return card;
    card = document.createElement('div');
    card.className = 'stock-hover-card';
    card.id = 'stock-hover-card';
    document.body.appendChild(card);
    return card;
  }

  function hideCard(){
    if (card) card.classList.remove('show');
    currentRow = null;
  }

  function showCard(row){
    var code = row.getAttribute('data-code');
    if (!code) return;
    var s = window.appState.stockMap && window.appState.stockMap[code];
    if (!s) return;
    card = ensureCard();

    var score = s.score || 0;
    var scoreColor = scoreColorOf(score);
    var chg = s.chg || 0, price = s.price || 0;
    var chgCls = chg > 0 ? 'chg-up' : chg < 0 ? 'chg-dn' : 'chg-flat';
    var chgStr = (chg > 0 ? '+' : '') + chg.toFixed(2) + '%';
    var circumference = 2 * Math.PI * 15;
    var dashOffset = circumference * (1 - score / 100);
    var sigs = (s.signals || []).slice(0, 4);
    var sigHtml = sigs.length
      ? sigs.map(function(g){ return '<span class="stag ' + escapeHtml(g.type || '') + '">' + escapeHtml(g.label || g.type || '') + '</span>'; }).join('')
      : '<span class="shc-muted">暂无策略信号</span>';
    var turnover = (s.turnover || 0) > 0 ? '换手 ' + s.turnover.toFixed(1) + '%' : '换手 --';
    var volRatio = (s.volume_ratio || 0) > 0 ? '量比 ' + s.volume_ratio.toFixed(2) : '量比 --';

    card.innerHTML =
      '<div class="shc-header">' +
        '<div class="shc-name">' + escapeHtml(s.name || code) + '</div>' +
        '<div class="shc-code">' + escapeHtml(code) + '</div>' +
      '</div>' +
      '<div class="shc-body">' +
        '<div class="shc-ring">' +
          '<svg width="40" height="40" viewBox="0 0 36 36">' +
            '<circle cx="18" cy="18" r="15" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="3"/>' +
            '<circle cx="18" cy="18" r="15" fill="none" stroke="' + scoreColor + '" stroke-width="3" stroke-linecap="round" stroke-dasharray="' + circumference.toFixed(1) + '" stroke-dashoffset="' + dashOffset.toFixed(1) + '" transform="rotate(-90 18 18)"/>' +
          '</svg>' +
          '<div class="shc-ring-num" style="color:' + scoreColor + '">' + score + '</div>' +
        '</div>' +
        '<div class="shc-metrics">' +
          '<div class="shc-price-row">' +
            '<span class="shc-price ' + (chg > 0 ? 'up' : chg < 0 ? 'dn' : '') + '">' + (price ? price.toFixed(2) : '--') + '</span>' +
            '<span class="stock-chg ' + chgCls + '" style="font-size:10px;padding:1px 6px">' + chgStr + '</span>' +
          '</div>' +
          '<div class="shc-meta-row"><span>' + turnover + '</span><span>·</span><span>' + volRatio + '</span></div>' +
        '</div>' +
      '</div>' +
      '<div class="shc-signals">' + sigHtml + '</div>';

    // 定位：行右侧，出界翻转到左侧；垂直居中于行并夹在视口内
    card.style.visibility = 'hidden';
    card.classList.add('show');
    var rect = row.getBoundingClientRect();
    var cw = card.offsetWidth, chh = card.offsetHeight;
    var x = rect.right + 8;
    if (x + cw > window.innerWidth - 10) x = Math.max(10, rect.left - cw - 8);
    var y = rect.top + rect.height / 2 - chh / 2;
    y = Math.max(8, Math.min(y, window.innerHeight - chh - 8));
    card.style.left = x + 'px';
    card.style.top = y + 'px';
    card.style.visibility = '';
  }

  // 事件委托：#stock-list 内行切换
  document.addEventListener('mouseover', function(e){
    if (!e.target || !e.target.closest) return;
    var row = e.target.closest('#stock-list .stock-row');
    if (row && row !== currentRow){
      clearTimeout(enterTimer);
      clearTimeout(hideTimer);
      currentRow = row;
      enterTimer = setTimeout(function(){ showCard(row); }, 800);
    } else if (!row && card && card.classList.contains('show')){
      // 鼠标移到卡片或行以外 → 延迟隐藏
      if (!e.target.closest('#stock-hover-card')){
        clearTimeout(hideTimer);
        hideTimer = setTimeout(hideCard, 200);
      }
    }
  });

  // 点击任何行 → 立即收起（避免挡住 K 线区）
  document.addEventListener('click', function(e){
    if (e.target && e.target.closest && e.target.closest('#stock-list .stock-row')) hideCard();
  }, true);

  // 滚动 / 窗口失焦 → 收起
  window.addEventListener('scroll', hideCard, true);
  window.addEventListener('blur', hideCard);
})();
function switchListTab(el, filter) {
  document.querySelectorAll('#left-panel .tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  window.appState.listFilter = filter;
  renderStockList();
}

// ================================================================
//  v7: 列表排序下拉
// ================================================================
const LIST_SORT_LABELS = { score: '评分', chg: '涨跌幅', turnover: '换手率', volume_ratio: '量比', code: '代码' };

function toggleSortMenu(e) {
  if (e) e.stopPropagation();
  const menu = document.getElementById('sort-menu');
  const trigger = document.getElementById('sort-trigger');
  if (!menu) return;
  const willOpen = !menu.classList.contains('show');
  closeCtxMenu();
  menu.classList.toggle('show', willOpen);
  if (trigger) trigger.classList.toggle('open', willOpen);
}

function closeSortMenu() {
  const menu = document.getElementById('sort-menu');
  const trigger = document.getElementById('sort-trigger');
  if (menu) menu.classList.remove('show');
  if (trigger) trigger.classList.remove('open');
}

function setListSort(key) {
  if (window.appState.sortKey === key) {
    window.appState.sortDir = window.appState.sortDir === 'desc' ? 'asc' : 'desc';
  } else {
    window.appState.sortKey = key;
    window.appState.sortDir = key === 'code' ? 'asc' : 'desc';
  }
  document.querySelectorAll('#sort-menu .sort-menu-item').forEach(el =>
    el.classList.toggle('active', el.getAttribute('data-key') === window.appState.sortKey));
  const labelEl = document.getElementById('sort-label');
  if (labelEl) labelEl.textContent = LIST_SORT_LABELS[window.appState.sortKey] || '评分';
  const dirEl = document.getElementById('sort-dir');
  if (dirEl) dirEl.textContent = window.appState.sortDir === 'desc' ? '↓' : '↑';
  closeSortMenu();
  renderStockList();
}

// ================================================================
//  v7: 信号标签点击筛选
// ================================================================
function toggleSignalFilter(type) {
  if (!type) return;
  window.appState.signalFilter = (window.appState.signalFilter === type) ? '' : type;
  renderStockList();
  // 增量渲染路径下手动同步标签激活态
  document.querySelectorAll('#stock-list .stag').forEach(el => {
    const t = el.getAttribute('data-sigtype');
    el.classList.toggle('filter-active', !!t && t === window.appState.signalFilter);
  });
  showToast('信号筛选', window.appState.signalFilter
    ? `只显示「${type}」信号股票，再次点击标签取消`
    : '已取消信号筛选');
}

// ================================================================
//  v7: 行内小 toast（★ / + 操作反馈）
// ================================================================
function showRowToast(row, text) {
  if (!row) return;
  row.querySelectorAll('.row-toast').forEach(el => el.remove());
  const t = document.createElement('div');
  t.className = 'row-toast';
  t.textContent = text;
  row.appendChild(t);
  setTimeout(() => t.remove(), 900);
}

function onFavoriteClick(el, code) {
  toggleFavorite(code);
  const row = document.querySelector('.stock-row[data-code="' + code + '"]');
  if (row) showRowToast(row, isFavorite(code) ? '已加入自选 ★' : '已移出自选 ☆');
}

function onCompareClick(el, code) {
  addToCompare(code);
  const row = document.querySelector('.stock-row[data-code="' + code + '"]');
  if (row) showRowToast(row, '已加入对比 +');
}

// ================================================================
//  v7: 股票行右键菜单
// ================================================================
let _ctxMenuEl = null;

function closeCtxMenu() {
  if (_ctxMenuEl) {
    _ctxMenuEl.remove();
    _ctxMenuEl = null;
  }
}

function showStockContextMenu(event, code, name) {
  if (event) { event.preventDefault(); event.stopPropagation(); }
  closeCtxMenu();
  closeSortMenu();
  if (!code) return false;

  const stock = window.appState.stockMap[code] || {};
  const fav = isFavorite(code);
  const inCmp = (window.appState.compareStocks || []).some(s => s.code === code);
  const pinned = (window.appState.pinnedStocks || []).includes(code);

  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.innerHTML = `
    <div class="ctx-menu-title"><span>${escapeHtml(name || stock.name || code)}</span><span class="ctx-code">${escapeHtml(code)}</span></div>
    <div class="ctx-menu-item" data-act="detail"><span class="cmi-ico">📊</span>查看详情</div>
    <div class="ctx-menu-item${fav ? ' is-on' : ''}" data-act="fav"><span class="cmi-ico">⭐</span>${fav ? '移出自选' : '加入自选'}<span class="cmi-state">${fav ? '已收藏' : ''}</span></div>
    <div class="ctx-menu-item${inCmp ? ' is-on' : ''}" data-act="cmp"><span class="cmi-ico">➕</span>加入对比<span class="cmi-state">${inCmp ? '已加入' : ''}</span></div>
    <div class="ctx-menu-item" data-act="copy"><span class="cmi-ico">📋</span>复制代码</div>
    <div class="ctx-menu-item${pinned ? ' is-on' : ''}" data-act="pin"><span class="cmi-ico">📌</span>${pinned ? '取消置顶' : '置顶该股'}</div>
    <div class="ctx-menu-sep"></div>
    <div class="ctx-menu-item" data-act="close"><span class="cmi-ico">✕</span>关闭菜单</div>
  `;
  document.body.appendChild(menu);

  // 定位（防溢出屏幕）
  const mw = 280;
  const mh = menu.offsetHeight || 260;
  const x = Math.max(8, Math.min(event.clientX, window.innerWidth - mw - 8));
  const y = Math.max(8, Math.min(event.clientY, window.innerHeight - mh - 8));
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';

  menu.addEventListener('click', function(ev) {
    const itemEl = ev.target.closest('.ctx-menu-item');
    if (!itemEl) return;
    const act = itemEl.getAttribute('data-act');
    closeCtxMenu();
    if (act === 'detail') {
      selectStock(code, name || stock.name || code);
    } else if (act === 'fav') {
      toggleFavorite(code);
    } else if (act === 'cmp') {
      addToCompare(code);
      showToast('股票对比', '已加入对比');
    } else if (act === 'copy') {
      copyStockCode(code);
    } else if (act === 'pin') {
      togglePinStock(code);
    }
  });
  menu.addEventListener('contextmenu', e => e.preventDefault());

  _ctxMenuEl = menu;
  return false;
}

function copyStockCode(code) {
  const done = () => showToast('复制成功', code);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(code).then(done).catch(() => fallbackCopyText(code, done));
  } else {
    fallbackCopyText(code, done);
  }
}

function fallbackCopyText(text, done) {
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    done();
  } catch (e) {
    showToast('复制失败', '请手动复制 ' + text);
  }
}

// ================================================================
//  v7: 置顶该股
// ================================================================
function togglePinStock(code) {
  if (!code) return;
  const list = window.appState.pinnedStocks || [];
  const willPin = !list.includes(code);
  window.appState.pinnedStocks = willPin ? [code].concat(list) : list.filter(c => c !== code);
  try {
    localStorage.setItem('dapeng_pinned', JSON.stringify(window.appState.pinnedStocks));
  } catch (e) { /* localStorage 不可用时忽略 */ }
  showToast('置顶', willPin ? `已置顶 ${code}` : `已取消置顶 ${code}`);
  renderStockList();
}

// ================================================================
//  v7: 窄面板自适应（#left-panel < 260px 时隐藏 sparkline / 环形评分）
// ================================================================
function initLeftPanelNarrow() {
  const panel = document.getElementById('left-panel');
  if (!panel) return;
  const apply = () => panel.classList.toggle('narrow', panel.offsetWidth < 260);
  apply();
  if (typeof ResizeObserver !== 'undefined') {
    try { new ResizeObserver(apply).observe(panel); } catch (e) { window.addEventListener('resize', apply); }
  } else {
    window.addEventListener('resize', apply);
  }
}

// v7: 点击外部关闭排序菜单 / 右键菜单
document.addEventListener('click', function(ev) {
  if (!ev.target.closest('.sort-wrap')) closeSortMenu();
  if (!ev.target.closest('.ctx-menu')) closeCtxMenu();
});
document.addEventListener('scroll', function() { closeCtxMenu(); closeSortMenu(); }, true);

// ================================================================
//  选股操作
// ================================================================
function selectStockWithRipple(event, code, name) {
  const row = event.currentTarget;
  addRipple(event, row);
  selectStock(code, name);
}

async function selectStock(code, name) {
  window.appState._klineVersion++;
  const currentVersion = window.appState._klineVersion;

  window.appState.activeCode = code;
  window.appState.activeName = name || window.appState.stockMap[code]?.name || code;

  // v6.1: 立即显示加载状态（不等K线），确保点击反馈即时
  showAnalysisLoading(name);

  document.querySelectorAll('.stock-row').forEach(el => {
    el.classList.toggle('active', el.getAttribute('data-code') === code);
  });

  const nameEl = document.getElementById('k-name');
  if (nameEl) {
    nameEl.style.opacity = '0';
    setTimeout(() => {
      if (window.appState._klineVersion === currentVersion) {
        nameEl.textContent = window.appState.activeName;
        nameEl.style.opacity = '1';
      }
    }, 150);
  }

  // K 线加载（并行，不阻塞 AI 分析）
  cancelPendingKline();
  const klinePromise = loadAndRenderKline(code, currentVersion);

  let stockData = window.appState.stockMap[code];
  if (!stockData) {
    stockData = { code, name, chg: 0, price: 0, high: 0, low: 0, yclose: 0, volume: 0, signals: [] };
  }

  // v6.1: AI 分析与 K 线并行发起，不互相阻塞
  // 用 Promise 单独处理，即使 K 线链抛出异常 AI 分析也能正常完成
  fetchAnalyze(code, currentVersion).then(analysisData => {
    if (currentVersion !== window.appState._klineVersion) return;
    try {
      if (analysisData) {
        updateAnalysisPanel(analysisData);
      } else {
        showAnalysisError('分析请求失败，请稍后重试');
      }
    } catch (e) {
      console.warn('updateAnalysisPanel error:', e);
      showAnalysisError('AI 分析渲染异常');
    }
  }).catch(e => {
    console.warn('fetchAnalyze error:', e);
    if (currentVersion === window.appState._klineVersion) {
      showAnalysisError('AI分析服务异常');
    }
  });

  // 等待 K 线完成（用于策略面板等后续更新）
  try {
    await klinePromise;
  } catch (e) {
    console.warn('K-line load failed for', code, e);
  }

  if (currentVersion !== window.appState._klineVersion) return;

  const klineResp = window.appState.klineCache[code + ':' + window.appState.period];
  try {
    updateStrategyPanel(stockData, klineResp);
    updateTechIndicators(klineResp);
    updateLaws(stockData, klineResp);
  } catch (e) {
    console.warn('Strategy panel update error:', e);
  }

  // 更新雷达图
  try {
    const signals = stockData.signals || [];
    const mScore = (signals.find(s => s.type === 'momentum') || {}).score || 50;
    const bScore = (signals.find(s => s.type === 'breakout') || {}).score || 50;
    const vScore = (signals.find(s => s.type === 'volume_divergence') || {}).score || 50;
    const strategy = klineResp && klineResp.strategy;
    const tScore = (strategy && strategy.trend_score) || 50;
    const rScore = (strategy && strategy.risk_score) || 50;
    updateRadarChart(mScore, bScore, vScore, tScore, rScore);
  } catch (e) {
    console.warn('Radar chart update error:', e);
  }

  // v5.1: 切换股票时收起浮层，并将Tab内容移回原位
  // v37（T09）: 基本面浮层例外——保持打开并按新标的刷新（下方通用懒加载分支负责拉数），
  //            用户在看 6 卡时连续切股不会被打断；其余 Tab 维持原有「切股即收起」行为。
  if (window.appState.activeOverlayTab && window.appState.activeOverlayTab !== 'fundamental') {
    restoreTabContent(window.appState.activeOverlayTab);
    hideKlineOverlay();
    window.appState.activeTab = 'strategy';
    updateKlineTabButtons('strategy');
  }

  // v5.0: 如果当前Tab不是strategy，触发对应Tab的数据懒加载
  if (window.appState.activeTab && window.appState.activeTab !== 'strategy') {
    var _tabConfig = KLINE_TABS[window.appState.activeTab];
    if (_tabConfig && _tabConfig.loadFn) {
      _tabConfig.loadFn(code);
    }
  }

  // 选中标的变化 → 同步跨页入口（情绪页聚焦板块、回测页带当前标的）
  _syncContextLinks();
}

// ================================================================
//  K 线加载与极速渲染
// ================================================================
/**
 * v5.5: K 线空态提示
 *
 * 背景：上游取不到 K 线时，chart.js 走的是 `klineChart.clear()` —— 用户看到的
 * 是一块**纯空白面板**，无法区分「还在加载」「休市」「这个标的没有分钟线」。
 * 港股通标的切到 60/30/15/5 分钟时必然触发（腾讯不供港股分钟线，东财备用源
 * 被限流时就真的没有），必须把后端给的 reason 显示出来。
 *
 * 覆盖层挂在 #kline-chart 内部绝对定位，对 KLineChart Pro 与 ECharts 两套
 * 渲染引擎同时成立，不需要改 chart.js 的内部分支。
 */
function _showKlineEmptyState(data) {
  var host = document.getElementById('kline-chart');
  if (!host) return;
  var old = host.querySelector('.kline-empty-state');
  if (old) old.remove();

  var reason = (data && data.reason) || '';
  var isHk = /^hk/i.test((data && data.code) || window.appState.activeCode || '');
  var title = isHk ? '港股通标的暂无该周期 K 线' : '该周期暂无 K 线数据';
  var hint = isHk
    ? '港股通标的的分钟级 K 线依赖备用数据源，可切换到「日K」查看完整历史。'
    : '可稍后重试，或切换到其他周期。';

  var box = document.createElement('div');
  box.className = 'kline-empty-state';
  box.innerHTML = '<div class="kes-icon">📉</div>'
    + '<div class="kes-title">' + title + '</div>'
    + '<div class="kes-msg">' + (reason || '数据源当前不可用') + '</div>'
    + '<div class="kes-hint">' + hint + '</div>';
  host.appendChild(box);
}

/** 清除 K 线空态（有数据时调用） */
function _clearKlineEmptyState() {
  var host = document.getElementById('kline-chart');
  if (!host) return;
  var old = host.querySelector('.kline-empty-state');
  if (old) old.remove();
}

async function loadAndRenderKline(code, version) {
  const period = window.appState.period || 'day';
  const cacheKey = code + ':' + period;

  // v36: 分时（min）走 /api/timeshare，不进 K 线缓存、不调用 /api/kline?period=min
  if (period === 'min') {
    if (typeof window.loadAndRenderTimeshare === 'function') {
      await window.loadAndRenderTimeshare(code, {});
    }
    if (version !== undefined && version !== window.appState._klineVersion) return;
    updateKlineHeader();
    if (typeof updateIndicatorButtonsUI === 'function') updateIndicatorButtonsUI();
    return;
  }

  try {
    const data = await fetchKline(code, period);

    if (version !== undefined && version !== window.appState._klineVersion) return;

    if (typeof klineChart !== 'undefined' && klineChart && !klineChart.isDisposed()) {
      klineChart.hideLoading();
    }

    if (!data) {
      if (version !== undefined && version !== window.appState._klineVersion) return;
      renderKline(null);
      _showKlineEmptyState({ code: code, reason: 'K线接口未返回数据（网络或数据源异常）' });
      return;
    }

    window.appState.klineCache[cacheKey] = data;
    renderKline(data);
    // v5.5：后端在无数据时会带 reason，据此渲染空态覆盖层而不是留白
    if (!data.klines || !data.klines.length) {
      _showKlineEmptyState(data);
    } else {
      _clearKlineEmptyState();
    }
    updateKlineHeader();
    
    // 同步更新策略面板
    const stockData = window.appState.stockMap[code] || {};
    if (typeof updateStrategyPanel === 'function') {
      updateStrategyPanel(stockData, data);
    }
    if (typeof updateIndicatorButtonsUI === 'function') {
      updateIndicatorButtonsUI();
    }
  } catch (e) {
    console.error('loadAndRenderKline error:', e);
    if (typeof klineChart !== 'undefined' && klineChart && !klineChart.isDisposed()) {
      klineChart.hideLoading();
    }
    _showKlineEmptyState({ code: code, reason: 'K线请求失败：' + (e && e.message ? e.message : '未知错误') });
  }
}

/**
 * 显示K线加载状态（非破坏性）
 */
function showKlineLoading(name) {
  if (typeof klineChart !== 'undefined' && klineChart && !klineChart.isDisposed()) {
    klineChart.showLoading({
      text: (name || 'K线') + ' 行情加载中...',
      color: '#3b82f6',
      textColor: '#8b9ec2',
      maskColor: 'rgba(6,12,28,0.4)',
      zlevel: 10
    });
    return;
  }
  const chartEl = document.getElementById('kline-chart');
  if (!chartEl || chartEl.children.length > 0) return;
  const safeName = name ? String(name).replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'})[c]) : '';
  chartEl.innerHTML = `
    <div class="kline-placeholder">
      <div class="kline-placeholder-icon">
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M3 3v18h18"/>
          <path d="M7 14l3-3 4 4 5-5"/>
          <circle cx="7" cy="14" r="1.5" fill="currentColor"/>
          <circle cx="10" cy="11" r="1.5" fill="currentColor"/>
          <circle cx="14" cy="15" r="1.5" fill="currentColor"/>
          <circle cx="19" cy="10" r="1.5" fill="currentColor"/>
        </svg>
      </div>
      <div class="kline-placeholder-text">${safeName} K线加载中...</div>
      <div class="kline-placeholder-hint">正在获取毫秒级行情数据</div>
    </div>`;
}

/**
 * 更新 K 线头部信息
 */
/* 量比 = 当日成交量 / 前4日均量
   与后端 server/strategies.py:249 的定义保持一致，避免前后端口径打架。
   数据取自日K缓存；取不到时返回 null（显示 "--" 并给出 title），不编造数值。 */
function _calcVolRatio(code) {
  try {
    var cache = window.appState.klineCache || {};
    var entry = cache[code + ':day'];
    if (!entry) {
      var keys = Object.keys(cache);
      for (var i = 0; i < keys.length; i++) {
        if (keys[i].indexOf(code + ':') === 0) { entry = cache[keys[i]]; break; }
      }
    }
    // /api/kline 的 data 是对象：{code,name,period,klines:[{d,o,c,h,l,v}],...}
    var kl = Array.isArray(entry) ? entry
      : (entry && Array.isArray(entry.klines)) ? entry.klines
        : (entry && Array.isArray(entry.bars)) ? entry.bars
          : null;
    if (!kl || kl.length < 5) return null;
    var vols = [];
    for (var j = 0; j < kl.length; j++) {
      var k = kl[j] || {};
      var v = parseFloat(k.v != null ? k.v : (k.volume != null ? k.volume : k.vol));
      if (isFinite(v) && v > 0) vols.push(v);
    }
    if (vols.length < 5) return null;
    var last = vols[vols.length - 1];
    var avg = (vols[vols.length - 2] + vols[vols.length - 3] + vols[vols.length - 4] + vols[vols.length - 5]) / 4;
    if (!avg) return null;
    return last / avg;
  } catch (e) {
    return null;
  }
}

function updateKlineHeader() {
  const code = window.appState.activeCode;
  if (!code) return;

  // 标的名：原实现只在 selectStock() 内赋值，首屏（含 ?code= 深链）恒为 "--"
  const nameEl = document.getElementById('k-name');
  if (nameEl) {
    const nm = window.appState.activeName
      || (window.appState.stockMap[code] || {}).name
      || code;
    if (nameEl.textContent !== nm) nameEl.textContent = nm;
  }

  const stockData = window.appState.stockMap[code];
  if (!stockData) return;

  const price = stockData.price || 0;
  const chg = stockData.chg || 0;

  if (price > 0) {
    const priceEl = document.getElementById('k-price');
    const chgEl = document.getElementById('k-chg');

    if (priceEl) {
      const oldPrice = priceEl.textContent;
      const newPrice = price.toFixed(2);
      priceEl.textContent = newPrice;
      priceEl.className = 'kline-price ' + (chg > 0 ? 'up' : chg < 0 ? 'dn' : '');

      if (oldPrice !== '--' && oldPrice !== newPrice) {
        const flashCls = parseFloat(newPrice) > parseFloat(oldPrice) ? 'price-flash-up' : 'price-flash-dn';
        priceEl.classList.add(flashCls);
        setTimeout(() => priceEl.classList.remove(flashCls), 500);
      }
    }
    if (chgEl) {
      chgEl.textContent = (chg > 0 ? '+' : '') + chg.toFixed(2) + '%';
      chgEl.className = 'kline-chg-badge ' + (chg > 0 ? 'chg-up' : chg < 0 ? 'chg-dn' : 'chg-flat');
    }

    const highEl = document.getElementById('k-high');
    const lowEl = document.getElementById('k-low');
    const ycEl = document.getElementById('k-yc');
    const ampEl = document.getElementById('k-amp');

    if (highEl) highEl.textContent = stockData.high ? stockData.high.toFixed(2) : '--';
    if (lowEl) lowEl.textContent = stockData.low ? stockData.low.toFixed(2) : '--';
    if (ycEl) ycEl.textContent = stockData.yclose ? stockData.yclose.toFixed(2) : '--';
    if (ampEl && stockData.high && stockData.low && stockData.yclose && stockData.yclose > 0) {
      const amp = ((stockData.high - stockData.low) / stockData.yclose * 100).toFixed(2);
      ampEl.textContent = amp + '%';
    }
  }

  // 成交量 / 换手 / 量比：这三个字段此前没有任何 JS 赋值，DOM 恒为 "--"。
  // 放在 price>0 判断之外，保证行情缺失时仍能显示可得的部分。
  const volEl = document.getElementById('k-vol');
  const toEl = document.getElementById('k-turnover');
  const vrEl = document.getElementById('k-vol-ratio');

  if (volEl) {
    const v = Number(stockData.volume); // 腾讯字段单位：手
    volEl.textContent = (!isFinite(v) || v <= 0)
      ? '--'
      : (v >= 10000 ? (v / 10000).toFixed(2) + '万手' : Math.round(v) + '手');
  }
  if (toEl) {
    const t = Number(stockData.turnover);
    toEl.textContent = (!isFinite(t) || t <= 0) ? '--' : t.toFixed(2) + '%';
  }
  if (vrEl) {
    const vr = _calcVolRatio(code);
    vrEl.textContent = (vr == null) ? '--' : vr.toFixed(2);
    vrEl.title = (vr == null) ? '量比需要日K数据，当前不可用' : '当日成交量 / 前4日均量';
  }
}

/**
 * 设置 K 线周期 — v6.5 极速无缝切换与缓存即时呈现
 */
function setPeriod(el, period) {
  document.querySelectorAll('[data-kline-period]').forEach(function(button) {
    var active = button === el;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });

  window.appState.period = period;
  window.appState._klineVersion++;
  const currentVersion = window.appState._klineVersion;
  const activeCode = window.appState.activeCode;
  const cacheKey = activeCode + ':' + period;

  // 1. 如果有缓存，立即以零延迟渲染
  const cached = window.appState.klineCache[cacheKey] || (typeof _getKlineCache === 'function' ? _getKlineCache(cacheKey) : null);
  if (cached && cached.klines && cached.klines.length > 0) {
    renderKline(cached);
    updateKlineHeader();
    if (typeof updateIndicatorButtonsUI === 'function') updateIndicatorButtonsUI();
  } else {
    // 2. 无缓存时触发轻量非破坏性 loading
    showKlineLoading(window.appState.activeName);
  }

  cancelPendingKline();
  loadAndRenderKline(activeCode, currentVersion);
}

// ================================================================
//  指数栏更新
// ================================================================
function updateIndicesBar(data) {
  if (!data || !data.indices) return;

  function setIdx(pElId, cElId, item) {
    if (!item) return;
    const pEl = document.getElementById(pElId);
    const cEl = document.getElementById(cElId);
    if (!pEl || !cEl) return;

    const oldPrice = pEl.textContent;
    const newPrice = item.price ? item.price.toFixed(2) : '--';
    pEl.textContent = newPrice;

    if (oldPrice !== '--' && oldPrice !== newPrice) {
      const chg = item.chg || 0;
      const flashCls = chg > 0 ? 'price-flash-up' : chg < 0 ? 'price-flash-dn' : '';
      if (flashCls) {
        pEl.classList.add(flashCls);
        setTimeout(() => pEl.classList.remove(flashCls), 500);
      }
    }

    const chg = item.chg || 0;
    cEl.textContent = (chg > 0 ? '+' : '') + chg.toFixed(2) + '%';
    cEl.className = 'idx-chg ' + (chg > 0 ? 'up' : chg < 0 ? 'dn' : 'flat');
    pEl.className = 'idx-price ' + (chg > 0 ? 'up' : chg < 0 ? 'dn' : 'flat');
  }

  setIdx('sh-p', 'sh-c', data.indices.sh);
  setIdx('cy-p', 'cy-c', data.indices.cyb);
  setIdx('sc-p', 'sc-c', data.indices.sz);
}

// ================================================================
//  自选股管理
// ================================================================
function isFavorite(code) {
  return (window.appState.favoriteStocks || []).includes(code);
}

function toggleFavorite(code) {
  if (!code) return;
  if (!Array.isArray(window.appState.favoriteStocks)) window.appState.favoriteStocks = [];
  if (isFavorite(code)) {
    window.appState.favoriteStocks = window.appState.favoriteStocks.filter(c => c !== code);
    showToast('自选股', '已移出自选');
  } else {
    window.appState.favoriteStocks.push(code);
    showToast('自选股', '已加入自选');
  }
  try { localStorage.setItem('dapeng_favorites', JSON.stringify(window.appState.favoriteStocks)); } catch (e) {}
  renderStockList();
}

function addFavorite(code) { if (!isFavorite(code)) toggleFavorite(code); }
function removeFavorite(code) { if (isFavorite(code)) toggleFavorite(code); }
window.toggleFavorite = toggleFavorite;

// ================================================================
//  搜索功能
// ================================================================
let searchDebounce = null;

function handleSearch(event) {
  if (event.key !== 'Enter') return;
  const input = event.target;
  const keyword = input.value.trim();
  if (!keyword) return;

  const localMatch = window.appState.stocks.find(s =>
    s.name.includes(keyword) || s.code.includes(keyword)
  );

  if (localMatch) {
    selectStock(localMatch.code, localMatch.name);
    return;
  }

  doCrossSectorSearch(keyword);
}

async function doCrossSectorSearch(keyword) {
  setStatus('loading');
  showStockListSkeleton();
  const data = await fetchSearch(keyword);
  if (!data || !data.results || data.results.length === 0) {
    showToast('搜索', `未找到"${keyword}"相关股票`);
    setStatus('ok');
    return;
  }

  if (data.results.length === 1) {
    const r = data.results[0];
    if (r.sector) {
      window.appState.activeSector = r.sector;
      renderSectorBar();
    }
    selectStock(r.code, r.name);
    showToast('搜索', `已定位到 ${escapeHtml(r.name)} (${r.code})`);
  } else {
    window.appState.stocks = data.results.map(r => ({
      code: r.code,
      name: r.name,
      price: 0,
      chg: 0,
      score: 50,
      signals: [],
    }));
    window.appState.stockMap = {};
    for (const s of window.appState.stocks) {
      window.appState.stockMap[s.code] = s;
    }
    renderStockList();

    const titleEl = document.getElementById('sector-title-name');
    if (titleEl) titleEl.textContent = `全市场搜索: ${keyword}`;
    showToast('搜索', `找到 ${data.results.length} 只匹配标的`);

    // 异步拉取搜索股票的实时行情
    var codes = data.results.slice(0, 30).map(r => r.code);
    fetch('/api/quotes/multi?codes=' + encodeURIComponent(codes.join(','))).then(r => r.json()).then(res => {
      if (res && res.data && Array.isArray(res.data)) {
        var qMap = {};
        res.data.forEach(q => { qMap[q.code] = q; });
        window.appState.stocks.forEach(s => {
          var q = qMap[s.code];
          if (q) {
            s.price = q.price || s.price;
            s.chg = q.chg || s.chg;
            s.amount = q.amount || 0;
            s.turnover = q.turnover || 0;
          }
        });
        renderStockList();
      }
    }).catch(function(){});
  }
  setStatus('ok');
}

// ================================================================
//  v5.4: 条件选股 — 5 条件 + 独立结果面板
// ================================================================
async function doScreenStocks() {
  // 收集 5 个条件
  var minScore = parseInt(document.getElementById('screen-score')?.value) || 70;
  var minChg = parseFloat(document.getElementById('screen-chg')?.value);
  var minVol = parseFloat(document.getElementById('screen-vol')?.value);
  var minTurnover = parseFloat(document.getElementById('screen-turnover')?.value);
  var maxPe = parseFloat(document.getElementById('screen-pe')?.value);

  // 至少输入一个条件（评分除外）
  if (isNaN(minChg) && isNaN(minVol) && isNaN(minTurnover) && isNaN(maxPe)) {
    showToast('选股', '请至少输入一个筛选条件（涨幅/量比/换手率/PE）');
    return;
  }

  var filters = {
    minScore: minScore,
    minChg: isNaN(minChg) ? null : minChg,
    minVol: isNaN(minVol) ? null : minVol,
    minTurnover: isNaN(minTurnover) ? null : minTurnover,
    maxPe: isNaN(maxPe) ? null : maxPe,
    limit: 30
  };

  setStatus('loading');

  // 打开结果面板并显示骨架屏
  var panel = document.getElementById('screen-result-panel');
  if (panel) panel.style.display = 'flex';
  var tbody = document.getElementById('screen-result-tbody');
  if (tbody) _renderTableSkeleton(tbody, 8);

  var data = await fetchScreen(filters);
  if (!data || !data.stocks || data.stocks.length === 0) {
    if (tbody) _renderTableEmpty(tbody, '未找到符合条件的股票');
    showToast('选股', '未找到符合条件的股票');
    setStatus('ok');
    return;
  }

  // 更新结果面板标题
  var titleEl = document.getElementById('screen-result-title');
  if (titleEl) titleEl.textContent = '选股结果 (' + (data.total || data.stocks.length) + '只)';

  // 渲染结果表格
  var html = '';
  data.stocks.forEach(function (s, i) {
    var code = s.code || '';
    var name = s.name || '--';
    var score = s.score || s.total_score || 0;
    var chg = parseFloat(s.chg || 0);
    var price = parseFloat(s.price || s.last_close || 0);
    var turnover = parseFloat(s.turnover || 0);
    var pe = s.pe || null;
    var chgCls = chg >= 0 ? 'col-chg-up' : 'col-chg-dn';
    var chgText = (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%';

    html += '<tr onclick="selectStock(\'' + escapeHtml(code) + '\', \'' +
      escapeHtml(name).replace(/'/g, "\\'") + '\'); closeScreenResultPanel();">' +
      '<td class="col-rank">' + (i + 1) + '</td>' +
      '<td class="col-code">' + escapeHtml(code) + '</td>' +
      '<td class="col-name">' + escapeHtml(name) + '</td>' +
      '<td class="col-num">' + score.toFixed(0) + '</td>' +
      '<td class="col-num ' + chgCls + '">' + chgText + '</td>' +
      '<td class="col-num">' + (price > 0 ? price.toFixed(2) : '--') + '</td>' +
      '<td class="col-num">' + (turnover > 0 ? turnover.toFixed(2) + '%' : '--') + '</td>' +
      '<td class="col-num">' + (pe != null ? pe.toFixed(1) : '--') + '</td>' +
      '</tr>';
  });
  if (tbody) tbody.innerHTML = html;

  showToast('选股', '找到 ' + (data.total || data.stocks.length) + ' 只符合条件的股票');
  setStatus('ok');
}

/**
 * 关闭选股结果面板
 */
function closeScreenResultPanel() {
  var panel = document.getElementById('screen-result-panel');
  if (panel) panel.style.display = 'none';
}

function toggleScreenPanel() {
  const panel = document.getElementById('screen-panel');
  if (!panel) return;
  const visible = panel.style.display !== 'none';
  panel.style.display = visible ? 'none' : 'block';
}

/**
 * 清除选股筛选，恢复当前板块股池
 * 重置 screenMode，重新加载行情，恢复板块标题
 */
function clearScreenFilter() {
  window.appState.screenMode = false;
  if (typeof loadQuotes === 'function') {
    loadQuotes(false);
  } else if (typeof fetchQuotes === 'function') {
    fetchQuotes(window.appState.activeSector).then(function (data) {
      if (data) {
        window.appState.stocks = data.stocks || [];
        window.appState.stocks.forEach(function (s) { s.sector = window.appState.activeSector; });
        window.appState.stockMap = {};
        window.appState.stocks.forEach(function (s) { window.appState.stockMap[s.code] = s; });
        // 恢复板块股池属于刷新：保留用户选中，仅在其消失时回退并补一次重绘
        _preserveActiveStock(window.appState.stocks);
        renderStockList();
        _syncChartAfterRefresh();
        var titleEl = document.getElementById('sector-title-name');
        var sectorCfg = SECTORS.find(function (s) { return s.key === window.appState.activeSector; });
        if (titleEl && sectorCfg) titleEl.textContent = sectorCfg.name;
      }
    });
  }
  showToast('选股', '已恢复当前板块股池');
}

// ================================================================
//  全量刷新
// ================================================================
async function refreshAll() {
  setStatus('loading');
  try {
    window.appState._klineVersion++;
    const currentVersion = window.appState._klineVersion;

    // 快速刷新行情（不等K线策略评分）
    const [quotesResult, globalResult] = await Promise.all([
      loadQuotes(true),   // fast=1 先快速刷新行情
      fetchGlobal(),
    ]);

    if (globalResult) {
      updateIndicesBar(globalResult);
      updateGlobalPanel(globalResult);
    }

    // 刷新后先做选中守卫：若 activeCode 已不在新列表中则回退到第一只，
    // 确保下面重绘的标的 = activeCode，不出现「标的分裂」。
    _preserveActiveStock(window.appState.stocks);

    delete window.appState.klineCache[window.appState.activeCode + ':' + window.appState.period];
    await loadAndRenderKline(window.appState.activeCode, currentVersion);

    if (currentVersion !== window.appState._klineVersion) {
      setStatus('ok');
      return;
    }

    const stockData = window.appState.stockMap[window.appState.activeCode] || {};
    const klineResp = window.appState.klineCache[window.appState.activeCode + ':' + window.appState.period];
    updateStrategyPanel(stockData, klineResp);
    updateTechIndicators(klineResp);
    updateLaws(stockData, klineResp);

    setStatus('ok');
  } catch (e) {
    console.error('refresh error', e);
    setStatus('error');
  }
}

// ================================================================
//  智能定时刷新（节能与前后台自适应调度）
// ================================================================
function startSmartRefresh() {
  if (window.appState.refreshTimer) {
    clearInterval(window.appState.refreshTimer);
  }

  const interval = getSmartRefreshInterval();
  window.appState.refreshTimer = setInterval(() => {
    // 后台休眠时跳过慢速轮询，节省带宽与 CPU
    if (document.hidden) return;

    fetchQuotes(window.appState.activeSector).then(data => {
      if (data) {
        // 迟到响应保护：期间用户可能已切板块，旧板块数据不得覆盖新板块
        if (data.sector && data.sector !== window.appState.activeSector) return;
        window.appState.stocks = data.stocks || [];
        for (const s of window.appState.stocks) { s.sector = window.appState.activeSector; }
        window.appState.stockMap = {};
        for (const s of window.appState.stocks) {
          window.appState.stockMap[s.code] = s;
        }
        // 定时刷新同样不许覆盖用户选中：标的消失才回退，且只有不一致才重绘
        _preserveActiveStock(window.appState.stocks);
        renderStockList();
        _syncChartAfterRefresh();
        updateKlineHeader();
        updateKPICards();
        if (typeof renderHeatmap === 'function') renderHeatmap();
        if (window.appState.signalTableVisible) renderSignalTable();
      }
    });

    fetchGlobal().then(data => {
      if (data) {
        updateIndicesBar(data);
        updateGlobalPanel(data);
      }
    });
  }, interval);
}

// ================================================================
//  腾讯实时行情轮询 — 节能调度（后台休眠，切回瞬间唤醒）
// ================================================================
let _realtimePolling = false;
let _realtimeFailCount = 0;
const REALTIME_MAX_FAIL = 5;  // 连续失败5次后降级
let _realtimeDegraded = false;

/**
 * 更新实时连接状态指示
 */
function updateRealtimeStatus(success) {
  const dot = document.getElementById('rt-status-dot');
  if (!dot) return;
  if (success) {
    _realtimeFailCount = 0;
    if (_realtimeDegraded) {
      _realtimeDegraded = false;
      dot.className = 'rt-dot rt-recovered';
      setTimeout(() => { dot.className = 'rt-dot rt-ok'; }, 2000);
    } else {
      dot.className = 'rt-dot rt-ok';
    }
    dot.title = '实时行情连接正常';
  } else {
    _realtimeFailCount++;
    if (_realtimeFailCount >= REALTIME_MAX_FAIL) {
      _realtimeDegraded = true;
      dot.className = 'rt-dot rt-degraded';
      dot.title = '实时行情连接异常，已降级为低频率刷新';
    } else if (_realtimeFailCount >= 2) {
      dot.className = 'rt-dot rt-warn';
      dot.title = `实时行情不稳定 (${_realtimeFailCount}/${REALTIME_MAX_FAIL})`;
    }
  }
}

async function pollRealtimeTick() {
  if (_realtimePolling || document.hidden) return;
  _realtimePolling = true;

  try {
    const stocks = window.appState.stocks || [];
    if (stocks.length === 0) { _realtimePolling = false; return; }

    // 取前20只 + 当前选中股票 + 指数
    const codes = stocks.slice(0, 20).map(s => s.code);
    if (window.appState.activeCode && !codes.includes(window.appState.activeCode)) {
      codes.push(window.appState.activeCode);
    }
    codes.push('sh000001', 'sz399006', 'sz399001');

    const data = await fetchRealtime(codes);
    if (!data || !data.data) {
      updateRealtimeStatus(false);
      _realtimePolling = false;
      return;
    }

    updateRealtimeStatus(true);
    const rt = data.data;

    let changed = false;
    for (const s of stocks) {
      const q = rt[s.code];
      if (q && q.price > 0) {
        if (s.price !== q.price || s.chg !== q.chg) changed = true;
        s.price = q.price;
        s.chg = q.chg;
        s.high = q.high || s.high;
        s.low = q.low || s.low;
        s.amount = q.amount || s.amount;
        s.turnover = q.turnover || s.turnover;
      }
    }

    const activeQuote = rt[window.appState.activeCode];
    if (activeQuote && activeQuote.price > 0) {
      updateKlineHeader();
      if (typeof updateActiveKlinePrice === 'function') {
        updateActiveKlinePrice(activeQuote.price, activeQuote.high, activeQuote.low);
      }
    }

    const updateIndex = (code, pId, cId) => {
      const q = rt[code];
      if (q && q.price > 0) {
        const pEl = document.getElementById(pId);
        const cEl = document.getElementById(cId);
        if (pEl) pEl.textContent = q.price.toFixed(2);
        if (cEl) {
          cEl.textContent = (q.chg > 0 ? '+' : '') + q.chg.toFixed(2) + '%';
          cEl.className = 'idx-chg ' + (q.chg > 0 ? 'up' : q.chg < 0 ? 'dn' : '');
        }
      }
    };
    updateIndex('sh000001', 'sh-p', 'sh-c');
    updateIndex('sz399006', 'cy-p', 'cy-c');
    updateIndex('sz399001', 'sc-p', 'sc-c');

    if (changed) {
      renderStockList();
      updateKPICards();
    }
    updateDataTimestamp();
  } catch(e) {
    updateRealtimeStatus(false);
  } finally {
    _realtimePolling = false;
  }
}

// 交易时段每3秒轮询，盘后每30秒；连接降级后盘中也降为15秒
function getRealtimeInterval() {
  if (_realtimeDegraded) return 15000;
  return isTradingHours() ? 3000 : 30000;
}

let _realtimeTimeout = null;

function scheduleNextRealtimePoll() {
  if (_realtimeTimeout) clearTimeout(_realtimeTimeout);
  const interval = getRealtimeInterval();
  _realtimeTimeout = setTimeout(() => {
    if (document.hidden) {
      // 后台时不发高频请求，等待唤醒
      return;
    }
    pollRealtimeTick().finally(() => {
      scheduleNextRealtimePoll();
    });
  }, interval);
}

// 标签页可见性变化事件监听：前台激活时立即刷新一次并恢复调度
document.addEventListener('visibilitychange', function() {
  if (!document.hidden) {
    pollRealtimeTick().finally(() => {
      scheduleNextRealtimePoll();
    });
  } else {
    if (_realtimeTimeout) clearTimeout(_realtimeTimeout);
  }
});

scheduleNextRealtimePoll();


// ================================================================
//  智能选股 — 全市场扫描
// ================================================================

/**
 * v5.4: 加载智能选股行业 checkbox 组
 * 从 appState.sectorRankingCache（或调 fetchSectorRanking）生成行业列表
 */
async function loadScanIndustries() {
  var container = document.getElementById('scan-industry-group');
  if (!container) return;

  // 如果没有缓存，先加载
  if (!window.appState.sectorRankingCache) {
    try {
      var apiData = await fetchSectorRanking();
      if (apiData) {
        var sectors = Array.isArray(apiData) ? apiData : (apiData.sectors || apiData.rows || []);
        if (Array.isArray(sectors) && sectors.length > 0) {
          window.appState.sectorRankingCache = sectors.map(function (s) {
            return {
              name: s.name || s['名称'] || '--',
              key: resolveSectorKey(s)
            };
          });
        }
      }
    } catch (e) {
      console.warn('loadScanIndustries: failed to load sector ranking:', e);
    }
  }

  var cache = window.appState.sectorRankingCache;
  if (!cache || cache.length === 0) {
    // 降级：使用本地 SECTORS 配置
    cache = SECTORS.map(function (s) { return { name: s.name, key: s.key }; });
  }

  var html = '';
  cache.forEach(function (s) {
    html += '<label class="sector-checkbox-item">' +
      '<input type="checkbox" value="' + escapeHtml(s.key) + '">' +
      '<span>' + escapeHtml(s.name) + '</span></label>';
  });
  container.innerHTML = html;
}

function showScanPanel() {
  const panel = document.getElementById('scan-panel');
  const leftPanel = document.getElementById('left-panel');
  const rightPanel = document.getElementById('right-panel');
  if (!panel) return;

  window.appState.scanPanelVisible = true;
  panel.style.display = 'flex';
  if (leftPanel) leftPanel.style.display = 'none';
  if (rightPanel) rightPanel.style.display = 'none';

  // v5.4: 加载行业 checkbox 组
  loadScanIndustries();
}

function closeScanPanel() {
  const panel = document.getElementById('scan-panel');
  const leftPanel = document.getElementById('left-panel');
  const rightPanel = document.getElementById('right-panel');
  if (!panel) return;

  window.appState.scanPanelVisible = false;
  panel.style.display = 'none';
  if (leftPanel) leftPanel.style.display = '';
  if (rightPanel) rightPanel.style.display = '';
}

async function startScan() {
  var btn = document.getElementById('btn-start-scan');
  var progress = document.getElementById('scan-progress');
  var progressFill = document.getElementById('scan-progress-fill');
  var progressText = document.getElementById('scan-progress-text');
  var resultArea = document.getElementById('scan-result-area');
  var subtitle = document.getElementById('scan-subtitle');

  if (!btn || !progress || !progressFill || !progressText || !resultArea) {
    console.error('scan: missing DOM elements');
    return;
  }

  // 扫描中禁用价格输入（防误改）
  var priceMinInput = document.getElementById('scan-price-min');
  var priceMaxInput = document.getElementById('scan-price-max');
  if (priceMinInput) priceMinInput.disabled = true;
  if (priceMaxInput) priceMaxInput.disabled = true;

  // Reset state
  btn.disabled = true;
  btn.innerHTML = '<span class="scan-btn-icon">⏳</span> 扫描中...';
  progress.style.display = 'block';
  progressFill.style.width = '0%';
  progressText.textContent = '正在启动扫描引擎...';
  resultArea.innerHTML = '<div class="scan-loading"><div class="scan-loader"></div><div>Tencent实时行情 + DeepSeek AI 分析中...</div></div>';
  setStatus('loading');

  // 读取价格区间
  var priceMinRaw = parseFloat(document.getElementById('scan-price-min')?.value);
  var priceMaxRaw = parseFloat(document.getElementById('scan-price-max')?.value);
  var priceMin = Number.isFinite(priceMinRaw) ? Math.max(0, priceMinRaw) : 0;
  var priceMax = Number.isFinite(priceMaxRaw) ? priceMaxRaw : 200;
  if (priceMax < priceMin) {
    showToast('智能选股', '最高价不能低于最低价');
    if (priceMinInput) priceMinInput.disabled = false;
    if (priceMaxInput) priceMaxInput.disabled = false;
    btn.disabled = false;
    btn.innerHTML = '<span class="scan-btn-icon">▶</span> 开始扫描';
    progress.style.display = 'none';
    return;
  }
  window.appState.priceMin = priceMin;
  window.appState.priceMax = priceMax;

  // v5.4: 收集选中的行业 checkbox
  var selectedSectors = [];
  document.querySelectorAll('#scan-industry-group input[type="checkbox"]:checked').forEach(function (cb) {
    selectedSectors.push(cb.value);
  });
  var sectorsParam = selectedSectors.length > 0 ? selectedSectors.join(',') : '';

  // 构建扫描URL（带价格参数 + 行业参数）
  var scanQuery = '?price_min=' + priceMin + '&price_max=' + priceMax + (sectorsParam ? '&sectors=' + encodeURIComponent(sectorsParam) : '');
  var scanUrl = API.scan + scanQuery;
  var scanStatusUrl = API.scanStatus + scanQuery;

  // Trigger scan
  var triggerResp = null;
  try {
    triggerResp = await fetch(scanUrl, { signal: AbortSignal.timeout(10000) }).then(function(r) { return r.json(); });
  } catch(e) {
    console.error('scan trigger error:', e);
    progressText.textContent = '启动扫描失败: ' + (e.message || '网络错误');
    btn.disabled = false;
    btn.innerHTML = '<span class="scan-btn-icon">↻</span> 重新扫描';
    if (priceMinInput) priceMinInput.disabled = false;
    if (priceMaxInput) priceMaxInput.disabled = false;
    setStatus('error');
    return;
  }

  if (!triggerResp || !triggerResp.ok) {
    progressText.textContent = '扫描服务异常';
    btn.disabled = false;
    btn.innerHTML = '<span class="scan-btn-icon">↻</span> 重新扫描';
    if (priceMinInput) priceMinInput.disabled = false;
    if (priceMaxInput) priceMaxInput.disabled = false;
    setStatus('error');
    return;
  }

  if (triggerResp.data && !triggerResp.pending) {
    // Already cached - render immediately
    renderScanResult(triggerResp.data);
    btn.disabled = false;
    btn.innerHTML = '<span class="scan-btn-icon">↻</span> 重新扫描';
    if (priceMinInput) priceMinInput.disabled = false;
    if (priceMaxInput) priceMaxInput.disabled = false;
    setStatus('ok');
    return;
  }

  // Poll for completion (up to 3 min)
  var totalStocks = (triggerResp.status && triggerResp.status.total) || 0;
  var startTime = Date.now();
  var maxWait = 180000;
  var pollTimer = null;

  function cleanup() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    btn.disabled = false;
    btn.innerHTML = '<span class="scan-btn-icon">↻</span> 重新扫描';
    // 恢复价格输入
    if (priceMinInput) priceMinInput.disabled = false;
    if (priceMaxInput) priceMaxInput.disabled = false;
    if (progress) {
      setTimeout(function() { if (progress) progress.style.display = 'none'; }, 3000);
    }
  }

  pollTimer = setInterval(async function() {
    try {
      var resp = await fetch(scanUrl, { signal: AbortSignal.timeout(8000) }).then(function(r) { return r.json(); });
      if(resp && resp.pending){var statusResp=await fetch(scanStatusUrl,{signal:AbortSignal.timeout(5000)}).then(function(r){return r.json();});if(statusResp&&statusResp.ok&&statusResp.status)resp.status=statusResp.status;}
      
      if (!resp || !resp.ok) return;
      if(resp.pending&&resp.status)resp.status=Object.assign({},resp.status,scanStatus);

      // Check if done
      if (resp.data && !resp.pending) {
        clearInterval(pollTimer);
        pollTimer = null;
        renderScanResult(resp.data);
        setStatus('ok');
        var d = resp.data;
        showToast('OK 智能选股', '扫描完成: ' + (d.total_recommended || 0) + ' 推荐 | ' + ((d.duration_ms || 0) / 1000).toFixed(1) + '秒');
        cleanup();
        return;
      }

      // Update progress and surface partial market-data coverage.
      var scanStatus = resp.status || {};
      if (scanStatus.status === 'error') {
        clearInterval(pollTimer);pollTimer=null;
        progressText.textContent = scanStatus.error || '扫描失败，请重试';
        resultArea.innerHTML = '<div class="scan-empty"><div class="scan-empty-icon">!</div><div class="scan-empty-text">扫描任务失败，服务端已记录错误状态。请稍后重试。</div></div>';
        setStatus('error');cleanup();return;
      }
      if (scanStatus.status === 'done' && !(resp.data && !resp.pending)) {
        clearInterval(pollTimer);pollTimer=null;
        progressText.textContent = '扫描已结束，但结果未能读取；可以重试获取缓存结果。';
        setStatus('error');cleanup();return;
      }
      if (resp.status) {
        var pct = resp.status.progress || 0;
        progressFill.style.width = pct + '%';
        var fetched = resp.status.fetched || 0;
        var phaseLabel = resp.status.phase || '';
        var coverageSuffix = resp.status.coverage != null ? ' · 行情覆盖 ' + resp.status.coverage + '%' : '';
        progressText.textContent = phaseLabel + ' 阶段: ' + fetched + '/' + totalStocks + ' (' + pct + '%)' + coverageSuffix;
      }

      // Timeout check
      if (Date.now() - startTime > maxWait) {
        clearInterval(pollTimer);
        pollTimer = null;
        progressText.textContent = '扫描超时，请重试';
        resultArea.innerHTML = '<div class="scan-empty"><div class="scan-empty-icon">⏰</div><div class="scan-empty-text">扫描超时（>3分钟），请重试</div></div>';
        setStatus('error');
        cleanup();
      }
    } catch(e) {
      // Poll errors are non-fatal
      console.log('scan poll:', e.message || e);
    }
  }, 2000);
}

// Helper: render scan result
function renderScanResult(data) {
  // 前端价格过滤
    var pmin = window.appState.priceMin ?? 0;
    var pmax = window.appState.priceMax ?? 200;
  var recs = (data.recommendations || []).filter(function(r) {
    var p = r.price ?? r.last_close ?? 0;
    return p >= pmin && p <= pmax;
  });
  data.recommendations = recs;
  data.total_recommended = recs.length;

  var pt = document.getElementById('scan-progress-text');
  var pf = document.getElementById('scan-progress-fill');
  var st = document.getElementById('scan-subtitle');
  var quality=data.data_quality||{};
  var coverageText=quality.coverage_pct==null?'覆盖未知':'有效行情覆盖 '+Number(quality.coverage_pct).toFixed(1)+'% ('+Number(quality.valid_count||0)+'/'+Number(quality.configured_count||data.total_scanned||0)+')';
  
  if (pf) { pf.style.width = '100%'; }
  if (pt) {
    pt.textContent = '扫描完成: ' + (data.total_scanned || '?') + ' 只, 推荐 ' + (data.total_recommended || 0) + ' 只 | ' + ((data.duration_ms || 0) / 1000).toFixed(1) + '秒 | AI增强: ' + (data.ai_enhanced || 0) + '只 | ' + coverageText;
    if(quality.level==='poor')pt.textContent+=' · 覆盖不足，结果不宜视为全样本结论';
  }
  if (st) {
    st.textContent = '价格区间: ' + pmin + '~' + pmax + '元 | 扫描 ' + (data.total_scanned || '?') + ' 只 | 推荐 ' + (data.total_recommended || 0) + ' 只 | ' + coverageText + ' | ' + (data.scan_time || '');
  }
  renderScanResults(data);

  // 自动记录扫描信号到历史（仅在首次渲染时记录，避免排序切换时重复）
  recs.forEach(function(rec) {
    addSignalToHistory({
      name: rec.name || '',
      code: rec.code || '',
      action: (rec.action || '').indexOf('卖') >= 0 ? 'sell' : 'buy',
      signal: (rec.signals || []).join(', ') || rec.action || '',
      score: rec.total_score || ''
    });
  });
}

function renderScanResults(data) {
  const resultArea = document.getElementById('scan-result-area');
  if (!resultArea) return;

  // 缓存最近扫描结果（用于排序切换时重新渲染）
  window.appState.lastScanData = data;
  window.appState.scanSortOrder = window.appState.scanSortOrder || 'desc';
  window.appState.scanSortKey = window.appState.scanSortKey || 'score';

  var recommendations = data.recommendations || [];

  // 价格区间过滤
    var pmin = window.appState.priceMin ?? 0;
    var pmax = window.appState.priceMax ?? 200;
  recommendations = recommendations.filter(function(r) {
    var p = r.price ?? r.last_close ?? 0;
    return p >= pmin && p <= pmax;
  });

  // 按综合评分或上涨概率排序
  var order = window.appState.scanSortOrder;
  var sortKey = window.appState.scanSortKey;
  recommendations = recommendations.slice().sort(function (a, b) {
    var sa = sortKey === 'probability' ? Number(a.rise_probability || 0) : Number(a.total_score || a.score || 0);
    var sb = sortKey === 'probability' ? Number(b.rise_probability || 0) : Number(b.total_score || b.score || 0);
    return order === 'desc' ? (sb - sa) : (sa - sb);
  });

  data.total_recommended = recommendations.length;

  if (recommendations.length === 0) {
    resultArea.innerHTML = '<div class="scan-empty"><div class="scan-empty-icon">🔍</div><div class="scan-empty-text">当前无评分>=60的推荐股票</div><div class="scan-empty-hint">市场可能处于调整期，请稍后再扫描</div></div>';
    return;
  }

  // 计算平均评分
  var avgScore = 0;
  if (recommendations.length > 0) {
    avgScore = recommendations.reduce(function (s, r) {
      return s + (r.total_score || r.score || 0);
    }, 0) / recommendations.length;
  }

  let html = '';

  // 排序切换栏
  html += '<div class="scan-sort-bar">' +
    '<span>基 ' + (data.total_recommended || recommendations.length) + ' 只推荐</span>' +
    '<span style="color:var(--gold)">⭐ 平均 ' + avgScore.toFixed(1) + '</span>' +
    '<span style="flex:1"></span>' +
    '<span style="font-size:11px">排序</span>' +
    '<button class="scan-sort-btn ' + (sortKey === 'score' && order === 'desc' ? 'active' : '') + '" onclick="toggleScanSortOrder(\'desc\',\'score\')">评分↓</button>' +
    '<button class="scan-sort-btn ' + (sortKey === 'probability' && order === 'desc' ? 'active' : '') + '" onclick="toggleScanSortOrder(\'desc\',\'probability\')">概率↓</button>' +
    '<button class="scan-sort-btn ' + (sortKey === 'score' && order === 'asc' ? 'active' : '') + '" onclick="toggleScanSortOrder(\'asc\',\'score\')">评分↑</button>' +
    '</div>';

  // 统计卡片
  html += '<div class="scan-stats">';
  html += `<div class="scan-stat-card"><div class="scan-stat-value" style="color:var(--green)">${data.total_recommended}</div><div class="scan-stat-label">推荐数量</div></div>`;

  // 评分分布
  const highScore = recommendations.filter(r => r.total_score >= 80).length;
  const midScore = recommendations.filter(r => r.total_score >= 70 && r.total_score < 80).length;
  const lowScore = recommendations.filter(r => r.total_score >= 60 && r.total_score < 70).length;

  html += `<div class="scan-stat-card"><div class="scan-stat-value" style="color:var(--gold)">${highScore}</div><div class="scan-stat-label">强烈推荐</div></div>`;
  html += `<div class="scan-stat-card"><div class="scan-stat-value" style="color:var(--blue)">${midScore}</div><div class="scan-stat-label">推荐买入</div></div>`;
  html += `<div class="scan-stat-card"><div class="scan-stat-value" style="color:var(--cyan)">${lowScore}</div><div class="scan-stat-label">值得关注</div></div>`;
  html += `<div class="scan-stat-card"><div class="scan-stat-value" style="color:var(--purple)">${avgScore.toFixed(1)}</div><div class="scan-stat-label">平均评分</div></div>`;
  html += '</div>';

  // 推荐卡片列表
  html += '<div class="scan-card-list">';

  for (const rec of recommendations) {
    const scoreColor = rec.total_score >= 80 ? 'var(--red)' : rec.total_score >= 70 ? 'var(--gold)' : 'var(--blue)';
    const scoreBg = rec.total_score >= 80 ? 'rgba(255,61,106,0.12)' : rec.total_score >= 70 ? 'rgba(245,158,11,0.12)' : 'rgba(59,130,246,0.12)';
    const scoreBorder = rec.total_score >= 80 ? 'rgba(255,61,106,0.3)' : rec.total_score >= 70 ? 'rgba(245,158,11,0.3)' : 'rgba(59,130,246,0.3)';
    const chgVal = rec.chg ?? rec.change_pct ?? 0;
    const chgCls = chgVal > 0 ? 'up' : chgVal < 0 ? 'dn' : 'flat';
    const chgSign = chgVal > 0 ? '+' : '';
    const safeName = escapeHtml(rec.name);
    const safeCode = escapeHtml(rec.code);
    const safeSector = escapeHtml(rec.sector_name || '');
    const safeAction = escapeHtml(rec.action || '');
    const safeReason = escapeHtml(rec.reason || rec.ai_reason || '');
    const safeBuyLogic = escapeHtml(rec.buy_logic || '等待价格进入计划区间，并结合量价承接分批执行');
    const priceVal = rec.price ?? rec.last_close ?? 0;
    const buyLow = Array.isArray(rec.buy_zone) ? rec.buy_zone[0] : rec.buy_zone?.low ?? 0;
    const buyHigh = Array.isArray(rec.buy_zone) ? rec.buy_zone[1] : rec.buy_zone?.high ?? 0;
    const probability = Math.max(0, Math.min(100, Number(rec.rise_probability || 0)));
    const probabilityTone = probability >= 68 ? 'high' : probability >= 56 ? 'medium' : 'low';
    const probabilityLabel = escapeHtml(rec.probability_level || (probability >= 68 ? '较高' : probability >= 56 ? '中等' : '偏低'));
    const riseDrivers = (rec.rise_drivers || []).slice(0, 4).map(function(item) {
      return '<li>' + escapeHtml(item) + '</li>';
    }).join('') || '<li>综合因子暂未形成强共振</li>';
    const entryConditions = (rec.entry_conditions || []).slice(0, 4).map(function(item) {
      return '<li>' + escapeHtml(item) + '</li>';
    }).join('');
    const riskFactors = (rec.risk_factors || []).slice(0, 3).map(function(item) {
      return '<span>' + escapeHtml(item) + '</span>';
    }).join('') || '<span class="safe">暂无高优先级风险因子</span>';
    const factorEvidence = (rec.factor_evidence || []).slice(0, 6).map(function(item) {
      var impact = ['strong','positive','warning','negative','neutral'].includes(item.impact) ? item.impact : 'neutral';
      return '<div class="scan-factor ' + impact + '"><span>' + escapeHtml(item.label || '') + '</span><strong>' + escapeHtml(item.value || '--') + '</strong></div>';
    }).join('');

    // 信号标签
    const signalTags = (rec.signals || []).slice(0, 4).map(s =>
      `<span class="scan-signal-tag">${escapeHtml(s)}</span>`
    ).join('');

    html += `
      <div class="scan-card" onclick="onScanCardClick(decodeURIComponent('${encodeURIComponent(rec.code || '').replace(/'/g, '%27')}'),decodeURIComponent('${encodeURIComponent(rec.name || '').replace(/'/g, '%27')}'))">
        <div class="scan-card-header">
          <div class="scan-card-stock">
            <div class="scan-card-name">${safeName}</div>
            <div class="scan-card-code">${safeCode} · ${safeSector}</div>
          </div>
          <div class="scan-card-head-metrics">
            <div class="scan-probability ${probabilityTone}"><span>上涨概率估算</span><strong>${probability.toFixed(1)}%</strong><em>${probabilityLabel}</em></div>
            <div class="scan-card-score" style="color:${scoreColor};background:${scoreBg};border-color:${scoreBorder}" title="综合评分">
              ${rec.total_score}
            </div>
          </div>
        </div>
        <div class="scan-card-body">
          <div class="scan-card-row">
            <span class="scan-card-label">现价</span>
            <span class="scan-card-value ${chgCls}">${safeFixed(priceVal)}</span>
            <span class="scan-card-label" style="margin-left:12px">涨跌</span>
            <span class="scan-card-value ${chgCls}">${chgSign}${safeFixed(chgVal)}%</span>
          </div>
          <div class="scan-card-row">
            <span class="scan-card-label">买入区间</span>
            <span class="scan-card-value scan-buy-zone">${safeFixed(buyLow)} - ${safeFixed(buyHigh)}</span>
            <span class="scan-card-label" style="margin-left:12px">止损</span>
            <span class="scan-card-value scan-stop-loss">${safeFixed(rec.stop_loss)}</span>
          </div>
          <div class="scan-card-row">
            <span class="scan-card-label">止盈1</span>
            <span class="scan-card-value" style="color:var(--cyan)">${safeFixed(rec.stop_profit_1)}</span>
            <span class="scan-card-label" style="margin-left:12px">止盈2</span>
            <span class="scan-card-value" style="color:var(--cyan)">${safeFixed(rec.stop_profit_2)}</span>
          </div>
          <div class="scan-card-row">
            <span class="scan-card-label">仓位</span>
            <span class="scan-card-value">${rec.position_pct}%</span>
            <span class="scan-card-label" style="margin-left:12px">风险</span>
            <span class="scan-card-value" style="color:${rec.risk_level === '低' ? 'var(--green)' : (rec.risk_level === '中' || rec.risk_level === '中等') ? 'var(--gold)' : 'var(--red)'}">${escapeHtml(rec.risk_level || '')}</span>
          </div>
        </div>
        ${factorEvidence ? `<div class="scan-factor-grid">${factorEvidence}</div>` : ''}
        <div class="scan-explain-grid">
          <section class="scan-explain-block drivers"><h4>为什么具备上涨动能</h4><ul>${riseDrivers}</ul></section>
          <section class="scan-explain-block logic"><h4>买入逻辑</h4><p>${safeBuyLogic}</p>${entryConditions ? `<ul>${entryConditions}</ul>` : ''}</section>
        </div>
        <div class="scan-risk-list"><strong>风险约束</strong>${riskFactors}</div>
        <div class="scan-probability-basis"><span>${escapeHtml(rec.probability_horizon || '未来1-5个交易日')}</span>${escapeHtml(rec.probability_basis || '')}</div>
        <div class="scan-card-footer">
          <div class="scan-card-signals">${signalTags}</div>
          <div class="scan-card-action" style="color:${scoreColor}">${safeAction}</div>
        </div>
        <div class="scan-card-reason">核心结论：${safeReason}</div>
        ${rec.ai_reason ? `<div class="scan-card-ai">AI补充 <span>${escapeHtml(rec.ai_reason)}</span></div>` : ''}
      </div>
    `;
  }

  html += '</div>';
  resultArea.innerHTML = html;
}

/**
 * 切换智能选股排序方向并重新渲染
 * @param {string} [order] - 'desc' 或 'asc'，不传则切换当前值
 */
function toggleScanSortOrder(order, key) {
  window.appState.scanSortOrder = order || (window.appState.scanSortOrder === 'desc' ? 'asc' : 'desc');
  window.appState.scanSortKey = key || window.appState.scanSortKey || 'score';
  if (window.appState.lastScanData) {
    renderScanResults(window.appState.lastScanData);
  }
}

function onScanCardClick(code, name) {
  // 关闭扫描面板，切换到该股票详情
  closeScanPanel();

  // 尝试从当前股票列表中找到该股票
  const stockData = window.appState.stockMap[code];
  if (stockData) {
    selectStock(code, name);
  } else {
    // 股票不在当前板块，直接加载K线
    window.appState.activeCode = code;
    window.appState.activeName = name;
    selectStock(code, name);
  }
}

// ================================================================
//  键盘导航
// ================================================================
let _kbFocusIdx = -1;

function _getStockRows() {
  return [...document.querySelectorAll('#stock-list .stock-row')];
}

function _focusStockRow(idx) {
  const rows = _getStockRows();
  if (rows.length === 0) return;
  idx = Math.max(0, Math.min(idx, rows.length - 1));
  _kbFocusIdx = idx;
  rows.forEach((r, i) => r.classList.toggle('kb-hover', i === idx));
  rows[idx].scrollIntoView({ block: 'nearest' });
}

document.addEventListener('keydown', (e) => {
  // 不要干扰输入框
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;

  const rows = _getStockRows();

  switch (e.key) {
    case 'ArrowDown':
    case 'j':
      e.preventDefault();
      if (rows.length === 0) return;
      if (_kbFocusIdx < 0) _kbFocusIdx = -1;
      _focusStockRow(_kbFocusIdx + 1);
      break;

    case 'ArrowUp':
    case 'k':
      e.preventDefault();
      if (rows.length === 0) return;
      if (_kbFocusIdx < 0) _kbFocusIdx = rows.length;
      _focusStockRow(_kbFocusIdx - 1);
      break;

    case 'Enter':
      if (_kbFocusIdx >= 0 && rows[_kbFocusIdx]) {
        e.preventDefault();
        rows[_kbFocusIdx].click();
      }
      break;

    // v9: Space 切换自选（焦点行）
    case ' ':
      if (_kbFocusIdx >= 0 && rows[_kbFocusIdx]) {
        e.preventDefault();
        const favCode = rows[_kbFocusIdx].getAttribute('data-code');
        if (favCode) onFavoriteClick(null, favCode);
      }
      break;

    // v9: C 加入对比（焦点行）
    case 'c':
    case 'C':
      if (!e.ctrlKey && !e.metaKey && _kbFocusIdx >= 0 && rows[_kbFocusIdx]) {
        e.preventDefault();
        const cmpCode = rows[_kbFocusIdx].getAttribute('data-code');
        if (cmpCode) onCompareClick(null, cmpCode);
      }
      break;

    case 'r':
    case 'R':
      if (!e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        refreshAll();
      }
      break;

    case 'Escape':
      // 关闭扫描面板
      if (window.appState.scanPanelVisible) {
        closeScanPanel();
      }
      // 关闭信号表格
      if (window.appState.signalTableVisible) {
        toggleSignalTable();
      }
      break;

    case '1':
      if (!e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        const allTab = document.querySelector('#left-panel .tab[data-filter="all"]');
        if (allTab) switchListTab(allTab, 'all');
      }
      break;

    case '2':
      if (!e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        const upTab = document.querySelector('#left-panel .tab[data-filter="up"]');
        if (upTab) switchListTab(upTab, 'up');
      }
      break;

    case '3':
      if (!e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        const dnTab = document.querySelector('#left-panel .tab[data-filter="dn"]');
        if (dnTab) switchListTab(dnTab, 'dn');
      }
      break;
  }
});

// 鼠标交互时清除键盘高亮
document.addEventListener('click', (e) => {
  if (e.target.closest('#stock-list')) {
    _kbFocusIdx = -1;
    document.querySelectorAll('#stock-list .stock-row.kb-hover').forEach(r => r.classList.remove('kb-hover'));
  }
  // 点击通知中心外部关闭
  const nc = document.getElementById('notification-center');
  if (nc && nc.classList.contains('open') && !e.target.closest('#notification-center') && !e.target.closest('#notif-bell')) {
    toggleNotificationCenter();
  }
  // 点击设置面板外部关闭
  const sp = document.getElementById('settings-panel');
  if (sp && sp.classList.contains('open') && !e.target.closest('#settings-panel') && !e.target.closest('#btn-settings')) {
    toggleSettingsPanel();
  }
});

// ================================================================
//  网络状态检测
// ================================================================
function _createOfflineBanner() {
  const banner = document.createElement('div');
  banner.className = 'offline-banner';
  banner.id = 'offline-banner';
  banner.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 1l22 22"/><path d="M16.72 11.06A10.94 10.94 0 0119 12.55"/><path d="M5 12.55a10.94 10.94 0 015.06-2.94M10.71 5.05A16 16 0 0122.58 9M1.42 9a15.91 15.91 0 014.7-2.88M8.53 16.11a6 6 0 016.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg> <span>网络连接已断开，数据可能不是最新的</span>';
  document.body.prepend(banner);
  return banner;
}

const _offlineBanner = _createOfflineBanner();

window.addEventListener('online', () => {
  _offlineBanner.classList.remove('show');
  setStatus('ok');
  refreshAll();
});

window.addEventListener('offline', () => {
  _offlineBanner.classList.add('show');
  setStatus('error');
  showToast('网络断开', '正在等待网络恢复...');
});

// ================================================================
//  重试初始化（loader 失败时点击重试按钮调用）
// ================================================================
async function retryInit() {
  // 重置加载状态
  var loader = document.getElementById('loader');
  var retryBtn = document.getElementById('loader-retry-btn');
  var stageEl = document.getElementById('loader-stage');
  var subEl = document.getElementById('loader-sub');
  var barFill = document.getElementById('loader-bar-fill');
  var ringProgress = document.getElementById('loader-ring-progress');

  // 显示 loader
  if (loader) {
    loader.classList.remove('done');
    loader.style.display = '';
  }
  if (retryBtn) retryBtn.classList.remove('show');

  // 手动更新加载进度
  if (window.__loadProgress) {
    window.__loadProgress.setStage(0);
  } else {
    if (stageEl) stageEl.textContent = '重新连接中…';
    if (subEl) subEl.textContent = '正在尝试重新初始化';
    if (barFill) barFill.style.width = '10%';
    if (ringProgress) ringProgress.setAttribute('stroke-dashoffset', '141');
  }

  // 等待一帧让UI更新
  await new Promise(function(r) { requestAnimationFrame(r); });

  // 重新初始化
  window.__initCalled = false;
  window.__initStart = Date.now();

  try {
    await init();
  } catch (e) {
    console.error('[DAPENG] 重试初始化失败:', e);
    if (window.__loadProgress) {
      window.__loadProgress.setError('初始化失败: ' + (e.message || '未知错误'));
    }
  }
}

// 暴露到全局，供 HTML onclick 调用
window.retryInit = retryInit;

// ================================================================
//  跨页深链
// ================================================================
/**
 * 解析 /dashboard.html?code=sz002185[&sector=semiconductor]
 *
 * 背景：落地页卡片、情绪榜榜单、接口目录示例统一用该形式跳转，但原实现
 * 完全不解析查询串 —— 所有 ?code= 链接都是空操作（点进去仍停在默认板块第一只）。
 *
 * 返回 {code, sector, name} 或 null。解析失败不阻断主流程。
 */
async function _resolveDeepLink() {
  var qs;
  try { qs = new URLSearchParams(window.location.search); } catch (e) { return null; }
  var code = (qs.get('code') || '').trim();
  var sector = (qs.get('sector') || '').trim();
  if (!code && !sector) return null;

  var out = { code: code, sector: sector, name: '' };
  if (!code) return out;

  // 代码 → 所属板块：/api/search 返回 {code, name, sector, sector_name}
  try {
    var r = await fetch('/api/search?keyword=' + encodeURIComponent(code) + '&limit=5', { cache: 'no-store' });
    var d = await r.json();
    var list = (d && d.ok && d.data && d.data.results) || [];
    var hit = null;
    for (var i = 0; i < list.length; i++) {
      if (list[i] && list[i].code === code) { hit = list[i]; break; }
    }
    if (hit) {
      out.sector = out.sector || hit.sector || '';
      out.name = hit.name || '';
    }
  } catch (e) {
    console.warn('[DAPENG] 深链板块解析失败，回退到默认板块:', e);
  }
  return out;
}

// ================================================================
//  跨页上下文同步：把「当前板块 / 当前标的」带进其它页面的入口
//
//  为什么需要：此前 header 里的「情绪 / 回测 / 文档 / 接口」都是裸链接，
//  用户从半导体板块点「情绪」，落地在情绪页的默认视图，
//  还得自己在 50 个东财行业板块里重新找一遍「半导体」—— 上下文在这一跳就丢了。
//  现在统一在这里按 appState 刷新 href（含 K 线浮层底部的「相关信息」条）。
// ================================================================
function _syncContextLinks() {
  var sec = window.appState.activeSector || '';
  var code = window.appState.activeCode || '';
  var secName = (SECTORS.find(function (s) { return s.key === sec; }) || {}).name || sec;
  var q = function (pairs) {
    var parts = [];
    for (var k in pairs) {
      if (pairs[k]) parts.push(k + '=' + encodeURIComponent(pairs[k]));
    }
    return parts.length ? ('?' + parts.join('&')) : '';
  };

  var links = [
    ['hdr-link-sentiment', '/dashboard.html', { sector: sec }, '#market'],
    ['kol-sentiment', '/dashboard.html', { sector: sec }, '#market'],
    ['kol-quant', '/quant.html', { code: code, sector: sec }],
    ['kol-docs', '/project-docs.html', { code: code }],
    ['kol-api', '/api-reference.html', null]
  ];

  links.forEach(function (it) {
    var el = document.getElementById(it[0]);
    if (!el) return;
    el.href = it[1] + (it[2] ? q(it[2]) : '') + (it[3] || '');
    if (it[2] && it[2].sector && secName && el.id === 'kol-sentiment') {
      el.textContent = '📈 ' + secName + ' · 市场情绪';
    }
  });
}

// ================================================================
//  初始化
// ================================================================
async function init() {
  // 防止重复初始化
  if (window.__initCalled) return;
  window.__initCalled = true;

  // 跨页深链：先解析目标代码所属板块，再走正常加载流程
  var _deep = await _resolveDeepLink();
  if (_deep) {
    if (_deep.sector && _deep.sector !== window.appState.activeSector) {
      window.appState.activeSector = _deep.sector;
    }
    if (_deep.sector) {
      // 板块标题只在 switchSector() 内赋值，不会随 activeSector 自动更新，
      // 深链改板块时必须显式同步，否则列表已是新板块而标题仍是旧板块。
      // （侧栏高亮由 renderSectorBar() 依据 activeSector 自动处理）
      var _secName = (SECTORS.find(function (s) { return s.key === _deep.sector; }) || {}).name;
      var _titleEl = document.getElementById('sector-title-name');
      if (_titleEl && _secName) _titleEl.textContent = _secName;
    }
    if (_deep.code) {
      // 预置选中；下面 _preserveActiveStock 见到它仍在列表中就不会被首只覆盖
      window.appState.activeCode = _deep.code;
      window.appState.activeName = _deep.name || _deep.code;
    }
  }

  // 首屏就把跨页入口的上下文参数对齐（深链进来的板块/标的也要带出去）
  _syncContextLinks();

  // 安全网：最多 10 秒强制隐藏 loader（防止任何异常导致永远卡住）
  setTimeout(() => {
    const loader = document.getElementById('loader');
    if (loader && !loader.classList.contains('hidden') && !loader.classList.contains('done')) {
      console.warn('[DAPENG] Loader timeout — force hiding');
      loader.classList.add('hidden');
      setTimeout(() => { if (loader) loader.style.display = 'none'; }, 400);
    }
  }, 10000);

  initMouseGlow();
  initTheme();
  initChart();
  initRadarChart();
  renderSectorBar();
  loadSectorCounts();  // 异步加载板块股票数量

  const searchInput = document.getElementById('search-input');
  if (searchInput) {
    searchInput.addEventListener('keydown', handleSearch);
  }

  showStockListSkeleton();

  // ===== 第一阶段：快速加载（fast=1 跳过K线等待，首屏秒开）=====
  const [quotesData, globalData] = await Promise.all([
    fetchQuotes(window.appState.activeSector, true),  // fast=1: 只要行情不等K线
    fetchGlobal(),
  ]);

  if (quotesData) {
    window.appState.stocks = quotesData.stocks || [];
    for (const stock of window.appState.stocks) { stock.sector = window.appState.activeSector; }
    window.appState.stockMap = {};
    for (const stock of window.appState.stocks) {
      window.appState.stockMap[stock.code] = stock;
    }
    // 保留用户当前选中：只有当它不在新列表中时才回退（不可无条件覆盖）。
    // 首次进入时 activeCode 为空 → 这里会取列表第一只；重试初始化（retryInit）
    // 时则尊重用户已经选过的标的。
    var _preserved = _preserveActiveStock(window.appState.stocks);
    renderStockList();
    updateKPICards();

    // 深链目标未落在当前板块：明确告知，而不是静默显示首只
    if (_deep && _deep.code && _preserved && _preserved.code !== _deep.code) {
      if (typeof showToast === 'function') {
        showToast('跳转', '未在当前板块找到 ' + _deep.code + '，已显示默认标的');
      }
    }
    // 深链命中：把选中行滚进可视区
    if (_deep && _deep.code && _preserved && _preserved.code === _deep.code) {
      var _row = document.querySelector('.stock-row[data-code="' + _deep.code + '"]');
      if (_row && _row.scrollIntoView) {
        try { _row.scrollIntoView({ block: 'nearest' }); } catch (e) { }
      }
    }
  }

  if (globalData) {
    updateIndicesBar(globalData);
    updateGlobalPanel(globalData);
  }

  // 隐藏 loader（行情已到，即可显示界面）
  const loader = document.getElementById('loader');
  if (loader) {
    loader.classList.add('hidden');
    setTimeout(() => loader.style.display = 'none', 400);
  }

  setStatus('ok');
  startSmartRefresh();
  initTickerTape();
  initSettings();
  initPanelResizer();
  initLeftPanelNarrow(); // v7: 窄面板自适应（<260px 隐藏 sparkline/环形评分）
  initThemeButtons();
  initSignalHistoryPanel();

  // ===== 第二阶段：加载K线（不阻塞首屏）=====
  if (window.appState.activeCode) {
    // 确保图表容器已渲染（等一个 frame）
    await new Promise(r => requestAnimationFrame(r));
    resizeKline();

    // 确保 echarts 已初始化
    await _ensureChartReady();

    await loadAndRenderKline(window.appState.activeCode);

    const stockData = window.appState.stockMap[window.appState.activeCode] || {};
    const klineResp = window.appState.klineCache[window.appState.activeCode + ':' + window.appState.period];
    updateStrategyPanel(stockData, klineResp);
    updateTechIndicators(klineResp);
    updateLaws(stockData, klineResp);
    updateKlineHeader();
  }

  // ===== 第三阶段：后台更新策略评分（不影响用户操作）=====
  setTimeout(() => {
    // 用非fast模式刷新一次获取完整策略评分
    fetchQuotes(window.appState.activeSector, false).then(data => {
      if (data && data.stocks) {
        // 静默更新评分（不重新渲染整个列表，只更新评分字段）
        for (const s of data.stocks) {
          if (window.appState.stockMap[s.code]) {
            window.appState.stockMap[s.code].score = s.score;
            window.appState.stockMap[s.code].signals = s.signals;
          }
        }
        for (const s of data.stocks) { s.sector = window.appState.activeSector; }
        window.appState.stocks = data.stocks;
        renderStockList();
        updateKPICards();

        // 自动加载左下角板块分析数据
        const sectorStats = calculateSectorStats();
        if (typeof renderMiniSectorAnalysis === 'function') {
          renderMiniSectorAnalysis(sectorStats);
        }
        if (typeof renderSectorRankings === 'function') {
          renderSectorRankings(sectorStats);
        }
      }
    }).catch(() => {});
  }, 3000);  // 3秒后后台补全策略评分
}

/**
 * 等待 ECharts 图表实例就绪
 * @returns {Promise<void>}
 */
async function _ensureChartReady() {
  if (klineChart && !klineChart.isDisposed()) return;
  // 如果图表未就绪，等待最多5秒
  let waited = 0;
  while (!klineChart && waited < 5000) {
    await new Promise(r => setTimeout(r, 100));
    waited += 100;
    // 尝试重新初始化
    if (!klineChart) initChart();
  }
  // 最后强制尝试
  if (!klineChart) {
    const el = document.getElementById('kline-chart');
    if (el && typeof echarts !== 'undefined') {
      if (el.clientHeight === 0) el.style.height = '380px';
      try {
        klineChart = echarts.init(el, null, { renderer: 'canvas' });
      } catch (e) {
        console.error('[DAPENG] 强制初始化 ECharts 失败:', e);
      }
    }
  }
}


/**
 * v10: 更新板块 KPI 指标卡片
 * 计算当前显示列表的统计数据：涨跌比、总成交、平均换手、领涨/领跌个股
 */
function updateKPICards() {
  const items = window.appState.stocks || [];
  if (items.length === 0) return;

  let up = 0, dn = 0, totalAmount = 0, totalTurnover = 0;
  let best = items[0], worst = items[0];

  items.forEach(s => {
    if ((s.chg || 0) > 0) up++;
    else if ((s.chg || 0) < 0) dn++;
    
    totalAmount += (s.amount || (s.price * s.volume / 100) || 0);
    totalTurnover += (s.turnover || 0);
    
    if ((s.chg || 0) > (best.chg || 0)) best = s;
    if ((s.chg || 0) < (worst.chg || 0)) worst = s;
  });

  const avgTurnover = totalTurnover / items.length;

  const updateEl = (id, val, cls, pulse = true) => {
    const el = document.getElementById(id);
    if (!el) return;
    const oldVal = el.textContent;
    el.textContent = val;
    if (cls) el.className = 'kpi-value ' + cls;
    if (pulse && oldVal !== val) {
      el.classList.remove('pulse');
      void el.offsetWidth;
      el.classList.add('pulse');
    }
  };

  // 1. 涨跌比（唯一写入方；卡片标签即「涨跌比」→ 副标题显示比值，方向性配色：涨多=红/跌多=绿）
  updateEl('kpi-updn', `${up}/${dn}`, up >= dn ? 'up' : 'dn');
  const updnPct = document.getElementById('kpi-updn-pct');
  if (updnPct) {
    const ratio = dn > 0 ? (up / dn).toFixed(1) : up > 0 ? '∞' : '0';
    updnPct.textContent = `涨跌比 ${ratio}`;
    updnPct.style.color = up > dn ? 'var(--red)' : up < dn ? 'var(--green)' : 'var(--text2)';
  }

  // 2. 成交额
  const amountStr = totalAmount >= 1e8 ? (totalAmount / 1e8).toFixed(2) + '亿' : (totalAmount / 1e4).toFixed(0) + '万';
  updateEl('kpi-amount', amountStr, 'cyan');
  const amountChgEl = document.getElementById('kpi-amount-chg');
  if (amountChgEl) amountChgEl.textContent = `${items.length}只个股`;

  // 3. 换手率
  updateEl('kpi-turnover', avgTurnover.toFixed(2) + '%', 'gold');
  const turnoverDescEl = document.getElementById('kpi-turnover-desc');
  if (turnoverDescEl) turnoverDescEl.textContent = avgTurnover > 5 ? '活跃' : avgTurnover > 2 ? '正常' : '低迷';

  // 4. 最强/最弱
  updateEl('kpi-best', best.name, 'up');
  const bestChg = document.getElementById('kpi-best-chg');
  if (bestChg) bestChg.textContent = (best.chg > 0 ? '+' : '') + best.chg.toFixed(2) + '%';

  updateEl('kpi-worst', worst.name, 'dn');
  const worstChg = document.getElementById('kpi-worst-chg');
  if (worstChg) worstChg.textContent = (worst.chg > 0 ? '+' : '') + worst.chg.toFixed(2) + '%';

  // v11: 更新市场广度
  const breadthFill = document.getElementById('breadth-fill');
  if (breadthFill) {
    const breadth = (up / items.length * 100);
    breadthFill.style.width = breadth + '%';
    document.getElementById('market-breadth-bar').title = `市场广度：当前板块上涨占比 ${breadth.toFixed(1)}%`;
  }
}

// 北向条目缓存 + 应用器。
// 背景：loadQuotes 每次行情刷新都会调用 updateTickerData() → renderTickerTape()，
// 而后者会整体重写 #ticker-track 的 innerHTML，从而把北向条目一并覆盖掉
// （表现为北向只闪现几秒就消失）。故把北向 HTML 缓存下来，轨道重建后自动恢复。
var _northTickerHtml = '';

function applyNorthTicker() {
  var el = document.getElementById('ticker-track');
  if (!el) return;
  el.querySelectorAll('.ticker-item.is-north, .ticker-sep.is-north').forEach(function (n) {
    n.remove();
  });
  if (_northTickerHtml) el.insertAdjacentHTML('afterbegin', _northTickerHtml);
}

/**
 * v12: 接入全市场北向资金监控
 * v17 修复四处缺陷：
 *   1) 原请求 /api/market/north 在该路由模块被挂载前**恒定 404**（模块 import 了
 *      不存在的单例，故长期未注册；现已修正并注册）；
 *   2) 原涨跌着色为「涨绿跌红」，与 A 股「红涨绿跌」惯例**相反**；
 *   3) 北向「成交净买额」自 2024-08 起交易所已**停止实时披露**（接口返回 0），
 *      原实现会渲染为「0.00 亿」造成误读 —— 现改为呈现仍然真实可得的
 *      成分股涨跌分布与相关指数表现，并明示「已停止披露」；
 *   4) 本函数此前**定义了却从未被调用**（死代码），且在行情刷新重写轨道后会被覆盖。
 *      现已接入首屏 + 定时刷新，并通过 applyNorthTicker() 保证重建后自动恢复。
 */
async function updateNorthFlow() {
  try {
    const r = await fetch('/api/market/north');
    const d = await r.json();
    if (!d.ok || !d.data) {
      console.warn('North flow unavailable:', d.message || 'no data');
      return;
    }
    const nf = d.data;
    const el = document.getElementById('ticker-track');
    if (!el) return;

    const RED = 'var(--red)', GREEN = 'var(--green)';
    let html = '';

    if (nf.disclosed) {
      const yi = (v) => (Number(v) / 10000).toFixed(2) + ' 亿';
      html =
        `<div class="ticker-item is-north">
          <span class="ticker-label">北向资金(今日)</span>
          <span class="ticker-val" style="color:${Number(nf.total) >= 0 ? RED : GREEN}">${yi(nf.total)}</span>
        </div>
        <div class="ticker-sep is-north"></div>
        <div class="ticker-item is-north">
          <span class="ticker-label">沪股通</span>
          <span class="ticker-val" style="color:${Number(nf.hgt) >= 0 ? RED : GREEN}">${yi(nf.hgt)}</span>
        </div>
        <div class="ticker-item is-north">
          <span class="ticker-label">深股通</span>
          <span class="ticker-val" style="color:${Number(nf.sgt) >= 0 ? RED : GREEN}">${yi(nf.sgt)}</span>
        </div>`;
    } else {
      const idx = (nf.indexes || []).map(function (ix) {
        const c = Number(ix.chg) || 0;
        return `<div class="ticker-item is-north">
          <span class="ticker-label">${ix.name || ''}</span>
          <span class="ticker-val" style="color:${c >= 0 ? RED : GREEN}">${c >= 0 ? '+' : ''}${c.toFixed(2)}%</span>
        </div>`;
      }).join('');
      html =
        `<div class="ticker-item is-north">
          <span class="ticker-label" title="${nf.note || ''}">北向净买额</span>
          <span class="ticker-val" style="color:var(--muted)">已停止披露</span>
        </div>
        <div class="ticker-sep is-north"></div>
        <div class="ticker-item is-north">
          <span class="ticker-label">沪股通涨跌</span>
          <span class="ticker-val"><span style="color:${RED}">${nf.hgt_up || 0}涨</span> / <span style="color:${GREEN}">${nf.hgt_down || 0}跌</span></span>
        </div>
        <div class="ticker-item is-north">
          <span class="ticker-label">深股通涨跌</span>
          <span class="ticker-val"><span style="color:${RED}">${nf.sgt_up || 0}涨</span> / <span style="color:${GREEN}">${nf.sgt_down || 0}跌</span></span>
        </div>` + idx;
    }
    _northTickerHtml = html;
    applyNorthTicker();
  } catch (e) {
    console.warn('North flow fetch failed', e);
  }
}
var SoundEngine = (function() {
  var ctx = null;
  var enabled = true;

  function getCtx() {
    if (!ctx) {
      try { ctx = new (window.AudioContext || window.webkitAudioContext)(); }
      catch(e) { enabled = false; return null; }
    }
    if (ctx.state === 'suspended') ctx.resume();
    return ctx;
  }

  function beep(freq, duration, type, vol) {
    if (!enabled) return;
    var c = getCtx();
    if (!c) return;
    type = type || 'sine';
    vol = vol || 0.08;
    var osc = c.createOscillator();
    var gain = c.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, c.currentTime);
    gain.gain.setValueAtTime(vol, c.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, c.currentTime + duration);
    osc.connect(gain);
    gain.connect(c.destination);
    osc.start(c.currentTime);
    osc.stop(c.currentTime + duration);
  }

  return {
    buy: function() {
      beep(880, 0.08, 'sine', 0.06);
      setTimeout(function() { beep(1100, 0.1, 'sine', 0.07); }, 80);
      setTimeout(function() { beep(1320, 0.15, 'sine', 0.05); }, 180);
    },
    sell: function() {
      beep(440, 0.1, 'square', 0.05);
      setTimeout(function() { beep(330, 0.15, 'square', 0.05); }, 110);
    },
    signal: function() {
      beep(660, 0.06, 'triangle', 0.04);
      setTimeout(function() { beep(880, 0.08, 'triangle', 0.04); }, 70);
    },
    notify: function() {
      beep(1000, 0.05, 'sine', 0.03);
    },
    setEnabled: function(v) { enabled = v; },
    isEnabled: function() { return enabled; }
  };
})();

// ===== 增强通知系统 =====
var notificationHistory = [];
var MAX_NOTIFICATIONS = 50;

// ★ 断环关键：必须在 enhancedToast 定义「之前」捕获原始 toast 引用。
//   此处 showToast 仍是第 404 行的原始实现（下方的 showToast patch 在文件更尾部才执行），
//   故 _origShowToast 指向真正弹窗的实现；enhancedToast 调它就不会回到被 patch 的 showToast。
var _origShowToast = showToast;
var _toastInFlight = false;   // 重入保护：一次通知流程内「弹窗 + 记历史」各只走一次，杜绝任何形式成环

// 记录一条通知历史（内部使用；每次通知只调用一次）
function _toastRecord(title, body, type) {
  notificationHistory.unshift({
    title: title, body: body, type: type,
    time: new Date().toLocaleTimeString('zh-CN', { hour12: false })
  });
  if (notificationHistory.length > MAX_NOTIFICATIONS) notificationHistory.pop();
  renderNotificationList();
  updateNotifBadge();
}

function enhancedToast(title, body, type) {
  // 重入保护：若已在一次通知流程内被再次进入，直接返回，防止无限递归
  if (_toastInFlight) return;
  _toastInFlight = true;
  try {
    type = type || 'signal';
    // 弹窗：只调用「原始」toast，绝不再回调被 patch 的 showToast（断环）
    _origShowToast(title, body);
    // 存入历史（一次调用只记一条）
    _toastRecord(title, body, type);
    // 音效
    if (type === 'buy') SoundEngine.buy();
    else if (type === 'sell') SoundEngine.sell();
    else if (type === 'signal') SoundEngine.signal();
    else SoundEngine.notify();
    // 粒子特效
    if (window._settings && window._settings.particles !== false) {
      spawnSparkles(type);
    }
  } finally {
    _toastInFlight = false;
  }
}

function renderNotificationList() {
  var list = document.getElementById('notif-list');
  var count = document.getElementById('notif-count');
  if (!list) return;
  if (count) count.textContent = notificationHistory.length;
  if (notificationHistory.length === 0) {
    list.innerHTML = '<div class="notif-empty">暂无通知</div>';
    return;
  }
  list.innerHTML = notificationHistory.map(function(n) {
    var cls = n.type === 'buy' ? 'buy' : n.type === 'sell' ? 'sell' : n.type === 'warn' ? 'warn' : 'signal';
    return '<div class="notif-item ' + cls + '"><div class="notif-time">' + escapeHtml(n.time) + '</div><div class="notif-text"><strong>' + escapeHtml(n.title) + '</strong><br>' + escapeHtml(n.body) + '</div></div>';
  }).join('');
}

function updateNotifBadge() {
  var badge = document.getElementById('notif-badge');
  if (!badge) return;
  var unread = notificationHistory.length;
  if (unread > 0) {
    badge.style.display = 'flex';
    badge.textContent = unread > 99 ? '99+' : unread;
  } else {
    badge.style.display = 'none';
  }
}

function toggleNotificationCenter() {
  var nc = document.getElementById('notification-center');
  if (!nc) return;
  nc.classList.toggle('open');
  if (nc.classList.contains('open')) {
    renderNotificationList();
    // 清除未读数
    var badge = document.getElementById('notif-badge');
    if (badge) badge.style.display = 'none';
  }
}

// ===== 粒子特效 =====
function spawnSparkles(type) {
  var colors = type === 'buy' ? [dirColor('up'),'#06b6d4','#3b82f6'] :
               type === 'sell' ? [dirColor('down'),'#f59e0b','#ec4899'] :
               ['#3b82f6','#a855f7','#06b6d4'];
  var count = type === 'buy' ? 12 : type === 'sell' ? 8 : 6;
  var cx = window.innerWidth / 2;
  var cy = window.innerHeight / 2;
  for (var i = 0; i < count; i++) {
    var p = document.createElement('div');
    p.className = 'sparkle-particle';
    p.style.left = cx + 'px';
    p.style.top = cy + 'px';
    p.style.background = colors[Math.floor(Math.random() * colors.length)];
    p.style.setProperty('--sx', (Math.random() - 0.5) * 200 + 'px');
    p.style.setProperty('--sy', (Math.random() - 0.5) * 200 + 'px');
    p.style.animationDuration = (0.5 + Math.random() * 0.8) + 's';
    document.body.appendChild(p);
    setTimeout(function() { if (p.parentNode) p.parentNode.removeChild(p); }, 1000);
  }
}

// ===== 命令面板 (Ctrl+K) =====
var cmdActiveIdx = -1;
var cmdResults = [];

function openCmdPalette() {
  var overlay = document.getElementById('cmd-palette-overlay');
  var input = document.getElementById('cmd-input');
  if (!overlay || !input) return;
  overlay.classList.add('open');
  input.value = '';
  cmdActiveIdx = -1;
  cmdResults = [];
  updateCmdResults('');
  setTimeout(function() { input.focus(); }, 100);
}

function closeCmdPalette() {
  var overlay = document.getElementById('cmd-palette-overlay');
  if (overlay) overlay.classList.remove('open');
  cmdActiveIdx = -1;
  cmdResults = [];
}

function updateCmdResults(query) {
  var container = document.getElementById('cmd-results');
  if (!container) return;
  cmdResults = [];

  if (!query || query.trim() === '') {
    // 默认显示板块跳转
    cmdResults = SECTORS.map(function(s) { return { type: 'sector', icon: s.icon, label: '切换到 ' + s.name, key: s.key, kbd: '板块' }; });
    cmdResults.push({ type: 'action', icon: '🔍', label: '条件选股', action: 'screen', kbd: '' });
    cmdResults.push({ type: 'action', icon: '🤖', label: '智能选股扫描', action: 'scan', kbd: '' });
    cmdResults.push({ type: 'action', icon: '📊', label: '选股信号排行', action: 'signaltable', kbd: 'T' });
    cmdResults.push({ type: 'action', icon: '⚙️', label: '系统设置', action: 'settings', kbd: 'S' });
    cmdResults.push({ type: 'action', icon: '🔔', label: '通知中心', action: 'notifications', kbd: 'N' });
    cmdResults.push({ type: 'action', icon: '屏️', label: '焦点模式', action: 'focus', kbd: 'F' });
  } else {
    var q = query.toLowerCase();
    // 搜索板块
    var sectorMatches = SECTORS.filter(function(s) { return s.name.toLowerCase().indexOf(q) >= 0 || s.key.toLowerCase().indexOf(q) >= 0; });
    cmdResults = sectorMatches.map(function(s) { return { type: 'sector', icon: s.icon, label: '切换到 ' + s.name, key: s.key, kbd: '板块' }; });
    // 搜索当前板块股票
    var stockMatches = (window.appState.stocks || []).filter(function(s) {
      return (s.name || '').toLowerCase().indexOf(q) >= 0 || (s.code || '').toLowerCase().indexOf(q) >= 0;
    }).slice(0, 5);
    stockMatches.forEach(function(s) {
      cmdResults.push({ type: 'stock', icon: '📈', label: s.name + ' (' + s.code + ')', code: s.code, name: s.name, kbd: '' });
    });
    // 搜索行动
    var actionLabels = { '扫描': 'scan', '选股': 'screen', '设置': 'settings', '通知': 'notifications', '刷新': 'refresh', '焦点': 'focus', '🔔': 'signaltable' };
    Object.keys(actionLabels).forEach(function(k) {
      if (k.indexOf(q) >= 0) {
        cmdResults.push({ type: 'action', icon: '⚡', label: k, action: actionLabels[k], kbd: '' });
      }
    });
  }

  cmdActiveIdx = cmdResults.length > 0 ? 0 : -1;
  container.innerHTML = cmdResults.length === 0 ?
    '<div class="cmd-empty">没有匹配结果</div>' :
    cmdResults.map(function(r, i) {
      var isActive = i === cmdActiveIdx;
      return '<div class="cmd-item' + (isActive ? ' active' : '') + '" data-idx="' + i + '" onclick="executeCmdResult(' + i + ')">' +
        '<span class="cmd-item-icon">' + r.icon + '</span>' +
        '<span class="cmd-item-label">' + escapeHtml(r.label) + '</span>' +
        (r.kbd ? '<span class="cmd-item-kbd">' + r.kbd + '</span>' : '') +
      '</div>';
    }).join('');
}

function executeCmdResult(idx) {
  if (idx < 0 || idx >= cmdResults.length) return;
  var r = cmdResults[idx];
  if (r.type === 'sector') {
    switchSector(r.key);
  } else if (r.type === 'stock') {
    selectStock(r.code, r.name);
  } else if (r.type === 'action') {
    if (r.action === 'screen') toggleScreenPanel();
    else if (r.action === 'scan') showScanPanel();
    else if (r.action === 'signaltable') toggleSignalTable();
    else if (r.action === 'settings') toggleSettingsPanel();
    else if (r.action === 'notifications') toggleNotificationCenter();
    else if (r.action === 'focus') toggleFocusMode();
    else if (r.action === 'refresh') refreshAll();
  }
  closeCmdPalette();
}

// 命令面板输入处理
document.addEventListener('keydown', function(e) {
  var overlay = document.getElementById('cmd-palette-overlay');
  var isOpen = overlay && overlay.classList.contains('open');

  // 全局快捷键
  if (e.key === 'k' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    if (isOpen) closeCmdPalette();
    else openCmdPalette();
    return;
  }

  if (e.key === 'Escape' && isOpen) {
    e.preventDefault();
    closeCmdPalette();
    return;
  }

  if (isOpen) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      cmdActiveIdx = Math.min(cmdActiveIdx + 1, cmdResults.length - 1);
      updateCmdActiveClass();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      cmdActiveIdx = Math.max(cmdActiveIdx - 1, 0);
      updateCmdActiveClass();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      executeCmdResult(cmdActiveIdx);
    }
  }
});

function updateCmdActiveClass() {
  var items = document.querySelectorAll('#cmd-results .cmd-item');
  items.forEach(function(el, i) { el.classList.toggle('active', i === cmdActiveIdx); });
  if (cmdActiveIdx >= 0 && items[cmdActiveIdx]) {
    items[cmdActiveIdx].scrollIntoView({ block: 'nearest' });
  }
}

// 命令面板输入事件
document.addEventListener('input', function(e) {
  if (e.target.id === 'cmd-input') {
    cmdActiveIdx = -1;
    updateCmdResults(e.target.value.trim());
  }
});

// ===== 焦点模式 =====
function toggleFocusMode() {
  var body = document.body;
  body.classList.toggle('focus-mode');
  if (body.classList.contains('focus-mode')) {
    enhancedToast('屏️ 焦点模式', '双击K线图或按 Esc 退出', 'signal');
    setTimeout(function() {
      if (klineChart && !klineChart.isDisposed()) klineChart.resize();
    }, 400);
  } else {
    setTimeout(function() {
      resizeKline();
      if (window.appState.radarChart && !window.appState.radarChart.isDisposed()) {
        window.appState.radarChart.resize();
      }
    }, 400);
  }
}

// ===== 跑马灯行情 =====
var tickerData = [];
var tickerTimer = null;

function initTickerTape() {
  // 初始种子数据
  tickerData = [
    { code: '000001', name: '平安银行', price: 10.50, chg: 0.8 },
    { code: '600519', name: '贵州茅台', price: 1680.00, chg: -0.3 },
    { code: '000858', name: '五粮液', price: 152.30, chg: 1.2 },
    { code: '300750', name: '宁德时代', price: 220.50, chg: 2.1 },
    { code: '002594', name: '比亚迪', price: 280.00, chg: -1.5 },
    { code: '688981', name: '中芯国际', price: 55.80, chg: 3.2 },
    { code: '300059', name: '东方财富', price: 18.50, chg: 0.5 },
    { code: '002475', name: '立讯精密', price: 35.20, chg: -0.8 },
    { code: '601012', name: '隆基绿能', price: 23.40, chg: 1.8 },
    { code: '600036', name: '招商银行', price: 38.60, chg: 0.2 },
  ];
  renderTickerTape();
  updateTickerData();
  // v17：北向资金条此前**定义了 updateNorthFlow 却从未调用**，
  // 导致其 404 断链长期不可见。现接入首屏与定时刷新。
  updateNorthFlow();
  tickerTimer = setInterval(function () {
    updateTickerData();
    updateNorthFlow();
  }, 30000);
}

function renderTickerTape() {
  var track = document.getElementById('ticker-track');
  if (!track || tickerData.length === 0) return;
  // 复制两份实现无缝滚动
  var items = tickerData.concat(tickerData);
  var dur = Math.max(20, tickerData.length * 3);
  track.style.setProperty('--ticker-dur', dur + 's');
  track.innerHTML = items.map(function(d) {
    var cls = d.chg >= 0 ? 'up' : 'dn';
    var sign = d.chg >= 0 ? '+' : '';
    return '<div class="ticker-item"><span class="ticker-code">' + escapeHtml(d.code) + '</span><span class="ticker-name">' + escapeHtml(d.name) + '</span><span class="ticker-price ' + cls + '">' + safeFixed(d.price) + '</span><span class="ticker-chg chg-' + cls + '">' + sign + safeFixed(d.chg) + '%</span></div>';
  }).join('');
  // 轨道被整体重写后恢复北向条目（否则每次行情刷新都会把它覆盖掉）
  applyNorthTicker();
}

function updateTickerData() {
  if (window._settings && window._settings.ticker === false) return;
  // 尝试用实时数据更新
  var stocks = window.appState.stocks || [];
  if (stocks.length >= 5) {
    tickerData = stocks.slice(0, 15).map(function(s) {
      return { code: s.code, name: s.name, price: s.price, chg: s.chg };
    });
    renderTickerTape();
  }
}

// ===== 设置面板 =====
window._settings = safeJsonParse(localStorage.getItem('dapeng_settings'), {
  autorefresh: true,
  sound: true,
  ticker: true,
  particles: true,
  aurora: true
}) || { // v7: 防 localStorage 存了 "null" 导致 init 中断
  autorefresh: true,
  sound: true,
  ticker: true,
  particles: true,
  aurora: true
};

function initSettings() {
  // 应用初始设置
  applyAllSettings();
}

// ================================================================
//  主题选择器按钮绑定
// ================================================================
function initThemeButtons() {
  document.querySelectorAll('.theme-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      setColorTheme(this.dataset.theme);
      showToast('主题 已切换至 ' + COLOR_THEMES[this.dataset.theme] + ' 主题');
    });
  });
}

// ================================================================
//  信号历史管理
// ================================================================
function getSignalHistory() {
  return safeJsonParse(localStorage.getItem('dapeng_signal_history'), []);
}

function addSignalToHistory(signal) {
  var history = getSignalHistory();
  history.unshift({
    id: Date.now(),
    time: new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    name: signal.name || '',
    code: signal.code || '',
    action: signal.action || 'buy',
    signal: signal.signal || '',
    score: signal.score || ''
  });
  if (history.length > 100) history.length = 100;
  try { localStorage.setItem('dapeng_signal_history', JSON.stringify(history)); } catch(e) {}
}

function renderSignalHistory(filter) {
  filter = filter || 'all';
  var list = document.getElementById('signal-history-list');
  if (!list) return;

  // 更新过滤按钮状态
  document.querySelectorAll('.sh-filter-btn').forEach(function(btn) {
    btn.classList.toggle('active', btn.dataset.filter === filter);
  });

  var history = getSignalHistory();
  var filtered = filter === 'all' ? history : history.filter(function(s) { return s.action === filter; });

  if (filtered.length === 0) {
    list.innerHTML = '<div class="sh-empty"><div class="sh-empty-icon">—</div><div class="sh-empty-text">暂无信号记录</div></div>';
    return;
  }

  list.innerHTML = filtered.map(function(s) {
    var cls = s.action === 'buy' ? 'sh-buy' : 'sh-sell';
    var actionText = s.action === 'buy' ? '买入' : '卖出';
    return '<div class="sh-item ' + cls + '" data-code="' + (s.code || '') + '">' +
      '<div class="sh-time">' + s.time + ' · ' + actionText + '</div>' +
      '<div class="sh-stock">' + (s.name || '') + ' <span class="sh-code">' + (s.code || '') + '</span></div>' +
      '<div class="sh-detail">' + (s.signal || '') + '</div>' +
      (s.score ? '<div class="sh-score">得分: ' + s.score + '</div>' : '') +
      '</div>';
  }).join('');

  // 点击跳转到股票
  list.querySelectorAll('.sh-item[data-code]').forEach(function(el) {
    el.addEventListener('click', function() {
      var code = this.dataset.code;
      if (code) selectStock(code);
      toggleSignalHistory();
    });
  });
}

function toggleSignalHistory() {
  var panel = document.getElementById('signal-history-panel');
  if (!panel) return;
  panel.classList.toggle('open');
  if (panel.classList.contains('open')) renderSignalHistory();
}

function clearSignalHistory() {
  localStorage.removeItem('dapeng_signal_history');
  renderSignalHistory();
  showToast('已清空信号历史');
}

function initSignalHistoryPanel() {
  // ESC 关闭面板
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      var panel = document.getElementById('signal-history-panel');
      if (panel) panel.classList.remove('open');
    }
  });
}

// ================================================================
//  板块热力图
// ================================================================
var heatmapChart = null;

function renderHeatmap() {
  var dom = document.getElementById('heatmap-chart');
  if (!dom) return;
  if (dom.offsetWidth < 10) return;

  if (!heatmapChart) {
    heatmapChart = echarts.init(dom, null, { renderer: 'canvas' });
  }

  var sectorData = SECTORS.map(function(sector) {
    var stocks = (window.allStocks || window.appState.stocks).filter(function(s) { return s.sector === sector.key; });
    if (stocks.length === 0) return { name: sector.name, value: 0.1, avgChg: 0, icon: sector.icon };

    var avgChg = stocks.reduce(function(sum, s) { return sum + (s.chg || s.changePercent || 0); }, 0) / stocks.length;
    return {
      name: sector.name,
      value: Math.max(Math.abs(avgChg), 0.1),
      avgChg: avgChg,
      icon: sector.icon
    };
  }).filter(function(d) { return d.value > 0; });

  var isLight = document.body.classList.contains('light-theme');
  var textColor = isLight ? '#475569' : '#94a3b8';

  heatmapChart.setOption({
    series: [{
      type: 'treemap',
      data: sectorData.map(function(d) {
        var intensity = Math.min(Math.abs(d.avgChg) * 20, 0.7);
        return {
          name: d.name + (d.avgChg >= 0 ? ' +' : ' ') + d.avgChg.toFixed(2) + '%',
          value: d.value,
          sectorKey: SECTORS.find(function(s) { return s.name === d.name; }) ? SECTORS.find(function(s) { return s.name === d.name; }).key : '',
          itemStyle: {
            color: d.avgChg >= 0
              ? dirRgba('up', intensity)      // 涨=红
              : dirRgba('down', intensity),   // 跌=绿
            borderColor: 'rgba(255,255,255,0.05)',
            borderWidth: 1
          }
        };
      }),
      label: {
        show: true,
        fontSize: 9,
        color: '#fff',
        formatter: '{b}'
      },
      roam: false,
      nodeClick: false,
      breadcrumb: { show: false },
      animation: true,
      animationDuration: 600
    }]
  });

  heatmapChart.off('click');
  heatmapChart.on('click', function(params) {
    var sector = SECTORS.find(function(s) { return s.name === params.name.split(' ')[0]; });
    if (sector) switchSector(sector.key);
  });
}

// ================================================================
//  主题管理（白天/黑夜模式 + 多配色主题）
// ================================================================
const COLOR_THEMES = {
  aurora: '极光暗夜',
  ocean: '海洋蓝',
  forest: '绿色交易',
  sunset: '暖阳',
};

function initTheme() {
  // 恢复白天/黑夜模式
  var saved = localStorage.getItem('dapeng_theme');
  if (saved === 'light') {
    document.body.classList.add('light-theme');
  }
  syncThemeToggleUI();
  updateMetaThemeColor();
  updateThemeIcon();

  // 恢复颜色主题
  initColorTheme();

  setTimeout(function() {
    applyThemeToCharts();
  }, 1500);
}

function initColorTheme() {
  var saved = localStorage.getItem('dapeng_color_theme') || 'aurora';
  setColorTheme(saved);
}

function setColorTheme(theme) {
  if (!COLOR_THEMES[theme]) theme = 'aurora';
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('dapeng_color_theme', theme);

  // 更新主题按钮状态
  document.querySelectorAll('.theme-btn').forEach(function(btn) {
    btn.classList.toggle('active', btn.dataset.theme === theme);
  });

  // 更新主题名称显示
  var display = document.getElementById('theme-name-display');
  if (display) display.textContent = '当前: ' + COLOR_THEMES[theme];

  // 重新渲染图表以匹配新主题
  setTimeout(function() {
    applyThemeToCharts();
    if (window.appState.activeCode) {
      var cacheKey = window.appState.activeCode + ':' + window.appState.period;
      var cached = window.appState.klineCache[cacheKey];
      if (cached) renderKline(cached, window.appState.activeName);
    }
    if (window.appState.radarChart) {
      var opt = window.appState.radarChart.getOption();
      window.appState.radarChart.setOption(opt, { notMerge: true });
    }
    if (typeof renderHeatmap === 'function') renderHeatmap();
  }, 100);
}

function updateThemeIcon() {
  // 由 CSS 控制显示，JS 仅需处理存根
  console.log("Theme toggled, current isLight:", document.body.classList.contains('light-theme'));
}

// 修正板块分析的数据源，处理搜素或筛选时的全局数据统计
function toggleTheme() {
  var isLight = document.body.classList.toggle('light-theme');
  localStorage.setItem('dapeng_theme', isLight ? 'light' : 'dark');
  syncThemeToggleUI();
  updateMetaThemeColor();
  updateThemeIcon();
  applyThemeToCharts();
  window.dispatchEvent(new Event('themechange'));
  // 触发一次图表重绘以防万一
  setTimeout(function() {
    if (typeof klineChart !== 'undefined' && klineChart) klineChart.resize();
    if (typeof renderHeatmap === 'function') renderHeatmap();
  }, 100);
}

function syncThemeToggleUI() {
  var toggle = document.getElementById('toggle-theme');
  var isLight = document.body.classList.contains('light-theme');
  if (toggle) toggle.classList.toggle('on', isLight);
}

function updateMetaThemeColor() {
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.content = document.body.classList.contains('light-theme') ? '#f8fafc' : '#020510';
  }
}

/**
 * 更新 ECharts 图表以匹配当前主题
 */
function applyThemeToCharts() {
  var isLight = document.body.classList.contains('light-theme');
  var textColor = isLight ? '#475569' : '#94a3b8';
  var mutedColor = isLight ? '#94a3b8' : '#3d5278';
  var splitColor = isLight ? 'rgba(148,163,184,0.12)' : 'rgba(59,130,246,0.08)';
  var splitArea = isLight ? ['rgba(241,245,249,0.6)', 'rgba(248,250,252,0.4)'] : ['rgba(59,130,246,0.02)', 'rgba(59,130,246,0.04)'];
  var axisColor = isLight ? 'rgba(148,163,184,0.15)' : 'rgba(59,130,246,0.1)';

  // 更新雷达图
  var radarChart = window.appState.radarChart;
  if (radarChart && !radarChart.isDisposed()) {
    var opt = radarChart.getOption();
    if (opt && opt.radar && opt.radar[0]) {
      opt.radar[0].axisName = opt.radar[0].axisName || { color: textColor, fontSize: 9 };
      opt.radar[0].axisName.color = textColor;
      opt.radar[0].splitLine = { lineStyle: { color: splitColor } };
      opt.radar[0].splitArea = { areaStyle: { color: splitArea } };
      opt.radar[0].axisLine = { lineStyle: { color: axisColor } };
    }
    radarChart.setOption(opt, { notMerge: false });
  }

  // 更新K线图
  if (klineChart && !klineChart.isDisposed()) {
    var kOpt = klineChart.getOption();
    if (kOpt) {
      if (kOpt.xAxis) {
        kOpt.xAxis.forEach(function(axis) {
          if (axis && axis.axisLabel) {
            axis.axisLabel.color = mutedColor;
          }
          if (axis && axis.splitLine) {
            axis.splitLine = { lineStyle: { color: splitColor } };
          }
        });
      }
      if (kOpt.yAxis) {
        kOpt.yAxis.forEach(function(axis) {
          if (axis && axis.axisLabel) {
            axis.axisLabel.color = mutedColor;
          }
          if (axis && axis.splitLine) {
            axis.splitLine = { lineStyle: { color: splitColor } };
          }
        });
      }
      if (kOpt.legend && kOpt.legend[0]) {
        kOpt.legend[0].textStyle = { color: mutedColor, fontSize: 10 };
      }
      klineChart.setOption(kOpt, { notMerge: false });
    }
  }
}

function toggleSetting(key) {
  window._settings[key] = !window._settings[key];
  localStorage.setItem('dapeng_settings', JSON.stringify(window._settings));
  // 更新 UI
  var toggle = document.getElementById('toggle-' + key);
  if (toggle) toggle.classList.toggle('on', window._settings[key]);
  applySetting(key);
}

function applySetting(key) {
  var v = window._settings[key];
  if (key === 'sound') SoundEngine.setEnabled(v);
  else if (key === 'ticker') {
    var tape = document.getElementById('ticker-tape');
    if (tape) tape.style.display = v ? '' : 'none';
  } else if (key === 'aurora') {
    var aurora = document.getElementById('aurora-bg');
    if (aurora) aurora.style.display = v ? '' : 'none';
    var gridOverlay = document.body.style;
    gridOverlay.backgroundImage = v ? '' : 'none';
  } else if (key === 'autorefresh') {
    if (v) startSmartRefresh();
    else {
      if (window.appState.refreshTimer) {
        clearInterval(window.appState.refreshTimer);
        window.appState.refreshTimer = null;
      }
    }
  }
}

function applyAllSettings() {
  var s = window._settings || {}; // v7: 防空
  Object.keys(s).forEach(function(k) {
    var toggle = document.getElementById('toggle-' + k);
    if (toggle) toggle.classList.toggle('on', s[k]);
    applySetting(k);
  });
}

function toggleSettingsPanel() {
  var panel = document.getElementById('settings-panel');
  if (!panel) return;
  panel.classList.toggle('open');
  if (panel.classList.contains('open')) {
    // 同步开关状态
    Object.keys(window._settings || {}).forEach(function(k) {
      var toggle = document.getElementById('toggle-' + k);
      if (toggle) toggle.classList.toggle('on', window._settings[k]);
    });
  }
}

// ===== 面板拖拽 =====
function initPanelResizer() {
  var resizer = document.getElementById('panel-resizer');
  var leftPanel = document.getElementById('left-panel');
  if (!resizer || !leftPanel) return;

  var startX = 0, startWidth = 0;

  resizer.addEventListener('mousedown', function(e) {
    e.preventDefault();
    startX = e.clientX;
    startWidth = leftPanel.offsetWidth;
    resizer.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  });

  document.addEventListener('mousemove', function(e) {
    if (!resizer.classList.contains('dragging')) return;
    var diff = e.clientX - startX;
    var newWidth = Math.max(200, Math.min(500, startWidth + diff));
    leftPanel.style.width = newWidth + 'px';
    setTimeout(function() {
      if (klineChart && !klineChart.isDisposed()) klineChart.resize();
      if (window.appState.radarChart && !window.appState.radarChart.isDisposed()) window.appState.radarChart.resize();
    }, 50);
  });

  document.addEventListener('mouseup', function() {
    if (resizer.classList.contains('dragging')) {
      resizer.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      localStorage.setItem('dapeng_panel_width', leftPanel.style.width);
    }
  });

  // 恢复上次宽度
  var savedWidth = localStorage.getItem('dapeng_panel_width');
  if (savedWidth) leftPanel.style.width = savedWidth;
}

// ===== 数据新鲜度 =====
function updateDataTimestamp(type) {
  var el = document.getElementById('data-ts');
  if (!el) return;
  var now = new Date();
  var ts = now.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  el.innerHTML = '<span class="refresh-timer-dot"></span>' + ts;
  el.className = 'data-timestamp';
}

// ===== 重写 showToast 接入增强通知 =====
// 注意：_origShowToast 已在 enhancedToast 定义之前捕获（见上方），此处不再重复捕获，
// 否则会覆盖引用；接线改为「原始弹窗」与「enhancedToast」各走一次，避免重复弹窗/记重复历史。
showToast = function(title, body) {
  // 只对重要信号发送增强通知（由 enhancedToast 统一负责弹窗 + 历史 + 音效 + 粒子，各一次）
  if (title.indexOf('买入') >= 0 || title.indexOf('预警') >= 0) {
    enhancedToast(title, body, 'buy');
  } else if (title.indexOf('涨') >= 0 && body.indexOf('+') >= 0) {
    enhancedToast(title, body, 'signal');
  } else {
    // 静默：只弹一次原始 toast + 记一条历史，不再回调 enhancedToast（无成环）
    _origShowToast(title, body);
    _toastRecord(title, body, 'signal');
  }
};

// ===== 增强键盘快捷键 =====
var _origKeydownHandler = null; // 扩展原有键盘处理
// 重写键盘事件在原有基础上添加新快捷键
(function enhanceKeyboardShortcuts() {
  var origHandler = document.onkeydown;
  document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;
    if (document.getElementById('cmd-palette-overlay').classList.contains('open')) return;

    // F - 焦点模式
    if (e.key === 'f' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      toggleFocusMode();
      return;
    }
    // N - 通知中心
    if (e.key === 'n' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      toggleNotificationCenter();
      return;
    }
    // S - 设置面板
    if (e.key === 's' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      toggleSettingsPanel();
      return;
    }
    // T - 信号排行
    if (e.key === 't' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      toggleSignalTable();
      return;
    }
    // ? - 快捷键帮助
    if (e.key === '?') {
      e.preventDefault();
      openShortcuts();
      return;
    }
    // Ctrl+F - 搜索
    if (e.key === 'f' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      var searchInput = document.getElementById('search-input');
      if (searchInput) searchInput.focus();
      return;
    }
    // Escape - 关闭各种面板/退出焦点模式
    if (e.key === 'Escape') {
      if (document.body.classList.contains('focus-mode')) {
        e.preventDefault();
        toggleFocusMode();
        return;
      }
      if (document.getElementById('notification-center').classList.contains('open')) {
        e.preventDefault();
        toggleNotificationCenter();
        return;
      }
      if (document.getElementById('settings-panel').classList.contains('open')) {
        e.preventDefault();
        toggleSettingsPanel();
        return;
      }
      if (document.getElementById('shortcuts-overlay').classList.contains('open')) {
        e.preventDefault();
        closeShortcuts();
        return;
      }
    }
  });
})();

// ===== 快捷键帮助弹窗 =====
function openShortcuts() {
  var overlay = document.getElementById('shortcuts-overlay');
  if (overlay) overlay.classList.add('open');
}
function closeShortcuts() {
  var overlay = document.getElementById('shortcuts-overlay');
  if (overlay) overlay.classList.remove('open');
}

// ===== K线双击进入焦点模式 =====
if (typeof initChart === 'function') {
  var _origInitChart = initChart;
  initChart = function() {
    _origInitChart();
    if (klineChart) {
      klineChart.on('dblclick', function() {
        toggleFocusMode();
      });
    }
  };
}

// ===== 监听 quote 数据更新跑马灯和时间戳 =====
var _origLoadQuotes = loadQuotes;
loadQuotes = async function() {
  await _origLoadQuotes();
  updateTickerData();
  updateDataTimestamp();
};

// ===== logo 双击打开命令面板 =====
document.addEventListener('DOMContentLoaded', function() {
  var logo = document.querySelector('.logo');
  if (logo) {
    logo.style.cursor = 'pointer';
    logo.addEventListener('click', function(e) {
      if (e.detail === 2) openCmdPalette();
    });
  }
});

// 时钟
setInterval(updateClock, 1000);
updateClock();

// 交易状态指示器（每30秒更新一次）
updateTradingStatus();
setInterval(updateTradingStatus, 30000);

/**
 * 更新交易状态指示器
 */
function updateTradingStatus() {
  const el = document.getElementById('trading-status');
  const labelEl = document.getElementById('ts-label');
  if (!el || !labelEl) return;

  const now = new Date();
  const day = now.getDay();
  const h = now.getHours();
  const m = now.getMinutes();
  const timeVal = h * 100 + m;

  let status = 'closed';
  let label = '已收盘';

  if (day === 0 || day === 6) {
    status = 'closed';
    label = '休市';
  } else if (timeVal >= 930 && timeVal <= 1130) {
    status = 'trading';
    label = '交易中';
  } else if (timeVal > 1130 && timeVal < 1300) {
    status = 'lunch';
    label = '午休';
  } else if (timeVal >= 1300 && timeVal <= 1500) {
    status = 'trading';
    label = '交易中';
  } else if (timeVal >= 915 && timeVal < 930) {
    status = 'pre';
    label = '竞价';
  } else if (timeVal < 915) {
    status = 'pre';
    label = '盘前';
  } else {
    status = 'closed';
    label = '已收盘';
  }

  el.className = 'trading-status ' + status;
  labelEl.textContent = label;
}

// 窗口 resize
// 统一尺寸同步：容器因响应式布局、侧栏拖拽或字体加载变化时，重绘图表。
function syncDashboardCharts() {
  resizeKline();
  var chart = window.appState && window.appState.radarChart;
  if (chart && !chart.isDisposed()) chart.resize();
  if (typeof window.resizeMarketDashboard === 'function') window.resizeMarketDashboard();
}

window.addEventListener('resize', syncDashboardCharts);

if (typeof ResizeObserver !== 'undefined') {
  const chartHost = document.getElementById('kline-card');
  if (chartHost) {
    new ResizeObserver(() => requestAnimationFrame(syncDashboardCharts)).observe(chartHost);
  }
}


// ================================================================
//  股票对比功能
// ================================================================
window.appState.compareStocks = [];

function addToCompare(code) {
  const stock = window.appState.stockMap[code];
  if (!stock || window.appState.compareStocks.length >= 5) return;
  if (window.appState.compareStocks.find(s => s.code === code)) return;
  
  window.appState.compareStocks.push({
    code: stock.code,
    name: stock.name,
    price: stock.price,
    chg: stock.chg,
    high: stock.high,
    low: stock.low,
    yclose: stock.yclose,
    score: stock.score || 0,
    volume: stock.volume || 0,
    amount: stock.amount || 0,
    turnover: stock.turnover || 0,
  });
  
  renderComparePanel();
}

function removeFromCompare(code) {
  window.appState.compareStocks = window.appState.compareStocks.filter(s => s.code !== code);
  renderComparePanel();
}

function clearCompare() {
  window.appState.compareStocks = [];
  renderComparePanel();
}

function toggleComparePanel() {
  const panel = document.getElementById('compare-panel');
  if (!panel) return;
  panel.classList.toggle('open');
  if (panel.classList.contains('open')) {
    renderComparePanel();
  }
}

function renderComparePanel() {
  const stocksEl = document.getElementById('compare-stocks');
  const emptyEl = document.getElementById('compare-empty');
  const tableEl = document.getElementById('compare-table');
  const bodyEl = document.getElementById('compare-body');
  
  if (!stocksEl || !emptyEl || !tableEl || !bodyEl) return;
  
  stocksEl.innerHTML = window.appState.compareStocks.map(s => `
    <div class="compare-stock-tag">
      <span>${escapeHtml(s.name)}</span>
      <span class="remove-btn" onclick="removeFromCompare('${s.code}')">&#x2715;</span>
    </div>
  `).join('');
  
  if (window.appState.compareStocks.length === 0) {
    emptyEl.style.display = 'flex';
    tableEl.style.display = 'none';
    return;
  }
  
  emptyEl.style.display = 'none';
  tableEl.style.display = 'block';
  
  const headers = document.querySelector('#compare-table thead tr');
  headers.innerHTML = '<th>指标</th>' + 
    window.appState.compareStocks.map(s => `<th>${escapeHtml(s.name)}</th>`).join('');
  
  const rows = [
    { label: '现价', key: 'price', format: 'price' },
    { label: '涨跌幅', key: 'chg', format: 'percent' },
    { label: '最高价', key: 'high', format: 'price' },
    { label: '最低价', key: 'low', format: 'price' },
    { label: '昨收', key: 'yclose', format: 'price' },
    { label: '评分', key: 'score', format: 'score' },
    { label: '成交量', key: 'volume', format: 'volume' },
    { label: '成交额', key: 'amount', format: 'amount' },
    { label: '换手率', key: 'turnover', format: 'percent' },
  ];
  
  bodyEl.innerHTML = rows.map(row => {
    const cells = window.appState.compareStocks.map(s => {
      const val = s[row.key];
      let formatted = '--';
      let cls = '';
      
      if (val !== undefined && val !== null && val > 0) {
        switch (row.format) {
          case 'price':
            formatted = val.toFixed(2);
            cls = row.key === 'chg' ? (val >= 0 ? 'chg-up' : 'chg-dn') : '';
            break;
          case 'percent':
            formatted = (row.key === 'chg' ? (val >= 0 ? '+' : '') : '') + val.toFixed(2) + '%';
            cls = row.key === 'chg' ? (val >= 0 ? 'chg-up' : 'chg-dn') : '';
            break;
          case 'score':
            formatted = val;
            cls = val >= 70 ? 'chg-up' : val >= 50 ? '' : 'chg-dn';
            break;
          case 'volume':
            formatted = val >= 1e8 ? (val / 1e8).toFixed(2) + '亿' : val >= 1e4 ? (val / 1e4).toFixed(0) + '万' : val;
            break;
          case 'amount':
            formatted = val >= 1e12 ? (val / 1e12).toFixed(2) + '万亿' : val >= 1e8 ? (val / 1e8).toFixed(2) + '亿' : val >= 1e4 ? (val / 1e4).toFixed(0) + '万' : val;
            break;
          default:
            formatted = val;
        }
      }
      return `<td${cls ? ` class="${cls}"` : ''}>${escapeHtml(formatted)}</td>`;
    }).join('');
    
    return `<tr><td>${row.label}</td>${cells}</tr>`;
  }).join('');
}

function exportCompareData() {
  if (window.appState.compareStocks.length === 0) {
    showToast('提示', '请先添加股票到对比列表');
    return;
  }
  
  const headers = ['代码', '名称', '现价', '涨跌幅', '最高价', '最低价', '昨收', '评分', '成交量', '成交额', '换手率'];
  const rows = window.appState.compareStocks.map(s => [
    s.code,
    s.name,
    s.price?.toFixed(2) || '--',
    (s.chg !== undefined ? (s.chg >= 0 ? '+' : '') + s.chg.toFixed(2) + '%' : '--'),
    s.high?.toFixed(2) || '--',
    s.low?.toFixed(2) || '--',
    s.yclose?.toFixed(2) || '--',
    s.score || '--',
    s.volume >= 1e8 ? (s.volume / 1e8).toFixed(2) + '亿' : s.volume >= 1e4 ? (s.volume / 1e4).toFixed(0) + '万' : s.volume,
    s.amount >= 1e12 ? (s.amount / 1e12).toFixed(2) + '万亿' : s.amount >= 1e8 ? (s.amount / 1e8).toFixed(2) + '亿' : s.amount >= 1e4 ? (s.amount / 1e4).toFixed(0) + '万' : s.amount,
    s.turnover ? s.turnover.toFixed(2) + '%' : '--',
  ]);
  
  const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `股票对比_${new Date().toLocaleDateString('zh-CN').replace(/\//g, '-')}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  
  showToast('OK 导出成功', '对比数据已保存为CSV文件');
}

// ================================================================
//  价格预警功能
// ================================================================
window.appState.alerts = safeJsonParse(localStorage.getItem('dapeng_alerts'), []) || [];

function addAlert() {
  const code = document.getElementById('alert-code')?.value.trim();
  const name = document.getElementById('alert-name')?.value.trim();
  const type = document.getElementById('alert-type')?.value;
  const price = parseFloat(document.getElementById('alert-price')?.value);
  
  if (!code || !name || isNaN(price)) {
    showToast('! 输入无效', '请填写完整的预警信息');
    return;
  }
  
  const alert = {
    id: Date.now(),
    code,
    name,
    type,
    price,
    triggered: false,
    createdAt: new Date().toLocaleString('zh-CN'),
  };
  
  window.appState.alerts.push(alert);
  localStorage.setItem('dapeng_alerts', JSON.stringify(window.appState.alerts));
  renderAlertList();
  
  document.getElementById('alert-code').value = '';
  document.getElementById('alert-name').value = '';
  document.getElementById('alert-price').value = '';
  
  showToast('OK 预警已添加', `${name} - ${type === 'above' ? '价格高于' : '价格低于'} ${price.toFixed(2)}元时提醒`);
}

function removeAlert(id) {
  window.appState.alerts = window.appState.alerts.filter(a => a.id !== id);
  localStorage.setItem('dapeng_alerts', JSON.stringify(window.appState.alerts));
  renderAlertList();
}

function toggleAlertPanel() {
  const panel = document.getElementById('alert-panel');
  if (!panel) return;
  panel.classList.toggle('open');
  if (panel.classList.contains('open')) {
    renderAlertList();
  }
}

function renderAlertList() {
  const listEl = document.getElementById('alert-list');
  const emptyEl = document.getElementById('alert-empty');
  
  if (!listEl || !emptyEl) return;
  
  if (window.appState.alerts.length === 0) {
    listEl.innerHTML = '';
    emptyEl.style.display = 'flex';
    return;
  }
  
  emptyEl.style.display = 'none';
  listEl.innerHTML = window.appState.alerts.map(alert => `
    <div class="alert-item">
      <div class="alert-item-info">
        <div class="alert-item-name">${escapeHtml(alert.name)} (${escapeHtml(alert.code)})</div>
        <div class="alert-item-desc">${alert.type === 'above' ? '价格高于' : '价格低于'} ${alert.price.toFixed(2)}元</div>
      </div>
      <div class="alert-item-price">${alert.price.toFixed(2)}</div>
      <div class="alert-item-remove" onclick="removeAlert(${alert.id})">&#x2715;</div>
    </div>
  `).join('');
}

function checkAlerts() {
  if (window.appState.alerts.length === 0) return;
  
  for (const alert of window.appState.alerts) {
    const stock = window.appState.stockMap[alert.code];
    if (!stock || stock.price <= 0) continue;
    
    const triggered = alert.type === 'above' 
      ? stock.price >= alert.price 
      : stock.price <= alert.price;
    
    if (triggered && !alert.triggered) {
      alert.triggered = true;
      showToast('🔔 价格预警', `${alert.name} 价格${alert.type === 'above' ? '突破' : '跌破'} ${alert.price.toFixed(2)}元，当前价 ${stock.price.toFixed(2)}元`);
      enhancedToast('能 价格预警', `${alert.name} ${alert.type === 'above' ? '高于' : '低于'} ${alert.price.toFixed(2)}元`, 'signal');
    } else if (!triggered) {
      alert.triggered = false;
    }
  }
}

// ================================================================
//  增强键盘快捷键
// ================================================================
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;
  if (document.getElementById('cmd-palette-overlay').classList.contains('open')) return;
  
  // C - 股票对比
  if (e.key === 'c' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    toggleComparePanel();
    return;
  }
  // W - 价格预警
  if (e.key === 'w' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    toggleAlertPanel();
    return;
  }
  // H - 信号历史
  if (e.key === 'h' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    toggleSignalHistory();
    return;
  }
  // Escape 关闭对比和预警面板
  if (e.key === 'Escape') {
    const comparePanel = document.getElementById('compare-panel');
    const alertPanel = document.getElementById('alert-panel');
    const shPanel = document.getElementById('signal-history-panel');
    if (comparePanel?.classList.contains('open')) {
      e.preventDefault();
      comparePanel.classList.remove('open');
      return;
    }
    if (alertPanel?.classList.contains('open')) {
      e.preventDefault();
      alertPanel.classList.remove('open');
      return;
    }
    if (shPanel && shPanel.classList.contains('open')) {
      e.preventDefault();
      shPanel.classList.remove('open');
      return;
    }
  }
});

// 价格预警由实时轮询调度器在每次完成后统一检查，避免重复包装轮询函数。

// ================================================================
//  板块分析功能（参考 ai1foo.com）
// ================================================================
// ================================================================
//  真实板块数据分析功能
// ================================================================

// ================================================================
//  v5.4: 板块分析面板 — 完全重写（API 驱动 + 多周期 + 排行 + 详情）
// ================================================================

/**
 * 打开/关闭板块分析面板
 * 打开时：display:flex → 初始化 5 日周期 → 加载排行数据 → 渲染表格
 */
function toggleSectorAnalysisPanel() {
  var panel = document.getElementById('sector-analysis-panel');
  if (!panel) return;

  if (panel.style.display === 'flex') {
    closeSectorPanel();
  } else {
    panel.style.display = 'flex';
    window.appState.sectorCurrentPeriod = '5';
    window.appState.sectorSortOrder = 'desc';
    window.appState.sectorSelectedKey = '';
    window.appState.sectorSelectedName = '';

    // 更新周期 Tab active 状态
    document.querySelectorAll('#sector-analysis-panel .sector-period-tab').forEach(function (tab) {
      tab.classList.toggle('active', tab.getAttribute('data-period') === '5');
    });

    // 加载排行数据并渲染
    loadSectorRankingData();

    // v5.0: 并发加载市场数据（板块排行/龙虎榜/市场总览/热门股）
    if (typeof updateSectorAnalysisPanel === 'function') {
      updateSectorAnalysisPanel();
    }
  }
}

/**
 * 关闭板块分析面板 + 清理选中状态
 */
function closeSectorPanel() {
  var panel = document.getElementById('sector-analysis-panel');
  if (panel) panel.style.display = 'none';
  window.appState.sectorSelectedKey = '';
  window.appState.sectorSelectedName = '';
  // 隐藏详情区
  var detail = document.getElementById('sa-detail-section');
  if (detail) detail.style.display = 'none';
}

/**
 * 读取数值：返回首个存在且可解析的值为 number，否则 null（用于区分真实 0 与缺失）
 */
function _sectorNum() {
  for (var i = 0; i < arguments.length; i++) {
    var v = arguments[i];
    if (v != null && v !== '') {
      var n = parseFloat(v);
      return isNaN(n) ? null : n;
    }
  }
  return null;
}

/**
 * 加载板块排行数据 — 调 fetchSectorRankingEnvelope() → 缓存 → 渲染表格
 * 首次加载后缓存，后续周期切换/排序纯前端操作
 */
async function loadSectorRankingData() {
  var tbody = document.getElementById('sa-ranking-tbody');
  if (!tbody) return;

  // 显示骨架屏
  _renderTableSkeleton(tbody, 10);

  // 如果已有缓存，直接渲染（并沿用上次记录的降级状态）
  if (window.appState.sectorRankingCache) {
    if (typeof renderDegradedBanner === 'function') {
      renderDegradedBanner('sector-analysis-panel',
        window.appState.sectorRankingDegraded === true,
        window.appState.sectorRankingSource || '');
    }
    renderSectorRankingTable(window.appState.sectorCurrentPeriod);
    return;
  }

  try {
    var env = await fetchSectorRankingEnvelope();
    var apiData = env ? env.data : null;
    var degraded = !!(env && env.degraded);
    var srcName = (env && env.source) || '';
    window.appState.sectorRankingDegraded = degraded;
    window.appState.sectorRankingSource = srcName;
    if (typeof renderDegradedBanner === 'function') {
      renderDegradedBanner('sector-analysis-panel', degraded, srcName);
    }
    if (!apiData) {
      _renderTableError(tbody, 'loadSectorRankingData()');
      return;
    }

    // API 数据可能是 {sectors:[...]}, {data:[...]} 或直接 [...]
    var sectors = Array.isArray(apiData) ? apiData : (apiData.sectors || apiData.rows || apiData.data || []);
    if (!Array.isArray(sectors) || sectors.length === 0) {
      _renderTableEmpty(tbody, '暂无板块排行数据');
      return;
    }

    // 解析并缓存 — 每行附带英文 key
    // 多周期收益直接取后端真实值，缺失时为 null（绝不回退到 chg*系数 推算）
    var parsed = sectors.map(function (s) {
      var fs = s.field_sources || {};
      var row = {
        name: s.name || s['名称'] || s['板块名称'] || '--',
        code: s.code || s['板块代码'] || '',
        key: resolveSectorKey(s),
        chg: parseFloat(s.chg_pct || s.change_pct || s.chg || s['涨跌幅'] || 0),
        leadStock: s.lead_stock || s.leading_stock || s['领涨股'] || '--',
        amount: parseFloat(s.amount || s['成交额'] || 0),
        upCount: parseInt(s.up_count || s['上涨数'] || 0),
        dnCount: parseInt(s.down_count || s['下跌数'] || 0),
        totalCount: parseInt(s.count || s['股票数'] || 0),
        '5': _sectorNum(s['5日%'], s['5d']),
        '20': _sectorNum(s['20日%'], s['20d']),
        '60': _sectorNum(s['60日%'], s['60d']),
        '120': _sectorNum(s['120日%'], s['120d']),
        '250': _sectorNum(s['250日%'], s['250d']),
        netFlow: _sectorNum(s.net_flow),
        netFlowSource: s.net_flow_source || fs.net_flow || null,
        periodReturnsSource: s.period_returns_source || fs.period_returns || null,
        _raw: s
      };
      return row;
    });

    window.appState.sectorRankingCache = parsed;
    renderSectorRankingTable(window.appState.sectorCurrentPeriod);
  } catch (e) {
    console.error('loadSectorRankingData error:', e);
    _renderTableError(tbody, 'loadSectorRankingData()');
  }
}

/**
 * 渲染板块排行表格 — 从缓存取对应周期列 → 排序 → 渲染到 #sa-ranking-tbody
 * @param {string} period - '5'|'20'|'60'|'120'|'250'
 */
function renderSectorRankingTable(period) {
  var tbody = document.getElementById('sa-ranking-tbody');
  if (!tbody) return;
  var cache = window.appState.sectorRankingCache;
  if (!cache || cache.length === 0) {
    _renderTableEmpty(tbody, '暂无板块排行数据');
    return;
  }

  var colKey = period; // '5', '20', '60', '120', '250'
  var order = window.appState.sectorSortOrder || 'desc';

  // 排序
  var sorted = cache.slice().sort(function (a, b) {
    var va = a[colKey] != null ? a[colKey] : 0;
    var vb = b[colKey] != null ? b[colKey] : 0;
    return order === 'desc' ? vb - va : va - vb;
  });

  var html = '';
  sorted.forEach(function (s, i) {
    var rank = i + 1;
    var rawVal = s[colKey];
    var hasVal = (rawVal !== null && rawVal !== undefined && !isNaN(rawVal));
    var chgVal = hasVal ? rawVal : 0;
    var chgCls = hasVal ? (chgVal >= 0 ? 'sa-chg-up' : 'sa-chg-dn') : '';
    var chgText = hasVal ? ((chgVal >= 0 ? '+' : '') + chgVal.toFixed(2) + '%')
      : '<span style="color:var(--muted)">--</span>';
    var badgeCls = rank === 1 ? 'gold' : (rank === 2 ? 'silver' : (rank === 3 ? 'bronze' : ''));
    var selectedCls = s.key === window.appState.sectorSelectedKey ? ' selected' : '';

    html += '<tr class="sa-rank-row' + selectedCls + '" onclick="selectSector(\'' +
      escapeHtml(s.key) + '\', \'' + escapeHtml(s.name).replace(/'/g, "\\'") + '\')">' +
      '<td class="col-rank"><span class="sa-rank-badge ' + badgeCls + '">' + rank + '</span></td>' +
      '<td class="col-name">' + escapeHtml(s.name) + '</td>' +
      '<td class="col-code">' + escapeHtml(s.code || s.key || '') + '</td>' +
      '<td class="col-num ' + chgCls + '">' + chgText + '</td>' +
      '<td>' + escapeHtml(s.leadStock || '--') + '</td>' +
      '</tr>';
  });
  tbody.innerHTML = html;

  // 更新排序箭头
  var arrow = document.getElementById('sa-sort-arrow');
  if (arrow) arrow.textContent = order === 'desc' ? '▼' : '▲';
}

/**
 * 切换周期 Tab — 更新 active → 重排表格（无 API 请求）
 * @param {string} period - '5'|'20'|'60'|'120'|'250'
 */
function switchSectorPeriod(period) {
  window.appState.sectorCurrentPeriod = period;
  document.querySelectorAll('#sector-analysis-panel .sector-period-tab').forEach(function (tab) {
    tab.classList.toggle('active', tab.getAttribute('data-period') === period);
  });
  renderSectorRankingTable(period);

  // 如果有选中板块，同步更新详情区
  if (window.appState.sectorSelectedKey) {
    renderSectorDetail(window.appState.sectorSelectedKey, period);
  }
}

/**
 * 切换排序方向 desc ↔ asc → 更新箭头 → 重排表格
 */
function toggleSectorSort() {
  window.appState.sectorSortOrder = window.appState.sectorSortOrder === 'desc' ? 'asc' : 'desc';
  renderSectorRankingTable(window.appState.sectorCurrentPeriod);
}

/**
 * 选中板块行 — 标记高亮 → 渲染详情区
 * @param {string} sectorKey - 板块英文 key
 * @param {string} sectorName - 板块中文名
 */
function selectSector(sectorKey, sectorName) {
  window.appState.sectorSelectedKey = sectorKey;
  window.appState.sectorSelectedName = sectorName;

  // 更新行高亮
  document.querySelectorAll('#sa-ranking-tbody .sa-rank-row').forEach(function (row) {
    row.classList.remove('selected');
  });
  // 通过 onclick 属性匹配不好，直接重新渲染表格以应用 selected 样式
  renderSectorRankingTable(window.appState.sectorCurrentPeriod);

  // 渲染详情区
  renderSectorDetail(sectorKey, window.appState.sectorCurrentPeriod);
}

/**
 * 渲染板块详情区 — 统计栏 + 龙头 TOP5 + 资金流 + 成分股按钮
 * @param {string} sectorKey - 板块英文 key
 * @param {string} period - 当前周期
 */
function renderSectorDetail(sectorKey, period) {
  var detailSection = document.getElementById('sa-detail-section');
  if (!detailSection) return;
  detailSection.style.display = 'block';

  var cache = window.appState.sectorRankingCache;
  if (!cache) return;

  // 从缓存中找到对应板块
  var sectorData = cache.find(function (s) { return s.key === sectorKey; });
  if (!sectorData) return;

  var chgVal = sectorData[period] != null ? sectorData[period] : 0;

  // 渲染统计栏
  var statsBar = document.getElementById('sa-stats-bar');
  if (statsBar) {
    statsBar.innerHTML =
      '<div class="sa-stat-item"><span class="sa-stat-label">均涨幅</span><span class="sa-stat-val ' + (chgVal >= 0 ? 'sa-chg-up' : 'sa-chg-dn') + '">' + (chgVal >= 0 ? '+' : '') + chgVal.toFixed(2) + '%</span></div>' +
      '<div class="sa-stat-item"><span class="sa-stat-label">上涨</span><span class="sa-stat-val sa-chg-up">' + (sectorData.upCount || 0) + '</span></div>' +
      '<div class="sa-stat-item"><span class="sa-stat-label">下跌</span><span class="sa-stat-val sa-chg-dn">' + (sectorData.dnCount || 0) + '</span></div>' +
      '<div class="sa-stat-item"><span class="sa-stat-label">成交额</span><span class="sa-stat-val">' + _fmtAmount(sectorData.amount) + '</span></div>';
  }

  // 渲染板块标题
  var detailTitle = document.getElementById('sa-detail-title');
  if (detailTitle) detailTitle.textContent = sectorData.name + ' 板块详情';

  // 渲染龙头股 TOP5 — 从 API 数据的 lead_stock 或本地股池
  renderSectorLeaders(sectorKey, sectorData);

  // 加载资金流数据
  loadSectorFundFlow(sectorKey, sectorData);
}

/**
 * 渲染龙头股 TOP5
 * @param {string} sectorKey - 板块英文 key
 * @param {Object} sectorData - 缓存中的板块数据
 */
function renderSectorLeaders(sectorKey, sectorData) {
  var container = document.getElementById('sa-leaders');
  if (!container) return;

  // 优先从本地股池中取该板块的龙头股
  var sectorStocks = window.appState.stocks.filter(function (s) {
    return s.sector === sectorKey && s.price > 0;
  });

  if (sectorStocks.length > 0) {
    var sorted = sectorStocks.sort(function (a, b) { return (b.chg || 0) - (a.chg || 0); });
    var top5 = sorted.slice(0, 5);
    var html = '';
    top5.forEach(function (s) {
      var chg = s.chg || 0;
      var chgCls = chg >= 0 ? 'sa-chg-up' : 'sa-chg-dn';
      html += '<span class="sa-leader-pill" onclick="selectStock(\'' + escapeHtml(s.code) + '\', \'' +
        escapeHtml(s.name).replace(/'/g, "\\'") + '\')">' +
        escapeHtml(s.name) + ' <span class="' + chgCls + '">' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%</span></span>';
    });
    container.innerHTML = html;
  } else {
    // 降级：显示 API 返回的领涨股名称
    container.innerHTML = '<span class="sa-leader-pill">' + escapeHtml(sectorData.leadStock || '暂无龙头股数据') + '</span>';
  }
}

/**
 * 加载板块资金流 — API 优先，空数据时本地估算
 * @param {string} sectorKey - 板块英文 key
 * @param {Object} sectorData - 缓存中的板块数据
 */
async function loadSectorFundFlow(sectorKey, sectorData) {
  var container = document.getElementById('sa-fundflow');
  if (!container) return;

  // 显示加载中
  container.innerHTML = '<div class="data-table-skeleton"><div class="skeleton-row"></div><div class="skeleton-row"></div></div>';

  try {
    var data = await fetchSectorFundFlow(sectorKey);
    if (data && (data.super_large != null || data.large != null || data.main != null)) {
      // API 有真实数据
      var flowData = {
        superLarge: parseFloat(data.super_large || data['超大单'] || 0),
        large: parseFloat(data.large || data['大单'] || 0),
        medium: parseFloat(data.medium || data['中单'] || 0),
        small: parseFloat(data.small || data['小单'] || 0),
        main: parseFloat(data.main || data['主力'] || ((data.super_large || 0) + (data.large || 0))),
        isEstimated: false
      };
      renderSectorFundFlow(flowData);
    } else {
      // API 无数据，本地估算
      var estimated = estimateSectorFundFlow(sectorData);
      renderSectorFundFlow(estimated);
    }
  } catch (e) {
    console.warn('loadSectorFundFlow error:', e);
    var estimated = estimateSectorFundFlow(sectorData);
    renderSectorFundFlow(estimated);
  }
}

/**
 * 渲染板块资金流 — 横向柱状图 + 数值列表 + 主力高亮
 * @param {Object} data - {superLarge, large, medium, small, main, isEstimated}
 */
function renderSectorFundFlow(data) {
  var container = document.getElementById('sa-fundflow');
  if (!container) return;

  var items = [
    { label: '超大单', val: data.superLarge },
    { label: '大单', val: data.large },
    { label: '中单', val: data.medium },
    { label: '小单', val: data.small },
  ];

  // 计算最大绝对值用于柱状图比例
  var maxAbs = Math.max(Math.abs(data.superLarge), Math.abs(data.large), Math.abs(data.medium), Math.abs(data.small), 1);

  var html = '<div class="sa-fundflow-bars">';
  items.forEach(function (item) {
    var pct = (Math.abs(item.val) / maxAbs) * 50; // 最大占 50% 宽度
    var isPositive = item.val >= 0;
    var barCls = isPositive ? 'sa-fundflow-bar-pos' : 'sa-fundflow-bar-neg';
    html += '<div class="sa-fundflow-bar-item">' +
      '<span class="sa-fundflow-label">' + item.label + '</span>' +
      '<div class="sa-fundflow-bar">' +
        (isPositive
          ? '<div class="sa-fundflow-bar-fill ' + barCls + '" style="width:' + pct + '%;margin-left:50%;"></div>'
          : '<div class="sa-fundflow-bar-fill ' + barCls + '" style="width:' + pct + '%;margin-right:50%;float:right;"></div>') +
      '</div>' +
      '<span class="sa-fundflow-val ' + (isPositive ? 'sa-chg-up' : 'sa-chg-dn') + '">' + _fmtAmount(item.val) + '</span>' +
      '</div>';
  });
  html += '</div>';

  // 主力净流入大号高亮
  var mainCls = data.main >= 0 ? 'sa-chg-up' : 'sa-chg-dn';
  html += '<div class="sa-fundflow-main">' +
    '<span class="sa-fundflow-main-label">主力净流入</span>' +
    '<span class="sa-fundflow-main-val ' + mainCls + '">' + (data.main >= 0 ? '+' : '') + _fmtAmount(data.main) + '</span>';
  if (data.isEstimated) {
    html += '<span class="sa-fundflow-estimated">(估)</span>';
  }
  html += '</div>';

  container.innerHTML = html;
}

/**
 * 本地估算板块资金流 — 基于成交额/上涨比例按经验系数估算
 * @param {Object} sectorData - 缓存中的板块数据
 * @returns {Object} {superLarge, large, medium, small, main, isEstimated}
 */
function estimateSectorFundFlow(sectorData) {
  var totalAmount = sectorData.amount || 0;
  var upCount = sectorData.upCount || 0;
  var totalCount = sectorData.totalCount || (upCount + (sectorData.dnCount || 0)) || 1;
  if (totalCount === 0) totalCount = 1;
  var ratio = upCount / totalCount;
  var baseFlow = totalAmount * ratio;

  return {
    superLarge: baseFlow * 0.30,
    large: baseFlow * 0.20,
    medium: -baseFlow * 0.15,
    small: -baseFlow * 0.10,
    main: baseFlow * 0.50,
    isEstimated: true
  };
}

/**
 * 打开成分股面板 — 调 fetchSectorStocks(key) → 渲染表格
 */
async function showSectorStocks() {
  var panel = document.getElementById('sector-stocks-panel');
  if (!panel) return;
  var sectorKey = window.appState.sectorSelectedKey;
  var sectorName = window.appState.sectorSelectedName;
  if (!sectorKey) return;

  panel.style.display = 'flex';

  // 更新标题
  var titleEl = document.getElementById('sector-stocks-title');
  if (titleEl) titleEl.textContent = sectorName + ' 成分股';

  var tbody = document.getElementById('sector-stocks-tbody');
  if (!tbody) return;
  _renderTableSkeleton(tbody, 10);

  try {
    var data = await fetchSectorStocks(sectorKey);
    var stocks = [];
    // 兼容三种返回形态（后端历史上换过字段名，前端此前只认第一种，导致接口结果被整体丢弃）：
    //   ① {stocks:[...]}  ② 裸数组  ③ fetchSectorStocks 统一映射后的 {data:[...], rows:[...]}
    if (data && data.stocks && data.stocks.length > 0) {
      stocks = data.stocks;
    } else if (Array.isArray(data) && data.length > 0) {
      stocks = data;
    } else if (data && Array.isArray(data.data) && data.data.length > 0) {
      stocks = data.data;
    } else if (data && Array.isArray(data.rows) && data.rows.length > 0) {
      stocks = data.rows;
    } else {
      // 降级：从本地股池过滤
      var localStocks = window.appState.stocks.filter(function (s) { return s.sector === sectorKey; });
      if (localStocks.length > 0) {
        stocks = localStocks.map(function (s) { return { code: s.code, name: s.name, priority: 0, chg: s.chg, price: s.price }; });
      }
    }

    if (stocks.length === 0) {
      _renderTableEmpty(tbody, '该板块暂无成分股数据');
      return;
    }

    // 更新标题数量
    if (titleEl) titleEl.textContent = sectorName + ' 成分股 (' + stocks.length + '只)';

    // 排序：按 priority 降序
    stocks.sort(function (a, b) { return (b.priority || 0) - (a.priority || 0); });

    var html = '';
    stocks.forEach(function (s, i) {
      var code = s.code || s['代码'] || '';
      var name = s.name || s['名称'] || '--';
      var chg = parseFloat(s.chg || s['涨跌幅'] || 0);
      var turnover = parseFloat(s.turnover || s['换手率'] || 0);
      var marketCap = s.market_cap || s['市值'] || null;
      var pe = s.pe || s['PE'] || null;
      var chgCls = chg >= 0 ? 'sa-chg-up' : 'sa-chg-dn';
      var chgText = (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%';

      html += '<tr onclick="selectStock(\'' + escapeHtml(code) + '\', \'' +
        escapeHtml(name).replace(/'/g, "\\'") + '\'); closeSectorStocksPanel();">' +
        '<td class="col-rank">' + (i + 1) + '</td>' +
        '<td class="col-code">' + escapeHtml(code) + '</td>' +
        '<td class="col-name">' + escapeHtml(name) + '</td>' +
        '<td class="col-num ' + chgCls + '">' + chgText + '</td>' +
        '<td class="col-num">' + (turnover > 0 ? turnover.toFixed(2) + '%' : '--') + '</td>' +
        '<td class="col-num">' + (marketCap != null ? _fmtAmount(marketCap) : '--') + '</td>' +
        '<td class="col-num">' + (pe != null ? pe.toFixed(1) : '--') + '</td>' +
        '</tr>';
    });
    tbody.innerHTML = html;
  } catch (e) {
    console.error('showSectorStocks error:', e);
    _renderTableError(tbody, 'showSectorStocks()');
  }
}

/**
 * 关闭成分股面板
 */
function closeSectorStocksPanel() {
  var panel = document.getElementById('sector-stocks-panel');
  if (panel) panel.style.display = 'none';
}

/**
 * v5.4: 保留旧版迷你分析渲染 — 供左下角板块行情摘要使用
 * 原 renderRealSectorAnalysis 的面板渲染部分已被新函数替代
 */
async function renderRealSectorAnalysis() {
  var sectorStats = calculateSectorStats();
  // 同步更新左下角迷你分析
  renderMiniSectorAnalysis(sectorStats);
}

// ===== v6.0 板块行情摘要 — 增强数据计算 =====
// 计算板块统计数据，包含上涨/下跌/平盘数量、资金流向等
function calculateSectorStats() {
  const sectorStats = [];
  const stocks = window.appState.stocks || [];
  
  if (stocks.length > 0) {
    const upStocks   = stocks.filter(s => (s.chg || 0) > 0);
    const downStocks = stocks.filter(s => (s.chg || 0) < 0);
    const flatStocks = stocks.filter(s => (s.chg || 0) === 0);
    const upCount   = upStocks.length;
    const downCount = downStocks.length;
    const flatCount = flatStocks.length;
    
    const avgChg = stocks.reduce((sum, s) => sum + (s.chg || 0), 0) / stocks.length;
    const totalAmount = stocks.reduce((sum, s) => sum + (s.amount || 0), 0);
    const avgTurnover = stocks.reduce((sum, s) => sum + (s.turnover || 0), 0) / stocks.length;
    
    // 资金流向：涨股成交额 = 流入，跌股成交额 = 流出
    const inflowAmount  = upStocks.reduce((sum, s) => sum + (s.amount || 0), 0);
    const outflowAmount = downStocks.reduce((sum, s) => sum + (s.amount || 0), 0);
    const netFlow = inflowAmount - outflowAmount;
    
    const sortedByChg = [...stocks].sort((a, b) => (b.chg || 0) - (a.chg || 0));
    const topStock = sortedByChg[0];
    const bottomStock = sortedByChg[sortedByChg.length - 1];
    
    sectorStats.push({
      key: window.appState.activeSector || 'semiconductor',
      name: (SECTORS.find(s => s.key === window.appState.activeSector) || {}).name || '当前板块',
      count: stocks.length,
      upCount,
      downCount,
      flatCount,
      avgChg,
      totalAmount,
      avgTurnover,
      topStock,
      bottomStock,
      inflowAmount,
      outflowAmount,
      netFlow,
      score: avgChg * 100 + (totalAmount / 1000000000)
    });
  }
  
  return sectorStats;
}

// 旧版 renderRealSectorAnalysis 已在 v5.4 重写中移除
// 新版函数在文件上方 v5.4 板块分析面板区块中定义

// ===== v6.0 板块行情摘要 — 增强渲染 =====
// 渲染左下角迷你板块分析卡片（市场情绪、涨跌平统计、资金流向）
function renderMiniSectorAnalysis(sectorStats) {
  const currentKey = window.appState.activeSector;
  const current = sectorStats.find(s => s.key === currentKey) || sectorStats[0];
  if (!current) return;

  const upEl   = document.getElementById('mini-up-count');
  const dnEl   = document.getElementById('mini-dn-count');
  const flatEl = document.getElementById('mini-flat-count');
  const upPctEl   = document.getElementById('mini-up-pct');
  const dnPctEl   = document.getElementById('mini-dn-pct');
  const flatPctEl = document.getElementById('mini-flat-pct');
  const sentimentFill  = document.getElementById('mini-sentiment-fill');
  const sentimentGlow  = document.getElementById('mini-sentiment-glow');
  const sentimentBadge = document.getElementById('mini-sentiment-badge');
  const flowValEl      = document.getElementById('mini-flow-val');
  const flowInflowBar  = document.getElementById('mini-flow-inflow-bar');
  const flowOutflowBar = document.getElementById('mini-flow-outflow-bar');
  const inflowAmtEl    = document.getElementById('mini-inflow-amt');
  const outflowAmtEl   = document.getElementById('mini-outflow-amt');

  // --- 1. 涨跌平统计：数量 + 占比 ---
  if (upEl)   upEl.textContent   = current.upCount;
  if (dnEl)   dnEl.textContent   = current.downCount;
  if (flatEl) flatEl.textContent = current.flatCount;

  const total = current.count || 1;
  if (upPctEl)   upPctEl.textContent   = ((current.upCount   / total) * 100).toFixed(0) + '%';
  if (dnPctEl)   dnPctEl.textContent   = ((current.downCount / total) * 100).toFixed(0) + '%';
  if (flatPctEl) flatPctEl.textContent = ((current.flatCount / total) * 100).toFixed(0) + '%';

  // --- 2. 市场情绪：进度条 + 光晕 + 徽章 ---
  const sentiment = Math.round((current.upCount / total) * 100);
  if (sentimentFill) {
    sentimentFill.style.width = sentiment + '%';
  }
  if (sentimentGlow) {
    sentimentGlow.style.left = sentiment + '%';
    // 根据情绪值动态调整光晕颜色
    if (sentiment > 60) {
      sentimentGlow.style.boxShadow = '0 0 10px rgba(0, 233, 106, 0.8), 0 0 20px rgba(0, 233, 106, 0.4)';
    } else if (sentiment < 40) {
      sentimentGlow.style.boxShadow = '0 0 10px rgba(255, 61, 106, 0.8), 0 0 20px rgba(255, 61, 106, 0.4)';
    } else {
      sentimentGlow.style.boxShadow = '0 0 10px rgba(245, 158, 11, 0.8), 0 0 20px rgba(245, 158, 11, 0.4)';
    }
  }
  if (sentimentBadge) {
    let mood, moodClass;
    if (sentiment > 60)      { mood = '乐观';  moodClass = 'bullish'; }
    else if (sentiment < 40) { mood = '悲观';  moodClass = 'bearish'; }
    else                     { mood = '中性';  moodClass = 'neutral'; }
    sentimentBadge.textContent = sentiment + '% ' + mood;
    sentimentBadge.className  = 'sentiment-badge ' + moodClass;
  }

  // --- 3. 资金流向：方向 + 金额 + 比例条 + 流入/流出明细 ---
  const inflow  = current.inflowAmount  || 0;
  const outflow = current.outflowAmount || 0;
  const netFlow = current.netFlow || 0;

  // 金额格式化
  const fmtFlow = function(v) {
    var abs = Math.abs(v);
    if (abs >= 1e12) return (abs / 1e12).toFixed(2) + '万亿';
    if (abs >= 1e8)  return (abs / 1e8).toFixed(1) + '亿';
    if (abs >= 1e4)  return (abs / 1e4).toFixed(0) + '万';
    return abs.toFixed(0);
  };

  if (flowValEl) {
    var direction = netFlow >= 0 ? '▲' : '▼';
    flowValEl.textContent = direction + ' ' + fmtFlow(netFlow);
    flowValEl.className = 'flow-val ' + (netFlow >= 0 ? 'net-inflow' : 'net-outflow');
  }

  // 流入/流出比例条
  var totalFlow = inflow + outflow || 1;
  var inflowPct = Math.round((inflow / totalFlow) * 100);
  var outflowPct = 100 - inflowPct;
  if (flowInflowBar)  flowInflowBar.style.width  = inflowPct + '%';
  if (flowOutflowBar) flowOutflowBar.style.width  = outflowPct + '%';

  // 流入/流出金额明细
  if (inflowAmtEl)  inflowAmtEl.textContent  = fmtFlow(inflow);
  if (outflowAmtEl) outflowAmtEl.textContent = fmtFlow(outflow);
}

// 板块热度排行（sa-hot-list）
function renderHotSectorList(sectorStats) {
  var container = document.getElementById('sa-hot-sectors');
  var countBadge = document.getElementById('sa-hot-count');
  if (!container) return;
  
  var top5 = sectorStats.slice(0, 5);
  if (countBadge) countBadge.textContent = top5.length + ' 个活跃板块';
  
  container.innerHTML = top5.map(function(s, i) {
    var rankCls = i === 0 ? 'top1' : i === 1 ? 'top2' : i === 2 ? 'top3' : '';
    var heatEmoji = s.avgChg >= 2 ? '🔥' : s.avgChg >= 0.5 ? '温️' : s.avgChg >= 0 ? '📊' : '冷️';
    var amountText = s.totalAmount >= 1e8 ? (s.totalAmount/1e8).toFixed(1)+'亿' : (s.totalAmount/1e4).toFixed(0)+'万';
    var chgCls = s.avgChg >= 0 ? 'up' : 'dn';
    return '<div class="sa-hot-item" onclick="switchSector(\'' + s.key + '\')">' +
      '<div class="sa-hot-rank ' + rankCls + '">' + (i+1) + '</div>' +
      '<div class="sa-hot-info"><div class="sa-hot-name">' + heatEmoji + ' ' + escapeHtml(s.name) + '</div>' +
      '<div class="sa-hot-desc">' + s.count + '只 · 涨' + s.upCount + '跌' + s.downCount + ' · 成交' + amountText + '</div></div>' +
      '<div class="sa-hot-score"><div class="sa-hot-score-val ' + chgCls + '">' + (s.avgChg>=0?'+':'') + s.avgChg.toFixed(2) + '%</div>' +
      '<div class="sa-hot-score-label">均涨幅</div></div></div>';
  }).join('');
}

// 当前板块涨跌分布条
function renderSectorDistribution(sectorStats) {
  var currentKey = window.appState.activeSector;
  var current = sectorStats.find(function(s) { return s.key === currentKey; });
  if (!current) {
    // 使用第一个板块
    current = sectorStats[0];
  }
  if (!current) return;
  
  var badge = document.getElementById('sa-dist-sector');
  if (badge) badge.textContent = (current.icon||'') + ' ' + current.name;
  
  var stocks = window.appState.stocks.filter(function(s) { return s.sector === current.key && s.price > 0; });
  var total = stocks.length || 1;
  var limitUp = stocks.filter(function(s) { return s.chg >= 9.9; }).length;
  var up5 = stocks.filter(function(s) { return s.chg >= 5 && s.chg < 9.9; }).length;
  var up = stocks.filter(function(s) { return s.chg > 0 && s.chg < 5; }).length;
  var flat = stocks.filter(function(s) { return s.chg === 0; }).length;
  var dn = stocks.filter(function(s) { return s.chg < 0 && s.chg > -5; }).length;
  var dn5 = stocks.filter(function(s) { return s.chg <= -5 && s.chg > -9.9; }).length;
  var limitDn = stocks.filter(function(s) { return s.chg <= -9.9; }).length;
  
  var setBar = function(id, count, cntId) {
    var el = document.getElementById(id);
    if (el) el.style.width = Math.max(1, (count/total)*100) + '%';
    var cnt = document.getElementById(cntId);
    if (cnt) cnt.textContent = count;
  };
  setBar('sa-dist-limit-up', limitUp, 'sa-count-limit-up');
  setBar('sa-dist-up5', up5, 'sa-count-up5');
  setBar('sa-dist-up', up, 'sa-count-up');
  setBar('sa-dist-flat', flat, 'sa-count-flat');
  setBar('sa-dist-dn', dn, 'sa-count-dn');
  setBar('sa-dist-dn5', dn5, 'sa-count-dn5');
  setBar('sa-dist-limit-dn', limitDn, 'sa-count-limit-dn');
}

// 资金流向概览
function renderSectorFlow(sectorStats) {
  var grid = document.getElementById('sa-flow-grid');
  if (!grid) return;
  
  var currentKey = window.appState.activeSector;
  var current = sectorStats.find(function(s) { return s.key === currentKey; }) || sectorStats[0];
  if (!current) return;
  
  var stocks = window.appState.stocks.filter(function(s) { return s.sector === current.key; });
  var totalAmount = stocks.reduce(function(sum, s) { return sum + (s.amount||0); }, 0);
  var inflowStocks = stocks.filter(function(s) { return s.chg >= 0; });
  var outflowStocks = stocks.filter(function(s) { return s.chg < 0; });
  var inflowAmount = inflowStocks.reduce(function(sum, s) { return sum + (s.amount||0); }, 0);
  var outflowAmount = outflowStocks.reduce(function(sum, s) { return sum + (s.amount||0); }, 0);
  var netFlow = inflowAmount - outflowAmount;
  
  var fmtAmt = function(v) {
    if (v >= 1e12) return (v/1e12).toFixed(2)+'万亿';
    if (v >= 1e8) return (v/1e8).toFixed(1)+'亿';
    if (v >= 1e4) return (v/1e4).toFixed(0)+'万';
    return v.toFixed(0);
  };
  
  grid.innerHTML = 
    '<div class="sa-flow-card"><div class="sa-flow-label">板块总成交</div><div class="sa-flow-value" style="color:var(--blue)">' + fmtAmt(totalAmount) + '</div><div class="sa-flow-sub">' + stocks.length + ' 只个股</div></div>' +
    '<div class="sa-flow-card"><div class="sa-flow-label">主力净流入</div><div class="sa-flow-value ' + (netFlow>=0?'inflow':'outflow') + '">' + (netFlow>=0?'+':'') + fmtAmt(netFlow) + '</div><div class="sa-flow-sub">' + (netFlow>=0?'资金净流入':'资金净流出') + '</div></div>' +
    '<div class="sa-flow-card"><div class="sa-flow-label">上涨资金</div><div class="sa-flow-value inflow">' + fmtAmt(inflowAmount) + '</div><div class="sa-flow-sub">' + inflowStocks.length + ' 只上涨股</div></div>' +
    '<div class="sa-flow-card"><div class="sa-flow-label">下跌资金</div><div class="sa-flow-value outflow">' + fmtAmt(outflowAmount) + '</div><div class="sa-flow-sub">' + outflowStocks.length + ' 只下跌股</div></div>';
}

// 板块对比表格
function renderSectorCompareTable(sectorStats) {
  var container = document.getElementById('sa-compare-table');
  if (!container) return;
  
  var top8 = sectorStats.slice(0, 8);
  var fmtChg = function(v) {
    return (v>=0?'+':'') + v.toFixed(2) + '%';
  };
  var fmtAmt = function(v) {
    if (v >= 1e12) return (v/1e12).toFixed(2)+'万亿';
    if (v >= 1e8) return (v/1e8).toFixed(1)+'亿';
    return (v/1e4).toFixed(0)+'万';
  };
  var heatDot = function(chg) {
    if (chg >= 2) return '<span class="sa-heat-dot hot"></span>热门';
    if (chg >= 0.5) return '<span class="sa-heat-dot warm"></span>活跃';
    if (chg >= 0) return '<span class="sa-heat-dot cool"></span>温和';
    return '<span class="sa-heat-dot cold"></span>冷清';
  };
  
  var headerRow = '<tr><th>板块</th>' + top8.map(function(s) { return '<th>' + s.icon + ' ' + escapeHtml(s.name) + '</th>'; }).join('') + '</tr>';
  var avgChgRow = '<tr><td>均涨幅</td>' + top8.map(function(s) { return '<td class="' + (s.avgChg>=0?'sa-trend-up':'sa-trend-dn') + '">' + fmtChg(s.avgChg) + '</td>'; }).join('') + '</tr>';
  var amountRow = '<tr><td>成交额</td>' + top8.map(function(s) { return '<td>' + fmtAmt(s.totalAmount) + '</td>'; }).join('') + '</tr>';
  var countRow = '<tr><td>个股数</td>' + top8.map(function(s) { return '<td>' + s.count + '</td>'; }).join('') + '</tr>';
  var ratioRow = '<tr><td>涨跌比</td>' + top8.map(function(s) { return '<td>' + s.upCount + ':' + s.downCount + '</td>'; }).join('') + '</tr>';
  var heatRow = '<tr><td>热度</td>' + top8.map(function(s) { return '<td>' + heatDot(s.avgChg) + '</td>'; }).join('') + '</tr>';
  
  container.innerHTML = '<table>' + headerRow + avgChgRow + amountRow + countRow + ratioRow + heatRow + '</table>';
}

function renderSectorRankings(sectorStats) {
  const container = document.getElementById('sector-analysis-sectors');
  if (!container) return;

  container.innerHTML = sectorStats.map((sector, index) => {
    const chgCls = sector.avgChg >= 0 ? 'chg-up' : 'chg-dn';
    const chgText = (sector.avgChg >= 0 ? '+' : '') + sector.avgChg.toFixed(2) + '%';
    const amountText = sector.totalAmount >= 100000000 ? (sector.totalAmount / 100000000).toFixed(1) + '亿' : 
                      sector.totalAmount >= 10000 ? (sector.totalAmount / 10000).toFixed(0) + '万' : sector.totalAmount;
    
    return `
      <div class="sector-card" style="cursor:pointer" onclick="switchSector('${sector.key}')">
        <div class="sector-card-header">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
            <div style="display:flex;align-items:center;gap:10px">
              <span style="font-size:20px">${sector.icon}</span>
              <div>
                <div class="sector-card-name">
                  <span style="color:var(--muted);font-size:11px;font-weight:normal">#${index + 1}</span>
                  ${sector.name}
                </div>
                <div style="font-size:11px;color:var(--muted);margin-top:2px">
                  ${sector.count}只股票 · 涨跌比 ${sector.upCount}:${sector.downCount}
                </div>
              </div>
            </div>
            <div style="text-align:right">
              <div style="font-size:18px;font-weight:700;color:${sector.avgChg >= 0 ? 'var(--red)' : 'var(--green)'}">${chgText}</div>
              <div style="font-size:11px;color:var(--muted);margin-top:2px">成交额 ${amountText}</div>
            </div>
          </div>
        </div>
        
        <div style="padding:10px;border-top:1px solid rgba(255,255,255,0.06);display:flex;justify-content:space-between;gap:10px">
          ${sector.topStock ? `
            <div style="flex:1">
              <div style="font-size:10px;color:var(--muted);margin-bottom:4px">涨幅居首</div>
              <div style="display:flex;justify-content:space-between;align-items:center">
                <div style="color:var(--text);font-size:12px">${sector.topStock.name}</div>
                <div style="color:var(--red);font-size:12px;font-weight:600">+${sector.topStock.chg.toFixed(2)}%</div>
              </div>
            </div>
          ` : ''}
          ${sector.bottomStock ? `
            <div style="flex:1">
              <div style="font-size:10px;color:var(--muted);margin-bottom:4px">跌幅居首</div>
              <div style="display:flex;justify-content:space-between;align-items:center">
                <div style="color:var(--text);font-size:12px">${sector.bottomStock.name}</div>
                <div style="color:var(--green);font-size:12px;font-weight:600">${sector.bottomStock.chg.toFixed(2)}%</div>
              </div>
            </div>
          ` : ''}
        </div>
      </div>
    `;
  }).join('');
}

function renderHotSectors(sectorStats) {
  const focusDesc = document.getElementById('market-focus-desc');
  const focusTags = document.getElementById('market-focus-tags');
  const topList = document.getElementById('top-stocks-list');
  
  const hotSectors = sectorStats.slice(0, 4);
  const hotSectorNames = hotSectors.map(s => s.name).join('、');
  
  if (focusDesc) {
    focusDesc.textContent = `今日热点聚焦${hotSectorNames}，${hotSectors[0]?.avgChg >= 0 ? '领涨市场' : '波动加大'}`;
  }
  
  if (focusTags) {
    focusTags.innerHTML = hotSectors.map(s => `
      <span class="focus-tag" style="cursor:pointer" onclick="switchSector('${s.key}')">${s.icon} ${s.name}</span>
    `).join('');
  }
  
  if (topList) {
    const allStocks = [...window.appState.stocks].filter(s => s.price > 0 && s.chg != null);
    const sortedStocks = [...allStocks].sort((a, b) => (b.chg || 0) - (a.chg || 0)).slice(0, 5);
    
    topList.innerHTML = sortedStocks.map((stock, index) => {
      const chgCls = stock.chg >= 0 ? 'up' : 'dn';
      const chgText = (stock.chg >= 0 ? '+' : '') + stock.chg.toFixed(2) + '%';
      const sectorInfo = SECTORS.find(s => s.key === stock.sector);
      
      return `
        <div class="top-item" onclick="selectStock(decodeURIComponent('${encodeURIComponent(stock.code || '').replace(/'/g, '%27')}'), decodeURIComponent('${encodeURIComponent(stock.name || '').replace(/'/g, '%27')}'))">
          <div class="top-item-rank">${index + 1}</div>
          <div class="top-item-info">
            <div class="top-item-name">${escapeHtml(stock.name)}</div>
            <div class="top-item-sector">${sectorInfo?.icon || ''} ${sectorInfo?.name || stock.sector}</div>
          </div>
          <div style="text-align:right">
            <div class="top-item-price">${stock.price.toFixed(2)}</div>
            <div class="top-item-chg ${chgCls}">${chgText}</div>
          </div>
        </div>
      `;
    }).join('');
  }
}

// 在实时行情更新时刷新板块分析
// 实时轮询完成后的附加 UI 更新，保持单一包装，避免自引用递归。
var _baseRealtimePoll = pollRealtimeTick;
pollRealtimeTick = async function() {
  await _baseRealtimePoll();
  checkAlerts();
  const panel = document.getElementById('sector-analysis-panel');
  if (panel && panel.style.display !== 'none') {
    renderRealSectorAnalysis();
  }
};

// 添加 V 键快捷键支持
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;
  
  // V - 板块分析
  if (e.key === 'v' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    toggleSectorAnalysisPanel();
    return;
  }
  
  // Escape 关闭对比、预警或板块分析面板
  if (e.key === 'Escape') {
    // v7: 优先关闭右键菜单 / 排序菜单
    if (_ctxMenuEl || (document.getElementById('sort-menu') && document.getElementById('sort-menu').classList.contains('show'))) {
      closeCtxMenu();
      closeSortMenu();
      e.preventDefault();
      return;
    }

    const comparePanel = document.getElementById('compare-panel');
    if (comparePanel?.classList.contains('open')) {
      e.preventDefault();
      comparePanel.classList.remove('open');
      return;
    }
    
    const alertPanel = document.getElementById('alert-panel');
    if (alertPanel?.classList.contains('open')) {
      e.preventDefault();
      alertPanel.classList.remove('open');
      return;
    }
    
    const sectorPanel = document.getElementById('sector-analysis-panel');
    if (sectorPanel && sectorPanel.style.display === 'flex') {
      e.preventDefault();
      closeSectorPanel();
      return;
    }
  }
});

// 启动
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

/* ══════ v11 移动流动版：点行自动定位详情 + 触屏适配 ══════ */
(function(){
  var mqMobile = window.matchMedia('(max-width: 768px)');
  function isMobileView(){ return mqMobile.matches; }

  // 移动端：点选股票后，若详情区（K线+AI分析）不在视野内，平滑滚动定位
  document.addEventListener('click', function(e){
    if (!isMobileView()) return;
    if (!e.target || !e.target.closest) return;
    // 排除行内工具按钮（自选/对比/信号标签）
    if (e.target.closest('.row-tools') || e.target.closest('.stag') || e.target.closest('.sort-wrap')) return;
    var row = e.target.closest('#stock-list .stock-row');
    if (!row) return;
    setTimeout(function(){
      var detail = document.getElementById('kline-area') || document.getElementById('right-panel');
      if (!detail) return;
      var r = detail.getBoundingClientRect();
      var outOfView = r.top > window.innerHeight * 0.7 || r.bottom < 80;
      if (outOfView && typeof detail.scrollIntoView === 'function') {
        detail.scrollIntoView({ behavior: 'smooth', block: 'start' });
        try {
          var nameEl = row.querySelector('.stock-name-text');
          if (typeof showRowToast === 'function' && nameEl) showRowToast(row, '已定位「' + nameEl.textContent + '」详情 ↓');
        } catch (err) { /* toast 失败不影响主流程 */ }
      }
    }, 400); // 等 selectStock 渲染启动后再定位
  }, true);

  // 移动端：板块切换后把股票列表滚回视野（点横滑芯片时列表可能在下方）
  document.addEventListener('click', function(e){
    if (!isMobileView()) return;
    if (!e.target || !e.target.closest) return;
    var sector = e.target.closest('.sidebar-sector');
    if (!sector) return;
    setTimeout(function(){
      var listCard = document.getElementById('left-panel');
      if (listCard) {
        var top = listCard.getBoundingClientRect().top;
        if (top > 60 || top < -window.innerHeight * 0.5) {
          listCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }
    }, 300);
  }, true);
})();

/* ===== v68 · 通知中心接入自动化信号（真实数据源，60s 轮询） ===== */
function seedNotificationsFromServer() {
  fetch('/api/automation/signals?limit=8', { cache: 'no-store' })
    .then(function (r) { return r.json(); })
    .then(function (j) {
      var d = (j && j.data) || {};
      var arr = d.items || d.signals || [];
      if (!arr.length) return;
      var added = 0;
      arr.forEach(function (sg) {
        if (notificationHistory.some(function (n) { return n.refId === sg.id; })) return;
        notificationHistory.unshift({
          type: sg.score >= 75 ? 'buy' : sg.score >= 60 ? 'signal' : 'warn',
          time: String(sg.ts || '').replace('T', ' ').slice(5, 16),
          title: (sg.name || sg.code) + ' · ' + ((sg.detail && sg.detail.label) || sg.signal_type || '信号'),
          body: '评分 ' + (sg.score != null ? sg.score : '--') + ' · 现价 ' + (sg.price != null ? sg.price : '--') + ((sg.detail && sg.detail.desc) ? ' · ' + sg.detail.desc : ''),
          refId: sg.id
        });
        added++;
      });
      if (notificationHistory.length > 40) notificationHistory.length = 40;
      if (added) { updateNotifBadge(); }
      var nc = document.getElementById('notification-center');
      if (added && nc && nc.classList.contains('open')) renderNotificationList();
    }).catch(function () {});
}
setInterval(seedNotificationsFromServer, 60000);
setTimeout(seedNotificationsFromServer, 1200);
