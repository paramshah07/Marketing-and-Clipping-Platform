"""Each user's own Zernio key: PUT / DELETE /api/me/zernio-key and the Re-check (POST .../check), and user 1's
one-shot .env import (cli bootstrap). Zernio is an httpx.MockTransport serving only tests/fixtures/zernio/ bodies:
GET /v1/auth/verify the live recordings, GET /v1/accounts accounts.json (its README says how the over-limit list
is made). The key is stored sealed and never sent back."""

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from app import cli
from app.core.config import settings
from app.core.db import SyncSession, engine
from app.core.secrets import unseal
from app.main import app
from app.models import Account, Post, Render, SourceClip, User
from app.services import zernio
from conftest import as_user, zernio_key

FIX = Path(__file__).parent / "fixtures" / "zernio"
VERIFY = json.loads((FIX / "live_auth_verify.json").read_text())["body"]
KEY, OTHER_KEY = "sk_" + "a1" * 32, "zrk_" + "b2" * 32  # the shapes Zernio mints: a prefix and 64 hex characters
REAL_CLIENT = zernio.client
PATH = "/api/me/zernio-key"


def fx(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


def verified(zernio_user_id: str) -> dict:
    """live_auth_verify.json for another Zernio user (each test its own: one Clipper user per Zernio user)."""
    d = fx("live_auth_verify")
    d["body"]["userId"] = zernio_user_id
    return d


class Zernio:
    """GET /v1/auth/verify -> verify (a fixture); GET /v1/accounts -> accounts.json's account once per (id, username)
    in listed, plus over_limit with includeOverLimit=true. Records every request."""

    def __init__(self, verify: dict, listed=(), over_limit=()):
        self.verify, self.listed, self.over, self.calls = verify, listed, over_limit, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if req.url.path.endswith("/auth/verify"):
            return httpx.Response(self.verify["status"], json=self.verify["body"])
        assert req.url.path.endswith("/accounts"), req.url
        body = fx("accounts")["body"]
        pairs = [*self.listed, *(self.over if req.url.params.get("includeOverLimit") == "true" else ())]
        body["accounts"] = [body["accounts"][0] | {"_id": zid, "username": name} for zid, name in pairs]
        return httpx.Response(200, json=body)


@pytest.fixture
def use(db, monkeypatch):
    """use(Zernio(...)): the api's Zernio calls go to it (the real client: base URL, the key, timeouts)."""

    def use(z: Zernio) -> Zernio:
        monkeypatch.setattr(zernio, "client", lambda key, **kw: REAL_CLIENT(key, transport=httpx.MockTransport(z), **kw))
        return z

    use(Zernio({"status": 500, "body": {}}))
    return use


@contextmanager
def api(uid: int):
    with TestClient(app, headers=as_user(uid)) as c:
        yield c
        c.portal.call(engine.dispose)  # its connections belong to this client's event loop


def new_user(**values) -> int:
    with SyncSession() as s:
        u = User(username=f"k.{uuid.uuid4().hex[:10]}", **values)
        s.add(u)
        s.commit()
        return u.id


def user(uid: int) -> User:
    with SyncSession() as s:
        return s.get(User, uid)


def post_of(uid: int, status: str) -> int:
    """A post of user uid's (account, clip and render too, all theirs)."""
    with SyncSession() as s:
        acc = Account(user_id=uid, zernio_account_id=uuid.uuid4().hex, zernio_profile_id="p", username="a", timezone="UTC")
        clip = SourceClip(user_id=uid, origin="upload", status="READY")
        s.add_all([acc, clip])
        s.flush()
        r = Render(user_id=uid, source_clip_id=clip.id, status="READY")
        s.add(r)
        s.flush()
        p = Post(user_id=uid, render_id=r.id, account_id=acc.id, caption="", scheduled_for=datetime.now(UTC),
                 status=status, idempotency_key=uuid.uuid4().hex)  # fmt: skip
        s.add(p)
        s.commit()
        return p.id


def set_post(pid: int, **values) -> None:
    with SyncSession() as s:
        s.execute(update(Post).where(Post.id == pid).values(**values))
        s.commit()


def code(r: httpx.Response) -> tuple[int, str]:
    return r.status_code, r.json()["detail"]["code"]


def test_put_verifies_stores_sealed_and_pulls_the_accounts(use):
    uid, zuid, zid = new_user(), uuid.uuid4().hex, uuid.uuid4().hex
    z = use(Zernio(verified(zuid), listed=[(zid, "mine.one")], over_limit=[(uuid.uuid4().hex, "third.one")]))
    with api(uid) as c:
        r = c.put(PATH, json={"key": f"  {KEY}\n"})
        me = c.get("/api/me").json()
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["zernio"] | {"checked_at": None} == {"status": "valid", "last4": KEY[-4:], "email": VERIFY["email"],
                                                    "name": VERIFY["name"], "checked_at": None, "error": None}  # fmt: skip
    assert (out["accounts"], out["skipped"], out["over_limit"]) == (["mine.one"], [], ["third.one"])
    assert KEY not in r.text and KEY not in json.dumps(me)  # never sent back
    assert (me["zernio"]["status"], me["zernio"]["last4"], me["setup"]["zernio"], me["setup"]["instagram"]) == (
        "valid", KEY[-4:], True, True)  # fmt: skip
    verify, listed, every = z.calls
    assert {c.headers["authorization"] for c in z.calls} == {f"Bearer {KEY}"}
    assert (verify.url.path, dict(listed.url.params), dict(every.url.params)) == (
        "/api/v1/auth/verify", {}, {"includeOverLimit": "true"})  # fmt: skip
    u = user(uid)
    assert (unseal(u.zernio_key_enc), u.zernio_key_gen, u.zernio_user_id) == (KEY, 1, zuid)
    assert KEY.encode() not in u.zernio_key_enc
    with SyncSession() as s:
        acc = s.scalar(select(Account).where(Account.zernio_account_id == zid))
        assert (acc.user_id, acc.username, acc.connection_status) == (uid, "mine.one", "connected")


def test_put_refusals_store_nothing(use, monkeypatch):
    uid = new_user()
    z = use(Zernio(fx("live_auth_verify_401")))
    with api(uid) as c:
        for bad in ("", "pk_" + "a" * 64, "sk_short", "sk_" + "a" * 200, f"{KEY}\nX-Other: header"):
            assert code(c.put(PATH, json={"key": bad})) == (422, "ZERNIO_KEY_INVALID"), bad
        assert z.calls == []  # the shape is checked first
        r = c.put(PATH, json={"key": KEY})  # well formed, but Zernio says 401 (the live recording)
        assert code(r) == (422, "ZERNIO_KEY_INVALID") and "refused" in r.json()["detail"]["message"]
        assert code(c.post(f"{PATH}/check")) == (409, "ZERNIO_KEY_MISSING")
        assert code(c.post("/api/accounts/sync")) == (409, "ZERNIO_KEY_MISSING")
        monkeypatch.setattr(settings, "SECRETS_KEY", "")
        assert code(c.put(PATH, json={"key": KEY})) == (503, "SECRETS_KEY_MISSING")
    u = user(uid)
    assert (u.zernio_key_enc, u.zernio_key_status, u.zernio_user_id, len(z.calls)) == (None, "none", None, 1)


def test_one_clipper_user_per_zernio_user_and_account(use):
    zuid = uuid.uuid4().hex
    new_user(zernio_user_id=zuid)
    use(Zernio(verified(zuid)))
    with api(new_user()) as c:
        r = c.put(PATH, json={"key": KEY})
    assert code(r) == (409, "ZERNIO_USER_CLAIMED")
    # Instagram accounts from one Zernio account: a key of another one would orphan them and their posts
    first, second = uuid.uuid4().hex, uuid.uuid4().hex
    uid = new_user(zernio_user_id=first)
    use(Zernio(verified(second)))
    with api(uid) as c:
        assert c.put(PATH, json={"key": KEY}).status_code == 200  # no accounts yet: a switch is fine
        assert user(uid).zernio_user_id == second
        post_of(uid, "DRAFT")  # now it has an account
        use(Zernio(verified(first)))
        assert code(c.put(PATH, json={"key": OTHER_KEY})) == (409, "ZERNIO_ACCOUNT_CHANGED")
        assert c.delete(PATH).status_code == 204  # removing the key keeps the Zernio user it belonged to
        assert code(c.put(PATH, json={"key": OTHER_KEY})) == (409, "ZERNIO_ACCOUNT_CHANGED")
        use(Zernio(verified(second)))
        assert c.put(PATH, json={"key": OTHER_KEY}).status_code == 200


def test_key_changes_wait_for_publishing_posts_and_count_generations(use):
    uid = new_user()
    use(Zernio(verified(uuid.uuid4().hex)))
    with api(uid) as c:
        assert c.put(PATH, json={"key": KEY}).status_code == 200
        assert c.put(PATH, json={"key": KEY}).status_code == 200  # the same credential again: same generation
        assert user(uid).zernio_key_gen == 1
        pid = post_of(uid, "PUBLISHING")
        assert code(c.put(PATH, json={"key": OTHER_KEY})) == (409, "KEY_IN_USE")
        assert code(c.delete(PATH)) == (409, "KEY_IN_USE")
        assert unseal(user(uid).zernio_key_enc) == KEY
        set_post(pid, status="FAILED")
        assert c.put(PATH, json={"key": OTHER_KEY}).json()["zernio"]["last4"] == OTHER_KEY[-4:]
        assert user(uid).zernio_key_gen == 2  # a replay of a post first sent with KEY is now refused (KEY_CHANGED)
        assert c.delete(PATH).status_code == 204
        assert c.get("/api/me").json()["zernio"] == {"status": "none", "last4": None, "email": VERIFY["email"],
                                                     "name": VERIFY["name"], "checked_at": None, "error": None}  # fmt: skip
        u = user(uid)
        assert (u.zernio_key_enc, u.zernio_key_gen, u.zernio_user_id is not None) == (None, 2, True)
        c.put(PATH, json={"key": OTHER_KEY})
        assert user(uid).zernio_key_gen == 3  # after a removal any key counts as a new credential


def test_a_key_change_waits_for_a_claim_in_flight(use, db):
    """publish_post claims a post under FOR SHARE of its user's row (publish._key): a key change (FOR UPDATE) waits
    for that commit, then finds the post PUBLISHING and is refused. The claim publishes with the key it read."""
    uid = new_user()
    zernio_key(uid, KEY)
    pid = post_of(uid, "SCHEDULED")
    use(Zernio(verified(uuid.uuid4().hex)))
    with api(uid) as c, db.connect() as claim, ThreadPoolExecutor(1) as pool:
        claim.execute(text("SELECT 1 FROM users WHERE id = :u FOR SHARE"), {"u": uid})
        pending = pool.submit(c.put, PATH, json={"key": OTHER_KEY})
        time.sleep(1)
        assert not pending.done()  # waiting for the claim's row lock
        claim.execute(update(Post).where(Post.id == pid).values(status="PUBLISHING"))
        claim.commit()
        r = pending.result(timeout=30)
    assert code(r) == (409, "KEY_IN_USE") and unseal(user(uid).zernio_key_enc) == KEY


def test_recheck_marks_the_key_invalid_and_valid_again(use, monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHING_ENABLED", True)
    uid, zuid, zid = new_user(), uuid.uuid4().hex, uuid.uuid4().hex
    use(Zernio(verified(zuid), listed=[(zid, "re.check")]))
    with api(uid) as c:
        assert c.put(PATH, json={"key": KEY}).status_code == 200
        use(Zernio(fx("live_auth_verify_401")))  # revoked in Zernio since
        r = c.post(f"{PATH}/check")
        assert r.status_code == 200, r.text
        assert (r.json()["zernio"]["status"], r.json()["zernio"]["error"], r.json()["accounts"]) == (
            "invalid", "Zernio refused the key", [])  # fmt: skip
        assert c.get("/api/status").json()["publishing_off"] == "key_invalid"
        use(Zernio(verified(zuid), listed=[(zid, "re.check")]))
        r = c.post(f"{PATH}/check").json()
        assert (r["zernio"]["status"], r["zernio"]["error"], r["accounts"]) == ("valid", None, ["re.check"])
        assert c.get("/api/status").json()["publishing_enabled"] is True
    assert user(uid).zernio_key_gen == 1  # the same key: no new generation


def test_env_import_is_one_shot(db, monkeypatch, capsys):
    """bootstrap seals ZERNIO_API_KEY into user 1 once: generation 1 (what migration 0007 gave the posts the .env key
    sent), valid with no network call. It waits for a SECRETS_KEY, and never runs again once stamped."""
    zernio_key(1, None, env_imported_at=None, zernio_user_id=None, zernio_checked_at=None, zernio_error=None)
    monkeypatch.setattr(settings, "ZERNIO_API_KEY", KEY)
    monkeypatch.setattr(settings, "SECRETS_KEY", "")
    cli.bootstrap()
    assert "waits for it" in capsys.readouterr().out
    assert (user(1).zernio_key_enc, user(1).env_imported_at) == (None, None)
    monkeypatch.setattr(settings, "SECRETS_KEY", os.environ["SECRETS_KEY"])
    cli.bootstrap()
    u = user(1)
    assert (unseal(u.zernio_key_enc), u.zernio_key_last4, u.zernio_key_status, u.zernio_key_gen) == (KEY, KEY[-4:], "valid", 1)
    assert (u.zernio_user_id, u.zernio_checked_at, u.env_imported_at is not None) == (None, None, True)
    monkeypatch.setattr(settings, "ZERNIO_API_KEY", OTHER_KEY)
    cli.bootstrap()  # stamped: the .env is never read again
    assert unseal(user(1).zernio_key_enc) == KEY
    with SyncSession() as s:  # DELETE /api/me/zernio-key
        s.execute(update(User).where(User.id == 1).values(zernio_key_enc=None, zernio_key_status="none"))
        s.commit()
    cli.bootstrap()  # a key the operator removed never comes back from .env
    assert (user(1).zernio_key_enc, user(1).zernio_key_status) == (None, "none")
    zernio_key(1, None)
