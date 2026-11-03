/**
 * DAPENG Alpha v6 — ECharts 渲染引擎
 * KDJ + 支撑/压力位 + 高低点标记 + 指标切换 + 视觉增强
 */
let klineChart = null;
let chartType = 'kline';
let _chartInitTries = 0;

// v6.0: 指标可见性配置
window._indicatorConfig = {
  showVolume: true,
  showMA5: true, showMA10: true, showMA20: true, showMA60: false,
  showBOLL: false,
  showMACD: true,
  showKDJ: false,
  showRSI: false,
  showSupportResistance: false,
  subIndicator: 'macd',  // 'macd' | 'kdj' | 'rsi'
  params: { macd: [12, 26, 9], kdj: [9, 3, 3], rsi: [14], boll: [20, 2] },
};
// v16: 载入用户自定义指标参数（本地持久化）
try {
  var _savedIndParams = JSON.parse(localStorage.getItem('dapeng_indicator_params') || 'null');
  if (_savedIndParams && typeof _savedIndParams === 'object') {
    ['macd', 'kdj', 'rsi', 'boll'].forEach(function (k) {
      if (Array.isArray(_savedIndParams[k]) && _savedIndParams[k].every(function (v) { return Number.isFinite(Number(v)) && Number(v) > 0; })) {
        window._indicatorConfig.params[k] = _savedIndParams[k].map(Number);
      }
    });
  }
} catch (e) {}

// ================================================================
//  v18: 涨跌配色 —— A 股惯例「涨=红 / 跌=绿」
//  ---------------------------------------------------------------
//  色值统一从 :root 主题变量读取（--red / --green），与 css/dashboard.css 及
//  全站 .up(红)/.dn(绿) 保持一致，不再自造独立色板。
//  ⚠️ 语义色（压力=红/支撑=绿、超买=红/超卖=绿、最高=红/最低=绿）不属涨跌色，
//     不受本模块影响，按惯例保持。
// ================================================================
var _themeUp = '#ff3d6a';    // 涨=红（--red 兜底）
var _themeDown = '#00e96a';  // 跌=绿（--green 兜底）

/** 读取 :root 主题变量（取不到时回退） */
function _readThemeColor(name, fallback) {
  try {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    v = (v || '').trim();
    return v || fallback;
  } catch (e) { return fallback; }
}

/** 刷新涨跌主题色缓存（每次渲染开始时调用，随主题切换生效） */
function refreshThemeColors() {
  _themeUp = _readThemeColor('--red', _themeUp || '#ff3d6a');
  _themeDown = _readThemeColor('--green', _themeDown || '#00e96a');
}

/** hex → rgba（用于成交量/MACD 的透明度渐变） */
function _hexToRgba(hex, alpha) {
  var h = String(hex == null ? '' : hex).trim().replace('#', '');
  if (h.length === 3) h = h.charAt(0) + h.charAt(0) + h.charAt(1) + h.charAt(1) + h.charAt(2) + h.charAt(2);
  var n = parseInt(h, 16);
  if (h.length !== 6 || isNaN(n)) return hex;
  return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + alpha + ')';
}

/** 向白色提亮（用于蜡烛边框，保持原视觉层次） */
function _lightenHex(hex, ratio) {
  var h = String(hex == null ? '' : hex).trim().replace('#', '');
  if (h.length === 3) h = h.charAt(0) + h.charAt(0) + h.charAt(1) + h.charAt(1) + h.charAt(2) + h.charAt(2);
  var n = parseInt(h, 16);
  if (h.length !== 6 || isNaN(n)) return hex;
  var r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  r = Math.round(r + (255 - r) * ratio);
  g = Math.round(g + (255 - g) * ratio);
  b = Math.round(b + (255 - b) * ratio);
  return 'rgb(' + r + ',' + g + ',' + b + ')';
}

// ================================================================
//  v35: 12 类技术指标（T10）
//  ---------------------------------------------------------------
//  数据来源：GET /api/kline/indicators（T03 后端，参数字典来自
//  INDICATOR_REGISTRY）。本段负责三件事：
//    ① 算法 JS 版（与 server/indicators.py 逐点等价，见 §「对拍」注释）——
//       用于「盘中最后一根本地重算」：SSE tick 到来时**不请求后端**，
//       由本段按当前 K 线重算序列，最后一根随之变化。
//    ② 副图渲染（7 个振荡/量纲指标）与主图 overlay 渲染（5 个价格量纲指标）。
//    ③ 参数标签（如 ADX(14)）与参数弹层（schema 来自后端）。
//
//  主图 Y 轴纪律：**主图 min/max 永远只由 K 线高低点决定**，overlay 指标
//  的值不参与主图刻度计算（否则 ichimoku 先行带/唐奇安外轨会把蜡烛压扁）。
//  做法见 _dpMainAxis / _dpClampToCandle。
//
//  配色纪律：方向性颜色只读 _themeUp/_themeDown（涨红跌绿），本段不写死色值。
// ================================================================

// ================================================================
//  v36: ICT 结构标注（FVG 缺口 / OB 订单块 / 流动性扫单）
//  ---------------------------------------------------------------
//  数据源：GET /api/ict/scan?code=&period=  ->  data.annotations
//    fvgs[]         {time, zone:[lo,hi], direction, ce, size}
//    order_blocks[] {direction, zone:[lo,hi], time, index, derived_from, note}
//    sweeps[]       {time, direction, kind, extreme, session, session_label}
//  注意：annotations 只给**最近 20 个 FVG**（不是全量），界面文案据此表述。
//
//  纪律：
//   ① 默认关闭（46 个框会糊满主图，用户会以为图坏了）；
//   ② 标注**不参与主图刻度**：ECharts 侧挂在独立 series 上（markArea + scatter），
//      Pro 侧走 overlay 绘制（overlay 天然不进 yAxis 计算）；
//   ③ 关闭时**物理移除**（ECharts 重新渲染不含该 series；Pro removeOverlay），
//      不是靠透明度隐藏；
//   ④ 分时（period=min / chartType=timeshare）下不画；
//   ⑤ 颜色只读 _themeUp/_themeDown（bullish/买方向=红，bearish/空方向=绿）；
//   ⑥ OB 的 note 原文必须出现在 tooltip 里，不许隐去不确定性。
// ================================================================

window._ict = {
  on: false,              // 开关状态（默认关）
  ttl: 120000,            // 同 code+period 120s 内不重复请求
  cache: {},              // `code:period` -> {annotations, fetchedAt}
  reqCount: 0,            // 本会话请求次数（验收断言用）
  overlays: 0,            // 当前挂在 Pro 图上的标注数（验收断言用）
  seriesPushed: false,    // ECharts 是否挂了 ICT series（验收断言用）
  lastKey: '',
  lastError: '',
  inflight: {},           // key -> true（并发去重，避免渲染钩子与手工切换各打一次）
  pending: {},            // key -> [cb...]（同键并发请求的回调队列）
  // v37: 历史统计（/api/ict/structure-stats）
  statsTtl: 300000,       // 5 分钟（冷启动要取 K 线约 3s，纯计算 6ms）
  statsCache: {},         // `code:period` -> {data, fetchedAt}
  statsInflight: {},
  statsPending: {},
  statsError: '',
  timing: {},             // {scan:{start,done}, stats:{start,done}} —— 用于验收「并发而非串行」
  pin: 0                  // tooltip 钉住到期时间（点击「外推警告」时钉住，便于阅读原文）
};

/** ICT 缓存键 */
function _ictKey() {
  var st = window.appState || {};
  var code = (_dpRenderCtx && _dpRenderCtx.code) || st.activeCode || '';
  var period = (_dpRenderCtx && _dpRenderCtx.period) || st.period || 'day';
  return code + ':' + period;
}

/** 当前是否处于分时（ICT 不适用） */
function _ictIsTimeshare() {
  var st = window.appState || {};
  if ((_dpRenderCtx && _dpRenderCtx.period) === 'min') return true;
  if (st.period === 'min') return true;
  if (typeof chartType !== 'undefined' && chartType === 'timeshare') return true;
  return false;
}

/** 开关状态（含分时抑制） */
function _ictActive() {
  return !!(window._ict && window._ict.on) && !_ictIsTimeshare();
}

/** 拉取 ICT 标注（仅开关打开时；120s 内同键不重复请求） */
function _ictFetch(cb) {
  if (!_ictActive()) { if (cb) cb(null); return; }
  var key = _ictKey();
  var st = window.appState || {};
  var _code = (_dpRenderCtx && _dpRenderCtx.code) || st.activeCode;
  var _period = (_dpRenderCtx && _dpRenderCtx.period) || st.period || 'day';
  if (!_code) { if (cb) cb(null); return; }
  var hit = window._ict.cache[key];
  var now = Date.now();
  if (hit && (now - hit.fetchedAt) < window._ict.ttl) { if (cb) cb(hit.annotations); return; }
  if (window._ict.inflight[key]) {          // 已有同键请求在飞 -> 排队回调，不重复打上游
    (window._ict.pending[key] = window._ict.pending[key] || []).push(cb || function () {});
    return;
  }
  window._ict.timing[key] = window._ict.timing[key] || {};
  window._ict.timing[key].scan = { start: Date.now(), done: 0 };
  window._ict.pending[key] = [cb || function () {}];
  window._ict.inflight[key] = true;
  var url = '/api/ict/scan?code=' + encodeURIComponent(_code)
    + '&period=' + encodeURIComponent(_period);
  window._ict.reqCount += 1;
  fetch(url).then(function (r) { return r.json(); }).then(function (j) {
    delete window._ict.inflight[key];
    if (window._ict.timing[key] && window._ict.timing[key].scan) window._ict.timing[key].scan.done = Date.now();
    var ann = (j && j.data && j.data.annotations) ? j.data.annotations : null;
    window._ict.cache[key] = { annotations: ann, fetchedAt: Date.now() };
    window._ict.lastError = (j && j.ok === false) ? (j.message || j.reason || '扫描失败') : '';
    if (cb) cb(ann);
  }).catch(function (e) {
    delete window._ict.inflight[key];
    window._ict.lastError = '扫描接口不可用：' + (e && e.message ? e.message : e);
    var cbs2 = window._ict.pending[key] || [];
    delete window._ict.pending[key];
    cbs2.forEach(function (fn) { try { fn(null); } catch (e2) {} });
  });
}

/** 渲染钩子调用：仅在「开关开 + 当前键无缓存」时请求一次（有缓存直接返回，不再渲染，避免回环） */
function _ictEnsure() {
  if (!_ictActive()) { window._ict.seriesPushed = false; return; }
  var hit = window._ict.cache[_ictKey()];
  if (hit && (Date.now() - hit.fetchedAt) < window._ict.ttl) return;
  _ictFetch(function () { _ictRender(); _ictUpdateLegend(); });
}

// -------------------- v37: 历史统计（结构 → 历史表现） --------------------
//  数据源：GET /api/ict/structure-stats?code=&period=day&limit=400
//  纪律：
//   ① 与 scan **并发**发出（互不等待），统计失败不影响标注绘制；
//   ② 结论口径照抄后端：负 edge 照实显示、无基准就写「无基准对照」、
//      样本 < 10 只报样本数**不报百分比**、每块末尾必须写「历史频率，非预测」；
//   ③ 缓存 5 分钟（冷启动要取 K 线，纯计算 6ms），同键并发单飞。

/** 当前键下的统计数据（无则 null） */
function _ictStatsData() {
  var hit = window._ict.statsCache[_ictKey()];
  return hit ? hit.data : null;
}

/**
 * 拉取结构历史统计
 * @param {Function} [cb] 统计到达后的回调（**不触发重绘**，tooltip 是 hover 时才读）
 */
function _ictFetchStats(cb) {
  var key = _ictKey();
  var st = window.appState || {};
  var code = (_dpRenderCtx && _dpRenderCtx.code) || st.activeCode;
  var period = (_dpRenderCtx && _dpRenderCtx.period) || st.period || 'day';
  if (!code) { if (cb) cb(null); return; }
  var hit = window._ict.statsCache[key];
  var now = Date.now();
  if (hit && (now - hit.fetchedAt) < window._ict.statsTtl) { if (cb) cb(hit.data); return; }
  if (window._ict.statsInflight[key]) {
    (window._ict.statsPending[key] = window._ict.statsPending[key] || []).push(cb || function () {});
    return;
  }
  window._ict.statsPending[key] = [cb || function () {}];
  window._ict.statsInflight[key] = true;
  window._ict.timing[key] = window._ict.timing[key] || {};
  window._ict.timing[key].stats = { start: Date.now(), done: 0 };
  var url = '/api/ict/structure-stats?code=' + encodeURIComponent(code)
    + '&period=' + encodeURIComponent(period) + '&limit=400';
  fetch(url).then(function (r) { return r.json(); }).then(function (j) {
    delete window._ict.statsInflight[key];
    if (window._ict.timing[key] && window._ict.timing[key].stats) window._ict.timing[key].stats.done = Date.now();
    var data = (j && j.data) ? j.data : null;
    window._ict.statsError = (j && j.ok === false) ? (j.message || j.reason || '统计不可用') : '';
    if (data) window._ict.statsCache[key] = { data: data, fetchedAt: Date.now() };
    var cbs = window._ict.statsPending[key] || [];
    delete window._ict.statsPending[key];
    cbs.forEach(function (fn) { try { fn(data); } catch (e) {} });
  }).catch(function (e) {
    delete window._ict.statsInflight[key];
    window._ict.statsError = '统计接口不可用：' + (e && e.message ? e.message : e);
    var cbs2 = window._ict.statsPending[key] || [];
    delete window._ict.statsPending[key];
    cbs2.forEach(function (fn) { try { fn(null); } catch (e2) {} });
  });
}

/** 百分比格式化（保留 2 位，不做四舍五入美化） */
function _ictPct(v) {
  return (typeof v === 'number' && isFinite(v)) ? (v.toFixed(2) + '%') : '—';
}

/** 百分点格式化：正数带 +，负数保留 − 号（负 edge 绝不含糊） */
function _ictPp(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '—';
  return (v > 0 ? '+' : (v < 0 ? '-' : '')) + Math.abs(v).toFixed(2) + 'pp';
}

/** 天数格式化：整数去掉 .0（1.0 -> '1'），非整数保留 2 位（3.86 -> '3.86'） */
function _ictDays(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '';
  return (Math.abs(v - Math.round(v)) < 1e-9) ? String(Math.round(v)) : v.toFixed(2);
}

/**
 * 「中位 X 天…（平均 Y 天）」文案
 * 口径纪律（后端 median 的分母是**命中数**，avg 的分母也是命中数）：
 *  - median 为 null -> **不显示任何天数**（绝不回退去用 avg 冒充，也绝不补 0）
 *  - median 有值、avg 为 null -> 只显示中位
 *  - 两者都有 -> 中位在前、平均在括号里，两个词不混用
 * @param {*} median 中位天数
 * @param {*} avg 平均天数
 * @param {string} verb '触及' | '反转' | '回踩'
 */
function _ictDaysText(median, avg, verb) {
  var hasMed = (typeof median === 'number' && isFinite(median));
  var hasAvg = (typeof avg === 'number' && isFinite(avg));
  if (!hasMed) return '';
  if (!hasAvg) return '中位 ' + _ictDays(median) + ' 天' + verb;
  return '中位 ' + _ictDays(median) + ' 天' + verb + '（平均 ' + _ictDays(avg) + ' 天）';
}

/** 样本充足性门槛（<10 只报样本数，不报比率） */
function _ictSampleOk(samples) {
  return typeof samples === 'number' && samples >= 10;
}

/** 「历史频率，非预测」灰字（每块统计末尾强制出现） */
function _ictNotPredict(text) {
  var t = text || '历史频率，非预测';
  return '<div style="font-size:9px;color:#3d5278;margin-top:1px">' + t + '</div>';
}

/** 单标的外推警告（短版 + 点击展开原文） */
function _ictCaveatHtml(forceOpen) {
  var st = _ictStatsData();
  var full = (st && st.verdict && st.verdict.single_symbol_caveat) ? st.verdict.single_symbol_caveat : '';
  var open = !!forceOpen || !!window._ict._caveatOpen;
  var html = '<div style="margin-top:3px;font-size:9px;color:#f59e0b">'
    + '<span style="cursor:pointer;text-decoration:underline dotted" data-ict-caveat="1" title="' + (full || '单标的结论不可外推') + '">单标的结论不可外推' + (full && !open ? '（点击展开）' : '') + '</span>';
  if (open && full) html += '<div style="margin-top:2px;color:#8b9ec2;font-size:9px;line-height:1.5">' + full + '</div>';
  html += '</div>';
  return html;
}

/**
 * 结构历史统计区块（FVG / OB / SWEEP 三种口径）
 * @param {string} type 'FVG' | 'OB' | 'SWEEP'
 * @returns {string} HTML（统计未到达时返回空串，不影响标注本身）
 */
function _ictStatsHtml(type) {
  var st = _ictStatsData();
  if (!st) return '';
  var days = st.candles || (st.baseline && st.baseline.samples) || null;
  var win = days ? ('近 ' + days + ' 日') : '统计窗口';
  var out = '';

  if (type === 'FVG') {
    var f = (st.fvg && st.fvg.overall) ? st.fvg.overall : null;
    if (!f) return '';
    if (!_ictSampleOk(f.samples)) {
      return '<div style="margin-top:4px;padding-top:3px;border-top:1px solid rgba(255,255,255,.08);font-size:10px;color:#8b9ec2">'
        + '同类 FVG 历史（' + win + '）：样本 ' + (f.samples == null ? '—' : f.samples) + ' 个，不足 10 个，不具参考价值'
        + '</div>' + _ictNotPredict();
    }
    var baseP = (st.baseline && typeof st.baseline.fvg_touch_rate_pct === 'number') ? st.baseline.fvg_touch_rate_pct : f.baseline_partial_pct;
    var pp = (st.edge && typeof st.edge.fvg_partial_fill_vs_baseline_pp === 'number') ? st.edge.fvg_partial_fill_vs_baseline_pp : null;
    // 命中数口径：median/avg 的分母都是 partial_hits，故写「91/103 命中」（不用 samples 对中位数，否则读者算不平）
    var hitTxt = (f.partial_hits != null && f.samples != null) ? (f.partial_hits + '/' + f.samples + ' 命中') : ('N=' + f.samples);
    var dayTxt = _ictDaysText(f.median_days_to_partial, f.avg_days_to_partial, '触及');
    out = '<div style="margin-top:4px;padding-top:3px;border-top:1px solid rgba(255,255,255,.08);font-size:10px;color:#c3ccdb">'
      + '同类 FVG 历史（' + win + '，' + hitTxt + '）' + (dayTxt ? ('：' + dayTxt) : '')
      + '<br>触及率 ' + _ictPct(f.partial_fill_rate_pct) + '（同波动基准 ' + _ictPct(baseP) + '，' + _ictPp(pp) + '）'
      + '</div>' + _ictNotPredict();
  } else if (type === 'OB') {
    var o = (st.ob && st.ob.overall) ? st.ob.overall : null;
    if (!o) return '';
    if (!_ictSampleOk(o.samples)) {
      return '<div style="margin-top:4px;padding-top:3px;border-top:1px solid rgba(255,255,255,.08);font-size:10px;color:#8b9ec2">'
        + 'OB 历史（' + win + '）：样本 ' + (o.samples == null ? '—' : o.samples) + ' 个，不足 10 个，不具参考价值'
        + '</div>' + _ictNotPredict();
    }
    // 无基准就直说「无基准对照」，绝不伪造对比
    var obPp = (st.edge && typeof st.edge.ob_touch_vs_baseline_pp === 'number') ? st.edge.ob_touch_vs_baseline_pp : null;
    var obTail = (obPp == null) ? '无基准对照' : ('同波动基准 ' + _ictPp(obPp));
    var obHit = (o.hits != null && o.samples != null) ? (o.hits + '/' + o.samples + ' 命中') : ('N=' + o.samples);
    var obDay = _ictDaysText(o.median_days_to_touch, o.avg_days_to_touch, '回踩');
    out = '<div style="margin-top:4px;padding-top:3px;border-top:1px solid rgba(255,255,255,.08);font-size:10px;color:#c3ccdb">'
      + 'OB 历史回踩率 ' + _ictPct(o.touch_rate_pct) + '（' + obHit + '，' + obTail + '）'
      + (obDay ? ('<br>' + obDay) : '')
      + '</div>' + _ictNotPredict();
  } else if (type === 'SWEEP') {
    var sw = (st.sweep && st.sweep.overall) ? st.sweep.overall : null;
    if (!sw) return '';
    if (!_ictSampleOk(sw.samples)) {
      return '<div style="margin-top:4px;padding-top:3px;border-top:1px solid rgba(255,255,255,.08);font-size:10px;color:#8b9ec2">'
        + '同类扫单历史（' + win + '）：样本 ' + (sw.samples == null ? '—' : sw.samples) + ' 个，不足 10 个，不具参考价值'
        + '</div>' + _ictNotPredict();
    }
    var sPp = (st.edge && typeof st.edge.sweep_reversal_vs_baseline_pp === 'number') ? st.edge.sweep_reversal_vs_baseline_pp : null;
    var swHit = (sw.hits != null && sw.samples != null) ? (sw.hits + '/' + sw.samples + ' 命中') : ('N=' + sw.samples);
    var swDay = _ictDaysText(sw.median_days_to_reversal, sw.avg_days_to_reversal, '反转');
    out = '<div style="margin-top:4px;padding-top:3px;border-top:1px solid rgba(255,255,255,.08);font-size:10px;color:#c3ccdb">'
      + '同类扫单历史（' + win + '，' + swHit + '）' + (swDay ? ('：' + swDay) : '')
      + '<br>反转率 ' + _ictPct(sw.reversal_rate_pct)
      + '（同波动基准 ' + _ictPct(sw.baseline_reversal_pct) + '，' + _ictPp(sPp) + '）'
      + '</div>' + _ictNotPredict();
  }
  return out;
}

/** tooltip 里的完整统计块（含外推警告） */
function _ictStatsBlock(type) {
  var body = _ictStatsHtml(type);
  if (!body) return '';
  return '<div style="margin-top:4px">' + body + _ictCaveatHtml(false) + '</div>';
}

/** 取当前已缓存的标注（无则 null） */
function _ictAnnotations() {
  var hit = window._ict.cache[_ictKey()];
  return hit ? hit.annotations : null;
}

/**
 * ICT 标注时间 -> 本地自然日 'YYYY-MM-DD'
 * 后端给的是 ISO（如 '2026-07-14T16:00:00Z'，即北京时间 2026-07-15 00:00），
 * 日线按北京时间（+08:00）换算回自然日再与 K 线日期比对，避免整体差一天。
 * 纯日期串（'2026-07-15' / '20260715'）原样归一。
 */
function _ictLocalDate(t) {
  var s = String(t == null ? '' : t).trim();
  if (!s) return '';
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  if (/^\d{8}$/.test(s)) return s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8);
  var ms = Date.parse(s);
  if (isNaN(ms)) return s.slice(0, 10);
  var d = new Date(ms + 8 * 3600 * 1000);   // 固定按北京时间取日
  var m = d.getUTCMonth() + 1, day = d.getUTCDate();
  return d.getUTCFullYear() + '-' + (m < 10 ? '0' : '') + m + '-' + (day < 10 ? '0' : '') + day;
}

/** K 线日期串（'2026-07-15' / '20260715' / '2026-07-15 10:30'）-> 'YYYY-MM-DD' */
function _ictNormBarDate(d) {
  var s = String(d == null ? '' : d).trim();
  var m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return m[1] + '-' + m[2] + '-' + m[3];
  var dg = s.replace(/[^0-9]/g, '');
  if (dg.length >= 8) return dg.slice(0, 4) + '-' + dg.slice(4, 6) + '-' + dg.slice(6, 8);
  return s;
}

/** 日期 -> 轴索引（同名日期取第一根） */
function _ictDateIndex(dates) {
  var idx = {};
  for (var i = 0; i < dates.length; i++) {
    var k = _ictNormBarDate(dates[i]);
    if (k && !Object.prototype.hasOwnProperty.call(idx, k)) idx[k] = i;
  }
  return idx;
}

/** 方向 -> 颜色（bullish/long 用涨色，bearish/short 用跌色；只读主题变量） */
function _ictColor(direction) {
  var d = String(direction || '').toLowerCase();
  if (d === 'bullish' || d === 'long' || d === 'up') return _themeUp;
  if (d === 'bearish' || d === 'short' || d === 'down') return _themeDown;
  return '#8b9ec2';
}

/** 方向中文（tooltip 用） */
function _ictDirLabel(direction) {
  var d = String(direction || '').toLowerCase();
  if (d === 'bullish' || d === 'long' || d === 'up') return '看涨';
  if (d === 'bearish' || d === 'short' || d === 'down') return '看跌';
  return d || '—';
}

/** ICT 开关（工具条按钮调用） */
window.toggleIct = function (force) {
  var on = (typeof force === 'boolean') ? force : !window._ict.on;
  window._ict.on = on;
  var btn = document.getElementById('ibt-ict');
  if (btn) { btn.classList.toggle('active', on); btn.setAttribute('aria-pressed', String(on)); }
  try { localStorage.setItem('dapeng_ict_on', on ? '1' : '0'); } catch (e) {}
  if (!on) {
    // 关闭：物理移除（ECharts 重渲染不再包含该 series；Pro 逐条删 overlay）
    _ictClearPro();
    window._ict.seriesPushed = false;
    _rerenderKlineWithCache();
    _ictUpdateLegend();
    return;
  }
  if (_ictIsTimeshare()) {
    showToast('ICT 结构', '分时模式不显示 ICT 结构标注（日线结构对分时无意义），请切到日/周/月线');
    _ictUpdateLegend();
    return;
  }
  // v37: 两份数据**并发**发出（不互相等待）；统计不阻塞标注绘制——先画结构，hover 时才补统计
  _ictFetchStats(function () { _ictUpdateLegend(); });
  _ictFetch(function (ann) {
    var n = ann ? ((ann.fvgs || []).length + (ann.order_blocks || []).length + (ann.sweeps || []).length) : 0;
    showToast('ICT 结构', n ? ('已标注 ' + (ann.fvgs || []).length + ' 个 FVG、' + (ann.order_blocks || []).length + ' 个 OB、' + (ann.sweeps || []).length + ' 个扫单（最近 20 个 FVG，非全量）')
      : ('未取到结构标注' + (window._ict.lastError ? '：' + window._ict.lastError : '')));
    _ictRender();
    _ictUpdateLegend();
  });
};

/** 图例/状态文案（挂在工具条按钮 title 上，不新增 DOM 区块） */
function _ictUpdateLegend() {
  var btn = document.getElementById('ibt-ict');
  if (!btn) return;
  var ann = _ictAnnotations();
  var n = ann ? ((ann.fvgs || []).length + (ann.order_blocks || []).length + (ann.sweeps || []).length) : 0;
  var base = 'ICT 结构：FVG 缺口 / OB 订单块 / 流动性扫单（默认关闭）';
  btn.title = window._ict.on
    ? (base + '\n已开启：' + (ann ? ('FVG ' + (ann.fvgs || []).length + ' / OB ' + (ann.order_blocks || []).length + ' / 扫单 ' + (ann.sweeps || []).length + '（FVG 仅最近 20 个）') : '数据加载中…') + '\n红=看涨结构，绿=看跌结构')
    : base;
}

// -------------------- ECharts 渲染 --------------------

/**
 * 构建 ICT 的 ECharts series（独立 series，**不参与主图刻度**）
 * 用 markArea 画 FVG/OB 带（从 time 向右延伸），用 scatter 画 sweep 三角。
 * 该 series 的 yAxisIndex 绑 0，但主图 min/max 已由 _dpMainAxis 显式给定（只读 K 线），
 * 因此标注不会改变主图刻度。
 */
function _ictEchartsSeries(kls, dates) {
  var ann = _ictAnnotations();
  if (!ann || !kls || !kls.length) return null;
  var lastDate = dates[dates.length - 1];
  var index = _ictDateIndex(dates);      // 归一日期 -> 轴索引
  var areas = [];
  var scatter = [];
  var skipped = 0;
  var fvgs = ann.fvgs || [], obs = ann.order_blocks || [], sweeps = ann.sweeps || [];

  // 标注时间 -> 轴上的日期串（匹配不到则跳过并计数，不臆造位置）
  function axisDate(t) {
    var i = index[_ictLocalDate(t)];
    return (i == null) ? null : dates[i];
  }

  fvgs.forEach(function (f) {
    if (!f || !f.zone || f.zone.length < 2 || !f.time) { skipped++; return; }
    var lo = Math.min(Number(f.zone[0]), Number(f.zone[1]));
    var hi = Math.max(Number(f.zone[0]), Number(f.zone[1]));
    if (!isFinite(lo) || !isFinite(hi)) { skipped++; return; }
    var x0 = axisDate(f.time);
    if (x0 == null) { skipped++; return; }
    var c = _ictColor(f.direction);
    areas.push([
      { xAxis: x0, itemStyle: { color: _hexToRgba(c, 0.10) },
        ict: { type: 'FVG', dir: f.direction, zone: [lo, hi], ce: f.ce, size: f.size } },
      { xAxis: lastDate }
    ]);
  });

  obs.forEach(function (o) {
    if (!o || !o.zone || o.zone.length < 2 || !o.time) { skipped++; return; }
    var lo = Math.min(Number(o.zone[0]), Number(o.zone[1]));
    var hi = Math.max(Number(o.zone[0]), Number(o.zone[1]));
    if (!isFinite(lo) || !isFinite(hi)) { skipped++; return; }
    var x0 = axisDate(o.time);
    if (x0 == null) { skipped++; return; }
    var c = _ictColor(o.direction);
    areas.push([
      { xAxis: x0, itemStyle: { color: 'transparent', borderColor: _hexToRgba(c, 0.55), borderWidth: 1, borderType: 'dashed' },
        ict: { type: 'OB', dir: o.direction, zone: [lo, hi], note: o.note, derived_from: o.derived_from } },
      { xAxis: lastDate }
    ]);
  });

  sweeps.forEach(function (sw) {
    if (!sw || !sw.time || !isFinite(Number(sw.extreme))) { skipped++; return; }
    var x0 = axisDate(sw.time);
    if (x0 == null) { skipped++; return; }
    var c = _ictColor(sw.direction);
    scatter.push({
      value: [x0, Number(sw.extreme)],
      itemStyle: { color: c },
      symbol: 'triangle',
      symbolSize: 8,
      ict: { type: 'SWEEP', kind: sw.kind, dir: sw.direction, session_label: sw.session_label, extreme: sw.extreme }
    });
  });
  window._ict.skipped = skipped;

  return {
    name: 'ICT结构',
    type: 'scatter',
    xAxisIndex: 0,
    yAxisIndex: 0,
    data: scatter,
    symbol: 'triangle',
    symbolSize: 8,
    z: 7,
    _dpIct: true,
    markArea: areas.length ? { silent: false, label: { show: false }, emphasis: { disabled: true }, data: areas } : undefined
  };
}

// -------------------- Pro（klinecharts）渲染 --------------------

var _ictOverlayRegistered = false;

/** 注册 ICT 的两个 overlay 模板（矩形带 / 三角标记） */
function _ictRegisterOverlays() {
  if (_ictOverlayRegistered) return;
  if (typeof klinecharts === 'undefined' || !klinecharts.registerOverlay) return;

  // 区间带：2 个点 -> 矩形（FVG 实填 / OB 虚线边框）
  klinecharts.registerOverlay({
    name: 'dpIctZone',
    totalStep: 3,
    lock: true,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: function (params) {
      var co = params.coordinates || [];
      var ov = params.overlay;
      if (co.length < 2 || !co[0] || !co[1] || !ov) return [];
      var d = ov.extendData || {};
      var c = _ictColor(d.direction);
      var x = Math.min(co[0].x, co[1].x);
      var y = Math.min(co[0].y, co[1].y);
      var w = Math.abs(co[1].x - co[0].x);
      var h = Math.abs(co[1].y - co[0].y);
      var isOb = d.type === 'OB';
      return [{
        type: 'rect',
        attrs: { x: x, y: y, width: Math.max(1, w), height: Math.max(1, h) },
        styles: isOb
          ? { style: 'stroke', color: 'rgba(0,0,0,0)', borderColor: _hexToRgba(c, 0.55), borderSize: 1, borderStyle: 'dashed', borderDashedValue: [3, 3] }
          : { style: 'fill', color: _hexToRgba(c, 0.10), borderColor: _hexToRgba(c, 0.22), borderSize: 1 }
      }];
    }
  });

  // 扫单标记：1 个点 -> 小三角
  klinecharts.registerOverlay({
    name: 'dpIctMark',
    totalStep: 2,
    lock: true,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    onMouseEnter: function (event) {
      var d = (event && event.overlay && event.overlay.extendData) || {};
      _ictShowTip(event, d);
    },
    onMouseLeave: function () { _ictHideTip(); },
    createPointFigures: function (params) {
      var co = params.coordinates || [];
      var ov = params.overlay;
      if (!co.length || !ov) return [];
      var d = ov.extendData || {};
      var c = _ictColor(d.direction);
      var base = co[0];
      var up = d.kind === 'sellside_sweep';
      return [{
        type: 'polygon',
        attrs: { coordinates: up
          ? [{ x: base.x, y: base.y - 6 }, { x: base.x - 5, y: base.y + 4 }, { x: base.x + 5, y: base.y + 4 }]
          : [{ x: base.x, y: base.y + 6 }, { x: base.x - 5, y: base.y - 4 }, { x: base.x + 5, y: base.y - 4 }] },
        styles: { style: 'fill', color: c, borderColor: c, borderSize: 1 }
      }];
    }
  });
  _ictOverlayRegistered = true;
}

/** 清空 Pro 上的 ICT overlay（关闭开关 / 切标的前调用） */
function _ictClearPro() {
  window._ict.overlays = 0;
  if (typeof proChart === 'undefined' || !proChart || !proChart.removeOverlay) return;
  try { proChart.removeOverlay({ name: 'dpIctZone' }); } catch (e) {}
  try { proChart.removeOverlay({ name: 'dpIctMark' }); } catch (e) {}
}

