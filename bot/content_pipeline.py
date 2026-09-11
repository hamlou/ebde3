"""
content_pipeline.py — Mirror content from source channels to X + Telegram.

Flow:
1. Fetch recent posts from wolfoftrading (text + images)
2. Deduplicate (don't repost same content)
3. Rewrite with our personality
4. Post to X
5. Post to Telegram (VIP: full, FREE: teaser)
"""

import asyncio
from datetime import datetime, timezone, timedelta
from aiogram import Bot
from aiogram.types import BufferedInputFile

from database import SessionLocal, Signal, AuditLog
from content_mirror import (
    mirror, SourcePost,
    rewrite_analysis_simple, rewrite_analysis_ai,
    generate_telegram_post_sync, rewrite_for_telegram
)
from x_poster import post_signal_to_x
from config import VIP_CHANNEL_ID, FREE_CHANNEL_ID

DEDUP_HOURS = 24
MIN_POST_INTERVAL_MINUTES = 20
PEAK_HOURS = range(13, 19)  # 13:00-18:59 UTC — London/NY overlap
OFF_PEAK_MIN_INTERVAL = 40  # Slower posting outside peak


def create_post_hash(post: SourcePost) -> str:
    """Create hash for deduplication."""
    # Use first 50 chars of text + asset
    text_part = post.text[:50].lower().replace(" ", "")
    return f"{post.source_channel}|{post.asset or 'none'}|{text_part}"


def is_duplicate(db, post: SourcePost) -> bool:
    """Check if we've already posted this content."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DEDUP_HOURS)
    post_hash = create_post_hash(post)

    existing = db.query(Signal).filter(
        Signal.signal_hash == post_hash,
        Signal.created_at >= cutoff.isoformat()
    ).first()

    return existing is not None


def can_post_now(db) -> bool:
    """Rate limit posts. Faster during peak hours, slower off-peak."""
    now = datetime.now(timezone.utc)
    interval = MIN_POST_INTERVAL_MINUTES if now.hour in PEAK_HOURS else OFF_PEAK_MIN_INTERVAL
    cutoff = now - timedelta(minutes=interval)

    recent = db.query(Signal).filter(
        Signal.created_at >= cutoff.isoformat()
    ).first()

    return recent is None


async def process_post(
    post: SourcePost,
    bot: Bot,
    post_to_twitter: bool = True,
    post_to_telegram: bool = True,
    use_ai_rewrite: bool = True,
) -> bool:
    """Process a single post: rewrite and distribute."""
    db = SessionLocal()
    try:
        if is_duplicate(db, post):
            print(f"[Pipeline] Skipping duplicate: {post.text[:50]}...")
            return False

        if not can_post_now(db):
            print(f"[Pipeline] Rate limited, waiting...")
            return False

        # Rewrite for X
        if use_ai_rewrite:
            x_post = await rewrite_analysis_ai(post, include_cta=True)
        else:
            x_post = rewrite_analysis_simple(post, include_cta=True)

        print(f"[Pipeline] Processing: {post.asset or 'content'}")
        print(f"[Pipeline] Original: {post.text[:80]}...")
        print(f"[Pipeline] Rewritten: {x_post[:80]}...")

        # Post to X (with image if available)
        x_success = False
        if post_to_twitter:
            result = await post_signal_to_x(
                x_post,
                image_bytes=post.image_bytes if post.has_image() else None
            )
            x_success = result.success
            print(f"[Pipeline] X: {'✅' if x_success else '❌ ' + str(result.error)}")

        # Post to Telegram (with AI rewrite - NEVER copy raw text)
        tg_vip_success = False
        tg_free_success = False

        if post_to_telegram:
            # Rewrite content for Telegram (different from X rewrite)
            tg_rewritten = await rewrite_for_telegram(post.text, post.asset or "Market")
            vip_msg = generate_telegram_post_sync(post, tg_rewritten, is_vip=True)

            try:
                if VIP_CHANNEL_ID:
                    if post.has_image():
                        photo = BufferedInputFile(post.image_bytes, filename="chart.png")
                        await bot.send_photo(
                            chat_id=VIP_CHANNEL_ID,
                            photo=photo,
                            caption=vip_msg,
                            parse_mode="HTML"
                        )
                    else:
                        await bot.send_message(
                            chat_id=VIP_CHANNEL_ID,
                            text=vip_msg,
                            parse_mode="HTML"
                        )
                    tg_vip_success = True
                    print(f"[Pipeline] VIP Telegram ✅" + (" (with image)" if post.has_image() else ""))
            except Exception as e:
                print(f"[Pipeline] VIP Telegram ❌: {e}")

            # Free channel gets teaser (no image)
            free_msg = generate_telegram_post_sync(post, tg_rewritten, is_vip=False)
            try:
                if FREE_CHANNEL_ID:
                    await bot.send_message(
                        chat_id=FREE_CHANNEL_ID,
                        text=free_msg,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                    tg_free_success = True
                    print(f"[Pipeline] FREE Telegram ✅")
            except Exception as e:
                print(f"[Pipeline] FREE Telegram ❌: {e}")

        # Save to database
        db_entry = Signal(
            asset=post.asset or "ANALYSIS",
            direction="ANALYSIS",
            signal_hash=create_post_hash(post),
            source_channel=post.source_channel,
            source_message_id=post.message_id,
            x_posted=x_success,
            telegram_vip_posted=tg_vip_success,
            telegram_free_posted=tg_free_success,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.add(db_entry)

        db.add(AuditLog(
            event_type="CONTENT_POSTED",
            description=f"Mirrored {post.asset or 'content'} from {post.source_channel}",
            timestamp=datetime.now(timezone.utc).isoformat()
        ))

        db.commit()
        return True

    except Exception as e:
        print(f"[Pipeline] Error: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        return False
    finally:
        db.close()


async def run_content_pipeline(bot: Bot):
    """
    Main pipeline job. Fetches content, processes the newest post.
    """
    print(f"\n[Pipeline] Starting content pipeline at {datetime.now(timezone.utc)}")

    try:
        if not mirror.is_connected:
            connected = await mirror.connect()
            if not connected:
                print("[Pipeline] ❌ Failed to connect to Telegram")
                return

        posts = await mirror.fetch_all_sources(hours_back=12)

        if not posts:
            print("[Pipeline] No posts found")
            return

        print(f"[Pipeline] Found {len(posts)} posts from source channels")

        # Process newest non-duplicate post
        for post in posts:
            # Skip posts without meaningful content
            if len(post.text) < 30 and not post.has_image():
                continue

            success = await process_post(
                post=post,
                bot=bot,
                post_to_twitter=True,
                post_to_telegram=True,
                use_ai_rewrite=True,
            )

            if success:
                print(f"[Pipeline] ✅ Posted successfully")
                break
        else:
            print("[Pipeline] No new content to post")

    except Exception as e:
        print(f"[Pipeline] Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    async def test():
        await mirror.connect()
        posts = await mirror.fetch_all_sources(hours_back=48)
        print(f"\nFound {len(posts)} posts")
        for p in posts[:3]:
            print(f"\n- {p.asset}: {p.text[:100]}...")
            print(f"  Image: {p.has_image()}")
            print(f"  Rewrite: {rewrite_analysis_simple(p)}")
        await mirror.disconnect()

    asyncio.run(test())
