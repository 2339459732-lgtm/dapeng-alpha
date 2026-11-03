/**
 * ICT 结构标注验收脚本（v36）
 * 1) 开关默认关  2) 打开后出现 FVG/OB/sweep  3) 主图 Y 轴前后一致
 * 4) 关闭后彻底移除  5) 重复开关不重复请求  6) tooltip 原文
 * 7) 颜色 = 主题变量  8) 回归：ADX/SuperTrend、分时无标注
 */
const { chromium } = require('playwright');
const EXE = '%LOCALAPPDATA%\\ms-playwright\\chromium_headless_shell-1223\\chrome-headless-shell-win64\\chrome-headless-shell.exe';
const CODE = process.argv[2] || 'sh600519';

(async () => {
  const b = await chromium.launch({ executablePath: EXE });
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 } });
  const ictReqs = [];
  const errs = [];
  p.on('request', (r) => { if (r.url().includes('/api/ict/scan')) ictReqs.push(r.url()); });
  p.on('pageerror', (e) => errs.push('PAGE:' + String(e).slice(0, 160)));
  p.on('console', (m) => { if (m.type() === 'error') errs.push('CON:' + m.text().slice(0, 160)); });

  await p.goto('http://127.0.0.1:8900/dashboard.html', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1200);
  // 初始自动选股可能与手工选择竞争：等首轮自动加载落定后再选，并等 pro 引擎切到目标标的
  await p.waitForTimeout(2500);
  await p.evaluate(async (c) => { await selectStock(c, c); }, CODE);
  await p.waitForFunction((c) => typeof proSymbol !== 'undefined' && proSymbol === c, CODE, { timeout: 20000 }).catch(() => {});
  await p.waitForTimeout(2500);
  const chartSymbol = await p.evaluate(() => ({ proSymbol: typeof proSymbol !== 'undefined' ? proSymbol : null, active: window.appState.activeCode, bars: (_dpCurrentKlines() || []).length }));
  console.log('CHART_SYMBOL', JSON.stringify(chartSymbol));

  // ---- 1) 默认关闭 ----
  const init = await p.evaluate(() => ({
    on: window._ict.on,
    btnPressed: document.getElementById('ibt-ict').getAttribute('aria-pressed'),
    active: document.getElementById('ibt-ict').classList.contains('active'),
    series: (() => { try { return (proChart.getIndicators() || []).length; } catch (e) { return null; } })(),
    overlays: window._ict.overlays,
    axisPixel: (() => { try { return proChart.convertToPixel({ value: 1300 }, { paneId: 'candle_pane' }); } catch (e) { return null; } })(),
    scale: (() => { try {
      const a = proChart.convertToPixel({ value: 1400 }, { paneId: 'candle_pane' });
      const c2 = proChart.convertToPixel({ value: 1200 }, { paneId: 'candle_pane' });
      return { dy: Math.abs(a.y - c2.y), perPx: +(200 / Math.abs(a.y - c2.y)).toFixed(6) };
    } catch (e) { return null; } })()
  }));
  console.log('DEFAULT_OFF', JSON.stringify(init), 'requests=', ictReqs.length);

  // ---- 2) 打开 ----
  const before = ictReqs.length;
  await p.click('#ibt-ict');
  await p.waitForFunction(() => window._ict.overlays > 0, null, { timeout: 15000 }).catch(() => {});
  await p.waitForTimeout(1800);
  const on = await p.evaluate(() => {
    const ann = _ictAnnotations() || {};
    return {
      on: window._ict.on,
      btnPressed: document.getElementById('ibt-ict').getAttribute('aria-pressed'),
      fvgs: (ann.fvgs || []).length, obs: (ann.order_blocks || []).length, sweeps: (ann.sweeps || []).length,
      overlays: window._ict.overlays,
      skipped: window._ict.skipped,
      reqCount: window._ict.reqCount,
      axisPixel: (() => { try { return proChart.convertToPixel({ value: 1300 }, { paneId: 'candle_pane' }); } catch (e) { return null; } })(),
      // overlay 的绘制样式取样（验证颜色来自主题变量）
      sampleZone: (() => { try { const o = (proChart.getOverlays() || []).filter((x) => x.name === 'dpIctZone')[0]; return o ? (o.extendData || {}) : null; } catch (e) { return null; } })(),
      themeUp: _themeUp, themeDown: _themeDown
    };
  });
  const sym2 = await p.evaluate(() => (typeof proSymbol !== 'undefined' ? proSymbol : null));
  console.log('ON', JSON.stringify(on), 'newRequests=', ictReqs.length - before, 'proSymbol=', sym2);
  await p.locator('#kline-card').screenshot({ path: 'shots/ict-on.png' });

  // ---- 3) 主图 Y 轴：像素位置对比（Pro 引擎原生口径）----
  const axisSame = JSON.stringify(init.axisPixel) === JSON.stringify(on.axisPixel);
  // 更强口径：两个价格的像素差 -> 每像素代表的价格（与 pane 偏移/高度无关）
  const pxScale = async () => p.evaluate(() => {
    try {
      const a = proChart.convertToPixel({ value: 1400 }, { paneId: 'candle_pane' });
      const b2 = proChart.convertToPixel({ value: 1200 }, { paneId: 'candle_pane' });
      return { dy: Math.abs(a.y - b2.y), perPx: +(200 / Math.abs(a.y - b2.y)).toFixed(6) };
    } catch (e) { return null; }
  });
  const scaleOn = await pxScale();
  console.log('AXIS_PIXEL', 'off=', JSON.stringify(init.axisPixel), 'on=', JSON.stringify(on.axisPixel), 'SAME=', axisSame);
  console.log('AXIS_SCALE', 'off=', JSON.stringify(init.scale), 'on=', JSON.stringify(scaleOn),
    'SAME=', JSON.stringify(init.scale) === JSON.stringify(scaleOn));

  // ---- 4) 关闭后彻底移除 ----
  await p.click('#ibt-ict');
  await p.waitForTimeout(1500);
  const off = await p.evaluate(() => ({
    on: window._ict.on,
    overlays: window._ict.overlays,
    ictOverlayLeft: (() => { try { return (proChart.getOverlays() || []).filter((x) => x.name === 'dpIctZone' || x.name === 'dpIctMark').length; } catch (e) { return null; } })(),
    axisPixel: (() => { try { return proChart.convertToPixel({ value: 1300 }, { paneId: 'candle_pane' }); } catch (e) { return null; } })(),
    scale: (() => { try {
      const a = proChart.convertToPixel({ value: 1400 }, { paneId: 'candle_pane' });
      const c2 = proChart.convertToPixel({ value: 1200 }, { paneId: 'candle_pane' });
      return { dy: Math.abs(a.y - c2.y), perPx: +(200 / Math.abs(a.y - c2.y)).toFixed(6) };
    } catch (e) { return null; } })()
  }));
  console.log('OFF_AFTER', JSON.stringify(off));

  // ---- 5) 重复开关：120s 内不重复请求 ----
  const beforeRepeat = ictReqs.length;
  for (let i = 0; i < 3; i++) {
    await p.click('#ibt-ict'); await p.waitForTimeout(900);
    await p.click('#ibt-ict'); await p.waitForTimeout(700);
  }
  console.log('REPEAT_TOGGLE newRequests =', ictReqs.length - beforeRepeat, '(期望 0)');

  // ---- 6) ECharts 路径：getOption().yAxis[0] 前后 + ICT series + tooltip 契约 ----
  await p.evaluate(() => { window._proChartDisabled = true; });
  await p.evaluate(async (c) => { await selectStock(c, c); }, CODE);
  await p.waitForFunction(() => typeof klineChart !== 'undefined' && klineChart && !klineChart.isDisposed(), null, { timeout: 20000 });
  await p.waitForTimeout(1200);

  const axisOfEc = () => p.evaluate(() => {
    const y = klineChart.getOption().yAxis[0];
    return { min: y.min, max: y.max };
  });
  const ecOff = await axisOfEc();
  const ecOffSeries = await p.evaluate(() => (klineChart.getOption().series || []).filter((s) => s._dpIct).length);
  await p.click('#ibt-ict');
  await p.waitForTimeout(2200);
  const ecOn = await axisOfEc();
  const ecOnInfo = await p.evaluate(() => {
    const op = klineChart.getOption();
    const s = (op.series || []).filter((x) => x._dpIct);
    return {
      seriesCount: s.length,
      sweepPoints: s.length ? (s[0].data || []).length : 0,
      markAreas: (s.length && s[0].markArea && s[0].markArea.data) ? s[0].markArea.data.length : 0,
      seriesPushed: window._ict.seriesPushed
    };
  });
  console.log('ECHARTS_AXIS off=', JSON.stringify(ecOff), 'on=', JSON.stringify(ecOn),
    'SAME=', JSON.stringify(ecOff) === JSON.stringify(ecOn), 'offSeries=', ecOffSeries, JSON.stringify(ecOnInfo));

  // tooltip 文案断言（直接调用 formatter，取一条含 OB/sweep 的 bar）
  const tipHtml = await p.evaluate(() => {
    const op = klineChart.getOption();
    const ann = _ictAnnotations() || {};
    const dates = (op.xAxis[0].data || []);
    const pick = (arr) => (arr || [])[0];
    const ob = pick(ann.order_blocks);
    const sw = pick(ann.sweeps);
    const norm = (t) => { const ms = Date.parse(String(t)); if (isNaN(ms)) return String(t).slice(0, 10);
      const d = new Date(ms + 8 * 3600 * 1000); const m = d.getUTCMonth() + 1, dd = d.getUTCDate();
      return d.getUTCFullYear() + '-' + (m < 10 ? '0' : '') + m + '-' + (dd < 10 ? '0' : '') + dd; };
    const barDate = (x) => String(x).slice(0, 10);
    const findIdx = (t) => { const k = norm(t); return dates.findIndex((x) => barDate(x) === k); };
    const idx = ob ? findIdx(ob.time) : (sw ? findIdx(sw.time) : -1);
    if (idx < 0) return { idx: -1 };
    const ps = [{ seriesName: 'K线', dataIndex: idx, value: [1, 2, 3, 4], name: dates[idx] }];
    const html = op.tooltip[0].formatter(ps);
    return {
      idx: idx,
      hasObNote: ob && ob.note ? html.includes(ob.note) : null,
      hasSessionLabel: sw && sw.session_label ? html.includes(sw.session_label) : null,
      snippet: String(html).replace(/<[^>]+>/g, ' ').slice(-260)
    };
  });
  console.log('TOOLTIP_OB', JSON.stringify(tipHtml));
  const tipSweep = await p.evaluate(() => {
    const op = klineChart.getOption();
    const ann = _ictAnnotations() || {};
    const dates = (op.xAxis[0].data || []);
    const sw = (ann.sweeps || [])[0];
    if (!sw) return { none: true };
    const ms = Date.parse(String(sw.time));
    const d = new Date(ms + 8 * 3600 * 1000);
    const k = d.getUTCFullYear() + '-' + String(d.getUTCMonth() + 1).padStart(2, '0') + '-' + String(d.getUTCDate()).padStart(2, '0');
    const idx = dates.findIndex((x) => String(x).slice(0, 10) === k);
    if (idx < 0) return { idx: -1, k: k };
    const ps = [{ seriesName: 'K线', dataIndex: idx, value: [1, 2, 3, 4], name: dates[idx] }];
    const html = String(op.tooltip[0].formatter(ps)).replace(/<[^>]+>/g, ' ');
    return { idx: idx, hasKind: sw.kind ? html.includes(sw.kind) : null, hasSessionLabel: sw.session_label ? html.includes(sw.session_label) : null, snippet: html.slice(-200) };
  });
  console.log('TOOLTIP_SWEEP', JSON.stringify(tipSweep));

  // ---- 7) 关闭（ECharts 路径）后 series 移除 ----
  await p.click('#ibt-ict');
  await p.waitForTimeout(1500);
  const ecOffAgain = await p.evaluate(() => ({
    seriesCount: (klineChart.getOption().series || []).filter((s) => s._dpIct).length,
    seriesPushed: window._ict.seriesPushed,
    axis: (() => { const y = klineChart.getOption().yAxis[0]; return { min: y.min, max: y.max }; })()
  }));
  console.log('ECHARTS_OFF_AGAIN', JSON.stringify(ecOffAgain));

  // ---- 8) 回归：ADX/SuperTrend 仍可切；分时无 ICT ----
  await p.evaluate(() => { window._proChartDisabled = false; });
  await p.evaluate(async (c) => { await selectStock(c, c); }, CODE);
  await p.waitForTimeout(2000);
  await p.click('#ibt-adx'); await p.waitForTimeout(800);
  await p.click('#ibt-supertrend'); await p.waitForTimeout(800);
  const regress = await p.evaluate(() => ({
    sub: window._indicatorConfig.subIndicator,
    indicators: (proChart.getIndicators() || []).map((i) => i.name + '@' + i.paneId)
  }));
  // 打开 ICT 后切分时 -> 应无标注
  await p.click('#ibt-ict'); await p.waitForTimeout(1500);
  const ictOnBeforeTs = await p.evaluate(() => window._ict.overlays);
  await p.click('[data-kline-period="min"]');
  await p.waitForTimeout(3500);
  const ts = await p.evaluate(() => ({
    chartType: typeof chartType !== 'undefined' ? chartType : null,
    ictActive: _ictActive(),
    overlays: window._ict.overlays,
    ictOverlaysOnCanvas: (() => { try { return (proChart.getOverlays() || []).filter((x) => x.name === 'dpIctZone' || x.name === 'dpIctMark').length; } catch (e) { return null; } })()
  }));
  await p.locator('#kline-card').screenshot({ path: 'shots/ict-timeshare.png' });
  console.log('REGRESS', JSON.stringify(regress));
  console.log('TIMESHARE_ICT', JSON.stringify(ts), 'overlaysBeforeTs=', ictOnBeforeTs);

  console.log('ICT_REQ_TOTAL', ictReqs.length);
  console.log('ERRORS', JSON.stringify(errs.slice(0, 6)));
  await b.close();
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
