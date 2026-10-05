"""Tenant isolation, with the api on its production role (clipper_app: row-level security applies; the rest of
the suite runs the api as the superuser). User A is the operator (user 1, the bot service's path), user B signs
up through the api (the cookie path). B gets a 404 for every A id in every route family and sees none of A's
rows in any list; defaults and counts are per user; and the database itself refuses a row with no user or a
reference to another user's row."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app import main
from app.api import auth, bots
from app.api.auth import _sha
from app.bot.clients import Clipper, Telegram
from app.bot.core import Bot
from app.cli import db_grants
from app.core.config import settings
from app.core.db import SessionLocal, engine
from app.main import app
from app.core.secrets import seal
from app.models import Account, Brand, Post, Render, SavedCaption, SavedCover, SourceClip, TelegramBot, User
from app.services import notify, zernio
from app.tasks import accounts as account_sync
from conftest import TEST_URL, as_user
from test_bot import FakeTelegram, Phone

APP_URL = TEST_URL.replace("://clipper:clipper@", f"://clipper_app:{settings.APP_DB_PASSWORD}@", 1)
A = as_user(1)
OWNED = {"source_clips", "brands", "saved_captions", "saved_covers", "renders", "accounts", "posts", "telegram_bots"}
JPEG = b"\xff\xd8\xff\xe0" + bytes(100)
PNG = b"\x89PNG\r\n\x1a\n"
PNG_RGBA = PNG + b"\0\0\0\rIHDR" + bytes(8) + b"\x08\x06" + bytes(7) + b"\0\0\0\0IDAT\0\0\0\0\0\0\0\0IEND\0\0\0\0"


@pytest.fixture(scope="module")
def client(db, tmp_path_factory):
    """The api with clipper_app sessions; its default headers are B's (a cookie, this site's Origin)."""
    assert "://clipper:clipper@" in TEST_URL
    db_grants()  # test_db's downgrade/upgrade dropped them
    app_engine = create_async_engine(APP_URL)
    with pytest.MonkeyPatch.context() as mp:
        for module in (auth, main, bots, notify):
            mp.setattr(module, "SessionLocal", async_sessionmaker(app_engine, expire_on_commit=False))
        mp.setattr(settings, "DATA_DIR", tmp_path_factory.mktemp("data"))
        media = next(r for r in app.routes if getattr(r, "name", None) == "media").app
        mp.setattr(media, "all_directories", [settings.DATA_DIR])  # /media serves this module's files
        mp.setattr(settings, "ZERNIO_API_KEY", "")  # quota -> None: never a Zernio call
        mp.setattr(settings, "APP_BASE_URL", "http://localhost:5173")
        mp.setattr(settings, "MAX_USERS", 1000)
        mp.setattr(auth, "BCRYPT_COST", 4)
        with TestClient(app, headers={"Origin": "http://localhost:5173"}) as c:
            r = c.post("/api/auth/signup", json={"username": f"b.{uuid.uuid4().hex[:8]}", "password": "b password 1"})
            assert r.status_code == 201, r.text
            c.b = r.json()["id"]
            yield c
            c.portal.call(app_engine.dispose)
            c.portal.call(engine.dispose)


@pytest.fixture(scope="module")
def a(db, client) -> dict:
    """One of everything of A's: a READY clip, a brand with a logo, a caption, a cover, a READY render, a
    connected account and a SCHEDULED post (rows by the superuser, which lands on user 1)."""
    with Session(db) as s:
        clip = SourceClip(origin="url", status="READY", source_url="https://www.tiktok.com/@a/video/7000000000000000001",
                          raw_key="raw/a.mp4", width=1080, height=1920, duration_s=20.0)  # fmt: skip
        brand = Brand(name="A brand", logo_key="logos/a.png")
        caption = SavedCaption(name="A caption", text="#a")
        cover = SavedCover(name="A cover", image_key="cover-library/a.jpg")
        acc = Account(zernio_account_id=uuid.uuid4().hex, zernio_profile_id="p", username="a_acct", timezone="UTC",
                      posting_slots={"times": ["09:00", "13:00"]})  # fmt: skip
        s.add_all([clip, brand, caption, cover, acc])
        s.flush()
        render = Render(source_clip_id=clip.id, brand_id=brand.id, status="READY", duration_s=20.0)
        s.add(render)
        s.flush()
        post = Post(render_id=render.id, account_id=acc.id, caption="a", status="SCHEDULED",
                    scheduled_for=datetime.now(UTC) + timedelta(days=2), idempotency_key=uuid.uuid4().hex)  # fmt: skip
        s.add(post)
        s.commit()
        ids = {"clip": clip.id, "brand": brand.id, "caption": caption.id, "cover": cover.id, "account": acc.id,
               "render": render.id, "post": post.id}  # fmt: skip
    assert {x["id"] for x in client.get("/api/clips", headers=A).json()} >= {ids["clip"]}  # A sees them
    return ids


def owner(db, model, id: int) -> int | None:
    with Session(db) as s:
        row = s.get(model, id)
        return row and row.user_id


def test_every_user_id_table_has_row_level_security(db):
    with db.connect() as c:
        rows = c.execute(text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, p.polname, pg_get_expr(p.polqual, p.polrelid),"
            " p.polwithcheck IS NULL, p.polcmd"
            " FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'user_id' AND NOT a.attisdropped"
            " LEFT JOIN pg_policy p ON p.polrelid = c.oid"
            " WHERE c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace AND c.relname <> 'sessions'"
        )).all()  # fmt: skip
        role = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'clipper_app'")).one()
    # sessions is the one exception by design: only app/api/auth.py reads it, by token
    assert {r[0] for r in rows} == OWNED and len(rows) == len(OWNED)  # a new user_id table fails here until it has RLS
    for name, enabled, forced, policy, qual, check_is_using, cmd in rows:
        assert (enabled, forced, policy, cmd, check_is_using) == (True, True, "tenant", "*", True), name
        assert qual == "(user_id = (NULLIF(current_setting('app.uid'::text, true), ''::text))::integer)", name
    assert tuple(role) == (False, False)


def test_security_definer_functions_are_hardened(db):
    """The functions that step outside row-level security (the upload sweep, the bot supervisor's list and report):
    pg_temp last on their search_path, and no EXECUTE for PUBLIC, only for clipper_app (db-grants) and their owner."""
    db_grants()  # test_db's downgrade/upgrade dropped them
    with db.connect() as c:
        rows = c.execute(text(
            "SELECT proname, proconfig, proacl::text[] FROM pg_proc"
            " WHERE prosecdef AND pronamespace = 'public'::regnamespace"
        )).all()  # fmt: skip
    assert {r[0] for r in rows} == {"abandon_orphan_uploads", "bots_for_supervisor", "report_bots"}
    for name, config, acl in rows:
        assert config == ["search_path=public, pg_temp"], name
        assert not any(x.startswith("=") for x in acl) and any(x.startswith("clipper_app=X/") for x in acl), (name, acl)


def test_no_uid_sees_nothing_and_writes_nothing(db, a):
    """The api's role with no app.uid (a forgotten session.info), or another user's: fail closed."""
    app_db = create_engine(APP_URL)
    try:
        with app_db.connect() as c:
            assert c.execute(text("SELECT count(*) FROM brands")).scalar() == 0
            assert c.execute(text("UPDATE brands SET name = 'x' WHERE id = :id"), {"id": a["brand"]}).rowcount == 0
            with pytest.raises(ProgrammingError, match="row-level security"):  # user_id defaults to 1, the check refuses
                c.execute(text("INSERT INTO brands (name) VALUES ('no uid')"))
            c.rollback()
            c.execute(text("SELECT set_config('app.uid', '999999', true)"))
            with pytest.raises(ProgrammingError, match="row-level security"):
                c.execute(text("INSERT INTO brands (name, user_id) VALUES ('forged', 1)"))
            c.rollback()
            with pytest.raises(ProgrammingError, match="permission denied"):  # and never the migrations table
                c.execute(text("DELETE FROM alembic_version"))
    finally:
        app_db.dispose()
    assert owner(db, Brand, a["brand"]) == 1


def test_composite_fks_refuse_another_users_rows(db, client, a):
    """Even the superuser (worker, CLI) can't point a row at another user's."""
    with Session(db) as s:
        for row, constraint in [
            (Render(source_clip_id=a["clip"], user_id=client.b), "fk_renders_source_clip_id_source_clips"),
            (Post(render_id=a["render"], account_id=a["account"], user_id=client.b, caption="", idempotency_key="x",
                  scheduled_for=datetime.now(UTC)), "fk_posts_(render|account)_id"),  # fmt: skip
        ]:
            s.add(row)
            with pytest.raises(IntegrityError, match=constraint):
                s.flush()
            s.rollback()


def test_every_route_family_is_404_for_another_users_ids(db, client, a):
    png = {"file": ("l.png", PNG)}
    for method, path, kw in [
        ("GET", f"/api/clips/{a['clip']}", {}),
        ("PATCH", f"/api/clips/{a['clip']}", {"json": {"source_creator_handle": "@b"}}),
        ("POST", f"/api/clips/{a['clip']}/retry", {}),
        ("DELETE", f"/api/clips/{a['clip']}", {}),
        ("PATCH", f"/api/brands/{a['brand']}", {"json": {"name": "stolen", "is_default": True}}),
        ("POST", f"/api/brands/{a['brand']}/logo", {"files": png}),
        ("GET", f"/api/renders/{a['render']}", {}),
        ("POST", f"/api/renders/{a['render']}/retry", {}),
        ("DELETE", f"/api/renders/{a['render']}", {}),
        ("PUT", f"/api/renders/{a['render']}/cover", {"files": {"file": ("c.jpg", JPEG)}}),
        ("DELETE", f"/api/renders/{a['render']}/cover", {}),
        ("PATCH", f"/api/captions/{a['caption']}", {"json": {"text": "stolen"}}),
        ("DELETE", f"/api/captions/{a['caption']}", {}),
        ("PATCH", f"/api/covers/{a['cover']}", {"json": {"name": "stolen"}}),
        ("DELETE", f"/api/covers/{a['cover']}", {}),
        ("PATCH", f"/api/accounts/{a['account']}", {"json": {"disabled": True}}),
        ("GET", f"/api/accounts/{a['account']}/next-slot", {}),
        ("GET", f"/api/posts/{a['post']}", {}),
        ("PATCH", f"/api/posts/{a['post']}", {"json": {"caption": "stolen"}}),
        ("POST", f"/api/posts/{a['post']}/approve", {}),
        ("POST", f"/api/posts/{a['post']}/cancel", {}),
        ("POST", f"/api/posts/{a['post']}/remedy", {"json": {}}),
        ("POST", "/api/posts", {"json": {"render_id": a["render"], "account_id": a["account"],
                                         "scheduled_for": (datetime.now(UTC) + timedelta(days=3)).isoformat()}}),
        ("POST", "/api/posts/auto-schedule", {"json": {"account_id": a["account"], "render_ids": [a["render"]]}}),
    ]:  # fmt: skip
        r = client.request(method, path, **kw)
        assert r.status_code == 404, (method, path, r.status_code, r.text)
    # creating from A's clip or with A's brand: the same answer as a missing one
    assert client.post("/api/renders", json={"clip_id": a["clip"]}).status_code == 409
    with Session(db) as s:
        mine = SourceClip(origin="upload", status="READY", user_id=client.b)
        s.add(mine)
        s.commit()
        mine = mine.id
    r = client.post("/api/renders", json={"clip_id": mine, "brand_id": a["brand"]})
    assert (r.status_code, r.json()["detail"]) == (409, f"brand {a['brand']} is missing, archived or has no logo")
    # nothing of A's changed
    with Session(db) as s:
        assert s.get(Brand, a["brand"]).name == "A brand" and s.get(SavedCaption, a["caption"]).text == "#a"
        assert s.get(Post, a["post"]).status == "SCHEDULED" and s.get(Account, a["account"]).disabled_at is None
        assert s.get(SourceClip, a["clip"]).source_creator_handle is None and s.get(Render, a["render"]) is not None


def test_lists_counts_and_new_rows_are_per_user(db, client, a):
    brand = client.post("/api/brands", json={"name": "B brand"}).json()
    assert owner(db, Brand, brand["id"]) == client.b  # the insert took app.uid
    for path, key in [("/api/clips", "clip"), ("/api/brands", "brand"), ("/api/captions", "caption"),
                      ("/api/covers", "cover"), (f"/api/renders?clip_id={a['clip']}", "render"), ("/api/accounts", "account"),
                      ("/api/posts", "post")]:  # fmt: skip
        assert a[key] in {x["id"] for x in client.get(path, headers=A).json()}, path
        assert a[key] not in {x["id"] for x in client.get(path).json()}, path
    assert [x["id"] for x in client.get("/api/brands").json()] == [brand["id"]]
    assert client.get("/api/brands?archived=true").json() == []
    assert brand["id"] not in {x["id"] for x in client.get("/api/brands", headers=A).json()}
    b, a_status = client.get("/api/status").json(), client.get("/api/status", headers=A).json()
    assert (b["scheduled_posts"], b["failed_posts"]) == (0, 0) and a_status["scheduled_posts"] >= 1
    assert client.get("/api/me").json()["id"] == client.b
    # a link A already imported is not "in B's library", and importing it makes B's own clip
    url = "https://www.tiktok.com/@a/video/7000000000000000001"
    found = client.post("/api/clips/links", files={"file": ("l.txt", url.encode())}).json()["links"]
    assert found == [{"url": url, "platform": "TikTok", "in_library": False}]
    [new] = client.post("/api/clips/from-urls", json={"urls": [url]}).json()["ids"]  # defers a job as clipper_app
    assert owner(db, SourceClip, new) == client.b


def test_defaults_are_per_user(client, a):
    for path, body in [("/api/captions", {"name": "d", "text": "", "is_default": True}),
                       ("/api/brands", {"name": "d", "is_default": True})]:  # fmt: skip
        mine = client.post(path, json=body, headers=A).json()["id"]
        theirs = client.post(path, json=body).json()["id"]
        again = client.post(path, json=body).json()["id"]  # B's new default replaces B's old one only
        assert [x["id"] for x in client.get(path, headers=A).json() if x["is_default"]] == [mine], path
        assert [x["id"] for x in client.get(path).json() if x["is_default"]] == [again] and theirs != again, path
    r = client.post("/api/covers", files={"file": ("c.jpg", JPEG)}, data={"name": "d", "is_default": "true"})
    mine = client.post("/api/covers", files={"file": ("c.jpg", JPEG)}, data={"name": "d", "is_default": "true"},
                       headers=A).json()["id"]  # fmt: skip
    assert [x["id"] for x in client.get("/api/covers").json() if x["is_default"]] == [r.json()["id"]]
    assert [x["id"] for x in client.get("/api/covers", headers=A).json() if x["is_default"]] == [mine]


def test_account_sync_touches_only_its_user(db, client, a):
    """The worker's upsert runs as the superuser: an empty list for B must not disconnect A's accounts."""

    async def sync_b():
        async with SessionLocal() as s:
            await account_sync.upsert(s, client.b, [])

    client.portal.call(sync_b)
    with Session(db) as s:
        assert s.get(Account, a["account"]).connection_status == "connected"


def test_startup_sweep_sees_every_users_uploads(db, client):
    with Session(db) as s:
        clips = [SourceClip(origin="upload", status="UPLOADING", user_id=u) for u in (1, client.b)]
        s.add_all(clips)
        s.commit()
        ids = [c.id for c in clips]
    client.portal.call(main._abandon_orphan_uploads)  # as clipper_app, whose own view is empty
    with Session(db) as s:
        assert [s.get(SourceClip, i).error_code for i in ids] == ["UPLOAD_ABANDONED"] * 2


def test_media_is_each_owners_only(client):
    """Critique B4: a file is its owner's (u/{id}/..., or user 1's for a legacy key with no prefix), judged on the path
    StaticFiles normalised (u/B/../1/x is u/1/x), so another user's file is a 404, the same as a missing one. Range
    (the <video> element) and ETag still work, and no shared cache may keep it."""
    b = client.b
    files = {"renders/legacy.mp4": A, "u/1/renders/new.mp4": A, f"u/{b}/renders/b.mp4": {}}  # key -> its owner's headers
    for key in files:
        (settings.DATA_DIR / key).parent.mkdir(parents=True, exist_ok=True)
        (settings.DATA_DIR / key).write_bytes(bytes(range(256)))
    for key, owner_headers in files.items():
        r = client.get(f"/media/{key}", headers=owner_headers)
        assert (r.status_code, r.headers["cache-control"], r.content[:3]) == (200, "private", b"\0\1\2"), key
        assert client.get(f"/media/{key}", headers=A if owner_headers == {} else {}).status_code == 404, key
        assert client.get(f"/media/{key}", headers={"If-None-Match": r.headers["etag"]} | owner_headers).status_code == 304
    r = client.get(f"/media/u/{b}/renders/b.mp4", headers={"Range": "bytes=0-9"})
    assert (r.status_code, r.content) == (206, bytes(range(10)))
    climbs = [f"u/{b}/%2e%2e/1/renders/new.mp4", f"u/{b}/%2e%2e/%2e%2e/renders/legacy.mp4"]
    for path in [*climbs, f"u/{b}/../1/renders/new.mp4", "u/abc/x.mp4", "u/%C2%B2/renders/b.mp4", "u/", f"u/{b}"]:
        assert client.get(f"/media/{path}").status_code == 404, path
    assert [client.get(f"/media/{p}", headers=A).status_code for p in climbs] == [200, 200]  # they are user 1's files


def test_new_files_go_under_their_owners_prefix(client):
    brand = client.post("/api/brands", json={"name": "B logo"}).json()
    logo = client.post(f"/api/brands/{brand['id']}/logo", files={"file": ("l.png", PNG_RGBA)}).json()["logo_url"]
    assert logo.startswith(f"/media/u/{client.b}/logos/{brand['id']}-")
    assert client.get(logo).status_code == 200 and client.get(logo, headers=A).status_code == 404


def test_a_key_listing_another_users_account_skips_it(db, client, a, monkeypatch):
    """Critique A6 under B's row-level security: A's account (another member of one Zernio team) is skipped and
    reported (INSERT ... ON CONFLICT DO NOTHING never sees A's row), B's own is added, and A's is untouched."""
    with Session(db) as s:
        theirs = s.get(Account, a["account"]).zernio_account_id
    mine = uuid.uuid4().hex
    fixtures = Path(__file__).parent / "fixtures" / "zernio"

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/auth/verify"):
            d = json.loads((fixtures / "live_auth_verify.json").read_text())
            d["body"]["userId"] = uuid.uuid4().hex
        else:
            d = json.loads((fixtures / "accounts.json").read_text())
            one = d["body"]["accounts"][0]
            d["body"]["accounts"] = [one | {"_id": theirs, "username": "a_acct"}, one | {"_id": mine, "username": "b.own"}]
        return httpx.Response(d["status"], json=d["body"])

    real = zernio.client
    monkeypatch.setattr(zernio, "client", lambda key, **kw: real(key, transport=httpx.MockTransport(handler), **kw))
    r = client.put("/api/me/zernio-key", json={"key": "sk_" + "c3" * 32})
    assert r.status_code == 200, r.text
    assert (r.json()["accounts"], r.json()["skipped"]) == (["b.own"], ["a_acct"])
    with Session(db) as s:
        assert s.scalar(select(Account.user_id).where(Account.zernio_account_id == mine)) == client.b
        assert (s.get(Account, a["account"]).user_id, s.get(Account, a["account"]).username) == (1, "a_acct")
    assert [x["username"] for x in client.get("/api/accounts").json()] == ["b.own"]
    assert client.delete("/api/me/zernio-key").status_code == 204


def test_storage_quota_and_disk_floor(db, client, monkeypatch):
    """New files need room: past the user's quota (users.quota_bytes, a new user's USER_QUOTA_BYTES) 507
    QUOTA_EXCEEDED; the disk under MIN_FREE_BYTES 507 DISK_FULL for everyone. User 1 has no quota."""
    assert client.get("/api/me").json()["storage"] == {"used_bytes": 0, "quota_bytes": settings.USER_QUOTA_BYTES}
    with Session(db) as s:
        s.add(SourceClip(user_id=client.b, origin="upload", status="READY", size_bytes=3000))
        s.execute(update(User).where(User.id == client.b).values(quota_bytes=2000))
        s.commit()
    assert client.get("/api/me").json()["storage"] == {"used_bytes": 3000, "quota_bytes": 2000}
    url = {"url": "https://www.tiktok.com/@b/video/7000000000000000009"}
    for r in [client.post("/api/clips/from-url", json=url), client.post("/api/clips/from-urls", json={"urls": [url["url"]]}),
              client.post("/api/clips", files={"file": ("x.mp4", b"0" * 100)}), client.post("/api/renders", json={"clip_id": 1})]:  # fmt: skip
        assert (r.status_code, r.json()["detail"]["code"]) == (507, "QUOTA_EXCEEDED"), r.text
    assert "storage is full" in r.json()["detail"]["message"]
    assert client.get("/api/me", headers=A).json()["storage"]["quota_bytes"] is None
    assert client.post("/api/clips/from-url", json=url, headers=A).status_code == 201
    monkeypatch.setattr(settings, "MIN_FREE_BYTES", 10**18)
    r = client.post("/api/clips/from-url", json=url, headers=A)
    assert (r.status_code, r.json()["detail"]["code"]) == (507, "DISK_FULL")
    with Session(db) as s:
        s.execute(update(User).where(User.id == client.b).values(quota_bytes=None))
        s.commit()


def test_bots_are_their_owners_only(db, client, a, monkeypatch):
    """Telegram under row-level security. A bot of B's (the bot service acting as B) finds none of A's rows, as B's
    browser doesn't; B sees and changes only B's bots; the supervisor's list and report (SECURITY DEFINER) span every
    user; a pairing code works for its bot's owner only; and each user's alerts reach only their own bots."""
    ta, tb = f"{uuid.uuid4().int % 10**9}:{'a' * 35}", f"{uuid.uuid4().int % 10**9}:{'b' * 35}"
    with Session(db) as s:
        mine = TelegramBot(user_id=1, bot_id=int(ta.split(":")[0]), token_enc=seal(ta), chat_id=1111)
        theirs = TelegramBot(user_id=client.b, bot_id=int(tb.split(":")[0]), token_enc=seal(tb),
                             pair_sha256=_sha("q" * 22), pair_expires_at=datetime.now(UTC) + timedelta(minutes=5))  # fmt: skip
        s.add_all([mine, theirs])
        s.commit()
        mine, theirs = mine.id, theirs.id
    service = {"Authorization": A["Authorization"]}
    listed = {x["id"]: x for x in client.get("/api/internal/bots", headers=service).json()}
    assert {mine: 1, theirs: client.b}.items() <= {i: x["user_id"] for i, x in listed.items()}.items()
    report = [{"id": i, "ver": listed[i]["ver"], "username": "n"} for i in (mine, theirs)]
    assert client.post("/api/internal/bots/report", json=report, headers=service).status_code == 204
    pair = f"/api/internal/bots/{theirs}/pair"
    assert client.post(pair, json={"code": "q" * 22, "chat_id": 2222}, headers=A).status_code == 404  # not A's bot
    assert client.post(pair, json={"code": "q" * 22, "chat_id": 2222}, headers=as_user(client.b)).status_code == 204
    with Session(db) as s:
        got = {b.id: (b.chat_id, b.username, b.last_seen_at is not None) for b in s.scalars(select(TelegramBot))}
    assert (got[mine], got[theirs]) == ((1111, "n", True), (2222, "n", True))
    assert [x["id"] for x in client.get("/api/me/bots").json()] == [theirs]
    assert [x["id"] for x in client.get("/api/me").json()["bots"]] == [theirs]
    assert client.patch(f"/api/me/bots/{mine}", json={"alerts": False}).status_code == 404
    assert client.delete(f"/api/me/bots/{mine}").status_code == 404

    async def as_b():  # the bot service's view of B, in the api's own event loop
        tg = FakeTelegram()
        bot = Bot(Telegram(tb, httpx.MockTransport(tg)), Clipper("http://api", httpx.ASGITransport(app=app), client.b), 4242, theirs)
        phone = Phone(bot, tg)
        await phone.say(f"/c{a['clip']}")
        assert phone.text() == f"Clip {a['clip']} not found."
        await phone.say(f"/p{a['post']}")
        assert phone.text() == f"Post {a['post']} not found."
        await phone.say("/clips")
        assert f"/c{a['clip']}\n" not in phone.text() + "\n" and not phone.text().endswith(f"/c{a['clip']}")

    client.portal.call(as_b)

    sent = []
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(
        lambda r: sent.append(str(r.url)) or httpx.Response(200, json={"ok": True})), **kw))  # fmt: skip
    assert client.portal.call(notify.notify, client.b, "for B") is True  # as clipper_app: B's bots only
    assert [u.split("/bot")[1].split("/")[0] for u in sent] == [tb]
    sent.clear()
    assert client.portal.call(notify.notify, 1, "for A") is True
    assert tb not in "".join(sent) and ta in "".join(sent)
    with Session(db) as s:
        s.execute(text("DELETE FROM telegram_bots WHERE id IN (:a, :b)"), {"a": mine, "b": theirs})
        s.commit()


