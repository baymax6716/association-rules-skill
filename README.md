# association-rules-skill

一個 Claude skill，依分析指南（`docs/skill-guide.html`）第 8 章的流程做關聯規則分析（association rules、
Apriori，屬於 unsupervised learning）。它讀取購物資料或類別型問卷，自動做已定義、可重現的前處理，涉及資料意義的事
一次一項詢問使用者；依使用者選的分析目標（自由探索、指定前件、指定後件）與確認過的參數產生規則，保留設定紀錄，
最後產出 HTML、Markdown 與 Quarto 報告，並附完整程式碼。

所有數字都由同一支程式 `scripts/run_arules.py` 依 `config.json` 算出，AI 只負責和使用者溝通、記錄確認與撰寫解讀。
同一份資料、同樣的設定與套件版本，會得到相同的結果。

📖 **分析指南**：[`docs/skill-guide.html`](docs/skill-guide.html)（原始檔 [`docs/skill-guide.qmd`](docs/skill-guide.qmd)）說明基本觀念、兩種資料的前處理、參數設定、規則解讀、可手算的完整示範，以及決策流程圖與操作規格。指南中的範例數字都由 [`docs/examples/build_guide_examples.py`](docs/examples/build_guide_examples.py) 產生，並與 mlxtend、R arules 交叉核對。

## 支援的資料與分析目標

| 資料 | 一筆觀測 | 項目 |
|---|---|---|
| 購物資料：交易明細長表、購物清單（一列一筆）、0/1 矩陣 | 一筆交易（ID 的意義由使用者確認） | 商品 |
| 類別型問卷：一列一位受訪者（可搭配資料字典） | 一位受訪者 | 「欄位=類別」，例如 `網購=否` |

分析目標：**自由探索**、**指定前件**（買牛奶的人還買什麼）、**指定後件**（哪些特徵與「網購=是」一起出現）。

不支援：原始數值變數的分箱、複選題、加權推論、序列樣式。遇到時 skill 會說明需要的調整並停止。

## 流程

1. **讀取與檢查**：筆數、欄位、前 5 列；格式判斷與依據；缺失、ID、權重、數字欄位；前處理方案。
2. **確認資料類型、一筆觀測與前處理**：一次問一項；機械性操作自動執行並記錄筆數。
3. **選擇分析目標**。
4. **確認參數**：程式列出示範設定（不是通用標準）與「只改一個參數時的規則數」，使用者確認或修改。
5. **產生與篩選規則**：依序篩選並記錄；每次執行的設定都寫入設定紀錄。保留資料驗證為選用。
6. **解讀與報告**：附前處理紀錄、設定紀錄、config 與完整程式碼。

skill 不會默默刪資料、不自動分箱、不合併類別、不刪除內容相同的觀測、不把缺失當成「否」、不把 ID 或權重當成項目、
不產生「未購買」項目，也不會未經同意調整門檻。

## 檔案結構

```
association-rules-skill/
├── SKILL.md                    給 AI 的流程與規則
├── README.md                   本說明
├── references/
│   ├── decision-tree.md        流程、格式判斷、前處理規則、參數與篩選順序、停止條件
│   ├── report-spec.md          報告章節與解讀寫法
│   ├── measures.md             指標定義與解讀界線
│   └── textbook-notes.md       教科書章節重點與 R arules 對照程式碼
├── scripts/
│   └── run_arules.py           主程式：inspect / run / finalize
├── docs/
│   ├── skill-guide.qmd / .html 分析指南（原始檔與可閱讀的 HTML）
│   ├── guide.css
│   └── examples/               指南的範例資料、計算程式、R 核對程式與產生的表格
└── data/                       兩個練習資料集（Groceries、IncomeESL），見 data/README.md
```

## 安裝

```bash
pip install pandas numpy mlxtend scipy
```

報告的 HTML 有安裝 [Quarto](https://quarto.org)（RStudio 已內建）時用 Quarto 轉檔，沒有時由程式自行產生，一樣可以摺疊程式碼。

## 使用方式

**Claude Code**：把資料夾放到 `~/.claude/skills/association-rules-skill/`（專案層級為 `.claude/skills/association-rules-skill/`），
然後輸入 `/association-rules-skill`，或直接描述需求，例如「幫我看這份訂單資料，買牛奶的人可以推薦什麼」。

**終端機**：

```bash
python scripts/run_arules.py inspect --data <資料.csv> --out arules_output
python scripts/run_arules.py run --config config.json
python scripts/run_arules.py finalize --config config.json --report arules_output/report.md
```

| 輸出 | 內容 |
|---|---|
| `inspect.json`、`config_template.json` | 檢查結果與設定範本 |
| `status.json` | `run` 停下時的待確認事項或格式問題 |
| `results.json`、`results_full.csv` | 報告用到的全部數字、完整結果 |
| `settings_log.json` | 每次執行的設定與保留規則數 |
| `report_draft.md` | 數字已填好、解讀待填的草稿 |
| `report.html`、`report.md`、`report.qmd` | 最終報告 |

## 限制

- 關聯規則描述共同出現，不代表因果。
- 結果依賴門檻與前處理；完整案例分析在缺失不是隨機發生時可能造成偏誤。
- 未使用調查權重，問卷結果描述的是樣本，不是母體。

## 參考文獻

Tan, P.-N., Steinbach, M., Karpatne, A., & Kumar, V. (2019). *Introduction to Data Mining*
(2nd ed.). Pearson. 第 5 章、6.1–6.2 節與 10.4 節。
