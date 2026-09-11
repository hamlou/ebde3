"""
realtime_mirror.py — Multi-channel smart signal mirroring.

Channels:
- wolfoftrading → TG signals, X results at 50%/100%
- mmsignalsfx → TG INSTANT, X best result when signal finishes

Features:
- Promo filtering
- Smart rewrite (Groq AI)
- Trade tracking with notifications
"""

import os
import sys
import asyncio
import re
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from aiogram import Bot
from aiogram.types import BufferedInputFile

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION = os.getenv("TELEGRAM_SESSION", "")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
FREE_CHANNEL_ID = os.getenv("FREE_CHANNEL_ID", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Multi-channel config
# Channels with custom handlers
WOLF_CHANNEL = "wolfoftrading"
MM_CHANNEL = "mmsignalsfx"

# EXTRA_SOURCE_CHANNELS: comma-separated list in .env for future channels
# These use the generic handler (TG mirror + trade_tracker for 50%/100% TP → X)
EXTRA_CHANNELS = [c.strip() for c in os.getenv("EXTRA_SOURCE_CHANNELS", "").split(",") if c.strip()]
SOURCE_CHANNELS = [WOLF_CHANNEL, MM_CHANNEL] + EXTRA_CHANNELS

client = None
bot = None

# Channel entity cache
channel_entities = {}


# ══════════════════════════════════════════════════════════════════════════════
# CONTENT FILTERING
# ══════════════════════════════════════════════════════════════════════════════

PROMO_PATTERNS = [
    r"join\s+(my|our|the)\s+(channel|group|vip|premium)",
    r"(subscribe|sign\s*up|register)\s+(to|for|now)",
    r"(discount|coupon|promo\s*code|special\s*offer)",
    r"(affiliate|referral)\s*(link|code)?",
    r"(free\s*trial|limited\s*time|exclusive\s*access)",
    r"(t\.me/|telegram\.me/|bit\.ly/|tinyurl)",
    r"(dm\s*me|contact\s*me|message\s*me)\s*(for|to)",
    r"(paid\s*group|premium\s*signals|vip\s*membership)",
    r"(giveaway|airdrop|free\s*money)",
    r"follow\s*(me|us)\s*(on|at)",
    r"(check\s*out|visit)\s*(my|our)\s*(website|channel|profile)",
    r"(link\s*in\s*bio|bio\s*link)",
    r"@\w+\s*(channel|group)",
]


def is_promo_content(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    for pattern in PROMO_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def is_trading_content(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    trading_indicators = [
        r"\$[A-Z]{2,}", r"(long|short)\s*(entry|position)?",
        r"(entry|target|tp|sl|stop\s*loss)", r"(leverage|x\d+|\d+x)",
        r"(buy|sell)\s*(zone|area|at)", r"(support|resistance|breakout)",
        r"(bullish|bearish)",
        r"(btc|eth|gold|xau|eur|gbp|jpy|usd|aud|nzd|chf|cad)",
        r"(market|price|chart|candle|trend|volume|momentum|reversal)",
        r"(analysis|setup|watch|alert|update|outlook|forecast)",
        r"(profit|loss|pips?|roi|r:r|risk.?reward)",
        r"(dxy|spx|nas|dow|oil|wti|brent|silver|xag)",
    ]
    return any(re.search(p, text_lower) for p in trading_indicators)


def is_trading_signal(text: str) -> bool:
    return is_trading_content(text)


# ══════════════════════════════════════════════════════════════════════════════
# SMART REWRITE (Groq)
# ══════════════════════════════════════════════════════════════════════════════

async def smart_rewrite(text: str) -> str:
    import httpx

    if not GROQ_API_KEY:
        return fallback_rewrite(text)

    try:
        prompt = f"""Rewrite this trading signal with a unique voice.

ORIGINAL:
{text}

RULES:
- Keep key info (pair, direction, entry, TP, SL) but REWORD
- Sound like a confident trader, not copy-paste
- Use different emojis: 🎯 💰 📈 📉 🔥 ⚡ 💎 🚀
- Max 350 chars
- End with 2-3 relevant hashtags from: #forex #crypto #trading #BTC #ETH #XAUUSD #GBPUSD #EURUSD #gold #signals (pick the ones that match the pair)
- NO disclaimers

OUTPUT: Just the rewritten text."""

        async with httpx.AsyncClient(timeout=20) as c:
            resp = await c.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": "groq/compound-mini", "messages": [{"role": "user", "content": prompt}], "max_tokens": 180}
            )
            return resp.json()["choices"][0]["message"]["content"].strip()[:400]
    except Exception as e:
        print(f"[AI] Groq failed: {e}")
        return fallback_rewrite(text)


def fallback_rewrite(text: str) -> str:
    import random
    emojis = random.choice([("🎯", "💰"), ("📈", "🔥"), ("🚀", "💎")])
    return f"{emojis[0]} {text[:300]}..."


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM POSTING
# ══════════════════════════════════════════════════════════════════════════════

async def post_to_telegram(text: str, image_bytes: bytes = None) -> int | None:
    """Post to FREE Telegram channel. Returns message ID."""
    global bot

    if not FREE_CHANNEL_ID:
        return None

    try:
        if image_bytes:
            photo = BufferedInputFile(image_bytes, filename="chart.png")
            msg = await bot.send_photo(chat_id=FREE_CHANNEL_ID, photo=photo, caption=text, parse_mode="HTML")
        else:
            msg = await bot.send_message(chat_id=FREE_CHANNEL_ID, text=text, parse_mode="HTML")
        return msg.message_id
    except Exception as e:
        print(f"[TG] Post failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# WOLF HANDLER — Signals to TG, track for X results
# ══════════════════════════════════════════════════════════════════════════════

async def handle_wolf_message(event):
    """Handle wolfoftrading messages."""
    message = event.message
    text = message.text or message.caption or ""

    if not text or len(text) < 10:
        return

    print(f"\n{'='*50}")
    print(f"[WOLF] {datetime.now().strftime('%H:%M:%S')}")
    print(f"[TEXT] {text[:80]}...")

    if is_promo_content(text):
        print("[SKIP] Promo")
        return

    if not is_trading_signal(text) and not message.photo:
        print("[SKIP] Not signal")
        return

    # Get image
    image_bytes = None
    if message.photo:
        try:
            image_bytes = await client.download_media(message, bytes)
        except:
            pass

    # Rewrite
    rewritten = await smart_rewrite(text)
    print(f"[REWRITE] {rewritten[:60]}...")

    # Post to TG
    msg_id = await post_to_telegram(rewritten, image_bytes)
    print(f"[TG] {'✅' if msg_id else '❌'}")

    # Track for X results (50%/100% TP hit → posted by trade_tracker)
    try:
        from trade_tracker import parse_signal, add_trade
        from notify_bot import notify_signal_tracked

        trade = parse_signal(text)
        if trade:
            add_trade(trade)
            await notify_signal_tracked(trade.pair, trade.direction, trade.entry, trade.targets)
            print(f"[TRACK] {trade.pair} {trade.direction}")
        else:
            print("[TRACK] No levels parsed")
    except Exception as e:
        print(f"[TRACK] Error: {e}")

    print(f"{'='*50}")


# ══════════════════════════════════════════════════════════════════════════════
# MM HANDLER — INSTANT to TG, delayed best result to X
# ══════════════════════════════════════════════════════════════════════════════

async def handle_mm_message(event):
    """Handle mmsignalsfx messages — INSTANT to TG."""
    message = event.message
    text = message.text or message.caption or ""

    if not text or len(text) < 5:
        return

    print(f"\n{'='*50}")
    print(f"[MM] {datetime.now().strftime('%H:%M:%S')}")
    print(f"[TEXT] {text[:80]}...")

    if is_promo_content(text):
        print("[SKIP] Promo")
        return

    # Get image
    image_bytes = None
    if message.photo:
        try:
            image_bytes = await client.download_media(message, bytes)
        except:
            pass

    # Use MM tracker for full handling (TG instant, X only on best result)
    try:
        from mm_tracker import handle_mm_message as mm_handle
        result = await mm_handle(text, image_bytes, bot, FREE_CHANNEL_ID)
        print(f"[MM] TG: {'✅' if result['posted_tg'] else '❌'} | Track: {result.get('signal_tracked', False)}")
    except Exception as e:
        print(f"[MM] Error: {e}")
        # Fallback: just post to TG
        rewritten = await smart_rewrite(text)
        await post_to_telegram(rewritten, image_bytes)
        print("[MM] Fallback TG post")

    print(f"{'='*50}")


# ══════════════════════════════════════════════════════════════════════════════
# GENERIC HANDLER — For any extra channel (TG + full tracking)
# ══════════════════════════════════════════════════════════════════════════════

async def handle_generic_message(event, channel_name: str):
    message = event.message
    text = message.text or message.caption or ""

    if not text or len(text) < 10:
        return

    tag = channel_name.upper()[:8]
    print(f"\n{'='*50}")
    print(f"[{tag}] {datetime.now().strftime('%H:%M:%S')}")
    print(f"[TEXT] {text[:80]}...")

    if is_promo_content(text):
        print("[SKIP] Promo")
        return

    if not is_trading_content(text) and not message.photo:
        print("[SKIP] Not trading content")
        return

    image_bytes = None
    if message.photo:
        try:
            image_bytes = await client.download_media(message, bytes)
        except:
            pass

    rewritten = await smart_rewrite(text)
    print(f"[REWRITE] {rewritten[:60]}...")

    msg_id = await post_to_telegram(rewritten, image_bytes)
    print(f"[TG] {'✅' if msg_id else '❌'}")

    # Track for 50%/100% TP → X posting
    try:
        from trade_tracker import parse_signal, add_trade
        from notify_bot import notify_signal_tracked

        trade = parse_signal(text)
        if trade:
            trade.id = f"{channel_name}_{trade.id}"
            add_trade(trade)
            await notify_signal_tracked(trade.pair, trade.direction, trade.entry, trade.targets)
            print(f"[TRACK] {trade.pair} {trade.direction}")
        else:
            print("[TRACK] No levels parsed")
    except Exception as e:
        print(f"[TRACK] Error: {e}")

    # Also track via MM-style best result flow
    try:
        from mm_tracker import handle_mm_message as mm_handle
        await mm_handle(text, image_bytes, bot, FREE_CHANNEL_ID)
    except:
        pass

    print(f"{'='*50}")


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED MESSAGE ROUTER
# ══════════════════════════════════════════════════════════════════════════════

async def route_message(event):
    """Route message to appropriate handler based on source."""
    chat = event.chat
    username = (getattr(chat, 'username', '') or '').lower()

    if WOLF_CHANNEL in username:
        await handle_wolf_message(event)
    elif MM_CHANNEL in username:
        await handle_mm_message(event)
    else:
        matched = next((ch for ch in EXTRA_CHANNELS if ch.lower() in username), None)
        await handle_generic_message(event, matched or username)


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════════════════════

async def run_wolf_tracker():
    """Monitor wolf trades for X results."""
    from trade_tracker import monitor_trades
    print("[WOLF TRACKER] Started")
    while True:
        try:
            await monitor_trades()
        except Exception as e:
            print(f"[WOLF TRACKER] Error: {e}")
        await asyncio.sleep(60)


async def run_mm_tracker():
    """Monitor MM signals for X best results."""
    from mm_tracker import check_finished_signals
    print("[MM TRACKER] Started")
    while True:
        try:
            await check_finished_signals()
        except Exception as e:
            print(f"[MM TRACKER] Error: {e}")
        await asyncio.sleep(300)


async def run_cookie_health():
    """Check X cookies every 4 hours. Alert on death."""
    from x_cookies import check_cookies_valid
    from notify_bot import notify_cookie_death, notify_cookie_ok

    print("[COOKIE HEALTH] Started — checking every 4h")
    last_status = None
    consecutive_fails = 0

    await asyncio.sleep(30)

    while True:
        try:
            valid = await check_cookies_valid()

            if valid:
                consecutive_fails = 0
                if last_status is False:
                    await notify_cookie_ok()
                    print("[COOKIE HEALTH] Recovered")
                last_status = True
                print(f"[COOKIE HEALTH] OK — {datetime.now().strftime('%H:%M')}")
            else:
                consecutive_fails += 1
                print(f"[COOKIE HEALTH] FAIL #{consecutive_fails}")
                if consecutive_fails == 1 or consecutive_fails % 3 == 0:
                    await notify_cookie_death(f"Failed {consecutive_fails}x consecutive checks")
                last_status = False

        except Exception as e:
            consecutive_fails += 1
            print(f"[COOKIE HEALTH] Error: {e}")
            if consecutive_fails == 1:
                await notify_cookie_death(str(e))
            last_status = False

        await asyncio.sleep(4 * 3600)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def start_realtime_mirror():
    global client, bot, channel_entities

    print("=" * 60)
    print("SMART MIRROR v3.1 — Multi-Channel + Health")
    print(f"Channels: {SOURCE_CHANNELS}")
    print(f"Groq AI: {'✅' if GROQ_API_KEY else '❌'}")
    print("Wolf: TG signals → X at 50%/100%")
    print("MM: TG INSTANT → X best result")
    print("Cookie health: every 4h")
    print("=" * 60)

    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH required")

    client = TelegramClient(StringSession(TELEGRAM_SESSION), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    bot = Bot(token=BOT_TOKEN)

    await client.start()
    print("[TELETHON] Connected")

    # Start background trackers
    asyncio.create_task(run_wolf_tracker())
    asyncio.create_task(run_mm_tracker())
    asyncio.create_task(run_cookie_health())

    # Get channel entities
    source_entities = []
    for channel in SOURCE_CHANNELS:
        try:
            entity = await client.get_entity(channel)
            source_entities.append(entity)
            channel_entities[channel] = entity
            print(f"[WATCH] @{channel}")
        except Exception as e:
            print(f"[ERROR] @{channel}: {e}")

    if not source_entities:
        raise ValueError("No valid source channels")

    @client.on(events.NewMessage(chats=source_entities))
    async def on_new_message(event):
        await route_message(event)

    print("\n🟢 LISTENING")
    print("   Wolf: TG signals, X results")
    print("   MM: TG instant, X best result")
    if EXTRA_CHANNELS:
        print(f"   Extra: {', '.join(EXTRA_CHANNELS)} (TG + full tracking)")
    print("\nPress Ctrl+C to stop\n")

    await client.run_until_disconnected()


async def stop():
    global client, bot
    if client:
        await client.disconnect()
    if bot:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(start_realtime_mirror())
    except KeyboardInterrupt:
        print("\nStopping...")
        asyncio.run(stop())
