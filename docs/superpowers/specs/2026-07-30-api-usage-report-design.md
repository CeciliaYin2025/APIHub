# API 使用統計報表 — 設計

日期：2026-07-30

## 目標

讓管理者回答兩類問題：

1. **統計** — 在某段期間內，哪些使用者呼叫了哪些 API、打在哪個 DW、呼叫幾次、成功幾次、多快。
2. **明細** — 針對統計中任一組，看到每一筆請求，以及該次請求實際傳入的查詢參數。

資料來源是既有的 `api_hub_audit_logs`（`AuditMiddleware` 每個 request 寫一列）。目前該表沒有記錄請求參數，也沒有任何查詢它的 API 或 UI 頁面，兩者都要補上。

## 已確認的決策

| 決策 | 選擇 |
|---|---|
| 明細看得到什麼 | 現有欄位 **＋ 實際請求參數** |
| 記錄範圍 | query 執行、procedure 執行（含 async）、write（含 batch）、SQL 定義 CRUD |
| 權限 | **僅 superuser** |
| 統計呈現 | 單一表格，篩選 + 可勾選的分組依據（user / DW / API / 日） |
| params 控管 | **不截斷、不遮罩**；改以保留期限 + 清理指令控制表大小 |
| 明細互動 | 統計列 drill-down → 明細清單 → 單筆展開看 params |
| 統計計算方式 | 即時 `GROUP BY` + 複合索引（非預聚合） |

## 非目標

以下明確不在本次範圍：CSV / Excel 匯出、圖表視覺化、排程寄送報表、跨 API Hub 實例的彙總、預聚合表或 materialized view。

## 資料層

### `AuditLog` 新增欄位

`app/models/audit_log.py`：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `action_type` | `String(20)`, index, nullable | `query` / `procedure` / `write` / `definition` / `report`。統計頁用它區分資料查詢使用量與管理操作 |
| `params` | `JSONB`, nullable | 該次請求的參數原文，不截斷、不遮罩 |
| `result_rows` | `Integer`, nullable | 該次回傳或影響的筆數 |

`action` 欄位沿用既有語意：query_name / procedure name / table / 定義名稱。

### Schema 演進

專案沒有 Alembic，沿用 `scripts/init_db.py` 既有的 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 模式，補上三個欄位與索引。舊資料三欄為 `NULL`，統計頁仍可計算次數與耗時。

### 索引

`ts` 已有索引。新增四個複合索引，對應統計頁的篩選維度：

- `(principal, ts)`
- `(db_alias, ts)`
- `(action, ts)`
- `(action_type, ts)`

### 保留期限與清理

- 新設定 `AUDIT_RETENTION_DAYS`（`app/core/config.py`，預設 `365`）。
- 新指令 `scripts/purge_audit_logs.py`：`python -m scripts.purge_audit_logs [--days N] [--dry-run]`。分批 `DELETE`（預設每批 10000 列）避免長交易鎖表。`--dry-run` 只回報將刪除的列數。
- 純手動 / 由外部排程呼叫，不掛 Celery beat。

## 寫入路徑

`AuditMiddleware` 在 `call_next` 之後才組 row，讀不到已被消費的 request body。沿用專案既有做法：由 router 把資訊放進 `request.state`，middleware 只負責搬進 audit row。

新增共用 helper：

```python
# app/middleware/audit.py
def record_audit(request, action_type, action=None, params=None, result_rows=None) -> None
```

寫入 `request.state.audit_action_type` / `audit_action` / `audit_params` / `audit_result_rows`；`build_audit_row()` 讀取這些值。可重複呼叫（先記 action_type/action/params，回應成功後再補 result_rows）。

### 要 instrument 的端點

| 端點 | `action_type` | `action` | `params` | `result_rows` |
|---|---|---|---|---|
| `POST /{db}/queries/execute` | `query` | `query_name` | `{params, fetch_mode}` | 回傳筆數；`count` 模式取 `total` |
| `POST /{db}/procedures/execute` | `procedure` | `name` | `{params, out_params, is_function}` | `NULL` |
| `POST /{db}/procedures/execute-async` | `procedure` | `name` | `{params, out_params, is_function}` | `NULL` |
| `POST /{db}/writes/execute` | `write` | `table` | `{operation, data, where, async_mode}` | `affected_rows` |
| `POST /{db}/writes/batch` | `write` | `table` | `{operations, async_mode}` | `total_affected` |
| `POST /{db}/queries/definitions` | `definition` | 定義名稱 | 請求 body | `NULL` |
| `PUT /{db}/queries/definitions/{name}` | `definition` | 定義名稱 | 請求 body | `NULL` |
| `DELETE /{db}/queries/definitions/{name}` | `definition` | 定義名稱 | `NULL` | `NULL` |
| `GET /audit/*`（報表自身） | `report` | `usage` / `logs` / `log_detail` / `filters` | `NULL` | `NULL` |

