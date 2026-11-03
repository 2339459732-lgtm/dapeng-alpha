/**
 * DAPENG Alpha v5.4 - 市场数据渲染模块
 *
 * v5.4 升级：
 * - 热门股表格增加"详情"按钮 + 股票简况弹卡
 * - 龙虎榜增加日期选择器
 * - ESC 键监听器扩展覆盖 6 个全屏面板
 *
 * P0: 资金流向 / 板块排行 / 龙虎榜TOP5 / 市场总览 / 热门股
 * P1: 个股简况 / 研报 / 北向资金 / 资讯
 *
 * v6.2 升级：
 * - 新增 isHKStock() / isAStock() 股票类型检测
 * - 港股在"北向资金"等不适用Tab中显示"暂不支持"
 * - 各Tab加载函数增加 try-catch 保护
 */

// ================================================================
//  数据源降级横幅（本地样本 vs 实时行情）
// ================================================================

/**
 * 渲染/清除数据源降级横幅。
 *
 * 后端信封里的 degraded 依据数据真实来源推导：任一行 synthetic=true 时为 true。
 * 前端据此在面板顶部显示醒目提示；degraded=false 时自动移除横幅。
 *
 * @param {string} panelId - 面板根元素 ID，横幅插入其 .full-overlay-body 顶部
 * @param {boolean} degraded - 信封 degraded 标志
 * @param {string} [sourceText] - 数据来源名（可选，附于文案后便于核对）
 */
function renderDegradedBanner(panelId, degraded, sourceText) {
  var panel = document.getElementById(panelId);
  if (!panel) return;
  var host = panel.querySelector('.full-overlay-body') || panel;
  var existing = host.querySelector('.degraded-banner');
  if (!degraded) {
    if (existing) existing.remove();
    return;
  }
  // 三种降级性质要区分：腾讯成分股聚合 / 历史快照 / 本地样本
  var src = sourceText || '';
  var isAgg = /腾讯行情聚合|聚合口径/.test(src);
  var isStale = /历史快照/.test(src);
  var text;
  if (isAgg) {
    text = '⚠ 数据源降级：当前展示为腾讯成分股聚合口径（东财板块源不可用），非东财原生板块行情';
  } else if (isStale) {
    text = '⚠ 数据源降级：当前展示为历史快照数据，非实时行情';
  } else {
    text = '⚠ 数据源降级：当前展示为本地样本数据，非实时行情';
  }
  if (src) text += '（来源：' + escapeHtml(src) + '）';
  var html = '<div class="degraded-banner" role="alert">' +
    '<span class="degraded-banner-icon">⚠</span>' +
    '<span class="degraded-banner-text">' + text + '</span>' +
    '</div>';
  if (existing) {
    existing.outerHTML = html;
  } else {
    host.insertAdjacentHTML('afterbegin', html);
  }
}

// ================================================================
//  股票类型检测工具
// ================================================================

/** 判断是否为港股 */
function isHKStock(code) {
  if (!code) return false;
  return String(code).toLowerCase().indexOf('hk') === 0;
}

/** 判断是否为 A 股 */
function isAStock(code) {
  if (!code) return false;
  var c = String(code).toLowerCase();
  return c.indexOf('sh') === 0 || c.indexOf('sz') === 0 || c.indexOf('bj') === 0;
}

/** Tab不适用时展示的统一提示 */
function showNotSupported(panelId, reason) {
  var panel = document.getElementById(panelId);
  if (!panel) return;
  panel.innerHTML = '<div class="not-supported">' +
    '<div class="not-supported-icon">&#x1F6AB;</div>' +
    '<div>' + (reason || '暂不支持该类型股票') + '</div>' +
    '</div>';
}

/**

// ================================================================
//  工具函数
// ================================================================

/**
 * 安全格式化金额（元 → 万/亿）
 */
function _fmtAmount(val) {
  if (val == null || isNaN(val)) return '--';
  var n = Number(val);
  if (Math.abs(n) >= 1e12) return (n / 1e12).toFixed(2) + '万亿';
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + '亿';
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(0) + '万';
  return n.toFixed(0);
}

/**
 * 安全格式化百分比
 */
function _fmtPct(val) {
  if (val == null || isNaN(val)) return '--';
  var n = Number(val);
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}

/**
 * 获取资金流向值的 CSS 类（正数绿色，负数红色）
 */
function _flowCls(val) {
  if (val == null || isNaN(val)) return '';
  return Number(val) >= 0 ? 'positive' : 'negative';
}

// ================================================================
//  P0: 资金流向
// ================================================================

/**
 * 加载资金流向 Tab
 */
async function loadFundFlowTab(code) {
  var panel = document.getElementById('fund-flow-panel');
  if (!panel) return;
  if (isHKStock(code)) {
    showNotSupported('fund-flow-panel', '港股暂不支持资金流向查询');
    return;
  }
  panel.innerHTML = '<div class="loading-shimmer">加载资金流向数据...</div>';
  try {
    var data = await fetchFundFlow(code);
    renderFundFlow(data, code, panel);
  } catch (e) {
    panel.innerHTML = '<div class="error-msg">资金流向数据加载失败</div>';
    console.error('Fund flow failed:', e);
  }
}

/**
 * 渲染资金流向
 */
function renderFundFlow(data, code, panel) {
  if (!data) {
    panel.innerHTML = '<div class="empty-msg">暂无资金流向数据</div>';
    return;
  }

  // 尝试从数据中提取各档资金净额
  var mainNet = data.main_net || data['主力净流入'] || data.main || 0;
  var superNet = data.super_net || data['超大单净额'] || data.super_large || 0;
  var bigNet = data.big_net || data['大单净额'] || data.big || 0;
  var midNet = data.mid_net || data['中单净额'] || data.mid || 0;
  var smallNet = data.small_net || data['小单净额'] || data.small || 0;

  var items = [
    { label: '主力', val: mainNet },
    { label: '超大单', val: superNet },
    { label: '大单', val: bigNet },
    { label: '中单', val: midNet },
    { label: '小单', val: smallNet },
  ];

  var gridHtml = '<div class="fund-flow-grid">';
  items.forEach(function (item) {
    var cls = _flowCls(item.val);
    var inflow = item.val != null && !isNaN(item.val) && Number(item.val) >= 0;
    gridHtml += '<div class="fund-flow-item ' + (inflow ? 'inflow' : 'outflow') + '">' +
      '<div class="fund-flow-label">' + escapeHtml(item.label) + '</div>' +
      '<div class="fund-flow-value ' + cls + '">' + _fmtAmount(item.val) + '</div>' +
      '</div>';
  });
  gridHtml += '</div>';

  // 趋势数据（如果有）
  var trendHtml = '';
  var day5 = data.day5_main || data['5日主力净流入'] || null;
  var day10 = data.day10_main || data['10日主力净流入'] || null;
  var day20 = data.day20_main || data['20日主力净流入'] || null;
  if (day5 != null || day10 != null || day20 != null) {
    trendHtml = '<div class="md-section-title">主力净流入趋势</div>' +
      '<div class="md-info-grid">' +
      '<div class="md-info-card"><div class="md-info-label">5日</div><div class="md-info-value ' + _flowCls(day5) + '">' + _fmtAmount(day5) + '</div></div>' +
      '<div class="md-info-card"><div class="md-info-label">10日</div><div class="md-info-value ' + _flowCls(day10) + '">' + _fmtAmount(day10) + '</div></div>' +
      '<div class="md-info-card"><div class="md-info-label">20日</div><div class="md-info-value ' + _flowCls(day20) + '">' + _fmtAmount(day20) + '</div></div>' +
      '</div>';
  }

  panel.innerHTML = gridHtml + trendHtml;
}

// ================================================================
//  P0: 板块排行
// ================================================================

/**
 * 加载板块排行
 */
async function loadSectorRanking() {
  try {
    var data = await fetchSectorRanking('industry');
    renderSectorRanking(data);
  } catch (e) {
    console.error('Sector ranking failed:', e);
  }
}

