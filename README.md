# association-rules-skill

一個 Claude skill，扮演**關聯規則 / 購物籃分析（association rules, market basket analysis）**的
統計顧問，屬於 unsupervised learning。它引導使用者在超市式交易資料上完成以下工作：
建立購物籃、先安排驗證、設定門檻、產生規則、評估可靠性，最後把結果轉成實務解讀。
skill 會解釋每一步的統計理由，並且一次只做一步。

## 範圍

- **輸入**：交易資料，包括交易編號（Transaction ID）、購買品項（Item）、時間戳記、數量。
  支援長格式（一列一品項）、basket 格式（一列一籃）與 0/1 寬格式。
- **方法**：Apriori / FP-growth、support、confidence、lift、leverage、conviction、φ、
  null-invariant 指標（cosine、Kulczynski、all-confidence）、h-confidence（cross-support）、
  closed / maximal itemsets，以及冗餘規則移除。
- **可靠性**：時間或隨機的 holdout 驗證、Fisher exact test + Benjamini–Hochberg FDR、
  置換檢定（permutation），以及分層檢查 Simpson's paradox。
- **不在範圍內**：序列樣式（sequential patterns）、連續變數離散化、商品階層、負向樣式。

## 工作流程

| 步驟 | 內容 | 輸出 |
|---|---|---|
| 1 | 檢查資料並建立購物籃 | 清理紀錄、交易數、商品數、購物籃大小分布、商品頻率 |
| 2 | 先安排驗證，再開始探勘 | 切分方式、理由、可支持的結論範圍 |
| 3 | 設定門檻與演算法 | 參數表與每個參數的理由（使用者可覆寫） |
| 4 | 產生規則，處理「太多」或「沒有」 | 候選規則表、被排除規則與原因 |
| 5 | 評估可靠性 | 驗證集重現率、q 值、置換檢定、分層一致性 |
| 6 | 轉成實務解讀 | 白話規則、行動建議與限制（共現 ≠ 因果） |

## 檔案結構

```
association-rules-skill/
├── SKILL.md                    # 主檔：行為限制 + 步驟 1–6
├── references/
│   ├── decision-tree.md        # 各步驟決策節點、Mermaid 流程圖、輸出格式
│   ├── measures.md             # 指標定義、性質（null-invariance 等）、解讀界線
│   ├── validation.md           # 切分、Fisher exact、BH-FDR、置換檢定、分層
│   └── textbook-notes.md       # 教科書章節重點（改寫）+ R arules 對照程式碼
├── scripts/
│   ├── run_arules.py           # 計算腳本（profile / split / mine / validate / permtest / stratify）
│   └── make_sample_data.py     # 產生示範資料
└── data/
    └── sample_baskets.csv      # 1,200 筆模擬超市交易（含刻意放入的髒資料）
```

## Agent 的行為方式

- 一次只做一步，回報輸出後等使用者決定是否繼續。
- 未經同意不探勘、不檢定；畫圖前先問。
- 參數只提建議並附理由，由使用者決定最後的值。
- 所有數字都由腳本計算，不心算。

## `scripts/run_arules.py`

```bash
pip install mlxtend scipy pandas numpy matplotlib

python scripts/run_arules.py profile  data/sample_baskets.csv
python scripts/run_arules.py split    data/sample_baskets.csv --method time --test-frac 0.3
python scripts/run_arules.py mine     arules_output/train.csv --min-support 0.02 --min-confidence 0.3 --min-lift 1.2 --hconf 0.05
python scripts/run_arules.py validate arules_output/test.csv --rules arules_output/rules.csv
python scripts/run_arules.py permtest arules_output/train.csv --min-support 0.02 --min-lift 1.2 --hconf 0.05 --n-perm 50
python scripts/run_arules.py stratify data/sample_baskets.csv --rules arules_output/rules.csv --by weekend
```

輸出寫入 `arules_output/`（可用 `--out-dir` 更改）：`train.csv`、`test.csv`、`rules.csv`、
`excluded_rules.csv`、`validation.csv`。

腳本以教科書例子做過核對：tea→coffee（N=1000）的 lift = 0.9375，單尾 Fisher p = 0.032；
tea→honey 的 φ = 0.5847，皆與書中數值一致。

## 安裝為 Claude skill

1. 把整個資料夾複製到 skills 目錄，例如 `~/.claude/skills/association-rules/`
   （專案層級則放在 `.claude/skills/association-rules/`）。
2. 安裝 Python 套件：`pip install mlxtend scipy pandas numpy matplotlib`。
3. 對 Claude 說「幫我分析這份超市交易資料有哪些商品常一起買」，skill 就會觸發。

## 參考文獻

Tan, P.-N., Steinbach, M., Karpatne, A., & Kumar, V. (2019). *Introduction to Data Mining*
(2nd ed.). Pearson. 第 5 章（Association Analysis: Basic Concepts and Algorithms）與
10.4 節（Statistical Testing for Association Analysis）。
