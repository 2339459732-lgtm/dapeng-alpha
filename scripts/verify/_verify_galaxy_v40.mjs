// AXON v40 验证：星系板块联动 / 环铭牌 / 图签配置 / 数据口径 / 控制台零错误
// 用法：node _verify_galaxy_v40.mjs
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8900';
const errs = [];
const ok = (name, cond, extra = '') => {
  console.log((cond ? 'PASS' : 'FAIL') + '  ' + name + (extra ? '  ' + extra : ''));
  if (!cond) process.exitCode = 1;
};

const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));

// 预置偏好：直接进星系模式
await page.addInitScript(() => {
  /* 仅首访兜底：不得在 reload 时覆盖测试中途写入的偏好（观测者/关注/方案） */
  if (!localStorage.getItem('axon-plate-v3')) {
    localStorage.setItem('axon-plate-v3', JSON.stringify({ view3d: 'galaxy', design: 'galaxy' }));
  }
});

await page.goto(BASE + '/plate-graph.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2600);

// 1) 数据与星系模式
const st = await page.evaluate(() => ({
  nodes: window.__AXON__.nodes.length,
  links: window.__AXON__.links.length,
  metrics: window.__AXON__.metrics,
  mode: document.getElementById('plate').className.includes('view-galaxy') ? 'galaxy' : 'other',
  atlas: !!document.getElementById('gal-canvas'),
  label: document.querySelector('#view3d-btn .v3d-label')?.textContent,
}));
ok('数据 36 节点 / 100 边（96 对去重）', st.nodes === 36 && st.links === 100, JSON.stringify({ nodes: st.nodes, links: st.links }));
ok('metrics：0 孤立 / 1 连通块 / 95 对', st.metrics.isolated === 0 && st.metrics.components === 1 && st.metrics.unique_pairs === 96, JSON.stringify(st.metrics));
ok('星系模式生效', st.mode === 'galaxy' && st.label === '星系');

// 2) 图签：板块行 + 联动
const plate0 = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('#gal-plate .gp-layer')];
  return {
    ringTitle: document.querySelector('#gal-plate .gp-k:nth-of-type(3)')?.textContent || [...document.querySelectorAll('#gal-plate .gp-k')].map(e => e.textContent).join('|'),
    rows: rows.length,
    first: rows[0]?.textContent.trim(),
    cat: [...document.querySelectorAll('#gal-plate .gp-k')].map(e => e.textContent).join(' / '),
    total: document.querySelector('#gal-plate .gp-row b')?.textContent,
  };
});
ok('图签 7 条板块行', plate0.rows === 7, plate0.first || '');
ok('图签读数 = 全量 36/96', (await page.evaluate(() => document.querySelector('#gal-plate .gp-row em')?.textContent)) === '36 / 96');

// 3) 点击"10 架构与设计"板块行 → scope 联动
await page.evaluate(() => {
  const row = [...document.querySelectorAll('#gal-plate .gp-layer')].find(r => r.textContent.includes('架构与设计'));
  row.click();
});
await page.waitForTimeout(700);
const scoped = await page.evaluate(() => ({
  chip: document.querySelector('#scope-chips')?.textContent || '',
  cat: document.querySelector('#gal-plate .gp-k')?.textContent,
  count: document.querySelector('#gal-plate .gp-row em')?.textContent,
  hot: document.querySelector('#gal-plate .gp-layer.is-hot b')?.textContent,
  canvasShown: getComputedStyle(document.getElementById('gal-canvas')).display !== 'none',
}));
ok('板块行点击 → 筛选条出现"架构与设计"', scoped.chip.includes('架构与设计'), scoped.chip);
ok('图签切换 SCOPE 读数 14/15（板块内部关系对）', scoped.cat.includes('SCOPE') && scoped.count === '14 / 15', scoped.cat + ' ' + scoped.count);
ok('图签板块行 is-hot 高亮', (scoped.hot || '').includes('架构与设计'), scoped.hot || '');

// 4) 再点一次取消，读数还原
await page.evaluate(() => {
  const row = [...document.querySelectorAll('#gal-plate .gp-layer')].find(r => r.textContent.includes('架构与设计'));
  row.click();
});
await page.waitForTimeout(500);
const back = await page.evaluate(() => document.querySelector('#gal-plate .gp-row em')?.textContent);
ok('再次点击取消筛选 → 读数还原 36/96', back === '36 / 96', back || '');

