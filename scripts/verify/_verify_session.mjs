// 验收：访问密钥 v2（服务端会话 + 会话级 Cookie + 关浏览器即失效）
// 前置：服务已配置强密钥（ACCESS_KEY ≥32 位），REQUIRE_ACCESS_KEY=1
// 运行：ACCESS_KEY=... node scripts/verify/_verify_session.mjs
import { chromium } from 'playwright';
import { loadKey } from './_login.mjs';

const BASE = process.env.AUTH_BASE || 'http://127.0.0.1:8900';
const KEY = process.env.ACCESS_KEY || loadKey();
if (!KEY) {
  console.error('需要 ACCESS_KEY');
  process.exit(2);
}

const browser = await chromium.launch({ channel: 'chrome' });
let pass = 0, fail = 0;
const check = (name, ok, detail = '') => {
  if (ok) { pass++; console.log(`  PASS ${name}${detail ? ' · ' + detail : ''}`); }
  else { fail++; console.log(`  FAIL ${name}${detail ? ' · ' + detail : ''}`); }
};

// 1) 全新浏览器会话：未登录必须被挡
const ctx1 = await browser.newContext();
const p1 = await ctx1.newPage();
await p1.goto(BASE + '/dashboard.html', { waitUntil: 'domcontentloaded' });
await p1.waitForTimeout(600);
check('新会话未登录被重定向', p1.url().includes('/login'), p1.url());

// 2) 错误私钥：就地报错
await p1.locator('#key').fill('071215');
await p1.locator('#submit').click();
await p1.waitForTimeout(900);
check('弱口令被拒绝', /密钥不正确/.test(await p1.locator('#msg').innerText()));
check('弱口令后仍停在登录页', p1.url().includes('/login'));

// 3) 正确私钥：进入
await p1.locator('#key').fill(KEY);
await p1.locator('#submit').click();
await p1.waitForURL(/dashboard\.html/, { timeout: 15000 }).catch(() => {});
await p1.waitForTimeout(1200);
check('私钥登录成功', p1.url().includes('dashboard.html'), p1.url());

// 4) Cookie 必须是会话级（无 expires / max-age）
const cookies = await ctx1.cookies(BASE);
const sid = cookies.find((c) => c.name === 'dapeng_sid');
check('会话 Cookie 存在且 HttpOnly', !!sid && sid.httpOnly === true);
check('Cookie 无过期时间（关浏览器即失效）', !!sid && sid.expires === -1,
  sid ? 'expires=' + sid.expires : 'no cookie');

// 5) 同一会话内刷新仍可用
await p1.reload({ waitUntil: 'domcontentloaded' });
await p1.waitForTimeout(800);
check('同一会话刷新保持登录', !p1.url().includes('/login'));

// 6) 关闭浏览器会话（销毁 context）→ 新 context 必须重新认证
await ctx1.close();
const ctx2 = await browser.newContext();
const p2 = await ctx2.newPage();
await p2.goto(BASE + '/dashboard.html', { waitUntil: 'domcontentloaded' });
await p2.waitForTimeout(600);
check('关闭会话后必须重新认证', p2.url().includes('/login'), p2.url());

// 7) 已登录时点退出 → 再访问仍需登录，且旧 sid 在服务端已删
const p3 = await ctx2.newPage();
await p3.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
await p3.locator('#key').fill(KEY);
await p3.locator('#submit').click();
await p3.waitForTimeout(1200);
const before = (await ctx2.cookies(BASE)).find((c) => c.name === 'dapeng_sid');
const out = await p3.evaluate(async () => (await fetch('/api/auth/logout', { method: 'POST' })).status);
check('退出接口 200', out === 200, 'status=' + out);
await p3.goto(BASE + '/dashboard.html', { waitUntil: 'domcontentloaded' });
await p3.waitForTimeout(500);
check('退出后需重新登录', p3.url().includes('/login'), p3.url());

// 8) 旧 sid 直接重放也必须 401（服务端撤销）
if (before) {
  const replay = await ctx2.request.get(BASE + '/api/decision/sentiment', {
    headers: { Cookie: `dapeng_sid=${before.value}` },
  });
  check('旧会话 ID 重放被拒', replay.status() === 401, 'status=' + replay.status());
}

console.log(`\n${pass} passed, ${fail} failed`);
await browser.close();
process.exit(fail ? 1 : 0);
