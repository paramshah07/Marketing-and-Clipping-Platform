"""The bot service (docs/telegram-bot.md): one process runs every user's bots (Supervisor), each a Bot: long
polling, the one-chat gate, pairing, routing, prompts, open views and watched jobs. What each command and button
does lives in app/bot/screens.py.

A bot acts as its owner: every api call carries the service's bearer and X-Clipper-User, so the api's guards and
row-level security apply as for their browser. It answers its paired chat only; unpaired, it waits for
/start <code> (docs section 2). Updates are handled one at a time, in order, each batch confirmed to Telegram
before it is handled (a crash never replays a tap). All state is in memory: a restart only loses open forms and
watches."""

import asyncio
import logging
import re
import time

import httpx

from app.bot import fmt, screens
from app.bot.clients import ApiError, Clipper, Telegram, TelegramError
from app.core.config import settings

log = logging.getLogger("app.bot")

PROMPT_TTL_S = 600
KEEP_VIEWS = 300  # open forms and lists remembered; the oldest go first
REFRESH_S = 10  # the supervisor's tick: bots added, removed or re-tokened in the web app start or stop this soon
PAIR = re.compile(r"/start(?:@\w+)? ([A-Za-z0-9_-]{16,64})")  # the t.me/<bot>?start=<code> link sends this


