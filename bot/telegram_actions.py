from aiogram import Bot

async def generate_invite_link(bot: Bot, channel_id: str):
    """Generates a single-use invite link for the VIP channel."""
    invite_link = await bot.create_chat_invite_link(
        chat_id=channel_id,
        member_limit=1,
        creates_join_request=False
    )
    return invite_link.invite_link

async def kick_user(bot: Bot, channel_id: str, user_id: int):
    """Kicks a user from the VIP channel by banning and immediately unbanning."""
    await bot.ban_chat_member(chat_id=channel_id, user_id=user_id)
    await bot.unban_chat_member(chat_id=channel_id, user_id=user_id)
