"""關聯規則（association rules）分析腳本：對應 SKILL.md 的步驟 1–6。

子命令
  profile   步驟 1：清理資料、建立購物籃，輸出交易數、商品數、籃子大小分布、商品頻率、建議參數
  split     步驟 2：依時間或隨機切分「探勘集 / 驗證集」
  mine      步驟 3–4：找 frequent itemsets、產生規則、篩選，輸出候選規則表與被排除規則（含原因）
  validate  步驟 5：在驗證集重算指標，做 Fisher exact test 與 Benjamini–Hochberg FDR 校正
  permtest  步驟 5：欄位置換（permutation）虛無分布，估計「純機率下會找到幾條規則」
  stratify  步驟 5：分層（週末 / 月份 / 時段 / 自訂欄位）檢查 Simpson's paradox

所有計算都以 Tan, Steinbach, Karpatne & Kumar (2019), Introduction to Data Mining (2nd ed.)
第 5 章與 10.4 節的定義為準，詳見 references/measures.md 與 references/validation.md。

範例
  python scripts/run_arules.py profile data/sample_baskets.csv
  python scripts/run_arules.py split data/sample_baskets.csv --method time --test-frac 0.3
  python scripts/run_arules.py mine arules_output/train.csv --min-support 0.015 --min-confidence 0.3
  python scripts/run_arules.py validate arules_output/test.csv --rules arules_output/rules.csv
"""
import argparse
import json
import math
import os
import sys
from itertools import combinations

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TID_NAMES = ["transaction_id", "transactionid", "tid", "invoice", "invoiceno", "invoice_no",
             "order_id", "orderid", "basket_id", "member_number", "receipt", "交易編號", "訂單編號"]
ITEM_NAMES = ["item", "items", "product", "product_name", "itemdescription", "description",
              "sku", "商品", "品項", "商品名稱"]
TIME_NAMES = ["timestamp", "time", "datetime", "date", "invoicedate", "order_time", "時間", "日期"]
QTY_NAMES = ["quantity", "qty", "amount", "數量"]


# ----------------------------------------------------------------------------- 載入與清理

def read_table(path, encoding=None):
    if path.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(path)
    encs = [encoding] if encoding else ["utf-8-sig", "cp950", "latin1"]
    last = None
    for enc in encs:
        try:
            return pd.read_csv(path, encoding=enc, dtype=str, keep_default_na=False)
        except UnicodeDecodeError as e:
            last = e
    raise last


def pick(cols, wanted, override):
    if override:
        if override not in cols:
            sys.exit(f"找不到欄位 {override!r}；現有欄位：{list(cols)}")
        return override
    low = {c.lower().strip(): c for c in cols}
    for w in wanted:
        if w in low:
            return low[w]
    return None


def norm_item(x):
    return " ".join(str(x).split()).lower()


