/**
 * T10 验收脚本（真实浏览器）：12 指标切换 / 请求计数 / overlay 不改主图 Y 轴 / 参数标签
 * 用法：node tools/dp-t10-verify.js [code]
 */
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8900';
const CODE = process.argv[2] || 'sh600519';
const NAMES = ['supertrend', 'ichimoku', 'donchian', 'keltner', 'vwap', 'adx', 'aroon', 'obv', 'mfi', 'atr', 'stoch', 'cci'];
const OVERLAY = ['supertrend', 'ichimoku', 'donchian', 'keltner', 'vwap'];

(async () => {
  // 浏览器可执行文件走 playwright 默认解析；本机若装的是非默认版本，
  // 用环境变量 PW_EXECUTABLE 覆盖（不要把个人机器路径写进仓库）。
  const EXE = process.env.PW_EXECUTABLE || undefined;
  const browser = await chromium.launch(EXE ? { executablePath: EXE } : {});
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const indReqs = [];
  page.on('request', (r) => { if (r.url().includes('/api/kline/indicators')) indReqs.push(r.url()); });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));

  await page.goto(BASE + '/dashboard.html', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(1500);

  // 选中标的（走真实交互入口）
  await page.evaluate(async (code) => { await selectStock(code, code); }, CODE);
  await page.waitForTimeout(2500);

  const engine = await page.evaluate(() => ({
    proDisabled: window._proChartDisabled === true,
    hasPro: typeof proChart !== 'undefined' && !!proChart,
    hasEcharts: typeof klineChart !== 'undefined' && !!klineChart,
    bars: (_dpCurrentKlines() || []).length,
    btnCount: document.querySelectorAll('[data-sub-indicator]').length,
    extraBtns: _DP_EXTRA_NAMES.filter((n) => !!document.getElementById('ibt-' + n)).length,
    overlayBtns: _DP_EXTRA_NAMES.filter((n) => {
      const b = document.getElementById('ibt-' + n); return b && b.getAttribute('data-ind-overlay') === 'true';
    }),
    catalog: Object.keys(window._dpInd.meta).length
  }));
  console.log('ENV', JSON.stringify(engine));

  // ---------- 逐个切换 12 个指标 ----------
  const rows = [];
  for (const name of NAMES) {
    const before = indReqs.length;
    const t0 = Date.now();
    await page.click('#ibt-' + name);
    await page.waitForTimeout(700);
    const info = await page.evaluate((nm) => {
      const cfg = window._indicatorConfig || {};
      const pro = (typeof proChart !== 'undefined' && proChart) ? proChart : null;
      let indicatorNames = [];
      if (pro) { try { indicatorNames = (pro.getIndicators() || []).map((i) => i.name + '@' + (i.paneId || '')); } catch (e) {} }
      const meta = _dpMeta(nm) || {};
      const series = _dpSeriesOf(nm);
      const firstPane = (meta.panes || [])[0];
      const vals = (series.panes || {})[firstPane] || [];
      return {
        sub: cfg.subIndicator,
        label: _dpLabel(nm),
        overlay: !!meta.overlay,
        paneCount: Object.keys(series.panes || {}).length,
        len: vals.length,
        nonnull: vals.filter((v) => v != null).length,
        indicatorNames,
        reqCount: window._dpInd.reqCount,
        jsRuns: window._dpInd.jsComputeCount
      };
    }, name);
    rows.push({ name, ms: Date.now() - t0, newReq: indReqs.length - before, ...info });
    console.log('SWITCH', name, 'label=' + info.label, 'overlay=' + info.overlay,
      'panes=' + info.paneCount, 'len=' + info.len, 'nonnull=' + info.nonnull,
      'pro=[' + info.indicatorNames.join(',') + ']', 'newReq=' + (indReqs.length - before), 'ms=' + (Date.now() - t0));
  }

  // ---------- 重复切回已看过的指标：不应产生新请求 ----------
  const beforeRepeat = indReqs.length;
  for (const name of ['adx', 'supertrend', 'adx']) {
    await page.click('#ibt-' + name);
    await page.waitForTimeout(400);
  }
  const repeatNew = indReqs.length - beforeRepeat;
  console.log('REPEAT_SWITCH newRequests =', repeatNew, '(期望 0)', JSON.stringify(indReqs.slice(beforeRepeat)));

  // ---------- 主图 Y 轴：ECharts 路径 getOption().yAxis[0] 对比 ----------
  // 强制走 ECharts 兼容路径（Pro 引擎没有 getOption，验收断言按 ECharts 口径给数字）
  await page.evaluate(() => { window._proChartDisabled = true; });
  await page.evaluate(async () => { await selectStock(window.appState.activeCode, window.appState.activeName || window.appState.activeCode); });
  // 等 legacy ECharts 实例就绪（initChart 有最多 20 次 × 300ms 的重试）
  await page.waitForFunction(() => typeof klineChart !== 'undefined' && klineChart && !klineChart.isDisposed(), null, { timeout: 20000 });
  await page.waitForTimeout(1200);

  const axisOf = () => page.evaluate(() => {
    if (!klineChart || klineChart.isDisposed()) return null;
    const op = klineChart.getOption();
    const y = op.yAxis[0];
    return { min: y.min, max: y.max, yMin: y.min != null ? y.min : null };
  });

  await page.click('#ibt-macd');
  await page.waitForTimeout(800);
  const axisBase = await axisOf();
  const axisCases = {};
  for (const name of OVERLAY) {
    await page.click('#ibt-' + name);
    await page.waitForTimeout(700);
    axisCases[name] = await axisOf();
    const seriesInfo = await page.evaluate((nm) => {
      const op = klineChart.getOption();
      return (op.series || []).filter((s) => s._dpIndicator === nm).map((s) => ({ name: s.name, y: s.yAxisIndex, n: (s.data || []).filter((v) => v != null).length }));
    }, name);
    console.log('AXIS', name, JSON.stringify(axisCases[name]), 'series=', JSON.stringify(seriesInfo));
  }
  console.log('AXIS_BASE(macd) =', JSON.stringify(axisBase));
  const axisEqual = Object.keys(axisCases).every((k) => axisCases[k] && axisBase && axisCases[k].min === axisBase.min && axisCases[k].max === axisBase.max);
  console.log('AXIS_UNCHANGED =', axisEqual, '(叠加前后 min/max 应逐位相等)');

  // ---------- 盘中 tick：本地重算，不产生请求 ----------
  await page.click('#ibt-adx');
  await page.waitForTimeout(900);
  const tickResult = await page.evaluate(async () => {
    const before = window._dpInd.reqCount;
    const jsBefore = window._dpInd.jsComputeCount;
    const kls = _dpCurrentKlines();
    const last = kls[kls.length - 1];
    const price = Number(last.c) * 1.01;
    // 模拟实时模块调用（updateLastCandle 已被 v35 包装，末尾追加本地重算）
    window.updateLastCandle(price, Number(last.h) + 1, Number(last.l), Number(last.v) + 100);
    await new Promise((r) => setTimeout(r, 300));
    const op = klineChart.getOption();
    const s = (op.series || []).filter((x) => x._dpIndicator === 'adx');
    return {
      reqBefore: before, reqAfter: window._dpInd.reqCount,
      jsBefore: jsBefore, jsAfter: window._dpInd.jsComputeCount,
      lastTick: window._dpInd.lastTickLocal,
      lastValues: s.map((x) => ({ pane: x._dpPane, last: (x.data || [])[(x.data || []).length - 1] })),
      axisAfter: (() => { const y = op.yAxis[0]; return { min: y.min, max: y.max }; })()
    };
  });
  console.log('TICK', JSON.stringify(tickResult));

  // ---------- 截图 ----------
  await page.click('#ibt-supertrend'); await page.waitForTimeout(800);
  await page.locator('#kline-card').screenshot({ path: 'shots/t10-supertrend.png' });
  await page.click('#ibt-adx'); await page.waitForTimeout(800);
  await page.locator('#kline-card').screenshot({ path: 'shots/t10-adx.png' });
  await page.click('#ibt-ichimoku'); await page.waitForTimeout(800);
  await page.locator('#kline-card').screenshot({ path: 'shots/t10-ichimoku.png' });

  console.log('ERRORS', JSON.stringify(errors.slice(0, 5)));
  console.log('REQ_TOTAL', indReqs.length);
  await browser.close();
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
