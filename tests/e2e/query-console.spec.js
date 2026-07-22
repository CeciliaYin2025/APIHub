const { test, expect } = require('@playwright/test');

async function login(page) {
  await page.goto('/admin');
  await page.fill('#loginUser', 'admin');
  await page.fill('#loginPass', 'changeme');
  await page.click('button:has-text("Login")');
  // Top bar appears only after a successful login.
  await expect(page.locator('#dbSelect')).toBeVisible();
}

async function openQueryConsole(page) {
  await page.click('.sidebar-nav a[data-page="query-console"]');
  await expect(page.locator('#qcName')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await login(page);
  await openQueryConsole(page);
});

test('dropdown lists the current DB\'s seeded queries', async ({ page }) => {
  // The picker populates asynchronously (fetch → qcPopulatePicker); poll until settled.
  // Hermetic fixture: exactly the definitions seeded in global-setup, plus the placeholder.
  await expect
    .poll(async () => (await page.$$eval('#qcName option', (els) => els.map((e) => e.value))).sort())
    .toEqual(['', 'e2e_types', 'employees', 'inventory_view']);
});

test('selecting a string-param query autofills an example', async ({ page }) => {
  await page.selectOption('#qcName', 'employees');
  await expect
    .poll(async () => JSON.parse(await page.inputValue('#qcParams')))
    .toEqual({ dept: 'example' });
});

test('selecting a multi-param query autofills all params', async ({ page }) => {
  await page.selectOption('#qcName', 'inventory_view');
  await expect
    .poll(async () => JSON.parse(await page.inputValue('#qcParams')))
    .toEqual({ product_code: 'example', warehouse: 'example' });
});

test('example values follow param type (number/date/string)', async ({ page }) => {
  await page.selectOption('#qcName', 'e2e_types');
  await expect
    .poll(async () => JSON.parse(await page.inputValue('#qcParams')))
    .toEqual({ n: 0, d: '2024-01-01', s: 'example' });
});

test('Request Body preview reflects the selected query and params', async ({ page }) => {
  await page.selectOption('#qcName', 'employees');
  const body = await page.textContent('#qcBodyPreview');
  expect(body).toContain('"query_name": "employees"');
  expect(body).toContain('"dept": "example"');
});

test('switching the DB clears the selection and parameters', async ({ page }) => {
  await page.selectOption('#qcName', 'employees');
  await page.selectOption('#dbSelect', 'pg_test');
  // The change handler re-loads the picker and clears async; poll until settled.
  await expect.poll(async () => await page.inputValue('#qcName')).toBe('');
  await expect.poll(async () => await page.inputValue('#qcParams')).toBe('{}');
  const opts = await page.$$eval('#qcName option', (els) => els.map((e) => e.value));
  expect(opts).toEqual(['']);
});

test('copying the Raw JSON response places plain JSON on the clipboard', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const mockResult = { total: 1, columns: ['dept'], data: [{ dept: 'IT' }] };
  await page.route('**/api/v1/default/queries/execute', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(mockResult),
  }));

  await page.selectOption('#qcName', 'employees');
  await page.click('button:has-text("Execute Query")');
  await page.click('#qcResult summary');
  await page.click('#qcResult button:has-text("Copy")');

  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(JSON.parse(clip)).toEqual(mockResult);
});
