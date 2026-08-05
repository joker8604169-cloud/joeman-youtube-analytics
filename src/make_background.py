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

# 導覽軌起點：六格各高 96、間隔 152，總跨距 856，
# 於頂部列以下（84–1080，共 996px）垂直置中 → 84 + (996-856)//2
RAIL_TOP = 154

TITLE_BAR_H = 76          # 卡片標題列高度
CONTENT_TOP = 86          # 卡片內容區距卡片頂端的偏移
CONTENT_TRIM = 96         # 內容區高度 = 卡片高 - 此值

TITLE = "Joeman 頻道數據分析"
# 副標題刻意不寫入具體支數或年數：資料每日更新，寫死的數字幾天後就過期。
# 動態規模由 Power BI 的 DAX 量值呈現。
SUBTITLE = "解構一個頻道的產能結構、廠商投放與 IP 接棒"
# 導覽軌涵蓋全部六個內容頁：資料 → 產能 → 投放 → 偏好 → 接棒 → 限制。
# 原本只列四個分析步驟，但第 2、7 頁同樣有導覽軌卻沒有任何一格會亮，
# 觀眾看不出自己在哪一頁。封面與結尾頁不畫導覽軌。
RAIL_ITEMS = [("1", "資料"), ("2", "產能"), ("3", "投放"),
              ("4", "偏好"), ("5", "接棒"), ("6", "限制")]

# slicers 以 (分組名稱, 容器寬度) 表示；空清單代表該頁無篩選器，
# 此時圖表區會往下延伸佔用篩選器列的空間。
PAGES = {
    1: {"layout": "cover"},
    2: {"layout": "table", "main": "資料特徵與判準", "active_rail": 0},
    3: {
        "active_rail": 1,
        "kpi": ["影片總數", "長片數", "Shorts 數", "長片中位觀看"],
        "main": "產能結構：長片與 Shorts 的消長",
        # 標題刻意不寫「2023 年前可比」：那是判斷不是事實（見 HANDOFF 第七章第 5 點，
        # 資料其實支持放寬到 2024），寫死在 PNG 裡會過期。虛線的意義改由卡片內的
        # 註記文字方塊「虛線：該年份影片仍在累積觀看」說明，該句不含年份、不會過期。
        "side_top": "長片中位觀看",
        "side_bottom": "內容類型組成變遷",
        # 年份區間用「介於」樣式，需要橫向空間放兩個輸入框＋滑桿，
        # 控制項會擺在標籤右側（見 BUILD.md 第 3 頁），所以這一格要留寬。
        "slicers": [("影片格式", 260), ("觀看數門檻", 480), ("年份區間", 754)],
    },
    4: {
        "active_rail": 2,
        # KPI 標題必須誠實反映「資料範圍」切換後的狀態（2026-08-04 修正）：
        #   KPI1「長片業配率」→「業配率」：它會隨切換變成 15.4%（含 Shorts），
        #     標題寫死「長片」會與內容矛盾；母體由下方的資料範圍膠囊揭露。
        #   KPI3、KPI4 加「（長片）」：兩者都**不隨切換變動**——推估值用長片召回率
        #     85.3% 回推（Shorts 只有 14.7%，套用會荒謬），精確率是長片抽樣的固定值。
        #
        # 2026-08-05：KPI2 也鎖長片並加標。原本它跟著資料範圍浮動（切「全部」變 397，
        # 長片 370 ＋ Shorts 27），與 KPI3 的 434（純長片回推）並排會被讀成
        # 「偵測 397、推估 434，漏 37 支」——那個 37 是假的。
        # KPI2 與 KPI3 是一對（下限 vs 推估），必須同母體；而 KPI3 只能是長片。
        # 鎖住之後四格裡只有 KPI1 會隨切換變動，那正是這頁要演的東西，
        # 且台詞更利：「業配率掉了，但偵測到的業配數一支沒變——變的是分母」。
        "kpi": ["業配率", "偵測業配數（長片，下限）", "推估實際業配（長片）", "判定精確率（長片）"],
        "main": "業配影片數與業配率",
        # 標題明示統計量與單位：全報表一律用中位數（第七章第 6 點），
        # 且第 3 頁側上與第 6 頁 KPI 都標了「中位」，這頁不標會不一致；
        # 「業配影片數」的「數」字則讓橫條上的 224／118 讀得出是支數不是比例。
        # 兩張側圖固定在長片（視覺層級篩選 video_format = 長片），不隨資料範圍切換，
        # 所以一併標「（長片）」——切到「全部」時整頁只剩它們維持長片口徑。
        # 側上圖於 2026-08-05 換題：原本畫「同系列內業配與非業配的中位觀看」，
        # 那張圖回答的是「廠商挑影片的顆粒度」，屬於第 5 頁（投放偏好）的主題，
        # 在這頁（投放規模）沒有工作。改畫偵測缺口——這頁最核心的宣稱
        # 「整體業配率下降是偵測失效造成的」原本只有口白沒有視覺，
        # 而它同時是全報表「商業分析一律排除 Shorts」的唯一依據。
        "side_top": "業配偵測缺口（長片與 Shorts）",
        # 圖例印在標題列右側：Power BI 的圖例會吃掉視覺物件頂端約 30px，
        # 印在背景則零佔位，且截圖備援也帶得走。
        "side_top_legend": [("測到", "#A0A0A0"), ("沒測到", "#5C5C5C")],
        "side_bottom": "業配影片數的類型組成（長片）",
        # 資料範圍在此頁是論證工具：切換即可展示整體數字被 Shorts 污染。
        # 系列篩選器於 2026-08-04 為側上圖的演出而加，2026-08-05 又移除：
        # 改用方案 B 之後圖表預設就攤開四組對照，篩選器沒有工作了——
        # 四個合格系列本來就全在畫面上，點下去只是放大；而點其他 25 個系列
        # 會讓 [配對中位觀看] 的 30 支門檻全數回傳 BLANK，**整張圖空白**。
        # 留一個上台誤觸就開天窗的控制項，換不到任何東西。
        # 「你只挑對你有利的系列」這個質疑用口白回答即可（另兩個系列是
        # −16% 與 −10%，方向一致，只是業配各只有 8 支）。
        # 年份區間需 754px 以上：「介於」樣式的控制項要擺在標籤右側（第 3 頁實測 610px）。
        "slicers": [("資料範圍", 720), ("年份區間", 834)],
    },
    5: {
        "active_rail": 3,
        "kpi": ["業配率最高類型", "業配率最低類型", "類型間差距", "樣本數（長片）"],
        "main": "各類型的業配率",
        "side_top": "廠商進場時序",
        "side_bottom": "系列的業配密度排序",
        # 類型篩選器用於「逐項加入」的演出：現場一個一個加類型，
        # 讓觀眾自己看出遊戲實況／電競賽事的接近零是年代效應而非廠商偏好。
        "slicers": [("系列", 480), ("類型", 480), ("觀看數門檻", 534)],
    },
    6: {
        "active_rail": 4,
        # KPI4 與側下圖於 2026-08-02 更換（方案丁）：
        # 原本的「新系列業配率 73.7%」與「新系列動能對照」都是比率比較，
        # 自助抽樣 95% 信賴區間跨越零，照本專案標準不能下結論；
        # 改成「首集就有業配」是二元類別觀察（新系列 4/4 vs 既有 2/22），
        # 不需要信賴區間就成立，且與第 5 頁的「廠商進場時序」前後呼應。
        "kpi": ["《對決》佔頻道觀看", "長片系列化比例", "新系列中位觀看", "首集就有業配"],
        "main": "系列 IP 的規模與效率",
        "side_top": "長片系列化比例趨勢",
        "side_bottom": "首次業配出現在第幾集",
        # 年份區間放最左且加寬：「介於」樣式的控制項要擺在標籤右側，
        # 需要約 610–690px（第 3、4 頁實測）。
        "slicers": [("年份區間", 830), ("觀看數門檻", 664)],
    },
    # 限制頁不列入 10 分鐘主流程，作為 Q&A 備用
    7: {"layout": "table", "main": "本報告的限制", "active_rail": 5},
    8: {"layout": "ending"},
}


