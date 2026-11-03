// 验收：拆分 / 契约 / 卫生三轮改动后，项目文档页与决策台仍正常（P2–P4）
// 运行：node scripts/verify/verify_split_round.mjs
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8900';
const browser = await chromium.launch({ channel: 'chrome' });
const errors = [];
let pass = 0, fail = 0;
function check(name, ok, detail = '') {
  if (ok) { pass++; console.log(`  PASS ${name}`); }
  else { fail++; console.log(`  FAIL ${name} ${detail}`); }
}

// ---- 1. 项目文档页 ----
{
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 160)); });
  await page.goto(`${BASE}/project-docs.html`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);

  const info = await page.evaluate(() => {
    const text = document.body.innerText;
    const rows = [...document.querySelectorAll('tr')].map(tr => tr.innerText.replace(/\s+/g, ' '));
    return {
      hasSplitRow: text.includes('模块瘦身（P2）'),
      hasContractRow: text.includes('契约统一（P3）'),
      hasHygieneRow: text.includes('工程卫生（P4）'),
      archRows: document.querySelectorAll('tr').length,
      overflow: document.documentElement.scrollWidth - window.innerWidth,
      deadAnchor: [...document.querySelectorAll('a[href^="#"]')].filter(a => {
        const id = a.getAttribute('href').slice(1);
        return id && !document.getElementById(id);
      }).length,
      navItems: document.querySelectorAll('.docs-nav-btn').length,
      sample: rows.find(r => r.includes('模块瘦身')) || '',
    };
  });
  check('架构章节新增 P2 行', info.hasSplitRow);
  check('架构章节新增 P3 行', info.hasContractRow);
  check('架构章节新增 P4 行', info.hasHygieneRow);
  check('架构章节表格非空', info.archRows >= 20, `rows=${info.archRows}`);
  check('1600 无横向溢出', info.overflow <= 0, `overflow=${info.overflow}`);
  check('锚点零死链', info.deadAnchor === 0, `dead=${info.deadAnchor}`);
  check('顶栏导航项 >= 5', info.navItems >= 5, `nav=${info.navItems}`);
  console.log('   示例行：', info.sample.slice(0, 110));

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(400);
  const m = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check('390 无横向溢出', m <= 0, `overflow=${m}`);
  await page.close();
}

// ---- 2. 决策台（拆分后专家团 / 扫描卡仍在） ----
{
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 160)); });
  await page.goto(`${BASE}/dashboard.html`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(600);
  const tab = await page.$('#workspace-decision-tab');
  if (tab) await tab.click();
  await page.waitForTimeout(20000);
  const d = await page.evaluate(() => ({
    cards: document.querySelectorAll('.dd-card').length,
    experts: document.querySelectorAll('.dd-expert-tile').length,
    scan: document.body.innerText.includes('全市场扫描'),
    overflow: document.documentElement.scrollWidth - window.innerWidth,
  }));
  check('决策台卡片数 >= 15', d.cards >= 15, `cards=${d.cards}`);
  check('专家卡存在', d.experts >= 6, `experts=${d.experts}`);
  check('全市场扫描卡存在', d.scan);
  check('决策台无横向溢出', d.overflow <= 0, `overflow=${d.overflow}`);
  await page.close();
}

await browser.close();
const realErrors = errors.filter(e => !/favicon|ERR_INTERNET|Failed to load resource/i.test(e));
check('控制台零错误', realErrors.length === 0, realErrors.slice(0, 3).join(' | '));
console.log(`\n结果：${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);
