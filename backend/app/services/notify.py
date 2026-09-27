"""Telegram alerts (Bot API sendMessage). Optional on localhost: a no-op unless both
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set. Never raises, so an alert can't fail the job
that sent it."""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)
# httpx logs every request URL at INFO and the worker's root logger is INFO; the URL holds the token
logging.getLogger("httpx").setLevel(logging.WARNING)


async def notify(text: str, link: str | None = None, buttons: list[list[tuple[str, str]]] | None = None) -> bool:
    """Send text (Telegram HTML: html.escape anything dynamic) with an optional link button, and rows of
    (label, callback_data or https URL) buttons; the bot service (app/bot) answers the callbacks.
    Returns True if Telegram accepted it."""
    if not (settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID):
        return False
    body: dict = {"chat_id": settings.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    rows = [[{"text": t, "url" if d.startswith("https://") else "callback_data": d} for t, d in row] for row in buttons or []]
    if link and link.startswith("https://"):
        rows.append([{"text": "Open in Clipper", "url": link}])
    elif link:  # Telegram rejects localhost button URLs ("Wrong HTTP URL") and drops the whole message
        body["text"] = f"{text}\n{link}"
    if rows:
        body["reply_markup"] = {"inline_keyboard": rows}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage", json=body)
    except Exception as exc:  # the message would contain the URL, which contains the token: log the type only
        logger.warning("telegram notify failed: %s", type(exc).__name__)
        return False
    if r.is_success:
        return True
    logger.warning("telegram notify failed: HTTP %s %s", r.status_code, r.text[:300])
    return False