class Bot:
    def __init__(self, tg: Telegram, api: Clipper, chat_id: str | int | None, id: int | None = None, ver: str = ""):
        self.tg, self.api, self.chat = tg, api, None if chat_id is None else str(chat_id)  # None: waiting to pair
        self.id, self.ver = id, ver  # telegram_bots.id, and its sealed token's fingerprint (the supervisor's report)
        self.username: str | None = None
        self.alive = False  # polling Telegram fine right now
        self.rejected = False  # Telegram refused the token: stopped for good
        self.views: dict[int, dict] = {}  # message id -> an open editor, form or list
        self.prompt: dict | None = None  # the question the next plain message answers
        self.watches: dict[tuple[str, int], dict] = {}  # (kind, id) -> what to report when it finishes
        self.done: set[tuple[int, str]] = set()  # confirm taps (verb ending in !) already acted on
        self.albums: dict[str, int] = {}  # media_group_id -> its import question's message id
        self.logos: dict[str, float] = {}  # logo URL -> its aspect in the output frame
        self.last_account: int | None = None  # the account picked last (schedule form, calendar, ready)
        self._accounts: tuple[float, list | None] = (0.0, None)
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------ sending

    async def send(self, text: str, rows=None, photo: str | None = None, reply_to: int | None = None) -> dict:
        """A message, or a photo with the text as its caption when photo (a /media URL) is given and fits."""
        kw = {
            "chat_id": self.chat, "parse_mode": "HTML", "reply_markup": fmt.markup(rows) if rows else None,
            "reply_parameters": {"message_id": reply_to, "allow_sending_without_reply": True} if reply_to else None,
        }  # fmt: skip
        if photo and fmt.visible(caption := fmt.shorten(text, 1024)) <= 1024:
            try:
                data = await self.api.media(photo)
            except ApiError:
                pass  # no thumbnail file after all: plain text
            else:
                return await self.tg("sendPhoto", files={"photo": ("thumb.jpg", data, "image/jpeg")}, caption=caption, **kw)
        return await self.tg("sendMessage", text=fmt.shorten(text, 4096), link_preview_options={"is_disabled": True}, **kw)

    async def edit(self, msg: dict, text: str, rows=None) -> None:
        """Edit in place: the caption of a photo message, else the text. rows None removes the buttons."""
        kw = {"chat_id": self.chat, "message_id": msg["message_id"], "parse_mode": "HTML", "reply_markup": fmt.markup(rows or [])}
        try:
            if "photo" in msg or "video" in msg:
                await self.tg("editMessageCaption", caption=fmt.shorten(text, 1024), **kw)
            else:
                await self.tg("editMessageText", text=fmt.shorten(text, 4096), link_preview_options={"is_disabled": True}, **kw)
        except TelegramError as e:
            if "not modified" not in e.description:
                raise

    async def buttons(self, msg: dict, rows) -> None:
        """Swap only the buttons (a confirm step, a picker)."""
        try:
            await self.tg("editMessageReplyMarkup", chat_id=self.chat, message_id=msg["message_id"], reply_markup=fmt.markup(rows))
        except TelegramError as e:
            if "not modified" not in e.description:
                raise

    async def ask(self, question: str, kind: str, placeholder: str = "Reply here", **data) -> None:
        """Ask for an answer: the next plain message (text, or a file where one is asked for) answers it."""
        msg = await self.tg(
            "sendMessage", chat_id=self.chat, text=question, parse_mode="HTML",
            reply_markup={"force_reply": True, "input_field_placeholder": placeholder[:64]},
        )  # fmt: skip
        self.prompt = {"kind": kind, "at": time.monotonic(), "id": msg["message_id"], **data}

    def spawn(self, coro) -> None:
        """Run slow work (video sends, uploads) beside the update loop; failures are logged and reported."""

        async def guarded():
            try:
                await coro
            except Exception as e:
                log.exception("background task")
                await self.send(f"Something went wrong: {fmt.h(getattr(e, 'message', None) or type(e).__name__)}")

        task = asyncio.create_task(guarded())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------ state

    def keep(self, msg: dict, view: dict) -> dict:
        view["msg"] = msg
        self.views[msg["message_id"]] = view
        while len(self.views) > KEEP_VIEWS:
            self.views.pop(next(iter(self.views)))
        return view

    def view(self, msg: dict, *kinds: str) -> dict:
        v = self.views.get(msg["message_id"])
        if not v or v["kind"] not in kinds:
            raise screens.Alert("This has expired: run the command again.")
        v["msg"] = msg  # the newest copy (a photo stays a photo)
        return v

    def watch(self, kind: str, id: int, **info) -> None:
        self.watches[(kind, id)] = {"since": time.monotonic(), **info}

    async def accounts(self, fresh: bool = False) -> list[dict]:
        """GET /api/accounts, kept 60 s (it asks Zernio for each account's quota)."""
        at, accs = self._accounts
        if fresh or accs is None or time.monotonic() - at > 60:
            accs = await self.api.get("/api/accounts")
            self._accounts = (time.monotonic(), accs)
        return accs

    async def tz(self, account_id: int) -> str:
        return next((a["timezone"] for a in await self.accounts() if a["id"] == account_id), "UTC")

    # ------------------------------------------------------------ routing

    async def handle(self, u: dict) -> None:
        cq = u.get("callback_query")
        m = u.get("message") or (cq or {}).get("message") or {}
        chat = str((m.get("chat") or {}).get("id"))
        private = not cq and (m.get("chat") or {}).get("type") == "private"
        if private and (code := PAIR.fullmatch((m.get("text") or "").strip())) and await self.pair(m, code[1]):
            return
        if chat != self.chat:
            log.warning("bot %s ignored an update from chat %s (it answers %s)", self.id, chat, self.chat or "no chat yet")
            return
        try:
            await (self.on_callback(cq) if cq else self.on_message(m))
        except Exception as e:
            log.exception("update %s", u.get("update_id"))
            reason = e.message if isinstance(e, ApiError) else type(e).__name__
            try:
                await self.send(f"Something went wrong: {fmt.h(reason)}")
            except Exception:  # noqa: BLE001, S110 (Telegram itself is failing: the log above has it)
                pass

    async def on_callback(self, cq: dict) -> None:
        data, msg = cq.get("data") or "", cq["message"]
        verb, *args = data.split(":")
        fn = screens.CALLBACKS.get(verb)
        self.prompt = None  # a tap elsewhere means the pending question is no longer wanted
        if fn and verb.endswith("!"):  # a confirm acts once: a double tap or an old message does nothing
            if (msg["message_id"], data) in self.done:
                return await self.answer(cq, "Already done.")
            self.done.add((msg["message_id"], data))
        try:
            toast = await fn(self, msg, *args) if fn else None
        except (screens.Alert, ApiError) as e:
            return await self.answer(cq, getattr(e, "message", None) or str(e), alert=True)
        except Exception:
            await self.answer(cq)
            raise
        await self.answer(cq, toast)

    async def answer(self, cq: dict, text: str | None = None, alert: bool = False) -> None:
        try:
            await self.tg("answerCallbackQuery", callback_query_id=cq["id"], text=fmt.cut(text, 200) or None, show_alert=alert or None)
        except TelegramError:
            pass  # answered too late (the tap is over 15 s old): the spinner has stopped by itself

    async def on_message(self, m: dict) -> None:
        text = (m.get("text") or "").strip()
        if text.startswith("/"):
            self.prompt = None
            name, _, arg = text[1:].partition(" ")
            name = name.split("@")[0].lower()
            if card := re.fullmatch(r"([crpbati])(\d+)", name):  # screens.NAMES
                return await screens.open_card(self, card[1], int(card[2]))
            fn = screens.COMMANDS.get(name)
            return await (fn(self, arg.strip()) if fn else self.send("I don't know that command. /help lists them."))
        p = self.prompt
        if p and time.monotonic() - p["at"] > PROMPT_TTL_S:
            p = self.prompt = None
        if p and (text or m.get("document") or m.get("photo")):
            self.prompt = None
            try:
                return await screens.ANSWERS[p["kind"]](self, m, p)
            except (screens.Alert, ApiError) as e:  # the same question stays open
                self.prompt = p | {"at": time.monotonic()}
                return await self.send(f"{fmt.h(getattr(e, 'message', None) or str(e))}\nTry again, or /cancel.")
        await screens.on_message(self, m)

    # ------------------------------------------------------------ pairing

    async def pair(self, m: dict, code: str) -> bool:
        """/start <code> in a private chat: the api makes this chat the bot's if the code is its current one (a
        paired bot too: Re-pair moves it). Anything else is ignored, without a reply."""
        if self.id is None:
            return False
        c = m["chat"]
        title = " ".join(filter(None, (c.get("first_name"), c.get("last_name")))) or c.get("username")
        try:
            await self.api.post(f"/api/internal/bots/{self.id}/pair", {"code": code, "chat_id": c["id"], "chat_title": title})
        except ApiError as e:
            log.warning("bot %s: pairing from chat %s refused: %s", self.id, c["id"], e.message)
            return False
        self.chat = str(c["id"])
        log.info("bot %s paired with chat %s", self.id, self.chat)
        try:
            await self.menu()
            await self.send("<b>Paired.</b> This chat runs your Clipper now. /help lists what I do.")
        except TelegramError as e:
            log.warning("bot %s: after pairing: %s", self.id, e)
        return True

    async def menu(self) -> None:
        scope = {"type": "chat", "chat_id": int(self.chat)} if self.chat.lstrip("-").isdigit() else None  # the menu shows in this chat
        await self.tg("setMyCommands", commands=[{"command": c, "description": d} for c, d in screens.MENU], scope=scope)

    # ------------------------------------------------------------ loops

    async def run(self) -> None:
        """Poll until cancelled (the supervisor), or until Telegram refuses the token (rejected)."""
        try:
            await self.poll()
        except TelegramError as e:
            if e.code not in (401, 404):
                raise
            self.rejected = True
            log.error("bot %s: Telegram rejected the token (%s): stopped until its owner pastes a new one", self.id, e)
        finally:
            self.alive = False
            for t in list(self._tasks):
                t.cancel()

    async def poll(self) -> None:
        me = await self.tg("getMe")
        self.username, self.alive = me.get("username"), True
        pending = (await self.tg("getWebhookInfo")).get("pending_update_count", 0)
        await self.tg("deleteWebhook")  # getUpdates doesn't work while a webhook is set
        offset = None
        if self.chat:
            # Drop what queued while the bot was down: a "Post now" tapped hours ago must not publish now.
            # offset=-1 returns the newest update and forgets the rest; the first poll below confirms it.
            # (Unpaired, keep it: the /start <code> may be waiting there.)
            stale = await self.tg("getUpdates", offset=-1, timeout=0)
            offset = stale[-1]["update_id"] + 1 if stale else None
        log.info("bot %s @%s answering chat %s", self.id, self.username, self.chat or "none: waiting for /start <code>")
        try:  # a chat Telegram refuses (the bot kicked or blocked there) must not keep it from polling: Re-pair moves it
            if self.chat:
                await self.menu()
            if pending and self.chat:
                await self.send(f"Back online. {fmt.plural(pending, 'message')} sent while I was offline "
                                f"{'was' if pending == 1 else 'were'} ignored: send {'it' if pending == 1 else 'them'} again.")  # fmt: skip
        except TelegramError as e:
            if e.code in (401, 404):
                raise
            log.warning("bot %s: chat %s: %s", self.id, self.chat, e)
        self.spawn(self.watch_loop())
        backoff = 1
        while True:
            try:
                updates = await self.tg("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "callback_query"])
            except TelegramError as e:
                self.alive = False
                if e.code in (401, 404):
                    raise
                wait = 30 if e.code == 409 else backoff
                log.warning("getUpdates: %s%s; again in %s s", e,
                            " (another process polls this bot token)" if e.code == 409 else "", wait)  # fmt: skip
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, 30)
                continue
            backoff, self.alive = 1, True
            if not updates:
                continue
            offset = updates[-1]["update_id"] + 1
            try:  # confirm the batch now: at most once, even if the bot dies while handling it
                await self.tg("getUpdates", offset=offset, limit=1, timeout=0)
            except TelegramError as e:
                log.warning("confirming updates: %s", e)
            for u in updates:
                await self.handle(u)

    async def watch_loop(self) -> None:
        while True:
            await asyncio.sleep(3)
            try:
                await screens.check_watches(self)
            except ApiError:
                pass  # the api is restarting or down: the watches wait for it
            except Exception:
                log.exception("watches")


