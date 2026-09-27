import asyncio
import io
import json
import logging

import httpx
import pytest

from app.core.config import Settings, settings
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


def telegram(monkeypatch, handler):
    """Configure Telegram and route notify()'s HTTP through handler."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "42")
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_notify_noop_without_config(monkeypatch):
    calls = []
    telegram(monkeypatch, lambda r: calls.append(r) or httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    assert asyncio.run(notify("hi")) is False
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    assert asyncio.run(notify("hi")) is False
    assert calls == []


def test_inline_env_comment_is_blank():
    # .env `TELEGRAM_CHAT_ID=   # leave blank` reaches the container as "# leave blank"
    s = Settings(TELEGRAM_CHAT_ID="# leave blank", TELEGRAM_BOT_TOKEN="   # none")
    assert (s.TELEGRAM_CHAT_ID, s.TELEGRAM_BOT_TOKEN) == ("", "")
    assert Settings(TELEGRAM_CHAT_ID="-100123").TELEGRAM_CHAT_ID == "-100123"


def test_notify_never_logs_token(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)  # the worker's root logger is INFO; httpx logs request URLs at INFO
    telegram(monkeypatch, lambda request: httpx.Response(400, json={"ok": False, "description": "chat not found"}))
    assert asyncio.run(notify("x")) is False
    assert caplog.records and "123:abc" not in caplog.text


def test_notify_sends_and_never_raises(monkeypatch):
    sent = []

    def ok(request):
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    telegram(monkeypatch, ok)
    assert asyncio.run(notify("<b>failed</b>", "https://clipper.example.com/recover/1")) is True
    assert str(sent[0].url) == "https://api.telegram.org/bot123:abc/sendMessage"
    assert json.loads(sent[0].content) == {
        "chat_id": "42",
        "text": "<b>failed</b>",
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[{"text": "Open in Clipper", "url": "https://clipper.example.com/recover/1"}]]},
    }
    # Telegram rejects localhost button URLs (live-verified), so a local link goes into the text instead
    assert asyncio.run(notify("<b>failed</b>", "http://localhost:5173/recover/1")) is True
    assert json.loads(sent[1].content) == {
        "chat_id": "42",
        "text": "<b>failed</b>\nhttp://localhost:5173/recover/1",
        "parse_mode": "HTML",
    }
    # bot buttons: callback data for the bot service, https values as link buttons
    assert asyncio.run(notify("x", "http://localhost:5173/accounts", [[("Reconnect", "https://zernio.com"), ("Sync", "sync")]]))
    assert json.loads(sent[2].content)["reply_markup"] == {
        "inline_keyboard": [[{"text": "Reconnect", "url": "https://zernio.com"}, {"text": "Sync", "callback_data": "sync"}]]
    }

    def down(request):
        raise httpx.ConnectError("network down")

    telegram(monkeypatch, down)
    assert asyncio.run(notify("x")) is False

    telegram(monkeypatch, lambda request: httpx.Response(400, json={"ok": False, "description": "chat not found"}))
    assert asyncio.run(notify("x")) is False
