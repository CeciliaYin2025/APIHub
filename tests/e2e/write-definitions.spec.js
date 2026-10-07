const { test, expect } = require('@playwright/test');
const { execFileSync } = require('child_process');
const { CONTAINER } = require('./test-env');

/* Named write definitions against a real PostgreSQL: the pg_test alias is the
   throwaway container itself (fixtures/databases/databases.yaml), so the writes
   land in a real table and can be counted with psql. */

const readToken = (page) => page.evaluate(() => localStorage.getItem(`hub_token@${window.__BASE_PATH__ || '/'}`));
const ALIAS = 'pg_test';
const TABLE = 'e2e_i698';
const NAME = 'e2e_i698_load';

function psql(sql) {
  return execFileSync('docker', ['exec', CONTAINER, 'psql', '-U', 'api_hub', '-d', 'api_hub', '-Atc', sql],
    { encoding: 'utf8' }).trim();
}

test.beforeAll(() => {
  psql(`DROP TABLE IF EXISTS ${TABLE}`);
  psql(`CREATE TABLE ${TABLE} (
    organization_code varchar(40) NOT NULL,
    invoice_no varchar(50),
    receipt_date date,
    units numeric,
    source_file varchar(100),
    creation_date timestamp DEFAULT now())`);
  // A row another writer owns: no source_file, so no replace key can reach it.
  psql(`INSERT INTO ${TABLE} (organization_code, invoice_no) VALUES ('ERP', 'SEED-1')`);
});

async function login(page) {
  await page.goto('/admin');
  await page.fill('#loginUser', 'admin');
  await page.fill('#loginPass', 'changeme');
  await page.click('#loginForm button[type="submit"]');
  await expect(page.locator('#appLayout')).toBeVisible();
  await page.selectOption('#dbSelect', ALIAS);
}

test('create a definition in the UI, loading its columns from the table', async ({ page }) => {
  await login(page);
  await page.click('.sidebar-nav a[data-page="write-definitions"]');
  await expect(page.locator('.card-header h2')).toContainText(`Write Definitions — ${ALIAS}`);

  await page.click('text=+ New Write Definition');
  await page.fill('#wdGroupIn', 'e2e');
  await page.fill('#wdName', NAME);
  await page.fill('#wdTableIn', TABLE);
  await page.click('text=Load columns from table');
  await expect(page.locator('#wdColRows tr')).toHaveCount(6);

  // creation_date has a DB default: callers must not send it.
  const row = (col) => page.locator('#wdColRows tr', { has: page.locator(`.wd-c-name[value="${col}"]`) });
  await row('creation_date').locator('button').click();
  await expect(page.locator('#wdColRows tr')).toHaveCount(5);
  await expect(row('receipt_date').locator('.wd-c-type')).toHaveValue('date');
  await expect(row('units').locator('.wd-c-type')).toHaveValue('number');
  await expect(row('organization_code').locator('.wd-c-req')).toBeChecked();
  await row('source_file').locator('.wd-c-key').check();

  await page.click('.card-footer button:has-text("Create")');
  await expect(page.locator('#pageTitle')).toHaveText(`Write Definition: ${NAME}`);
  await expect(page.locator('.detail-value').filter({ hasText: 'write:' + NAME })).toBeVisible();
  await expect(page.locator('td', { hasText: 'source_file' }).locator('.badge')).toHaveText('key');
  await expect(page.locator('#wdExample, pre').first()).toContainText('"key"');

  // Listed under its group.
  await page.click('text=Back to list');
  await expect(page.locator('.split-list-item', { hasText: 'e2e' })).toBeVisible();
  await expect(page.locator('#wdTable')).toContainText(NAME);
});