class Supervisor:
    """Every user's bots, each a Bot task acting as its owner. Every REFRESH_S: report how each is doing (polling
    fine, or its token refused), then take the api's list of bots to run and start the new ones, stop the removed
    ones, and restart one whose token changed or whose task ended. Keyed on (id, token) only: a bot that pairs
    itself keeps running (no restart, so no stale-update drop or "Back online"). While the api can't be reached,
    the bots run as they are."""

    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None,
                 tg_transport: httpx.AsyncBaseTransport | None = None):  # fmt: skip
        self.url, self.transport, self.tg_transport = url, transport, tg_transport
        self.api = Clipper(url, transport, user_id=None)  # /api/internal/*: the service itself
        self.bots: dict[int, tuple[str, asyncio.Task, Bot]] = {}  # telegram_bots.id -> (token, task, bot)

    async def tick(self) -> None:
        report = [{"id": id, "ver": bot.ver, "error": "TOKEN_REJECTED"} if bot.rejected else
                  {"id": id, "ver": bot.ver, "username": bot.username}
                  for id, (_, _, bot) in self.bots.items() if bot.rejected or bot.alive]  # fmt: skip
        try:
            if report:
                await self.api.post("/api/internal/bots/report", report)
            rows = {r["id"]: r for r in await self.api.get("/api/internal/bots")}
        except ApiError as e:
            return log.warning("bot list: %s", e.message)
        for id, (token, task, bot) in list(self.bots.items()):
            if (r := rows.get(id)) is None or r["token"] != token or task.done():
                task.cancel()
                del self.bots[id]
            else:
                bot.ver = r["ver"]  # a re-sealed token (SECRETS_KEY rotation) is the same token
                # the list's chat, also after a pairing whose answer never reached the bot (a stale list: next tick)
                bot.chat = None if r["chat_id"] is None else str(r["chat_id"])
        for id, r in rows.items():
            if id not in self.bots:
                bot = Bot(Telegram(r["token"], self.tg_transport), Clipper(self.url, self.transport, r["user_id"]),
                          r["chat_id"], id, r["ver"])  # fmt: skip
                self.bots[id] = (r["token"], asyncio.create_task(self.guard(bot)), bot)

    async def guard(self, bot: Bot) -> None:
        """bot.run(), its failures logged (the next tick restarts it), and its HTTP clients closed."""
        try:
            await bot.run()
        except Exception:
            log.exception("bot %s stopped", bot.id)
        finally:
            await bot.tg.http.aclose()
            await bot.api.http.aclose()

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(REFRESH_S)


async def main() -> int:
    if not settings.BOT_SERVICE_SECRET:  # the api answers the bot service only with it
        log.warning("Telegram bots off: set BOT_SERVICE_SECRET in .env (api and bot), then docker compose up -d api bot")
        return 0
    log.info("bot service: running every user's Telegram bots (the api's list, every %s s)", REFRESH_S)
    await Supervisor(settings.CLIPPER_API_URL).run()
    return 0
