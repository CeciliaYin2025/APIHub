# E&O MCP 服務與 Skill 安裝指南

給需要用 AI 查詢 E&O 資料的使用者。裝完之後，你可以直接用自然語言問
「SP 站上一期的紅燈料號比前一期多了哪些？」而不必開報表撈資料。

本文的端點與前置條件都是在 PRD 上實測過的，不是照抄設定檔。

> **不想自己裝 MCP？** 同一套 E&O 分析也以 Dify Chatflow 提供，資料走 API Hub 的
> web API 而不是 MCP，一般使用者在 Open WebUI 選 `dify/E&O Analyst` 即可。
> 建置方式見 `docs/eo-dify-setup.md`。

---

## 0. 你需要先有帳號

**這一步不能跳過，也不能自助完成。**

API Hub 的 `SSO_AUTOCREATE` 是關閉的，所以即使你在 isap-auth 登入成功，只要
API Hub 裡沒有對應的本地帳號，MCP 連線就會拿到 **403 Not provisioned**。

請管理者為你建立帳號並指派 `eo_reader` 角色。要提供的資訊只有兩項：

| 項目 | 說明 |
|---|---|
| Username | **必須與 isap-auth 送出的完全一致**，含前導零（例如 `00049` 不是 `49`） |
| 姓名 | 顯示用 |

`eo_reader` 的權限是 `query:eo_*`，只看得到 13 個 E&O 定義，看不到 HR/RD 的任何資料。
之後新增的 E&O 定義會自動涵蓋，不需要再調權限。

---

## 1. 安裝 MCP 服務

### 端點

| 項目 | 值 |
|---|---|
| MCP endpoint | `https://ia-dev.sercomm.com/apihub/mcp/mcp/` |
| Transport | Streamable HTTP |
| 授權伺服器 | `https://isap.sercomm.com/auth` |

兩個容易踩到的地方：

- **`/mcp/mcp/` 是兩層，而且結尾斜線必要。** 前面的 `/apihub/mcp/` 是 gateway 的
  路由前綴，後面的 `/mcp/` 是 Streamable HTTP 的掛載點。少一層或少一個斜線都會失敗。
- **授權伺服器在另一台主機**（`isap.sercomm.com`），不是 `ia-dev.sercomm.com`。
  這是正常的，MCP client 會自己從 RS metadata 讀到正確位置。

### Claude Code

```bash
claude mcp add apihub --transport http https://ia-dev.sercomm.com/apihub/mcp/mcp/
```

第一次呼叫工具時會開啟瀏覽器走 isap-auth 登入。已登入的話會直接跳回來。
之後 token 會自動更新，不需要重複登入。

驗證：

```bash
claude mcp list          # apihub 應為 connected
```

然後問一句「列出可用的 E&O 凍結日期」，應該會呼叫 `query_dw_eo_frozen_dates`
並回傳最近三期。

### Claude 桌面版 / 網頁版

在設定的 Connectors／MCP 區塊新增一個 **Streamable HTTP** 連線，URL 填上面的
endpoint。授權流程相同。

### Hermes

Hermes 是標準 MCP client，需要的資訊與上面完全相同：

- Transport：**Streamable HTTP**（不是 SSE、不是 stdio）
- URL：`https://ia-dev.sercomm.com/apihub/mcp/mcp/`
- 認證：OAuth 2.1，client 會自行完成動態註冊（RFC 7591），你不需要事先申請
  `client_id`

> Hermes 的設定介面位置會隨版本不同，本文未能實機驗證其 UI 路徑。以上是任何
> MCP client 都需要的值；若 Hermes 的設定畫面要求填 `client_id` 而不支援動態
> 註冊，請聯絡 Hermes 管理者，因為這個 MCP 服務是以動態註冊為前提部署的。

### Open WebUI

Open WebUI 沒有 MCP 的「安裝」概念，工具是掛在 **Settings → Tools（工具伺服器）**
底下，整台實例共用，通常由管理者設定一次：

- 較新的版本可以直接填 MCP 的 **Streamable HTTP** URL（同上）
- 較舊的版本只吃 OpenAPI，需要管理者用 `mcpo` 之類的代理把這個 MCP 轉成 OpenAPI
  再掛上來

> **本文未能實機驗證 Open WebUI 的 OAuth 行為。** 這個 MCP 服務以動態註冊
> （RFC 7591）為前提部署，若 Open WebUI 的工具設定畫面要求填固定的 `client_id`
> 或只支援 Bearer token，請聯絡管理者；後者可以改用 API Key／`apihub_login` 換到
> 的 token（第 5 節）當作靜態憑證，但那條路上**所有人共用同一個身分**，稽核記錄
> 會全部記在那個帳號底下。

### 連不上時的判讀

在**公司網路內**執行以下指令，可以定位問題出在哪一段：

```bash
# 1. 服務本身活著，且正確要求授權 -> 應為 401 且帶 WWW-Authenticate
curl -ksI -X POST https://ia-dev.sercomm.com/apihub/mcp/mcp/

# 2. 資源描述文件 -> 應為 200
curl -ks https://ia-dev.sercomm.com/.well-known/oauth-protected-resource/apihub/mcp

# 3. 授權伺服器描述文件 -> 應為 200
curl -ks https://isap.sercomm.com/auth/.well-known/oauth-authorization-server
```

| 症狀 | 原因 |
|---|---|
| 工具清單是空的，但連線成功 | 帳號沒有 `eo_reader` 角色 |
| 登入後仍然 403 | API Hub 沒有你的本地帳號，或 username 與 isap-auth 不一致（檢查前導零） |
| `404 Not Found` | URL 少了一層 `/mcp/` 或結尾斜線 |
| 瀏覽器授權頁面出現 `Unknown app_id or redirect_uri not allowed` | 這是伺服器端設定問題，回報給管理者 |
| 每隔約 8 小時就被要求重新授權 | access token 效期 8 小時，正常情況下 client 會拿 refresh token 靜默續期（最長 30 天）。若每 8 小時都跳登入，表示這次授權沒拿到 refresh token——請清掉該 MCP server 的既有授權再重連一次，讓 client 重跑一次完整流程 |

---

## 2. 安裝 Skill

MCP 給的是**資料**，Skill 給的是**怎麼讀這份資料**——哪些欄位不能直接加總、
哪些百分比會誤導、追查歸屬要照什麼順序。沒有它，AI 拿到正確的數字仍可能講出
錯誤的結論。

共五份，各自獨立，可以只裝需要的：

| Skill | 用途 | 附錄 |
|---|---|---|
| `eo-analysis` | **進入點。** 工具選擇、期別鎖定、判讀陷阱、回報規則 | A |
| `eo-data-dictionary` | 欄位參考：金額家族、可否加總、代碼分類、資料粒度 | B |
| `eo-business-rules` | 歸屬邏輯與已知規則變更，解釋歸屬為何改變 | C |
| `eo-monthly-review` | 月度檢視工作流 | D |
| `eo-issue-investigation` | Issue 追查工作流 | E |

**只裝一份的話裝 `eo-analysis`。** 其餘四份會在需要時被它引用；沒有它們仍能運作，
只是遇到欄位語意或追查流程時 AI 得自己推敲。

### Claude Code

```bash
# 個人用（所有專案都生效）
mkdir -p ~/.claude/skills
cp -r .claude/skills/eo-* ~/.claude/skills/

# 或專案用（只在這個 repo 生效，可進版控與同事共用）
# 本 repo 已內含這些 skill，不需要額外動作
```

驗證：輸入 `/eo-analysis`，或直接問一個 E&O 問題，它應該會先呼叫
`eo_frozen_dates` 確認期別再往下查。

### Hermes 及其他 client

Skill 是純 Markdown（`SKILL.md` + YAML frontmatter），不依賴任何 Claude Code
專屬功能。若 client 不支援 skill 機制，把內容當成系統提示、自訂指令或知識文件
貼進去，效果相同。

