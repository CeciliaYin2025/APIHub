# E&O 分析：Dify Chatflow + API Hub Web API 建置指南

`docs/eo-mcp-setup.md` 的 Dify 版。那份文件讓每個 AI client 經 **MCP** 連 API Hub；
這份把整個流程改成 **Dify 建立的 agent flow**，資料一律走 **API Hub 的 web API**
（REST），不經 MCP。

裝完之後，一般使用者在 Open WebUI 選 `dify/E&O Analyst`，直接問
「SP 站上一期的紅燈料號比前一期多了哪些？」即可，不需要自己裝 MCP 或 skill。

判讀規則（期別是週不是月、代碼不能猜、看金額占比而非列數占比、Obsolete 與 E&O
是同一欄位）與 `eo-mcp-setup.md` 第 4 節完全相同，本文不重複；那些規則已經整份
寫進 Agent 的指令裡。

---

## 架構

```
使用者 ──ISAP SSO──► Open WebUI ──dify pipe──► Dify Chatflow「E&O Analyst」
                                                  │
                                    Start → Agent（Function Calling）→ Answer
                                                  │  13 個 API 工具（apihub_eo）
                                                  ▼  X-API-Key（服務帳號 svc_dify_eo）
                             https://ia-dev.sercomm.com/apihub/api/v1/DW/tools/<definition>
                                                  │
                                         queries.execute（權限、MC/Sales view、稽核）
```

| 元件 | 來源 |
|---|---|
| 工具定義 | `GET /api/v1/DW/tools/openapi.json?group=eo`，由 SQL 定義即時產生 |
| 工具呼叫 | `POST /api/v1/DW/tools/{definition}`，扁平 JSON body |
| Chatflow | `docs/dify/eo-analyst.yml`，由 `scripts/build_dify_app.py` 產生 |
| Agent 策略 | Dify 外掛 `langgenius/agent`（Function Calling） |
| 知識 | 五份 E&O skill + rule zero，與 Open WebUI 版共用同一段組裝程式 |

### 為什麼不直接用 `/queries/execute`

Dify 從 OpenAPI 匯入工具時，**只會依 request body 的第一層屬性組 body**，而且每個
工具需要自己的 path。`/queries/execute` 是所有定義共用一個 path、參數包在
`params` 底下，Dify 填不進去。所以 API Hub 加了一層轉接：

- `POST /{db}/tools/{name}` 收扁平 body（參數 + `fetch_mode`／`page`／`page_size`／
  `view`），轉成 `QueryRequest` 後呼叫同一個 `queries.execute`——權限、view、稽核
  與錯誤處理都走同一條路徑，沒有第二套實作
- 邊界與 MCP 工具一致：**拒絕 `all`／`batch`**、`page_size` 上限 100
  （`TOOL_PAGE_SIZE_MAX`）、**未宣告的參數回 400**，並列出可接受的參數
- operationId 就是 MCP 的工具名稱（`query_dw_eo_summary`…），skill 裡的名稱在兩邊都對得上

---

## 0. 身分：一把服務帳號金鑰

**Dify 社群版沒有 SSO**，拿不到使用者自己的 isap-auth token，所以 Dify 以一個服務
帳號呼叫 API Hub：

| 項目 | 值 |
|---|---|
| 帳號 | `svc_dify_eo`（`is_service_account`，不能登入密碼、不能是 superuser） |
| 角色 | `eo_reader`（`query:eo_*`）——看不到 HR／RD 的任何資料 |
| 金鑰 | `sc-dify E&O Analyst`，`rate_limit_per_minute=300` |
| 存放 | 只存在 Dify 工具 `apihub_eo` 的認證設定（Dify 加密儲存），不進任何檔案 |

**代價要知道：**

- **所有使用者共用同一個身分。** API Hub 的稽核記錄全部記在 `svc_dify_eo` 底下；
  要追是誰問的，看 Dify 的日誌（使用者欄是 Open WebUI 的 user id）
- **這個帳號拿到的是完整 MC view。** 若要開放給 Sales，另建一個帶 `eo:read:scoped`
  的帳號與一套 Dify 工具／應用，`eo_summary` 會被強制降成 sales view
- **同時在途上限是「每個 principal 4 個」**（`MAX_CONCURRENT_REQUESTS_PER_PRINCIPAL`），
  所有 Dify 使用者共用這 4 個。一個 Agent 回合內的工具呼叫是依序的，一般使用沒問題；
  多人同時跑慢查詢（`eo_item_history` 全站 ~35 秒）時，後到的請求等 5 秒拿不到位置
  會回 429。真的撞到再調高該帳號的上限，不要預先放大

金鑰撤銷：API Hub Admin UI → API Keys → 撤銷；Dify 端工具會立刻開始回 401。
換新金鑰後，到 Dify「工具 → 自訂 → apihub_eo → 編輯」更新認證即可，**應用不用重匯**。

