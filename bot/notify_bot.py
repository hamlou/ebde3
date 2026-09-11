"""
notify_bot.py — Admin Dashboard + Notifications via Followup Bot

Features:
- App-like admin dashboard with buttons
- VIP link generator
- Trade/signal monitoring
- Status notifications
"""

import os
import asyncio
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

FOLLOWUP_BOT_TOKEN = os.getenv("FOLLOWUP_BOT_TOKEN", "8600603795:AAE-cv-Vo6FsLaP8lRdgDP6wHxCeEFkWhX4")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "6660139135")
VIP_CHANNEL_ID = os.getenv("VIP_CHANNEL_ID", "-1004396034184")
FREE_CHANNEL_ID = os.getenv("FREE_CHANNEL_ID", "-1004472769700")
FREE_TELEGRAM_LINK = os.getenv("FREE_TELEGRAM_LINK", "https://t.me/RoyallTraders")

ADMIN_IDS = [6660139135]

_bot = None
_dp = None


async def get_bot():
    global _bot
    if _bot is None:
        _bot = Bot(token=FOLLOWUP_BOT_TOKEN)
    return _bot


# ═══════════════════════════════════════════════════════════════════════════════
# KEYBOARDS
# ═══════════════════════════════════════════════════════════════════════════════

def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 VIP Links", callback_data="admin_links")],
        [InlineKeyboardButton(text="📊 Stats & Analytics", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📈 Open Trades", callback_data="admin_trades")],
        [InlineKeyboardButton(text="📡 Recent Signals", callback_data="admin_signals")],
        [InlineKeyboardButton(text="👥 Members", callback_data="admin_members")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="admin_settings")],
    ])


def links_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Monthly Link (30 days)", callback_data="gen_monthly")],
        [InlineKeyboardButton(text="📆 Weekly Link (7 days)", callback_data="gen_weekly")],
        [InlineKeyboardButton(text="🕐 Daily Link (24 hours)", callback_data="gen_daily")],
        [InlineKeyboardButton(text="« Back", callback_data="admin_home")],
    ])


def back_to_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Back to Dashboard", callback_data="admin_home")],
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_stats():
    try:
        from database import SessionLocal, Trade, Signal, User, FreeMember
        db = SessionLocal()
        try:
            total_signals = db.query(Signal).count()
            total_trades = db.query(Trade).count()
            open_trades = db.query(Trade).filter(Trade.status == "OPEN").count()
            won = db.query(Trade).filter(Trade.status == "WON").count()
            lost = db.query(Trade).filter(Trade.status == "LOST").count()
            win_rate = round((won / (won + lost)) * 100, 1) if (won + lost) > 0 else 0
            vip_members = db.query(User).filter(User.is_active == True).count()
            free_members = db.query(FreeMember).filter(FreeMember.is_active == True).count()
            return {
                "total_signals": total_signals,
                "total_trades": total_trades,
                "open_trades": open_trades,
                "won": won,
                "lost": lost,
                "win_rate": win_rate,
                "vip_members": vip_members,
                "free_members": free_members,
            }
        finally:
            db.close()
    except:
        return {"total_signals": 0, "total_trades": 0, "open_trades": 0, "won": 0, "lost": 0, "win_rate": 0, "vip_members": 0, "free_members": 0}


def get_open_trades():
    try:
        from database import SessionLocal, Trade
        db = SessionLocal()
        try:
            return db.query(Trade).filter(Trade.status == "OPEN").order_by(Trade.id.desc()).limit(10).all()
        finally:
            db.close()
    except:
        return []


def get_recent_signals():
    try:
        from database import SessionLocal, Signal
        db = SessionLocal()
        try:
            return db.query(Signal).order_by(Signal.id.desc()).limit(10).all()
        finally:
            db.close()
    except:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

