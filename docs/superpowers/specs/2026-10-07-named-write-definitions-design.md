# 具名寫入定義（Named Write Definitions）— 設計

日期：2026-10-07

## 目標

讓「寫入」跟「查詢」一樣，成為在 Admin UI 上看得到、管得到的具名 API：

1. **可見** —— 每個寫入用途是一筆定義（例如 `juniper_i698_load`），在 Admin UI 列表、詳情頁看得到目標表、允許欄位、刪除範圍與呼叫範例。
2. **可約束** —— 由 Hub 強制「只能寫這些欄位」、「只能依這個鍵刪除」，而不是信任呼叫端送來的任意 `where`。
3. **型別正確** —— 日期欄位由 Hub 轉成 driver 原生型別綁定，不再依賴 DB session 的 `NLS_DATE_FORMAT`。

## 背景：現況與問題

外部系統寫入目前只有通用端點 `POST /{db}/writes/execute` 與 `POST /{db}/writes/batch`（`app/api/v1/writes.py`、`app/services/write_service.py`）。授權是 `table:<table>` + `write`。Juniper i698 整合（2026-10-07 UAT）實際踩到的問題：

| 問題 | 現況行為 | 實際影響 |
|---|---|---|
| Admin UI 看不到寫入 | 寫入沒有定義，只有 table 權限 | 使用者找不到「Juniper 的寫入 API」 |
| 刪除範圍不受控 | `where` 由呼叫端決定；省略 `where` 即 `DELETE ... WHERE 1=1` | 依 `FROM_ORG+INVOICE+ORDER+LINE` 刪除時，覆蓋了一筆 ERP 既有資料（EXP26060001） |
| 欄位不受控 | 欄位清單取自第一筆 row 的 key，任何欄位都可寫 | DAG 寫錯欄位不會被 Hub 擋下 |
| 日期 | JSON 字串原樣綁定，靠 Oracle 隱式轉換（`DD-MON-RR`） | `2026/04/16`、ISO 格式會 ORA-01861；呼叫端被迫送 `16-APR-2026` |
| 權限不綁 alias | `table:SOM_I698_REPORT` 對所有 alias 生效 | 目前只靠 DB 帳號權限擋住 |
| 稽核不分操作 | `result_rows` 只有整批總數 | 查「刪了幾筆、插了幾筆」要靠 Oracle flashback |

## 已確認的決策

| 決策 | 選擇 |
|---|---|
| 定義存放 | 新表 `api_hub_write_definitions`，與 SQL 定義同樣以 `(name, db_alias)` 唯一 |
| 寫入模式（v1） | `insert`（純新增）、`replace`（依鍵刪除後新增，同一交易） |
| 刪除範圍 | 只能依定義的 `key_columns` 等值刪除；鍵值必填、不可為 null/空字串 |
| 鍵一致性 | `replace` 模式下，每筆 row 的鍵欄位值必須等於請求的 `key`，確保「刪的」與「寫的」是同一範圍 |
| 欄位 | 定義列出允許欄位（allowlist）與型別；request 出現未宣告欄位 → 400 |
| 型別轉換 | `string` / `number` / `date` / `datetime`，由 Hub 轉成 Python 原生型別後綁定 |
| 權限 | `write:<name>` + action `write`（不與 `table:*` 重疊） |
| 端點 | `POST /{db}/writes/named/execute`；CRUD 在 `/{db}/writes/definitions` |
| 執行 | 同步；提供 `dry_run`（執行後 rollback，回報筆數） |
| 載入方式 | 每次呼叫直接讀平台 store，不做 in-memory registry |
| 通用寫入端點 | 保留不動（向下相容），遷移完成後收回呼叫端的 `table:` 權限 |

### 為何不做 in-memory registry

`SqlRegistry` 存在是因為查詢量大（每月約 91k 次 REST 呼叫）且 MCP 目錄要列舉定義。寫入量小（Juniper 每批一次），每次多一個以主鍵讀平台 store 的查詢可以忽略；換來的是 CRUD 後不需要 reload、多 worker 不會不同步。

### 為何 `replace` 要求 row 的鍵值等於 `key`

