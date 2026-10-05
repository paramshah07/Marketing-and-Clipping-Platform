"""The Telegram bot (app/bot, docs/telegram-bot.md).

Pure helpers against the web app's own test values (geometry.test.ts, utils.test.ts), then the flows end
to end: the bot drives the real api in process (httpx ASGI transport, the clipper_test database) and talks
to a fake Telegram that records every call and checks what real Telegram would refuse (HTML it can't
parse, callback_data over 64 bytes, over-long text). Zernio is never called: no API key, or a key with
zernio.client patched to fail. Nothing publishes: no worker listens on clipper_test."""

import asyncio
import html
import json
import re
import struct
import uuid
import zlib
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select, text, update

from app.bot import fmt, screens
from app.bot.clients import Clipper, Telegram
from app.bot.core import Bot
from app.core.config import settings
from app.core.db import SyncSession, engine
from app.main import app
from app.models import Account, Brand, Post, Render, SavedCaption, SavedCover, SourceClip
from app.schemas import CropConfig, OverlayConfig
from app.services import errors, zernio
from conftest import zernio_key

CHAT = 4242

# ---------------------------------------------------------------- pure helpers


def close(a, b, eps=1e-9):
    return all(abs(x - y) < eps for x, y in zip(a, b, strict=True))


def test_geometry_matches_the_web_editor():
    aspect = fmt.logo_aspect((400, 160))  # geometry.test.ts: a 400x160 logo at w 0.22
    assert abs(0.22 * aspect - 0.0495) < 1e-12
    h = 0.22 * aspect
    assert close(fmt.snap(2, 0.22, h), (0.68, 0.1625))
    assert close(fmt.snap(4, 0.22, h), (0.39, 0.37025))
    assert close(fmt.snap(6, 0.22, h), (0.1, 0.578))
    assert fmt.snap(6, 0.6, 0.6 * 1.9)[1] == 0  # taller than the safe zone: starts at the top, never above
    # place: width kept in [2%, min(60%, 1/aspect)], snapped when a cell is set, valid for the api either way
    o = fmt.place({"x": 0.9, "y": 0.95, "w": 0.8, "opacity": 1}, None, aspect)
    assert o["w"] == 0.6 and close((o["x"], o["y"]), (0.4, 1 - 0.6 * aspect))
    for cell in (None, *range(9)):
        for w in (0.0, 0.22, 5.0):
            OverlayConfig(**fmt.place({"x": 0.72, "y": 0.16, "w": w, "opacity": 0.5}, cell, fmt.logo_aspect((100, 900))))
    assert fmt.png_size(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + struct.pack(">II", 400, 160)) == (400, 160)
    assert fmt.png_size(b"GIF89a") is None and fmt.logo_aspect(None) == 1080 / 1920


def test_crop_windows():
    assert fmt.crop_options(1920, 1080) == ["centre", "left", "right"]
    assert fmt.crop_options(1080, 2400) == ["centre", "top", "bottom"]
    assert fmt.crop_options(1080, 1920) == ["centre"]
    assert fmt.crop_box(1920, 1080, "centre") is None  # the render's own centre fill
    left, right = fmt.crop_box(1920, 1080, "left"), fmt.crop_box(1920, 1080, "right")
    assert close((left["x"], left["y"], left["h"]), (0, 0, 1)) and abs(left["w"] - 1080 * 9 / 16 / 1920) < 1e-12
    assert abs(right["x"] + right["w"] - 1) < 1e-12
    bottom = fmt.crop_box(1080, 2400, "bottom")
    assert close((bottom["x"], bottom["w"], bottom["y"] + bottom["h"]), (0, 1, 1))
    for box in (left, right, bottom, fmt.crop_box(1080, 2400, "top")):
        CropConfig(**box)  # inside the source frame, as the api checks


def test_captions_and_names_match_the_web_app():
    t = "Fuel up → {link} · clip by {creator}\n#ad"  # utils.test.ts
    assert fmt.fill_caption(t, "flux.gg", "@maya") == "Fuel up → flux.gg · clip by @maya\n#ad"
    assert fmt.fill_caption(t, "flux.gg", None) == "Fuel up → flux.gg\n#ad"
    assert fmt.fill_caption("Night fuel\nClip: {creator}\n#ad", None, "") == "Night fuel\n#ad"
    assert fmt.fill_caption(None, None, None) == ""
    assert fmt.short_url("https://www.tiktok.com/@maya.eats/video/7421983301?is_from_webapp=1&sender_device=pc") == "tiktok.com/@maya.eats/video/7421983301"
    assert fmt.short_url("https://www.youtube.com/watch?v=jNQXAC9IVRw&t=4s") == "youtube.com/watch?v=jNQXAC9IVRw"
    assert fmt.short_url("IMG_4821.MOV") == "IMG_4821.MOV"
    assert fmt.hashtags("#a #b_2 #ünï x#y #") == 4
    assert fmt.mmss(None) == "—" and fmt.mmss(125.9) == "02:05"
    assert fmt.placement({"brand_id": 1, "overlay_config": {"x": 0.68, "y": 0.1625, "w": 0.22}, "crop_config": None}) == "Top right · 22% · full frame"
    assert fmt.placement({"brand_id": None, "overlay_config": None, "crop_config": {"x": 0, "y": 0, "w": 0.3, "h": 1}}) == "9:16 crop"
    card = "<b>x</b>\n<blockquote>" + "y" * 2000 + "</blockquote>"
    assert fmt.shorten(card, 1024) == "<b>x</b>" and fmt.visible("a &amp; <b>b</b>") == 5
    assert set(errors.CAUSES) <= set(screens.TITLES)  # a failed post's card has a title, never the raw code


