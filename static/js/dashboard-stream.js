/* ================================================================
 * DAPENG Alpha — 行情/信号实时推送（SSE）客户端助手  v3
 * static/js/dashboard-stream.js
 *
 * 用法（与 v1/v2 完全兼容，老调用方无需修改）：
 *   var ctrl = window.DashboardStream.create({
 *     url: '/api/stream/market',
 *     onEvent:    function (name, payload) { ... },   // 命名事件回调
 *     onOpen:     function () { ... },                // 连接成功
 *     onDegrade:  function (reason) { ... },          // 进入降级（软降级：仍会继续重连）
 *     onReconnect:function (attempt, delayMs) { ... },// 即将第 attempt 次重连
 *     onResync:   function () { ... },                // 重连成功 → 请拉一次快照再吃增量
 *     onStale:    function (idleMs) { ... }           // 假死看门狗触发
 *   });
 *   ctrl.stats();   // {reconnects, dropped, failures, lastEventAt, idleMs, degraded, reconnecting}
 *   ctrl.stop();
 *
 * v3 韧性改造（对齐「断线自动重连 / 数据异常容错 / 状态可见」要求）：
 * 1. **指数退避自动重连**：1s→2s→4s→8s→16s→30s（上限 30s），每次 ±20% 抖动；
 *    默认无限重试（maxReconnectAttempts=0），不再像 v1/v2 那样失败 3 次就永久关闭。
 * 2. **假死看门狗**：lastEventAt 超过 staleTimeout（默认 45s）→ 主动断开并重连。
 *    依赖后端命名 `ping` 事件（SSE 注释行不触发 JS 事件，无法刷新 lastEventAt）。
 * 3. **重连后重新对齐**：onOpen 之后额外回调 onResync()，调用方拉一次快照补空洞。
 * 4. **数据异常丢弃**：payload.seq 回退/重复 → 计数并丢弃，不回调 onEvent；
 *    JSON 解析失败同样计数（可观测，不再静默吞掉）。
 * 5. 事件白名单补 ping / quotes.unavailable / quotes.snapshot。
 * ================================================================ */