`replace` 的承諾是「重送同一個檔案是冪等的」。若只約束刪除條件、不約束插入的資料，呼叫端可以送 `key={SOURCE_FILE: A}` 但插入 `SOURCE_FILE=B` 的資料，重送 A 時就刪不到 B，冪等性破功。兩邊綁同一個鍵，刪除範圍就恰好等於這個呼叫曾寫入的範圍 —— 也就不會再刪到 ERP 或其他寫入者的資料（Juniper EXP26060001 事件的根因）。

### 為何型別由 Hub 轉換

Oracle 的隱式轉換取決於 session 的 `NLS_DATE_FORMAT`，而這個值屬於資料源、不屬於 Hub，換帳號或換 DB 就可能不同。Hub 把 `"2026-04-16"` 解析成 `datetime.date` 再綁定，`oracledb` / `psycopg2` / `pymssql` 都原生支援，三種 engine 行為一致，也不需要在 dialect 層拼 `TO_DATE`。

## 非目標

- 不改、不移除通用 `/writes/execute`、`/writes/batch`
- 不支援 `update` / `upsert`（MERGE）模式 —— 等有需求再加，模式欄位已預留
- 不支援非同步（Celery）執行 —— 具名寫入的呼叫端需要同步得知結果
- 不提供多表寫入（一個定義對一張表）
- 不輸出成 MCP tool 或 OpenAPI tool —— 寫入不應由 LLM 驅動
- 不由 Hub 自動填 `CREATION_DATE` 之類的欄位 —— 交給 DB `DEFAULT`（請求不送該欄即可）

---

## W1 — 資料模型

`app/models/write_definition.py`：

```python
class WriteDefinitionModel(Base):
    __tablename__ = "api_hub_write_definitions"
    __table_args__ = (UniqueConstraint("name", "db_alias", name="uq_write_def_name_db_alias"),)

    id           = Column(Integer, primary_key=True, autoincrement=True)
    name         = Column(String(200), nullable=False, index=True)
    db_alias     = Column(String(64), nullable=False, index=True)
    group_name   = Column(String(100), nullable=False, server_default="", index=True)
    description  = Column(Text, nullable=False, server_default="")
    table_name   = Column(String(128), nullable=False)       # 依 dialect.table_name_regex() 驗證
    mode         = Column(String(20), nullable=False)         # "insert" | "replace"
    columns      = Column(JSONB, nullable=False)              # 見下
    key_columns  = Column(JSONB, nullable=False, server_default="[]")  # replace 模式必填
    max_rows     = Column(Integer, nullable=False, server_default="10000")
    permission   = Column(String(200), nullable=False)        # 預設 write:<name>
    enabled      = Column(Boolean, nullable=False, server_default="true")
    created_at / updated_at
```

`columns` 範例：

```json
[
  {"name": "ORGANIZATION_CODE", "type": "string", "required": true},
  {"name": "RECEIPT_DATE",      "type": "date"},
  {"name": "UNITS_EXPECTED",    "type": "number"},
  {"name": "SOURCE_FILE",       "type": "string", "required": true}
]
```

建表走既有 `scripts/init_db.py` 的 `Base.metadata.create_all`（新表，不需要 `ALTER`）。

**定義層驗證**（建立／修改時，`app/schemas/write_definition.py` + service）：

- `name`、欄位名、`key_columns` 符合 `^[A-Za-z_][A-Za-z0-9_]{0,127}$`（與 `OrderBySchema` 相同）
- `table_name` 符合該 alias dialect 的 `table_name_regex()`
- 欄位名不重複（大小寫不敏感）
- `mode=replace` 時 `key_columns` 非空，且每個鍵都在 `columns` 裡並強制視為 `required`
- `mode=insert` 時 `key_columns` 必須為空
- **對實際資料表驗證**：用 `schema_service.describe_table(table, db_alias)` 取欄位清單，宣告欄位不存在 → 400 並列出不存在的欄位。這同時驗證了「該 alias 的 DB 帳號看得到這張表」。表還沒建好時可帶 `?skip_table_check=true`，但詳情頁會顯示未驗證的警告。

## W2 — 執行端點

```
POST /api/v1/{db}/writes/named/execute
```

Request：

