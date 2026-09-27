"""Periodic Zernio account sync + disconnect alerts (Phase 4)."""

import html
import logging
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Account
from app.services import zernio
from app.services.notify import notify
from app.tasks import publish
from app.tasks.queue import app

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Europe/London"
DEFAULT_SLOTS = [f"{h:02d}:00" for h in range(7, 24)]  # every hour, 07:00-23:00 (the "Every hour" slot preset)
DISCONNECTED = "ACCOUNT_DISCONNECTED"


async def upsert(s: AsyncSession, parsed: list[dict]) -> None:
    """Apply a parsed GET /v1/accounts (zernio.parse_accounts) and commit. New accounts get default slots;
    known ones only get their connection fields refreshed (slots, cap, timezone, gap, disabled are the
    operator's). An account Zernio no longer lists is disconnected. A connected -> disconnected change
    sends one alert per account per 6 h; a disconnected -> connected one moves the account's
    ACCOUNT_DISCONNECTED posts to their next free slots."""
    now = datetime.now(UTC)
    await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('accounts_sync'))"))  # api sync vs periodic
    known = {a.zernio_account_id: a for a in (await s.scalars(select(Account))).all()}
    went_down, came_back = [], []
    for p in parsed:
        acc = known.pop(p["zernio_account_id"], None)
        if acc is None:
            s.add(Account(**p, timezone=DEFAULT_TIMEZONE, posting_slots={"times": DEFAULT_SLOTS}, connected_at=now))
            continue
        if acc.connection_status != p["connection_status"]:
            if p["connection_status"] == "connected":
                acc.connected_at = now
                came_back.append(acc.id)
            else:
                went_down.append(acc)
        for k, v in p.items():
            setattr(acc, k, v)
    for acc in known.values():  # gone from Zernio
        if acc.connection_status == "connected":
            acc.connection_status = "disconnected"
            went_down.append(acc)
    await s.flush()
    alert = [acc.username for acc in went_down if await publish.claim_account_alert(s, acc.id, DISCONNECTED)]
    await s.commit()
    for account_id in came_back:
        if (await s.get(Account, account_id)).disabled_at is None:
            await publish.reslot_disconnected(s, account_id)
    for username in alert:
        logger.warning("account @%s disconnected in Zernio", username)
        await notify(
            f"Instagram account <b>@{html.escape(username)}</b> is disconnected in Zernio. "
            "Reconnect it there, then Sync accounts in Clipper.",
            f"{settings.APP_BASE_URL}/accounts",
            [[("Reconnect in Zernio", "https://zernio.com"), ("Sync accounts", "sync")]],  # "sync": answered by the bot
        )


async def sync(s: AsyncSession) -> None:
    """Pull Zernio's accounts (read-only) into the database. Raises zernio.ZernioError."""
    await upsert(s, zernio.parse_accounts(await zernio.list_accounts()))


@app.periodic(cron="0 */6 * * *", periodic_id="sync_accounts")
@app.task(name="sync_accounts", queueing_lock="sync_accounts")
async def sync_accounts(timestamp: int) -> None:
    if not settings.ZERNIO_API_KEY:
        logger.info("sync_accounts: ZERNIO_API_KEY not set, skipping")
        return
    async with SessionLocal() as s:
        await sync(s)
