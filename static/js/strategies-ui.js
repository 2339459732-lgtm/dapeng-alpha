/**
 * DAPENG Alpha 策略选股系统 - 策略面板 UI 更新 v3
 *
 * v3.0 升级：
 * - 评分数字发光动效
 * - 策略条渐变发光
 * - K线形态标签入场动画
 * - 铁律通过/警告动态颜色
 * - 仓位环形指示器
 * - AI面板展示布林带/KDJ/多时间框架/风险量化/操作建议
 */

// ================================================================
//  方向配色（A 股惯例：红=涨/买/多/正，绿=跌/卖/空/负）
//  统一从 :root 主题变量读取（--red / --green），不写死 hex
// ================================================================
function dirColor(dir) {
  try {
    var v = (getComputedStyle(document.documentElement).getPropertyValue(dir === 'down' ? '--green' : '--red') || '').trim();
    return v || (dir === 'down' ? '#00e96a' : '#ff3d6a');
  } catch (e) { return dir === 'down' ? '#00e96a' : '#ff3d6a'; }
}
function dirRgba(dir, alpha) {
  var hex = String(dirColor(dir)).replace('#', '');
  if (hex.length === 3) hex = hex.charAt(0) + hex.charAt(0) + hex.charAt(1) + hex.charAt(1) + hex.charAt(2) + hex.charAt(2);
  var n = parseInt(hex, 16);
  if (hex.length !== 6 || isNaN(n)) return dirColor(dir);
  return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + alpha + ')';
}

// ================================================================
//  AI 分析面板状态管理
// ================================================================

/**
 * 显示 AI 分析加载状态 — v6.1 加固
 */
function showAnalysisLoading(msg) {
  try {
    const panel = document.getElementById('analysis-panel');
    const mini = document.getElementById('ai-decision-mini');
    if (mini) mini.style.display = 'none';
    if (!panel) { console.warn('analysis-panel not found in DOM'); return; }
    panel.style.display = 'block';
    panel.innerHTML = `
      <div class="analysis-loading">
        <span style="font-size:14px">AI</span>
        <span>${msg || 'AI 正在分析...'}</span>
        <div class="analysis-loading-dot"></div>
        <div class="analysis-loading-dot"></div>
        <div class="analysis-loading-dot"></div>
      </div>
    `;
    // 重新应用缩放
    if (typeof window._aiZoomReset === 'function') window._aiZoomReset();
  } catch (e) {
    console.warn('showAnalysisLoading failed:', e);
  }
}

/**
 * 显示 AI 分析错误状态 — v6.1 加固
 */
function showAnalysisError(msg) {
  try {
    const panel = document.getElementById('analysis-panel');
    const mini = document.getElementById('ai-decision-mini');
    if (mini) mini.style.display = 'none';
    if (!panel) { console.warn('analysis-panel not found in DOM'); return; }
    panel.style.display = 'block';
    panel.innerHTML = `
      <div class="analysis-error">
        <div class="analysis-error-icon">!</div>
        <div>${msg || 'AI 分析暂时不可用'}</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px">将使用量化系统分析结果</div>
      </div>
    `;
    if (typeof window._aiZoomReset === 'function') window._aiZoomReset();
  } catch (e) {
    console.warn('showAnalysisError failed:', e);
  }
}

// ================================================================
//  K 线形态识别
// ================================================================
function detectCandlePatterns(klines) {
  if (!klines || klines.length < 5) return [];

  const patterns = [];
  const last = klines[klines.length - 1];
  const prev = klines[klines.length - 2];

  const body = Math.abs(last.c - last.o);
  const upperShadow = last.h - Math.max(last.c, last.o);
  const lowerShadow = Math.min(last.c, last.o) - last.l;
  const totalRange = last.h - last.l;
  const isUp = last.c > last.o;
  const isDown = last.c < last.o;

  if (totalRange > 0 && body / totalRange < 0.1 && upperShadow > body * 2 && lowerShadow > body * 2) {
    patterns.push({ name: '十字星', type: 'neutral', desc: '多空平衡，变盘信号', icon: '✦' });
  }
  if (isDown && lowerShadow > body * 2 && upperShadow < body * 0.5) {
    patterns.push({ name: '锤子线', type: 'bullish', desc: '下跌中见底信号', icon: '🔨' });
  }
  if (isDown && upperShadow > body * 2 && lowerShadow < body * 0.5 && prev.c < prev.o) {
    patterns.push({ name: '倒锤子', type: 'bullish', desc: '可能反转上涨', icon: '⤴' });
  }
  if (isUp && upperShadow > body * 2 && lowerShadow < body * 0.5 && prev.c > prev.o) {
    patterns.push({ name: '射击之星', type: 'bearish', desc: '上涨受阻，见顶信号', icon: '⤵' });
  }
  if (prev && isUp && last.c > prev.o && last.o < prev.c) {
    patterns.push({ name: '看涨吞没', type: 'bullish', desc: '强力反转上涨信号', icon: '⬆' });
  }
  if (prev && isDown && last.o > prev.c && last.c < prev.o) {
    patterns.push({ name: '看跌吞没', type: 'bearish', desc: '反转下跌信号', icon: '⬇' });
  }
  const avgBody = klines.slice(-10).reduce((s, k) => s + Math.abs(k.c - k.o), 0) / 10;
  if (isUp && body > avgBody * 2) {
    patterns.push({ name: '大阳线', type: 'bullish', desc: '多方强势', icon: '🟢' });
  }
  if (isDown && body > avgBody * 2) {
    patterns.push({ name: '大阴线', type: 'bearish', desc: '空方强势', icon: '📭' });
  }
  if (klines.length >= 3) {
    const last3 = klines.slice(-3);
    if (last3.every(k => k.c > k.o)) {
      patterns.push({ name: '三连阳', type: 'bullish', desc: '连续上涨', icon: '📈' });
    }
    if (last3.every(k => k.c < k.o)) {
      patterns.push({ name: '三连阴', type: 'bearish', desc: '连续下跌', icon: '📉' });
    }
  }

  return patterns;
}

