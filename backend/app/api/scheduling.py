"""Accounts, posts and scheduling (Phase 4). The remedy route lives in app/api/recovery.py (Phase 5).

Errors use HTTPException(detail={"code": ..., "message": ...}), e.g. 409 {"code": "STATE_CONFLICT"}, 422
{"code": "RENDER_NOT_READY" | "FILE_DELETED" | "TOO_LONG" | "ACCOUNT_UNAVAILABLE" | "TOO_SOON"}. A video never
goes to one account twice unasked: a new post of a clip (or of another clip of its link) on an account that already
has one, a draft up to published, is 409 ALREADY_POSTED unless the request says repost, and auto-schedule leaves it
unplaced. Anything that picks or moves
a post's time holds the account's row lock (_lock_account) until commit, so parallel requests can't
double-book a slot or exceed the cap. Manual placement (create, PATCH time) is refused only on an
exact-instant collision (409 SLOT_TAKEN); the daily cap and min gap bind automatic placement, and the
calendar warns about them for manual moves.
"""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_user, zernio_key
from app.api.pipeline import Db
from app.core.config import settings
from app.models import Account, Brand, Post, Render, SourceClip, cas
from app.schemas import (
    AccountOut,
    AccountPatch,
    AutoScheduleIn,
    AutoScheduleOut,
    NextSlot,
    Placed,
    PostCreate,
    PostOut,
    PostPatch,
    PostRender,
    PostStatus,
    Unplaced,
)
from app.services import links, slots, zernio
from app.services.errors import describe
from app.services.storage import url_for
from app.tasks import accounts as account_sync
from app.tasks import publish

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])

MIN_LEAD = timedelta(minutes=-1)  # "now" is fine (the dispatcher takes it within a minute); a minute-precise picker puts it up to 60 s back
AUTO_LEAD = slots.AUTO_LEAD  # automatic placement (auto-schedule, next-slot, late approve) skips nearer slots
EDITABLE = ("DRAFT", "SCHEDULED")
CANCELLABLE = ("DRAFT", "SCHEDULED", "FAILED", "DEAD_LETTER")


def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message, **extra})


def _now() -> datetime:
    return datetime.now(UTC)


def idempotency_key(render_id: int, account_id: int, scheduled_for: datetime, n: int = 0) -> str:
    base = f"{settings.IDEMPOTENCY_SALT}{render_id}:{account_id}:{scheduled_for.astimezone(UTC).isoformat()}"
    return hashlib.sha256((f"{base}:{n}" if n else base).encode()).hexdigest()


async def _free_key(s: AsyncSession, render_id: int, account_id: int, at: datetime) -> str:
    """idempotency_key(render, account, at), counted up while another non-cancelled post holds it (a post
    created at `at` and moved away keeps its key): no two posts that can still publish share a key."""
    n = 0
    while await s.scalar(
        select(Post.id).where(
            Post.idempotency_key == (key := idempotency_key(render_id, account_id, at, n)), Post.status != "CANCELLED"
        ).limit(1)
    ):
        n += 1
    return key


async def _lock_account(s: AsyncSession, account_id: int) -> Account:
    """SELECT ... FOR UPDATE: held until the session commits."""
    if (acc := await slots.lock_account(s, account_id)) is None:
        raise _err(404, "NOT_FOUND", f"account {account_id} not found")
    return acc


async def _slot_taken(s: AsyncSession, account_id: int, at: datetime, exclude_post_id: int | None = None) -> None:
    """409 SLOT_TAKEN when another non-cancelled post of the account goes out at exactly `at`. Under the lock."""
    q = select(Post.id).where(Post.account_id == account_id, Post.status != "CANCELLED", slots.post_time() == at)
    if exclude_post_id is not None:
        q = q.where(Post.id != exclude_post_id)
    if await s.scalar(q.limit(1)) is not None:
        raise _err(409, "SLOT_TAKEN", "another post on this account is already at that time")


def _usable(acc: Account) -> None:
    if acc.disabled_at is not None or acc.connection_status != "connected":
        raise _err(422, "ACCOUNT_UNAVAILABLE", f"@{acc.username} is {'disabled' if acc.disabled_at else 'disconnected'}")


