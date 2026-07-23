/**
 * Oracle API Hub — Admin SPA
 *
 * Single-page management console communicating with the FastAPI backend.
 */

/* ── Global State ─────────────────────────────────────────────────────── */
const API = (window.__BASE_PATH__ || '') + '/api/v1';
let token = localStorage.getItem('hub_token') || '';
let currentUser = null;
let currentDb = localStorage.getItem('hub_current_db') || '';
let databases = [];
let qcDefs = [];  // definitions for the Query Console picker (scoped to currentDb)
let qcAuthMode = 'jwt';  // 'jwt' | 'apikey' — which credential the generated examples show

/* ── Bootstrap ────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  if (token) {
    fetchCurrentUser().then(ok => {
      if (ok) showApp();
      else showLogin();
    });
  } else {
    showLogin();
  }
  bindGlobalEvents();
});

/* ── Auth ─────────────────────────────────────────────────────────────── */
function showLogin() {
  document.getElementById('loginPage').classList.remove('hidden');
  document.getElementById('appLayout').classList.add('hidden');
}

async function showApp() {
  document.getElementById('loginPage').classList.add('hidden');
  document.getElementById('appLayout').classList.remove('hidden');
  document.getElementById('userBadge').textContent = currentUser?.username || '?';
  await loadDatabases();
  navigateTo('dashboard');
}

