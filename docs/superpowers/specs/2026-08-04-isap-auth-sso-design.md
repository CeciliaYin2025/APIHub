# isap-auth 自註冊與 Admin UI SSO — 設計

日期：2026-08-04

## 目標

補上 Oracle API Hub 與 isap-auth 之間缺少的兩塊整合：

1. **文件** — MCP 走 OAuth 需要 isap-auth 開啟 `OAUTH_DCR_ENABLED`，這個前置條件目前完全沒有寫在部署文件裡，實際上線會直接卡住。
2. **自註冊 + Admin UI SSO** — API Hub 目前沒有出現在 isap-auth 的 `registered_app` 允許清單裡（三條註冊路徑一條都沒走），Admin UI 也只有本地帳密登入。

## 現況（實作前）

| 項目 | 狀態 |
|---|---|
| MCP OAuth 2.1 Resource Server | 已實作（`mcp_server/oauth.py`），32 個測試通過 |
| MCP 身分橋接 `POST /api/v1/auth/sso-token` | 已實作（`app/api/v1/auth.py:330`） |
| API Hub 在 isap-auth 的 `registered_app` 記錄 | **不存在** —— 不在 `_seed_data()`，也沒有任何自註冊程式碼 |
| Admin UI SSO 登入 | **不存在** —— `app/admin/` 無相關程式碼，只有本地帳密 |
| `OAUTH_DCR_ENABLED` 前置條件文件 | **不存在** |

MCP 的 Resource Server 角色本身不需要 `registered_app` 記錄（它只用全域共享的 introspection secret），所以現有 MCP 功能不受影響。缺的是 **MCP client 端**需要 DCR 才能取得 client_id，以及 Admin UI SSO 需要的 app_id。

## 已確認的決策

| 決策 | 選擇 |
|---|---|
| 自註冊用途 | 註冊 `apihub` app_id **並**實作 Admin UI SSO 登入 |
| DCR 文件深度 | 前置條件 + isap-auth A.6 建議的 gateway 限制作法 |
| 註冊機制 | **Compose one-shot job**（貼合 `CONTRACT.md:108-147` 平台契約） |
| isap-auth 整合方式 | **方法 1（傳統 app-code）** —— `/authorize` → `POST /token` → user info → API Hub 自己 mint JWT |
| 本地帳密登入 | **並存，SSO 為主** —— 登入頁上方 SSO 按鈕，下方保留帳密表單 |
| SSO 使用者佈建 | 抽成共用的 `SSO_AUTOCREATE`（`MCP_BRIDGE_AUTOCREATE` 保留為相容別名） |
| JWT 交回瀏覽器 | **URL fragment**（`#sso_token=`），SPA 讀取後立即 `history.replaceState` 清除 |

### 為何選方法 1

isap-auth README A.2 建議新整合優先用方法 2/3，但方法 1 在此情境更合適：它回傳的是 user info 而非 token，而 API Hub **本來就自己 mint JWT**（`create_access_token`），和現有的 `sso_token_bridge` 邏輯完全一致，可以共用同一份使用者解析程式碼。方法 2/3 會多出 PKCE 狀態管理、JWKS 快取與簽章驗證，換來的標準性在這個內部第一方應用上沒有對應收益。

### 為何選 fragment 而非一次性交換碼

現有 SPA 是 `localStorage` + `Authorization: Bearer`（`app/admin/js/app.js:9,143`），token 必須進得了 JavaScript。fragment 不會送到伺服器、不進 nginx access log、不出現在 Referer。一次性交換碼安全性更好，但需要伺服器端儲存 —— Redis 目前**不是** API 進程的依賴（`redis_url` 在 `config.py:49` 宣告卻無任何 `app/` 程式碼使用），為登入引進 Redis 耦合不划算；改用平台 store 則要多一張表與過期清理。

已知代價：JWT 會留在瀏覽器歷史紀錄中，在 token 有效期（預設 60 分鐘）內有意義。

## 非目標

- 不實作 OIDC / PKCE / introspection 路徑（Admin UI 走方法 1）
- 不改 `AuthContextMiddleware` 接受 cookie 認證
- 不移除本地帳密登入
- 不把登入事件納入稽核報表（現有 `/auth/token` 也沒有，維持一致）
- 不部署 isap-auth 本身（既存平台服務）

---

## W1 — DCR 前置條件文件

