"""關聯規則（association rules）分析腳本：對應 SKILL.md 的步驟 1–6。

子命令
  encode    步驟 1（問卷）：問卷寬表 → 「欄位=值」項目（長格式），處理代碼、缺失、跳題、分組、合併
  profile   步驟 1：清理資料、建立購物籃，輸出交易數、商品數、籃子大小分布、商品頻率、建議參數
  split     步驟 2：依時間、日期、隨機或顧客 / 受訪者分組切分「探勘集 / 驗證集」
  mine      步驟 3–4：找 frequent itemsets、產生規則、篩選，輸出候選規則表與被排除規則（含原因）
  validate  步驟 5：在驗證集重算指標，做 Fisher exact test 與 Benjamini–Hochberg FDR 校正
  permtest  步驟 5：欄位置換（permutation）虛無分布，估計「純機率下會找到幾條規則」
  stratify  步驟 5：分層（週末 / 月份 / 時段 / 自訂欄位 / 問卷題目）檢查 Simpson's paradox
  report    步驟 6：把這次分析的每一步（含程式碼）整理成 report.qmd 與 report.html

每次執行都會把指令與輸出記錄到 <out-dir>/run_log.json，report 依此產生報告。

所有計算都以 Tan, Steinbach, Karpatne & Kumar (2019), Introduction to Data Mining (2nd ed.)
第 5、6 章與 10.4 節的定義為準，詳見 references/measures.md 與 references/validation.md。

範例（交易資料）
  python scripts/run_arules.py profile data/sample_baskets.csv
  python scripts/run_arules.py split data/sample_baskets.csv --method time --test-frac 0.3
  python scripts/run_arules.py mine arules_output/train.csv --min-support 0.015 --min-confidence 0.3
  python scripts/run_arules.py validate arules_output/test.csv --rules arules_output/rules.csv
  python scripts/run_arules.py report --notes arules_output/interpretation.md

範例（問卷資料）
  python scripts/run_arules.py encode data/sample_survey.csv --codebook data/sample_codebook.csv \\
      --bins 年齡=18,30,45,65 --exclude 縣市
  python scripts/run_arules.py split arules_output/items.csv --method random
  python scripts/run_arules.py mine arules_output/train.csv --min-count 30 --rhs 政策A態度 --max-lhs 2 --max-rhs 1
"""
import argparse
import io
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from itertools import combinations

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TID_NAMES = ["transaction_id", "transactionid", "tid", "invoice", "invoiceno", "invoice_no",
             "order_id", "orderid", "basket_id", "member_number", "receipt", "交易編號", "訂單編號",
             "respondent_id", "受訪者編號"]
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
    s = " ".join(str(x).split())
    return s if "=" in s else s.lower()   # 「欄位=值」項目（問卷）保留大小寫


def md_table(df, digits=4):
    """DataFrame → Markdown pipe table（終端機可讀，報告可直接渲染）。"""
    if df is None or len(df) == 0:
        return "（無）"
    d = df.copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else (f"{v:.{digits}f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:.3g}"))
    cols = [str(c) for c in d.columns]
    rows = [[str(v).replace("|", "\\|") for v in r] for r in d.itertuples(index=False)]
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def item_matches(item, keys):
    """item 是否屬於 keys 中的某個商品名稱或題目（題目以「題目=」前綴比對）。"""
    return any(item == k or item.startswith(k + "=") for k in keys)


def item_attr(item):
    return item.split("=", 1)[0] if "=" in item else None


def load_transactions(args):
    """回傳 (baskets: dict[tid] -> frozenset, tx: DataFrame[tid, time, ...], log: list[str])"""
    df = read_table(args.data, getattr(args, "encoding", None))
    log = [f"讀入 {len(df):,} 列、欄位 {list(df.columns)}"]
    fmt = getattr(args, "format", "auto")
    if fmt == "survey":
        sys.exit("問卷寬表（一列一位受訪者）請先用 encode 轉成「欄位=值」項目，再做 profile / mine。")
    tid_c = pick(df.columns, TID_NAMES, getattr(args, "tid_col", None))
    item_c = pick(df.columns, ITEM_NAMES, getattr(args, "item_col", None))
    if fmt == "auto":
        if tid_c and item_c:
            fmt = "long"
        elif df.shape[1] >= 2 and all(set(df[c].unique()) <= {"0", "1", "True", "False", "true", "false", ""}
                                      for c in df.columns if c != tid_c):
            fmt = "wide"
        elif df.shape[1] > 3:
            sys.exit("看起來不是交易資料（找不到交易編號 / 品項欄位，也不是 0/1 矩陣）。"
                     "若是問卷寬表，請先用 encode；若是交易資料，請用 --tid-col / --item-col 指定欄位。")
        else:
            fmt = "basket"
    log.append(f"判定資料格式：{fmt}")

    # 分析範圍：--where 欄位=值（資料中有該欄位時，在列層級篩選）
    item_where = []
    for w in getattr(args, "where", None) or []:
        col, _, val = w.partition("=")
        if col in df.columns:
            before = len(df)
            df = df[df[col].astype(str).str.strip() == val].copy()
            log.append(f"分析範圍 {w}：保留 {len(df):,} / {before:,} 列")
        else:
            item_where.append(w)

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

    if getattr(args, "item_map", None):
        mp = read_table(args.item_map)
        src, dst = mp.columns[0], mp.columns[-1]
        mapping = {norm_item(a): norm_item(b) for a, b in zip(mp[src], mp[dst])}
        hit = df["item"].isin(mapping)
        df["item"] = df["item"].map(lambda x: mapping.get(x, x))
        log.append(f"項目粒度：依 {args.item_map} 換算 {int(hit.sum())} 列（{len(set(mapping))} 個原始項目 → "
                   f"{len(set(mapping.values()))} 個新項目）；未列在對照表的項目維持原樣")

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

    # 分析範圍：期間
    d_from, d_to = getattr(args, "date_from", None), getattr(args, "date_to", None)
    if d_from or d_to:
        if "time" not in tx.columns:
            sys.exit("資料沒有時間戳記，無法用 --date-from / --date-to 限定期間。")
        ok = pd.Series(True, index=tx.index)
        if d_from:
            ok &= tx["time"] >= pd.Timestamp(d_from)
        if d_to:
            ok &= tx["time"] < pd.Timestamp(d_to) + pd.Timedelta(days=1)
        log.append(f"分析範圍 {d_from or '開始'} ～ {d_to or '結束'}：保留 {int(ok.sum()):,} / {len(tx):,} 筆交易")
        tx = tx[ok].reset_index(drop=True)
        baskets = {t: baskets[t] for t in tx["tid"]}
    # 分析範圍：資料中沒有該欄位時，視為項目條件（例如問卷項目「就業狀態=就業」），並把條件項目本身移除
    for w in item_where:
        before = len(baskets)
        baskets = {t: b - {w} for t, b in baskets.items() if w in b}
        log.append(f"分析範圍 {w}：保留 {len(baskets):,} / {before:,} 個觀測單位（支持度的分母改為這些單位）")
        tx = tx[tx["tid"].isin(baskets)].reset_index(drop=True)
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
    freq = pd.DataFrame({"item": counts.index, "count": counts.values, "support": sup.values})
    print(f"\n### 商品頻率（前 {k} 名）\n")
    print(md_table(freq.head(k)))
    print(f"\n### 商品頻率（後 {min(k, 10)} 名）\n")
    print(md_table(freq.tail(min(k, 10))))
    q = sup.quantile([0, .25, .5, .75, .9, 1])
    print("\n### 商品 support 分布（分位數）")
    print("  " + "  ".join(f"{int(p * 100)}%={v:.4f}" for p, v in q.items()))
    skew = sup.max() / sup.median()
    if skew > 5:
        print(f"> support 分布偏斜（最大 / 中位數 = {skew:.1f}）：低門檻時容易產生 cross-support 規則"
              "（Tan et al. 5.8），建議在 mine 加上 --hconf。")

    med = float(sup.median())
    std = max(10 / n, min(0.05, 2 * med ** 2))
    print("\n### 建議參數（供參考，使用者可自行覆寫）\n")
    print("| 參數 | 寬鬆 | 標準 | 嚴格 | 理由 |\n|---|---|---|---|---|")
    print(f"| min_support | {std / 2:.4f}（{math.ceil(std / 2 * n)} 筆） | {std:.4f}（{math.ceil(std * n)} 筆） | "
          f"{std * 2:.4f}（{math.ceil(std * 2 * n)} 筆） | 標準值 = max(10/N, 2×(中位數 support)²)："
          f"讓兩個中位數頻率商品在 lift≈2 時仍可被找到，且每條規則至少有約 {std * n:.0f} 筆交易支撐 |")
    print("| min_confidence | 0.20 | 0.30 | 0.50 | confidence 要和後項本身的 support 比較才有意義，"
          "門檻不宜過高，主要篩選交給 lift |")
    print("| min_lift（篩選，非探勘門檻） | 1.0 | 1.2 | 1.5 | lift>1 代表比獨立情況更常一起出現 |")
    print("| 規則長度（--max-lhs / --max-rhs） | 前件 3、後件 2 | 前件 2、後件 1 | 前件 1、後件 1 | 越長越難解讀，也越容易是巧合 |")
    hc = "0.05" if skew > 5 else "不需要"
    print(f"| hconf（h-confidence） | — | {hc} | 0.10 | 去除高頻商品與低頻商品硬湊的 cross-support 規則 |")
    print("| 演算法 | — | auto（= FP-growth） | — | Apriori 與 FP-growth 結果完全相同，只差速度 |")

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
        args._outputs.append(args.plot)
        print(f"\n圖已存到 {args.plot}")


