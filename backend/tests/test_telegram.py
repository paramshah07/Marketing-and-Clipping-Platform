"""Each user's Telegram bots: /api/me/bots (add with Telegram's checks, pair, alerts, test, remove), the bot
service's /api/internal/* (its bearer only: the list, the health report, pairing), the supervisor running every
bot as its owner, and user 1's one-shot .env import. Telegram is a fake Bot API (httpx.MockTransport) that knows
a few tokens; nothing reaches the real one. Row-level security for all of this: test_tenancy."""

import asyncio
import json
import os
import re
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from app import cli
from app.api import bots as bots_api
from app.api.auth import _sha
from app.bot import core
from app.bot.clients import Telegram
from app.bot.core import Supervisor
from app.core.config import settings
from app.core.db import SyncSession, engine
from app.core.secrets import seal, unseal
from app.main import app
from app.models import TelegramBot, User
from conftest import as_user, zernio_key

CHAT = 4242
BEARER = as_user(1)["Authorization"]


def token(n: int | None = None) -> str:
    """A token of a new bot (its id: n, or a random one)."""
    return f"{n or uuid.uuid4().int % 10**9 + 10**6}:AAE{uuid.uuid4().hex[:32]}"


class FakeTelegram:
    """The Bot API for the tokens it knows (401 for any other): getMe, getWebhookInfo (hooked: a webhook set),
    getUpdates from each bot's queue (a long poll waits a moment when there is nothing), sendMessage and the rest
    answered ok. Records every call as (token, method, params)."""

    def __init__(self, *tokens: str, hooked=()):
        self.known, self.hooked = set(tokens), set(hooked)
        self.queues: dict[str, list] = defaultdict(list)
        self.calls: list[tuple[str, str, dict]] = []
        self.refuse: dict[str, tuple[int, str]] = {}  # method -> (error code, description)
        self.n = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        tok, method = re.fullmatch(r"/bot([^/]+)/(\w+)", request.url.path).groups()
        p = json.loads(request.content or b"{}")
        self.calls.append((tok, method, p))
        if tok not in self.known:
            return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        if method in self.refuse:
            code, desc = self.refuse[method]
            return httpx.Response(code, json={"ok": False, "error_code": code, "description": desc})
        result = True
        if method == "getMe":
            result = {"id": int(tok.split(":")[0]), "is_bot": True, "username": f"bot{tok.split(':')[0]}"}
        elif method == "getWebhookInfo":
            result = {"url": "https://another.app/hook" if tok in self.hooked else "", "pending_update_count": len(self.queues[tok])}
        elif method == "getUpdates":
            q, offset = self.queues[tok], p.get("offset")
            if offset is not None:  # confirms (forgets) every update before it; -1: all but the newest
                q[:] = q[offset:] if offset < 0 else [u for u in q if u["update_id"] >= offset]
            if not q and p.get("timeout"):
                await asyncio.sleep(0.01)
            result = q[: p.get("limit") or 100]
        elif method.startswith("send"):
            result = {"message_id": len(self.calls), "chat": {"id": p.get("chat_id")}}
        return httpx.Response(200, json={"ok": True, "result": result})

    def say(self, tok: str, text: str, chat: int = CHAT, kind: str = "private") -> None:
        self.n += 1
        chat_ = {"id": chat, "type": kind, "first_name": "Op", "last_name": "Erator"}
        self.queues[tok].append({"update_id": self.n, "message": {"message_id": self.n, "chat": chat_, "text": text}})

    def sent(self, tok: str, method: str = "sendMessage") -> list[dict]:
        return [p for t, m, p in self.calls if t == tok and m == method]


@pytest.fixture
def tg(db, monkeypatch):
    """The fake Telegram behind the api's own calls (add, test); telegram_bots emptied afterwards."""
    fake = FakeTelegram()
    monkeypatch.setattr(bots_api, "telegram", lambda t: Telegram(t, httpx.MockTransport(fake)))
    yield fake
    with SyncSession() as s:
        s.execute(delete(TelegramBot))
        s.commit()


@contextmanager
def api(uid: int = 1):
    """The api in process as user uid (the bot service's path); another user per request: headers=as_user(n)."""
    with TestClient(app, headers=as_user(uid)) as c:
        yield c


