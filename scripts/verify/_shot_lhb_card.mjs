import { chromium } from 'playwright';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.goto('http://127.0.0.1:8900/dashboard.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2200);
// 两步：先切到决策工作区（display），再切到决策层 hub（面板互斥）
await page.evaluate(() => {
  if (typeof switchWorkspace === 'function') switchWorkspace('decision');
  if (typeof switchHubSegment === 'function') switchHubSegment('decision');
});
await page.waitForTimeout(4500);
// 用 locator 而不是 ElementHandle：决策层是渐进渲染，句柄会因重绘失效
const card = page.locator('#decision-mount .dd-card', { hasText: '龙虎榜复盘' }).first();
await card.waitFor({ state: 'visible', timeout: 20000 });
// 复盘数据是独立异步拉的：卡片可见≠数据到了，等「主因分布」行出现再截
await card.locator('.dd-row', { hasText: '主因分布' }).waitFor({ timeout: 30000 });
await card.scrollIntoViewIfNeeded();
await page.waitForTimeout(400);
await card.screenshot({ path: 'shots/lr-card.png' });
console.log('done');
await browser.close();