失敗的請求（4xx / 5xx）同樣留下 audit 列並帶 params —— 這對排查「某支查詢為何一直失敗」最有價值。

### 需要處理的命名衝突

`app/api/v1/procedures.py` 與 `app/api/v1/writes.py` 目前把 request body 參數命名為 `request`，與 FastAPI 的 `Request` 撞名。這兩個檔的 body 參數改名為 `payload`（與 `queries.py` 既有寫法一致；body 參數名不出現在對外 API 合約中），再注入真正的 `request: Request`。

## API 層

新增 `app/api/v1/audit.py`（router，prefix `/audit`，全部 `Depends(get_current_active_superuser)`）與 `app/services/audit_report_service.py`（純 SQLAlchemy 聚合，不碰 HTTP）。Schemas 放 `app/schemas/audit.py`。

這些端點**不在 `/{db}/` 底下** —— 報表要跨 DW 比較，`db_alias` 是篩選條件而非路由段。

### `GET /audit/usage` — 統計

篩選參數：

| 參數 | 預設 | 說明 |
|---|---|---|
| `date_from` / `date_to` | 近 30 天 | 含端點（`date_to` 當日整天） |
| `principal` | 全部 | 使用者名稱 |
| `db_alias` | 全部 | DW |
| `action` | 全部 | API 名稱 |
| `action_type` | `query,procedure,write` | 逗號分隔；預設排除 `definition` 與 `report`，讓「使用量」不被管理操作污染 |
| `group_by` | `user,db,api` | 逗號分隔，可組合 `user` / `db` / `api` / `day`；空值時 `items` 為空陣列、只回 `totals` |
| `sort` | `-calls` | 支援 `calls`、`failed`、`avg_duration_ms`、`total_rows`、`last_used_at`，前綴 `-` 為遞減 |
| `page` / `page_size` | `1` / `50` | `page_size` 上限 200 |

回應：

```json
{
  "total_groups": 128,
  "page": 1,
  "page_size": 50,
  "totals": { "calls": 4210, "success": 4180, "failed": 30,
              "avg_duration_ms": 168, "max_duration_ms": 4120,
              "total_rows": 918233, "last_used_at": "2026-07-30T10:12:33" },
  "items": [
    { "principal": "alice", "db_alias": "DW1", "action": "get_orders",
      "day": null, "calls": 320, "success": 318, "failed": 2,
      "avg_duration_ms": 145, "max_duration_ms": 980,
      "total_rows": 74210, "last_used_at": "2026-07-30T10:12:33" }
  ]
}
```

未被 `group_by` 選中的維度在 `items` 中為 `null`。

度量定義：`success` = `status_code < 400`，`failed` = `status_code >= 400`，`calls` = 兩者之和。`avg_duration_ms` 四捨五入為整數。`total_rows` 為 `result_rows` 之和，`NULL` 視為 0。`last_used_at` 為該組 `ts` 的最大值。

### `GET /audit/logs` — 明細清單

篩選同 `/audit/usage`（drill-down 帶著該組的 user / db / api 值過來），另加 `status_code`、`min_duration_ms`。`page_size` 預設 25、上限 100。

回應每筆：`id`、`ts`、`principal`、`principal_type`、`api_key_name`、`client_ip`、`method`、`path`、`db_alias`、`action_type`、`action`、`status_code`、`duration_ms`、`result_rows`、`params`。

**params 大小處理**（因為 params 不截斷，`writes/batch` 可能數 MB）：僅在序列化後小於 `AUDIT_PARAMS_INLINE_LIMIT`（`app/core/config.py`，預設 4096 bytes）時內嵌 `params`；否則回 `params: null`、`params_omitted: true`、`params_bytes: <size>`，由前端在展開該列時另外取回。

### `GET /audit/logs/{id}` — 單筆完整內容

回傳單筆的完整欄位含未截斷的 `params`。供明細清單展開 `params_omitted` 的列時使用。查無 → 404。

### `GET /audit/filters` — 下拉選單選項

參數 `date_from` / `date_to`。回傳該區間內出現過的 distinct 清單：

```json
{ "principals": ["alice", "bob"], "db_aliases": ["DW1", "DW2"],
  "actions": ["get_orders", "sp_rebuild", "ORDERS"] }
```

前端無法從既有 `/users`、`/databases` 端點拼出 `actions`（混了 query 名、procedure 名、table 名），所以需要這支。

## Admin UI

