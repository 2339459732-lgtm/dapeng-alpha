/**
 * 市场情绪追踪系统 — 核心逻辑 v1
 *
 * 接入 DAPENG Alpha 实时 API：
 *   /api/global, /api/kline, /api/market-overview, /api/sector-ranking,
 *   /api/hot-stocks, /api/north-holding, /api/fund-flow/sector
 *
 * 17个模块实时渲染 + 情绪计算 + 信号塔 + 预判 + 计算器
 */

// ================================================================
//  全局状态
// ================================================================
const _charts = {};
let _marketData = {};
let _refreshTimer = null;

// ================================================================
//  涨跌配色（A 股惯例：涨=红 / 跌=绿）
//  色值统一从 :root 主题变量读取（--red / --green），与全站 .up/.dn 一致
// ================================================================
let _ssUpColor = '#ff3d6a';    // 涨=红（--red 兜底）
let _ssDownColor = '#00e96a';  // 跌=绿（--green 兜底）
function _ssReadColor(name, fallback) {
  try {
    const v = (getComputedStyle(document.documentElement).getPropertyValue(name) || '').trim();
    return v || fallback;
  } catch (e) { return fallback; }
}
function _ssRefreshColors() {
  _ssUpColor = _ssReadColor('--red', _ssUpColor || '#ff3d6a');
  _ssDownColor = _ssReadColor('--green', _ssDownColor || '#00e96a');
}
function _ssRgba(hex, alpha) {
  let h = String(hex == null ? '' : hex).trim().replace('#', '');
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  const n = parseInt(h, 16);
  if (h.length !== 6 || isNaN(n)) return hex;
  return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + alpha + ')';
}

// ================================================================
//  板块体系映射：东财行业板块（本页数据源，50 个）↔ 仪表盘自选板块（26 个）
//
//  背景：两个页面的板块口径完全不同 ——
//    情绪页 /api/sector-ranking 返回东财行业板块（BK1500 医美耗材、BK0475 银行…）；
//    仪表盘 /api/quotes?sector= 用的是 server/config.py 里的 26 个自选板块 key
//    （semiconductor / pharma / …）。
//  两边名字对不上、代码对不上，于是「看到某板块走强 → 去仪表盘看成分股」
//  这条最自然的动作此前无法完成，只能靠用户自己再找一遍。
//
//  这里用「关键词别名」做确定性映射：命中即给出仪表盘 sector key，
//  未命中返回 null（该板块未纳入自选池，界面不提供跳转，不做臆测）。
//  顺序敏感：越具体的别名越靠前，避免「消费电子」被「消费」先截走。
// ================================================================
var SECTOR_ALIAS = [
  ['consumer_electronics', ['消费电子', '电子元件', 'PCB', '面板', '光学光电', '被动元件']],
  ['cyber', ['网络安全', '信息安全']],
  ['ai_compute', ['算力', 'CPO', '服务器', '数据中心', '液冷', 'AI']],
  ['semiconductor', ['半导体', '芯片', '集成电路', '光刻', '封测', '存储']],
  ['photovoltaic', ['光伏', '硅料', '逆变器', '组件']],
  ['new_energy', ['新能源', '锂电', '电池', '储能', '风电', '氢能', '充电桩']],
  ['military', ['军工', '航天', '航空', '船舶', '兵装']],
  ['media_entertainment', ['传媒', '影视', '游戏', '出版', '广告', '文化']],
  ['internet', ['互联网', '软件', '云计算', '电商', '在线教育']],
  ['telecom', ['通信', '5G', '光模块', '运营商']],
  ['finance', ['银行', '保险', '证券', '多元金融', '金融']],
  ['pharma', ['医药', '生物', '医疗', '中药', '制药', '医美', '疫苗', 'CRO']],
  ['consumer', ['白酒', '食品', '饮料', '乳品', '调味', '零售', '商业百货', '家电', '旅游', '酒店', '餐饮']],
  ['real_estate', ['房地产', '地产', '物业', '建筑装饰']],
  ['steel_material', ['钢铁', '有色', '稀土', '金属', '黄金', '铜', '铝', '锂矿']],
  ['auto', ['汽车', '整车', '零部件', '轮胎']],
  ['power_equip', ['电力', '电网', '电源', '电气', '输配电']],
  ['chemical', ['化工', '化学', '新材料', '农药', '化肥']],
  ['machinery', ['机械', '通用设备', '专用设备', '工程机械', '机器人']],
  ['environmental', ['环保', '水务', '节能', '固废']],
  ['textile', ['纺织', '服装', '服饰', '化纤']],
  ['agriculture', ['农业', '种植', '养殖', '饲料', '猪肉', '水产']],
  ['transport', ['交通', '物流', '港口', '航运', '铁路', '公路']],
  ['building_materials', ['建材', '水泥', '玻璃', '陶瓷']],
  ['etf', ['ETF', '基金']],
  ['hk_stocks', ['港股', '恒生']]
];

var SECTOR_KEY_NAME = {
  semiconductor: '半导体', new_energy: '新能源', pharma: '医药', consumer: '消费',
  finance: '金融', military: '军工', ai_compute: 'AI算力', photovoltaic: '光伏',
  internet: '互联网', real_estate: '房地产', steel_material: '钢铁有色', auto: '汽车',
  power_equip: '电力设备', telecom: '通信', etf: 'ETF基金', cyber: '网络安全',
  consumer_electronics: '消费电子', chemical: '化工新材料', machinery: '机械设备',
  environmental: '环保节能', textile: '纺织服装', agriculture: '农业',
  transport: '交通运输', building_materials: '建材', media_entertainment: '传媒娱乐',
  hk_stocks: '港股通'
};

/** 东财板块名 → 仪表盘 sector key；无对应返回 null */
function mapToSector(name) {
  var s = String(name || '').trim();
  if (!s) return null;
  for (var i = 0; i < SECTOR_ALIAS.length; i++) {
    var key = SECTOR_ALIAS[i][0], aliases = SECTOR_ALIAS[i][1];
    for (var j = 0; j < aliases.length; j++) {
      if (s.indexOf(aliases[j]) >= 0) return key;
    }
  }
  return null;
}

/** 读取 URL 上的 ?sector=，用于从仪表盘跳进来时聚焦同一板块 */
function _ssFocusSector() {
  try {
    var k = (new URLSearchParams(location.search).get('sector') || '').trim();
    return k || null;
  } catch (e) { return null; }
}
// 风险/亏损语义色（红）—— 表示"风险/亏损"，与涨跌方向无关，勿按「红=涨」误改
function _ssRiskColor() { return _ssReadColor('--red', '#ff3d6a'); }

