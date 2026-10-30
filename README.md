# association-rules-skill

一個 Claude skill，扮演**關聯規則 / 購物籃分析（association rules, market basket analysis）**的
統計顧問，屬於 unsupervised learning。它支援**交易資料**和**問卷資料**，引導使用者完成以下工作：
建立項目集合、先安排驗證、設定門檻、產生規則、評估可靠性、把結果轉成實務解讀，最後輸出一份附程式碼的
分析報告（QMD / HTML）。

📖 **完整導覽**：[`docs/skill-guide.html`](docs/skill-guide.html) 說明整個流程、每個決策和專業術語。
下載後用瀏覽器打開即可閱讀；原始檔是 [`docs/skill-guide.qmd`](docs/skill-guide.qmd)。

## 範圍

- **交易資料**：交易編號、購買品項、時間戳記、數量。支援長格式（一列一品項）、basket 格式
  （一列一籃）與 0/1 寬格式。
- **問卷資料**：一列一位受訪者。答案轉成「題目=答案」項目，處理 codebook 代碼、拒答 / 不知道、
  跳題、連續變數分組（離散化）、次序答案合併、複選題；偵測調查權重並提醒（Tan et al. 6.1、6.2.1）。
- **方法**：Apriori / FP-growth、support、confidence、lift、leverage、conviction、φ、
  null-invariant 指標（cosine、Kulczynski、all-confidence）、h-confidence（cross-support）、
  closed / maximal itemsets、冗餘規則與定義性規則的排除、指定後件 / 前件。
- **可靠性**：時間、日期、隨機或依顧客 / 受訪者分組的 holdout 驗證；Fisher exact test +
  Benjamini–Hochberg FDR；置換檢定；分層檢查 Simpson's paradox。
- **不在範圍內**：序列樣式、多層概念階層探勘、負向樣式、調查權重的加權推論。

## 互動方式：三次確認

| 確認 | 涵蓋步驟 | 一定會問 | 有預設值、可以改 |
|---|---|---|---|
| ① 資料設定 | 1 檢查資料、建立項目集合 | 分析單位、納入欄位、分組與缺失處理 | 分析範圍、項目粒度、次序答案合併 |
| ② 探勘設定 | 2 安排驗證、3 設定門檻 | 是否指定後件、最低支持度、是否保留驗證資料 | 前件限制、confidence、lift、規則長度、演算法、切分方式、驗證標準 |
| ③ 結果檢視 | 4 產生規則、5 評估可靠性、6 解讀與報告 | 怎麼使用結果、要不要回頭調整 | 排序方式、顯示條數、輸出深度 |

- 確認之間自動執行；讀取格式、欄位對應、缺失比例這類技術細節不會拿來問使用者。
- 驗證方式和標準在 ② 就鎖定；看過驗證結果再改設定的話，報告會標示驗證已不再獨立。
- 所有數字都由腳本計算，不心算；畫圖前先問。

## 檔案結構

```
association-rules-skill/
├── SKILL.md                    # 主檔：互動原則 + 三次確認的流程（skill 名稱 association-rules-skill）
├── .gitignore                  # 排除書本 PDF、分析輸出、R / Quarto 暫存檔
├── docs/
│   ├── skill-guide.qmd         # 導覽報告原始檔（流程、決策、術語表）
│   ├── skill-guide.html        # 導覽報告（單一檔案，瀏覽器直接開）
│   └── guide.css
├── references/
│   ├── options.md              # 三次確認的選項表（核心 / 進階 / 預設）與訊息範本
│   ├── survey-data.md          # 問卷資料的五個子步驟、codebook 格式、權重說明
│   ├── decision-tree.md        # 決策節點、Mermaid 流程圖、輸出格式
│   ├── measures.md             # 指標定義、性質（null-invariance 等）、解讀界線
│   ├── validation.md           # 切分、Fisher exact、BH-FDR、置換檢定、分層
│   └── textbook-notes.md       # 教科書章節重點（改寫）+ R arules 對照程式碼
├── scripts/
│   ├── run_arules.py           # 計算腳本（encode / profile / split / mine / validate / permtest / stratify / report）
│   ├── make_sample_data.py     # 產生交易範例資料
│   └── make_sample_survey.py   # 產生問卷範例資料
└── data/
    ├── sample_baskets.csv      # 1,200 筆模擬超市交易（含刻意放入的髒資料）
    ├── sample_survey.csv       # 1,200 位模擬受訪者（含特殊代碼、跳題、複選題、權重）
    ├── sample_codebook.csv     # 問卷代碼表
    └── region_map.csv          # 縣市 → 區域對照表（示範項目粒度）
```

