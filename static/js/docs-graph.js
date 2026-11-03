/**
 * DAPENG Alpha · 文档关系图谱（docs-graph.js v1）
 * ---------------------------------------------------------------------------
 * 设计来源：借鉴 neural-creator-dashboard 的「三层交互 + 一类一色 + 尊重减少动效」
 * 的信息架构，**不复制其源码/样式/素材**（该项目为 PolyForm Noncommercial）。
 *
 * 三层交互：
 *   ① 全部（所有文档节点）
 *   ② 分组（点 chip 高亮一组，其余淡出，但**不移除**——避免布局跳动）
 *   ③ 单文档（点节点 → 右侧分析栏：出链 / 反链 / 摘要 / 路径复制）
 *   Esc 关闭分析栏；面包屑「全部 › {分组} › {文档名}」可逐层返回。
 *
 * 数据源：GET /static/docs-graph.json（后端契约见 tools/gen_docs_graph.py 头部）
 *
 * 动效纪律：
 *   - 力导向**只跑一次**（layoutAnimation:false，收敛后把坐标固化，后续筛选改 layout:'none'），
 *     几十个节点持续抖动既耗 CPU 也影响阅读；
 *   - 除面板滑入与 hover 外无动画；prefers-reduced-motion 时面板瞬时显示（CSS 处理）。
 *
 * 渲染引擎：本地 vendor 的 ECharts（series.type='graph'），不引入新库。
 */
