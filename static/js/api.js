/**
 * DAPENG Alpha 策略选股系统 - API 请求封装 v6
 *
 * v6.0 升级：
 * - fetchScreen 改为 opts 对象模式，支持 minVol/minTurnover/maxPe
 * - 新增 fetchSectorFundFlow(sector) 板块资金流请求封装
 * - 请求去重：同一 URL 的并发请求只发一次
 * - AbortController：切换板块/股票时取消未完成的请求
 * - 超时控制：防止请求无限挂起
 * - 智能刷新：盘后降低刷新频率
 */

const API_BASE = '';  // 同源，无需前缀

// ================================================================
//  工具函数
// ================================================================

/**
 * HTML 转义 — 防止 XSS，所有用户数据插入 DOM 前必须转义
 */
function escapeHtml(text) {
  if (text == null) return '';
  const div = document.createElement('div');
  div.textContent = String(text);
  return div.innerHTML;
}

/**
 * 安全 JSON 解析 — 数据损坏时返回默认值，不抛异常
 */
function safeJsonParse(raw, fallback) {
  try { return JSON.parse(raw); }
  catch { return fallback; }
}

/**
 * 安全 toFixed — 处理 null/undefined
 */
function safeFixed(val, digits) {
  if (val == null || isNaN(val)) return '--';
  return Number(val).toFixed(digits || 2);
}

const API = {
  quotes: (sector, fast) => `${API_BASE}/api/quotes?sector=${encodeURIComponent(sector || 'semiconductor')}${fast ? '&fast=1' : ''}`,
  kline: (code, period) => `${API_BASE}/api/kline?code=${encodeURIComponent(code)}&period=${encodeURIComponent(period || 'day')}`,
  global: `${API_BASE}/api/global`,
  search: (keyword) => `${API_BASE}/api/search?keyword=${encodeURIComponent(keyword)}`,
  screen: (opts) => {
    opts = opts || {};
    let url = `${API_BASE}/api/screen?min_score=${opts.minScore ?? 70}&limit=${opts.limit ?? 20}`;
    if (opts.minChg != null) url += `&min_chg=${opts.minChg}`;
    if (opts.minVol != null) url += `&min_vol=${opts.minVol}`;
    if (opts.minTurnover != null) url += `&min_turnover=${opts.minTurnover}`;
    if (opts.maxPe != null) url += `&max_pe=${opts.maxPe}`;
    if (opts.sector) url += `&sector=${encodeURIComponent(opts.sector)}`;
    return url;
  },
  health: `${API_BASE}/api/health`,
  analyze: (code) => `${API_BASE}/api/analyze?code=${encodeURIComponent(code)}`,
  scan: `${API_BASE}/api/scan`,
  scanStatus: `${API_BASE}/api/scan/status`,
  realtime: (codes) => `${API_BASE}/api/realtime?codes=${encodeURIComponent(codes)}`,
  // ---- v5.0 MCP 市场数据端点 ----
  fundFlow: (code) => `${API_BASE}/api/fund-flow?code=${encodeURIComponent(code)}`,
  sectorFundFlow: (sector) => `${API_BASE}/api/fund-flow/sector?sector=${encodeURIComponent(sector)}`,
  sectorRanking: () => `${API_BASE}/api/sector-ranking`,
  lhb: (tab, date) => `${API_BASE}/api/lhb?tab=${encodeURIComponent(tab||'jg')}${date ? '&date='+encodeURIComponent(date) : ''}`,
  marketOverview: () => `${API_BASE}/api/market-overview`,
  hotStocks: (type, limit) => `${API_BASE}/api/hot-stocks?type=${encodeURIComponent(type||'stock')}&limit=${limit||10}`,
  profile: (code) => `${API_BASE}/api/profile?code=${encodeURIComponent(code)}`,
  finance: (code, type) => `${API_BASE}/api/finance?code=${encodeURIComponent(code)}${type ? '&type='+encodeURIComponent(type) : ''}`,
  report: (code, limit) => `${API_BASE}/api/report?code=${encodeURIComponent(code)}&limit=${limit||10}`,
  consensus: (code) => `${API_BASE}/api/consensus?code=${encodeURIComponent(code)}`,
  northHolding: (market) => `${API_BASE}/api/north-holding?market=${encodeURIComponent(market||'sh')}`,
  news: (code, limit) => `${API_BASE}/api/news?code=${encodeURIComponent(code)}&limit=${limit||10}`,
  marketNews: (limit) => `${API_BASE}/api/news/market?limit=${limit||20}`,
  sectorStocks: (sector) => `${API_BASE}/api/sector-stocks?sector=${encodeURIComponent(sector)}`,
  strategyLab: (code, period) => `${API_BASE}/api/strategy-lab/evaluate?code=${encodeURIComponent(code)}&period=${encodeURIComponent(period || 'day')}`,
  marketDashboard: `${API_BASE}/api/market-dashboard`,
};