def new_user(**values) -> int:
    with SyncSession() as s:
        u = User(username=f"t.{uuid.uuid4().hex[:10]}", **values)
        s.add(u)
        s.commit()
        return u.id


def row(id: int) -> TelegramBot | None:
    with SyncSession() as s:
        return s.get(TelegramBot, id)


def add_row(uid: int, tok: str, chat_id: int | None = None, code: str | None = None, **values) -> int:
    """A bot as POST /api/me/bots stores it (code: its pairing code, 15 min)."""
    if code:
        values |= {"pair_sha256": _sha(code), "pair_expires_at": datetime.now(UTC) + timedelta(minutes=15)}
    with SyncSession() as s:
        b = TelegramBot(user_id=uid, bot_id=int(tok.split(":")[0]), token_enc=seal(tok), chat_id=chat_id, **values)
        s.add(b)
        s.commit()
        return b.id


def code(r) -> str:
    return r.json()["detail"]["code"]


# ---------------------------------------------------------------- /api/me/bots


def test_add_pair_alerts_test_remove(tg):
    t = token()
    with api() as c:
        assert code(c.post("/api/me/bots", json={"token": "nonsense"})) == "TOKEN_REJECTED"
        assert code(c.post("/api/me/bots", json={"token": t})) == "TOKEN_REJECTED"  # Telegram: 401
        tg.known.add(t)
        tg.hooked.add(t)
        assert code(c.post("/api/me/bots", json={"token": t})) == "BOT_IN_USE"  # another app's webhook: not stolen
        tg.hooked.clear()
        r = c.post("/api/me/bots", json={"token": t})
        assert r.status_code == 201, r.text
        added, bid = r.json(), r.json()["bot"]["id"]
        name = f"bot{t.split(':')[0]}"
        pair_code = added["start"].removeprefix("/start ")
        assert added["pair_url"] == f"https://t.me/{name}?start={pair_code}" and len(pair_code) == 22
        assert added["bot"] | {"created_at": None} == {"id": bid, "username": name, "chat_title": None, "alerts": True,
                                                        "health": "waiting", "pairing": True, "last_seen_at": None,
                                                        "created_at": None}  # fmt: skip
        assert t not in r.text and t.split(":")[1] not in c.get("/api/me/bots").text  # never sent back
        assert unseal(row(bid).token_enc) == t and row(bid).pair_sha256 == _sha(pair_code)
        me = c.get("/api/me").json()
        assert [b["id"] for b in me["bots"]] == [bid] and me["setup"]["telegram"] is False
        assert code(c.post(f"/api/me/bots/{bid}/test")) == "BOT_NOT_PAIRED"

        pair = f"/api/internal/bots/{bid}/pair"
        assert c.post(pair, json={"code": pair_code, "chat_id": CHAT, "chat_title": "Op Erator"}).status_code == 204
        assert code(c.post(pair, json={"code": pair_code, "chat_id": 1})) == "PAIR_CODE_INVALID"  # used up
        b = c.get("/api/me/bots").json()[0]
        assert (b["chat_title"], b["health"], b["pairing"]) == ("Op Erator", "not_responding", False)  # no report yet
        assert c.get("/api/me").json()["setup"]["telegram"] is True

        assert c.post(f"/api/me/bots/{bid}/test").json() == {"ok": True, "error": None}
        assert tg.sent(t)[-1]["chat_id"] == CHAT and "sends your failure alerts" in tg.sent(t)[-1]["text"]
        assert c.patch(f"/api/me/bots/{bid}", json={"alerts": False}).json()["alerts"] is False
        assert row(bid).alerts is False
        tg.refuse["sendMessage"] = (403, "Forbidden: bot was blocked by the user")
        assert c.post(f"/api/me/bots/{bid}/test").json() == {"ok": False, "error": "Telegram 403: Forbidden: bot was blocked by the user"}

        # Re-pair: a new code; the chat answers until another chat uses it
        new = c.post(f"/api/me/bots/{bid}/pair").json()
        new_code = new["start"].removeprefix("/start ")
        assert new_code != pair_code and new["bot"]["pairing"] is True and row(bid).chat_id == CHAT
        assert c.post(pair, json={"code": new_code, "chat_id": 777}).status_code == 204
        assert (row(bid).chat_id, row(bid).chat_title, row(bid).pair_sha256) == (777, None, None)

        # @BotFather /revoke: the same bot with a new token keeps its chat and needs no pairing
        t2 = f"{t.split(':')[0]}:AAE{uuid.uuid4().hex[:32]}"
        tg.known.add(t2)
        with SyncSession() as s:
            s.execute(update(TelegramBot).where(TelegramBot.id == bid).values(error="TOKEN_REJECTED"))
            s.commit()
        again = c.post("/api/me/bots", json={"token": t2}).json()
        assert (again["bot"]["id"], again["pair_url"], again["start"], again["bot"]["health"]) == (bid, None, None, "not_responding")
        assert (unseal(row(bid).token_enc), row(bid).chat_id, row(bid).error) == (t2, 777, None)

        assert c.delete(f"/api/me/bots/{bid}").status_code == 204
        assert row(bid) is None and c.get("/api/me/bots").json() == []


