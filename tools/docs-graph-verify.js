/**
 * 文档图谱验收 v2（正式版数据：30 节点 / 78 连线 / 7 分组）
 * 覆盖：渲染规模、kind 中文化、isCore(readme)、边出处（真实行号 vs 登记分组）、
 *      三节点 in/out 与 JSON 对账、chip(root) 布局不跳动、列表视图一致、降级路径。
 *
 * 用法：node tools/docs-graph-verify.js            # 主验证
 *      node tools/docs-graph-verify.js --error    # 仅验降级路径（配合临时改名 JSON）
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const EXE = '%LOCALAPPDATA%\\ms-playwright\\chromium_headless_shell-1223\\chrome-headless-shell-win64\\chrome-headless-shell.exe';
const BASE = 'http://127.0.0.1:8900';
const JSON_PATH = path.join(__dirname, '..', 'static', 'docs-graph.json');

const KIND_LABEL = { entry: '入口', readme: '索引', index: '索引', design: '设计', prd: 'PRD', spec: '规范', plan: '计划', ops: '运维', data: '数据字典', archive: '归档', diagram: '架构图', doc: '文档', missing: '缺失' };

(async () => {
  const errOnly = process.argv.includes('--error');
  const raw = fs.existsSync(JSON_PATH) ? JSON.parse(fs.readFileSync(JSON_PATH, 'utf8')) : null;
  const b = await chromium.launch({ executablePath: EXE });

  if (errOnly) {
    const p = await b.newPage({ viewport: { width: 1200, height: 900 } });
    const errs = [];
    const r404 = [];
    p.on('pageerror', (e) => errs.push('PAGE:' + String(e).slice(0, 160)));
    p.on('response', (r) => { if (r.status() === 404) r404.push(r.url()); });
    await p.goto(BASE + '/project-docs.html', { waitUntil: 'domcontentloaded' });
    await p.waitForTimeout(1000);
    await p.click('[data-dg-view="graph"]');
    await p.waitForTimeout(1800);
    const st = await p.evaluate(() => ({
      stateVisible: !document.getElementById('dg-state').hidden,
      text: document.getElementById('dg-state').innerText.replace(/\s+/g, ' ').slice(0, 220),
      hasRetry: !!document.getElementById('dg-retry'),
      canvasHidden: document.getElementById('dg-canvas-card').hidden
    }));
    await p.screenshot({ path: 'shots/dg-6-error.png' });
    console.log('DEGRADE', JSON.stringify(st));
    console.log('DEGRADE_404', JSON.stringify(r404.filter((u) => /docs-graph\.json/.test(u))));
    console.log('DEGRADE_UNCAUGHT', JSON.stringify(errs));
    await b.close();
    return;
  }

  if (!raw) { console.error('FATAL: docs-graph.json 不存在'); process.exit(1); }

  const jsonNodes = raw.nodes || [];
  const jsonLinks = raw.links || [];
  const outCount = {}, inCount = {};
  jsonNodes.forEach((n) => { outCount[n.id] = 0; inCount[n.id] = 0; });
  jsonLinks.forEach((l) => { outCount[l.source] = (outCount[l.source] || 0) + 1; inCount[l.target] = (inCount[l.target] || 0) + 1; });
  console.log('JSON_STATS', JSON.stringify(raw.stats));
  console.log('JSON_SPLIT link=', jsonLinks.filter((l) => l.type === 'link').length,
    'data_link=', jsonLinks.filter((l) => l.type === 'data_link').length,
    '| dupPairs=', (function () {
      const m = {}; let dup = 0;
      jsonLinks.forEach((l) => { const k = l.source + '>' + l.target; if (m[k]) dup++; m[k] = 1; });
      return dup;
    })());

  const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
  const errs = [];
  p.on('pageerror', (e) => errs.push('PAGE:' + String(e).slice(0, 180)));
  p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push('CON:' + m.text().slice(0, 180)); });

  await p.goto(BASE + '/project-docs.html', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1500);

  // ---------- 列表视图默认态 + 与「拦截图谱资源」的对照组比对 ----------
  const listState = await p.evaluate(() => ({
    view: document.body.classList.contains('docs-view-graph') ? 'graph' : 'list',
    graphHidden: document.getElementById('docs-graph-view').hidden,
    sections: document.querySelectorAll('#docs-main .docs-section').length,
    tocItems: document.querySelectorAll('#docs-toc-list li').length,
    h2: document.querySelectorAll('#docs-main h2').length,
    textLen: (document.getElementById('docs-main').innerText || '').length
  }));
  const pList = await b.newPage({ viewport: { width: 1440, height: 1000 } });
  await pList.route('**/docs-graph.js*', (r) => r.abort());
  await pList.route('**/docs-graph.css*', (r) => r.abort());
  await pList.goto(BASE + '/project-docs.html', { waitUntil: 'domcontentloaded' });
  await pList.waitForTimeout(1500);
  const baseline = await pList.evaluate(() => ({
    sections: document.querySelectorAll('#docs-main .docs-section').length,
    tocItems: document.querySelectorAll('#docs-toc-list li').length,
    h2: document.querySelectorAll('#docs-main h2').length,
    textLen: (document.getElementById('docs-main').innerText || '').length
  }));
  await pList.close();
  const parity = JSON.stringify({ sections: listState.sections, tocItems: listState.tocItems, h2: listState.h2, textLen: listState.textLen })
    === JSON.stringify(baseline);
  console.log('LIST_DEFAULT', JSON.stringify(listState));
  console.log('LIST_PARITY baseline=', JSON.stringify(baseline), 'SAME=', parity);

  // ---------- 图谱渲染规模 + 7 chips ----------
  await p.click('[data-dg-view="graph"]');
  await p.waitForFunction(() => window.DocsGraph && window.DocsGraph.state.settled, null, { timeout: 25000 }).catch(() => {});
  await p.waitForTimeout(1200);
  const g = await p.evaluate(() => {
    const s = window.DocsGraph.state;
    const op = s.chart.getOption();
    const nodes = op.series[0].data || [];
    const links = op.series[0].links || [];
    const chips = Array.from(document.querySelectorAll('#dg-chips .dg-chip')).map((c) => ({
      g: c.getAttribute('data-dg-group'), label: c.innerText.replace(/\s+/g, ' ').trim()
    }));
    const goldNodes = nodes.filter((d) => d.itemStyle && d.itemStyle.color === '#ecd9a6').map((d) => d.id);
    const readme = nodes.find((d) => d.id === 'docs/00-strategy/README.md');
    // 度数（in+out）
    const deg = {};
    Object.keys(s.byId).forEach((id) => { deg[id] = (s.degree[id] || 0); });
    const labeled = nodes.filter((d) => d.label && d.label.show).map((d) => ({ id: d.id, text: d.label.formatter, color: d.label.color }));
    const coreSet = nodes.filter((d) => d._n && (d._n.group === 'root' || ['entry', 'readme', 'index'].indexOf(d._n.kind) >= 0)).map((d) => d.id);
    const hubTop5 = Object.keys(deg).filter((id) => coreSet.indexOf(id) < 0)
      .sort((a, b) => (deg[b] - deg[a]) || (a < b ? -1 : 1)).slice(0, 5);
    return {
      nodes: nodes.length, links: links.length, layout: op.series[0].layout,
      labeledCount: labeled.length, labeledIds: labeled.map((x) => x.id), labeledTexts: labeled.map((x) => x.text),
      labeledColors: Array.from(new Set(labeled.map((x) => x.color))),
      coreSet, hubTop5, degOfTop: hubTop5.map((id) => id + '=' + deg[id]),
      chips: chips.length, chipList: chips.map((c) => c.g + '|' + c.label),
      firstChip: chips[0] && chips[0].g, secondChip: chips[1] && chips[1].g,
      goldCount: goldNodes.length, goldIds: goldNodes,
      readmeColor: readme ? readme.itemStyle.color : null,
      dashed: links.filter((l) => l.lineStyle && l.lineStyle.type === 'dashed').length,
      solid: links.filter((l) => l.lineStyle && l.lineStyle.type === 'solid').length
    };
  });
  console.log('GRAPH', JSON.stringify(g));
  await p.screenshot({ path: 'shots/dg-2-graph.png' });

  // ---------- kind 中文化（逐个真实 kind 检查面板「类型」字段） ----------
  const kindPanel = await p.evaluate(() => {
    const s = window.DocsGraph.state;
    const out = {};
    const seen = {};
    Object.keys(s.byId).forEach((id) => {
      const n = s.byId[id];
      if (seen[n.kind]) return;
      seen[n.kind] = 1;
      window.DocsGraph.selectNode(id);
      const panel = document.getElementById('dg-panel');
      const ks = Array.from(panel.querySelectorAll('.dg-kv .k'));
      const vs = Array.from(panel.querySelectorAll('.dg-kv .v'));
      let val = '';
      ks.forEach((k, i) => { if (k.textContent.trim() === '类型') val = vs[i].textContent.trim(); });
      out[n.kind] = { id, shown: val };
    });
    window.DocsGraph.closePanel(true);
    return out;
  });
  let kindOk = true;
  const kindDetail = {};
  Object.keys(kindPanel).forEach((k) => {
    const got = kindPanel[k].shown;
    const want = KIND_LABEL[k] || '文档';
    const ok = got === want;
    if (!ok) kindOk = false;
    kindDetail[k] = got + (ok ? ' ✓' : (' ✗ 期望 ' + want));
  });
  console.log('KIND_PANEL', JSON.stringify(kindDetail), 'ALL_OK=', kindOk);

  // ---------- 边出处：link 行号 vs data_link 登记分组 ----------
  const edgeCheck = await p.evaluate(() => {
    const s = window.DocsGraph.state;
    const L = s.data.links || [];
    const linkEdge = L.filter((l) => l.type === 'link' && l.line != null)[0];
    const dataEdge = L.filter((l) => l.type === 'data_link')[0];
    function panelOf(nodeId) {
      window.DocsGraph.selectNode(nodeId);
      return document.getElementById('dg-panel');
    }
    // 逐条取「该目标 + 该边类型」的条目文本，避免被同一面板里其它边污染
    function itemText(panel, targetId, type) {
      const items = Array.from(panel.querySelectorAll('[data-dg-rel]'));
      const hit = items.filter((el) => el.getAttribute('data-dg-rel') === targetId && el.getAttribute('data-dg-edge') === type);
      return hit.length ? hit[0].innerText : null;
    }
    const linkPanel = panelOf(linkEdge.source);
    const linkItem = itemText(linkPanel, linkEdge.target, 'link');
    const dataPanel = panelOf(dataEdge.source);
    const dataItem = itemText(dataPanel, dataEdge.target, 'data_link');
    const linkText = linkPanel.innerText;
    const dataText = dataPanel.innerText;
    window.DocsGraph.closePanel(true);
    // 只截取「该边」对应的条目文本更严谨：抓带 data-dg-edge 的条目
    return {
      linkEdge: { source: linkEdge.source, target: linkEdge.target, line: linkEdge.line },
      dataEdge: { source: dataEdge.source, target: dataEdge.target, group: dataEdge.group, line: dataEdge.line },
      linkItemText: linkItem,
      linkHasLine: !!linkItem && linkItem.indexOf('引用位置：第 ' + linkEdge.line + ' 行') >= 0,
      dataItemText: dataItem,
      dataHasGroup: (dataItem !== null && dataEdge.group) ? (dataItem.indexOf('登记分组：' + dataEdge.group) >= 0) : null,
      dataHasLinePhrase: dataItem !== null ? /引用位置：第/.test(dataItem) : null,
      badZero: /第\s*0\s*行/.test(linkText + dataText),
      badNull: /第\s*(null|undefined|NaN)\s*行/.test(linkText + dataText),
      nullLineCount: (L.filter((l) => l.line == null).length)
    };
  });
  console.log('EDGE_ORIGIN', JSON.stringify(edgeCheck));

  // ---------- 三节点 in/out 与 JSON 对账 ----------
  const targets = ['README.md', 'docs/00-strategy/MASTER-PLAN-2026Q4.md', 'docs/40-ops/API-REFERENCE.md'];
  const domCounts = await p.evaluate((ids) => {
    const out = {};
    ids.forEach((id) => {
      if (!window.DocsGraph.state.byId[id]) { out[id] = null; return; }
      window.DocsGraph.selectNode(id);
      const panel = document.getElementById('dg-panel');
      const hds = Array.from(panel.querySelectorAll('.dg-rel-hd'));
      const num = (t) => { const m = String(t).match(/(\d+)/); return m ? Number(m[1]) : null; };
      const insHd = hds.find((h) => h.textContent.indexOf('被引用') >= 0);
      const outsHd = hds.find((h) => h.textContent.indexOf('引用了') >= 0);
      const ks = Array.from(panel.querySelectorAll('.dg-kv .k'));
      const vs = Array.from(panel.querySelectorAll('.dg-kv .v'));
      let kvIn = null, kvOut = null;
      ks.forEach((k, i) => {
        const label = k.textContent.trim();
        const val = Number(String(vs[i].textContent).replace(/[^0-9]/g, ''));
        if (label === '被引用') kvIn = val;
        if (label === '引用了') kvOut = val;
      });
      const lists = panel.querySelectorAll('.dg-rel-list');
      out[id] = {
        hdIn: insHd ? num(insHd.textContent) : null,
        hdOut: outsHd ? num(outsHd.textContent) : null,
        kvIn, kvOut,
        listIn: lists[0] ? lists[0].querySelectorAll('[data-dg-rel]').length : null,
        listOut: lists[1] ? lists[1].querySelectorAll('[data-dg-rel]').length : null
      };
    });
    window.DocsGraph.closePanel(true);
    return out;
  }, targets);
  const reconcile = {};
  targets.forEach((id) => {
    const d = domCounts[id];
    const jIn = inCount[id], jOut = outCount[id];
    reconcile[id] = d ? {
      jsonIn: jIn, jsonOut: jOut, dom: d,
      ok: d.kvIn === jIn && d.kvOut === jOut && d.listIn === jIn && d.listOut === jOut && d.hdIn === jIn && d.hdOut === jOut
    } : { missing: true };
  });
  console.log('RECONCILE', JSON.stringify(reconcile));

  // ---------- v3 标签分层断言 ----------
  const labelCheck = await p.evaluate(() => {
    const s = window.DocsGraph.state;
    const op = s.chart.getOption();
    const nodes = op.series[0].data;
    const shownIds = nodes.filter((d) => d.label && d.label.show).map((d) => d.id);
    const always = Object.keys(s.labelAlways);
    const core = Object.keys(s.labelCore);
    const hub = Object.keys(s.labelHub);
    const nonAlwaysShown = shownIds.filter((id) => always.indexOf(id) < 0);
    const alwaysHidden = always.filter((id) => shownIds.indexOf(id) < 0);
    return {
      narrow: s.narrow, shownCount: shownIds.length,
      alwaysCount: always.length, coreCount: core.length, hubCount: hub.length,
      core, hub, nonAlwaysShown, alwaysHidden,
      allLabelsHiddenForNonAlways: nodes.filter((d) => d.label && !d.label.show).length,
      labelPos: nodes.filter((d) => d.label && d.label.show)[0] ? nodes.filter((d) => d.label && d.label.show)[0].label.position : null
    };
  });
  console.log('LABELS', JSON.stringify(labelCheck));
  await p.screenshot({ path: 'shots/dg-7-labels.png' });

  // ---------- chip 筛选（含 root）布局不跳动 ----------
  const chipRuns = [];
  for (const gk of ['root', '10-architecture']) {
    const before = await p.evaluate(() => window.DocsGraph.positions());
    const clicked = await p.evaluate((gk2) => {
      const c = document.querySelector('#dg-chips .dg-chip[data-dg-group="' + gk2 + '"]');
      if (!c) return null;
      c.click();
      return { label: c.innerText.replace(/\s+/g, ' ').trim() };
    }, gk);
    await p.waitForTimeout(700);
    const after = await p.evaluate(() => window.DocsGraph.positions());
    const dim = await p.evaluate(() => {
      const op = window.DocsGraph.state.chart.getOption();
      return {
        lit: op.series[0].data.filter((d) => (d.itemStyle.opacity || 1) > 0.5).length,
        total: op.series[0].data.length,
        group: window.DocsGraph.state.group
      };
    });
    let maxMove = 0, moved = 0;
    Object.keys(before).forEach((id) => {
      if (!after[id]) return;
      const dd = Math.hypot(after[id][0] - before[id][0], after[id][1] - before[id][1]);
      if (dd > 0.01) moved++;
      if (dd > maxMove) maxMove = dd;
    });
    chipRuns.push({ group: gk, label: clicked && clicked.label, posBefore: Object.keys(before).length, posAfter: Object.keys(after).length, maxMove: Number(maxMove.toFixed(4)), moved, dim });
    await p.evaluate(() => { const c = document.querySelector('#dg-chips .dg-chip[data-dg-group=""]'); if (c) c.click(); });
    await p.waitForTimeout(500);
  }
  console.log('CHIP_RUNS', JSON.stringify(chipRuns));
  await p.screenshot({ path: 'shots/dg-4-chip.png' });

  // ---------- 静置 10s 不重绘 ----------
  const r0 = await p.evaluate(() => window.DocsGraph.stats().rendered);
  await p.waitForTimeout(10000);
  const r1 = await p.evaluate(() => window.DocsGraph.stats().rendered);
  console.log('IDLE_REDRAW', r0, '->', r1, 'delta=', r1 - r0);

  // ---------- 窄屏 ----------
  await p.setViewportSize({ width: 800, height: 900 });
  await p.waitForTimeout(800);
  await p.evaluate(() => { const s = window.DocsGraph.state; window.DocsGraph.selectNode(Object.keys(s.byId)[0]); });
  await p.waitForTimeout(600);
  await p.waitForTimeout(600);
  const narrow = await p.evaluate(() => {
    const cs = getComputedStyle(document.getElementById('dg-panel'));
    const op = window.DocsGraph.state.chart.getOption();
    const shown = (op.series[0].data || []).filter((d) => d.label && d.label.show).length;
    return {
      position: cs.position, bottom: cs.bottom, width: cs.width,
      roam: op.series[0].roam,
      narrowFlag: window.DocsGraph.state.narrow,
      labelsAlwaysOn: shown
    };
  });
  console.log('NARROW', JSON.stringify(narrow));
  await p.screenshot({ path: 'shots/dg-5-narrow.png' });

  console.log('ERRORS', JSON.stringify(errs.slice(0, 6)));
  await b.close();

  const pass = parity && g.nodes === 30 && g.links === 78 && g.chips === 8
    && g.firstChip === '' && g.secondChip === 'root'
    && kindOk && edgeCheck.linkHasLine && edgeCheck.dataHasGroup !== false
    && !edgeCheck.badZero && !edgeCheck.badNull
    && Object.keys(reconcile).every((k) => reconcile[k].ok)
    && chipRuns.every((c) => c.maxMove === 0 && c.posBefore > 0)
    && labelCheck.shownCount === labelCheck.alwaysCount
    && labelCheck.nonAlwaysShown.length === 0 && labelCheck.alwaysHidden.length === 0
    && labelCheck.coreCount === 3 && labelCheck.hubCount === 5
    && narrow.narrowFlag === true && narrow.labelsAlwaysOn === 0
    && (r1 - r0) === 0;
  console.log('VERDICT', pass ? 'PASS' : 'FAIL');
  process.exit(pass ? 0 : 2);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
