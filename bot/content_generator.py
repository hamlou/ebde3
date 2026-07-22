import httpx
import json
import os
from urllib.parse import quote
from config import VIP_CHANNEL_ID, FREE_CHANNEL_ID, MAKE_WEBHOOK_URL

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# Key rotation pool — check multiple possible env var names
_GEMINI_KEYS = [
    k for k in [
        os.getenv("GEMINI_API_KEY", ""),
        os.getenv("GEMINI_API_KEY_2", ""),
        os.getenv("GEMINI_API_KEY_3", ""),
        os.getenv("gemini_2", ""),
        os.getenv("gemini_3", ""),
        os.getenv("GEMINI_2", ""),
        os.getenv("GEMINI_3", ""),
        os.getenv("gemini_4", ""),
        os.getenv("gemini_5", ""),
        os.getenv("gemini_6", ""),
        os.getenv("GEMINI_4", ""),
        os.getenv("GEMINI_5", ""),
        os.getenv("GEMINI_6", ""),
    ] if k  # Only keep non-empty keys
]

async def fetch_market_data():
    """Fetches real-time prices for BTC, ETH, Gold, and EUR/USD."""
    data = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Fetch Crypto (CoinGecko)
        try:
            cg_url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"
            cg_resp = await client.get(cg_url)
            if cg_resp.status_code == 200:
                cg_json = cg_resp.json()
                data['BTC'] = cg_json.get('bitcoin', {})
                data['ETH'] = cg_json.get('ethereum', {})
        except Exception as e:
            print(f"Error fetching CoinGecko: {e}")

        # Fetch TradFi (Yahoo Finance via public Chart API)
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            # Gold
            gold_resp = await client.get("https://query1.finance.yahoo.com/v8/finance/chart/GC=F", headers=headers)
            if gold_resp.status_code == 200:
                gold_val = gold_resp.json()['chart']['result'][0]['meta']['regularMarketPrice']
                data['GOLD'] = {"usd": gold_val, "usd_24h_change": 0.0} # Simplified change
                
            # EUR/USD
            eur_resp = await client.get("https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X", headers=headers)
            if eur_resp.status_code == 200:
                eur_val = eur_resp.json()['chart']['result'][0]['meta']['regularMarketPrice']
                data['EUR_USD'] = {"usd": eur_val, "usd_24h_change": 0.0}
        except Exception as e:
            print(f"Error fetching Yahoo Finance: {e}")
            
    return data

def generate_quickchart_url(sentiment_score):
    """
    Generates a radial gauge chart URL showing market sentiment.
    Score: 0-100 (0 = Extreme Bearish, 100 = Extreme Bullish)
    """
    chart_config = {
        "type": "radialGauge",
        "data": {
            "datasets": [{"data": [sentiment_score], "backgroundColor": "green" if sentiment_score > 50 else "red"}]
        },
        "options": {
            "title": {"display": True, "text": "Project Apex Market Sentiment"},
            "centerPercentage": 70,
            "roundedCorners": False
        }
    }
    encoded_config = quote(json.dumps(chart_config))
    return f"https://quickchart.io/chart?c={encoded_config}&w=400&h=300"

async def generate_content(market_data):
    """Uses Gemini with automatic key rotation to generate a structured response."""
    prompt = f"""
    You are an elite, institutional quantitative analyst for "Project Apex" writing a quick Telegram update for your trading floor.
    Review the following real-time market data:
    {json.dumps(market_data, indent=2)}
    
    CRITICAL INSTRUCTION: Your writing MUST NOT sound like an AI. It must sound like a highly intelligent, slightly cynical human trader typing quickly on Telegram. 
    - DO NOT use words like "Furthermore", "Delving into", "Crucial", "It's important to note", "In summary", or "Let's examine". 
    - DO use punchy, direct sentences. Use real trader slang occasionally (e.g., "choppy", "dumping", "sweeping liquidity", "bids stacking", "fakeout").
    - DO NOT use emojis excessively. 1 or 2 is enough.
    - Be brutally objective. If it looks bad, say it looks bad. 
    
    You must output your response in EXACTLY this JSON format (no markdown code blocks, just raw JSON):
    {{
        "sentiment_score": <number between 0 and 100, where 0 is extreme bearish, 50 is neutral, 100 is extreme bullish>,
        "directional_bias": "<Bullish | Bearish | Neutral>",
        "vip_analysis": "<A 150-word highly natural, human-sounding analysis covering BTC, ETH, Gold, and EUR/USD. Use the exact data points provided. Include a short 'NFA' disclaimer at the end.>",
        "free_teaser": "<Leave blank>"
    }}
    """

    if not _GEMINI_KEYS:
        print("No Gemini API keys configured!")
        return None

    for i, key in enumerate(_GEMINI_KEYS):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{GEMINI_API_URL}?key={key}",
                    headers={"Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": prompt}]}]}
                )
                if resp.status_code == 429:
                    print(f"Key {i+1} quota exhausted, trying next key...")
                    continue  # Try next key
                resp.raise_for_status()
                raw_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                text_resp = raw_text.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(text_resp)
                print(f"Success using key {i+1}")
                return parsed
        except Exception as e:
            print(f"Error with key {i+1}: {e}")
            continue

    print("All Gemini API keys exhausted or failed.")
    return None

async def execute_daily_pipeline(bot):
    """The master function that runs the content engine."""
    if not _GEMINI_KEYS:
        print("No GEMINI API Keys configured!")
        return {"status": "error", "message": "Missing API Key"}

    print("Fetching market data...")
    data = await fetch_market_data()
    if not data:
        return {"status": "error", "message": "Failed to fetch data"}
        
    print("Generating AI Analysis...")
    analysis = await generate_content(data)
    if not analysis:
        return {"status": "error", "message": "AI Generation Failed"}
        
    print("Generating Image Chart...")
    image_url = generate_quickchart_url(analysis["sentiment_score"])
    
    # Post Full Analysis to Free Channel (Audience Building Phase)
    free_text = f"🚨 **PROJECT APEX MARKET UPDATE** 🚨\n\n"
    free_text += f"**Directional Bias:** {analysis['directional_bias']} (Sentiment: {analysis['sentiment_score']}/100)\n\n"
    free_text += analysis["vip_analysis"]
    
    print(f"Sending to Free Channel: {FREE_CHANNEL_ID}")
    try:
        await bot.send_photo(chat_id=FREE_CHANNEL_ID, photo=image_url, caption=free_text, parse_mode="Markdown")
    except Exception as e:
        print(f"Failed to post Free: {e}")
        
    # Send to Make.com Webhook for Twitter/X syndication
    if MAKE_WEBHOOK_URL:
        print(f"Sending to Make.com Webhook for Twitter...")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                payload = {
                    "text": free_text,
                    "image_url": image_url
                }
                await client.post(MAKE_WEBHOOK_URL, json=payload)
        except Exception as e:
            print(f"Failed to send to Make.com: {e}")
        
    return {"status": "success", "message": "Content published to Free Channel successfully"}
