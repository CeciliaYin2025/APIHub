/**
 * Oracle API Hub — Admin SPA
 *
 * Single-page management console communicating with the FastAPI backend.
 */

/* ── Global State ─────────────────────────────────────────────────────── */
const API = '/api/v1';
let token = localStorage.getItem('hub_token') || '';
let currentUser = null;
let currentDb = localStorage.getItem('hub_current_db') || '';
let databases = [];

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
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
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
  document.getElementById('modalOverlay').addEventListener('click', e => {
    if (e.target === e.currentTarget) closeModal();
  });
  document.getElementById('modalClose').addEventListener('click', closeModal);
}

function navigateTo(page) {
  document.querySelectorAll('.sidebar-nav a').forEach(a => a.classList.remove('active'));
  const link = document.querySelector(`.sidebar-nav a[data-page="${page}"]`);
  if (link) link.classList.add('active');

  const titles = {
    dashboard: 'Dashboard', users: 'User Management', roles: 'Role Management',
    permissions: 'Permission Management', 'sql-definitions': 'SQL Definitions',
    'query-console': 'Query Console', 'procedure-console': 'Procedure Console',
    'write-console': 'Write Console', 'data-maintenance': 'Data Maintenance',
    tasks: 'Async Tasks',
  };
  document.getElementById('pageTitle').textContent = titles[page] || page;
  const renderers = {
    dashboard: renderDashboard, users: renderUsers, roles: renderRoles,
    permissions: renderPermissions, 'sql-definitions': renderSqlDefs,
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
        <div class="card-header"><h2>Databases (${databases.length})</h2></div>
        <div class="card-body no-pad">
          <table class="data-table"><thead><tr><th>Alias</th><th>Type</th><th>Description</th><th>Connection</th><th>User</th></tr></thead>
          <tbody>${databases.map(d => `<tr>
            <td><strong>${esc(d.alias)}</strong>${d.alias === currentDb ? ' <span class="badge badge-blue">Active</span>' : ''}</td>
            <td><span class="badge ${d.db_type === 'oracle' ? 'badge-blue' : d.db_type === 'postgresql' ? 'badge-green' : 'badge-amber'}">${esc((d.db_type || 'oracle').toUpperCase())}</span></td>
            <td>${esc(d.description || '—')}</td>
            <td><code>${esc(d.dsn || (d.host ? d.host + ':' + d.port + '/' + d.database : '—'))}</code></td>
            <td>${esc(d.user)}</td>
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
  } catch (err) {
    area.innerHTML = `<div class="card"><div class="card-body"><p>Error loading dashboard: ${esc(err.message)}</p></div></div>`;
  }
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
          <select name="is_superuser">
            <option value="false" ${!user.is_superuser ? 'selected' : ''}>No</option>
            <option value="true" ${user.is_superuser ? 'selected' : ''}>Yes</option>
          </select>
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
            <th>ID</th><th>Name</th><th>Description</th><th>Permissions</th>
          </tr></thead>
          <tbody>${roles.map(r => `<tr>
            <td>${r.id}</td>
            <td><strong>${esc(r.name)}</strong></td>
            <td>${esc(r.description || '—')}</td>
            <td>${(r.permissions || []).map(p => `<span class="tag">${esc(p.resource)}:${esc(p.action)}</span>`).join(' ') || '—'}</td>
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
            <th>ID</th><th>Resource</th><th>Action</th><th>Description</th>
          </tr></thead>
          <tbody>${perms.map(p => `<tr>
            <td>${p.id}</td>
            <td><span class="tag">${esc(p.resource)}</span></td>
            <td><span class="badge badge-blue">${esc(p.action)}</span></td>
            <td>${esc(p.description || '—')}</td>
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
  if (mode === 'edit' && name) {
    try {
      data = await api('GET', `${dbPrefix()}/queries/definitions/${encodeURIComponent(name)}`);
    } catch (err) {
      toast(err.message, 'error');
      return;
    }
  }

  const isEdit = mode === 'edit';
  const title = isEdit ? `Edit: ${name}` : 'New SQL Definition';
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
  `;

  const footer = `
    <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
    <button class="btn btn-primary" onclick="submitSqlDef('${mode}', '${esc(name || '')}')">${isEdit ? 'Update' : 'Create'}</button>
  `;

  openModal(title, body, footer);
};

window.submitSqlDef = async function(mode, name) {
  const nameVal = document.getElementById('sdName').value.trim();
  const desc = document.getElementById('sdDesc').value.trim();
  const perm = document.getElementById('sdPerm').value.trim();
  const sql = document.getElementById('sdSql').value;
  const paramsRaw = document.getElementById('sdParams').value.trim();

  if (!nameVal && mode === 'create') { toast('Name is required', 'error'); return; }
  if (!sql.trim()) { toast('SQL is required', 'error'); return; }

  let params = {};
  try {
    params = paramsRaw ? JSON.parse(paramsRaw) : {};
  } catch (e) {
    toast('Invalid JSON in Parameters: ' + e.message, 'error');
    return;
  }

  try {
    if (mode === 'create') {
      await api('POST', `${dbPrefix()}/queries/definitions`, { name: nameVal, description: desc, sql, permission: perm, params });
      toast(`Definition '${nameVal}' created`, 'success');
    } else {
      const body = { description: desc, sql, permission: perm, params };
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

window.prefillQuery = function(name) {
  navigateTo('query-console');
  setTimeout(() => {
    const nameInput = document.getElementById('qcName');
    if (nameInput) nameInput.value = name;
  }, 100);
};

/* ── Query Console ────────────────────────────────────────────────────── */
function renderQueryConsole() {
  const area = document.getElementById('contentArea');
  area.innerHTML = `
    <div class="card">
      <div class="card-header"><h2>Query Console</h2></div>
      <div class="card-body">
        <div class="form-row">
          <div class="form-group">
            <label>Query Name</label>
            <input type="text" id="qcName" placeholder="e.g. employees">
          </div>
          <div class="form-group">
            <label>Fetch Mode</label>
            <select id="qcMode" onchange="toggleQcModeFields()">
              <option value="page">page</option>
              <option value="all">all</option>
              <option value="batch">batch</option>
              <option value="top_n">top_n</option>
              <option value="one">one</option>
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
        <div class="form-group query-console">
          <label>Parameters (JSON)</label>
          <textarea id="qcParams" placeholder='{"dept": "IT"}'>{}</textarea>
        </div>
        <button class="btn btn-primary" onclick="executeQuery()">&#9654; Execute Query</button>
        <div class="result-area" id="qcResult"></div>
      </div>
    </div>
  `;
}

window.toggleQcModeFields = function() {
  const mode = document.getElementById('qcMode').value;
  document.getElementById('qcPageFields').classList.toggle('hidden', mode !== 'page');
  document.getElementById('qcBatchFields').classList.toggle('hidden', mode !== 'batch');
  document.getElementById('qcTopNFields').classList.toggle('hidden', mode !== 'top_n');
};

window.executeQuery = async function() {
  const name = document.getElementById('qcName').value.trim();
  if (!name) return toast('Please enter a query name', 'error');

  let params = {};
  try { params = JSON.parse(document.getElementById('qcParams').value || '{}'); } catch { return toast('Invalid JSON parameters', 'error'); }

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
        <pre>${syntaxHighlight(JSON.stringify(result, null, 2))}</pre>
      </details>
    `;
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
      <pre>${syntaxHighlight(JSON.stringify(result, null, 2))}</pre>
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
      <pre>${syntaxHighlight(JSON.stringify(result, null, 2))}</pre>
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
      <pre>${syntaxHighlight(JSON.stringify(result, null, 2))}</pre>
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
