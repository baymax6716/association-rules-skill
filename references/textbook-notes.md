# 教科書重點筆記與 R 對照程式碼

參考文獻：Tan, P.-N., Steinbach, M., Karpatne, A., & Kumar, V. (2019). *Introduction to Data
Mining* (2nd ed.). Pearson.

以下為依章節**改寫**的重點（非原文引用），頁碼為書本印刷頁碼。

## 目錄

1. 5.1 基本概念（p.358）
2. 5.2 Frequent itemset 與 Apriori 原理（p.362–380）
3. 5.3 規則產生（p.380–384）
4. 5.4 精簡表示：maximal 與 closed itemsets（p.384–389）
5. 5.6 FP-growth（p.393–401）
6. 5.7 關聯樣式的評估（p.401–418）
7. 5.8 偏斜的 support 分布（p.418–424）
8. 6.1 類別屬性（p.451–454）
9. 6.2.1 連續屬性的離散化（p.454–458）
10. 10.4 關聯分析的統計檢定（p.787–795）
11. R（arules）對照程式碼

---

## 1. 5.1 基本概念（p.358）

- 購物籃資料可表示成交易 × 商品的 0/1 矩陣。商品是**非對稱二元變數**：「有買」比「沒買」重要。
  這種表示忽略了數量與價格。
- itemset：零個或多個商品的集合；k-itemset 有 k 個商品。
- support count σ(X)：含 X 的交易數；support s(X) = σ(X)/N。
- 關聯規則 X→Y（X、Y 不相交）：support = s(X∪Y)；confidence = σ(X∪Y)/σ(X)。
- support 太低的規則可能只是巧合，商業上也較無價值；confidence 估計 P(Y|X)。
- **規則不代表因果**，只代表共同出現；因果需要知道變數間的因果方向與時間順序。
- 探勘問題：找出所有 support ≥ minsup 且 confidence ≥ minconf 的規則。暴力法要檢查
  3^d − 2^{d+1} + 1 條規則（d 為商品數），不可行。因此分兩階段：先找 frequent itemsets，
  再從中產生規則。

## 2. 5.2 Frequent itemset 與 Apriori 原理（p.362–380）

- **Apriori 原理**：若一個 itemset 是頻繁的，它的所有子集也都是頻繁的。反過來說，不頻繁的
  itemset，其所有超集也不頻繁。理由是 support 具有 **anti-monotone** 性質。
- Apriori 演算法逐層進行：產生 k-itemset 候選 → 剪掉含有不頻繁子集的候選 → 掃描資料計數 →
  留下頻繁者。
- 計算量受以下因素影響（5.2.5）：support 門檻（越低越多）、商品數、交易數、**平均交易寬度**
  （籃子越大，頻繁 itemset 越長）。

## 3. 5.3 規則產生（p.380–384）

- 每個頻繁 k-itemset 可以產生 2^k − 2 條規則；這些規則的 support 都相同，不需要再掃描資料。
- confidence **不是** anti-monotone，但同一個 itemset 產生的規則有一個性質：把前項的商品
  移到後項時，confidence 只會下降或不變。這可以用來剪枝（confidence-based pruning）。
- 書中的國會投票例子顯示，高 confidence 規則可以提供可解讀的群體行為描述。

## 4. 5.4 精簡表示（p.384–389）

- **Maximal frequent itemset**：頻繁，且沒有任何直接超集是頻繁的。它最精簡，但遺失了子集的
  support。
- **Closed itemset**：沒有任何直接超集的 support 和它相同。closed frequent itemsets 保留了所有
  頻繁 itemset 的 support 資訊，可以避免產生冗餘規則。
- 包含關係：maximal ⊆ closed frequent ⊆ frequent。規則太多時，可改看 closed 或 maximal。

## 5. 5.6 FP-growth（p.393–401）

- 把交易壓縮成 FP-tree（prefix tree），再以分治法從各商品的條件樹中挖掘頻繁 itemset，
  **不需要產生候選集**。
- 結果與 Apriori 相同；交易共用前綴多時壓縮率高，速度明顯較快。

## 6. 5.7 關聯樣式的評估（p.401–418）

- 客觀興趣度（objective interestingness）由資料計算、與領域無關；主觀興趣度取決於是否出乎
  意料、是否可行動，需要領域知識。
- **support–confidence 架構的限制**：support 門檻會漏掉低頻但有意義的樣式；confidence 忽略
  後項本身的 support。書中以 tea→coffee 說明「高 confidence 卻是負相關」，以 tea→honey 說明
  「低 confidence 卻是強正相關」。
