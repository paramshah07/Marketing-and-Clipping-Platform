"""publish_post, the dispatcher and the remedy endpoint against the test database. Zernio is an
httpx.MockTransport serving ONLY bodies from tests/fixtures/zernio/ (see its README). Crash windows are
simulated by raising from publish._pause, the PUBLISH_DEBUG_PAUSE hook, at the named point."""

import asyncio
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import engine
from app.main import app as api_app
from app.models import Account, Post, Render, SourceClip, User, cas
from app.services import publisher, slots, zernio
from app.services.errors import CATEGORY
from app.tasks import accounts as account_sync
from app.tasks import publish
from app.tasks.publish import dispatch, publish_post
from app.tasks.queue import app
from conftest import as_user, zernio_key

FIX = Path(__file__).parent / "fixtures" / "zernio"
PRESIGN = json.loads((FIX / "presign.json").read_text())["body"]
ZPOST = "65f1c0a9e2b5af0012ab34cd"  # the docs examples' post id
COVER = b"\xff\xd8\xff\xe0" + bytes(1000)  # the JPEG magic bytes are all the api checks
COVER_URL = PRESIGN["publicUrl"].removesuffix(".mp4") + ".jpg"  # cover_presign()'s publicUrl
SLOT = datetime(2031, 3, 3, 9, 0, tzinfo=UTC)  # the stubbed next free slot


def now() -> datetime:
    return datetime.now(UTC)


def fx(name: str, **platform) -> dict:
    """A fixture; platform=... overrides fields of its Instagram entry (e.g. another errorCategory)."""
    d = json.loads((FIX / f"{name}.json").read_text())
    for p in d["body"].get("post", {}).get("platforms", []):
        if p["platform"] == "instagram":
            p.update(platform)
    return d


class Zernio:
    """MockTransport handler: routes are 'METHOD /path' (or 'PUT upload') -> list of fixtures/exceptions,
    served in order. Records every request. The pre-publish quota read ('GET limit') serves the live
    publishing_limit fixture unless routed, and is recorded apart (limits)."""

    def __init__(self, routes: dict):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls: list[tuple[str, httpx.Request]] = []
        self.limits: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        key = "PUT upload" if req.method == "PUT" else f"{req.method} {req.url.path.removeprefix('/api/v1')}"
        if key.endswith("/instagram/publishing-limit"):
            self.limits.append(req)
            key = "GET limit"
            self.routes.setdefault(key, ["publishing_limit"])
            if len(self.routes[key]) == 1:
                self.routes[key].append(self.routes[key][0])  # keep serving the last one
        else:
            self.calls.append((key, req))
        item = self.routes[key].pop(0)
        if isinstance(item, Exception):
            raise item
        if item is None:  # the bucket's empty 200
            return httpx.Response(200)
        d = fx(item) if isinstance(item, str) else item
        return httpx.Response(d["status"], json=d["body"], headers={k: v for k, v in d["headers"].items() if v})

    def sent(self, key: str) -> list[httpx.Request]:
        return [r for k, r in self.calls if k == key]


class Crash(BaseException):
    """Stands in for SIGKILL: nothing after the raise point runs (and no `except Exception` sees it)."""


@pytest.fixture(autouse=True)
def env(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHING_ENABLED", True)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    zernio_key(1, "sk_test")  # user 1 (the posts' owner) publishes with its own key, generation 1
    alerts: list[tuple[str, str]] = []
    uids: list[int] = []  # whom each alert went to

    async def record(uid, text_, link=None, buttons=None):
        alerts.append((text_, link))
        uids.append(uid)
        return True

    monkeypatch.setattr(publish, "notify", record)
    monkeypatch.setattr(account_sync, "notify", record)

    async def next_slot(s, account, after, exclude_post_id=None):
        return SLOT

    monkeypatch.setattr(slots, "next_free_slot", next_slot)
    zernio._quota_cache.clear()
    use(monkeypatch, Zernio({}))  # nothing ever reaches the real Zernio: an unrouted call fails the test
    yield SimpleNamespace(alerts=alerts, uids=uids, dir=tmp_path)
    zernio_key(1, None)


REAL_CLIENT = zernio.client


def use(monkeypatch, z: Zernio) -> Zernio:
    """The real Zernio clients (base URL, the user's key, timeouts) over a MockTransport."""
    monkeypatch.setattr(zernio, "client", lambda key, **kw: REAL_CLIENT(key, transport=httpx.MockTransport(z), **kw))
    return z


def crash_at(monkeypatch, point: str) -> None:
    async def pause(p):
        if p == point:
            raise Crash(p)

    monkeypatch.setattr(publish, "_pause", pause)


def make_post(
    db, env, status="SCHEDULED", at=None, render_status="READY", duration=12.0, account_id=None, user_id=1, **post
) -> int:
    with Session(db) as s:
        if account_id is None:
            acc = Account(user_id=user_id, zernio_account_id=uuid.uuid4().hex, zernio_profile_id="p", username="ig_acct",
                          timezone="Europe/London")  # fmt: skip
            s.add(acc)
            s.flush()
            account_id = acc.id
        clip = SourceClip(user_id=user_id, origin="upload", status="READY", original_filename="c.mp4")
        s.add(clip)
        s.flush()
        (env.dir / "renders").mkdir(exist_ok=True)
        key = f"renders/{uuid.uuid4().hex}.mp4"
        (env.dir / key).write_bytes(bytes(range(256)) * 10_000)  # 2.5 MB: several 1 MB chunks
        r = Render(user_id=user_id, source_clip_id=clip.id, status=render_status, output_key=key, duration_s=duration,
                   overlay_config={"x": 0.1}, crop_config={"x": 0.2}, filter="Juno", caption="render caption")  # fmt: skip
        s.add(r)
        s.flush()
        at = at or now() - timedelta(minutes=1)
        if post.get("first_post_at"):  # sent under user 1's current key unless said otherwise
            post.setdefault("key_gen", 1)
        p = Post(user_id=user_id, render_id=r.id, account_id=account_id, caption="caption #reel", scheduled_for=at,
                 status=status, idempotency_key=uuid.uuid4().hex, **post)  # fmt: skip
        s.add(p)
        s.commit()
        return p.id


def row(db, post_id: int) -> Post:
    with Session(db) as s:
        return s.get(Post, post_id)


def jobs(db, post_id: int) -> list[dict]:
    with db.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(
            "SELECT task_name, status::text, scheduled_at, priority FROM procrastinate_jobs WHERE queueing_lock = :l"
            " ORDER BY id"
        ), {"l": f"post:{post_id}"})]  # fmt: skip


def run(coro):
    async def main():
        try:
            async with app.open_async():
                return await coro
        finally:
            await engine.dispose()  # its connections belong to this event loop

    return asyncio.run(main())


def go(post_id: int, attempts: int = 0):
    """One publish_post run as the worker would make it (job.attempts = earlier failed runs)."""
    return run(publish_post(SimpleNamespace(job=SimpleNamespace(attempts=attempts)), post_id=post_id))


PRESIGN_, PUT_, POST_ = "POST /media/presign", "PUT upload", "POST /posts"
GET_, RETRY_ = f"GET /posts/{ZPOST}", f"POST /posts/{ZPOST}/retry"


def routes(*post: str | Exception) -> dict:
    return {PRESIGN_: ["presign"], PUT_: [None], POST_: list(post or ["docs_create_published"])}


# ---------------------------------------------------------------- publish_post


def test_publish_happy_path(db, env, monkeypatch):
    pid = make_post(db, env)
    z = use(monkeypatch, Zernio(routes()))
    go(pid)
    p = row(db, pid)
    assert (p.status, p.zernio_post_id, p.permalink) == ("PUBLISHED", ZPOST, "https://www.instagram.com/p/DGx7Yk2ScAb/")
    assert p.zernio_media_url == PRESIGN["publicUrl"] and p.published_at and p.error_code is None
    with Session(db) as s:
        assert s.get(Account, p.account_id).last_publish_at is not None
    [presign], [put], [post] = z.sent(PRESIGN_), z.sent(PUT_), z.sent(POST_)
    size = len(bytes(range(256)) * 10_000)
    assert json.loads(presign.content) == {"filename": presign_name(db, pid), "contentType": "video/mp4", "size": size}
    assert str(put.url) == PRESIGN["uploadUrl"] and put.content == bytes(range(256)) * 10_000
    assert "authorization" not in put.headers and put.headers["content-length"] == str(size)
    assert post.headers["Idempotency-Key"] == p.idempotency_key and post.headers["Authorization"] == "Bearer sk_test"
    with Session(db) as s:
        zid = s.get(Account, p.account_id).zernio_account_id
    assert json.loads(post.content) == {  # no cover: one presign, no instagramThumbnail
        "content": "caption #reel",
        "mediaItems": [{"type": "video", "url": PRESIGN["publicUrl"]}],
        "platforms": [{"platform": "instagram", "accountId": zid, "platformSpecificData": {"shareToFeed": True}}],
        "publishNow": True,
    }
    assert post.extensions["timeout"]["read"] == 300
    assert env.alerts == []