def load_transactions(args):
    """回傳 (baskets: dict[tid] -> frozenset, tx: DataFrame[tid, time, ...], log: list[str])"""
    df = read_table(args.data, getattr(args, "encoding", None))
    log = [f"讀入 {len(df):,} 列、欄位 {list(df.columns)}"]
    fmt = getattr(args, "format", "auto")
    tid_c = pick(df.columns, TID_NAMES, getattr(args, "tid_col", None))
    item_c = pick(df.columns, ITEM_NAMES, getattr(args, "item_col", None))
    if fmt == "auto":
        if tid_c and item_c:
            fmt = "long"
        elif df.shape[1] >= 2 and all(set(df[c].unique()) <= {"0", "1", "True", "False", "true", "false", ""}
                                      for c in df.columns if c != tid_c):
            fmt = "wide"
        else:
            fmt = "basket"
    log.append(f"判定資料格式：{fmt}")

    if fmt == "wide":
        tid_c = tid_c or "_row"
        if tid_c == "_row":
            df["_row"] = [f"R{i+1}" for i in range(len(df))]
        item_cols = [c for c in df.columns if c != tid_c]
        long = df.melt(id_vars=[tid_c], value_vars=item_cols, var_name="item", value_name="v")
        long = long[long["v"].isin(["1", "True", "true"])]
        df = long.rename(columns={tid_c: "tid"})[["tid", "item"]]
        time_c = qty_c = None
    elif fmt == "basket":
        col = item_c or df.columns[-1]
        sep = getattr(args, "sep", None) or ","
        if not tid_c:
            df["_row"] = [f"R{i+1}" for i in range(len(df))]
            tid_c = "_row"
        time_c = pick(df.columns, TIME_NAMES, getattr(args, "time_col", None))
        df = df.assign(item=df[col].str.split(sep)).explode("item")
        df = df.rename(columns={tid_c: "tid"})
        keep = ["tid", "item"] + ([time_c] if time_c else [])
        df = df[keep].rename(columns={time_c: "time"} if time_c else {})
        qty_c = None
    else:
        if not (tid_c and item_c):
            sys.exit("長格式需要交易編號與品項欄位，請用 --tid-col / --item-col 指定。")
        time_c = pick(df.columns, TIME_NAMES, getattr(args, "time_col", None))
        qty_c = pick(df.columns, QTY_NAMES, getattr(args, "qty_col", None))
        ren = {tid_c: "tid", item_c: "item"}
        if time_c:
            ren[time_c] = "time"
        if qty_c:
            ren[qty_c] = "qty"
        extra = [c for c in getattr(args, "keep_cols", []) or [] if c in df.columns]
        df = df[list(ren) + extra].rename(columns=ren)
        log.append(f"欄位對應：交易編號={tid_c}, 品項={item_c}, 時間={time_c}, 數量={qty_c}")

    n0 = len(df)
    df["tid"] = df["tid"].astype(str).str.strip()
    raw_item = df["item"].astype(str)
    df["item"] = raw_item.map(norm_item)
    bad = (df["tid"] == "") | (df["item"].isin(["", "nan", "none", "null"]))
    if bad.sum():
        log.append(f"移除缺漏交易編號或品名的列：{int(bad.sum())}")
    df = df[~bad].copy()
    changed = int((df["item"] != raw_item[df.index]).sum())
    if changed:
        log.append(f"品名正規化（去空白、轉小寫）影響 {changed} 列，同一商品的不同寫法已合併")

    if "qty" in df.columns:
        q = pd.to_numeric(df["qty"], errors="coerce")
        nonpos = (q <= 0) | q.isna()
        if nonpos.sum():
            log.append(f"移除數量 ≤ 0 或無法解析的列（退貨 / 取消 / 錯誤）：{int(nonpos.sum())}")
        df = df[~nonpos].copy()
        log.append("數量欄位只用於清理；關聯規則把每個品項視為「有 / 無購買」的二元變數（Tan et al. 5.1）")

    dup = int(df.duplicated(["tid", "item"]).sum())
    if dup:
        log.append(f"同一交易內重複出現的品項合併為一次：{dup} 列")

    tx = pd.DataFrame({"tid": df["tid"].unique()})
    if "time" in df.columns:
        t = pd.to_datetime(df["time"], errors="coerce")
        df["time"] = t
        if t.isna().all():
            log.append("時間欄位無法解析，視為沒有時間戳記")
            df = df.drop(columns="time")
        else:
            g = df.groupby("tid")["time"]
            multi = int((g.nunique() > 1).sum())
            if multi:
                log.append(f"{multi} 筆交易含多個時間戳記，取最早時間作為交易時間")
            tx = tx.merge(g.min().rename("time").reset_index(), on="tid", how="left")
    for c in getattr(args, "keep_cols", []) or []:
        if c in df.columns:
            tx = tx.merge(df.groupby("tid")[c].first().reset_index(), on="tid", how="left")

    baskets = df.groupby("tid")["item"].agg(frozenset).to_dict()
    tx = tx[tx["tid"].isin(baskets)].reset_index(drop=True)
    log.append(f"清理後：{len(df.drop_duplicates(['tid', 'item'])):,} 個（交易, 品項）組合，原始 {n0:,} 列")
    return baskets, tx, log


