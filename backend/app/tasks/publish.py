"""Dispatcher and publish_post state machine (docs/PLAN.md section 4). Every post state change is a
compare-and-set; Zernio HTTP lives in app.services.publisher. Nothing here runs unless PUBLISHING_ENABLED.
A post publishes with its owner's Zernio key (users.zernio_key_enc), read under the claim's lock."""

import asyncio
import html
import logging
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from procrastinate import RetryStrategy
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.secrets import unseal
from app.models import Account, Post, Render, SourceClip, User, cas
from app.services import publisher, slots, zernio
from app.services.errors import describe
from app.services.notify import notify
from app.services.publisher import KEY_CODES, Later, NetworkError, Rejected
from app.tasks.queue import app

logger = logging.getLogger(__name__)

MAX_RETRIES = 3  # NetworkError retries of one publish_post job (4 runs), then DEAD_LETTER
MAX_ORPHAN_REDEFERS = 3  # PUBLISHING posts whose job vanished (worker killed), then DEAD_LETTER WORKER_CRASHED
OVERDUE = timedelta(minutes=30)  # a SCHEDULED post later than this moves to the next free slot instead
KEY_WINDOW = timedelta(hours=20)  # Zernio replays a key for 24 h: never POST past 20 h after first_post_at
POLL_S = 60
PRIORITY = 10  # dispatch and publish_post jump the queue ahead of renders (priority 0)
UPLOAD_ABANDONED_AFTER = timedelta(hours=24)
ACCOUNT_CODES = {"ACCOUNT_DISCONNECTED", "RATE_LIMITED", "PROFILE_OVER_LIMIT"}  # one alert per (account, code) per 6 h
ACCOUNT_ALERT_EVERY = timedelta(hours=6)
RECOVERABLE = ("FAILED", "DEAD_LETTER")


def maybe_live(post: Post) -> bool:
    """A POST went out with this key and Zernio never told us the post id: the Reel may be live. Such a post
    keeps its key and media URL for good (only a replay of the same key is safe) and is never re-keyed."""
    return post.first_post_at is not None and post.zernio_post_id is None


def _now() -> datetime:
    return datetime.now(UTC)


_held: ContextVar[list | None] = ContextVar("held_alerts", default=None)


async def alert(user_id: int, text_: str, link: str | None = None, buttons=None) -> None:
    """notify(), or held until the end of the dispatch tick (dispatch): no due post waits on Telegram."""
    if (held := _held.get()) is not None:
        held.append((user_id, text_, link, buttons))
    else:
        await notify(user_id, text_, link, buttons)


async def _key(s: AsyncSession, user_id: int, claim: bool) -> tuple[str, int] | None:
    """The user's Zernio key and its generation (users.zernio_key_gen), None when there is none that opens.
    claim: only a valid key of an enabled user, and FOR SHARE until the claim commits: a key change (FOR UPDATE,
    app/api/auth.py) waits for it, then sees the post PUBLISHING and is refused, so a post publishes with the key it
    was claimed with. A PUBLISHING re-run reads it as it is: it can't have changed meanwhile."""
    q = select(User.zernio_key_enc, User.zernio_key_gen).where(User.id == user_id)
    if claim:
        q = q.where(User.zernio_key_status == "valid", User.disabled_at.is_(None)).with_for_update(read=True)
    row = (await s.execute(q)).first()
    key = row and unseal(row[0])
    return (key, row[1]) if key else None


def refusal(code: str, detail: dict | None = None) -> str:
    """users.zernio_error for a key code (publisher.KEY_CODES)."""
    if code == "ZERNIO_PAYMENT_REQUIRED":
        return "Zernio reports a failed payment on your Zernio account"
    if group := (detail or {}).get("required_group"):
        return f"the key has Zernio's {group} group disabled"
    return "Zernio refused the key"


async def key_refused(s: AsyncSession, user_id: int, reason: str) -> None:
    """Zernio refused the user's key (or account): it goes invalid, so dispatch skips their posts until a Re-check or
    a new key, with one alert per valid -> invalid flip. Commits: callers commit their post change first (a key
    change locks users, then posts: the other order could deadlock with it)."""
    q = update(User).where(User.id == user_id, User.zernio_key_status == "valid")
    r = await s.execute(q.values(zernio_key_status="invalid", zernio_error=reason, zernio_checked_at=func.now()))
    await s.commit()
    if r.rowcount == 1:
        await alert(user_id, f"Publishing is paused: {html.escape(reason)}. Update your Zernio key in Settings, "
                    "then retry the failed posts.", f"{settings.APP_BASE_URL}/settings")  # fmt: skip


