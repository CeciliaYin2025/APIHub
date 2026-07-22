# Query Console：JSON 結果複製按鈕 + 參數說明欄位設計

日期：2026-07-22
狀態：設計待審

## 1. 背景與問題

Admin UI（`app/admin/js/app.js`）目前有 5 處會把 API 回應以 `syntaxHighlight(JSON.stringify(result, null, 2))` 塞進 `<pre>` 顯示（Query Console、Procedure Console、Write Console、Task 狀態查詢、Data Maintenance 批次結果），但沒有一處提供「複製」功能 —— 使用者只能手動選取 `<pre>` 內文字，选取範圍容易含入語法高亮插入的 HTML 結構或被捲軸截斷。

另外，Query Console 的 `Parameters (JSON)` 欄位只給一個文字框，使用者選定查詢後只看到型別推測出的範例值（`qcExampleParams`），看不到 SQL 定義裡已經存在的 `params[name].description`（如 `app/sql_definitions/employees.yaml` 的 `dept` 參數說明），必須切去 SQL Definitions 頁面另外查。

## 2. 目標

- Query Console、Procedure Console、Write Console、Task 狀態查詢、Data Maintenance 批次結果這 5 處 Raw JSON 區塊，都能一鍵複製「純文字 JSON」（不含語法高亮的 HTML 標籤）到剪貼簿，並用既有 `toast()` 給成功/失敗回饋。
- Query Console 選定查詢後，`Parameters (JSON)` 旁新增「參數說明」欄位，並排顯示每個宣告參數的名稱、型別、必填與否、描述。
- 未選查詢時參數說明欄位顯示提示文字，不報錯。

### 非目標（YAGNI）

- 不改動 Procedure Console / Write Console 的參數輸入欄 —— 這兩者呼叫的是即時輸入的 procedure/function，沒有像 SQL Definition 一樣預先存好的 per-param 描述可用。
- 不做「依使用者目前輸入的 JSON key 動態過濾」參數說明列表 —— 一律顯示該查詢定義的完整參數清單，與文字框內容無關（已與需求方確認）。
- 不重構既有 Python/cURL 範例的複製按鈕（`copyQcPython`/`copyQcCurl`/`qcCopyCode`）—— 它們已經運作正常，本次只新增給 Raw JSON 用的複製機制，不強行合併。

## 3. 關鍵決策彙整

| 項目 | 決策 |
|------|------|
| 複製範圍 | 全部 5 處 Raw JSON 區塊（不只 Query Console）|
| 複製內容 | 未經 `syntaxHighlight` 處理的純文字 `JSON.stringify(result, null, 2)` |
| 複製機制 | 新增通用 `registerCopyPayload` / `copyPayload`，取代各處各自處理 |
| 既有摺疊結構 | 維持不變（`<details>` 的維持 `<details>`，純 `<pre>` 的維持純 `<pre>`）|
| 參數說明來源 | `SqlDefinitionOut.params[name]`（`type` / `required` / `description`），已由 `GET /{db}/queries/definitions` 回傳 |
| 參數說明即時性 | 只在選定查詢時（`qcOnSelectQuery`）重新渲染，不隨文字框輸入即時更新 |
| 版面 | 沿用既有 `.form-row`（1fr/1fr grid，Query Name / Fetch Mode 已用同一 class）|

## 4. 設計

### 4.1 通用 JSON 複製機制

新增於 `app/admin/js/app.js`（放在 `syntaxHighlight` 函式附近或 Query Console 區塊上方均可，選擇放在檔案較前面的共用工具區，因為 5 個呼叫點橫跨多個 console）：

```js
// Raw text for each copyable JSON block, keyed by an id unique per render.
// Needed because the visible <pre> holds syntax-highlighted HTML, not plain text.
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

// Builds a syntax-highlighted <pre> plus a Copy button for `data`, registering
// the plain-text JSON under `id` so copyPayload can retrieve it later.
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
```

`.qc-code-toolbar` 已存在（`justify-content: space-between`），左側放空 `<span>` 讓 Copy 按鈕靠右對齊，與 Python/cURL 分頁的 toolbar 視覺一致。

5 個呼叫點改法（id 各自唯一，避免互相覆蓋 registry）：

| 位置 | 現況 | 改為 |
|------|------|------|
| `executeQuery`（Query Console，`app.js:1684`）| `<details>...<pre>${syntaxHighlight(...)}</pre></details>` | `<details>...${rawJsonBlock('qcResultJson', result)}</details>` |
| `executeProcedure`（`app.js:1752`）| 純 `<pre>` | `${rawJsonBlock('pcResultJson', result)}` |
| `executeWrite`（`app.js:1826`）| 純 `<pre>` | `${rawJsonBlock('wcResultJson', result)}` |
| `checkTask`（`app.js:1881`）| 純 `<pre>` | `${rawJsonBlock('taskResultJson', result)}` |
| Data Maintenance 批次結果（`app.js:2150`）| `<details>...<pre>...</pre></details>` | `<details>...${rawJsonBlock('dmBatchResultJson', result)}</details>` |