def onehot(baskets):
    items = sorted({i for b in baskets.values() for i in b})
    idx = {it: j for j, it in enumerate(items)}
    tids = list(baskets)
    m = np.zeros((len(tids), len(items)), dtype=bool)
    for r, t in enumerate(tids):
        for it in baskets[t]:
            m[r, idx[it]] = True
    return pd.DataFrame(m, columns=items, index=tids)


# ----------------------------------------------------------------------------- 指標

def measures(n, cA, cB, cAB, maxitem=None, minitem=None):
    """以計數計算規則 A→B 的各項指標（定義見 Tan et al. Table 5.9 / 5.8 節）。"""
    sA, sB, sAB = cA / n, cB / n, cAB / n
    conf = cAB / cA if cA else float("nan")
    lift = sAB / (sA * sB) if sA * sB else float("nan")
    lev = sAB - sA * sB
    denom = math.sqrt(sA * sB * (1 - sA) * (1 - sB)) if 0 < sA < 1 and 0 < sB < 1 else float("nan")
    phi = lev / denom if denom else float("nan")
    conv = (1 - sB) / (1 - conf) if conf < 1 else float("inf")
    cos = sAB / math.sqrt(sA * sB) if sA * sB else float("nan")
    kulc = 0.5 * (cAB / cA + cAB / cB) if cA and cB else float("nan")
    allc = cAB / max(cA, cB) if max(cA, cB) else float("nan")
    out = dict(support=sAB, count=int(cAB), confidence=conf, lift=lift, leverage=lev,
               conviction=conv, phi=phi, cosine=cos, kulczynski=kulc, all_confidence=allc,
               antecedent_support=sA, consequent_support=sB)
    if maxitem:
        out["h_confidence"] = cAB / maxitem
        out["support_ratio"] = minitem / maxitem
    return out


def fmt_set(s):
    return "{" + ", ".join(sorted(s)) + "}"


# ----------------------------------------------------------------------------- 子命令