LIVE_ZPOST = "6ab81e960931bc4d7225a320"  # the live Phase 5 recording's post id


def test_publish_live_flow_201_publishing_then_polls_to_published(db, env, monkeypatch):
    """What a real Instagram video does (live_*.json): the create returns 201 while the post is still
    `publishing`; the job keeps the Zernio id, re-defers, and a later GET finds it published."""
    pid = make_post(db, env)
    z = use(monkeypatch, Zernio(routes("live_create_publishing") | {f"GET /posts/{LIVE_ZPOST}": ["live_get_published"]}))
    go(pid)
    p = row(db, pid)
    assert (p.status, p.zernio_post_id, p.permalink) == ("PUBLISHING", LIVE_ZPOST, None)
    assert [j["status"] for j in jobs(db, pid)] == ["todo"]  # polled again later, never re-POSTed
    go(pid)
    p = row(db, pid)
    assert (p.status, p.permalink) == ("PUBLISHED", "https://www.instagram.com/reel/DdwzLx_jozA/")
    assert len(z.sent(POST_)) == 1 and len(z.sent(f"GET /posts/{LIVE_ZPOST}")) == 1


def presign_name(db, post_id: int) -> str:
    with Session(db) as s:
        return Path(s.get(Render, s.get(Post, post_id).render_id).output_key).name


def test_crash_after_upload_before_url_commit(db, env, monkeypatch):
    pid = make_post(db, env)
    z = use(monkeypatch, Zernio(routes() | {PRESIGN_: ["presign", "presign"], PUT_: [None, None]}))
    crash_at(monkeypatch, "after_upload")
    with pytest.raises(Crash):
        go(pid)
    p = row(db, pid)
    assert (p.status, p.zernio_media_url, p.zernio_post_id) == ("PUBLISHING", None, None)
    assert z.sent(POST_) == []
    monkeypatch.setattr(publish, "_pause", lambda point: asyncio.sleep(0))
    go(pid)  # the orphaned first upload is Zernio temp media, deleted after 7 days
    assert row(db, pid).status == "PUBLISHED"
    assert (len(z.sent(PRESIGN_)), len(z.sent(POST_))) == (2, 1)


def test_crash_after_url_commit_before_post(db, env, monkeypatch):
    pid = make_post(db, env)
    z = use(monkeypatch, Zernio(routes()))
    crash_at(monkeypatch, "before_post")
    with pytest.raises(Crash):
        go(pid)
    p = row(db, pid)
    assert (p.status, p.zernio_media_url, p.zernio_post_id) == ("PUBLISHING", PRESIGN["publicUrl"], None)
    monkeypatch.setattr(publish, "_pause", lambda point: asyncio.sleep(0))
    go(pid)
    assert row(db, pid).status == "PUBLISHED"
    assert len(z.sent(PRESIGN_)) == 1  # the committed URL is reused, not uploaded again
    assert json.loads(z.sent(POST_)[0].content)["mediaItems"][0]["url"] == PRESIGN["publicUrl"]


def test_crash_after_post_success_before_commit_replays_same_key(db, env, monkeypatch):
    pid = make_post(db, env)
    z = use(monkeypatch, Zernio(routes("docs_create_published", "docs_replay_published")))
    crash_at(monkeypatch, "after_post")
    with pytest.raises(Crash):
        go(pid)  # Zernio published it (201), we never stored that
    p = row(db, pid)
    assert (p.status, p.zernio_post_id) == ("PUBLISHING", None)
    monkeypatch.setattr(publish, "_pause", lambda point: asyncio.sleep(0))
    go(pid)
    p = row(db, pid)
    assert (p.status, p.zernio_post_id) == ("PUBLISHED", ZPOST)
    first, second = z.sent(POST_)
    assert first.headers["Idempotency-Key"] == second.headers["Idempotency-Key"] == p.idempotency_key
    assert first.content == second.content  # same media URL too
    assert z.routes[POST_] == []  # served: the 201, then the 200 idempotent replay (no second post)


@pytest.mark.parametrize("fixture, seconds", [("docs_409_idempotency_conflict", 5), ("docs_429", 12)])
def test_still_processing_or_rate_limited_redefers_after_retry_after(db, env, monkeypatch, fixture, seconds):
    pid = make_post(db, env)
    z = use(monkeypatch, Zernio(routes(fixture, "docs_replay_published")))
    t0 = now()
    go(pid)
    p = row(db, pid)
    assert (p.status, p.zernio_media_url, p.error_code) == ("PUBLISHING", PRESIGN["publicUrl"], None)
    [job] = jobs(db, pid)
    assert job["status"] == "todo" and timedelta(seconds=seconds - 1) < job["scheduled_at"] - t0 < timedelta(
        seconds=seconds + 5
    )
    go(pid)  # the re-deferred run: same key, same URL, Zernio replays the original
    assert row(db, pid).status == "PUBLISHED"
    first, second = z.sent(POST_)
    assert first.headers["Idempotency-Key"] == second.headers["Idempotency-Key"] and first.content == second.content


def test_duplicate_content_409_resolves_to_existing_post(db, env, monkeypatch):
    pid = make_post(db, env)
    z = use(monkeypatch, Zernio(routes("docs_409_duplicate") | {GET_: ["docs_create_published"]}))
    go(pid)
    p = row(db, pid)
    assert (p.status, p.zernio_post_id) == ("PUBLISHED", ZPOST) and len(z.sent(GET_)) == 1


def test_403_account_disconnected(db, env, monkeypatch):
    pid = make_post(db, env)
    use(monkeypatch, Zernio(routes("docs_403_disconnected")))
    go(pid)
    p = row(db, pid)
    assert (p.status, p.error_code, p.error_detail["code"]) == (
        "FAILED",
        "ACCOUNT_DISCONNECTED",
        "ACCOUNT_DISCONNECTED",
    )
    assert len(env.alerts) == 1 and env.alerts[0][1].endswith(f"/recover/{pid}")


@pytest.mark.parametrize("category", sorted(CATEGORY))
def test_207_failed_per_category(db, env, monkeypatch, category):
    pid = make_post(db, env)
    use(monkeypatch, Zernio(routes(fx("docs_207_failed", errorCategory=category))))
    code = publisher.classify(category)
    if code == "NETWORK_ERROR":  # transient: the job retries, Zernio's post id is kept for the retry
        with pytest.raises(publisher.NetworkError):
            go(pid)
        p = row(db, pid)
        assert (p.status, p.zernio_post_id, p.error_detail["errorCategory"]) == ("PUBLISHING", ZPOST, category)
        return
    go(pid)
    p = row(db, pid)
    assert p.zernio_post_id == ZPOST and p.error_code == code and p.error_detail["errorCategory"] == category
    if code == "RATE_LIMITED":  # next free slot, error kept for display, one alert
        assert (p.status, p.scheduled_for) == ("SCHEDULED", SLOT)
    else:
        assert p.status == "FAILED"
    with Session(db) as s:  # account-level codes dedupe per (account, code), the rest once per post
        assert (
            (code in s.get(Account, p.account_id).last_alerts) == (code in publish.ACCOUNT_CODES) != bool(p.alerted_at)
        )
    assert len(env.alerts) == 1


def test_207_partial_uses_the_instagram_entry(db, env, monkeypatch):
    pid = make_post(db, env)
    use(monkeypatch, Zernio(routes("docs_207_partial")))
    go(pid)
    assert (row(db, pid).status, row(db, pid).error_code) == ("FAILED", "CONTENT_REJECTED")


