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

// Groups only mean something with several definitions spread across them, and
// the global fixture seeds none — it predates the field, so its definitions are
// exactly the "(ungrouped)" case this spec also asserts.
const SEEDED = [
  { name: 'ZZ_beta', group_name: 'ZZ_grp_b' },
  { name: 'ZZ_alpha', group_name: 'ZZ_grp_b' },
  { name: 'ZZ_solo', group_name: 'ZZ_grp_a' },
];

async function seedGroupedDefinitions(page) {
  const token = await readToken(page);
  for (const d of SEEDED) {
    await page.request.post('/api/v1/default/queries/definitions', {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        name: d.name,
        db_alias: 'default',
        group_name: d.group_name,
        description: `E2E fixture: ${d.name}`,
        sql: 'SELECT 1',
        permission: '',
        params: {},
      },
      failOnStatusCode: false,   // re-running the suite against a warm DB is fine
    });
  }
  return token;
}

async function openDefinitions(page) {
  await page.click('.sidebar-nav a[data-page="sql-definitions"]');
  await expect(page.locator('.split-list-title')).toBeVisible();
}

test('the list groups by group_name, sorted by name, and switches panes on click', async ({ page }) => {
  await login(page);
  await seedGroupedDefinitions(page);
  await openDefinitions(page);

  // Left pane: groups, not permissions. Each seeded definition has its own
  // permission, so grouping by permission would put ZZ_alpha and ZZ_beta in
  // separate buckets instead of together under ZZ_grp_b.
  await expect(page.locator('.split-list-title')).toContainText('Groups');
  const groupNames = page.locator('.split-list-item-name');
  const labels = await groupNames.allTextContents();
  expect(labels).toEqual([...labels].sort((a, b) => a.localeCompare(b)));
  expect(labels).toContain('ZZ_grp_a');
  expect(labels).toContain('ZZ_grp_b');

  // Definitions with no group are still reachable rather than dropped.
  expect(labels).toContain('(ungrouped)');

  const groupB = page.locator('.split-list-item', { hasText: 'ZZ_grp_b' });
  await expect(groupB.locator('.split-list-item-count')).toHaveText('2');
  await groupB.click();
  await expect(groupB).toHaveClass(/active/);

  // Right pane: only that group's definitions, sorted by name.
  const rows = page.locator('#sqlDefTable tbody tr td:first-child');
  await expect(rows).toHaveText(['ZZ_alpha', 'ZZ_beta']);

  await page.locator('.split-list-item', { hasText: 'ZZ_grp_a' }).click();
  await expect(rows).toHaveText(['ZZ_solo']);
});

test('the selected group survives a trip to the detail page and back', async ({ page }) => {
  await login(page);
  await seedGroupedDefinitions(page);
  await openDefinitions(page);

  await page.locator('.split-list-item', { hasText: 'ZZ_grp_a' }).click();
  await page.locator('#sqlDefTable tbody tr', { hasText: 'ZZ_solo' })
    .getByRole('button', { name: 'Detail' }).click();
  await expect(page.locator('.detail-grid')).toContainText('ZZ_grp_a');

  await page.getByRole('button', { name: 'Back to list' }).click();
  await expect(page.locator('.split-list-item', { hasText: 'ZZ_grp_a' })).toHaveClass(/active/);
  await expect(page.locator('#sqlDefTable tbody tr td:first-child')).toHaveText(['ZZ_solo']);
});

test('the create form derives Name and Permission from the Group', async ({ page }) => {
  await login(page);
  await seedGroupedDefinitions(page);
  await openDefinitions(page);

  await page.click('button:has-text("+ New Definition")');

  // Typing a group prefixes the (empty) name and mirrors it into permission.
  await page.fill('#sdGroup', 'ZZ_grp_a');
  await expect(page.locator('#sdName')).toHaveValue('ZZ_grp_a_');
  await expect(page.locator('#sdPerm')).toHaveValue('query:ZZ_grp_a_');

  // Finishing the name carries the permission along with it.
  await page.fill('#sdName', 'ZZ_grp_a_report');
  await expect(page.locator('#sdPerm')).toHaveValue('query:ZZ_grp_a_report');

  // Changing the group swaps only the prefix; the tail the user typed stays.
  await page.fill('#sdGroup', 'ZZ_grp_b');
  await expect(page.locator('#sdName')).toHaveValue('ZZ_grp_b_report');
  await expect(page.locator('#sdPerm')).toHaveValue('query:ZZ_grp_b_report');
});

test('a hand-edited Permission is never overwritten by the Group or the Name', async ({ page }) => {
  await login(page);
  await openDefinitions(page);

  await page.click('button:has-text("+ New Definition")');
  await page.fill('#sdGroup', 'ZZ_grp_a');
  await page.fill('#sdPerm', 'query:ZZ_CUSTOM');

  await page.fill('#sdName', 'ZZ_grp_a_thing');
  await expect(page.locator('#sdPerm')).toHaveValue('query:ZZ_CUSTOM');

  await page.fill('#sdGroup', 'ZZ_grp_b');
  await expect(page.locator('#sdName')).toHaveValue('ZZ_grp_b_thing');
  await expect(page.locator('#sdPerm')).toHaveValue('query:ZZ_CUSTOM');
});

test('a definition created with a group is stored with it and lands in that group', async ({ page }) => {
  await login(page);
  const token = await seedGroupedDefinitions(page);
  await openDefinitions(page);

  await page.click('button:has-text("+ New Definition")');
  await page.fill('#sdGroup', 'ZZ_grp_c');
  await page.fill('#sdName', 'ZZ_grp_c_created');
  await page.fill('#sdSql', 'SELECT 1 AS ok');
  await page.click('.card-footer button.btn-primary');

  // Saving lands on the detail page, which reports the group back.
  await expect(page.locator('.detail-grid')).toContainText('ZZ_grp_c');

  // Rendered is not stored: confirm the column actually round-tripped.
  const res = await page.request.get('/api/v1/default/queries/definitions/ZZ_grp_c_created', {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(res.ok()).toBeTruthy();
  const stored = await res.json();
  expect(stored.group_name).toBe('ZZ_grp_c');
  expect(stored.permission).toBe('query:ZZ_grp_c_created');

  // Back from the detail page, the list opens on the group just saved into.
  await page.getByRole('button', { name: 'Back to list' }).click();
  await expect(page.locator('.split-list-item', { hasText: 'ZZ_grp_c' })).toHaveClass(/active/);
  await expect(page.locator('#sqlDefTable tbody tr td:first-child')).toHaveText(['ZZ_grp_c_created']);
});
