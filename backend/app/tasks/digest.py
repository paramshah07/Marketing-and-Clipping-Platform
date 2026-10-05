"""The evening digest (docs/telegram-bot.md section 7): at 20:00 in each user's zone, one message through their alert
bots with how full tomorrow is on each account, the drafts waiting for approval, the Ready tray and the failed posts,
so the night's queueing starts from what's missing."""

import html
import logging
from datetime import UTC, datetime, timedelta
from itertools import groupby
from zoneinfo import ZoneInfo

from sqlalchemy import Row, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Account, Post, Render, User
from app.services import slots
from app.services.notify import notify
from app.tasks.queue import app

logger = logging.getLogger(__name__)

HOUR = 20  # local time in the zone of the user's first account
# ponytail: the first account's zone decides when; a users.timezone if one user's accounts span zones


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def free_slots(times, tz, cap, gap, taken, after, until) -> int:
    """How many more posts automatic placement would fit in [after, until]: slots.first_free, again and again."""
    taken, count = list(taken), 0
    while (slot := slots.first_free(times, tz, cap, gap, taken, after, until)) is not None:
        taken.append(slot)
        count += 1
    return count


async def account_line(s: AsyncSession, acc: Row, now: datetime) -> str:
    """'@a: 9 of 12 slots filled tomorrow, 2 drafts to approve': tomorrow in the account's zone, its posts (as the
    daily cap counts them) out of those plus the slots auto-schedule could still fill (cap and min gap)."""
    tz = ZoneInfo(acc.timezone)
    start, end = slots.day_bounds(slots.local_day(now, tz) + timedelta(days=1), tz)
    q = select(slots.post_time()).where(
        Post.account_id == acc.id, Post.status != "CANCELLED",
        slots.post_time() >= start - timedelta(days=1), slots.post_time() < end + timedelta(days=1),  # the gap's reach
    )  # fmt: skip
    taken = list((await s.scalars(q)).all())
    filled = sum(start <= t < end for t in taken)
    times = (acc.posting_slots or {}).get("times") or []
    gap = timedelta(minutes=acc.min_gap_minutes)
    free = free_slots(times, tz, acc.daily_cap, gap, taken, max(start, now + slots.AUTO_LEAD), end - timedelta(microseconds=1))
    line = f"<b>@{html.escape(acc.username)}</b>: " + (f"{filled} of {filled + free} slots filled tomorrow" if times else "no posting slots")
    if drafts := await s.scalar(select(func.count()).where(Post.account_id == acc.id, Post.status == "DRAFT")):
        line += f", {plural(drafts, 'draft')} to approve /drafts"
    if acc.connection_status != "connected":
        line += " · disconnected in Zernio"
    return line


async def digest_text(s: AsyncSession, uid: int, accounts: list[Row], now: datetime, tz: ZoneInfo) -> str:
    """The superuser's session: every query of many rows filters user_id (row-level security doesn't apply)."""
    day = now.astimezone(tz)
    lines = [f"<b>Evening digest</b> · {day:%a} {day.day} {day:%b}"] + [await account_line(s, a, now) for a in accounts]
    ready = await s.scalar(select(func.count()).select_from(Render).where(  # the Calendar's Ready tray (list_renders)
        Render.user_id == uid, Render.status == "READY", Render.superseded_at.is_(None),
        ~exists().where(Post.render_id == Render.id, Post.status != "CANCELLED"),
    ))  # fmt: skip
    if ready:
        lines.append(f"{plural(ready, 'render')} in the Ready tray /ready")
    if failed := await s.scalar(select(func.count()).where(Post.user_id == uid, Post.status.in_(("FAILED", "DEAD_LETTER")))):
        lines.append(f"{plural(failed, 'failed post')} to recover /failed")
    if (key := await s.scalar(select(User.zernio_key_status).where(User.id == uid))) != "valid":
        why = "Zernio refused your key" if key == "invalid" else "you have no Zernio key"
        lines.append(f"<b>Publishing is paused</b>: {why}. Fix it in Settings.")
    return "\n".join(lines)


@app.periodic(cron="0 * * * *", periodic_id="digest")
@app.task(name="digest", queueing_lock="digest")
async def digest(timestamp: int) -> None:
    """Every hour: the users whose first enabled account's zone reads HOUR o'clock get their digest. Not a user
    without enabled accounts; notify() skips one without an alert bot, or disabled."""
    now = datetime.fromtimestamp(timestamp, UTC)
    async with SessionLocal() as s:
        # rows, not Account objects: a rollback expires loaded objects, and touching one after it raises (MissingGreenlet)
        q = select(Account.id, Account.user_id, Account.username, Account.timezone, Account.posting_slots,
                   Account.daily_cap, Account.min_gap_minutes, Account.connection_status)  # fmt: skip
        q = q.join(User, User.id == Account.user_id).where(Account.disabled_at.is_(None), User.disabled_at.is_(None))
        accounts = (await s.execute(q.order_by(Account.user_id, Account.id))).all()
        await s.commit()
        for uid, group in groupby(accounts, key=lambda a: a.user_id):
            mine = list(group)
            tz = ZoneInfo(mine[0].timezone)
            if now.astimezone(tz).hour != HOUR:
                continue
            try:
                text = await digest_text(s, uid, sorted(mine, key=lambda a: a.username), now, tz)
                await s.commit()  # no transaction held over Telegram
                await notify(uid, text, f"{settings.APP_BASE_URL}/calendar")
            except Exception:  # one user's failure must not stop the rest
                logger.exception("digest: user %s", uid)
                await s.rollback()
