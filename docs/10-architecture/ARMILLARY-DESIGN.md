# 浑天仪 ARMILLARY · 星系设计代码全稿（v53–v59 权威底稿）

> 本文是 AXON 星系视图「浑天仪」设计的**完整可运行代码与设计说明**，从
> `static/plate-graph.html.bak-v58`（完整实现备份）整理而来。
> 当前现网 `plate-graph.html` 可能运行其他会话的实现——如需恢复本设计：
> `cp static/plate-graph.html.bak-v58 static/plate-graph.html`，或按本文逐段重放。
> 设计账本：AXON-GRAPH-DESIGN.md §五十（刻线仪器）/ §四十五（浑天仪）。

## 0 · 设计原则

- **刻线不涂光**：一切结构用微粒紧贴线位排布成"发光细丝"，禁用大光斑软晕；
- **日期即星历**：文档星在轨道上的相位 = 修订日期在年内的位置；
- **开普勒差速**：内环快外环慢，ω(r) = ω0·(r0/r)^0.75，GL 与 2D 同式同速；
- **kind 三分**：粒子流按 kind 分速——0=数据环（差速）·1=文档星（JS 全角烘焙）·2=背景（匀速）；
- **个性化五 hooks**：palette 浸染 / 盘心观测者小印 / MY ASTERISM 星座 / CAT.№ 仪器编号 / ringTilt 环架倾角。

## 1 · 着色器（GL_PART_VS 核心：kind 三分支）

```js
var GL_PART_VS = [
  'attribute vec3 aPos; attribute vec3 aColor; attribute float aSize; attribute float aKind;',
  'uniform mat4 uRot; uniform float uD; uniform float uZoom; uniform float uW; uniform float uH;',
  'uniform float uTime; uniform float uSpin; uniform float uSizeMul; uniform float uPR; uniform float uAlpha;',
  'uniform vec2 uPan;',
  'uniform float uAlpha; uniform float uIntro;',   // uIntro：进场显影系数
  'varying vec3 vColor; varying float vKind; varying float vFade;',
  'void main(){',
  '  vec3 q;',
  '  if (aKind > 0.5) {',
  '    if (aKind > 1.5) {',                                   // kind=2 背景：匀速自转
  '      float ca = cos(uTime * uSpin), sa = sin(uTime * uSpin);',
  '      q = vec3(ca * aPos.x + sa * aPos.z, aPos.y, -sa * aPos.x + ca * aPos.z);',
  '    } else { q = aPos; }',                                  // kind=1 文档星：JS 已含全角
  '  } else {',                                                // kind=0 数据环：开普勒差速
  '    float rXZ = max(30.0, length(aPos.xz));',
  '    float th = uTime * uSpin * pow(40.0 / rXZ, 0.75);',
  '    float ca = cos(th), sa = sin(th);',
  '    q = vec3(ca * aPos.x + sa * aPos.z, aPos.y, -sa * aPos.x + ca * aPos.z);',
  '  }',
  '  vec4 p = uRot * vec4(q, 1.0);',
  '  float s = uD / max(260.0, uD - p.z) * uZoom;',
  '  vec2 screen = vec2(p.x * s, p.y * s) + uPan;',
  '  gl_Position = vec4(screen.x / (uW * 0.5), -screen.y / (uH * 0.5), 0.0, 1.0);',
  '  gl_PointSize = max(1.2, aSize * uSizeMul * uPR * s * 1.35);',
  '  vColor = aColor; vKind = aKind;',
  '  float rr = length(aPos.xz);',
  '  vFade = clamp(1.25 - rr / 520.0, 0.35, 1.0);',
  '}'
].join('\n');

// 片元：软点精灵（半径衰减），uIntro 控制显影
var GL_PART_FS = [
  'precision mediump float;',
  'varying vec3 vColor; varying float vKind; varying float vFade;',
  'uniform float uAlpha; uniform float uIntro;',
  'void main(){',
  '  vec2 d = gl_PointCoord - vec2(0.5);',
  '  float rad = length(d);',
  '  float glow = pow(max(0.0, 1.0 - rad * 2.0), 3.0);',
  '  float core = smoothstep(0.16, 0.0, rad);',
  '  float alpha = glow * 0.22 + core * 0.55;',
  '  if (rad > 0.5) discard;',
  '  float fade = clamp(vFade, 0.0, 1.0);',
  '  gl_FragColor = vec4(vColor * alpha * fade * uAlpha * uIntro, alpha * fade * uAlpha * uIntro);',
  '}'
].join('\n');
```