def test_207_scheduled_polls_until_published(db, env, monkeypatch):
    pid = make_post(db, env)
    z = use(monkeypatch, Zernio(routes("docs_207_scheduled") | {GET_: ["docs_207_scheduled", "docs_create_published"]}))
    go(pid)
    p = row(db, pid)
    assert (p.status, p.zernio_post_id, p.error_code) == ("PUBLISHING", ZPOST, None)
    assert [j["status"] for j in jobs(db, pid)] == ["todo"]
    go(pid)  # still in Zernio's hands: poll again
    go(pid)
    assert row(db, pid).status == "PUBLISHED"
    assert (len(z.sent(POST_)), len(z.sent(GET_)), z.sent(RETRY_)) == (1, 2, [])


def test_207_platform_error_retries_the_zernio_post(db, env, monkeypatch):
    pid = make_post(db, env)
    failed = fx("docs_207_failed", errorCategory="platform_error")
    z = use(monkeypatch, Zernio(routes(failed) | {GET_: [failed], RETRY_: ["docs_create_published"]}))
    with pytest.raises(publisher.NetworkError):
        go(pid)
    go(pid, attempts=1)  # RetryStrategy's second run: GET, then Zernio's retry of the same post
    assert row(db, pid).status == "PUBLISHED"
    assert (len(z.sent(POST_)), len(z.sent(RETRY_))) == (1, 1)


def test_network_errors_three_retries_then_dead_letter(db, env, monkeypatch):
    pid = make_post(db, env)
    z = use(monkeypatch, Zernio(routes(*[httpx.ReadTimeout("read timed out")] * 4)))
    for attempt in range(publish.MAX_RETRIES):
        with pytest.raises(publisher.NetworkError):
            go(pid, attempts=attempt)
        assert row(db, pid).status == "PUBLISHING"
    go(pid, attempts=publish.MAX_RETRIES)  # the last run
    p = row(db, pid)
    assert (p.status, p.error_code) == ("DEAD_LETTER", "NETWORK_ERROR") and "ReadTimeout" in p.error_detail["error"]
    posts = z.sent(POST_)
    assert (
        len(posts) == 4
        and len({r.headers["Idempotency-Key"] for r in posts}) == 1
        and len({r.content for r in posts}) == 1
    )
    assert len(z.sent(PRESIGN_)) == 1 and len(env.alerts) == 1


def test_5xx_is_a_network_error(db, env, monkeypatch):
    pid = make_post(db, env)
    use(monkeypatch, Zernio(routes("docs_5xx")))
    with pytest.raises(publisher.NetworkError, match="HTTP 502"):
        go(pid)


def test_never_posts_past_20h(db, env, monkeypatch):
    # scheduled_for is recent (a reslot moved it): the clock is the first POST, 21 h ago
    pid = make_post(db, env, status="PUBLISHING", zernio_media_url=PRESIGN["publicUrl"],
                    first_post_at=now() - timedelta(hours=21))  # fmt: skip
    z = use(monkeypatch, Zernio({}))
    go(pid)
    p = row(db, pid)
    assert (p.status, p.error_code, z.calls) == ("DEAD_LETTER", "WINDOW_EXPIRED", [])


def test_guards_before_first_attempt(db, env, monkeypatch):
    z = use(monkeypatch, Zernio({}))
    too_long = make_post(db, env, duration=settings.ZERNIO_MAX_REEL_SECONDS + 0.5)
    too_short = make_post(db, env, duration=2.9)
    render_failed = make_post(db, env, render_status="FAILED")
    rendering = make_post(db, env, render_status="RENDERING")
    not_due = make_post(db, env, at=now() + timedelta(hours=1))
    missed = make_post(db, env, at=now() - timedelta(hours=2))
    for pid in (too_long, too_short, render_failed, rendering, not_due, missed):
        go(pid)
    assert [(row(db, p).status, row(db, p).error_code) for p in (too_long, too_short, render_failed, rendering, not_due)] == [
        ("FAILED", "TOO_LONG"), ("FAILED", "TOO_SHORT"), ("FAILED", "RENDER_FAILED"), ("SCHEDULED", None), ("SCHEDULED", None)
    ]  # fmt: skip
    assert (row(db, missed).status, row(db, missed).scheduled_for, row(db, missed).error_code) == (
        "SCHEDULED",
        SLOT,
        "MISSED",
    )
    assert z.calls == []


