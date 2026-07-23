"""
market_analyzer.py — Real ICT/SMC Indicator Engine

Architecture upgrade:
  OLD: price → AI (hallucinated levels) → signal
  NEW: OHLCV → smc library → real FVGs/OBs/BOS → AI interprets real numbers → signal

Uses:
  - smartmoneyconcepts (joshyattridge) for FVG, OB, BOS/CHoCH, liquidity
  - ta (pandas-based) for RSI, EMA, MACD
  - Yahoo Finance for free multi-timeframe OHLCV data
"""

import asyncio
import httpx
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from config import TWELVEDATA_API_KEY

# ── Yahoo Finance symbol map ──────────────────────────────────────────────────
YAHOO_MAP = {
    "EUR_USD": "EURUSD=X",
    "USD_JPY": "JPY=X",
    "GBP_USD": "GBPUSD=X",
    "USD_CHF": "CHF=X",
    "AUD_USD": "AUDUSD=X",
    "USD_CAD": "CAD=X",
    "GOLD":    "GC=F",
    "SILVER":  "SI=F",
    "BTC":     "BTC-USD",
}


async def fetch_ohlcv(asset: str, interval: str = "1h", period: str = "30d") -> pd.DataFrame | None:
    """Fetch OHLCV from Yahoo Finance and return a clean pandas DataFrame."""
    symbol = YAHOO_MAP.get(asset)
    if not symbol:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                params={"interval": interval, "range": period}
            )
            resp.raise_for_status()
            data = resp.json()

        result = data["chart"]["result"][0]
        quotes  = result["indicators"]["quote"][0]
        ts      = result["timestamp"]

        df = pd.DataFrame({
            "open":   quotes["open"],
            "high":   quotes["high"],
            "low":    quotes["low"],
            "close":  quotes["close"],
            "volume": quotes.get("volume", [0] * len(ts)),
        }, index=pd.to_datetime(ts, unit="s", utc=True))

        return df.dropna()
    except Exception as e:
        print(f"[fetch_ohlcv] Yahoo Finance failed for {asset} {interval}: {e}")
        
    # FALLBACK to Twelve Data if Yahoo fails
    if TWELVEDATA_API_KEY:
        print(f"[fetch_ohlcv] Attempting Twelve Data fallback for {asset} {interval}...")
        td_symbol = YAHOO_MAP.get(asset, "").replace("=X", "").replace("-USD", "/USD").replace("=F", "")
        # Adjust symbol mapping for Twelve Data (e.g. EUR/USD, XAU/USD, BTC/USD)
        if asset == "GOLD": td_symbol = "XAU/USD"
        elif asset == "SILVER": td_symbol = "XAG/USD"
        elif "USD" in asset and td_symbol == "JPY": td_symbol = "USD/JPY"
        elif "USD" in asset and td_symbol == "CHF": td_symbol = "USD/CHF"
        elif "USD" in asset and td_symbol == "CAD": td_symbol = "USD/CAD"
        elif "_" in asset: td_symbol = asset.replace("_", "/")
        
        # Convert Yahoo interval to Twelve Data interval
        td_interval = interval.replace("d", "day").replace("h", "h").replace("m", "min")
        size = 300 if interval == "1h" else (90 if interval == "1d" else 100)
        
        td_url = f"https://api.twelvedata.com/time_series?symbol={td_symbol}&interval={td_interval}&outputsize={size}&apikey={TWELVEDATA_API_KEY}"
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(td_url)
                resp.raise_for_status()
                data = resp.json()
            
            if "values" not in data:
                print(f"[fetch_ohlcv] Twelve Data returned error: {data}")
                return None
                
            df = pd.DataFrame(data["values"])
            df.rename(columns={"datetime": "timestamp"}, inplace=True)
            df.set_index(pd.to_datetime(df["timestamp"]), inplace=True)
            df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
            df.drop(columns=["timestamp"], inplace=True)
            df = df.sort_index() # Twelve Data returns newest first, so we reverse it
            return df.dropna()
        except Exception as e:
            print(f"[fetch_ohlcv] Twelve Data fallback also failed for {asset}: {e}")
            
    return None