async def _set(s: AsyncSession, post_id: int, from_statuses, guard=None, **values) -> bool:
    q = cas(Post, post_id, from_statuses, **values)
    return (await s.execute(q if guard is None else q.where(guard))).rowcount == 1


async def claim_account_alert(s: AsyncSession, account_id: int, code: str) -> bool:
    """Atomically stamp accounts.last_alerts[code] unless it was stamped within 6 h (shared with the
    account sync). Runs in the caller's transaction."""
    now = _now()
    q = text(
        "UPDATE accounts SET last_alerts ="
        " last_alerts || jsonb_build_object(CAST(:code AS text), CAST(:now AS text))"
        " WHERE id = :id AND (last_alerts->>CAST(:code AS text) IS NULL"
        " OR CAST(last_alerts->>CAST(:code AS text) AS timestamptz) < :cutoff)"
    )
    r = await s.execute(q, {"code": code, "now": now.isoformat(), "id": account_id, "cutoff": now - ACCOUNT_ALERT_EVERY})
    return r.rowcount == 1


async def _claim_alert(s: AsyncSession, post: Post, code: str, status: str) -> bool:
    """Dedupe inside the state change's transaction: account codes per (account, code) per 6 h, terminal
    per-post codes once (alerted_at), reslot notices (MISSED) every time."""
    if code in KEY_CODES:  # the user's key, not this post: key_refused alerts once per flip
        return False
    if code in ACCOUNT_CODES:
        return await claim_account_alert(s, post.account_id, code)
    if status in RECOVERABLE:
        r = await s.execute(update(Post).where(Post.id == post.id, Post.alerted_at.is_(None)).values(alerted_at=_now()))
    else:
        return True
    return r.rowcount == 1


async def _finish(s: AsyncSession, post: Post, from_statuses, status: str, code: str | None = None,
                  detail: dict | None = None, guard=None, **values) -> bool:  # fmt: skip
    """CAS the post to status (+ error), claim its alert, commit, then send the alert. ACCOUNT_DISCONNECTED
    also marks the account disconnected: its other due posts fail before uploading, and the next sync that
    lists it connected again moves them all to free slots (accounts.upsert)."""
    ok = await _set(s, post.id, from_statuses, guard, status=status, error_code=code, error_detail=detail, **values)
    if ok and code == "ACCOUNT_DISCONNECTED":
        await s.execute(update(Account).where(Account.id == post.account_id).values(connection_status="disconnected"))
    claimed = ok and code is not None and await _claim_alert(s, post, code, status)
    await s.commit()
    if ok and code in KEY_CODES:
        await key_refused(s, post.user_id, refusal(code, detail))
    if claimed:
        account = await s.get(Account, post.account_id)
        cause = describe(code)[0]
        state = "moved to a new slot" if status == "SCHEDULED" else status.lower().replace("_", " ")
        await alert(
            post.user_id,
            f"<b>@{html.escape(account.username)}</b> post {post.id} {state}: {html.escape(cause)}",
            f"{settings.APP_BASE_URL}/recover/{post.id}",
            [[("Open post", f"p:{post.id}")]],  # the bot's post card, with the remedy
        )
    return ok


async def _reslot(s: AsyncSession, post: Post, code: str | None, from_statuses, **values) -> bool:
    """Back to SCHEDULED at the account's next free slot, error_code kept for display (None clears it);
    FAILED NO_FREE_SLOT when nothing is free within 30 days. The CAS also requires the scheduled_for we
    read, so an operator's move in between wins. Key and media URL stay: first_post_at keeps the 20 h
    guard honest, and a never-POSTed URL (video and cover) is re-uploaded only if it was dropped here (Zernio
    deletes temp media after 7 days)."""
    account = await slots.lock_account(s, post.account_id)
    slot = await slots.next_free_slot(s, account, _now() + slots.AUTO_LEAD, exclude_post_id=post.id)
    guard = Post.scheduled_for == post.scheduled_for
    if slot is None:
        return await _finish(s, post, from_statuses, "FAILED", "NO_FREE_SLOT", guard=guard, **values)
    if post.first_post_at is None and not (values.get("zernio_post_id") or post.zernio_post_id):
        values |= {"zernio_media_url": None, "zernio_cover_url": None}
    return await _finish(s, post, from_statuses, "SCHEDULED", code, guard=guard, scheduled_for=slot, **values)