/** Pro 侧渲染：把 FVG/OB 画成矩形带、sweep 画成三角 */
function _ictRenderPro() {
  if (typeof proChart === 'undefined' || !proChart || !proChart.createOverlay) return;
  _ictClearPro();
  if (!_ictActive()) return;
  _ictRegisterOverlays();          // 必须先注册模板，否则 createOverlay 会静默返回 null
  var ann = _ictAnnotations();
  if (!ann) return;
  var bars = (typeof proDataStore !== 'undefined' && proDataStore && proDataStore.bars) ? proDataStore.bars : [];
  if (!bars.length) return;
  var dateIndex = {};
  for (var i = 0; i < bars.length; i++) {
    var ds = _ictNormBarDate(_kfsDateStr(bars[i].timestamp));
    if (ds && !Object.prototype.hasOwnProperty.call(dateIndex, ds)) dateIndex[ds] = i;
  }
  var lastTs = bars[bars.length - 1].timestamp;
  var ovs = [];
  var skip = 0;

  function pushZone(item, type) {
    if (!item || !item.zone || item.zone.length < 2 || !item.time) { skip++; return; }
    var bi = dateIndex[_ictLocalDate(item.time)];
    if (bi == null) { skip++; return; }
    var lo = Math.min(Number(item.zone[0]), Number(item.zone[1]));
    var hi = Math.max(Number(item.zone[0]), Number(item.zone[1]));
    if (!isFinite(lo) || !isFinite(hi)) { skip++; return; }
    ovs.push({
      name: 'dpIctZone',
      paneId: 'candle_pane',
      lock: true,
      points: [{ timestamp: bars[bi].timestamp, value: hi }, { timestamp: lastTs, value: lo }],
      extendData: { type: type, direction: item.direction, note: item.note, zone: [lo, hi], ce: item.ce, size: item.size }
    });
  }

  (ann.fvgs || []).forEach(function (f) { pushZone(f, 'FVG'); });
  (ann.order_blocks || []).forEach(function (o) { pushZone(o, 'OB'); });

  (ann.sweeps || []).forEach(function (sw) {
    if (!sw || !sw.time || !isFinite(Number(sw.extreme))) { skip++; return; }
    var bi = dateIndex[_ictLocalDate(sw.time)];
    if (bi == null) { skip++; return; }
    ovs.push({
      name: 'dpIctMark',
      paneId: 'candle_pane',
      lock: true,
      points: [{ timestamp: bars[bi].timestamp, value: Number(sw.extreme) }],
      extendData: { type: 'SWEEP', kind: sw.kind, direction: sw.direction, session_label: sw.session_label, extreme: sw.extreme }
    });
  });

  if (ovs.length) {
    try { proChart.createOverlay(ovs); } catch (e) { console.error('[DAPENG] ICT 标注渲染失败:', e); }
  }
  window._ict.overlays = ovs.length;
  window._ict.skipped = skip;
}

/** 统一渲染入口：按当前生效引擎分发 */
function _ictRender() {
  if (!_ictActive()) { _ictClearPro(); return; }
  if (window._proChartDisabled !== true && typeof proChart !== 'undefined' && proChart) {
    _ictRenderPro();
  } else {
    // ECharts：重新渲染（renderKline 会按开关状态决定是否挂 ICT series）
    _rerenderKlineWithCache();
  }
}

// -------------------- 悬浮提示（OB note / sweep session_label 原文） --------------------

function _ictTipEl() {
  var el = document.getElementById('dp-ict-tip');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'dp-ict-tip';
  el.style.cssText = 'position:fixed;z-index:9999;max-width:320px;padding:8px 10px;border-radius:8px;'
    + 'background:rgba(6,12,28,.97);border:1px solid rgba(59,130,246,.35);color:#e2e8f0;'
    + 'font:11px/1.5 "SF Mono",monospace;box-shadow:0 10px 30px rgba(0,0,0,.6);display:none;pointer-events:none';
  document.body.appendChild(el);
  return el;
}

function _ictShowTip(event, d) {
  if (!d) return;
  window._ict.lastTipData = d;
  window._ict.lastTipType = d.type;
  var el = _ictTipEl();
  var c = _ictColor(d.direction);
  var html = '';
  if (d.type === 'SWEEP') {
    html = '<div style="color:' + c + ';font-weight:700;margin-bottom:3px">流动性扫单 ' + (d.kind || '') + '</div>'
      + '<div>方向：' + _ictDirLabel(d.direction) + '</div>'
      + '<div>极值：' + Number(d.extreme).toFixed(2) + '</div>'
      + '<div>时段：' + (d.session_label || '—') + '</div>';
  } else {
    html = '<div style="color:' + c + ';font-weight:700;margin-bottom:3px">' + (d.type === 'OB' ? '订单块 OB' : 'FVG 缺口') + '（' + _ictDirLabel(d.direction) + '）</div>'
      + '<div>区间：' + Number(d.zone[0]).toFixed(2) + ' ~ ' + Number(d.zone[1]).toFixed(2) + '</div>'
      + (d.ce != null ? '<div>中位 CE：' + Number(d.ce).toFixed(2) + (d.size != null ? '（' + Number(d.size).toFixed(2) + '）' : '') + '</div>' : '')
      + (d.note ? '<div style="margin-top:4px;color:#f59e0b;font-size:10px">' + d.note + '</div>' : '');
  }
  // v37: 结构历史统计（统计未到达时不显示，标注本身不受影响）
  try { html += _ictStatsBlock(d.type); } catch (e) {}
  el.innerHTML = html;
  var x = (event && event.event && event.event.clientX) || (event && event.x) || 0;
  var y = (event && event.event && event.event.clientY) || (event && event.y) || 0;
  el.style.left = Math.min(window.innerWidth - 330, Math.max(8, x + 14)) + 'px';
  el.style.top = Math.min(window.innerHeight - 120, Math.max(8, y + 14)) + 'px';
  el.style.display = 'block';
}

function _ictHideTip() {
  if (window._ict && window._ict.pin && Date.now() < window._ict.pin) return;  // 钉住期内不移除
  var el = document.getElementById('dp-ict-tip');
  if (el) el.style.display = 'none';
}

/**
 * v37: 外推警告「点击展开原文」
 * tooltip 本体 pointer-events:none，只有这条警告行可点（pointer-events:auto），
 * 点击后把原文展开并把 tooltip 钉住 8s，方便阅读。
 */
(function () {
  var ensureClickable = function () {
    var el = document.getElementById('dp-ict-tip');
    if (el && el.getAttribute('data-bind') !== '1') {
      el.setAttribute('data-bind', '1');
      el.addEventListener('click', function (ev) {
        var t = ev.target;
        if (!t || !t.getAttribute || !t.getAttribute('data-ict-caveat')) return;
        window._ict._caveatOpen = !window._ict._caveatOpen;
        window._ict.pin = Date.now() + 8000;
        // 展开后重绘当前 tooltip 文本
        var host = document.getElementById('dp-ict-tip');
        if (host && window._ict.lastTipType) {
          var d = window._ict.lastTipData || {};
          _ictShowTip({ x: parseInt(host.style.left, 10), y: parseInt(host.style.top, 10) }, d);
          host.style.display = 'block';
        }
      });
    }
  };
  document.addEventListener('mouseover', function (ev) {
    var t = ev.target;
    if (t && t.getAttribute && t.getAttribute('data-ict-caveat')) {
      var el = document.getElementById('dp-ict-tip');
      if (el) { el.style.pointerEvents = 'auto'; el.style.display = 'block'; }
    }
    ensureClickable();
  }, true);
})();

/** v35: 扩展指标名（顺序 = 按钮顺序），前 5 个为主图 overlay */
var _DP_EXTRA_NAMES = ['supertrend', 'ichimoku', 'donchian', 'keltner', 'vwap',
                       'adx', 'aroon', 'obv', 'mfi', 'atr', 'stoch', 'cci'];

/** v35: 非方向性线条调色板（与既有 MA/KDJ 线同一套视觉语言，非涨跌色） */
var _DP_LINE_COLORS = ['#3b82f6', '#f59e0b', '#a855f7', '#06b6d4', '#8b9ec2', '#eab308'];

/** v35: 静态兜底元数据（catalog 未取到时仍可渲染；字段与后端 INDICATOR_REGISTRY 一致） */
var _DP_FALLBACK_META = {
  supertrend: { label: 'SuperTrend', overlay: true, panes: ['st_line', 'st_dir'], primary: 'st_line', value_range: [null, null], params: { period: { type: 'int', default: 10, min: 2, max: 100, unit: '根', desc: 'ATR 周期' }, mult: { type: 'float', default: 3, min: 0.5, max: 10, unit: '倍', desc: 'ATR 倍数' } } },
  ichimoku:   { label: '一目均衡表', overlay: true, panes: ['tenkan', 'kijun', 'senkou_a', 'senkou_b', 'chikou'], primary: 'tenkan', value_range: [null, null], params: { tenkan: { type: 'int', default: 9, min: 2, max: 100, unit: '根', desc: '转换线周期' }, kijun: { type: 'int', default: 26, min: 2, max: 200, unit: '根', desc: '基准线周期' }, senkou_b: { type: 'int', default: 52, min: 2, max: 400, unit: '根', desc: '先行带B周期' }, shift: { type: 'int', default: 26, min: 0, max: 200, unit: '根', desc: '前移/后移根数' } } },
  donchian:   { label: '唐奇安通道', overlay: true, panes: ['upper', 'middle', 'lower'], primary: 'middle', value_range: [null, null], params: { period: { type: 'int', default: 20, min: 2, max: 250, unit: '根', desc: '通道周期' } } },
  keltner:    { label: '肯特纳通道', overlay: true, panes: ['upper', 'middle', 'lower'], primary: 'middle', value_range: [null, null], params: { period: { type: 'int', default: 20, min: 2, max: 250, unit: '根', desc: '中轨EMA周期' }, mult: { type: 'float', default: 2, min: 0.5, max: 6, unit: '倍', desc: 'ATR倍数' }, atr_period: { type: 'int', default: 20, min: 2, max: 250, unit: '根', desc: 'ATR周期' } } },
  vwap:       { label: 'VWAP', overlay: true, panes: ['vwap'], primary: 'vwap', value_range: [null, null], params: {} },
  adx:        { label: 'ADX', overlay: false, panes: ['adx', 'plus_di', 'minus_di'], primary: 'adx', value_range: [0, 100], params: { period: { type: 'int', default: 14, min: 2, max: 200, unit: '根', desc: '计算窗口根数' } } },
  aroon:      { label: 'Aroon', overlay: false, panes: ['aroon_up', 'aroon_down', 'aroon_osc'], primary: 'aroon_up', value_range: [0, 100], params: { period: { type: 'int', default: 25, min: 2, max: 250, unit: '根', desc: '回溯窗口根数' } } },
  obv:        { label: 'OBV', overlay: false, panes: ['obv'], primary: 'obv', value_range: [null, null], params: {} },
  mfi:        { label: 'MFI', overlay: false, panes: ['mfi'], primary: 'mfi', value_range: [0, 100], params: { period: { type: 'int', default: 14, min: 2, max: 200, unit: '根', desc: '计算窗口根数' } } },
  atr:        { label: 'ATR', overlay: false, panes: ['atr'], primary: 'atr', value_range: [0, null], params: { period: { type: 'int', default: 14, min: 2, max: 200, unit: '根', desc: '计算窗口根数' } } },
  stoch:      { label: 'KDJ', overlay: false, panes: ['k', 'd', 'j'], primary: 'k', value_range: [0, 100], params: { k_period: { type: 'int', default: 9, min: 2, max: 100, unit: '根', desc: 'K值窗口' }, d_period: { type: 'int', default: 3, min: 1, max: 50, unit: '根', desc: 'D值平滑窗口' } } },
  cci:        { label: 'CCI', overlay: false, panes: ['cci'], primary: 'cci', value_range: [null, null], params: { period: { type: 'int', default: 20, min: 2, max: 250, unit: '根', desc: '计算窗口根数' } } },
};

/** v35: 指标运行态（命名空间统一挂在 window._dpInd，便于诊断与验收） */
// v36: 当前「图上实际渲染」的标的/周期/K线（单一事实源）
// 背景：appState.activeCode 可能被其它自动刷新流程改走（实测出现「图上是 sh600519、
// appState 是 sh603501」），若用它作缓存键/取数键，会把 A 股的指标算到 B 股图上。
var _dpRenderCtx = { code: '', period: '', klines: [] };

window._dpInd = {
  meta: {},              // name -> 元数据（catalog 或兜底）
  catalogLoaded: false,
  apiCache: {},          // `${code}:${period}:${name}` -> {panes, params, label, fetchedAt}
  params: {},            // name -> {pname: value} 用户参数（与后端 params 位置语义互通）
  reqCount: 0,           // 本会话 /api/kline/indicators 请求次数（验收断言用）
  jsComputeCount: 0,     // 本会话本地重算次数（验收断言用）
  lastTickLocal: null,   // 最近一次本地重算的证据 {name, index, value, at}
  axisLog: []            // 主图 Y 轴取值日志（验收断言用，仅保留最近 20 条）
};

/** 扩展指标（含别名归一） */
function _dpNormalizeName(name) {
  var n = String(name || '').trim().toLowerCase();
  var alias = { kc: 'keltner', chop: 'choppiness', wr: 'williams_r', kdj: 'stoch', st: 'supertrend', williams: 'williams_r' };
  return alias[n] || n;
}

/** 是否为 v35 扩展指标 */
function _dpIsExtra(name) {
  return _DP_EXTRA_NAMES.indexOf(_dpNormalizeName(name)) >= 0;
}

/** 该指标是否叠主图 */
function _dpIsOverlay(name) {
  var m = _dpMeta(name);
  return !!(m && m.overlay);
}

/** 指标元数据（catalog 优先，静态兜底） */
function _dpMeta(name) {
  var n = _dpNormalizeName(name);
  return window._dpInd.meta[n] || _DP_FALLBACK_META[n] || null;
}

/** 生效参数（用户自定义 > 元数据默认） */
function _dpParams(name) {
  var n = _dpNormalizeName(name);
  var meta = _dpMeta(n) || {};
  var schema = meta.params || {};
  var saved = window._dpInd.params[n] || {};
  var out = {};
  Object.keys(schema).forEach(function (k) {
    var sp = schema[k] || {};
    var v = saved[k];
    if (v == null || !isFinite(Number(v))) v = sp.default;
    v = Number(v);
    if (sp.min != null) v = Math.max(Number(sp.min), v);
    if (sp.max != null) v = Math.min(Number(sp.max), v);
    out[k] = (sp.type === 'int') ? Math.round(v) : v;
  });
  return out;
}

/** 参数数组（位置语义，用于请求 /api/kline/indicators?params=a,b） */
function _dpParamList(name) {
  var p = _dpParams(name);
  return Object.keys(p).map(function (k) { return p[k]; });
}

/** 参数标签，如 ADX(14) / SuperTrend(10,3.0) / 一目均衡表(9,26,52) */
function _dpLabel(name, params) {
  var n = _dpNormalizeName(name);
  var meta = _dpMeta(n) || {};
  // catalog 的 label 可能已带默认参数（如 'ADX(14)'），去掉括号部分再按生效参数重拼，避免 '(14)(14)'
  var base = String(meta.label || n.toUpperCase()).replace(/\s*\([^)]*\)\s*$/, '');
  var p = params || _dpParams(n);
  var keys = Object.keys(p);
  if (!keys.length) return base;
  // 派生参数不入标签（与后端 label_params 口径一致）
  if (n === 'ichimoku') keys = ['tenkan', 'kijun', 'senkou_b'];
  if (n === 'keltner') keys = ['period', 'mult'];
  var parts = keys.filter(function (k) { return p[k] != null; }).map(function (k) {
    var v = Number(p[k]);
    var sp = (meta.params || {})[k] || {};
    if (sp.type === 'float') {
      var s = String(Math.round(v * 1000) / 1000);
      return s.indexOf('.') < 0 ? s + '.0' : s;
    }
    return String(Math.round(v));
  });
  return parts.length ? (base + '(' + parts.join(',') + ')') : base;
}

/**
 * 按 catalog 的 overlay 字段把 12 个按钮归位到「叠加 / 振荡」两组
 * 前端不持有「哪几个是 overlay」的硬编码：分组、按钮文案、title 全部来自后端。
 * （HTML 里的初始分组只是 catalog 到达前的静态骨架，这里会按后端结果纠正。）
 */
function _dpApplyCatalogToButtons() {
  var gOver = document.getElementById('dp-ind-group-overlay');
  var gOsc = document.getElementById('dp-ind-group-osc');
  if (!gOver || !gOsc) return;
  var moved = 0;
  _DP_EXTRA_NAMES.forEach(function (name) {
    var btn = document.getElementById('ibt-' + name);
    if (!btn) return;
    var meta = _dpMeta(name) || {};
    var target = meta.overlay ? gOver : gOsc;
    if (btn.parentNode !== target) { target.appendChild(btn); moved++; }
    btn.setAttribute('data-ind-overlay', meta.overlay ? 'true' : 'false');
    if (meta.desc) btn.setAttribute('title', meta.desc);
    if (meta.label) {
      var short = String(meta.label).split('(')[0];
      if (short && btn.textContent.trim().length > 8) btn.textContent = short;
    }
  });
  return moved;
}

/** 拉取指标目录（一次；失败静默走静态兜底） */
function _dpLoadCatalog() {
  if (window._dpInd.catalogLoaded) return;
  window._dpInd.catalogLoaded = true;   // 先占位，避免并发重复请求
  try {
    fetch('/api/kline/indicators/catalog').then(function (r) { return r.json(); }).then(function (j) {
      if (!j || !j.ok || !j.data || !j.data.indicators) return;
      j.data.indicators.forEach(function (it) {
        if (it && it.name) window._dpInd.meta[it.name] = it;
      });
      _dpApplyCatalogToButtons();     // 分组由 catalog 的 overlay 决定，不写死
      if (typeof _dpRenderParamRows === 'function') _dpRenderParamRows();
    }).catch(function () { /* 兜底元数据可用，不影响渲染 */ });
  } catch (e) { /* 同上 */ }
}

// -------------------- 算法 JS 版（与 server/indicators.py 逐点等价） --------------------
// 约定：全部返回「与 K 线等长的数组」，前置不足为 null（**不用 0 填充**）。

/** K 线数组 -> OHLCV 数组（缺值为 NaN，末段由 _dpNull 统一转 null） */
function _dpOhlcv(kls) {
  var n = kls ? kls.length : 0;
  var o = new Array(n), h = new Array(n), l = new Array(n), c = new Array(n), v = new Array(n);
  for (var i = 0; i < n; i++) {
    var k = kls[i] || {};
    o[i] = Number(k.o); h[i] = Number(k.h); l[i] = Number(k.l); c[i] = Number(k.c);
    v[i] = Number(k.v == null ? 0 : k.v);
  }
  return { o: o, h: h, l: l, c: c, v: v, n: n };
}

/** 数值 → null（NaN/inf 一律 null，禁止 0 兜底） */
function _dpNull(x) {
  return (typeof x === 'number' && isFinite(x)) ? x : null;
}

/** 简单移动平均（前置 period-1 为 null；窗口内任一缺数 -> null，与后端一致） */
function _dpSma(arr, period) {
  var n = arr.length, out = new Array(n);
  if (period <= 0) { for (var q = 0; q < n; q++) out[q] = null; return out; }
  for (var i = 0; i < n; i++) {
    if (i < period - 1) { out[i] = null; continue; }
    var sum = 0, bad = false;
    for (var j = i - period + 1; j <= i; j++) {
      var x = arr[j];
      if (x == null || !isFinite(x)) { bad = true; break; }
      sum += x;
    }
    out[i] = bad ? null : (sum / period);
  }
  return out;
}

/** 指数移动平均（首值为种子，alpha = 2/(period+1)） */
function _dpEma(arr, period) {
  var n = arr.length, out = new Array(n);
  if (!n || period <= 0) return out;
  var alpha = 2 / (period + 1), prev = arr[0];
  out[0] = prev;
  for (var i = 1; i < n; i++) { prev = alpha * arr[i] + (1 - alpha) * prev; out[i] = prev; }
  return out;
}

/** Wilder 平滑（首窗口均值为种子，其后 prev += (v-prev)/period） */
function _dpWilder(arr, period, start) {
  var n = arr.length, out = new Array(n);
  for (var i = 0; i < n; i++) out[i] = null;
  start = start || 0;
  if (period <= 0 || n - start < period) return out;
  var seedAt = -1;
  for (var s = start; s <= n - period; s++) {
    var ok = true;
    for (var q = 0; q < period; q++) {
      var sv = arr[s + q];
      if (sv == null || !isFinite(sv)) { ok = false; break; }   // isFinite(null) === true，须显式判空
    }
    if (ok) { seedAt = s; break; }
  }
  if (seedAt < 0) return out;
  var sum = 0;
  for (var k = 0; k < period; k++) sum += arr[seedAt + k];
  var prev = sum / period;
  out[seedAt + period - 1] = prev;
  for (var j = seedAt + period; j < n; j++) {
    var val = arr[j];
    if (val == null || !isFinite(val)) { out[j] = prev; continue; }
    prev = prev + (val - prev) / period;
    out[j] = prev;
  }
  return out;
}

/** 窗口极值/均值：type = 'max' | 'min' | 'sum' */
function _dpRoll(arr, period, type) {
  var n = arr.length, out = new Array(n);
  for (var i = 0; i < n; i++) {
    if (i < period - 1) { out[i] = null; continue; }
    var acc = type === 'max' ? -Infinity : (type === 'min' ? Infinity : 0), bad = false;
    for (var j = i - period + 1; j <= i; j++) {
      var x = arr[j];
      // 注意：isFinite(null) === true（null 会被当 0），必须显式判 null
      if (x == null || !isFinite(x)) { bad = true; break; }
      if (type === 'max') { if (x > acc) acc = x; }
      else if (type === 'min') { if (x < acc) acc = x; }
      else acc += x;
    }
    out[i] = bad ? null : acc;
  }
  return out;
}

/** 真实波幅（首根 = high-low，与后端一致） */
function _dpTr(h, l, c) {
  var n = c.length, tr = new Array(n);
  if (!n) return tr;
  tr[0] = h[0] - l[0];
  for (var i = 1; i < n; i++) {
    tr[i] = Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1]));
  }
  return tr;
}

/** 逐元素安全除法（分母 0/非有限 -> null） */
function _dpDiv(num, den, scale) {
  var out = new Array(num.length);
  for (var i = 0; i < num.length; i++) {
    var a = num[i], b = den[i];
    out[i] = (a != null && b != null && isFinite(a) && isFinite(b) && Math.abs(b) > 1e-12) ? a / b * (scale || 1) : null;
  }
  return out;
}

/** 方向运动 +DM/-DM（第 0 根为 0） */
function _dpDm(h, l) {
  var n = h.length, plus = new Array(n), minus = new Array(n);
  for (var i = 0; i < n; i++) { plus[i] = 0; minus[i] = 0; }
  for (var j = 1; j < n; j++) {
    var up = h[j] - h[j - 1], down = l[j - 1] - l[j];
    plus[j] = (up > down && up > 0) ? up : 0;
    minus[j] = (down > up && down > 0) ? down : 0;
  }
  return { plus: plus, minus: minus };
}

/** DMI 内核 -> {plusDi, minusDi, dx, first} */
function _dpDmi(h, l, c, period) {
  var tr = _dpTr(h, l, c), dm = _dpDm(h, l);
  var atrS = _dpWilder(tr, period, 0);
  var pS = _dpWilder(dm.plus, period, 0);
  var nS = _dpWilder(dm.minus, period, 0);
  var pdi = _dpDiv(pS, atrS, 100), ndi = _dpDiv(nS, atrS, 100);
  var diff = new Array(pdi.length), sum = new Array(pdi.length);
  for (var i = 0; i < pdi.length; i++) {
    if (pdi[i] == null || ndi[i] == null) { diff[i] = null; sum[i] = null; }
    else { diff[i] = Math.abs(pdi[i] - ndi[i]); sum[i] = pdi[i] + ndi[i]; }
  }
  return { plusDi: pdi, minusDi: ndi, dx: _dpDiv(diff, sum, 100), first: period - 1 };
}

/** 交易日键（用于 VWAP 日内重置；取日期前 8 位） */
function _dpSessionKeys(kls) {
  if (!kls || !kls.length) return null;
  var keys = [];
  for (var i = 0; i < kls.length; i++) {
    var k = kls[i] || {};
    var raw = k.d || k.t || k.date || k.time;
    if (raw == null) return null;
    var s = String(raw).trim();
    if (!s) return null;
    var digits = s.replace(/[^0-9]/g, '');
    keys.push(digits.length >= 8 ? digits.substring(0, 8) : s.substring(0, 10));
  }
  return keys;
}

/** 指标计算总入口：返回 {pane: [values]}（与后端 panes 同名同序） */
function _dpCompute(name, kls, params) {
  var n0 = _dpNormalizeName(name);
  var meta = _dpMeta(n0) || {};
  var p = params || _dpParams(n0);
  var d = _dpOplcvSafe(kls);
  var h = d.h, l = d.l, c = d.c, v = d.v, n = d.n;
  var out = {};
  var empty = function () {
    var o = {};
    (meta.panes || []).forEach(function (x) { o[x] = []; });
    return o;
  };
  if (!n) return empty();

  if (n0 === 'atr') {
    out.atr = _dpWilder(_dpTr(h, l, c), p.period, 0).map(_dpNull);
    return out;
  }
  if (n0 === 'supertrend') {
    var trS = _dpTr(h, l, c), atrS = _dpWilder(trS, p.period, 0);
    var hl2 = new Array(n), lower = new Array(n), upper = new Array(n), dir = new Array(n);
    var line = new Array(n);
    for (var i = 0; i < n; i++) { hl2[i] = (h[i] + l[i]) / 2; line[i] = null; dir[i] = null; }
    var first = p.period - 1;
    for (var i2 = first; i2 < n; i2++) {
      if (atrS[i2] == null) continue;
      var bl = hl2[i2] - p.mult * atrS[i2], bu = hl2[i2] + p.mult * atrS[i2];
      if (i2 === first) { lower[i2] = bl; upper[i2] = bu; dir[i2] = 1; line[i2] = bl; continue; }
      lower[i2] = (lower[i2 - 1] != null && c[i2 - 1] > lower[i2 - 1]) ? Math.max(bl, lower[i2 - 1]) : bl;
      upper[i2] = (upper[i2 - 1] != null && c[i2 - 1] < upper[i2 - 1]) ? Math.min(bu, upper[i2 - 1]) : bu;
      if (upper[i2 - 1] != null && c[i2] > upper[i2 - 1]) dir[i2] = 1;
      else if (lower[i2 - 1] != null && c[i2] < lower[i2 - 1]) dir[i2] = -1;
      else dir[i2] = dir[i2 - 1];
      line[i2] = dir[i2] > 0 ? lower[i2] : upper[i2];
    }
    out.st_line = line.map(_dpNull);
    out.st_dir = dir.map(function (x) { return x == null ? null : (x > 0 ? 1 : -1); });
    return out;
  }
  if (n0 === 'ichimoku') {
    var mid = function (per) {
      var hi = _dpRoll(h, per, 'max'), lo = _dpRoll(l, per, 'min'), m = new Array(n);
      for (var i = 0; i < n; i++) m[i] = (hi[i] != null && lo[i] != null) ? (hi[i] + lo[i]) / 2 : null;
      return m;
    };
    var tenkan = mid(p.tenkan), kijun = mid(p.kijun), spanB = mid(p.senkou_b);
    var spanA = new Array(n);
    for (var i3 = 0; i3 < n; i3++) spanA[i3] = (tenkan[i3] != null && kijun[i3] != null) ? (tenkan[i3] + kijun[i3]) / 2 : null;
    var sa = new Array(n), sb = new Array(n), ch = new Array(n);
    for (var i4 = 0; i4 < n; i4++) {
      sa[i4] = (i4 >= p.shift) ? spanA[i4 - p.shift] : null;
      sb[i4] = (i4 >= p.shift) ? spanB[i4 - p.shift] : null;
      ch[i4] = (i4 + p.shift < n) ? c[i4 + p.shift] : null;
    }
    out.tenkan = tenkan.map(_dpNull); out.kijun = kijun.map(_dpNull);
    out.senkou_a = sa.map(_dpNull); out.senkou_b = sb.map(_dpNull); out.chikou = ch.map(_dpNull);
    return out;
  }
  if (n0 === 'donchian') {
    var up = _dpRoll(h, p.period, 'max'), lo2 = _dpRoll(l, p.period, 'min'), mid2 = new Array(n);
    for (var i5 = 0; i5 < n; i5++) mid2[i5] = (up[i5] != null && lo2[i5] != null) ? (up[i5] + lo2[i5]) / 2 : null;
    out.upper = up.map(_dpNull); out.middle = mid2.map(_dpNull); out.lower = lo2.map(_dpNull);
    return out;
  }
  if (n0 === 'keltner') {
    var midK = _dpEma(c, p.period), atrK = _dpWilder(_dpTr(h, l, c), p.atr_period, 0);
    var upK = new Array(n), loK = new Array(n);
    for (var i6 = 0; i6 < n; i6++) {
      upK[i6] = (midK[i6] != null && atrK[i6] != null) ? midK[i6] + p.mult * atrK[i6] : null;
      loK[i6] = (midK[i6] != null && atrK[i6] != null) ? midK[i6] - p.mult * atrK[i6] : null;
    }
    out.upper = upK.map(_dpNull); out.middle = midK.map(_dpNull); out.lower = loK.map(_dpNull);
    return out;
  }
  if (n0 === 'vwap') {
    var keys = _dpSessionKeys(kls);
    var sessions = null;
    if (keys) { sessions = {}; keys.forEach(function (k2) { sessions[k2] = 1; }); }
    var resetDaily = !!keys && Object.keys(sessions).length < n;
    var cumPv = 0, cumV = 0, prevKey = null, res = new Array(n);
    for (var i7 = 0; i7 < n; i7++) {
      var tp = (h[i7] + l[i7] + c[i7]) / 3, vol = v[i7];
      if (!isFinite(tp) || !isFinite(vol) || vol < 0) { res[i7] = null; continue; }
      var key = keys ? keys[i7] : null;
      if (resetDaily && prevKey !== null && key !== prevKey) { cumPv = 0; cumV = 0; }
      prevKey = key;
      cumPv += tp * vol; cumV += vol;
      res[i7] = cumV > 1e-12 ? cumPv / cumV : null;
    }
    out.vwap = res.map(_dpNull);
    return out;
  }
  if (n0 === 'adx') {
    var dmr = _dpDmi(h, l, c, p.period);
    out.adx = _dpWilder(dmr.dx, p.period, dmr.first).map(_dpNull);
    out.plus_di = dmr.plusDi.map(_dpNull);
    out.minus_di = dmr.minusDi.map(_dpNull);
    return out;
  }
  if (n0 === 'aroon') {
    var au = new Array(n), ad = new Array(n);
    for (var i8 = 0; i8 < n; i8++) {
      au[i8] = null; ad[i8] = null;
      if (i8 < p.period) continue;
      var hiIdx = i8 - p.period, loIdx = i8 - p.period;
      for (var j2 = i8 - p.period; j2 <= i8; j2++) {
        if (h[j2] > h[hiIdx]) hiIdx = j2;
        if (l[j2] < l[loIdx]) loIdx = j2;
      }
      au[i8] = 100 * (p.period - (i8 - hiIdx)) / p.period;
      ad[i8] = 100 * (p.period - (i8 - loIdx)) / p.period;
    }
    out.aroon_up = au.map(_dpNull); out.aroon_down = ad.map(_dpNull);
    out.aroon_osc = au.map(function (x, i9) { return (x == null || ad[i9] == null) ? null : x - ad[i9]; });
    return out;
  }
  if (n0 === 'obv') {
    var obv = new Array(n);
    if (n) obv[0] = 0;                    // 定义即「从 0 起累计」，非占位
    for (var i10 = 1; i10 < n; i10++) {
      var vol2 = isFinite(v[i10]) ? v[i10] : 0;
      if (c[i10] > c[i10 - 1]) obv[i10] = obv[i10 - 1] + vol2;
      else if (c[i10] < c[i10 - 1]) obv[i10] = obv[i10 - 1] - vol2;
      else obv[i10] = obv[i10 - 1];
    }
    out.obv = obv.map(_dpNull);
    return out;
  }
  if (n0 === 'mfi') {
    var tpArr = new Array(n), mf = new Array(n), pos = new Array(n), neg = new Array(n);
    for (var i11 = 0; i11 < n; i11++) {
      tpArr[i11] = (h[i11] + l[i11] + c[i11]) / 3;
      mf[i11] = tpArr[i11] * (isFinite(v[i11]) && v[i11] > 0 ? v[i11] : 0);
      pos[i11] = null; neg[i11] = null;
      if (i11 >= 1) {
        var dl2 = tpArr[i11] - tpArr[i11 - 1];
        pos[i11] = dl2 > 0 ? mf[i11] : 0;
        neg[i11] = dl2 < 0 ? mf[i11] : 0;
      }
    }
    var posS = _dpRoll(pos, p.period, 'sum'), negS = _dpRoll(neg, p.period, 'sum');
    var mfiOut = new Array(n);
    for (var i12 = 0; i12 < n; i12++) {
      var ps = posS[i12], ns = negS[i12];
      if (ps == null || ns == null) { mfiOut[i12] = null; continue; }
      if (ns > 1e-12) mfiOut[i12] = 100 - 100 / (1 + ps / ns);
      else if (ps > 1e-12) mfiOut[i12] = 100;
      else mfiOut[i12] = null;
    }
    out.mfi = mfiOut.map(_dpNull);
    return out;
  }
  if (n0 === 'stoch') {
    var hiS = _dpRoll(h, p.k_period, 'max'), loS = _dpRoll(l, p.k_period, 'min');
    var kArr = new Array(n);
    for (var i13 = 0; i13 < n; i13++) {
      var w = (hiS[i13] != null && loS[i13] != null) ? (hiS[i13] - loS[i13]) : null;
      kArr[i13] = (w != null && Math.abs(w) > 1e-12) ? 100 * (c[i13] - loS[i13]) / w : null;
    }
    var dArr = _dpSma(kArr, p.d_period), jArr = new Array(n);
    for (var i14 = 0; i14 < n; i14++) {
      jArr[i14] = (kArr[i14] != null && dArr[i14] != null) ? 3 * kArr[i14] - 2 * dArr[i14] : null;
    }
    out.k = kArr.map(_dpNull); out.d = dArr.map(_dpNull); out.j = jArr.map(_dpNull);
    return out;
  }
  if (n0 === 'cci') {
    var tpC = new Array(n);
    for (var i15 = 0; i15 < n; i15++) tpC[i15] = (h[i15] + l[i15] + c[i15]) / 3;
    var maC = _dpSma(tpC, p.period), devC = new Array(n);
    for (var i16 = 0; i16 < n; i16++) devC[i16] = (maC[i16] != null) ? Math.abs(tpC[i16] - maC[i16]) : null;
    var mdC = _dpSma(devC, p.period), cciOut = new Array(n);
    for (var i17 = 0; i17 < n; i17++) {
      var den = (mdC[i17] != null) ? 0.015 * mdC[i17] : null;
      cciOut[i17] = (den != null && Math.abs(den) > 1e-12 && maC[i17] != null) ? (tpC[i17] - maC[i17]) / den : null;
    }
    out.cci = cciOut.map(_dpNull);
    return out;
  }
  if (n0 === 'choppiness') {
    var trC = _dpTr(h, l, c), sumC = _dpRoll(trC, p.period, 'sum');
    var hiC = _dpRoll(h, p.period, 'max'), loC = _dpRoll(l, p.period, 'min'), chopO = new Array(n);
    for (var i18 = 0; i18 < n; i18++) {
      var rng = (hiC[i18] != null && loC[i18] != null) ? hiC[i18] - loC[i18] : null;
      if (sumC[i18] == null || rng == null || Math.abs(rng) <= 1e-12 || sumC[i18] <= 0) { chopO[i18] = null; continue; }
      chopO[i18] = 100 * Math.log10(sumC[i18] / rng) / Math.log10(p.period);
    }
    out.chop = chopO.map(_dpNull);
    return out;
  }
  if (n0 === 'williams_r') {
    var hiW = _dpRoll(h, p.period, 'max'), loW = _dpRoll(l, p.period, 'min'), wrO = new Array(n);
    for (var i19 = 0; i19 < n; i19++) {
      var rngW = (hiW[i19] != null && loW[i19] != null) ? hiW[i19] - loW[i19] : null;
      wrO[i19] = (rngW != null && Math.abs(rngW) > 1e-12) ? -100 * (hiW[i19] - c[i19]) / rngW : null;
    }
    out.wr = wrO.map(_dpNull);
    return out;
  }
  if (n0 === 'di') {
    var dmr2 = _dpDmi(h, l, c, p.period);
    out.plus_di = dmr2.plusDi.map(_dpNull);
    out.minus_di = dmr2.minusDi.map(_dpNull);
    return out;
  }
  return empty();
}