// ================================================================
//  策略面板更新 (动量/突破/量价/趋势/风险 + K线形态多维诊断)
// ================================================================
function updateStrategyPanel(strategyData, klineData) {
  if (!strategyData && !klineData) return;

  const strategyObj = (klineData && klineData.strategy) || {};
  const fallbackSignals = strategyObj.signals || [];
  const signals = (strategyData && strategyData.signals && strategyData.signals.length > 0)
    ? strategyData.signals
    : fallbackSignals;

  const momentumSignal = signals.find(s => s.type === 'momentum');
  const breakoutSignal = signals.find(s => s.type === 'breakout');
  const volumeSignal = signals.find(s => s.type === 'volume_divergence');

  const mScore = momentumSignal ? momentumSignal.score : (strategyObj.momentum_score != null ? strategyObj.momentum_score : (strategyData && strategyData.score ? strategyData.score : 55));
  const mDesc = momentumSignal ? momentumSignal.desc : (strategyObj.momentum_desc || (mScore >= 70 ? '中期多头动量加速' : mScore >= 50 ? '动量温和处于均线带上方' : '动量转弱承压调整'));

  const bScore = breakoutSignal ? breakoutSignal.score : (strategyObj.breakout_score != null ? strategyObj.breakout_score : (strategyData && strategyData.score ? Math.min(95, strategyData.score + 2) : 52));
  const bDesc = breakoutSignal ? breakoutSignal.desc : (strategyObj.breakout_desc || (bScore >= 70 ? '放量突破前期平台阻力' : bScore >= 50 ? '箱体震荡蓄势整理' : '跌破短期支撑位'));

  const vScore = volumeSignal ? volumeSignal.score : (strategyObj.volume_score != null ? strategyObj.volume_score : (strategyData && strategyData.score ? Math.max(45, strategyData.score - 4) : 50));
  const vDesc = volumeSignal ? volumeSignal.desc : (strategyObj.volume_desc || (vScore >= 65 ? '量价配合稳步抬升' : vScore >= 50 ? '温和换手筹码相对集中' : '量能背离存在换手洗盘'));

  // 动量策略
  animateScore('s-momentum', mScore);
  const mDescEl = document.getElementById('s-momentum-desc');
  const mBarEl = document.getElementById('s-m-bar');
  if (mDescEl) mDescEl.textContent = mDesc;
  if (mBarEl) {
    mBarEl.style.width = mScore + '%';
    mBarEl.style.background = mScore >= 70 ? 'linear-gradient(90deg,rgba(245,158,11,0.8),' + dirRgba('up', 0.9) + ')' :
                               mScore >= 50 ? 'linear-gradient(90deg,rgba(59,130,246,0.7),rgba(245,158,11,0.8))' :
                               'linear-gradient(90deg,' + dirRgba('down', 0.7) + ',rgba(245,158,11,0.6))';
  }

  // 突破策略
  animateScore('s-break', bScore);
  const bDescEl = document.getElementById('s-break-desc');
  const bBarEl = document.getElementById('s-b-bar');
  if (bDescEl) bDescEl.textContent = bDesc;
  if (bBarEl) {
    bBarEl.style.width = bScore + '%';
    bBarEl.style.background = bScore >= 70 ? 'linear-gradient(90deg,rgba(6,182,212,0.8),' + dirRgba('up', 0.9) + ')' :
                               bScore >= 50 ? 'linear-gradient(90deg,rgba(59,130,246,0.7),rgba(6,182,212,0.8))' :
                               'linear-gradient(90deg,' + dirRgba('down', 0.7) + ',rgba(6,182,212,0.6))';
  }

  // 量价策略
  animateScore('s-vol', vScore);
  const vDescEl = document.getElementById('s-vol-desc');
  const vBarEl = document.getElementById('s-v-bar');
  if (vDescEl) vDescEl.textContent = vDesc;
  if (vBarEl) {
    vBarEl.style.width = vScore + '%';
    vBarEl.style.background = vScore >= 65 ? 'linear-gradient(90deg,rgba(168,85,247,0.8),' + dirRgba('up', 0.9) + ')' :
                               vScore >= 50 ? 'linear-gradient(90deg,rgba(168,85,247,0.7),rgba(59,130,246,0.8))' :
                               'linear-gradient(90deg,' + dirRgba('down', 0.7) + ',rgba(168,85,247,0.6))';
  }

  // 综合评分
  const avgScore = Math.round(mScore * 0.35 + bScore * 0.3 + vScore * 0.35);

  // K 线形态识别
  const klines = klineData ? (klineData.klines || (Array.isArray(klineData) ? klineData : null)) : null;
  const patterns = detectCandlePatterns(klines);
  const patternEl = document.getElementById('pattern-panel');
  if (patternEl) {
    if (patterns.length > 0) {
      patternEl.innerHTML = patterns.map((p, i) => {
        const cls = p.type === 'bullish' ? 'ai-tag-success' :
                    p.type === 'bearish' ? 'ai-tag-error' : 'ai-tag-warning';
        return `<div class="ai-tag ${cls}" title="${p.desc}" style="display:inline-flex;align-items:center;gap:3px;margin:2px 3px;padding:3px 7px;border-radius:4px;font-size:10px;font-weight:600;animation:fadeIn .3s ease-out ${i * 80}ms both">${p.icon || ''} ${p.name} <span style="font-size:8px;opacity:0.85">${p.desc.substring(0, 6)}</span></div>`;
      }).join('');
    } else if (klines && klines.length >= 5) {
      const last = klines[klines.length - 1];
      const isUp = last && last.c >= last.o;
      patternEl.innerHTML = `<div class="ai-tag ${isUp ? 'ai-tag-success' : 'ai-tag-warning'}" style="display:inline-flex;align-items:center;gap:3px;margin:2px 3px;padding:3px 7px;border-radius:4px;font-size:10px;font-weight:600">📊 ${isUp ? '多头蓄势整理' : '空头弱势盘整'} <span style="font-size:8px;opacity:0.85">均线系统支撑</span></div>`;
    } else {
      patternEl.innerHTML = '<span style="font-size:10px;color:var(--muted)">等待行情K线...</span>';
    }
  }

  // 信号提示
  if (mScore > 75 && bScore > 75) {
    const name = window.appState ? window.appState.activeName : '';
    showToast('⚡ 买入信号', `${name} 动量+突破双强，评分 ${avgScore}`);
  }

  const bullishPatterns = patterns.filter(p => p.type === 'bullish');
  if (bullishPatterns.length >= 2) {
    const name = window.appState ? window.appState.activeName : '';
    showToast('📈 K线形态', `${name} 出现 ${bullishPatterns.map(p=>p.name).join('+')}`);
  }

  // ===== v4.0 新增：展示6大战法评分 =====
  const combinedScore = strategyData.combined_score || 0;
  const tacticScores = [
    { key: 'ma_bullish', label: '多头排列', score: strategyData.ma_bullish_score || 0 },
    { key: 'pullback', label: '强势回调', score: strategyData.pullback_score || 0 },
    { key: 'limit_up', label: '涨停战法', score: strategyData.limit_up_score || 0 },
    { key: 'bottom_reversal', label: '底部反转', score: strategyData.bottom_reversal_score || 0 },
    { key: 'dragon_head', label: '龙头战法', score: strategyData.dragon_head_score || 0 },
    { key: 'momentum_reversal', label: '动量反转', score: strategyData.momentum_reversal_score || 0 },
  ];

  const tacticEl = document.getElementById('tactics-panel');
  if (tacticEl) {
    tacticEl.innerHTML = tacticScores.map((t, i) => {
      const color = t.score >= 70 ? 'var(--red)' : t.score >= 50 ? 'var(--gold)' : 'var(--green)';
      const bg = t.score >= 70 ? dirRgba('up', 0.1) : t.score >= 50 ? 'rgba(245,158,11,0.1)' : dirRgba('down', 0.1);
      return `<div style="display:flex;justify-content:space-between;align-items:center;padding:2px 0;font-size:10px;animation:fadeIn .2s ease-out ${i*40}ms both;opacity:0">
        <span style="color:var(--text2)">${t.label}</span>
        <span style="color:${color};font-weight:600;font-family:'Courier New',monospace">${t.score}</span>
      </div>`;
    }).join('') + `
      <div style="margin-top:4px;padding-top:4px;border-top:1px solid rgba(255,255,255,0.04);display:flex;justify-content:space-between;align-items:center">
        <span style="font-size:9px;color:var(--muted)">综合战法评分</span>
        <span id="combined-score" style="font-size:13px;font-weight:700;font-family:'Courier New',monospace;color:${combinedScore>=70?'var(--red)':combinedScore>=50?'var(--gold)':'var(--green)'}">${combinedScore}</span>
      </div>`;
  }
}