test('replace is idempotent per key and never reaches other rows', async ({ page }) => {
  await login(page);
  const token = await readToken(page);
  const run = (body) => page.request.post(`/api/v1/${ALIAS}/writes/named/execute`, {
    headers: { Authorization: `Bearer ${token}` }, data: { name: NAME, ...body }, failOnStatusCode: false,
  });
  const r = (inv, units, file = 'a.csv') =>
    ({ organization_code: 'EIAMSFGI', invoice_no: inv, receipt_date: '2026/10/07', units, source_file: file });

  let res = await run({ key: { source_file: 'a.csv' }, rows: [r('INV1', 2), r('INV2', 1)] });
  expect(res.status()).toBe(200);
  expect(await res.json()).toMatchObject({ deleted_rows: 0, inserted_rows: 2, committed: true });

  // Resend of the same file with corrected content: replaces exactly its rows.
  res = await run({ key: { source_file: 'a.csv' }, rows: [r('INV1', 15)] });
  expect(await res.json()).toMatchObject({ deleted_rows: 2, inserted_rows: 1 });
  await run({ key: { source_file: 'b.csv' }, rows: [r('INV9', 9, 'b.csv')] });

  expect(psql(`SELECT invoice_no||':'||units FROM ${TABLE} WHERE source_file='a.csv'`)).toBe('INV1:15');
  expect(psql(`SELECT count(*) FROM ${TABLE} WHERE source_file='b.csv'`)).toBe('1');
  expect(psql(`SELECT count(*) FROM ${TABLE} WHERE invoice_no='SEED-1'`)).toBe('1');
  // Dates parsed by the Hub, DB default filled in.
  expect(psql(`SELECT receipt_date||' '||(creation_date IS NOT NULL) FROM ${TABLE} WHERE source_file='a.csv'`))
    .toBe('2026-10-07 true');

  // Dry run reports real counts and writes nothing.
  res = await run({ key: { source_file: 'a.csv' }, rows: [r('X1', 1), r('X2', 2), r('X3', 3)], dry_run: true });
  expect(await res.json()).toMatchObject({ deleted_rows: 1, inserted_rows: 3, committed: false, dry_run: true });
  expect(psql(`SELECT count(*) FROM ${TABLE} WHERE source_file='a.csv'`)).toBe('1');

  // The guards.
  for (const [body, msg] of [
    [{ rows: [r('INV1', 1)] }, 'key is required'],
    [{ key: { source_file: '' }, rows: [] }, 'must not be empty'],
    [{ key: { source_file: 'a.csv' }, rows: [r('INV1', 1, 'other.csv')] }, 'must equal the request key'],
    [{ key: { source_file: 'a.csv' }, rows: [{ ...r('INV1', 1), creation_date: '2026-01-01' }] }, 'not declared'],
    [{ key: { source_file: 'a.csv' }, rows: [{ ...r('INV1', 1), receipt_date: '07-OCT-2026' }] }, 'expected a date'],
  ]) {
    res = await run(body);
    expect(res.status(), msg).toBe(400);
    expect(JSON.stringify(await res.json())).toContain(msg);
  }
  expect(psql(`SELECT count(*) FROM ${TABLE}`)).toBe('3');
});

test('the detail page dry run shows counts without writing', async ({ page }) => {
  await login(page);
  await page.click('.sidebar-nav a[data-page="write-definitions"]');
  await page.click(`#wdTable button:has-text("Detail")`);
  await page.fill('#wdDryBody', JSON.stringify({
    name: NAME, key: { source_file: 'b.csv' },
    rows: [{ organization_code: 'X', source_file: 'b.csv' }, { organization_code: 'Y', source_file: 'b.csv' }],
  }));
  await page.click('button:has-text("Dry run")');
  await expect(page.locator('#wdDryResult')).toContainText('"deleted_rows": 1');
  await expect(page.locator('#wdDryResult')).toContainText('"inserted_rows": 2');
  await expect(page.locator('#wdDryResult')).toContainText('"committed": false');
  expect(psql(`SELECT count(*) FROM ${TABLE} WHERE source_file='b.csv'`)).toBe('1');
});
