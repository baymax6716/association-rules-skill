"""產生問卷範例資料：data/sample_survey.csv、data/sample_codebook.csv、data/region_map.csv。

一列一位受訪者（寬格式），刻意放入的結構，供示範與測試：
- 真實關聯：18–29 歲且住南部 → 支持政策 A；關注交通 → 反對政策 B
- 衍生欄位：區域由縣市決定（函數關係，會產生「縣市=台南市 → 區域=南部」這種定義性規則）
- 高頻值：使用網路=有（約 88%）
- 跳題：工作滿意度只有就業者回答，其他人空白
- 特殊代碼：政策態度 98 = 不知道、99 = 拒答；另有少數漏答（空白）
- 複選題：關注議題以分號分隔
- 調查權重欄位、自由文字意見欄位

用法：python scripts/make_sample_survey.py [輸出資料夾]
"""
import os
import sys
import random

SEED = 20261030
N = 1200

COUNTIES = {"台北市": "北部", "新北市": "北部", "桃園市": "北部", "台中市": "中部", "彰化縣": "中部",
            "台南市": "南部", "高雄市": "南部", "屏東縣": "南部", "花蓮縣": "東部", "台東縣": "東部"}
COUNTY_W = [11, 14, 9, 11, 5, 12, 15, 6, 3, 2]
ISSUES = ["教育", "交通", "環境", "經濟", "治安"]
OPINIONS = ["", "", "", "希望多建公園", "交通很亂", "物價太高", "沒意見", "希望改善長照"]


def respondent(rng, i):
    age = rng.randint(18, 80)
    county = rng.choices(list(COUNTIES), COUNTY_W)[0]
    region = COUNTIES[county]
    sex = rng.choice([1, 2])
    employed = 1 if (age < 65 and rng.random() < 0.72) or (age >= 65 and rng.random() < 0.12) else 2
    sat = rng.choices([1, 2, 3, 4, 5], [6, 14, 30, 35, 15])[0] if employed == 1 else ""
    internet = 1 if rng.random() < (0.97 if age < 50 else 0.75) else 2

    issues = [x for x in ISSUES if rng.random() < 0.3]
    if not issues:
        issues = [rng.choice(ISSUES)]

    p_a = 0.88 if (age < 30 and region == "南部") else (0.42 if age < 45 else 0.28)
    pol_a = 1 if rng.random() < p_a else 2
    p_b_oppose = 0.75 if "交通" in issues else 0.35
    pol_b = 2 if rng.random() < p_b_oppose else 1
    # 特殊代碼與漏答
    r = rng.random()
    if r < 0.05:
        pol_a = 98
    elif r < 0.07:
        pol_a = 99
    r = rng.random()
    if r < 0.04:
        pol_b = 98
    elif r < 0.06:
        pol_b = 99
    elif r < 0.08:
        pol_b = ""          # 漏答

    weight = round(rng.uniform(0.6, 1.6), 3)
    return [f"R{i:04d}", age, sex, county, region, employed, sat, internet,
            pol_a, pol_b, ";".join(issues), weight, rng.choice(OPINIONS)]


CODEBOOK = [
    ("性別", 1, "男", ""), ("性別", 2, "女", ""),
    ("就業狀態", 1, "就業", ""), ("就業狀態", 2, "非就業", ""),
    ("工作滿意度", 1, "非常不滿意", ""), ("工作滿意度", 2, "不滿意", ""), ("工作滿意度", 3, "普通", ""),
    ("工作滿意度", 4, "滿意", ""), ("工作滿意度", 5, "非常滿意", ""),
    ("使用網路", 1, "有", ""), ("使用網路", 2, "沒有", ""),
    ("政策A態度", 1, "支持", ""), ("政策A態度", 2, "反對", ""),
    ("政策A態度", 98, "不知道", "dont_know"), ("政策A態度", 99, "拒答", "refused"),
    ("政策B態度", 1, "支持", ""), ("政策B態度", 2, "反對", ""),
    ("政策B態度", 98, "不知道", "dont_know"), ("政策B態度", 99, "拒答", "refused"),
]


def main(out_dir):
    rng = random.Random(SEED)
    os.makedirs(out_dir, exist_ok=True)
    header = ["受訪者編號", "年齡", "性別", "縣市", "區域", "就業狀態", "工作滿意度", "使用網路",
              "政策A態度", "政策B態度", "關注議題", "權重", "意見"]
    with open(os.path.join(out_dir, "sample_survey.csv"), "w", encoding="utf-8", newline="") as f:
        f.write(",".join(header) + "\n")
        for i in range(1, N + 1):
            f.write(",".join(str(v) for v in respondent(rng, i)) + "\n")
    with open(os.path.join(out_dir, "sample_codebook.csv"), "w", encoding="utf-8", newline="") as f:
        f.write("column,code,label,missing\n")
        for row in CODEBOOK:
            f.write(",".join(str(v) for v in row) + "\n")
    with open(os.path.join(out_dir, "region_map.csv"), "w", encoding="utf-8", newline="") as f:
        f.write("column,value,new_value\n")
        for c, r in COUNTIES.items():
            f.write(f"縣市,{c},{r}\n")
    print(f"wrote {N} respondents -> {out_dir}/sample_survey.csv（含 codebook 與 region_map）")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data")
