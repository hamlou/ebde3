"""
trade_manager.py — Autonomous Trade Engine (Real-Data Edition)

Three background jobs:
1. scan_markets()      — Fetches REAL OHLCV for 9 assets, computes actual SMC/TA
                         indicators, feeds structured data to AI. Posts ONLY the
                         single highest-conviction setup. Also fires to X immediately.
2. monitor_positions() — Every 5 min: checks TP/SL against live prices.
                         TP hit → Telegram "BOOM!" + analysis. SL → silent close.
3. daily_wrapup()      — 23:00 UTC: winning trade recap to Make.com (X).
"""

import json
import httpx
import asyncio
from datetime import datetime, timezone
from database import SessionLocal, Trade
import os
from config import MAKE_WEBHOOK_URL, FREE_CHANNEL_ID, VIP_CHANNEL_ID, GEMINI_KEYS

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_KEYS = [k for k in [
    os.getenv("GROQ_API_KEY", ""),
    os.getenv("groq_api_key", ""),
    os.getenv("groq", ""),
] if k]

ASSET_BASKET = [
    "EUR_USD", "USD_JPY", "GBP_USD", "USD_CHF",
    "AUD_USD", "USD_CAD", "GOLD", "SILVER", "BTC"
]

PIP_SIZE = {
    "EUR_USD": 0.0001, "USD_JPY": 0.01,  "GBP_USD": 0.0001,
    "USD_CHF": 0.0001, "AUD_USD": 0.0001,"USD_CAD": 0.0001,
    "GOLD":    0.01,   "SILVER":  0.001,  "BTC":     1.0,
}

YAHOO_PRICE_MAP = {
    "EUR_USD": "EURUSD=X", "USD_JPY": "JPY=X",    "GBP_USD": "GBPUSD=X",
    "USD_CHF": "CHF=X",    "AUD_USD": "AUDUSD=X", "USD_CAD": "CAD=X",
    "GOLD":    "GC=F",     "SILVER":  "SI=F",      "BTC":     "BTC-USD",
}


async def fetch_price(asset: str) -> float | None:
    sym = YAHOO_PRICE_MAP.get(asset)
    if not sym:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1m&range=1d"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except Exception as e:
        print(f"[fetch_price] {asset}: {e}")
        return None


def calculate_profit(asset: str, entry: float, close: float, direction: str):
    diff = (close - entry) if direction == "BUY" else (entry - close)
    pip  = PIP_SIZE.get(asset, 0.0001)
    return round(diff / pip, 1), round((diff / entry) * 100, 3)


