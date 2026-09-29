"""Sign-up, sign-in, sessions, the CSRF check, throttles, the bot service's path, bootstrap and the user CLI.
The browser's path: a cookie, and Origin = APP_BASE_URL on every unsafe request. Passwords are throwaway ones
made here; hashes use bcrypt cost 4 (fast) except where the cost itself is checked."""

import asyncio
import base64
import io
import sys
from datetime import UTC, datetime, timedelta

import bcrypt
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import cli
from app.api import auth
from app.core.config import settings
from app.core.db import SyncSession, engine
from app.main import app
from app.models import AuthSession, User
from conftest import as_user

ORIGIN = {"Origin": "http://localhost:5173"}  # APP_BASE_URL's default: what Vite forwards in dev
PW = "correct horse battery"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(auth, "BCRYPT_COST", 4)
    monkeypatch.setattr(settings, "APP_BASE_URL", "http://localhost:5173")
    monkeypatch.setattr(settings, "MAX_USERS", 1000)
    auth.LOGIN_FAILS.hits.clear()
    auth.SIGNUPS.hits.clear()
    yield
    auth.LOGIN_FAILS.hits.clear()
    auth.SIGNUPS.hits.clear()


@pytest.fixture(scope="module")
def client(db):
    with TestClient(app) as c:
        yield c
        c.portal.call(engine.dispose)


@pytest.fixture
def browser(client):
    """A fresh browser: no cookies, this site's Origin on every request."""
    client.cookies.clear()
    client.headers.update(ORIGIN)
    yield client
    client.cookies.clear()
    client.headers.pop("Origin", None)


def signup(c, username, password=PW, **kw):
    return c.post("/api/auth/signup", json={"username": username, "password": password}, **kw)


def login(c, username, password=PW, **kw):
    return c.post("/api/auth/login", json={"username": username, "password": password}, **kw)


def code(r) -> str:
    return r.json()["detail"]["code"]


def active() -> int:
    with SyncSession() as s:
        return len(s.scalars(select(User.id).where(User.disabled_at.is_(None))).all())


def test_new_hashes_cost_12_and_any_bcrypt_verifies(monkeypatch):
    monkeypatch.setattr(auth, "BCRYPT_COST", 12)  # the real one
    h = auth.hash_password("throwaway-1")
    assert h.startswith("$2b$12$") and auth.verify_password("throwaway-1", h)
    for prefix in ("$2a$", "$2y$"):  # Caddy writes $2a$; PHP-style $2y$ is the same algorithm
        assert auth.verify_password("throwaway-1", prefix + h[4:])
    assert not auth.verify_password("throwaway-2", h) and not auth.verify_password("x", "not a hash")
    long = "é" * 100  # 200 bytes: bcrypt reads 72, and bcrypt 5 raises past them
    assert auth.verify_password(long, auth.hash_password(long))


def test_signup_login_logout(browser):
    r = browser.get("/api/auth/signup-status")
    assert r.json() == {"open": True, "remaining": 1000 - active()}
    assert code(signup(browser, "A")) == "USERNAME_INVALID"
    assert code(signup(browser, "-bad")) == "USERNAME_INVALID"
    assert code(signup(browser, "alice.t", "short")) == "PASSWORD_TOO_SHORT"
    assert code(signup(browser, "alice.t", "x" * 129)) == "PASSWORD_TOO_LONG"
    r = signup(browser, "  Alice.T ")
    assert r.status_code == 201, r.text
    assert r.json()["username"] == "alice.t"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("clipper_session=") and "HttpOnly" in cookie and "Max-Age=2592000" in cookie
    assert "SameSite=lax" in cookie and "Path=/" in cookie and "Secure" not in cookie  # http APP_BASE_URL
    me = browser.get("/api/me").json()
    assert (me["username"], me["setup"], me["zernio"]["status"], me["bots"]) == (
        "alice.t", {"zernio": False, "instagram": False, "telegram": False}, "none", [])  # fmt: skip
    with SyncSession() as s:
        assert s.get(User, me["id"]).quota_bytes == settings.USER_QUOTA_BYTES
    assert (r := signup(browser, "ALICE.T")).status_code == 409 and code(r) == "USERNAME_TAKEN"
    assert browser.get("/api/clips").status_code == 200
    assert browser.post("/api/auth/logout").status_code == 204
    r = browser.get("/api/clips")
    assert (r.status_code, code(r)) == (401, "NOT_SIGNED_IN")
    r = login(browser, "ALICE.T")  # case-insensitive
    assert r.status_code == 200 and r.json() == {"id": me["id"], "username": "alice.t"}
    assert browser.get("/api/me").json()["id"] == me["id"]
    for name, pw in (("alice.t", "wrong password"), ("nobody.here", PW)):  # same answer: no username oracle
        r = login(browser, name, pw)
        assert (r.status_code, code(r)) == (401, "INVALID_LOGIN")


