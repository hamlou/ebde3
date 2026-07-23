import time
import requests
import MetaTrader5 as mt5

# ==============================================================================
# CONFIGURATION
# ==============================================================================

# 1. MT5 Account Details
MT5_LOGIN = 10011858378          
MT5_PASSWORD = "Lg-1PzRo" 
MT5_SERVER = "MetaQuotes-Demo" 

# Treat account as $50 instead of the real balance
VIRTUAL_BALANCE = 50.0

# 2. Render Bot URL
API_BASE_URL = "https://ebde3.onrender.com"

# 3. Symbol Mapping
# Our bot outputs symbols like "GOLD", "EUR_USD". MT5 usually uses "XAUUSD" or "EURUSD".
SYMBOL_MAP = {
    "EUR_USD": "EURUSD",
    "USD_JPY": "USDJPY",
    "GBP_USD": "GBPUSD",
    "USD_CHF": "USDCHF",
    "AUD_USD": "AUDUSD",
    "USD_CAD": "USDCAD",
    "GOLD":    "XAUUSD",
    "SILVER":  "XAGUSD",
    "BTC":     "BTCUSD",
}

# ==============================================================================

def connect_mt5():
    if not mt5.initialize():
        print("initialize() failed, error code =", mt5.last_error())
        return False

    authorized = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    if not authorized:
        print(f"Failed to connect at account #{MT5_LOGIN}, error code: {mt5.last_error()}")
        return False
        
    print(f"SUCCESS: Connected to MT5 Account: {MT5_LOGIN}")
    return True

def calculate_lot_size(symbol: str, entry: float, sl: float, risk_pct: float) -> float:
    """
    Calculate the exact lot size to risk `risk_pct`% of the account balance.
    """
    account_info = mt5.account_info()
    if account_info is None:
        print("Failed to get account info")
        return 0.01

    # Using $50 virtual balance as requested instead of real balance
    balance = VIRTUAL_BALANCE
    risk_amount_usd = balance * (risk_pct / 100.0)
    
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        print(f"{symbol} not found")
        return 0.01

    tick_value = symbol_info.trade_tick_value
    tick_size = symbol_info.trade_tick_size

    if tick_size == 0 or tick_value == 0:
        return 0.01

    # Distance in raw price
    sl_distance = abs(entry - sl)
    
    # Distance in ticks
    ticks_at_risk = sl_distance / tick_size
    
    # Loss per 1 full lot
    loss_per_lot = ticks_at_risk * tick_value
    
    if loss_per_lot == 0:
        return 0.01

    # Exact lot size
    lot_size = risk_amount_usd / loss_per_lot
    
    # Clamp to broker limits
    min_lot = symbol_info.volume_min
    max_lot = symbol_info.volume_max
    step = symbol_info.volume_step

    # Round to nearest step
    lot_size = round(lot_size / step) * step
    
    if lot_size < min_lot:
        lot_size = min_lot
    if lot_size > max_lot:
        lot_size = max_lot

    return float(lot_size)

def execute_trade(trade):
    trade_id = trade["id"]
    asset = trade["asset"]
    direction = trade["direction"]
    entry = trade["entry_price"]
    tp = trade["tp_price"]
    sl = trade["sl_price"]
    risk_pct = trade["risk_pct"]

    symbol = SYMBOL_MAP.get(asset, asset)

    if not mt5.symbol_select(symbol, True):
        print(f"ERROR: Failed to select {symbol}")
        return False

    lot_size = calculate_lot_size(symbol, entry, sl, risk_pct)
    
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    price = mt5.symbol_info_tick(symbol).ask if direction == "BUY" else mt5.symbol_info_tick(symbol).bid

    print(f"\nEXECUTING #{trade_id} -> {direction} {symbol} | Risk: {risk_pct}% | Lot: {lot_size}")
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 234000,
        "comment": f"Apex Signal #{trade_id}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"ERROR: Order failed, retcode={result.retcode}")
        # Sometimes IOC filling fails on certain brokers, fallback to RETURN
        request["type_filling"] = mt5.ORDER_FILLING_RETURN
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"ERROR: Fallback Order failed, retcode={result.retcode}")
            return False
            
    print(f"SUCCESS: Order executed successfully! Ticket: {result.order}")
    return True

def report_status(trade_id, status, error=None):
    try:
        url = f"{API_BASE_URL}/mt5/confirm/{trade_id}"
        payload = {"status": status}
        if error:
            payload["error"] = error
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Failed to report status to Render: {e}")

def main():
    print("Starting Apex MT5 Bridge...")
    if not connect_mt5():
        return

    print("Polling Render API for pending signals every 5 seconds...")
    while True:
        try:
            r = requests.get(f"{API_BASE_URL}/mt5/pending", timeout=10)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "ok":
                    trades = data.get("trades", [])
                    for trade in trades:
                        success = execute_trade(trade)
                        if success:
                            report_status(trade["id"], "EXECUTED")
                        else:
                            report_status(trade["id"], "FAILED", "Order rejected by MT5")
        except Exception as e:
            print(f"Polling error: {e}")
            
        time.sleep(5)

if __name__ == "__main__":
    main()
