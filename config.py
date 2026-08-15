import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0") or 0)
RETAIL_MARKUP = 1.15
PRICE_PER_PAGE = 10
SEARCH_PER_PAGE = 10
TEMP_MSG_TTL = 120  # секунд, через которые временные сообщения удаляются
UPDATE_INTERVAL_SECONDS = 60 * 60  # 1 час