**五份全文收錄在本文結尾的附錄 A～E**，可直接複製，不需要另外取得 repo。

關於開頭的 `---` frontmatter：存成 `SKILL.md` 由 skill 機制載入時**必要**；當成
純提示詞貼上時可以省略。

skill 之間以 `[[skill-name]]` 互相引用。若你只貼其中幾份，那些指向未安裝 skill
的連結會失效 —— 不影響運作，AI 會忽略它們，但該處的知識就缺了。

### Open WebUI（直接匯入現成的模型）

Open WebUI 的對應物是 **workspace model**：一個 base model 加一段存好的系統提示。
五份 skill 已經合併成一份可直接匯入的 JSON：

```
docs/openwebui/eo-analyst.json
```

匯入方式：**Workspace → Models → Import**（右上角），選這個檔案。

匯入後**一定要確認兩個地方**，否則模型會匯入成功但一問就失敗：

1. **Base model** — 檔案裡是 `CHANGE_ME`，改成這台實例上實際存在的模型。故意留成
   一個明顯錯誤的值，因為填一個看起來合理但不存在的 id 會安靜地匯入、直到第一則
   訊息才爆掉
2. **Tools** — JSON 已經把 `toolIds` 綁成 `API Hub — E&O`，也就是這台實例上工具
   伺服器的名稱（注意中間是破折號 `—`，不是連字號）。若你的實例上不是這個名字，
   請在模型設定裡改掉。這一項沒有 `CHANGE_ME` 那麼明顯：**對不上的 id 會被安靜
   忽略**，模型照常回答，只是一個數字都查不到——看起來就像它自己決定不呼叫工具

系統提示的第一節是硬規則：**任何 E&O 數字都必須來自當回合的 `query_dw_eo_*` 呼叫**，
先鎖期別（`eo_max_frozen_date`／`eo_frozen_dates`）、代碼一律問 `eo_dimensions`、
工具掛不上就直說而不是憑常識作答。這一段是必要的——後面那五份 skill 讀起來就像一份
參考文件，而參考文件正好會誘使模型直接照著回答。

其餘預設值：`temperature` 0.2、可見範圍為全實例公開（要限縮請在 UI 的 Access
Control 改）。系統提示約 39,000 字元（≈1 萬 token），每則訊息都會送出。

skill 更新後重新產生：

```bash
python -m scripts.build_openwebui_model            # 重新產生 JSON
python -m scripts.build_openwebui_model --check    # 檢查是否過期（CI 用）
```

已匯入的模型**不會自動更新**——重新產生後要再匯入一次（同 `id` 會覆蓋）。
`tests/test_openwebui_model_export.py` 會在 JSON 與 skill 不一致時讓建置失敗。

---

## 3.（選用）本機快取，加速重複查詢

E&O 有兩個查詢特別慢：`eo_item_history` 全站約 35 秒、`eo_red_items` 跨兩期
全站約 14 秒。原因是底層 view 對 `Item_No` 沒有索引。

凍結期一旦結束就不會再變，所以重跑同一個封閉期別的查詢只會拿到一模一樣的結果。
`scripts/eo_cache.py` 把這類結果存在本機重複使用。

```bash
export APIHUB_URL=https://ia-dev.sercomm.com/apihub
export APIHUB_API_KEY=ahk_...        # 或 APIHUB_TOKEN=<JWT>

# 查詢（第一次連線，之後同樣參數直接命中快取）
python -m scripts.eo_cache fetch --query eo_red_items \
    -p frozen_date=2026-06-26 -p compare_date=2026-05-29 -p ou=SP

python -m scripts.eo_cache status              # 看目前快取了什麼
python -m scripts.eo_cache clear --query eo_red_items
python -m scripts.eo_cache clear --all         # 全部清空
python -m scripts.eo_cache fetch ... --refresh # 忽略快取，強制重抓單筆
```

**最新一期永遠不會被快取。** 腳本每次執行都會去問 `eo_max_frozen_date`，只要你
要的日期不是嚴格早於最新一期，就一律即時查詢。沒有帶日期參數的查詢
（`eo_trends`、`eo_item_history`、未指定日期的 `eo_dimensions`）本質上都會掃到
最新一期，因此也一律即時查詢。這是刻意的：最新一期上游還會重跑修正，快取它
會在分析師最可能查看的情況下悄悄給出過期資料。

快取位置預設在 `~/.cache/apihub-eo`，可用 `EO_CACHE_DIR` 改。

**超過約 5 萬列的結果不會進快取**，腳本會直接擋下並要你改用
`scripts/extract_query.py` 串流到檔案——`eo_detail` 單期就有約 139,000 列，
那種量不屬於快取，也不屬於 AI 的上下文。做法見第 5 節。

> 這一節需要一個憑證。**你不需要 API Key** —— SSO／SAML 使用者可以用
> `python -m scripts.apihub_login` 從瀏覽器換一顆 token，做法見第 5 節開頭。
> API Key 仍然適用於無人值守的排程（那種情境沒有瀏覽器可開）。

---

## 4. 可用的工具

裝好之後你會看到 13 個 `query_dw_eo_*` 工具。不需要背，AI 會自己挑，但知道有
哪些能幫你問得更精準：

| 工具 | 回答什麼 |
|---|---|
| `eo_frozen_dates` / `eo_max_frozen_date` | 有哪些期別 / 最新一期 |
| `eo_dimensions` | OU / BU / PID / 客戶 / 專案 的有效代碼 |
| `eo_summary` | 依 OU+BU+PID+客戶群的彙總金額。**有 `view` 參數**：`mc`（預設，完整報表）或 `sales`（給 Sales 的較窄欄位集） |
| `eo_detail` | 料號級明細 |
| `eo_red_items` | 紅燈料號，可比較兩期新增／移除 |
| `eo_item_history` | 某料號的歸屬異動軌跡與異動時點 |
| `eo_trends` | 逐月趨勢與月變動 |
| `eo_data_quality` | 無 PID／無客戶／BU 為 TBC 的金額與占比 |
| `eo_unrecoverable` | Unrecoverable、Buy Down、Reserved |
| `eo_feedback` | 料號級 Action Plan 與人工註記 |
| `eo_feedback_summary` | PID 級 MC 註記 |

### 四件最容易問錯的事

**期別是「週」不是「月」。** 快照是每週四加上月底補一筆，不是月結。問「上個月」
時請說清楚是哪一期，或讓 AI 先查 `eo_frozen_dates`。傳入一個不存在的凍結日
**會回傳零筆而不是錯誤**，看起來就像「沒有 E&O」。

**代碼不好猜。** `BU_NO` 長得像 `S3B`、`PID` 長得像 `DYT`。猜錯一樣是靜默回零筆。
先問「有哪些 BU？」讓 AI 呼叫 `eo_dimensions`。

**數字要看金額占比，不是列數占比。** 未歸屬的料號集中在高金額品項，例如 SN 站
只有 6.5% 的列沒有 PID，卻占了 54.9% 的呆滯金額。只看列數會嚴重低估問題。

**「Obsolete」和「E&O」是同一個欄位的兩個名字。** 同一筆 `INV_EO_PID_SUMMARY_F`
資料同時供給兩份報表，而**兩份用不同的顯示名稱**：MC 報表寫「Obsolete All OH
Amt」，寄給 Sales 的 Excel 寫「E&O OH (All) Amt」，兩者都是
`OBSOLETE_OH_ALL_AMT_TWD`。Sales 那份把所有「Obsolete」一律改成「E&O」。所以當
對方引用一個欄位名稱時，**先確認他看的是哪一份報表**，否則會很有信心地回答錯的
欄位。完整對照表在附錄 B。`eo_summary` 的回應會告訴你套用了哪個 view，以及
`masked_columns` 有哪些；`view_downgraded: true` 代表是呼叫者的權限把欄位收窄
了，不是資料缺漏。