def test_parse_when():
    now = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)  # a Sunday; 13:00 in New York (EDT, UTC-4)
    ny = "America/New_York"

    def at(s, **kw):
        t = fmt.parse_when(s, ny, now, **kw)
        return t and t.astimezone(ZoneInfo(ny)).strftime("%Y-%m-%d %H:%M")

    assert at("18:30") == "2026-09-27 18:30"
    assert at("6:30pm") == at("6.30 PM") == "2026-09-27 18:30"
    assert at("12am") == "2026-09-27 00:00" and at("12pm") == "2026-09-27 12:00"
    assert at("tomorrow 9am") == at("tmrw at 9:00") == "2026-09-28 09:00"
    assert at("fri 13:00") == at("Friday 1pm") == "2026-10-02 13:00"
    assert at("sun 12:00") == "2026-10-04 12:00" and at("sun 14:00") == "2026-09-27 14:00"  # passed today: next week
    assert at("2026-10-02 09:00") == "2026-10-02 09:00"
    assert at("09:00", day=date(2026, 10, 5)) == "2026-10-05 09:00"
    assert fmt.parse_when("now", ny, now) == now
    for bad in ("9", "25:00", "13pm", "hello", "2026-13-01 09:00", "18:61"):
        assert fmt.parse_when(bad, ny, now) is None, bad
    # a time in the spring-forward gap moves forward, as the slot engine does (01:30 London -> 02:30 BST)
    assert fmt.parse_when("2026-03-29 01:30", "Europe/London", now) == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    assert fmt.day_slots(["13:00", "09:00"], date(2026, 9, 27), ny) == [datetime(2026, 9, 27, 13, tzinfo=UTC), datetime(2026, 9, 27, 17, tzinfo=UTC)]


def test_slot_presets_match_the_web_app():
    assert fmt.every(60, "07:00", "23:00") == [f"{h:02d}:00" for h in range(7, 24)]  # schedule.test.ts
    assert fmt.every(30, "22:30", "23:30") == ["22:30", "23:00", "23:30"]
    assert [len(t) for _, t in fmt.SLOT_PRESETS.values()] == [17, 34, 8, 3]
    for _, times in fmt.SLOT_PRESETS.values():
        assert times == sorted(set(times))
    assert fmt.slots_label(fmt.every(60, "07:00", "23:00")) == "every hour, 07:00–23:00"
    assert fmt.slots_label(["08:00", "20:30"]) == "08:00 20:30" and fmt.slots_label([]) == "none"


def test_markup():
    assert fmt.markup([[("Open", "https://zernio.com"), ("Go", "p:1")], []]) == {
        "inline_keyboard": [[{"text": "Open", "url": "https://zernio.com"}, {"text": "Go", "callback_data": "p:1"}]]
    }
    for bad in ("", "x" * 65, "é" * 33):
        with pytest.raises(ValueError):
            fmt.markup([[("x", bad)]])
    assert screens.handle_in("see https://www.tiktok.com/@maya/video/1 by @maya.eats. and a@b.com") == "@maya.eats"
    assert screens.handle_in("mail a@b.com") is None


# ---------------------------------------------------------------- the flows: fake Telegram


ALLOWED_TAGS = {"b", "i", "u", "s", "a", "code", "pre", "blockquote"}


class TelegramHTML(HTMLParser):
    """Refuses what Telegram's HTML parse mode refuses: other tags, unbalanced tags, other named entities."""

    def __init__(self, s: str):
        super().__init__(convert_charrefs=False)
        self.stack = []
        self.feed(s)
        self.close()
        assert not self.stack, f"unclosed {self.stack} in {s!r}"

    def handle_starttag(self, tag, attrs):
        assert tag in ALLOWED_TAGS, f"<{tag}> in Telegram HTML"
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack.pop() == tag, f"</{tag}> out of order"

    def handle_entityref(self, name):
        assert name in ("lt", "gt", "amp", "quot"), f"&{name};"


def form(request: httpx.Request) -> dict:
    """A multipart body's fields (JSON ones decoded); files as {filename, size}."""
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    out = {}
    for part in request.content.split(b"--" + boundary):
        head, _, body = part.partition(b"\r\n\r\n")
        if not (name := re.search(rb'name="([^"]+)"', head)):
            continue
        body = body.removesuffix(b"\r\n")
        if filename := re.search(rb'filename="([^"]*)"', head):
            out[name[1].decode()] = {"filename": filename[1].decode(), "size": len(body)}
        else:
            value = body.decode()
            out[name[1].decode()] = json.loads(value) if value[:1] in "{[" else value
    return out