async def mark_published(s: AsyncSession, post: Post, zpost: dict, from_statuses) -> bool:
    """CAS to PUBLISHED with Zernio's ids and permalink, and stamp the account. Caller commits."""
    p = publisher.instagram(zpost)
    ok = await _set(
        s, post.id, from_statuses, status="PUBLISHED", zernio_post_id=zpost.get("_id") or post.zernio_post_id,
        ig_media_id=p.get("platformPostId"), permalink=p.get("platformPostUrl"), published_at=_now(),
        error_code=None, error_detail=None,
    )  # fmt: skip
    if ok:
        await s.execute(update(Account).where(Account.id == post.account_id).values(last_publish_at=func.now()))
    return ok


async def reslot_disconnected(s: AsyncSession, account_id: int) -> None:
    """After a reconnect: the account's ACCOUNT_DISCONNECTED failures move to its next free slots."""
    q = select(Post.id).where(
        Post.account_id == account_id, Post.status.in_(RECOVERABLE), Post.error_code == "ACCOUNT_DISCONNECTED"
    )
    for pid in (await s.scalars(q.order_by(Post.scheduled_for))).all():
        try:
            post = await s.get(Post, pid, populate_existing=True)
            await _reslot(s, post, None, RECOVERABLE, alerted_at=None, attempt_count=0)
        except IntegrityError:  # a live post holds its idempotency key: leave this one FAILED
            await s.rollback()
            logger.warning("post %s: idempotency key in use by a live post, not re-slotted", pid)


async def defer_publish(post_id: int, in_s: int = 0) -> None:
    """queueing_lock: at most one queued job per post; lock: never two running at once."""
    job = publish_post.configure(queueing_lock=f"post:{post_id}", lock=f"post:{post_id}", schedule_in={"seconds": in_s})
    try:
        await job.defer_async(post_id=post_id)
    except AlreadyEnqueued:  # a queued job will do it
        pass


async def _pause(point: str) -> None:
    """PUBLISH_DEBUG_PAUSE: a 60 s window to kill the worker in (docs/PLAN.md section 4)."""
    if settings.PUBLISH_DEBUG_PAUSE == point:
        logger.warning("PUBLISH_DEBUG_PAUSE=%s: sleeping 60 s", point)
        await asyncio.sleep(60)


async def _resolve(s: AsyncSession, post: Post, zpost: dict) -> None:
    """Apply a Zernio post (from POST, GET or retry) to a PUBLISHING post."""
    code = publisher.outcome(zpost)
    p = publisher.instagram(zpost)
    ids = {"zernio_post_id": zpost.get("_id") or post.zernio_post_id}
    if code == "PUBLISHED":
        await mark_published(s, post, zpost, ["PUBLISHING"])
        await s.commit()
    elif code == "PROCESSING":  # 207 scheduled: Zernio retries by itself; poll it
        await _set(s, post.id, ["PUBLISHING"], **ids)
        await s.commit()
        await defer_publish(post.id, POLL_S)
    elif code == "RATE_LIMITED":
        await _reslot(s, post, code, ["PUBLISHING"], detail=p, **ids)
    elif code == "NETWORK_ERROR":  # platform/system error: transient, the next run retries the Zernio post
        await _set(s, post.id, ["PUBLISHING"], error_detail=p, **ids)
        await s.commit()
        raise NetworkError(f"Zernio post failed: {p.get('errorCategory')}: {p.get('errorMessage')}")
    else:
        await _finish(s, post, ["PUBLISHING"], "FAILED", code, p, **ids)


async def _publish(s: AsyncSession, post_id: int) -> None:
    now = _now()
    post = await s.get(Post, post_id)
    if post is None or not (post.status == "PUBLISHING" or (post.status == "SCHEDULED" and post.scheduled_for <= now)):
        return
    fresh = post.status == "SCHEDULED"
    key = await _key(s, post.user_id, claim=fresh)
    if fresh:  # checks before the first attempt; a PUBLISHING re-run finishes what it started
        account = await s.get(Account, post.account_id)
        render = await s.get(Render, post.render_id)
        if account.disabled_at:
            return await _finish(s, post, ["SCHEDULED"], "CANCELLED")
        if account.connection_status != "connected":
            return await _finish(s, post, ["SCHEDULED"], "FAILED", "ACCOUNT_DISCONNECTED")
        if render.status == "FAILED":
            return await _finish(s, post, ["SCHEDULED"], "FAILED", "RENDER_FAILED")
        if render.status != "READY":
            return
        if (render.duration_s or 0) > settings.ZERNIO_MAX_REEL_SECONDS:
            return await _finish(s, post, ["SCHEDULED"], "FAILED", "TOO_LONG")
        if render.duration_s is not None and render.duration_s < settings.ZERNIO_MIN_REEL_SECONDS:
            return await _finish(s, post, ["SCHEDULED"], "FAILED", "TOO_SHORT")
        if now - post.scheduled_for > OVERDUE:
            return await _reslot(s, post, "MISSED", ["SCHEDULED"])
        if key is None:  # dispatch skips such users: the key went (or went invalid) since it looked
            return await _finish(s, post, ["SCHEDULED"], "FAILED", "ZERNIO_KEY_MISSING")
        quota = await zernio.publishing_limit(key[0], account.zernio_account_id)  # cached 5 min; None: unknown, go on
        if quota is not None and quota.used >= quota.total:
            return await _reslot(s, post, "RATE_LIMITED", ["SCHEDULED"])
        q = cas(Post, post.id, ["SCHEDULED"], status="PUBLISHING").where(Post.scheduled_for <= now)
        if (await s.execute(q)).rowcount != 1:  # cancelled or moved meanwhile
            return
        await s.commit()
        await s.refresh(post)  # a caption edit committed before the claim is what goes out
    elif key is None:  # the key can't change while PUBLISHING (KEY_IN_USE): SECRETS_KEY went or changed
        return await _finish(s, post, ["PUBLISHING"], "FAILED", "ZERNIO_KEY_MISSING")
    async with publisher.client(key[0]) as c:
        await _send(s, c, post, key[1], now, fresh)


