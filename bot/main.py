import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart

from database import init_db, get_db, User, SessionLocal
from whop_handler import WhopWebhookPayload, process_webhook
from config import TELEGRAM_BOT_TOKEN, VIP_CHANNEL_ID, FREE_CHANNEL_ID, WHOP_WEBHOOK_SECRET
from telegram_actions import generate_invite_link, kick_user

# Initialize Telegram Bot
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# --- Telegram Bot Handlers ---
@dp.message(CommandStart())
async def command_start_handler(message: types.Message) -> None:
    # MVP: User DMs the bot with their Whop ID to link accounts
    # Format: /start <whop_id>
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Welcome to Project Apex.\n\nPlease provide your Whop ID to link your account. e.g. /start user_xxxx")
        return
        
    whop_id = args[1]
    
    db = SessionLocal()
    user = db.query(User).filter(User.whop_id == whop_id).first()
    
    if not user:
        await message.answer("Whop ID not found. Please ensure you have purchased the subscription on Whop.")
        db.close()
        return
        
    if not user.is_active:
        await message.answer("Your subscription is not currently active.")
        db.close()
        return
        
    # Link Telegram ID
    user.telegram_id = str(message.from_user.id)
    db.commit()
    
    # Generate one-time invite link to VIP channel
    try:
        invite_link = await generate_invite_link(bot, VIP_CHANNEL_ID)
        await message.answer(f"Account linked successfully! Here is your exclusive VIP access link:\n\n{invite_link}\n\n⚠️ Do not share this link, it will only work once.")
    except Exception as e:
        await message.answer(f"Error generating invite link. Please contact support. (Is the bot an admin in the channel?)")
        print(f"Invite error: {e}")
    finally:
        db.close()

# --- FastAPI Setup ---
# When deployed on Render, RENDER_EXTERNAL_URL is automatically provided
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
TELEGRAM_WEBHOOK_PATH = f"/webhook/telegram/{TELEGRAM_BOT_TOKEN}"
TELEGRAM_WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{TELEGRAM_WEBHOOK_PATH}"

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if RENDER_EXTERNAL_URL:
        # Set webhook on Render
        print(f"Setting Telegram webhook to: {TELEGRAM_WEBHOOK_URL}")
        await bot.set_webhook(url=TELEGRAM_WEBHOOK_URL)
    else:
        # Local development fallback: use polling
        print("Starting long polling for local development...")
        asyncio.create_task(dp.start_polling(bot))
    yield
    # Cleanup on shutdown
    if RENDER_EXTERNAL_URL:
        await bot.delete_webhook()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.post(TELEGRAM_WEBHOOK_PATH)
async def telegram_webhook(update: dict):
    """ Endpoint for Telegram to send user messages to """
    telegram_update = types.Update(**update)
    await dp.feed_update(bot=bot, update=telegram_update)
    return {"status": "ok"}

@app.post("/webhook/whop")
async def whop_webhook(payload: WhopWebhookPayload, db: Session = Depends(get_db)):
    """ Endpoint for Whop to send membership events to """
    result = process_webhook(payload, db)
    
    # If user deactivated, kick from Telegram
    if result.get("message") == "User deactivated":
        whop_id = result.get("whop_id")
        user = db.query(User).filter(User.whop_id == whop_id).first()
        if user and user.telegram_id:
            try:
                await kick_user(bot, VIP_CHANNEL_ID, int(user.telegram_id))
                print(f"Kicked user {user.telegram_id} successfully.")
            except Exception as e:
                print(f"Failed to kick user {user.telegram_id}: {e}")
                
    return result

@app.get("/health")
def health_check():
    """ Used by Make.com to ping the server every 14 mins to keep it awake """
    return {"status": "ok", "app": "Project Apex"}

from content_generator import execute_daily_pipeline, fetch_market_data, generate_content, generate_quickchart_url, _GROQ_KEYS

@app.post("/trigger/daily-content")
async def trigger_daily_content(background_tasks: BackgroundTasks, secret: str = None):
    """ Used by cron-job.org to trigger the automated daily post """
    if WHOP_WEBHOOK_SECRET and secret != WHOP_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    background_tasks.add_task(execute_daily_pipeline, bot)
    return {"status": "ok", "message": "Content pipeline triggered in background"}

@app.get("/debug/run-pipeline")
async def debug_pipeline():
    """ Runs the full pipeline synchronously and returns detailed logs for debugging """
    import traceback
    try:
        logs = []
        logs.append(f"Keys loaded: {len(_GROQ_KEYS)}")
        logs.append(f"Keys (masked): {[k[:12]+'...' for k in _GROQ_KEYS]}")
        logs.append(f"VIP_CHANNEL_ID: {VIP_CHANNEL_ID}")
        logs.append(f"FREE_CHANNEL_ID: {FREE_CHANNEL_ID}")
        
        # Step 1: Fetch market data
        try:
            data = await fetch_market_data()
            logs.append(f"Market data fetched: {list(data.keys()) if data else 'EMPTY'}")
        except Exception as e:
            logs.append(f"MARKET DATA ERROR: {e}")
            return {"logs": logs}
        
        # Step 2: Generate AI analysis
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                for idx, key in enumerate(_GROQ_KEYS):
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": "hi"}]}
                    )
                    logs.append(f"Groq Key {idx+1} Status: {resp.status_code}")
                    if resp.status_code != 200:
                        logs.append(f"Groq Key {idx+1} Error: {resp.text[:200]}")
            
            analysis = await generate_content(data)
            if analysis:
                logs.append(f"AI analysis OK - Bias: {analysis.get('directional_bias')}, Score: {analysis.get('sentiment_score')}")
            else:
                logs.append("AI analysis returned None - all keys failed!")
                return {"logs": logs}
        except Exception as e:
            logs.append(f"AI ERROR: {e}")
            return {"logs": logs}
        
        # Step 3: Generate chart
        try:
            image_url = generate_quickchart_url(analysis.get("sentiment_score", 50))
            logs.append(f"Chart URL generated OK")
        except Exception as e:
            logs.append(f"CHART ERROR: {e}")
            return {"logs": logs}

        # Step 4: Send to Telegram
        try:
            import html as html_module
            safe_analysis = html_module.escape(analysis.get('vip_analysis', ''))
            bias_emoji = "🟢" if analysis.get('directional_bias') == 'Bullish' else ("🔴" if analysis.get('directional_bias') == 'Bearish' else "🟡")
            free_text = (
                f"🚨 <b>PROJECT APEX — MARKET UPDATE</b> 🚨\n\n"
                f"{bias_emoji} <b>Directional Bias:</b> {analysis.get('directional_bias')} "
                f"(Sentiment: {analysis.get('sentiment_score')}/100)\n\n"
                f"{safe_analysis}"
            )
            await bot.send_photo(chat_id=FREE_CHANNEL_ID, photo=image_url, caption=free_text, parse_mode="HTML")
            logs.append("Telegram FREE channel post: SUCCESS")
        except Exception as e:
            logs.append(f"TELEGRAM ERROR: {e}")

        return {"status": "done", "logs": logs}
    except Exception as fatal_e:
        return {"status": "fatal_error", "error": str(fatal_e), "traceback": traceback.format_exc()}

@app.get("/")
def read_root():
    return {"status": "Project Apex MVP is running."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
