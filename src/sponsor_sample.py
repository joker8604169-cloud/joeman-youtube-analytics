# -*- coding: utf-8 -*-
"""
業配判定準確度驗證：產生分層抽樣清單供人工核對

分三組，各測不同面向：
  A 組（判定為業配）    → 測「精確率」：判成業配的有多少是真的
  B 組（分數 2 分臨界）  → 測「門檻該不該降到 2 分」
  C 組（分數 0~1 未判定）→ 測「召回率」：漏抓了多少

集中抽近三年（2023 起）樣本，因早期影片業配文化尚未成形，
核對舊片對本專題的分析結論幫助有限。

執行方式：
  python -m src.sponsor_sample
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

RANDOM_SEED = 42        # 固定亂數種子，重跑會得到同一份清單
DESC_PREVIEW_CHARS = 400
SAMPLE_SIZES = {"A_判定業配": 15, "B_臨界2分": 10, "C_未判定": 10}


def load_descriptions():
    """從原始 JSON 取說明欄並剔除罐頭文字"""
    with open(config.RAW_DIR / "videos.json", encoding="utf-8") as f:
        videos = json.load(f)
    return {
        v["id"]: strip_boilerplate(v["snippet"].get("description", ""))
        for v in videos
    }


def main():
    df = pd.read_csv(config.PROCESSED_DIR / "videos.csv", encoding="utf-8-sig")
    recent = df[df["published_year"] >= 2023].copy()
    print(f"近三年影片母體：{len(recent)} 支")

    pools = {
        "A_判定業配": recent[recent["is_sponsored"] == True],
        "B_臨界2分": recent[
            (recent["is_sponsored"] == False) & (recent["sponsor_score"] == 2)
        ],
        # C 組聚焦最可疑的漏抓區：商業性質強的類型
        "C_未判定": recent[
            (recent["is_sponsored"] == False)
            & (recent["sponsor_score"] <= 1)
            & (recent["category"].isin(["3C開箱評測", "挑戰/企劃", "旅遊/航空", "汽車"]))
        ],
    }

    frames = []
    for group, pool in pools.items():
        n = min(SAMPLE_SIZES[group], len(pool))
        print(f"  {group}：母體 {len(pool)} 支，抽出 {n} 支")
        picked = pool.sample(n, random_state=RANDOM_SEED).copy()
        picked.insert(0, "分組", group)
        frames.append(picked)

    sample = pd.concat(frames, ignore_index=True)
    descs = load_descriptions()
    sample["說明欄摘要"] = sample["video_id"].map(
        lambda v: descs.get(v, "")[:DESC_PREVIEW_CHARS].replace("\n", " ⏎ ")
    )
    sample["目前判定"] = sample["is_sponsored"].map({True: "業配", False: "非業配"})
    sample.insert(0, "檢核編號", range(1, len(sample) + 1))

    # 供人工填寫的兩欄，刻意留空
    sample["實際是否業配_請填寫"] = ""
    sample["備註_請填寫"] = ""

    out_cols = [
        "檢核編號", "分組", "title", "published_date", "category", "video_format",
        "目前判定", "sponsor_score", "sponsor_hits", "說明欄摘要", "url",
        "實際是否業配_請填寫", "備註_請填寫", "video_id",
    ]
    path = config.PROCESSED_DIR / "sponsor_review_sample.csv"
    sample[out_cols].to_csv(path, index=False, encoding="utf-8-sig")

    print(f"\n已輸出 {path}")
    print(f"共 {len(sample)} 支待核對\n")
    print("填寫方式：在「實際是否業配_請填寫」欄填入 是 / 否 / 不確定")
    print("判斷依據建議依序檢視：")
    print("  1. 先看「說明欄摘要」欄，多數情況可直接判斷")
    print("  2. 摘要看不出來時，點 url 開影片，看說明欄完整內容")
    print("  3. 仍不確定時，看影片開頭 30 秒是否有口頭提及合作")
    print("  4. 影片下方若出現 YouTube「含付費宣傳」標籤，直接填「是」")


if __name__ == "__main__":
    main()
