"""產生《關聯規則分析指南》中所有範例數字與表格。

指南（docs/skill-guide.qmd）裡的範例表格都由本程式計算後寫入 examples/output/，
再以 Quarto 的 include 載入；指南中沒有手寫的計算結果。

執行（在 association-rules-skill 資料夾）：
    python docs/examples/build_guide_examples.py

內容
  1. 購物小案例：交易明細長表、購物清單兩種原始格式 → 前處理紀錄 → basket → 0/1 編碼 → 規則
  2. 問卷小案例：類別問卷寬表 + 資料字典 → 解碼 → 選欄位 → 完整案例分析 → 「欄位=類別」項目 → 規則
  3. 交叉核對：手算（逐筆計數）、mlxtend、R arules（若有安裝 R）三者結果是否一致
  4. 門檻敏感度：Groceries 練習資料在不同最低支持度下的項目集與規則數（說明門檻沒有通用標準）
"""
import inspect
import json
import math
import os
import shutil
import subprocess
import sys
from itertools import combinations

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "output")
REPO = os.path.dirname(os.path.dirname(HERE))
os.makedirs(OUT, exist_ok=True)
RESULTS = {}


# ----------------------------------------------------------------------------- 輸出工具

def num(v, d=4):
    if isinstance(v, (bool, np.bool_)):
        return "是" if v else "否"
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}"
    if isinstance(v, (float, np.floating)):
        if math.isnan(v):
            return "—"
        if math.isinf(v):
            return "∞"
        return f"{v:.{d}f}".rstrip("0").rstrip(".")
    return str(v)