def split_ids(ids, groups, frac, seed):
    """隨機切分；給 groups 時以群組為單位（同一顧客 / 受訪者只會落在其中一邊）。"""
    rng = np.random.default_rng(seed)
    if groups is None:
        perm = rng.permutation(len(ids))
        cut = int(len(ids) * (1 - frac))
        return ids.iloc[perm[:cut]], ids.iloc[perm[cut:]]
    g = groups.reset_index(drop=True)
    uniq = g.unique()
    perm = rng.permutation(len(uniq))
    test_g = set(uniq[perm[int(len(uniq) * (1 - frac)):]])
    mask = g.isin(test_g).values
    return ids[~mask], ids[mask]


def cmd_split_survey(args):
    """問卷寬表在編碼前切分（列層級），供 encode --cuts-out / --cuts-in 使用。"""
    df = read_table(args.data, args.encoding)
    rows = pd.Series(range(len(df)))
    groups = df[args.group_col].reset_index(drop=True) if args.method == "group" else None
    tr, te = split_ids(rows, groups, args.test_frac, args.seed)
    os.makedirs(args.out_dir, exist_ok=True)
    for name, idx in [("survey_train", tr), ("survey_test", te)]:
        path = os.path.join(args.out_dir, f"{name}.csv")
        df.iloc[list(idx)].to_csv(path, index=False, encoding="utf-8")
        args._outputs.append(path)
    how = f"依 {args.group_col} 分組隨機切分" if groups is not None else "隨機切分（列層級）"
    print(f"## 步驟 2：驗證安排（問卷，編碼前切分）\n- {how}，seed={args.seed}，驗證比例 {args.test_frac:.0%}\n"
          f"- 探勘集 {len(tr):,} 位，驗證集 {len(te):,} 位\n"
          f"- 檔案：{args.out_dir}/survey_train.csv、{args.out_dir}/survey_test.csv\n"
          "- 下一步：encode survey_train.csv --cuts-out cuts.json 決定切點，再以 --cuts-in 套用到 survey_test.csv")


def cmd_split(args):
    if args.format == "survey":
        return cmd_split_survey(args)
    if args.method == "group":
        if not args.group_col:
            sys.exit("--method group 需要 --group-col（例如顧客編號）")
        args.keep_cols = [args.group_col]
    baskets, tx, log = load_transactions(args)
    os.makedirs(args.out_dir, exist_ok=True)
    method = args.method
    if args.split_date:
        method = "date"
    if method in ("time", "date") and "time" not in tx.columns:
        print("沒有時間戳記，改用隨機切分。")
        method = "random"
    if method == "date":
        cut_time = pd.Timestamp(args.split_date)
        early = tx["time"] < cut_time
        train_ids, test_ids = tx.loc[early, "tid"], tx.loc[~early, "tid"]
        desc = f"依指定日期切分：{cut_time.date()} 之前為探勘集，當天起為驗證集"
    elif method == "group":
        if args.group_col not in tx.columns:
            sys.exit(f"找不到分組欄位 {args.group_col!r}")
        train_ids, test_ids = split_ids(tx["tid"], tx[args.group_col].astype(str), args.test_frac, args.seed)
        desc = (f"依 {args.group_col} 分組隨機切分（seed={args.seed}）：同一個 {args.group_col} 只會出現在其中一邊，"
                "避免同一位顧客的習慣同時出現在探勘集與驗證集")
    elif method == "time":
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
        path = os.path.join(args.out_dir, f"{name}.csv")
        out.to_csv(path, index=False, encoding="utf-8")
        args._outputs.append(path)
    print(f"## 步驟 2：驗證安排\n- {desc}\n- 探勘集 {len(train_ids):,} 筆，驗證集 {len(test_ids):,} 筆"
          f"\n- 檔案：{args.out_dir}/train.csv、{args.out_dir}/test.csv（已清理）")
    if min(len(train_ids), len(test_ids)) < 200:
        print("> 注意：其中一邊少於 200 筆，驗證的檢定力有限；未重現不一定代表規則不存在。")


def resolve_mining_args(a, n):
    """整理探勘參數：筆數 ↔ 比例、規則長度、演算法。"""
    if a.min_count:
        a.min_support = a.min_count / n
    if not a.min_support:
        sys.exit("請用 --min-support（比例）或 --min-count（筆數）指定最低支持度。")
    if a.max_lhs or a.max_rhs:
        a.max_lhs = a.max_lhs or max(1, a.max_len - 1)
        a.max_rhs = a.max_rhs or 1
        a.max_len = a.max_lhs + a.max_rhs
    a.algo_used = "fpgrowth" if a.algo == "auto" else a.algo


def mine_rules(baskets, a):
    from mlxtend.frequent_patterns import apriori, fpgrowth
    X = onehot(baskets)
    n = len(X)
    resolve_mining_args(a, n)
    algo = fpgrowth if a.algo_used == "fpgrowth" else apriori
    fi = algo(X, min_support=a.min_support, use_colnames=True, max_len=a.max_len)
    sup = {frozenset(s): round(v * n) for s, v in zip(fi["itemsets"], fi["support"])}
    single = X.sum().to_dict()
    rhs, lhs_cols = a.rhs or [], a.lhs_cols or []
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
                # 規則形式的限制：長度、指定後件、允許的前件
                if a.max_lhs and (len(A) > a.max_lhs or len(B) > a.max_rhs):
                    continue
                if rhs and not all(item_matches(i, rhs) for i in B):
                    continue
                if lhs_cols and not all(item_matches(i, lhs_cols) for i in A):
                    continue
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


def functional_deps(baskets):
    """「欄位=值」項目之間的函數關係 X → Y：每個 X 值只對應一個 Y 值（例如 縣市 → 區域）。"""
    vals = []
    for b in baskets.values():
        attrs = {}
        for i in b:
            at = item_attr(i)
            if at:
                attrs.setdefault(at, set()).add(i)
        vals.append({k: next(iter(v)) for k, v in attrs.items() if len(v) == 1})
    cols = sorted({k for d in vals for k in d})
    deps = set()
    for x in cols:
        for y in cols:
            if x == y:
                continue
            seen, cnt, ok = {}, {}, True
            for d in vals:
                if x in d and y in d:
                    cnt[d[x]] = cnt.get(d[x], 0) + 1
                    if seen.setdefault(d[x], d[y]) != d[y]:
                        ok = False
                        break
            # 每個 X 值至少出現 5 次才算數，避免稀有值湊巧只對應一個 Y 值
            if ok and len(set(seen.values())) > 1 and cnt and min(cnt.values()) >= 5:
                deps.add((x, y))
    return deps


def describe_mining(args, n):
    rl = (f"前件最多 {args.max_lhs} 項、後件最多 {args.max_rhs} 項" if args.max_lhs
          else f"規則總長度最多 {args.max_len} 項")
    parts = [f"N = {n:,}", f"演算法 = {args.algo}（實際 {args.algo_used}）",
             f"min_support = {args.min_support:.4g}（至少 {math.ceil(args.min_support * n - 1e-9)} 筆）",
             f"min_confidence = {args.min_confidence}", f"min_lift = {args.min_lift}", rl, f"hconf = {args.hconf}"]
    if args.rhs:
        parts.append(f"指定後件 = {args.rhs}")
    if args.lhs_cols:
        parts.append(f"允許的前件 = {args.lhs_cols}")
    if args.min_lhs_count:
        parts.append(f"最低前件筆數 = {args.min_lhs_count}")
    return "；".join(parts)