def test_publishing_disabled_does_nothing(db, env, monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHING_ENABLED", False)
    pid = make_post(db, env)
    z = use(monkeypatch, Zernio({}))
    go(pid)
    run(dispatch(timestamp=0))
    assert (row(db, pid).status, z.calls, jobs(db, pid)) == ("SCHEDULED", [], [])


def test_account_alerts_dedupe_per_account_and_code(db, env, monkeypatch):
    first = make_post(db, env)
    second = make_post(db, env, account_id=row(db, first).account_id)
    use(monkeypatch, Zernio(routes("docs_403_disconnected") | {PRESIGN_: ["presign"] * 2, PUT_: [None] * 2,
                                                                POST_: ["docs_403_disconnected"] * 2}))  # fmt: skip
    go(first)
    go(second)
    assert [row(db, p).error_code for p in (first, second)] == ["ACCOUNT_DISCONNECTED"] * 2
    assert len(env.alerts) == 1


# ---------------------------------------------------------------- dispatcher


def test_dispatcher(db, env, monkeypatch):
    with Session(db) as s:  # other tests' leftovers must not be dispatched here
        s.execute(update(Post).where(Post.status.in_(["SCHEDULED", "PUBLISHING"])).values(status="CANCELLED"))
        stale = SourceClip(origin="upload", status="UPLOADING", created_at=now() - timedelta(hours=25))
        recent = SourceClip(origin="upload", status="UPLOADING", created_at=now() - timedelta(hours=1))
        s.add_all([stale, recent])
        s.commit()
        stale_id, recent_id = stale.id, recent.id
    due = make_post(db, env)
    rendering = make_post(db, env, render_status="RENDERING")
    render_failed = make_post(db, env, render_status="FAILED")
    overdue = make_post(db, env, at=now() - timedelta(minutes=45))
    future = make_post(db, env, at=now() + timedelta(hours=2))
    orphan = make_post(db, env, status="PUBLISHING")
    worn_out = make_post(db, env, status="PUBLISHING", attempt_count=3)
    busy = make_post(db, env, status="PUBLISHING")
    run(publish.defer_publish(busy))  # a live job: not an orphan

    run(dispatch(timestamp=0))

    state = {name: (row(db, p).status, row(db, p).error_code, len(jobs(db, p))) for name, p in [
        ("due", due), ("rendering", rendering), ("render_failed", render_failed), ("overdue", overdue),
        ("future", future), ("orphan", orphan), ("worn_out", worn_out), ("busy", busy)]}  # fmt: skip
    assert state == {
        "due": ("SCHEDULED", None, 1),  # deferred; publish_post does the CAS
        "rendering": ("SCHEDULED", None, 0),
        "render_failed": ("FAILED", "RENDER_FAILED", 0),
        "overdue": ("SCHEDULED", "MISSED", 0),
        "future": ("SCHEDULED", None, 0),
        "orphan": ("PUBLISHING", None, 1),
        "worn_out": ("DEAD_LETTER", "WORKER_CRASHED", 0),
        "busy": ("PUBLISHING", None, 1),
    }
    assert row(db, overdue).scheduled_for == SLOT and row(db, orphan).attempt_count == 1
    assert (row(db, busy).attempt_count, row(db, worn_out).attempt_count) == (0, 3)
    [job] = jobs(db, due)
    assert (job["task_name"], job["priority"]) == ("publish_post", publish.PRIORITY)  # ahead of renders
    with Session(db) as s:
        assert (s.get(SourceClip, stale_id).status, s.get(SourceClip, stale_id).error_code) == (
            "FAILED",
            "UPLOAD_ABANDONED",
        )
        assert s.get(SourceClip, recent_id).status == "UPLOADING"
    assert len(env.alerts) == 3  # render failed, missed, worker crashed

    run(dispatch(timestamp=0))  # next minute: queued jobs are live, nothing is deferred twice
    assert (len(jobs(db, due)), len(jobs(db, orphan)), row(db, orphan).attempt_count) == (1, 1, 1)


# ---------------------------------------------------------------- races


def test_cancel_while_publishing_is_refused(db, env, monkeypatch):
    pid = make_post(db, env)
    use(monkeypatch, Zernio(routes()))
    crash_at(monkeypatch, "before_post")
    with pytest.raises(Crash):
        go(pid)
    with TestClient(api_app, headers=as_user()) as c:
        r = c.post(f"/api/posts/{pid}/cancel")
        c.portal.call(engine.dispose)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "STATE_CONFLICT"
    assert row(db, pid).status == "PUBLISHING"


def test_cancel_before_the_worker_wins(db, env, monkeypatch):
    pid = make_post(db, env)
    z = use(monkeypatch, Zernio({}))
    with Session(db) as s:
        assert (
            s.execute(cas(Post, pid, ["DRAFT", "SCHEDULED", "FAILED", "DEAD_LETTER"], status="CANCELLED")).rowcount == 1
        )
        s.commit()
    go(pid)
    assert (row(db, pid).status, z.calls) == ("CANCELLED", [])


# ---------------------------------------------------------------- remedy


def remedy(post_id: int, action: str | None = None) -> httpx.Response:
    with TestClient(api_app, headers=as_user()) as c:
        r = c.post(f"/api/posts/{post_id}/remedy", json={"action": action})
        c.portal.call(engine.dispose)
    return r


def test_remedy_only_for_failed_posts(db, env):
    for status in ("SCHEDULED", "PUBLISHING", "PUBLISHED", "CANCELLED"):
        r = remedy(make_post(db, env, status=status))
        assert (r.status_code, r.json()["detail"]["code"]) == (409, "STATE_CONFLICT")
    assert remedy(10**9).status_code == 404


def test_remedy_retry_finds_it_published(db, env, monkeypatch):
    pid = make_post(db, env, status="DEAD_LETTER", error_code="NETWORK_ERROR", zernio_post_id=ZPOST)
    z = use(monkeypatch, Zernio({GET_: ["docs_create_published"]}))
    r = remedy(pid)
    assert r.status_code == 200 and (r.json()["status"], r.json()["permalink"]) == (
        "PUBLISHED",
        "https://www.instagram.com/p/DGx7Yk2ScAb/",
    )
    assert [k for k, _ in z.calls] == [GET_]
    assert z.calls[0][1].extensions["timeout"]["read"] == 15  # a quick check, not the publisher's 300 s


def test_remedy_retry_of_a_failed_zernio_post_uses_zernio_retry(db, env, monkeypatch):
    failed = fx("docs_207_failed", errorCategory="platform_error")
    pid = make_post(db, env, status="DEAD_LETTER", error_code="NETWORK_ERROR", zernio_post_id=ZPOST, attempt_count=2,
                    zernio_media_url=PRESIGN["publicUrl"], alerted_at=now())  # fmt: skip
    z = use(monkeypatch, Zernio({GET_: [failed, failed], RETRY_: ["docs_create_published"]}))
    r = remedy(pid)
    p = row(db, pid)
    assert (r.json()["status"], r.json()["cause"], p.zernio_post_id, p.attempt_count, p.alerted_at) == (
        "SCHEDULED",
        None,
        ZPOST,
        0,
        None,
    )
    assert abs(p.scheduled_for - now()) < timedelta(minutes=1)
    go(pid)
    assert row(db, pid).status == "PUBLISHED"
    assert [k for k, _ in z.calls] == [GET_, GET_, RETRY_]  # never a new POST /v1/posts


def test_remedy_retry_after_ambiguous_failure_keeps_key_url_and_time(db, env, monkeypatch):
    at = now() - timedelta(hours=2)
    pid = make_post(db, env, status="DEAD_LETTER", at=at, error_code="NETWORK_ERROR",
                    zernio_media_url=PRESIGN["publicUrl"], first_post_at=at)  # fmt: skip
    key = row(db, pid).idempotency_key
    z = use(monkeypatch, Zernio({POST_: ["docs_replay_published"]}))
    r = remedy(pid)
    p = row(db, pid)
    assert (r.json()["status"], p.scheduled_for, p.zernio_media_url, p.idempotency_key) == (
        "PUBLISHING",
        at,
        PRESIGN["publicUrl"],
        key,
    )
    assert [j["status"] for j in jobs(db, pid)] == ["todo"]
    go(pid)
    [post] = z.sent(POST_)
    assert row(db, pid).status == "PUBLISHED" and post.headers["Idempotency-Key"] == key


def test_remedy_retry_after_ambiguous_failure_past_20h_never_posts(db, env, monkeypatch):
    pid = make_post(db, env, status="DEAD_LETTER", at=now() - timedelta(hours=21), error_code="WORKER_CRASHED",
                    zernio_media_url=PRESIGN["publicUrl"], first_post_at=now() - timedelta(hours=21))  # fmt: skip
    z = use(monkeypatch, Zernio({}))
    assert remedy(pid).json()["status"] == "PUBLISHING"
    go(pid)
    assert (row(db, pid).status, row(db, pid).error_code, z.calls) == ("DEAD_LETTER", "WINDOW_EXPIRED", [])


def test_remedy_retry_when_nothing_reached_zernio(db, env, monkeypatch):
    pid = make_post(db, env, status="FAILED", at=now() - timedelta(days=9), error_code="UNKNOWN",
                    error_detail={"error": "bad"}, zernio_media_url=PRESIGN["publicUrl"],
                    zernio_cover_url=COVER_URL)  # fmt: skip
    key = row(db, pid).idempotency_key
    r = remedy(pid)
    p = row(db, pid)
    assert (r.json()["status"], p.zernio_media_url, p.zernio_cover_url, p.error_code, p.error_detail,
            p.idempotency_key) == ("SCHEDULED", None, None, None, None, key)  # fmt: skip
    assert abs(p.scheduled_for - now()) < timedelta(minutes=1)


def test_remedy_rerender(db, env, monkeypatch):
    from app.api.scheduling import idempotency_key

    pid = make_post(db, env, status="FAILED", error_code="CONTENT_REJECTED", zernio_post_id=ZPOST,
                    zernio_media_url=PRESIGN["publicUrl"], alerted_at=now(), first_post_at=now())  # fmt: skip
    old = row(db, pid)
    z = use(monkeypatch, Zernio({GET_: [fx("docs_207_failed", errorCategory="user_content")]}))
    r = remedy(pid)
    assert [k for k, _ in z.calls] == [GET_]  # the old Zernio post is checked first: failed, so re-key
    assert r.status_code == 200, r.text
    p = row(db, pid)
    with Session(db) as s:
        a, b = s.get(Render, old.render_id), s.get(Render, p.render_id)
        same = lambda x: (x.source_clip_id, x.brand_id, x.overlay_config, x.crop_config, x.filter, x.caption)  # noqa: E731
        assert b.id != a.id and same(b) == same(a) and b.status == "PENDING"
    assert (p.status, p.scheduled_for, p.idempotency_key) == (
        "SCHEDULED",
        SLOT,
        idempotency_key(p.render_id, p.account_id, SLOT),
    )
    assert (p.zernio_post_id, p.zernio_media_url, p.error_code, p.alerted_at, p.first_post_at) == (None,) * 5
    assert r.json()["render"]["id"] == p.render_id
    with db.connect() as c:
        q = text("SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'render' AND args @> CAST(:a AS jsonb)")
        assert c.execute(q, {"a": json.dumps({"render_id": p.render_id})}).scalar() == 1


@pytest.mark.parametrize("connected", [True, False])
def test_remedy_reconnect(db, env, monkeypatch, connected):
    pid = make_post(db, env, status="FAILED", error_code="ACCOUNT_DISCONNECTED")
    acc = row(db, pid).account_id
    sibling = make_post(db, env, status="DEAD_LETTER", error_code="ACCOUNT_DISCONNECTED", account_id=acc)
    other = make_post(db, env, status="FAILED", error_code="UNKNOWN", account_id=acc)
    with Session(db) as s:
        s.execute(update(Account).where(Account.id == acc).values(connection_status="disconnected"))
        s.commit()

    async def sync(s, uid, key):  # stands in for the Zernio account sync (GET /v1/accounts)
        assert (uid, key) == (1, "sk_test")
        await s.execute(
            update(Account)
            .where(Account.id == acc)
            .values(connection_status="connected" if connected else "disconnected")
        )
        await s.commit()

    monkeypatch.setattr(account_sync, "sync", sync)
    r = remedy(pid)
    assert r.status_code == 200
    expected = ("SCHEDULED", None) if connected else ("FAILED", "ACCOUNT_DISCONNECTED")
    assert (r.json()["status"], r.json()["error_code"]) == expected
    assert (row(db, sibling).status, row(db, sibling).scheduled_for == SLOT) == (
        ("SCHEDULED", True) if connected else ("DEAD_LETTER", False)
    )
    assert (row(db, other).status, row(db, other).error_code) == ("FAILED", "UNKNOWN")


# ---------------------------------------------------------------- review fixes


def test_reconnect_reslot_keeps_the_20h_clock_of_the_first_post(db, env, monkeypatch):
    """POST #1 times out (may be live), POST #2 gets a 403: FAILED ACCOUNT_DISCONNECTED. The next sync sees the
    account back and reslots it; 23 h after POST #1 the same key must never be POSTed again."""
    pid = make_post(db, env)
    z = use(monkeypatch, Zernio(routes(httpx.ReadTimeout("slow"), "docs_403_disconnected")))
    with pytest.raises(publisher.NetworkError):
        go(pid)
    go(pid, attempts=1)
    p = row(db, pid)
    assert (p.status, p.error_code, p.zernio_post_id, p.first_post_at is not None) == (
        "FAILED", "ACCOUNT_DISCONNECTED", None, True)  # fmt: skip
    first, second = z.sent(POST_)
    assert first.headers["Idempotency-Key"] == second.headers["Idempotency-Key"]
    with Session(db) as s:
        s.execute(update(Post).where(Post.id == pid).values(first_post_at=now() - timedelta(hours=23)))
        s.execute(update(Account).where(Account.id == p.account_id).values(connection_status="disconnected"))
        s.commit()
        accs = [a for a in s.query(Account).all()]
        parsed = [{"zernio_account_id": a.zernio_account_id, "zernio_profile_id": a.zernio_profile_id,
                   "username": a.username, "avatar_url": None, "connection_status": "connected"} for a in accs]  # fmt: skip

    async def sync():
        async with publish.SessionLocal() as s:
            await account_sync.upsert(s, 1, parsed)

    run(sync())  # disconnected -> connected: its ACCOUNT_DISCONNECTED posts move to the next free slot
    p = row(db, pid)
    assert (p.status, p.scheduled_for, p.error_code, p.zernio_media_url) == ("SCHEDULED", SLOT, None, PRESIGN["publicUrl"])
    set_due(db, pid)
    go(pid)
    assert (row(db, pid).status, row(db, pid).error_code, len(z.sent(POST_))) == ("DEAD_LETTER", "WINDOW_EXPIRED", 2)


def set_due(db, post_id: int) -> None:
    with Session(db) as s:
        s.execute(update(Post).where(Post.id == post_id).values(scheduled_for=now() - timedelta(minutes=1)))
        s.commit()


def test_remedy_retry_after_a_post_went_out_keeps_url_and_key(db, env, monkeypatch):
    """error_code only records the last run: an UNKNOWN after an earlier POST still replays the same key+URL."""
    pid = make_post(db, env, status="FAILED", error_code="UNKNOWN", zernio_media_url=PRESIGN["publicUrl"],
                    first_post_at=now() - timedelta(hours=1))  # fmt: skip
    before = row(db, pid)
    assert remedy(pid).json()["status"] == "PUBLISHING"
    p = row(db, pid)
    assert (p.zernio_media_url, p.idempotency_key, p.scheduled_for) == (
        PRESIGN["publicUrl"], before.idempotency_key, before.scheduled_for)  # fmt: skip


def test_remedy_refusals(db, env, monkeypatch):
    maybe = {"status": "DEAD_LETTER", "error_code": "NETWORK_ERROR", "zernio_media_url": PRESIGN["publicUrl"],
             "first_post_at": now() - timedelta(hours=1)}  # fmt: skip
    r = remedy(make_post(db, env, **maybe), "rerender")  # a new key would mean a second live Reel
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "MAYBE_PUBLISHED")
    expired = make_post(db, env, **maybe | {"error_code": "WINDOW_EXPIRED"})  # the operator checked Instagram
    assert remedy(expired).json()["status"] == "SCHEDULED" and row(db, expired).first_post_at is None
    monkeypatch.setattr(settings, "PUBLISHING_ENABLED", False)
    r = remedy(make_post(db, env, **maybe))  # would sit in PUBLISHING, uncancellable
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "PUBLISHING_DISABLED")
    monkeypatch.setattr(settings, "PUBLISHING_ENABLED", True)
    pid = make_post(db, env, status="FAILED", error_code="UNKNOWN")
    with Session(db) as s:
        s.execute(update(Account).where(Account.id == row(db, pid).account_id).values(disabled_at=now()))
        s.commit()
    for action in ("retry", "rerender"):
        r = remedy(pid, action)
        assert (r.status_code, r.json()["detail"]["code"]) == (409, "ACCOUNT_UNAVAILABLE")


