const { chromium } = require('playwright');
const EXE = '%LOCALAPPDATA%//ms-playwright//chromium_headless_shell-1223//chrome-headless-shell-win64//chrome-headless-shell.exe';
(async () => {
  const b = await chromium.launch({ executablePath: EXE });
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 } });
  p.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') console.log('CONSOLE[' + m.type() + ']', m.text().slice(0, 300)); });
  p.on('pageerror', (e) => console.log('PAGEERROR', String(e).slice(0, 300)));
  await p.goto('http://127.0.0.1:8900/dashboard.html', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1200);
  await p.evaluate(async () => { await selectStock('sh600519', '贵州茅台'); });
  await p.waitForTimeout(2500);
  console.log('pro?', await p.evaluate(() => ({ pro: !!window.proChart, disabled: window._proChartDisabled })));
  await p.evaluate(() => { window._proChartDisabled = true; });
  await p.evaluate(async () => { await selectStock('sh600519', '贵州茅台'); });
  await p.waitForTimeout(3000);
  const st = await p.evaluate(() => {
    const kc = (typeof klineChart !== 'undefined') ? klineChart : null;
    let opKeys = null, ok = false;
    try { const op = kc && kc.getOption(); opKeys = op ? Object.keys(op) : null; ok = !!(op && op.yAxis); } catch (e) { opKeys = 'THROW:' + e.message; }
    return { hasKC: !!kc, disposed: kc ? kc.isDisposed() : null, opKeys, ok, bars: (_dpCurrentKlines() || []).length,
      legacyVisible: (() => { const el = document.getElementById('kline-chart'); return el ? getComputedStyle(el).display : null; })(),
      proVisible: (() => { const el = document.getElementById('kline-chart-pro'); return el ? getComputedStyle(el).display : null; })() };
  });
  console.log('STATE', JSON.stringify(st));
  await b.close();
})();