def _aware(t: datetime) -> datetime:
    if t.tzinfo is None:
        raise _err(422, "BAD_TIME", "scheduled_for needs a timezone offset")
    return t.astimezone(UTC)


# ---------------------------------------------------------------- posts out

_POST_ROW = (
    select(Post, Account.username, Render, SourceClip.original_filename, SourceClip.source_url, Brand.name)
    .join(Account, Account.id == Post.account_id)
    .join(Render, Render.id == Post.render_id)
    .join(SourceClip, SourceClip.id == Render.source_clip_id)
    .outerjoin(Brand, Brand.id == Render.brand_id)
)


def _post_out(row) -> PostOut:
    p, username, r, filename, source_url, brand_name = row
    cause, remedy = describe(p.error_code)
    return PostOut(
        id=p.id, render_id=p.render_id, account_id=p.account_id, account_username=username, caption=p.caption,
        scheduled_for=p.scheduled_for, status=p.status, error_code=p.error_code, error_detail=p.error_detail,
        cause=cause, remedy=remedy, attempt_count=p.attempt_count, zernio_post_id=p.zernio_post_id, permalink=p.permalink,
        published_at=p.published_at, created_at=p.created_at, updated_at=p.updated_at,
        render=PostRender(
            id=r.id, clip_id=r.source_clip_id, clip_name=filename or source_url, brand_id=r.brand_id,
            brand_name=brand_name, duration_s=r.duration_s,
            thumbnail_url=url_for(r.thumbnail_key) if r.thumbnail_key else None,
            output_url=url_for(r.output_key) if r.output_key else None,
        ),
    )  # fmt: skip


async def load_post_out(s, post_id: int) -> PostOut:
    """Build one PostOut (joins render, clip, brand, account; cause/remedy via app.services.errors.describe).
    Shared with app/api/recovery.py. 404 {"code": "NOT_FOUND"} if missing."""
    row = (await s.execute(_POST_ROW.where(Post.id == post_id).execution_options(populate_existing=True))).first()
    if row is None:
        raise _err(404, "NOT_FOUND", f"post {post_id} not found")
    return _post_out(row)


# ---------------------------------------------------------------- accounts


async def _account_out(s: AsyncSession, acc: Account, key: str | None) -> AccountOut:
    """key: the user's Zernio key, for the account's publishing quota (None: no quota)."""
    tz = ZoneInfo(acc.timezone)
    start, end = slots.day_bounds(slots.local_day(_now(), tz), tz)
    live = (Post.account_id == acc.id, Post.status != "CANCELLED")
    today = await s.scalar(select(func.count()).where(*live, slots.post_time() >= start, slots.post_time() < end))
    next_at = await s.scalar(
        select(func.min(Post.scheduled_for)).where(
            Post.account_id == acc.id, Post.status.in_(EDITABLE), Post.scheduled_for >= _now()
        )
    )
    out = AccountOut.model_validate(acc)
    out.today_count, out.next_post_at = today, next_at
    out.quota = await zernio.publishing_limit(key, acc.zernio_account_id)
    return out


async def _accounts_out(s: AsyncSession) -> list[AccountOut]:
    accs = (await s.scalars(select(Account).order_by(Account.username))).all()
    outs = [await _account_out(s, a, None) for a in accs]
    key = await zernio_key(s)
    quotas = await asyncio.gather(*(zernio.publishing_limit(key, a.zernio_account_id) for a in accs))
    for out, q in zip(outs, quotas, strict=True):
        out.quota = q
    return outs


@router.get("/accounts")
async def list_accounts(s: Db) -> list[AccountOut]:
    return await _accounts_out(s)


async def sync(s: AsyncSession) -> None:
    """The user's accounts from Zernio (account_sync.sync, with their key). 409 ZERNIO_KEY_MISSING without a key,
    409 ZERNIO_KEY_INVALID when Zernio refuses it (it goes invalid: publish.key_refused), 502 when Zernio fails."""
    uid = s.info["uid"]
    if (key := await zernio_key(s)) is None:
        raise _err(409, "ZERNIO_KEY_MISSING", "add your Zernio API key in Settings first")
    try:
        await account_sync.sync(s, uid, key)
    except zernio.ZernioError as e:
        await s.rollback()
        if e.status == 401:
            await publish.key_refused(s, uid, publish.refusal("ZERNIO_KEY_INVALID"))
            raise _err(409, "ZERNIO_KEY_INVALID", "Zernio refused your key: update it in Settings") from e
        raise _err(502, "ZERNIO_ERROR", str(e)) from e


