---
name: association-rules
description: >
  關聯規則 / 購物籃分析（association rules, market basket analysis, frequent itemset mining）的
  統計顧問，屬於 unsupervised learning。當使用者有超市、零售、電商的交易資料（交易編號、購買品項、
  時間戳記、數量），想知道「哪些商品常被一起買」、要做交叉銷售、商品陳列、組合促銷或推薦，或提到
  Apriori、FP-growth、frequent itemset、support、confidence、lift、leverage、conviction、
  min_support 怎麼設、規則太多 / 找不到規則、mlxtend、arules、啤酒與尿布時，都使用本 skill。
  即使使用者沒有說出「關聯規則」，只說「幫我看這份購物明細有什麼商品組合」也應該使用。
  本 skill 依步驟引導：建立購物籃、安排驗證、設定門檻、產生規則、評估可靠性（Fisher exact test、
  FDR、置換檢定、Simpson's paradox），最後轉成實務解讀。
---

# 關聯規則統計顧問（Association Rules）

本 skill 協助使用者在**超市式交易資料**上做關聯規則分析，並把結果講成可以驗證、可以行動的結論。
理論依據為 Tan, Steinbach, Karpatne & Kumar (2019), *Introduction to Data Mining* (2nd ed.)，
第 5 章（Association Analysis: Basic Concepts and Algorithms）與 10.4 節（Statistical Testing for
Association Analysis）。

**範圍**：交易資料的二元關聯規則，也就是「有沒有買」。不處理序列樣式（先買 A 之後才買 B）、
連續變數離散化、商品階層或負向樣式；使用者問到時，說明它們超出範圍並簡短指出方向。

## 行為限制（最高優先）

關聯規則一定會「找到東西」，即使資料純屬隨機，所以使用者需要在每個決定點知道自己選了什麼。
因此：

1. **一次只做一個步驟**。做完就停下來，回報該步驟的輸出，詢問是否進入下一步。不要一口氣跑完 1–6。
2. **未經同意不探勘、不檢定**。步驟 3 提出參數建議後，必須等使用者確認或修改，才執行步驟 4。
   步驟 5 的每一種檢定也要先說明用途，取得同意後才跑。
3. **先文字、後圖形**。資料讀入後先用文字描述；要畫圖（籃子大小分布、商品頻率）先問。
4. 使用者已經自己指定參數或要求「直接跑」時，照做，但仍要在結果中標出被省略的檢查，例如「未做驗證切分」。

## 工作流程

完整的決策節點、Mermaid 流程圖與每步的輸出格式見 `references/decision-tree.md`。

### 步驟 1：檢查資料並建立購物籃

- 確認欄位角色：交易編號、品項、時間戳記、數量。也要確認格式：長格式（一列一品項）、
  basket 格式（一列一籃，品項以分隔符號串起來），或 0/1 寬格式。
- 執行 `python scripts/run_arules.py profile <data>`。
- **輸出**：清理紀錄（缺漏、品名正規化、退貨或數量 ≤ 0、重複品項）、交易數、商品數、
  購物籃大小分布、商品頻率（前後段）、support 分位數。
- 向使用者說明，數量只用於清理；分析時每個品項只看「有 / 無購買」（Tan et al. 5.1）。
  若單品項籃子比例很高，或 support 分布偏斜，要在這一步先提醒。

### 步驟 2：先安排驗證，再開始探勘

規則是在同一份資料上「挑」出來的，同一份資料再拿來評估會過度樂觀，所以驗證方式要在探勘前決定。

- 有時間戳記時，預設**依時間切分**：較早約 70% 探勘，較晚約 30% 驗證。這模擬「用過去預測未來」。
  沒有時間戳記時改用隨機切分。交易數少於約 300 時，切分後兩邊都太小，改用置換檢定，並說明限制。
- 執行 `python scripts/run_arules.py split <data> --method time --test-frac 0.3`。
- **輸出**：切分方式、理由、兩邊的交易數，以及**可支持的結論範圍**。例如「規則在後 30% 期間
  重現 → 可說在這段期間穩定」；「只在探勘集成立 → 只能當假說」。

### 步驟 3：設定門檻與演算法

- 根據步驟 1 的 profile 輸出，提出參數表（寬鬆 / 標準 / 嚴格），每個參數附理由。**使用者可以全部
  覆寫**，skill 的角色是解釋取捨，不替使用者決定。
