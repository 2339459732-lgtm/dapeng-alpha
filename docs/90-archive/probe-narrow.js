const { chromium } = require('playwright');
const BASE = 'http://localhost:8900/dashboard.html';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.on('pageerror', e => console.log('PAGEERROR:', e.message, '\nSTACK:', e.stack && e.stack.split('\n').slice(0, 6).join('\n')));
  page.on('console', m => { if (m.type() === 'error') console.log('CONSOLE:', m.text(), m.location() && m.location().url, m.location() && m.location().lineNumber); });

  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);

  const probe = await page.evaluate(() => {
    const p = document.getElementById('left-panel');
    return {
      typeofInitNarrow: typeof initLeftPanelNarrow,
      width: p.offsetWidth,
      cls: p.className,
    };
  });
  console.log('PROBE:', JSON.stringify(probe));

  await page.evaluate(() => {
    const p = document.getElementById('left-panel');
    p.style.width = '240px';
  });
  await page.waitForTimeout(800);
  const probe2 = await page.evaluate(() => {
    const p = document.getElementById('left-panel');
    return { width: p.offsetWidth, cls: p.className, sparkDisplay: getComputedStyle(document.querySelector('.stock-sparkline-wrap')).display };
  });
  console.log('PROBE2 (240px):', JSON.stringify(probe2));

  await browser.close();
})();
