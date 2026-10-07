/* Write Definitions — named, constrained writes (the write-side counterpart of
 * SQL Definitions). Loads after app.js and uses its globals (api(), esc(),
 * escAttr(), descCell(), toast(), dbPrefix(), currentDb, currentUser,
 * rawJsonBlock()). Separate file for the same reason usage_report.js is.
 *
 * Same three views as SQL Definitions — list (two-pane, grouped), detail, form —
 * scoped to the DB selector. The detail page is where an integrator learns the
 * call: it prints a request example built from the definition and offers a dry
 * run, which executes in a transaction and rolls back. */

let wdDefs = [];
let wdGroups = [];
let wdGroup = null;
const WD_UNGROUPED = '(ungrouped)';
const WD_TYPES = ['string', 'number', 'date', 'datetime'];

function wdGroupOf(d) {
  return d.group_name || WD_UNGROUPED;
}

function enterWdView(title) {
  document.querySelectorAll('.sidebar-nav a').forEach(a =>
    a.classList.toggle('active', a.dataset.page === 'write-definitions'));
  document.getElementById('pageTitle').textContent = title;
  const area = document.getElementById('contentArea');
  area.scrollTop = 0;
  return area;
}

// The DB selector does not re-render pages on its own; this one is scoped to
// it, so follow the change while the section is open.
document.addEventListener('DOMContentLoaded', () => {
  const sel = document.getElementById('dbSelect');
  if (sel) sel.addEventListener('change', () => {
    if (document.querySelector('.sidebar-nav a[data-page="write-definitions"].active')) renderWriteDefs();
  });
});

/* ── List ──────────────────────────────────────────────────────────────── */

async function renderWriteDefs() {
  const area = enterWdView('Write Definitions');
  area.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div></div>';
  try {
    wdDefs = await api('GET', `${dbPrefix()}/writes/definitions`);
  } catch (err) {
    area.innerHTML = `<div class="card"><div class="card-body"><p>Error: ${esc(err.message)}</p></div></div>`;
    return;
  }
  wdDefs.sort((a, b) => String(a.name).localeCompare(String(b.name)));
  wdGroups = [...new Set(wdDefs.map(wdGroupOf))].sort((a, b) => a.localeCompare(b));
  if (!wdGroups.includes(wdGroup)) wdGroup = wdGroups[0] || null;

  area.innerHTML = `
    <div class="card">
      <div class="card-header">
        <h2>Write Definitions — ${esc(currentDb)} (${wdDefs.length})</h2>
        <div class="btn-group">
          <button class="btn btn-success btn-sm" onclick="openWriteDefForm('create')">+ New Write Definition</button>
          <button class="btn btn-secondary btn-sm" onclick="renderWriteDefs()">&#8635; Refresh</button>
        </div>
      </div>
      <div class="card-body no-pad">
        <div class="split-list">
          <aside class="split-list-aside" id="wdGroupList"></aside>
          <div class="split-list-main" id="wdTable"></div>
        </div>
      </div>
    </div>
    <p class="text-muted text-sm mt-4">
      A write definition pins the table, the columns a caller may send and — in <strong>replace</strong> mode —
      the key that bounds the delete. Callers run it with <code>POST ${esc(dbPrefix())}/writes/named/execute</code>
      and need the definition's <code>write:&lt;name&gt;</code> permission; <code>table:</code> permissions do not apply.
    </p>`;
  paintWdGroups();
  paintWdTable();
}

function paintWdGroups() {
  const host = document.getElementById('wdGroupList');
  if (!host) return;
  host.innerHTML = `
    <div class="split-list-title">Groups (${wdGroups.length})</div>
    ${wdGroups.map((g, i) => `
      <button type="button" class="split-list-item${g === wdGroup ? ' active' : ''}" onclick="selectWdGroup(${i})">
        <span class="split-list-item-name">${esc(g)}</span>
        <span class="split-list-item-count">${wdDefs.filter(d => wdGroupOf(d) === g).length}</span>
      </button>`).join('')
      || '<div class="split-list-empty">No write definitions in this database.</div>'}`;
}

