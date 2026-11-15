"""關聯規則分析主程式（association-rules-skill）。

三個模式，全部由 config.json 驅動；同一份資料、同一份 config 與相同套件版本，會得到相同的結果。

  inspect   讀取資料：筆數、欄位、少量範例；判斷資料類型（購物清單／問卷類別表／已完成的項目集合）
            並列出依據；檢查缺失、欄位值、可能的 ID／權重、不支援的數值欄位；提出前處理方案，
            輸出 inspect.json 與 config_template.json。
  run       依 config 前處理。需要使用者確認的事項還沒確認時，列出事項後停止（結束代碼 2）；
            資料不符合支援格式時，說明需要的調整後停止（結束代碼 1）。
            通過後切出驗證資料、依分析問題的固定規則形式探勘、在驗證資料上檢驗，
            輸出 results.json、results_full.csv 與 report_draft.md。
  finalize  確認報告沒有未填寫處，附上 config、前處理紀錄與完整程式碼，
            輸出 report.md、report.qmd、report.html。

用法
  python scripts/run_arules.py inspect --data <資料.csv> [--out arules_output] [--type shopping|survey]
  python scripts/run_arules.py run --config config.json
  python scripts/run_arules.py finalize --config config.json --report arules_output/report.md

定義依據：Tan, Steinbach, Karpatne & Kumar (2019), Introduction to Data Mining (2nd ed.)
第 5、6 章與 10.4 節；固定規則與門檻見 references/decision-tree.md。
"""
import argparse
import difflib
import html as H
import inspect as pyinspect
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

VERSION = "2.0"

# ----------------------------------------------------------------------------- 固定常數（見 decision-tree.md）
ID_UNIQUE = 0.95          # 不同值比例 ≥ 此值 → 可能是 ID
NUMERIC_SHARE = 0.8       # 非缺失值中可解析為數字的比例 ≥ 此值 → 數字代碼或數值欄位
CODE_MAX_LEVELS = 10      # 數字欄位不同值 ≤ 此值 → 可能是選項代碼；否則視為數值
HIGH_FREQ = 0.8           # 某答案占比 ≥ 此值 → 提醒（當後件時 lift 多半接近 1）
SEPARATORS = [",", ";", "|", "、"]
VALID_MIN_UNITS = 300     # 觀測單位少於此數 → 不切驗證資料
TEST_FRAC = 0.3
SEED = 2026
ALPHA = 0.05
TOP = 20
FD_MIN = 5                # 判斷欄位間函數關係時，每個值至少要出現的次數
MISSING_LIKE = {"na", "n/a", "nan", "null", "none", "-", "--", ".", "?", "missing", "refused", "dk",
                "don't know", "dont know", "拒答", "不知道", "不適用", "無意見", "未填", "缺"}
HINT_ID = ({"id", "no", "編號"}, ["編號", "序號", "代號", "受訪者"])
HINT_WEIGHT = ({"weight", "wt", "wgt", "weights"}, ["權重", "加權"])
HINT_TIME = ({"time", "date", "datetime", "timestamp", "invoicedate"}, ["時間", "日期"])
HINT_QTY = ({"qty", "quantity"}, ["數量"])

SHOP_Q = ["bundle", "recommend", "placement", "explore"]
SURVEY_Q = ["cooccur", "target", "given"]
QLABEL = {
    "bundle": "哪些商品適合組成套餐？",
    "recommend": "買「{x}」的人，可以推薦什麼？",
    "placement": "哪些商品可以考慮放在相近位置？",
    "explore": "我不知道有什麼有趣的搭配，幫我看看",
    "cooccur": "哪些回答常一起出現？",
    "target": "哪些條件與「{x}」有關？",
    "given": "已知「{x}」，還有哪些答案常見？",
}
QFORM = {
    "bundle": "2–3 項商品的頻繁項目集；組合中每一項商品和其餘商品都要正相關（每個方向的 lift > 1）；依 support（共同出現筆數）排序",
    "recommend": "前件 = {指定商品}、後件 = 1 項商品；lift > 1 且 confidence ≥ 門檻；依 lift 排序，同分再看 confidence",
    "placement": "2 項商品的組合（雙向）；lift > 1；依 lift 排序，附 cosine、Kulczynski",
    "explore": "前件 ≤ 2 項、後件 1 項的全部規則；lift > 1 且 confidence ≥ 門檻；移除冗餘規則；依 lift 排序",
    "cooccur": "選定欄位間，前件 ≤ 2 個答案、後件 1 個答案的全部規則；lift > 1 且 confidence ≥ 門檻；移除冗餘與定義性規則；依 lift 排序",
    "target": "後件 = 指定的「欄位=答案」、前件 ≤ 2 個其他欄位的答案；lift > 1；移除冗餘與定義性規則；依 lift 排序",
    "given": "前件 = 指定的特徵、後件 = 1 個其他欄位的答案；依 confidence 排序，附基準比例與 lift（lift ≤ 1 另外標示）",
}


# ----------------------------------------------------------------------------- 共用工具

def read_table(path, encoding=None):
    """全部欄位讀成文字；不把任何字串（例如 NA、99）自動當成缺失，只有空白格是空字串。"""
    if not os.path.exists(path):
        sys.exit(f"找不到資料檔：{path}")
    if path.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(path, dtype=str).fillna(""), "excel"
    for enc in ([encoding] if encoding else ["utf-8-sig", "cp950", "latin1"]):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str, keep_default_na=False), enc
        except UnicodeDecodeError:
            continue
    sys.exit(f"無法以 {encoding} 讀取 {path}")


def name_hint(col, hint):
    tokens = set(re.split(r"[^0-9a-z]+", str(col).lower())) - {""}
    return bool(tokens & hint[0]) or any(w in str(col) for w in hint[1])


def is_num(s):
    return pd.to_numeric(s, errors="coerce").notna()


def fmt_set(s):
    return "{" + ", ".join(sorted(s)) + "}"


def fmt(v, d=4):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, float):
        if math.isinf(v):
            return "∞"
        if v != 0 and abs(v) < 10 ** -d:
            return f"{v:.2e}"
        return f"{v:.{d}f}".rstrip("0").rstrip(".")
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}"
    return str(v)


def md_table(rows, cols, headers=None):
    """rows: list[dict]；cols: 要輸出的鍵；headers: 表頭文字。"""
    if not rows:
        return "（無）"
    headers = headers or cols
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        out.append("| " + " | ".join(fmt(r.get(c)).replace("|", "\\|").replace("\n", " ") for c in cols) + " |")
    return "\n".join(out)


def log_add(log, step, action, reason, affected):
    log.append({"step": step, "action": action, "reason": reason, "affected": affected})


def details(summary, lang, code):
    return f'<details class="code-fold">\n<summary>{summary}</summary>\n\n```{lang}\n{code}\n```\n\n</details>\n'


# ----------------------------------------------------------------------------- inspect：欄位檢查與類型判斷

