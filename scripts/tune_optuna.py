import os
import sys
import json
import asyncio
import pandas as pd
import optuna
from datetime import datetime, timezone
import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bot'))
from market_analyzer import resample_4h, compute_smc, compute_ta
from trade_manager import analyze_with_real_data

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_FILE = "backtest_cache.json"
PIP_SIZE = {"EUR_USD": 0.0001, "USD_JPY": 0.01, "GBP_USD": 0.0001, "USD_CHF": 0.0001, "AUD_USD": 0.0001, "USD_CAD": 0.0001, "GOLD": 0.01, "SILVER": 0.01, "BTC": 1.0}

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

CACHED_DF = {}

def load_all_data():
    """Load MT5 exported data from CSVs."""
    assets = ["EUR_USD", "USD_JPY", "GBP_USD", "USD_CHF", "AUD_USD", "USD_CAD", "GOLD", "SILVER", "BTC"]
    for asset in assets:
        csv_path = os.path.join(DATA_DIR, f"{asset}_1H.csv")
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            CACHED_DF[asset] = df
    return CACHED_DF

def simulate_trade_sync(asset: str, df: pd.DataFrame, current_idx, direction, entry, tp, sl, spread_multiplier=2.0):
    """Sync version of simulate_trade for fast Optuna tuning."""
    pip_size = PIP_SIZE.get(asset, 0.0001)
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

async def run_objective(trial, is_test=False):
    conviction_threshold = trial.suggest_int("conviction_threshold", 60, 95)
    ote_width = trial.suggest_int("ote_width", 10, 30)
    spread_multiplier = trial.suggest_float("spread_multiplier", 1.0, 4.0)
    
    total_trades = 0
    total_wins = 0
    total_r = 0.0
    cache = load_cache()
    
    for asset, df_1h in CACHED_DF.items():
        if len(df_1h) < 100: continue
        df_4h = resample_4h(df_1h)
        
        split_idx = int(len(df_4h) * 0.8)
        start_idx = 50 if not is_test else split_idx
        end_idx = split_idx if not is_test else len(df_4h)
        
        if not is_test:
            start_idx = max(start_idx, split_idx - (15 * 6))
        else:
            start_idx = max(start_idx, end_idx - (5 * 6))
            
        for i in range(start_idx, end_idx):
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
            if not analysis:
                setups = await analyze_with_real_data([ctx])
                analysis = setups[0] if setups else {"directional_bias": "NEUTRAL", "conviction": 0}
                cache[cache_key] = analysis
                with open(CACHE_FILE, "w") as f:
                    json.dump(cache, f, indent=2)
                await asyncio.sleep(0.1)
                
            conv = analysis.get("conviction", 0)
            bias = analysis.get("directional_bias", "NEUTRAL").upper()

            if bias in ["BUY", "SELL"] and conv >= conviction_threshold:
                entry = analysis.get("entry_price", 0)
                tp = analysis.get("tp_price", 0)
                sl = analysis.get("sl_price", 0)
                if not entry: continue
                
                outcome = simulate_trade_sync(asset, df_1h, len(historical_1h)-1, bias, entry, tp, sl, spread_multiplier)
                if outcome != "OPEN":
                    pip_size = PIP_SIZE.get(asset, 0.0001)
                    pips_won_lost = (tp - entry) / pip_size if outcome == "WON" else -abs(entry - sl) / pip_size
                    risk_pips = abs(entry - sl) / pip_size
                    r_multiple = pips_won_lost / risk_pips if risk_pips > 0 else 0
                    total_r += r_multiple
                    total_trades += 1
                    if outcome == "WON": total_wins += 1

    if total_trades < 30:
        return -999.0
        
    return total_r / total_trades

def objective_wrapper(trial):
    return asyncio.run(run_objective(trial, is_test=False))

def test_wrapper(trial):
    return asyncio.run(run_objective(trial, is_test=True))

if __name__ == "__main__":
    print("Loading MT5 CSV data...")
    load_all_data()
    if not CACHED_DF:
        print("No MT5 data found in data/. Please run auto_export_history.py first.")
        sys.exit(1)
        
    print(f"Data loaded for {len(asset_data)} assets. Starting Optuna tuning (Train Window)...")
    study = optuna.create_study(direction="maximize")
    study.optimize(objective_wrapper, n_trials=50)
    
    print("\nBest Trial (Train):")
    print("  Value (Expectancy R): ", study.best_trial.value)
    print("  Params: ")
    for key, value in study.best_trial.params.items():
        print(f"    {key}: {value}")
        
    # Walk-forward validation
    print("\nRunning Walk-Forward Validation on Test Set (Last 20%)...")
    test_expectancy = test_wrapper(study.best_trial)
    print(f"  Test Value (Expectancy R): {test_expectancy}")
    if test_expectancy > 0:
        print("  ✅ Validation successful! Parameters hold up out-of-sample.")
    else:
        print("  ❌ Validation failed! System is overfitted to training data.")
