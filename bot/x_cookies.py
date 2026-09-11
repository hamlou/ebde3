"""
x_cookies.py — Post to X using imported cookies.

Export cookies from your browser using EditThisCookie extension,
save to cookies.json, then use this script to post.

Usage:
    1. Install EditThisCookie extension
    2. Go to x.com (logged in), click extension, Export
    3. Save JSON to: project_apex/x_cookies.json
    4. python x_cookies.py post "Your tweet"
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

COOKIES_FILE = Path(__file__).parent.parent / "x_cookies.json"
SESSION_DIR = Path(__file__).parent.parent / "x_cookie_session"


@dataclass
class PostResult:
    success: bool
    error: Optional[str] = None
    tweet_id: Optional[str] = None


def load_cookies() -> list:
    """Load cookies from JSON file."""
    if not COOKIES_FILE.exists():
        return []
    with open(COOKIES_FILE) as f:
        return json.load(f)


async def check_cookies_valid() -> bool:
    """Check if cookies are still valid."""
    from playwright.async_api import async_playwright

    cookies = load_cookies()
    if not cookies:
        return False

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            pw_cookies = []
            for c in cookies:
                cookie = {
                    "name": c.get("name"),
                    "value": c.get("value"),
                    "domain": c.get("domain", ".x.com"),
                    "path": c.get("path", "/"),
                }
                if c.get("expirationDate"):
                    cookie["expires"] = c["expirationDate"]
                pw_cookies.append(cookie)

            context = await browser.new_context()
            await context.add_cookies(pw_cookies)
            page = await context.new_page()
            await page.goto("https://x.com/home", wait_until="domcontentloaded")
            await asyncio.sleep(2)

            valid = "login" not in page.url and page.url != "https://x.com/"
            await browser.close()
            return valid
    except:
        return False


async def notify_cookies_expired():
    """Send Telegram notification that cookies expired."""
    import os
    try:
        from aiogram import Bot
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        admin_chat = os.getenv("VIP_CHANNEL_ID")  # or your personal chat ID

        if bot_token and admin_chat:
            bot = Bot(token=bot_token)
            await bot.send_message(
                chat_id=admin_chat,
                text="⚠️ <b>X Cookies Expired!</b>\n\n"
                     "X posting is paused.\n\n"
                     "To fix:\n"
                     "1. Open Chrome → x.com\n"
                     "2. EditThisCookie → Export\n"
                     "3. Upload to VPS:\n"
                     "<code>scp x_cookies.json root@62.171.159.136:/opt/project_apex/</code>\n"
                     "4. Restart: <code>systemctl restart project_apex</code>",
                parse_mode="HTML"
            )
            await bot.session.close()
    except Exception as e:
        print(f"[X] Failed to send cookie expiry notification: {e}")


async def post_tweet(text: str, image_path: Optional[str] = None, reply_to_id: Optional[str] = None) -> PostResult:
    """Post tweet using imported cookies. If reply_to_id given, replies to that tweet."""
    from playwright.async_api import async_playwright

    cookies = load_cookies()
    if not cookies:
        return PostResult(success=False, error=f"No cookies found. Export from EditThisCookie to: {COOKIES_FILE}")

    try:
        async with async_playwright() as p:
            # Use fresh context with imported cookies
            browser = await p.chromium.launch(headless=True)

            # Convert EditThisCookie format to Playwright format
            pw_cookies = []
            for c in cookies:
                cookie = {
                    "name": c.get("name"),
                    "value": c.get("value"),
                    "domain": c.get("domain", ".x.com"),
                    "path": c.get("path", "/"),
                }
                if c.get("expirationDate"):
                    cookie["expires"] = c["expirationDate"]
                if c.get("secure"):
                    cookie["secure"] = c["secure"]
                if c.get("httpOnly"):
                    cookie["httpOnly"] = c["httpOnly"]
                if c.get("sameSite"):
                    ss = c["sameSite"].lower()
                    if ss in ["strict", "lax", "none"]:
                        cookie["sameSite"] = ss.capitalize() if ss != "none" else "None"
                pw_cookies.append(cookie)

            context = await browser.new_context()
            await context.add_cookies(pw_cookies)

            page = await context.new_page()

            if reply_to_id:
                # Get our username first to build tweet URL
                await page.goto("https://x.com/home", wait_until="domcontentloaded")
                await asyncio.sleep(3)
                if "login" in page.url or page.url == "https://x.com/":
                    await browser.close()
                    await notify_cookies_expired()
                    return PostResult(success=False, error="Cookies expired")
                # Navigate to the tweet to reply to
                try:
                    handle = await page.locator('[data-testid="UserAvatar-Container-unknown"] a, [data-testid="SideNav_AccountSwitcher_Button"]').first.get_attribute("href")
                    username = handle.strip("/") if handle else ""
                except:
                    username = ""
                # Search for the tweet - try navigating to status URL
                await page.goto(f"https://x.com/i/status/{reply_to_id}", wait_until="domcontentloaded")
                await asyncio.sleep(3)
                # Click the reply button on the tweet
                reply_btn = page.locator('[data-testid="reply"]').first
                if await reply_btn.count():
                    await reply_btn.click()
                    await asyncio.sleep(2)
            else:
                await page.goto("https://x.com/home", wait_until="domcontentloaded")
                await asyncio.sleep(3)

            # Check if logged in
            if "login" in page.url or page.url == "https://x.com/":
                await browser.close()
                await notify_cookies_expired()
                return PostResult(success=False, error="Cookies expired or invalid. Export fresh cookies from x.com")

            # Close any overlays/popups
            try:
                close_btn = page.locator('[data-testid="xMigrationBottomBar"] button, [aria-label="Close"], [data-testid="app-bar-close"]')
                if await close_btn.count():
                    await close_btn.first.click()
                    await asyncio.sleep(1)
            except:
                pass

            # Click anywhere to dismiss overlays
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)

            # Find tweet input
            selectors = [
                '[data-testid="tweetTextarea_0"]',
                '[data-testid^="tweetTextarea"]',
                'div[contenteditable="true"][role="textbox"]',
            ]

            tweet_input = None
            for s in selectors:
                loc = page.locator(s).first
                if await loc.count() > 0:
                    tweet_input = loc
                    break

            if not tweet_input:
                compose = page.locator('[data-testid="SideNav_NewTweet_Button"]')
                if await compose.count():
                    await compose.click()
                    await asyncio.sleep(2)
                    for s in selectors:
                        loc = page.locator(s).first
                        if await loc.count() > 0:
                            tweet_input = loc
                            break

            if not tweet_input:
                # Debug: save screenshot
                await page.screenshot(path="debug_x_cookies.png")
                print(f"Debug: URL={page.url}, screenshot saved")
                await browser.close()
                return PostResult(success=False, error="Could not find tweet input")

            # Type and post - use force to bypass overlays
            await tweet_input.click(force=True)
            await tweet_input.fill(text)
            await asyncio.sleep(1)

            # Image
            if image_path and Path(image_path).exists():
                file_input = page.locator('input[type="file"][accept*="image"]')
                if await file_input.count():
                    await file_input.set_input_files(image_path)
                    await asyncio.sleep(3)

            # Click post - try multiple selectors
            await asyncio.sleep(2)  # Wait for button to enable

            post_selectors = [
                '[data-testid="tweetButtonInline"]',
                '[data-testid="tweetButton"]',
                'button[data-testid*="tweetButton"]',
                '[role="button"][data-testid*="tweet"]',
            ]

            post_btn = None
            for ps in post_selectors:
                loc = page.locator(ps).first
                if await loc.count() > 0:
                    post_btn = loc
                    break

            if post_btn and await post_btn.is_enabled():
                await post_btn.click()
                await asyncio.sleep(4)

                # Try to extract tweet ID from notifications or URL
                tweet_id = None
                try:
                    # Check for toast/success notification with link
                    toast_link = page.locator('a[href*="/status/"]').last
                    if await toast_link.count():
                        href = await toast_link.get_attribute("href")
                        if href and "/status/" in href:
                            tweet_id = href.split("/status/")[-1].split("?")[0].split("/")[0]
                except:
                    pass

                await browser.close()
                return PostResult(success=True, tweet_id=tweet_id)

            # Debug screenshot
            await page.screenshot(path="debug_post_btn.png")
            print(f"Debug screenshot saved: debug_post_btn.png")
            await browser.close()
            return PostResult(success=False, error="Post button not found or disabled")

    except Exception as e:
        return PostResult(success=False, error=str(e))


async def test_cookies():
    """Test if cookies are valid."""
    from playwright.async_api import async_playwright

    cookies = load_cookies()
    if not cookies:
        print(f"❌ No cookies found at: {COOKIES_FILE}")
        return False

    print(f"Found {len(cookies)} cookies")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        pw_cookies = []
        for c in cookies:
            cookie = {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain", ".x.com"),
                "path": c.get("path", "/"),
            }
            if c.get("expirationDate"):
                cookie["expires"] = c["expirationDate"]
            pw_cookies.append(cookie)

        context = await browser.new_context()
        await context.add_cookies(pw_cookies)

        page = await context.new_page()
        await page.goto("https://x.com/home", wait_until="domcontentloaded")
        await asyncio.sleep(3)

        if "login" not in page.url and page.url != "https://x.com/":
            print("✅ Cookies valid! Logged in to X")
            await browser.close()
            return True
        else:
            print("❌ Cookies invalid or expired")
            await browser.close()
            return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python x_cookies.py test           # Test cookies")
        print("  python x_cookies.py post \"text\"    # Post tweet")
        print("")
        print(f"Put your cookies in: {COOKIES_FILE}")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "test":
        asyncio.run(test_cookies())
    elif cmd == "post":
        if len(sys.argv) < 3:
            print("Usage: python x_cookies.py post \"tweet text\"")
            sys.exit(1)
        result = asyncio.run(post_tweet(sys.argv[2]))
        print(f"Success: {result.success}, Error: {result.error}")
    else:
        result = asyncio.run(post_tweet(cmd))
        print(f"Success: {result.success}, Error: {result.error}")
