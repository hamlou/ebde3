"""
engagement_posts.py — Periodic engagement tweets to boost X algo visibility.

Posts 2-3x/day during peak hours (14:00-18:00 UTC).
Asks questions, shares insights, invites discussion.
"""

import os
import random
import httpx
from datetime import datetime, timezone

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
FREE_CHANNEL_LINK = os.getenv("FREE_CHANNEL_LINK", "https://t.me/RoyallTraders")

ENGAGEMENT_TEMPLATES = [
    "What's your top watchlist pair this week? Drop it below 👇",
    "Patience > overtrading. How many setups did you take today?",
    "Gold or Bitcoin — which one are you more bullish on right now?",
    "What's the hardest lesson trading taught you?",
    "London session or NY session — which one prints better for you?",
    "One rule that saved your account. Go.",
    "Are you a scalper, day trader, or swing trader? What's your edge?",
    "Most underrated pair in forex right now?",
    "What's your risk per trade? 1%? 2%? Higher?",
    "Structure > indicators. Agree or disagree?",
    "What timeframe do you use for entries?",
    "Best trade you took this month — what was the setup?",
    "Name a pair that's setting up beautifully right now 📈",
    "ICT or SMC — does the label even matter if it works?",
    "How long did it take you to become consistently profitable?",
]


async def generate_engagement_post() -> str:
    if not GROQ_API_KEY:
        return random.choice(ENGAGEMENT_TEMPLATES) + f"\n\n#trading #forex #crypto"

    topic = random.choice([
        "gold price action", "crypto market sentiment", "forex pair setup",
        "risk management tip", "trading psychology", "market structure analysis",
        "London vs NY session", "weekly market outlook", "trading discipline",
    ])

    try:
        prompt = f"""You are a confident forex/crypto trader posting on Twitter/X. Write an ENGAGEMENT post about: {topic}

RULES:
- Ask a question OR share a hot take that invites replies
- Sound like a real trader, casual and confident
- Max 200 characters (short and punchy)
- Use 1 emoji max
- End with 2-3 relevant hashtags from: #forex #crypto #trading #BTC #gold #XAUUSD
- NO links, NO disclaimers
- Make people want to reply or retweet

OUTPUT: Just the tweet text."""

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": "groq/compound-mini", "messages": [{"role": "user", "content": prompt}], "max_tokens": 100, "temperature": 0.95}
            )
            text = resp.json()["choices"][0]["message"]["content"].strip().strip('"\'')
            if len(text) > 30:
                return text
    except Exception as e:
        print(f"[Engagement] AI failed: {e}")

    return random.choice(ENGAGEMENT_TEMPLATES) + "\n\n#trading #forex #crypto"


async def post_engagement_tweet(bot=None):
    now = datetime.now(timezone.utc)
    if not (8 <= now.hour <= 22):
        print("[Engagement] Outside posting hours, skipping")
        return

    text = await generate_engagement_post()
    print(f"[Engagement] Posting: {text[:60]}...")

    try:
        from x_cookies import post_tweet
        result = await post_tweet(text)
        print(f"[Engagement] {'✅' if result.success else '❌ ' + (result.error or '')}")
    except Exception as e:
        print(f"[Engagement] Error: {e}")
