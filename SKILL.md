---
name: association-rules-skill
description: >
  以繁體中文、依固定流程做關聯規則分析（association rules、market basket analysis、frequent itemset、
  Apriori、FP-growth，屬於 unsupervised learning）。支援兩種資料：(1) 購物資料（交易明細長表、一列一筆的購物清單、
  0/1 矩陣），項目是商品；(2) 類別型問卷（一列一位受訪者），項目是「欄位=類別」。分析目標有三種：
  自由探索、指定前件（例如「買牛奶的人還買什麼」）、指定後件（例如「哪些特徵與網購=是一起出現」）。
  前處理只做已定義、可重現的機械性操作，涉及資料意義的事一次一項詢問使用者；門檻由使用者確認並保留
  設定紀錄；結果為描述性、探索性。最後輸出 HTML、Markdown、Quarto 報告並附完整程式碼。使用者提到 Apriori、
  FP-growth、support、confidence、lift、購物籃、商品組合、交叉銷售、問卷答案的關聯，或只說「幫我看
  這份購物明細／問卷有什麼關聯」時，都使用本 skill。
---

# 關聯規則分析

本 skill 實作 `docs/skill-guide.html`（分析指南）第 8 章的流程與操作規格。

## 任務與交付

依使用者的分析目標完成資料檢查、前處理、規則產生與篩選、解讀，交付：

- `report.html`（主要報告；程式碼預設摺疊，單一檔案即可開啟）
- `report.md`、`report.qmd`（內容相同）

報告最後附**完整程式碼**、`config.json`、前處理紀錄與設定紀錄（由 `finalize` 自動附上）。

**所有數字都必須由 `scripts/run_arules.py` 產生。** 不得自己寫臨時程式計算報告中的數字，也不得改動程式算出的數字。
使用者的需求超出程式能力時，先說明限制，不自行變通。

## 參考資料

| 檔案 | 何時讀 |
|:--|:--|
| [references/decision-tree.md](references/decision-tree.md) | 流程、格式判斷、前處理規則、參數與篩選、停止條件 |
| [references/report-spec.md](references/report-spec.md) | 撰寫報告前：章節、解讀寫法、不能寫的話 |
| [references/measures.md](references/measures.md) | 解讀指標時 |
| [references/textbook-notes.md](references/textbook-notes.md) | 使用者問到教科書內容或 R arules 寫法時 |

`docs/` 是給人看的分析指南，分析時不需要讀。`data/` 是給使用者自己練習的資料，不要主動拿來分析。

## 適用範圍

- **支援**：購物資料、類別型問卷（含已分組的年齡層、所得區間）。
- **不支援**：
  - 原始數值變數的分箱（第一版不自動分箱）
  - 複選題
  - 加權推論
  - 序列樣式
  - 分類模型或預測準確率

  遇到時說明需要的調整並停止。

## 互動原則

- 使用繁體中文。
- **每次只問一個問題**，收到回答才問下一個。**使用者已經指定的設定不重複詢問**。
  例如使用者說「這是 Groceries，每筆是一張收銀交易」，資料類型與 `unit` 就直接沿用。
- 需要使用者選擇時，有選項式提問工具就用選單，沒有就用編號清單。建議的選項放第一個並標「（建議）」，附一句理由。
- 自動處理已明確定義、可重現的機械性操作。涉及資料語意、分析單位、欄位選擇或刪除策略時，
  若現有設定不足以決定，才詢問使用者。
- 可以**提出判斷**，但不能只根據欄位名稱認定資料意義。數字 1、2、3 可能是類別代碼，要有資料字典或使用者說明。
- 不做以下的事（程式也不會做）：
  - 默默刪除資料
  - 自動分箱
  - 擅自合併類別或商品
  - 刪除內容相同的不同觀測
  - 把缺失、拒答、不知道、不適用當成「否」或一般答案
  - 把 ID、權重當成項目
  - 產生「未購買」項目
  - 不經同意就調整門檻
- 示範設定只是起點，**不得描述成通用標準**。所有支持度都同時說出對應的最低筆數。

## 工作流程

指令在 skill 資料夾執行。`config.json` 由你依使用者的回答建立、更新。

### 步驟 1：讀取與檢查

```bash
python scripts/run_arules.py inspect --data <資料檔> --out arules_output [--type shopping|survey] [--unit 一張發票]
```

向使用者報告：

1. 筆數、欄位、前 5 列。
2. 格式判斷與依據。
3. 缺失、ID 或權重候選、數字欄位。
4. 前處理方案。

然後把 `arules_output/config_template.json` 另存為 `config.json`。

### 步驟 2：確認資料類型、一筆觀測與前處理（一次一項）

1. **資料類型**：填 `data_type`（`shopping` 或 `survey`）。0/1 矩陣要先問 0/1 的意義。
2. **一筆觀測的定義**：購物資料要確認交易 ID 代表一張發票、一位顧客，還是一位顧客的一天，填 `unit`。
3. **前處理**：規則見 decision-tree.md 第 3 節。
   - **購物**：固定規則不問。只有以下情況才問，答案寫入 `shopping.decisions`：
     - 空白的商品名稱
     - 疑似同義名稱：確認是同一商品的才寫入 `item_aliases`
     - 數量 ≤ 0
     - 空的交易
   - **問卷**：
     - 有資料字典就填 `survey.dictionary_path`。資料字典是 CSV，欄位為「欄位、原始值、標籤、類別」，類別為答案、缺失或 ID。
     - 沒有資料字典時，數字代碼欄位要請使用者說明，寫入 `codebook`、`missing_codes`，或確認數字本身就是答案（`categorical_as_is`）。
     - 請使用者選分析欄位，填 `survey.columns`。
