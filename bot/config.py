import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
VIP_CHANNEL_ID = os.getenv("VIP_CHANNEL_ID", "")
FREE_CHANNEL_ID = os.getenv("FREE_CHANNEL_ID", "")
WHOP_WEBHOOK_SECRET = os.getenv("WHOP_WEBHOOK_SECRET", "")
GEMINI_KEYS = [
    os.getenv("GEMINI_KEY_1", "AQ.Ab8RN6K-MeBbTTjmqZNBi42sl0kRZTGxiRIvrOllUZbxlXqSjA"),
    os.getenv("GEMINI_KEY_2", "AQ.Ab8RN6Khspsnp-Mh6ega8LZe9KoUZveSwEubVPq-APoxtxhFew"),
    os.getenv("GEMINI_KEY_3", "AQ.Ab8RN6KtveDs_cZ4pC_J7vExxfoTDQiYHljtgbCYwUEeGB3xow"),
    os.getenv("GEMINI_KEY_4", "AQ.Ab8RN6Iz3d2eQnwmtNSgHlH96s_UN3ugoG2wXWtxmualbUKTmg"),
    os.getenv("GEMINI_KEY_5", "AQ.Ab8RN6L4nxfnFSTb7weIx1aA3lbK0F9ZL_ub4O1oESs8eRP47g"),
    os.getenv("GEMINI_KEY_6", "AQ.Ab8RN6Kl5OrkUkvHaDtxsV0Zpqee5tueG683aFofA2iUEvPevg"),
]

MAKE_WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL", "")
APIFLASH_KEY = os.getenv("APIFLASH_KEY", "dc426ed4ac194bdc9b6964a34f7d5799")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