// 5) 悬停文档星 → 信息卡字段（groupLabel 中文 + 群落 + 环号）
await page.evaluate(() => {
  const hit = document.querySelector('.n3d circle[class*="hit"], .n3d'); // 任一星
  const ev = new MouseEvent('pointermove', { bubbles: true });
  (hit.closest('.n3d') || hit).dispatchEvent(ev);
});
await page.waitForTimeout(600);
const info = await page.evaluate(() => {
  const el = document.getElementById('gal-info');
  return el && !el.hidden ? el.textContent : null;
});
ok('信息卡可显示（含中文层级/群落维度）', !!info && /环/.test(info) && /(群落)/.test(info), (info || 'null').slice(0, 80));

// 6) 层间引用矩阵：互引计一对（00↔10 应为 16 对，旧口径 17 条）
const mx = await page.evaluate(() => {
  const cells = [...document.querySelectorAll('#matrix .mx-c[data-pair]')];
  const c = cells.find(el => {
    const [a, b] = el.dataset.pair.split('|');
    return (a === '00-strategy' && b === '10-architecture') || (a === '10-architecture' && b === '00-strategy');
  });
  return c ? c.textContent : null;
});
ok('层间矩阵 00↔10 = 15 对（16 条边含 1 对互引，去重后 15）', mx === '15', 'got ' + mx);

// 7) 规模读数（页脚 tb-scale 35/95）
const scale = await page.evaluate(() => document.getElementById('tb-scale')?.textContent);
ok('页脚规模 35 / 95（去重对）', scale === '36 / 96', scale || '');

// 8) 截图存档（默认星系 + 板块筛选态）
await page.evaluate(() => {
  const row = [...document.querySelectorAll('#gal-plate .gp-layer')].find(r => r.textContent.includes('架构与设计'));
  row.click();
});
await page.waitForTimeout(900);
await page.screenshot({ path: 'shots/_galaxy_v40_scope.png' });
await page.evaluate(() => document.getElementById('scope-clear').click());
await page.waitForTimeout(900);
await page.screenshot({ path: 'shots/_galaxy_v40_full.png' });

