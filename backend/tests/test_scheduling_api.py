"""Accounts, posts and auto-schedule API against the test database. The clock is scheduling._now
(monkeypatched), Zernio is never called (no API key), Telegram is a recorder."""

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, update

from app.api import scheduling
from app.core.config import settings
from app.core.db import SessionLocal, SyncSession, engine
from app.main import app
from app.models import Account, Brand, Post, Render, SourceClip
from app.services import zernio
from app.tasks import accounts as account_sync
from conftest import as_user, zernio_key

NOW = datetime(2030, 1, 7, 8, 0, tzinfo=UTC)  # a Monday, winter: London = UTC
M, H, D = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)


@pytest.fixture(scope="module")
def client(db):
    with TestClient(app, headers=as_user()) as c:
        yield c
        c.portal.call(engine.dispose)  # its connections belong to this client's event loop


@pytest.fixture(autouse=True)
def env(monkeypatch):
    zernio._quota_cache.clear()  # user 1 has no Zernio key here: quota None, never a real call
    clock = {"now": NOW}
    monkeypatch.setattr(scheduling, "_now", lambda: clock["now"])
    return clock


def iso(t: datetime) -> str:
    return t.isoformat()


def account(**kw) -> int:
    values = {
        "zernio_account_id": uuid.uuid4().hex, "zernio_profile_id": "p", "username": "acct", "timezone": "UTC",
        "posting_slots": {"times": ["09:00", "13:00", "19:00"]}, "min_gap_minutes": 30, "daily_cap": 10,
    } | kw  # fmt: skip
    with SyncSession() as s:
        a = Account(**values)
        s.add(a)
        s.commit()
        return a.id


def render(auto_approve=False, brand=True, **kw) -> int:
    with SyncSession() as s:
        clip = SourceClip(origin="upload", status="READY", original_filename="c.mp4")
        b = Brand(name="Acme", auto_approve=auto_approve) if brand else None
        s.add_all([clip] + ([b] if b else []))
        s.flush()
        r = Render(**{"source_clip_id": clip.id, "brand_id": b.id if b else None, "status": "READY", "duration_s": 20.0,
                      "caption": "hello", "thumbnail_key": "renders/1.jpg", "output_key": "renders/1.mp4"} | kw)  # fmt: skip
        s.add(r)
        s.commit()
        return r.id


def set_post(post_id: int, **values) -> None:
    with SyncSession() as s:
        s.execute(update(Post).where(Post.id == post_id).values(**values))
        s.commit()


def create(client, rid, aid, at, **kw):
    return client.post("/api/posts", json={"render_id": rid, "account_id": aid, "scheduled_for": iso(at), **kw})


def code(r) -> str:
    return r.json()["detail"]["code"]


# ---------------------------------------------------------------- create


def test_create_draft_and_defaults(client):
    aid, rid = account(), render()
    at = NOW + D
    r = create(client, rid, aid, at)
    assert r.status_code == 201, r.text
    p = r.json()
    assert (p["status"], p["caption"], p["account_username"]) == ("DRAFT", "hello", "acct")
    assert p["render"]["clip_name"] == "c.mp4" and p["render"]["brand_name"] == "Acme"
    assert p["render"]["thumbnail_url"] == "/media/renders/1.jpg" and p["render"]["output_url"] == "/media/renders/1.mp4"
    assert (p["cause"], p["remedy"]) == (None, None)
    assert datetime.fromisoformat(p["scheduled_for"]) == at
    with SyncSession() as s:
        assert s.get(Post, p["id"]).idempotency_key == scheduling.idempotency_key(rid, aid, at)
    assert client.get(f"/api/posts/{p['id']}").json() == p


def test_create_auto_approve_brand_and_caption(client):
    aid = account()
    p = create(client, render(auto_approve=True), aid, NOW + D, caption="custom").json()
    assert (p["status"], p["caption"]) == ("SCHEDULED", "custom")
    assert create(client, render(brand=False), aid, NOW + D + H).json()["status"] == "DRAFT"  # no brand: draft


