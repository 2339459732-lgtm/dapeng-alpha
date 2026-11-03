// 交互回归：hover / 选中 → Esc 还原 / 视图来回切换
import { chromium } from 'playwright';
const BASE = 'http://127.0.0.1:8900';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', e => errs.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 160)); });

await page.goto(BASE + '/project-docs.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(900);
await (await page.$('[data-dg-view="graph"]')).click();
await page.waitForTimeout(3000);

const base = await page.evaluate(() => {
  const d = echarts.getInstanceByDom(document.getElementById('dg-canvas')).getOption().series[0].data;
  return d.map(x => Math.round((x.itemStyle && x.itemStyle.opacity || 0) * 100) / 100);
});

// 选中后按 Esc（走真实键盘事件，验证 closePanel → applyFilter 还原）
await page.evaluate(() => window.DocsGraph.selectNode('docs/10-architecture/SYSTEM-WORKFLOW.md'));
await page.waitForTimeout(600);
const dimmed = await page.evaluate(() => {
  const d = echarts.getInstanceByDom(document.getElementById('dg-canvas')).getOption().series[0].data;
  return { min: Math.min(...d.map(x => x.itemStyle.opacity)), n: d.length };
});
await page.keyboard.press('Escape');
await page.waitForTimeout(900);
const restored = await page.evaluate(() => {
  const d = echarts.getInstanceByDom(document.getElementById('dg-canvas')).getOption().series[0].data;
  return d.map(x => Math.round((x.itemStyle && x.itemStyle.opacity || 0) * 100) / 100);
});
const same = base.length === restored.length && base.every((v, i) => Math.abs(v - restored[i]) <= 0.02);

// 视图来回切换
for (let i = 0; i < 2; i++) {
  await page.evaluate(() => window.DocsGraph.setView('list'));
  await page.waitForTimeout(500);
  await page.evaluate(() => window.DocsGraph.setView('graph'));
  await page.waitForTimeout(1200);
}
const after = await page.evaluate(() => ({
  canvases: document.querySelectorAll('#dg-canvas canvas').length,
  nodes: echarts.getInstanceByDom(document.getElementById('dg-canvas')).getOption().series[0].data.length,
  pings: document.querySelectorAll('.dg-ping').length,
}));

console.log(JSON.stringify({ dimmedMin: dimmed.min, restoredMatch: same, after, errors: errs.slice(0, 4) }, null, 1));
await browser.close();
