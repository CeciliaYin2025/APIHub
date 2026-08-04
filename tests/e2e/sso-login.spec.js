const { test, expect } = require('@playwright/test');

const ADMIN = { user: 'admin', pass: 'changeme' };

// Mint a JWT the same way GET /auth/sso/callback would, via the normal password
// login. The callback contract with the SPA is just a URL fragment, so the SSO
// surface can be exercised end to end without a live isap-auth.
async function mintToken(request) {
  const res = await request.post('/api/v1/auth/token', {
    form: { username: ADMIN.user, password: ADMIN.pass },
  });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).access_token;
}

test('a valid #sso_token= fragment signs the user in and is stripped', async ({ page, request }) => {
  const jwt = await mintToken(request);

  await page.goto(`/admin#sso_token=${jwt}`);
  await expect(page.locator('#appLayout')).toBeVisible();
  expect(page.url()).not.toContain('#');
  expect(await page.evaluate(() => localStorage.getItem('hub_token'))).toBe(jwt);
});

test('an #sso_error= fragment stays on the login page with a message', async ({ page }) => {
  await page.goto('/admin#sso_error=not_provisioned');
  await expect(page.locator('#loginPage')).toBeVisible();
  await expect(page.locator('.toast-container')).toContainText('no API Hub user');
  expect(page.url()).not.toContain('#');
});

test('an unknown #sso_error= slug still surfaces a generic message', async ({ page }) => {
  await page.goto('/admin#sso_error=something_new');
  await expect(page.locator('#loginPage')).toBeVisible();
  await expect(page.locator('.toast-container')).toContainText('SSO login failed');
});

test('the SSO button is hidden when ADMIN_SSO_ENABLED is off', async ({ page }) => {
  // The e2e stack runs without isap-auth, so ADMIN_SSO_ENABLED stays false.
  await page.goto('/admin');
  await expect(page.locator('#ssoBlock')).toBeHidden();
});