def test_create_is_idempotent_and_recreatable_after_cancel(client):
    aid, rid = account(), render()
    at = NOW + D
    first = create(client, rid, aid, at)
    assert first.status_code == 201
    again = create(client, rid, aid, datetime.fromisoformat("2030-01-08T03:00:00-05:00"))  # same instant, other offset
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert client.post(f"/api/posts/{first.json()['id']}/cancel").json()["status"] == "CANCELLED"
    recreated = create(client, rid, aid, at)
    assert recreated.status_code == 201 and recreated.json()["id"] != first.json()["id"]


def test_create_guards(client):
    aid, rid = account(), render()
    ok = NOW + D
    assert code(create(client, render(status="RENDERING"), aid, ok)) == "RENDER_NOT_READY"
    assert code(create(client, render(output_key=None), aid, ok)) == "FILE_DELETED"  # free-published took its MP4
    limit = settings.ZERNIO_MAX_REEL_SECONDS
    r = create(client, render(duration_s=limit + 0.5), aid, ok)
    assert (r.status_code, code(r)) == (422, "TOO_LONG")
    assert create(client, render(duration_s=float(limit)), aid, ok).status_code == 201  # the limit itself is allowed
    r = create(client, rid, account(disabled_at=NOW), ok)
    assert (r.status_code, code(r)) == (422, "ACCOUNT_UNAVAILABLE")
    assert code(create(client, rid, account(connection_status="disconnected"), ok)) == "ACCOUNT_UNAVAILABLE"
    r = create(client, rid, aid, NOW - 2 * M)
    assert (r.status_code, code(r)) == (422, "TOO_SOON")
    assert create(client, rid, aid, NOW - M).status_code == 201  # "now" on a minute-precise picker
    assert create(client, render(), aid, NOW + 5 * M).status_code == 201
    assert code(create(client, rid, aid, datetime(2030, 1, 9, 9))) == "BAD_TIME"  # noqa: DTZ001 (naive on purpose)
    assert create(client, 10**6, aid, ok).status_code == 404
    assert create(client, rid, 10**6, ok).status_code == 404


def test_create_ignores_an_old_clients_rights_override(client):
    aid = account()
    assert create(client, render(), aid, NOW + D, rights_override=False).status_code == 201


def link_clip(url: str) -> int:
    with SyncSession() as s:
        c = SourceClip(origin="url", status="READY", source_url=url)
        s.add(c)
        s.commit()
        return c.id


def clip_of(render_id: int) -> int:
    with SyncSession() as s:
        return s.get(Render, render_id).source_clip_id


def test_a_video_goes_to_an_account_once_unless_reposted(client):
    """A post of a clip (or of another clip of its link) on an account that has one, a draft up to published: 409
    ALREADY_POSTED unless repost, and auto-schedule leaves it unplaced. Other accounts and cancelled posts don't count."""
    aid, other, rid = account(), account(), render()
    first = create(client, rid, aid, NOW + D).json()
    twin = render(source_clip_id=clip_of(rid))  # the same clip, rendered again
    r = create(client, twin, aid, NOW + 2 * D)
    assert (r.status_code, code(r)) == (409, "ALREADY_POSTED")
    assert r.json()["detail"]["message"] == f"This video is already queued on @acct for Tue 8 Jan (post {first['id']})."
    assert [u["reason"] for u in auto(client, aid, [twin]).json()["unplaced"]] == [
        f"this video is already queued on @acct for Tue 8 Jan (post {first['id']})"]  # fmt: skip
    assert create(client, twin, other, NOW + 2 * D).status_code == 201  # another account
    set_post(first["id"], status="PUBLISHED", published_at=NOW + D)
    r = create(client, twin, aid, NOW + 2 * D)
    assert r.json()["detail"]["message"] == f"This video already went to @acct on Tue 8 Jan (post {first['id']})."
    assert create(client, twin, aid, NOW + 2 * D, repost=True).status_code == 201  # asked, and meant
    # another clip of the same link is the same video, whatever its tracking query
    a = render(source_clip_id=link_clip("https://www.tiktok.com/@x/video/7000000000000000042?_r=1&_t=a"))
    b = render(source_clip_id=link_clip("https://www.tiktok.com/@x/video/7000000000000000042"))
    p = create(client, a, aid, NOW + 3 * D).json()
    assert code(create(client, b, aid, NOW + 4 * D)) == "ALREADY_POSTED"
    client.post(f"/api/posts/{p['id']}/cancel")
    assert create(client, b, aid, NOW + 4 * D).status_code == 201


