/**
 * 临时探针：第 4 项「节点标签分层」的补充实测（不属于正式验收脚本）。
 *
 * 关键点：不读配置，直接读 zrender 显示列表里真实参与绘制的文字（tspan），
 *        这样才能区分「label.show 配了 true」与「画布上真的有这段字」。
 *
 * 覆盖：
 *   1) 8 个 chip（含「全部」）筛选后的 maxMove（要求恒 0）+ 标签数与淡出节点残留
 *   2) 宽带基线：8 个常显配置 vs 画布真实文字数（hideOverlap 可能吞 1 个，如实报出）
 *   3) 窄屏 800px：分层集合仍为 8，但常显归零
 *   4) 窄屏 hover 核心节点：画布出现该标签；移开后消失（证明 hover-only 而非被改成常显）
 *
 * 用法：node tools/dg-label-probe.js
 */
const { chromium } = require('playwright');

const EXE = '%LOCALAPPDATA%\\ms-playwright\\chromium_headless_shell-1223\\chrome-headless-shell-win64\\chrome-headless-shell.exe';
const BASE = 'http://127.0.0.1:8900';
const GROUPS = ['', 'root', '00-strategy', '10-architecture', '20-prd', '30-data', '40-ops', '90-archive'];
const CORE_ID = 'docs/00-strategy/README.md';

/** 从 zrender 显示列表取真实绘制的文字（ZRText 的外层是 tspan，故取 style.text 或子元素） */
const RENDER_TEXT_FN = `(() => {
  const chart = window.DocsGraph.state.chart;
  const list = chart.getZr().storage.getDisplayList(true);
  const out = [];
  list.forEach(function (el) {
    if (!el) return;
    var t = (el.style && el.style.text) || null;
    if (typeof t !== 'string' && el.children && el.children.length) {
      el.children.forEach(function (c) { if (c && c.style && typeof c.style.text === 'string') out.push(c.style.text); });
      return;
    }
    if (typeof t === 'string' && t) out.push(t);
  });
  return out.filter(function (s) { return s && s.length > 1; });
})()`;

/** 数据坐标 → 画布像素（加 canvas 在页面中的偏移），用于真实鼠标 hover */
const PIXEL_FN = `(() => {
  const chart = window.DocsGraph.state.chart;
  const id = %ID%;
  const data = chart.getModel().getSeriesByIndex(0).getData();
  var idx = -1;
  for (var i = 0; i < data.count(); i++) { if (data.getName(i) === id || (data.getRawDataItem(i) || {}).id === id) { idx = i; break; } }
  if (idx < 0) return null;
  var lay = data.getItemLayout(idx);
  var px = chart.convertToPixel({ seriesIndex: 0 }, [lay[0], lay[1]]);
  var rect = chart.getDom().getBoundingClientRect();
  return { dataIndex: idx, x: Math.round(rect.left + px[0]), y: Math.round(rect.top + px[1]) };
})()`;

