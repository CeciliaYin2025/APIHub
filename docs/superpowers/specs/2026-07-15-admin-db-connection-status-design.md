# Admin Dashboard 資料庫連線狀態檢查設計

日期：2026-07-15
狀態：設計待審

## 1. 背景與問題

Admin Dashboard 目前已有「Databases」卡片（`app/admin/js/app.js:308-320`），以 `GET /api/v1/databases` 取得資料，顯示 alias / type / description / connection / user 五欄。

問題在於這些欄位**全部來自設定檔**（`DatabaseRegistry.list_databases()`，`app/db/multi_pool.py:247`），並不代表資料庫真的連得上。該方法回傳的 `pool_active` 只表示「pool 物件已被建立」（`alias in self._pools`），**不等於連線健康**。

同時 `init_all_pools()`（`app/db/multi_pool.py:162`）刻意容忍死掉的資料來源 —— 一個連不上的後端不應阻擋整個 App 啟動，失敗的 alias 會在首次使用時由 `get_connection` 重試 `init_pool`。這代表 Dashboard 上一個 alias 看起來「已設定」，實際上可能從啟動起就從未連通過，管理者無從得知。

現況要判斷某個資料庫是否還活著，只能去翻 container log。

## 2. 目標

- Dashboard 的 Databases 卡片**每一列顯示真實的連線狀態**：Online / Offline / Timeout。
- 顯示**回應時間（latency_ms）**，讓管理者看得出「連得上但很慢」。
- Offline 時顯示**driver 原始錯誤訊息**（如 `ORA-12541: TNS:no listener`）以利診斷。
- 進入 Dashboard 時**自動檢查**，並提供**手動 Refresh** 重新檢查。
- 單一資料庫連不上或卡住時，**不得拖慢或阻擋整個 Dashboard**。

### 非目標（YAGNI）

- 不做伺服器端背景輪詢與快取（每次檢查即時發問，維持簡單）。
- 不做狀態歷史紀錄 / 趨勢圖 / 告警通知。
- 不改三個 adapter 的 driver 層 connect timeout（見 §6 取捨）。
- 不做 per-alias 的獨立檢查按鈕（自動檢查已涵蓋）。

## 3. 關鍵決策彙整

| 項目 | 決策 |
|------|------|
| 檢查時機 | 進入 Dashboard 自動檢查 + Refresh 按鈕手動重檢 |
| 檢查方式 | 真實取連線並執行 dialect 提供的健康檢查 SQL |
| 錯誤揭露 | 狀態 + latency + driver 原始錯誤訊息 |
| 權限 | superuser only（`get_current_active_superuser`）|
| 平行化 | `ThreadPoolExecutor` 併發檢查所有 alias，共用單一整體 deadline |
| 引擎差異 | 放在 Dialect 層，service 不得 branch `db_type` |

## 4. 設計

### 4.1 Dialect 層 —— 健康檢查 SQL

各引擎的「最小連線驗證查詢」不同：Oracle 必須有 `FROM DUAL`，PostgreSQL / MSSQL 則否。依 CLAUDE.md 規範「When adding engine-specific behavior, extend the `Dialect` base class — never branch on `db_type` inside services」，此差異放在 dialect。

於 `app/db/dialect.py` 的 `Dialect` 新增抽象方法：

```python
@abc.abstractmethod
def health_check_sql(self) -> str:
    """Return a trivial query that proves the connection is alive."""
```

實作：

| Dialect | 回傳 |
|---------|------|
| `OracleDialect` | `SELECT 1 FROM DUAL` |
| `PostgresqlDialect` | `SELECT 1` |
| `MssqlDialect` | `SELECT 1` |

因為是 `@abc.abstractmethod`，未來新增引擎若漏實作會在實體化時就失敗，而非執行期才出錯。

### 4.2 Service —— `app/services/db_health_service.py`（新檔）

**`check_database(alias: str) -> dict`**