def test_everything_but_health_and_auth_needs_a_user(client):
    client.cookies.clear()
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/signup-status").status_code == 200
    for method, path in [("GET", "/api/status"), ("GET", "/api/me"), ("GET", "/api/clips"), ("GET", "/api/brands"),
                         ("GET", "/api/renders"), ("GET", "/api/captions"), ("GET", "/api/covers"),
                         ("GET", "/api/accounts"), ("GET", "/api/posts"), ("GET", "/api/posts/1"),
                         ("POST", "/api/brands"), ("POST", "/api/me/password"), ("GET", "/media/raw/1.mp4")]:  # fmt: skip
        r = client.request(method, path, headers=ORIGIN, json={})
        assert (r.status_code, code(r)) == (401, "NOT_SIGNED_IN"), path
    assert client.get("/media/raw/nothing.mp4", headers=as_user()).status_code == 404  # signed in: just missing


def test_csrf(browser):
    signup(browser, "csrf.user")
    del browser.headers["Origin"]  # each request below says its own
    brand = {"name": "CSRF"}
    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}, {}):
        r = browser.post("/api/brands", json=brand, headers=headers)
        assert (r.status_code, code(r)) == (403, "CROSS_SITE"), headers
    assert browser.get("/api/brands").status_code == 200  # safe methods pass
    assert browser.post("/api/brands", json=brand, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 201
    assert browser.post("/api/brands", json=brand, headers=ORIGIN).status_code == 201
    assert code(login(browser, "csrf.user", headers={"Origin": "https://evil.example"})) == "CROSS_SITE"
    browser.cookies.clear()
    assert browser.post("/api/brands", json=brand, headers=as_user()).status_code == 201  # the bot: bearer, no Origin


def test_bot_service_path(client, monkeypatch):
    client.cookies.clear()
    assert client.get("/api/me", headers=as_user()).json()["id"] == 1
    for headers in (
        {"Authorization": "Bearer wrong", "X-Clipper-User": "1"},
        {"Authorization": "Bearer test-bot-secret"},  # the bearer alone names no one
        {"Authorization": "Bearer test-bot-secret", "X-Clipper-User": "abc"},
        {"Authorization": "Bearer test-bot-secret", "X-Clipper-User": "99999999999"},  # past int4: 401, not 422
        {"Authorization": "Bearer test-bot-secret", "X-Clipper-User": "999999"},  # no such user
        {"X-Clipper-User": "1"},  # the header alone is nothing (Caddy strips it anyway)
    ):
        assert client.get("/api/me", headers=headers).status_code == 401, headers
    monkeypatch.setattr(settings, "BOT_SERVICE_SECRET", "")  # blank: the path is off
    r = client.post("/api/brands", json={"name": "x"}, headers={"Authorization": "Bearer ", "X-Clipper-User": "1"})
    assert r.status_code == 403


def test_basic_auth_header_falls_back_to_the_cookie(browser):
    """Caddy's basic_auth forwards Authorization: Basic; the cookie must still count (not a 401)."""
    signup(browser, "basic.user")
    basic = {"Authorization": "Basic " + base64.b64encode(b"clipper:x").decode()}
    assert browser.get("/api/me", headers=basic).json()["username"] == "basic.user"
    assert browser.post("/api/brands", json={"name": "b"}, headers=basic).status_code == 201


def test_session_expiry_and_sliding(browser):
    uid = signup(browser, "slide.user").json()["id"]

    def expires() -> datetime:
        with SyncSession() as s:
            return s.scalar(select(AuthSession.expires_at).where(AuthSession.user_id == uid))

    def set_expires(t: datetime) -> None:
        with SyncSession() as s:
            s.execute(update(AuthSession).where(AuthSession.user_id == uid).values(expires_at=t))
            s.commit()

    now = datetime.now(UTC)
    assert abs(expires() - (now + timedelta(days=30))) < timedelta(minutes=1)
    r = browser.get("/api/me")  # 30 days left: nothing to extend
    assert "set-cookie" not in r.headers
    set_expires(now + timedelta(days=10))
    assert browser.post("/api/brands", json={"name": "s"}).status_code == 201 and expires() < now + timedelta(days=11)
    r = browser.get("/api/me")  # a GET re-sets the cookie with the extension
    assert "clipper_session=" in r.headers["set-cookie"] and expires() > now + timedelta(days=29)
    set_expires(now - timedelta(seconds=1))
    assert browser.get("/api/me").status_code == 401


def test_change_password(browser):
    signup(browser, "pw.user")
    other = {"Cookie": f"clipper_session={browser.cookies['clipper_session']}"}  # the signup's: another browser now
    assert login(browser, "pw.user").status_code == 200  # this browser's session
    assert browser.get("/api/me", headers=other).status_code == 200
    r = browser.post("/api/me/password", json={"current": "wrong one!", "new": "new password 1"})
    assert (r.status_code, code(r)) == (403, "WRONG_PASSWORD")
    assert code(browser.post("/api/me/password", json={"current": PW, "new": "short"})) == "PASSWORD_TOO_SHORT"
    assert browser.post("/api/me/password", json={"current": PW, "new": "new password 1"}).status_code == 204
    assert browser.get("/api/me").status_code == 200  # this browser stays signed in
    assert browser.get("/api/me", headers=other).status_code == 401  # every other session is gone
    assert login(browser, "pw.user").status_code == 401
    assert login(browser, "pw.user", "new password 1").status_code == 200


def test_disabled_user(browser, monkeypatch):
    signup(browser, "gone.user")
    monkeypatch.setattr(sys, "argv", ["app.cli", "disable-user", "Gone.User"])
    cli.main()
    assert browser.get("/api/me").status_code == 401  # its sessions went with it
    assert code(login(browser, "gone.user")) == "ACCOUNT_DISABLED"
    assert code(login(browser, "gone.user", "not the password")) == "INVALID_LOGIN"  # nothing told without it
    monkeypatch.setattr(sys, "argv", ["app.cli", "enable-user", "gone.user"])
    cli.main()
    assert login(browser, "gone.user").status_code == 200


def test_signup_cap_counts_active_users_and_is_race_free(browser, client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_USERS", active() + 1)
    assert browser.get("/api/auth/signup-status").json() == {"open": True, "remaining": 1}

    async def race():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", headers=ORIGIN) as c:
            return await asyncio.gather(*(signup(c, f"racer.{i}") for i in range(4)))

    results = client.portal.call(race)
    assert sorted(r.status_code for r in results) == [201, 403, 403, 403]
    assert {code(r) for r in results if r.status_code == 403} == {"SIGNUPS_FULL"}
    assert browser.get("/api/auth/signup-status").json() == {"open": False, "remaining": 0}
    winner = next(r.json()["username"] for r in results if r.status_code == 201)
    monkeypatch.setattr(sys, "argv", ["app.cli", "disable-user", winner])
    cli.main()  # a disabled user frees a spot
    assert browser.get("/api/auth/signup-status").json() == {"open": True, "remaining": 1}


def test_login_and_signup_throttles(browser):
    signup(browser, "throttled")
    signup(browser, "not.throttled")
    for _ in range(10):
        assert login(browser, "throttled", "wrong password").status_code == 401
    r = login(browser, "throttled")  # even the right password, for 15 minutes
    assert (r.status_code, code(r)) == (429, "TOO_MANY_ATTEMPTS")
    assert login(browser, "not.throttled").status_code == 200  # another username from the same address is not
    auth.SIGNUPS.hits.clear()
    assert [signup(browser, f"many.{i}").status_code for i in range(6)] == [201] * 5 + [429]


def test_status_without_a_database_says_so(client, monkeypatch):
    """B2: a dead database is db: false, not a 500 or a 401 (the shell tells 'Database offline' apart)."""
    dead = create_async_engine("postgresql+psycopg://x:y@127.0.0.1:1/none", connect_args={"connect_timeout": 2})
    monkeypatch.setattr(auth, "SessionLocal", async_sessionmaker(dead))
    r = client.get("/api/status", headers=as_user())  # signed out needs no database: that is a 401
    assert r.status_code == 200 and (r.json()["db"], r.json()["worker_alive"]) == (False, False)
    client.cookies.clear()
    assert client.get("/api/status").status_code == 401


def run_cli(monkeypatch, capsys, *args, stdin: str = "") -> str:
    monkeypatch.setattr(sys, "argv", ["app.cli", *args])
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    cli.main()
    return capsys.readouterr().out


def test_user_cli(browser, monkeypatch, capsys):
    signup(browser, "cli.user")
    assert "cli.user" in run_cli(monkeypatch, capsys, "list-users")
    run_cli(monkeypatch, capsys, "set-password", "CLI.user", stdin="reset password 9\n")
    assert browser.get("/api/me").status_code == 401  # signed out everywhere
    assert login(browser, "cli.user", "reset password 9").status_code == 200
    with pytest.raises(SystemExit, match="8 to 128"):
        run_cli(monkeypatch, capsys, "set-password", "cli.user", stdin="short\n")
    with pytest.raises(SystemExit, match="no user"):
        run_cli(monkeypatch, capsys, "enable-user", "nobody.here")
    run_cli(monkeypatch, capsys, "set-quota", "cli.user", "2.5")
    assert "quota 2.5 GB" in run_cli(monkeypatch, capsys, "list-users")
    run_cli(monkeypatch, capsys, "set-quota", "cli.user", "none")
    with SyncSession() as s:
        assert s.scalar(select(User.quota_bytes).where(User.username == "cli.user")) is None


def test_bootstrap(browser, monkeypatch, capsys):
    """User 1 from the migrate service's env, with a throwaway hash made here (never the operator's)."""
    raw = bcrypt.hashpw(b"throwaway boot pw", bcrypt.gensalt(4)).decode().replace("$2b$", "$2a$", 1)  # Caddy's prefix
    try:
        monkeypatch.setattr(settings, "CLIPPER_USER", "Operator.One")
        monkeypatch.setattr(settings, "CLIPPER_PASSWORD_HASH", base64.b64encode(raw.encode()).decode())
        assert "user 1 is 'operator.one', password set" in run_cli(monkeypatch, capsys, "bootstrap")
        with SyncSession() as s:
            assert s.get(User, 1).password_hash == raw
        assert login(browser, "OPERATOR.ONE", "throwaway boot pw").json() == {"id": 1, "username": "operator.one"}
        other = bcrypt.hashpw(b"another pw here", bcrypt.gensalt(4)).decode()
        monkeypatch.setattr(settings, "CLIPPER_PASSWORD_HASH", other)  # raw form; ignored: user 1 has a password
        run_cli(monkeypatch, capsys, "bootstrap")
        with SyncSession() as s:
            assert s.get(User, 1).password_hash == raw
            s.execute(update(User).where(User.id == 1).values(password_hash=None))
            s.commit()
        assert "password set" in run_cli(monkeypatch, capsys, "bootstrap")  # the raw $2b$ form works too
        with SyncSession() as s:
            assert s.get(User, 1).password_hash == other
            s.execute(update(User).where(User.id == 1).values(password_hash=None))
            s.commit()
        monkeypatch.setattr(settings, "CLIPPER_USER", "no way!")
        monkeypatch.setattr(settings, "CLIPPER_PASSWORD_HASH", "bm90IGEgaGFzaA==")  # base64, not of a hash
        out = run_cli(monkeypatch, capsys, "bootstrap")  # never fails the migrate: says so and goes on
        assert "not a valid free username" in out and "not a bcrypt hash" in out and "password not set" in out
        monkeypatch.setattr(settings, "CLIPPER_USER", "")
        monkeypatch.setattr(settings, "CLIPPER_PASSWORD_HASH", "")
        assert "user 1 is 'operator.one', password not set" in run_cli(monkeypatch, capsys, "bootstrap")
    finally:
        with SyncSession() as s:
            s.execute(update(User).where(User.id == 1).values(username="clipper", password_hash=None))
            s.commit()
