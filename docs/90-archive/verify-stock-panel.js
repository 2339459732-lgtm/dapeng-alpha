/**
 * 股票面板 v7 验证脚本（已归档至 docs/90-archive/）
 * 截图输出: docs/assets/stock-panel-{layout,hover,contextmenu,empty,sort,light}.png
 */
const { chromium } = require('playwright');
const path = require('path');

const BASE = 'http://localhost:8900/dashboard.html';
const OUT = path.resolve(__dirname, '..', 'assets');

const TEST_STOCKS = [
  { code: '600519', name: '贵州茅台', price: 1580.20, yclose: 1436.80, chg: 9.98, score: 86, turnover: 1.2, volume_ratio: 2.14, signals: [{ type: 'momentum', label: '动量↑', score: 82 }, { type: 'breakout', label: '突破', score: 76 }, { type: 'macd_gold', label: 'MACD金', score: 68 }] },
  { code: '300750', name: '宁德时代', price: 268.40, yclose: 253.20, chg: 6.00, score: 74, turnover: 3.4, volume_ratio: 1.85, signals: [{ type: 'breakout', label: '突破', score: 71 }, { type: 'kc_upper', label: 'KC上轨', score: 60 }] },
  { code: '688981', name: '中芯国际', price: 92.15, yclose: 90.10, chg: 2.27, score: 68, turnover: 5.6, volume_ratio: 3.02, signals: [{ type: 'momentum', label: '动量↑', score: 77 }, { type: 'boll_break', label: 'BOLL破', score: 64 }] },
  { code: '002415', name: '海康威视', price: 34.60, yclose: 35.10, chg: -1.42, score: 55, turnover: 0.8, volume_ratio: 0.95, signals: [{ type: 'rsi_oversold', label: 'RSI超卖', score: 58 }] },
  { code: '601899', name: '紫金矿业', price: 17.84, yclose: 19.02, chg: -6.20, score: 41, turnover: 2.9, volume_ratio: 2.4, signals: [{ type: 'macd_dead', label: 'MACD死', score: 38 }] },
  { code: '000858', name: '五粮液', price: 148.30, yclose: 164.20, chg: -9.68, score: 29, turnover: 4.1, volume_ratio: 5.2, signals: [{ type: 'rsi_overbought', label: 'RSI超买', score: 30 }, { type: 'ma_cross_dead', label: '均线死叉', score: 25 }] },
  { code: '601318', name: '中国平安', price: 52.30, yclose: 52.28, chg: 0.04, score: 62, turnover: 0.6, volume_ratio: 0.88, signals: [] },
  { code: '002594', name: '比亚迪', price: 265.00, yclose: 251.90, chg: 5.20, score: 79, turnover: 2.2, volume_ratio: 1.66, signals: [{ type: 'momentum', label: '动量↑', score: 84 }, { type: 'ma_cross_gold', label: '均线金叉', score: 72 }] },
];