1. 以 `db_registry.get_db_type(alias)` 取得引擎型別，`get_dialect(db_type)` 取得 dialect。
2. 計時開始（`time.perf_counter()`）。
3. `with db_registry.get_cursor(alias) as cursor:` → `cursor.execute(dialect.health_check_sql())` → `cursor.fetchone()`。
4. 計時結束，回傳 `status="online"` 與 `latency_ms`。
5. 任何 `Exception` → `status="offline"`，`error=str(exc)`，並記錄 `logger.warning`（含 alias）。

回傳結構：

```python
{
    "alias": str,
    "status": "online" | "offline" | "timeout",
    "latency_ms": float | None,   # offline/timeout 時為 None
    "error": str | None,          # online 時為 None
    "checked_at": str,            # ISO-8601 UTC
}
```

此設計自然涵蓋「pool 啟動時就失敗」的情境：該 alias 的 `get_connection` 會重試 `init_pool`，失敗即拋例外，錯誤字串直接成為 `error`。

**`check_all_databases() -> list[dict]`**

三個 driver（`oracledb` / `psycopg2` / `pymssql`）都是**阻塞式同步 API**，因此併發只能靠 thread：

- 以 `ThreadPoolExecutor` 對 `db_registry.aliases()` 逐一 submit `check_database`。
- executor 大小為 `min(len(aliases), 8)` —— **有界**，避免對卡住的資料庫重複 Refresh 時無限增生 thread。
- 採**整體 deadline**：submit 完所有 future 後記錄 `deadline = perf_counter() + settings.db_health_check_timeout`，再逐一以 `future.result(timeout=max(0, deadline - perf_counter()))` 取值。所有 alias **共用同一份 N 秒預算** —— 10 個死掉的資料庫最多仍只等 N 秒，而非 10×N 秒。
- 逾時（`concurrent.futures.TimeoutError`）→ 該 alias 回 `status="timeout"`、`error="Health check exceeded {N}s"`。
- 以 `shutdown(wait=False)` 收尾，不等待被放棄的 thread（見 §6）。

**設定**：`app/core/config.py` 新增 `db_health_check_timeout: float = 5.0`（env `DB_HEALTH_CHECK_TIMEOUT`）。

### 4.3 API —— `GET /api/v1/databases/status`

位於 `app/api/v1/databases.py`（既有 router，prefix `/databases`）：

```python
@router.get("/status")
def database_status(current_user: User = Depends(get_current_active_superuser)):
    """Check live connectivity for every configured database."""
    return check_all_databases()
```

- **權限**：`get_current_active_superuser`（`app/services/auth_service.py:227`）。此端點會實際開連線並回傳 driver 原始錯誤，比僅列設定的 `GET /databases`（只需 `get_current_user`）敏感，故收斂至 superuser。
- **路由衝突**：此 router 目前只有 `GET ""`，無 `/{alias}` 路徑參數，故 `/status` 不會被吃掉。
- 端點宣告為 `def`（非 `async def`），由 FastAPI 自動丟到 threadpool 執行，不阻塞 event loop。

### 4.4 Admin UI —— `renderDashboard()`

修改 `app/admin/js/app.js` 的 Databases 卡片（現 `app.js:308-320`）：

- 表頭新增 **Status** 欄；card-header 右側新增 **Refresh** 按鈕。
- **兩段式渲染**：Dashboard 主體照現有流程立即渲染，Status 欄先填 `Checking…`；另外**獨立**發 `GET /databases/status`，回來後才填入各列。慢的資料庫因此**不會延遲頁面其他部分**。
- 狀態呈現：
  - `online` → `<span class="badge badge-green">Online</span>` + `42ms`
  - `offline` → `<span class="badge badge-red">Offline</span>`，錯誤訊息截斷後 inline 顯示，完整字串放 `title` tooltip
  - `timeout` → `<span class="badge badge-amber">Timeout</span>`
- **非 superuser**：`GET /databases/status` 回 403，前端 catch 後該欄顯示 `—`，不跳錯誤（Dashboard 對一般使用者仍完全可用）。
- 錯誤訊息一律經既有 `esc()` 逸出後才插入 DOM。

## 5. 資料流

