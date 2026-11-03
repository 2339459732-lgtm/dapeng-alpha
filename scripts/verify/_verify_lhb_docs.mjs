// 验收：文档页能加载新增的 LHB-REVIEW 节点，且图谱/列表无报错
import { chromium } from 'playwright';
const BASE = 'http://127.0.0.1:8900';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', e => errs.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });

await page.goto(BASE + '/project-docs.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(1500);

// 数据层是否含新节点
const data = await page.evaluate(() => {
  const D = window.DAPENG_DOCS;
  const arch = D.changelogSources.groups.flatMap(g => g.items || []).filter(x => x && x.path);
  return {
    mileCount: D.milestones.items.length,
    lastMile: D.milestones.items[D.milestones.items.length - 1].title,
    lhbDoc: arch.some(x => String(x.path).includes('LHB-REVIEW')),
    routingDoc: arch.some(x => String(x.path).includes('DECISION-ROUTING')),
    archCount: arch.length,
  };
});

// 直接看文档卡片有没有渲染出来
const btn = await page.$('[data-dg-view="graph"]');
if (btn) await btn.click();
await page.waitForTimeout(5000);
const graph = await page.evaluate(() => {
  const t = id => { const e = document.getElementById(id); return e ? e.textContent.trim().slice(0, 120) : 'MISSING'; };
  return { meta: t('dg-meta'), stateVisible: (() => { const s = document.getElementById('dg-state'); return s ? !s.hasAttribute('hidden') : null; })(), stateText: (() => { const s = document.getElementById('dg-state'); return s ? s.textContent.trim().slice(0, 100) : null; })() };
});

console.log(JSON.stringify({ data, graph, errs }, null, 1));
await browser.close();
