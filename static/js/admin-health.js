/* ================================================================
 * 后台 · 系统健康总览（升级模块状态）
 *   static/js/admin-health.js
 *
 * 数据源：GET /api/admin/system-health（server/routers/admin.py）
 * 五块：外部因子新鲜度 / 离线产物 / 交易引擎 / 实时推送 / 功耗与缓存
 *
 * 硬约束（用户红线）：
 *   - 取不到一律显示 `--`，**绝不显示 0**（0 会被当真值读）
 *   - 每条信息必须带来源与时间戳，不出现裸数字
 *   - CPU / 内存 / 缓存 / 命中率属**非方向性**指标 → 不用红绿，用中性色 + 阈值文案
 *   - 端点未就绪 / ok:false → 显式「数据源不可用 + reason」空态，不用假数据填充
 *   - 轮询收敛为**单一可暂停定时器**：页面隐藏（document.hidden）时停止
 * ================================================================ */
(function () {
  'use strict';

  var API = '/api/admin/system-health';
  var INTERVAL_MS = 45000;
  var timer = null;
  var inflight = false;
  var failures = 0;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  // 空值统一渲染为 '--'（不写 0）
  function val(v, suffix, digits) {
    if (v === null || v === undefined || v === '') return '<span class="ah-na">--</span>';
    if (typeof v === 'number' && isFinite(v)) {
      var n = (digits === undefined) ? v : Number(v.toFixed(digits));
      return esc(String(n)) + (suffix || '');
    }
    return esc(String(v)) + (suffix || '');
  }
  function pct(v) { return val(v, '%', 2); }
  function mb(v) { return (v === null || v === undefined) ? '<span class="ah-na">--</span>' : esc(String(v)) + ' MB'; }

  // 中性色阈值文案（非方向性指标 → 不使用红绿）
  function neutral(v, warn, bad, unit) {
    if (v === null || v === undefined || !isFinite(v)) return '<span class="ah-na">--</span>';
    var cls = 'ah-neutral';
    var tag = '正常';
    if (v >= bad) { cls = 'ah-neutral-strong'; tag = '偏高'; }
    else if (v >= warn) { cls = 'ah-neutral-mid'; tag = '关注'; }
    return '<span class="' + cls + '">' + esc(String(v)) + (unit || '') + ' · ' + tag + '</span>';
  }

  function block(title, sourceText, bodyHtml) {
    return '<div class="ah-block">' +
      '<div class="ah-block-head"><span class="ah-block-title">' + esc(title) + '</span>' +
      '<span class="ah-block-src" title="数据来源">' + esc(sourceText || '来源未标注') + '</span></div>' +
      '<div class="ah-block-body">' + bodyHtml + '</div></div>';
  }

  function unavailable(what, reason) {
    return '<div class="ah-empty">数据源不可用：' + esc(what) +
      (reason ? '（' + esc(reason) + '）' : '') + '</div>';
  }

  function kv(k, v) {
    return '<div class="ah-kv"><span class="ah-k">' + esc(k) + '</span><span class="ah-v">' + v + '</span></div>';
  }

  // ---------- ① 外部因子新鲜度 ----------
  function renderExternal(ef) {
    if (!ef || !ef.items) return unavailable('/api/admin/system-health → external_factors', '未返回');
    var rows = ef.items.map(function (x) {
      var staleCls = x.stale === true ? 'ah-flag-stale' : (x.stale === false ? 'ah-flag-ok' : '');
      var staleTxt = x.stale === true ? '落后于最新' : (x.stale === false ? '最新' : '--');
      return '<tr>' +
        '<td>' + esc(x.symbol) + '</td>' +
        '<td>' + val(x.last_date) + '</td>' +
        '<td>' + val(x.rows) + '</td>' +
        '<td>' + val(x.fetch_ts) + '</td>' +
        '<td><span class="' + staleCls + '">' + staleTxt + '</span></td>' +
        '</tr>';
    }).join('');
    var head = '<div class="ah-sub">可用 ' + val(ef.available) + ' / ' + val(ef.count) +
      ' ｜ 最新交易日 ' + val(ef.latest_last_date) +
      ' ｜ 陈旧 ' + val(ef.stale_count) + ' 项</div>';
    return head + '<table class="ah-table"><thead><tr>' +
      '<th>因子</th><th>最后交易日</th><th>行数</th><th>抓取时间</th><th>新鲜度</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>';
  }

  // ---------- ② 离线构建产物 ----------
  function renderArtifacts(oa) {
    if (!oa || !oa.items) return unavailable('/api/admin/system-health → offline_artifacts', '未返回');
    var rows = oa.items.map(function (a) {
      return '<tr>' +
        '<td>' + esc(a.file) + '</td>' +
        '<td>' + val(a.count) + '</td>' +
        '<td>' + val(a.mtime) + '</td>' +
        '<td>' + val((a.size_bytes === null || a.size_bytes === undefined) ? null : (a.size_bytes / 1024).toFixed(1), ' KB', 1) + '</td>' +
        '<td>' + (a.reason ? '<span class="ah-na">' + esc(a.reason) + '</span>' : '就绪') + '</td>' +
        '</tr>';
    }).join('');
    return '<table class="ah-table"><thead><tr>' +
      '<th>产物</th><th>条目数</th><th>构建时间</th><th>大小</th><th>状态</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>';
  }

  // ---------- ③ 交易引擎 ----------
  function renderTrading(te) {
    if (!te) return unavailable('/api/admin/system-health → trading_engine', '未返回');
    if (te.available === false) return unavailable('交易引擎', te.reason);
    var dist = te.distribution || {};
    var distHtml = Object.keys(dist).map(function (k) {
      return '<span class="ah-chip">' + esc(k) + ' <b>' + esc(String(dist[k])) + '</b></span>';
    }).join('') || '<span class="ah-na">--</span>';
    return kv('初始本金', val(te.initial_cash, ' 元')) +
      kv('当前现金', val(te.cash, ' 元')) +
      kv('订单总数', val(te.orders_total)) +
      kv('真实单 / 演练单', '<b>' + val(te.orders_real) + '</b> / ' + val(te.orders_drill) +
        ' <span class="ah-note">（演练单不计入真实统计）</span>') +
      '<div class="ah-kv ah-kv-col"><span class="ah-k">状态分布</span><span class="ah-v">' + distHtml + '</span></div>';
  }

  // ---------- ④ 实时推送 ----------
  function renderSse(s) {
    if (!s) return unavailable('/api/stream/stats', '未返回');
    if (s.available === false) return unavailable('实时推送', s.reason);
    var subs = (s.subscribers || []).map(function (x) {
      return '<span class="ah-chip">sid ' + esc(String(x.sid)) + ' · ' + esc((x.topics || []).join('/')) +
        ' · 队内 ' + esc(String(x.queued)) + ' · 丢弃 ' + esc(String(x.dropped)) + '</span>';
    }).join('') || '<span class="ah-na">无在线订阅</span>';
    return kv('在线订阅', val(s.active_clients) + ' <span class="ah-note">（累计连接 ' + esc(String(s.clients_total_ever == null ? '--' : s.clients_total_ever)) + '）</span>') +
      kv('累计广播事件', val(s.events_broadcast)) +
      kv('丢弃事件', val(s.events_dropped)) +
      kv('上游刷新 / 缓存命中', val(s.market_refresh_invocations) + ' / ' + val(s.market_cache_hits)) +
      kv('启动时间', val(s.started_at)) +
      '<div class="ah-kv ah-kv-col"><span class="ah-k">订阅明细</span><span class="ah-v">' + subs + '</span></div>';
  }

  // ---------- ⑤ 功耗与缓存 ----------
  function renderPower(pc) {
    if (!pc) return unavailable('/api/admin/system-health → power_cache', '未返回');
    var buckets = pc.cache_ttl_buckets || {};
    var bucketTxt = Object.keys(buckets).map(function (k) {
      return '<span class="ah-chip">' + esc(k) + ' <b>' + esc(String(buckets[k])) + '</b></span>';
    }).join('') || '<span class="ah-na">--</span>';
    return kv('进程 CPU（口径 ' + esc(pc.cpu_scope || '--') + '）',
        pc.cpu_percent == null
          ? '<span class="ah-na" title="' + esc(pc.cpu_reason || '') + '">-- <i>' + esc(pc.cpu_reason || '暂不可得') + '</i></span>'
          : neutral(pc.cpu_percent, 60, 85, '%')) +
      kv('进程内存 RSS', mb(pc.mem_process_rss_mb)) +
      kv('整机内存占用', neutral(pc.mem_system_used_pct, 75, 90, '%')) +
      kv('API 调用表行数', val(pc.api_calls_rows) + ' <span class="ah-note">（' + esc(pc.api_calls_table || '--') + '，' +
        val((pc.api_calls_size_bytes === null || pc.api_calls_size_bytes === undefined) ? null : (pc.api_calls_size_bytes / 1048576).toFixed(2), ' MB', 2) + '）</span>') +
      kv('上次清理时间', val(pc.api_calls_last_cleanup)) +
      kv('缓存条目', val(pc.cache_entries) + ' <span class="ah-note">（5s 内过期 ' + esc(String(pc.cache_expiring_within_5s == null ? '--' : pc.cache_expiring_within_5s)) + '）</span>') +
      '<div class="ah-kv ah-kv-col"><span class="ah-k">TTL 分布</span><span class="ah-v">' + bucketTxt + '</span></div>';
  }

  function render(payload) {
    var root = document.getElementById('system-health-root');
    if (!root) return;
    var d = (payload && payload.data) || null;
    if (!d) {
      root.innerHTML = unavailable(API, (payload && (payload.reason || payload.detail)) || '未返回 data');
      return;
    }
    var ef = d.external_factors || {};
    var oa = d.offline_artifacts || {};
    var te = d.trading_engine || {};
    var sse = d.sse || {};
    var pc = d.power_cache || {};

    root.innerHTML =
      block('① 外部因子数据新鲜度（M2 全球传导）', ef.source, renderExternal(ef)) +
      block('② 离线构建产物（M3 板块体系）', oa.source, renderArtifacts(oa)) +
      block('③ 交易引擎（虚拟盘 · 真实/演练分离）', te.source, renderTrading(te)) +
      block('④ 实时推送（SSE）', sse.source, renderSse(sse)) +
      block('⑤ 功耗与缓存治理', pc.source, renderPower(pc));

    var upd = document.getElementById('syshealth-updated');
    if (upd) upd.textContent = '更新于 ' + (payload.generated_at || '--') + ' ｜ 每 45s 刷新（页面隐藏时暂停）';
  }

  function load() {
    if (inflight) return;
    inflight = true;
    fetch(API, { headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (j) {
        failures = 0;
        if (j && j.ok === false) { render({ data: null, reason: j.reason || j.message }); return; }
        render(j);
      })
      .catch(function (e) {
        failures++;
        render({ data: null, reason: '请求失败（第 ' + failures + ' 次）：' + e.message });
      })
      .then(function () { inflight = false; });
  }

  // 单一可暂停定时器：页面隐藏即停止轮询（CPU/功耗侧的真实收益）
  function start() {
    if (timer) return;
    load();
    timer = setInterval(load, INTERVAL_MS);
  }
  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { stop(); }
    else { start(); }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { if (!document.hidden) start(); });
  } else if (!document.hidden) {
    start();
  }

  // 供运维/验收脚本查询（不暴露敏感信息）
  window.__adminHealth = { load: load, start: start, stop: stop, isRunning: function () { return timer !== null; } };
})();
