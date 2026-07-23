const { test, expect } = require('@playwright/test');

async function login(page) {
  await page.goto('/admin');
  await page.fill('#loginUser', 'admin');
  await page.fill('#loginPass', 'changeme');
  await page.click('button:has-text("Login")');
  await expect(page.locator('#dbSelect')).toBeVisible();
}

test.beforeEach(async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await login(page);
});

test('Procedure Console: copying the raw JSON result works', async ({ page }) => {
  const mockResult = { code: 0, out_params: { p_salary: 5000 } };
  await page.route('**/api/v1/default/procedures/execute', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(mockResult),
  }));

  await page.click('.sidebar-nav a[data-page="procedure-console"]');
  await page.fill('#pcName', 'PKG_TEST.GET_INFO');
  await page.click('button:has-text("Execute")');
  await page.click('#pcResult button:has-text("Copy")');

  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(JSON.parse(clip)).toEqual(mockResult);
});

test('Write Console: copying the raw JSON result works', async ({ page }) => {
  const mockResult = { affected_rows: 1 };
  await page.route('**/api/v1/default/writes/execute', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(mockResult),
  }));

  await page.click('.sidebar-nav a[data-page="write-console"]');
  await page.fill('#wcTable', 'HR.EMPLOYEES');
  await page.click('button:has-text("Execute Write")');
  await page.click('#wcResult button:has-text("Copy")');

  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(JSON.parse(clip)).toEqual(mockResult);
});

test('Tasks: copying the raw JSON result works', async ({ page }) => {
  const mockResult = { task_id: 'abc123', status: 'SUCCESS', result: { rows: 1 } };
  await page.route('**/api/v1/tasks/abc123', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(mockResult),
  }));

  await page.click('.sidebar-nav a[data-page="tasks"]');
  await page.fill('#taskIdInput', 'abc123');
  await page.click('button:has-text("Check")');
  await page.click('#taskResult button:has-text("Copy")');

  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(JSON.parse(clip)).toEqual(mockResult);
});

test('Data Maintenance: copying the batch result raw JSON works', async ({ page }) => {
  await page.route('**/api/v1/default/schemas/describe/TEST_TABLE', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      table: 'TEST_TABLE',
      column_count: 1,
      primary_keys: ['ID'],
      columns: [{ name: 'ID', data_type: 'NUMBER', data_length: null, data_precision: 10, data_scale: 0, nullable: false, is_pk: true }],
    }),
  }));
  const batchResult = { total_affected: 1, operation_count: 1, results: [{ index: 0, operation: 'insert', affected_rows: 1 }] };
  await page.route('**/api/v1/default/writes/batch', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(batchResult),
  }));

  await page.click('.sidebar-nav a[data-page="data-maintenance"]');
  await page.fill('#dmTable', 'TEST_TABLE');
  await page.click('button:has-text("Load Schema")');
  await page.click('button:has-text("+ Insert Row")');
  await page.fill('input[data-idx="0"][data-target="values"][data-col="ID"]', '1');
  await page.click('button:has-text("Execute (Transaction)")');
  await page.click('#dmResultArea summary');
  await page.click('#dmResultArea button:has-text("Copy")');

  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(JSON.parse(clip)).toEqual(batchResult);
});