/**
 * 渲染板块排行
 */
function renderSectorRanking(data) {
  var container = document.getElementById('sa-hot-sectors');
  if (!container || !data) return;

  var sectors = data.sectors || data.rows || data || [];
  if (!Array.isArray(sectors) || sectors.length === 0) return;

  var topN = sectors.slice(0, 8);
  var html = topN.map(function (s, i) {
    var name = s.name || s['板块'] || s.sector_name || '--';
    var chg = parseFloat(s.chg || s.change_pct || s['涨跌幅'] || 0);
    var leadStock = s.lead_stock || s['领涨股'] || s.top_stock || '';
    var chgCls = chg >= 0 ? 'up' : 'dn';
    var barWidth = Math.min(Math.abs(chg) * 20, 100);
    var barColor = chg >= 0 ? 'var(--red)' : 'var(--green)';
    var rankCls = i === 0 ? 'top1' : i === 1 ? 'top2' : i === 2 ? 'top3' : '';

    return '<div class="sector-rank-row" onclick="switchSector(\'' + escapeHtml(name) + '\')">' +
      '<div class="hot-stock-rank ' + rankCls + '">' + (i + 1) + '</div>' +
      '<div class="hot-stock-name" style="flex:1">' + escapeHtml(name) + '</div>' +
      '<div class="sector-rank-bar"><div class="sector-rank-fill" style="width:' + barWidth + '%;background:' + barColor + '"></div></div>' +
      '<div class="hot-stock-code ' + chgCls + '" style="width:60px;text-align:right">' + _fmtPct(chg) + '</div>' +
      (leadStock ? '<div class="hot-stock-code" style="width:60px;text-align:right">' + escapeHtml(leadStock) + '</div>' : '') +
      '</div>';
  }).join('');

  container.innerHTML = html;
}

// ================================================================
//  P0: 龙虎榜 TOP5
// ================================================================

/**
 * 加载龙虎榜 TOP5
 */
async function loadLHBTop5() {
  try {
    var data = await fetchLHB('jg');
    renderLHBTop5(data);
  } catch (e) {
    console.error('LHB failed:', e);
  }
}

/**
 * 渲染龙虎榜 TOP5
 */