// ================================================================
//  多周期共振分析 — v5.0：6周期指标共振 + 环形评分 + 卡片网格 + 光晕
// ================================================================

var _resonanceData = null; // 缓存最近一次检测结果

/**
 * 获取多周期共振数据（调后端 API）
 */
function detectResonance(stockData, klineData) {
  // 尝试复用已有 K 线数据的 code
  var code = (klineData && klineData.code) ? klineData.code
    : (stockData && stockData.code) ? stockData.code : null;
  if (!code) return null;
  return code;
}

/**
 * 渲染共振分析面板
 */
function updateLaws(stockData, klineData) {
  var el = document.getElementById('resonance-grid');
  if (!el) return;

  var code = (klineData && klineData.code) ? klineData.code
    : (stockData && stockData.code) ? stockData.code : null;
  if (!code) {
    el.innerHTML = '<div class="res-empty">请先选择股票</div>';
    return;
  }

  // 显示加载状态
  el.innerHTML = '<div class="res-loading"><span>计算多周期共振</span><div class="res-loading-dot"></div><div class="res-loading-dot"></div><div class="res-loading-dot"></div></div>';

  fetch('/api/multi-period-resonance?code=' + encodeURIComponent(code))
    .then(function(r) {
      if (!r.ok) throw new Error('API ' + r.status);
      return r.json();
    })
    .then(function(json) {
      if (!json.ok || !json.data) throw new Error('API empty');
      renderResonance(el, json.data);
    })
    .catch(function(e) {
      console.warn('Resonance fetch fail:', e.message);
      // 降级：使用已有 K 线数据本地计算简化共振
      var localData = computeLocalResonance(code, klineData);
      if (localData) {
        el.innerHTML = '';
        // 精简渲染：仅显示 K 线周期状态卡片
        var ph = localData.periods;
        var h = '<div class="res-fallback-banner">离线模式 · 基于缓存数据</div>';
        h += '<div class="res-grid">';
        for (var i = 0; i < ph.length; i++) {
          var pp = ph[i];
          var vc = pp.verdict === '🟢' ? 'card-bullish' : pp.verdict === '🔴' ? 'card-bearish' : 'card-neutral';
          var vi = pp.verdict === '🟢' ? '↑' : pp.verdict === '🔴' ? '↓' : '→';
          var vcl = pp.verdict === '🟢' ? 'verdict-bull' : pp.verdict === '🔴' ? 'verdict-bear' : 'verdict-neutral';
          h += '<div class="res-period-card ' + vc + '" style="animation-delay:' + (i*60) + 'ms">';
          h += '<div class="res-pc-header"><span class="res-pc-label">' + (pp.label || pp.period) + '</span><span class="res-pc-trend">' + vi + '</span></div>';
          h += '<div class="res-pc-footer"><span class="res-pc-bars">' + pp.bars + ' 根</span><span class="res-pc-verdict ' + vcl + '">' + pp.verdict + '</span></div>';
          h += '</div>';
        }
        h += '</div>';
        el.innerHTML = h;
      } else {
        el.innerHTML = '<div class="res-error">共振数据获取失败<br><small>' + e.message + '</small><br><small class="res-retry-hint">请稍后刷新重试</small></div>';
      }
    });
}

/**
 * 本地计算简化多周期共振（API 降级方案）
 * 从已有 K 线数据提取价格趋势和简单指标
 */
function computeLocalResonance(code, klineData) {
  if (!klineData || !klineData.klineRaw || !klineData.klineRaw.length) return null;

  var raw = klineData.klineRaw;
  // 只使用日K数据做本地估算
  var closes = [];
  for (var i = 0; i < raw.length; i++) {
    if (raw[i].close != null) closes.push(raw[i].close);
  }
  if (closes.length < 5) return null;

  var lastClose = closes[closes.length - 1];

  // 简单 MA 计算
  function ma(arr, n) {
    if (arr.length < n) return null;
    var sum = 0;
    for (var i = arr.length - n; i < arr.length; i++) sum += arr[i];
    return sum / n;
  }

  var ma5 = ma(closes, 5);
  var ma10 = ma(closes, 10);
  var ma20 = ma(closes, 20);

  // 判断多空
  function verdictBullBear() {
    if (ma5 == null) return '中';
    if (ma10 != null && ma5 > ma10 && lastClose > ma5) return '🟢';
    if (ma10 != null && ma5 < ma10 && lastClose < ma5) return '🔴';
    if (ma20 != null && lastClose > ma20) return '🟢';
    if (ma20 != null && lastClose < ma20) return '🔴';
    return '中';
  }

  // 构造周期数据
  var periods = [
    { period: '日K', label: '日K', bars: raw.length, verdict: verdictBullBear() },
  ];

  // 如果数据足够，模拟多周期（实际只有日K，但标记警告）
  if (closes.length >= 20) {
    periods.push({ period: '周估', label: '周估', bars: Math.floor(raw.length/5), verdict: verdictBullBear() });
  }
  periods.push({ period: '月估', label: '月估', bars: Math.floor(raw.length/22), verdict: lastClose > closes[0] ? '🟢' : '🔴' });

  return { periods: periods, resonance: { score: lastClose > closes[0] ? 65 : 35 } };
}