async def analyze_with_real_data(contexts: list[dict]) -> list[dict]:
    """
    Passes REAL computed SMC/TA indicator data to the AI.
    The AI is no longer asked to imagine levels — it reads actual calculated numbers.
    """
    if not _GROQ_KEYS:
        return []

    system_prompt = """
You are a senior quantitative analyst at a tier-1 prop firm.
You are given REAL, machine-computed market data for 9 assets:
- SMC indicators: FVGs, Order Blocks, Break of Structure, CHoCH, Liquidity levels
- TA indicators: RSI-14, EMA-20/50/200, MACD direction
- Premium/Discount zone position

Your job is to READ these real numbers and identify the SINGLE best trade setup.

STRICT SCORING RULES:
- 85-100: Exceptional. HTF trend + 4H structure align + unmitigated FVG near price + OB as entry confluence + RSI not extreme + clean R:R ≥ 1:2. This is RARE.
- 70-84:  Strong. 3 solid confluences. Clear structure. R:R ≥ 1:1.5.
- 50-69:  Average. Possible but weak confluence. DO NOT POST.
- 0-49:   No setup, choppy, or high risk. Ignore.

ENTRY/TP/SL RULES:
- Entry: As close to the OB or FVG as possible. Not at mid-range.
- TP: The NEXT liquidity pool (unswept equal highs/lows) or opposite FVG.
- SL: BELOW/ABOVE the OB that defines the setup — not arbitrary.
- If R:R < 1.5, score it below 60. No exceptions.

RISK MANAGEMENT:
- Assign a `risk_pct` (e.g. 5.0, 10.0, 15.0).
- If conviction is 75-80, risk 5.0%. If 80-90, risk 10.0%. If 90+, risk 15.0%.
- If the R:R is weak or conviction is low, risk 0.0%.

QUALITY OVER QUANTITY:
- If there is no exceptionally clean trade across ALL 9 assets, return low conviction scores (<60) for everything. Do NOT force a trade if the market structure is messy. Only high-probability setups are acceptable.

ANALYSIS FIELD RULES:
- Reference ACTUAL numbers from the provided data (FVG levels, OB levels, BOS level).
- Maximum 3 sentences. Punchy, direct, professional.
- No markdown, no asterisks, no hashtags.
- End with: NFA.

Return STRICT raw JSON array (no code blocks, no markdown wrapper):
[
  {
    "asset": "GOLD",
    "direction": "BUY",
    "conviction": 88,
    "entry_price": 3285.50,
    "tp_price": 3312.00,
    "sl_price": 3271.00,
    "risk_pct": 1.5,
    "analysis": "4H bullish OB at 3271-3280 held perfectly, coinciding with an unmitigated bullish FVG at 3278-3283. 4H BOS printed bullish at 3250, EMA20 crossed above EMA50. Targeting equal highs / sell-side liquidity at 3312. NFA."
  }
]

Include ALL 9 assets in the response — even ones scoring 20/100. I need the full ranking.
"""

    user_prompt = (
        f"Current UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}\n\n"
        f"REAL computed market data:\n{json.dumps(contexts, indent=2)}"
    )

    for i, key in enumerate(GEMINI_KEYS):
        if not key:
            continue
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={key}",
                    headers={"Content-Type": "application/json"},
                    json={
                        "contents": [{"parts": [{"text": system_prompt + "\n\n" + user_prompt}]}],
                        "generationConfig": {
                            "temperature": 0.2,
                            "responseMimeType": "application/json"
                        }
                    }
                )
                if resp.status_code == 429:
                    print(f"[Gemini] Key {i+1} rate-limited.")
                    continue
                resp.raise_for_status()
                raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(raw)
                
                # Handle both {"setups": [...]} and [...]
                if isinstance(parsed, dict):
                    for v in parsed.values():
                        if isinstance(v, list):
                            parsed = v
                            break
                if isinstance(parsed, list):
                    parsed.sort(key=lambda x: x.get("conviction", 0), reverse=True)
                    return parsed
        except Exception as e:
            print(f"[Gemini] Key {i+1} error: {e}")
            continue

    # Fallback to Groq if all Gemini keys fail
    if _GROQ_KEYS:
        print("[AI] Gemini failed, falling back to Groq...")
        for i, key in enumerate(_GROQ_KEYS):
            try:
                async with httpx.AsyncClient(timeout=90.0) as client:
                    resp = await client.post(
                        GROQ_API_URL,
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={
                            "model": "llama-3.3-70b-versatile",
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user",   "content": user_prompt}
                            ],
                            "temperature": 0.2,
                            "response_format": {"type": "json_object"}
                        }
                    )
                    resp.raise_for_status()
                    raw = resp.json()["choices"][0]["message"]["content"]
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        for v in parsed.values():
                            if isinstance(v, list):
                                parsed = v
                                break
                    if isinstance(parsed, list):
                        parsed.sort(key=lambda x: x.get("conviction", 0), reverse=True)
                        return parsed
            except Exception as e:
                print(f"[Groq] Key {i+1} error: {e}")
                continue
                
    raise Exception("All AI engines failed (Gemini and Groq). API keys might be exhausted.")


