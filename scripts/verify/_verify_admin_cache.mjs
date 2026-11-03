// 验证 admin.html 缓存页：统计卡片 / 过期标记 / 「回收过期」按钮
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8900';
const out = [];
const errs = [];

const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
page.on('pageerror', e => errs.push('pageerror: ' + e.message));

await page.goto(BASE + '/admin.html', { waitUntil: 'domcontentloaded' });

// 直接调用页面内的 loadCache()，不依赖侧边栏路由状态
await page.waitForFunction(() => typeof window.loadCache === 'function', { timeout: 20000 });
await page.evaluate(() => window.loadCache());
await page.waitForFunction(() => {
  const el = document.getElementById('cache-stats');
  return el && el.children.length > 0;
}, { timeout: 20000 });

const stats = await page.evaluate(() => {
  const cards = Array.from(document.querySelectorAll('#cache-stats .stat-card'));
  return cards.map(c => ({
    label: (c.querySelector('.stat-label') || {}).textContent || '',
    value: (c.querySelector('.stat-value') || {}).textContent || '',
    sub: (c.querySelector('.stat-sub') || {}).textContent || '',
  }));
});
out.push('统计卡片数=' + stats.length);
stats.forEach(s => out.push(`  · ${s.label} = ${s.value.trim()}  (${s.sub.trim()})`));

const rows = await page.evaluate(() => document.querySelectorAll('#cache-tbody tr').length);
out.push('表格行数=' + rows);

const sub = await page.evaluate(() => {
  const el = document.getElementById('cache-subtitle');
  return el ? el.textContent.trim() : null;
});
out.push('副标题=' + sub);

const btn = await page.evaluate(() => {
  const b = Array.from(document.querySelectorAll('#page-cache button'))
    .find(x => (x.textContent || '').includes('回收过期'));
  return b ? b.textContent.trim() : null;
});
out.push('回收按钮=' + JSON.stringify(btn));

// 点击「回收过期」→ 应弹出 toast 且不报错
if (btn) {
  await page.evaluate(() => window.purgeExpiredCache());
  await page.waitForTimeout(1500);
  const toast = await page.evaluate(() => {
    const t = document.querySelector('.toast, #toast, .toast-item');
    return t ? t.textContent.trim() : '(未捕获到 toast)';
  });
  out.push('点击后提示=' + toast);
}

// 侧边栏「缓存管理」入口存在性
const navHas = await page.evaluate(() => document.body.innerText.includes('缓存'));
out.push('页面含缓存区块=' + navHas);

console.log(out.join('\n'));
console.log('控制台错误=' + (errs.length ? JSON.stringify(errs.slice(0, 5)) : '无'));
await browser.close();