function renderLHBTop5(data) {
  var container = document.getElementById('top-stocks-list');
  if (!container || !data) return;

  var rows = data.rows || data || [];
  if (!Array.isArray(rows) || rows.length === 0) return;

  var top5 = rows.slice(0, 5);
  var html = top5.map(function (r, i) {
    var name = r.name || r['名称'] || '--';
    var code = r.code || r['代码'] || '';
    var buyAmt = parseFloat(r.buy_amount || r.net_buy || r['净买入额'] || 0);
    var reason = r.reason || r['上榜原因'] || '';
    var tag = r.inst_count > 0 ? '机构' : '普通';

    return '<div class="lhb-stock-card" onclick="selectStock(\'' + escapeHtml(code) + '\',\'' + escapeHtml(name).replace(/'/g, "\\'") + '\')">' +
      '<div class="lhb-stock-header">' +
      '<div><span class="hot-stock-rank">' + (i + 1) + '</span>' +
      '<span class="lhb-stock-name">' + escapeHtml(name) + '</span>' +
      '<span class="hot-stock-code" style="margin-left:6px">' + escapeHtml(code) + '</span></div>' +
      '<div><span class="lhb-stock-buy">' + _fmtAmount(buyAmt) + '</span></div>' +
      '</div>' +
      '<div style="display:flex;gap:6px;align-items:center">' +
      '<span class="lhb-stock-tag">' + tag + '</span>' +
      (reason ? '<span class="hot-stock-code">' + escapeHtml(reason) + '</span>' : '') +
      '</div>' +
      '</div>';
  }).join('');

  container.innerHTML = html;
}

// ================================================================
//  P0: 市场总览
// ================================================================

/**
 * 加载市场总览
 */
async function loadMarketOverview() {
  try {
    var data = await fetchMarketOverview();
    renderMarketOverview(data);
  } catch (e) {
    console.error('Market overview failed:', e);
  }
}

/**
 * 渲染市场总览
 */
function renderMarketOverview(data) {
  var bar = document.getElementById('market-overview-bar');
  if (!bar || !data) return;

  var upCount = data.up_count || data['上涨'] || 0;
  var dnCount = data.down_count || data['下跌'] || 0;
  var flatCount = data.flat_count || data['平盘'] || 0;
  var limitUp = data.limit_up || data['涨停'] || 0;
  var limitDn = data.limit_down || data['跌停'] || 0;
  var totalAmount = data.total_amount || data['总成交额'] || 0;

  bar.innerHTML =
    '<div class="mo-stat"><div class="mo-val" style="color:var(--green)">' + upCount + '</div><div class="mo-label">上涨</div></div>' +
    '<div class="mo-divider"></div>' +
    '<div class="mo-stat"><div class="mo-val" style="color:var(--red)">' + dnCount + '</div><div class="mo-label">下跌</div></div>' +
    '<div class="mo-divider"></div>' +
    '<div class="mo-stat"><div class="mo-val" style="color:var(--text2)">' + flatCount + '</div><div class="mo-label">平盘</div></div>' +
    '<div class="mo-divider"></div>' +
    '<div class="mo-stat"><div class="mo-val" style="color:var(--green)">' + limitUp + '</div><div class="mo-label">涨停</div></div>' +
    '<div class="mo-divider"></div>' +
    '<div class="mo-stat"><div class="mo-val" style="color:var(--red)">' + limitDn + '</div><div class="mo-label">跌停</div></div>' +
    (totalAmount > 0 ? '<div class="mo-divider"></div>' +
      '<div class="mo-stat"><div class="mo-val" style="color:var(--blue)">' + _fmtAmount(totalAmount) + '</div><div class="mo-label">总成交</div></div>' : '');

  // 同时更新 #mini-flow-val
  var flowVal = document.getElementById('mini-flow-val');
  if (flowVal && totalAmount > 0) {
    var netFlow = upCount - dnCount;
    flowVal.textContent = (netFlow >= 0 ? '+' : '') + netFlow + ' 家';
    flowVal.style.color = netFlow >= 0 ? 'var(--red)' : 'var(--green)';
  }
}

// ================================================================
//  P0: 热门股
// ================================================================

/**
 * 加载热门股
 */
async function loadHotStocks() {
  try {
    var data = await fetchHotStocks('stock', 10);
    renderHotStocks(data);
  } catch (e) {
    console.error('Hot stocks failed:', e);
  }
}

/**
 * 渲染热门股排行列表
 */
function renderHotStocks(data) {
  // 尝试在板块分析面板的 top-stocks-list 或单独面板中渲染
  var container = document.getElementById('top-stocks-list');
  if (!container || !data) return;

  var stocks = Array.isArray(data) ? data : (data.stocks || data.rows || []);
  if (!Array.isArray(stocks) || stocks.length === 0) return;

  var topN = stocks.slice(0, 10);
  var html = topN.map(function (s, i) {
    var name = s.name || s['名称'] || '--';
    var code = s.code || s['代码'] || '';
    var chg = parseFloat(s.chg || s.change_pct || s['涨跌幅'] || 0);
    var price = parseFloat(s.price || s['现价'] || 0);
    var chgCls = chg >= 0 ? 'up' : 'dn';

    return '<div class="hot-stock-row" onclick="selectStock(\'' + escapeHtml(code) + '\',\'' + escapeHtml(name).replace(/'/g, "\\'") + '\')">' +
      '<div class="hot-stock-rank">' + (i + 1) + '</div>' +
      '<div style="flex:1"><div class="hot-stock-name">' + escapeHtml(name) + '</div>' +
      '<div class="hot-stock-code">' + escapeHtml(code) + '</div></div>' +
      '<div style="text-align:right">' +
      '<div class="hot-stock-code" style="font-family:Courier New,monospace">' + (price > 0 ? price.toFixed(2) : '--') + '</div>' +
      '<div class="hot-stock-code ' + chgCls + '" style="font-weight:600">' + _fmtPct(chg) + '</div>' +
      '</div></div>';
  }).join('');

  container.innerHTML = html;
}

// ================================================================
//  板块分析面板入口 — 并发加载 P0 数据
// ================================================================

/**
 * 并发加载 4 个 P0 数据
 */
async function loadSectorAnalysisData(sector) {
  await Promise.allSettled([
    loadSectorRanking(),
    loadLHBTop5(),
    loadMarketOverview(),
    loadHotStocks(),
  ]);
}

// ================================================================
//  P1: 个股简况
// ================================================================

/**
 * 加载个股简况 Tab
 */
async function loadProfileTab(code) {
  var panel = document.getElementById('profile-panel');
  if (!panel) return;
  panel.innerHTML = '<div class="loading-shimmer">加载个股简况...</div>';
  try {
    var results = await Promise.all([
      fetchProfile(code),
      fetchFinance(code, 'income'),
    ]);
    renderProfile(results[0], results[1], panel);
  } catch (e) {
    panel.innerHTML = '<div class="error-msg">数据加载失败</div>';
    console.error('Profile failed:', e);
  }
}

/**
 * 渲染个股简况
 */
function renderProfile(profile, finance, panel) {
  if (!profile && !finance) {
    // 接口 ok:true 但 data=null → 数据源不可用，明示路径与原因，不写无信息「暂无」
    panel.innerHTML = '<div class="empty-msg">数据源不可用：/api/profile 与 /api/finance 均返回空（data=null）</div>';
    return;
  }

  var html = '';

  // 基本信息
  if (profile) {
    var name = profile.name || profile['名称'] || '--';
    var industry = profile.industry || profile['行业'] || profile.sector || '--';
    var marketCap = profile.market_cap || profile['总市值'] || 0;
    var peRatio = profile.pe || profile['市盈率'] || 0;
    var pbRatio = profile.pb || profile['市净率'] || 0;
    var totalShares = profile.total_shares || profile['总股本'] || 0;
    var floatShares = profile.float_shares || profile['流通股本'] || 0;

    html += '<div class="md-section-title">' + escapeHtml(name) + ' · ' + escapeHtml(industry) + '</div>';
    html += '<div class="md-info-grid">' +
      '<div class="md-info-card"><div class="md-info-label">总市值</div><div class="md-info-value" style="color:var(--blue)">' + _fmtAmount(marketCap) + '</div></div>' +
      '<div class="md-info-card"><div class="md-info-label">市盈率(PE)</div><div class="md-info-value">' + (peRatio > 0 ? peRatio.toFixed(2) : '--') + '</div></div>' +
      '<div class="md-info-card"><div class="md-info-label">市净率(PB)</div><div class="md-info-value">' + (pbRatio > 0 ? pbRatio.toFixed(2) : '--') + '</div></div>' +
      '<div class="md-info-card"><div class="md-info-label">总股本</div><div class="md-info-value">' + _fmtAmount(totalShares) + '</div></div>' +
      '<div class="md-info-card"><div class="md-info-label">流通股本</div><div class="md-info-value">' + _fmtAmount(floatShares) + '</div></div>' +
      '</div>';
  }

  // 财务摘要
  if (finance && Array.isArray(finance) && finance.length > 0) {
    html += '<div class="md-section-title">财务摘要</div>';
    var finRows = finance.slice(0, 3);
    finRows.forEach(function (f) {
      var period = f.date || f.period || f['报告期'] || '--';
      var revenue = f.revenue || f['营业收入'] || 0;
      var netProfit = f.net_profit || f['净利润'] || 0;
      var roe = f.roe || f['净资产收益率'] || 0;

      html += '<div class="md-info-card" style="margin-bottom:4px">' +
        '<div class="md-report-meta"><span>' + escapeHtml(period) + '</span></div>' +
        '<div style="display:flex;gap:12px;margin-top:4px">' +
        '<span class="hot-stock-code">营收: <span style="color:var(--text)">' + _fmtAmount(revenue) + '</span></span>' +
        '<span class="hot-stock-code">净利: <span style="color:var(--text)">' + _fmtAmount(netProfit) + '</span></span>' +
        '<span class="hot-stock-code">ROE: <span style="color:var(--text)">' + (roe > 0 ? roe.toFixed(2) + '%' : '--') + '</span></span>' +
        '</div></div>';
    });
  }

  panel.innerHTML = html || '<div class="empty-msg">数据源不可用：/api/profile 与 /api/finance 均返回空（data=null）</div>';
}

// ================================================================
//  P1: 研报
// ================================================================

/**
 * 加载研报 Tab
 */
async function loadReportTab(code) {
  var panel = document.getElementById('report-panel');
  if (!panel) return;
  panel.innerHTML = '<div class="loading-shimmer">加载研报数据...</div>';
  try {
    var results = await Promise.all([
      fetchReport(code, 10),
      fetchConsensus(code),
    ]);
    renderReport(results[0], results[1], panel);
  } catch (e) {
    panel.innerHTML = '<div class="error-msg">研报数据加载失败</div>';
    console.error('Report failed:', e);
  }
}

/**
 * 渲染研报
 */
function renderReport(reports, consensus, panel) {
  var hasReports = Array.isArray(reports) && reports.length > 0;
  var _rating = (consensus && consensus.rating) || {};
  var _cons = (consensus && consensus.consensus) || {};
  var hasConsensus = !!(_rating.rating || _rating['评级'] || _rating.target_price || _rating['目标价'] ||
                        _cons.eps || _cons['每股收益'] || _cons.pe || _cons['预测市盈率'] ||
                        _cons.profit_growth || _cons['净利润增长率']);
  if (!hasReports && !hasConsensus) {
    // 注意：/api/consensus 恒返回 {ok:true,data:{rating:null,consensus:null}}，是「空对象」而非 null，
    // 故不能再用 `!consensus` 判定，需按实际字段判定，否则会渲染一张全是 '--' 的假卡片。
    panel.innerHTML = '<div class="empty-msg">数据源不可用：/api/report 与 /api/consensus 均返回空（data=null）</div>';
    return;
  }

  var html = '';

  // 一致预期 + 机构评级
  if (hasConsensus) {
    var rating = consensus.rating || {};
    var cons = consensus.consensus || {};

    var ratingText = rating.rating || rating['评级'] || '--';
    var targetPrice = rating.target_price || rating['目标价'] || 0;
    var eps = cons.eps || cons['每股收益'] || 0;
    var peForecast = cons.pe || cons['预测市盈率'] || 0;
    var netProfitGrowth = cons.profit_growth || cons['净利润增长率'] || 0;

    html += '<div class="md-consensus-card">' +
      '<div class="md-section-title" style="margin-top:0">机构评级 & 一致预期</div>' +
      '<div class="md-info-grid">' +
      '<div class="md-info-card"><div class="md-info-label">最新评级</div><div class="md-info-value" style="color:var(--green)">' + escapeHtml(ratingText) + '</div></div>' +
      '<div class="md-info-card"><div class="md-info-label">目标价</div><div class="md-info-value" style="color:var(--gold)">' + (targetPrice > 0 ? targetPrice.toFixed(2) : '--') + '</div></div>' +
      '<div class="md-info-card"><div class="md-info-label">预测EPS</div><div class="md-info-value">' + (eps > 0 ? eps.toFixed(2) : '--') + '</div></div>' +
      '<div class="md-info-card"><div class="md-info-label">预测PE</div><div class="md-info-value">' + (peForecast > 0 ? peForecast.toFixed(2) : '--') + '</div></div>' +
      '</div></div>';
  }

  // 研报列表
  if (reports && Array.isArray(reports) && reports.length > 0) {
    html += '<div class="md-section-title">研报列表</div>';
    reports.forEach(function (r) {
      var title = r.title || r['标题'] || '--';
      var org = r.org || r['机构'] || r.institution || '--';
      var ratingText = r.rating || r['评级'] || '';
      var date = r.date || r.time || r['日期'] || '--';
      var summary = r.summary || r.content || r['摘要'] || '';

      html += '<div class="md-report-item">' +
        '<div class="md-report-title">' + escapeHtml(title) + '</div>' +
        '<div class="md-report-meta">' +
        '<span>' + escapeHtml(org) + '</span>' +
        '<span>' + escapeHtml(date) + '</span>' +
        (ratingText ? '<span class="md-report-rating">' + escapeHtml(ratingText) + '</span>' : '') +
        '</div>' +
        (summary ? '<div class="hot-stock-code" style="margin-top:4px;line-height:1.4">' + escapeHtml(summary.substring(0, 120)) + (summary.length > 120 ? '...' : '') + '</div>' : '') +
        '</div>';
    });
  }

  panel.innerHTML = html || '<div class="empty-msg">数据源不可用：/api/report 与 /api/consensus 均返回空（data=null）</div>';
}

// ================================================================
//  P1: 北向资金
// ================================================================

/**
 * 加载北向资金 Tab
 */
async function loadNorthTab(code) {
  var panel = document.getElementById('north-panel');
  if (!panel) return;
  if (isHKStock(code)) {
    showNotSupported('north-panel', '港股无北向资金数据（北向资金仅适用于沪深A股）');
    return;
  }
  panel.innerHTML = '<div class="loading-shimmer">加载北向资金数据...</div>';
  try {
    // 并发获取沪深股通数据
    var results = await Promise.all([
      fetchNorthHolding('sh'),
      fetchNorthHolding('sz'),
    ]);
    renderNorth(results[0], results[1], panel);
  } catch (e) {
    panel.innerHTML = '<div class="error-msg">北向资金数据加载失败</div>';
    console.error('North holding failed:', e);
  }
}

/**
 * 渲染北向资金
 */
function renderNorth(shData, szData, panel) {
  if (!shData && !szData) {
    panel.innerHTML = '<div class="empty-msg">数据源不可用：/api/north-holding 返回空（data=null）</div>';
    return;
  }

  // 来源标注：本面板直取 /api/north-holding（沪深股通成份股持仓），非本地快照；
  // 若将来接入本地/历史快照作为兜底，必须同时标注「快照」性质与时间，不得当实时用。
  var html = '<div class="md-section-title">沪深股通成份股</div>' +
    '<div class="md-report-meta" style="margin-bottom:6px">数据源：/api/north-holding（沪深股通成份股持仓）</div>';

  // 沪股通 TOP10
  if (shData && Array.isArray(shData)) {
    var shTop = shData.slice(0, 10);
    html += '<div class="md-section-title" style="font-size:11px;color:var(--cyan)">沪股通持仓 TOP10</div>';
    shTop.forEach(function (s) {
      var name = s.name || s['名称'] || '--';
      var code = s.code || s['代码'] || '';
      var holdRatio = parseFloat(s.hold_ratio || s['持股比例'] || 0);
      var holdAmount = parseFloat(s.hold_amount || s['持股市值'] || 0);

      html += '<div class="md-north-row">' +
        '<div><span class="hot-stock-name" style="font-size:12px">' + escapeHtml(name) + '</span>' +
        '<span class="hot-stock-code" style="margin-left:6px">' + escapeHtml(code) + '</span></div>' +
        '<div style="text-align:right">' +
        '<div class="hot-stock-code" style="font-weight:600;color:var(--gold)">' + (holdRatio > 0 ? holdRatio.toFixed(2) + '%' : '--') + '</div>' +
        '<div class="hot-stock-code">' + _fmtAmount(holdAmount) + '</div>' +
        '</div></div>';
    });
  }

  // 深股通 TOP10
  if (szData && Array.isArray(szData)) {
    var szTop = szData.slice(0, 10);
    html += '<div class="md-section-title" style="font-size:11px;color:var(--cyan);margin-top:12px">深股通持仓 TOP10</div>';
    szTop.forEach(function (s) {
      var name = s.name || s['名称'] || '--';
      var code = s.code || s['代码'] || '';
      var holdRatio = parseFloat(s.hold_ratio || s['持股比例'] || 0);
      var holdAmount = parseFloat(s.hold_amount || s['持股市值'] || 0);

      html += '<div class="md-north-row">' +
        '<div><span class="hot-stock-name" style="font-size:12px">' + escapeHtml(name) + '</span>' +
        '<span class="hot-stock-code" style="margin-left:6px">' + escapeHtml(code) + '</span></div>' +
        '<div style="text-align:right">' +
        '<div class="hot-stock-code" style="font-weight:600;color:var(--gold)">' + (holdRatio > 0 ? holdRatio.toFixed(2) + '%' : '--') + '</div>' +
        '<div class="hot-stock-code">' + _fmtAmount(holdAmount) + '</div>' +
        '</div></div>';
    });
  }

  panel.innerHTML = html || '<div class="empty-msg">数据源不可用：/api/north-holding 返回空（data=null）</div>';
}

// ================================================================
//  P1: 资讯
// ================================================================

/**
 * 加载资讯 Tab
 */
async function loadNewsTab(code) {
  var panel = document.getElementById('news-panel');
  if (!panel) return;
  panel.innerHTML = '<div class="loading-shimmer">加载资讯数据...</div>';
  try {
    var results = await Promise.all([
      fetchNews(code, 15),
      fetchMarketNews(15),
    ]);
    renderNews(results[0], results[1], panel);
  } catch (e) {
    panel.innerHTML = '<div class="error-msg">资讯数据加载失败</div>';
    console.error('News failed:', e);
  }
}

/**
 * 渲染资讯
 */
function renderNews(stockNews, marketNews, panel) {
  if (!stockNews && !marketNews) {
    panel.innerHTML = '<div class="empty-msg">暂无资讯数据</div>';
    return;
  }

  var html = '';

  // 个股新闻
  if (stockNews && Array.isArray(stockNews) && stockNews.length > 0) {
    html += '<div class="md-section-title">个股新闻</div>';
    stockNews.forEach(function (n) {
      var title = n.title || n['标题'] || '--';
      var time = n.time || n.date || n['时间'] || '--';
      var source = n.source || n['来源'] || '';

      html += '<div class="md-news-item">' +
        '<div class="md-news-title">' + escapeHtml(title) + '</div>' +
        '<div class="md-news-meta"><span>' + escapeHtml(time) + '</span>' +
        (source ? '<span>' + escapeHtml(source) + '</span>' : '') +
        '</div></div>';
    });
  }

  // 市场资讯
  if (marketNews && Array.isArray(marketNews) && marketNews.length > 0) {
    html += '<div class="md-section-title">市场资讯</div>';
    marketNews.forEach(function (n) {
      var title = n.title || n['标题'] || '--';
      var time = n.time || n.date || n['时间'] || '--';
      var source = n.source || n['来源'] || '';

      html += '<div class="md-news-item">' +
        '<div class="md-news-title">' + escapeHtml(title) + '</div>' +
        '<div class="md-news-meta"><span>' + escapeHtml(time) + '</span>' +
        (source ? '<span>' + escapeHtml(source) + '</span>' : '') +
        '</div></div>';
    });
  }

  panel.innerHTML = html || '<div class="empty-msg">暂无资讯数据</div>';
}

// ================================================================
//  热门股 / 龙虎榜侧边栏面板入口
// ================================================================

/**
 * 显示热门股面板（独立全屏覆盖面板）
 */
function showHotStocksPanel() {
  var panel = document.getElementById('hot-stocks-panel');
  if (!panel) return;
  panel.style.display = 'flex';
  loadHotStocksFull('stock', 50, 'hot-stocks-table');
  showToast('热 热门股', '正在加载市场热门股排行');
}

/**
 * 显示龙虎榜面板（独立全屏覆盖面板）
 */
function showLHBPanel() {
  var panel = document.getElementById('lhb-panel');
  if (!panel) return;
  panel.style.display = 'flex';
  // v5.4: 设置日期选择器默认值为今天
  var datePicker = document.getElementById('lhb-date-picker');
  if (datePicker && !datePicker.value) {
    var today = new Date();
    datePicker.value = today.getFullYear() + '-' +
      String(today.getMonth() + 1).padStart(2, '0') + '-' +
      String(today.getDate()).padStart(2, '0');
  }
  // 激活 jg tab
  document.querySelectorAll('#lhb-panel .full-overlay-tab').forEach(function (t) {
    t.classList.toggle('active', t.getAttribute('data-tab') === 'jg');
  });
  loadLHBFull('jg', 'lhb-table');
  showToast('档 龙虎榜', '正在加载龙虎榜数据');
}

// ================================================================
//  P0 v5.3: 热门股 + 龙虎榜独立全屏面板
// ================================================================

/**
 * 通用：渲染表格骨架
 * @param {HTMLElement} container - 目标容器元素
 * @param {number} rows - 骨架行数，默认 8
 */
function _renderTableSkeleton(container, rows) {
  rows = rows || 8;
  var html = '<div class="data-table-skeleton">';
  for (var i = 0; i < rows; i++) {
    html += '<div class="skeleton-row"></div>';
  }
  html += '</div>';
  container.innerHTML = html;
}

/**
 * 通用：渲染表格错误态（含重试按钮）
 * @param {HTMLElement} container - 目标容器元素
 * @param {string} retryFnName - 重试按钮 onclick 调用的函数名字符串
 */
function _renderTableError(container, retryFnName) {
  container.innerHTML = '<div class="error-empty">' +
    '<div class="error-empty-icon">!</div>' +
    '<div class="error-empty-text">数据加载失败</div>' +
    '<button class="retry-btn" onclick="' + retryFnName + '">重试</button>' +
    '</div>';
}

/**
 * 通用：渲染表格空态
 * @param {HTMLElement} container - 目标容器元素
 * @param {string} msg - 空态提示文字，默认"暂无数据"
 */
function _renderTableEmpty(container, msg) {
  msg = msg || '暂无数据';
  container.innerHTML = '<div class="error-empty">' +
    '<div class="error-empty-icon">空</div>' +
    '<div class="error-empty-text">' + msg + '</div>' +
    '</div>';
}

/**
 * 加载热门股数据（独立全屏面板或指定容器）
 * @param {string} type - 'stock' 或 'fund'，默认 'stock'
 * @param {number} limit - 请求数量，默认 50
 * @param {string} containerId - 渲染目标容器 ID，默认 'hot-stocks-table'
 */
async function loadHotStocksFull(type, limit, containerId) {
  type = type || 'stock';
  // v5.3: API 不支持 type=fund，实际数据是 ETF（type=etf）
  var apiType = type === 'fund' ? 'etf' : type;
  limit = limit || 50;
  containerId = containerId || 'hot-stocks-table';
  var container = document.getElementById(containerId);
  if (!container) return;

  _renderTableSkeleton(container, 10);
  try {
    var env = await fetchHotStocksEnvelope(apiType, limit);
    var data = env ? env.data : null;
    var degraded = !!(env && env.degraded);
    var srcName = (env && env.source) || '';
    renderDegradedBanner('hot-stocks-panel', degraded, srcName);
    if (!data || (Array.isArray(data) ? data.length === 0 : (!data.rows || data.rows.length === 0))) {
      _renderTableEmpty(container, '暂无' + (type === 'fund' ? '基金' : '股票') + '热门数据');
      return;
    }
    if (containerId === 'hot-stocks-table') {
      renderHotStocksFull(data);
    } else {
      renderHotStocks(data, containerId);
    }
  } catch (e) {
    console.error('Hot stocks full failed:', e);
    var retryFn = containerId === 'hot-stocks-table'
      ? "loadHotStocksFull('" + type + "', " + limit + ")"
      : "loadHotStocks()";
    _renderTableError(container, retryFn);
  }
}

/**
 * 通用：渲染分页控制栏
 */
function _renderPaginationBar(page, totalPages, totalItems, onPageChangeFnName) {
  if (totalPages <= 1) return '';
  var html = '<div class="table-pagination-bar" style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:rgba(255,255,255,0.02);border-top:1px solid rgba(255,255,255,0.06);margin-top:8px;font-size:12px;color:var(--text2)">' +
    '<span>共 <b>' + totalItems + '</b> 条记录 &bull; 第 <b>' + page + '</b> / ' + totalPages + ' 页</span>' +
    '<div class="page-btn-group" style="display:flex;gap:6px">';

  // 首页 & 上一页
  if (page > 1) {
    html += '<button class="pagination-btn" onclick="' + onPageChangeFnName + '(1)" style="padding:4px 8px;background:var(--bg3);border:1px solid var(--border);border-radius:4px;color:var(--text);cursor:pointer">&laquo; 首页</button>';
    html += '<button class="pagination-btn" onclick="' + onPageChangeFnName + '(' + (page - 1) + ')" style="padding:4px 8px;background:var(--bg3);border:1px solid var(--border);border-radius:4px;color:var(--text);cursor:pointer">&lsaquo; 上一页</button>';
  } else {
    html += '<button class="pagination-btn disabled" disabled style="padding:4px 8px;background:transparent;border:1px solid rgba(255,255,255,0.05);border-radius:4px;color:var(--muted);cursor:not-allowed">&laquo; 首页</button>';
    html += '<button class="pagination-btn disabled" disabled style="padding:4px 8px;background:transparent;border:1px solid rgba(255,255,255,0.05);border-radius:4px;color:var(--muted);cursor:not-allowed">&lsaquo; 上一页</button>';
  }

  // 页码按钮 (最多显示 5 个)
  var startP = Math.max(1, page - 2);
  var endP = Math.min(totalPages, startP + 4);
  if (endP - startP < 4) startP = Math.max(1, endP - 4);

  for (var p = startP; p <= endP; p++) {
    var isActive = p === page;
    var style = isActive
      ? 'padding:4px 10px;background:var(--blue);border:1px solid var(--blue);border-radius:4px;color:#fff;font-weight:700;cursor:default'
      : 'padding:4px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:4px;color:var(--text);cursor:pointer';
    html += '<button class="pagination-btn ' + (isActive ? 'active' : '') + '" onclick="' + onPageChangeFnName + '(' + p + ')" style="' + style + '">' + p + '</button>';
  }

  // 下一页 & 末页
  if (page < totalPages) {
    html += '<button class="pagination-btn" onclick="' + onPageChangeFnName + '(' + (page + 1) + ')" style="padding:4px 8px;background:var(--bg3);border:1px solid var(--border);border-radius:4px;color:var(--text);cursor:pointer">下一页 &rsaquo;</button>';
    html += '<button class="pagination-btn" onclick="' + onPageChangeFnName + '(' + totalPages + ')" style="padding:4px 8px;background:var(--bg3);border:1px solid var(--border);border-radius:4px;color:var(--text);cursor:pointer">末页 &raquo;</button>';
  } else {
    html += '<button class="pagination-btn disabled" disabled style="padding:4px 8px;background:transparent;border:1px solid rgba(255,255,255,0.05);border-radius:4px;color:var(--muted);cursor:not-allowed">下一页 &rsaquo;</button>';
    html += '<button class="pagination-btn disabled" disabled style="padding:4px 8px;background:transparent;border:1px solid rgba(255,255,255,0.05);border-radius:4px;color:var(--muted);cursor:not-allowed">末页 &raquo;</button>';
  }

  html += '</div></div>';
  return html;
}

window._hotStocksState = { page: 1, limit: 15, allData: [] };
window._lhbState = { page: 1, limit: 15, allData: [] };

function setHotStocksPage(page) {
  window._hotStocksState.page = page;
  renderHotStocksFull(window._hotStocksState.allData, page);
}

function setLHBPage(page) {
  window._lhbState.page = page;
  renderLHBFull(window._lhbState.allData, window._lhbState.tab || 'jg', page);
}

/**
 * 渲染热门股完整表格（全屏面板 #hot-stocks-table）
 * 列：排名 / 名称 / 代码 / 涨跌幅 / 现价 / 换手率 / 成交额 / 人气热度 / 驱动理由 / 状态 / 操作
 * @param {Object|Array} data - API 返回的热门股数据
 * @param {number} page - 当前页码
 */
function renderHotStocksFull(data, page) {
  var container = document.getElementById('hot-stocks-table');
  if (!container) return;
  var stocks = Array.isArray(data) ? data : (data.rows || data.stocks || data.data || []);
  if (!stocks.length) {
    _renderTableEmpty(container, '暂无热门股票数据');
    return;
  }

  window._hotStocksState.allData = stocks;
  page = page || window._hotStocksState.page || 1;
  var pageSize = window._hotStocksState.limit || 15;
  var totalPages = Math.ceil(stocks.length / pageSize);
  if (page > totalPages) page = totalPages;
  if (page < 1) page = 1;

  var startIdx = (page - 1) * pageSize;
  var pagedStocks = stocks.slice(startIdx, startIdx + pageSize);

  var sourceStr = data.source || (stocks[0] && stocks[0].source) || '东方财富实时人气榜';
  var updateTime = data.updated_at || (stocks[0] && stocks[0].updated_at) || new Date().toLocaleTimeString();

  var html = '<div class="table-meta-bar" style="display:flex;justify-content:space-between;align-items:center;padding:6px 12px;background:rgba(80,140,255,0.06);border-radius:6px;margin-bottom:8px;font-size:11px;color:var(--text2)">' +
    '<span>📡 来源: <b style="color:var(--cyan)">' + escapeHtml(sourceStr) + '</b> &bull; 全市场精选 <b style="color:var(--gold)">' + stocks.length + '</b> 只活跃标的</span>' +
    '<span>⏱️ 刷新时间: <b style="color:var(--text)">' + escapeHtml(updateTime) + '</b></span>' +
    '</div>';

  html += '<table class="data-table"><thead><tr>' +
    '<th class="col-rank">排名</th>' +
    '<th>名称</th>' +
    '<th>代码</th>' +
    '<th class="col-num">涨跌幅</th>' +
    '<th class="col-num">现价</th>' +
    '<th class="col-num">换手率</th>' +
    '<th class="col-num">成交额</th>' +
    '<th class="col-num">热度分</th>' +
    '<th>筛选与驱动依据</th>' +
    '<th>状态</th>' +
    '<th>操作</th>' +
    '</tr></thead><tbody>';

  pagedStocks.forEach(function (s, i) {
    var globalRank = startIdx + i + 1;
    var name = s.name || s['名称'] || '--';
    var code = s.code || s['代码'] || '';
    var chg = parseFloat(s.zdf || s.chg || s.change_pct || s['涨跌幅'] || 0);
    var price = parseFloat(s.zxj || s.price || s['现价'] || 0);
    var turnover = parseFloat(s.turnover || s['换手率'] || 0);
    var amount = parseFloat(s.amount || s['成交额'] || 0);
    var hotScore = s.hot_score || (98 - (globalRank - 1) * 1.5);
    var reason = s.reason || s['驱动理由'] || '主力资金深度博弈';
    var status = s.status || (chg >= 9.8 ? '涨停' : chg >= 5.0 ? '大涨' : chg > 0 ? '领涨' : '回调');
    var chgCls = chg >= 0 ? 'col-chg-up' : 'col-chg-dn';
    var chgText = (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%';
    
    var stCls = 'flat';
    if (/涨停|涨|up|bull/i.test(status)) stCls = 'up';
    else if (/跌停|跌|dn|bear/i.test(status)) stCls = 'dn';
    var statusTag = '<span class="status-tag ' + stCls + '">' + escapeHtml(status) + '</span>';

    html += '<tr onclick="selectStock(\'' + escapeHtml(code) + '\', \'' +
      escapeHtml(name).replace(/'/g, "\\'") + '\'); closeHotStocksPanel();">' +
      '<td class="col-rank"><span class="rank-badge rank-' + (globalRank <= 3 ? globalRank : 'normal') + '">' + globalRank + '</span></td>' +
      '<td class="col-name"><strong>' + escapeHtml(name) + '</strong></td>' +
      '<td class="col-code" style="font-family:\'SF Mono\',monospace">' + escapeHtml(code) + '</td>' +
      '<td class="col-num ' + chgCls + '" style="font-weight:700">' + chgText + '</td>' +
      '<td class="col-num" style="font-family:\'SF Mono\',monospace">' + (price > 0 ? price.toFixed(2) : '--') + '</td>' +
      '<td class="col-num" style="color:var(--gold);font-weight:600">' + (turnover > 0 ? turnover.toFixed(2) + '%' : '--') + '</td>' +
      '<td class="col-num">' + _fmtAmount(amount) + '</td>' +
      '<td class="col-num" style="color:var(--cyan);font-weight:700">' + hotScore + '</td>' +
      '<td style="font-size:11px;color:var(--text);max-width:260px;line-height:1.4">' + escapeHtml(reason) + '</td>' +
      '<td>' + statusTag + '</td>' +
      '<td><button class="hot-detail-btn" onclick="event.stopPropagation(); showStockDetailPopover(\'' + escapeHtml(code) + '\', event)">详情</button></td>' +
      '</tr>';
  });
  html += '</tbody></table>';
  html += _renderPaginationBar(page, totalPages, stocks.length, 'setHotStocksPage');
  container.innerHTML = html;
}

/**
 * v5.4: 热门股详情弹卡 — 调 fetchProfile(code) → 渲染弹卡定位到行旁
 * @param {string} code - 股票代码
 * @param {Event} event - 点击事件（用于定位）
 */
async function showStockDetailPopover(code, event) {
  // 先关闭已有的弹卡
  closeStockDetailPopover();

  var popover = document.createElement('div');
  popover.className = 'stock-detail-popover';
  popover.id = 'stock-detail-popover';
  popover.innerHTML = '<div class="stock-detail-popover-loading">加载中...</div>';

  // 定位到点击位置附近
  var rect = event.target.getBoundingClientRect();
  popover.style.position = 'fixed';
  popover.style.top = (rect.bottom + 4) + 'px';
  popover.style.left = Math.max(8, rect.left - 200) + 'px';
  document.body.appendChild(popover);

  // 点击其他区域关闭
  setTimeout(function () {
    document.addEventListener('click', _closePopoverOnOutside);
  }, 0);

  try {
    var data = await fetchProfile(code);
    if (!data) {
      popover.innerHTML = '<div class="stock-detail-popover-empty">暂无简况数据</div>';
      return;
    }

    var name = data.name || data['名称'] || '--';
    var industry = data.industry || data['行业'] || '--';
    var marketCap = data.market_cap || data['市值'] || null;
    var pe = data.pe || data['PE'] || null;
    var turnover = data.turnover || data['换手率'] || null;
    var pb = data.pb || data['PB'] || null;

    popover.innerHTML =
      '<div class="stock-detail-popover-header">' +
        '<span class="stock-detail-popover-name">' + escapeHtml(name) + '</span>' +
        '<span class="stock-detail-popover-code">' + escapeHtml(code) + '</span>' +
        '<button class="stock-detail-popover-close" onclick="closeStockDetailPopover()">✕</button>' +
      '</div>' +
      '<div class="stock-detail-popover-body">' +
        '<div class="stock-detail-row"><span>行业</span><span>' + escapeHtml(industry) + '</span></div>' +
        '<div class="stock-detail-row"><span>市值</span><span>' + (marketCap != null ? _fmtAmount(marketCap) : '--') + '</span></div>' +
        '<div class="stock-detail-row"><span>PE</span><span>' + (pe != null ? pe.toFixed(1) : '--') + '</span></div>' +
        '<div class="stock-detail-row"><span>PB</span><span>' + (pb != null ? pb.toFixed(2) : '--') + '</span></div>' +
        '<div class="stock-detail-row"><span>换手率</span><span>' + (turnover != null ? turnover.toFixed(2) + '%' : '--') + '</span></div>' +
      '</div>';
  } catch (e) {
    console.error('showStockDetailPopover error:', e);
    popover.innerHTML = '<div class="stock-detail-popover-empty">数据加载失败</div>';
  }
}

/**
 * v5.4: 关闭股票详情弹卡
 */
function closeStockDetailPopover() {
  var popover = document.getElementById('stock-detail-popover');
  if (popover) popover.remove();
  document.removeEventListener('click', _closePopoverOnOutside);
}

/**
 * v5.4: 外部点击关闭弹卡
 */
function _closePopoverOnOutside(e) {
  var popover = document.getElementById('stock-detail-popover');
  if (popover && !popover.contains(e.target) && !e.target.classList.contains('hot-detail-btn')) {
    closeStockDetailPopover();
  }
}

/**
 * v5.4: 获取龙虎榜日期选择器值
 * @returns {string|null} YYYY-MM-DD 格式日期，或 null
 */
function getLHBSelectedDate() {
  var picker = document.getElementById('lhb-date-picker');
  if (picker && picker.value) return picker.value;
  return null;
}

/**
 * 加载龙虎榜数据（独立全屏面板或指定容器）
 * @param {string} tab - 龙虎榜 tab 类型，默认 'jg'
 * @param {string} containerId - 渲染目标容器 ID，默认 'lhb-table'
 */
async function loadLHBFull(tab, containerId) {
  tab = tab || 'jg';
  containerId = containerId || 'lhb-table';
  var container = document.getElementById(containerId);
  if (!container) return;

  _renderTableSkeleton(container, 10);
  try {
    var date = getLHBSelectedDate();
    var env = await fetchLHBEnvelope(tab, date);
    var data = env ? env.data : null;
    var degraded = !!(env && env.degraded);
    var srcName = (env && env.source) || '';
    renderDegradedBanner('lhb-panel', degraded, srcName);
    // 归一化：接口 data 可能是数组（分页行），也可能是含 rows/data 的对象
    var rows = Array.isArray(data) ? data : (data && (data.rows || data.data)) || [];
    if (!rows.length) {
      _renderTableEmpty(container, '暂无龙虎榜数据');
      return;
    }
    if (containerId === 'lhb-table') {
      renderLHBFull(rows, tab);
    } else {
      renderLHBTop5(rows, containerId);
    }
  } catch (e) {
    console.error('LHB full failed:', e);
    _renderTableError(container, "loadLHBFull('" + tab + "')");
  }
}

/**
 * 渲染龙虎榜完整表格（全屏面板 #lhb-table）
 * 列：排名 / 日期 / 名称 / 代码 / 现价 / 涨跌幅 / 买入额 / 卖出额 / 净买入 / 核心席位 / 上榜原因 / 性质
 * @param {Object} data - API 返回的龙虎榜数据
 * @param {string} tab - 当前 tab 类型
 * @param {number} page - 当前页码
 */
function renderLHBFull(data, tab, page) {
  var container = document.getElementById('lhb-table');
  if (!container) return;
  var rows = Array.isArray(data) ? data : (data.rows || data.data || []);
  if (!rows.length) {
    _renderTableEmpty(container, '暂无龙虎榜上榜明细数据');
    return;
  }

  window._lhbState.allData = rows;
  window._lhbState.tab = tab;
  page = page || window._lhbState.page || 1;
  var pageSize = window._lhbState.limit || 15;
  var totalPages = Math.ceil(rows.length / pageSize);
  if (page > totalPages) page = totalPages;
  if (page < 1) page = 1;

  var startIdx = (page - 1) * pageSize;
  var pagedRows = rows.slice(startIdx, startIdx + pageSize);

  var sourceStr = data.source || (rows[0] && rows[0].source) || '东方财富龙虎榜数据中心';
  var updateTime = data.updated_at || (rows[0] && rows[0].updated_at) || new Date().toLocaleTimeString();
  var lhbDate = (rows[0] && rows[0].date) || getLHBSelectedDate() || new Date().toISOString().substring(0, 10);

  var html = '<div class="table-meta-bar" style="display:flex;justify-content:space-between;align-items:center;padding:6px 12px;background:rgba(80,140,255,0.06);border-radius:6px;margin-bottom:8px;font-size:11px;color:var(--text2)">' +
    '<span>📡 来源: <b style="color:var(--cyan)">' + escapeHtml(sourceStr) + '</b> &bull; 上榜日期: <b style="color:var(--gold)">' + escapeHtml(lhbDate) + '</b> (共 <b style="color:var(--text)">' + rows.length + '</b> 席)</span>' +
    '<span>⏱️ 刷新时间: <b style="color:var(--text)">' + escapeHtml(updateTime) + '</b></span>' +
    '</div>';

  html += '<table class="data-table"><thead><tr>' +
    '<th class="col-rank">排名</th>' +
    '<th>日期</th>' +
    '<th>名称</th>' +
    '<th>代码</th>' +
    '<th class="col-num">现价</th>' +
    '<th class="col-num">涨跌幅</th>' +
    '<th class="col-num">买入总额</th>' +
    '<th class="col-num">卖出总额</th>' +
    '<th class="col-num">净买入额</th>' +
    '<th>核心买卖席位</th>' +
    '<th>上榜原因</th>' +
    '<th>性质</th>' +
    '</tr></thead><tbody>';

  pagedRows.forEach(function (r, i) {
    var globalRank = startIdx + i + 1;
    var name = r.name || r['名称'] || '--';
    var code = r.code || r['代码'] || '';
    var rDate = r.date || r.trade_date || lhbDate;
    var price = parseFloat(r.price || r.close_price || 0);
    var chg = parseFloat(r.chg || r.change_pct || r.change_rate || 0);
    var buyAmt = parseFloat(r.buy_amount || r.buy_amt || 0);
    var sellAmt = parseFloat(r.sell_amount || r.sell_amt || 0);
    var netAmt = parseFloat(r.net_amount || r.net_buy || r.net_amt || (buyAmt - sellAmt));
    var reason = r.reason || r['上榜原因'] || r.explanation || '';
    // branch_top 现为东财真实席位性质文本（EXPLAIN），无则留空，不再编造营业部名
    var branch = r.branch_top || r['营业部席位'] || '';
    // inst_count 由真实文本推导（0/1）；不能再对 0 回退到净额启发式，避免伪造
    var instCount = (r.inst_count != null) ? parseInt(r.inst_count, 10) : 0;
    var tag = instCount > 0 ? '<span class="status-tag bull">机构席位</span>' :
      '<span class="status-tag bear">普通席位</span>';
    var chgCls = chg >= 0 ? 'col-chg-up' : 'col-chg-dn';
    var netCls = netAmt >= 0 ? 'col-chg-up' : 'col-chg-dn';

    html += '<tr onclick="selectStock(\'' + escapeHtml(code) + '\', \'' +
      escapeHtml(name).replace(/'/g, "\\'") + '\'); closeLHBPanel();">' +
      '<td class="col-rank"><span class="rank-badge rank-' + (globalRank <= 3 ? globalRank : 'normal') + '">' + globalRank + '</span></td>' +
      '<td style="font-size:11px;color:var(--text2)">' + escapeHtml(rDate) + '</td>' +
      '<td class="col-name"><strong>' + escapeHtml(name) + '</strong></td>' +
      '<td class="col-code" style="font-family:\'SF Mono\',monospace">' + escapeHtml(code) + '</td>' +
      '<td class="col-num" style="font-family:\'SF Mono\',monospace">' + (price > 0 ? price.toFixed(2) : '--') + '</td>' +
      '<td class="col-num ' + chgCls + '" style="font-weight:700">' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%</td>' +
      '<td class="col-num" style="color:var(--green)">' + _fmtAmount(buyAmt) + '</td>' +
      '<td class="col-num" style="color:var(--red)">' + _fmtAmount(sellAmt) + '</td>' +
      '<td class="col-num ' + netCls + '" style="font-weight:700">' + (netAmt >= 0 ? '+' : '') + _fmtAmount(netAmt) + '</td>' +
      '<td style="font-size:11px;color:var(--text);max-width:180px">' + escapeHtml(branch) + '</td>' +
      '<td style="font-size:11px;color:var(--text2);max-width:200px">' + escapeHtml(reason) + '</td>' +
      '<td>' + tag + '</td>' +
      '</tr>';
  });
  html += '</tbody></table>';
  html += _renderPaginationBar(page, totalPages, rows.length, 'setLHBPage');
  container.innerHTML = html;
}

/**
 * 关闭热门股全屏面板
 */
function closeHotStocksPanel() {
  var panel = document.getElementById('hot-stocks-panel');
  if (panel) panel.style.display = 'none';
}

/**
 * 关闭龙虎榜全屏面板
 */
function closeLHBPanel() {
  var panel = document.getElementById('lhb-panel');
  if (panel) panel.style.display = 'none';
}

/**
 * 切换热门股类型 tab（股票/基金）
 * @param {string} type - 'stock' 或 'fund'
 */
function switchHotTab(type) {
  document.querySelectorAll('#hot-stocks-panel .full-overlay-tab').forEach(function (t) {
    t.classList.toggle('active', t.getAttribute('data-type') === type);
  });
  loadHotStocksFull(type, 50, 'hot-stocks-table');
}

/**
 * 切换龙虎榜 tab
 * @param {string} tab - tab 类型: jg/yzb/yyb/gslmr/gslxw
 */
function switchLHBTab(tab) {
  document.querySelectorAll('#lhb-panel .full-overlay-tab').forEach(function (t) {
    t.classList.toggle('active', t.getAttribute('data-tab') === tab);
  });
  loadLHBFull(tab, 'lhb-table');
}

// v5.4: ESC 键关闭全屏面板 — 按优先级关闭最上层面板
// 检查顺序: popover > sector-stocks > screen-result > sector-analysis > hot-stocks > lhb > compare
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' || e.keyCode === 27) {
    // 优先关闭股票详情弹卡
    var popover = document.getElementById('stock-detail-popover');
    if (popover) {
      closeStockDetailPopover();
      e.stopPropagation();
      return;
    }
    // 板块成分股面板
    var sectorStocks = document.getElementById('sector-stocks-panel');
    if (sectorStocks && sectorStocks.style.display === 'flex') {
      closeSectorStocksPanel();
      e.stopPropagation();
      return;
    }
    // 选股结果面板
    var screenResult = document.getElementById('screen-result-panel');
    if (screenResult && screenResult.style.display === 'flex') {
      closeScreenResultPanel();
      e.stopPropagation();
      return;
    }
    // 板块分析面板
    var sectorAnalysis = document.getElementById('sector-analysis-panel');
    if (sectorAnalysis && sectorAnalysis.style.display === 'flex') {
      closeSectorPanel();
      e.stopPropagation();
      return;
    }
    // 热门股面板
    var hot = document.getElementById('hot-stocks-panel');
    if (hot && hot.style.display === 'flex') {
      closeHotStocksPanel();
      e.stopPropagation();
      return;
    }
    // 龙虎榜面板
    var lhb = document.getElementById('lhb-panel');
    if (lhb && lhb.style.display === 'flex') {
      closeLHBPanel();
      e.stopPropagation();
      return;
    }
  }
});

// ================================================================
//  Wrapper: 保持板块分析面板的 #top-stocks-list 仍能渲染
//  原 loadHotStocks() / loadLHBTop5() / renderHotStocks() / renderLHBTop5()
//  被包装为调用新版函数的精简版
// ================================================================

// 保存原始函数引用
var _origLoadHotStocks = loadHotStocks;
var _origLoadLHBTop5 = loadLHBTop5;
var _origRenderHotStocks = renderHotStocks;
var _origRenderLHBTop5 = renderLHBTop5;

/**
 * loadHotStocks 包装器 — 板块分析面板调用时渲染 Top5 到 #top-stocks-list
 */
loadHotStocks = function () {
  return loadHotStocksFull('stock', 5, 'top-stocks-list');
};

/**
 * loadLHBTop5 包装器 — 板块分析面板调用时渲染 Top5 到 #top-stocks-list
 */
loadLHBTop5 = function () {
  return loadLHBFull('jg', 'top-stocks-list');
};

/**
 * renderHotStocks 包装器 — 支持可选 containerId 参数
 * 传入 containerId 时渲染 Top5 精简卡片到指定容器；否则调用原始函数
 * @param {Object|Array} data - 热门股数据
 * @param {string} [containerId] - 可选容器 ID
 */
renderHotStocks = function (data, containerId) {
  if (containerId) {
    var container = document.getElementById(containerId);
    if (!container || !data) return;
    var stocks = Array.isArray(data) ? data : (data.stocks || data.rows || []);
    if (!Array.isArray(stocks) || stocks.length === 0) return;
    var topN = stocks.slice(0, 5);
    var html = topN.map(function (s, i) {
      var name = s.name || s['名称'] || '--';
      var code = s.code || s['代码'] || '';
      var chg = parseFloat(s.chg || s.change_pct || s['涨跌幅'] || 0);
      var price = parseFloat(s.price || s['现价'] || 0);
      var chgCls = chg >= 0 ? 'up' : 'dn';
      return '<div class="hot-stock-row" onclick="selectStock(\'' + escapeHtml(code) + '\',\'' + escapeHtml(name).replace(/'/g, "\\'") + '\')">' +
        '<div class="hot-stock-rank">' + (i + 1) + '</div>' +
        '<div style="flex:1"><div class="hot-stock-name">' + escapeHtml(name) + '</div>' +
        '<div class="hot-stock-code">' + escapeHtml(code) + '</div></div>' +
        '<div style="text-align:right">' +
        '<div class="hot-stock-code" style="font-family:Courier New,monospace">' + (price > 0 ? price.toFixed(2) : '--') + '</div>' +
        '<div class="hot-stock-code ' + chgCls + '" style="font-weight:600">' + _fmtPct(chg) + '</div>' +
        '</div></div>';
    }).join('');
    container.innerHTML = html;
  } else {
    return _origRenderHotStocks(data);
  }
};

/**
 * renderLHBTop5 包装器 — 支持可选 containerId 参数
 * 传入 containerId 时渲染 Top5 精简卡片到指定容器；否则调用原始函数
 * @param {Object} data - 龙虎榜数据
 * @param {string} [containerId] - 可选容器 ID
 */
renderLHBTop5 = function (data, containerId) {
  if (containerId) {
    var container = document.getElementById(containerId);
    if (!container || !data) return;
    var rows = data.rows || data || [];
    if (!Array.isArray(rows) || rows.length === 0) return;
    var top5 = rows.slice(0, 5);
    var html = top5.map(function (r, i) {
      var name = r.name || r['名称'] || '--';
      var code = r.code || r['代码'] || '';
      var buyAmt = parseFloat(r.buy_amount || r.net_buy || r['净买入额'] || 0);
      var reason = r.reason || r['上榜原因'] || '';
      var tag = (r.inst_count || r['机构席位数'] || 0) > 0 ? '机构' : '普通';

      return '<div class="lhb-stock-card" onclick="selectStock(\'' + escapeHtml(code) + '\',\'' + escapeHtml(name).replace(/'/g, "\\'") + '\')">' +
        '<div class="lhb-stock-header">' +
        '<div><span class="hot-stock-rank">' + (i + 1) + '</span>' +
        '<span class="lhb-stock-name">' + escapeHtml(name) + '</span>' +
        '<span class="hot-stock-code" style="margin-left:6px">' + escapeHtml(code) + '</span></div>' +
        '<div><span class="lhb-stock-buy">' + _fmtAmount(buyAmt) + '</span></div>' +
        '</div>' +
        '<div style="display:flex;gap:6px;align-items:center">' +
        '<span class="lhb-stock-tag">' + tag + '</span>' +
        (reason ? '<span class="hot-stock-code">' + escapeHtml(reason) + '</span>' : '') +
        '</div>' +
        '</div>';
    }).join('');
    container.innerHTML = html;
  } else {
    return _origRenderLHBTop5(data);
  }
};
