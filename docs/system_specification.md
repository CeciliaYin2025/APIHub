# Oracle API Hub — 系統規格書

| 項目 | 內容 |
|------|------|
| 文件名稱 | Oracle RESTful API Hub 系統規格書 |
| 版本 | 1.8.0 |
| 目標資料庫 | Oracle Database 11g (11.1.0.4)（支援多資料庫） |
| 框架 | FastAPI (Python 3.10+) |
| 文件日期 | 2026-08-07 |

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
| 通用性 | 透過 Admin UI 或 API 管理 SQL 定義，無需撰寫程式碼即可新增查詢端點（支援 YAML 匯出/匯入跨資料庫遷移） |
| 開放資料格式 | 查詢結果回傳結構化 JSON（含欄位名稱、分頁資訊、資料列） |
| 程序呼叫 | 支援呼叫 Oracle Function / Procedure（含 IN/OUT 參數） |
| 資料寫入 | 支援 INSERT / UPDATE / DELETE，並可切換同步或非同步模式 |
| 負載緩解 | 透過 Celery + Redis 工作佇列將寫入與程序呼叫非同步化 |
| 權限控管 | JWT 認證搭配 RBAC（角色基礎存取控制），支援萬用字元權限 |
| 管理介面 | 內建 Web 管理 UI（SPA），提供使用者/角色/權限 CRUD、查詢測試、任務監控 |
| 多資料庫支援 | 透過 YAML 設定多個 Oracle 資料庫，以 `/{db}/` 路徑前綴選擇目標資料庫 |
| 11g 相容 | 使用 cx_Oracle SessionPool 與 ROWNUM 分頁，完全相容 Oracle 11.1.0.4 |

### 1.3 系統邊界

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          Oracle API Hub                                   │
│                                                                          │
│  ┌──────────┐    ┌───────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ 外部系統  │───▶│  FastAPI   │───▶│   Service     │───▶│  Oracle DB A │  │
│  │ (Client)  │◀──│  Router    │◀──│   Layer       │──▶│  Oracle DB B │  │
│  └──────────┘    └───────────┘    └──────┬───────┘──▶│  Oracle DB C │  │
│                                          │            └──────────────┘  │
│                                   ┌──────▼───────┐          ▲           │
│                  /{db}/ 路徑 ─────│DatabaseRegistry│─────────┘           │
│                   前綴選擇         │ (Multi-Pool)  │                     │
│                                   └──────┬───────┘                      │
│                                   ┌──────▼───────┐                      │
│                                   │ Celery Worker │                      │
│                                   │ (Redis Queue) │                      │
│                                   └──────────────┘                      │
└──────────────────────────────────────────────────────────────────────────┘
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
                    │  ┌───────────────────────────────────────────────────────┐  │
                    │  │              API Layer (v1)                              │  │
                    │  │  ┌──────┐ ┌─────┐ ┌───────┐ ┌──────┐ ┌──────┐ ┌──────┐ │  │
                    │  │  │ Auth │ │ DBs │ │Queries│ │Procs │ │Writes│ │Schema│ │  │
                    │  │  │Router│ │Route│ │Router │ │Router│ │Router│ │Router│ │  │
                    │  │  └──┬───┘ └──┬──┘ └──┬────┘ └──┬───┘ └──┬───┘ └──┬──┘ │  │
                    │  └─────┼────────┼───────┼─────────┼────────┼────────┼────┘  │
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
                    │  │ Database  │ │SQLAlchemy│ │  Celery  │        │
                    │  │ Registry  │ │  ORM     │ │  Tasks   │        │
                    │  │(cx_Oracle)│ └────┬────┘ └─────┬────┘        │
                    │  └─────┬─────┘      │            │             │
                    └────────┼────────────┼────────────┼──────────────┘
                    ┌────────┼────────┐   │            │
                    ▼        ▼        ▼   ▼            ▼
               ┌────────┐┌────────┐┌────────┐     ┌────────┐
               │Oracle A││Oracle B││Oracle C│     │  Redis  │
               │  11g   ││  11g   ││  11g   │     │ Broker  │
               └────────┘└────────┘└────────┘     └────────┘
