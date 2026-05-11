#!/bin/bash
# Wrapper that injects the correct bot token before starting
export TELEGRAM_BOT_TOKEN="${RESALE_BOT_TOKEN:-$TELEGRAM_BOT_TOKEN}"
exec python3 resale_bot.py
