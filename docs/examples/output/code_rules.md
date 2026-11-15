```python
def count(baskets, itemset):
    return sum(1 for b in baskets.values() if itemset <= b)


def enumerate_rules(baskets, max_lhs=2):
    """列出所有前件 ≤ max_lhs 項、後件 1 項、且至少在 1 筆中同時出現的規則（直接逐筆計數）。"""
    n = len(baskets)
    items = sorted({i for b in baskets.values() for i in b})
    rows = []
    for k in range(1, max_lhs + 1):
        for lhs in combinations(items, k):
            A = frozenset(lhs)
            cA = count(baskets, A)
            if cA == 0:
                continue
            for b in items:
                if b in A or (any("=" in x for x in A) and b.split("=")[0] in {x.split("=")[0] for x in A}):
                    continue
                B = frozenset([b])
                cAB = count(baskets, A | B)
                if cAB == 0:
                    continue
                cB = count(baskets, B)
                rows.append({"前件": set_text(A), "後件": set_text(B), "前件筆數": cA, "後件筆數": cB,
                             "共同筆數": cAB, "support": cAB / n, "confidence": cAB / cA,
                             "後件比例": cB / n, "lift": (cAB / cA) / (cB / n)})
    return pd.DataFrame(rows)


def apply_filters(rules, n, minsup, minconf, minlift, min_lhs_count, max_lhs):
    """依設定篩選，並記錄每一步剩下幾條（不偷偷調整門檻）。"""
    steps = [("全部候選規則（前件 ≤ {} 項、後件 1 項）".format(max_lhs), len(rules))]
    min_count = math.ceil(minsup * n - 1e-9)
    r = rules[rules["共同筆數"] >= min_count]
    steps.append((f"support ≥ {minsup}（共同筆數 ≥ {min_count}）", len(r)))
    r = r[r["前件筆數"] >= min_lhs_count]
    steps.append((f"前件筆數 ≥ {min_lhs_count}", len(r)))
    r = r[r["confidence"] >= minconf - 1e-12]
    steps.append((f"confidence ≥ {minconf}", len(r)))
    r = r[r["lift"] > minlift]
    steps.append((f"lift > {minlift}", len(r)))
    # 冗餘：前件 2 項的規則，若拿掉其中一項後（同一後件）confidence 已相同或更高，多出的那一項沒有提供資訊
    conf = {(a, b): c for a, b, c in zip(rules["前件"], rules["後件"], rules["confidence"])}

    def redundant(row):
        lhs = row["前件"].strip("{}").split(", ")
        return len(lhs) == 2 and any(conf.get((set_text([x]), row["後件"]), -1) >= row["confidence"] - 1e-12 for x in lhs)
    red = r.apply(redundant, axis=1) if len(r) else pd.Series(dtype=bool)
    r = r[~red] if len(r) else r
    steps.append(("移除冗餘規則（較短前件的 confidence 已相同或更高）", len(r)))
    return r.sort_values(["lift", "共同筆數", "前件", "後件"], ascending=[False, False, True, True]), steps
```