// ================================================================
//  初始化入口
// ================================================================
async function initSentiment() {
  renderRules();
  initCalculator();
  startClock();

  // 独立数据源并行加载（互不依赖）
  await Promise.allSettled([
    loadGlobalIndices(),
    loadSSETrend(),
    loadSectorHeatmap(),
    loadNorthHolding(),
    loadFundFlow(),
  ]);

  // 核心大盘数据（真实涨跌家数 + 领涨/领跌榜）必须先就绪：
  // 涨跌分布 / 涨幅榜 / 情绪分 / 锚点 / 预判均消费同一份真实数据。
  await loadDashboardCore();
  loadMarketOverview();
  loadTopGainers();

  // 依赖模块（需要多个数据源汇总）
  loadSentimentGauge();
  loadAnchors();
  loadPrediction();

  // 启动自动刷新
  startAutoRefresh();
}

// ================================================================
//  Section 1: 全球指数概览
// ================================================================
async function loadGlobalIndices() {
  const data = await fetchGlobal();
  if (!data) { document.getElementById('index-grid').innerHTML = '<div class="loading-skeleton">数据加载失败</div>'; return; }
  _marketData.global = data;

  const indices = [];
  if (data.indices) {
    Object.values(data.indices).forEach(idx => indices.push(idx));
  }
  if (data.overseas) {
    Object.values(data.overseas).forEach(idx => indices.push(idx));
  }

  const grid = document.getElementById('index-grid');
  grid.innerHTML = indices.map(idx => {
    const chg = parseFloat(idx.chg || 0);
    const isUp = chg >= 0;
    const cls = isUp ? 'up' : 'down';
    const arrow = isUp ? '▲' : '▼';
    return `<div class="idx-card ${cls}" onclick="window.location.href='dashboard.html'">
      <div class="idx-name">${escapeHtml(idx.name)}</div>
      <div class="idx-price">${safeFixed(idx.price, 2)}</div>
      <div class="idx-chg">${arrow} ${isUp ? '+' : ''}${chg.toFixed(2)}%</div>
    </div>`;
  }).join('');
}

// ================================================================
//  Section 2: 市场情绪仪表
// ================================================================
function loadSentimentGauge() {
  let score = 50; // 基础分
  let upCount = 0, downCount = 0, limitUp = 0, limitDown = 0;
  // 真实涨跌家数来自 /api/market-dashboard 的 breadth（东财 push2 阻断期间为 null）。
  const b = _marketData.breadth;
  const hasBreadth = !!(b && (b.up != null || b.down != null));

  if (hasBreadth) {
    upCount = parseInt(b.up, 10) || 0;
    downCount = parseInt(b.down, 10) || 0;
    limitUp = parseInt(b.limit_up, 10) || 0;
    limitDown = parseInt(b.limit_down, 10) || 0;
    if (upCount + downCount > 0) {
      const ratio = upCount / (upCount + downCount);
      if (ratio > 0.7) score += 15;
      else if (ratio > 0.6) score += 10;
      else if (ratio > 0.5) score += 5;
      else if (ratio < 0.3) score -= 15;
      else if (ratio < 0.4) score -= 10;
    }
    if (limitUp > 20) score += 10;
    else if (limitUp > 5) score += 5;
    if (limitDown > 20) score -= 10;
    else if (limitDown > 5) score -= 5;
  }

  // 从全球指数判断大盘方向
  if (_marketData.global && _marketData.global.indices) {
    const sh = _marketData.global.indices.sh;
    if (sh) {
      const chg = parseFloat(sh.chg || 0);
      if (chg > 1) score += 5;
      else if (chg < -1) score -= 5;
    }
  }

  score = Math.max(0, Math.min(100, Math.round(score)));

  // 渲染仪表盘
  const dom = document.getElementById('sentiment-gauge');
  if (!_charts.gauge) _charts.gauge = echarts.init(dom);
  _charts.gauge.setOption({
    series: [{
      type: 'gauge', radius: '90%', startAngle: 200, endAngle: -20,
      min: 0, max: 100,
      progress: { show: true, width: 14, roundCap: true,
        // 高分=乐观/看多 → 红（与 .lab-regime.up 同惯例）；中档琥珀；低分=绿
        itemStyle: { color: score > 60 ? _ssUpColor : score > 40 ? '#f59e0b' : _ssDownColor } },
      axisLine: { lineStyle: { width: 14, color: [[1, 'rgba(255,255,255,0.05)']] } },
      pointer: { length: '55%', width: 4, itemStyle: { color: '#8b9ec2' } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      detail: {
        valueAnimation: true, fontSize: 28, fontWeight: 700, offsetCenter: [0, '65%'],
        formatter: function(v) {
          const label = v > 70 ? '偏热' : v > 50 ? '中性偏多' : v > 30 ? '中性偏空' : '偏冷';
          return '{val|' + v + '}\n{lab|' + label + '}';
        },
        rich: {
          val: { fontSize: 28, fontWeight: 700, color: '#e8edf5', fontFamily: 'SF Mono, monospace' },
          lab: { fontSize: 11, color: '#8b9ec2', padding: [4, 0, 0, 0] }
        }
      },
      data: [{ value: score }]
    }]
  });

  // 渲染统计（涨跌家数不可用时如实显示 "--"，绝不以 0 冒充真实数据）
  const gsLimitUp = hasBreadth ? (limitUp + '+') : '--';
  const gsLimitDn = hasBreadth ? (limitDown + '+') : '--';
  const gsUpCnt = hasBreadth ? upCount : '--';
  const gsDnCnt = hasBreadth ? downCount : '--';
  document.getElementById('gauge-stats').innerHTML = [
    `<div class="gs-item"><span class="gs-label">涨停</span><span class="gs-val up">${gsLimitUp}</span></div>`,
    `<div class="gs-item"><span class="gs-label">跌停</span><span class="gs-val down">${gsLimitDn}</span></div>`,
    `<div class="gs-item"><span class="gs-label">上涨</span><span class="gs-val up">${gsUpCnt}</span></div>`,
    `<div class="gs-item"><span class="gs-label">下跌</span><span class="gs-val down">${gsDnCnt}</span></div>`,
  ].join('');
}

// ================================================================
//  Section 3: 上证10日走势
// ================================================================
async function loadSSETrend() {
  const data = await fetchKline('sh000001', 'day');
  if (!data || !data.klines || data.klines.length === 0) return;
  _marketData.sseKline = data;

  const klines = data.klines.slice(-12); // 最近12天
  const dates = klines.map(k => k.date ? k.date.slice(5) : '');
  const closes = klines.map(k => parseFloat(k.last || k.c || 0));

  // 计算5日均线
  const ma5 = closes.map((_, i) => {
    if (i < 4) return null;
    return closes.slice(i - 4, i + 1).reduce((a, b) => a + b, 0) / 5;
  });

  const dom = document.getElementById('sse-trend-chart');
  if (!_charts.sseTrend) _charts.sseTrend = echarts.init(dom);
  _charts.sseTrend.setOption({
    grid: { left: 40, right: 14, top: 20, bottom: 24 },
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(10,14,26,0.9)', borderColor: 'rgba(255,255,255,0.06)', textStyle: { color: '#e8edf5', fontSize: 11 } },
    xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } }, axisLabel: { color: '#6b7a99', fontSize: 9 } },
    yAxis: { type: 'value', scale: true, axisLine: { show: false }, axisLabel: { color: '#6b7a99', fontSize: 9 }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.03)' } } },
    series: [
      { name: '上证', type: 'line', data: closes, smooth: true, symbol: 'circle', symbolSize: 4,
        lineStyle: { width: 2, color: '#3b82f6' }, itemStyle: { color: '#3b82f6' },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: 'rgba(59,130,246,0.15)' }, { offset: 1, color: 'transparent' }] } } },
      { name: 'MA5', type: 'line', data: ma5, smooth: true, symbol: 'none',
        lineStyle: { width: 1, color: '#f59e0b', type: 'dashed' } }
    ]
  });
}

