import io
import asyncio
import pandas as pd
import numpy as np
import mplfinance as mpf
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import httpx

# ── Fetch real OHLCV data from Yahoo Finance ───────────────────────────────

async def fetch_ohlcv(symbol: str, interval: str = "1h", range_str: str = "7d") -> pd.DataFrame:
    """Fetch candlestick data from Yahoo Finance public API."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={range_str}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        raw = resp.json()

    result = raw['chart']['result'][0]
    timestamps = result['timestamp']
    quotes = result['indicators']['quote'][0]
    
    df = pd.DataFrame({
        "Open time": pd.to_datetime(timestamps, unit='s'),
        "Open": quotes['open'],
        "High": quotes['high'],
        "Low": quotes['low'],
        "Close": quotes['close'],
        "Volume": quotes['volume']
    })
    
    # Drop rows with NaN values (market closed periods)
    df = df.dropna()
    df.set_index("Open time", inplace=True)
    return df


# ── ICT / SMC Concept Detection ───────────────────────────────────────────────

def detect_fair_value_gaps(df: pd.DataFrame):
    """Find Fair Value Gaps (FVG): bullish (gap up) and bearish (gap down)."""
    fvgs = []
    for i in range(1, len(df) - 1):
        prev_high = df["High"].iloc[i - 1]
        prev_low  = df["Low"].iloc[i - 1]
        curr_high = df["High"].iloc[i]
        curr_low  = df["Low"].iloc[i]
        next_high = df["High"].iloc[i + 1]
        next_low  = df["Low"].iloc[i + 1]
        idx       = df.index[i]

        # Bullish FVG: gap between prev candle's high and next candle's low
        if prev_high < next_low:
            fvgs.append({"type": "bullish", "top": next_low, "bottom": prev_high, "idx": idx, "bar": i})
        # Bearish FVG: gap between next candle's high and prev candle's low
        elif next_high < prev_low:
            fvgs.append({"type": "bearish", "top": prev_low, "bottom": next_high, "idx": idx, "bar": i})
    return fvgs[-4:] if len(fvgs) > 4 else fvgs  # Show only the last 4


def detect_order_blocks(df: pd.DataFrame):
    """Find Order Blocks: last counter-trend candle before a strong displacement."""
    obs = []
    closes = df["Close"].values
    opens  = df["Open"].values
    highs  = df["High"].values
    lows   = df["Low"].values

    for i in range(2, len(df) - 3):
        # Bullish OB: bearish candle followed by strong bullish displacement (3 up closes)
        if closes[i] < opens[i]:  # bearish candle
            if all(closes[j] > opens[j] for j in range(i + 1, i + 3)):
                displacement = closes[i + 2] - opens[i + 1]
                if displacement > 0.002 * closes[i]:
                    obs.append({"type": "bullish", "top": opens[i], "bottom": closes[i], "bar": i})
        # Bearish OB: bullish candle followed by strong bearish displacement
        elif closes[i] > opens[i]:
            if all(closes[j] < opens[j] for j in range(i + 1, i + 3)):
                displacement = opens[i + 1] - closes[i + 2]
                if displacement > 0.002 * closes[i]:
                    obs.append({"type": "bearish", "top": closes[i], "bottom": opens[i], "bar": i})

    return obs[-3:] if len(obs) > 3 else obs


def detect_liquidity_levels(df: pd.DataFrame):
    """Find equal highs/lows that represent liquidity pools."""
    levels = []
    highs = df["High"].values
    lows  = df["Low"].values
    tolerance = 0.0015  # 0.15% tolerance for "equal" levels

    for i in range(5, len(df) - 5):
        # Equal highs (sell-side liquidity above)
        nearby_highs = [highs[j] for j in range(max(0, i - 8), i) if abs(highs[j] - highs[i]) / highs[i] < tolerance]
        if len(nearby_highs) >= 2:
            levels.append({"type": "sell_side", "price": highs[i], "bar": i})
        # Equal lows (buy-side liquidity below)
        nearby_lows = [lows[j] for j in range(max(0, i - 8), i) if abs(lows[j] - lows[i]) / lows[i] < tolerance]
        if len(nearby_lows) >= 2:
            levels.append({"type": "buy_side", "price": lows[i], "bar": i})

    # Deduplicate close levels
    unique = []
    for lv in levels:
        if not any(abs(lv["price"] - u["price"]) / lv["price"] < tolerance * 3 for u in unique):
            unique.append(lv)
    return unique[-6:] if len(unique) > 6 else unique


def detect_structure(df: pd.DataFrame):
    """Detect Break of Structure (BOS) and Change of Character (CHoCH)."""
    events = []
    closes = df["Close"].values
    highs  = df["High"].values
    lows   = df["Low"].values
    n = len(df)

    for i in range(3, n - 1):
        prev_hh = max(highs[max(0, i - 5):i])
        prev_ll = min(lows[max(0, i - 5):i])

        if closes[i] > prev_hh and closes[i - 1] <= prev_hh:
            events.append({"type": "BOS↑", "bar": i, "price": closes[i]})
        elif closes[i] < prev_ll and closes[i - 1] >= prev_ll:
            events.append({"type": "BOS↓", "bar": i, "price": closes[i]})

    return events[-4:] if len(events) > 4 else events


# ── Chart Renderer ─────────────────────────────────────────────────────────────

async def generate_smc_chart(symbol: str = "BTC-USD", interval: str = "1h", range_str: str = "7d") -> bytes:
    """
    Generate a TradingView-style dark chart with ICT/SMC drawings.
    Returns raw PNG bytes ready to send to Telegram.
    """
    df = await fetch_ohlcv(symbol, interval, range_str)

    fvgs   = detect_fair_value_gaps(df)
    obs    = detect_order_blocks(df)
    liq    = detect_liquidity_levels(df)
    structs = detect_structure(df)

    # ── Dark TradingView-style theme ──────────────────────────────────────────
    bg_color     = "#131722"
    grid_color   = "#1e2235"
    text_color   = "#d1d4dc"
    bull_color   = "#26a69a"
    bear_color   = "#ef5350"
    fvg_bull_col = "#26a69a"
    fvg_bear_col = "#ef5350"
    ob_bull_col  = "#1565c0"
    ob_bear_col  = "#b71c1c"

    mc = mpf.make_marketcolors(
        up=bull_color, down=bear_color,
        edge={"up": bull_color, "down": bear_color},
        wick={"up": bull_color, "down": bear_color},
        volume={"up": bull_color, "down": bear_color},
    )
    style = mpf.make_mpf_style(
        marketcolors=mc,
        facecolor=bg_color,
        figcolor=bg_color,
        gridcolor=grid_color,
        gridstyle="-",
        rc={
            "axes.labelcolor": text_color,
            "axes.edgecolor": grid_color,
            "xtick.color": text_color,
            "ytick.color": text_color,
            "text.color": text_color,
            "font.size": 9,
        },
    )

    fig, axes = mpf.plot(
        df, type="candle", style=style,
        figsize=(14, 8), volume=False,
        returnfig=True, tight_layout=True,
        title=f"\n  {symbol}  {interval.upper()}  —  PROJECT APEX",
    )
    ax = axes[0]
    ax.title.set_color(text_color)
    ax.title.set_fontsize(13)
    ax.title.set_fontweight("bold")

    xmin, xmax = ax.get_xlim()
    x_range = xmax - xmin
    n = len(df)

    # ── Draw Fair Value Gaps ───────────────────────────────────────────────────
    for fvg in fvgs:
        bar  = fvg["bar"]
        col  = fvg_bull_col if fvg["type"] == "bullish" else fvg_bear_col
        rect = mpatches.Rectangle(
            (bar, fvg["bottom"]),
            n - bar,
            fvg["top"] - fvg["bottom"],
            linewidth=0, facecolor=col, alpha=0.18, zorder=1
        )
        ax.add_patch(rect)
        ax.text(bar + 0.5, (fvg["top"] + fvg["bottom"]) / 2,
                f"FVG {'↑' if fvg['type'] == 'bullish' else '↓'}",
                color=col, fontsize=7, alpha=0.85, va="center")

    # ── Draw Order Blocks ──────────────────────────────────────────────────────
    for ob in obs:
        bar = ob["bar"]
        col = ob_bull_col if ob["type"] == "bullish" else ob_bear_col
        rect = mpatches.Rectangle(
            (bar, ob["bottom"]),
            n - bar,
            ob["top"] - ob["bottom"],
            linewidth=1, edgecolor=col, facecolor=col, alpha=0.25, zorder=2
        )
        ax.add_patch(rect)
        label = "Bull OB" if ob["type"] == "bullish" else "Bear OB"
        ax.text(bar + 0.5, ob["top"], label, color=col, fontsize=7.5,
                fontweight="bold", va="bottom", alpha=0.9)

    # ── Draw Liquidity Levels ─────────────────────────────────────────────────
    for lv in liq:
        col = "#f59e0b" if lv["type"] == "sell_side" else "#818cf8"
        ax.axhline(y=lv["price"], color=col, linewidth=0.7,
                   linestyle="--", alpha=0.65, zorder=3)
        label = "SSL" if lv["type"] == "sell_side" else "BSL"
        ax.text(xmax - 1, lv["price"], f"  {label} {lv['price']:.1f}",
                color=col, fontsize=7, va="center", alpha=0.85)

    # ── Draw BOS / CHoCH labels ───────────────────────────────────────────────
    for ev in structs:
        col = bull_color if "↑" in ev["type"] else bear_color
        ax.text(ev["bar"], ev["price"], f"  {ev['type']}",
                color=col, fontsize=8, fontweight="bold",
                va="bottom" if "↑" in ev["type"] else "top", alpha=0.9)

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_items = [
        mpatches.Patch(color=fvg_bull_col, alpha=0.5, label="FVG"),
        mpatches.Patch(color=ob_bull_col,  alpha=0.5, label="Order Block"),
        mpatches.Patch(color="#f59e0b",    alpha=0.7, label="Sell-side Liquidity"),
        mpatches.Patch(color="#818cf8",    alpha=0.7, label="Buy-side Liquidity"),
    ]
    ax.legend(handles=legend_items, loc="upper left", fontsize=8,
              facecolor="#1e2235", edgecolor=grid_color, labelcolor=text_color,
              framealpha=0.85)

    # ── Export to bytes ───────────────────────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=bg_color, edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ── Symbol mapping ────────────────────────────────────────────────────────────
SYMBOL_MAP = {
    "BTC":     "BTC-USD",
    "ETH":     "ETH-USD",
    "GOLD":    "GC=F",
    "EUR_USD": "EURUSD=X",
}

async def get_chart_for_asset(asset: str) -> bytes:
    """Return chart bytes for a given asset key (BTC, ETH, GOLD, EUR_USD)."""
    symbol = SYMBOL_MAP.get(asset.upper(), "BTC-USD")
    return await generate_smc_chart(symbol=symbol, interval="1h", range_str="7d")