4. 執行 `run`（指令見步驟 5）。程式會依序在需要確認的地方停下：
   - **結束代碼 2**：有事項要確認，包括一筆觀測的定義、前處理決定、完整案例分析的排除人數、分析目標、分析參數。
     問使用者後寫入 config，再重跑。
   - **結束代碼 1**：資料或設定不符合支援格式。把程式列出的調整方式告訴使用者，不自行修改資料。
   - 完整案例分析停下時，告訴使用者排除與剩下的人數，以及可能的偏誤。同意後把列出的數字寫入
     `complete_case_confirmed`；不同意則說明第一版無法分析。

### 步驟 3：選擇分析目標

使用者沒說時才問（`question.goal`）：

1. 自由探索（`free`）：沒有特定問題，想看資料中有哪些組合。
2. 指定前件（`lhs`）：例如「買牛奶的人還買什麼」。項目寫入 `question.items`，名稱必須和資料完全相同。
3. 指定後件（`rhs`）：例如「哪些特徵與『網購=是』一起出現」。一個項目寫入 `question.items`。

把使用者原本的問題寫入 `question.label`。

### 步驟 4：確認參數

`run` 第一次走到這裡會停下，列出三樣東西：

- 示範設定與理由：演算法、最低支持度與對應筆數、confidence、lift、前件長度、最低前件筆數。
- 只改最低支持度時的規則數表。
- 只改 confidence 時的規則數表。

把這些給使用者看，強調**示範設定不是通用標準**。演算法請使用者選 Apriori 或 FP-growth（`params.algorithm` = `apriori`／`fpgrowth`），並說明兩者找到的頻繁項目集完全相同、只差速度（指定前件時不影響結果）。使用者確認或修改後，把值寫入 `params`，並設：
- `"confirmed": true`
- `"reason"`：使用者的理由

### 步驟 5：執行

```bash
python scripts/run_arules.py run --config config.json
```

程式依目標產生規則，並依序篩選：最低筆數 → 最低前件筆數 → confidence → lift → 冗餘規則，每一步都記錄剩下幾條。
每次執行的設定與規則數都寫入 `settings_log.json`。

- **沒有規則，或規則多到無法逐條檢視**：說明原因，提出一個放寬或收斂方案。經使用者同意才修改 `params`，
  並更新 `reason`。設定紀錄會保留每一次。使用者不同意調整時，如實回報「沒有規則」。
- **指定的項目找不到**：程式會列出相近名稱，請使用者確認，不自行替換。
- **不做保留資料驗證或統計檢定**：結果是對這份資料的描述，屬探索性。使用者問到時說明第一版不支援。

### 步驟 6：撰寫解讀並完成報告

1. 讀 `references/report-spec.md`。
2. 把 `report_draft.md` 另存為 `arules_output/report.md`，把每一處 `【待填：…】` 換成內容。數字一律取自草稿表格或 `results.json`。
3. 執行：

```bash
python scripts/run_arules.py finalize --config config.json --report arules_output/report.md
```

結束代碼 0 才算完成。最後把 `report.html` 的路徑告訴使用者，並用一段話摘要結論。

## config.json 欄位

| 欄位 | 說明 |
|:--|:--|
| `data_path`、`encoding`、`out_dir` | 資料路徑、CSV 編碼（null 表示自動）、輸出資料夾 |
| `data_type` | `shopping` 或 `survey`（經使用者確認） |
| `unit` | 一筆觀測的定義，例如「一張發票」「一位受訪者」 |
| `shopping.format` | `long`（交易明細長表）、`basket`（購物清單，商品以 `sep` 分隔）、`wide01`（0/1 矩陣） |
| `shopping.tid_col`、`item_col`、`item_cols`、`sep`、`qty_col` | 欄位角色；`qty_col` 只用來檢查數量 ≤ 0；時間欄位不使用 |
| `shopping.decisions` | `missing_item: "exclude"`、`empty_baskets: "exclude"/"keep"`、`nonpositive_qty: "exclude"/"keep"`、`synonyms: "reviewed"` |
| `shopping.item_aliases` | 使用者確認為同一商品的名稱對照 `{"原名": "統一名稱"}` |
| `survey.format` | `wide`（一列一位受訪者）或 `long_kv`（一列一個「欄位=類別」） |
| `survey.id_col`、`exclude_cols` | ID；不納入規則的欄位與原因 `[{"name", "reason"}]` |
| `survey.columns` | 使用者選的分析欄位 |
| `survey.dictionary_path` | 資料字典 CSV（欄位、原始值、標籤、類別） |
| `survey.codebook`、`missing_codes`、`missing_values` | 沒有資料字典時，使用者說明的代碼與缺失代碼 |
| `survey.categorical_as_is` | 使用者確認「數字本身就是答案」的欄位 |
| `survey.complete_case_confirmed` | 使用者同意的完整案例分析人數 `{"excluded": n, "remaining": m}` |
| `question` | `goal`（`free`／`lhs`／`rhs`）、`items`、`label`（使用者的原始問題） |
| `params` | `algorithm`（`apriori`／`fpgrowth`）、`min_support`、`min_confidence`、`min_lift`、`max_lhs`、`min_lhs_count`、`confirmed`、`reason` |
| `report` | `title`、`background` |