@router.post("/accounts/sync")
async def sync_accounts(s: Db) -> list[AccountOut]:
    """Pull GET /v1/accounts from Zernio (read-only) and upsert Instagram accounts by zernio_account_id."""
    await sync(s)
    return await _accounts_out(s)


def _clean_times(times: list[str]) -> list[str]:
    out = set()
    for t in times:
        try:
            out.add(datetime.strptime(t, "%H:%M").strftime("%H:%M"))  # noqa: DTZ007 (wall-clock time only)
        except ValueError:
            raise _err(422, "BAD_SLOT", f"slot {t!r} is not HH:MM") from None
    return sorted(out)


@router.patch("/accounts/{account_id}")
async def update_account(account_id: int, body: AccountPatch, s: Db) -> AccountOut:
    acc = await _lock_account(s, account_id)
    fields = body.model_fields_set
    if "timezone" in fields and body.timezone is not None:
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise _err(422, "BAD_TIMEZONE", f"unknown timezone {body.timezone!r}") from None
        acc.timezone = body.timezone
    if "posting_slots" in fields and body.posting_slots is not None:
        acc.posting_slots = {"times": _clean_times(body.posting_slots.times)}
    for f in ("daily_cap", "min_gap_minutes"):
        if f in fields and getattr(body, f) is not None:
            setattr(acc, f, getattr(body, f))
    if "disabled" in fields and body.disabled is not None:
        if body.disabled:
            acc.disabled_at = acc.disabled_at or _now()
            await s.execute(
                update(Post).where(Post.account_id == acc.id, Post.status.in_(CANCELLABLE)).values(status="CANCELLED")
            )
        else:
            acc.disabled_at = None
    await s.commit()
    return await _account_out(s, acc, await zernio_key(s))


@router.get("/accounts/{account_id}/next-slot")
async def next_slot(account_id: int, s: Db) -> NextSlot:
    if (acc := await s.get(Account, account_id)) is None:
        raise _err(404, "NOT_FOUND", f"account {account_id} not found")
    if acc.disabled_at is not None or acc.connection_status != "connected":
        return NextSlot(scheduled_for=None)  # nothing can be scheduled on it (create would 422)
    return NextSlot(scheduled_for=await slots.next_free_slot(s, acc, _now() + AUTO_LEAD))


# ---------------------------------------------------------------- posts

_RENDER_ROW = select(Render, func.coalesce(Brand.auto_approve, False)).outerjoin(Brand, Brand.id == Render.brand_id)
LIVE_KEY_STATUSES = ("CANCELLED", "FAILED", "DEAD_LETTER")  # matches uq_posts_idempotency_key_live


def _unfit(r: Render) -> tuple[str, str] | None:
    """(code, reason) when a render can't be posted."""
    if r.status != "READY":
        return "RENDER_NOT_READY", f"render is {r.status.lower()}, not ready"
    if r.output_key is None:  # POST /api/renders/free-published deleted it
        return "FILE_DELETED", "its MP4 was deleted to free space: render the clip again to post it"
    if r.duration_s is not None and r.duration_s > settings.ZERNIO_MAX_REEL_SECONDS:
        return "TOO_LONG", f"render longer than {settings.ZERNIO_MAX_REEL_SECONDS} s"
    if r.duration_s is not None and r.duration_s < settings.ZERNIO_MIN_REEL_SECONDS:
        return "TOO_SHORT", f"render shorter than {settings.ZERNIO_MIN_REEL_SECONDS} s"
    return None


QUEUED_OR_LIVE = ("DRAFT", "SCHEDULED", "PUBLISHING", "PUBLISHED")  # a post that went, or is to go, out


async def _videos(s: AsyncSession) -> dict[int, str]:
    """Clip id -> links.key of its link, for the user's link clips: two clips with one key are one video."""
    rows = await s.execute(select(SourceClip.id, SourceClip.source_url).where(SourceClip.source_url.is_not(None)))
    return {clip_id: links.key(url) for clip_id, url in rows}


