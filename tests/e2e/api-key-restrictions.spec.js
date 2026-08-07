const { test, expect } = require('@playwright/test');

const ADMIN = { user: 'admin', pass: 'changeme' };

async function login(page) {
  await page.goto('/admin');
  await page.fill('#loginUser', ADMIN.user);
  await page.fill('#loginPass', ADMIN.pass);
  await page.click('#loginForm button[type="submit"]');
  await expect(page.locator('#appLayout')).toBeVisible();
}

// A key needs a service account to hang off, and the seeded DB has none.
async function seedServiceAccount(page, username) {
  const token = await page.evaluate(() => localStorage.getItem('hub_token'));
  await page.request.post('/api/v1/auth/users', {
    headers: { Authorization: `Bearer ${token}` },
    data: { username, password: 'unused-service-account', is_service_account: true },
    failOnStatusCode: false,
  });
  return token;
}

test('an API key shows its restrictions and can have them changed', async ({ page }) => {
  await login(page);
  const token = await seedServiceAccount(page, 'svc_e2e_restrict');

  const created = await page.request.post('/api/v1/auth/api-keys', {
    headers: { Authorization: `Bearer ${token}` },
    data: { service_account_username: 'svc_e2e_restrict', name: 'e2e-restrict' },
  });
  expect(created.ok()).toBeTruthy();
  const key = await created.json();

  // Created without restrictions: the list must say so rather than stay silent.
  await page.click('.sidebar-nav a[data-page="api-keys"]');
  const row = page.locator('tr', { hasText: 'e2e-restrict' });
  await expect(row).toContainText('any');
  await expect(row).toContainText('default');

  await row.getByRole('button', { name: 'Edit' }).click();
  await page.fill('#akIps', '10.0.0.0/8, 192.168.1.5');
  await page.fill('#akRate', '120');
  await page.click('#modalFooter button.btn-primary');

  await expect(row).toContainText('10.0.0.0/8, 192.168.1.5');
  await expect(row).toContainText('120/min');

  // And the change is real, not just rendered.
  const after = await page.request.get('/api/v1/auth/api-keys', {
    headers: { Authorization: `Bearer ${token}` },
  });
  const stored = (await after.json()).find(k => k.id === key.id);
  expect(stored.ip_allowlist).toEqual(['10.0.0.0/8', '192.168.1.5']);
  expect(stored.rate_limit_per_minute).toBe(120);
});

test('a revoked key cannot be edited', async ({ page }) => {
  await login(page);
  const token = await seedServiceAccount(page, 'svc_e2e_revoked');

  const created = await page.request.post('/api/v1/auth/api-keys', {
    headers: { Authorization: `Bearer ${token}` },
    data: { service_account_username: 'svc_e2e_revoked', name: 'e2e-revoked' },
  });
  const key = await created.json();
  await page.request.delete(`/api/v1/auth/api-keys/${key.id}`, {
    headers: { Authorization: `Bearer ${token}` },
  });

  const res = await page.request.patch(`/api/v1/auth/api-keys/${key.id}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { rate_limit_per_minute: 10 },
    failOnStatusCode: false,
  });
  expect(res.status()).toBe(409);
});