// ================================================================
//  核心大盘数据（/api/market-dashboard）
//  真实涨跌家数 breadth + 领涨/领跌榜 leaders/laggards，供
//  「涨跌分布」「涨幅/跌幅榜」「情绪分」共用，避免各取各的空/假数据。
// ================================================================
async function loadDashboardCore() {
  const data = await fetchMarketDashboard();
  if (!data) {
    _marketData.dashboard = null;
    _marketData.breadth = null;
    _marketData.leaders = null;
    _marketData.laggards = null;
    return null;
  }
  _marketData.dashboard = data;
  _marketData.breadth = data.breadth || null;
  _marketData.leaders = Array.isArray(data.leaders) ? data.leaders : null;
  _marketData.laggards = Array.isArray(data.laggards) ? data.laggards : null;
  _marketData.turnover = data.turnover || null;
  _marketData.coverage = data.coverage || null;
  _marketData.dashSource = data.source || '';
  _marketData.dashAsOf = data.as_of || '';
  _marketData.dashStale = data.stale === true;
  return data;
}

/**
 * 图表/区块不可用时的统一空态：销毁旧图并显示「暂不可用 + 原因」，
 * 绝不渲染 0 值冒充真实数据。
 */
function _ssShowUnavailable(chartKey, domId, msg) {
  if (chartKey && _charts[chartKey]) {
    try { _charts[chartKey].dispose(); } catch (e) { /* ignore */ }
    _charts[chartKey] = null;
  }
  const dom = document.getElementById(domId);
  if (dom) dom.innerHTML = '<div class="ss-unavailable">' + escapeHtml(msg) + '</div>';
}

/** 在指定 section 标题栏追加数据来源/时间标识（仅真实来源才展示）。 */
function _ssSetSourceChip(sectionId, text) {
  const sec = document.getElementById(sectionId);
  if (!sec) return;
  const head = sec.querySelector('.s-card-head');
  if (!head) return;
  let chip = head.querySelector('.ss-src-chip');
  if (!chip) {
    chip = document.createElement('span');
    chip.className = 'ss-src-chip';
    head.appendChild(chip);
  }
  chip.textContent = text;
}

// ================================================================
//  Section 4: 涨跌分布
// ================================================================
async function loadMarketOverview() {
  _ssRefreshColors();

  // 主源：/api/market-overview（AkShare 全市场广度，字段 up_count/down_count/flat_count）。
  // 该源在东财 push2 阻断期间返回 data=null，此时回退到 market-dashboard 的 breadth。
  let up = null, down = null, flat = null, srcText = '';

  const ov = await fetchMarketOverview();
  if (ov && ov.up_count != null) {
    up = parseInt(ov.up_count, 10) || 0;
    down = parseInt(ov.down_count, 10) || 0;
    flat = parseInt(ov.flat_count, 10) || 0;
    srcText = 'AkShare 全市场广度';
  } else if (_marketData.breadth && (_marketData.breadth.up != null || _marketData.breadth.down != null)) {
    const b = _marketData.breadth;
    up = parseInt(b.up, 10) || 0;
    down = parseInt(b.down, 10) || 0;
    flat = parseInt(b.flat, 10) || 0;
    const cov = _marketData.coverage && _marketData.coverage.valid;
    srcText = (_marketData.dashSource ? ('腾讯行情 ' + _marketData.dashSource) : '腾讯行情') +
      ' 样本 · 覆盖 ' + (cov != null ? cov : (up + down + flat)) + ' 只';
  }

  if (up == null) {
    _ssShowUnavailable('adChart', 'ad-chart',
      '涨跌分布暂不可用：/api/market-overview 返回空且 /api/market-dashboard 未含 breadth（东财 push2 阻断，无全市场涨跌家数）');
    _ssSetSourceChip('sec-ad', '数据源不可用');
    return;
  }

  const dom = document.getElementById('ad-chart');
  if (dom && dom.querySelector('.ss-unavailable')) dom.innerHTML = '';
  if (!_charts.adChart) _charts.adChart = echarts.init(dom);
  _charts.adChart.setOption({
    tooltip: { trigger: 'item', backgroundColor: 'rgba(10,14,26,0.9)', borderColor: 'rgba(255,255,255,0.06)', textStyle: { color: '#e8edf5', fontSize: 11 } },
    legend: { bottom: 0, textStyle: { color: '#8b9ec2', fontSize: 10 }, itemWidth: 10, itemHeight: 6 },
    series: [{
      type: 'pie', radius: ['40%', '65%'], center: ['50%', '42%'],
      label: { show: true, color: '#e8edf5', fontSize: 11, formatter: '{b}\n{d}%' },
      itemStyle: { borderColor: 'rgba(10,14,26,0.5)', borderWidth: 2 },
      data: [
        { value: up, name: '上涨', itemStyle: { color: _ssUpColor } },
        { value: down, name: '下跌', itemStyle: { color: _ssDownColor } },
        { value: flat, name: '平盘', itemStyle: { color: '#6b7a99' } },
      ]
    }]
  }, true);
  _charts.adChart.resize();

  _ssSetSourceChip('sec-ad', '来源 ' + srcText + ' · 涨' + up + '/跌' + down + '/平' + flat);
}