function paintWdTable() {
  const host = document.getElementById('wdTable');
  if (!host) return;
  const rows = wdDefs.filter(d => wdGroupOf(d) === wdGroup);
  if (!rows.length) {
    host.innerHTML = '<div class="split-list-empty">Select a group to see its write definitions.</div>';
    return;
  }
  host.innerHTML = `
    <table class="data-table"><thead><tr>
      <th>Name</th><th>Status</th><th>Table</th><th>Mode</th><th>Key</th><th>Description</th><th>Actions</th>
    </tr></thead>
    <tbody>${rows.map(d => `<tr${d.enabled === false ? ' class="row-disabled"' : ''}>
      <td><strong>${esc(d.name)}</strong></td>
      <td>${d.enabled === false ? '<span class="badge badge-red">Disabled</span>' : '<span class="badge badge-green">Enabled</span>'}</td>
      <td><code>${esc(d.table_name)}</code></td>
      <td><span class="badge ${d.mode === 'replace' ? 'badge-amber' : 'badge-blue'}">${esc(d.mode)}</span></td>
      <td>${(d.key_columns || []).map(k => `<span class="tag">${esc(k)}</span>`).join(' ') || '—'}</td>
      ${descCell(d.description, 'text-sm')}
      <td><div class="btn-group">
        <button class="btn btn-sm btn-outline" onclick="viewWriteDef('${esc(d.name)}')">Detail</button>
        <button class="btn btn-sm btn-primary" onclick="openWriteDefForm('edit','${esc(d.name)}')">Edit</button>
        <button class="btn btn-sm btn-danger" onclick="deleteWriteDef('${esc(d.name)}')">Delete</button>
      </div></td>
    </tr>`).join('')}</tbody></table>`;
}

window.selectWdGroup = function(index) {
  const g = wdGroups[index];
  if (g === undefined || g === wdGroup) return;
  wdGroup = g;
  paintWdGroups();
  paintWdTable();
};

/* ── Detail ────────────────────────────────────────────────────────────── */

// A request body that passes validation as-is: key filled, one row with every
// declared column, typed placeholder values.
function wdExample(d) {
  const sample = { string: 'text', number: 1, date: '2026-01-31', datetime: '2026-01-31T13:00:00' };
  const key = {};
  (d.key_columns || []).forEach(k => { key[k] = '<key value>'; });
  const row = {};
  (d.columns || []).forEach(c => {
    row[c.name] = (d.key_columns || []).includes(c.name) ? '<key value>' : sample[c.type] ?? 'text';
  });
  const body = { name: d.name };
  if (d.mode === 'replace') body.key = key;
  body.rows = [row];
  body.dry_run = false;
  return body;
}