- 以統計獨立為基準的指標：lift / interest factor、PS（leverage）、φ 係數、IS（cosine）等。
- 不同指標對同一組列聯表的排序可能差很多；**沒有一個指標在所有情況都最好**。
- 選擇指標時要看它的性質：
  - **inversion**：0 與 1 互換後值是否不變（φ 不變，適合對稱二元變數）；
  - **row/column scaling**：列或欄乘上常數後是否不變（只有 odds ratio 不變，適合抽樣比例會變的研究）；
  - **null addition**：加入「A、B 都沒有」的交易後是否不變。購物籃與文件分析應選擇 null addition
    不變的指標，如 cosine、Jaccard、all-confidence、Kulczynski；lift、φ、odds ratio 不符合這個性質。
- 對稱指標（lift）適合 itemset；非對稱指標（confidence）較適合規則，因為規則有方向。
- 5.7.2：多個變數時可用多維列聯表推廣，但會出現**部分關聯**（partial association）的問題。
- 5.7.3 **Simpson's paradox**：隱藏變數會讓關聯在分層後消失或反轉。書中以 HDTV 與健身器材為例：
  合併資料是正相關，依「大學生 / 上班族」分層後兩層都是負相關。超市資料應依**門市**分層。

## 7. 5.8 偏斜的 support 分布（p.418–424）

- 多數商品低頻、少數商品極高頻時，support 門檻很難設定：門檻高會漏掉低頻商品，門檻低會產生大量
  **cross-support 樣式**（把極高頻商品和低頻商品湊在一起）。書中人口普查資料在低門檻下，有 93% 的
  頻繁 pair 是 cross-support。
- support ratio r(X) = 最小商品 support / 最大商品 support。
- cross-support 規則的 confidence 可能很高（因為高頻商品到處都有），所以 confidence 也擋不住。
- **h-confidence** = s(X) / max_i s(i)，就是 itemset 能產生的最低 confidence。它滿足
  h-confidence ≤ r(X)，因此設門檻 hc 會排除 r < hc 的 cross-support 樣式。它也是 anti-monotone，
  可以直接嵌入探勘。h-confidence ≥ hc 的 itemset 稱為 hyperclique。

## 8. 6.1 類別屬性（p.451–454）

- 問卷常有對稱二元屬性（性別、是否上網）與名目屬性（教育程度、州別）。書中以網路使用者問卷為例。
- 做法：每一個「屬性-值」組合轉成一個二元項目（例如 教育=研究所、教育=大學）；對稱二元屬性要轉成
  **兩個**項目（隱私顧慮=是、隱私顧慮=否），因為兩種答案都有意義。之後就可以直接套用既有的探勘演算法。
- 要注意的三件事：
  1. **值太多的名目屬性**（例如州別）每個值都不常見，降低門檻又會讓樣式數暴增；比較實際的做法是把
     相關的值併成少數類別（州 → 地區），或把少見的值併成「其他」。
  2. **出現頻率很高的值**（例如 85% 的人家裡有電腦）會產生許多冗餘樣式，因為這種典型值很少帶來新資訊；
     可以在探勘前移除，或用 5.8 的方法處理支持度範圍很大的資料。
  3. 項目變多會增加計算時間；同一屬性的兩個值不可能同時出現（support = 0），因此不必產生含同一屬性
     兩個值的候選 itemset。

## 9. 6.2.1 連續屬性的離散化（p.454–458）

- 含連續屬性的規則稱為 **quantitative association rules**。6.2 介紹三類方法：離散化、統計式、
  非離散化；本 skill 只用最常見的**離散化**。
- 離散化把相鄰的值分成區間（等寬、等頻、熵、分群皆可），每個區間再對應成一個非對稱二元項目。
- 關鍵參數是區間數，通常由使用者決定。書中例子（250 位使用者、年齡與線上聊天）說明：
  - **區間太寬**：規則的 confidence 被稀釋到門檻以下而消失；
  - **區間太窄**：每個子規則的 support 不足而消失；
  - 適中的寬度才能同時保留兩條規則。
- 嘗試所有相鄰區間的組合可以找回規則，但計算量大、產生大量冗餘規則（較寬區間的規則包含較窄的）；
  可以用最大支持度門檻限制過寬的區間。
- 實務含意：列出各組人數，讓使用者確認切點；必要時比較兩種切法。

## 10. 10.4 關聯分析的統計檢定（p.787–795）

- 非監督問題沒有標準答案，所以需要統計檢定來區分真實樣式與偶然樣式。也可用 bootstrap 估計
  support 的不確定性。
