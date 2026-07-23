import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart

from database import init_db, get_db, User, SessionLocal
from whop_handler import WhopWebhookPayload, process_webhook
from config import TELEGRAM_BOT_TOKEN, VIP_CHANNEL_ID, FREE_CHANNEL_ID, WHOP_WEBHOOK_SECRET, API_SECRET_KEY
from telegram_actions import generate_invite_link, kick_user
from chart_generator import get_tv_chart_html, TV_SYMBOL_MAP, get_chart_for_asset
import hmac
import hashlib
from fastapi.security.api_key import APIKeyHeader

# Initialize Telegram Bot
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# --- Telegram Bot Handlers ---
@dp.message(CommandStart())
async def command_start_handler(message: types.Message) -> None:
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
    user.telegram_id = str(message.from_user.id)
    db.commit()
    try:
        invite_link = await generate_invite_link(bot, VIP_CHANNEL_ID)
        await message.answer(f"Account linked successfully! Here is your exclusive VIP access link:\n\n{invite_link}\n\n⚠️ Do not share this link, it will only work once.")
    except Exception as e:
        await message.answer(f"Error generating invite link. Please contact support.")
        print(f"Invite error: {e}")
    finally:
        db.close()

# --- FastAPI Setup ---
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
TELEGRAM_WEBHOOK_PATH = f"/webhook/telegram/{TELEGRAM_BOT_TOKEN}"
TELEGRAM_WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{TELEGRAM_WEBHOOK_PATH}"

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from trade_manager import scan_markets, monitor_positions, daily_wrapup, poll_confirmations

scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # ── Job 1: Market Scanner — scans every 30 minutes for high-conviction setups
    scheduler.add_job(scan_markets, 'interval', minutes=30, args=[bot],
                      id="market_scanner", replace_existing=True)

    # ── Job 2: Position Monitor — checks TP/SL every 5 minutes
    scheduler.add_job(monitor_positions, 'interval', minutes=5, args=[bot],
                      id="position_monitor", replace_existing=True)

    # ── Job 3: Daily Wrap-up — posts winning trades to X at 23:00 UTC every day
    scheduler.add_job(daily_wrapup, 'cron', hour=23, minute=0, args=[bot],
                      id="daily_wrapup", replace_existing=True)

    # ── Job 4: Confirmation Poller — checks awaiting trades for 15m CHoCH every 15 minutes
    scheduler.add_job(poll_confirmations, 'interval', minutes=15, args=[bot],
                      id="confirmation_poller", replace_existing=True)

    scheduler.start()
    print("✅ Trade Engine started: Scanner(30m) | Monitor(5m) | Wrapup(23:00)")

    if RENDER_EXTERNAL_URL:
        print(f"Setting Telegram webhook to: {TELEGRAM_WEBHOOK_URL}")
        await bot.set_webhook(url=TELEGRAM_WEBHOOK_URL)
    else:
        print("Starting long polling for local development...")
        asyncio.create_task(dp.start_polling(bot))
    yield
    scheduler.shutdown()
    if RENDER_EXTERNAL_URL:
        await bot.delete_webhook()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

# ── TradingView Chart HTML endpoint — ApiFlash screenshots this ───────────────
@app.get("/tv-chart/{asset}", response_class=HTMLResponse)
async def tradingview_chart(
    asset: str,
    entry: float = None,
    tp: float = None,
    sl: float = None,
    direction: str = None
):
    """
    Serves the TradingView widget HTML page.
    Accepts optional query params: entry, tp, sl, direction
    to draw the Long/Short Position tool on the chart.
    """
    tv_symbol = TV_SYMBOL_MAP.get(asset.upper(), "BINANCE:BTCUSDT")
    html = get_tv_chart_html(tv_symbol, interval="240", entry=entry, tp=tp, sl=sl, direction=direction)
    return HTMLResponse(content=html)

@app.post(TELEGRAM_WEBHOOK_PATH)
async def telegram_webhook(update: dict):
    telegram_update = types.Update(**update)
    await dp.feed_update(bot=bot, update=telegram_update)
    return {"status": "ok"}

