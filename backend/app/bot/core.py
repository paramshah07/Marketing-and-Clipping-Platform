"""The bot service (docs/telegram-bot.md): long polling, the one-chat gate, routing, prompts, open views and
watched jobs. What each command and button does lives in app/bot/screens.py.

Updates are handled one at a time, in order, each batch confirmed to Telegram before it is handled (a crash
never replays a tap). All state is in memory: a restart only loses open forms and watches."""

import asyncio
import logging
import re
import time

from app.bot import fmt, screens
from app.bot.clients import ApiError, Clipper, Telegram, TelegramError
from app.core.config import settings

log = logging.getLogger("app.bot")

PROMPT_TTL_S = 600
KEEP_VIEWS = 300  # open forms and lists remembered; the oldest go first


class Bot:
    def __init__(self, tg: Telegram, api: Clipper, chat_id: str):
        self.tg, self.api, self.chat = tg, api, str(chat_id)
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
        if chat != self.chat:
            log.warning("ignored an update from chat %s (TELEGRAM_CHAT_ID is %s)", chat, self.chat)
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
            if card := re.fullmatch(r"([crpba])(\d+)", name):
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

    # ------------------------------------------------------------ loops

    async def run(self) -> None:
        me = await self.tg("getMe")
        pending = (await self.tg("getWebhookInfo")).get("pending_update_count", 0)
        await self.tg("deleteWebhook")  # getUpdates doesn't work while a webhook is set
        # Drop what queued while the bot was down: a "Post now" tapped hours ago must not publish now.
        # offset=-1 returns the newest update and forgets the rest; the first poll below confirms it.
        stale = await self.tg("getUpdates", offset=-1, timeout=0)
        offset = stale[-1]["update_id"] + 1 if stale else None
        scope = {"type": "chat", "chat_id": int(self.chat)} if self.chat.lstrip("-").isdigit() else None  # the menu shows in this chat
        await self.tg("setMyCommands", commands=[{"command": c, "description": d} for c, d in screens.MENU], scope=scope)
        log.info("bot @%s answering chat %s", me.get("username"), self.chat)
        if pending:
            await self.send(f"Back online. {fmt.plural(pending, 'message')} sent while I was offline "
                            f"{'was' if pending == 1 else 'were'} ignored: send {'it' if pending == 1 else 'them'} again.")  # fmt: skip
        self.spawn(self.watch_loop())
        backoff = 1
        while True:
            try:
                updates = await self.tg("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "callback_query"])
            except TelegramError as e:
                if e.code in (401, 404):
                    raise
                wait = 30 if e.code == 409 else backoff
                log.warning("getUpdates: %s%s; again in %s s", e,
                            " (another process polls this bot token)" if e.code == 409 else "", wait)  # fmt: skip
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, 30)
                continue
            backoff = 1
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


async def main() -> int:
    if not (settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID):
        log.warning("Telegram bot off: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env, then docker compose up -d bot")
        return 0
    bot = Bot(Telegram(settings.TELEGRAM_BOT_TOKEN), Clipper(settings.CLIPPER_API_URL), settings.TELEGRAM_CHAT_ID)
    try:
        await bot.run()
    except TelegramError as e:
        if e.code in (401, 404):
            log.error("Telegram rejected TELEGRAM_BOT_TOKEN (%s): bot off. Fix .env, then docker compose up -d bot", e)
            return 0
        raise
    return 0