// ================================================================
//  请求去重 + 超时控制 + K线请求取消 + sessionStorage 缓存
// ================================================================
const _pendingRequests = new Map();
let _klineAbortController = null;
const KLINE_CACHE_TTL = 5 * 60 * 1000; // 5 分钟

/**
 * 取消当前正在进行的K线请求
 */
function cancelPendingKline() {
  if (_klineAbortController) {
    _klineAbortController.abort();
    _klineAbortController = null;
  }
}

/**
 * 从 sessionStorage 读取 K 线缓存
 */
function _getKlineCache(key) {
  try {
    const raw = sessionStorage.getItem('kl_' + key);
    if (!raw) return null;
    const entry = JSON.parse(raw);
    if (Date.now() - entry.ts > KLINE_CACHE_TTL) {
      sessionStorage.removeItem('kl_' + key);
      return null;
    }
    return entry.data;
  } catch { return null; }
}

/**
 * 写入 K 线缓存到 sessionStorage
 */
function _setKlineCache(key, data) {
  try {
    sessionStorage.setItem('kl_' + key, JSON.stringify({ data, ts: Date.now() }));
  } catch { /* storage full, ignore */ }
}

/**
 * 通用请求封装
 * - 同 URL 并发请求去重
 * - 超时控制（默认 15 秒）
 * - 请求失败静默降级
 */
async function request(url, options = {}) {
  const timeout = options.timeout || 15000;

  // 请求去重：如果同一 URL 有正在进行的请求，复用 Promise
  if (_pendingRequests.has(url)) {
    return _pendingRequests.get(url);
  }

  const promise = _doRequest(url, options, timeout);
  _pendingRequests.set(url, promise);

  try {
    const result = await promise;
    return result;
  } finally {
    _pendingRequests.delete(url);
  }
}

async function _doRequest(url, options, timeout) {
  const maxRetries = options.retries !== undefined ? options.retries : 1;
  let lastError;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);

    try {
      const response = await fetch(url, {
        ...options,
        retries: undefined,
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          ...options.headers,
        },
      });

      if (!response.ok) {
        console.warn(`API HTTP error: ${response.status} ${url}`);
        lastError = new Error('HTTP ' + response.status);
        if (attempt < maxRetries) {
          await new Promise(r => setTimeout(r, 2000));
          continue;
        }
        return null;
      }

      const json = await response.json();

      if (!json.ok) {
        console.warn(`API business error: ${json.message || 'unknown'} ${url}`);
        return null;
      }

      return json.data;
    } catch (e) {
      if (e.name === 'AbortError') {
        console.warn(`API timeout: ${url} (attempt ${attempt + 1}/${maxRetries + 1})`);
      } else {
        console.warn(`API request error: ${url} (attempt ${attempt + 1}/${maxRetries + 1})`, e);
      }
      lastError = e;
      if (attempt < maxRetries) {
        await new Promise(r => setTimeout(r, 2000));
        continue;
      }
      return null;
    } finally {
      clearTimeout(timer);
    }
  }
  return null;
}

/**
 * 与 request() 类似，但返回**完整信封**（含 source / degraded / total）。
 *
 * 供需要展示"数据源降级"提示的调用方使用：信封里的 degraded 由后端依据
 * 数据真实来源推导，前端据此显示/隐藏降级横幅，绝不臆测。
 * @returns {Promise<{data:any, rows:any, source:string, degraded:boolean, total:number}|null>}
 */