class FakeTelegram:
    """Answers the Bot API like Telegram and keeps the chat: every message with its text and buttons."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.messages: dict[int, dict] = {}
        self.toasts: list[tuple[str | None, bool]] = []
        self.files: dict[str, bytes] = {}
        self.ids = iter(range(1000, 10**6))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/file/"):
            return httpx.Response(200, content=self.files[request.url.path.rsplit("/", 1)[1]])
        method = request.url.path.rsplit("/", 1)[1]
        p = form(request) if request.headers["content-type"].startswith("multipart/") else json.loads(request.content)
        self.calls.append((method, p))
        assert str(p.get("chat_id", CHAT)) == str(CHAT)
        body = p.get("text", p.get("caption"))
        if body is not None:
            TelegramHTML(body)
            assert fmt.visible(body) <= (4096 if "text" in p else 1024), f"{method} too long"
        for row in (p.get("reply_markup") or {}).get("inline_keyboard", []):
            for b in row:
                assert b["text"] and ("url" in b) != ("callback_data" in b)
                assert "url" in b or 1 <= len(b["callback_data"].encode()) <= 64
                assert "url" not in b or b["url"].startswith("https://")
        result = True
        if method in ("sendMessage", "sendPhoto", "sendVideo", "sendDocument"):
            mid = next(self.ids)
            self.messages[mid] = {"text": body or "", "markup": p.get("reply_markup"), "photo": method == "sendPhoto",
                                  "reply_to": (p.get("reply_parameters") or {}).get("message_id")}  # fmt: skip
            result = {"message_id": mid, "chat": {"id": CHAT}} | ({"photo": [{"file_id": "x"}]} if method == "sendPhoto" else {})
        elif method.startswith("editMessage"):
            m = self.messages[int(p["message_id"])]
            if body is not None:
                m["text"] = body
            m["markup"] = p.get("reply_markup")
        elif method == "answerCallbackQuery":
            self.toasts.append((p.get("text"), bool(p.get("show_alert"))))
        elif method == "getFile":
            result = {"file_id": p["file_id"], "file_path": f"documents/{p['file_id']}"}
        return httpx.Response(200, json={"ok": True, "result": result})

    def last(self) -> dict:
        return self.messages[max(self.messages)]

    def buttons(self, mid: int) -> list[tuple[str, str]]:
        rows = (self.messages[mid]["markup"] or {}).get("inline_keyboard", [])
        return [(b["text"], b.get("callback_data") or b.get("url")) for row in rows for b in row]


class Phone:
    """The operator's side: sends messages and taps buttons, as Telegram would deliver them to the bot."""

    def __init__(self, bot: Bot, tg: FakeTelegram):
        self.bot, self.tg, self.n = bot, tg, 0

    async def say(self, text: str | None = None, chat: int = CHAT, **message) -> None:
        self.n += 1
        msg = {"message_id": 500000 + self.n, "chat": {"id": chat, "type": "private"}} | ({"text": text} if text is not None else {}) | message
        await self.bot.handle({"update_id": self.n, "message": msg})

    async def tap(self, label: str, mid: int | None = None) -> str | None:
        """Tap the newest button whose label starts with `label` (in message mid, if given); the toast back."""
        for m in [mid] if mid else sorted(self.tg.messages, reverse=True):
            data = next((d for t, d in self.tg.buttons(m) if t.startswith(label)), None)
            if data:
                break
        else:
            raise AssertionError(f"no {label!r} button; newest: {self.tg.buttons(max(self.tg.messages))}")
        return await self.press(data, m)

    async def press(self, data: str, m: int) -> str | None:
        """A tap on the button with this callback data in message m (it may be gone already: a double tap)."""
        self.n += 1
        message = {"message_id": m, "chat": {"id": CHAT}} | ({"photo": [{"file_id": "x"}]} if self.tg.messages[m]["photo"] else {})
        toasts = len(self.tg.toasts)
        await self.bot.handle({"update_id": self.n, "callback_query": {"id": str(self.n), "data": data, "message": message}})
        return self.tg.toasts[toasts][0] if len(self.tg.toasts) > toasts else None

    def text(self, mid: int | None = None) -> str:
        return self.tg.messages[mid or max(self.tg.messages)]["text"]

    async def settle(self) -> None:
        while self.bot._tasks:
            await asyncio.gather(*list(self.bot._tasks))


@pytest.fixture
def env(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "ZERNIO_API_KEY", "")  # quota -> None: never a real Zernio call
    monkeypatch.setattr(settings, "PUBLISHING_ENABLED", False)
    monkeypatch.setattr(settings, "APP_BASE_URL", "http://localhost:5173")
    media = next(r for r in app.routes if getattr(r, "name", None) == "media").app
    monkeypatch.setattr(media, "all_directories", [tmp_path])  # /media serves this test's files, not ./data
    zernio._quota_cache.clear()
    yield tmp_path
    zernio_key(1, None)


def run(scenario):
    """scenario(phone, tg, bot) inside the api's lifespan (it opens the job queue), on one event loop."""

    async def main():
        await engine.dispose()  # pooled connections of another test's loop
        tg = FakeTelegram()
        bot = Bot(Telegram("123:abc", transport=httpx.MockTransport(tg)),
                  Clipper("http://api", transport=httpx.ASGITransport(app=app)), str(CHAT))  # fmt: skip
        try:
            async with app.router.lifespan_context(app):
                await scenario(Phone(bot, tg), tg, bot)
        finally:
            await engine.dispose()

    asyncio.run(main())


def job(task: str, **kwargs) -> bool:
    with SyncSession() as s:
        q = text("SELECT count(*) FROM procrastinate_jobs WHERE task_name = :t AND args @> CAST(:a AS jsonb)")
        return s.execute(q, {"t": task, "a": json.dumps(kwargs)}).scalar() == 1


