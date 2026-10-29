# 可靠性評估：切分、檢定、多重比較

依據 Tan et al. (2019) 10.4 節（Statistical Testing for Association Analysis），以及 10.1.3 節
（多重假設檢定）。

## 為什麼關聯規則特別需要驗證

- 關聯規則是**非監督式**的，沒有標準答案可比對；而且一次會評估成百上千個 itemset，
  一定會有一些「看起來很強」的規則純屬巧合。
- 規則是用同一份資料**挑出來**的；在同一份資料上算 p 值會有選擇偏誤（selection bias），p 值偏小。
  所以要先切分（步驟 2），在驗證集上檢定。
- 多條規則共用品項，彼此不獨立；Bonferroni 過度保守（10.4），所以建議用控制 FDR 的
  Benjamini–Hochberg。

## 1. 驗證切分（split）

| 方法 | 指令 | 優點 | 注意 |
|---|---|---|---|
| 時間切分 | `split data.csv --method time --test-frac 0.3` | 模擬「用過去找規則、用未來檢查」 | 有季節性時，驗證期需涵蓋可比較的季節 |
| 隨機切分 | `split data.csv --method random --seed 42` | 兩邊分布相近 | 不能保證未來仍成立 |

切分後**只在 train 上探勘**；test 只用來驗證，不能回頭調參數再驗證同一個 test，否則 test 就
變成另一個 train。

## 2. Fisher exact test + BH-FDR（validate）

對每條規則 A→B，在驗證集建立 2×2 列聯表（A 對 B）：

- H0：A 與 B 獨立；H1：A 與 B 正相關（單尾，因為我們只關心「更常一起出現」）。
- 在固定的邊際總數下，f11 服從超幾何分布；Fisher exact test 就是超幾何檢定（10.4.1）。
  用二項分布會錯，因為它不考慮有限母體。
- 書中例子：tea/coffee，N=1000、tea=200、coffee=800、兩者=150，左尾 p ≈ 0.032。
- 所有規則的 p 值一起用 Benjamini–Hochberg 校正成 q 值；q < 0.05 表示在 FDR 5% 下顯著。
- 判定 ✅ 重現：test_lift > 1 且 q < α。

指令：`python scripts/run_arules.py validate test.csv --rules rules.csv --alpha 0.05`

多於兩個品項的 itemset 也可以用 χ² 檢定多維列聯表（10.4.1），但期望次數小時近似不準；
本腳本以「前項整體 vs 後項整體」的 2×2 表處理規則，這已足以回答「規則是否成立」。

## 3. 置換檢定（permtest，10.4.2）

建立「商品彼此獨立」的虛無資料：每個商品欄位**各自獨立打亂**，保留每個商品的 support，但切斷
商品之間的關聯。用相同門檻探勘，數規則數，重複 n 次。

- 虛無平均規則數 / 觀察規則數 ≈ 觀察到的規則中純屬巧合的比例（一種 FDR 的粗估）。
- 若觀察規則數沒有明顯多於虛無分布的 95 百分位，代表門檻太鬆，或資料本身沒什麼關聯。
- 限制：逐欄打亂會改變每筆交易的籃子大小。**swap randomization**（找兩列兩欄形成 1-0/0-1
  的矩形並互換）能同時保留商品 support 與籃子大小，較準確，但計算量大（10.4.2）。

指令：`python scripts/run_arules.py permtest train.csv --min-support 0.02 --min-confidence 0.3 --min-lift 1.2 --n-perm 50`

## 4. 分層檢查（stratify，5.7.3）

- `--by weekend / weekday / month / hour`：用時間戳記衍生分層；
- `--by store`：用資料中的欄位分層（需要該欄位在每筆交易內為固定值）；
- 每層的前項至少 `--min-count` 筆交易（預設 10）才計算，避免小樣本雜訊；
- `simpson_reversal = True`：所有分層方向都與合併資料相反 → 合併的關聯是隱藏變數造成的；
- `inconsistent = True`：部分分層方向不同 → 規則只在特定情境成立，報告時要註明。

## 5. 不確定性（bootstrap，選用）

10.4 提到可用 bootstrap 重抽交易來估計 support 的不確定性。腳本未內建；需要時可以寫一個迴圈，
重抽交易後重算 support / lift，取 2.5%–97.5% 分位數作為區間。

## 報告格式

| 規則 | train lift | test lift | test conf | q (BH) | 分層一致 | 分級 |
|---|---|---|---|---|---|---|
| {chips} → {soda} | 5.33 | 5.01 | 0.76 | <0.001 | ✓ | ✅ |
| {potatoes} → {bread} | 1.44 | 1.29 | 0.36 | 0.16 | — | ⚠️ |
| {cheese} → {bread} | 1.36 | 0.76 | 0.21 | 0.82 | — | ❌ |