```

### 2.2 分層說明

| 層級 | 職責 | 對應模組 |
|------|------|----------|
| **中介層 (Middleware)** | CORS 跨域、速率限制 | `app/middleware/rate_limiter.py` |
| **路由層 (Router)** | HTTP 端點定義、請求驗證 | `app/api/v1/*.py` |
| **服務層 (Service)** | 業務邏輯、SQL 執行、權限檢查 | `app/services/*.py` |
| **資料存取層 (Data)** | 多資料庫連線池管理（DatabaseRegistry）、ORM Session | `app/db/*.py` |
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
| SQLAlchemy | >= 2.0.0, < 3.0 | ORM（使用者/角色管理表 + SQL 定義表） |
| Pydantic | >= 2.0.0 | 資料驗證與序列化 |
| pydantic-settings | >= 2.0.0 | 環境變數組態管理 |
| python-jose | >= 3.3.0 | JWT Token 編解碼 |
| passlib[bcrypt] | >= 1.7.4 | 密碼雜湊（bcrypt） |
| Celery[redis] | >= 5.3.0 | 分散式工作佇列 |
| Redis | >= 5.0.0 | 訊息佇列 Broker & 結果儲存 |
| PyYAML | >= 6.0 | SQL 定義 YAML 備援/匯入解析 |
| psycopg2-binary | >= 2.9.9 | PostgreSQL 驅動（SQLAlchemy 底層） |
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
| PostgreSQL | 系統內部資料庫（使用者/角色/權限/SQL 定義） |
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
    database_definitions_dir: str = "app/database_definitions"
```

### 4.2 安全模組 — `app/core/security.py`

| 函式 | 簽章 | 說明 |
|------|------|------|
| `verify_password` | `(plain: str, hashed: str) -> bool` | 以 bcrypt 驗證密碼 |
| `get_password_hash` | `(password: str) -> str` | 產生 bcrypt 雜湊值 |
| `create_access_token` | `(data: dict, expires_delta?) -> str` | 產生 HS256 簽章之 JWT Token，預設 60 分鐘過期 |

### 4.3 多資料庫連線池管理

系統支援同時連接多個資料庫（Oracle / PostgreSQL / MS SQL Server），透過 `DatabaseRegistry` 單例管理所有連線池。資料來源與寫入目標不分離——同一個 alias 既可被查詢（讀），也可被寫入或執行 procedure（寫）；要新增多個來源/目標，即註冊多個 alias。

#### 4.3.1 資料庫定義 — `app/database_definitions/databases.yaml`

以單一 YAML 清單檔定義所有資料庫連線，每個項目即一個 alias。由 `DatabaseRegistry.load()`（`app/db/multi_pool.py`）讀取。

**各引擎欄位：**

| 欄位 | Oracle | PostgreSQL / MSSQL | 說明 |
|------|--------|--------------------|------|
| `alias` | ✅ | ✅ | 識別碼，亦為 API 路徑片段；規則 `^[a-zA-Z][a-zA-Z0-9_]{0,63}$` |
| `db_type` | `oracle`（可省略） | `postgresql` / `mssql` | 決定 adapter（`app/db/adapters/`）與 dialect（`app/db/dialects/`） |
| `description` | ✅ | ✅ | 顯示於 `GET /api/v1/databases` 與 Admin UI DB 選單 |
| `dsn` | ✅ `host:port/service` | — | 僅 Oracle |
| `host` / `port` / `database` | — | ✅ | 僅 PostgreSQL / MSSQL |
| `user` / `password` | ✅ | ✅ | 連線帳密（`password` 請留空，見下） |
| `pool_min` / `pool_max` | ✅ | ✅ | 連線池大小 |
| `pool_increment` | ✅ | —（僅 Oracle 使用） | 連線池增長步階 |

```yaml
# Oracle（db_type 可省略）
- alias: default
  db_type: oracle
  description: "Oracle UAT database"
  dsn: "172.31.3.85:1531/UAT"
  user: "apps"
  password: ""          # 由 DB_DEFAULT_PASSWORD 提供
  pool_min: 2
  pool_max: 10
  pool_increment: 1

# PostgreSQL
- alias: pg_test
  db_type: postgresql
  description: "PostgreSQL test database"
  host: "localhost"
  port: 5432
  database: "db_api_demo"
  user: "db_api_demo"
  password: ""          # 由 DB_PG_TEST_PASSWORD 提供
  pool_min: 2
  pool_max: 5
```

**密碼安全（不在檔案保存明文）：** YAML 的 `password` 一律留空 `""`，改以環境變數 `DB_{ALIAS大寫}_PASSWORD` 提供（例：alias `pg_test` → `DB_PG_TEST_PASSWORD`）。`_load_file()` 在載入時若偵測到該環境變數即覆蓋 YAML 值；每個 alias 都需對應的密碼變數，否則連線失敗。

**載入時機與生效方式：** 定義僅在**啟動時**（lifespan 內 `db_registry.load()`）載入一次，連線池則於首次使用時延遲建立。資料來源**沒有熱更新端點**（與 SQL 定義不同）——修改 `databases.yaml` 後必須**重啟程序/容器**才會生效，且 Celery worker 也須一併重啟（它們透過同一份 YAML 連線）。

**部署掛載（改檔免 rebuild）：** 本機 `docker-compose.yaml` 與 ISAP 閘道 `deploy/apihub/docker-compose.yml`（測試/UAT 環境為 `deploy/apihub-test/`）兩套 compose 都將 `app/database_definitions` 目錄 bind-mount 進 `api` 與兩個 celery 服務，因此改 YAML 只需 `docker compose restart`、毋須重建映像。新增 alias 時，記得在對應的 `.env` 補上其 `DB_*_PASSWORD`。

#### 4.3.2 多連線池管理器 — `app/db/multi_pool.py`

**資料類別 `DatabaseConfig`：**

| 欄位 | 型態 | 說明 |
|------|------|------|
| `alias` | `str` | 資料庫別名（用於 URL 路徑） |
| `db_type` | `str` | 引擎類型：`oracle`（預設）/ `postgresql` / `mssql` |
| `description` | `str` | 人類可讀說明 |
| `dsn` | `str` | Oracle 連線字串（`host:port/service`） |
| `host` | `str` | 主機（PostgreSQL / MSSQL） |
| `port` | `int` | 連接埠（PostgreSQL / MSSQL） |
| `database` | `str` | 資料庫名稱（PostgreSQL / MSSQL） |
| `user` | `str` | 資料庫使用者 |
| `password` | `str` | 資料庫密碼（建議留空，由 `DB_{ALIAS}_PASSWORD` 提供） |
| `pool_min` | `int` | 連線池最小連線數（預設 2） |
| `pool_max` | `int` | 連線池最大連線數（預設 10） |
| `pool_increment` | `int` | 連線池增長步階（預設 1，僅 Oracle 使用） |

**類別 `DatabaseRegistry`（Singleton）：**

| 方法 | 說明 |
|------|------|
| `load(directory?)` | 從指定目錄載入所有 `*.yaml` / `*.yml` 資料庫定義 |
| `init_all_pools()` | 為所有已載入的資料庫初始化 cx_Oracle SessionPool |
| `init_pool(alias)` | 為指定資料庫初始化 SessionPool |
| `close_all_pools()` | 關閉所有 SessionPool |
| `get_connection(alias)` | Context Manager，依別名取得連線並自動歸還 |
| `get_cursor(alias)` | Context Manager，依別名取得 Cursor 並自動關閉 |
| `list_databases()` | 列出所有已設定的資料庫（別名、說明、狀態） |
| `has(alias)` | 檢查指定別名是否已註冊 |
| `aliases()` | 回傳所有已註冊的別名列表 |

**每個資料庫的連線池組態：**

```python
cx_Oracle.SessionPool(
    user=config.user, password=config.password, dsn=config.dsn,
    min=config.pool_min, max=config.pool_max, increment=config.pool_increment,
    encoding="UTF-8",
    threaded=True,                          # 執行緒安全
    getmode=cx_Oracle.SPOOL_ATTRVAL_WAIT,   # 連線用盡時等待（不拋例外）
)
```

**Legacy 單庫向下相容：** 若 `database_definitions` 目錄中無 YAML 檔案，系統自動從環境變數（`ORACLE_DSN`、`ORACLE_USER`、`ORACLE_PASSWORD`）建立名為 `default` 的資料庫項目，確保向下相容。

#### 4.3.3 向下相容封裝 — `app/db/oracle_pool.py`

改寫為 `DatabaseRegistry` 的薄封裝層，保持既有 API 介面向下相容。

| 函式 | 說明 |
|------|------|
| `init_pool()` | 載入並初始化所有資料庫連線池 |
| `close_pool()` | 關閉所有資料庫連線池 |
| `get_connection(db_alias?)` | Context Manager，依別名取得連線（省略時使用第一個資料庫） |
| `get_cursor(db_alias?)` | Context Manager，依別名取得 Cursor |

### 4.4 SQLAlchemy Session — `app/db/session.py`

用於管理系統內部的使用者、角色、權限及 SQL 定義 ORM 表。動態 SQL（查詢、Procedure、DML）一律使用 cx_Oracle 連線池。

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
| `load(directory?)` | 先嘗試從 PostgreSQL 載入；若 DB 為空則回退到 YAML 檔案 |
| `get(name) -> SqlDefinition?` | 依名稱查詢定義 |
| `list_all() -> list[SqlDefinition]` | 取得所有定義 |
| `reload()` | 清空並重新載入（先嘗試 DB，再回退 YAML） |
| `reload_from_db()` | 僅從 DB 重新載入（CRUD 操作後自動呼叫） |

**載入優先順序：**

1. **PostgreSQL**（`api_hub_sql_definitions` 表）— 主要來源
2. **YAML 檔案**（`app/sql_definitions/*.yaml`）— 備援（DB 為空時使用）

**ORM 模型 — `app/models/sql_definition.py`：**

| 類別 | 表名 | 說明 |
|------|------|------|
| `SqlDefinitionModel` | `api_hub_sql_definitions` | SQL 定義持久化儲存 |

**Pydantic Schema — `app/schemas/sql_definition.py`：**

| 類別 | 說明 |
|------|------|
| `SqlDefinitionCreate` | 建立用（`name` 必填且唯一） |
| `SqlDefinitionUpdate` | 更新用（所有欄位可選） |
| `SqlDefinitionOut` | 回應用（含 `id`、`created_at`、`updated_at`） |
| `ParamDefSchema` | 參數定義驗證（`type` 限 `string`/`number`/`date`） |

### 4.6 查詢執行服務 — `app/services/query_service.py`

**函式 `execute_query`：**

```
(definition, params, fetch_mode, *, page, page_size, batch_size, batch_offset, top_n, db_alias=None)
  -> dict[str, Any]   # 包含 columns, data, total, 及模式特有的分頁資訊
```

> `db_alias` 參數指定目標資料庫別名，由路由層的 `{db}` 路徑參數傳入。

> **SQL 正規化（1.6.0 起）：** `execute_query` 在分派前會先呼叫 `_normalize_sql()`，去除定義 SQL 結尾的空白與**語句終止分號** `;`（語句中間的 `;` 保留）。因為所有 fetch mode 都會把原始 SQL 包成子查詢（`… FROM (<sql>) sub_`），結尾分號會在每個引擎都導致語法錯誤（如 MSSQL：`Incorrect syntax near ';'`）。儲存定義時僅 `.strip()` 空白、不會去除分號，故於執行層統一處理。

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
| `execute_procedure(name, params, out_params, is_function, db_alias=None)` | 主入口，回傳 `{"result": ..., "out_params": {...}}` |
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
| `execute_write(operation, table, data, where, db_alias=None)` | DML 主入口，回傳受影響筆數 |
| `execute_batch_write(table, operations, db_alias=None)` | 批次交易寫入，所有操作在同一交易中執行；任一失敗即全部回滾 |
| `_do_insert(cursor, table, data)` | 使用 `executemany` 批次新增 |
| `_do_update(cursor, table, data, where)` | 逐筆更新，SET 與 WHERE 均為綁定參數 |
| `_do_delete(cursor, table, where)` | 條件刪除 |

**批次交易寫入 (`execute_batch_write`)：**

在單一資料庫連線內依序執行多個 DML 操作，全部成功才 `commit`，任一失敗即 `rollback` 並拋出 `BatchWriteError`（攜帶已執行的部分結果與失敗索引）。

```python
def execute_batch_write(table, operations, db_alias=None) -> dict:
    """回傳 {total_affected, operation_count, committed, results}"""
    with get_connection(db_alias) as conn:
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
| `describe_table(table, db_alias=None)` | 回傳目標表/View 的所有欄位 metadata 與主鍵列表 |
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

### 4.11 資料庫健康檢查服務 — `app/services/db_health_service.py`

回報每個資料來源 alias 的**即時連線狀態**。`DatabaseRegistry.list_databases()` 的欄位全部來自設定檔，其 `pool_active` 只代表「pool 物件已建立」，並不等於連線健康；`init_all_pools()` 又刻意容忍死掉的資料來源（避免一個後端拖垮整個 App 啟動）。因此一個 alias 可能從啟動起就從未連通，而 Dashboard 上看起來一切正常。本服務即為了讓這種狀態浮上檯面。

| 元件 | 說明 |
|------|------|
| `check_database(alias)` | 取得 dialect 的健康檢查 SQL、實際取連線執行並計時。回傳 `status="online"` 或 `"offline"`。**不會拋例外** —— 任何 driver/組態錯誤都轉成 `offline` + 原始錯誤字串 |
| `check_all_databases()` | 以 `ThreadPoolExecutor` 併發檢查 `db_registry.aliases()` 全部 alias，共用單一整體 deadline。逾時者回 `status="timeout"` |
| `_MAX_WORKERS = 8` | 單次檢查內的併發上限（非累積上限，見下方限制） |

**健康檢查 SQL 由 Dialect 層提供**（`Dialect.health_check_sql()`），service 不得 branch `db_type`：

| Dialect | 回傳 |
|---------|------|
| `OracleDialect` | `SELECT 1 FROM DUAL`（Oracle 無 FROM 不合法） |
| `PostgresqlDialect` / `MssqlDialect` | `SELECT 1` |

**回傳結構（每個 alias 一筆）：**

```python
{
    "alias": str,
    "status": "online" | "offline" | "timeout",
    "latency_ms": float | None,   # offline / timeout 時為 None
    "error": str | None,          # online 時為 None；否則為 driver 原始錯誤
    "checked_at": str,            # ISO-8601 UTC
}
```

**整體 deadline（重要設計）：** 三個 driver（`oracledb` / `psycopg2` / `pymssql`）都是阻塞式同步 API，因此併發只能靠 thread。submit 完所有 future 後記錄單一 `deadline`，再逐一以「剩餘時間」取值 —— **所有 alias 共用同一份 `DB_HEALTH_CHECK_TIMEOUT` 預算**，10 個死掉的資料庫最多仍只等 N 秒，而非 10×N 秒。

**已知限制：** Python 無法安全砍掉執行中的 thread，逾時後只能放棄該 worker（`shutdown(wait=False, cancel_futures=True)`），它會存活到 driver 自身的網路逾時才結束。故此 timeout 限制的是「呼叫端等待的時間」，不是「thread 存活的時間」；`_MAX_WORKERS` 只限制單次檢查內的併發，executor 每次呼叫都重建，重複 Refresh 打向卡死的資料庫會累積被放棄的 thread。實務上受限於本端點僅開放 superuser。根治方式是在三個 adapter 各自設定 driver 層 connect timeout（未納入）。

**注意：** 健康檢查會佔用一個真實的 pool 連線；當 alias 已達 `pool_max` 時，健康的資料庫也可能因等不到連線而顯示為 `timeout`。

---

### 4.12 MCP 工具目錄檢視服務 — `app/services/mcp_catalog_service.py`

回報 **MCP server 實際會暴露哪些工具**。MCP server 是獨立行程，對每個 (db_alias, SQL 定義) 各衍生一個工具；當衍生出的名稱與既有者相同時，它保留先到者並**靜默丟棄**其餘（`mcp_server/catalog.py`）。丟棄行為發生在該行程內，沒有 log、REST API 也看不出痕跡 —— 一個定義因此可能在 Admin UI 正常、REST 呼叫正常，卻對所有 LLM 不存在。本服務即為讓此狀態浮上檯面，**唯讀，不設定任何東西**。

**工具名稱衍生規則**（`mcp_server/naming.py`）：

```
query_{sanitize(db_alias)}_{sanitize(definition_name)}   →  截斷至 64 字元
sanitize(text) = 轉小寫，[a-zA-Z0-9_-] 以外的連續字元換成單一 "_"，再去除頭尾 "_"
```

64 與字元集皆源自 MCP／Anthropic 對工具名稱的限制 `^[a-zA-Z0-9_-]{1,64}$`。扣掉固定前綴 `query_` 與中間的 `_`，alias 與定義名稱**合計僅餘 57 字元**。

**三種撞名途徑：**

| 途徑 | 範例 | 說明 |
|------|------|------|
| 大小寫／字元折疊 | `EO Detail`、`EO_DETAIL`、`eo_detail` → `query_dw_eo_detail` | `sanitize()` 轉小寫並把空白等字元換成 `_`。`-` 被保留，故 `eo-detail` 不會撞 |
| 分隔符歧義 | (`pg_test`, `employees`) 與 (`pg`, `test_employees`) → `query_pg_test_employees` | alias 與定義名稱皆可含 `_`，兩者又以 `_` 相接，**可跨資料來源撞名** |
| 長度截斷 | `..._region_v2` 與 `..._region_v3` → 同一個 64 字元名稱 | 版本號位於結尾者最易中招 |

**工具描述組成**（`mcp_server/catalog.py::build_description`，本檢視不涵蓋）：標頭為 `[alias · group_name]`（`group_name` 為空時退為 `[alias]`），其後接 `description`（為空時退為 `Run registered query '<name>'`），最後若 `result_description` 非空則另起一行 `Returns: ...`。`group_name` 讓模型拿到維運者自己的分類而非從名稱猜測；`result_description` 說明查詢回傳什麼（通常是欄位清單），**無任何驗證機制**，純粹是寫給 LLM 看的文件 —— 不寫的話，模型只能先跑一次查詢才知道結果長什麼樣。

**參數描述組成**（`mcp_server/catalog.py::param_schema`）：型別提示以 ` — ` **接在**定義自身的參數描述之後，不是取代它。`date` 參數得到 `<描述> — Format: YYYY-MM-DD (e.g. 2026-08-01).`；`string`/`number` 無提示，維持原描述。提示放在 description 而非只靠 JSON Schema 的 `format` 關鍵字，是因為模型對後者的遵守度偏低，而綁定值是以字串送進資料庫的 —— 格式錯誤會是執行期錯誤，dialect 層救不回來。

**忠實性依據：** 名稱一律由 `mcp_server/naming.py` 產生（與 `catalog.py` 同一份實作，`tests/test_mcp_catalog_service.py` 有守則測試防止兩份漂移）；迭代順序比照 `ToolCatalog._load` —— alias 依 `GET /databases` 順序，定義依 `GET /{db}/queries/definitions` 順序。**順序決定撞名時誰存活**，因此屬於答案的一部分。

**已知落差：** MCP server 若設定 `MCP_DB_ALIASES`，實際只暴露此處所列的子集。該設定屬於 MCP 行程的環境變數，Hub 無從得知，故本服務一律涵蓋全部 alias。

同理，MCP server 抓取定義時帶 `?permitted_only=true`（見 5.4.0），因此**個別呼叫者看到的工具數量會少於本檢視**：本頁為超級管理員視角的全集，回答「這個定義有沒有被撞名吃掉」；`tools/list` 則是該呼叫者視角，回答「我能用哪些」。兩者不一致屬預期。

**丟棄不再靜默：** `ToolCatalog._load()` 對每個撞名或截斷的定義各寫一筆 `WARNING`，並將明細留在 `catalog.diagnostics()`；`refresh_query_tools` 工具直接回傳該結構（`{"query_tools": N, "shadowed": [...], "truncated": [...]}`），故操作者從 MCP client 端亦可看見。本檢視仍是唯一能在 **Hub 側**看到全貌的地方。

---

## 5. API 端點規格

**基本路徑 (Base URL):** `/api/v1`

**端點分類：**

| 類別 | 路徑模式 | 說明 |
|------|----------|------|
| 全域端點 | `/api/v1/{endpoint}` | 不依賴特定資料庫（健康檢查、認證、資料庫列表、SQL 定義 CRUD、任務狀態） |
| 資料庫範圍端點 | `/api/v1/{db}/{endpoint}` | 需要指定目標資料庫（查詢執行、寫入、程序、表結構探查） |

### 5.1 健康檢查

| 方法 | 路徑 | 認證 | 說明 |
|------|------|------|------|
| `GET` | `/api/v1/health` | 不需要 | 回傳 `{"status": "ok"}` |

### 5.2 資料庫端點 (`/api/v1/databases`)

| 方法 | 路徑 | 認證 | 說明 |
|------|------|------|------|
| `GET` | `/api/v1/databases` | Bearer Token | 列出所有已設定的資料庫連線（僅設定內容，不代表連得上） |
| `GET` | `/api/v1/databases/status` | Bearer Token（**superuser only**） | 即時檢查每個資料庫的連線狀態（詳見 §4.11） |

**回應範例：**

```json
[
  {
    "alias": "hr_db",
    "description": "HR 人力資源資料庫",
    "dsn": "hr-oracle.company.com:1521/hrdb",
    "user": "hr_api_user",
    "pool_min": 2,
    "pool_max": 10,
    "pool_increment": 1
  },
  {
    "alias": "finance_db",
    "description": "財務資料庫",
    "dsn": "fin-oracle.company.com:1521/findb",
    "user": "fin_api_user",
    "pool_min": 2,
    "pool_max": 5,
    "pool_increment": 1
  }
]
```

**`GET /api/v1/databases/status` 回應範例：**

```json
[
  {
    "alias": "hr_db",
    "status": "online",
    "latency_ms": 3.4,
    "error": null,
    "checked_at": "2026-07-15T07:45:58.915811+00:00"
  },
  {
    "alias": "finance_db",
    "status": "offline",
    "latency_ms": null,
    "error": "DPY-4001: no credentials specified\nORA-01005: null password given; logon denied",
    "checked_at": "2026-07-15T07:45:25.809322+00:00"
  }
]
```

**為何收斂至 superuser：** 此端點會實際開連線，並將 **driver 原始錯誤訊息**（可能含主機、埠、帳號等線索）回傳給呼叫端，比僅列設定的 `GET /api/v1/databases`（只需 `get_current_user`）敏感，故改用 `get_current_active_superuser`。非 superuser 呼叫回 403；Admin UI 會 catch 後將該欄顯示為 `—`，不影響 Dashboard 其餘功能。

**`get_db_alias` 共用依賴：**

所有資料庫範圍端點使用 `Depends(get_db_alias)` 驗證 `{db}` 路徑參數：
- 若別名已註冊 → 回傳別名字串
- 若別名未註冊 → 回傳 HTTP 404 並列出可用的資料庫

### 5.3 認證端點 (`/api/v1/auth`)

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

### 5.4 查詢端點 (`/api/v1/queries`)

#### 5.4.0 SQL 定義管理端點（全域，不依賴特定資料庫）

| 方法 | 路徑 | 認證 | 說明 |
|------|------|------|------|
| `GET` | `/queries/definitions` | Bearer Token | 列出所有可用查詢定義（僅 metadata，不含 SQL）。`?permitted_only=true` 只回傳呼叫者確實有權執行者 |
| `GET` | `/queries/definitions/{name}` | 超級管理員 | 取得單一定義完整資訊（含 SQL） |
| `POST` | `/queries/definitions` | 超級管理員 | 新增 SQL 定義 |
| `PUT` | `/queries/definitions/{name}` | 超級管理員 | 更新 SQL 定義 |
| `DELETE` | `/queries/definitions/{name}` | 超級管理員 | 刪除 SQL 定義 |
| `POST` | `/queries/definitions/reload` | 超級管理員 | 從 DB（或 YAML 備援）重新載入至記憶體 |
| `POST` | `/queries/definitions/import-yaml` | 超級管理員 | 將伺服器端 YAML 檔案匯入 DB（已存在的名稱自動跳過） |
| `GET` | `/{db}/queries/definitions/export-yaml` | 超級管理員 | 匯出該資料庫所有 SQL 定義為 YAML 檔案下載 |
| `POST` | `/{db}/queries/definitions/import-yaml-file` | 超級管理員 | 上傳 YAML 檔案匯入 SQL 定義（已存在的名稱自動跳過） |

> **路由順序**：靜態路徑（`reload`、`import-yaml`、`export-yaml`、`import-yaml-file`）定義在 `{name}` 參數路徑之前，避免被參數路徑攔截。

**新增 SQL 定義請求：**

```json
POST /api/v1/queries/definitions
{
  "name": "my_report",
  "group_name": "Sales",
  "description": "Monthly sales report",
  "result_description": "REGION, PRODUCT, TOTAL_AMOUNT",
  "sql": "SELECT region, product, SUM(amount) total_amount\nFROM sales\nWHERE sale_date BETWEEN :start_date AND :end_date\nGROUP BY region, product",
  "permission": "query:my_report",
  "params": {
    "start_date": {"type": "date", "required": true, "description": "Start date"},
    "end_date": {"type": "date", "required": true, "description": "End date"}
  }
}
```

**新增成功回應（HTTP 201）：**

```json
{
  "id": 4,
  "name": "my_report",
  "description": "Monthly sales report",
  "sql": "SELECT region, product, SUM(amount) total_amount\nFROM sales\n...",
  "permission": "query:my_report",
  "params": {
    "start_date": {"type": "date", "required": true, "description": "Start date"},
    "end_date": {"type": "date", "required": true, "description": "End date"}
  },
  "created_at": "2026-03-03T10:00:00",
  "updated_at": "2026-03-03T10:00:00"
}
```

**YAML 匯入回應：**

```json
{
  "message": "YAML import completed",
  "imported": 3,
  "skipped": 0,
  "errors": []
}
```

**YAML 匯出回應：**

```
GET /api/v1/{db}/queries/definitions/export-yaml
Content-Type: application/x-yaml
Content-Disposition: attachment; filename="sql_definitions_default_2026-03-04.yaml"
```

```yaml
- name: employees
  description: List all employees
  sql: "SELECT * FROM EMPLOYEES WHERE DEPT_ID = :dept_id"
  permission: "query:employees"
  params:
    dept_id:
      type: number
      required: true
      description: Department ID
```

**YAML 檔案上傳匯入回應：**

```json
POST /api/v1/{db}/queries/definitions/import-yaml-file
Content-Type: multipart/form-data

{
  "imported": 2,
  "skipped": 1,
  "errors": []
}
```

#### 查詢執行端點（資料庫範圍）

| 方法 | 路徑 | 認證 | 權限 | 說明 |
|------|------|------|------|------|
| `POST` | `/{db}/queries/execute` | Bearer Token | `{permission}:read` | 對指定資料庫執行已註冊的查詢定義 |

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

#### 5.4.1 `fetch_mode = "page"` — 分頁模式（預設）

```json
POST /api/v1/{db}/queries/execute
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

#### 5.4.2 `fetch_mode = "all"` — 整批取回

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

#### 5.4.3 `fetch_mode = "batch"` — 分批取用

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

#### 5.4.4 `fetch_mode = "top_n"` — 取前 N 筆

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

#### 5.4.5 `fetch_mode = "one"` — 取一筆

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

### 5.5 程序端點 (`/api/v1/{db}/procedures`)

| 方法 | 路徑 | 認證 | 權限 | 說明 |
|------|------|------|------|------|
| `POST` | `/{db}/procedures/execute` | Bearer Token | `procedure:{name}:execute` | 同步執行 Procedure / Function |
| `POST` | `/{db}/procedures/execute-async` | Bearer Token | `procedure:{name}:execute` | 非同步執行，回傳 task_id |

**Procedure 呼叫請求：**

```json
POST /api/v1/{db}/procedures/execute
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

### 5.6 寫入端點 (`/api/v1/{db}/writes`)

| 方法 | 路徑 | 認證 | 權限 | 說明 |
|------|------|------|------|------|
| `POST` | `/{db}/writes/execute` | Bearer Token | `table:{table}:write` | 對指定資料庫執行單筆 INSERT / UPDATE / DELETE |
| `POST` | `/{db}/writes/batch` | Bearer Token | `table:{table}:write` | 對指定資料庫批次交易寫入（多個 DML 操作在同一交易） |
| `GET` | `/tasks/{task_id}` | Bearer Token | 無特殊權限 | 查詢非同步任務狀態 — 全域 |

**INSERT 請求（同步）：**

```json
POST /api/v1/{db}/writes/execute
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
GET /api/v1/tasks/b4c2d1e0-f5a6-7890-bcde-fg2345678901
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
POST /api/v1/{db}/writes/batch
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

### 5.7 表結構探查端點 (`/api/v1/{db}/schemas`)

| 方法 | 路徑 | 認證 | 說明 |
|------|------|------|------|
| `GET` | `/{db}/schemas/describe/{table}` | Bearer Token | 取得指定資料庫中目標表/View 的欄位定義與主鍵資訊 |

**請求範例：**

```
GET /api/v1/hr_db/schemas/describe/HR.EMPLOYEES
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

### 5.8 API 使用統計端點 (`/api/v1/audit`)

回答「哪些使用者呼叫了哪些 API、打在哪個資料來源、幾次、多快」以及「該次查詢實際傳了什麼參數」。資料來源為 `api_hub_audit_logs`（§6.1.7）。

**全部端點皆為 superuser only**（`Depends(get_current_active_superuser)`，非 superuser 回 `403`）—— 報表會揭露所有呼叫者的請求參數。

這些端點**不在 `/{db}/` 底下**：報表要跨資料來源比較，`db_alias` 是篩選條件而非路由段。

| 方法 | 路徑 | 說明 |
|------|------|------|
| `GET` | `/audit/usage` | 統計，依指定維度分組 |
| `GET` | `/audit/logs` | 統計背後的個別請求，新到舊 |
| `GET` | `/audit/logs/{log_id}` | 單筆，`params` 一律完整回傳 |
| `GET` | `/audit/filters` | 指定期間內出現過的 distinct 使用者／資料來源／API 名稱，供 UI 下拉選單 |

#### 5.8.1 `GET /audit/usage`

| 參數 | 預設 | 說明 |
|------|------|------|
| `date_from` / `date_to` | 近 30 天 | 含端點；`date_to` 涵蓋當日整天 |
| `principal` | 全部 | 使用者帳號 |
| `db_alias` | 全部 | 資料來源別名 |
| `action` | 全部 | API 名稱 |
| `action_type` | `query,procedure,write` | 逗號分隔；預設排除 `definition` 與 `report`，空字串代表完全不過濾 |
| `group_by` | `user,db,api` | 逗號分隔，可組合 `user`/`db`/`api`/`day`；空字串則 `items` 為空、只回 `totals` |
| `sort` | `-calls` | `calls`/`success`/`failed`/`avg_duration_ms`/`max_duration_ms`/`total_rows`/`last_used_at`，前綴 `-` 為遞減 |
| `page` / `page_size` | `1` / `50` | `page_size` 上限 **200** |

**度量定義：** `success` = `status_code < 400`，`failed` = `status_code >= 400`，`calls` = 兩者之和；`avg_duration_ms` 四捨五入為整數；`total_rows` 為 `result_rows` 之和（`NULL` 視為 0）；`last_used_at` 為該組 `ts` 最大值。未被 `group_by` 選中的維度在 `items` 中為 `null`。

**回應範例：**

```json
{
  "total_groups": 128,
  "page": 1,
  "page_size": 50,
  "totals": { "calls": 4210, "success": 4180, "failed": 30,
              "avg_duration_ms": 168, "max_duration_ms": 4120,
              "total_rows": 918233, "last_used_at": "2026-07-30T10:12:33" },
  "items": [
    { "principal": "alice", "db_alias": "DW1", "action": "get_orders", "day": null,
      "calls": 320, "success": 318, "failed": 2,
      "avg_duration_ms": 145, "max_duration_ms": 980,
      "total_rows": 74210, "last_used_at": "2026-07-30T10:12:33" }
  ]
}
```

統計以**即時 `GROUP BY`** 計算，沒有預聚合表或 materialized view —— 因為分組維度由呼叫者自由勾選，預聚合會限制彈性；效能靠 §6.1.7 的四個複合索引與保留期限所界定的表大小。

#### 5.8.2 `GET /audit/logs`

篩選參數同 §5.8.1（drill-down 時帶入該組的維度值），另加 `status_code` 與 `min_duration_ms`。`page` / `page_size` 預設 `1` / `25`，`page_size` 上限 **100**。

每筆回傳 `id`、`ts`、`principal`、`principal_type`、`api_key_name`、`client_ip`、`method`、`path`、`db_alias`、`action_type`、`action`、`status_code`、`duration_ms`、`result_rows`、`params`。

**params 大小處理：** `params` 不截斷儲存，`writes/batch` 的 payload 可能達數 MB，一次 100 列會使回應爆量。因此僅在序列化後小於 `AUDIT_PARAMS_INLINE_LIMIT`（預設 4096 bytes）時內嵌；否則回 `params: null`、`params_omitted: true`、`params_bytes: <size>`，由前端展開該列時改呼叫 `GET /audit/logs/{log_id}` 取回完整內容。此上限只保護 API 回應大小，不減少儲存量。

#### 5.8.3 錯誤處理

| 情況 | 回應 |
|------|------|
| `date_from > date_to`、未知的 `group_by` / `sort` / `action_type` 值 | `400`，`detail` 附可用值 |
| 非 superuser | `403` |
| `GET /audit/logs/{log_id}` 查無資料 | `404` |
| 條件查無資料 | `200`，空 `items` 與 `total_groups: 0`（不是 404） |

時間基準沿用 `api_hub_audit_logs.ts` 的無時區本地時間，按日分組使用 DB 端 `date()` 截斷，不做時區轉換。

---

### 5.9 MCP 工具目錄端點 (`/api/v1/mcp`)

回答「哪些 SQL 定義真的變成了 MCP 工具、哪些被靜默丟棄」。衍生規則與撞名途徑見 §4.12。

**唯讀端點，無對應的寫入操作** —— MCP server 是獨立行程，組態來自 `MCP_*` 環境變數，Admin UI 無處可寫。

| 方法 | 路徑 | 說明 |
|------|------|------|
| `GET` | `/mcp/tools` | 列出每個 (資料來源, SQL 定義) 及其衍生的 MCP 工具名稱與暴露狀態 |

**superuser only**（`Depends(get_current_active_superuser)`，非 superuser 回 `403`）—— 本端點跨全部 alias，否則呼叫者可藉此列舉自己並無 `query:<name>` 權限的定義。與 §5.8 同理，不置於 `/{db}/` 之下。

稽核以 `action_type="report"`、`action="mcp_tools"` 記錄，故預設不計入 §5.8 的資料存取量。

**回應範例：**

```json
{
  "tool_name_max_len": 64,
  "db_aliases": ["DW", "pg_test"],
  "summary": { "definitions": 2, "exposed": 1, "shadowed": 1, "truncated": 0 },
  "tools": [
    { "tool_name": "query_dw_eo_detail", "db_alias": "DW", "definition": "eo_detail",
      "description": "EO detail rows", "params": ["frozen_date"],
      "truncated": false, "exposed": true, "shadowed_by": null,
      "shadows": [ { "db_alias": "DW", "definition": "EO Detail" } ] },
    { "tool_name": "query_dw_eo_detail", "db_alias": "DW", "definition": "EO Detail",
      "description": "", "params": [],
      "truncated": false, "exposed": false,
      "shadowed_by": { "db_alias": "DW", "definition": "eo_detail" }, "shadows": [] }
  ]
}
```

| 欄位 | 說明 |
|------|------|
| `exposed` | 此定義是否真的成為 MCP 工具；`false` 代表名稱已被先到者佔用而遭丟棄 |
| `shadowed_by` | `exposed=false` 時，佔用該名稱的 (資料來源, 定義)；否則為 `null` |
| `shadows` | 此定義所排擠掉的其他定義，讓撞名從兩端都看得見 |
| `truncated` | 衍生名稱是否曾超過 64 字元而被截斷 |

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

#### 6.1.6 SQL 定義表 `api_hub_sql_definitions`

| 欄位 | 型態 | 約束 | 說明 |
|------|------|------|------|
| `id` | INTEGER | PK, AUTO | 主鍵 |
| `name` | VARCHAR(200) | UNIQUE, NOT NULL, INDEX | 定義名稱（用於 API 的 `query_name`） |
| `description` | TEXT | DEFAULT '' | 人類可讀說明 |
| `sql` | TEXT | NOT NULL | SQL SELECT 語句（支援 `:param` 綁定變數） |
| `permission` | VARCHAR(200) | NOT NULL | 權限名稱（如 `query:employees`） |
| `params` | JSONB | DEFAULT '{}' | 參數定義（鍵值結構，每個參數含 `type`/`required`/`description`） |
| `created_at` | DATETIME | SERVER DEFAULT SYSDATE | 建立時間 |
| `updated_at` | DATETIME | SERVER DEFAULT SYSDATE, ONUPDATE | 更新時間 |

**`params` JSONB 結構範例：**

```json
{
  "dept": {
    "type": "string",
    "required": false,
    "description": "Filter by department code"
  },
  "start_date": {
    "type": "date",
    "required": true,
    "description": "Start date (YYYY-MM-DD)"
  }
}
```

#### 6.1.7 稽核軌跡表 `api_hub_audit_logs`

由 `AuditMiddleware` 每個 API request 寫入一列（best-effort，寫入失敗只記 log、不影響回應）。同時是 API 使用統計報表（§5.8）的唯一資料來源。

| 欄位 | 型態 | 約束 | 說明 |
|------|------|------|------|
| `id` | BIGINT | PK, AUTO | 主鍵 |
| `ts` | DATETIME | SERVER DEFAULT NOW, INDEX | 請求時間（平台 PostgreSQL 本地時間，無時區） |
| `principal_type` | VARCHAR(20) | | `user` / `api_key` / `anonymous` |
| `principal` | VARCHAR(100) | INDEX | 呼叫者帳號 |
| `api_key_id` | INTEGER | NULL | 使用的 API key ID |
| `api_key_name` | VARCHAR(100) | NULL | 使用的 API key 名稱 |
| `client_ip` | VARCHAR(64) | | 來源 IP（依 `TRUSTED_PROXIES` 還原） |
| `method` | VARCHAR(10) | | HTTP method |
| `path` | VARCHAR(500) | | 請求路徑 |
| `db_alias` | VARCHAR(64) | NULL | 目標資料來源別名 |
| `action` | VARCHAR(200) | NULL | 操作對象名稱（query_name / procedure 名 / 表名 / 定義名） |
| `status_code` | INTEGER | | HTTP 狀態碼 |
| `duration_ms` | INTEGER | | 整條 middleware 鏈的耗時 |
| `action_type` | VARCHAR(20) | NULL | 操作分類，見下表 |
| `params` | JSONB | NULL | 該次請求的參數，**不遮罩、不截斷** |
| `result_rows` | INTEGER | NULL | 回傳或影響的筆數 |

**複合索引**（對應報表的篩選維度）：`ix_audit_principal_ts`、`ix_audit_db_alias_ts`、`ix_audit_action_ts`、`ix_audit_action_type_ts`，皆為 `(維度, ts)`。

**`action_type` 分類與各端點寫入的內容：**

| `action_type` | 端點 | `action` | `params` | `result_rows` |
|---|---|---|---|---|
| `query` | `POST /{db}/queries/execute` | `query_name` | `{params, fetch_mode}` | 回傳筆數；`count` 模式取 `total` |
| `procedure` | `POST /{db}/procedures/execute`、`/execute-async` | 程序名 | `{params, out_params, is_function}` | NULL |
| `write` | `POST /{db}/writes/execute` | 表名 | `{operation, data, where, async_mode}` | `affected_rows` |
| `write` | `POST /{db}/writes/batch` | 表名 | `{operations, async_mode}` | `total_affected` |
| `definition` | `POST`/`PUT`/`DELETE /{db}/queries/definitions[/{name}]` | 定義名 | 請求 body（DELETE 為 NULL） | NULL |
| `report` | `GET /api/v1/audit/*` | `usage`/`logs`/`log_detail`/`filters` | NULL | NULL |

`AuditMiddleware` 在 `call_next` 之後才組 row，讀不到已被消費的 request body，所以上表的資料是由各 router 呼叫 `app/middleware/audit.py::record_audit()` 放進 `request.state`，middleware 只負責搬進 audit row。失敗的請求（4xx/5xx）同樣留下含 `params` 的列。

**保留期限：** 沒有任何自動清理機制。`params` 原樣儲存（`writes/batch` 的 payload 可能達數 MB），表的大小完全由 `AUDIT_RETENTION_DAYS` 加上手動／排程執行 `python -m scripts.purge_audit_logs [--days N] [--dry-run]` 決定；該指令分批 `DELETE`（預設每批 10000 列）以避免長交易鎖表。

> **安全性：** `params` 不遮罩，呼叫方若把密碼、token 之類的值當作 bind 參數傳入會原樣落地。此表僅 superuser 可讀，但資料庫備份會一併帶走。

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

┌─────────────────────────────┐
│  api_hub_sql_definitions    │
├─────────────────────────────┤
│ id              PK          │
│ name            UQ, INDEX   │
│ description                 │
│ sql             NN          │
│ permission      NN          │
│ params          JSONB       │
│ created_at                  │
│ updated_at                  │
└─────────────────────────────┘
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
    │  POST /{db}/queries/execute    │                              │
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

#### 7.1.1 ISAP SSO 登入流程（選用，`ADMIN_SSO_ENABLED`）

Admin UI 除本地帳密外，可改用 ISAP 帳號登入。走 isap-auth 的 app-code 流程，API Hub 仍然自己簽發 JWT，因此後續每個請求的驗證路徑與本地登入完全相同。

```
  Browser                    API Hub                     isap-auth
    │                           │                            │
    │  GET /auth/sso/login      │                            │
    │─────────────────────────▶│                            │
    │                           │  產生 state，寫入          │
    │                           │  httpOnly SameSite cookie  │
    │  302 → /authorize?app_id=&redirect_uri=&state=         │
    │◀─────────────────────────│                            │
    │  GET /authorize ...                                    │
    │───────────────────────────────────────────────────────▶│
    │                           │        檢查 SSO cookie；   │
    │                           │        未登入則先顯示登入頁│
    │  302 → /auth/sso/callback?code=&state=                 │
    │◀───────────────────────────────────────────────────────│
    │  GET /auth/sso/callback   │                            │
    │─────────────────────────▶│                            │
    │                           │  比對 state 與 cookie      │
    │                           │  POST /api/v1/token        │
    │                           │  （走 docker 內網）        │
    │                           │───────────────────────────▶│
    │                           │◀───────────────────────────│
    │                           │  resolve_sso_user()        │
    │                           │  建立 JWT (HS256)          │
    │  302 → /admin#sso_token=eyJ...                         │
    │◀─────────────────────────│                            │
```

要點：

- **`state` 是必要的防護**，不是選配。缺少它時，攻擊者可先自行取得一個 code，再誘導受害者開啟帶該 code 的 callback 網址，使受害者靜默登入成攻擊者的帳號。isap-auth 會原樣帶回 `state`，比對後即清除 cookie。
- **JWT 以 URL fragment 交付**，不用 query string —— fragment 不會送到伺服器，因此不會進入 nginx access log 或 Referer。SPA 讀取後立即以 `history.replaceState` 清除。
- **使用者解析共用** `app/services/sso_service.py::resolve_sso_user`，與 MCP identity bridge（`POST /auth/sso-token`）同一份政策：`SSO_AUTOCREATE` 決定未建檔者自動建立或 403，服務帳號與停用帳號一律拒絕。
- **失敗一律導回登入頁**並附 `#sso_error=` slug（`missing_code`、`state_mismatch`、`auth_unreachable`、`invalid_code`、`not_provisioned`、`not_eligible`），不回傳 JSON。
- 本地帳密登入保留，isap-auth 故障時管理者仍可進入。
- `ADMIN_SSO_ENABLED=false`（預設）時兩個端點回 404。

應用本身必須先登記在 isap-auth 的 `registered_app` 允許清單中。`scripts/register_isap_app.py` 以 `PUT /apps/{app_id}` 搭配 per-app bootstrap token 完成登記，由 compose one-shot（`apihub-app-register`）在 api 啟動前執行。該端點是宣告式的：`redirect_uris` 整批取代，因此 `deploy/*/.env.isap` 的 `ISAP_REDIRECT_URIS` 是唯一真實來源，重複執行結果一致。未設定 token 時腳本印出訊息並以 0 退出，不阻擋不使用 SSO 的部署。

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
| 查詢 | `query:employees`、`query:RD_TC*`（前綴）、`query:*`（整類） | `read` |
| 程序 | `procedure:PKG_HR.GET_SALARY`、`procedure:*` | `execute` |
| 表 | `table:HR.EMPLOYEES`、`table:*` | `write` |

### 7.4 權限檢查邏輯

`PermissionChecker` 類別實作：

```
1. 若 user.is_superuser == True → 直接通過（繞過所有檢查）
2. 遍歷 user.roles：
   2a. 遍歷 role.permissions（動作必須相符，否則略過此權限）：
       - 精確比對：perm.resource == 請求資源 → 通過
       - 前綴萬用字元比對：perm.resource 以 "*" 結尾時，
         取 "*" 之前的字串為前綴，若請求資源以此前綴開頭 → 通過