---

## 1. API Hub web API

| 項目 | 值 |
|---|---|
| Base URL | `https://ia-dev.sercomm.com/apihub/api/v1` |
| 認證 | `X-API-Key: ahk_...`（或 `Authorization: Bearer <JWT／isap-auth token>`） |
| 工具文件 | `GET /DW/tools/openapi.json?group=eo` |
| 工具呼叫 | `POST /DW/tools/<definition>` |

```bash
export APIHUB=https://ia-dev.sercomm.com/apihub/api/v1
export APIHUB_API_KEY=ahk_...

# 工具文件 -> 應為 200，13 個 operation
curl -ks -H "X-API-Key: $APIHUB_API_KEY" "$APIHUB/DW/tools/openapi.json?group=eo" \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d['paths']),d['servers'])"

# 最新一期 -> 應為 200，data 裡一筆 MAX_FROZEN_DATE
curl -ks -H "X-API-Key: $APIHUB_API_KEY" -H 'Content-Type: application/json' \
  -d '{}' "$APIHUB/DW/tools/eo_max_frozen_date"

# 扁平參數
curl -ks -H "X-API-Key: $APIHUB_API_KEY" -H 'Content-Type: application/json' \
  -d '{"frozen_date":"2026-09-01","ou":"SP","page_size":20}' "$APIHUB/DW/tools/eo_data_quality"
```

- 工具文件**需要認證，且只列出該憑證能執行的定義**。所以 Dify 無法「從 URL 匯入」
  （它抓 URL 時不帶認證標頭），要先用 curl 抓下來再貼上
- 回應格式與 `/queries/execute` 完全相同（`total`、`columns`、`data`…）
- `servers[0].url` 取自 `APIHUB_PUBLIC_URL`，所以是對外網址，不是容器內部位址

---

## 2. 在 Dify 建立

sc-dify（`https://sc-dify.sercomm.com`，Dify 1.17.1）**已經建好**，本節是重建或在
另一台 Dify 上建置的步驟。

### 2.1 安裝 Agent 策略外掛

Agent 節點本身不含推理策略，需要 **`langgenius/agent`**（本台為 0.0.47）：
Dify → 外掛 → 探索 Marketplace → 搜尋「Dify Agent 策略」→ 安裝。

DSL 已把它列為相依套件，匯入時 Dify 也會提示安裝。

### 2.2 建立自訂工具 `apihub_eo`

1. 用上一節的 curl 抓 `openapi.json?group=eo`，存成檔案
2. Dify → 工具 → 自訂 → **建立自訂工具**
   - 名稱：`apihub_eo`
   - Schema：貼上整份 JSON，下方應列出 **13 個工具**，名稱都是 `query_dw_eo_*`
   - 認證方法：**API Key**，標頭 `X-API-Key`，前綴 **無**，值填金鑰
3. 儲存。任選 `query_dw_eo_max_frozen_date` 按「測試」，應回一筆日期

> **SQL 定義改了（加參數、改描述、新增 E&O 定義）要重新抓 schema 貼上。**
> Dify 存的是貼上當下的副本，不會自動跟著 API Hub 變。

### 2.3 匯入 Chatflow

`docs/dify/eo-analyst.yml` 的工具 provider id 與模型是佔位值（`CHANGE_ME…`），因為
provider id 是 Dify 建立工具時才產生的 UUID，每台都不同。兩種做法擇一：

**A. 產生該台專用的 DSL（建議）**

```bash
# provider id：Dify 資料庫 tool_api_providers 表中 name='apihub_eo' 的 id，
# 或在工具頁開發者工具看 /console/api/workspaces/current/tool-provider/api/get 的回應
python -m scripts.build_dify_app --stdout \
    --provider-id <apihub_eo 的 id> --model gb10/qwen3.8-27b > eo-analyst.yml
```

Dify → 建立應用 → **匯入 DSL 檔案** → 選 `eo-analyst.yml`。

**B. 匯入 repo 裡的檔案再手動修正**

匯入 `docs/dify/eo-analyst.yml` 後打開 Agent 節點：模型改成實際存在的模型；
工具清單刪掉原有工具後，從「自訂 → apihub_eo」重新全選加入。

### 2.4 檢查並發布

打開 Agent 節點確認：

| 項目 | 值 |
|---|---|
| Agent 策略 | FunctionCalling |
| 模型 | 支援 tool call 的模型（本台 `gb10/qwen3.8-27b`） |
| 工具清單 | 13 個 `query_dw_eo_*`，全部啟用 |
| 指令 | 以 `# Role` 開頭，約 4 萬字元 |
| 最大迭代次數 | 12（月度檢視一次約要 10 次工具呼叫；再高只會讓上下文更容易爆掉） |
| 錯誤處理 | 預設值：失敗時輸出一段「請縮小範圍」的說明，而不是外掛的錯誤堆疊 |
| 記憶 | 開啟，視窗 20 |

