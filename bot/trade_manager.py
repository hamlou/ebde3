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


import google.generativeai as genai

def call_gemini(system_msg: str, user_msg: str, api_key: str) -> dict:
    """Synchronous call to Gemini SDK."""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=system_msg)
        resp = model.generate_content(
            user_msg,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
            )
        )
        parsed = json.loads(resp.text)
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list): return v
        if isinstance(parsed, list): return parsed
        return None
    except Exception as e:
        print(f"[Gemini] Error: {e}")
        return None

async def analyze_with_real_data(contexts: list[dict]) -> list[dict]:
    import random
    
    system_prompt = (
        "You are an elite ICT/SMC trading algorithm. Analyze the provided market context (FVGs, OBs, Liquidity, Trend). "
        "Return a JSON array of setups: "
        '[{"asset":"GOLD","directional_bias":"BUY"|"SELL"|"NEUTRAL","conviction":0-100,"entry_price":float,"tp_price":float,'
        '"sl_price":float,"reasoning":"brief explanation","risk_pct":1.0-15.0}] '
        "Conviction must be >=75 to trade. Strict ICT principles only. Set risk_pct between 5.0 and 15.0 based on conviction. "
        "CRITICAL: Do not score any setup >= 75 unless price has swept a liquidity pool ('swept': true) prior to the entry."
    )
    user_prompt = json.dumps(contexts, indent=2)

    # 1. Run Groq (Async)
    groq_setups = None
    g_key = random.choice(_GROQ_KEYS) if _GROQ_KEYS else None
    if g_key:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(
                    GROQ_API_URL,
                    headers={"Authorization": f"Bearer {g_key}"},
                    json={
                        "model": "llama3-70b-8192",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "response_format": {"type": "json_object"}
                    }
                )
                if res.status_code == 200:
                    raw = json.loads(res.json()["choices"][0]["message"]["content"])
                    if isinstance(raw, dict):
                        for v in raw.values():
                            if isinstance(v, list): groq_setups = v
                    if isinstance(raw, list): groq_setups = raw
        except Exception as e:
            print(f"[Dual-Model] Groq Error: {e}")

    # 2. Run Gemini
    gemini_setups = None
    for gem_key in GEMINI_KEYS:
        if not gem_key: continue
        gemini_setups = await asyncio.to_thread(call_gemini, system_prompt, user_prompt, gem_key)
        if gemini_setups: break

    # If both fail, return empty
    if not groq_setups and not gemini_setups:
        return []

    # If only one works, return it (avoids downtime)
    if not groq_setups: return sorted(gemini_setups, key=lambda x: x.get("conviction", 0), reverse=True)
    if not gemini_setups: return sorted(groq_setups, key=lambda x: x.get("conviction", 0), reverse=True)

    # 3. Consensus Logic
    consensus_setups = []
    g_map = {s.get("asset"): s for s in groq_setups}
    
    for m in gemini_setups:
        asset = m.get("asset")
        g = g_map.get(asset)
        if not g: continue
        
        m_bias = m.get("directional_bias", "NEUTRAL").upper()
        g_bias = g.get("directional_bias", "NEUTRAL").upper()
        m_conv = float(m.get("conviction", 0))
        g_conv = float(g.get("conviction", 0))
        
        if m_bias == g_bias and m_bias in ["BUY", "SELL"] and m_conv >= 75 and g_conv >= 75:
            avg_conv = round((m_conv + g_conv) / 2.0, 1)
            m["conviction"] = avg_conv
            m["reasoning"] = f"Consensus reached (Gemini {m_conv}, Groq {g_conv}). {m.get('reasoning','')}"
            consensus_setups.append(m)

    return sorted(consensus_setups, key=lambda x: x.get("conviction", 0), reverse=True)