@app.post("/webhook/whop")
async def whop_webhook(request: Request, payload: WhopWebhookPayload, db: Session = Depends(get_db)):
    if WHOP_WEBHOOK_SECRET:
        signature = request.headers.get("x-whop-signature", "")
        body = await request.body()
        expected_sig = hmac.new(WHOP_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_sig, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")

    result = process_webhook(payload, db)
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
    return {"status": "ok", "app": "Project Apex Trade Engine"}

# ── API Key Dependency ─────────────────────────────────────────────────────
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

def get_api_key(api_key: str = Depends(api_key_header)):
    if api_key != API_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Could not validate credentials")
    return api_key

# ── Debug: see full SMC/TA context for any asset ─────────────────────────────
@app.get("/debug/analyze/{asset}")
async def debug_analyze(asset: str, api_key: str = Depends(get_api_key)):
    """Show the real SMC+TA computed context for one asset."""
    import traceback
    try:
        from market_analyzer import build_full_context
        ctx = await build_full_context(asset.upper())
        return {"status": "ok", "context": ctx}
    except Exception as e:
        return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}


# ── MT5 Bridge Endpoints ───────────────────────────────────────────────────
from pydantic import BaseModel
class MT5ConfirmRequest(BaseModel):
    status: str
    error: str = None
    fill_price: float = None

@app.get("/mt5/pending")
def get_pending_mt5_trades(api_key: str = Depends(get_api_key)):
    """Local Windows MT5 script polls this every 5 seconds to find new trades."""
    from database import SessionLocal, Trade
    db = SessionLocal()
    try:
        pending = db.query(Trade).filter(Trade.mt5_status == "PENDING", Trade.status == "OPEN").all()
        return {
            "status": "ok",
            "trades": [
                {
                    "id": t.id,
                    "asset": t.asset,
                    "direction": t.direction,
                    "entry_price": float(t.entry_price),
                    "tp_price": float(t.tp_price),
                    "sl_price": float(t.sl_price),
                    "risk_pct": float(t.risk_pct) if t.risk_pct else 1.0
                } for t in pending
            ]
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        db.close()

@app.post("/mt5/confirm/{trade_id}")
def confirm_mt5_trade(trade_id: int, req: MT5ConfirmRequest, api_key: str = Depends(get_api_key)):
    """Local Windows MT5 script reports back success or failure."""
    from database import SessionLocal, Trade
    db = SessionLocal()
    try:
        trade = db.query(Trade).filter(Trade.id == trade_id).first()
        if not trade:
            return {"status": "error", "error": "trade not found"}
        trade.mt5_status = req.status
        if req.fill_price is not None:
            trade.actual_entry_price = str(req.fill_price)
        db.commit()
        return {"status": "ok", "message": f"Trade {trade_id} marked as {req.status}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        db.close()

# ── Debug endpoint — manually trigger a market scan ──────────────────────────
@app.get("/debug/scan-now")
async def debug_scan_now(api_key: str = Depends(get_api_key)):
    """Manually trigger a market scan for testing (posts to Telegram on signal)."""
    import traceback
    try:
        await scan_markets(bot)
        return {"status": "done", "message": "Market scan triggered. Check Telegram for new signals."}
    except Exception as e:
        return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

# ── Debug endpoint — manually trigger the full pipeline (1 trade + chart) ────
@app.get("/debug/run-pipeline")
async def debug_run_pipeline(api_key: str = Depends(get_api_key)):
    """Force-trigger the market scanner right now (posts to Telegram)."""
    import traceback
    try:
        await scan_markets(bot)
        return {"status": "done", "message": "Market scan triggered. Check Telegram for new signals."}
    except Exception as e:
        return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

# ── Debug endpoint — test the TradingView chart screenshot ────────────────────
@app.get("/debug/test-chart/{asset}")
async def debug_test_chart(asset: str, api_key: str = Depends(get_api_key)):
    """Test the ApiFlash + TradingView screenshot pipeline for a specific asset."""
    import traceback
    try:
        from chart_generator import get_chart_for_asset
        from aiogram.types import BufferedInputFile
        chart_bytes = await get_chart_for_asset(asset.upper())
        photo = BufferedInputFile(chart_bytes, filename=f"{asset}_test.png")
        await bot.send_photo(
            chat_id=FREE_CHANNEL_ID,
            photo=photo,
            caption=f"📸 Chart test for {asset.upper()} — TradingView widget via ApiFlash"
        )
        return {"status": "ok", "message": f"Chart for {asset} sent to Telegram. Size: {len(chart_bytes)} bytes"}
    except Exception as e:
        return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

@app.get("/")
def read_root():
    return {"status": "Project Apex Trade Engine is running.", "endpoints": [
        "/debug/scan-now", "/debug/run-pipeline", "/debug/test-chart/{asset}", "/tv-chart/{asset}"
    ]}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
