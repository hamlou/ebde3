"""
mm_tracker.py — Track mmsignalsfx signals and post BEST results to X

Workflow:
1. Signal arrives → Post to TG INSTANTLY
2. Track the signal, collect all "boom" screenshots with pips
3. When signal "finishes" (new signal or timeout) → Find BEST result
4. Post to X: Best result screenshot + OUR TG signal screenshot

Key: Wait for signal to complete, don't post first "boom", find highest pips
"""

import os
import re
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict, field
from typing import Optional
from aiogram import Bot
from aiogram.types import BufferedInputFile

from dotenv import load_dotenv
load_dotenv()

FREE_CHANNEL_ID = os.getenv("FREE_CHANNEL_ID", "")
FREE_CHANNEL_LINK = os.getenv("FREE_CHANNEL_LINK", "https://t.me/RoyallTraders")
MM_SIGNALS_FILE = Path(__file__).parent.parent / "mm_signals.json"

# Signal timeout: if no update for 2 hours, signal is "finished"
SIGNAL_TIMEOUT_HOURS = 2


@dataclass
class MMSignal:
    id: str
    pair: str
    direction: str  # BUY/SELL/LONG/SHORT
    original_text: str
    our_tg_message_id: Optional[int] = None  # Message ID in our TG channel
    created_at: str = ""
    results: list = field(default_factory=list)  # [{pips: int, image_bytes_b64: str, text: str}]
    last_update: str = ""
    posted_to_x: bool = False
    status: str = "ACTIVE"  # ACTIVE, FINISHED, POSTED


def load_mm_signals() -> list[dict]:
    if not MM_SIGNALS_FILE.exists():
        return []
    with open(MM_SIGNALS_FILE) as f:
        return json.load(f)


def save_mm_signals(signals: list[dict]):
    with open(MM_SIGNALS_FILE, "w") as f:
        json.dump(signals, f, indent=2)


def get_active_signal(pair: str) -> Optional[dict]:
    """Get active signal for a pair."""
    signals = load_mm_signals()
    for s in signals:
        if s["pair"] == pair and s["status"] == "ACTIVE":
            return s
    return None


def add_mm_signal(signal: MMSignal):
    signals = load_mm_signals()
    signals.append(asdict(signal))
    save_mm_signals(signals)


def update_mm_signal(signal_id: str, updates: dict):
    signals = load_mm_signals()
    for s in signals:
        if s["id"] == signal_id:
            s.update(updates)
            break
    save_mm_signals(signals)


# ══════════════════════════════════════════════════════════════════════════════
# PARSING — Detect signals vs results vs analysis
# ══════════════════════════════════════════════════════════════════════════════

def extract_pair(text: str) -> Optional[str]:
    """Extract trading pair from text."""
    patterns = [
        r'(XAU|GOLD|XAUUSD)',
        r'(EUR|GBP|USD|JPY|AUD|CAD|CHF|NZD)[/_]?(USD|EUR|GBP|JPY|AUD|CAD|CHF|NZD)',
        r'\$([A-Z]{3,10})',
        r'(BTC|ETH|SOL|XRP|DOGE|ADA)[/_]?(USD|USDT)?',
    ]
    text_upper = text.upper()
    for pattern in patterns:
        match = re.search(pattern, text_upper)
        if match:
            pair = match.group(0).replace("/", "").replace("_", "")
            if pair in ["XAU", "GOLD"]:
                return "XAUUSD"
            return pair
    return None


def extract_direction(text: str) -> Optional[str]:
    """Extract trade direction."""
    text_lower = text.lower()
    if any(x in text_lower for x in ["buy", "long", "bullish"]):
        return "BUY"
    if any(x in text_lower for x in ["sell", "short", "bearish"]):
        return "SELL"
    return None


def is_signal_post(text: str) -> bool:
    """Check if this is a new signal (not result/analysis)."""
    text_lower = text.lower()

    # Must have direction
    if not extract_direction(text):
        return False

    # Must have pair
    if not extract_pair(text):
        return False

    # Signal indicators
    signal_words = ["entry", "tp", "sl", "target", "stop", "buy now", "sell now", "zone"]
    return any(w in text_lower for w in signal_words)


def is_result_post(text: str) -> bool:
    """Check if this is a result post (boom, pips, profit)."""
    text_lower = text.lower()
    result_words = ["boom", "pips", "profit", "tp hit", "target hit", "running", "secured", "banked", "+"]
    return any(w in text_lower for w in result_words)