// ---- 渲染引擎 ----
function renderResonance(el, data) {
  var periods = data.periods || [];
  var resonance = data.resonance || {};
  var n = periods.length;

  if (n === 0) {
    el.innerHTML = '<div class="res-empty">无数据</div>';
    return;
  }

  _resonanceData = data;

  // === 颜色定义 ===
  var bull  = 'var(--res-bull)';
  var bear  = 'var(--res-bear)';
  var neut  = 'var(--res-neutral)';
  var muted = 'var(--muted)';

  var score = resonance.score || 0;
  var glowType = resonance.glow || 'neutral';
  var summary = resonance.summary || '';

  // 统计
  var bullCount = 0, bearCount = 0;
  for (var i = 0; i < n; i++) {
    if (periods[i].verdict === '🟢') bullCount++;
    else if (periods[i].verdict === '🔴') bearCount++;
  }

  // === Glow ===
  el.className = glowType + '-glow';

  // === 环颜色 ===
  var ringColor = bull;
  if (glowType === 'bearish' || glowType === 'strong_bearish') ringColor = bear;
  else if (glowType === 'neutral' || glowType === 'divergent') ringColor = neut;
  else if (glowType === 'slight_bearish') ringColor = bear;
  else if (glowType === 'slight_bullish') ringColor = bull;

  // === SVG 环 ===
  var circumference = 194.779; // r=31
  var dashOffset = circumference * (1 - score / 100);
  var ringColorHex = ringColor;
  // 从 var() 提取实际颜色
  if (ringColorHex === 'var(--res-bull)') ringColorHex = '#00E676';
  else if (ringColorHex === 'var(--res-bear)') ringColorHex = '#FF5252';
  else if (ringColorHex === 'var(--res-neutral)') ringColorHex = '#5C6BC0';

  var html = '';

  // === Hero 区 ===
  html += '<div class="res-hero">';
  html += '<div class="res-ring-wrap">';
  html += '<svg class="res-ring" viewBox="0 0 72 72">';
  html += '<circle cx="36" cy="36" r="31" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="4"/>';
  html += '<circle cx="36" cy="36" r="31" fill="none" stroke="' + ringColorHex + '" stroke-width="4" '
    + 'stroke-linecap="round" stroke-dasharray="' + circumference + '" '
    + 'stroke-dashoffset="' + circumference + '" class="res-ring-fill" '
    + 'data-target="' + dashOffset + '" '
    + 'style="transform:rotate(-90deg);transform-origin:center;transition:stroke-dashoffset 0.8s cubic-bezier(0.34,1.56,0.64,1);"/>';
  html += '</svg>';
  html += '<div class="res-ring-value" style="color:' + ringColorHex + '">';
  html += '<span class="res-ring-num" data-score="' + score + '">0</span>';
  html += '<span class="res-ring-pct">分</span>';
  html += '</div>';
  html += '</div>';

  html += '<div class="res-hero-info">';
  html += '<div class="res-hero-title" style="color:' + ringColorHex + '">' + summary + '</div>';
  html += '<div class="res-hero-stats">';
  html += '<span class="res-hero-stat" style="color:' + bull + '"><b>' + bullCount + '</b> 偏多</span>';
  html += '<span class="res-hero-stat" style="color:' + bear + '"><b>' + bearCount + '</b> 偏空</span>';
  html += '<span class="res-hero-stat" style="color:' + muted + '"><b>' + (n - bullCount - bearCount) + '</b> 中性</span>';
  html += '</div>';
  html += '</div>';
  html += '</div>';

  // === 周期卡片网格 ===
  html += '<div class="res-grid">';
  for (var p = 0; p < n; p++) {
    var pd = periods[p];
    var ver = pd.verdict;
    var cardClass = 'card-neutral';
    var trendClass = 'trend-flat';
    var trendIcon = '→';
    var verdictClass = 'verdict-neutral';

    if (pd.status === 'insufficient') {
      cardClass = 'card-insufficient';
      trendClass = 'trend-none';
      trendIcon = '—';
    } else if (ver === '🟢') {
      cardClass = 'card-bullish';
      trendClass = 'trend-up';
      trendIcon = '↑';
      verdictClass = 'verdict-bull';
    } else if (ver === '🔴') {
      cardClass = 'card-bearish';
      trendClass = 'trend-down';
      trendIcon = '↓';
      verdictClass = 'verdict-bear';
    }

    // 指标灯：MA / MACD / KDJ / RSI
    function dotCls(sig) {
      if (sig === 'bullish' || sig === 'oversold') return 'dot-bull';
      if (sig === 'bearish' || sig === 'overbought') return 'dot-bear';
      if (sig === 'neutral') return 'dot-neutral';
      return 'dot-none';
    }

    html += '<div class="res-period-card ' + cardClass + '" style="animation-delay:' + (p * 60) + 'ms">';
    html += '<div class="res-pc-header">';
    html += '<span class="res-pc-label">' + (pd.label || pd.period) + '</span>';
    html += '<span class="res-pc-trend ' + trendClass + '">' + trendIcon + '</span>';
    html += '</div>';
    html += '<div class="res-dots">';
    html += '<span class="res-dot ' + dotCls(pd.ma_trend) + '" title="MA"></span>';
    html += '<span class="res-dot ' + dotCls(pd.macd_signal) + '" title="MACD"></span>';
    html += '<span class="res-dot ' + dotCls(pd.kdj_zone) + '" title="KDJ"></span>';
    html += '<span class="res-dot ' + dotCls(pd.rsi_zone) + '" title="RSI"></span>';
    html += '<span class="res-dot-label">MA MACD KDJ RSI</span>';
    html += '</div>';
    html += '<div class="res-pc-footer">';
    html += '<span class="res-pc-bars">' + pd.bars + ' 根</span>';
    html += '<span class="res-pc-verdict ' + verdictClass + '">' + ver + '</span>';
    html += '</div>';
    html += '</div>';
  }
  html += '</div>';

  // === 底部信号摘要 ===
  var sigIcon = '', sigText = '';
  if (glowType === 'strong_bullish' || glowType === 'bullish') {
    sigIcon = '&#x1F7E2;';
    sigText = '<strong>多头共振信号：</strong>多周期指标趋向一致，趋势延续概率较高，可关注顺势入场机会。';
  } else if (glowType === 'strong_bearish' || glowType === 'bearish') {
    sigIcon = '&#x1F534;';
    sigText = '<strong>空头共振信号：</strong>多周期同步偏空，短期承压概率较大，建议减仓或观望。';
  } else if (glowType === 'slight_bullish') {
    sigIcon = '&#x1F7E1;';
    sigText = '<strong>短线偏多：</strong>部分周期转多，短线或有反弹机会，适合轻仓试探。';
  } else if (glowType === 'slight_bearish') {
    sigIcon = '&#x1F7E0;';
    sigText = '<strong>短线偏空：</strong>空方略占优势，建议谨慎操作、控制仓位。';
  } else {
    sigIcon = '&#x1F535;';
    sigText = '<strong>周期分歧：</strong>不同周期方向不一致，短期方向不明，宜观望等待共振。';
  }

  html += '<div class="res-signature">';
  html += '<span class="res-signature-icon">' + sigIcon + '</span>';
  html += '<span class="res-signature-text">' + sigText + '</span>';
  html += '</div>';

  el.innerHTML = html;

  // 动画
  requestAnimationFrame(function() {
    var ringFill = el.querySelector('.res-ring-fill');
    if (ringFill) {
      ringFill.style.strokeDashoffset = ringFill.getAttribute('data-target');
    }
    var numEl = el.querySelector('.res-ring-num');
    if (numEl) {
      animateLawsScore(numEl, score);
    }
  });
}

// ---- 数字动画（复用旧版函数名保持兼容） ----
function animateLawsScore(el, target) {
  var cur = 0;
  var step = Math.max(1, Math.ceil(target / 30));
  var timer = setInterval(function() {
    cur += step;
    if (cur >= target) { cur = target; clearInterval(timer); }
    el.textContent = cur;
  }, 25);
}

// 旧版 toggle 函数留空桩，避免报错
function toggleLawDetail() {}
function toggleAllLaws() {}
var _ironLawsData = [];

