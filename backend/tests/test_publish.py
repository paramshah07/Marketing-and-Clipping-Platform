"""publish_post, the dispatcher and the remedy endpoint against the test database. Zernio is an
httpx.MockTransport serving ONLY bodies from tests/fixtures/zernio/ (see its README). Crash windows are
simulated by raising from publish._pause, the PUBLISH_DEBUG_PAUSE hook, at the named point."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import engine
from app.main import app as api_app
from app.models import Account, Post, Render, SourceClip, cas
from app.services import publisher, slots, zernio
from app.services.errors import CATEGORY
from app.tasks import accounts as account_sync
from app.tasks import publish
from app.tasks.publish import dispatch, publish_post
from app.tasks.queue import app

FIX = Path(__file__).parent / "fixtures" / "zernio"
PRESIGN = json.loads((FIX / "presign.json").read_text())["body"]
ZPOST = "65f1c0a9e2b5af0012ab34cd"  # the docs examples' post id
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
    alerts: list[tuple[str, str]] = []

    async def record(text_, link=None):
        alerts.append((text_, link))
        return True

    monkeypatch.setattr(publish, "notify", record)
    monkeypatch.setattr(account_sync, "notify", record)

    async def next_slot(s, account, after, exclude_post_id=None):
        return SLOT

    monkeypatch.setattr(slots, "next_free_slot", next_slot)
    zernio._quota_cache.clear()
    use(monkeypatch, Zernio({}))  # nothing ever reaches the real Zernio: an unrouted call fails the test
    return SimpleNamespace(alerts=alerts, dir=tmp_path)


REAL_CLIENT = zernio.client


def use(monkeypatch, z: Zernio) -> Zernio:
    """The real Zernio clients (base URL, auth, timeouts) over a MockTransport."""
    monkeypatch.setattr(settings, "ZERNIO_API_KEY", "sk_test")
    monkeypatch.setattr(zernio, "client", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(z), **kw))
    return z


def crash_at(monkeypatch, point: str) -> None:
    async def pause(p):
        if p == point:
            raise Crash(p)

    monkeypatch.setattr(publish, "_pause", pause)


def make_post(
    db, env, status="SCHEDULED", at=None, render_status="READY", duration=12.0, account_id=None, **post
) -> int:
    with Session(db) as s:
        if account_id is None:
            acc = Account(
                zernio_account_id=uuid.uuid4().hex, zernio_profile_id="p", username="ig_acct", timezone="Europe/London"
            )
            s.add(acc)
            s.flush()
            account_id = acc.id
        clip = SourceClip(origin="upload", status="READY", original_filename="c.mp4")
        s.add(clip)
        s.flush()
        (env.dir / "renders").mkdir(exist_ok=True)
        key = f"renders/{uuid.uuid4().hex}.mp4"
        (env.dir / key).write_bytes(bytes(range(256)) * 10_000)  # 2.5 MB: several 1 MB chunks
        r = Render(source_clip_id=clip.id, status=render_status, output_key=key, duration_s=duration,
                   overlay_config={"x": 0.1}, crop_config={"x": 0.2}, caption="render caption")  # fmt: skip
        s.add(r)
        s.flush()
        at = at or now() - timedelta(minutes=1)
        p = Post(render_id=r.id, account_id=account_id, caption="caption #reel", scheduled_for=at, status=status,
                 idempotency_key=uuid.uuid4().hex, **post)  # fmt: skip
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
    assert json.loads(post.content) == {
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
    with TestClient(api_app) as c:
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
    with TestClient(api_app) as c:
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
                    error_detail={"error": "bad"}, zernio_media_url=PRESIGN["publicUrl"])  # fmt: skip
    key = row(db, pid).idempotency_key
    r = remedy(pid)
    p = row(db, pid)
    assert (r.json()["status"], p.zernio_media_url, p.error_code, p.error_detail, p.idempotency_key) == (
        "SCHEDULED",
        None,
        None,
        None,
        key,
    )
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
        same = lambda x: (x.source_clip_id, x.brand_id, x.overlay_config, x.crop_config, x.caption)  # noqa: E731
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
    from app.api import recovery

    pid = make_post(db, env, status="FAILED", error_code="ACCOUNT_DISCONNECTED")
    acc = row(db, pid).account_id
    sibling = make_post(db, env, status="DEAD_LETTER", error_code="ACCOUNT_DISCONNECTED", account_id=acc)
    other = make_post(db, env, status="FAILED", error_code="UNKNOWN", account_id=acc)
    with Session(db) as s:
        s.execute(update(Account).where(Account.id == acc).values(connection_status="disconnected"))
        s.commit()

    async def sync(s):  # stands in for the Zernio account sync (GET /v1/accounts)
        await s.execute(
            update(Account)
            .where(Account.id == acc)
            .values(connection_status="connected" if connected else "disconnected")
        )
        await s.commit()

    monkeypatch.setattr(recovery.account_sync, "sync", sync)
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
            await account_sync.upsert(s, parsed)

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
