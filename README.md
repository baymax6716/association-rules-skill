# association-rules-skill

一個 Claude skill，依**固定流程**做關聯規則分析（association rules、market basket analysis，屬於
unsupervised learning）。它讀取使用者的購物清單或類別問卷，逐項確認前處理，請使用者從固定的分析問題中
選一個，再依固定的規則形式、門檻與驗證方式執行，最後產出 HTML、Markdown 與 Quarto 報告，並附完整程式碼。

所有數字都由同一支程式 `scripts/run_arules.py` 依 `config.json` 算出，AI 只負責和使用者溝通、記錄確認與撰寫解讀。
同一份資料、同樣的設定與套件版本，會得到相同的結果。

📖 **導覽**：[`docs/skill-guide.html`](docs/skill-guide.html) 說明流程、每個決策與專業術語（原始檔 [`docs/skill-guide.qmd`](docs/skill-guide.qmd)）。

## 支援的資料與問題

| 資料 | 前處理 | 可以選的問題 |
|---|---|---|
| 購物清單：長格式（訂單編號＋商品）、一列一籃、0/1 矩陣 | 購物清單整理 | 哪些商品適合組成套餐？／買 XX 的人可以推薦什麼？／哪些商品可以放在相近位置？／我不知道，幫我看看 |
| 類別問卷：一列一位受訪者 | 類別問卷編碼（「欄位=答案」、完整個案分析） | 哪些回答常一起出現？／哪些條件與某個特定答案有關？／已知某種特徵，還有哪些答案常見？ |

不支援：複選題、加權分析、序列樣式。遇到時 skill 會說明需要的調整並停止。

## 流程

1. **讀取與檢查**：筆數、欄位、範例；資料類型判斷與依據；缺失、ID、權重、數值欄位；前處理方案。
2. **確認資料類型與前處理**：一次問一項；固定規則不問，只問需要使用者決定的事。
3. **選擇分析問題**。
4. **確認門檻**：固定公式算出預設值，可以修改。
5. **執行**：隨機或依時間切出 30% 驗證資料，探勘後以 Fisher exact test ＋ Benjamini–Hochberg 檢驗是否重現。
6. **解讀與報告**：附前處理紀錄（原因與影響筆數）、config 與完整程式碼。

skill 不會自行建立切點、不會合併答案或商品、不會刪除內容相同的觀測、不會把缺失當成「否」、不會把 ID 或權重當成項目。

## 檔案結構

```
association-rules-skill/
├── SKILL.md                    給 AI 的流程與規則
├── README.md                   本說明
├── references/
│   ├── decision-tree.md        流程圖、類型判斷規則、前處理固定規則、規則形式與預設門檻、驗證
│   ├── report-spec.md          報告章節與解讀寫法
│   ├── measures.md             指標定義與解讀界線
│   └── textbook-notes.md       教科書章節重點與 R arules 對照程式碼
├── scripts/
│   └── run_arules.py           主程式：inspect / run / finalize
├── docs/                       給人看的導覽（skill-guide.qmd / .html）
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
| `report_draft.md` | 數字已填好、解讀待填的草稿 |
| `report.html`、`report.md`、`report.qmd` | 最終報告 |

## 限制

- 關聯規則描述共同出現，不代表因果。
- 結果依賴門檻與前處理；完整個案分析在缺失不是隨機發生時可能造成偏誤。
- 未使用調查權重，問卷結果描述的是樣本，不是母體。

## 參考文獻

Tan, P.-N., Steinbach, M., Karpatne, A., & Kumar, V. (2019). *Introduction to Data Mining*
(2nd ed.). Pearson. 第 5 章、6.1–6.2 節與 10.4 節。