def cmd_mine(args):
    baskets, tx, log = load_transactions(args)
    rules, sup, n = mine_rules(baskets, args)
    os.makedirs(args.out_dir, exist_ok=True)
    sizes = pd.Series([len(s) for s in sup]).value_counts().sort_index()
    closed, maximal = closed_maximal(sup)
    print(f"## 步驟 3–4：探勘結果\n- {describe_mining(args, n)}")
    print("- frequent itemsets：" + "，".join(f"{k}-itemset {v}" for k, v in sizes.items())
          + f"（共 {len(sup)}；closed {closed}，maximal {maximal}）")
    if rules.empty:
        print("\n沒有符合條件的規則（沒有長度 ≥ 2 的 frequent itemset，或規則形式限制排除了全部）。")
        print_none_advice(baskets, args)
        return
    rules["excluded_reason"] = ""

    def mark(mask, reason):
        sel = mask & (rules["excluded_reason"] == "")
        rules.loc[sel, "excluded_reason"] = reason

    # 定義上必然成立的規則（例如 縣市=台南市 → 區域=南部）：不是新發現
    deps = functional_deps(baskets)
    if deps:
        def definitional(r):
            A, B = json.loads(r.antecedent), json.loads(r.consequent)
            return any((item_attr(x), item_attr(y)) in deps for x in A for y in B)
        mark(pd.Series([definitional(r) for r in rules.itertuples()], index=rules.index),
             "定義性（欄位間有函數關係，必然成立）")
    if args.flag_definitional:
        mark(rules["confidence"] >= 0.99, "疑似定義性（confidence ≥ 0.99，請確認是否必然成立）")
    if args.min_lhs_count:
        mark(rules["antecedent_support"] * n < args.min_lhs_count - 1e-9, f"前件筆數 < {args.min_lhs_count}")
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

    sort_key = {"lift": ["lift", "support"], "support": ["support", "lift"],
                "confidence": ["confidence", "lift"]}[args.sort]
    keep = rules[rules["excluded_reason"] == ""].sort_values(sort_key, ascending=False)
    drop = rules[rules["excluded_reason"] != ""]
    for name, d in [("rules.csv", keep.drop(columns="excluded_reason")), ("excluded_rules.csv", drop)]:
        path = os.path.join(args.out_dir, name)
        d.to_csv(path, index=False, encoding="utf-8")
        args._outputs.append(path)
    print(f"- 產生規則 {len(rules)} 條 → 保留 {len(keep)} 條，排除 {len(drop)} 條")
    print("\n### 排除原因統計")
    for reason, c in drop["excluded_reason"].value_counts().items():
        print(f"- {reason}：{c}")
    cols = ["rule", "support", "count", "confidence", "consequent_support", "lift", "leverage", "conviction", "kulczynski"]
    if args.hconf or "h_confidence" in keep:
        cols.append("h_confidence")
    print(f"\n### 候選規則（依 {args.sort} 排序，前 {args.top} 條）\n")
    print("consequent_support 是後件的基準比例：confidence 要和它比較才知道前件有沒有「加分」。\n")
    print(md_table(keep[cols].head(args.top)))
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
    out["replicated"] = (out["test_lift"] > 1) & (out["q_value_BH"] < args.alpha) & (out["test_count"] >= args.min_test_count)
    out = out.sort_values(["replicated", "test_lift"], ascending=[False, False])
    os.makedirs(args.out_dir, exist_ok=True)
    path = os.path.join(args.out_dir, "validation.csv")
    out.to_csv(path, index=False, encoding="utf-8")
    args._outputs.append(path)
    rate = out["replicated"].mean() if len(out) else float("nan")
    crit = f"驗證集 lift > 1 且 q < {args.alpha}" + (f" 且驗證集共同出現 ≥ {args.min_test_count} 筆" if args.min_test_count else "")
    print(f"## 步驟 5：可靠性評估（驗證資料 N = {n:,}）\n- 驗證標準（探勘前已決定）：{crit}"
          f"\n- 檢定：每條規則以 2×2 列聯表做單尾 Fisher exact test"
          f"（H0：前項與後項獨立；H1：正相關），{len(out)} 條規則以 Benjamini–Hochberg 控制 FDR = {args.alpha}")
    print(f"- 重現：{int(out['replicated'].sum())} / {len(out)}（{rate:.0%}）\n")
    print(md_table(out.drop(columns=["train_support"])))
    print(f"\n完整表：{args.out_dir}/validation.csv")


def cmd_permtest(args):
    baskets, tx, log = load_transactions(args)
    X = onehot(baskets)
    rng = np.random.default_rng(args.seed)

    def n_rules(bk):
        if args.min_count:   # 以筆數設定時，維持相同的筆數門檻
            args.min_support = None
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
    elif args.by in tx.columns:
        tx["stratum"] = tx[args.by].astype(str)
    else:
        # 問卷：以「題目=值」項目分層；分層題目本身的項目從籃子中移除，含該題目的規則略過
        pre = args.by + "="
        if not any(i.startswith(pre) for b in baskets.values() for i in b):
            sys.exit(f"找不到分層依據 {args.by!r}：既不是時間分層、資料欄位，也不是「題目=值」項目的題目。")
        val = {t: next((i[len(pre):] for i in b if i.startswith(pre)), "（未回答）") for t, b in baskets.items()}
        tx["stratum"] = tx["tid"].map(val)
        baskets = {t: frozenset(i for i in b if not i.startswith(pre)) for t, b in baskets.items()}
        skip = rules["rule"].str.contains(re.escape(pre))
        if skip.any():
            print(f"- 略過 {int(skip.sum())} 條含有 {args.by} 的規則（分層變數不能同時出現在規則裡）")
        rules = rules[~skip]
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
    print(f"- 每層前項至少 {args.min_count} 筆才計算 lift，否則留白")
    if out.empty:
        print("- 沒有可檢查的規則")
        return
    print(f"- 全部分層方向都與合併資料相反（Simpson's paradox）：{int(out['simpson_reversal'].sum())} 條；"
          f"部分分層方向不一致：{int(out['inconsistent'].sum())} 條\n")
    print(md_table(out, digits=3))
    path = os.path.join(args.out_dir, "stratify.csv")
    os.makedirs(args.out_dir, exist_ok=True)
    out.to_csv(path, index=False, encoding="utf-8")
    args._outputs.append(path)


# ----------------------------------------------------------------------------- 問卷編碼（步驟 1，問卷）

ID_HINTS = ["id", "編號", "respondent", "受訪者"]
WEIGHT_NAMES = ["weight", "wt", "wgt", "權重", "加權", "weights"]
TEXT_HINTS = ["意見", "說明", "備註", "其他", "comment", "opinion", "note", "remark", "open", "text"]
MISSING_LABEL = {"refused": "拒答", "dont_know": "不知道", "na": "不適用"}


def to_num(s):
    return pd.to_numeric(s, errors="coerce")


def fmt_num(v):
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def make_cuts(x, spec):
    """spec：'18,30,45,65'（領域切點）、'quantile:4'（等頻）、'width:10'（等寬）。回傳遞增切點。"""
    x = x.dropna()
    integer = bool((x == x.round()).all())
    if spec.startswith("quantile:"):
        k = int(spec.split(":")[1])
        cuts = np.quantile(x, [i / k for i in range(1, k)])
        cuts = np.round(cuts) if integer else cuts
    elif spec.startswith("width:"):
        w = float(spec.split(":")[1])
        cuts = np.arange(math.floor(x.min() / w) * w + w, x.max() + 1e-9, w)
    else:
        cuts = [float(v) for v in spec.split(",")]
    return sorted(set(float(c) for c in cuts))