async def _send(s: AsyncSession, c, post: Post, gen: int, now: datetime, fresh: bool) -> None:
    """The Zernio half of a claimed (PUBLISHING) post, under key generation gen."""
    if post.zernio_post_id:  # Zernio already has the post: never POST again, ask it
        zpost = await publisher.get_post(c, post.zernio_post_id)
        state = publisher.outcome(zpost)
        # re-slotted/remedied (fresh) or transient: retry the same Zernio post's failed platform
        if state not in ("PUBLISHED", "PROCESSING") and (fresh or state == "NETWORK_ERROR"):
            zpost = await publisher.retry_post(c, post.zernio_post_id)
        return await _resolve(s, post, zpost)

    if post.first_post_at and now - post.first_post_at > KEY_WINDOW:  # a replay may no longer match the first POST
        return await _finish(s, post, ["PUBLISHING"], "DEAD_LETTER", "WINDOW_EXPIRED")
    if post.first_post_at and post.key_gen != gen:  # Zernio replays a key per credential: under another, a second Reel
        return await _finish(s, post, ["PUBLISHING"], "DEAD_LETTER", "KEY_CHANGED")
    if not post.zernio_media_url:
        render = await s.get(Render, post.render_id)
        url = await publisher.upload(c, settings.DATA_DIR / render.output_key)
        await _pause("after_upload")
        if not await _set(s, post.id, ["PUBLISHING"], zernio_media_url=url):
            return
        await s.commit()
        post.zernio_media_url = url
    if post.first_post_at is None and not post.zernio_cover_url:  # the cover goes out with the first POST or never
        render = await s.get(Render, post.render_id, populate_existing=True)  # expire_on_commit=False: re-read
        if render.cover_key:
            url = await publisher.upload(c, settings.DATA_DIR / render.cover_key, "image/jpeg")
            if not await _set(s, post.id, ["PUBLISHING"], zernio_cover_url=url):
                return
            await s.commit()
            post.zernio_cover_url = url
    await _pause("before_post")
    if post.first_post_at is None:  # committed before the POST: from here on the Reel may be live, under this key
        if not await _set(s, post.id, ["PUBLISHING"], first_post_at=now, key_gen=gen):
            return
        await s.commit()
        post.first_post_at, post.key_gen = now, gen
    account = await s.get(Account, post.account_id)
    render = await s.get(Render, post.render_id)
    zpost = await publisher.create_post(
        c, post.idempotency_key, post.caption, post.zernio_media_url, account.zernio_account_id, post.zernio_cover_url,
        post.music, (render.music or {}).get("name"),
    )
    await _pause("after_post")
    await _resolve(s, post, zpost)