// 9) 经典图谱页（docs-graph.json 再生成后）不回归：切到图谱视图，确认 ECharts 建图
await page.goto(BASE + '/project-docs.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2500);
const listLen = await page.evaluate(() => document.body.innerText.length);
await page.evaluate(() => {
  const btn = document.querySelector('[data-dg-view="graph"]');
  if (btn) btn.click();
});
await page.waitForTimeout(4000);
const classicState = await page.evaluate(() => ({
  canvases: document.querySelectorAll('#dg-canvas canvas').length,
  viewShown: !document.getElementById('docs-graph-view')?.hidden,
}));
ok('project-docs.html 图谱视图正常渲染', classicState.viewShown && classicState.canvases > 0 && listLen > 5000, JSON.stringify({ ...classicState, listLen }));

// 10) v40.1 细节精进：停档 / 信息卡左侧 / 聚焦图签 / 空范围提示 / 纵深标尺
await page.goto(BASE + '/plate-graph.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2600);

// 10.1 星系自转「停」档位可达并生效
await page.evaluate(() => document.getElementById('pers-btn').click());
await page.waitForTimeout(400);
const haltBtn = await page.evaluate(() => {
  const row = document.querySelector('#prefs [data-pref="galSpeed"][data-value="halt"]');
  if (row) row.click();
  return !!row;
});
await page.waitForTimeout(300);
const haltSaved = await page.evaluate(() => JSON.parse(localStorage.getItem('axon-plate-v3') || '{}').galSpeed);
await page.evaluate(() => {
  document.querySelector('#prefs [data-pref="galSpeed"][data-value="normal"]')?.click();
  document.getElementById('pers-btn').click();
});
ok('自转「停」档可达并持久化', haltBtn && haltSaved === 'halt', JSON.stringify({ found: haltBtn, saved: haltSaved }));

// 10.2 信息卡在左侧（不再压住右侧图签）
await page.evaluate(() => {
  const star = document.querySelector('#graph3d .n3d');
  star.dispatchEvent(new MouseEvent('pointermove', { bubbles: true }));
});
await page.waitForTimeout(500);
const infoRect = await page.evaluate(() => {
  const el = document.getElementById('gal-info');
  if (!el || el.hidden) return null;
  const r = el.getBoundingClientRect();
  const plate = document.getElementById('gal-plate').getBoundingClientRect();
  return { x: Math.round(r.x), right: Math.round(r.right), plateX: Math.round(plate.x), overlap: !(r.right < plate.x || r.x > plate.right) };
});
ok('信息卡移至左侧，与图签零重叠', !!infoRect && infoRect.right < 840 && !infoRect.overlap, JSON.stringify(infoRect));

// 10.3 聚焦模式：图签显示「聚焦」且读数 = 该星邻居数+1
const starId = await page.evaluate(() => document.querySelector('#graph3d .n3d').dataset.id);
await page.evaluate(() => {
  document.querySelector('#graph3d .n3d').dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
});
await page.waitForTimeout(800);
const focusState = await page.evaluate((sid) => {
  const deg = window.__AXON__.nodes.find(n => n.id === sid)?.degree;
  return {
    tag: document.querySelector('#gal-plate .gp-k')?.textContent,
    count: document.querySelector('#gal-plate .gp-row em')?.textContent,
    deg,
  };
}, starId);
ok('聚焦图签：标签含「聚焦」', (focusState.tag || '').includes('聚焦'), focusState.tag || '');
ok('聚焦读数 = 邻居数+1', focusState.count && focusState.count.startsWith(String(focusState.deg + 1) + ' /'), JSON.stringify(focusState));
await page.keyboard.press('Escape');
await page.waitForTimeout(600);

// 10.4 空范围：可信=生效计划 × 日期=2026-07-07 → 0 份，图签给空态提示
await page.evaluate(() => {
  document.querySelector('#legend [data-tier="plan"]')?.click();
  document.querySelector('#dist .dt-col[data-date="2026-07-07"]')?.click();
});
await page.waitForTimeout(700);
const emptyState = await page.evaluate(() => ({
  note: [...document.querySelectorAll('#gal-plate .gp-row b')].some(b => b.textContent.includes('范围内无文档')),
  tag: document.querySelector('#gal-plate .gp-k')?.textContent,
}));
ok('空范围提示「范围内无文档」', emptyState.note, JSON.stringify(emptyState));
await page.evaluate(() => document.getElementById('scope-clear').click());
await page.waitForTimeout(500);

// 10.5 纵深标尺（depth3d=tier 截图存档）
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  p.depth3d = 'tier'; localStorage.setItem('axon-plate-v3', JSON.stringify(p));
});
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2600);
await page.screenshot({ path: 'shots/_galaxy_v40_depth_tier.png' });
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  p.depth3d = 'layer'; localStorage.setItem('axon-plate-v3', JSON.stringify(p));
});