# ══════════════════════════════════════════════════════════════════════════════
# JOB 1: scan_markets
# ══════════════════════════════════════════════════════════════════════════════
async def scan_markets(bot):
    from market_analyzer import build_full_context
    from chart_generator import get_chart_for_asset
    from aiogram.types import BufferedInputFile
    import html as html_module, re

    print("[Scanner] Starting real-data market scan of 9 assets...")

    # Build full SMC/TA context for all assets concurrently
    contexts = await asyncio.gather(*[build_full_context(a) for a in ASSET_BASKET])
    valid_contexts = [c for c in contexts if c.get("current_price")]
    print(f"[Scanner] Got data for {len(valid_contexts)}/9 assets.")

    if not valid_contexts:
        print("[Scanner] No data fetched. Aborting.")
        return

    # AI analyzes REAL computed indicators
    try:
        all_setups = await analyze_with_real_data(valid_contexts)
    except Exception as e:
        print(f"[Scanner] AI error: {e}")
        try:
            await bot.send_message(
                chat_id=FREE_CHANNEL_ID, 
                text=f"⚠️ <b>SYSTEM ALERT</b>\n\nAI Engine failure: {e}\nMarket scanning paused until resolved.", 
                parse_mode="HTML"
            )
        except:
            pass
        return

    if not all_setups:
        print("[Scanner] AI returned no setups.")
        return

    # Log full ranking
    print("[Scanner] Full ranking:")
    for s in all_setups:
        print(f"  {s.get('asset','?'):10s} {s.get('direction','?'):4s} conviction={s.get('conviction',0)}")

    # Pick ONLY the single best setup with conviction >= 75
    best = all_setups[0]
    if best.get("conviction", 0) < 75:
        print(f"[Scanner] Best is {best['asset']} at {best['conviction']}/100. Below threshold. No post.")
        return

    asset     = best["asset"]
    direction = best["direction"].upper()

    # Don't open duplicate trades on same asset
    db = SessionLocal()
    try:
        existing = db.query(Trade).filter(Trade.asset == asset, Trade.status == "OPEN").first()
        if existing:
            print(f"[Scanner] Already have open trade on {asset}. Skipping.")
            return

        trade = Trade(
            asset=asset, direction=direction,
            entry_price=str(best["entry_price"]),
            tp_price=str(best["tp_price"]),
            sl_price=str(best["sl_price"]),
            risk_pct=str(best.get("risk_pct", 1.0)),
            status="OPEN",
            mt5_status="PENDING",
            opened_at=datetime.now(timezone.utc).isoformat(),
        )
        db.add(trade)
        
        from database import AuditLog
        log = AuditLog(
            event_type="TRADE_OPENED",
            description=f"Opened {direction} on {asset} @ {best['entry_price']}. Conviction: {best['conviction']}.",
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        db.add(log)
        db.commit()
        db.refresh(trade)
        print(f"[Scanner] Trade #{trade.id} opened: {direction} {asset} @ {best['entry_price']}")
    finally:
        db.close()

    # Generate TradingView chart with Long/Short Position tool drawn
    try:
        chart_bytes = await get_chart_for_asset(
            asset,
            entry=float(best["entry_price"]),
            tp=float(best["tp_price"]),
            sl=float(best["sl_price"]),
            direction=direction,
        )
        print(f"[Scanner] TradingView chart: {len(chart_bytes)} bytes")
    except Exception as e:
        print(f"[Scanner] Chart failed: {e}")
        chart_bytes = None

    # Build message
    entry  = float(best["entry_price"])
    tp     = float(best["tp_price"])
    sl     = float(best["sl_price"])
    risk   = abs(entry - sl)
    reward = abs(tp - entry)
    rrr    = f"1:{round(reward/risk, 1)}" if risk else "N/A"
    stars  = "⭐" * min(5, best["conviction"] // 20)
    arrow  = "🚀" if direction == "BUY" else "🔻"
    badge  = "🟢" if direction == "BUY" else "🔴"
    safe_a = html_module.escape(best.get("analysis", ""))

    msg = (
        f"{arrow} <b>APEX SIGNAL — {asset.replace('_','/')}</b> {arrow}\n\n"
        f"{badge} <b>{direction}</b>  |  {stars} ({best['conviction']}/100)\n\n"
        f"📍 <b>Entry:</b>  {best['entry_price']}\n"
        f"🎯 <b>TP:</b>     {best['tp_price']}\n"
        f"🛑 <b>SL:</b>     {best['sl_price']}\n"
        f"📊 <b>R:R:</b>    {rrr}\n\n"
        f"📋 {safe_a}\n\n"
        f"<i>Signal #{trade.id} • Real ICT/SMC • NFA</i>"
    )
    
    teaser_msg = (
        f"{arrow} <b>APEX VIP ALERT — {asset.replace('_','/')}</b> {arrow}\n\n"
        f"{badge} <b>{direction}</b>  |  {stars} ({best['conviction']}/100)\n\n"
        f"📍 <b>Entry:</b>  <i>[Hidden — VIP Only]</i>\n"
        f"🎯 <b>TP:</b>     <i>[Hidden — VIP Only]</i>\n"
        f"🛑 <b>SL:</b>     {best['sl_price']}\n"
        f"📊 <b>R:R:</b>    {rrr}\n\n"
        f"📋 {safe_a}\n\n"
        f"🔒 <i>Unlock full params instantly via Whop.</i>"
    )

    # Post full signal to VIP
    try:
        if chart_bytes and VIP_CHANNEL_ID:
            photo = BufferedInputFile(chart_bytes, filename=f"{asset}_signal.png")
            await bot.send_photo(chat_id=VIP_CHANNEL_ID, photo=photo, caption=msg, parse_mode="HTML")
        elif VIP_CHANNEL_ID:
            await bot.send_message(chat_id=VIP_CHANNEL_ID, text=msg, parse_mode="HTML")
    except Exception as e:
        print(f"[Scanner] VIP Telegram error: {e}")

    # Post teaser to FREE
    try:
        await bot.send_message(chat_id=FREE_CHANNEL_ID, text=teaser_msg, parse_mode="HTML")
        print("[Scanner] Posted to Telegram VIP and Free ✅")
    except Exception as e:
        print(f"[Scanner] Free Telegram error: {e}")

    # Fire immediately to Make.com → X/Twitter
    if MAKE_WEBHOOK_URL:
        plain = re.sub(r'<[^>]+>', '', msg)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(MAKE_WEBHOOK_URL, json={"text": plain})
            print("[Scanner] Fired to Make.com → X ✅")
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
            price = await fetch_price(trade.asset)
            if not price:
                continue

            entry = float(trade.entry_price)
            tp    = float(trade.tp_price)
            sl    = float(trade.sl_price)

            tp_hit = (trade.direction == "BUY"  and price >= tp) or \
                     (trade.direction == "SELL" and price <= tp)
            sl_hit = (trade.direction == "BUY"  and price <= sl) or \
                     (trade.direction == "SELL" and price >= sl)

            if tp_hit:
                pips, pct = calculate_profit(trade.asset, entry, tp, trade.direction)
                trade.status   = "WON"
                trade.closed_at = datetime.now(timezone.utc).isoformat()
                db.commit()
                print(f"[Monitor] #{trade.id} {trade.asset} TP HIT! +{pips} pips")

                try:
                    chart_bytes = await get_chart_for_asset(trade.asset)
                except Exception:
                    chart_bytes = None

                why = await _tp_explanation(trade, pips, pct)
                msg = (
                    f"💥 <b>BOOM BOOM! TP SMASHED!</b> 💥\n\n"
                    f"{'🟢' if trade.direction=='BUY' else '🔴'} "
                    f"<b>{trade.asset.replace('_','/')} {trade.direction}</b>\n\n"
                    f"📍 Entry: {entry}  →  🎯 TP: {tp} ✅\n"
                    f"💰 <b>+{pips} pips  (+{pct}%)</b>\n\n"
                    f"📖 {html_module.escape(why)}\n\n"
                    f"<i>Signal #{trade.id} • Project Apex</i>"
                )
                try:
                    if chart_bytes:
                        photo = BufferedInputFile(chart_bytes, filename=f"{trade.asset}_tp.png")
                        if VIP_CHANNEL_ID: await bot.send_photo(chat_id=VIP_CHANNEL_ID, photo=photo, caption=msg, parse_mode="HTML")
                        await bot.send_photo(chat_id=FREE_CHANNEL_ID, photo=photo, caption=msg, parse_mode="HTML")
                    else:
                        if VIP_CHANNEL_ID: await bot.send_message(chat_id=VIP_CHANNEL_ID, text=msg, parse_mode="HTML")
                        await bot.send_message(chat_id=FREE_CHANNEL_ID, text=msg, parse_mode="HTML")
                except Exception as e:
                    print(f"[Monitor] BOOM post failed: {e}")

            elif sl_hit:
                pips, pct = calculate_profit(trade.asset, entry, sl, trade.direction)
                trade.status   = "LOST"
                trade.closed_at = datetime.now(timezone.utc).isoformat()
                db.commit()
                print(f"[Monitor] #{trade.id} {trade.asset} SL hit. {pips} pips")

                msg = (
                    f"⚠️ <b>STOP LOSS HIT</b>\n\n"
                    f"{'🟢' if trade.direction=='BUY' else '🔴'} "
                    f"<b>{trade.asset.replace('_','/')} {trade.direction}</b>\n\n"
                    f"📍 Entry: {entry}  →  🛑 SL: {sl}\n"
                    f"📉 <b>{pips} pips  ({pct}%)</b>\n\n"
                    f"<i>Losses are part of the game. Risk was strictly managed.</i>"
                )
                try:
                    if VIP_CHANNEL_ID: await bot.send_message(chat_id=VIP_CHANNEL_ID, text=msg, parse_mode="HTML")
                    await bot.send_message(chat_id=FREE_CHANNEL_ID, text=msg, parse_mode="HTML")
                except Exception as e:
                    print(f"[Monitor] SL post failed: {e}")
    finally:
        db.close()


async def _tp_explanation(trade, pips, pct) -> str:
    valid_keys = [k for k in GEMINI_KEYS if k]
    if not valid_keys:
        return "Price delivered precisely to our target. Structure confirmed the bias."
    
    prompt = (
        f"Our {trade.direction} on {trade.asset} from {trade.entry_price} hit TP at {trade.tp_price}. "
        f"+{pips} pips (+{pct}%). Write 2 punchy sentences why this worked from an ICT perspective. "
        f"Reference price levels. No markdown. No asterisks."
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={valid_keys[0]}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]}
            )
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return "Price delivered exactly as structure dictated. OB held perfectly."