`deploy/apihub/README.md`、`deploy/apihub-test/README.md` 各新增一節，置於 MCP 章節的部署步驟之前。

內容要點：

1. **前置條件**：MCP client（Claude Code 等）需向 isap-auth 動態註冊取得 client_id，因此 isap-auth 必須開啟 `OAUTH_DCR_ENABLED=True`；未開啟時 client 註冊會得到 `403 registration_disabled`，`README.md` 現有驗證清單第 4 步的瀏覽器 SSO 流程會失敗。
2. **這是平台層決策**：`OAUTH_DCR_ENABLED` 預設 `False` 是刻意的安全設計（`/register` 不需任何憑證），且 isap-auth 的 `ci/policy-check.sh` 會 grep 確保預設值不被改動。開啟前必須與 isap-auth 維運者確認，因為它影響該 AS 上的所有應用。
3. **建議的加固**：依 isap-auth README A.6，開啟 DCR 時應一併在 gateway 限制 `/register` 的來源（例如僅允許內網或特定網段）。
4. **徵狀對應**：`403 registration_disabled` → DCR 未開啟。

不動任何程式碼。

---

## W2 — 自註冊 one-shot

### 新增：`scripts/register_isap_app.py`

對 isap-auth 發 `PUT {ISAP_AUTH_INTERNAL_URL}/api/v1/apps/{ISAP_APP_ID}`：

- Header：`Authorization: Bearer {ISAP_REGISTRATION_TOKEN}`
- Body：`{"app_name": ISAP_APP_NAME, "redirect_uris": [...]}`（由 `ISAP_REDIRECT_URIS` 換行分隔解析）

**未設定時視為成功跳過**：`ISAP_REGISTRATION_TOKEN` 為空時印出 `SSO registration skipped (no token configured)` 並 exit 0。沒有這條，所有不使用 SSO 的部署都會因為 one-shot 失敗而起不來。

| 情況 | 退出碼 | 訊息 |
|---|---|---|
| 無 token | 0 | 已跳過 |
| 有 token 但缺 app_id / internal URL / redirect_uris | 2 | 指出缺哪一項 |
| isap-auth 連不上 | 1 | 提示 isap-auth 需先啟動且在 gateway-net 上 |
| 401 | 1 | token 無效或已撤銷 → `docker exec isap-auth python -m app.cli rotate-token` |
| 403 | 1 | token 綁定的 app_id 與設定不符，兩個值都印出 |
| 400 | 1 | 原樣印出 isap-auth 的 `redirect_uris` 驗證訊息 |
| 200 | 0 | 印出 app_id / redirect_uris / is_active |

**冪等且宣告式**：`PUT /apps/{app_id}` 會整批取代 `redirect_uris`（isap-auth `app/controllers/registered_app.py::replace_registration`），因此 `.env.isap` 是唯一真實來源，重複執行結果一致。

**redirect_uri 限制**：isap-auth 正式環境只接受 `https://`，`http://` 僅限 loopback（`registered_app.py:27-33`）。此限制寫入 `.env.isap.example` 註解。

### 憑證存放：獨立的 `.env.isap`

bootstrap token 能改寫 `apihub` 的 `redirect_uris`。一旦外洩，攻擊者可註冊自己的 callback 位址竊取 authorization code —— 這是實質的 open-redirect 風險。而 `.env` 由 `apihub-api` 與所有 celery worker 共用，token 放進去等於所有長駐容器都持有它。

| 檔案 | 變數 | 誰讀 |
|---|---|---|
| `.env` | `ISAP_APP_ID`、`ISAP_AUTH_PUBLIC_URL`、`ISAP_AUTH_INTERNAL_URL`、`ADMIN_SSO_ENABLED`、`SSO_AUTOCREATE` | api |
| `.env.isap` | `ISAP_REGISTRATION_TOKEN`、`ISAP_REDIRECT_URIS`、`ISAP_APP_NAME` | 只有註冊 job |

作法與既有的 `.env.mcp` 慣例一致。

### Compose 接線

```yaml
apihub-app-register:
  image: oracle-api-hub:latest
  container_name: apihub-app-register
  restart: "no"
  env_file: [.env, .env.isap]
  networks: [gateway-net]          # isap-auth 也在 gateway-net
  command: ["python", "-m", "scripts.register_isap_app"]
  depends_on:
    apihub-migrate: { condition: service_completed_successfully }
```

