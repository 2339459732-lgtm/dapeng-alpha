/**
 * DAPENG Alpha · 文档图谱 v3 · 个性化增强层
 * ---------------------------------------------------------------------------
 * 不改动 docs-graph.js：以「增强层」方式挂在它暴露的 window.DocsGraph 上，
 * 拦截 chart.setOption 对 graph series 的标签做重写，并注入一个「个性化」面板。
 *
 * 做四件事：
 *   1. 标签：中文短名 + 文件码两行（原来只有截断的文件名）
 *   2. 常显范围：仅枢纽（原版）/ 全部 / 仅关注
 *   3. 着色：随节点（原版）/ 按「该信哪一篇」的可信层级
 *   4. 关注：双击节点 ★ 关注（常显 + 金星），存在本机
 * 另外把动效 / 光晕 / 节点尺度接到原版主题系统（DocsGraph.setTheme），
 * 所有偏好写 localStorage['dg-personal-v1']，刷新保留。
 */
(function () {
  'use strict';

  /* id -> [中文短名, 文件码, 可信层级] */
  var MAP = {"CHANGELOG.md":["更新日志","CHANGELOG","index"],"README.md":["项目总览","README","index"],"docs/00-strategy/MASTER-PLAN-2026Q4.md":["总体规划","MASTER-PLAN","truth"],"docs/00-strategy/OPEN_SOURCE_INTEGRATION_PLAN.md":["开源融合","OPEN-SOURCE","plan"],"docs/00-strategy/README.md":["文档地图","DOC-MAP","index"],"docs/00-strategy/capability-evolution-plan-2026.md":["能力进化","CAPABILITY","plan"],"docs/00-strategy/product-roadmap-2026.md":["产品路线","ROADMAP","plan"],"docs/10-architecture/SYSTEM-WORKFLOW.md":["系统工作流","WORKFLOW","truth"],"docs/10-architecture/architecture-sidebar-tools.md":["侧栏架构","ARCH-SIDEBAR","history"],"docs/10-architecture/architecture-tab-admin.md":["Tab 后台","ARCH-TAB","history"],"docs/10-architecture/architecture-v54-sidebar-pro.md":["v5.4 架构","ARCH-V5.4","history"],"docs/10-architecture/architecture-westock-mcp.md":["MCP 架构","ARCH-MCP","history"],"docs/10-architecture/auto-trading-architecture.md":["交易架构","ARCH-AUTO","truth"],"docs/10-architecture/class-diagram-v54.mermaid":["v5.4 类图","CLASS-v5.4","history"],"docs/10-architecture/impl-checklist-openbb-dsa.md":["实施清单","IMPL-LIST","plan"],"docs/10-architecture/openbb-dsa-integration-design.md":["OpenBB 设计","OPENBB-DESIGN","plan"],"docs/10-architecture/sequence-diagram-v54.mermaid":["v5.4 时序图","SEQ-v5.4","history"],"docs/10-architecture/system_design.md":["系统底稿","SYS-DESIGN","history"],"docs/20-prd/auto-trading-prd.md":["自动交易","PRD-AUTO","history"],"docs/20-prd/incremental-prd-sidebar-tools.md":["侧栏工具","PRD-SIDEBAR","history"],"docs/20-prd/incremental-prd-tab-admin.md":["Tab 重构","PRD-TAB","history"],"docs/20-prd/incremental-prd-v54-sidebar-pro.md":["v5.4 侧栏","PRD-V5.4","history"],"docs/20-prd/incremental-prd-westock-mcp.md":["MCP 接入","PRD-MCP","history"],"docs/20-prd/openbb-dsa-integration-prd.md":["OpenBB 需求","OPENBB-PRD","plan"],"docs/30-data/DATA-DICTIONARY.md":["数据口径","DATA-DICT","truth"],"docs/40-ops/API-REFERENCE.md":["接口参考","API-REF","truth"],"docs/40-ops/CHANGELOG-2026-10-22.md":["修复总账","LEDGER-10-22","index"],"docs/40-ops/DEPLOYMENT.md":["部署运维","DEPLOYMENT","truth"],"docs/90-archive/HANDOFF-v5.4.md":["v5.4 交接","HANDOFF-v5.4","archive"],"docs/90-archive/SYSTEM-OPTIMIZATION-v4.0.md":["v4.0 报告","OPT-v4.0","archive"],"docs/90-archive/auto-trading-arch-v0-deprecated.md":["架构 v0","ARCH-v0","archive"]};

  var TIER_COLOR = {
    truth: '#D9B481', plan: '#93AECE', history: '#B29EBE',
    archive: '#8C8880', index: '#C3D1D3'
  };
  var TIER_LABEL = { truth: '真相源', plan: '生效计划', history: '历史设计', archive: '归档', index: '索引台账' };
  var RICH_KEYS = ['core', 'norm', 'pin', 'code'];
  var PIN_KEY = 'dg-personal-v1';
  var DEFAULT = { label: 'zh2', show: 'auto', tone: 'auto', motion: 'normal', glow: 1, nodes: 'normal', pins: [] };

  var prefs = (function () {
    var p = Object.assign({}, DEFAULT);
    try {
      var raw = JSON.parse(localStorage.getItem(PIN_KEY) || '{}');
      Object.keys(DEFAULT).forEach(function (k) { if (raw[k] !== undefined) p[k] = raw[k]; });
    } catch (e) {}
    if (!Array.isArray(p.pins)) p.pins = [];
    return p;
  })();
  function save() { try { localStorage.setItem(PIN_KEY, JSON.stringify(prefs)); } catch (e) {} }
  function pinned(id) { return prefs.pins.indexOf(id) >= 0; }

  var DG = null;

  function richStyles() {
    var font = '"Noto Sans SC","Source Han Sans SC","Microsoft YaHei",sans-serif';
    var mono = '"Cascadia Mono","Consolas","SF Mono",monospace';
    var s = {
      core: { fontFamily: font, fontSize: 12.5, fontWeight: 500, lineHeight: 15, color: '#F4EEE2' },
      norm: { fontFamily: font, fontSize: 11.5, fontWeight: 400, lineHeight: 15, color: '#D9D3C7' },
      pin: { fontFamily: font, fontSize: 11, lineHeight: 15, color: '#D9B481' },
      code: { fontFamily: mono, fontSize: 8.5, lineHeight: 11, color: 'rgba(231,225,213,.52)' }
    };
    Object.keys(TIER_COLOR).forEach(function (t) {
      s['t_' + t] = { fontFamily: font, fontSize: 11.5, fontWeight: 450, lineHeight: 15, color: TIER_COLOR[t] };
      s['tc_' + t] = { fontFamily: mono, fontSize: 8.5, lineHeight: 11, color: TIER_COLOR[t], opacity: .78 };
    });
    return s;
  }

  function tierOf(id) { return (MAP[id] && MAP[id][2]) || 'index'; }
  function bucket(id) {
    if (prefs.tone === 'tier') return 't_' + tierOf(id);
    return (DG.state.labelCore && DG.state.labelCore[id]) ? 'core' : 'norm';
  }
  function codeBucket(id) {
    return prefs.tone === 'tier' ? 'tc_' + tierOf(id) : 'code';
  }

  function formatterFor(id) {
    var m = MAP[id] || [id, id, 'index'];
    var zh = m[0], code = m[1];
    var star = pinned(id) ? '{pin|★ }' : '';
    if (prefs.label === 'code') return '{' + codeBucket(id) + '|' + code + '}';
    if (prefs.label === 'zh') return star + '{' + bucket(id) + '|' + zh + '}';
    return star + '{' + bucket(id) + '|' + zh + '}\n{' + codeBucket(id) + '|' + code + '}';
  }

  /** 是否常显：原版枢纽 / 全部 / 仅关注；筛选淡出的节点不显示 */
  function shouldShow(d) {
    var id = d.id;
    var lit = DG.state.litNodes ? !!DG.state.litNodes[id] : true;
    if (!lit) return false;
    if (pinned(id)) return true;
    if (prefs.show === 'all') return true;
    if (prefs.show === 'pins') return false;
    return !!(d.label && d.label.show);
  }

  /** 把一段 option 里的 graph series 标签全部重写 */
  function transform(option) {
    if (!option || !option.series || !DG) return option;
    var list = Array.isArray(option.series) ? option.series : [option.series];
    list.forEach(function (s) {
      if (!s || s.type !== 'graph' || !Array.isArray(s.data)) return;
      s.data.forEach(function (d) {
        if (!d || !d.id || !MAP[d.id]) return;
        var show = shouldShow(d);
        d.label = Object.assign({}, d.label || {}, {
          show: show,
          position: 'right',
          distance: 9,
          align: 'left',
          verticalAlign: 'middle',
          formatter: function () { return formatterFor(d.id); },
          rich: richStyles(),
          fontSize: 12,
          color: '#D9D3C7',
          textBorderColor: 'rgba(2,5,16,.92)',
          textBorderWidth: 3
        });
        if (d.emphasis && d.emphasis.label) {
          d.emphasis.label = Object.assign({}, d.emphasis.label, {
            formatter: function () { return formatterFor(d.id); },
            rich: richStyles(),
            position: 'right', distance: 9, align: 'left',
            textBorderColor: 'rgba(2,5,16,.92)', textBorderWidth: 3
          });
        }
      });
    });
    return option;
  }

  function patchChart() {
    var chart = DG && DG.state && DG.state.chart;
    if (!chart || chart.isDisposed() || chart.__dgpPatched) return false;
    chart.__dgpPatched = true;
    var orig = chart.setOption.bind(chart);
    chart.setOption = function (option, notMerge, lazyUpdate) {
      try { transform(option); } catch (e) {}
      return orig(option, notMerge, lazyUpdate);
    };
    chart.on('dblclick', function (p) {
      var id = p && p.data && p.data.id;
      if (!id || !MAP[id]) return;
      togglePin(id);
    });
    return true;
  }

  /* ── 关注 ─────────────────────────────────────────────────────────────── */
  function togglePin(id) {
    var i = prefs.pins.indexOf(id);
    if (i >= 0) prefs.pins.splice(i, 1); else prefs.pins.push(id);
    save();
    toast((i >= 0 ? '取消关注 ' : '★ 已关注 ') + (MAP[id] ? MAP[id][0] : id));
    refresh();
    renderPanel();
  }
  function refresh() {
    if (!DG) return;
    try { DG.applyFilter(); } catch (e) {
      try { DG.state.chart.setOption({}, { lazyUpdate: false }); } catch (_) {}
    }
  }

  /* ── 面板 UI（复用原版 .dg-settings / .dg-set-* 样式，保证同源观感） ──── */
  var GROUPS = [
    { k: 'label', t: '标签', opts: [['zh2', '双行'], ['zh', '中文'], ['code', '文件码']] },
    { k: 'show', t: '常显', opts: [['auto', '仅枢纽'], ['all', '尽量多'], ['pins', '仅关注']] },
    { k: 'tone', t: '标签色', opts: [['auto', '随节点'], ['tier', '按层级']] },
    { k: 'motion', t: '动效', opts: [['off', '关'], ['normal', '标准']] },
    { k: 'glow', t: '光晕', opts: [[0, '关'], [1, '标准'], [2, '强']] },
    { k: 'nodes', t: '节点', opts: [['compact', '紧凑'], ['normal', '标准'], ['large', '舒展']] }
  ];

  function renderPanel() {
    var box = document.getElementById('dgp-settings');
    if (!box) return;
    var html = GROUPS.map(function (g) {
      return '<div class="dg-set-row"><span class="dg-set-label">' + g.t + '</span><span class="dg-set-opts">' +
        g.opts.map(function (o) {
          var on = String(prefs[g.k]) === String(o[0]);
          return '<button type="button" class="dg-set-opt' + (on ? ' is-on' : '') +
            '" data-dgp-set="' + g.k + '" data-val="' + o[0] + '" aria-pressed="' + on + '">' + o[1] + '</button>';
        }).join('') + '</span></div>';
    }).join('');
    html += '<div class="dg-set-row dgp-tiers"><span class="dg-set-label">层级</span><span class="dgp-tier-legend">' +
      Object.keys(TIER_COLOR).map(function (t) {
        return '<i title="' + TIER_LABEL[t] + '"><b style="background:' + TIER_COLOR[t] + '"></b>' + TIER_LABEL[t] + '</i>';
      }).join('') + '</span></div>';
    html += '<div class="dgp-actions">' +
      '<button type="button" class="dg-set-opt" data-dgp-pins-clear>清除关注（' + prefs.pins.length + '）</button>' +
      '<button type="button" class="dg-set-opt" data-dgp-reset>恢复默认</button></div>';
    html += '<div class="dg-set-foot">球体颜色仍是分组色（与上方 chip 一致）；「按层级」只改标签颜色。「尽量多」会显示所有不重叠的标签。<br>双击任意节点 = ★ 关注 / 取消。设置保存在本机。</div>';
    box.innerHTML = html;
  }

  function applyThemeToOriginal() {
    try {
      if (DG && DG.setTheme) {
        DG.setTheme({ motion: prefs.motion, glow: Number(prefs.glow), nodes: prefs.nodes });
      }
    } catch (e) {}
  }

  function bindPanel() {
    var btn = document.getElementById('dgp-btn');
    var box = document.getElementById('dgp-settings');
    if (!btn || !box) return;
    renderPanel();
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = box.hidden;
      box.hidden = !open;
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) {
        // 贴在「个性化」按钮下方（原版面板用的是 right:10px，这里按按钮位置对齐）
        var host = box.offsetParent || box.parentNode;
        var left = btn.offsetLeft + btn.offsetWidth - box.offsetWidth;
        box.style.right = 'auto';
        box.style.left = Math.max(8, left) + 'px';
      }
    });
    box.addEventListener('click', function (e) {
      var t = e.target.closest ? e.target.closest('[data-dgp-set]') : null;
      if (t) {
        var k = t.getAttribute('data-dgp-set');
        var v = t.getAttribute('data-val');
        if (k === 'glow') v = Number(v);
        prefs[k] = v;
        save();
        if (k === 'motion' || k === 'glow' || k === 'nodes') applyThemeToOriginal();
        renderPanel();
        refresh();
        return;
      }
      if (e.target.closest('[data-dgp-pins-clear]')) {
        prefs.pins = []; save(); renderPanel(); refresh(); return;
      }
      if (e.target.closest('[data-dgp-reset]')) {
        prefs = Object.assign({}, DEFAULT, { pins: [] });
        save(); applyThemeToOriginal(); renderPanel(); refresh();
        return;
      }
    });
    document.addEventListener('click', function (e) {
      // 面板内按钮会触发 innerHTML 重渲染，原 target 已脱离文档；这种点击不当作「点在外面」
      if (e.target && !document.documentElement.contains(e.target)) return;
      if (!box.hidden && !box.contains(e.target) && e.target !== btn) {
        box.hidden = true;
        btn.setAttribute('aria-expanded', 'false');
      }
    });
  }

  /** 版本级入口：放到项目文档页顶栏，避免挤压图谱工具条的 8 个分组 chip */
  function injectTopbarLink() {
    if (document.getElementById('dgp-axon')) return;
    var host = document.querySelector('.docs-topbar-actions');
    if (!host) return;
    var a = document.createElement('a');
    a.className = 'docs-nav-btn is-gold dgp-axon-link';
    a.id = 'dgp-axon';
    a.href = '/plate-graph.html';
    a.target = '_blank';
    a.rel = 'noopener';
    a.title = '替代版式：按信息架构分列的仪器图版（另开一页，可与原版对照）';
    a.innerHTML = 'AXON 图版 <span aria-hidden="true">↗</span>';
    // 顶栏已按「视图 / 业务入口 / 工具」分组：AXON 属于业务入口，
    // 必须插进对应分组内。直接以组内按钮为参照插到 host 会抛 NotFoundError
    // （参照节点不是 host 的直接子节点），入口会被 try/catch 静默吞掉。
    var group = host.querySelector('.docs-nav-group.is-business') || host;
    if (group.firstChild) group.insertBefore(a, group.firstChild);
    else group.appendChild(a);
  }
  function injectUI() {
    var actions = document.querySelector('.dg-actions');
    if (!actions || document.getElementById('dgp-btn')) return;

    var btn = document.createElement('button');
    btn.className = 'dg-btn';
    btn.type = 'button';
    btn.id = 'dgp-btn';
    btn.setAttribute('aria-expanded', 'false');
    btn.setAttribute('aria-controls', 'dgp-settings');
    btn.title = '标签 / 常显 / 着色 / 关注 —— 每人一套的读法';
    btn.textContent = '个性化';
    var anchor = document.getElementById('dg-reset');
    if (anchor && anchor.parentNode === actions) actions.insertBefore(btn, anchor);
    else actions.appendChild(btn);

    var box = document.createElement('div');
    box.className = 'dg-settings dgp-settings';
    box.id = 'dgp-settings';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-label', '个性化设置');
    box.hidden = true;
    var host = document.getElementById('dg-settings');
    if (host && host.parentNode) host.parentNode.insertBefore(box, host.nextSibling);
    else document.querySelector('.dg-toolbar').appendChild(box);
    bindPanel();
  }

  var toastTimer = null;
  function toast(msg) {
    var el = document.getElementById('dgp-toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'dgp-toast';
      el.className = 'dgp-toast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add('is-on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove('is-on'); }, 1500);
  }

  /* ── 启动：等原版就绪 → 打补丁 → 注入 UI ─────────────────────────────── */
  try { injectTopbarLink(); } catch (e) {}

  var tries = 0;
  var timer = setInterval(function () {
    tries += 1;
    DG = window.DocsGraph;
    if (DG && DG.state && DG.state.chart && DG.state.data) {
      clearInterval(timer);
      if (patchChart()) {
        injectUI();
        applyThemeToOriginal();
        refresh();
      }
      return;
    }
    if (tries > 200) clearInterval(timer);
  }, 80);

  /* 切到图谱视图时，原版会重新 paint / applyFilter，补丁已在，无需重复处理 */
  document.addEventListener('click', function (e) {
    var t = e.target.closest ? e.target.closest('[data-dg-view]') : null;
    if (!t) return;
    setTimeout(function () {
      DG = window.DocsGraph;
      if (patchChart()) { injectUI(); refresh(); }
    }, 60);
  });
})();
