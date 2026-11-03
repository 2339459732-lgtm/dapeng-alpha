// 验收：三层对证（layer-crosscheck.v1）
//
// 要验证的是「同一指标在事实/判断/决策三层对得上」这件事被显式呈现：
//   1) 决策层顶部有对证条，含每项检查的状态与差值；
//   2) 后端返回的 checks 与页面渲染一致（数量、状态）；
//   3) 数据配置有校验结果（非法项会回落默认值）；
//   4) 无 undefined/NaN 泄漏、控制台零错误。
//
// 运行：node scripts/verify/verify_layer_crosscheck.mjs
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

await page.goto(`${BASE}/dashboard.html#decision`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(9000);

const api = await page.evaluate(async () => {
  const [light, full, caps] = await Promise.all([
    fetch('/api/brain/alignment?light=true').then(r => r.json()),
    fetch('/api/brain/alignment?light=false').then(r => r.json()),
    fetch('/api/brain/capabilities').then(r => r.json()),
  ]);
  return { light, full, caps };
});

const lightCc = (api.light && api.light.crosscheck) || {};
const cc = (api.full && api.full.crosscheck) || {};
const checks = cc.checks || [];
const lightChecks = lightCc.checks || [];
console.log('\nA. 后端对证');
check('配置校验通过', (api.full.config || {}).ok === true, JSON.stringify((api.full.config || {}).issues || []));
check('对证有结果', checks.length >= 7, `${checks.length} 项`);
check('结论字段完整', ['ok', 'score', 'counts', 'comparable', 'comparable_ratio'].every(k => k in cc),
  `ok=${cc.ok} score=${cc.score} 可比 ${cc.comparable}/${cc.total}`);
check('没有「缺数据」当冲突', !(cc.conflicts || []).some(x => /缺值/.test(x.note || '')), JSON.stringify(cc.conflicts || []));
const ids = checks.map(c => c.id);
check('覆盖同源指标', ['sentiment_fact_survey', 'breadth_up_ratio', 'turnover', 'limit_up', 'scope', 'freshness'].every(k => ids.includes(k)),
  ids.join(','));
check('异量纲项存在且不判数值', checks.some(c => c.id === 'score_axes' && c.status === 'incomparable'));
check('完整对证含决策层', ids.includes('sentiment_fact_decision'), `${checks.length} 项`);
check('轻量对证不含决策层', !lightChecks.some(c => c.id === 'sentiment_fact_decision'), `${lightChecks.length} 项`);

console.log('\nB. 页面呈现');
const ui = await page.evaluate(() => {
  const host = document.getElementById('dd-crosscheck');
  const chips = [...document.querySelectorAll('#dd-crosscheck .dd-cc-chip')];
  const lead = document.querySelector('#dd-crosscheck .dd-cc-lead');
  return {
    exists: !!host && host.getBoundingClientRect().height > 0,
    leadText: lead ? lead.textContent.trim() : '',
    leadState: lead ? lead.getAttribute('data-state') : '',
    chips: chips.map(c => ({ text: c.textContent.replace(/\s+/g, ' ').trim(), state: c.getAttribute('data-state'), title: c.getAttribute('title') || '' })),
    hasRefresh: !!document.querySelector('[data-act="crosscheck-refresh"]'),
    bodyText: host ? host.textContent : '',
  };
});
check('对证条已渲染', ui.exists, ui.leadText.slice(0, 40));
check('标题含「三层对证」与一致率', /三层对证/.test(ui.leadText) && /一致性/.test(ui.leadText), ui.leadText);
check('顶部状态合法', ['pass', 'warn', 'fail'].includes(ui.leadState), ui.leadState);
check('逐项 chip 数量与轻量档一致', ui.chips.length === lightChecks.length, `页面 ${ui.chips.length} / 接口 ${lightChecks.length}`);
check('每个 chip 有状态与说明', ui.chips.every(c => c.state && c.title.length > 4));
check('无 undefined/NaN 泄漏', !/undefined|NaN/.test(ui.bodyText), ui.bodyText.slice(0, 60));
check('有「重新对证」入口', ui.hasRefresh);

console.log('\nC. 手动重跑');
const before = ui.leadText;
await page.click('[data-act="crosscheck-refresh"]');
await page.waitForTimeout(6000);
const after = await page.evaluate(() => {
  const lead = document.querySelector('#dd-crosscheck .dd-cc-lead');
  return lead ? lead.textContent.trim() : '';
});
check('重新对证后仍有结果', /三层对证/.test(after), after.slice(0, 40));

console.log('\nD. 能力图与配置');
const caps = api.caps || {};
check('能力图仍可用', (caps.counts && caps.counts.ok >= 0) && caps.graph && caps.graph.count >= 15,
  `counts=${JSON.stringify(caps.counts)}`);

const realErrors = errors.filter(e => !/favicon|Failed to load resource/i.test(e));
check('控制台零错误', realErrors.length === 0, realErrors.slice(0, 2).join(' | '));

console.log(`\n结果：${pass} PASS / ${fail} FAIL`);
await browser.close();
process.exit(fail ? 1 : 0);
