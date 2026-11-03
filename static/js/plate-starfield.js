/**
 * DAPENG Alpha · AXON 3D 星图（自由力导向星系）
 * ---------------------------------------------------------------------------
 * 定位：AXON 图版的增强视角，独立于 plate.js（不改其视图机与 SVG 渲染）。
 * 数据：读取 static/docs-graph.json（与经典图谱同源，单一事实源）。
 * 呈现：3D 力导向布局 + 透视投影，可 360° 环绕；点击节点出档案卡。
 *
 * 交互：拖拽 = 环绕；滚轮 = 缩放；点击 = 档案卡；空格 = 暂停/继续自转；Esc = 关闭。
 * 无障碍：role=dialog + aria-modal；打开时焦点入内，关闭后回到入口按钮；
 *         尊重 prefers-reduced-motion（不自动旋转）。
 */
(function () {
  'use strict';

  var SRC = 'docs-graph.json';
  var GROUP_COLOR = {
    root: '#d8b48a',
    '00-strategy': '#9fb6cf',
    '10-architecture': '#b7c9d6',
    '20-prd': '#c3aec9',
    '30-data': '#a9c3b4',
    '40-ops': '#c9b58a',
    '90-archive': '#8e8b84'
  };
  var GROUP_LABEL = {
    root: '根目录', '00-strategy': '战略', '10-architecture': '架构',
    '20-prd': '需求', '30-data': '数据', '40-ops': '运维', '90-archive': '归档'
  };

  var root = null, canvas = null, ctx = null, tip = null, card = null, hudState = null;
  var nodes = [], links = [], byId = {};
  var W = 0, H = 0, dpr = 1;
  var yaw = -0.62, pitch = 0.36, zoom = 1.18, auto = true, reduceMotion = false;
  var drag = null, hover = null, sel = null, raf = 0, lastT = 0;
  var pointer = { x: 0, y: 0 };

  /* ── 确定性随机：同一份数据每次打开布局一致 ───────────────────────────── */
  function wrapPi(v) {
    var t = Math.PI * 2;
    return ((v + Math.PI) % t + t) % t - Math.PI;
  }

  function seedOf(str) {
    var h = 2166136261;
    for (var i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }
  function rng(seed) {
    var t = seed >>> 0;
    return function () {
      t += 0x6D2B79F5;
      var r = Math.imul(t ^ (t >>> 15), 1 | t);
      r ^= r + Math.imul(r ^ (r >>> 7), 61 | r);
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* ── 布局：球面初始化 + 3D 力导向（一次性求解，渲染零物理解算） ───────── */
  function layout() {
    var n = nodes.length;
    var golden = Math.PI * (3 - Math.sqrt(5));
    nodes.forEach(function (node, i) {
      var rand = rng(seedOf(node.id));
      var y = 1 - (i / Math.max(1, n - 1)) * 2;
      var radius = Math.sqrt(Math.max(0, 1 - y * y));
      var theta = golden * i;
      node.p = {
        x: Math.cos(theta) * radius + (rand() - 0.5) * 0.05,
        y: y + (rand() - 0.5) * 0.05,
        z: Math.sin(theta) * radius + (rand() - 0.5) * 0.05
      };
    });

    var groupKeys = Object.keys(GROUP_COLOR);
    var anchors = {};
    groupKeys.forEach(function (key, i) {
      var a = (i / groupKeys.length) * Math.PI * 2;
      anchors[key] = { x: Math.cos(a) * 0.62, y: (i % 2 ? 0.24 : -0.2), z: Math.sin(a) * 0.62 };
    });

    var ITER = 320;
    for (var step = 0; step < ITER; step++) {
      var cooling = 1 - step / ITER;
      // 斥力（O(n²)，50+ 节点足够）
      for (var i = 0; i < n; i++) {
        for (var j = i + 1; j < n; j++) {
          var a = nodes[i].p, b = nodes[j].p;
          var dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
          var d2 = dx * dx + dy * dy + dz * dz + 0.0008;
          var d = Math.sqrt(d2);
          var force = 0.0016 / d2;
          var ux = dx / d, uy = dy / d, uz = dz / d;
          a.x += ux * force; a.y += uy * force; a.z += uz * force;
          b.x -= ux * force; b.y -= uy * force; b.z -= uz * force;
        }
      }
      // 弹簧（连线）
      for (var k = 0; k < links.length; k++) {
        var s = byId[links[k].s], t = byId[links[k].t];
        if (!s || !t) continue;
        var vx = t.p.x - s.p.x, vy = t.p.y - s.p.y, vz = t.p.z - s.p.z;
        var dist = Math.sqrt(vx * vx + vy * vy + vz * vz) + 1e-5;
        var pull = (dist - 0.42) * 0.045 * cooling;
        var nx = vx / dist, ny = vy / dist, nz = vz / dist;
        s.p.x += nx * pull; s.p.y += ny * pull; s.p.z += nz * pull;
        t.p.x -= nx * pull; t.p.y -= ny * pull; t.p.z -= nz * pull;
      }
      // 向心 + 分组锚点
      for (var m = 0; m < n; m++) {
        var p = nodes[m].p;
        var anchor = anchors[nodes[m].group] || { x: 0, y: 0, z: 0 };
        p.x += (anchor.x - p.x) * 0.006 + (-p.x) * 0.004;
        p.y += (anchor.y - p.y) * 0.006 + (-p.y) * 0.004;
        p.z += (anchor.z - p.z) * 0.006 + (-p.z) * 0.004;
      }
    }
    // 归一化到单位球
    var maxR = 0.001;
    nodes.forEach(function (node) {
      var p = node.p;
      maxR = Math.max(maxR, Math.sqrt(p.x * p.x + p.y * p.y + p.z * p.z));
    });
    nodes.forEach(function (node) {
      node.p.x /= maxR; node.p.y /= maxR; node.p.z /= maxR;
    });
  }

  /* ── 投影 ─────────────────────────────────────────────────────────────── */
  function project(p) {
    var cy = Math.cos(yaw), sy = Math.sin(yaw);
    var cp = Math.cos(pitch), sp = Math.sin(pitch);
    var x1 = p.x * cy - p.z * sy;
    var z1 = p.x * sy + p.z * cy;
    var y1 = p.y * cp - z1 * sp;
    var z2 = p.y * sp + z1 * cp;
    var persp = 1 / (1 + z2 * 0.55);           // 近大远小
    var scale = Math.min(W, H) * 0.40 * zoom * persp;
    return { x: W / 2 + x1 * scale, y: H / 2 + y1 * scale, z: z2, k: persp };
  }

  function depthAlpha(z) {
    // z ∈ [-1,1]，越近越亮
    return 0.32 + 0.55 * (1 - (z + 1) / 2);
  }

  /* ── 渲染 ─────────────────────────────────────────────────────────────── */
  function paint() {
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    var pts = nodes.map(function (node) {
      var q = project(node.p);
      return { node: node, x: q.x, y: q.y, z: q.z, k: q.k };
    });
    var index = {};
    pts.forEach(function (pt) { index[pt.node.id] = pt; });

    // 连线：细、暖金、按深度给透明度
    links.forEach(function (link) {
      var a = index[link.s], b = index[link.t];
      if (!a || !b) return;
      var alpha = 0.10 + 0.22 * ((depthAlpha(a.z) + depthAlpha(b.z)) / 2);
      var hot = sel && (link.s === sel || link.t === sel);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.strokeStyle = hot ? 'rgba(240,216,180,.72)' : 'rgba(214,176,132,' + alpha.toFixed(3) + ')';
      ctx.lineWidth = hot ? 1.0 : 0.65;
      ctx.stroke();
    });

    // 节点：远→近排序（画家算法）
    pts.sort(function (m, n) { return n.z - m.z; });
    var labelCandidates = [];
    pts.forEach(function (pt) {
      var node = pt.node;
      var color = GROUP_COLOR[node.group] || '#c9c4b8';
      var base = Math.max(1.5, Math.min(8.2, 1.9 + Math.sqrt(node.degree || 1) * 1.05)) * pt.k * Math.min(1.3, zoom);
      var isSel = sel === node.id;
      var isHover = hover && hover.id === node.id;

      var glow = ctx.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, base * (isSel ? 7 : 4));
      glow.addColorStop(0, hexA(color, isSel ? 0.55 : 0.28));
      glow.addColorStop(0.45, hexA(color, 0.10));
      glow.addColorStop(1, hexA(color, 0));
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, base * (isSel ? 7 : 4), 0, Math.PI * 2);
      ctx.fill();

      ctx.beginPath();
      ctx.arc(pt.x, pt.y, base, 0, Math.PI * 2);
      ctx.fillStyle = hexA(color, 0.92);
      ctx.fill();
      ctx.lineWidth = 0.6;
      ctx.strokeStyle = isSel ? 'rgba(255,244,226,.95)' : hexA(color, 0.55);
      ctx.stroke();

      if (isSel || isHover || (node.degree || 0) >= 7) {
        labelCandidates.push({ node: node, x: pt.x, y: pt.y, base: base, z: pt.z, isSel: isSel, isHover: isHover });
      }
    });

    // 标签：先按优先级排序，再做矩形避让，避免中心堆叠成一片
    labelCandidates.sort(function (a, b) {
      var pa = (a.isSel || a.isHover) ? 2 : 1;
      var pb = (b.isSel || b.isHover) ? 2 : 1;
      if (pa !== pb) return pb - pa;
      return (b.node.degree || 0) - (a.node.degree || 0);
    });
    var placed = [];
    labelCandidates.forEach(function (cand) {
      var label = shortLabel(cand.node);
      var big = cand.isSel || cand.isHover;
      ctx.font = (big ? '600 ' : '500 ') + (big ? 12 : 10.5) + 'px "Cascadia Mono", ui-monospace, monospace';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      var w = ctx.measureText(label).width;
      var lx = cand.x + cand.base + 5, ly = cand.y;
      // 右/左边界收拢：标签不出画布
      if (lx + w > W - 12) lx = cand.x - cand.base - 5 - w;
      if (lx < 8) lx = 8;
      var box = { x: lx - 2, y: ly - 7, w: w + 4, h: 14 };
      var clash = placed.some(function (b) {
        return !(box.x + box.w < b.x || box.x > b.x + b.w || box.y + box.h < b.y || box.y > b.y + b.h);
      });
      if (clash && !big) return;  // 选中/悬停始终显示，其余让位
      placed.push(box);
      var fade = Math.min(1, Math.max(0.35, depthAlpha(cand.z)));
      ctx.fillStyle = 'rgba(6,8,12,' + (big ? 0.82 : 0.55) + ')';
      ctx.fillRect(box.x, box.y, box.w, box.h);
      ctx.fillStyle = cand.isSel
        ? 'rgba(255,246,232,.98)'
        : 'rgba(235,228,216,' + (0.55 + 0.35 * fade).toFixed(2) + ')';
      ctx.fillText(label, lx, ly);
    });

    if (hudState) {
      var deg = ((yaw * 180 / Math.PI) % 360 + 360) % 360;
      hudState.textContent = nodes.length + ' 节点 · ' + links.length + ' 关系 · ' + Math.round(deg) + '°' + (auto ? ' 自转' : ' 暂停');
    }
  }

  function shortLabel(node) {
    var label = String(node.label || node.name || node.id || '');
    label = label.replace(/^docs\//, '').replace(/\.md$/i, '');
    if (label.length <= 26) return label;
    var parts = label.split('/');
    return parts[parts.length - 1].slice(0, 26);
  }

  function hexA(hex, alpha) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex);
    if (!m) return 'rgba(216,180,132,' + alpha + ')';
    var v = parseInt(m[1], 16);
    return 'rgba(' + ((v >> 16) & 255) + ',' + ((v >> 8) & 255) + ',' + (v & 255) + ',' + alpha + ')';
  }

  /* ── 交互 ─────────────────────────────────────────────────────────────── */
  function pick(x, y) {
    var best = null, bestD = 16;
    for (var i = 0; i < nodes.length; i++) {
      var q = project(nodes[i].p);
      var dx = q.x - x, dy = q.y - y;
      var d = Math.sqrt(dx * dx + dy * dy);
      var hitR = Math.max(9, 3 + Math.sqrt(nodes[i].degree || 1) * 1.6);
      if (d < Math.max(bestD, hitR) && d < hitR) { best = nodes[i]; bestD = d; }
    }
    return best;
  }

  function bind() {
    canvas.addEventListener('pointerdown', function (e) {
      canvas.setPointerCapture(e.pointerId);
      drag = { x: e.clientX, y: e.clientY, moved: 0 };
      root.classList.add('is-drag');
    });
    canvas.addEventListener('pointermove', function (e) {
      pointer.x = e.clientX; pointer.y = e.clientY;
      if (drag) {
        var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        drag.moved += Math.abs(dx) + Math.abs(dy);
        yaw += dx * 0.0048;
        pitch = wrapPi(pitch + dy * 0.0038);  // 俯仰整圈环绕：越过极点翻面但不设死角
        drag.x = e.clientX; drag.y = e.clientY;
        paint();
        return;
      }
      var hit = pick(e.clientX, e.clientY);
      if ((hit && hit.id) !== (hover && hover.id)) {
        hover = hit;
        canvas.style.cursor = hit ? 'pointer' : 'grab';
        paint();
      }
    });
    canvas.addEventListener('pointerup', function (e) {
      var wasDrag = drag && drag.moved > 6;
      root.classList.remove('is-drag');
      drag = null;
      if (wasDrag) return;
      var hit = pick(e.clientX, e.clientY);
      sel = hit ? hit.id : null;
      renderCard(hit);
      paint();
    });
    canvas.addEventListener('pointerleave', function () {
      if (hover) { hover = null; paint(); }
    });
    canvas.addEventListener('wheel', function (e) {
      e.preventDefault();
      zoom = Math.max(0.45, Math.min(3.0, zoom * Math.exp(-e.deltaY * 0.0011)));
      paint();
    }, { passive: false });
    window.addEventListener('keydown', onKey);
    window.addEventListener('resize', resize);
  }

  function onKey(e) {
    if (!root || !root.classList.contains('is-open')) return;
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    if (e.key === ' ') { e.preventDefault(); auto = !auto; }
  }

  function resize() {
    if (!root || !root.classList.contains('is-open')) return;
    W = window.innerWidth; H = window.innerHeight;
    dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.floor(W * dpr);
    canvas.height = Math.floor(H * dpr);
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    paint();
  }

  function loop(t) {
    if (!root || !root.classList.contains('is-open')) return;
    if (!lastT) lastT = t;
    var dt = Math.min(48, t - lastT);
    lastT = t;
    if (auto && !drag && !reduceMotion) {
      yaw += dt * 0.000045;
      paint();
    }
    raf = requestAnimationFrame(loop);
  }

  /* ── 档案卡 ───────────────────────────────────────────────────────────── */
  function renderCard(node) {
    if (!card) return;
    if (!node) { card.classList.remove('is-show'); card.innerHTML = ''; return; }
    var groupLabel = GROUP_LABEL[node.group] || node.group_label || node.group || '';
    var href = 'project-docs.html#graph=' + encodeURIComponent(node.id);
    card.innerHTML =
      '<div class="sf-card-hd"><span class="sf-card-tag">' + esc(groupLabel) + '</span>' +
      '<button type="button" class="sf-card-x" aria-label="关闭档案卡">×</button></div>' +
      '<h3>' + esc(shortLabel(node)) + '</h3>' +
      '<p class="sf-card-path">' + esc(node.id) + '</p>' +
      '<div class="sf-card-meta">' +
        '<span>度数 <b>' + (node.degree == null ? '--' : node.degree) + '</b></span>' +
        '<span>行数 <b>' + (node.lines == null ? '--' : node.lines) + '</b></span>' +
        '<span>更新 <b>' + esc(node.updated || '--') + '</b></span>' +
      '</div>' +
      (node.summary ? '<p class="sf-card-sum">' + esc(String(node.summary).slice(0, 120)) + '…</p>' : '') +
      '<a class="sf-card-link" href="' + href + '">在原版图谱中查看 →</a>';
    card.classList.add('is-show');
    var x = card.querySelector('.sf-card-x');
    if (x) x.addEventListener('click', function () {
      sel = null; card.classList.remove('is-show'); paint();
    });
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /* ── 装配 / 开关 ──────────────────────────────────────────────────────── */
  function build() {
    root = document.createElement('div');
    root.className = 'starmap';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-modal', 'true');
    root.setAttribute('aria-label', 'AXON 3D 星图：文档关系星系');
    root.innerHTML =
      '<canvas class="starmap-canvas" aria-label="3D 星系画布：可拖拽环绕、滚轮缩放"></canvas>' +
      '<span class="starmap-frame" aria-hidden="true"></span>' +
      '<div class="starmap-hud">' +
        '<div class="starmap-kicker">AXON · STELLAR VIEW</div>' +
        '<h2 class="starmap-title">文档关系 · 3D 星图</h2>' +
        '<div class="starmap-state" id="sf-state">加载中…</div>' +
      '</div>' +
      '<div class="starmap-actions">' +
        '<button type="button" class="starmap-btn" data-reset>重置视角</button>' +
        '<button type="button" class="starmap-btn is-close" data-close>关闭 ESC</button>' +
      '</div>' +
      '<div class="starmap-legend" id="sf-legend"></div>' +
      '<div class="starmap-hint">拖拽 360° 环绕 · 滚轮缩放 · 点击节点出档案 · 空格暂停自转</div>' +
      '<aside class="sf-card" id="sf-card" aria-live="polite"></aside>';
    document.body.appendChild(root);
    canvas = root.querySelector('.starmap-canvas');
    ctx = canvas.getContext('2d');
    hudState = root.querySelector('#sf-state');
    card = root.querySelector('#sf-card');
    root.querySelector('[data-close]').addEventListener('click', close);
    root.querySelector('[data-reset]').addEventListener('click', function () {
      yaw = -0.62; pitch = 0.36; zoom = 1.0; auto = true; sel = null;
      renderCard(null); paint();
    });
    var legend = root.querySelector('#sf-legend');
    legend.innerHTML = Object.keys(GROUP_COLOR).map(function (key) {
      return '<span class="sf-leg"><i style="background:' + GROUP_COLOR[key] + '"></i>' +
        esc(GROUP_LABEL[key] || key) + '</span>';
    }).join('');
    bind();
  }

  function open() {
    if (!root) build();
    if (!nodes.length) {
      load().then(function () {
        root.classList.add('is-open');
        document.body.classList.add('starmap-open');
        resize();
        if (!raf) raf = requestAnimationFrame(loop);
        var closeBtn = root.querySelector('[data-close]');
        if (closeBtn) closeBtn.focus({ preventScroll: true });
      }).catch(function (err) {
        root.classList.add('is-open');
        document.body.classList.add('starmap-open');
        if (hudState) hudState.textContent = '数据加载失败：' + (err && err.message || err);
      });
      return;
    }
    root.classList.add('is-open');
    document.body.classList.add('starmap-open');
    resize();
    if (!raf) raf = requestAnimationFrame(loop);
  }

  function close() {
    if (!root) return;
    root.classList.remove('is-open');
    document.body.classList.remove('starmap-open');
    cancelAnimationFrame(raf);
    raf = 0; lastT = 0;
    var trigger = document.getElementById('starfield-btn');
    if (trigger) trigger.focus({ preventScroll: true });
  }

  function load() {
    if (hudState) hudState.textContent = '加载 docs-graph.json…';
    return fetch(SRC, { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (json) {
      nodes = (json.nodes || []).map(function (n) {
        return {
          id: n.id, name: n.name, group: n.group, label: n.label || n.name,
          title: n.title || '', degree: n.degree || 0, lines: n.lines,
          updated: n.updated, summary: n.summary || ''
        };
      });
      links = (json.links || []).map(function (l) {
        return { s: l.source, t: l.target, type: l.type };
      }).filter(function (l) { return l.s !== l.t; });
      byId = {};
      nodes.forEach(function (n) { byId[n.id] = n; });
      layout();
      if (hudState) hudState.textContent = nodes.length + ' 节点 · ' + links.length + ' 关系';
    });
  }

  function init() {
    reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    auto = !reduceMotion;
    var btn = document.getElementById('starfield-btn');
    if (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        open();
      });
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  window.AxonStarfield = { open: open, close: close };
})();
