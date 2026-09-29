import asyncio
import io
import json
import logging
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import delete

from app.core.config import Settings, settings
from app.core.db import SyncSession, engine
from app.core.secrets import seal
from app.models import TelegramBot, User
from app.services import storage
from app.services.notify import notify


def test_storage_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    path = storage.save("renders/1.mp4", io.BytesIO(b"video bytes"))
    assert path == tmp_path.resolve() / "renders/1.mp4"
    assert storage.path_for("renders/1.mp4").read_bytes() == b"video bytes"
    assert storage.url_for("renders/1.mp4") == "/media/renders/1.mp4"
    storage.delete("renders/1.mp4")
    assert not path.exists()
    storage.delete("renders/1.mp4")  # already gone is fine
    for bad in ("../x", "/etc/passwd", "", "renders/../../x"):
        with pytest.raises(ValueError):
            storage.path_for(bad)


def test_storage_keys_and_owners():
    assert storage.key(7, "raw/5.mp4") == "u/7/raw/5.mp4"
    owners = {"renders/1.mp4": 1, "u/7/raw/5.mp4": 7, "u/1/x": 1, "u/x/y.mp4": None, "u/7": None, "u": None,
              "u/\u00b2/x": None, "u/-1/x": None, "users/2/x": 1}  # fmt: skip
    assert {k: storage.owner(k) for k in owners} == owners


def test_secrets_seal_and_rotate(monkeypatch):
    from cryptography.fernet import Fernet

    from app.core.secrets import seal, unseal

    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "SECRETS_KEY", old)
    sealed = seal("sk_x")
    assert b"sk_x" not in sealed and unseal(sealed) == "sk_x" and unseal(None) is None
    monkeypatch.setattr(settings, "SECRETS_KEY", f"{new},{old}")  # rotation: the new key first, the old still opens
    assert unseal(sealed) == "sk_x" and unseal(seal("sk_y")) == "sk_y"
    monkeypatch.setattr(settings, "SECRETS_KEY", new)  # the old key gone: its values read as none
    assert unseal(sealed) is None
    for blank in ("", "not a fernet key"):
        monkeypatch.setattr(settings, "SECRETS_KEY", blank)
        assert unseal(sealed) is None
        with pytest.raises(RuntimeError):
            seal("sk_x")


REAL_CLIENT = httpx.AsyncClient


def telegram(monkeypatch, handler):
    """Route notify()'s HTTP through handler."""
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))


@pytest.fixture
def bots(db):
    """bots(uid, chat_id, alerts=True, error=None) -> the new bot's token: a telegram_bots row, token sealed."""

    def add(uid: int, chat_id: int | None, alerts: bool = True, error: str | None = None) -> str:
        n = uuid.uuid4().int % 10**9
        token = f"{n}:{'x' * 35}"
        with SyncSession() as s:
            s.add(TelegramBot(user_id=uid, bot_id=n, token_enc=seal(token), chat_id=chat_id, alerts=alerts, error=error))
            s.commit()
        return token

    yield add
    with SyncSession() as s:
        s.execute(delete(TelegramBot))
        s.commit()


def send(*args) -> bool:
    """notify(*args) on a fresh event loop (the async engine's pooled connections belong to one loop)."""

    async def go():
        await engine.dispose()
        try:
            return await notify(*args)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_notify_noop_without_a_bot(bots, monkeypatch):
    calls = []
    telegram(monkeypatch, lambda r: calls.append(r) or httpx.Response(200, json={"ok": True}))
    assert send(1, "hi") is False  # no bot at all
    bots(1, None)  # waiting to be paired
    bots(1, 42, alerts=False)
    bots(1, 42, error="TOKEN_REJECTED")
    assert send(1, "hi") is False
    assert calls == []


