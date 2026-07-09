# API Key 系統認證 + 稽核設計

日期：2026-07-09
狀態：設計待審

## 1. 背景與問題

目前 Oracle API Hub 的認證只有一種：使用者帳密走 `POST /api/v1/auth/token`（OAuth2 password flow）換取 JWT（HS256），後續以 `Authorization: Bearer <jwt>` 呼叫。授權為 RBAC：`get_current_user`（`app/services/auth_service.py`）解出 `User`，各路由再以 `PermissionChecker(resource, action)(user)` 檢查權限（`resource:action`，尾碼 `*` 萬用；superuser 略過）。

現在需要開放**其他應用系統（機器對機器，M2M）**呼叫 API。直接沿用帳密/JWT 對系統呼叫端不理想：需每 60 分鐘換 token、無法即時撤銷、無法分辨是哪個系統發起請求。現況也**沒有 principal 層級的稽核** —— log 只有 IP/method/path/status，查不到「哪個身分做了什麼」。

## 2. 目標

- 新增 **API Key** 認證，讓外部系統以一把長效、可即時撤銷的 key 呼叫 API。
- key 綁定到**服務帳號**（`User`），**完全沿用現有 RBAC**，授權邏輯零改動。
- 新增**稽核**：每請求記錄「哪個 principal / 哪把 key、來源 IP、對哪個資料庫的哪支動作、狀態、耗時」，同時寫**結構化 log 行**與 **PostgreSQL 稽核表**。
- per-key 控制：`expires_at`、IP 白名單、每 key 獨立限流。
- 管理介面：API 端點 + Admin UI，superuser 才能操作。
- **完全向後相容**：現有 human 使用者的 JWT 流程與行為不變。

### 非目標（YAGNI）

- 不做 OAuth2 Client Credentials / 外部 IdP（未來可另案，與本設計不衝突）。
- 不做 per-key scope 收斂（要不同權限就開不同服務帳號）。
- 不做稽核查詢 UI（v1 先靠 log / 直接 SQL 查稽核表）。
- 不做稽核表自動保留/清理程式（僅在本文件建議以排程或分割表處理）。
- 不改限流的 in-memory 實作為 Redis（維持現況，僅註明多實例限制）。

## 3. 關鍵決策彙整

| 項目 | 決策 |
|------|------|
| 傳輸 | `X-API-Key` 優先，fallback 到 `Authorization: Bearer`（JWT，或 `ahk_` 前綴的 key）|
| 授權 | 完全沿用服務帳號 RBAC，無 per-key scope |
| 稽核 | 結構化 log 行 **＋** PostgreSQL 稽核表，兩者都寫 |
| per-key 控制 | `expires_at`、IP 白名單、每 key 獨立限流 |
| 管理 | API 端點 + Admin UI，superuser only |
| 安全 | 禁止 superuser 服務帳號；服務帳號不可帳密登入 |
| 認證解析位置 | 早期 AuthContext middleware 統一解析，dependency 轉為讀 `request.state` |

## 4. 架構

### 4.1 Middleware 疊放順序（外 → 內）

```
AuditMiddleware  →  AuthContextMiddleware  →  RateLimiterMiddleware  →  routers
   (最外，計時)        (解析+驗證憑證)            (讀 principal 套限額)
```

`request.state` 就地共用：`AuthContextMiddleware` 在往內呼叫前寫好 `request.state.principal`，`RateLimiterMiddleware` 讀得到；`AuditMiddleware` 雖在最外層，但它在 `call_next` **之後**才讀 principal，那時已被填好。

> Starlette 中，`add_middleware` 後加入者為外層（先執行）。因此 `app.add_middleware` 的呼叫順序需為：先加 RateLimiter，再加 AuthContext，最後加 Audit（使 Audit 最外、RateLimiter 最內）。實作時以此順序為準並加測試/註解防止誤調。

### 4.2 認證解析：為何提前到 middleware