async def check_news_filter(asset: str) -> bool:
    """
    Returns True if HIGH impact news is scheduled within 60 mins for this asset's currency.
    """
    try:
        currencies = []
        if "USD" in asset: currencies.append("USD")
        if "EUR" in asset: currencies.append("EUR")
        if "GBP" in asset: currencies.append("GBP")
        if "JPY" in asset: currencies.append("JPY")
        if "CHF" in asset: currencies.append("CHF")
        if "CAD" in asset: currencies.append("CAD")
        if "AUD" in asset: currencies.append("AUD")
        if asset in ["GOLD", "SILVER", "BTC"]: currencies.append("USD")

        if not currencies: return False

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json")
            if resp.status_code != 200: return False
            events = resp.json()

        now = datetime.now(timezone.utc)
        for event in events:
            if event.get("impact") != "High" or event.get("country") not in currencies: continue
            event_time_str = event.get("date")
            if not event_time_str: continue
            
            event_time = datetime.fromisoformat(event_time_str).astimezone(timezone.utc)
            diff_mins = (event_time - now).total_seconds() / 60.0
            if -60 <= diff_mins <= 60:
                print(f"[News Filter] HIGH impact news ({event.get('title')}) for {event.get('country')}. Skipping trade.")
                return True
        return False
    except Exception as e:
        print(f"[News Filter] Error fetching calendar: {e}")
        return False