def test_one_bot_one_user(tg):
    """A bot is one user's: another user adding it gets 409 BOT_TAKEN, every route on it answers 404, and its code
    pairs nothing as them."""
    t = token()
    tg.known.add(t)
    other = new_user()
    with api() as c:
        added = c.post("/api/me/bots", json={"token": t}).json()
        bid, pair_code = added["bot"]["id"], added["start"].removeprefix("/start ")
        them = as_user(other)
        assert code(c.post("/api/me/bots", json={"token": t}, headers=them)) == "BOT_TAKEN"
        assert c.get("/api/me/bots", headers=them).json() == [] and c.get("/api/me", headers=them).json()["bots"] == []
        for method, path, body in [("PATCH", "", {"alerts": False}), ("POST", "/pair", None), ("POST", "/test", None),
                                   ("DELETE", "", None)]:  # fmt: skip
            assert c.request(method, f"/api/me/bots/{bid}{path}", json=body, headers=them).status_code == 404, path
        r = c.post(f"/api/internal/bots/{bid}/pair", json={"code": pair_code, "chat_id": 1}, headers=them)
        assert code(r) == "PAIR_CODE_INVALID"
    assert (row(bid).chat_id, row(bid).alerts, row(bid).pair_sha256) == (None, True, _sha(pair_code))


# ---------------------------------------------------------------- /api/internal/*


def test_internal_endpoints_are_the_bot_services_only(tg, monkeypatch):
    monkeypatch.setattr(settings, "APP_BASE_URL", "http://localhost:5173")
    bid = add_row(1, token(), CHAT)
    paths = [("GET", "/api/internal/bots", None), ("POST", "/api/internal/bots/report", []),
             ("POST", f"/api/internal/bots/{bid}/pair", {"code": "x" * 22, "chat_id": 1})]  # fmt: skip
    with TestClient(app, headers={"Origin": "http://localhost:5173"}) as c:  # this site: past the CSRF check
        for method, path, body in paths:
            assert c.request(method, path, json=body).status_code == 401, path  # no bearer
            for auth in ("Bearer wrong", "Basic Y2xpcHBlcjpwdw==", f"{BEARER}x"):
                r = c.request(method, path, json=body, headers={"Authorization": auth, "X-Clipper-User": "1"})
                assert (r.status_code, code(r)) == (403, "BOT_SERVICE_ONLY"), (path, auth)
        assert c.get("/api/internal/bots", headers={"Authorization": BEARER}).status_code == 200  # the service
    assert "/api/internal/bots" not in app.openapi()["paths"]  # not in the web app's client