def test_remedy_rerender_finds_it_published(db, env, monkeypatch):
    pid = make_post(db, env, status="DEAD_LETTER", error_code="NETWORK_ERROR", zernio_post_id=ZPOST)
    use(monkeypatch, Zernio({GET_: ["docs_create_published"]}))
    r = remedy(pid, "rerender")
    assert (r.json()["status"], r.json()["render"]["id"]) == ("PUBLISHED", row(db, pid).render_id)


def test_remedy_key_held_by_a_live_post_is_a_409(db, env):
    pid = make_post(db, env, status="FAILED", error_code="UNKNOWN")
    live = make_post(db, env, status="SCHEDULED", at=now() + timedelta(days=1), account_id=row(db, pid).account_id)
    with Session(db) as s:
        s.execute(update(Post).where(Post.id == live).values(idempotency_key=s.get(Post, pid).idempotency_key))
        s.commit()
    r = remedy(pid)
    assert (r.status_code, r.json()["detail"]["code"], row(db, pid).status) == (409, "STATE_CONFLICT", "FAILED")


def test_caption_edited_just_before_the_claim_is_what_goes_out(db, env, monkeypatch):
    pid = make_post(db, env)
    z = use(monkeypatch, Zernio(routes()))
    real_cas = publish.cas

    def cas_after_edit(model, id, from_statuses, **values):
        if values.get("status") == "PUBLISHING":  # the claim: the operator's PATCH commits just before it
            with Session(db) as s:
                s.execute(update(Post).where(Post.id == id).values(caption="NEW caption"))
                s.commit()
        return real_cas(model, id, from_statuses, **values)

    monkeypatch.setattr(publish, "cas", cas_after_edit)
    go(pid)
    assert json.loads(z.sent(POST_)[0].content)["content"] == "NEW caption"


def test_unexpected_error_fails_the_post_not_worker_crashed(db, env, monkeypatch):
    pid = make_post(db, env)
    use(monkeypatch, Zernio(routes()))
    with Session(db) as s:
        (env.dir / s.get(Render, s.get(Post, pid).render_id).output_key).unlink()  # the render file is gone
    go(pid)
    p = row(db, pid)
    assert (p.status, p.error_code) == ("FAILED", "UNKNOWN") and "FileNotFoundError" in p.error_detail["error"]
    assert len(env.alerts) == 1


def test_quota_exhausted_reslots_before_uploading(db, env, monkeypatch):
    pid = make_post(db, env)
    full = fx("publishing_limit")
    full["body"]["quotaUsage"] = full["body"]["quotaTotal"]
    z = use(monkeypatch, Zernio({"GET limit": [full]}))
    go(pid)
    p = row(db, pid)
    assert (p.status, p.scheduled_for, p.error_code, z.calls, len(z.limits)) == ("SCHEDULED", SLOT, "RATE_LIMITED", [], 1)


def test_missed_reslot_loses_to_an_operator_move(db, env):
    pid = make_post(db, env, at=now() - timedelta(hours=1))
    stale = row(db, pid)  # what the dispatcher read
    moved = now() + timedelta(hours=2)
    with Session(db) as s:
        s.execute(update(Post).where(Post.id == pid).values(scheduled_for=moved))
        s.commit()

    async def reslot():
        async with publish.SessionLocal() as s:
            post = await s.get(Post, pid)
            post.scheduled_for = stale.scheduled_for  # the value read before the move
            s.expunge(post)
            return await publish._reslot(s, post, "MISSED", ["SCHEDULED"])

    assert run(reslot()) is False
    assert (row(db, pid).scheduled_for, row(db, pid).error_code, env.alerts) == (moved, None, [])