def cmd_profile(args):
    baskets, tx, log = load_transactions(args)
    n = len(baskets)
    sizes = pd.Series({t: len(b) for t, b in baskets.items()})
    counts = pd.Series([i for b in baskets.values() for i in b]).value_counts()
    sup = counts / n
    print("## 步驟 1：資料檢查與購物籃建立\n\n### 清理紀錄")
    for line in log:
        print(f"- {line}")
    print(f"\n### 基本量\n- 交易數 N = {n:,}\n- 商品數 = {len(counts):,}\n"
          f"- 平均籃子大小 = {sizes.mean():.2f}（中位數 {sizes.median():.0f}，最大 {sizes.max()}）\n"
          f"- 矩陣密度 = {sizes.sum() / (n * len(counts)):.4f}")
    if "time" in tx.columns:
        print(f"- 時間範圍：{tx['time'].min()} ～ {tx['time'].max()}")
    print("\n### 購物籃大小分布")
    dist = sizes.clip(upper=10).value_counts().sort_index()
    for k, v in dist.items():
        lab = f"{k}+" if k == 10 else str(k)
        print(f"- {lab:>3} 件：{v:>6,}（{v / n:6.1%}）")
    one = (sizes == 1).mean()
    if one > 0.4:
        print(f"> 注意：{one:.0%} 的籃子只有 1 件，這些交易不可能貢獻任何規則，規則可能很少。")
    k = args.top
    print(f"\n### 商品頻率（前 {k} 名）")
    print(pd.DataFrame({"count": counts, "support": sup.round(4)}).head(k).to_string())
    print(f"\n### 商品頻率（後 {min(k, 10)} 名）")
    print(pd.DataFrame({"count": counts, "support": sup.round(4)}).tail(min(k, 10)).to_string())
    q = sup.quantile([0, .25, .5, .75, .9, 1])
    print("\n### 商品 support 分布（分位數）")
    print("  " + "  ".join(f"{int(p * 100)}%={v:.4f}" for p, v in q.items()))
    skew = sup.max() / sup.median()
    if skew > 5:
        print(f"> support 分布偏斜（最大 / 中位數 = {skew:.1f}）：低門檻時容易產生 cross-support 規則"
              "（Tan et al. 5.8），建議在 mine 加上 --hconf。")

    med = float(sup.median())
    std = max(10 / n, min(0.05, 2 * med ** 2))
    print("\n### 建議參數（供參考，使用者可自行覆寫）")
    print("| 參數 | 寬鬆 | 標準 | 嚴格 | 理由 |\n|---|---|---|---|---|")
    print(f"| min_support | {std / 2:.4f} | {std:.4f} | {std * 2:.4f} | 標準值 = max(10/N, 2×(中位數 support)²)："
          f"讓兩個中位數頻率商品在 lift≈2 時仍可被找到，且每條規則至少有約 {std * n:.0f} 筆交易支撐 |")
    print("| min_confidence | 0.20 | 0.30 | 0.50 | confidence 要和後項本身的 support 比較才有意義，"
          "門檻不宜過高，主要篩選交給 lift |")
    print("| min_lift（篩選，非探勘門檻） | 1.0 | 1.2 | 1.5 | lift>1 代表比獨立情況更常一起出現 |")
    print("| max_len（規則總長度） | 4 | 3 | 2 | 越長越難解讀，也越容易是巧合 |")
    hc = "0.05" if skew > 5 else "不需要"
    print(f"| hconf（h-confidence） | — | {hc} | 0.10 | 去除高頻商品與低頻商品硬湊的 cross-support 規則 |")
    print("| 演算法 | — | fpgrowth | — | Apriori 與 FP-growth 結果完全相同，只差速度；資料大時用 FP-growth |")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(12, 4))
        sizes.clip(upper=15).value_counts().sort_index().plot.bar(ax=ax[0], color="#4C72B0")
        ax[0].set(title="Basket size distribution", xlabel="items per basket (15 = 15+)", ylabel="transactions")
        sup.head(30).iloc[::-1].plot.barh(ax=ax[1], color="#55A868")
        ax[1].set(title="Item support (top 30)", xlabel="support")
        fig.tight_layout()
        os.makedirs(os.path.dirname(os.path.abspath(args.plot)), exist_ok=True)
        fig.savefig(args.plot, dpi=130)
        print(f"\n圖已存到 {args.plot}")


def cmd_split(args):
    baskets, tx, log = load_transactions(args)
    os.makedirs(args.out_dir, exist_ok=True)
    method = args.method
    if method == "time" and "time" not in tx.columns:
        print("沒有時間戳記，改用隨機切分。")
        method = "random"
    if method == "time":
        tx = tx.sort_values("time")
        cut = int(len(tx) * (1 - args.test_frac))
        train_ids, test_ids = tx["tid"].iloc[:cut], tx["tid"].iloc[cut:]
        cut_time = tx["time"].iloc[cut]
        desc = f"依時間切分：{cut_time} 之前為探勘集，之後為驗證集"
    else:
        rng = np.random.default_rng(args.seed)
        perm = rng.permutation(len(tx))
        cut = int(len(tx) * (1 - args.test_frac))
        train_ids, test_ids = tx["tid"].iloc[perm[:cut]], tx["tid"].iloc[perm[cut:]]
        desc = f"隨機切分（seed={args.seed}）"
    for name, ids in [("train", train_ids), ("test", test_ids)]:
        rows = [(t, i) for t in ids for i in sorted(baskets[t])]
        out = pd.DataFrame(rows, columns=["transaction_id", "item"])
        if "time" in tx.columns:
            out = out.merge(tx[["tid", "time"]].rename(columns={"tid": "transaction_id", "time": "timestamp"}),
                            on="transaction_id")
        out.to_csv(os.path.join(args.out_dir, f"{name}.csv"), index=False, encoding="utf-8")
    print(f"## 步驟 2：驗證安排\n- {desc}\n- 探勘集 {len(train_ids):,} 筆交易，驗證集 {len(test_ids):,} 筆交易"
          f"\n- 檔案：{args.out_dir}/train.csv、{args.out_dir}/test.csv（已清理）")


