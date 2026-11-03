// 验收：浑天仪三环拖拽（armillary-drag.v1）
//
// 为什么不用「质心位移」当唯一指标：
//   星系是一个近似平坦、近似轴对称的盘。当俯仰到 ±90°（盘面正对镜头）时，
//   绕屏幕竖轴旋转在几何上表现为「横向压扁」——盘心几乎不动，边缘内收。
//   只测质心会把这种**正确**的反馈误判成「无响应」。
//
// 因此分三项：
//   A. 运动能量：全部星点的平均屏幕位移（每个姿态都要动起来）
//   B. 画幅形变：亮点包围盒 宽/高 的变化（极点处应能明显压扁/立起）
//   C. 近轴跟手：取偏离旋转轴 120px 以上的星点，位移方向应与指针同号
//
// 运行：node scripts/verify/verify_armillary_drag.mjs
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8900';
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on('pageerror', e => errors.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 140)); });

// 固定确定性状态：关自转、停动效，否则环境动画会污染测量
await page.addInitScript(() => {
  try {
    localStorage.setItem('axon-plate-v5', JSON.stringify({ galaxySpin: 'off', galSpeed: 'halt', motion: 'reduce' }));
  } catch (e) {}
});
await page.goto(`${BASE}/plate-graph.html`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1500);

let pass = 0, fail = 0;
const check = (name, ok, detail = '') => {
  if (ok) { pass++; console.log(`  PASS ${name}${detail ? ' · ' + detail : ''}`); }
  else { fail++; console.log(`  FAIL ${name}${detail ? ' · ' + detail : ''}`); }
};

async function toGalaxy() {
  for (let i = 0; i < 6; i++) {
    const l = await page.$eval('#view3d-btn .v3d-label', e => e.textContent.trim());
    if (l === '星系') return;
    await page.click('#view3d-btn');
    await page.waitForTimeout(300);
  }
}
await toGalaxy();

const snap = () => page.evaluate(() => {
  const nodes = [...document.querySelectorAll('.n3d')].map(el => {
    const r = el.getBoundingClientRect();
    return { id: el.dataset.id, x: r.left + r.width / 2, y: r.top + r.height / 2 };
  });
  // 点云主轴比 sqrt(λ1/λ2)：纯自转不改变它，压扁/立起会改变
  const mx = nodes.reduce((s, n) => s + n.x, 0) / nodes.length;
  const my = nodes.reduce((s, n) => s + n.y, 0) / nodes.length;
  let cxx = 0, cyy = 0, cxy = 0;
  for (const n of nodes) {
    const dx = n.x - mx, dy = n.y - my;
    cxx += dx * dx; cyy += dy * dy; cxy += dx * dy;
  }
  const tr = cxx + cyy, det = cxx * cyy - cxy * cxy;
  const disc = Math.sqrt(Math.max(0, tr * tr / 4 - det));
  const l1 = tr / 2 + disc, l2 = Math.max(1e-6, tr / 2 - disc);
  return { nodes, aspect: Math.sqrt(l1 / l2) };
});

async function drag(dx, dy, from = [800, 470], steps = 10) {
  await page.mouse.move(from[0], from[1]);
  await page.mouse.down();
  for (let i = 1; i <= steps; i++) {
    await page.mouse.move(from[0] + dx * i / steps, from[1] + dy * i / steps);
    await page.waitForTimeout(9);
  }
  await page.mouse.up();
  await page.waitForTimeout(300);
}

async function reset() {
  await page.click('#view3d-btn');
  await page.waitForTimeout(220);
  await toGalaxy();
  await page.waitForTimeout(320);
}

async function measure(name, preDy, dx, dy, from = [800, 470]) {
  await reset();
  if (preDy) await drag(0, preDy);
  const a = await snap();
  await drag(dx, dy, from);
  const b = await snap();
  const byId = new Map(b.nodes.map(n => [n.id, n]));
  let sum = 0, cnt = 0, near = null, nearD = 1e9;
  for (const n of a.nodes) {
    const m = byId.get(n.id);
    if (!m) continue;
    const d = Math.hypot(m.x - n.x, m.y - n.y);
    sum += d; cnt++;
    // 近轴跟手样本：偏离屏幕中心竖轴 120px 以上（不在旋转轴上）
    if (Math.abs(n.x - 800) > 120) {
      const dist = Math.hypot(n.x - from[0], n.y - from[1]);
      if (dist < nearD) { nearD = dist; near = { n, m }; }
    }
  }
  const energy = cnt ? sum / cnt : 0;
  const deform = Math.abs(b.aspect - a.aspect) / Math.max(0.001, a.aspect);
  const flow = near ? { dx: near.m.x - near.n.x, dy: near.m.y - near.n.y } : null;
  console.log(`  ${name.padEnd(22)} 运动能量 ${energy.toFixed(1)}px  主轴比 ${a.aspect.toFixed(3)}→${b.aspect.toFixed(3)}（${(deform * 100).toFixed(1)}%）`
    + (flow ? `  近轴位移(${flow.dx.toFixed(1)}, ${flow.dy.toFixed(1)})` : ''));
  return { energy, deform, flow };
}

