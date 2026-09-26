"""Posting-slot engine (Phase 4). Pure and tested; the API and the publisher (RATE_LIMITED reslot)
both call next_free_slot."""

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Post

HORIZON = timedelta(days=30)
AUTO_LEAD = timedelta(minutes=10)  # automatic placement (auto-schedule, next-slot, reslots) skips nearer slots


async def lock_account(s: AsyncSession, account_id: int) -> Account | None:
    """SELECT ... FOR UPDATE, held until commit: every slot pick or move takes it first, so parallel
    requests (and the worker's reslots) can't double-book a slot or exceed the cap."""
    q = select(Account).where(Account.id == account_id).with_for_update().execution_options(populate_existing=True)
    return await s.scalar(q)


def post_time():
    """When a post goes (or went) out: what the cap and the gap count against."""
    return func.coalesce(Post.published_at, Post.scheduled_for)


def local_day(t: datetime, tz: ZoneInfo) -> date:
    return t.astimezone(tz).date()


def day_bounds(d: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC [start, end) of local day d (23 or 25 h long on DST days)."""
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def slot_instants(times: Iterable[str], tz: ZoneInfo, after: datetime, until: datetime) -> list[datetime]:
    """Every 'HH:MM' on every local day, as sorted UTC instants in [after, until].
    zoneinfo's fold=0 does both DST rules: a time in the spring gap resolves with the pre-transition
    offset (01:30 London -> 02:30 BST, i.e. shifted forward), a time in the autumn overlap is the first one."""
    hm = [tuple(map(int, t.split(":"))) for t in times]
    d, last = local_day(after, tz) - timedelta(days=1), local_day(until, tz) + timedelta(days=1)
    out = set()
    while d <= last:
        for h, m in hm:
            t = datetime(d.year, d.month, d.day, h, m, tzinfo=tz).astimezone(UTC)
            if after <= t <= until:
                out.add(t)
        d += timedelta(days=1)
    return sorted(out)


def first_free(
    times: Iterable[str], tz: ZoneInfo, cap: int, gap: timedelta, taken: list[datetime], after: datetime,
    until: datetime,
) -> datetime | None:
    """The earliest slot in [after, until] whose local day has < cap posts and that is >= gap from every
    taken instant (exactly gap apart is fine; the same instant is never free, even with gap 0)."""
    per_day = Counter(local_day(t, tz) for t in taken)
    for slot in slot_instants(times, tz, after, until):
        if per_day[local_day(slot, tz)] >= cap:
            continue
        if any(t == slot or abs(t - slot) < gap for t in taken):
            continue
        return slot
    return None


async def next_free_slot(
    s: AsyncSession, account: Account, after: datetime, exclude_post_id: int | None = None
) -> datetime | None:
    """Earliest slot instant (UTC, tz-aware) >= after that respects the account's daily cap (local
    day) and min gap against its other non-cancelled posts; None if nothing is free within 30 days.
    Callers hold a per-account lock (see the API) so two requests can't take the same slot."""
    times = (account.posting_slots or {}).get("times") or []
    if not times:
        return None
    tz, until = ZoneInfo(account.timezone), after + HORIZON
    # two days of margin cover a whole local day at either end, plus any gap up to 12 h
    q = select(post_time()).where(
        Post.account_id == account.id,
        Post.status != "CANCELLED",
        post_time() >= after - timedelta(days=2),
        post_time() <= until + timedelta(days=2),
    )
    if exclude_post_id is not None:
        q = q.where(Post.id != exclude_post_id)
    taken = list((await s.scalars(q)).all())
    gap = timedelta(minutes=account.min_gap_minutes)
    return first_free(times, tz, account.daily_cap, gap, taken, after, until)