window.viewWriteDef = async function(name) {
  const area = enterWdView(`Write Definition: ${name}`);
  area.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div></div>';
  let d, roles = [];
  try {
    [d, roles] = await Promise.all([
      api('GET', `${dbPrefix()}/writes/definitions/${encodeURIComponent(name)}`),
      api('GET', '/auth/roles').catch(() => []),
    ]);
  } catch (err) {
    area.innerHTML = `
      <div class="page-nav"><button class="btn btn-outline btn-sm" onclick="renderWriteDefs()">&#8592; Back to list</button></div>
      <div class="card"><div class="card-body"><p class="text-muted">${esc(err.message)}</p></div></div>`;
    return;
  }

  // Exact match or a trailing-* prefix, the same rule has_permission applies.
  const grants = p => p.action === 'write' &&
    (p.resource === d.permission || (p.resource.endsWith('*') && d.permission.startsWith(p.resource.slice(0, -1))));
  const holders = roles.filter(r => (r.permissions || []).some(grants)).map(r => r.name);
  const example = wdExample(d);
  const exampleText = JSON.stringify(example, null, 2);

  area.innerHTML = `
    <div class="page-nav">
      <button class="btn btn-outline btn-sm" onclick="renderWriteDefs()">&#8592; Back to list</button>
      <span class="page-nav-crumb">Write Definitions — ${esc(currentDb)}</span>
    </div>
    <div class="card">
      <div class="card-header">
        <h2>${esc(d.name)}</h2>
        <div class="btn-group">
          <button class="btn btn-sm btn-outline" onclick="validateWriteDef('${esc(d.name)}')">Check against table</button>
          <button class="btn btn-sm ${d.enabled === false ? 'btn-success' : 'btn-secondary'}"
                  onclick="toggleWriteDef('${esc(d.name)}', ${d.enabled === false})">${d.enabled === false ? 'Enable' : 'Disable'}</button>
          <button class="btn btn-sm btn-primary" onclick="openWriteDefForm('edit','${esc(d.name)}')">Edit</button>
          <button class="btn btn-sm btn-danger" onclick="deleteWriteDef('${esc(d.name)}')">Delete</button>
        </div>
      </div>
      <div class="card-body">
        <div class="detail-grid">
          <div class="detail-item"><span class="detail-label">Database / Table</span><span class="detail-value"><code>${esc(d.db_alias)}</code> / <code>${esc(d.table_name)}</code></span></div>
          <div class="detail-item"><span class="detail-label">Status</span><span class="detail-value">${d.enabled === false ? '<span class="badge badge-red">Disabled</span>' : '<span class="badge badge-green">Enabled</span>'}</span></div>
          <div class="detail-item"><span class="detail-label">Mode</span><span class="detail-value"><span class="badge ${d.mode === 'replace' ? 'badge-amber' : 'badge-blue'}">${esc(d.mode)}</span>
            <small class="text-muted">${d.mode === 'replace'
              ? `deletes rows WHERE ${esc((d.key_columns || []).join(' AND '))} = key, then inserts — one transaction`
              : 'insert only, never deletes'}</small></span></div>
          <div class="detail-item"><span class="detail-label">Max rows per call</span><span class="detail-value">${esc(d.max_rows)}</span></div>
          <div class="detail-item"><span class="detail-label">Permission</span><span class="detail-value"><span class="tag">${esc(d.permission)}</span> + <span class="tag">write</span></span></div>
          <div class="detail-item"><span class="detail-label">Granted to roles</span><span class="detail-value">${holders.map(r => `<span class="tag">${esc(r)}</span>`).join(' ') || '<span class="text-muted">none (superusers only)</span>'}</span></div>
          <div class="detail-item detail-wide"><span class="detail-label">Description</span><span class="detail-value">${esc(d.description || '—')}</span></div>
        </div>

        <div class="form-group mt-4">
          <label>Columns (${(d.columns || []).length})</label>
          <table class="data-table"><thead><tr><th>Column</th><th>Type</th><th>Required</th><th>Description</th></tr></thead>
          <tbody>${(d.columns || []).map(c => {
            const isKey = (d.key_columns || []).includes(c.name);
            return `<tr><td><code>${esc(c.name)}</code>${isKey ? ' <span class="badge badge-amber">key</span>' : ''}</td>
              <td>${esc(c.type)}</td><td>${c.required || isKey ? 'yes' : '—'}</td>${descCell(c.description, 'text-sm')}</tr>`;
          }).join('')}</tbody></table>
          <div class="hint">Columns not sent are left out of the INSERT, so the table's DEFAULT applies.
            Dates are sent as YYYY-MM-DD (or YYYY/MM/DD), datetimes as ISO 8601.</div>
        </div>

        <div class="form-group mt-4">
          <label>Request example — <code>POST ${esc(dbPrefix())}/writes/named/execute</code></label>
          ${rawJsonBlock('wdExample', example)}
        </div>

        <div class="form-group mt-4">
          <label>Dry run</label>
          <textarea id="wdDryBody" class="monospace" rows="12">${esc(exampleText)}</textarea>
          <div class="hint">Runs the call in a transaction and rolls back: real validation and real row counts, nothing written.</div>
          <div class="btn-group mt-2"><button class="btn btn-sm btn-success" onclick="dryRunWriteDef()">&#9654; Dry run</button></div>
          <div id="wdDryResult" class="mt-2"></div>
        </div>
      </div>
    </div>`;
};

window.dryRunWriteDef = async function() {
  const out = document.getElementById('wdDryResult');
  let body;
  try {
    body = JSON.parse(document.getElementById('wdDryBody').value);
  } catch (err) {
    out.innerHTML = `<p class="text-danger">Invalid JSON: ${esc(err.message)}</p>`;
    return;
  }
  body.dry_run = true;  // never commit from this button, whatever the textarea says
  out.innerHTML = '<div class="spinner"></div>';
  try {
    const res = await api('POST', `${dbPrefix()}/writes/named/execute`, body);
    out.innerHTML = rawJsonBlock('wdDryRes', res);
  } catch (err) {
    out.innerHTML = `<p class="text-danger">${esc(err.message)}</p>`;
  }
};

