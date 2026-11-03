// 浏览器验收的登录助手：返回一个已带会话 Cookie 的 context。
// 未配置 ACCESS_KEY 的实例直接返回匿名 context，脚本行为不变。
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

export function loadKey() {
  if (process.env.ACCESS_KEY) return process.env.ACCESS_KEY.trim();
  try {
    const text = readFileSync(resolve(process.cwd(), '.env'), 'utf8');
    for (const line of text.split(/\r?\n/)) {
      const m = /^\s*(ACCESS_KEY|DAPENG_ACCESS_KEY)\s*=\s*(.+)$/.exec(line);
      if (m) return m[2].trim().replace(/^["']|["']$/g, '');
    }
  } catch { /* 没有 .env 就当未启用 */ }
  return '';
}

export async function loginContext(browser, base, options = {}) {
  const ctx = await browser.newContext({
    viewport: options.viewport || { width: 1600, height: 1000 },
    ...(options.contextOptions || {}),
  });
  const key = options.key || loadKey();
  if (!key) return ctx;
  const res = await ctx.request.post(base.replace(/\/$/, '') + '/api/auth/login', {
    data: { key },
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok()) {
    throw new Error(`登录失败：HTTP ${res.status()}（检查 ACCESS_KEY 是否正确）`);
  }
  return ctx;
}