## 2 · buildGalGL（浑天仪粒子生成）

缓冲布局：每粒子 8 float（x,y,z, r,g,b, size, kind）。常量 `COSMO = { R: 252, r0: 40 }`，
`ringR(li) = r0 + (li/6)^0.92 · (R - r0)`（七层）。

```js
function buildGalGL() {
  var gl = GALGL.gl;
  var seed = parseInt(prefs.galaxySeed || '3', 10) || 3;
  var rnd = (function () { var t = seed * 9301 + 49297; return function () {
    t = (t * 9301 + 49297) % 233280; return t / 233280; }; })();
  var density = prefs.galaxyDust === 'high' ? 2.0 : prefs.galaxyDust === 'low' ? 0.55 : 1.0;
  var cs2 = getComputedStyle(document.documentElement);                    // 个性化：palette 浸染
  var accP = (cs2.getPropertyValue('--accent-rgb').trim() || '214,176,132').split(',');
  var accR = accP[0]/255, accG = accP[1]/255, accB = accP[2]/255;
  var ringN = Math.round(5200 * density);                                  // 刻线：加密贴线
  var dustN = Math.round(1400 * density);
  var coreN = Math.round(7000 * Math.max(0.6, density));
  var cageN = 1400, haloN = Math.round(3500 * density);
  var total = ringN * LAYERS.length + dustN + coreN + cageN * 4 + haloN;
  var data = new Float32Array(total * 8);
  var R = COSMO.R, r0 = COSMO.r0, idx = 0;
  function put(x, y, z, r, g, b, size, kind) {
    var o = idx * 8;
    data[o] = x; data[o+1] = y; data[o+2] = z;
    data[o+3] = r; data[o+4] = g; data[o+5] = b;
    data[o+6] = size; data[o+7] = kind || 0; idx++;
  }

  /* 七层数据轨道环：内暖外冷 + accent 浸染 + 金屑 + 密度波雕（kind=0 差速） */
  for (var li = 0; li < LAYERS.length; li++) {
    var rr0 = ringR(li);
    var rw = 1.1 + li * 0.25;                       // 刻线宽度：紧贴环线
    var mixK = li / Math.max(1, LAYERS.length - 1); // 0 内暖 → 1 外冷
    var ph0 = (seed * 0.7 + li * 1.618) % (Math.PI * 2);
    for (var i = 0; i < ringN; i++) {
      var ang = ph0 + rnd() * Math.PI * 2;
      var rr = rr0 + (rnd() + rnd() - 1) * rw;
      var yy = (rnd() + rnd() - 1) * (1.2 + li * 0.18);
      var gold = rnd() < 0.055;                     // 金屑
      var wv  = 0.7 + 0.3 * Math.sin(ang * 3 + li * 1.7);  // 密度波雕
      var rC = (1.0 + (0.64 - 1.0) * mixK) * (0.78 + rnd() * 0.22) * wv;
      var gC = (0.86 + (0.76 - 0.86) * mixK) * (0.78 + rnd() * 0.22) * wv;
      var bC = (0.60 + (0.97 - 0.60) * mixK) * (0.78 + rnd() * 0.22) * wv;
      rC = rC * 0.72 + accR * 0.28; gC = gC * 0.72 + accG * 0.28; bC = bC * 0.72 + accB * 0.28;
      put(Math.cos(ang) * rr, yy, Math.sin(ang) * rr,
          gold ? 1.0 : rC, gold ? 0.85 : gC, gold ? 0.60 : bC,
          (0.5 + rnd() * 0.6) * (gold ? 1.6 : 1), 0);
    }
    /* 伴线：环外侧一根更细更淡的镌刻细环（双线轨道） */
    for (var i2 = 0; i2 < 520; i2++) {
      var ang2 = ph0 + rnd() * Math.PI * 2;
      var rr2 = rr0 + rw * (2.6 + (rnd() - 0.5) * 0.8) + li * 0.4;
      put(Math.cos(ang2) * rr2, (rnd() + rnd() - 1) * 0.9, Math.sin(ang2) * rr2,
          accR * 0.55, accG * 0.55, accB * 0.55, 0.45 + rnd() * 0.5, 0);
    }
    /* 刻度：每 6° 内外短刻，每 30° 金色长刻（表圈 graduations，骑差速旋转） */
    for (var tg = 0; tg < 60; tg++) {
      var tga = ph0 + (tg / 60) * Math.PI * 2;
      var goldT = tg % 5 === 0;
      var tLen = goldT ? 4.2 : 2.0;
      var tCol = goldT ? [1.0, 0.84, 0.55] : [accR * 0.9, accG * 0.9, accB * 0.9];
      for (var tsi = 0; tsi < 2; tsi++) {
        var trr = rr0 + (tsi === 0 ? tLen : -tLen);
        put(Math.cos(tga) * trr, 0, Math.sin(tga) * trr,
            tCol[0], tCol[1], tCol[2], goldT ? 1.1 : 0.7, 0);
      }
    }
    /* 历元金标：该环"1 月 1 日"相位点一粒金亮星（日期即星历的原点） */
    put(Math.cos(ph0) * rr0, 0, Math.sin(ph0) * rr0, 1.0, 0.88, 0.6, 2.4, 0);
  }

  /* 环间浮尘（kind=2 匀速） */
  for (var d2 = 0; d2 < dustN; d2++) {
    var rrD = r0 - 18 + rnd() * (R - r0 + 48);
    var angD = rnd() * Math.PI * 2;
    put(Math.cos(angD) * rrD, (rnd() + rnd() - 1) * 4, Math.sin(angD) * rrD,
        0.52, 0.56, 0.64, 0.45 + rnd() * 0.65, 2);
  }
  /* 核球：致密白金灯芯（kind=2） */
  for (var b = 0; b < coreN; b++) {
    var br = Math.pow(rnd(), 2.6) * 46;
    var ba = rnd() * Math.PI * 2;
    var bz = (rnd() + rnd() + rnd() - 1.5) * (6 + br * 0.14);
    put(Math.cos(ba) * br, bz, Math.sin(ba) * br,
        1.0, 0.9 + rnd() * 0.08, 0.72 + rnd() * 0.14, 0.9 + rnd() * 1.3, 2);
  }
  /* 浑天仪环架：四道倾斜大环（含一道竖直子午环），钢蓝底 + 金屑（kind=2 匀速） */
  var tiltF = prefs.ringTilt === 'compact' ? 0.6 : prefs.ringTilt === 'wide' ? 1.45 : 1.0;
  var CAGE = [
    { r: R + 46,  tx: 0.42 * tiltF,  tz: 0.18 * tiltF },
    { r: R + 74,  tx: -0.30 * tiltF, tz: -0.34 * tiltF },
    { r: R + 104, tx: 0.62 * tiltF,  tz: 0.52 * tiltF },
    { r: R + 104, tx: Math.PI / 2,   tz: 0 }              // 子午环：竖直
  ];
  CAGE.forEach(function (C) {
    var cxr = Math.cos(C.tx), sxr = Math.sin(C.tx), czr = Math.cos(C.tz), szr = Math.sin(C.tz);
    for (var ci2 = 0; ci2 < cageN; ci2++) {
      var aC = rnd() * Math.PI * 2;
      var px = Math.cos(aC) * C.r, py = 0, pz = Math.sin(aC) * C.r;
      var y1 = py * cxr - pz * sxr, z1 = py * sxr + pz * cxr;   // Rx
      var x2 = px * czr - y1 * szr, y2 = px * szr + y1 * czr;   // Rz
      var gilt = rnd() < 0.12;
      var cw = 0.68 + 0.32 * Math.sin(aC * 5 + C.r * 0.01);     // 丝带亮度波
      put(x2, y2, z1,
          (gilt ? 1.0 : 0.58) * cw, (gilt ? 0.84 : 0.69) * cw, (gilt ? 0.56 : 0.90) * cw,
          (gilt ? 1.1 : 0.5) + rnd() * 0.5, 2);
    }
  });
  /* 晕：远处稀疏冷星（kind=2） */
  for (var h = 0; h < haloN; h++) {
    var hr = 300 + Math.pow(rnd(), 0.6) * 420;
    var th = rnd() * Math.PI * 2, ph = Math.acos(2 * rnd() - 1);
    put(Math.sin(ph) * Math.cos(th) * hr, Math.cos(ph) * hr * 0.72, Math.sin(ph) * Math.sin(th) * hr,
        0.60, 0.64, 0.72, 0.4 + rnd() * 0.6, 2);
  }

  gl.bindBuffer(gl.ARRAY_BUFFER, GALGL.buf);
  gl.bufferData(gl.ARRAY_BUFFER, data.subarray(0, idx * 8), gl.STATIC_DRAW);
  GALGL.count = idx;
  /* 文档星动态缓冲：每星 13 条目（1 亮核 + 12 准星微刻度），逐帧烘焙 */
  GALGL.docData = new Float32Array(DATA.nodes.length * 13 * 8);
  if (!GALGL.docBuf) GALGL.docBuf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, GALGL.docBuf);
  gl.bufferData(gl.ARRAY_BUFFER, GALGL.docData.byteLength, gl.DYNAMIC_DRAW);
  GALGL.sig = ['armillary', prefs.palette, prefs.ringTilt, prefs.galaxyDust,
               prefs.galaxySeed, cam.depthMode].join('|');
}
```