```json
{
  "name": "juniper_i698_load",
  "key":  {"SOURCE_FILE": "20261007_074842_SERCOMM_PH_POTrace.csv"},
  "rows": [
    {"ORGANIZATION_CODE": "EIAMSFGI", "INVOICE_NO": "MOCKTEST-PH-0001",
     "RECEIPT_DATE": "2026-10-07", "UNITS_EXPECTED": 15,
     "SOURCE_FILE": "20261007_074842_SERCOMM_PH_POTrace.csv"}
  ],
  "dry_run": false
}
```

Response：

```json
{
  "success": true, "name": "juniper_i698_load", "mode": "replace",
  "deleted_rows": 2, "inserted_rows": 2,
  "committed": true, "dry_run": false
}
```

處理順序（`app/services/named_write_service.py`）：

1. 依 `(name, db)` 讀定義；不存在 404，`enabled=false` → 409 `"disabled"`（與查詢一致，不混成 not found）
2. `PermissionChecker(definition.permission, "write")`
3. 驗證請求（任一失敗 → 400，訊息指出第幾筆、哪個欄位）：
   - `rows` 1..`max_rows` 筆；`replace` 允許 0 筆（＝「這個檔案現在是空的」，只刪不插）
   - 未宣告欄位 → 400；`required` 欄位缺漏或為 null → 400
   - 型別轉換：`string` 原樣（拒絕非字串）；`number` 接受 JSON 數字或數字字串 → `Decimal`；`date` 接受 `YYYY-MM-DD`、`YYYY/MM/DD` → `datetime.date`；`datetime` 接受 ISO 8601 → `datetime.datetime`
   - `replace`：`key` 的欄位集合必須等於 `key_columns`，值非 null、非空字串；每筆 row 的鍵欄位值必須等於 `key`
   - `insert`：不得帶 `key`
4. 組 SQL —— 欄位清單來自**定義**（不是請求），所以不會有使用者字串進入 SQL 識別字：
   - 欄位集合以「宣告欄位 ∩ 這批 rows 實際出現的欄位」決定，且要求每筆 row 欄位集合相同（避免第一筆決定欄位的舊問題；不一致 → 400）
   - 請求未送的欄位不出現在 INSERT 中 → DB `DEFAULT` 生效（例如 `CREATION_DATE DEFAULT SYSDATE`）
   - `replace`：`DELETE FROM <table> WHERE k1 = :k1 AND ...`，然後 `executemany` INSERT
5. 同一 connection、同一交易執行；`dry_run` 則 rollback。失敗 rollback 並回傳失敗階段（`delete` / `insert`）與 driver 錯誤；ORA-01031 / ORA-00942 對應 403/400 的可讀訊息。
6. `record_audit(request, "write", definition.name, params={"key":…, "rows":…, "dry_run":…}, result_rows=deleted+inserted)`，並另外把 `deleted_rows` / `inserted_rows` 放進 params 的 `result` 子物件 —— 稽核表就能直接看出各自筆數。`params` 依既有政策不遮罩不截斷。

`/writes/execute`、`/writes/batch` 的程式碼不動；共用的只有 `get_connection`、dialect 的 bind placeholder。

## W3 — 定義 CRUD API

與 SQL 定義對稱，superuser only：

| Method | Path | 說明 |
|---|---|---|
| GET | `/{db}/writes/definitions` | 列表（含 disabled，供 Admin UI） |
| GET | `/{db}/writes/definitions/{name}` | 單筆 |
| POST | `/{db}/writes/definitions` | 建立；`permission` 空白時自動填 `write:<name>` |
| PUT | `/{db}/writes/definitions/{name}` | 修改（`name`、`db_alias` 不可改） |
| DELETE | `/{db}/writes/definitions/{name}` | 刪除 |
| POST | `/{db}/writes/definitions/{name}/validate` | 重跑 W1 的資料表驗證 |

CRUD 皆 `record_audit(..., "definition", ...)`，與 SQL 定義相同。

## W4 — Admin UI

`app/admin/js/write_definitions.js`（獨立檔案，理由同 `usage_report.js`：`app.js` 已 2900 行），sidebar「Data Services」新增 **Write Definitions**，放在 SQL Definitions 下方。

