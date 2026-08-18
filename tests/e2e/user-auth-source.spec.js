const { test, expect } = require('@playwright/test');

/* The SPA scopes its storage keys by base path (localStorage is per-origin, so
   /apihub and /apihub-test would otherwise share one session). Read the key the
   app actually wrote rather than hardcoding the legacy name. */
const readToken = (page) => page.evaluate(() => localStorage.getItem(`hub_token@${window.__BASE_PATH__ || '/'}`));

const ADMIN = { user: 'admin', pass: 'changeme' };

async function login(page) {
  await page.goto('/admin');
  await page.fill('#loginUser', ADMIN.user);
  await page.fill('#loginPass', ADMIN.pass);
  await page.click('#loginForm button[type="submit"]');
  await expect(page.locator('#appLayout')).toBeVisible();
}

async function seedLocalUser(page, username) {
  const token = await readToken(page);
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

test('switching an account to SSO saves, and leaves Full Name alone', async ({ page }) => {
  // The regression: the Edit dialog carried a password field, so a browser
  // password manager treated it as a login form — filling that field AND
  // overwriting Full Name with the operator's own username. Saving then PUT the
  // account to sso and PATCHed a password the server rightly refuses (400),
  // which aborted the handler after the PUT had already landed: the dialog
  // stayed open and it read as "cannot save" while half of it had.
  await login(page);
  await seedLocalUser(page, 'e2e_switch_sso');

  await page.click('.sidebar-nav a[data-page="users"]');
  await page.locator('tr', { hasText: 'e2e_switch_sso' }).getByRole('button', { name: 'Edit' }).click();

  // No password field on this form at all — that is what keeps the password
  // manager away from Full Name.
  await expect(page.locator('#editUserForm input[type="password"]')).toHaveCount(0);

  await page.fill('#editUserForm input[name="full_name"]', 'Real Name');
  await page.selectOption('#editUserForm select[name="auth_source"]', 'sso');
  await page.click('button:has-text("Save Changes")');

  await expect(page.locator('#modalOverlay')).not.toHaveClass(/active/);
  await expect(page.locator('.toast-container')).toContainText('User updated');
  const row = page.locator('tr', { hasText: 'e2e_switch_sso' });
  await expect(row).toContainText('SSO');
  await expect(row).toContainText('Real Name');
});

test('password reset is its own dialog, and is offered only where it can work', async ({ page }) => {
  await login(page);
  await seedLocalUser(page, 'e2e_pw_dialog');

  await page.click('.sidebar-nav a[data-page="users"]');
  const row = page.locator('tr', { hasText: 'e2e_pw_dialog' });
  await row.getByRole('button', { name: 'Password' }).click();
  await page.fill('#resetPwForm input[name="new_password"]', 'a-new-password-12345');
  await page.click('button:has-text("Set Password")');
  await expect(page.locator('.toast-container')).toContainText('Password updated');

  const ok = await page.request.post('/api/v1/auth/token', {
    form: { username: 'e2e_pw_dialog', password: 'a-new-password-12345' },
    failOnStatusCode: false,
  });
  expect(ok.status()).toBe(200);

  // Once the account is SSO the button goes away: a password there is refused
  // by the server, so offering the dialog would only lead to a dead end.
  await row.getByRole('button', { name: 'Edit' }).click();
  await page.selectOption('#editUserForm select[name="auth_source"]', 'sso');
  await page.click('button:has-text("Save Changes")');
  await expect(row).toContainText('SSO');
  await expect(row.getByRole('button', { name: 'Password' })).toHaveCount(0);
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
