// 减少动效 / 主题设置 回归
import { chromium } from 'playwright';
const browser = await chromium.launch({ channel: 'chrome' });
const errs = [];
const page = await browser.newPage({ viewport: { width: 1400, height: 950 }, reducedMotion: 'reduce' });
page.on('pageerror', e => errs.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 120)); });
await page.goto('http://127.0.0.1:8900/project-docs.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(700);
await (await page.$('[data-dg-view="graph"]')).click();
await page.waitForTimeout(2500);
// 选中：reduce 下不应产生脉冲元素
await page.evaluate(() => window.DocsGraph.selectNode('README.md'));
const ping = await page.evaluate(() => document.querySelectorAll('.dg-ping').length);
await page.screenshot({ path: 'shots/v27-reduced.png' });
// 主题：关光晕 / 关动效
const theme = await page.evaluate(() => window.DocsGraph.setTheme({ glow: 0, motion: 'off', nodes: 'compact' }));
await page.waitForTimeout(800);
const size = await page.evaluate(() => {
  const d = echarts.getInstanceByDom(document.getElementById('dg-canvas')).getOption().series[0].data;
  return { max: Math.max(...d.map(x => x.symbolSize)), particles: window.DocsGraph.particles().running };
});
console.log(JSON.stringify({ pingUnderReduce: ping, theme, size, errors: errs }, null, 1));
await browser.close();