def test_dispatcher_keeps_going_after_one_bad_post(db, env, monkeypatch):
    with Session(db) as s:
        s.execute(update(Post).where(Post.status.in_(["SCHEDULED", "PUBLISHING"])).values(status="CANCELLED"))
        s.commit()
    bad = make_post(db, env, at=now() - timedelta(minutes=3))
    good = make_post(db, env, at=now() - timedelta(minutes=2))
    real = publish.defer_publish

    async def defer(post_id, in_s=0):
        if post_id == bad:
            raise RuntimeError("boom")
        await real(post_id, in_s)

    monkeypatch.setattr(publish, "defer_publish", defer)
    run(dispatch(timestamp=0))
    assert (len(jobs(db, bad)), len(jobs(db, good))) == (0, 1)


# ---------------------------------------------------------------- audit fixes


def test_403_marks_the_account_disconnected_and_the_next_sync_reslots(db, env, monkeypatch):
    """The account row goes down with the 403: its other due post fails before uploading, and the sync that
    lists it connected again (operator reconnected in Zernio) moves both to free slots."""
    pid = make_post(db, env)
    acc = row(db, pid).account_id
    sibling = make_post(db, env, account_id=acc)
    z = use(monkeypatch, Zernio(routes("docs_403_disconnected")))
    go(pid)
    go(sibling)
    assert [(row(db, p).status, row(db, p).error_code) for p in (pid, sibling)] == [
        ("FAILED", "ACCOUNT_DISCONNECTED")] * 2  # fmt: skip
    assert (len(z.sent(PRESIGN_)), len(z.sent(POST_)), len(env.alerts)) == (1, 1, 1)
    with Session(db) as s:
        a = s.get(Account, acc)
        assert a.connection_status == "disconnected"
        parsed = [{"zernio_account_id": a.zernio_account_id, "zernio_profile_id": a.zernio_profile_id,
                   "username": a.username, "avatar_url": None, "connection_status": "connected"}]  # fmt: skip

    async def sync():
        async with publish.SessionLocal() as s:
            await account_sync.upsert(s, 1, parsed)

    run(sync())
    assert [(row(db, p).status, row(db, p).error_code, row(db, p).scheduled_for) for p in (pid, sibling)] == [
        ("SCHEDULED", None, SLOT)] * 2  # fmt: skip


def test_rerender_keeps_the_old_render_out_of_the_ready_tray(db, env, monkeypatch):
    pid = make_post(db, env, status="FAILED", error_code="CONTENT_REJECTED", zernio_post_id=ZPOST,
                    zernio_media_url=PRESIGN["publicUrl"], first_post_at=now())  # fmt: skip
    old = row(db, pid).render_id
    use(monkeypatch, Zernio({GET_: [fx("docs_207_failed", errorCategory="user_content")]}))
    assert remedy(pid).status_code == 200
    with Session(db) as s:
        r = s.get(Render, old)
        s.execute(update(Render).where(Render.id.in_([old, row(db, pid).render_id])).values(status="READY",
                  overlay_config=None, crop_config=None))  # fmt: skip
        s.commit()
        clip, superseded = r.source_clip_id, r.superseded_at
    with TestClient(api_app, headers=as_user()) as c:
        tray = c.get("/api/renders", params={"clip_id": clip, "status": "READY", "unscheduled": True}).json()
        c.portal.call(engine.dispose)
    assert superseded is not None and tray == []  # the new render has the post; the old one is superseded


def test_delete_render_takes_its_cancelled_posts_but_not_a_live_one(db, env):
    gone = make_post(db, env, status="CANCELLED")
    kept = make_post(db, env, status="CANCELLED")
    with Session(db) as s:
        p = s.get(Post, kept)
        s.add(Post(render_id=p.render_id, account_id=p.account_id, caption="c", scheduled_for=now() + timedelta(days=1),
                   status="SCHEDULED", idempotency_key=uuid.uuid4().hex))  # fmt: skip
        s.commit()
        rid_gone, rid_kept = s.get(Post, gone).render_id, p.render_id
    with TestClient(api_app, headers=as_user()) as c:
        assert c.delete(f"/api/renders/{rid_gone}").status_code == 204
        assert c.delete(f"/api/renders/{rid_kept}").status_code == 409
        c.portal.call(engine.dispose)
    with Session(db) as s:
        assert (s.get(Render, rid_gone), s.get(Post, gone)) == (None, None)
        assert s.get(Render, rid_kept) is not None and s.get(Post, kept).status == "CANCELLED"


# ---------------------------------------------------------------- cover


def cover_presign() -> dict:
    d = fx("presign")  # the recorded presign, its publicUrl renamed so the cover's URL differs from the video's
    d["body"]["publicUrl"] = COVER_URL
    return d


def add_cover(db, env, post_id: int) -> str:
    """Give the post's render a cover the way PUT /api/renders/{id}/cover stores one (the api refuses while
    the post is live)."""
    with Session(db) as s:
        r = s.get(Render, s.get(Post, post_id).render_id)
        r.cover_key = f"covers/{r.id}-0000abcd.jpg"
        (env.dir / "covers").mkdir(exist_ok=True)
        (env.dir / r.cover_key).write_bytes(COVER)
        s.commit()
        return r.cover_key


def put_cover(c, render_id: int, data: bytes = COVER) -> httpx.Response:
    return c.put(f"/api/renders/{render_id}/cover", files={"file": ("cover.jpg", data)})


def test_cover_api(db, env):
    rid = row(db, make_post(db, env, status="CANCELLED")).render_id  # a CANCELLED post doesn't pin the cover
    pinned = row(db, make_post(db, env, at=now() + timedelta(days=1))).render_id
    with Session(db) as s:  # make_post's stub configs don't serialise as RenderOut
        s.execute(update(Render).where(Render.id == rid).values(overlay_config=None, crop_config=None))
        s.commit()

    def covers():
        return sorted(f"/media/u/1/covers/{p.name}" for p in (env.dir / "u/1/covers").glob("*"))

    with TestClient(api_app, headers=as_user()) as c:
        assert put_cover(c, rid, b"\x89PNG\r\n\x1a\n").status_code == 415
        assert put_cover(c, rid, COVER + bytes(8 * 1024**2)).status_code == 413
        assert (put_cover(c, 10**9).status_code, c.delete(f"/api/renders/{10**9}/cover").status_code) == (404, 404)
        assert (put_cover(c, pinned).status_code, c.delete(f"/api/renders/{pinned}/cover").status_code) == (409, 409)
        assert covers() == []  # refused uploads leave no file
        first = put_cover(c, rid).json()["cover_url"]
        second = put_cover(c, rid).json()["cover_url"]
        assert first != second and second.startswith(f"/media/u/1/covers/{rid}-") and covers() == [second]
        assert c.delete(f"/api/renders/{rid}/cover").json()["cover_url"] is None and covers() == []
        assert put_cover(c, rid).status_code == 200
        assert c.delete(f"/api/renders/{rid}").status_code == 204 and covers() == []
        c.portal.call(engine.dispose)


def test_publish_with_a_cover(db, env, monkeypatch):
    pid = make_post(db, env)
    key = add_cover(db, env, pid)
    z = use(monkeypatch, Zernio(routes(httpx.ReadTimeout("slow"), "docs_replay_published")
                                | {PRESIGN_: ["presign", cover_presign()], PUT_: [None, None]}))  # fmt: skip
    with pytest.raises(publisher.NetworkError):
        go(pid)
    p = row(db, pid)
    assert (p.status, p.zernio_media_url, p.zernio_cover_url) == ("PUBLISHING", PRESIGN["publicUrl"], COVER_URL)
    go(pid, attempts=1)  # the retry reuses both URLs: no new presign
    assert row(db, pid).status == "PUBLISHED"
    [_, presign], [_, put], [first, second] = z.sent(PRESIGN_), z.sent(PUT_), z.sent(POST_)
    assert json.loads(presign.content) == {"filename": Path(key).name, "contentType": "image/jpeg", "size": len(COVER)}
    assert put.content == COVER and put.headers["content-type"] == "image/jpeg" and "authorization" not in put.headers
    assert first.content == second.content and first.headers["Idempotency-Key"] == second.headers["Idempotency-Key"]
    ig = json.loads(first.content)["platforms"][0]["platformSpecificData"]
    assert ig == {"shareToFeed": True, "instagramThumbnail": COVER_URL}