# ---------------------------------------------------------------- patch / approve / cancel


def test_patch_moves_without_rekeying(client):
    aid, rid = account(), render()
    p = create(client, rid, aid, NOW + D).json()
    r = client.patch(f"/api/posts/{p['id']}", json={"scheduled_for": iso(NOW + 2 * D), "caption": "new"})
    assert r.status_code == 200, r.text
    assert (datetime.fromisoformat(r.json()["scheduled_for"]), r.json()["caption"]) == (NOW + 2 * D, "new")
    with SyncSession() as s:
        assert s.get(Post, p["id"]).idempotency_key == scheduling.idempotency_key(rid, aid, NOW + D)
    assert code(client.patch(f"/api/posts/{p['id']}", json={"scheduled_for": iso(NOW - 2 * M)})) == "TOO_SOON"
    for status in ("PUBLISHING", "PUBLISHED", "FAILED", "CANCELLED"):
        set_post(p["id"], status=status)
        r = client.patch(f"/api/posts/{p['id']}", json={"caption": "x"})
        assert (r.status_code, code(r)) == (409, "STATE_CONFLICT"), status
    assert client.patch("/api/posts/999999", json={"caption": "x"}).status_code == 404


def test_music_on_a_post(client, monkeypatch):
    aid, rid = account(), render()
    music = {"id": "482851939985510", "title": "Summer Nights", "artist": "The Example Band", "volume": 80}
    r = create(client, rid, aid, NOW + D, music=music)  # the server's switch is off
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "INSTAGRAM_MUSIC_OFF")
    monkeypatch.setattr(settings, "INSTAGRAM_CATALOG_MUSIC", True)
    p = create(client, rid, aid, NOW + D, music=music).json()
    assert p["music"] == music | {"video_volume": 100}
    assert create(client, render(), aid, NOW + 2 * D, music={"id": "summer"}).status_code == 422  # ids are digits
    patch = lambda body: client.patch(f"/api/posts/{p['id']}", json=body)  # noqa: E731
    assert patch({"music": music | {"volume": 50}}).json()["music"]["volume"] == 50
    assert patch({"caption": "new"}).json()["music"]["volume"] == 50  # omitted: kept
    monkeypatch.setattr(settings, "INSTAGRAM_CATALOG_MUSIC", False)
    assert patch({"music": music}).json()["detail"]["code"] == "INSTAGRAM_MUSIC_OFF"
    assert patch({"music": None}).json()["music"] is None  # taking it off always works
    monkeypatch.setattr(settings, "INSTAGRAM_CATALOG_MUSIC", True)
    set_post(p["id"], first_post_at=NOW)  # Zernio may have it with its first body
    r = patch({"music": music})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "MUSIC_LOCKED")


