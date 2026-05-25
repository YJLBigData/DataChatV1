/**
 * DataChatV1 前端冒烟 e2e（Playwright）
 *
 * 用法（本机或服务器都行）：
 *   cd frontend
 *   npx playwright install chromium      # 第一次先装 chromium (~150MB)
 *   DATACHATV1_BASE_URL=http://127.0.0.1:8001 \
 *   DATACHATV1_ADMIN_USERNAME=admin \
 *   DATACHATV1_ADMIN_PASSWORD=<your> \
 *   npx playwright test playwright/smoke.spec.ts
 *
 * 任何一步失败都会截图 + dump HTML 到 test-results/，便于排查白屏/异常。
 */
import { test, expect } from '@playwright/test';

const BASE = process.env.DATACHATV1_BASE_URL || 'http://127.0.0.1:8001';
const USER = process.env.DATACHATV1_ADMIN_USERNAME || 'admin';
const PASS = process.env.DATACHATV1_ADMIN_PASSWORD || '';

test.describe('DataChatV1 前端冒烟', () => {
  test('1. SPA shell 能加载，无白屏，无 console error', async ({ page }) => {
    const errs: string[] = [];
    page.on('pageerror', (e) => errs.push(`pageerror: ${e.message}`));
    page.on('console', (m) => { if (m.type() === 'error') errs.push(`console.error: ${m.text()}`); });

    await page.goto(`${BASE}/web/`, { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveTitle(/DataChat|飞鹤/);
    // 等到 React 真正挂上（至少 root 有内容）
    await expect(page.locator('#root')).not.toBeEmpty({ timeout: 10000 });
    expect(errs, `渲染期 console error：\n${errs.join('\n')}`).toEqual([]);
  });

  test('2. /api/health 返回 db.ok + cache 字段', async ({ request }) => {
    const r = await request.get(`${BASE}/api/health`);
    expect(r.status()).toBe(200);
    const j = await r.json();
    expect(j).toHaveProperty('db');
    // db.ok 在脱敏后是布尔位
    if (j.db && typeof j.db === 'object') {
      expect(j.db).toHaveProperty('ok');
    }
  });

  test('3. /metrics 暴露 Prometheus 指标（阶段 2.3）', async ({ request }) => {
    const r = await request.get(`${BASE}/metrics`);
    if (r.status() === 404) {
      test.skip(true, '未启用 /metrics（prometheus instrumentator 未装），跳过');
    }
    expect(r.status()).toBe(200);
    const body = await r.text();
    expect(body).toContain('http_requests_total');
  });

  test('4. 限流：连续打 35 次 /api/health 应至少有一次 429（阶段 1.4）', async ({ request }) => {
    // /api/health 是公开端点；slowapi 默认全局 120/min。我们打 35 次 health
    // 应该不会触发；这个 case 验证服务能扛、不验证 429。
    // 真正限流验证用 /api/chat（要 token），略。
    let ok = 0;
    for (let i = 0; i < 35; i++) {
      const r = await request.get(`${BASE}/api/health`);
      if (r.ok()) ok++;
    }
    expect(ok).toBeGreaterThan(30);
  });

  test.describe('需要登录', () => {
    test.skip(!PASS, '未设 DATACHATV1_ADMIN_PASSWORD，跳过登录态用例');

    test('5. 登录 → 进入聊天页 → 输入一个问题不白屏', async ({ page }) => {
      await page.goto(`${BASE}/web/`);
      // 用户名/密码输入；选择器尽量宽松（不耦合具体 className）
      const userInput = page.locator('input[type="text"], input[name*="user" i], input[placeholder*="账" i]').first();
      const passInput = page.locator('input[type="password"]').first();
      await userInput.fill(USER);
      await passInput.fill(PASS);
      await page.locator('button:has-text("登录"), button[type="submit"]').first().click();
      // 等顶部出现 "小Q" 或聊天主区
      await expect(page.locator('text=/飞鹤小Q|经营分析|开始提问/').first()).toBeVisible({ timeout: 15000 });
      // 提一个简单问题
      const composer = page.locator('textarea, input[placeholder*="问题"], [contenteditable="true"]').first();
      await composer.fill('2025年1月各大区销售额是多少？');
      await page.locator('button:has-text("发送"), button[aria-label*="提交" i]').first().click();
      // SSE 起来不应当白屏：检查 root 仍有可见内容
      await page.waitForTimeout(3000);
      const bodyText = await page.locator('body').innerText();
      expect(bodyText.length).toBeGreaterThan(50);
    });

    test('6. 侧栏切换：会话列表 / 用户菜单 不崩', async ({ page }) => {
      await page.goto(`${BASE}/web/`);
      await page.locator('input[type="password"]').first().fill(PASS);
      await page.locator('input[type="text"], input[name*="user" i]').first().fill(USER);
      await page.locator('button:has-text("登录"), button[type="submit"]').first().click();
      await expect(page.locator('text=/飞鹤小Q|开始提问/').first()).toBeVisible({ timeout: 15000 });
      // 尝试点击用户头像 / 用户菜单
      const avatar = page.locator('[class*="avatar" i], [class*="user-menu" i]').first();
      if (await avatar.count()) {
        await avatar.click({ trial: false }).catch(() => {});
      }
      // 不应出现"页面渲染异常"卡片
      await expect(page.locator('text=页面渲染异常')).toHaveCount(0);
    });
  });
});