// ================================================================
//  Section 5: 北向资金
// ================================================================
async function loadNorthHolding() {
  const dom = document.getElementById('north-chart');
  if (!dom) return;

  const data = await fetchNorthHolding('sh');
  const stocks = Array.isArray(data) ? data.slice(0, 10) : [];

  if (!stocks.length) {
    /* 数据源不可用时给出明确原因，而不是留一张空白卡片。
       当前后端 /api/north-holding 返回 ok:false（westock CLI 链路已废弃），
       request() 会把 reason 吞掉，故此处直接读一次原始信封。 */
    let reason = '北向资金数据暂不可用';
    try {
      const r = await dsFetch('/api/north-holding?market=sh', 30); /* v74 · 链路废弃：长缓存兜底 */
      const j = await r.json();
      if (j && j.reason) reason = j.reason;
      else if (j && j.message) reason = j.message;
    } catch (e) { /* 保留默认文案 */ }
    if (_charts.northChart) { _charts.northChart.dispose(); _charts.northChart = null; }
    /* 数据都没有，LIVE 角标会误导，改为「未接入」 */
    const card = dom.closest('.s-card');
    const badge = card ? card.querySelector('.live-tag') : null;
    if (badge) {
      badge.textContent = '未接入';
      badge.classList.add('is-off');
      badge.title = reason;
    }
    dom.innerHTML = '<div class="hm-empty">' + escapeHtml(reason) + '</div>';
    /* v77 · 整节收起：section 级隐藏 + 密集回流，网格不留空洞 */
    var deadCard = dom.closest('.s-card');
    if (deadCard) {
      var sec = deadCard.closest('section') || deadCard;
      sec.style.display = 'none';
      sec.dataset.dead = '1';
      var flow = sec.parentElement;
      while (flow && flow !== document.body) {
        var dsp = getComputedStyle(flow).display;
        if (dsp.indexOf('grid') >= 0 || dsp.indexOf('flex') >= 0) {
          if (dsp.indexOf('grid') >= 0) flow.style.gridAutoFlow = 'row dense';
          break;
        }
        flow = flow.parentElement;
      }
    }
    return;
  }

  _marketData.northHolding = data;
  const names = stocks.map(s => s.name || '');
  const codes = stocks.map(s => (s.code || '').replace(/^(sh|sz)/, ''));

  if (dom.querySelector('.hm-empty')) dom.innerHTML = '';
  if (!_charts.northChart || !dom.querySelector('canvas')) {
    if (_charts.northChart) { _charts.northChart.dispose(); _charts.northChart = null; }
    _charts.northChart = echarts.init(dom);
  }
  _charts.northChart.setOption({
    grid: { left: 80, right: 14, top: 10, bottom: 24 },
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(10,14,26,0.9)', borderColor: 'rgba(255,255,255,0.06)', textStyle: { color: '#e8edf5', fontSize: 11 } },
    xAxis: { type: 'category', data: names, axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } }, axisLabel: { color: '#6b7a99', fontSize: 9, rotate: 30 } },
    yAxis: { type: 'value', show: false },
    series: [{
      type: 'bar', data: codes.map((c, i) => ({ value: stocks.length - i, name: names[i] })),
      barMaxWidth: 16,
      itemStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [{ offset: 0, color: 'rgba(0,233,106,0.4)' }, { offset: 1, color: 'rgba(0,233,106,0.08)' }] },
        borderRadius: [4, 4, 0, 0] }
    }]
  });
}

// ================================================================
//  Section 6: 资金流向概览
// ================================================================
async function loadFundFlow() {
  const data = await fetchSectorFundFlow('semiconductor');
  const box = document.getElementById('ff-stats');
  if (!data) {
    /* 用空态而非 loading-skeleton（后者会被误读为仍在加载），并尽量给出后端原因 */
    let reason = '板块资金流暂不可用';
    try {
      const r = await dsFetch('/api/fund-flow/sector?sector=semiconductor', 5); /* v74 · 上游超时：5min 缓存兜底 */
      const j = await r.json();
      if (j && (j.message || j.reason)) reason = j.message || j.reason;
    } catch (e) { /* 保留默认文案 */ }
    if (box) box.innerHTML = '<div class="hm-empty">' + escapeHtml(reason) + '</div>';
    return;
  }
  _marketData.fundFlow = data;

  // 从返回数据中提取资金流信息
  let netInflow = 0, shMarket = 0, szMarket = 0, consecutive = null;
  if (data.summary) {
    netInflow = parseFloat(data.summary.net_inflow || 0) || 0;
    // 注意：后端按代码前缀聚合的是「沪市/深市」，并非「沪股通/深股通」，
    // 标签须如实标注（原实现标为沪深股通属事实错误）。
    shMarket = parseFloat(data.summary.sh_inflow || 0) || 0;
    szMarket = parseFloat(data.summary.sz_inflow || 0) || 0;
    consecutive = data.summary.consecutive_days == null
      ? null : parseInt(data.summary.consecutive_days, 10);
  } else if (Array.isArray(data) && data.length > 0) {
    const item = data[0];
    netInflow = parseFloat(item.net_amount || item['主力净流入'] || 0) || 0;
  }

  const fmtVal = function(v) {
    if (Math.abs(v) >= 100000000) return (v / 100000000).toFixed(2) + '亿';
    if (Math.abs(v) >= 10000) return (v / 10000).toFixed(2) + '万';
    return v.toFixed(0);
  };
  const cls = function(v) { return v > 0 ? 'up' : v < 0 ? 'down' : 'flat'; };

  const cards = [
    `<div class="ff-item"><div class="ff-label">板块主力净流入</div><div class="ff-val ${cls(netInflow)}">${netInflow >= 0 ? '+' : ''}${fmtVal(netInflow)}</div></div>`,
    `<div class="ff-item"><div class="ff-label">沪市</div><div class="ff-val ${cls(shMarket)}">${shMarket >= 0 ? '+' : ''}${fmtVal(shMarket)}</div></div>`,
    `<div class="ff-item"><div class="ff-label">深市</div><div class="ff-val ${cls(szMarket)}">${szMarket >= 0 ? '+' : ''}${fmtVal(szMarket)}</div></div>`,
  ];
  // 无连续净流入数据时如实隐藏该卡，避免渲染成「0日」造成误读
  if (consecutive !== null && !Number.isNaN(consecutive)) {
    cards.push(`<div class="ff-item"><div class="ff-label">连续净流入</div><div class="ff-val">${consecutive}日</div></div>`);
  }

  document.getElementById('ff-stats').innerHTML = cards.join('');
}