- 多個 itemset 共用商品、彼此不獨立，Bonferroni 過度保守；可用 FDR 控制，但要注意資料結構。
- **10.4.1 統計模型**：
  - 兩個商品：Fisher exact test（等同超幾何檢定）。二項分布不適合，因為它允許共同出現次數
    超過個別 support。
  - tea/coffee 例子：在獨立假設下，共同出現次數期望值為 160；觀察到 150，左尾 p ≈ 0.032，
    呈負相關。
  - 任何由 2×2 表決定的指標（lift、odds ratio、cosine、all-confidence）都可以由同一個超幾何分布
    得到虛無分布。
  - 多個商品：χ² 檢定（多維列聯表）。
- **10.4.2 隨機化**：產生「商品彼此無關」的合成資料。
  - 逐欄打亂：保留商品 support，但破壞籃子大小。
  - 逐列打亂：保留籃子大小，但破壞商品 support。
  - **swap randomization**：同時保留兩者，但計算量大。
  - 由合成資料中的 support 分布，可以用統計方式決定 support 門檻。

---

## 11. R（arules）對照程式碼

```r
# install.packages(c("arules", "arulesViz"))
library(arules)

# 步驟 1：長格式（transaction_id, item）→ transactions
df <- read.csv("data/sample_baskets.csv", stringsAsFactors = FALSE)
df$item <- tolower(trimws(df$item))
df <- subset(df, item != "" & quantity > 0)
df <- unique(df[, c("transaction_id", "item")])
trans <- as(split(df$item, df$transaction_id), "transactions")
summary(trans)                         # 交易數、商品數、籃子大小分布
itemFrequency(trans, type = "relative") |> sort(decreasing = TRUE) |> head(15)
# itemFrequencyPlot(trans, topN = 30)  # 先問使用者再畫

# 步驟 3–4：探勘（arules 的 maxlen 是「整條規則」的品項數）
rules <- apriori(trans, parameter = list(supp = 0.02, conf = 0.3, minlen = 2, maxlen = 3))
rules <- subset(rules, lift > 1.2)
rules <- rules[!is.redundant(rules)]
inspect(head(sort(rules, by = "lift"), 20))

# 其他指標（含 null-invariant 指標）
quality(rules) <- cbind(quality(rules),
  interestMeasure(rules, c("leverage", "conviction", "phi", "cosine", "kulczynski", "allConfidence"),
                  transactions = trans))

# cross-support：h-confidence（= all-confidence）
itemsets <- eclat(trans, parameter = list(supp = 0.02, maxlen = 3))
quality(itemsets)$hconf <- interestMeasure(itemsets, "allConfidence", transactions = trans)

# 精簡表示
sum(is.closed(itemsets)); sum(is.maximal(itemsets))

# 步驟 5：Fisher exact test + BH（arules 內建）
p <- interestMeasure(rules, "fishersExactTest", transactions = trans)   # 單尾，正相關
q <- p.adjust(p, method = "BH")
```

### 問卷資料（6.1、6.2.1）

```r
library(arules)
sv <- read.csv("data/sample_survey.csv", colClasses = "character")
cb <- read.csv("data/sample_codebook.csv", colClasses = "character")
# 代碼 → 標籤；98/99（missing 欄非空白）設為 NA，不產生項目
for (v in unique(cb$column)) {
  m <- cb[cb$column == v, ]
  lab <- setNames(ifelse(m$missing == "", m$label, NA), m$code)
  sv[[v]] <- unname(lab[sv[[v]]])
}
sv[sv == ""] <- NA
sv$年齡 <- cut(as.numeric(sv$年齡), breaks = c(-Inf, 18, 30, 45, 65, Inf), right = FALSE,
              labels = c("<18", "18–29", "30–44", "45–64", "≥65"))
# 或用 discretizeDF(sv, methods = list(年齡 = list(method = "frequency", breaks = 4)))
items <- sv[, c("年齡", "性別", "區域", "就業狀態", "政策A態度", "政策B態度")]
items[] <- lapply(items, factor)
trans <- as(items, "transactions")      # 自動產生「欄位=值」項目，NA 不產生項目
itemLabels(trans)[1:6]

# 指定後件：只找以政策A態度為後件的規則
rules <- apriori(trans, parameter = list(supp = 20 / length(trans), conf = 0.3, minlen = 2, maxlen = 3),
                 appearance = list(rhs = grep("^政策A態度=", itemLabels(trans), value = TRUE),
                                   default = "lhs"))
inspect(head(sort(subset(rules, lift > 1.2), by = "lift"), 10))
```

Python 對應：`scripts/run_arules.py`（mlxtend 的 `fpgrowth` / `apriori` + scipy 的
`fisher_exact` 與 `false_discovery_control`）。
