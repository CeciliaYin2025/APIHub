const { test, expect } = require('@playwright/test');

const ADMIN = { user: 'admin', pass: 'changeme' };

async function login(page, user = ADMIN.user, pass = ADMIN.pass) {
  await page.goto('/admin');
  await page.fill('#loginUser', user);
  await page.fill('#loginPass', pass);
  await page.click('#loginForm button[type="submit"]');
  await expect(page.locator('#appLayout')).toBeVisible();
}

// Seeds one audited query execution. There is no Oracle in the harness, so
// this fails — and that is the point: failed calls are audited too, so the
// row lands with action_type=query and its params intact.
async function seedQueryCall(page, params) {
  const token = await page.evaluate(() => localStorage.getItem('hub_token'));
  const res = await page.request.post('/api/v1/default/queries/execute', {
    headers: { Authorization: `Bearer ${token}` },
    data: { query_name: 'employees', params },
    failOnStatusCode: false,
  });
  expect(res.status()).toBeGreaterThanOrEqual(400);
}

test('usage report aggregates calls and drills into request params', async ({ page, context }) => {
  // navigator.clipboard needs explicit permission in headless Chromium; the
  // same grant the raw-json-copy specs use.
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await login(page);
  await seedQueryCall(page, { dept: 'E2E-IT' });
  await seedQueryCall(page, { dept: 'E2E-HR' });

  await page.click('.sidebar-nav a[data-page="usage-report"]');
  await expect(page.locator('#urFrom')).toBeVisible();

  // Default filters already cover query/procedure/write over the last 30 days.
  // The audit row is written as a background task, so retry the Apply until
  // the aggregation catches up.
  await expect(async () => {
    await page.click('#urApply');
    await expect(page.locator('.data-table tbody tr', { hasText: 'employees' }).first())
      .toBeVisible({ timeout: 2000 });
  }).toPass({ timeout: 20000 });

  const row = page.locator('.data-table tbody tr', { hasText: 'employees' }).first();
  await expect(row).toContainText('admin');
  // Grouped by user + DW + API, so the first three cells are the dimensions
  // and the metrics follow: calls, success, failed.
  await expect(row.locator('td').nth(3)).toHaveText('2');
  await expect(row.locator('td').nth(4)).toHaveText('0');
  await expect(row.locator('td').nth(5)).toHaveText('2');

  await row.locator('.ur-drill').click();
  await expect(page.locator('#urBack')).toBeVisible();
  await expect(page.locator('#urDetailResults .data-table tbody tr').first()).toContainText('employees');

  // Expand the newest request and confirm the recorded params are visible.
  await page.locator('.ur-expand').first().click();
  const pre = page.locator('#urDetailResults pre').first();
  await expect(pre).toBeVisible();
  await expect(pre).toContainText('E2E-HR');
  await expect(pre).toContainText('fetch_mode');

  await page.locator('button', { hasText: 'Copy JSON' }).first().click();
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(JSON.parse(clip).params.dept).toBe('E2E-HR');
});

test('grouping by day collapses the API dimension', async ({ page }) => {
  await login(page);
  await seedQueryCall(page, { dept: 'E2E-DAY' });

  await page.click('.sidebar-nav a[data-page="usage-report"]');
  await expect(page.locator('#urFrom')).toBeVisible();
  await page.uncheck('.ur-group[value="api"]');
  await page.uncheck('.ur-group[value="db"]');
  await page.check('.ur-group[value="day"]');

  await expect(async () => {
    await page.click('#urApply');
    await expect(page.locator('.data-table thead th', { hasText: 'Day' })).toBeVisible({ timeout: 2000 });
    await expect(page.locator('.data-table tbody tr', { hasText: 'admin' }).first()).toBeVisible({ timeout: 2000 });
  }).toPass({ timeout: 20000 });

  await expect(page.locator('.data-table thead th', { hasText: 'API' })).toHaveCount(0);
});

test('non-superuser never sees the report link', async ({ page }) => {
  await login(page);
  const token = await page.evaluate(() => localStorage.getItem('hub_token'));
  await page.request.post('/api/v1/auth/users', {
    headers: { Authorization: `Bearer ${token}` },
    data: { username: 'e2e_reader', password: 'reader-pass-1', full_name: 'Reader', is_superuser: false },
    failOnStatusCode: false,
  });

  await page.evaluate(() => localStorage.removeItem('hub_token'));
  await login(page, 'e2e_reader', 'reader-pass-1');
  await expect(page.locator('#navUsageReport')).toBeHidden();
});
