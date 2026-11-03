import { chromium } from 'playwright';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', e => errs.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });

// 1) 侧边栏入口
await page.goto('http://127.0.0.1:8900/dashboard.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2500);
const sidebar = await page.evaluate(() => {
  const links = [...document.querySelectorAll('.sidebar-tool')].map(a => ({ text: a.textContent.trim(), href: a.getAttribute('href') }));
  return links.filter(x => x.text.includes('龙虎榜'));
});

// 2) 决策层卡片
await page.evaluate(() => { if (typeof switchHubSegment === 'function') switchHubSegment('decision'); });
await page.waitForTimeout(4000);
const cardInfo = await page.evaluate(() => {
  const cards = [...document.querySelectorAll('#decision-mount .dd-card')];
  const c = cards.find(x => /龙虎榜/.test(x.textContent));
  if (!c) return { found: false, cardTitles: cards.map(x => x.querySelector('strong')?.textContent) };
  const link = c.querySelector('a.dd-link');
  return {
    found: true,
    head: c.querySelector('.dd-card-head')?.textContent.trim(),
    linkText: link?.textContent.trim(), linkHref: link?.getAttribute('href'),
    rows: [...c.querySelectorAll('.dd-row')].map(r => r.textContent.replace(/\s+/g, ' ').trim()).slice(0, 6),
    note: c.querySelector('.dd-note')?.textContent.trim()
  };
});
console.log(JSON.stringify({ sidebar, cardInfo, errs }, null, 1));
await browser.close();
