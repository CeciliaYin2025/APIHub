const { test, expect } = require('@playwright/test');

const ADMIN = { user: 'admin', pass: 'changeme' };

async function login(page) {
  await page.goto('/admin');
  await page.fill('#loginUser', ADMIN.user);
  await page.fill('#loginPass', ADMIN.pass);
  await page.click('#loginForm button[type="submit"]');
  await expect(page.locator('#appLayout')).toBeVisible();
}

async function seedLocalUser(page, username) {
  const token = await page.evaluate(() => localStorage.getItem('hub_token'));
  await page.request.post('/api/v1/auth/users', {
    headers: { Authorization: `Bearer ${token}` },
    data: { username, password: 'seeded-local-pw-12345', auth_source: 'local' },
    failOnStatusCode: false,
  });
  return token;
}

test('the Users list labels how each account authenticates', async ({ page }) => {
  await login(page);
  await seedLocalUser(page, 'e2e_local_user');

  await page.click('.sidebar-nav a[data-page="users"]');
  await expect(page.locator('tr', { hasText: 'e2e_local_user' })).toContainText('LOCAL');
  await expect(page.locator('tr', { hasText: 'admin' }).first()).toContainText('LOCAL');
});

test('switching an account to SSO saves even with the reset-password box filled', async ({ page }) => {
  // The regression: a password manager autofills "Reset Password" on the edit
  // form, the save PUTs the account to sso and then PATCHes a password the
  // server refuses (400). The edit landed, the error aborted the rest, and the
  // dialog stayed open — so it read as "cannot save" while half of it had.
  await login(page);
  await seedLocalUser(page, 'e2e_switch_sso');

  await page.click('.sidebar-nav a[data-page="users"]');
  await page.locator('tr', { hasText: 'e2e_switch_sso' }).getByRole('button', { name: 'Edit' }).click();

  // Stand in for the password manager: fill the box the admin never touched.
  await page.fill('#editUserForm input[name="new_password"]', 'autofilled-by-the-browser');
  await page.selectOption('#editUserForm select[name="auth_source"]', 'sso');

  // Choosing SSO must empty and disable that box on the spot.
  await expect(page.locator('#editUserForm input[name="new_password"]')).toBeDisabled();
  await expect(page.locator('#editUserForm input[name="new_password"]')).toHaveValue('');

  await page.click('button:has-text("Save Changes")');

  await expect(page.locator('.modal-overlay')).toBeHidden();
  await expect(page.locator('.toast-container')).toContainText('User updated');
  await expect(page.locator('tr', { hasText: 'e2e_switch_sso' })).toContainText('SSO');
});

test('an SSO account cannot be given a password', async ({ page }) => {
  await login(page);
  const token = await seedLocalUser(page, 'e2e_sso_pw');
  const users = await (await page.request.get('/api/v1/auth/users', {
    headers: { Authorization: `Bearer ${token}` },
  })).json();
  const id = users.find(u => u.username === 'e2e_sso_pw').id;

  const toSso = await page.request.put(`/api/v1/auth/users/${id}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { auth_source: 'sso' },
  });
  expect(toSso.ok()).toBeTruthy();

  const reset = await page.request.patch(`/api/v1/auth/users/${id}/password`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { new_password: 'should-be-refused' },
    failOnStatusCode: false,
  });
  expect(reset.status()).toBe(400);

  // And the old password must not work either — the gate is in the login path,
  // not only on the reset endpoint.
  const login = await page.request.post('/api/v1/auth/token', {
    form: { username: 'e2e_sso_pw', password: 'seeded-local-pw-12345' },
    failOnStatusCode: false,
  });
  expect(login.status()).toBe(401);
});
