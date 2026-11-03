// 多视口 + 缩放重排 + 稳定性检查
import { chromium } from 'playwright';
const BASE = 'http://127.0.0.1:8900';
const browser = await chromium.launch({ channel: 'chrome' });
const errs = [];

async function open(w, h) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 140)); });
  await page.goto(BASE + '/project-docs.html', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(800);
  await (await page.$('[data-dg-view="graph"]')).click();
  await page.waitForTimeout(4200);
  return page;
}

const probe = (page) => page.evaluate(() => {
  const st = window.DocsGraph.state;
  const cv = document.getElementById('dg-canvas').getBoundingClientRect();
  const chart = echarts.getInstanceByDom(document.getElementById('dg-canvas'));
  const ids = Object.keys(st.pos);
  let minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
  ids.forEach(id => {
    const p = chart.convertToPixel({ seriesIndex: 0 }, st.pos[id]);
    if (!p) return;
    minX = Math.min(minX, p[0]); maxX = Math.max(maxX, p[0]);
    minY = Math.min(minY, p[1]); maxY = Math.max(maxY, p[1]);
  });
  return {
    canvas: [Math.round(cv.width), Math.round(cv.height)],
    coverage: [Math.round((maxX - minX) / cv.width * 100), Math.round((maxY - minY) / cv.height * 100)],
    centerOffset: [Math.round((minX + maxX) / 2 - cv.width / 2), Math.round((minY + maxY) / 2 - cv.height / 2)],
    hOverflow: document.documentElement.scrollWidth > window.innerWidth,
    zoomText: (document.getElementById('dg-zoom-val') || {}).textContent,
    zoom: Math.round(Number((echarts.getInstanceByDom(document.getElementById('dg-canvas')).getOption().series[0] || {}).zoom) * 100) / 100,
    rendered: st.rendered, paints: st.paints,
  };
});

// 1) 桌面
let page = await open(1600, 1000);
console.log('1600:', JSON.stringify(await probe(page)));
await page.screenshot({ path: 'shots/v27-desktop.png' });

// 2) 缩小窗口触发 applyFit
await page.setViewportSize({ width: 1200, height: 900 });
await page.waitForTimeout(1200);
console.log('1200(重排):', JSON.stringify(await probe(page)));
await page.screenshot({ path: 'shots/v27-resized.png' });
await page.close();

// 3) 平板 / 手机
for (const [w, h, tag] of [[900, 900, 'narrow900'], [760, 900, 'narrow760']]) {
  const p2 = await open(w, h);
  console.log(tag + ':', JSON.stringify(await probe(p2)));
  await p2.screenshot({ path: `shots/v27-${tag}.png` });
  await p2.close();
}

// 4) 静置 2 秒看是否持续重绘（预期 rendered 不再增长）
page = await open(1600, 1000);
const a = await probe(page);
await page.waitForTimeout(2000);
const b = await probe(page);
console.log('静置增量 rendered/paints:', b.rendered - a.rendered, b.paints - a.paints);
await page.close();

console.log('错误=' + (errs.length ? JSON.stringify(errs.slice(0, 5)) : '无'));
await browser.close();