async def scan_markets(bot):
    from market_analyzer import build_full_context
    from chart_generator import get_chart_for_asset
    from aiogram.types import BufferedInputFile
    import html as html_module, re

    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour
    
    print(f"\n[Scanner] Starting scan at {now_utc}")
    db = SessionLocal()
    try:
        contexts = await asyncio.gather(*[build_full_context(a) for a in ASSET_BASKET])
        valid_contexts = [c for c in contexts if c.get("current_price")]
        if not valid_contexts: return

        # 1. Deterministic Pre-Filtering (Sweep & OTE & Killzone)
        filtered_contexts = []
        for ctx in valid_contexts:
            asset_name = ctx["asset"]
            
            # A. Killzone Check (Exclude BTC)
            if asset_name != "BTC" and not ((7 <= hour < 10) or (12 <= hour < 15)):
                continue
                
            smc_data = ctx.get("4h_smc", {})
            if "smc_error" in smc_data or not smc_data:
                continue
                
            # B. Liquidity Sweep Check
            has_sweep = False
            for liq in smc_data.get("liquidity_levels", []):
                if liq.get("swept"):
                    has_sweep = True
                    break
            
            if not has_sweep:
                continue
                
            # C. OTE Check (Fib 61.8 - 79.0)
            # pct_of_range: 0 is Swing Low, 100 is Swing High.
            # BUY OTE: 20.0 to 40.0 (price is deep in discount)
            # SELL OTE: 60.0 to 80.0 (price is deep in premium)
            pct = smc_data.get("premium_discount", {}).get("pct_of_range", 50)
            in_ote = (20.0 <= pct <= 40.0) or (60.0 <= pct <= 80.0)
            
            if not in_ote:
                continue
                
            filtered_contexts.append(ctx)

        if not filtered_contexts:
            print(f"[Scanner] {len(valid_contexts)} assets fetched, but none met deterministic Sweep/OTE/Killzone rules.")
            return

        all_setups = await analyze_with_real_data(filtered_contexts)
        if not all_setups: return
        
        best = all_setups[0]
        if best.get("conviction", 0) < 75: return

        asset = best["asset"]
        direction = best["directional_bias"].upper()

        # HTF Trend Gate & Directional OTE strict check
        for ctx in filtered_contexts:
            if ctx["asset"] == asset:
                # Re-verify OTE specifically for the AI's chosen direction
                pct = ctx.get("4h_smc", {}).get("premium_discount", {}).get("pct_of_range", 50)
                if direction == "BUY" and not (20.0 <= pct <= 40.0):
                    print(f"[Scanner] {asset} REJECTED: AI said BUY but price is not in discount OTE (pct={pct}).")
                    return
                if direction == "SELL" and not (60.0 <= pct <= 80.0):
                    print(f"[Scanner] {asset} REJECTED: AI said SELL but price is not in premium OTE (pct={pct}).")
                    return
                
                htf_trend = ctx.get("4h_ta", {}).get("htf_trend")
                if htf_trend:
                    if (direction == "BUY" and htf_trend == "bearish") or (direction == "SELL" and htf_trend == "bullish"):
                        print(f"[Scanner] {asset} REJECTED: {direction} conflicts with HTF {htf_trend} trend.")
                        return

        # High-Impact News Filter
        if await check_news_filter(asset):
            return

        existing = db.query(Trade).filter(Trade.asset == asset, Trade.status == "OPEN").first()
        if existing: return

        trade = Trade(
            asset=asset, direction=direction,
            entry_price=str(best["entry_price"]),
            tp_price=str(best["tp_price"]),
            sl_price=str(best["sl_price"]),
            risk_pct=str(best.get("risk_pct", 1.0)),
            conviction=int(best["conviction"]),
            status="OPEN",
            mt5_status="PENDING",
            opened_at=datetime.now(timezone.utc).isoformat(),
        )
        db.add(trade)
        from database import AuditLog
        db.add(AuditLog(event_type="TRADE_OPENED", description=f"Opened {direction} on {asset} @ {best['entry_price']}. Conviction: {best['conviction']}.", timestamp=datetime.now(timezone.utc).isoformat()))
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
    safe_a = html_module.escape(best.get("reasoning", ""))

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

            # Add spread/slippage buffer for realistic TP/SL checks
            spread_buffer = PIP_SIZE.get(trade.asset, 0.0001) * 2.0  # 2 pips buffer
            
            tp_hit = (trade.direction == "BUY"  and price >= (tp - spread_buffer)) or \
                     (trade.direction == "SELL" and price <= (tp + spread_buffer))
            sl_hit = (trade.direction == "BUY"  and price <= (sl + spread_buffer)) or \
                     (trade.direction == "SELL" and price >= (sl - spread_buffer))

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
        buckets = {"[75-80)": {"w":0, "l":0}, "[80-90)": {"w":0, "l":0}, "[90+]": {"w":0, "l":0}}
        
        for t in closed:
            c = getattr(t, "conviction", 75) or 75
            b_key = "[90+]" if c >= 90 else ("[80-90)" if c >= 80 else "[75-80)")
            
            if t.status == "WON":
                pips, pct = calculate_profit(t.asset, float(t.entry_price), float(t.tp_price), t.direction)
                total_pips += pips
                wins += 1
                buckets[b_key]["w"] += 1
                e = "🟢" if t.direction == "BUY" else "🔴"
                lines.append(f"{e} {t.asset.replace('_','/')} {t.direction} ({c}/100) → +{pips} pips")
            else:
                pips, pct = calculate_profit(t.asset, float(t.entry_price), float(t.sl_price), t.direction)
                total_pips += pips  # pips will be negative from calculate_profit for losses
                losses += 1
                buckets[b_key]["l"] += 1
                e = "🟢" if t.direction == "BUY" else "🔴"
                lines.append(f"{e} {t.asset.replace('_','/')} {t.direction} ({c}/100) → {pips} pips")

        total = wins + losses
        win_rate = round((wins / total) * 100, 1) if total > 0 else 0

        # Build Calibration String
        cal_strs = []
        for b, st in buckets.items():
            t_b = st["w"] + st["l"]
            if t_b > 0:
                r = round(st["w"] / t_b * 100, 1)
                cal_strs.append(f"{b}: {r}% Win ({st['w']}W-{st['l']}L)")

        calibration_text = "\n".join(cal_strs)
        
        text = "\n".join(lines)
        x_post = (
            f"📊 DAILY RECAP — PROJECT APEX\n\n"
            f"Today's ICT signals:\n{text}\n\n"
            f"Net: {round(total_pips, 1)} pips | Win Rate: {win_rate}%\n"
            f"AI Calibration:\n{calibration_text}\n\n"
            f"Transparent SMC/ICT trading.\n"
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
            f"<b>🤖 AI Conviction Calibration:</b>\n"
            f"<pre>{calibration_text}</pre>\n\n"
            f"<i>100% Transparent Tracking. Project Apex.</i>"
        )
        try:
            if VIP_CHANNEL_ID: await bot.send_message(chat_id=VIP_CHANNEL_ID, text=tg_msg, parse_mode="HTML")
            await bot.send_message(chat_id=FREE_CHANNEL_ID, text=tg_msg, parse_mode="HTML")
        except Exception as e:
            print(f"[Wrapup] Telegram error: {e}")
    finally:
        db.close()
