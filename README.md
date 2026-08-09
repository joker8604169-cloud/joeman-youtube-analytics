# Joeman 頻道數據分析

透過 YouTube Data API v3 抓取 [Joeman 頻道](https://www.youtube.com/@joeman)
（`UCPRWWKG0VkBA0Pqa4Jr5j0Q`）的全部公開影片，經清洗與分類後以 Power BI 呈現，
並以 GitHub Actions **每日自動更新**。

## 分析主軸

| # | 主題 | 核心問題 |
|---|---|---|
| 1 | 產能結構 | 產能如何在長片與 Shorts 之間移轉？ |
| 2 | 投放規模 | 廠商的投放有沒有減少？ |
| 3 | 投放偏好 | 廠商的錢集中在哪些內容？ |
| 4 | 接棒動能 | 招牌 IP 停播後，新系列接得住嗎？ |

## 專案結構

```
├── config.py                共用設定（頻道 ID、路徑）
├── requirements.txt
├── .env                     API 金鑰（不入版控）
├── .github/workflows/       每日排程
├── src/
│   ├── fetch.py             抓取 API 並落地原始 JSON
│   ├── transform.py         清洗、分類、輸出 CSV
│   ├── build_star.py        資料清洗 ＋ 拆分事實表／維度表
│   ├── verify_star.py       驗收：確認新舊模型數字等價
│   ├── sponsor_sample.py    業配判定抽樣（分層）
│   ├── sponsor_sample_recall.py  漏抓率抽樣（純隨機）
│   ├── sponsor_evaluate.py  準確度評估（含信賴區間）
│   └── make_background.py   產生 Power BI 背景圖
├── rules/                   8 個可編輯的規則表（含抽樣驗證常數）
├── assets/                  報表背景圖與座標表
└── data/
    ├── raw/                 API 原始 JSON（不入版控，每日重抓）
    └── processed/           分析用 CSV
        └── star/            Power BI 星狀模型（事實表／維度表）
```

## 資料輸出

### 分析寬表（`data/processed/`）

| 檔案 | 內容 |
|---|---|
| `videos.csv` | 影片主檔，24 欄。**抽樣驗證程式的輸入** |
| `daily_stats.csv` | 每日觀看／按讚／留言快照 |
| `data_notes.csv` | 資料特徵與判準（供報表呈現） |
| `limitations.csv` | 本報告的限制清單 |
| `review_queue.csv` | **需人工檢視的項目**（新系列、未分類、業配臨界） |
| `excluded_videos.csv` | 被排除的影片與原因 |

### 星狀模型（`data/processed/star/`）— **Power BI 讀這一份**

| 檔案 | 種類 | 粒度 |
|---|---|---|
| `fact_video.csv` | 事實 | 一支影片一列 |
| `fact_daily.csv` | 事實 | 一天一支影片一列 |
| `dim_date.csv` | 維度 | 連續日曆 |
| `dim_video.csv` | 維度 | 影片屬性與查核欄位 |
| `dim_format.csv` | 維度 | 長片／Shorts，**含各格式的召回率與精確率** |
| `dim_category.csv` | 維度 | 14 個影片類型 |
| `dim_series.csv` | 維度 | 29 個系列 ＋「（無系列）」，含系列世代 |
| `dim_sponsor.csv` | 維度 | 業配判定結果 × 判定來源 |
| `data_quality.csv` | 稽核 | 16 項資料清洗檢查結果 |

模型設計、清洗規則、關聯設定見 **`STAR_SCHEMA.md`**；
量值改寫見 **`DAX_星狀模型.md`**。

## 方法與驗證

- **Shorts 判定**：以 `youtube.com/shorts/` 網址實測，非依時長推斷
- **業配判定**：關鍵字加權計分，先剔除說明欄罐頭文字與自營商品導流
- **準確度**：以**純隨機抽樣**分格式驗證，長片與 Shorts 各抽 40 支人工核對
  - 長片：精確率 22/22、**召回率 76.3%**（95% 信賴區間 69.1–83.6%）
  - Shorts：**召回率僅 14.6%**（85.7% 說明欄空白），**故商業分析排除 Shorts**
  - ⚠️ 召回率必須**分格式量測**，兩者的可用文字量差十倍，不可互相沿用

規則表皆為 CSV，修改後重跑 `python -m src.transform` 即可全量重新分類。

## 本機執行

```bash
pip install -r requirements.txt
# 複製 .env.example 為 .env 並填入 API 金鑰
python -m src.fetch
python -m src.transform
python -m src.build_star     # 資料清洗與星狀重建
python -m src.verify_star    # 驗收（新舊模型逐項比對，不符即失敗）
```

只調整模型時**不要跑 `transform`**——本機 `data/raw` 會過期，重跑會讓資料回滾。
直接跑 `build_star` 與 `verify_star` 即可。

## 雲端自動更新

GitHub Actions 每日台灣時間 06:00 執行，抓取最新資料、重新分類後自動提交。
API 金鑰存於 GitHub Secrets（`YT_API_KEY`），不會出現在程式碼中。