def test_approve(client, env):
    aid = account()
    p = create(client, render(), aid, NOW + D).json()
    r = client.post(f"/api/posts/{p['id']}/approve")
    assert r.json()["status"] == "SCHEDULED" and r.json()["scheduled_for"] == p["scheduled_for"]
    assert code(client.post(f"/api/posts/{p['id']}/approve")) == "STATE_CONFLICT"  # DRAFT only
    # a draft whose time has passed moves to the next free slot >= now + 10 min
    late = create(client, render(), aid, NOW + 10 * H).json()  # 18:00
    env["now"] = NOW + 10 * H + 3 * M  # 18:03
    r = client.post(f"/api/posts/{late['id']}/approve").json()
    assert (r["status"], datetime.fromisoformat(r["scheduled_for"])) == ("SCHEDULED", NOW + 11 * H)  # 19:00
    # "Post now": created at this instant and approved at once, it keeps its time
    now = create(client, render(), aid, env["now"]).json()
    r = client.post(f"/api/posts/{now['id']}/approve").json()
    assert (r["status"], r["scheduled_for"]) == ("SCHEDULED", now["scheduled_for"])


def test_cancel(client):
    aid = account()
    ids = [create(client, render(), aid, NOW + D + i * H).json()["id"] for i in range(6)]
    for pid, status in zip(ids, ["DRAFT", "SCHEDULED", "FAILED", "DEAD_LETTER"]):
        set_post(pid, status=status)
        assert client.post(f"/api/posts/{pid}/cancel").json()["status"] == "CANCELLED"
    assert code(client.post(f"/api/posts/{ids[0]}/cancel")) == "STATE_CONFLICT"  # already cancelled
    for pid, status in zip(ids[4:], ["PUBLISHING", "PUBLISHED"]):
        set_post(pid, status=status)
        assert code(client.post(f"/api/posts/{pid}/cancel")) == "STATE_CONFLICT"


# ---------------------------------------------------------------- accounts


def test_account_patch_and_disable(client):
    aid = account()
    r = client.patch(f"/api/accounts/{aid}", json={"posting_slots": {"times": ["19:00", "9:05", "19:00"]},
                                                    "timezone": "America/New_York", "daily_cap": 3})  # fmt: skip
    assert r.status_code == 200, r.text
    a = r.json()
    assert (a["posting_slots"]["times"], a["timezone"], a["daily_cap"], a["min_gap_minutes"]) == (
        ["09:05", "19:00"], "America/New_York", 3, 30)  # fmt: skip
    assert code(client.patch(f"/api/accounts/{aid}", json={"timezone": "Mars/Base"})) == "BAD_TIMEZONE"
    assert code(client.patch(f"/api/accounts/{aid}", json={"posting_slots": {"times": ["25:00"]}})) == "BAD_SLOT"
    assert client.patch(f"/api/accounts/{aid}", json={"daily_cap": 0}).status_code == 422
    posts = [create(client, render(), aid, NOW + D + i * H).json()["id"] for i in range(3)]
    client.post(f"/api/posts/{posts[1]}/approve")
    set_post(posts[2], status="PUBLISHED")
    a = client.patch(f"/api/accounts/{aid}", json={"disabled": True}).json()
    assert a["disabled_at"] is not None
    statuses = [client.get(f"/api/posts/{p}").json()["status"] for p in posts]
    assert statuses == ["CANCELLED", "CANCELLED", "PUBLISHED"]
    assert client.patch(f"/api/accounts/{aid}", json={"disabled": False}).json()["disabled_at"] is None


def test_account_list_counts(client):
    # New York, now = 2030-01-07 08:00 UTC = 03:00 EST: the local day is 01-07 05:00 UTC .. 01-08 05:00 UTC
    aid = account(timezone="America/New_York", username="zz-counts")
    ids = [create(client, render(), aid, t).json()["id"]
           for t in (NOW + 2 * H, NOW + 19 * H, NOW + 20 * H, NOW + 21 * H + 30 * M, NOW + 2 * D)]  # fmt: skip
    client.post(f"/api/posts/{ids[2]}/cancel")
    a = next(x for x in client.get("/api/accounts").json() if x["id"] == aid)
    # 05:00 EST and 22:00 EST (03:00 UTC tomorrow) count; 23:00 EST is cancelled; 00:30 EST is tomorrow
    assert a["today_count"] == 2
    assert datetime.fromisoformat(a["next_post_at"]) == NOW + 2 * H
    assert a["quota"] is None  # no Zernio key in tests


