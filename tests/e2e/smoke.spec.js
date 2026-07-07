const { test, expect } = require('@playwright/test');

test('admin login page loads', async ({ page }) => {
  await page.goto('/admin');
  await expect(page.locator('#loginUser')).toBeVisible();
  await expect(page.locator('#loginPass')).toBeVisible();
});