def rgba_png(w: int, hh: int) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\0" + b"\0\0\0\0" * w for _ in range(hh))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, hh, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def seed(tmp_path, auto_approve=False, width=1920, height=1080) -> dict:
    """A READY clip, a brand with a 400x160 logo, a READY render of them and a connected account (UTC,
    09:00 / 13:00 / 19:00)."""
    (tmp_path / "logos").mkdir(exist_ok=True)
    with SyncSession() as s:
        clip = SourceClip(origin="url", status="READY", source_url="https://www.tiktok.com/@maya/video/7421983301",
                          source_creator_handle="@maya", duration_s=20.0, width=width, height=height, fps=30.0, has_audio=True,
                          size_bytes=5_000_000, raw_key="raw/x.mp4")  # fmt: skip
        brand = Brand(name="Northwind", caption_template="Coffee at {link} · clip by {creator}", link="nw.coffee", auto_approve=auto_approve)
        acc = Account(zernio_account_id=uuid.uuid4().hex, zernio_profile_id="p", username="acct", timezone="UTC",
                      posting_slots={"times": ["09:00", "13:00", "19:00"]}, daily_cap=10, min_gap_minutes=30)  # fmt: skip
        s.add_all([clip, brand, acc])
        s.flush()
        brand.logo_key = f"logos/{brand.id}.png"
        (tmp_path / brand.logo_key).write_bytes(rgba_png(400, 160))
        r = Render(source_clip_id=clip.id, brand_id=brand.id, status="READY", duration_s=20.0, size_bytes=4_000_000,
                   caption="hello #ad", output_key=f"renders/{clip.id}.mp4", overlay_config={"x": 0.68, "y": 0.1625, "w": 0.22, "opacity": 1})  # fmt: skip
        s.add(r)
        s.commit()
        return {"clip": clip.id, "brand": brand.id, "account": acc.id, "render": r.id}


# ---------------------------------------------------------------- flows


def test_gate_help_status(env):
    async def scenario(phone, tg, bot):
        await phone.say("/start", chat=999)  # anyone else: nothing, not even a reply
        await phone.say("/start " + "c" * 22, chat=999)  # nor a pairing code that isn't this bot's
        assert tg.calls == []
        await phone.say("/start")
        assert "everything the web app does" in phone.text()
        await phone.say("/start " + "c" * 22)  # its own chat with a stale code: help, as ever
        assert "everything the web app does" in phone.text()
        await phone.say("/nonsense")
        assert "don't know that command" in phone.text()
        await phone.say("/status")
        assert phone.text().startswith("<b>Worker offline.</b>")  # no worker listens on clipper_test
        await phone.say("/c99999999")
        assert phone.text() == "Clip 99999999 not found."
        await phone.say("hello there")
        assert "Send a video, a link or a document" in phone.text()

    run(scenario)


def test_imports(env):
    async def scenario(phone, tg, bot):
        # one link, with the creator next to it
        await phone.say("look https://www.tiktok.com/@maya.eats/video/7421983301999?is_from_webapp=1 by @maya.eats")
        assert "Import this TikTok by @maya.eats?" in phone.text()
        await phone.tap("Import")
        with SyncSession() as s:
            c = s.scalars(select(SourceClip).order_by(SourceClip.id.desc())).first()
        assert (c.status, c.source_creator_handle, c.source_url) == (
            "DOWNLOADING", "@maya.eats", "https://www.tiktok.com/@maya.eats/video/7421983301999")
        assert job("download_clip", clip_id=c.id) and ("clip", c.id) in bot.watches
        # the same video again: its card, no second clip
        await phone.say("https://www.tiktok.com/@maya.eats/video/7421983301999")
        assert f"clip {c.id}" in phone.text() and "Already in the library" in phone.text(max(tg.messages) - 1)

        # a document: one known, two new, a repeat and a page that isn't a video
        tg.files["doc1"] = (b"https://youtu.be/aaaaaaaaaaa https://www.instagram.com/reel/BBB222xyz/ "
                            b"https://www.tiktok.com/@maya.eats/video/7421983301999 https://youtu.be/aaaaaaaaaaa https://example.com")  # fmt: skip
        await phone.say(document={"file_id": "doc1", "file_name": "links.txt", "mime_type": "text/plain", "file_size": 200})
        assert "Found 3 videos (YouTube 1 · Instagram 1 · TikTok 1): 1 already in the library, 1 repeat, 1 other link skipped." in phone.text()
        await phone.tap("Import")
        with SyncSession() as s:
            new = s.scalars(select(SourceClip).where(SourceClip.id > c.id)).all()
        assert sorted(x.source_url for x in new) == ["https://www.instagram.com/reel/BBB222xyz/", "https://youtu.be/aaaaaaaaaaa"]
        batch = next(w for (kind, _), w in bot.watches.items() if kind == "batch")
        assert sorted(batch["ids"]) == sorted(x.id for x in new)
        # the batch reports once, when every clip has finished
        with SyncSession() as s:
            s.execute(update(SourceClip).where(SourceClip.id == new[0].id).values(status="READY"))
            s.commit()
        sent = len(tg.messages)
        await screens.check_watches(bot)
        assert not any("Import finished" in m["text"] for m in list(tg.messages.values())[sent:])
        with SyncSession() as s:
            s.execute(update(SourceClip).where(SourceClip.id == new[1].id).values(status="FAILED", error_code="PRIVATE"))
            s.commit()
        await screens.check_watches(bot)
        done = [m["text"] for m in tg.messages.values() if "Import finished" in m["text"]]
        assert done and "1 of 2 ready, 1 failed" in done[0] and "Private video" in done[0]

        # a video (an album of two: one question), too big, and a photo
        tg.files["v1"], tg.files["v2"] = b"video one", b"video two"
        await phone.say(video={"file_id": "v1", "file_size": 9, "mime_type": "video/mp4"}, caption="@creator", media_group_id="g")
        await phone.say(video={"file_id": "v2", "file_size": 9, "mime_type": "video/quicktime", "file_name": "IMG_1.MOV"}, media_group_id="g")
        assert "Import 2 videos (0.0 MB) by @creator?" in phone.text()
        await phone.tap("Import")
        await phone.settle()
        with SyncSession() as s:
            up = s.scalars(select(SourceClip).where(SourceClip.origin == "upload").order_by(SourceClip.id)).all()[-2:]
        assert [(x.status, x.source_creator_handle) for x in up] == [("PROBING", "@creator")] * 2
        assert up[0].original_filename.startswith("telegram-") and up[0].original_filename.endswith(".mp4")
        assert up[1].original_filename == "IMG_1.MOV" and job("probe_clip", clip_id=up[1].id)
        assert "Uploaded 2 videos" in phone.text(min(m for m, x in tg.messages.items() if "Uploaded" in x["text"]))
        await phone.say(video={"file_id": "big", "file_size": 25 * 1024**2, "mime_type": "video/mp4"})
        assert "Telegram lets bots download files up to 20 MB" in phone.text()
        await phone.say(photo=[{"file_id": "p"}])
        assert "That's a photo" in phone.text()

    run(scenario)


