"""
chart_generator.py — TradingView Widget + ApiFlash Screenshot Engine

Serves a raw HTML page embedding the official TradingView Advanced Chart widget.
When trade parameters (entry, tp, sl, direction) are provided, it draws the
official TradingView "Long Position" or "Short Position" drawing tool on the chart.
ApiFlash takes a screenshot of this page to produce the final image.
"""
import httpx
from config import APIFLASH_KEY, RENDER_EXTERNAL_URL

# ── TradingView symbol mapping ────────────────────────────────────────────────
TV_SYMBOL_MAP = {
    "BTC":       "BINANCE:BTCUSDT",
    "ETH":       "BINANCE:ETHUSDT",
    "GOLD":      "TVC:GOLD",
    "XAU_USD":   "TVC:GOLD",
    "SILVER":    "TVC:SILVER",
    "XAG_USD":   "TVC:SILVER",
    "EUR_USD":   "FX:EURUSD",
    "USD_JPY":   "FX:USDJPY",
    "GBP_USD":   "FX:GBPUSD",
    "USD_CHF":   "FX:USDCHF",
    "AUD_USD":   "FX:AUDUSD",
    "USD_CAD":   "FX:USDCAD",
}

def get_tv_chart_html(symbol: str, interval: str = "240",
                      entry: float = None, tp: float = None,
                      sl: float = None, direction: str = None) -> str:
    """
    Generate the HTML page embedding the TradingView Advanced Chart widget.
    If entry/tp/sl/direction are provided, the Long/Short Position drawing
    tool is painted on the chart using the TradingView Widget API.
    """

    # Build the JavaScript that draws the position on the chart (if params given)
    if entry and tp and sl and direction:
        shape_type = "long_position" if direction == "BUY" else "short_position"
        position_js = f"""
        widget.onChartReady(function() {{
            setTimeout(function() {{
                try {{
                    var chart = widget.activeChart();
                    var entryPrice = {entry};
                    var tpPrice    = {tp};
                    var slPrice    = {sl};

                    // Get the last visible bar's time for the shape anchor
                    var bars = chart.getVisibleRange();
                    var anchorTime = bars ? bars.to - 3600 : Math.floor(Date.now() / 1000);

                    chart.createMultipointShape(
                        [{{ time: anchorTime, price: entryPrice }}],
                        {{
                            shape: '{shape_type}',
                            lock: true,
                            disableSelection: true,
                            disableSave: true,
                            overrides: {{
                                stopLevel: slPrice,
                                profitLevel: tpPrice,
                                stopLevelColor: "rgba(239, 83, 80, 0.9)",
                                profitLevelColor: "rgba(8, 153, 129, 0.9)",
                                backgroundColor: "rgba(8, 153, 129, 0.08)",
                            }}
                        }}
                    );
                }} catch(e) {{
                    console.log("Shape draw error:", e);
                }}
            }}, 3500); // wait 3.5s for the chart to fully load all candles
        }});
        """
    else:
        position_js = ""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #131722; overflow: hidden; }}
  #tv_chart_container {{ width: 1280px; height: 720px; }}
</style>
</head>
<body>
<div id="tv_chart_container"></div>
<script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
<script type="text/javascript">
var widget = new TradingView.widget({{
    "container_id": "tv_chart_container",
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
    "save_image": false,
    "hide_top_toolbar": false,
    "studies": [],
    "overrides": {{
        "mainSeriesProperties.candleStyle.upColor": "#089981",
        "mainSeriesProperties.candleStyle.downColor": "#f23645",
        "mainSeriesProperties.candleStyle.borderUpColor": "#089981",
        "mainSeriesProperties.candleStyle.borderDownColor": "#f23645",
        "mainSeriesProperties.candleStyle.wickUpColor": "#089981",
        "mainSeriesProperties.candleStyle.wickDownColor": "#f23645",
        "paneProperties.background": "#131722",
        "paneProperties.vertGridProperties.color": "#2a2e39",
        "paneProperties.horzGridProperties.color": "#2a2e39"
    }},
    "loading_screen": {{ "backgroundColor": "#131722" }}
}});

{position_js}
</script>
</body>
</html>"""


async def get_chart_for_asset(asset: str,
                               entry: float = None, tp: float = None,
                               sl: float = None, direction: str = None) -> bytes:
    """
    Captures a real TradingView chart screenshot using ApiFlash.
    Passes trade parameters via URL query string so the chart endpoint
    can draw the Long/Short Position tool.
    """
    base_url = RENDER_EXTERNAL_URL or "https://ebde3.onrender.com"
    chart_page_url = f"{base_url}/tv-chart/{asset.upper()}"

    # Append position params if provided
    if entry and tp and sl and direction:
        chart_page_url += f"?entry={entry}&tp={tp}&sl={sl}&direction={direction}"

    params = {
        "access_key": APIFLASH_KEY,
        "url": chart_page_url,
        "width": 1280,
        "height": 720,
        "format": "png",
        "quality": 95,
        "delay": 6,        # 6 seconds to let the chart + position shape fully render
        "fresh": True,
        "response_type": "image",
        "no_cookie_banners": True,
        "no_ads": True,
    }

    async with httpx.AsyncClient(timeout=40.0) as client:
        resp = await client.get("https://api.apiflash.com/v1/urltoimage", params=params)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            raise ValueError(f"ApiFlash error: {resp.json()}")
        return resp.content