async def _twice(s: AsyncSession, videos: dict[int, str], r: Render, acc: Account) -> str | None:
    """Why posting r on acc would post one video twice: acc has a post (a draft up to published) of r's clip, or of
    another clip of the same link. None when it has none. Under the account lock, so a batch sees its own posts."""
    key = videos.get(r.source_clip_id)
    clips = {r.source_clip_id} | {c for c, k in videos.items() if key and k == key}
    p = await s.scalar(
        select(Post).join(Render, Render.id == Post.render_id)
        .where(Post.account_id == acc.id, Post.status.in_(QUEUED_OR_LIVE), Render.source_clip_id.in_(clips))
        .order_by(slots.post_time().desc()).limit(1)
    )  # fmt: skip
    if p is None:
        return None
    at = (p.published_at or p.scheduled_for).astimezone(ZoneInfo(acc.timezone))
    when = f"{at:%a} {at.day} {at:%b}"
    if p.status == "PUBLISHED":
        return f"this video already went to @{acc.username} on {when} (post {p.id})"
    return f"this video is already queued on @{acc.username} for {when} (post {p.id})"


async def _new_post(
    s: AsyncSession, r: Render, auto_approve: bool, account_id: int, at: datetime, caption: str | None
) -> Post:
    return Post(
        render_id=r.id,
        account_id=account_id,
        caption=caption if caption is not None else (r.caption or ""),
        scheduled_for=at,
        status="SCHEDULED" if auto_approve else "DRAFT",
        idempotency_key=await _free_key(s, r.id, account_id, at),  # set once, never recomputed
    )


async def _post(s: AsyncSession, post_id: int) -> Post:
    if (p := await s.get(Post, post_id)) is None:
        raise _err(404, "NOT_FOUND", f"post {post_id} not found")
    return p


async def _cas_post(s: AsyncSession, post_id: int, from_statuses: tuple[str, ...], **values) -> None:
    if (await s.execute(cas(Post, post_id, from_statuses, **values))).rowcount != 1:
        await s.rollback()
        p = await _post(s, post_id)
        raise _err(409, "STATE_CONFLICT", f"post {post_id} is {p.status}, not {' or '.join(from_statuses)}")


@router.get("/posts")
async def list_posts(
    s: Db,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    account_id: int | None = None,
    brand_id: int | None = None,
    status: Annotated[list[PostStatus] | None, Query()] = None,
) -> list[PostOut]:
    """Sorted by scheduled_for. from/to filter on coalesce(published_at, scheduled_for)."""
    q = _POST_ROW.order_by(Post.scheduled_for, Post.id)
    if from_ is not None:
        q = q.where(slots.post_time() >= from_)
    if to is not None:
        q = q.where(slots.post_time() < to)
    if account_id is not None:
        q = q.where(Post.account_id == account_id)
    if brand_id is not None:
        q = q.where(Render.brand_id == brand_id)
    if status:
        q = q.where(Post.status.in_(status))
    return [_post_out(row) for row in (await s.execute(q)).all()]


@router.get("/posts/{post_id}")
async def get_post(post_id: int, s: Db) -> PostOut:
    return await load_post_out(s, post_id)


@router.post("/posts", status_code=201)
async def create_post(body: PostCreate, s: Db, response: Response) -> PostOut:
    # 201 with a new post; 200 with the existing one when a live post already has the same render, account
    # and time (a double submit). 409 SLOT_TAKEN when another post is at that exact instant.
    # (Comments, not docstrings: those would change openapi.json.)
    at = _aware(body.scheduled_for)
    acc = await _lock_account(s, body.account_id)
    same = (Post.render_id == body.render_id, Post.account_id == acc.id, Post.scheduled_for == at)
    live = await s.scalar(select(Post.id).where(*same, Post.status.not_in(LIVE_KEY_STATUSES)).limit(1))
    if live is not None:
        response.status_code = 200
        return await load_post_out(s, live)
    if (row := (await s.execute(_RENDER_ROW.where(Render.id == body.render_id))).first()) is None:
        raise _err(404, "NOT_FOUND", f"render {body.render_id} not found")
    r, auto_approve = row
    if bad := _unfit(r):
        raise _err(422, *bad)
    _usable(acc)
    if at < _now() + MIN_LEAD:
        raise _err(422, "TOO_SOON", "that time has passed: pick now or later")
    await _slot_taken(s, acc.id, at)
    # last, so a "post it again?" is only asked when nothing else stands in the way
    if not body.repost and (twice := await _twice(s, await _videos(s), r, acc)):
        raise _err(409, "ALREADY_POSTED", f"{twice[0].upper()}{twice[1:]}.")
    post = await _new_post(s, r, auto_approve, acc.id, at, body.caption)
    s.add(post)
    await s.commit()
    return await load_post_out(s, post.id)


