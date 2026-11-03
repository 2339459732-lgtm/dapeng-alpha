// 交互态验证：选中节点（光圈聚焦 + 面板）与分组筛选
import { chromium } from 'playwright';
const BASE = 'http://127.0.0.1:8900';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
const errs = [];
page.on('pageerror', e => errs.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 160)); });
await page.goto(BASE + '/project-docs.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(900);
await (await page.$('[data-dg-view="graph"]')).click();
await page.waitForTimeout(3500);

// 1) 选中 README.md（出链最多的枢纽）
await page.evaluate(() => window.DocsGraph.selectNode('README.md'));
await page.waitForTimeout(1200);
await page.screenshot({ path: 'shots/graph-focus.png' });

// 2) 关闭面板 → 点分组 chip（10-architecture）
await page.evaluate(() => window.DocsGraph.closePanel(true));
await page.waitForTimeout(700);
const chips = await page.$$('#dg-chips .dg-chip');
if (chips[3]) await chips[3].click();
await page.waitForTimeout(1500);
await page.screenshot({ path: 'shots/graph-filter.png' });

const state = await page.evaluate(() => {
  const s = window.DocsGraph.state;
  return { group: s.group, selected: s.selected, hulls: s.hulls && s.hulls.length, paints: s.paints };
});
console.log(JSON.stringify(state));
console.log('错误=' + (errs.length ? JSON.stringify(errs.slice(0, 4)) : '无'));
await browser.close();
