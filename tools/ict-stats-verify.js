/**
 * v37 验收（同步断言版）：每次断言前在同一个 evaluate 内重放 pinned 渲染，
 * 避免 app 层自动刷新把标的换走导致读到别的 code 的缓存。
 */
const { chromium } = require('playwright');
const EXE = '%LOCALAPPDATA%\\ms-playwright\\chromium_headless_shell-1223\\chrome-headless-shell-win64\\chrome-headless-shell.exe';
const CODE = process.argv[2] || 'sh600519';

const strip = (h) => String(h).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();

(async () => {
  const b = await chromium.launch({ executablePath: EXE });
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 } });
  const reqs = [];
  const errs = [];
  p.on('request', (r) => {
    const u = r.url();
    if (u.includes('/api/ict/')) reqs.push(u.replace(/^.*\/api\/ict\//, 'ict/'));
  });
  p.on('pageerror', (e) => errs.push('PAGE:' + String(e).slice(0, 140)));
  p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push('CON:' + m.text().slice(0, 140)); });

  await p.goto('http://127.0.0.1:8900/dashboard.html?v38', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(2500);

  // 取一份 K 线，打桩 app 层选股/加载，之后所有断言都在同一 evaluate 内重放渲染
  const DATA = await p.evaluate(async (c) => (await (await fetch('/api/kline?code=' + c + '&period=day&limit=250')).json()).data, CODE);
  await p.evaluate(async (d) => {
    window.selectStock = async function () {};
    window.loadAndRenderKline = async function () {};
    window.__pin = d;
    try { localStorage.removeItem('dapeng_ict_on'); } catch (e) {}
    window._ict.on = false;
    window.appState.activeCode = d.code;
    window.appState.period = 'day';
    window.appState.klineCache[d.code + ':day'] = d;
    renderKline(d);
  }, DATA);
  await p.waitForTimeout(1200);
  const ctx = await p.evaluate(() => ({ proSymbol: proSymbol, ctx: { code: _dpRenderCtx.code, bars: _dpRenderCtx.klines.length } }));
  console.log('PINNED', JSON.stringify(ctx));
  const axisOf = () => p.evaluate(() => {
    const a = proChart.convertToPixel({ value: 1400 }, { paneId: 'candle_pane' });
    const c2 = proChart.convertToPixel({ value: 1200 }, { paneId: 'candle_pane' });
    const y = proChart.convertToPixel({ value: 1300 }, { paneId: 'candle_pane' });
    return { perPx: +(200 / Math.abs(a.y - c2.y)).toFixed(6), y1300: y.y };
  });
  await p.evaluate(() => renderKline(window.__pin));
  await p.waitForTimeout(1300);
  const axisOff = await axisOf();

  // ---------- 1) 打开开关：并发请求 ----------
  await p.evaluate(() => {
    // 清缓存，保证本轮是「冷启动」：scan/两种请求都真打上游，才能测出串行/并行差异
    window._ict.cache = {};
    window._ict.statsCache = {};
    window._ict.timing = {};
  });
  const before = reqs.length;
  await p.click('#ibt-ict');
  await p.waitForTimeout(1200);
  const early = await p.evaluate(() => {
    _ictRenderPro();                      // 只重画 overlay，不动 K 线/轴
    return {
      overlays: (proChart.getOverlays() || []).filter((x) => x.name === 'dpIctZone' || x.name === 'dpIctMark').length,
      statsReady: !!_ictStatsData(),
      key: _ictKey()
    };
  });
  await p.waitForFunction(() => { renderKline(window.__pin); return !!_ictStatsData(); }, null, { timeout: 90000 }).catch(() => {});
  await p.waitForTimeout(300);
  const timing = await p.evaluate((c) => ({ all: window._ict.timing, key: c + ':day' }), CODE);
  const t = timing.all[timing.key] || {};
  console.log('REQS_ON', JSON.stringify(reqs.slice(before)));
  console.log('EARLY_DRAW overlays=', early.overlays, 'statsReady=', early.statsReady, 'key=', early.key, '(overlays>0 且 statsReady=false => 统计不阻塞绘制)');
  console.log('TIMING', JSON.stringify(t));
  const axisOn = await axisOf();   // 与 axisOff 同一渲染上下文（未重放 renderKline）
  console.log('AXIS off=', JSON.stringify(axisOff), 'on=', JSON.stringify(axisOn),
    'SAME=', JSON.stringify(axisOff) === JSON.stringify(axisOn));
  console.log('CONCURRENT =', !!(t.scan && t.stats && t.stats.start < (t.scan.done || Infinity)),
    '| stats.start', t.stats && t.stats.start, '< scan.done', t.scan && t.scan.done,
    '| scan', t.scan ? (t.scan.done - t.scan.start) + 'ms' : '-',
    '| stats', t.stats ? (t.stats.done - t.stats.start) + 'ms' : '-');

  // ---------- 1b) 统计慢 3s 时：标注先画、统计后补（不阻塞的硬证明） ----------
  await p.route('**/api/ict/structure-stats**', async (route) => {
    await new Promise((r) => setTimeout(r, 3000));
    try { await route.continue(); } catch (e) {}
  });
  await p.evaluate(() => { window._ict.cache = {}; window._ict.statsCache = {}; window._ict.timing = {}; _ictClearPro(); _ictHideTip(); });
  await p.click('#ibt-ict');   // 关
  await p.waitForTimeout(500);
  await p.click('#ibt-ict');   // 开（此时 stats 被延迟 3s）
  await p.waitForTimeout(1300);
  const nonBlock = await p.evaluate(() => {
    renderKline(window.__pin); _ictRenderPro();
    return {
      overlays: (proChart.getOverlays() || []).filter((x) => x.name === 'dpIctZone' || x.name === 'dpIctMark').length,
      statsReady: !!_ictStatsData(),
      statsInflight: Object.keys(window._ict.statsInflight).length,
      tipHasStats: /同类 FVG 历史|OB 历史回踩率|同类扫单历史/.test(_ictStatsBlock('FVG') + _ictStatsBlock('OB') + _ictStatsBlock('SWEEP'))
    };
  });
  console.log('NONBLOCK overlays=', nonBlock.overlays, 'statsReady=', nonBlock.statsReady,
    'inflight=', nonBlock.statsInflight, '=> 标注先画、统计后补 =', nonBlock.overlays > 0 && !nonBlock.statsReady);
  await p.waitForFunction(() => !!(_ictStatsData && _ictStatsData()), null, { timeout: 20000 }).catch(() => {});
  const afterStats = await p.evaluate(() => {
    renderKline(window.__pin);
    return { statsReady: !!_ictStatsData(), fvg: String(_ictStatsHtml('FVG')).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 90) };
  });
  console.log('AFTER_STATS', JSON.stringify(afterStats));
  await p.unroute('**/api/ict/structure-stats**');

  // ---------- 2) 三块文案（同步重放后读取） ----------
  const tips = await p.evaluate(() => {
    renderKline(window.__pin);
    return {
      fvg: _ictStatsHtml('FVG'), ob: _ictStatsHtml('OB'), sweep: _ictStatsHtml('SWEEP'),
      notPredict: (_ictStatsHtml('FVG') + _ictStatsHtml('OB') + _ictStatsHtml('SWEEP')).split('历史频率，非预测').length - 1,
      edge: (_ictStatsData() || {}).edge, candles: (_ictStatsData() || {}).candles
    };
  });
  console.log('TIP_FVG   :', strip(tips.fvg));
  console.log('TIP_OB    :', strip(tips.ob));
  console.log('TIP_SWEEP :', strip(tips.sweep));
  console.log('NOT_PREDICT_COUNT =', tips.notPredict, '(应=3)  candles=', tips.candles, 'edge=', JSON.stringify(tips.edge));

  // ---------- 3) 真实 DOM tooltip（FVG / sweep） ----------
  const domFvg = await p.evaluate(() => {
    renderKline(window.__pin); _ictRenderPro();
    const ov = (proChart.getOverlays() || []).find((o) => o.name === 'dpIctZone' && (o.extendData || {}).type === 'FVG');
    _ictShowTip({ x: 240, y: 240 }, (ov || {}).extendData || {});
    const el = document.getElementById('dp-ict-tip');
    return { found: !!ov, display: el.style.display, text: el.innerText.replace(/\s+/g, ' ') };
  });
  console.log('DOM_TIP_FVG  :', JSON.stringify(domFvg.text.slice(0, 460)));
  const domSweep = await p.evaluate(() => {
    const ov = (proChart.getOverlays() || []).find((o) => o.name === 'dpIctMark');
    _ictShowTip({ x: 240, y: 240 }, (ov || {}).extendData || {});
    return document.getElementById('dp-ict-tip').innerText.replace(/\s+/g, ' ');
  });
  console.log('DOM_TIP_SWEEP:', JSON.stringify(domSweep.slice(0, 460)));
  const domOb = await p.evaluate(() => {
    const ov = (proChart.getOverlays() || []).find((o) => o.name === 'dpIctZone' && (o.extendData || {}).type === 'OB');
    _ictShowTip({ x: 240, y: 240 }, (ov || {}).extendData || {});
    return document.getElementById('dp-ict-tip').innerText.replace(/\s+/g, ' ');
  });
  console.log('DOM_TIP_OB   :', JSON.stringify(domOb.slice(0, 460)));

  // ---------- 4) 样本不足（真实 limit=120 -> sweep samples=4） ----------
  const small = await p.evaluate(async (c) => {
    renderKline(window.__pin);
    const r = await fetch('/api/ict/structure-stats?code=' + c + '&period=day&limit=120').then((x) => x.json());
    const d = r && r.data ? r.data : null;
    if (!d) return { err: (r && r.message) || 'no data' };
    const key = _ictKey();
    const keep = window._ict.statsCache[key];
    window._ict.statsCache[key] = { data: d, fetchedAt: Date.now() };
    const out = {
      samples: { fvg: d.fvg.overall.samples, sweep: d.sweep.overall.samples, ob: d.ob.overall.samples },
      fvgText: String(_ictStatsHtml('FVG')).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim(),
      sweepText: String(_ictStatsHtml('SWEEP')).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim(),
      fvgHasPct: /%/.test(_ictStatsHtml('FVG')),
      sweepHasPct: /%/.test(_ictStatsHtml('SWEEP')),
      sweepHasDays: /中位|平均|天/.test(_ictStatsHtml('SWEEP')),
      sweepHasZero: /0 天|0%/.test(_ictStatsHtml('SWEEP'))
    };
    window._ict.statsCache[key] = keep;
    return out;
  }, CODE);
  console.log('SMALL_SAMPLE', JSON.stringify(small));

  // ---------- 4b) v38: 中位/平均并列 + 命中数分母 + null 边界 ----------
  const v38 = await p.evaluate(() => {
    renderKline(window.__pin);
    const st = _ictStatsData();
    const key = _ictKey();
    const keep = window._ict.statsCache[key];
    const txt = (t) => String(_ictStatsHtml(t)).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    const real = { fvg: txt('FVG'), ob: txt('OB'), sweep: txt('SWEEP') };

    // 边界 A：median=null, avg=null（样本充足但未算出天数）-> 不出现任何天数、不出现 0 天
    const mockA = JSON.parse(JSON.stringify(st));
    mockA.fvg.overall.median_days_to_partial = null;
    mockA.fvg.overall.avg_days_to_partial = null;
    window._ict.statsCache[key] = { data: mockA, fetchedAt: Date.now() };
    const noDays = txt('FVG');

    // 边界 B：median 有值、avg=null -> 只显示中位
    const mockB = JSON.parse(JSON.stringify(st));
    mockB.fvg.overall.median_days_to_partial = 1.0;
    mockB.fvg.overall.avg_days_to_partial = null;
    window._ict.statsCache[key] = { data: mockB, fetchedAt: Date.now() };
    const medOnly = txt('FVG');

    // 边界 C：median=null、avg 有值 -> 依然不显示天数（禁止回退用 avg 冒充中位）
    const mockC = JSON.parse(JSON.stringify(st));
    mockC.sweep.overall.median_days_to_reversal = null;
    mockC.sweep.overall.avg_days_to_reversal = 3.86;
    window._ict.statsCache[key] = { data: mockC, fetchedAt: Date.now() };
    const swNoMed = txt('SWEEP');

    window._ict.statsCache[key] = keep;
    return { real: real, noDays: noDays, medOnly: medOnly, swNoMed: swNoMed, daysHelper: [_ictDays(1.0), _ictDays(3.0), _ictDays(3.86)] };
  });
  console.log('V38_FVG   :', v38.real.fvg);
  console.log('V38_OB    :', v38.real.ob);
  console.log('V38_SWEEP :', v38.real.sweep);
  console.log('V38_MEDIAN_NULL(no days):', v38.noDays);
  console.log('V38_MED_ONLY            :', v38.medOnly);
  console.log('V38_MEDIAN_NULL_AVG_SET :', v38.swNoMed);
  console.log('V38_DAYS_HELPER', JSON.stringify(v38.daysHelper));
  const checks = {
    fvgMedian: /中位 1 天触及（平均 2.75 天）/.test(v38.real.fvg),
    fvgHits: /91\/103 命中/.test(v38.real.fvg),
    obMedian: /中位 2 天回踩/.test(v38.real.ob),
    obNoBaseline: /无基准对照/.test(v38.real.ob),
    sweepMedian: /中位 3 天反转（平均 3\.\d+ 天）/.test(v38.real.sweep),
    noDaysWhenNull: !/中位|平均/.test(v38.noDays) && !/0 天/.test(v38.noDays),
    medOnlyNoAvg: /中位 1 天触及/.test(v38.medOnly) && !/平均/.test(v38.medOnly),
    noAvgFallback: !/中位|平均/.test(v38.swNoMed)
  };
  console.log('V38_CHECKS', JSON.stringify(checks));

  // ---------- 5) 重复开关：同标的请求数不增 ----------
  const beforeRepeat = reqs.length;
  for (let i = 0; i < 3; i++) {
    await p.click('#ibt-ict'); await p.waitForTimeout(600);
    await p.click('#ibt-ict'); await p.waitForTimeout(500);
  }
  const rep = reqs.slice(beforeRepeat);
  console.log('REPEAT_TOGGLE newRequests =', rep.length, JSON.stringify(rep));

  // ---------- 6) 关 -> 消失；开 -> 持久化；刷新 -> 保持 ----------
  await p.click('#ibt-ict');  // 若当前是开则关
  await p.waitForTimeout(1000);
  let off = await p.evaluate(() => ({
    on: window._ict.on,
    overlays: (proChart.getOverlays() || []).filter((x) => x.name === 'dpIctZone' || x.name === 'dpIctMark').length,
    ls: localStorage.getItem('dapeng_ict_on')
  }));
  if (off.on) { await p.click('#ibt-ict'); await p.waitForTimeout(900); off = await p.evaluate(() => ({ on: window._ict.on, overlays: (proChart.getOverlays() || []).filter((x) => x.name === 'dpIctZone' || x.name === 'dpIctMark').length, ls: localStorage.getItem('dapeng_ict_on') })); }
  console.log('OFF', JSON.stringify(off));
  await p.click('#ibt-ict');   // 再打开 -> localStorage=1
  await p.waitForTimeout(1200);
  const on = await p.evaluate(() => ({ on: window._ict.on, ls: localStorage.getItem('dapeng_ict_on'), overlays: (proChart.getOverlays() || []).filter((x) => x.name === 'dpIctZone' || x.name === 'dpIctMark').length }));
  console.log('ON', JSON.stringify(on));

  await p.reload({ waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(4000);
  const reload = await p.evaluate(() => ({
    on: window._ict.on,
    btn: document.getElementById('ibt-ict').getAttribute('aria-pressed'),
    ls: (() => { try { return localStorage.getItem('dapeng_ict_on'); } catch (e) { return null; } })()
  }));
  console.log('AFTER_RELOAD', JSON.stringify(reload));
  await p.locator('#kline-card').screenshot({ path: 'shots/ict-stats.png' });

  // ---------- 7) 回归：T10 + 分时 ----------
  await p.waitForTimeout(1500);
  await p.evaluate(async (c) => {
    const d = (await (await fetch('/api/kline?code=' + c + '&period=day&limit=250')).json()).data;
    window.__pin = d;
    window.appState.klineCache[d.code + ':day'] = d;
    renderKline(d);
  }, CODE);
  await p.waitForTimeout(800);
  await p.click('#ibt-supertrend'); await p.waitForTimeout(900);
  const t10 = await p.evaluate(() => ({ sub: window._indicatorConfig.subIndicator, ind: (proChart.getIndicators() || []).map((i) => i.name + '@' + i.paneId) }));
  const axisPx = await p.evaluate(() => {
    const a = proChart.convertToPixel({ value: 1400 }, { paneId: 'candle_pane' });
    const c2 = proChart.convertToPixel({ value: 1200 }, { paneId: 'candle_pane' });
    return { dy: Math.abs(a.y - c2.y) };
  });
  await p.click('[data-kline-period="min"]'); await p.waitForTimeout(4000);
  const ts = await p.evaluate(() => ({
    chartType: typeof chartType !== 'undefined' ? chartType : null,
    ictActive: _ictActive(),
    ictOverlays: (proChart.getOverlays() || []).filter((x) => x.name === 'dpIctZone' || x.name === 'dpIctMark').length
  }));
  console.log('T10', JSON.stringify(t10), 'AXIS_DY', JSON.stringify(axisPx), 'TIMESHARE', JSON.stringify(ts));
  console.log('TOTAL_ICT_REQS', reqs.length, JSON.stringify(reqs));
  console.log('ERRORS', JSON.stringify(errs.slice(0, 5)));
  await b.close();
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