def setup_handlers(dp: Dispatcher):

    @dp.message(Command("start"))
    async def cmd_start(message: Message):
        if message.from_user.id not in ADMIN_IDS:
            return
        await show_dashboard(message)

    @dp.message(Command("admin"))
    async def cmd_admin(message: Message):
        if message.from_user.id not in ADMIN_IDS:
            return
        await show_dashboard(message)

    @dp.message(Command("genlink"))
    async def cmd_genlink(message: Message):
        if message.from_user.id not in ADMIN_IDS:
            return
        bot = await get_bot()
        await generate_link(bot, message, days=30, label="Monthly")

    @dp.message(F.chat.type == "private")
    async def any_message(message: Message):
        if message.from_user.id not in ADMIN_IDS:
            return
        if message.text and message.text.startswith("/"):
            return
        await show_dashboard(message)

    # Callback handlers
    @dp.callback_query(F.data == "admin_home")
    async def cb_home(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            return
        stats = get_stats()
        await callback.message.edit_text(
            f"🎛 <b>Royal Signals Dashboard</b>\n\n"
            f"📊 <b>Quick Stats</b>\n"
            f"├ Open Trades: <b>{stats['open_trades']}</b>\n"
            f"├ Total Signals: <b>{stats['total_signals']}</b>\n"
            f"├ Win Rate: <b>{stats['win_rate']}%</b> ({stats['won']}W/{stats['lost']}L)\n"
            f"└ Status: 🟢 Online\n\n"
            f"Select an option 👇",
            reply_markup=admin_main_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_links")
    async def cb_links(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            return
        await callback.message.edit_text(
            f"🔗 <b>VIP Link Generator</b>\n\n"
            f"Generate single-use invite links that auto-expire.\n\n"
            f"Select expiration 👇",
            reply_markup=links_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_stats")
    async def cb_stats(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            return
        stats = get_stats()
        await callback.message.edit_text(
            f"📊 <b>Full Analytics</b>\n\n"
            f"<b>Trading Performance</b>\n"
            f"├ Total Signals: <b>{stats['total_signals']}</b>\n"
            f"├ Tracked Trades: <b>{stats['total_trades']}</b>\n"
            f"├ Currently Open: <b>{stats['open_trades']}</b>\n"
            f"├ Won: <b>{stats['won']}</b> ✅\n"
            f"├ Lost: <b>{stats['lost']}</b> ❌\n"
            f"└ Win Rate: <b>{stats['win_rate']}%</b>\n\n"
            f"<b>Community</b>\n"
            f"├ VIP Members: <b>{stats['vip_members']}</b>\n"
            f"└ Free Members: <b>{stats['free_members']}</b>",
            reply_markup=back_to_home(),
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_trades")
    async def cb_trades(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            return
        trades = get_open_trades()
        if not trades:
            text = "<i>No open trades</i>"
        else:
            text = ""
            for t in trades:
                emoji = "🟢" if t.direction == "BUY" else "🔴"
                source = getattr(t, 'source_channel', None) or "APEX AI"
                text += f"{emoji} <b>{t.asset}</b> {t.direction}\n"
                text += f"   E: {t.entry_price} | TP: {t.tp_price} | SL: {t.sl_price}\n"
                text += f"   Source: <i>{source}</i>\n\n"
        await callback.message.edit_text(
            f"📈 <b>Open Trades</b>\n\n{text}",
            reply_markup=back_to_home(),
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_signals")
    async def cb_signals(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            return
        signals = get_recent_signals()
        if not signals:
            text = "<i>No signals yet</i>"
        else:
            text = ""
            for s in signals:
                emoji = "🟢" if s.direction == "BUY" else "🔴"
                x_icon = "✅" if s.x_posted else "❌"
                tg_icon = "✅" if s.telegram_vip_posted else "❌"
                text += f"{emoji} <b>{s.asset}</b> {s.direction}\n"
                text += f"   Src: {s.source_channel} | X:{x_icon} TG:{tg_icon}\n\n"
        await callback.message.edit_text(
            f"📡 <b>Recent Signals</b>\n\n{text}",
            reply_markup=back_to_home(),
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_members")
    async def cb_members(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            return
        stats = get_stats()
        await callback.message.edit_text(
            f"👥 <b>Members Overview</b>\n\n"
            f"<b>VIP Channel</b>\n"
            f"└ Active: <b>{stats['vip_members']}</b>\n\n"
            f"<b>Free Channel</b>\n"
            f"└ Active: <b>{stats['free_members']}</b>",
            reply_markup=back_to_home(),
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_settings")
    async def cb_settings(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            return
        await callback.message.edit_text(
            f"⚙️ <b>Bot Settings</b>\n\n"
            f"<b>Channels</b>\n"
            f"├ VIP: <code>{VIP_CHANNEL_ID}</code>\n"
            f"├ FREE: <code>{FREE_CHANNEL_ID}</code>\n"
            f"└ Link: {FREE_TELEGRAM_LINK}\n\n"
            f"<b>Status</b>\n"
            f"└ Bot: 🟢 Online",
            reply_markup=back_to_home(),
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.callback_query(F.data == "gen_monthly")
    async def cb_gen_monthly(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            return
        bot = await get_bot()
        await generate_link_edit(bot, callback.message, days=30, label="Monthly")
        await callback.answer()

    @dp.callback_query(F.data == "gen_weekly")
    async def cb_gen_weekly(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            return
        bot = await get_bot()
        await generate_link_edit(bot, callback.message, days=7, label="7-Day")
        await callback.answer()

    @dp.callback_query(F.data == "gen_daily")
    async def cb_gen_daily(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            return
        bot = await get_bot()
        await generate_link_edit(bot, callback.message, days=1, label="24-Hour")
        await callback.answer()


async def show_dashboard(message: Message):
    stats = get_stats()
    await message.answer(
        f"🎛 <b>Royal Signals Dashboard</b>\n\n"
        f"📊 <b>Quick Stats</b>\n"
        f"├ Open Trades: <b>{stats['open_trades']}</b>\n"
        f"├ Total Signals: <b>{stats['total_signals']}</b>\n"
        f"├ Win Rate: <b>{stats['win_rate']}%</b> ({stats['won']}W/{stats['lost']}L)\n"
        f"└ Status: 🟢 Online\n\n"
        f"Select an option 👇",
        reply_markup=admin_main_keyboard(),
        parse_mode="HTML"
    )


async def generate_link(bot: Bot, message: Message, days: int, label: str):
    """Generate single-use link. User subscription tracked separately."""
    try:
        invite = await bot.create_chat_invite_link(
            chat_id=VIP_CHANNEL_ID,
            member_limit=1,
            creates_join_request=False
        )
        await message.answer(
            f"✅ <b>{label} VIP Link</b>\n\n"
            f"<code>{invite.invite_link}</code>\n\n"
            f"👤 <b>Single use</b> — link dies after 1 join\n"
            f"📅 <b>{days} days</b> subscription starts when they join\n\n"
            f"<i>User gets kicked automatically after {days} days</i>",
            reply_markup=links_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Error: {e}", parse_mode="HTML")


async def generate_link_edit(bot: Bot, message: Message, days: int, label: str):
    """Generate single-use link. User subscription tracked separately."""
    try:
        invite = await bot.create_chat_invite_link(
            chat_id=VIP_CHANNEL_ID,
            member_limit=1,
            creates_join_request=False
        )
        await message.edit_text(
            f"✅ <b>{label} VIP Link</b>\n\n"
            f"<code>{invite.invite_link}</code>\n\n"
            f"👤 <b>Single use</b> — link dies after 1 join\n"
            f"📅 <b>{days} days</b> subscription starts when they join\n\n"
            f"<i>User gets kicked automatically after {days} days</i>",
            reply_markup=links_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        await message.edit_text(f"❌ Error: {e}", reply_markup=links_keyboard(), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════════════
# NOTIFICATION FUNCTIONS (existing)
# ═══════════════════════════════════════════════════════════════════════════════

async def notify(message: str):
    if not ADMIN_CHAT_ID:
        print(f"[NOTIFY] No ADMIN_CHAT_ID. Message: {message}")
        return
    try:
        bot = await get_bot()
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode="HTML")
    except Exception as e:
        print(f"[NOTIFY] Failed: {e}")


async def notify_sl_hit(pair: str, entry: float, sl: float, direction: str, source: str = "APEX AI"):
    msg = (
        f"🛑 <b>SL HIT</b>\n\n"
        f"<b>Source:</b> {source}\n"
        f"<b>Pair:</b> {pair}\n"
        f"<b>Direction:</b> {direction}\n"
        f"<b>Entry:</b> {entry}\n"
        f"<b>SL:</b> {sl}"
    )
    await notify(msg)


async def notify_tp_hit(pair: str, entry: float, tp: float, direction: str, pips: float, source: str = "APEX AI"):
    msg = (
        f"🎯 <b>TP HIT</b>\n\n"
        f"<b>Source:</b> {source}\n"
        f"<b>Pair:</b> {pair}\n"
        f"<b>Direction:</b> {direction}\n"
        f"<b>Entry:</b> {entry}\n"
        f"<b>TP:</b> {tp}\n"
        f"<b>Profit:</b> +{pips:.1f} pips"
    )
    await notify(msg)


async def notify_signal_tracked(pair: str, direction: str, entry: float, targets: list):
    tp_str = ", ".join(str(t) for t in targets[:3])
    msg = (
        f"📡 <b>SIGNAL TRACKED</b>\n\n"
        f"<b>Pair:</b> {pair}\n"
        f"<b>Direction:</b> {direction}\n"
        f"<b>Entry:</b> {entry}\n"
        f"<b>Targets:</b> {tp_str}\n\n"
        f"<i>Monitoring for 50%/100% TP</i>"
    )
    await notify(msg)


async def notify_50pct_posted(pair: str, profit_pct: float, current_price: float):
    msg = (
        f"📊 <b>50% TP → POSTED TO X</b>\n\n"
        f"<b>Pair:</b> {pair}\n"
        f"<b>Profit:</b> +{profit_pct:.1f}%\n"
        f"<b>Price:</b> {current_price}"
    )
    await notify(msg)


async def notify_100pct_posted(pair: str, profit_pct: float):
    msg = (
        f"🎯 <b>100% TP → POSTED TO X</b>\n\n"
        f"<b>Pair:</b> {pair}\n"
        f"<b>Profit:</b> +{profit_pct:.1f}%"
    )
    await notify(msg)


async def notify_mm_best_result(pair: str, pips: int, source: str = "mmsignalsfx"):
    msg = (
        f"🏆 <b>MM BEST RESULT → POSTED TO X</b>\n\n"
        f"<b>Source:</b> {source}\n"
        f"<b>Pair:</b> {pair}\n"
        f"<b>Best:</b> +{pips} pips"
    )
    await notify(msg)


async def notify_cookie_death(error_detail: str = ""):
    msg = (
        f"🚨 <b>X COOKIES DEAD</b>\n\n"
        f"X posting is DOWN.\n\n"
        f"<b>Fix:</b>\n"
        f"1. Chrome → x.com (logged in)\n"
        f"2. EditThisCookie → Export\n"
        f"3. Upload to server\n"
        f"4. Restart service"
    )
    if error_detail:
        msg += f"\n\n<i>{error_detail[:100]}</i>"
    await notify(msg)


async def notify_cookie_ok():
    await notify("✅ <b>X COOKIES VALID</b>\n\nX posting operational.")


async def notify_error(context: str, error: str):
    await notify(f"⚠️ <b>ERROR</b>\n\n{context}\n{error[:150]}")


# ═══════════════════════════════════════════════════════════════════════════════
# RUN BOT
# ═══════════════════════════════════════════════════════════════════════════════

async def run_followup_bot():
    """Run the followup bot with message handlers."""
    global _bot, _dp
    _bot = Bot(token=FOLLOWUP_BOT_TOKEN)
    _dp = Dispatcher()
    setup_handlers(_dp)
    print("✅ Followup bot started")
    await _dp.start_polling(_bot)


if __name__ == "__main__":
    asyncio.run(run_followup_bot())
