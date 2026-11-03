// 验收：龙虎榜复盘页（lhb-review.html）渲染与交互
// 运行：node scripts/verify/_verify_lhb_page.mjs
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const BASE = 'http://127.0.0.1:8900';
const URL = BASE + '/lhb-review.html';
const SHOT_DIR = resolve(process.cwd(), 'shots');
const SHOT = resolve(SHOT_DIR, 'lhb-review-page.png');

const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

const errors = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
page.on('console', (m) => {
  if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 300));
});

async function count(sel) {
  return page.locator(sel).count();
}

// 在标注了英文小标题（BY SEAT / BY INDUSTRY）的面板内统计
async function inPanel(en, sel) {
  return page.evaluate(
    ({ en, sel }) => {
      const panels = [...document.querySelectorAll('.lr-panel')];
      const p = panels.find((x) => {
        const s = x.querySelector('.lr-panel-head span');
        return s && s.textContent.trim() === en;
      });
      return p ? p.querySelectorAll(sel).length : -1;
    },
    { en, sel }
  );
}

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(3000);

// 数据若慢于 3s，最多再等 15s（不额外拖慢正常路径）
try {
  await page.waitForSelector('#lr-main .lr-panel', { timeout: 15000 });
} catch {
  /* 交给下面的空态统计体现 */
}

// ---------------- 静态结构统计 ----------------
const heroText = (await page.locator('.lr-hero-text').count())
  ? (await page.locator('.lr-hero-text').first().innerText()).trim()
  : null;

const kpiCount = await count('.lr-kpi');
const ctxCellCount = await count('.lr-ctx-cell');
const findCount = await count('.lr-find');
const barRowCount = await count('.lr-bar-row');
const motiveBars = await inPanel('MOTIVE DISTRIBUTION', '.lr-bar-row');
const industryBars = await inPanel('BY INDUSTRY', '.lr-bar-row');
const factorBuyRows = await inPanel('FACTOR HITS', '.lr-fac-col.buy .lr-fac-row');
const factorSellRows = await inPanel('FACTOR HITS', '.lr-fac-col.sell .lr-fac-row');

const firstTable = await page.evaluate(() => {
  const t = document.querySelectorAll('table.lr-table')[0];
  if (!t) return null;
  return {
    rows: t.querySelectorAll('tbody tr').length,
    head: [...t.querySelectorAll('thead th')].map((th) => th.textContent.trim()).join(' | '),
  };
});

const recCount = await count('tr.lr-rec');
const chipCount = await count('.lr-fchip');
const chips = await page.evaluate(() => {
  const pick = (attr) =>
    [...document.querySelectorAll('.lr-fchip[' + attr + ']')].map((e) => e.getAttribute(attr) || '(全部)');
  return { seat: pick('data-f-seat'), side: pick('data-f-side'), motive: pick('data-f-motive') };
});

const seatRows = await inPanel('BY SEAT', 'tbody tr');
const emptyCount = await count('.lr-empty');
const emptyTexts = await page.locator('.lr-empty').allInnerTexts();
const stamp = (await page.locator('#lr-stamp').count()) ? (await page.locator('#lr-stamp').innerText()).trim() : '';
const dateVal = await page.locator('#lr-date').inputValue();

// ---------------- 交互 1：展开明细 ----------------
const recLoc = page.locator('tr.lr-rec');
let detailExists = false;
let detailSections = null;
if (recCount > 0) {
  await recLoc.first().click();
  await page.waitForTimeout(500);
  detailExists = (await count('tr.lr-detail')) > 0;
  if (detailExists) {
    detailSections = await page.evaluate(() => {
      const d = document.querySelector('tr.lr-detail');
      const secs = [...d.querySelectorAll('.lr-detail-inner > div')].map((div) => {
        const head = (div.querySelector('.lr-detail-sec-head') || {}).textContent || '';
        const all = (div.textContent || '').replace(/\s+/g, ' ').trim();
        return { head: head.trim(), text: all.replace(head.trim(), '').trim() };
      });
      const grab = (k) => {
        const s = secs.find((x) => x.head.includes(k));
        return s ? s.text.slice(0, 200) : null;
      };
      return {
        heads: secs.map((s) => s.head),
        dims: grab('五维分数'),
        logic: grab('推演逻辑'),
        factors: grab('买卖因素命中'),
        factorHitCount: d.querySelectorAll('.lr-fac-hit').length,
        factorSides: [...d.querySelectorAll('.lr-fac-hit-side')].map((e) => e.className),
      };
    });
  }
}

const expandedRows = await count('tr.lr-detail');

// ---------------- 交互 2：席位 chip 筛选 ----------------
const seatChips = await page.evaluate(() => {
  return [...document.querySelectorAll('[data-f-seat]')]
    .filter((e) => (e.getAttribute('data-f-seat') || '') !== '')
    .map((e) => e.getAttribute('data-f-seat'));
});
let seatFilterKey = null;
let recAfterFilter = null;
let countTextAfterFilter = null;
if (seatChips.length) {
  seatFilterKey = seatChips[0];
  await page.locator('[data-f-seat="' + seatFilterKey + '"]').first().click();
  await page.waitForTimeout(500);
  recAfterFilter = await count('tr.lr-rec');
  countTextAfterFilter = (await page.locator('.lr-count').count())
    ? (await page.locator('.lr-count').first().innerText()).trim()
    : null;
}

