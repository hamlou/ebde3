"""
trade_tracker.py — Track mirrored trades and post RESULTS to X

Flow:
1. Signal arrives from wolfoftrading → parse entry/targets/SL
2. Store in local DB (JSON file)
3. Monitor prices via Binance API
4. 50% target hit → screenshot + explain strategy + post to X
5. 100% target hit → short update to X
6. SL hit → skip (no post)
"""

import os
import json
import asyncio
import re
import httpx
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
FREE_CHANNEL_LINK = os.getenv("FREE_CHANNEL_LINK", "")
TRADES_FILE = Path(__file__).parent.parent / "tracked_trades.json"


@dataclass
class TrackedTrade:
    id: str
    pair: str  # e.g., "BTCUSDT"
    direction: str  # "LONG" or "SHORT"
    entry: float
    targets: list[float]  # [tp1, tp2, tp3...]
    sl: float
    leverage: str  # e.g., "10x"
    original_text: str
    created_at: str
    posted_50pct: bool = False
    posted_100pct: bool = False
    status: str = "OPEN"  # OPEN, WON, LOST


def load_trades() -> list[dict]:
    if not TRADES_FILE.exists():
        return []
    with open(TRADES_FILE) as f:
        return json.load(f)


def save_trades(trades: list[dict]):
    with open(TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=2)


def add_trade(trade: TrackedTrade):
    trades = load_trades()
    trades.append(asdict(trade))
    save_trades(trades)


def update_trade(trade_id: str, updates: dict):
    trades = load_trades()
    for t in trades:
        if t["id"] == trade_id:
            t.update(updates)
            break
    save_trades(trades)


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL PARSING — Extract trade info from wolfoftrading messages
# ══════════════════════════════════════════════════════════════════════════════

