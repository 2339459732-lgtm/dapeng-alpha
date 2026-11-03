// 全站页面健康扫描：pageerror / console.error / 关键区块是否渲染
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8900';
const PAGES = [
  ['index.html', null],
  ['dashboard.html', null],
  ['sentiment.html', null],
  ['quant.html', null],
  ['project-docs.html', null],
  ['api-reference.html', null],
  ['admin.html', '#dash-sectors'],
];

const browser = await chromium.launch({ channel: 'chrome' });
const report = [];

for (const [p, sel] of PAGES) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errs = [];
  const fails = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 160)); });
  page.on('response', r => { if (r.status() >= 400 && !r.url().includes('favicon')) fails.push(r.status() + ' ' + r.url().replace(BASE, '')); });

  try {
    await page.goto(BASE + '/' + p, { waitUntil: 'domcontentloaded', timeout: 45000 });
  } catch (e) {
    errs.push('goto: ' + e.message.split('\n')[0]);
  }
  await page.waitForTimeout(8000);

  let block = '';
  if (sel) {
    block = await page.evaluate(s => {
      const el = document.querySelector(s);
      if (!el) return 'MISSING';
      return el.children.length + ' children';
    }, sel).catch(e => 'evalerr ' + e.message);
  }

  // 文档页：检查「问题与解决」条目数（今日新增 I-36..I-42）
  let docsIssues = null;
  if (p === 'project-docs.html') {
    docsIssues = await page.evaluate(() => {
      const cards = document.querySelectorAll('#sec-issues .docs-card');
      const rows = document.querySelectorAll('#sec-issues table tbody tr');
      return { cards: cards.length, rows: rows.length };
    }).catch(() => null);
  }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  const title = await page.title();

  report.push({
    page: p,
    title,
    errs: errs.slice(0, 4),
    fails: [...new Set(fails)].slice(0, 4),
    block,
    overflow,
    docsIssues,
  });
  await page.close();
}

for (const r of report) {
  console.log(`\n== ${r.page}  「${r.title}」`);
  console.log(`   横向溢出=${r.overflow}px${r.block ? '  区块=' + r.block : ''}`);
  if (r.docsIssues) console.log(`   问题条目=${JSON.stringify(r.docsIssues)}`);
  console.log(`   4xx/5xx=${r.fails.length ? JSON.stringify(r.fails) : '无'}`);
  console.log(`   错误=${r.errs.length ? JSON.stringify(r.errs) : '无'}`);
}

await browser.close();
