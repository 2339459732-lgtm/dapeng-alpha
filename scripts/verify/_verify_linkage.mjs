// 验证面板信息互联：情绪页聚焦、热力格跳转、仪表盘上下文外带、浮层出口条
import { chromium } from 'playwright';
const BASE = 'http://127.0.0.1:8900';
const browser = await chromium.launch({ channel: 'chrome' });
const out = [];
const errs = [];

// ── 1. 情绪页 ?sector=pharma ──
{
  const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
  page.on('pageerror', e => errs.push('sentiment pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('sentiment console: ' + m.text().slice(0, 150)); });

  await page.goto(BASE + '/sentiment.html?sector=pharma', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(14000);

  const info = await page.evaluate(() => {
    const bar = document.getElementById('hm-focus');
    const cells = Array.from(document.querySelectorAll('#heatmap-grid .hm-cell'));
    const linked = cells.filter(c => c.classList.contains('is-linked'));
    const focus = cells.filter(c => c.classList.contains('is-focus'));
    return {
      barText: bar ? bar.textContent.replace(/\s+/g, ' ').trim() : null,
      cells: cells.length,
      linked: linked.length,
      focus: focus.length,
      focusNames: focus.map(c => (c.querySelector('.hm-name') || {}).textContent).slice(0, 6),
      sampleLinked: linked.slice(0, 3).map(c => (c.querySelector('.hm-name') || {}).textContent + '→' + c.getAttribute('data-sector')),
      cursorNotLinked: (() => {
        const c = cells.find(x => !x.classList.contains('is-linked'));
        return c ? getComputedStyle(c).cursor : null;
      })(),
    };
  });
  out.push('【情绪页 ?sector=pharma】');
  out.push('  聚焦条: ' + info.barText);
  out.push(`  格子 ${info.cells} 个 / 可跳转 ${info.linked} / 高亮 ${info.focus}  ${JSON.stringify(info.focusNames)}`);
  out.push('  映射样例: ' + JSON.stringify(info.sampleLinked));
  out.push('  不可跳转格子 cursor=' + info.cursorNotLinked);

  // 点击一个可跳转格子 → 应落到 dashboard?sector=
  if (info.linked > 0) {
    const target = await page.evaluate(() => {
      const c = document.querySelector('#heatmap-grid .hm-cell.is-linked');
      return c ? c.getAttribute('data-sector') : null;
    });
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 20000 }).catch(() => {}),
      page.evaluate(() => document.querySelector('#heatmap-grid .hm-cell.is-linked').click()),
    ]);
    await page.waitForTimeout(6000);
    const landed = await page.evaluate(() => ({
      url: location.pathname + location.search,
      title: (document.getElementById('sector-title-name') || {}).textContent,
      rows: document.querySelectorAll('.stock-row').length,
    }));
    out.push(`  点击格子(sector=${target}) → ${landed.url}  标题=${landed.title}  行数=${landed.rows}`);

    // 仪表盘上下文外带
    const links = await page.evaluate(() => {
      const g = id => { const e = document.getElementById(id); return e ? e.getAttribute('href') : null; };
      return { sentiment: g('hdr-link-sentiment'), kolSentiment: g('kol-sentiment'), kolQuant: g('kol-quant'), kolDocs: g('kol-docs') };
    });
    out.push('  仪表盘外链: ' + JSON.stringify(links));
  }
  await page.close();
}

// ── 2. 浮层出口条 ──
{
  const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
  page.on('pageerror', e => errs.push('dash pageerror: ' + e.message));
  await page.goto(BASE + '/dashboard.html', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(7000);
  const strip = await page.evaluate(() => {
    const el = document.getElementById('kline-overlay-links');
    if (!el) return 'MISSING';
    const cs = getComputedStyle(el);
    return { links: el.querySelectorAll('a').length, visible: cs.display !== 'none', text: el.textContent.replace(/\s+/g, ' ').trim() };
  });
  out.push('\n【K 线浮层「相关信息」条】');
  out.push('  ' + JSON.stringify(strip));
  await page.close();
}

// ── 3. 回测页上下文 ──
{
  const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
  page.on('pageerror', e => errs.push('quant pageerror: ' + e.message));
  await page.goto(BASE + '/quant.html?code=sh600276&sector=pharma', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);
  const chip = await page.evaluate(() => {
    const el = document.getElementById('ctx-chip');
    if (!el) return 'MISSING';
    return { hidden: el.hasAttribute('hidden'), text: el.textContent.replace(/\s+/g, ' ').trim() };
  });
  out.push('\n【回测页上下文条】');
  out.push('  ' + JSON.stringify(chip));
  await page.close();
}

console.log(out.join('\n'));
console.log('\n错误=' + (errs.length ? JSON.stringify(errs.slice(0, 6), null, 2) : '无'));
await browser.close();