---

## 5. 大量資料：走檔案，不要走 MCP

MCP 工具會把查到的**每一列都放進 AI 的上下文**。這對「SP 站這期呆滯金額多少」
很合適，對「把這期全站明細撈出來自己算」則是錯的工具——不是慢，是形狀就不對。

具體的數字：`eo_detail` 單一期別約 **139,000 列**。MCP 的 `page_size` 有上限
（`MCP_PAGE_SIZE_MAX`，本站 prod 是 100），所以那是 **1,390 次工具呼叫**，而且
每一次的結果都留在上下文裡。實際發生過：2026-08-20 與 08-24，單一 MCP 客戶端
翻頁一個沒有篩選條件的定義，吃掉了伺服器 **99.5%** 的處理時間——伺服器現在對
每個呼叫者有同時在途請求數的上限，就是那次之後加的。

### 第一步：先拿到憑證（SSO／SAML 使用者看這裡）

抽檔腳本走的是 REST API，需要一個憑證，而你用 SAML 登入 —— 沒有本機密碼可以換
JWT，也沒有 API Key（那屬於管理者建的服務帳號）。

**但你不需要向管理者要 API Key。** API Hub 本身就是 OAuth 2.1 的 Resource
Server，所以 isap-auth 發的 token 直接就是 `APIHUB_TOKEN`。
`scripts/apihub_login.py` 會開瀏覽器讓你用平常那套 SSO 登入，換一顆 token 回來：

```bash
export APIHUB_URL=https://ia-dev.sercomm.com/apihub

eval "$(python -m scripts.apihub_login --export)"   # 開瀏覽器登入，設好 APIHUB_TOKEN
python -m scripts.apihub_login --status             # 看還有多久到期
```

- **瀏覽器那一段就是 SAML 能運作的原因**：身分驗證由你自己在瀏覽器裡完成，跟登入
  Admin UI 是同一條路徑，這支腳本從頭到尾看不到你的密碼
- 若你的 SSO session 還活著，整個過程是**靜默的**，瀏覽器一閃就完成
- token 存在 `~/.config/apihub/oauth.json`（權限 `0600`，可用 `APIHUB_CACHE_DIR`
  改位置）。**有效期 8 小時**，而且帶了 refresh token，所以之後直接命中快取或自動
  續期，不會每次都要你登入
- 同一顆 token 也適用於第 3 節的 `scripts/eo_cache.py`
- **無人值守的排程仍然要用 API Key** —— 那種情境沒有瀏覽器可以開，請管理者核發一把
  服務帳號金鑰

> 腳本只從 `APIHUB_URL` 出發自動探索：先讀 API Hub 的 RFC 9728 資源描述文件找出
> 授權伺服器，再讀它的 RFC 8414 描述文件取得各端點。換一套環境不需要改設定。

### 第二步：抽成檔案

`scripts/extract_query.py` 走 REST API 的 `batch` 模式串流寫檔，兩邊都不會一次
持有整份結果：

```bash
python -m scripts.extract_query --db DW --query eo_detail \
    -p frozen_date=2026-08-01 --out ~/eo/eo_detail_2026-08-01.csv
```

- `--out` 的副檔名決定格式：`.csv` / `.parquet` / `.xlsx`（後兩者需要額外套件）
- `--batch-size` 預設 5000，上限 10000。**調大幾乎純賺**：伺服器每個 batch 都會
  重算一次 `COUNT(*)`，所以每次請求的成本幾乎與筆數無關
- 這是**客戶端工具**，不在伺服器映像的執行路徑上；需要 API Key 或 JWT

一期抽一次就好。**封閉期別的資料不會再變**，重抽只會拿到一模一樣的東西。

### 第三步：讓 AI 自己建一份 skill 去讀那個檔案

抽完檔案，分析的形狀就從「把列讀進上下文」變成「對本機檔案跑程式」。這件事不該
每次重講一遍，讓 AI 把它寫成一份自己的 skill。可以直接這樣要求它：

> 幫我建一份 skill，讀 `~/eo/` 底下用 `scripts/extract_query.py` 抽出來的 E&O
> 明細檔。原則：用 pandas 或 duckdb 讀檔，**先聚合再把結果帶回上下文**，不要把
> 整份資料印出來。欄位語意、哪些欄位不能跨列加總、金額家族的定義，一律以
> `eo-data-dictionary` 為準。

一份堪用的 skill 至少要寫清楚這四件事：

1. **檔案在哪、代表哪一期。** 檔名要帶 `frozen_date`，因為期別是週不是月，事後
   分不出來的檔案等於廢的
2. **先聚合，再回上下文。** `groupby().sum()` 之後回傳幾十列，不要 `df.to_string()`
   整份、也不要 `cat` 那個 CSV——那等於繞一大圈又把資料塞回上下文，白抽了
3. **加總規則不因為換成本機檔案而改變。** 比率欄、已經是彙總的欄位、沒有 `_TWD`
   雙生欄的本地幣別欄，該不能加總的還是不能——判斷依據仍然是 `eo-data-dictionary`，
   不是 pandas 能不能算出一個數字
4. **要重算就重抽，不要改檔。** 就地編輯過的抽出檔沒有辦法追溯來源

### 兩者怎麼分工

| 情境 | 用什麼 |
|---|---|
| 問一個數字、一個排名、一個期別的比較 | **MCP 工具** |
| 重跑同一個封閉期別的慢查詢 | **`scripts/eo_cache`**（第 3 節） |
| 需要全站明細、要自己跑統計、要跨多期迴歸 | **`scripts/extract_query.py` + 自建 skill** |

判準很簡單：**答案需要幾列，就用 MCP 拿幾列。需要幾萬列才算得出來的東西，先抽成
檔案。**

---

## 相關文件

- `README.md` — API Hub 總覽
- `docs/system_specification.md` — 系統規格
- `deploy/apihub/README.md` — 伺服器端部署與 OAuth 設定
- `.claude/skills/eo-*/SKILL.md` — 五份 skill 本體（全文見下方附錄 A～E）
- `docs/openwebui/eo-analyst.json` — Open WebUI 可直接匯入的模型（第 2 節），由 `scripts/build_openwebui_model.py` 產生
- `.claude/skills/new-sql-definition/SKILL.md` — 新增 SQL 定義的規則（給開發者，未收錄於附錄）
- `scripts/apihub_login.py` — SSO／SAML 使用者用瀏覽器換取 token（第 5 節）
- `scripts/extract_query.py` — 大量資料抽檔（第 5 節），`--help` 有完整參數
- `scripts/eo_cache.py` — 封閉期別的本機快取（第 3 節）
- `docs/eo-dify-setup.md` — Dify Chatflow 版：Agent + API Hub web API，不經 MCP

---

---

## 附錄：Skill 全文

供無法取得 repo、或 client 不支援 skill 機制者（例如 Hermes）直接複製。
每一份都是獨立完整的，可以只取需要的。

> **以下皆為副本。** 唯一有效的版本是 repo 裡的 `.claude/skills/<name>/SKILL.md`；
> 那些檔案更新時本附錄必須一併更新。`tests/test_docs_skill_sync.py` 會在兩者
> 不一致時使建置失敗。

### 附錄 A — `eo-analysis`

進入點。工具選擇、期別鎖定、判讀陷阱與回報規則。**只裝一個的話裝這個。**

````markdown
---
name: eo-analysis
description: Query and analyse Sercomm E&O (Excess & Obsolete) inventory data through the API Hub MCP tools, with local caching of closed frozen periods. Use for any question about E&O amounts, obsolete/excess stock, red-flag items, item attribution (why an item sits under a BU/PID/customer), Action Plan feedback, or E&O trends.
---

# E&O Analysis

Answer E&O questions using the thirteen `query_dw_eo_*` MCP tools, reusing locally
cached results for closed periods instead of re-running slow queries.

