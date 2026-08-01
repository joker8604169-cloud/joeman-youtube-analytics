# -*- coding: utf-8 -*-
"""
產生 Power BI 報表背景 PNG（1920×1080，YouTube 深色介面風格）

背景圖只負責「容器與靜態文字」：
  ✓ 畫布底色、頂部列、左側導覽軌
  ✓ 卡片外框、標題列、紅色標記、區塊標題
  ✓ 篩選器列容器與分組標籤
  ✗ 不含 KPI 數值、圖表內容、篩選器按鈕 —— 這些由 Power BI 視覺物件疊在上方

同時輸出座標對照表 background_layout.csv，供在 Power BI 中精準對齊。

執行方式：
  python -m src.make_background
"""
import csv
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

W, H = 1920, 1080
OUT_DIR = config.PROJECT_ROOT / "assets"

# YouTube 深色模式取色
BG = "#0F0F0F"
CARD = "#1F1F1F"
CARD_HEAD = "#272727"
BAR = "#181818"
LINE = "#272727"
LINE2 = "#303030"
RED = "#FF0000"
GREEN = "#2BA640"
TXT = "#F1F1F1"
TXT2 = "#AAAAAA"
TXT3 = "#717171"
SEARCH_BG = "#121212"

FONT_REG = "C:/Windows/Fonts/msjh.ttc"
FONT_BOLD = "C:/Windows/Fonts/msjhbd.ttc"

# 版面座標（1920×1080）
TOPBAR_H = 84
RAIL_W = 158
PAD = 34
CONTENT_X = RAIL_W + PAD          # 192
CONTENT_R = W - PAD               # 1886
GAP = 22

# 版面高度分配（總計 84 頂列 + 24 內距 + 118 + 26 + 624 + 26 + 130 = 1032，
# 底部留 48px 邊界）。側圖卡片加高至 301，內容區約 205px，
# 讓 6~7 列的橫條圖每列有 29px 以上，標籤與數值才不會擠在一起。
KPI_Y, KPI_H = 108, 118
KPI_W = (CONTENT_R - CONTENT_X - GAP * 3) // 4      # 407

ZONE_Y = 252
MAIN_W = 836
MAIN_H = 624
SIDE_X = CONTENT_X + MAIN_W + GAP                    # 1050
SIDE_H = (MAIN_H - GAP) // 2                         # 301

SLICER_Y, SLICER_H = 902, 130

TITLE_BAR_H = 76          # 卡片標題列高度
CONTENT_TOP = 86          # 卡片內容區距卡片頂端的偏移
CONTENT_TRIM = 96         # 內容區高度 = 卡片高 - 此值

TITLE = "Joeman 頻道數據分析"
# 副標題刻意不寫入具體支數或年數：資料每日更新，寫死的數字幾天後就過期。
# 動態規模由 Power BI 的 DAX 量值呈現。
SUBTITLE = "解構一個頻道的產能結構、廠商投放與 IP 接棒"
# 導覽軌只標示四個分析步驟；封面與方法頁不佔軌位（active_rail = -1）
RAIL_ITEMS = [("1", "產能"), ("2", "投放"), ("3", "偏好"), ("4", "接棒")]

# slicers 以 (分組名稱, 容器寬度) 表示；空清單代表該頁無篩選器，
# 此時圖表區會往下延伸佔用篩選器列的空間。
PAGES = {
    1: {"layout": "cover"},
    2: {"layout": "table", "main": "資料特徵與判準"},
    3: {
        "active_rail": 0,
        "kpi": ["影片總數", "長片數", "Shorts 數", "長片中位觀看"],
        "main": "產能結構：長片與 Shorts 的消長",
        "side_top": "長片中位觀看（2023 年前可比）",
        "side_bottom": "內容類型組成變遷",
        "slicers": [("影片格式", 380), ("觀看數門檻", 690), ("年份區間", 420)],
    },
    4: {
        "active_rail": 1,
        "kpi": ["長片業配率", "偵測業配數（下限）", "推估實際業配", "判定精確率"],
        "main": "業配影片數與業配率",
        "side_top": "業配與非業配的觀看對照",
        "side_bottom": "業配影片的類型組成",
        # 格式篩選器在此頁是論證工具：切換即可展示整體數字被 Shorts 污染
        "slicers": [("資料範圍", 720), ("年份區間", 480)],
    },
    5: {
        "active_rail": 2,
        "kpi": ["業配率最高類型", "業配率最低類型", "類型間差距", "樣本數（長片）"],
        "main": "各類型的業配率",
        "side_top": "廠商進場時序",
        "side_bottom": "系列的業配密度排序",
        "slicers": [("系列", 780), ("觀看數門檻", 780)],
    },
    6: {
        "active_rail": 3,
        "kpi": ["《對決》佔頻道觀看", "長片系列化比例", "新系列中位觀看", "新系列業配率"],
        "main": "系列 IP 的規模與效率",
        "side_top": "長片系列化比例趨勢",
        "side_bottom": "新系列動能對照（2025 年起）",
        "slicers": [("年份區間", 600), ("觀看數門檻", 960)],
    },
    # 限制頁不列入 10 分鐘主流程，作為 Q&A 備用
    7: {"layout": "table", "main": "本報告的限制"},
}


