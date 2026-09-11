"""
content_mirror.py — Mirror analysis content from source channels.

Instead of parsing signals, we:
1. Fetch recent posts (text + images)
2. Rewrite the text with our personality
3. Post to X and Telegram with the same images

This is content mirroring, not signal copying.
"""

import os
import re
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

from telethon import TelegramClient
from telethon.sessions import StringSession

TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION = os.getenv("TELEGRAM_SESSION", "")
SOURCE_CHANNELS = os.getenv("SOURCE_CHANNELS", "wolfoftrading").split(",")


@dataclass
class SourcePost:
    text: str
    image_bytes: Optional[bytes] = None
    source_channel: str = ""
    message_id: int = 0
    timestamp: str = ""
    asset: Optional[str] = None

    def has_image(self) -> bool:
        return self.image_bytes is not None

    def extract_asset(self) -> Optional[str]:
        """Extract asset ticker from text like $BTCUSDT or $XRP"""
        match = re.search(r'\$([A-Z]{2,10}(?:USDT?|USD)?)', self.text.upper())
        if match:
            return match.group(1)
        return None


class ContentMirror:
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.is_connected = False

    async def connect(self):
        if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
            raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH required")

        if TELEGRAM_SESSION:
            self.client = TelegramClient(
                StringSession(TELEGRAM_SESSION),
                int(TELEGRAM_API_ID),
                TELEGRAM_API_HASH
            )
        else:
            self.client = TelegramClient(
                "bot/scraper_session",
                int(TELEGRAM_API_ID),
                TELEGRAM_API_HASH
            )

        await self.client.connect()

        if not await self.client.is_user_authorized():
            print("⚠️ Telegram not authorized")
            return False

        self.is_connected = True
        print("✅ Content mirror connected")
        return True

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()
            self.is_connected = False

    async def fetch_posts(
        self,
        channel_name: str = "wolfoftrading",
        limit: int = 10,
        hours_back: int = 24,
        with_images_only: bool = False
    ) -> list[SourcePost]:
        """Fetch recent posts from a channel."""
        if not self.is_connected:
            await self.connect()

        if not self.is_connected:
            return []

        posts = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

        try:
            entity = await self.client.get_entity(channel_name)

            async for message in self.client.iter_messages(entity, limit=limit):
                if message.date.replace(tzinfo=timezone.utc) < cutoff:
                    break

                text = message.text or message.message or ""

                # Skip very short messages or forwarded content
                if len(text) < 20 and not message.photo:
                    continue

                image_bytes = None
                if message.photo:
                    try:
                        image_bytes = await self.client.download_media(message, bytes)
                    except Exception as e:
                        print(f"[Mirror] Image download failed: {e}")

                if with_images_only and not image_bytes:
                    continue

                post = SourcePost(
                    text=text,
                    image_bytes=image_bytes,
                    source_channel=channel_name,
                    message_id=message.id,
                    timestamp=message.date.isoformat(),
                )
                post.asset = post.extract_asset()
                posts.append(post)

            print(f"[Mirror] Fetched {len(posts)} posts from {channel_name}")
            return posts

        except Exception as e:
            print(f"[Mirror] Error: {e}")
            return []

    async def fetch_all_sources(self, hours_back: int = 12) -> list[SourcePost]:
        """Fetch from all configured source channels."""
        all_posts = []
        for channel in SOURCE_CHANNELS:
            channel = channel.strip()
            if not channel:
                continue
            posts = await self.fetch_posts(channel, limit=15, hours_back=hours_back)
            all_posts.extend(posts)

        all_posts.sort(key=lambda p: p.timestamp, reverse=True)
        return all_posts


mirror = ContentMirror()


# === Human-like rewriting for analysis posts ===

ANALYSIS_REWRITES = {
    "openers": [
        "Just spotted this on {asset}.",
        "{asset} update.",
        "Quick look at {asset}.",
        "Eyes on {asset} rn.",
        "{asset} doing exactly what we expected.",
        "Called it. {asset}.",
    ],
    "bullish": [
        "Looking strong here.",
        "Bulls stepping in.",
        "Breakout loading.",
        "This wants to go higher.",
        "Momentum building.",
    ],
    "bearish": [
        "Weakness showing.",
        "Bears in control.",
        "Breakdown incoming.",
        "This wants to go lower.",
        "Selling pressure evident.",
    ],
    "neutral": [
        "Watching for the next move.",
        "Consolidating. Patience.",
        "Let it play out.",
        "No rush here.",
        "Waiting for confirmation.",
    ],
    "cta": [
        "\n\nMore analysis in the channel 👇",
        "\n\nJoin for real-time updates ⚡",
        "\n\nFull breakdown in the group.",
        "\n\nLink in bio for alerts.",
    ],
}

import random
from llm import call_llm_text


def detect_sentiment(text: str) -> str:
    """Detect if the post is bullish, bearish, or neutral."""
    text_lower = text.lower()

    bullish_words = ["bullish", "long", "buy", "breakout", "pump", "moon", "green", "higher", "up move", "going up", "upside", "rally", "bounce"]
    bearish_words = ["bearish", "short", "sell", "breakdown", "dump", "drop", "red", "lower", "down move", "going down", "downside", "crash", "fall", "slight drop", "expected to go down"]

    bull_score = sum(1 for w in bullish_words if w in text_lower)
    bear_score = sum(1 for w in bearish_words if w in text_lower)

    # Check for negations that flip sentiment
    if "while everyone was bullish" in text_lower and ("drop" in text_lower or "down" in text_lower):
        bear_score += 2
    if "smashed" in text_lower and "target" in text_lower:
        # Win post - use the direction mentioned
        if "short" in text_lower:
            bear_score += 2
        elif "long" in text_lower:
            bull_score += 2

    if bull_score > bear_score:
        return "bullish"
    elif bear_score > bull_score:
        return "bearish"
    return "neutral"


