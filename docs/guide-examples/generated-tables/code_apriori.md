```python
def apriori_levels(baskets, min_count):
    """Apriori 的逐層搜尋：由頻繁 (k-1)-項目集產生 k-項目集候選，先剪掉「有子集不頻繁」的候選，再掃描資料計數。"""
    rows, freq = [], {}
    items = sorted({i for b in baskets.values() for i in b})
    cands = [frozenset([i]) for i in items]
    k = 1
    while cands:
        level = {}
        for c in cands:
            bad = [s for s in combinations(sorted(c), k - 1) if k > 1 and frozenset(s) not in freq]
            if bad:
                rows.append([k, set_text(c), "剪掉：子集 " + set_text(bad[0]) + " 不頻繁", "不計數", "—"])
                continue
            n = count(baskets, c)
            ok = n >= min_count
            rows.append([k, set_text(c), "掃描資料計數", n, "頻繁" if ok else "不頻繁"])
            if ok:
                level[c] = n
        freq.update(level)
        k += 1
        prev = sorted(level, key=lambda s: sorted(s))
        cands = sorted({a | b for a, b in combinations(prev, 2) if len(a | b) == k}, key=lambda s: sorted(s))
    return rows, freq


def rule_pruning(baskets, itemset, minconf):
    """同一個頻繁項目集產生的規則：前件較大的規則若 confidence 不夠，把前件再縮小的規則一定也不夠，可直接跳過。"""
    I, rows = frozenset(itemset), []
    failed = []
    for k in range(len(I) - 1, 0, -1):
        for lhs in combinations(sorted(I), k):
            A = frozenset(lhs)
            if any(A < f for f in failed):
                rows.append([set_text(A), set_text(I - A), "—", "跳過：前件更大的規則已不合格"])
                continue
            c = count(baskets, I) / count(baskets, A)
            ok = c >= minconf - 1e-12
            rows.append([set_text(A), set_text(I - A), c, "合格" if ok else "不合格"])
            if not ok:
                failed.append(A)
    return rows
```
