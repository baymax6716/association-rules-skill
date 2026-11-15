```python
def decode_survey(df, dictionary):
    """依資料字典把原始值換成標籤；標示為「缺失」的值與空白一律設為缺失（不是「否」，也不是答案）。"""
    out, log = df.copy(), []
    for col in df.columns:
        rules = dictionary[dictionary["欄位"] == col]
        if (rules["類別"] == "ID").any():
            continue
        lab = dict(zip(rules["原始值"], rules["標籤"]))
        kind = dict(zip(rules["原始值"], rules["類別"]))
        raw = df[col].astype(str).str.strip()
        miss = (raw == "") | raw.map(lambda v: kind.get(v) == "缺失")
        unknown = sorted(set(raw[~miss]) - set(lab))
        if unknown:
            raise ValueError(f"{col} 有資料字典沒有的值 {unknown}：停止，請使用者補充資料字典")
        out[col] = raw.map(lab).where(~miss, None)
        changed = int((raw[~miss] != out.loc[~miss, col]).sum())
        if changed:
            log.append(("依資料字典", f"`{col}`：原始值換成標籤", changed))
        for v in sorted(set(raw[miss])):
            name = "空白（未回答）" if v == "" else f"{v} = {lab.get(v)}"
            log.append(("依資料字典", f"`{col}`：{name} 設為缺失（不當成「否」）", int((raw == v).sum())))
    return out, log


def complete_case(df, columns):
    """完整案例分析：只看選定的分析欄位；任一欄缺失的受訪者排除。"""
    miss = df[columns].isna()
    keep = ~miss.any(axis=1)
    log = [("完整案例分析", f"`{c}` 缺失人數", int(miss[c].sum())) for c in columns]
    log.append(("完整案例分析", "排除的受訪者（選定欄位任一缺失）", int((~keep).sum())))
    log.append(("結果", "分析人數 N（support 的分母）", int(keep.sum())))
    return df[keep], log


def encode_items(df, id_col, columns):
    """每位受訪者一個 basket，項目寫成「欄位=類別」；「否」也是一個項目。"""
    return {r[id_col]: frozenset(f"{c}={r[c]}" for c in columns) for _, r in df.iterrows()}
```
