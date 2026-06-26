# Oracle API Hub — 權限角色與職責 (Permission R&R)

> 本文件以**後端實際授權檢查**為準（非僅 Admin UI 的按鈕顯示）。
> RBAC 比對演算法、萬用字元語意等機制細節，請見 [`system_specification.md` 第 7 章「認證與授權」](./system_specification.md#7-認證與授權)。
> 最後同步：2026-06-26。

---

## 1. 授權層級（四層）

| 層級 | 判定方式 | 來源 |
|------|----------|------|
| **未驗證 Anonymous** | 無有效 token | — |
| **一般使用者 User** | `get_current_user`（token 有效且 `is_active=True`） | `auth_service.py:23` |
| **被授權使用者 Authorized** | `PermissionChecker(resource, action)` 通過 | `auth_service.py:52` |
| **超級管理員 Superuser** | `is_superuser=True`，通過 `_require_superuser` / `get_current_active_superuser`，並**繞過所有 `PermissionChecker`** | `auth_service.py:46`、`queries.py:31` |

> **登入前提：** `authenticate_user` 會驗證帳密**且**帳號需為啟用中（`is_active=True`）；
> 停用帳號連 token 都拿不到，一律回傳通用 `401`，不洩漏帳號狀態。來源 `auth_service.py:16`。

---

## 2. 端點 × 所需層級 對照表

### 🟢 公開（免登入）

| 端點 | 功能 | 來源 |
|------|------|------|
| `GET /health` | 健康檢查 | `health.py:8` |
| `POST /api/v1/auth/token` | 登入（需正確帳密 + 啟用中帳號） | `auth.py:30` |

### 🔵 任何登入帳號（僅需 `get_current_user`）

| 端點 | 功能 | 來源 |
|------|------|------|
| `GET /auth/me` | 看自己的帳號資料 | `auth.py:39` |
| `GET /{db}/databases` | 列出資料庫清單 | `databases.py:23` |
| `GET /{db}/queries/definitions` | 列出 SQL 定義（**清單，不含 SQL 內文**） | `queries.py:55` |
| `GET /{db}/schemas/describe/{table}` | 看資料表 schema | `schemas.py:16` |
| `GET /tasks/{task_id}` | 查非同步任務狀態 | `writes.py:28` |

### 🟡 需 RBAC 權限（`PermissionChecker`；superuser 全放行）

| 端點 | 所需權限 `resource:action` | 來源 |
|------|---------------------------|------|
| `POST /{db}/queries/execute` | `query:<name>` + **read**（取定義上的 `permission` 欄位） | `queries.py:366` |
| `POST /{db}/procedures/execute` | `procedure:<name>` + **execute** | `procedures.py:17` |
| `POST /{db}/procedures/execute-async` | `procedure:<name>` + **execute** | `procedures.py:44` |
| `POST /{db}/writes/execute` | `table:<table>` + **write** | `writes.py:44` |
| `POST /{db}/writes/batch` | `table:<table>` + **write** | `writes.py:89` |

### 🔴 僅 Superuser（`_require_superuser` / `get_current_active_superuser`）

| 端點群 | 功能 | 來源 |
|--------|------|------|
| `POST/GET/PUT/DELETE /auth/users`、`PATCH .../{id}/password` | 使用者管理 | `auth.py:46–126` |
| `POST/GET/PUT/DELETE /auth/roles` | 角色管理 | `auth.py:131–186` |
| `POST/GET/PUT/DELETE /auth/permissions` | 權限管理 | `auth.py:191–242` |
| `POST/PUT/DELETE /{db}/queries/definitions` | **建立／修改／刪除** SQL 定義 | `queries.py:249,305,339` |
| `GET /{db}/queries/definitions/{name}` | **看單筆定義（含 SQL 內文）** ⚠️ | `queries.py:285,293` |
| `.../definitions/reload`、`import-yaml`、`import-yaml-file`、`export-yaml` | 定義熱重載 / YAML 匯入匯出 | `queries.py:76,88,160,191` |

---

## 3. RBAC 權限格式速查 `resource:action`

| 項目 | 內容 |
|------|------|
| **action** | `read`（查詢）、`execute`（程序/函式）、`write`（寫入） |
| **resource 前綴** | `query:<名稱>`、`procedure:<名稱>`、`table:<表名>` |
| **萬用字元** | 完整類別 `query:*`；前綴 `query:RD_TC*` → 比對 `query:RD_TC` 開頭；superuser 無條件通過 |

> 詳細比對規則與範例表見 `system_specification.md` §7.3–7.4。

---

## 4. 角色職責摘要（Roles & Responsibilities）

| 角色 | 可做什麼 | 不可做什麼 |
|------|----------|------------|
| **一般使用者** | 登入；看自己的帳號；列出資料庫 / SQL 定義清單 / schema；查任務狀態；在**被授權範圍內**執行 query / procedure / write | 看單筆定義 SQL 內文；任何定義 CRUD;使用者 / 角色 / 權限管理 |
| **被授權使用者** | 一般使用者全部 ＋ 依持有的 `resource:action` 執行對應 query / procedure / write | 同上所有 superuser-only 操作 |
| **Superuser** | 全系統:使用者 / 角色 / 權限管理、SQL 定義 CRUD 與匯入匯出、看任意定義內文、無視 RBAC 執行任何 query / procedure / write | —（系統最高權限） |

---

## 5. 設計注意事項

1. **看「清單」與看「單筆」分屬不同層級。** `GET .../definitions`（清單)任何登入者皆可,但 `GET .../definitions/{name}`(含 SQL 內文)為 superuser only(`queries.py:293`)。Admin UI 的 `viewSqlDef` 即依此:superuser 取完整詳情,非 superuser 退回清單快取。

2. **沒有「中階管理員」角色。** 所有管理類操作(使用者/角色/權限/定義 CRUD)一律 all-or-nothing 落在 superuser。若需「能管定義但不能管使用者」這類分權,目前模型不支援,需新增更細的權限檢查(例如把定義 CRUD 改為一個 `definition:* + manage` 權限,而非寫死 `_require_superuser`)。

3. **資料來源密碼不在此授權範圍。** RBAC 控管的是「呼叫哪個 API / 執行哪個定義」;能否真的連到目標資料庫,取決於 `DB_{ALIAS}_PASSWORD` 等連線設定,屬基礎設施層,與使用者權限無關。