`apihub-api` 加上 `depends_on: apihub-app-register: { condition: service_completed_successfully }`。

正式站 app_id 為 `apihub`，測試站為 `apihub-test`（`deploy/apihub-test/` 同構）。

---

## W3 — Admin UI SSO 登入

### 新增設定（`app/core/config.py`）

```
ADMIN_SSO_ENABLED=false      # 關閉時登入頁不顯示 SSO 按鈕，兩個端點回 404
ISAP_AUTH_PUBLIC_URL=        # 瀏覽器導向用，如 https://isap.sercomm.com/auth
ISAP_AUTH_INTERNAL_URL=      # 後端換 token 用，如 http://isap-auth:9997/auth
ISAP_APP_ID=apihub
SSO_AUTOCREATE=false         # MCP_BRIDGE_AUTOCREATE 為相容別名
```

公開與內部 URL 刻意分開：瀏覽器必須走公開的 `/authorize`，後端的 `POST /token` 走 docker 內網，不繞 gateway。

註冊專用的 `ISAP_APP_NAME`、`ISAP_REGISTRATION_TOKEN`、`ISAP_REDIRECT_URIS` 同樣宣告在 `config.py`（依專案慣例，設定一律經 Pydantic Settings，不直接讀 `os.environ`），但只有 `scripts/register_isap_app.py` 會讀取；api 容器的 `.env` 不提供這三個值，它們維持預設空字串。

### 新增：`app/services/sso_service.py`

把目前寫死在 `app/api/v1/auth.py::sso_token_bridge`（`auth.py:347-364`）的邏輯抽出：

```
resolve_sso_user(db, username, email) -> User
```

- 查既有 `User`；不存在時依 `SSO_AUTOCREATE` 決定建立或拋 403
- 自動建立的使用者：`is_active=True`、`is_superuser=False`、`is_service_account=False`，密碼為隨機不可猜雜湊（SSO 使用者無可用本地密碼，且 `verify("")` 會拋例外）
- 服務帳號或已停用帳號一律拒絕

`sso_token_bridge` 與新的 callback 共用此函式，確保 MCP 與 Admin UI 的佈建政策不會分岐。

拒絕情形以 `HTTPException(403)` 拋出，維持 `sso_token_bridge` 現有的行為與訊息；Admin UI 的 callback 則攔截它，改導向對應的 `#sso_error=`（見下方錯誤回報表），因為瀏覽器端不該收到 JSON 錯誤。

### 新增端點（`app/api/v1/auth.py`）

**`GET /auth/sso/login`**

1. `ADMIN_SSO_ENABLED=false` → 404
2. 產生隨機 `state`，寫入 `httpOnly` + `SameSite=Lax` 短效 cookie
3. 302 到 `{ISAP_AUTH_PUBLIC_URL}/api/v1/authorize?app_id={ISAP_APP_ID}&redirect_uri={callback}&state={state}`

`redirect_uri` 一律由後端從設定組出（`ROOT_PATH` + `/api/v1/auth/sso/callback`），絕不接受請求參數傳入。

**`GET /auth/sso/callback`**

1. `ADMIN_SSO_ENABLED=false` → 404
2. 比對 `state` 與 cookie，隨即清除 cookie
3. `POST {ISAP_AUTH_INTERNAL_URL}/api/v1/token`，body `{"code": code, "app_id": ISAP_APP_ID}` → user info
4. `resolve_sso_user(...)` → 本地 `User`
5. `create_access_token({"sub": user.username})`
6. 302 到 `{ROOT_PATH}/admin#sso_token=<jwt>`

### state 防護是必要的

isap-auth 的 `/authorize` 支援 `state` 並在導回時原樣帶回（`app/api/v1/auth/auth.py:124,168-172`），legacy `app_id` 模式亦然。

沒有這道防護存在實際攻擊：攻擊者先自行走完流程取得一個 code，再誘導受害者點擊 `…/auth/sso/callback?code=<攻擊者的code>`，受害者的瀏覽器會**靜默登入成攻擊者的帳號**，之後在 Admin UI 中建立的定義與查詢紀錄都落在攻擊者可存取的帳號下。

### 錯誤回報

瀏覽器導向的端點一律用 fragment 回報，不丟 JSON 或堆疊：