/** OHLCV 提取（容错版，供 _dpCompute 用） */
function _dpOplcvSafe(kls) {
  try { return _dpOhlcv(kls); } catch (e) { return _dpOhlcv([]); }
}

/** 把 null 段保留（ECharts / klinecharts 都认 null 断点） */
function _dpArraysEqualLen(panes, n) {
  var out = {};
  Object.keys(panes || {}).forEach(function (k) {
    var arr = panes[k] || [];
    if (arr.length === n) { out[k] = arr; return; }
    var fixed = arr.slice(0, n);
    while (fixed.length < n) fixed.push(null);
    out[k] = fixed;
  });
  return out;
}

// -------------------- 主图 Y 轴：只由 K 线决定 --------------------

/**
 * 主图可见区间的 K 线高低点（**不含任何指标值**）
 * @param {Array} kls K 线
 * @param {number} startPct 可见窗口起点百分比（0-100，与 dataZoom.start 同义）
 * @param {number} endPct 可见窗口终点百分比
 */
function _dpCandleExtent(kls, startPct, endPct) {
  var n = kls ? kls.length : 0;
  if (!n) return null;
  var s = Math.max(0, Math.min(n - 1, Math.floor(n * (startPct == null ? 50 : startPct) / 100)));
  var e = Math.max(s, Math.min(n - 1, Math.ceil(n * (endPct == null ? 100 : endPct) / 100) - 1));
  var lo = Infinity, hi = -Infinity;
  for (var i = s; i <= e; i++) {
    var k = kls[i] || {};
    var lv = Number(k.l), hv = Number(k.h);
    if (isFinite(lv) && lv < lo) lo = lv;
    if (isFinite(hv) && hv > hi) hi = hv;
  }
  if (!isFinite(lo) || !isFinite(hi)) return null;
  if (hi - lo < 1e-9) { hi = lo + Math.max(Math.abs(lo) * 0.01, 0.01); }
  return { lo: lo, hi: hi };
}

/** 取当前生效的缩放窗口（ECharts 的 dataZoom 百分比） */
function _dpZoomWindow() {
  try {
    if (klineChart && !klineChart.isDisposed()) {
      var op = klineChart.getOption();
      var dz = op && op.dataZoom && op.dataZoom[0];
      if (dz && isFinite(dz.start) && isFinite(dz.end)) return { start: dz.start, end: dz.end };
    }
  } catch (e) { /* 走默认 */ }
  return { start: 50, end: 100 };
}

/** 主图 Y 轴范围（带 1% 留白，仅由 K 线决定） */
function _dpMainAxis(kls, win) {
  var w = win || _dpZoomWindow();
  var ext = _dpCandleExtent(kls, w.start, w.end);
  if (!ext) return null;
  var pad = (ext.hi - ext.lo) * 0.01;
  return { min: +(ext.lo - pad).toFixed(4), max: +(ext.hi + pad).toFixed(4) };
}

/** 记录一次主图 Y 轴取值（验收断言用：叠加前后应完全一致） */
function _dpLogAxis(tag, axis) {
  try {
    var log = window._dpInd.axisLog;
    log.push({ tag: tag, min: axis ? axis.min : null, max: axis ? axis.max : null, at: Date.now() });
    if (log.length > 20) log.shift();
  } catch (e) {}
}

/**
 * overlay 值钳制到主图 K 线区间
 * 目的：先行带/唐奇安外轨越界时**不把主图刻度拉扁**（越界段沿主图边缘走）。
 * 与后端「不造数」纪律不冲突：钳制只影响绘制像素，tooltip 仍显示原始值。
 */
function _dpClampToCandle(values, ext) {
  if (!ext || !values) return values;
  return values.map(function (v) {
    if (v == null) return null;
    return Math.min(ext.hi, Math.max(ext.lo, v));
  });
}

// -------------------- 数据获取（按需一次，切换不重复请求） --------------------

/** 缓存键：同标的 + 同周期 + 同指标 + 同参数 共享 */
function _dpCacheKey(name) {
  var code = (_dpRenderCtx && _dpRenderCtx.code) || (window.appState && window.appState.activeCode) || '';
  var period = (_dpRenderCtx && _dpRenderCtx.period) || (window.appState && window.appState.period) || 'day';
  return code + ':' + period + ':' + _dpNormalizeName(name) + ':' + _dpParamList(name).join(',');
}

/**
 * 取指标序列
 * ---------------------------------------------------------------
 * 绘制数据 = **本地算法**（与 server/indicators.py 逐点等价，已对拍 2.3e-12）。
 * 原因有二：
 *   ① 序列长度必须与「当前图上的 K 线根数」严格一致，否则副图错位；
 *      后端按自己的 limit 返回，根数可能与图上不同（首帧/切周期瞬间尤甚）。
 *   ② 盘中 tick 要在本地重算最后一根，绘制源与重算源必须是同一套代码，
 *      否则会出现「首帧用后端值、tick 后用本地值」的两套数。
 * 后端响应仍是**权威侧**：参数 schema / 标签 / min_bars / 不可用原因取自它，
 * 且首次切换一定请求一次（不重复请求），可用它对本地算法做旁证。
 * 若本地无 K 线（极端情况），才退化为后端序列。
 */
function _dpSeriesOf(name) {
  var n = _dpNormalizeName(name);
  var params = _dpParams(n);
  var kls = _dpCurrentKlines();
  if (kls.length) {
    var panes = _dpArraysEqualLen(_dpCompute(n, kls, params), kls.length);
    window._dpInd.jsComputeCount += 1;
    return { panes: panes, params: params, label: _dpLabel(n, params), source: 'js' };
  }
  var key = _dpCacheKey(n);
  var hit = window._dpInd.apiCache[key];
  if (hit && hit.panes) return { panes: hit.panes, params: hit.params, label: hit.label, source: 'api' };
  return { panes: {}, params: params, label: _dpLabel(n, params), source: 'js' };
}

/** 当前渲染的 K 线（与 /api/kline 同源，来自 appState.klineCache） */
function _dpCurrentKlines() {
  if (_dpRenderCtx && _dpRenderCtx.klines && _dpRenderCtx.klines.length) return _dpRenderCtx.klines;
  var st = window.appState || {};
  var cacheKey = st.activeCode ? (st.activeCode + ':' + (st.period || 'day')) : null;
  var data = cacheKey && st.klineCache ? st.klineCache[cacheKey] : null;
  return (data && data.klines) ? data.klines : [];
}

/**
 * 按需请求后端指标（每个「标的+周期+指标+参数」只请求一次）
 * 切回已看过的指标时命中 apiCache，**不产生新请求**（验收断言 2）。
 */
function _dpFetchIndicator(name, cb) {
  var n = _dpNormalizeName(name);
  var key = _dpCacheKey(n);
  if (window._dpInd.apiCache[key]) { if (cb) cb(window._dpInd.apiCache[key]); return; }
  var st = window.appState || {};
  var code = (_dpRenderCtx && _dpRenderCtx.code) || st.activeCode;
  if (!code) return;
  var period = (_dpRenderCtx && _dpRenderCtx.period) || st.period || 'day';
  var kls = _dpCurrentKlines();
  var limit = Math.max(30, kls.length || 250);
  var params = _dpParamList(n).join(',');
  var url = '/api/kline/indicators?code=' + encodeURIComponent(code)
    + '&period=' + encodeURIComponent(period)
    + '&limit=' + limit
    + '&names=' + encodeURIComponent(n)
    + (params ? ('&params=' + encodeURIComponent(params)) : '');
  window._dpInd.reqCount += 1;
  fetch(url).then(function (r) { return r.json(); }).then(function (j) {
    if (!j || !j.data || !j.data.series) return;
    var item = j.data.series[n];
    if (!item) return;
    if (item.params_schema) {
      var meta = _dpMeta(n) || {};
      meta.params = item.params_schema;
      meta.label = String(item.label || meta.label || n).split('(')[0];
      meta.overlay = !!item.overlay;
      meta.value_range = item.value_range || meta.value_range;
      meta.min_bars = item.min_bars;
      window._dpInd.meta[n] = meta;
    }
    var panes = item.panes && Object.keys(item.panes).length ? item.panes : (function () {
      var o = {};
      (item.values && item.values.length) ? (o[(meta || {}).primary || n] = item.values) : null;
      return o;
    })();
    window._dpInd.apiCache[key] = {
      panes: _dpArraysEqualLen(panes, (panes && panes[Object.keys(panes)[0]]) ? panes[Object.keys(panes)[0]].length : 0),
      params: item.params || _dpParams(n),
      label: item.label || _dpLabel(n),
      reason: item.reason || null,
      fetchedAt: Date.now()
    };
    if (cb) cb(window._dpInd.apiCache[key]);
  }).catch(function () { /* 失败时保留本地计算结果，不阻塞渲染 */ });
}

// -------------------- 渲染：ECharts（兼容引擎） --------------------

/**
 * 构建扩展指标的 ECharts series
 * @param {string} name 指标名
 * @param {Object} ctx {kls, overlay, ext, xIdx, yIdx}
 * @returns {Array} series 数组
 */
function _dpBuildEchartsSeries(name, ctx) {
  var n = _dpNormalizeName(name);
  var meta = _dpMeta(n) || {};
  var got = _dpSeriesOf(n);
  var panes = got.panes || {};
  var kls = ctx.kls || [];
  var nBars = kls.length;
  var out = [];
  var xIdx = ctx.xIdx, yIdx = ctx.yIdx;
  var overlay = !!ctx.overlay;

  var mk = function (pane, color, width, dash, title) {
    var data = panes[pane] || [];
    if (overlay) data = _dpClampToCandle(data, ctx.ext);
    return {
      name: title || (meta.label || n.toUpperCase()) + '·' + pane,
      type: 'line', xAxisIndex: xIdx, yAxisIndex: yIdx, data: data,
      symbol: 'none', smooth: 0.2, connectNulls: false,
      lineStyle: { width: width || 1.2, color: color, type: dash || 'solid' },
      itemStyle: { color: color },
      z: overlay ? 6 : 2,          // overlay 在蜡烛之上，避免被 K 线盖住
      _dpIndicator: n,
      _dpPane: pane
    };
  };

  if (n === 'supertrend') {
    // 按方向着色：多头段读 _themeUp，空头段读 _themeDown（涨红跌绿，不写死色值）
    var line = panes.st_line || [], dir = panes.st_dir || [];
    var upSeg = line.map(function (v, i) { return (dir[i] === 1) ? v : null; });
    var dnSeg = line.map(function (v, i) { return (dir[i] === -1) ? v : null; });
    if (overlay) { upSeg = _dpClampToCandle(upSeg, ctx.ext); dnSeg = _dpClampToCandle(dnSeg, ctx.ext); }
    out.push({
      name: 'SuperTrend(涨)', type: 'line', xAxisIndex: xIdx, yAxisIndex: yIdx, data: upSeg,
      symbol: 'none', connectNulls: false, z: 6, _dpIndicator: n, _dpPane: 'st_line',
      lineStyle: { width: 1.6, color: _themeUp }, itemStyle: { color: _themeUp }
    });
    out.push({
      name: 'SuperTrend(跌)', type: 'line', xAxisIndex: xIdx, yAxisIndex: yIdx, data: dnSeg,
      symbol: 'none', connectNulls: false, z: 6, _dpIndicator: n, _dpPane: 'st_line',
      lineStyle: { width: 1.6, color: _themeDown }, itemStyle: { color: _themeDown }
    });
    return out;
  }

  if (n === 'obv') {
    var obvVals = panes.obv || [];
    out.push({
      name: 'OBV', type: 'line', xAxisIndex: xIdx, yAxisIndex: yIdx, data: obvVals,
      symbol: 'none', z: 2, _dpIndicator: n, _dpPane: 'obv',
      lineStyle: { width: 1.3, color: _DP_LINE_COLORS[3] }, itemStyle: { color: _DP_LINE_COLORS[3] },
      areaStyle: { color: _hexToRgba(_DP_LINE_COLORS[3], 0.12) }
    });
    return out;
  }

  var paneKeys = meta.panes || Object.keys(panes);
  for (var i = 0; i < paneKeys.length; i++) {
    var pane = paneKeys[i];
    if (n === 'ichimoku' && pane === 'chikou') {
      out.push(mk(pane, _DP_LINE_COLORS[4], 1, 'dotted', '迟行线'));
      continue;
    }
    if (n === 'adx' && pane === 'adx') { out.push(mk(pane, _DP_LINE_COLORS[1], 1.5, 'solid', 'ADX')); continue; }
    if (n === 'stoch') {
      var colors = { k: _DP_LINE_COLORS[1], d: _DP_LINE_COLORS[0], j: _DP_LINE_COLORS[2] };
      out.push(mk(pane, colors[pane] || _DP_LINE_COLORS[0], 1.2, pane === 'j' ? 'dashed' : 'solid', 'K/D/J'.split('/')[i] || pane));
      continue;
    }
    out.push(mk(pane, _DP_LINE_COLORS[i % _DP_LINE_COLORS.length], 1.2));
  }
  return out;
}

/** 取该指标的「不可用原因」（后端 ok:false 时的 reason；无则 null） */
function _dpReasonOf(name) {
  var key = _dpCacheKey(name);
  var hit = window._dpInd.apiCache[key];
  if (hit && hit.reason) return hit.reason;
  var meta = _dpMeta(name) || {};
  var kls = _dpCurrentKlines();
  if (kls.length && meta.min_bars && kls.length < meta.min_bars) {
    return (meta.label || name) + ' 需要至少 ' + meta.min_bars + ' 根 K 线（当前 ' + kls.length + ' 根），前置样本不足，返回空序列而非 0 占位';
  }
  return null;
}

/**
 * 扩展指标 tooltip 行（口径按后端纪要素描，前端不自行解释）
 * - VWAP：典型价加权（腾讯日线无成交额），盘中按自然日重置
 * - OBV：首根 0 是定义值
 */
function _dpTooltipHtml(name, idx) {
  var got = _dpSeriesOf(name);
  var meta = _dpMeta(name) || {};
  var panes = got.panes || {};
  var keys = meta.panes || Object.keys(panes);
  var out = '';
  var reason = _dpReasonOf(name);
  if (reason) {
    return '<div style="font-size:10px;margin-top:3px;color:#f59e0b;max-width:260px">' + reason + '</div>';
  }
  var rows = [];
  keys.forEach(function (p) {
    var v = (panes[p] || [])[idx];
    if (v == null) return;
    rows.push('<span style="color:' + _DP_LINE_COLORS[rows.length % _DP_LINE_COLORS.length] + '">' + p + ' ' + Number(v).toFixed(2) + '</span>');
  });
  if (!rows.length) return '';
  out += '<div style="display:flex;gap:10px;font-size:10px;margin-top:2px;flex-wrap:wrap"><span style="color:#8b9ec2">' +
    _dpLabel(name) + '</span>' + rows.join('') + '</div>';
  if (name === 'vwap') out += '<div style="font-size:9px;color:#8b9ec2;margin-top:1px">典型价加权 (H+L+C)/3 × 量；盘中按自然日重置，日/周/月线为整段锚定累计</div>';
  if (name === 'obv') out += '<div style="font-size:9px;color:#8b9ec2;margin-top:1px">首根 0 为定义值（从 0 起累计），非缺数</div>';
  if (name === 'ichimoku') out += '<div style="font-size:9px;color:#8b9ec2;margin-top:1px">先行带 A/B 前移 26 根、迟行线后移 26 根</div>';
  return out;
}

/** 副图 Y 轴配置（值域来自后端 value_range：0-100 / -100-0 / 自适应） */
function _dpSubYAxis(name, gridIndex) {
  var meta = _dpMeta(name) || {};
  var vr = meta.value_range || [null, null];
  var axis = { gridIndex: gridIndex, axisLabel: { color: '#3d5278', fontSize: 8 }, splitLine: { show: false }, splitNumber: 3 };
  if (vr[0] != null && vr[1] != null) { axis.min = vr[0]; axis.max = vr[1]; }
  else if (vr[0] != null) { axis.min = vr[0]; axis.scale = true; }
  else { axis.scale = true; }
  return axis;
}

/** 副图参考线（超买/超卖等），颜色读主题变量 */
function _dpPaneMarkLines(name) {
  if (name === 'mfi' || name === 'stoch' || name === 'adx' || name === 'aroon') {
    return { silent: true, symbol: 'none', lineStyle: { color: 'rgba(255,255,255,0.10)', type: 'dashed' },
      data: [{ yAxis: 80, label: { formatter: '80', color: _themeUp, fontSize: 8 } },
             { yAxis: 20, label: { formatter: '20', color: _themeDown, fontSize: 8 } }] };
  }
  if (name === 'cci') {
    return { silent: true, symbol: 'none', lineStyle: { color: 'rgba(255,255,255,0.10)', type: 'dashed' },
      data: [{ yAxis: 100, label: { formatter: '+100', color: _themeUp, fontSize: 8 } },
             { yAxis: -100, label: { formatter: '-100', color: _themeDown, fontSize: 8 } }] };
  }
  return null;
}

// -------------------- 渲染：klinecharts（Pro 主引擎） --------------------

/** v35 已注册的 Pro 指标名（幂等注册） */
var _dpProRegistered = {};

/** 注册扩展指标到 klinecharts（按需，幂等） */
function _dpProRegister(name) {
  var n = _dpNormalizeName(name);
  if (_dpProRegistered[n]) return;
  if (typeof klinecharts === 'undefined' || !klinecharts.registerIndicator) return;
  var meta = _dpMeta(n) || {};
  var panes = meta.panes || [];
  var colorAt = function (i) { return _DP_LINE_COLORS[i % _DP_LINE_COLORS.length]; };

  // figures：声明参与绘制的线；overlay 指标在此声明，刻度影响由 calc 钳制消除
  var figures = panes.map(function (p, i) {
    return { key: p, title: p + ': ', type: 'line', styles: function () { return { color: colorAt(i) }; } };
  });
  if (n === 'supertrend') {
    figures = [
      { key: 'st_up', title: '涨: ', type: 'line', styles: function () { return { color: _themeUp }; } },
      { key: 'st_dn', title: '跌: ', type: 'line', styles: function () { return { color: _themeDown }; } }
    ];
  }

  klinecharts.registerIndicator({
    name: 'DP_' + n.toUpperCase(),
    shortName: (meta.label || n).substring(0, 8),
    series: meta.overlay ? 'price' : 'normal',
    precision: meta.overlay ? 2 : 2,
    shouldOhlc: false,
    figures: figures,
    calcParams: (function () { var p = _dpParams(n); return Object.keys(p).map(function (k) { return p[k]; }); })(),
    calc: function (dataList) {
      // klinecharts 的 dataList 是其内部 kline 结构（open/high/low/close/volume）
      var kls = dataList.map(function (d) { return { o: d.open, h: d.high, l: d.low, c: d.close, v: d.volume, d: d.timestamp }; });
      var res = _dpCompute(n, kls, _dpParams(n));
      window._dpInd.jsComputeCount += 1;
      var ext = null;
      if (meta.overlay) {
        // 主图 Y 轴纪律：把值钳制到可见 K 线区间，避免把蜡烛压扁
        try {
          var vr = (proChart && proChart.getVisibleRange) ? proChart.getVisibleRange() : null;
          var from = (vr && isFinite(vr.realFrom)) ? Math.max(0, Math.floor(vr.realFrom)) : 0;
          var to = (vr && isFinite(vr.realTo)) ? Math.min(dataList.length - 1, Math.ceil(vr.realTo)) : dataList.length - 1;
          var lo = Infinity, hi = -Infinity;
          for (var i = from; i <= to; i++) {
            var dd = dataList[i]; if (!dd) continue;
            if (dd.low < lo) lo = dd.low;
            if (dd.high > hi) hi = dd.high;
          }
          if (isFinite(lo) && isFinite(hi)) ext = { lo: lo, hi: hi };
        } catch (e) { ext = null; }
      }
      var out = new Array(dataList.length);
      for (var i2 = 0; i2 < dataList.length; i2++) {
        var row = {};
        if (n === 'supertrend') {
          var line = (res.st_line || [])[i2], dir = (res.st_dir || [])[i2];
          var lv = line;
          if (ext && lv != null) lv = Math.min(ext.hi, Math.max(ext.lo, lv));
          if (lv != null && dir === 1) row.st_up = lv;
          if (lv != null && dir === -1) row.st_dn = lv;
        } else {
          panes.forEach(function (p) {
            var v = (res[p] || [])[i2];
            if (v == null) return;
            row[p] = (ext && meta.overlay) ? Math.min(ext.hi, Math.max(ext.lo, v)) : v;
          });
        }
        out[i2] = row;
      }
      return out;
    }
  });
  _dpProRegistered[n] = true;
}

/** Pro 引擎：同步扩展指标（当前副图 = 扩展指标时创建，其余情况移除） */
function _dpSyncProIndicator() {
  if (!proChart) return;
  var cfg = window._indicatorConfig || {};
  var active = _dpNormalizeName(cfg.subIndicator);
  var isExtra = _DP_EXTRA_NAMES.indexOf(active) >= 0;
  // 先清掉其它扩展指标
  _DP_EXTRA_NAMES.forEach(function (n) {
    if (n === active && isExtra) return;
    var pname = 'DP_' + n.toUpperCase();
    try { if (proHasIndicator(pname)) proChart.removeIndicator({ name: pname }); } catch (e) {}
  });
  if (!isExtra) return;
  _dpProRegister(active);
  var pname = 'DP_' + active.toUpperCase();
  var meta = _dpMeta(active) || {};
  // 扩展指标与内置 MACD/KDJ/RSI 互斥（副图只有一块）
  ['MACD', 'KDJ', 'RSI'].forEach(function (nm) {
    try { if (proHasIndicator(nm)) proChart.removeIndicator({ name: nm }); } catch (e) {}
  });
  try {
    var isOverlay = !!meta.overlay;
    var paneId = isOverlay ? 'candle_pane' : undefined;
    if (proHasIndicator(pname)) {
      proChart.overrideIndicator(isOverlay ? { name: pname, paneId: paneId } : { name: pname });
    } else if (isOverlay) {
      // overlay：叠在主图（candle_pane），isStack=true 表示与既有指标共用该 pane
      proChart.createIndicator({ name: pname, paneId: paneId }, true);
    } else {
      proChart.createIndicator({ name: pname }, false);
    }
    // 主图刻度纪律：overlay 只做像素渲染，不参与主图刻度 → 值已在 calc 内钳制到可见 K 线区间
    if (isOverlay) { try { applyProPaneHeights(); } catch (e2) {} }
  } catch (e) {
    console.error('[DAPENG] 扩展指标创建失败:', active, e);
  }
  if (typeof applyProPaneHeights === 'function') applyProPaneHeights();
}

// -------------------- 盘中 tick：最后一根本地重算（不请求后端） --------------------

/**
 * tick 后本地重算（ECharts 兼容路径；Pro 路径由 klinecharts 自行重算 calc）
 * 只在「当前副图/主图是扩展指标」时生效，且**不发任何网络请求**。
 */
function _dpOnLiveTick(price, high, low, volume) {
  var cfg = window._indicatorConfig || {};
  var active = _dpNormalizeName(cfg.subIndicator);
  if (_DP_EXTRA_NAMES.indexOf(active) < 0) return;
  if (!klineChart || klineChart.isDisposed()) return;
  var kls = _dpCurrentKlines();
  if (!kls.length) return;
  // 用当前 K 线（updateLastCandle 已改过最后一根）本地重算，取最后一点
  var panes = _dpCompute(active, kls, _dpParams(active));
  window._dpInd.jsComputeCount += 1;
  var meta = _dpMeta(active) || {};
  var overlay = !!meta.overlay;
  var ext = overlay ? _dpCandleExtent(kls, _dpZoomWindow().start, _dpZoomWindow().end) : null;
  var lastIdx = kls.length - 1;
  var option = klineChart.getOption();
  if (!option || !option.series) return;
  var changed = [];
  for (var i = 0; i < option.series.length; i++) {
    var s = option.series[i];
    if (!s || s._dpIndicator !== active) continue;
    var pane = s._dpPane;
    var v = (active === 'supertrend') ? (function () {
      var line = (panes.st_line || [])[lastIdx], dir = (panes.st_dir || [])[lastIdx];
      if (line == null) return null;
      return ext ? Math.min(ext.hi, Math.max(ext.lo, line)) : line;
    })() : (function () {
      var val = (panes[pane] || [])[lastIdx];
      if (val == null) return null;
      return ext ? Math.min(ext.hi, Math.max(ext.lo, val)) : val;
    })();
    if (s.data && s.data.length) s.data[lastIdx] = v; else continue;
    changed.push({ pane: pane, value: v });
  }
  if (changed.length) {
    window._dpInd.lastTickLocal = { name: active, index: lastIdx, value: changed[0].value, at: Date.now(), panes: changed };
    klineChart.setOption({ series: option.series }, { notMerge: false, lazyUpdate: true, silent: true });
  }
}

// v35: 包装 updateLastCandle —— 原函数（实时模块）字节不变，仅在其后追加本地重算钩子
(function () {
  var _orig = window.updateLastCandle;
  if (typeof _orig === 'function' && !_orig.__dpWrapped) {
    var wrapped = function (price, high, low, volume) {
      var r = _orig.apply(this, arguments);
      try { _dpOnLiveTick(price, high, low, volume); } catch (e) { /* 不影响实时主链 */ }
      return r;
    };
    wrapped.__dpWrapped = true;
    window.updateLastCandle = wrapped;
    try { updateLastCandle = wrapped; } catch (e) {}
  }
})();

function initChart() {
  const el = document.getElementById('kline-chart');
  if (!el) return;

  // 确保容器有高度（flex布局有时初始高度为0）
  if (el.clientHeight === 0) {
    el.style.height = '380px';
  }

  if (typeof echarts === 'undefined') {
    _chartInitTries++;
    if (_chartInitTries < 20) {
      // 每300ms重试，最多6秒
      setTimeout(initChart, 300);
    } else {
      console.error('[DAPENG] ECharts 加载超时，K线图不可用');
    }
    return;
  }

  // 已经初始化过
  if (klineChart && !klineChart.isDisposed()) return;

  try {
    klineChart = echarts.init(el, null, { renderer: 'canvas' });
    _chartInitTries = 0;
    window.addEventListener('resize', () => {
      if (klineChart && !klineChart.isDisposed()) klineChart.resize();
    });
    // 通知 app.js 图表已就绪
    if (typeof window._onChartReady === 'function') {
      window._onChartReady();
    }
  } catch (e) {
    console.error('[DAPENG] ECharts 初始化失败:', e);
  }
}

function calcEMA(data, period) {
  if (!data || data.length < period) return [];
  const k = 2 / (period + 1), res = [];
  let ema = data[0]; res.push(ema);
  for (let i = 1; i < data.length; i++) { ema = data[i] * k + ema * (1 - k); res.push(ema); }
  return res;
}

function calcMA(data, period) {
  if (!data || data.length < period) return [];
  const res = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) { res.push(null); continue; }
    let s = 0; for (let j = i - period + 1; j <= i; j++) s += data[j];
    res.push(s / period);
  }
  return res;
}

function calcBOLL(closes, period = 20, mult = 2) {
  if (!closes || closes.length < period) return { upper: [], mid: [], lower: [], width: [] };
  const up = [], mid = [], lo = [], w = [];
  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) { up.push(null); mid.push(null); lo.push(null); w.push(null); continue; }
    const slice = closes.slice(i - period + 1, i + 1);
    const sma = slice.reduce((a, b) => a + b, 0) / period;
    const std = Math.sqrt(slice.reduce((s, v) => s + (v - sma) ** 2, 0) / period);
    mid.push(sma); up.push(sma + mult * std); lo.push(sma - mult * std);
    w.push((2 * mult * std / sma * 100));
  }
  return { upper: up, mid, lower: lo, width: w };
}

function calcRSI(closes, period = 14) {
  if (!closes || closes.length < period + 1) return [];
  const rsi = [];
  for (let i = 0; i < closes.length; i++) {
    if (i < period) { rsi.push(null); continue; }
    let g = 0, l = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const d = closes[j] - closes[j - 1];
      d > 0 ? (g += d) : (l += Math.abs(d));
    }
    g /= period; l /= period;
    rsi.push(l === 0 ? 100 : 100 - 100 / (1 + g / l));
  }
  return rsi;
}

function calcBBI(closes) {
  if (!closes || closes.length < 24) return [];
  const ma3 = calcMA(closes, 3), ma6 = calcMA(closes, 6), ma12 = calcMA(closes, 12), ma24 = calcMA(closes, 24);
  const bbi = [];
  for (let i = 0; i < closes.length; i++) {
    if (!ma3[i] || !ma6[i] || !ma12[i] || !ma24[i]) { bbi.push(null); continue; }
    bbi.push((ma3[i] + ma6[i] + ma12[i] + ma24[i]) / 4);
  }
  return bbi;
}

/**
 * KDJ 指标计算 — v6.0
 * @param {number[]} highs - 最高价数组
 * @param {number[]} lows - 最低价数组
 * @param {number[]} closes - 收盘价数组
 * @param {number} n - RSV 周期 (默认 9)
 * @param {number} m1 - K 平滑 (默认 3)
 * @param {number} m2 - D 平滑 (默认 3)
 * @returns {{k: number[], d: number[], j: number[]}}
 */
function calcKDJ(highs, lows, closes, n, m1, m2) {
  n = n || 9; m1 = m1 || 3; m2 = m2 || 3;
  var kArr = [], dArr = [], jArr = [];
  var prevK = 50, prevD = 50;

  for (var i = 0; i < closes.length; i++) {
    if (i < n - 1) { kArr.push(null); dArr.push(null); jArr.push(null); continue; }
    // RSV
    var hMax = -Infinity, lMin = Infinity;
    for (var j = i - n + 1; j <= i; j++) {
      if (highs[j] > hMax) hMax = highs[j];
      if (lows[j] < lMin) lMin = lows[j];
    }
    var rsv = hMax === lMin ? 50 : ((closes[i] - lMin) / (hMax - lMin)) * 100;
    // K/D/J
    prevK = (rsv + (m1 - 1) * prevK) / m1;
    prevD = (prevK + (m2 - 1) * prevD) / m2;
    var jVal = 3 * prevK - 2 * prevD;
    kArr.push(+prevK.toFixed(2));
    dArr.push(+prevD.toFixed(2));
    jArr.push(+jVal.toFixed(2));
  }
  return { k: kArr, d: dArr, j: jArr };
}

/**
 * 支撑/压力位计算 — v6.0
 * 基于近期高低点 + 整数关口 + 均线共振
 * @returns {{support: number[], resistance: number[]}}
 */
function calcSupportResistance(kls) {
  if (!kls || kls.length < 20) return { support: [], resistance: [] };
  var len = kls.length;
  var lookback = Math.min(len, 60);
  var sliceStart = len - lookback;

  // 找局部极值点
  var highs = [], lows = [];
  for (var i = sliceStart + 3; i < len - 3; i++) {
    var h = kls[i].h, l = kls[i].l;
    var isPeak = true, isValley = true;
    for (var j = i - 3; j <= i + 3; j++) {
      if (j === i) continue;
      if (kls[j].h >= h) isPeak = false;
      if (kls[j].l <= l) isValley = false;
    }
    if (isPeak) highs.push({ price: h, idx: i });
    if (isValley) lows.push({ price: l, idx: i });
  }

  // 压力位：取近期高点聚类的平均值
  var resistance = [], support = [];
  var currentPrice = kls[len - 1].c;
  
  // 从高点取 2 个最近的压力（高于当前价）
  highs.sort(function(a, b) { return a.price - b.price; });
  var aboveHighs = highs.filter(function(h) { return h.price > currentPrice * 1.005; });
  if (aboveHighs.length > 0) resistance.push(+aboveHighs[aboveHighs.length - 1].price.toFixed(2));
  if (aboveHighs.length > 1) resistance.push(+aboveHighs[aboveHighs.length - 2].price.toFixed(2));

  // 支撑：取近期低点聚类的 2 个（低于当前价）
  lows.sort(function(a, b) { return b.price - a.price; });
  var belowLows = lows.filter(function(l) { return l.price < currentPrice * 0.995; });
  if (belowLows.length > 0) support.push(+belowLows[belowLows.length - 1].price.toFixed(2));
  if (belowLows.length > 1) support.push(+belowLows[belowLows.length - 2].price.toFixed(2));

  return { support: support, resistance: resistance };
}