**Question / task:** $ARGUMENTS

## Before anything else: pin the period

E&O snapshots are **weekly** (usually Thursday), plus an extra one at month-end.
They are *not* monthly, and older documentation that says "monthly close basis"
is wrong.

- `query_dw_eo_max_frozen_date` — the single latest date.
- `query_dw_eo_frozen_dates` — the list, newest first; defaults to the last 3
  periods (= last 3 weeks), `periods=0` for all 71.

Never invent a date. A date that is not an actual frozen date returns **zero
rows, not an error** — which reads exactly like "no E&O", and is the single
easiest way to produce a confidently wrong answer.

`eo_feedback` is the exception: its periods are roughly **monthly** and do not
line up with the weekly snapshot dates. Omit `frozen_date` there to get its own
latest period.

## Tool map

| Question | Tool |
|---|---|
| Which periods exist? | `eo_frozen_dates`, `eo_max_frozen_date` |
| What are the valid OU / BU / PID / customer / project codes? | `eo_dimensions` |
| How much E&O by OU+BU+PID+customer group? | `eo_summary` |
| Item-level detail for a period | `eo_detail` |
| Which items are red-flagged, and what changed vs another period? | `eo_red_items` |
| Which items' E&O amount moved most between two periods (for a customer / BU / PID / site)? | `eo_item_diff` |
| Why is this item attributed to X? When did it move? | `eo_item_history` |
| How is E&O trending month to month? | `eo_trends` |
| How much E&O has no PID / no customer / a TBC BU? | `eo_data_quality` |
| Unrecoverable with buy-down and reserve | `eo_unrecoverable` |
| What did the owner say about this item? | `eo_feedback` |
| PID-level MC highlight notes | `eo_feedback_summary` |

Always resolve codes with `eo_dimensions` before filtering. `BU_NO` looks like
`S3B`, `PID` like `DYT` — a guessed value silently returns nothing.

**Never page through a tool to search for something.** Every page you fetch stays
in the context for the rest of the question, and a handful of 100-row pages of
`eo_dimensions` or `eo_detail` is enough to overflow it — the run then fails with
no answer at all. Fetch at most two pages of any one tool per question. If what
you need is not there, narrow the filter instead: to find a customer's items, look
the customer group up with `eo_dimensions` `dim='CUSTOMER'` (sorted by amount, so it
is on page 1) and pass it as a filter. For "which items changed most between two
periods", use `eo_item_diff`: it ranks server-side, so page 1 *is* the answer. If a
question genuinely needs thousands of rows, say so and hand over the
`scripts/extract_query.py` command rather than walking pages.

## Caching closed periods

Frozen periods are immutable once closed, so re-querying one can only return the
same rows. The slow tools (`eo_item_history` ~35s, `eo_red_items` ~14s across all
sites) are worth caching; use `scripts/eo_cache.py` rather than repeating them.

```bash
export APIHUB_URL=https://ia-dev.sercomm.com/apihub
export APIHUB_API_KEY=ahk_...            # or APIHUB_TOKEN=<JWT>

python -m scripts.eo_cache fetch --query eo_red_items \
    -p frozen_date=2026-06-26 -p compare_date=2026-05-29 -p ou=SP
python -m scripts.eo_cache status
python -m scripts.eo_cache clear --query eo_red_items
python -m scripts.eo_cache clear --all
```

**The latest period is never cached**, and the script enforces that itself — it
checks `eo_max_frozen_date` on every run and refuses to store a result whose
newest date parameter is not strictly older. Requests with no date parameter
(`eo_trends`, `eo_item_history`, a bare `eo_dimensions`) always sweep the latest
period and are always fetched live. Do not try to work around this; the latest
period is re-run and corrected upstream.

When to reach for the cache versus the MCP tool directly:

- **MCP tool** — the latest period, small results, anything interactive.
- **`eo_cache.py`** — a closed period you will query more than once, or a
  multi-step analysis that revisits the same period.
- **`scripts/extract_query.py`** — anything above ~50k rows. `eo_detail` for one
  period is ~139k rows and belongs in a file, never in a cache entry or a context
  window. The cache script refuses these and says so.

Force a refetch with `--refresh` for one entry, or `clear` to drop entries. Do
this whenever the user says the data looks stale, or after upstream reports a
period was re-run.

## Reading the data correctly

These are the traps that produce plausible, wrong answers. Each is documented in
the tool's own `Returns:` text — read it rather than assuming.

- **`eo_summary` serves two audiences under different names.** It takes a `view`
  argument: `mc` (default, the full report) or `sales` (the narrower set sent to
  Sales). The two rename the same columns — MC "Obsolete All OH Amt" is Sales
  "E&O OH (All) Amt" — so when a user quotes a column name, resolve it through
  the `eo-data-dictionary` skill before answering. The response reports which
  `view` was applied and lists `masked_columns`; `view_downgraded: true` means the
  caller's permissions narrowed it, not that data is missing.
- **`eo_summary` grain is OU + BU_NO + PID + CUSTOMERGROUPNAME.** Grouping by
  OU+BU+PID alone collapses ~4% of rows and double-counts amounts.
- **`eo_detail` has no unique key.** ~390 of 139k rows repeat even on a nine-column
  key. Aggregate with SUM; never assume one row per item.
- **`eo_unrecoverable`: `BUYDOWN_AMT` and `RESERVED_AMT` are BU-level values
  repeated on every customer-group row.** Summing them across rows overstates
  RESERVED by ~10.6x. Take one value per `BU_NO`. There is **no balance column**
  and the obvious arithmetic does not hold — do not derive one.
- **`eo_data_quality`: read `PCT_OF_SITE_OBSOLETE_AMT`, not `PCT_OF_SITE_ROWS`.**
  They diverge hard because unattributed items skew to high value — SN is 6.5% of
  rows but 54.9% of obsolete amount. Its ISSUE rows overlap and do not sum to `ALL`.
- **`eo_trends`: the newest month may be a few days after the previous one.**
  Month-end picks are compared pairwise, and the current month is usually
  incomplete. Check `FROZEN_DATE` against `PREV_FROZEN_DATE` before quoting a
  month-on-month percentage.
- **`eo_feedback_summary` is almost empty** (2 rows in total). An empty result
  means "nobody recorded anything here", never "no issue".
- **Missing attribution is an empty string, not NULL**, and `TBC` appears only in
  `BU_NO`.

## Investigating an attribution issue

The common MC question is "why is this item sitting under X?".

1. `eo_item_history` with `item_no` and **`ou`** — pass `ou`, it is the
   difference between ~3s and ~35s. Read `IS_CHANGE_POINT` and `CHANGED_FIELDS`.
2. In `CHANGED_FIELDS`, `BU_NO` / `PID` / `CUSTOMER` mean the **value** changed;
   `BU_NO_N` / `PID_N` / `CUSTOMER_N` mean only the **number of distinct values**
   changed. They are different findings — do not report a count change as a
   re-attribution.
3. `eo_feedback` with the same `item_no` and `manual_only='Y'` for what the owner
   wrote about it.
4. `eo_feedback_summary` for any PID-level MC note citing a transfer document or
   an instruction to change BU.

## Reporting

State the frozen period in every answer — a number without its period is
meaningless here. Amounts are TWD unless the column name says otherwise; several
columns have no `_TWD` twin and are in local currency, which each tool's
`Returns:` text lists explicitly. If a figure came from cache, say which period
and when it was cached.
````

### 附錄 B — `eo-data-dictionary`

欄位參考。金額家族、可否加總、代碼分類、各來源的真實資料粒度。

````markdown
---
name: eo-data-dictionary
description: Field-level reference for Sercomm E&O data — what each amount family means, which columns are safe to sum, the code taxonomies (INDICATOR, ACTION_PLAN, TBC), the real grain of each source, and the period cadences. Load when interpreting E&O column names, deciding how to aggregate, or checking whether a figure means what it appears to mean.
---

