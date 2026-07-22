import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
VIP_CHANNEL_ID = os.getenv("VIP_CHANNEL_ID", "")
FREE_CHANNEL_ID = os.getenv("FREE_CHANNEL_ID", "")
WHOP_WEBHOOK_SECRET = os.getenv("WHOP_WEBHOOK_SECRET", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MAKE_WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL", "")
APIFLASH_KEY = os.getenv("APIFLASH_KEY", "dc426ed4ac194bdc9b6964a34f7d5799")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
