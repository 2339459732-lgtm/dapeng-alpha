// dashboard.html 专项：SSE 常驻，不能用 networkidle，改为 domcontentloaded + 固定等待
import { chromium } from 'playwright';
const BASE = 'http://127.0.0.1:8900';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errs = [];
page.on('pageerror', e => errs.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });

await page.goto(BASE + '/dashboard.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(9000);

const probe = await page.evaluate(() => {
  const t = id => { const el = document.getElementById(id); return el ? (el.textContent || '').trim().slice(0, 24) : 'MISSING'; };
  const rows = document.querySelectorAll('#stock-list tr, #stock-tbody tr, .stock-row').length;
  return {
    rows,
    kName: t('k-name'),
    kPrice: t('k-price'),
    sectorTitle: t('sector-title-name'),
    vol: t('k-vol'),
    turnover: t('k-turnover'),
    volRatio: t('k-vol-ratio'),
    live: document.querySelector('.live-tag') ? document.querySelector('.live-tag').textContent.trim() : null,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
});

console.log(JSON.stringify(probe, null, 2));
console.log('错误=' + (errs.length ? JSON.stringify(errs.slice(0, 5), null, 2) : '无'));
await browser.close();