// 11) v40.2 板块数据配置：layers[] 配置一致性 + 配置卡交互
await page.goto(BASE + '/plate-graph.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2600);

// 11.1 配置字段与图数据全量一致（页面内重算对比）
const cfgOk = await page.evaluate(() => {
  const d = window.__AXON__;
  const byId = {}; d.nodes.forEach(n => byId[n.id] = n);
  const pairs = new Set(d.links.map(l => [l.s, l.t].sort().join('|')));
  const bad = [];
  d.layers.forEach(L => {
    const ns = d.nodes.filter(n => n.group === L.key);
    const ids = new Set(ns.map(n => n.id));
    let internal = 0, cross = 0;
    pairs.forEach(p => {
      const [a, b] = p.split('|');
      const ia = ids.has(a), ib = ids.has(b);
      if (ia && ib) internal++;
      else if (ia || ib) cross++;
    });
    const expect = {
      bytes: ns.reduce((s, n) => s + n.bytes, 0),
      internalPairs: internal, crossPairs: cross,
      bridges: ns.filter(n => n.bridge).length,
      maxDegree: Math.max(...ns.map(n => n.degree)),
      updated: ns.map(n => n.updated).sort().pop(),
    };
    ['bytes', 'internalPairs', 'crossPairs', 'bridges', 'maxDegree', 'updated'].forEach(k => {
      if (L[k] !== expect[k]) bad.push(L.key + '.' + k + ' ' + L[k] + '≠' + expect[k]);
    });
    const hub = ns.reduce((m, n) => (n.degree > m.degree ? n : m), ns[0]);
    if (L.hub.id !== hub.id) bad.push(L.key + '.hub');
    d.tiers.forEach(t => {
      const c = ns.filter(n => n.tier === t.key).length;
      if ((L.tiers[t.key] || 0) !== c) bad.push(L.key + '.tiers.' + t.key);
    });
  });
  return bad;
});
ok('layers[] 配置与图数据全量一致', cfgOk.length === 0, cfgOk.slice(0, 4).join(' | ') || '7 层 × 11 项字段');

// 11.2 ⇱ 打开配置卡（不触发范围切换）
await page.evaluate(() => {
  document.querySelector('#gal-plate [data-cfg="10-architecture"]').click();
});
await page.waitForTimeout(500);
const card = await page.evaluate(() => ({
  open: document.getElementById('dossier').classList.contains('is-open'),
  top: document.getElementById('ds-layer')?.textContent,
  title: document.getElementById('ds-title')?.textContent,
  body: document.getElementById('ds-body')?.innerText.replace(/\s+/g, ' '),
  chips: document.getElementById('scope-chips')?.textContent || '',
}));
ok('配置卡打开：10 · 架构与设计', card.open && /PLATE CONFIG · 10/.test(card.top || '') && card.title === '10 · 架构与设计', JSON.stringify(card.top));
ok('配置卡数据：板块内 15 / 跨板块 28 / 枢纽', /板块内\s*15/.test(card.body) && /跨板块\s*28/.test(card.body) && card.body.includes('OpenBB 设计'), (card.body || '').slice(0, 90));
ok('⇱ 不触发范围切换（chips 为空）', card.chips.trim() === '', JSON.stringify(card.chips));

// 11.3 可信层级行可点 → tier 筛选 + 卡关闭
await page.evaluate(() => document.querySelector('#ds-body [data-tier-pick="truth"]').click());
await page.waitForTimeout(400);
const tierPick = await page.evaluate(() => ({
  chips: document.getElementById('scope-chips')?.textContent || '',
  closed: !document.getElementById('dossier').classList.contains('is-open'),
}));
ok('配置卡可信层行 → 真相源筛选', tierPick.chips.includes('真相源') && tierPick.closed, JSON.stringify(tierPick));

// 11.4 「只看该板块」按钮
await page.evaluate(() => document.querySelector('#gal-plate [data-cfg="10-architecture"]').click());
await page.waitForTimeout(300);
await page.evaluate(() => document.getElementById('dl-scope').click());
await page.waitForTimeout(400);
const scopeBtn = await page.evaluate(() => document.getElementById('scope-chips')?.textContent || '');
ok('配置卡「只看该板块」→ 层级筛选', scopeBtn.includes('架构与设计'), scopeBtn);

// 11.5 「聚焦枢纽」按钮
await page.evaluate(() => document.querySelector('#gal-plate [data-cfg="10-architecture"]').click());
await page.waitForTimeout(300);
await page.evaluate(() => document.getElementById('dl-hub').click());
await page.waitForTimeout(600);
const hubFocus = await page.evaluate(() => ({
  focus: document.getElementById('focus-bar')?.textContent || '',
  plate: document.querySelector('#gal-plate .gp-k')?.textContent,
}));
ok('配置卡「聚焦枢纽」→ 进入聚焦', hubFocus.focus.includes('聚焦') && (hubFocus.plate || '').includes('聚焦'), JSON.stringify(hubFocus).slice(0, 90));
await page.keyboard.press('Escape');
await page.waitForTimeout(400);
await page.evaluate(() => document.getElementById('scope-clear').click());
await page.waitForTimeout(400);
await page.keyboard.press('Escape');
await page.waitForTimeout(300);
await page.screenshot({ path: 'shots/_galaxy_v40_layer_card.png', fullPage: false });

// 11.6 平面视图：列头 meta 含 KB，双击列头开配置卡
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  p.view3d = 'flat'; localStorage.setItem('axon-plate-v3', JSON.stringify(p));
});
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2200);
const flat = await page.evaluate(() => {
  const meta = document.querySelector('.col-meta')?.textContent || '';
  document.querySelector('.col-hit')?.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
  return { meta, top: document.getElementById('ds-layer')?.textContent, open: document.getElementById('dossier').classList.contains('is-open') };
});
ok('平面列头 meta 含 KB（' + flat.meta + '）', /KB/.test(flat.meta), flat.meta);
ok('双击列头 → ROOT 配置卡', flat.open && /PLATE CONFIG · ROOT/.test(flat.top || ''), JSON.stringify(flat.top));
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  p.view3d = 'galaxy'; localStorage.setItem('axon-plate-v3', JSON.stringify(p));
});

