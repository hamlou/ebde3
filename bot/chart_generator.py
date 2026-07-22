"""
chart_generator.py — TradingView Widget + ApiFlash Screenshot Engine

Instead of drawing charts with matplotlib (which looked fake), we:
1. Serve a raw HTML page that embeds the OFFICIAL TradingView Advanced Chart widget.
2. Use the ApiFlash API (free tier) to take a real screenshot of that page.
3. Return the screenshot bytes to be sent directly to Telegram.

This gives 100% authentic TradingView chart screenshots for free.
"""
import httpx
import asyncio
from config import APIFLASH_KEY, RENDER_EXTERNAL_URL

# ── TradingView symbol mapping ────────────────────────────────────────────────
# These are the exact TradingView ticker symbols used in their widget
TV_SYMBOL_MAP = {
    "BTC":       "BINANCE:BTCUSDT",
    "ETH":       "BINANCE:ETHUSDT",
    "GOLD":      "TVC:GOLD",
    "SILVER":    "TVC:SILVER",
    "EUR_USD":   "FX:EURUSD",
    "GBP_USD":   "FX:GBPUSD",
    "XAU_USD":   "TVC:GOLD",
}

def get_tv_chart_html(symbol: str, interval: str = "240") -> str:
    """
    Generate the HTML page that embeds the TradingView Advanced Chart widget.
    This is served as a FastAPI endpoint so ApiFlash can screenshot it.
    
    interval: TradingView interval codes
        "15" = 15 min, "60" = 1 hour, "240" = 4 hour, "D" = Daily
    """
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #131722; overflow: hidden; }}
  .tradingview-widget-container {{ 
    width: 1280px; 
    height: 720px;
  }}
</style>
</head>
<body>
<div class="tradingview-widget-container">
  <div id="tradingview_chart"></div>
  <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
  <script type="text/javascript">
  new TradingView.widget({{
    "autosize": false,
    "width": 1280,
    "height": 720,
    "symbol": "{symbol}",
    "interval": "{interval}",
    "timezone": "Etc/UTC",
    "theme": "dark",
    "style": "1",
    "locale": "en",
    "toolbar_bg": "#131722",
    "enable_publishing": false,
    "withdateranges": true,
    "hide_side_toolbar": false,
    "allow_symbol_change": false,
    "container_id": "tradingview_chart",
    "studies": [
      "MASimple@tv-studytemplate",
      "MACD@tv-studytemplate"
    ],
    "overrides": {{
      "mainSeriesProperties.candleStyle.upColor": "#089981",
      "mainSeriesProperties.candleStyle.downColor": "#f23645",
      "mainSeriesProperties.candleStyle.borderUpColor": "#089981",
      "mainSeriesProperties.candleStyle.borderDownColor": "#f23645",
      "mainSeriesProperties.candleStyle.wickUpColor": "#089981",
      "mainSeriesProperties.candleStyle.wickDownColor": "#f23645"
    }},
    "loading_screen": {{ "backgroundColor": "#131722" }}
  }});
  </script>
</div>
</body>
</html>"""


async def get_chart_for_asset(asset: str, bot_base_url: str = None) -> bytes:
    """
    Captures a real TradingView chart screenshot using ApiFlash.
    
    1. Constructs the URL to our own /tv-chart/{asset} endpoint on Render.
    2. Sends that URL to ApiFlash, which renders it in a headless Chrome and returns a PNG.
    3. Returns the raw PNG bytes.
    """
    tv_symbol = TV_SYMBOL_MAP.get(asset.upper(), "BINANCE:BTCUSDT")
    
    # The URL of our OWN chart endpoint that ApiFlash will screenshot
    base_url = bot_base_url or RENDER_EXTERNAL_URL or "https://ebde3.onrender.com"
    chart_page_url = f"{base_url}/tv-chart/{asset.upper()}"
    
    # ApiFlash API — free tier gives 100 screenshots/month
    apiflash_url = "https://api.apiflash.com/v1/urltoimage"
    params = {
        "access_key": APIFLASH_KEY,
        "url": chart_page_url,
        "width": 1280,
        "height": 720,
        "format": "png",
        "quality": 95,
        "delay": 4,           # Wait 4 seconds for TradingView chart to fully render
        "scroll_page": False,
        "response_type": "image",
        "fresh": True,        # Always take a fresh screenshot (no cache)
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(apiflash_url, params=params)
        resp.raise_for_status()
        
        # If ApiFlash returns an error JSON instead of image bytes
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            error_data = resp.json()
            raise ValueError(f"ApiFlash error: {error_data}")
        
        return resp.content
