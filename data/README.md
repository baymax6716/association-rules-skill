# 練習用資料集

這裡的兩個資料集讓使用者下載後**自己用 skill 試試看**。skill 不會事先替它們跑好結果或報告：
你把資料交給 skill，它會從頭依三次確認的流程帶你分析。

兩個資料集都來自 R 套件 **arules**（版本 1.7.15，授權 GPL-3），用 R 匯出成 CSV，內容沒有任何修改。

| 檔案 | 類型 | 大小 | 對應 skill 的流程 | 對應教科書 |
|---|---|---|---|---|
| `groceries.csv` | 交易資料（長格式） | 9,835 筆交易、169 種商品、43,367 列 | 交易資料：`profile` → `split` → `mine` | 第 5 章的購物籃交易（5.1 的雜貨店收銀資料情境） |
| `income_esl.csv` | 問卷資料（一列一人） | 8,993 位受訪者、14 個題目 | 問卷資料：`encode` → `split` → `mine` | 6.1–6.2 節的問卷資料（類別屬性、有序與連續屬性） |

## groceries.csv

某間地方雜貨店 30 天的真實收銀機交易資料；商品已彙整成 169 個類別。

| 欄位 | 說明 |
|---|---|
| `transaction_id` | 交易編號（T00001 ～ T09835，依原資料順序編號） |
| `item` | 商品類別，例如 `whole milk`、`other vegetables`、`rolls/buns` |

- 沒有時間戳記和數量欄位，所以驗證只能用隨機切分（`split --method random`）。
- 有幾個商品出現在很大比例的交易裡（例如 whole milk），適合練習 cross-support 與 `--hconf`。

**來源**：Hahsler, M., Hornik, K., & Reutterer, T. (2006). Implications of probabilistic data modeling
for mining association rules. In *From Data and Information Analysis to Knowledge Engineering*
(pp. 598–605). Springer.

## income_esl.csv

舊金山灣區購物中心顧客的問卷，14 個人口特徵。原始資料有 9,409 人，arules 移除了年收入缺失的人，
剩 8,993 人。資料裡沒有受訪者編號，skill 會用列號當編號。

| 欄位 | 答案 | 缺失 |
|---|---|---|
| `income` | 年收入（千美元）：[0,10)、[10,15)、…、[50,75)、75+（有序，9 級） | 0 |
| `sex` | male、female | 0 |
| `marital status` | married、cohabitation、divorced、widowed、single | 160 |
| `age` | 14-17、18-24、25-34、35-44、45-54、55-64、65+（有序） | 0 |
| `education` | grade <9 ～ graduate study（有序，6 級） | 86 |
| `occupation` | professional/managerial、sales、laborer、clerical/service、homemaker、student、military、retired、unemployed | 136 |
| `years in bay area` | <1、1-3、4-6、7-10、>10（有序） | 913 |
| `dual incomes` | not married、yes、no | 0 |
| `number in household` | 1 ～ 9+（有序） | 375 |
| `number of children` | 0 ～ 9+（有序） | 0 |
| `householder status` | own、rent、live with parents/family | 240 |
| `type of home` | house、condominium、apartment、mobile Home、other | 357 |
| `ethnic classification` | american indian、asian、black、east indian、hispanic、pacific islander、white、other | 68 |
| `language in home` | english、spanish、other | 359 |

- 答案已經是文字標籤，不需要 codebook；缺失是空白。
- 很多題目是**有序類別**（收入、年齡、教育…），適合練習「次序答案要不要合併」（`encode --merge`）。
- `dual incomes` 幾乎由 `marital status` 決定：divorced、single、widowed 的人一律是 `not married`。這類規則
  confidence 會等於 1，但只是定義；可以練習用 `mine --flag-definitional` 把它們標出來。

**來源**：Impact Resources, Inc., Columbus, OH (1987)。取自 Hastie, T., Tibshirani, R., & Friedman, J.
(2001). *The Elements of Statistical Learning*. Springer 的書籍網站，經 arules 整理。

## 自己試試看

在 Claude Code 中輸入：

```text
/association-rules-skill 幫我分析 data/groceries.csv，找出哪些商品常一起被買
/association-rules-skill 幫我分析 data/income_esl.csv，看看哪些人口特徵和高收入常一起出現
```

或直接用腳本（skill 會在每次確認時建議這些參數，以下只是起點）：

```bash
python scripts/run_arules.py profile data/groceries.csv
python scripts/run_arules.py encode  data/income_esl.csv
```

## 自行重新匯出

```r
library(arules)
data(Groceries)
l <- LIST(Groceries)
write.csv(data.frame(transaction_id = rep(sprintf("T%05d", seq_along(l)), lengths(l)), item = unlist(l)),
          "groceries.csv", row.names = FALSE, fileEncoding = "UTF-8")
data(IncomeESL)
write.csv(IncomeESL, "income_esl.csv", row.names = FALSE, na = "", fileEncoding = "UTF-8")
```
