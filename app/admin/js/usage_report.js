/**
 * Oracle API Hub — API Usage Report
 *
 * Statistics over api_hub_audit_logs (who called which API, on which data
 * source, how often) plus a drill-down to the individual requests and the
 * parameters they were called with. Superuser-only; the backend enforces it.
 *
 * Lives outside app.js because that file is already large and this section is
 * self-contained. It relies on app.js globals: api(), esc(), toast(),
 * currentUser, syntaxHighlight(), registerCopyPayload()/copyPayload().
 */

/* ── State ────────────────────────────────────────────────────────────── */
const UR_ACTION_TYPES = ['query', 'procedure', 'write', 'definition', 'report'];
const UR_GROUPS = [
  { key: 'user', label: 'User' },
  { key: 'db', label: 'DW' },
  { key: 'api', label: 'API' },
  { key: 'day', label: 'Day' },
];

const urState = {
  dateFrom: '',
  dateTo: '',
  principal: '',
  dbAlias: '',
  action: '',
  actionTypes: ['query', 'procedure', 'write'],
  groupBy: ['user', 'db', 'api'],
  sort: '-calls',
  page: 1,
  pageSize: 50,
};

function urDefaultRange() {
  const end = new Date();
  const start = new Date(end.getTime() - 29 * 86400000);
  const iso = d => d.toISOString().slice(0, 10);
  return { from: iso(start), to: iso(end) };
}

// Shared section header for the statistics and detail views, which are two
// pages under one sidebar entry (same approach as enterSqlDefView).
function enterUsageView(title) {
  document.querySelectorAll('.sidebar-nav a').forEach(a =>
    a.classList.toggle('active', a.dataset.page === 'usage-report'));
  document.getElementById('pageTitle').textContent = title;
  const area = document.getElementById('contentArea');
  area.scrollTop = 0;
  return area;
}

function urQueryString(extra = {}) {
  const params = new URLSearchParams();
  params.set('date_from', urState.dateFrom);
  params.set('date_to', urState.dateTo);
  if (urState.principal) params.set('principal', urState.principal);
  if (urState.dbAlias) params.set('db_alias', urState.dbAlias);
  if (urState.action) params.set('action', urState.action);
  // Always explicit: an empty string means "no action_type filter", which is
  // different from omitting the parameter (that would re-apply the default).
  params.set('action_type', urState.actionTypes.join(','));
  Object.entries(extra).forEach(([k, v]) => params.set(k, v));
  return params.toString();
}

/* ── Statistics page ─────────────────────────────────────────────────── */
async function renderUsageReport() {
  const area = enterUsageView('API Usage Report');
  if (!urState.dateFrom) {
    const { from, to } = urDefaultRange();
    urState.dateFrom = from;
    urState.dateTo = to;
  }
  area.innerHTML = urFilterBar() +
    '<div id="urResults"><div style="text-align:center;padding:40px"><div class="spinner"></div></div></div>';
  urBindFilterEvents();
  await urLoadFilterOptions();
  await urLoadStats();
}

function urFilterBar() {
  return `
    <div class="card">
      <div class="card-body">
        <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end">
          <div>
            <label class="text-sm">From</label><br>
            <input type="date" id="urFrom" value="${esc(urState.dateFrom)}">
          </div>
          <div>
            <label class="text-sm">To</label><br>
            <input type="date" id="urTo" value="${esc(urState.dateTo)}">
          </div>
          <div>
            <label class="text-sm">User</label><br>
            <select id="urPrincipal"><option value="">All</option></select>
          </div>
          <div>
            <label class="text-sm">DW</label><br>
            <select id="urDbAlias"><option value="">All</option></select>
          </div>
          <div>
            <label class="text-sm">API</label><br>
            <select id="urAction"><option value="">All</option></select>
          </div>
          <div>
            <button class="btn btn-primary btn-sm" id="urApply">Apply</button>
            <button class="btn btn-outline btn-sm" id="urDetail">Detail &rsaquo;</button>
          </div>
        </div>
        <div style="margin-top:12px;display:flex;flex-wrap:wrap;gap:20px">
          <div>
            <span class="text-sm"><strong>Type</strong></span>
            ${UR_ACTION_TYPES.map(t => `
              <label class="text-sm" style="margin-left:8px">
                <input type="checkbox" class="ur-type" value="${t}"
                  ${urState.actionTypes.includes(t) ? 'checked' : ''}> ${t}
              </label>`).join('')}
          </div>
          <div>
            <span class="text-sm"><strong>Group by</strong></span>
            ${UR_GROUPS.map(g => `
              <label class="text-sm" style="margin-left:8px">
                <input type="checkbox" class="ur-group" value="${g.key}"
                  ${urState.groupBy.includes(g.key) ? 'checked' : ''}> ${esc(g.label)}
              </label>`).join('')}
          </div>
        </div>
      </div>
    </div>`;
}

function urReadFilterInputs() {
  urState.dateFrom = document.getElementById('urFrom').value;
  urState.dateTo = document.getElementById('urTo').value;
  urState.principal = document.getElementById('urPrincipal').value;
  urState.dbAlias = document.getElementById('urDbAlias').value;
  urState.action = document.getElementById('urAction').value;
  urState.actionTypes = [...document.querySelectorAll('.ur-type:checked')].map(el => el.value);
  urState.groupBy = [...document.querySelectorAll('.ur-group:checked')].map(el => el.value);
  urState.page = 1;
}