def resample_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H → 4H candles."""
    return df_1h.resample("4h").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum")
    ).dropna()


# ── SMC Indicator Engine ──────────────────────────────────────────────────────

def compute_smc(df: pd.DataFrame, tf_label: str = "4H") -> dict:
    """
    Run smartmoneyconcepts library to compute real ICT indicators.
    Returns a human-readable dict suitable for feeding to the AI.
    """
    out = {}
    try:
        import smartmoneyconcepts as smc

        # 1. Swing Highs/Lows (the anchor for everything else)
        swings = smc.swing_highs_lows(df, swing_length=5)

        # 2. Fair Value Gaps
        fvg_df = smc.fvg(df)
        active_fvgs = fvg_df[(fvg_df["FVG"] != 0) & (fvg_df["MitigatedIndex"] == 0)].tail(4)
        out["fair_value_gaps"] = []
        for idx, (original_index, r) in enumerate(active_fvgs.iterrows()):
            out["fair_value_gaps"].append({
                "id": idx + 1,
                "type": "bullish" if r["FVG"] == 1 else "bearish",
                "top": round(float(r["Top"]), 5),
                "bottom": round(float(r["Bottom"]), 5),
            })

        # 3. BOS and CHoCH
        structure = smc.bos_choch(df, swings)
        recent_bos   = structure[structure["BOS"]   != 0].tail(3)
        recent_choch = structure[structure["CHOCH"] != 0].tail(2)
        out["break_of_structure"] = [
            {"type": "BOS",   "direction": "bullish" if r["Direction"] == 1 else "bearish", "level": round(float(r["Level"]), 5)}
            for _, r in recent_bos.iterrows()
        ]
        out["change_of_character"] = [
            {"type": "CHoCH", "direction": "bullish" if r["Direction"] == 1 else "bearish", "level": round(float(r["Level"]), 5)}
            for _, r in recent_choch.iterrows()
        ]

        # 4. Order Blocks
        obs = smc.ob(df, swings)
        active_obs = obs[obs["OB"] != 0].tail(4)
        out["order_blocks"] = []
        for idx, (original_index, r) in enumerate(active_obs.iterrows()):
            out["order_blocks"].append({
                "id": idx + 1,
                "type": "bullish" if r["OB"] == 1 else "bearish",
                "top": round(float(r["Top"]), 5),
                "bottom": round(float(r["Bottom"]), 5),
            })

        # 5. Liquidity Levels (BSL / SSL)
        liq = smc.liquidity(df, swings)
        active_liq = liq[liq["Liquidity"] != 0].tail(6)
        out["liquidity_levels"] = [
            {
                "type": "buy_side"  if r["Liquidity"] == 1 else "sell_side",
                "level": round(float(r["Level"]), 5),
                "swept": bool(r["Swept"] != 0),
            }
            for _, r in active_liq.iterrows()
        ]

        # 6. Current price position relative to swing range
        recent_highs = swings[swings["HighLow"] == 1]["Level"].tail(3)
        recent_lows  = swings[swings["HighLow"] == -1]["Level"].tail(3)
        if not recent_highs.empty and not recent_lows.empty:
            swing_h = float(recent_highs.max())
            swing_l = float(recent_lows.min())
            current = float(df["close"].iloc[-1])
            pct_in_range = (current - swing_l) / (swing_h - swing_l) * 100 if swing_h != swing_l else 50
            out["premium_discount"] = {
                "swing_high": round(swing_h, 5),
                "swing_low":  round(swing_l, 5),
                "current_price": round(current, 5),
                "zone": "premium" if pct_in_range > 50 else "discount",
                "pct_of_range": round(pct_in_range, 1),
            }

    except Exception as e:
        out["smc_error"] = str(e)

    return out


def compute_ta(df: pd.DataFrame) -> dict:
    """
    Compute standard TA indicators: RSI, EMAs, MACD.
    These confirm or deny the SMC bias.
    """
    out = {}
    try:
        from ta.momentum import RSIIndicator
        from ta.trend import EMAIndicator, MACD as MACDIndicator

        close = df["close"]
        price = float(close.iloc[-1])

        # RSI
        rsi_val = float(RSIIndicator(close, window=14).rsi().iloc[-1])
        out["rsi_14"] = round(rsi_val, 1)
        out["rsi_zone"] = "overbought" if rsi_val > 70 else ("oversold" if rsi_val < 30 else "neutral")

        # EMAs
        ema20  = float(EMAIndicator(close, window=20).ema_indicator().iloc[-1])
        ema50  = float(EMAIndicator(close, window=50).ema_indicator().iloc[-1])
        out["ema_20"]        = round(ema20, 5)
        out["ema_50"]        = round(ema50, 5)
        out["ema_trend"]     = "bullish" if ema20 > ema50 else "bearish"
        out["price_vs_ema20"]= "above" if price > ema20 else "below"
        out["price_vs_ema50"]= "above" if price > ema50 else "below"

        if len(close) >= 200:
            ema200 = float(EMAIndicator(close, window=200).ema_indicator().iloc[-1])
            out["ema_200"]   = round(ema200, 5)
            out["htf_trend"] = "bullish" if price > ema200 else "bearish"

        # MACD
        macd_obj = MACDIndicator(close)
        macd_line   = float(macd_obj.macd().iloc[-1])
        signal_line = float(macd_obj.macd_signal().iloc[-1])
        out["macd_direction"] = "bullish" if macd_line > signal_line else "bearish"
        out["macd_value"]     = round(macd_line, 6)

    except Exception as e:
        out["ta_error"] = str(e)

    return out


async def build_full_context(asset: str) -> dict:
    """
    The main entry point. Fetches multi-timeframe OHLCV, runs SMC + TA,
    and returns a rich structured context dict ready for the AI.
    """
    # Fetch 1H (30d) and daily (90d) concurrently
    df_1h, df_daily = await asyncio.gather(
        fetch_ohlcv(asset, "1h", "30d"),
        fetch_ohlcv(asset, "1d", "90d"),
    )

    if df_1h is None or len(df_1h) < 50:
        return {"asset": asset, "error": "insufficient data", "current_price": None}

    df_4h = resample_4h(df_1h)

    current_price = float(df_1h["close"].iloc[-1])

    ctx = {
        "asset": asset,
        "current_price": round(current_price, 5),
        "candles_1h": len(df_1h),
        "candles_4h": len(df_4h),
    }

    # Daily high/low range
    if df_daily is not None and len(df_daily) >= 5:
        ctx["weekly_high"] = round(float(df_daily["high"].tail(5).max()), 5)
        ctx["weekly_low"]  = round(float(df_daily["low"].tail(5).min()), 5)

    # 4H analysis (primary trading timeframe)
    if len(df_4h) >= 20:
        ctx["4h_smc"] = compute_smc(df_4h, "4H")
        ctx["4h_ta"]  = compute_ta(df_4h)

    # 1H analysis (entry refinement)
    if len(df_1h) >= 50:
        ctx["1h_smc"] = compute_smc(df_1h, "1H")
        ctx["1h_ta"]  = compute_ta(df_1h)

    return ctx
