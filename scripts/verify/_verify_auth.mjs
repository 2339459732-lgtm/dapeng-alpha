// 验收：访问密钥登录（access-key.v1）
// 前置：ACCESS_KEY 已设置，服务在 BASE 上运行
// 运行：ACCESS_KEY=... node scripts/verify/_verify_auth.mjs
import { chromium } from 'playwright';

const BASE = process.env.AUTH_BASE || 'http://127.0.0.1:8911';
const KEY = process.env.ACCESS_KEY || '';
if (!KEY) {
  console.error('需要 ACCESS_KEY 环境变量');
  process.exit(2);
}

const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
page.on('console', (m) => {
  // 401 是鉴权的正常拒绝（退出登录后页面仍在轮询），不是脚本错误
  if (m.type() === 'error' && !/status of 401|401 \(Unauthorized\)/.test(m.text())) {
    errors.push('console: ' + m.text().slice(0, 200));
  }
});

let pass = 0, fail = 0;
const check = (name, ok, detail = '') => {
  if (ok) { pass++; console.log(`  PASS ${name}${detail ? ' · ' + detail : ''}`); }
  else { fail++; console.log(`  FAIL ${name}${detail ? ' · ' + detail : ''}`); }
};

// 1) 未登录访问看板 → 跳登录页
await page.goto(BASE + '/dashboard.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(800);
check('未登录被重定向到登录页', page.url().includes('/login'), page.url());

// 2) 错误密钥 → 就地报错，不跳转
await page.locator('#key').fill('wrong-key');
await page.locator('#submit').click();
await page.waitForTimeout(1200);
const errText = await page.locator('#msg').innerText();
check('错误密钥给出提示', /密钥不正确/.test(errText), errText.trim());
check('错误后仍停留在登录页', page.url().includes('/login'));

// 3) 正确密钥 → 进入看板
await page.locator('#key').fill(KEY);
await page.locator('#submit').click();
await page.waitForURL(/dashboard\.html/, { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(1500);
check('正确密钥进入看板', page.url().includes('dashboard.html'), page.url());

// 4) 刷新仍保持登录（Cookie 生效）
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1200);
check('刷新后会话保持', !page.url().includes('/login'), page.url());

// 5) 退出登录后回到登录页
const out = await page.evaluate(async () => {
  const r = await fetch('/api/auth/logout', { method: 'POST' });
  return r.status;
});
check('退出接口返回 200', out === 200, 'status=' + out);
await page.goto(BASE + '/dashboard.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(800);
check('退出后需重新登录', page.url().includes('/login'), page.url());

check('无 JS 运行时错误', errors.length === 0, errors.join(' | ').slice(0, 300));

console.log(`\n${pass} passed, ${fail} failed`);
await browser.close();
process.exit(fail ? 1 : 0);