function renderMinute(data) {
  // v31: 统一入口 —— /api/timeshare 载荷（含 points）走新版分时渲染器
  if (data && Array.isArray(data.points)) { renderTimeshare(data); return; }
  // 兼容旧载荷（klines + avg）：保留原 ECharts 渲染；v31 起不再停止实时对接
  if (typeof restoreLegacyKlineContainer === 'function') restoreLegacyKlineContainer();
  if (!klineChart || klineChart.isDisposed()) return;
  chartType = 'minute';
  if (!data || !data.klines || !data.klines.length) { klineChart.clear(); return; }
  const kls = data.klines;
  const dates = kls.map(k => k.d);
  const prices = kls.map(k => k.c);
  const vols = kls.map(k => k.v);
  const avgP = kls.map(k => k.avg || 0);
  const yc = data.yclose || prices[0];
  klineChart.setOption({
    backgroundColor: 'transparent', animation: true, animationDuration: 500,
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(6,12,28,0.96)', borderColor: 'rgba(59,130,246,0.25)', borderWidth: 1,
      padding: [10, 14], textStyle: { color: '#e2e8f0', fontSize: 11 },
      extraCssText: 'border-radius:10px;box-shadow:0 8px 32px rgba(0,0,0,0.5);backdrop-filter:blur(12px)',
    },
    grid: [{ left: 54, right: 14, top: 8, height: '55%' }, { left: 54, right: 14, top: '68%', height: '18%' }],
    xAxis: [
      { gridIndex: 0, data: dates, axisLabel: { show: false }, axisLine: { lineStyle: { color: 'rgba(59,130,246,0.06)' } } },
      { gridIndex: 1, data: dates, axisLabel: { color: '#3d5278', fontSize: 9 }, axisLine: { lineStyle: { color: 'rgba(59,130,246,0.06)' } } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, axisLabel: { color: '#3d5278', fontSize: 9, formatter: v => v.toFixed(2) }, splitLine: { lineStyle: { color: 'rgba(59,130,246,0.04)', type: 'dashed' } } },
      { scale: true, gridIndex: 1, axisLabel: { color: '#3d5278', fontSize: 8 }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], bottom: 2, height: 14, start: 0, end: 100, backgroundColor: 'rgba(6,12,28,0.5)', borderColor: 'rgba(59,130,246,0.08)', fillerColor: 'rgba(59,130,246,0.04)', handleStyle: { color: '#3b82f6' }, textStyle: { color: '#3d5278', fontSize: 8 } },
    ],
    series: [
      { name: '价格', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: prices, symbol: 'none',
        lineStyle: { width: 1.5, color: '#3b82f6' },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(59,130,246,0.25)' }, { offset: 1, color: 'rgba(59,130,246,0.02)' }]) },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: 'rgba(255,255,255,0.12)', type: 'dashed' }, data: [{ yAxis: yc, label: { formatter: '昨收 {c}', color: '#8b9ec2', fontSize: 9 } }] },
      },
      { name: '均价', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: avgP, symbol: 'none', lineStyle: { width: 1, color: '#f59e0b', type: 'dashed' } },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: vols.map(v => ({ value: v, itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(59,130,246,0.5)' }, { offset: 1, color: 'rgba(59,130,246,0.1)' }]) } })), barMaxWidth: 4 },
    ],
  }, { notMerge: true, lazyUpdate: false });
}

// ================================================================
//  分时图 timeshare（v31）
//  ---------------------------------------------------------------
//  数据链路：GET /api/timeshare?code=<code>
//    后端三级真实源（腾讯分时 → 腾讯 m1 → 新浪 1m），全失败则 points:[] + reason。
//  渲染：ECharts 双网格（价格/均价 + 成交量），X 轴固定交易分钟槽位
//        （09:30-11:30 + 13:00-15:00），Y 轴以昨收居中对称，涨=红 / 跌=绿。
//  实时：**复用 K 线实时通道**（同一条 SSE，不为分时另开连接），quotes.tick 按分钟归并：
//        同分钟 → 更新该点价格/累加量/重算均价；跨分钟 → 追加新点。
//        每 60s 用 /api/timeshare 校准一次（防累计漂移）；校准失败不影响增量继续。
//  红线：points 为空即显示「分时暂不可用：<reason>」，**不画假线、不给 0 价**。
// ================================================================
var TS_CALIBRATE_MS = 60000;   // 分时校准周期（ms）

var _tsState = {
  code: '',
  name: '',
  date: '',
  prevClose: null,
  points: [],            // [{t, price, avg_price, volume, amount}]
  cumVol: 0,             // 累计成交量（手）
  cumAmt: 0,             // 累计成交额（元）
  minuteBaseVol: 0,      // 当前分钟起点处的累计量（用于算该分钟增量）
  minuteBaseAmt: 0,
  source: '',
  reason: '',
  degraded: false,
  avgEstimated: false,
  outOfOrder: 0,         // 时间倒退/乱序被丢弃的次数（异常数据容错计数）
  applied: 0,            // 成功归并的报价次数
  rejected: [],          // 最近 5 条被拒原因（诊断用，不影响渲染）
  volMax: 0,             // 当日分钟量真实最大值（手）
  volP90: 0,             // 当日分钟量 P90（手）
  volP50: 0,             // 当日分钟量中位数（手）
  volCap: 0,             // 量轴截断上界（= P95×1.8，且 < 最大值时才生效）
  volTruncated: false,   // 量轴是否处于截断显示
  calibTimer: 0,
  renders: 0
};

/** 记录一次报价被拒（只保留最近 5 条，避免无限增长） */
function _tsReject(reason) {
  _tsState.rejected.push(reason);
  if (_tsState.rejected.length > 5) _tsState.rejected.shift();
  return false;
}

/** 交易分钟槽位：09:30-11:30（121）+ 13:00-15:00（121）= 242 */
var _tsSlots = (function () {
  var out = [];
  var i, h, m;
  for (i = 0; i <= 120; i++) {
    h = 9 + Math.floor((30 + i) / 60);
    m = (30 + i) % 60;
    out.push((h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m);
  }
  for (i = 0; i <= 120; i++) {
    h = 13 + Math.floor(i / 60);
    m = i % 60;
    out.push((h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m);
  }
  return out;
})();

var _tsSlotIndex = (function () {
  var map = {};
  for (var i = 0; i < _tsSlots.length; i++) map[_tsSlots[i]] = i;
  return map;
})();

/** 分时暂不可用：只显示文字，不画任何线 */
function _tsRenderUnavailable(reason) {
  if (!klineChart || klineChart.isDisposed()) return;
  chartType = 'timeshare';
  klineChart.clear();
  klineChart.setOption({
    backgroundColor: 'transparent',
    graphic: [{
      type: 'text', left: 'center', top: 'middle',
      style: {
        text: '分时暂不可用' + (reason ? ('：' + reason) : ''),
        fill: '#8b9ec2', fontSize: 12, fontFamily: 'inherit'
      }
    }]
  });
}

/** 渲染分时图（幂等：可重复调用，用于校准与切标的） */
function renderTimeshare(payload) {
  // v36: 分时不显示 ICT 结构标注（日线结构与分时无关），清掉可能残留的 overlay
  try { if (typeof _ictClearPro === 'function') _ictClearPro(); } catch (e) {}
  if (typeof restoreLegacyKlineContainer === 'function') restoreLegacyKlineContainer();
  if (!klineChart || klineChart.isDisposed()) {
    if (typeof initChart === 'function') initChart();
  }
  if (!klineChart || klineChart.isDisposed()) return;

  var d = payload || {};
  _tsState.code = d.code || _tsState.code || '';
  _tsState.name = d.name || '';
  _tsState.date = d.date || '';
  _tsState.prevClose = _klineLiveNum(d.prev_close, 0) || null;
  _tsState.source = d.source || '';
  _tsState.reason = d.reason || '';
  _tsState.degraded = !!d.degraded;
  _tsState.avgEstimated = !!d.avg_price_estimated;
  _tsState.points = Array.isArray(d.points) ? d.points.slice() : [];

  // 累计量/额以点为基准重建（实时增量的基线；60s 校准会纠正漂移）
  var cv = 0, ca = 0;
  for (var i = 0; i < _tsState.points.length; i++) {
    cv += _klineLiveNum(_tsState.points[i].volume, 0);
    ca += _klineLiveNum(_tsState.points[i].amount, 0);
  }
  _tsState.cumVol = cv;
  _tsState.cumAmt = ca;
  var lastP = _tsState.points.length ? _tsState.points[_tsState.points.length - 1] : null;
  _tsState.minuteBaseVol = Math.max(0, cv - (lastP ? _klineLiveNum(lastP.volume, 0) : 0));
  _tsState.minuteBaseAmt = Math.max(0, ca - (lastP ? _klineLiveNum(lastP.amount, 0) : 0));

  if (!_tsState.points.length) {
    _tsRenderUnavailable(d.reason || '分时数据源暂不可用');
    if (typeof klineChart !== 'undefined' && klineChart) klineChart.hideLoading();
    return;
  }

  _tsRepaint();
  if (typeof klineChart !== 'undefined' && klineChart) klineChart.hideLoading();

  // 启动/保持实时对接（同参数幂等，不会重复订阅）
  if (_tsState.code && typeof startKlineLive === 'function') {
    startKlineLive(_tsState.code, 'min');
  }
  _tsStartCalibration();
}

/** 依据 _tsState 重画（价格线 + 均价线 + 昨收基准 + 成交量柱） */
function _tsRepaint() {
  if (!klineChart || klineChart.isDisposed()) return;
  chartType = 'timeshare';

  var slots = _tsSlots;
  var n = slots.length;
  var prices = new Array(n);
  var avgs = new Array(n);
  var vols = new Array(n);
  var volColors = new Array(n);
  var pts = _tsState.points;
  var pc = _tsState.prevClose;
  var prevP = null;
  var pMax = -Infinity, pMin = Infinity;

  for (var i = 0; i < n; i++) { prices[i] = null; avgs[i] = null; vols[i] = null; }

  for (var k = 0; k < pts.length; k++) {
    var p = pts[k];
    var idx = _tsSlotIndex[p.t];
    if (idx == null) continue;
    var price = _klineLiveNum(p.price, 0);
    if (!(price > 0)) continue;
    prices[idx] = price;
    avgs[idx] = _klineLiveNum(p.avg_price, 0) || null;
    vols[idx] = Math.max(0, _klineLiveNum(p.volume, 0));
    // 量柱配色：与上一分钟比价（涨红 / 跌绿 / 平灰）
    var ref = (prevP != null) ? prevP : (pc || price);
    volColors[idx] = price > ref ? (window.THEME_COLORS ? THEME_COLORS.up : '#ff3d6a')
                    : price < ref ? (window.THEME_COLORS ? THEME_COLORS.dn : '#00e96a')
                    : 'rgba(140,165,205,0.55)';
    prevP = price;
    if (price > pMax) pMax = price;
    if (price < pMin) pMin = price;
  }

  if (!isFinite(pMax)) { _tsRenderUnavailable(_tsState.reason || '无有效分时点'); return; }

  // —— 量轴截断（视觉辅助，不改变真实值）——
  // 分钟量分布极度右偏（开盘集合竞价量可达中位数的 10~15 倍），若量轴按最大值
  // 线性铺满，普通分钟柱高不足 4% 像素、几乎不可见。
  // 取当日分钟量 **P90 × 1.8**（并以中位数 ×2 兜底）作为量轴上界：
  //   - 超出者柱体顶格（仍如实画出，不裁剪数据）；
  //   - tooltip 始终显示**真实成交量**；
  //   - 图内显式标注「已截断」，让用户知道当前不是全量线性刻度。
  var volVals = [];
  for (var vi = 0; vi < n; vi++) {
    if (vols[vi] != null && vols[vi] > 0) volVals.push(vols[vi]);
  }
  var volMax = 0, volP90 = 0, volP50 = 0, volCap = 0, volTruncated = false;
  if (volVals.length) {
    volMax = Math.max.apply(null, volVals);
    if (volVals.length >= 5) {
      var volSorted = volVals.slice().sort(function (a, b) { return a - b; });
      volP90 = volSorted[Math.min(volSorted.length - 1, Math.floor(volSorted.length * 0.90))];
      volP50 = volSorted[Math.min(volSorted.length - 1, Math.floor(volSorted.length * 0.50))];
      var capTry = Math.max(volP90 * 1.8, volP50 * 2);
      if (capTry < volMax) { volCap = capTry; volTruncated = true; }
    }
  }
  _tsState.volMax = volMax;
  _tsState.volP90 = volP90;
  _tsState.volP50 = volP50;
  _tsState.volCap = volCap;
  _tsState.volTruncated = volTruncated;

  var base = pc || prices[0] || pMax;
  var dev = Math.max(Math.abs(pMax - base), Math.abs(pMin - base));
  if (!(dev > 0)) dev = base * 0.005;
  var pad = dev * 1.08;
  var yMin = base - pad;
  var yMax = base + pad;

  // 价格线颜色：最新价 vs 昨收（涨红 / 跌绿 / 平灰）
  var lastPrice = 0;
  for (var q = n - 1; q >= 0; q--) { if (prices[q] != null) { lastPrice = prices[q]; break; } }
  var upColor = window.THEME_COLORS ? THEME_COLORS.up : '#ff3d6a';
  var dnColor = window.THEME_COLORS ? THEME_COLORS.dn : '#00e96a';
  var priceColor = pc ? (lastPrice > pc ? upColor : (lastPrice < pc ? dnColor : '#8b9ec2')) : '#508cff';

  klineChart.setOption({
    backgroundColor: 'transparent',
    animation: true,
    animationDuration: 300,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', link: [{ xAxisIndex: 'all' }] },
      backgroundColor: 'rgba(6,12,28,0.96)',
      borderColor: 'rgba(59,130,246,0.25)',
      borderWidth: 1,
      padding: [8, 12],
      textStyle: { color: '#e2e8f0', fontSize: 11 },
      formatter: function (params) {
        if (!params || !params.length) return '';
        var i0 = params[0].dataIndex;
        var pr = prices[i0];
        if (pr == null) return slots[i0] + '<br/>暂无成交';
        var av = avgs[i0];
        var chg = pc ? ((pr / pc - 1) * 100) : null;
        var col = chg == null ? '#e2e8f0' : (chg >= 0 ? upColor : dnColor);
        var s = '<b>' + slots[i0] + '</b><br/>价格：<b style="color:' + col + '">' + pr.toFixed(2) + '</b>';
        if (chg != null) s += ' (' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%)';
        if (av != null) s += '<br/>均价：<b style="color:#f5b942">' + av.toFixed(2) + '</b>' + (_tsState.avgEstimated ? '（推算）' : '');
        var vReal = vols[i0];
        s += '<br/>成交量：<b>' + (vReal != null ? vReal.toFixed(0) : '--') + '</b> 手';
        if (volTruncated && vReal != null && vReal > volCap) {
          s += '<span style="color:#f5b942">（超出量轴，顶格显示；此为真实值）</span>';
        }
        if (pc) s += '<br/>昨收：' + pc.toFixed(2);
        if (_tsState.source) s += '<br/><span style="color:#8b9ec2">数据源：' + _tsState.source + '</span>';
        return s;
      }
    },
    grid: [
      { left: 58, right: 58, top: 14, height: '58%' },
      { left: 58, right: 58, top: '76%', height: '16%' }
    ],
    xAxis: [
      {
        type: 'category', gridIndex: 0, data: slots, boundaryGap: false,
        axisLine: { lineStyle: { color: 'rgba(59,130,246,0.12)' } },
        axisTick: { show: false },
        axisLabel: {
          color: '#3d5278', fontSize: 9,
          interval: function (index) { return index === 0 || index === 120 || index === 121 || index === n - 1; }
        },
        splitLine: { show: true, interval: 60, lineStyle: { color: 'rgba(59,130,246,0.05)' } }
      },
      {
        type: 'category', gridIndex: 1, data: slots, boundaryGap: false,
        axisLine: { lineStyle: { color: 'rgba(59,130,246,0.12)' } },
        axisTick: { show: false },
        axisLabel: { show: false },
        splitLine: { show: false }
      }
    ],
    yAxis: [
      {
        scale: true, gridIndex: 0, min: yMin, max: yMax,
        axisLabel: {
          color: '#3d5278', fontSize: 9,
          formatter: function (v) { return v.toFixed(2); }
        },
        splitLine: { lineStyle: { color: 'rgba(59,130,246,0.05)', type: 'dashed' } }
      },
      {
        scale: true, gridIndex: 0, position: 'right', min: yMin, max: yMax,
        axisLabel: {
          color: '#3d5278', fontSize: 9,
          formatter: function (v) { return pc ? (((v / pc) - 1) * 100).toFixed(2) + '%' : ''; }
        },
        splitLine: { show: false }
      },
      {
        gridIndex: 1, scale: false, min: 0,
        max: volCap > 0 ? volCap : null,   // 截断上界；超出者柱体顶格（真实值见 tooltip）
        name: volTruncated ? '量轴截断 P90' : '',
        nameLocation: 'end',
        nameTextStyle: { color: '#f5b942', fontSize: 9, align: 'left' },
        nameGap: 4,
        axisLabel: { color: '#3d5278', fontSize: 8 },
        splitLine: { show: false }
      }
    ],
    graphic: volTruncated ? [{
      type: 'text',
      right: 62,
      top: '73%',
      style: {
        text: '量轴已截断至 P90×1.8（顶格柱=超出，真实量见 tooltip）',
        fill: '#f5b942', fontSize: 9, textAlign: 'right'
      }
    }] : [],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 }],
    series: [
      {
        name: '价格', type: 'line', xAxisIndex: 0, yAxisIndex: 0,
        data: prices, symbol: 'none', connectNulls: false, smooth: false,
        lineStyle: { width: 1.6, color: priceColor },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: priceColor === upColor ? 'rgba(255,61,106,0.22)' : (priceColor === dnColor ? 'rgba(0,233,106,0.18)' : 'rgba(80,140,255,0.16)') },
            { offset: 1, color: 'rgba(0,0,0,0)' }
          ])
        },
        markLine: {
          silent: true, symbol: 'none',
          lineStyle: { color: 'rgba(140,165,205,0.5)', type: 'dashed', width: 1 },
          label: {
            show: true, position: 'insideEndTop', color: '#8b9ec2', fontSize: 9,
            formatter: pc ? ('昨收 ' + pc.toFixed(2)) : ''
          },
          data: pc ? [{ yAxis: pc }] : []
        }
      },
      {
        name: '均价', type: 'line', xAxisIndex: 0, yAxisIndex: 0,
        data: avgs, symbol: 'none', connectNulls: false,
        lineStyle: { width: 1.1, color: '#f5b942', type: 'dashed' }
      },
      {
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 2,
        data: vols.map(function (v, i2) {
          return v == null ? null : { value: v, itemStyle: { color: volColors[i2] || 'rgba(140,165,205,0.45)' } };
        }),
        barMaxWidth: 4
      }
    ]
  }, { notMerge: true, lazyUpdate: false });

  _tsState.renders += 1;
}

/**
 * 实时报价 → 分时点（同一 SSE 通道，按分钟归并）
 * @returns {boolean} 是否成功应用
 */
/** 'HH:MM' → 当日分钟序号（解析失败返回 null） */
function _tsMinuteOfDay(hm) {
  var m = String(hm || '').match(/^(\d{1,2}):(\d{2})$/);
  if (!m) return null;
  var h = parseInt(m[1], 10), mi = parseInt(m[2], 10);
  if (!isFinite(h) || !isFinite(mi)) return null;
  return h * 60 + mi;
}

/** 任意时刻 → 规范交易分钟槽：集合竞价→09:30，午休→11:30，盘后→15:00 */
function _tsCanonSlot(hm) {
  var mod = _tsMinuteOfDay(hm);
  if (mod == null) return null;
  if (mod < 570) return '09:30';
  if (mod > 690 && mod < 780) return '11:30';
  if (mod > 900) return '15:00';
  return hm;
}

/**
 * 实时报价 → 分时点（同一 SSE 通道，按分钟归并）
 *
 * v33：时序判定改用「当日分钟序号」而非槽位下标 —— 分时数据可能包含 15:30
 * （盘后固定价格交易）等非标准槽位，若按数组尾巴做基准会误判，导致早盘时间戳
 * 被当成新点插入。这里以「最后一个落在交易槽内的点」为时序基准。
 *
 * @returns {boolean} 是否成功应用
 */
function _tsApplyQuote(q) {
  var price = _klineLiveNum(q.price, 0);
  if (!(price > 0)) return _tsReject('price<=0');
  if (_tsState.code && q.code && String(q.code) !== String(_tsState.code)) return _tsReject('code_mismatch');

  var pc = _klineLiveNum(q.prev_close, 0);
  if (pc > 0) _tsState.prevClose = pc;

  var pts = _tsState.points;
  if (!pts.length) return _tsReject('no_points');       // 无基线 → 不造点

  var raw = String(q.bar_ts || q.ts || q.as_of || '');
  var mm = raw.match(/(\d{1,2}):(\d{2})/);
  var hmRaw = mm ? (_klineLivePad2(mm[1]) + ':' + _klineLivePad2(mm[2])) : '';
  if (!hmRaw) hmRaw = pts[pts.length - 1].t;
  var slot = _tsCanonSlot(hmRaw);
  if (!slot) return _tsReject('no_time');

  // 现有数据建索引 + 求「最后一个落在交易槽内的点」作为时序基准
  var byT = {};
  var ref = null, refIdx = -1;
  for (var i = 0; i < pts.length; i++) {
    var p0 = pts[i];
    byT[p0.t] = p0;
    var si = _tsSlotIndex[p0.t];
    if (si != null && si > refIdx) { refIdx = si; ref = p0; }
  }

  var newIdx = _tsSlotIndex[slot];

  // 时序校验必须**先于**目标槽存在性判断：历史槽虽然存在，也不允许被迟到的旧报价回写
  if (newIdx != null && refIdx >= 0 && newIdx < refIdx - 1) {
    _tsState.outOfOrder += 1;
    return _tsReject('out_of_order:' + slot + '<' + ref.t);
  }

  var target = null;
  var isNew = false;
  if (newIdx != null && refIdx >= 0 && newIdx < refIdx) {
    target = ref;                       // ≤1 分钟时钟偏差：归入基准点，不造新点
  } else {
    target = byT[slot] || null;
    if (!target) {
      if (newIdx == null) return _tsReject('off_slot:' + slot);
      target = { t: slot, price: price, avg_price: null, volume: 0, amount: 0 };
      pts.push(target);
      byT[slot] = target;
      isNew = true;
    }
  }
  var qVol = _klineLiveNum(q.volume, 0);          // 累计成交量（手）
  var qAmtWan = _klineLiveNum(q.amount, 0);       // 累计成交额（万元）
  var qAmt = qAmtWan > 0 ? qAmtWan * 1e4 : 0;     // → 元

  var isLast = (target === pts[pts.length - 1]);
  if (isLast) {
    // 该分钟基线 = 当前累计 - 该点已记录的量（新点已记录量为 0）
    var baseVol = Math.max(0, _tsState.cumVol - _klineLiveNum(target.volume, 0));
    var baseAmt = Math.max(0, _tsState.cumAmt - _klineLiveNum(target.amount, 0));
    if (qVol > 0) _tsState.cumVol = qVol;
    if (qAmt > 0) _tsState.cumAmt = qAmt;
    target.volume = Math.max(0, _tsState.cumVol - baseVol);
    target.amount = Math.max(0, _tsState.cumAmt - baseAmt);
  } else {
    if (qVol > 0) _tsState.cumVol = qVol;
    if (qAmt > 0) _tsState.cumAmt = qAmt;
  }

  target.price = price;
  if (_tsState.cumVol > 0 && _tsState.cumAmt > 0) {
    // 均价 = 累计成交额 ÷ 累计成交股数（手×100），真实口径
    target.avg_price = +(_tsState.cumAmt / (_tsState.cumVol * 100)).toFixed(3);
  }

  _tsRepaint();
  _tsState.applied += 1;
  _tsState.lastApplied = {
    t: target.t, price: price, isNew: isNew,
    cumVol: _tsState.cumVol, cumAmt: _tsState.cumAmt, at: Date.now()
  };
  _klineLive.updates += 1;
  return true;
}

/** 60s 校准：用 /api/timeshare 校正累计漂移（失败不影响增量继续） */
function _tsStartCalibration() {
  _tsStopCalibration();
  _tsState.calibTimer = setInterval(function () {
    if (typeof document !== 'undefined' && document.hidden) return;
    if (_klineLive.period !== 'min') { _tsStopCalibration(); return; }
    if (!_tsState.code || typeof window.loadAndRenderTimeshare !== 'function') return;
    window.loadAndRenderTimeshare(_tsState.code, { silent: true, calibrate: true });
  }, TS_CALIBRATE_MS);
}

function _tsStopCalibration() {
  if (_tsState.calibTimer) {
    clearInterval(_tsState.calibTimer);
    _tsState.calibTimer = 0;
  }
}

/**
 * 拉取并渲染分时（供 setPeriod / 校准 / 切标的调用）
 * @param {string} code
 * @param {{silent?:boolean, calibrate?:boolean}} [opts]
 */
window.loadAndRenderTimeshare = function (code, opts) {
  opts = opts || {};
  code = String(code || '').trim();
  if (!code) return Promise.resolve(null);
  // 校准只在当前确实是分时视图时执行
  if (opts.calibrate && window.appState && window.appState.period !== 'min') return Promise.resolve(null);

  var url = '/api/timeshare?code=' + encodeURIComponent(code);
  return fetch(url, { headers: { 'Accept': 'application/json' }, cache: 'no-store' })
    .then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    })
    .then(function (json) {
      if (!json || !json.ok || !json.data) {
        throw new Error((json && json.message) || '分时接口返回异常');
      }
      // v32 竞态防护：请求发出后用户可能已切到日K/周K（或本请求是校准/重连回补），
      // 返回时必须再确认「当前仍是分时视图」，否则会把日K图表覆盖回分时并污染 chartType。
      if (window.appState && window.appState.period && window.appState.period !== 'min') {
        return json.data;
      }
      renderTimeshare(json.data);
      return json.data;
    })
    .catch(function (err) {
      var msg = (err && err.message) ? err.message : '网络错误';
      if (!opts.calibrate) {
        _tsState.code = code;
        _tsState.points = [];
        _tsState.reason = msg;
        _tsRenderUnavailable(msg);
        if (typeof klineChart !== 'undefined' && klineChart && !klineChart.isDisposed()) klineChart.hideLoading();
        if (typeof renderKlineLiveBadge === 'function') {
          _klineLive.status = 'degraded';
          _klineLive.reason = '分时接口不可用：' + msg;
          renderKlineLiveBadge();
        }
      }
      // 校准失败：保留现有增量数据，不打断实时
      return null;
    });
};

