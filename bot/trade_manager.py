"""
trade_manager.py — Autonomous Trade Engine (Serious Analyst Edition)

Three background jobs:
1. scan_markets()      — Scans 9 major pairs every 30 min. STRICT AI scoring.
                         Posts ONLY the single highest-conviction setup.
                         Posts immediately to Telegram + X (Make.com).
2. monitor_positions() — Checks TP/SL every 5 minutes.
                         TP hit → "BOOM!" celebration to Telegram only.
                         SL hit → silent close, no post.
3. daily_wrapup()      — 23:00 UTC. Compiles winning trades, posts to Make.com (X).
"""

import json
import httpx
import asyncio
from datetime import datetime, timezone
from database import SessionLocal, Trade
from config import MAKE_WEBHOOK_URL, FREE_CHANNEL_ID

import os
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_KEYS = [k for k in [
    os.getenv("GROQ_API_KEY", ""),
    os.getenv("groq_api_key", ""),
    os.getenv("groq", ""),
] if k]

# ── Full professional asset basket ─────────────────────────────────────────────
ASSET_BASKET = [
    "EUR_USD", "USD_JPY", "GBP_USD", "USD_CHF",
    "AUD_USD", "USD_CAD", "GOLD", "SILVER", "BTC"
]

YAHOO_SYMBOLS = {
    "EUR_USD": "EURUSD=X",
    "USD_JPY": "JPY=X",
    "GBP_USD": "GBPUSD=X",
    "USD_CHF": "CHFUSD=X",
    "AUD_USD": "AUDUSD=X",
    "USD_CAD": "CAD=X",
    "GOLD":    "GC=F",
    "SILVER":  "SI=F",
    "BTC":     "BTC-USD",
}

# Pip sizes for profit calculation
PIP_SIZE = {
    "EUR_USD": 0.0001,
    "USD_JPY": 0.01,
    "GBP_USD": 0.0001,
    "USD_CHF": 0.0001,
    "AUD_USD": 0.0001,
    "USD_CAD": 0.0001,
    "GOLD":    0.01,
    "SILVER":  0.001,
    "BTC":     1.0,
}


