# Oracle API Hub — 系統規格書

| 項目 | 內容 |
|------|------|
| 文件名稱 | Oracle RESTful API Hub 系統規格書 |
| 版本 | 1.1.0 |
| 目標資料庫 | Oracle Database 11g (11.1.0.4) |
| 框架 | FastAPI (Python 3.10+) |
| 文件日期 | 2026-03-01 |

---

## 目錄

1. [系統概述](#1-系統概述)
2. [系統架構](#2-系統架構)
3. [技術堆疊](#3-技術堆疊)
4. [模組規格](#4-模組規格)
5. [API 端點規格](#5-api-端點規格)
6. [資料庫設計](#6-資料庫設計)
7. [認證與授權](#7-認證與授權)
8. [工作佇列設計](#8-工作佇列設計)
9. [SQL 定義系統](#9-sql-定義系統)
10. [管理介面 (Admin UI)](#10-管理介面-admin-ui)
11. [中介層 (Middleware)](#11-中介層-middleware)
12. [部署架構](#12-部署架構)
13. [安全性設計](#13-安全性設計)
14. [設定參數一覽](#14-設定參數一覽)
15. [專案目錄結構](#15-專案目錄結構)

---

## 1. 系統概述

### 1.1 目的

Oracle API Hub 是一套通用型 RESTful API 平台，將 Oracle Database 的 Table、View、Stored Procedure 及 Function 封裝為標準化的 REST API，讓異質系統能以統一的 JSON 格式取用資料庫資源。

### 1.2 設計目標

| 目標 | 說明 |
|------|------|
| 通用性 | 透過 YAML 定義 SQL，無需撰寫程式碼即可新增查詢端點 |
| 開放資料格式 | 查詢結果回傳結構化 JSON（含欄位名稱、分頁資訊、資料列） |
| 程序呼叫 | 支援呼叫 Oracle Function / Procedure（含 IN/OUT 參數） |
| 資料寫入 | 支援 INSERT / UPDATE / DELETE，並可切換同步或非同步模式 |
| 負載緩解 | 透過 Celery + Redis 工作佇列將寫入與程序呼叫非同步化 |
| 權限控管 | JWT 認證搭配 RBAC（角色基礎存取控制），支援萬用字元權限 |
| 管理介面 | 內建 Web 管理 UI（SPA），提供使用者/角色/權限 CRUD、查詢測試、任務監控 |
| 11g 相容 | 使用 cx_Oracle SessionPool 與 ROWNUM 分頁，完全相容 Oracle 11.1.0.4 |

### 1.3 系統邊界

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Oracle API Hub                                │
│                                                                      │
│  ┌──────────┐    ┌───────────┐    ┌──────────────┐    ┌──────────┐  │
│  │ 外部系統  │───▶│  FastAPI   │───▶│   Service     │───▶│  Oracle  │  │
│  │ (Client)  │◀──│  Router    │◀──│   Layer       │◀──│  11g DB  │  │
│  └──────────┘    └───────────┘    └──────┬───────┘    └──────────┘  │
│                                          │                           │
│                                   ┌──────▼───────┐                   │
│                                   │ Celery Worker │                   │
│                                   │ (Redis Queue) │                   │
│                                   └──────────────┘                   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. 系統架構

### 2.1 總體架構圖

```
                    ┌─────────────────────────────────────────────────────┐
                    │                   API Gateway                       │
  ┌──────────┐      │  ┌──────────────────────────────────────────────┐  │
  │          │      │  │              Middleware Layer                 │  │
  │  Client  │─────▶│  │  ┌────────────┐  ┌─────────────────────┐   │  │
  │  App /   │      │  │  │   CORS     │  │  Rate Limiter       │   │  │
  │  Browser │◀─────│  │  │ Middleware  │  │  (Sliding Window)   │   │  │
  │          │      │  │  └────────────┘  └─────────────────────┘   │  │
  └──────────┘      │  └──────────────────────────────────────────────┘  │
                    │                                                     │
                    │  ┌──────────────────────────────────────────────┐  │
                    │  │              API Layer (v1)                   │  │
                    │  │  ┌──────┐ ┌───────┐ ┌──────┐ ┌──────┐ ┌───────┐ │  │
                    │  │  │ Auth │ │Queries│ │Procs │ │Writes│ │Schemas│ │  │
                    │  │  │Router│ │Router │ │Router│ │Router│ │Router │ │  │
                    │  │  └──┬───┘ └──┬────┘ └──┬───┘ └──┬───┘ └──┬────┘ │  │
                    │  └─────┼────────┼─────────┼────────┼────────┼──────┘  │
                    │         │          │         │         │          │
                    │  ┌──────▼──────────▼─────────▼─────────▼──────┐  │
                    │  │             Service Layer                   │  │
                    │  │  ┌─────────┐ ┌──────────┐ ┌────────────┐  │  │
                    │  │  │  Auth   │ │  Query   │ │ Procedure  │  │  │
                    │  │  │ Service │ │ Service  │ │  Service   │  │  │
                    │  │  ├─────────┤ ├──────────┤ ├────────────┤  │  │
                    │  │  │  SQL    │ │  Write   │ │ Permission │  │  │
                    │  │  │Registry │ │ Service  │ │  Checker   │  │  │
                    │  │  ├─────────┤ └──────────┘ └────────────┘  │  │
                    │  │  │ Schema  │                               │  │
                    │  │  │ Service │                               │  │
                    │  │  └─────────┘                               │  │
                    │  └──────────────────┬────────────────────────┘  │
                    │                     │                            │
                    │         ┌───────────┼───────────┐               │
                    │         ▼           ▼           ▼               │
                    │  ┌───────────┐ ┌─────────┐ ┌──────────┐        │
                    │  │cx_Oracle  │ │SQLAlchemy│ │  Celery  │        │
                    │  │  Pool     │ │  ORM     │ │  Tasks   │        │
                    │  └─────┬─────┘ └────┬────┘ └─────┬────┘        │
                    └────────┼────────────┼────────────┼──────────────┘
                             │            │            │
                             ▼            ▼            ▼
                    ┌─────────────┐                ┌────────┐
                    │   Oracle    │                │  Redis  │
                    │  Database   │                │ Broker  │
                    │  11.1.0.4   │                └────────┘
                    └─────────────┘
```

### 2.2 分層說明

| 層級 | 職責 | 對應模組 |
|------|------|----------|
| **中介層 (Middleware)** | CORS 跨域、速率限制 | `app/middleware/rate_limiter.py` |
| **路由層 (Router)** | HTTP 端點定義、請求驗證 | `app/api/v1/*.py` |
| **服務層 (Service)** | 業務邏輯、SQL 執行、權限檢查 | `app/services/*.py` |
| **資料存取層 (Data)** | 連線池管理、ORM Session | `app/db/*.py` |
| **任務層 (Task)** | 非同步工作佇列 | `app/tasks/*.py` |
| **模型層 (Model)** | ORM 模型定義 | `app/models/*.py` |
| **綱要層 (Schema)** | 請求/回應資料結構 | `app/schemas/*.py` |

---

## 3. 技術堆疊

### 3.1 核心依賴

| 元件 | 版本需求 | 用途 |
|------|----------|------|
| Python | >= 3.10 | 執行環境 |
| FastAPI | >= 0.110.0, < 1.0 | Web 框架 |
| Uvicorn | >= 0.27.0 | ASGI 伺服器 |
| cx_Oracle | >= 8.3.0 | Oracle 資料庫驅動（與 11g 相容） |
| SQLAlchemy | >= 2.0.0, < 3.0 | ORM（僅用於使用者/角色管理表） |
| Pydantic | >= 2.0.0 | 資料驗證與序列化 |
| pydantic-settings | >= 2.0.0 | 環境變數組態管理 |
| python-jose | >= 3.3.0 | JWT Token 編解碼 |
| passlib[bcrypt] | >= 1.7.4 | 密碼雜湊（bcrypt） |
| Celery[redis] | >= 5.3.0 | 分散式工作佇列 |
| Redis | >= 5.0.0 | 訊息佇列 Broker & 結果儲存 |
| PyYAML | >= 6.0 | SQL 定義檔解析 |
| Alembic | >= 1.13.0 | 資料庫遷移管理 |

### 3.2 開發依賴

| 元件 | 用途 |
|------|------|
| pytest >= 8.0 | 單元測試框架 |
| pytest-cov >= 4.1 | 測試覆蓋率 |
| ruff >= 0.3.0 | 程式碼檢查與格式化 |

### 3.3 基礎設施

| 元件 | 用途 |
|------|------|
| Docker (Python 3.11-slim) | 容器化部署 |
| Oracle Instant Client | cx_Oracle 連線所需之客戶端函式庫 |
| Redis 7 (Alpine) | Celery Broker + 結果後端 |
| Flower | Celery 監控儀表板（port 5555） |

---

## 4. 模組規格

### 4.1 核心組態模組 — `app/core/config.py`

**類別：`Settings`** (繼承 `pydantic_settings.BaseSettings`)

從 `.env` 檔或環境變數載入所有組態。支援的參數見 [第 13 節](#13-設定參數一覽)。

```python
class Settings(BaseSettings):
    oracle_dsn: str = "localhost:1521/orcl"
    oracle_user: str = "api_user"
    oracle_password: str = "changeme"
    oracle_pool_min: int = 2
    oracle_pool_max: int = 10
    oracle_pool_increment: int = 1
    secret_key: str = "replace-with-a-secure-random-string"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    api_title: str = "Oracle API Hub"
    api_version: str = "1.0.0"
    api_prefix: str = "/api/v1"
    debug: bool = False
    rate_limit_per_minute: int = 60
    sql_definitions_dir: str = "app/sql_definitions"
```

### 4.2 安全模組 — `app/core/security.py`

| 函式 | 簽章 | 說明 |
|------|------|------|
| `verify_password` | `(plain: str, hashed: str) -> bool` | 以 bcrypt 驗證密碼 |
| `get_password_hash` | `(password: str) -> str` | 產生 bcrypt 雜湊值 |
| `create_access_token` | `(data: dict, expires_delta?) -> str` | 產生 HS256 簽章之 JWT Token，預設 60 分鐘過期 |

### 4.3 Oracle 連線池 — `app/db/oracle_pool.py`

使用 `cx_Oracle.SessionPool` 管理連線生命週期。

| 函式 | 說明 |
|------|------|
| `init_pool()` | 啟動時初始化 SessionPool（Singleton） |
| `close_pool()` | 關閉時釋放所有連線 |
| `get_connection()` | Context Manager，取得連線並自動歸還 |
| `get_cursor()` | Context Manager，取得 Cursor 並自動關閉 |

**連線池組態：**

```python
cx_Oracle.SessionPool(
    user=..., password=..., dsn=...,
    min=2, max=10, increment=1,
    encoding="UTF-8",
    threaded=True,                          # 執行緒安全
    getmode=cx_Oracle.SPOOL_ATTRVAL_WAIT,   # 連線用盡時等待（不拋例外）
)
```

### 4.4 SQLAlchemy Session — `app/db/session.py`

僅用於管理系統內部的使用者、角色、權限 ORM 表。動態 SQL（查詢、Procedure、DML）一律使用 cx_Oracle 連線池。

| 元件 | 說明 |
|------|------|
| `engine` | SQLAlchemy Engine，`pool_pre_ping=True` |
| `SessionLocal` | Session 工廠，`autocommit=False` |
| `Base` | ORM 宣告基底類別 |
| `get_db()` | FastAPI Dependency，yield Session 並自動 close |

### 4.5 SQL 登錄服務 — `app/services/sql_registry.py`

**資料類別：**

| 類別 | 欄位 | 說明 |
|------|------|------|
| `ParamDef` | `name`, `type`, `required`, `description` | 參數定義 |
| `SqlDefinition` | `name`, `description`, `sql`, `permission`, `params` | 查詢定義 |

**類別 `SqlRegistry`（Singleton）：**

| 方法 | 說明 |
|------|------|
| `load(directory?)` | 從指定目錄載入所有 `*.yaml` / `*.yml` 檔 |
| `get(name) -> SqlDefinition?` | 依名稱查詢定義 |
| `list_all() -> list[SqlDefinition]` | 取得所有定義 |
| `reload()` | 清空並重新載入（熱更新） |

### 4.6 查詢執行服務 — `app/services/query_service.py`

**函式 `execute_query`：**

```
(definition, params, fetch_mode, *, page, page_size, batch_size, batch_offset, top_n)
  -> dict[str, Any]   # 包含 columns, data, total, 及模式特有的分頁資訊
```

**支援五種資料擷取模式 (fetch_mode)：**

| 模式 | 參數 | 說明 | 是否計算 COUNT |
|------|------|------|----------------|
| `page` | `page` + `page_size` | 傳統分頁（預設） | 是 |
| `all` | — | 回傳全部資料 | 是 |
| `batch` | `batch_size` + `batch_offset` | 固定批次大小 + 偏移量，適合游標式逐批取用 | 是 |
| `top_n` | `top_n` | 取前 N 筆（不做 COUNT，效能最佳） | 否 |
| `one` | — | 僅取一筆，無資料時回傳 400 | 否 |

**Oracle 11g 相容分頁機制（`page` / `batch` 模式使用 ROWNUM）：**

```sql
SELECT * FROM (
    SELECT inner_.*, ROWNUM rnum_ FROM (
        {原始 SQL}
    ) inner_
    WHERE ROWNUM <= :_max_row
)
WHERE rnum_ > :_min_row
```

**`top_n` / `one` 模式使用簡化 ROWNUM：**

```sql
SELECT * FROM ({原始 SQL}) WHERE ROWNUM <= :_top_n
```

> 不使用 `OFFSET / FETCH`，因 Oracle 11g 不支援。

**共用處理流程：**
1. 驗證必填參數（缺少則拋 `ValueError`）
2. 依模式決定是否執行 `SELECT COUNT(*) FROM (sql)`
3. 依模式包裝 ROWNUM 或 TOP-N SQL
4. 將 `cx_Oracle.LOB` 自動轉換為字串
5. 過濾掉 `RNUM_` 輔助欄位

### 4.7 程序執行服務 — `app/services/procedure_service.py`

| 函式 | 說明 |
|------|------|
| `execute_procedure(name, params, out_params, is_function)` | 主入口，回傳 `{"result": ..., "out_params": {...}}` |
| `_call_function(cursor, name, params)` | 使用 `cursor.callfunc()` 呼叫 Function |
| `_call_procedure(cursor, name, params, out_param_names)` | 使用 `cursor.callproc(keywordParameters=...)` 呼叫 Procedure |
| `_convert(value)` | 將 `cx_Oracle.LOB` 轉為可序列化類型 |

**OUT 參數處理：**
- 以 `cursor.var(cx_Oracle.DB_TYPE_VARCHAR, 4000)` 建立 OUT 變數
- Procedure 執行後透過 `var.getvalue()` 取值

**交易控制：**
- Procedure 成功後 `conn.commit()`
- 例外時 `conn.rollback()`

### 4.8 寫入服務 — `app/services/write_service.py`

| 函式 | 說明 |
|------|------|
| `validate_table_name(table)` | 正則驗證表名，防止 SQL injection |
| `execute_write(operation, table, data, where)` | DML 主入口，回傳受影響筆數 |
| `execute_batch_write(table, operations)` | 批次交易寫入，所有操作在同一交易中執行；任一失敗即全部回滾 |
| `_do_insert(cursor, table, data)` | 使用 `executemany` 批次新增 |
| `_do_update(cursor, table, data, where)` | 逐筆更新，SET 與 WHERE 均為綁定參數 |
| `_do_delete(cursor, table, where)` | 條件刪除 |

**批次交易寫入 (`execute_batch_write`)：**

在單一資料庫連線內依序執行多個 DML 操作，全部成功才 `commit`，任一失敗即 `rollback` 並拋出 `BatchWriteError`（攜帶已執行的部分結果與失敗索引）。

```python
def execute_batch_write(table, operations) -> dict:
    """回傳 {total_affected, operation_count, committed, results}"""
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            for idx, op in enumerate(operations):
                # 依 op["operation"] 分派 insert/update/delete
                ...
            conn.commit()       # 全部成功 → 提交
        except Exception:
            conn.rollback()     # 任一失敗 → 回滾
            raise BatchWriteError(...)
```

**`BatchWriteError` 例外類別：**

| 屬性 | 型態 | 說明 |
|------|------|------|
| `message` | `str` | 錯誤訊息 |
| `failed_index` | `int` | 失敗的操作索引（0-based） |
| `committed` | `bool` | 交易是否已提交（失敗時永遠為 `False`） |
| `results` | `list[dict]` | 失敗前已執行的操作結果 |

**表名驗證正則：**
```
^[A-Za-z_][A-Za-z0-9_$.#]{0,127}$
```

**參數化 SQL 範例：**
```sql
-- INSERT
INSERT INTO employees (emp_name, dept) VALUES (:emp_name, :dept)

-- UPDATE (SET 使用 :set_ 前綴, WHERE 使用 :wh_ 前綴)
UPDATE employees SET emp_name = :set_emp_name WHERE emp_id = :wh_emp_id

-- DELETE
DELETE FROM employees WHERE emp_id = :wh_emp_id
```

### 4.9 表結構探查服務 — `app/services/schema_service.py`

透過 Oracle 資料字典（`ALL_TAB_COLUMNS`、`ALL_CONSTRAINTS`、`ALL_CONS_COLUMNS`）動態讀取目標表或 View 的欄位定義與主鍵資訊。

| 函式 | 說明 |
|------|------|
| `describe_table(table)` | 回傳目標表/View 的所有欄位 metadata 與主鍵列表 |
| `validate_table_name(table)` | 重用 write_service 的表名正則驗證 |

**回傳格式：**

```json
{
  "table": "HR.EMPLOYEES",
  "owner": "HR",
  "table_name": "EMPLOYEES",
  "column_count": 8,
  "primary_keys": ["EMP_ID"],
  "columns": [
    {
      "name": "EMP_ID",
      "data_type": "NUMBER",
      "data_length": 22,
      "data_precision": 10,
      "data_scale": 0,
      "nullable": false,
      "data_default": null,
      "is_pk": true
    }
  ]
}
```

**Oracle 資料字典查詢：**

| 查詢目標 | 資料字典 View | 用途 |
|----------|--------------|------|
| 欄位定義 | `ALL_TAB_COLUMNS` | 取得欄位名稱、型態、長度、精度、可空、預設值 |
| 主鍵約束 | `ALL_CONSTRAINTS` (constraint_type = 'P') | 識別主鍵約束名稱 |
| 主鍵欄位 | `ALL_CONS_COLUMNS` | 取得主鍵約束所包含的欄位 |

### 4.10 認證服務 — `app/services/auth_service.py`

| 元件 | 說明 |
|------|------|
| `oauth2_scheme` | `OAuth2PasswordBearer`，tokenUrl 指向 `/api/v1/auth/token` |
| `authenticate_user(db, username, password)` | 查詢使用者並驗證密碼，回傳 `User` 或 `None` |
| `get_current_user(token, db)` | FastAPI Dependency — 解碼 JWT、查詢使用者、驗證啟用狀態 |
| `get_current_active_superuser(user)` | FastAPI Dependency — 檢查超級管理員權限 |
| `PermissionChecker(resource, action)` | 可呼叫物件 Dependency，檢查 RBAC 權限（詳見第 7 節） |

---

## 5. API 端點規格

**基本路徑 (Base URL):** `/api/v1`

### 5.1 健康檢查

| 方法 | 路徑 | 認證 | 說明 |
|------|------|------|------|
| `GET` | `/api/v1/health` | 不需要 | 回傳 `{"status": "ok"}` |

### 5.2 認證端點 (`/api/v1/auth`)

| 方法 | 路徑 | 認證 | 說明 |
|------|------|------|------|
| `POST` | `/auth/token` | 不需要 | 取得 JWT Token |
| `GET` | `/auth/me` | Bearer Token | 查詢目前登入使用者 |
| `POST` | `/auth/users` | 超級管理員 | 建立使用者 |
| `GET` | `/auth/users` | 超級管理員 | 列出所有使用者 |
| `POST` | `/auth/roles` | 超級管理員 | 建立角色 |
| `GET` | `/auth/roles` | 超級管理員 | 列出所有角色 |
| `POST` | `/auth/permissions` | 超級管理員 | 建立權限 |
| `GET` | `/auth/permissions` | 超級管理員 | 列出所有權限 |

**登入請求/回應：**

```
POST /api/v1/auth/token
Content-Type: application/x-www-form-urlencoded

username=admin&password=changeme
```

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer"
}
```

**建立使用者請求：**

```json
{
  "username": "data_analyst",
  "password": "secure-password",
  "full_name": "資料分析師",
  "is_superuser": false,
  "role_ids": [2]
}
```

### 5.3 查詢端點 (`/api/v1/queries`)

| 方法 | 路徑 | 認證 | 權限 | 說明 |
|------|------|------|------|------|
| `GET` | `/queries/definitions` | Bearer Token | 無特殊權限 | 列出所有可用查詢定義（僅 metadata） |
| `POST` | `/queries/definitions/reload` | 超級管理員 | — | 從磁碟熱重載 SQL 定義 |
| `POST` | `/queries/execute` | Bearer Token | `{permission}:read` | 執行已註冊的查詢定義 |

**查詢請求參數說明：**

| 欄位 | 型態 | 預設值 | 說明 |
|------|------|--------|------|
| `query_name` | string | — (必填) | 已註冊的 SQL 定義名稱 |
| `params` | dict | `{}` | 綁定參數 |
| `fetch_mode` | string | `"page"` | 擷取模式：`page` / `all` / `batch` / `top_n` / `one` |
| `page` | int (>=1) | `1` | 頁碼（`page` 模式） |
| `page_size` | int (1-1000) | `50` | 每頁筆數（`page` 模式） |
| `batch_size` | int (1-10000) | `500` | 批次大小（`batch` 模式） |
| `batch_offset` | int (>=0) | `0` | 起始偏移量（`batch` 模式） |
| `top_n` | int (1-1000) | `10` | 取前 N 筆（`top_n` 模式） |

#### 5.3.1 `fetch_mode = "page"` — 分頁模式（預設）

```json
POST /api/v1/queries/execute
{
  "query_name": "employees",
  "params": {"dept": "IT"},
  "fetch_mode": "page",
  "page": 2,
  "page_size": 20
}
```

```json
{
  "success": true,
  "query_name": "employees",
  "fetch_mode": "page",
  "total": 128,
  "page": 2,
  "page_size": 20,
  "columns": ["EMP_ID", "EMP_NAME", "DEPARTMENT", "HIRE_DATE", "SALARY"],
  "data": [...]
}
```

#### 5.3.2 `fetch_mode = "all"` — 整批取回

```json
{
  "query_name": "departments",
  "fetch_mode": "all"
}
```

```json
{
  "success": true,
  "query_name": "departments",
  "fetch_mode": "all",
  "total": 15,
  "columns": ["DEPT_ID", "DEPT_NAME", "MANAGER_ID", "LOCATION"],
  "data": [...]
}
```

#### 5.3.3 `fetch_mode = "batch"` — 分批取用

適合大量資料的游標式逐批處理，客戶端持續遞增 `batch_offset` 直到 `has_more = false`。

```json
{
  "query_name": "employees",
  "fetch_mode": "batch",
  "batch_size": 500,
  "batch_offset": 0
}
```

```json
{
  "success": true,
  "query_name": "employees",
  "fetch_mode": "batch",
  "total": 1200,
  "batch_offset": 0,
  "batch_size": 500,
  "has_more": true,
  "columns": ["EMP_ID", "EMP_NAME", "DEPARTMENT", "HIRE_DATE", "SALARY"],
  "data": [...]
}
```

第二批：`batch_offset: 500`，第三批：`batch_offset: 1000`（`has_more: false`）。

#### 5.3.4 `fetch_mode = "top_n"` — 取前 N 筆

不執行 COUNT 查詢，效能最佳。適合只需要前幾筆結果的場景。

```json
{
  "query_name": "employees",
  "fetch_mode": "top_n",
  "top_n": 5
}
```

```json
{
  "success": true,
  "query_name": "employees",
  "fetch_mode": "top_n",
  "total": 5,
  "columns": ["EMP_ID", "EMP_NAME", "DEPARTMENT", "HIRE_DATE", "SALARY"],
  "data": [...]
}
```

#### 5.3.5 `fetch_mode = "one"` — 取一筆

僅回傳一筆資料，無資料時回傳 HTTP 400。

```json
{
  "query_name": "employees",
  "params": {"dept": "CEO"},
  "fetch_mode": "one"
}
```

```json
{
  "success": true,
  "query_name": "employees",
  "fetch_mode": "one",
  "total": 1,
  "columns": ["EMP_ID", "EMP_NAME", "DEPARTMENT", "HIRE_DATE", "SALARY"],
  "data": [
    {
      "EMP_ID": 1001,
      "EMP_NAME": "王小明",
      "DEPARTMENT": "CEO",
      "HIRE_DATE": "2020-03-15T00:00:00",
      "SALARY": 200000
    }
  ]
}
```

### 5.4 程序端點 (`/api/v1/procedures`)

| 方法 | 路徑 | 認證 | 權限 | 說明 |
|------|------|------|------|------|
| `POST` | `/procedures/execute` | Bearer Token | `procedure:{name}:execute` | 同步執行 Procedure / Function |
| `POST` | `/procedures/execute-async` | Bearer Token | `procedure:{name}:execute` | 非同步執行，回傳 task_id |

**Procedure 呼叫請求：**

```json
POST /api/v1/procedures/execute
{
  "name": "PKG_HR.GET_EMPLOYEE_SALARY",
  "params": {"p_emp_id": 1001},
  "out_params": ["p_salary", "p_currency"],
  "is_function": false
}
```

**Procedure 呼叫回應：**

```json
{
  "success": true,
  "name": "PKG_HR.GET_EMPLOYEE_SALARY",
  "result": null,
  "out_params": {
    "p_salary": "65000",
    "p_currency": "TWD"
  }
}
```

**Function 呼叫請求：**

```json
{
  "name": "FN_CALCULATE_TAX",
  "params": {"p_amount": 100000},
  "out_params": [],
  "is_function": true
}
```

**Function 呼叫回應：**

```json
{
  "success": true,
  "name": "FN_CALCULATE_TAX",
  "result": "5000",
  "out_params": {}
}
```

**非同步執行回應：**

```json
{
  "task_id": "a3f1b2c4-d5e6-7890-abcd-ef1234567890",
  "status": "PENDING",
  "result": null
}
```

### 5.5 寫入端點 (`/api/v1/writes`)

| 方法 | 路徑 | 認證 | 權限 | 說明 |
|------|------|------|------|------|
| `POST` | `/writes/execute` | Bearer Token | `table:{table}:write` | 執行單筆 INSERT / UPDATE / DELETE |
| `POST` | `/writes/batch` | Bearer Token | `table:{table}:write` | 批次交易寫入（多個 DML 操作在同一交易） |
| `GET` | `/writes/tasks/{task_id}` | Bearer Token | 無特殊權限 | 查詢非同步任務狀態 |

**INSERT 請求（同步）：**

```json
POST /api/v1/writes/execute
{
  "operation": "insert",
  "table": "HR.EMPLOYEES",
  "data": [
    {"emp_name": "新員工A", "department": "IT", "salary": 55000},
    {"emp_name": "新員工B", "department": "HR", "salary": 52000}
  ],
  "async_mode": false
}
```

**同步回應：**

```json
{
  "success": true,
  "operation": "insert",
  "affected_rows": 2,
  "task_id": null,
  "message": "INSERT completed successfully"
}
```

**UPDATE 請求（非同步）：**

```json
{
  "operation": "update",
  "table": "HR.EMPLOYEES",
  "data": [{"salary": 60000}],
  "where": {"emp_id": 1001},
  "async_mode": true
}
```

**非同步回應：**

```json
{
  "success": true,
  "operation": "update",
  "affected_rows": 0,
  "task_id": "b4c2d1e0-f5a6-7890-bcde-fg2345678901",
  "message": "Task enqueued for async processing"
}
```

**任務狀態查詢：**

```
GET /api/v1/writes/tasks/b4c2d1e0-f5a6-7890-bcde-fg2345678901
```

```json
{
  "task_id": "b4c2d1e0-f5a6-7890-bcde-fg2345678901",
  "status": "SUCCESS",
  "result": {"success": true, "operation": "update", "affected_rows": 1}
}
```

**批次交易寫入請求（同步）：**

所有操作在同一資料庫交易中執行，任一操作失敗即全部回滾。

```json
POST /api/v1/writes/batch
{
  "table": "HR.EMPLOYEES",
  "operations": [
    {
      "operation": "insert",
      "data": [{"emp_name": "新員工C", "department": "RD", "salary": 58000}]
    },
    {
      "operation": "update",
      "data": [{"salary": 62000}],
      "where": {"emp_id": 1001}
    },
    {
      "operation": "delete",
      "where": {"emp_id": 9999}
    }
  ],
  "async_mode": false
}
```

**批次成功回應：**

```json
{
  "success": true,
  "table": "HR.EMPLOYEES",
  "total_affected": 4,
  "operation_count": 3,
  "committed": true,
  "results": [
    {"index": 0, "operation": "insert", "affected_rows": 1},
    {"index": 1, "operation": "update", "affected_rows": 2},
    {"index": 2, "operation": "delete", "affected_rows": 1}
  ],
  "task_id": null,
  "message": "Batch transaction committed — 3 operations, 4 rows affected"
}
```

**批次失敗回應（交易回滾）：**

```json
{
  "success": false,
  "table": "HR.EMPLOYEES",
  "total_affected": 0,
  "operation_count": 3,
  "committed": false,
  "results": [
    {"index": 0, "operation": "insert", "affected_rows": 1}
  ],
  "task_id": null,
  "message": "ORA-02291: integrity constraint violated — ROLLED BACK (failed at operation #1)"
}
```

### 5.6 表結構探查端點 (`/api/v1/schemas`)

| 方法 | 路徑 | 認證 | 說明 |
|------|------|------|------|
| `GET` | `/schemas/describe/{table}` | Bearer Token | 取得目標表/View 的欄位定義與主鍵資訊 |

**請求範例：**

```
GET /api/v1/schemas/describe/HR.EMPLOYEES
Authorization: Bearer <token>
```

**回應範例：**

```json
{
  "table": "HR.EMPLOYEES",
  "owner": "HR",
  "table_name": "EMPLOYEES",
  "column_count": 5,
  "primary_keys": ["EMP_ID"],
  "columns": [
    {
      "name": "EMP_ID",
      "data_type": "NUMBER",
      "data_length": 22,
      "data_precision": 10,
      "data_scale": 0,
      "nullable": false,
      "data_default": null,
      "is_pk": true
    },
    {
      "name": "EMP_NAME",
      "data_type": "VARCHAR2",
      "data_length": 100,
      "data_precision": null,
      "data_scale": null,
      "nullable": false,
      "data_default": null,
      "is_pk": false
    }
  ]
}
```

**支援的表名格式：**

| 格式 | 範例 | 說明 |
|------|------|------|
| 簡單名稱 | `EMPLOYEES` | 使用目前連線使用者的 Schema |
| Schema 限定 | `HR.EMPLOYEES` | 指定 Schema（Owner） |

---

## 6. 資料庫設計

### 6.1 系統內部表（由 SQLAlchemy ORM 管理）

#### 6.1.1 使用者表 `api_hub_users`

| 欄位 | 型態 | 約束 | 說明 |
|------|------|------|------|
| `id` | INTEGER | PK, AUTO | 主鍵 |
| `username` | VARCHAR(100) | UNIQUE, NOT NULL, INDEX | 使用者帳號 |
| `hashed_password` | VARCHAR(255) | NOT NULL | bcrypt 雜湊密碼 |
| `full_name` | VARCHAR(200) | NULLABLE | 全名 |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT TRUE | 啟用狀態 |
| `is_superuser` | BOOLEAN | NOT NULL, DEFAULT FALSE | 超級管理員（繞過權限檢查） |
| `created_at` | DATETIME | SERVER DEFAULT SYSDATE | 建立時間 |
| `updated_at` | DATETIME | SERVER DEFAULT SYSDATE, ONUPDATE | 更新時間 |

#### 6.1.2 角色表 `api_hub_roles`

| 欄位 | 型態 | 約束 | 說明 |
|------|------|------|------|
| `id` | INTEGER | PK, AUTO | 主鍵 |
| `name` | VARCHAR(100) | UNIQUE, NOT NULL | 角色名稱 |
| `description` | TEXT | NULLABLE | 角色說明 |
| `created_at` | DATETIME | SERVER DEFAULT SYSDATE | 建立時間 |

#### 6.1.3 權限表 `api_hub_permissions`

| 欄位 | 型態 | 約束 | 說明 |
|------|------|------|------|
| `id` | INTEGER | PK, AUTO | 主鍵 |
| `resource` | VARCHAR(200) | NOT NULL | 資源名稱（如 `query:employees`、`procedure:*`） |
| `action` | VARCHAR(50) | NOT NULL | 操作（`read`、`execute`、`write`） |
| `description` | TEXT | NULLABLE | 權限說明 |

#### 6.1.4 使用者-角色關聯表 `api_hub_user_roles`

| 欄位 | 型態 | 約束 |
|------|------|------|
| `user_id` | INTEGER | PK, FK → api_hub_users.id |
| `role_id` | INTEGER | PK, FK → api_hub_roles.id |

#### 6.1.5 角色-權限關聯表 `api_hub_role_perms`

| 欄位 | 型態 | 約束 |
|------|------|------|
| `role_id` | INTEGER | PK, FK → api_hub_roles.id |
| `permission_id` | INTEGER | PK, FK → api_hub_permissions.id |

### 6.2 ER Diagram

```
┌──────────────────┐     ┌───────────────────┐     ┌──────────────────────┐
│  api_hub_users   │     │  api_hub_roles     │     │ api_hub_permissions  │
├──────────────────┤     ├───────────────────┤     ├──────────────────────┤
│ id           PK  │──┐  │ id           PK   │──┐  │ id             PK   │
│ username     UQ  │  │  │ name         UQ   │  │  │ resource       NN   │
│ hashed_password  │  │  │ description       │  │  │ action         NN   │
│ full_name        │  │  │ created_at        │  │  │ description         │
│ is_active        │  │  └───────────────────┘  │  └──────────────────────┘
│ is_superuser     │  │           │              │            │
│ created_at       │  │  ┌────────┴──────────┐  │  ┌─────────┴──────────┐
│ updated_at       │  │  │api_hub_user_roles │  │  │api_hub_role_perms  │
└──────────────────┘  │  ├───────────────────┤  │  ├────────────────────┤
                      └─▶│ user_id    FK,PK  │  └─▶│ role_id   FK,PK   │
                         │ role_id    FK,PK  │     │ perm_id   FK,PK   │
                         └───────────────────┘     └────────────────────┘
```

### 6.3 初始化種子資料

透過 `python -m scripts.init_db` 建立：

**預設權限：**

| resource | action | 說明 |
|----------|--------|------|
| `query:*` | `read` | 讀取任意查詢 |
| `procedure:*` | `execute` | 執行任意程序 |
| `table:*` | `write` | 寫入任意表 |

**預設角色：**

| 角色 | 權限 | 說明 |
|------|------|------|
| `admin` | `query:*:read` + `procedure:*:execute` + `table:*:write` | 完全存取 |
| `reader` | `query:*:read` | 唯讀存取 |

**預設使用者：**

| 帳號 | 密碼 | 超級管理員 | 角色 |
|------|------|----------|------|
| `admin` | `changeme` | 是 | admin |

> **重要：** 上線前必須立即變更 admin 密碼。

---

## 7. 認證與授權

### 7.1 認證流程

```
  Client                          API Hub                       Oracle DB
    │                                │                              │
    │  POST /auth/token              │                              │
    │  username + password           │                              │
    │──────────────────────────────▶│                              │
    │                                │  查詢 api_hub_users          │
    │                                │─────────────────────────────▶│
    │                                │◀─────────────────────────────│
    │                                │  bcrypt 驗證密碼             │
    │                                │  建立 JWT (HS256)            │
    │  {"access_token": "eyJ..."}   │                              │
    │◀──────────────────────────────│                              │
    │                                │                              │
    │  GET /queries/execute          │                              │
    │  Authorization: Bearer eyJ... │                              │
    │──────────────────────────────▶│                              │
    │                                │  解碼 JWT                    │
    │                                │  查詢 User + Roles           │
    │                                │  檢查 Permission             │
    │                                │  執行 SQL                    │
    │                                │─────────────────────────────▶│
    │  {"data": [...]}              │◀─────────────────────────────│
    │◀──────────────────────────────│                              │
```

### 7.2 JWT Token 結構

```json
{
  "sub": "admin",
  "exp": 1709337600
}
```

| 欄位 | 說明 |
|------|------|
| `sub` | 使用者帳號（subject） |
| `exp` | Token 過期時間（UTC 時間戳記） |

- 演算法：HS256
- 預設存活時間：60 分鐘
- 簽章金鑰：由 `SECRET_KEY` 環境變數設定

### 7.3 RBAC 權限模型

**權限格式：** `resource` + `action`

| 資源類型 | 範例 | 可用動作 |
|----------|------|----------|
| 查詢 | `query:employees`、`query:*` | `read` |
| 程序 | `procedure:PKG_HR.GET_SALARY`、`procedure:*` | `execute` |
| 表 | `table:HR.EMPLOYEES`、`table:*` | `write` |

### 7.4 權限檢查邏輯

`PermissionChecker` 類別實作：

```
1. 若 user.is_superuser == True → 直接通過（繞過所有檢查）
2. 遍歷 user.roles：
   2a. 遍歷 role.permissions：
       - 精確比對：perm.resource == 請求資源 AND perm.action == 請求動作
       - 萬用字元比對：perm.resource 以 ":*" 結尾時，
         取前綴（如 "query:"），若請求資源以此前綴開頭且動作相符 → 通過
3. 若無任何權限匹配 → 回傳 HTTP 403 Forbidden
```

**萬用字元範例：**

| 持有權限 | 請求資源 | 請求動作 | 結果 |
|----------|----------|----------|------|
| `query:*` + `read` | `query:employees` | `read` | 通過 |
| `query:*` + `read` | `query:departments` | `read` | 通過 |
| `query:employees` + `read` | `query:departments` | `read` | 拒絕 |
| `procedure:*` + `execute` | `procedure:PKG.PROC1` | `execute` | 通過 |
| `table:HR.EMP` + `write` | `table:HR.DEPT` | `write` | 拒絕 |

### 7.5 權限綁定位置

| 端點類型 | 權限資源 | 動作 | 取得方式 |
|----------|----------|------|----------|
| 查詢 | YAML 中的 `permission` 欄位 | `read` | `SqlDefinition.permission` |
| 程序 | `procedure:{name}` | `execute` | 由請求中的 `name` 動態組合 |
| 寫入 | `table:{table}` | `write` | 由請求中的 `table` 動態組合 |

---

## 8. 工作佇列設計

### 8.1 架構概觀

```
                ┌─────────────┐
                │   FastAPI    │
                │   (API)      │
                └──────┬──────┘
                       │ enqueue
                       ▼
                ┌─────────────┐
                │    Redis     │
                │   Broker     │
                │  (port 6379) │
                └──────┬──────┘
                       │
              ┌────────┼────────┐
              ▼                 ▼
    ┌──────────────┐   ┌──────────────┐
    │ Write Worker │   │ Proc Worker  │
    │  Queue: write│   │Queue:procedure│
    │ Concurrency:4│   │ Concurrency:2│
    └──────┬───────┘   └──────┬───────┘
           │                  │
           ▼                  ▼
    ┌──────────────────────────┐
    │    Oracle Database       │
    └──────────────────────────┘
```

### 8.2 Celery 組態

| 參數 | 值 | 說明 |
|------|-----|------|
| `task_serializer` | `json` | 任務以 JSON 序列化 |
| `result_serializer` | `json` | 結果以 JSON 序列化 |
| `accept_content` | `["json"]` | 僅接受 JSON |
| `timezone` | `UTC` | 統一時區 |
| `task_track_started` | `True` | 追蹤 STARTED 狀態 |
| `task_acks_late` | `True` | 任務完成後才確認（確保 worker 崩潰時重試） |
| `worker_prefetch_multiplier` | `1` | 每個 worker 一次僅預取 1 個任務（保護 DB 連線池） |

### 8.3 佇列路由

| 任務名稱 | 佇列 | Worker 並行數 |
|----------|------|---------------|
| `app.tasks.write_tasks.async_write` | `write` | 4 |
| `app.tasks.write_tasks.async_batch_write` | `write` | 4 |
| `app.tasks.procedure_tasks.async_procedure` | `procedure` | 2 |

### 8.4 重試策略

| 參數 | 值 |
|------|-----|
| 最大重試次數 | 3 |
| 退避策略 | 指數退避：`2^attempt` 秒 |
| 第 1 次重試等待 | 1 秒 |
| 第 2 次重試等待 | 2 秒 |
| 第 3 次重試等待 | 4 秒 |

### 8.5 任務狀態

| 狀態 | 說明 |
|------|------|
| `PENDING` | 任務已排入佇列，尚未被 worker 取得 |
| `STARTED` | Worker 已開始處理 |
| `SUCCESS` | 任務成功完成 |
| `FAILURE` | 任務失敗（重試次數已耗盡） |
| `RETRY` | 任務失敗，正在等待重試 |

### 8.6 Worker 連線池管理

每個 Celery Worker 進程在首次執行任務時呼叫 `init_pool()` 初始化自己的 cx_Oracle 連線池。設定 `worker_prefetch_multiplier=1` 確保每個 worker 一次只處理一個任務，避免過度消耗資料庫連線。

### 8.7 Flower 監控

- 存取位址：`http://localhost:5555`
- 提供即時 Worker 狀態、任務歷史、佇列深度、成功/失敗率

---

## 9. SQL 定義系統

### 9.1 YAML 檔案格式

檔案位置：`app/sql_definitions/*.yaml`

```yaml
# 必填欄位
name: <string>                  # 唯一識別名稱（用於 API 請求的 query_name）
sql: |                          # SQL SELECT 語句（支援 :param 綁定變數）
  SELECT ...

# 選填欄位
description: <string>           # 人類可讀說明
permission: <string>            # 權限名稱（預設：query:{name}）
params:                         # 參數定義
  <param_name>:
    type: string|number|date    # 類型提示（預設：string）
    required: true|false        # 是否必填（預設：false）
    description: <string>       # 參數說明
```

### 9.2 範例定義

**employees.yaml — 帶可選參數的員工查詢：**

```yaml
name: employees
description: "Query active employees with optional department filter"
sql: |
  SELECT emp_id, emp_name, department, hire_date, salary
  FROM employees
  WHERE status = 'ACTIVE'
  AND (:dept IS NULL OR department = :dept)
  ORDER BY emp_id
params:
  dept:
    type: string
    required: false
    description: "Filter by department code"
permission: "query:employees"
```

**departments.yaml — 無參數的簡單查詢：**

```yaml
name: departments
description: "List all departments"
sql: |
  SELECT dept_id, dept_name, manager_id, location
  FROM departments
  ORDER BY dept_name
params: {}
permission: "query:departments"
```

**inventory_view.yaml — 多重可選參數的 View 查詢：**

```yaml
name: inventory_view
description: "Query inventory view with optional product code and warehouse filters"
sql: |
  SELECT product_code, product_name, warehouse, quantity, last_updated
  FROM v_inventory
  WHERE (:product_code IS NULL OR product_code = :product_code)
  AND (:warehouse IS NULL OR warehouse = :warehouse)
  ORDER BY product_code
params:
  product_code:
    type: string
    required: false
    description: "Filter by product code"
  warehouse:
    type: string
    required: false
    description: "Filter by warehouse name"
permission: "query:inventory_view"
```

### 9.3 可選參數設計模式

使用 Oracle 的 NULL 合併模式實現可選篩選：

```sql
WHERE (:param IS NULL OR column = :param)
```

- 當 `param` 為 `NULL` 時，`:param IS NULL` 為 `TRUE`，條件被跳過
- 當 `param` 有值時，作為精確比對篩選

### 9.4 熱重載

```
POST /api/v1/queries/definitions/reload    (需要超級管理員權限)
```

回應：

```json
{"message": "SQL definitions reloaded", "count": 3}
```

無需重啟服務即可新增、修改、刪除 SQL 定義。

---

## 10. 管理介面 (Admin UI)

### 10.1 概觀

系統內建一套 Web 管理介面（Single-Page Application），透過瀏覽器即可管理所有系統資源，無需使用 curl 或 Postman。

**存取位址：** `http://localhost:8000/admin`

**技術實作：**
- 純前端 SPA（Vanilla JavaScript，無框架依賴）
- 由 FastAPI 以 `StaticFiles` + `FileResponse` 伺服
- 透過 REST API（`/api/v1/*`）與後端通訊
- JWT Token 儲存於 `localStorage`

### 10.2 頁面功能

| 頁面 | 路徑 | 說明 |
|------|------|------|
| **Login** | `/admin` | JWT 登入頁面（帳號密碼表單） |
| **Dashboard** | — | 統計總覽（使用者數、角色數、權限數、SQL 定義數）、快捷資訊 |
| **Users** | — | 使用者清單 + 新增使用者（帳號、密碼、角色指派、超級管理員設定） |
| **Roles** | — | 角色清單 + 新增角色（名稱、描述、權限指派） |
| **Permissions** | — | 權限清單 + 新增權限（resource、action、描述） |
| **SQL Definitions** | — | 查看所有已註冊的 SQL 定義、參數資訊、熱重載 |
| **Query Console** | — | 互動式查詢測試台（選擇 fetch mode、填入參數、即時查看結果表格） |
| **Procedure Console** | — | 執行 Procedure / Function（支援 OUT 參數、非同步模式） |
| **Write Console** | — | 執行 INSERT / UPDATE / DELETE（支援非同步佇列模式） |
| **Data Maintenance** | — | 資料維護介面：讀取表結構 → 動態產生表單 → 支援單筆/批次交易寫入 |
| **Async Tasks** | — | 查詢 Celery 任務狀態、連結至 Flower 監控面板 |

### 10.3 UI 元件

```
┌─────────────────────────────────────────────────────────────────┐
│  ┌──────────┐  ┌──────────────────────────────────────────────┐ │
│  │          │  │  Top Header        [user badge] [Logout]     │ │
│  │  Sidebar │  ├──────────────────────────────────────────────┤ │
│  │          │  │                                              │ │
│  │ Dashboard│  │  Content Area                                │ │
│  │ Users    │  │                                              │ │
│  │ Roles    │  │  ┌─────────────────────────────────────────┐ │ │
│  │ Perms    │  │  │  Stats Grid / Data Tables / Forms       │ │ │
│  │ SQL Defs │  │  │                                         │ │ │
│  │ Query    │  │  │  (dynamically rendered per page)        │ │ │
│  │ Proc     │  │  │                                         │ │ │
│  │ Write    │  │  └─────────────────────────────────────────┘ │ │
│  │ DataMaint│  │                                              │ │
│  │ Tasks    │  │                                              │ │
│  │          │  │                                              │ │
│  └──────────┘  └──────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 10.4 認證流程

1. 使用者在 Login 頁面輸入帳號密碼
2. 前端以 `POST /api/v1/auth/token` 取得 JWT Token
3. Token 儲存於 `localStorage`（key: `hub_token`）
4. 後續所有 API 請求附帶 `Authorization: Bearer <token>` 標頭
5. Token 過期或無效時自動導回 Login 頁面
6. Logout 清除 `localStorage` 中的 Token

### 10.5 Query Console 功能

支援所有五種 fetch mode 的互動式測試：

- **模式切換**：下拉選單切換 page / all / batch / top_n / one
- **動態參數欄位**：依模式顯示/隱藏 page/batch/top_n 相關參數
- **JSON 參數編輯器**：以 textarea 輸入 bind parameters
- **結果展示**：成功時顯示資料表格 + 可展開的 Raw JSON（語法高亮）
- **錯誤提示**：失敗時顯示紅色錯誤訊息

### 10.6 檔案結構

```
app/admin/
├── index.html            # SPA 主頁面（含所有 HTML 結構）
├── css/
│   └── style.css         # 完整樣式（佈局、元件、響應式）
└── js/
    └── app.js            # 應用邏輯（路由、API 呼叫、頁面渲染）
```

### 10.7 路由掛載

```python
# app/main.py
app.mount("/admin/css", StaticFiles(directory="app/admin/css"))
app.mount("/admin/js",  StaticFiles(directory="app/admin/js"))

@app.get("/admin")
@app.get("/admin/{rest_of_path:path}")
def admin_spa(rest_of_path: str = ""):
    return FileResponse("app/admin/index.html")
```

所有 `/admin/*` 路徑均回傳 `index.html`，由前端 JavaScript 負責頁面路由。

### 10.8 資料維護頁面 (Data Maintenance)

資料維護頁面是一個動態表單產生器，能讀取目標表的結構描述（Schema），自動建立對應的操作表單，支援單筆或批次交易寫入。

**操作流程：**

```
使用者輸入表名 → 呼叫 GET /schemas/describe/{table}
                        ↓
               取得欄位定義 + 主鍵資訊
                        ↓
               顯示表結構摘要（欄位標籤含型態/PK/可空）
                        ↓
    使用者新增操作 → Insert / Update / Delete 動態表單卡片
                        ↓
  Insert: 依所有欄位產生輸入框
  Update: SET 區域 + WHERE 區域（預設填入 PK 欄位）
  Delete: 僅 WHERE 區域（預設填入 PK 欄位）
                        ↓
  送出 → POST /writes/batch（同步或非同步）
                        ↓
  顯示交易結果：成功（已提交）/ 失敗（已回滾 + 失敗位置）
```

**主要功能：**

| 功能 | 說明 |
|------|------|
| 表結構探查 | 輸入表名後自動載入欄位定義，顯示資料型態、主鍵、可空屬性 |
| 動態表單產生 | 依欄位定義自動產生輸入框，NUMBER 型態自動轉型 |
| 多操作編輯器 | 可新增多個 Insert / Update / Delete 操作卡片 |
| 交易處理 | 所有操作在單一交易中執行，全部成功才 Commit，任一失敗全部 Rollback |
| 非同步模式 | 可選擇透過 Celery 佇列非同步執行 |
| 結果摘要 | 顯示每個操作的受影響筆數、總計、交易狀態 |

**欄位標籤顯示：**

每個欄位以標籤方式呈現，包含：
- 欄位名稱
- 資料型態（如 `VARCHAR2(100)`、`NUMBER(10,2)`）
- 🔑 PK 標記（主鍵欄位）
- ⚪ Nullable 標記（可空欄位）

---

## 11. 中介層 (Middleware)

### 11.1 CORS 中介層

```python
CORSMiddleware(
    allow_origins=["*"],         # 生產環境應限制來源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

> **生產環境注意：** 應將 `allow_origins` 設為明確的前端網域清單。

### 11.2 速率限制中介層

**類別：** `RateLimiterMiddleware`
**演算法：** 滑動視窗 (Sliding Window)

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `max_requests` | 60 | 每客戶端每分鐘最大請求數 |
| `window` | 60 秒 | 滑動視窗長度 |

**處理流程：**

```
1. 取得客戶端 IP（request.client.host）
2. 清除超過 60 秒的歷史記錄
3. 若現存記錄 >= max_requests
   → 回傳 HTTP 429: {"detail": "Rate limit exceeded. Try again later."}
4. 否則記錄本次請求時間，繼續處理
```

**限制事項：**
- 記憶體內儲存，各 API 實例獨立計算
- 多實例部署時應改用 Redis-based 限流器（如 `fastapi-limiter`）
- 若部署於反向代理後方，需改用 `X-Forwarded-For` 標頭辨識客戶端

---

## 12. 部署架構

### 12.1 Docker 映像

**基底映像：** `python:3.11-slim`

**建置步驟：**
1. 安裝 `libaio1`（Oracle Instant Client 依賴）
2. 下載並解壓 Oracle Instant Client
3. 設定 `LD_LIBRARY_PATH` 並執行 `ldconfig`
4. 安裝 Python 依賴
5. 複製應用程式碼
6. 啟動 Uvicorn：`uvicorn app.main:app --host 0.0.0.0 --port 8000`

### 12.2 Docker Compose 服務

| 服務 | 映像 | 連接埠 | 說明 |
|------|------|--------|------|
| `api` | 自建 | 8000 | FastAPI 應用伺服器 |
| `celery-write-worker` | 自建 | — | 寫入佇列 Worker（並行 4） |
| `celery-proc-worker` | 自建 | — | 程序佇列 Worker（並行 2） |
| `flower` | 自建 | 5555 | Celery 監控儀表板 |
| `redis` | redis:7-alpine | 6379 | 訊息佇列 Broker |

### 12.3 部署拓撲

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Compose Host                       │
│                                                             │
│  ┌───────────┐  ┌───────────────┐  ┌────────────────────┐  │
│  │  api:8000  │  │ write-worker  │  │  proc-worker       │  │
│  │  (FastAPI) │  │ (Celery x4)   │  │  (Celery x2)       │  │
│  └─────┬─────┘  └───────┬───────┘  └─────────┬──────────┘  │
│        │                │                     │             │
│        │         ┌──────▼──────┐              │             │
│        │         │ redis:6379  │◀─────────────┘             │
│        │         └─────────────┘                            │
│        │                                                    │
│  ┌─────▼─────────┐                                         │
│  │ flower:5555   │                                         │
│  │ (Monitoring)  │                                         │
│  └───────────────┘                                         │
└────────────────────────────────┬────────────────────────────┘
                                 │
                                 ▼
                    ┌──────────────────────┐
                    │   Oracle Database    │
                    │   11.1.0.4           │
                    │   (外部或同網段)      │
                    └──────────────────────┘
```

### 12.4 Volume 掛載

| 掛載 | 說明 |
|------|------|
| `./app/sql_definitions` → `/app/app/sql_definitions` | SQL 定義檔熱更新 |
| `redis_data` (named volume) | Redis 資料持久化 |

### 12.5 快速啟動

```bash
# 1. 複製並編輯組態
cp .env.example .env
vi .env   # 設定 ORACLE_DSN、密碼、SECRET_KEY 等

# 2. 啟動所有服務
docker-compose up -d

# 3. 初始化資料庫表與管理員帳號
docker-compose exec api python -m scripts.init_db

# 4. 取得 Token
curl -X POST http://localhost:8000/api/v1/auth/token \
  -d "username=admin&password=changeme"

# 5. 執行查詢
curl -X POST http://localhost:8000/api/v1/queries/execute \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query_name": "employees", "params": {"dept": "IT"}}'
```

---

## 13. 安全性設計

### 13.1 安全措施總覽

| 威脅 | 防護措施 | 實作位置 |
|------|----------|----------|
| **SQL Injection** | 所有 SQL 使用綁定參數（`:param`），表名正則驗證 | `query_service.py`, `write_service.py` |
| **未授權存取** | JWT Bearer Token 認證 | `auth_service.py` |
| **越權操作** | RBAC 權限檢查 + 超級管理員機制 | `PermissionChecker` |
| **暴力破解** | 速率限制（每分鐘 60 次） | `rate_limiter.py` |
| **密碼洩漏** | bcrypt 雜湊儲存 | `security.py` |
| **Token 偽造** | HS256 簽章驗證 + 過期時間 | `security.py` |
| **DML Injection** | 表名正則 + 參數化 SQL | `write_service.py` |
| **資料庫過載** | 連線池上限 + Celery 佇列緩衝 | `oracle_pool.py`, `celery_app.py` |

### 13.2 表名驗證規則

```
正則：^[A-Za-z_][A-Za-z0-9_$.#]{0,127}$

合法：EMPLOYEES, HR.EMPLOYEES, my_table_123
非法：'; DROP TABLE --, (空字串), 123_bad, table name with spaces
```

### 13.3 上線前檢查清單

- [ ] 變更 `admin` 帳號的預設密碼
- [ ] 將 `SECRET_KEY` 設為高強度隨機字串
- [ ] 將 CORS `allow_origins` 限縮為實際前端網域
- [ ] 變更 `ORACLE_PASSWORD` 為正式環境密碼
- [ ] 若部署多實例，將速率限制改為 Redis-backed
- [ ] 若在反向代理後方，配置 `X-Forwarded-For` 信任

---

## 14. 設定參數一覽

| 環境變數 | 預設值 | 說明 |
|----------|--------|------|
| `ORACLE_DSN` | `localhost:1521/orcl` | Oracle 連線字串 |
| `ORACLE_USER` | `api_user` | 資料庫使用者 |
| `ORACLE_PASSWORD` | `changeme` | 資料庫密碼 |
| `ORACLE_POOL_MIN` | `2` | 連線池最小連線數 |
| `ORACLE_POOL_MAX` | `10` | 連線池最大連線數 |
| `ORACLE_POOL_INCREMENT` | `1` | 連線池增長步階 |
| `SECRET_KEY` | `replace-with-...` | JWT 簽章金鑰 |
| `ALGORITHM` | `HS256` | JWT 簽章演算法 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Token 存活時間（分鐘） |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 連線 |
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` | Celery Broker |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/2` | Celery 結果儲存 |
| `API_TITLE` | `Oracle API Hub` | API 標題（Swagger UI 顯示） |
| `API_VERSION` | `1.0.0` | API 版本號 |
| `API_PREFIX` | `/api/v1` | API 路徑前綴 |
| `DEBUG` | `false` | 除錯模式（啟用後 log level 為 DEBUG） |
| `RATE_LIMIT_PER_MINUTE` | `60` | 每客戶端每分鐘最大請求數 |
| `SQL_DEFINITIONS_DIR` | `app/sql_definitions` | SQL 定義檔目錄路徑 |

---

## 15. 專案目錄結構

```
Oracle-api-hub/
├── app/
│   ├── __init__.py
│   ├── main.py                          # FastAPI 應用進入點（lifespan 管理）
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                    # Settings（環境變數組態）
│   │   └── security.py                  # JWT 產生 + bcrypt 密碼處理
│   ├── db/
│   │   ├── __init__.py
│   │   ├── oracle_pool.py               # cx_Oracle SessionPool 連線池管理
│   │   └── session.py                   # SQLAlchemy ORM Engine / Session
│   ├── models/
│   │   ├── __init__.py
│   │   └── user.py                      # User / Role / Permission ORM 模型
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── auth.py                      # 認證相關 Pydantic Schema
│   │   └── query.py                     # 查詢 / 程序 / 寫入 / 批次交易 Pydantic Schema
│   ├── services/
│   │   ├── __init__.py
│   │   ├── auth_service.py              # 認證邏輯 + PermissionChecker
│   │   ├── sql_registry.py              # YAML SQL 定義載入器（Singleton）
│   │   ├── query_service.py             # SQL 查詢執行 + ROWNUM 分頁
│   │   ├── procedure_service.py         # Oracle Procedure / Function 呼叫
│   │   ├── write_service.py             # DML 操作（INSERT/UPDATE/DELETE + 批次交易）
│   │   └── schema_service.py            # 表結構探查（ALL_TAB_COLUMNS / 主鍵偵測）
│   ├── tasks/
│   │   ├── __init__.py
│   │   ├── celery_app.py                # Celery 組態 + 佇列路由
│   │   ├── write_tasks.py               # 非同步寫入任務（含批次交易）
│   │   └── procedure_tasks.py           # 非同步程序呼叫任務
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── rate_limiter.py              # 滑動視窗速率限制
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── auth.py                  # 認證端點（登入 / 使用者 / 角色 / 權限 CRUD）
│   │       ├── health.py                # 健康檢查端點
│   │       ├── queries.py               # 查詢端點（定義列表 / 重載 / 執行）
│   │       ├── procedures.py            # 程序端點（同步 / 非同步）
│   │       ├── writes.py                # 寫入端點（單筆 / 批次 / 非同步 / 狀態查詢）
│   │       └── schemas.py              # 表結構探查端點
│   ├── admin/                           # 管理介面 SPA
│   │   ├── index.html                   # SPA 主頁面
│   │   ├── css/
│   │   │   └── style.css                # UI 樣式
│   │   └── js/
│   │       └── app.js                   # 前端邏輯（路由、API、頁面渲染）
│   └── sql_definitions/                 # SQL 查詢定義 YAML 檔
│       ├── employees.yaml               # 員工查詢範例
│       ├── departments.yaml             # 部門查詢範例
│       └── inventory_view.yaml          # 庫存 View 查詢範例
├── scripts/
│   ├── __init__.py
│   └── init_db.py                       # 資料庫初始化腳本（建表 + 種子資料）
├── tests/
│   ├── __init__.py
│   ├── test_security.py                 # 安全模組測試
│   ├── test_sql_registry.py             # SQL 登錄服務測試
│   └── test_write_service.py            # 寫入服務測試（表名驗證）
├── docs/
│   └── system_specification.md          # 本系統規格書
├── Dockerfile                           # Docker 映像定義
├── docker-compose.yaml                  # 多服務容器編排
├── pyproject.toml                       # 專案元資料與依賴
├── requirements.txt                     # pip 依賴清單
├── .env.example                         # 環境變數範例
├── .gitignore                           # Git 忽略規則
└── README.md                            # 專案說明文件
```
