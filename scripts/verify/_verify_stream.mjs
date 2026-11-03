import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8900/dashboard.html';
const STATS = 'http://127.0.0.1:8900/api/stream/stats';
const out = { market: {}, auto: {}, degrade: {}, consoleErrors: {} };
const browser = await chromium.launch();

// ============ Test A: 大盘纵览 实时推送（推送驱动的区块数值变化） ============
{
  const errors = [];
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

  await page.goto(BASE, { waitUntil: 'load', timeout: 30000 });
  await page.evaluate(() => { if (typeof window.switchWorkspace === 'function') window.switchWorkspace('market'); });
  await page.waitForSelector('#market-stream-indicator', { timeout: 20000 }).catch(() => {});
  await page.waitForFunction(() => {
    const el = document.getElementById('market-stream-indicator');
    return el && /实时推送已连接/.test(el.innerText);
  }, { timeout: 20000 }).catch(() => {});

  const ind1 = await page.$eval('#market-stream-indicator', (el) => el.innerText).catch(() => '<<missing>>');
  const upd1 = await page.$eval('#market-updated', (el) => el.innerText).catch(() => '<<missing>>');

  let changed = false;
  try {
    await page.waitForFunction((prev) => {
      const el = document.getElementById('market-updated');
      return el && el.innerText !== prev;
    }, upd1, { timeout: 100000 });
    changed = true;
  } catch (e) { changed = false; }
  const upd2 = await page.$eval('#market-updated', (el) => el.innerText).catch(() => '<<missing>>');
  out.market = { indicatorConnected: ind1, updatedBefore: upd1, pushChangedValue: changed, updatedAfter: upd2 };
  out.consoleErrors.market = errors;
  await page.screenshot({ path: 'shots/stream_market_live.png' });
  await page.close();
}

// ============ Test A2: 自动化交易中心 实时推送（推送驱动刷新） ============
{
  const errors = [];
  let sigReqCount = 0;
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('request', (r) => { if (r.url().includes('/api/automation/signals')) sigReqCount += 1; });

  await page.goto(BASE, { waitUntil: 'load', timeout: 30000 });
  await page.evaluate(() => {
    if (typeof window.toggleAutomationPanel === 'function') window.toggleAutomationPanel();
    if (typeof window.switchAutomationTab === 'function') window.switchAutomationTab('signals');
  });
  await page.waitForSelector('#auto-signals-tbody tr', { timeout: 20000 }).catch(() => {});
  const rowsBefore = await page.$$eval('#auto-signals-tbody tr', (r) => r.length).catch(() => -1);

  // 服务端确认：确实有一个订阅 signals 的活跃连接（来自本页面 onEvent 关联的 EventSource）
  let hasSignalsSub = false;
  try {
    const s = await (await fetch(STATS)).json();
    hasSignalsSub = (s.data.subscribers || []).some((x) => x.topics.includes('signals'));
  } catch (e) { hasSignalsSub = false; }

  const reqAtStart = sigReqCount;
  await page.waitForTimeout(25000);   // 观察 25s 内是否出现推送驱动的自动刷新
  const rowsAfter = await page.$$eval('#auto-signals-tbody tr', (r) => r.length).catch(() => -1);

  out.auto = {
    hasSignalsSubscriber: hasSignalsSub,
    pushDrivenReloads: sigReqCount - reqAtStart,
    rowsBefore, rowsAfter
  };
  out.consoleErrors.auto = errors;
  await page.screenshot({ path: 'shots/stream_auto_signals.png' });
  await page.close();
}

// ============ Test B: 降级为轮询（阻断 SSE 端点） ============
{
  const errors = [];
  let marketPollCount = 0;
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('request', (req) => { if (req.url().includes('/api/market-dashboard')) marketPollCount += 1; });

  await page.route('**/api/stream/**', (route) => route.abort());
  await page.goto(BASE, { waitUntil: 'load', timeout: 30000 });
  await page.evaluate(() => { if (typeof window.switchWorkspace === 'function') window.switchWorkspace('market'); });
  await page.waitForSelector('#market-stream-indicator', { timeout: 20000 }).catch(() => {});
  await page.waitForFunction(() => {
    const el = document.getElementById('market-stream-indicator');
    return el && /降级为轮询/.test(el.innerText);
  }, { timeout: 40000 }).catch(() => {});

  const ind = await page.$eval('#market-stream-indicator', (el) => el.innerText).catch(() => '<<missing>>');
  await page.waitForTimeout(2000);
  out.degrade = { indicator: ind, degraded: /降级为轮询/.test(ind), marketRESTPollRequests: marketPollCount };
  out.consoleErrors.degrade = errors;
  await page.screenshot({ path: 'shots/stream_degrade_polling.png' });
  await page.close();
}

console.log('=== STREAM FRONTEND VERIFICATION ===');
console.log(JSON.stringify(out, null, 2));
await browser.close();
