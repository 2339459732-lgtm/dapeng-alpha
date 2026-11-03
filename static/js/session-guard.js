/* 会话守卫（access-key.v2）
 * ---------------------------------------------------------------------------
 * 两件事：
 * 1. **401 即回登录页**：会话空闲超时/被撤销后，页面里的轮询会拿到 401；
 *    不处理的话用户只会看到一堆「加载失败」，不知道要重新登录。
 * 2. **可选：关页即退出**（服务端 REQUIRE 由 /api/auth/status 告知）：
 *    浏览器关闭时会话 Cookie 本就清除；开启 PAGE_CLOSE_LOGOUT 后，关闭标签页
 *    也会调用一次登出，把服务端会话一并撤销。
 *
 * 不做的事：不在前端存任何密钥/令牌——会话只存在于 HttpOnly Cookie 里。
 */
(function () {
  'use strict';

  var redirecting = false;
  var pageCloseLogout = false;

  function goLogin() {
    if (redirecting) return;
    if (/\/login(\.html)?$/.test(location.pathname)) return;
    redirecting = true;
    var next = location.pathname + location.search;
    location.replace('/login?next=' + encodeURIComponent(next));
  }

  // 包装 fetch：只拦同源 /api/ 的 401，登录接口本身除外
  var origFetch = window.fetch;
  if (typeof origFetch === 'function') {
    window.fetch = function (input, init) {
      var url = typeof input === 'string' ? input : (input && input.url) || '';
      return origFetch.apply(this, arguments).then(function (resp) {
        if (resp && resp.status === 401 && url.indexOf('/api/') === 0
            && url.indexOf('/api/auth/') !== 0) {
          goLogin();
        }
        return resp;
      });
    };
  }

  // 读取会话策略（是否开启关页登出）
  try {
    origFetch.call(window, '/api/auth/status', { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        pageCloseLogout = !!(d && d.enabled && d.page_close_logout);
        if (d && d.enabled && d.authenticated === false) goLogin();
      })
      .catch(function () { /* 状态接口不可用时不打扰用户 */ });
  } catch (e) { /* 老浏览器忽略 */ }

  window.addEventListener('pagehide', function (e) {
    if (!pageCloseLogout || e.persisted) return;
    try {
      var body = new Blob(['{}'], { type: 'application/json' });
      navigator.sendBeacon('/api/auth/logout', body);
    } catch (err) { /* 尽力而为 */ }
  });
})();