3. 若無任何權限匹配 → 回傳 HTTP 403 Forbidden
```

> **萬用字元語意（1.6.0 起）：** 結尾 `*` 一律視為「前綴比對」，`*` 之前的整段字串即為前綴。
> 因此 `query:*`（整類）與 `query:RD_TC*`（部分前綴）走同一條規則 —— 前者前綴為 `query:`、後者為 `query:RD_TC`。
> 舊版僅支援結尾 `:*` 的整類萬用字元；`query:RD_TC*` 這類部分前綴在舊版不會匹配（會一律 403）。

**萬用字元範例：**

| 持有權限 | 請求資源 | 請求動作 | 結果 |
|----------|----------|----------|------|
| `query:*` + `read` | `query:employees` | `read` | 通過 |
| `query:*` + `read` | `query:departments` | `read` | 通過 |
| `query:RD_TC*` + `read` | `query:RD_TC_All` | `read` | 通過（前綴 `query:RD_TC`） |
| `query:HR*` + `read` | `query:HR_ORG` | `read` | 通過（前綴 `query:HR`） |
| `query:HR*` + `read` | `query:RD_TC_All` | `read` | 拒絕（前綴不符） |
| `query:RD_TC*` + `read` | `query:RD_TC_All` | `write` | 拒絕（動作不符） |
| `query:employees` + `read` | `query:departments` | `read` | 拒絕 |
| `procedure:*` + `execute` | `procedure:PKG.PROC1` | `execute` | 通過 |
| `table:HR.EMP` + `write` | `table:HR.DEPT` | `write` | 拒絕 |

### 7.5 權限綁定位置

| 端點類型 | 權限資源 | 動作 | 取得方式 |
|----------|----------|------|----------|
| 查詢 | DB 中 `api_hub_sql_definitions.permission` 欄位 | `read` | `SqlDefinition.permission` |
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

> 所有非同步任務均接受 `db_alias` 參數，指定在哪個資料庫上執行操作。Worker 進程在首次執行任務時會初始化所有資料庫的連線池。

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

每個 Celery Worker 進程在首次執行任務時呼叫 `init_pool()` 初始化所有資料庫的 cx_Oracle 連線池（透過 `DatabaseRegistry`）。設定 `worker_prefetch_multiplier=1` 確保每個 worker 一次只處理一個任務，避免過度消耗資料庫連線。

### 8.7 Flower 監控

- 存取位址：`http://localhost:5555`
- 提供即時 Worker 狀態、任務歷史、佇列深度、成功/失敗率

