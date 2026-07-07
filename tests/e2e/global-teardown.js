const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const { CONTAINER } = require('./test-env');

const STATE_FILE = path.join(__dirname, '.e2e-state.json');

function killPid(pid) {
  if (!pid) return;
  try {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/PID', String(pid), '/T', '/F']);
    } else {
      process.kill(pid);
    }
  } catch (_) { /* already gone */ }
}

module.exports = async () => {
  let state = {};
  try { state = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); } catch (_) { /* none */ }
  killPid(state.uvicornPid);
  spawnSync('docker', ['rm', '-f', state.container || CONTAINER]);
  try { fs.unlinkSync(STATE_FILE); } catch (_) { /* none */ }
  console.log('[e2e] torn down');
};
