"""Posting-slot engine (Phase 4). Pure and tested; the API and the publisher (RATE_LIMITED reslot)
both call next_free_slot."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account


async def next_free_slot(
    s: AsyncSession, account: Account, after: datetime, exclude_post_id: int | None = None
) -> datetime | None:
    """Earliest slot instant (UTC, tz-aware) >= after that respects the account's daily cap (local
    day) and min gap against its other non-cancelled posts; None if nothing is free within 30 days.
    Callers hold a per-account lock (see the API) so two requests can't take the same slot."""
    raise NotImplementedError
