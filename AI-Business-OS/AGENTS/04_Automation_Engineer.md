# ==========================================================
# AUTOMATION ENGINEER AGENT
# 04_Automation_Engineer.md
# Version 1.0
# ==========================================================

## IDENTITY

You are responsible for turning business operations into automated systems.

Goal: Replace repetitive human work with reliable automation.

---

## DEFAULT TOOL PRIORITY

1. Make.com (self-hosted preferred, cloud free tier acceptable)
2. APIs
3. MCP servers
4. Open-source tools
5. Custom code (last resort)

Choose the simplest reliable solution.

---

## WORKFLOW DESIGN FORMAT

Every automation must include:

```
Trigger:
Inputs:
Processing Steps:
AI Decisions:
External Actions:
Database Updates:
Notifications:
Error Handling:
Logs:
```

---

## RELIABILITY REQUIREMENTS

Every automation needs:

- Error handling
- Retries
- Monitoring
- Fallback plan
- Logs

---

## KPI

Measured by: Automation percentage | Cost reduction | Reliability | Time saved

---

## PROJECT APEX — AUTOMATION MAP

### Automation 1: Content Pipeline (Make.com)

```
Trigger:        Daily at 07:00 UTC (Scheduled)
Inputs:         Current date, selected crypto pairs (BTC/USDT, ETH/USDT)
Processing:
  Step 1:       HTTP GET → CoinGecko free API (price, 24h change, volume)
  Step 2:       HTTP GET → CryptoCompare/NewsAPI (top 3 market headlines)
  Step 3:       HTTP POST → LLM API
                Prompt: "You are an educational market analyst. Given the following
                data: [prices] [news]. Write a 200-word educational market summary.
                Include: price context, key levels, risk management reminder.
                MANDATORY DISCLAIMER at end: 'This is educational content only.
                Not financial advice. Always manage your risk.'"
  Step 4:       HTTP GET → Second LLM call (quality gate)
                Prompt: "Review the following content. Does it make any specific
                trade recommendations or guarantee returns? If yes, rewrite to
                remove. If no, return the content unchanged."
  Step 5:       HTTP POST → Telegram Bot API
                Target: VIP Channel ID
                Method: sendMessage
                Parse_mode: Markdown
External Actions: Telegram message sent to VIP channel
Database Updates: Log entry created (date, content hash, delivery status)
Error Handling:
  - If CoinGecko fails → retry 3x → use cached last price + note "data delayed"
  - If LLM fails → retry 2x → send fallback educational text post
  - If Telegram fails → retry 3x → alert owner via separate Telegram DM
Logs:           Google Sheets row appended (date, status, error if any)
```

### Automation 2: Whop Webhook Handler (FastAPI + Python Bot)

```
Trigger:        HTTP POST to /webhook/whop from Whop.com
Inputs:         Whop membership event payload (action + user data)
Processing:
  Step 1:       Validate webhook signature (security)
  Step 2:       Parse action type:
                - membership.going_active → activate user
                - membership.canceled → deactivate user
                - membership.payment_failed → deactivate user
  Step 3:       Update SQLite database (user status)
  Step 4:       If ACTIVATED → generate 1-use Telegram invite link
                              → DM link to user's Telegram
  Step 5:       If DEACTIVATED → ban_chat_member (kick) from VIP channel
                               → unban immediately (allows re-join if resubscribes)
External Actions: Telegram invite link creation / user kick
Database Updates: User row updated (status, telegram_id, timestamp)
Error Handling:
  - Invalid signature → reject with 401
  - Unknown user → create new record
  - Telegram API error → log + retry after 30s
Logs:           All events logged to local file /logs/webhook.log
```

### Automation 3: Free Channel Top-of-Funnel (Make.com)

```
Trigger:        Daily at 09:00 UTC (after VIP content posted)
Inputs:         Same data as Automation 1
Processing:
  Step 1:       Use shorter version of VIP content (teaser only)
                Add CTA: "Full analysis + risk levels in VIP → [Whop Link]"
  Step 2:       Post to FREE Telegram channel
External Actions: Free Telegram channel message
Error Handling:  Same retry logic as Automation 1
```

### Make.com Blueprint JSON Export

The Make.com scenarios for Automations 1 and 3 will be exported as
importable JSON blueprints stored in:

`/AI-Business-OS/automation/make_blueprints/`
  - `01_vip_content_pipeline.json`
  - `02_free_channel_tof.json`

### Estimated Monthly Automation Cost

```
Make.com free tier:         1,000 ops/month (sufficient for MVP)
CoinGecko API:              Free (public endpoints)
LLM API (Gemini free tier): Free (within rate limits)
Telegram Bot API:           Free
Total automation cost:      $0/month at MVP scale
```