def extract_pips(text: str) -> Optional[int]:
    """Extract pip count from result text."""
    patterns = [
        r'(\d+)\s*pips?',
        r'\+\s*(\d+)',
        r'(\d+)\s*pts?',
    ]
    text_lower = text.lower()
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            return int(match.group(1))
    return None


def is_analysis_post(text: str) -> bool:
    """Check if this is analysis/update (post to both TG and X)."""
    text_lower = text.lower()
    analysis_words = ["update", "analysis", "outlook", "forecast", "weekly", "daily", "review", "expecting"]
    return any(w in text_lower for w in analysis_words)


def is_promo(text: str) -> bool:
    """Check if this is self-promotion."""
    text_lower = text.lower()
    promo_words = ["join", "subscribe", "channel", "vip", "premium", "discount", "free trial", "t.me/", "telegram.me/"]
    return any(w in text_lower for w in promo_words)


# ══════════════════════════════════════════════════════════════════════════════
# TG SCREENSHOT — Capture our own signal post
# ══════════════════════════════════════════════════════════════════════════════

async def capture_tg_message_screenshot(message_id: int, channel_id: str) -> Optional[bytes]:
    """
    Capture screenshot of our TG channel message.
    Uses web preview or Playwright.
    """
    try:
        from playwright.async_api import async_playwright

        # Telegram web link format
        # Convert -100xxxx to channel username or use t.me/c/xxxx format
        channel_num = str(channel_id).replace("-100", "")
        url = f"https://t.me/c/{channel_num}/{message_id}?embed=1"

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 500, "height": 400})

            await page.goto(url, wait_until="networkidle")
            await asyncio.sleep(2)

            screenshot = await page.screenshot(type="png")
            await browser.close()

            return screenshot
    except Exception as e:
        print(f"[MM] TG screenshot failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# X POSTING
# ══════════════════════════════════════════════════════════════════════════════

async def post_mm_result_to_x(result_image: bytes, our_tg_image: bytes, pair: str, pips: int) -> bool:
    """
    Post to X: result screenshot + our TG signal screenshot
    """
    try:
        from x_cookies import post_tweet
        import tempfile

        # Create combined image or post with multiple images
        # For now, post result image with text mentioning our channel

        from attribution import generate_attribution_link
        attr_link, _ = generate_attribution_link(pair, "mm_best_result")

        hashtags = "#trading #forex #signals"
        if any(x in pair.upper() for x in ["BTC", "ETH", "SOL", "XRP"]):
            hashtags = "#crypto #trading #signals"
        elif any(x in pair.upper() for x in ["XAU", "GOLD"]):
            hashtags = "#gold #XAUUSD #trading"

        text = (
            f"🎯 {pair} called it. +{pips} pips.\n\n"
            f"Signal was in our free channel.\n"
            f"{attr_link}\n\n{hashtags}"
        )

        # Save result image
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(result_image)
            image_path = f.name

        result = await post_tweet(text, image_path)

        try:
            os.unlink(image_path)
        except:
            pass

        return result.success

    except Exception as e:
        print(f"[MM] X post failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def handle_mm_message(text: str, image_bytes: Optional[bytes], bot: Bot, our_channel_id: str) -> dict:
    """
    Handle message from mmsignalsfx.
    Returns: {posted_tg: bool, signal_tracked: bool, result_added: bool}
    """
    import base64
    from notify_bot import notify_signal_tracked

    result = {"posted_tg": False, "signal_tracked": False, "result_added": False}

    # Skip promos
    if is_promo(text):
        print("[MM] Skipping promo")
        return result

    pair = extract_pair(text)

    # ── NEW SIGNAL ──
    if is_signal_post(text):
        direction = extract_direction(text)

        # Rewrite for TG
        rewritten = await rewrite_mm_signal(text)

        # Post to TG INSTANTLY
        try:
            if image_bytes:
                photo = BufferedInputFile(image_bytes, filename="chart.png")
                msg = await bot.send_photo(chat_id=our_channel_id, photo=photo, caption=rewritten, parse_mode="HTML")
            else:
                msg = await bot.send_message(chat_id=our_channel_id, text=rewritten, parse_mode="HTML")

            result["posted_tg"] = True
            tg_msg_id = msg.message_id
            print(f"[MM] Signal posted to TG: {pair} {direction}")
        except Exception as e:
            print(f"[MM] TG post failed: {e}")
            tg_msg_id = None

        # Track signal (MM screenshot flow)
        if pair and direction:
            signal = MMSignal(
                id=f"mm_{pair}_{int(datetime.now().timestamp())}",
                pair=pair,
                direction=direction,
                original_text=text[:500],
                our_tg_message_id=tg_msg_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                last_update=datetime.now(timezone.utc).isoformat(),
            )
            add_mm_signal(signal)
            result["signal_tracked"] = True

            # Notify
            await notify_signal_tracked(pair, direction, 0, [])
            print(f"[MM] Tracking signal (screenshot flow): {pair} {direction}")

        # Also track via trade_tracker for SL/50%/100% price monitoring
        try:
            from trade_tracker import parse_signal, add_trade
            trade = parse_signal(text)
            if trade:
                trade.id = f"mm_{trade.id}"
                add_trade(trade)
                print(f"[MM] Tracking signal (price flow): {trade.pair} {trade.direction} entry={trade.entry}")
            else:
                print("[MM] No entry/TP/SL levels parsed for price tracking")
        except Exception as e:
            print(f"[MM] Price tracking setup error: {e}")

    # ── RESULT POST (boom, pips) ──
    elif is_result_post(text) and pair:
        pips = extract_pips(text)
        active_signal = get_active_signal(pair)

        if active_signal and pips:
            # Add result to signal
            results = active_signal.get("results", [])
            results.append({
                "pips": pips,
                "text": text[:200],
                "image_b64": base64.b64encode(image_bytes).decode() if image_bytes else None,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            update_mm_signal(active_signal["id"], {
                "results": results,
                "last_update": datetime.now(timezone.utc).isoformat()
            })

            result["result_added"] = True
            print(f"[MM] Added result: {pair} +{pips} pips")

        # Also post to TG instantly
        rewritten = await rewrite_mm_result(text)
        try:
            if image_bytes:
                photo = BufferedInputFile(image_bytes, filename="result.png")
                await bot.send_photo(chat_id=our_channel_id, photo=photo, caption=rewritten, parse_mode="HTML")
            else:
                await bot.send_message(chat_id=our_channel_id, text=rewritten, parse_mode="HTML")
            result["posted_tg"] = True
        except Exception as e:
            print(f"[MM] TG result post failed: {e}")

    # ── ANALYSIS/UPDATE ──
    elif is_analysis_post(text):
        rewritten = await rewrite_mm_analysis(text)

        # Post to TG
        try:
            if image_bytes:
                photo = BufferedInputFile(image_bytes, filename="analysis.png")
                await bot.send_photo(chat_id=our_channel_id, photo=photo, caption=rewritten, parse_mode="HTML")
            else:
                await bot.send_message(chat_id=our_channel_id, text=rewritten, parse_mode="HTML")
            result["posted_tg"] = True
        except Exception as e:
            print(f"[MM] TG analysis post failed: {e}")

        # Also post to X (analysis is okay for X)
        try:
            from x_cookies import post_tweet
            import tempfile

            from attribution import generate_attribution_link
            attr_link, _ = generate_attribution_link(extract_pair(text) or "analysis", "mm_analysis")
            x_text = rewritten[:250] + f"\n\n{attr_link}\n\n#forex #trading #signals"

            if image_bytes:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    f.write(image_bytes)
                    image_path = f.name
                await post_tweet(x_text, image_path)
                os.unlink(image_path)
            else:
                await post_tweet(x_text)

            print("[MM] Analysis posted to X")
        except Exception as e:
            print(f"[MM] X analysis post failed: {e}")

    # ── GENERAL POST (not signal, not result, not analysis) ──
    else:
        # Just post to TG
        rewritten = await rewrite_mm_general(text)
        try:
            if image_bytes:
                photo = BufferedInputFile(image_bytes, filename="post.png")
                await bot.send_photo(chat_id=our_channel_id, photo=photo, caption=rewritten, parse_mode="HTML")
            else:
                await bot.send_message(chat_id=our_channel_id, text=rewritten, parse_mode="HTML")
            result["posted_tg"] = True
        except Exception as e:
            print(f"[MM] TG general post failed: {e}")

    return result


async def check_finished_signals():
    """
    Check for signals that have "finished" (timeout or new signal started).
    Post best result to X.
    """
    import base64
    from notify_bot import notify_mm_best_result, notify_sl_hit

    signals = load_mm_signals()
    now = datetime.now(timezone.utc)

    for signal in signals:
        if signal["status"] != "ACTIVE":
            continue

        # Check timeout
        last_update = datetime.fromisoformat(signal["last_update"].replace("Z", "+00:00"))
        hours_since_update = (now - last_update).total_seconds() / 3600

        if hours_since_update >= SIGNAL_TIMEOUT_HOURS:
            results = signal.get("results", [])

            if results:
                # Find best result (highest pips)
                best = max(results, key=lambda r: r.get("pips", 0))
                best_pips = best.get("pips", 0)

                if best_pips > 0 and best.get("image_b64"):
                    # Post to X
                    result_image = base64.b64decode(best["image_b64"])

                    # Try to get our TG screenshot
                    our_tg_image = None
                    if signal.get("our_tg_message_id"):
                        our_tg_image = await capture_tg_message_screenshot(
                            signal["our_tg_message_id"],
                            FREE_CHANNEL_ID
                        )

                    success = await post_mm_result_to_x(
                        result_image,
                        our_tg_image,
                        signal["pair"],
                        best_pips
                    )

                    if success:
                        await notify_mm_best_result(signal["pair"], best_pips, "mmsignalsfx")
                        print(f"[MM] Posted best result to X: {signal['pair']} +{best_pips} pips")

                    update_mm_signal(signal["id"], {"status": "POSTED", "posted_to_x": True})
                else:
                    # No results or SL (no pips)
                    update_mm_signal(signal["id"], {"status": "FINISHED"})
                    print(f"[MM] Signal finished without results: {signal['pair']}")
            else:
                # No results collected - might be SL
                update_mm_signal(signal["id"], {"status": "FINISHED"})
                await notify_sl_hit(signal["pair"], 0, 0, signal.get("direction", ""))


# ══════════════════════════════════════════════════════════════════════════════
# REWRITE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

async def rewrite_mm_signal(text: str) -> str:
    """Rewrite signal with unique voice."""
    import httpx

    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    if not GROQ_API_KEY:
        return text[:500]

    try:
        prompt = f"""Rewrite this trading signal with a unique voice. Keep all the key info (pair, direction, entry, TP, SL) but change the wording.

ORIGINAL:
{text}

RULES:
- Keep entry, TP, SL levels EXACT (don't change numbers)
- Change emojis and formatting
- Sound confident but not copy-paste
- Max 400 chars

OUTPUT: Just the rewritten signal."""

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": "groq/compound-mini", "messages": [{"role": "user", "content": prompt}], "max_tokens": 200}
            )
            return resp.json()["choices"][0]["message"]["content"].strip()[:500]
    except:
        return text[:500]


async def rewrite_mm_result(text: str) -> str:
    """Rewrite result post."""
    # Simple rewrite - keep it short
    pips = extract_pips(text)
    pair = extract_pair(text) or "Trade"

    if pips:
        templates = [
            f"🎯 {pair} +{pips} pips secured",
            f"💰 {pair} hit target — +{pips} pips",
            f"✅ {pair} running +{pips} pips",
        ]
        import random
        return random.choice(templates)
    return text[:200]


async def rewrite_mm_analysis(text: str) -> str:
    """Rewrite analysis post."""
    import httpx

    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    if not GROQ_API_KEY:
        return text[:400]

    try:
        prompt = f"""Rewrite this market analysis with a unique voice.

ORIGINAL:
{text}

RULES:
- Keep the main analysis points
- Change wording and emojis
- Sound like a knowledgeable trader
- Max 350 chars

OUTPUT: Just the rewritten analysis."""

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": "groq/compound-mini", "messages": [{"role": "user", "content": prompt}], "max_tokens": 180}
            )
            return resp.json()["choices"][0]["message"]["content"].strip()[:400]
    except:
        return text[:400]


async def rewrite_mm_general(text: str) -> str:
    """Rewrite general post."""
    return text[:400]  # Just truncate for now


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND TASK
# ══════════════════════════════════════════════════════════════════════════════

async def run_mm_tracker():
    """Background task to check finished signals."""
    print("[MM TRACKER] Starting background check...")
    while True:
        try:
            await check_finished_signals()
        except Exception as e:
            print(f"[MM TRACKER] Error: {e}")
        await asyncio.sleep(300)  # Check every 5 minutes
