"""Recovery (Phase 5): apply a failed post's remedy. docs/PLAN.md section 4."""

import secrets
import shutil
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.auth import current_user
from app.api.pipeline import Db, _defer
from app.api.scheduling import _err, idempotency_key, load_post_out
from app.core.config import settings
from app.models import Account, Post, Render, cas
from app.schemas import PostOut, RemedyIn
from app.services import publisher, slots, storage, zernio
from app.services.errors import RETRY, describe
from app.tasks import accounts as account_sync
from app.tasks import publish
from app.tasks.media import render

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])

CHECK_TIMEOUT_S = 15  # the Recover button waits on this GET: never the publisher's 300 s


async def _cas(s: AsyncSession, post: Post, **values) -> None:
    if (await s.execute(cas(Post, post.id, publish.RECOVERABLE, **values))).rowcount != 1:
        raise _err(409, "STATE_CONFLICT", f"post {post.id} is no longer FAILED or DEAD_LETTER")


async def _zernio_state(s: AsyncSession, post: Post) -> str | None:
    """GET the post's Zernio post: its outcome (publisher.outcome), after marking the post PUBLISHED when it
    went out after all. None when there is no Zernio post or it can't be read."""
    if not post.zernio_post_id:
        return None
    try:
        async with publisher.client(timeout=CHECK_TIMEOUT_S) as c:
            zpost = await publisher.get_post(c, post.zernio_post_id)
    except (zernio.ZernioError, publisher.NetworkError, publisher.Later, publisher.Rejected):
        return None
    state = publisher.outcome(zpost)
    if state == "PUBLISHED" and not await publish.mark_published(s, post, zpost, publish.RECOVERABLE):
        raise _err(409, "STATE_CONFLICT", f"post {post.id} is no longer FAILED or DEAD_LETTER")
    return state


async def _retry(s: AsyncSession, post: Post) -> None:
    fresh = {"error_code": None, "error_detail": None, "alerted_at": None, "attempt_count": 0}
    if await _zernio_state(s, post) == "PUBLISHED":
        return
    if post.zernio_post_id:
        # a failed Zernio post: publish_post retries it (POST /v1/posts/{id}/retry), never a new post
        return await _cas(s, post, status="SCHEDULED", scheduled_for=datetime.now(UTC), **fresh)
    if post.first_post_at is not None:
        # a POST may have gone out: same key and media URL, and publish_post's 20 h guard still counts
        # from first_post_at. Straight to PUBLISHING (no reslot) so the replay happens now.
        if not (settings.PUBLISHING_ENABLED and settings.ZERNIO_API_KEY):
            raise _err(409, "PUBLISHING_DISABLED", "publishing is off, so the retry would wait in PUBLISHING")
        await _cas(s, post, status="PUBLISHING", **fresh)
        raw = await (await s.connection()).get_raw_connection()
        job = publish.publish_post.configure(
            connection=raw.driver_connection, queueing_lock=f"post:{post.id}", lock=f"post:{post.id}"
        )
        try:
            async with s.begin_nested():
                await job.defer_async(post_id=post.id)
        except AlreadyEnqueued:
            pass
        return
    # nothing was ever POSTed with this key: a clean new attempt now (the old upload may be past Zernio's 7 days)
    await _cas(s, post, status="SCHEDULED", scheduled_for=datetime.now(UTC), zernio_media_url=None,
               zernio_cover_url=None, **fresh)  # fmt: skip