# E&O Data Dictionary

Reference for the columns behind the `query_dw_eo_*` tools. Every fact here was
measured against the live warehouse rather than read off a schema — the source
column names are frequently misleading, and several long-standing descriptions
turned out to be wrong.

**Looking up:** $ARGUMENTS

Measurements are from frozen period **2026-08-01** unless stated. Counts drift;
the shapes and relationships do not.

## Periods

Two different cadences. Confusing them silently returns zero rows.

| Source | Cadence | Count | Range |
|---|---|---|---|
| `INV_EO_PID_SUMMARY_F` (summary, detail, trends, quality, red items) | **Weekly**, usually Thursday, plus an extra at month-end | 71 | 2025-01-02 → 2026-08-01 |
| `INV_EO_UNRECOVERABLE_V` | Weekly, wider history | 86 | 2024-03-04 → 2026-08-01 |
| `INV_EO_PID_DETAIL_Feedback_R` (feedback) | **Roughly monthly**, does not align with the above | 15 | 2025-06-26 → 2026-08-01 |

`2025-10-31` exists in feedback but has **no matching E&O snapshot**. Documentation
calling the frozen date a "monthly close basis" is wrong; 52 of 70 gaps are 7 days.

A `frozen_date` that is not an actual frozen date returns **zero rows, not an
error**. Resolve with `eo_frozen_dates` first.

## Grain — what one row means

| Source | Grain | Verified |
|---|---|---|
| `INV_EO_PID_SUMMARY_F` | OU + BU_NO + PID + CUSTOMERGROUPNAME | 1,774 rows = 1,774 distinct combos = 1,774 distinct `SUMMARY_ID` |
| Detail TVF | **No unique key exists** | 138,986 rows vs 138,600 distinct on a nine-column key — ~390 genuine duplicates |
| `INV_EO_UNRECOVERABLE_V` | BU_NO + CUSTOMERGROUPNAME (`OU` is always the literal `'ALL'`) | 354 rows = 354 combos |
| `INV_EO_PID_DETAIL_Feedback_R` | SITE + BU_NO + PID + ITEM_NO + CUSTOMER_NUMBER, **not unique** | 51,921 rows vs 51,842 combos — 79 duplicates |

Grouping summary by OU + BU + PID (without customer group) collapses to 1,424
combos — it silently merges ~20% of rows. Always SUM; never assume one row per item.

## Two audiences, two names for the same column

The same `INV_EO_PID_SUMMARY_F` row is published twice, and **the two use
different display names**. Getting this wrong is the single most common way to
answer confidently about the wrong column.

- **MC view** — the online report "MC E&O Summary by Site" (`EO_Summary_v4.0.cpt`).
  Internal technical names, including the TP70/TP75 exclusion conditions, and the
  loss-evaluation block.
- **Sales view** — the Excel sent to Sales ("E&O Summary - Sales", columns A–X).
  Fewer columns, plainer names, and the rule that catches everyone:
  **"Obsolete" is renamed "E&O"**.

So a user who says "E&O OH (All) Amt" and a user who says "Obsolete All OH Amt"
are asking about **the same column**, `OBSOLETE_OH_ALL_AMT_TWD`. Always answer
using the db_field.

### The renames (everything else keeps its name)

| db_field | MC label | Sales label |
|---|---|---|
| `OBSOLETE_OH_ALL_AMT_TWD` | Obsolete All OH Amt | **E&O OH (All) Amt** |
| `OBSOLETE_OPO_AMT_TWD` | Obsolete OPO Amt | **E&O OPO Amt** |
| `OBSOLETE_OHOPO_ALL_AMT_TWD` | Obsolete All OH+OPO Amt | **E&O OH + OPO Amt** |
| `FG_Intransit_Amt_TWD` | FG on the way Amt | **FG In Transit Amt** |
| `FG_Hub_Amt_TWD` | FG Hub Stock Amt | **Hub OH Amt** |
| `FACTORY_STOCK_AMT_TWD` | Available OH Amt (exclude TP75 & TP80 / TP70 & TP82) | **Non-TP70 & Non-TP75 OH Amt** |
| `ACTION_PLAN_DUE_DATE` | Due Date for Sales' Action | **Sales AP Due Date** |
| `ALLOCATED_AMT_TWD` + `PLANNED_AMT_TWD` | Allocated Amt **(C)** and **(E)**, plus a total | **Allocated Amt** (total only) |
| `ProjectName` / `AliasProjectNAme` | Project Name, **two columns** | Project Name, **one** |

`FROZEN_DATE` is a data column in MC and a **sheet header** in the Sales
workbook — the API returns it in both views, because a row with no period is
meaningless.

### What Sales does NOT get

The Sales view has no loss evaluation, no excess ageing, and no stock structure
beyond the headline buckets. If a user is reading the Sales workbook they cannot
see any of:

| Group | Columns |
|---|---|
| Loss evaluation | `OH_unrecoverable`, `OPO_unrecoverable`, `Obsolete_All_OH_EST_Loss_Amt`, `Obsolete_OPO_EST_Loss_Amt`, `SALES_OBSOLETE_OH_ALL_AMT`, `SALES_OBSOLETE_OPO_AMT`, `Cust_Liable_Amt` |
| Unrecoverable block | `UNRECOVERABLE_AMT`, `BUYDOWN_AMT`, `RESERVED_AMT`, Balance |
| Excess ageing | the six `EXCESS_{OH,OPO,OHOPO}_AMT_{26W,52W}_TWD` |
| E&O breakdown | the `_TP70` / `_TP75` / `_PLUSAMT` / `Non_TP70 & TP75` variants |
| Stock structure | `TP80_Amt`, `ALL_OH_AMT`, `UNAVAILABLE_OH_AMT`, Total INV Amt |
| Reference | `LOCK_QTY`, `MSIC` |
| Goal | `TARGET_AMT` (Meeting Goals) |

`WIP_AMT` and `TP75_Amt` are in the Sales view and have **no `_TWD` twin** — they
are local currency. Sales therefore also carries `CURRENCY_CODE` and
`TWD_CURRENCY_RATE`, which neither report prints: a workbook is rendered per site
so its currency is implicit, an API response has no such context.

### Asking for a view

`query_dw_eo_summary` takes a **`view`** argument: `mc` (default, everything) or
`sales`. The response reports `view`, and `masked_columns` when anything was
dropped. A caller granted `eo:read:scoped` is served `sales` whatever they ask
for, with `view_downgraded: true` — so if a number a user expects is absent,
check that flag before assuming the data is missing.

The machine-readable mapping is the `eo://data-dictionary` MCP resource, served
from `GET /api/v1/eo/field-dictionary`. Canonical source:
`app/services/eo_field_views.py`.

Columns Y onward in the Sales workbook (`Management Mandate`, `Category`,
`Rax's reply`, `Type`, dated notes like `20260315lc`) are **not report columns** —
they are MC/IT annotations typed into Excel for issue tracking, and exist in no
database.

## Amount families

Both summary and detail carry the same conceptual families. Names differ between
the two sources, so check which tool you are reading.

| Family | Meaning |
|---|---|
| **Stock on hand** | `FG_Intransit`, `FG_Hub`, `TP70`, `TP75`, `TP80`, `FACTORY_STOCK`, `WIP`, `ALL_OH`, `UNAVAILABLE_OH` |
| **Commitment** | `OPO` (open purchase order), `ALLOCATED`, `PLANNED_ALLOCATED`, `PLANNED` |
| **Excess** | `EXCESS_{OH,OPO,OHOPO}_AMT_{26W,52W}` — excess against a 26- or 52-week demand horizon |
| **Obsolete** | `OBSOLETE_{OH,OPO,OHOPO}_AMT`, with `_PLUSAMT`, `_ALL_AMT`, `_TP70`, `_TP75`, `_NON70` variants |
| **Unrecoverable** | `unrecoverable_oh`, `OH_UNRECOVERABLE_AMT`, `unrecoverable_opo`, `OPO_UNRECOVERABLE_AMT` (detail only) |

