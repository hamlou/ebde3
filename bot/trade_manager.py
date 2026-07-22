"""
trade_manager.py — Autonomous Trade Engine

This module powers three independent background jobs:

1. scan_markets()    — Scans 7 assets every ~30 min, uses Groq AI to analyze
                       market structure (ICT/SMC), generates a conviction score,
                       and opens a simulated trade if score > 80.

2. monitor_positions() — Runs every 5 minutes. Checks live prices against all
                         OPEN trades. If TP is hit: posts a "BOOM!" Telegram
                         message with analysis. If SL is hit: silently closes
                         the trade, no post.

3. daily_wrapup()    — Runs once per day at 23:00 UTC. Compiles all WON trades,
                       calculates total pips/%, and sends a daily summary post
                       with all winning screenshots to Make.com for X/Twitter.
"""

import json
import httpx
import asyncio
from datetime import datetime, timezone
from database import SessionLocal, Trade
from config import MAKE_WEBHOOK_URL, FREE_CHANNEL_ID

# ── Groq Configuration ────────────────────────────────────────────────────────
import os
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_KEYS = [k for k in [
    os.getenv("GROQ_API_KEY", ""),
    os.getenv("groq_api_key", ""),
    os.getenv("groq", ""),
] if k]

# ── Asset Basket to scan ──────────────────────────────────────────────────────
ASSET_BASKET = ["BTC", "ETH", "GOLD", "SILVER", "EUR_USD", "GBP_USD"]

# ── Pip value per asset (for profit calculation) ──────────────────────────────
# These are the pip sizes for calculating gains
PIP_SIZE = {
    "BTC":     1.0,   # $1 per pip
    "ETH":     0.01,  # $0.01 per pip  
    "GOLD":    0.01,  # $0.01 per pip (XAU/USD)
    "SILVER":  0.001,
    "EUR_USD": 0.0001, # 1 pip = 0.0001
    "GBP_USD": 0.0001,
}


# ── Fetch live prices from Yahoo Finance ──────────────────────────────────────
YAHOO_SYMBOLS = {
    "BTC":     "BTC-USD",
    "ETH":     "ETH-USD",
    "GOLD":    "GC=F",
    "SILVER":  "SI=F",
    "EUR_USD": "EURUSD=X",
    "GBP_USD": "GBPUSD=X",
}

async def fetch_price(asset: str) -> float | None:
    """Fetch the current live price of an asset."""
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
        print(f"[fetch_price] Error for {asset}: {e}")
        return None


async def fetch_all_prices() -> dict:
    """Fetch live prices for all assets in the basket simultaneously."""
    tasks = {asset: fetch_price(asset) for asset in ASSET_BASKET}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    prices = {}
    for asset, result in zip(tasks.keys(), results):
        if isinstance(result, float):
            prices[asset] = result
    return prices


# ── AI Analysis Engine ─────────────────────────────────────────────────────────
async def analyze_all_assets(prices: dict) -> list[dict]:
    """
    Uses Groq to analyze multiple assets and return a ranked list of
    trade setups with conviction scores. Only BUY or SELL — never HOLD.
    """
    if not _GROQ_KEYS:
        print("[analyze_all_assets] No Groq keys!")
        return []

    system_prompt = """
    You are a professional institutional trader at a top prop firm. You specialize in 
    ICT (Inner Circle Trader) and SMC (Smart Money Concepts) methodology.
    
    Given a set of real-time asset prices, you must analyze each one for potential 
    high-conviction trade setups based on:
    - Market structure (BOS, CHoCH, inducement)
    - Liquidity sweeps (sell-side / buy-side)
    - Fair Value Gaps (FVGs) that price may want to fill
    - Premium / discount zones
    - Confluence between multiple factors
    
    Rules:
    - NEVER output HOLD. Every asset is either BUY or SELL.
    - Only include assets with genuine conviction (score >= 80)
    - Be specific about WHY the trade is valid (reference price levels)
    - TP should be 1.5x to 2x the distance of the SL from entry (positive RRR)
    - Write in punchy, direct trader language. No fluff. No hedging.
    - The 'analysis' field is posted publicly — sound confident but say NFA at the end.
    
    Output STRICT JSON array. No markdown, no code blocks:
    [
      {
        "asset": "GOLD",
        "direction": "BUY",
        "conviction": 92,
        "entry_price": 2350.50,
        "tp_price": 2368.00,
        "sl_price": 2342.00,
        "analysis": "Gold swept the daily low at 2342, reclaimed the 4H OB, and is now trading in discount relative to the weekly range. FVG sits between 2348-2352. Expecting a move to sweep the equal highs at 2368 once this FVG is cleared. NFA."
      }
    ]
    Only include assets where conviction >= 80. Return empty array [] if nothing qualifies.
    """
    
    user_prompt = f"Current live prices:\n{json.dumps(prices, indent=2)}\n\nAnalyze each asset and return trade setups."

    for i, key in enumerate(_GROQ_KEYS):
        try:
            async with httpx.AsyncClient(timeout=40.0) as client:
                payload = {
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "response_format": {"type": "json_object"}
                }
                resp = await client.post(
                    GROQ_API_URL,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload
                )
                if resp.status_code == 429:
                    print(f"Groq Key {i+1} rate limited, trying next...")
                    continue
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(raw)
                # Handle both {"setups": [...]} and plain [...] response formats
                if isinstance(parsed, dict):
                    setups = parsed.get("setups", parsed.get("trades", list(parsed.values())[0] if parsed else []))
                else:
                    setups = parsed
                return [s for s in setups if isinstance(s, dict) and s.get("conviction", 0) >= 80]
        except Exception as e:
            print(f"[analyze_all_assets] Error with key {i+1}: {e}")
            continue
    return []