def mine_rules(baskets, a):
    from mlxtend.frequent_patterns import apriori, fpgrowth
    X = onehot(baskets)
    n = len(X)
    algo = fpgrowth if a.algo == "fpgrowth" else apriori
    fi = algo(X, min_support=a.min_support, use_colnames=True, max_len=a.max_len)
    sup = {frozenset(s): round(v * n) for s, v in zip(fi["itemsets"], fi["support"])}
    single = X.sum().to_dict()
    rows = []
    for I, cI in sup.items():
        if len(I) < 2:
            continue
        mx = max(single[i] for i in I)
        mn = min(single[i] for i in I)
        for k in range(1, len(I)):
            for A in combinations(sorted(I), k):
                A = frozenset(A)
                B = I - A
                m = measures(n, sup[A], sup[B], cI, mx, mn)
                rows.append(dict(rule=f"{fmt_set(A)} → {fmt_set(B)}", antecedent=json.dumps(sorted(A), ensure_ascii=False),
                                 consequent=json.dumps(sorted(B), ensure_ascii=False), length=len(I), **m))
    return pd.DataFrame(rows), sup, n


def closed_maximal(sup):
    by_len = {}
    for s in sup:
        by_len.setdefault(len(s), []).append(s)
    closed = maximal = 0
    for s, c in sup.items():
        supers = [t for t in by_len.get(len(s) + 1, []) if s < t]
        if not supers:
            maximal += 1
        if all(sup[t] < c for t in supers):
            closed += 1
    return closed, maximal


def cmd_mine(args):
    baskets, tx, log = load_transactions(args)
    rules, sup, n = mine_rules(baskets, args)
    os.makedirs(args.out_dir, exist_ok=True)
    sizes = pd.Series([len(s) for s in sup]).value_counts().sort_index()
    closed, maximal = closed_maximal(sup)
    print(f"## 步驟 3–4：探勘結果\n- N = {n:,}；演算法 = {args.algo}；min_support = {args.min_support}"
          f"（至少 {math.ceil(args.min_support * n)} 筆交易）；min_confidence = {args.min_confidence}；"
          f"min_lift = {args.min_lift}；max_len = {args.max_len}；hconf = {args.hconf}")
    print("- frequent itemsets：" + "，".join(f"{k}-itemset {v}" for k, v in sizes.items())
          + f"（共 {len(sup)}；closed {closed}，maximal {maximal}）")
    if rules.empty:
        print("\n沒有任何長度 ≥ 2 的 frequent itemset，因此沒有規則。")
        print_none_advice(baskets, args)
        return
    rules["excluded_reason"] = ""

    def mark(mask, reason):
        sel = mask & (rules["excluded_reason"] == "")
        rules.loc[sel, "excluded_reason"] = reason

    mark(rules["confidence"] < args.min_confidence, "confidence 未達門檻")
    mark(rules["lift"] <= args.min_lift, f"lift ≤ {args.min_lift}（未高於獨立期望）")
    if args.hconf:
        mark(rules["h_confidence"] < args.hconf, "cross-support（h-confidence 過低）")
    if not args.keep_redundant:
        kept = rules[rules["excluded_reason"] == ""]
        conf_of = {(r.antecedent, r.consequent): r.confidence for r in kept.itertuples()}
        red = []
        for r in kept.itertuples():
            A, B = json.loads(r.antecedent), r.consequent
            redundant = False
            for k in range(1, len(A)):
                for sub in combinations(A, k):
                    key = (json.dumps(list(sub), ensure_ascii=False), B)
                    if key in conf_of and conf_of[key] >= r.confidence - 1e-12:
                        redundant = True
                        break
                if redundant:
                    break
            red.append(redundant)
        mark(pd.Series(red, index=kept.index).reindex(rules.index, fill_value=False),
             "冗餘（較短前項的規則 confidence 已相同或更高）")

    keep = rules[rules["excluded_reason"] == ""].sort_values(["lift", "support"], ascending=False)
    drop = rules[rules["excluded_reason"] != ""]
    keep.drop(columns="excluded_reason").to_csv(os.path.join(args.out_dir, "rules.csv"), index=False, encoding="utf-8")
    drop.to_csv(os.path.join(args.out_dir, "excluded_rules.csv"), index=False, encoding="utf-8")
    print(f"- 產生規則 {len(rules)} 條 → 保留 {len(keep)} 條，排除 {len(drop)} 條")
    print("\n### 排除原因統計")
    for reason, c in drop["excluded_reason"].value_counts().items():
        print(f"- {reason}：{c}")
    cols = ["rule", "support", "count", "confidence", "lift", "leverage", "conviction", "kulczynski"]
    if args.hconf or "h_confidence" in keep:
        cols.append("h_confidence")
    print(f"\n### 候選規則（依 lift 排序，前 {args.top} 條）")
    print(keep[cols].head(args.top).round(4).to_string(index=False) if len(keep) else "（無）")
    print(f"\n完整表：{args.out_dir}/rules.csv；被排除規則與原因：{args.out_dir}/excluded_rules.csv")
    if len(keep) > args.too_many:
        print(f"\n> 規則數 {len(keep)} > {args.too_many}，可能「太多」。建議：提高 min_support 或 min_lift；"
              "max_len 改為 2；加上 --hconf 去除 cross-support；只看 closed / maximal itemsets 對應的規則；"
              "或只保留含特定商品（例如要促銷的品項）的規則。")
    elif len(keep) == 0:
        print_none_advice(baskets, args)