def test_next_slot(client):
    aid = account(posting_slots={"times": ["08:05", "08:15"]})
    assert datetime.fromisoformat(client.get(f"/api/accounts/{aid}/next-slot").json()["scheduled_for"]) == NOW + 15 * M
    assert client.get(f"/api/accounts/{account(posting_slots={'times': []})}/next-slot").json() == {"scheduled_for": None}


# ---------------------------------------------------------------- auto-schedule


def auto(client, aid, rids, **kw):
    return client.post("/api/posts/auto-schedule", json={"account_id": aid, "render_ids": rids, **kw})


def placed_times(r) -> dict[int, datetime]:
    return {p["render_id"]: datetime.fromisoformat(p["post"]["scheduled_for"]) for p in r.json()["placed"]}


def test_auto_schedule_order_lead_and_reasons(client, env):
    env["now"] = NOW + 52 * M  # 08:52: the 09:00 slot is 8 min away, too close
    aid = account(posting_slots={"times": ["09:00", "09:05", "13:00"]}, min_gap_minutes=0, daily_cap=2)
    r1, r2, r3 = render(), render(auto_approve=True), render()
    bad = [render(status="FAILED"), render(duration_s=settings.ZERNIO_MAX_REEL_SECONDS + 30.0), 10**6]
    r = auto(client, aid, [r3, bad[0], r1, bad[1], r2, bad[2]])
    assert r.status_code == 200, r.text
    assert placed_times(r) == {r3: NOW + 65 * M, r1: NOW + 5 * H, r2: NOW + D + 60 * M}  # input order, cap 2/day
    assert [p["post"]["status"] for p in r.json()["placed"]] == ["DRAFT", "DRAFT", "SCHEDULED"]
    assert [(u["render_id"], u["reason"]) for u in r.json()["unplaced"]] == [
        (bad[0], "render is failed, not ready"), (bad[1], f"render longer than {settings.ZERNIO_MAX_REEL_SECONDS} s"), (bad[2], "render not found")]  # fmt: skip


def test_auto_schedule_skips_taken_and_reuses_cancelled(client):
    aid = account(posting_slots={"times": ["09:00", "13:00"]})
    manual = create(client, render(), aid, NOW + H).json()  # 09:00 taken by hand
    first = auto(client, aid, [render()])
    assert list(placed_times(first).values()) == [NOW + 5 * H]
    client.post(f"/api/posts/{manual['id']}/cancel")
    assert list(placed_times(auto(client, aid, [render()])).values()) == [NOW + H]  # cancelled frees the slot


def test_auto_schedule_horizon_and_no_slots(client):
    aid = account(posting_slots={"times": ["09:00"]}, daily_cap=1)
    r = auto(client, aid, [render() for _ in range(31)])
    assert len(r.json()["placed"]) == 30  # 01-07 09:00 .. 02-05 09:00, then past 30 days
    assert r.json()["unplaced"][0]["reason"] == "no free slot within 30 days"
    r = auto(client, account(posting_slots={"times": []}), [render()])
    assert r.json()["unplaced"][0]["reason"] == "account has no posting slots"


def test_auto_schedule_guards(client):
    aid, ok = account(), render()
    assert len(auto(client, aid, [ok, render()], rights_override=False).json()["placed"]) == 2  # an old client's field
    assert code(auto(client, account(disabled_at=NOW), [ok])) == "ACCOUNT_UNAVAILABLE"
    assert auto(client, 10**6, [ok]).status_code == 404
    assert auto(client, aid, []).status_code == 422