def profile_columns(df):
    prof = {}
    for c in df.columns:
        raw = df[c].astype(str)
        st = raw.str.strip()
        miss = st == ""
        v = st[~miss]
        nu = int(v.nunique())
        num = is_num(v) if len(v) else pd.Series(dtype=bool)
        nums = pd.to_numeric(v[num], errors="coerce") if len(v) else pd.Series(dtype=float)
        sep_share = {s: float(v.str.contains(s, regex=False).mean()) if len(v) else 0.0 for s in SEPARATORS}
        best_sep = max(sep_share, key=sep_share.get)
        prof[c] = dict(
            missing=int(miss.sum()), unique=nu, unique_ratio=nu / max(1, len(v)),
            numeric_share=float(num.mean()) if len(v) else 0.0,
            non_integer=bool(len(nums) and (nums != nums.round()).any()),
            is01=bool(len(v)) and set(v.str.lower().unique()) <= {"0", "1", "true", "false"},
            ws_changed=int((raw != st).sum() - 0),
            missing_like=sorted({x for x in v.unique() if x.lower() in MISSING_LIKE}),
            top=[[k, int(n)] for k, n in v.value_counts().head(12).items()],
            top_share=float(v.value_counts(normalize=True).iloc[0]) if len(v) else 0.0,
            avg_len=float(v.str.len().mean()) if len(v) else 0.0,
            sep=best_sep, sep_share=sep_share[best_sep],
            time_share=float(pd.to_datetime(v.head(500), errors="coerce", format="mixed").notna().mean())
            if len(v) and (name_hint(c, HINT_TIME) or v.head(50).str.contains(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}").mean() > 0.5) else 0.0,
        )
    return prof


def survey_role(c, p, n):
    """問卷欄位的初步角色（只是提議，必須由使用者確認）。"""
    if p["unique"] >= 20 and p["unique_ratio"] >= ID_UNIQUE:
        return "id", "幾乎每列都不同（不同值比例 {:.0%}）".format(p["unique_ratio"])
    if name_hint(c, HINT_ID) and p["unique_ratio"] >= 0.9:
        return "id", "欄位名稱像編號，且不同值比例 {:.0%}".format(p["unique_ratio"])
    if name_hint(c, HINT_WEIGHT) and p["numeric_share"] >= NUMERIC_SHARE:
        return "weight", "欄位名稱像權重，且內容是數字"
    if p["numeric_share"] >= NUMERIC_SHARE:
        if p["unique"] <= CODE_MAX_LEVELS and not p["non_integer"]:
            return "code", f"內容是數字、只有 {p['unique']} 種值：可能是選項代碼或計數，需要說明或編碼表"
        return "numeric", f"內容是數字、有 {p['unique']} 種值：連續或計數變數，本 skill 不自行切分"
    if p["avg_len"] > 20 and p["unique_ratio"] > 0.5:
        return "text", "像開放題文字（平均長度 {:.0f} 字、多數答案都不同）".format(p["avg_len"])
    return "category", f"文字類別，{p['unique']} 種答案"


def detect_type(df, prof, args):
    """依資料結構提出類型判斷；欄位名稱只當輔助依據。回傳 dict(type, format, roles..., basis)。"""
    n, cols = len(df), list(df.columns)
    forced = getattr(args, "type", None)
    basis = []

    # 1) 0/1 矩陣：已完成的項目集合
    bin_cols = [c for c in cols if prof[c]["is01"]]
    rest = [c for c in cols if c not in bin_cols]
    if len(bin_cols) >= 2 and len(rest) <= 1 and (not rest or prof[rest[0]]["unique_ratio"] >= ID_UNIQUE):
        basis.append(f"{len(bin_cols)} 個欄位只有 0/1（或 TRUE/FALSE）")
        if rest:
            basis.append(f"另一欄 `{rest[0]}` 每列不同，可能是編號")
        return dict(type="itemset", format="wide01", tid_col=rest[0] if rest else None, item_cols=bin_cols,
                    basis=basis + ["0/1 可能代表「有買／沒買」，也可能是問卷「是／否」：需要使用者說明"])

    # 2) 購物籃格式：一列一張訂單，商品以分隔符號寫在同一格
    if len(cols) <= 3 and forced != "survey":
        text = [c for c in cols if prof[c]["numeric_share"] < 0.5 and prof[c]["sep_share"] >= 0.5]
        if text:
            c = max(text, key=lambda x: prof[x]["sep_share"])
            ids = [x for x in cols if x != c and prof[x]["unique_ratio"] >= ID_UNIQUE]
            basis.append(f"`{c}` 有 {prof[c]['sep_share']:.0%} 的列含分隔符號「{prof[c]['sep']}」，一格寫了多個值")
            return dict(type="shopping", format="basket", item_col=c, sep=prof[c]["sep"],
                        tid_col=ids[0] if ids else None, basis=basis)

    # 3) 長格式：一列一個（單位, 項目）
    if 2 <= len(cols) <= 6 and forced != "survey":
        cand = []
        for c in cols:
            p = prof[c]
            filled = n - p["missing"]
            if p["unique"] and 0.01 <= p["unique"] / max(1, filled) <= 0.67:
                cand.append(c)
        best = None
        for t in cand:
            for it in cols:
                if it == t or prof[it]["numeric_share"] >= 0.5 or prof[it]["unique"] < 2:
                    continue
                g = df.groupby(df[t].str.strip())[it].nunique()
                per = float(g.mean())
                if per >= 1.5:
                    score = (name_hint(t, HINT_ID) or "trans" in t.lower() or "order" in t.lower()) + per / 100
                    if best is None or score > best[0]:
                        best = (score, t, it, per)
        if best:
            _, t, it, per = best
            kv = float(df[it].str.contains("=", regex=False).mean())
            basis.append(f"`{t}` 的值會重複（{prof[t]['unique']:,} 種值、{n:,} 列），每個值平均對應 {per:.1f} 個不同的 `{it}`")
            others = [c for c in cols if c not in (t, it)]
            time_c = next((c for c in others if prof[c]["time_share"] >= 0.9), None)
            qty_c = next((c for c in others if prof[c]["numeric_share"] >= 0.95 and c != time_c), None)
            if kv >= 0.9:
                basis.append(f"`{it}` 有 {kv:.0%} 的值寫成「欄位=答案」：已完成的問卷項目")
                return dict(type="itemset", format="long_kv", tid_col=t, item_col=it, basis=basis)
            if time_c:
                basis.append(f"`{time_c}` 可解析為日期時間（{prof[time_c]['time_share']:.0%}）")
            if qty_c:
                basis.append(f"`{qty_c}` 全是數字，可能是數量（需確認）")
            return dict(type="shopping", format="long", tid_col=t, item_col=it, time_col=time_c, qty_col=qty_c,
                        basis=basis)

    # 4) 問卷類別表：一列一位受訪者
    if len(cols) >= 2:
        roles = {c: survey_role(c, prof[c], n) for c in cols}
        k = sum(r[0] == "category" for r in roles.values())
        basis.append(f"一列一個觀測單位、{len(cols)} 個欄位，其中 {k} 欄是文字類別")
        return dict(type="survey", format="wide", roles={c: r[0] for c, r in roles.items()},
                    role_reason={c: r[1] for c, r in roles.items()}, basis=basis)
    return dict(type="unsupported", format=None, basis=["只有 1 個欄位，無法判斷觀測單位與項目"])


def synonym_groups(items):
    """只差大小寫、全形半形或空白的商品名稱 → 疑似同義（需要使用者確認，不自動合併）。"""
    groups = {}
    for it in items:
        key = " ".join(unicodedata.normalize("NFKC", it).casefold().split())
        groups.setdefault(key, set()).add(it)
    return [sorted(v) for v in groups.values() if len(v) > 1]


def config_template(args, det, prof, df):
    cfg = {"data_path": args.data.replace("\\", "/"), "encoding": None, "out_dir": args.out.replace("\\", "/"),
           "data_type": None, "unit": None,
           "shopping": None, "survey": None,
           "question": {"type": None, "item": None, "target": None, "given": None},
           "params": {"min_count": None, "min_confidence": None, "min_lift": None},
           "validation": {"enabled": True},
           "report": {"title": "關聯規則分析報告", "background": None, "goal": None},
           "confirmations": []}
    t = det["type"]
    if t == "shopping" or (t == "itemset" and det["format"] == "wide01"):
        cfg["data_type"] = "shopping" if t == "shopping" else None
        cfg["shopping"] = {"format": det["format"], "tid_col": det.get("tid_col"), "item_col": det.get("item_col"),
                           "item_cols": det.get("item_cols"), "sep": det.get("sep"),
                           "time_col": det.get("time_col"), "qty_col": det.get("qty_col"),
                           "decisions": {}, "item_aliases": {}}
    if t == "survey" or (t == "itemset" and det["format"] == "long_kv"):
        cfg["data_type"] = "survey"
        roles = det.get("roles", {})
        cfg["survey"] = {"format": "wide" if t == "survey" else "long_kv",
                         "tid_col": det.get("tid_col"), "item_col": det.get("item_col"),
                         "id_col": next((c for c, r in roles.items() if r == "id"), None),
                         "columns": [c for c, r in roles.items() if r == "category"],
                         "exclude_cols": [{"name": c, "reason": det["role_reason"][c]} for c, r in roles.items()
                                          if r in ("id", "weight", "text")],
                         "missing_values": [], "missing_codes": {}, "codebook": {}, "cuts": {},
                         "categorical_as_is": [], "complete_case_confirmed": None}
    return cfg


def cmd_inspect(args):
    df, enc = read_table(args.data, args.encoding)
    n = len(df)
    prof = profile_columns(df)
    det = detect_type(df, prof, args)
    if args.type == "shopping" and det["type"] == "survey":
        sys.exit("使用者說明這是購物資料，但資料結構不是長格式、購物籃格式或 0/1 矩陣；"
                 "請用 --tid-col / --item-col 之外的方式先整理成支援格式（見 decision-tree.md）。")
    os.makedirs(args.out, exist_ok=True)
    cfg = config_template(args, det, prof, df)
    if args.type and cfg["data_type"] is None:
        cfg["data_type"] = args.type
    if args.unit:
        cfg["unit"] = args.unit

    print(f"## 資料概況\n- 檔案：`{args.data}`（編碼 {enc}）\n- {n:,} 列、{df.shape[1]} 個欄位\n")
    rows = []
    for c in df.columns:
        p = prof[c]
        role = det.get("roles", {}).get(c, "")
        rows.append({"欄位": c, "缺失（空白）": p["missing"], "不同值": p["unique"],
                     "數字比例": f"{p['numeric_share']:.0%}",
                     "初步角色": {"id": "可能是 ID", "weight": "可能是權重", "code": "數字代碼？",
                              "numeric": "數值（不支援）", "text": "開放文字", "category": "類別"}.get(role, ""),
                     "常見值": "、".join(f"{k}（{v}）" for k, v in p["top"][:5])})
    print(md_table(rows, list(rows[0])))
    print("\n### 前 5 列\n")
    head = df.head(5).to_dict("records")
    print(md_table(head, list(df.columns)))

    label = {"shopping": "購物清單", "survey": "問卷類別表", "itemset": "已完成的項目集合", "unsupported": "不支援的格式"}
    fmt_label = {"long": "長格式：一列一個商品", "basket": "購物籃格式：一列一張訂單、商品寫在同一格",
                 "wide01": "0/1 矩陣：一欄一個項目", "long_kv": "長格式：一列一個「欄位=答案」",
                 "wide": "一列一位受訪者、一欄一題", None: ""}
    print(f"\n## 資料類型判斷（提議，需要使用者確認）\n- 判斷：**{label[det['type']]}**（{fmt_label[det['format']]}）")
    for b in det["basis"]:
        print(f"- 依據：{b}")
    if args.type:
        print(f"- 使用者已說明資料類型：{args.type}（沿用，不再詢問）")

    notes, pending = [], []
    ident = int(df.duplicated().sum())
    if ident and det["type"] == "survey":
        notes.append(f"有 {ident:,} 列與其他列內容完全相同：**不會刪除**（可能是不同的人剛好回答相同）")
    elif ident:
        notes.append(f"有 {ident:,} 列與其他列完全相同：同一張訂單重複出現的商品會記為一次（記錄列數）；"
                     "不同訂單即使內容相同也全部保留")
    for c in df.columns:
        p = prof[c]
        if p["missing_like"]:
            pending.append(f"`{c}` 出現 {', '.join(p['missing_like'])}：可能代表缺失、拒答或不適用，"
                           "需要使用者說明是否當成缺失（不會自動當成「否」）")
        if p["ws_changed"]:
            notes.append(f"`{c}` 有 {p['ws_changed']:,} 格前後有空白：會去除前後空白並記錄")

    if det["type"] == "survey":
        roles, why = det["roles"], det["role_reason"]
        for c, r in roles.items():
            if r in ("id", "weight", "text"):
                notes.append(f"`{c}`：{why[c]} → 建議**不當成項目**（不進入分析，記錄原因）")
            elif r == "code":
                pending.append(f"`{c}`：{why[c]}。請使用者提供編碼表，或確認這些數字本身就是答案（例如人數）")
            elif r == "numeric":
                pending.append(f"`{c}`：{why[c]}。需要使用者提供切點，否則排除")
            if r == "category" and prof[c]["top_share"] >= HIGH_FREQ:
                notes.append(f"`{c}` 的「{prof[c]['top'][0][0]}」占 {prof[c]['top_share']:.0%}：當後件時 lift 多半接近 1")
        miss_cols = [c for c in cfg["survey"]["columns"] if prof[c]["missing"]]
        if cfg["survey"]["columns"]:
            sub = df[cfg["survey"]["columns"]].apply(lambda s: s.str.strip() == "")
            exc = int(sub.any(axis=1).sum())
            notes.append(f"若分析全部 {len(cfg['survey']['columns'])} 個類別欄位，完整個案分析會排除 {exc:,} 列、"
                         f"剩 {n - exc:,} 列（缺失欄位：{', '.join(miss_cols) or '無'}）；實際數字依使用者選的欄位而定")
        plan = ["保留原類別，每個答案寫成「欄位=答案」項目（原欄位名稱與答案都不改）",
                "使用者選擇要分析的欄位；ID、權重、開放文字不當成項目",
                "在選定欄位上做完整個案分析：任一選定欄位缺失的列排除；執行前告知排除與剩下的筆數並確認",
                "數字代碼需要編碼表或說明；數值欄位需要使用者提供切點，否則排除",
                "不合併答案、不刪除相同的列、不把缺失當成「否」"]
    elif det["type"] in ("shopping", "itemset") and det["format"] in ("long", "basket", "wide01"):
        s = dict(cfg["shopping"])
        prep = prep_shopping(df, s, preview=True)
        u = prep["units"]
        sizes = pd.Series([len(x) for x in u]) if u else pd.Series(dtype=int)
        if u:
            notes.append(f"整理後約 {len(u):,} 筆訂單、{len({i for x in u for i in x}):,} 種商品；"
                         f"每筆訂單平均 {sizes.mean():.2f} 項（中位數 {sizes.median():.0f}，最多 {sizes.max()}）")
        for q in prep["pending"]:
            pending.append(q["question"])
        for q in prep["problems"]:
            pending.append("資料格式問題：" + q)
        if s.get("qty_col"):
            notes.append(f"數量欄 `{s['qty_col']}` 只用來檢查 ≤ 0 的列；關聯規則只看「有沒有買」")
        if s.get("time_col"):
            notes.append(f"時間欄 `{s['time_col']}` 只用來把最後 30% 的訂單切成驗證資料")
        plan = ["去除商品名稱前後空白（固定規則，記錄影響列數）",
                "同一張訂單中重複出現的商品記為一次（固定規則，記錄影響列數）",
                "疑似同義的商品名稱、缺失的商品值、空的訂單、數量 ≤ 0：逐項請使用者確認",
                "不合併商品類別、不刪除內容相同的訂單"]
        if det["type"] == "itemset":
            pending.insert(0, "0/1 代表的是「有買／沒買」（購物）還是問卷的「是／否」？")
    elif det["type"] == "itemset":
        plan = ["把「欄位=答案」項目還原成一列一位受訪者，之後依問卷流程處理"]
    else:
        plan = []
        pending.append("資料不符合支援的格式；需要整理成：長格式（編號, 商品）、購物籃格式（一列一筆、商品以逗號分隔）、"
                       "0/1 矩陣，或問卷類別表（一列一位受訪者、一欄一題）")

    if notes:
        print("\n## 檢查結果\n" + "\n".join(f"- {x}" for x in notes))
    if plan:
        print("\n## 前處理方案（提議）\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(plan, 1)))
    if pending:
        print("\n## 需要使用者說明或決定的事項（一次問一項）\n" + "\n".join(f"- {x}" for x in pending))

    out = {"data": args.data, "encoding": enc, "rows": n, "columns": list(df.columns), "detected": det,
           "profile": prof, "identical_rows": ident, "notes": notes, "plan": plan, "pending": pending}
    with open(os.path.join(args.out, "inspect.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    with open(os.path.join(args.out, "config_template.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"\n輸出：{args.out}/inspect.json、{args.out}/config_template.json")


# ----------------------------------------------------------------------------- 前處理：購物清單整理

def ask(decisions, key, count, question, options, pending):
    """需要使用者決定的事項：config 已有答案就回傳答案，否則記入 pending。"""
    if count == 0:
        return None
    v = decisions.get(key)
    if v is None:
        pending.append({"key": key, "count": count, "question": question, "options": options})
        return None
    if v not in options:
        sys.exit(f"decisions.{key} 只能是 {list(options)}，目前是 {v!r}")
    return v


def prep_shopping(df, s, preview=False):
    """購物清單整理：固定規則 + 需要使用者確認的事項。回傳 units（每筆訂單的商品集合）與紀錄。"""
    log, pending, problems = [], [], []
    dec = s.get("decisions") or {}
    fmtx = s.get("format")
    for k in ("tid_col", "item_col", "time_col", "qty_col"):
        if s.get(k) and s[k] not in df.columns:
            problems.append(f"找不到欄位 `{s[k]}`（{k}）")
    if problems:
        return dict(units=[], ids=[], times=None, log=log, pending=pending, problems=problems)

    # (1) 轉成「訂單, 商品」長格式
    if fmtx == "long":
        if not (s.get("tid_col") and s.get("item_col")):
            problems.append("長格式需要指定訂單編號欄（tid_col）與商品欄（item_col）")
            return dict(units=[], ids=[], times=None, log=log, pending=pending, problems=problems)
        d = pd.DataFrame({"tid": df[s["tid_col"]].astype(str), "item": df[s["item_col"]].astype(str)})
        if s.get("time_col"):
            d["time"] = df[s["time_col"]].astype(str)
        if s.get("qty_col"):
            d["qty"] = df[s["qty_col"]].astype(str)
        d["empty_cell"] = False
    elif fmtx == "basket":
        if not s.get("item_col"):
            problems.append("購物籃格式需要指定商品欄（item_col）")
            return dict(units=[], ids=[], times=None, log=log, pending=pending, problems=problems)
        sep = s.get("sep") or ","
        tid = df[s["tid_col"]].astype(str) if s.get("tid_col") else pd.Series([f"R{i + 1}" for i in range(len(df))])
        cell = df[s["item_col"]].astype(str)
        d = pd.DataFrame({"tid": tid.values, "item": cell.str.split(sep, regex=False).values,
                          "empty_cell": (cell.str.strip() == "").values})
        if s.get("time_col"):
            d["time"] = df[s["time_col"]].astype(str).values
        d = d.explode("item").reset_index(drop=True)
        log_add(log, "轉換格式", f"以「{sep}」拆開每一格的商品，一列一張訂單 → 一列一個商品", "統一成（訂單, 商品）格式",
                f"{len(df):,} 列 → {len(d):,} 列")
    elif fmtx == "wide01":
        cols = s.get("item_cols") or [c for c in df.columns if c != s.get("tid_col")]
        blanks = int(sum((df[c].str.strip() == "").sum() for c in cols))
        bad = sorted({v for c in cols for v in df[c].str.strip().str.lower().unique()} - {"0", "1", "true", "false", ""})
        if blanks:
            problems.append(f"0/1 欄位有 {blanks:,} 個空白格：不能當成 0（沒買）。請補成 0 或 1，或說明空白代表什麼")
        if bad:
            problems.append(f"0/1 欄位出現其他值 {bad[:10]}：請先整理成只有 0/1")
        if problems:
            return dict(units=[], ids=[], times=None, log=log, pending=pending, problems=problems)
        tid = df[s["tid_col"]].astype(str) if s.get("tid_col") else pd.Series([f"R{i + 1}" for i in range(len(df))])
        recs = []
        for r, t in enumerate(tid):
            got = [c for c in cols if df[c].iloc[r].strip().lower() in ("1", "true")]
            recs += [(t, c, False) for c in got] or [(t, "", True)]
        d = pd.DataFrame(recs, columns=["tid", "item", "empty_cell"])
        log_add(log, "轉換格式", "0/1 矩陣中值為 1 的欄位名稱當成商品", "統一成（訂單, 商品）格式",
                f"{len(df):,} 列 × {len(cols)} 欄 → {int((~d['empty_cell']).sum()):,} 個（訂單, 商品）")
    else:
        problems.append(f"不支援的購物資料格式：{fmtx}")
        return dict(units=[], ids=[], times=None, log=log, pending=pending, problems=problems)

    # (2) 訂單編號缺失：無法判斷屬於哪一張訂單 → 格式問題
    d["tid"] = d["tid"].str.strip()
    no_tid = int((d["tid"] == "").sum())
    if no_tid:
        problems.append(f"{no_tid:,} 列沒有訂單編號，無法判斷屬於哪一張訂單：請補上編號或說明如何處理")

    # (3) 固定規則：去除商品名稱前後空白
    raw = d["item"].astype(str)
    d["item"] = raw.str.strip()
    ws = int(((raw != d["item"]) & ~d["empty_cell"]).sum())
    log_add(log, "固定規則", "去除商品名稱前後空白", "避免同一商品因多打空白被當成不同商品", f"{ws:,} 列")

    # (4) 商品值缺失（需確認）
    miss = (d["item"] == "") & ~d["empty_cell"]
    v = ask(dec, "missing_item", int(miss.sum()),
            f"有 {int(miss.sum()):,} 列的商品值是空白。建議排除這些列（不知道是什麼商品，也不能當成「沒買」）；"
            "若不同意，請先補齊資料", ["exclude"], pending)
    if v == "exclude":
        log_add(log, "使用者確認", "排除商品值空白的列", "不知道是什麼商品，不能當成沒買", f"{int(miss.sum()):,} 列")
    if not miss.sum():
        log_add(log, "檢查", "商品值空白", "未發現", "0 列")
    d = d[~miss].copy() if v == "exclude" or preview else d

    # (5) 數量 ≤ 0 或無法解析（需確認）
    if "qty" in d.columns:
        q = pd.to_numeric(d["qty"], errors="coerce")
        bad = (q <= 0) | q.isna()
        if not bad.sum():
            log_add(log, "檢查", "數量 ≤ 0 或無法解析", "未發現", "0 列")
        v = ask(dec, "nonpositive_qty", int(bad.sum()),
                f"有 {int(bad.sum()):,} 列的數量 ≤ 0 或不是數字（可能是退貨、取消或輸入錯誤）。"
                "要排除這些列（exclude），還是照樣當成有買（keep）？", ["exclude", "keep"], pending)
        if v:
            log_add(log, "使用者確認", "數量 ≤ 0 或無法解析的列：" + ("排除" if v == "exclude" else "保留"),
                    "使用者說明", f"{int(bad.sum()):,} 列")
        if v == "exclude":
            d = d[~bad].copy()

    # (6) 疑似同義商品名稱（需確認；只套用使用者確認過的對照）
    items = sorted(d.loc[~d["empty_cell"], "item"].unique())
    groups = synonym_groups(items)
    v = ask(dec, "synonyms", len(groups),
            f"有 {len(groups)} 組商品名稱只差大小寫、全形半形或空白，例如 "
            + "；".join(" / ".join(g) for g in groups[:5])
            + "。請逐組說明是否為同一商品；確認為同一商品的寫入 item_aliases（原名 → 統一名稱），"
              "全部看過後把 decisions.synonyms 設為 \"reviewed\"", ["reviewed"], pending)
    if not groups:
        log_add(log, "檢查", "疑似同義的商品名稱（只差大小寫、全形半形或空白）", "未發現", "0 組")
    aliases = s.get("item_aliases") or {}
    for src, dst in aliases.items():
        hit = d["item"] == src
        d.loc[hit, "item"] = dst
        log_add(log, "使用者確認", f"「{src}」改寫為「{dst}」", "使用者確認為同一商品", f"{int(hit.sum()):,} 列")
    if v == "reviewed" and not aliases:
        log_add(log, "使用者確認", "疑似同義的商品名稱全部維持原樣", "使用者確認不是同一商品", "0 列")

    # (7) 固定規則：同一張訂單重複出現的商品記為一次
    real = d[~d["empty_cell"] & (d["item"] != "")]
    dup = int(real.duplicated(["tid", "item"]).sum())
    log_add(log, "固定規則", "同一張訂單中重複出現的商品記為一次",
            "關聯規則只看一張訂單「有沒有」這項商品（Tan et al. 5.1）；不同訂單即使內容相同也全部保留", f"{dup:,} 列")

    # (8) 空的訂單（需確認）
    all_t = list(dict.fromkeys(d["tid"]))
    basket = real.groupby("tid", sort=False)["item"].agg(frozenset).to_dict()
    empty = [t for t in all_t if t not in basket]
    v = ask(dec, "empty_baskets", len(empty),
            f"有 {len(empty):,} 筆訂單沒有任何商品。要排除（exclude），還是保留在總訂單數裡（keep，會讓所有 support 變小）？",
            ["exclude", "keep"], pending)
    if v:
        log_add(log, "使用者確認", "空的訂單：" + ("排除" if v == "exclude" else "保留在分母"), "使用者說明",
                f"{len(empty):,} 筆訂單")
    if not empty:
        log_add(log, "檢查", "沒有任何商品的訂單", "未發現", "0 筆訂單")
    ids = [t for t in all_t if t in basket or v == "keep"]
    units = [basket.get(t, frozenset()) for t in ids]

    # (9) 時間（只用來決定驗證資料的切法）
    times = None
    if "time" in d.columns:
        tt = pd.to_datetime(d["time"], errors="coerce", format="mixed")
        tmin = tt.groupby(d["tid"]).min()
        bad_t = int(tmin.reindex(ids).isna().sum())
        if bad_t:
            log_add(log, "固定規則", "時間無法解析的訂單存在，驗證改用隨機切分", "時間切分需要每筆訂單都有時間", f"{bad_t:,} 筆訂單")
        else:
            times = list(tmin.reindex(ids))
    return dict(units=units, ids=ids, times=times, log=log, pending=pending, problems=problems)


# ----------------------------------------------------------------------------- 前處理：類別問卷編碼

def kv_to_wide(df, sv, problems, log):
    """「編號, 欄位=答案」長格式 → 一列一位受訪者。"""
    t, it = sv.get("tid_col"), sv.get("item_col")
    if not (t in df.columns and it in df.columns):
        problems.append("long_kv 格式需要指定 tid_col 與 item_col")
        return None
    kv = df[it].str.strip().str.partition("=")
    d = pd.DataFrame({"id": df[t].str.strip(), "col": kv[0], "val": kv[2]})
    multi = d.duplicated(["id", "col"]).sum()
    if multi:
        problems.append(f"有 {multi:,} 筆同一位受訪者在同一欄位有多個答案（複選題），本 skill 不支援；請先拆成每個選項一欄")
        return None
    w = d.pivot(index="id", columns="col", values="val").fillna("").reset_index()
    log_add(log, "轉換格式", "「欄位=答案」項目還原成一列一位受訪者", "統一成問卷類別表", f"{len(w):,} 位受訪者")
    return w


def prep_survey(df, sv, preview=False):
    """類別問卷編碼：保留原類別，項目為「欄位=答案」；在選定欄位上做完整個案分析。"""
    log, pending, problems = [], [], []
    if sv.get("format") == "long_kv":
        df = kv_to_wide(df, sv, problems, log)
        if df is None:
            return dict(units=[], ids=[], times=None, log=log, pending=pending, problems=problems)
        sv = dict(sv, id_col="id")
    n0 = len(df)
    cols = sv.get("columns") or []
    excl = {e["name"]: e.get("reason", "") for e in sv.get("exclude_cols") or []}
    id_col = sv.get("id_col")
    if not cols:
        problems.append("尚未選擇要分析的欄位（survey.columns）")
    for c in cols + list(excl) + ([id_col] if id_col else []):
        if c not in df.columns:
            problems.append(f"找不到欄位 `{c}`")
    if problems:
        return dict(units=[], ids=[], times=None, log=log, pending=pending, problems=problems)
    if id_col in cols or set(cols) & set(excl):
        problems.append("ID 或已排除的欄位不能同時列在分析欄位中")
    if id_col:
        log_add(log, "欄位角色", f"`{id_col}` 當作受訪者編號，不當成項目", "編號沒有分析意義", f"{n0:,} 列")
        dup_id = int(df[id_col].duplicated().sum())
        if dup_id:
            log_add(log, "提醒", f"`{id_col}` 有重複值，全部保留（不刪除）", "不因內容相同而刪除觀測", f"{dup_id:,} 列")
    for c, why in excl.items():
        log_add(log, "欄位角色", f"`{c}` 不當成項目", why or "使用者決定", f"{n0:,} 列")
    others = [c for c in df.columns if c not in cols and c not in excl and c != id_col]
    if others:
        log_add(log, "欄位角色", "未選入分析的欄位：" + "、".join(f"`{c}`" for c in others), "使用者只選擇部分欄位",
                f"{len(others)} 欄")

    data = pd.DataFrame(index=df.index)
    miss_global = set(sv.get("missing_values") or [])
    miss_codes = sv.get("missing_codes") or {}
    codebook = sv.get("codebook") or {}
    cuts = sv.get("cuts") or {}
    as_is = set(sv.get("categorical_as_is") or [])
    for c in cols:
        raw = df[c].astype(str)
        v = raw.str.strip()
        ws = int((raw != v).sum())
        if ws:
            log_add(log, "固定規則", f"`{c}`：去除答案前後空白", "避免同一答案因空白被當成不同答案", f"{ws:,} 列")
        codes = miss_global | set(miss_codes.get(c, []))
        m_blank = v == ""
        m_code = v.isin(codes) & ~m_blank
        if m_code.sum():
            log_add(log, "使用者說明", f"`{c}`：{sorted(codes & set(v.unique()))} 當成缺失", "使用者說明這些值是拒答／不知道／不適用",
                    f"{int(m_code.sum()):,} 列")
        v = v.mask(m_blank | m_code, "")
        filled = v[v != ""]
        numeric_like = len(filled) and is_num(filled).mean() >= NUMERIC_SHARE
        if c in cuts:
            spec = cuts[c]
            x = pd.to_numeric(filled, errors="coerce")
            if x.isna().any():
                problems.append(f"`{c}` 有 {int(x.isna().sum())} 個值不是數字，無法套用使用者給的切點")
                continue
            br = spec["breaks"]
            labels = spec.get("labels") or [f"[{br[i]},{br[i + 1]})" for i in range(len(br) - 1)]
            b = pd.cut(x, bins=br, right=spec.get("right", False), labels=labels)
            if b.isna().any():
                problems.append(f"`{c}` 有 {int(b.isna().sum())} 個值落在切點範圍外：請使用者調整切點")
                continue
            v.loc[filled.index] = b.astype(str)
            log_add(log, "使用者提供", f"`{c}`：依使用者給的切點 {br} 分組，標籤 {labels}", "使用者提供的切點",
                    f"{len(filled):,} 列")
        elif c in codebook:
            mp = {str(k): str(val) for k, val in codebook[c].items()}
            unk = sorted(set(filled) - set(mp))
            if unk:
                problems.append(f"`{c}` 有編碼表沒有的值 {unk[:10]}：請補齊編碼表或說明這些值")
                continue
            v.loc[filled.index] = filled.map(mp)
            log_add(log, "使用者提供", f"`{c}`：依編碼表把代碼換成標籤", "使用者提供的編碼表", f"{len(filled):,} 列")
        elif numeric_like and c not in as_is:
            nu = filled.nunique()
            if nu <= CODE_MAX_LEVELS:
                problems.append(f"`{c}` 是數字（{nu} 種值）：請提供編碼表（codebook），或確認數字本身就是答案（categorical_as_is）")
            else:
                problems.append(f"`{c}` 是數值變數（{nu} 種值）：本 skill 不自行切分；請提供切點（cuts）或不選這一欄")
            continue
        elif c in as_is and numeric_like:
            log_add(log, "使用者確認", f"`{c}`：數字本身就是答案，照原值當成類別", "使用者說明", f"{len(filled):,} 列")
        data[c] = v
    if problems:
        return dict(units=[], ids=[], times=None, log=log, pending=pending, problems=problems)

    # 完整個案分析
    miss = data == ""
    per_col = {c: int(miss[c].sum()) for c in cols}
    drop = miss.any(axis=1)
    exc, rem = int(drop.sum()), int((~drop).sum())
    detail = "、".join(f"{c} {k:,}" for c, k in per_col.items() if k)
    conf = sv.get("complete_case_confirmed") or {}
    if exc and (conf.get("excluded") != exc or conf.get("remaining") != rem):
        pending.append({"key": "complete_case", "count": exc,
                        "question": f"完整個案分析：選定的 {len(cols)} 個欄位中任一個缺失的列會被排除，"
                                    f"共排除 {exc:,} 列、剩下 {rem:,} 列（各欄缺失：{detail}）。"
                                    "同意的話，把 complete_case_confirmed 設為 "
                                    f'{{"excluded": {exc}, "remaining": {rem}}}',
                        "options": ["confirm"]})
    log_add(log, "完整個案分析", f"選定欄位任一缺失的列排除（各欄缺失：{detail or '無'}）",
            "缺失、拒答、不適用不能當成任何答案，也不能當成「否」", f"排除 {exc:,} 列，剩 {rem:,} 列")
    keep = data[~drop]
    units = [frozenset(f"{c}={r[c]}" for c in cols) for _, r in keep.iterrows()]
    ids = (df.loc[keep.index, id_col].astype(str).tolist() if id_col else [f"R{i + 1}" for i in keep.index])
    ident = int(keep.duplicated().sum())
    log_add(log, "提醒", "答案完全相同的受訪者全部保留", "不因兩筆觀測內容相同而刪除其中一筆", f"{ident:,} 列與其他列相同")
    freq = {c: keep[c].value_counts().to_dict() for c in cols}
    return dict(units=units, ids=ids, times=None, log=log, pending=pending, problems=problems, freq=freq,
                columns=cols, n_before=n0)


# ----------------------------------------------------------------------------- 指標與探勘

def measures(n, cA, cB, cAB):
    """規則 A→B 的指標（定義見 Tan et al. 5.1、5.7 節）。"""
    sA, sB, sAB = cA / n, cB / n, cAB / n
    conf = cAB / cA if cA else float("nan")
    lift = sAB / (sA * sB) if sA * sB else float("nan")
    return dict(count=int(cAB), support=sAB, confidence=conf, base=sB, lift=lift,
                leverage=sAB - sA * sB,
                cosine=sAB / math.sqrt(sA * sB) if sA * sB else float("nan"),
                kulczynski=0.5 * (cAB / cA + cAB / cB) if cA and cB else float("nan"),
                lhs_count=int(cA))


class Binary:
    """觀測單位 × 項目 的 0/1 矩陣。"""

    def __init__(self, units):
        self.items = sorted({i for u in units for i in u})
        self.idx = {it: j for j, it in enumerate(self.items)}
        self.n = len(units)
        self.X = np.zeros((self.n, len(self.items)), dtype=bool)
        for r, u in enumerate(units):
            for it in u:
                self.X[r, self.idx[it]] = True
        self.single = self.X.sum(axis=0)

    def count(self, s):
        if any(i not in self.idx for i in s):
            return 0
        if not s:
            return self.n
        return int(self.X[:, [self.idx[i] for i in s]].all(axis=1).sum())


def frequent(B, min_count, max_len):
    from mlxtend.frequent_patterns import fpgrowth
    if B.n == 0 or not B.items:
        return {}
    df = pd.DataFrame(B.X, columns=B.items)
    fi = fpgrowth(df, min_support=min_count / B.n, use_colnames=True, max_len=max_len)
    sup = {frozenset(s): int(round(v * B.n)) for s, v in zip(fi["itemsets"], fi["support"])}
    return {k: c for k, c in sup.items() if c >= min_count}


def attr(item):
    return item.split("=", 1)[0] if "=" in item else None


def functional_deps(units):
    """欄位 X → 欄位 Y 的函數關係：每個 X 值只對應一個 Y 值（例如 縣市 → 區域），這類規則必然成立。"""
    rows = [{attr(i): i for i in u} for u in units]
    cols = sorted({k for r in rows for k in r})
    deps = set()
    for x in cols:
        for y in cols:
            if x == y:
                continue
            seen, cnt, ok = {}, {}, True
            for r in rows:
                if x in r and y in r:
                    cnt[r[x]] = cnt.get(r[x], 0) + 1
                    if seen.setdefault(r[x], r[y]) != r[y]:
                        ok = False
                        break
            if ok and len(set(seen.values())) > 1 and cnt and min(cnt.values()) >= FD_MIN:
                deps.add((x, y))
    return deps


def default_params(q, n):
    """固定的預設門檻（decision-tree.md 第 4 節）。"""
    if q in SHOP_Q:
        mc, rule = max(10, math.ceil(0.005 * n)), "max(10, ⌈0.5% × N⌉)"
    elif q == "cooccur":
        mc, rule = max(10, math.ceil(0.05 * n)), "max(10, ⌈5% × N⌉)"
    else:
        mc, rule = max(10, math.ceil(0.01 * n)), "max(10, ⌈1% × N⌉)"
    conf = {"recommend": 0.1, "explore": 0.1, "cooccur": 0.5}.get(q)
    lift = None if q == "given" else 1.0
    return {"min_count": mc, "min_count_rule": rule, "min_confidence": conf, "min_lift": lift}


def rule_text(A, B):
    return f"{fmt_set(A)} → {fmt_set(B)}"


def mine(q, B, units, P, qa):
    """依分析問題的固定規則形式產生結果。回傳 (rows, kind, filter_log)。"""
    mc, mconf, mlift = P["min_count"], P["min_confidence"] or 0.0, P["min_lift"]
    flog = []
    rows = []

    def keep_rule(m):
        return m["count"] >= mc and (mlift is None or m["lift"] > mlift) and m["confidence"] >= mconf - 1e-12

    if q in ("bundle", "placement"):
        sup = frequent(B, mc, 3 if q == "bundle" else 2)
        cand = [I for I in sup if len(I) >= 2]
        flog.append(("至少共同出現 {} 筆的 {} 項組合".format(mc, "2–3" if q == "bundle" else "2"), len(cand)))
        for I in cand:
            if q == "bundle":
                lifts = [measures(B.n, sup[I - {i}], sup[frozenset([i])], sup[I])["lift"] for i in I]
                if min(lifts) <= 1:
                    continue
                mx = max(sup[frozenset([i])] for i in I)
                rows.append(dict(result="{" + " + ".join(sorted(I)) + "}", items=sorted(I), size=len(I),
                                 count=sup[I], support=sup[I] / B.n, min_lift=min(lifts),
                                 all_confidence=sup[I] / mx))
            else:
                a, b = sorted(I)
                m = measures(B.n, sup[frozenset([a])], sup[frozenset([b])], sup[I])
                if m["lift"] <= 1:
                    continue
                rows.append(dict(result=f"{a} ↔ {b}", lhs=[a], rhs=[b], count=m["count"], support=m["support"],
                                 lift=m["lift"], cosine=m["cosine"], kulczynski=m["kulczynski"],
                                 conf_ab=m["confidence"], conf_ba=sup[I] / sup[frozenset([b])]))
        flog.append(("其中正相關（{}）".format("每個方向 lift > 1" if q == "bundle" else "lift > 1"), len(rows)))
        if q == "bundle":
            rows.sort(key=lambda r: (-r["count"], -r["min_lift"], r["result"]))
        else:
            rows.sort(key=lambda r: (-r["lift"], -r["count"], r["result"]))
        return rows, ("itemset" if q == "bundle" else "pair"), flog

    if q in ("recommend", "given"):
        A = frozenset(qa)
        cA = B.count(A)
        lhs_attrs = {attr(i) for i in A}
        flog.append((f"前件 {fmt_set(A)} 出現筆數", cA))
        if cA:
            sel = B.X[:, [B.idx[i] for i in A]].all(axis=1)
            co = B.X[sel].sum(axis=0)
            for j, it in enumerate(B.items):
                if it in A or (q == "given" and attr(it) in lhs_attrs):
                    continue
                m = measures(B.n, cA, int(B.single[j]), int(co[j]))
                rows.append(dict(result=rule_text(A, [it]), lhs=sorted(A), rhs=[it], **m))
        flog.append(("候選後件", len(rows)))
        rows = [r for r in rows if keep_rule(r)]
        flog.append((f"共同出現 ≥ {mc} 筆" + (f"、lift > {mlift}" if mlift is not None else "")
                     + (f"、confidence ≥ {mconf}" if mconf else ""), len(rows)))
        if q == "recommend":
            rows.sort(key=lambda r: (-r["lift"], -r["confidence"], r["result"]))
        else:
            for r in rows:
                r["note"] = "lift ≤ 1：比例不高於整體，不是這個特徵特有" if r["lift"] <= 1 else ""
            rows.sort(key=lambda r: (-r["confidence"], -r["lift"], r["result"]))
        return rows, "rule", flog

    # explore / cooccur / target：前件 ≤ 2、後件 1
    sup = frequent(B, mc, 3)
    target = frozenset(qa) if q == "target" else None
    allr = []
    for I, cI in sup.items():
        if len(I) < 2:
            continue
        for b in sorted(I):
            A = I - {b}
            if target is not None and b not in target:
                continue
            m = measures(B.n, sup[A], sup[frozenset([b])], cI)
            allr.append(dict(result=rule_text(A, [b]), lhs=sorted(A), rhs=[b], **m))
    flog.append((f"共同出現 ≥ {mc} 筆、前件 ≤ 2 項、後件 1 項" + ("（後件為指定答案）" if target else ""), len(allr)))
    rows = [r for r in allr if keep_rule(r)]
    flog.append((f"lift > {mlift}" + (f"、confidence ≥ {mconf}" if mconf else ""), len(rows)))
    # 冗餘：較短前件的規則 confidence 已相同或更高
    red = []
    for r in rows:
        A, b = frozenset(r["lhs"]), r["rhs"][0]
        if len(A) == 2 and any(sup[frozenset([a, b])] / sup[frozenset([a])] >= r["confidence"] - 1e-12 for a in A):
            red.append(r["result"])
    rows = [r for r in rows if r["result"] not in set(red)]
    flog.append(("移除冗餘規則（較短前件的 confidence 已相同或更高）", len(rows)))
    if q in SURVEY_Q:
        deps = functional_deps(units)
        dfn = [r for r in rows if any((attr(x), attr(r["rhs"][0])) in deps for x in r["lhs"])]
        if dfn:
            rows = [r for r in rows if r not in dfn]
        flog.append(("移除定義性規則（欄位間有函數關係，必然成立）" + (
            "：" + "、".join(f"{x}→{y}" for x, y in sorted(deps)) if deps else "：無"), len(rows)))
    for r in rows:
        r["note"] = "confidence = 1：請確認是否定義上必然" if r["confidence"] >= 1 - 1e-12 else ""
    rows.sort(key=lambda r: (-r["lift"], -r["count"], r["result"]))
    return rows, "rule", flog


def split_units(units, times, seed):
    n = len(units)
    if n < VALID_MIN_UNITS:
        return None, f"觀測單位 {n:,} < {VALID_MIN_UNITS}，切出 30% 後驗證資料太少：不做驗證，結果全部視為探索性"
    k = int(round(n * TEST_FRAC))
    if times is not None:
        order = sorted(range(n), key=lambda i: (times[i], i))
        return (sorted(order[:n - k]), sorted(order[n - k:])), f"依時間切分：最早的 70%（{n - k:,}）探勘、最晚的 30%（{k:,}）驗證"
    rng = np.random.default_rng(seed)
    perm = rng.permutation(n)
    return (sorted(perm[:n - k].tolist()), sorted(perm[n - k:].tolist())), \
        f"隨機切分（seed = {seed}）：70%（{n - k:,}）探勘、30%（{k:,}）驗證"


def validate(rows, kind, T, alpha):
    """在驗證資料上重算，單尾 Fisher exact test（H1：正相關）＋ Benjamini–Hochberg。"""
    from scipy.stats import false_discovery_control, fisher_exact

    def test(A, Bs):
        cA, cB, cAB = T.count(A), T.count(Bs), T.count(A | Bs)
        if not (cA and cB):
            return 1.0, float("nan"), cAB
        p = fisher_exact([[cAB, cA - cAB], [cB - cAB, T.n - cA - cB + cAB]], alternative="greater")[1]
        return p, measures(T.n, cA, cB, cAB)["lift"], cAB

    idx, ps = [], []
    for k, r in enumerate(rows):
        if kind == "itemset":
            I = frozenset(r["items"])
            res = [test(I - {i}, frozenset([i])) for i in I]
            p, lift, c = max(x[0] for x in res), min(x[1] for x in res), T.count(I)
        else:
            if r.get("lift", 2) <= 1:
                r.update(test_count=None, test_lift=None, q_value=None, replicated="不檢驗（lift ≤ 1）")
                continue
            p, lift, c = test(frozenset(r["lhs"]), frozenset(r["rhs"]))
        r.update(test_count=c, test_lift=lift, p_value=p)
        idx.append(k)
        ps.append(min(1.0, p))
    if ps:
        qs = false_discovery_control(ps, method="bh")
        for k, q in zip(idx, qs):
            r = rows[k]
            r["q_value"] = float(q)
            ok = (r["test_lift"] == r["test_lift"]) and r["test_lift"] > 1 and q < alpha
            r["replicated"] = "是" if ok else "否"
    return sum(1 for r in rows if r.get("replicated") == "是"), len(ps)


# ----------------------------------------------------------------------------- run

def load_config(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def prepare(cfg):
    df, enc = read_table(cfg["data_path"], cfg.get("encoding"))
    t = cfg.get("data_type")
    if t == "shopping":
        prep = prep_shopping(df, cfg.get("shopping") or {})
    elif t == "survey":
        prep = prep_survey(df, cfg.get("survey") or {})
    else:
        sys.exit("config 的 data_type 必須是 shopping 或 survey（由使用者確認）")
    prep.update(rows=len(df), columns=list(df.columns), encoding=enc)
    return prep


def check_question(cfg, B):
    q = cfg.get("question") or {}
    t, problems = q.get("type"), []
    allowed = SHOP_Q if cfg["data_type"] == "shopping" else SURVEY_Q
    if t not in allowed:
        return None, None, [f"question.type 必須是 {allowed} 之一（由使用者選擇）"]
    arg = None
    if t == "recommend":
        arg = q.get("item")
        arg = [arg] if isinstance(arg, str) else (arg or [])
        if not arg:
            problems.append("請使用者輸入商品名稱（question.item）")
    elif t == "target":
        arg = q.get("target")
        arg = [arg] if isinstance(arg, str) else (arg or [])
        if len(arg) != 1:
            problems.append("question.target 需要一個「欄位=答案」")
    elif t == "given":
        arg = q.get("given")
        arg = [arg] if isinstance(arg, str) else (arg or [])
        if not arg:
            problems.append("question.given 需要至少一個「欄位=答案」")
    for x in arg or []:
        if x not in B.idx:
            close = difflib.get_close_matches(x, B.items, n=5, cutoff=0.5) + \
                [i for i in B.items if x.lower() in i.lower()][:5]
            problems.append(f"資料中沒有「{x}」這個項目" + (f"；相近的有：{sorted(set(close))[:8]}" if close else "")
                            + "。請使用者確認正確名稱（不自行替換）")
    return t, arg, problems


def stop(code, title, items, out_dir, kind):
    os.makedirs(out_dir, exist_ok=True)
    print(f"## {title}")
    for x in items:
        print(f"- {x['question'] if isinstance(x, dict) else x}")
    with open(os.path.join(out_dir, "status.json"), "w", encoding="utf-8") as f:
        json.dump({"status": kind, "items": items}, f, ensure_ascii=False, indent=1)
    sys.exit(code)


def cmd_run(args):
    cfg = load_config(args.config)
    out = cfg.get("out_dir") or "arules_output"
    prep = prepare(cfg)
    if prep["problems"]:
        stop(1, "資料不符合支援的格式（需要使用者調整，不自行猜測）", prep["problems"], out, "unsupported")
    if prep["pending"]:
        stop(2, "以下事項需要使用者確認後才能執行（一次問一項）", prep["pending"], out, "pending")
    units, times = prep["units"], prep["times"]
    if not units:
        stop(1, "前處理後沒有任何觀測單位", ["請檢查資料或前處理設定"], out, "unsupported")

    Ball = Binary(units)
    q, qa, problems = check_question(cfg, Ball)
    if problems:
        stop(1, "分析問題的設定需要補充", problems, out, "question")

    vcfg = cfg.get("validation") or {}
    seed = vcfg.get("seed", SEED)
    split, split_note = (None, "使用者選擇不做驗證") if vcfg.get("enabled") is False else split_units(units, times, seed)
    mine_idx = split[0] if split else list(range(len(units)))
    mine_units = [units[i] for i in mine_idx]
    B = Binary(mine_units)
    if qa:
        miss = [x for x in qa if x not in B.idx]
        if miss:
            stop(1, "指定項目沒有出現在探勘資料中", [f"「{x}」只出現在驗證資料，或次數太少" for x in miss], out, "question")

    P = default_params(q, B.n)
    user = {k: v for k, v in (cfg.get("params") or {}).items() if v is not None}
    for k, v in user.items():
        P[k] = v
    P["source"] = {k: ("使用者調整" if k in user else "預設規則") for k in ("min_count", "min_confidence", "min_lift")}

    rows, kind, flog = mine(q, B, mine_units, P, qa)
    n_rep = n_test = 0
    T = None
    if split:
        T = Binary([units[i] for i in split[1]])
        n_rep, n_test = validate(rows, kind, T, vcfg.get("alpha", ALPHA))

    os.makedirs(out, exist_ok=True)
    pd.DataFrame([{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v) for k, v in r.items()}
                  for r in rows]).to_csv(os.path.join(out, "results_full.csv"), index=False, encoding="utf-8-sig")
    label = QLABEL[q].format(x="、".join(qa or []))
    res = {
        "version": VERSION, "generated": datetime.now().isoformat(timespec="seconds"),
        "config_path": args.config,
        "data": {"path": cfg["data_path"], "encoding": prep["encoding"], "rows": prep["rows"],
                 "columns": prep["columns"], "type": cfg["data_type"],
                 "format": (cfg.get("shopping") or cfg.get("survey") or {}).get("format"),
                 "units": len(units), "items": len(Ball.items), "unit": cfg.get("unit")},
        "preprocessing": prep["log"],
        "survey_columns": prep.get("columns"), "survey_freq": prep.get("freq"),
        "question": {"type": q, "label": label, "form": QFORM[q], "arg": qa},
        "params": P,
        "split": {"note": split_note, "mine_units": B.n, "test_units": T.n if T else 0},
        "filter_log": [{"step": s, "remaining": c} for s, c in flog],
        "n_results": len(rows), "kind": kind,
        "top": rows[:TOP],
        "validation": {"enabled": bool(split), "tested": n_test, "replicated": n_rep,
                       "alpha": vcfg.get("alpha", ALPHA)},
        "environment": env_versions(),
    }
    with open(os.path.join(out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    draft = build_draft(cfg, res)
    with open(os.path.join(out, "report_draft.md"), "w", encoding="utf-8") as f:
        f.write(draft)
    with open(os.path.join(out, "status.json"), "w", encoding="utf-8") as f:
        json.dump({"status": "done"}, f)

    print(f"## 分析完成：{label}\n- 觀測單位 {len(units):,}；{split_note}")
    print(f"- 門檻：最低共同出現筆數 {P['min_count']}（{P['source']['min_count']}："
          f"{P['min_count_rule']}，N = {B.n:,}）；confidence "
          + (f"≥ {P['min_confidence']}" if P['min_confidence'] is not None else "不設")
          + "；lift " + (f"> {P['min_lift']}" if P['min_lift'] is not None else "不設"))
    for s, c in flog:
        print(f"- {s}：{c:,}")
    print(f"- 結果 {len(rows):,} 筆" + (f"；驗證資料重現 {n_rep} / {n_test}" if split else ""))
    print("\n" + result_table(rows[:TOP], kind, q, bool(split)))
    print(f"\n輸出：{out}/results.json、{out}/results_full.csv、{out}/report_draft.md")


# ----------------------------------------------------------------------------- 報告

def result_table(rows, kind, q, val):
    if kind == "itemset":
        cols, hd = ["result", "count", "support", "min_lift", "all_confidence"], ["組合", "共同出現筆數", "support", "最低 lift", "all-confidence"]
    elif kind == "pair":
        cols, hd = ["result", "count", "support", "lift", "cosine", "kulczynski"], ["商品組合", "共同出現筆數", "support", "lift", "cosine", "Kulczynski"]
    else:
        cols = ["result", "lhs_count", "count", "support", "confidence", "base", "lift"]
        hd = ["規則", "前件筆數", "共同出現筆數", "support", "confidence", "後件基準比例", "lift"]
    if val:
        cols, hd = cols + ["test_lift", "q_value", "replicated"], hd + ["驗證 lift", "q 值", "重現"]
    if any(r.get("note") for r in rows):
        cols, hd = cols + ["note"], hd + ["備註"]
    return md_table([dict(r, **{"rank": i}) for i, r in enumerate(rows, 1)], ["rank"] + cols, ["#"] + hd)


def env_versions():
    v = {"python": platform.python_version()}
    for m in ("pandas", "numpy", "mlxtend", "scipy"):
        try:
            v[m] = __import__(m).__version__
        except Exception:
            v[m] = "未安裝"
    return v


def todo(what):
    return f"【待填：{what}】"


def build_draft(cfg, r):
    d, q, P = r["data"], r["question"], r["params"]
    rep = cfg.get("report") or {}
    typ = {"shopping": "購物清單", "survey": "問卷類別表"}[d["type"]]
    L = [f"# {rep.get('title') or '關聯規則分析報告'}\n"]
    L.append("## 1. 分析目的與資料\n")
    L.append(f"- 背景：{rep.get('background') or todo('使用者提供的背景；未提供寫「未提供」')}")
    L.append(f"- 分析問題：**{q['label']}**")
    L.append(f"- 觀測單位：{d.get('unit') or ('一筆訂單' if d['type'] == 'shopping' else '一位受訪者')}")
    L.append(f"- 資料檔：`{d['path']}`（{d['rows']:,} 列、{len(d['columns'])} 個欄位；判定為{typ}，格式 `{d['format']}`，經使用者確認）\n")

    L.append("## 2. 前處理\n")
    L.append("每一項排除與轉換都列出原因與影響筆數。固定規則每次都會執行；標示「使用者」的項目是經使用者確認的決定。\n")
    L.append(md_table(r["preprocessing"], ["step", "action", "reason", "affected"], ["類別", "處理", "原因", "影響"]))
    L.append(f"\n前處理後共有 **{d['units']:,}** 個觀測單位、**{d['items']:,}** 種項目。\n")
    if r.get("survey_freq"):
        L.append("各分析欄位的答案分布（完整個案分析之後）：\n")
        fr = [{"欄位": c, "答案（人數）": "、".join(f"{k}（{v:,}）" for k, v in sorted(f.items(), key=lambda x: -x[1]))}
              for c, f in r["survey_freq"].items()]
        L.append(md_table(fr, ["欄位", "答案（人數）"]) + "\n")
    fn = prep_shopping if d["type"] == "shopping" else prep_survey
    src = pyinspect.getsource(fn) + ("\n\n" + pyinspect.getsource(kv_to_wide) if d["type"] == "survey" else "")
    L.append(details("顯示程式碼：本次使用的前處理程式", "python", src))
    L.append(todo("一到三句：前處理對資料的影響（例如排除比例是否偏高、可能造成的偏誤）") + "\n")

    L.append("## 3. 分析設定\n")
    L.append(f"- 規則形式（依分析問題固定）：{q['form']}")
    L.append(f"- 最低共同出現筆數：**{P['min_count']}**（{P['source']['min_count']}；預設規則為 {P['min_count_rule']}，"
             f"N = {r['split']['mine_units']:,}），相當於 support ≥ {P['min_count'] / max(1, r['split']['mine_units']):.4f}")
    L.append(f"- confidence 門檻：{P['min_confidence'] if P['min_confidence'] is not None else '不設'}（{P['source']['min_confidence']}）")
    L.append(f"- lift 門檻：{'> ' + str(P['min_lift']) if P['min_lift'] is not None else '不設，lift ≤ 1 另外標示'}（{P['source']['min_lift']}）")
    L.append(f"- 驗證安排：{r['split']['note']}\n")
    L.append("篩選過程：\n")
    L.append(md_table(r["filter_log"], ["step", "remaining"], ["步驟", "剩下"]) + "\n")
    L.append(details("顯示程式碼：本次使用的探勘程式", "python", pyinspect.getsource(default_params) + "\n\n" + pyinspect.getsource(mine)))

    L.append("## 4. 結果\n")
    L.append(f"共 {r['n_results']:,} 筆結果，下表列出前 {min(TOP, r['n_results'])} 筆（完整結果在 `results_full.csv`）。\n")
    L.append(result_table(r["top"], r["kind"], q["type"], r["validation"]["enabled"]) + "\n")
    L.append("指標說明：support = 共同出現筆數 / N；confidence = 前件出現時後件也出現的比例；"
             "後件基準比例 = 全部資料中後件出現的比例；lift = confidence / 後件基準比例（1 表示沒有關聯）。\n")
    if r["n_results"] == 0:
        L.append("> 沒有結果通過篩選。依固定規則，可以請使用者決定是否把最低共同出現筆數降到 10 筆後重跑一次。\n")
    L.append(todo("用諮詢者看得懂的話解讀前幾筆結果，回答分析問題；數字一律取自上表") + "\n")

    L.append("## 5. 可靠性檢查\n")
    v = r["validation"]
    if v["enabled"]:
        L.append(f"在探勘時沒有用到的驗證資料（{r['split']['test_units']:,} 個觀測單位）上，對每一筆結果做單尾 Fisher exact test"
                 f"（H0：前件與後件獨立；H1：正相關），{v['tested']} 個檢定以 Benjamini–Hochberg 控制 FDR = {v['alpha']}。"
                 f"「重現」= 驗證資料 lift > 1 且 q < {v['alpha']}。\n")
        L.append(f"- 重現：**{v['replicated']} / {v['tested']}**\n")
    else:
        L.append(f"- {r['split']['note']}\n")
    L.append(todo("說明哪些結果重現、哪些沒有，以及這代表什麼") + "\n")

    L.append("## 6. 結論與建議\n")
    L.append(todo("回答分析問題，提出可以採取的行動；未重現的結果不作為主要依據") + "\n")
    L.append("## 7. 限制\n")
    L.append("- 關聯規則描述的是「一起出現」，不代表因果。")
    L.append("- 結果依賴本次的門檻與前處理；改變門檻會改變規則數量與排序。")
    if d["type"] == "survey":
        L.append("- 完整個案分析排除了有缺失的受訪者；若缺失不是隨機發生，結果可能偏誤。本 skill 未使用調查權重，結果描述的是樣本。")
    L.append(todo("本次資料特有的限制") + "\n")
    L.append("## 附錄 A：指標定義\n")
    L.append("| 指標 | 公式 |\n|---|---|\n| support | s(A∪B) = 共同出現筆數 / N |\n| confidence | s(A∪B) / s(A) |\n"
             "| lift | s(A∪B) / (s(A) s(B)) |\n| cosine | s(A∪B) / √(s(A) s(B)) |\n| Kulczynski | ½ [P(B\\|A) + P(A\\|B)] |\n"
             "| all-confidence | s(I) / max 單項 support |\n")
    L.append("定義依據：Tan, P.-N., Steinbach, M., Karpatne, A., & Kumar, V. (2019). *Introduction to Data Mining* (2nd ed.). Pearson. 第 5、6 章與 10.4 節。\n")
    return "\n".join(L)


QUARTO_LANG = ('language:\n  toc-title-document: "目錄"\n  title-block-published: "日期"\n'
               '  code-tools-source-code: "原始碼"\n  copy-button-tooltip: "複製"\n'
               '  copy-button-tooltip-success: "已複製"\n  callout-note-title: "說明"\n')
TOGGLE_HTML = ('<div class="code-toggle-bar"><button type="button" onclick="(function(){var d=[].slice.call('
               "document.querySelectorAll('details.code-fold'));var o=!d.every(function(x){return x.open});"
               'd.forEach(function(x){x.open=o})})()">全部顯示 / 隱藏程式碼</button></div>\n'
               '<style>.code-toggle-bar{margin:.5rem 0 1rem}.code-toggle-bar button{padding:.35rem .9rem;'
               'border:1px solid #888;border-radius:6px;background:transparent;color:inherit;cursor:pointer;font:inherit}'
               'details.code-fold{margin:.4rem 0 .9rem}details.code-fold>summary{cursor:pointer;color:#2a6db0}'
               'main table,#quarto-document-content table{display:block;max-width:100%;overflow-x:auto}'
               'li code,p code{word-break:break-all;white-space:normal;overflow-wrap:anywhere}</style>\n')
APPENDIX_MARK = "<!-- 以下由 finalize 自動附上 -->"
FALLBACK_CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#666;--line:#ddd;--code:#f5f5f7;--accent:#2a6db0}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#e8e8ea;--muted:#aaa;--line:#3a3a3e;--code:#232327;--accent:#7cb3ef}}
body{background:var(--bg);color:var(--fg);font:16px/1.65 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;max-width:980px;margin:0 auto;padding:24px 16px}
h1{font-size:1.8rem}h2{border-bottom:1px solid var(--line);padding-bottom:.3rem;margin-top:2.2rem}
.tbl{overflow-x:auto}table{border-collapse:collapse;margin:.6rem 0;font-size:.9rem}th,td{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
pre{background:var(--code);padding:12px;border-radius:6px;overflow-x:auto}code{font-family:Consolas,Menlo,monospace;font-size:.88em}
blockquote{border-left:4px solid var(--accent);margin:.6rem 0;padding:.2rem .8rem;color:var(--muted)}
details.code-fold>summary{color:var(--accent);cursor:pointer}
"""


def find_quarto():
    cands = [os.environ.get("QUARTO_PATH"), shutil.which("quarto"),
             r"C:\Program Files\RStudio\resources\app\bin\quarto\bin\quarto.exe",
             r"C:\Program Files\Quarto\bin\quarto.exe",
             "/Applications/RStudio.app/Contents/Resources/app/quarto/bin/quarto",
             "/usr/lib/rstudio/resources/app/bin/quarto/bin/quarto", "/usr/local/bin/quarto"]
    return next((c for c in cands if c and os.path.exists(c)), None)


def md_to_html(md):
    """備援用的極簡 Markdown → HTML（只處理本報告用到的語法）。"""
    out, i, lines = [], 0, md.split("\n")

    def inline(s):
        s = H.escape(s, quote=False)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![\w*])\*([^*]+)\*(?!\w)", r"<em>\1</em>", s)
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            lang, j = ln[3:].strip(), i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append(f'<pre><code class="{lang}">' + H.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
        elif ln.startswith(("<details", "</details", "<summary", "<div", "<style", "<!--")):
            out.append(ln)
            i += 1
        elif re.match(r"^#{1,6} ", ln):
            k = len(ln.split(" ")[0])
            out.append(f"<h{k}>{inline(ln[k + 1:])}</h{k}>")
            i += 1
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = re.split(r"(?<!\\) \| ", lines[i].strip()[1:-1].strip())
                rows.append([c.strip().replace("\\|", "|") for c in cells])
                i += 1
            body = [r for r in rows[1:] if not set("".join(r)) <= set("-|: ")]
            out.append("<div class='tbl'><table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in rows[0])
                       + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r)
                                                          + "</tr>" for r in body) + "</tbody></table></div>")
        elif re.match(r"^(- |\d+\. )", ln):
            tag = "ol" if ln[0].isdigit() else "ul"
            items = []
            while i < len(lines) and re.match(r"^(- |\d+\. )", lines[i]):
                items.append(re.sub(r"^(- |\d+\. )", "", lines[i]))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
        elif ln.startswith("> "):
            out.append(f"<blockquote>{inline(ln[2:])}</blockquote>")
            i += 1
        elif ln.strip():
            out.append(f"<p>{inline(ln)}</p>")
            i += 1
        else:
            i += 1
    return "\n".join(out)


def cmd_finalize(args):
    cfg = load_config(args.config)
    out = cfg.get("out_dir") or "arules_output"
    with open(args.report, encoding="utf-8") as f:
        md = f.read()
    md = md.split(APPENDIX_MARK)[0].rstrip() + "\n"
    left = [ln.strip() for ln in md.split("\n") if "【待填" in ln]
    if left:
        print("## 報告還有未填寫的地方：")
        for x in left:
            print(f"- {x[:120]}")
        sys.exit(1)
    with open(os.path.join(out, "results.json"), encoding="utf-8") as f:
        res = json.load(f)
    with open(__file__, encoding="utf-8") as f:
        code = f.read()
    env = res.get("environment") or env_versions()
    app = [APPENDIX_MARK, "",
           "## 附錄 B：重現方式\n",
           "以下三個檔案加上原始資料，就能重現本報告的所有數字：`config.json`（本次的全部設定與使用者確認）、"
           "`scripts/run_arules.py`（完整程式，含前處理與探勘）、執行指令如下。\n",
           details("顯示程式碼：執行指令", "bash",
                   f"python scripts/run_arules.py inspect --data {cfg['data_path']} --out {out}\n"
                   f"python scripts/run_arules.py run --config {args.config}\n"
                   f"python scripts/run_arules.py finalize --config {args.config} --report {args.report}"),
           details("顯示程式碼：config.json", "json", json.dumps(cfg, ensure_ascii=False, indent=2)),
           "執行環境：" + "、".join(f"{k} {v}" for k, v in env.items()) + f"；腳本版本 {VERSION}。\n",
           "## 附錄 C：完整程式碼\n",
           details("顯示程式碼：scripts/run_arules.py（完整）", "python", code)]
    final = md + "\n" + "\n".join(app)
    md_path, qmd_path, html_path = (os.path.join(out, f"report.{e}") for e in ("md", "qmd", "html"))
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(final)
    title = (cfg.get("report") or {}).get("title") or "關聯規則分析報告"
    body = re.sub(r"^# .*\n", "", final, count=1)
    qmd = (f'---\ntitle: "{title}"\ndate: "{datetime.now().strftime("%Y-%m-%d")}"\nlang: zh-TW\n' + QUARTO_LANG +
           "format:\n  html:\n    toc: true\n    toc-depth: 2\n    embed-resources: true\n---\n\n" + TOGGLE_HTML + "\n" + body)
    with open(qmd_path, "w", encoding="utf-8") as f:
        f.write(qmd)
    how = ""
    quarto = None if args.no_quarto else find_quarto()
    if quarto:
        r = subprocess.run([quarto, "render", "report.qmd", "--to", "html"], cwd=out, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0 and os.path.exists(html_path):
            how = "Quarto"
        else:
            print("Quarto 轉檔失敗，改用內建轉檔：\n" + (r.stderr or "")[-600:])
    if not how:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(f'<!doctype html><html lang="zh-TW"><head><meta charset="utf-8">'
                    f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{H.escape(title)}</title>'
                    f'<style>{FALLBACK_CSS}</style></head><body><h1>{H.escape(title)}</h1>'
                    f'{TOGGLE_HTML}{md_to_html(body)}</body></html>')
        how = "內建轉檔（找不到 Quarto 或轉檔失敗）"
    print(f"## 完成\n- report.md：{md_path}\n- report.qmd：{qmd_path}\n- report.html：{html_path}（{how}）")


# ----------------------------------------------------------------------------- CLI

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("inspect", help="讀取並檢查資料，提出類型判斷與前處理方案")
    a.add_argument("--data", required=True)
    a.add_argument("--out", default="arules_output")
    a.add_argument("--encoding")
    a.add_argument("--type", choices=["shopping", "survey"], help="使用者已說明的資料類型（沿用，不再詢問）")
    a.add_argument("--unit", help="使用者已說明的觀測單位，例如「一張訂單」")
    a.set_defaults(func=cmd_inspect)
    b = sub.add_parser("run", help="依 config 前處理、探勘、驗證，產生報告草稿")
    b.add_argument("--config", required=True)
    b.set_defaults(func=cmd_run)
    c = sub.add_parser("finalize", help="附上程式碼與設定，輸出 report.md / .qmd / .html")
    c.add_argument("--config", required=True)
    c.add_argument("--report", required=True)
    c.add_argument("--no-quarto", action="store_true")
    c.set_defaults(func=cmd_finalize)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
