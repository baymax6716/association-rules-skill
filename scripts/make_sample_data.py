"""產生 data/sample_baskets.csv：模擬超市交易（長格式，一列一品項）。

刻意放入的結構，供示範與測試：
- 真實關聯：bread→butter、diapers→beer、pasta→tomato sauce、chips↔soda、
  wine→cheese（低 support 但強關聯）
- 高頻商品 milk（support 約 60%），用來製造 cross-support 規則
- 髒資料：品名大小寫與空白不一致、缺漏品名、數量 ≤ 0（退貨）、重複列

用法：python scripts/make_sample_data.py [輸出路徑]
"""
import sys
import random
from datetime import datetime, timedelta

SEED = 20261029
N_TX = 1200
START = datetime(2026, 7, 1, 8, 0)
DAYS = 56  # 8 週，方便示範依時間切分

FILLER = ["eggs", "apples", "bananas", "yogurt", "rice", "coffee", "tea",
          "toilet paper", "shampoo", "detergent", "onions", "potatoes",
          "chicken", "cereal", "orange juice", "ice cream"]


def basket(rng):
    items = set()
    if rng.random() < 0.60:
        items.add("milk")
    if rng.random() < 0.30:
        items.add("bread")
        if rng.random() < 0.55:
            items.add("butter")
    elif rng.random() < 0.06:
        items.add("butter")
    if rng.random() < 0.12:
        items.add("diapers")
        if rng.random() < 0.50:
            items.add("beer")
    elif rng.random() < 0.08:
        items.add("beer")
    if rng.random() < 0.15:
        items.add("pasta")
        if rng.random() < 0.65:
            items.add("tomato sauce")
    if rng.random() < 0.18:
        items.update(["chips", "soda"] if rng.random() < 0.6 else [rng.choice(["chips", "soda"])])
    if rng.random() < 0.04:
        items.add("wine")
        if rng.random() < 0.8:
            items.add("cheese")
    elif rng.random() < 0.03:
        items.add("cheese")
    for f in FILLER:
        if rng.random() < 0.09:
            items.add(f)
    if not items:
        items.add(rng.choice(FILLER))
    return items


def messy(name, rng):
    r = rng.random()
    if r < 0.03:
        return name.upper()
    if r < 0.06:
        return " " + name.title() + " "
    return name


def main(out):
    rng = random.Random(SEED)
    rows = []
    for t in range(1, N_TX + 1):
        tid = f"T{t:05d}"
        ts = START + timedelta(days=rng.randrange(DAYS), minutes=rng.randrange(13 * 60))
        for it in sorted(basket(rng)):
            qty = rng.choice([1, 1, 1, 2, 2, 3])
            rows.append([tid, messy(it, rng), ts.strftime("%Y-%m-%d %H:%M"), qty])
    # 髒資料
    for _ in range(15):
        r = rng.choice(rows)
        rows.append([r[0], "", r[2], 1])                 # 缺漏品名
    for _ in range(12):
        r = rng.choice(rows)
        rows.append([r[0], r[1], r[2], -r[3]])          # 退貨
    for _ in range(20):
        rows.append(list(rng.choice(rows)))              # 重複列
    rows.sort(key=lambda r: (r[2], r[0]))
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write("transaction_id,item,timestamp,quantity\n")
        for r in rows:
            f.write(f"{r[0]},{r[1]},{r[2]},{r[3]}\n")
    print(f"wrote {len(rows)} rows, {N_TX} transactions -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/sample_baskets.csv")