def print_none_advice(baskets, args):
    sizes = pd.Series([len(b) for b in baskets.values()])
    print(f"\n> 沒有規則通過篩選。單品項籃子占 {(sizes == 1).mean():.0%}、平均籃子大小 {sizes.mean():.2f}。建議：")
    print("  1) 逐步降低 min_support（例如減半），但每條規則至少要有約 10 筆交易支撐，否則多半是巧合；")
    print("  2) 降低 min_confidence，改以 lift 篩選；")
    print("  3) 若多數籃子只有 1–2 件，本質上就難有規則，這本身就是一個結論，不必硬找。")


def load_rules(path):
    r = pd.read_csv(path, encoding="utf-8")
    r["A"] = r["antecedent"].map(lambda s: frozenset(json.loads(s)))
    r["B"] = r["consequent"].map(lambda s: frozenset(json.loads(s)))
    return r


def count_sets(baskets, sets):
    out = {}
    bl = list(baskets.values())
    for s in sets:
        if s not in out:
            out[s] = sum(1 for b in bl if s <= b)
    return out


def cmd_validate(args):
    from scipy.stats import fisher_exact, false_discovery_control
    baskets, tx, log = load_transactions(args)
    rules = load_rules(args.rules)
    n = len(baskets)
    cnt = count_sets(baskets, list(rules["A"]) + list(rules["B"]) + [a | b for a, b in zip(rules["A"], rules["B"])])
    res = []
    for r in rules.itertuples():
        cA, cB, cAB = cnt[r.A], cnt[r.B], cnt[r.A | r.B]
        m = measures(n, cA, cB, cAB)
        table = [[cAB, cA - cAB], [cB - cAB, n - cA - cB + cAB]]
        p = fisher_exact(table, alternative="greater")[1] if cA and cB else 1.0
        res.append(dict(rule=r.rule, train_support=r.support, train_confidence=r.confidence, train_lift=r.lift,
                        test_count=cAB, test_support=m["support"], test_confidence=m["confidence"],
                        test_lift=m["lift"], p_value=p))
    out = pd.DataFrame(res)
    out["q_value_BH"] = false_discovery_control(out["p_value"].clip(upper=1), method="bh") if len(out) else []
    out["replicated"] = (out["test_lift"] > 1) & (out["q_value_BH"] < args.alpha)
    out = out.sort_values(["replicated", "test_lift"], ascending=[False, False])
    os.makedirs(args.out_dir, exist_ok=True)
    out.to_csv(os.path.join(args.out_dir, "validation.csv"), index=False, encoding="utf-8")
    rate = out["replicated"].mean() if len(out) else float("nan")
    print(f"## 步驟 5：可靠性評估（驗證資料 N = {n:,}）\n- 檢定：每條規則以 2×2 列聯表做單尾 Fisher exact test"
          f"（H0：前項與後項獨立；H1：正相關），{len(out)} 條規則以 Benjamini–Hochberg 控制 FDR = {args.alpha}")
    print(f"- 重現（驗證集 lift > 1 且 q < {args.alpha}）：{int(out['replicated'].sum())} / {len(out)}（{rate:.0%}）")
    print("\n" + out.drop(columns=["train_support"]).round(4).to_string(index=False))
    print(f"\n完整表：{args.out_dir}/validation.csv")