## 3 · galPos（开普勒星历几何）

```js
function datePhase(ds) {                       // 修订日期 → 年内相位 0..1
  var d = Date.parse(ds || '2026-10-01') || 0;
  var y0 = new Date(d).getUTCFullYear();
  var start = Date.parse(y0 + '-01-01'), end = Date.parse((y0 + 1) + '-01-01');
  return end > start ? (d - start) / (end - start) : 0.5;
}
function galPos(nm, t) {
  var li = galIndex(nm), seed = galSeed(nm);
  var intro = galIntro();                      // 进场：半径 0→1 缓入
  var r = ringR(li) * intro;
  /* 开普勒差速：ω(r) = ω0·(r0/r)^0.75，与着色器 kind=0 分支同式同速（负号=CW 同向） */
  var spU = prefs.galSpeed === 'slow' ? 0.45 : prefs.galSpeed === 'fast' ? 1.9 : 1;
  var w0 = (prefs.galaxySpin === 'off' || prefs.galSpeed === 'halt') ? 0
         : GALAXY.speed * 1400 * spU;          // 每秒弧度（uSpin 同源）
  var ang = -w0 * Math.pow(COSMO.r0 / Math.max(30, r), 0.75) * ((t || 0) / 1000)
          + datePhase(nm.updated) * Math.PI * 2 + (seed - 0.5) * 0.22;
  var y = depthY(nm);                          // 纵深维度（620ms 形变动画）
  return { x: geo.W/2 + Math.cos(ang) * r, y: geo.H/2 + y, z: Math.sin(ang) * r,
           ang: ang, li: li, r: r, tilt: 0, nu: ang, ecc: 0 };
}
```