def test_crash_after_cover_upload_before_its_commit(db, env, monkeypatch):
    pid = make_post(db, env)
    add_cover(db, env, pid)
    z = use(monkeypatch, Zernio(routes() | {PRESIGN_: ["presign", cover_presign(), cover_presign()], PUT_: [None] * 3}))
    real = publisher.upload

    async def upload(c, path, content_type="video/mp4"):
        url = await real(c, path, content_type)
        if content_type == "image/jpeg" and len(z.sent(PRESIGN_)) == 2:
            raise Crash("after the cover upload")
        return url

    monkeypatch.setattr(publisher, "upload", upload)
    with pytest.raises(Crash):
        go(pid)
    p = row(db, pid)
    assert (p.status, p.zernio_media_url, p.zernio_cover_url, p.first_post_at) == (
        "PUBLISHING", PRESIGN["publicUrl"], None, None)  # fmt: skip
    go(pid)  # the committed video URL is reused; the cover, never committed, is uploaded again
    assert (row(db, pid).status, row(db, pid).zernio_cover_url) == ("PUBLISHED", COVER_URL)
    types = [json.loads(r.content)["contentType"] for r in z.sent(PRESIGN_)]
    assert types == ["video/mp4", "image/jpeg", "image/jpeg"] and len(z.sent(POST_)) == 1


def test_a_post_that_went_out_never_gets_a_cover(db, env, monkeypatch):
    """Once a POST went out its body never changes: no cover is uploaded, even if the render has one."""
    pid = make_post(db, env, status="PUBLISHING", zernio_media_url=PRESIGN["publicUrl"],
                    first_post_at=now() - timedelta(hours=1))  # fmt: skip
    add_cover(db, env, pid)
    z = use(monkeypatch, Zernio({POST_: ["docs_replay_published"]}))
    go(pid)
    [post] = z.sent(POST_)
    assert row(db, pid).status == "PUBLISHED" and "instagramThumbnail" not in post.content.decode()


def test_reslot_before_any_post_drops_the_cover_url(db, env):
    urls = {"zernio_media_url": PRESIGN["publicUrl"], "zernio_cover_url": COVER_URL}
    fresh = make_post(db, env, at=now() - timedelta(hours=2), **urls)
    posted = make_post(db, env, at=now() - timedelta(hours=2), first_post_at=now() - timedelta(hours=1), **urls)
    for pid in (fresh, posted):
        go(pid)  # missed: next free slot
    assert [(row(db, p).status, row(db, p).zernio_media_url, row(db, p).zernio_cover_url) for p in (fresh, posted)] == [
        ("SCHEDULED", None, None), ("SCHEDULED", PRESIGN["publicUrl"], COVER_URL)]  # fmt: skip


def test_rerender_copies_the_cover(db, env):
    pid = make_post(db, env, status="FAILED", error_code="CONTENT_REJECTED", zernio_media_url=PRESIGN["publicUrl"],
                    zernio_cover_url=COVER_URL)  # fmt: skip
    old_key, old = add_cover(db, env, pid), row(db, pid).render_id
    assert remedy(pid, "rerender").status_code == 200
    p = row(db, pid)
    with Session(db) as s:
        new_key = s.get(Render, p.render_id).cover_key
    assert new_key.startswith(f"u/1/covers/{p.render_id}-") and (env.dir / new_key).read_bytes() == COVER
    assert (p.zernio_media_url, p.zernio_cover_url) == (None, None)
    with TestClient(api_app, headers=as_user()) as c:
        assert c.delete(f"/api/renders/{old}").status_code == 204
        c.portal.call(engine.dispose)
    assert not (env.dir / old_key).exists() and (env.dir / new_key).read_bytes() == COVER


# ---------------------------------------------------------------- each user's own Zernio key


def new_user(db, **values) -> int:
    with Session(db) as s:
        u = User(username=f"p.{uuid.uuid4().hex[:10]}", **values)
        s.add(u)
        s.commit()
        return u.id


def user(db, uid: int) -> User:
    with Session(db) as s:
        return s.get(User, uid)


def only_these_due(db) -> None:
    """Other tests' leftovers must not be dispatched here."""
    with Session(db) as s:
        s.execute(update(Post).where(Post.status.in_(["SCHEDULED", "PUBLISHING"])).values(status="CANCELLED"))
        s.commit()


def test_each_post_goes_out_with_its_owners_key_and_generation(db, env, monkeypatch):
    other = new_user(db)
    zernio_key(other, "sk_other", gen=4)
    mine, theirs = make_post(db, env), make_post(db, env, user_id=other)
    z = use(monkeypatch, Zernio(routes() | {PRESIGN_: ["presign"] * 2, PUT_: [None] * 2, POST_: ["docs_create_published"] * 2}))
    go(mine)
    go(theirs)
    assert [row(db, p).status for p in (mine, theirs)] == ["PUBLISHED"] * 2
    assert [r.headers["authorization"] for r in z.sent(POST_)] == ["Bearer sk_test", "Bearer sk_other"]
    assert [r.headers["authorization"] for r in z.limits] == ["Bearer sk_test", "Bearer sk_other"]  # the quota read too
    assert [row(db, p).key_gen for p in (mine, theirs)] == [1, 4]  # set with first_post_at, in the same CAS


def test_a_maybe_live_post_never_replays_under_another_key(db, env, monkeypatch):
    """Critique A1: Zernio replays an Idempotency-Key per credential, so a post first sent under key generation 1 is
    never POSTed under generation 2 (DEAD_LETTER KEY_CHANGED); Retry says so at once, and Re-render is its remedy.
    The same generation replays as always."""
    zernio_key(1, "sk_new", gen=2)
    maybe = {"zernio_media_url": PRESIGN["publicUrl"], "first_post_at": now() - timedelta(hours=1)}  # key_gen 1
    pid = make_post(db, env, status="PUBLISHING", **maybe)
    z = use(monkeypatch, Zernio({}))
    go(pid)
    assert (row(db, pid).status, row(db, pid).error_code, z.calls) == ("DEAD_LETTER", "KEY_CHANGED", [])
    failed = make_post(db, env, status="FAILED", error_code="UNKNOWN", **maybe)  # failed before the key changed
    for _ in range(2):
        r = remedy(failed, "retry").json()  # no replay is queued
        assert (r["status"], r["error_code"], r["remedy"]["action"], jobs(db, failed)) == (
            "DEAD_LETTER", "KEY_CHANGED", "rerender", [])  # fmt: skip
    r = remedy(failed, "rerender")  # after checking Instagram: a new render and key
    assert (r.status_code, r.json()["status"], row(db, failed).first_post_at) == (200, "SCHEDULED", None)
    same = make_post(db, env, status="PUBLISHING", key_gen=2, **maybe)
    use(monkeypatch, Zernio({POST_: ["docs_replay_published"]}))
    go(same)
    assert row(db, same).status == "PUBLISHED"


def test_the_claim_reads_the_key_under_the_users_row_lock(db, env, monkeypatch):
    """publish_post reads the key FOR SHARE, in the claim's transaction: a key change in flight (FOR UPDATE) lands
    first, and the post goes out with the new key under its generation. A user whose key is no longer valid (or who
    is disabled) gets FAILED ZERNIO_KEY_MISSING, nothing sent (dispatch skips them; this is the race)."""
    from app.core.secrets import seal

    pid = make_post(db, env)
    z = use(monkeypatch, Zernio(routes()))
    with db.connect() as change, ThreadPoolExecutor(1) as pool:
        change.execute(text("SELECT 1 FROM users WHERE id = 1 FOR UPDATE"))  # PUT /api/me/zernio-key, mid-way
        pending = pool.submit(go, pid)
        time.sleep(1)
        assert not pending.done()
        change.execute(update(User).where(User.id == 1).values(zernio_key_enc=seal("sk_rotated"), zernio_key_gen=2))
        change.commit()
        pending.result(timeout=30)
    assert (row(db, pid).status, row(db, pid).key_gen) == ("PUBLISHED", 2)
    assert {r.headers["authorization"] for k, r in z.calls if k != PUT_} | {r.headers["authorization"] for r in z.limits} == {
        "Bearer sk_rotated"}  # fmt: skip
    for values in ({"status": "invalid"}, {"disabled_at": now()}):
        zernio_key(1, "sk_test", **values)
        pid = make_post(db, env)
        z = use(monkeypatch, Zernio({}))
        go(pid)
        assert (row(db, pid).status, row(db, pid).error_code, z.calls, z.limits) == ("FAILED", "ZERNIO_KEY_MISSING", [], [])
    zernio_key(1, "sk_test", disabled_at=None)


