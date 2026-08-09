# -*- coding: utf-8 -*-
"""
階段三：資料清洗 ＋ 星狀結構重建（Power BI 模型層）

輸入（由 src.transform 產生）：
  data/processed/videos.csv        影片主檔（扁平寬表，24 欄）
  data/processed/daily_stats.csv   每日觀看快照
  rules/format_audit.csv           人工抽樣驗證結果（召回率／精確率）

輸出（data/processed/star/）：
  維度表  dim_date.csv      日期維度（連續日曆）
          dim_video.csv     影片維度（描述性屬性與查核欄位）
          dim_format.csv    影片格式維度（含各格式的偵測可信度常數）
          dim_category.csv  影片類型維度
          dim_series.csv    系列維度（含系列世代、首播日）
          dim_sponsor.csv   業配判定維度（junk dimension）
  事實表  fact_video.csv    影片事實（粒度＝一支影片一列）
          fact_daily.csv    每日快照事實（粒度＝一天一支影片一列）
  稽核    data_quality.csv  資料品質檢查報告（可直接放進報表）

為什麼另開一支程式而不改 transform.py
--------------------------------------
`transform.py` 承擔的是「原始 JSON → 乾淨寬表」的判定邏輯（分類、業配計分），
那一層的產出是**分析資料集**，每日自動化與所有人工抽樣檔都綁在它上面。
本程式承擔的是「寬表 → 維度模型」的**建模層**，只做重塑與型別／參照完整性清洗，
不碰任何判定規則。兩層分開的好處是：改模型不會動到已驗證的判定結果，
而 videos.csv 仍可獨立存在供抽樣程式使用。

執行方式（於專案根目錄）：
  python -m src.build_star
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

STAR_DIR = config.PROCESSED_DIR / "star"

# ── 建模常數 ────────────────────────────────────────────────
# 《Joe是要對決》最後一集（第 307 集）的發布日。系列世代的分界線。
# 🔴 刻意寫死，不用 MAX(published_date) 動態抓：動態寫法在《對決》若復播時
#    界線會往後跳，現有的新系列會全部被重新歸類且不報錯。
DUEL_END_DATE = pd.Timestamp("2025-11-10")

# 日期維度的起點。取固定值而非資料最小值，讓維度表在資料變動時保持穩定。
CALENDAR_START_YEAR = 2010

# 觀看數跨年比較的可比上限。2024 年後的影片仍在累積觀看。
# 判準來源：兩個快照實測的 8 日成長中位數（2023 年 188 次、2024 年 577 次、2025 年 2,785 次）。
LAST_COMPARABLE_YEAR = 2023

# 系列維度中代表「沒有系列歸屬」的成員。
# 🔴 這個成員是本次改版最重要的一個結構決定：
#    舊模型靠 ISBLANK(series) || series = "" 判斷「有沒有系列」，
#    而 series 的空白值匯入 Power BI 後可能是 null 也可能是空字串，
#    ISBLANK 只抓得到前者，抓不到時 2,374 支無系列影片會被誤判成有系列，
#    長片系列化比例從 36.6% 變成 100% 且不報錯。
#    改成「每一列都指向一個真實存在的維度成員」之後，這個坑在結構上就不存在了。
NO_SERIES_KEY = 0
NO_SERIES_NAME = "（無系列）"

_quality_rows = []


def note(item, status, count, action):
    """記錄一筆資料品質檢查結果"""
    _quality_rows.append(
        {"檢查項目": item, "結果": status, "筆數": count, "處理方式": action}
    )
    mark = "  " if status == "通過" else "⚠ "
    print(f"  {mark}{item}：{status}（{count}）")


# ── 清洗工具 ────────────────────────────────────────────────
def clean_text(s):
    """文字欄位清洗：全形空白轉半形、換行/定位字元壓成單一空白、去頭尾空白。

    ⚠️ 這裡可以放心做，與 transform.py 的 sanitize_text_columns 不同——
    那邊刻意只在寫檔前套用，因為分類與業配判定都拿 title 做正則比對。
    到了建模層，所有判定都已完成，清洗不會改變任何判定結果。
    """
    return (
        s.astype("string")
        .str.replace("　", " ", regex=False)
        .str.replace(r"\s*[\r\n\t]+\s*", " ", regex=True)
        .str.strip()
    )


def blank_to_na(s):
    """空字串一律轉成缺值，讓「空白」在整個模型裡只有一種表示法"""
    return s.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})


def clean_pipe_list(s):
    """以 | 分隔的多值欄位：去除空片段與重複，統一分隔符"""
    def _one(v):
        if pd.isna(v):
            return pd.NA
        parts, seen = [], set()
        for p in re.split(r"\s*\|\s*", str(v)):
            p = p.strip()
            if p and p not in seen:
                seen.add(p)
                parts.append(p)
        return "|".join(parts) if parts else pd.NA
    return s.map(_one)


def to_bool(s):
    """布林欄位正規化：容忍 True/False/1/0/是/否/y/n 等各種來源寫法"""
    truthy = {"true", "1", "是", "y", "yes", "t"}
    falsy = {"false", "0", "否", "n", "no", "f"}

    def _one(v):
        if isinstance(v, bool):
            return v
        if pd.isna(v):
            return False
        t = str(v).strip().lower()
        if t in truthy:
            return True
        if t in falsy:
            return False
        raise ValueError(f"無法辨識的布林值：{v!r}")
    return s.map(_one).astype(bool)


# ── 讀取與清洗來源 ──────────────────────────────────────────
def load_videos():
    path = config.PROCESSED_DIR / "videos.csv"
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    print(f"載入 {path.name}：{len(df)} 列 × {len(df.columns)} 欄")

    text_cols = [
        "video_id", "title", "published_at_taipei", "published_date",
        "video_format", "category", "category_source", "series",
        "sponsor_source", "url",
    ]
    for c in text_cols:
        df[c] = blank_to_na(clean_text(df[c]))
    for c in ("playlists", "sponsor_hits", "self_promo_products"):
        df[c] = clean_pipe_list(blank_to_na(clean_text(df[c])))
    for c in ("is_sponsored", "ppd_api_flag", "is_self_promo"):
        df[c] = to_bool(df[c])

    # ── 主鍵唯一性 ──
    dup = int(df["video_id"].duplicated().sum())
    if dup:
        df = df.drop_duplicates(subset="video_id", keep="last").reset_index(drop=True)
    note("影片主鍵 video_id 唯一性", "通過" if dup == 0 else "已去重",
         dup, "重複時保留最後一列" if dup else "無重複")

    missing_id = int(df["video_id"].isna().sum())
    if missing_id:
        df = df[df["video_id"].notna()].reset_index(drop=True)
    note("影片主鍵不可為空", "通過" if missing_id == 0 else "已剔除",
         missing_id, "剔除無主鍵的列" if missing_id else "無缺失")

    # ── 日期 ──
    df["published_date"] = pd.to_datetime(df["published_date"], errors="coerce")
    bad_date = int(df["published_date"].isna().sum())
    if bad_date:
        df = df[df["published_date"].notna()].reset_index(drop=True)
    note("發布日期可解析", "通過" if bad_date == 0 else "已剔除",
         bad_date, "剔除無法解析日期的列" if bad_date else "全部可解析為日期型別")

    df["published_at_taipei"] = pd.to_datetime(
        df["published_at_taipei"], errors="coerce"
    )

    # ── 數值 ──
    for c in ("duration_seconds", "published_hour", "youtube_category_id"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    df["view_count"] = pd.to_numeric(df["view_count"], errors="coerce").astype("Int64")
    # 🔴 like_count / comment_count 的空值代表「創作者關閉了按讚顯示／留言功能」，
    #    不是「零個讚」。補成 0 會製造假資料，一律保留為缺值。
    for c in ("like_count", "comment_count", "sponsor_score"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("like_count", "comment_count"):
        df[c] = df[c].astype("Int64")

    note("按讚數／留言數缺值",
         "保留",
         int(df["like_count"].isna().sum() + df["comment_count"].isna().sum()),
         "空值代表關閉功能，不補 0（補 0 會製造假資料）")

    # ── 值域 ──
    bad_hour = int(((df["published_hour"] < 0) | (df["published_hour"] > 23)).sum())
    note("發布時段 0–23", "通過" if bad_hour == 0 else "異常", bad_hour,
         "超出範圍者需回頭查 transform" if bad_hour else "全部在值域內")

    bad_view = int((df["view_count"].isna() | (df["view_count"] < 0)).sum())
    note("觀看數為非負整數", "通過" if bad_view == 0 else "異常", bad_view,
         "觀看數為 0 的影片已於 transform 階段排除")

    bad_dur = int((df["duration_seconds"] < 0).sum())
    note("影片長度非負", "通過" if bad_dur == 0 else "異常", bad_dur, "—")

    unknown_fmt = sorted(set(df["video_format"].dropna()) - {"長片", "Shorts"})
    note("影片格式僅長片／Shorts", "通過" if not unknown_fmt else "異常",
         len(unknown_fmt), f"未知值：{unknown_fmt}" if unknown_fmt else "無其他值")

    return df


def load_daily():
    path = config.PROCESSED_DIR / "daily_stats.csv"
    if not path.exists():
        note("每日快照檔存在", "略過", 0, "找不到 daily_stats.csv，不產生 fact_daily")
        return None
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    print(f"載入 {path.name}：{len(df)} 列")
    df["video_id"] = blank_to_na(clean_text(df["video_id"]))
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], errors="coerce")
    for c in ("view_count", "like_count", "comment_count"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")

    bad = int(df["snapshot_date"].isna().sum() | df["video_id"].isna().sum())
    df = df[df["snapshot_date"].notna() & df["video_id"].notna()]

    dup = int(df.duplicated(subset=["snapshot_date", "video_id"]).sum())
    if dup:
        df = df.drop_duplicates(subset=["snapshot_date", "video_id"], keep="last")
    note("每日快照複合主鍵（日期＋影片）唯一性",
         "通過" if dup == 0 else "已去重", dup,
         "重複時保留最後一列" if dup else "無重複")
    if bad:
        note("每日快照鍵值完整", "已剔除", bad, "剔除日期或影片 ID 為空的列")
    return df.reset_index(drop=True)


# ── 維度表 ──────────────────────────────────────────────────
def build_dim_format():
    """影片格式維度。

    ⭐ 這張表把人工抽樣得到的常數從 DAX 裡搬出來變成資料。
    舊模型的 `格式召回率` 是一個 SWITCH，把 0.763／0.146 寫死在量值裡，
    而 `判定精確率_長片` 又把 "22 / 22" 寫死在另一個量值裡——
    重做抽樣要改兩個地方，且改漏了不會報錯（2026-08-05 就踩過：
    側上圖總長 674、KPI3 卻顯示 603，同一頁兩個數字對不上）。
    改成維度資料行之後，重做抽樣只要改 rules/format_audit.csv 一個檔。
    """
    path = config.RULES_DIR / "format_audit.csv"
    src = pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False)
    dim = pd.DataFrame({
        "格式鍵": pd.to_numeric(src["format_order"]).astype("int64"),
        "影片格式": src["video_format"].map(str.strip),
        "格式排序": pd.to_numeric(src["format_order"]).astype("int64"),
        "納入商業分析": to_bool(src["include_in_commercial"]),
        "業配召回率": pd.to_numeric(src["recall_rate"]),
        "業配召回率信賴區間": blank_to_na(src["recall_ci"]),
        "業配精確率": blank_to_na(src["precision_label"]),
        "業配精確率信賴區間": blank_to_na(src["precision_ci"]),
        "抽樣數": pd.to_numeric(src["audit_sample_size"]).astype("int64"),
        "偵測可信度說明": src["detection_note"],
    })
    return dim.sort_values("格式鍵").reset_index(drop=True)


def first_seen(videos, col):
    """每個維度成員第一次出現的日期（不分格式）。

    ⭐ 代理鍵一律照這個日期排序後編號，理由見 assign_stable_keys。
    """
    return videos.dropna(subset=[col]).groupby(col)["published_date"].min()


def assign_stable_keys(members, seen, start=1):
    """依「第一次出現的日期」編代理鍵，回傳 {成員: 鍵}。

    🔴 **為什麼不用字母序、也不用資料量排序（2026-08-10 改）**

    代理鍵每次執行都重新產生。若照字母序編號，頻道開了一個名字排在中間的新系列，
    後面所有系列的鍵都會往後移一位——**事實表 3,506 列的外鍵全部改變**，
    每日自動更新那筆 commit 會從「多三列」變成「整份檔案重寫」。
    照資料量排序更糟：兩個類型的支數交叉就會互換，**沒有新成員也會漂**。

    改用「第一次出現的日期」之後，新成員的日期必然是最新的，鍵一律往後追加，
    既有成員的鍵不動。實測：新增一個系列時 fact_video.csv 只多三列。

    ⚠️ 唯一會插隊的情況是**補規則讓一批舊影片被重新歸類**（新成員的首次出現日
       落在過去）。那本來就是會全表重算的事件，且一年不會發生幾次。

    ⚠️ 鍵**不保證跨版本永久不變**，只保證「一般情況下不變」。任何時候
       事實表與維度表都是同一次執行的產物、一起提交，所以數字永遠是對的。
    """
    ordered = sorted(members, key=lambda m: (seen[m], str(m)))
    return {m: i for i, m in enumerate(ordered, start=start)}


def build_dim_category(videos):
    """影片類型維度。

    代理鍵依首次出現日期（穩定），顯示排序依全期長片數遞減、「未分類」固定排最後。
    **兩者刻意分開**——排序欄是給切片器看的，會隨資料變動；鍵是給事實表用的，不能變。
    """
    long_cnt = (
        videos[videos["video_format"] == "長片"]["category"]
        .value_counts()
    )
    cats = list(videos["category"].dropna().unique())
    keys = assign_stable_keys(cats, first_seen(videos, "category"))

    display = sorted(cats, key=lambda c: (c == "未分類", -int(long_cnt.get(c, 0)), c))
    order = {c: i for i, c in enumerate(display, start=1)}

    dim = pd.DataFrame({"影片類型": sorted(cats, key=lambda c: keys[c])})
    dim.insert(0, "類型鍵", dim["影片類型"].map(keys).astype("int64"))
    dim["已分類"] = dim["影片類型"] != "未分類"
    dim["類型排序"] = dim["影片類型"].map(order).astype("int64")
    dim["全期長片數"] = dim["影片類型"].map(
        lambda c: int(long_cnt.get(c, 0))
    ).astype("int64")
    return dim


def build_dim_series(videos):
    """系列維度。系列世代與首播日在此一次算好，取代兩個 DAX 計算欄位。

    系列世代的判定（與舊 DAX 計算欄位逐條等價）：
      1. 無系列歸屬                       → 無系列
      2. 該系列第一支**長片**晚於停播日     → 新系列
      3. 其餘（含只有 Shorts 的系列）      → 既有系列

    ⚠️ 第 3 點的括號是關鍵：若某系列只有 Shorts，firstPub 是空值，
       舊 DAX 的 IF ( firstPub > duelEnd, ... ) 在空值時走 FALSE 分支，
       結果是「既有系列」。這裡照樣處理，數字才會對得起來。
    """
    longs = videos[videos["video_format"] == "長片"]
    first_long = longs.groupby("series")["published_date"].min()

    # 代理鍵依「該系列第一支影片的發布日」編號（不限長片，只有 Shorts 的系列才有值）。
    # 新系列的日期必然最新 → 鍵往後追加，既有系列的鍵不動。理由見 assign_stable_keys。
    names = list(videos["series"].dropna().unique())
    keys = assign_stable_keys(names, first_seen(videos, "series"))

    rows = [{
        "系列鍵": NO_SERIES_KEY,
        "系列名稱": NO_SERIES_NAME,
        "有系列": False,
        "系列世代": "無系列",
        "系列首播日": pd.NaT,
        "系列長片集數_全期": 0,
        "系列世代判準": "沒有系列歸屬",
    }]
    for name in sorted(names, key=lambda n: keys[n]):
        i = keys[name]
        fp = first_long.get(name, pd.NaT)
        if pd.isna(fp):
            gen = "既有系列"
            basis = "只有 Shorts、沒有長片，無法認定為停播後開播"
        elif fp > DUEL_END_DATE:
            gen = "新系列"
            basis = (f"首播 {fp:%Y-%m-%d}，晚於《對決》停播日 "
                     f"{DUEL_END_DATE:%Y-%m-%d} {(fp - DUEL_END_DATE).days} 天")
        else:
            gen = "既有系列"
            basis = (f"首播 {fp:%Y-%m-%d}，早於《對決》停播日 "
                     f"{DUEL_END_DATE:%Y-%m-%d}")
        rows.append({
            "系列鍵": i,
            "系列名稱": name,
            "有系列": True,
            "系列世代": gen,
            "系列首播日": fp,
            "系列長片集數_全期": int((longs["series"] == name).sum()),
            "系列世代判準": basis,
        })
    return pd.DataFrame(rows)


SPONSOR_SOURCE_LABEL = {
    "override": "人工覆寫（最高優先）",
    "api": "YouTube 官方付費宣傳旗標",
    "keyword": "關鍵字加權計分達門檻 2 分",
    "none": "未達門檻，判定為非業配",
}


def build_dim_sponsor(videos):
    """業配判定維度（junk dimension）：把判定結果與判定來源收成一張小表。"""
    v = videos.copy()
    v["_combo"] = (
        v["is_sponsored"].astype(str) + "|" + v["sponsor_source"].astype(str)
    )
    keys = assign_stable_keys(list(v["_combo"].unique()), first_seen(v, "_combo"))

    combos = (
        v[["_combo", "is_sponsored", "sponsor_source"]]
        .drop_duplicates(subset="_combo")
        .assign(業配判定鍵=lambda d: d["_combo"].map(keys))
        .sort_values("業配判定鍵")
        .drop(columns="_combo")
        .reset_index(drop=True)
    )
    combos.insert(0, "業配判定鍵", combos.pop("業配判定鍵").astype("int64"))
    combos["業配標籤"] = combos["is_sponsored"].map({True: "業配", False: "非業配"})
    combos["判定來源說明"] = combos["sponsor_source"].map(SPONSOR_SOURCE_LABEL)
    return combos.rename(
        columns={"is_sponsored": "是否業配", "sponsor_source": "判定來源"}
    )[["業配判定鍵", "是否業配", "業配標籤", "判定來源", "判定來源說明"]]


def build_dim_date(max_date):
    """日期維度：2010-01-01 起的連續日曆，補到資料最大年度的年底。

    補到年底而不是資料最後一天，是為了讓「年份區間」切片器與月份軸
    在年中重新整理時不會少一截；沒有事實列的日期不會出現在任何視覺上
    （量值回傳 BLANK，圖表不畫）。
    """
    end = pd.Timestamp(year=max_date.year, month=12, day=31)
    idx = pd.date_range(f"{CALENDAR_START_YEAR}-01-01", end, freq="D")
    dim = pd.DataFrame({"日期": idx})
    dim["年"] = dim["日期"].dt.year
    dim["季"] = "Q" + dim["日期"].dt.quarter.astype(str)
    dim["月"] = dim["日期"].dt.month
    dim["年月"] = dim["日期"].dt.strftime("%Y-%m")
    dim["年月排序"] = dim["年"] * 100 + dim["月"]
    dim["月份"] = dim["月"].astype(str) + "月"
    # 觀看數跨年比較的可比性。條件式格式與工具提示用。
    dim["觀看可比性"] = dim["年"].map(
        lambda y: "可比" if y <= LAST_COMPARABLE_YEAR else "仍在累積"
    )
    dim["是否完整年度"] = dim["年"] < max_date.year
    return dim


def build_dim_video(videos):
    """影片維度：一支影片一列，放**描述性屬性與查核欄位**。

    數值（觀看／按讚／留言）留在事實表，這裡只放「用來描述這支影片是什麼」的欄位。
    ⚠️ 查核用欄位（分類依據、業配命中規則、自營商品、播放清單）刻意保留在維度裡，
       它們是給人追溯判定依據用的，不放進任何視覺。
    """
    dim = pd.DataFrame({
        "影片鍵": videos["影片鍵"],
        "video_id": videos["video_id"],
        "影片標題": videos["title"],
        "影片網址": videos["url"],
        "發布時間": videos["published_at_taipei"],
        "發布時段": videos["published_hour"],
        "影片長度秒": videos["duration_seconds"],
        "是首集": videos["是首集"],
        "分類依據": videos["category_source"],
        "業配分數": videos["sponsor_score"],
        "業配命中規則": videos["sponsor_hits"],
        "官方付費宣傳旗標": videos["ppd_api_flag"],
        "含自營商品導流": videos["is_self_promo"],
        "自營商品": videos["self_promo_products"],
        "所屬播放清單": videos["playlists"],
        "YouTube官方分類ID": videos["youtube_category_id"],
    })
    return dim


def mark_first_episode(videos):
    """標記每個系列的第一支長片（取代 DAX 計算欄位 `是首集`）。

    ⚠️ 首集同日發多支會標記多列——實測只有《Joeman Vlog》（3 支同天），
       它從無業配、不出現在任何視覺上，不影響。這裡刻意保留舊行為，
       改成只留一支會讓 KPI4 的分母悄悄改變。
    """
    longs = videos[videos["video_format"] == "長片"]
    first_long = longs.groupby("series")["published_date"].min()
    mapped = videos["series"].map(first_long)
    return (
        videos["series"].notna()
        & (videos["video_format"] == "長片")
        & (videos["published_date"] == mapped)
    ).fillna(False)


# ── 事實表 ──────────────────────────────────────────────────
def build_fact_video(videos, dim_format, dim_category, dim_series, dim_sponsor):
    f = videos.merge(
        dim_format[["格式鍵", "影片格式"]],
        left_on="video_format", right_on="影片格式", how="left",
    ).merge(
        dim_category[["類型鍵", "影片類型"]],
        left_on="category", right_on="影片類型", how="left",
    ).merge(
        dim_series[["系列鍵", "系列名稱"]],
        left_on=videos["series"].fillna(NO_SERIES_NAME),
        right_on="系列名稱", how="left",
    ).merge(
        dim_sponsor[["業配判定鍵", "是否業配", "判定來源"]],
        left_on=["is_sponsored", "sponsor_source"],
        right_on=["是否業配", "判定來源"], how="left",
    )

    fact = pd.DataFrame({
        "影片鍵": f["影片鍵"],
        "發布日期": f["published_date"],
        "格式鍵": f["格式鍵"],
        "類型鍵": f["類型鍵"],
        "系列鍵": f["系列鍵"],
        "業配判定鍵": f["業配判定鍵"],
        "觀看數": f["view_count"],
        "按讚數": f["like_count"],
        "留言數": f["comment_count"],
    })

    for col in ("格式鍵", "類型鍵", "系列鍵", "業配判定鍵"):
        orphan = int(fact[col].isna().sum())
        note(f"事實表外鍵 {col} 參照完整性",
             "通過" if orphan == 0 else "異常", orphan,
             "每一列都對得到維度成員" if orphan == 0
             else "有事實列找不到對應的維度成員，需回頭查建模程式")
        fact[col] = fact[col].astype("int64")
    return fact


def build_fact_daily(daily, videos):
    key = videos.set_index("video_id")["影片鍵"]
    d = daily.copy()
    d["影片鍵"] = d["video_id"].map(key)

    orphan = int(d["影片鍵"].isna().sum())
    if orphan:
        d = d[d["影片鍵"].notna()]
    note("每日快照參照完整性（影片存在於影片維度）",
         "通過" if orphan == 0 else "已剔除", orphan,
         "剔除已被排除影片留下的孤兒快照" if orphan else "每一列都對得到影片")

    return pd.DataFrame({
        "快照日期": d["snapshot_date"],
        "影片鍵": d["影片鍵"].astype("int64"),
        "觀看數": d["view_count"],
        "按讚數": d["like_count"],
        "留言數": d["comment_count"],
    }).reset_index(drop=True)


# ── 輸出 ────────────────────────────────────────────────────
def write(df, name, date_cols=()):
    out = df.copy()
    for c in date_cols:
        out[c] = pd.to_datetime(out[c]).dt.strftime("%Y-%m-%d")
    # 布林一律輸出 True/False，Power BI 才會自動判成 True/False 型別
    for c in out.columns:
        if out[c].dtype == bool:
            out[c] = out[c].map({True: "True", False: "False"})
    path = STAR_DIR / name
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  已輸出 {path.name}（{len(out)} 列 × {len(out.columns)} 欄）")


def main():
    STAR_DIR.mkdir(parents=True, exist_ok=True)

    print("\n=== 一、讀取與清洗 ===")
    videos = load_videos()
    daily = load_daily()

    print("\n=== 二、建立維度表 ===")
    videos = videos.sort_values(["published_date", "video_id"]).reset_index(drop=True)
    videos["影片鍵"] = range(1, len(videos) + 1)
    videos["是首集"] = mark_first_episode(videos)

    dim_format = build_dim_format()
    dim_category = build_dim_category(videos)
    dim_series = build_dim_series(videos)
    dim_sponsor = build_dim_sponsor(videos)
    dim_video = build_dim_video(videos)

    max_date = videos["published_date"].max()
    if daily is not None and len(daily):
        max_date = max(max_date, daily["snapshot_date"].max())
    dim_date = build_dim_date(max_date)

    missing_fmt = sorted(
        set(videos["video_format"].dropna()) - set(dim_format["影片格式"])
    )
    note("rules/format_audit.csv 涵蓋所有影片格式",
         "通過" if not missing_fmt else "異常", len(missing_fmt),
         f"缺少：{missing_fmt}" if missing_fmt else "長片與 Shorts 都有抽樣常數")

    print("\n=== 三、建立事實表 ===")
    fact_video = build_fact_video(
        videos, dim_format, dim_category, dim_series, dim_sponsor
    )
    fact_daily = (
        build_fact_daily(daily, videos) if daily is not None else None
    )

    fact_dates = set(fact_video["發布日期"].dt.normalize())
    outside = len(fact_dates - set(dim_date["日期"]))
    note("發布日期都落在日期維度範圍內",
         "通過" if outside == 0 else "異常", outside,
         "日曆已涵蓋全部事實日期" if outside == 0 else "需擴大 dim_date 範圍")

    print("\n=== 四、輸出 ===")
    write(dim_date, "dim_date.csv", date_cols=["日期"])
    write(dim_video, "dim_video.csv")
    write(dim_format, "dim_format.csv")
    write(dim_category, "dim_category.csv")
    write(dim_series, "dim_series.csv", date_cols=["系列首播日"])
    write(dim_sponsor, "dim_sponsor.csv")
    write(fact_video, "fact_video.csv", date_cols=["發布日期"])
    if fact_daily is not None:
        write(fact_daily, "fact_daily.csv", date_cols=["快照日期"])

    pd.DataFrame(_quality_rows).to_csv(
        STAR_DIR / "data_quality.csv", index=False, encoding="utf-8-sig"
    )
    print(f"  已輸出 data_quality.csv（檢查 {len(_quality_rows)} 項）")

    print("\n=== 五、建模摘要 ===")
    print(f"  fact_影片        {len(fact_video):>6} 列（粒度：一支影片一列）")
    if fact_daily is not None:
        print(f"  fact_每日快照    {len(fact_daily):>6} 列（粒度：一天一支影片一列）")
    print(f"  dim_日期         {len(dim_date):>6} 列"
          f"（{dim_date['日期'].min():%Y-%m-%d} – {dim_date['日期'].max():%Y-%m-%d}）")
    print(f"  dim_影片         {len(dim_video):>6} 列")
    print(f"  dim_影片格式     {len(dim_format):>6} 列")
    print(f"  dim_類型         {len(dim_category):>6} 列")
    print(f"  dim_系列         {len(dim_series):>6} 列")
    print(f"  dim_業配判定     {len(dim_sponsor):>6} 列")

    print("\n  系列世代分佈（影片數）：")
    gen = (
        fact_video.merge(dim_series[["系列鍵", "系列世代"]], on="系列鍵")
        ["系列世代"].value_counts()
    )
    for k, v in gen.items():
        print(f"    {k:<6} {v:>6}")
    print(f"    首集標記        {int(dim_video['是首集'].sum()):>4} 支")

    failed = [r for r in _quality_rows if r["結果"] == "異常"]
    if failed:
        print("\n🔴 有品質檢查未通過，請先處理再載入 Power BI：")
        for r in failed:
            print(f"    {r['檢查項目']}：{r['筆數']} 筆")
        sys.exit(1)
    print("\n✅ 全部品質檢查通過。")


if __name__ == "__main__":
    main()