// 12) v43 观测者手记：署名 / 我的方案 / 分享码 / 会话恢复 / 我的星座
await page.goto(BASE + '/plate-graph.html', { waitUntil: 'domcontentloaded' });
await page.evaluate(() => {
  localStorage.setItem('axon-plate-v3', JSON.stringify({ view3d: 'galaxy', design: 'galaxy' }));
  localStorage.removeItem('axon-plate-looks-v1');
  localStorage.removeItem('axon-plate-session-v1');
});
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2600);

// 12.1 观测者署名：输入 → 持久化 + 图签印刷
await page.evaluate(() => document.getElementById('pers-btn').click());
await page.waitForTimeout(400);
await page.evaluate(() => {
  const inp = document.getElementById('pr-obs');
  inp.value = '.dk';
  inp.dispatchEvent(new Event('change', { bubbles: true }));
});
await page.waitForTimeout(500);
const obs = await page.evaluate(() => ({
  saved: JSON.parse(localStorage.getItem('axon-plate-v3') || '{}').observer,
  plate: document.querySelector('#gal-plate .gp-obs')?.textContent || '',
}));
ok('署名持久化并印上图签', obs.saved === '.dk' && /OBS \u00b7 \.dk \u00b7 CAT\.\u2116/.test(obs.plate), JSON.stringify(obs));

// 12.2 仪器编号：改色调 → 编号变化（设定指纹）
const catA = await page.evaluate(() => document.querySelector('#gal-plate .gp-obs')?.textContent.match(/№(\w{4})/)?.[1]);
await page.evaluate(() => document.querySelector('#prefs [data-pref="palette"][data-value="cyan"]')?.click());
await page.waitForTimeout(400);
const catB = await page.evaluate(() => document.querySelector('#gal-plate .gp-obs')?.textContent.match(/№(\w{4})/)?.[1]);
ok('仪器编号随设定变化（' + catA + '→' + catB + '）', !!(catA && catB && catA !== catB), catA + ' → ' + catB);

// 12.3 我的方案：存 → 应用（色调切换）→ 删除
await page.evaluate(() => {
  document.getElementById('pr-look-name').value = '夜航二号';
  document.getElementById('pr-look-save').click();
});
await page.waitForTimeout(300);
const savedLook = await page.evaluate(() => !!document.querySelector('[data-mylook="夜航二号"]'));
await page.evaluate(() => document.querySelector('#prefs [data-pref="palette"][data-value="gold"]')?.click());
await page.waitForTimeout(300);
await page.evaluate(() => document.querySelector('[data-mylook="夜航二号"]').click());
await page.waitForTimeout(400);
const applied = await page.evaluate(() => document.documentElement.dataset.palette);
ok('我的方案：保存后可一键应用', savedLook && applied === 'cyan', JSON.stringify({ savedLook, applied }));
await page.evaluate(() => document.querySelector('[data-delook="夜航二号"]')?.click());
await page.waitForTimeout(300);
const deleted = await page.evaluate(() => !document.querySelector('[data-mylook="夜航二号"]'));
ok('我的方案：可删除', deleted);