per-key 限流與稽核 principal 都需要「先知道是誰」，但 FastAPI 路由 dependency 執行在 middleware 之後。故將憑證解析統一提前到 `AuthContextMiddleware`，一次解析、三處共用（認證、限流、稽核），而 `get_current_user` 退化為讀 `request.state.principal`，各路由的 `PermissionChecker` 與 Swagger 安全宣告完全不動。

## 5. 資料模型

### 5.1 `api_hub_users` 新增欄位

| 欄位 | 型別 | 說明 |
|------|------|------|
| `is_service_account` | Boolean, default false, not null | 服務帳號旗標。為 true 時強制 `is_superuser=false`，且不可經 `/auth/token` 帳密登入 |

Migration：`init_db.py` 於 `create_all` 後執行冪等
`ALTER TABLE api_hub_users ADD COLUMN IF NOT EXISTS is_service_account boolean DEFAULT false NOT NULL`。

### 5.2 新表 `api_hub_api_keys`

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | int PK autoincrement | |
| `user_id` | int FK → api_hub_users.id, not null | 綁定的服務帳號 |
| `name` | varchar(100), not null | key 標籤，如 `hr-portal-prod`；同一 `user_id` 下唯一 |
| `key_prefix` | varchar(16), not null | 明文前段（如 `ahk_live_9f3c`），列表顯示用、非敏感 |
| `key_hash` | varchar(64), not null, unique index | 完整 key 的 SHA-256（hex）。每請求以此索引查詢 |
| `enabled` | Boolean, default true, not null | 即時撤銷開關 |
| `expires_at` | DateTime, nullable | null = 永久有效直到停用 |
| `ip_allowlist` | JSONB, nullable | CIDR 字串清單；null/空 = 不限 IP |
| `rate_limit_per_minute` | int, nullable | null = 退回全域限額 |
| `last_used_at` | DateTime, nullable | 每次成功驗證後更新 |
| `created_at` | DateTime, server_default now | |
| `created_by` | varchar(100), nullable | 發放此 key 的管理者 username |
| `revoked_at` | DateTime, nullable | 撤銷時間戳 |

**key 格式與雜湊**：明文 = `ahk_` + 至少 32 字元 url-safe 隨機字串（`secrets.token_urlsafe`）。明文**只在建立時回傳一次**；DB 只存 `key_hash`（SHA-256）與 `key_prefix`。API key 為高熵值隨機，SHA-256 已足夠且可索引，**不使用 bcrypt**（bcrypt 每請求驗證過慢且無法索引查詢）。

### 5.3 新表 `api_hub_audit_logs`

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | bigserial PK | |
| `ts` | DateTime, server_default now, index | 請求時間 |
| `principal_type` | varchar(20) | `user` / `api_key` / `anonymous` |
| `principal` | varchar(100), index | 帳號名；未認證為 `anonymous` |
| `api_key_id` | int, nullable | 哪一把 key |
| `api_key_name` | varchar(100), nullable | key 標籤（去正規化，方便查詢）|
| `client_ip` | varchar(64) | 來源 IP |
| `method` | varchar(10) | HTTP method |
| `path` | varchar(500) | 請求路徑 |
| `db_alias` | varchar(64), nullable | 目標資料庫 alias |
| `action` | varchar(200), nullable | 動作標的：query_name / table / procedure 名（能取到才記）|
| `status_code` | int | 回應狀態碼 |
| `duration_ms` | int | 處理耗時 |

保留策略：v1 不含清理程式。建議正式環境以排程刪舊資料或改用時間分割表（於運維文件補充）。

## 6. 各層改動