async function requestEnvelope(url, options = {}) {
  const timeout = options.timeout || 15000;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, {
      ...options,
      retries: undefined,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
    });
    if (!response.ok) {
      console.warn(`API HTTP error (envelope): ${response.status} ${url}`);
      return null;
    }
    const json = await response.json();
    if (!json.ok) {
      console.warn(`API business error (envelope): ${json.message || 'unknown'} ${url}`);
      return null;
    }
    return {
      data: json.data,
      rows: json.rows,
      source: json.source,
      degraded: json.degraded === true,
      total: json.total,
    };
  } catch (e) {
    console.warn(`API request error (envelope): ${url}`, e);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// ================================================================
//  业务 API 封装
// ================================================================

/**
 * 获取板块行情数据
 * @param {string} sector - 板块key
 * @param {boolean} fast - 快速模式：只取行情不等K线策略（首屏加速用）
 */
async function fetchQuotes(sector, fast) {
  return request(API.quotes(sector, fast));
}

/**
 * 获取 K 线数据 — 支持请求取消 + sessionStorage 缓存
 */
async function fetchKline(code, period) {
  // 先检查 sessionStorage 缓存
  const cacheKey = `${code}:${period}`;
  const cached = _getKlineCache(cacheKey);
  if (cached) return cached;

  // 每次请求K线时创建新的 AbortController
  cancelPendingKline();
  _klineAbortController = new AbortController();
  const controller = _klineAbortController;
  const url = API.kline(code, period);

  const timeout = 15000;  // K线超时15秒

  try {
    const timer = setTimeout(() => controller.abort(), timeout);
    const response = await fetch(url, {
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
    });
    clearTimeout(timer);

    if (!response.ok) {
      console.warn(`K线 API HTTP error: ${response.status}`);
      return null;
    }

    const json = await response.json();
    if (!json.ok) {
      console.warn(`K线 API business error: ${json.message || 'unknown'}`);
      return null;
    }

    // 写入缓存
    if (json.data) _setKlineCache(cacheKey, json.data);
    return json.data;
  } catch (e) {
    if (e.name === 'AbortError') {
      console.log(`K线请求已取消: ${code}`);
    } else {
      console.warn(`K线请求错误: ${code}`, e);
    }
    return null;
  } finally {
    if (_klineAbortController === controller) {
      _klineAbortController = null;
    }
  }
}

/**
 * 获取全球市场数据
 */
async function fetchGlobal() {
  return request(API.global);
}

/**
 * 搜索股票（跨板块）
 */
async function fetchSearch(keyword) {
  return request(API.search(keyword), { timeout: 10000 });
}

/**
 * 条件选股 — v6 增强版，接收 opts 对象
 * @param {Object} opts - 筛选条件 {minScore, minChg, minVol, minTurnover, maxPe, sector, limit}
 */
async function fetchScreen(opts) {
  return request(API.screen(opts), { timeout: 40000 });
}

/**
 * 获取实时分析总结 — 带版本号校验
 */
async function fetchAnalyze(code, version) {
  return request(API.analyze(code), { timeout: 30000 });
}

/**
 * 全市场智能选股扫描
 */
async function fetchScan() {
  // 首次请求触发扫描（可能返回 pending 状态）
  let resp = await request(API.scan, { timeout: 10000 });
  if (!resp) return null;

  // 如果扫描完成，直接返回数据
  if (!resp.pending || resp.data) return resp;

  // 轮询等待扫描完成，每2秒查询一次，最多等90秒
  const maxWait = 90000;
  const pollInterval = 2000;
  const startTime = Date.now();

  while (Date.now() - startTime < maxWait) {
    await new Promise(r => setTimeout(r, pollInterval));
    resp = await request(API.scan, { timeout: 10000 });
    if (!resp) return null;
    if (!resp.pending || resp.data) return resp;
  }

  return null; // 超时
}

/**
 * 健康检查
 */
async function fetchHealth() {
  return request(API.health, { timeout: 5000 });
}

/**
 * 腾讯实时行情
 */
async function fetchRealtime(codes) {
  if (!codes || codes.length === 0) return null;
  return request(API.realtime(codes.join(',')), { timeout: 4000 });
}

// ================================================================
//  智能刷新间隔
// ================================================================

/**
 * 判断是否在交易时间（A 股 9:30-15:00 工作日）
 */
function isTradingHours() {
  const now = new Date();
  const day = now.getDay();
  const h = now.getHours();
  const m = now.getMinutes();
  const timeVal = h * 100 + m;

  // 周末
  if (day === 0 || day === 6) return false;
  // 9:30 - 11:30 或 13:00 - 15:00
  if ((timeVal >= 930 && timeVal <= 1130) || (timeVal >= 1300 && timeVal <= 1500)) {
    return true;
  }
  return false;
}

/**
 * 获取推荐的刷新间隔（毫秒）
 */
function getSmartRefreshInterval() {
  return isTradingHours() ? 30000 : 120000;  // 盘中30秒，盘后2分钟
}

// ================================================================
//  v5.0 MCP 市场数据 API 封装（13个函数）
// ================================================================

/**
 * 个股资金流向
 */
async function fetchFundFlow(code) {
  return request(API.fundFlow(code), { timeout: 10000 });
}

/**
 * 板块排行
 */
async function fetchSectorRanking(type) {
  return request(API.sectorRanking(), { timeout: 10000 });
}

/**
 * 龙虎榜
 */
async function fetchLHB(tab, date) {
  return request(API.lhb(tab, date), { timeout: 10000 });
}

/**
 * 市场涨跌分布
 */
async function fetchMarketOverview() {
  return request(API.marketOverview(), { timeout: 10000 });
}

/**
 * 热门股
 */
async function fetchHotStocks(type, limit) {
  return request(API.hotStocks(type, limit), { timeout: 10000 });
}

/**
 * 板块排行（含信封）— 供数据源降级横幅判断
 * 超时放宽到 45s：后端在东财 push2 不可达时会先轮询 3 个节点（各 6s，经代理更久），
 * 再走腾讯成分股聚合（~4s），冷调用可达 ~30s，避免前端过早中止拿不到 degraded。
 * 成功结果由后端/路由缓存（≥60s），后续调用为毫秒级。
 */
async function fetchSectorRankingEnvelope() {
  return requestEnvelope(API.sectorRanking(), { timeout: 45000 });
}

/**
 * 龙虎榜（含信封）— 供数据源降级横幅判断
 * 超时放宽：无日期时后端需先探测最近交易日再取数（最多 2 次请求 × 6s）。
 */
async function fetchLHBEnvelope(tab, date) {
  return requestEnvelope(API.lhb(tab, date), { timeout: 20000 });
}

/**
 * 热门股（含信封）— 供数据源降级横幅判断（同板块排行的多节点超时考量）
 */
async function fetchHotStocksEnvelope(type, limit) {
  return requestEnvelope(API.hotStocks(type, limit), { timeout: 20000 });
}

/**
 * 个股简况
 */
async function fetchProfile(code) {
  return request(API.profile(code), { timeout: 10000 });
}

/**
 * 财务报表
 */
async function fetchFinance(code, type) {
  return request(API.finance(code, type), { timeout: 10000 });
}

/**
 * 研报列表
 */
async function fetchReport(code, limit) {
  return request(API.report(code, limit), { timeout: 10000 });
}

/**
 * 机构评级 + 一致预期
 */
async function fetchConsensus(code) {
  return request(API.consensus(code), { timeout: 10000 });
}

/**
 * 沪深股通成份股
 */
async function fetchNorthHolding(code) {
  return request(API.northHolding(code), { timeout: 10000 });
}

/**
 * 个股新闻资讯
 */
async function fetchNews(code, limit) {
  return request(API.news(code, limit), { timeout: 10000 });
}

/**
 * 市场资讯
 */
async function fetchMarketNews(limit) {
  return request(API.marketNews(limit), { timeout: 10000 });
}

/**
 * 板块成分股
 */
async function fetchStrategyLab(code, period) {
  return request(API.strategyLab(code, period), { timeout: 12000, retries: 0 });
}

async function fetchMarketDashboard() {
  return request(API.marketDashboard, { timeout: 30000, retries: 0 });
}

async function fetchSectorStocks(sector) {
  return request(API.sectorStocks(sector), { timeout: 10000 });
}

/**
 * 板块资金流向 — v6 新增
 * @param {string} sector - 板块英文 key
 */
async function fetchSectorFundFlow(sector) {
  return request(API.sectorFundFlow(sector), { timeout: 10000 });
}