function renderKline(data) {
  // v32：任何进入 K 线渲染的路径都先把图表类型归位（Pro 分支会提前 return，
  // 否则从分时切回日K后 chartType 仍停留在 'timeshare'，误导诊断与分支逻辑）
  chartType = 'kline';
  // v36: 记录「图上真正渲染」的标的/周期/K线，供指标与 ICT 使用
  if (data) {
    _dpRenderCtx = {
      code: data.code || (window.appState && window.appState.activeCode) || '',
      period: data.period || (window.appState && window.appState.period) || 'day',
      klines: data.klines || []
    };
  }
  refreshThemeColors(); // v18: 刷新涨跌主题色（涨=红/跌=绿）
  // v16: 联动加载 K线+震荡指标自动分析总结
  if (typeof window.loadChartSummary === 'function' && data && data.klines && data.klines.length) {
    var _csCode = data.code || (window.appState && window.appState.activeCode);
    var _csPeriod = data.period || (window.appState && window.appState.period) || 'day';
    if (_csCode) window.loadChartSummary(_csCode, _csPeriod);
  }
  // v10.1: 优先走 KLineChart Pro 引擎；任何异常都会回退 ECharts（本次会话永久生效）
  if (window._proChartDisabled !== true && typeof window.klinecharts !== 'undefined') {
    try {
      renderKlinePro(data);
      // v29: 渲染完成后按当前 code/period 对齐实时对接（同参数幂等，不会重复订阅）
      _klineLiveSyncFromRender(data);
      return;
    } catch (e) {
      console.error('[DAPENG] KLineChart Pro 渲染失败，回退 ECharts:', e);
      window._proChartDisabled = true;
      restoreLegacyKlineContainer();
    }
  }
  // 兜底闭环：无论标志由 catch 设置还是外部预置，进入 legacy 分支前必须恢复 legacy 容器
  if (typeof restoreLegacyKlineContainer === 'function') restoreLegacyKlineContainer();
  if (!klineChart) { initChart(); if (!klineChart) return; }
  if (klineChart.isDisposed()) { initChart(); if (!klineChart) return; }
  chartType = 'kline';
  if (!data || !data.klines || !data.klines.length) {
    klineChart.clear();
    // v29: 无有效 K 线数据时不维持实时对接（避免用报价凭空造 bar）
    _klineLiveSyncFromRender(data);
    return;
  }

  var cfg = window._indicatorConfig || {};
  var indParams = cfg.params || {};
  var macdParams = indParams.macd || [12, 26, 9];
  var kdjParams = indParams.kdj || [9, 3, 3];
  var rsiParams = indParams.rsi || [14];
  var bollParams = indParams.boll || [20, 2];
  var subTitleMap = {
    macd: 'MACD (' + macdParams.join(',') + ')',
    kdj: 'KDJ (' + kdjParams.join(',') + ')',
    rsi: 'RSI (' + rsiParams.join(',') + ')'
  };
  // v35: 12 类扩展指标的标题带实际参数（如 ADX(14)、SuperTrend(10,3.0)）
  _DP_EXTRA_NAMES.forEach(function (nm) { subTitleMap[nm] = _dpLabel(nm); });
  if (cfg.subIndicator && !subTitleMap[cfg.subIndicator]) cfg.subIndicator = 'macd';
  var activeSubIndicator = cfg.subIndicator || 'macd';
  var _dpActiveExtra = _dpIsExtra(activeSubIndicator) ? _dpNormalizeName(activeSubIndicator) : null;
  var _dpActiveOverlay = !!_dpActiveExtra && _dpIsOverlay(_dpActiveExtra);
  var showMacdPane = activeSubIndicator === 'macd';
  // v35: 扩展指标中「非 overlay」的 7 个占用振荡副图（overlay 的 5 个不占副图）
  var showOscillatorPane = activeSubIndicator === 'kdj' || activeSubIndicator === 'rsi'
    || (!!_dpActiveExtra && !_dpActiveOverlay);
  var showVolumePane = !!cfg.showVolume;
  var subTitle = subTitleMap[activeSubIndicator] || subTitleMap.macd;
  var kls = data.klines;
  var maData = data.ma || {};
  var macdData = data.macd || {};
  var dates = kls.map(function(k) { return k.d; });
  var candles = kls.map(function(k) { return [k.o, k.c, k.l, k.h]; });
  var closes = kls.map(function(k) { return k.c; });
  var highs = kls.map(function(k) { return k.h; });
  var lows = kls.map(function(k) { return k.l; });
  var rawVolumes = kls.map(function(k) { return k.v; });

  // 成交量柱（带渐变 + 微边框）；v18: 涨=红 / 跌=绿
  var volUpFill = _hexToRgba(_themeUp, 0.7), volUpFade = _hexToRgba(_themeUp, 0.12), volUpBrd = _hexToRgba(_themeUp, 0.3);
  var volDnFill = _hexToRgba(_themeDown, 0.7), volDnFade = _hexToRgba(_themeDown, 0.12), volDnBrd = _hexToRgba(_themeDown, 0.3);
  var volumes = kls.map(function(k) {
    var isUp = k.c >= k.o;
    return {
      value: k.v,
      itemStyle: {
        borderWidth: 0.5,
        borderColor: isUp ? volUpBrd : volDnBrd,
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: isUp ? volUpFill : volDnFill },
          { offset: 1, color: isUp ? volUpFade : volDnFade },
        ]),
      },
    };
  });

  // 均线
  var ma5  = (maData.ma5  || []).map(function(v) { return v > 0 ? +v.toFixed(2) : null; });
  var ma10 = (maData.ma10 || []).map(function(v) { return v > 0 ? +v.toFixed(2) : null; });
  var ma20 = (maData.ma20 || []).map(function(v) { return v > 0 ? +v.toFixed(2) : null; });
  var ma60 = (maData.ma60 || []).map(function(v) { return v > 0 ? +v.toFixed(2) : null; });

  // 技术指标（v16: 参数可自定义）
  var ema12  = calcEMA(closes, 12);
  var ema26  = calcEMA(closes, 26);
  var boll   = calcBOLL(closes, bollParams[0], bollParams[1]);
  var bbi    = calcBBI(closes);
  var rsi14  = calcRSI(closes, rsiParams[0]);
  var kdj    = calcKDJ(highs, lows, closes, kdjParams[0], kdjParams[1], kdjParams[2]);
  var sr     = calcSupportResistance(kls);

  // MACD（默认参数优先用服务端序列；自定义参数时本地计算）
  var dif, dea, hist;
  if (macdParams.join(',') === '12,26,9' && macdData.dif && macdData.dif.length) {
    dif = macdData.dif;
    dea = macdData.dea;
    hist = macdData.hist;
  } else {
    var emaF = calcEMA(closes, macdParams[0]);
    var emaS = calcEMA(closes, macdParams[1]);
    dif = closes.map(function (_, i) {
      return (emaF[i] != null && emaS[i] != null) ? +(emaF[i] - emaS[i]).toFixed(4) : null;
    });
    var difFilled = dif.map(function (v) { return v == null ? 0 : v; });
    var deaRaw = calcEMA(difFilled, macdParams[2]);
    dea = dif.map(function (v, i) { return v == null ? null : +deaRaw[i].toFixed(4); });
    hist = dif.map(function (v, i) { return v == null ? null : +((v - dea[i]) * 2).toFixed(4); });
  }

  // 量能均线
  var vol5  = calcMA(rawVolumes, 5);
  var vol10 = calcMA(rawVolumes, 10);

  // =========== 构建 series ===========
  var series = [];

  // [0] K线 — 带微渐变边框增强（v18: 涨=红 / 跌=绿）
  series.push({
    name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, data: candles,
    itemStyle: {
      color: _themeUp, color0: _themeDown,
      borderColor: _themeUp, borderColor0: _themeDown,
      borderWidth: 1,
    },
    emphasis: {
      itemStyle: { shadowBlur: 14, shadowColor: 'rgba(59,130,246,0.5)', borderWidth: 2 },
    },
  });

  // [1-3] BOLL 带
  if (cfg.showBOLL) {
    series.push({ name: 'BOLL上轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: boll.upper, symbol: 'none', silent: true, lineStyle: { width: 0.8, color: 'rgba(255,61,106,0.3)', type: 'dashed' }, tooltip: { show: false } });
    series.push({ name: 'BOLL中轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: boll.mid,  symbol: 'none', silent: true, lineStyle: { width: 0.8, color: 'rgba(255,255,255,0.25)' }, tooltip: { show: false } });
    series.push({ name: 'BOLL下轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: boll.lower, symbol: 'none', silent: true, lineStyle: { width: 0.8, color: 'rgba(0,233,106,0.3)', type: 'dashed' }, tooltip: { show: false } });
  } else {
    series.push({ name: 'BOLL上轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: [], tooltip: { show: false } });
    series.push({ name: 'BOLL中轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: [], tooltip: { show: false } });
    series.push({ name: 'BOLL下轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: [], tooltip: { show: false } });
  }

  // [4-7] 移动平均线
  series.push({ name: 'MA5',  type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: cfg.showMA5  ? ma5  : [], smooth: 0.3, symbol: 'none', lineStyle: { width: 1.2, color: '#f59e0b' }, tooltip: { show: false } });
  series.push({ name: 'MA10', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: cfg.showMA10 ? ma10 : [], smooth: 0.3, symbol: 'none', lineStyle: { width: 1.2, color: '#3b82f6' }, tooltip: { show: false } });
  series.push({ name: 'MA20', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: cfg.showMA20 ? ma20 : [], smooth: 0.3, symbol: 'none', lineStyle: { width: 1.2, color: '#a855f7' }, tooltip: { show: false } });
  series.push({ name: 'MA60', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: cfg.showMA60 ? ma60 : [], smooth: 0.3, symbol: 'none', lineStyle: { width: 1, color: 'rgba(255,61,106,0.5)', type: 'dotted' }, tooltip: { show: false } });

  // [8-9] EMA
  series.push({ name: 'EMA12', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ema12, smooth: 0.5, symbol: 'none', silent: true, lineStyle: { width: 0.8, color: 'rgba(0,233,106,0.35)' }, tooltip: { show: false } });
  series.push({ name: 'EMA26', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ema26, smooth: 0.5, symbol: 'none', silent: true, lineStyle: { width: 0.8, color: 'rgba(255,61,106,0.3)' },  tooltip: { show: false } });

  // [10] BBI
  series.push({ name: 'BBI', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: bbi, symbol: 'none', lineStyle: { width: 1.5, color: '#06b6d4' }, tooltip: { show: false } });

  // [11] 成交量
  series.push({
    name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: cfg.showVolume ? volumes : [], barMaxWidth: 10,
  });

  // [12-13] 量能均线
  series.push({ name: 'VOL5',  type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: cfg.showVolume ? vol5 : [], symbol: 'none', silent: true, lineStyle: { width: 1, color: '#f59e0b' }, tooltip: { show: false } });
  series.push({ name: 'VOL10', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: cfg.showVolume ? vol10 : [], symbol: 'none', silent: true, lineStyle: { width: 1, color: '#a855f7' }, tooltip: { show: false } });

  // [14-16] MACD
  series.push({ name: 'DIF', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: showMacdPane ? dif.map(function(v) { return typeof v === 'number' ? +v.toFixed(4) : v; }) : [], smooth: 0.2, symbol: 'none', lineStyle: { width: 1, color: '#3b82f6' } });
  series.push({ name: 'DEA', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: showMacdPane ? dea.map(function(v) { return typeof v === 'number' ? +v.toFixed(4) : v; }) : [], smooth: 0.2, symbol: 'none', lineStyle: { width: 1, color: '#f59e0b' } });

  series.push({
    name: 'MACD', type: 'bar', xAxisIndex: 2, yAxisIndex: 2, barMaxWidth: 6,
    data: showMacdPane ? hist.map(function(v) {
      var n = typeof v === 'number' ? v : parseFloat(v) || 0;
      return { value: +n.toFixed(4), itemStyle: { color: n >= 0 ? new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: _hexToRgba(_themeUp, 0.8) }, { offset: 1, color: _hexToRgba(_themeUp, 0.15) }]) : new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: _hexToRgba(_themeDown, 0.15) }, { offset: 1, color: _hexToRgba(_themeDown, 0.8) }]) } };
    }) : [],
  });

  // [17] RSI14（始终在副图区域3）
  var rsiSeries = {
    name: 'RSI14', type: 'line', xAxisIndex: 3, yAxisIndex: 3, data: activeSubIndicator === 'rsi' ? rsi14 : [], symbol: 'none',
    lineStyle: { width: 1.5, color: '#06b6d4' },
    markLine: { silent: true, symbol: 'none', lineStyle: { color: 'rgba(255,255,255,0.12)', type: 'dashed' },
      data: [{ yAxis: 70, label: { formatter: '超买 70', color: '#ff3d6a', fontSize: 8 } }, { yAxis: 30, label: { formatter: '超卖 30', color: '#00e96a', fontSize: 8 } }, { yAxis: 50, label: { formatter: '50', color: '#8b9ec2', fontSize: 8 } }] },
    areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(6,182,212,0.15)' }, { offset: 1, color: 'rgba(6,182,212,0.02)' }]) },
  };
  series.push(rsiSeries);

  // [18-20] KDJ（复用副图区域3，与 RSI 共用 Y 轴刻度 0-100）
  series.push({ name: 'KDJ_K', type: 'line', xAxisIndex: 3, yAxisIndex: 3, data: activeSubIndicator === 'kdj' ? kdj.k : [], symbol: 'none', smooth: 0.2, lineStyle: { width: 1.2, color: '#f59e0b' } });
  series.push({ name: 'KDJ_D', type: 'line', xAxisIndex: 3, yAxisIndex: 3, data: activeSubIndicator === 'kdj' ? kdj.d : [], symbol: 'none', smooth: 0.2, lineStyle: { width: 1.2, color: '#3b82f6' } });
  series.push({ name: 'KDJ_J', type: 'line', xAxisIndex: 3, yAxisIndex: 3, data: activeSubIndicator === 'kdj' ? kdj.j : [], symbol: 'none', smooth: 0.2, lineStyle: { width: 1, color: '#a855f7', type: 'dashed' } });

  // [21+] v35: 12 类扩展指标
  //   overlay=true（supertrend/ichimoku/donchian/keltner/vwap）叠主图 grid0；
  //   其余 7 个占振荡副图 grid3（与 KDJ/RSI 共用一块，互斥）。
  //   主图 min/max 由 _dpMainAxis 单点决定（只读 K 线高低点），overlay 值被
  //   _dpClampToCandle 钳进主图区间 —— 保证「叠不叠 overlay 主图刻度都一样」。
  var _dpMainExtent = _dpCandleExtent(kls, _dpZoomWindow().start, _dpZoomWindow().end);
  var _dpSubReason = null;
  if (_dpActiveExtra) {
    _dpSubReason = _dpReasonOf(_dpActiveExtra);
    if (_dpSubReason) {
      // 数据不足/无有效值：**只显示原因文本，不画 0 线**（0 会被当成真实指标值）
      subTitle = _dpLabel(_dpActiveExtra) + ' · 数据不足';
    }
    var _dpCtx = {
      kls: kls,
      overlay: _dpActiveOverlay,
      ext: _dpMainExtent,
      xIdx: _dpActiveOverlay ? 0 : 3,
      yIdx: _dpActiveOverlay ? 0 : 3
    };
    var _dpSeries = _dpBuildEchartsSeries(_dpActiveExtra, _dpCtx);
    for (var _dpI = 0; _dpI < _dpSeries.length; _dpI++) series.push(_dpSeries[_dpI]);
    // 副图参考线（超买/超卖）
    var _dpMl = _dpActiveOverlay ? null : _dpPaneMarkLines(_dpActiveExtra);
    if (_dpMl && _dpSeries.length) _dpSeries[0].markLine = _dpMl;
  }

  // v36: ICT 结构标注（独立 series，不参与主图刻度；关闭时**不挂**该 series）
  window._ict.seriesPushed = false;
  if (_ictActive()) {
    var _ictS = _ictEchartsSeries(kls, dates);
    if (_ictS) { series.push(_ictS); window._ict.seriesPushed = true; }
    _ictEnsure();
  }

  // =========== 支撑/压力位 markLine ===========
  var srMarkLines = [];
  if (cfg.showSupportResistance && sr.resistance.length > 0) {
    sr.resistance.forEach(function(r, i) {
      srMarkLines.push({ yAxis: r, label: { formatter: '压力 ' + r, color: '#ff3d6a', fontSize: 9, position: 'end' }, lineStyle: { color: 'rgba(255,61,106,0.4)', type: 'dashed', width: 1 } });
    });
  }
  if (cfg.showSupportResistance && sr.support.length > 0) {
    sr.support.forEach(function(s, i) {
      srMarkLines.push({ yAxis: s, label: { formatter: '支撑 ' + s, color: '#00e96a', fontSize: 9, position: 'end' }, lineStyle: { color: 'rgba(0,233,106,0.4)', type: 'dashed', width: 1 } });
    });
  }
  // =========== 最新价标线 ===========
  var lastClose = candles.length > 0 ? candles[candles.length - 1][1] : 0;
  if (lastClose > 0) {
    var lastClr = candles[candles.length - 1][1] >= candles[candles.length - 1][0] ? _themeUp : _themeDown;
    srMarkLines.push({ yAxis: lastClose, label: { formatter: lastClose.toFixed(2), color: '#fff', backgroundColor: lastClr, padding: [2, 5], borderRadius: 3, fontSize: 10, position: 'end' }, lineStyle: { color: lastClr, type: 'dashed', width: 1, opacity: 0.45 } });
  }

  if (srMarkLines.length > 0) {
    series[0].markLine = { silent: true, symbol: 'none', data: srMarkLines };
  }

  // =========== 最高/最低价标记 ===========
  // 在可见范围标记近期最高和最低
  var last60Start = Math.max(0, candles.length - 60);
  var visibleHigh = -Infinity, visibleLow = Infinity;
  var highIdx = last60Start, lowIdx = last60Start;
  for (var i = last60Start; i < candles.length; i++) {
    if (candles[i][3] > visibleHigh) { visibleHigh = candles[i][3]; highIdx = i; }
    if (candles[i][2] < visibleLow)  { visibleLow  = candles[i][2]; lowIdx  = i; }
  }
  var markPoints = [
    { name: '最高', coord: [highIdx, visibleHigh], value: visibleHigh.toFixed(2), symbol: 'pin', symbolSize: 28, itemStyle: { color: '#ff3d6a' }, label: { formatter: '{c}', color: '#ff3d6a', fontSize: 10 } },
    { name: '最低', coord: [lowIdx, visibleLow],   value: visibleLow.toFixed(2),  symbol: 'pin', symbolSize: 28, itemStyle: { color: '#00e96a' }, label: { formatter: '{c}', color: '#00e96a', fontSize: 10 } },
  ];
  series[0].markPoint = { data: markPoints, symbol: 'pin', symbolSize: 28, animation: true };

  // =========== 副图 Y 轴标签 ===========
  var subYLabel;
  if (_dpActiveExtra && !_dpActiveOverlay) {
    subYLabel = _dpSubYAxis(_dpActiveExtra, 3);
  } else if (cfg.subIndicator === 'kdj') {
    subYLabel = { scale: true, gridIndex: 3, axisLabel: { color: '#3d5278', fontSize: 8 }, splitLine: { lineStyle: { color: 'rgba(59,130,246,0.04)', type: 'dashed' } }, splitNumber: 4 };
  } else if (cfg.subIndicator === 'rsi') {
    subYLabel = { min: 0, max: 100, gridIndex: 3, axisLabel: { color: '#3d5278', fontSize: 8 }, splitLine: { show: false }, splitNumber: 3 };
  } else {
    subYLabel = { scale: true, gridIndex: 3, axisLabel: { color: '#3d5278', fontSize: 8 }, splitLine: { show: false }, splitNumber: 3 };
  }

  // =========== MACD 副图 Y 轴 ===========
  var macdYLabel = { scale: true, gridIndex: 2, axisLabel: { color: '#3d5278', fontSize: 8 }, splitLine: { show: false } };

  // =========== legend 数据 ===========
  var legendData = [];
  if (cfg.showMA5) legendData.push('MA5');
  if (cfg.showMA10) legendData.push('MA10');
  if (cfg.showMA20) legendData.push('MA20');
  if (cfg.showMA60) legendData.push('MA60');
  legendData.push('BBI');
  if (cfg.showBOLL) legendData.push('BOLL');

  // =========== 副图标题 ===========
  // =========== Tooltip formatter ===========
  var _volumesForTip = volumes;
  var _kdjForTip = kdj;
  var _rsiForTip = rsi14;
  var _bollForTip = boll;
  var _ma5ForTip = ma5, _ma10ForTip = ma10, _ma20ForTip = ma20, _ma60ForTip = ma60;

  var _dpAxis0 = (function () {
    var ax = { scale: true, gridIndex: 0, axisLabel: { color: '#3d5278', fontSize: 9, formatter: function (v) { return v.toFixed(2); } }, splitLine: { lineStyle: { color: 'rgba(59,130,246,0.04)', type: 'dashed' } } };
    var m = _dpMainAxis(kls, { start: 50, end: 100 });
    if (m) { ax.min = m.min; ax.max = m.max; delete ax.scale; }
    _dpLogAxis(_dpActiveExtra ? ('overlay:' + _dpActiveExtra) : ('plain:' + activeSubIndicator), m);
    return ax;
  })();
  klineChart.setOption({
    backgroundColor: 'transparent',
    animation: true,
    animationDuration: 500,
    animationEasing: 'cubicOut',
    title: [
      { text: data.adjusted !== false ? '前复权' : '不复权', left: 8, bottom: 4, textStyle: { color: 'rgba(61,82,120,0.35)', fontSize: 9 } },
      { text: subTitle, right: 14, bottom: 4, textStyle: { color: 'rgba(61,82,120,0.45)', fontSize: 9 } },
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'cross',
        crossStyle: { color: 'rgba(99,155,255,0.25)' },
        lineStyle: { color: 'rgba(99,155,255,0.12)', type: 'dashed' },
      },
      backgroundColor: 'rgba(6,12,28,0.98)',
      borderColor: 'rgba(59,130,246,0.3)',
      borderWidth: 1,
      extraCssText: 'border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,0.6);backdrop-filter:blur(16px)',
      formatter: function(ps) {
        var k = null;
        for (var i = 0; i < ps.length; i++) { if (ps[i].seriesName === 'K线') { k = ps[i]; break; } }
        if (!k) return '';
        var o = k.value[0], c = k.value[1], l = k.value[2], h = k.value[3];
        var idx = k.dataIndex;
        var chg = o !== 0 ? ((c - o) / o * 100).toFixed(2) : '0.00';
        var chgAbs = (c - o).toFixed(2);
        var amp = o !== 0 ? ((h - l) / o * 100).toFixed(2) : '0.00';
        var clr = c >= o ? _themeUp : _themeDown;
        var v = _volumesForTip[idx] ? _volumesForTip[idx].value : 0;
        var volRatio = '--';
        if (idx > 0 && v > 0) {
          var volSum = 0, volCnt = 0;
          for (var vi = Math.max(0, idx - 5); vi < idx; vi++) {
            if (_volumesForTip[vi] && _volumesForTip[vi].value > 0) { volSum += _volumesForTip[vi].value; volCnt++; }
          }
          if (volCnt > 0) volRatio = (v / (volSum / volCnt)).toFixed(2);
        }
        var volRatioClr = volRatio !== '--' && parseFloat(volRatio) > 1.5 ? '#f59e0b' : volRatio !== '--' && parseFloat(volRatio) < 0.5 ? '#06b6d4' : '#eef2f7';
        var html = '<div style="min-width:250px;font-family:monospace">' +
          '<div style="height:2px;background:' + clr + ';border-radius:1px;margin-bottom:6px"></div>' +
          '<div style="color:#8b9ec2;margin-bottom:6px;font-size:10px;letter-spacing:.5px">' + k.name + '</div>' +
          '<div style="display:grid;grid-template-columns:1fr 1fr;gap:2px 16px;font-size:11px">' +
          '<span style="color:#8b9ec2">开盘</span><span>' + o.toFixed(2) + '</span>' +
          '<span style="color:#8b9ec2">收盘</span><span style="color:' + clr + ';font-weight:700">' + c.toFixed(2) + '</span>' +
          '<span style="color:#8b9ec2">最高</span><span style="color:' + _themeUp + '">' + h.toFixed(2) + '</span>' +
          '<span style="color:#8b9ec2">最低</span><span style="color:' + _themeDown + '">' + l.toFixed(2) + '</span>' +
          '<span style="color:#8b9ec2">涨跌额</span><span style="color:' + clr + '">' + (c >= o ? '+' : '') + chgAbs + '</span>' +
          '<span style="color:#8b9ec2">涨跌幅</span><span style="color:' + clr + ';font-weight:700">' + (c >= o ? '▲ ' : '▼ ') + (chg > 0 ? '+' : '') + chg + '%</span>' +
          '<span style="color:#8b9ec2">振幅</span><span>' + amp + '%</span>' +
          '<span style="color:#8b9ec2">成交量</span><span>' + (v / 10000).toFixed(0) + '万手</span>' +
          '<span style="color:#8b9ec2">量比</span><span style="color:' + volRatioClr + ';font-weight:600">' + volRatio + '</span>' +
          '</div>';

        // 均线行
        var maLine = '';
        if (_ma5ForTip[idx] != null) maLine += '<span><span style="color:#f59e0b">MA5</span> ' + _ma5ForTip[idx].toFixed(2) + '</span>';
        if (_ma10ForTip[idx] != null) maLine += '<span><span style="color:#3b82f6">MA10</span> ' + _ma10ForTip[idx].toFixed(2) + '</span>';
        if (_ma20ForTip[idx] != null) maLine += '<span><span style="color:#a855f7">MA20</span> ' + _ma20ForTip[idx].toFixed(2) + '</span>';
        if (_ma60ForTip && _ma60ForTip[idx] != null) maLine += '<span><span style="color:#ff3d6a">MA60</span> ' + _ma60ForTip[idx].toFixed(2) + '</span>';
        if (maLine) html += '<div style="border-top:1px solid rgba(255,255,255,0.05);margin-top:5px;padding-top:5px;display:flex;gap:12px;font-size:10px;flex-wrap:wrap">' + maLine + '</div>';

        // BOLL
        if (cfg.showBOLL && _bollForTip.mid[idx] != null) {
          html += '<div style="display:flex;gap:12px;font-size:10px;margin-top:2px">' +
            '<span style="color:#ff3d6a">上轨 ' + _bollForTip.upper[idx].toFixed(2) + '</span>' +
            '<span style="color:#8b9ec2">中轨 ' + _bollForTip.mid[idx].toFixed(2) + '</span>' +
            '<span style="color:#00e96a">下轨 ' + _bollForTip.lower[idx].toFixed(2) + '</span></div>';
        }

        // KDJ
        if (activeSubIndicator === 'kdj' && _kdjForTip.k[idx] != null) {
          html += '<div style="display:flex;gap:12px;font-size:10px;margin-top:2px">' +
            '<span style="color:#f59e0b">K ' + _kdjForTip.k[idx].toFixed(2) + '</span>' +
            '<span style="color:#3b82f6">D ' + _kdjForTip.d[idx].toFixed(2) + '</span>' +
            '<span style="color:#a855f7">J ' + _kdjForTip.j[idx].toFixed(2) + '</span></div>';
        }

        // RSI
        if (activeSubIndicator === 'rsi' && _rsiForTip[idx] != null) {
          var rc = _rsiForTip[idx] > 70 ? '#ff3d6a' : _rsiForTip[idx] < 30 ? '#00e96a' : '#8b9ec2';
          html += '<div style="font-size:10px;margin-top:2px"><span style="color:#8b9ec2">RSI(14)</span> <span style="color:' + rc + ';font-weight:600">' + _rsiForTip[idx].toFixed(1) + '</span></div>';
        }

        // v36: ICT 标注明细（hover 到带/三角所在 bar 时给出原文说明）
        try {
          if (_ictActive()) {
            var _ictAnn = _ictAnnotations();
            var _ictDate = dates[idx];
            if (_ictAnn && _ictDate) {
              var _ictRows = [];
              (_ictAnn.fvgs || []).forEach(function (f) {
                if (_ictLocalDate(f.time) !== _ictNormBarDate(_ictDate)) return;
                _ictRows.push('<span style="color:' + _ictColor(f.direction) + '">FVG ' + _ictDirLabel(f.direction) + ' ' + Number(f.zone[0]).toFixed(2) + '~' + Number(f.zone[1]).toFixed(2) + '</span>');
              });
              (_ictAnn.order_blocks || []).forEach(function (o) {
                if (_ictLocalDate(o.time) !== _ictNormBarDate(_ictDate)) return;
                _ictRows.push('<span style="color:' + _ictColor(o.direction) + '">OB ' + _ictDirLabel(o.direction) + ' ' + Number(o.zone[0]).toFixed(2) + '~' + Number(o.zone[1]).toFixed(2) + '</span>');
                if (o.note) _ictRows.push('<span style="color:#f59e0b;font-size:9px">' + o.note + '</span>');
              });
              (_ictAnn.sweeps || []).forEach(function (sw) {
                if (_ictLocalDate(sw.time) !== _ictNormBarDate(_ictDate)) return;
                _ictRows.push('<span style="color:' + _ictColor(sw.direction) + '">扫单 ' + (sw.kind || '') + ' @' + Number(sw.extreme).toFixed(2) + ' · ' + (sw.session_label || '') + '</span>');
              });
              if (_ictRows.length) {
                html += '<div style="border-top:1px solid rgba(255,255,255,0.08);margin-top:5px;padding-top:4px;display:flex;flex-direction:column;gap:1px;font-size:10px">'
                  + '<span style="color:#8b9ec2;font-size:9px">ICT 结构</span>' + _ictRows.join('') + '</div>';
                // v37: 该 bar 上出现的结构类型 -> 追加对应历史统计（各类型一次，不重复）
                var _ictSeen = {};
                (_ictAnn.fvgs || []).forEach(function (f) {
                  if (_ictLocalDate(f.time) === _ictNormBarDate(_ictDate)) _ictSeen.FVG = 1;
                });
                (_ictAnn.order_blocks || []).forEach(function (o) {
                  if (_ictLocalDate(o.time) === _ictNormBarDate(_ictDate)) _ictSeen.OB = 1;
                });
                (_ictAnn.sweeps || []).forEach(function (sw) {
                  if (_ictLocalDate(sw.time) === _ictNormBarDate(_ictDate)) _ictSeen.SWEEP = 1;
                });
                Object.keys(_ictSeen).forEach(function (tp) {
                  try { html += _ictStatsBlock(tp); } catch (e) {}
                });
              }
            }
          }
        } catch (e) {}

        // v35: 12 类扩展指标（多线用 panes，单线用 values；不可用时显示原因）
        if (_dpActiveExtra) {
          try { html += _dpTooltipHtml(_dpActiveExtra, idx); } catch (e) {}
        }

        html += '</div>';
        return html;
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2, 3], start: 50, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1, 2, 3], bottom: 2, height: 18, start: 50, end: 100,
        backgroundColor: 'rgba(6,12,28,0.5)', borderColor: 'rgba(59,130,246,0.12)',
        fillerColor: 'rgba(59,130,246,0.06)', handleStyle: { color: '#3b82f6' },
        textStyle: { color: '#3d5278', fontSize: 9 } },
    ],
    grid: [
      { left: 60, right: 14, top: 8, height: (showVolumePane && (showMacdPane || showOscillatorPane)) ? '46%' : ((showVolumePane || showMacdPane || showOscillatorPane) ? '65%' : '80%') },
      { left: 60, right: 14, top: showVolumePane ? ((showMacdPane || showOscillatorPane) ? '58%' : '76%') : '90%', height: showVolumePane ? '13%' : '0%' },
      { left: 60, right: 14, top: showMacdPane ? (showVolumePane ? '75%' : '76%') : '90%', height: showMacdPane ? '12%' : '0%' },
      { left: 60, right: 14, top: showOscillatorPane ? (showVolumePane ? '75%' : '76%') : '90%', height: showOscillatorPane ? '15%' : '0%' },
    ],
    xAxis: [
      { gridIndex: 0, data: dates, axisLine: { lineStyle: { color: 'rgba(59,130,246,0.06)' } }, axisLabel: { show: !showVolumePane && !showMacdPane && !showOscillatorPane, color: '#3d5278', fontSize: 9 }, show: !showVolumePane && !showMacdPane && !showOscillatorPane },
      { gridIndex: 1, data: dates, axisLine: { lineStyle: { color: 'rgba(59,130,246,0.06)' } }, axisLabel: { show: showVolumePane && !showMacdPane && !showOscillatorPane, color: '#3d5278', fontSize: 9 }, show: showVolumePane },
      { gridIndex: 2, data: dates, axisLine: { lineStyle: { color: 'rgba(59,130,246,0.06)' } }, axisLabel: { show: showMacdPane && !showOscillatorPane, color: '#3d5278', fontSize: 9 }, show: showMacdPane },
      { gridIndex: 3, data: dates, axisLine: { lineStyle: { color: 'rgba(59,130,246,0.06)' } }, axisLabel: { show: showOscillatorPane, color: '#3d5278', fontSize: 9 }, show: showOscillatorPane },
    ],
    yAxis: [
      // v35: 主图 min/max 由 _dpMainAxis 单点给出（仅 K 线高低点 + 1% 留白）。
      // 不用 ECharts 自动刻度：自动刻度会把叠加的 overlay 指标算进去（先行带/唐奇安
      // 外轨会把蜡烛压扁）。此写法与原 'scale:true' 视觉几乎一致，且叠加前后**逐位相等**。
      _dpAxis0,
      { scale: true, gridIndex: 1, axisLabel: { color: '#3d5278', fontSize: 8 }, splitLine: { show: false }, show: showVolumePane },
      Object.assign({}, macdYLabel, { show: showMacdPane }),
      Object.assign({}, subYLabel, { show: showOscillatorPane }),
    ],
    series: series,
    legend: { data: legendData, top: 4, right: 14, textStyle: { color: '#3d5278', fontSize: 10 }, itemWidth: 14, itemHeight: 2, icon: 'roundRect', itemGap: 10 },
    graphic: [
      showVolumePane ? { type: 'text', left: 64, top: '59%', style: { text: 'VOL', fill: 'rgba(139,158,194,0.42)', fontSize: 9 }, silent: true, z: 100 } : null,
      showMacdPane ? { type: 'text', left: 64, top: showVolumePane ? '76%' : '76%', style: { text: 'MACD(12,26,9)', fill: 'rgba(139,158,194,0.42)', fontSize: 9 }, silent: true, z: 100 } : null,
      showOscillatorPane ? { type: 'text', left: 64, top: showVolumePane ? '76%' : '76%', style: { text: subTitle, fill: 'rgba(139,158,194,0.42)', fontSize: 9 }, silent: true, z: 100 } : null,
      // v35: 扩展指标不可用时，把后端 reason 原文摆在图上（不画 0 线、不静默）
      _dpSubReason ? { type: 'text', left: '18%', top: _dpActiveOverlay ? '44%' : '82%', z: 120, silent: true,
        style: { text: _dpSubReason, fill: '#f59e0b', fontSize: 10, width: 320, overflow: 'break', lineHeight: 14 } } : null,
    ].filter(Boolean),
  }, { notMerge: true, lazyUpdate: false });
  // v17: 兼容渲染（ECharts）路径下同步全屏状态栏（无 overlay 能力时图例给出说明）
  if (typeof kfsAfterRenderKline === 'function') kfsAfterRenderKline(data);
  // v29: 兼容渲染路径同样对齐实时对接（走 updateLastCandle 增量刷新）
  _klineLiveSyncFromRender(data);
}

/**
 * v35: 缩放后重算主图 Y 轴（同样只读 K 线高低点）
 * 说明：ECharts 的 dataZoom 只改可视窗口、不重跑 renderKline，故在此同步刻度，
 * 保证「任何时候主图刻度都等于当前可见 K 线区间」，与叠加哪些指标无关。
 */
function _dpSyncMainAxisOnZoom() {
  if (!klineChart || klineChart.isDisposed() || chartType !== 'kline') return;
  var kls = _dpCurrentKlines();
  if (!kls.length) return;
  var m = _dpMainAxis(kls, _dpZoomWindow());
  if (!m) return;
  _dpLogAxis('zoom', m);
  try { klineChart.setOption({ yAxis: [{ min: m.min, max: m.max }] }, { notMerge: false, lazyUpdate: true, silent: true }); } catch (e) {}
}

(function () {
  var _t = 0;
  var bind = function () {
    if (!klineChart || klineChart.isDisposed() || klineChart.__dpZoomBound) return;
    klineChart.__dpZoomBound = true;
    klineChart.on('dataZoom', function () {
      clearTimeout(_t);
      _t = setTimeout(_dpSyncMainAxisOnZoom, 120);
    });
  };
  window.__dpBindZoom = bind;
  // initChart 完成后绑定（幂等）
  window.addEventListener('resize', function () { try { bind(); } catch (e) {} });
  setTimeout(function () { try { bind(); } catch (e) {} }, 0);
  setTimeout(function () { try { bind(); } catch (e) {} }, 1500);
})();

/**
 * 调整 K 线图尺寸
 */
function resizeKline() {
  if (klineChart && !klineChart.isDisposed()) {
    klineChart.resize();
  }
  if (typeof proChart !== 'undefined' && proChart) {
    try { sizeProToCard(); applyProPaneHeights(); proChart.resize(); } catch (e) {}
  }
  if (window.appState && window.appState.radarChart && !window.appState.radarChart.isDisposed()) {
    window.appState.radarChart.resize();
  }
}

window.resetKlineZoom = function() {
  if (window._proChartDisabled !== true && typeof proChart !== 'undefined' && proChart) {
    try { proChart.scrollToRealTime(); } catch (e) {}
    return;
  }
  if (!klineChart || klineChart.isDisposed()) return;
  klineChart.dispatchAction({type:'dataZoom',start:50,end:100});
};

function syncKlinePeriodButtons() {
  var period=window.appState&&window.appState.period||'day';
  document.querySelectorAll('[data-kline-period]').forEach(function(button){
    var active=button.getAttribute('data-kline-period')===String(period);
    button.classList.toggle('active',active);
    button.setAttribute('aria-pressed',String(active));
  });
}

function updateKlineFullscreenState() {
  var card=document.getElementById('kline-card');
  var button=document.getElementById('kline-fullscreen-btn');
  var label=document.getElementById('kline-fullscreen-label');
  var icon=document.getElementById('kline-fullscreen-icon');
  if(!card)return;
  var active=document.fullscreenElement===card||card.classList.contains('kline-fullscreen-fallback');
  card.classList.toggle('kline-fullscreen-active',active);
  document.body.classList.toggle('kline-fallback-open',card.classList.contains('kline-fullscreen-fallback'));
  if(button){button.setAttribute('aria-pressed',String(active));button.title=active?'退出K线全屏':'全屏查看K线';button.setAttribute('aria-label',button.title);}
  if(label)label.textContent=active?'退出全屏':'全屏';
  if(icon)icon.textContent=active?'⤢':'⛶';
  if(typeof kfsOnFullscreenChange==='function')kfsOnFullscreenChange(active);
  window.setTimeout(resizeKline,80);
}

window.toggleKlineFullscreen = function() {
  var card=document.getElementById('kline-card');
  if(!card)return;
  if(document.fullscreenElement&&document.fullscreenElement!==card)return;
  if(document.fullscreenElement===card){
    if(document.exitFullscreen)document.exitFullscreen().catch(function(){});
    return;
  }
  if(card.classList.contains('kline-fullscreen-fallback')){
    card.classList.remove('kline-fullscreen-fallback');
    updateKlineFullscreenState();
    return;
  }
  if(card.requestFullscreen){
    card.requestFullscreen().then(updateKlineFullscreenState).catch(function(){card.classList.add('kline-fullscreen-fallback');updateKlineFullscreenState();});
  }else{
    card.classList.add('kline-fullscreen-fallback');
    updateKlineFullscreenState();
  }
};

document.addEventListener('fullscreenchange',function(){
  if(!document.fullscreenElement){var card=document.getElementById('kline-card');if(card)card.classList.remove('kline-fullscreen-fallback');}
  updateKlineFullscreenState();
});
document.addEventListener('keydown',function(event){
  if(event.key==='Escape'){
    var card=document.getElementById('kline-card');
    if(card&&card.classList.contains('kline-fullscreen-fallback')){card.classList.remove('kline-fullscreen-fallback');updateKlineFullscreenState();}
  }
});

/**
 * 实时更新 K 线最后一根蜡烛（交易时段实时行情）— v6.0 使用名称查找
 * @param {number} price - 实时价格
 * @param {number} high - 实时最高价
 * @param {number} low - 实时最低价
 * @param {number} volume - 实时成交量
 */
function updateLastCandle(price, high, low, volume) {
  // v10.1: KLineChart Pro 激活时，实时刷新走 pro 引擎
  if (window._proChartDisabled !== true && proChart && proLastBar) {
    try {
      var _h = (high > 0) ? Math.max(high, proLastBar.high) : proLastBar.high;
      var _l = (low > 0) ? Math.min(low, proLastBar.low) : proLastBar.low;
      var _c = (price > 0) ? +price : proLastBar.close;
      var _v = (volume > 0) ? +volume : proLastBar.volume;
      var _bar = { timestamp: proLastBar.timestamp, open: proLastBar.open, high: _h, low: _l, close: _c, volume: _v };
      if (typeof proRealtimeCb === 'function') { proRealtimeCb(_bar); }
      proLastBar = _bar;
      return;
    } catch (e) {
      console.error('[DAPENG] KLineChart Pro 实时刷新失败:', e);
    }
  }
  if (!klineChart || klineChart.isDisposed() || chartType !== 'kline') return;

  var option = klineChart.getOption();
  if (!option || !option.series || !option.series.length) return;

  // 查找 K线 series 索引
  var kIdx = -1, volIdx = -1;
  for (var i = 0; i < option.series.length; i++) {
    if (option.series[i].name === 'K线') kIdx = i;
    if (option.series[i].name === '成交量') volIdx = i;
  }
  if (kIdx < 0) return;

  var kData = option.series[kIdx].data;
  if (!kData || !kData.length) return;

  var lastIdx = kData.length - 1;
  var last = kData[lastIdx];
  if (!last || !Array.isArray(last) || last.length < 4) return;

  // 更新价
  if (price > 0) last[1] = +price.toFixed(2);
  if (high  > 0) last[3] = +high.toFixed(2);
  if (low   > 0) last[2] = +low.toFixed(2);

  // 涨跌颜色（v18: 涨=红 / 跌=绿）
  var isUp = last[1] >= last[0];
  last.itemStyle = { color: isUp ? _themeUp : _themeDown, borderColor: isUp ? _themeUp : _themeDown };

  // 更新成交量柱
  if (volume > 0 && volIdx >= 0 && option.series[volIdx].data && option.series[volIdx].data.length > 0) {
    var volSeries = option.series[volIdx];
    if (volSeries && volSeries.data && volSeries.data.length > 0) {
      var lastVol = volSeries.data[lastIdx];
      if (lastVol && typeof lastVol === 'object') {
        lastVol.value = volume;
        lastVol.itemStyle = {
          borderWidth: 0.5,
          borderColor: isUp ? _hexToRgba(_themeUp, 0.3) : _hexToRgba(_themeDown, 0.3),
          color: isUp
            ? new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: _hexToRgba(_themeUp, 0.7) }, { offset: 1, color: _hexToRgba(_themeUp, 0.12) }])
            : new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: _hexToRgba(_themeDown, 0.7) }, { offset: 1, color: _hexToRgba(_themeDown, 0.12) }]),
        };
      }
    }
  }

  klineChart.setOption(option, { notMerge: false, lazyUpdate: true, silent: true });
}

window.updateLastCandle = updateLastCandle;

/**
 * v6.0: 切换技术指标可见性
 * @param {string} key - 指标键名（'MA5','MA10','MA20','MA60','BOLL','MACD','KDJ','RSI','SupportResistance'）
 */
window.toggleIndicator = function(key) {
  var cfg = window._indicatorConfig;
  if (!cfg) return;
  if (key === 'VOLUME') { cfg.showVolume = !cfg.showVolume; }
  else if (key === 'MACD') { cfg.showMACD = !cfg.showMACD; }
  else if (key === 'KDJ') { cfg.showKDJ = !cfg.showKDJ; }
  else if (key === 'RSI') { cfg.showRSI = !cfg.showRSI; }
  else if (key === 'BOLL') { cfg.showBOLL = !cfg.showBOLL; }
  else if (key === 'SupportResistance') { cfg.showSupportResistance = !cfg.showSupportResistance; }
  else if (key === 'MA5') { cfg.showMA5 = !cfg.showMA5; }
  else if (key === 'MA10') { cfg.showMA10 = !cfg.showMA10; }
  else if (key === 'MA20') { cfg.showMA20 = !cfg.showMA20; }
  else if (key === 'MA60') { cfg.showMA60 = !cfg.showMA60; }
  updateIndicatorButtonsUI();
  // 重新渲染
  var code = window.appState && window.appState.activeCode;
  var cacheKey = code ? code + ':' + (window.appState.period || 'day') : null;
  if (cacheKey && window.appState && window.appState.klineCache) {
    var cachedData = window.appState.klineCache[cacheKey];
    if (cachedData) renderKline(cachedData);
  }
};

