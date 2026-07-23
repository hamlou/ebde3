"""
backtest_llm.py
Standalone script to backtest the LLM conviction thresholds without blowing up API limits.

Usage:
1. It fetches 30 days of 1H data (the limit of what Yahoo/TwelveData gives easily on free tiers).
2. It steps through every 4 hours (simulating the scan_markets cron).
3. If a valid SMC setup exists (price near FVG or OB), it queries Gemini/Groq.
4. It caches the response in `backtest_cache.json` so if you restart, it skips the LLM call.
5. Finally, it simulates the trade and prints a win-rate heatmap.
"""

import sys
import os
import json
import asyncio
import pandas as pd
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bot'))
from market_analyzer import YAHOO_MAP, fetch_ohlcv, resample_4h, compute_smc, compute_ta
from trade_manager import analyze_with_real_data, calculate_profit

CACHE_FILE = "backtest_cache.json"

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

async def simulate_trade(asset: str, df: pd.DataFrame, current_idx, direction, entry, tp, sl):
    """Scan forward in df to see if TP or SL is hit first with realistic spread buffer."""
    
    # Approx 2 pip spread for realistic fills
    pip_size = 0.0001
    if "JPY" in asset: pip_size = 0.01
    if "GOLD" == asset: pip_size = 0.01
    if "BTC" == asset: pip_size = 1.0
    spread = pip_size * 2.0
    
    for i in range(current_idx + 1, len(df)):
        row = df.iloc[i]
        high, low = row["high"], row["low"]
        
        if direction == "BUY":
            if low <= (sl + spread): return "LOST"
            if high >= (tp - spread): return "WON"
        else:
            if high >= (sl - spread): return "LOST"
            if low <= (tp + spread): return "WON"
    return "OPEN" # Didn't hit either before dataset ended

async def run_backtest(asset="GOLD"):
    print(f"Starting LLM Backtest for {asset}...")
    cache = load_cache()
    
    # Fetch data (e.g. 60 days of 1H data for a solid sample)
    df_1h = await fetch_ohlcv(asset, "1h", "60d")
    if df_1h is None or len(df_1h) < 100:
        print("Failed to fetch historical data.")
        return

    df_4h = resample_4h(df_1h)
    
    results = []

    # Step through time, starting from index 50 to have enough history
    for i in range(50, len(df_4h)):
        # Simulate what the bot saw at this exact moment in the past
        historical_4h = df_4h.iloc[:i]
        
        # We need the corresponding 1h data up to the same timestamp
        current_ts = historical_4h.index[-1]
        historical_1h = df_1h[df_1h.index <= current_ts]
        
        ctx = {
            "asset": asset,
            "current_price": round(float(historical_1h["close"].iloc[-1]), 5),
            "candles_1h": len(historical_1h),
            "candles_4h": len(historical_4h),
            "4h_smc": compute_smc(historical_4h, "4H"),
            "4h_ta": compute_ta(historical_4h),
        }

        # DETERMINISTIC PRE-FILTER:
        # Only query LLM if price is inside OTE, RSI isn't extreme, and liquidity swept.
        rsi = ctx["4h_ta"].get("rsi_14", 50)
        if rsi > 75 or rsi < 25:
            continue
            
        smc = ctx.get("4h_smc", {})
        
        # Sweep Check
        has_sweep = False
        for liq in smc.get("liquidity_levels", []):
            if liq.get("swept"):
                has_sweep = True
                break
        if not has_sweep: continue
        
        # OTE Check
        pct = smc.get("premium_discount", {}).get("pct_of_range", 50)
        if not ((20.0 <= pct <= 40.0) or (60.0 <= pct <= 80.0)):
            continue
            
        # Check cache
        cache_key = f"{asset}_{current_ts.isoformat()}"
        if cache_key in cache:
            analysis = cache[cache_key]
        else:
            print(f"[{current_ts}] Querying LLM...")
            # Query LLM (Dual-consensus)
            setups = await analyze_with_real_data([ctx])
            if setups:
                analysis = setups[0]
            else:
                analysis = {"directional_bias": "NEUTRAL", "conviction": 0}
            cache[cache_key] = analysis
            save_cache(cache)
            # Sleep to avoid rate limits
            await asyncio.sleep(2)

        conv = analysis.get("conviction", 0)
        bias = analysis.get("directional_bias", "NEUTRAL").upper()

        if bias in ["BUY", "SELL"] and conv >= 50: # We test all the way down to 50
            entry = analysis.get("entry_price")
            tp = analysis.get("tp_price")
            sl = analysis.get("sl_price")
            
            # Simulate forward with realistic spread
            outcome = await simulate_trade(asset, df_1h, len(historical_1h)-1, bias, entry, tp, sl)
            
            results.append({
                "ts": current_ts.isoformat(),
                "bias": bias,
                "conviction": conv,
                "outcome": outcome
            })
            print(f"[{current_ts}] {bias} (Conv: {conv}) -> {outcome}")

    # Heatmap summary
    print("\n=== BACKTEST RESULTS ===")
    buckets = [(50,60), (60,70), (70,80), (80,90), (90,101)]
    for low, high in buckets:
        trades = [r for r in results if low <= r["conviction"] < high and r["outcome"] != "OPEN"]
        if not trades:
            continue
        wins = len([r for r in trades if r["outcome"] == "WON"])
        wr = round((wins / len(trades)) * 100, 1)
        print(f"Conviction [{low}-{high}): {wr}% Win Rate ({wins}W / {len(trades)-wins}L)")

if __name__ == "__main__":
    asyncio.run(run_backtest("GOLD"))