`OH` = on hand, `OPO` = open PO, `OHOPO` = the two combined. `TP70` / `TP75` /
`TP80` are stock-status buckets, not amounts of their own family.

### Currency — the `_TWD` suffix is not universal

Most amounts have a `_TWD` twin converted from `CURRENCY_CODE` via
`TWD_CURRENCY_RATE`. **Some do not**, and assuming otherwise mixes currencies.

- `INV_EO_PID_SUMMARY_F` — 25 `_TWD` columns. **No twin** for `PLANNED_ALLOCATED_AMT`,
  `TP75_Amt`, `UNAVAILABLE_OH_AMT`, `ALL_OH_AMT`, `WIP_AMT`
- Detail TVF — 21 `_TWD` columns, 26 `*QTY*` columns. **No twin** for `ALLOCATED_AMT`,
  `ALL_OH_AMT`, `UNAVAILABLE_OH_AMT`, `OH_UNRECOVERABLE_AMT`, `OPO_UNRECOVERABLE_AMT`,
  `WIP_AMT`
- **Already TWD despite the name** — `OBSOLETE_OH_TP75_AMT` and `OBSOLETE_OH_ALL_AMT`
  in the detail source have no `_TWD` suffix but hold TWD values. **Do not multiply
  them by `TWD_CURRENCY_RATE`.** Verified 2026-09-15 on the PHP / INR / CNY sites:
  `OBSOLETE_OH_AMT_TWD + OBSOLETE_OH_TP70_AMT_TWD + OBSOLETE_OH_TP75_AMT` equals
  `OBSOLETE_OH_ALL_AMT` exactly, and applying the rate to either side breaks it. The
  item-level "E&O OH + OPO Amt" (`OBSOLETE_OHOPO_AMT_TWD + OBSOLETE_OH_TP70_AMT_TWD +
  OBSOLETE_OH_TP75_AMT`, what `eo_item_diff` sums) reconciles to the summary's
  `OBSOLETE_OHOPO_ALL_AMT_TWD` to the cent. The `eo_detail` tool text still lists both
  as local currency; this entry supersedes it

Detail also carries `DOS_CURRENCY_RATE` alongside `TWD_CURRENCY_RATE`.

### Columns that must not be summed across rows

`INV_EO_UNRECOVERABLE_V` repeats a **BU-level** figure on every customer-group row
of that BU:

| Column | Constant within BU_NO | Row-SUM | True per-BU total | Inflation |
|---|---|---|---|---|
| `RESERVED_AMT` | 37 / 37 BU groups | 5,820,481,268 | 551,391,353 | **10.6x** |
| `BUYDOWN_AMT` | 31 / 37 | 840,916,048 | 658,602,954 | 1.3x |
| `Unrecoverable_AMT` | 16 / 37 — genuinely per row | 380,787,023 | — | safe |
| `OBSOLETE_OHOPO_ALL_AMT` | 1 / 37 — genuinely per row | — | — | safe |

To total the first two, take one value per `BU_NO` (e.g. `MAX` per BU, then sum).
`RESERVED_AMT` can also be **negative**.

### Verified identities

- `Unrecoverable_AMT = OH_unrecoverable + OPO_unrecoverable` — exact on all 354
  rows. Adding the total to its parts double-counts
- `OBSOLETE_OHOPO_ALL_AMT >= Unrecoverable_AMT` always; gross vs a subset, **not additive**
- **No balance / net column exists** in any source. `OBS_ALL − BD − RES` is negative
  on 122/354 rows, `UNREC − BD − RES` on 153/354. Do not derive one — ask the
  business owner for the definition

## Code taxonomies

### `INDICATOR` (detail, red items)

`'R'` or NULL. Computed by the Hub, not stored: within each **Site + PID**, the
top 5 items by `OBSOLETE_OHOPO_AMT_TWD + OBSOLETE_OH_TP70_AMT_TWD`, counting only
items above **10,000 TWD**. 2,493 flagged rows in a 138,986-row period.

### `ACTION_PLAN_OH` / `ACTION_PLAN_OPO` (feedback)

The owner's chosen disposition. Paired with a free-text `_MANUAL` column holding
the human note (often Chinese); only ~11% of rows carry one.

| Code | Meaning |
|---|---|
| `A` | Get buy-down POs from the customer to cover E&O |
| `B-1` / `B-2` / `B-3` | Can be consumed — new demand / new projects / TC |
| `C` | Transfer to another site for consumption |
| `D` | Ship OH to the customer (with customer PO) |
| `E-1` / `E-2` | Move to TP70 / TP75 |
| `F` | Resell to brokers |
| `G-1` | Scrap OH (covered by customer PO or BU reserve) |
| `G-2` | Waiting for MC to provide scrap evidence |
| `O.Others` | Free text carries the real answer |

Values outside this list occur — a part number has been seen typed into the code
field. Do not assume the set is closed.

### Attribution quality markers

**There are no NULLs.** Missing attribution is an **empty string**, so test with
`LTRIM(RTRIM(x)) = ''`, never `IS NULL`. `TBC` appears **only in `BU_NO`**.

| Field | Blank rows | Share |
|---|---|---|
| `CUSTOMERGROUPNAME` | 19,080 | 13.7% |
| `Customer_Number` | 18,365 | 13.2% |
| `PID` | 9,090 | 6.5% |
| `BU_NO` = `TBC` | 1,702 | 1.2% |
| `BU_NO` blank | 8 | — |

**Row share badly understates the problem** because unattributed items skew to
high value. On the same period, SN has 6.5% of rows missing a PID but **54.9% of
its obsolete amount**; SP is 8.3% of rows and 32.1% of amount. Always quote the
amount share (`PCT_OF_SITE_OBSOLETE_AMT`), not the row share.

`CUSTOMER_MISSING` and `CUSTOMERGROUP_MISSING` track each other almost exactly —
the same records are missing both, consistent with customer being dropped when a
BU is corrected by hand.

## Other flags