---

## 9. SQL 定義系統

### 9.1 儲存架構

SQL 定義以 **PostgreSQL** 為主要儲存（`api_hub_sql_definitions` 表），支援透過 API 和 Admin UI 進行完整 CRUD 操作。YAML 檔案作為備援來源，在 DB 為空時自動回退使用。

**載入優先順序：**

```
啟動 / 重載
    │
    ├─ 嘗試從 PostgreSQL 載入
    │     │
    │     ├─ DB 有資料 → 使用 DB 定義 ✓
    │     │
    │     └─ DB 為空 → 回退至 YAML 檔案
    │                     │
    │                     └─ 從 app/sql_definitions/*.yaml 載入 ✓
    │
    └─ CRUD 操作後 → 自動呼叫 reload_from_db() 刷新記憶體
```

### 9.2 定義結構

每個 SQL 定義包含以下欄位：

| 欄位 | 型態 | 必填 | 說明 |
|------|------|------|------|
| `name` | string | 是 | 唯一識別名稱（用於 API 請求的 `query_name`） |
| `group_name` | string | 否 | 自由文字分類，Admin UI 列表的分群依據；亦進入 MCP 工具描述標頭。留空歸為 `(ungrouped)`，不參與授權 |
| `description` | string | 否 | 人類可讀說明 |
| `result_description` | string | 否 | 回傳內容說明（通常為欄位清單），進入 MCP 工具描述的 `Returns:` 一行。**不做任何驗證** |
| `sql` | string | 是 | SQL SELECT 語句（支援 `:param` 綁定變數） |
| `permission` | string | 否 | 權限名稱（預設自動生成 `query:{name}`） |
| `params` | JSON object | 否 | 參數定義，每個參數含 `type`/`required`/`description` |
| `order_by` | JSON array | 否 | 排序欄位 `[{column, direction}]`，由 Hub 於外層套用（見 5.4） |