右上「預覽」問「列出可用的 E&O 凍結日期」，應看到呼叫 `query_dw_eo_frozen_dates`
並回傳最近三期。沒問題就**發布**。

### 2.5 接到 Open WebUI（一般使用者入口）

依 sc-ai-poc README「Dify 應用 → Open WebUI」：

1. 應用 → 存取 API → 建立金鑰（`app-…`）
2. Open WebUI → Admin Panel → Functions → Dify Apps → Valves → `DIFY_APPS` 加一筆
   `E&O Analyst:app-…`（聊天流程，第三段省略）
3. Admin Panel → Settings → Models → `dify/E&O Analyst` → 存取權限：
   設為要使用 E&O 資料的群組。**不要設成公開**——這個應用背後是完整 MC view

---

## 3. 驗證與故障判讀

| 症狀 | 原因 |
|---|---|
| Agent 節點顯示找不到策略 | 沒裝 `langgenius/agent` |
| 執行時 `api provider … not found` | DSL 的 provider id 不是這台的（第 2.3 節） |
| 工具回 **401** | 金鑰被撤銷或填錯；Dify 工具認證前綴要選「無」 |
| 工具回 **403** | 服務帳號沒有 `eo_reader`，或該定義的權限不是 `query:eo_*` |
| 工具回 **404**，路徑是 `/DW/tools/...` | API Hub 還沒部署這個轉接路由，或定義被停用（訊息會寫 disabled） |
| 工具回 **400** `Unknown argument(s)` | schema 過期：API Hub 的定義改了參數，Dify 還是舊副本——重抓貼上 |
| 工具回 **429** | 共用帳號的同時在途上限（4）或每分鐘上限（300）被打滿，見第 0 節 |
| 回答有數字但沒呼叫任何工具 | 模型不支援 tool call，或工具被停用；換模型，**不要採信那個回答** |
| 查詢回零筆 | 多半是期別或代碼猜錯（週不是月、`BU_NO` 像 `S3B`）——與 MCP 版相同，見 `eo-mcp-setup.md` 第 4 節 |
| 做到一半就停、沒有結論 | 最大迭代次數太小 |

在 Dify 那台主機上確認網路（sc-dify 的工具呼叫會經過 `ssrf_proxy`）：

```bash
docker exec sc-dify-api-1 python -c "import httpx;print(httpx.get('https://ia-dev.sercomm.com/apihub/api/v1/health',verify=False).status_code)"
```

---

## 4. 大量資料

與 MCP 版相同的原則，這裡只是更嚴格：工具呼叫**一頁最多 100 列**，而且拒絕
`all`／`batch`。`eo_detail` 單期約 139,000 列，不屬於 Agent 的上下文。

需要全站明細、自己跑統計、跨多期迴歸時，用 `scripts/extract_query.py` 抽成檔案，
做法見 `eo-mcp-setup.md` 第 5 節。Agent 被要求遇到這種需求時直接給使用者指令，
而不是自己翻頁。

---

## 5. 更新

| 變更 | 要做的事 |
|---|---|
| 改了 skill（`.claude/skills/eo-*`） | `python -m scripts.build_dify_app` 重產 DSL；Dify 端重新匯入或把新指令貼進 Agent 節點 |
| 改了 SQL 定義（參數、描述、新定義） | 重抓 `openapi.json?group=eo`，更新 Dify 工具 `apihub_eo` 的 schema；新定義還要加進 Agent 的工具清單與 `EO_TOOLS` |
| 換金鑰 | 只更新 Dify 工具的認證 |
| 換模型 | 只改 Agent 節點的模型 |

```bash
python -m scripts.build_dify_app            # 重產 docs/dify/eo-analyst.yml
python -m scripts.build_dify_app --check    # 檢查是否過期（CI 用）
```

`tests/test_dify_app_export.py` 會在 DSL 與 skill 不一致、或 skill 工具對照表列出的
工具不在 Agent 工具清單裡時讓建置失敗。

---

## 相關文件

- `docs/eo-mcp-setup.md` — MCP 版（Claude Code／Claude 桌面版／Hermes／Open WebUI 工具伺服器）
- `app/api/v1/tools.py`、`app/services/tool_openapi.py` — 轉接路由與 OpenAPI 產生
- `scripts/build_dify_app.py` — Chatflow DSL 產生器
- `docs/dify/eo-analyst.yml` — 可匯入的 Chatflow（含佔位值）
- `.claude/skills/eo-*/SKILL.md` — 五份 skill 本體
