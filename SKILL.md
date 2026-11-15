---
name: association-rules-skill
description: >
  以繁體中文、依固定流程做關聯規則分析（association rules、market basket analysis、frequent itemset，
  屬於 unsupervised learning）。支援兩種資料：(1) 購物清單（訂單編號＋商品、一列一籃、0/1 矩陣），
  回答「哪些商品適合組成套餐」「買 XX 的人可以推薦什麼」「哪些商品可以放在相近位置」或自由探索；
  (2) 類別問卷（一列一位受訪者），以「欄位=答案」項目回答「哪些回答常一起出現」「哪些條件與某個
  答案有關」「已知某種特徵，還有哪些答案常見」。資料檢查、前處理（購物清單整理、類別問卷編碼）、
  門檻、規則形式與驗證都依固定規則執行，每項排除與轉換都記錄原因與影響筆數，最後輸出 HTML、
  Markdown、Quarto 報告並附完整程式碼。使用者提到 Apriori、FP-growth、support、confidence、lift、
  購物籃、商品組合、交叉銷售、推薦、陳列、問卷答案的關聯，或只說「幫我看這份購物明細／問卷
  有什麼關聯」時，都使用本 skill。
---

# 關聯規則分析

## 任務與交付

依使用者選的分析問題，完成資料檢查、前處理、探勘、驗證與解讀，交付：

- `report.html`（主要報告；程式碼預設摺疊，單一檔案即可開啟）
- `report.md`、`report.qmd`（內容相同）

報告最後附**完整程式碼**、`config.json` 與前處理紀錄（由 `finalize` 自動附上）。

**所有數字都必須由 `scripts/run_arules.py` 產生。** 不得自己寫臨時程式計算報告中的數字，也不得改動程式算出的數字。
使用者的需求超出程式能力時，先說明限制，不自行變通。

## 參考資料

| 檔案 | 何時讀 |
|:--|:--|
| [references/decision-tree.md](references/decision-tree.md) | 流程圖、資料類型判斷規則、前處理固定規則、各問題的規則形式與預設門檻 |
| [references/report-spec.md](references/report-spec.md) | 撰寫報告前：章節、解讀寫法、不能寫的話 |
| [references/measures.md](references/measures.md) | 解讀指標時（support、confidence、lift、cosine、Kulczynski） |
| [references/textbook-notes.md](references/textbook-notes.md) | 使用者問到教科書定義或 R arules 寫法時 |

`docs/` 是給人看的導覽，分析時不需要讀。`data/` 是給使用者自己練習的資料，不要主動拿來分析。

## 互動原則

- 使用繁體中文。
- **每次只問一個問題**，收到回答才問下一個。使用者已經說過的資訊直接沿用，不再問。
  例如使用者說「這是 Groceries，每筆是一張訂單」，就把資料類型當作購物清單、觀測單位當作一張訂單。
- 需要使用者選擇時，有選項式提問工具就用選單，沒有就用編號清單。建議的選項放第一個並標「（建議）」，附一句理由。
- 可以自動**提出判斷**，但**不能只根據欄位名稱就認定資料的意義**。例如數字 1、2、3 可能是問卷選項代碼，
  要有使用者說明或編碼表才能確定。
- 不做以下的事（程式也不會做）：
  - 自行建立連續變數切點
  - 自行合併問卷答案或商品類別
  - 因兩筆觀測內容相同而刪除其中一筆
  - 把缺失、拒答或不適用當成「否」
  - 把 ID、調查權重當成項目
  - 改掉問卷的原欄位名稱或答案
- 資料不符合支援格式時，說明需要怎麼調整，不自行猜測完成。

## 工作流程

指令都在 skill 資料夾執行。`config.json` 由你依使用者的回答建立、更新，每個確認都記在 `confirmations`
（例如 `{"item": "缺失商品值", "answer": "排除"}`）。

### 步驟 1：讀取與檢查

```bash
python scripts/run_arules.py inspect --data <資料檔> --out arules_output [--type shopping|survey] [--unit 一張訂單]
```

使用者已經說明資料類型或觀測單位時，用 `--type`、`--unit` 帶入。

向使用者報告：

1. 資料筆數、欄位，以及少量範例（前 5 列）。
2. 資料類型判斷（購物清單、問卷類別表，或已完成的項目集合），以及判斷依據。
3. 缺失、各欄位的值、可能的 ID 或權重、不支援的數值欄位。
4. 前處理方案。

然後把 `arules_output/config_template.json` 另存為 `config.json`。

### 步驟 2：確認資料類型與前處理（一次一項）

1. **資料類型**（使用者已說明就跳過）。如果是 0/1 矩陣，問清楚 0/1 代表「有買／沒買」還是問卷的「是／否」。
   填入 `data_type`，值是 `shopping` 或 `survey`。
2. 依類型確認前處理，規則見 decision-tree.md 第 3 節：
   - **購物清單整理**
     - 固定規則不必詢問：去除商品名稱前後空白；同一張訂單重複出現的商品記一次。
     - 只有以下情況逐項詢問，答案寫入 `shopping.decisions`：
       - 疑似同義的商品名稱：確認為同一商品的寫入 `item_aliases`。
       - 商品值缺失。
       - 空的訂單。
       - 數量 ≤ 0。
   - **類別問卷編碼**
     - 請使用者選要分析的欄位（`survey.columns`），可多選，預設勾選 inspect 判斷為類別的欄位。
     - 數字代碼欄位：請使用者提供編碼表（填 `codebook`），或確認數字本身就是答案（填 `categorical_as_is`）。
     - 數值欄位：請使用者提供切點（填 `cuts`），否則不選這一欄。
     - 使用者說明哪些值代表拒答、不知道或不適用，填 `missing_values` 或 `missing_codes`，這些值會當成缺失。
