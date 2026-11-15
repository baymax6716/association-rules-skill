```python
def prep_shopping_long(df, tid_col, item_col):
    """交易明細長表 → 每筆交易一個 basket。只做已定義、可重現的機械性操作，並記錄筆數。"""
    log = []
    d = pd.DataFrame({"tid": df[tid_col].astype(str).str.strip(), "raw": df[item_col].astype(str)})
    log.append(("讀入", "原始資料列數", len(d)))
    d["item"] = d["raw"].str.strip()
    log.append(("固定規則", "去除商品名稱前後空白", int((d["item"] != d["raw"]).sum())))
    empty = d["item"] == ""
    log.append(("檢查", "商品名稱空白的列（有的話要詢問使用者）", int(empty.sum())))
    d = d[~empty]
    dup = d.duplicated(["tid", "item"])
    log.append(("固定規則", "同一筆交易中的同一商品只算一次（合併的列數）", int(dup.sum())))
    d = d[~dup]
    baskets = d.groupby("tid", sort=True)["item"].agg(lambda s: frozenset(s)).to_dict()
    log.append(("結果", "交易筆數 N（support 的分母）", len(baskets)))
    log.append(("結果", "只有一種商品的有效交易（保留）", sum(len(b) == 1 for b in baskets.values())))
    same = pd.Series([tuple(sorted(b)) for b in baskets.values()]).duplicated(keep=False).sum()
    log.append(("結果", "與其他交易內容完全相同的交易（全部保留，不刪除）", int(same)))
    return baskets, log


def prep_shopping_basket(df, tid_col, items_col, sep=","):
    """購物清單（一列一筆交易，商品寫在同一格）→ 先拆成長表，再走同一套規則。"""
    rows = [(r[tid_col], it) for _, r in df.iterrows() for it in str(r[items_col]).split(sep)]
    long = pd.DataFrame(rows, columns=[tid_col, "商品"])
    return prep_shopping_long(long, tid_col, "商品")
```