// ================================================================
//  Section 7: 板块热力图
// ================================================================
async function loadSectorHeatmap() {
  _ssRefreshColors();
  const grid = document.getElementById('heatmap-grid');
  if (!grid) return;

  /* 用信封版本读取，才能区分「实时」与「降级快照」。
     原实现无论数据新旧都挂 LIVE 角标，而 /api/sector-ranking 常返回
     degraded=true 的历史快照，角标与实际状态不符。 */
  const env = await fetchSectorRankingEnvelope();
  const raw = env ? (env.data != null ? env.data : env.rows) : null;
  /* request()/requestEnvelope() 已把信封解包，/api/sector-ranking 的 data 本身就是数组。
     原实现判断 data.sectors —— 恒为 undefined，于是直接 return，
     热力图永久停在「加载中...」、排行图也拿不到 _marketData.sectors。 */
  const list = Array.isArray(raw)
    ? raw
    : (raw && Array.isArray(raw.sectors)) ? raw.sectors : null;

  const card = grid.closest('.s-card');
  const badge = card ? card.querySelector('.live-tag') : null;
  if (badge) {
    if (env && env.degraded) {
      badge.textContent = '快照';
      badge.classList.add('is-off');
      badge.title = env.source || '数据源降级：展示最近一次成功快照';
    } else {
      badge.textContent = 'LIVE';
      badge.classList.remove('is-off');
      badge.removeAttribute('title');
    }
  }

  if (!list || !list.length) {
    grid.innerHTML = '<div class="hm-empty">板块行情暂不可用（/api/sector-ranking 无返回）</div>';
    renderSectorRankChart();
    return;
  }
  _marketData.sectors = list;

  const _focus = _ssFocusSector();
  grid.innerHTML = list.map(function(s) {
    const chg = parseFloat(s['5日%'] || s['5d_pct'] || 0) || 0;
    let bg, textClr;
    // 涨=红 / 跌=绿（色值取自主题变量，透明度派生）
    if (chg > 5) { bg = _ssRgba(_ssUpColor, 0.25); textClr = _ssUpColor; }
    else if (chg > 0) { bg = _ssRgba(_ssUpColor, 0.05 + chg * 0.04); textClr = _ssUpColor; }
    else if (chg > -5) { bg = _ssRgba(_ssDownColor, 0.05 + Math.abs(chg) * 0.04); textClr = _ssDownColor; }
    else { bg = _ssRgba(_ssDownColor, 0.25); textClr = _ssDownColor; }

    /* 板块 → 仪表盘深链：只有能在 26 个自选板块里找到对应的才可点，
       找不到的不做假跳转（避免点进去落到一个不相干的板块）。 */
    const sName = s.name || s['名称'] || '';
    const sKey = mapToSector(sName);
    const isFocus = !!(sKey && _focus && sKey === _focus);
    const cls = 'hm-cell' + (sKey ? ' is-linked' : '') + (isFocus ? ' is-focus' : '');
    const tip = sName
      + ' · 今日 ' + (parseFloat(s.chg) >= 0 ? '+' : '') + (parseFloat(s.chg) || 0).toFixed(2) + '%'
      + ' · 5日 ' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%'
      + (sKey ? '　→ 点击查看「' + (SECTOR_KEY_NAME[sKey] || sKey) + '」成分股' : '　（未纳入仪表盘自选板块）');

    return '<div class="' + cls + '"' + (sKey ? ' data-sector="' + escapeHtml(sKey) + '"' : '') +
      ' style="background:' + bg + ';color:' + textClr + '" title="' + escapeHtml(tip) + '">' +
      '<div class="hm-name">' + escapeHtml(sName) + '</div>' +
      '<div class="hm-pct">' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%</div>' +
      '<div class="hm-vol">5日' + (sKey ? '<span class="hm-link-dot" title="可跳转仪表盘">↗</span>' : '') + '</div>' +
      '</div>';
  }).join('');

  _ssRenderFocusBar(grid, list, _focus);
  _ssWireHeatmapClicks(grid);
}

/** 顶部聚焦条：从仪表盘带 ?sector= 进来时，告诉用户「正在看哪个板块」 */
function _ssRenderFocusBar(grid, list, focusKey) {
  const host = grid.parentNode;
  if (!host) return;
  let bar = document.getElementById('hm-focus');
  if (!focusKey) { if (bar) bar.remove(); return; }

  // 统计该自选板块在东财口径下命中了几个行业板块
  let hits = 0;
  list.forEach(function (s) { if (mapToSector(s.name || s['名称'] || '') === focusKey) hits++; });

  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'hm-focus';
    bar.className = 'hm-focus';
    host.insertBefore(bar, grid);
  }
  bar.innerHTML = '<span class="hm-focus-tag">聚焦板块</span>'
    + '<b>' + escapeHtml(SECTOR_KEY_NAME[focusKey] || focusKey) + '</b>'
    + '<span class="hm-focus-sub">东财口径命中 ' + hits + ' 个行业板块'
    + (hits ? '（下方高亮）' : '（当前快照无对应行业，仅保留筛选条件）') + '</span>'
    + '<a class="hm-focus-act" href="/dashboard.html?sector=' + encodeURIComponent(focusKey) + '">回仪表盘 →</a>'
    + '<button class="hm-focus-x" type="button" title="清除聚焦">×</button>';
  bar.querySelector('.hm-focus-x').onclick = function () {
    try {
      const u = new URL(location.href);
      u.searchParams.delete('sector');
      history.replaceState(null, '', u.pathname + (u.search || ''));
    } catch (e) { /* 忽略 */ }
    bar.remove();
    document.querySelectorAll('.hm-cell.is-focus').forEach(function (el) { el.classList.remove('is-focus'); });
  };
}

/** 热力格点击 → 仪表盘对应板块（事件委托，只注册一次） */
function _ssWireHeatmapClicks(grid) {
  if (grid.dataset.wired === '1') return;
  grid.dataset.wired = '1';
  grid.addEventListener('click', function (e) {
    const cell = e.target.closest('.hm-cell[data-sector]');
    if (!cell) return;
    const key = cell.getAttribute('data-sector');
    if (!key) return;
    location.href = '/dashboard.html?sector=' + encodeURIComponent(key);
  });
}

// ================================================================
//  Section 8: 板块涨跌排行
// ================================================================
function renderSectorRankChart() {
  _ssRefreshColors();
  const dom = document.getElementById('sector-rank-chart');
  if (!dom) return;

  const all = Array.isArray(_marketData.sectors) ? _marketData.sectors.slice() : [];
  if (!all.length) {
    dom.innerHTML = '<div class="hm-empty">板块涨跌数据暂不可用</div>';
    return;
  }

  /* 按「今日涨跌幅」排序，取涨幅端 10 个 + 跌幅端 10 个。
     原实现取 API 顺序前 15 个，而 API 已按涨幅降序 —— 等于只画了涨幅榜，
     跌幅端完全缺失，与「涨跌排行」的标题不符。 */
  const val = function (s) { return parseFloat(s.chg != null ? s.chg : s.change_pct) || 0; };
  all.sort(function (a, b) { return val(b) - val(a); });   // 降序
  const sectors = all.length <= 20
    ? all
    : all.slice(0, 10).concat(all.slice(-10));

  const names = sectors.map(function (s) { return s.name || s['名称'] || ''; });
  const vals = sectors.map(val);

  if (!_charts.sectorRank) _charts.sectorRank = echarts.init(dom);
  _charts.sectorRank.setOption({
    grid: { left: 80, right: 30, top: 10, bottom: 24 },
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(10,14,26,0.9)', borderColor: 'rgba(255,255,255,0.06)', textStyle: { color: '#e8edf5', fontSize: 11 } },
    xAxis: { type: 'value', axisLine: { show: false }, axisLabel: { color: '#6b7a99', fontSize: 9, formatter: '{value}%' }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.03)' } } },
    yAxis: { type: 'category', data: names.reverse(), axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } }, axisLabel: { color: '#8b9ec2', fontSize: 10 } },
    series: [{
      type: 'bar', data: vals.reverse().map(function(v) { return { value: v, itemStyle: { color: v >= 0 ? _ssUpColor : _ssDownColor, borderRadius: [0, 3, 3, 0] } }; }),
      barMaxWidth: 14,
      label: { show: true, position: 'right', color: '#8b9ec2', fontSize: 9, formatter: function(p) { return (p.value >= 0 ? '+' : '') + p.value.toFixed(2) + '%'; } }
    }]
  });
}

