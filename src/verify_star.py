# -*- coding: utf-8 -*-
"""
星狀模型驗收：證明「換了模型，數字沒變」

沒有 Power BI 也能跑。做四件事：

  A. 結構等價  把星狀模型依關聯還原成寬表，與原本的 videos.csv 逐欄逐列比對。
               這一關通過，代表關聯設對之後 Power BI 看到的資料與舊模型完全相同。

  B. 衍生欄位  用**舊 DAX 計算欄位的定義**在 pandas 裡重算 `系列世代` 與 `是首集`，
               與 build_star.py 寫進維度表的值比對。這一關通過，代表把計算欄位
               搬進 Python 沒有改變任何判定。

  C. 量值等價  以舊扁平表與新星狀表分別復算 30+ 個關鍵指標（含三種觀看數門檻），
               逐項比對。這一關通過，代表改寫後的 DAX 會算出一樣的數字。

  D. 靜態引用  把 DAX_星狀模型.md 裡所有 '資料表'[資料行] 抓出來，
               對照實際 CSV 的欄位名。這一關通過，代表量值貼進 Power BI
               不會因為「找不到資料行」而報錯。

執行方式（於專案根目錄）：
  python -m src.verify_star
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from src.build_star import DUEL_END_DATE, NO_SERIES_NAME  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

STAR_DIR = config.PROCESSED_DIR / "star"
DAX_DOC = config.PROJECT_ROOT / "DAX_星狀模型.md"

# 觀看數門檻切片器的五個停點，取三個代表值做驗證
THRESHOLDS = [0, 100_000, 1_000_000]

_failures = []


def check(name, left, right, tol=1e-9):
    """比對一項，記錄結果並回報是否通過"""
    if isinstance(left, float) and isinstance(right, float):
        ok = abs(left - right) <= tol
    else:
        ok = left == right
    if ok:
        print(f"  ✓ {name}：{fmt(left)}")
    else:
        print(f"  ✗ {name}：舊 {fmt(left)} ≠ 新 {fmt(right)}")
        _failures.append(name)
    return ok


def fmt(v):
    if isinstance(v, float):
        return f"{v:,.6f}".rstrip("0").rstrip(".")
    if isinstance(v, (int,)):
        return f"{v:,}"
    return str(v)


# ── 兩邊都攤平成同一組欄位名，之後共用同一份量值程式 ──────────
CANON = [
    "video_id", "published_date", "video_format", "category",
    "series_name", "has_series", "series_gen", "is_first_ep",
    "is_sponsored", "sponsor_source", "view_count", "like_count", "comment_count",
]


def old_first_long_pub(df):
    """舊 DAX 計算欄位裡那段 ALLEXCEPT：每個系列第一支長片的發布日"""
    longs = df[df["video_format"] == "長片"]
    return longs.groupby("series")["published_date"].min()


def load_old():
    """讀 videos.csv，並用**舊 DAX 的定義**補上兩個計算欄位"""
    df = pd.read_csv(
        config.PROCESSED_DIR / "videos.csv", encoding="utf-8-sig",
        dtype={"video_id": str, "series": str, "category": str,
               "video_format": str, "sponsor_source": str},
    )
    df["published_date"] = pd.to_datetime(df["published_date"])

    first_pub = old_first_long_pub(df)
    mapped = df["series"].map(first_pub)

    # 系列世代：ISBLANK(s) || s = "" → 無系列；firstPub > 停播日 → 新系列；其餘既有系列
    # ⚠️ firstPub 為空（該系列只有 Shorts）時，DAX 的 IF 走 FALSE 分支 → 既有系列
    def gen(row_series, fp):
        if pd.isna(row_series) or row_series == "":
            return "無系列"
        if pd.notna(fp) and fp > DUEL_END_DATE:
            return "新系列"
        return "既有系列"

    df["series_gen"] = [gen(s, fp) for s, fp in zip(df["series"], mapped)]
    df["is_first_ep"] = (
        df["series"].notna()
        & (df["series"] != "")
        & (df["video_format"] == "長片")
        & (df["published_date"] == mapped)
    ).fillna(False)

    out = pd.DataFrame({
        "video_id": df["video_id"],
        "published_date": df["published_date"],
        "video_format": df["video_format"],
        "category": df["category"],
        "series_name": df["series"].fillna(NO_SERIES_NAME).replace("", NO_SERIES_NAME),
        "has_series": df["series"].notna() & (df["series"] != ""),
        "series_gen": df["series_gen"],
        "is_first_ep": df["is_first_ep"],
        "is_sponsored": df["is_sponsored"].astype(bool),
        "sponsor_source": df["sponsor_source"],
        "view_count": df["view_count"].astype("int64"),
        "like_count": pd.to_numeric(df["like_count"]).astype("Int64"),
        "comment_count": pd.to_numeric(df["comment_count"]).astype("Int64"),
    })
    return out.sort_values("video_id").reset_index(drop=True)


def read_star(name):
    return pd.read_csv(STAR_DIR / name, encoding="utf-8-sig")


def load_new():
    """把星狀模型依「多對一、單向」關聯還原成寬表——
    這正是 Power BI 在量值裡看到的資料。"""
    fact = read_star("fact_video.csv")
    dim_v = read_star("dim_video.csv")
    dim_f = read_star("dim_format.csv")
    dim_c = read_star("dim_category.csv")
    dim_s = read_star("dim_series.csv")
    dim_p = read_star("dim_sponsor.csv")

    w = (
        fact
        .merge(dim_v[["影片鍵", "video_id", "是首集"]], on="影片鍵", how="left")
        .merge(dim_f[["格式鍵", "影片格式"]], on="格式鍵", how="left")
        .merge(dim_c[["類型鍵", "影片類型"]], on="類型鍵", how="left")
        .merge(dim_s[["系列鍵", "系列名稱", "有系列", "系列世代"]], on="系列鍵", how="left")
        .merge(dim_p[["業配判定鍵", "是否業配", "判定來源"]], on="業配判定鍵", how="left")
    )
    out = pd.DataFrame({
        "video_id": w["video_id"].astype(str),
        "published_date": pd.to_datetime(w["發布日期"]),
        "video_format": w["影片格式"],
        "category": w["影片類型"],
        "series_name": w["系列名稱"],
        "has_series": w["有系列"].astype(bool),
        "series_gen": w["系列世代"],
        "is_first_ep": w["是首集"].astype(bool),
        "is_sponsored": w["是否業配"].astype(bool),
        "sponsor_source": w["判定來源"],
        "view_count": w["觀看數"].astype("int64"),
        "like_count": pd.to_numeric(w["按讚數"]).astype("Int64"),
        "comment_count": pd.to_numeric(w["留言數"]).astype("Int64"),
    })
    return out.sort_values("video_id").reset_index(drop=True)


# ── C. 量值復算 ─────────────────────────────────────────────
def safe_div(a, b):
    """分母為零回傳 None 而不是丟例外——這支程式擋在每日自動更新的路徑上"""
    return round(a / b, 10) if b else None


def m_count(df, th=0):
    return int((df["view_count"] >= th).sum())


def m_sponsored(df, th=0):
    return int((df["is_sponsored"] & (df["view_count"] >= th)).sum())


def m_rate(df, th=0):
    d = m_count(df, th)
    return round(m_sponsored(df, th) / d, 10) if d else None


def m_median(df, th=0):
    s = df.loc[df["view_count"] >= th, "view_count"]
    return float(s.median()) if len(s) else None


def m_sum(df, th=0):
    return int(df.loc[df["view_count"] >= th, "view_count"].sum())


def longs(df):
    return df[df["video_format"] == "長片"]


def category_rates(df, th, min_long=20):
    """[類型業配率] 逐類型，含 >= 20 的樣本量閘門（閘門本身不含門檻）"""
    rows = []
    for cat, g in df.groupby("category"):
        gl = longs(g)
        if len(gl) < min_long:          # [僅長片] >= 20，不含觀看數門檻
            continue
        sample = m_count(gl, th)        # [樣本數_長片]，含門檻
        rate = m_rate(gl, th)
        rows.append({"category": cat, "rate": rate, "sample": sample})
    return pd.DataFrame(rows)


def m_top_category(df, th):
    t = category_rates(df, th)
    t = t[t["rate"].notna()]
    if t.empty:
        return None
    return t.sort_values("rate", ascending=False).iloc[0]["category"]


def m_bottom_category(df, th):
    """TOPN ( 1, t, [@率], ASC, [@樣本], DESC ) —— 兩組排序，並列時取樣本多的"""
    t = category_rates(df, th)
    t = t[(t["sample"] > 0) & t["rate"].notna()]
    if t.empty:
        return None
    return t.sort_values(["rate", "sample"], ascending=[True, False]).iloc[0]["category"]


def m_gap(df, th):
    t = category_rates(df, th)
    t = t[t["rate"].notna()]
    if t.empty:
        return None
    return f"{(t['rate'].max() - t['rate'].min()) * 100:.1f}pp"


def m_series_first_sponsor_ep(df, series):
    """[首次業配集數]：到第一支業配為止，該系列累計第幾集（限長片）"""
    g = longs(df[df["series_name"] == series])
    sp = g[g["is_sponsored"]]
    if sp.empty:
        return None
    first = sp["published_date"].min()
    return int((g["published_date"] <= first).sum())


def m_series_density_top(df, th, min_eps=10):
    """[系列業配密度] 排序後的第一名（集數門檻不含觀看數門檻）"""
    rows = []
    for name, g in df[df["has_series"]].groupby("series_name"):
        if len(longs(g)) < min_eps:
            continue
        r = m_rate(longs(g), th)
        if r is not None:
            rows.append((name, r))
    if not rows:
        return None
    return sorted(rows, key=lambda x: -x[1])[0][0]


def m_first_sponsor_chart_rows(df, min_eps=50):
    """[首次業配出現在第幾集] 會畫出幾列：新系列全收 ＋ 集數 >= 50 的既有系列"""
    keep = []
    for name, g in df[df["has_series"]].groupby("series_name"):
        gen = g["series_gen"].iloc[0]
        eps = len(longs(g))
        if gen == "新系列" or eps >= min_eps:
            if m_series_first_sponsor_ep(df, name) is not None:
                keep.append(name)
    return len(keep)


RECALL = {"長片": 0.763, "Shorts": 0.146}


def metrics(df, label):
    """一次算完所有要比對的指標，回傳 dict"""
    out = {}
    L = longs(df)
    for th in THRESHOLDS:
        tag = "全部" if th == 0 else f"{th // 10000}萬"
        out[f"影片數（門檻{tag}）"] = m_count(df, th)
        out[f"長片數（門檻{tag}）"] = m_count(L, th)
        out[f"Shorts數（門檻{tag}）"] = m_count(df[df["video_format"] == "Shorts"], th)
        out[f"中位觀看（門檻{tag}）"] = m_median(df, th)
        out[f"長片中位觀看（門檻{tag}）"] = m_median(L, th)
        out[f"業配影片數_長片（門檻{tag}）"] = m_sponsored(L, th)
        out[f"業配率_長片（門檻{tag}）"] = m_rate(L, th)
        out[f"業配率_全部（門檻{tag}）"] = m_rate(df, th)
        out[f"流量貢獻度（門檻{tag}）"] = safe_div(m_sum(df, th), m_sum(df, 0))
        out[f"業配率最高類型（門檻{tag}）"] = m_top_category(df, th)
        out[f"業配率最低類型（門檻{tag}）"] = m_bottom_category(df, th)
        out[f"類型間差距（門檻{tag}）"] = m_gap(df, th)
        out[f"系列業配密度第一名（門檻{tag}）"] = m_series_density_top(df, th)

    # 年度切片（模擬年份區間切片器）
    for y0 in (2022,):
        sub = df[df["published_date"].dt.year >= y0]
        out[f"業配率_長片（{y0} 年起）"] = m_rate(longs(sub), 0)
        out[f"業配率_全部（{y0} 年起）"] = m_rate(sub, 0)
    y2010 = df[df["published_date"].dt.year == 2010]
    out["長片系列化比例（2010）"] = safe_div(
        int(longs(y2010)["has_series"].sum()), len(longs(y2010))
    )

    # 第 6 頁
    out["長片系列化比例（全期）"] = safe_div(int(L["has_series"].sum()), len(L))
    out["對決佔頻道觀看"] = safe_div(
        int(df.loc[df["series_name"] == "Joe是要對決", "view_count"].sum()),
        int(df["view_count"].sum()),
    )
    # ⚠️ 用 safe_div 不用直接相除：這支程式跑在每日自動更新的關鍵路徑上，
    #    它一 crash 整個工作流程就失敗、當天的資料不會提交。分母為零時要回傳 None
    #    讓比對照常進行（兩邊都是 None 即為通過），不能讓 ZeroDivisionError 擋住更新。
    new_long = L[L["series_gen"] == "新系列"]
    out["新系列中位觀看"] = m_median(new_long, 0)
    out["新系列業配率"] = safe_div(int(new_long["is_sponsored"].sum()), len(new_long))
    fe_new = df[df["is_first_ep"] & (df["series_gen"] == "新系列")]
    out["首集就有業配"] = f"{int(fe_new['is_sponsored'].sum())} / {len(fe_new)}"
    out["首次業配集數_開箱去"] = m_series_first_sponsor_ep(df, "Joeman開箱去")
    out["首次業配集數_看房"] = m_series_first_sponsor_ep(df, "Joe是要看房")
    out["首次業配出現在第幾集_列數"] = m_first_sponsor_chart_rows(df)

    # 第 4 頁偵測缺口
    for f in ("長片", "Shorts"):
        g = df[df["video_format"] == f]
        det = m_sponsored(g, 0)
        est = round(det / RECALL[f])
        out[f"偵測業配數_{f}"] = det
        out[f"推估實際業配_{f}"] = int(est)
        out[f"沒測到的業配數_{f}"] = int(est - det)

    # 系列世代分佈與首集標記
    for k, v in df["series_gen"].value_counts().items():
        out[f"系列世代_{k}"] = int(v)
    out["是首集標記數"] = int(df["is_first_ep"].sum())

    # 文字量值
    out["動態資料規模"] = (
        f"分析母體 {len(df):,} 支影片｜"
        f"{df['published_date'].dt.year.min()}–{df['published_date'].dt.year.max()}｜每日自動更新"
    )
    mx = df["published_date"].max()
    out["最新年度提醒"] = f"{mx.year} 年僅 1–{mx.month} 月"
    return out


# ── D. DAX 靜態引用檢查 ─────────────────────────────────────
# Power BI 資料表名 → 產生它的 CSV（無檔案者為 DATATABLE，欄位在此列出）
TABLE_SOURCE = {
    "fact_影片": "fact_video.csv",
    "fact_每日快照": "fact_daily.csv",
    "dim_日期": "dim_date.csv",
    "dim_影片": "dim_video.csv",
    "dim_影片格式": "dim_format.csv",
    "dim_類型": "dim_category.csv",
    "dim_系列": "dim_series.csv",
    "dim_業配判定": "dim_sponsor.csv",
}
DATATABLE_COLUMNS = {
    "門檻": {"門檻標籤", "門檻值", "排序"},
    "資料範圍": {"範圍", "排序"},
}


def dax_reference_check():
    text = DAX_DOC.read_text(encoding="utf-8")
    blocks = re.findall(r"```dax\n(.*?)```", text, flags=re.S)
    print(f"  DAX 區塊 {len(blocks)} 個")

    known = {t: set(read_star(f).columns) for t, f in TABLE_SOURCE.items()}
    known.update(DATATABLE_COLUMNS)

    refs = set()
    for b in blocks:
        # 只在 DATATABLE 定義裡出現的欄位宣告不是引用，跳過整個區塊
        if "DATATABLE" in b:
            continue
        refs |= set(re.findall(r"'([^']+)'\s*\[([^\]]+)\]", b))

    bad = []
    for table, col in sorted(refs):
        if table not in known:
            bad.append(f"未知的資料表 '{table}'（引用了 [{col}]）")
        elif col not in known[table]:
            bad.append(f"'{table}' 沒有資料行 [{col}]")
    print(f"  檢出 '資料表'[資料行] 引用 {len(refs)} 種")
    for t in sorted({t for t, _ in refs}):
        cols = sorted(c for tt, c in refs if tt == t)
        print(f"    {t}：{'、'.join(cols)}")
    if bad:
        for b in bad:
            print(f"  ✗ {b}")
            _failures.append(b)
    else:
        print("  ✓ 全部引用都對得到實際的資料行")


# ── 主流程 ──────────────────────────────────────────────────
def main():
    if not (STAR_DIR / "fact_video.csv").exists():
        sys.exit("[錯誤] 找不到星狀模型輸出，請先執行 python -m src.build_star")

    print("\n=== A. 結構等價：星狀模型還原成寬表 vs 原始 videos.csv ===")
    old, new = load_old(), load_new()
    check("列數", len(old), len(new))
    check("影片 ID 集合", set(old["video_id"]) == set(new["video_id"]), True)
    for col in CANON:
        if col in ("series_gen", "is_first_ep"):
            continue                      # 這兩欄留給 B 段單獨驗
        if col in ("like_count", "comment_count"):
            same = old[col].fillna(-1).equals(new[col].fillna(-1))
        else:
            same = old[col].equals(new[col])
        check(f"欄位 {col} 逐列相同", True, bool(same))

    print("\n=== B. 衍生欄位：舊 DAX 計算欄位定義 vs 維度表實際值 ===")
    check("系列世代 逐列相同", True, bool(old["series_gen"].equals(new["series_gen"])))
    check("是首集 逐列相同", True, bool(old["is_first_ep"].equals(new["is_first_ep"])))
    for k, v in old["series_gen"].value_counts().items():
        check(f"系列世代 {k} 筆數", int(v), int((new["series_gen"] == k).sum()))
    check("是首集 標記數", int(old["is_first_ep"].sum()), int(new["is_first_ep"].sum()))

    print("\n=== C. 量值等價：舊扁平表 vs 新星狀表 ===")
    mo, mn = metrics(old, "舊"), metrics(new, "新")
    for k in mo:
        check(k, mo[k], mn.get(k))

    print("\n=== D. DAX 靜態引用檢查（DAX_星狀模型.md） ===")
    dax_reference_check()

    print("\n" + "=" * 62)
    if _failures:
        print(f"🔴 {len(_failures)} 項未通過：")
        for f in _failures:
            print(f"   - {f}")
        sys.exit(1)
    print("✅ 全部通過：星狀模型與改寫後的 DAX 與舊模型完全等價。")
    print("   （C 段印出的數字即為 DAX_星狀模型.md 第 8-2 節的抽查期望值）")


if __name__ == "__main__":
    main()