def bin_labels(x, cuts):
    integer = bool((x.dropna() == x.dropna().round()).all())

    def lab(v):
        if pd.isna(v):
            return np.nan
        if v < cuts[0]:
            return f"<{fmt_num(cuts[0])}"
        if v >= cuts[-1]:
            return f"≥{fmt_num(cuts[-1])}"
        for a, b in zip(cuts, cuts[1:]):
            if a <= v < b:
                return f"{fmt_num(a)}–{fmt_num(b - 1)}" if integer and float(a).is_integer() and float(b).is_integer() \
                    else f"[{fmt_num(a)},{fmt_num(b)})"
    order = [f"<{fmt_num(cuts[0])}"] + [
        (f"{fmt_num(a)}–{fmt_num(b - 1)}" if integer and float(a).is_integer() and float(b).is_integer()
         else f"[{fmt_num(a)},{fmt_num(b)})") for a, b in zip(cuts, cuts[1:])] + [f"≥{fmt_num(cuts[-1])}"]
    return x.map(lab), order


def parse_kv(specs, sep="="):
    out = {}
    for s in specs or []:
        k, _, v = s.partition(sep)
        out[k.strip()] = v.strip()
    return out


def cmd_encode(args):
    df = read_table(args.data, args.encoding).apply(lambda s: s.astype(str).str.strip())
    df = df.replace({"nan": "", "NaN": "", "None": ""})
    n0 = len(df)
    cols = list(df.columns)
    notes, checks = [], []

    # --- 分析單位：識別欄位
    id_c = args.id_col or next((c for c in cols if any(h in c.lower() for h in ID_HINTS)
                                and df[c].nunique() >= 0.9 * n0), None)
    if id_c:
        dup = int(df[id_c].duplicated().sum())
        if dup:
            checks.append(f"⚠️ {id_c} 有 {dup} 筆重複：同一位受訪者可能有多次訪問（多波次）。必須先決定分析單位是"
                          "「人」還是「人 × 訪問波次」；目前把每一列當成一個觀測單位。")
            tid = df[id_c] + "#" + df.groupby(id_c).cumcount().add(1).astype(str)
        else:
            tid = df[id_c]
    else:
        tid = pd.Series([f"R{i + 1}" for i in range(n0)], index=df.index)
        notes.append("沒有找到識別欄位，以列號作為受訪者編號")

    # --- codebook：代碼 → 標籤，並標記特殊代碼
    cb = {}
    if args.codebook:
        c = read_table(args.codebook)
        for r in c.itertuples(index=False):
            col, code, label = str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip()
            miss = str(r[3]).strip() if len(r) > 3 else ""
            cb.setdefault(col, {})[code] = (label, miss)

    weight_c = args.weight_col or pick(cols, WEIGHT_NAMES, None)
    multi = set(args.multi or [])
    bins = parse_kv(args.bins)
    cuts_in = json.load(open(args.cuts_in, encoding="utf-8")) if args.cuts_in else {}
    bins_cols = set(bins) | set(cuts_in)

    # 套用 codebook（全部欄位，供 --where 使用）
    labels = {}
    for c in cols:
        s = df[c]
        if c in cb:
            mp = cb[c]
            unknown = sorted(set(s[(s != "") & ~s.isin(mp)]))
            if unknown:
                checks.append(f"⚠️ {c} 有不在 codebook 裡的代碼 {unknown[:5]}，保留原值，請確認意義")
            sp = s.map(lambda v: mp[v][1] if v in mp else "")
            lab = s.map(lambda v: mp[v][0] if v in mp else v)
            if args.missing_codes == "drop":
                lab = lab.where(sp == "", "")
            labels[c] = lab
        else:
            labels[c] = s
    L = pd.DataFrame(labels)

    # --- 分析範圍（--where 欄位=值，可比對標籤或原始代碼）
    keep = pd.Series(True, index=df.index)
    where_cols = []
    for w in args.where or []:
        col, _, val = w.partition("=")
        if col not in cols:
            sys.exit(f"--where 找不到欄位 {col!r}")
        keep &= (L[col] == val) | (df[col] == val)
        where_cols.append(col)
    L, df, tid = L[keep], df[keep], tid[keep]
    n = len(L)

    # --- 欄位角色
    roles, excluded = {}, {}
    for c in cols:
        s = L[c]
        nonblank = s[s != ""]
        num = to_num(nonblank)
        if c == id_c:
            excluded[c] = "識別欄位（只用來追蹤，不當項目）"
        elif c == weight_c:
            excluded[c] = "調查權重（設計資訊，不當項目）"
        elif args.include and c not in args.include:
            excluded[c] = "未列在 --include"
        elif c in (args.exclude or []):
            excluded[c] = "使用者排除"
        elif c in where_cols and nonblank.nunique() <= 1:
            excluded[c] = "已用於限定分析範圍（篩選後只剩一個值）"
        elif c in multi:
            roles[c] = "複選題"
        elif c in bins_cols:
            roles[c] = "連續（分組）"
        elif any(h in c.lower() for h in TEXT_HINTS) or (num.isna().all() and len(nonblank) > 20
                                                          and nonblank.nunique() > 0.5 * len(nonblank)):
            excluded[c] = "自由文字（第一版不自動編碼，需要另行人工編碼）"
        elif len(nonblank) and num.notna().all() and c not in cb and nonblank.nunique() > 10:
            q = num.quantile([0, .25, .5, .75, 1]).round(2).tolist()
            excluded[c] = (f"連續數值，需要先分組：用 --bins {c}=切點（例如 {c}=quantile:4）；"
                           f"分位數 0/25/50/75/100% = {q}")
        elif len(nonblank) and nonblank.nunique() == len(nonblank) == n:
            excluded[c] = "每列都不同，疑似識別碼"
        elif nonblank.nunique() <= 1:
            excluded[c] = "只有一個值（常數），不提供資訊"
        else:
            roles[c] = "二元" if nonblank.nunique() == 2 else "類別"

    # --- 粒度換算、合併、分組
    recode_log, merge_log, cut_log, cuts_out = [], [], [], {}
    if args.recode:
        mp = read_table(args.recode)
        for r in mp.itertuples(index=False):
            col, old, new = str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip()
            if col in roles:
                L.loc[L[col] == old, col] = new
        for col in sorted(set(mp.iloc[:, 0]) & set(roles)):
            recode_log.append((col, L[col][L[col] != ""].value_counts()))
    for m in args.merge or []:
        col, _, rule = m.partition(":")
        src, _, dst = rule.partition("=")
        if col not in roles:
            sys.exit(f"--merge 找不到納入的欄位 {col!r}")
        before = L[col][L[col] != ""].value_counts()
        L.loc[L[col].isin(src.split("+")), col] = dst
        merge_log.append((col, src.split("+"), dst, before, L[col][L[col] != ""].value_counts()))
    for c in [c for c in roles if roles[c] == "連續（分組）"]:
        x = to_num(L[c].replace("", np.nan))
        if c in cuts_in:
            cuts, how = cuts_in[c], f"沿用 {args.cuts_in}"
        else:
            cuts = make_cuts(x, bins[c])
            how = {"quantile": "等頻", "width": "等寬"}.get(bins[c].split(":")[0], "自訂切點")
        cuts_out[c] = cuts
        lab, order = bin_labels(x, cuts)
        L[c] = lab.fillna("")
        cnt = L[c][L[c] != ""].value_counts().reindex(order).fillna(0).astype(int)
        cut_log.append((c, how, cuts, cnt[cnt > 0] if c not in cuts_in else cnt))

    # --- 缺失與跳題
    miss_rows = []
    cat_cols = [c for c in roles if roles[c] in ("二元", "類別", "連續（分組）")]
    for c in roles:
        blank = df[c] == ""                     # 原始空白（漏答 / 跳題），不含已丟棄的特殊代碼
        sp = {}
        if c in cb:   # 特殊代碼在分析範圍內的筆數
            kinds = df[c].map(lambda v: cb[c][v][1] if v in cb[c] else "")
            sp = {MISSING_LABEL.get(k, k): int(v) for k, v in kinds[kinds != ""].value_counts().items()}
        how = ("特殊代碼：" + ("不產生項目" if args.missing_codes == "drop" else "保留為獨立類別")) if sp else ""
        skip = ""
        if blank.sum() >= max(5, 0.05 * n):
            for c2 in cat_cols:
                if c2 == c:
                    continue
                for v, g in L[blank].groupby(c2):
                    if v == "":
                        continue
                    grp = L[c2] == v
                    if blank[grp].mean() >= 0.95 and grp[blank].mean() >= 0.9:
                        skip = f"空白集中在 {c2}={v}（疑似跳題 / 不適用）"
                        break
                if skip:
                    break
        if blank.sum() or sp:
            miss_rows.append({"欄位": c, "空白": int(blank.sum()),
                              "特殊代碼": "、".join(f"{k} {v}" for k, v in sp.items()) or "—",
                              "處理": "；".join(x for x in ["空白不產生項目（不視為反對或未選）", how, skip] if x)})
            if skip:
                checks.append(f"⚠️ {c}：{skip}。若只想分析適用者，用 --where 限定；否則這些人在 {c} 上沒有項目，"
                              f"含 {c} 的規則分母仍是全部 {n} 人")

    # --- 建立項目
    rows = []
    for c in roles:
        s = L[c]
        if roles[c] == "複選題":
            for t, v in zip(tid, s):
                for opt in [o.strip() for o in v.split(args.multi_sep) if o.strip()]:
                    rows.append((t, f"{c}={opt}"))
        else:
            rows += [(t, f"{c}={v}") for t, v in zip(tid, s) if v != ""]
        if roles[c] != "複選題" and (s.str.contains(args.multi_sep, regex=False)).any():
            checks.append(f"⚠️ {c} 有些值含有「{args.multi_sep}」，可能是複選題；若是，請加 --multi {c}")
    items = pd.DataFrame(rows, columns=["respondent_id", "item"])
    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(args.out_dir, args.output)
    items.to_csv(out_path, index=False, encoding="utf-8")
    args._outputs.append(out_path)
    freq = items["item"].value_counts()

    # --- 自動檢查：函數關係、低頻與高頻類別
    for x in cat_cols:
        for y in cat_cols:
            if x >= y:
                continue
            both = L[(L[x] != "") & (L[y] != "")]
            if both.empty:
                continue
            xy = both.groupby(x)[y].nunique().max() == 1 and both[x].value_counts().min() >= 5 and both[y].nunique() > 1
            yx = both.groupby(y)[x].nunique().max() == 1 and both[y].value_counts().min() >= 5 and both[x].nunique() > 1
            if xy and yx:
                checks.append(f"⚠️ {x} 與 {y} 一對一對應：是同一資訊的兩種寫法，建議只保留一個")
            elif xy or yx:
                a, b = (x, y) if xy else (y, x)
                checks.append(f"⚠️ {a} → {b}：{a} 的值決定 {b}（例如原始變數與衍生變數、縣市與區域）。"
                              f"「{a}=… → {b}=…」是定義上必然成立的規則，mine 會自動標為定義性；"
                              f"也可以只保留其中一個欄位（項目粒度的選擇）")
    low = freq[freq < args.min_cat]
    if len(low):
        checks.append(f"⚠️ {len(low)} 個項目少於 {args.min_cat} 人（{', '.join(low.index[:8])}）："
                      "很難成為頻繁項目。可考慮把語意相近的類別合併（--merge），但不能只因為人少就合併（Tan et al. 6.1）")
    high = freq[freq / n >= 0.8]
    if len(high):
        checks.append(f"⚠️ 高頻項目（≥ 80%）：{', '.join(f'{i}（{v / n:.0%}）' for i, v in high.items())}。"
                      "它們會和許多項目湊成看似強的規則，建議在 mine 加 --hconf 或排除該值（Tan et al. 6.1、5.8）")
    if weight_c:
        checks.append(f"⚠️ 偵測到調查權重欄位 {weight_c}：本分析是**未加權的樣本描述**，support 是樣本比例，"
                      "不能直接說成母體比例。加權推論需要另外的模組（並考慮抽樣設計）。")

    if args.cuts_out:
        with open(args.cuts_out, "w", encoding="utf-8") as f:
            json.dump(cuts_out, f, ensure_ascii=False, indent=2)
        args._outputs.append(args.cuts_out)
    meta = {"data": args.data, "n_rows": n0, "n_units": n, "id_col": id_c, "where": args.where or [],
            "roles": roles, "excluded": excluded, "cuts": cuts_out, "missing_codes": args.missing_codes,
            "merge": args.merge or [], "recode": args.recode, "weight_col": weight_c}
    meta_path = os.path.join(args.out_dir, "encoding.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    args._outputs.append(meta_path)

    # --- 摘要（確認 ① 要給使用者看的內容）
    print("## 步驟 1（問卷）：資料設定摘要\n\n### 分析單位與分析對象")
    print(f"- 分析單位：一位受訪者（每列一筆；識別欄位 = {id_c or '列號'}）")
    print(f"- 原始資料 {n0:,} 列" + (f"；分析範圍 {' 且 '.join(args.where)} → **{n:,} 人**" if args.where else f"；全部納入 **{n:,} 人**"))
    print(f"- 支持度的分母 = {n:,} 人" + ("（只計算符合範圍條件的人）" if args.where else ""))
    for x in notes:
        print(f"- {x}")
    print("\n### 納入欄位\n")
    print(md_table(pd.DataFrame([{"欄位": c, "類型": r, "有效回答": int((L[c] != "").sum()),
                                  "類別數": int(L[c][L[c] != ""].str.split(args.multi_sep).explode().nunique()
                                             if r == "複選題" else L[c][L[c] != ""].nunique())}
                                 for c, r in roles.items()])))
    print("\n### 排除欄位\n")
    print(md_table(pd.DataFrame([{"欄位": c, "原因": r} for c, r in excluded.items()])))
    print("\n### 缺失與特殊答案\n")
    print(md_table(pd.DataFrame(miss_rows)) if miss_rows else "（納入欄位沒有缺失）")
    if cut_log:
        print("\n### 連續變數分組")
        for c, how, cuts, cnt in cut_log:
            print(f"\n**{c}**：{how}，切點 {[fmt_num(v) for v in cuts]}\n")
            print(md_table(pd.DataFrame({"組別": cnt.index, "人數": cnt.values,
                                         "比例": (cnt.values / max(1, cnt.sum())).round(3)})))
    for col, src, dst, before, after in merge_log:
        print(f"\n### 合併答案：{col}（{' + '.join(src)} → {dst}）\n")
        print("合併前：" + "、".join(f"{k} {v}" for k, v in before.items()))
        print("\n合併後：" + "、".join(f"{k} {v}" for k, v in after.items()))
    for col, cnt in recode_log:
        print(f"\n### 項目粒度：{col} 依 {args.recode} 換算後\n")
        print("、".join(f"{k} {v}" for k, v in cnt.items()))
    print("\n### 自動檢查")
    print("- 單選題每人最多一個值：同一題的不同答案不會出現在同一個項目集合（Tan et al. 6.1）")
    for x in checks:
        print(f"- {x}")
    print(f"\n### 項目頻率（共 {len(freq)} 個項目，前 15）\n")
    print(md_table(pd.DataFrame({"item": freq.index, "count": freq.values, "support": freq.values / n}).head(15)))
    print(f"\n輸出：{out_path}（respondent_id, item），之後的 profile / split / mine 都用這個檔案；"
          f"設定紀錄：{meta_path}" + (f"；切點：{args.cuts_out}" if args.cuts_out else ""))


# ----------------------------------------------------------------------------- 執行紀錄與報告

LOG_NAME = "run_log.json"
QUARTO_LANG = 'language:\n  toc-title-document: "目錄"\n  title-block-published: "日期"\n  title-block-author-single: "作者"\n  code-tools-source-code: "原始碼"\n  code-tools-view-source: "檢視 QMD 原始碼"\n  copy-button-tooltip: "複製"\n  copy-button-tooltip-success: "已複製"\n  section-title-footnotes: "註腳"\n  callout-note-title: "說明"\n  callout-tip-title: "提示"\n  callout-warning-title: "注意"\n'
STEP_OF = {"encode": "data", "profile": "data", "split": "split", "mine": "mine",
           "validate": "check", "permtest": "check", "stratify": "check"}


def load_log(out_dir):
    path = os.path.join(out_dir, LOG_NAME)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


def write_log_entry(args, output):
    os.makedirs(args.out_dir, exist_ok=True)
    log = load_log(args.out_dir)
    data = os.path.abspath(getattr(args, "data", "") or "")
    produced = {os.path.abspath(p) for e in log for p in e.get("files", [])}
    # encode / profile 讀的是新的原始資料時，代表開始一次新的分析：舊紀錄另存，開新的 log
    if args.cmd in ("encode", "profile") and log and data not in produced:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        os.replace(os.path.join(args.out_dir, LOG_NAME), os.path.join(args.out_dir, f"run_log_{stamp}.json"))
        log = []
    params = {k: v for k, v in vars(args).items() if not k.startswith("_") and k not in ("func",)}
    log.append({"cmd": args.cmd, "time": datetime.now().isoformat(timespec="seconds"),
                "command": "python scripts/run_arules.py " + " ".join(shlex.quote(a) for a in sys.argv[1:]),
                "params": json.loads(json.dumps(params, default=str, ensure_ascii=False)),
                "files": [os.path.abspath(p) for p in args._outputs], "output": output})
    with open(os.path.join(args.out_dir, LOG_NAME), "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)


class Tee(io.TextIOBase):
    def __init__(self, stream):
        self.stream, self.buf = stream, io.StringIO()

    def write(self, s):
        self.stream.write(s)
        self.buf.write(s)
        return len(s)

    def flush(self):
        self.stream.flush()


def py_code(e):
    """每個步驟對應的核心 Python 程式碼（與腳本相同的計算，方便讀者理解或改寫）。"""
    p = e["params"]
    if e["cmd"] == "encode":
        return f'''import pandas as pd
df = pd.read_csv("{p["data"]}", dtype=str, keep_default_na=False)
# 1) codebook：代碼 → 標籤；特殊代碼（拒答 / 不知道）處理方式 = {p["missing_codes"]}
# 2) 分析範圍 = {p.get("where") or "全部"}；分組切點 = {p.get("bins") or "無"}；合併 = {p.get("merge") or "無"}
# 3) 每位受訪者的每個答案寫成「欄位=值」項目（空白不產生項目）
items = [(r[id_col], f"{{c}}={{r[c]}}") for _, r in df.iterrows() for c in item_cols if r[c] != ""]
pd.DataFrame(items, columns=["respondent_id", "item"]).to_csv("items.csv", index=False)'''
    if e["cmd"] == "profile":
        return f'''import pandas as pd
df = pd.read_csv("{p["data"]}")
baskets = df.groupby(df.columns[0])["item"].agg(frozenset)       # 一個觀測單位 = 一個項目集合
sizes = baskets.map(len)                                           # 籃子大小分布
support = df.drop_duplicates().groupby("item").size() / len(baskets)   # 每個項目的 support'''
    if e["cmd"] == "split":
        return f'''import numpy as np
# 方法 = {p.get("method")}；驗證比例 = {p.get("test_frac")}；seed = {p.get("seed")}
rng = np.random.default_rng({p.get("seed")})
perm = rng.permutation(len(ids)); cut = int(len(ids) * (1 - {p.get("test_frac")}))
train_ids, test_ids = ids[perm[:cut]], ids[perm[cut:]]      # 時間切分則改為依時間排序後切'''
    if e["cmd"] in ("mine", "permtest"):
        ms = p.get("min_support") or f'{p.get("min_count")} / N'
        return f'''from mlxtend.frequent_patterns import fpgrowth
X = onehot(baskets)                                  # 觀測單位 × 項目 的 0/1 矩陣
fi = fpgrowth(X, min_support={ms}, use_colnames=True, max_len={p.get("max_len")})
# 由每個 frequent itemset I 產生規則 A → I−A，計算 support / confidence / lift 等指標
# 篩選：confidence ≥ {p.get("min_confidence")}、lift > {p.get("min_lift")}、hconf ≥ {p.get("hconf")}、移除冗餘規則
# 規則形式：指定後件 = {p.get("rhs")}；允許的前件 = {p.get("lhs_cols")}'''
    if e["cmd"] == "validate":
        return f'''from scipy.stats import fisher_exact, false_discovery_control
# 對每條規則 A → B 在驗證集建立 2×2 表 [[f11, f10], [f01, f00]]
p = fisher_exact([[f11, f10], [f01, f00]], alternative="greater")[1]
q = false_discovery_control(p_values, method="bh")      # Benjamini–Hochberg
replicated = (test_lift > 1) & (q < {p.get("alpha")}) & (test_count >= {p.get("min_test_count")})'''
    if e["cmd"] == "stratify":
        return f'''# 依 {p.get("by")} 分層，各層分別計算 lift，與合併資料的方向比較（Simpson's paradox）
for s, sub in groups:
    lift_s = support(A ∪ B, sub) / (support(A, sub) * support(B, sub))'''
    return ""


def r_code(e):
    p = e["params"]
    if e["cmd"] == "encode":
        return '''library(arules)
df <- read.csv("data.csv", colClasses = "character")
df[df == ""] <- NA
df[] <- lapply(df, factor)              # 類別欄位 → factor
# 連續變數：discretizeDF(df, methods = list(年齡 = list(method = "fixed", breaks = c(-Inf, 18, 30, 45, 65, Inf))))
trans <- as(df[, item_cols], "transactions")   # 自動產生「欄位=值」項目，NA 不產生項目'''
    if e["cmd"] in ("profile", "split"):
        return '''library(arules)
trans <- as(split(df$item, df[[1]]), "transactions")
summary(trans); itemFrequency(trans)'''
    if e["cmd"] in ("mine", "permtest"):
        ms = p.get("min_support") or f'{p.get("min_count")} / length(trans)'
        app = f', appearance = list(rhs = grep("^{p["rhs"][0]}", itemLabels(trans), value = TRUE), default = "lhs")' \
            if p.get("rhs") else ""
        return f'''rules <- apriori(trans, parameter = list(supp = {ms}, conf = {p.get("min_confidence")},
                 minlen = 2, maxlen = {p.get("max_len")}){app})
rules <- subset(rules, lift > {p.get("min_lift")})
rules <- rules[!is.redundant(rules)]
inspect(head(sort(rules, by = "lift"), 20))'''
    if e["cmd"] == "validate":
        return f'''p <- interestMeasure(rules, "fishersExactTest", transactions = test_trans)
q <- p.adjust(p, method = "BH")
replicated <- interestMeasure(rules, "lift", transactions = test_trans) > 1 & q < {p.get("alpha")}'''
    return ""


SECTION_TITLE = {"data": "資料設定與前處理（確認 ①）", "split": "驗證安排（確認 ②）",
                 "mine": "探勘設定與結果（確認 ②、③）", "check": "可靠性評估（依確認 ② 事先決定的標準）"}
STEP_EXPLAIN = {
    "encode": "把問卷寬表轉成「欄位=值」項目：處理代碼、缺失與跳題、連續變數分組，並自動檢查無意義規則的來源。",
    "profile": "清理資料、建立項目集合，描述觀測單位數、項目數與頻率分布，作為設定門檻的依據。",
    "split": "在探勘之前先切出驗證資料，避免用同一份資料挑規則又評估規則。",
    "mine": "找出 frequent itemsets、產生規則，依門檻與規則形式篩選，並記錄每條被排除規則的原因。",
    "validate": "在沒看過的驗證資料上重算指標，做單尾 Fisher exact test 與 Benjamini–Hochberg FDR 校正。",
    "permtest": "把每個項目欄位各自打亂，建立「項目彼此無關」的虛無資料，估計同樣門檻下會出現幾條巧合規則。",
    "stratify": "依分層變數分別計算 lift，檢查合併資料的關聯是否在各層消失或反轉（Simpson's paradox）。",
}

TOGGLE_HTML = '''<div class="code-toggle-bar"><button type="button" onclick="(function(){var d=[].slice.call(document.querySelectorAll('details.code-fold'));var o=!d.every(function(x){return x.open});d.forEach(function(x){x.open=o})})()">全部顯示 / 隱藏程式碼</button></div>
<style>.code-toggle-bar{margin:.5rem 0 1rem}.code-toggle-bar button{padding:.35rem .9rem;border:1px solid #888;border-radius:6px;background:transparent;cursor:pointer;font:inherit}details.code-fold{margin:.4rem 0 .9rem}details.code-fold>summary{cursor:pointer;color:#2a6db0}main table,#quarto-document-content table{display:block;max-width:100%;overflow-x:auto}li code,p code{word-break:break-all;white-space:normal;overflow-wrap:anywhere}</style>'''


def details(summary, lang, code):
    return f'<details class="code-fold">\n<summary>{summary}</summary>\n\n```{lang}\n{code}\n```\n\n</details>\n'


def demote(md, levels=2):
    return re.sub(r"^(#{1,4}) ", lambda m: "#" * min(6, len(m.group(1)) + levels) + " ", md, flags=re.M)


def find_quarto(explicit=None):
    cands = [explicit, os.environ.get("QUARTO_PATH"), shutil.which("quarto"),
             r"C:\Program Files\RStudio\resources\app\bin\quarto\bin\quarto.exe",
             r"C:\Program Files\Quarto\bin\quarto.exe",
             "/Applications/RStudio.app/Contents/Resources/app/quarto/bin/quarto",
             "/usr/lib/rstudio/resources/app/bin/quarto/bin/quarto", "/usr/local/bin/quarto"]
    return next((c for c in cands if c and os.path.exists(c)), None)


def md_to_html(md):
    """備援用的極簡 Markdown → HTML（只處理本報告用到的語法）。"""
    import html as H
    out, i, lines = [], 0, md.split("\n")

    def inline(s):
        s = H.escape(s, quote=False)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            lang = ln[3:].strip()
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append(f'<pre><code class="{lang}">' + H.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
        elif ln.startswith(("<details", "</details", "<summary", "<div", "<style", "<button")):
            out.append(ln); i += 1
        elif re.match(r"^#{1,6} ", ln):
            k = len(ln.split(" ")[0])
            out.append(f"<h{k}>{inline(ln[k + 1:])}</h{k}>"); i += 1
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split(" | ")]); i += 1
            body = [r for r in rows[1:] if not set("".join(r)) <= set("-|: ")]
            out.append("<div class='tbl'><table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in rows[0])
                       + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
                                                          for r in body) + "</tbody></table></div>")
        elif ln.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(lines[i][2:]); i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
        elif ln.startswith("> "):
            out.append(f"<blockquote>{inline(ln[2:])}</blockquote>"); i += 1
        elif ln.strip():
            out.append(f"<p>{inline(ln)}</p>"); i += 1
        else:
            i += 1
    return "\n".join(out)