// ================================================================
//  Section 9: 涨幅TOP20
// ================================================================
/**
 * Section 9: 涨幅/跌幅 TOP20
 * 数据源改为 /api/market-dashboard 的 leaders / laggards（腾讯行情真实值，
 * 字段 chg / price）。原 hot-stocks 在数据源降级时返回「本地样本」合成数据，
 * 不能当作真实涨跌幅展示，故不再用于本榜。
 */
async function loadTopGainers() {
  const leaders = _marketData.leaders;
  const laggards = _marketData.laggards;

  if (!leaders && !laggards) {
    document.getElementById('gainers-body').innerHTML =
      '<tr><td colspan="5" class="loading-cell">涨幅榜暂不可用：/api/market-dashboard 未返回 leaders</td></tr>';
    document.getElementById('losers-body').innerHTML =
      '<tr><td colspan="5" class="loading-cell">跌幅榜暂不可用：/api/market-dashboard 未返回 laggards</td></tr>';
    renderSectorRankChart();
    return;
  }

  renderGainersTable(leaders || []);
  renderLosersTable(laggards || []);
  renderSectorRankChart();
}

/* ===== 榜单行 → 策略看板深链 =====
   链接形如 /dashboard.html?code=sz002185，由 app.js 的 _resolveDeepLink()
   解析板块并自动选中该标的。原实现榜单为纯展示，无法点进去看详情。 */
function stockRowAttrs(s) {
  const code = String((s && s.code) || '').trim();
  if (!code) return '';
  return ' data-href="/dashboard.html?code=' + encodeURIComponent(code) + '"';
}

document.addEventListener('click', function (e) {
  if (!e.target || !e.target.closest) return;
  const tr = e.target.closest('tr[data-href]');
  if (!tr) return;
  const href = tr.getAttribute('data-href');
  if (href) window.location.href = href;
});

function renderGainersTable(stocks) {
  const body = document.getElementById('gainers-body');
  if (!stocks || stocks.length === 0) {
    body.innerHTML = '<tr><td colspan="5" class="loading-cell">暂无数据</td></tr>';
    return;
  }
  body.innerHTML = stocks.slice(0, 20).map(function(s, i) {
    // 真实字段为 chg / price（保留 zdf / zxj 兜底以兼容旧结构）
    const zdf = parseFloat(s.chg != null ? s.chg : (s.zdf || 0));
    const cls = zdf >= 0 ? 'up' : 'down';
    return '<tr' + stockRowAttrs(s) + '>' +
      '<td class="rank">' + (i + 1) + '</td>' +
      '<td class="stock-name">' + escapeHtml(s.name || '') + '</td>' +
      '<td class="stock-code">' + escapeHtml((s.code || '').replace(/^(sh|sz)/, '')) + '</td>' +
      '<td class="' + cls + '">' + (zdf >= 0 ? '+' : '') + zdf.toFixed(2) + '%</td>' +
      '<td class="mono">' + safeFixed(s.price != null ? s.price : s.zxj, 2) + '</td>' +
      '</tr>';
  }).join('');
}

function renderLosersTable(stocks) {
  const body = document.getElementById('losers-body');
  if (!stocks || stocks.length === 0) {
    body.innerHTML = '<tr><td colspan="5" class="loading-cell">暂无数据</td></tr>';
    return;
  }
  body.innerHTML = stocks.slice(0, 20).map(function(s, i) {
    const zdf = parseFloat(s.chg != null ? s.chg : (s.zdf || 0));
    const cls = zdf >= 0 ? 'up' : 'down';
    return '<tr' + stockRowAttrs(s) + '>' +
      '<td class="rank">' + (i + 1) + '</td>' +
      '<td class="stock-name">' + escapeHtml(s.name || '') + '</td>' +
      '<td class="stock-code">' + escapeHtml((s.code || '').replace(/^(sh|sz)/, '')) + '</td>' +
      '<td class="' + cls + '">' + (zdf >= 0 ? '+' : '') + zdf.toFixed(2) + '%</td>' +
      '<td class="mono">' + safeFixed(s.price != null ? s.price : s.zxj, 2) + '</td>' +
      '</tr>';
  }).join('');
}

// ================================================================
//  Section 11: 三锚点信号塔
// ================================================================
function loadAnchors() {
  let volPass = false, maPass = false, sentimentStatus = 'warn';

  // 量能锚：以真实市场成交额判定（/api/market-dashboard turnover.amount）
  if (_marketData.turnover && parseFloat(_marketData.turnover.amount) > 0) {
    volPass = true;
  }

  // 均线锚：检查上证是否站上5日均线
  if (_marketData.sseKline && _marketData.sseKline.klines) {
    const klines = _marketData.sseKline.klines;
    if (klines.length >= 5) {
      const lastClose = parseFloat(klines[klines.length - 1].last || klines[klines.length - 1].c || 0);
      const ma5 = klines.slice(-5).reduce(function(sum, k) { return sum + parseFloat(k.last || k.c || 0); }, 0) / 5;
      maPass = lastClose >= ma5;
    }
  }

  // 情绪锚：检查真实涨跌比（/api/market-dashboard breadth）
  if (_marketData.breadth) {
    const up = parseInt(_marketData.breadth.up, 10) || 0;
    const down = parseInt(_marketData.breadth.down, 10) || 0;
    if (up + down > 0) {
      const ratio = up / (up + down);
      if (ratio > 0.6) sentimentStatus = 'pass';
      else if (ratio < 0.4) sentimentStatus = 'fail';
      else sentimentStatus = 'warn';
    }
  }

  const anchors = [
    { icon: '', name: '量能锚', status: volPass ? 'pass' : 'warn', statusText: volPass ? '信号通过' : '量能不足',
      desc: volPass ? '市场交投活跃，成交额充足' : '量能萎缩，需关注成交变化' },
    { icon: '', name: '均线锚', status: maPass ? 'pass' : 'fail', statusText: maPass ? '信号通过' : '均线破位',
      desc: maPass ? '上证站上5日均线，短期趋势偏多' : '上证跌破5日均线，短期趋势偏空' },
    { icon: '', name: '情绪锚', status: sentimentStatus,
      statusText: sentimentStatus === 'pass' ? '情绪偏多' : sentimentStatus === 'fail' ? '注意分歧' : '多空平衡',
      desc: sentimentStatus === 'pass' ? '涨跌比偏多，市场情绪积极' : sentimentStatus === 'fail' ? '涨跌比偏空，高位股补跌风险' : '多空力量均衡，建议观望' },
  ];

  document.getElementById('anchor-grid').innerHTML = anchors.map(function(a) {
    return '<div class="anchor-item">' +
      '<div class="anchor-icon">' + a.icon + '</div>' +
      '<div class="anchor-name">' + a.name + '</div>' +
      '<span class="anchor-status ' + a.status + '">' + a.statusText + '</span>' +
      '<div class="anchor-desc">' + a.desc + '</div>' +
      '</div>';
  }).join('');
}