// ================================================================
//  评分动画
// ================================================================
function animateScore(id, target) {
  const el = document.getElementById(id);
  if (!el) return;
  let current = parseInt(el.textContent) || 0;
  const step = (target - current) / 15;
  if (Math.abs(step) < 0.5) {
    el.textContent = target;
    applyScoreGlow(el, target);
    return;
  }
  const timer = setInterval(() => {
    current += step;
    if ((step > 0 && current >= target) || (step < 0 && current <= target)) {
      current = target;
      clearInterval(timer);
      applyScoreGlow(el, target);
    }
    el.textContent = Math.round(current);
  }, 20);
}

function applyScoreGlow(el, score) {
  const color = score >= 70 ? 'var(--red)' : score >= 50 ? 'var(--gold)' : 'var(--green)';
  const glowColor = score >= 70 ? dirRgba('up', 0.3) : score >= 50 ? 'rgba(245,158,11,0.3)' : dirRgba('down', 0.3);
  el.style.color = color;
  el.style.textShadow = `0 0 8px ${glowColor}`;
}

// ================================================================
//  AI 分析总结面板 — v4.0 现代化组件版
// ================================================================
function updateAnalysisPanel(data) {
  const panel = document.getElementById('analysis-panel');
  if (!panel || !data) return;

  try {
    const et = s => escapeHtml(s);
    const safeTrend = et(data.trend || '分析中');
    const safeRisk = et(data.risk_level || '未知');
    const safeAction = et(data.action || '观望');
    const safeSummary = et(data.summary || '');

    const trendColor = safeTrend.includes('涨') ? 'var(--red)' :
                       safeTrend.includes('跌') ? 'var(--green)' : 'var(--gold)';
    // 风险等级：红=高风险（风险语义，与涨跌方向无关），保持
    const riskColor = safeRisk === '高风险' ? 'var(--red)' :
                      safeRisk === '中风险' ? 'var(--gold)' : 'var(--green)';
    const actionColor = (safeAction.includes('买入') || safeAction.includes('积极')) ? 'var(--red)' :
                        (safeAction.includes('减仓') || safeAction.includes('离场')) ? 'var(--green)' : 'var(--gold)';

    // 1. AI 核心决策组件 (DeepSeek 增强)
    let aiDecisionHtml = '';
    if (data.ai_decision) {
      const ai = data.ai_decision;
      const safeAiDecision = et(ai.decision || '观望');
      const safeAiSummary = et(ai.summary || '');
      const safeAiReason = et(ai.key_reason || '');
      const safeAiTrend = et(ai.trend_judgment || '--');
      const safeAiRisk = et(ai.risk_alert || '');

      const decisionColor = safeAiDecision === '买入' ? 'var(--red)' :
                            safeAiDecision === '卖出' ? 'var(--green)' :
                            safeAiDecision === '持有' ? 'var(--cyan)' : 'var(--gold)';
      const decisionBg = safeAiDecision === '买入' ? dirRgba('up', 0.1) :
                         safeAiDecision === '卖出' ? dirRgba('down', 0.1) :
                         safeAiDecision === '持有' ? 'rgba(6,182,212,0.1)' : 'rgba(245,158,11,0.1)';
      
      const cf = ai.confidence || 0;
      // 置信度=非方向（质量），用中性强调色，避免红绿被读成涨跌
      const cfColor = cf >= 75 ? 'var(--cyan)' : cf >= 50 ? 'var(--gold)' : 'var(--muted)';
      const entryPrice = ai.entry_price ? `${safeFixed(ai.entry_price.low)}-${safeFixed(ai.entry_price.high)}` : '--';
      const stopProfit = ai.take_profit ? `①${safeFixed(ai.take_profit.first)} ②${safeFixed(ai.take_profit.second)}` : '--';

      // 本块配色约定：「建议入场」=买方向 → 红；「止损参考」=风险语义 → 红（非涨跌）
      aiDecisionHtml = `
        <div class="ai-decision-card" style="background:${decisionBg}; border-color:${decisionColor}40">
          <div class="ai-decision-header" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px">
            <div class="ai-decision-badge" style="background:${decisionColor}25; color:${decisionColor}; padding:4px 12px; border-radius:8px; border:1px solid ${decisionColor}40; font-weight:800; font-size:14px">
              AI ${safeAiDecision}
            </div>
            <button class="btn-execute" onclick="event.stopPropagation(); showTradeDialog('${safeAiDecision === '卖出' ? 'sell' : 'buy'}')" style="background:${decisionColor}; color:#000; border:none; padding:4px 12px; border-radius:6px; font-weight:700; cursor:pointer; font-size:12px">
              快速执行
            </button>
            <div class="ai-confidence" style="text-align:right">
              <div style="font-size:16px; font-weight:800; color:${cfColor}; font-family:monospace">${cf}%</div>
              <div style="font-size:9px; color:var(--text2)">置信度</div>
            </div>
          </div>
          <div class="ai-summary" style="font-size:12px; font-weight:600; margin-bottom:8px; color:var(--text)">${safeAiSummary}</div>
          <div class="ai-reason" style="font-size:11px; color:var(--text2); margin-bottom:10px; line-height:1.4">💡 ${safeAiReason}</div>
          ${safeAiRisk ? `<div class="ai-risk-alert" style="background:rgba(255,61,106,0.1); color:var(--red); padding:6px 10px; border-radius:6px; font-size:10px; margin-bottom:12px">⚠️ ${safeAiRisk}</div>` : ''}
          
          <div class="ai-grid">
            <div class="ai-grid-item">
              <div class="ai-grid-label">建议入场</div>
              <div class="ai-grid-value" style="color:var(--red)">${entryPrice}</div>
            </div>
            <div class="ai-grid-item">
              <div class="ai-grid-label">止损参考</div>
              <div class="ai-grid-value" style="color:var(--red)">${safeFixed(ai.stop_loss)}</div>
            </div>
            <div class="ai-grid-item">
              <div class="ai-grid-label">止盈目标</div>
              <div class="ai-grid-value" style="color:var(--gold)">${stopProfit}</div>
            </div>
            <div class="ai-grid-item">
              <div class="ai-grid-label">建议仓位</div>
              <div class="ai-grid-value" style="color:var(--cyan)">${ai.position_pct || 0}%</div>
            </div>
          </div>
        </div>
      `;
    }

    // 2. 量化研判面板
    const quantPanelHtml = `
      <div class="ai-component-card">
        <div class="mini-title" style="margin-bottom:10px">📊 量化系统研判</div>
        <div style="display:flex; gap:8px; margin-bottom:10px">
          <div class="analysis-tag" style="border:1px solid ${trendColor}40; color:${trendColor}; background:${trendColor}10; padding:2px 8px; border-radius:4px; font-size:10px">趋势: ${safeTrend}</div>
          <div class="analysis-tag" style="border:1px solid ${riskColor}40; color:${riskColor}; background:${riskColor}10; padding:2px 8px; border-radius:4px; font-size:10px">风险: ${safeRisk}</div>
          <div class="analysis-tag" style="border:1px solid ${actionColor}40; color:${actionColor}; background:${actionColor}10; padding:2px 8px; border-radius:4px; font-size:10px">操作: ${safeAction}</div>
        </div>
        <div style="font-size:11px; color:var(--text2); line-height:1.5">${safeSummary}</div>
      </div>
    `;

    // 3. 支撑阻力组件
    let srHtml = '';
    if (data.support_resistance && data.support_resistance.length > 0) {
      const items = data.support_resistance.slice(0, 4).map(sr => {
        const isRes = sr.type === '阻力';
        const color = isRes ? 'var(--red)' : 'var(--green)';
        return `
          <div style="display:flex; justify-content:space-between; align-items:center; padding:4px 0">
            <span style="color:var(--text2); font-size:10px">${et(sr.type)}</span>
            <span style="font-family:monospace; font-weight:700; color:${color}">${safeFixed(sr.price)}</span>
          </div>
        `;
      }).join('');
      srHtml = `
        <div class="ai-component-card">
          <div class="mini-title" style="margin-bottom:6px">🎯 支撑与阻力</div>
          ${items}
        </div>
      `;
    }

    // 4. 技术指标聚合组件
    let techHtml = '';
    if (data.boll || data.kdj || data.macd_detail) {
      const boll = data.boll ? `<div style="font-size:10px; color:var(--text2)">BOLL: <span style="color:var(--gold)">${et(data.boll.signal)}</span></div>` : '';
      const kdj = data.kdj ? `<div style="font-size:10px; color:var(--text2)">KDJ: <span style="color:var(--cyan)">${et(data.kdj.signal)}</span></div>` : '';
      const macd = data.macd_detail ? `<div style="font-size:10px; color:var(--text2)">MACD: <span style="color:var(--blue)">${et(data.macd_detail.signal)}</span></div>` : '';
      
      techHtml = `
        <div class="ai-component-card">
          <div class="mini-title" style="margin-bottom:8px">⚡ 技术指标概览</div>
          <div style="display:grid; grid-template-columns: 1fr 1fr; gap:6px">
            ${boll} ${kdj} ${macd}
          </div>
        </div>
      `;
    }

    // v5.0: 完整分析内容缓存为共享片段 —— 内联摘要模式与大屏模式共用同一数据源
    // v5.1: #ai-fs-body 本身就是两列网格 —— 各卡片作为直接子项自动流入
    // （决策卡跨双列居首，量化研判/支撑压力、技术指标/关注点两两并排），
    // 不再嵌套内层 grid，避免右列空置造成的稀疏观感
    _aiAnalysisSnapshot = data;
    // AI 增强决策缺失时的占位说明（未配置 DEEPSEEK_API_KEY 或模型超时），
    // 避免用户误以为决策卡渲染出错
    const aiPendingHtml = data.ai_decision ? '' : `
      <div class="ai-component-card ai-fs-pending">
        <div class="mini-title" style="margin-bottom:6px">🤖 AI 增强决策</div>
        <div style="font-size:11px; color:var(--text2); line-height:1.6">暂未获取到 AI 增强决策（服务端未配置 DeepSeek API Key 或模型未响应）。以下为量化系统研判结果；配置后 AI 决策卡（入场区间/止损/止盈/仓位）将自动出现在此处。</div>
      </div>
    `;
    const fullHtml = `
      ${aiDecisionHtml}
      ${aiPendingHtml}
      ${quantPanelHtml}
      ${srHtml}
      ${techHtml}
      ${data.key_points && data.key_points.length > 0 ? `
        <div class="ai-component-card">
          <div class="mini-title" style="margin-bottom:6px">🔍 核心关注点</div>
          ${data.key_points.map(kp => `<div style="font-size:10px; color:var(--text2); margin-bottom:3px">• ${et(kp)}</div>`).join('')}
        </div>
      ` : ''}
    `;
    _aiFullHtml = fullHtml;

    // v5.0 布局重构：内联区只保留"一眼可读"的摘要（决策徽章 + 置信度 + 关键价位 + 三态标签），
    // 完整版（决策卡/量化研判/支撑阻力/技术指标/关注点）移入全屏大屏模式
    const ai = data.ai_decision || {};
    const decision = et(ai.decision || safeAction || '观望');
    const decisionColor = decision === '买入' ? 'var(--red)' :
                          decision === '卖出' ? 'var(--green)' :
                          decision === '持有' ? 'var(--cyan)' : 'var(--gold)';
    const cf = ai.confidence || 0;
    // 置信度=非方向（质量），用中性强调色
    const cfColor = cf >= 75 ? 'var(--cyan)' : cf >= 50 ? 'var(--gold)' : 'var(--muted)';
    const entryTxt = ai.entry_price ? `${safeFixed(ai.entry_price.low)}-${safeFixed(ai.entry_price.high)}` : '--';
    const keyPoint = (data.key_points && data.key_points.length) ? et(data.key_points[0]) : safeSummary;

    // 本块配色约定：「建议入场」=买方向 → 红；「止损」=风险语义 → 红（非涨跌）
    panel.innerHTML = `
      <div class="ai-inline-summary">
        <div class="ai-inline-top">
          <span class="ai-inline-badge" style="color:${decisionColor};border-color:${decisionColor}55;background:${decisionColor}18">${data.ai_decision ? 'AI ' : '研判 '}${decision}</span>
          <span class="ai-inline-conf" style="color:${cfColor}">${cf}%<small>置信</small></span>
          <button class="ai-inline-expand" type="button" onclick="openAIFullscreen()" title="全屏查看完整 AI 分析">⛶ 大屏查看</button>
        </div>
        <div class="ai-inline-tags">
          <span style="color:${trendColor};border-color:${trendColor}40">趋势 ${safeTrend}</span>
          <span style="color:${riskColor};border-color:${riskColor}40">风险 ${safeRisk}</span>
          <span style="color:${actionColor};border-color:${actionColor}40">操作 ${safeAction}</span>
        </div>
        <div class="ai-inline-kv">
          <div><small>建议入场</small><b style="color:var(--red)">${entryTxt}</b></div>
          <div><small>止损</small><b style="color:var(--red)">${ai.stop_loss ? safeFixed(ai.stop_loss) : '--'}</b></div>
          <div><small>仓位</small><b style="color:var(--cyan)">${ai.position_pct || 0}%</b></div>
        </div>
        <div class="ai-inline-line" title="${keyPoint}">${keyPoint}</div>
      </div>
    `;

    panel.style.display = 'block';
    updateAIDecisionMini(data);
    // 大屏已打开时同步刷新其内容，避免展示陈旧分析
    syncAIFullscreenIfOpen();

  } catch (e) {
    console.error('updateAnalysisPanel Error:', e);
    showAnalysisError('AI 渲染异常');
  }
}

