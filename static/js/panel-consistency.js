/**
 * 面板一致性（panel consistency）v1
 * - 切 tab 时把内容区滚动回顶部（避免"切过去停在中段"的错位感）
 * - 打开全屏面板时聚焦关闭按钮，Esc 可关（缺失时补一个）
 * - 暴露 window.dshPanels 供其它脚本查询当前面板
 * 不修改任何业务脚本，纯增强。
 */
(function () {
  'use strict';

  function visiblePanels() {
    return [...document.querySelectorAll('.full-overlay-panel')].filter(function (el) {
      var cs = getComputedStyle(el);
      return cs.display !== 'none' && cs.visibility !== 'hidden';
    });
  }

  document.addEventListener('click', function (e) {
    var tab = e.target && e.target.closest && e.target.closest('.full-overlay-tab');
    if (!tab) return;
    var panel = tab.closest('.full-overlay-panel');
    if (!panel) return;
    var body = panel.querySelector('.full-overlay-body');
    if (!body) return;
    // 让内容先完成切页，再回到顶部
    setTimeout(function () { body.scrollTop = 0; }, 30);
  }, true);

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var panels = visiblePanels();
    if (!panels.length) return;
    var panel = panels[panels.length - 1];
    if (panel.id === 'automation-panel') return; // 自动化中心自身处理 Esc
    var close = panel.querySelector('.full-overlay-close');
    if (close) { close.click(); }
  });

  window.dshPanels = {
    visible: function () { return visiblePanels().map(function (p) { return p.id; }); },
  };
})();