def font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size, index=0)


def card(d, x, y, w, h, title=None):
    """卡片容器；有 title 時加上標題列與紅色標記"""
    d.rounded_rectangle([x, y, x + w, y + h], radius=17, fill=CARD)
    if title:
        d.rounded_rectangle([x, y, x + w, y + TITLE_BAR_H], radius=17, fill=CARD_HEAD)
        d.rectangle([x, y + TITLE_BAR_H - 17, x + w, y + TITLE_BAR_H], fill=CARD_HEAD)
        d.rectangle([x + 34, y + 24, x + 41, y + 54], fill=RED)
        d.text((x + 56, y + 21), title, font=font(30, True), fill=TXT)


def finish(img, page_no, rows):
    """存檔 PNG 與座標表"""
    OUT_DIR.mkdir(exist_ok=True)
    png = OUT_DIR / f"bg_page{page_no}.png"
    img.save(png, "PNG")
    print(f"已產生 {png}（{W}×{H}）")

    csv_path = OUT_DIR / f"layout_page{page_no}.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["區塊", "X", "Y", "寬", "高"])
        w.writeheader()
        w.writerows(rows)
    print(f"已產生 {csv_path}（{len(rows)} 個擺放區）")
    return rows


def draw_cover(d, log):
    """封面：文字資訊靠左，播放鍵視覺靠右，不放頂部列與導覽軌"""
    d.text((140, 318), TITLE, font=font(84, True), fill=TXT)
    d.text((142, 452), SUBTITLE, font=font(38), fill=TXT2)
    d.rectangle([140, 552, 420, 558], fill=RED)

    # 供 Power BI 文字方塊擺放的三個區域
    log("封面｜報告人姓名（文字方塊，34px，#F1F1F1）", 140, 610, 620, 62)
    log("封面｜日期／課程（文字方塊，26px，#AAAAAA）", 140, 680, 620, 46)
    log("封面｜動態資料規模（DAX 量值，24px，#717171）", 140, 740, 780, 46)

    # 播放鍵視覺：外圈兩道細環增加層次，內為紅色圓角矩形加白色三角
    cx, cy = 1480, 540
    d.ellipse([cx - 330, cy - 330, cx + 330, cy + 330], outline=CARD, width=3)
    d.ellipse([cx - 288, cy - 288, cx + 288, cy + 288], outline=CARD_HEAD, width=4)
    d.rounded_rectangle([cx - 240, cy - 165, cx + 240, cy + 165], radius=74, fill=RED)
    d.polygon([(cx - 50, cy - 82), (cx + 80, cy), (cx - 50, cy + 82)], fill="#FFFFFF")