| 層 | 檔案 | 改動 |
|----|------|------|
| ORM | `app/models/user.py` | `User` 加 `is_service_account`；新增 `ApiKey` model |
| ORM | `app/models/audit_log.py`（新）| `AuditLog` model |
| Migration | `scripts/init_db.py` | 冪等 ALTER（users 加欄位）+ `create_all` 建兩張新表 |
| 安全工具 | `app/core/security.py` | `generate_api_key() -> (plaintext, prefix, hash)`；`hash_api_key(plaintext) -> hash` |
| 認證服務 | `app/services/auth_service.py` | 新增 `resolve_api_key(db, raw_key, client_ip) -> Principal`；`get_current_user` 改讀 `request.state.principal`；`authenticate_user` 拒絕服務帳號帳密登入 |
| Middleware | `app/middleware/auth_context.py`（新）| `AuthContextMiddleware`：解析憑證、驗證、寫 `request.state.principal`；明確錯誤（過期/停用/IP）短路回應 |
| Middleware | `app/middleware/audit.py`（新）| `AuditMiddleware`：計時、寫 log 行 + 稽核表（非致命）|
| Middleware | `app/middleware/rate_limiter.py` | 限流 key 改為優先用 `api_key:{id}` + 該 key 限額，否則 `ip:{client_ip}` + 全域 |
| 註冊 | `app/main.py` | 依 4.1 順序註冊三個 middleware；OpenAPI 加 `APIKeyHeader` 安全方案 |
| Schema | `app/schemas/api_key.py`（新）| `ApiKeyCreate` / `ApiKeyOut` / `ApiKeyCreated`（含一次性明文）|
| API | `app/api/v1/auth.py` | 新增 `/auth/api-keys` POST/GET/DELETE（superuser only）；`/auth/users` 建立/更新強制服務帳號規則 |
| Service | `app/services/api_key_service.py`（新）| 建立/列表/撤銷 key 的商業邏輯 |
| Admin UI | `app/admin/index.html` + `app/admin/js/app.js` | 新增「API Keys」頁（列表 + 新增表單，建立後一次性顯示明文）；Users 頁加「服務帳號」勾選（鎖 superuser）|

## 7. 認證流程（AuthContextMiddleware）

```
1. 讀 X-API-Key header；若無 → 讀 Authorization: Bearer <token>
2. 判定憑證型別：
   - 來自 X-API-Key，或 token 以 "ahk_" 開頭  → API key 分支
   - 其餘                                      → JWT 分支
3. API key 分支：
   h = sha256(key)
   row = SELECT * FROM api_hub_api_keys WHERE key_hash = h
   驗證（任一不過即明確錯誤，短路回應，不進路由）：
     - row 不存在                       → 401 Could not validate credentials
     - not row.enabled                  → 401 API key is disabled
     - row.expires_at 且已過期          → 401 API key has expired
     - ip_allowlist 非空且 client_ip 不命中 → 403 Source IP not allowed
     - 服務帳號 not is_active           → 401 Could not validate credentials
   通過 → request.state.principal = Principal(
             type="api_key", user=<service account>,
             key_id=row.id, key_name=row.name,
             rate_limit=row.rate_limit_per_minute)
          背景更新 row.last_used_at
4. JWT 分支：沿用 jose 解碼 → 查 user →
   request.state.principal = Principal(type="user", user=user, rate_limit=None)
   （解碼失敗不在此 raise，principal 留 None）
5. 無憑證 / JWT 無效 → request.state.principal = None（交由 dependency 決定 401）
```

`get_current_user`（改寫）：
```python
def get_current_user(request: Request) -> User:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise credentials_exception  # 401
    return principal.user
```

Swagger：新增 `APIKeyHeader(name="X-API-Key", auto_error=False)` 安全方案，Authorize 面板可填 key；既有 OAuth2 Bearer 保留。