// ---------------- 交互 3：重置 ----------------
await page.locator('.lr-reset').first().click();
await page.waitForTimeout(500);
const recAfterReset = await count('tr.lr-rec');
const countTextAfterReset = (await page.locator('.lr-count').count())
  ? (await page.locator('.lr-count').first().innerText()).trim()
  : null;

// ---------------- 交互 4：表头排序 ----------------
const sortHeaders = await page.evaluate(() =>
  [...document.querySelectorAll('th[data-sort]')].map((th) => th.getAttribute('data-sort'))
);
let sortKey = null;
let sortedFirstRow = null;
if (sortHeaders.length) {
  sortKey = sortHeaders.includes('net') ? 'net' : sortHeaders[0];
  await page.locator('th[data-sort="' + sortKey + '"]').first().click();
  await page.waitForTimeout(400);
  sortedFirstRow = await page.evaluate(() => {
    const t = document.querySelectorAll('table.lr-table')[0];
    if (!t) return null;
    const tr = t.querySelector('tbody tr');
    if (!tr) return null;
    const name = (tr.querySelector('.lr-stock-name') || {}).textContent || '';
    const cells = [...tr.querySelectorAll('td')].map((td) => td.textContent.trim());
    return { name: name.trim(), cells };
  });
}

// ---------------- 滚动能力诊断（页面能否看到首屏以下内容） ----------------
const scrollBefore = await page.evaluate(() => ({
  scrollY,
  scrollHeight: document.scrollingElement.scrollHeight,
  clientHeight: document.scrollingElement.clientHeight,
  htmlOverflow: getComputedStyle(document.documentElement).overflow,
  bodyOverflow: getComputedStyle(document.body).overflow,
  bodyHeight: getComputedStyle(document.body).height,
  panelsBelowFold: [...document.querySelectorAll('.lr-panel')].filter((e) => {
    const b = e.getBoundingClientRect();
    return b.top > innerHeight;
  }).length,
  panelCount: document.querySelectorAll('.lr-panel').length,
}));
await page.mouse.wheel(0, 2000);
await page.waitForTimeout(400);
const scrollAfter = await page.evaluate(() => ({ scrollY, wrapScrollTop: document.querySelector('.lr-wrap').scrollTop }));

// ---------------- 截图 ----------------
mkdirSync(SHOT_DIR, { recursive: true });
// 真实视口（1600x1000）：记录页面被 overflow:hidden 截断的现场
const viewportShot = resolve(SHOT_DIR, 'lhb-review-page-viewport.png');
await page.screenshot({ path: viewportShot });

// 要求项：全页截图。html/body 被 dashboard.css 锁成 overflow:hidden，
// 直接 fullPage 只能拍到首屏 + 大片空白，故仅对本次截图临时解除该限制。
await page.addStyleTag({ content: 'html,body{overflow:visible !important;height:auto !important}' });
await page.waitForTimeout(300);
await page.screenshot({ path: SHOT, fullPage: true });

// ---------------- 汇总 ----------------
const result = {
  url: URL,
  date: dateVal,
  stamp,
  heroText,
  heroTextLength: heroText ? heroText.length : 0,
  kpi: kpiCount,
  ctxCells: ctxCellCount,
  findings: findCount,
  barRows: barRowCount,
  motiveBars,
  industryBars,
  factorBuyRows,
  factorSellRows,
  stockTable: firstTable,
  recRows: recCount,
  chips: chipCount,
  chipValues: chips,
  seatTableRows: seatRows,
  empties: emptyCount,
  emptyTexts,
  expand: {
    detailRowShown: detailExists,
    detailRowCount: expandedRows,
    sectionHeads: detailSections ? detailSections.heads : null,
    dims200: detailSections ? detailSections.dims : null,
    logic200: detailSections ? detailSections.logic : null,
    factors200: detailSections ? detailSections.factors : null,
    factorHitCount: detailSections ? detailSections.factorHitCount : 0,
    factorSides: detailSections ? detailSections.factorSides : null,
  },
  filter: {
    seatKey: seatFilterKey,
    rowsAfter: recAfterFilter,
    countTextAfter: countTextAfterFilter,
    rowsAfterReset: recAfterReset,
    countTextAfterReset,
    restored: recAfterReset === recCount,
  },
  sort: {
    key: sortKey,
    headers: sortHeaders,
    firstRowName: sortedFirstRow ? sortedFirstRow.name : null,
    firstRowCells: sortedFirstRow ? sortedFirstRow.cells : null,
  },
  scroll: {
    ...scrollBefore,
    afterWheelScrollY: scrollAfter.scrollY,
    afterWheelWrapScrollTop: scrollAfter.wrapScrollTop,
    scrollable: scrollAfter.scrollY > 0 || scrollAfter.wrapScrollTop > 0,
  },
  screenshot: SHOT,
  viewportScreenshot: viewportShot,
  errors,
  errorCount: errors.length,
};

console.log(JSON.stringify(result, null, 1));

await browser.close();