def test_render_editor(env):
    ids = seed(env)
    with SyncSession() as s:  # no render yet: the editor asks for a brand
        s.execute(text("DELETE FROM renders WHERE id = :r"), {"r": ids["render"]})
        s.commit()

    async def scenario(phone, tg, bot):
        await phone.say(f"/c{ids['clip']}")
        assert "00:20 · 1920x1080 · 30fps · audio · 5.0 MB" in phone.text()
        await phone.tap("Render…")
        editor = max(tg.messages)
        assert "Brand: <b>pick one below</b>" in phone.text(editor)
        await phone.tap("Northwind", editor)
        assert "Logo: Top right · 22% of the width · opacity 100% (brand default)" in phone.text(editor)
        assert "Coffee at nw.coffee · clip by @maya" in phone.text(editor)  # the template, filled
        await phone.tap("↗", editor)  # top right, 4 % margin, the logo's real shape (400x160)
        await phone.tap("Crop: centre", editor)
        assert "Crop: left" in phone.text(editor)
        await phone.tap("Opacity 100%", editor)
        await phone.tap("Caption", editor)
        await phone.say("Big news #coffee")
        assert "Big news #coffee" in phone.text(editor) and "1/30 hashtags" in phone.text(editor)
        toast = await phone.tap("Render", editor)
        with SyncSession() as s:
            r = s.scalars(select(Render).where(Render.source_clip_id == ids["clip"])).one()
        assert toast == f"Render #{r.id} queued"
        assert (r.status, r.brand_id, r.caption) == ("PENDING", ids["brand"], "Big news #coffee")
        o = r.overlay_config
        assert close((o["x"], o["y"], o["w"], o["opacity"]), (0.68, 0.1625, 0.22, 0.75))
        assert close([r.crop_config[k] for k in "xywh"], [0, 0, 1080 * 9 / 16 / 1920, 1])
        assert job("render", render_id=r.id) and ("render", r.id) in bot.watches
        assert await phone.tap("Render", editor) == "Already queued"  # a double tap is one render
        # save the placement as the brand's default
        await phone.tap("↙", editor)
        await phone.tap("Save as default", editor)
        with SyncSession() as s:
            d = s.get(Brand, ids["brand"]).default_overlay_config
        assert close((d["x"], d["y"]), fmt.snap(6, 0.22, 0.0495))
        # the render finishes: its card arrives, with Schedule and Post now
        with SyncSession() as s:
            s.execute(update(Render).where(Render.id == r.id).values(status="READY", duration_s=20.0, size_bytes=1))
            s.commit()
        await screens.check_watches(bot)
        assert f"<b>Render #{r.id}</b> · Northwind · Top right · 22% · 9:16 crop" in phone.text()  # as rendered
        assert [t for t, _ in tg.buttons(max(tg.messages))] == ["Watch", "Schedule…", "Post now", "Delete"]
        # a caption over Instagram's limits never reaches the api
        await phone.tap("Caption", editor)
        await phone.say(" ".join(f"#t{i}" for i in range(31)))
        assert "Instagram refuses captions" in phone.text() and bot.prompt is not None
        await phone.say("/cancel")
        assert bot.prompt is None

    run(scenario)