/**
 * v6.0: 切换副图指标类型
 */
window.setSubIndicator = function(type) {
  if (!window._indicatorConfig) return;
  var name = _dpNormalizeName(type);
  var isExtra = _DP_EXTRA_NAMES.indexOf(name) >= 0;
  if (!isExtra && ['macd', 'kdj', 'rsi'].indexOf(name) < 0) return;
  window._indicatorConfig.subIndicator = name;
  // 互斥：副图只有一块 —— 选中谁就只显示谁
  window._indicatorConfig.showMACD = (name === 'macd');
  window._indicatorConfig.showKDJ  = (name === 'kdj');
  window._indicatorConfig.showRSI  = (name === 'rsi');
  updateIndicatorButtonsUI();
  var code = window.appState && window.appState.activeCode;
  var cacheKey = code ? code + ':' + (window.appState.period || 'day') : null;
  var cachedData = cacheKey ? window.appState.klineCache[cacheKey] : null;
  if (isExtra) {
    // 先按本地算法即时重绘（≤1 帧，切换无等待感），再由后端结果回填（权威值/参数标签）
    if (cachedData) renderKline(cachedData);
    _dpFetchIndicator(name, function () {
      var st = window.appState || {};
      var ck = st.activeCode ? (st.activeCode + ':' + (st.period || 'day')) : null;
      var data2 = ck && st.klineCache ? st.klineCache[ck] : null;
      if (data2 && _dpNormalizeName((window._indicatorConfig || {}).subIndicator) === name) renderKline(data2);
    });
    return;
  }
  if (cachedData) renderKline(cachedData);
};

/**
 * v16: 指标参数自定义 — 弹层开关 / 应用 / 重置
 */
var IND_PARAM_DEFAULTS = { macd: [12, 26, 9], kdj: [9, 3, 3], rsi: [14], boll: [20, 2] };

function _rerenderKlineWithCache() {
  var code = window.appState && window.appState.activeCode;
  var cacheKey = code ? code + ':' + (window.appState.period || 'day') : null;
  var cachedData = cacheKey && window.appState && window.appState.klineCache ? window.appState.klineCache[cacheKey] : null;
  if (cachedData) renderKline(cachedData);
}

function _syncParamsToInputs() {
  var p = (window._indicatorConfig && window._indicatorConfig.params) || IND_PARAM_DEFAULTS;
  var set = function (id, v) { var el = document.getElementById(id); if (el) el.value = v; };
  set('ipp-macd-fast', p.macd[0]); set('ipp-macd-slow', p.macd[1]); set('ipp-macd-signal', p.macd[2]);
  set('ipp-kdj-n', p.kdj[0]); set('ipp-kdj-m1', p.kdj[1]); set('ipp-kdj-m2', p.kdj[2]);
  set('ipp-rsi-period', p.rsi[0]);
  set('ipp-boll-period', p.boll[0]); set('ipp-boll-mult', p.boll[1]);
}

window.toggleIndicatorParams = function () {
  var pop = document.getElementById('indicator-params-pop');
  if (!pop) return;
  pop.hidden = !pop.hidden;
  if (!pop.hidden) { _syncParamsToInputs(); _dpRenderParamRows(); }
  var btn = document.getElementById('ibt-params');
  if (btn) btn.classList.toggle('active', !pop.hidden);
};

function _readInt(id, fallback, min, max) {
  var el = document.getElementById(id);
  var v = el ? parseInt(el.value, 10) : NaN;
  if (!Number.isFinite(v)) return fallback;
  return Math.max(min, Math.min(max, v));
}

function _readNum(id, fallback, min, max) {
  var el = document.getElementById(id);
  var v = el ? parseFloat(el.value) : NaN;
  if (!Number.isFinite(v)) return fallback;
  return Math.max(min, Math.min(max, v));
}

window.applyIndicatorParams = function () {
  var d = IND_PARAM_DEFAULTS;
  var macdFast = _readInt('ipp-macd-fast', d.macd[0], 2, 60);
  var macdSlow = _readInt('ipp-macd-slow', d.macd[1], 5, 120);
  var macdSignal = _readInt('ipp-macd-signal', d.macd[2], 2, 60);
  if (macdSlow <= macdFast) macdSlow = macdFast + 1;
  var params = {
    macd: [macdFast, macdSlow, macdSignal],
    kdj: [_readInt('ipp-kdj-n', d.kdj[0], 2, 60), _readInt('ipp-kdj-m1', d.kdj[1], 1, 30), _readInt('ipp-kdj-m2', d.kdj[2], 1, 30)],
    rsi: [_readInt('ipp-rsi-period', d.rsi[0], 2, 60)],
    boll: [_readInt('ipp-boll-period', d.boll[0], 5, 120), _readNum('ipp-boll-mult', d.boll[1], 0.5, 4)]
  };
  window._indicatorConfig.params = params;
  try { localStorage.setItem('dapeng_indicator_params', JSON.stringify(params)); } catch (e) {}
  _syncParamsToInputs();
  _rerenderKlineWithCache();
  showToast('指标参数', '已应用：MACD(' + params.macd.join(',') + ') KDJ(' + params.kdj.join(',') + ') RSI(' + params.rsi[0] + ') BOLL(' + params.boll.join(',') + ')');
};

/**
 * v35: 渲染 12 类扩展指标的参数行（schema 来自 /api/kline/indicators/catalog）
 * 容器：#ipp-dp-rows（dashboard.html 中已有占位）
 */
function _dpRenderParamRows() {
  var box = document.getElementById('ipp-dp-rows');
  if (!box) return;
  var html = '';
  _DP_EXTRA_NAMES.forEach(function (name) {
    var meta = _dpMeta(name) || {};
    var schema = meta.params || {};
    var keys = Object.keys(schema);
    html += '<div class="ipp-row" data-dp-row="' + name + '"><span class="ipp-name">' + (meta.label || name) + '</span>';
    if (!keys.length) {
      html += '<span class="ipp-hint">无参数</span>';
    } else {
      keys.forEach(function (k) {
        var sp = schema[k] || {};
        var v = (_dpParams(name)[k] != null) ? _dpParams(name)[k] : sp.default;
        html += '<label>' + (sp.desc || k) +
          '<input data-dp-param="' + name + '.' + k + '" type="number" value="' + v + '"' +
          ' min="' + (sp.min != null ? sp.min : '') + '" max="' + (sp.max != null ? sp.max : '') + '"' +
          ' step="' + (sp.type === 'float' ? '0.5' : '1') + '"' +
          ' title="' + (sp.desc || k) + (sp.unit ? '（' + sp.unit + '）' : '') + '"></label>';
      });
    }
    html += '</div>';
  });
  box.innerHTML = html;
}

/** v35: 应用扩展指标参数（越界自动夹取；参数变化会清该指标的接口缓存并重取） */
window.applyDpIndicatorParams = function () {
  var box = document.getElementById('ipp-dp-rows');
  var changed = {};
  if (box) {
    var inputs = box.querySelectorAll('[data-dp-param]');
    for (var i = 0; i < inputs.length; i++) {
      var el = inputs[i];
      var key = el.getAttribute('data-dp-param');
      if (!key) continue;
      var parts = key.split('.');
      var name = _dpNormalizeName(parts[0]), pname = parts[1];
      var meta = _dpMeta(name) || {};
      var sp = (meta.params || {})[pname] || {};
      var v = sp.type === 'float' ? parseFloat(el.value) : parseInt(el.value, 10);
      if (!isFinite(v)) continue;
      if (sp.min != null) v = Math.max(Number(sp.min), v);
      if (sp.max != null) v = Math.min(Number(sp.max), v);
      if (sp.type !== 'float') v = Math.round(v);
      window._dpInd.params[name] = window._dpInd.params[name] || {};
      window._dpInd.params[name][pname] = v;
      changed[name] = true;
    }
  }
  try { localStorage.setItem('dapeng_dp_indicator_params', JSON.stringify(window._dpInd.params)); } catch (e) {}
  // 参数变了 -> 该指标的接口缓存失效（下次切到它时按新参数请求）
  Object.keys(changed).forEach(function (name) {
    Object.keys(window._dpInd.apiCache).forEach(function (k) {
      if (k.split(':')[2] === name) delete window._dpInd.apiCache[k];
    });
  });
  _dpRenderParamRows();
  var active = _dpNormalizeName((window._indicatorConfig || {}).subIndicator);
  if (_DP_EXTRA_NAMES.indexOf(active) >= 0) {
    _rerenderKlineWithCache();
    _dpFetchIndicator(active, function () { _rerenderKlineWithCache(); });
  }
  var label = _dpLabel(active);
  showToast('指标参数', '已应用：' + label + (Object.keys(changed).length ? '' : '（无改动）'));
};

window.resetDpIndicatorParams = function () {
  window._dpInd.params = {};
  window._dpInd.apiCache = {};
  try { localStorage.removeItem('dapeng_dp_indicator_params'); } catch (e) {}
  _dpRenderParamRows();
  _rerenderKlineWithCache();
  showToast('指标参数', '12 类指标已重置为默认参数');
};

window.resetIndicatorParams = function () {
  window._indicatorConfig.params = JSON.parse(JSON.stringify(IND_PARAM_DEFAULTS));
  try { localStorage.removeItem('dapeng_indicator_params'); } catch (e) {}
  _syncParamsToInputs();
  _rerenderKlineWithCache();
  showToast('指标参数', '已重置为默认参数');
};

/**
 * v6.0: 更新指标按钮 UI 状态
 */
function updateIndicatorButtonsUI() {
  var cfg = window._indicatorConfig;
  if (!cfg) return;
  // v35: 12 类扩展指标按钮（data-sub-indicator）—— 与副图当前值比对高亮
  try {
    var activeName = _dpNormalizeName(cfg.subIndicator);
    var btns = document.querySelectorAll('[data-sub-indicator]');
    for (var bi = 0; bi < btns.length; bi++) {
      var b = btns[bi];
      var on = _dpNormalizeName(b.getAttribute('data-sub-indicator')) === activeName;
      b.classList.toggle('active', on);
      b.setAttribute('aria-pressed', String(on));
    }
  } catch (e) {}
  var mapping = {
    'ibt-volume': cfg.showVolume, 'ibt-ma5': cfg.showMA5, 'ibt-ma10': cfg.showMA10, 'ibt-ma20': cfg.showMA20, 'ibt-ma60': cfg.showMA60,
    'ibt-boll': cfg.showBOLL, 'ibt-macd': cfg.showMACD, 'ibt-kdj': cfg.showKDJ,
    'ibt-rsi': cfg.showRSI, 'ibt-sr': cfg.showSupportResistance,
  };
  var subMap = { macd: 'ibt-macd', kdj: 'ibt-kdj', rsi: 'ibt-rsi' };
  Object.keys(mapping).forEach(function(id) {
    var btn = document.getElementById(id);
    if (!btn) return;
    var isSub = (id === 'ibt-macd' || id === 'ibt-kdj' || id === 'ibt-rsi');
    var active = isSub ? subMap[cfg.subIndicator] === id : !!mapping[id];
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
  });
}

// v35: 恢复 12 类指标的本地参数 + 拉取指标目录（失败走静态兜底，不阻塞渲染）
(function () {
  try {
    var saved = JSON.parse(localStorage.getItem('dapeng_dp_indicator_params') || 'null');
    if (saved && typeof saved === 'object') window._dpInd.params = saved;
  } catch (e) { window._dpInd.params = {}; }
  var boot = function () {
    try { _dpApplyCatalogToButtons(); } catch (e) {}
    _dpLoadCatalog();
    // v37: 恢复 ICT 开关（刷新后保持上次选择；无记录 = 关）
    try {
      var _ictSaved = localStorage.getItem('dapeng_ict_on');
      if (_ictSaved === '1') {
        window._ict.on = true;
        var b2 = document.getElementById('ibt-ict');
        if (b2) { b2.classList.add('active'); b2.setAttribute('aria-pressed', 'true'); }
        // 首次渲染钩子会调 _ictEnsure 取数；这里再兜一次（图表可能已渲染完成）
        setTimeout(function () { try { if (_ictActive()) _ictEnsure(); } catch (e) {} }, 1200);
      }
    } catch (e) {}
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();

// 保留单一权威实现：全局 showToast 由 app.js 定义并 patch（路由到 enhancedToast）。
// chart.js 在 dashboard.html 中先于 app.js 加载，若此处用函数声明会（在解析期提升）无条件
// 覆盖全局 showToast；改为「仅在全局尚未定义时兜底」，保证 app.js 的实现始终生效，
// 同时避免面板重开后 toast 因两次定义相互覆盖而失效。
if (typeof window.showToast !== 'function') {
  window.showToast = function showToast(title, body) {
    try {
      if (typeof window.showAppToast === 'function') { window.showAppToast(title, body); return; }
      var el = document.getElementById('signal-toast');
      if (!el) { console.log('[toast]', title, body); return; }
      var titleEl = document.getElementById('toast-title');
      var bodyEl = document.getElementById('toast-body');
      if (titleEl) titleEl.textContent = title;
      if (bodyEl) bodyEl.textContent = body || '';
      el.classList.remove('show');
      void el.offsetWidth;
      el.classList.add('show');
      clearTimeout(window.__chartToastTimer);
      window.__chartToastTimer = setTimeout(function() { el.classList.remove('show'); }, 4000);
    } catch (e) { console.log('[toast]', title, body); }
  };
}

// ================================================================
//  KLineChart Pro 引擎（v10.1）
//  GitHub 开源 klinecharts v10.0.3（Apache-2.0，本地 vendor 加载）
//  renderKline 顶部分发器优先走此引擎；任何异常回退 ECharts
// ================================================================
var proChart = null;
var proLastBar = null;
var proRealtimeCb = null;
var proInited = false;
var proSymbol = '';
var proPeriodKey = '';
var proDataStore = { bars: [] };

/**
 * 动态计算 Pro 容器高度 = 卡片可用高度 - 兄弟元素（头部/指标栏/Tab条）
 * #kline-overlay 默认被卡片 overflow 裁剪（Tab 激活时由 app.js 接管），不参与预算
 * 普通模式与全屏模式自动适配
 */
function sizeProToCard() {
  var pro = document.getElementById('kline-chart-pro');
  var legacy = document.getElementById('kline-chart');
  if (!pro || !pro.parentNode) return;
  var parent = pro.parentNode;
  var card = document.getElementById('kline-card');
  var isFs = card && (document.fullscreenElement === card || card.classList.contains('kline-fullscreen-fallback'));
  // 流动布局（页面自然高度、卡片高度由内容驱动）：此时 parent.clientHeight 会随图表高度
  // 变化而自引用发散，改用固定高度档位。阈值须与 ui-refinement.css 的流动断点一致（≤900px），
  // 否则 768–900px 区间会走桌面分支 → 自引用 → 图表高度爆炸。
  if (!isFs && window.matchMedia && window.matchMedia('(max-width: 900px)').matches) {
    // 与 legacy 移动端档位一致（ui-refinement ≤720px: min-height:300px!important；横屏矮屏 200px）
    var mh = window.innerHeight <= 500 ? 200 : 300;
    pro.style.height = mh + 'px';
    return;
  }
  var avail = parent.clientHeight;
  var others = 0;
  var kids = parent.children;
  for (var i = 0; i < kids.length; i++) {
    var k = kids[i];
    if (k === pro || k === legacy) continue;
    if (k.id === 'kline-overlay') continue;
    var cs = getComputedStyle(k);
    if (cs.display === 'none' || cs.position === 'absolute' || cs.position === 'fixed') continue;
    others += k.getBoundingClientRect().height;
  }
  // K 线实体高度下限（v19）：不再允许被压到 120px。卡片 min-height 保障不足时由
  // #right-panel 滚动兜底；此处仅作最后防线，避免图表被压扁到不可用。
  var h = Math.max(240, Math.floor(avail - others - 4));
  pro.style.height = h + 'px';
  // 自校正：确保 #kline-tabs 完整落在卡片内（补偿 flex gap/边距）
  var tabs = document.getElementById('kline-tabs');
  if (tabs && tabs.parentNode === parent) {
    var over = tabs.getBoundingClientRect().bottom - parent.getBoundingClientRect().bottom;
    if (over > 0) {
      pro.style.height = Math.max(240, h - over - 4) + 'px';
    }
  }
}

/**
 * 创建/复用 Pro 容器（与 #kline-chart 同级），隐藏 legacy 容器
 */
function ensureProContainer() {
  var legacy = document.getElementById('kline-chart');
  if (!legacy || !legacy.parentNode) return null;
  var pro = document.getElementById('kline-chart-pro');
  if (!pro) {
    pro = document.createElement('div');
    pro.id = 'kline-chart-pro';
    // flex:0 0 auto 禁止收缩，高度由 sizeProToCard 精确分配（卡片网格高度固定，强撑会被裁剪）
    pro.style.cssText = 'width:100%;flex:0 0 auto;min-height:0;border-radius:8px;display:block;position:relative;';
    legacy.parentNode.insertBefore(pro, legacy.nextSibling);
  }
  legacy.style.display = 'none';
  pro.style.display = 'block';
  sizeProToCard();
  return pro;
}

/**
 * 恢复 legacy ECharts 容器（兜底路径 / 分时图使用）
 */
function restoreLegacyKlineContainer() {
  var legacy = document.getElementById('kline-chart');
  var pro = document.getElementById('kline-chart-pro');
  if (pro) pro.style.display = 'none';
  if (legacy) legacy.style.display = '';
  if (klineChart && !klineChart.isDisposed()) { try { klineChart.resize(); } catch (e) {} }
}

/**
 * 时间戳解析：兼容 '2026-04-09'、'2026-08-25 10:30'、紧凑 '202608191030'(12位)、'20260409'(8位)
 */
function parseProTimestamp(d) {
  var s = String(d);
  if (/^\d{12}$/.test(s)) {
    return new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8), +s.slice(8, 10), +s.slice(10, 12)).getTime();
  }
  if (/^\d{8}$/.test(s)) {
    return new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8)).getTime();
  }
  return new Date(s.replace(' ', 'T')).getTime();
}

/**
 * API 数据 [{d,o,c,h,l,v}] → KLineChart [{timestamp,open,high,low,close,volume}]
 */
function convertToProBars(klines) {
  var bars = [];
  if (!klines || !klines.length) return bars;
  for (var i = 0; i < klines.length; i++) {
    var k = klines[i];
    if (!k || k.d == null) continue;
    var ts = parseProTimestamp(k.d);
    if (!isFinite(ts)) continue;
    bars.push({
      timestamp: ts,
      open: +k.o, high: +k.h, low: +k.l, close: +k.c,
      volume: isFinite(+k.v) ? +k.v : 0
    });
  }
  bars.sort(function (a, b) { return a.timestamp - b.timestamp; });
  return bars;
}

/**
 * 面板周期 → KLineChart Period 对象
 */
function mapProPeriod(period) {
  var p = String(period || 'day');
  if (p === 'week') return { span: 1, type: 'week' };
  if (p === 'month') return { span: 1, type: 'month' };
  // 分钟周期：本项目按钮值为 '60'/'30'/'15'/'5'，兼容 '60m' 形式
  var m = p.match(/^(\d+)m?$/);
  if (m) return { span: parseInt(m[1], 10), type: 'minute' };
  return { span: 1, type: 'day' };
}

/**
 * 暗色主题样式（v18: 涨=红 / 跌=绿，与 ECharts 版及全站 .up/.dn 一致）
 */
function applyProStyles() {
  if (!proChart) return;
  refreshThemeColors();
  proChart.setStyles({
    grid: {
      show: true,
      horizontal: { show: true, style: 'dashed', dashedValue: [3, 5], size: 1, color: 'rgba(148,163,184,0.14)' },
      vertical: { show: true, style: 'dashed', dashedValue: [3, 5], size: 1, color: 'rgba(148,163,184,0.08)' }
    },
    candle: {
      type: 'candle_solid',
      bar: {
        compareRule: 'current_open',
        upColor: _themeUp, downColor: _themeDown, noChangeColor: '#8b9ec2',
        upBorderColor: _lightenHex(_themeUp, 0.25), downBorderColor: _lightenHex(_themeDown, 0.25), noChangeBorderColor: '#8b9ec2',
        upWickColor: _hexToRgba(_themeUp, 0.85), downWickColor: _hexToRgba(_themeDown, 0.85), noChangeWickColor: '#8b9ec2'
      },
      priceMark: {
        show: true,
        high: { show: true, color: 'rgba(226,232,240,0.5)', textSize: 9, textMargin: 4 },
        low: { show: true, color: 'rgba(226,232,240,0.5)', textSize: 9, textMargin: 4 },
        last: {
          show: true,
          compareRule: 'current_open',
          upColor: _themeUp, downColor: _themeDown, noChangeColor: '#8b9ec2',
          line: { show: true, style: 'dashed', dashedValue: [3, 3], size: 1 },
          text: { show: true, style: 'fill', size: 10, color: '#04101f', paddingLeft: 5, paddingTop: 3, paddingRight: 5, paddingBottom: 3, borderRadius: 3, weight: 'bold' }
        }
      },
      tooltip: {
        showRule: 'always',
        showType: 'standard',
        labels: ['时间', '开', '收', '高', '低', '成交量'],
        text: { size: 11, family: 'Helvetica Neue', weight: 'normal', color: '#cbd5e1' },
        rect: { fillColor: 'rgba(6,12,28,0.94)', borderColor: 'rgba(59,130,246,0.3)', borderSize: 1, borderRadius: 7, paddingLeft: 9, paddingRight: 9, paddingTop: 8, paddingBottom: 8, offsetLeft: 8, offsetTop: 8, offsetRight: 8, offsetBottom: 8 }
      }
    },
    indicator: {
      // v18: 副图柱状指标（MACD 柱等）涨=红 / 跌=绿（深合并到默认 bars[0]）
      bars: [{ upColor: _themeUp, downColor: _themeDown, noChangeColor: '#8b9ec2' }]
    },
    xAxis: {
      axisLine: { show: true, color: 'rgba(148,163,184,0.2)', size: 1 },
      tickLine: { show: false },
      tickText: { show: true, color: 'rgba(203,213,225,0.62)', size: 10, family: 'SF Mono, Cascadia Code, monospace', weight: 'normal' }
    },
    yAxis: {
      axisLine: { show: false, color: 'rgba(148,163,184,0.2)', size: 1 },
      tickLine: { show: false },
      tickText: { show: true, color: 'rgba(203,213,225,0.62)', size: 10, family: 'SF Mono, Cascadia Code, monospace', weight: 'normal' }
    },
    separator: { size: 1, color: 'rgba(148,163,184,0.16)', fill: true, activeBackgroundColor: 'rgba(59,130,246,0.1)' },
    crosshair: {
      show: true,
      horizontal: {
        show: true,
        line: { show: true, style: 'dashed', dashedValue: [4, 4], size: 1, color: 'rgba(226,232,240,0.5)' },
        text: { show: true, style: 'fill', size: 10, color: '#04101f', backgroundColor: 'rgba(125,211,252,0.92)', paddingLeft: 5, paddingRight: 5, paddingTop: 3, paddingBottom: 3, borderRadius: 3, weight: 'bold' }
      },
      vertical: {
        show: true,
        line: { show: true, style: 'dashed', dashedValue: [4, 4], size: 1, color: 'rgba(226,232,240,0.5)' },
        text: { show: true, style: 'fill', size: 10, color: '#04101f', backgroundColor: 'rgba(125,211,252,0.92)', paddingLeft: 5, paddingRight: 5, paddingTop: 3, paddingBottom: 3, borderRadius: 3, weight: 'bold' }
      }
    }
  });
}

/**
 * 初始化 Pro 引擎（v10 DataLoader 模式）
 */
function initProEngine(dom) {
  proChart = klinecharts.init(dom);
  if (!proChart) throw new Error('klinecharts.init returned null');
  try { proChart.setLocale('zh-CN'); } catch (e) {}
  applyProStyles();
  proChart.setDataLoader({
    getBars: function (params) {
      if (!params || typeof params.callback !== 'function') return;
      if (params.type === 'init') {
        params.callback((proDataStore.bars || []).slice(), { backward: false, forward: false });
      } else {
        // 不提供历史分页加载
        params.callback([], { backward: false, forward: false });
      }
    },
    subscribeBar: function (params) { proRealtimeCb = params.callback; },
    unsubscribeBar: function () { proRealtimeCb = null; }
  });
}

/**
 * 检查某指标是否已存在（防御性：即使 getIndicators 忽略 filter 参数也正确）
 */
function proHasIndicator(name) {
  if (!proChart || !proChart.getIndicators) return false;
  var list = [];
  try { list = proChart.getIndicators({ name: name }) || []; } catch (e) { try { list = proChart.getIndicators() || []; } catch (e2) { list = []; } }
  for (var i = 0; i < list.length; i++) {
    if (list[i] && list[i].name === name) return true;
  }
  return false;
}

/**
 * 按 window._indicatorConfig 对齐 Pro 引擎指标（每次渲染 reconcile）
 */
function syncProIndicators() {
  if (!proChart) return;
  var cfg = window._indicatorConfig || {};
  var CANDLE_PANE = 'candle_pane';

  // 主图 MA（颜色与 ECharts 版一致）
  var maDefs = [
    [5, '#f59e0b', !!cfg.showMA5],
    [10, '#3b82f6', !!cfg.showMA10],
    [20, '#a855f7', !!cfg.showMA20],
    [60, 'rgba(255,61,106,0.5)', !!cfg.showMA60]
  ];
  var maParams = [], maLines = [];
  for (var i = 0; i < maDefs.length; i++) {
    if (maDefs[i][2]) { maParams.push(maDefs[i][0]); maLines.push({ color: maDefs[i][1], size: 1.2 }); }
  }
  var hasMA = proHasIndicator('MA');
  if (maParams.length) {
    if (hasMA) {
      proChart.overrideIndicator({ paneId: CANDLE_PANE, name: 'MA', calcParams: maParams, styles: { lines: maLines } });
    } else {
      proChart.createIndicator({ name: 'MA', calcParams: maParams, paneId: CANDLE_PANE, styles: { lines: maLines } }, true);
    }
  } else if (hasMA) {
    proChart.removeIndicator({ paneId: CANDLE_PANE, name: 'MA' });
  }

  // 主图 BOLL（v16: 参数可自定义）
  var bollCalcParams = (cfg.params && cfg.params.boll) || [20, 2];
  var hasBoll = proHasIndicator('BOLL');
  if (cfg.showBOLL) {
    if (hasBoll) {
      proChart.overrideIndicator({ paneId: CANDLE_PANE, name: 'BOLL', calcParams: bollCalcParams });
    } else {
      proChart.createIndicator({ name: 'BOLL', calcParams: bollCalcParams, paneId: CANDLE_PANE }, true);
    }
  } else if (!cfg.showBOLL && hasBoll) {
    proChart.removeIndicator({ paneId: CANDLE_PANE, name: 'BOLL' });
  }

  // VOL 独立副图
  var hasVol = proHasIndicator('VOL');
  if (cfg.showVolume && !hasVol) {
    proChart.createIndicator({
      name: 'VOL', calcParams: [5, 10],
      styles: { bars: [{ upColor: _hexToRgba(_themeUp, 0.75), downColor: _hexToRgba(_themeDown, 0.75), noChangeColor: 'rgba(139,158,194,0.5)' }] }
    }, false);
  } else if (!cfg.showVolume && hasVol) {
    proChart.removeIndicator({ name: 'VOL' });
  }

  // 振荡副图三选一（MACD/KDJ/RSI，v16: 参数可自定义并热更新）
  var subMap = { macd: 'MACD', kdj: 'KDJ', rsi: 'RSI' };
  var want = subMap[cfg.subIndicator] || 'MACD';
  var subParamMap = {
    MACD: (cfg.params && cfg.params.macd) || [12, 26, 9],
    KDJ: (cfg.params && cfg.params.kdj) || [9, 3, 3],
    RSI: (cfg.params && cfg.params.rsi) || [14]
  };
  var oscNames = ['MACD', 'KDJ', 'RSI'];
  var wantExtra = _DP_EXTRA_NAMES.indexOf(_dpNormalizeName(cfg.subIndicator)) >= 0;
  for (var j = 0; j < oscNames.length; j++) {
    var n = oscNames[j];
    var has = proHasIndicator(n);
    if (n === want && !wantExtra) {      // v35: 扩展指标生效时内置副图让位
      if (has) {
        proChart.overrideIndicator({ name: n, calcParams: subParamMap[n] });
      } else {
        proChart.createIndicator({ name: n, calcParams: subParamMap[n] }, false);
      }
    } else if (has) {
      proChart.removeIndicator({ name: n });
    }
  }
  // v35: 12 类扩展指标（overlay 走 candle_pane，其余走独立副图）
  try { _dpSyncProIndicator(); } catch (e) { console.error('[DAPENG] 扩展指标同步失败:', e); }
}

/**
 * 显式分配 pane 高度：主图吃剩余空间，副图各 ~19%（默认布局会把主图压成与副图等高）
 */
function applyProPaneHeights() {
  if (!proChart || !proChart.setPaneOptions) return;
  try {
    var pro = document.getElementById('kline-chart-pro');
    var total = pro ? pro.clientHeight : 0;
    if (total < 60) total = 320; // 仅防止极端异常（容器未布局完成），不强行拔高
    var subPaneIds = [];
    var names = ['VOL', 'MACD', 'KDJ', 'RSI'];
    var inds = [];
    try { inds = proChart.getIndicators() || []; } catch (e) { inds = []; }
    for (var i = 0; i < inds.length; i++) {
      if (!inds[i] || !inds[i].paneId || inds[i].paneId === 'candle_pane') continue;
      // v35: 'DP_*' 为 12 类扩展指标，同样参与副图高度分配
      var known = names.indexOf(inds[i].name) >= 0 || String(inds[i].name || '').indexOf('DP_') === 0;
      if (known && subPaneIds.indexOf(inds[i].paneId) < 0) {
        subPaneIds.push(inds[i].paneId);
      }
    }
    // 面板高度分配：主图优先（K线可读性 > 副图），副图按可用高度自适应并设上下限
    var xAxisH = 20, sepH = 9;
    var avail = Math.max(0, total - xAxisH);
    var subCount = subPaneIds.length;
    var subH = 0;
    if (subCount) {
      // 副图最多占 40% 可用高度，且限制在 30-90px（保证 MACD 柱+双线可辨）
      var maxSubTotal = Math.round(avail * 0.4);
      subH = Math.min(90, Math.max(30, Math.floor(maxSubTotal / subCount) - sepH));
    }
    var candleH = total - xAxisH - (subH + sepH) * subCount;
    proChart.setPaneOptions({ id: 'candle_pane', height: Math.max(70, candleH) });
    for (var j = 0; j < subPaneIds.length; j++) {
      proChart.setPaneOptions({ id: subPaneIds[j], height: subH });
    }
  } catch (e) {}
}

/**
 * Pro 引擎主入口（由 renderKline 分发器调用）
 */
function renderKlinePro(data) {
  var dom = ensureProContainer();
  if (!dom) throw new Error('pro container unavailable');
  if (!proChart) initProEngine(dom);

  var kls = (data && data.klines) || [];
  // v36: 同 renderKline —— 记录图上真实渲染的上下文
  _dpRenderCtx = {
    code: (data && data.code) || (window.appState && window.appState.activeCode) || '',
    period: (data && data.period) || (window.appState && window.appState.period) || 'day',
    klines: kls
  };
  var bars = convertToProBars(kls);
  proDataStore.bars = bars;
  proLastBar = bars.length ? bars[bars.length - 1] : null;

  var code = (data && data.code) || 'unknown';
  var pKey = (data && data.period) || (window.appState && window.appState.period) || 'day';

  if (!proInited) {
    proChart.setPeriod(mapProPeriod(pKey));
    proChart.setSymbol({ ticker: code, pricePrecision: 2, volumePrecision: 0 });
    proInited = true;
    proSymbol = code;
    proPeriodKey = pKey;
  } else {
    if (pKey !== proPeriodKey) { proChart.setPeriod(mapProPeriod(pKey)); proPeriodKey = pKey; }
    if (code !== proSymbol) { proChart.setSymbol({ ticker: code, pricePrecision: 2, volumePrecision: 0 }); proSymbol = code; }
    proChart.resetData();
  }
  syncProIndicators();
  applyProPaneHeights();
  // v36: ICT 结构标注（overlay 绘制，天然不进主图刻度）
  try { if (_ictActive()) { _ictRenderPro(); _ictEnsure(); } else { _ictClearPro(); } } catch (e) { console.error('[DAPENG] ICT 渲染失败:', e); }
  // v17: K线全屏买卖点标注联动（数据/状态栏/图例由 kfs 模块维护）
  if (typeof kfsAfterRenderKline === 'function') kfsAfterRenderKline(data);
}

window.renderKlinePro = renderKlinePro;

// ================================================================
//  K 线全屏买卖点标注（v17 — M6 全屏展示细节）
//  ---------------------------------------------------------------
//  数据来源（真实优先，失败如实降级，绝不编造）：
//    1) GET /api/review/round-trips  闭环交易（pnl 为净口径，契约优先）
//    2) GET /api/automation/orders   真实成交流水（round-trips 不可用时回退，
//       展示真实买卖价/数量；净盈亏字段缺失时如实标注"接口未就绪"，不推算）
//  渲染：klinecharts 自定义 overlay 'dapengSignal'（B 买 / S 卖 / 演练灰虚线）
//  归属：本区块全部 DOM 由本文件动态创建；样式见 css/kline-fullscreen.css
//  仅在"全屏激活"时把标注绘制到图上（普通视图保持原状，避免干扰）
// ================================================================

// 数据性质徽章 —— 沿用 market-dashboard.js 同一套四级定义（label/tip 文案完全一致）
var KLINE_NATURE_META = {
  real: { label: '真实', tip: '数据性质：真实 —— 实时直采，未经二次加工。' },
  snapshot: { label: '历史快照', tip: '数据性质：历史快照 —— 上游停更或本次取数失败，沿用上一次成功结果。' },
  aggregated: { label: '聚合口径', tip: '数据性质：聚合口径 —— 由第三方行情或系统样本池聚合推导，非全市场逐笔直采。' },
  local: { label: '本地样本', tip: '数据性质：本地样本 —— 浏览器本地存储 / 本地样本兜底数据。' }
};

var KLINE_PERIOD_LABEL = { day: '日K', week: '周K', month: '月K', '60': '60分', '30': '30分', '15': '15分', '5': '5分' };
var KLINE_EXIT_LABEL = { take_profit: '止盈', trailing_stop: '移动止损', fixed_stop: '固定止损', signal: '信号平仓' };

var _klineFsMeta = null;   // 当前 K 线元信息 { code, name, period, source, lastTs, bars }
var _klineSignal = {
  code: '',
  source: '',        // '' | 'round_trips' | 'orders'
  status: 'idle',    // idle | loading | ready | empty | empty_orders | not_ready | unsupported
  points: [],        // 归一化买卖点
  note: '',
  unmatched: [],     // 时间戳无对应 bar 的记录
  matchedCount: 0,
  includeDrill: false,
  fetchedAt: 0
};
var _klineSignalSeq = 0;
var _dapengSignalRegistered = false;
var _klineSignalTipBoundFor = null;

