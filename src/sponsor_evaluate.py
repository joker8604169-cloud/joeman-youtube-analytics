# -*- coding: utf-8 -*-
"""
業配判定準確度評估（分層推估）

資料來源為兩份人工核對清單，性質不同，必須分開使用：

  sponsor_review_sample.csv  分層抽樣，刻意集中商業性質強的類型。
                             有偏，僅能用於評估「判定為業配者」的精確率，
                             不可用於推估漏抓率。

  sponsor_recall_sample.csv  純隨機抽樣，抽自「判定為非業配」的母體。
                             可用於推估漏抓率，進而回推實際業配總數。

所有比例均附 Wilson score 95% 信賴區間，因樣本量小、
且觀察值可能落在 0% 或 100% 的極端，一般常態近似不適用。

執行方式：
  python -m src.sponsor_evaluate
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

YES = {"是", "y", "Y", "yes", "1", "有"}
NO = {"否", "n", "N", "no", "0", "沒有", "無"}
START_YEAR = 2023


def normalize(v):
    s = str(v).strip()
    if s in YES:
        return True
    if s in NO:
        return False
    return None      # 不確定、不好判定或未填


def wilson(k, n, z=1.96):
    """Wilson score 區間：小樣本與極端比例下仍適用"""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z**2 / n
    centre = p + z**2 / (2 * n)
    half = z * ((p * (1 - p) / n + z**2 / (4 * n**2)) ** 0.5)
    return ((centre - half) / d, (centre + half) / d)


def load_sample(name):
    path = config.PROCESSED_DIR / name
    if not path.exists():
        return None
    for enc in ("utf-8-sig", "cp950"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    return None


def main():
    # 可指定格式，例如：python -m src.sponsor_evaluate Shorts
    # Shorts 與長片的可用文字量差異極大，召回率必須分開量測。
    fmt = sys.argv[1] if len(sys.argv) > 1 else None
    # 每種格式對應自己的抽樣檔：長片 → _長片、Shorts → _shorts。
    # 不帶參數時讀的 sponsor_recall_sample.csv 是 2026 年初抽的**混合格式**樣本
    # （25 Shorts ＋ 14 長片），算出來的召回率不屬於任何單一格式，見下方警語。
    recall_file = (f"sponsor_recall_sample_{fmt.lower()}.csv" if fmt
                   else "sponsor_recall_sample.csv")

    videos = pd.read_csv(config.PROCESSED_DIR / "videos.csv", encoding="utf-8-sig")
    if fmt:
        videos = videos[videos["video_format"] == fmt]
        print(f"限定格式：{fmt}")
    else:
        print("⚠️  未指定格式：母體與抽樣皆為長片＋Shorts 混合，")
        print("    算出的召回率不可標示為任何單一格式的數值。")
        print("    要長片的數字請執行：python -m src.sponsor_evaluate 長片\n")
    recent = videos[videos["published_year"] >= START_YEAR]
    n_pos = int(recent["is_sponsored"].sum())
    n_neg = len(recent) - n_pos

    pred = videos.set_index("video_id")["is_sponsored"].to_dict()

    print(f"近三年（{START_YEAR} 年起）母體：{len(recent)} 支")
    print(f"  判定為業配：{n_pos} 支｜判定為非業配：{n_neg} 支\n")

    # ── 精確率：用所有已核對且「目前判定為業配」的樣本 ──────────
    frames = []
    for name in ("sponsor_review_sample.csv", recall_file):
        s = load_sample(name)
        if s is not None:
            s = s[["video_id", "實際是否業配_請填寫", "備註_請填寫"]].copy()
            s["來源"] = name
            frames.append(s)
    labels = pd.concat(frames, ignore_index=True).drop_duplicates("video_id")
    labels["實際"] = labels["實際是否業配_請填寫"].map(normalize)
    labels["預測"] = labels["video_id"].map(pred)
    labeled = labels[labels["實際"].notna() & labels["預測"].notna()]

    pos_labeled = labeled[labeled["預測"] == True]
    tp = int(pos_labeled["實際"].astype(bool).sum())
    fp = len(pos_labeled) - tp
    lo, hi = wilson(tp, len(pos_labeled))
    print("=== 精確率（判定為業配者，有多少確實是）===")
    print(f"  {tp}/{len(pos_labeled)} = {tp / len(pos_labeled) * 100:.1f}%")
    print(f"  95% 信賴區間：{lo * 100:.1f}% ~ {hi * 100:.1f}%")
    if fp:
        print(f"  誤判 {fp} 支")

    # ── 漏抓率：只能用純隨機樣本 ────────────────────────────
    rec = load_sample(recall_file).copy()
    rec["實際"] = rec["實際是否業配_請填寫"].map(normalize)
    rec["預測"] = rec["video_id"].map(pred)
    undecided = rec[rec["實際"].isna()]

    # 樣本抽自「當時」的非業配母體；規則調整後部分已被抓到。
    # 仍為非業配者構成現行非業配母體的隨機子集，據此估算漏抓率。
    still_neg = rec[rec["實際"].notna() & (rec["預測"] == False)]
    now_caught = rec[rec["實際"].notna() & (rec["預測"] == True)
                     & rec["實際"].astype(bool)]
    missed = int(still_neg["實際"].astype(bool).sum())
    n_dec = len(still_neg)
    m_lo, m_hi = wilson(missed, n_dec)

    print(f"\n=== 漏抓率（判定為非業配者，其實是業配的比例）===")
    print(f"  隨機抽驗 {len(rec)} 支，{len(undecided)} 支無法判定")
    if len(now_caught):
        print(f"  其中 {len(now_caught)} 支經規則調整後已被抓到，"
              f"移出非業配母體")
    print(f"  現仍判定為非業配者 {n_dec} 支，其中實為業配 {missed} 支")
    print(f"  漏抓率 {missed}/{n_dec} = {missed / n_dec * 100:.1f}%")
    print(f"  95% 信賴區間：{m_lo * 100:.1f}% ~ {m_hi * 100:.1f}%")

    # ── 回推實際業配總數 ────────────────────────────────────
    est = n_pos + n_neg * (missed / n_dec)
    est_lo = n_pos + n_neg * m_lo
    est_hi = n_pos + n_neg * m_hi
    print(f"\n=== 回推近三年實際業配總數 ===")
    print(f"  模型偵測：{n_pos} 支（{n_pos / len(recent) * 100:.1f}%）← 下限")
    print(f"  推估實際：{est:.0f} 支（{est / len(recent) * 100:.1f}%）")
    print(f"  95% 區間：{est_lo:.0f} ~ {est_hi:.0f} 支"
          f"（{est_lo / len(recent) * 100:.1f}% ~ {est_hi / len(recent) * 100:.1f}%）")

    print(f"\n=== 召回率（實際業配中，被抓到的比例）===")
    print(f"  {n_pos} / {est:.0f} = {n_pos / est * 100:.1f}%")
    print(f"  95% 區間：{n_pos / est_hi * 100:.1f}% ~ {n_pos / est_lo * 100:.1f}%")

    # ── 漏抓案例，供規則改進 ────────────────────────────────
    # 精確率下滑時，逐支列出誤判以便追查是哪條規則過寬
    fp_rows = pos_labeled[~pos_labeled["實際"].astype(bool)]
    if len(fp_rows):
        info = videos.set_index("video_id")
        print(f"\n=== 誤判為業配的 {len(fp_rows)} 支 ===")
        for vid in fp_rows["video_id"]:
            r = info.loc[vid]
            print(f"  {str(r['title'])[:45]}")
            print(f"      分數 {r['sponsor_score']}｜命中：{r['sponsor_hits']}")

    miss_rows = still_neg[still_neg["實際"] == True]
    if len(miss_rows):
        print(f"\n=== 仍漏抓的 {len(miss_rows)} 支（規則改進參考）===")
        for _, r in miss_rows.iterrows():
            print(f"  [{r['category']}｜{r['video_format']}] {str(r['title'])[:40]}")
            note = str(r.get("備註_請填寫", "")).strip()
            if note not in ("", "nan"):
                print(f"      備註：{note}")

    if len(undecided):
        print(f"\n=== 無法判定的 {len(undecided)} 支（未計入統計）===")
        for _, r in undecided.iterrows():
            print(f"  {str(r['title'])[:50]}")


if __name__ == "__main__":
    main()
