import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException
from sqlalchemy.orm import Session
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart

from database import init_db, get_db, User, SessionLocal
from whop_handler import WhopWebhookPayload, process_webhook
from config import TELEGRAM_BOT_TOKEN, VIP_CHANNEL_ID
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

@app.get("/")
def read_root():
    return {"status": "Project Apex MVP is running."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