def parse_signal(text: str) -> Optional[TrackedTrade]:
    """
    Parse wolfoftrading signal format:

    $BTCUSDT Long
    Leverage: Isolated x10
    Entry: 95000-96000
    Targets: 97000, 98000, 100000
    SL: 93000
    """
    text_lower = text.lower()

    # Must have entry and target
    if "entry" not in text_lower or ("target" not in text_lower and "tp" not in text_lower):
        return None

    # Extract pair
    pair_match = re.search(r'\$([A-Z0-9]+)', text, re.IGNORECASE)
    if not pair_match:
        return None
    pair = pair_match.group(1).upper()
    if not pair.endswith("USDT"):
        pair += "USDT"

    # Extract direction
    direction = "LONG"
    if "short" in text_lower:
        direction = "SHORT"

    # Extract entry (take average if range)
    entry_match = re.search(r'entry[:\s]*([0-9.,\-\s]+)', text_lower)
    entry = 0.0
    if entry_match:
        entry_str = entry_match.group(1).strip()
        # Handle range like "95000-96000"
        if "-" in entry_str:
            parts = entry_str.split("-")
            try:
                entry = (float(parts[0].replace(",", "").strip()) +
                        float(parts[1].replace(",", "").strip())) / 2
            except:
                pass
        else:
            try:
                entry = float(entry_str.replace(",", "").strip())
            except:
                pass

    # Extract targets
    targets = []
    # Try multiple patterns
    target_patterns = [
        r'targets?[:\s]*([0-9.,\s]+)',
        r'tp[:\s]*([0-9.,\s]+)',
        r'take\s*profit[:\s]*([0-9.,\s]+)',
    ]
    for pattern in target_patterns:
        match = re.search(pattern, text_lower)
        if match:
            targets_str = match.group(1)
            # Split by comma, space, or newline
            parts = re.split(r'[,\s\n]+', targets_str)
            for p in parts:
                try:
                    val = float(p.replace(",", "").strip())
                    if val > 0:
                        targets.append(val)
                except:
                    pass
            if targets:
                break

    # Extract SL
    sl = 0.0
    sl_match = re.search(r'(?:sl|stop\s*loss)[:\s]*([0-9.,]+)', text_lower)
    if sl_match:
        try:
            sl = float(sl_match.group(1).replace(",", "").strip())
        except:
            pass

    # Extract leverage
    leverage = ""
    lev_match = re.search(r'(?:leverage|lev)[:\s]*(?:isolated\s*)?x?(\d+)', text_lower)
    if lev_match:
        leverage = f"{lev_match.group(1)}x"

    if not entry or not targets:
        return None

    return TrackedTrade(
        id=f"{pair}_{int(datetime.now().timestamp())}",
        pair=pair,
        direction=direction,
        entry=entry,
        targets=sorted(targets) if direction == "LONG" else sorted(targets, reverse=True),
        sl=sl,
        leverage=leverage,
        original_text=text[:500],
        created_at=datetime.now(timezone.utc).isoformat(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# PRICE MONITORING — Binance API
# ══════════════════════════════════════════════════════════════════════════════

async def get_current_price(pair: str) -> Optional[float]:
    """Get current price from Binance."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.binance.com/api/v3/ticker/price",
                params={"symbol": pair}
            )
            if resp.status_code == 200:
                return float(resp.json()["price"])
    except Exception as e:
        print(f"[Price] Error fetching {pair}: {e}")
    return None


def calculate_progress(trade: dict, current_price: float) -> float:
    """
    Calculate % progress toward first target.
    Returns 0-100+ (can exceed 100 if past target)
    """
    entry = trade["entry"]
    targets = trade["targets"]
    if not targets:
        return 0

    first_target = targets[0]

    if trade["direction"] == "LONG":
        total_distance = first_target - entry
        current_distance = current_price - entry
    else:
        total_distance = entry - first_target
        current_distance = entry - current_price

    if total_distance <= 0:
        return 0

    return (current_distance / total_distance) * 100


def check_sl_hit(trade: dict, current_price: float) -> bool:
    """Check if stop loss was hit."""
    if not trade["sl"]:
        return False

    if trade["direction"] == "LONG":
        return current_price <= trade["sl"]
    else:
        return current_price >= trade["sl"]


# ══════════════════════════════════════════════════════════════════════════════
# CONTENT GENERATION — Human-like results posts
# ══════════════════════════════════════════════════════════════════════════════

async def generate_50pct_post(trade: dict, current_price: float, profit_pct: float) -> str:
    """
    Generate human-like post for 50% target hit.
    Explains strategy, sounds natural, invites to TG.
    """
    if not GROQ_API_KEY:
        return fallback_50pct_post(trade, current_price, profit_pct)

    try:
        prompt = f"""You are a crypto trader sharing results on Twitter. Write a post about a winning trade.

TRADE INFO:
- Pair: {trade['pair'].replace('USDT', '/USDT')}
- Direction: {trade['direction']}
- Entry: {trade['entry']}
- Current price: {current_price}
- Profit so far: +{profit_pct:.1f}%
- Target was: {trade['targets'][0]}
- We're at 50% of target

RULES:
- Sound like a REAL human trader, not a bot
- Explain briefly WHY this trade worked (support/resistance, trend, breakout, etc.)
- Keep it casual and confident but not arrogant
- Max 250 characters
- Use 1-2 emojis MAX (not more)
- End with 2-3 relevant hashtags (e.g. #crypto #BTC #forex #trading #XAUUSD — pick what fits the pair)
- End with something like "Join our free TG for signals" (don't include the actual link, I'll add it)
- Don't say "50% target" explicitly, just show the profit

Example tone: "BTC long from 95k paying off nicely. Grabbed the bounce off support like clockwork. +3.2% and letting it ride. Free signals in our TG."

OUTPUT: Just the post text, nothing else."""

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "groq/compound-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 120,
                    "temperature": 0.85
                }
            )
            data = resp.json()

        result = data["choices"][0]["message"]["content"].strip().strip('"\'')

        from attribution import generate_attribution_link
        attr_link, _ = generate_attribution_link(trade["pair"], "wolf_50pct")
        result += f"\n\n{attr_link}"

        return result[:350]

    except Exception as e:
        print(f"[AI] 50% post failed: {e}")
        return fallback_50pct_post(trade, current_price, profit_pct)


def _pair_hashtags(pair: str) -> str:
    p = pair.upper()
    tags = ["#trading"]
    if any(x in p for x in ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"]):
        tags.append("#crypto")
        if "BTC" in p: tags.append("#BTC")
        elif "ETH" in p: tags.append("#ETH")
        else: tags.append(f"#{p.replace('USDT','')}")
    elif any(x in p for x in ["XAU", "GOLD"]):
        tags.extend(["#gold", "#XAUUSD"])
    elif any(x in p for x in ["EUR", "GBP", "JPY", "USD", "AUD", "NZD", "CHF", "CAD"]):
        tags.append("#forex")
        tags.append(f"#{p.replace('_','').replace('/','')[:6]}")
    else:
        tags.append("#forex")
    return " ".join(tags[:3])


def fallback_50pct_post(trade: dict, current_price: float, profit_pct: float) -> str:
    pair = trade["pair"].replace("USDT", "/USDT")
    direction = trade["direction"].lower()
    hashtags = _pair_hashtags(trade["pair"])

    from attribution import generate_attribution_link
    attr_link, _ = generate_attribution_link(trade["pair"], "wolf_50pct_fallback")

    templates = [
        f"{pair} {direction} from {trade['entry']} running well. +{profit_pct:.1f}% so far, targeting more. Caught the move clean.\n\nFree signals: {attr_link}\n\n{hashtags}",
        f"Nice move on {pair}. Entered {direction} at {trade['entry']}, now at {current_price}. +{profit_pct:.1f}% and holding.\n\nJoin free TG: {attr_link}\n\n{hashtags}",
        f"{pair} doing exactly what we expected. {direction.title()} entry at {trade['entry']} now +{profit_pct:.1f}%. Structure played out perfectly.\n\nSignals: {attr_link}\n\n{hashtags}",
    ]

    import random
    return random.choice(templates)


async def generate_100pct_post(trade: dict, current_price: float, profit_pct: float) -> str:
    pair = trade["pair"].replace("USDT", "/USDT")
    hashtags = _pair_hashtags(trade["pair"])

    from attribution import generate_attribution_link
    attr_link, _ = generate_attribution_link(trade["pair"], "wolf_100pct")

    templates = [
        f"🎯 {pair} target hit. +{profit_pct:.1f}% in the bag.\n\n{attr_link}\n\n{hashtags}",
        f"💰 Full TP on {pair}. +{profit_pct:.1f}% done.\n\n{attr_link}\n\n{hashtags}",
        f"✅ {pair} closed at target. Another clean +{profit_pct:.1f}%.\n\n{attr_link}\n\n{hashtags}",
        f"🔥 Target reached on {pair}. +{profit_pct:.1f}%\n\n{attr_link}\n\n{hashtags}",
    ]

    import random
    return random.choice(templates)


# ══════════════════════════════════════════════════════════════════════════════
# CHART SCREENSHOT — TradingView via Playwright
# ══════════════════════════════════════════════════════════════════════════════

async def capture_trade_chart(trade: dict, current_price: float, profit_pct: float) -> Optional[bytes]:
    try:
        from chart_screenshot import capture_trade_chart as _capture
        return await _capture(
            pair=trade["pair"],
            direction=trade["direction"],
            entry=trade["entry"],
            targets=trade["targets"],
            sl=trade["sl"],
            current_price=current_price,
            profit_pct=profit_pct,
        )
    except Exception as e:
        print(f"[Chart] Screenshot failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# X POSTING
# ══════════════════════════════════════════════════════════════════════════════

async def post_to_x(text: str, image_bytes: bytes = None) -> bool:
    """Post to X using cookies."""
    try:
        from x_cookies import post_tweet
        import tempfile

        image_path = None
        if image_bytes:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(image_bytes)
                image_path = f.name

        result = await post_tweet(text, image_path)

        if image_path:
            try:
                os.unlink(image_path)
            except:
                pass

        return result.success

    except Exception as e:
        print(f"[X] Post failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN MONITOR LOOP
# ══════════════════════════════════════════════════════════════════════════════

async def monitor_trades():
    """
    Main loop: check all open trades, post results when targets hit.
    Run this every 1-2 minutes.
    """
    trades = load_trades()

    for trade in trades:
        if trade["status"] != "OPEN":
            continue

        price = await get_current_price(trade["pair"])
        if not price:
            continue

        # Check SL first
        if check_sl_hit(trade, price):
            print(f"[Monitor] {trade['pair']} SL hit. Skipping (no post).")
            update_trade(trade["id"], {"status": "LOST"})

            # Notify about SL hit
            try:
                from notify_bot import notify_sl_hit
                await notify_sl_hit(trade["pair"], trade["entry"], trade["sl"], trade["direction"])
            except Exception as e:
                print(f"[Notify] Error: {e}")

            continue

        # Calculate progress
        progress = calculate_progress(trade, price)

        # Calculate profit %
        if trade["direction"] == "LONG":
            profit_pct = ((price - trade["entry"]) / trade["entry"]) * 100
        else:
            profit_pct = ((trade["entry"] - price) / trade["entry"]) * 100

        # 50% target hit
        if progress >= 50 and not trade["posted_50pct"]:
            print(f"[Monitor] {trade['pair']} 50% target! Posting to X...")

            # Generate post
            post_text = await generate_50pct_post(trade, price, profit_pct)

            # Capture chart with TP/SL zones
            chart = await capture_trade_chart(trade, price, profit_pct)

            # Post to X
            success = await post_to_x(post_text, chart)
            print(f"[X] 50% post: {'OK' if success else 'FAIL'}")

            update_trade(trade["id"], {"posted_50pct": True})

            # Notify about 50% posted
            if success:
                try:
                    from notify_bot import notify_50pct_posted
                    await notify_50pct_posted(trade["pair"], profit_pct, price)
                except Exception as e:
                    print(f"[Notify] Error: {e}")

        # 100% target hit
        if progress >= 100 and not trade["posted_100pct"]:
            print(f"[Monitor] {trade['pair']} 100% target! Short update to X...")

            post_text = await generate_100pct_post(trade, price, profit_pct)

            chart = await capture_trade_chart(trade, price, profit_pct)
            success = await post_to_x(post_text, chart)
            print(f"[X] 100% post: {'OK' if success else 'FAIL'}")

            update_trade(trade["id"], {"posted_100pct": True, "status": "WON"})

            # Notify about 100% posted
            if success:
                try:
                    from notify_bot import notify_100pct_posted
                    await notify_100pct_posted(trade["pair"], profit_pct)
                except Exception as e:
                    print(f"[Notify] Error: {e}")

        await asyncio.sleep(0.5)


async def run_tracker_daemon():
    """Run the tracker as a background daemon."""
    print("=" * 50)
    print("TRADE TRACKER — Results to X")
    print("50% target → explain strategy + invite to TG")
    print("100% target → short update")
    print("SL hit → skip")
    print("=" * 50)

    while True:
        try:
            await monitor_trades()
        except Exception as e:
            print(f"[Tracker] Error: {e}")

        await asyncio.sleep(60)  # Check every minute


if __name__ == "__main__":
    asyncio.run(run_tracker_daemon())