def test_notify_goes_to_each_alert_bot_of_that_user(bots, monkeypatch):
    sent = []
    telegram(monkeypatch, lambda r: sent.append(r) or httpx.Response(200, json={"ok": True}))
    with SyncSession() as s:
        other = User(username=f"n.{uuid.uuid4().hex[:10]}")
        s.add(other)
        s.commit()
    one, two = bots(1, 42), bots(1, 43)
    bots(1, 44, alerts=False)  # interactive only (the operator's bots 2 and 3)
    theirs = bots(other.id, 99)
    assert send(1, "x") is True
    to = {(str(r.url).split("/bot")[1].split("/")[0], json.loads(r.content)["chat_id"]) for r in sent}
    assert to == {(one, 42), (two, 43)}
    sent.clear()
    assert send(other.id, "y") is True
    assert [(str(r.url).split("/bot")[1].split("/")[0], json.loads(r.content)["chat_id"]) for r in sent] == [(theirs, 99)]
    sent.clear()
    with SyncSession() as s:  # disabled: their bots don't run, so nothing would answer the alert's buttons
        s.get(User, other.id).disabled_at = datetime.now(UTC)
        s.commit()
    assert send(other.id, "z") is False and sent == []
    monkeypatch.setattr(settings, "SECRETS_KEY", "")  # tokens that can't be opened: no bot to send with
    assert send(1, "x") is False and sent == []


def test_inline_env_comment_is_blank():
    # .env `TELEGRAM_CHAT_ID=   # leave blank` reaches the container as "# leave blank"
    s = Settings(TELEGRAM_CHAT_ID="# leave blank", TELEGRAM_BOT_TOKEN="   # none", TELEGRAM_BOT_TOKEN_3="# none")
    assert (s.TELEGRAM_CHAT_ID, s.TELEGRAM_BOT_TOKEN, s.TELEGRAM_BOT_TOKEN_3) == ("", "", "")
    assert Settings(TELEGRAM_CHAT_ID="-100123").TELEGRAM_CHAT_ID == "-100123"


def test_notify_never_logs_token(bots, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)  # the worker's root logger is INFO; httpx logs request URLs at INFO
    token = bots(1, 42)
    telegram(monkeypatch, lambda request: httpx.Response(400, json={"ok": False, "description": "chat not found"}))
    assert send(1, "x") is False
    assert caplog.records and token not in caplog.text


def test_notify_sends_and_never_raises(bots, monkeypatch):
    sent = []

    def ok(request):
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    token = bots(1, 42)
    telegram(monkeypatch, ok)
    assert send(1, "<b>failed</b>", "https://clipper.example.com/recover/1") is True
    assert str(sent[0].url) == f"https://api.telegram.org/bot{token}/sendMessage"
    assert json.loads(sent[0].content) == {
        "chat_id": 42,
        "text": "<b>failed</b>",
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[{"text": "Open in Clipper", "url": "https://clipper.example.com/recover/1"}]]},
    }
    # Telegram rejects localhost button URLs (live-verified), so a local link goes into the text instead
    assert send(1, "<b>failed</b>", "http://localhost:5173/recover/1") is True
    assert json.loads(sent[1].content) == {
        "chat_id": 42,
        "text": "<b>failed</b>\nhttp://localhost:5173/recover/1",
        "parse_mode": "HTML",
    }
    # bot buttons: callback data for the bot service, https values as link buttons
    assert send(1, "x", "http://localhost:5173/accounts", [[("Reconnect", "https://zernio.com"), ("Sync", "sync")]])
    assert json.loads(sent[2].content)["reply_markup"] == {
        "inline_keyboard": [[{"text": "Reconnect", "url": "https://zernio.com"}, {"text": "Sync", "callback_data": "sync"}]]
    }

    def down(request):
        raise httpx.ConnectError("network down")

    telegram(monkeypatch, down)
    assert send(1, "x") is False

    telegram(monkeypatch, lambda request: httpx.Response(400, json={"ok": False, "description": "chat not found"}))
    assert send(1, "x") is False

    two = bots(1, 43)  # one bot's chat refuses, another's takes it: delivered
    telegram(monkeypatch, lambda r: httpx.Response(200 if two in str(r.url) else 403, json={"ok": two in str(r.url)}))
    assert send(1, "x") is True
