#!/bin/bash
# Wrapper so launchd can run the bot with your .env credentials loaded.
# Edit the folder below if you put giveaway-bot somewhere else.
cd "$HOME/giveaway-bot" || exit 1
set -a
source .env
set +a
exec /usr/bin/python3 giveaway_bot.py --live --once
