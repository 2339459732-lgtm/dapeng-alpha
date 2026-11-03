import { chromium } from 'playwright';
const B = 'http://127.0.0.1:8900';
const b = await chromium.launch({ channel: 'chrome' });

// 1) 文档页「问题与解决」表格
const p1 = await b.newPage({ viewport: { width: 1500, height: 950 } });
await p1.goto(B + '/project-docs.html', { waitUntil: 'domcontentloaded' });
await p1.waitForTimeout(3000);
await p1.evaluate(() => document.getElementById('sec-issues')?.scrollIntoView({ block: 'start' }));
await p1.waitForTimeout(800);
await p1.screenshot({ path: 'shots/docs-issues.png' });
console.log('docs-issues.png ok');

// 2) 情绪页聚焦条 + 热力格
const p2 = await b.newPage({ viewport: { width: 1500, height: 950 } });
await p2.goto(B + '/sentiment.html?sector=pharma', { waitUntil: 'domcontentloaded' });
await p2.waitForTimeout(14000);
await p2.evaluate(() => document.getElementById('heatmap-grid')?.scrollIntoView({ block: 'center' }));
await p2.waitForTimeout(800);
await p2.screenshot({ path: 'shots/sentiment-focus.png' });
console.log('sentiment-focus.png ok');
await b.close();
