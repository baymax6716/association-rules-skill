# 指標速查：定義、性質與解讀界線

依據 Tan et al. (2019) 5.1、5.7、5.8 節。符號：N 為交易數；s(X) 為含 X 的交易比例；
規則 A→B 的 2×2 列聯表如下。

|  | B | ¬B | 合計 |
|---|---|---|---|
| A | f11 | f10 | f1+ |
| ¬A | f01 | f00 | f0+ |
| 合計 | f+1 | f+0 | N |

## 基本指標

| 指標 | 公式 | 範圍 | 意義 | 解讀界線 |
|---|---|---|---|---|
| support | s(A∪B) = f11 / N | 0–1 | 規則涵蓋多少交易（影響範圍） | 太低的規則可能只是巧合，商業影響也小 |
| confidence | s(A∪B) / s(A) | 0–1 | P(B \| A) 的估計 | **不考慮 B 本身多常見**，會誤導（見 tea→coffee） |
| lift（interest factor） | s(A∪B) / (s(A)s(B)) | 0–∞ | 相對於獨立情況的倍數；1 = 獨立 | 對稱（A→B 與 B→A 相同）；稀有品項容易被放大；**不是 null-invariant** |
| leverage（PS） | s(A∪B) − s(A)s(B) | −0.25–0.25 | 比獨立多出的交易比例 | 偏好高 support 的規則 |
| conviction | (1 − s(B)) / (1 − conf) | 0–∞ | 規則「出錯」比獨立時少幾倍 | conf = 1 時為 ∞ |
| φ 係數 | (f11 f00 − f10 f01) / √(f1+ f+1 f0+ f+0) | −1–1 | 二元變數的 Pearson 相關 | 同時重視 0–0 與 1–1；購物籃中 0 很多，會受 null addition 影響 |

## Null-invariant 指標（購物籃首選）

購物籃資料中，絕大多數交易同時「沒有買 A 也沒有買 B」（f00 很大）。若加入大量與 A、B 無關的
交易，lift、φ 都會改變；以下指標則不受 f00 影響（null addition invariance，5.7.1）：

| 指標 | 公式 | 範圍 | 說明 |
|---|---|---|---|
| cosine（IS） | s(A∪B) / √(s(A)s(B)) | 0–1 | = √(lift × support)；偏好高 support |
| Kulczynski | ½ [P(B\|A) + P(A\|B)] | 0–1 | 兩個方向 confidence 的平均 |
| all-confidence | s(A∪B) / max(s(A), s(B)) | 0–1 | 兩個方向中較小的 confidence |
| h-confidence | s(X) / max_{i∈X} s(i) | 0–1 | itemset 層級；用來去除 cross-support 樣式（5.8） |

**實務建議**：lift 用來判斷方向（> 1 正相關、< 1 負相關）；Kulczynski 或 all-confidence 用來
確認關聯是否涵蓋夠多交易。lift 很高但 Kulczynski 很低，通常表示其中一個品項很稀有。

## 經典例子（可用來向使用者說明）

**tea→coffee（confidence 的陷阱）**：1000 人中 200 人喝茶、800 人喝咖啡、150 人兩者都喝。
- confidence = 150/200 = 75%，看似很高；
- 但整體喝咖啡的比例是 80%，所以 lift = 0.75/0.8 = 0.9375 < 1，喝茶反而稍微**降低**喝咖啡的機率；
- φ = −0.0625；單尾 Fisher exact p ≈ 0.032（負相關方向，10.4.1）。

**tea→honey（低 confidence 但強關聯）**：200 人喝茶、120 人用蜂蜜、100 人兩者都有。
- confidence = 50%，用 70% 門檻會被刪掉；
- 但 lift = 0.5/0.12 ≈ 4.17，φ ≈ 0.585，是強烈正相關。

這兩個數字都可以用 `scripts/run_arules.py` 的 `measures()` 重現。

## Cross-support 樣式（5.8）

商品 support 偏斜時（例如牛奶出現在 60% 的籃子），低門檻會產生大量「高頻品 + 低頻品」的規則，
例如 {wine} → {cheese, milk}。這類規則 confidence 可能很高，卻只是因為牛奶本來就到處都有。
- support ratio r(X) = min s(i) / max s(i)；r 很小就是 cross-support；
- h-confidence ≤ r(X)，所以設 h-confidence 門檻 hc 會自動排除 r < hc 的樣式；
- 實務：profile 顯示「最大 / 中位數 support > 5」時，mine 加上 `--hconf 0.05`（或更高）。

## Simpson's paradox（5.7.3）

合併資料中 A、B 正相關，分層後各層卻都是負相關（或反過來）。原因是有隱藏變數同時影響兩者，
例如門市、顧客族群、時段。超市資料應依門市分層（Tan et al. 建議）。用 `stratify --by <欄位>`
檢查；方向反轉的規則不可直接用於決策。

## 主觀有趣性（5.7）

客觀指標只能排序。「麵包→奶油」可靠但顯而易見；意外且可靠的規則才有新資訊。是否有用、
能否行動，交給使用者以領域知識判斷。
