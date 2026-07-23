import os
import sys
import pandas as pd
from datetime import datetime
import MetaTrader5 as mt5

# Adjust path so we can import config/executor logic if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5_executor import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, SYMBOL_MAP, connect_mt5

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

def export_history(asset_name, mt5_symbol, num_days=365):
    """Export historical 1H data from MT5 to CSV."""
    if not mt5.symbol_select(mt5_symbol, True):
        print(f"Failed to select {mt5_symbol}")
        return False
        
    print(f"Fetching {num_days} days of 1H data for {asset_name} ({mt5_symbol})...")
    
    # 1H timeframe = mt5.TIMEFRAME_H1
    rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_H1, 0, num_days * 24)
    if rates is None or len(rates) == 0:
        print(f"No data returned for {mt5_symbol}")
        return False
        
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    
    # Format to match Yahoo Finance output expected by backtester
    df = df[['time', 'open', 'high', 'low', 'close', 'tick_volume']]
    df.rename(columns={'time': 'timestamp', 'tick_volume': 'volume'}, inplace=True)
    
    csv_path = os.path.join(DATA_DIR, f"{asset_name}_1H.csv")
    df.to_csv(csv_path, index=False)
    print(f"Saved {len(df)} candles to {csv_path}")
    return True

def main():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        
    if not connect_mt5():
        print("Failed to connect to MT5. Make sure the terminal is running.")
        return
        
    print(f"Starting automated MT5 historical data export...")
    success_count = 0
    
    for asset, mt5_symbol in SYMBOL_MAP.items():
        if export_history(asset, mt5_symbol, num_days=365):
            success_count += 1
            
    print(f"\nExport complete. Successfully downloaded {success_count}/{len(SYMBOL_MAP)} assets.")
    mt5.shutdown()

if __name__ == "__main__":
    main()