def rewrite_analysis_simple(post: SourcePost, include_cta: bool = True) -> str:
    """Simple template-based rewrite of analysis post."""
    asset = post.asset or "this"
    sentiment = detect_sentiment(post.text)

    opener = random.choice(ANALYSIS_REWRITES["openers"]).format(asset=asset)
    body = random.choice(ANALYSIS_REWRITES[sentiment])

    result = f"{opener} {body}"

    if include_cta:
        result += random.choice(ANALYSIS_REWRITES["cta"])

    return result


async def rewrite_analysis_ai(post: SourcePost, include_cta: bool = True) -> str:
    """AI-powered rewrite of analysis post."""
    prompt = f"""You are a confident crypto/forex trader posting on Twitter/X.

COMPLETELY REWRITE this analysis in YOUR OWN words (under 280 chars):

Original: "{post.text[:500]}"

CRITICAL RULES:
- NEVER copy phrases word-for-word. Paraphrase EVERYTHING.
- Change sentence structure completely
- Use different vocabulary while keeping the same meaning
- Sound like a real human, casual and confident
- Keep exact price numbers if mentioned (e.g. $2340, 1.0850)
- Use 1-2 emojis max
- End with 2-3 relevant hashtags (e.g. #crypto #BTC #forex #trading #XAUUSD #gold #signals — pick what fits)
- NO "not financial advice"

Example transformation:
Original: "BTC showing bullish divergence on the 4H, expecting a move to 68k"
Your style: "BTC 4H structure looking good. Eyes on 68k if this plays out."

Write ONLY the rewritten tweet (no quotes, no explanation):"""

    try:
        ai_text = await call_llm_text(prompt, fallback="")
        if ai_text and len(ai_text) < 350:
            if include_cta:
                ai_text += random.choice(ANALYSIS_REWRITES["cta"])
            return ai_text.strip()
    except Exception as e:
        print(f"[Mirror] AI rewrite failed: {e}")

    return rewrite_analysis_simple(post, include_cta)


async def rewrite_for_telegram(text: str, asset: str) -> str:
    """AI rewrite for Telegram posts - never copy original text."""
    prompt = f"""Rewrite this trading analysis in your own words. NEVER copy phrases directly.

Original: "{text[:600]}"

Rules:
- COMPLETELY paraphrase - different sentence structure, different words
- Keep exact price levels (numbers like $2340, 1.0850, etc.)
- Keep the same market direction/prediction
- Write 2-4 sentences max
- Sound natural and confident
- No emojis, no hashtags

Write ONLY the rewritten text:"""

    try:
        result = await call_llm_text(prompt, fallback="")
        if result and len(result) > 20:
            return result.strip()
    except:
        pass

    # Fallback: use simple template if AI fails
    sentiment = detect_sentiment(text)
    if sentiment == "bullish":
        return f"{asset} showing strength. Structure looks good for upside continuation."
    elif sentiment == "bearish":
        return f"{asset} looking weak. Bears may push this lower from here."
    return f"Watching {asset} closely. Waiting for clearer direction."


def generate_telegram_post_sync(post: SourcePost, rewritten_text: str, is_vip: bool = True) -> str:
    """Generate Telegram post with pre-rewritten text."""
    asset = post.asset or "Market"
    sentiment = detect_sentiment(post.text)

    emoji = "🟢" if sentiment == "bullish" else ("🔴" if sentiment == "bearish" else "⚪")

    if is_vip:
        return f"""{emoji} <b>{asset} Analysis</b>

{rewritten_text}

<i>Real-time alerts • Royal Signals</i>"""
    else:
        preview = rewritten_text[:100] + "..." if len(rewritten_text) > 100 else rewritten_text
        return f"""{emoji} <b>{asset} Analysis</b>

{preview}

━━━━━━━━━━━━━━━━━━━━
🔒 <b>Full analysis + all updates</b>
VIP members see everything first.

💎 Join VIP → $49/month
━━━━━━━━━━━━━━━━━━━━"""


def generate_telegram_post(post: SourcePost, is_vip: bool = True) -> str:
    """DEPRECATED sync version - use async version in pipeline."""
    asset = post.asset or "Market"
    sentiment = detect_sentiment(post.text)
    emoji = "🟢" if sentiment == "bullish" else ("🔴" if sentiment == "bearish" else "⚪")

    # Simple fallback - should use async rewrite in pipeline
    if sentiment == "bullish":
        body = f"{asset} showing strength. Structure looks good for upside."
    elif sentiment == "bearish":
        body = f"{asset} looking weak. Bears in control here."
    else:
        body = f"Watching {asset} closely. Waiting for clearer direction."

    if is_vip:
        return f"""{emoji} <b>{asset} Analysis</b>

{body}

<i>Real-time alerts • Royal Signals</i>"""
    else:
        return f"""{emoji} <b>{asset} Analysis</b>

{body[:80]}...

━━━━━━━━━━━━━━━━━━━━
🔒 <b>Full analysis + all updates</b>
VIP members see everything first.

💎 Join VIP → $49/month
━━━━━━━━━━━━━━━━━━━━"""


if __name__ == "__main__":
    async def test():
        await mirror.connect()
        posts = await mirror.fetch_posts("wolfoftrading", limit=5, hours_back=48)

        for i, post in enumerate(posts, 1):
            print(f"\n=== Post {i} ===")
            print(f"Asset: {post.asset}")
            print(f"Has image: {post.has_image()}")
            print(f"Original: {post.text[:150]}...")
            print(f"Rewritten: {rewrite_analysis_simple(post)}")

        await mirror.disconnect()

    asyncio.run(test())