## `scripts/run_arules.py`

```bash
pip install mlxtend scipy pandas numpy matplotlib
```

**交易資料**

```bash
python scripts/run_arules.py profile  data/sample_baskets.csv
python scripts/run_arules.py split    data/sample_baskets.csv --method time --test-frac 0.3
python scripts/run_arules.py mine     arules_output/train.csv --min-support 0.02 --min-confidence 0.3 --min-lift 1.2 --hconf 0.05
python scripts/run_arules.py validate arules_output/test.csv --rules arules_output/rules.csv
python scripts/run_arules.py permtest arules_output/train.csv --min-support 0.02 --min-lift 1.2 --hconf 0.05 --n-perm 50
python scripts/run_arules.py stratify data/sample_baskets.csv --rules arules_output/rules.csv --by weekend
python scripts/run_arules.py report   --notes arules_output/interpretation.md
```

**問卷資料**

```bash
python scripts/run_arules.py encode   data/sample_survey.csv --codebook data/sample_codebook.csv --bins 年齡=18,30,45,65 --multi 關注議題 --exclude 縣市
python scripts/run_arules.py split    arules_output/items.csv --method random
python scripts/run_arules.py mine     arules_output/train.csv --min-count 20 --max-lhs 2 --max-rhs 1 --rhs 政策A態度 政策B態度 --hconf 0.1 --min-lift 1.2
python scripts/run_arules.py validate arules_output/test.csv --rules arules_output/rules.csv --min-test-count 5
python scripts/run_arules.py report   --notes arules_output/interpretation.md --title "政策態度的關聯規則"
```

輸出寫入 `arules_output/`（可用 `--out-dir` 更改）：`items.csv`、`train.csv`、`test.csv`、`rules.csv`、
`excluded_rules.csv`、`validation.csv`、`run_log.json`（每一步的指令與輸出），以及 `report.qmd` /
`report.html`。

**分析報告**：`report` 依 `run_log.json` 整理出每一步做了什麼、當時的輸出，以及可以摺疊的指令與
Python / R 程式碼。有安裝 Quarto（含 RStudio 內建版）就用 Quarto 轉檔，沒有的話由腳本自行產生
同樣可摺疊的 HTML。

腳本以教科書例子做過核對：tea→coffee（N=1000）的 lift = 0.9375，單尾 Fisher p = 0.032；
tea→honey 的 φ = 0.5847，皆與書中數值一致。

## 安裝為 Claude skill

1. 把整個資料夾直接放到 skills 目錄，不用改名：`~/.claude/skills/association-rules-skill/`
   （專案層級則放在 `.claude/skills/association-rules-skill/`）。
2. 安裝 Python 套件：`pip install mlxtend scipy pandas numpy matplotlib`。報告要用 Quarto 轉檔的話，
   另外安裝 [Quarto](https://quarto.org)（RStudio 已內建）；沒有也可以。
3. 觸發方式（兩種都可以）：
   - 直接輸入 `/association-rules-skill`，也可以接著寫需求，例如 `/association-rules-skill 分析 data/sample_survey.csv`；
   - 用自然語言描述需求，例如「幫我分析這份超市交易資料有哪些商品常一起買」或「這份問卷哪些族群的
     政策態度比較特別」，skill 會自動觸發。

## 參考文獻

Tan, P.-N., Steinbach, M., Karpatne, A., & Kumar, V. (2019). *Introduction to Data Mining*
(2nd ed.). Pearson. 第 5 章（Association Analysis: Basic Concepts and Algorithms）、
6.1–6.2 節（Handling Categorical / Continuous Attributes）與 10.4 節（Statistical Testing for
Association Analysis）。