## 4 · 文档星逐帧烘焙（renderGalGL 内：亮核 + 准星环）

```js
/* 每帧：35 星 × 13 条目（1 亮核 + 12 准星微刻度）写入 docBuf */
var dd = GALGL.docData, di = 0;
var calmG = prefs.motion === 'reduce';
DATA.nodes.forEach(function (nm) {
  var wp = galPos(nm, cam.t || 0);
  var deg = nm.degree || 0;
  var hov = state.hover === nm.id || state.sel === nm.id || state.focus === nm.id;
  var inScope = matchDoc(nm) && previewDoc(nm)
    && (!prefs.onlyPins || isPinned(nm.id)) && (!state.q || state.match.has(nm.id));
  var kL = (hov || inScope) ? 1 : 0.28;                       // 筛选/悬停压暗
  var size = (9 + Math.min(13, deg * 0.75)) * (hov ? 1.4 : 1)
           * (calmG ? 1 : 1 + 0.08 * Math.sin((cam.t || 0) / 700 + di)); // 呼吸
  if (g3.viewIds && !g3.viewIds[nm.id]) size = 0;             // 聚焦视野过滤
  var nmSeed = galSeed(nm) * 6.28;
  var bx = wp.x - geo.W / 2, by = wp.y - geo.H / 2, bz = wp.z;
  var cr = colD[0]/255 * kL, cg = colD[1]/255 * kL, cb = colD[2]/255 * kL;
  /* 亮核（每星第 1 条目——low 档只画亮核） */
  dd[di]=bx; dd[di+1]=by; dd[di+2]=bz;
  dd[di+3]=cr; dd[di+4]=cg; dd[di+5]=cb;
  dd[di+6]=size; dd[di+7]=1; di += 8;
  /* 准星环：12 粒微刻度绕星缓旋（对焦环，无光晕） */
  var retR = 11 + deg * 0.32;
  for (var rd = 0; rd < 12; rd++) {
    var ra = (rd / 12) * Math.PI * 2 + (cam.t || 0) / 2400 + nmSeed;
    dd[di]   = bx + Math.cos(ra) * retR;
    dd[di+1] = by + Math.sin(ra) * retR * 0.62;               // 透视压扁
    dd[di+2] = bz;
    dd[di+3] = cr * 0.55; dd[di+4] = cg * 0.55; dd[di+5] = cb * 0.55;
    dd[di+6] = GALGL.low ? 0 : 1.15; dd[di+7] = 1; di += 8;
  }
});
gl.bindBuffer(gl.ARRAY_BUFFER, GALGL.docBuf);
gl.bufferSubData(gl.ARRAY_BUFFER, 0, dd);
/* ...4 个 vertexAttribPointer（pos/color/size/kind）... */
gl.drawArrays(gl.POINTS, 0, DATA.nodes.length * (GALGL.low ? 1 : 13));
```

