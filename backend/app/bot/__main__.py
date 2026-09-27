"""python -m app.bot: the Telegram bot service (compose service `bot`, docs/telegram-bot.md)."""

import asyncio
import logging
import sys

from app.bot.core import main

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
sys.exit(asyncio.run(main()))
