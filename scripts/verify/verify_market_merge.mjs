// 验收：大盘纵览 × 市场情绪追踪 融合（market-merge.v1）
//
// 背景：两个页面原本各抓一批行情、各算一个「情绪分」。合并后要求：
//   1) 情绪分只有 sentiment.v1 一个口径（与 /api/decision/sentiment 一致）；
//   2) 六维、情绪追踪（变化/斜率/分位/持续性/驱动）、三锚点都在大盘纵览内呈现；
//   3) 旧入口 sentiment.html 不再是一张重复页面，而是带参深链跳转；
//   4) 1600 / 390 两档无横向溢出，控制台零错误。
//
// 运行：node scripts/verify/verify_market_merge.mjs
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8900';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on('pageerror', e => errors.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 140)); });

let pass = 0, fail = 0;
const check = (name, ok, detail = '') => {
  if (ok) { pass++; console.log(`  PASS ${name}${detail ? ' · ' + detail : ''}`); }
  else { fail++; console.log(`  FAIL ${name}${detail ? ' · ' + detail : ''}`); }
};

await page.goto(`${BASE}/dashboard.html#market`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(9000);

// 参考值：统一情绪口径接口（必须在页面内取，否则同源 fetch 无 origin 会失败）
const api = await page.evaluate(async () => {
  const [s, f] = await Promise.all([
    fetch('/api/decision/sentiment').then(r => r.json()),
    fetch('/api/decision/sentiment/flow').then(r => r.json()),
  ]);
  return { score: s && s.score, status: s && s.status, flowLevel: f && f.level, flowRegime: f && (f.regime || {}).label };
}).catch(() => ({}));

const view = await page.evaluate(() => {
  const mv = document.getElementById('market-dashboard-view');
  const txt = id => { const el = document.getElementById(id); return el ? el.textContent.trim() : null; };
  const dims = [...document.querySelectorAll('#market-sent-dims .mo-dim')].map(el => el.textContent.trim());
  const metrics = [...document.querySelectorAll('#market-flow-metrics .mo-flow-metric')].map(el => el.textContent.trim());
  const drivers = [...document.querySelectorAll('#market-flow-drivers .mo-flow-driver')].map(el => el.textContent.trim());
  const anchors = [...document.querySelectorAll('#market-anchors .mo-anchor')].map(el => ({
    state: el.getAttribute('data-state'), text: el.textContent.replace(/\s+/g, ' ').trim(),
  }));
  const gauge = txt('market-gauge-score');
  const title = document.getElementById('market-gauge-ring');
  return {
    visible: !!mv && mv.offsetParent !== null,
    gauge, regime: txt('market-regime'), judgment: txt('market-judgment'),
    dims, metrics, drivers, anchors,
    provenance: title ? (title.getAttribute('title') || '') : '',
    overflow: document.documentElement.scrollWidth - window.innerWidth,
  };
});

console.log('\nA. 页面与口径');
check('#market 深链落到大盘纵览', view.visible);
check('情绪分已渲染', /^\d+$/.test(String(view.gauge || '')), `gauge=${view.gauge}`);
const scoreMatch = api.score != null && view.gauge != null && Math.abs(Number(view.gauge) - Math.round(api.score)) <= 1;
check('情绪分与 sentiment.v1 一致', scoreMatch, `页面 ${view.gauge} / 接口 ${api.score == null ? '--' : Math.round(api.score)}`);
check('状态文案来自统一口径', !!view.regime && view.regime !== '--', `regime=${view.regime}`);
check('溯源标注统一口径', /sentiment\.v1/.test(view.provenance), view.provenance.slice(0, 46));

console.log('\nB. 六维 / 追踪 / 归因');
check('六维芯片 = 6', view.dims.length === 6, view.dims.join(' | ').slice(0, 60));
check('追踪指标 = 4', view.metrics.length === 4, view.metrics.join(' | ').slice(0, 80));
check('归因条 ≥ 1', view.drivers.length >= 1, view.drivers.join(' | ').slice(0, 60));
const tag = await page.$eval('#market-flow-tag', el => el.textContent.trim()).catch(() => '');
check('追踪状态不是占位', !!tag && tag !== '追踪' && tag !== '追踪不可用', `tag=${tag}`);

console.log('\nC. 三锚点');
check('三锚点 = 3', view.anchors.length === 3, view.anchors.map(a => a.text).join(' / ').slice(0, 90));
check('锚点状态合法', view.anchors.every(a => ['pass', 'warn', 'fail'].includes(a.state)),
  view.anchors.map(a => a.state).join(','));

console.log('\nD. 布局');
check('1600 无横向溢出', view.overflow <= 0, `overflow=${view.overflow}`);
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(600);
const m390 = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
check('390 无横向溢出', m390 <= 0, `overflow=${m390}`);

console.log('\nE. 旧入口收敛');
await page.setViewportSize({ width: 1200, height: 900 });
await page.goto(`${BASE}/sentiment.html?sector=半导体`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(4200);
const finalUrl = page.url();
check('sentiment.html 自动跳转', /dashboard\.html/.test(finalUrl) && /#(market|fact)/.test(finalUrl), finalUrl.replace(BASE, ''));
check('跳转保留板块参数', /sector=/.test(finalUrl), finalUrl.replace(BASE, ''));
const landed = await page.evaluate(() => {
  const mv = document.getElementById('market-dashboard-view');
  return {
    visible: !!mv && mv.offsetParent !== null,
    selectedSector: [...document.querySelectorAll('.market-sector-tile[aria-pressed="true"]')].map(el => el.textContent.trim().slice(0, 12)),
    movers: [...document.querySelectorAll('.market-mover-name strong')].slice(0, 3).map(el => el.textContent.trim()),
    summary: (document.getElementById('market-filter-summary') || {}).textContent || '',
  };
});
check('跳转后直接落在大盘纵览', landed.visible);
check('板块参数落到板块筛选', landed.selectedSector.length >= 1, landed.selectedSector.join(',') || '未选中');
check('榜单已按板块过滤', landed.movers.length >= 1, landed.movers.join('/') + ' · ' + landed.summary.slice(0, 24));

const realErrors = errors.filter(e => !/favicon|Failed to load resource/i.test(e));
check('控制台零错误', realErrors.length === 0, realErrors.slice(0, 2).join(' | '));

console.log(`\n结果：${pass} PASS / ${fail} FAIL`);
await browser.close();
process.exit(fail ? 1 : 0);
