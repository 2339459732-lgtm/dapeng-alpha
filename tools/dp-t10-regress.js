const { chromium } = require('playwright');
const EXE = '%LOCALAPPDATA%//ms-playwright//chromium_headless_shell-1223//chrome-headless-shell-win64//chrome-headless-shell.exe';
(async () => {
  const b = await chromium.launch({ executablePath: EXE });
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 } });
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e).slice(0, 160)));
  p.on('console', (m) => { if (m.type() === 'error') errs.push('C:' + m.text().slice(0, 160)); });
  await p.goto('http://127.0.0.1:8900/dashboard.html', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1200);
  await p.evaluate(async () => { await selectStock('sh600519', '贵州茅台'); });
  await p.waitForTimeout(2500);

  // 1) 日K 正常
  await p.click('#ibt-adx'); await p.waitForTimeout(700);
  await p.locator('#kline-card').screenshot({ path: 'shots/t10-regress-dayk.png' });
  const dayk = await p.evaluate(() => ({ bars: _dpCurrentKlines().length, sub: window._indicatorConfig.subIndicator }));

  // 2) 分时模式
  await p.click('[data-kline-period="min"]');
  await p.waitForTimeout(4000);
  const ts = await p.evaluate(() => ({
    chartType: typeof chartType !== 'undefined' ? chartType : null,
    tsPoints: (window._tsState && _tsState.points) ? _tsState.points.length : null,
    liveStatus: window._klineLive ? _klineLive.status : null,
    liveReason: window._klineLive ? _klineLive.reason : null
  }));
  await p.locator('#kline-card').screenshot({ path: 'shots/t10-regress-timeshare.png' });

  // 3) 回日K + 全屏
  await p.click('[data-kline-period="day"]');
  await p.waitForTimeout(2000);
  await p.evaluate(() => window.toggleKlineFullscreen());
  await p.waitForTimeout(1200);
  const fs = await p.evaluate(() => ({
    fsActive: document.fullscreenElement === document.getElementById('kline-card') || document.getElementById('kline-card').classList.contains('kline-fullscreen-fallback'),
    proH: (document.getElementById('kline-chart-pro') || {}).clientHeight || null
  }));
  await p.locator('#kline-card').screenshot({ path: 'shots/t10-regress-fullscreen.png' });
  await p.evaluate(() => window.toggleKlineFullscreen());
  await p.waitForTimeout(800);

  // 4) 切回日K后再切指标仍正常
  await p.click('#ibt-supertrend'); await p.waitForTimeout(700);
  const after = await p.evaluate(() => {
    const pro = (typeof proChart !== 'undefined' && proChart) ? proChart : null;
    return { indicators: pro ? (pro.getIndicators() || []).map((i) => i.name + '@' + i.paneId) : [], sub: window._indicatorConfig.subIndicator };
  });
  console.log('DAYK', JSON.stringify(dayk));
  console.log('TIMESHARE', JSON.stringify(ts));
  console.log('FULLSCREEN', JSON.stringify(fs));
  console.log('AFTER', JSON.stringify(after));
  console.log('ERRORS', JSON.stringify(errs.slice(0, 6)));
  await b.close();
})();