def test_schedule_and_posts(env, monkeypatch):
    ids = seed(env)
    other = seed(env)

    async def scenario(phone, tg, bot):
        bot.last_account = ids["account"]
        await phone.say(f"/r{ids['render']}")
        await phone.tap("Schedule…")
        form = max(tg.messages)
        assert "the next free slot" in phone.text(form) and "Publishing is off" in phone.text(form)
        assert await phone.tap("Schedule for", form) == "Saved as a draft"
        with SyncSession() as s:
            p = s.scalars(select(Post).where(Post.render_id == ids["render"])).one()
        assert (p.status, p.caption) == ("DRAFT", "hello #ad")
        assert "<b>Saved as a draft</b>" in phone.text(form)
        assert (await phone.tap("Approve", form)).startswith("Approved: goes out")
        with SyncSession() as s:
            assert s.get(Post, p.id).status == "SCHEDULED"
        assert "Scheduled to publish" in phone.text(form)  # the result became the post's card
        # move to tomorrow's first slot, caption, then publishing off refuses Post now
        await phone.tap("Move…", form)
        await phone.tap("Tomorrow", form)
        toast = await phone.tap("09:00", form)
        tomorrow = (datetime.now(UTC) + timedelta(days=1)).date()
        with SyncSession() as s:
            assert s.get(Post, p.id).scheduled_for == datetime(tomorrow.year, tomorrow.month, tomorrow.day, 9, tzinfo=UTC)
        assert toast.startswith("Moved to")
        await phone.tap("Caption", form)
        await phone.say("new words")
        with SyncSession() as s:
            assert s.get(Post, p.id).caption == "new words"
        assert await phone.tap("Post now", form) is not None and tg.toasts[-1] == (
            "Publishing is off (PUBLISHING_ENABLED is off on the server): nothing can post now.", True)
        # a typed time, then no confirm step: Schedule saves it
        await phone.say(f"/r{other['render']}")
        await phone.tap("Schedule…")
        form2 = max(tg.messages)
        await phone.tap("Other time…", form2)
        await phone.tap("Type a time…", form2)
        await phone.say("tomorrow 13:00")
        assert "tomorrow" not in phone.text(form2) and "13:00" in phone.text(form2)
        assert await phone.tap("Schedule for", form2) == "Saved as a draft"
        with SyncSession() as s:
            p2 = s.scalars(select(Post).where(Post.render_id == other["render"])).one()
        assert p2.scheduled_for.hour == 13 and p2.status == "DRAFT"
        # cancel it from its card
        await phone.say(f"/p{p2.id}")
        await phone.tap("Cancel post")
        await phone.tap("Cancel the post")
        with SyncSession() as s:
            assert s.get(Post, p2.id).status == "CANCELLED"
        # Post now with publishing on (a key that can't reach Zernio: nothing here may call it)
        monkeypatch.setattr(settings, "PUBLISHING_ENABLED", True)
        zernio_key(1, "sk_test_never_sent")

        async def no_quota(key, zernio_account_id):
            return None

        monkeypatch.setattr(zernio, "publishing_limit", no_quota)
        monkeypatch.setattr(zernio, "client", lambda key, **kw: pytest.fail("Zernio called"))
        await phone.say(f"/p{p.id}")
        card = max(tg.messages)
        await phone.tap("Post now", card)
        confirm = next(d for t, d in tg.buttons(card) if t == "Post to @acct now")
        assert await phone.press(confirm, card) == "Posting now: live within about a minute"
        with SyncSession() as s:
            q = s.get(Post, p.id)
        assert q.status == "SCHEDULED" and abs(q.scheduled_for - datetime.now(UTC)) < timedelta(seconds=30)
        assert ("post", p.id) in bot.watches
        assert await phone.press(confirm, card) == "Already done."  # a double tap posts once
        # the watch reports it live, once
        with SyncSession() as s:
            s.execute(update(Post).where(Post.id == p.id).values(status="PUBLISHED", permalink="https://www.instagram.com/reel/X/", published_at=datetime.now(UTC)))
            s.commit()
        await screens.check_watches(bot)
        assert phone.text().startswith("<b>Live on Instagram</b>: @acct, post") and ("post", p.id) not in bot.watches
        assert tg.buttons(max(tg.messages)) == [("View on Instagram", "https://www.instagram.com/reel/X/")]
        # Post now from a render card: one confirm per account, then the post at this second, approved
        await phone.say(f"/r{other['render']}")
        card = max(tg.messages)
        await phone.tap("Post now", card)
        confirm = f"rn!:{other['render']}:{other['account']}"
        assert ("Post to @acct now", confirm) in tg.buttons(card)
        assert await phone.press(confirm, card) == "Posting now"
        with SyncSession() as s:
            p3 = s.scalars(select(Post).where(Post.render_id == other["render"], Post.status != "CANCELLED")).one()
        assert p3.status == "SCHEDULED" and ("post", p3.id) in bot.watches

    run(scenario)


