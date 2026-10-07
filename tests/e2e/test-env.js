const fs = require('fs');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const APP_PORT = 8199;
const PG_PORT = 55432;
const CONTAINER = 'apihub-e2e-pg';
const BASE_URL = `http://127.0.0.1:${APP_PORT}`;

function resolvePython() {
  const win = path.join(REPO_ROOT, '.venv', 'Scripts', 'python.exe');
  const nix = path.join(REPO_ROOT, '.venv', 'bin', 'python');
  if (fs.existsSync(win)) return win;
  if (fs.existsSync(nix)) return nix;
  return process.env.E2E_PYTHON || 'python';
}

const APP_ENV = {
  POSTGRES_HOST: 'localhost',
  POSTGRES_PORT: String(PG_PORT),
  POSTGRES_USER: 'api_hub',
  POSTGRES_PASSWORD: 'changeme',
  POSTGRES_DB: 'api_hub',
  SECRET_KEY: 'e2e-secret-key',
  // All specs hit the app from 127.0.0.1; the default 60/min rate limit would
  // return 429 mid-suite. Raise it far above the suite's request volume.
  RATE_LIMIT_PER_MINUTE: '100000',
  // Hermetic data sources (see fixtures/databases/databases.yaml) instead of the
  // developer's own, possibly real, app/database_definitions.
  DATABASE_DEFINITIONS_DIR: path.join(__dirname, 'fixtures', 'databases'),
  DB_PG_TEST_PASSWORD: 'changeme',
};

module.exports = {
  REPO_ROOT,
  APP_PORT,
  PG_PORT,
  CONTAINER,
  BASE_URL,
  PYTHON: resolvePython(),
  APP_ENV,
};
