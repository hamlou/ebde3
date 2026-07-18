# Project Apex — Anti-Emotion AI Trading Terminal

An automated AI-powered daily market analysis service delivered via Telegram VIP channel.

## Overview
This repository contains the MVP backend for Project Apex.
- FastAPI server handles incoming webhooks from Whop.com.
- Aiogram Telegram Bot manages VIP channel access (invite generation, kicking).
- SQLite manages user subscription states locally.

## Setup

1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Environment Variables:**
   Copy `.env.example` to `.env` and fill in your keys:
   ```bash
   cp .env.example .env
   ```

3. **Run the Server:**
   ```bash
   python bot/main.py
   ```

## Testing

Run unit tests via Pytest:
```bash
pytest tests/
```

Simulate a webhook:
```bash
python scripts/test_webhook.py
```

## Content Generation

To generate the initial 30-day educational content library (required before launch):
```bash
python scripts/generate_content_library.py
```

## Architecture
See `AI-Business-OS/AGENTS/05_Software_Engineer.md` for full architecture details.