def font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size, index=0)


def card(d, x, y, w, h, title=None, legend=None):
    """卡片容器；有 title 時加上標題列與紅色標記

    legend：[(標籤, 色碼), ...]，畫在標題列右側、由上而下堆疊。

    這是給「圖例必須關掉才放得下長條」的側圖用的靜態圖例。側圖內容區只有
    205px，Power BI 的圖例會吃掉頂端約 30px，長條被壓到 13px 就開始掉資料標籤。
    印在背景裡則不佔視覺物件任何像素，而且截圖備援也帶得走。
    **由上而下的排列刻意對應群組長條由上而下的序列順序**，讀者不必比對顏色。
    """
    d.rounded_rectangle([x, y, x + w, y + h], radius=17, fill=CARD)
    if title:
        d.rounded_rectangle([x, y, x + w, y + TITLE_BAR_H], radius=17, fill=CARD_HEAD)
        d.rectangle([x, y + TITLE_BAR_H - 17, x + w, y + TITLE_BAR_H], fill=CARD_HEAD)
        d.rectangle([x + 34, y + 24, x + 41, y + 54], fill=RED)
        d.text((x + 56, y + 21), title, font=font(30, True), fill=TXT)
    if legend:
        f = font(20)
        sw, gap, lh = 14, 8, 26
        block_w = sw + gap + max(f.getbbox(t)[2] for t, _ in legend)
        lx = x + w - 34 - block_w
        top = y + (TITLE_BAR_H - lh * len(legend)) // 2
        # 與標題文字撞在一起會靜默疊字，寧可中止讓人回來縮短標題
        if title:
            title_r = x + 56 + font(30, True).getbbox(title)[2]
            if lx < title_r + 24:
                raise ValueError(
                    f"標題與圖例重疊：標題右緣 {title_r}、圖例左緣 {lx}，請縮短標題"
                )
        for i, (label, color) in enumerate(legend):
            ly = top + lh * i
            d.rectangle([lx, ly + (lh - sw) // 2, lx + sw, ly + (lh + sw) // 2], fill=color)
            d.text((lx + sw + gap, ly + 1), label, font=f, fill=TXT2)


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


def draw_ending(d, log):
    """結尾頁：置中收尾，與封面成對。不放頂部列與導覽軌

    構圖刻意與封面相反——封面是文字靠左、視覺靠右的展開式，
    結尾是垂直置中的收攏式，讀起來像句號。
    """
    cx = W // 2

    # 播放鍵標記：與封面同一個符號但縮小，作為前後呼應
    d.rounded_rectangle([cx - 80, 320, cx + 80, 430], radius=26, fill=RED)
    d.polygon([(cx - 20, 348), (cx + 28, 375), (cx - 20, 402)], fill="#FFFFFF")

    d.text((cx, 490), "謝謝聆聽", font=font(96, True), fill=TXT, anchor="ma")
    d.rectangle([cx - 120, 645, cx + 120, 651], fill=RED)

    # 刻意不放網址或任何數字：結尾頁的作用是收束，多一行字就多一個
    # 讓觀眾分心去讀的東西。儲存庫要展示的話在 Q&A 直接開瀏覽器更有力。
    # 需要在結尾再報一次規模時，可放 [動態資料規模]；不放也成立
    log("結尾｜動態資料規模（DAX 量值，24px，#717171，選用）", cx - 390, 700, 780, 46)


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
    if layout == "ending":
        draw_ending(d, log)
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
        y = RAIL_TOP + i * 152
        active = i == spec.get("active_rail", -1)
        if active:
            d.rounded_rectangle([17, y, 141, y + 96], radius=17, fill=CARD_HEAD)
            d.rectangle([17, y, 24, y + 96], fill=RED)
        col = TXT if active else TXT3
        d.text((79, y + 18), num, font=font(31, True), fill=col, anchor="ma")
        d.text((79, y + 56), label, font=font(26), fill=col, anchor="ma")

    # 表格頁：單張全幅卡片，不放 KPI 列與側圖
    if layout == "table":
        # 卡片高度沿用標準內容區（252→1032），但表格頁沒有 KPI 列，
        # 若同樣從 ZONE_Y 起會上方空 168px、下方只剩 48px，整張卡片偏低。
        # 改為在頂部列以下垂直置中，上下留白各 108px。
        table_h = 1032 - ZONE_Y
        table_y = TOPBAR_H + (H - TOPBAR_H - table_h) // 2
        card(d, CONTENT_X, table_y, CONTENT_R - CONTENT_X, table_h, spec["main"])
        log(f"表格｜{spec['main']}", CONTENT_X + 20, table_y + CONTENT_TOP,
            CONTENT_R - CONTENT_X - 40, table_h - CONTENT_TRIM)
        return finish(img, page_no, rows)

    # KPI 卡片：只畫容器與標籤，數值由 Power BI 卡片視覺提供
    for i, label in enumerate(spec["kpi"]):
        x = CONTENT_X + i * (KPI_W + GAP)
        card(d, x, KPI_Y, KPI_W, KPI_H)
        # 標籤 21px（畫在 KPI_Y+16），數值區 64px 高。
        # 數值區必須 ≥ 64px，Power BI 才放得下 33pt（＝44px，行高 60px）的數字；
        # 舊版標籤 27px、數值區僅 48px，數值最大只能到 26pt，KPI 會弱得像內文。
        #
        # 數值區的 y 偏移 37 是在 Power BI 裡目視微調出來的，不是幾何計算值：
        # 舊版「卡片」視覺會把數值**垂直置中**於自己的框內，而背景圖卡片的內距
        # 上下不對稱（上 16、下 7），照幾何置中會看起來偏下。往上收到 +37 才平衡。
        # 改這個數字要重新在 Power BI 裡目視確認，不能只靠算的。
        d.text((x + 34, KPI_Y + 16), label, font=font(21), fill=TXT2)
        log(f"KPI{i + 1}｜{label}（數值區）", x + 34, KPI_Y + 37, KPI_W - 68, 64)

    # 三個圖表區
    card(d, CONTENT_X, ZONE_Y, MAIN_W, main_h, spec["main"])
    log(f"主圖表｜{spec['main']}", CONTENT_X + 20, ZONE_Y + CONTENT_TOP,
        MAIN_W - 40, main_h - CONTENT_TRIM)

    card(d, SIDE_X, ZONE_Y, MAIN_W, side_h, spec["side_top"],
         legend=spec.get("side_top_legend"))
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
