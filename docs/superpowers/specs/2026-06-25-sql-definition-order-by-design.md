# SQL Definition `order_by` 欄位設計

日期：2026-06-25
狀態：設計待審

## 1. 背景與問題

SQL definition 的查詢執行（`app/services/query_service.py`）對所有 fetch mode 都會把定義的 SQL 包成 derived table（`... FROM (<sql>) sub_`）以套用分頁、top_n、one、count。

在 **MS SQL Server**，`ORDER BY` 不允許出現在 derived table / subquery / view / CTE 內，除非同時有 `TOP`、`OFFSET` 或 `FOR XML`。因此使用者在 MSSQL 的 SQL definition 內寫 `ORDER BY` 會執行失敗。Oracle 與 PostgreSQL 雖無此限制，但把 `ORDER BY` 寫在子查詢內，分頁時的排序穩定性也不保證。

## 2. 目標

- 在 SQL definition 新增結構化 `order_by` 欄位，由 Hub 在 wrapper 層注入排序，解決 MSSQL 限制，並讓三引擎分頁排序穩定一致。
- **完全向後相容**：未設定 `order_by`（空清單）的定義行為與現況完全相同。

非目標（YAGNI）：

- 不支援呼叫端（request 時）動態指定排序——排序固定在定義層。
- 不支援 `NULLS FIRST/LAST`（MSSQL 不支援，跨引擎不一致）。
- 不支援排序運算式（只允許輸出欄位名）。

## 3. 資料形狀與防注入

排序欄位無法用 bind 參數，必須拼進 SQL，故採白名單式結構化資料：

```python
class OrderBySchema(BaseModel):
    column: str = Field(..., pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    direction: Literal["ASC", "DESC"] = "ASC"
```

- `column` 必須是定義 SQL 的**輸出欄位名**（排序作用在 derived table `sub_` 上）。regex 僅允許純識別字，禁止點號、引號、空白、分號等。
- 定義層 `order_by` 為 `list[OrderBySchema]`。
- Service 端 `_build_order_by(definition) -> str | None` 把清單組成字串，例如 `"PROJECT_ID ASC, NAME DESC"`。因 `column` 經 regex、`direction` 為 enum，組出的字串無 SQL injection 風險。空清單回傳 `None`。
- `_build_order_by` 會**再驗一次** column regex 與 direction（防 YAML/DB 繞過 Pydantic），不合法時 `raise ValueError`。

## 4. 各層改動

| 層 | 檔案 | 改動 |
|----|------|------|
| Schema | `app/schemas/sql_definition.py` | 新增 `OrderBySchema`；`SqlDefinitionCreate/Update/Out` 增 `order_by: list[OrderBySchema] = Field(default_factory=list)` |
| ORM | `app/models/sql_definition.py` | 新增 `order_by = Column(JSONB, default=list)` |
| Migration | `scripts/init_db.py` | `create_all` 後執行冪等 `ALTER TABLE api_hub_sql_definitions ADD COLUMN IF NOT EXISTS order_by jsonb DEFAULT '[]'::jsonb` |
| Registry | `app/services/sql_registry.py` | `SqlDefinition` dataclass 增 `order_by: list[dict] = field(default_factory=list)`（每筆 `{"column":..,"direction":..}`）；DB loader 與 YAML loader 讀取 `order_by` |
| Service | `app/services/query_service.py` | 新增 `_build_order_by()`；在 `_fetch_page/_fetch_all/_fetch_batch/_fetch_top_n/_fetch_one` 把 order_by 傳入對應 wrap 函式 |
| Dialect 介面 | `app/db/dialect.py` | `wrap_paginated/wrap_top_n/wrap_one` 簽章加 `order_by: str \| None = None`；base 新增 `wrap_all(self, sql, order_by=None)` 供 `all` 模式 |
| Dialects | `app/db/dialects/{oracle,postgresql,mssql}_dialect.py` | 依引擎注入排序（見 §5） |
| API | `app/api/v1/queries.py` | CRUD 寫入/回傳與 metadata 端點帶上 `order_by` |
| Admin UI | `app/admin/*`（JS + HTML） | 新增結構化「Order By」表單列：可「加一列」，每列為欄位名輸入 + ASC/DESC 下拉；送出時序列化為 JSON 陣列 |

## 5. 各引擎注入點（`order_by` 存在時）

令 `O = order_by` 字串（如 `PROJECT_ID ASC, NAME DESC`）。

### MSSQL (`mssql_dialect.py`)
- `wrap_paginated`：`ROW_NUMBER() OVER (ORDER BY (SELECT NULL))` → `ROW_NUMBER() OVER (ORDER BY {O})`。
- `wrap_top_n`：`SELECT TOP (:pag_top_n) * FROM ({sql}) sub_ ORDER BY {O}`（有 TOP 即合法）。
- `wrap_one`：`SELECT TOP 1 * FROM ({sql}) sub_ ORDER BY {O}`。

### Oracle (`oracle_dialect.py`)
- `wrap_paginated`：內層來源改為 `SELECT * FROM ({sql}) ORDER BY {O}` 再套 ROWNUM（Oracle 允許子查詢 ORDER BY；排序須在 ROWNUM 指派前）。
- `wrap_top_n`：`SELECT * FROM (SELECT * FROM ({sql}) ORDER BY {O}) WHERE ROWNUM <= :pag_top_n`。
- `wrap_one`：同上，`ROWNUM <= 1`。

### PostgreSQL (`postgresql_dialect.py`)
- `wrap_paginated`：`SELECT * FROM ({sql}) sub_ ORDER BY {O} LIMIT :pag_limit OFFSET :pag_offset`。
- `wrap_top_n`：`SELECT * FROM ({sql}) sub_ ORDER BY {O} LIMIT :pag_top_n`。
- `wrap_one`：`SELECT * FROM ({sql}) sub_ ORDER BY {O} LIMIT 1`。

### `all` 模式（base `Dialect.wrap_all`，三引擎共用）
- `order_by` 為 None：回傳原 `sql`（與現況相同，`_fetch_all` 直接執行）。
- `order_by` 存在：`SELECT * FROM ({sql}) sub_ ORDER BY {O}`（最外層 ORDER BY，三引擎皆合法）。

### `wrap_count`
- 不變。count 與排序無關。

`order_by` 為 None 時，所有 wrap 函式行為與現況完全相同（向後相容）。

## 6. 測試

純單元測試（不需真實 DB 連線）：

- **各 dialect wrap 函式**：傳入 / 不傳入 `order_by` 兩種，斷言組裝出的 SQL 字串正確（含 MSSQL window、Oracle ROWNUM 前排序、PG 外層 ORDER BY、`wrap_all` 兩種分支）。
- **`_build_order_by`**：合法清單→正確字串；空清單→None；非法 column（含 `;`、空白、點號、保留字注入嘗試）→ `ValueError`；direction 正規化。
- **Schema 驗證**：非法 `direction`、非法 `column` regex 被 Pydantic 擋下。

## 7. 向後相容與部署

- 既有定義 `order_by` 預設空清單 → 行為不變。
- 既有 PostgreSQL 表透過 init_db 的冪等 ALTER 取得新欄位；新部署由 `create_all` + 預設值處理。
- 不需重啟資料來源連線池以外的額外步驟；SQL 定義經既有 hot-reload 機制刷新。
