"""Periodic Zernio account sync + disconnect alerts (Phase 4)."""

import html
import logging
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.secrets import unseal
from app.models import Account, User
from app.services import zernio
from app.services.notify import notify
from app.tasks import publish
from app.tasks.queue import app

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Europe/London"
DEFAULT_SLOTS = [f"{h:02d}:00" for h in range(7, 24)]  # every hour, 07:00-23:00 (the "Every hour" slot preset)
DISCONNECTED = "ACCOUNT_DISCONNECTED"


async def upsert(s: AsyncSession, uid: int, parsed: list[dict]) -> list[str]:
    """Apply a parsed GET /v1/accounts (zernio.parse_accounts) to user uid's accounts and commit (user_id is
    explicit: row-level security doesn't scope the worker's superuser session). New accounts get default slots;
    known ones only get their connection fields refreshed (slots, cap, timezone, gap, disabled are the
    operator's). An account Zernio no longer lists is disconnected. A connected -> disconnected change
    sends one alert per account per 6 h; a disconnected -> connected one moves the account's
    ACCOUNT_DISCONNECTED posts to their next free slots. Returns the usernames skipped because another Clipper
    user already has them (one row per Instagram account: two members of one Zernio team see the same ones)."""
    now = datetime.now(UTC)
    lock = text("SELECT pg_advisory_xact_lock(hashtext('accounts_sync:' || CAST(:uid AS text)))")
    await s.execute(lock, {"uid": uid})  # api sync vs periodic, per user
    known = {a.zernio_account_id: a for a in (await s.scalars(select(Account).where(Account.user_id == uid))).all()}
    went_down, came_back, skipped = [], [], []
    for p in parsed:
        acc = known.pop(p["zernio_account_id"], None)
        if acc is None:  # row by row: another user's account skips itself, not the whole sync
            q = insert(Account).values(**p, user_id=uid, timezone=DEFAULT_TIMEZONE,
                                       posting_slots={"times": DEFAULT_SLOTS}, connected_at=now)  # fmt: skip
            q = q.on_conflict_do_nothing(index_elements=["zernio_account_id"]).returning(Account.id)
            if await s.scalar(q) is None:
                skipped.append(p["username"])
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
            uid,
            f"Instagram account <b>@{html.escape(username)}</b> is disconnected in Zernio. "
            "Reconnect it there, then Sync accounts in Clipper.",
            f"{settings.APP_BASE_URL}/accounts",
            [[("Reconnect in Zernio", "https://zernio.com"), ("Sync accounts", "sync")]],  # "sync": answered by the bot
        )
    return skipped


async def sync(s: AsyncSession, uid: int, key: str | None) -> list[str]:
    """Pull Zernio's accounts (read-only, with the user's key) into user uid's; the skipped usernames (upsert).
    Raises zernio.ZernioError."""
    return await upsert(s, uid, zernio.parse_accounts(await zernio.list_accounts(key)))


@app.periodic(cron="0 */6 * * *", periodic_id="sync_accounts")
@app.task(name="sync_accounts", queueing_lock="sync_accounts")
async def sync_accounts(timestamp: int) -> None:
    """Every enabled user with a valid key. A key Zernio refuses goes invalid (publish.key_refused).
    ponytail: sequential; fan out per-user jobs past ~100 users."""
    async with SessionLocal() as s:
        q = select(User.id, User.zernio_key_enc).where(User.zernio_key_status == "valid", User.disabled_at.is_(None))
        users = (await s.execute(q.order_by(User.id))).all()
        await s.commit()
        for uid, sealed in users:
            try:
                if skipped := await sync(s, uid, unseal(sealed)):
                    logger.info("sync_accounts: user %s: held by another user: %s", uid, ", ".join(skipped))
            except zernio.ZernioError as e:
                await s.rollback()
                if e.status == 401:
                    await publish.key_refused(s, uid, publish.refusal("ZERNIO_KEY_INVALID"))
                else:
                    logger.warning("sync_accounts: user %s: %s", uid, e)
            except Exception:  # one user's failure must not stop the rest
                logger.exception("sync_accounts: user %s", uid)
                await s.rollback()
