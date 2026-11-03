import { chromium } from 'playwright';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 430, height: 900 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
const errs = [];
page.on('pageerror', e => errs.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 160)); });
await page.goto('http://127.0.0.1:8900/lhb-review.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(3500);
const info = await page.evaluate(() => {
  const de = document.documentElement;
  const wide = [...document.querySelectorAll('.lr-wrap *')].filter(el => el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0)
    .slice(0, 5).map(el => el.className || el.tagName);
  return {
    docW: de.clientWidth, scrollW: de.scrollWidth,
    horizontalOverflow: de.scrollWidth > de.clientWidth + 2,
    overflowEls: wide,
    kpi: document.querySelectorAll('.lr-kpi').length,
    recRows: document.querySelectorAll('tr.lr-rec').length
  };
});
await page.screenshot({ path: 'shots/lhb-mobile.png', fullPage: false });
console.log(JSON.stringify({ info, errs }, null, 1));
await browser.close();
