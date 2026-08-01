# -*- coding: utf-8 -*-
"""
階段一：全量擷取 Joeman 頻道資料

流程：
  1. 以 handle 解析頻道，核對 channel ID（不符即中止）
  2. 從「上傳」播放清單分頁取得全部影片 ID
  3. 以每批 50 支呼叫 videos.list 取得完整影片資料
  4. 抓取頻道全部自建播放清單與其收錄影片（供系列判定交叉比對）
  5. 原始 JSON 落地保存至 data/raw/，並輸出摘要與配額消耗

執行方式（於專案根目錄）：
  python -m src.fetch
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

# Windows 主控台預設編碼可能非 UTF-8，避免中文輸出報錯
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

quota_used = 0  # 每次 list 呼叫 = 1 unit，累計供結尾回報


def get_client():
    import os
    load_dotenv(config.PROJECT_ROOT / ".env")
    api_key = os.getenv(config.API_KEY_ENV_NAME)
    if not api_key:
        sys.exit(
            f"[錯誤] 找不到 API 金鑰。請在專案根目錄建立 .env，內容："
            f"{config.API_KEY_ENV_NAME}=你的金鑰"
        )
    if not api_key.isascii() or "金鑰" in api_key:
        sys.exit(
            "[錯誤] .env 裡的金鑰仍是範本文字，尚未換成真正的 API 金鑰。\n"
            "請用記事本打開 .env，把等號後面整段換成你在 Google Cloud "
            "取得的金鑰（英數字串，通常以 AIza 開頭）後存檔再執行。"
        )
    return build("youtube", "v3", developerKey=api_key)


def resolve_channel(yt):
    """以 handle 解析頻道並核對 ID，回傳頻道資源"""
    global quota_used
    resp = yt.channels().list(
        part="id,snippet,contentDetails,statistics",
        forHandle=config.CHANNEL_HANDLE,
    ).execute()
    quota_used += 1

    items = resp.get("items", [])
    if not items:
        sys.exit(f"[錯誤] 找不到 handle 為 @{config.CHANNEL_HANDLE} 的頻道")

    ch = items[0]
    if ch["id"] != config.EXPECTED_CHANNEL_ID:
        sys.exit(
            f"[錯誤] 頻道 ID 不符！API 回傳 {ch['id']}，"
            f"預期 {config.EXPECTED_CHANNEL_ID}，中止執行"
        )
    print(f"頻道確認：{ch['snippet']['title']}（{ch['id']}）")
    print(f"  訂閱數：{ch['statistics'].get('subscriberCount', 'N/A')}")
    print(f"  影片總數（API 回報）：{ch['statistics'].get('videoCount', 'N/A')}")
    return ch


def fetch_all_playlist_items(yt, playlist_id):
    """分頁取得指定播放清單的全部項目"""
    global quota_used
    items, page_token = [], None
    while True:
        resp = yt.playlistItems().list(
            part="contentDetails,snippet",
            playlistId=playlist_id,
            maxResults=config.PAGE_SIZE,
            pageToken=page_token,
        ).execute()
        quota_used += 1
        items.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            return items


def fetch_video_details(yt, video_ids):
    """以每批 50 支取得影片完整資料"""
    global quota_used
    videos = []
    for i in range(0, len(video_ids), config.PAGE_SIZE):
        batch = video_ids[i : i + config.PAGE_SIZE]
        resp = yt.videos().list(
            part=(
                "id,snippet,contentDetails,statistics,"
                "status,paidProductPlacementDetails"
            ),
            id=",".join(batch),
            maxResults=config.PAGE_SIZE,
        ).execute()
        quota_used += 1
        videos.extend(resp.get("items", []))
        print(f"  影片資料進度：{len(videos)}/{len(video_ids)}", end="\r")
    print()
    return videos


def fetch_channel_playlists(yt, channel_id):
    """取得頻道全部自建播放清單"""
    global quota_used
    playlists, page_token = [], None
    while True:
        resp = yt.playlists().list(
            part="id,snippet,contentDetails",
            channelId=channel_id,
            maxResults=config.PAGE_SIZE,
            pageToken=page_token,
        ).execute()
        quota_used += 1
        playlists.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            return playlists


def save_json(obj, filename):
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = config.RAW_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    size_mb = path.stat().st_size / 1024 / 1024
    print(f"  已存 {path.name}（{size_mb:.2f} MB）")


def main():
    started = datetime.now(timezone.utc)
    yt = get_client()

    try:
        channel = resolve_channel(yt)

        uploads_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        print("抓取上傳清單（含 Shorts）…")
        upload_items = fetch_all_playlist_items(yt, uploads_id)
        video_ids = [it["contentDetails"]["videoId"] for it in upload_items]
        print(f"  共 {len(video_ids)} 支影片")

        print("抓取影片完整資料…")
        videos = fetch_video_details(yt, video_ids)

        print("抓取頻道播放清單（供系列判定）…")
        playlists = fetch_channel_playlists(yt, channel["id"])
        print(f"  共 {len(playlists)} 個播放清單，逐一抓取收錄影片…")
        playlist_video_map = {}
        for pl in playlists:
            pl_items = fetch_all_playlist_items(yt, pl["id"])
            playlist_video_map[pl["id"]] = {
                "title": pl["snippet"]["title"],
                "video_ids": [it["contentDetails"]["videoId"] for it in pl_items],
            }

    except HttpError as e:
        if e.resp.status == 403:
            sys.exit(f"[錯誤] API 拒絕（403）：金鑰無效或配額用盡。詳情：{e}")
        raise

    print("落地保存原始 JSON…")
    save_json(channel, "channel.json")
    save_json(videos, "videos.json")
    save_json(playlists, "playlists.json")
    save_json(playlist_video_map, "playlist_video_map.json")
    save_json(
        {
            "fetched_at_utc": started.isoformat(),
            "video_count": len(videos),
            "playlist_count": len(playlists),
            "quota_units_used": quota_used,
        },
        "fetch_meta.json",
    )

    print(f"\n完成。本次配額消耗約 {quota_used} units（每日上限 10,000）")


if __name__ == "__main__":
    main()