@app.task(
    name="publish_post",
    pass_context=True,
    retry=RetryStrategy(max_attempts=MAX_RETRIES, wait=30, retry_exceptions={NetworkError}),
    priority=PRIORITY,
)
async def publish_post(context, post_id: int) -> None:
    """Async, never blocks the loop: file reads go to a thread (publisher.upload). Deferred with
    queueing_lock and lock 'post:{id}' (defer_publish)."""
    if not settings.PUBLISHING_ENABLED:
        logger.warning("publish_post %s: PUBLISHING_ENABLED is false, not publishing", post_id)
        return
    async with SessionLocal() as s:
        try:
            await _publish(s, post_id)
        except NetworkError as e:
            await s.rollback()
            if context.job.attempts < MAX_RETRIES:
                logger.warning("publish_post %s: %s, retrying with the same key", post_id, e)
                raise
            post = await s.get(Post, post_id)
            await _finish(s, post, ["PUBLISHING"], "DEAD_LETTER", "NETWORK_ERROR", {"error": str(e)})
        except Later as e:
            await s.rollback()
            logger.info("publish_post %s: Zernio says later (%s s)", post_id, e.seconds)
            await defer_publish(post_id, e.seconds)
        except Rejected as e:
            await s.rollback()
            await _finish(s, await s.get(Post, post_id), ["PUBLISHING"], "FAILED", e.code, e.body)
        except Exception as e:  # a bug, a missing render file, an odd body: say so, not WORKER_CRASHED
            logger.exception("publish_post %s", post_id)
            await s.rollback()
            detail = {"error": f"{type(e).__name__}: {e}"}
            await _finish(s, await s.get(Post, post_id), ["PUBLISHING"], "FAILED", "UNKNOWN", detail)


NO_LIVE_JOB = text(
    "NOT EXISTS (SELECT 1 FROM procrastinate_jobs j"
    " WHERE j.queueing_lock = 'post:' || posts.id AND j.status IN ('todo', 'doing'))"
)


async def _dispatch_one(s: AsyncSession, post: Post, render_status: str, now: datetime) -> None:
    if render_status in ("PENDING", "RENDERING") or post.status not in ("SCHEDULED", "PUBLISHING"):
        return
    if post.status == "SCHEDULED":
        if render_status == "FAILED":
            await _finish(s, post, ["SCHEDULED"], "FAILED", "RENDER_FAILED")
        elif now - post.scheduled_for > OVERDUE:
            await _reslot(s, post, "MISSED", ["SCHEDULED"])
        else:
            await defer_publish(post.id)
        return
    # PUBLISHING with no queued or running job: the worker was killed, or the stalled sweeper gave up
    n = post.attempt_count + 1
    if n > MAX_ORPHAN_REDEFERS:
        await _finish(s, post, ["PUBLISHING"], "DEAD_LETTER", "WORKER_CRASHED")
    elif await _set(s, post.id, ["PUBLISHING"], attempt_count=n):
        await s.commit()
        logger.warning("post %s: PUBLISHING with no job, re-deferring (orphan %s)", post.id, n)
        await defer_publish(post.id)


@app.periodic(cron="* * * * *", periodic_id="dispatch")
@app.task(name="dispatch", queueing_lock="dispatch", lock="dispatch", priority=PRIORITY)
async def dispatch(timestamp: int) -> None:
    """Every minute: defer due posts, then re-slot missed ones and recover orphans; abandon stale uploads. A user's
    posts only while their key is valid (a PUBLISHING post always: its job ends it). Alerts go out together at the
    end: however many users' posts moved, a slow Telegram never makes a due post late."""
    now, held = _now(), []
    token = _held.set(held)
    try:
        async with SessionLocal() as s:
            await s.execute(
                update(SourceClip)
                .where(SourceClip.status == "UPLOADING", SourceClip.created_at < now - UPLOAD_ABANDONED_AFTER)
                .values(status="FAILED", error_code="UPLOAD_ABANDONED")
            )
            await s.commit()
            if not settings.PUBLISHING_ENABLED:
                return
            keyed = and_(User.zernio_key_status == "valid", User.disabled_at.is_(None))
            due = or_(and_(Post.status == "SCHEDULED", Post.scheduled_for <= now, keyed), Post.status == "PUBLISHING")
            q = (
                select(Post.id, Post.status, Post.scheduled_for, Render.status)
                .join(Render, Render.id == Post.render_id)
                .join(User, User.id == Post.user_id)
                .where(due, NO_LIVE_JOB)
            )
            rest = []
            # ids, not rows: a rollback expires loaded rows, and touching one after it raises (MissingGreenlet)
            for pid, status, at, render_status in (await s.execute(q.order_by(Post.scheduled_for))).all():
                if status == "SCHEDULED" and render_status == "READY" and now - at <= OVERDUE:
                    try:  # first: each reslot below locks an account and searches its slots
                        await defer_publish(pid)
                    except Exception:  # one bad post must not block the rest
                        logger.exception("dispatch: post %s", pid)
                else:
                    rest.append((pid, render_status))
            for pid, render_status in rest:
                try:
                    post = await s.get(Post, pid, populate_existing=True)
                    await _dispatch_one(s, post, render_status, now)
                except Exception:
                    logger.exception("dispatch: post %s", pid)
                    await s.rollback()
    finally:
        _held.reset(token)
        await asyncio.gather(*(notify(*a) for a in held))