@router.post("/posts/auto-schedule")
async def auto_schedule(body: AutoScheduleIn, s: Db) -> AutoScheduleOut:
    # each render, in input order, takes the account's next free slot >= now + 10 min
    acc = await _lock_account(s, body.account_id)
    _usable(acc)
    rows = {row[0].id: row for row in (await s.execute(_RENDER_ROW.where(Render.id.in_(body.render_ids)))).all()}
    after, placed, unplaced, videos = _now() + AUTO_LEAD, [], [], await _videos(s)
    for rid in dict.fromkeys(body.render_ids):  # a repeated id is placed once
        if rid not in rows:
            unplaced.append(Unplaced(render_id=rid, reason="render not found"))
            continue
        r, auto_approve = rows[rid]
        if bad := _unfit(r):
            unplaced.append(Unplaced(render_id=rid, reason=bad[1]))
            continue
        if twice := await _twice(s, videos, r, acc):  # never posts a video twice; placing it by hand asks first
            unplaced.append(Unplaced(render_id=rid, reason=twice))
            continue
        if (at := await slots.next_free_slot(s, acc, after)) is None:
            none = not (acc.posting_slots or {}).get("times")
            unplaced.append(Unplaced(render_id=rid, reason="account has no posting slots" if none else "no free slot within 30 days"))
            continue
        post = await _new_post(s, r, auto_approve, acc.id, at, None)
        s.add(post)
        await s.flush()  # the next render's slot search sees it
        placed.append((rid, post.id))
    await s.commit()
    return AutoScheduleOut(
        placed=[Placed(render_id=rid, post=await load_post_out(s, pid)) for rid, pid in placed], unplaced=unplaced
    )


@router.patch("/posts/{post_id}")
async def update_post(post_id: int, body: PostPatch, s: Db) -> PostOut:
    post, values = await _post(s, post_id), {}
    if body.caption is not None:
        values["caption"] = body.caption
    if body.scheduled_for is not None:
        at = _aware(body.scheduled_for)
        if at < _now() + MIN_LEAD:
            raise _err(422, "TOO_SOON", "that time has passed: pick now or later")
        await _lock_account(s, post.account_id)
        await _slot_taken(s, post.account_id, at, exclude_post_id=post_id)
        values["scheduled_for"] = at  # idempotency_key stays as created
    if values:
        await _cas_post(s, post_id, EDITABLE, **values)
        await s.commit()
    elif post.status not in EDITABLE:
        raise _err(409, "STATE_CONFLICT", f"post {post_id} is {post.status}")
    return await load_post_out(s, post_id)


@router.post("/posts/{post_id}/approve")
async def approve_post(post_id: int, s: Db) -> PostOut:
    # DRAFT -> SCHEDULED; a draft whose time has passed moves to the next free slot
    post = await _post(s, post_id)
    acc = await _lock_account(s, post.account_id)
    await s.refresh(post)
    _usable(acc)
    values: dict = {"status": "SCHEDULED"}
    if post.status == "DRAFT" and post.scheduled_for < _now() + MIN_LEAD:
        if (at := await slots.next_free_slot(s, acc, _now() + AUTO_LEAD, exclude_post_id=post.id)) is None:
            raise _err(422, "TOO_SOON", "its time has passed and there is no free slot within 30 days")
        values["scheduled_for"] = at
    await _cas_post(s, post_id, ("DRAFT",), **values)
    await s.commit()
    return await load_post_out(s, post_id)


@router.post("/posts/{post_id}/cancel")
async def cancel_post(post_id: int, s: Db) -> PostOut:
    await _post(s, post_id)
    await _cas_post(s, post_id, CANCELLABLE, status="CANCELLED")
    await s.commit()
    return await load_post_out(s, post_id)