def test_link_imports_are_known_video_sites_only(client):
    """yt-dlp runs inside the server's network: another user's link must be one video on a known site (the operator's
    may still be anything), or the server would fetch internal addresses for them and report what it found."""
    ok = "https://www.tiktok.com/@b/video/7000000000000000002"
    for path, body in (("/api/clips/from-url", {"url": "http://169.254.169.254/latest/meta-data/"}),
                       ("/api/clips/from-url", {"url": "https://x.com@169.254.169.254/status/1"}),
                       ("/api/clips/from-urls", {"urls": [ok, "http://api:8000/api/health"]})):  # fmt: skip
        r = client.post(path, json=body)
        assert r.status_code == 422 and "only a link to one video on YouTube" in r.json()["detail"], r.text
    assert client.post("/api/clips/from-url", json={"url": ok}).status_code == 201


def test_library_guards_and_free_up_space_are_per_user(db, client, a):
    """A video A has doesn't stop B importing it (B's second import does), and B's Free up space never sees A's
    published renders: the duplicate check and the freeing read only the signed-in user's rows."""
    url = "https://www.tiktok.com/@a/video/7000000000000000003"
    assert client.post("/api/clips/from-url", json={"url": url}, headers=A).status_code == 201
    assert client.post("/api/clips/from-url", json={"url": url}).status_code == 201  # B's own clip of it
    r = client.post("/api/clips/from-url", json={"url": url + "?_t=again"})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "ALREADY_IN_LIBRARY")
    with Session(db) as s:  # an A render that went out, MP4 still there
        r = Render(source_clip_id=a["clip"], status="READY", output_key="renders/a-out.mp4", size_bytes=10)
        s.add(r)
        s.flush()
        s.add(Post(render_id=r.id, account_id=a["account"], caption="", status="PUBLISHED", scheduled_for=datetime.now(UTC),
                   idempotency_key=uuid.uuid4().hex))  # fmt: skip
        s.commit()
    assert client.post("/api/renders/free-published?dry_run=true").json() == {"renders": 0, "bytes": 0}
    assert client.post("/api/renders/free-published?dry_run=true", headers=A).json()["renders"] >= 1
