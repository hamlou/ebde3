import os
import sys
import asyncio
import json

# Load env
from dotenv import load_dotenv
load_dotenv()

# Add bot folder to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot'))

from bot.config import GEMINI_KEYS
from bot.trade_manager import analyze_with_real_data
from bot.market_analyzer import build_full_context

async def main():
    print(f"=== DIAGNOSTIC REPORT ===")
    print(f"Keys loaded: {len(GEMINI_KEYS)}")
    print(f"Keys (masked): {[k[:10]+'...' if k else 'None' for k in GEMINI_KEYS]}")
    
    print("\n[1] Fetching market data...")
    ctx = await build_full_context("GOLD")
    print(f"Market data: {json.dumps(ctx, indent=2)}")
    
    if not ctx.get("current_price"):
        print("ERROR: No market data returned!")
        return
    
    print("\n[2] Testing Gemini AI...")
    try:
        analysis = await analyze_with_real_data([ctx])
        if analysis:
            print(f"SUCCESS: Analysis generated!")
            print(f"Analysis: {json.dumps(analysis, indent=2)}")
        else:
            print("ERROR: Gemini returned None/Empty!")
    except Exception as e:
        print(f"AI ERROR: {e}")

if __name__ == '__main__':
    asyncio.run(main())