def md_table(df, align=None):
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(":--" if (align or "l") == "l" else "--:" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        out.append("| " + " | ".join(num(v).replace("|", "\\|") for v in row) + " |")
    return "\n".join(out) + "\n"


def write(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")


def code_block(*funcs):
    src = "\n\n".join(inspect.getsource(f) for f in funcs)
    return f"```python\n{src}```\n"


def set_text(s):
    return "{" + ", ".join(sorted(s)) + "}"


# ----------------------------------------------------------------------------- 購物資料前處理

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


# ----------------------------------------------------------------------------- 問卷資料前處理

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


# ----------------------------------------------------------------------------- 規則計算（逐筆計數，可手算）

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


def contingency(baskets, A, B):
    n = len(baskets)
    f11 = count(baskets, A | B)
    f1p, fp1 = count(baskets, A), count(baskets, B)
    return pd.DataFrame({"": [f"有 {set_text(A)}", f"沒有 {set_text(A)}", "合計"],
                         f"有 {set_text(B)}": [f11, fp1 - f11, fp1],
                         f"沒有 {set_text(B)}": [f1p - f11, n - f1p - fp1 + f11, n - fp1],
                         "合計": [f1p, n - f1p, n]})


def binary_matrix(baskets):
    items = sorted({i for b in baskets.values() for i in b})
    return pd.DataFrame([[t] + [int(i in b) for i in items] for t, b in baskets.items()], columns=["編號"] + items)


# ----------------------------------------------------------------------------- 交叉核對

def mlxtend_rules(baskets, max_len=3):
    from mlxtend.frequent_patterns import apriori, association_rules
    X = binary_matrix(baskets).set_index("編號").astype(bool)
    fi = apriori(X, min_support=1e-9, use_colnames=True, max_len=max_len)
    ar = association_rules(fi, num_itemsets=len(X), metric="confidence", min_threshold=0)
    ar = ar[ar["consequents"].map(len) == 1]
    return pd.DataFrame({"前件": ar["antecedents"].map(set_text), "後件": ar["consequents"].map(set_text),
                         "support": ar["support"], "confidence": ar["confidence"], "lift": ar["lift"]})


def arules_rules(baskets, tag):
    """以 R 套件 arules 的 apriori 計算同一份 basket（需要 Rscript 與 arules）。"""
    rs = shutil.which("Rscript") or next((p for p in [r"C:\Program Files\R\R-4.6.1\bin\Rscript.exe"] if os.path.exists(p)), None)
    if not rs:
        return None, "找不到 Rscript"
    tmp = os.path.join(OUT, f"_{tag}_long.csv")
    res = os.path.join(OUT, f"_{tag}_arules.csv")
    pd.DataFrame([(t, i) for t, b in baskets.items() for i in sorted(b)], columns=["tid", "item"]).to_csv(
        tmp, index=False, encoding="utf-8")
    script = os.path.join(HERE, "crosscheck_arules.R")
    r = subprocess.run([rs, script, tmp, res], capture_output=True, text=True, encoding="utf-8", errors="replace")
    os.remove(tmp)
    if r.returncode != 0:
        return None, r.stderr[-300:]
    df = pd.read_csv(res, encoding="utf-8")
    os.remove(res)
    # arules 的標籤依它自己的項目順序排列；比對前統一成依名稱排序
    for c in ("前件", "後件"):
        df[c] = df[c].map(lambda s: set_text(x.strip() for x in s.strip("{}").split(", ")))
    return df, "R " + r.stdout.strip()


def compare(hand, other):
    m = hand.merge(other, on=["前件", "後件"], how="outer", suffixes=("", "_o"), indicator=True)
    both = m["_merge"] == "both"
    diff = max(float((m.loc[both, c] - m.loc[both, c + "_o"]).abs().max()) for c in ("support", "confidence", "lift"))
    return {"手算規則數": len(hand), "對方規則數": len(other), "兩邊都有": int(both.sum()),
            "只在一邊": int((~both).sum()), "最大差異": diff}


# ----------------------------------------------------------------------------- 1. 購物小案例

def shopping_case():
    long = pd.read_csv(os.path.join(DATA, "shop_small_long.csv"), dtype=str, keep_default_na=False)
    basket = pd.read_csv(os.path.join(DATA, "shop_small_basket.csv"), dtype=str, keep_default_na=False)
    write("shop_raw_long.md", md_table(long.assign(商品=long["商品"].map(lambda s: s.replace(" ", "␣")))))
    write("shop_raw_basket.md", md_table(basket.assign(購買商品=basket["購買商品"].map(lambda s: s.replace(" ", "␣")))))
    b1, log1 = prep_shopping_long(long, "交易編號", "商品")
    b2, log2 = prep_shopping_basket(basket, "交易編號", "購買商品")
    assert b1 == b2, "兩種原始格式整理後的 basket 應該完全相同"
    write("shop_log.md", md_table(pd.DataFrame(log1, columns=["類別", "處理或檢查", "影響筆數"])))
    write("shop_baskets.md", md_table(pd.DataFrame([(t, set_text(b), len(b)) for t, b in b1.items()],
                                                   columns=["交易編號", "basket", "商品數"])))
    write("shop_binary.md", md_table(binary_matrix(b1)))
    n = len(b1)
    items = sorted({i for b in b1.values() for i in b})
    freq = pd.DataFrame([(i, count(b1, frozenset([i])), count(b1, frozenset([i])) / n) for i in items],
                        columns=["商品", "出現筆數", "support"]).sort_values(["出現筆數", "商品"], ascending=[False, True])
    write("shop_item_freq.md", md_table(freq))
    rules = enumerate_rules(b1)
    cfg = dict(minsup=0.25, minconf=0.6, minlift=1.0, min_lhs_count=3, max_lhs=2)
    kept, steps = apply_filters(rules, n, **cfg)
    write("shop_filter_steps.md", md_table(pd.DataFrame(steps, columns=["篩選步驟", "剩下規則數"])))
    write("shop_rules_kept.md", md_table(kept[["前件", "後件", "前件筆數", "共同筆數", "support", "confidence", "後件比例", "lift"]]))
    # 被擋下的代表性規則
    picks = [("{吐司}", "{牛奶}"), ("{咖啡}", "{餅乾}"), ("{牛奶}", "{吐司}")]
    sel = pd.concat([rules[(rules["前件"] == a) & (rules["後件"] == b)] for a, b in picks])
    sel = sel.assign(結果=[reason(r, n, cfg) for r in sel.itertuples()])
    write("shop_rules_examples.md", md_table(sel[["前件", "後件", "前件筆數", "共同筆數", "support", "confidence", "後件比例", "lift", "結果"]]))
    # 設定紀錄示範：第一次設定沒有規則 → 說明原因、經同意後放寬，兩次設定都保留
    strict = dict(cfg, minsup=0.6)
    k0, _ = apply_filters(rules, n, **strict)
    need = math.ceil(strict["minsup"] * n - 1e-9)
    top_pair = rules.sort_values(["共同筆數", "前件"], ascending=[False, True]).iloc[0]
    why = (f"沒有規則：共同出現最多的組合是 {set_text(set(top_pair['前件'].strip('{}').split(', ')) | set(top_pair['後件'].strip('{}').split(', ')))}"
           f"，只有 {top_pair['共同筆數']} 筆，達不到 {need} 筆") if len(k0) == 0 else f"保留 {len(k0)} 條"
    write("shop_settings_log.md", md_table(pd.DataFrame([
        [1, f"最低支持度 {strict['minsup']}（≥ {need} 筆）、confidence ≥ 0.6、lift > 1、前件筆數 ≥ 3", len(k0),
         "使用者最初的設定", why],
        [2, f"最低支持度 {cfg['minsup']}（≥ {math.ceil(cfg['minsup'] * n - 1e-9)} 筆），其餘不變", len(kept),
         "說明原因後，使用者同意放寬", "採用；報告同時列出兩次設定"]],
        columns=["次序", "設定", "保留規則數", "決定者與理由", "結果"])))
    A, B = frozenset(["雞蛋"]), frozenset(["吐司"])
    write("shop_contingency.md", md_table(contingency(b1, A, B)))
    RESULTS["shopping"] = {"N": n, "items": len(items), "config": cfg, "kept": len(kept),
                           "candidates": len(rules), "rule_eggs_toast": rule_numbers(b1, A, B),
                           "rule_toast_milk": rule_numbers(b1, frozenset(["吐司"]), frozenset(["牛奶"])),
                           "rule_coffee_cookie": rule_numbers(b1, frozenset(["咖啡"]), frozenset(["餅乾"]))}
    return b1, rules


def reason(r, n, cfg):
    mc = math.ceil(cfg["minsup"] * n - 1e-9)
    if r.共同筆數 < mc:
        return f"排除：共同筆數 {r.共同筆數} < {mc}"
    if r.前件筆數 < cfg["min_lhs_count"]:
        return f"排除：前件筆數 {r.前件筆數} < {cfg['min_lhs_count']}"
    if r.confidence < cfg["minconf"] - 1e-12:
        return f"排除：confidence < {cfg['minconf']}"
    if r.lift <= cfg["minlift"]:
        return f"排除：lift ≤ {cfg['minlift']}"
    return "保留"


def rule_numbers(baskets, A, B):
    n = len(baskets)
    cA, cB, cAB = count(baskets, A), count(baskets, B), count(baskets, A | B)
    return {"N": n, "cA": cA, "cB": cB, "cAB": cAB, "support": cAB / n, "confidence": cAB / cA,
            "base": cB / n, "lift": (cAB / cA) / (cB / n)}


# ----------------------------------------------------------------------------- 2. 問卷小案例

def survey_case():
    raw = pd.read_csv(os.path.join(DATA, "survey_small.csv"), dtype=str, keep_default_na=False)
    dic = pd.read_csv(os.path.join(DATA, "survey_small_dictionary.csv"), dtype=str, keep_default_na=False)
    write("survey_raw.md", md_table(raw.replace("", "（空白）")))
    write("survey_dictionary.md", md_table(dic))
    dec, log = decode_survey(raw.replace("（空白）", ""), dic.assign(原始值=dic["原始值"].replace("（空白）", "")))
    write("survey_decoded.md", md_table(dec.fillna("（缺失）")))
    columns = ["年齡層", "網購", "運動頻率"]
    kept, log2 = complete_case(dec, columns)
    excluded = sorted(set(dec["受訪者編號"]) - set(kept["受訪者編號"]))
    write("survey_log.md", md_table(pd.DataFrame(
        [("欄位角色", "`受訪者編號` 是 ID，不納入規則", "—"), ("使用者選擇", "分析欄位：" + "、".join(columns), "—")]
        + [("使用者選擇", "未選入的欄位：`性別`、`居住地`（`居住地` 的缺失不影響本次分析）", "—")] + log + log2,
        columns=["類別", "處理或檢查", "影響筆數"])))
    baskets = encode_items(kept, "受訪者編號", columns)
    write("survey_baskets.md", md_table(pd.DataFrame([(t, set_text(b)) for t, b in baskets.items()],
                                                     columns=["受訪者編號", "basket（欄位=類別）"])))
    write("survey_binary.md", md_table(binary_matrix(baskets)))
    n = len(baskets)
    rules = enumerate_rules(baskets)
    cfg = dict(minsup=0.2, minconf=0.6, minlift=1.0, min_lhs_count=3, max_lhs=2)
    allk, steps = apply_filters(rules, n, **cfg)
    write("survey_filter_steps.md", md_table(pd.DataFrame(steps, columns=["篩選步驟", "剩下規則數"])))
    cols = ["前件", "後件", "前件筆數", "共同筆數", "support", "confidence", "後件比例", "lift"]
    write("survey_rules_free.md", md_table(allk[cols]))
    tgt = allk[allk["後件"] == "{網購=是}"]
    write("survey_rules_rhs.md", md_table(tgt[cols]))
    lhs = allk[allk["前件"] == "{網購=否}"]
    write("survey_rules_lhs.md", md_table(lhs[cols]))
    A, B = frozenset(["網購=否"]), frozenset(["運動頻率=幾乎不運動"])
    write("survey_contingency.md", md_table(contingency(baskets, A, B)))
    RESULTS["survey"] = {"N_raw": len(raw), "N": n, "excluded": excluded, "columns": columns, "config": cfg,
                         "candidates": len(rules), "kept": len(allk), "rhs_kept": len(tgt), "lhs_kept": len(lhs),
                         "rule_noshop_noexercise": rule_numbers(baskets, A, B),
                         "rule_young_shop": rule_numbers(baskets, frozenset(["年齡層=18-29 歲"]), frozenset(["網購=是"]))}
    return baskets, rules


# ----------------------------------------------------------------------------- 3. 交叉核對

def crosscheck(cases):
    rows = []
    for name, (baskets, hand) in cases.items():
        h = hand[["前件", "後件", "support", "confidence", "lift"]]
        c = compare(h, mlxtend_rules(baskets))
        rows.append([name, "mlxtend " + __import__("mlxtend").__version__, c["手算規則數"], c["對方規則數"],
                     c["兩邊都有"], c["只在一邊"], f"{c['最大差異']:.1e}"])
        ar, msg = arules_rules(baskets, "shop" if "購物" in name else "survey")
        if ar is None:
            rows.append([name, "R arules（未執行：" + msg + "）", c["手算規則數"], "—", "—", "—", "—"])
        else:
            c2 = compare(h, ar)
            rows.append([name, msg, c2["手算規則數"], c2["對方規則數"], c2["兩邊都有"], c2["只在一邊"], f"{c2['最大差異']:.1e}"])
    df = pd.DataFrame(rows, columns=["案例", "核對工具", "手算規則數", "工具規則數", "兩邊都有", "只在一邊", "support／confidence／lift 最大差異"])
    write("crosscheck.md", md_table(df))
    RESULTS["crosscheck"] = df.to_dict("records")


# ----------------------------------------------------------------------------- 4. 門檻敏感度（Groceries）

def sensitivity():
    from mlxtend.frequent_patterns import association_rules, fpgrowth
    path = os.path.join(REPO, "data", "groceries.csv")
    g = pd.read_csv(path, dtype=str, keep_default_na=False)
    baskets, log = prep_shopping_long(g, "transaction_id", "item")
    n = len(baskets)
    X = binary_matrix(baskets).set_index("編號").astype(bool)
    rows = []
    for s in [0.005, 0.01, 0.02, 0.05]:
        fi = fpgrowth(X, min_support=s, use_colnames=True, max_len=3)
        ar = association_rules(fi, num_itemsets=n, metric="confidence", min_threshold=0.3) if len(fi) else pd.DataFrame()
        ar = ar[(ar["consequents"].map(len) == 1) & (ar["lift"] > 1)] if len(ar) else ar
        rows.append([s, math.ceil(s * n - 1e-9), len(fi), int((fi["itemsets"].map(len) >= 2).sum()), len(ar)])
    df = pd.DataFrame(rows, columns=["最低支持度", "對應最低筆數", "頻繁項目集數", "其中 ≥ 2 項", "規則數（confidence ≥ 0.3、lift > 1）"])
    write("groceries_sensitivity.md", md_table(df))
    write("groceries_log.md", md_table(pd.DataFrame(log, columns=["類別", "處理或檢查", "影響筆數"])))
    top = X.mean().sort_values(ascending=False).head(5)
    write("groceries_top_items.md", md_table(pd.DataFrame({"商品": top.index, "出現筆數": (top * n).round().astype(int).values,
                                                            "support": top.values})))
    RESULTS["groceries"] = {"N": n, "items": X.shape[1], "sensitivity": df.to_dict("records")}


# ----------------------------------------------------------------------------- 程式碼片段（指南中顯示的程式就是實際執行的程式）

def code_fragments():
    write("code_shopping.md", code_block(prep_shopping_long, prep_shopping_basket))
    write("code_survey.md", code_block(decode_survey, complete_case, encode_items))
    write("code_rules.md", code_block(count, enumerate_rules, apply_filters))


def check_prose():
    """指南正文中直接寫出的數字，必須和計算結果一致；不一致時停止，避免手寫數字與表格脫節。"""
    s, v, g = RESULTS["shopping"], RESULTS["survey"], RESULTS["groceries"]
    close = lambda a, b: abs(a - b) < 5e-5
    e, m, c = s["rule_eggs_toast"], s["rule_toast_milk"], v["rule_noshop_noexercise"]
    checks = {
        "購物 N = 8": s["N"] == 8, "購物 5 種商品": s["items"] == 5, "購物保留 4 條": s["kept"] == 4,
        "雞蛋→吐司 3/8、3/3、5/8、1.6": (e["cA"], e["cB"], e["cAB"]) == (3, 5, 3) and close(e["lift"], 1.6),
        "吐司→牛奶 4/5、0.75、1.0667": (m["cAB"], m["cA"]) == (4, 5) and close(m["base"], 0.75) and close(m["lift"], 1.0667),
        "問卷原始 12 人、N = 10、排除 R08 R11": v["N_raw"] == 12 and v["N"] == 10 and v["excluded"] == ["R08", "R11"],
        "網購=否→幾乎不運動 3/4、0.3、2.5": (c["cA"], c["cB"], c["cAB"]) == (4, 3, 3) and close(c["lift"], 2.5),
        "Groceries N = 9,835、169 種": g["N"] == 9835 and g["items"] == 169,
        "Groceries 規則數 3 → 442": [r["規則數（confidence ≥ 0.3、lift > 1）"] for r in g["sensitivity"]][::3] == [442, 3],
    }
    bad = [k for k, ok in checks.items() if not ok]
    if bad:
        raise SystemExit("指南正文的數字與計算結果不一致：" + "；".join(bad))
    print(f"正文數字檢查：{len(checks)} 項全部一致")


def main():
    shop = shopping_case()
    survey = survey_case()
    crosscheck({"購物小案例": shop, "問卷小案例": survey})
    sensitivity()
    code_fragments()
    check_prose()
    env = {"python": sys.version.split()[0]}
    for m in ("pandas", "numpy", "mlxtend"):
        env[m] = __import__(m).__version__
    RESULTS["environment"] = env
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=1, default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else str(o))
    write("environment.md", "、".join(f"{k} {v}" for k, v in env.items()))
    print(json.dumps({k: v for k, v in RESULTS.items() if k != "crosscheck"}, ensure_ascii=False, indent=1, default=str)[:3000])
    print(open(os.path.join(OUT, "crosscheck.md"), encoding="utf-8").read())


if __name__ == "__main__":
    main()
