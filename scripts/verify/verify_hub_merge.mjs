// 验收：三工作区合一「主脑中心」（decision-hub.v1）
//
// 目标：大盘纵览（事实）与动态市场调查（判断）不再是并列工作区，
// 而是决策台内部的两层；三层共用同一份市场快照，同屏不允许出现两个情绪分。
//
// 运行：node scripts/verify/verify_hub_merge.mjs
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

const snap = () => page.evaluate(() => {
  const vis = sel => { const el = document.querySelector(sel); return !!el && el.offsetParent !== null; };
  return {
    workspaceTabs: [...document.querySelectorAll('.workspace-tab')].map(t => t.textContent.trim()),
    hubTabs: [...document.querySelectorAll('.dd-hub-tab')].map(t => t.getAttribute('data-hub')),
    activeSegs: [...document.querySelectorAll('.dd-hub-seg.is-active')].map(s => s.getAttribute('data-hub-panel')),
    factVisible: vis('[data-hub-panel="fact"] #market-dashboard-view'),
    surveyVisible: vis('[data-hub-panel="survey"] #survey-dashboard-view'),
    decisionVisible: vis('[data-hub-panel="decision"] #decision-mount'),
    gauge: (document.getElementById('market-gauge-score') || {}).textContent,
    conclusionScore: (document.getElementById('market-conclusion-score') || {}).textContent,
    cards: document.querySelectorAll('.dd-card').length,
    surveyPanels: document.querySelectorAll('#survey-dashboard-view .market-panel').length,
    surveyLayout: (() => {
      const view = document.querySelector('#survey-dashboard-view');
      if (!view) return null;
      const first = view.querySelector('.market-panel');
      if (!first) return null;
      const vr = view.getBoundingClientRect(), fr = first.getBoundingClientRect();
      return { viewW: Math.round(vr.width), panelW: Math.round(fr.width), leftGap: Math.round(fr.left - vr.left) };
    })(),
    hash: location.hash,
  };
});

console.log('\nA. 工作区入口收敛');
await page.goto(`${BASE}/dashboard.html#decision`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(9000);
let s = await snap();
const tabText = s.workspaceTabs.join('|');
check('工作区只剩 3 个入口', s.workspaceTabs.length === 3, tabText);
check('「大盘纵览」不再独立成 tab', !/大盘纵览/.test(tabText));
check('「动态市场调查」不再独立成 tab', !/动态市场调查/.test(tabText));
check('决策台内有三层', s.hubTabs.join(',') === 'fact,survey,decision', s.hubTabs.join(','));
check('默认停在决策层', s.activeSegs.join(',') === 'decision', s.activeSegs.join(','));

console.log('\nB. 三层互斥且各自可见');
check('决策层可见', s.decisionVisible && s.cards >= 15, `cards=${s.cards}`);
check('事实层此时不可见', !s.factVisible);
check('判断层此时不可见', !s.surveyVisible);

await page.click('[data-hub="fact"]');
await page.waitForTimeout(5000);
s = await snap();
check('切到事实层后仅事实层可见', s.activeSegs.join(',') === 'fact' && s.factVisible && !s.surveyVisible && !s.decisionVisible);
check('事实层有情绪分', /^\d+$/.test(String(s.gauge || '')), `gauge=${s.gauge}`);
check('同屏两个情绪分一致', String(s.gauge) === String(s.conclusionScore),
  `卡片 ${s.gauge} / 结论条 ${s.conclusionScore}`);

await page.click('[data-hub="survey"]');
await page.waitForTimeout(5500);
s = await snap();
check('切到判断层后仅判断层可见', s.activeSegs.join(',') === 'survey' && s.surveyVisible && !s.factVisible && !s.decisionVisible);
check('判断层有调查面板', s.surveyPanels >= 5, `panels=${s.surveyPanels}`);
check('判断层面板铺满宽度（未被挤成窄条）',
  !!s.surveyLayout && s.surveyLayout.panelW >= s.surveyLayout.viewW * 0.8 && s.surveyLayout.leftGap <= 24,
  s.surveyLayout ? `panel ${s.surveyLayout.panelW}/${s.surveyLayout.viewW}px · left ${s.surveyLayout.leftGap}px` : 'n/a');

console.log('\nC. 卡片内跳层（不再跨工作区）');
await page.click('[data-hub="decision"]');
await page.waitForTimeout(1500);
const link = await page.$('[data-act="open-market"]');
check('决策层存在「事实层」链接', !!link);
if (link) {
  await link.click();
  await page.waitForTimeout(900);
  s = await snap();
  check('点击后切到事实层（未离开决策台）', s.activeSegs.join(',') === 'fact', s.activeSegs.join(','));
}

console.log('\nD. 深链与布局');
await page.goto(`${BASE}/dashboard.html#market`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(6000);
s = await snap();
check('#market 落到事实层', s.activeSegs.join(',') === 'fact', s.hash);
await page.goto(`${BASE}/dashboard.html#survey`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(6000);
s = await snap();
check('#survey 落到判断层', s.activeSegs.join(',') === 'survey', s.hash);
const ov1600 = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
check('1600 无横向溢出', ov1600 <= 0, `overflow=${ov1600}`);
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(700);
const ov390 = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
check('390 无横向溢出', ov390 <= 0, `overflow=${ov390}`);

const realErrors = errors.filter(e => !/favicon|Failed to load resource/i.test(e));
check('控制台零错误', realErrors.length === 0, realErrors.slice(0, 2).join(' | '));

console.log(`\n结果：${pass} PASS / ${fail} FAIL`);
await browser.close();
process.exit(fail ? 1 : 0);
