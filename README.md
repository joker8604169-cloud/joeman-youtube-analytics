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
│   ├── sponsor_sample.py    業配判定抽樣（分層）
│   ├── sponsor_sample_recall.py  漏抓率抽樣（純隨機）
│   ├── sponsor_evaluate.py  準確度評估（含信賴區間）
│   └── make_background.py   產生 Power BI 背景圖
├── rules/                   8 個可編輯的規則表
├── assets/                  報表背景圖與座標表
└── data/
    ├── raw/                 API 原始 JSON（不入版控，每日重抓）
    └── processed/           分析用 CSV
```

## 資料輸出

| 檔案 | 內容 |
|---|---|
| `videos.csv` | 影片主檔，24 欄 |
| `daily_stats.csv` | 每日觀看／按讚／留言快照 |
| `data_notes.csv` | 資料特徵與判準（供報表呈現） |
| `limitations.csv` | 本報告的限制清單 |
| `review_queue.csv` | **需人工檢視的項目**（新系列、未分類、業配臨界） |
| `excluded_videos.csv` | 被排除的影片與原因 |

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
```

## 雲端自動更新

GitHub Actions 每日台灣時間 06:00 執行，抓取最新資料、重新分類後自動提交。
API 金鑰存於 GitHub Secrets（`YT_API_KEY`），不會出現在程式碼中。