```
renderDashboard()
  ├─ 既有 Promise.all（users/roles/perms/defs）→ 立即渲染，Status 欄 = "Checking…"
  └─ GET /api/v1/databases/status          （獨立、不阻塞上面）
       └─ get_current_active_superuser
            └─ check_all_databases()
                 └─ ThreadPoolExecutor（有界，併發）
                      └─ check_database(alias)  × N
                           ├─ get_dialect(db_type).health_check_sql()
                           ├─ db_registry.get_cursor(alias)   ← 死 pool 在此重試 init_pool
                           └─ execute + fetchone + 計時
       └─ 回填每一列的 Status 欄
```

## 6. 取捨與已知限制

**逾時無法真正中斷阻塞中的 driver 呼叫。** Python 無法安全地砍掉執行中的 thread。逾時後我們放棄該 worker thread 並回報 `timeout`，但該 thread 會一直存活到 driver 自身的網路逾時觸發才結束。

因此本設計的 timeout **限制的是「Dashboard 等待的時間」，而非「thread 存活的時間」**。executor 有界（最多 8）**只限制單次檢查內的併發數**，不是累積上限：`ThreadPoolExecutor` 是在 `check_all_databases()` 內**每次呼叫都重新建立**，因此重複 Refresh 打向同一個卡死的資料庫時，每次都會放棄（`shutdown(wait=False)`）最多 8 個新 thread，這些 thread 會一路存活到 driver 自身的網路逾時才結束——10 次 Refresh 就會累積 10 組被放棄的 thread。這是已知且刻意接受的取捨：實務上受限於本端點僅開放 superuser 呼叫（見 §4.3 權限收斂），且被放棄的 thread 終究會在 driver timeout 後自然結束，不會無限存活。

另外一個目前未記錄的後果：`ThreadPoolExecutor` 的 worker 是 non-daemon thread，並由 Python 的 `atexit` hook 負責 join，因此一次卡在 driver 呼叫上的健康檢查會**延後 interpreter/process 的結束**，直到該 driver 逾時為止——實務上會表現為 container 重啟變慢。

真正的根治方式是在三個 adapter 各自設定 driver 層的 connect timeout，那是跨 `app/db/adapters/` 三個檔案的較大變更，本案不納入（已與需求方確認接受此取捨）。

## 7. 測試計畫

| 測試 | 內容 |
|------|------|
| dialect | 三個 dialect 的 `health_check_sql()` 各自回傳正確 SQL；Oracle 含 `FROM DUAL` |
| service - online | mock `get_cursor` 成功 → `status="online"`、`latency_ms` 為數值、`error is None` |
| service - offline | mock `get_cursor` 拋例外 → `status="offline"`、`error` 含例外字串、`latency_ms is None` |
| service - timeout | mock 檢查阻塞超過 timeout → `status="timeout"` |
| service - all | 多 alias 且其中一個失敗 → 其餘仍回 `online`（失敗不互相污染）|
| API - 權限 | 非 superuser 呼叫 `/databases/status` → 403 |
| API - 成功 | superuser 呼叫 → 200 且每個已設定 alias 都有一筆 |

依 CLAUDE.md，測試以 `pytest -v` 執行，lint 以 `ruff check app/ tests/`。

## 8. 異動檔案

| 檔案 | 異動 |
|------|------|
| `app/db/dialect.py` | 新增 `health_check_sql()` 抽象方法 |
| `app/db/dialects/oracle_dialect.py` | 實作 → `SELECT 1 FROM DUAL` |
| `app/db/dialects/postgresql_dialect.py` | 實作 → `SELECT 1` |
| `app/db/dialects/mssql_dialect.py` | 實作 → `SELECT 1` |
| `app/services/db_health_service.py` | **新檔** —— `check_database` / `check_all_databases` |
| `app/core/config.py` | 新增 `db_health_check_timeout: float = 5.0` |
| `app/api/v1/databases.py` | 新增 `GET /status`（superuser only）|
| `app/admin/js/app.js` | Databases 卡片新增 Status 欄 + Refresh 按鈕、兩段式渲染 |
| `tests/test_db_health.py` | **新檔** —— 見 §7 |