## 8. 管理端點（superuser only，掛於 `auth.router`）

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/auth/api-keys` | body: `service_account_username`、`name`、`expires_at?`、`ip_allowlist?`、`rate_limit_per_minute?`。回 `201` + **明文 key（僅此一次）** + key metadata |
| GET | `/auth/api-keys` | 列表：id / name / 服務帳號 / key_prefix / enabled / expires_at / last_used_at / revoked_at（**不含明文/雜湊**）|
| DELETE | `/auth/api-keys/{id}` | 撤銷：`enabled=false` + `revoked_at=now`（軟撤銷，保留稽核可追溯）|

建立時驗證：目標帳號必須存在且 `is_service_account=true`；否則 400。

服務帳號規則（`/auth/users` 建立/更新）：`is_service_account=true` 時強制 `is_superuser=false`（同時送兩者為 true → 400）；`authenticate_user` 對服務帳號一律回 None（不可帳密登入）。

## 9. 稽核（AuditMiddleware）

- 範圍：所有 `/api/v1/*` 請求，**跳過** health、`/auth/token`、Admin 靜態檔。
- 流程：記 start → `call_next` → 取 `request.state.principal`、`response.status_code`、`db_alias`/`action`（由 path 解析；query 執行類由路由把 `query_name` 回填至 `request.state`）→ 寫**結構化 log 行** + **INSERT 稽核表一筆**。
- **非致命**：整段以 try/except 包住，稽核寫入失敗只記 error log，**絕不影響請求回應**。
- principal 為 None（未認證嘗試）仍寫一筆，`principal_type="anonymous"`，便於稽核異常存取。

log 行範例：
```
2026-07-09 10:22:01 [AUDIT] principal=svc_hr_portal type=api_key key=hr-portal-prod
  ip=10.1.2.3 POST /apihub/api/v1/hr/queries/execute db=hr action=HR_QueryEmployeeAll
  status=200 dur=42ms
```

## 10. 限流（RateLimiterMiddleware 擴充）

- 限流 key：`request.state.principal` 為 api_key → `api_key:{id}`，限額用該 key 的 `rate_limit_per_minute`（null 退回全域 `settings.rate_limit_per_minute`）；否則 `ip:{client_ip}` + 全域限額。
- 維持現有 in-memory sliding window。多實例部署需改 Redis-backed（現況既有限制，本次不處理，僅註明）。

## 11. 錯誤處理

| 情況 | 回應 |
|------|------|
| 無憑證 / JWT 無效 / key 查無 | 401 `Could not validate credentials` |
| key 已停用 | 401 `API key is disabled` |
| key 已過期 | 401 `API key has expired` |
| client_ip 不在 IP 白名單 | 403 `Source IP not allowed` |
| 服務帳號 `is_active=false` | 401 `Could not validate credentials` |
| 權限不足（RBAC） | 403（現有 `PermissionChecker` 訊息）|
| 超過限額 | 429 `Rate limit exceeded` |
| 建服務帳號同時設 superuser | 400 |
| 對非服務帳號發 key | 400 |

## 12. 測試（pytest，多為純單元、不需真 DB 連線）

- **security**：`generate_api_key` 產出格式、SHA-256 雜湊穩定、prefix 擷取。
- **key 驗證**：enabled / expired / ip_allowlist 命中與否 / 服務帳號 inactive 各分支。
- **get_current_user**：X-API-Key 命中、Bearer JWT、Bearer `ahk_` key、無憑證 401。
- **服務帳號限制**：建立時 superuser 被擋（400）、服務帳號帳密登入被拒。
- **限流**：per-key 限額 vs IP 全域；超額 429。
- **稽核**：principal / db_alias / action 正確組出；模擬 DB 例外時稽核失敗不影響請求（回應仍正常）。
- **管理端點**：非 superuser 被擋（403）、建立回明文一次、撤銷後該 key 失效（401）。

## 13. 向後相容與部署

- 純新增：不動現有 JWT 流程；既有 human 使用者行為不變。
- `init_db.py` 冪等 ALTER（users 加 `is_service_account`）+ `create_all`（兩張新表）；既有部署重跑即可升級。
- 新 middleware 對未帶新憑證的請求無行為改變（principal 由 JWT 分支或 None 決定）。

### 前置硬化（與本功能相依的既有風險）

- **正式環境務必替換 `app/core/config.py` 的預設 `secret_key`**（目前為 `"replace-with-a-secure-random-string"`）。HS256 為對稱金鑰，外洩即可偽造任意身分 token；M2M 開放後此為最高風險點。
- 建議服務帳號一律最小權限授權；key `expires_at` 與 IP 白名單依對接系統性質設定。
