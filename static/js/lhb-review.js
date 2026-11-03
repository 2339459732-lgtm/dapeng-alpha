/* 龙虎榜复盘页（lhb-review.v1）
 * ---------------------------------------------------------------------------
 * 数据全部来自 /api/decision/lhb-review*，前端不做任何归因判断——只负责排序、
 * 筛选与展示。归因口径见 docs/10-architecture/LHB-REVIEW.md。
 *
 * 三个设计原则：
 * 1. **一句话先给结论，细节按需求展开**：默认只渲染概览，35 条明细的详情按需展开，
 *    避免一屏塞满文字反而读不出重点；
 * 2. **每个数字都能追到来源**：金额、占比、封板时间都带口径（万/亿、涨停池口径），
 *    推断等级（封板时刻级 / 日级）直接标在行内，不让人误以为是分时成交；
 * 3. **筛选即钻取**：点主因条、点标的行、点要点卡片，都会把明细表收敛到对应子集，
 *    而不是跳到另一个页面。
 */
(function () {
  'use strict';

  var BASE = '/api/decision/lhb-review';
  var MOTIVES = ['题材炒作', '市场情绪', '资金博弈', '消息面', '技术形态', '证据不足（无法归类）'];
  var SEAT_TYPES = [
    { key: 'institution', label: '机构' },
    { key: 'northbound', label: '北向' },
    { key: 'hot_money', label: '游资' },
    { key: 'retail', label: '散户' },
    { key: 'other', label: '其他' }
  ];

  var state = {
    date: '',
    report: null,
    recent: [],
    loading: false,
    error: '',
    filters: { seat: '', side: '', motive: '', q: '', minNet: 0, stock: null },
    open: {},
    sort: { key: 'net', dir: -1 }
  };

  // ---------------------------------------------------------------- 工具
  function $(id) { return document.getElementById(id); }
  function esc(v) {
    if (v == null) return '';
    return String(v).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }
  function num(v) {
    var n = parseFloat(v);
    return isFinite(n) ? n : null;
  }
  /** 万元 → 可读金额：≥1 亿用亿，否则用万。符号单独给颜色，不在文本里混。 */
  function money(wan) {
    var n = num(wan);
    if (n == null) return '--';
    var a = Math.abs(n);
    if (a >= 10000) return (a / 10000).toFixed(2) + ' 亿';
    if (a >= 1) return Math.round(a).toLocaleString('zh-CN') + ' 万';
    return n.toFixed(0) + ' 万';
  }
  /** 带符号与涨跌色（红涨绿跌，符合国内习惯）。 */
  /** 带符号与涨跌色（红涨绿跌，符合国内习惯）。金额单位沿用 money() 的万/亿自适应。 */
  function signed(wan) {
    var n = num(wan);
    if (n == null) return '<span class="lr-muted">--</span>';
    var cls = n > 0 ? 'lr-up' : n < 0 ? 'lr-down' : 'lr-flat';
    var sign = n > 0 ? '+' : n < 0 ? '-' : '';
    return '<span class="' + cls + '">' + sign + money(Math.abs(n)) + '</span>';
  }
  function pctText(v, digits) {
    var n = num(v);
    if (n == null) return '--';
    return (n * 100).toFixed(digits == null ? 0 : digits) + '%';
  }
  function seatTag(type, label) {
    var cls = type === 'institution' ? 'inst' : type === 'northbound' ? 'north'
      : type === 'hot_money' ? 'hotm' : type === 'retail' ? 'retail' : 'other';
    return '<span class="lr-tag ' + cls + '">' + esc(label || '其他') + '</span>';
  }
  /** 席位类型 → 中文。`other` 是「未收录的营业部」，直接叫营业部更准确。 */
  function typeLabel(type, seat) {
    if (type === 'institution') return '机构';
    if (type === 'northbound') return '北向';
    if (type === 'hot_money') return '游资';
    if (type === 'retail') return '散户';
    return seat && /营业部|分公司|证券/.test(seat) ? '营业部' : '其他';
  }
  function sideTag(side) {
    return side === 'buy' ? '<span class="lr-tag buy">买入</span>' : '<span class="lr-tag sell">卖出</span>';
  }
  function confBar(v) {
    var n = num(v);
    if (n == null) return '<span class="lr-muted">--</span>';
    var w = Math.max(6, Math.round(n * 100));
    return '<span class="lr-conf"><span class="lr-conf-track"><i style="width:' + w + '%"></i></span>'
      + '<span>' + n.toFixed(2) + '</span></span>';
  }
  function shortDate(d) { return String(d || '').slice(5) || '--'; }

  // ---------------------------------------------------------------- 数据
  function fetchJSON(url, timeout) {
    var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, timeout || 60000) : null;
    return fetch(url, { signal: ctrl ? ctrl.signal : undefined, headers: { Accept: 'application/json' } })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .finally(function () { if (timer) clearTimeout(timer); });
  }

  function loadRecent() {
    return fetchJSON(BASE + '/recent?limit=30', 15000)
      .then(function (d) { state.recent = (d && d.items) || []; })
      .catch(function () { state.recent = []; });
  }

  function load(date, refresh) {
    if (state.loading) return;
    state.loading = true;
    state.error = '';
    setBusy(true);

    // 默认读已落盘的报告（毫秒级）；refresh 才会重新打上游（5–10 秒）。
    // 传日期时不带 refresh，后端会先看该日缓存文件，没有才提示生成。
    var url = date ? BASE + '/' + encodeURIComponent(date) : BASE;
    if (refresh) url += '?refresh=true';

    fetchJSON(url, refresh ? 180000 : 45000)
      .then(function (d) {
        state.report = d;
        if (d && d.date) { state.date = d.date; $('lr-date').value = d.date; }
        if (!d || !d.ok) {
          state.error = (d && d.message) || '该日暂无复盘数据';
        }
        if (refresh) return loadRecent().then(function () { return d; });
        return d;
      })
      .then(function () { render(); })
      .catch(function (e) {
        state.error = e && e.name === 'AbortError'
          ? '请求超时：营业部明细要拉十几页，首次生成可能需要 10 秒以上，请稍后再点「读取」'
          : ('请求失败：' + (e && e.message ? e.message : e));
        render();
      })
      .finally(function () {
        state.loading = false;
        setBusy(false);
      });
  }

  function setBusy(busy) {
    var a = $('lr-load'), b = $('lr-regen');
    if (a) a.disabled = busy;
    if (b) { b.disabled = busy; b.textContent = busy ? '生成中…' : '重新生成'; }
  }

  // ---------------------------------------------------------------- 渲染入口
  function render() {
    var main = $('lr-main');
    var r = state.report;
    var stamp = $('lr-stamp');
    var alertBox = $('lr-alert');

    if (state.error) {
      alertBox.hidden = false;
      alertBox.className = 'lr-alert' + (r && r.ok ? '' : ' is-error');
      alertBox.innerHTML = esc(state.error)
        + (state.date ? ' <code>' + esc(state.date) + '</code>' : '')
        + (r && r.ok ? '' : '<br>龙虎榜营业部明细通常在收盘后 18:00–19:00 补齐；'
          + '点「重新生成」可立即重跑（约 5–10 秒）。');
    } else {
      alertBox.hidden = true;
    }

    if (!r || !r.ok) {
      main.innerHTML = emptyState();
      $('lr-caveats').hidden = true;
      if (stamp) stamp.textContent = '';
      return;
    }

    if (stamp) {
      stamp.textContent = '生成于 ' + String(r.generated_at || '').replace('T', ' ');
    }

    var ins = r.insight || null;
    var html = '';
    html += renderHero(r, ins);
    html += renderKpis(r, ins);
    html += renderContext(r);
    html += renderFindings(ins);
    html += renderMotives(ins, r);
    html += renderFactors(ins);
    html += renderStocks(ins);
    html += renderRecords(r);
    html += renderSeats(ins);
    html += renderIndustry(ins);
    main.innerHTML = html;

    renderCaveats(r, ins);
  }

  function emptyState() {
    return '<div class="lr-panel"><div class="lr-empty">'
      + '<b>这一天没有复盘结果</b>'
      + '龙虎榜只在有异动标的的交易日产生；非交易日、或当日尚未披露时为空。<br>'
      + '可以先点「读取」看最近一份，或换一个交易日。'
      + '</div></div>';
  }

  // ---------------------------------------------------------------- 一句话结论
  function renderHero(r, ins) {
    var s = r.summary || {};
    var t = (ins && ins.totals) || {};
    var parts = [];

    parts.push('共 <em>' + (s.records || 0) + '</em> 条大单、<em>' + (s.stocks || 0) + '</em> 只标的');

    if (t.consensus_buy != null) {
      var bits = ['合力做多 <em>' + t.consensus_buy + '</em> 只'];
      if (t.consensus_sell) bits.push('合力出货 <em>' + t.consensus_sell + '</em> 只');
      if (t.conflict) bits.push('多空对峙 <em>' + t.conflict + '</em> 只');
      parts.push(bits.join('、'));
    }

    var topBuy = null, topSell = null;
    if (ins && ins.by_stock && ins.by_stock.length) {
      ins.by_stock.forEach(function (x) {
        if (!topBuy && x.net > 0) topBuy = x;
        if (!topSell && x.net < 0) topSell = x;
      });
    }
    if (topBuy) {
      parts.push('最大净买 <em>' + esc(topBuy.name) + '</em>（' + money(topBuy.net_wan) + '）');
    }
    if (topSell) {
      parts.push('最大净卖 <em>' + esc(topSell.name) + '</em>（' + money(Math.abs(topSell.net_wan)) + '）');
    }

    var lead = parts.join('，') + '。';

    var sub = [];
    if (ins && ins.by_seat && ins.by_seat.length) {
      var kinds = ins.by_seat.map(function (x) {
        return x.type_label + ' ' + (x.net_wan > 0 ? '净买 ' : '净卖 ') + money(Math.abs(x.net_wan));
      });
      sub.push('资金构成：' + kinds.join(' · '));
    }
    if (r.context && r.context.max_board) {
      sub.push('当日情绪：涨停 ' + r.context.zt_count + ' 家、最高 ' + r.context.max_board
        + ' 连板、封板率 ' + (r.context.seal_rate == null ? '--' : r.context.seal_rate + '%'));
    }

    return '<section class="lr-hero">'
      + '<div class="lr-hero-eyebrow">' + esc(r.date || '') + ' 复盘结论</div>'
      + '<div class="lr-hero-text">' + lead + '</div>'
      + (sub.length ? '<div class="lr-hero-sub">' + sub.map(esc).join('　·　') + '</div>' : '')
      + '</section>';
  }

  // ---------------------------------------------------------------- KPI
  function renderKpis(r, ins) {
    var s = r.summary || {};
    var t = (ins && ins.totals) || {};
    var cells = [
      { v: s.records || 0, l: '上榜记录', cls: '' },
      { v: s.stocks || 0, l: '涉及标的', cls: '' },
      { v: money(t.buy_net_wan), l: '席位净买入（合计）', cls: 'up' },
      { v: money(t.sell_net_wan), l: '席位净卖出（合计）', cls: 'down' },
      { v: (s.seal_time_inferred || 0) + '/' + (s.records || 0), l: '封板时刻级推断', cls: 'cyan' },
      { v: (s.factor_covered != null ? s.factor_covered : '--') + '/' + (s.records || 0), l: '因素命中覆盖', cls: 'cyan' },
      { v: t.seats != null ? t.seats : '--', l: '参与席位数', cls: 'gold' }
    ];
    return '<div class="lr-kpis">' + cells.map(function (c) {
      return '<div class="lr-kpi ' + c.cls + '"><b>' + esc(c.v) + '</b><span>' + esc(c.l) + '</span></div>';
    }).join('') + '</div>';
  }

  // ---------------------------------------------------------------- 市场底座
  function renderContext(r) {
    var c = r.context || {};
    if (!c.available && !c.zt_count) {
      return panel('当日市场底座', 'MARKET CONTEXT',
        '<div class="lr-empty">涨停池不可用（' + esc(c.reason || '接口异常')
        + '），情绪与题材维度已按「不评分」处理，不会用 0 分冒充。</div>');
    }
    var cells = [
      { v: c.zt_count, l: '涨停', cls: 'hot' },
      { v: c.zb_count, l: '炸板', cls: 'warn' },
      { v: c.dt_count, l: '跌停', cls: 'cold' },
      { v: c.seal_rate == null ? '--' : c.seal_rate + '%', l: '封板率', cls: c.seal_rate >= 80 ? 'hot' : 'warn' },
      { v: c.max_board, l: '最高连板', cls: 'hot' }
    ];
    var body = '<div class="lr-ctx-grid">' + cells.map(function (x) {
      return '<div class="lr-ctx-cell ' + x.cls + '"><b>' + esc(x.v == null ? '--' : x.v) + '</b>'
        + '<span>' + esc(x.l) + '</span></div>';
    }).join('') + '</div>';

    var inds = c.top_industries || [];
    if (inds.length) {
      body += '<div class="lr-chips">' + inds.map(function (p) {
        return '<span class="lr-chip hot">' + esc(p[0]) + ' <b>' + p[1] + '</b> 家涨停</span>';
      }).join('') + '</div>';
    }
    body += '<div class="lr-note-line" style="margin-top:11px">'
      + '封板率 = 涨停 /（涨停 + 炸板）。封板率越低，说明当日承接越差，打板与接力的成功率随之下降；'
      + '最高连板代表情绪周期的空间高度，高度被压缩时高位股风险显著上升。</div>';
    return panel('当日市场底座', 'MARKET CONTEXT', body,
      '来自复盘日涨停 / 炸板 / 跌停池');
  }

  // ---------------------------------------------------------------- 关键发现
  function renderFindings(ins) {
    var list = (ins && ins.highlights) || [];
    if (!list.length) return '';
    var icons = { largest: '单', consensus_buy: '合', conflict: '歧', focus: '聚', context: '境' };
    var body = '<div class="lr-finds">' + list.map(function (h) {
      var clickable = !!h.code;
      return '<div class="lr-find tone-' + esc(h.tone || 'neutral') + '">'
        + '<div class="lr-find-ico">' + esc(icons[h.kind] || '·') + '</div>'
        + '<div><div class="lr-find-title">' + esc(h.title)
        + (clickable ? ' <a class="lr-find-link" data-jump-stock="' + esc(h.code) + '">看明细</a>' : '')
        + '</div><div class="lr-find-detail">' + esc(h.detail) + '</div></div>'
        + '</div>';
    }).join('') + '</div>';
    return panel('关键发现', 'HIGHLIGHTS', body, '按金额与合力自动排序，不含因果推断');
  }

  // ---------------------------------------------------------------- 主因分布
  function renderMotives(ins, r) {
    var matrix = (ins && ins.motive_matrix) || [];
    var byMotive = (r.summary && r.summary.by_motive) || {};
    if (!matrix.length) {
      // 老报告没有 insight：退回用 summary 的计数，仍然能看分布
      var keys = Object.keys(byMotive);
      if (!keys.length) return '';
      matrix = keys.map(function (k) { return { motive: k, total: byMotive[k], buy: null, sell: null, net_wan: 0 }; });
    }
    var max = Math.max.apply(null, matrix.map(function (m) { return m.total || 0; }).concat([1]));
    var body = '<div class="lr-bars">' + matrix.map(function (m) {
      var total = m.total || 0;
      var buy = m.buy || 0, sell = m.sell || 0;
      var sum = buy + sell;
      var w = Math.round((total / max) * 100);
      var buyW = sum ? Math.round((buy / sum) * 100) : 0;
      var bars;
      if (sum > 0) {
        bars = '<span class="lr-bar-track" data-filter-motive="' + esc(m.motive) + '" title="'
          + esc(m.motive) + '：买 ' + buy + ' / 卖 ' + sell + '，点击筛选明细">'
          + '<i class="lr-bar-buy" style="width:' + (w * buyW / 100) + '%"><em>' + (buy || '') + '</em></i>'
          + '<i class="lr-bar-sell" style="width:' + (w * (100 - buyW) / 100) + '%"><em>' + (sell || '') + '</em></i>'
          + '</span>';
      } else {
        bars = '<span class="lr-bar-track" data-filter-motive="' + esc(m.motive) + '">'
          + '<i class="lr-bar-buy" style="width:' + w + '%"></i></span>';
      }
      return '<div class="lr-bar-row">'
        + '<div class="lr-bar-label" title="' + esc(m.motive) + '">'
        + esc(m.motive.length > 5 ? m.motive.slice(0, 5) : m.motive) + ' <b>' + total + '</b></div>'
        + bars
        + '<div class="lr-bar-meta">' + (num(m.net_wan) == null ? '' : signed(m.net_wan)) + '</div>'
        + '</div>';
    }).join('') + '</div>';

    body += '<div class="lr-note-line" style="margin-top:12px">'
      + '<b style="color:var(--red)">红</b> = 净买入条数，<b style="color:var(--green)">绿</b> = 净卖出条数；'
      + '右侧为该主因下的净额合计。点击任一条可把下方明细筛选到该主因。'
      + '市场情绪按「环境打折 + 龙头效应」计分：环境分全市场共用，不打折会让所有记录都归成情绪。</div>';
    return panel('动因分布', 'MOTIVE DISTRIBUTION', body);
  }

  // ---------------------------------------------------------------- 因素命中统计
  function renderFactors(ins) {
    var stats = (ins && ins.factor_stats) || {};
    var buys = stats.buy || [], sells = stats.sell || [];
    if (!buys.length && !sells.length) return '';

    function col(title, rows, cls) {
      var head = '<div class="lr-fac-col-head">' + title + '</div>';
      if (!rows.length) {
        return '<div class="lr-fac-col ' + cls + '">' + head
          + '<div class="lr-empty" style="padding:12px 0">当日无命中因素</div></div>';
      }
      var max = Math.max.apply(null, rows.map(function (x) { return x.count || 0; }).concat([1]));
      return '<div class="lr-fac-col ' + cls + '">' + head
        + rows.map(function (x) {
          var w = Math.round(((x.count || 0) / max) * 100);
          return '<div class="lr-fac-row" title="' + esc(x.label || '') + '：命中 '
            + (x.count || 0) + ' 次，涉及 |净额| 合计 ' + money(x.net_wan) + '">'
            + '<span class="lr-fac-label">' + esc(x.label || x.id) + '</span>'
            + '<span class="lr-bar-track"><i class="' + (cls === 'buy' ? 'lr-bar-buy' : 'lr-bar-sell')
            + '" style="width:' + Math.max(4, w) + '%"></i></span>'
            + '<span class="lr-fac-count">' + (x.count || 0) + ' 次</span>'
            + '<span class="lr-fac-amt">' + money(x.net_wan) + '</span>'
            + '</div>';
        }).join('') + '</div>';
    }

    var body = '<div class="lr-factors">'
      + col('买入侧命中（为什么买）', buys, 'buy')
      + col('卖出侧命中（为什么卖）', sells, 'sell')
      + '</div>'
      + '<div class="lr-note-line" style="margin-top:12px">'
      + '因素来自可配置规则库（data/lhb_config.json 的 factors 段）：每条是「事实 + 阈值」的判定式。'
      + '统计口径：买入侧只计买入记录的命中、卖出侧只计卖出记录的命中；'
      + '同一只票可同时具备买入理由与兑现条件（在明细里成对展示）——这正是分歧的来源。'
      + '金额为命中记录的 |净额| 合计，只衡量该理由背后资金的体量。</div>';
    return panel('买卖因素命中统计', 'FACTOR HITS', body);
  }

  // ---------------------------------------------------------------- 标的聚合
  function renderStocks(ins) {
    var rows = (ins && ins.by_stock) || [];
    if (!rows.length) return '';
    var k = state.sort.key, dir = state.sort.dir;
    var sorted = rows.slice().sort(function (a, b) {
      var av = k === 'name' ? String(a.name || '') : num(a[k]);
      var bv = k === 'name' ? String(b.name || '') : num(b[k]);
      if (k === 'name') return av.localeCompare(bv, 'zh') * dir;
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av - bv) * dir;
    });

    function th(key, label, cls) {
      var on = state.sort.key === key;
      return '<th class="' + (cls || '') + ' is-sortable' + (on ? ' is-sorted' : '') + (on && dir > 0 ? ' is-asc' : '')
        + '" data-sort="' + key + '">' + label + '</th>';
    }

    var head = '<tr>'
      + th('name', '标的')
      + th('industry', '题材')
      + th('net', '净额', 'num')
      + '<th>资金合力</th>'
      + '<th>封板质量</th>'
      + '<th>参与席位</th>'
      + '<th>主要理由</th>'
      + '</tr>';

    var body = sorted.map(function (s) {
      var alignCls = s.alignment === '合力做多' ? 'ok' : s.alignment === '合力出货' ? 'bad' : 'warn';
      var types = s.seat_types || {};
      // 席位构成比「买盘占比」有用：后者与「资金合力」标签是同一个量的两种说法，并排显示是重复信息。
      var typeTxt = Object.keys(types).map(function (t) {
        return '<span class="lr-tag other" style="margin-right:3px">' + esc(t) + ' ' + types[t] + '</span>';
      }).join('');
      return '<tr class="is-clickable" data-jump-stock="' + esc(s.code) + '">'
        + '<td><span class="lr-stock-name">' + esc(s.name) + '</span>'
        + '<span class="lr-stock-code">' + esc(String(s.code || '').slice(2)) + '</span></td>'
        + '<td class="lr-muted">' + esc(s.industry || '未归入涨停池') + '</td>'
        + '<td class="num">' + signed(s.net_wan) + '</td>'
        + '<td><span class="lr-tag ' + alignCls + '">' + esc(s.alignment) + '</span>'
        + (s.buy_share != null ? ' <span class="lr-muted" style="font-size:10.5px">买 ' + pctText(s.buy_share) + '</span>' : '')
        + '</td>'
        + '<td><span class="lr-tag ' + esc((s.seal && s.seal.level) || 'none') + '">'
        + esc((s.seal && s.seal.label) || '--') + '</span></td>'
        + '<td>' + (typeTxt || '--') + '</td>'
        + '<td>' + esc((s.motive && s.motive.primary) || '--') + '</td>'
        + '</tr>';
    }).join('');

    var bodyHtml = '<div class="lr-table-wrap"><table class="lr-table">'
      + '<thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>'
      + '<div class="lr-note-line" style="margin:12px 15px 14px">'
      + '「资金合力」按净买额占买卖总额比例划分：≥80% 合力做多、≤20% 合力出货、其余为多空对峙——'
      + '对峙意味着买卖双方都有实质金额，次日方向取决于谁先松手，不宜当作单一方向的信号。'
      + '点击任一行可把详情收敛到该标的。</div>';

    return panel('标的视角：一只票上发生了什么', 'BY STOCK', bodyHtml,
      rows.length + ' 只', true);
  }

  // ---------------------------------------------------------------- 明细
  function recordsFiltered() {
    var r = state.report;
    if (!r || !r.ok) return [];
    var list = (r.records || []).slice();
    var f = state.filters;
    if (f.seat) list = list.filter(function (x) { return x.seat_type === f.seat; });
    if (f.side) list = list.filter(function (x) { return (x.side || (x.net >= 0 ? 'buy' : 'sell')) === f.side; });
    if (f.motive) list = list.filter(function (x) { return ((x.motive || {}).primary) === f.motive; });
    if (f.stock) list = list.filter(function (x) { return x.code === f.stock; });
    if (f.minNet) list = list.filter(function (x) { return Math.abs(num(x.net_wan) || 0) >= f.minNet; });
    if (f.q) {
      var q = f.q.toLowerCase();
      list = list.filter(function (x) {
        return [x.name, x.code, x.seat, x.seat_alias, x.reason, x.stock_explain]
          .some(function (v) { return v && String(v).toLowerCase().indexOf(q) >= 0; });
      });
    }
    return list;
  }

  function renderRecords(r) {
    var all = r.records || [];
    if (!all.length) return '';

    var counts = {
      all: all.length,
      institution: all.filter(function (x) { return x.seat_type === 'institution'; }).length,
      northbound: all.filter(function (x) { return x.seat_type === 'northbound'; }).length,
      hot_money: all.filter(function (x) { return x.seat_type === 'hot_money'; }).length,
      retail: all.filter(function (x) { return x.seat_type === 'retail'; }).length,
      other: all.filter(function (x) { return x.seat_type === 'other'; }).length,
      buy: all.filter(function (x) { return (x.side || (x.net >= 0 ? 'buy' : 'sell')) === 'buy'; }).length,
      sell: all.filter(function (x) { return (x.side || (x.net >= 0 ? 'buy' : 'sell')) === 'sell'; }).length
    };
    var motifCounts = {};
    all.forEach(function (x) {
      var m = (x.motive || {}).primary;
      if (m) motifCounts[m] = (motifCounts[m] || 0) + 1;
    });

    var f = state.filters;
    var seatChips = '<span class="lr-filter-label">席位</span>'
      + '<span class="lr-fchip' + (f.seat === '' ? ' on' : '') + '" data-f-seat="">全部<em>' + counts.all + '</em></span>'
      + SEAT_TYPES.filter(function (t) { return counts[t.key]; }).map(function (t) {
        return '<span class="lr-fchip' + (f.seat === t.key ? ' on' : '') + '" data-f-seat="' + t.key + '">'
          + t.label + '<em>' + counts[t.key] + '</em></span>';
      }).join('');

    var sideChips = '<span class="lr-filter-label">方向</span>'
      + '<span class="lr-fchip' + (f.side === '' ? ' on' : '') + '" data-f-side="">全部</span>'
      + '<span class="lr-fchip' + (f.side === 'buy' ? ' on' : '') + '" data-f-side="buy">买入<em>' + counts.buy + '</em></span>'
      + '<span class="lr-fchip' + (f.side === 'sell' ? ' on' : '') + '" data-f-side="sell">卖出<em>' + counts.sell + '</em></span>';

    var motifChips = '<span class="lr-filter-label">主因</span>'
      + '<span class="lr-fchip' + (f.motive === '' ? ' on' : '') + '" data-f-motive="">全部</span>'
      + MOTIVES.filter(function (m) { return motifCounts[m]; }).map(function (m) {
        return '<span class="lr-fchip' + (f.motive === m ? ' on' : '') + '" data-f-motive="' + esc(m) + '">'
          + esc(m) + '<em>' + motifCounts[m] + '</em></span>';
      }).join('');

    var stockHint = f.stock
      ? '<span class="lr-fchip on" data-f-stock="">标的：' + esc(f.stock) + ' ✕</span>'
      : '';

    var list = recordsFiltered();
    var head = '<tr><th style="width:26px"></th><th>标的</th><th>席位</th><th>方向</th>'
      + '<th class="num">净额</th><th class="num" title="该席位买入额占该股当日成交额的比例">占成交</th>'
      + '<th>主因</th><th>置信度</th></tr>';
    var rows = list.map(function (x, i) { return recordRow(x, i); }).join('');

    var empty = list.length ? '' : '<div class="lr-empty">当前筛选条件下没有记录。'
      + '<br>可以点「重置」回到全部 ' + counts.all + ' 条。</div>';

    var body = '<div class="lr-filters">'
      + '<div class="lr-filter-group">' + seatChips + '</div>'
      + '<div class="lr-filter-group">' + sideChips + '</div>'
      + '<div class="lr-filter-group">' + motifChips + '</div>'
      + '<input class="lr-search" id="lr-q" type="search" placeholder="搜标的 / 席位 / 上榜原因" value="' + esc(f.q) + '">'
      + stockHint
      + '<button type="button" class="lr-reset" id="lr-reset">重置</button>'
      + '<span class="lr-count">' + list.length + ' / ' + counts.all + ' 条</span>'
      + '</div>'
      + (list.length
        ? '<div class="lr-table-wrap"><table class="lr-table"><thead>' + head + '</thead><tbody>' + rows + '</tbody></table></div>'
        : empty);

    return panel('逐笔明细与归因', 'RECORDS',
      body + '<div class="lr-note-line" style="margin:12px 15px 14px">'
      + '点任一行展开归因依据（五维分数、证据、价位与时机、卖出触发条件、完整推演逻辑）。'
      + '「封板时刻级」表示用了当日涨停封板时间推断买卖时点；「日级」表示只能定性到盘中/板上/尾盘，'
      + '龙虎榜本身不含逐笔时间。</div>', null, true);
  }

  function recordRow(x, idx) {
    var key = (x.code || '') + '|' + (x.seat || '') + '|' + idx;
    var open = !!state.open[key];
    var side = x.side || (num(x.net) >= 0 ? 'buy' : 'sell');
    var motive = (x.motive || {}).primary || '--';
    var infer = (x.price_context || {}).inference;
    var board = x.board_count || (x.price_context || {}).board_count;

    var row = '<tr class="lr-rec' + (open ? ' is-open' : '') + '" data-rec="' + esc(key) + '">'
      + '<td><span class="lr-rec-chev">▶</span></td>'
      + '<td><span class="lr-stock-name">' + esc(x.name) + '</span>'
      + (board ? '<span class="lr-stock-code">' + board + '板</span>' : '')
      + '</td>'
      + '<td>' + seatTag(x.seat_type, typeLabel(x.seat_type, x.seat))
      + ' <span class="lr-flat" title="' + esc(x.seat) + '">' + esc(x.seat_alias || x.seat) + '</span></td>'
      + '<td>' + sideTag(side) + '</td>'
      + '<td class="num">' + signed(x.net_wan) + '</td>'
      + '<td class="num">' + (num(x.buy_ratio_pct) != null && num(x.buy_ratio_pct) > 0
        ? num(x.buy_ratio_pct).toFixed(2) + '%'
        : (num(x.sell_ratio_pct) != null && num(x.sell_ratio_pct) > 0 ? num(x.sell_ratio_pct).toFixed(2) + '%' : '--')) + '</td>'
      + '<td>' + esc(motive) + (infer === 'seal-time' ? ' <span class="lr-tag ok" title="用当日涨停封板时间推断买卖时点">封板时刻</span>' : '') + '</td>'
      + '<td>' + confBar(x.confidence) + '</td>'
      + '</tr>';

    if (!open) return row;

    var scores = (x.motive || {}).scores || {};
    var evidence = (x.motive || {}).evidence || {};
    var primary = (x.motive || {}).primary;
    var dims = MOTIVES.filter(function (m) { return m !== '证据不足（无法归类）'; }).map(function (m) {
      var v = num(scores[dimKey(m)]);
      if (v == null) return '';
      var isPrimary = m === primary;
      return '<div class="lr-dim' + (isPrimary ? ' is-primary' : '') + '">'
        + '<div class="lr-dim-label">' + esc(m) + '</div>'
        + '<div class="lr-dim-track"><i style="width:' + Math.max(2, Math.round(v * 100)) + '%"></i></div>'
        + '<div class="lr-dim-val">' + v.toFixed(2) + '</div></div>';
    }).join('');

    var evRows = Object.keys(evidence).map(function (k) {
      var arr = evidence[k] || [];
      return '<div class="lr-ev-row"><div class="lr-ev-key">' + esc(k) + '</div>'
        + '<div class="lr-ev-val' + (arr.length ? '' : ' is-empty') + '">'
        + (arr.length ? arr.map(esc).join('；') : '无证据（该维度不评分）') + '</div></div>';
    }).join('');

    var pc = x.price_context || {};
    var kv = [
      { l: '收盘 / VWAP', v: (pc.close == null ? '--' : pc.close) + ' / ' + (pc.vwap == null ? '--' : pc.vwap) },
      { l: '价位关系', v: pc.zone || '--' },
      { l: '封板时间', v: pc.first_seal ? (pc.first_seal + (pc.last_seal && pc.last_seal !== pc.first_seal ? ' → ' + pc.last_seal : '')) : '未涨停 / 无数据' },
      { l: '炸板次数', v: pc.open_times == null ? '--' : pc.open_times + ' 次' },
      { l: '席位角色', v: (x.seat_role || '--') + (num(x.seat_share) != null ? '（占榜内净额 ' + pctText(x.seat_share) + '）' : '') },
      { l: '3 日成功率', v: x.win_rate_3d == null ? '--' : (x.win_rate_3d + '%' + (x.win_samples ? '（样本 ' + x.win_samples + '）' : '')) },
      { l: '上榜原因', v: x.reason || '--' },
      { l: '该席位解读', v: x.stock_explain || '--' }
    ].map(function (i) {
      return '<div class="lr-kv-item"><span>' + esc(i.l) + '</span><b>' + esc(i.v) + '</b></div>';
    }).join('');

    var fac = x.factors || {};
    var facHtml = '';
    if (fac.buy || fac.sell) {
      var facList = function (arr, cls) {
        if (!arr || !arr.length) {
          return '<div class="lr-muted" style="font-size:11px;padding:2px 0">无命中</div>';
        }
        return arr.map(function (f) {
          var w = num(f.weight);
          return '<div class="lr-fac-hit ' + cls + '">'
            + '<div class="lr-fac-hit-line"><span class="lr-fac-hit-label">' + esc(f.label) + '</span>'
            + '<span class="lr-fac-hit-w">权重 ' + (w == null ? '--' : w.toFixed(2)) + '</span>'
            + (f.note ? '<span class="lr-fac-hit-note" title="' + esc(f.note) + '">ⓘ</span>' : '')
            + '</div>'
            + (f.evidence && f.evidence.length
              ? '<div class="lr-fac-hit-ev">' + f.evidence.map(esc).join('；') + '</div>' : '')
            + '</div>';
        }).join('');
      };
      var bal = num(fac.balance);
      facHtml = '<div><div class="lr-detail-sec-head">买卖因素命中（规则库判定，可配置）</div>'
        + '<div class="lr-fac-hit-group">'
        + '<div class="lr-fac-hit-side buy"><b>买入侧因素</b>' + facList(fac.buy, 'buy') + '</div>'
        + '<div class="lr-fac-hit-side sell"><b>卖出侧因素</b>' + facList(fac.sell, 'sell') + '</div>'
        + '</div>'
        + (bal != null
          ? '<div class="lr-note-line">多空因素差值 balance = ' + (bal > 0 ? '+' : '') + bal.toFixed(2)
            + '（正 = 买入理由更强，负 = 兑现条件更强，接近 0 = 分歧）</div>' : '')
        + '</div>';
    }

    var detail = '<tr class="lr-detail"><td colspan="8"><div class="lr-detail-inner">'
      + '<div><div class="lr-detail-sec-head">五维分数（0–1，取最高者为主因）</div><div class="lr-dims">' + dims + '</div></div>'
      + '<div><div class="lr-detail-sec-head">各维度证据</div><div class="lr-evidence">' + evRows + '</div></div>'
      + '<div><div class="lr-detail-sec-head">价位、时机与席位角色</div><div class="lr-kv">' + kv + '</div></div>'
      + facHtml
      + (x.timing ? '<div class="lr-note-line"><b>买入/卖出时机：</b>' + esc(x.timing) + '</div>' : '')
      + (x.sell_trigger ? '<div class="lr-note-line"><b>卖出触发条件：</b>' + esc(x.sell_trigger) + '</div>' : '')
      + '<div><div class="lr-detail-sec-head">推演逻辑</div><div class="lr-logic">' + esc(x.logic || '') + '</div></div>'
      + (x.alias_source ? '<div class="lr-note-line">席位别名来源：' + esc(x.alias_source) + '</div>' : '')
      + '</div></td></tr>';

    return row + detail;
  }

  function dimKey(label) {
    return ({
      '题材炒作': 'theme', '市场情绪': 'mood', '资金博弈': 'game',
      '消息面': 'news', '技术形态': 'technical'
    })[label] || label;
  }

  // ---------------------------------------------------------------- 席位画像
  function renderSeats(ins) {
    var rows = (ins && ins.by_seat) || [];
    if (!rows.length) return '';
    var body = rows.map(function (s) {
      var focusCls = s.focus === '集中狙击' ? 'warn' : s.focus === '通道汇总' ? 'other' : 'ok';
      return '<tr>'
        + '<td>' + seatTag(s.seat_type, s.type_label)
        + ' <span class="lr-stock-name">' + esc(s.alias || s.seat) + '</span>'
        + (s.style ? '<div class="lr-muted" style="font-size:10.5px;margin-top:2px">' + esc(s.style) + '</div>' : '')
        + '</td>'
        + '<td class="num">' + s.trades + '</td>'
        + '<td class="num"><span class="lr-up">' + s.buy_count + '</span> / <span class="lr-down">' + s.sell_count + '</span></td>'
        + '<td class="num">' + signed(s.net_wan) + '</td>'
        + '<td class="num">' + s.stocks + ' 只</td>'
        + '<td><span class="lr-tag ' + focusCls + '">' + esc(s.focus) + '</span>'
        + (s.top_share != null ? ' <span class="lr-muted" style="font-size:10.5px">单票 ' + pctText(s.top_share) + '</span>' : '')
        + '</td>'
        + '<td class="num">' + (s.win_rate_3d == null ? '--' : s.win_rate_3d + '%') + '</td>'
        + '<td class="lr-muted" style="font-size:11px">'
        + (s.stocks_detail || []).slice(0, 4).map(function (d) {
          return esc(d.name) + (d.side === 'buy' ? '<span class="lr-up">买</span>' : '<span class="lr-down">卖</span>');
        }).join('、') + '</td>'
        + '</tr>';
    }).join('');

    var head = '<tr><th>席位</th><th class="num">笔数</th><th class="num">买 / 卖</th>'
      + '<th class="num">净额</th><th class="num">涉及</th><th>集中度</th>'
      + '<th class="num">3 日成功率</th><th>主要操作</th></tr>';

    return panel('席位视角：谁在出手，怎么出手', 'BY SEAT',
      '<div class="lr-table-wrap"><table class="lr-table"><thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>'
      + '<div class="lr-note-line" style="margin:12px 15px 14px">'
      + '「机构专用」「北向资金」标注为<b>通道汇总</b>——它们背后是几十上百个互不相干的账户，'
      + '把这些名字当成「一个人」去算集中度是误读，所以这一列的集中度对通道不计算。'
      + '只有具体营业部（游资/席位）的「集中狙击 / 分散试仓」才代表真实打法。</div>', null, true);
  }

  // ---------------------------------------------------------------- 题材资金
  function renderIndustry(ins) {
    var rows = (ins && ins.by_industry) || [];
    if (rows.length < 2) return '';
    var maxAbs = Math.max.apply(null, rows.map(function (x) { return Math.abs(x.net_wan || 0); }).concat([1]));
    var body = rows.map(function (x) {
      var w = Math.round((Math.abs(x.net_wan) / maxAbs) * 100);
      var up = x.net_wan >= 0;
      return '<div class="lr-bar-row">'
        + '<div class="lr-bar-label" title="' + esc(x.industry) + '">' + esc(x.industry.length > 6 ? x.industry.slice(0, 6) : x.industry) + '</div>'
        + '<span class="lr-bar-track"><i class="' + (up ? 'lr-bar-buy' : 'lr-bar-sell') + '" style="width:' + w + '%"></i></span>'
        + '<div class="lr-bar-meta">' + signed(x.net_wan) + '</div>'
        + '</div>';
    }).join('');
    return panel('题材视角：钱往哪里去', 'BY INDUSTRY', '<div class="lr-bars">' + body + '</div>'
      + '<div class="lr-note-line" style="margin-top:12px">'
      + '行业归属取自复盘日涨停池。未涨停的标的归入「未归入涨停池」——'
      + '这是事实（该票当日没涨停），不是数据缺失，所以不丢弃也不猜测归属。</div>');
  }

  // ---------------------------------------------------------------- 说明
  function renderCaveats(r, ins) {
    var list = (r.caveats || []).slice();
    if (ins && ins.notes) list = list.concat(ins.notes);
    if (!list.length) { $('lr-caveats').hidden = true; return; }
    $('lr-caveat-list').innerHTML = list.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('');
    $('lr-caveats').hidden = false;
  }

  // ---------------------------------------------------------------- 面板
  function panel(title, en, body, side, flush) {
    return '<section class="lr-panel">'
      + '<div class="lr-panel-head"><strong>' + esc(title) + '</strong><span>' + esc(en) + '</span>'
      + (side ? '<span class="lr-head-side">' + esc(side) + '</span>' : '')
      + '</div>'
      + '<div class="lr-panel-body' + (flush ? ' is-flush' : '') + '">' + body + '</div>'
      + '</section>';
  }

  // ---------------------------------------------------------------- 交互
  function scrollToRecords() {
    var tables = document.querySelectorAll('table.lr-table');
    var target = null;
    for (var i = 0; i < tables.length; i++) {
      if (tables[i].querySelector('tr.lr-rec')) { target = tables[i]; break; }
    }
    if (!target) return;
    var panel = target.closest('.lr-panel');
    if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  document.addEventListener('click', function (e) {
    var t = e.target;

    // 点标的（要点卡片 / 标的表）→ 收敛明细
    var jump = t.closest('[data-jump-stock]');
    if (jump) {
      state.filters.stock = jump.getAttribute('data-jump-stock');
      state.open = {};
      render();
      scrollToRecords();
      return;
    }

    // 清掉标的筛选
    var clearStock = t.closest('[data-f-stock]');
    if (clearStock) {
      state.filters.stock = null;
      render();
      return;
    }

    // 主因条
    var mb = t.closest('[data-filter-motive]');
    if (mb) {
      var m = mb.getAttribute('data-filter-motive');
      state.filters.motive = state.filters.motive === m ? '' : m;
      render();
      scrollToRecords();
      return;
    }

    // 筛选 chips
    var fs = t.closest('[data-f-seat]');
    if (fs) { state.filters.seat = fs.getAttribute('data-f-seat'); render(); return; }
    var fd = t.closest('[data-f-side]');
    if (fd) { state.filters.side = fd.getAttribute('data-f-side'); render(); return; }
    var fm = t.closest('[data-f-motive]');
    if (fm) { state.filters.motive = fm.getAttribute('data-f-motive'); render(); return; }

    if (t.closest('#lr-reset')) {
      state.filters = { seat: '', side: '', motive: '', q: '', minNet: 0, stock: null };
      state.open = {};
      render();
      return;
    }

    // 展开 / 收起明细：只记录展开状态后重绘，并把滚动位置还原，
    // 否则每次展开都会把读者甩回页首。
    var row = t.closest('tr.lr-rec');
    if (row) {
      var key = row.getAttribute('data-rec');
      if (state.open[key]) delete state.open[key]; else state.open[key] = true;
      var y = window.scrollY;
      render();
      window.scrollTo(0, y);
      return;
    }

    // 表头排序
    var th = t.closest('th[data-sort]');
    if (th) {
      var k = th.getAttribute('data-sort');
      if (state.sort.key === k) state.sort.dir = -state.sort.dir;
      else { state.sort.key = k; state.sort.dir = k === 'name' || k === 'industry' ? 1 : -1; }
      render();
      return;
    }
  });

  document.addEventListener('input', function (e) {
    if (e.target && e.target.id === 'lr-q') {
      state.filters.q = e.target.value.trim();
      // 输入时不重绘输入框本身，只更新表格：整体重绘会丢焦点
      var pos = e.target.selectionStart;
      render();
      var box = $('lr-q');
      if (box) { box.focus(); try { box.setSelectionRange(pos, pos); } catch (err) { } }
    }
  });

  document.addEventListener('change', function (e) {
    if (e.target && e.target.id === 'lr-date') {
      var v = (e.target.value || '').trim();
      if (/^\d{4}-\d{2}-\d{2}$/.test(v)) { state.date = v; load(v, false); }
    }
  });

  function shiftDate(step) {
    var dates = state.recent.map(function (x) { return x.date; });
    if (!dates.length) return;
    var cur = state.date;
    var idx = dates.indexOf(cur);
    if (idx < 0) idx = 0;
    var next = dates[idx + step];
    if (!next) return;
    state.date = next;
    $('lr-date').value = next;
    load(next, false);
  }

  $('lr-load').addEventListener('click', function () {
    var v = ($('lr-date').value || '').trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(v)) { state.date = v; load(v, false); }
    else load(state.date, false);
  });
  $('lr-regen').addEventListener('click', function () {
    var v = ($('lr-date').value || '').trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(v)) state.date = v;
    load(state.date, true);
  });
  $('lr-prev').addEventListener('click', function () { shiftDate(1); });   // recent 是倒序，+1 是更早
  $('lr-next').addEventListener('click', function () { shiftDate(-1); });

  // ---------------------------------------------------------------- 启动
  loadRecent().then(function () {
    var latest = state.recent.length ? state.recent[0].date : '';
    if (latest) { state.date = latest; $('lr-date').value = latest; }
    load(latest, false);
  });
})();
