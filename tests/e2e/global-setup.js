const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const { REPO_ROOT, APP_PORT, PG_PORT, CONTAINER, BASE_URL, PYTHON, APP_ENV } = require('./test-env');

const STATE_FILE = path.join(__dirname, '.e2e-state.json');
const LOG_FILE = path.join(__dirname, '.uvicorn.log');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { encoding: 'utf8', ...opts });
  if (res.error) throw res.error;
  return res;
}

async function waitFor(label, fn, { tries = 60, delay = 1000 } = {}) {
  for (let i = 0; i < tries; i++) {
    try {
      if (await fn()) return;
    } catch (_) { /* keep polling */ }
    await sleep(delay);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

module.exports = async () => {
  const env = { ...process.env, ...APP_ENV };

  // 1. Fresh Postgres container.
  console.log('[e2e] starting Postgres container...');
  run('docker', ['rm', '-f', CONTAINER]);
  const up = run('docker', ['run', '-d', '--name', CONTAINER,
    '-e', 'POSTGRES_USER=api_hub', '-e', 'POSTGRES_PASSWORD=changeme',
    '-e', 'POSTGRES_DB=api_hub', '-p', `${PG_PORT}:5432`, 'postgres:16-alpine']);
  if (up.status !== 0) throw new Error(`docker run failed: ${up.stderr}`);

  // 2. Wait for Postgres to accept connections.
  await waitFor('Postgres ready', () =>
    run('docker', ['exec', CONTAINER, 'pg_isready', '-U', 'api_hub']).status === 0);

  // 3. Seed tables + admin user.
  console.log('[e2e] running init_db...');
  const init = run(PYTHON, ['-m', 'scripts.init_db'], { cwd: REPO_ROOT, env });
  if (init.status !== 0) throw new Error(`init_db failed:\n${init.stdout}\n${init.stderr}`);

  // 4. Launch uvicorn (detached; logs to file).
  console.log('[e2e] launching uvicorn...');
  const logFd = fs.openSync(LOG_FILE, 'w');
  const app = spawn(PYTHON, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(APP_PORT)],
    { cwd: REPO_ROOT, env, stdio: ['ignore', logFd, logFd], detached: true });
  app.unref();

  // 5. Wait for health.
  await waitFor('app health', async () => {
    const res = await fetch(`${BASE_URL}/api/v1/health`);
    return res.ok;
  });

  // 6. Seed the SQL definitions the specs assert against (dummy SQL, never
  //    executed). NOTE: seeding any definition into the DB flips the registry
  //    from YAML-fallback to DB-only, so the fixture must seed EVERY definition
  //    the tests reference — it cannot rely on the app/sql_definitions samples.
  console.log('[e2e] seeding SQL definitions...');
  const form = new URLSearchParams({ username: 'admin', password: 'changeme' });
  const tokRes = await fetch(`${BASE_URL}/api/v1/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form,
  });
  const { access_token } = await tokRes.json();

  const p = (type, opts = {}) => ({ type, required: !!opts.required, description: opts.description || '' });
  const definitions = [
    { name: 'employees', params: { dept: p('string', { description: 'Filter by department code' }) } },
    { name: 'inventory_view', params: {
      product_code: p('string', { required: true, description: 'SKU or product code to filter by' }),
      warehouse: p('string'),
    } },
    { name: 'e2e_types', params: { n: p('number'), d: p('date'), s: p('string') } },
  ];
  for (const d of definitions) {
    const res = await fetch(`${BASE_URL}/api/v1/default/queries/definitions`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${access_token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: d.name,
        db_alias: 'default',
        description: `E2E fixture: ${d.name}`,
        sql: 'SELECT 1',
        permission: '',
        params: d.params,
      }),
    });
    if (!res.ok) throw new Error(`seed ${d.name} failed: ${res.status} ${await res.text()}`);
  }

  fs.writeFileSync(STATE_FILE, JSON.stringify({ uvicornPid: app.pid, container: CONTAINER }));
  console.log(`[e2e] ready at ${BASE_URL} (uvicorn pid ${app.pid})`);
};