3. 執行 `run`（見步驟 5 的指令）。程式在前處理後會停下：
   - **結束代碼 2**：還有事項需要確認。逐項問使用者，寫入 config 後重跑。
     問卷的完整個案分析一定會在這裡停下，列出排除幾筆、剩下幾筆。使用者同意後，
     把列出的數字寫入 `complete_case_confirmed`。
   - **結束代碼 1**：資料或設定不符合支援格式。把程式列出的調整方式告訴使用者，不自行修改資料。

### 步驟 3：選擇分析問題（選單）

- **購物清單**（`question.type`）：
  1. 哪些商品適合組成套餐？（`bundle`）
  2. 買 XX 的人，可以推薦什麼？（`recommend`）：請使用者輸入 XX，填入 `question.item`，名稱要和資料完全相同。
  3. 哪些商品可以考慮放在相近位置？（`placement`）
  4. 我不知道有什麼有趣的搭配，幫我看看（`explore`）
- **問卷**：核心問題是「哪些特徵或回答，經常出現在同一位受訪者身上？」。看過使用者的資料後，再請使用者選：
  1. 哪些回答常一起出現？（`cooccur`）
  2. 哪些條件與某個特定答案有關？（`target`）：從資料中的「欄位=答案」列出選項，請使用者選一個，填入 `question.target`。
  3. 已知某種特徵，還有哪些答案常見？（`given`）：請使用者選特徵，可以多個，填入 `question.given`。

使用者在對話一開始就已經說出問題時，直接對應，不再問。

### 步驟 4：確認門檻

依 decision-tree.md 第 4 節的固定公式算出預設值，用一張表給使用者看：最低共同出現筆數、confidence、lift，
以及驗證方式。使用者可以全部接受，或修改其中幾項。只有修改的項目才寫入 `params`，沒寫的就用預設規則。

### 步驟 5：執行

```bash
python scripts/run_arules.py run --config config.json
```

程式依分析問題的固定規則形式探勘，並在驗證資料上檢驗。輸出：`results.json`、`results_full.csv`、`report_draft.md`。

- 沒有結果時，依固定規則只提一個選項：把最低共同出現筆數降到 10 再跑一次，由使用者決定。
- 推薦的商品或指定的答案在資料中找不到時，程式會列出相近名稱，請使用者確認正確名稱，不自行替換。
- 程式報錯時如實告知，不更改資料來繞過。

### 步驟 6：撰寫解讀並完成報告

1. 讀 `references/report-spec.md`。
2. 把 `report_draft.md` 另存為 `arules_output/report.md`，把每一處 `【待填：…】` 換成內容。數字一律取自草稿的表格或 `results.json`。
3. 執行：

```bash
python scripts/run_arules.py finalize --config config.json --report arules_output/report.md
```

程式會檢查沒有未填寫處，附上重現方式、config 與完整程式碼，輸出 `report.md`、`report.qmd`、`report.html`。
結束代碼 0 才算完成。最後把 `report.html` 的路徑告訴使用者，並用一段話摘要結論。

## config.json 欄位

| 欄位 | 說明 |
|:--|:--|
| `data_path`、`encoding`、`out_dir` | 資料路徑、CSV 編碼（null 表示自動）、輸出資料夾 |
| `data_type` | `shopping` 或 `survey`（經使用者確認） |
| `unit` | 觀測單位，例如「一張訂單」「一位受訪者」 |
| `shopping.format` | `long`（一列一個商品）、`basket`（一列一筆、商品以 `sep` 分隔）、`wide01`（0/1 矩陣） |
| `shopping.tid_col`、`item_col`、`item_cols`、`sep`、`time_col`、`qty_col` | 欄位角色；`time_col` 只用來依時間切驗證資料，`qty_col` 只用來檢查數量 ≤ 0 |
| `shopping.decisions` | `missing_item: "exclude"`、`empty_baskets: "exclude"/"keep"`、`nonpositive_qty: "exclude"/"keep"`、`synonyms: "reviewed"` |
| `shopping.item_aliases` | 使用者確認為同一商品的名稱對照 `{"原名": "統一名稱"}` |
| `survey.format` | `wide`（一列一位受訪者）或 `long_kv`（一列一個「欄位=答案」） |
| `survey.id_col`、`exclude_cols` | 受訪者編號；不當成項目的欄位與原因 `[{"name", "reason"}]` |
| `survey.columns` | 使用者選的分析欄位 |
| `survey.missing_values`、`missing_codes` | 使用者說明代表缺失的值（全部欄位共用；或 `{"欄位": ["99"]}`） |
| `survey.codebook` | 使用者提供的編碼表 `{"欄位": {"1": "男", "2": "女"}}` |
| `survey.categorical_as_is` | 使用者確認「數字本身就是答案」的欄位 |
| `survey.cuts` | 使用者提供的切點 `{"欄位": {"breaks": [0, 18, 65, 200], "labels": [...], "right": false}}` |
| `survey.complete_case_confirmed` | 使用者同意的完整個案分析筆數 `{"excluded": n, "remaining": m}` |
| `question` | `type`；`item`（recommend）、`target`（target）、`given`（given） |
| `params` | 使用者修改過的門檻：`min_count`、`min_confidence`、`min_lift`；沒寫的用預設規則 |
| `validation` | `enabled`（預設 true）、`seed`（預設 2026）、`alpha`（預設 0.05） |
| `report` | `title`、`background`、`goal` |
| `confirmations` | 使用者確認紀錄 |