- **列表**：沿用 SQL Definitions 的 `.split-list`（左 group、右定義，皆 client-side 依名稱排序），受頂部 DB selector 影響。欄位：Name、Table、Mode、Key、Enabled。
- **詳情頁**：目標 alias／表、模式、鍵、欄位表（名稱／型別／必填）、權限字串、資料表驗證狀態，以及一段可複製的 request body 範例（從定義生成）。
- **表單**：欄位清單可「從資料表載入」（呼叫既有 schema 端點），使用者勾選要開放的欄位、設型別與必填，避免手打。
- **Dry run**：詳情頁貼上 JSON 執行 `dry_run=true`，顯示 `deleted_rows` / `inserted_rows`，不落地。
- 「誰能用」：詳情頁列出持有該 `permission` 的角色（讀既有 roles API，client-side 過濾）。

## W5 — 權限模型

- 新資源前綴 `write:`，action `write`。`has_permission` 不需修改（既有字串比對＋尾端 `*` 萬用字元）。
- `write:*` 可授予「所有具名寫入」；**`table:*` 不涵蓋 `write:`**，反之亦然 —— 收回通用寫入權限不影響具名寫入。
- 權限仍不綁 alias（與 `query:` 一致），但定義本身綁 alias：`write:juniper_i698_load` 只存在於 `ebs_ysc`，在其他 alias 呼叫會 404。

## W6 — Juniper 遷移（首個使用者）

1. 部署本功能到 apihub-test。
2. 建立定義 `juniper_i698_load`：alias `ebs_ysc`、表 `SOM_I698_REPORT`、mode `replace`、key `[SOURCE_FILE]`；欄位為 i698 的 15 個業務欄位 + `RECEIPT_QUANTITY`、`SOURCE_FILE`，**不含** `CREATION_DATE`、`LAST_UPDATE_DATE`（交給 `DEFAULT SYSDATE`）；`RECEIPT_DATE` 型別 `date`。
3. 角色 `juniper_airflow` 加 `write:juniper_i698_load`。
4. Airflow 改呼叫 `/ebs_ysc/writes/named/execute`，日期改送 `YYYY-MM-DD`；以 `dry_run` 先驗證，再重跑 TC-UAT-04/05/09。
5. 驗證通過後**收回** `table:SOM_I698_REPORT` —— 從此 Airflow 無法用通用端點對此表做任意刪除。
6. PROD 上線時直接以具名寫入建置，不再授予 `table:` 權限。

## 測試

- `tests/test_named_write_service.py`：定義驗證（regex、mode/key 組合、重複欄位）、請求驗證（未宣告欄位、required、型別轉換、鍵不一致、空 key、rows 欄位集合不一致、`max_rows`）、產生的 SQL 形狀（mock cursor，比照現有 write 測試）、`dry_run` 會 rollback、失敗會 rollback。
- `tests/test_write_definitions_api.py`：CRUD 權限（非 superuser 403）、`write:<name>` 授權、disabled → 409、跨 alias → 404、稽核內容含 `deleted_rows`/`inserted_rows`。
- `tests/e2e/write-definitions.spec.js`：建立 → 詳情 → dry run（PG 測試容器上的表）→ 停用。

## 文件

- `README.md`、`docs/system_specification.md`（`update-spec` skill）
- `CLAUDE.md` 架構節新增一段 named writes，並鏡像到 `AGENTS.md`
- `app/admin/CLAUDE.md`：新頁面與 `write_definitions.js` 的分檔說明

## 待決事項

1. `replace` 允許 0 筆 rows（只刪不插）是否預設開啟？建議開啟 —— 上游檔案被清空時，這才是正確的同步結果；需要時可在定義加 `allow_empty: false`。
2. 是否需要「每次請求最多刪除 N 筆」的保險絲（例如刪除筆數 > 插入筆數 × 2 就拒絕）？v1 先不做，以 `dry_run` 與稽核觀察。
3. PROD 是否要以設定 `GENERIC_WRITES_ENABLED=false` 完全關閉通用寫入端點？牽涉其他既有呼叫端，需先盤點 audit 中 `/writes/*` 的使用者。