// ================================================================
//  AI 实时分析 · 全屏大屏模式（v5.0）
//  内联区保留摘要供快速浏览，点击"大屏查看"进入沉浸式阅读
// ================================================================
var _aiFullHtml = '';
var _aiAnalysisSnapshot = null;
var _aiFsPrevOverflow = '';

/** 打开大屏模式：注入最新完整分析 + 锁定背景滚动 + 支持 ESC 关闭 */
function openAIFullscreen() {
  var overlay = document.getElementById('ai-fullscreen-panel');
  if (!overlay) return;
  var body = document.getElementById('ai-fs-body');
  var meta = document.getElementById('ai-fs-meta');
  if (body) {
    body.innerHTML = _aiFullHtml || '<div class="ai-fs-empty">暂无分析数据，请先选择股票</div>';
  }
  if (meta) {
    var snap = _aiAnalysisSnapshot || {};
    var nameEl = document.getElementById('k-name');
    var title = (nameEl && nameEl.textContent) ? nameEl.textContent.trim() : '';
    var upd = snap.updated_at || snap.timestamp || '';
    meta.textContent = [title, upd ? ('更新 ' + upd) : ''].filter(Boolean).join(' · ');
  }
  overlay.style.display = 'flex';
  overlay.setAttribute('aria-hidden', 'false');
  _aiFsPrevOverflow = document.body.style.overflow;
  document.body.style.overflow = 'hidden';
  var closeBtn = overlay.querySelector('.full-overlay-close');
  if (closeBtn) closeBtn.focus();
  if (typeof window._aiZoomReset === 'function') window._aiZoomReset();
}

