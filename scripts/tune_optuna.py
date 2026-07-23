import os
import sys
import json
import asyncio
import pandas as pd
import optuna
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bot'))
from market_analyzer import resample_4h, compute_smc, compute_ta
from trade_manager import analyze_with_real_data

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_FILE = "backtest_cache.json"

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

cache = load_cache()
asset_data = {}

def load_all_data():
    """Load MT5 exported data from CSVs."""
    assets = ["EUR_USD", "USD_JPY", "GBP_USD", "USD_CHF", "AUD_USD", "USD_CAD", "GOLD", "SILVER", "BTC"]
    for asset in assets:
        csv_path = os.path.join(DATA_DIR, f"{asset}_1H.csv")
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            asset_data[asset] = df
    return asset_data

def simulate_trade_sync(asset: str, df: pd.DataFrame, current_idx, direction, entry, tp, sl, spread_multiplier=2.0):
    """Sync version of simulate_trade for fast Optuna tuning."""
    pip_size = 0.0001
    if "JPY" in asset: pip_size = 0.01
    if "GOLD" == asset: pip_size = 0.01
    if "BTC" == asset: pip_size = 1.0
    spread = pip_size * spread_multiplier
    
    for i in range(current_idx + 1, len(df)):
        row = df.iloc[i]
        high, low = row["high"], row["low"]
        
        if direction == "BUY":
            if low <= (sl + spread): return "LOST"
            if high >= (tp + spread): return "WON"
        else:
            if high >= (sl - spread): return "LOST"
            if low <= (tp - spread): return "WON"
    return "OPEN"

async def run_objective(trial):
    conviction_threshold = trial.suggest_int("conviction_threshold", 60, 95)
    ote_width = trial.suggest_int("ote_width", 10, 30)
    spread_multiplier = trial.suggest_float("spread_multiplier", 1.0, 4.0)
    
    total_trades = 0
    total_wins = 0
    
    for asset, df_1h in asset_data.items():
        if len(df_1h) < 100: continue
        df_4h = resample_4h(df_1h)
        
        for i in range(50, len(df_4h)):
            historical_4h = df_4h.iloc[:i]
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

            rsi = ctx["4h_ta"].get("rsi_14", 50)
            if rsi > 75 or rsi < 25: continue
                
            smc = ctx.get("4h_smc", {})
            has_sweep = any(liq.get("swept") for liq in smc.get("liquidity_levels", []))
            if not has_sweep: continue
            
            pct = smc.get("premium_discount", {}).get("pct_of_range", 50)
            buy_ote = (50 - ote_width - 10) <= pct <= (50 - 10)
            sell_ote = (50 + 10) <= pct <= (50 + 10 + ote_width)
            
            if not (buy_ote or sell_ote): continue
                
            cache_key = f"{asset}_{current_ts.isoformat()}"
            analysis = cache.get(cache_key)
            if not analysis: continue
                
            conv = analysis.get("conviction", 0)
            bias = analysis.get("directional_bias", "NEUTRAL").upper()

            if bias in ["BUY", "SELL"] and conv >= conviction_threshold:
                entry = analysis.get("entry_price", 0)
                tp = analysis.get("tp_price", 0)
                sl = analysis.get("sl_price", 0)
                if not entry: continue
                
                outcome = simulate_trade_sync(asset, df_1h, len(historical_1h)-1, bias, entry, tp, sl, spread_multiplier)
                if outcome != "OPEN":
                    total_trades += 1
                    if outcome == "WON": total_wins += 1

    if total_trades < 10:
        return 0.0
        
    win_rate = total_wins / total_trades
    return win_rate

def objective_wrapper(trial):
    return asyncio.run(run_objective(trial))

if __name__ == "__main__":
    print("Loading MT5 CSV data...")
    load_all_data()
    if not asset_data:
        print("No MT5 data found in data/. Please run auto_export_history.py first.")
        sys.exit(1)
        
    print(f"Data loaded for {len(asset_data)} assets. Starting Optuna tuning...")
    study = optuna.create_study(direction="maximize")
    study.optimize(objective_wrapper, n_trials=50)
    
    print("\nBest Trial:")
    print("  Value (Win Rate): ", study.best_trial.value)
    print("  Params: ")
    for key, value in study.best_trial.params.items():
        print(f"    {key}: {value}")
