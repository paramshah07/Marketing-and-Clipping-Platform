"""Tenant isolation, with the api on its production role (clipper_app: row-level security applies; the rest of
the suite runs the api as the superuser). User A is the operator (user 1, the bot service's path), user B signs
up through the api (the cookie path). B gets a 404 for every A id in every route family and sees none of A's
rows in any list; defaults and counts are per user; and the database itself refuses a row with no user or a
reference to another user's row."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app import main
from app.api import auth
from app.cli import db_grants
from app.core.config import settings
from app.core.db import SessionLocal, engine
from app.main import app
from app.models import Account, Brand, Post, Render, SavedCaption, SavedCover, SourceClip
from app.tasks import accounts as account_sync
from conftest import TEST_URL, as_user

APP_URL = TEST_URL.replace("://clipper:clipper@", f"://clipper_app:{settings.APP_DB_PASSWORD}@", 1)
A = as_user(1)
OWNED = {"source_clips", "brands", "saved_captions", "saved_covers", "renders", "accounts", "posts", "telegram_bots"}
JPEG = b"\xff\xd8\xff\xe0" + bytes(100)
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def client(db, tmp_path_factory):
    """The api with clipper_app sessions; its default headers are B's (a cookie, this site's Origin)."""
    assert "://clipper:clipper@" in TEST_URL
    db_grants()  # test_db's downgrade/upgrade dropped them
    app_engine = create_async_engine(APP_URL)
    with pytest.MonkeyPatch.context() as mp:
        for module in (auth, main):
            mp.setattr(module, "SessionLocal", async_sessionmaker(app_engine, expire_on_commit=False))
        mp.setattr(settings, "DATA_DIR", tmp_path_factory.mktemp("data"))
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