| Column | Meaning |
|---|---|
| `NCNR_FLAG` | Non-cancellable / non-returnable — cannot be sent back to the supplier |
| `AGING_FLAG` | Aging bucket marker on the detail row |
| `MSIC` | 2-character classification code |
| `Organization_ID` | Numeric site identifier; `Site` is the readable form |
| `AliasProjectNAme` | Alternate project name (note the source's capitalisation) |

## Dimension cardinality

For sizing a query before running it:

| Dimension | Distinct values | Blank |
|---|---|---|
| OU | 7 — DW, OND, SC, SI, SN, SP, TN1 | 0 |
| BU (`BU_NO`) | 52 | 4 |
| CUSTOMER (`CUSTOMERGROUPNAME`) | 140 | 326 rows |
| PROJECT (`ProjectName`) | 615 | 257 rows |
| PID | 753 | 153 rows |

Resolve real values with `eo_dimensions` — codes are not guessable (`BU_NO` looks
like `S3B`, `PID` like `DYT`) and a wrong value returns nothing rather than an error.

## Source objects

| Object | Columns | Notes |
|---|---|---|
| `INV_EO_PID_SUMMARY_F` | 72 | Base table |
| `ufn_INV_EO_PID_Detail_Unrecoverable(date)` | 88 | TVF, one period per call |
| `INV_EO_PID_Detail_Unrecoverable_V` | 88 | Same columns, **all periods**, no parameter. Slow: no index on `Item_No`, so always bound by `Frozen_Date` |
| `INV_EO_UNRECOVERABLE_V` | 11 | Pre-aggregated across sites |
| `INV_EO_PID_DETAIL_Feedback_R` | 16 | 411,539 rows over 15 periods |
| `INV_EO_PID_Summary_Feedback_R` | 14 | **2 rows in total** — effectively unused |
| `ufn_GetMCUnrecoverableBase(date)` | 14 | TVF, not currently exposed as a tool |

Related: [[eo-analysis]] for tool selection and reporting rules,
[[eo-business-rules]] for why attribution changes happen.
````

### 附錄 C — `eo-business-rules`

歸屬邏輯與已知規則變更。解釋為何某個料號的歸屬會改變。

````markdown
---
name: eo-business-rules
description: Why E&O attribution changes — the logic that assigns an item to a BU/PID/customer, known rule changes and their effective dates, and the observed failure patterns behind Issue List disputes. Load when explaining a change point found by eo_item_history, or judging whether an attribution move was system logic or manual intervention.
---

# E&O Attribution Rules

Why an item is booked where it is, and why that changes between periods.
[[eo-issue-investigation]] is the workflow; this is the reasoning it draws on.

**Case in hand:** $ARGUMENTS

> **This document is incomplete.** The rule history below is what is currently
> written down and verified. The root-cause classification distilled from closed
> Issue List cases is **not yet captured here** — see "Missing" at the end.
> Until it is, classify only against the rules stated below and say plainly when
> a case matches none of them. Do not invent a category: this feeds replies sent
> to other departments.

## How attribution works

An E&O row carries three attribution fields, assigned independently and able to
change independently:

| Field | What it identifies |
|---|---|
| `BU_NO` | The division that owns the exposure |
| `PID` | The project the material was bought for |
| `Customer_Number` / `CUSTOMERGROUPNAME` | The end customer behind that project |

They move independently, which is why `eo_item_history` reports `CHANGED_FIELDS`
per field rather than a single "reattributed" flag. A BU change with the PID
unchanged, and a PID change with the BU unchanged, have different causes.

## Known rule changes

Each of these changes attribution for periods after its effective date. When a
change point lands on one of these dates, the rule is the likely cause — but
confirm against the item's own evidence before saying so.

| Effective | Change |
|---|---|
| **2025-08-07** | Attribution basis moved from **Using Assembly** to **Top Item**. Items previously attributed via the assembly that consumed them are attributed to the top-level item instead. Expect BU/PID moves clustered on the first period after this date |

> Further rule changes belong here as they are made, with their effective date
> and the direction of the resulting move. A change without a dated entry here
> is indistinguishable from a data error when someone reads the history months
> later.

## Known failure patterns

Observed behaviours that produce disputed attribution. These are causes, not
categories — the classification table is still missing.

### Manual BU correction drops the customer

When a TBC or misassigned `BU_NO` is corrected by hand, the customer fields are
frequently not updated with it, leaving the row attributed to a BU with no
customer.

**Verified in the data** (2026-08-01): `CUSTOMER_MISSING` and
`CUSTOMERGROUP_MISSING` track each other almost exactly across every site — SN
4,601 vs 4,847 rows, SP 7,240 vs 7,251, SC 4,182 vs 4,638. The same records are
missing both, which is what a dropped customer looks like rather than two
independent gaps.

Signature in `eo_item_history`: `CHANGED_FIELDS` shows `BU_NO` while
`CUSTOMER_NUMBER` goes blank in the same period.

### Orders entered but not written back

Order status `ENTERED` does not push back against the E&O position, so material
can show as excess or obsolete while an order that would consume it exists.

Signature: the amount falls sharply in a later period with no attribution change
and no action plan — the order landed rather than anything being disposed of.

### Excess material parked under TBC

Material with no clear owner is parked with `BU_NO = 'TBC'` pending a decision.
`TBC` appears **only** in `BU_NO` — never in PID or customer — so a blank PID and
a `TBC` BU are separate conditions with separate causes.

**Verified**: 1,702 rows carry `BU_NO = 'TBC'` (1.2% of rows) but they concentrate
value — SP alone holds 1,462 of them at 313.8M TWD, 3.6% of that site's obsolete
amount.

## Reading the value share, not the row share

Unattributed material skews heavily to high value, so row counts understate the
problem by roughly an order of magnitude. On 2026-08-01, SN was **6.5% of rows**
missing a PID but **54.9% of its obsolete amount**; SP 8.3% of rows and 32.1% of
amount.

Any statement about how much E&O is unattributed must quote the amount share.
`eo_data_quality` returns both — use `PCT_OF_SITE_OBSOLETE_AMT`.

## Evidence ranking

When attributing a cause, prefer in this order:

1. **A written instruction** — a note in `eo_feedback` or `eo_feedback_summary`
   naming a transfer or submission document, or a named person's instruction.
   Strongest, and the only kind that settles a dispute
2. **A rule change on the matching date** — a change point landing on a dated
   entry above
3. **A signature match** — the change's shape matches a known failure pattern
4. **Timing alone** — a hypothesis. Label it as one

Nothing above the line? Say the cause is undetermined and name what would settle
it. An open question is recoverable; a wrong cause in a cross-department reply is
not.

## Missing

To be supplied by MC / the Issue List owner:

- **The root-cause classification** used when closing Issue List cases — the
  category names, what distinguishes each, and what evidence closes one. Without
  it, investigations produce a narrative but no consistent classification
- **Further rule-change dates** beyond 2025-08-07
- **The definition of "balance"** in the unrecoverable report. No source column
  provides it and the obvious arithmetic does not hold (`UNREC − BUYDOWN −
  RESERVED` is negative on 153 of 354 rows), so `eo_unrecoverable` deliberately
  derives nothing

Related: [[eo-data-dictionary]] for field semantics, [[eo-analysis]] for the
general reading rules.
````

### 附錄 D — `eo-monthly-review`

月度檢視工作流：趨勢 → 紅燈增減 → 歸屬品質 → 產出報告。

````markdown
---
name: eo-monthly-review
description: Run the monthly E&O review — trend movement, red-flag items added and removed, and attribution quality — and produce a written summary. Use when asked for a monthly or period E&O review, an E&O status report, "what changed this month", or a management summary of excess and obsolete inventory.
---

# E&O Monthly Review

Produce a period review: what moved, which items became critical, and how much of
the number cannot be trusted to its owner.

**Scope (site / BU / period, if given):** $ARGUMENTS

Load [[eo-data-dictionary]] when a column's meaning or safety is unclear;
[[eo-analysis]] carries the general reading rules.

## Step 0 — fix the periods first

```
eo_frozen_dates(periods=0)
```

Pick two: the period under review, and the one to compare against. State both in
the output. Everything downstream is meaningless without them.

Snapshots are **weekly**, so "this month" is 3–5 periods, not one. Two sensible
readings — say which you used:

- **Month-end to month-end** — `eo_trends` already does this, taking the last
  frozen date of each month
- **Latest vs previous week** — the freshest movement, but a week is not a month

If the current month is incomplete its representative period may be only days
after the previous month's. Check `FROZEN_DATE` against `PREV_FROZEN_DATE` in the
trends output before calling any percentage a monthly move.

## Step 1 — trend

```
eo_trends(group_by="TOTAL", months=6)          # the headline series
eo_trends(group_by="OU", months=6)             # where it moved
```

Report `OBSOLETE_OHOPO_AMT_TWD` with `MOM_OBSOLETE_DELTA_TWD` and
`MOM_OBSOLETE_PCT`. Name the sites driving the change rather than only the total.

Drill only where the total moved: `group_by="BU"` with an `ou` filter. Skip
`group_by="PID"` unless investigating something specific — 753 PIDs per period
does not belong in a review.

Excess and obsolete move for different reasons; if `EXCESS_OHOPO_AMT_52W_TWD`
diverges from the obsolete line, say so.

## Step 2 — red-flag items

```
eo_red_items(frozen_date=<current>, compare_date=<previous>, status="ADDED", ou=<site>)
eo_red_items(frozen_date=<current>, compare_date=<previous>, status="REMOVED", ou=<site>)
```

**Pass `ou`.** Without it a two-period diff scans every site (~14s versus ~2s).
Loop over sites rather than running it unfiltered.

`ADDED` is the actionable list — items that became top-5 unrecoverable-obsolete
within their Site+PID this period. Report the largest by
`CUR_UNRECOV_OBSOLETE_TWD` with `BU_NO`, `PID`, `Item_No` and the amount.

`REMOVED` needs care: a row leaving the list is not necessarily stock leaving the
building. It may have been consumed, or another item may simply have outranked it
in the same Site+PID. `CUR_UNRECOV_OBSOLETE_TWD` is 0 on those rows by
construction — do not read that as "written down to zero".

Counts alone are noise (~2,462 flagged rows per period). Lead with amount.

## Step 3 — attribution quality

```
eo_data_quality(frozen_date=<current>)
```

~29 rows, all sites, one call. Report **`PCT_OF_SITE_OBSOLETE_AMT`**, not
`PCT_OF_SITE_ROWS` — they diverge hard because unattributed items skew to high
value, and the row share makes a serious problem look minor.

The `ISSUE` rows overlap (a row missing both PID and customer counts under both),
so they do not sum to `ALL`. Never present them as a breakdown that adds up.

Compare against the previous period by calling it twice — quality trending the
wrong way while the total falls is a finding worth stating on its own.

## Step 4 — what the owners said

Only when step 2 surfaced something worth chasing:

```
eo_feedback(manual_only="Y", ou=<site>)        # omit frozen_date: its periods differ
```

~11% of rows carry a free-text note, and those are the ones with content. Quote
the note next to the item rather than paraphrasing a code.

Feedback periods are **monthly and do not align** with the weekly snapshots —
passing a weekly date returns zero rows. Omit `frozen_date` for its own latest.

## Reporting

Structure:

1. **Headline** — total obsolete for the period, the move versus comparison, both
   dates named
2. **Where it moved** — sites and BUs driving it, with amounts
3. **New critical items** — top `ADDED` red items by amount, with owner feedback
   where it exists
4. **Attribution quality** — amount share unattributed, per site, and whether it
   is improving
5. **What needs a decision** — items with no action plan, or a plan long past
   `ACTION_PLAN_DUE_DATE`

Rules that keep the report honest:

- Every figure carries its frozen period. A number without one is unusable
- Amounts are TWD **except** the columns with no `_TWD` twin — see
  [[eo-data-dictionary]] before mixing them into a total
- Say what you did not check. A review over one site is not a company review
- If a figure came from `scripts/eo_cache.py`, say which period and when it was
  cached
````

### 附錄 E — `eo-issue-investigation`

Issue 追查工作流：定位 → 歷程 → 回覆紀錄 → 判定 → 草擬回覆。

````markdown
---
name: eo-issue-investigation
description: Investigate a single E&O item or PID — why it is attributed to a given BU/PID/customer, when that changed, what the owner said, and draft a reply. Use for MC Issue List questions like "why is this item under X", "when did this move to us", "this is not ours", or disputes about E&O ownership.
---

# E&O Issue Investigation

Trace one item's attribution history and produce an evidence-backed answer.

**Item / PID / question:** $ARGUMENTS

Load [[eo-data-dictionary]] for column meanings, [[eo-business-rules]] for the
attribution logic behind a change.

## What you are actually being asked

Most Issue List questions reduce to one of four, and they need different evidence:

| Question | Evidence |
|---|---|
| "Why is this under my BU?" | The change point where `BU_NO` moved, and any note explaining it |
| "When did this become ours?" | The first period it appears under that BU |
| "This is not ours" | Whether attribution moved by system logic or by hand |
| "Why is this obsolete at all?" | Amount history, not attribution history |

Establish which before pulling data — the last one does not need
`eo_item_history` at all.

## Step 1 — locate the item

If you have an exact `Item_No`, skip ahead. Otherwise narrow first:

```
eo_dimensions(dim="PID", bu=<bu>)                  # find the PID
eo_detail(frozen_date=<latest>, pid=<pid>, ou=<site>)
```

Never guess a code. `BU_NO` looks like `S3B`, `PID` like `DYT`, and a wrong value
returns **zero rows rather than an error** — which reads as "no such problem".

## Step 2 — attribution history

```
eo_item_history(item_no=<item>, ou=<site>, periods=12)
```

**Always pass `ou`.** It is the difference between ~3s and ~35s; the underlying
view has no index on `Item_No`.

Read `IS_CHANGE_POINT` and `CHANGED_FIELDS` newest-first. The distinction in
`CHANGED_FIELDS` matters and is easy to misreport:

| Label | Means |
|---|---|
| `BU_NO` / `PID` / `CUSTOMER` | The **value** changed — a genuine re-attribution |
| `BU_NO_N` / `PID_N` / `CUSTOMER_N` | Only the **number of distinct values** changed |

A count change is not a re-attribution. Saying "the PID changed" when `PID_N` went
2→3 and the value never moved sends the owner looking for something that did not
happen.

`PREV_BU_NO` / `PREV_PID` / `PREV_CUSTOMER_NUMBER` give the before-values, so a
change point reads directly as `was → is`.

When `*_N > 1` the displayed value is the `MIN` of several — the item sits under
more than one BU/PID/customer in that period. Say so rather than presenting one.

Widen with `periods=0` only if no change point appears in the default window.

## Step 3 — what the owner recorded

```
eo_feedback(item_no=<item>, manual_only="Y")       # omit frozen_date
eo_feedback(item_no=<item>)                        # if the above is empty
eo_feedback_summary(pid=<pid>)                     # PID-level MC notes
```

Omit `frozen_date`: feedback periods are **monthly** and do not align with the
weekly snapshots, so a snapshot date returns nothing.

The free-text `_MANUAL` columns carry the real explanation; the coded
`ACTION_PLAN_OH` / `_OPO` values only say what was planned. Quote the note.

`eo_feedback_summary` holds **2 rows in total** — an empty result means nobody
recorded a PID-level note, never that there is no issue. When it does hit, the
content is exactly what this step wants: transfer document numbers and named
instructions to change a BU.

## Step 4 — classify the cause

Match the change point against the known attribution rules in
[[eo-business-rules]]. Anchor on evidence, in this order:

1. A free-text note or document number naming the change — strongest
2. A change point whose shape matches a known rule (e.g. customer dropping to
   blank in the same period a BU was corrected by hand)
3. Timing alone — weakest. State it as a hypothesis, not a cause

If nothing matches, say the cause is undetermined and name what would settle it.
An invented cause is worse than an open question here: it goes into a reply to
another department.

## Step 5 — draft the reply

Structure:

- **Item and scope** — `Item_No`, `Site`, current `BU_NO` / `PID` / customer, and
  the period you read
- **What changed and when** — each change point as `date: was → is`, with the
  `CHANGED_FIELDS` labels read correctly
- **Evidence** — the note, document number or instruction, quoted
- **Assessment** — cause if established, or what is still open
- **Ask** — the specific decision or confirmation needed

Keep it factual. The reply crosses departments; a hedge is cheaper than a
retraction.

## Traps specific to this workflow

- **Zero rows is not an answer.** A non-existent frozen date, a mistyped
  `Item_No`, or a wrong `ou` all return empty. Confirm the item exists in the
  latest period before concluding anything about its history
- **The detail source has no unique key** — ~390 duplicate rows per period.
  `eo_item_history` aggregates per period for you, but raw `eo_detail` does not
- **`REMOVED` from the red list is not disposal.** Another item may simply have
  outranked it within the same Site+PID
- **Amounts:** several columns have no `_TWD` twin and are in local currency. Do
  not add them into a TWD total — see [[eo-data-dictionary]]
````

