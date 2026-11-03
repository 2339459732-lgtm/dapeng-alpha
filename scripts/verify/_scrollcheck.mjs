import { chromium } from 'playwright';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', e => errs.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 160)); });
await page.goto('http://127.0.0.1:8900/lhb-review.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(3500);
const s = await page.evaluate(() => {
  const de = document.documentElement;
  window.scrollTo(0, 1200);
  const y = window.scrollY;
  window.scrollTo(0, 0);
  return {
    scrollHeight: de.scrollHeight, clientHeight: de.clientHeight,
    htmlOverflow: getComputedStyle(de).overflowY,
    bodyOverflow: getComputedStyle(document.body).overflowY,
    scrollable: de.scrollHeight > de.clientHeight,
    scrollToWorked: y > 500,
    panels: document.querySelectorAll('.lr-panel').length
  };
});
await page.evaluate(() => window.scrollTo(0, 900));
await page.waitForTimeout(300);
const mid = await page.evaluate(() => window.scrollY);
await page.screenshot({ path: 'shots/lhb-scroll-mid.png' });
console.log(JSON.stringify({ s, midScroll: mid, errs }, null, 1));
await browser.close();
