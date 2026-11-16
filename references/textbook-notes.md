# 教科書重點與 R 對照程式碼

參考文獻：Tan, P.-N., Steinbach, M., Karpatne, A., & Kumar, V. (2019). *Introduction to Data Mining* (2nd ed.). Pearson.

以下是**改寫**的重點，不是原文引用。每一節都已對照原文核對；手邊的電子版沒有印刷頁碼，所以只列章節、表號、例號，不列頁碼。
本檔只收 skill 與分析指南用到的內容。

## 目錄

1. 5.1 基本概念
2. 5.2 Apriori 原理與頻繁項目集的產生
3. 5.3 規則產生與 confidence 剪枝
4. 5.6 FP-growth
5. 5.7.1 客觀指標與 support–confidence 架構的限制
6. 5.7.3 Simpson's paradox
7. 6.1 類別屬性
8. 6.2 連續屬性（第一版不採用）
9. R（arules）對照程式碼

---

## 1. 5.1 基本概念

- 購物籃資料可以表示成「交易 × 商品」的 0/1 矩陣（Table 5.1、5.2）。商品是**非對稱二元變數**：「出現」比「沒出現」重要。這種表示法忽略了數量與價格。
- itemset 是零個或多個項目的集合；有 k 個項目的稱為 k-itemset。support count σ(X) 是包含 X 的交易數；support s(X) = σ(X)/N。
- 規則 X → Y 中，X、Y 不能有共同項目。support = σ(X ∪ Y)/N（式 5.1）；confidence = σ(X ∪ Y)/σ(X)（式 5.2），是 P(Y | X) 的估計（Example 5.1）。
- support 很低的規則可能只是巧合，商業上也較少價值。
- **關聯不代表因果**：因果需要知道哪些變數是原因、哪些是結果，通常還涉及時間先後。
- 探勘問題（Definition 5.1）：找出所有 support ≥ minsup 且 confidence ≥ minconf 的規則。暴力列舉的規則數隨項目數呈指數成長，所以分兩步：先找頻繁項目集，再從中產生規則。同一個項目集產生的規則 support 都相同。

## 2. 5.2 Apriori 原理與頻繁項目集的產生

- **Apriori 原理**（5.2.1 節 Theorem 5.1）：頻繁項目集的所有子集都頻繁。反過來，不頻繁項目集的所有超集都不頻繁。原因是 support 的**反單調性**：項目集的 support 不會超過它任何子集的 support。依此剪掉候選，稱為 support-based pruning。
- **Apriori 演算法**（5.2.2 節 Algorithm 5.1）：
  1. 先掃描一次資料，得到頻繁 1-項目集。
  2. 由上一層的頻繁項目集產生新的候選。
  3. 剪掉含有不頻繁子集的候選（5.2.3 節）。
  4. 掃描資料計數，留下達到門檻的候選。
  5. 重複第 2–4 步，直到沒有新的頻繁項目集。

  這種做法是逐層（level-wise）、先產生候選再測試（generate-and-test）。

## 3. 5.3 規則產生與 confidence 剪枝

- 每個頻繁 k-項目集最多產生 2^k − 2 條規則（Example 5.2）。這些規則都已經滿足 support 門檻，計算 confidence 也不必再掃描資料，因為子集的 support 在找頻繁項目集時已經算過。
- confidence 沒有反單調性，但**同一個項目集**產生的規則有以下性質（5.3.1 節 Theorem 5.2）：若 X → Y − X 不滿足 confidence 門檻，則前件是 X 的任一子集的規則也一定不滿足。理由是前件越小，分母 σ(前件) 越大或不變。

## 4. 5.6 FP-growth

- 不走「產生候選再測試」的做法，而是把資料壓縮成 **FP-tree**，再直接從樹中找頻繁項目集（5.6.1 節）：
  1. 第一次掃描：計算每個項目的出現筆數，去掉不頻繁項目，其餘在每筆交易內依出現筆數由多到少排序。
  2. 第二次掃描：每筆交易放成從根節點出發的一條路徑。共用前綴的交易共用節點，節點記錄經過的交易數。交易間共同項目越多，壓縮越多。
- 由下往上分治（5.6.2 節）：從最後一個項目開始，例如找以 e 結尾的頻繁項目集。
  - 收集含 e 的**前綴路徑**，計數更新後去掉不頻繁項目，建立 e 的**條件 FP-tree**。
  - 遞迴找以 de、ce… 結尾的頻繁項目集。
  - 各子問題互不重疊，不會產生重複的項目集。
- 某些資料上 FP-growth 比 Apriori 快很多。條件樹很「茂密」時，效能會明顯下降。找到的頻繁項目集與 Apriori 相同。

## 5. 5.7.1 客觀指標與 support–confidence 架構的限制

- 客觀指標由資料計算、與領域無關；主觀有趣性要看是否出乎意料、能否行動。例如 {Butter} → {Bread} 可能可靠但不有趣。
- 指標通常由 2×2 列聯表計算（Table 5.6）。
- **support 的缺點**：低 support 但有意義的樣式可能被門檻排除。
- **confidence 的缺點**：忽略後件本身的 support。
  - 茶與咖啡（Example 5.3、Table 5.7）：confidence 75%，但整體喝咖啡的比例是 80%，其實是負相關。
  - 茶與蜂蜜（Table 5.8）：confidence 只有 50%，卻是很強的正相關。