| 狀況 | 導回 |
|---|---|
| 缺 `code` | `#sso_error=missing_code` |
| state 不符或 cookie 遺失 | `#sso_error=state_mismatch` |
| isap-auth 連不上 | `#sso_error=auth_unreachable` |
| code 無效或過期 | `#sso_error=invalid_code` |
| 使用者未佈建（`SSO_AUTOCREATE=false`） | `#sso_error=not_provisioned` |
| 服務帳號或已停用 | `#sso_error=not_eligible` |

### Admin UI 改動

`app/admin/index.html`：登入卡片上方加「使用 ISAP SSO 登入」按鈕（`ADMIN_SSO_ENABLED` 為 false 時不渲染），下方保留現有帳密表單。

`app/admin/js/app.js` 開機流程：

1. 讀 `location.hash`
2. `sso_token=` → 存入 `localStorage.hub_token`、`history.replaceState` 清除 fragment、進入主畫面
3. `sso_error=` → 停在登入頁顯示對應可讀訊息、同樣清除 fragment
4. 皆無 → 維持現有行為（有 token 就驗證，否則顯示登入頁）

SSO 開關值透過現有的 `{{BASE_PATH}}` 注入機制（`app/main.py:87`）一併注入。

---

## 測試

沿用既有兩種模式：DB 相關用 `_FakeDB` 直接呼叫 router 函式（`tests/test_api_key_endpoints.py`），外部 HTTP 用 `httpx.MockTransport`（`tests/mcp/test_oauth.py`）。`pytest` 無 live Postgres，不引入打真實 DB 的 TestClient。

**`tests/test_register_isap_app.py`**
無 token → exit 0 且未發出任何 HTTP 請求；缺設定 → exit 2；200 → 驗證 PUT 方法、路徑含 app_id、Authorization header、body 的 `redirect_uris` 為解析後 list；401/403/400 各自的退出碼與訊息；連線失敗 → exit 1。

**`tests/test_sso_service.py`**
既有使用者直接回傳不新增；不存在 + autocreate 開 → 建立且欄位正確、密碼為隨機雜湊；不存在 + autocreate 關 → 403；服務帳號 / 已停用 → 403；`MCP_BRIDGE_AUTOCREATE` 相容別名仍生效。

**`tests/test_sso_endpoints.py`**
`ADMIN_SSO_ENABLED=false` 時兩端點 404；login → 302 且 URL 含正確 app_id / redirect_uri / state，cookie 為 httpOnly + SameSite=Lax；callback 的 state 不符、cookie 遺失、缺 code 各自的 `#sso_error=`；成功路徑（MockTransport 假造 isap-auth `/token` 回應）→ 302 到 `#sso_token=` 且 JWT 的 `sub` 正確；連線失敗 → `#sso_error=auth_unreachable`。

**`tests/test_sso_token_bridge.py`**
重構安全網：`sso_token_bridge` 改呼叫 `sso_service` 後，bridge secret 驗證、未設定 secret 的 404、secret 錯誤的 403 行為必須不變。

**`tests/e2e/sso-login.spec.js`**（不需真實 isap-auth）
`ADMIN_SSO_ENABLED` 開關控制 SSO 按鈕顯示；導向 `/admin#sso_token=<測試簽出的有效 JWT>` → 進入 UI、hash 清空、token 進 localStorage；導向 `/admin#sso_error=not_provisioned` → 登入頁顯示訊息且 hash 清空。

**無法在 CI 驗證的部分**
完整 SSO 往返需要真實 isap-auth。比照現有 MCP 的作法，在 `deploy/*/README.md` 增列部署後驗證步驟：註冊 job 成功、SSO 按鈕導向 isap-auth、登入後回到 Admin UI 且身分正確、未佈建使用者得到 `not_provisioned`。

## 文件更新

- `README.md`、`docs/system_specification.md` —— 新增 SSO 登入與自註冊說明（`update-spec` skill）
- `deploy/apihub/README.md`、`deploy/apihub-test/README.md` —— W1 的 DCR 前置條件、`.env.isap` 說明、部署後驗證步驟
- `CLAUDE.md` 與 `AGENTS.md` —— 在 Request Pipeline 一節補上 SSO 登入路徑；兩檔需同步
- `app/admin/CLAUDE.md` —— 登入頁的 SSO 分支與 fragment 處理慣例
