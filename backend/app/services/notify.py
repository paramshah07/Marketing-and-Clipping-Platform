"""Telegram alerts (Bot API sendMessage), to the user the alert is about: every bot of theirs that is paired, has
alerts on and a token Telegram takes (telegram_bots). A no-op for a user without one, or a disabled one (their bots
don't run: nothing would answer the alert's buttons). Never raises, so an alert can't fail the job that sent it."""

import asyncio
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import SessionLocal
from app.core.secrets import unseal
from app.models import TelegramBot, User

logger = logging.getLogger(__name__)
# httpx logs every request URL at INFO and the worker's root logger is INFO; the URL holds the token
logging.getLogger("httpx").setLevel(logging.WARNING)


async def _send(client: httpx.AsyncClient, token: str, body: dict) -> bool:
    try:
        r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body)
    except Exception as exc:  # the message would contain the URL, which contains the token: log the type only
        logger.warning("telegram notify failed: %s", type(exc).__name__)
        return False
    if r.is_success:
        return True
    logger.warning("telegram notify failed: HTTP %s %s", r.status_code, r.text[:300])
    return False


async def notify(
    user_id: int, text: str, link: str | None = None, buttons: list[list[tuple[str, str]]] | None = None
) -> bool:
    """Send user_id text (Telegram HTML: html.escape anything dynamic) with an optional link button, and rows of
    (label, callback_data or https URL) buttons; the bot service (app/bot) answers the callbacks in each chat.
    Returns True if Telegram accepted it from at least one bot."""
    try:  # as user_id: in the api (row-level security) that is the only way to see their bots
        async with SessionLocal(info={"uid": user_id}) as s:
            q = select(TelegramBot.token_enc, TelegramBot.chat_id).join(User, User.id == TelegramBot.user_id).where(
                TelegramBot.user_id == user_id, TelegramBot.alerts, TelegramBot.chat_id.is_not(None),
                TelegramBot.error.is_(None), User.disabled_at.is_(None),
            )  # fmt: skip
            found = (await s.execute(q.order_by(TelegramBot.id))).all()
    except SQLAlchemyError as exc:
        logger.warning("telegram notify failed: %s", type(exc).__name__)
        return False
    bots = [(token, chat) for sealed, chat in found if (token := unseal(sealed))]
    if not bots:
        return False
    body: dict = {"text": text, "parse_mode": "HTML"}
    rows = [[{"text": t, "url" if d.startswith("https://") else "callback_data": d} for t, d in row] for row in buttons or []]
    if link and link.startswith("https://"):
        rows.append([{"text": "Open in Clipper", "url": link}])
    elif link:  # Telegram rejects localhost button URLs ("Wrong HTTP URL") and drops the whole message
        body["text"] = f"{text}\n{link}"
    if rows:
        body["reply_markup"] = {"inline_keyboard": rows}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            sent = await asyncio.gather(*(_send(client, token, body | {"chat_id": chat}) for token, chat in bots))
    except Exception as exc:  # noqa: BLE001 (never fail the job that sent it)
        logger.warning("telegram notify failed: %s", type(exc).__name__)
        return False
    return any(sent)
