/**
 * v35 对拍脚本：JS 版指标 vs server/indicators.py 输出（逐点比较）
 * 用法：node tools/dp-ind-parity.js <tmpdir>
 */
const fs = require('fs');
const path = require('path');

const dir = process.argv[2];
const klines = JSON.parse(fs.readFileSync(path.join(dir, 'klines.json'), 'utf8'));
const py = JSON.parse(fs.readFileSync(path.join(dir, 'py.json'), 'utf8'));

// ---- 极简 DOM/浏览器桩，仅为让 chart.js 顶层代码可执行 ----
global.window = global;
global.document = {
  documentElement: {},
  getElementById: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
  createElement: () => ({ style: {}, classList: { add() {}, remove() {}, toggle() {} }, setAttribute() {}, appendChild() {} }),
  body: { classList: { add() {}, remove() {}, toggle() {} } }
};
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.getComputedStyle = () => ({ getPropertyValue: () => '' });
global.navigator = { userAgent: 'node' };
global.fetch = () => Promise.reject(new Error('offline'));
global.echarts = { graphic: { LinearGradient: function () {} }, init: () => null };

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'chart.js'), 'utf8');
(0, eval)(src);

const names = ['supertrend', 'ichimoku', 'donchian', 'keltner', 'vwap', 'adx', 'aroon', 'obv', 'mfi', 'atr', 'stoch', 'cci'];
let worst = 0;
let fail = 0;
names.forEach((name) => {
  const params = _dpParams(name);
  const got = _dpCompute(name, klines, params);
  const exp = py[name].panes;
  Object.keys(exp).forEach((pane) => {
    const a = got[pane] || [];
    const b = exp[pane] || [];
    if (a.length !== b.length) { console.log(`  ✗ ${name}.${pane} 长度 ${a.length} != ${b.length}`); fail++; return; }
    let maxDiff = 0, cmp = 0, nullMismatch = 0;
    for (let i = 0; i < b.length; i++) {
      const x = a[i], y = b[i];
      if (x == null && y == null) continue;
      if (x == null || y == null) { nullMismatch++; continue; }
      cmp++;
      const d = Math.abs(x - y);
      if (d > maxDiff) maxDiff = d;
    }
    if (maxDiff > worst) worst = maxDiff;
    const ok = maxDiff < 1e-9 && nullMismatch === 0;
    if (!ok) fail++;
    console.log(`  ${ok ? '✓' : '✗'} ${name}.${pane}: 可比 ${cmp} 点, 最大误差 ${maxDiff.toExponential(2)}${nullMismatch ? `, null 不一致 ${nullMismatch}` : ''}`);
  });
});
console.log(`\n总体最大误差 = ${worst.toExponential(3)}  (阈值 1e-9)  失败项 = ${fail}`);
process.exit(fail ? 1 : 0);