def test_recover_ready_drafts(env):
    ids = seed(env)
    more = seed(env, auto_approve=False)
    third = seed(env)

    async def scenario(phone, tg, bot):
        with SyncSession() as s:  # a failure nothing was ever POSTed for: Retry now reschedules it at once
            p = Post(render_id=ids["render"], account_id=ids["account"], caption="c", status="FAILED", error_code="NETWORK_ERROR",
                     scheduled_for=datetime.now(UTC) - timedelta(hours=1), idempotency_key=f"k{ids['render']}")  # fmt: skip
            s.add(p)
            s.commit()
        await phone.say("/failed")
        assert f"/p{p.id}" in phone.text() and "Couldn't reach Instagram" in html.unescape(phone.text())
        await phone.say(f"/p{p.id}")
        assert "Publishing failed" in phone.text() and "three retries in a row" in phone.text()
        assert [t for t, _ in tg.buttons(max(tg.messages))] == ["Retry now", "Details", "Dismiss"]
        await phone.tap("Details")
        assert "restarts 0/3" in phone.text()
        await phone.tap("Retry now")
        with SyncSession() as s:
            assert s.get(Post, p.id).status == "SCHEDULED"
        # KEY_CHANGED: the first POST went out under the previous key, so the Reel may be live. Fix asks first
        with SyncSession() as s:
            k = Post(render_id=third["render"], account_id=third["account"], caption="c", status="DEAD_LETTER", error_code="KEY_CHANGED",
                     scheduled_for=datetime.now(UTC) - timedelta(hours=1), idempotency_key=f"k{third['render']}",
                     first_post_at=datetime.now(UTC) - timedelta(hours=1), key_gen=1)  # fmt: skip
            s.add(k)
            s.commit()
        await phone.say(f"/p{k.id}")
        assert "Sent with your previous Zernio key" in phone.text()
        card = max(tg.messages)
        assert "Instagram first" in await phone.tap("Re-render and retry", card)
        with SyncSession() as s:
            assert s.get(Post, k.id).status == "DEAD_LETTER"
        await phone.tap("I checked: re-render", card)
        with SyncSession() as s:
            k = s.get(Post, k.id)
            assert k.status == "SCHEDULED" and k.render_id != third["render"] and job("render", render_id=k.render_id)
        # the ready tray: select one render, auto-schedule it
        bot.last_account = more["account"]
        await phone.say("/ready")
        tray = max(tg.messages)
        assert f"/r{more['render']}" in phone.text(tray)
        await phone.tap(f"☐ #{more['render']}", tray)
        assert "1 selected" in phone.text(tray)
        assert await phone.tap("Auto-schedule 1", tray) == "Placed 1"
        assert "<b>Placed 1 on @acct</b>" in phone.text(tray) and "Draft" in phone.text(tray)
        # approve every draft, after seeing the list
        await phone.say("/drafts")
        drafts = max(tg.messages)
        await phone.tap("Approve all", drafts)
        assert "<b>Approve" in phone.text(drafts)
        await phone.tap("Approve", drafts)
        with SyncSession() as s:
            assert not s.scalars(select(Post).where(Post.status == "DRAFT", Post.account_id == more["account"])).all()
        await phone.say("/calendar")
        assert "Scheduled · Northwind" in phone.text() and "<i>free</i> " in phone.text()  # free slots: one line a day

    run(scenario)


def test_accounts_and_brands(env):
    ids = seed(env)

    async def scenario(phone, tg, bot):
        await phone.say("/accounts")
        assert f"/a{ids['account']}" in phone.text()
        await phone.say(f"/a{ids['account']}")
        card = max(tg.messages)
        await phone.tap("Slots", card)
        assert await phone.tap("Every 30 min", card) == "Posting times: Every 30 min, 07:00–23:30"
        with SyncSession() as s:
            assert s.get(Account, ids["account"]).posting_slots == {"times": fmt.every(30, "07:00", "23:30")}
        assert "slots every 30 min, 07:00–23:30" in phone.text(card)
        await phone.tap("Slots", card)
        await phone.tap("Type times…", card)
        await phone.say("8:00, 20:30")
        await phone.tap("Timezone", card)
        await phone.say("Mars/Base")
        assert "unknown timezone" in phone.text() and bot.prompt is not None  # asked again
        await phone.say("Europe/Paris")
        with SyncSession() as s:
            a = s.get(Account, ids["account"])
        assert (a.posting_slots, a.timezone) == ({"times": ["08:00", "20:30"]}, "Europe/Paris")
        await phone.tap("Disable", card)
        await phone.tap("Disable @acct", card)
        with SyncSession() as s:
            assert s.get(Account, ids["account"]).disabled_at is not None
        assert "Disabled in Clipper" in phone.text(card)
        # a new brand, a logo sent the wrong way and then right, a template, auto-approve
        await phone.say("/brands")
        await phone.tap("New brand")
        await phone.say("Nova")
        await phone.say(photo=[{"file_id": "ph"}])
        assert "came as a photo" in phone.text()
        tg.files["png"] = rgba_png(8, 4)
        await phone.say(document={"file_id": "png", "file_name": "nova.png", "mime_type": "image/png", "file_size": 100})
        with SyncSession() as s:
            b = s.scalars(select(Brand).where(Brand.name == "Nova")).one()
        assert b.logo_key and (env / b.logo_key).read_bytes() == rgba_png(8, 4)
        card = max(tg.messages)
        assert "<b>Nova</b>" in phone.text(card)
        await phone.tap("Template", card)
        await phone.say("Try {link} · by {creator}")
        await phone.tap("Auto-approve off", card)
        with SyncSession() as s:
            b = s.get(Brand, b.id)
        assert (b.caption_template, b.auto_approve) == ("Try {link} · by {creator}", True)
        await phone.tap("Placement", card)
        await phone.tap("↓")
        await phone.tap("Save")
        with SyncSession() as s:
            o = s.get(Brand, b.id).default_overlay_config
        assert close((o["x"], o["y"]), fmt.snap(7, 0.22, 0.22 * fmt.logo_aspect((8, 4))))

    run(scenario)