(async () => {
  const b = await chromium.launch({ executablePath: EXE });
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e).slice(0, 200)));

  await p.goto(BASE + '/project-docs.html', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(800);
  await p.click('[data-dg-view="graph"]');
  await p.waitForTimeout(2200);

  // ---------- 1) 8 个 chip 全跑 ----------
  const runs = [];
  for (const gk of GROUPS) {
    const before = await p.evaluate(() => window.DocsGraph.positions());
    const info = await p.evaluate((g) => {
      const c = document.querySelector('#dg-chips .dg-chip[data-dg-group="' + g + '"]');
      if (!c) return null;
      c.click();
      return { label: c.innerText.replace(/\s+/g, ' ').trim() };
    }, gk);
    await p.waitForTimeout(650);
    const after = await p.evaluate(() => window.DocsGraph.positions());
    const vis = await p.evaluate(() => {
      const data = window.DocsGraph.state.chart.getOption().series[0].data || [];
      return {
        labelShown: data.filter((d) => d.label && d.label.show).length,
        labelOnDim: data.filter((d) => d.label && d.label.show && (d.itemStyle.opacity || 1) <= 0.5).length,
      };
    });
    let maxMove = 0;
    let moved = 0;
    Object.keys(before).forEach((id) => {
      if (!after[id]) return;
      const dd = Math.hypot(after[id][0] - before[id][0], after[id][1] - before[id][1]);
      if (dd > 0.01) moved++;
      if (dd > maxMove) maxMove = dd;
    });
    runs.push({
      group: gk || '(全部)', label: info && info.label,
      posBefore: Object.keys(before).length, posAfter: Object.keys(after).length,
      maxMove: Number(maxMove.toFixed(4)), moved,
      labelShown: vis.labelShown, labelOnDim: vis.labelOnDim,
    });
    await p.evaluate(() => {
      const c = document.querySelector('#dg-chips .dg-chip[data-dg-group=""]');
      if (c) c.click();
    });
    await p.waitForTimeout(400);
  }
  console.log('CHIP_ALL8', JSON.stringify(runs));

  // ---------- 2) 宽带基线：1440 与 1920 ----------
  const snap = async () => {
    const cfg = await p.evaluate(() => {
      const data = window.DocsGraph.state.chart.getOption().series[0].data || [];
      const on = data.filter((d) => d.label && d.label.show);
      return { alwaysOn: on.length, alwaysMap: Object.keys(window.DocsGraph.state.labelAlways).length, textsCfg: on.map((d) => (d.label.formatter && typeof d.label.formatter === 'string' ? d.label.formatter : d.label.formatter && d.label.formatter.text) || d.name) };
    });
    const painted = await p.evaluate(RENDER_TEXT_FN);
    return { cfg, paintedCount: painted.length, painted };
  };
  const wide1440 = await snap();
  console.log('WIDE_1440', JSON.stringify(wide1440));
  await p.setViewportSize({ width: 1920, height: 1080 });
  await p.waitForTimeout(1200);
  const wide1920 = await snap();
  console.log('WIDE_1920', JSON.stringify({ paintedCount: wide1920.paintedCount, alwaysOn: wide1920.cfg.alwaysOn, painted: wide1920.painted }));
  await p.setViewportSize({ width: 1440, height: 900 });
  await p.waitForTimeout(1000);

  // ---------- 3) 窄屏 800px：常显归零 ----------
  await p.setViewportSize({ width: 800, height: 900 });
  await p.waitForTimeout(1100);
  const narrowBefore = await p.evaluate(() => {
    const data = window.DocsGraph.state.chart.getOption().series[0].data || [];
    return {
      narrow: window.DocsGraph.state.narrow,
      alwaysOn: data.filter((d) => d.label && d.label.show).length,
      alwaysMap: Object.keys(window.DocsGraph.state.labelAlways).length,
      total: data.length,
    };
  });
  const narrowPainted = await p.evaluate(RENDER_TEXT_FN);
  console.log('NARROW_800', JSON.stringify({ state: narrowBefore, paintedCount: narrowPainted.length, painted: narrowPainted.slice(0, 10) }));

  // ---------- 4) 窄屏 hover 核心节点 ----------
  const px = await p.evaluate(PIXEL_FN.replace('%ID%', JSON.stringify(CORE_ID)));
  await p.mouse.move(px.x, px.y);
  await p.waitForTimeout(800);
  const hoverPainted = await p.evaluate(RENDER_TEXT_FN);
  const hit = hoverPainted.some((t) => /文档地图|Document Ma/.test(t));
  console.log('NARROW_HOVER', JSON.stringify({ px, paintedCount: hoverPainted.length, painted: hoverPainted, targetPresent: hit }));
  await p.screenshot({ path: 'shots/dg-8-narrow-label.png' });

  await p.mouse.move(3, 3);
  await p.waitForTimeout(700);
  const leavePainted = await p.evaluate(RENDER_TEXT_FN);
  const gone = !leavePainted.some((t) => /文档地图|Document Ma/.test(t));
  console.log('NARROW_LEAVE', JSON.stringify({ paintedCount: leavePainted.length, targetGone: gone }));

  console.log('ERRORS', JSON.stringify(errs.slice(0, 5)));
  const ok = runs.every((r) => r.maxMove === 0 && r.posBefore > 0 && r.posAfter > 0 && r.labelOnDim === 0)
    && wide1440.cfg.alwaysOn === 8 && wide1440.cfg.alwaysMap === 8
    && narrowBefore.narrow === true && narrowBefore.alwaysOn === 0 && narrowBefore.alwaysMap === 8
    && narrowPainted.length === 0
    && hit === true && gone === true;
  console.log('PROBE_VERDICT', ok ? 'PASS' : 'FAIL');
  await b.close();
  process.exit(ok ? 0 : 2);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
