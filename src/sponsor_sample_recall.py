# -*- coding: utf-8 -*-
"""
漏抓率驗證：從「判定為非業配」的影片中純隨機抽樣

與 sponsor_sample.py 的差異：
  sponsor_sample.py 是分層抽樣，刻意集中於商業性質強的類型，
  用於快速找出規則缺口，但因抽樣有偏，無法推估母體漏抓率。

  本腳本改採「純隨機抽樣」，不挑類型、不挑分數，
  因此核對結果可直接推估 705 支未判定影片中的漏抓比例，
  並讓精確率的信賴區間一併收窄（首次取得確認為非業配的樣本）。

執行方式：
  python -m src.sponsor_sample_recall
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from src.transform import strip_boilerplate  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RANDOM_SEED = 2026          # 與第一份抽樣不同，避免重複抽中同一批
SAMPLE_SIZE = 40            # 誤差約 ±15%
DESC_PREVIEW_CHARS = 400
START_YEAR = 2023

# 可用參數指定格式，例如：python -m src.sponsor_sample_recall Shorts
# Shorts 有 85.7% 說明欄空白，必須另外抽樣量測其召回率，
# 不能沿用長片的結果。


def main():
    fmt = sys.argv[1] if len(sys.argv) > 1 else None

    # 🔴 不帶參數時 suffix 為空，輸出檔名就是 sponsor_recall_sample.csv，
    # 會直接覆蓋既有的人工標記——那是花時間標出來的永久資產，覆蓋掉無法復原。
    # 而且不指定格式抽出來的是混合樣本（長片與 Shorts 的可用文字量差十倍），
    # 算出的召回率不屬於任何單一格式。一律要求指定格式。
    if fmt is None:
        raise SystemExit(
            "必須指定格式：\n"
            "  python -m src.sponsor_sample_recall 長片\n"
            "  python -m src.sponsor_sample_recall Shorts\n"
            "不帶參數會寫入 sponsor_recall_sample.csv，覆蓋既有的人工標記（永久資產）。"
        )
    suffix = f"_{fmt.lower()}"

    df = pd.read_csv(config.PROCESSED_DIR / "videos.csv", encoding="utf-8-sig")
    recent = df[df["published_year"] >= START_YEAR]
    recent = recent[recent["video_format"] == fmt]
    print(f"限定格式：{fmt}")
    pool = recent[recent["is_sponsored"] == False].copy()

    # 排除所有已核對過的影片，避免重複勞動。
    # 原本只排除 sponsor_review_sample，漏掉既有的漏抓率抽樣檔。
    for prev_name in ("sponsor_review_sample.csv",
                      "sponsor_recall_sample.csv",
                      "sponsor_recall_sample_shorts.csv",
                      f"sponsor_recall_sample{suffix}.csv"):
        prev_path = config.PROCESSED_DIR / prev_name
        if not prev_path.exists():
            continue
        for enc in ("utf-8-sig", "cp950"):
            try:
                prev = pd.read_csv(prev_path, encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        before = len(pool)
        pool = pool[~pool["video_id"].isin(prev["video_id"])]
        if before - len(pool):
            print(f"排除 {prev_name} 已核對的 {before - len(pool)} 支")

    print(f"未判定為業配的母體：{len(pool)} 支（{START_YEAR} 年起）")
    n = min(SAMPLE_SIZE, len(pool))
    sample = pool.sample(n, random_state=RANDOM_SEED).copy()
    print(f"純隨機抽出：{n} 支（未依類型或分數篩選）")

    with open(config.RAW_DIR / "videos.json", encoding="utf-8") as f:
        descs = {
            v["id"]: strip_boilerplate(v["snippet"].get("description", ""))
            for v in json.load(f)
        }
    sample["說明欄摘要"] = sample["video_id"].map(
        lambda v: descs.get(v, "")[:DESC_PREVIEW_CHARS].replace("\n", " ⏎ ")
    )
    sample["目前判定"] = "非業配"
    sample = sample.sort_values("published_date", ascending=False)
    sample.insert(0, "檢核編號", range(1, len(sample) + 1))
    sample["實際是否業配_請填寫"] = ""
    sample["備註_請填寫"] = ""

    out_cols = [
        "檢核編號", "title", "published_date", "category", "video_format",
        "目前判定", "sponsor_score", "sponsor_hits", "is_self_promo",
        "說明欄摘要", "url", "實際是否業配_請填寫", "備註_請填寫", "video_id",
    ]
    path = config.PROCESSED_DIR / f"sponsor_recall_sample{suffix}.csv"
    sample[out_cols].to_csv(path, index=False, encoding="utf-8-sig")

    print(f"\n已輸出 {path}")
    print(f"\n抽樣組成（確認為隨機、未偏向特定類型）：")
    print(sample["category"].value_counts().to_string())
    print(f"\n格式分布：")
    print(sample["video_format"].value_counts().to_string())
    print("\n填寫方式：在「實際是否業配_請填寫」欄填入 是 / 否")
    print("提醒：依專案定義，自營商品（買房課程、9好吃、團購平台、頻道會員）")
    print("      不算業配，只有外部廠商的商業委託才填「是」。")


if __name__ == "__main__":
    main()