def test_auto_schedule_race_does_not_double_book(client):
    """Two auto-schedules for one account at once: every placed post has its own slot and the cap holds."""
    aid = account(posting_slots={"times": ["09:00", "10:00", "11:00", "12:00", "13:00"]}, daily_cap=3)
    batches = [[render() for _ in range(4)] for _ in range(2)]

    async def race():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t", headers=as_user()) as c:
            return await asyncio.gather(*(c.post("/api/posts/auto-schedule", json={"account_id": aid, "render_ids": b})
                                          for b in batches))  # fmt: skip

    results = client.portal.call(race)
    times = [t for r in results for t in placed_times(r).values()]
    assert len(times) == 8 and len(set(times)) == 8
    per_day = {}
    for t in times:
        per_day[t.date()] = per_day.get(t.date(), 0) + 1
    assert max(per_day.values()) == 3


# ---------------------------------------------------------------- sync (the upsert behind POST /api/accounts/sync)


def test_sync_upsert(client, monkeypatch):
    sent = []

    async def fake_notify(uid, text, link=None, buttons=None):
        sent.append(text)
        return True

    monkeypatch.setattr(account_sync, "notify", fake_notify)
    zid = uuid.uuid4().hex
    row = {"zernio_account_id": zid, "zernio_profile_id": "p1", "username": "new.one", "avatar_url": None,
           "connection_status": "connected"}  # fmt: skip

    async def upsert(*rows):
        async with SessionLocal() as s:
            await account_sync.upsert(s, 1, list(rows))

    client.portal.call(upsert, row)
    a = next(x for x in client.get("/api/accounts").json() if x["zernio_account_id"] == zid)
    assert (a["timezone"], a["posting_slots"]["times"], a["daily_cap"]) == ("Europe/London", [f"{h:02d}:00" for h in range(7, 24)], 10)
    client.patch(f"/api/accounts/{a['id']}", json={"daily_cap": 4, "timezone": "UTC", "posting_slots": {"times": ["07:00"]}})
    client.portal.call(upsert, row | {"username": "renamed", "connection_status": "disconnected"})
    client.portal.call(upsert, row | {"connection_status": "connected"})
    client.portal.call(upsert)  # gone from Zernio: disconnected, but the alert was sent < 6 h ago
    a = client.get("/api/accounts").json()
    a = next(x for x in a if x["zernio_account_id"] == zid)
    assert (a["daily_cap"], a["timezone"], a["posting_slots"]["times"]) == (4, "UTC", ["07:00"])  # operator's
    assert (a["username"], a["connection_status"]) == ("new.one", "disconnected")
    assert len([t for t in sent if "renamed" in t]) == 1 and not [t for t in sent if "new.one" in t]
    with SyncSession() as s:
        acc = s.get(Account, a["id"])
        acc.last_alerts = {"ACCOUNT_DISCONNECTED": (datetime.now(UTC) - 7 * H).isoformat()}
        s.commit()
    client.portal.call(upsert, row)
    client.portal.call(upsert, row | {"connection_status": "disconnected"})
    assert len([t for t in sent if "new.one" in t]) == 1  # 6 h later: alerts again


# ---------------------------------------------------------------- review fixes


def key_of(post_id: int) -> str:
    with SyncSession() as s:
        return s.get(Post, post_id).idempotency_key


def test_same_instant_is_slot_taken(client):
    aid, cap1 = account(), account(daily_cap=1)
    a = create(client, render(), aid, NOW + D).json()
    r = create(client, render(), aid, NOW + D)
    assert (r.status_code, code(r)) == (409, "SLOT_TAKEN")
    assert create(client, render(), cap1, NOW + D).status_code == 201  # another account: fine
    b = create(client, render(), aid, NOW + D + H).json()
    r = client.patch(f"/api/posts/{b['id']}", json={"scheduled_for": iso(NOW + D)})
    assert (r.status_code, code(r)) == (409, "SLOT_TAKEN")
    assert client.patch(f"/api/posts/{a['id']}", json={"scheduled_for": iso(NOW + D)}).status_code == 200  # its own
    client.post(f"/api/posts/{a['id']}/cancel")
    assert client.patch(f"/api/posts/{b['id']}", json={"scheduled_for": iso(NOW + D)}).status_code == 200


