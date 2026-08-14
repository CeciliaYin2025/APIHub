# E&O MCP 服務與 Skill 安裝指南

給需要用 AI 查詢 E&O 資料的使用者。裝完之後，你可以直接用自然語言問
「SP 站上一期的紅燈料號比前一期多了哪些？」而不必開報表撈資料。

本文的端點與前置條件都是在 PRD 上實測過的，不是照抄設定檔。

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

`eo_reader` 的權限是 `query:eo_*`，只看得到 12 個 E&O 定義，看不到 HR/RD 的任何資料。
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

---

## 2. 安裝 eo-analysis Skill

MCP 給的是**資料**，Skill 給的是**怎麼讀這份資料**——哪些欄位不能直接加總、
哪些百分比會誤導、追查歸屬要照什麼順序。沒有它，AI 拿到正確的數字仍可能講出
錯誤的結論。

### Claude Code

複製整個 skill 目錄到其中一個位置：

```bash
# 個人用（所有專案都生效）
mkdir -p ~/.claude/skills
cp -r .claude/skills/eo-analysis ~/.claude/skills/

# 或專案用（只在這個 repo 生效，且可進版控與同事共用）
# 本 repo 已內含 .claude/skills/eo-analysis，不需要額外動作
```

驗證：在 Claude Code 輸入 `/eo-analysis`，或直接問一個 E&O 問題，它應該會先
呼叫 `eo_frozen_dates` 確認期別再往下查。

### 其他 client（Hermes 等）

Skill 是純 Markdown（`SKILL.md`），不依賴任何 Claude Code 專屬功能。若你的
client 不支援 skill 機制，把內容當成系統提示、自訂指令或知識文件貼進去，效果
相同。

**全文收錄在本文結尾的〈附錄 A〉**，可直接複製，不需要另外取得 repo。連同開頭
的 `---` frontmatter 一起複製 —— 存成 `SKILL.md` 使用時那段是必要的；當成純提示
詞貼上時可以省略。

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
那種量不屬於快取，也不屬於 AI 的上下文。

> 這一節需要 API Key 或 JWT。若你是純 SSO 使用者（沒有 API Key），MCP 工具
> 本身已經可以正常使用，只是無法使用這個快取腳本；需要的話請管理者核發一把
> 服務用 API Key。

---

## 4. 可用的工具

裝好之後你會看到 12 個 `query_dw_eo_*` 工具。不需要背，AI 會自己挑，但知道有
哪些能幫你問得更精準：

| 工具 | 回答什麼 |
|---|---|
| `eo_frozen_dates` / `eo_max_frozen_date` | 有哪些期別 / 最新一期 |
| `eo_dimensions` | OU / BU / PID / 客戶 / 專案 的有效代碼 |
| `eo_summary` | 依 OU+BU+PID+客戶群的彙總金額 |
| `eo_detail` | 料號級明細 |
| `eo_red_items` | 紅燈料號，可比較兩期新增／移除 |
| `eo_item_history` | 某料號的歸屬異動軌跡與異動時點 |
| `eo_trends` | 逐月趨勢與月變動 |
| `eo_data_quality` | 無 PID／無客戶／BU 為 TBC 的金額與占比 |
| `eo_unrecoverable` | Unrecoverable、Buy Down、Reserved |
| `eo_feedback` | 料號級 Action Plan 與人工註記 |
| `eo_feedback_summary` | PID 級 MC 註記 |

### 三件最容易問錯的事

**期別是「週」不是「月」。** 快照是每週四加上月底補一筆，不是月結。問「上個月」
時請說清楚是哪一期，或讓 AI 先查 `eo_frozen_dates`。傳入一個不存在的凍結日
**會回傳零筆而不是錯誤**，看起來就像「沒有 E&O」。

**代碼不好猜。** `BU_NO` 長得像 `S3B`、`PID` 長得像 `DYT`。猜錯一樣是靜默回零筆。
先問「有哪些 BU？」讓 AI 呼叫 `eo_dimensions`。

**數字要看金額占比，不是列數占比。** 未歸屬的料號集中在高金額品項，例如 SN 站
只有 6.5% 的列沒有 PID，卻占了 54.9% 的呆滯金額。只看列數會嚴重低估問題。

---

## 相關文件

- `README.md` — API Hub 總覽
- `docs/system_specification.md` — 系統規格
- `deploy/apihub/README.md` — 伺服器端部署與 OAuth 設定
- `.claude/skills/eo-analysis/SKILL.md` — skill 本體（全文見下方附錄 A）
- `.claude/skills/new-sql-definition/SKILL.md` — 新增 SQL 定義的規則

---

## 附錄 A：eo-analysis skill 全文

以下是 `.claude/skills/eo-analysis/SKILL.md` 的完整內容，供無法取得 repo 或
client 不支援 skill 機制的使用者直接複製。

> **這份文件是副本。** 唯一有效的版本是 repo 裡的 `.claude/skills/eo-analysis/SKILL.md`；
> 該檔案更新時，這份附錄必須一併更新，否則兩邊會漂移。

````markdown
---
name: eo-analysis
description: Query and analyse Sercomm E&O (Excess & Obsolete) inventory data through the API Hub MCP tools, with local caching of closed frozen periods. Use for any question about E&O amounts, obsolete/excess stock, red-flag items, item attribution (why an item sits under a BU/PID/customer), Action Plan feedback, or E&O trends.
---

# E&O Analysis

Answer E&O questions using the twelve `query_dw_eo_*` MCP tools, reusing locally
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
| Why is this item attributed to X? When did it move? | `eo_item_history` |
| How is E&O trending month to month? | `eo_trends` |
| How much E&O has no PID / no customer / a TBC BU? | `eo_data_quality` |
| Unrecoverable with buy-down and reserve | `eo_unrecoverable` |
| What did the owner say about this item? | `eo_feedback` |
| PID-level MC highlight notes | `eo_feedback_summary` |

Always resolve codes with `eo_dimensions` before filtering. `BU_NO` looks like
`S3B`, `PID` like `DYT` — a guessed value silently returns nothing.

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