(function (global) {
  'use strict';

  var DEFAULT_FAIL_THRESHOLD = 3;      // 连续失败进入「软降级」的门槛（兼容 v1 语义）
  var DEFAULT_STALE_TIMEOUT = 45000;   // 假死判定（毫秒）
  var BACKOFF_BASE = 1000;             // 退避基数
  var BACKOFF_MAX = 30000;             // 退避上限
  var WATCHDOG_INTERVAL = 5000;        // 看门狗巡检间隔

  var EVENT_NAMES = [
    'connected', 'ping',
    'market.snapshot',
    'signals.snapshot', 'signal.new', 'order.new', 'signals.unavailable',
    // K 线/分时实时报价：缺白名单则事件永远收不到
    'quotes.tick', 'quotes.snapshot', 'quotes.unavailable'
  ];

  function nowMs() {
    return (typeof Date !== 'undefined' && Date.now) ? Date.now() : new Date().getTime();
  }

  /** 指数退避 + ±20% 抖动（单位毫秒）。 */
  function backoffDelay(attempt) {
    var n = Math.max(1, attempt) - 1;
    var base = Math.min(BACKOFF_MAX, BACKOFF_BASE * Math.pow(2, n));
    var jitter = base * 0.2;
    var d = base + (Math.random() * 2 - 1) * jitter;
    return Math.max(300, Math.round(Math.min(BACKOFF_MAX, d)));
  }

  function create(opts) {
    opts = opts || {};
    var url = opts.url;
    var onEvent = typeof opts.onEvent === 'function' ? opts.onEvent : function () {};
    var onOpen = typeof opts.onOpen === 'function' ? opts.onOpen : function () {};
    var onDegrade = typeof opts.onDegrade === 'function' ? opts.onDegrade : function () {};
    var onReconnect = typeof opts.onReconnect === 'function' ? opts.onReconnect : function () {};
    var onResync = typeof opts.onResync === 'function' ? opts.onResync : function () {};
    var onStale = typeof opts.onStale === 'function' ? opts.onStale : function () {};

    var failThreshold = opts.failThreshold || DEFAULT_FAIL_THRESHOLD;
    // 0 = 无限重试（默认）；>0 = 超过该次数后放弃并永久降级
    var maxReconnectAttempts = (typeof opts.maxReconnectAttempts === 'number')
      ? opts.maxReconnectAttempts : 0;
    var staleTimeout = opts.staleTimeout || DEFAULT_STALE_TIMEOUT;

    var state = {
      failures: 0,            // 连续失败次数
      degraded: false,        // 软降级（仍会自动重连）；调用方可据此回落轮询
      connected: false,
      stopped: false,
      reconnecting: false,    // 是否处于「等待重连」窗口
      attempt: 0,             // 本次断线后的重连尝试序号
      reconnects: 0,          // 累计重连成功次数
      dropped: 0,             // 丢弃事件数（seq 回退/重复 + JSON 解析失败）
      parseErrors: 0,
      lastEventAt: 0,
      lastSeq: null,
      reason: ''
    };
    var es = null;
    var retryTimer = null;
    var watchdogTimer = null;

    function supported() {
      return typeof global.EventSource !== 'undefined' || typeof EventSource !== 'undefined';
    }

    function clearRetry() {
      if (retryTimer) {
        try { clearTimeout(retryTimer); } catch (e) { /* ignore */ }
        retryTimer = null;
      }
    }

    function hardClose() {
      if (es) {
        try { es.close(); } catch (e) { /* ignore */ }
        es = null;
      }
    }

    /** 软降级：通知调用方「现在没推送、请先用轮询」，但**不放弃重连**。 */
    function degrade(reason) {
      if (state.degraded) return;
      state.degraded = true;
      state.connected = false;
      state.reason = reason || '推送不可用';
      onDegrade(state.reason);
    }

    function scheduleReconnect(reason) {
      if (state.stopped) return;
      hardClose();
      state.connected = false;
      state.attempt += 1;

      if (maxReconnectAttempts > 0 && state.attempt > maxReconnectAttempts) {
        state.reconnecting = false;
        state.reason = '重连已达上限（' + maxReconnectAttempts + ' 次）：' + (reason || '');
        degrade(state.reason);
        return;
      }

      var delay = backoffDelay(state.attempt);
      state.reconnecting = true;
      onReconnect(state.attempt, delay);
      clearRetry();
      retryTimer = global.setTimeout(function () {
        retryTimer = null;
        open();
      }, delay);
    }

    function handleError() {
      if (state.stopped) return;
      state.connected = false;
      state.failures += 1;
      if (state.failures >= failThreshold) {
        degrade('推送连接连续失败 ' + state.failures + ' 次（持续自动重连中）');
      }
      scheduleReconnect('连接错误');
    }

    function handlePayloadText(text) {
      if (text == null) {
        state.dropped += 1;
        state.parseErrors += 1;
        return null;
      }
      try {
        return JSON.parse(text);
      } catch (e) {
        state.dropped += 1;
        state.parseErrors += 1;
        return null;
      }
    }

    function dispatch(name, ev) {
      state.lastEventAt = nowMs();
      var payload = handlePayloadText(ev && ev.data);
      if (payload === null) return;

      // seq 单调校验：回退/重复一律丢弃（防止乱序、重放污染图表）
      if (payload && typeof payload.seq === 'number') {
        if (state.lastSeq !== null && payload.seq <= state.lastSeq) {
          state.dropped += 1;
          return;
        }
        state.lastSeq = payload.seq;
      }
      onEvent(name, payload);
    }

    function open() {
      if (state.stopped) return;
      if (!supported()) { degrade('当前浏览器不支持 EventSource'); return; }
      clearRetry();
      try {
        es = new global.EventSource(url);
      } catch (e) {
        degrade('EventSource 初始化失败：' + (e && e.message ? e.message : e));
        scheduleReconnect('初始化失败');
        return;
      }

      es.onopen = function () {
        var wasReconnect = state.attempt > 0 || state.failures > 0 || state.degraded;
        state.connected = true;
        state.reconnecting = false;
        state.failures = 0;
        state.attempt = 0;
        state.degraded = false;
        state.reason = '';
        state.lastSeq = null;        // 重连后以服务端新序列为准
        state.lastEventAt = nowMs();
        onOpen();
        if (wasReconnect) {
          state.reconnects += 1;
          // 重连成功：调用方据此拉一次完整快照，避免「断线期间的空洞」
          try { onResync(); } catch (e) { /* 调用方异常不影响连接 */ }
        }
      };

      es.onerror = function () {
        // 浏览器 EventSource 自身也会重连，这里接管为「指数退避 + 看门狗」策略
        handleError();
      };

      EVENT_NAMES.forEach(function (name) {
        es.addEventListener(name, function (ev) { dispatch(name, ev); });
      });
    }

    function startWatchdog() {
      if (watchdogTimer) return;
      watchdogTimer = global.setInterval(function () {
        if (state.stopped || !state.connected) return;
        var idle = state.lastEventAt ? (nowMs() - state.lastEventAt) : staleTimeout + 1;
        if (idle > staleTimeout) {
          // 假死：连接层没报错但已无任何事件（含命名 ping）→ 主动断开重连
          state.reason = '连接假死 ' + Math.round(idle / 1000) + 's 无事件';
          try { onStale(idle); } catch (e) { /* ignore */ }
          scheduleReconnect('stale');
        }
      }, WATCHDOG_INTERVAL);
    }

    function stopWatchdog() {
      if (watchdogTimer) {
        try { clearInterval(watchdogTimer); } catch (e) { /* ignore */ }
        watchdogTimer = null;
      }
    }

    var controller = {
      state: state,
      /**
       * QA/调试用：把一段原始 data 文本喂给同一 dispatch 管线
       * （用于验证 seq 去重 / JSON 解析容错，不经过网络）。
       */
      feed: function (name, dataText) {
        dispatch(name, { data: dataText });
      },
      stop: function () {
        state.stopped = true;
        state.reconnecting = false;
        clearRetry();
        stopWatchdog();
        hardClose();
      },
      /** 手动强制重连（调试/测试用）。 */
      reconnectNow: function () {
        if (state.stopped) return;
        scheduleReconnect('manual');
      },
      isDegraded: function () { return state.degraded; },
      isConnected: function () { return state.connected && !state.degraded; },
      lastEventAt: function () { return state.lastEventAt; },
      /** 运行态快照（验收：reconnects / dropped / failures / idleMs ...）。 */
      stats: function () {
        return {
          reconnects: state.reconnects,
          dropped: state.dropped,
          parseErrors: state.parseErrors,
          failures: state.failures,
          lastEventAt: state.lastEventAt,
          idleMs: state.lastEventAt ? (nowMs() - state.lastEventAt) : null,
          degraded: state.degraded,
          reconnecting: state.reconnecting,
          connected: state.connected,
          attempt: state.attempt,
          lastSeq: state.lastSeq
        };
      }
    };

    open();
    startWatchdog();
    return controller;
  }

  global.DashboardStream = { create: create, version: 3, backoffDelay: backoffDelay };
})(window);
