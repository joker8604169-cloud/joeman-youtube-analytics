# -*- coding: utf-8 -*-
"""
階段二：資料清洗與分類

輸入：data/raw/*.json（由 src.fetch 產生）
輸出：
  data/processed/videos.csv       影片主檔（每次全量重建）
  data/processed/daily_stats.csv  每日統計快照（累加，不覆蓋歷史）

分類優先序（高→低）：
  manual_overrides.csv > playlist_map.csv > series_title_patterns.csv
  > category_rules.csv > 未分類

執行方式（於專案根目錄）：
  python -m src.transform
"""
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import isodate
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SHORTS_CACHE = config.DATA_DIR / "shorts_check_cache.json"
# Shorts 於 2020-09 後才存在；且目前上限 3 分鐘。僅對可能是 Shorts 的影片發請求
SHORTS_ERA_START = "2020-09-01"
SHORTS_MAX_SECONDS = 181


def read_csv_rules(path):
    """讀取規則 CSV，容忍 Excel 另存造成的編碼差異。

    正則表達式常含逗號（如 .{0,20}），未加引號會被 CSV 切成多欄且不會報錯，
    導致規則靜默失效。此處檢查每列欄位數，不符即中止。
    """
    rows = None
    for enc in ("utf-8-sig", "cp950"):
        try:
            with open(path, encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue
    if rows is None:
        sys.exit(f"[錯誤] 無法讀取 {path}，請確認檔案編碼為 UTF-8")

    for i, row in enumerate(rows, start=2):
        if None in row:            # 欄位過多：正則裡的逗號被當成分隔符
            sys.exit(
                f"[錯誤] {path.name} 第 {i} 列欄位數過多，"
                f"多半是正則表達式含逗號卻未加雙引號。\n"
                f"       請將該欄位改寫為 \"...\" 包起來。內容：{row}"
            )
        if any(v is None for v in row.values()):   # 欄位過少
            sys.exit(f"[錯誤] {path.name} 第 {i} 列欄位數不足，內容：{row}")
    return rows


def load_raw():
    def _load(name):
        with open(config.RAW_DIR / name, encoding="utf-8") as f:
            return json.load(f)
    return _load("videos.json"), _load("playlist_video_map.json")


def parse_duration_seconds(iso_duration):
    try:
        return int(isodate.parse_duration(iso_duration).total_seconds())
    except Exception:
        return 0


def detect_shorts(df):
    """URL redirect 檢測，結果快取；非候選影片直接視為長片"""
    cache = {}
    if SHORTS_CACHE.exists():
        with open(SHORTS_CACHE, encoding="utf-8") as f:
            cache = json.load(f)

    candidates = df[
        (df["duration_seconds"] > 0)
        & (df["duration_seconds"] <= SHORTS_MAX_SECONDS)
        & (df["published_at_taipei"] >= SHORTS_ERA_START)
    ]["video_id"].tolist()
    to_check = [v for v in candidates if v not in cache]
    print(f"Shorts 候選 {len(candidates)} 支，其中 {len(to_check)} 支需要網路檢測…")

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    for i, vid in enumerate(to_check, 1):
        try:
            r = session.head(
                f"https://www.youtube.com/shorts/{vid}",
                allow_redirects=False, timeout=10,
            )
            if r.status_code == 200:
                cache[vid] = True
            elif 300 <= r.status_code < 400:
                cache[vid] = False
            else:
                cache[vid] = None  # 無法判定，之後用時長備援
        except requests.RequestException:
            cache[vid] = None
        if i % 50 == 0 or i == len(to_check):
            print(f"  檢測進度：{i}/{len(to_check)}", end="\r")
            with open(SHORTS_CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f)
        time.sleep(0.05)
    if to_check:
        print()
        with open(SHORTS_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f)

    def is_short(row):
        vid = row["video_id"]
        if vid in cache and cache[vid] is not None:
            return cache[vid]
        # 備援：檢測失敗或非候選 → 60 秒內且在 Shorts 時代視為 Shorts
        return (
            row["published_at_taipei"] >= SHORTS_ERA_START
            and 0 < row["duration_seconds"] <= 60
        )

    undetermined = sum(1 for v in candidates if cache.get(v) is None)
    if undetermined:
        print(f"  [注意] {undetermined} 支檢測無法判定，已用 60 秒時長備援判斷")
    return df.apply(is_short, axis=1)


def build_classifiers():
    """回傳 (override_map, playlist_lookup, series_patterns, category_rules)"""
    overrides = {
        r["video_id"]: r
        for r in read_csv_rules(config.RULES_DIR / "manual_overrides.csv")
        if r.get("video_id")
    }

    playlist_rows = read_csv_rules(config.RULES_DIR / "playlist_map.csv")
    playlist_rows.sort(key=lambda r: -int(r["item_count"] or 0))

    series_patterns = [
        (r["series_name"], re.compile(r["title_pattern"], re.IGNORECASE), r["category"])
        for r in read_csv_rules(config.RULES_DIR / "series_title_patterns.csv")
    ]

    category_rules = read_csv_rules(config.RULES_DIR / "category_rules.csv")
    category_rules.sort(key=lambda r: int(r["priority"]))
    compiled_cat = [
        (r["category"], r["match_field"], re.compile(r["keywords"], re.IGNORECASE))
        for r in category_rules
    ]
    return overrides, playlist_rows, series_patterns, compiled_cat


def classify(df, playlist_video_map):
    overrides, playlist_rows, series_patterns, category_rules = build_classifiers()

    # 影片 → 所屬播放清單（依收錄數大者優先取系列/類型）
    vid_to_playlists = {}
    for pl_id, info in playlist_video_map.items():
        for vid in info["video_ids"]:
            vid_to_playlists.setdefault(vid, []).append(pl_id)
    pl_meta = {r["playlist_id"]: r for r in playlist_rows}

    # 頻道日後新增播放清單時，規則表不會自動包含，需提醒補上分類
    unmapped = [
        (pid, info["title"], len(info["video_ids"]))
        for pid, info in playlist_video_map.items()
        if pid not in pl_meta
    ]
    if unmapped:
        print(f"  [提醒] 有 {len(unmapped)} 個播放清單未列於 playlist_map.csv，"
              f"其影片將改由標題關鍵字分類：")
        for pid, title, cnt in unmapped:
            print(f"         {cnt:>4} 支 | {title}")
        print(f"         如需納入系列判定，請在 rules/playlist_map.csv 補上該列")

    series_list, category_list, source_list, pl_titles_list = [], [], [], []
    for _, row in df.iterrows():
        vid, title = row["video_id"], row["title"]
        series, category, source = "", "", ""

        pl_ids = vid_to_playlists.get(vid, [])
        pl_titles_list.append(
            "|".join(pl_meta[p]["playlist_title"] for p in pl_ids if p in pl_meta)
        )

        ov = overrides.get(vid)
        if ov:
            series = ov.get("series_name", "") or ""
            category = ov.get("category", "") or ""
            source = "override"

        if not category:
            for r in playlist_rows:
                if r["playlist_id"] in pl_ids and r["category"]:
                    series = series or r["series_name"]
                    category = r["category"]
                    source = "playlist"
                    break

        if not category:
            for s_name, s_re, s_cat in series_patterns:
                if s_re.search(title):
                    series, category, source = s_name, s_cat, "title_series"
                    break

        if not category:
            for cat, field, kw_re in category_rules:
                text = title if field == "title" else row.get(field, "")
                if kw_re.search(str(text)):
                    category, source = cat, "keyword"
                    break

        if not category:
            category, source = "未分類", "none"

        # 即使類型由關鍵字決定，系列仍可由標題正則補判
        if not series:
            for s_name, s_re, _ in series_patterns:
                if s_re.search(title):
                    series = s_name
                    break

        series_list.append(series)
        category_list.append(category)
        source_list.append(source)

    df["series"] = series_list
    df["category"] = category_list
    df["category_source"] = source_list
    df["playlists"] = pl_titles_list
    return df


def strip_boilerplate(description):
    """剔除說明欄中每支影片都有的罐頭文字（社群連結、器材清單、常設團購等），
    避免其中的「連結」「團購」「官方」等字造成業配誤判。"""
    patterns = getattr(strip_boilerplate, "_patterns", None)
    if patterns is None:
        rows = read_csv_rules(config.RULES_DIR / "description_boilerplate.csv")
        patterns = [re.compile(r["pattern"]) for r in rows]
        strip_boilerplate._patterns = patterns

    kept = []
    for line in str(description).split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if any(p.search(stripped) for p in patterns):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def extract_self_promo(description):
    """分離自營商品導流內容。

    自營商品（如自家課程、自營團購、頻道會員）屬於自我推廣，
    並非外部廠商的商業委託，因此不應計入業配。
    此函式將這類內容自說明欄抽出，回傳 (剩餘文字, 命中的自營商品清單)，
    剩餘文字才進入業配計分。
    """
    rules = getattr(extract_self_promo, "_rules", None)
    if rules is None:
        rows = read_csv_rules(config.RULES_DIR / "self_promo_rules.csv")
        rules = [(re.compile(r["pattern"]), r["product"]) for r in rows]
        extract_self_promo._rules = rules

    kept, products = [], []
    for line in str(description).split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        matched = None
        for pat, product in rules:
            if pat.search(stripped):
                matched = product
                break
        if matched:
            if matched not in products:
                products.append(matched)
        else:
            kept.append(stripped)
    return "\n".join(kept), products


# 加權計分達此門檻即判定為業配。
# 門檻由 3 降為 2 的依據：2026-07 人工抽樣核對中，分數 2 分的 9 支影片
# 經逐支查證全部為真實業配（9/9），顯示 3 分門檻過嚴造成系統性漏抓。
SPONSOR_SCORE_THRESHOLD = 2


def detect_sponsor(df):
    """業配判定：人工覆寫 > API 官方旗標 > 關鍵字加權計分

    計分在「已剔除罐頭文字」的說明欄上進行。
    weight 3 = 強訊號（折扣碼、贊助聲明等），單獨命中即成立；
    weight 2 = 導購用語，需兩項以上；weight 1 = 弱訊號，需搭配其他訊號。
    """
    rules = read_csv_rules(config.RULES_DIR / "sponsor_rules.csv")
    compiled = [
        (int(r["weight"]), r["match_field"], re.compile(r["pattern"]), r["note"])
        for r in rules
    ]
    overrides = {
        r["video_id"]: r
        for r in read_csv_rules(config.RULES_DIR / "manual_overrides.csv")
        if r.get("video_id") and (r.get("is_sponsored") or "").strip() != ""
    }

    flags, sources, scores, hits_list = [], [], [], []
    for _, row in df.iterrows():
        ov = overrides.get(row["video_id"])
        if ov:
            flags.append(
                ov["is_sponsored"].strip() in ("1", "true", "True", "是", "y", "Y")
            )
            sources.append("override")
            scores.append(None)
            hits_list.append("")
            continue

        clean_desc = row["description_clean"]
        score, hits = 0, []
        for weight, field, pat, note in compiled:
            # both：標題與說明欄都查。因 Shorts 有 85.7% 說明欄完全空白，
            # 導購訊息只寫在標題裡，僅查說明欄會系統性漏抓。
            # 但只有權重 3 的「強訊號」開放標題判定——標題僅約 40 字、
            # 缺乏上下文，若讓「限時」「新品」等弱訊號在標題就成立會誤判。
            if field == "both":
                text = f"{row['title']}\n{clean_desc}"
            elif field == "title":
                text = row["title"]
            else:
                text = clean_desc
            if pat.search(str(text)):
                score += weight
                hits.append(note)

        if row["ppd_api_flag"]:
            flags.append(True)
            sources.append("api")
            scores.append(score)
            hits_list.append("|".join(hits))
            continue

        is_sp = score >= SPONSOR_SCORE_THRESHOLD
        flags.append(is_sp)
        sources.append("keyword" if is_sp else "none")
        scores.append(score)
        hits_list.append("|".join(hits))

    df["is_sponsored"] = flags
    df["sponsor_source"] = sources
    df["sponsor_score"] = scores
    df["sponsor_hits"] = hits_list
    return df


def apply_exclusions(df):
    """剔除不適合納入分析的異常影片，回傳 (保留, 排除) 兩份資料。

    排除規則以 (條件函式, 原因) 表示，新增規則只需擴充此清單。
    被排除者不會進入 videos.csv 與 daily_stats.csv，但會完整記錄於
    excluded_videos.csv，保持資料處理可追溯。
    """
    exclusion_rules = [
        (
            lambda d: d["view_count"] == 0,
            "觀看數為 0：測試影片或中斷的直播，計算比率會產生除以零",
        ),
    ]

    reasons = pd.Series([""] * len(df), index=df.index)
    for condition, reason in exclusion_rules:
        mask = condition(df) & (reasons == "")
        reasons[mask] = reason

    excluded = df[reasons != ""].copy()
    excluded["exclusion_reason"] = reasons[reasons != ""]
    kept = df[reasons == ""].copy()
    return kept, excluded


# 人工抽樣驗證的結果。這些數字無法由程式自動算出，
# 必須由 src.sponsor_evaluate 產出後填回。
#
# 🔴 2026-08-05 全部重測。舊值（精確率 96.8%、召回率 85.3%、抽樣 63 支）是
# **混合格式**樣本算出來的——`sponsor_recall_sample.csv` 實際組成是 25 Shorts ＋ 14 長片，
# 而 `sponsor_evaluate` 不帶格式參數時也不篩格式，所以那從來就不是長片的數字。
# 拆出長片自己的部分只剩 6 支可判定、0 漏抓，推不出任何東西。
# 已重抽 40 支純長片重測，漏抓率 14/40＝35.0%。詳見 HANDOFF 第八章。
#
# ⚠️ 兩種格式的召回率**必須分開量測**（可用文字量差十倍），不可互相沿用。
# ⚠️ 重跑抽樣時務必指定格式：python -m src.sponsor_sample_recall 長片
AUDIT = {
    "長片精確率": "22/22（95% 信賴區間 85.1–100%）",
    "長片召回率": "76.3%（95% 信賴區間 69.1–83.6%）",
    "Shorts召回率": "14.6%（95% 信賴區間 10.5–21.7%）",
    "Shorts推估業配率": "37.9%（95% 信賴區間 25.6–52.9%）",
    "長片抽樣數": 40,
    "Shorts抽樣數": 40,
}


def write_data_notes(total, kept, excluded, snapshot_dates, api_video_count=None):
    """輸出資料備註檔，供 Power BI 以表格視覺呈現"""
    gap = (f"（API 回報 {api_video_count} 支，差距 {api_video_count - total} 支"
           f"含會員限定影片，無法逐支確認）" if api_video_count else "")
    notes = [
        ("資料來源", "YouTube Data API v3，頻道 Joeman（UCPRWWKG0VkBA0Pqa4Jr5j0Q）"),
        ("抓取範圍", f"公開影片 {total} 支{gap}"),
        ("分析母體", f"{kept} 支（排除觀看數為 0 的 {len(excluded)} 支）"),
        ("時區", "所有時間已轉換為台灣時間（UTC+8）"),
        ("Shorts 判定",
         "以 youtube.com/shorts/ 網址實測判定，非依時長推斷"),
        ("影片類型分類",
         "依播放清單歸屬與標題關鍵字規則分類，另設人工覆寫表"),
        ("業配判定方式",
         "關鍵字加權計分，門檻 2 分；自營商品（課程、團購、會員、自有品牌）另計"),
        ("業配準確度（長片）",
         f"精確率 {AUDIT['長片精確率']}、召回率 {AUDIT['長片召回率']}"
         f"，經 {AUDIT['長片抽樣數']} 支純隨機抽樣人工核對"),
        ("Shorts 偵測限制",
         f"召回率僅 {AUDIT['Shorts召回率']}——85.7% 說明欄完全空白。"
         f"經 {AUDIT['Shorts抽樣數']} 支抽樣，推估真實業配率 "
         f"{AUDIT['Shorts推估業配率']}，故商業分析排除 Shorts"),
        ("更新時間", pd.Timestamp.now(tz="Asia/Taipei").strftime("%Y-%m-%d %H:%M:%S")),
    ]
    path = config.PROCESSED_DIR / "data_notes.csv"
    pd.DataFrame(notes, columns=["項目", "說明"]).to_csv(
        path, index=False, encoding="utf-8-sig"
    )
    print(f"已輸出 {path}（資料備註 {len(notes)} 項）")


REVIEW_WINDOW_DAYS = 90     # 待檢視報告只看近期影片，避免清單過長


def write_review_queue(df, playlist_video_map, playlist_rows):
    """輸出需人工檢視的項目。

    分類規則不會自己成長：頻道開新系列、業配換新措辭時規則都會漏。
    此報告的用途是讓人每週花幾分鐘掃一眼就知道要不要補規則，
    而不是等到數字不對才回頭查。
    """
    cutoff = (pd.Timestamp.now(tz="Asia/Taipei")
              - pd.Timedelta(days=REVIEW_WINDOW_DAYS)).strftime("%Y-%m-%d")
    recent = df[df["published_date"] >= cutoff]
    rows = []

    mapped = {r["playlist_id"] for r in playlist_rows}
    for pid, info in playlist_video_map.items():
        if pid not in mapped:
            rows.append({
                "類型": "未對應的播放清單",
                "項目": info["title"],
                "說明": f"{len(info['video_ids'])} 支影片，請補入 rules/playlist_map.csv",
            })

    # 標題含書名號卻無系列歸屬 —— 這正是漏掉新系列時的特徵
    import re as _re
    seen = set()
    for _, r in df[df["series"].isna()].iterrows():
        for name in _re.findall(r"《([^》]{2,14})》", str(r["title"])):
            if name not in seen:
                seen.add(name)
                n = int(df["title"].str.contains(f"《{name}", regex=False, na=False).sum())
                if n >= 2:
                    rows.append({
                        "類型": "疑似新系列",
                        "項目": f"《{name}》",
                        "說明": f"{n} 支影片標題含此名稱但未歸入任何系列",
                    })

    n_unc = int((recent["category"] == "未分類").sum())
    if n_unc:
        rows.append({
            "類型": "近期未分類影片",
            "項目": f"{n_unc} 支",
            "說明": f"近 {REVIEW_WINDOW_DAYS} 天內無法歸類，可考慮補 rules/category_rules.csv",
        })

    near = recent[(~recent["is_sponsored"])
                  & recent["sponsor_score"].between(1, SPONSOR_SCORE_THRESHOLD - 0.01)]
    if len(near):
        rows.append({
            "類型": "業配判定臨界",
            "項目": f"{len(near)} 支",
            "說明": f"近 {REVIEW_WINDOW_DAYS} 天內分數接近門檻，建議抽查是否為漏抓",
        })

    path = config.PROCESSED_DIR / "review_queue.csv"
    if not rows:
        rows = [{"類型": "無", "項目": "—", "說明": "本次執行未發現需人工檢視的項目"}]
    sanitize_text_columns(pd.DataFrame(rows)).to_csv(
        path, index=False, encoding="utf-8-sig")
    print(f"已輸出 {path}（待檢視 {len(rows)} 項）")
    for r in rows:
        print(f"  [{r['類型']}] {r['項目']}：{r['說明']}")


def write_limitations(df, excluded, api_video_count=None):
    """輸出報告限制清單，分為看不到／判不準／不能推論三類"""
    n_unclassified = int((df["category"] == "未分類").sum())
    pct_unclassified = n_unclassified / len(df) * 100
    gap = (api_video_count - (len(df) + len(excluded))) if api_video_count else None

    rows = [
        ("看不到", "會員限定影片"
         + (f"（約 {gap} 支差距）" if gap else ""),
         "會員收入線的內容與成效完全未納入；需頻道擁有者授權才能取得"),
        ("看不到", "歷史每日觀看數",
         "API 僅提供當下累計值，無法回溯；每日快照自建置日起累積"),
        ("看不到", "YouTube Analytics（觀眾留存、流量來源、收益）",
         "需頻道擁有者 OAuth 授權，本報告僅使用公開資料"),
        ("看不到", "影片畫面與口頭內容",
         "僅在影片中口頭揭露的業配無法偵測"),
        ("看不到", "產品上市日期",
         "無法用時序推斷「上市前搶先開箱」是否為廠商提供樣機"),
        ("判不準",
         f"Shorts 業配偵測（召回率 {AUDIT['Shorts召回率'].split('（')[0]}）",
         "85.7% 說明欄空白，僅能倚賴 40 字標題；商業分析已排除 Shorts"),
        ("判不準", f"未分類影片 {n_unclassified} 支（{pct_unclassified:.1f}%）",
         "關鍵字規則無法歸類，類型分析中列為獨立區塊"),
        ("判不準", "業配的金額與合作深度",
         "只能判定有無，無法衡量商業強度；一次大型業配與一次小額合作同權"),
        ("判不準", "業配與廠商提供樣機的界線",
         "本專案將樣機提供納入業配，但無法區分兩者比例"),
        ("判不準", "系列內部的業配成效",
         "各系列樣本不足，以自助抽樣法估計之 95% 信賴區間跨越零，不下結論"),
        ("不能推論", "無對照頻道",
         "無法排除整體市場趨勢的影響，觀察到的變化未必為該頻道獨有"),
        ("不能推論", "2024 年後影片仍在累積觀看",
         "實測 8 日成長中位數顯示仍在成長，跨年觀看比較限 2023 年及以前"),
        ("不能推論", "2026 年僅半年資料",
         "該年數字不與完整年度並列比較"),
        ("不能推論", "分類規則需人工維護",
         "新系列或新的業配措辭會漏抓，已設待檢視機制降低維護成本"),
    ]
    path = config.PROCESSED_DIR / "limitations.csv"
    pd.DataFrame(rows, columns=["類別", "限制", "對分析的影響"]).to_csv(
        path, index=False, encoding="utf-8-sig"
    )
    print(f"已輸出 {path}（限制清單 {len(rows)} 項）")


def sanitize_text_columns(df):
    """輸出前把文字欄位裡的換行與定位字元壓成單一空白。

    YouTube 允許標題含換行（創作者常把雜湊標籤打在第二行），pandas 會依 CSV 標準
    用雙引號把這種欄位包起來，**檔案本身完全合法**。

    但 Power BI 的「文字/CSV」連接器預設產生 `QuoteStyle=QuoteStyle.None`，
    那個設定叫它無視雙引號，於是遇到欄位內的換行就硬切成新的一列，
    造成後續欄位整體位移（症狀：video_id 出現標題碎片、duration_seconds 變成 Error）。

    在輸出端統一清掉，任何下游工具都不會再踩到這個坑。

    ⚠️ 刻意只在寫檔前套用，不動 df 本身——分類與業配判定都拿 title 做正則比對，
    提前改動可能讓判定結果產生微小差異。這樣可保證輸出的數字與過去完全一致。

    ⚠️ 判斷「是不是文字欄」不能寫 `df[col].dtype == object`。
    pandas 3.x 起文字欄的 dtype 是 `str` 而非 `object`，那樣寫會讓每一欄都被跳過、
    函式靜默失效且不報錯——本機（3.x）與 CI（可能是 2.x）行為還會不一致。
    """
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_string_dtype(out[col]) or out[col].dtype == object:
            out[col] = out[col].map(
                lambda v: re.sub(r"\s*[\r\n\t]+\s*", " ", v).strip()
                if isinstance(v, str) else v
            )
    return out


def main():
    videos, playlist_video_map = load_raw()
    print(f"載入原始資料：{len(videos)} 支影片")

    rows = []
    for v in videos:
        sn, st = v["snippet"], v.get("statistics", {})
        rows.append({
            "video_id": v["id"],
            "title": sn["title"],
            "description": sn.get("description", ""),
            "published_at_utc": sn["publishedAt"],
            "duration_seconds": parse_duration_seconds(
                v.get("contentDetails", {}).get("duration", "")
            ),
            "youtube_category_id": sn.get("categoryId", ""),
            "tags": "|".join(sn.get("tags", [])),
            "view_count": int(st.get("viewCount", 0)),
            "like_count": int(st["likeCount"]) if "likeCount" in st else None,
            "comment_count": int(st["commentCount"]) if "commentCount" in st else None,
            "ppd_api_flag": v.get("paidProductPlacementDetails", {}).get(
                "hasPaidProductPlacement", False
            ),
        })
    df = pd.DataFrame(rows)

    ts = pd.to_datetime(df["published_at_utc"]).dt.tz_convert("Asia/Taipei")
    df["published_at_taipei"] = ts.dt.strftime("%Y-%m-%d %H:%M:%S")
    df["published_date"] = ts.dt.strftime("%Y-%m-%d")
    df["published_year"] = ts.dt.year
    df["published_hour"] = ts.dt.hour

    print("進行 Shorts 判定…")
    df["is_short"] = detect_shorts(df)
    df["video_format"] = df["is_short"].map({True: "Shorts", False: "長片"})

    print("進行系列與類型分類…")
    df = classify(df, playlist_video_map)
    print("剔除說明欄罐頭文字…")
    df["description_clean"] = df["description"].map(strip_boilerplate)

    print("分離自營商品導流…")
    separated = df["description_clean"].map(extract_self_promo)
    df["description_clean"] = separated.map(lambda x: x[0])
    df["self_promo_products"] = separated.map(lambda x: "|".join(x[1]))
    df["is_self_promo"] = df["self_promo_products"] != ""
    print(f"  含自營商品導流：{int(df['is_self_promo'].sum())} 支"
          f"（不計入業配）")

    print("進行業配判定…")
    df = detect_sponsor(df)

    df["url"] = "https://www.youtube.com/watch?v=" + df["video_id"]

    total_before = len(df)
    df, excluded = apply_exclusions(df)
    print(f"排除異常影片：{len(excluded)} 支（保留 {len(df)} 支）")
    for _, r in excluded.iterrows():
        print(f"  排除「{r['title'][:30]}」→ {r['exclusion_reason']}")

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    excluded_path = config.PROCESSED_DIR / "excluded_videos.csv"
    sanitize_text_columns(excluded[
        ["video_id", "title", "published_at_taipei", "video_format",
         "view_count", "like_count", "comment_count", "exclusion_reason", "url"]
    ]).to_csv(excluded_path, index=False, encoding="utf-8-sig")
    print(f"已輸出 {excluded_path}（{len(excluded)} 筆，保留紀錄以利追溯）")

    out_cols = [
        "video_id", "title", "published_at_taipei", "published_date",
        "published_year", "published_hour", "duration_seconds", "video_format",
        "category", "category_source", "series", "playlists",
        "is_sponsored", "sponsor_source", "sponsor_score", "sponsor_hits",
        "ppd_api_flag", "is_self_promo", "self_promo_products",
        "view_count", "like_count", "comment_count",
        "youtube_category_id", "url",
    ]
    videos_path = config.PROCESSED_DIR / "videos.csv"
    sanitize_text_columns(df[out_cols]).to_csv(
        videos_path, index=False, encoding="utf-8-sig")
    print(f"已輸出 {videos_path}（{len(df)} 筆）")

    # 每日快照：日期取自「原始資料抓取時間」而非執行時間。
    # 否則對同一份 raw JSON 重跑清洗，會產生數值完全相同卻標示不同日期的
    # 假快照，讓時間序列失真。
    with open(config.RAW_DIR / "fetch_meta.json", encoding="utf-8") as f:
        fetched_at = json.load(f)["fetched_at_utc"]
    today = (pd.Timestamp(fetched_at).tz_convert("Asia/Taipei")
             .strftime("%Y-%m-%d"))
    print(f"快照日期採用原始資料抓取日：{today}")
    snap = df[["video_id", "view_count", "like_count", "comment_count"]].copy()
    snap.insert(0, "snapshot_date", today)
    stats_path = config.PROCESSED_DIR / "daily_stats.csv"
    if stats_path.exists():
        old = pd.read_csv(stats_path, encoding="utf-8-sig")
        old = old[old["snapshot_date"] != today]
        # 歷史快照中若含已排除影片，一併清除以維持兩表一致
        old = old[~old["video_id"].isin(excluded["video_id"])]
        snap = pd.concat([old, snap], ignore_index=True)
    snap.to_csv(stats_path, index=False, encoding="utf-8-sig")
    snapshot_dates = sorted(snap["snapshot_date"].unique())
    print(f"已輸出 {stats_path}（快照日：{today}，累計 {len(snap)} 筆）")

    # 頻道統計回報的影片數，用於揭露與可抓取數量的差距
    with open(config.RAW_DIR / "channel.json", encoding="utf-8") as f:
        api_count = int(json.load(f)["statistics"].get("videoCount", 0)) or None

    write_data_notes(total_before, len(df), excluded, snapshot_dates, api_count)
    write_limitations(df, excluded, api_count)
    write_review_queue(df, playlist_video_map,
                       read_csv_rules(config.RULES_DIR / "playlist_map.csv"))

    print("\n=== 分類摘要 ===")
    print(df["category"].value_counts().to_string())
    print("\n=== 分類依據來源 ===")
    print(df["category_source"].value_counts().to_string())
    print("\n=== 長片 / Shorts ===")
    print(df["video_format"].value_counts().to_string())
    print("\n=== 業配判定 ===")
    print(df.groupby(["is_sponsored", "sponsor_source"]).size().to_string())
    print("\n=== 系列（前15）===")
    print(df[df["series"] != ""]["series"].value_counts().head(15).to_string())


if __name__ == "__main__":
    main()