def test_supervisor_list_and_report(tg, monkeypatch):
    """The list: runnable bots with their tokens and owners, never a rejected token's or a disabled user's. The
    report: last_seen_at and the @name of bots polling fine, TOKEN_REJECTED for refused ones, and only for the token
    the supervisor ran (ver)."""
    other, gone = new_user(), new_user(disabled_at=datetime.now(UTC))
    t1, t2, t3, t4 = token(), token(), token(), token()
    paired, waiting = add_row(1, t1, CHAT), add_row(other, t2)
    add_row(1, t3, error="TOKEN_REJECTED")
    add_row(gone, t4, CHAT)
    with TestClient(app, headers={"Authorization": BEARER}) as c:
        listed = {b["id"]: b for b in c.get("/api/internal/bots").json()}
        assert set(listed) == {paired, waiting}
        assert {k: v for k, v in listed[paired].items() if k != "ver"} == {"id": paired, "user_id": 1, "token": t1, "chat_id": CHAT}
        assert listed[waiting]["chat_id"] is None and listed[waiting]["user_id"] == other
        ver = listed[paired]["ver"]
        report = [{"id": paired, "ver": ver, "username": "op_bot"}, {"id": waiting, "ver": "stale", "error": "TOKEN_REJECTED"}]
        assert c.post("/api/internal/bots/report", json=report).status_code == 204
        assert (row(paired).username, row(paired).last_seen_at is not None, row(waiting).error) == ("op_bot", True, None)
        assert c.get("/api/me/bots", headers=as_user(1)).json()[0]["health"] == "running"
        assert c.post("/api/internal/bots/report", json=[{"id": paired, "ver": ver, "error": "GONE"}]).status_code == 422
        c.post("/api/internal/bots/report", json=[{"id": waiting, "ver": listed[waiting]["ver"], "error": "TOKEN_REJECTED"}])
        assert row(waiting).error == "TOKEN_REJECTED" and {b["id"] for b in c.get("/api/internal/bots").json()} == {paired}
        assert c.get("/api/me/bots", headers=as_user(other)).json()[0]["health"] == "rejected"
        monkeypatch.setattr(settings, "SECRETS_KEY", "")  # tokens this api can't open: nothing to run
        assert c.get("/api/internal/bots").json() == []