async def _rerender(s: AsyncSession, post: Post) -> None:
    """Same clip, brand, overlay, crop, caption and cover into a new render; the post points at it with a new
    idempotency key at the next free slot (it waits there for the render). A new key means no replay
    protection, so it is refused while the old attempt may still be live."""
    state = await _zernio_state(s, post)
    if state == "PUBLISHED":
        return
    if post.zernio_post_id and state in (None, "PROCESSING"):
        raise _err(409, "MAYBE_PUBLISHED", "Zernio may still publish the earlier attempt; check again shortly")
    if publish.maybe_live(post) and post.error_code != "WINDOW_EXPIRED":  # WINDOW_EXPIRED: operator checked
        raise _err(409, "MAYBE_PUBLISHED", "the earlier attempt may be live: Retry now replays it with the same key")
    old = await s.get(Render, post.render_id)
    acc = await slots.lock_account(s, post.account_id)
    at = await slots.next_free_slot(s, acc, datetime.now(UTC) + slots.AUTO_LEAD, exclude_post_id=post.id)
    if at is None:
        raise _err(409, "NO_FREE_SLOT", f"@{acc.username} has no free slot in the next 30 days")
    new = Render(
        source_clip_id=old.source_clip_id, brand_id=old.brand_id, overlay_config=old.overlay_config,
        crop_config=old.crop_config, caption=old.caption,
    )  # fmt: skip
    s.add(new)
    old.superseded_at = datetime.now(UTC)  # with no post left it would be 'Ready to schedule' again: a duplicate Reel
    await s.flush()
    await _cas(
        s, post, status="SCHEDULED", render_id=new.id, scheduled_for=at,
        idempotency_key=idempotency_key(new.id, acc.id, at), zernio_media_url=None, zernio_cover_url=None,
        zernio_post_id=None, first_post_at=None, ig_media_id=None, permalink=None, published_at=None,
        error_code=None, error_detail=None, alerted_at=None, attempt_count=0,
    )  # fmt: skip
    if old.cover_key:  # a copy, not the same file: deleting either render never breaks the other
        new.cover_key = f"covers/{new.id}-{secrets.token_hex(4)}.jpg"
        await run_in_threadpool(shutil.copyfile, storage.path_for(old.cover_key), storage.path_for(new.cover_key))
    await _defer(s, render, render_id=new.id)


async def _reconnect(s: AsyncSession, post: Post) -> None:
    """Pull the account state from Zernio (read-only); if it is connected, its ACCOUNT_DISCONNECTED posts
    move to their next free slots (the sync itself does that on a disconnected -> connected change, and a
    post failing ACCOUNT_DISCONNECTED marks its account disconnected; this also catches posts a sync left
    behind). Still disconnected: the post stays as it is."""
    try:
        await account_sync.sync(s, s.info["uid"])
    except zernio.ZernioError as e:
        raise _err(502, "ZERNIO_ERROR", str(e)) from e
    acc = await s.get(Account, post.account_id, populate_existing=True)
    if acc.connection_status == "connected" and acc.disabled_at is None:
        await publish.reslot_disconnected(s, acc.id)


@router.post("/posts/{post_id}/remedy")
async def remedy_post(post_id: int, body: RemedyIn, s: Db) -> PostOut:
    """FAILED / DEAD_LETTER only (409 {"code": "STATE_CONFLICT"} otherwise). See docs/PLAN.md section 4."""
    post = await s.get(Post, post_id)
    if post is None:
        raise _err(404, "NOT_FOUND", f"post {post_id} not found")
    if post.status not in publish.RECOVERABLE:
        raise _err(409, "STATE_CONFLICT", f"post {post_id} is {post.status}, not FAILED or DEAD_LETTER")
    action = body.action or (describe(post.error_code)[1] or RETRY).action
    try:
        if action == "reconnect":
            await _reconnect(s, post)
        else:
            if (await s.get(Account, post.account_id)).disabled_at is not None:
                raise _err(409, "ACCOUNT_UNAVAILABLE", "the account is disabled in Clipper; enable it first")
            await (_rerender if action == "rerender" else _retry)(s, post)  # retry, or "auto" that failed anyway
        await s.commit()
    except IntegrityError:  # uq_posts_idempotency_key_live: a newer live post already holds this post's key
        await s.rollback()
        raise _err(409, "STATE_CONFLICT", f"another live post holds post {post_id}'s idempotency key") from None
    return await load_post_out(s, post_id)