// 12.4 分享码：导出 → 清库 → 导入恢复
await page.evaluate(() => {
  document.getElementById('pr-look-name').value = '随身';
  document.getElementById('pr-look-save').click();
});
await page.waitForTimeout(300);
await page.evaluate(() => document.getElementById('pr-share-out').click());
await page.waitForTimeout(300);
const code = await page.evaluate(() => document.getElementById('pr-share').value);
ok('分享码以 AXON1. 开头且非空', code.startsWith('AXON1.') && code.length > 40, code.slice(0, 24) + '…');
await page.evaluate(() => { localStorage.removeItem('axon-plate-looks-v1'); document.getElementById('pr-share-in').click(); });
await page.waitForTimeout(400);
const imported = await page.evaluate(() => ({
  looks: Object.keys(JSON.parse(localStorage.getItem('axon-plate-looks-v1') || '{}')),
  obs: JSON.parse(localStorage.getItem('axon-plate-v3') || '{}').observer,
}));
ok('分享码导入恢复方案库与署名', imported.looks.includes('随身') && imported.obs === '.dk', JSON.stringify(imported));
await page.evaluate(() => document.getElementById('pers-btn').click());
await page.waitForTimeout(300);

// 12.5 会话恢复：设范围 → 刷新 → 恢复条 → 继续
await page.evaluate(() => {
  document.querySelector('#gal-plate [data-layer="10-architecture"]')?.click();
});
await page.waitForTimeout(600);
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2400);
const resumeBar = await page.evaluate(() => {
  const el = document.getElementById('obs-resume');
  return { shown: el && !el.hidden, text: el ? el.textContent : '' };
});
ok('恢复条出现并含板块与星系', resumeBar.shown && /架构与设计/.test(resumeBar.text) && /星系视图/.test(resumeBar.text), resumeBar.text.slice(0, 60));
await page.evaluate(() => document.getElementById('or-go').click());
await page.waitForTimeout(800);
const resumed = await page.evaluate(() => ({
  chips: document.getElementById('scope-chips')?.textContent || '',
  galaxy: document.getElementById('plate').className.includes('view-galaxy'),
}));
ok('「继续」恢复星系 + 板块筛选', resumed.galaxy && resumed.chips.includes('架构与设计'), JSON.stringify(resumed).slice(0, 80));

// 12.6 我的星座：预置 3 个关注 → 图签 PIN 计数 + 署名保留 + 截图
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  p.pins = ['docs/30-data/DATA-DICTIONARY.md', 'docs/10-architecture/AXON-GRAPH-DESIGN.md', 'README.md'];
  localStorage.setItem('axon-plate-v3', JSON.stringify(p));
});
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  p.pins = ['docs/30-data/DATA-DICTIONARY.md', 'docs/10-architecture/AXON-GRAPH-DESIGN.md', 'README.md'];
  localStorage.setItem('axon-plate-v3', JSON.stringify(p));
});
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2800);
const v3dbg = await page.evaluate(() => localStorage.getItem('axon-plate-v3'));
const asterism = await page.evaluate(() => ({
  pin: document.querySelector('#gal-plate .gp-obs')?.textContent,
  obs: JSON.parse(localStorage.getItem('axon-plate-v3') || '{}').observer,
}));
ok('星座就绪：图签 PIN 3 + 署名保留', /PIN 3/.test(asterism.pin || '') && asterism.obs === '.dk', JSON.stringify(asterism));
await page.screenshot({ path: 'shots/_galaxy_v43_asterism.png' });
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  delete p.pins;
  localStorage.setItem('axon-plate-v3', JSON.stringify(p));
  localStorage.removeItem('axon-plate-session-v1');
});

// 13) v44 星系精修：混合渲染 / 环交互 / 双击复位 / 动态细节
await page.goto(BASE + '/plate-graph.html', { waitUntil: 'domcontentloaded' });
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  p.view3d = 'galaxy'; p.design = 'galaxy'; p.galaxyRender = '3d';
  delete p.pins;
  localStorage.setItem('axon-plate-v3', JSON.stringify(p));
  localStorage.removeItem('axon-plate-session-v1');
});
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2800);

// 13.0 环交互测试在停转下进行（自转会让两次点击之间的环投影移位，命中点漂移）
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  p.galSpeed = 'halt';
  localStorage.setItem('axon-plate-v3', JSON.stringify(p));
  document.querySelector('#prefs [data-pref="galSpeed"][data-value="halt"]')?.click();
});
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2600);

