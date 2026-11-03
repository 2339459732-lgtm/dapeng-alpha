// 截取项目文档页「图谱」视图，用于设计评估
import { chromium } from 'playwright';
const BASE = 'http://127.0.0.1:8900';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
const errs = [];
page.on('pageerror', e => errs.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });

await page.goto(BASE + '/project-docs.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(1500);

// 切到图谱视图
const btn = await page.$('[data-dg-view="graph"]');
if (btn) await btn.click();
await page.waitForTimeout(6000);

const meta = await page.evaluate(() => {
  const t = id => { const e = document.getElementById(id); return e ? e.textContent.trim().slice(0, 160) : 'MISSING'; };
  const canvas = document.getElementById('dg-canvas');
  const r = canvas ? canvas.getBoundingClientRect() : null;
  return {
    meta: t('dg-meta'),
    chips: document.querySelectorAll('#dg-chips .dg-chip').length,
    stateVisible: (() => { const s = document.getElementById('dg-state'); return s ? !s.hasAttribute('hidden') : null; })(),
    stateText: (() => { const s = document.getElementById('dg-state'); return s ? s.textContent.trim().slice(0, 120) : null; })(),
    canvasSize: r ? `${Math.round(r.width)}x${Math.round(r.height)}` : null,
    filterInfo: t('dg-filter-info'),
    crumb: t('dg-crumb'),
  };
});
console.log(JSON.stringify(meta, null, 2));
console.log('错误=' + (errs.length ? JSON.stringify(errs.slice(0, 5)) : '无'));

await page.screenshot({ path: 'shots/graph-after.png', fullPage: false });
console.log('screenshot -> shots/graph-after.png');
await browser.close();
