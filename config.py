import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0") or 0)
PROXY_URL = os.getenv("PROXY_URL", "").strip()
RETAIL_MARKUP = 1.10
PRICE_PER_PAGE = 20
SEARCH_PER_PAGE = 10
TEMP_MSG_TTL = 120  # секунд, через которые временные сообщения удаляются
UPDATE_INTERVAL_SECONDS = 60 * 60  # 1 час
WHOLESALERS_FILE = Path(__file__).parent / "wholesalers.txt"  # файл с ID оптовиков (по одному на строку)
WHOLESALERS_CHECK_SECONDS = 30
PRICE_482_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1aI1SuXZ2PfiKL4d4neY0xYRuWSHybS0PD1z3rFOnnM8/edit?gid=0#gid=0"
)