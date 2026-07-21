import os
import sys
import asyncio
import json

# Load env
from dotenv import load_dotenv
load_dotenv()

# Add bot folder to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot'))

from content_generator import fetch_market_data, generate_content, generate_quickchart_url, _GEMINI_KEYS

async def main():
    print(f"=== DIAGNOSTIC REPORT ===")
    print(f"Keys loaded: {len(_GEMINI_KEYS)}")
    print(f"Keys (masked): {[k[:10]+'...' for k in _GEMINI_KEYS]}")
    
    print("\n[1] Fetching market data...")
    data = await fetch_market_data()
    print(f"Market data: {json.dumps(data, indent=2)}")
    
    if not data:
        print("ERROR: No market data returned!")
        return
    
    print("\n[2] Testing Gemini AI...")
    analysis = await generate_content(data)
    if analysis:
        print(f"SUCCESS: Analysis generated!")
        print(f"Bias: {analysis.get('directional_bias')}")
        print(f"Score: {analysis.get('sentiment_score')}")
    else:
        print("ERROR: Gemini returned None!")
    
    print("\n[3] Testing Chart URL...")
    url = generate_quickchart_url(70)
    print(f"Chart URL: {url[:80]}...")

if __name__ == '__main__':
    asyncio.run(main())
