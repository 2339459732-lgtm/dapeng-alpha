import { chromium } from 'playwright';
const browser = await chromium.launch({ channel: 'chrome' });
const errs = [];
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on('pageerror', e => errs.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 120)); });
// 1) 列表视图（默认）不应受影响
await page.goto('http://127.0.0.1:8900/project-docs.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2500);
const list = await page.evaluate(() => ({
  toc: document.querySelectorAll('#docs-toc-list a').length,
  sections: document.querySelectorAll('#docs-main section, #docs-main .docs-section').length,
  bodyClass: document.body.className,
}));
await page.screenshot({ path: 'shots/v27-list.png' });
// 2) 图谱视图：HUD 元素齐备
await (await page.$('[data-dg-view="graph"]')).click();
await page.waitForTimeout(2800);
const hud = await page.evaluate(() => ({
  ticks: document.querySelectorAll('.dg-hud-tick').length,
  cap: (document.querySelector('.dg-hud-cap') || {}).textContent,
  zoom: (document.getElementById('dg-zoom-val') || {}).textContent,
  legend: document.querySelectorAll('.dg-hint .dg-lg').length,
  hint: (document.querySelector('.dg-lg-hint') || {}).textContent,
}));
console.log(JSON.stringify({ list, hud, errors: errs }, null, 1));
await browser.close();