# ── pip/% profit calculator ───────────────────────────────────────────────────
def calculate_profit(asset: str, entry: float, close: float, direction: str) -> tuple[float, float]:
    """
    Returns (pips_won, percent_gain).
    pips_won is calculated using the PIP_SIZE for the asset.
    """
    if direction == "BUY":
        raw_diff = close - entry
    else:
        raw_diff = entry - close
    
    pip_size = PIP_SIZE.get(asset, 0.0001)
    pips = round(raw_diff / pip_size, 1)
    pct = round((raw_diff / entry) * 100, 2)
    return pips, pct


# ══════════════════════════════════════════════════════════════════════════════
# JOB 1: scan_markets
# ══════════════════════════════════════════════════════════════════════════════
async def scan_markets(bot):
    """
    Runs on a schedule. Fetches prices, asks AI for setups, 
    opens trades, generates TradingView screenshots, posts signals to Telegram.
    """
    from chart_generator import get_chart_for_asset
    from aiogram.types import BufferedInputFile
    import html as html_module

    print("[Scanner] Starting market scan...")
    
    prices = await fetch_all_prices()
    if not prices:
        print("[Scanner] Could not fetch any prices. Skipping.")
        return
    
    setups = await analyze_all_assets(prices)
    if not setups:
        print("[Scanner] No high-conviction setups found this scan.")
        return
    
    # Sort by conviction, highest first
    setups.sort(key=lambda x: x.get("conviction", 0), reverse=True)
    print(f"[Scanner] Found {len(setups)} valid setup(s): {[s['asset'] for s in setups]}")
    
    db = SessionLocal()
    try:
        for setup in setups:
            asset = setup["asset"]
            direction = setup["direction"].upper()
            
            # Don't open duplicate trades on same asset
            existing = db.query(Trade).filter(
                Trade.asset == asset,
                Trade.status == "OPEN"
            ).first()
            if existing:
                print(f"[Scanner] Already have open trade on {asset}, skipping.")
                continue
            
            # Save trade to database
            trade = Trade(
                asset=asset,
                direction=direction,
                entry_price=str(setup["entry_price"]),
                tp_price=str(setup["tp_price"]),
                sl_price=str(setup["sl_price"]),
                status="OPEN",
                opened_at=datetime.now(timezone.utc).isoformat(),
            )
            db.add(trade)
            db.commit()
            db.refresh(trade)
            print(f"[Scanner] Opened trade #{trade.id}: {direction} {asset} @ {setup['entry_price']}")
            
            # Generate TradingView screenshot
            try:
                chart_bytes = await get_chart_for_asset(asset)
            except Exception as e:
                print(f"[Scanner] Chart generation failed for {asset}: {e}")
                chart_bytes = None
            
            # Build Telegram post
            bias_emoji = "🟢" if direction == "BUY" else "🔴"
            conviction_bar = "⭐" * (setup["conviction"] // 20)  # 1-5 stars
            safe_analysis = html_module.escape(setup.get("analysis", ""))
            
            entry = float(setup["entry_price"])
            tp    = float(setup["tp_price"])
            sl    = float(setup["sl_price"])
            risk  = abs(entry - sl)
            reward = abs(tp - entry)
            rrr   = round(reward / risk, 2) if risk else "N/A"
            
            msg = (
                f"{'🚀' if direction=='BUY' else '🔻'} <b>NEW SIGNAL — {asset}</b> {'🚀' if direction=='BUY' else '🔻'}\n\n"
                f"{bias_emoji} <b>Direction:</b> {direction}\n"
                f"💰 <b>Entry:</b> {setup['entry_price']}\n"
                f"🎯 <b>Take Profit:</b> {setup['tp_price']}\n"
                f"🛑 <b>Stop Loss:</b> {setup['sl_price']}\n"
                f"📊 <b>Risk/Reward:</b> 1:{rrr}\n"
                f"🔥 <b>Conviction:</b> {conviction_bar} ({setup['conviction']}/100)\n\n"
                f"📋 <b>Analysis:</b>\n{safe_analysis}\n\n"
                f"<i>Trade #{trade.id} • Project Apex</i>"
            )
            
            try:
                if chart_bytes:
                    photo = BufferedInputFile(chart_bytes, filename=f"{asset}_signal.png")
                    await bot.send_photo(chat_id=FREE_CHANNEL_ID, photo=photo, caption=msg, parse_mode="HTML")
                else:
                    await bot.send_message(chat_id=FREE_CHANNEL_ID, text=msg, parse_mode="HTML")
                print(f"[Scanner] Posted signal for {asset} to Telegram.")
            except Exception as e:
                print(f"[Scanner] Telegram post failed for {asset}: {e}")
            
            # Stagger posts to avoid Telegram flood limits
            await asyncio.sleep(2)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# JOB 2: monitor_positions
# ══════════════════════════════════════════════════════════════════════════════
async def monitor_positions(bot):
    """
    Runs every 5 minutes. Checks all OPEN trades against live prices.
    - TP hit → Post "BOOM!" celebration + analysis to Telegram, mark WON.
    - SL hit → Silently mark LOST. No post.
    """
    from chart_generator import get_chart_for_asset
    from aiogram.types import BufferedInputFile
    import html as html_module

    db = SessionLocal()
    try:
        open_trades = db.query(Trade).filter(Trade.status == "OPEN").all()
        if not open_trades:
            return
        
        # Fetch prices only for assets we have open trades on
        assets_needed = list(set(t.asset for t in open_trades))
        prices = {}
        for asset in assets_needed:
            price = await fetch_price(asset)
            if price:
                prices[asset] = price
        
        for trade in open_trades:
            current_price = prices.get(trade.asset)
            if not current_price:
                continue
            
            entry = float(trade.entry_price)
            tp    = float(trade.tp_price)
            sl    = float(trade.sl_price)
            direction = trade.direction
            
            tp_hit = (direction == "BUY"  and current_price >= tp) or \
                     (direction == "SELL" and current_price <= tp)
            sl_hit = (direction == "BUY"  and current_price <= sl) or \
                     (direction == "SELL" and current_price >= sl)
            
            if tp_hit:
                pips, pct = calculate_profit(trade.asset, entry, tp, direction)
                
                # Close trade as WON
                trade.status = "WON"
                trade.closed_at = datetime.now(timezone.utc).isoformat()
                db.commit()
                print(f"[Monitor] Trade #{trade.id} {trade.asset} HIT TP! +{pips} pips (+{pct}%)")
                
                # Generate chart
                try:
                    chart_bytes = await get_chart_for_asset(trade.asset)
                except Exception:
                    chart_bytes = None
                
                # Ask AI to generate a celebratory explanation of WHY the trade worked
                why_it_worked = await generate_tp_explanation(trade, pips, pct)
                safe_why = html_module.escape(why_it_worked)
                
                # Build "BOOM!" post
                msg = (
                    f"💥 <b>BOOM BOOM! TP HIT!</b> 💥\n\n"
                    f"{'🟢' if direction=='BUY' else '🔴'} <b>{trade.asset} {direction}</b>\n\n"
                    f"📍 <b>Entry:</b> {entry}\n"
                    f"🎯 <b>TP:</b> {tp}\n\n"
                    f"✅ <b>Result:</b> +{pips} pips (+{pct}%)\n\n"
                    f"📖 <b>Why it worked:</b>\n{safe_why}\n\n"
                    f"<i>Track record: Trade #{trade.id} • Project Apex</i>"
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
                print(f"[Monitor] Trade #{trade.id} {trade.asset} hit SL. Closed silently.")
    finally:
        db.close()


async def generate_tp_explanation(trade, pips: float, pct: float) -> str:
    """Ask the AI to write a brief, human-sounding explanation of why the trade worked."""
    if not _GROQ_KEYS:
        return f"Price delivered precisely to our target as the market structure confirmed our bias. Pure ICT execution."
    
    prompt = f"""
    A simulated trade just hit Take Profit:
    - Asset: {trade.asset}
    - Direction: {trade.direction}
    - Entry: {trade.entry_price}
    - Take Profit: {trade.tp_price} (REACHED)
    - Profit: +{pips} pips (+{pct}%)
    
    Write 2-3 punchy sentences (max 60 words) explaining WHY this trade worked from an ICT/SMC perspective.
    Talk about the price delivering into our target, the FVG fill, the liquidity sweep, the OB reaction, etc.
    Write like a confident but humble trader. Do NOT use the word "significant". End with a brief comment.
    Just return the plain text explanation, no JSON.
    """
    
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
            }
            resp = await client.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {_GROQ_KEYS[0]}", "Content-Type": "application/json"},
                json=payload
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[TP explanation] Error: {e}")
        return "Price delivered exactly as the market structure dictated. OB held perfectly."


# ══════════════════════════════════════════════════════════════════════════════
# JOB 3: daily_wrapup
# ══════════════════════════════════════════════════════════════════════════════
async def daily_wrapup(bot):
    """
    Runs once per day at 23:00 UTC.
    Compiles all WON trades from today, calculates total pips, 
    and posts a single summary to Make.com for X/Twitter.
    Only winning trades are included. Losing trades are never mentioned.
    """
    from chart_generator import get_chart_for_asset
    from aiogram.types import BufferedInputFile, MediaGroupBuilder
    import html as html_module

    print("[DailyWrapup] Compiling today's winning trades...")
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    db = SessionLocal()
    try:
        won_trades = db.query(Trade).filter(
            Trade.status == "WON",
            Trade.closed_at.startswith(today)
        ).all()
        
        if not won_trades:
            print("[DailyWrapup] No winning trades today. Skipping X post.")
            return
        
        # Calculate totals
        total_pips = 0.0
        trade_lines = []
        for t in won_trades:
            pips, pct = calculate_profit(t.asset, float(t.entry_price), float(t.tp_price), t.direction)
            total_pips += pips
            direction_emoji = "🟢" if t.direction == "BUY" else "🔴"
            trade_lines.append(f"{direction_emoji} {t.asset} {t.direction} → +{pips} pips (+{pct}%)")
        
        trades_text = "\n".join(trade_lines)
        
        # Build the X/Twitter post text
        x_text = (
            f"📊 DAILY REPORT — PROJECT APEX\n\n"
            f"🏆 Today's winning trades:\n{trades_text}\n\n"
            f"💰 Total: +{round(total_pips, 1)} pips today\n\n"
            f"📈 Full ICT/SMC analysis daily. Follow for more.\n"
            f"👇 Join the free Telegram channel ↓"
        )
        
        print(f"[DailyWrapup] Sending daily wrap-up to Make.com for X post...")
        
        if MAKE_WEBHOOK_URL:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    await client.post(MAKE_WEBHOOK_URL, json={"text": x_text})
                print("[DailyWrapup] Sent to Make.com successfully.")
            except Exception as e:
                print(f"[DailyWrapup] Make.com error: {e}")
        
        # Also post the summary to Telegram
        tg_summary = (
            f"📊 <b>DAILY WRAP-UP</b> 📊\n\n"
            f"<b>Today's winning trades:</b>\n{html_module.escape(trades_text)}\n\n"
            f"<b>Total: +{round(total_pips, 1)} pips today 💰</b>\n\n"
            f"<i>Only winners get posted. That's how we roll. Project Apex.</i>"
        )
        try:
            await bot.send_message(chat_id=FREE_CHANNEL_ID, text=tg_summary, parse_mode="HTML")
        except Exception as e:
            print(f"[DailyWrapup] Telegram summary post failed: {e}")
    finally:
        db.close()