def build(page_no, spec):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    rows = []

    def log(name, x, y, w, h):
        rows.append({"區塊": name, "X": x, "Y": y, "寬": w, "高": h})

    layout = spec.get("layout", "standard")
    if layout == "cover":
        draw_cover(d, log)
        return finish(img, page_no, rows)

    # 頂部列
    d.line([(0, TOPBAR_H), (W, TOPBAR_H)], fill=LINE, width=2)
    d.rounded_rectangle([40, 26, 91, 63], radius=11, fill=RED)
    d.polygon([(59, 35), (74, 44), (59, 53)], fill="#FFFFFF")
    d.text((107, 28), TITLE, font=font(34, True), fill=TXT)
    d.rounded_rectangle([706, 20, 1214, 68], radius=24,
                        fill=SEARCH_BG, outline=LINE2, width=2)
    d.text((740, 32), "搜尋影片、系列、類型", font=font(26), fill=TXT3)
    d.rounded_rectangle([1581, 23, 1880, 65], radius=21, fill=CARD)
    d.ellipse([1608, 37, 1622, 51], fill=GREEN)
    d.text((1632, 32), "每日自動更新", font=font(24), fill=TXT2)

    # 圖表區高度：無篩選器的頁面往下延伸，佔用篩選器列的空間
    has_slicer = bool(spec.get("slicers"))
    main_h = MAIN_H if has_slicer else MAIN_H + GAP + SLICER_H + GAP
    side_h = (main_h - GAP) // 2

    # 左側導覽軌
    d.line([(RAIL_W, TOPBAR_H), (RAIL_W, H)], fill=LINE, width=2)
    for i, (num, label) in enumerate(RAIL_ITEMS):
        y = 119 + i * 152
        active = i == spec.get("active_rail", -1)
        if active:
            d.rounded_rectangle([17, y, 141, y + 96], radius=17, fill=CARD_HEAD)
            d.rectangle([17, y, 24, y + 96], fill=RED)
        col = TXT if active else TXT3
        d.text((79, y + 18), num, font=font(31, True), fill=col, anchor="ma")
        d.text((79, y + 56), label, font=font(26), fill=col, anchor="ma")

    # 表格頁：單張全幅卡片，不放 KPI 列與側圖
    if layout == "table":
        table_h = 1032 - ZONE_Y
        card(d, CONTENT_X, ZONE_Y, CONTENT_R - CONTENT_X, table_h, spec["main"])
        log(f"表格｜{spec['main']}", CONTENT_X + 20, ZONE_Y + CONTENT_TOP,
            CONTENT_R - CONTENT_X - 40, table_h - CONTENT_TRIM)
        return finish(img, page_no, rows)

    # KPI 卡片：只畫容器與標籤，數值由 Power BI 卡片視覺提供
    for i, label in enumerate(spec["kpi"]):
        x = CONTENT_X + i * (KPI_W + GAP)
        card(d, x, KPI_Y, KPI_W, KPI_H)
        d.text((x + 34, KPI_Y + 22), label, font=font(27), fill=TXT2)
        log(f"KPI{i + 1}｜{label}（數值區）", x + 34, KPI_Y + 58, KPI_W - 68, 48)

    # 三個圖表區
    card(d, CONTENT_X, ZONE_Y, MAIN_W, main_h, spec["main"])
    log(f"主圖表｜{spec['main']}", CONTENT_X + 20, ZONE_Y + CONTENT_TOP,
        MAIN_W - 40, main_h - CONTENT_TRIM)

    card(d, SIDE_X, ZONE_Y, MAIN_W, side_h, spec["side_top"])
    log(f"右上圖｜{spec['side_top']}", SIDE_X + 20, ZONE_Y + CONTENT_TOP,
        MAIN_W - 40, side_h - CONTENT_TRIM)

    y2 = ZONE_Y + side_h + GAP
    card(d, SIDE_X, y2, MAIN_W, side_h, spec["side_bottom"])
    log(f"右下圖｜{spec['side_bottom']}", SIDE_X + 20, y2 + CONTENT_TOP,
        MAIN_W - 40, side_h - CONTENT_TRIM)

    # 篩選器列：畫容器與分組標籤，按鈕由 Power BI 交叉分析篩選器提供
    if has_slicer:
        d.rounded_rectangle([CONTENT_X, SLICER_Y, CONTENT_R, SLICER_Y + SLICER_H],
                            radius=17, fill=BAR, outline=LINE, width=2)
        groups = spec["slicers"]
        gx = CONTENT_X + 40
        for i, (name, gw) in enumerate(groups):
            d.text((gx, SLICER_Y + 20), name, font=font(27), fill=TXT3)
            log(f"篩選器｜{name}", gx, SLICER_Y + 62, gw, 56)
            gx += gw
            if i < len(groups) - 1:
                d.line([(gx + 20, SLICER_Y + 26),
                        (gx + 20, SLICER_Y + SLICER_H - 26)],
                       fill=LINE2, width=2)
                gx += 60

    return finish(img, page_no, rows)


def main():
    # 可指定頁碼只產生特定頁面，例如：python -m src.make_background 2
    targets = [int(a) for a in sys.argv[1:]] or list(PAGES)
    for page_no in targets:
        rows = build(page_no, PAGES[page_no])
        print(f"\n=== 第 {page_no} 頁視覺物件擺放座標 ===")
        for r in rows:
            print(f"  {r['區塊']:<28} X={r['X']:>4} Y={r['Y']:>4} "
                  f"寬={r['寬']:>4} 高={r['高']:>3}")


if __name__ == "__main__":
    main()