function urBindFilterEvents() {
  document.getElementById('urApply').addEventListener('click', () => {
    urReadFilterInputs();
    urLoadStats();
  });
  document.getElementById('urDetail').addEventListener('click', () => {
    urReadFilterInputs();
    renderUsageDetail({});
  });
}

async function urLoadFilterOptions() {
  let options = { principals: [], db_aliases: [], actions: [] };
  try {
    options = await api('GET', `/audit/filters?date_from=${urState.dateFrom}&date_to=${urState.dateTo}`);
  } catch (err) {
    toast(err.message, 'error');
  }
  const fill = (id, values, selected) => {
    const sel = document.getElementById(id);
    if (!sel) return;
    sel.innerHTML = '<option value="">All</option>' +
      values.map(v => `<option value="${esc(v)}" ${v === selected ? 'selected' : ''}>${esc(v)}</option>`).join('');
  };
  fill('urPrincipal', options.principals, urState.principal);
  fill('urDbAlias', options.db_aliases, urState.dbAlias);
  fill('urAction', options.actions, urState.action);
}

const UR_METRIC_COLUMNS = [
  { key: 'calls', label: 'Calls' },
  { key: 'success', label: 'Success' },
  { key: 'failed', label: 'Failed' },
  { key: 'avg_duration_ms', label: 'Avg ms' },
  { key: 'max_duration_ms', label: 'Max ms' },
  { key: 'total_rows', label: 'Rows' },
  { key: 'last_used_at', label: 'Last used' },
];

const UR_DIM_KEY = { user: 'principal', db: 'db_alias', api: 'action', day: 'day' };

function urCell(item, key) {
  if (key === 'last_used_at') return esc((item.last_used_at || '—').replace('T', ' ').slice(0, 19));
  return esc(item[key] == null ? '—' : item[key]);
}

async function urLoadStats() {
  const results = document.getElementById('urResults');
  results.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div></div>';
  let data;
  try {
    data = await api('GET', `/audit/usage?${urQueryString({
      group_by: urState.groupBy.join(','), sort: urState.sort,
      page: urState.page, page_size: urState.pageSize,
    })}`);
  } catch (err) {
    results.innerHTML = `<div class="card"><div class="card-body"><p>Error: ${esc(err.message)}</p></div></div>`;
    return;
  }

  const dims = UR_GROUPS.filter(g => urState.groupBy.includes(g.key));
  const pages = Math.max(1, Math.ceil(data.total_groups / urState.pageSize));

  results.innerHTML = `
    <div class="card">
      <div class="card-header">
        <h2>Usage — ${data.total_groups} group(s)</h2>
        <span class="text-sm">Total ${data.totals.calls} call(s), ${data.totals.failed} failed,
          avg ${data.totals.avg_duration_ms} ms, ${data.totals.total_rows} row(s)</span>
      </div>
      <div class="card-body no-pad">
        <table class="data-table"><thead><tr>
          ${dims.map(d => `<th>${esc(d.label)}</th>`).join('')}
          ${UR_METRIC_COLUMNS.map(c => `<th><a href="#" class="ur-sort" data-col="${c.key}">${esc(c.label)}</a></th>`).join('')}
          <th></th>
        </tr></thead>
        <tbody>${data.items.length === 0
          ? `<tr><td colspan="${dims.length + UR_METRIC_COLUMNS.length + 1}">No usage in this range.</td></tr>`
          : data.items.map((item, i) => `<tr>
              ${dims.map(d => `<td>${esc(item[UR_DIM_KEY[d.key]] == null ? '—' : item[UR_DIM_KEY[d.key]])}</td>`).join('')}
              ${UR_METRIC_COLUMNS.map(c => `<td>${urCell(item, c.key)}</td>`).join('')}
              <td><button class="btn btn-sm btn-outline ur-drill" data-index="${i}">Detail</button></td>
            </tr>`).join('')}</tbody></table>
      </div>
      <div class="card-body">
        <span class="text-sm">Page ${data.page} / ${pages}</span>
        <button class="btn btn-sm btn-outline" id="urPrev" ${data.page <= 1 ? 'disabled' : ''}>Prev</button>
        <button class="btn btn-sm btn-outline" id="urNext" ${data.page >= pages ? 'disabled' : ''}>Next</button>
      </div>
    </div>`;

  window._urItems = data.items;
  results.querySelectorAll('.ur-sort').forEach(a => a.addEventListener('click', e => {
    e.preventDefault();
    const col = a.dataset.col;
    urState.sort = urState.sort === `-${col}` ? col : `-${col}`;
    urState.page = 1;
    urLoadStats();
  }));
  results.querySelectorAll('.ur-drill').forEach(b => b.addEventListener('click', () => {
    const item = window._urItems[Number(b.dataset.index)];
    renderUsageDetail({
      principal: item.principal || '', dbAlias: item.db_alias || '',
      action: item.action || '', day: item.day || '',
    });
  }));
  const prev = document.getElementById('urPrev');
  const next = document.getElementById('urNext');
  if (prev) prev.addEventListener('click', () => { urState.page -= 1; urLoadStats(); });
  if (next) next.addEventListener('click', () => { urState.page += 1; urLoadStats(); });
}