(function () {
  'use strict';

  /* ── 配色（规格：6 组各一色，明度接近、色相错开；入口/核心用香槟金）── */
  /* ── 设计令牌（v10）──────────────────────────────────────────────────────
     参考 solar-system-atlas 的设计语言：近黑中性底 + 暖金单一强调色 + 仪器式细线框架
     + 宽字距排版。核心判断：那种「高级感」来自**克制**——金色只做点缀、线条极细、
     光效极弱。所以这一版是往回收，不是继续加：
       · 取消青/紫极光，背景回归中性近黑，仅保留极稀疏暖色星尘
       · 强调色从青（#06b6d4）改为暖金（#D6B084），且只用于悬停/选中/核心节点
       · 分组色整体降饱和、压暗，保留可区分性但不再抢眼 */
  var INK = '#E7E1D5';             // 暖骨白（主文字）
  var GOLD = '#D6B084';            // 暖金（强调）
  var COPPER = '#C58A55';          // 铜（次强调）
  var HAIR = 'rgba(198,168,130,.28)';   // 仪器细线
  var DIM = 'rgba(231,225,213,.42)';    // 次级文字
  var PALETTE = ['#5F78A6', '#617F5C', '#A3773F', '#4F7C92', '#8A6474', '#437F6A'];
  var LINK_COLOR = 'rgba(198,168,130,.20)';

  /** kind 枚举 -> 中文（界面全中文；未知值一律回落「文档」，不泄露内部枚举） */
  var KIND_LABEL = {
    entry: '入口', readme: '索引', index: '索引', design: '设计', prd: 'PRD',
    spec: '规范', plan: '计划', ops: '运维', data: '数据字典', archive: '归档',
    diagram: '架构图', doc: '文档', missing: '缺失'
  };

  var GROUP_LABEL_FALLBACK = {
    '00-strategy': '战略与计划',
    '10-architecture': '架构与设计',
    '20-prd': '产品需求',
    '30-data': '数据字典',
    '40-ops': '运维与交付',
    '90-archive': '归档',
    root: '根目录'
  };

  var state = {
    data: null,          // 原始 JSON
    byId: {},            // id -> node
    degree: {},          // id -> 度数
    outMap: {},          // id -> [target...]
    inMap: {},           // id -> [source...]
    chart: null,
    ready: false,
    loading: false,
    settled: false,      // 力导向是否已收敛（收敛后坐标固化）
    pos: {},             // id -> [x, y]
    group: '',           // 当前高亮分组（空 = 全部）
    keyword: '',         // 搜索词
    selected: '',        // 当前选中节点 id
    labelAlways: {},     // 常显标签的节点（核心 + 度数 Top5 枢纽）
    labelCore: {},       // 其中属于「核心/入口/索引」的那部分（视觉权重更高）
    labelHub: {},        // 其中属于「度数 Top5 枢纽」的那部分
    labelText: {},       // id -> 标签文本（文件名消歧后的确定性结果，两两互异）
    narrow: false,       // 窄屏（<=900px）时全部退回 hover-only
    rendered: 0,         // ECharts rendered 事件计数（验收：静置不持续重绘）
    paints: 0,           // 主动 setOption 次数
    error: '',
    layoutSource: '',    // 'python'（预计算坐标）| 'client'（本地兜底）
    communities: [],     // 社区发现结果（含凸包，用于画群落光环）
    metrics: null        // 图指标（密度/模块度/直径…），用于信息条
  };

  /* ── 视觉主题（可调 · 可持久化 · 可编程）───────────────────────────────
     把此前散落在各处的魔法数字收敛成一份 theme。四组开关覆盖了「观感」的主要自由度：
       motion : off | soft | normal   动效强度（环呼吸 / 星尘 / 粒子 / 呼吸光晕）
       labels : low | normal | high   常显标签密度（占节点数的比例）
       nodes  : compact | normal | large  节点尺度（整体放大/缩小）
       glow   : 0 | 1 | 2             光晕强度（节点 shadowBlur 倍率）
     存 localStorage，刷新后保持；同时暴露 DocsGraph.setTheme()，便于将来接后台下发配置。
     约束：任何取值都不改变布局坐标与数据语义 —— 只影响观感，不影响 maxMove=0。 */
  var THEME_KEY = 'dg-theme-v1';
  var THEME_DEFAULTS = { motion: 'normal', labels: 'normal', nodes: 'normal', glow: 1 };
  var THEME_VALUES = {
    motion: ['off', 'soft', 'normal'],
    labels: ['low', 'normal', 'high'],
    nodes: ['compact', 'normal', 'large'],
    glow: [0, 1, 2]
  };

  function readTheme() {
    var t = { motion: THEME_DEFAULTS.motion, labels: THEME_DEFAULTS.labels,
              nodes: THEME_DEFAULTS.nodes, glow: THEME_DEFAULTS.glow };
    try {
      var raw = JSON.parse(localStorage.getItem(THEME_KEY) || '{}');
      Object.keys(t).forEach(function (k) {
        if (THEME_VALUES[k] && THEME_VALUES[k].indexOf(raw[k]) >= 0) t[k] = raw[k];
      });
    } catch (e) {}
    return t;
  }
  function writeTheme() {
    try { localStorage.setItem(THEME_KEY, JSON.stringify(theme)); } catch (e) {}
  }
  var theme = readTheme();

  function themeMotion() {
    return theme.motion === 'off' ? 0 : (theme.motion === 'soft' ? 0.5 : 1);
  }
  function themeLabelRatio() {
    return theme.labels === 'low' ? 0.20 : (theme.labels === 'high' ? 0.60 : 0.40);
  }
  function themeNodeScale() {
    return theme.nodes === 'compact' ? 0.86 : (theme.nodes === 'large' ? 1.16 : 1);
  }
  function themeGlow() {
    return Number(theme.glow) || 0;
  }

  /* ── 小工具 ─────────────────────────────────────────────────────────── */
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n >= 1024 * 1024) return (n / 1048576).toFixed(2) + ' MB';
    if (n >= 1024) return (n / 1024).toFixed(1) + ' KB';
    return n + ' B';
  }
  function cssVar(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name);
      v = (v || '').trim();
      return v || fallback;
    } catch (e) { return fallback; }
  }
  /** kind 中文标签（未知 -> 文档） */
  function kindLabel(kind) {
    return KIND_LABEL[String(kind == null ? '' : kind).toLowerCase()] || '文档';
  }

  /**
   * 边的出处文案（硬红线：真实数据 > 如实降级 > 不编造）
   *  - type='link'      有真实源码行号 line -> 「引用位置：第 N 行」
   *  - type='data_link' 天生无行位置（只是登记在同一导航分组）-> 「登记分组：<group>」
   *  - 任一字段缺失/非法 -> 返回空串，**不渲染该行**（绝不出现「第 0 行」「第 null 行」）
   * @param {Object} edge {type, line, group}
   * @param {boolean} isBack 是否为反链（行号属于对方文件，标注清楚避免误读）
   */
  function edgeOriginText(edge, isBack) {
    if (!edge) return '';
    var type = String(edge.type || 'link');
    if (type === 'data_link') {
      var g = edge.group;
      if (typeof g !== 'string' || !g.trim()) return '';
      return '登记分组：' + g;
    }
    var ln = edge.line;
    var ok = (typeof ln === 'number' && isFinite(ln) && ln > 0 && Math.floor(ln) === ln);
    if (!ok) return '';
    return '引用位置：第 ' + ln + ' 行' + (isBack ? '（对方文件）' : '');
  }

  function isReducedMotion() {
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
  }

  /* ── 视图切换（列表 / 图谱）─────────────────────────────────────────── */
  function currentView() {
    return document.body.classList.contains('docs-view-graph') ? 'graph' : 'list';
  }

  function setView(view, opts) {
    var isGraph = view === 'graph';
    document.body.classList.toggle('docs-view-graph', isGraph);
    var btns = document.querySelectorAll('[data-dg-view]');
    for (var i = 0; i < btns.length; i++) {
      btns[i].setAttribute('aria-selected', String(btns[i].getAttribute('data-dg-view') === view));
    }
    var gv = $('docs-graph-view');
    if (gv) gv.hidden = !isGraph;
    if (isGraph) {
      // 首次进入才初始化（列表视图下不加载 JSON、不建 ECharts 实例，省流量与内存）
      initGraph();
      atmoRefresh();
      try {
        if (history.replaceState) history.replaceState(null, '', '#graph');
      } catch (e) {}
    } else {
      // 离开图谱：立刻停掉背景与粒子动画，不在后台空转
      atmoStop();
      try {
        if (history.replaceState) history.replaceState(null, '', location.pathname + location.search);
      } catch (e) {}
      closePanel(true);
    }
    if (opts && opts.keepScroll !== true) {
      try { window.scrollTo({ top: 0, behavior: isReducedMotion() ? 'auto' : 'smooth' }); } catch (e) { window.scrollTo(0, 0); }
    }
  }

  /* ── 状态层（加载 / 错误 / 空）─────────────────────────────────────── */
  function showState(kind, title, msgHtml, withRetry) {
    var box = $('dg-state');
    if (!box) return;
    var canvasCard = $('dg-canvas-card');
    if (canvasCard) canvasCard.hidden = true;
    box.hidden = false;
    box.innerHTML = ''
      + '<div class="dg-state-icon" aria-hidden="true">' + (kind === 'error' ? '⚠' : (kind === 'empty' ? '○' : '◌')) + '</div>'
      + '<div class="dg-state-title">' + esc(title) + '</div>'
      + '<div class="dg-state-msg">' + msgHtml + '</div>'
      + (withRetry ? '<button class="dg-btn is-primary" type="button" id="dg-retry">重新加载</button>' : '');
    var retry = $('dg-retry');
    if (retry) retry.addEventListener('click', function () { loadData(true); });
  }

  /**
   * 进入编排：图谱首次出现时，工具条 / 画布 / chip 依次上浮淡入（80–140ms 递进）。
   * 目的不是"炫"，而是让眼睛有个落点顺序：先框架，再图形，最后控件。
   * 只播一次（重进图谱不再重放，避免反复进出时的廉价感）；reduce 时整体跳过。
   */
  function playEnterChoreography() {
    if (isReducedMotion()) return;
    var gv = $('docs-graph-view');
    if (!gv || gv._dgEntered) return;
    gv._dgEntered = true;
    gv.classList.add('dg-entering');
    window.setTimeout(function () { gv.classList.remove('dg-entering'); }, 1500);
  }

  function hideState() {
    var box = $('dg-state');
    if (box) { box.hidden = true; box.innerHTML = ''; }
    var canvasCard = $('dg-canvas-card');
    if (canvasCard) canvasCard.hidden = false;
  }

  /* ── 布局：自算力导向（Fruchterman–Reingold 变体，确定性）─────────────
     为什么不用 ECharts 内置 layout:'force'：
       ECharts 的 force 会在**每次 setOption 时重跑一次模拟**，于是「点分组 chip /
       搜索」这类只改透明度的操作也会让节点漂移几十到几百像素（实测 400+ px），
       直接违反「筛选不跳动」的验收项；同时它需要首帧后再固化坐标，逻辑脆弱。
     改为：建图时算一次确定性布局，之后所有重绘都用 layout:'none' + 固定坐标，
     既零漂移也零持续计算（CPU 友好），视觉上仍是力导向的团簇结构。
     初始位置用「分组圆环 + 基于 id 的确定性抖动」，同一份数据每次结果一致。 */
  function computeLayout(nodes, links, opts) {
    var o = opts || {};
    var W = o.w || 1000, H = o.h || 660;
    var n = nodes.length;
    if (!n) return {};
    var pos = {};
    var order = nodes.map(function (x) { return x.id; });
    var indexOf = {};
    order.forEach(function (id, i) { indexOf[id] = i; });
    var cx = W / 2, cy = H / 2;
    // 分组凝聚力权重：太小看不出簇，太大会把组压成球、组间连线拉长成放射状。
    // 0.16 是实测值（31 节点 / 78 连线）：簇边界清晰且组间连线仍可读。
    var COHESION = 0.26;
    // v10：半径 0.34 → 0.40。之前节点聚在中部，四周留下大片空白，
    // 仪器环画上去后构图明显"缩在框里"。放大到 0.40 让网络大致贴合第三环，
    // 仍留出外圈刻度的呼吸区。
    // v25：由「正圆」改为「椭圆」。画布是 1458×740 的宽幅，正圆半径只能按短边取，
    // 结果点云横向只占中间约 40%，两侧各留 400px 空白，构图显得缩在框里。
    // 现在横竖分别给半径：横向铺开到画布 60% 宽，竖向仍按短边 40% 留出刻度环呼吸区。
    var Rx = W * 0.30;
    var Ry = Math.min(H * 0.40, Rx * 0.92);

    function hash(str) {
      var h = 2166136261;
      for (var i = 0; i < str.length; i++) {
        h ^= str.charCodeAt(i);
        h = (h * 16777619) >>> 0;
      }
      return h / 4294967295;
    }

    // 初始位置：按分组落到圆环的不同扇区（同组聚在一起），加确定性抖动
    var groups = [];
    order.forEach(function (id, i) {
      var g = nodes[i].group || '';
      if (groups.indexOf(g) < 0) groups.push(g);
    });
    order.forEach(function (id, i) {
      var g = nodes[i].group || '';
      var gi = Math.max(0, groups.indexOf(g));
      var sector = (gi / Math.max(1, groups.length)) * Math.PI * 2;
      var j1 = hash(id + 'a'), j2 = hash(id + 'b');
      var ang = sector + (j1 - 0.5) * 0.9;
      var jr = 0.45 + 0.60 * j2;
      pos[id] = [cx + Math.cos(ang) * Rx * jr, cy + Math.sin(ang) * Ry * jr];
    });

    // 力导向迭代（经典 FR：斥力 k²/d、弹簧 d²/k、向心 gravity、温度退火）
    var area = W * H;
    var k = Math.sqrt(area / n) * 1.26;   // v12：斥力加大 → 点云密度更均匀，减少中心堆积
    var iters = Math.min(420, 220 + n * 4);
    var edges = links.map(function (l) { return [l.source, l.target]; })
      .filter(function (e) { return pos[e[0]] && pos[e[1]]; });
    var disp = {};
    for (var it = 0; it < iters; it++) {
      var t = (1 - it / iters) * (k * 0.55) + 0.4;    // 温度：逐步收敛
      order.forEach(function (id) { disp[id] = [0, 0]; });

      /* v25：分组凝聚力。
         原实现只在「初始位置」按分组分扇区，随后纯 FR 力导向会把分组打散 ——
         跨组引用很多，弹簧力一拉，颜色就混成一锅，看不出「架构 / PRD / 运维」的结构。
         这里每轮迭代先把各组质心算出来，再让节点向自己组的质心收拢，
         组与组之间靠斥力自然分离。净效果：簇可见，且不改变连线的连通关系。 */
      var gSum = {}, gCnt = {};
      order.forEach(function (id) {
        var g = nodes[indexOf[id]].group || '';
        var p = pos[id];
        if (!gSum[g]) { gSum[g] = [0, 0]; gCnt[g] = 0; }
        gSum[g][0] += p[0]; gSum[g][1] += p[1]; gCnt[g] += 1;
      });

      for (var i = 0; i < n; i++) {
        var a = order[i], pa = pos[a];
        for (var j = i + 1; j < n; j++) {
          var b = order[j], pb = pos[b];
          var dx = pa[0] - pb[0], dy = pa[1] - pb[1];
          var d2 = dx * dx + dy * dy;
          if (d2 < 0.01) { dx = (hash(a + b) - 0.5) * 2; dy = (hash(b + a) - 0.5) * 2; d2 = dx * dx + dy * dy || 0.01; }
          var d = Math.sqrt(d2);
          var f = (k * k) / d;
          var ux = dx / d, uy = dy / d;
          disp[a][0] += ux * f; disp[a][1] += uy * f;
          disp[b][0] -= ux * f; disp[b][1] -= uy * f;
        }
      }

      edges.forEach(function (e) {
        var pa = pos[e[0]], pb = pos[e[1]];
        var dx = pa[0] - pb[0], dy = pa[1] - pb[1];
        var d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        var f = (d * d) / k;
        var ux = dx / d, uy = dy / d;
        disp[e[0]][0] -= ux * f; disp[e[0]][1] -= uy * f;
        disp[e[1]][0] += ux * f; disp[e[1]][1] += uy * f;
      });

      order.forEach(function (id) {
        var p0 = pos[id];
        disp[id][0] += (cx - p0[0]) * 0.06;      // 向心力，避免整体飘散
        disp[id][1] += (cy - p0[1]) * 0.06;
        // 分组凝聚力：向本组质心收拢（质心取本轮迭代开始时的位置）
        var g = nodes[indexOf[id]].group || '';
        if (gCnt[g] > 1) {
          var gx = gSum[g][0] / gCnt[g], gy = gSum[g][1] / gCnt[g];
          disp[id][0] += (gx - p0[0]) * COHESION;
          disp[id][1] += (gy - p0[1]) * COHESION;
        }
        var dd = Math.hypot(disp[id][0], disp[id][1]) || 1;
        var step = Math.min(dd, t);
        p0[0] += (disp[id][0] / dd) * step;
        p0[1] += (disp[id][1] / dd) * step;
        p0[0] = Math.max(24, Math.min(W - 24, p0[0]));
        p0[1] = Math.max(24, Math.min(H - 24, p0[1]));
      });
    }
    /* v12：碰撞松弛（解决节点重叠）
       力导向只保证"大致均匀"，会留下少数互相贴住的节点对 —— 实测最近邻仅 36px，
       而两个节点半径之和可达 70px，于是画面上出现"两个球粘在一起"，这是杂乱的直接来源。
       这里做一次确定性的局部松弛：仅对距离小于「两半径之和 + 间隙」的节点对沿连线互相推开，
       迭代到稳定为止。不改变整体结构，只消除重叠。 */
    var degMap = {};
    order.forEach(function (id) { degMap[id] = 0; });
    edges.forEach(function (e) { degMap[e[0]] += 1; degMap[e[1]] += 1; });
    var GAP = 10;
    for (var pass = 0; pass < 80; pass++) {
      var moved = false;
      for (var a1 = 0; a1 < n; a1++) {
        for (var b1 = a1 + 1; b1 < n; b1++) {
          var ia = order[a1], ib = order[b1];
          var pa2 = pos[ia], pb2 = pos[ib];
          var need = (sizeOf(degMap[ia]) + sizeOf(degMap[ib])) / 2 + GAP;
          var ddx = pb2[0] - pa2[0], ddy = pb2[1] - pa2[1];
          var dd2 = Math.hypot(ddx, ddy) || 0.01;
          if (dd2 >= need) continue;
          var push = (need - dd2) / 2;
          var ux2 = ddx / dd2, uy2 = ddy / dd2;
          pa2[0] -= ux2 * push; pa2[1] -= uy2 * push;
          pb2[0] += ux2 * push; pb2[1] += uy2 * push;
          moved = true;
        }
      }
      if (!moved) break;
    }

    var out = {};
    order.forEach(function (id) { out[id] = [Math.round(pos[id][0] * 100) / 100, Math.round(pos[id][1] * 100) / 100]; });
    return out;
  }

  /* ── 数据加载 ───────────────────────────────────────────────────────── */
  function loadData(force) {
    if (state.loading) return;
    state.loading = true;
    state.error = '';
    showState('loading', '正在加载图谱数据…', '读取 <code>static/docs-graph.json</code>');
    var url = 'docs-graph.json' + (force ? ('?t=' + Date.now()) : '');
    fetch(url, { cache: force ? 'no-store' : 'default' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + (r.statusText || ''));
        return r.text();
      })
      .then(function (text) {
        var json;
        try {
          json = JSON.parse(text);
        } catch (e) {
          throw new Error('JSON 解析失败：' + (e && e.message ? e.message : e));
        }
        state.loading = false;
        // 标记「数据已读到、是渲染阶段抛的错」——否则会被下面的 catch 当成
        // 「数据文件不可用」，把用户引向「去重新生成 JSON」这个错误方向。
        try {
          ingest(json);
        } catch (e) {
          if (e && typeof e === 'object') e._stage = 'render'; else throw e;
          throw e;
        }
      })
      .catch(function (err) {
        state.loading = false;
        state.error = String(err && err.message ? err.message : err);
        var is404 = /HTTP 404/.test(state.error);
        if (err && err._stage === 'render') {
          showState('error', '图谱渲染出错：数据已读到，但代码抛错',
            '原因：<code>' + esc(state.error) + '</code>'
            + '<br>这不是数据文件的问题，<b>不要</b>重新生成 <code>static/docs-graph.json</code>；'
            + '请检查 <code>static/js/docs-graph.js</code>（可按 F12 查看堆栈）。',
            true);
          return;
        }
        showState('error',
          is404 ? '图谱数据不可用：文件不存在' : '图谱数据不可用',
          '原因：' + esc(state.error)
          + '<br>数据文件：<code>static/docs-graph.json</code>'
          + '<br>请先生成图谱数据（后端生成器或 <code>tools/gen_docs_graph.py</code>），再点下方重试。',
          true);
      });
  }

  /** 归一化数据：容忍字段缺失（后端仍在演进） */
  /* ── 布局来源：优先 Python 预计算坐标（v26）─────────────────────────────
     ``tools/graph_layout.py`` 在生成 docs-graph.json 时已经把 FR 布局算完，
     写进每个节点的 ``x`` / ``y``（连同顶层 ``layout.w/h``）。前端直接拿来用
     ``layout:'none'`` 渲染，收益是：

       · 首屏不再跑 420 轮 × 31² 斥力对（≈40 万次浮点），打开即出图；
       · 坐标跨浏览器/跨设备完全一致，不受浮点实现差异影响；
       · 布局可以离线调参后重新生成 JSON，不用改 JS。

     只有在 JSON 缺坐标（旧产物 / 生成脚本降级）时才退回本地 ``computeLayout``，
     保证「没有新字段也能跑」。 */
  function readPrecomputedPos(json) {
    var lay = json && json.layout;
    var nodes = (json && json.nodes) || [];
    if (!lay || !lay.w || !nodes.length) return null;
    var out = {};
    var ok = 0;
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var x = n && n.x, y = n && n.y;
      if (typeof x !== 'number' || typeof y !== 'number' || !isFinite(x) || !isFinite(y)) continue;
      out[String(n.id)] = [x, y];
      ok += 1;
    }
    // 至少八成节点带坐标才整份采信，否则视为旧数据，统一退回本地布局
    if (ok < Math.ceil(nodes.length * 0.8)) return null;
    return out;
  }

  function ingest(json) {
    var nodes = Array.isArray(json && json.nodes) ? json.nodes : [];
    var links = Array.isArray(json && json.links) ? json.links : [];
    if (!nodes.length) {
      state.data = json || {};
      showState('empty', '图谱为空：没有任何文档节点',
        '数据文件可读，但 <code>nodes</code> 为 0。请检查文档目录下是否存在 <code>.md</code> 文件，或重新生成图谱数据。',
        true);
      return;
    }

    state.data = json;
    state.byId = {};
    state.degree = {};
    state.outMap = {};
    state.inMap = {};
    state.pos = {};
    state.settled = false;

    nodes.forEach(function (n) {
      var id = String(n.id || n.name || '');
      if (!id) return;
      state.byId[id] = {
        id: id,
        name: n.name || id.split('/').pop(),
        title: n.title || n.name || id,
        group: n.group || 'root',
        group_label: n.group_label || GROUP_LABEL_FALLBACK[n.group] || (n.group || '根目录'),
        kind: n.kind || 'doc',
        bytes: Number(n.bytes) || 0,
        lines: Number(n.lines) || 0,
        updated: n.updated || '',
        missing: !!n.missing,
        summary: n.summary || '',
        raw: n
      };
      state.degree[id] = 0;
      state.outMap[id] = [];
      state.inMap[id] = [];
    });

    links.forEach(function (l) {
      var s = String(l.source || ''), t = String(l.target || '');
      if (!state.byId[s] || !state.byId[t]) return;
      // 存边对象：同一对节点可能同时存在 link（有行号）与 data_link（有登记分组）
      state.outMap[s].push({ id: t, type: String(l.type || 'link'), line: l.line, group: l.group });
      state.inMap[t].push({ id: s, type: String(l.type || 'link'), line: l.line, group: l.group });
      state.degree[s] = (state.degree[s] || 0) + 1;
      state.degree[t] = (state.degree[t] || 0) + 1;
    });

    // 尺寸/透明度/颜色三通道共用同一个分母，必须先算全图最大度数
    MAX_DEG_CACHE = 1;
    Object.keys(state.degree).forEach(function (k) {
      if (state.degree[k] > MAX_DEG_CACHE) MAX_DEG_CACHE = state.degree[k];
    });

    // 标签分层要在度数算完之后（依赖 degree）
    computeLabelSet();
    // 标签文本同样依赖完整节点集（要靠全量比对来消歧），必须在这里算，不能拖到渲染时
    state.labelText = computeLabelTexts();

    // 先算好确定性布局（一次性），再用 layout:'none' 渲染 —— 之后任何重绘都不动坐标
    state.pos = readPrecomputedPos(json);
    state.layoutSource = 'python';
    if (!state.pos) {
      // 旧产物兜底：JSON 里没有预计算坐标，退回浏览器端自算（参数与 Python 侧同源）
      state.pos = computeLayout(nodes.map(function (n) { return n.raw || n; }).map(function (n) {
        return { id: String(n.id), group: n.group || 'root' };
      }), (json.links || []).map(function (l) { return { source: String(l.source), target: String(l.target) }; }));
      state.layoutSource = 'client';
    }
    // 群落 / 桥接 / 指标：只有新版产物才有，缺失时相关图层自动不画
    state.communities = Array.isArray(json.communities) ? json.communities : [];
    state.metrics = (json.metrics && typeof json.metrics === 'object') ? json.metrics : null;
    state.settled = true;
    // 有离线布局就用它（构图质量更高），否则保留上面算好的前端布局
    applyOfflineLayout();

    hideState();
    renderChips();
    buildChart();
    updateMeta();
    // 首次出现时的入场编排（只播一次；reduce 时跳过）
    playEnterChoreography();
    applyHashSelection();
  }

  function updateMeta() {
    var el = $('dg-meta');
    if (!el) return;
    var st = (state.data && state.data.stats) || {};
    var n = Object.keys(state.byId).length;
    var l = (state.data && state.data.links ? state.data.links.length : 0);
    var m = state.metrics || {};

    var parts = ['节点 ' + n, '连线 ' + l];
    if (st.missing) parts.push('缺失 ' + st.missing);
    /* 图指标（v26）：这些数由 Python 侧精确算出（Brandes 介数、CNM 模块度、
       全源 BFS 直径），前端只负责展示 —— 不在浏览器里重算一遍，避免
       「同一张图两个数字」的尴尬。 */
    if (typeof m.communities === 'number' && m.communities > 0) {
      parts.push('群落 ' + m.communities);
    }
    if (typeof m.modularity === 'number') {
      parts.push('模块度 ' + m.modularity.toFixed(2));
    }
    if (typeof m.diameter === 'number' && m.diameter > 0) {
      parts.push('直径 ' + m.diameter);
    }
    if (typeof m.density === 'number') {
      parts.push('密度 ' + (m.density * 100).toFixed(1) + '%');
    }
    parts.push('生成于 ' + ((state.data && state.data.generated_at)
      ? state.data.generated_at.replace('T', ' ').slice(0, 19) : '—'));
    el.textContent = parts.join(' · ');

    // 悬停看完整指标：信息条只放最关键的 4 个数，细节放 title 里
    var tip = [];
    if (state.layoutSource === 'python') tip.push('布局：Python 预计算（fr-cohesion-v1）');
    else if (state.layoutSource === 'client') tip.push('布局：浏览器本地兜底计算');
    if (typeof m.avg_degree === 'number') tip.push('平均度 ' + m.avg_degree);
    if (typeof m.max_degree === 'number') tip.push('最大度 ' + m.max_degree);
    if (typeof m.components === 'number') tip.push('连通分量 ' + m.components);
    if (typeof m.isolated === 'number') tip.push('孤立节点 ' + m.isolated);
    if (typeof m.avg_clustering === 'number') tip.push('平均聚类系数 ' + m.avg_clustering);
    if (typeof m.modularity === 'number') tip.push('模块度 Q = ' + m.modularity);
    if (m.diameter_from && m.diameter_to) {
      tip.push('直径端点：' + String(m.diameter_from).split('/').pop()
        + ' ↔ ' + String(m.diameter_to).split('/').pop());
    }
    el.title = tip.join('\n');
  }

  /* ── 图例（分组 chip）───────────────────────────────────────────────── */
  /**
   * 分组顺序：优先遵循 payload.groups（生成器给的顺序，root 在最前），
   * 未在 groups[] 里登记的组追加到末尾 —— 与数据源保持一致，不自行重排。
   */
  function groupKeys() {
    var seen = {}, out = [];
    Object.keys(state.byId).forEach(function (id) {
      var g = state.byId[id].group;
      if (!seen[g]) { seen[g] = 1; out.push(g); }
    });
    var declared = (state.data && Array.isArray(state.data.groups)) ? state.data.groups : null;
    if (!declared || !declared.length) {
      // 无 groups[] 时：root 优先，其余按名字排序（保证 root 在最前）
      out.sort(function (a, b) {
        if (a === 'root') return -1;
        if (b === 'root') return 1;
        return a < b ? -1 : (a > b ? 1 : 0);
      });
      return out;
    }
    var ordered = [];
    declared.forEach(function (g) {
      var k = (g && typeof g === 'object') ? String(g.key || '') : String(g || '');
      if (k && seen[k] && ordered.indexOf(k) < 0) ordered.push(k);
    });
    out.forEach(function (k) { if (ordered.indexOf(k) < 0) ordered.push(k); });
    return ordered;
  }

  function groupColor(group) {
    if (group === 'root') return GOLD;       // 根目录/入口：香槟金强调
    var dirs = groupKeys().filter(function (g) { return g !== 'root'; });
    var i = dirs.indexOf(group);
    return PALETTE[(i < 0 ? 0 : i) % PALETTE.length];
  }

  /* ── 光效工具（v7）──────────────────────────────────────────────────────
     目的：让节点呈"受光的玻璃球"而不是纯色圆点，并让核心节点自然泛光。
     原则：所有光效只改观感，不改语义 ——
       · 分组色相仍由 groupColor 决定（球面渐变只是同一色相的明暗变化）
       · 核心节点的金色仍是 GOLD，不因为加光而偏色
       · 度数越高光晕越强，这是真实结构的可视化，不是随机装饰 */
  function hexToRgb(hex) {
    var h = String(hex || '').replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    if (h.length !== 6) return null;
    var n = parseInt(h, 16);
    if (!isFinite(n)) return null;
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function mixRgb(rgb, target, amount) {
    var a = Math.max(0, Math.min(1, amount));
    return [
      Math.round(rgb[0] + (target[0] - rgb[0]) * a),
      Math.round(rgb[1] + (target[1] - rgb[1]) * a),
      Math.round(rgb[2] + (target[2] - rgb[2]) * a)
    ];
  }

  function rgbaOf(rgb, alpha) {
    return 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',' + alpha + ')';
  }

  /** 球面渐变：左上受光、右下入影，中间保持原色相。
      v10 收窄明暗对比：原来的 0.58/0.55 会做出"塑料球"，现在压到 0.34/0.40，
      呈现哑光金属质感，与参考站的克制风格一致。 */
  function sphereGradient(hex) {
    var c = hexToRgb(hex);
    if (!c) return hex;
    if (!(window.echarts && echarts.graphic && echarts.graphic.RadialGradient)) return hex;
    var hi = mixRgb(c, [255, 244, 228], 0.34);
    var lo = mixRgb(c, [6, 6, 8], 0.42);
    return new echarts.graphic.RadialGradient(0.36, 0.30, 0.92, [
      { offset: 0, color: rgbaOf(hi, 1) },
      { offset: 0.46, color: rgbaOf(c, 1) },
      { offset: 1, color: rgbaOf(lo, 1) }
    ]);
  }

  function rimColor(hex) {
    var c = hexToRgb(hex);
    if (!c) return hex;
    return rgbaOf(mixRgb(c, [231, 225, 213], 0.55), 0.55);
  }

  function glowColor(hex, alpha) {
    var c = hexToRgb(hex);
    if (!c) return 'transparent';
    return rgbaOf(c, alpha);
  }

  /* ── 精密球体符号（v27 视觉内核）────────────────────────────────────────
     原生 circle 只有「一层填充 + 一条描边」，放大后就是个发光的糖球，撑不起
     「仪器 / 星图」的语言。这里把每个节点合成一张内联 SVG：
       ① 盘面：左上受光的径向渐变（高光偏移，不是居中泛光）
       ② 镜圈：1px 硬边亮环 —— 让边缘锐利，这是「精密」的来源
       ③ 刻度环：外圈 12 格刻度，每 3 格加长（只给度数 ≥3 的节点，叶子保持安静）
       ④ 高光弧：左上 190°→248° 的一段短弧，模拟玻璃/金属镜面反光
       ⑤ 枢纽内环 / 桥接虚线环 / 缺失红色环，三种结构角色各有一个识别符号
     为什么用 image symbol 而不是再加一个 series：筛选与聚焦都会重写 series[0]
     的 data，多 series 必然出现「盘面动了、刻度没动」的错位；合成到同一图元里
     就没有同步问题，且 SVG 是矢量的，缩放后仍然锐利。 */
  var ORB_CACHE = {};

  function polar(cx, cy, r, deg) {
    var a = deg * Math.PI / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  }

  /* 颜色解析：节点色可能来自两处 —— groupColor() 的 #hex，或 depthFadeHex() 的
     rgba(r,g,b,a)。原先直接用 hexToRgb 解析，遇到 rgba 一律返回 null 并回落到灰色
     [150,150,150] —— 表现就是「所有分组节点都变成灰白球，分组色全丢」。
     这里统一成 [r,g,b]，两条来源都能正确取色。 */
  function parseRgb(v) {
    var s = String(v == null ? '' : v).trim();
    var m = s.match(/^rgba?\(([^)]+)\)$/i);
    if (m) {
      var p = m[1].split(',');
      var r = parseFloat(p[0]), g = parseFloat(p[1]), b = parseFloat(p[2]);
      if (isFinite(r) && isFinite(g) && isFinite(b)) return [r | 0, g | 0, b | 0];
      return null;
    }
    return hexToRgb(s);
  }

  function arcPath(cx, cy, r, a0, a1) {
    var p0 = polar(cx, cy, r, a0), p1 = polar(cx, cy, r, a1);
    var large = Math.abs(a1 - a0) > 180 ? 1 : 0;
    return 'M' + p0[0].toFixed(1) + ' ' + p0[1].toFixed(1)
      + ' A' + r + ' ' + r + ' 0 ' + large + ' 1 ' + p1[0].toFixed(1) + ' ' + p1[1].toFixed(1);
  }

  function orbSvg(colorHex, deg, imp, flags) {
    var c = parseRgb(colorHex) || [150, 150, 150];
    var key = colorHex + '|' + Math.min(deg, 9) + '|' + Math.round(imp * 10)
      + '|' + (flags.missing ? 'm' : '') + (flags.bridge ? 'b' : '') + (flags.core ? 'c' : '')
      + '|g' + (flags.glow || 0)
      + '|io' + (flags.inDeg | 0) + '-' + (flags.outDeg | 0);
    if (ORB_CACHE[key]) return ORB_CACHE[key];

    var CX = 60, CY = 60, RD = 39;
    var hi = rgbaOf(mixRgb(c, [255, 247, 233], 0.40), 1);
    var mid = rgbaOf(c, 1);
    var lo = rgbaOf(mixRgb(c, [5, 5, 7], 0.58), 1);
    var rim = flags.missing ? 'rgba(239,68,68,.9)' : 'rgba(242,237,228,.52)';
    // 枢纽的引用环已经承担了外圈，bezel 要收着画，否则双环在最密处会打结
    var bezel = flags.bridge ? 'rgba(214,176,132,.40)'
      : (flags.core ? 'rgba(214,176,132,.34)' : 'rgba(198,168,130,.18)');
    var bezelDash = flags.bridge || flags.missing ? ' stroke-dasharray="3 3"' : '';

    var s = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">'
      + '<defs>'
      + '<radialGradient id="b" cx="35%" cy="29%" r="86%">'
      + '<stop offset="0" stop-color="' + hi + '"/>'
      + '<stop offset=".10" stop-color="' + mid + '"/>'
      + '<stop offset=".72" stop-color="' + mid + '"/>'
      + '<stop offset=".94" stop-color="' + lo + '"/>'
      + '<stop offset="1" stop-color="' + rgbaOf(mixRgb(c, [5, 5, 7], 0.74), 1) + '"/>'
      + '</radialGradient>';

    // 光晕只给重要节点：中心不透明到边缘透明，压在原盘面之下，只露出外沿一圈
    var haloOn = imp >= 0.55 && !flags.missing && (flags.glow || 0) > 0;
    if (haloOn) {
      var g2 = flags.core ? [214, 176, 132] : c;
      var hA = 0.09 + 0.055 * (flags.glow || 1);
      s += '<radialGradient id="h" cx="50%" cy="50%" r="50%">'
        + '<stop offset=".60" stop-color="' + rgbaOf(g2, hA) + '"/>'
        + '<stop offset="1" stop-color="' + rgbaOf(g2, 0) + '"/>'
        + '</radialGradient>';
    }
    s += '</defs>';

    if (haloOn) {
      s += '<circle cx="' + CX + '" cy="' + CY + '" r="' + (flags.glow >= 2 ? 59 : 56) + '" fill="url(#h)"/>';
    }

    // 外圈刻度环：12 格在 20px 尺度上会糊成一圈毛边，改成 8 格；
    // 只有真正的枢纽（度数 ≥5）才画刻度，叶子只留一圈细环保持安静。
    s += '<circle cx="' + CX + '" cy="' + CY + '" r="54" fill="none" stroke="' + bezel
      + '" stroke-width="1"' + bezelDash + '/>';
    /* 引用环（v27）：度数 ≥8 的枢纽不再画刻度，改画一圈「引用平衡环」——
       金色弧 = 该文档主动引用别人（出链）的占比，冷灰弧 = 被别人引用（反链）的占比。
       这是把图例里的「枢纽」从模糊的大小暗示，升级成一眼可读的结构指标：
       满金 = 资料汇编型文档，满灰 = 被广泛依赖的规范/入口。 */
    var totalRefs = (flags.inDeg | 0) + (flags.outDeg | 0);
    if (totalRefs >= 8) {
      var outShare = Math.max(0, Math.min(1, (flags.outDeg | 0) / totalRefs));
      var aStart = -90, aEnd = 270, gap = 3.2;
      var aOut = aStart + (360 - gap * 2) * outShare;
      if (outShare > 0.02) {
        s += '<path d="' + arcPath(CX, CY, 47.5, aStart, aOut) + '" fill="none" stroke="rgba(216,180,136,.88)"'
          + ' stroke-width="2.2" stroke-linecap="round"/>';
      }
      if (outShare < 0.98) {
        s += '<path d="' + arcPath(CX, CY, 47.5, aOut + gap, aEnd) + '" fill="none" stroke="rgba(233,228,216,.34)"'
          + ' stroke-width="2.2" stroke-linecap="round"/>';
      }
    }
    if (deg >= 5 && totalRefs < 8) {
      for (var i = 0; i < 8; i++) {
        var major = (i % 2 === 0);
        var a = i * 45 + 22.5;
        var p1 = polar(CX, CY, major ? 46 : 49, a);
        var p2 = polar(CX, CY, 53, a);
        s += '<line x1="' + p1[0].toFixed(1) + '" y1="' + p1[1].toFixed(1)
          + '" x2="' + p2[0].toFixed(1) + '" y2="' + p2[1].toFixed(1)
          + '" stroke="rgba(214,176,132,' + (major ? 0.34 : 0.16) + ')" stroke-width="1"/>';
      }
    }

    // 盘面 + 硬边镜圈
    s += '<circle cx="' + CX + '" cy="' + CY + '" r="' + RD + '" fill="url(#b)"/>';
    s += '<circle cx="' + CX + '" cy="' + CY + '" r="' + (RD - 0.5) + '" fill="none" stroke="'
      + rim + '" stroke-width="1.1"/>';

    // 高光弧（190°→248°，落在左上）
    var a1 = polar(CX, CY, RD - 11, 190), a2 = polar(CX, CY, RD - 11, 248);
    s += '<path d="M' + a1[0].toFixed(1) + ' ' + a1[1].toFixed(1)
      + ' A' + (RD - 11) + ' ' + (RD - 11) + ' 0 0 1 ' + a2[0].toFixed(1) + ' ' + a2[1].toFixed(1)
      + '" fill="none" stroke="rgba(255,252,244,.40)" stroke-width="1.5" stroke-linecap="round"/>';

    // 枢纽内环：核心/高重要度节点加一层机芯，暗示「可展开」
    if (flags.core || imp >= 0.75) {
      s += '<circle cx="' + CX + '" cy="' + CY + '" r="24" fill="none" stroke="rgba(214,176,132,.34)"'
        + ' stroke-width="1" stroke-dasharray="1 5" stroke-linecap="round"/>';
    }
    s += '</svg>';

    var url = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(s);
    ORB_CACHE[key] = url;
    return url;
  }

  function groupCount(group) {
    var c = 0;
    Object.keys(state.byId).forEach(function (id) { if (state.byId[id].group === group) c++; });
    return c;
  }

  function renderChips() {
    var box = $('dg-chips');
    if (!box) return;
    var keys = groupKeys();
    var html = '<button class="dg-chip" type="button" data-dg-group="" aria-pressed="true" title="显示全部（点击分组可高亮单组）">'
      + '<span class="dg-sw" style="background:' + GOLD + '"></span>全部<span class="dg-n">' + Object.keys(state.byId).length + '</span></button>';
    keys.forEach(function (g, i) {
      var color = groupColor(g, i);
      var label = (Object.keys(state.byId).map(function (id) { return state.byId[id]; })
        .filter(function (n) { return n.group === g; })[0] || {}).group_label || g;
      html += '<button class="dg-chip' + (g === 'root' ? ' is-gold' : '') + '" type="button" data-dg-group="' + esc(g) + '"'
        + ' aria-pressed="false" title="高亮分组：' + esc(label) + '">'
        + '<span class="dg-sw" style="background:' + color + '"></span>' + esc(label)
        + '<span class="dg-n">' + groupCount(g) + '</span></button>';
    });
    box.innerHTML = html;
    box.querySelectorAll('[data-dg-group]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var g = btn.getAttribute('data-dg-group') || '';
        state.group = (state.group === g) ? '' : g;
        syncChips();
        applyFilter();
        renderCrumb();
      });
    });
  }

  function syncChips() {
    var box = $('dg-chips');
    if (!box) return;
    box.querySelectorAll('[data-dg-group]').forEach(function (btn) {
      var g = btn.getAttribute('data-dg-group') || '';
      btn.setAttribute('aria-pressed', String(g === state.group));
    });
  }

  /* ── 标签分层 ──────────────────────────────────────────────────────────
     全量常显会糊成一片，全不显又让人认不出哪个点是哪篇文档。规则（数据驱动）：
       ① 必显：核心节点 —— group==='root' 或 kind ∈ {entry,readme,index}
       ② 必显：度数（in+out）最高的前 N 个枢纽，N 随规模自适应
       ③ 其余：hover / 选中时显示
       ④ 窄屏（≤900px）：全部退回 hover-only
     坐标固定（layout:'none'），加标签不改变任何几何，验收要求 maxMove=0。

     N 取 `clamp(⌈节点数 × 0.40⌉, LABEL_HUB_TOP_N, LABEL_HUB_TOP_MAX)`：
       · 早期固定 5 的问题：31 个节点里只显示 8 个名字，大部分圆点是"无名球"，
         必须逐个悬停才知道是什么，图谱退化成装饰；
       · 但也不能全显：布局改成紧凑分区后，同一分区内节点间距只有 ~120px，
         标签会互相压住。9 是实测「覆盖够用 + 不叠字」的折中。
     重叠仍由 labelLayout 的 moveOverlap/hideOverlap 兜底，但那是安全网不是策略。 */
  var LABEL_HUB_TOP_N = 4;    // 下限：小图也要有若干枢纽可认
  var LABEL_HUB_TOP_MAX = 9;  // 上限：超过这个数标签开始互相压住
  var LABEL_HUB_RATIO = 0.40;

  function isNarrowViewport() {
    try { return window.matchMedia('(max-width: 900px)').matches; } catch (e) { return false; }
  }

  function computeLabelSet() {
    var core = [], coreMap = {}, hubMap = {}, alwaysMap = {};
    var ids = Object.keys(state.byId);
    ids.forEach(function (id) {
      var n = state.byId[id];
      if (n.group === 'root' || n.kind === 'entry' || n.kind === 'readme' || n.kind === 'index') {
        core.push(id);
        coreMap[id] = 1;
      }
    });
    // 枢纽：按度数降序取前 N（度数相同按 id 稳定排序，保证可复现）
    var rest = ids.filter(function (id) { return !coreMap[id]; });
    rest.sort(function (a, b) {
      var da = state.degree[a] || 0, db = state.degree[b] || 0;
      if (db !== da) return db - da;
      return a < b ? -1 : (a > b ? 1 : 0);
    });
    // 枢纽数量自适应：clamp(⌈total × 0.40⌉, 4, 9)，与上方常量保持一致
    var total = ids.length;
    var topN = Math.max(LABEL_HUB_TOP_N,
      Math.min(LABEL_HUB_TOP_MAX, Math.ceil(total * themeLabelRatio())));
    var hubs = rest.slice(0, topN);
    hubs.forEach(function (id) { hubMap[id] = 1; });
    core.forEach(function (id) { alwaysMap[id] = 1; });
    hubs.forEach(function (id) { alwaysMap[id] = 1; });
    state.labelCore = coreMap;
    state.labelHub = hubMap;
    state.labelAlways = alwaysMap;
    state.narrow = isNarrowViewport();
  }

  /* ── 标签文本（v4）：文件名优先 + 机械保证两两互异 ────────────────────
     教训（v3 缺陷）：曾用 title 做标签，理由是「中文标题信息量大于文件名」。
     但本仓文档标题共享重度前缀，截断到 16 字后直接撞车：
       openbb-dsa-integration-design.md → "OpenBB × DailySt…"
       openbb-dsa-integration-prd.md    → "OpenBB × DailySt…"   ← 一字不差
     另有 "DAPENG Alpha · 策/数/系…" 三条只差末字。
     两个标签同字，等于标签失效，比不显示更误导。
     现改为确定性规则，不依赖「选哪个字段」的直觉：
       ① 基线：路径末段去扩展名（basename）
       ② 撞名：逐级加父目录前缀，直到全局唯一
          （README.md 与 docs/00-strategy/README.md 只差目录 → "README" / "00-strategy/README"；
           这是通用延长逻辑，不是特判这一对）
       ③ 截断：超过 LABEL_MAX_CHARS 截断；截断后若再次撞车，回退到未截断形式
          （文件名区别通常在尾部，截断恰好会把区别切掉，所以不能只截不查）
     title 不参与标签，仍用于 tooltip / 右侧分析面板（那里空间够、不截断）。 */
  var LABEL_MAX_CHARS = 20;

  /** 中段省略：头部定位 + 尾部区分，中间用 … 代替。
   *  比尾部截断更不容易把「唯一的区别」切掉，长度严格不超过 max。 */
  function middleEllipsis(s, max) {
    var str = String(s == null ? '' : s);
    if (str.length <= max) return str;
    var tail = Math.min(11, Math.floor(max * 0.45));   // 尾部多留一点，差异通常在结尾
    var head = max - tail - 1;                          // -1 给省略号
    if (head < 4) return str.slice(0, max - 1) + '…';
    return str.slice(0, head) + '…' + str.slice(str.length - tail);
  }

  function stemOf(part) {
    return String(part || '').replace(/\.(md|markdown|html?)$/i, '');
  }

  /** 计算 id -> 标签文本 的确定性映射；返回值保证同一数据集内两两互异 */
  function computeLabelTexts() {
    var ids = Object.keys(state.byId);
    var parts = {};
    var depth = {}; // 0 = 仅 basename；每 +1 多带一层父目录
    ids.forEach(function (id) {
      parts[id] = String(id).split('/');
      depth[id] = 0;
    });

    function candidate(id) {
      var segs = parts[id] || [];
      if (!segs.length) return '';
      var take = Math.min(depth[id] + 1, segs.length);
      var tail = segs.slice(segs.length - take);
      return tail.map(function (s, i) {
        return i === take - 1 ? stemOf(s) : s; // 只有末段去扩展名
      }).join('/');
    }

    // ② 撞名则逐级加父目录，直到没有可加（或已唯一）
    for (var guard = 0; guard < 12; guard++) {
      var bucket = {};
      ids.forEach(function (id) {
        var c = candidate(id);
        (bucket[c] || (bucket[c] = [])).push(id);
      });
      var dup = false;
      Object.keys(bucket).forEach(function (c) {
        if (bucket[c].length < 2) return;
        dup = true;
        bucket[c].forEach(function (id) {
          if (depth[id] + 1 < parts[id].length) depth[id] += 1;
        });
      });
      if (!dup) break;
    }

    var raw = {};
    ids.forEach(function (id) { raw[id] = candidate(id); });

    // ③ 截断，然后对截断结果再查一遍重，撞车者回退到未截断形式
    //
    // v25：由「尾部截断」改为「中段省略」。
    // 原实现 slice(0, MAX) + '…' 会把区分度最高的尾部直接切掉，得到
    //   incremental-prd-v54-sidebar-pro → "incremental-prd-v54-side…"
    //   incremental-prd-tab-admin     → "incremental-prd-tab-adm…"
    // 两条都还长得很像，而它们唯一的区别（sidebar-pro / tab-admin）恰恰在尾部。
    // 本文件 v4 的教训已经写明「文件名区别通常在尾部」，这里与之对齐：
    // 保留头部定位 + 尾部区分，中间省略。
    var out = {};
    ids.forEach(function (id) { out[id] = middleEllipsis(raw[id], LABEL_MAX_CHARS); });
    for (var pass = 0; pass < 6; pass++) {
      var byText = {};
      ids.forEach(function (id) { (byText[out[id]] || (byText[out[id]] = [])).push(id); });
      var clash = false;
      Object.keys(byText).forEach(function (t) {
        if (byText[t].length < 2) return;
        clash = true;
        byText[t].forEach(function (id) { out[id] = raw[id]; });
      });
      if (!clash) break;
    }
    return out;
  }

  /**
   * 取某节点的标签文本。
   * 文本由 computeLabelTexts() 一次性算好并存入 state.labelText（保证两两互异）；
   * 这里只做读取 + 兜底：映射缺失时退回文件名，绝不让标签渲染成 undefined / 空串。
   */
  function labelText(n) {
    if (!n) return '';
    var t = state.labelText && state.labelText[n.id];
    if (t) return t;
    var nm = String(n.name || n.id || '');
    return nm.replace(/\.(md|markdown|html?)$/i, '') || nm;
  }

  /* ── 粒子流动（v5）─────────────────────────────────────────────────────
     需求：「图谱面板设计太过单一，可增添粒子动态效果 UI 设计，要求能够正确的
     链接功能间的数据传导」。

     ★ 正确性边界（本模块最重要的约束：宁可少画，也不让动画说谎）
       · type='link'      有真实方向 —— A 的正文引用了 B，数据从 A 流向 B。
                          粒子沿 source → target 流动，方向即真实引用方向。
       · type='data_link' **无方向** —— 两篇文档被登记在同一导航分组，
                          谁也不是谁的来源。因此这类边**不产生流动**，
                          只画原地明灭的静态点，避免用动画暗示一个不存在的方向。
       · 端点坐标来自 chart.convertToPixel（与缩放/平移同源），不用自己推算，
                          所以 roam 之后粒子仍贴着线；取不到就不画，绝不按错误位置硬画。
       · 活性严格跟随真实交互状态：hover > 选中 > 分组/搜索筛选 > 全部。
                          目的就是回答「此刻这条链路上的数据从哪来、到哪去」。

     ★ 性能与无障碍
       · prefers-reduced-motion → 完全不启动 RAF，也不画粒子（纯静态）
       · 页面隐藏 / 不在图谱视图 / 用户手动关闭 → 暂停
       · 总粒子数有硬上限，超出按比例摊薄
       · 粒子画在独立的 overlay canvas 上，不触发 ECharts 重绘
         （验收项「静置 10s rendered delta = 0」因此不受影响） */
  var PARTICLE_TOTAL_MAX = 260;
  var PARTICLE_PER_EDGE_MAX = 4;

  var particles = {
    on: true,
    raf: 0,
    canvas: null,
    ctx: null,
    dpr: 1,
    w: 0,
    h: 0,
    segs: [],
    t0: 0,
    last: 0,
    hover: '',
    ok: true,
    why: '',
    labelRects: [],
    syncTimer: 0
  };

  /* v10：粒子改为「暖金细尘」——颗粒更小、更暗、拖尾更短。
     参考站的金色只做稀疏点缀，密集的亮色光点会立刻显廉价。 */
  function particleAccent() { return GOLD; }
  function particleMuted() { return 'rgba(231,225,213,.55)'; }

  /** 当前是否应当运行动画（任一条不满足就静止，并说明原因） */
  function particlesAllowed() {
    if (!particles.on) { particles.why = 'user-off'; return false; }
    if (themeMotion() === 0) { particles.why = 'motion-off'; return false; }
    if (isReducedMotion()) { particles.why = 'reduced-motion'; return false; }
    if (document.hidden) { particles.why = 'hidden'; return false; }
    if (currentView() !== 'graph') { particles.why = 'not-graph-view'; return false; }
    var card = $('dg-canvas-card');
    if (card && card.hidden) { particles.why = 'card-hidden'; return false; }
    if (!particles.ok) { particles.why = particles.why || 'unsupported'; return false; }
    return true;
  }

  /** 节点屏幕坐标：优先 convertToPixel，退回 coordinateSystem.dataToPoint */
  function nodePixel(chart, pos) {
    if (!chart || !pos) return null;
    var p = null;
    try { p = chart.convertToPixel({ seriesIndex: 0 }, pos); } catch (e) { p = null; }
    if (!p || !isFinite(p[0]) || !isFinite(p[1])) {
      try {
        var sm = chart.getModel().getSeriesByIndex(0);
        var view = sm && sm.coordinateSystem;
        if (view && typeof view.dataToPoint === 'function') p = view.dataToPoint(pos);
      } catch (e) { p = null; }
    }
    if (!p || !isFinite(p[0]) || !isFinite(p[1])) return null;
    return [p[0], p[1]];
  }

  /* ── 缩放读数（仪器框右上角）────────────────────────────────────────────
     为什么不用 series.zoom：graph 的 roam 缩放改的是视图变换，getOption().zoom
     始终是 1，读不到真实倍数。这里改用「两点间像素距离 / 布局距离」反解比例，
     再除以首次渲染时记录的比例尺，得到相对初始视角的倍数（1.00 = 默认视角）。
     取横向跨度最大的两个节点，分母足够大，滚轮缩放时读数不会跳。 */
  function zoomMetrics() {
    var chart = state.chart;
    if (!chart || chart.isDisposed()) return null;
    var ids = Object.keys(state.pos || {});
    if (ids.length < 2) return null;
    var bi = ids[0], bj = ids[1], best = -1;
    for (var i = 0; i < ids.length; i++) {
      for (var j = i + 1; j < ids.length; j++) {
        var d = Math.abs(state.pos[ids[i]][0] - state.pos[ids[j]][0]);
        if (d > best) { best = d; bi = ids[i]; bj = ids[j]; }
      }
    }
    if (!(best > 0)) return null;
    var a = nodePixel(chart, state.pos[bi]), b = nodePixel(chart, state.pos[bj]);
    if (!a || !b) return null;
    var k = Math.hypot(b[0] - a[0], b[1] - a[1]) / best;
    if (!(k > 0) || !isFinite(k)) return null;
    if (!state.zoomBase) {
      // 画幅还在自动收敛：此时还没有"默认视角"可参照，先按 1.00 显示
      if (!state._fitSettled) return { z: 1, k: k };
      state.zoomBase = k;
    }
    return { z: k / state.zoomBase, k: k };
  }

  function updateHud() {
    var val = $('dg-zoom-val');
    if (!val) return;
    var m = zoomMetrics();
    var z = m ? m.z : 1;
    if (!isFinite(z) || z <= 0) z = 1;
    val.textContent = '×' + (z < 10 ? z.toFixed(2) : z.toFixed(1));
    var fill = $('dg-zoom-fill');
    if (fill) {
      // 0.40×–2.60× 映射到 0–100%，条形到 55% 处正好是 1.00×
      var pct = Math.max(2, Math.min(100, ((z - 0.4) / 2.2) * 100));
      fill.style.width = pct.toFixed(1) + '%';
    }
  }

  function hash01(str) {
    var h = 2166136261;
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = (h * 16777619) >>> 0; }
    return (h % 997) / 997;
  }

  /**
   * 采集当前标签的屏幕矩形（含 padding）。
   * 粒子层在 ECharts 画布之上，若不做避让，青色光点会把 10.5px 的标签打成麻点 ——
   * 实测截图中 "openbb-dsa-integration-p…" 直接被点成 "o0p-e0nbbsa-tra…"，
   * 等于动画把文字毁掉了。所以粒子要避开标签区域。
   */
  function collectLabelRects() {
    var out = [];
    try {
      var chart = state.chart;
      if (!chart || chart.isDisposed()) return out;
      var list = chart.getZr().storage.getDisplayList(false) || [];
      var pad = 3;
      for (var i = 0; i < list.length; i++) {
        var el = list[i];
        if (!el || el.type !== 'tspan') continue;
        var s = el.style || {};
        if (!s.text) continue;
        if (el.ignore || el.invisible) continue;
        var bb = el.getBoundingRect();
        if (!bb || !bb.width) continue;
        var m = el.getComputedTransform && el.getComputedTransform();
        var x = bb.x, y = bb.y;
        if (m) { x = m[0] * bb.x + m[2] * bb.y + m[4]; y = m[1] * bb.x + m[3] * bb.y + m[5]; }
        if (!isFinite(x) || !isFinite(y)) continue;
        out.push([x - pad, y - pad, x + bb.width + pad, y + bb.height + pad]);
      }
    } catch (e) { out = []; }
    return out;
  }

  function inAnyRect(x, y) {
    var r = particles.labelRects;
    for (var i = 0; i < r.length; i++) {
      if (x >= r[i][0] && x <= r[i][2] && y >= r[i][1] && y <= r[i][3]) return true;
    }
    return false;
  }

  /** 构建/刷新线段（含端点像素坐标）。画布尺寸或漫游变化时都要重来。 */
  function particlesSync() {
    var chart = state.chart;
    var el = $('dg-canvas');
    var cv = particles.canvas || $('dg-particles');
    if (!chart || chart.isDisposed() || !el || !cv) return;
    particles.canvas = cv;
    particles.ctx = cv.getContext('2d');

    var w = el.clientWidth, h = el.clientHeight;
    if (!w || !h) return;
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    if (particles.w !== w || particles.h !== h || particles.dpr !== dpr) {
      particles.w = w; particles.h = h; particles.dpr = dpr;
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
      cv.style.width = w + 'px';
      cv.style.height = h + 'px';
    }

    var rawLinks = (state.data && state.data.links) || [];
    var segs = [], missing = 0;
    for (var i = 0; i < rawLinks.length; i++) {
      var l = rawLinks[i];
      var s = String(l.source), t = String(l.target);
      var ps = state.pos[s], pt = state.pos[t];
      if (!ps || !pt) { missing += 1; continue; }
      var a = nodePixel(chart, ps), b = nodePixel(chart, pt);
      if (!a || !b) { missing += 1; continue; }
      segs.push({
        a: a, b: b,
        type: String(l.type || 'link'),
        src: s, tgt: t,
        active: true,
        phase: hash01(s + '>' + t),
        speed: 0.00016 + hash01(t + '<' + s) * 0.00010,   // 每条边速度稳定且互不相同
        n: 0
      });
    }
    // 一条都算不出坐标 -> 不要立刻判死刑。
    // 之前踩过：一次异常的同步（图表处于中间状态）会把 ok 永久置 false，之后再也恢复不了。
    // 更隐蔽的一个坑：先把空数组赋给 particles.segs 再 return，等于「保住了空数组」，
    // 旧线段照样被冲掉 —— 所以赋值必须在守卫之内。
    if (segs.length) {
      particles.segs = segs;
      particles.labelRects = collectLabelRects();
      particles.ok = true;
      particles.why = '';
    } else if (rawLinks.length && particles.segs.length) {
      // 保留上一轮线段继续跑（端点可能略有偏差，好过整层消失），下次同步再校正
      particles.why = 'sync-empty-retry';
      return;
    } else {
      particles.segs = segs;
      particles.labelRects = collectLabelRects();
      if (rawLinks.length) {
        particles.ok = false;
        particles.why = 'no-pixel-coord';
      }
    }
    particlesActive();
  }

  /** 依据 hover / 选中 / 筛选，决定每条边是否「通流」 */
  function particlesActive() {
    var segs = particles.segs;
    if (!segs.length) return;
    var focusId = particles.hover || state.selected || '';
    var lit = state.litNodes || null;
    var filtering = !!(state.group || (state.keyword && state.keyword.trim()));
    var maxEdge = PARTICLE_PER_EDGE_MAX;

    var activeCount = 0;
    for (var i = 0; i < segs.length; i++) {
      var s = segs[i];
      var on = true;
      if (focusId) {
        on = (s.src === focusId || s.tgt === focusId);
      } else if (filtering && lit) {
        on = !!(lit[s.src] && lit[s.tgt]);
      }
      s.active = on;
      if (on) activeCount += 1;
    }
    // 摊薄：活跃边太多时降低每条边的粒子数，保证总量不超上限
    var per = activeCount > 0 ? Math.floor(PARTICLE_TOTAL_MAX / activeCount) : 0;
    if (per < 1) per = 1;
    if (per > maxEdge) per = maxEdge;
    for (var j = 0; j < segs.length; j++) {
      // 无方向的登记关联只画 2 个静止点，不参与「流动」配额
      segs[j].n = segs[j].active ? (segs[j].type === 'data_link' ? 2 : per) : 0;
    }

    // 核心节点的像素位置：用于画呼吸光晕（常驻，弱于聚焦光环）
    particles.corePx = [];
    if (state.labelCore && state.chart && !state.narrow) {
      var ci = 0;
      Object.keys(state.labelCore).forEach(function (cid) {
        var ps = state.pos[cid];
        if (!ps) return;
        var pp = nodePixel(state.chart, ps);
        if (pp) particles.corePx.push({ p: pp, phase: ci * 2.1 });
        ci += 1;
      });
    }

    // 聚焦节点的像素位置：用于画脉冲光环（选中/悬停时才有）
    particles.focusPx = null;
    if (focusId && state.chart && state.pos[focusId]) {
      var fp = nodePixel(state.chart, state.pos[focusId]);
      if (fp) particles.focusPx = fp;
    }
  }

  function particlesClear() {
    if (!particles.ctx || !particles.w) return;
    particles.ctx.setTransform(1, 0, 0, 1, 0, 0);
    particles.ctx.clearRect(0, 0, particles.canvas.width, particles.canvas.height);
  }

  function drawParticle(x, y, r, alpha, color) {
    var ctx = particles.ctx;
    ctx.globalAlpha = Math.max(0, Math.min(1, alpha));
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }

  function particleFrame(ts) {
    particles.raf = 0;
    if (!particlesAllowed()) { particlesClear(); return; }
    if (!particles.t0) particles.t0 = ts;
    var t = ts - particles.t0;
    particles.last = ts;

    var ctx = particles.ctx;
    if (!ctx || !particles.w) { return; }
    ctx.setTransform(particles.dpr, 0, 0, particles.dpr, 0, 0);
    ctx.clearRect(0, 0, particles.w, particles.h);

    var dirColor = particleAccent();
    var undirColor = particleMuted();
    // 聚焦（hover/选中）时整条链路加强：半径 ×1.25、透明度 ×1.3。
    // 让"我正在看这条链路"有可见的强度差，而不是只靠旁边的点变暗来暗示。
    var boost = (particles.hover || state.selected) ? 1.25 : 1;
    var segs = particles.segs;

    for (var i = 0; i < segs.length; i++) {
      var s = segs[i];
      if (!s.active || !s.n) continue;
      var ax = s.a[0], ay = s.a[1], bx = s.b[0], by = s.b[1];

      if (s.type === 'data_link') {
        // 无方向：两个固定分数位的静态点，只做极缓慢的明暗呼吸。
        // 不移动 = 不暗示「从谁流向谁」。
        var breathe = 0.5 + 0.5 * Math.sin(t * 0.0012 + s.phase * 6.283);
        for (var u = 1; u <= s.n; u++) {
          var fu = u / (s.n + 1);
          var ux = ax + (bx - ax) * fu, uy = ay + (by - ay) * fu;
          if (inAnyRect(ux, uy)) continue;          // 避开标签，别把文字打成麻点
          drawParticle(ux, uy, 1.1, 0.16 + 0.18 * breathe, undirColor);
        }
        continue;
      }

      // 有方向：source -> target
      for (var k = 0; k < s.n; k++) {
        var p = (s.phase + k / s.n + t * s.speed) % 1;
        if (p < 0) p += 1;
        var x = ax + (bx - ax) * p;
        var y = ay + (by - ay) * p;
        // 两端淡入淡出，避免粒子在节点符号上突然出现/消失
        var fade = Math.sin(Math.PI * p);
        var a = 0.16 + 0.72 * fade * fade;
        if (inAnyRect(x, y)) continue;              // 避开标签，别把文字打成麻点
        // 拖尾：沿运动反方向留 2 个衰减样本，让「流向」看得出来
        // （只画头就看不出方向；拖尾比箭头更轻，也不与 edgeSymbol 的箭头语义冲突）
        // 拖尾：只留 1 个衰减样本，形成极短的"行进痕迹"，不做彗星尾
        for (var q = 1; q >= 1; q--) {
          var pq = p - q * 0.014;
          if (pq < 0) continue;
          var tx = ax + (bx - ax) * pq;
          var ty = ay + (by - ay) * pq;
          if (inAnyRect(tx, ty)) continue;
          drawParticle(tx, ty, 1.0, a * 0.18, dirColor);
        }
        drawParticle(x, y, 1.7 * boost, a * 0.13 * boost, dirColor);   // 极弱外晕
        drawParticle(x, y, 0.95 * boost, a * 0.92, dirColor);          // 细核心
      }
    }
    ctx.globalAlpha = 1;

    // 核心节点呼吸光晕：两圈错相位外扩，周期 ~3.2s。
    // 只给 3 个核心节点 —— 它是"心跳"，不是氛围灯；给所有节点加就变成噪音。
    if (particles.corePx && particles.corePx.length) {
      for (var ci2 = 0; ci2 < particles.corePx.length; ci2++) {
        var cp = particles.corePx[ci2];
        for (var ck = 0; ck < 2; ck++) {
          var cq = ((t / 3200) + cp.phase / 6.283 + ck * 0.5) % 1;
          var cr = 12 + cq * 22;
          ctx.globalAlpha = 0.18 * (1 - cq) * (1 - cq) * themeMotion();
          ctx.strokeStyle = GOLD;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.arc(cp.p[0], cp.p[1], cr, 0, Math.PI * 2);
          ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;
    }

    // 聚焦光环：慢速、细线、只两圈，取消中心填充光斑（雾感会毁掉"精密"观感）
    if (particles.focusPx) {
      var fx = particles.focusPx[0], fy = particles.focusPx[1];
      var PERIOD = 2600;
      ctx.lineWidth = 1;
      for (var ring = 0; ring < 2; ring++) {
        var ph = ((t + ring * (PERIOD / 2)) % PERIOD) / PERIOD;
        var rr = 12 + ph * 26;
        ctx.globalAlpha = 0.34 * (1 - ph) * (1 - ph);
        ctx.strokeStyle = dirColor;
        ctx.beginPath();
        ctx.arc(fx, fy, rr, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    particles.raf = window.requestAnimationFrame(particleFrame);
  }

  function particlesStart() {
    if (particles.raf) return;
    if (!particlesAllowed()) { particlesClear(); return; }
    particles.t0 = 0;
    particles.raf = window.requestAnimationFrame(particleFrame);
  }

  function particlesStop() {
    if (particles.raf) {
      try { window.cancelAnimationFrame(particles.raf); } catch (e) {}
      particles.raf = 0;
    }
    particlesClear();
  }

  function particlesRefresh() {
    particlesSync();
    if (particlesAllowed()) particlesStart();
    else particlesStop();
  }

  /** 节流版：漫游/缩放会高频触发 rendered，不允许每帧都重算 78 条边 */
  function particlesRefreshSoon() {
    clearTimeout(particles.syncTimer);
    particles.syncTimer = setTimeout(function () { try { particlesRefresh(); } catch (e) {} }, 120);
  }

  function updateParticleToggle() {
    var btn = $('dg-particles-toggle');
    if (!btn) return;
    var reduced = isReducedMotion();
    var on = particles.on && !reduced;
    btn.setAttribute('aria-pressed', String(on));
    btn.classList.toggle('is-on', on);
    btn.disabled = reduced;
    if (reduced) {
      btn.title = '已按系统「减少动态效果」偏好关闭粒子动画';
    } else if (!particles.ok) {
      btn.title = '当前无法取得节点像素坐标（' + (particles.why || '未知') + '），粒子已停用';
    } else {
      btn.title = on ? '关闭粒子流动' : '开启动画：沿引用方向流动（登记关联不流动）';
    }
  }

  function particlesInit() {
    var cv = $('dg-particles');
    if (!cv) return;
    particles.canvas = cv;
    particles.ctx = cv.getContext('2d');

    try {
      var saved = window.localStorage ? localStorage.getItem('dg.particles') : null;
      if (saved === '0') particles.on = false;
      if (saved === '1') particles.on = true;
    } catch (e) {}

    var btn = $('dg-particles-toggle');
    if (btn) {
      btn.addEventListener('click', function () {
        if (isReducedMotion()) return;
        particles.on = !particles.on;
        try { if (window.localStorage) localStorage.setItem('dg.particles', particles.on ? '1' : '0'); } catch (e) {}
        updateParticleToggle();
        if (particles.on) particlesRefresh(); else particlesStop();
      });
    }
    updateParticleToggle();

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) particlesStop();
      else if (particlesAllowed()) particlesRefresh();
    });
    try {
      var mq = window.matchMedia('(prefers-reduced-motion: reduce)');
      var onMq = function () { updateParticleToggle(); if (particlesAllowed()) particlesRefresh(); else particlesStop(); };
      if (mq.addEventListener) mq.addEventListener('change', onMq);
      else if (mq.addListener) mq.addListener(onMq);
    } catch (e) {}
  }

  /* ── 大气层（v7）────────────────────────────────────────────────────────
     画布背景不再是纯黑：星尘 + 缓慢漂移的极光光斑 + 暗角。
     为什么单独一层 canvas 而不是给 ECharts 设 backgroundColor：
       ECharts 的背景是整体填充，做不出「随尺寸自适应、暗角居中、光斑漂移」的效果，
       而且每次重绘都会重画背景，反而更贵。
     实现要点：
       · 静态部分（底色渐变 + 星尘 + 暗角）预渲染到离屏 canvas，每帧只 drawImage
       · 动态部分只有 3 个光斑，用缓存的径向渐变 + 平移，避免每帧新建渐变对象
       · 帧率主动限到 ~24fps（背景漂移很慢，看不出差别，但省一半绘制）
       · prefers-reduced-motion → 只画一次静态结果，不启动 RAF */
  var ATMO_FPS_MS = 42;

  var atmo = {
    canvas: null, ctx: null, base: null, baseCtx: null,
    raf: 0, w: 0, h: 0, dpr: 1, t0: 0, last: 0,
    rings: null, cx: null, cy: null, R: 0, ok: false
  };

  /** 确定性伪随机（同一尺寸每次星尘分布一致，避免每次进图星星乱跳） */
  function seeded(n) {
    var s = n >>> 0;
    return function () {
      s = (s * 1664525 + 1013904223) >>> 0;
      return s / 4294967296;
    };
  }

  function atmoBuildBase() {
    var w = atmo.w, h = atmo.h;
    if (!w || !h) return;
    var cv = document.createElement('canvas');
    cv.width = Math.round(w * atmo.dpr);
    cv.height = Math.round(h * atmo.dpr);
    var c = cv.getContext('2d');
    c.setTransform(atmo.dpr, 0, 0, atmo.dpr, 0, 0);

    // 1) 底色：中性近黑（#070708），只为承托线条，不带任何色相倾向
    var g = c.createRadialGradient(w * 0.5, h * 0.48, 0, w * 0.5, h * 0.48, Math.max(w, h) * 0.80);
    g.addColorStop(0, 'rgba(11,11,12,1)');
    g.addColorStop(0.46, 'rgba(7,7,8,1)');
    g.addColorStop(1, 'rgba(4,4,5,1)');
    c.fillStyle = g;
    c.fillRect(0, 0, w, h);

    // 2) 星尘：数量比上一版减半、亮度更低，只做"空气感"，不参与视觉竞争
    var rnd = seeded(20261021);
    var dust = Math.round((w * h) / 17000);
    for (var i = 0; i < dust; i++) {
      var x = rnd() * w, y = rnd() * h;
      var edge = Math.min(x / w, 1 - x / w, y / h, 1 - y / h);
      var a = 0.03 + rnd() * 0.13 * Math.min(1, edge * 2.2);
      var r = 0.4 + rnd() * 0.9;
      c.globalAlpha = a;
      c.fillStyle = rnd() > 0.78 ? '#D6B084' : '#E7E1D5';
      c.beginPath();
      c.arc(x, y, r, 0, Math.PI * 2);
      c.fill();
    }
    c.globalAlpha = 1;

    // 3) 暗角：比上一版更强，让边缘彻底沉下去（参考站的边缘几乎是纯黑）
    var v = c.createRadialGradient(w * 0.5, h * 0.5, Math.min(w, h) * 0.20, w * 0.5, h * 0.5, Math.max(w, h) * 0.70);
    v.addColorStop(0, 'rgba(0,0,0,0)');
    v.addColorStop(0.55, 'rgba(0,0,0,.34)');
    v.addColorStop(1, 'rgba(0,0,0,.88)');
    c.fillStyle = v;
    c.fillRect(0, 0, w, h);

    atmo.base = cv;
    atmo.baseCtx = c;
  }

  /* 仪器环：参考站的核心识别元素 —— 同心细圆 + 径向刻度 + 稀疏金色主刻度。
     它们不是装饰性光斑，而是"测量框架"：给散点图一个几何参照，视觉上立刻从
     「一坨散点」变成「一张星图」。三环转速两慢一快且正反交错（最慢 95 秒/圈），
     肉眼只在长时间注视时察觉在转，避免抢注意力。 */
  /** 节点云的像素重心。
      为什么需要：环原本画在画布正中，但力导向结果的质心并不在正中（实测偏左约 40px），
      于是「仪器框」和「被测对象」错位，构图看着别扭。环必须以云的重心为圆心。 */
  function cloudCenterPx() {
    var chart = state.chart;
    if (!chart || chart.isDisposed() || !state.pos) return null;
    var sx = 0, sy = 0, n = 0;
    var ids = Object.keys(state.pos);
    for (var i = 0; i < ids.length; i++) {
      var p = nodePixel(chart, state.pos[ids[i]]);
      if (p) { sx += p[0]; sy += p[1]; n += 1; }
    }
    if (!n) return null;
    return [sx / n, sy / n];
  }

  /* 节点云的画布包围盒（含 zoom/center 变换后的真实位置）。
     仪器环要以「被测对象」为基准，而不是以画布短边为基准 ——
     否则宽画幅下环会横着撑到画布两端，变成几条穿过空白的弧线。 */
  function cloudBBoxPx() {
    var chart = state.chart;
    if (!chart || chart.isDisposed()) return null;
    var ids = Object.keys(state.pos || {});
    if (!ids.length) return null;
    var minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9, n = 0;
    for (var i = 0; i < ids.length; i++) {
      var p = nodePixel(chart, state.pos[ids[i]]);
      if (!p) continue;
      if (p[0] < minX) minX = p[0];
      if (p[0] > maxX) maxX = p[0];
      if (p[1] < minY) minY = p[1];
      if (p[1] > maxY) maxY = p[1];
      n += 1;
    }
    if (n < 2) return null;
    return {
      cx: (minX + maxX) / 2, cy: (minY + maxY) / 2,
      w: maxX - minX, h: maxY - minY
    };
  }

  function atmoBuildRings() {
    var w = atmo.w, h = atmo.h;
    if (!w || !h) return;
    var bb = cloudBBoxPx();
    var c = bb ? [bb.cx, bb.cy] : cloudCenterPx();
    atmo.cx = c ? c[0] : w * 0.5;
    atmo.cy = c ? c[1] : h * 0.5;
    // 环半径跟着「被测对象」走，而不是画布：取节点到云心的 95 分位半径 +12% 余量，
    // 再受画布短边约束（圆环必须完整可见）。这样无论缩放/窗口怎么变，
    // 仪器框始终刚好包住散点，不会出现"点跑到框外"的断裂感。
    // 环半径不再试图"包住云团"：画布 2.18:1，圆形几何上包不住横向拉开的云团，
    // 反复缩放只会让点更挤。改为略大于短边的量测背景，允许上下轻微出血 ——
    // 这与参考站「大尺度仪器被画幅裁切」的语言一致，也更像背景网格而非容器。
    /* 环半径改为「贴着节点云」：外环 ≈ 云的半宽/半高 × 1.22。
       画幅再宽，环也不会横穿两侧空白 —— 取景器始终对准被测对象。 */
    var baseHalfH = bb ? Math.max(80, bb.h / 2) : Math.min(w, h) * 0.42;
    var baseHalfW = bb ? Math.max(80, bb.w / 2) : Math.min(w, h) * 0.42;
    var R = Math.max(120, Math.min(baseHalfH * 1.22, h * 0.46));
    atmo.R = R;
    // v25：环改为椭圆。
    // 点云在 v25 改成椭圆分布（横向铺到画布 60% 宽）之后，正圆环只能包住中间一段，
    // 左右两端的节点会落在环外，"仪器框包住被测对象"的构图语言就断了。
    // 这里按画布宽高比给横向拉伸系数，环与点云同形；窄屏（≈1:1）自动退回正圆。
    atmo.rxScale = Math.max(0.75, Math.min(1.9, (baseHalfW * 1.26) / R));
    // 窄屏减少环与刻度数量，避免小画布上刻度过密变成毛边
    var narrow = w < 900;
    /* 环透明度整体再压一档（v27）：环是「坐标系」，不是主体。
       之前 0.20/0.26 的环在节点密度高的位置会和连线抢对比度，看着像划痕。 */
    atmo.rings = [
      { r: R * 0.34, ticks: narrow ? 18 : 28, tickLen: 3, alpha: 0.070, speed: 0.0000132 },
      { r: R * 0.60, ticks: narrow ? 28 : 44, tickLen: 4, alpha: 0.130, speed: 0.0000105 },
      { r: R * 0.81, ticks: narrow ? 36 : 56, tickLen: 5, alpha: 0.170, speed: -0.0000072 },
      { r: R * 1.00, ticks: narrow ? 44 : 72, tickLen: 2, alpha: 0.090, speed: 0.0000048 }
    ];
  }

  /* ── 分组凸包（离线算好，这里只做投影与填充）─────────────────────────
     作用：让 7 个文档分组在画布上有"领地"，读者一眼能看出团簇边界，
     而不是面对一堆等权重的点。多边形由 Python 侧 convex_hull 生成，
     前端不算几何，只把「布局坐标」线性映射到「画布像素」——
     映射系数从两个节点反解（ECharts 视图是等比缩放，x 比例即全图比例），
     因此 roam / 缩放后凸包依然贴合节点。 */
  function hullMapper() {
    var chart = state.chart;
    var hulls = state.hulls;
    if (!chart || chart.isDisposed() || !hulls || !hulls.length) return null;
    var ids = Object.keys(state.pos || {});
    if (ids.length < 2) return null;
    var bi = null, bj = null, bestDx = -1;
    for (var i = 0; i < ids.length; i++) {
      for (var j = i + 1; j < ids.length; j++) {
        var d = Math.abs(state.pos[ids[i]][0] - state.pos[ids[j]][0]);
        if (d > bestDx) { bestDx = d; bi = ids[i]; bj = ids[j]; }
      }
    }
    if (bestDx < 1) return null;
    var a = nodePixel(chart, state.pos[bi]);
    var b = nodePixel(chart, state.pos[bj]);
    if (!a || !b) return null;
    var sx = (b[0] - a[0]) / (state.pos[bj][0] - state.pos[bi][0]);
    if (!isFinite(sx) || sx === 0) return null;
    var ax = state.pos[bi][0], ay = state.pos[bi][1];
    return function (x, y) {
      return [a[0] + (x - ax) * sx, a[1] + (y - ay) * sx];
    };
  }

  function drawHulls(ctx) {
    var map = hullMapper();
    if (!map) return;
    var comms = state.communities || [];
    var hulls = state.hulls || [];
    var w = atmo.w || 0;
    var ccx = w / 2, ccy = (atmo.h || 0) / 2;

    for (var i = 0; i < hulls.length; i++) {
      var pts = (hulls[i] && hulls[i].points) || [];
      if (pts.length < 3) continue;
      /* 社区对象的字段有两套来源，必须都兼容：
         · docs-graph.json（标签传播）：{top_group, label, size, members}
         · docs-graph-layout.json（离线布局）：{group, name, size, members} */
      var c = comms[hulls[i].community] || {};
      var grp = c.top_group || c.group || '';
      var active = !!state.group && grp === state.group;

      // 1) 投影到像素，并求中心 / 包围半径
      var px = [], cx = 0, cy = 0;
      for (var m = 0; m < pts.length; m++) {
        var p = map(pts[m][0], pts[m][1]);
        px.push(p); cx += p[0]; cy += p[1];
      }
      cx /= px.length; cy /= px.length;
      var spread = 0;
      for (var k = 0; k < px.length; k++) {
        var d = Math.hypot(px[k][0] - cx, px[k][1] - cy);
        if (d > spread) spread = d;
      }
      if (!(spread > 8)) continue;

      /* 2) 领地 = 一团柔光，而不是多边形边界。
         为什么放弃凸包轮廓：这份布局是应力优化的全局解，社区之间本来就互相咬合，
         凸包会生成数条横穿整幅画的大斜线，看着像乱画的辅助线而不是领地。
         柔光只在视觉上把「这一片是同一组」托出来，不主张精确边界；
         边界只在选中该分组时才画 —— 那时它才有「框选」的语义。 */
      var g = ctx.createRadialGradient(cx, cy, spread * 0.15, cx, cy, spread * 1.02);
      var a0 = active ? 0.16 : 0.075;
      g.addColorStop(0, 'rgba(214,176,132,' + a0 + ')');
      g.addColorStop(0.62, 'rgba(214,176,132,' + (a0 * 0.42).toFixed(3) + ')');
      g.addColorStop(1, 'rgba(214,176,132,0)');
      ctx.save();
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(cx, cy, spread * 1.02, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      if (active) {
        ctx.save();
        ctx.beginPath();
        for (var n1 = 0; n1 < px.length; n1++) {
          if (n1 === 0) ctx.moveTo(px[0][0], px[0][1]); else ctx.lineTo(px[n1][0], px[n1][1]);
        }
        ctx.closePath();
        ctx.strokeStyle = 'rgba(214,176,132,.40)';
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.restore();
      }

      /* 3) 领地铭牌：沿「画布中心 → 社区中心」方向外推到团的边缘，
         让标签落在团与团之间的空隙里，不压节点。 */
      var label = c.label || c.name || GROUP_LABEL_FALLBACK[grp] || '';
      if (!label || w < 900) continue;
      var dx = cx - ccx, dy = cy - ccy;
      var dl = Math.hypot(dx, dy) || 1;
      var lx = cx + (dx / dl) * spread * 0.66;
      var ly = cy + (dy / dl) * spread * 0.66;
      ctx.save();
      ctx.font = '700 10px "SF Mono", Consolas, monospace';
      try { ctx.letterSpacing = '2.2px'; } catch (e) {}
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      // 先描一圈深色边，再填字：标签压在连线/节点上也读得清
      ctx.lineWidth = 3.2;
      ctx.strokeStyle = 'rgba(5,5,7,.86)';
      ctx.strokeText(label, lx, ly);
      ctx.fillStyle = active ? 'rgba(242,236,225,.92)' : 'rgba(214,176,132,.58)';
      ctx.fillText(label, lx, ly);
      var tw = ctx.measureText(label).width;
      ctx.strokeStyle = active ? 'rgba(214,176,132,.55)' : 'rgba(198,168,130,.26)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(lx - tw / 2 - 16, ly);
      ctx.lineTo(lx - tw / 2 - 8, ly);
      ctx.moveTo(lx + tw / 2 + 8, ly);
      ctx.lineTo(lx + tw / 2 + 16, ly);
      ctx.stroke();
      ctx.restore();
    }
  }


  /* 星尘闪烁：位置由确定性哈希决定（同一尺寸每次一样），只让亮度缓慢起伏。
     28 个点 × 一次 arc，开销可忽略；不画则在静态帧里保持恒定亮度。 */
  function drawTwinkle(ctx, t) {
    var w = atmo.w, h = atmo.h;
    if (!w || !h) return;
    for (var i = 0; i < 28; i++) {
      var hx = hash01('tw' + i), hy = hash01('tk' + i);
      var x = hx * w, y = hy * h;
      var ph = (t / 2600) + i * 0.37;
      var a = (0.05 + 0.16 * (0.5 + 0.5 * Math.sin(ph))) * themeMotion();
      ctx.globalAlpha = a;
      ctx.fillStyle = i % 5 === 0 ? GOLD : '#E7E1D5';
      ctx.beginPath();
      ctx.arc(x, y, 0.9, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  /** 绘制三环 + 刻度 + 十字准线（t=0 时为静态成帧） */
  function drawRings(ctx, t) {
    var rings = atmo.rings, cx = atmo.cx, cy = atmo.cy;
    if (!rings || cx == null) return;
    // 环呼吸：整体透明度以 ~7 秒为周期起伏 ±15%。幅度必须小 ——
    // 大起大落会变成"闪烁"，反而廉价；这里只做到"看起来在呼吸"。
    var mf = themeMotion();
    var breathe = 1 - mf * 0.15 + mf * 0.15 * Math.sin(t / 1120);
    var sx = atmo.rxScale || 1;     // 横向拉伸系数（v25 椭圆化）
    for (var i = 0; i < rings.length; i++) {
      var rg = rings[i];
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(t * rg.speed);
      // 环体：1px 细线
      ctx.lineWidth = 1;
      ctx.strokeStyle = 'rgba(198,168,130,' + (rg.alpha * breathe) + ')';
      ctx.beginPath();
      ctx.ellipse(0, 0, rg.r * sx, rg.r, 0, 0, Math.PI * 2);
      ctx.stroke();
      // 径向刻度：每 6 个一根金色主刻度
      var n = rg.ticks, step = Math.PI * 2 / n;
      for (var k = 0; k < n; k++) {
        var major = (k % 6 === 0);
        var ang = k * step;
        var len = major ? rg.tickLen * 2.2 : rg.tickLen;
        ctx.strokeStyle = major
          ? 'rgba(214,176,132,.52)'
          : 'rgba(198,168,130,' + (rg.alpha * 0.85) + ')';
        ctx.lineWidth = major ? 1.2 : 1;
        ctx.beginPath();
        ctx.moveTo(Math.cos(ang) * (rg.r - len) * sx, Math.sin(ang) * (rg.r - len));
        ctx.lineTo(Math.cos(ang) * rg.r * sx, Math.sin(ang) * rg.r);
        ctx.stroke();
      }
      ctx.restore();
    }
    // 十字准线 + 12 条极弱的径向引导线：给散点图一个"坐标纸"式的参照，
    // 这是参考站「星图/仪器」质感的关键 —— 极暗，但能感到结构存在。
    var R = atmo.R || Math.min(atmo.w, atmo.h) * 0.52;
    ctx.strokeStyle = 'rgba(198,168,130,.055)';
    ctx.lineWidth = 1;
    for (var g2 = 0; g2 < 12; g2++) {
      var ga = (g2 * Math.PI * 2) / 12;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(ga) * R * 0.12 * sx, cy + Math.sin(ga) * R * 0.12);
      ctx.lineTo(cx + Math.cos(ga) * R * 1.02 * sx, cy + Math.sin(ga) * R * 1.02);
      ctx.stroke();
    }
    // 十字准线：只画在最外层之外，极短，制造"取景器"感
    ctx.strokeStyle = 'rgba(198,168,130,.20)';
    var arms = [[1, 0], [-1, 0], [0, 1], [0, -1]];
    for (var a = 0; a < arms.length; a++) {
      ctx.beginPath();
      ctx.moveTo(cx + arms[a][0] * (R * sx + 6), cy + arms[a][1] * (R + 6));
      ctx.lineTo(cx + arms[a][0] * (R * sx + 22), cy + arms[a][1] * (R + 22));
      ctx.stroke();
    }
    // 中心微点
    ctx.fillStyle = 'rgba(214,176,132,.55)';
    ctx.beginPath();
    ctx.arc(cx, cy, 1.4, 0, Math.PI * 2);
    ctx.fill();
  }

  /* ── 群落光环（v26）────────────────────────────────────────────────────
     社区发现（CNM 贪心模块度）在 Python 侧算好，每个社区带一组凸包顶点。
     这里把凸包映射到屏幕坐标，画一层极低透明度的填充 + 虚线描边，
     形成「星云团块」的视觉语言 —— 让「哪几篇其实是一个主题」一眼可见，
     而不用去数连线。

     实现要点：
       · 填充取该社区主导分组的颜色，alpha 0.05 左右，只做体积感、不盖节点；
       · 凸包先沿「质心 → 顶点」方向外扩 PAD 像素，否则多边形会从节点中间切过；
       · 用「顶点间中点 + 二次贝塞尔」把多边形磨成闭合圆滑曲线，
         比直线多边形柔和得多，也更像星云而不是多边形标注；
       · 描边做 7 秒周期的呼吸，静态（reduced-motion）时取中位值，依然可读。

     为什么画在背景层（ECharts 之下）而不是加 polygon series：
       polygon 会参与 ECharts 的布局包围盒计算，zoom / roam 时整个图会被重新
       框选，节点位置随之漂移；画在背景层则完全不影响坐标系。 */
  var CLUSTER_PAD = 15;

  function hullToBlobPath(ctx, pts) {
    var n = pts.length;
    if (n < 3) return false;
    // 外扩：沿「质心 → 顶点」方向推出 PAD，避免描边压到节点上
    var mx = 0, my = 0;
    for (var i = 0; i < n; i++) { mx += pts[i][0]; my += pts[i][1]; }
    mx /= n; my /= n;
    var out = [];
    for (var j = 0; j < n; j++) {
      var dx = pts[j][0] - mx, dy = pts[j][1] - my;
      var d = Math.hypot(dx, dy) || 1;
      out.push([pts[j][0] + (dx / d) * CLUSTER_PAD, pts[j][1] + (dy / d) * CLUSTER_PAD]);
    }
    // 闭合圆滑曲线：起点取最后一条边的中点，逐点以「原顶点」为控制点、下一条边中点为终点
    var mid = function (a, b) { return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]; };
    var start = mid(out[n - 1], out[0]);
    ctx.beginPath();
    ctx.moveTo(start[0], start[1]);
    for (var k = 0; k < n; k++) {
      var cur = out[k];
      var nxt = out[(k + 1) % n];
      var end = mid(cur, nxt);
      ctx.quadraticCurveTo(cur[0], cur[1], end[0], end[1]);
    }
    ctx.closePath();
    return true;
  }

  function drawClusters(ctx, t) {
    var chart = state.chart;
    var list = state.communities;
    if (!chart || chart.isDisposed() || !list || !list.length) return;
    if (currentView() !== 'graph') return;

    // 呼吸：7 秒一个周期，幅度很小（只让画面「活着」，不抢视线）
    var pulse = 0.5 + 0.5 * Math.sin(t * 0.000898);

    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      var hull = c.hull || [];
      if (hull.length < 3) continue;             // 成员太少，凸包退化，不画
      var px = [];
      for (var h = 0; h < hull.length; h++) {
        var p = nodePixel(chart, hull[h]);
        if (p) px.push(p);
      }
      if (px.length < 3) continue;

      var gi = groupKeys().indexOf(c.top_group);
      var hex = groupColor(c.top_group, gi);
      var rgb = hexToRgb(hex);
      if (!rgb) continue;

      ctx.save();
      if (!hullToBlobPath(ctx, px)) { ctx.restore(); continue; }
      // 填充：极淡，只做「团块体积」
      ctx.fillStyle = 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',' + (0.045 + 0.018 * pulse) + ')';
      ctx.fill();
      // 描边：虚线 + 呼吸，边界可读但不抢
      ctx.setLineDash([3, 5]);
      ctx.lineWidth = 1;
      ctx.strokeStyle = 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',' + (0.15 + 0.07 * pulse) + ')';
      ctx.stroke();
      ctx.restore();
    }
    ctx.setLineDash([]);
  }

  function atmoFrame(ts) {
    atmo.raf = 0;
    if (!atmo.ok || !atmo.ctx) return;
    if (currentView() !== 'graph' || document.hidden) return;

    if (!atmo.t0) atmo.t0 = ts;
    var t = ts - atmo.t0;
    // 限帧：环转得很慢，30fps 足够平滑
    if (ts - atmo.last < ATMO_FPS_MS) {
      atmo.raf = window.requestAnimationFrame(atmoFrame);
      return;
    }
    atmo.last = ts;

    var w = atmo.w, h = atmo.h;
    var ctx = atmo.ctx;
    ctx.setTransform(atmo.dpr, 0, 0, atmo.dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (atmo.base) ctx.drawImage(atmo.base, 0, 0, w, h);

    if (!atmo.rings) atmoBuildRings();
    drawHulls(ctx);          // 凸包在环之下：先有领地，再有刻度
    drawRings(ctx, t);
    drawTwinkle(ctx, t);     // 星尘闪烁：最上层的一点"空气" 
    drawClusters(ctx, t);

    atmo.raf = window.requestAnimationFrame(atmoFrame);
  }

  function atmoStop() {
    if (atmo.raf) { try { window.cancelAnimationFrame(atmo.raf); } catch (e) {} atmo.raf = 0; }
  }

  /** 静态渲染一帧（reduced-motion 或暂停时使用）：环画在初始角度，背景仍有结构 */
  function atmoPaintStatic() {
    var ctx = atmo.ctx;
    if (!ctx || !atmo.w) return;
    ctx.setTransform(atmo.dpr, 0, 0, atmo.dpr, 0, 0);
    ctx.clearRect(0, 0, atmo.w, atmo.h);
    if (atmo.base) ctx.drawImage(atmo.base, 0, 0, atmo.w, atmo.h);
    if (!atmo.rings) atmoBuildRings();
    drawHulls(ctx);
    drawRings(ctx, 0);
    drawTwinkle(ctx, 0);
    drawClusters(ctx, 0);
  }

  function atmoRefresh() {
    var el = $('dg-canvas');
    var cv = $('dg-atmo');
    if (!el || !cv) return;
    var w = el.clientWidth, h = el.clientHeight;
    if (!w || !h) return;
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var sizeChanged = (atmo.w !== w || atmo.h !== h || atmo.dpr !== dpr);
    if (sizeChanged) {
      atmo.w = w; atmo.h = h; atmo.dpr = dpr;
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
      cv.style.width = w + 'px';
      cv.style.height = h + 'px';
      atmo.rings = null;   // 尺寸变化后环半径/刻度密度都要重算
      atmoBuildBase();
    }
    atmo.ok = true;
    if (isReducedMotion() || currentView() !== 'graph' || document.hidden) {
      atmoStop();
      atmoPaintStatic();
      return;
    }
    if (!atmo.raf) {
      atmo.t0 = 0;
      atmo.last = 0;
      atmo.raf = window.requestAnimationFrame(atmoFrame);
    }
  }

  function atmoInit() {
    var cv = $('dg-atmo');
    if (!cv) return;
    atmo.canvas = cv;
    atmo.ctx = cv.getContext('2d');
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) { atmoStop(); }
      else atmoRefresh();
    });
    try {
      var mq = window.matchMedia('(prefers-reduced-motion: reduce)');
      var h2 = function () { atmoRefresh(); };
      if (mq.addEventListener) mq.addEventListener('change', h2);
      else if (mq.addListener) mq.addListener(h2);
    } catch (e) {}
    atmoRefresh();
  }

  /* ── 构建 ECharts 图 ────────────────────────────────────────────────── */
  /* ── 视觉层级（v12）：把"分档"改成"连续映射" ──────────────────────────
     问题（实测）：旧实现只有 13/18/23/28/34 五档，结果 30 个节点里 21 个落在
     23px 与 28px 两档（度数 2–5 全挤在一起），而 6 个枢纽（度数 8/10/12/15/17/21）
     全被压成同一个 34px。视觉上就是「一堆差不多大的球 + 几个大球」，没有节奏。
     改为幂函数连续映射：
       · 半径 18 → 40，指数 0.62（略低于 1，让小节点也不至于缩成点）
       · 透明度 0.62 → 1，指数 0.55：叶子后退成"远景点"，枢纽站出来
       · 颜色：度数越低越向背景色靠拢，进一步做空气透视
     三个通道同时按同一「重要度」驱动，层次才会一致，而不是各自为政。 */
  var MAX_DEG_CACHE = 1;
  function importanceOf(deg) {
    var m = MAX_DEG_CACHE > 0 ? MAX_DEG_CACHE : 1;
    var r = Math.max(0, Math.min(1, deg / m));
    return r;
  }
  function sizeOf(deg) {
    if (deg <= 0) return 13;
    var r = Math.pow(importanceOf(deg), 0.62);
    // 注意：这里只算「数据尺寸」，不乘主题缩放。
    // 主题缩放在 symbolSize 赋值处统一施加一次，避免 raw.size 路径与本地公式路径各乘一遍。
    return Math.round((16 + 20 * r) * 10) / 10;
  }
  function baseOpacityOf(deg) {
    if (deg <= 0) return 0.55;
    return Math.round((0.80 + 0.20 * Math.pow(importanceOf(deg), 0.55)) * 1000) / 1000;
  }
  /** 空气透视：越不重要越向背景色靠，避免所有点都在前台抢视线 */
  function depthFadeHex(hex, deg) {
    var c = hexToRgb(hex);
    if (!c) return hex;
    var k = (1 - Math.pow(importanceOf(deg), 0.5)) * 0.13;
    var bg = [11, 11, 12];
    return rgbaOf(mixRgb(c, bg, Math.max(0, Math.min(0.4, k))), 1);
  }

  function buildChart() {
    var el = $('dg-canvas');
    if (!el) return;
    if (typeof echarts === 'undefined') {
      showState('error', '图谱渲染引擎加载失败', '未找到本地 ECharts（<code>js/echarts.min.js</code>），请检查静态资源。', true);
      return;
    }
    if (!state.chart) {
      state.chart = echarts.init(el, null, { renderer: 'canvas' });
      state.chart.on('rendered', function () {
        state.rendered += 1;
        // 漫游/缩放/重绘后节点像素位置会变，粒子端点必须跟着重算（节流）
        if (particles.canvas) particlesRefreshSoon();
        // 缩放读数（首次 rendered 时记录基准比例尺，之后显示相对倍数）
        updateHud();
        // 构图偏小时自动补一次画幅（最多 3 次）
        if ((state._fitFixes || 0) < 3) correctFit();
        /* 群落光环画在大气层（ECharts 之下），依赖 convertToPixel 的当前变换。
           动画模式（RAF 在跑）下每帧都会重画，无需处理；
           静态模式（prefers-reduced-motion）没有 RAF，必须在这里补一帧，
           否则缩放后光环会停在旧位置、和节点错开。 */
        if (!atmo.raf) atmoPaintStatic();
      });
      // 坐标在 ingest 阶段已固定（layout:'none'），这里不再需要任何布局回调
      state.chart.on('click', function (p) {
        if (p && p.dataType === 'node' && p.data && p.data.id) selectNode(p.data.id);
      });
      // hover 节点时只让该节点的链路流动：这是「数据传导」最直接的表达
      state.chart.on('mouseover', function (p) {
        if (!p || p.dataType !== 'node' || !p.data || !p.data.id) return;
        particles.hover = p.data.id;
        particlesActive();
      });
      state.chart.on('mouseout', function (p) {
        if (!p || p.dataType !== 'node') return;
        particles.hover = '';
        particlesActive();
      });
      // 缩放/平移过程中同步右上角读数（不等 rendered，滚轮时更跟手）
      state.chart.on('georoam', function () {
        state._userRoamed = true;   // 用户接管视角后，不再自动校正画幅
        updateHud();
      });
      /* 容器尺寸监听（v27）：右侧分析面板滑出时，.dg-body 是 flex 布局，
         画布容器会从 1218px 缩到 866px，但 ECharts 只在 window.resize 时重算，
         于是画布仍按 1218 绘制、右半边被 overflow 裁掉 —— 面板一开，图就"缺一角"。
         这里改用 ResizeObserver 监听容器本身（面板开合、工具栏换行都会触发），
         并顺带按新画幅重新自适应一次（用户手动缩放过则尊重其视角）。 */
      if (typeof ResizeObserver !== 'undefined' && !state._ro) {
        state._ro = new ResizeObserver(function () {
          if (!state.chart || state.chart.isDisposed()) return;
          clearTimeout(state._roTimer);
          state._roTimer = setTimeout(function () {
            if (!state.chart || state.chart.isDisposed()) return;
            state.chart.resize();
            // 容器尺寸变了：清掉自适应计数与「用户已接管」标记，
            // 让随后的 rendered 事件用实测包围盒重新收敛一次（最多 3 步）
            state._fitFixes = 0;
            state._userRoamed = false;
            state._fitSettled = false;
            state.zoomBase = 0;
            particlesRefreshSoon();
            atmoRefresh();
            try { correctFit(); } catch (e) {}
          }, 90);
        });
        state._ro.observe(el);
      }
      window.addEventListener('resize', function () {
        if (!state.chart || state.chart.isDisposed()) return;
        state.chart.resize();
        // 画布尺寸变了：重置自适应计数，由 correctFit 按实测包围盒重新收敛
        state._fitFixes = 0;
        state._userRoamed = false;
        state._fitSettled = false;
        state.zoomBase = 0;
        try { correctFit(); } catch (e) {}
        particlesRefreshSoon();
        atmoRefresh();
        // 跨 900px 断点：标签分层策略变化 -> 重画（layout:'none'，坐标不变）
        var narrowNow = isNarrowViewport();
        if (narrowNow !== state.narrow) {
          state.narrow = narrowNow;
          clearTimeout(state._lblTimer);
          state._lblTimer = setTimeout(function () { try { paint(); } catch (e) {} }, 160);
        }
      });
    }
    paint();
    // 粒子层依赖画布尺寸与节点像素坐标，必须在首次渲染之后初始化
    particlesInit();
    particlesRefresh();
    // 大气层只依赖画布尺寸，放在最后（它是最底层，失败也不影响图）
    atmoInit();
  }

  /* ── 画幅自适应（v27）──────────────────────────────────────────────────
     ECharts 在 layout:'none' 时把节点坐标**当作像素**直接落点，不会自动铺满画布。
     实测 31 个节点只占据了 1458×740 画布的中间 40%，四周大片空白 —— 这是整张图
     「空、散、没气势」的真正原因，不是配色问题。
     这里按节点包围盒反算 zoom 与 center，让构图铺到画布的 90%/86%（留出仪器框呼吸），
     zoom 只在 layout:'none' 下生效，且不影响 state.pos（所有像素换算仍走
     convertToPixel，因此凸包、粒子、缩放读数会自动跟随）。 */
  function computeFit() {
    var el = $('dg-canvas');
    var W = (el && el.clientWidth) || 1200;
    var H = (el && el.clientHeight) || 700;
    var ids = Object.keys(state.pos || {});
    if (!ids.length) return { zoom: 1, center: null };
    var minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
    for (var i = 0; i < ids.length; i++) {
      var q = state.pos[ids[i]];
      if (q[0] < minX) minX = q[0];
      if (q[0] > maxX) maxX = q[0];
      if (q[1] < minY) minY = q[1];
      if (q[1] > maxY) maxY = q[1];
    }
    var spanX = Math.max(1, maxX - minX);
    var spanY = Math.max(1, maxY - minY);
    var z = Math.min((W * 0.90) / spanX, (H * 0.84) / spanY);
    if (!isFinite(z) || z <= 0) z = 1;
    z = Math.max(0.7, Math.min(3.2, z));
    return { zoom: z, center: [(minX + maxX) / 2, (minY + maxY) / 2] };
  }

  /* 渲染后二次校正：paint() 发生在首帧，此时画布可能还没拿到最终尺寸
     （工具栏换行、字体加载都会改变高度），computeFit 用的 W/H 就是错的，
     结果是构图缩在画布中间、四周大片空白。这里用「渲染后的真实包围盒」反算，
     偏小就把 zoom 乘上去，最多校正 3 次，避免与用户手动缩放打架。 */
  function correctFit() {
    if (!state.chart || state.chart.isDisposed()) return;
    if (state._userRoamed) { state._fitSettled = true; return; }   // 用户已接管视角
    if ((state._fitFixes || 0) >= 3) { state._fitSettled = true; return; }
    var bb = cloudBBoxPx();
    var el = $('dg-canvas');
    if (!bb || !el) return;
    var W = el.clientWidth, H = el.clientHeight;
    if (!W || !H) return;
    var cover = Math.max(bb.w / W, bb.h / H);
    if (cover >= 0.72 || cover <= 0.02) { state._fitSettled = true; return; }   // 已经铺够 / 明显异常
    var k = Math.min((W * 0.90) / bb.w, (H * 0.84) / bb.h);
    if (!isFinite(k) || k <= 1.02) return;
    var cur = 1;
    try {
      var op = state.chart.getOption();
      cur = Number(op.series[0].zoom);
      if (!isFinite(cur) || cur <= 0) cur = 1;
    } catch (e) { return; }
    var next = Math.min(3.2, cur * Math.min(k, 1.6));
    state._fitFixes = (state._fitFixes || 0) + 1;
    state.zoomBase = 0;                        // 画幅变了，读数重新按 ×1.00 起算
    state.chart.setOption({ series: [{ zoom: next }] }, { notMerge: false, lazyUpdate: false });
  }

  function applyFit(lazy) {
    if (!state.chart || state.chart.isDisposed()) return;
    // 画幅重排后，「默认视角」变了：清掉基准比例尺，读数重新从 ×1.00 起算
    state.zoomBase = 0;
    var f = computeFit();
    var series = { zoom: f.zoom };
    if (f.center) series.center = f.center;
    state.chart.setOption({ series: [series] }, { notMerge: false, lazyUpdate: lazy !== false });
  }

  /** 构造 series[0] 负载（nodes/links/categories/force 参数） */
  function seriesPayload() {
    var keys = groupKeys();
    var red = cssVar('--red', '#ff3d6a');
    var cyan = GOLD;   // v10：强调色统一为暖金（cssVar 读的是 :root，拿不到 body 作用域的重定义）
    var nodes = [];
    Object.keys(state.byId).forEach(function (id) {
      var n = state.byId[id];
      var deg = state.degree[id] || 0;
      var gi = keys.indexOf(n.group);
      var base = groupColor(n.group, gi);
      // 核心/入口：根目录文档 + 入口 + 索引（readme 为新枚举；index 保留兼容旧数据）
      var isCore = (n.group === 'root' || n.kind === 'entry' || n.kind === 'readme' || n.kind === 'index');
      // 三通道同源：核心节点用金色（不参与空气透视），其余按重要度做深度衰减
      var color = isCore ? GOLD : depthFadeHex(base, deg);
      var imp = importanceOf(deg);
      var baseOp = baseOpacityOf(deg);
      /* 尺寸口径（v26）：优先用 Python 预计算的 size。
         Python 侧用同一套幂函数（16 + 20·r^0.62，r = degree/maxDegree）算出并写进
         JSON，好处是「气泡大小 / 碰撞半径 / 指标卡」三处用同一个数，
         不会再出现「布局按 A 算、渲染按 B 算」的错位。
         字段缺失（旧产物）时退回本地 sizeOf，公式完全一致。 */
      var rawSize = n.raw && n.raw.size;
      var baseSize = (typeof rawSize === 'number' && isFinite(rawSize) && rawSize > 0)
        ? rawSize : sizeOf(deg);
      // 主题缩放只在渲染层施加一次：raw.size（Python 预计算）与本地公式都走这里，
      // 保证「数据尺寸」与「显示尺寸」解耦，切换主题不需要重算布局。
      var sz = Math.round(baseSize * themeNodeScale() * 10) / 10;
      // 桥接节点（介数进入前 18%）：它们把几个群落缝在一起，
      // 用一圈金色虚线外环单独标出来 —— 这是「结构角色」而不是「大小」的表达。
      var isBridge = !!(n.raw && n.raw.bridge);
      // v27：节点改用合成 SVG 符号。symbolSize 需要按比例放大 ——
      // SVG 的 viewBox 是 120，盘面直径只占 78（65%），不补偿会让所有节点缩水一圈。
      var orbSize = Math.round(sz * 1.42 * 10) / 10;
      var item = {
        id: id,
        name: n.title || n.name,
        value: deg,
        symbolSize: orbSize,
        symbol: 'image://' + orbSvg(color, deg, imp, {
          missing: !!n.missing, bridge: isBridge, core: isCore, glow: themeGlow(),
          inDeg: (state.inMap[id] || []).length,
          outDeg: (state.outMap[id] || []).length
        }),
        category: gi < 0 ? 0 : gi,
        _n: n,
        _baseOpacity: baseOp,
        itemStyle: {
          // 颜色/描边/光晕全部由 SVG 承担，这里只保留透明度与选中态的兜底值。
          // 注意：image symbol 上不要再加 shadowBlur —— 阴影按图片包围盒绘制，
          // 会在球体四周出现一个方形光斑，是这套视觉里最刺眼的一种脏。
          color: color,
          opacity: baseOp,
          shadowBlur: 0,
          shadowColor: 'transparent',
          shadowOffsetX: 0,
          shadowOffsetY: 0
        },
        // v3 标签分层：核心节点 + 度数 Top5 枢纽常显；其余 hover/选中时显示
        label: (function () {
          var always = !!state.labelAlways[id] && !state.narrow;
          return {
            show: always,
            position: 'right',
            distance: 6,
            formatter: labelText(n),
            // 字号/字重做两级：核心 11px/600（近白），枢纽 10px/500（暖白）。
            // 之前 8 个标签全 10.5px 只靠 700/400 区分，在 1398px 宽的画布上几乎看不出差别。
            color: state.labelCore[id] ? '#F2EDE3' : '#D8D2C6',
            fontSize: state.labelCore[id] ? 11 : 10,
            fontWeight: state.labelCore[id] ? 600 : 500,
            fontFamily: 'SF Mono, Consolas, monospace',
            // 文字描边：标签压在连线上也读得清（不依赖背景色）
            textBorderColor: 'rgba(2,5,16,.9)',
            textBorderWidth: 2.4
          };
        })(),
        emphasis: {
          label: {
            show: true, position: 'right', distance: 6,
            color: '#eef2f7', fontSize: 11,
            fontFamily: 'SF Mono, Consolas, monospace',
            textBorderColor: 'rgba(2,5,16,.9)', textBorderWidth: 2.4
          },
          itemStyle: { borderColor: cyan, borderWidth: 2 }
        }
      };
      if (state.pos[id]) { item.x = state.pos[id][0]; item.y = state.pos[id][1]; }
      nodes.push(item);
    });

    var links = [];
    var staged = [];
    (state.data.links || []).forEach(function (l) {
      var s = String(l.source || ''), t = String(l.target || '');
      if (!state.byId[s] || !state.byId[t]) return;
      var ty = String(l.type || 'link');
      staged.push({
        s: s, t: t, type: ty, dashed: ty === 'data_link',
        // 权重取「两端中较不重要的那一端」：叶子↔叶子是该沉到背景里的
        w: Math.min(state.degree[s] || 0, state.degree[t] || 0),
        line: l.line, group: l.group
      });
    });
    /* 连线分层（v27）：把边分成三段 —— 骨架 / 常规 / 纹理。
       为什么必须分层：80 条同透明度细线叠在一起，读者的眼睛找不到入口，
       整张图就退化成毛线团。前 18% 的边（两端度数都高的）抬成金色骨架，
       一眼就能看出「哪几篇文档是承重墙」。分级只影响观感，不改数据。 */
    var ranked = staged.filter(function (x) { return !x.dashed; })
      .slice().sort(function (a, b) { return b.w - a.w; });
    var tierOf = {};
    ranked.forEach(function (x, i) {
      var r = i / Math.max(1, ranked.length);
      tierOf[x.s + '\u0000' + x.t] = r < 0.18 ? 2 : (r < 0.52 ? 1 : 0);
    });
    staged.forEach(function (x) {
      var tier = x.dashed ? 0 : (tierOf[x.s + '\u0000' + x.t] || 0);
      var color, width, op;
      if (x.dashed) { color = 'rgba(231,225,213,.15)'; width = 1; op = 0.55; }
      else if (tier === 2) { color = 'rgba(216,180,136,.56)'; width = 1.4; op = 0.96; }
      else if (tier === 1) { color = 'rgba(198,168,130,.26)'; width = 1; op = 0.80; }
      else { color = 'rgba(198,168,130,.14)'; width = 1; op = 0.62; }
      // 曲率按端点 hash 取正负：同向弯曲会让往返边压成一条，看不出是两个方向
      var dir = hash01(x.s + '>' + x.t) > 0.5 ? 1 : -1;
      var curv = (x.dashed ? 0.06 : (tier === 2 ? 0.15 : 0.09)) * dir;
      links.push({
        source: x.s,
        target: x.t,
        _type: x.type,
        _baseOpacity: op,
        _baseColor: color,
        _baseWidth: width,
        _tier: tier,
        _edge: { type: x.type, line: x.line, group: x.group },
        lineStyle: {
          color: color,
          width: width,
          type: x.dashed ? 'dashed' : 'solid',
          curveness: curv,
          opacity: op,
          shadowBlur: 0,
          shadowColor: 'transparent',
          shadowOffsetX: 0,
          shadowOffsetY: 0
        }
      });
    });

    return {
      type: 'graph',
      layout: 'none',                      // 坐标由 computeLayout 固定给出，ECharts 不再跑布局
      roam: true,
      draggable: true,
      focusNodeAdjacency: false,
      // 画幅自适应：按包围盒反算 zoom/center，让构图铺满画布（详见 computeFit）
      zoom: computeFit().zoom,
      center: computeFit().center || undefined,
      // 力导向由 computeLayout 一次性算完；ECharts 侧不做任何持续布局/动画
      categories: keys.map(function (g, i) {
        var label = (Object.keys(state.byId).map(function (id) { return state.byId[id]; })
          .filter(function (n) { return n.group === g; })[0] || {}).group_label || GROUP_LABEL_FALLBACK[g] || g;
        return { name: label, itemStyle: { color: groupColor(g, i) } };
      }),
      data: nodes,
      links: links,
      lineStyle: { color: LINK_COLOR, opacity: 1 },
      // 引用方向：箭头指向被引用方（source -> target）
      edgeSymbol: ['none', 'arrow'],
      edgeSymbolSize: [0, 5],
      // 悬停时轻微放大 + 邻接聚焦：放大是纯渲染态，不改坐标（筛选零漂移的验收不受影响）
      emphasis: { focus: 'adjacency', scale: 1.06, lineStyle: { color: cyan, width: 1.4, opacity: 1 } },
      // 聚焦某节点时，非邻居只淡出到 25%（不做成几乎不可见，保留全局方位感）
      blur: { itemStyle: { opacity: 0.25 }, lineStyle: { opacity: 0.08 }, label: { show: false } },
      // 标签重叠处理：先水平挪开（position:'right' 的标签留在节点同一行），
      // 挪不开才隐藏。只用 hideOverlap 会把 1440 宽下的第 8 个标签直接吞掉，
      // 而被吞的恰是入链最多的 DATA-DICTIONARY —— 最不该藏的那个。
      labelLayout: { hideOverlap: true },
      select: { itemStyle: { borderColor: cyan, borderWidth: 2 }, label: { show: true } }
    };
  }

  /** 整幅 option（首帧用） */
  function nodeOption() {
    var cyan = GOLD;   // v10：强调色统一为暖金（cssVar 读的是 :root，拿不到 body 作用域的重定义）
    return {
      backgroundColor: 'transparent',
      animation: true,                     // 首帧 0ms，仅用于筛选时的透明度过渡（坐标固定，不会被插值移动）
      animationDuration: 0,
      animationDurationUpdate: 240,
      animationEasingUpdate: 'cubicOut',
      tooltip: {
        backgroundColor: 'rgba(6,12,28,.96)',
        borderColor: 'rgba(6,182,212,.35)',
        borderWidth: 1,
        padding: [8, 10],
        textStyle: { color: '#eef2f7', fontSize: 11 },
        formatter: function (p) {
          if (p.dataType === 'edge') {
            var ed = (p.data && p.data._edge) || {};
            var t = String(ed.type || (p.data && p.data._type) || 'link');
            var org = edgeOriginText(ed, false);
            return '<div style="font-size:11px">' + esc(t === 'data_link' ? '登记关联（虚线）' : '正文引用（实线）') + '</div>'
              + (org ? ('<div style="font-size:10px;color:#8b9ec2;margin-top:2px">' + esc(org) + '</div>') : '');
          }
          var n = (p.data && p.data._n) || {};
          var h = '<div style="font-weight:700;margin-bottom:3px">' + esc(n.title || n.name || '') + '</div>'
            + '<div style="font-family:SF Mono,Consolas,monospace;font-size:10px;color:#8b9ec2">' + esc(n.id || '') + '</div>'
            + '<div style="margin-top:4px;font-size:10.5px">分组 ' + esc(n.group_label || n.group || '—')
            + ' · ' + esc(kindLabel(n.kind))
            + ' · 度数 ' + (state.degree[n.id] || 0) + '</div>';
          if (n.missing) h += '<div style="margin-top:4px;color:' + cssVar('--red', '#ff3d6a') + '">⚠ 文件不存在（被引用但仓库内缺失）</div>';
          if ((state.degree[n.id] || 0) === 0) h += '<div style="margin-top:4px;color:#8b9ec2">孤立节点（无引用关系）</div>';
          h += '<div style="margin-top:4px;color:#3d5278;font-size:9.5px">点击查看出链 / 反链</div>';
          return h;
        }
      },
      series: [seriesPayload()]
    };
  }

  function paint() {
    if (!state.chart) return;
    state.paints += 1;
    state.chart.setOption(nodeOption(), { notMerge: true, lazyUpdate: false });
  }

  /** 读取当前渲染坐标（诊断/兜底用；主链路坐标由 computeLayout 一次给定） */
  function snapshotPositions() {
    if (!state.chart) return 0;
    var got = 0;
    try {
      var data = state.chart.getModel().getSeriesByIndex(0).getData();
      var n = data.count();
      for (var i = 0; i < n; i++) {
        var lay = data.getItemLayout(i);
        var id = data.getId ? data.getId(i) : (data.getRawDataItem(i) || {}).id;
        if (lay && lay.length >= 2 && isFinite(lay[0]) && isFinite(lay[1]) && id != null) {
          state.pos[String(id)] = [lay[0], lay[1]];
          got += 1;
        }
      }
    } catch (e) { got = 0; }
    if (got) state.settled = true;
    return got;
  }

  /* ── 筛选（分组 + 搜索）：只改透明度，不删节点 → 布局不动 ─────────── */
  function matchKeyword(n, kw) {
    if (!kw) return true;
    var k = kw.toLowerCase();
    return (n.name || '').toLowerCase().indexOf(k) >= 0
      || (n.title || '').toLowerCase().indexOf(k) >= 0
      || (n.id || '').toLowerCase().indexOf(k) >= 0;
  }

  function applyFilter() {
    if (!state.chart || !state.data) return;
    // 先清掉「节点聚焦」的 blur 状态：否则它会与筛选的透明度叠加，
    // 非匹配节点被压到几乎不可见（0.12 × 0.25），整图发黑。
    try {
      state.chart.dispatchAction({ type: 'downplay', seriesIndex: 0 });
      state.chart.dispatchAction({ type: 'unfocusNodeAdjacency', seriesIndex: 0 });
    } catch (e) {}
    // 首次（未固化坐标）先固化，避免筛选触发重新力导向
    if (!state.settled) snapshotPositions();
    var kw = state.keyword.trim();
    var g = state.group;
    var op;
    try { op = state.chart.getOption(); } catch (e) { return; }
    var series = op.series && op.series[0];
    if (!series) return;
    var nodes = series.data || [];
    var links = series.links || [];
    var metric = 0;
    var dimNodes = {}, litNodes = {};

    nodes.forEach(function (d) {
      var n = (d && d._n) ? d._n : state.byId[d.id];
      if (!n) return;
      if (state.pos[d.id]) { d.x = state.pos[d.id][0]; d.y = state.pos[d.id][1]; }
      var okGroup = !g || n.group === g;
      var okKw = matchKeyword(n, kw);
      var lit = okGroup && okKw;
      if (lit) { litNodes[d.id] = 1; metric += 1; } else { dimNodes[d.id] = 1; }
      d.itemStyle = d.itemStyle || {};
      // 滤镜态必须在「该节点本来的深度透明度」上做衰减，不能一律拉回 1 ——
      // 否则一按分组就所有叶子重新变实，之前做的空气透视全丢。
      var bOp = typeof d._baseOpacity === 'number' ? d._baseOpacity : 1;
      d.itemStyle.opacity = lit ? bOp : bOp * 0.30;
      // 筛选时：常显节点保留标签，但被淡出的那些要收起（否则淡出节点还挂着标签更乱）
      if (d.label) {
        var always = !!state.labelAlways[d.id] && !state.narrow;
        d.label.show = always && lit;
      }
    });

    links.forEach(function (l) {
      var lit = litNodes[l.source] && litNodes[l.target];
      l.lineStyle = l.lineStyle || {};
      // 同理：在边自身的深度透明度上衰减，而不是固定 1
      var bOp = typeof l._baseOpacity === 'number' ? l._baseOpacity : 1;
      l.lineStyle.opacity = lit ? bOp : bOp * 0.22;
      // 光圈聚焦会临时改写颜色/线宽，还原时必须回到分层时的基线值
      if (l._baseColor) l.lineStyle.color = l._baseColor;
      if (l._baseWidth) l.lineStyle.width = l._baseWidth;
    });

    state.paints += 1;
    // 筛选结果供粒子层复用：只有两端都被点亮的边才通流
    state.litNodes = litNodes;
    particlesActive();
    // 合并式更新：data 内已带固定 x/y + 透明度，layout 保持 none -> 布局零跳动
    state.chart.setOption({
      series: [{ data: nodes, links: links, layout: 'none' }]
    }, { notMerge: false, lazyUpdate: false });

    var info = $('dg-filter-info');
    if (info) {
      info.textContent = (g || kw)
        ? ('高亮 ' + metric + '/' + nodes.length + ' 个节点' + (kw ? ('（关键词「' + kw + '」）') : ''))
        : '';
    }
    return metric;
  }

  /* ── 选中脉冲（v27）────────────────────────────────────────────────────
     点击节点时，从该点扩散一圈金色细环（1 秒内放大 14 倍并淡出）。
     这不是装饰：读者的视线本来要「找到刚才点的那个点」，脉冲给出一个明确落点，
     同时把注意力引向随后出现的光圈聚焦与右侧面板。用 DOM 元素 + CSS 动画实现，
     不进任何 canvas 的绘制循环，零常驻开销；减少动效时直接跳过。 */
  function pingAt(id) {
    if (isReducedMotion()) return;
    var chart = state.chart;
    if (!chart || chart.isDisposed()) return;
    var p = nodePixel(chart, state.pos[id]);
    if (!p) return;
    var card = $('dg-canvas-card');
    if (!card) return;
    var el = document.createElement('i');
    el.className = 'dg-ping';
    el.setAttribute('aria-hidden', 'true');
    el.style.left = p[0] + 'px';
    el.style.top = p[1] + 'px';
    card.appendChild(el);
    window.setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 1150);
  }

  /* ── 光圈聚焦（v27）────────────────────────────────────────────────────
     选中一个节点 = 把镜头对准它。这里不是简单地把全图压暗，而是按「图距离」分级：
       0 跳（自身）1.00 / 1 跳（邻居）0.92 / 2 跳以内 0.40 / 更远 0.10
     连线只保留 1 跳内并抬成金色（0.95 / 1.6px），其余压到 0.05 ——
     链路于是从背景纹理里「浮」出来，读者看的是这一条链，而不是整张网。
     可逆：closePanel → applyFilter() 会按 _baseOpacity / _baseColor 全量还原。 */
  function applyAperture(id) {
    if (!state.chart || state.chart.isDisposed() || !state.byId[id]) return;
    if (!state.settled) snapshotPositions();
    var dist = {};
    dist[id] = 0;
    var nbr = {};
    (state.outMap[id] || []).forEach(function (e) { nbr[e.id] = 1; });
    (state.inMap[id] || []).forEach(function (e) { nbr[e.id] = 1; });
    Object.keys(nbr).forEach(function (k) { if (dist[k] === undefined) dist[k] = 1; });
    Object.keys(nbr).forEach(function (k) {
      (state.outMap[k] || []).concat(state.inMap[k] || []).forEach(function (e) {
        if (dist[e.id] === undefined) dist[e.id] = 2;
      });
    });
    var series;
    try { series = state.chart.getOption().series[0]; } catch (e) { return; }
    if (!series) return;
    var nodes = series.data || [];
    var links = series.links || [];
    nodes.forEach(function (d) {
      if (!d) return;
      var dd = dist[d.id];
      var bOp = typeof d._baseOpacity === 'number' ? d._baseOpacity : 1;
      d.itemStyle = d.itemStyle || {};
      d.itemStyle.opacity = dd === 0 ? 1
        : (dd === 1 ? Math.max(bOp, 0.92) : (dd === 2 ? 0.40 : 0.10));
      if (state.pos[d.id]) { d.x = state.pos[d.id][0]; d.y = state.pos[d.id][1]; }
      if (d.label) {
        var always = !!state.labelAlways[d.id] && !state.narrow;
        d.label.show = (dd === 0 && !state.narrow) || (dd === 1 && always);
      }
    });
    links.forEach(function (l) {
      var adj = (l.source === id || l.target === id);
      l.lineStyle = l.lineStyle || {};
      l.lineStyle.opacity = adj ? 0.95 : 0.05;
      l.lineStyle.color = adj ? 'rgba(226,190,141,.85)' : (l._baseColor || 'rgba(198,168,130,.2)');
      l.lineStyle.width = adj ? 1.6 : 1;
    });
    nbr[id] = 1;
    state.litNodes = nbr;
    state.paints += 1;
    state.chart.setOption({
      series: [{ data: nodes, links: links, layout: 'none' }]
    }, { notMerge: false, lazyUpdate: false });
    particlesActive();
  }

  function clearSearch() {
    state.keyword = '';
    var inp = $('dg-search-input');
    if (inp) inp.value = '';
    applyFilter();
    renderCrumb();
  }

  /* ── 面包屑 ─────────────────────────────────────────────────────────── */
  function renderCrumb() {
    var box = $('dg-crumb');
    if (!box) return;
    var sel = state.selected ? state.byId[state.selected] : null;
    var g = state.group;
    var gLabel = '';
    if (g) {
      var first = Object.keys(state.byId).map(function (id) { return state.byId[id]; })
        .filter(function (n) { return n.group === g; })[0];
      gLabel = (first && first.group_label) || GROUP_LABEL_FALLBACK[g] || g;
    }
    var html = '<button type="button" data-dg-crumb="all">全部</button>';
    if (gLabel) {
      html += '<span class="dg-sep">›</span><button type="button" data-dg-crumb="group">' + esc(gLabel) + '</button>';
    }
    if (sel) {
      html += '<span class="dg-sep">›</span><span class="dg-now">' + esc(sel.title || sel.name) + '</span>';
    } else if (!gLabel) {
      html += '<span class="dg-sep">›</span><span class="dg-now">全部文档</span>';
    }
    box.innerHTML = html;
    box.querySelectorAll('[data-dg-crumb]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var lvl = btn.getAttribute('data-dg-crumb');
        if (lvl === 'all') { state.group = ''; syncChips(); closePanel(); applyFilter(); renderCrumb(); }
        else if (lvl === 'group') { closePanel(); renderCrumb(); }
      });
    });
  }

  /* ── 右侧分析栏（三层交互的第③层）────────────────────────────────── */
  /**
   * 关系条目（出链/反链共用）
   * @param {Object} edge {id, type, line, group}
   * @param {boolean} isBack 是否反链（行号属于对方文件）
   */
  function relItem(edge, isBack) {
    if (!edge) return '';
    var id = edge.id;
    var n = state.byId[id];
    if (!n) return '';
    var origin = edgeOriginText(edge, isBack);
    return '<button class="dg-rel" type="button" data-dg-rel="' + esc(id) + '"'
      + ' data-dg-edge="' + esc(String(edge.type || 'link')) + '"'
      + ' title="' + esc((n.title || n.name) + (origin ? ' · ' + origin : '')) + '">'
      + '<span class="dg-rel-main">'
      +   '<span class="dg-rel-name">' + esc(n.title || n.name) + '</span>'
      +   '<span class="dg-rel-dir">' + esc(n.group_label || n.group || '') + '</span>'
      + '</span>'
      + (origin ? ('<span class="dg-rel-origin">' + esc(origin) + '</span>') : '')   // 拿不到出处就不渲染这一行
      + '</button>';
  }

  function selectNode(id) {
    var n = state.byId[id];
    if (!n) return;
    state.selected = id;
    // 选中即「聚焦一条链路」：粒子只在这条链路上流动
    particlesActive();
    pingAt(id);
    var panel = $('dg-panel');
    if (!panel) return;
    var outs = state.outMap[id] || [];
    var ins = state.inMap[id] || [];
    var red = cssVar('--red', '#ff3d6a');
    var html = ''
      + '<div class="dg-panel-hd">'
      +   '<div class="dg-panel-title">' + esc(n.title || n.name) + '</div>'
      +   '<button class="dg-panel-close" type="button" id="dg-panel-close" aria-label="关闭（Esc）">✕</button>'
      + '</div>'
      + '<div class="dg-path"><span>' + esc(n.id) + '</span>'
      +   '<button class="dg-copy" type="button" id="dg-copy-path" title="复制仓库路径">复制</button></div>'
      + '<div class="dg-kv">'
      +   '<span class="k">分组</span><span class="v is-gold">' + esc(n.group_label || n.group) + '</span>'
      +   '<span class="k">类型</span><span class="v">' + esc(kindLabel(n.kind)) + '</span>'
      +   '<span class="k">大小</span><span class="v">' + fmtBytes(n.bytes) + ' · ' + (n.lines || 0) + ' 行</span>'
      +   '<span class="k">更新</span><span class="v">' + esc(n.updated || '—') + '</span>'
      +   '<span class="k">被引用</span><span class="v">' + ins.length + ' 条</span>'
      +   '<span class="k">引用了</span><span class="v">' + outs.length + ' 条</span>'
      +   (n.missing ? ('<span class="k">状态</span><span class="v is-red">文件不存在（被引用但缺失）</span>') : '')
      + '</div>'
      + (n.summary ? ('<div class="dg-summary">' + esc(n.summary) + '</div>') : '<div class="dg-summary">（该文档没有可提取的首段摘要）</div>')
      + '<div class="dg-rel-hd">被引用（反链）<b>' + ins.length + '</b></div>'
      + '<div class="dg-rel-list">' + (ins.length
          ? ins.map(function (e) { return relItem(e, true); }).join('')
          : '<div class="dg-rel-empty">没有其它文档引用它</div>') + '</div>'
      + '<div class="dg-rel-hd">引用了（出链）<b>' + outs.length + '</b></div>'
      + '<div class="dg-rel-list">' + (outs.length
          ? outs.map(function (e) { return relItem(e, false); }).join('')
          : '<div class="dg-rel-empty">它没有引用其它文档</div>') + '</div>'
      + sysLinkBlock(id)
      + '<div class="dg-actions">'
      +   '<button class="dg-btn is-primary" type="button" id="dg-focus">聚焦此节点</button>'
      +   '<button class="dg-btn" type="button" id="dg-copy-md">复制文档链接</button>'
      + '</div>'
      + '<div class="dg-note">仓库内的 <code>docs/</code> 目录未对外提供 HTTP 访问，故此处不放置「打开源文件」假链接；'
      +   '用上面的「复制」按钮取仓库相对路径，在本地编辑器打开。</div>';

    panel.innerHTML = html;
    panel.hidden = false;
    panel.classList.remove('is-leaving');
    panel.setAttribute('aria-hidden', 'false');

    // 高亮选中节点：光圈聚焦（按图距离分级压暗，链路浮出）
    try {
      state.chart.dispatchAction({ type: 'downplay', seriesIndex: 0 });
      applyAperture(id);
    } catch (e) {}

    var closeBtn = $('dg-panel-close');
    if (closeBtn) closeBtn.addEventListener('click', function () { closePanel(); });
    var copyPath = $('dg-copy-path');
    if (copyPath) copyPath.addEventListener('click', function () {
      copyText(n.id, copyPath);
    });
    var copyMd = $('dg-copy-md');
    if (copyMd) copyMd.addEventListener('click', function () {
      copyText('[' + (n.title || n.name) + '](' + n.id + ')', copyMd);
    });
    var focusBtn = $('dg-focus');
    if (focusBtn) focusBtn.addEventListener('click', function () {
      try { state.chart.dispatchAction({ type: 'focusNodeAdjacency', seriesIndex: 0, dataIndex: findDataIndex(id) }); } catch (e) {}
    });
    panel.querySelectorAll('[data-dg-rel]').forEach(function (b) {
      b.addEventListener('click', function () { selectNode(b.getAttribute('data-dg-rel')); });
    });

    renderCrumb();
  }

  function findDataIndex(id) {
    try {
      var data = state.chart.getOption().series[0].data || [];
      for (var i = 0; i < data.length; i++) { if (data[i] && data[i].id === id) return i; }
    } catch (e) {}
    return undefined;
  }

  function copyText(text, btn) {
    var done = function (ok) {
      if (!btn) return;
      var old = btn.textContent;
      btn.textContent = ok ? '已复制' : '复制失败';
      setTimeout(function () { btn.textContent = old; }, 1200);
    };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
        return;
      }
    } catch (e) {}
    try {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      done(!!ok);
    } catch (e2) { done(false); }
  }

  function closePanel(instant) {
    var panel = $('dg-panel');
    if (!panel || panel.hidden) { state.selected = ''; particlesActive(); return; }
    state.selected = '';
    // 取消选中后恢复「全部链路通流」，并按基线还原被光圈压暗的节点与连线
    particlesActive();
    try {
      if (state.chart) {
        state.chart.dispatchAction({ type: 'downplay', seriesIndex: 0 });
        state.chart.dispatchAction({ type: 'unfocusNodeAdjacency', seriesIndex: 0 });
      }
    } catch (e) {}
    try { applyFilter(); } catch (e) {}
    if (instant === true || isReducedMotion()) {
      panel.hidden = true;
      panel.classList.remove('is-leaving');
      panel.setAttribute('aria-hidden', 'true');
    } else {
      panel.classList.add('is-leaving');
      setTimeout(function () {
        panel.hidden = true;
        panel.classList.remove('is-leaving');
        panel.setAttribute('aria-hidden', 'true');
      }, 180);
    }
    renderCrumb();
  }

  /* ── 首次进入初始化 ─────────────────────────────────────────────────── */
  function initGraph() {
    if (state.ready) {
      // 二次进入：容器尺寸可能变化，触发一次 resize
      setTimeout(function () { try { state.chart && state.chart.resize(); } catch (e) {} }, 30);
      return;
    }
    state.ready = true;
    wireToolbar();
    loadData(false);
  }

  function wireToolbar() {
    var inp = $('dg-search-input');
    if (inp) {
      inp.addEventListener('input', function () {
        state.keyword = inp.value || '';
        applyFilter();
        renderCrumb();
      });
      inp.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter') {
          ev.preventDefault();
          var kw = (inp.value || '').trim().toLowerCase();
          if (!kw) return;
          var hit = Object.keys(state.byId).map(function (id) { return state.byId[id]; })
            .filter(function (n) { return matchKeyword(n, kw); })[0];
          if (hit) selectNode(hit.id);
        } else if (ev.key === 'Escape') {
          clearSearch();
          inp.blur();
        }
      });
    }
    var resetBtn = $('dg-reset');
    if (resetBtn) resetBtn.addEventListener('click', function () {
      state.group = '';
      state.keyword = '';
      if (inp) inp.value = '';
      syncChips();
      closePanel();
      applyFilter();
      renderCrumb();
      try { state.chart && state.chart.dispatchAction({ type: 'restore' }); } catch (e) {}
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Escape') return;
      if (currentView() !== 'graph') return;
      var panel = $('dg-panel');
      if (panel && !panel.hidden) { closePanel(); ev.preventDefault(); }
    });
  }

  /** 支持 #graph 直达 + #graph=docs/xx.md 选中 */
  function applyHashSelection() {
    var h = String(location.hash || '');
    var m = h.match(/^#graph(?:=(.+))?$/);
    if (!m) return;
    if (m[1]) {
      var id = decodeURIComponent(m[1]);
      if (state.byId[id]) selectNode(id);
    }
  }

  /* ── 启动：绑定视图切换（列表默认）───────────────────────────────── */
  /**
   * 预取离线布局（Python SMACOF 结果）。失败静默 —— 拿不到就用前端力导向，
   * 这是「增强」不是「依赖」。节点集合对不上时同样拒绝使用，绝不拿错位坐标硬画。
   */
  function prefetchOfflineLayout() {
    try {
      fetch('docs-graph-layout.json', { cache: 'no-cache' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (doc) {
          if (!doc || !doc.nodes) return;
          state.layoutDoc = doc;
          /* 竞态修复（v27）：布局 JSON（6.8KB）和图谱 JSON（47KB）是两条并发请求，
             若图谱先到，ingest() 时 layoutDoc 还是 null → applyOfflineLayout() 返回 false
             → state.hulls 永远为空 → 群落地图（凸包 + 领地铭牌）一帧都不会画。
             这是「领地铭牌看不见」的真正原因，不是绘制逻辑的问题。
             处理：布局后到时，若图已建好且节点集完全一致，就把坐标/凸包补上并重绘一次。 */
          if (!state.chart || !state.data) return;
          if (!applyOfflineLayout()) return;
          try { paint(); } catch (e) {}
          atmoRefresh();
          particlesRefreshSoon();
        })
        .catch(function () {});
    } catch (e) {}
  }

  /** 用离线布局覆盖前端坐标；校验节点集合完全一致才采纳 */
  function applyOfflineLayout() {
    var doc = state.layoutDoc;
    if (!doc || !doc.nodes) return false;
    var ids = Object.keys(state.byId);
    for (var i = 0; i < ids.length; i++) {
      if (!doc.nodes[ids[i]]) return false;   // 数据变了 -> 整份布局作废
    }
    var pos = {};
    ids.forEach(function (id) {
      var p = doc.nodes[id];
      pos[id] = [Number(p[0]), Number(p[1])];
    });
    state.pos = pos;
    state.hulls = Array.isArray(doc.hulls) ? doc.hulls : null;
    state.communities = Array.isArray(doc.communities) ? doc.communities : null;
    state.layoutSource = 'offline';
    return true;
  }

  /* ── 显示设置（主题的可视化入口）──────────────────────────────────────
     三个维度 + 光晕强度，改动即时生效并存 localStorage。
     只影响观感：不重算布局、不改数据语义，所以不会破坏 maxMove=0 这条验收。 */
  var SETTING_GROUPS = [
    { k: 'motion', t: '动效', opts: [['off', '关'], ['soft', '弱'], ['normal', '标准']] },
    { k: 'labels', t: '标签', opts: [['low', '少'], ['normal', '标准'], ['high', '多']] },
    { k: 'nodes', t: '节点', opts: [['compact', '紧凑'], ['normal', '标准'], ['large', '舒展']] },
    { k: 'glow', t: '光晕', opts: [[0, '关'], [1, '标准'], [2, '强']] }
  ];

  function renderSettings() {
    var box = $('dg-settings');
    if (!box) return;
    box.innerHTML = SETTING_GROUPS.map(function (g) {
      return '<div class="dg-set-row"><span class="dg-set-label">' + g.t + '</span>'
        + '<span class="dg-set-opts">'
        + g.opts.map(function (o) {
            var on = String(theme[g.k]) === String(o[0]);
            return '<button type="button" class="dg-set-opt' + (on ? ' is-on' : '')
              + '" data-dg-set="' + g.k + '" data-val="' + o[0]
              + '" aria-pressed="' + (on ? 'true' : 'false') + '">' + o[1] + '</button>';
          }).join('')
        + '</span></div>';
    }).join('') + '<div class="dg-set-foot">设置保存在本机，刷新后保留</div>';
  }

  /** 应用主题：重绘节点（尺寸/光晕/标签）+ 刷新粒子 + 重画背景 */
  function applyThemeChange() {
    writeTheme();
    renderSettings();
    try {
      if (state.ready && state.chart && !state.chart.isDisposed()) {
        // 标签密度变了必须重算「哪些节点常显」，否则 label.show 还是旧集合
        computeLabelSet();
        paint();              // 重新生成 series（symbolSize / shadowBlur / label 规则都在里面）
        applyFilter();        // 重新套用筛选与选中态
        particlesRefreshSoon();
        atmo.rings = null;    // 环数量与呼吸幅度随主题变化
        atmoRefresh();
      }
    } catch (e) {}
  }

  function bindSettings() {
    var btn = $('dg-settings-btn');
    var box = $('dg-settings');
    if (!btn || !box) return;
    renderSettings();
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = box.hidden;
      box.hidden = !open;
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    box.addEventListener('click', function (e) {
      var t = e.target.closest ? e.target.closest('[data-dg-set]') : null;
      if (!t) return;
      var k = t.getAttribute('data-dg-set');
      var v = t.getAttribute('data-val');
      if (k === 'glow') v = Number(v);
      theme[k] = v;
      applyThemeChange();
    });
    document.addEventListener('click', function (e) {
      if (!box.hidden && !box.contains(e.target) && e.target !== btn) {
        box.hidden = true;
        btn.setAttribute('aria-expanded', 'false');
      }
    });
  }

  /** 预取「系统↔设计」索引：把文档里提到的 server 模块 / API 端点标为可点查的实体 */
  function prefetchSystemLinks() {
    try {
      fetch('system-links.json', { cache: 'no-cache' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (doc) { if (doc && doc.doc_links) state.sysLinks = doc; })
        .catch(function () {});
    } catch (e) {}
  }

  /** 面板里的「系统关联」区块：模块是否存在、被文档引用几次一目了然 */
  function sysLinkBlock(id) {
    var doc = state.sysLinks;
    if (!doc || !doc.doc_links) return '';
    var link = doc.doc_links[id];
    if (!link) {
      return '<div class="dg-rel-hd">系统关联<b>0</b></div>'
        + '<div class="dg-sys-empty">该文档正文未提及具体的 server 模块或 API 端点</div>';
    }
    var mods = link.modules || [];
    var eps = link.endpoints || [];
    var html = '<div class="dg-rel-hd">系统关联<b>' + (mods.length + eps.length) + '</b></div>';
    if (mods.length) {
      html += '<div class="dg-sys-group">模块</div><div class="dg-sys-list">';
      mods.slice(0, 8).forEach(function (m) {
        var cls = m.exists ? 'is-ok' : (m.planned ? 'is-plan' : 'is-miss');
        var tip = m.exists ? '模块已存在' : (m.planned ? '文档里标为「新增/新建」，尚未实现' : '文档引用了不存在的模块');
        html += '<span class="dg-sys-chip ' + cls + '" title="' + esc(tip) + '">'
          + esc(m.name) + '<i>' + m.mentions + '</i></span>';
      });
      html += '</div>';
    }
    if (eps.length) {
      html += '<div class="dg-sys-group">接口</div><div class="dg-sys-list">';
      eps.slice(0, 8).forEach(function (e) {
        var cls = e.exists === false ? 'is-miss' : (e.exists === true ? 'is-ok' : 'is-unk');
        var tip = e.exists === false ? 'OpenAPI 里没有这个端点' : (e.exists === true ? '端点存在' : '未校验（离线生成）');
        html += '<span class="dg-sys-chip ' + cls + '" title="' + esc(tip) + '">'
          + esc(e.path) + '<i>' + e.mentions + '</i></span>';
      });
      html += '</div>';
    }
    if (doc.missing_modules && doc.missing_modules[id] && doc.missing_modules[id].length) {
      html += '<div class="dg-sys-warn">文档引用了不存在的模块：'
        + esc(doc.missing_modules[id].join('、')) + '</div>';
    }
    return html;
  }

  function boot() {
    prefetchOfflineLayout();
    prefetchSystemLinks();
    bindSettings();
    document.querySelectorAll('[data-dg-view]').forEach(function (btn) {
      btn.addEventListener('click', function () { setView(btn.getAttribute('data-dg-view')); });
    });
    if (/^#graph/.test(location.hash)) setView('graph');
    // 供验收/诊断使用（只读快照，不参与渲染逻辑）
    window.DocsGraph = {
      state: state,
      setView: setView,
      // 编程式改主题（将来可接后台配置下发）：{motion,labels,nodes,glow}
      setTheme: function (patch) {
        Object.keys(patch || {}).forEach(function (k) {
          if (THEME_VALUES[k] && THEME_VALUES[k].indexOf(patch[k]) >= 0) theme[k] = patch[k];
        });
        applyThemeChange();
        return Object.assign({}, theme);
      },
      getTheme: function () { return Object.assign({}, theme); },
      themeValues: THEME_VALUES,
      selectNode: selectNode,
      closePanel: closePanel,
      applyFilter: function () { return applyFilter(); },
      snapshotPositions: snapshotPositions,
      // 粒子层只读快照（验收/诊断用，不参与渲染决策）
      particles: function () {
        var directed = 0, undirected = 0, active = 0, nTotal = 0;
        for (var i = 0; i < particles.segs.length; i++) {
          var s = particles.segs[i];
          if (s.type === 'data_link') undirected += 1; else directed += 1;
          if (s.active) active += 1;
          nTotal += (s.n || 0);
        }
        return {
          on: particles.on,
          ok: particles.ok,
          why: particles.why,
          running: !!particles.raf,
          allowed: particlesAllowed(),
          segs: particles.segs.length,
          directed: directed,
          undirected: undirected,
          active: active,
          particlesDrawn: nTotal,
          size: [particles.w, particles.h],
          hover: particles.hover
        };
      },
      positions: function () {
        var out = {};
        try {
          var data = state.chart.getModel().getSeriesByIndex(0).getData();
          var n = data.count();
          for (var i = 0; i < n; i++) {
            var lay = data.getItemLayout(i);
            var id = data.getId ? data.getId(i) : (data.getRawDataItem(i) || {}).id;
            if (lay && lay.length >= 2) out[String(id)] = [Math.round(lay[0] * 100) / 100, Math.round(lay[1] * 100) / 100];
          }
        } catch (e) {}
        return out;
      },
      nodePixel: function (i) {
        try {
          var data = state.chart.getModel().getSeriesByIndex(0).getData();
          return data.getItemLayout(i || 0);
        } catch (e) { return null; }
      },
      stats: function () {
        return {
          nodes: Object.keys(state.byId).length,
          links: (state.data && state.data.links) ? state.data.links.length : 0,
          settled: state.settled,
          rendered: state.rendered,
          paints: state.paints,
          group: state.group,
          keyword: state.keyword,
          selected: state.selected
        };
      }
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