document.getElementById('loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  const username = document.getElementById('loginUser').value;
  const password = document.getElementById('loginPass').value;
  try {
    const res = await fetch(`${API}/auth/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `username=${encodeURIComponent(username)}&password=${encodeURIComponent(password)}`,
    });
    if (!res.ok) throw new Error('Login failed');
    const data = await res.json();
    token = data.access_token;
    localStorage.setItem('hub_token', token);
    await fetchCurrentUser();
    showApp();
    toast('Login successful', 'success');
  } catch {
    toast('Invalid username or password', 'error');
  }
});

document.getElementById('logoutBtn').addEventListener('click', () => {
  token = '';
  currentUser = null;
  localStorage.removeItem('hub_token');
  showLogin();
});

async function fetchCurrentUser() {
  try {
    currentUser = await api('GET', '/auth/me');
    return true;
  } catch {
    token = '';
    localStorage.removeItem('hub_token');
    return false;
  }
}

/* ── Database selector ───────────────────────────────────────────────── */
async function loadDatabases() {
  try {
    databases = await api('GET', '/databases');
  } catch {
    databases = [];
  }
  const sel = document.getElementById('dbSelect');
  sel.innerHTML = '';
  if (databases.length === 0) {
    sel.innerHTML = '<option value="">(no databases)</option>';
    currentDb = '';
  } else {
    databases.forEach(db => {
      const opt = document.createElement('option');
      opt.value = db.alias;
      const typeBadge = db.db_type && db.db_type !== 'oracle' ? ` [${db.db_type.toUpperCase()}]` : '';
      opt.textContent = db.alias + typeBadge + (db.description ? ' — ' + db.description : '');
      sel.appendChild(opt);
    });
    // Restore last selection if valid
    if (currentDb && databases.some(d => d.alias === currentDb)) {
      sel.value = currentDb;
    } else {
      currentDb = databases[0].alias;
      sel.value = currentDb;
    }
    localStorage.setItem('hub_current_db', currentDb);
  }
  sel.addEventListener('change', () => {
    currentDb = sel.value;
    localStorage.setItem('hub_current_db', currentDb);
    // If the Query Console is open, re-scope its picker to the new db and
    // clear the stale selection + parameters.
    if (document.querySelector('.sidebar-nav a[data-page="query-console"].active')) {
      qcLoadDefinitions().then(() => {
        qcPopulatePicker();
        const nameEl = document.getElementById('qcName');
        const paramsEl = document.getElementById('qcParams');
        if (nameEl) nameEl.value = '';
        if (paramsEl) paramsEl.value = '{}';
        qcRenderParamsInfo(null);
        refreshQcPreviews();
      });
    }
  });
}

function dbPrefix() {
  return currentDb ? `/${currentDb}` : '';
}

/* ── API helper ───────────────────────────────────────────────────────── */
async function api(method, path, body) {
  const opts = {
    method,
    headers: { 'Authorization': `Bearer ${token}` },
  };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`${API}${path}`, opts);
  if (res.status === 401) { showLogin(); throw new Error('Unauthorized'); }
  // Some endpoints return 204 No Content (password reset, deletes) — an empty
  // body has no JSON to parse, so read as text and parse only when present.
  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    const detail = (data && typeof data === 'object') ? data.detail : data;
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return data;
}

/* ── Navigation ───────────────────────────────────────────────────────── */
function bindGlobalEvents() {
  document.querySelectorAll('.sidebar-nav a[data-page]').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      navigateTo(a.dataset.page);
    });
  });
  // Intentionally NOT closing the modal on backdrop (outside) click: while
  // editing a definition/user/role, an accidental click on the blank area
  // would discard in-progress input. The modal is opened/closed only by
  // explicit controls (the ✕ button, Cancel, or a successful save).
  document.getElementById('modalClose').addEventListener('click', closeModal);
}

function navigateTo(page) {
  document.querySelectorAll('.sidebar-nav a').forEach(a => a.classList.remove('active'));
  const link = document.querySelector(`.sidebar-nav a[data-page="${page}"]`);
  if (link) link.classList.add('active');

  const titles = {
    dashboard: 'Dashboard', users: 'User Management', roles: 'Role Management',
    permissions: 'Permission Management', 'api-keys': 'API Keys',
    'sql-definitions': 'SQL Definitions',
    'query-console': 'Query Console', 'procedure-console': 'Procedure Console',
    'write-console': 'Write Console', 'data-maintenance': 'Data Maintenance',
    tasks: 'Async Tasks',
  };
  document.getElementById('pageTitle').textContent = titles[page] || page;
  const renderers = {
    dashboard: renderDashboard, users: renderUsers, roles: renderRoles,
    permissions: renderPermissions, 'api-keys': renderApiKeys,
    'sql-definitions': renderSqlDefs,
    'query-console': renderQueryConsole, 'procedure-console': renderProcConsole,
    'write-console': renderWriteConsole, 'data-maintenance': renderDataMaintenance,
    tasks: renderTasks,
  };
  (renderers[page] || renderDashboard)();
}

/* ── Toast ─────────────────────────────────────────────────────────────── */
function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById('toastContainer').appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

/* ── Modal ─────────────────────────────────────────────────────────────── */
function openModal(title, bodyHtml, footerHtml) {
  document.getElementById('modalTitle').textContent = title;
  document.getElementById('modalBody').innerHTML = bodyHtml;
  document.getElementById('modalFooter').innerHTML = footerHtml || '';
  document.getElementById('modalOverlay').classList.add('active');
}
function closeModal() {
  document.getElementById('modalOverlay').classList.remove('active');
}

/* ── HTML Helpers ──────────────────────────────────────────────────────── */
function esc(s) {
  if (s == null) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

function syntaxHighlight(json) {
  if (typeof json !== 'string') json = JSON.stringify(json, null, 2);
  return json.replace(/("(\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, match => {
    let cls = 'json-number';
    if (/^"/.test(match)) {
      cls = /:$/.test(match) ? 'json-key' : 'json-string';
    } else if (/true|false/.test(match)) {
      cls = 'json-bool';
    } else if (/null/.test(match)) {
      cls = 'json-null';
    }
    return `<span class="${cls}">${match}</span>`;
  });
}

/* ── Raw JSON copy (shared across consoles) ──────────────────────────────
   The visible <pre> holds syntax-highlighted HTML, not plain text, so the
   copyable plain-text JSON is kept in a small registry keyed by a per-block
   id and read back when the Copy button is clicked. */
const _copyRegistry = {};

function registerCopyPayload(id, text) {
  _copyRegistry[id] = text;
}

window.copyPayload = async function(id, label) {
  const text = _copyRegistry[id];
  if (!text) return toast('Nothing to copy', 'error');
  try {
    await navigator.clipboard.writeText(text);
    toast(`${label} copied`, 'success');
  } catch {
    toast('Copy failed', 'error');
  }
};

// Renders a syntax-highlighted <pre> plus a Copy button for `data`,
// registering the plain-text JSON under `id`. Callers embed the result
// wherever a raw JSON response used to be shown directly.
function rawJsonBlock(id, data) {
  const json = JSON.stringify(data, null, 2);
  registerCopyPayload(id, json);
  return `
    <div class="qc-code-toolbar" style="margin-bottom:4px">
      <span></span>
      <button class="btn btn-outline btn-sm" onclick="copyPayload('${id}','JSON')">&#128203; Copy</button>
    </div>
    <pre>${syntaxHighlight(json)}</pre>`;
}

/* ══════════════════════════════════════════════════════════════════════════
   Page Renderers
   ══════════════════════════════════════════════════════════════════════════ */

/* ── Dashboard ────────────────────────────────────────────────────────── */
async function renderDashboard() {
  const area = document.getElementById('contentArea');
  area.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div></div>';

  try {
    const [users, roles, perms, defs] = await Promise.all([
      api('GET', '/auth/users').catch(() => []),
      api('GET', '/auth/roles').catch(() => []),
      api('GET', '/auth/permissions').catch(() => []),
      api('GET', `${dbPrefix()}/queries/definitions`).catch(() => []),
    ]);

    area.innerHTML = `
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-info"><h3>Users</h3><div class="stat-value">${users.length}</div></div>
          <div class="stat-icon blue">&#9823;</div>
        </div>
        <div class="stat-card">
          <div class="stat-info"><h3>Roles</h3><div class="stat-value">${roles.length}</div></div>
          <div class="stat-icon green">&#9881;</div>
        </div>
        <div class="stat-card">
          <div class="stat-info"><h3>Permissions</h3><div class="stat-value">${perms.length}</div></div>
          <div class="stat-icon yellow">&#9919;</div>
        </div>
        <div class="stat-card">
          <div class="stat-info"><h3>SQL Definitions</h3><div class="stat-value">${defs.length}</div></div>
          <div class="stat-icon blue">&#9998;</div>
        </div>
        <div class="stat-card">
          <div class="stat-info"><h3>Databases</h3><div class="stat-value">${databases.length}</div></div>
          <div class="stat-icon green">&#9741;</div>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
        <div class="card">
          <div class="card-header"><h2>Recent Users</h2></div>
          <div class="card-body no-pad">
            <table class="data-table"><thead><tr><th>Username</th><th>Full Name</th><th>Status</th></tr></thead>
            <tbody>${users.slice(0, 5).map(u => `<tr>
              <td>${esc(u.username)}</td>
              <td>${esc(u.full_name || '—')}</td>
              <td>${u.is_active ? '<span class="badge badge-green">Active</span>' : '<span class="badge badge-red">Inactive</span>'}</td>
            </tr>`).join('')}</tbody></table>
          </div>
        </div>

        <div class="card">
          <div class="card-header"><h2>SQL Definitions</h2></div>
          <div class="card-body no-pad">
            <table class="data-table"><thead><tr><th>Name</th><th>Permission</th></tr></thead>
            <tbody>${defs.slice(0, 5).map(d => `<tr>
              <td>${esc(d.name)}</td>
              <td><span class="tag">${esc(d.permission)}</span></td>
            </tr>`).join('')}</tbody></table>
          </div>
        </div>
      </div>

      <div class="card mt-4">
        <div class="card-header">
          <h2>Databases (${databases.length})</h2>
          <button class="btn btn-sm" onclick="refreshDbStatus()">Refresh</button>
        </div>
        <div class="card-body no-pad">
          <table class="data-table"><thead><tr><th>Alias</th><th>Type</th><th>Description</th><th>Connection</th><th>User</th><th>Status</th></tr></thead>
          <tbody>${databases.map(d => `<tr>
            <td><strong>${esc(d.alias)}</strong>${d.alias === currentDb ? ' <span class="badge badge-blue">Active</span>' : ''}</td>
            <td><span class="badge ${d.db_type === 'oracle' ? 'badge-blue' : d.db_type === 'postgresql' ? 'badge-green' : 'badge-amber'}">${esc((d.db_type || 'oracle').toUpperCase())}</span></td>
            <td>${esc(d.description || '—')}</td>
            <td><code>${esc(d.dsn || (d.host ? d.host + ':' + d.port + '/' + d.database : '—'))}</code></td>
            <td>${esc(d.user)}</td>
            <td id="dbstat-${d.alias}">Checking…</td>
          </tr>`).join('')}</tbody></table>
        </div>
      </div>

      <div class="card mt-4">
        <div class="card-header"><h2>System Info</h2></div>
        <div class="card-body">
          <p><strong>Current User:</strong> ${esc(currentUser?.username)} ${currentUser?.is_superuser ? '<span class="badge badge-blue">Superuser</span>' : ''}</p>
          <p class="mt-2"><strong>Active Database:</strong> <code>${esc(currentDb)}</code></p>
          <p class="mt-2"><strong>API Base:</strong> <code>${API}</code></p>
          <p class="mt-2"><strong>Swagger UI:</strong> <a href="/docs" target="_blank">/docs</a></p>
        </div>
      </div>
    `;

    // Deliberately NOT awaited: a slow or hung database must not delay the
    // rest of the dashboard from rendering.
    refreshDbStatus();
  } catch (err) {
    area.innerHTML = `<div class="card"><div class="card-body"><p>Error loading dashboard: ${esc(err.message)}</p></div></div>`;
  }
}

function dbStatusHtml(r) {
  // Badge markup only — no title attribute. Driver errors (e.g. psycopg2's
  // `connection to server at "localhost" ...`) routinely contain double
  // quotes, and esc() (textContent->innerHTML) does not escape them, so
  // string-templating the error into a title="..." attribute would close
  // the attribute early. The tooltip is set via the DOM property instead
  // by the caller, which is quote-safe by construction.
  if (r.status === 'online') {
    return `<span class="badge badge-green">Online</span> <small>${esc(String(r.latency_ms))}ms</small>`;
  }
  const badge = r.status === 'timeout'
    ? '<span class="badge badge-amber">Timeout</span>'
    : '<span class="badge badge-red">Offline</span>';
  const err = r.error || '';
  const short = err.length > 60 ? err.slice(0, 60) + '…' : err;
  return `${badge} <small>${esc(short)}</small>`;
}

async function refreshDbStatus() {
  const cell = alias => document.getElementById(`dbstat-${alias}`);
  databases.forEach(d => { const c = cell(d.alias); if (c) { c.textContent = 'Checking…'; c.title = ''; } });

  let rows;
  try {
    rows = await api('GET', '/databases/status');
  } catch (err) {
    // Non-superusers get 403 here. The dashboard stays usable; the column
    // simply reports nothing rather than showing an error.
    databases.forEach(d => { const c = cell(d.alias); if (c) { c.textContent = '—'; c.title = ''; } });
    return;
  }

  rows.forEach(r => {
    const c = cell(r.alias);
    if (c) { c.innerHTML = dbStatusHtml(r); c.title = r.error || ''; }
  });
}

/* ── Users ─────────────────────────────────────────────────────────────── */
async function renderUsers() {
  const area = document.getElementById('contentArea');
  area.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div></div>';

  try {
    const [users, roles] = await Promise.all([
      api('GET', '/auth/users'),
      api('GET', '/auth/roles').catch(() => []),
    ]);
    window._cachedRoles = roles;

    area.innerHTML = `
      <div class="card">
        <div class="card-header">
          <h2>Users (${users.length})</h2>
          <button class="btn btn-primary btn-sm" onclick="openUserModal()">+ New User</button>
        </div>
        <div class="card-body no-pad">
          <table class="data-table"><thead><tr>
            <th>ID</th><th>Username</th><th>Full Name</th><th>Roles</th><th>Status</th><th>Superuser</th><th>Actions</th>
          </tr></thead>
          <tbody>${users.map(u => `<tr>
            <td>${u.id}</td>
            <td><strong>${esc(u.username)}</strong></td>
            <td>${esc(u.full_name || '—')}</td>
            <td>${(u.roles || []).map(r => `<span class="tag">${esc(r.name)}</span>`).join(' ') || '—'}</td>
            <td>${u.is_active ? '<span class="badge badge-green">Active</span>' : '<span class="badge badge-red">Inactive</span>'}</td>
            <td>${u.is_superuser ? '<span class="badge badge-blue">Yes</span>' : '—'}</td>
            <td>
              <button class="btn btn-xs btn-secondary" onclick="openEditUserModal(${u.id})">Edit</button>
              <button class="btn btn-xs btn-danger" onclick="deleteUser(${u.id}, '${esc(u.username)}')">Delete</button>
            </td>
          </tr>`).join('')}</tbody></table>
        </div>
      </div>
    `;
  } catch (err) {
    area.innerHTML = `<div class="card"><div class="card-body"><p>Error: ${esc(err.message)}</p></div></div>`;
  }
}

// Shared handler for the "Service account" checkbox on the user create/edit
// forms: a service account cannot be superuser (enforced server-side too), so
// checking it disables + clears the Superuser select.
window.toggleSuperuserForServiceAccount = function(checkbox) {
  const form = checkbox.form;
  const suField = form.is_superuser;
  if (!suField) return;
  suField.disabled = checkbox.checked;
  if (checkbox.checked) suField.value = 'false';
};

window.openUserModal = function() {
  const roles = window._cachedRoles || [];
  const body = `
    <form id="userForm">
      <div class="form-row">
        <div class="form-group">
          <label>Username</label>
          <input type="text" name="username" required>
        </div>
        <div class="form-group">
          <label>Password</label>
          <input type="password" name="password" required>
        </div>
      </div>
      <div class="form-group">
        <label>Full Name</label>
        <input type="text" name="full_name">
      </div>
      <div class="form-group">
        <label>Superuser</label>
        <select name="is_superuser"><option value="false">No</option><option value="true">Yes</option></select>
      </div>
      <div class="form-group">
        <div class="checkbox-item">
          <input type="checkbox" id="uNewServiceAccount" name="is_service_account" onchange="toggleSuperuserForServiceAccount(this)">
          <label for="uNewServiceAccount">Service account (API-key only; cannot be superuser)</label>
        </div>
      </div>
      <div class="form-group">
        <label>Roles</label>
        <div class="checkbox-list">
          ${roles.map(r => `<div class="checkbox-item">
            <input type="checkbox" id="role_${r.id}" name="role_ids" value="${r.id}">
            <label for="role_${r.id}">${esc(r.name)} <span class="text-muted text-sm">${esc(r.description || '')}</span></label>
          </div>`).join('')}
        </div>
      </div>
    </form>
  `;
  const footer = `<button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                   <button class="btn btn-primary" onclick="submitUser()">Create User</button>`;
  openModal('New User', body, footer);
};

window.submitUser = async function() {
  const form = document.getElementById('userForm');
  const data = {
    username: form.username.value,
    password: form.password.value,
    full_name: form.full_name.value || null,
    is_superuser: form.is_superuser.value === 'true',
    is_service_account: form.is_service_account.checked,
    role_ids: [...form.querySelectorAll('input[name="role_ids"]:checked')].map(c => parseInt(c.value)),
  };
  try {
    await api('POST', '/auth/users', data);
    closeModal();
    toast('User created', 'success');
    renderUsers();
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.openEditUserModal = async function(userId) {
  const roles = window._cachedRoles || [];
  let user;
  try {
    const users = await api('GET', '/auth/users');
    user = users.find(u => u.id === userId);
    if (!user) { toast('User not found', 'error'); return; }
  } catch (err) { toast(err.message, 'error'); return; }

  const userRoleIds = (user.roles || []).map(r => r.id);
  const body = `
    <form id="editUserForm">
      <input type="hidden" name="user_id" value="${user.id}">
      <div class="form-group">
        <label>Username</label>
        <input type="text" value="${esc(user.username)}" disabled>
      </div>
      <div class="form-group">
        <label>Full Name</label>
        <input type="text" name="full_name" value="${esc(user.full_name || '')}">
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Active</label>
          <select name="is_active">
            <option value="true" ${user.is_active ? 'selected' : ''}>Yes</option>
            <option value="false" ${!user.is_active ? 'selected' : ''}>No</option>
          </select>
        </div>
        <div class="form-group">
          <label>Superuser</label>
          <select name="is_superuser" ${user.is_service_account ? 'disabled' : ''}>
            <option value="false" ${!user.is_superuser ? 'selected' : ''}>No</option>
            <option value="true" ${user.is_superuser ? 'selected' : ''}>Yes</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <div class="checkbox-item">
          <input type="checkbox" id="uEditServiceAccount" name="is_service_account" ${user.is_service_account ? 'checked' : ''} onchange="toggleSuperuserForServiceAccount(this)">
          <label for="uEditServiceAccount">Service account (API-key only; cannot be superuser)</label>
        </div>
      </div>
      <div class="form-group">
        <label>Roles</label>
        <div class="checkbox-list">
          ${roles.map(r => `<div class="checkbox-item">
            <input type="checkbox" id="erole_${r.id}" name="role_ids" value="${r.id}" ${userRoleIds.includes(r.id) ? 'checked' : ''}>
            <label for="erole_${r.id}">${esc(r.name)}</label>
          </div>`).join('')}
        </div>
      </div>
      <hr>
      <div class="form-group">
        <label>Reset Password (leave blank to keep current)</label>
        <input type="password" name="new_password" placeholder="New password">
      </div>
    </form>
  `;
  const footer = `<button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                   <button class="btn btn-primary" onclick="submitEditUser()">Save Changes</button>`;
  openModal('Edit User', body, footer);
};

window.submitEditUser = async function() {
  const form = document.getElementById('editUserForm');
  const userId = form.user_id.value;
  const data = {
    full_name: form.full_name.value || null,
    is_active: form.is_active.value === 'true',
    is_superuser: form.is_superuser.value === 'true',
    is_service_account: form.is_service_account.checked,
    role_ids: [...form.querySelectorAll('input[name="role_ids"]:checked')].map(c => parseInt(c.value)),
  };
  try {
    await api('PUT', `/auth/users/${userId}`, data);
    const newPw = form.new_password.value;
    if (newPw) {
      await api('PATCH', `/auth/users/${userId}/password`, { new_password: newPw });
    }
    closeModal();
    toast('User updated', 'success');
    renderUsers();
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.deleteUser = async function(userId, username) {
  if (!confirm(`Deactivate user "${username}"? The user will no longer be able to log in.`)) return;
  try {
    await api('DELETE', `/auth/users/${userId}`);
    toast('User deactivated', 'success');
    renderUsers();
  } catch (err) {
    toast(err.message, 'error');
  }
};

/* ── Roles ─────────────────────────────────────────────────────────────── */
async function renderRoles() {
  const area = document.getElementById('contentArea');
  area.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div></div>';

  try {
    const [roles, perms] = await Promise.all([
      api('GET', '/auth/roles'),
      api('GET', '/auth/permissions').catch(() => []),
    ]);
    window._cachedPerms = perms;

    area.innerHTML = `
      <div class="card">
        <div class="card-header">
          <h2>Roles (${roles.length})</h2>
          <button class="btn btn-primary btn-sm" onclick="openRoleModal()">+ New Role</button>
        </div>
        <div class="card-body no-pad">
          <table class="data-table"><thead><tr>
            <th>ID</th><th>Name</th><th>Description</th><th>Permissions</th><th>Actions</th>
          </tr></thead>
          <tbody>${roles.map(r => `<tr>
            <td>${r.id}</td>
            <td><strong>${esc(r.name)}</strong></td>
            <td>${esc(r.description || '—')}</td>
            <td>${(r.permissions || []).map(p => `<span class="tag">${esc(p.resource)}:${esc(p.action)}</span>`).join(' ') || '—'}</td>
            <td>
              <button class="btn btn-xs btn-secondary" onclick="openEditRoleModal(${r.id})">Edit</button>
              <button class="btn btn-xs btn-danger" onclick="deleteRole(${r.id}, '${esc(r.name)}')">Delete</button>
            </td>
          </tr>`).join('')}</tbody></table>
        </div>
      </div>
    `;
  } catch (err) {
    area.innerHTML = `<div class="card"><div class="card-body"><p>Error: ${esc(err.message)}</p></div></div>`;
  }
}

window.openRoleModal = function() {
  const perms = window._cachedPerms || [];
  const body = `
    <form id="roleForm">
      <div class="form-group"><label>Role Name</label><input type="text" name="name" required></div>
      <div class="form-group"><label>Description</label><textarea name="description" rows="2"></textarea></div>
      <div class="form-group">
        <label>Permissions</label>
        <div class="checkbox-list">
          ${perms.map(p => `<div class="checkbox-item">
            <input type="checkbox" id="perm_${p.id}" name="perm_ids" value="${p.id}">
            <label for="perm_${p.id}">${esc(p.resource)}:${esc(p.action)} <span class="text-muted text-sm">${esc(p.description || '')}</span></label>
          </div>`).join('')}
        </div>
      </div>
    </form>
  `;
  const footer = `<button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                   <button class="btn btn-primary" onclick="submitRole()">Create Role</button>`;
  openModal('New Role', body, footer);
};

window.submitRole = async function() {
  const form = document.getElementById('roleForm');
  const data = {
    name: form.name.value,
    description: form.description.value || null,
    permission_ids: [...form.querySelectorAll('input[name="perm_ids"]:checked')].map(c => parseInt(c.value)),
  };
  try {
    await api('POST', '/auth/roles', data);
    closeModal();
    toast('Role created', 'success');
    renderRoles();
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.openEditRoleModal = async function(roleId) {
  const allPerms = window._cachedPerms || [];
  let role;
  try {
    const roles = await api('GET', '/auth/roles');
    role = roles.find(r => r.id === roleId);
    if (!role) { toast('Role not found', 'error'); return; }
  } catch (err) { toast(err.message, 'error'); return; }

  const rolePermIds = (role.permissions || []).map(p => p.id);
  const body = `
    <form id="editRoleForm">
      <input type="hidden" name="role_id" value="${role.id}">
      <div class="form-group"><label>Role Name</label><input type="text" name="name" value="${esc(role.name)}" required></div>
      <div class="form-group"><label>Description</label><textarea name="description" rows="2">${esc(role.description || '')}</textarea></div>
      <div class="form-group">
        <label>Permissions</label>
        <div class="checkbox-list">
          ${allPerms.map(p => `<div class="checkbox-item">
            <input type="checkbox" id="eperm_${p.id}" name="perm_ids" value="${p.id}" ${rolePermIds.includes(p.id) ? 'checked' : ''}>
            <label for="eperm_${p.id}">${esc(p.resource)}:${esc(p.action)}</label>
          </div>`).join('')}
        </div>
      </div>
    </form>
  `;
  const footer = `<button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                   <button class="btn btn-primary" onclick="submitEditRole()">Save Changes</button>`;
  openModal('Edit Role', body, footer);
};

window.submitEditRole = async function() {
  const form = document.getElementById('editRoleForm');
  const roleId = form.role_id.value;
  const data = {
    name: form.name.value,
    description: form.description.value || null,
    permission_ids: [...form.querySelectorAll('input[name="perm_ids"]:checked')].map(c => parseInt(c.value)),
  };
  try {
    await api('PUT', `/auth/roles/${roleId}`, data);
    closeModal();
    toast('Role updated', 'success');
    renderRoles();
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.deleteRole = async function(roleId, roleName) {
  if (!confirm(`Delete role "${roleName}"? This cannot be undone.`)) return;
  try {
    await api('DELETE', `/auth/roles/${roleId}`);
    toast('Role deleted', 'success');
    renderRoles();
  } catch (err) {
    toast(err.message, 'error');
  }
};

/* ── Permissions ──────────────────────────────────────────────────────── */
async function renderPermissions() {
  const area = document.getElementById('contentArea');
  area.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div></div>';

  try {
    const perms = await api('GET', '/auth/permissions');
    area.innerHTML = `
      <div class="card">
        <div class="card-header">
          <h2>Permissions (${perms.length})</h2>
          <button class="btn btn-primary btn-sm" onclick="openPermModal()">+ New Permission</button>
        </div>
        <div class="card-body no-pad">
          <table class="data-table"><thead><tr>
            <th>ID</th><th>Resource</th><th>Action</th><th>Description</th><th>Actions</th>
          </tr></thead>
          <tbody>${perms.map(p => `<tr>
            <td>${p.id}</td>
            <td><span class="tag">${esc(p.resource)}</span></td>
            <td><span class="badge badge-blue">${esc(p.action)}</span></td>
            <td>${esc(p.description || '—')}</td>
            <td>
              <button class="btn btn-xs btn-secondary" onclick="openEditPermModal(${p.id})">Edit</button>
              <button class="btn btn-xs btn-danger" onclick="deletePerm(${p.id}, '${esc(p.resource)}')">Delete</button>
            </td>
          </tr>`).join('')}</tbody></table>
        </div>
      </div>
    `;
  } catch (err) {
    area.innerHTML = `<div class="card"><div class="card-body"><p>Error: ${esc(err.message)}</p></div></div>`;
  }
}

window.openPermModal = function() {
  const body = `
    <form id="permForm">
      <div class="form-group">
        <label>Resource</label>
        <input type="text" name="resource" placeholder="e.g. query:employees or table:*" required>
        <div class="hint">Format: type:name (supports * wildcard)</div>
      </div>
      <div class="form-group">
        <label>Action</label>
        <select name="action">
          <option value="read">read</option>
          <option value="execute">execute</option>
          <option value="write">write</option>
        </select>
      </div>
      <div class="form-group"><label>Description</label><textarea name="description" rows="2"></textarea></div>
    </form>
  `;
  const footer = `<button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                   <button class="btn btn-primary" onclick="submitPerm()">Create Permission</button>`;
  openModal('New Permission', body, footer);
};

window.submitPerm = async function() {
  const form = document.getElementById('permForm');
  const data = {
    resource: form.resource.value,
    action: form.action.value,
    description: form.description.value || null,
  };
  try {
    await api('POST', '/auth/permissions', data);
    closeModal();
    toast('Permission created', 'success');
    renderPermissions();
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.openEditPermModal = async function(permId) {
  let perm;
  try {
    const perms = await api('GET', '/auth/permissions');
    perm = perms.find(p => p.id === permId);
    if (!perm) { toast('Permission not found', 'error'); return; }
  } catch (err) { toast(err.message, 'error'); return; }

  const body = `
    <form id="editPermForm">
      <input type="hidden" name="perm_id" value="${perm.id}">
      <div class="form-group">
        <label>Resource</label>
        <input type="text" name="resource" value="${esc(perm.resource)}" required>
        <div class="hint">Format: type:name (supports * wildcard)</div>
      </div>
      <div class="form-group">
        <label>Action</label>
        <select name="action">
          <option value="read" ${perm.action === 'read' ? 'selected' : ''}>read</option>
          <option value="execute" ${perm.action === 'execute' ? 'selected' : ''}>execute</option>
          <option value="write" ${perm.action === 'write' ? 'selected' : ''}>write</option>
        </select>
      </div>
      <div class="form-group"><label>Description</label><textarea name="description" rows="2">${esc(perm.description || '')}</textarea></div>
    </form>
  `;
  const footer = `<button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                   <button class="btn btn-primary" onclick="submitEditPerm()">Save Changes</button>`;
  openModal('Edit Permission', body, footer);
};

window.submitEditPerm = async function() {
  const form = document.getElementById('editPermForm');
  const permId = form.perm_id.value;
  const data = {
    resource: form.resource.value,
    action: form.action.value,
    description: form.description.value || null,
  };
  try {
    await api('PUT', `/auth/permissions/${permId}`, data);
    closeModal();
    toast('Permission updated', 'success');
    renderPermissions();
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.deletePerm = async function(permId, resource) {
  if (!confirm(`Delete permission "${resource}"? This cannot be undone.`)) return;
  try {
    await api('DELETE', `/auth/permissions/${permId}`);
    toast('Permission deleted', 'success');
    renderPermissions();
  } catch (err) {
    toast(err.message, 'error');
  }
};

/* ── API Keys ──────────────────────────────────────────────────────────── */
async function renderApiKeys() {
  const area = document.getElementById('contentArea');
  area.innerHTML = `
    <div class="card">
      <div class="card-header">
        <h2>API Keys</h2>
        <button class="btn btn-primary btn-sm" onclick="openApiKeyModal()">+ New API Key</button>
      </div>
      <div class="card-body no-pad" id="apiKeyList"><p class="text-muted" style="padding:16px">Loading…</p></div>
    </div>
    <div id="apiKeyResult"></div>
  `;
  await loadApiKeys();
}

async function loadApiKeys() {
  const el = document.getElementById('apiKeyList');
  try {
    const keys = await api('GET', '/auth/api-keys');
    if (!keys.length) {
      el.innerHTML = '<p class="text-muted" style="padding:16px">No API keys yet.</p>';
      return;
    }
    el.innerHTML = `
      <table class="data-table"><thead><tr>
        <th>Name</th><th>Service Account</th><th>Prefix</th><th>Last Used</th><th>Expires</th><th>Status</th><th>Actions</th>
      </tr></thead>
      <tbody>${keys.map(k => `<tr>
        <td><strong>${esc(k.name)}</strong></td>
        <td>${esc(k.service_account)}</td>
        <td><code>${esc(k.key_prefix)}…</code></td>
        <td>${k.last_used_at ? esc(k.last_used_at) : '—'}</td>
        <td>${k.expires_at ? esc(k.expires_at) : '—'}</td>
        <td>${k.revoked_at ? '<span class="badge badge-red">Revoked</span>' : (k.enabled ? '<span class="badge badge-green">Active</span>' : '<span class="badge badge-red">Disabled</span>')}</td>
        <td>${(k.enabled && !k.revoked_at) ? `<button class="btn btn-xs btn-danger" onclick="revokeApiKey(${k.id}, '${esc(k.name)}')">Revoke</button>` : '—'}</td>
      </tr>`).join('')}</tbody></table>
    `;
  } catch (err) {
    el.innerHTML = `<p style="padding:16px;color:var(--danger)">Error: ${esc(err.message)}</p>`;
  }
}

window.openApiKeyModal = async function() {
  let accounts = [];
  try {
    const users = await api('GET', '/auth/users');
    accounts = users.filter(u => u.is_service_account);
  } catch (err) {
    toast('Failed to load service accounts: ' + err.message, 'error');
  }
  const opts = accounts.length
    ? accounts.map(u => `<option value="${esc(u.username)}">${esc(u.username)}</option>`).join('')
    : '<option value="">(no service accounts — create one under Users first)</option>';

  const body = `
    <form id="apiKeyForm">
      <div class="form-group">
        <label>Service Account</label>
        <select name="service_account_username" ${accounts.length ? 'required' : 'disabled'}>${opts}</select>
      </div>
      <div class="form-group">
        <label>Name</label>
        <input type="text" name="name" placeholder="e.g. hr-portal-prod" required>
      </div>
      <div class="form-group">
        <label>Expires At (optional)</label>
        <input type="date" name="expires_at">
      </div>
      <div class="form-group">
        <label>IP Allowlist (comma-separated CIDR, optional)</label>
        <input type="text" name="ip_allowlist" placeholder="10.1.0.0/16, 192.168.1.0/24">
      </div>
      <div class="form-group">
        <label>Rate Limit / Minute (optional)</label>
        <input type="number" name="rate_limit_per_minute" min="1">
      </div>
    </form>
  `;
  const footer = `<button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                   <button class="btn btn-primary" onclick="submitApiKey()">Create Key</button>`;
  openModal('New API Key', body, footer);
};

window.submitApiKey = async function() {
  const form = document.getElementById('apiKeyForm');
  const ipsRaw = form.ip_allowlist.value.trim();
  const data = {
    service_account_username: form.service_account_username.value,
    name: form.name.value.trim(),
    expires_at: form.expires_at.value || null,
    ip_allowlist: ipsRaw ? ipsRaw.split(',').map(s => s.trim()).filter(Boolean) : null,
    rate_limit_per_minute: form.rate_limit_per_minute.value ? Number(form.rate_limit_per_minute.value) : null,
  };
  if (!data.service_account_username || !data.name) {
    toast('Service account and name are required', 'error');
    return;
  }
  try {
    const created = await api('POST', '/auth/api-keys', data);
    closeModal();
    toast('API key created', 'success');
    document.getElementById('apiKeyResult').innerHTML = `
      <div class="card">
        <div class="card-header"><h2>API Key Created</h2></div>
        <div class="card-body">
          <p style="color:var(--danger)"><strong>Copy this key now — it will not be shown again:</strong></p>
          <pre class="monospace" style="user-select:all;white-space:pre-wrap">${esc(created.api_key)}</pre>
        </div>
      </div>
    `;
    await loadApiKeys();
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.revokeApiKey = async function(id, name) {
  if (!confirm(`Revoke API key "${name}"? Requests using it will start failing immediately.`)) return;
  try {
    await api('DELETE', `/auth/api-keys/${id}`);
    toast('API key revoked', 'success');
    await loadApiKeys();
  } catch (err) {
    toast(err.message, 'error');
  }
};

/* ── SQL Definitions ──────────────────────────────────────────────────── */
async function renderSqlDefs() {
  const area = document.getElementById('contentArea');
  area.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div></div>';

  const isSuperuser = currentUser && currentUser.is_superuser;

  try {
    const defs = await api('GET', `${dbPrefix()}/queries/definitions`);
    area.innerHTML = `
      <div class="card">
        <div class="card-header">
          <h2>SQL Definitions — ${esc(currentDb)} (${defs.length})</h2>
          <div class="btn-group">
            ${isSuperuser ? `
              <button class="btn btn-success btn-sm" onclick="openSqlDefModal('create')">+ New Definition</button>
              <button class="btn btn-info btn-sm" onclick="exportYamlDefs()">Export YAML</button>
              <button class="btn btn-primary btn-sm" onclick="uploadYamlFile()">Upload YAML</button>
              <button class="btn btn-primary btn-sm" onclick="importYamlDefs()">Import YAML</button>
            ` : ''}
            <button class="btn btn-secondary btn-sm" onclick="reloadDefs()">&#8635; Reload</button>
          </div>
        </div>
        <div class="card-body no-pad">
          <table class="data-table"><thead><tr>
            <th>Name</th><th>Description</th><th>Permission</th><th>Parameters</th><th>Actions</th>
          </tr></thead>
          <tbody>${defs.map(d => `<tr>
            <td><strong>${esc(d.name)}</strong></td>
            <td class="text-sm">${esc(d.description || '—')}</td>
            <td><span class="tag">${esc(d.permission)}</span></td>
            <td>${Object.keys(d.params || {}).map(k => {
              const p = d.params[k];
              return `<span class="tag">${esc(k)}${p.required ? '*' : ''}</span>`;
            }).join(' ') || '—'}</td>
            <td>
              <div class="btn-group">
                <button class="btn btn-sm btn-outline" onclick="viewSqlDef('${esc(d.name)}')">Detail</button>
                ${isSuperuser ? `
                  <button class="btn btn-sm btn-primary" onclick="openSqlDefModal('edit','${esc(d.name)}')">Edit</button>
                  <button class="btn btn-sm btn-info" onclick="openSqlDefModal('copy','${esc(d.name)}')">Copy</button>
                  <button class="btn btn-sm btn-danger" onclick="deleteSqlDef('${esc(d.name)}')">Delete</button>
                ` : ''}
              </div>
            </td>
          </tr>`).join('')}</tbody></table>
        </div>
      </div>
    `;
    window._cachedDefs = defs;
  } catch (err) {
    area.innerHTML = `<div class="card"><div class="card-body"><p>Error: ${esc(err.message)}</p></div></div>`;
  }
}

window.reloadDefs = async function() {
  try {
    const res = await api('POST', `${dbPrefix()}/queries/definitions/reload`);
    toast(`Reloaded ${res.count} definitions`, 'success');
    renderSqlDefs();
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.importYamlDefs = async function() {
  if (!confirm('Import all YAML definitions into the database? Existing names will be skipped.')) return;
  try {
    const res = await api('POST', `${dbPrefix()}/queries/definitions/import-yaml`);
    toast(`Imported ${res.imported}, skipped ${res.skipped}${res.errors.length ? ', errors: ' + res.errors.length : ''}`, 'success');
    renderSqlDefs();
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.exportYamlDefs = async function() {
  try {
    const res = await fetch(`${API}/${currentDb}/queries/definitions/export-yaml`, {
      headers: { 'Authorization': `Bearer ${token}` },
    });
    if (!res.ok) throw new Error(`Export failed: ${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = res.headers.get('Content-Disposition')?.match(/filename="(.+)"/)?.[1]
                 || `sql_definitions_${currentDb}.yaml`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast('YAML exported', 'success');
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.uploadYamlFile = async function() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.yaml,.yml';
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    if (!confirm(`Import definitions from "${file.name}" into database "${currentDb}"? Existing names will be skipped.`)) return;
    const form = new FormData();
    form.append('file', file);
    try {
      const res = await fetch(`${API}/${currentDb}/queries/definitions/import-yaml-file`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` },
        body: form,
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `Import failed: ${res.status}`);
      }
      const data = await res.json();
      toast(`Imported ${data.imported}, skipped ${data.skipped}${data.errors.length ? ', errors: ' + data.errors.length : ''}`, 'success');
      renderSqlDefs();
    } catch (err) {
      toast(err.message, 'error');
    }
  };
  input.click();
};

window.openSqlDefModal = async function(mode, name) {
  let data = { name: '', description: '', sql: '', permission: '', params: {} };
  if ((mode === 'edit' || mode === 'copy') && name) {
    try {
      data = await api('GET', `${dbPrefix()}/queries/definitions/${encodeURIComponent(name)}`);
    } catch (err) {
      toast(err.message, 'error');
      return;
    }
  }

  const isEdit = mode === 'edit';
  const isCopy = mode === 'copy';
  if (isCopy) {
    // Copy: prefill source content but force a new, editable name and clear the
    // permission so the create flow auto-generates query:<newname>.
    data = { ...data, name: `${data.name}_copy`, permission: '' };
  }

  const title = isEdit ? `Edit: ${name}` : (isCopy ? `Copy: ${name}` : 'New SQL Definition');
  const paramsJson = Object.keys(data.params || {}).length > 0
    ? JSON.stringify(data.params, null, 2)
    : '{}';

  const body = `
    <div class="form-group">
      <label>Name</label>
      <input type="text" id="sdName" value="${esc(data.name)}" ${isEdit ? 'disabled' : ''} placeholder="e.g. employees">
      ${isEdit ? '' : '<div class="hint">Unique identifier, cannot be changed later</div>'}
    </div>
    <div class="form-group">
      <label>Description</label>
      <input type="text" id="sdDesc" value="${esc(data.description)}" placeholder="Short description">
    </div>
    <div class="form-group">
      <label>Permission</label>
      <input type="text" id="sdPerm" value="${esc(data.permission)}" placeholder="query:name (auto-generated if empty)">
      <div class="hint">Format: query:&lt;name&gt;</div>
    </div>
    <div class="form-group">
      <label>SQL</label>
      <textarea id="sdSql" class="monospace" rows="8" placeholder="SELECT ... FROM ... WHERE ...">${esc(data.sql || '')}</textarea>
    </div>
    <div class="form-group">
      <label>Parameters (JSON)</label>
      <textarea id="sdParams" class="monospace" rows="5" placeholder='{"param": {"type":"string","required":false,"description":"..."}}'>${esc(paramsJson)}</textarea>
      <div class="hint">JSON object: { "param_name": { "type": "string|number|date", "required": false, "description": "..." } }</div>
    </div>
    <div class="form-group">
      <label>Order By</label>
      <div id="sdOrderByRows"></div>
      <button type="button" class="btn btn-xs btn-outline" onclick="addOrderByRow()">+ Add order column</button>
      <div class="hint">Sorts on output column names. Applied by the Hub in the wrapper (works on MSSQL). Empty = no ordering.</div>
    </div>
  `;

  const footer = `
    <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
    <button class="btn btn-primary" onclick="submitSqlDef('${mode}', '${esc(name || '')}')">${isEdit ? 'Update' : 'Create'}</button>
  `;

  openModal(title, body, footer);
  (data.order_by || []).forEach(o => addOrderByRow(o.column, o.direction));
};

// Order By row helpers for the SQL Definition create/edit form.
window.addOrderByRow = function(column = '', direction = 'ASC') {
  const rows = document.getElementById('sdOrderByRows');
  if (!rows) return;
  const div = document.createElement('div');
  div.className = 'order-by-row';
  div.innerHTML = `
    <input type="text" class="ob-col" placeholder="COLUMN_NAME" value="${esc(column)}">
    <select class="ob-dir">
      <option ${direction === 'ASC' ? 'selected' : ''}>ASC</option>
      <option ${direction === 'DESC' ? 'selected' : ''}>DESC</option>
    </select>
    <button type="button" class="btn btn-xs btn-danger" onclick="this.parentElement.remove()">&#10005;</button>
  `;
  rows.appendChild(div);
};

function collectOrderBy() {
  return Array.from(document.querySelectorAll('#sdOrderByRows .order-by-row'))
    .map(r => ({ column: r.querySelector('.ob-col').value.trim(), direction: r.querySelector('.ob-dir').value }))
    .filter(o => o.column);
}

window.submitSqlDef = async function(mode, name) {
  const nameVal = document.getElementById('sdName').value.trim();
  const desc = document.getElementById('sdDesc').value.trim();
  const perm = document.getElementById('sdPerm').value.trim();
  const sql = document.getElementById('sdSql').value;
  const paramsRaw = document.getElementById('sdParams').value.trim();

  if (!nameVal && mode !== 'edit') { toast('Name is required', 'error'); return; }
  if (!sql.trim()) { toast('SQL is required', 'error'); return; }

  let params = {};
  try {
    params = paramsRaw ? JSON.parse(paramsRaw) : {};
  } catch (e) {
    toast('Invalid JSON in Parameters: ' + e.message, 'error');
    return;
  }

  const orderBy = collectOrderBy();

  try {
    if (mode === 'create' || mode === 'copy') {
      await api('POST', `${dbPrefix()}/queries/definitions`, { name: nameVal, description: desc, sql, permission: perm, params, order_by: orderBy });
      toast(`Definition '${nameVal}' created`, 'success');
    } else {
      const body = { description: desc, sql, permission: perm, params, order_by: orderBy };
      await api('PUT', `${dbPrefix()}/queries/definitions/${encodeURIComponent(name)}`, body);
      toast(`Definition '${name}' updated`, 'success');
    }
    closeModal();
    renderSqlDefs();
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.deleteSqlDef = async function(name) {
  if (!confirm(`Delete SQL definition "${name}"? This cannot be undone.`)) return;
  try {
    await api('DELETE', `${dbPrefix()}/queries/definitions/${encodeURIComponent(name)}`);
    toast(`Definition '${name}' deleted`, 'success');
    renderSqlDefs();
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.viewSqlDef = async function(name) {
  let d;
  // Try to get full details (with SQL) for superusers
  if (currentUser && currentUser.is_superuser) {
    try {
      d = await api('GET', `${dbPrefix()}/queries/definitions/${encodeURIComponent(name)}`);
    } catch (_) {
      d = (window._cachedDefs || []).find(x => x.name === name);
    }
  } else {
    d = (window._cachedDefs || []).find(x => x.name === name);
  }
  if (!d) return;

  const body = `
    <div class="form-group"><label>Name</label><input type="text" value="${esc(d.name)}" disabled></div>
    <div class="form-group"><label>Description</label><input type="text" value="${esc(d.description)}" disabled></div>
    <div class="form-group"><label>Permission</label><input type="text" value="${esc(d.permission)}" disabled></div>
    ${d.sql ? `<div class="form-group"><label>SQL</label><pre class="sql-preview">${esc(d.sql)}</pre></div>` : ''}
    <div class="form-group">
      <label>Parameters</label>
      ${Object.keys(d.params || {}).length === 0 ? '<p class="text-muted">No parameters</p>' :
        `<table class="data-table"><thead><tr><th>Name</th><th>Type</th><th>Required</th><th>Description</th></tr></thead>
        <tbody>${Object.entries(d.params).map(([k,v]) => `<tr>
          <td><code>${esc(k)}</code></td><td>${esc(v.type)}</td>
          <td>${v.required ? '<span class="badge badge-red">Yes</span>' : 'No'}</td>
          <td>${esc(v.description || '—')}</td>
        </tr>`).join('')}</tbody></table>`}
    </div>
    <div class="mt-4">
      <button class="btn btn-primary btn-sm" onclick="closeModal();prefillQuery('${esc(d.name)}')">Open in Query Console</button>
    </div>
  `;
  openModal(`SQL Definition: ${d.name}`, body, '');
};

window.prefillQuery = async function(name) {
  navigateTo('query-console');
  // renderQueryConsole kicks off an async load; wait for it, then select.
  await qcLoadDefinitions();
  qcPopulatePicker();
  const sel = document.getElementById('qcName');
  if (sel) {
    sel.value = name;
    qcOnSelectQuery();
  }
};

/* ── Query Console ────────────────────────────────────────────────────── */

// Build an example parameter object from a definition's params map.
// Values are derived from each param's declared type so the operator gets a
// ready-to-edit JSON body. Key order is preserved.
function qcExampleParams(params) {
  const out = {};
  for (const [name, def] of Object.entries(params || {})) {
    const type = def && def.type;
    if (type === 'number') out[name] = 0;
    else if (type === 'date') out[name] = '2024-01-01';
    else out[name] = 'example';  // string + unknown/missing
  }
  return out;
}

// Radio pair that switches the generated examples between a human JWT and a
// service-account API key. Rendered once per code tab; `group` keeps the radio
// names/ids unique, and qcSetAuthMode keeps every group in sync.
function qcAuthToggleHtml(group) {
  return `
    <div class="qc-auth">
      <span class="qc-auth-label">Auth</span>
      <div class="qc-auth-opt">
        <input type="radio" name="qcAuth-${group}" id="qcAuth-${group}-jwt" value="jwt"
               ${qcAuthMode === 'jwt' ? 'checked' : ''} onchange="qcSetAuthMode('jwt')">
        <label for="qcAuth-${group}-jwt">JWT (user login)</label>
      </div>
      <div class="qc-auth-opt">
        <input type="radio" name="qcAuth-${group}" id="qcAuth-${group}-apikey" value="apikey"
               ${qcAuthMode === 'apikey' ? 'checked' : ''} onchange="qcSetAuthMode('apikey')">
        <label for="qcAuth-${group}-apikey">API Key (service account)</label>
      </div>
    </div>`;
}

// Auth header the examples send: X-API-Key for service accounts, Bearer JWT for
// human logins. Both are accepted by AuthContextMiddleware.
function qcAuthHeader() {
  return qcAuthMode === 'apikey'
    ? { name: 'X-API-Key', value: 'ahk_YOUR_API_KEY' }
    : { name: 'Authorization', value: 'Bearer YOUR_ACCESS_TOKEN' };
}

window.qcSetAuthMode = function(mode) {
  qcAuthMode = mode === 'apikey' ? 'apikey' : 'jwt';
  ['py', 'curl'].forEach(group => {
    const el = document.getElementById(`qcAuth-${group}-${qcAuthMode}`);
    if (el) el.checked = true;
  });
  refreshQcPreviews();
};

// Load the current database's SQL definitions for the picker.
async function qcLoadDefinitions() {
  try {
    qcDefs = await api('GET', `${dbPrefix()}/queries/definitions`);
  } catch (e) {
    qcDefs = [];
    toast('Failed to load queries: ' + e.message, 'error');
  }
}

function renderQueryConsole() {
  const area = document.getElementById('contentArea');
  area.innerHTML = `
    <div class="card" id="qcInputCard">
      <div class="card-header" id="qcInputHeader" onclick="qcHeaderClick(event)">
        <h2>Query Console</h2>
        <button class="btn btn-outline btn-sm" id="qcCollapseBtn" onclick="toggleQcCollapse()">&#9650; Collapse</button>
      </div>
      <div class="card-body" id="qcInputBody">
        <div class="tabs">
          <button class="tab-btn active" data-qctab="params" onclick="switchQcTab('params')">Query Parameter</button>
          <button class="tab-btn" data-qctab="body" onclick="switchQcTab('body')">Request Body</button>
          <button class="tab-btn" data-qctab="python" onclick="switchQcTab('python')">Python Example</button>
          <button class="tab-btn" data-qctab="curl" onclick="switchQcTab('curl')">cURL Example</button>
        </div>

        <div class="tab-content active" id="qcTab-params">
          <div class="form-row">
            <div class="form-group">
              <label>Query Name</label>
              <select id="qcName" onchange="qcOnSelectQuery()">
                <option value="">— select a query —</option>
              </select>
            </div>
            <div class="form-group">
              <label>Fetch Mode</label>
              <select id="qcMode" onchange="toggleQcModeFields()">
                <option value="page">page</option>
                <option value="all">all</option>
                <option value="batch">batch</option>
                <option value="top_n">top_n</option>
                <option value="one">one</option>
                <option value="count">count</option>
              </select>
            </div>
          </div>
          <div id="qcModeFields">
            <div class="form-row" id="qcPageFields">
              <div class="form-group"><label>Page</label><input type="number" id="qcPage" value="1" min="1"></div>
              <div class="form-group"><label>Page Size</label><input type="number" id="qcPageSize" value="50" min="1" max="1000"></div>
            </div>
            <div class="form-row hidden" id="qcBatchFields">
              <div class="form-group"><label>Batch Size</label><input type="number" id="qcBatchSize" value="500" min="1" max="10000"></div>
              <div class="form-group"><label>Batch Offset</label><input type="number" id="qcBatchOffset" value="0" min="0"></div>
            </div>
            <div class="form-row hidden" id="qcTopNFields">
              <div class="form-group"><label>Top N</label><input type="number" id="qcTopN" value="10" min="1" max="1000"></div>
            </div>
          </div>
          <div class="form-row">
            <div class="form-group query-console">
              <label>Parameters (JSON)</label>
              <textarea id="qcParams" placeholder='{"dept": "IT"}'>{}</textarea>
            </div>
            <div class="form-group">
              <label>Parameter Reference</label>
              <div class="qc-params-info" id="qcParamsInfo">
                <p class="text-muted text-sm">Select a query to see parameter details.</p>
              </div>
            </div>
          </div>
        </div>

        <div class="tab-content" id="qcTab-body">
          <p class="text-sm text-muted mb-2">The JSON body sent to <code>POST ${esc(API)}${esc(dbPrefix())}/queries/execute</code>:</p>
          <pre class="sql-preview" id="qcBodyPreview"></pre>
        </div>

        <div class="tab-content" id="qcTab-python">
          <div class="qc-code-toolbar">
            ${qcAuthToggleHtml('py')}
            <button class="btn btn-outline btn-sm" onclick="copyQcPython()">&#128203; Copy</button>
          </div>
          <pre class="sql-preview" id="qcPythonPreview"></pre>
        </div>

        <div class="tab-content" id="qcTab-curl">
          <div class="qc-code-toolbar">
            ${qcAuthToggleHtml('curl')}
            <button class="btn btn-outline btn-sm" onclick="copyQcCurl()">&#128203; Copy</button>
          </div>
          <pre class="sql-preview" id="qcCurlPreview"></pre>
        </div>

        <div style="margin-top:16px">
          <button class="btn btn-primary" onclick="executeQuery()">&#9654; Execute Query</button>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><h2>Result</h2></div>
      <div class="card-body">
        <div class="result-area" id="qcResult"><p class="text-muted">No query executed yet.</p></div>
      </div>
    </div>
  `;
  qcLoadDefinitions().then(qcPopulatePicker);
}

// Fill the Query Name picker from qcDefs, keeping a leading placeholder.
function qcPopulatePicker() {
  const sel = document.getElementById('qcName');
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">— select a query —</option>' +
    qcDefs.map(d => `<option value="${esc(d.name)}">${esc(d.name)}</option>`).join('');
  // Restore selection if the name still exists in the new list.
  if (current && qcDefs.some(d => d.name === current)) sel.value = current;
}

// Render the "Parameter Reference" panel next to Parameters (JSON) from the
// selected query's declared params — always the full list, independent of
// whatever the user has typed into the JSON textarea.
function qcRenderParamsInfo(def) {
  const el = document.getElementById('qcParamsInfo');
  if (!el) return;
  const entries = def ? Object.entries(def.params || {}) : [];
  if (entries.length === 0) {
    el.innerHTML = `<p class="text-muted text-sm">${
      def ? 'This query has no parameters.' : 'Select a query to see parameter details.'
    }</p>`;
    return;
  }
  el.innerHTML = entries.map(([name, p]) => `
    <div class="qc-param-item">
      <span class="qc-param-name">${esc(name)}</span>
      <span class="qc-param-meta">${esc(p.type || 'string')} &middot; ${p.required ? 'required' : 'optional'}</span>
      ${p.description ? `<div class="qc-param-desc">${esc(p.description)}</div>` : ''}
    </div>`).join('');
}

// When a query is picked, fill Parameters with type-based examples.
window.qcOnSelectQuery = function() {
  const sel = document.getElementById('qcName');
  const paramsEl = document.getElementById('qcParams');
  if (!sel || !paramsEl) return;
  const def = qcDefs.find(d => d.name === sel.value);
  const example = def ? qcExampleParams(def.params) : {};
  paramsEl.value = JSON.stringify(example, null, 2);
  qcRenderParamsInfo(def);
  refreshQcPreviews();
};

window.toggleQcModeFields = function() {
  const mode = document.getElementById('qcMode').value;
  document.getElementById('qcPageFields').classList.toggle('hidden', mode !== 'page');
  document.getElementById('qcBatchFields').classList.toggle('hidden', mode !== 'batch');
  document.getElementById('qcTopNFields').classList.toggle('hidden', mode !== 'top_n');
};

// Build the request body from the current form state. Returns { body, paramsError }.
function qcBuildBody() {
  const name = document.getElementById('qcName').value.trim();
  let params = {};
  let paramsError = null;
  try { params = JSON.parse(document.getElementById('qcParams').value || '{}'); }
  catch (e) { paramsError = e.message; }

  const mode = document.getElementById('qcMode').value;
  const body = { query_name: name, params, fetch_mode: mode };

  if (mode === 'page') {
    body.page = parseInt(document.getElementById('qcPage').value);
    body.page_size = parseInt(document.getElementById('qcPageSize').value);
  } else if (mode === 'batch') {
    body.batch_size = parseInt(document.getElementById('qcBatchSize').value);
    body.batch_offset = parseInt(document.getElementById('qcBatchOffset').value);
  } else if (mode === 'top_n') {
    body.top_n = parseInt(document.getElementById('qcTopN').value);
  }
  return { body, paramsError };
}

// Serialize a JS value as a Python literal (dict/list/str/bool/None) with 4-space indent.
function toPyLiteral(v, indent) {
  indent = indent || 0;
  const pad = '    '.repeat(indent);
  const pad1 = '    '.repeat(indent + 1);
  if (v === null || v === undefined) return 'None';
  if (typeof v === 'boolean') return v ? 'True' : 'False';
  if (typeof v === 'number') return Number.isNaN(v) ? 'None' : String(v);
  if (typeof v === 'string') return JSON.stringify(v);
  if (Array.isArray(v)) {
    if (v.length === 0) return '[]';
    return '[\n' + v.map(x => pad1 + toPyLiteral(x, indent + 1)).join(',\n') + '\n' + pad + ']';
  }
  if (typeof v === 'object') {
    const keys = Object.keys(v);
    if (keys.length === 0) return '{}';
    return '{\n' + keys.map(k => pad1 + JSON.stringify(k) + ': ' + toPyLiteral(v[k], indent + 1)).join(',\n') + '\n' + pad + '}';
  }
  return 'None';
}

function qcExecuteUrl() {
  return `${window.location.origin}${API}${dbPrefix()}/queries/execute`;
}

function qcPythonCode(body) {
  const auth = qcAuthHeader();
  return `import requests

url = "${qcExecuteUrl()}"
headers = {
    ${JSON.stringify(auth.name)}: ${JSON.stringify(auth.value)},
    "Content-Type": "application/json",
}
payload = ${toPyLiteral(body, 0)}

response = requests.post(url, json=payload, headers=headers)
response.raise_for_status()
result = response.json()
print(result)`;
}

// Wrap a string in single quotes for POSIX shells: end the quote, emit an
// escaped quote, reopen. Params can contain apostrophes.
function toShellSingleQuoted(s) {
  return `'${String(s).replace(/'/g, `'\\''`)}'`;
}

function qcCurlCode(body) {
  const auth = qcAuthHeader();
  return `curl -X POST "${qcExecuteUrl()}" \\
  -H "${auth.name}: ${auth.value}" \\
  -H "Content-Type: application/json" \\
  -d ${toShellSingleQuoted(JSON.stringify(body, null, 2))}`;
}

// Refresh the Request Body, Python and cURL previews from the live form.
function refreshQcPreviews() {
  const { body, paramsError } = qcBuildBody();
  const bodyPre = document.getElementById('qcBodyPreview');
  const pyPre = document.getElementById('qcPythonPreview');
  const curlPre = document.getElementById('qcCurlPreview');
  if (!bodyPre || !pyPre || !curlPre) return;

  if (paramsError) {
    const msg = `// Invalid Parameters JSON: ${esc(paramsError)}`;
    const shellMsg = `<span style="color:var(--red,#dc2626)"># Invalid Parameters JSON: ${esc(paramsError)}</span>`;
    bodyPre.innerHTML = `<span style="color:var(--red,#dc2626)">${msg}</span>`;
    pyPre.innerHTML = shellMsg;
    curlPre.innerHTML = shellMsg;
    window._qcPythonCode = '';
    window._qcCurlCode = '';
    return;
  }
  bodyPre.innerHTML = syntaxHighlight(JSON.stringify(body, null, 2));
  const code = qcPythonCode(body);
  window._qcPythonCode = code;
  pyPre.textContent = code;
  const curl = qcCurlCode(body);
  window._qcCurlCode = curl;
  curlPre.textContent = curl;
}

window.switchQcTab = function(tab) {
  document.querySelectorAll('#qcInputBody .tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.qctab === tab));
  document.querySelectorAll('#qcInputBody .tab-content').forEach(c =>
    c.classList.toggle('active', c.id === `qcTab-${tab}`));
  if (tab === 'body' || tab === 'python' || tab === 'curl') refreshQcPreviews();
};

function collapseQcInput(collapsed) {
  const bodyEl = document.getElementById('qcInputBody');
  const btn = document.getElementById('qcCollapseBtn');
  const header = document.getElementById('qcInputHeader');
  if (!bodyEl || !btn) return;
  bodyEl.classList.toggle('hidden', collapsed);
  btn.innerHTML = collapsed ? '&#9660; Expand' : '&#9650; Collapse';
  if (header) header.classList.toggle('is-collapsed', collapsed);
}

window.toggleQcCollapse = function() {
  const collapsed = document.getElementById('qcInputBody').classList.contains('hidden');
  collapseQcInput(!collapsed);
};

// While collapsed, clicking anywhere on the header re-expands it (the button remains
// a valid target too, but no longer the only way in). Once expanded, only the button
// collapses it again — clicking the header shouldn't fight with clicks on its title.
window.qcHeaderClick = function(event) {
  if (event.target.closest('#qcCollapseBtn')) return;
  const bodyEl = document.getElementById('qcInputBody');
  if (bodyEl && bodyEl.classList.contains('hidden')) {
    collapseQcInput(false);
  }
};

async function qcCopyCode(code, label) {
  if (!code) return toast('Nothing to copy', 'error');
  try {
    await navigator.clipboard.writeText(code);
    toast(`${label} example copied`, 'success');
  } catch {
    toast('Copy failed — select and copy manually', 'error');
  }
}

window.copyQcPython = function() {
  return qcCopyCode(window._qcPythonCode || '', 'Python');
};

window.copyQcCurl = function() {
  return qcCopyCode(window._qcCurlCode || '', 'cURL');
};

window.executeQuery = async function() {
  const { body, paramsError } = qcBuildBody();
  if (!body.query_name) return toast('Please enter a query name', 'error');
  if (paramsError) return toast('Invalid JSON parameters', 'error');

  refreshQcPreviews();

  const resultArea = document.getElementById('qcResult');
  resultArea.innerHTML = '<div class="spinner"></div>';

  try {
    const result = await api('POST', `${dbPrefix()}/queries/execute`, body);
    const meta = { ...result };
    delete meta.data;
    delete meta.columns;

    resultArea.innerHTML = `
      <div class="mb-4">
        <span class="badge badge-green">SUCCESS</span>
        <span class="text-muted text-sm" style="margin-left:8px">Total: ${result.total} rows | Returned: ${result.data.length} rows</span>
      </div>
      ${result.data.length > 0 ? `
        <div style="overflow-x:auto">
          <table class="data-table"><thead><tr>${result.columns.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
          <tbody>${result.data.map(row => `<tr>${result.columns.map(c => `<td>${esc(row[c])}</td>`).join('')}</tr>`).join('')}</tbody></table>
        </div>
      ` : '<p class="text-muted">No data returned</p>'}
      <details class="mt-4"><summary class="text-sm" style="cursor:pointer">Raw JSON Response</summary>
        ${rawJsonBlock('qcResultJson', result)}
      </details>
    `;
    collapseQcInput(true);
  } catch (err) {
    resultArea.innerHTML = `<div class="mt-2"><span class="badge badge-red">ERROR</span> <span class="text-sm">${esc(err.message)}</span></div>`;
  }
};

/* ── Procedure Console ────────────────────────────────────────────────── */
function renderProcConsole() {
  const area = document.getElementById('contentArea');
  area.innerHTML = `
    <div class="card">
      <div class="card-header"><h2>Procedure / Function Console</h2></div>
      <div class="card-body">
        <div class="form-row">
          <div class="form-group">
            <label>Procedure/Function Name</label>
            <input type="text" id="pcName" placeholder="e.g. PKG_HR.GET_SALARY">
          </div>
          <div class="form-group">
            <label>Type</label>
            <select id="pcIsFunc">
              <option value="false">Procedure</option>
              <option value="true">Function</option>
            </select>
          </div>
        </div>
        <div class="form-group query-console">
          <label>Input Parameters (JSON)</label>
          <textarea id="pcParams" placeholder='{"p_emp_id": 1001}'>{}</textarea>
        </div>
        <div class="form-group">
          <label>OUT Parameters (comma-separated names)</label>
          <input type="text" id="pcOutParams" placeholder="p_salary, p_currency">
        </div>
        <div class="form-group">
          <label><input type="checkbox" id="pcAsync"> Execute Asynchronously (Celery)</label>
        </div>
        <button class="btn btn-primary" onclick="executeProcedure()">&#9654; Execute</button>
        <div class="result-area" id="pcResult"></div>
      </div>
    </div>
  `;
}

window.executeProcedure = async function() {
  const name = document.getElementById('pcName').value.trim();
  if (!name) return toast('Please enter a procedure name', 'error');

  let params = {};
  try { params = JSON.parse(document.getElementById('pcParams').value || '{}'); } catch { return toast('Invalid JSON', 'error'); }

  const outRaw = document.getElementById('pcOutParams').value.trim();
  const outParams = outRaw ? outRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
  const isFunction = document.getElementById('pcIsFunc').value === 'true';
  const isAsync = document.getElementById('pcAsync').checked;

  const body = { name, params, out_params: outParams, is_function: isFunction };
  const endpoint = isAsync ? `${dbPrefix()}/procedures/execute-async` : `${dbPrefix()}/procedures/execute`;
  const resultArea = document.getElementById('pcResult');
  resultArea.innerHTML = '<div class="spinner"></div>';

  try {
    const result = await api('POST', endpoint, body);
    resultArea.innerHTML = `
      <div class="mb-4"><span class="badge badge-green">SUCCESS</span></div>
      ${rawJsonBlock('pcResultJson', result)}
    `;
  } catch (err) {
    resultArea.innerHTML = `<div class="mt-2"><span class="badge badge-red">ERROR</span> <span class="text-sm">${esc(err.message)}</span></div>`;
  }
};

/* ── Write Console ────────────────────────────────────────────────────── */
function renderWriteConsole() {
  const area = document.getElementById('contentArea');
  area.innerHTML = `
    <div class="card">
      <div class="card-header"><h2>Write Console</h2></div>
      <div class="card-body">
        <div class="form-row">
          <div class="form-group">
            <label>Operation</label>
            <select id="wcOp">
              <option value="insert">INSERT</option>
              <option value="update">UPDATE</option>
              <option value="delete">DELETE</option>
            </select>
          </div>
          <div class="form-group">
            <label>Table Name</label>
            <input type="text" id="wcTable" placeholder="e.g. HR.EMPLOYEES">
          </div>
        </div>
        <div class="form-group query-console">
          <label>Data (JSON array of objects)</label>
          <textarea id="wcData" placeholder='[{"emp_name": "John", "department": "IT"}]'>[]</textarea>
        </div>
        <div class="form-group query-console">
          <label>WHERE Clause Params (JSON, for UPDATE/DELETE)</label>
          <textarea id="wcWhere" rows="2" placeholder='{"emp_id": 1001}'></textarea>
        </div>
        <div class="form-group">
          <label><input type="checkbox" id="wcAsync"> Execute Asynchronously (Celery)</label>
        </div>
        <button class="btn btn-primary" onclick="executeWrite()">&#9654; Execute Write</button>
        <div class="result-area" id="wcResult"></div>
      </div>
    </div>
  `;
}

window.executeWrite = async function() {
  const table = document.getElementById('wcTable').value.trim();
  if (!table) return toast('Please enter a table name', 'error');

  let data = [];
  try { data = JSON.parse(document.getElementById('wcData').value || '[]'); } catch { return toast('Invalid JSON data', 'error'); }

  let where = null;
  const whereRaw = document.getElementById('wcWhere').value.trim();
  if (whereRaw) {
    try { where = JSON.parse(whereRaw); } catch { return toast('Invalid WHERE JSON', 'error'); }
  }

  const body = {
    operation: document.getElementById('wcOp').value,
    table,
    data,
    where,
    async_mode: document.getElementById('wcAsync').checked,
  };

  const resultArea = document.getElementById('wcResult');
  resultArea.innerHTML = '<div class="spinner"></div>';

  try {
    const result = await api('POST', `${dbPrefix()}/writes/execute`, body);
    resultArea.innerHTML = `
      <div class="mb-4"><span class="badge badge-green">SUCCESS</span></div>
      ${rawJsonBlock('wcResultJson', result)}
    `;
  } catch (err) {
    resultArea.innerHTML = `<div class="mt-2"><span class="badge badge-red">ERROR</span> <span class="text-sm">${esc(err.message)}</span></div>`;
  }
};

/* ── Tasks ─────────────────────────────────────────────────────────────── */
function renderTasks() {
  const area = document.getElementById('contentArea');
  area.innerHTML = `
    <div class="card">
      <div class="card-header"><h2>Async Task Status</h2></div>
      <div class="card-body">
        <div class="form-group">
          <label>Task ID</label>
          <div class="flex gap-2">
            <input type="text" id="taskIdInput" placeholder="Enter Celery task ID" style="flex:1">
            <button class="btn btn-primary" onclick="checkTask()">Check</button>
          </div>
        </div>
        <div class="result-area" id="taskResult"></div>
      </div>
    </div>
    <div class="card">
      <div class="card-header">
        <h2>Flower Dashboard</h2>
      </div>
      <div class="card-body">
        <p>For full task monitoring, open the Flower dashboard:</p>
        <a href="http://localhost:5555" target="_blank" class="btn btn-outline mt-2">Open Flower (port 5555)</a>
      </div>
    </div>
  `;
}

window.checkTask = async function() {
  const taskId = document.getElementById('taskIdInput').value.trim();
  if (!taskId) return toast('Please enter a task ID', 'error');

  const resultArea = document.getElementById('taskResult');
  resultArea.innerHTML = '<div class="spinner"></div>';

  try {
    const result = await api('GET', `/tasks/${encodeURIComponent(taskId)}`);
    const statusBadge = {
      SUCCESS: 'badge-green', FAILURE: 'badge-red', PENDING: 'badge-yellow',
      STARTED: 'badge-blue', RETRY: 'badge-yellow',
    }[result.status] || 'badge-gray';

    resultArea.innerHTML = `
      <div class="mb-4">
        <span class="badge ${statusBadge}">${esc(result.status)}</span>
        <span class="text-muted text-sm" style="margin-left:8px">Task: ${esc(result.task_id)}</span>
      </div>
      ${rawJsonBlock('taskResultJson', result)}
    `;
  } catch (err) {
    resultArea.innerHTML = `<div class="mt-2"><span class="badge badge-red">ERROR</span> <span class="text-sm">${esc(err.message)}</span></div>`;
  }
};

/* ══════════════════════════════════════════════════════════════════════════
   Data Maintenance — schema-driven write UI with transaction support
   ══════════════════════════════════════════════════════════════════════════ */

let _dmSchema = null;   // current table schema
let _dmRows = [];       // rows being edited
let _dmRowCounter = 0;

function renderDataMaintenance() {
  _dmSchema = null;
  _dmRows = [];
  _dmRowCounter = 0;

  const area = document.getElementById('contentArea');
  area.innerHTML = `
    <div class="card">
      <div class="card-header"><h2>Data Maintenance</h2></div>
      <div class="card-body">
        <p class="text-sm text-muted mb-4">
          Enter a table name to load its schema. The system reads column definitions from Oracle and
          generates a dynamic form. Supports single-row and batch operations with full transaction control.
        </p>
        <div class="form-group">
          <label>Table Name</label>
          <div class="flex gap-2">
            <input type="text" id="dmTable" placeholder="e.g. HR.EMPLOYEES" style="flex:1">
            <button class="btn btn-primary" onclick="dmLoadSchema()">Load Schema</button>
          </div>
        </div>
        <div id="dmSchemaArea"></div>
        <div id="dmEditorArea"></div>
        <div id="dmResultArea"></div>
      </div>
    </div>
  `;
}

window.dmLoadSchema = async function() {
  const table = document.getElementById('dmTable').value.trim();
  if (!table) return toast('Please enter a table name', 'error');

  const schemaArea = document.getElementById('dmSchemaArea');
  const editorArea = document.getElementById('dmEditorArea');
  const resultArea = document.getElementById('dmResultArea');
  schemaArea.innerHTML = '<div class="spinner"></div>';
  editorArea.innerHTML = '';
  resultArea.innerHTML = '';

  try {
    _dmSchema = await api('GET', `${dbPrefix()}/schemas/describe/${encodeURIComponent(table)}`);
    _dmRows = [];
    _dmRowCounter = 0;

    // Render schema info
    schemaArea.innerHTML = `
      <div class="card mt-4">
        <div class="card-header">
          <h2>Schema: ${esc(_dmSchema.table)}</h2>
          <span class="text-sm text-muted">${_dmSchema.column_count} columns${_dmSchema.primary_keys.length ? ' | PK: ' + _dmSchema.primary_keys.join(', ') : ''}</span>
        </div>
        <div class="card-body">
          <div class="dm-schema-info">
            ${_dmSchema.columns.map(c => `
              <span class="col-tag ${c.is_pk ? 'pk' : ''}">
                ${c.is_pk ? '<span class="dm-field"><label><span class="pk-badge">PK</span></label></span>' : ''}
                <strong>${esc(c.name)}</strong>
                <span class="text-muted">${esc(c.data_type)}${c.data_precision ? '(' + c.data_precision + (c.data_scale ? ',' + c.data_scale : '') + ')' : c.data_length && c.data_type === 'VARCHAR2' ? '(' + c.data_length + ')' : ''}</span>
                ${!c.nullable ? '<span style="color:var(--danger)">*</span>' : ''}
              </span>
            `).join('')}
          </div>
        </div>
      </div>
    `;

    // Render the editor
    dmRenderEditor();
  } catch (err) {
    schemaArea.innerHTML = `<div class="mt-2"><span class="badge badge-red">ERROR</span> <span class="text-sm">${esc(err.message)}</span></div>`;
  }
};

function dmRenderEditor() {
  const editorArea = document.getElementById('dmEditorArea');

  editorArea.innerHTML = `
    <div class="card mt-4">
      <div class="card-header">
        <h2>Operations</h2>
        <div class="btn-group">
          <button class="btn btn-sm btn-primary" onclick="dmAddRow('insert')">+ Insert Row</button>
          <button class="btn btn-sm btn-secondary" onclick="dmAddRow('update')">+ Update Row</button>
          <button class="btn btn-sm btn-danger" onclick="dmAddRow('delete')">+ Delete Row</button>
        </div>
      </div>
      <div class="card-body">
        <div id="dmRowsContainer">
          ${_dmRows.length === 0 ? '<div class="empty-state"><div class="icon">&#9998;</div><p>Click a button above to add an operation</p></div>' : ''}
        </div>
        ${_dmRows.length > 0 ? `
          <div class="dm-toolbar mt-4">
            <button class="btn btn-success" onclick="dmExecute(false)">&#9654; Execute (Transaction)</button>
            <button class="btn btn-outline" onclick="dmExecute(true)">&#8987; Execute Async</button>
            <span class="text-sm text-muted">${_dmRows.length} operation(s) — all wrapped in one transaction</span>
          </div>
        ` : ''}
      </div>
    </div>
  `;

  // Re-render existing row cards
  const container = document.getElementById('dmRowsContainer');
  if (_dmRows.length > 0) {
    container.innerHTML = _dmRows.map((row, idx) => dmRenderRowCard(row, idx)).join('');
  }
}

window.dmAddRow = function(operation) {
  const id = _dmRowCounter++;
  _dmRows.push({ id, operation, values: {}, where: {} });
  dmRenderEditor();
};

window.dmRemoveRow = function(idx) {
  _dmRows.splice(idx, 1);
  dmRenderEditor();
};

function dmRenderRowCard(row, idx) {
  const cols = _dmSchema.columns;
  const opColors = { insert: 'badge-green', update: 'badge-blue', delete: 'badge-red' };
  const opLabels = { insert: 'INSERT', update: 'UPDATE', delete: 'DELETE' };
  const showData = row.operation !== 'delete';
  const showWhere = row.operation !== 'insert';

  let html = `<div class="dm-row-card">
    <div class="row-header">
      <span class="row-label"><span class="badge ${opColors[row.operation]}">${opLabels[row.operation]}</span> Operation #${idx + 1}</span>
      <button class="btn btn-sm btn-danger" onclick="dmRemoveRow(${idx})">&times; Remove</button>
    </div>`;

  if (showData) {
    const label = row.operation === 'insert' ? 'Column Values' : 'SET Values';
    html += `<p class="text-sm text-muted mb-4">${label}</p><div class="dm-fields-grid">`;
    for (const col of cols) {
      const val = row.values[col.name] || '';
      html += `<div class="dm-field">
        <label>
          ${col.is_pk ? '<span class="pk-badge">PK </span>' : ''}${esc(col.name)}
          <span class="type-hint">${esc(col.data_type)}</span>
          ${!col.nullable ? '<span class="req-star">*</span>' : ''}
        </label>
        <input type="text" data-idx="${idx}" data-target="values" data-col="${esc(col.name)}"
               value="${esc(val)}" onchange="dmFieldChange(this)"
               placeholder="${col.nullable ? 'NULL' : 'required'}">
      </div>`;
    }
    html += `</div>`;
  }

  if (showWhere) {
    const pkCols = _dmSchema.primary_keys.length > 0 ? _dmSchema.primary_keys : cols.map(c => c.name);
    html += `<p class="text-sm text-muted mt-4 mb-4">WHERE Conditions (identify target rows)</p><div class="dm-fields-grid">`;
    for (const pkName of pkCols) {
      const col = cols.find(c => c.name === pkName);
      if (!col) continue;
      const val = row.where[col.name] || '';
      html += `<div class="dm-field">
        <label>
          ${col.is_pk ? '<span class="pk-badge">PK </span>' : ''}${esc(col.name)}
          <span class="type-hint">${esc(col.data_type)}</span>
        </label>
        <input type="text" data-idx="${idx}" data-target="where" data-col="${esc(col.name)}"
               value="${esc(val)}" onchange="dmFieldChange(this)" placeholder="value">
      </div>`;
    }
    html += `</div>`;
  }

  html += `</div>`;
  return html;
}

window.dmFieldChange = function(input) {
  const idx = parseInt(input.dataset.idx);
  const target = input.dataset.target; // "values" or "where"
  const col = input.dataset.col;
  const val = input.value;
  if (_dmRows[idx]) {
    _dmRows[idx][target][col] = val;
  }
};

window.dmExecute = async function(asyncMode) {
  if (!_dmSchema || _dmRows.length === 0) return toast('No operations to execute', 'error');

  // Build operations payload
  const operations = _dmRows.map(row => {
    const op = { operation: row.operation };

    if (row.operation !== 'delete') {
      // Build data array — filter out empty values
      const dataObj = {};
      for (const [k, v] of Object.entries(row.values)) {
        if (v !== '') dataObj[k] = _castValue(k, v);
      }
      op.data = [dataObj];
    } else {
      op.data = [];
    }

    if (row.operation !== 'insert') {
      const whereObj = {};
      for (const [k, v] of Object.entries(row.where)) {
        if (v !== '') whereObj[k] = _castValue(k, v);
      }
      if (Object.keys(whereObj).length > 0) op.where = whereObj;
    }

    return op;
  });

  const body = {
    table: _dmSchema.table,
    operations,
    async_mode: asyncMode,
  };

  const resultArea = document.getElementById('dmResultArea');
  resultArea.innerHTML = '<div style="text-align:center;padding:20px"><div class="spinner"></div></div>';

  try {
    const result = await api('POST', `${dbPrefix()}/writes/batch`, body);
    if (result.task_id) {
      resultArea.innerHTML = `
        <div class="card mt-4">
          <div class="card-header"><h2>Result</h2></div>
          <div class="card-body">
            <span class="badge badge-yellow">ASYNC</span>
            <span class="text-sm" style="margin-left:8px">Task ID: <code>${esc(result.task_id)}</code></span>
            <p class="mt-2 text-sm text-muted">${esc(result.message)}</p>
          </div>
        </div>
      `;
    } else {
      resultArea.innerHTML = `
        <div class="card mt-4">
          <div class="card-header"><h2>Result</h2></div>
          <div class="card-body">
            <span class="badge badge-green">COMMITTED</span>
            <span class="text-sm" style="margin-left:8px">${result.total_affected} rows affected across ${result.operation_count} operation(s)</span>
            <div class="dm-batch-summary mt-4">
              <table class="data-table">
                <thead><tr><th>#</th><th>Operation</th><th>Affected Rows</th></tr></thead>
                <tbody>${result.results.map(r => `<tr>
                  <td>${r.index + 1}</td>
                  <td><span class="badge badge-blue">${esc(r.operation.toUpperCase())}</span></td>
                  <td>${r.affected_rows}</td>
                </tr>`).join('')}</tbody>
              </table>
            </div>
            <details class="mt-4"><summary class="text-sm" style="cursor:pointer">Raw JSON</summary>
              <pre>${syntaxHighlight(JSON.stringify(result, null, 2))}</pre>
            </details>
          </div>
        </div>
      `;
    }
    toast('Transaction executed successfully', 'success');
  } catch (err) {
    let detail = err.message;
    try { detail = JSON.stringify(JSON.parse(err.message), null, 2); } catch {}
    resultArea.innerHTML = `
      <div class="card mt-4">
        <div class="card-header"><h2>Result</h2></div>
        <div class="card-body">
          <span class="badge badge-red">ROLLED BACK</span>
          <span class="text-sm" style="margin-left:8px">Transaction failed — no data was committed</span>
          <pre class="mt-4">${esc(detail)}</pre>
        </div>
      </div>
    `;
    toast('Transaction failed and was rolled back', 'error');
  }
};

function _castValue(colName, value) {
  if (!_dmSchema) return value;
  const col = _dmSchema.columns.find(c => c.name === colName);
  if (!col) return value;
  if (col.data_type === 'NUMBER' && value !== '') {
    const n = Number(value);
    if (!isNaN(n)) return n;
  }
  return value;
}