def test_retry_waits_for_a_key_change_then_refuses_the_replay(db, env):
    """Critique A2: Retry of a maybe-live post takes its user's row lock as the claim does, so a key change in flight
    (FOR UPDATE) lands first, and the replay under the new generation is refused, never queued."""
    pid = make_post(db, env, status="FAILED", error_code="UNKNOWN", zernio_media_url=PRESIGN["publicUrl"],
                    first_post_at=now() - timedelta(hours=1))  # fmt: skip
    with db.connect() as change, ThreadPoolExecutor(1) as pool:
        change.execute(text("SELECT 1 FROM users WHERE id = 1 FOR UPDATE"))  # PUT /api/me/zernio-key, mid-way
        pending = pool.submit(remedy, pid)
        time.sleep(1)
        assert not pending.done()
        change.execute(update(User).where(User.id == 1).values(zernio_key_gen=2))
        change.commit()
        r = pending.result(timeout=30)
    assert (r.json()["status"], r.json()["error_code"], jobs(db, pid)) == ("DEAD_LETTER", "KEY_CHANGED", [])


@pytest.mark.parametrize("fixture, at, code, reason", [
    ("docs_401_unauthorized", POST_, "ZERNIO_KEY_INVALID", "Zernio refused the key"),
    ("docs_403_insufficient_permissions", POST_, "ZERNIO_KEY_INVALID", "the key has Zernio's publishing group disabled"),
    ("docs_402_payment_required", POST_, "ZERNIO_PAYMENT_REQUIRED", "Zernio reports a failed payment on your Zernio account"),
    ("docs_403_not_your_account", PRESIGN_, "ZERNIO_KEY_INVALID", "Zernio refused the key"),  # presign names no account
    ("docs_403_profile_over_limit", POST_, "PROFILE_OVER_LIMIT", None),  # the account: this user's other posts go on
    ("docs_403_not_your_account", POST_, "UNKNOWN", None),  # an accountId outside the key's reach: this post only
])  # fmt: skip
def test_rejections_of_the_key_pause_its_user(db, env, monkeypatch, fixture, at, code, reason):
    """Critique A3: on status, code, required_group and type, never the message. The key's own problems fail the post
    and mark the user's key invalid (dispatch then skips them), with one alert about the key, after the post's commit."""
    pid = make_post(db, env)
    use(monkeypatch, Zernio(routes(fixture) | ({PRESIGN_: [fixture]} if at == PRESIGN_ else {})))
    go(pid)
    p, u = row(db, pid), user(db, 1)
    assert (p.status, p.error_code) == ("FAILED", code)
    assert (u.zernio_key_status, u.zernio_error) == ("invalid", reason) if reason else ("valid", None)
    [(_, link)] = env.alerts
    assert link.endswith("/settings" if reason else f"/recover/{pid}")
    assert p.alerted_at is None or not reason  # the key's alert, not the post's


def test_one_alert_per_key_flip(db, env, monkeypatch):
    a, b = make_post(db, env, status="PUBLISHING"), make_post(db, env, status="PUBLISHING")
    use(monkeypatch, Zernio({PRESIGN_: ["docs_401_unauthorized"] * 2}))
    go(a)
    go(b)
    assert [row(db, p).error_code for p in (a, b)] == ["ZERNIO_KEY_INVALID"] * 2
    assert len(env.alerts) == 1 and user(db, 1).zernio_key_status == "invalid"


def test_dispatch_only_for_users_with_a_working_key(db, env, monkeypatch):
    """No valid key (none, refused) or a disabled user: their due posts stay SCHEDULED, untouched (not even re-slotted).
    A PUBLISHING post always gets its job, which ends it: FAILED ZERNIO_KEY_MISSING without a key that opens."""
    only_these_due(db)
    keyless, refused, disabled = new_user(db), new_user(db), new_user(db, disabled_at=now())
    zernio_key(refused, "sk_refused", status="invalid")
    zernio_key(disabled, "sk_disabled")
    mine = make_post(db, env)
    waiting = [make_post(db, env, user_id=u) for u in (keyless, refused, disabled)]
    overdue = make_post(db, env, user_id=keyless, at=now() - timedelta(hours=2))
    orphan = make_post(db, env, user_id=keyless, status="PUBLISHING")
    run(dispatch(timestamp=0))
    assert [len(jobs(db, p)) for p in (mine, *waiting, overdue, orphan)] == [1, 0, 0, 0, 0, 1]
    assert (row(db, overdue).status, row(db, overdue).error_code) == ("SCHEDULED", None)
    z = use(monkeypatch, Zernio({}))
    go(orphan)
    assert (row(db, orphan).status, row(db, orphan).error_code, z.calls) == ("FAILED", "ZERNIO_KEY_MISSING", [])
    assert env.uids == [keyless]


def test_dispatch_defers_publishes_first_and_alerts_last(db, env, monkeypatch):
    """Critique C1: one tick serves every user, so due posts are deferred before any reslot (each locks an account and
    searches slots), and alerts go out together at the end: a slow Telegram never makes a due post late."""
    only_these_due(db)
    missed = make_post(db, env, at=now() - timedelta(hours=2))  # the earliest, so first in scheduled order
    failed = make_post(db, env, at=now() - timedelta(minutes=10), render_status="FAILED")
    due = make_post(db, env)
    events = []
    real_defer, real_one = publish.defer_publish, publish._dispatch_one

    async def defer(post_id, in_s=0):
        events.append(("defer", post_id))
        await real_defer(post_id, in_s)

    async def one(s, post, render_status, now_):
        events.append(("work", post.id))
        await real_one(s, post, render_status, now_)

    async def alert(uid, text_, link=None, buttons=None):
        events.append(("alert", int(link.rsplit("/", 1)[1])))
        return True

    monkeypatch.setattr(publish, "defer_publish", defer)
    monkeypatch.setattr(publish, "_dispatch_one", one)
    monkeypatch.setattr(publish, "notify", alert)
    run(dispatch(timestamp=0))
    assert events == [("defer", due), ("work", missed), ("work", failed), ("alert", missed), ("alert", failed)]
    assert (row(db, missed).error_code, row(db, failed).error_code) == ("MISSED", "RENDER_FAILED")


def test_sync_skips_accounts_another_user_has(db, env):
    """Critique A6: row by row, so one account held by another Clipper user (two members of one Zernio team) is
    reported, and the rest of the sync still happens."""
    held = row(db, make_post(db, env)).account_id
    with Session(db) as s:
        theirs = s.get(Account, held).zernio_account_id
    other, new = new_user(db), uuid.uuid4().hex
    parsed = [{"zernio_account_id": z, "zernio_profile_id": "p", "username": name, "avatar_url": None,
               "connection_status": "connected"} for z, name in [(theirs, "held.one"), (new, "new.one")]]  # fmt: skip

    async def sync():
        async with publish.SessionLocal() as s:
            return await account_sync.upsert(s, other, parsed)

    assert run(sync()) == ["held.one"]
    with Session(db) as s:
        assert s.get(Account, held).user_id == 1
        assert s.scalar(select(Account.user_id).where(Account.zernio_account_id == new)) == other


def test_periodic_sync_runs_per_user_with_their_key(db, env, monkeypatch):
    """Every enabled user with a valid key, each with their own; a key Zernio refuses goes invalid, with one alert."""
    ok, refused = new_user(db), new_user(db)
    with Session(db) as s:  # nobody else's key counts here (user 1's accounts are other tests' fixtures)
        s.execute(update(User).where(User.id.not_in([ok, refused])).values(zernio_key_status="none"))
        s.commit()
    zernio_key(ok, "sk_ok")
    zernio_key(refused, "sk_refused")
    zid, seen = uuid.uuid4().hex, []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers["authorization"])
        d = fx("docs_401_unauthorized") if req.headers["authorization"] == "Bearer sk_refused" else fx("accounts")
        for a in d["body"].get("accounts", []):
            a |= {"_id": zid, "username": "synced.one"}
        return httpx.Response(d["status"], json=d["body"])

    monkeypatch.setattr(zernio, "client", lambda key, **kw: REAL_CLIENT(key, transport=httpx.MockTransport(handler), **kw))
    run(account_sync.sync_accounts(timestamp=0))
    assert seen == ["Bearer sk_ok", "Bearer sk_refused"]
    with Session(db) as s:
        assert s.scalar(select(Account.user_id).where(Account.zernio_account_id == zid)) == ok
    assert (user(db, refused).zernio_key_status, user(db, ok).zernio_key_status) == ("invalid", "valid")
    assert env.uids == [refused]