**參數 `type` 允許值：** `string`（預設）、`number`、`date`

### 9.3 CRUD 操作

#### 透過 Admin UI

1. 進入 **SQL Definitions** 頁面；左欄選擇群組（`group_name`，依名稱排序），右欄即列出該群組底下的定義（依名稱排序）
2. 點擊 **+ New Definition** 新增、**Edit** 編輯、**Delete** 刪除
3. 表單包含：**群組**、名稱、描述、**Returns**、權限、SQL 編輯區（monospace）、參數 JSON 編輯器
   - 新增時填入群組後，**名稱**自動帶入 `<群組>_` 開頭、**權限**自動帶入 `query:<名稱>`；手動改過的欄位不會再被覆寫
   - 群組是純標籤，不參與授權；留空會歸到 `(ungrouped)`
   - **Returns**（`result_description`）填該查詢回傳哪些欄位。Hub 本身不使用此欄位，它的唯一消費者是 MCP 工具描述 —— 留空的定義，LLM 只能先跑一次才知道結果長什麼樣
4. 所有操作即時生效（自動刷新記憶體 Registry）

#### 透過 API

完整的 RESTful CRUD — 詳見 [5.4 查詢端點](#54-查詢端點-apiv1queries)。

### 9.4 YAML 匯出與匯入

#### 9.4.1 伺服器端 YAML 匯入（遷移用）

對於既有的 YAML 定義，提供一鍵匯入功能：

```
POST /api/v1/queries/definitions/import-yaml    (需要超級管理員權限)
```

- 掃描 `app/sql_definitions/*.yaml` 目錄中所有 YAML 檔案
- 已存在的名稱自動跳過（不覆蓋）
- 回傳匯入數量、跳過數量、錯誤列表

也可在 Admin UI 的 SQL Definitions 頁面點擊 **Import YAML** 按鈕執行。

#### 9.4.2 YAML 匯出（下載）

將指定資料庫的所有 SQL 定義匯出為 YAML 檔案下載：

```
GET /api/v1/{db}/queries/definitions/export-yaml    (需要超級管理員權限)
```

- 查詢該 `db_alias` 的所有 SQL 定義，依名稱排序
- 序列化為 YAML 陣列（包含 `name`、`description`、`sql`、`permission`、`params`）
- 以 `Content-Disposition: attachment` 回應，檔名格式：`sql_definitions_{db}_{date}.yaml`
- Admin UI 使用 `fetch()` + `Blob` 方式下載（透過 Authorization header 認證）

#### 9.4.3 YAML 檔案上傳匯入

上傳 YAML 檔案將定義匯入至指定資料庫（支援跨資料庫遷移）：

```
POST /api/v1/{db}/queries/definitions/import-yaml-file    (需要超級管理員權限)
```

- 接收 `multipart/form-data` 上傳的 `.yaml` / `.yml` 檔案
- 使用 `yaml.safe_load` 解析，支援陣列格式及單一定義格式
- `db_alias` 由路徑 `{db}` 決定（忽略檔案中的 `db_alias` 欄位），同一份匯出檔可匯入至任意資料庫
- 已存在的 `(name, db_alias)` 組合自動跳過
- 回傳 `{ imported, skipped, errors }` 摘要
- 匯入後自動呼叫 `sql_registry.reload_from_db()` 刷新記憶體

**典型遷移流程：**

```
1. 在來源資料庫點擊 Export YAML → 下載 YAML 檔案
2. 切換至目標資料庫
3. 點擊 Upload YAML → 選擇下載的檔案 → 確認匯入
4. 重複名稱自動跳過，參數定義完整保留
```

### 9.5 YAML 檔案格式（備援/遷移用）

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

### 9.6 範例定義

**透過 API 新增：**

```json
POST /api/v1/queries/definitions
{
  "name": "employees",
  "description": "Query active employees with optional department filter",
  "sql": "SELECT emp_id, emp_name, department, hire_date, salary\nFROM employees\nWHERE status = 'ACTIVE'\nAND (:dept IS NULL OR department = :dept)\nORDER BY emp_id",
  "permission": "query:employees",
  "params": {
    "dept": {
      "type": "string",
      "required": false,
      "description": "Filter by department code"
    }
  }
}
```

**等同的 YAML 定義（employees.yaml）：**

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

### 9.7 可選參數設計模式

使用 Oracle 的 NULL 合併模式實現可選篩選：

```sql
WHERE (:param IS NULL OR column = :param)
```

- 當 `param` 為 `NULL` 時，`:param IS NULL` 為 `TRUE`，條件被跳過
- 當 `param` 有值時，作為精確比對篩選

### 9.8 熱重載

```
POST /api/v1/queries/definitions/reload    (需要超級管理員權限)
```

回應：

```json
{"message": "SQL definitions reloaded", "count": 3}
```

從 DB（或 YAML 備援）重新載入所有定義至記憶體，無需重啟服務。CRUD 操作後會自動觸發重載，通常不需手動呼叫。

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
| **Dashboard** | — | 統計總覽（使用者數、角色數、權限數、SQL 定義數、資料庫數）、資料庫列表（含**即時連線狀態**欄與 Refresh 按鈕）、快捷資訊 |
| **Users** | — | 使用者清單 + 新增使用者（帳號、密碼、角色指派、超級管理員設定） |
| **Roles** | — | 角色清單 + 新增角色（名稱、描述、權限指派） |
| **Permissions** | — | 權限清單 + 新增權限（resource、action、描述） |
| **SQL Definitions** | — | 左右兩欄清單（左：**群組**依名稱排序並顯示定義數；右：該群組的定義依名稱排序）+ SQL 定義完整 CRUD：新增、編輯、刪除、檢視詳情（含 SQL）、YAML 匯出下載、YAML 檔案上傳匯入、伺服器端 YAML 匯入、熱重載 |
| **Query Console** | — | 互動式查詢測試台（選擇 fetch mode、填入參數、即時查看結果表格） |
| **Procedure Console** | — | 執行 Procedure / Function（支援 OUT 參數、非同步模式） |
| **Write Console** | — | 執行 INSERT / UPDATE / DELETE（支援非同步佇列模式） |
| **Data Maintenance** | — | 資料維護介面：讀取表結構 → 動態產生表單 → 支援單筆/批次交易寫入 |
| **Async Tasks** | — | 查詢 Celery 任務狀態、連結至 Flower 監控面板 |
| **API Usage Report** | — | API 使用統計（依日期區間／使用者／資料來源／API／類型篩選，可勾選 user／DW／API／日為分組依據，度量欄位可點擊排序），並可 drill-down 至個別請求明細，單筆展開檢視該次的請求參數 JSON 並複製。**僅 superuser**，非 superuser 連側邊欄項目都不顯示 |
| **MCP Tools** | — | 唯讀檢視 MCP 工具目錄：每個 (資料來源, SQL 定義) 對應的工具名稱、是否真的暴露、撞名時被誰佔用、是否遭 64 字元截斷；可依名稱篩選或只看有問題者。**不含任何設定功能**（見 §4.12、§5.9）。**僅 superuser**，非 superuser 連側邊欄項目都不顯示 |

### 10.3 UI 元件

```
┌─────────────────────────────────────────────────────────────────┐
│  ┌──────────┐  ┌──────────────────────────────────────────────┐ │
│  │          │  │  Top Header  [DB: ▼ hr_db] [user] [Logout]  │ │
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
6. 登入成功後載入可用資料庫列表（`GET /databases`），填充 Header 中的資料庫選擇器
7. 選擇的資料庫儲存於 `localStorage`（key: `hub_current_db`），頁面重載後自動恢復
8. Logout 清除 `localStorage` 中的 Token

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
    ├── app.js            # 應用邏輯（路由、共用 helper、多數頁面渲染）
    ├── usage_report.js   # API Usage Report 專屬（在 app.js 之後載入，沿用其全域 helper）
    └── mcp_tools.js      # MCP Tools 專屬（同樣在 app.js 之後載入，沿用其全域 helper）
```

`/admin/js` 是整個目錄掛載為 StaticFiles，新增 JS 檔只需在 `index.html` 加 `<script>` 標籤，不必改 `main.py`。

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
選擇目標資料庫（Header DB Selector）
                        ↓
使用者輸入表名 → 呼叫 GET /{db}/schemas/describe/{table}
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
  送出 → POST /{db}/writes/batch（同步或非同步）
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
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
           ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
           │ Oracle DB A  │ │ Oracle DB B  │ │ Oracle DB C  │
           │ (hr_db)      │ │ (finance_db) │ │ (...)        │
           │  11.1.0.4    │ │  11.1.0.4    │ │  11.1.0.4    │
           └──────────────┘ └──────────────┘ └──────────────┘
```

### 12.4 Volume 掛載

| 掛載 | 說明 |
|------|------|
| `./app/sql_definitions` → `/app/app/sql_definitions` | SQL 定義 YAML 備援檔（匯入用） |
| `./app/database_definitions` → `/app/app/database_definitions` | 資料庫定義檔 |
| `redis_data` (named volume) | Redis 資料持久化 |

### 12.5 快速啟動

```bash
# 1. 複製並編輯組態
cp .env.example .env
vi .env   # 設定 SECRET_KEY 等基本組態

# 2. 設定多資料庫連線
cp app/database_definitions/databases.yaml.example app/database_definitions/databases.yaml
vi app/database_definitions/databases.yaml   # 設定各資料庫 DSN、帳號
# 或使用環境變數覆蓋密碼：export DB_HR_DB_PASSWORD=real_password

# 3. 啟動所有服務
docker-compose up -d

# 4. 初始化資料庫表與管理員帳號
docker-compose exec api python -m scripts.init_db

# 5. 取得 Token
curl -X POST http://localhost:8000/api/v1/auth/token \
  -d "username=admin&password=changeme"

# 6. 列出可用資料庫
curl http://localhost:8000/api/v1/databases \
  -H "Authorization: Bearer <token>"

# 7. 對指定資料庫執行查詢
curl -X POST http://localhost:8000/api/v1/hr_db/queries/execute \
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
| **資料庫過載** | 連線池上限 + Celery 佇列緩衝 | `multi_pool.py`, `celery_app.py` |
| **密碼外洩（DB）** | 環境變數覆蓋 YAML 密碼（`DB_{ALIAS}_PASSWORD`） | `multi_pool.py` |

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
- [ ] 使用 `DB_{ALIAS}_PASSWORD` 環境變數設定各資料庫正式密碼
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
| `DATABASE_DEFINITIONS_DIR` | `app/database_definitions` | 資料庫定義檔目錄路徑 |
| `DB_{ALIAS}_PASSWORD` | — | 覆蓋指定資料庫別名的密碼（如 `DB_HR_DB_PASSWORD`） |
| `DB_HEALTH_CHECK_TIMEOUT` | `5.0` | `GET /api/v1/databases/status` 的**整體**逾時秒數 —— 所有 alias 共用此預算，非每個 alias 各算（見 §4.11） |
| `AUDIT_RETENTION_DAYS` | `365` | `python -m scripts.purge_audit_logs` 的預設保留天數（見 §6.1.7）。無自動清理，需自行排程 |
| `AUDIT_PARAMS_INLINE_LIMIT` | `4096` | `GET /audit/logs` 內嵌 `params` 的位元組上限，超過則改由 `GET /audit/logs/{id}` 單筆取回（見 §5.8.2） |
| `ADMIN_SSO_ENABLED` | `false` | Admin UI 的 ISAP SSO 登入。關閉時 `/auth/sso/login`、`/auth/sso/callback` 回 404，登入頁不顯示按鈕（見 §7.1.1） |
| `APIHUB_PUBLIC_URL` | — | 本服務對外位址（如 `https://host/apihub`）。送給 isap-auth 的 `redirect_uri` 由此組出，**必須**與 `ISAP_REDIRECT_URIS` 登記值逐字相同 |
| `ISAP_AUTH_PUBLIC_URL` | — | isap-auth 對外位址，供瀏覽器導向 `/authorize` |
| `ISAP_AUTH_INTERNAL_URL` | — | isap-auth 內網位址，供後端 `POST /token` 交換 code，不繞出 gateway |
| `ISAP_APP_ID` | `apihub` | 本應用在 isap-auth 的 app_id，必須與 bootstrap token 綁定的值相同 |
| `SSO_AUTOCREATE` | `false` | 未建檔的 SSO 使用者是否自動建立為無角色帳號。同時作用於 Admin UI 與 MCP bridge；`MCP_BRIDGE_AUTOCREATE` 保留為相容別名 |
| `ISAP_APP_NAME` | `Oracle API Hub` | 註冊時送出的顯示名稱。**僅** `scripts/register_isap_app.py` 讀取 |
| `ISAP_REGISTRATION_TOKEN` | — | 自註冊用的 bootstrap token（`isapreg_…`）。**僅**註冊腳本讀取，放在 `deploy/*/.env.isap` 而非 `.env`：它能改寫本應用的 `redirect_uris`，外洩即為 auth code 竊取途徑。留空時註冊腳本跳過並以 0 退出 |
| `ISAP_REDIRECT_URIS` | — | 登記的 callback 位址，換行或逗號分隔。宣告式整批取代。**僅**註冊腳本讀取 |

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
│   │   ├── multi_pool.py                # DatabaseRegistry — 多資料庫連線池管理器
│   │   ├── oracle_pool.py               # 向下相容封裝（委派至 DatabaseRegistry）
│   │   └── session.py                   # SQLAlchemy ORM Engine / Session
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py                      # User / Role / Permission ORM 模型
│   │   └── sql_definition.py            # SqlDefinitionModel ORM（api_hub_sql_definitions）
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── auth.py                      # 認證相關 Pydantic Schema
│   │   ├── query.py                     # 查詢 / 程序 / 寫入 / 批次交易 Pydantic Schema
│   │   └── sql_definition.py            # SQL 定義 CRUD Pydantic Schema
│   ├── services/
│   │   ├── __init__.py
│   │   ├── auth_service.py              # 認證邏輯 + PermissionChecker
│   │   ├── sql_registry.py              # SQL 定義 Registry（DB 為主，YAML 備援）
│   │   ├── query_service.py             # SQL 查詢執行 + ROWNUM 分頁
│   │   ├── procedure_service.py         # Oracle Procedure / Function 呼叫
│   │   ├── write_service.py             # DML 操作（INSERT/UPDATE/DELETE + 批次交易）
│   │   ├── schema_service.py            # 表結構探查（ALL_TAB_COLUMNS / 主鍵偵測）
│   │   ├── db_health_service.py         # 資料來源即時連線檢查（併發 + 共用 deadline）
│   │   └── mcp_catalog_service.py       # MCP 工具目錄唯讀投影（撞名 / 截斷偵測）
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
│   │       ├── audit.py                 # API 使用統計端點（/audit/usage / logs / filters，superuser only）
│   │       ├── health.py                # 健康檢查端點
│   │       ├── databases.py             # 資料庫列表端點 + get_db_alias 依賴
│   │       ├── queries.py               # 查詢端點（CRUD / 匯出 / 匯入 / 重載 / /{db}/執行）
│   │       ├── procedures.py            # 程序端點（/{db}/同步 / 非同步）
│   │       ├── writes.py                # 寫入端點（/{db}/單筆 / 批次 / 非同步 / 狀態查詢）
│   │       ├── schemas.py              # 表結構探查端點（/{db}/describe）
│   │       └── mcp.py                   # MCP 工具目錄唯讀端點（/mcp/tools，superuser only）
│   ├── admin/                           # 管理介面 SPA
│   │   ├── index.html                   # SPA 主頁面
│   │   ├── css/
│   │   │   └── style.css                # UI 樣式
│   │   └── js/
│   │       ├── app.js                   # 前端邏輯（路由、API、頁面渲染）
│   │       ├── usage_report.js          # API Usage Report 頁面（統計 + 明細）
│   │       └── mcp_tools.js             # MCP Tools 頁面（唯讀工具目錄）
│   ├── database_definitions/             # 多資料庫連線定義
│   │   └── databases.yaml.example       # 資料庫定義範例
│   └── sql_definitions/                 # SQL 查詢定義 YAML 備援檔（匯入至 DB 用）
│       ├── employees.yaml               # 員工查詢範例
│       ├── departments.yaml             # 部門查詢範例
│       └── inventory_view.yaml          # 庫存 View 查詢範例
├── mcp_server/                          # MCP server（獨立行程，僅透過 REST 代理，不 import app/）
│   ├── catalog.py                       # 動態工具目錄（每個 (alias, 定義) 一個工具）
│   ├── naming.py                        # 工具名稱衍生規則（sanitize / 64 字元上限，與 Hub 共用）
│   ├── client.py                        # REST API HTTP client
│   └── config.py                        # MCP_* 環境變數組態
├── scripts/
│   ├── __init__.py
│   ├── init_db.py                       # 資料庫初始化腳本（建表 + 欄位/索引補齊 + 種子資料）
│   └── purge_audit_logs.py              # 稽核軌跡保留期限清理（分批 DELETE）
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
