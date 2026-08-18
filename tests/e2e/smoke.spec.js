const { test, expect } = require('@playwright/test');

test('admin login page loads', async ({ page }) => {
  await page.goto('/admin');
  await expect(page.locator('#loginUser')).toBeVisible();
  await expect(page.locator('#loginPass')).toBeVisible();
});

test('the session key is scoped to this deployment, and the shared one is retired', async ({ page }) => {
  /* localStorage is per-ORIGIN: /apihub and /apihub-test share it. An unscoped
     `hub_token` meant signing into one silently signed you out of the other. */
  await page.goto('/admin');
  await page.evaluate(() => localStorage.setItem('hub_token', 'left-over-from-the-other-deployment'));
  await page.reload();

  const state = await page.evaluate(() => ({
    scope: window.__BASE_PATH__ || '/',
    legacy: localStorage.getItem('hub_token'),
    scoped: localStorage.getItem(`hub_token@${window.__BASE_PATH__ || '/'}`),
  }));

  expect(state.legacy).toBeNull();                 // shared key removed, cannot stomp again
  expect(state.scoped).toBe('left-over-from-the-other-deployment');  // adopted, so a live session survives the upgrade
});

test('signing in writes only the scoped key', async ({ page }) => {
  await page.goto('/admin');
  await page.fill('#loginUser', 'admin');
  await page.fill('#loginPass', 'changeme');
  await page.click('#loginForm button[type="submit"]');
  await expect(page.locator('#appLayout')).toBeVisible();

  const { keys, scope } = await page.evaluate(() => ({
    keys: Object.keys(localStorage),
    scope: window.__BASE_PATH__ || '/',
  }));
  expect(keys).toContain(`hub_token@${scope}`);
  expect(keys).not.toContain('hub_token');
});
