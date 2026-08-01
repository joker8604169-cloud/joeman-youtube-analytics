# -*- coding: utf-8 -*-
"""專案共用設定"""
from pathlib import Path

# ── 頻道識別 ────────────────────────────────────────────────
# handle 由使用者提供的網址而來；channel ID 已於 2026-07-21 透過
# https://www.youtube.com/@joeman 頁面的 canonical 標記驗證。
# 執行時會再用 API 解析 handle，若與此 ID 不符即中止，防止抓錯頻道。
CHANNEL_HANDLE = "joeman"
EXPECTED_CHANNEL_ID = "UCPRWWKG0VkBA0Pqa4Jr5j0Q"

# ── 路徑 ────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
RULES_DIR = PROJECT_ROOT / "rules"

# ── API ─────────────────────────────────────────────────────
API_KEY_ENV_NAME = "YT_API_KEY"   # 金鑰放在 .env，變數名稱如左
PAGE_SIZE = 50                    # API 單頁上限
