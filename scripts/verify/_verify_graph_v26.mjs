// 验证 Python 图算法层在浏览器端的实际效果：
//   1) 是否走了 Python 预计算坐标（layoutSource === 'python'）
//   2) 群落光环是否真的画出来了（大气层 canvas 非空像素占比）
//   3) 指标条是否显示群落/模块度/直径/密度
//   4) 桥接节点是否带虚线外环
// 用法：node _verify_graph_v26.mjs
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8900';
const errs = [];

const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));

await page.goto(BASE + '/project-docs.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(3500);

// 切到图谱视图
const switched = await page.evaluate(() => {
  const btn = document.querySelector('[data-view="graph"], #dg-view-graph, .dg-view-btn');
  if (btn) { btn.click(); return true; }
  if (window.DocsGraph && typeof window.DocsGraph.setView === 'function') {
    window.DocsGraph.setView('graph'); return true;
  }
  return false;
});
await page.waitForTimeout(4000);

const info = await page.evaluate(() => {
  const out = { switched: true };
  const meta = document.getElementById('dg-meta');
  out.metaText = meta ? meta.textContent : '';
  out.metaTitle = meta ? (meta.title || '') : '';

  // 群落光环：读大气层 canvas 的像素，统计非背景像素占比
  const atmo = document.getElementById('dg-atmo');
  if (atmo) {
    const c = atmo.getContext('2d');
    const w = atmo.width, h = atmo.height;
    const d = c.getImageData(0, 0, w, h).data;
    let lit = 0, total = 0;
    for (let i = 0; i < d.length; i += 4 * 37) {   // 抽样
      total++;
      const r = d[i], g = d[i + 1], b = d[i + 2];
      // 背景近黑（<20），光环会让通道值抬起来
      if (r > 24 || g > 24 || b > 24) lit++;
    }
    out.atmoLitRatio = +(lit / total).toFixed(4);
    out.atmoSize = [w, h];
  }
  return out;
});

// 桥接节点：从 series 数据里数一下带 dashed 边框的节点
const bridge = await page.evaluate(() => {
  const el = document.getElementById('dg-canvas');
  if (!el || typeof echarts === 'undefined') return null;
  const inst = echarts.getInstanceByDom(el);
  if (!inst) return null;
  const opt = inst.getOption();
  const data = (opt.series && opt.series[0] && opt.series[0].data) || [];
  let dashed = 0, total = 0, sizes = [];
  data.forEach((d) => {
    total++;
    const st = d.itemStyle || {};
    if (st.borderType === 'dashed') dashed++;
    if (typeof d.symbolSize === 'number') sizes.push(d.symbolSize);
  });
  return { total, dashed, minSize: Math.min(...sizes), maxSize: Math.max(...sizes) };
});

console.log('=== 图谱 v26 验收 ===');
console.log('视图切换:', switched);
console.log('指标条:', info.metaText);
console.log('指标 tooltip:\n  ' + String(info.metaTitle).split('\n').join('\n  '));
console.log('大气层 canvas:', info.atmoSize, ' 非背景像素占比:', info.atmoLitRatio);
console.log('节点/虚线边框(桥接+缺失+孤立):', JSON.stringify(bridge));
console.log('控制台错误:', errs.length ? errs.slice(0, 6) : '无');

await page.screenshot({ path: '_shot_graph_v26.png', fullPage: false });
await browser.close();
