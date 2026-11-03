// 探针：量化星系（浑天仪）拖拽在各空间姿态下的实际响应
// 目的：找出「拖不动/拖了没反应/方向不对」的具体角度与区域，而不是靠猜
// 运行：node scripts/verify/probe_armillary_drag.mjs
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8900';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
// 关掉星系自转与呼吸动效：否则环境动画会污染「拖拽是否有响应」的测量
await page.addInitScript(() => {
  try {
    localStorage.setItem('axon-plate-v5', JSON.stringify({ galaxySpin: 'off', galSpeed: 'halt', motion: 'reduce' }));
  } catch (e) {}
});
const errs = [];
page.on('pageerror', e => errs.push('pageerror: ' + e.message));
await page.goto(`${BASE}/plate-graph.html`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1500);

// 进入星系：循环点 view3d-btn 直到标签为「星系」
for (let i = 0; i < 6; i++) {
  const label = await page.$eval('#view3d-btn .v3d-label', el => el.textContent.trim());
  if (label === '星系') break;
  await page.click('#view3d-btn');
  await page.waitForTimeout(350);
}
const mode = await page.$eval('#view3d-btn .v3d-label', el => el.textContent.trim());
console.log('当前视角：', mode);

// 画布签名：把 gal-canvas 降采样成 32x20 的占用网格 + 亮像素质心
async function sig() {
  return page.evaluate(() => {
    const c = document.getElementById('gal-canvas');
    if (!c) return null;
    const ctx = c.getContext('2d');
    const W = c.width, H = c.height;
    const img = ctx.getImageData(0, 0, W, H).data;
    const GX = 40, GY = 24;
    const grid = new Array(GX * GY).fill(0);
    let sx = 0, sy = 0, n = 0, sum = 0;
    // 背景是近黑（rgb 4,4,11）：必须用亮度阈值，alpha 恒为 255 会全饱和
    const LUM = 46;
    for (let y = 0; y < H; y += 2) {
      for (let x = 0; x < W; x += 2) {
        const i = (y * W + x) * 4;
        const lum = 0.2126 * img[i] + 0.7152 * img[i + 1] + 0.0722 * img[i + 2];
        sum += lum;
        if (lum > LUM) {
          const gx = Math.min(GX - 1, (x * GX / W) | 0);
          const gy = Math.min(GY - 1, (y * GY / H) | 0);
          grid[gy * GX + gx]++;
          sx += x; sy += y; n++;
        }
      }
    }
    return { grid, cx: n ? sx / n : 0, cy: n ? sy / n : 0, n, lum: sum / (W * H / 4) };
  });
}

function diff(a, b) {
  if (!a || !b) return { ratio: 0, shift: 0 };
  let changed = 0;
  for (let i = 0; i < a.grid.length; i++) if ((a.grid[i] > 0) !== (b.grid[i] > 0)) changed++;
  return {
    ratio: changed / a.grid.length,
    shift: Math.hypot(b.cx - a.cx, b.cy - a.cy),
    painted: a.n,
    lumA: a.lum, lumB: b.lum,
  };
}

async function drag(x0, y0, dx, dy, steps = 12) {
  await page.mouse.move(x0, y0);
  await page.mouse.down();
  for (let i = 1; i <= steps; i++) {
    await page.mouse.move(x0 + dx * i / steps, y0 + dy * i / steps);
    await page.waitForTimeout(12);
  }
  await page.mouse.up();
  await page.waitForTimeout(260);
}

async function resetGalaxy() {
  // 切到平面再切回星系，等价于重置相机预设
  await page.click('#view3d-btn');
  await page.waitForTimeout(250);
  for (let i = 0; i < 5; i++) {
    const label = await page.$eval('#view3d-btn .v3d-label', el => el.textContent.trim());
    if (label === '星系') break;
    await page.click('#view3d-btn');
    await page.waitForTimeout(250);
  }
  await page.waitForTimeout(500);
}

const CX = 800, CY = 470;
const results = [];

async function probe(name, setup, dx, dy, from = [CX, CY]) {
  await resetGalaxy();
  if (setup) await setup();
  const a = await sig();
  await drag(from[0], from[1], dx, dy);
  const b = await sig();
  const d = diff(a, b);
  results.push({ name, ...d, from: from.join(',') });
}

// 1) 基准姿态：水平 / 垂直
await probe('基准·水平拖 240', null, 240, 0);
await probe('基准·垂直拖 240', null, 0, 240);
// 2) 接近极点（俯仰 ≈ +90°：dy=317 → rx≈0.62+0.95=1.57）
await probe('极点附近·水平拖 240', async () => { await drag(CX, CY, 0, 317); }, 240, 0);
// 3) 越过极点（俯仰 ≈ 180°：dy=630）
await probe('越过极点(180°)·水平拖 240', async () => { await drag(CX, CY, 0, 630); }, 240, 0);
// 4) 极点附近继续垂直拖（期待还能继续转）
await probe('极点附近·再垂直拖 240', async () => { await drag(CX, CY, 0, 317); }, 0, 240);
// 5) 边角与栏位起拖
await probe('左上角起拖', null, 200, 120, [40, 120]);
await probe('右下角起拖', null, -200, -120, [1560, 900]);
await probe('右侧说明栏起拖', null, -200, 0, [1540, 420]);
await probe('底部图例起拖', null, 200, 0, [800, 930]);

// 6) 释放到窗口外 / 未收到 pointerup 时的手感（drag 状态是否残留）
await resetGalaxy();
const beforeStuck = await sig();
await page.mouse.move(CX, CY);
await page.mouse.down();
await page.mouse.move(CX + 80, CY + 40, { steps: 6 });
await page.mouse.move(1595, 995, { steps: 6 });
await page.mouse.up();
await page.waitForTimeout(400);
const afterUp = await sig();
await page.mouse.move(CX - 260, CY - 200, { steps: 10 }); // 不按键移动
await page.waitForTimeout(400);
const afterMoveNoButton = await sig();
const stuck = diff(afterUp, afterMoveNoButton);

console.log('\n姿态 / 手势                        画布变化率  质心位移  起拖点');
for (const r of results) {
  const flag = r.ratio < 0.04 && r.shift < 5 ? '  ← 无响应（死角）' : (r.ratio > 0.60 ? '  ← 变化过大（跳变）' : '');
  console.log(`${r.name.padEnd(30)} ${(r.ratio * 100).toFixed(1).padStart(7)}% ${r.shift.toFixed(1).padStart(8)}px  亮${(r.lumA).toFixed(1)}→${(r.lumB).toFixed(1)} ${r.from}${flag}`);
}
console.log(`\n未按键移动画布变化率 ${(stuck.ratio * 100).toFixed(1)}%  质心位移 ${stuck.shift.toFixed(1)}px`
  + (stuck.ratio > 0.08 ? '  ← drag 状态残留：没有按键也在转' : '  ← 正常'));
console.log('控制台错误：', errs.length ? errs.slice(0, 3) : '无');

await browser.close();