const INJECT = `
  (function(){
    const stocks = ${JSON.stringify(TEST_STOCKS)};
    window.appState.stocks = stocks;
    window.appState.stockMap = {};
    stocks.forEach(s => { window.appState.stockMap[s.code] = s; });
    if (!window.appState.activeCode) window.appState.activeCode = stocks[2].code;
    renderStockList();
    return true;
  })()
`;

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

  const errors = [];
  page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('CONSOLE: ' + m.text()); });

  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  // 等 init 第二阶段（非 fast 的 fetchQuotes + 策略评分）完成，避免它在我们注入后覆盖测试数据
  await page.waitForTimeout(5500);

  // 停掉自动刷新，避免真实行情覆盖注入的测试数据
  await page.evaluate(() => {
    if (window.appState && window.appState.refreshTimer) { clearInterval(window.appState.refreshTimer); window.appState.refreshTimer = null; }
    if (window._settings) window._settings.autorefresh = false;
  });

  // 注入测试数据（覆盖真实数据）
  const inject = async () => { await page.evaluate(INJECT); await page.waitForTimeout(300); };
  await inject();

  // ---------- 截图 1: 244px 默认布局（股票名可见性） ----------
  const listInfo = await page.evaluate(() => {
    const list = document.getElementById('stock-list');
    const rows = Array.from(list.querySelectorAll('.stock-row'));
    return {
      listWidth: list.offsetWidth,
      rows: rows.map(r => {
        const t = r.querySelector('.stock-name-text');
        return {
          name: t ? t.textContent : '(missing)',
          textW: t ? t.getBoundingClientRect().width : 0,
          visibleW: t ? t.clientWidth : 0,
          clipped: t ? (t.scrollWidth > t.clientWidth + 1) : null,
        };
      }),
    };
  });
  console.log('LAYOUT@244px listWidth=' + listInfo.listWidth);
  listInfo.rows.forEach(r => console.log(`  ${r.name}: text=${r.textW.toFixed(0)}px visible=${r.visibleW.toFixed(0)}px clipped=${r.clipped}`));

  const panel = await page.$('#left-panel');
  await panel.screenshot({ path: path.join(OUT, 'stock-panel-layout.png') });

  // ---------- 截图 2: hover 状态（工具区 + 环形评分绽放 + 3D 倾斜） ----------
  const row = await page.$('.stock-row[data-code="600519"]');
  await row.hover();
  await page.waitForTimeout(500);
  await panel.screenshot({ path: path.join(OUT, 'stock-panel-hover.png') });

  // ---------- 截图 3: 右键菜单 ----------
  const box = await row.boundingBox();
  await page.mouse.click(box.x + 120, box.y + 10, { button: 'right' });
  await page.waitForTimeout(400);
  const menuVisible = await page.evaluate(() => !!document.querySelector('.ctx-menu'));
  console.log('CTX_MENU visible:', menuVisible);
  await page.screenshot({ path: path.join(OUT, 'stock-panel-contextmenu.png') });
  await page.keyboard.press('Escape');
  await page.waitForTimeout(200);

  // ---------- 排序菜单 + 信号筛选功能自检 ----------
  await inject();
  await page.click('#sort-trigger');
  await page.waitForTimeout(300);
  await page.click('#sort-menu .sort-menu-item[data-key="chg"]');
  await page.waitForTimeout(400);
  const sortedFirst = await page.evaluate(() => document.querySelector('.stock-row .stock-name-text').textContent);
  console.log('SORT by chg desc → first row:', sortedFirst, '(expect 贵州茅台 +9.98)');
  const panel2 = await page.$('#left-panel');
  await panel2.screenshot({ path: path.join(OUT, 'stock-panel-sort.png') });

  // 信号筛选
  await inject();
  await page.evaluate(() => { window.appState.sortKey = 'score'; window.appState.sortDir = 'desc'; renderStockList(); });
  await page.click('.stock-row[data-code="300750"] .stag.breakout');
  await page.waitForTimeout(400);
  const filterCount = await page.evaluate(() => document.querySelectorAll('#stock-list .stock-row').length);
  console.log('SIGNAL FILTER breakout → rows:', filterCount, '(expect 2: 贵州茅台/宁德时代)');
  await page.click('.stock-row .stag.filter-active');
  await page.waitForTimeout(300);

  // ---------- 截图 4: 空状态 ----------
  await page.evaluate(() => { window.appState.stocks = []; window.appState.signalFilter = ''; renderStockList(); });
  await page.waitForTimeout(400);
  const emptyOk = await page.evaluate(() => !!document.querySelector('.stock-empty'));
  console.log('EMPTY STATE:', emptyOk);
  await panel2.screenshot({ path: path.join(OUT, 'stock-panel-empty.png') });

  // ---------- 窄面板（<260px）自检 ----------
  await inject();
  await page.evaluate(() => {
    const p = document.getElementById('left-panel');
    p.style.width = '240px';
    p.dispatchEvent(new Event('resize'));
  });
  await page.waitForTimeout(600);
  const narrow = await page.evaluate(() => {
    const p = document.getElementById('left-panel');
    const sparkHidden = getComputedStyle(document.querySelector('.stock-sparkline-wrap')).display === 'none';
    return { cls: p.className, sparkHidden, w: p.offsetWidth };
  });
  console.log('NARROW 240px:', JSON.stringify(narrow));
  const panel3 = await page.$('#left-panel');
  await panel3.screenshot({ path: path.join(OUT, 'stock-panel-narrow.png') });
  await page.evaluate(() => { document.getElementById('left-panel').style.width = '270px'; });

  // ---------- 亮色主题自检 ----------
  await page.evaluate(() => { if (typeof toggleTheme === 'function') toggleTheme(); document.body.classList.add('light-theme'); renderStockList(); });
  await page.waitForTimeout(500);
  const panel4 = await page.$('#left-panel');
  await panel4.screenshot({ path: path.join(OUT, 'stock-panel-light.png') });

  console.log('\nERRORS (' + errors.length + '):');
  errors.slice(0, 20).forEach(e => console.log('  ' + e));

  await browser.close();
})();
