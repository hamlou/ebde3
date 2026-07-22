import httpx
import json
import os
from urllib.parse import quote
from config import VIP_CHANNEL_ID, FREE_CHANNEL_ID, MAKE_WEBHOOK_URL

# --- API Configuration ---
# We are pivoting to Groq (Llama 3 70B) because it is 100% free, blazingly fast, and requires no credit card.
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Check for Groq keys in environment variables
_GROQ_KEYS = [
    k for k in [
        os.getenv("GROQ_API_KEY", ""),
        os.getenv("groq_api_key", ""),
        os.getenv("groq", ""),
        os.getenv("GROQ", ""),
    ] if k
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
                cg_data = cg_resp.json()
                data['BTC'] = cg_data.get('bitcoin', {})
                data['ETH'] = cg_data.get('ethereum', {})
        except Exception as e:
            print(f"Error fetching crypto: {e}")

        # Fetch Gold & Forex (Yahoo Finance via alternative public endpoint to avoid 403)
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
            print(f"Error fetching traditional markets: {e}")
            
    return data

def generate_quickchart_url(sentiment_score):
    """Generates a gauge chart image URL using QuickChart."""
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
    """Uses Groq (Llama 3) to generate a structured JSON response."""
    system_prompt = """
    You are an elite, institutional quantitative analyst for "Project Apex" writing a quick Telegram update for your trading floor.
    CRITICAL INSTRUCTION: Your writing MUST NOT sound like an AI. It must sound like a highly intelligent, slightly cynical human trader typing quickly on Telegram. 
    - DO NOT use words like "Furthermore", "Delving into", "Crucial", "It's important to note", "In summary", or "Let's examine". 
    - DO use punchy, direct sentences. Use real trader slang occasionally (e.g., "choppy", "dumping", "sweeping liquidity", "bids stacking", "fakeout").
    - DO NOT use emojis excessively. 1 or 2 is enough.
    - Be brutally objective. If it looks bad, say it looks bad. 
    
    You must output your response in EXACTLY this JSON format:
    {
        "sentiment_score": <number 0-100>,
        "directional_bias": "<Bullish | Bearish | Neutral>",
        "vip_analysis": "<150 words of human-sounding analysis. End with NFA.>",
        "free_teaser": "<Leave blank>"
    }
    """

    user_prompt = f"Real-time market data:\n{json.dumps(market_data, indent=2)}\n\nGenerate the JSON analysis."

    if not _GROQ_KEYS:
        print("No Groq API keys configured!")
        return None

    for i, key in enumerate(_GROQ_KEYS):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "response_format": {"type": "json_object"}
                }
                resp = await client.post(
                    GROQ_API_URL,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload
                )
                
                if resp.status_code == 429:
                    print(f"Groq Key {i+1} quota exhausted, trying next...")
                    continue
                    
                resp.raise_for_status()
                raw_text = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(raw_text)
                print(f"Success using Groq key {i+1}")
                return parsed
        except Exception as e:
            print(f"Error with Groq key {i+1}: {e}")
            continue

    print("All Groq API keys exhausted or failed.")
    return None

async def execute_daily_pipeline(bot):
    """The master function that runs the content engine."""
    if not _GROQ_KEYS:
        print("No Groq API Keys configured!")
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
