"""Each user's Telegram bots (docs/telegram-bot.md), and the bot service's own endpoints.

/api/me/bots: add a bot by its @BotFather token (checked with Telegram, sealed like the Zernio key), pair it with a
chat, switch its alerts, send a test message, remove it. Pairing: the user opens t.me/<bot>?start=<code> (a code
good for 15 min, stored as its sha256) and taps Start; the bot service hands the code back through
/api/internal/bots/{id}/pair, and that private chat becomes the one the bot answers.

/api/internal/*: the bot service's bearer only (Caddy answers 404 for them from outside). Its supervisor reads every
user's runnable bots and reports their health through two SECURITY DEFINER functions (migration 0009): row-level
security shows this role no one's bots outside a request. Pairing runs as the bot's owner (X-Clipper-User)."""

import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import CurrentUser, Db, _err, _sha, bot_out, current_user, is_bot
from app.bot.clients import Telegram, TelegramError
from app.core.db import SessionLocal
from app.core.secrets import seal, unseal
from app.models import TelegramBot
from app.schemas import BotIn, BotOut, BotPairing, BotPatch, BotTest

router = APIRouter(prefix="/api/me/bots", dependencies=[Depends(current_user)])

TOKEN = re.compile(r"(\d{5,15}):[A-Za-z0-9_-]{30,60}")  # <bot id>:<secret>, as @BotFather hands it out (ids < 2^52)
PAIR_LIFE = timedelta(minutes=15)
UNSEALED = "this server can't store bot tokens yet (SECRETS_KEY is not set)"
TEST_TEXT = "Clipper test message: this bot works."


def telegram(token: str) -> Telegram:  # tests swap in a MockTransport
    return Telegram(token)


async def call(token: str, method: str, **params):
    """One Bot API call with its own client. Raises TelegramError (never with the token in it)."""
    tg = telegram(token)
    try:
        return await tg(method, **params)
    finally:
        await tg.http.aclose()


def _now() -> datetime:
    return datetime.now(UTC)


async def _get(s: AsyncSession, id: int) -> TelegramBot:
    if (bot := await s.scalar(select(TelegramBot).where(TelegramBot.id == id, TelegramBot.user_id == s.info["uid"]))) is None:
        raise _err(404, "BOT_NOT_FOUND", f"bot {id} not found")
    return bot


def _code(bot: TelegramBot) -> str:
    """A new pairing code (the old one stops working). A paired bot keeps its chat until another chat uses it."""
    code = secrets.token_urlsafe(16)  # 22 characters of Telegram's start alphabet (A-Z a-z 0-9 _ -, up to 64)
    bot.pair_sha256, bot.pair_expires_at = _sha(code), _now() + PAIR_LIFE
    return code


def _pairing(bot: TelegramBot, code: str | None) -> BotPairing:
    url = f"https://t.me/{bot.username}?start={code}" if code and bot.username else None
    return BotPairing(bot=bot_out(bot, _now()), pair_url=url, start=code and f"/start {code}",
                      expires_at=code and bot.pair_expires_at)  # fmt: skip


@router.get("")
async def list_bots(s: Db) -> list[BotOut]:
    now = _now()
    bots = await s.scalars(select(TelegramBot).where(TelegramBot.user_id == s.info["uid"]).order_by(TelegramBot.id))
    return [bot_out(b, now) for b in bots]


@router.post("", status_code=201)
async def add_bot(body: BotIn, s: Db) -> BotPairing:
    """Check the token with Telegram (getMe), store it sealed, and hand out a pairing code. 422 TOKEN_REJECTED (not a
    token, or Telegram refuses it); 409 BOT_IN_USE (it has a webhook: another app gets its messages), BOT_TAKEN (another
    Clipper user added it). Your own bot again with a new token (@BotFather /revoke) keeps its chat."""
    token = body.token.strip()
    if not TOKEN.fullmatch(token):
        raise _err(422, "TOKEN_REJECTED", "a bot token looks like 123456789:AAE3x… (@BotFather: /newbot, or /token)")
    try:
        sealed = seal(token)
    except RuntimeError:
        raise _err(503, "SECRETS_KEY_MISSING", UNSEALED) from None
    try:
        me = await call(token, "getMe")
        hook = await call(token, "getWebhookInfo")
    except TelegramError as e:
        if e.code in (401, 404):
            raise _err(422, "TOKEN_REJECTED", "Telegram refused the token: copy it again from @BotFather") from None
        raise _err(502, "TELEGRAM_ERROR", f"could not check the token with Telegram ({e})") from None
    if hook.get("url"):  # polling it would delete another app's webhook
        raise _err(409, "BOT_IN_USE", "another app receives this bot's messages (a webhook): make a new bot for Clipper "
                   "with @BotFather /newbot")  # fmt: skip
    bot = await s.scalar(select(TelegramBot).where(TelegramBot.bot_id == me["id"], TelegramBot.user_id == s.info["uid"]))
    if bot is None:
        bot = TelegramBot(bot_id=me["id"], token_enc=sealed)
        s.add(bot)
    else:
        bot.token_enc, bot.error = sealed, None
    bot.username = me.get("username")
    code = None if bot.chat_id else _code(bot)
    try:
        await s.commit()
    except IntegrityError:  # uq_telegram_bots_bot_id: row-level security hides the other user's row
        raise _err(409, "BOT_TAKEN", "another Clipper user has added this bot") from None
    await s.refresh(bot)
    return _pairing(bot, code)