// ================================================================
//  Section 12: 竞价剧本预判
// ================================================================
function loadPrediction() {
  let score = 50;
  if (_marketData.breadth) {
    const up = parseInt(_marketData.breadth.up, 10) || 0;
    const down = parseInt(_marketData.breadth.down, 10) || 0;
    if (up + down > 0) score = up / (up + down) * 100;
  }
  if (_marketData.global && _marketData.global.indices && _marketData.global.indices.sh) {
    const chg = parseFloat(_marketData.global.indices.sh.chg || 0);
    score += chg * 5;
  }
  score = Math.max(0, Math.min(100, score));

  let bullPct, neutralPct, bearPct;
  if (score > 65) { bullPct = 45; neutralPct = 40; bearPct = 15; }
  else if (score > 45) { bullPct = 30; neutralPct = 45; bearPct = 25; }
  else { bullPct = 15; neutralPct = 35; bearPct = 50; }

  const predictions = [
    { cls: 'bull', icon: '', name: '强做多', pct: bullPct, desc: '指数高开+北向流入' },
    { cls: 'neutral', icon: '', name: '震荡', pct: neutralPct, desc: '涨跌分歧+量能减弱' },
    { cls: 'bear', icon: '', name: '防守', pct: bearPct, desc: '跌停增加+北向流出' },
  ];

  document.getElementById('predict-grid').innerHTML = predictions.map(function(p) {
    return '<div class="predict-item ' + p.cls + '">' +
      '<div class="predict-icon">' + p.icon + '</div>' +
      '<div class="predict-name">' + p.name + '</div>' +
      '<div class="predict-pct">' + p.pct + '%</div>' +
      '<div class="predict-desc">' + p.desc + '</div>' +
      '</div>';
  }).join('');
}

// ================================================================
//  Section 13: 仓位管理计算器
// ================================================================
function initCalculator() {
  const inputs = ['calc-capital', 'calc-price', 'calc-stoploss', 'calc-risk'];
  inputs.forEach(function(id) {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', calcPosition);
  });
  calcPosition();
}

function calcPosition() {
  const capital = parseFloat(document.getElementById('calc-capital').value) || 0;
  const price = parseFloat(document.getElementById('calc-price').value) || 0;
  const stopLossPct = parseFloat(document.getElementById('calc-stoploss').value) || 0;
  const riskPct = parseFloat(document.getElementById('calc-risk').value) || 0;

  if (capital <= 0 || price <= 0 || stopLossPct <= 0 || riskPct <= 0) {
    document.getElementById('calc-results').innerHTML = '<div class="loading-skeleton">请输入有效参数</div>';
    return;
  }

  const maxLoss = capital * riskPct / 100;
  const stopLossPrice = price * (1 - stopLossPct / 100);
  const lossPerShare = price - stopLossPrice;
  const buyShares = Math.floor(maxLoss / lossPerShare / 100) * 100; // 按手取整
  const buyAmount = buyShares * price;
  const positionPct = buyAmount / capital * 100;

  document.getElementById('calc-results').innerHTML = [
    '<div class="cr-item"><span class="cr-label">建议买入股数</span><span class="cr-val">' + buyShares.toLocaleString() + ' 股</span></div>',
    '<div class="cr-item"><span class="cr-label">建议买入金额</span><span class="cr-val">' + buyAmount.toLocaleString(undefined, { minimumFractionDigits: 2 }) + ' 元</span></div>',
    '<div class="cr-item"><span class="cr-label">仓位占比</span><span class="cr-val">' + positionPct.toFixed(1) + '%</span></div>',
    '<div class="cr-item"><span class="cr-label">止损价</span><span class="cr-val">' + stopLossPrice.toFixed(2) + ' 元</span></div>',
    '<div class="cr-item"><span class="cr-label">最大亏损</span><span class="cr-val" style="color:' + _ssRiskColor() + '">' + maxLoss.toLocaleString(undefined, { minimumFractionDigits: 2 }) + ' 元</span></div>',
  ].join('');
}

// ================================================================
//  Section 14: 八条铁律
// ================================================================
function renderRules() {
  const rules = [
    { num: 1, text: '铁律止损：亏损超过5%无条件止损，绝不补仓摊平' },
    { num: 2, text: '拒绝追高：涨停板不开板不追，一字板次日谨慎' },
    { num: 3, text: '仓位控制：单只个股不超过总仓位30%，总仓位不超过80%' },
    { num: 4, text: '竞价观察：9:20-9:25观察竞价量价，大单优先' },
    { num: 5, text: '龙头优先：只做板块龙头，龙二龙三谨慎参与' },
    { num: 6, text: '分时低吸：回踩分时均线企稳再入，不追直线' },
    { num: 7, text: '情绪周期：退潮期空仓，冰点期试仓，高潮期减仓' },
    { num: 8, text: '复利为王：日盈1%年翻12倍，稳定比暴利更重要' },
  ];
  document.getElementById('rules-grid').innerHTML = rules.map(function(r) {
    return '<div class="rule-item">' +
      '<div class="rule-num">' + r.num + '</div>' +
      '<div class="rule-text">' + r.text + '</div>' +
      '</div>';
  }).join('');
}

// ================================================================
//  时钟 + 市场状态
// ================================================================
function startClock() {
  updateClock();
  setInterval(updateClock, 1000);
}