async def fetch_price(asset: str) -> float | None:
    yahoo_sym = YAHOO_SYMBOLS.get(asset)
    if not yahoo_sym:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}?interval=1m&range=1d"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            data = resp.json()
            return data["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except Exception as e:
        print(f"[fetch_price] {asset}: {e}")
        return None


async def fetch_all_prices() -> dict:
    results = await asyncio.gather(*[fetch_price(a) for a in ASSET_BASKET], return_exceptions=True)
    return {a: r for a, r in zip(ASSET_BASKET, results) if isinstance(r, float)}


async def analyze_all_assets(prices: dict) -> list[dict]:
    """
    Calls Groq with a strict institutional-grade prompt.
    Most scores should be 30-65. Only a genuine, multi-confluence
    confluence setup deserves 80+. Returns all setups sorted by conviction.
    """
    if not _GROQ_KEYS:
        return []

    system_prompt = """
You are a senior quantitative analyst at a tier-1 prop firm (similar to FTMO or The Funded Trader).
Your job is to scan major currency pairs, commodities, and crypto for REAL, high-probability trade setups.

METHODOLOGY — ICT/SMC Framework:
- Higher timeframe bias first (Weekly → Daily → 4H)
- Look for: sweeps of liquidity (equal highs/lows), Fair Value Gaps (FVG), Order Blocks (OB)
- Confirm with: Break of Structure (BOS) or Change of Character (CHoCH)
- Premium/Discount zones relative to swing range
- Avoid trading into strong opposing structure

CONVICTION SCORING CRITERIA (BE STRICT — most assets should score 30-60):
- 85-100: EXCEPTIONAL. Minimum 4 confluences. Clear higher timeframe alignment. Obvious liquidity sweep. 
          Low-risk entry with defined 1:2+ RRR. Active trading session. This should be RARE.
- 70-84:  GOOD. 3 solid confluences. Clear direction. Decent RRR. Post only if nothing scores higher.
- 50-69:  AVERAGE. Possible setup but lacks confluence or clarity. Do NOT post.
- 0-49:   Choppy, unclear, or risky. Ignore.

RULES:
- NEVER output HOLD. Every asset gets a BUY or SELL score.
- If a market is ranging and unclear, give it a LOW score (20-40) — don't force a setup.
- Be realistic about TP/SL. TP should be a real liquidity target (equal highs/lows, FVG fill).
- SL must be behind real structure (beyond the swing that would invalidate the setup).
- RRR must be at least 1:1.5. If you can't find a clean 1:1.5, give the asset a low score.
- The 'analysis' must sound like a real trader's brief. Mention SPECIFIC price levels. No fluff.
- WRITE IN PLAIN TEXT in the analysis field. No markdown, no asterisks, no hashtags.

Output STRICT JSON array (no markdown wrapper, just raw JSON):
[
  {
    "asset": "EUR_USD",
    "direction": "BUY",
    "conviction": 42,
    "entry_price": 1.0921,
    "tp_price": 1.0960,
    "sl_price": 1.0895,
    "analysis": "EURUSD is ranging between 1.0880-1.0960. No clear structure break. Avoiding."
  }
]
Include ALL assets in the response — even the low-scoring ones. That allows me to pick the best one.
"""

    user_prompt = (
        f"Current UTC time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}\n"
        f"Live prices:\n{json.dumps(prices, indent=2)}\n\n"
        f"Score ALL 9 assets and return your full analysis array."
    )

    for i, key in enumerate(_GROQ_KEYS):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                payload = {
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.3,  # Lower temperature = more consistent, less hallucinated
                    "response_format": {"type": "json_object"}
                }
                resp = await client.post(
                    GROQ_API_URL,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload
                )
                if resp.status_code == 429:
                    continue
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(raw)
                # Handle both {"setups": [...]} and plain [...]
                if isinstance(parsed, dict):
                    for v in parsed.values():
                        if isinstance(v, list):
                            parsed = v
                            break
                if isinstance(parsed, list):
                    # Sort by conviction descending
                    parsed.sort(key=lambda x: x.get("conviction", 0), reverse=True)
                    return parsed
        except Exception as e:
            print(f"[analyze_all_assets] Key {i+1} error: {e}")
            continue
    return []


def calculate_profit(asset: str, entry: float, close: float, direction: str):
    """Returns (pips, pct)"""
    diff = (close - entry) if direction == "BUY" else (entry - close)
    pip_size = PIP_SIZE.get(asset, 0.0001)
    return round(diff / pip_size, 1), round((diff / entry) * 100, 3)


# ══════════════════════════════════════════════════════════════════════════════
# JOB 1: scan_markets — posts the SINGLE best setup
# ══════════════════════════════════════════════════════════════════════════════
async def scan_markets(bot):
    from chart_generator import get_chart_for_asset
    from aiogram.types import BufferedInputFile
    import html as html_module

    print("[Scanner] Starting full market scan of 9 assets...")
    prices = await fetch_all_prices()
    if not prices:
        print("[Scanner] No prices fetched. Aborting.")
        return

    all_setups = await analyze_all_assets(prices)
    if not all_setups:
        print("[Scanner] AI returned no setups.")
        return

    # Log what the AI found
    for s in all_setups:
        print(f"  {s['asset']:10s} {s['direction']:4s} conviction={s['conviction']}")

    # Pick ONLY the single best setup with conviction >= 75
    best = all_setups[0]
    if best.get("conviction", 0) < 75:
        print(f"[Scanner] Best setup ({best['asset']}) only scored {best['conviction']}. Not posting — no clean setup today.")
        return

    asset     = best["asset"]
    direction = best["direction"].upper()

    # Don't open duplicate trades on same asset
    db = SessionLocal()
    try:
        existing = db.query(Trade).filter(Trade.asset == asset, Trade.status == "OPEN").first()
        if existing:
            print(f"[Scanner] Already tracking open trade on {asset}. Skipping.")
            return

        trade = Trade(
            asset=asset,
            direction=direction,
            entry_price=str(best["entry_price"]),
            tp_price=str(best["tp_price"]),
            sl_price=str(best["sl_price"]),
            status="OPEN",
            opened_at=datetime.now(timezone.utc).isoformat(),
        )
        db.add(trade)
        db.commit()
        db.refresh(trade)
        print(f"[Scanner] Trade #{trade.id} opened: {direction} {asset} @ {best['entry_price']}")
    finally:
        db.close()

    # Generate TradingView chart with the Long/Short Position tool drawn
    try:
        chart_bytes = await get_chart_for_asset(
            asset,
            entry=float(best["entry_price"]),
            tp=float(best["tp_price"]),
            sl=float(best["sl_price"]),
            direction=direction
        )
        print(f"[Scanner] Chart generated: {len(chart_bytes)} bytes")
    except Exception as e:
        print(f"[Scanner] Chart failed: {e}")
        chart_bytes = None

    # Build post text
    entry = float(best["entry_price"])
    tp    = float(best["tp_price"])
    sl    = float(best["sl_price"])
    risk  = abs(entry - sl)
    reward = abs(tp - entry)
    rrr   = f"1:{round(reward/risk, 1)}" if risk else "N/A"

    bias_emoji = "🟢" if direction == "BUY" else "🔴"
    arrow      = "🚀" if direction == "BUY" else "🔻"
    safe_analysis = html_module.escape(best.get("analysis", ""))
    conviction = best["conviction"]
    stars = "⭐" * min(5, conviction // 20)

    msg = (
        f"{arrow} <b>APEX SIGNAL — {asset.replace('_', '/')}</b> {arrow}\n\n"
        f"{bias_emoji} <b>{direction}</b>  |  Conviction: {stars} ({conviction}/100)\n\n"
        f"📍 <b>Entry:</b>  {best['entry_price']}\n"
        f"🎯 <b>TP:</b>     {best['tp_price']}\n"
        f"🛑 <b>SL:</b>     {best['sl_price']}\n"
        f"📊 <b>R:R:</b>    {rrr}\n\n"
        f"📋 {safe_analysis}\n\n"
        f"<i>Trade #{trade.id} • Project Apex • NFA</i>"
    )

    # Post to Telegram
    try:
        if chart_bytes:
            photo = BufferedInputFile(chart_bytes, filename=f"{asset}_signal.png")
            await bot.send_photo(chat_id=FREE_CHANNEL_ID, photo=photo, caption=msg, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=FREE_CHANNEL_ID, text=msg, parse_mode="HTML")
        print(f"[Scanner] Signal posted to Telegram.")
    except Exception as e:
        print(f"[Scanner] Telegram post failed: {e}")

    # Post IMMEDIATELY to Make.com (→ X/Twitter) as well
    if MAKE_WEBHOOK_URL:
        # X doesn't support HTML — strip tags for plain text
        import re
        plain_msg = re.sub(r'<[^>]+>', '', msg)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(MAKE_WEBHOOK_URL, json={"text": plain_msg})
            print("[Scanner] Signal sent to Make.com for X post.")
        except Exception as e:
            print(f"[Scanner] Make.com error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# JOB 2: monitor_positions
# ══════════════════════════════════════════════════════════════════════════════
async def monitor_positions(bot):
    from chart_generator import get_chart_for_asset
    from aiogram.types import BufferedInputFile
    import html as html_module

    db = SessionLocal()
    try:
        open_trades = db.query(Trade).filter(Trade.status == "OPEN").all()
        if not open_trades:
            return

        for trade in open_trades:
            current_price = await fetch_price(trade.asset)
            if not current_price:
                continue

            entry = float(trade.entry_price)
            tp    = float(trade.tp_price)
            sl    = float(trade.sl_price)

            tp_hit = (trade.direction == "BUY"  and current_price >= tp) or \
                     (trade.direction == "SELL" and current_price <= tp)
            sl_hit = (trade.direction == "BUY"  and current_price <= sl) or \
                     (trade.direction == "SELL" and current_price >= sl)

            if tp_hit:
                pips, pct = calculate_profit(trade.asset, entry, tp, trade.direction)
                trade.status = "WON"
                trade.closed_at = datetime.now(timezone.utc).isoformat()
                db.commit()
                print(f"[Monitor] Trade #{trade.id} {trade.asset} TP HIT! +{pips} pips ({pct}%)")

                try:
                    chart_bytes = await get_chart_for_asset(trade.asset)
                except Exception:
                    chart_bytes = None

                why = await generate_tp_explanation(trade, pips, pct)

                msg = (
                    f"💥 <b>BOOM BOOM! TP SMASHED!</b> 💥\n\n"
                    f"{'🟢' if trade.direction=='BUY' else '🔴'} "
                    f"<b>{trade.asset.replace('_', '/')} {trade.direction}</b>\n\n"
                    f"📍 Entry: {entry}\n"
                    f"🎯 TP: {tp} ✅ <b>REACHED</b>\n\n"
                    f"💰 <b>+{pips} pips (+{pct}%)</b>\n\n"
                    f"📖 {html_module.escape(why)}\n\n"
                    f"<i>Track record #{trade.id} • Project Apex</i>"
                )
                try:
                    if chart_bytes:
                        photo = BufferedInputFile(chart_bytes, filename=f"{trade.asset}_tp.png")
                        await bot.send_photo(chat_id=FREE_CHANNEL_ID, photo=photo, caption=msg, parse_mode="HTML")
                    else:
                        await bot.send_message(chat_id=FREE_CHANNEL_ID, text=msg, parse_mode="HTML")
                except Exception as e:
                    print(f"[Monitor] BOOM post failed: {e}")

            elif sl_hit:
                trade.status = "LOST"
                trade.closed_at = datetime.now(timezone.utc).isoformat()
                db.commit()
                print(f"[Monitor] Trade #{trade.id} {trade.asset} SL hit. Closed silently.")
    finally:
        db.close()


async def generate_tp_explanation(trade, pips, pct) -> str:
    if not _GROQ_KEYS:
        return "Price delivered exactly to our target. Structure played out perfectly."
    prompt = (
        f"Our {trade.direction} trade on {trade.asset} from {trade.entry_price} "
        f"just hit TP at {trade.tp_price}, making +{pips} pips (+{pct}%). "
        f"Write 2-3 punchy, human sentences explaining WHY this worked from an ICT/SMC perspective. "
        f"Reference the price levels. Sound confident but humble. No markdown."
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {_GROQ_KEYS[0]}", "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}]}
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return "Price delivered precisely into our target zone. The structure didn't lie."


# ══════════════════════════════════════════════════════════════════════════════
# JOB 3: daily_wrapup — posts winning trades summary to X at 23:00 UTC
# ══════════════════════════════════════════════════════════════════════════════
async def daily_wrapup(bot):
    import re
    import html as html_module

    print("[Wrapup] Compiling today's winning trades...")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        won_trades = db.query(Trade).filter(
            Trade.status == "WON",
            Trade.closed_at.startswith(today)
        ).all()

        if not won_trades:
            print("[Wrapup] No winners today. Skipping.")
            return

        total_pips = 0.0
        trade_lines = []
        for t in won_trades:
            pips, pct = calculate_profit(t.asset, float(t.entry_price), float(t.tp_price), t.direction)
            total_pips += pips
            emoji = "🟢" if t.direction == "BUY" else "🔴"
            trade_lines.append(f"{emoji} {t.asset.replace('_', '/')} {t.direction} → +{pips} pips (+{pct}%)")

        lines = "\n".join(trade_lines)

        # X post (plain text)
        x_text = (
            f"📊 DAILY RECAP — PROJECT APEX\n\n"
            f"Today's verified winning signals:\n{lines}\n\n"
            f"Total: +{round(total_pips, 1)} pips\n\n"
            f"Real ICT/SMC analysis. No fake signals.\n"
            f"Join the free Telegram for live alerts 👇"
        )

        if MAKE_WEBHOOK_URL:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    await client.post(MAKE_WEBHOOK_URL, json={"text": x_text})
                print("[Wrapup] Daily recap sent to Make.com for X.")
            except Exception as e:
                print(f"[Wrapup] Make.com error: {e}")

        # Telegram summary (HTML formatted)
        tg_msg = (
            f"📊 <b>DAILY RECAP</b> 📊\n\n"
            f"<b>Today's winning trades:</b>\n{html_module.escape(lines)}\n\n"
            f"<b>Total: +{round(total_pips, 1)} pips 💰</b>\n\n"
            f"<i>Only winners get posted. No sugar-coating. Project Apex.</i>"
        )
        try:
            await bot.send_message(chat_id=FREE_CHANNEL_ID, text=tg_msg, parse_mode="HTML")
        except Exception as e:
            print(f"[Wrapup] Telegram summary failed: {e}")
    finally:
        db.close()