FALLBACK_CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#666;--line:#ddd;--code:#f5f5f7;--accent:#2a6db0}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#e8e8ea;--muted:#aaa;--line:#3a3a3e;--code:#232327;--accent:#7cb3ef}}
body{background:var(--bg);color:var(--fg);font:16px/1.65 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;max-width:960px;margin:0 auto;padding:24px 16px}
h1{font-size:1.8rem}h2{border-bottom:1px solid var(--line);padding-bottom:.3rem;margin-top:2.2rem}
.tbl{overflow-x:auto}table{border-collapse:collapse;margin:.6rem 0;font-size:.9rem}th,td{border:1px solid var(--line);padding:4px 8px;text-align:left}
pre{background:var(--code);padding:12px;border-radius:6px;overflow-x:auto}code{font-family:Consolas,Menlo,monospace;font-size:.88em}
blockquote{border-left:4px solid var(--accent);margin:.6rem 0;padding:.2rem .8rem;color:var(--muted)}
details.code-fold>summary{color:var(--accent)}
"""


def cmd_report(args):
    log = load_log(args.out_dir)
    if not log:
        sys.exit(f"{args.out_dir}/{LOG_NAME} 沒有紀錄：請先執行 encode / profile / mine 等步驟。")
    notes = open(args.notes, encoding="utf-8").read().strip() if args.notes else ""
    first = log[0]
    data = first["params"].get("data", "")
    mines = [i for i, e in enumerate(log) if e["cmd"] == "mine"]
    vals = [i for i, e in enumerate(log) if e["cmd"] == "validate"]
    revisit = bool(vals) and any(m > vals[0] for m in mines)

    # 摘要數字（取最後一次 mine / validate 的輸出檔）
    summ = [f"- 資料：`{data}`", f"- 分析時間：{log[0]['time']} ～ {log[-1]['time']}，共執行 {len(log)} 個步驟"]
    if mines:
        rp = next((f for f in log[mines[-1]]["files"] if f.endswith("rules.csv") and "excluded" not in f), None)
        if rp and os.path.exists(rp):
            summ.append(f"- 最後一次探勘保留規則：{len(pd.read_csv(rp))} 條")
    if vals:
        vp = next((f for f in log[vals[-1]]["files"] if f.endswith("validation.csv")), None)
        if vp and os.path.exists(vp):
            v = pd.read_csv(vp)
            summ.append(f"- 驗證集重現：{int(v['replicated'].sum())} / {len(v)} 條")
    if revisit:
        summ.append("- ⚠️ 看過驗證結果後又重新探勘：驗證資料已參與規則選擇，最後的驗證結果應視為**探索性**")

    title = args.title or "關聯規則分析報告"
    parts = [f'---\ntitle: "{title}"\nsubtitle: "association-rules-skill 自動產生"\n'
             f'date: "{datetime.now().strftime("%Y-%m-%d %H:%M")}"\nlang: zh-TW\n'
             + QUARTO_LANG +
             'format:\n  html:\n'
             '    toc: true\n    toc-depth: 3\n    number-sections: true\n    embed-resources: true\n'
             '    code-tools: true\n    df-print: kable\n---\n', TOGGLE_HTML + "\n",
             "本報告記錄這次對資料做的每一個處理與決策。每一節先說明目的，再列出當時的輸出；"
             "產生該結果的指令與程式碼收在「顯示程式碼」裡，可以逐段展開，或用上方按鈕一次全部顯示。\n",
             "## 摘要\n\n" + "\n".join(summ) + "\n"]
    seen = set()
    for k, e in enumerate(log, 1):
        sec = STEP_OF.get(e["cmd"])
        if not sec:
            continue
        if sec not in seen:
            parts.append(f"## {SECTION_TITLE[sec]}\n")
            seen.add(sec)
        parts.append(f"### 第 {k} 步：`{e['cmd']}`（{e['time']}）\n\n{STEP_EXPLAIN.get(e['cmd'], '')}\n")
        if revisit and e["cmd"] in ("mine", "validate") and k - 1 > vals[0]:
            parts.append("> ⚠️ 這一步發生在已經看過驗證結果之後，驗證資料不再是獨立的。\n")
        parts.append(details("顯示程式碼：可重現的指令", "bash", e["command"]))
        pc, rc = py_code(e), r_code(e)
        if pc:
            parts.append(details("顯示程式碼：對應的 Python", "python", pc))
        if rc:
            parts.append(details("顯示程式碼：對應的 R（arules）", "r", rc))
        parts.append(demote(e["output"].strip(), 2) + "\n")
    parts.append("## 實務解讀與限制\n\n" + (demote(notes, 1) if notes else
                 "（尚未提供解讀：請用 `report --notes interpretation.md` 加入步驟 6 的白話解讀、行動建議與限制。）") + "\n")
    parts.append("## 回頭修改紀錄\n\n" + (
        f"- 探勘執行 {len(mines)} 次、驗證執行 {len(vals)} 次。\n" +
        ("- ⚠️ 在看過驗證結果之後又重新探勘或調整設定，因此驗證資料已參與規則選擇，不能再當成獨立驗證；"
         "若要確認結果，請用新的資料或新的保留期間。\n" if revisit else
         "- 驗證結果產生後沒有再修改探勘設定，驗證資料仍是獨立的。\n")))
    import platform
    vers = []
    for mod in ("pandas", "numpy", "mlxtend", "scipy"):
        try:
            vers.append(f"{mod} {__import__(mod).__version__}")
        except Exception:
            pass
    parts.append("## 附錄：執行環境\n\n" + f"- Python {platform.python_version()}；" + "、".join(vers) +
                 f"\n- 執行紀錄：`{os.path.join(args.out_dir, LOG_NAME)}`\n" +
                 details("顯示程式碼：全部指令（依序）", "bash", "\n".join(e["command"] for e in log)))
    qmd = "\n".join(parts)

    os.makedirs(args.out_dir, exist_ok=True)
    qmd_path = os.path.join(args.out_dir, "report.qmd")
    html_path = os.path.join(args.out_dir, "report.html")
    if os.path.exists(qmd_path):   # 舊版報告保留，加上時間戳
        stamp = datetime.fromtimestamp(os.path.getmtime(qmd_path)).strftime("%Y%m%d-%H%M%S")
        for p in (qmd_path, html_path):
            if os.path.exists(p):
                os.replace(p, p.replace("report.", f"report_{stamp}."))
    with open(qmd_path, "w", encoding="utf-8") as f:
        f.write(qmd)
    quarto = None if args.no_quarto else find_quarto(args.quarto)
    how = ""
    if quarto:
        r = subprocess.run([quarto, "render", "report.qmd", "--to", "html"], cwd=args.out_dir,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0 and os.path.exists(html_path):
            how = f"以 Quarto 轉檔（{quarto}）"
        else:
            print("Quarto 轉檔失敗，改用內建轉檔：\n" + (r.stderr or "")[-800:])
    if not how:
        body = md_to_html(qmd.split("---\n", 2)[2])
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(f'<!doctype html><html lang="zh-TW"><head><meta charset="utf-8">'
                    f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>'
                    f'<style>{FALLBACK_CSS}</style></head><body><h1>{title}</h1>{body}</body></html>')
        how = ("依 --no-quarto" if args.no_quarto else "找不到 Quarto") + "，以腳本內建轉檔（程式碼一樣可以摺疊）"
    print(f"## 步驟 6：分析報告\n- {how}\n- QMD：{qmd_path}\n- HTML：{html_path}")


# ----------------------------------------------------------------------------- CLI

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("data", help="交易資料或 encode 輸出的項目檔（CSV / XLSX）")
        sp.add_argument("--format", choices=["auto", "long", "basket", "wide", "survey"], default="auto",
                        help="long=一列一品項；basket=一列一籃（品項以 --sep 分隔）；wide=0/1 矩陣；"
                             "survey=問卷寬表（只用於 split，其餘請先 encode）")
        sp.add_argument("--tid-col")
        sp.add_argument("--item-col")
        sp.add_argument("--time-col")
        sp.add_argument("--qty-col")
        sp.add_argument("--sep", help="basket 格式的品項分隔符號（預設逗號）")
        sp.add_argument("--encoding")
        sp.add_argument("--out-dir", default="arules_output")
        # 分析範圍與項目粒度（確認 ① 的進階選項）
        sp.add_argument("--where", nargs="+", metavar="欄位=值",
                        help="限定分析範圍：資料中有該欄位時篩選列（例如 store=A），否則當成項目條件（例如 就業狀態=就業）")
        sp.add_argument("--date-from", help="只分析這一天（含）之後的交易")
        sp.add_argument("--date-to", help="只分析這一天（含）之前的交易")
        sp.add_argument("--item-map", help="項目粒度對照表（CSV：原項目, 新項目），例如品項 → 商品類別")

    def mining(sp):
        sp.add_argument("--min-support", type=float, help="最低支持度（比例）")
        sp.add_argument("--min-count", type=int, help="最低支持度（筆數），與 --min-support 擇一")
        sp.add_argument("--min-confidence", type=float, default=0.3)
        sp.add_argument("--min-lift", type=float, default=1.0, help="保留 lift 大於此值的規則（篩選用）")
        sp.add_argument("--max-len", type=int, default=3, help="規則（前件＋後件）最多幾個項目")
        sp.add_argument("--max-lhs", type=int, help="前件最多幾個項目（給定時取代 --max-len）")
        sp.add_argument("--max-rhs", type=int, help="後件最多幾個項目（預設 1）")
        sp.add_argument("--rhs", nargs="+", help="指定後件：商品名稱或問卷題目（例如 政策A態度）")
        sp.add_argument("--lhs-cols", nargs="+", help="前件只能來自這些商品或題目")
        sp.add_argument("--min-lhs-count", type=int, help="前件至少出現幾筆")
        sp.add_argument("--algo", choices=["auto", "fpgrowth", "apriori"], default="auto",
                        help="結果相同，只差速度；auto = FP-growth")
        sp.add_argument("--hconf", type=float, help="h-confidence 門檻，用來去除 cross-support 規則")
        sp.add_argument("--flag-definitional", action="store_true",
                        help="把 confidence ≥ 0.99 的規則標為「疑似定義性」並排除")

    sp = sub.add_parser("encode", help="問卷寬表 → 「欄位=值」項目")
    sp.add_argument("data", help="問卷資料（一列一位受訪者）")
    sp.add_argument("--id-col")
    sp.add_argument("--include", nargs="+", help="只納入這些欄位")
    sp.add_argument("--exclude", nargs="+", help="排除這些欄位")
    sp.add_argument("--codebook", help="CSV：column, code, label, missing（missing：refused / dont_know / na）")
    sp.add_argument("--missing-codes", choices=["drop", "keep"], default="drop",
                    help="拒答 / 不知道等特殊代碼：drop=不產生項目；keep=保留為獨立類別")
    sp.add_argument("--where", nargs="+", metavar="欄位=值", help="限定分析人群（例如 就業狀態=就業）")
    sp.add_argument("--bins", nargs="+", metavar="欄位=切點",
                    help="連續變數分組：年齡=18,30,45,65（自訂）、收入=quantile:4（等頻）、時數=width:10（等寬）")
    sp.add_argument("--cuts-out", help="把這次用的切點存成 JSON（在探勘集決定切點時用）")
    sp.add_argument("--cuts-in", help="沿用 JSON 裡的切點（套用到驗證集）")
    sp.add_argument("--merge", nargs="+", metavar="欄位:舊+舊=新", help="合併答案，例如 滿意度:非常滿意+滿意=滿意")
    sp.add_argument("--recode", help="項目粒度對照表（CSV：column, value, new_value），例如 縣市 → 區域")
    sp.add_argument("--multi", nargs="+", help="複選題欄位")
    sp.add_argument("--multi-sep", default=";")
    sp.add_argument("--weight-col", help="調查權重欄位（只用於提醒，不加權）")
    sp.add_argument("--min-cat", type=int, default=20, help="少於此人數的類別會被提醒")
    sp.add_argument("--output", default="items.csv")
    sp.add_argument("--encoding")
    sp.add_argument("--out-dir", default="arules_output")
    sp.set_defaults(func=cmd_encode)

    sp = sub.add_parser("profile"); common(sp)
    sp.add_argument("--top", type=int, default=15)
    sp.add_argument("--plot", help="輸出圖檔路徑（經使用者同意後才使用）")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("split"); common(sp)
    sp.add_argument("--method", choices=["time", "random", "group"], default="time")
    sp.add_argument("--group-col", help="--method group 的分組欄位（顧客或受訪者編號）")
    sp.add_argument("--split-date", help="依指定日期切分（該日之前為探勘集）")
    sp.add_argument("--test-frac", type=float, default=0.3)
    sp.add_argument("--seed", type=int, default=42)
    sp.set_defaults(func=cmd_split)

    sp = sub.add_parser("mine"); common(sp); mining(sp)
    sp.add_argument("--keep-redundant", action="store_true")
    sp.add_argument("--sort", choices=["lift", "support", "confidence"], default="lift")
    sp.add_argument("--top", type=int, default=20)
    sp.add_argument("--too-many", type=int, default=100)
    sp.set_defaults(func=cmd_mine)

    sp = sub.add_parser("validate"); common(sp)
    sp.add_argument("--rules", required=True, help="mine 輸出的 rules.csv")
    sp.add_argument("--alpha", type=float, default=0.05)
    sp.add_argument("--min-test-count", type=int, default=0, help="驗證集中規則至少要共同出現幾筆才算重現")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("permtest"); common(sp); mining(sp)
    sp.add_argument("--n-perm", type=int, default=20)
    sp.add_argument("--seed", type=int, default=42)
    sp.set_defaults(func=cmd_permtest)

    sp = sub.add_parser("stratify"); common(sp)
    sp.add_argument("--rules", required=True)
    sp.add_argument("--by", required=True,
                    help="weekend / weekday / month / hour、資料中的欄位名稱（例如 store），或問卷題目（例如 性別）")
    sp.add_argument("--min-count", type=int, default=10)
    sp.set_defaults(func=cmd_stratify)

    sp = sub.add_parser("report", help="把這次分析整理成 report.qmd / report.html")
    sp.add_argument("--notes", help="步驟 6 的白話解讀、行動建議與限制（Markdown）")
    sp.add_argument("--title")
    sp.add_argument("--quarto", help="quarto 執行檔路徑（預設自動尋找）")
    sp.add_argument("--no-quarto", action="store_true", help="不用 Quarto，改用腳本內建轉檔")
    sp.add_argument("--out-dir", default="arules_output")
    sp.set_defaults(func=cmd_report)

    args = p.parse_args()
    args._outputs = []
    if args.cmd == "report":
        return args.func(args)
    tee = Tee(sys.stdout)
    sys.stdout = tee
    try:
        args.func(args)
    finally:
        sys.stdout = tee.stream
    write_log_entry(args, tee.buf.getvalue())


if __name__ == "__main__":
    main()