console.log('\nA. 运动能量（每个姿态拖 100px，全部星点平均位移应 ≥8px）');
const base = await measure('基准', 0, 100, 0);
const vert = await measure('基准·竖直', 0, 0, 100);
const pole = await measure('极点(90°)·水平', 314, 100, 0);
const over = await measure('超极点(180°)·水平', 628, 100, 0);
const poleV = await measure('极点(90°)·竖直', 314, 0, 100);
check('基准水平拖动有响应', base.energy >= 8, `${base.energy.toFixed(1)}px`);
check('基准竖直拖动有响应', vert.energy >= 8, `${vert.energy.toFixed(1)}px`);
check('极点水平拖动有响应（不再退化自转）', pole.energy >= 8, `${pole.energy.toFixed(1)}px`);
check('超极点水平拖动有响应', over.energy >= 8, `${over.energy.toFixed(1)}px`);
check('极点竖直拖动有响应', poleV.energy >= 8, `${poleV.energy.toFixed(1)}px`);

console.log('\nB. 视角真的变了（点云主轴比变化 ≥1%：排除「原地自转」这种伪响应）');
check('极点水平拖动改变主轴比', pole.deform >= 0.01, `${(pole.deform * 100).toFixed(1)}%`);
check('超极点水平拖动改变主轴比', over.deform >= 0.01, `${(over.deform * 100).toFixed(1)}%`);
check('基准竖直拖动改变主轴比', vert.deform >= 0.01, `${(vert.deform * 100).toFixed(1)}%`);

console.log('\nC. 近轴跟手（离旋转轴 120px 以上的星点，位移方向应与指针同号）');
check('基准水平跟手', base.flow && base.flow.dx > 5, base.flow ? `${base.flow.dx.toFixed(1)}px` : 'n/a');
check('基准竖直跟手', vert.flow && vert.flow.dy > 5, vert.flow ? `${vert.flow.dy.toFixed(1)}px` : 'n/a');

console.log('\nD. 拖拽区域（每个起拖点都要能带动画面）');
const zones = [
  ['左上角', [40, 130]],
  ['右下角', [1560, 900]],
  ['右侧说明栏', [1545, 300]],
  ['底部图例', [800, 930]],
];
for (const [name, at] of zones) {
  const r = await measure(`区域·${name}`, 0, 90, 0, at);
  check(`起拖区「${name}」可拖`, r.energy >= 6, `${r.energy.toFixed(1)}px`);
}

console.log('\nE. 交互不回归（按钮仍可点击 / 无残留拖拽）');
await reset();
const beforeMode = await page.$eval('#view3d-btn .v3d-label', e => e.textContent.trim());
await page.click('#view3d-btn');
await page.waitForTimeout(280);
const afterMode = await page.$eval('#view3d-btn .v3d-label', e => e.textContent.trim());
check('从按钮起拖不吞点击（视角仍能切换）', beforeMode !== afterMode, `${beforeMode} → ${afterMode}`);

await reset();
const s1 = await snap();
await page.mouse.move(800, 470);
await page.mouse.down();
await page.mouse.move(900, 520, { steps: 6 });
await page.mouse.up();
await page.waitForTimeout(900);
const s2 = await snap();
await page.mouse.move(500, 300, { steps: 8 });
await page.waitForTimeout(500);
const s3 = await snap();
let idle = 0, cnt2 = 0;
const m2 = new Map(s3.nodes.map(n => [n.id, n]));
for (const n of s2.nodes) { const m = m2.get(n.id); if (m) { idle += Math.hypot(m.x - n.x, m.y - n.y); cnt2++; } }
idle = cnt2 ? idle / cnt2 : 0;
check('松手后不再继续转（无残留拖拽）', idle < 2.5, `${idle.toFixed(2)}px`);

const realErrors = errors.filter(e => !/favicon|Failed to load resource/i.test(e));
check('控制台零错误', realErrors.length === 0, realErrors.slice(0, 2).join(' | '));

console.log(`\n结果：${pass} PASS / ${fail} FAIL`);
await browser.close();
process.exit(fail ? 1 : 0);