def test_parallel_creates_at_one_instant(client):
    aid, rids = account(), [render(), render()]

    async def race():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", headers=as_user()) as c:
            return await asyncio.gather(*(c.post("/api/posts", json={
                "render_id": rid, "account_id": aid, "scheduled_for": iso(NOW + D)}) for rid in rids))  # fmt: skip

    assert sorted(r.status_code for r in client.portal.call(race)) == [201, 409]


def test_render_placed_again_on_the_slot_it_was_moved_from(client):
    aid, rid = account(posting_slots={"times": ["09:00"]}), render()
    moved = create(client, rid, aid, NOW + H).json()  # 09:00, the first slot
    client.patch(f"/api/posts/{moved['id']}", json={"scheduled_for": iso(NOW + 3 * D)})
    r = create(client, rid, aid, NOW + H, repost=True)  # the vacated 09:00 slot; the moved post still holds that slot's key
    assert r.status_code == 201, r.text
    assert key_of(r.json()["id"]) != key_of(moved["id"]) == scheduling.idempotency_key(rid, aid, NOW + H)
    other = account()
    first = create(client, rid, other, NOW + D).json()
    client.patch(f"/api/posts/{first['id']}", json={"scheduled_for": iso(NOW + 2 * D)})
    r = create(client, rid, other, NOW + D, repost=True)  # not the moved post back at another time: a new post
    assert (r.status_code, datetime.fromisoformat(r.json()["scheduled_for"])) == (201, NOW + D)
    assert r.json()["id"] != first["id"] and key_of(r.json()["id"]) != key_of(first["id"])


def test_auto_schedule_places_a_repeated_render_once(client):
    aid, rid = account(), render()
    assert list(placed_times(auto(client, aid, [rid, rid, rid]))) == [rid]
    again = auto(client, aid, [rid, rid]).json()  # it is queued there now: never placed twice
    assert again["placed"] == [] and [u["render_id"] for u in again["unplaced"]] == [rid]


def test_past_draft_is_not_the_next_post(client, env):
    aid = account(username="zz-next")
    create(client, render(), aid, NOW + D)
    env["now"] = NOW + 3 * D
    a = next(x for x in client.get("/api/accounts").json() if x["id"] == aid)
    assert a["next_post_at"] is None


def test_unusable_accounts_get_no_slot_and_no_approval(client):
    for kw in ({"disabled_at": NOW}, {"connection_status": "disconnected"}):
        assert client.get(f"/api/accounts/{account(**kw)}/next-slot").json() == {"scheduled_for": None}
    aid = account()
    p = create(client, render(), aid, NOW + D).json()
    with SyncSession() as s:
        s.execute(update(Account).where(Account.id == aid).values(connection_status="disconnected"))
        s.commit()
    r = client.post(f"/api/posts/{p['id']}/approve")
    assert (r.status_code, code(r)) == (422, "ACCOUNT_UNAVAILABLE")


def test_disable_cancels_failed_posts_too(client):
    aid = account()
    ids = [create(client, render(), aid, NOW + D + i * H).json()["id"] for i in range(2)]
    set_post(ids[0], status="FAILED")
    set_post(ids[1], status="DEAD_LETTER")
    client.patch(f"/api/accounts/{aid}", json={"disabled": True})
    assert [client.get(f"/api/posts/{p}").json()["status"] for p in ids] == ["CANCELLED", "CANCELLED"]