- **interest factor（lift）**：s(A,B) / (s(A)s(B))（式 5.5）。= 1 獨立、> 1 正相關、< 1 負相關（式 5.6）。茶與咖啡 0.9375，茶與蜂蜜 4.1667。
- 其他指標：PS、φ 係數、IS（cosine），以及 Table 5.9 的 odds ratio、Jaccard、all-confidence 等。不同指標對同一組列聯表的排序可能差很多（Table 5.10、5.11）。

## 6. 5.7.3 Simpson's paradox

隱藏變數可能讓兩個變數間的關聯消失或反轉。書中以高畫質電視與健身器材為例（Table 5.17、5.18）：合併資料中買電視的人較常買健身器材，依「大學生／上班族」分層後，兩層的方向都反過來。

## 7. 6.1 類別屬性

- 問卷常有對稱二元屬性（例如是否網購）與名目屬性（例如教育程度）。書中以網路使用者問卷為例（Table 6.1）。
- 做法：每一個「屬性-值」組合轉成一個二元項目。對稱二元屬性轉成**兩個**項目，例如「網購=是」與「網購=否」（Table 6.2）。之後就能直接套用既有演算法。
- 要注意的三件事：
  1. **值很多的名目屬性**：每個值都不常見，降低門檻又會讓樣式暴增。書中建議把相關的值併成少數類別，或把少見的值併成「其他」。**skill 不自行合併**，要由使用者決定。
  2. **出現頻率很高的值**：例如 85% 的人家裡有電腦。這類典型值很少帶來新資訊，卻會產生許多冗餘樣式。
  3. 項目變多會增加計算量。同一屬性的兩個值不會同時出現，不必產生這種候選。

## 8. 6.2 連續屬性（第一版不採用）

- 含連續屬性的規則稱為 quantitative association rules。書中介紹三類方法：離散化、統計式、非離散化。
- 離散化（6.2.1 節）把相鄰的值分成區間（例如年齡 [12,16)、[16,20)…），可用等寬、等頻、熵或分群等方法，再把每個區間當成一個項目。
- **skill 第一版不自動分箱**：原始年齡、原始所得等數值欄位不支援，只能使用資料中已經分組的欄位。

---

## 9. R（arules）對照程式碼

練習資料 `data/groceries.csv`、`data/income_esl.csv` 就是 arules 內建的 `Groceries`、`IncomeESL`。
下面的參數值**只是佔位**，要換成使用者在 skill 中確認過的設定（示範設定不是通用標準）。
arules 的 `apriori()` 對應 skill 的 `algorithm: "apriori"`。arules 沒有內建 FP-growth，但頻繁項目集與規則的結果相同。

```r
library(arules)
minsup <- 0.01; minconf <- 0.3; minlift <- 1; max_lhs <- 2; min_lhs_count <- 99   # 換成使用者確認的設定

# ---- 購物資料：交易明細長表 → transactions ----
df <- read.csv("data/groceries.csv", stringsAsFactors = FALSE, na.strings = character(0))
df$item <- trimws(df$item)                                   # 固定規則：去除前後空白
df <- unique(df[df$item != "", c("transaction_id", "item")]) # 空白商品名稱需先經使用者同意排除；同交易同商品記一次
trans <- as(split(df$item, df$transaction_id), "transactions")
N <- length(trans)                                           # support 的分母
cat("N =", N, "；最低共同筆數 =", ceiling(minsup * N), "\n")

# 自由探索：前件 ≤ max_lhs 項、後件 1 項（arules 的 maxlen 是整條規則的項目數）
rules <- apriori(trans, parameter = list(supp = minsup, conf = minconf, minlen = 2, maxlen = max_lhs + 1),
                 control = list(verbose = FALSE))
quality(rules)$lhs_count <- round(interestMeasure(rules, "coverage", transactions = trans) * N)
rules <- subset(rules, lhs_count >= min_lhs_count & lift > minlift)
rules <- rules[!is.redundant(rules)]           # 較短前件的 confidence 已相同或更高者視為冗餘
inspect(head(sort(rules, by = "lift"), 20))

# 指定後件，例如後件 = whole milk
rhs_rules <- apriori(trans, parameter = list(supp = minsup, conf = minconf, minlen = 2, maxlen = max_lhs + 1),
                     appearance = list(rhs = "whole milk", default = "lhs"), control = list(verbose = FALSE))

# ---- 類別問卷：只用選定欄位，完整案例分析，項目為「欄位=類別」 ----
sv <- read.csv("data/income_esl.csv", stringsAsFactors = FALSE, check.names = FALSE, na.strings = "")
cols <- c("income", "sex", "marital status", "age", "education", "occupation", "householder status")
sub <- sv[, cols]
keep <- complete.cases(sub)                     # 只看選定欄位的缺失
cat("排除", sum(!keep), "人，剩", sum(keep), "人\n")   # 執行前要先告知使用者並取得同意
sub <- sub[keep, ]
sub[] <- lapply(sub, factor)                    # 序位變數也只當類別，不合併類別
strans <- as(sub, "transactions")               # 自動產生「欄位=類別」項目
srules <- apriori(strans, parameter = list(supp = minsup, conf = minconf, minlen = 2, maxlen = max_lhs + 1),
                  appearance = list(rhs = "income=75+", default = "lhs"), control = list(verbose = FALSE))
inspect(head(sort(subset(srules, lift > minlift), by = "lift"), 10))
```

Python 對應：`scripts/run_arules.py`（mlxtend 的 `apriori`／`fpgrowth`）。