# ══════════════════════════════════════════════════════════════════════════════
# JOB 3: daily_wrapup
# ══════════════════════════════════════════════════════════════════════════════
async def daily_wrapup(bot):
    import html as html_module
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        closed = db.query(Trade).filter(
            Trade.status.in_(["WON", "LOST"]), 
            Trade.closed_at.startswith(today)
        ).all()
        
        if not closed:
            print("[Wrapup] No completed trades today.")
            return

        total_pips = 0.0
        wins = 0
        losses = 0
        lines = []
        
        for t in closed:
            if t.status == "WON":
                pips, pct = calculate_profit(t.asset, float(t.entry_price), float(t.tp_price), t.direction)
                total_pips += pips
                wins += 1
                e = "🟢" if t.direction == "BUY" else "🔴"
                lines.append(f"{e} {t.asset.replace('_','/')} {t.direction} → +{pips} pips (WON)")
            else:
                pips, pct = calculate_profit(t.asset, float(t.entry_price), float(t.sl_price), t.direction)
                total_pips += pips  # pips will be negative from calculate_profit for losses
                losses += 1
                e = "🟢" if t.direction == "BUY" else "🔴"
                lines.append(f"{e} {t.asset.replace('_','/')} {t.direction} → {pips} pips (LOST)")

        total = wins + losses
        win_rate = round((wins / total) * 100, 1) if total > 0 else 0

        text = "\n".join(lines)
        x_post = (
            f"📊 DAILY RECAP — PROJECT APEX\n\n"
            f"Today's ICT signals:\n{text}\n\n"
            f"Net: {round(total_pips, 1)} pips | Win Rate: {win_rate}%\n\n"
            f"Transparent SMC/ICT trading. Both wins and losses.\n"
            f"Free Telegram for live alerts 👇"
        )
        if MAKE_WEBHOOK_URL:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    await client.post(MAKE_WEBHOOK_URL, json={"text": x_post})
                print("[Wrapup] Daily recap → Make.com ✅")
            except Exception as e:
                print(f"[Wrapup] Make.com error: {e}")

        tg_msg = (
            f"📊 <b>DAILY RECAP</b>\n\n"
            f"<b>Today's Trades:</b>\n{html_module.escape(text)}\n\n"
            f"<b>Net Pips: {round(total_pips, 1)} pips</b>\n"
            f"<b>Win Rate: {win_rate}% ({wins}W - {losses}L)</b>\n\n"
            f"<i>100% Transparent Tracking. Project Apex.</i>"
        )
        try:
            if VIP_CHANNEL_ID: await bot.send_message(chat_id=VIP_CHANNEL_ID, text=tg_msg, parse_mode="HTML")
            await bot.send_message(chat_id=FREE_CHANNEL_ID, text=tg_msg, parse_mode="HTML")
        except Exception as e:
            print(f"[Wrapup] Telegram error: {e}")
    finally:
        db.close()
