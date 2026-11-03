/**
 * DAPENG Alpha - 本地模拟持仓中心
 * 数据仅保存在当前浏览器，不连接券商或真实交易账户。
 */
(function () {
  'use strict';

  var INITIAL_CAPITAL = 2000;

  function loadArray(key) {
    try {
      var value = JSON.parse(localStorage.getItem(key) || '[]');
      return Array.isArray(value) ? value : [];
    } catch (e) { return []; }
  }

  window.portfolioState = {
    holdings: loadArray('dapeng_portfolio'),
    trades: loadArray('dapeng_trades'),
    initialCapital: Number(localStorage.getItem('dapeng_initial_capital')) || INITIAL_CAPITAL
  };

  function notify(title, message) {
    if (typeof showToast === 'function') showToast(title, message);
  }

  function finite(value, fallback) {
    var n = Number(value);
    return Number.isFinite(n) ? n : (fallback || 0);
  }

  function saveState() {
    try {
      localStorage.setItem('dapeng_portfolio', JSON.stringify(window.portfolioState.holdings));
      localStorage.setItem('dapeng_trades', JSON.stringify(window.portfolioState.trades.slice(-500)));
      localStorage.setItem('dapeng_initial_capital', String(window.portfolioState.initialCapital));
      return true;
    } catch (e) {
      notify('保存失败', '浏览器本地存储空间不足或不可用');
      return false;
    }
  }

  function quoteFor(code) {
    return window.appState && window.appState.stockMap ? window.appState.stockMap[code] : null;
  }

  function calculateCash() {
    return window.portfolioState.trades.reduce(function (cash, trade) {
      var amount = finite(trade.price) * finite(trade.qty);
      return cash + (trade.type === 'sell' ? amount : -amount);
    }, window.portfolioState.initialCapital);
  }

  function holdingFor(code) {
    return window.portfolioState.holdings.find(function (h) { return h.code === code; });
  }

  function recordTrade(type, code, name, price, qty, stopLoss) {
    price = finite(price);
    qty = Math.floor(finite(qty));
    stopLoss = finite(stopLoss);
    if (!code || price <= 0) return notify('交易未执行', '当前股票缺少有效行情价格');
    if (qty <= 0) return notify('交易未执行', '数量必须是正整数');
    if (type === 'buy' && qty % 100 !== 0) return notify('交易未执行', 'A股模拟买入数量需为 100 股的整数倍');

    var existing = holdingFor(code);
    if (type === 'sell' && (!existing || qty > finite(existing.qty))) {
      return notify('交易未执行', '卖出数量不能超过当前持仓');
    }
    if (type === 'buy' && price * qty > calculateCash()) {
      return notify('交易未执行', '可用模拟资金不足');
    }

    var trade = { id: Date.now(), type: type, code: String(code), name: String(name || code), price: price, qty: qty, stopLoss: stopLoss, time: new Date().toISOString() };
    window.portfolioState.trades.push(trade);

    if (type === 'buy') {
      if (existing) {
        var newQty = finite(existing.qty) + qty;
        existing.avgPrice = (finite(existing.avgPrice) * finite(existing.qty) + price * qty) / newQty;
        existing.qty = newQty;
        if (stopLoss > 0) existing.stopLoss = stopLoss;
      } else {
        window.portfolioState.holdings.push({ code: String(code), name: String(name || code), qty: qty, avgPrice: price, stopLoss: stopLoss });
      }
    } else {
      existing.qty -= qty;
      if (existing.qty <= 0) window.portfolioState.holdings = window.portfolioState.holdings.filter(function (h) { return h.code !== code; });
    }

    saveState();
    renderPortfolio();
    notify('模拟执行成功', (type === 'buy' ? '买入 ' : '卖出 ') + (name || code) + ' ' + qty + ' 股');
  }

  function portfolioTrade(type, code) {
    code = code || (window.appState && window.appState.activeCode);
    var quote = quoteFor(code) || {};
    var holding = holdingFor(code);
    var name = quote.name || (holding && holding.name) || (window.appState && window.appState.activeName) || code;
    var price = finite(quote.price, holding ? holding.avgPrice : 0);
    if (!code || price <= 0) return notify('无法交易', '请先选择一只有有效行情的股票');

    var maxHint = type === 'sell' && holding ? String(holding.qty) : '100';
    var qtyRaw = window.prompt((type === 'buy' ? '模拟买入' : '模拟卖出') + ' ' + name + ' (' + code + ')\n当前价：' + price.toFixed(2) + '\n请输入数量：', maxHint);
    if (qtyRaw == null) return;
    var stopLoss = 0;
    if (type === 'buy') {
      var stopRaw = window.prompt('设置参考止损位（可留空）：', (price * 0.95).toFixed(2));
      if (stopRaw == null) return;
      stopLoss = stopRaw === '' ? 0 : finite(stopRaw);
      if (stopLoss < 0 || stopLoss >= price) return notify('交易未执行', '止损位应大于 0 且低于买入价');
    }
    recordTrade(type, code, name, price, qtyRaw, stopLoss);
  }

  function renderPortfolio() {
    var container = document.getElementById('portfolio-list');
    if (!container) return;
    var holdings = window.portfolioState.holdings;
    var totalValue = 0, totalCost = 0, dailyPnl = 0;
    var missingQuotes = 0, stopWarnings = 0;

    var html = holdings.map(function (holding) {
      var quote = quoteFor(holding.code);
      if (!quote) missingQuotes++;
      var current = finite(quote && quote.price, finite(holding.avgPrice));
      var change = finite(quote && quote.chg);
      var denominator = 1 + change / 100;
      var previousClose = denominator > 0 ? current / denominator : current;
      var value = current * finite(holding.qty);
      var cost = finite(holding.avgPrice) * finite(holding.qty);
      var profit = value - cost;
      var profitPct = cost > 0 ? profit / cost * 100 : 0;
      var dayProfit = (current - previousClose) * finite(holding.qty);
      var breached = finite(holding.stopLoss) > 0 && current <= finite(holding.stopLoss);
      if (breached) stopWarnings++;
      totalValue += value;
      totalCost += cost;
      dailyPnl += dayProfit;

      var encodedCode = encodeURIComponent(holding.code).replace(/'/g, '%27');
      return '<div class="portfolio-item' + (breached ? ' risk' : '') + '">' +
        '<div class="p-item-info"><strong>' + escapeHtml(holding.name) + '</strong><span>' + escapeHtml(holding.code) + ' · ' + Math.round(finite(holding.qty)) + '股</span></div>' +
        '<div><span class="p-label">现价 / 成本</span><strong>' + current.toFixed(2) + ' / ' + finite(holding.avgPrice).toFixed(2) + '</strong></div>' +
        '<div><span class="p-label">市值</span><strong>' + value.toFixed(0) + '</strong></div>' +
        '<div><span class="p-label">累计盈亏</span><strong class="' + (profit >= 0 ? 'up' : 'dn') + '">' + (profit >= 0 ? '+' : '') + profit.toFixed(0) + ' (' + profitPct.toFixed(2) + '%)</strong></div>' +
        '<div><span class="p-label">止损</span><strong class="' + (breached ? 'dn' : '') + '">' + (finite(holding.stopLoss) > 0 ? finite(holding.stopLoss).toFixed(2) : '--') + '</strong></div>' +
        '<button class="osl-btn" onclick="event.stopPropagation();portfolioTrade(\'sell\',decodeURIComponent(\'' + encodedCode + '\'))">卖出</button></div>';
    }).join('');

    container.innerHTML = html || '<div class="osl-state"><strong>暂无模拟持仓</strong><span>选择股票后点击“买入当前股”开始模拟。</span></div>';
    var cash = calculateCash();
    updateText('p-assets', totalValue + cash, 'currency');
    updateText('p-total', totalValue, 'currency');
    updateText('p-cash', cash, 'currency');
    updateText('p-daily', dailyPnl, 'signed');
    updateText('p-profit', totalValue - totalCost, 'signed');

    var concentration = totalValue > 0 && holdings.length ? Math.max.apply(null, holdings.map(function (h) {
      var q = quoteFor(h.code);
      return finite(q && q.price, finite(h.avgPrice)) * finite(h.qty) / totalValue * 100;
    })) : 0;
    var messages = [];
    if (concentration > 50) messages.push('单股最高权重 ' + concentration.toFixed(1) + '%，集中度偏高');
    if (holdings.some(function (h) { return finite(h.stopLoss) <= 0; })) messages.push('部分持仓未设置止损位');
    if (stopWarnings) messages.push(stopWarnings + ' 只股票已触及参考止损位');
    if (missingQuotes) messages.push(missingQuotes + ' 只股票暂时使用持仓成本估值');
    var riskEl = document.getElementById('portfolio-risk');
    if (riskEl) riskEl.innerHTML = messages.length ? messages.map(function (m) { return '<span>' + escapeHtml(m) + '</span>'; }).join('') : '<span class="safe">组合暂未发现高优先级风险</span>';
    renderTrades();
  }

  function updateText(id, value, mode) {
    var el = document.getElementById(id);
    if (!el) return;
    value = finite(value);
    el.textContent = mode === 'currency' ? '¥' + value.toLocaleString('zh-CN', {maximumFractionDigits: 0}) : (value >= 0 ? '+' : '') + '¥' + value.toLocaleString('zh-CN', {maximumFractionDigits: 0});
    el.className = mode === 'signed' ? (value >= 0 ? 'up' : 'dn') : '';
  }

  function renderTrades() {
    var el = document.getElementById('portfolio-trades');
    if (!el) return;
    var rows = window.portfolioState.trades.slice(-10).reverse();
    el.innerHTML = rows.length ? rows.map(function (trade) {
      var date = new Date(trade.time);
      return '<div class="trade-row"><span class="trade-side ' + escapeHtml(trade.type) + '">' + (trade.type === 'buy' ? '买' : '卖') + '</span>' +
        '<div><strong>' + escapeHtml(trade.name || trade.code) + '</strong><small>' + escapeHtml(trade.code) + ' · ' + date.toLocaleString('zh-CN', {hour12:false}) + '</small></div>' +
        '<div><strong>' + Math.round(finite(trade.qty)) + '股</strong><small>@ ' + finite(trade.price).toFixed(2) + '</small></div></div>';
    }).join('') : '<div class="osl-state compact"><span>暂无交易记录</span></div>';
  }

  function exportPortfolioCsv() {
    if (!window.portfolioState.trades.length) return notify('无法导出', '暂无模拟交易记录');
    var rows = [['时间','方向','代码','名称','价格','数量','止损']].concat(window.portfolioState.trades.map(function (t) {
      return [t.time, t.type === 'buy' ? '买入' : '卖出', t.code, t.name, finite(t.price), Math.round(finite(t.qty)), finite(t.stopLoss) || ''];
    }));
    var csv = '\ufeff' + rows.map(function (row) { return row.map(function (cell) { return '"' + String(cell == null ? '' : cell).replace(/"/g, '""') + '"'; }).join(','); }).join('\r\n');
    var url = URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8'}));
    var a = document.createElement('a'); a.href = url; a.download = 'dapeng-portfolio-' + new Date().toISOString().slice(0,10) + '.csv'; a.click();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function clearPortfolio() {
    if (!window.portfolioState.holdings.length && !window.portfolioState.trades.length) return;
    if (!window.confirm('确定清空全部模拟持仓和交易记录吗？此操作不可撤销。')) return;
    window.portfolioState.holdings = [];
    window.portfolioState.trades = [];
    saveState(); renderPortfolio(); notify('模拟持仓', '数据已清空');
  }

  window.recordTrade = recordTrade;
  window.portfolioTrade = portfolioTrade;
  window.showTradeDialog = portfolioTrade;
  window.renderPortfolio = renderPortfolio;
  window.exportPortfolioCsv = exportPortfolioCsv;
  window.clearPortfolio = clearPortfolio;

  setInterval(function () {
    if (window.appState && window.appState.activeTab === 'portfolio' && !document.hidden) renderPortfolio();
  }, 10000);
})();
