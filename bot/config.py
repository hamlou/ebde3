import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
VIP_CHANNEL_ID = os.getenv("VIP_CHANNEL_ID", "")
FREE_CHANNEL_ID = os.getenv("FREE_CHANNEL_ID", "")
WHOP_WEBHOOK_SECRET = os.getenv("WHOP_WEBHOOK_SECRET", "")
GEMINI_KEYS = [
    os.getenv("GEMINI_KEY_1", ""),
    os.getenv("GEMINI_KEY_2", ""),
    os.getenv("GEMINI_KEY_3", ""),
    os.getenv("GEMINI_KEY_4", ""),
    os.getenv("GEMINI_KEY_5", ""),
    os.getenv("GEMINI_KEY_6", ""),
]

MAKE_WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL", "")
APIFLASH_KEY = os.getenv("APIFLASH_KEY", "")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "change-me-in-production")