@router.post("/{id}/pair")
async def pair_bot(id: int, s: Db) -> BotPairing:
    """A new pairing code: the chat that uses it becomes the bot's (the current one answers until then)."""
    bot = await _get(s, id)
    code = _code(bot)
    await s.commit()
    return _pairing(bot, code)


@router.patch("/{id}")
async def patch_bot(id: int, body: BotPatch, s: Db) -> BotOut:
    bot = await _get(s, id)
    bot.alerts = body.alerts
    await s.commit()
    return bot_out(bot, _now())


@router.post("/{id}/test")
async def test_bot(id: int, s: Db) -> BotTest:
    """Send the bot's chat a message now, from here (not the bot service): ok, or Telegram's reason."""
    bot = await _get(s, id)
    if bot.chat_id is None:
        raise _err(409, "BOT_NOT_PAIRED", "pair the bot first: open it in Telegram and tap Start")
    if (token := unseal(bot.token_enc)) is None:
        raise _err(503, "SECRETS_KEY_MISSING", UNSEALED)
    text_ = TEST_TEXT + (" It sends your failure alerts." if bot.alerts else " Alerts are off for it.")
    try:
        await call(token, "sendMessage", chat_id=bot.chat_id, text=text_)
    except TelegramError as e:
        return BotTest(ok=False, error=str(e))
    return BotTest(ok=True, error=None)


@router.delete("/{id}", status_code=204)
async def delete_bot(id: int, s: Db) -> None:
    """The bot service stops it within ~10 s."""
    await s.delete(await _get(s, id))
    await s.commit()


# ---------------------------------------------------------------- the bot service


def bot_service(request: Request) -> None:
    if not is_bot(request.headers):
        raise _err(401 if "authorization" not in request.headers else 403, "BOT_SERVICE_ONLY", "the bot service only")


internal = APIRouter(prefix="/api/internal", dependencies=[Depends(bot_service)], include_in_schema=False)


class Pair(BaseModel):
    code: str
    chat_id: int
    chat_title: str | None = None


class Seen(BaseModel):  # one bot in the supervisor's report
    id: int
    ver: str  # bots_for_supervisor's: the sealed token it ran
    username: str | None = None
    error: Literal["TOKEN_REJECTED"] | None = None


@internal.get("/bots")
async def supervised_bots() -> list[dict]:
    """Every bot to run: [{id, user_id, token, chat_id (null: waiting to pair), ver}]. Not those of disabled users,
    nor rejected tokens (until their owner pastes a new one)."""
    async with SessionLocal() as s:
        rows = (await s.execute(text("SELECT id, user_id, token_enc, chat_id, ver FROM bots_for_supervisor()"))).all()
    return [{"id": id, "user_id": uid, "token": token, "chat_id": chat, "ver": ver}
            for id, uid, sealed, chat, ver in rows if (token := unseal(sealed))]  # fmt: skip


@internal.post("/bots/report", status_code=204)
async def report(body: list[Seen]) -> None:
    """Each tick: the bots polling fine (last_seen_at, their @name) and the ones Telegram refused (TOKEN_REJECTED)."""
    async with SessionLocal() as s:
        await s.execute(text("SELECT report_bots(CAST(:r AS jsonb))"), {"r": json.dumps([b.model_dump() for b in body])})
        await s.commit()


@internal.post("/bots/{id}/pair", status_code=204)
async def pair(id: int, body: Pair, user: CurrentUser, s: Db) -> None:
    """/start <code> reached bot id from a private chat: with the right, unexpired code that chat becomes the bot's.
    As the bot's owner, so row-level security keeps it to their bots. 404 PAIR_CODE_INVALID otherwise."""
    q = update(TelegramBot).where(
        TelegramBot.id == id, TelegramBot.user_id == user.id, TelegramBot.pair_sha256 == _sha(body.code),
        TelegramBot.pair_expires_at > func.now(),
    ).values(chat_id=body.chat_id, chat_title=body.chat_title, pair_sha256=None, pair_expires_at=None)  # fmt: skip
    if (await s.execute(q)).rowcount != 1:
        raise _err(404, "PAIR_CODE_INVALID", "not this bot's code, or it expired")
    await s.commit()