window.validateWriteDef = async function(name) {
  try {
    await api('POST', `${dbPrefix()}/writes/definitions/${encodeURIComponent(name)}/validate`);
    toast('Table and columns check out', 'success');
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.toggleWriteDef = async function(name, enable) {
  if (!enable && !confirm(`Disable "${name}"?\n\nCalls will fail with 409 until it is enabled again. Nothing is deleted.`)) return;
  try {
    await api('PUT', `${dbPrefix()}/writes/definitions/${encodeURIComponent(name)}`, { enabled: enable });
    toast(`${enable ? 'Enabled' : 'Disabled'} ${name}`, 'success');
    viewWriteDef(name);
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.deleteWriteDef = async function(name) {
  if (!confirm(`Delete write definition "${name}"? Callers using it will get 404.`)) return;
  try {
    await api('DELETE', `${dbPrefix()}/writes/definitions/${encodeURIComponent(name)}`);
    toast(`Deleted ${name}`, 'success');
    renderWriteDefs();
  } catch (err) {
    toast(err.message, 'error');
  }
};

/* ── Form ──────────────────────────────────────────────────────────────── */

window.openWriteDefForm = async function(mode, name) {
  let d = { name: '', group_name: wdGroup && wdGroup !== WD_UNGROUPED ? wdGroup : '', description: '',
            table_name: '', mode: 'replace', columns: [], key_columns: [], max_rows: 10000, permission: '' };
  if (mode === 'edit') {
    try {
      d = await api('GET', `${dbPrefix()}/writes/definitions/${encodeURIComponent(name)}`);
    } catch (err) {
      toast(err.message, 'error');
      return;
    }
  }
  const isEdit = mode === 'edit';
  const cancel = isEdit ? `viewWriteDef('${esc(name)}')` : 'renderWriteDefs()';
  const area = enterWdView(isEdit ? `Edit Write Definition: ${name}` : 'New Write Definition');
  area.innerHTML = `
    <div class="page-nav">
      <button class="btn btn-outline btn-sm" onclick="${cancel}">&#8592; Back</button>
      <span class="page-nav-crumb">Write Definitions — ${esc(currentDb)}</span>
    </div>
    <div class="card">
      <div class="card-header"><h2>${isEdit ? 'Edit' : 'New'} Write Definition</h2></div>
      <div class="card-body">
        <div class="form-row">
          <div class="form-group">
            <label>Group</label>
            <input type="text" id="wdGroupIn" value="${escAttr(d.group_name || '')}" placeholder="e.g. juniper">
          </div>
          <div class="form-group">
            <label>Name</label>
            <input type="text" id="wdName" value="${escAttr(d.name)}" ${isEdit ? 'disabled' : ''} placeholder="e.g. juniper_i698_load">
            ${isEdit ? '' : '<div class="hint">Letters, digits and _; cannot be changed later.</div>'}
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Permission</label>
            <input type="text" id="wdPerm" value="${escAttr(d.permission)}" placeholder="write:<name> (auto-generated if empty)">
          </div>
          <div class="form-group">
            <label>Max rows per call</label>
            <input type="number" id="wdMaxRows" min="1" max="100000" value="${escAttr(d.max_rows)}">
          </div>
        </div>
        <div class="form-group">
          <label>Description</label>
          <input type="text" id="wdDesc" value="${escAttr(d.description)}" placeholder="What writes through this, and from where">
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Table</label>
            <input type="text" id="wdTableIn" value="${escAttr(d.table_name)}" placeholder="e.g. SOM_I698_REPORT">
            <div class="hint">As this database's account sees it. <a href="#" onclick="wdLoadColumns();return false">Load columns from table</a></div>
          </div>
          <div class="form-group">
            <label>Mode</label>
            <select id="wdMode" onchange="wdModeChanged()">
              <option value="replace" ${d.mode === 'replace' ? 'selected' : ''}>replace — delete by key, then insert</option>
              <option value="insert" ${d.mode === 'insert' ? 'selected' : ''}>insert — append only</option>
            </select>
          </div>
        </div>
        <div class="form-group">
          <label>Columns</label>
          <table class="data-table"><thead><tr><th>Column</th><th>Type</th><th>Required</th><th class="wd-key-col">Key</th><th>Description</th><th></th></tr></thead>
            <tbody id="wdColRows"></tbody></table>
          <button type="button" class="btn btn-xs btn-outline mt-2" onclick="wdAddColRow()">+ Add column</button>
          <div class="hint">Only these columns may be sent. Leave audit columns with a DB DEFAULT (e.g. CREATION_DATE) out.
            <span class="wd-key-col">Key columns bound the delete; every row must carry the request's key values.</span></div>
        </div>
        ${isEdit ? '' : `<label class="text-sm"><input type="checkbox" id="wdSkipCheck"> Skip the table check (the table does not exist yet)</label>`}
      </div>
      <div class="card-footer">
        <button class="btn btn-secondary" onclick="${cancel}">Cancel</button>
        <button class="btn btn-primary" onclick="submitWriteDef('${mode}','${esc(name || '')}')">${isEdit ? 'Update' : 'Create'}</button>
      </div>
    </div>`;
  (d.columns || []).forEach(c => wdAddColRow(c, (d.key_columns || []).includes(c.name)));
  if (!(d.columns || []).length) wdAddColRow();
  wdModeChanged();
};

window.wdAddColRow = function(col = {}, isKey = false) {
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><input type="text" class="wd-c-name" value="${escAttr(col.name || '')}" placeholder="COLUMN_NAME"></td>
    <td><select class="wd-c-type">${WD_TYPES.map(t => `<option ${t === (col.type || 'string') ? 'selected' : ''}>${t}</option>`).join('')}</select></td>
    <td><input type="checkbox" class="wd-c-req" ${col.required ? 'checked' : ''}></td>
    <td class="wd-key-col"><input type="checkbox" class="wd-c-key" ${isKey ? 'checked' : ''}></td>
    <td><input type="text" class="wd-c-desc" value="${escAttr(col.description || '')}"></td>
    <td><button type="button" class="btn btn-xs btn-danger" onclick="this.closest('tr').remove()">&times;</button></td>`;
  document.getElementById('wdColRows').appendChild(tr);
};

window.wdModeChanged = function() {
  const replace = document.getElementById('wdMode').value === 'replace';
  document.querySelectorAll('.wd-key-col').forEach(el => { el.style.display = replace ? '' : 'none'; });
};

// Fill the column list from the real table. Existing rows keep their settings;
// only columns not already listed are added, so this is safe to click twice.
window.wdLoadColumns = async function() {
  const table = document.getElementById('wdTableIn').value.trim();
  if (!table) return toast('Enter a table name first', 'error');
  let schema;
  try {
    schema = await api('GET', `${dbPrefix()}/schemas/describe/${encodeURIComponent(table)}`);
  } catch (err) {
    return toast(err.message, 'error');
  }
  const have = new Set([...document.querySelectorAll('.wd-c-name')].map(i => i.value.trim().toUpperCase()).filter(Boolean));
  document.querySelectorAll('#wdColRows tr').forEach(tr => {
    if (!tr.querySelector('.wd-c-name').value.trim()) tr.remove();
  });
  const typeOf = t => /NUMBER|INT|DEC|NUMERIC|FLOAT|DOUBLE|REAL|MONEY/i.test(t) ? 'number'
    : /TIMESTAMP|DATETIME/i.test(t) ? 'datetime' : /DATE/i.test(t) ? 'date' : 'string';
  let added = 0;
  schema.columns.forEach(c => {
    if (have.has(String(c.name).toUpperCase())) return;
    wdAddColRow({ name: c.name, type: typeOf(c.type || c.data_type || ''), required: c.nullable === false });
    added++;
  });
  wdModeChanged();
  toast(`Added ${added} column(s) — remove the ones callers must not write`, 'success');
};

window.submitWriteDef = async function(mode, name) {
  const replace = document.getElementById('wdMode').value === 'replace';
  const columns = [], keys = [];
  document.querySelectorAll('#wdColRows tr').forEach(tr => {
    const n = tr.querySelector('.wd-c-name').value.trim();
    if (!n) return;
    columns.push({
      name: n,
      type: tr.querySelector('.wd-c-type').value,
      required: tr.querySelector('.wd-c-req').checked,
      description: tr.querySelector('.wd-c-desc').value.trim(),
    });
    if (replace && tr.querySelector('.wd-c-key').checked) keys.push(n);
  });
  const body = {
    group_name: document.getElementById('wdGroupIn').value.trim(),
    description: document.getElementById('wdDesc').value.trim(),
    table_name: document.getElementById('wdTableIn').value.trim(),
    mode: replace ? 'replace' : 'insert',
    columns,
    key_columns: keys,
    max_rows: parseInt(document.getElementById('wdMaxRows').value, 10) || 10000,
    permission: document.getElementById('wdPerm').value.trim(),
  };
  try {
    if (mode === 'edit') {
      await api('PUT', `${dbPrefix()}/writes/definitions/${encodeURIComponent(name)}`, body);
    } else {
      body.name = document.getElementById('wdName').value.trim();
      const skip = document.getElementById('wdSkipCheck')?.checked ? '?skip_table_check=true' : '';
      await api('POST', `${dbPrefix()}/writes/definitions${skip}`, body);
      name = body.name;
    }
    toast(`Saved ${name}`, 'success');
    viewWriteDef(name);
  } catch (err) {
    toast(err.message, 'error');
  }
};
