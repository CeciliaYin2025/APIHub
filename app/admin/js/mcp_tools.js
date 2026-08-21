/* MCP Tools — read-only view of the tool catalog the MCP server derives from
 * the registered SQL definitions. Loads after app.js and uses its globals
 * (api(), esc(), toast()). Separate file for the same reason usage_report.js
 * is: app.js is already long and this section is self-contained.
 *
 * There is deliberately nothing to edit here. The MCP server is a separate
 * process configured through MCP_* environment variables, so the Admin UI has
 * nowhere to write to; what it can do is show which definitions actually
 * become tools, because the MCP server drops name collisions silently. */

let mcpToolsData = null;
let mcpToolsFilter = '';
let mcpToolsProblemsOnly = false;

/* A row worth an operator's attention: dropped outright, or renamed by the
 * length cap in a way that may have eaten what made it distinct. The cap itself
 * comes from the API response (tool_name_max_len), never hardcoded here. */
function mcpToolsIsProblem(t) {
  return !t.exposed || t.truncated;
}

async function renderMcpTools() {
  const area = document.getElementById('contentArea');
  area.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div></div>';

  try {
    mcpToolsData = await api('GET', '/mcp/tools');
  } catch (err) {
    area.innerHTML = `<div class="card"><div class="card-body"><p>Error: ${esc(err.message)}</p></div></div>`;
    return;
  }

  const s = mcpToolsData.summary;
  // Count rows, not flags: a definition can be both shadowed and truncated, and
  // summing the two counters would promise more rows than the filter shows.
  const problems = mcpToolsData.tools.filter(mcpToolsIsProblem).length;
  area.innerHTML = `
    <div class="card">
      <div class="card-header">
        <h2>MCP Tool Catalog</h2>
        <button class="btn btn-secondary btn-sm" onclick="renderMcpTools()">Refresh</button>
      </div>
      <div class="card-body">
        <p>
          <span class="badge badge-blue">${s.definitions} definitions</span>
          <span class="badge badge-green">${s.exposed} exposed</span>
          <span class="badge ${s.shadowed ? 'badge-red' : 'badge-gray'}">${s.shadowed} shadowed</span>
          <span class="badge ${s.truncated ? 'badge-amber' : 'badge-gray'}">${s.truncated} truncated</span>
        </p>
        <div class="hint">
          One MCP tool is derived per (database, SQL definition) as
          <code>query_&lt;alias&gt;_&lt;definition&gt;</code>, lowercased, with characters outside
          <code>[a-zA-Z0-9_-]</code> replaced by <code>_</code> and the result capped at
          ${mcpToolsData.tool_name_max_len} characters. When two definitions derive the same name the
          MCP server keeps the first and <strong>silently drops the rest</strong> — those are the
          shadowed rows below, and they never reach an LLM even though the REST API serves them
          normally.
        </div>
        <div class="hint">
          This is a read-only projection built from the same naming rules the MCP server uses, over
          every configured alias in <code>GET /databases</code> order. If the MCP server sets
          <code>MCP_DB_ALIASES</code> it exposes a subset of these; the Hub cannot see that setting,
          since it belongs to the MCP process.
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <input type="text" id="mcpToolFilter" placeholder="Filter by tool, definition or database…"
               style="max-width:320px" oninput="mcpToolsOnFilter(this.value)" value="${esc(mcpToolsFilter)}">
        <label style="display:flex;align-items:center;gap:6px;font-size:.85rem">
          <input type="checkbox" id="mcpToolProblems" ${mcpToolsProblemsOnly ? 'checked' : ''}
                 onchange="mcpToolsOnProblemsOnly(this.checked)">
          Problems only (${problems})
        </label>
      </div>
      <div class="card-body no-pad">
        <table class="data-table"><thead><tr>
          <th>Status</th><th>MCP Tool Name</th><th>Database</th><th>Definition</th><th>Parameters</th>
        </tr></thead>
        <tbody id="mcpToolRows"></tbody></table>
      </div>
    </div>
  `;
  mcpToolsRenderRows();
}

window.mcpToolsOnFilter = function(value) {
  mcpToolsFilter = value;
  mcpToolsRenderRows();
};

window.mcpToolsOnProblemsOnly = function(checked) {
  mcpToolsProblemsOnly = checked;
  mcpToolsRenderRows();
};

function mcpToolsRenderRows() {
  const body = document.getElementById('mcpToolRows');
  if (!body || !mcpToolsData) return;

  const needle = mcpToolsFilter.trim().toLowerCase();
  const rows = mcpToolsData.tools.filter(t => {
    if (mcpToolsProblemsOnly && !mcpToolsIsProblem(t)) return false;
    if (!needle) return true;
    return `${t.tool_name} ${t.db_alias} ${t.definition}`.toLowerCase().includes(needle);
  });

  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="5"><div class="empty-state">No matching definitions.</div></td></tr>';
    return;
  }

  body.innerHTML = rows.map(t => {
    const status = t.exposed
      ? '<span class="badge badge-green">exposed</span>'
      : '<span class="badge badge-red">shadowed</span>';
    const truncated = t.truncated ? ' <span class="badge badge-amber">truncated</span>' : '';
    const shadows = t.shadows.length
      ? ` <span class="badge badge-yellow">shadows ${t.shadows.length}</span>`
      : '';

    let note = '';
    if (!t.exposed) {
      note = `<div class="hint">Name already taken by
        <strong>${esc(t.shadowed_by.definition)}</strong> on
        <strong>${esc(t.shadowed_by.db_alias)}</strong> — this definition is not offered to any LLM.</div>`;
    } else if (t.shadows.length) {
      const list = t.shadows.map(o => `${esc(o.db_alias)} / ${esc(o.definition)}`).join(', ');
      note = `<div class="hint">Wins this name; dropped: ${list}</div>`;
    }
    if (t.truncated) {
      note += `<div class="hint">Derived name exceeded ${mcpToolsData.tool_name_max_len} characters and was cut —
        anything distinguishing at the end of the definition name (a version suffix, for example) is gone.</div>`;
    }

    return `<tr>
      <td>${status}${truncated}${shadows}</td>
      <td><code>${esc(t.tool_name)}</code>${note}</td>
      <td><span class="tag">${esc(t.db_alias)}</span></td>
      <td>${esc(t.definition)}</td>
      <td>${t.params.length ? t.params.map(p => `<span class="tag">${esc(p)}</span>`).join('') : '—'}</td>
    </tr>`;
  }).join('');
}