/** 关闭大屏模式：恢复滚动与焦点 */
function closeAIFullscreen() {
  var overlay = document.getElementById('ai-fullscreen-panel');
  if (!overlay) return;
  overlay.style.display = 'none';
  overlay.setAttribute('aria-hidden', 'true');
  document.body.style.overflow = _aiFsPrevOverflow || '';
  var trigger = document.querySelector('.ai-inline-expand');
  if (trigger) trigger.focus();
}

function toggleAIFullscreen() {
  var overlay = document.getElementById('ai-fullscreen-panel');
  if (!overlay) return;
  if (overlay.style.display === 'flex') closeAIFullscreen();
  else openAIFullscreen();
}

/** 大屏打开状态下，数据刷新时同步重渲染（保持阅读连续性） */
function syncAIFullscreenIfOpen() {
  var overlay = document.getElementById('ai-fullscreen-panel');
  if (!overlay || overlay.style.display !== 'flex') return;
  var body = document.getElementById('ai-fs-body');
  if (body && _aiFullHtml) body.innerHTML = _aiFullHtml;
}

// ESC 关闭 + 遮罩点击关闭
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' || e.key === 'Esc') {
    var overlay = document.getElementById('ai-fullscreen-panel');
    if (overlay && overlay.style.display === 'flex') {
      e.preventDefault();
      closeAIFullscreen();
    }
  }
});

window.openAIFullscreen = openAIFullscreen;
window.closeAIFullscreen = closeAIFullscreen;
window.toggleAIFullscreen = toggleAIFullscreen;


// ================================================================
//  v5.0: 板块分析面板数据更新 — 调用市场数据模块并发加载
// ================================================================

/**
 * 更新板块分析面板 — 触发MCP市场数据并发加载
 * 调用 loadSectorAnalysisData() 并发获取：板块排行/龙虎榜/市场总览/热门股
 */
function updateSectorAnalysisPanel() {
  if (typeof loadSectorAnalysisData === 'function') {
    loadSectorAnalysisData(window.appState ? window.appState.activeSector : 'semiconductor');
  }
}

/**
 * 更新K线头部价格的AI决策迷你标签
 */
function updateAIDecisionMini(data) {
  const mini = document.getElementById('ai-decision-mini');
  if (!mini) return;

  const ai = data.ai_decision;
  if (!ai || !ai.decision) {
    mini.style.display = 'none';
    return;
  }

  const decision = ai.decision;
  const confidence = ai.confidence || 0;
  const colors = {
    '买入': { color: 'var(--red)', bg: dirRgba('up', 0.1), border: dirRgba('up', 0.3) },
    '卖出': { color: 'var(--green)', bg: dirRgba('down', 0.1), border: dirRgba('down', 0.3) },
    '持有': { color: 'var(--cyan)', bg: 'rgba(6,182,212,0.1)', border: 'rgba(6,182,212,0.3)' },
    '观望': { color: 'var(--gold)', bg: 'rgba(245,158,11,0.1)', border: 'rgba(245,158,11,0.3)' },
  };
  const c = colors[decision] || colors['观望'];

  mini.innerHTML = `
    <span class="ai-mini-tag animate-pop" style="background:${c.bg};color:${c.color};border:1px solid ${c.border}">
      AI ${decision} ${confidence}%
    </span>
  `;
  mini.style.display = 'inline-flex';
}

// ================================================================
//  全球情绪面板
// ================================================================
function updateGlobalPanel(data) {
  if (!data) return;

  const panel = document.getElementById('global-panel');
  if (!panel) return;

  const items = [];

  if (data.overseas) {
    if (data.overseas.gold) {
      items.push({ key: 'gold', d: data.overseas.gold, label: '黄金' });
    }
    if (data.overseas.ndx) {
      items.push({ key: 'ndx', d: data.overseas.ndx, label: '纳指' });
    }
  }

  panel.innerHTML = items.map(i => {
    const d = i.d;
    if (!d) return '';
    const chg = d.chg || 0;
    const cls = chg > 0 ? 'up' : chg < 0 ? 'dn' : 'flat';
    const sign = chg > 0 ? '+' : '';
    const arrow = chg > 0 ? '▲' : chg < 0 ? '▼' : '—';
    return `
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-size:10px;color:var(--text2)">${d.name || i.label}</span>
        <span class="${cls}" style="font-size:11px;font-weight:600;font-family:'Courier New',monospace">${arrow} ${sign}${chg.toFixed(2)}%</span>
      </div>
    `;
  }).join('');

  if (data.overseas) {
    const nd = data.overseas.ndx;
    const gc = data.overseas.gold;

    if (nd) {
      const sign = nd.chg > 0 ? '+' : '';
      const cls = nd.chg > 0 ? 'up' : nd.chg < 0 ? 'dn' : 'flat';
      const ndPEl = document.getElementById('nd-p');
      const ndCEl = document.getElementById('nd-c');
      if (ndPEl) ndPEl.textContent = (nd.price || 0).toFixed(0);
      if (ndCEl) {
        ndCEl.textContent = sign + (nd.chg || 0).toFixed(2) + '%';
        ndCEl.className = 'idx-chg ' + cls;
      }
    }
    if (gc) {
      const sign = gc.chg > 0 ? '+' : '';
      const cls = gc.chg > 0 ? 'up' : gc.chg < 0 ? 'dn' : 'flat';
      const gcPEl = document.getElementById('gc-p');
      const gcCEl = document.getElementById('gc-c');
      if (gcPEl) gcPEl.textContent = (gc.price || 0).toFixed(0);
      if (gcCEl) {
        gcCEl.textContent = sign + (gc.chg || 0).toFixed(2) + '%';
        gcCEl.className = 'idx-chg ' + cls;
      }
    }
  }
}

// ================================================================
//  v5.2 技术指标紧凑卡 — MACD + 仓位建议
//  数据源：klineResp.macd (dif/dea/hist 数组) + klineResp.strategy
// ================================================================

/**
 * 更新技术指标紧凑卡（MACD 柱状值 + DIF/DEA + 金叉/死叉标签 + 仓位进度条）
 *
 * @param {Object} klineData - K线响应数据，包含 macd 数组和 strategy 对象
 */