def test_pairing_codes(tg):
    """The right, unexpired code of that bot, as its owner: that chat is the bot's. Anything else changes nothing."""
    bid = add_row(1, token(), code="k" * 22)
    pair = f"/api/internal/bots/{bid}/pair"
    with api() as c:
        assert code(c.post(pair, json={"code": "w" * 22, "chat_id": CHAT})) == "PAIR_CODE_INVALID"
        with SyncSession() as s:
            s.execute(update(TelegramBot).where(TelegramBot.id == bid).values(pair_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
            s.commit()
        assert code(c.post(pair, json={"code": "k" * 22, "chat_id": CHAT})) == "PAIR_CODE_INVALID"  # expired
        assert row(bid).chat_id is None
        with SyncSession() as s:
            s.execute(update(TelegramBot).where(TelegramBot.id == bid).values(pair_expires_at=datetime.now(UTC) + timedelta(minutes=1)))
            s.commit()
        assert c.post(pair, json={"code": "k" * 22, "chat_id": CHAT, "chat_title": "Op"}).status_code == 204
        assert (row(bid).chat_id, row(bid).chat_title, row(bid).pair_sha256, row(bid).pair_expires_at) == (CHAT, "Op", None, None)


# ---------------------------------------------------------------- the supervisor


async def until(check, seconds: float = 3) -> None:
    for _ in range(int(seconds / 0.01)):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


class Api(httpx.AsyncBaseTransport):
    """The api in process, or unreachable while down."""

    def __init__(self):
        self.asgi, self.down = httpx.ASGITransport(app=app), False

    async def handle_async_request(self, request):
        if self.down:
            raise httpx.ConnectError("down")
        return await self.asgi.handle_async_request(request)


def supervise(scenario, fake: FakeTelegram) -> None:
    """scenario(sup) on one event loop, the api in process (its lifespan), Telegram the fake; every bot stopped after."""

    async def main():
        await engine.dispose()  # pooled connections of another test's loop
        sup = Supervisor("http://api", Api(), httpx.MockTransport(fake))
        try:
            async with app.router.lifespan_context(app):
                await scenario(sup)
        finally:
            tasks = [task for _, task, _ in sup.bots.values()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await engine.dispose()

    asyncio.run(main())


def test_supervisor_runs_pairs_restarts_and_stops_bots(tg):
    other = new_user()
    ta, tb, ta2, tc = token(), token(), token(), token()
    tg.known |= {ta, tb, ta2}  # tc: Telegram refuses it
    a = add_row(1, ta, CHAT)  # paired
    b = add_row(other, tb, code="p" * 22)  # waiting for /start <code>
    tg.say(ta, "/status")  # queued while the service was down: a paired bot drops it
    tg.say(tb, "/start " + "p" * 22, chat=31, kind="group")  # a group: never pairs
    tg.say(tb, "/start " + "w" * 22, chat=32)  # a wrong code
    tg.say(tb, "/start " + "p" * 22, chat=33)  # the one: kept while the bot was down

    async def scenario(sup):
        await sup.tick()
        assert set(sup.bots) == {a, b}
        bot_a, bot_b = sup.bots[a][2], sup.bots[b][2]
        assert (bot_a.api.http.headers["x-clipper-user"], bot_b.api.http.headers["x-clipper-user"]) == ("1", str(other))
        await until(lambda: bot_b.chat == "33")
        assert (row(b).chat_id, row(b).chat_title, row(b).pair_sha256) == (33, "Op Erator", None)
        assert [p["chat_id"] for p in tg.sent(tb)] == ["33"]  # "Paired", nothing to the group or the wrong code's chat
        assert "Paired" in tg.sent(tb)[0]["text"]
        assert tg.sent(tb, "setMyCommands")[0]["scope"] == {"type": "chat", "chat_id": 33}
        await until(lambda: tg.sent(ta))
        assert [p["text"][:40] for p in tg.sent(ta)] == ["Back online. 1 message sent while I was "]  # the /status: dropped
        assert tg.sent(ta, "setMyCommands")[0]["scope"] == {"type": "chat", "chat_id": CHAT}
        task_a, task_b = sup.bots[a][1], sup.bots[b][1]
        await until(lambda: bot_a.alive and bot_b.alive)

        await sup.tick()  # the report; pairing restarted nothing (no stale drop, no "Back online")
        assert (sup.bots[a][1], sup.bots[b][1]) == (task_a, task_b)
        assert (row(a).username, row(b).username) == (f"bot{ta.split(':')[0]}", f"bot{tb.split(':')[0]}")
        assert row(a).last_seen_at is not None and row(b).last_seen_at is not None

        tg.say(tb, "/help", chat=33)  # the paired chat is answered
        await until(lambda: len(tg.sent(tb)) == 2)
        assert "everything the web app does" in tg.sent(tb)[1]["text"]
        tg.say(tb, "/help", chat=34)  # another chat is not
        await asyncio.sleep(0.1)
        assert len(tg.sent(tb)) == 2
        tg.say(tb, "/status", chat=33)  # the api answers it, as its owner
        await until(lambda: len(tg.sent(tb)) == 3)
        assert " rendering · " in tg.sent(tb)[2]["text"] and "API offline" not in tg.sent(tb)[2]["text"]

        with SyncSession() as s:  # a's new token (same bot): restarted with it; b removed: stopped
            s.execute(update(TelegramBot).where(TelegramBot.id == a).values(token_enc=seal(ta2)))
            s.execute(delete(TelegramBot).where(TelegramBot.id == b))
            s.commit()
        await sup.tick()
        assert set(sup.bots) == {a} and sup.bots[a][0] == ta2 and sup.bots[a][1] is not task_a
        await asyncio.gather(task_a, task_b, return_exceptions=True)
        assert task_a.cancelled() and task_b.cancelled()
        await until(lambda: tg.sent(ta2, "getUpdates"))  # polling with the new token

        c = add_row(1, tc, CHAT)  # a token Telegram refuses: reported, then no longer run
        await sup.tick()
        bot_c = sup.bots[c][2]
        await until(lambda: bot_c.rejected)
        await sup.tick()
        assert row(c).error == "TOKEN_REJECTED" and set(sup.bots) == {a}
        async with httpx.AsyncClient(transport=sup.transport, base_url="http://api", headers=as_user(1)) as me:
            assert {x["id"]: x["health"] for x in (await me.get("/api/me/bots")).json()}[c] == "rejected"

    supervise(scenario, tg)


def test_a_bot_its_chat_refuses_still_polls_and_follows_the_list(tg):
    """Kicked from its chat (Telegram refuses the menu and "Back online" there): it polls anyway, so a Re-pair can move
    it. And the chat is the list's: one changed in the database (a pairing whose answer never reached the bot) is the
    one it answers from the next tick, without a restart."""
    t, code_ = token(), "k" * 22
    tg.known.add(t)
    a = add_row(1, t, CHAT, code=code_)  # paired, a Re-pair's code out
    tg.say(t, "/help")  # queued while the service was down: "Back online" goes to the chat that refuses it
    tg.refuse |= {"sendMessage": (403, "Forbidden: bot was kicked from the group chat"),
                  "setMyCommands": (400, "Bad Request: chat not found")}  # fmt: skip

    async def scenario(sup):
        await sup.tick()
        bot, task = sup.bots[a][2], sup.bots[a][1]
        await until(lambda: any(tok == t and m == "getUpdates" and p.get("timeout") == 50 for tok, m, p in tg.calls))
        assert not task.done()
        tg.say(t, "/start " + code_, chat=35)  # the Re-pair, from a private chat
        await until(lambda: bot.chat == "35")
        assert row(a).chat_id == 35
        with SyncSession() as s:
            s.execute(update(TelegramBot).where(TelegramBot.id == a).values(chat_id=36))
            s.commit()
        await sup.tick()
        assert (bot.chat, sup.bots[a][1]) == ("36", task)

    supervise(scenario, tg)


def test_supervisor_keeps_bots_while_the_api_is_down(tg):
    t = token()
    tg.known.add(t)
    a = add_row(1, t, CHAT)

    async def scenario(sup):
        await sup.tick()
        task = sup.bots[a][1]
        sup.transport.down = True
        await sup.tick()
        assert sup.bots[a][1] is task and not task.done()

    supervise(scenario, tg)


def test_service_off_without_its_secret(monkeypatch):
    monkeypatch.setattr(settings, "BOT_SERVICE_SECRET", "")
    assert asyncio.run(core.main()) == 0


# ---------------------------------------------------------------- the one-shot .env import


def test_env_import_brings_the_operators_bots(tg, monkeypatch, capsys):
    """bootstrap seals TELEGRAM_BOT_TOKEN (alerts on) and _2, _3 (alerts off) into user 1, paired with their chats,
    once: stamped, it never reads them again; a bot already there is left as it is."""
    zernio_key(1, None, env_imported_at=None)
    monkeypatch.setattr(settings, "ZERNIO_API_KEY", "")
    t1, t2, t3 = token(), token(), token()
    env = {"TELEGRAM_BOT_TOKEN": t1, "TELEGRAM_CHAT_ID": str(CHAT), "TELEGRAM_BOT_TOKEN_2": t2,
           "TELEGRAM_CHAT_ID_2": "-100123", "TELEGRAM_BOT_TOKEN_3": "not-a-token", "TELEGRAM_CHAT_ID_3": "5"}  # fmt: skip
    for k, v in env.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(settings, "SECRETS_KEY", "")
    cli.bootstrap()
    assert "waits for it" in capsys.readouterr().out and all_bots() == []
    monkeypatch.setattr(settings, "SECRETS_KEY", os.environ["SECRETS_KEY"])
    cli.bootstrap()
    out = capsys.readouterr().out
    assert "TELEGRAM_BOT_TOKEN_3 / TELEGRAM_CHAT_ID_3 is not a bot token and a chat id: skipped" in out
    assert "Telegram bots: 2" in out
    got = {unseal(b.token_enc): (b.user_id, b.bot_id, b.chat_id, b.alerts, b.username) for b in all_bots()}
    assert got == {t1: (1, int(t1.split(":")[0]), CHAT, True, None), t2: (1, int(t2.split(":")[0]), -100123, False, None)}
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN_3", t3)
    cli.bootstrap()  # stamped: never again
    assert len(all_bots()) == 2
    with SyncSession() as s:
        s.execute(update(User).where(User.id == 1).values(env_imported_at=None))
        s.execute(update(TelegramBot).where(TelegramBot.alerts).values(alerts=False))
        s.commit()
    cli.bootstrap()  # unstamped by hand: bots already there stay as the operator set them
    assert sorted(b.alerts for b in all_bots()) == [False, False, False] and len(all_bots()) == 3
    zernio_key(1, None)


def all_bots() -> list[TelegramBot]:
    with SyncSession() as s:
        return list(s.scalars(select(TelegramBot).order_by(TelegramBot.id)))
