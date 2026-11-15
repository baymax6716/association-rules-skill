```python
def fp_order(baskets, min_count):
    """第一次掃描：計算每個項目的出現筆數，去掉不頻繁項目，其餘依出現筆數由多到少排序（同數依名稱）。"""
    cnt = {}
    for b in baskets.values():
        for i in b:
            cnt[i] = cnt.get(i, 0) + 1
    order = sorted((i for i in cnt if cnt[i] >= min_count), key=lambda i: (-cnt[i], i))
    return order, cnt


def fp_tree(paths, order):
    """第二次掃描：把每筆排序後的交易放進前綴樹；共用前綴的交易共用節點，節點上記錄經過的交易數。"""
    root = {"item": None, "count": 0, "children": {}}
    rank = {i: k for k, i in enumerate(order)}
    for items, c in paths:
        node = root
        for i in sorted((x for x in items if x in rank), key=rank.get):
            node = node["children"].setdefault(i, {"item": i, "count": 0, "children": {}})
            node["count"] += c
    return root


def prefix_paths(root, item):
    """找出某項目在樹中的每個節點，往上收集它的前綴路徑；路徑次數取該節點的次數。"""
    out = []

    def walk(node, path):
        for ch in node["children"].values():
            if ch["item"] == item:
                out.append((tuple(path), ch["count"]))
            walk(ch, path + [ch["item"]])
    walk(root, [])
    return out


def fp_growth(paths, min_count, suffix=()):
    """由出現次數最少的項目開始，對每個項目建立條件 FP-tree（只看包含它的交易）並遞迴，找出所有頻繁項目集。"""
    cnt = {}
    for items, c in paths:
        for i in items:
            cnt[i] = cnt.get(i, 0) + c
    result = {}
    for item in sorted((i for i in cnt if cnt[i] >= min_count), key=lambda i: (cnt[i], i)):
        itemset = frozenset((item,) + suffix)
        result[itemset] = cnt[item]
        order = sorted(cnt, key=lambda i: (-cnt[i], i))
        cond = prefix_paths(fp_tree(paths, order), item)
        result.update(fp_growth(cond, min_count, (item,) + suffix))
    return result
```