function updateTechIndicators(klineData) {
  if (!klineData) return;

  const strategy = klineData.strategy || {};
  const macd = klineData.macd || {};

  // ── MACD：dif/dea/hist 是数组，取最后一个有效值 ──
  const difArr = macd.dif || [];
  const deaArr = macd.dea || [];
  const histArr = macd.hist || [];

  const lastNum = function (arr) {
    if (!arr || !arr.length) return 0;
    var v = arr[arr.length - 1];
    return typeof v === 'number' ? v : (parseFloat(v) || 0);
  };

  var dif = lastNum(difArr);
  var dea = lastNum(deaArr);
  var hist = lastNum(histArr);
  // hist 为 0 且 dif/dea 有值时，用 dif-dea 兜底
  if (hist === 0 && (dif !== 0 || dea !== 0)) {
    hist = dif - dea;
  }

  var macdValEl = document.getElementById('macd-val');
  var macdDifEl = document.getElementById('macd-dif');
  var macdDeaEl = document.getElementById('macd-dea');
  var macdTagEl = document.getElementById('macd-signal-tag');

  if (macdValEl) {
    var histText = (hist >= 0 ? '+' : '') + hist.toFixed(3);
    macdValEl.textContent = histText;
    macdValEl.style.color = hist >= 0 ? 'var(--red)' : 'var(--green)';
  }
  if (macdDifEl) macdDifEl.textContent = dif.toFixed(3);
  if (macdDeaEl) macdDeaEl.textContent = dea.toFixed(3);

  if (macdTagEl) {
    if (hist > 0 && dif > dea) {
      macdTagEl.textContent = '金叉';
      macdTagEl.className = 'tech-signal bull';
    } else if (hist < 0 && dif < dea) {
      macdTagEl.textContent = '死叉';
      macdTagEl.className = 'tech-signal bear';
    } else {
      macdTagEl.textContent = '震荡';
      macdTagEl.className = 'tech-signal neutral';
    }
  }

  // ── 仓位建议 ──
  var posPct = strategy.position_pct != null ? strategy.position_pct : 0;
  var stopLoss = strategy.stop_loss != null ? strategy.stop_loss : 0;
  var atr14 = strategy.atr14 != null ? strategy.atr14 : 0;

  var posPctEl = document.getElementById('pos-pct');
  var posBarEl = document.getElementById('pos-bar');
  var posDescEl = document.getElementById('pos-desc');

  if (posPctEl) {
    posPctEl.textContent = posPct + '%';
    // 建议仓位=仓位量（非方向），用中性色，避免红绿被读成涨跌
    posPctEl.style.color = posPct >= 70 ? 'var(--cyan)' :
                           posPct >= 40 ? 'var(--gold)' : 'var(--muted)';
  }
  if (posBarEl) {
    posBarEl.style.width = Math.min(posPct, 100) + '%';
  }
  if (posDescEl) {
    if (stopLoss > 0) {
      var parts = ['止损 ' + stopLoss.toFixed(2)];
      if (atr14 > 0) parts.push('ATR ' + atr14.toFixed(3));
      posDescEl.textContent = parts.join(' · ');
    } else {
      posDescEl.textContent = '基于ATR止损';
    }
  }
}

// ================================================================
//  AI 分析区域缩放功能
// ================================================================
(function() {
  var ZOOM_KEY = 'dapeng_ai_analysis_zoom';
  var ZOOM_MIN = 80;
  var ZOOM_MAX = 200;
  var ZOOM_STEP = 10;
  var ZOOM_DEFAULT = 100;

  var currentZoom = ZOOM_DEFAULT;
  var _initDone = false;

  function loadZoom() {
    try {
      var v = parseInt(localStorage.getItem(ZOOM_KEY), 10);
      if (v >= ZOOM_MIN && v <= ZOOM_MAX) return v;
    } catch(e) {}
    return ZOOM_DEFAULT;
  }

  function saveZoom(v) {
    try { localStorage.setItem(ZOOM_KEY, String(v)); } catch(e) {}
  }

  function applyZoom(zoom) {
    var panel = document.getElementById('analysis-panel');
    if (panel) {
      const scale = zoom / 100;
      panel.style.transform = `scale(${scale})`;
      // 调整 margin 以补偿缩放后的空间偏移
      panel.style.marginBottom = `-${(1 - scale) * panel.offsetHeight}px`;
      panel.style.marginRight = `-${(1 - scale) * panel.offsetWidth}px`;
    }
    // 同步更新 mini-decision 的容器
    var mini = document.getElementById('ai-decision-mini');
    if (mini) {
      mini.style.transform = `scale(${zoom / 100})`;
    }
  }

  function updateUI(zoom) {
    var label = document.getElementById('ai-zoom-label');
    if (label) {
      label.textContent = zoom + '%';
      // 超出默认范围时高亮
      if (zoom > 100) {
        label.style.color = 'var(--green)';
      } else if (zoom < 100) {
        label.style.color = 'var(--gold)';
      } else {
        label.style.color = 'var(--blue)';
      }
    }
    // 按钮禁用态
    var btnOut = document.getElementById('ai-zoom-out');
    var btnIn = document.getElementById('ai-zoom-in');
    if (btnOut) btnOut.style.opacity = zoom <= ZOOM_MIN ? '0.3' : '1';
    if (btnIn) btnIn.style.opacity = zoom >= ZOOM_MAX ? '0.3' : '1';
  }

  function zoomIn() {
    if (currentZoom >= ZOOM_MAX) return;
    currentZoom = Math.min(currentZoom + ZOOM_STEP, ZOOM_MAX);
    saveZoom(currentZoom);
    applyZoom(currentZoom);
    updateUI(currentZoom);
  }

  function zoomOut() {
    if (currentZoom <= ZOOM_MIN) return;
    currentZoom = Math.max(currentZoom - ZOOM_STEP, ZOOM_MIN);
    saveZoom(currentZoom);
    applyZoom(currentZoom);
    updateUI(currentZoom);
  }

  function zoomReset() {
    currentZoom = ZOOM_DEFAULT;
    saveZoom(currentZoom);
    applyZoom(currentZoom);
    updateUI(currentZoom);
  }

  function initZoom() {
    if (_initDone) return;
    _initDone = true;

    currentZoom = loadZoom();

    var btnOut = document.getElementById('ai-zoom-out');
    var btnIn  = document.getElementById('ai-zoom-in');
    var btnRst = document.getElementById('ai-zoom-reset');

    if (btnOut) btnOut.addEventListener('click', zoomOut);
    if (btnIn)  btnIn.addEventListener('click', zoomIn);
    if (btnRst) btnRst.addEventListener('click', zoomReset);

    applyZoom(currentZoom);
    updateUI(currentZoom);
  }

  // 等 DOM 就绪后初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initZoom);
  } else {
    initZoom();
  }

  // 暴露 reset 供外部调用（如板块切换后重新应用）
  window._aiZoomReset = function() {
    applyZoom(currentZoom);
    updateUI(currentZoom);
  };
})();

