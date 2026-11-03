/**
 * 管理后台 · 主题桥（admin theme bridge）v1
 * 1) 为 ECharts 注册墨金主题，并让页面内 `echarts.init(el)` 默认使用它
 *    （不改 admin-monitor.js / admin-health.js，任何图表自动换色）
 * 2) 暴露 window.dshAdminTheme
 */
(function () {
  'use strict';

  var PALETTE = ['#d6b084', '#93a9c4', '#8fbf9a', '#a99bc8', '#d08f7c', '#9fb8c9', '#c8b48a', '#b3aa99'];

  function theme(dark) {
    return {
      color: PALETTE,
      backgroundColor: 'transparent',
      textStyle: { color: dark ? '#b3aa99' : '#5c5344', fontFamily: '"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif' },
      title: { textStyle: { color: dark ? '#ece6da' : '#211d16', fontWeight: 500 }, subtextStyle: { color: dark ? '#7a7264' : '#8d8371' } },
      legend: { textStyle: { color: dark ? '#b3aa99' : '#5c5344' } },
      tooltip: {
        backgroundColor: dark ? 'rgba(18,20,26,.94)' : 'rgba(255,253,247,.96)',
        borderColor: dark ? 'rgba(214,176,132,.28)' : 'rgba(90,70,40,.3)',
        textStyle: { color: dark ? '#ece6da' : '#211d16' },
      },
      categoryAxis: {
        axisLine: { lineStyle: { color: dark ? 'rgba(214,176,132,.18)' : 'rgba(90,70,40,.2)' } },
        axisTick: { lineStyle: { color: dark ? 'rgba(214,176,132,.18)' : 'rgba(90,70,40,.2)' } },
        axisLabel: { color: dark ? '#7a7264' : '#8d8371' },
        splitLine: { lineStyle: { color: dark ? 'rgba(255,255,255,.04)' : 'rgba(90,70,40,.08)' } },
      },
      valueAxis: {
        axisLine: { lineStyle: { color: dark ? 'rgba(214,176,132,.18)' : 'rgba(90,70,40,.2)' } },
        axisTick: { lineStyle: { color: dark ? 'rgba(214,176,132,.18)' : 'rgba(90,70,40,.2)' } },
        axisLabel: { color: dark ? '#7a7264' : '#8d8371' },
        splitLine: { lineStyle: { color: dark ? 'rgba(255,255,255,.04)' : 'rgba(90,70,40,.08)' } },
      },
      line: { smooth: true, symbolSize: 4 },
      bar: { itemStyle: { borderRadius: [2, 2, 0, 0] } },
      pie: { itemStyle: { borderColor: dark ? '#0b0d13' : '#f7f4ec', borderWidth: 2 } },
    };
  }

  function isDark() { return !document.documentElement.classList.contains('light') && !document.body.classList.contains('light'); }

  function install() {
    var ec = window.echarts;
    if (!ec || ec.__dapengThemed) return;
    if (typeof ec.registerTheme === 'function') {
      try { ec.registerTheme('dapeng-ink', theme(true)); } catch (e) {}
      try { ec.registerTheme('dapeng-paper', theme(false)); } catch (e) {}
    }
    var init = ec.init;
    ec.init = function (dom, t, opts) {
      return init.call(this, dom, t || (isDark() ? 'dapeng-ink' : 'dapeng-paper'), opts);
    };
    ec.__dapengThemed = true;

  /* ── ECharts 颜色归一：把冷蓝/紫/青映射到墨金+钢灰，保留绿/红语义 ── */
  var COLOR_MAP = {
    '#3b82f6': '#93a9c4', '#60a5fa': '#a7bcd4', '#2563eb': '#8ba2bd', '#1d4ed8': '#7e95b0',
    '#06b6d4': '#9fb8c9', '#22d3ee': '#b6cbda', '#0891b2': '#8aa8b8',
    '#8b5cf6': '#a99bc8', '#a855f7': '#b6a9d0', '#7c3aed': '#9c8fbd', '#6366f1': '#96a3bd',
    '#818cf8': '#a9b4cb', '#c084fc': '#c3b8d8', '#ec4899': '#c99ab8',
    'rgb(59,130,246)': 'rgb(147,169,196)', 'rgb(6,182,212)': 'rgb(159,184,201)',
    'rgb(139,92,246)': 'rgb(169,155,200)', 'rgb(168,85,247)': 'rgb(182,169,208)'
  };
  function remap(v) {
    if (typeof v === 'string') {
      var s = v.trim().toLowerCase();
      if (COLOR_MAP[s]) return COLOR_MAP[s];
      return v.replace(/rgba?\(59,130,246[^)]*\)/gi, 'rgba(147,169,196,.85)')
        .replace(/rgba?\(6,182,212[^)]*\)/gi, 'rgba(159,184,201,.85)')
        .replace(/rgba?\(139,92,246[^)]*\)/gi, 'rgba(169,155,200,.85)')
        .replace(/rgba?\(168,85,247[^)]*\)/gi, 'rgba(182,169,208,.85)');
    }
    if (Array.isArray(v)) return v.map(remap);
    if (v && typeof v === 'object') {
      var o = {};
      for (var k in v) { if (Object.prototype.hasOwnProperty.call(v, k)) o[k] = remap(v[k]); }
      return o;
    }
    return v;
  }
  var init2 = ec.init;
  ec.init = function (dom, t, opts) {
    var inst = init2.call(this, dom, t || (isDark() ? 'dapeng-ink' : 'dapeng-paper'), opts);
    if (inst && !inst.__dapengMapped && typeof inst.setOption === 'function') {
      var so = inst.setOption;
      inst.setOption = function (option) {
        try { option = remap(option); } catch (e) {}
        return so.apply(inst, arguments.length > 1 ? [option].concat([].slice.call(arguments, 1)) : [option]);
      };
      inst.__dapengMapped = true;
    }
    return inst;
  };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(install, 0); });
  } else {
    setTimeout(install, 0);
  }
  // echarts 由 CDN/本地脚本异步就绪时兜底
  var tries = 0;
  var t = setInterval(function () {
    install();
    if ((window.echarts && window.echarts.__dapengThemed) || ++tries > 40) clearInterval(t);
  }, 120);

  window.dshAdminTheme = { palette: PALETTE, theme: theme, isDark: isDark };
})();