`<details>` 外層的摺疊行為完全不動，只有內部的 `<pre>` 換成 `rawJsonBlock(...)` 的輸出（含 toolbar + pre）。

### 4.2 Query Console — 參數說明欄位

`renderQueryConsole()` 內，把現有

```html
<div class="form-group query-console">
  <label>Parameters (JSON)</label>
  <textarea id="qcParams" placeholder='{"dept": "IT"}'>{}</textarea>
</div>
```

改為左右並排：

```html
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
```

新增渲染函式，取代目前 `qcOnSelectQuery` 只設定範例 JSON 的部分：

```js
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
      <span class="qc-param-meta">${esc(p.type || 'string')} · ${p.required ? 'required' : 'optional'}</span>
      ${p.description ? `<div class="qc-param-desc">${esc(p.description)}</div>` : ''}
    </div>`).join('');
}

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
```

新增 CSS（`app/admin/css/style.css`，Query Console 區塊附近）：

```css
.qc-params-info {
  border: 1px solid var(--gray-200); border-radius: var(--radius);
  padding: 10px 12px; min-height: 120px; max-height: 200px; overflow-y: auto;
  font-size: .85rem;
}
.qc-param-item { padding: 6px 0; border-bottom: 1px solid var(--gray-100); }
.qc-param-item:last-child { border-bottom: none; }
.qc-param-name { font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-weight: 600; }
.qc-param-meta { color: var(--gray-500); font-size: .78rem; margin-left: 6px; }
.qc-param-desc { color: var(--gray-600); margin-top: 2px; }
```

`min-height: 120px` 對齊 `.query-console textarea` 的既有 `min-height`，兩欄視覺高度一致。

## 5. 資料流

```
qcOnSelectQuery()  (Query Name 下拉選擇觸發)
  ├─ qcExampleParams(def.params)   → 填入左側 Parameters (JSON) 範例值（既有行為，不變）
  └─ qcRenderParamsInfo(def)       → 就 def.params 逐一渲染右側「Parameter Reference」
                                       （name / type / required / description，全部已隨
                                       GET /{db}/queries/definitions 一次拿到，無額外 API 呼叫）
```

Raw JSON 複製維持現有渲染時機（各 console 的 `execute*`/`checkTask` 成功回呼內），只是把 `<pre>` 换成 `rawJsonBlock(id, result)`，多做一次 `registerCopyPayload` 存純文字，不影響任何既有 API 呼叫或渲染時序。

## 6. 測試計畫

前端無既有 JS 單元測試框架（`tests/` 皆為 pytest，針對後端），本次改動全部在 `app/admin/`，因此驗證方式為手動於瀏覽器操作：

| 測試 | 內容 |
|------|------|
| Query Console 選定查詢 | 選一個有 `params` 且 `description` 非空的查詢（如 `employees`）→ 右側出現 name/type/required/description |
| Query Console 無參數查詢 | 選一個 `params` 為空的查詢 → 右側顯示「This query has no parameters.」|
| Query Console 未選查詢 | 進入頁面尚未選查詢 → 右側顯示「Select a query to see parameter details.」|
| Raw JSON 複製 - Query Console | 執行查詢成功 → 展開 Raw JSON Response → 點 Copy → 剪貼簿內容為未含 HTML 標籤的合法 JSON，且與展開內容數值一致 |
| Raw JSON 複製 - 其餘 4 處 | Procedure / Write / Task / Data Maintenance 批次結果，各自執行一次成功案例，確認 Copy 按鈕存在且可用 |
| 複製失敗情境 | 於非 HTTPS/非 localhost 環境（`navigator.clipboard` 不可用）點 Copy → 顯示 `Copy failed` toast，不拋出未捕捉例外 |
| 既有功能不迴歸 | Python/cURL 範例分頁的 Copy 按鈕、Query Console 摺疊/展開行為、Data Maintenance 批次摘要表格，均維持原行為 |

不需要後端測試，本次無 `app/api`、`app/services`、`app/db` 異動。

## 7. 異動檔案

| 檔案 | 異動 |
|------|------|
| `app/admin/js/app.js` | 新增 `_copyRegistry` / `registerCopyPayload` / `copyPayload` / `rawJsonBlock`；5 處 Raw JSON 渲染改用 `rawJsonBlock`；新增 `qcRenderParamsInfo`；`qcOnSelectQuery` 呼叫它；`renderQueryConsole` 的 Parameters 區塊改為兩欄 `.form-row` |
| `app/admin/css/style.css` | 新增 `.qc-params-info` / `.qc-param-item` / `.qc-param-name` / `.qc-param-meta` / `.qc-param-desc` |