function updateClock() {
  const now = new Date();
  const h = String(now.getHours()).padStart(2, '0');
  const m = String(now.getMinutes()).padStart(2, '0');
  const s = String(now.getSeconds()).padStart(2, '0');
  const clockEl = document.getElementById('header-clock');
  if (clockEl) clockEl.textContent = h + ':' + m + ':' + s;

  // 市场状态
  const badge = document.getElementById('ms-badge');
  if (badge) {
    const day = now.getDay();
    const hour = now.getHours();
    const minute = now.getMinutes();
    const timeVal = hour * 100 + minute;
    const isTrading = (day >= 1 && day <= 5) && ((timeVal >= 930 && timeVal <= 1130) || (timeVal >= 1300 && timeVal <= 1500));
    if (isTrading) {
      badge.className = 'market-status-badge';
      badge.innerHTML = '<span class="ms-dot"></span>交易中';
    } else {
      badge.className = 'market-status-badge closed';
      badge.innerHTML = '<span class="ms-dot"></span>已收盘';
    }
  }
}

// ================================================================
//  自动刷新
// ================================================================
function startAutoRefresh() {
  if (_refreshTimer) clearInterval(_refreshTimer);
  const interval = isTradingHours() ? 30000 : 120000; // 盘中30秒，盘后2分钟
  _refreshTimer = setInterval(function() {
    loadGlobalIndices();
    loadSSETrend();
    loadSectorHeatmap();
    loadNorthHolding();
    // 大盘核心数据先就绪，再刷新依赖它的区块
    loadDashboardCore().then(function() {
      loadMarketOverview();
      loadTopGainers();
      loadSentimentGauge();
      loadAnchors();
      loadPrediction();
    });
  }, interval);
}

function refreshAll() {
  // 手动刷新：重新加载所有数据
  loadGlobalIndices();
  loadSSETrend();
  loadSectorHeatmap();
  loadNorthHolding();
  loadFundFlow();
  loadDashboardCore().then(function() {
    loadMarketOverview();
    loadTopGainers();
    loadSentimentGauge();
    loadAnchors();
    loadPrediction();
  });
}

// ================================================================
//  窗口 resize 处理
// ================================================================
window.addEventListener('resize', function() {
  Object.values(_charts).forEach(function(c) {
    if (c) c.resize();
  });
});

// ================================================================
//  启动
// ================================================================
initSentiment();


/* ================================================================
 *  Section: 数据源状态条（v57）——让"数据不足"自解释：哪个源、几条、多快
 * ================================================================ */
(function () {
  var style = document.createElement('style');
  style.textContent = '.ds-strip{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 14px;padding:8px 12px;border:1px solid rgba(120,140,170,.18);border-radius:6px;font:11px/1.5 "Segoe UI",system-ui,sans-serif;color:#9aa7b8;background:rgba(10,14,22,.5)}' +
    '.ds-strip .ds-t{color:#d4ae7d;letter-spacing:.08em;font-weight:600;margin-right:4px}' +
    '.ds-item{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:10px;border:1px solid rgba(120,140,170,.15)}' +
    '.ds-item i{width:6px;height:6px;border-radius:50%;background:#39d98a;flex:none}' +
    '.ds-item.is-bad{color:#c98a90}.ds-item.is-bad i{background:#e06c75}';
  document.head.appendChild(style);
  var SRC = [
    { name: '全球指数', url: '/api/global' },
    { name: '市场情绪', url: '/api/market-sentiment' },
    { name: '板块热力', url: '/api/sector-ranking' },
    { name: '北向资金', url: '/api/north-holding?market=sh' },
    { name: '资金流', url: '/api/fund-flow/sector?sector=semiconductor' },
    { name: '市场概览', url: '/api/market-overview' }
  ];
  function countItems(d) {
    if (Array.isArray(d)) return d.length;
    if (d && typeof d === 'object') {
      for (var k in d) if (Array.isArray(d[k])) return d[k].length;
      var n = 0;
      for (var k2 in d) if (d[k2] !== null && d[k2] !== undefined) n++;
      return n;
    }
    return d ? 1 : 0;
  }
  function probe(src) {
    var t0 = performance.now();
    return fetch(src.url, { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var ok = !!(j && j.ok !== false);
        var d = j && (j.data !== undefined ? j.data : j);
        return { name: src.name, ok: ok, n: countItems(d), ms: Math.round(performance.now() - t0) };
      })
      .catch(function () { return { name: src.name, ok: false, n: 0, ms: Math.round(performance.now() - t0) }; });
  }
  var lastGood = {};                                    /* v69 · 失败保留旧值（缓存兜底） */
  function render(rows) {
    var el = document.getElementById('ds-strip');
    if (!el) return;
    rows.forEach(function (r) {
      if (r.ok && r.n) { r.stale = false; lastGood[r.name] = { n: r.n, ms: r.ms }; }
      else if (lastGood[r.name]) { r.ok = true; r.n = lastGood[r.name].n + ' (缓存)'; r.ms = lastGood[r.name].ms; r.stale = true; }
    });
    var good = rows.filter(function (r) { return r.ok && !r.stale; }).length;
    el.innerHTML = '<span class="ds-t">数据源 ' + good + '/' + rows.length + '</span>' +
      rows.map(function (r) {
        var bad = !(r.ok && r.n);
        var tag = r.stale ? ' · 缓存' : '';
        return '<span class="ds-item' + (bad ? ' is-bad' : '') + '" title="' + r.name + '：' +
          (bad ? '无数据' : (r.stale ? '缓存兜底 ' : '正常 ') + r.n + ' 条') + ' · ' + r.ms + 'ms"><i></i>' + r.name + ' ' + (bad ? '×' : r.n) + '</span>';
      }).join('');
  }
  function run() { Promise.all(SRC.map(probe)).then(render); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(run, 300); });
  } else { setTimeout(run, 300); }
  setInterval(run, 120000); /* v69 · 轮询 60s→120s：降上游负载 */
})();


/* ===== v74 · 数据接入配置层：dsFetch 会话级缓存取数 =====
   成功 → 写 sessionStorage；失败/空数据 → 回落最近一次成功（面板拿真实结构继续渲染，不再死显错误） */
function dsFetch(url, ttlMin) {
  var ttl = (ttlMin || 5) * 60000;
  var key = 'dscache:' + url;
  return fetch(url, { cache: 'no-store' })
    .then(function (r) { return r.json(); })
    .then(function (j) {
      var ok = !!(j && j.ok !== false && j.data !== null && j.data !== undefined);
      if (ok) { try { sessionStorage.setItem(key, JSON.stringify({ t: Date.now(), j: j })); } catch (e) {} }
      return { ok: true, json: function () { return Promise.resolve(j); } };
    })
    .catch(function () {
      try {
        var raw = sessionStorage.getItem(key);
        if (raw) {
          var c = JSON.parse(raw);
          if (Date.now() - c.t < ttl * 3) return { ok: true, json: function () { return Promise.resolve(c.j); } };
        }
      } catch (e) {}
      return { ok: false, json: function () { return Promise.resolve({ ok: false, data: null }); } };
    });
}