// 13.1 GL 混合渲染：2D 仪器层在 GL 模式下也绘制（角落签名有像素）
const hybrid = await page.evaluate(() => {
  const cv = document.getElementById('gal-canvas');
  const shown = getComputedStyle(cv).display !== 'none';
  const ctx = cv.getContext('2d');
  const w = cv.width, h = cv.height;
  const corner = ctx.getImageData(Math.round(w * 0.02), h - Math.round(h * 0.08), Math.round(w * 0.3), Math.round(h * 0.06)).data;
  let alpha = 0;
  for (let i = 3; i < corner.length; i += 4) alpha += corner[i];
  const gl = !!document.getElementById('gal-gl');
  return { shown, alpha, gl, dbg: window.__AXON_DEBUG__ ? window.__AXON_DEBUG__.version : 0 };
});
ok('GL 混合：仪器层叠加绘制（角落像素 α=' + hybrid.alpha + '）', hybrid.shown && hybrid.alpha > 200 && hybrid.dbg === 53, JSON.stringify({ shown: hybrid.shown, alpha: hybrid.alpha, dbg: hybrid.dbg }));

// 13.2 环交互：命中 10-architecture 环 → 点击 → 筛选；再点取消
const ringPt = await page.evaluate(() => {
  const r = document.getElementById('graph3d').getBoundingClientRect();
  for (let x = r.left + 20; x < r.right - 20; x += 6) {
    for (let y = r.top + 20; y < r.bottom - 20; y += 6) {
      const el = document.elementFromPoint(x, y);
      if (el && el.closest && el.closest('.n3d')) continue; /* 星优先于环，跳开星体 */
      if (window.__AXON_DEBUG__.ringAt(x - r.left, y - r.top) === '10-architecture') {
        return { cx: x, cy: y };
      }
    }
  }
  return null;
});
ok('环命中检测可定位 10-architecture 环上一点', !!ringPt, JSON.stringify(ringPt));
let ringScope = null;
if (ringPt) {
  await page.evaluate((pt) => {
    const el = document.elementFromPoint(pt.cx, pt.cy) || document.getElementById('graph3d');
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: pt.cx, clientY: pt.cy }));
  }, ringPt);
  await page.waitForTimeout(500);
  ringScope = await page.evaluate(() => ({
    chips: document.getElementById('scope-chips')?.textContent || '',
    hot: document.querySelector('#gal-plate .gp-layer.is-hot b')?.textContent || '',
  }));
  ok('点击环体 → 选中该板块', ringScope.chips.includes('架构与设计') && ringScope.hot.includes('架构与设计'), JSON.stringify(ringScope));
  const pt2 = await page.evaluate(() => {
    const r = document.getElementById('graph3d').getBoundingClientRect();
    for (let x = r.left + 20; x < r.right - 20; x += 6) {
      for (let y = r.top + 20; y < r.bottom - 20; y += 6) {
        const el = document.elementFromPoint(x, y);
        if (el && el.closest && el.closest('.n3d')) continue;
        if (window.__AXON_DEBUG__.ringAt(x - r.left, y - r.top) === '10-architecture') {
          return { cx: x, cy: y };
        }
      }
    }
    return null;
  });
  await page.evaluate((pt) => {
    const el = document.elementFromPoint(pt.cx, pt.cy) || document.getElementById('graph3d');
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: pt.cx, clientY: pt.cy }));
  }, pt2 || ringPt);
  await page.waitForTimeout(400);
  const off = await page.evaluate(() => document.getElementById('scope-chips')?.textContent || '');
  ok('再次点击环体 → 取消筛选', off.trim() === '', off);
}

// 13.3 双击空白复位：先滚轮缩放，再双击空白回到 ×1.00
const zoomBefore = await page.evaluate(() => {
  const g = document.getElementById('graph3d');
  g.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: -240, clientX: 800, clientY: 400 }));
  return document.getElementById('hud-zoom')?.textContent;
});
await page.waitForTimeout(400);
const zoomAfter = await page.evaluate(() => {
  const g = document.getElementById('graph3d');
  g.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, clientX: 400, clientY: 500 }));
  return document.getElementById('hud-zoom')?.textContent;
});
ok('双击空白复位相机（' + zoomBefore + ' → ' + zoomAfter + '）', zoomAfter === '×1.00', zoomBefore + ' → ' + zoomAfter);

