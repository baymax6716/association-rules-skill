"""關聯規則分析主程式（association-rules-skill）。

三個模式，全部由 config.json 驅動；同一份資料、同一份 config 與相同套件版本，會得到相同的結果。

  inspect   讀取資料：筆數、欄位、少量範例；判斷資料類型（購物清單／問卷類別表／已完成的項目集合）
            並列出依據；檢查缺失、欄位值、可能的 ID／權重、不支援的數值欄位；提出前處理方案，
            輸出 inspect.json 與 config_template.json。
  run       依 config 前處理。需要使用者確認的事項（含一筆觀測的定義、完整案例分析、分析目標、
            分析參數）還沒確認時，列出事項與示範設定後停止（結束代碼 2）；資料不符合支援格式時，
            說明需要的調整後停止（結束代碼 1）。通過後依分析目標（自由探索／指定前件／指定後件）
            產生規則並依序篩選。輸出 results.json、results_full.csv、
            settings_log.json 與 report_draft.md。
  finalize  確認報告沒有未填寫處，附上 config、前處理紀錄與完整程式碼，
            輸出 report.md、report.qmd、report.html。

用法
  python scripts/run_arules.py inspect --data <資料.csv> [--out arules_output] [--type shopping|survey]
  python scripts/run_arules.py run --config config.json
  python scripts/run_arules.py finalize --config config.json --report arules_output/report.md

定義依據：Tan, Steinbach, Karpatne & Kumar (2019), Introduction to Data Mining (2nd ed.)
第 5、6 章；流程與規則見 docs/skill-guide.html 第 8 章與 references/decision-tree.md。
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

VERSION = "3.0"

# ----------------------------------------------------------------------------- 固定常數（見 decision-tree.md）
ID_UNIQUE = 0.95          # 不同值比例 ≥ 此值 → 可能是 ID
NUMERIC_SHARE = 0.8       # 非缺失值中可解析為數字的比例 ≥ 此值 → 數字代碼或數值欄位
CODE_MAX_LEVELS = 10      # 數字欄位不同值 ≤ 此值 → 可能是選項代碼；否則視為數值
HIGH_FREQ = 0.8           # 某答案占比 ≥ 此值 → 提醒（當後件時 lift 多半接近 1）
SEPARATORS = [",", ";", "|", "、"]
TOP = 20
FD_MIN = 5                # 判斷欄位間函數關係時，每個值至少要出現的次數
MISSING_LIKE = {"na", "n/a", "nan", "null", "none", "-", "--", ".", "?", "missing", "refused", "dk",
                "don't know", "dont know", "拒答", "不知道", "不適用", "無意見", "未填", "缺"}
HINT_ID = ({"id", "no", "編號"}, ["編號", "序號", "代號", "受訪者"])
HINT_WEIGHT = ({"weight", "wt", "wgt", "weights"}, ["權重", "加權"])
HINT_TIME = ({"time", "date", "datetime", "timestamp", "invoicedate"}, ["時間", "日期"])
HINT_QTY = ({"qty", "quantity"}, ["數量"])

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
        return "numeric", f"內容是數字、有 {p['unique']} 種值：連續或計數變數，第一版不自動分箱"
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
                if it == t or prof[it]["numeric_share"] >= 0.5 or prof[it]["unique"] < 2                         or prof[it]["unique_ratio"] >= ID_UNIQUE:
                    continue
                grp = df.groupby(df[t].str.strip())[it]
                per = float(grp.nunique().mean())
                rows_per = float(grp.size().mean())
                # 長表的特徵：同一個 ID 底下的各列，項目幾乎都不同（一列一個項目）
                if per >= 1.5 and per / rows_per >= 0.8:
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
                basis.append(f"`{time_c}` 是日期時間（{prof[time_c]['time_share']:.0%} 可解析）：不當成項目，也不用於分析")
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
           "question": {"goal": None, "items": [], "label": None},
           "params": {"algorithm": None, "min_support": None, "min_confidence": None, "min_lift": None, "max_lhs": None,
                      "min_lhs_count": None, "confirmed": False, "reason": None},
           "report": {"title": "關聯規則分析報告", "background": None, "goal": None},
           "confirmations": []}
    t = det["type"]
    if t == "shopping" or (t == "itemset" and det["format"] == "wide01"):
        cfg["data_type"] = "shopping" if t == "shopping" else None
        cfg["shopping"] = {"format": det["format"], "tid_col": det.get("tid_col"), "item_col": det.get("item_col"),
                           "item_cols": det.get("item_cols"), "sep": det.get("sep"),
                           "qty_col": det.get("qty_col"),
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
                         "dictionary_path": None, "missing_values": [], "missing_codes": {}, "codebook": {},
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
                pending.append(f"`{c}`：{why[c]}。請使用者提供資料字典，或確認這些數字本身就是答案（例如人數）")
            elif r == "numeric":
                pending.append(f"`{c}`：{why[c]}。第一版不自動分箱：請改用已分組的欄位，或不選這一欄")
            if r == "category" and prof[c]["top_share"] >= HIGH_FREQ:
                notes.append(f"`{c}` 的「{prof[c]['top'][0][0]}」占 {prof[c]['top_share']:.0%}：當後件時 lift 多半接近 1")
        miss_cols = [c for c in cfg["survey"]["columns"] if prof[c]["missing"]]
        if cfg["survey"]["columns"]:
            sub = df[cfg["survey"]["columns"]].apply(lambda s: s.str.strip() == "")
            exc = int(sub.any(axis=1).sum())
            notes.append(f"若分析全部 {len(cfg['survey']['columns'])} 個類別欄位，完整案例分析會排除 {exc:,} 列、"
                         f"剩 {n - exc:,} 列（缺失欄位：{', '.join(miss_cols) or '無'}）；實際數字依使用者選的欄位而定")
        plan = ["保留原類別，每個答案寫成「欄位=答案」項目（原欄位名稱與答案都不改）",
                "使用者選擇要分析的欄位；ID、權重、開放文字不當成項目",
                "在選定欄位上做完整案例分析：任一選定欄位缺失的列排除；執行前告知排除與剩下的筆數並確認",
                "數字代碼依資料字典解讀；未分組的數值欄位第一版不支援（不自動分箱）",
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
        plan = ["確認交易 ID 的意義（一張發票、一位顧客或一位顧客的一天）",
                "去除商品名稱前後空白（固定規則，記錄影響列數）",
                "同一張訂單中重複出現的商品記為一次（固定規則，記錄影響列數）",
                "疑似同義的商品名稱、缺失的商品值、空的訂單、數量 ≤ 0：逐項請使用者確認",
                "保留只有一種商品的交易與內容相同的交易；數量、金額不當成項目；不產生「未購買」項目"]
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
    for k in ("tid_col", "item_col", "qty_col"):
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

    # (2) 交易編號缺失（需確認）：無法判斷屬於哪一筆交易，不猜測歸屬
    d["tid"] = d["tid"].str.strip()
    no_tid = d["tid"] == ""
    v = ask(dec, "missing_tid", int(no_tid.sum()),
            f"有 {int(no_tid.sum()):,} 列沒有交易編號，無法判斷屬於哪一筆交易（併到相鄰交易或自成一筆都是猜測）。"
            "建議排除這些列；若不同意，請先補上交易編號", ["exclude"], pending)
    if v == "exclude":
        log_add(log, "使用者確認", "排除沒有交易編號的列", "無法判斷屬於哪一筆交易，不猜測歸屬", f"{int(no_tid.sum()):,} 列")
    elif not no_tid.sum():
        log_add(log, "檢查", "交易編號空白", "未發現", "0 列")
    d = d[~no_tid].copy() if v == "exclude" or preview else d

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
            "關聯規則只看一張訂單「有沒有」這項商品；不同訂單即使內容相同也全部保留", f"{dup:,} 列")

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
    log_add(log, "固定規則", "只有一種商品的有效交易保留", "它仍是一筆交易，計入分母",
            f"{sum(len(u) == 1 for u in units):,} 筆交易")
    same = int(pd.Series([tuple(sorted(u)) for u in units]).duplicated(keep=False).sum()) if units else 0
    log_add(log, "固定規則", "不同交易即使內容相同也全部保留", "它們是不同的觀測", f"{same:,} 筆交易")
    log_add(log, "固定規則", "數量、金額不當成項目；沒買的商品不產生「未購買」項目",
            "項目是非對稱二元變數，只看有沒有出現", "—")

    # (9) 時間（只用來決定驗證資料的切法）
    return dict(units=units, ids=ids, times=None, log=log, pending=pending, problems=problems)


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


def load_dictionary(path):
    """資料字典 CSV：欄位, 原始值, 標籤, 類別（答案／缺失／ID）。回傳 codebook 與缺失代碼。"""
    d, _ = read_table(path)
    need = {"欄位", "原始值", "標籤", "類別"}
    if not need <= set(d.columns):
        sys.exit(f"資料字典需要欄位 {sorted(need)}，目前是 {list(d.columns)}")
    codebook, missing = {}, {}
    for r in d.itertuples(index=False):
        col, raw, lab, kind = (str(getattr(r, k)).strip() for k in ("欄位", "原始值", "標籤", "類別"))
        if kind == "缺失":
            missing.setdefault(col, []).append(raw)
        elif kind == "答案":
            codebook.setdefault(col, {})[raw] = lab
    return codebook, missing


def prep_survey(df, sv, preview=False):
    """類別問卷編碼：每位受訪者一個 basket，項目為「欄位=類別」；在選定欄位上做完整案例分析。"""
    log, pending, problems = [], [], []
    if sv.get("format") == "long_kv":
        df = kv_to_wide(df, sv, problems, log)
        if df is None:
            return dict(units=[], ids=[], times=None, log=log, pending=pending, problems=problems)
        sv = dict(sv, id_col="id")
    n0 = len(df)
    cols = sv.get("columns") or []
    id_col = sv.get("id_col")
    excl = {e["name"]: e.get("reason", "") for e in sv.get("exclude_cols") or [] if e["name"] != id_col}
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
        log_add(log, "欄位角色", f"`{id_col}` 是 ID，不納入規則", "每人不同，沒有分析意義", "—")
        dup_id = int(df[id_col].duplicated().sum())
        if dup_id:
            log_add(log, "提醒", f"`{id_col}` 有重複值，全部保留", "不因內容相同而刪除觀測", f"{dup_id:,} 列")
    for c, why in excl.items():
        log_add(log, "欄位角色", f"`{c}` 不納入規則", why or "使用者決定", "—")
    others = [c for c in df.columns if c not in cols and c not in excl and c != id_col]
    if others:
        log_add(log, "使用者選擇", "未選入分析的欄位：" + "、".join(f"`{c}`" for c in others),
                "完整案例分析只看選定欄位，這些欄位的缺失不影響本次分析", "—")

    codebook = {k: {str(a): str(b) for a, b in v.items()} for k, v in (sv.get("codebook") or {}).items()}
    miss_codes = {k: list(v) for k, v in (sv.get("missing_codes") or {}).items()}
    if sv.get("dictionary_path"):
        cb, mc = load_dictionary(sv["dictionary_path"])
        for k, v in cb.items():
            codebook.setdefault(k, {}).update(v)
        for k, v in mc.items():
            miss_codes.setdefault(k, []).extend(v)
        log_add(log, "使用者提供", f"資料字典 `{sv['dictionary_path']}`", "代碼的意義與缺失代碼依資料字典", "—")
    miss_global = set(sv.get("missing_values") or [])
    as_is = set(sv.get("categorical_as_is") or [])
    data = pd.DataFrame(index=df.index)
    for c in cols:
        raw = df[c].astype(str)
        v = raw.str.strip()
        ws = int((raw != v).sum())
        if ws:
            log_add(log, "固定規則", f"`{c}`：去除答案前後空白", "避免同一答案因空白被當成不同答案", f"{ws:,} 列")
        codes = miss_global | set(miss_codes.get(c, []))
        m_blank = v == ""
        if m_blank.sum():
            log_add(log, "缺失", f"`{c}`：空白設為缺失", "未回答不是任何答案，也不是「否」", f"{int(m_blank.sum()):,} 列")
        for code in sorted(codes & set(v.unique())):
            k = int((v == code).sum())
            lab = f"（{codebook.get(c, {}).get(code)}）" if codebook.get(c, {}).get(code) else ""
            log_add(log, "依資料字典", f"`{c}`：{code}{lab} 設為缺失", "資料字典標示為未回答／不知道／不適用／拒答，不當成「否」", f"{k:,} 列")
        v = v.mask(m_blank | v.isin(codes), "")
        filled = v[v != ""]
        numeric_like = len(filled) and is_num(filled).mean() >= NUMERIC_SHARE
        if c in codebook:
            mp = codebook[c]
            unk = sorted(set(filled) - set(mp))
            if unk:
                problems.append(f"`{c}` 有資料字典沒有的值 {unk[:10]}：請補齊資料字典或說明這些值")
                continue
            changed = int((filled.map(mp) != filled).sum())
            v.loc[filled.index] = filled.map(mp)
            log_add(log, "依資料字典", f"`{c}`：原始值換成標籤", "數字型態不代表連續變數，依資料字典解讀", f"{changed:,} 列")
        elif numeric_like and c not in as_is:
            nu = filled.nunique()
            if nu <= CODE_MAX_LEVELS:
                problems.append(f"`{c}` 是數字（{nu} 種值），可能是類別代碼：請提供資料字典，或確認數字本身就是答案（categorical_as_is）")
            else:
                problems.append(f"`{c}` 是數值變數（{nu} 種值）：第一版不自動分箱，請改用已分組的欄位，或不選這一欄")
            continue
        elif c in as_is and numeric_like:
            log_add(log, "使用者確認", f"`{c}`：數字本身就是答案，照原值當成類別", "使用者說明", f"{len(filled):,} 列")
        data[c] = v
    if problems:
        return dict(units=[], ids=[], times=None, log=log, pending=pending, problems=problems)

    miss = data == ""
    per_col = {c: int(miss[c].sum()) for c in cols}
    drop = miss.any(axis=1)
    exc, rem = int(drop.sum()), int((~drop).sum())
    detail = "、".join(f"{c} {k:,}" for c, k in per_col.items() if k)
    conf = sv.get("complete_case_confirmed") or {}
    if exc and (conf.get("excluded") != exc or conf.get("remaining") != rem):
        pending.append({"key": "complete_case", "count": exc,
                        "question": f"完整案例分析：選定的 {len(cols)} 個欄位中任一個缺失的受訪者會被排除，"
                                    f"共排除 {exc:,} 人、剩下 {rem:,} 人（各欄缺失：{detail}）。"
                                    "若缺失不是隨機發生，結果會偏向有回答的人。同意的話，把 complete_case_confirmed 設為 "
                                    f'{{"excluded": {exc}, "remaining": {rem}}}；不同意則第一版無法分析',
                        "options": ["confirm"]})
    log_add(log, "完整案例分析", f"只看選定欄位，任一缺失者排除（各欄缺失：{detail or '無'}）",
            "缺失、拒答、不適用不能當成任何答案", f"排除 {exc:,} 人，剩 {rem:,} 人")
    keep = data[~drop]
    units = [frozenset(f"{c}={r[c]}" for c in cols) for _, r in keep.iterrows()]
    ids = (df.loc[keep.index, id_col].astype(str).tolist() if id_col else [f"R{i + 1}" for i in keep.index])
    ident = int(keep.duplicated().sum())
    log_add(log, "固定規則", "答案完全相同的受訪者全部保留", "不同的人是不同的觀測", f"{ident:,} 人與其他人相同")
    log_add(log, "固定規則", "每人一個 basket，項目為「欄位=類別」；「否」也是項目；序位變數當類別",
            "每個「欄位=類別」組合成為一個項目；不使用順序或距離資訊", f"{rem:,} 人")
    freq = {c: keep[c].value_counts().to_dict() for c in cols}
    return dict(units=units, ids=ids, times=None, log=log, pending=pending, problems=problems, freq=freq,
                columns=cols, n_before=n0)


# ----------------------------------------------------------------------------- 指標與探勘

def measures(n, cA, cB, cAB):
    """規則 A→B 的 support、confidence、後件比例與 lift。"""
    conf = cAB / cA if cA else float("nan")
    base = cB / n
    return dict(lhs_count=int(cA), rhs_count=int(cB), count=int(cAB), support=cAB / n,
                confidence=conf, base=base, lift=conf / base if base else float("nan"))


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
        self._cache = {}

    def count(self, s):
        s = frozenset(s)
        if s not in self._cache:
            if any(i not in self.idx for i in s):
                self._cache[s] = 0
            elif not s:
                self._cache[s] = self.n
            else:
                self._cache[s] = int(self.X[:, [self.idx[i] for i in s]].all(axis=1).sum())
        return self._cache[s]


ALGORITHMS = {"apriori": "Apriori", "fpgrowth": "FP-growth"}


def frequent(B, min_count, max_len, algorithm="fpgrowth"):
    """頻繁項目集：使用者選擇 Apriori（逐層產生候選並剪枝）或 FP-growth（FP-tree 分治），兩者結果相同，只差速度。"""
    from mlxtend.frequent_patterns import apriori, fpgrowth
    if B.n == 0 or not B.items:
        return {}
    df = pd.DataFrame(B.X, columns=B.items)
    algo = {"apriori": apriori, "fpgrowth": fpgrowth}[algorithm]
    fi = algo(df, min_support=min_count / B.n, use_colnames=True, max_len=max_len)
    sup = {frozenset(s): int(round(v * B.n)) for s, v in zip(fi["itemsets"], fi["support"])}
    return {k: c for k, c in sup.items() if c >= min_count}


def attr(item):
    return item.split("=", 1)[0] if "=" in item else None


def functional_deps(units):
    """欄位 X → 欄位 Y 的函數關係：每個 X 值只對應一個 Y 值（例如 縣市 → 區域），這類規則定義上必然成立。"""
    rows = [{attr(i): i for i in u} for u in units]
    cols = sorted({k for r in rows for k in r if k})
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


def min_count_of(minsup, n):
    return max(1, math.ceil(minsup * n - 1e-9))


def propose_params(n):
    """示範設定（起點，不是通用標準）：最低支持度 1%，但至少 2 筆；confidence 0.3；lift > 1；前件 ≤ 2。"""
    mc = max(2, math.ceil(0.01 * n))
    s = 0.01 if mc == math.ceil(0.01 * n) else math.ceil(mc / n * 10000) / 10000
    return {"algorithm": "fpgrowth", "min_support": s, "min_confidence": 0.3, "min_lift": 1.0, "max_lhs": 2,
            "min_lhs_count": min_count_of(s, n)}


def rule_text(A, B):
    return f"{fmt_set(A)} → {fmt_set(B)}"


def mine(goal, B, units, P, target, survey):
    """依分析目標產生規則，依序篩選：最低筆數 → 最低前件筆數 → confidence → lift → 冗餘。回傳 (rows, steps)。"""
    n = B.n
    mc = min_count_of(P["min_support"], n)
    max_lhs = int(P.get("max_lhs") or 2)
    steps, rows = [], []
    if goal == "lhs":
        A = frozenset(target)
        cA = B.count(A)
        lhs_attrs = {attr(i) for i in A}
        if cA:
            sel = B.X[:, [B.idx[i] for i in A]].all(axis=1)
            co = B.X[sel].sum(axis=0)
            for j, it in enumerate(B.items):
                if it in A or co[j] == 0 or (survey and attr(it) in lhs_attrs):
                    continue
                rows.append(dict(result=rule_text(A, [it]), lhs=sorted(A), rhs=[it],
                                 **measures(n, cA, int(B.single[j]), int(co[j]))))
        steps.append((f"候選規則（前件 = {fmt_set(A)}、後件 1 項、至少 1 筆共同出現）", len(rows)))
        rows = [r for r in rows if r["count"] >= mc]
        steps.append((f"support ≥ {P['min_support']}（共同筆數 ≥ {mc}）", len(rows)))
    else:
        sup = frequent(B, mc, max_lhs + 1, P.get("algorithm") or "fpgrowth")
        tgt = frozenset(target or [])
        for I, cI in sup.items():
            if len(I) < 2:
                continue
            for b in sorted(I):
                A = I - {b}
                if len(A) > max_lhs or (goal == "rhs" and b not in tgt):
                    continue
                rows.append(dict(result=rule_text(A, [b]), lhs=sorted(A), rhs=[b],
                                 **measures(n, sup[A], sup[frozenset([b])], cI)))
        form = f"後件 = {fmt_set(tgt)}、前件 ≤ {max_lhs} 項" if goal == "rhs" else f"前件 ≤ {max_lhs} 項、後件 1 項"
        steps.append((f"support ≥ {P['min_support']}（共同筆數 ≥ {mc}）的候選規則（{form}）", len(rows)))
    rows = [r for r in rows if r["lhs_count"] >= P["min_lhs_count"]]
    steps.append((f"前件筆數 ≥ {P['min_lhs_count']}", len(rows)))
    if P.get("min_confidence") is not None:
        rows = [r for r in rows if r["confidence"] >= P["min_confidence"] - 1e-12]
        steps.append((f"confidence ≥ {P['min_confidence']}", len(rows)))
    if P.get("min_lift") is not None:
        rows = [r for r in rows if r["lift"] > P["min_lift"]]
        steps.append((f"lift > {P['min_lift']}", len(rows)))
    # 冗餘：前件拿掉任一項後（同一後件）confidence 已相同或更高，多出的項目沒有提供資訊
    keep = []
    for r in rows:
        A, b = frozenset(r["lhs"]), r["rhs"][0]
        red = len(A) >= 2 and goal != "lhs" and any(
            B.count((A - {a}) | {b}) / B.count(A - {a}) >= r["confidence"] - 1e-12 for a in A)
        if not red:
            keep.append(r)
    rows = keep
    steps.append(("移除冗餘規則（較短前件的 confidence 已相同或更高）", len(rows)))
    deps = functional_deps(units) if survey else set()
    for r in rows:
        r["note"] = ("欄位間有函數關係：定義上必然成立，不是新發現"
                     if any((attr(x), attr(r["rhs"][0])) in deps for x in r["lhs"]) else "")
    rows.sort(key=lambda r: (-r["lift"], -r["count"], r["result"]))
    return rows, steps


# ----------------------------------------------------------------------------- run

def load_config(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def prepare(cfg):
    df, enc = read_table(cfg["data_path"], cfg.get("encoding"))
    t = cfg.get("data_type")
    if t == "shopping":
        prep = prep_shopping(df, cfg.get("shopping") or {})
        if not cfg.get("unit"):
            prep["pending"].insert(0, {"key": "unit", "count": 1, "options": ["文字說明"],
                                       "question": "交易 ID 代表什麼（一張發票、一位顧客，或一位顧客的一天）？確認後寫入 unit"})
    elif t == "survey":
        prep = prep_survey(df, cfg.get("survey") or {})
    else:
        sys.exit("config 的 data_type 必須是 shopping 或 survey（由使用者確認）")
    prep.update(rows=len(df), columns=list(df.columns), encoding=enc)
    return prep


GOAL_LABEL = {"free": "自由探索", "lhs": "指定前件", "rhs": "指定後件"}


def check_question(cfg, B):
    q = cfg.get("question") or {}
    goal = q.get("goal")
    if goal not in GOAL_LABEL:
        return None, None, ["請使用者選擇分析目標：question.goal = free（自由探索）、lhs（指定前件）或 rhs（指定後件）"]
    items = q.get("items") or []
    items = [items] if isinstance(items, str) else list(items)
    problems = []
    if goal == "lhs" and not items:
        problems.append("指定前件需要至少一個項目（question.items）")
    if goal == "rhs" and len(items) != 1:
        problems.append("指定後件需要剛好一個項目（question.items）")
    for x in items:
        if x not in B.idx:
            close = difflib.get_close_matches(x, B.items, n=5, cutoff=0.5) + \
                [i for i in B.items if x.lower() in i.lower()][:5]
            problems.append(f"資料中沒有「{x}」這個項目" + (f"；相近的有：{sorted(set(close))[:8]}" if close else "")
                            + "。請使用者確認正確名稱（不自行替換）")
    return goal, items, problems


def stop(code, title, items, out_dir, kind, extra=""):
    os.makedirs(out_dir, exist_ok=True)
    print(f"## {title}")
    for x in items:
        print(f"- {x['question'] if isinstance(x, dict) else x}")
    if extra:
        print("\n" + extra)
    with open(os.path.join(out_dir, "status.json"), "w", encoding="utf-8") as f:
        json.dump({"status": kind, "items": items}, f, ensure_ascii=False, indent=1)
    sys.exit(code)


def param_reference(goal, B, units, P, target, survey):
    """只改一個參數（最低支持度乘上 0.5、1、2、4 倍；或 confidence 門檻），列出保留規則數，協助使用者決定（不自動選擇）。"""
    rows = []
    for f in (0.5, 1, 2, 4):
        s = round(P["min_support"] * f, 6)
        q = dict(P, min_support=s)
        r, _ = mine(goal, B, units, q, target, survey)
        rows.append({"最低支持度": s, "對應最低筆數": min_count_of(s, B.n), "保留規則數": len(r)})
    rows2 = []
    for c in (0.1, 0.2, 0.3, 0.5):
        r, _ = mine(goal, B, units, dict(P, min_confidence=c), target, survey)
        rows2.append({"confidence 門檻": c, "保留規則數": len(r)})
    return rows, rows2


def settings_text(P, n):
    return (f"演算法 {ALGORITHMS.get(P.get('algorithm') or 'fpgrowth')}、最低支持度 {P['min_support']}（共同筆數 ≥ {min_count_of(P['min_support'], n)}）、"
            f"confidence ≥ {P.get('min_confidence')}、lift > {P.get('min_lift')}、"
            f"前件 ≤ {P.get('max_lhs', 2)} 項、後件 1 項、前件筆數 ≥ {P['min_lhs_count']}")


def cmd_run(args):
    cfg = load_config(args.config)
    out = cfg.get("out_dir") or "arules_output"
    prep = prepare(cfg)
    if prep["problems"]:
        stop(1, "資料不符合支援的格式（需要使用者調整，不自行猜測）", prep["problems"], out, "unsupported")
    if prep["pending"]:
        stop(2, "以下事項需要使用者確認後才能執行（一次問一項）", prep["pending"], out, "pending")
    units = prep["units"]
    if not units:
        stop(1, "前處理後沒有任何觀測", ["請檢查資料或前處理設定"], out, "unsupported")
    survey = cfg["data_type"] == "survey"
    goal, target, problems = check_question(cfg, Binary(units))
    if problems:
        stop(2 if goal is None else 1, "分析目標需要確認", problems, out, "question")

    mine_units = units
    B = Binary(mine_units)

    P = dict(cfg.get("params") or {})
    if not P.get("confirmed"):
        demo = propose_params(B.n)
        demo.update({k: v for k, v in P.items() if v is not None and k in demo})
        ref, ref2 = param_reference(goal, B, mine_units, demo, target, survey)
        extra = ("### 示範設定（起點，不是通用標準）\n" + settings_text(demo, B.n) +
                 f"\n- 分母 N = {B.n:,}；理由：最低支持度 1%（至少 2 筆）避免只出現一兩次的巧合組合；"
                 f"confidence {demo['min_confidence']} 只排除較弱的規則，判斷強弱仍要對照後件比例與 lift；前件 ≤ 2 項較容易解讀；"
                 "前件筆數不少於最低共同筆數，避免「1 筆中 1 筆」的 confidence。\n"
                 "- 演算法：Apriori（逐層產生候選、用 Apriori 原理剪枝）與 FP-growth（先壓成 FP-tree 再分治）"
                 "找到的頻繁項目集完全相同，只差速度；FP-growth 通常較快，資料小時差異不大。請使用者選擇 apriori 或 fpgrowth。"
                 + ("指定前件時程式直接計數包含前件的交易，演算法選項不影響結果。" if goal == "lhs" else "") +
                 "\n\n### 只改最低支持度時的規則數\n" +
                 md_table(ref, list(ref[0])) + "\n\n### 只改 confidence 門檻時的規則數\n" +
                 md_table(ref2, list(ref2[0])) +
                 "\n\n使用者確認或修改後，把值寫入 params，並設定 \"confirmed\": true、\"reason\": 使用者的理由。")
        stop(2, "需要使用者確認分析參數", [{"key": "params", "count": 1, "options": ["confirm"],
                                         "question": "請確認或修改示範設定"}], out, "params", extra)
    for k in ("min_support", "min_lhs_count"):
        if P.get(k) is None:
            stop(1, "參數不完整", [f"params.{k} 必須填寫"], out, "params")
    P.setdefault("max_lhs", 2)
    P["algorithm"] = P.get("algorithm") or "fpgrowth"
    if P["algorithm"] not in ALGORITHMS:
        stop(1, "參數不正確", [f"params.algorithm 只能是 {list(ALGORITHMS)}"], out, "params")

    rows, steps = mine(goal, B, mine_units, P, target, survey)

    os.makedirs(out, exist_ok=True)
    # 設定紀錄：每次執行的設定與結果都保留
    log_path = os.path.join(out, "settings_log.json")
    hist = json.load(open(log_path, encoding="utf-8")) if os.path.exists(log_path) else []
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "goal": GOAL_LABEL[goal], "items": target,
             "settings": settings_text(P, B.n), "n_rules": len(rows), "reason": P.get("reason") or "（未填）"}
    if not hist or {k: hist[-1].get(k) for k in ("goal", "items", "settings")} != {k: entry[k] for k in ("goal", "items", "settings")}:
        hist.append(entry)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)

    pd.DataFrame([{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v) for k, v in r.items()}
                  for r in rows]).to_csv(os.path.join(out, "results_full.csv"), index=False, encoding="utf-8-sig")
    res = {
        "version": VERSION, "generated": datetime.now().isoformat(timespec="seconds"), "config_path": args.config,
        "data": {"path": cfg["data_path"], "encoding": prep["encoding"], "rows": prep["rows"],
                 "columns": prep["columns"], "type": cfg["data_type"],
                 "format": (cfg.get("shopping") or cfg.get("survey") or {}).get("format"),
                 "units": len(units), "items": len(Binary(units).items), "unit": cfg.get("unit"),
                 "n_before": prep.get("n_before")},
        "preprocessing": prep["log"], "survey_columns": prep.get("columns"), "survey_freq": prep.get("freq"),
        "question": {"goal": goal, "goal_label": GOAL_LABEL[goal], "items": target,
                     "label": (cfg.get("question") or {}).get("label")},
        "params": P, "settings": settings_text(P, B.n), "settings_log": hist,
        "N": B.n,
        "filter_log": [{"step": s, "remaining": c} for s, c in steps],
        "n_results": len(rows), "top": rows[:TOP],
        "environment": env_versions(),
    }
    with open(os.path.join(out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    with open(os.path.join(out, "report_draft.md"), "w", encoding="utf-8") as f:
        f.write(build_draft(cfg, res))
    with open(os.path.join(out, "status.json"), "w", encoding="utf-8") as f:
        json.dump({"status": "done"}, f)

    print(f"## 分析完成：{GOAL_LABEL[goal]}" + (f"（{', '.join(target)}）" if target else ""))
    print(f"- 分母 N = {B.n:,}\n- 設定：{settings_text(P, B.n)}")
    for s, c in steps:
        print(f"- {s}：{c:,}")
    print(f"- 保留規則 {len(rows):,} 條")
    if not rows:
        print("\n> 沒有規則。請向使用者說明原因並提出放寬方案；經同意才修改參數（設定紀錄會保留每一次）。")
    print("\n" + result_table(rows[:TOP]))
    print(f"\n輸出：{out}/results.json、{out}/results_full.csv、{out}/settings_log.json、{out}/report_draft.md")


# ----------------------------------------------------------------------------- 報告

def result_table(rows):
    cols = ["result", "lhs_count", "count", "support", "confidence", "base", "lift"]
    hd = ["規則", "前件筆數", "共同筆數", "support", "confidence", "後件比例", "lift"]
    if any(r.get("note") for r in rows):
        cols, hd = cols + ["note"], hd + ["備註"]
    return md_table([dict(r, rank=i) for i, r in enumerate(rows, 1)], ["rank"] + cols, ["#"] + hd)


def env_versions():
    v = {"python": platform.python_version()}
    for m in ("pandas", "numpy", "mlxtend"):
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
    typ = {"shopping": "購物資料", "survey": "類別問卷"}[d["type"]]
    unit = d.get("unit") or ("一筆交易" if d["type"] == "shopping" else "一位受訪者")
    N = r["N"]
    L = [f"# {rep.get('title') or '關聯規則分析報告'}\n"]
    L.append("## 1. 分析目的與資料\n")
    L.append(f"- 背景：{rep.get('background') or todo('使用者提供的背景；未提供寫「未提供」')}")
    L.append(f"- 分析問題：{q.get('label') or todo('用使用者的話寫出分析問題')}")
    L.append(f"- 分析目標：**{q['goal_label']}**" + (f"（{', '.join(q['items'])}）" if q["items"] else ""))
    L.append(f"- 資料：`{d['path']}`，{d['rows']:,} 列、{len(d['columns'])} 個欄位；{typ}（格式 `{d['format']}`，經使用者確認）")
    L.append(f"- 一筆觀測：{unit}")
    L.append(f"- **support 的分母 N = {N:,}**\n")
    L.append("> 關聯規則描述的是共同出現，不代表因果。\n")

    L.append("## 2. 前處理\n")
    L.append("每一項處理都列出原因與影響筆數；「使用者」開頭的項目是經使用者確認的決定。\n")
    L.append(md_table(r["preprocessing"], ["step", "action", "reason", "affected"], ["類別", "處理或檢查", "原因", "影響"]))
    L.append(f"\n分析前後筆數：原始資料 {d['rows']:,} 列 → {d['units']:,} 個觀測（{unit}）、{d['items']:,} 種項目。\n")
    if r.get("survey_freq"):
        L.append("編碼對照（各分析欄位的類別與人數，完整案例分析之後）：\n")
        fr = [{"欄位": c, "類別（人數）": "、".join(f"{c}={k}（{v:,}）" for k, v in sorted(f.items(), key=lambda x: -x[1]))}
              for c, f in r["survey_freq"].items()]
        L.append(md_table(fr, ["欄位", "類別（人數）"]) + "\n")
    fn = [prep_shopping] if d["type"] == "shopping" else [prep_survey, kv_to_wide, load_dictionary]
    L.append(details("顯示程式碼：本次使用的前處理程式", "python", "\n\n".join(pyinspect.getsource(f) for f in fn)))
    L.append(todo("一到三句：前處理對資料的影響，例如排除比例與可能的偏誤") + "\n")

    L.append("## 3. 分析設定\n")
    L.append(f"- 本次設定：{r['settings']}（分母 N = {N:,}）")
    L.append(f"- 設定理由：{P.get('reason') or todo('使用者確認設定的理由')}")
    L.append("- 這些門檻是本次分析的選擇，不是通用標準。\n")
    L.append("設定紀錄（每一次執行的設定與保留規則數）：\n")
    L.append(md_table(r["settings_log"], ["time", "goal", "settings", "n_rules", "reason"],
                      ["時間", "目標", "設定", "保留規則數", "理由"]) + "\n")
    L.append("篩選過程：\n")
    L.append(md_table(r["filter_log"], ["step", "remaining"], ["篩選步驟", "剩下規則數"]) + "\n")
    L.append(details("顯示程式碼：本次使用的規則產生與篩選程式", "python",
                     pyinspect.getsource(mine) + "\n\n" + pyinspect.getsource(measures)))

    L.append("## 4. 結果與解讀\n")
    L.append(f"共保留 {r['n_results']:,} 條規則，下表依 lift 排序列出前 {min(TOP, r['n_results'])} 條（完整結果在 `results_full.csv`）。\n")
    L.append(result_table(r["top"]) + "\n")
    L.append("support = 共同筆數 / N；confidence = 共同筆數 / 前件筆數；後件比例 = 後件筆數 / N；"
             "lift = confidence / 後件比例（1 表示與獨立情況相同）。lift > 1 不等於統計顯著。\n")
    if r["n_results"] == 0:
        L.append("> 本次設定下沒有規則。若之後經使用者同意調整設定，設定紀錄會保留每一次的結果。\n")
    L.append(todo("解讀主要規則：每條說明分母、共同筆數、confidence 與後件比例的對照、lift；不寫因果、不寫顯著") + "\n")

    L.append("## 5. 結論與建議\n")
    L.append(todo("回答分析問題；行動建議寫成「可以考慮」，必要時建議以實驗確認") + "\n")
    L.append("## 6. 限制\n")
    L.append("- 結果是對這份資料的描述，屬探索性；沒有檢驗規則在其他資料上是否成立，lift > 1 也不代表統計顯著。")
    L.append("- 關聯不代表因果；可能有未納入分析的隱藏變數（Simpson's paradox）。")
    L.append("- 結果依賴門檻與前處理；改變設定會改變規則數量與排序。")
    if d["type"] == "survey":
        L.append("- 完整案例分析排除了選定欄位有缺失的受訪者；若缺失不是隨機發生，結果可能偏誤。")
        L.append("- 未使用調查權重，結果是未加權的樣本描述，不代表整體人口。")
    L.append(todo("本次資料特有的限制") + "\n")
    L.append("## 附錄 A：指標定義與出處\n")
    L.append("σ(X) 是包含 X 全部項目的交易數；σ(X∪Y) 是同時包含前件與後件全部項目的交易數。\n")
    L.append("| 指標 | 公式 |\n|---|---|\n| support | σ(X∪Y) / N |\n| confidence | σ(X∪Y) / σ(X) |\n"
             "| 後件比例 | σ(Y) / N |\n| lift | N σ(X∪Y) / (σ(X) σ(Y)) |\n")
    L.append("出處：Tan, P.-N., Steinbach, M., Karpatne, A., & Kumar, V. (2019). *Introduction to Data Mining* (2nd ed.). Pearson. "
             "support 與 confidence 見 5.1 節；lift（interest factor）見 5.7.1 節；Apriori 見 5.2 節；FP-growth 見 5.6 節；"
             "類別屬性轉成項目見 6.1 節。\n")
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