def cmd_permtest(args):
    baskets, tx, log = load_transactions(args)
    X = onehot(baskets)
    rng = np.random.default_rng(args.seed)

    def n_rules(bk):
        r, _, _ = mine_rules(bk, args)
        if r.empty:
            return 0
        ok = (r["confidence"] >= args.min_confidence) & (r["lift"] > args.min_lift)
        if args.hconf:
            ok &= r["h_confidence"] >= args.hconf
        return int(ok.sum())

    obs = n_rules(baskets)
    null = []
    for _ in range(args.n_perm):
        Y = X.apply(lambda c: rng.permutation(c.values))  # 每欄獨立打亂：保留商品 support，切斷商品間關聯
        bk = {t: frozenset(Y.columns[row]) for t, row in zip(Y.index, Y.values)}
        null.append(n_rules(bk))
    null = np.array(null)
    print(f"## 步驟 5：置換檢定（column permutation，{args.n_perm} 次）\n"
          f"- 觀察資料通過門檻的規則數：{obs}\n- 虛無資料（商品彼此獨立）規則數：平均 {null.mean():.1f}，"
          f"95 百分位 {np.percentile(null, 95):.0f}，最大 {null.max()}")
    if obs:
        print(f"- 估計規則中「純屬巧合」的比例 ≈ {min(1, null.mean() / obs):.0%}（平均虛無規則數 / 觀察規則數）")
    print("- 規則數以 confidence / lift / hconf 篩選計算，未做冗餘移除，因此可能比 mine 保留的條數多。")
    print("- 注意：逐欄打亂保留了商品 support，但沒有保留每筆交易的籃子大小；"
          "swap randomization 能同時保留兩者，但計算量大（Tan et al. 10.4.2）。")


def cmd_stratify(args):
    args.keep_cols = [args.by] if args.by not in ("weekend", "weekday", "month", "hour") else []
    baskets, tx, log = load_transactions(args)
    rules = load_rules(args.rules)
    if args.by in ("weekend", "weekday", "month", "hour"):
        if "time" not in tx.columns:
            sys.exit("資料沒有時間戳記，無法用時間分層；請改用 --by <欄位名稱>。")
        t = tx["time"]
        tx["stratum"] = {"weekend": np.where(t.dt.dayofweek >= 5, "週末", "平日"),
                         "weekday": t.dt.day_name(), "month": t.dt.to_period("M").astype(str),
                         "hour": pd.cut(t.dt.hour, [-1, 11, 17, 24], labels=["上午", "下午", "晚上"]).astype(str)}[args.by]
    else:
        tx["stratum"] = tx[args.by].astype(str)
    out = []
    for r in rules.itertuples():
        row = {"rule": r.rule}
        n_all = len(baskets)
        c = count_sets(baskets, [r.A, r.B, r.A | r.B])
        pooled = measures(n_all, c[r.A], c[r.B], c[r.A | r.B])["lift"]
        row["pooled_lift"] = pooled
        signs = []
        for s, ids in tx.groupby("stratum")["tid"]:
            sub = {t: baskets[t] for t in ids}
            c = count_sets(sub, [r.A, r.B, r.A | r.B])
            if c[r.A] < args.min_count or c[r.B] == 0:
                row[f"lift[{s}]"] = float("nan")
                continue
            l = measures(len(sub), c[r.A], c[r.B], c[r.A | r.B])["lift"]
            row[f"lift[{s}]"] = l
            signs.append(np.sign(math.log(l)) if l > 0 else -1)
        ps = np.sign(math.log(pooled)) if pooled > 0 else -1
        row["simpson_reversal"] = bool(signs) and all(s != ps for s in signs if s != 0)
        row["inconsistent"] = bool(signs) and any(s != ps for s in signs)
        out.append(row)
    out = pd.DataFrame(out)
    print(f"## 步驟 5：分層檢查 Simpson's paradox（依 {args.by}）")
    print(f"- 每層前項至少 {args.min_count} 筆交易才計算 lift，否則記為 NaN")
    print(f"- 全部分層方向都與合併資料相反（Simpson's paradox）：{int(out['simpson_reversal'].sum())} 條；"
          f"部分分層方向不一致：{int(out['inconsistent'].sum())} 條")
    print("\n" + out.round(3).to_string(index=False))