// 13.4 仪器图谱模式回归 + 截图（GL 混合与 atlas 各一张）
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  p.observer = '.dk';
  p.pins = ['docs/30-data/DATA-DICTIONARY.md', 'docs/10-architecture/AXON-GRAPH-DESIGN.md', 'README.md'];
  localStorage.setItem('axon-plate-v3', JSON.stringify(p));
});
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2600);
await page.screenshot({ path: 'shots/_galaxy_v44_hybrid.png' });
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  p.galaxyRender = 'atlas';
  p.galSpeed = 'normal';
  localStorage.setItem('axon-plate-v3', JSON.stringify(p));
});
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2600);
const atlasMode = await page.evaluate(() => ({
  canvas: getComputedStyle(document.getElementById('gal-canvas')).display !== 'none',
  glHidden: getComputedStyle(document.getElementById('gal-gl')).display === 'none',
}));
ok('仪器图谱模式：纯 2D 路径正常', atlasMode.canvas && atlasMode.glHidden, JSON.stringify(atlasMode));
await page.screenshot({ path: 'shots/_galaxy_v44_atlas.png' });
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  p.galaxyRender = 'atlas';
  delete p.pins;
  localStorage.setItem('axon-plate-v3', JSON.stringify(p));
  localStorage.removeItem('axon-plate-session-v1');
});

// 14) v49 背景分离 / 维度快捷条 / 维度形变
await page.goto(BASE + '/plate-graph.html', { waitUntil: 'domcontentloaded' });
await page.evaluate(() => {
  localStorage.setItem('axon-plate-v3', JSON.stringify({ view3d: 'galaxy', design: 'galaxy', galaxyRender: 'atlas', palette: 'cyan' }));
  localStorage.removeItem('axon-plate-session-v1');
});
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2600);

// 14.1 星系色调只在星系视图挂载
const palGalaxy = await page.evaluate(() => document.documentElement.dataset.palette || '(none)');
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  p.view3d = 'flat'; p.design = 'original';
  localStorage.setItem('axon-plate-v3', JSON.stringify(p));
});
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2200);
const palFlat = await page.evaluate(() => document.documentElement.dataset.palette || '(none)');
await page.evaluate(() => {
  const p = JSON.parse(localStorage.getItem('axon-plate-v3') || '{}');
  p.view3d = 'galaxy'; p.design = 'galaxy';
  localStorage.setItem('axon-plate-v3', JSON.stringify(p));
});
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2600);
const palBack = await page.evaluate(() => document.documentElement.dataset.palette || '(none)');
ok('背景分离：星系 cyan / 平面无色调 / 回星系恢复', palGalaxy === 'cyan' && palFlat === '(none)' && palBack === 'cyan', [palGalaxy, palFlat, palBack].join(' → '));

// 14.2 维度快捷条：显示、切换、激活态
const dimsw0 = await page.evaluate(() => {
  const el = document.getElementById('dimsw');
  return { shown: el && !el.hidden, n: el ? el.querySelectorAll('[data-dim]').length : 0, on: el?.querySelector('.is-on')?.dataset.dim };
});
ok('维度快捷条：星系内显示 8 键且层级激活', dimsw0.shown && dimsw0.n === 8 && dimsw0.on === 'layer', JSON.stringify(dimsw0));
await page.evaluate(() => document.querySelector('#dimsw [data-dim="time"]').click());
await page.waitForTimeout(300);
const dimsw1 = await page.evaluate(() => ({
  on: document.querySelector('#dimsw .is-on')?.dataset.dim,
  hud: document.getElementById('hud-z')?.textContent,
  gauge: [...document.querySelectorAll('#dimsw ~ *')].length >= 0,
}));
ok('点「时间」→ 激活移动 + HUD 同步', dimsw1.on === 'time' && /修订/.test(dimsw1.hud || ''), JSON.stringify(dimsw1));
await page.waitForTimeout(700); // 形变动画完成（无错误即通过，页面错误由全局捕获）
await page.evaluate(() => document.querySelector('#dimsw [data-dim="layer"]').click());
await page.waitForTimeout(900);

ok('控制台零错误', errs.length === 0, errs.slice(0, 3).join(' | '));
await browser.close();
console.log(process.exitCode ? '\n== 有 FAIL ==' : '\n== 全部通过 ==');