def test_customizations(env):
    """The user's Customizations in the bot: brands, saved captions and saved covers to see, and renders that start
    from the defaults (brand, caption, cover), as the web Editor's do."""
    ids = seed(env)
    neon, calm = b"\xff\xd8\xff\xe0neon", b"\xff\xd8\xff\xe0calm"  # the JPEG magic is all the api checks
    with SyncSession() as s:
        s.execute(text("DELETE FROM renders WHERE id = :r"), {"r": ids["render"]})  # a clip never rendered
        for model in (Brand, SavedCaption, SavedCover):  # defaults other tests left
            s.execute(update(model).where(model.user_id == 1, model.is_default).values(is_default=False))
        flux = Brand(name="Flux", link="flux.gg", is_default=True)  # no caption template
        late = SavedCaption(name="Late night", text="Night fuel → {link} · clip by {creator}", is_default=True)
        plain = SavedCaption(name="Plain", text="Just #ad")
        covers = [SavedCover(name="Neon", image_key="cover-library/neon.jpg", is_default=True),
                  SavedCover(name="Calm", image_key="cover-library/calm.jpg")]  # fmt: skip
        s.add_all([flux, late, plain, *covers])
        s.flush()
        flux.logo_key = f"logos/{flux.id}.png"
        s.commit()
    (env / flux.logo_key).write_bytes(rgba_png(400, 160))
    (env / "cover-library").mkdir()
    (env / "cover-library/neon.jpg").write_bytes(neon)
    (env / "cover-library/calm.jpg").write_bytes(calm)

    async def scenario(phone, tg, bot):
        await phone.say(f"/b{flux.id}")
        assert phone.text().startswith(f"<b>Flux</b> · brand {flux.id} · default")
        assert await phone.tap("View logo") == "Sending the logo…"
        await phone.settle()
        assert any(m == "sendDocument" and p["document"]["filename"] == f"logo-{flux.id}.png" for m, p in tg.calls)
        await phone.say("/captions")
        assert f"/t{late.id} · Late night · default · Night fuel → {{link}} · clip by {{creator}}" in phone.text()
        await phone.say(f"/t{plain.id}")
        assert "<b>Plain</b> · saved caption" in phone.text() and "<blockquote>Just #ad</blockquote>" in phone.text()
        await phone.say("/covers")
        assert f"/i{covers[0].id} · Neon · default" in phone.text() and f"/i{covers[1].id} · Calm" in phone.text()
        await phone.say(f"/i{covers[1].id}")
        assert tg.last()["photo"] and "<b>Calm</b> · saved cover" in phone.text()

        # the render editor starts from the defaults: no render of this clip yet, so the default brand
        await phone.say(f"/c{ids['clip']}")
        await phone.tap("Render…")
        editor = max(tg.messages)
        assert "Brand: <b>Flux</b>" in phone.text(editor) and "Cover: Neon (default)" in phone.text(editor)
        assert "<blockquote>Night fuel → flux.gg · clip by @maya</blockquote>" in phone.text(editor)  # Flux has no template
        await phone.tap("Saved captions", editor)
        await phone.tap("Plain", editor)
        assert "<blockquote>Just #ad</blockquote>" in phone.text(editor)
        await phone.tap("Cover: Neon", editor)
        await phone.tap("Calm", editor)
        assert "Cover: Calm" in phone.text(editor)
        toast = await phone.tap("Render", editor)
        await phone.settle()  # the cover's copy goes up in the background
        with SyncSession() as s:
            r = s.scalars(select(Render).where(Render.source_clip_id == ids["clip"])).one()
        assert toast == f"Render #{r.id} queued" and (r.brand_id, r.caption) == (flux.id, "Just #ad")
        assert r.cover_key.startswith("u/1/covers/") and (env / r.cover_key).read_bytes() == calm

        # Re-render for… a brand: its template, else the default caption, and the default cover
        with SyncSession() as s:
            p = Post(render_id=r.id, account_id=ids["account"], caption="c", status="PUBLISHED", idempotency_key=f"cust{r.id}",
                     scheduled_for=datetime.now(UTC) - timedelta(hours=1), published_at=datetime.now(UTC) - timedelta(hours=1),
                     permalink="https://www.instagram.com/reel/C/")  # fmt: skip
            s.add(p)
            s.commit()
        await phone.say(f"/p{p.id}")
        card = max(tg.messages)
        await phone.tap("Re-render for…", card)
        assert (await phone.tap("Flux (original)", card)).startswith("Render #")
        await phone.settle()
        with SyncSession() as s:
            again = s.scalars(select(Render).where(Render.source_clip_id == ids["clip"], Render.id != r.id)).one()
        assert (again.brand_id, again.caption) == (flux.id, "Night fuel → flux.gg · clip by @maya")
        assert (env / again.cover_key).read_bytes() == neon

    try:
        run(scenario)
    finally:
        with SyncSession() as s:  # later tests start without these defaults
            for model in (Brand, SavedCaption, SavedCover):
                s.execute(update(model).where(model.user_id == 1, model.is_default).values(is_default=False))
            s.commit()