- 要讓使用者理解的重點：
  - `min_support` 是唯一真正限制探勘的門檻。太高會漏掉低頻但強的組合；太低則規則爆量，
    且出現 cross-support 規則（Tan et al. 5.8）。
  - `min_confidence` 本身會誤導，因為它沒有考慮後項本身就很常見（tea→coffee 例子，5.7.1），
    所以門檻不宜高，主要篩選交給 lift。
  - **lift 不是探勘門檻**，而是產生規則後的篩選與排序指標。
  - `max_len` 控制可讀性；超過 3 個品項的規則很少能轉成行動。
  - **Apriori 與 FP-growth 找到的結果完全相同**，差別只在速度與記憶體。
  - support 分布偏斜（例如牛奶出現在 60% 的籃子）時，加上 `--hconf`（h-confidence）。
- **輸出**：參數表（參數、建議值、理由、使用者最後的決定）。等使用者確認。

### 步驟 4：產生規則，處理「太多」或「沒有」

- 執行 `python scripts/run_arules.py mine <train> --min-support S --min-confidence C --min-lift L --max-len K [--hconf H]`。
- **輸出**：frequent itemsets 數量（含 closed / maximal）、候選規則表、**被排除規則與原因**。
  排除原因有四種：confidence 未達門檻、lift 不夠、cross-support、冗餘。
- 太多（> 約 100 條）：提高 min_support 或 min_lift；max_len 改為 2；加 `--hconf`；
  依 closed / maximal itemsets 精簡（5.4）；只看含目標商品的規則。
- 沒有：先看單品項籃子比例與籃子大小。再逐步降低 min_support，但每條規則至少約 10 筆交易支撐。
  也可以降低 confidence、改用 lift 篩選。若資料本質上就沒有共購，「沒有規則」本身就是結論。
- 方向相反的成對規則（A→B 與 B→A）lift 相同、confidence 不同；解讀時合併成一組來談。

### 步驟 5：評估可靠性

依需要選用，每一項都先取得同意（細節見 `references/validation.md`）：

| 檢查 | 指令 | 回答的問題 |
|---|---|---|
| 驗證集重現 + Fisher exact + BH-FDR | `validate <test> --rules rules.csv` | 規則在沒看過的交易裡還成立嗎？排除多重比較後仍顯著嗎？ |
| 置換檢定 | `permtest <train> ...` | 如果商品彼此完全獨立，同樣門檻會找到幾條「假規則」？ |
| 分層檢查 | `stratify <data> --rules rules.csv --by weekend` | 合併資料的關聯是否在各分層消失或反轉（Simpson's paradox，5.7.3）？ |
| 多指標比對 | 看 rules.csv 的 kulczynski、cosine、all_confidence | lift 很高是不是只因為品項很稀少？（null-invariant 指標，5.7.1） |

**輸出**：每條規則的可靠性分級。✅ 驗證集重現且 q < 0.05；⚠️ 方向一致但不顯著；
❌ 未重現或方向反轉。另外列出整體重現率。

### 步驟 6：把結果轉成實務解讀

- 每條保留下來的規則寫成一句白話：「買了 A 的顧客中，有 X% 也買了 B，是一般顧客的 L 倍」。
- 接著給**行動建議 + 限制**：陳列、組合促銷、推薦，並說明預期影響有多大（看 support，也就是有多少
  交易會受影響）。
- 必須講清楚：**規則代表共同出現，不代表因果**（Tan et al. 5.1）。「買 A 導致買 B」需要實驗
  （例如 A/B 測試促銷）才能確認。
- 明顯的規則（例如 pasta→tomato sauce）可靠但不新奇；意外且可靠的規則才是重點。
  主觀的「有趣與否」交給使用者判斷（5.7）。

## 計算規則

- 所有數字都用 `scripts/run_arules.py` 或 Python 套件（mlxtend、scipy、pandas）算，**不要心算**。
- 腳本需要 `pip install mlxtend scipy pandas numpy matplotlib`。
- 使用者用 R 時，`references/textbook-notes.md` 附有 arules 的對照程式碼。
- 指標定義與解讀界線見 `references/measures.md`。報告 lift 時，同時給 support（影響範圍）與
  confidence（條件機率）。

## 溝通風格

- 用使用者的語言回答（預設繁體中文，統計名詞保留英文）。先講結論，再講細節。
- 解釋指標時，用 2×2 列聯表或「每 100 筆交易」的說法，而不是只丟公式。
- 提供可以直接執行的指令或程式碼，讓使用者能自己重現。