def test_too_short_and_legacy_timezones(client):
    r = create(client, render(duration_s=2.9), account(), NOW + D)
    assert (r.status_code, code(r)) == (422, "TOO_SHORT")
    aid = account()
    for tz in ("Asia/Calcutta", "Europe/Kiev", "Asia/Katmandu", "Asia/Saigon", "Asia/Kolkata", "UTC"):
        r = client.patch(f"/api/accounts/{aid}", json={"timezone": tz})  # Chrome's Intl names (tzdata backward)
        assert (r.status_code, r.json().get("timezone")) == (200, tz), r.text


def test_status_counts(client):
    before = client.get("/api/status").json()
    render(status="PENDING")
    create(client, render(auto_approve=True), account(), NOW + D)
    after = client.get("/api/status").json()
    assert after["rendering_renders"] - before["rendering_renders"] == 1
    assert after["scheduled_posts"] - before["scheduled_posts"] == 1
    assert after["publishing_enabled"] is False  # user 1 has no Zernio key here


def test_status_tells_the_publisher_and_the_media_worker_apart(client, db):
    """procrastinate_workers has no queue: a live worker that has run a default-queue job is the publisher, any other
    live one the media worker. Either down is its own sidebar line (App.tsx, the bot's /status)."""

    def alive():
        s = client.get("/api/status").json()
        return s["worker_alive"], s["publisher_alive"]

    add = text("INSERT INTO procrastinate_workers DEFAULT VALUES RETURNING id")
    stale = text("UPDATE procrastinate_workers SET last_heartbeat = now() - interval '1 minute' WHERE id = :w")
    with db.begin() as c:
        c.execute(text("DELETE FROM procrastinate_workers"))  # none left over from other tests
        media, pub = c.execute(add).scalar(), c.execute(add).scalar()
        c.execute(text("INSERT INTO procrastinate_jobs (queue_name, task_name, status, worker_id)"
                       " VALUES ('default', 'dispatch', 'succeeded', :w)"), {"w": pub})  # fmt: skip
    try:
        assert alive() == (True, True)
        with db.begin() as c:
            c.execute(stale, {"w": pub})
        assert alive() == (True, False)  # dispatch, publishing and alerts stopped: the sidebar must not say live
        with db.begin() as c:
            c.execute(text("UPDATE procrastinate_workers SET last_heartbeat = now() WHERE id = :w"), {"w": pub})
            c.execute(stale, {"w": media})
        assert alive() == (False, True)
    finally:
        with db.begin() as c:
            c.execute(text("DELETE FROM procrastinate_jobs WHERE worker_id = :w"), {"w": pub})
            c.execute(text("DELETE FROM procrastinate_workers"))


def test_status_publishing_enabled(client, monkeypatch):
    """On only with the server's switch and the user's own valid key; publishing_off says which one it is."""

    def status():
        s = client.get("/api/status").json()
        return s["publishing_enabled"], s["publishing_off"]

    monkeypatch.setattr(settings, "PUBLISHING_ENABLED", True)
    assert status() == (False, "no_key")
    zernio_key(1, "sk_test", status="invalid")
    assert status() == (False, "key_invalid")
    zernio_key(1, "sk_test")
    assert status() == (True, None)
    monkeypatch.setattr(settings, "PUBLISHING_ENABLED", False)
    assert status() == (False, "switch")
    zernio_key(1, None)


def test_idempotency_salt_keeps_productions_keys_and_separates_stagings(monkeypatch):
    at = datetime(2026, 10, 1, 17, tzinfo=UTC)
    plain = hashlib.sha256(b"640:3:2026-10-01T17:00:00+00:00").hexdigest()
    assert scheduling.idempotency_key(640, 3, at) == plain  # no salt: every key production already holds stays valid
    monkeypatch.setattr(scheduling.settings, "IDEMPOTENCY_SALT", "staging:")
    assert scheduling.idempotency_key(640, 3, at) not in (plain, scheduling.idempotency_key(640, 3, at, 1))