側邊欄新增 `data-page="usage-report"`，標題 `API Usage Report`，位置在 `tasks` 之後。非 superuser 隱藏該連結（用既有的 `currentUser.is_superuser`，`renderSqlDefs()` 已有同樣做法）；後端仍是唯一權威。

依 `app/admin/CLAUDE.md`，list → detail 的導覽採 SQL Definitions 的全頁模式，不用 modal。兩個 in-section 視圖：

**`renderUsageReport()`** — 統計頁

- 篩選列：日期區間、User、DW、API 下拉（選項來自 `/audit/filters`）
- 「分組依據」勾選：user / DW / API / 日
- 統計表格：欄位標題可點擊排序，底部分頁，最上方一列顯示 `totals`
- 每列尾端「明細」按鈕 → 帶該列的維度值進入明細視圖

**`enterUsageDetailView(filters)`** — 明細頁

- 頂端「‹ 返回統計」與目前生效篩選的 chips
- 明細表格每列可展開，顯示格式化後的 `params` JSON 與「複製 JSON」按鈕（沿用 Data Maintenance 既有的複製 JSON 做法）
- `params_omitted` 的列在展開時呼叫 `GET /audit/logs/{id}` 取完整內容
- 可直接從側邊欄進入自行篩選，不必先經過統計頁

**檔案結構**：`app/admin/js/app.js` 已 2457 行，本功能約再 400 行，因此放到新檔 `app/admin/js/usage_report.js`，在 `index.html` 用第二個 `<script src="{{BASE_PATH}}/admin/js/usage_report.js">` 載入（`/admin/js` 整個目錄已是 StaticFiles 掛載，不需改 `main.py`）。`navigateTo()` 只多註冊一個 renderer。共用 helper（`api()`、`esc()`、`toast()`、`currentUser`）維持在 `app.js`，新檔直接沿用其全域。需同步更新 `app/admin/CLAUDE.md` 對「one js/app.js」的描述。

## 錯誤處理與邊界

- Audit 寫入維持 best-effort：新增欄位不改變「失敗只記 log、絕不影響回應」的保證。`params` 若含無法序列化為 JSON 的值，在既有 `try/except` 內退回字串表示，不讓請求失敗。
- 報表端點驗證失敗回 `400` 並附可用值：`date_from > date_to`、未知的 `group_by` 值、未知的 `sort` 欄位、未知的 `action_type` 值。
- 查無資料回空 `items` 與 `total_groups: 0`，不是 404。
- 非 superuser 回 `403`（既有 `get_current_active_superuser` dependency）。
- 時間基準沿用現有的 `func.now()`（平台 PostgreSQL 本地時間、無時區）；按日分組用 DB 端 date truncation，不做時區轉換。

## 測試

| 檔案 | 覆蓋 |
|---|---|
| `tests/test_audit_middleware.py`（擴充） | 新三欄從 `request.state` 正確落地；`params` 序列化失敗時請求仍成功 |
| `tests/test_audit_instrumentation.py`（新） | query / procedure / write / definition 四類端點各自寫入正確的 `action_type`、`action`、`params`、`result_rows`；失敗請求也留列 |
| `tests/test_audit_report_service.py`（新） | 聚合正確性：`group_by` 各種組合、日期邊界含端點、成功/失敗切分、`avg`/`max duration`、`total_rows`、`last_used_at`、`totals`、排序與分頁 |
| `tests/test_audit_endpoints.py`（新） | 非 superuser 403、篩選與分頁、`/audit/filters`、參數驗證 400、params 過大時的 omit 與 `/audit/logs/{id}` 取回 |
| `tests/test_purge_audit_logs.py`（新） | 保留天數 cutoff 正確、`--dry-run` 不刪、分批刪除 |
| `tests/e2e/`（新 spec） | 篩選 → 勾選分組 → drill-down → 展開看 params → 複製 JSON |

## 已知風險

**params 原樣儲存**（依明確決策，不截斷、不遮罩）帶來兩個風險：

1. **敏感資料落地** — 若呼叫方把密碼、token 之類的值當作 bind 參數傳入，會原樣存進 `api_hub_audit_logs.params`。該表僅 superuser 可讀，但資料庫備份會一併帶走。
2. **單列體積** — `writes/batch` 的 payload 可能達數 MB，會使表快速成長。以 `AUDIT_RETENTION_DAYS` + `purge_audit_logs` 控制上界；`AUDIT_PARAMS_INLINE_LIMIT` 只避免 API 回應爆量，不減少儲存量。

若日後要收緊，最小改動是在 `record_audit()` 內加入遮罩與截斷，不影響其他層。

## 文件更新

依 CLAUDE.md 一併更新 `README.md`、`docs/system_specification.md`，以及 `app/admin/CLAUDE.md`（第二個 JS 檔的說明）。