配套 uniform（每帧）：`uSpin = GALAXY.speed * 1400 * spU`（每秒弧度，与 galPos 每毫秒率×1000 精确相等）、
`uPan = (cam.panX+cam.parX, cam.panY+cam.parY)`、`uIntro = galIntro()`、`uD = cam.d`、`uZoom = cam.zoom`。

## 5 · 2D chrome 层（叠在 GL 之上，只画"仪器读数"）

- 轨道铭牌：`编号 · 板块名 · 份数`，8.5px mono，固定方位角，墨色描边；
- 纵深标尺：非层级维度时左缘虚线标尺 + 上下端语义（真相源/归档、新/旧…）；
- MY ASTERISM：关注文档按收藏顺序的平滑虚线星座 + 菱形端点；
- 角标：`DOCUMENT CONSTELLATION · 36 STARS · 96 PAIRS` / `AXON · PLATE 07` / `OBS · 署名 · CAT.№编号 · PIN n`；
- 流星：~15s 一颗渐隐尾迹（reduce-motion 关闭）；
- 命中：`ringAt(px,py)` 环采样（内/中/外三圈，容差 9px）→ 点环选板块、悬停高亮。

## 6 · 集成要点

- `initGalGL()`：webgl 上下文 + 4 program（粒子/背景/亮部/模糊/合成）+ docBuf；
- 后处理：亮部提取（阈值 0.52）→ 两次分离高斯 → ACES 合成（辉光强度随 galaxyGlow）；
- 帧率治理：空闲 8s 自动 ~30fps；EMA>24ms → GALGL.low（uSizeMul 0.82 + 只画亮核）；页面隐藏暂停；
- 偏好键：`galaxyRender:'3d'|'atlas'`、`ringTilt`、`galaxyDust/Seed/Spin/galSpeed`、`observer`、`pins`；
- **恢复本设计**：`cp static/plate-graph.html.bak-v58 static/plate-graph.html`。