# ----------------------------------------------------------------------------- CLI

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("data", help="交易資料（CSV / XLSX）")
        sp.add_argument("--format", choices=["auto", "long", "basket", "wide"], default="auto",
                        help="long=一列一品項；basket=一列一籃（品項以 --sep 分隔）；wide=0/1 矩陣")
        sp.add_argument("--tid-col")
        sp.add_argument("--item-col")
        sp.add_argument("--time-col")
        sp.add_argument("--qty-col")
        sp.add_argument("--sep", help="basket 格式的品項分隔符號（預設逗號）")
        sp.add_argument("--encoding")
        sp.add_argument("--out-dir", default="arules_output")

    def mining(sp):
        sp.add_argument("--min-support", type=float, required=True)
        sp.add_argument("--min-confidence", type=float, default=0.3)
        sp.add_argument("--min-lift", type=float, default=1.0, help="保留 lift 大於此值的規則（篩選用）")
        sp.add_argument("--max-len", type=int, default=3, help="規則（前項＋後項）最多幾個品項")
        sp.add_argument("--algo", choices=["fpgrowth", "apriori"], default="fpgrowth")
        sp.add_argument("--hconf", type=float, help="h-confidence 門檻，用來去除 cross-support 規則")

    sp = sub.add_parser("profile"); common(sp)
    sp.add_argument("--top", type=int, default=15)
    sp.add_argument("--plot", help="輸出圖檔路徑（經使用者同意後才使用）")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("split"); common(sp)
    sp.add_argument("--method", choices=["time", "random"], default="time")
    sp.add_argument("--test-frac", type=float, default=0.3)
    sp.add_argument("--seed", type=int, default=42)
    sp.set_defaults(func=cmd_split)

    sp = sub.add_parser("mine"); common(sp); mining(sp)
    sp.add_argument("--keep-redundant", action="store_true")
    sp.add_argument("--top", type=int, default=20)
    sp.add_argument("--too-many", type=int, default=100)
    sp.set_defaults(func=cmd_mine)

    sp = sub.add_parser("validate"); common(sp)
    sp.add_argument("--rules", required=True, help="mine 輸出的 rules.csv")
    sp.add_argument("--alpha", type=float, default=0.05)
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("permtest"); common(sp); mining(sp)
    sp.add_argument("--n-perm", type=int, default=20)
    sp.add_argument("--seed", type=int, default=42)
    sp.set_defaults(func=cmd_permtest)

    sp = sub.add_parser("stratify"); common(sp)
    sp.add_argument("--rules", required=True)
    sp.add_argument("--by", required=True, help="weekend / weekday / month / hour，或資料中的欄位名稱（例如 store）")
    sp.add_argument("--min-count", type=int, default=10)
    sp.set_defaults(func=cmd_stratify)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