// ---------------- 小工具 ----------------
function _kfsEsc(text) {
  if (text == null) return '';
  return String(text).replace(/[<>&"']/g, function (c) {
    return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function _kfsNum(v) { var n = Number(v); return Number.isFinite(n) ? n : 0; }
function _kfsInt(v) { var n = parseInt(v, 10); return Number.isFinite(n) ? n : 0; }
function _kfsPrice(v) { return Number(_kfsNum(v)).toFixed(2); }
function _kfsQty(v) { return _kfsInt(v).toLocaleString('zh-CN'); }
function _kfsPad2(n) { return (n < 10 ? '0' : '') + n; }
/** bar 时间戳（ms）→ 本地日期 YYYY-MM-DD（与 bar 的 d 字段一致） */
function _kfsDateStr(ts) {
  var d = new Date(Number(ts));
  if (isNaN(d.getTime())) return '';
  return d.getFullYear() + '-' + _kfsPad2(d.getMonth() + 1) + '-' + _kfsPad2(d.getDate());
}
/** 交易时间戳 YYYY-MM-DDTHH:MM:SS → bar 日期 YYYY-MM-DD（对齐用） */
function _kfsDateFromTs(ts) {
  if (!ts) return '';
  var m = String(ts).match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : '';
}
function _kfsFmtTs(ts) {
  if (!ts) return '—';
  var s = String(ts).replace('T', ' ');
  return s.length > 16 ? s.slice(0, 16) : s;
}
function _kfsNatureBadge(nature) {
  var meta = KLINE_NATURE_META[nature] || KLINE_NATURE_META.aggregated;
  return '<span class="mn-nature-badge" data-nature="' + nature + '" title="' + _kfsEsc(meta.tip) + '">' + meta.label + '</span>';
}
function _kfsExitLabel(r) { return r ? (KLINE_EXIT_LABEL[r] || String(r)) : '未标注'; }
function _kfsCard() { return document.getElementById('kline-card'); }
function _kfsIsFullscreen() {
  var card = _kfsCard();
  return !!card && card.classList.contains('kline-fullscreen-active');
}

// ---------------- DOM 动态创建 ----------------
function ensureKlineFsUI() {
  var card = _kfsCard();
  if (!card) return;
  if (!document.getElementById('kline-fs-status')) {
    var status = document.createElement('div');
    status.id = 'kline-fs-status';
    status.className = 'kfs-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    card.insertBefore(status, card.firstChild);
  }
  if (!document.getElementById('kline-signal-legend')) {
    var legend = document.createElement('div');
    legend.id = 'kline-signal-legend';
    legend.className = 'kline-signal-legend';
    var tabs = document.getElementById('kline-tabs');
    if (tabs && tabs.parentNode === card) card.insertBefore(legend, tabs);
    else card.appendChild(legend);
  }
  if (!document.getElementById('kline-signal-tip')) {
    var tip = document.createElement('div');
    tip.id = 'kline-signal-tip';
    tip.className = 'kline-signal-tip';
    tip.hidden = true;
    card.appendChild(tip);
  }
  // v20: 价格口径徽章（附加在 K 线头部动作区，flex 行内安全）
  if (!document.getElementById('kline-adjust-badge')) {
    var actions = card.querySelector('.kline-chart-actions');
    if (actions) {
      var adj = document.createElement('span');
      adj.id = 'kline-adjust-badge';
      adj.className = 'kline-adjust-badge';
      adj.textContent = '—';
      adj.title = 'K线价格口径';
      actions.appendChild(adj);
    }
  }
  // v20: 数据可用性提示（空数据 / 指标不足时显示，避免只留空图）
  if (!document.getElementById('kline-data-notice')) {
    var notice = document.createElement('div');
    notice.id = 'kline-data-notice';
    notice.className = 'kline-data-notice';
    notice.hidden = true;
    var tabsEl = document.getElementById('kline-tabs');
    if (tabsEl && tabsEl.parentNode === card) card.insertBefore(notice, tabsEl);
    else card.appendChild(notice);
  }
}

// ---------------- 状态栏 ----------------
function updateKlineFsStatus() {
  var el = document.getElementById('kline-fs-status');
  if (!el) return;
  var m = _klineFsMeta;
  if (!m) { el.innerHTML = '<span class="kfs-code">--</span>'; return; }
  var period = KLINE_PERIOD_LABEL[m.period] || m.period || '—';
  var st = _klineSignal.status;
  var statusTxt;
  if (st === 'loading') statusTxt = '交易记录加载中…';
  else if (st === 'ready') statusTxt = (_klineSignal.source === 'orders')
    ? ('订单流 · ' + _klineSignal.points.length + ' 点（净盈亏暂缺）')
    : ('闭环交易 · ' + _klineSignal.points.length + ' 点');
  else if (st === 'empty') statusTxt = '暂无闭环交易';
  else if (st === 'empty_orders') statusTxt = '暂无成交记录';
  else if (st === 'not_ready') statusTxt = '闭环接口未就绪';
  else if (st === 'unsupported') statusTxt = '兼容模式';
  else statusTxt = '—';
  var un = _klineSignal.unmatched ? _klineSignal.unmatched.length : 0;
  var unCls = un > 0 ? 'kfs-unmatched has' : 'kfs-unmatched';
  var unTip = un > 0
    ? ('共 ' + un + ' 笔交易时间戳在 K 线中找不到对应 bar（如停牌/非交易日），未绘制以避免错位')
    : '交易时间戳与 K 线 bar 全部对齐';
  el.innerHTML =
    '<span class="kfs-code">' + _kfsEsc(m.code || '--') + '</span>' +
    '<span class="kfs-name">' + _kfsEsc(m.name || '') + '</span>' +
    '<span class="kfs-sep">·</span>' +
    '<span class="kfs-period">' + _kfsEsc(period) + '</span>' +
    '<span class="kfs-sep">·</span>' +
    '<span class="kfs-src">数据源 ' + _kfsEsc(m.source || '未知') + '</span>' +
    '<span class="kfs-sep">·</span>' +
    '<span class="kfs-time">数据时间 ' + _kfsEsc(m.lastTs || '未知') + '</span>' +
    '<span class="kfs-src-nature">' + _kfsNatureBadge('real') + '</span>' +
    '<span class="kfs-sep">·</span>' +
    '<span class="' + unCls + '" title="' + _kfsEsc(unTip) + '">未匹配记录 ' + un + ' 笔</span>' +
    '<span class="kfs-sep">·</span>' +
    '<span class="kfs-note">' + _kfsEsc(statusTxt) + '</span>' +
    '<button class="kfs-refresh" type="button" onclick="window.refreshKlineSignals()" title="重新拉取该标的交易记录">刷新交易记录</button>';
}

// ---------------- 图例 / 无数据态 ----------------
function updateKlineFsLegend() {
  var el = document.getElementById('kline-signal-legend');
  if (!el) return;
  var st = _klineSignal.status;
  var html = '<span class="ksl-title">交易标注</span>';
  if (_klineSignal.points.length) {
    html += '<span class="ksl-item"><i class="ksl-dot ksl-buy">B</i>买点（open_ts）</span>';
    html += '<span class="ksl-item"><i class="ksl-dot ksl-sell">S</i>卖点（close_ts · 净盈亏）</span>';
    html += '<span class="ksl-item"><i class="ksl-dot ksl-drill">B</i>演练造数（非真实信号交易）</span>';
  }
  if (st === 'loading') html += '<span class="ksl-note">交易记录加载中…</span>';
  else if (st === 'empty') html += '<span class="ksl-note">该标的暂无闭环交易记录（演练订单默认已排除）</span>';
  else if (st === 'empty_orders') html += '<span class="ksl-note">该标的暂无成交记录</span>';
  else if (st === 'unsupported') html += '<span class="ksl-note ksl-warn">当前为兼容渲染模式（ECharts），买卖点标注不可用</span>';
  if (_klineSignal.note) {
    html += '<span class="ksl-note ' + (st === 'not_ready' ? 'ksl-warn' : '') + '">' + _kfsEsc(_klineSignal.note) + '</span>';
  }
  if (_klineSignal.unmatched && _klineSignal.unmatched.length) {
    html += '<span class="ksl-note ksl-warn">未匹配记录 ' + _klineSignal.unmatched.length + ' 笔（时间戳无对应 K 线 bar，未标在图上）</span>';
  }
  html += '<button class="ksl-toggle" type="button" aria-pressed="' + (_klineSignal.includeDrill ? 'true' : 'false') +
    '" onclick="window.toggleKlineSignalDrill()" title="是否在图上叠加演练造数（is_drill=1）的成交点">' +
    (_klineSignal.includeDrill ? '隐藏演练单' : '显示演练单') + '</button>';
  el.innerHTML = html;
}

// ---------------- 数据抓取（真实优先，如实降级） ----------------
function _kfsFetchJson(url) {
  return fetch(url, { headers: { 'Content-Type': 'application/json' } })
    .then(function (res) {
      if (res.status === 404) return { ok: false, reason: 'not_ready', http: 404 };
      if (!res.ok) return { ok: false, reason: 'http_' + res.status, http: res.status };
      return res.json().then(function (json) {
        if (!json || json.ok !== true || !json.data) return { ok: false, reason: 'bad_payload' };
        return { ok: true, data: json.data };
      }).catch(function () { return { ok: false, reason: 'bad_json' }; });
    })
    .catch(function () { return { ok: false, reason: 'network' }; });
}

function _kfsFetchRoundTrips(code) {
  var url = '/api/review/round-trips?code=' + encodeURIComponent(code) +
    '&include_drill=' + (_klineSignal.includeDrill ? 'true' : 'false') + '&limit=500';
  return _kfsFetchJson(url).then(function (r) { if (r.ok) r.source = 'round_trips'; return r; });
}

function _kfsFetchOrders(code) {
  var url = '/api/automation/orders?code=' + encodeURIComponent(code) + '&limit=500';
  return _kfsFetchJson(url).then(function (r) { if (r.ok) r.source = 'orders'; return r; });
}

function _kfsSortPoints(arr) {
  return arr
    .filter(function (p) { return p && p.ts; })
    .sort(function (a, b) { return String(a.ts) < String(b.ts) ? -1 : (String(a.ts) > String(b.ts) ? 1 : 0); });
}

/** 闭环交易（/api/review/round-trips）→ 归一化买卖点 */
function _kfsNormalizeRoundTrips(data) {
  var items = (data && data.items) || [];
  var out = [];
  for (var i = 0; i < items.length; i++) {
    var it = items[i] || {};
    var drill = !!it.is_drill;
    if (it.open_ts) out.push({ kind: 'B', ts: String(it.open_ts), price: _kfsNum(it.buy_price), qty: _kfsInt(it.qty), pnl: null, exit_reason: null, is_drill: drill, strategy_id: it.strategy_id || '' });
    if (it.close_ts) out.push({ kind: 'S', ts: String(it.close_ts), price: _kfsNum(it.sell_price), qty: _kfsInt(it.qty), pnl: (it.pnl == null ? null : _kfsNum(it.pnl)), exit_reason: it.exit_reason || null, is_drill: drill, strategy_id: it.strategy_id || '' });
  }
  return _kfsSortPoints(out);
}

/** 真实成交流水（/api/automation/orders，仅 filled）→ 归一化买卖点（净盈亏不可得） */
function _kfsNormalizeOrders(data) {
  var items = (data && data.items) || [];
  var out = [];
  for (var i = 0; i < items.length; i++) {
    var o = items[i] || {};
    if (o.status !== 'filled') continue;
    var side = String(o.side || '').toLowerCase();
    if (side !== 'buy' && side !== 'sell') continue;
    var drill = !!o.is_drill;
    if (drill && !_klineSignal.includeDrill) continue;   // 默认排除演练单
    out.push({
      kind: side === 'buy' ? 'B' : 'S', ts: String(o.ts || ''),
      price: _kfsNum(o.price), qty: _kfsInt(o.qty), pnl: null,
      exit_reason: o.exit_reason || null, is_drill: drill, strategy_id: o.strategy_id || ''
    });
  }
  return _kfsSortPoints(out);
}

function _kfsFinalize(source, pts, note) {
  _klineSignal.source = source;
  _klineSignal.points = pts;
  _klineSignal.note = note || '';
  _klineSignal.status = pts.length ? 'ready' : (source === 'orders' ? 'empty_orders' : 'empty');
  _klineSignal.fetchedAt = Date.now();
  renderKlineSignals();
  updateKlineFsStatus();
  updateKlineFsLegend();
}

/** 加载买卖点（round-trips 优先；不可用则回退 orders；全部失败如实提示） */
function loadKlineSignals(code, force) {
  if (!code) return;
  if (!force && _klineSignal.code === code && _klineSignal.status !== 'idle' &&
      (Date.now() - _klineSignal.fetchedAt) < 60000) {
    renderKlineSignals();     // 60s 内复用结果，仅按当前 bar 重绘（周期切换后 bar 变了）
    return;
  }
  var seq = ++_klineSignalSeq;
  _klineSignal.code = code;
  _klineSignal.status = 'loading';
  _klineSignal.points = [];
  _klineSignal.unmatched = [];
  _klineSignal.matchedCount = 0;
  _klineSignal.source = '';
  _klineSignal.note = '';
  ensureKlineFsUI();
  updateKlineFsStatus();
  updateKlineFsLegend();

  if (window._proChartDisabled === true) {
    _klineSignal.status = 'unsupported';
    _klineSignal.fetchedAt = Date.now();
    updateKlineFsStatus();
    updateKlineFsLegend();
    return;
  }

  _kfsFetchRoundTrips(code).then(function (rt) {
    if (seq !== _klineSignalSeq) return;
    if (rt.ok) { _kfsFinalize('round_trips', _kfsNormalizeRoundTrips(rt.data), ''); return; }
    _kfsFetchOrders(code).then(function (of) {
      if (seq !== _klineSignalSeq) return;
      if (of.ok) {
        var pts = _kfsNormalizeOrders(of.data);
        var note = (rt.reason === 'not_ready')
          ? '闭环交易接口未就绪（/api/review/round-trips 暂不可用），已回退至订单流真实成交点，净盈亏暂缺'
          : '闭环交易接口异常（' + rt.reason + '），已回退至订单流真实成交点，净盈亏暂缺';
        if (!pts.length) {
          _klineSignal.source = 'orders';
          _klineSignal.points = [];
          _klineSignal.note = note;
          _klineSignal.status = 'empty_orders';
          _klineSignal.fetchedAt = Date.now();
          renderKlineSignals(); updateKlineFsStatus(); updateKlineFsLegend();
        } else {
          _kfsFinalize('orders', pts, note);
        }
      } else {
        var msg = (rt.reason === 'not_ready')
          ? '闭环交易接口未就绪（/api/review/round-trips 暂不可用）'
          : '交易记录获取失败（' + rt.reason + '）';
        _klineSignal.source = '';
        _klineSignal.points = [];
        _klineSignal.note = msg;
        _klineSignal.status = 'not_ready';
        _klineSignal.fetchedAt = Date.now();
        renderKlineSignals(); updateKlineFsStatus(); updateKlineFsLegend();
      }
    });
  });
}

// ---------------- 自定义 overlay：买卖点标注 ----------------
function registerKlineSignalOverlay() {
  if (_dapengSignalRegistered) return;
  if (typeof klinecharts === 'undefined' || !klinecharts.registerOverlay) return;
  klinecharts.registerOverlay({
    name: 'dapengSignal',
    totalStep: 1,
    lock: true,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: function (params) {
      var coordinates = params.coordinates || [];
      var overlay = params.overlay;
      if (!coordinates.length || !overlay) return [];
      var d = overlay.extendData || {};
      var isBuy = d.kind === 'B';
      var isDrill = !!d.is_drill;
      var stagger = Number(d.stagger) || 0;      // 同一 bar 多笔 → 垂直错开
      var gap = 22 + stagger * 18;
      var base = coordinates[0];
      var cy = isBuy ? base.y + gap : base.y - gap;  // 买点在下方、卖点在上方
      var accent = isDrill ? '#6b7280' : (isBuy ? '#3b82f6' : '#f59e0b');
      return [
        {
          type: 'line',
          attrs: { coordinates: [{ x: base.x, y: base.y }, { x: base.x, y: cy }] },
          styles: { color: accent, size: 1, style: isDrill ? 'dashed' : 'solid', dashedValue: [3, 3] },
          ignoreEvent: true
        },
        {
          type: 'circle',
          attrs: { x: base.x, y: cy, r: 9 },
          styles: isDrill
            ? { style: 'stroke', color: 'rgba(0,0,0,0)', borderColor: accent, borderSize: 1.5, borderStyle: 'dashed', borderDashedValue: [3, 3] }
            : { style: 'fill', color: accent, borderColor: accent, borderSize: 1 },
          ignoreEvent: true
        },
        {
          type: 'text',
          attrs: { x: base.x, y: cy, text: isBuy ? 'B' : 'S', align: 'center', baseline: 'middle' },
          styles: {
            style: 'fill', color: isDrill ? '#c3ccdb' : '#04101f',
            size: 11, weight: 'bold', family: 'SF Mono, Cascadia Code, monospace',
            backgroundColor: 'rgba(0,0,0,0)', borderSize: 0,
            paddingLeft: 0, paddingRight: 0, paddingTop: 0, paddingBottom: 0
          },
          ignoreEvent: true
        }
      ];
    }
  });
  _dapengSignalRegistered = true;
}

function clearKlineSignalOverlays() {
  if (typeof proChart === 'undefined' || !proChart || !proChart.removeOverlay) return;
  try { proChart.removeOverlay({ name: 'dapengSignal' }); } catch (e) {}
}

/** 把归一化买卖点绘制为 overlay（日期截断对齐 bar；未匹配单独记录；同 bar 错开） */
function renderKlineSignals() {
  ensureKlineFsUI();
  if (window._proChartDisabled === true || typeof proChart === 'undefined' || !proChart) {
    updateKlineFsStatus(); updateKlineFsLegend(); return;
  }
  clearKlineSignalOverlays();
  hideKlineSignalTip();
  _klineSignal.unmatched = [];
  _klineSignal.matchedCount = 0;
  // 仅在全屏激活时绘制（普通视图保持原状）
  if (!_kfsIsFullscreen()) { updateKlineFsStatus(); updateKlineFsLegend(); return; }
  if (!_klineSignal.points.length) { updateKlineFsStatus(); updateKlineFsLegend(); return; }

  if (!_dapengSignalRegistered) registerKlineSignalOverlay();

  var bars = (typeof proDataStore !== 'undefined' && proDataStore && proDataStore.bars) ? proDataStore.bars : [];
  if (!bars.length) { updateKlineFsStatus(); updateKlineFsLegend(); return; }

  var dateIndex = {};
  for (var i = 0; i < bars.length; i++) {
    var ds = _kfsDateStr(bars[i].timestamp);
    if (ds && !Object.prototype.hasOwnProperty.call(dateIndex, ds)) dateIndex[ds] = i;
  }
  var perBar = {};
  var overlays = [];
  var unmatched = [];
  for (var j = 0; j < _klineSignal.points.length; j++) {
    var p = _klineSignal.points[j];
    var pds = _kfsDateFromTs(p.ts);
    if (!pds || !Object.prototype.hasOwnProperty.call(dateIndex, pds)) { unmatched.push(p); continue; }
    var bi = dateIndex[pds];
    var bar = bars[bi];
    var key = bi + '|' + p.kind;
    var n = perBar[key] || 0; perBar[key] = n + 1;
    var value = p.kind === 'B' ? _kfsNum(bar.low) : _kfsNum(bar.high);
    if (!(value > 0)) value = _kfsNum(bar.close);
    overlays.push({
      name: 'dapengSignal',
      paneId: 'candle_pane',
      lock: true,
      points: [{ timestamp: bar.timestamp, value: value }],
      extendData: { kind: p.kind, is_drill: !!p.is_drill, stagger: n, payload: p }
    });
  }
  _klineSignal.unmatched = unmatched;
  _klineSignal.matchedCount = overlays.length;
  if (overlays.length) {
    try { proChart.createOverlay(overlays); } catch (e) { console.error('[DAPENG] 买卖点标注渲染失败:', e); }
  }
  updateKlineFsStatus();
  updateKlineFsLegend();
}

// ---------------- 悬浮明细（十字光标联动） ----------------
function showKlineSignalTip(cr, hits) {
  var tip = document.getElementById('kline-signal-tip');
  var card = _kfsCard();
  if (!tip || !card) return;
  var html = '';
  for (var i = 0; i < hits.length; i++) {
    var p = hits[i];
    var isBuy = p.kind === 'B';
    html += '<div class="kft-row ' + (isBuy ? 'kft-buy' : 'kft-sell') + '">' +
      '<div class="kft-head"><b>' + (isBuy ? '买点 B' : '卖点 S') + '</b>' +
      (p.is_drill ? '<span class="kft-drill">演练</span>' : '') +
      '<span class="kft-ts">' + _kfsEsc(_kfsFmtTs(p.ts)) + '</span></div>' +
      '<div class="kft-line"><span>成交价</span><b>' + (p.price > 0 ? _kfsPrice(p.price) : '—') + '</b></div>' +
      '<div class="kft-line"><span>数量</span><b>' + _kfsQty(p.qty) + ' 股</b></div>';
    if (!isBuy) {
      if (p.pnl == null) {
        html += '<div class="kft-line"><span>净盈亏</span><b class="kft-na">接口未就绪</b></div>';
      } else {
        html += '<div class="kft-line"><span>净盈亏</span><b class="' + (p.pnl >= 0 ? 'up' : 'dn') + '">' +
          (p.pnl >= 0 ? '+' : '') + _kfsPrice(p.pnl) + '</b></div>';
      }
      html += '<div class="kft-line"><span>平仓原因</span><b>' + _kfsEsc(_kfsExitLabel(p.exit_reason)) + '</b></div>';
    }
    html += '</div>';
  }
  tip.innerHTML = html;
  tip.hidden = false;
  var cardRect = card.getBoundingClientRect();
  var pro = document.getElementById('kline-chart-pro');
  var proRect = pro ? pro.getBoundingClientRect() : cardRect;
  var ox = proRect.left - cardRect.left;
  var oy = proRect.top - cardRect.top;
  var left = ox + cr.x + 16;
  var top = oy + cr.y + 14;
  var w = tip.offsetWidth, h = tip.offsetHeight;
  if (left + w > card.clientWidth - 8) left = Math.max(6, ox + cr.x - w - 16);
  if (top + h > card.clientHeight - 8) top = Math.max(6, card.clientHeight - h - 8);
  tip.style.left = Math.max(4, left) + 'px';
  tip.style.top = Math.max(4, top) + 'px';
}

function hideKlineSignalTip() {
  var tip = document.getElementById('kline-signal-tip');
  if (tip) tip.hidden = true;
}

function bindKlineSignalTooltip() {
  if (typeof proChart === 'undefined' || !proChart || !proChart.subscribeAction) return;
  if (_klineSignalTipBoundFor === proChart) return;
  _klineSignalTipBoundFor = proChart;
  try {
    proChart.subscribeAction('onCrosshairChange', function (cr) {
      if (!cr || cr.x == null || !_klineSignal.points.length || !_kfsIsFullscreen()) { hideKlineSignalTip(); return; }
      // 注意：klinecharts v10 的 onCrosshairChange 回调只带 {x,y,paneId}，
      // 需自行用 convertFromPixel(x) 反解该 x 对应的 bar 时间戳
      var ts = null;
      try {
        var info = proChart.convertFromPixel({ x: cr.x }, { paneId: 'candle_pane' });
        if (info && info.timestamp != null) ts = info.timestamp;
      } catch (e) {}
      if (ts == null) { hideKlineSignalTip(); return; }
      var ds = _kfsDateStr(ts);
      var hits = [];
      for (var i = 0; i < _klineSignal.points.length; i++) {
        if (_kfsDateFromTs(_klineSignal.points[i].ts) === ds) hits.push(_klineSignal.points[i]);
      }
      if (!hits.length) { hideKlineSignalTip(); return; }
      showKlineSignalTip(cr, hits);
    });
  } catch (e) {}
  var card = _kfsCard();
  if (card && !card._kfsLeaveBound) {
    card._kfsLeaveBound = true;
    card.addEventListener('mouseleave', hideKlineSignalTip);
  }
}

// ---------------- 价格口径 / 数据可用性（v20） ----------------
/** 价格口径：日/周/月走 fqkline(qfq 前复权)；分钟线 mkline 上游为不复权原始价 */
function klineAdjustLabel(period) {
  var p = String(period || 'day');
  return (p === 'day' || p === 'week' || p === 'month') ? '前复权' : '不复权';
}

/** 刷新口径徽章与「数据不可用/指标不足」提示 */
function updateKlineMetaUI(data) {
  var period = (data && data.period) || (window.appState && window.appState.period) || 'day';
  var source = (data && data.source) || '';
  var bars = (data && data.klines) ? data.klines.length : 0;
  var plabel = KLINE_PERIOD_LABEL[period] || period;

  var badge = document.getElementById('kline-adjust-badge');
  if (badge) {
    var adj = klineAdjustLabel(period);
    badge.textContent = adj;
    badge.setAttribute('data-adjust', adj === '前复权' ? 'qfq' : 'raw');
    badge.title = 'K线价格口径：' + adj +
      (adj === '前复权' ? '（qfq · 日/周/月线）' : '（原始价 · 分钟线）') +
      (source ? ' · 数据源 ' + source : '');
  }

  var notice = document.getElementById('kline-data-notice');
  if (!notice) return;
  var msg = '';
  if (!data) {
    msg = 'K线数据不可用：接口未返回数据（周期 ' + plabel + '）。';
  } else if (!bars) {
    msg = 'K线数据不可用：接口返回空数据（周期 ' + plabel + '，数据源 ' + (source || '未知') + '）。';
  } else {
    var ma5len = ((data.ma || {}).ma5 || []).length;
    var diflen = ((data.macd || {}).dif || []).length;
    var need = 26; // MACD(12,26,9) 的最小有效长度
    if (bars < need) {
      msg = '指标数据不足：仅 ' + bars + ' 根K线，MA/MACD 需 ≥' + need + ' 根，对应指标可能为空。';
    } else if (!ma5len && !diflen) {
      msg = '指标数据为空：后端未返回 MA/MACD 序列（周期 ' + plabel + '）。';
    }
  }
  if (msg) { notice.textContent = '⚠ ' + msg; notice.hidden = false; }
  else { notice.hidden = true; }
}

// ---------------- 对外钩子 ----------------
/** renderKline / renderKlinePro 渲染完成后调用：刷新元信息、口径徽章、可用性提示与买卖点 */
function kfsAfterRenderKline(data) {
  var kl = (data && data.klines) || [];
  var lastBar = kl.length ? kl[kl.length - 1] : {};
  _klineFsMeta = {
    code: (data && data.code) || (window.appState && window.appState.activeCode) || '',
    name: (data && data.name) || '',
    period: (data && data.period) || (window.appState && window.appState.period) || 'day',
    source: (data && data.source) || (kl.length ? '未知' : ''),
    lastTs: lastBar.d || '',
    bars: kl.length
  };
  ensureKlineFsUI();
  updateKlineMetaUI(data);
  updateKlineFsStatus();
  // 仅在全屏激活且确有数据时拉取/绘制买卖点（普通视图保持零额外请求、零改动）
  if (kl.length && _kfsIsFullscreen()) {
    loadKlineSignals(_klineFsMeta.code, false);
  } else {
    clearKlineSignalOverlays();
    hideKlineSignalTip();
  }
}
window.kfsAfterRenderKline = kfsAfterRenderKline;

/** 全屏状态变化时调用：进入则拉数据并绘制，退出则清除 overlay */
function kfsOnFullscreenChange(active) {
  ensureKlineFsUI();
  if (active) {
    bindKlineSignalTooltip();
    if (_klineFsMeta && _klineFsMeta.code) loadKlineSignals(_klineFsMeta.code, false);
    updateKlineFsStatus();
    renderKlineSignals();
  } else {
    clearKlineSignalOverlays();
    hideKlineSignalTip();
  }
}
window.kfsOnFullscreenChange = kfsOnFullscreenChange;

/** 强制重新拉取当前标的交易记录 */
window.refreshKlineSignals = function () {
  var code = (_klineFsMeta && _klineFsMeta.code) || (window.appState && window.appState.activeCode) || '';
  if (!code) return;
  loadKlineSignals(code, true);
};

/** 切换是否叠加演练造数（is_drill=1）成交点 */
window.toggleKlineSignalDrill = function () {
  _klineSignal.includeDrill = !_klineSignal.includeDrill;
  var code = (_klineFsMeta && _klineFsMeta.code) || (window.appState && window.appState.activeCode) || '';
  if (code) loadKlineSignals(code, true);
};

// ================================================================
//  K 线实时行情对接（v29）
//  ---------------------------------------------------------------
//  目标：盘中最后一根 K 线随实时成交动态刷新；新周期自动追加 bar；
//        断线 / 休市 / 取数降级在 K 线区右上角如实可见。
//
//  数据链路（真实优先 → 如实降级 → ✗编造）：
//    1) SSE   GET /api/stream?topics=quotes&codes=<code>   事件 quotes.tick
//    2) 兜底 GET /api/kline/quote?code=<code>              5s 轮询（推送不可用时）
//  两条路都失败 → 徽章显示「实时暂不可用：<reason>」，且不再改动任何 bar。
//
//  渲染策略：
//    - klinecharts v10.0.3（本地 vendor）没有 applyNewData/pushData，
//      实时通道只有 DataLoader 的 subscribeBar 回调（内部 _addData(..., 'update')：
//      timestamp 相同 → 替换最后一根；更大 → 追加新 bar），只重算最后一根，
//      不会整表重算指标，因此不闪烁。
//    - 回调不可用/抛错 → 回退 resetData() 节流重画，并 console.warn + 徽章标注，
//      绝不静默失效。
//    - 兼容渲染（ECharts）走既有 updateLastCandle() 增量刷新。
//
//  节流：≥1000ms，且仅在 K 线可见（页面未隐藏 且 卡片在布局内）时渲染。
//  生命周期：打开 K 线启动；切换标的/周期、全屏切换、页面隐藏 → 停止；
//            页面回到前台 → 重新启动。
// ================================================================

/** 实时节流下限（ms）—— 事件最快 3s 一次，渲染最多 1s 一次 */
var KLINE_LIVE_THROTTLE_MS = 1000;
/** SSE 不可用时的兜底轮询间隔（ms） */
var KLINE_LIVE_POLL_MS = 5000;
/** 轮询期间回探 SSE 是否恢复的间隔（ms） */
var KLINE_LIVE_SSE_RETRY_MS = 30000;
/** SSE 连接后多久收不到该标的报价即判定为「连上了但没数据」→ 降级 */
var KLINE_LIVE_WATCHDOG_MS = 12000;

var _klineLive = {
  code: '',
  period: 'day',
  active: false,          // 是否处于对接中（页面隐藏/无数据时为 false）
  mode: 'sse',            // 'sse' | 'poll'
  status: 'idle',         // idle|connecting|live|poll|closed|reconnecting|degraded|paused|stopped
  reason: '',             // 降级/失败原因（原样展示，不粉饰）
  asOf: '',               // 最近一笔报价的 as_of（真实时间戳，缺失则留空）
  price: 0,
  prevClose: 0,
  chgPct: 0,
  marketClosed: false,
  closedApplied: false,   // 休市态下只应用一次，避免高频空转
  pending: null,          // 待渲染的 quote（节流窗口内被覆盖）
  stream: null,
  pollTimer: 0,
  retryTimer: 0,
  flushTimer: 0,
  watchdogTimer: 0,
  lastRenderAt: 0,
  reconnectAttempt: 0,    // v31：当前断线后的重连序号（徽章展示「第 n 次」）
  reconnectDelay: 0,      // 下次重连延迟（ms）
  updates: 0,             // 更新最后一根的次数
  appends: 0,             // 追加新 bar 的次数
  fallbacks: 0,           // 回退全量重画的次数
  startedAt: 0
};

/** 数值安全读取（非法值回退，绝不产生 NaN/假价格） */
function _klineLiveNum(v, fallback) {
  var n = Number(v);
  if (!isFinite(n)) return fallback;
  return n;
}

function _klineLivePad2(n) {
  n = parseInt(n, 10);
  if (!isFinite(n)) return '00';
  return (n < 10 ? '0' : '') + n;
}

/** 报价时间戳 → ms（兼容 '2026-10-19' / '2026-10-19 10:30' / ISO / 8位 / 12位） */
function _klineLiveParseTs(v) {
  if (v == null || v === '') return 0;
  if (typeof v === 'number') return isFinite(v) ? v : 0;
  var s = String(v).trim();
  if (!s) return 0;
  var t = parseProTimestamp(s);
  return isFinite(t) ? t : 0;
}

/**
 * bar 归属桶起点（ms）：同一根 bar 内的所有报价应落到同一个桶
 * - 日/周/月：归到当日 / 本周一 / 本月 1 日的 00:00
 * - 分钟线：精确到分钟（秒/毫秒归零）
 * 用于判断「更新最后一根」还是「追加新 bar」。
 */
function _klineLiveBarStart(ts, periodKey) {
  var d = new Date(ts);
  if (isNaN(d.getTime())) return 0;
  var p = String(periodKey || 'day');
  d.setSeconds(0, 0);
  if (p === 'week') {
    d.setHours(0, 0, 0, 0);
    var wd = (d.getDay() + 6) % 7;          // 周一=0
    d.setDate(d.getDate() - wd);
  } else if (p === 'month') {
    d.setHours(0, 0, 0, 0);
    d.setDate(1);
  } else if (p === 'day' || p === 'week' || p === 'month') {
    d.setHours(0, 0, 0, 0);
  } else {
    // 分钟线：只对齐到分钟
    d.setSeconds(0, 0);
  }
  return d.getTime();
}

/** bar 时间戳（ms）→ API 侧日期字符串（与 /api/kline 的 d 字段同形） */
function _klineLiveBarDate(ts, periodKey) {
  var d = new Date(ts);
  if (isNaN(d.getTime())) return '';
  var ymd = d.getFullYear() + '-' + _klineLivePad2(d.getMonth() + 1) + '-' + _klineLivePad2(d.getDate());
  var p = String(periodKey || 'day');
  if (p === 'day' || p === 'week' || p === 'month') return ymd;
  return ymd + ' ' + _klineLivePad2(d.getHours()) + ':' + _klineLivePad2(d.getMinutes());
}

/** 从 as_of / ts 中取时钟串：'HH:MM:SS'（无秒则 'HH:MM'），取不到返回 ''（不编造） */
function _klineLiveClock(s) {
  var m = String(s || '').match(/(\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return '';
  var out = _klineLivePad2(m[1]) + ':' + _klineLivePad2(m[2]);
  return m[3] ? (out + ':' + _klineLivePad2(m[3])) : out;
}

// ---------------- 徽章 DOM / 渲染 ----------------
/** 创建（幂等）并返回一个挂载在 K 线头部动作区的实时状态徽章 */
function ensureKlineLiveUI() {
  var card = document.getElementById('kline-card');
  if (!card) return null;
  var el = document.getElementById('kline-live-badge');
  if (el) return el;
  el = document.createElement('span');
  el.id = 'kline-live-badge';
  el.className = 'kline-live-badge';
  el.setAttribute('data-state', 'idle');
  el.setAttribute('role', 'status');
  el.setAttribute('aria-live', 'polite');
  var actions = card.querySelector('.kline-chart-actions');
  if (actions) {
    actions.insertBefore(el, actions.firstChild);
  } else {
    var header = card.querySelector('.kline-header');
    if (header) header.appendChild(el);
    else card.insertBefore(el, card.firstChild);
  }
  return el;
}

/** 按当前状态渲染徽章（涨=红 / 跌=绿，沿用项目主题变量） */
function renderKlineLiveBadge() {
  var el = ensureKlineLiveUI();
  if (!el) return;
  var st = _klineLive.status;
  var txt = '';
  var tip = '';
  if (st === 'connecting') {
    txt = '实时连接中…';
    tip = '已连上实时推送，等待该标的第一笔报价';
  } else if (st === 'live') {
    txt = (_klineLive.period === 'min' ? '分时实时 · ' : '实时 · ') + (_klineLiveClock(_klineLive.asOf) || '--:--:--');
    tip = '实时成交驱动' + (_klineLive.period === 'min' ? '分时最后一点' : '最后一根 K 线') + '（行情时间 ' + (_klineLive.asOf || '未提供') + '）';
  } else if (st === 'poll') {
    txt = '轮询兜底（5s）· ' + (_klineLiveClock(_klineLive.asOf) || '--:--:--');
    tip = '实时推送不可用，已降级为每 5 秒轮询 /api/kline/quote（' + (_klineLive.asOf || '未提供') + '）';
  } else if (st === 'closed') {
    var hm = _klineLiveClock(_klineLive.asOf);
    txt = '已收盘 · 最后更新 ' + (hm ? hm.slice(0, 5) : '--:--');
    tip = '休市/收盘中，已停止高频刷新，显示最后一根真实' + (_klineLive.period === 'min' ? '分时点' : ' bar');
  } else if (st === 'reconnecting') {
    var nTry = _klineLive.reconnectAttempt || 0;
    var dSec = _klineLive.reconnectDelay ? Math.round(_klineLive.reconnectDelay / 1000) : 0;
    txt = nTry > 0
      ? ('重连中(第 ' + nTry + ' 次' + (dSec > 0 ? (' · ' + dSec + 's后') : '') + ')')
      : '重连中…';
    tip = '实时推送断开，指数退避自动重连（1s→2s→4s→8s→16s→30s）' + (_klineLive.reason ? ('；' + _klineLive.reason) : '');
  } else if (st === 'degraded') {
    txt = '实时暂不可用：' + (_klineLive.reason || '原因未知');
    tip = '实时取数失败，未显示任何推算价格（K 线保持最后一次真实数据）';
  } else if (st === 'paused') {
    txt = '已暂停（页面不可见）';
    tip = '页面切到后台，已停止接收与处理推送';
  } else {
    txt = '实时未开启';
    tip = '尚未开始实时对接';
  }
  if (_klineLive.fallbacks > 0) {
    tip += '；注意：本机 klinecharts 实时通道不可用，已回退「节流重画最后一根」（' + _klineLive.fallbacks + ' 次）';
  }

  var html = '<i class="klb-dot" aria-hidden="true"></i><span class="klb-text">' + _kfsEsc(txt) + '</span>';
  // 仅展示真实收到的价格；降级/未开启时一律不显示，避免假价格
  if (_klineLive.price > 0 && (st === 'live' || st === 'poll' || st === 'closed')) {
    var chg = _klineLive.chgPct;
    var cls = chg > 0 ? 'up' : (chg < 0 ? 'dn' : 'flat');
    var chgTxt = isFinite(chg) && chg !== 0 ? (' ' + (chg > 0 ? '+' : '') + chg.toFixed(2) + '%') : '';
    html += ' <span class="klb-price ' + cls + '">' + _klineLive.price.toFixed(2) + chgTxt + '</span>';
  }
  el.innerHTML = html;
  el.setAttribute('data-state', st);
  el.title = tip;
}

// ---------------- 可见性 ----------------
/** 页面可见 且 K 线卡片在布局内 → 才允许渲染（省电、避免后台空转） */
function _klineLiveVisible() {
  if (typeof document !== 'undefined' && document.hidden) return false;
  var card = document.getElementById('kline-card');
  if (!card) return false;
  var pro = document.getElementById('kline-chart-pro');
  var legacy = document.getElementById('kline-chart');
  var target = (pro && pro.style.display !== 'none') ? pro : legacy;
  if (!target) return card.offsetParent !== null;
  return target.offsetParent !== null || card.offsetParent !== null;
}

// ---------------- 应用报价到图表 ----------------
/** 把一次报价同步进 klineCache（保证切换指标/重渲染时不丢实时增量） */
function _klineLiveSyncCache(bar, isNew) {
  try {
    var cacheKey = _klineLive.code + ':' + _klineLive.period;
    var cached = (window.appState && window.appState.klineCache) ? window.appState.klineCache[cacheKey] : null;
    if (!cached || !cached.klines || !cached.klines.length) return;
    var k = {
      d: _klineLiveBarDate(bar.timestamp, _klineLive.period),
      o: bar.open, h: bar.high, l: bar.low, c: bar.close, v: bar.volume
    };
    if (isNew) cached.klines.push(k);
    else cached.klines[cached.klines.length - 1] = k;
  } catch (e) { /* 缓存同步失败不影响主流程 */ }
}

/**
 * 应用报价到 Pro 引擎（klinecharts）
 * @returns {boolean} 是否成功改动了图表
 */
function _klineLiveApplyPro(q, price, ts) {
  var bars = (typeof proDataStore !== 'undefined' && proDataStore && proDataStore.bars) ? proDataStore.bars : [];
  if (!bars.length) return false;
  var last = bars[bars.length - 1];
  if (!last) return false;

  var period = _klineLive.period;
  var lastStart = _klineLiveBarStart(last.timestamp, period);
  var qStart = ts > 0 ? _klineLiveBarStart(ts, period) : lastStart;   // 无 bar_ts 视为同一根
  var isNew = ts > 0 && qStart > lastStart;

  var qHigh = _klineLiveNum(q.high, 0);
  var qLow = _klineLiveNum(q.low, 0);
  var qVol = _klineLiveNum(q.volume, 0);
  var bar;
  if (isNew) {
    // 新周期：新开一根（开=高=低=收=最新价，量按后端给的增量）
    bar = {
      timestamp: qStart,
      open: _klineLiveNum(q.open, price) > 0 ? _klineLiveNum(q.open, price) : price,
      high: Math.max(price, qHigh > 0 ? qHigh : price),
      low: Math.min(price, qLow > 0 ? qLow : price),
      close: price,
      volume: qVol > 0 ? qVol : 0
    };
  } else {
    // 同一根：收盘=最新价；高低取极值扩展；量取「已有与最新」的较大者（累计量语义）
    var baseHigh = _klineLiveNum(last.high, price);
    var baseLow = _klineLiveNum(last.low, price);
    var candHigh = Math.max(price, qHigh > 0 ? qHigh : price);
    var candLow = Math.min(price, qLow > 0 ? qLow : price);
    bar = {
      timestamp: last.timestamp,
      open: _klineLiveNum(last.open, 0) > 0 ? _klineLiveNum(last.open, 0) : _klineLiveNum(q.open, price),
      high: Math.max(baseHigh, candHigh),
      low: Math.min(baseLow, candLow),
      close: price,
      volume: qVol > 0 ? Math.max(_klineLiveNum(last.volume, 0), qVol) : _klineLiveNum(last.volume, 0)
    };
  }

  // 写回数据仓（供后续 getBars/resetData 保持一致）
  if (isNew) bars.push(bar);
  else bars[bars.length - 1] = bar;
  proLastBar = bar;
  _klineLiveSyncCache(bar, isNew);

  // ① 首选：DataLoader 实时通道（只重算最后一根）
  var applied = false;
  try {
    if (typeof proRealtimeCb === 'function') {
      proRealtimeCb(bar);
      applied = true;
    }
  } catch (e) {
    console.warn('[DAPENG][kline-live] subscribeBar 实时回调失败，尝试 updateData：', e);
  }
  // ② 次选：chart.updateData（个别构建提供）
  if (!applied && typeof proChart !== 'undefined' && proChart && typeof proChart.updateData === 'function') {
    try { proChart.updateData(bar); applied = true; }
    catch (e) { console.warn('[DAPENG][kline-live] proChart.updateData 失败：', e); }
  }
  // ③ 兜底：节流重画（数据已写回 bars，resetData 会重新载入并含最新 bar）
  if (!applied) {
    _klineLive.fallbacks += 1;
    console.warn('[DAPENG][kline-live] klinecharts 实时通道不可用，回退 resetData 重画最后一根');
    try { proChart.resetData(); applied = true; }
    catch (e) { console.error('[DAPENG][kline-live] resetData 兜底也失败：', e); }
  }
  if (applied) { if (isNew) _klineLive.appends += 1; else _klineLive.updates += 1; }
  return applied;
}

/** 应用报价到兼容渲染（ECharts） */
function _klineLiveApplyLegacy(q, price, ts) {
  var qHigh = _klineLiveNum(q.high, 0);
  var qLow = _klineLiveNum(q.low, 0);
  var qVol = _klineLiveNum(q.volume, 0);
  var bars = (typeof proDataStore !== 'undefined' && proDataStore && proDataStore.bars) ? proDataStore.bars : [];
  var isNew = false;
  if (bars.length && ts > 0) {
    var lastStart = _klineLiveBarStart(bars[bars.length - 1].timestamp, _klineLive.period);
    isNew = _klineLiveBarStart(ts, _klineLive.period) > lastStart;
  }
  if (isNew) {
    // ECharts 路径追加新 bar 需整表重画（新周期属于低频事件，1s 节流内可接受）
    var bar = {
      timestamp: _klineLiveBarStart(ts, _klineLive.period),
      open: _klineLiveNum(q.open, price) > 0 ? _klineLiveNum(q.open, price) : price,
      high: Math.max(price, qHigh > 0 ? qHigh : price),
      low: Math.min(price, qLow > 0 ? qLow : price),
      close: price,
      volume: qVol > 0 ? qVol : 0
    };
    if (bars.length) bars.push(bar);
    proLastBar = bar;
    _klineLiveSyncCache(bar, true);
    _klineLive.appends += 1;
    _klineLive.fallbacks += 1;   // 兼容渲染路径本身即降级渲染
    _rerenderKlineWithCache();
    return true;
  }
  if (typeof updateLastCandle === 'function') {
    updateLastCandle(price, qHigh, qLow, qVol);
    _klineLive.updates += 1;
    return true;
  }
  return false;
}

/**
 * v33 双源自愈：收到真实 tick 且推送连接确实健康时，把模式从 poll 切回 sse，
 * 停掉 5s 轮询与 30s 回探（否则 onOpen 已发生 → 永远双源并行、白耗上游）。
 */
function _klineLiveHealMode() {
  if (_klineLive.mode !== 'poll') return;
  var s = _klineLive.stream;
  if (!s || typeof s.isConnected !== 'function' || !s.isConnected()) return;
  _klineLiveStopPoll();
  _klineLiveStopRetry();
  _klineLive.mode = 'sse';
  renderKlineLiveBadge();
}

/** 真正把一笔报价落到图表（由节流器调用） */
function _klineLiveApply(q) {
  if (!_klineLive.active || !q) return false;
  var applied = false;

  if (_klineLive.period === 'min') {
    // 分时模式：按分钟归并到分时点（同一条 SSE 通道，不另开连接）
    applied = _tsApplyQuote(q);
  } else {
    var price = _klineLiveNum(q.price, 0);
    if (!(price > 0)) return false;                 // 无有效价 → 不改图，也不编造
    var ts = _klineLiveParseTs(q.bar_ts || q.ts || q.as_of || '');
    var usePro = (window._proChartDisabled !== true && typeof proChart !== 'undefined' && proChart);
    applied = usePro ? _klineLiveApplyPro(q, price, ts) : _klineLiveApplyLegacy(q, price, ts);
  }

  if (applied) _klineLiveHealMode();
  return applied;
}

// ---------------- 节流调度 ----------------
function _klineLiveSchedule(q) {
  if (!_klineLive.active) return;
  _klineLive.pending = q;
  if (_klineLive.flushTimer) return;              // 窗口内已有待渲染任务
  var wait = Math.max(0, KLINE_LIVE_THROTTLE_MS - (Date.now() - _klineLive.lastRenderAt));
  _klineLive.flushTimer = setTimeout(function () {
    _klineLive.flushTimer = 0;
    _klineLiveFlush(false);
  }, wait);
}

/**
 * 冲刷待渲染报价
 * @param {boolean} force - true 时忽略「可见性」检查（供 QA 注入事件使用）
 */
function _klineLiveFlush(force) {
  var q = _klineLive.pending;
  _klineLive.pending = null;
  if (!q) return false;
  if (!force && !_klineLiveVisible()) return false;   // 不可见：不渲染，等下一个事件
  var ok = _klineLiveApply(q);
  if (ok) _klineLive.lastRenderAt = Date.now();
  renderKlineLiveBadge();
  return ok;
}

// ---------------- 信封 / 单笔报价处理 ----------------
/** 记录报价元信息并判断是否需要渲染（休市只应用一次） */
function _klineLiveHandleQuote(q) {
  if (!_klineLive.active || !q) return false;
  if (String(q.code || '') !== String(_klineLive.code)) return false;   // 只处理当前标的
  var price = _klineLiveNum(q.price, 0);
  if (!(price > 0)) {
    // 无有效价：如实降级，绝不拿其它字段顶替
    _klineLive.status = 'degraded';
    _klineLive.reason = '报价无有效价格';
    renderKlineLiveBadge();
    return false;
  }
  _klineLive.price = price;
  _klineLive.prevClose = _klineLiveNum(q.prev_close, 0);
  _klineLive.chgPct = _klineLiveNum(q.chg_pct, 0);
  _klineLive.asOf = q.as_of || q.ts || '';
  _klineLive.marketClosed = (q.market_closed === true);

  if (_klineLive.marketClosed) {
    _klineLive.status = 'closed';
    if (_klineLive.closedApplied) {
      // 盘后一次性闸门：本条不再重画 bar，但若此刻已降级为 5s 轮询，
      // 说明推送连接其实仍在（休市 30s 降频），顺手把 mode 拉回 sse 并停掉轮询，
      // 否则会每 5s 白打一次上游且永不自愈。
      _klineLiveHealMode();
      renderKlineLiveBadge();
      return false;
    }
    _klineLive.closedApplied = true;
  } else {
    _klineLive.closedApplied = false;
    if (_klineLive.status !== 'poll') _klineLive.status = 'live';
  }
  _klineLiveSchedule(q);
  renderKlineLiveBadge();
  return true;
}

/**
 * 处理 SSE 事件信封
 * {"topic":"quotes","event":"quotes.tick","degraded":bool,"reason":...,"data":{"quotes":[...],"codes":[...]}}
 *
 * v33：**先按标的过滤，命中后再看 degraded** —— 别的标的的降级事件不得污染当前图
 * （否则 A 标的失败后打开 B，B 会显示 A 的失败原因，并把 mode 打成 poll）。
 */
function _klineLiveHandleEnvelope(payload) {
  if (!_klineLive.active || !payload) return false;

  var quotes = null;
  if (payload.data && Array.isArray(payload.data.quotes)) quotes = payload.data.quotes;
  else if (payload.data && payload.data.quotes && payload.data.quotes.code) quotes = [payload.data.quotes];
  else if (Array.isArray(payload.quotes)) quotes = payload.quotes;
  else if (payload.data && payload.data.code) quotes = [payload.data];
  else if (payload.code) quotes = [payload];
  if (!quotes || !quotes.length) return false;

  // 只保留当前标的的报价；一条都不属于本标的 → 直接返回，不改任何状态
  var mine = [];
  for (var i = 0; i < quotes.length; i++) {
    var q = quotes[i];
    if (q && String(q.code || '') === String(_klineLive.code)) mine.push(q);
  }
  if (!mine.length) return false;

  if (payload.degraded === true) {
    _klineLiveDegrade(payload.reason || '上游取数降级');
  }
  var hit = false;
  for (var j = 0; j < mine.length; j++) {
    if (_klineLiveHandleQuote(mine[j])) hit = true;
  }
  return hit;
}

function _klineLiveOnStreamEvent(name, payload) {
  if (!_klineLive.active) return;
  if (name === 'ping') return;                                     // 命名心跳：仅用于 watchdog 计时
  if (typeof document !== 'undefined' && document.hidden) return;    // 页面隐藏：不处理推送
  if (name === 'quotes.tick' || name === 'quotes.snapshot') {
    var hit = _klineLiveHandleEnvelope(payload);
    if (hit) _klineLiveClearWatchdog();                              // 收到该标的报价 → 取消看门狗
    return;
  }
  if (name === 'quotes.unavailable') {
    // v33：无 quotes 明细的降级事件——若带 codes，同样先按标的过滤（别人的失败不串台）
    var ucodes = (payload && payload.data && payload.data.codes) || [];
    if (ucodes.length) {
      var mineCode = String(_klineLive.code || '');
      var belongs = false;
      for (var ui = 0; ui < ucodes.length; ui++) {
        if (String(ucodes[ui]) === mineCode) { belongs = true; break; }
      }
      if (!belongs) return;
    }
    _klineLiveDegrade((payload && payload.reason) || '上游行情源连续失败');
    return;
  }
  if (name === 'connected') {
    // 推送已连接，但还没有该标的报价 → 保持「连接中」，并起看门狗
    if (_klineLive.mode === 'sse' && _klineLive.status === 'connecting') {
      _klineLiveArmWatchdog();
      renderKlineLiveBadge();
    }
  }
}

// ---------------- 看门狗（连上但没数据 → 如实降级，不静默） ----------------
function _klineLiveArmWatchdog() {
  _klineLiveClearWatchdog();
  _klineLive.watchdogTimer = setTimeout(function () {
    _klineLive.watchdogTimer = 0;
    if (!_klineLive.active || _klineLive.mode !== 'sse') return;
    _klineLiveDegrade('推送已连接但 ' + Math.round(KLINE_LIVE_WATCHDOG_MS / 1000) + 's 内未收到该标的实时报价');
  }, KLINE_LIVE_WATCHDOG_MS);
}
function _klineLiveClearWatchdog() {
  if (_klineLive.watchdogTimer) { clearTimeout(_klineLive.watchdogTimer); _klineLive.watchdogTimer = 0; }
}

// ---------------- 启动 / 停止 / 降级 ----------------
function _klineLiveStopStream() {
  if (_klineLive.stream && typeof _klineLive.stream.stop === 'function') {
    try { _klineLive.stream.stop(); } catch (e) {}
  }
  _klineLive.stream = null;
  proRealtimeCb = proRealtimeCb;   // 保持引用，stop 不改变引擎订阅
}
function _klineLiveStopPoll() {
  if (_klineLive.pollTimer) { clearInterval(_klineLive.pollTimer); _klineLive.pollTimer = 0; }
}
function _klineLiveStopRetry() {
  if (_klineLive.retryTimer) { clearInterval(_klineLive.retryTimer); _klineLive.retryTimer = 0; }
}
function _klineLiveClearFlush() {
  if (_klineLive.flushTimer) { clearTimeout(_klineLive.flushTimer); _klineLive.flushTimer = 0; }
}

/** 推送不可用 → 切轮询兜底（5s）；轮询也失败则由轮询回调置为 degraded */
function _klineLiveDegrade(reason) {
  if (!_klineLive.active) return;
  _klineLive.reason = reason || '推送不可用';
  _klineLive.mode = 'poll';
  _klineLive.status = 'reconnecting';
  // v31：不再硬关推送流 —— dashboard-stream v3 自身会指数退避重连；
  //      轮询仅作兜底并行运行，推送恢复（onOpen）后自动停轮询。
  _klineLiveClearWatchdog();
  renderKlineLiveBadge();
  _klineLiveStartPoll();
  if (_klineLive.stream) {
    _klineLiveStopRetry();          // 流在自愈，无需 30s 回探
  } else {
    _klineLiveStartSseRetry();      // 无流（初始化失败）才靠回探重建
  }
}

/** 轮询兜底：每 5s 拉一次 /api/kline/quote */
function _klineLivePollOnce() {
  if (!_klineLive.active) return;
  if (typeof document !== 'undefined' && document.hidden) return;
  var url = '/api/kline/quote?code=' + encodeURIComponent(_klineLive.code);
  fetch(url, { headers: { 'Accept': 'application/json' }, cache: 'no-store' })
    .then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    })
    .then(function (json) {
      if (!_klineLive.active) return;
      var okFlag = !!(json && json.ok);
      var data = (json && json.data) ? json.data : null;
      if (!okFlag || !data) {
        _klineLive.status = 'degraded';
        _klineLive.reason = (json && json.reason) ? String(json.reason) : '兜底接口返回异常';
        renderKlineLiveBadge();
        return;
      }
      var q = (data.quotes && data.quotes.length) ? data.quotes[0] : data;
      _klineLiveHandleQuote(q);
      if (_klineLive.status === 'reconnecting') {
        _klineLive.status = _klineLive.marketClosed ? 'closed' : 'poll';
      }
      renderKlineLiveBadge();
    })
    .catch(function (err) {
      if (!_klineLive.active) return;
      _klineLive.status = 'degraded';
      _klineLive.reason = '兜底接口不可用（' + (err && err.message ? err.message : '网络错误') + '）';
      renderKlineLiveBadge();
    });
}

function _klineLiveStartPoll() {
  _klineLiveStopPoll();
  _klineLivePollOnce();                                    // 立即取一次
  _klineLive.pollTimer = setInterval(_klineLivePollOnce, KLINE_LIVE_POLL_MS);
}

/** 轮询期间每 30s 回探 SSE，恢复则切回推送 */
function _klineLiveStartSseRetry() {
  _klineLiveStopRetry();
  _klineLive.retryTimer = setInterval(function () {
    if (!_klineLive.active || _klineLive.mode !== 'poll') { _klineLiveStopRetry(); return; }
    if (typeof document !== 'undefined' && document.hidden) return;
    _klineLiveStartSse();
  }, KLINE_LIVE_SSE_RETRY_MS);
}

function _klineLiveStartSse() {
  if (typeof window.DashboardStream === 'undefined' || typeof window.DashboardStream.create !== 'function') {
    _klineLive.status = 'degraded';
    _klineLive.reason = '推送组件未加载（dashboard-stream.js）';
    renderKlineLiveBadge();
    return;
  }
  _klineLiveStopStream();
  // v31：分时订阅 1 分钟对齐粒度（bar_ts 落到分钟），其余周期原样
  var ssePeriod = (_klineLive.period === 'min') ? '1' : _klineLive.period;
  try {
    _klineLive.stream = window.DashboardStream.create({
      url: '/api/stream?topics=quotes&codes=' + encodeURIComponent(_klineLive.code) +
           '&period=' + encodeURIComponent(ssePeriod),
      onOpen: function () {
        if (!_klineLive.active) return;
        if (_klineLive.mode === 'poll') { _klineLiveStopPoll(); _klineLiveStopRetry(); }
        _klineLive.mode = 'sse';
        _klineLive.reconnectAttempt = 0;
        if (_klineLive.status !== 'live' && _klineLive.status !== 'closed') _klineLive.status = 'connecting';
        _klineLiveArmWatchdog();
        renderKlineLiveBadge();
      },
      onEvent: _klineLiveOnStreamEvent,
      onDegrade: function (reason) { _klineLiveDegrade(reason); },
      // v3 韧性回调：重连中状态可见 / 重连成功后重新对齐 / 假死主动重连
      onReconnect: function (attempt, delayMs) {
        if (!_klineLive.active) return;
        _klineLive.reconnectAttempt = attempt;
        _klineLive.reconnectDelay = delayMs;
        _klineLive.status = 'reconnecting';
        renderKlineLiveBadge();
      },
      onResync: function () {
        if (!_klineLive.active) return;
        // 重连成功：先补一次快照，再继续吃增量（避免断线期间的空洞）
        if (_klineLive.period === 'min') {
          if (typeof window.loadAndRenderTimeshare === 'function') {
            window.loadAndRenderTimeshare(_klineLive.code, { silent: true });
          }
        } else {
          _klineLivePollOnce();
        }
      },
      onStale: function (idleMs) {
        if (!_klineLive.active) return;
        _klineLive.status = 'reconnecting';
        _klineLive.reason = '推送假死 ' + Math.round(idleMs / 1000) + 's 无事件，正在重连';
        renderKlineLiveBadge();
      }
    });
  } catch (e) {
    _klineLive.status = 'degraded';
    _klineLive.reason = '推送初始化失败：' + (e && e.message ? e.message : e);
    renderKlineLiveBadge();
  }
}

/**
 * 启动 K 线实时对接
 * @param {string} code - 标的代码（如 sh600519）
 * @param {string} periodKey - 周期（day/week/month/60/30/15/5）
 * @param {boolean=} force - 同参数也强制重启
 */
function startKlineLive(code, periodKey, force) {
  code = String(code == null ? '' : code).trim();
  periodKey = String(periodKey == null ? 'day' : periodKey).trim() || 'day';
  if (!code) { stopKlineLive(); return; }
  if (!force && _klineLive.active && _klineLive.code === code && _klineLive.period === periodKey) return;  // 幂等

  stopKlineLive();
  _klineLive.code = code;
  _klineLive.period = periodKey;
  _klineLive.active = true;
  _klineLive.mode = 'sse';
  _klineLive.status = 'connecting';
  _klineLive.reason = '';
  _klineLive.asOf = '';
  _klineLive.price = 0;
  _klineLive.prevClose = 0;
  _klineLive.chgPct = 0;
  _klineLive.marketClosed = false;
  _klineLive.closedApplied = false;
  _klineLive.pending = null;
  _klineLive.updates = 0;
  _klineLive.appends = 0;
  _klineLive.fallbacks = 0;
  _klineLive.reconnectAttempt = 0;
  _klineLive.reconnectDelay = 0;
  _klineLive.startedAt = Date.now();
  ensureKlineLiveUI();
  if (periodKey !== 'min' && typeof _tsStopCalibration === 'function') _tsStopCalibration();

  if (typeof document !== 'undefined' && document.hidden) {
    _klineLive.status = 'paused';
    renderKlineLiveBadge();
    return;
  }
  renderKlineLiveBadge();
  _klineLiveStartSse();
}

/** 停止 K 线实时对接（切标的/切周期/全屏切换/页面隐藏） */
function stopKlineLive() {
  _klineLiveStopStream();
  _klineLiveStopPoll();
  _klineLiveStopRetry();
  _klineLiveClearWatchdog();
  _klineLiveClearFlush();
  if (typeof _tsStopCalibration === 'function') _tsStopCalibration();
  _klineLive.pending = null;
  _klineLive.active = false;
  _klineLive.status = 'idle';
  renderKlineLiveBadge();
}

/** 用当前 code/period 重开一次（全屏切换、页面回前台） */
function _klineLiveRestart() {
  var code = _klineLive.code;
  var period = _klineLive.period;
  if (!code) return;
  startKlineLive(code, period, true);
}

/** renderKline 渲染完成后调用：按当前数据对齐实时对接（幂等） */
function _klineLiveSyncFromRender(data) {
  var code = (data && data.code) || (window.appState && window.appState.activeCode) || '';
  var period = (data && data.period) || (window.appState && window.appState.period) || 'day';
  if (!code) { stopKlineLive(); return; }
  var hasBars = !!(data && data.klines && data.klines.length);
  if (!hasBars) { stopKlineLive(); return; }        // 无 bar：不启动，避免凭报价造 bar
  startKlineLive(code, period);
}

// ---------------- 页面可见性 ----------------
function _klineLiveOnVisibility() {
  if (typeof document === 'undefined') return;
  if (document.hidden) {
    if (_klineLive.active || _klineLive.code) {
      _klineLiveStopStream();
      _klineLiveStopPoll();
      _klineLiveStopRetry();
      _klineLiveClearWatchdog();
      _klineLiveClearFlush();
      _klineLive.pending = null;
      _klineLive.active = false;
      _klineLive.status = 'paused';
      renderKlineLiveBadge();
    }
  } else if (_klineLive.code) {
    _klineLiveRestart();
  }
}
if (typeof document !== 'undefined' && !document.__klineLiveVisBound) {
  document.__klineLiveVisBound = true;
  document.addEventListener('visibilitychange', _klineLiveOnVisibility);
}

// ---------------- 对外接口 / QA 只读快照 ----------------
window.startKlineLive = startKlineLive;
window.stopKlineLive = stopKlineLive;

/** 当前最后一根 bar 的收盘价（Pro 数据仓优先，兼容渲染回退 ECharts） */
function _klineLiveLastClose() {
  var bars = (typeof proDataStore !== 'undefined' && proDataStore && proDataStore.bars) ? proDataStore.bars : [];
  if (bars.length) return bars[bars.length - 1].close;
  if (typeof proLastBar !== 'undefined' && proLastBar) return proLastBar.close;
  if (typeof klineChart !== 'undefined' && klineChart && !klineChart.isDisposed()) {
    try {
      var opt = klineChart.getOption();
      var series = opt && opt.series ? opt.series : [];
      for (var i = 0; i < series.length; i++) {
        if (series[i] && series[i].name === 'K线' && series[i].data && series[i].data.length) {
          var last = series[i].data[series[i].data.length - 1];
          if (Array.isArray(last)) return last[1];
        }
      }
    } catch (e) {}
  }
  return null;
}

/** QA / 调试接口：只读状态 + 注入 stub 报价（不改变生产路径） */
window.klineLiveDebug = {
  state: function () {
    return {
      code: _klineLive.code, period: _klineLive.period, active: _klineLive.active,
      mode: _klineLive.mode, status: _klineLive.status, reason: _klineLive.reason,
      asOf: _klineLive.asOf, price: _klineLive.price, chgPct: _klineLive.chgPct,
      marketClosed: _klineLive.marketClosed, closedApplied: _klineLive.closedApplied,
      updates: _klineLive.updates, appends: _klineLive.appends, fallbacks: _klineLive.fallbacks,
      hasStream: !!_klineLive.stream, polling: !!_klineLive.pollTimer, retrying: !!_klineLive.retryTimer,
      streamStats: (_klineLive.stream && typeof _klineLive.stream.stats === 'function')
        ? _klineLive.stream.stats() : null,
      lastRenderAt: _klineLive.lastRenderAt,
      chartType: (typeof chartType !== 'undefined' ? chartType : null),
      proDisabled: window._proChartDisabled === true,
      hasProChart: (typeof proChart !== 'undefined' && !!proChart),
      hasRealtimeCb: (typeof proRealtimeCb === 'function')
    };
  },
  lastBar: function () {
    var bars = (typeof proDataStore !== 'undefined' && proDataStore && proDataStore.bars) ? proDataStore.bars : [];
    if (!bars.length) return null;
    var b = bars[bars.length - 1];
    return { timestamp: b.timestamp, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume };
  },
  lastClose: _klineLiveLastClose,
  badge: function () {
    var el = document.getElementById('kline-live-badge');
    if (!el) return null;
    return { text: (el.textContent || '').replace(/\s+/g, ' ').trim(), state: el.getAttribute('data-state'), title: el.getAttribute('title') };
  },
  /** 模拟一封 quotes.tick 信封（QA 用；未激活/页面隐藏时返回 ok:false）
   *  opts.degraded=true 时信封带 degraded，用于验证「先 code 过滤、后降级」与自愈链路
   */
  inject: function (quote, opts) {
    if (!_klineLive.active) return { ok: false, reason: 'inactive' };
    if (typeof document !== 'undefined' && document.hidden) return { ok: false, reason: 'hidden' };
    opts = opts || {};
    var before = _klineLiveLastClose();
    var hit = _klineLiveHandleEnvelope({
      topic: 'quotes', event: 'quotes.tick', source: 'stub',
      degraded: opts.degraded === true, reason: opts.reason || null,
      seq: opts.seq,
      data: { quotes: [quote], codes: [quote && quote.code] }
    });
    var applied = _klineLiveFlush(true);
    return {
      ok: !!applied, matched: !!hit, before: before, after: _klineLiveLastClose(),
      status: _klineLive.status, updates: _klineLive.updates, appends: _klineLive.appends
    };
  },
  start: function (code, period) { startKlineLive(code, period, true); return window.klineLiveDebug.state(); },
  /** 分时视图只读快照（QA：验证渲染/实时/校准状态） */
  timeshare: function () {
    var seriesCount = -1;
    try {
      if (klineChart && !klineChart.isDisposed()) {
        var opt = klineChart.getOption();
        seriesCount = (opt && opt.series) ? opt.series.length : -1;
      }
    } catch (e) { seriesCount = -1; }
    return {
      code: _tsState.code, date: _tsState.date, points: _tsState.points.length,
      source: _tsState.source, degraded: _tsState.degraded, reason: _tsState.reason,
      prevClose: _tsState.prevClose, avgEstimated: _tsState.avgEstimated,
      last: _tsState.points.length ? _tsState.points[_tsState.points.length - 1] : null,
      lastSlot: (function () {
        var pts = _tsState.points, out = null;
        for (var i = 0; i < pts.length; i++) {
          if (_tsSlotIndex[pts[i].t] != null) out = pts[i];
        }
        return out;
      })(),
      renders: _tsState.renders, calibrating: !!_tsState.calibTimer,
      applied: _tsState.applied, outOfOrder: _tsState.outOfOrder,
      rejected: _tsState.rejected.slice(),
      volMax: _tsState.volMax, volP90: _tsState.volP90, volP50: _tsState.volP50,
      volCap: _tsState.volCap, volTruncated: _tsState.volTruncated,
      lastApplied: _tsState.lastApplied || null,
      chartType: (typeof chartType !== 'undefined' ? chartType : null),
      seriesCount: seriesCount
    };
  },
  /** 把一封事件原样喂给实时事件处理器（QA 用，走生产分支：code 过滤 / 降级 / 自愈） */
  dispatch: function (name, payload) {
    try { _klineLiveOnStreamEvent(name, payload); return true; }
    catch (e) { return false; }
  },
  stop: function () { stopKlineLive(); return window.klineLiveDebug.state(); }
};

// 供页面/QA 读取当前买卖点状态（只读，不修改）
window.klineSignalSnapshot = function () {
  var list = [];
  for (var i = 0; i < _klineSignal.points.length; i++) {
    var p = _klineSignal.points[i];
    list.push({ kind: p.kind, ts: p.ts, price: p.price, qty: p.qty, pnl: p.pnl, exit_reason: p.exit_reason, is_drill: !!p.is_drill });
  }
  return {
    code: _klineSignal.code, source: _klineSignal.source, status: _klineSignal.status,
    note: _klineSignal.note, includeDrill: _klineSignal.includeDrill,
    points: list, matchedCount: _klineSignal.matchedCount,
    unmatchedCount: _klineSignal.unmatched.length,
    overlays: (typeof proChart !== 'undefined' && proChart && proChart.getOverlays)
      ? (function () { try { return (proChart.getOverlays({ name: 'dapengSignal' }) || []).length; } catch (e) { return -1; } })()
      : -1
  };
};