---

## 7 · 实现落地（Canvas 版，2026-10-30）

因用户已明确删除 WebGL 银河，本设计以 **2D Canvas 粒子浑天仪**落地（不使用 GL），保留设计原则：

| 设计原则 | Canvas 实现 |
|---|---|
| 刻线不涂光 | 环线改为「细线 + 点刻星尘」；文档星光晕缩小为 3.4×半径，deg≥3 加准星细环 |
| 日期即星历 | `datePhase()`：修订日期 → 年内相位；`galPos()` 用日期相位定位文档星 |
| 开普勒差速 | `ω(r)=ω0·(r0/r)^0.75`，内环快外环慢；星尘/刻度/文档星同式 |
| kind 三分 | Canvas 内以「环上星尘 / 文档星 / 背景星」三层绘制 |
| 环架 | 四道倾斜大环（含竖直子午环），钢蓝细线 + 金屑；`ringTilt` 紧凑/标准/舒展 |
| 个性化 hooks | `ringTilt` 环架倾角、`galaxyDust` 星尘密度、`galaxySeed` 形态种子、`palette` 色调、`galaxyTilt` 环面倾角、`galaxyDimension` 维度线 |
| MY ASTERISM | `pins` 关注文档按顺序连成虚线星座 + 菱形端点 + `PIN n` 标注 |
| 观测者印 | 角标 `DOCUMENT CONSTELLATION · 41 STARS · 105 RELATIONS / AXON · PLATE 07 · LAYER · 7 RINGS` |

差异：GL 版的粒子流、辉光后处理、`uIntro` 进场显影未移植；改用 Canvas 路径合并与点刻控制性能，实测 95–119 FPS（1600×1000）。着色器段落保留为后续可选实现参考，不再默认加载。


---

## 8 · 维度 / 链条 / 光点优化（2026-10-30）

参考 `solar-system-atlas.pages.dev/solar/` 的维度设计（三维/图层/实时/时间线/多尺度），本轮在 Canvas 浑天仪上做四项增强：

- **星尘数量**：每环点刻 96 → 156（受 `galaxyDust` 控制），并新增环间浮尘场；点刻更密但保持细丝感；
- **数据链条**：新增 `galaxyChains`（关闭/细链/全链），按文档引用关系绘制带深度弧度的链条；悬停/选中端点时高亮，默认细链只画有向引用；
- **星系光点**：改为预烘焙精灵（按 tier 颜色缓存），双层辉光 + deg≥6 十字星芒 + 呼吸闪烁；新增 `galaxyPoint`（微光/标准/璀璨）；
- **维度线**：天轴/辐条/子午圈透明度约翻倍、线宽加粗；天轴加 `TOP/BASE` 与当前维度标签（层级纵深/tier/time…）；
- **维度打通**：切换纵深维度时，文档星高度做 620ms 缓动（与 3D 视图同一语义），不再瞬移；
- **流畅度**：环线 Path2D 合并、光点精灵缓存、路径批量绘制；实测 **114–118 FPS**（1600×1000）。


---

## 9 · 拖动偏移 / 链条重绘 / 流速（2026-10-30）

- **拖动偏移修复**：星系禁用鼠标指针视差（`lookTX/lookTY=0`），拖动结束不再调用 `computeGalFit()`（它会重算中心/缩放），并清零 `vry/vrx` 惯性；鼠标停在画布任意位置都不再拉偏相机。
- **数据链条重绘**：默认改为「悬停显示」——只画当前悬停/选中文档的引用链，暖金细弧 + 关联虚线；「全部细链」只画同层近邻且极低透明度，避免满屏乱线。选项：关闭 / 悬停显示 / 全部细链。
- **粒子流速**：`GALAXY.speed` 0.00034 → 0.000125（约 37%），默认自转明显放慢；「星系自转」慢/标准/快仍可调。
- 实测拖动后核心仍居中，104 FPS，无控制台错误。
