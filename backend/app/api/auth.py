"""Users and sign-in. Every route except /api/health, /api/auth/* and the SPA's files runs as a user: the
clipper_session cookie (a browser), or the bot service's bearer + X-Clipper-User (the user a Telegram bot acts
for). The request's Db session carries that user's id (app.core.db) and Postgres row-level security scopes
every query to their rows, so a forgotten filter fails closed.

users and sessions have no row-level security: only this module and the CLI touch them, always by id or
token. Errors are {"detail": {"code", "message"}}, as in scheduling. Each user's own Zernio key: /api/me/zernio-key;
their Telegram bots: app/api/bots.py."""

import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Annotated
from urllib.parse import urlsplit

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import delete, exists, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.secrets import seal, unseal
from app.models import Account, AuthSession, Post, TelegramBot, User
from app.schemas import (
    BotOut,
    Credentials,
    KeyCheck,
    Me,
    PasswordChange,
    Setup,
    SignupStatus,
    Storage,
    UserOut,
    ZernioKeyIn,
    ZernioKeyOut,
)
from app.services import storage, zernio
from app.tasks import accounts as account_sync

router = APIRouter(prefix="/api")

SESSION_LIFE, EXTEND_BELOW = timedelta(days=30), timedelta(days=15)
USERNAME = re.compile(r"[a-z0-9][a-z0-9_.-]{2,31}")  # users.username's CHECK
MIN_PASSWORD, MAX_PASSWORD = 8, 128
BCRYPT_COST = 12
BOT_ALIVE = timedelta(seconds=90)  # the supervisor reports every ~10 s


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


def _now() -> datetime:
    return datetime.now(UTC)


def _sha(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _https() -> bool:
    return settings.APP_BASE_URL.startswith("https://")


def cookie() -> str:
    """The session cookie's name. Over https it has the __Host- prefix: a browser takes that one only from this host
    itself (Secure, Path=/, no Domain), so no other *.sslip.io site can plant a session of its choosing here (sslip.io
    is not on the Public Suffix List). Plain http (dev) can't carry the prefix."""
    return "__Host-clipper_session" if _https() else "clipper_session"


# ---------------------------------------------------------------- passwords and throttles

# bcrypt reads 72 bytes (bcrypt 5 raises past them): cut there, as every bcrypt did (and Go's refuses longer).
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt(BCRYPT_COST)).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Any $2a$ / $2b$ / $2y$ hash: the operator's came from `caddy hash-password` (cost 14)."""
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.replace("$2y$", "$2b$", 1).encode())
    except ValueError:  # not a bcrypt hash
        return False


@cache
def _dummy_hash() -> str:  # an unknown username costs a bcrypt check too: no timing tells it apart
    return hash_password(secrets.token_hex(16))


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise _err(422, "PASSWORD_TOO_SHORT", f"the password needs at least {MIN_PASSWORD} characters")
    if len(password) > MAX_PASSWORD:
        raise _err(422, "PASSWORD_TOO_LONG", f"the password can have at most {MAX_PASSWORD} characters")


class Throttle:
    """At most `limit` hits per key in `window` seconds. ponytail: in memory, one api process (a restart
    forgets); the map is swept past 10k keys."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window, self.hits = limit, window, defaultdict(list)

    def full(self, key) -> bool:
        cutoff = time.monotonic() - self.window
        return sum(t > cutoff for t in self.hits.get(key, ())) >= self.limit

    def hit(self, key) -> None:
        now = time.monotonic()
        if len(self.hits) > 10_000:
            self.hits = defaultdict(list, {k: v for k, v in self.hits.items() if v[-1] > now - self.window})
        self.hits[key] = [t for t in self.hits[key] if t > now - self.window] + [now]

    def clear(self, key) -> None:
        self.hits.pop(key, None)


LOGIN_FAILS = Throttle(10, 15 * 60)  # per (client ip, username): a stranger can't lock a user out everywhere
SIGNUPS = Throttle(5, 60 * 60)  # per client ip


def client_ip(request: Request) -> str:
    """In production uvicorn runs with --proxy-headers --forwarded-allow-ips for Caddy's network, so this is
    the X-Forwarded-For client when the request came through Caddy, else the peer itself."""
    return request.client.host if request.client else ""


# ---------------------------------------------------------------- who is asking


def is_bot(headers: Headers) -> bool:
    """The bot service's bearer. Anything else in Authorization (Caddy's Basic, say) is not it."""
    secret = settings.BOT_SERVICE_SECRET
    return bool(secret) and hmac.compare_digest(headers.get("authorization", "").encode(), f"Bearer {secret}".encode())


async def signed_in(request: Request, response: Response | None = None) -> User:
    """The user, else 401. A session with under 15 days left is extended to 30 on a GET with a response to
    re-set the cookie on (every api GET returns a model; /media passes none). Its own db session, closed before
    the request's: that one's first transaction must begin with the uid set."""
    async with SessionLocal() as s:
        user = None
        if is_bot(request.headers):
            uid = request.headers.get("x-clipper-user", "")
            user = await s.get(User, int(uid)) if re.fullmatch(r"\d{1,9}", uid) else None
        elif token := request.cookies.get(cookie()):
            q = select(User, AuthSession.expires_at).join(AuthSession, AuthSession.user_id == User.id)
            row = (await s.execute(q.where(AuthSession.token_sha256 == _sha(token), AuthSession.expires_at > func.now()))).first()
            if row:
                user, expires = row
                if response is not None and request.method == "GET" and expires - _now() < EXTEND_BELOW:
                    await s.execute(update(AuthSession).where(AuthSession.token_sha256 == _sha(token))
                                    .values(expires_at=_now() + SESSION_LIFE))  # fmt: skip
                    await s.commit()
                    _set_cookie(response, token)
    if user is None or user.disabled_at is not None:
        raise _err(401, "NOT_SIGNED_IN", "sign in first")
    return user


async def current_user(request: Request, response: Response) -> User:
    return await signed_in(request, response)


CurrentUser = Annotated[User, Depends(current_user)]


async def _db(user: CurrentUser):
    """The request's session, as the signed-in user (row-level security)."""
    async with SessionLocal(info={"uid": user.id}) as s:
        yield s


Db = Annotated[AsyncSession, Depends(_db)]


class CSRF:
    """ASGI middleware: an unsafe method needs this site's Origin (APP_BASE_URL), Sec-Fetch-Site: same-origin,
    or the bot service's bearer, so another site can't act with the user's cookie (sslip.io isn't on the
    Public Suffix List, and a multipart form post gets no CORS preflight). 403 otherwise."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            h, base = Headers(scope=scope), urlsplit(settings.APP_BASE_URL)
            if not (h.get("origin") == f"{base.scheme}://{base.netloc}" or h.get("sec-fetch-site") == "same-origin" or is_bot(h)):
                body = {"detail": {"code": "CROSS_SITE", "message": "cross-site request refused"}}
                return await JSONResponse(body, 403)(scope, receive, send)
        await self.app(scope, receive, send)


# ---------------------------------------------------------------- sign up, in and out


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(cookie(), token, max_age=int(SESSION_LIFE.total_seconds()), path="/", httponly=True,
                        samesite="lax", secure=_https())  # fmt: skip


async def _sign_in(s: AsyncSession, user_id: int, response: Response) -> None:
    """A new session row (the caller commits) and its cookie."""
    token = secrets.token_urlsafe(32)
    s.add(AuthSession(token_sha256=_sha(token), user_id=user_id, expires_at=_now() + SESSION_LIFE))
    _set_cookie(response, token)


async def _active_users(s: AsyncSession) -> int:
    return await s.scalar(select(func.count()).select_from(User).where(User.disabled_at.is_(None)))


@router.get("/auth/signup-status")
async def signup_status() -> SignupStatus:
    async with SessionLocal() as s:
        left = max(0, settings.MAX_USERS - await _active_users(s))
    return SignupStatus(open=left > 0, remaining=left)


@router.post("/auth/signup", status_code=201)
async def signup(body: Credentials, request: Request, response: Response) -> UserOut:
    """Username + password, nothing else; signs the new user in. Open until MAX_USERS users exist."""
    username = body.username.strip().lower()
    if not USERNAME.fullmatch(username):
        raise _err(422, "USERNAME_INVALID", "3 to 32 characters: a-z, 0-9, . _ -, starting with a letter or digit")
    check_password(body.password)
    if SIGNUPS.full(ip := client_ip(request)):
        raise _err(429, "TOO_MANY_ATTEMPTS", "too many signups from here: try again in an hour")
    SIGNUPS.hit(ip)
    hashed = await run_in_threadpool(hash_password, body.password)
    async with SessionLocal() as s:
        await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('signup'))"))  # count + insert, one at a time
        if await _active_users(s) >= settings.MAX_USERS:
            raise _err(403, "SIGNUPS_FULL", "signups are full")
        user = User(username=username, password_hash=hashed, quota_bytes=settings.USER_QUOTA_BYTES)
        s.add(user)
        try:
            await s.flush()
        except IntegrityError:  # uq_users_username
            raise _err(409, "USERNAME_TAKEN", f"{username} is taken") from None
        await _sign_in(s, user.id, response)
        await s.commit()
    return UserOut(id=user.id, username=username)


@router.post("/auth/login")
async def login(body: Credentials, request: Request, response: Response) -> UserOut:
    username = body.username.strip().lower()
    key = (client_ip(request), username)
    if LOGIN_FAILS.full(key):
        raise _err(429, "TOO_MANY_ATTEMPTS", "too many failed sign-ins: try again in 15 minutes")
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.username == username))
    hashed = user.password_hash if user and user.password_hash else None
    if not (await run_in_threadpool(verify_password, body.password, hashed or _dummy_hash()) and hashed):
        LOGIN_FAILS.hit(key)
        raise _err(401, "INVALID_LOGIN", "wrong username or password")
    if user.disabled_at is not None:
        raise _err(403, "ACCOUNT_DISABLED", "this account is disabled")
    LOGIN_FAILS.clear(key)
    async with SessionLocal() as s:
        await s.execute(delete(AuthSession).where(AuthSession.expires_at < func.now()))  # housekeeping
        await _sign_in(s, user.id, response)
        await s.commit()
    return UserOut(id=user.id, username=user.username)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    if token := request.cookies.get(cookie()):
        async with SessionLocal() as s:
            await s.execute(delete(AuthSession).where(AuthSession.token_sha256 == _sha(token)))
            await s.commit()
    response.delete_cookie(cookie(), path="/", httponly=True, samesite="lax", secure=_https())


# ---------------------------------------------------------------- the signed-in user


def bot_out(b: TelegramBot, now: datetime) -> BotOut:
    health = (
        "rejected" if b.error else "waiting" if b.chat_id is None
        else "running" if b.last_seen_at and now - b.last_seen_at < BOT_ALIVE else "not_responding"
    )  # fmt: skip
    pairing = b.pair_sha256 is not None and b.pair_expires_at is not None and b.pair_expires_at > now
    return BotOut(id=b.id, username=b.username, chat_title=b.chat_title, alerts=b.alerts, health=health, pairing=pairing,
                  last_seen_at=b.last_seen_at, created_at=b.created_at)  # fmt: skip


def _zernio_out(u: User) -> ZernioKeyOut:
    return ZernioKeyOut(status=u.zernio_key_status, last4=u.zernio_key_last4, email=u.zernio_email, name=u.zernio_name,
                        checked_at=u.zernio_checked_at, error=u.zernio_error)  # fmt: skip


@router.get("/me")
async def me(user: CurrentUser, s: Db) -> Me:
    now = _now()
    bots = (await s.scalars(select(TelegramBot).where(TelegramBot.user_id == user.id).order_by(TelegramBot.id))).all()
    usable = exists().where(Account.user_id == user.id, Account.connection_status == "connected", Account.disabled_at.is_(None))
    quota, used = (await s.execute(storage.USAGE, {"u": user.id})).one()
    return Me(
        id=user.id, username=user.username,
        setup=Setup(zernio=user.zernio_key_status == "valid", instagram=await s.scalar(select(usable)),
                    telegram=any(b.chat_id is not None and not b.error for b in bots)),
        zernio=_zernio_out(user), bots=[bot_out(b, now) for b in bots],
        storage=Storage(used_bytes=used, quota_bytes=quota),
    )  # fmt: skip


@router.post("/me/password", status_code=204)
async def change_password(body: PasswordChange, user: CurrentUser, request: Request) -> None:
    """Signs out every other session of the user; this browser stays signed in."""
    key = (client_ip(request), user.username)
    if LOGIN_FAILS.full(key):
        raise _err(429, "TOO_MANY_ATTEMPTS", "too many wrong passwords: try again in 15 minutes")
    if not (user.password_hash and await run_in_threadpool(verify_password, body.current, user.password_hash)):
        LOGIN_FAILS.hit(key)
        raise _err(403, "WRONG_PASSWORD", "the current password is wrong")  # not 401: that means signed out
    check_password(body.new)
    hashed = await run_in_threadpool(hash_password, body.new)
    keep = _sha(request.cookies.get(cookie(), ""))
    async with SessionLocal() as s:
        await s.execute(update(User).where(User.id == user.id).values(password_hash=hashed))
        await s.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.token_sha256 != keep))
        await s.commit()


# ---------------------------------------------------------------- the user's Zernio key
#
# Stored sealed (app.core.secrets), never sent back (last4 only). zernio_key_gen counts the credentials: a post that
# may be live never replays under another (publish._send, KEY_CHANGED). Changing or removing the key takes the user
# row FOR UPDATE, which waits out publish_post's claim (FOR SHARE), and is refused while a post is PUBLISHING.

KEY_SHAPE = re.compile(r"(sk|zrk)_[A-Za-z0-9_-]{8,196}")  # a full-access or restricted Zernio key, up to 200 chars
REFUSED = "Zernio refused the key"
NO_ACCOUNTS = "Zernio won't list your accounts with this key: create one with full access (Full, Read-write)"
CLAIMED = "this Zernio account is already connected to another Clipper user"


async def zernio_key(s: AsyncSession) -> str | None:
    """The request user's Zernio key; None when there is none (or SECRETS_KEY can't open it)."""
    return unseal(await s.scalar(select(User.zernio_key_enc).where(User.id == s.info["uid"])))


async def _verify(key: str) -> dict | None:
    """GET /v1/auth/verify: the key's Zernio user ({userId, name, email, ...}), None when Zernio refuses the key.
    502 when Zernio can't be asked."""
    try:
        who = await zernio.verify(key)
    except zernio.ZernioError as e:
        if e.status == 401:
            return None
        raise _err(502, "ZERNIO_ERROR", f"could not check the key with Zernio: {e}") from None
    return who if who.get("valid") is True and who.get("userId") else None


def _verified(who: dict) -> dict:
    return {"zernio_user_id": who["userId"], "zernio_email": who.get("email"), "zernio_name": who.get("name"),
            "zernio_key_status": "valid", "zernio_checked_at": _now(), "zernio_error": None}  # fmt: skip


async def _not_elsewhere(s: AsyncSession, uid: int, who: dict) -> None:
    """One Clipper user per Zernio user: Zernio scopes idempotency keys per user, and it keeps two users off one
    Instagram account. User 1's .env key was stored without its Zernio user (the import makes no network call): asked
    here first, so nobody takes the operator's Zernio account, and the operator's own ZERNIO_ACCOUNT_CHANGED check
    has an account to compare with. May commit: call it before locking the user row."""
    unknown = select(User.id, User.zernio_key_enc).where(
        User.env_imported_at.is_not(None), User.zernio_user_id.is_(None), User.zernio_key_enc.is_not(None))  # fmt: skip
    for other, sealed in (await s.execute(unknown)).all():
        if (key := unseal(sealed)) and (them := await _verify(key)):
            q = update(User).where(User.id == other, User.zernio_key_enc == sealed, User.zernio_user_id.is_(None))
            await s.execute(q.values(zernio_user_id=them["userId"]))
            try:
                await s.commit()
            except IntegrityError:  # another user holds it already
                await s.rollback()
    if await s.scalar(select(User.id).where(User.zernio_user_id == who["userId"], User.id != uid)):
        raise _err(409, "ZERNIO_USER_CLAIMED", CLAIMED)


async def _not_publishing(s: AsyncSession, uid: int) -> None:
    """Under the user row's FOR UPDATE: no post may be publishing with the key it is about to lose."""
    if await s.scalar(select(exists().where(Post.user_id == uid, Post.status == "PUBLISHING"))):
        raise _err(409, "KEY_IN_USE", "a post is publishing with your key right now: try again in a minute")


async def _sync(s: AsyncSession, u: User, key: str) -> KeyCheck:
    """Pull the key's Instagram accounts (GET /v1/accounts) into the user's, and name the ones Zernio holds back
    because they are beyond the plan's limit (includeOverLimit)."""
    try:
        parsed = zernio.parse_accounts(await zernio.list_accounts(key))
        skipped = await account_sync.upsert(s, u.id, parsed)
        every = zernio.parse_accounts(await zernio.list_accounts(key, over_limit=True))
    except zernio.ZernioError as e:
        if e.status not in (401, 403):
            raise _err(502, "ZERNIO_ERROR", f"the key is saved, but listing its accounts failed ({e}): Re-check") from None
        # the key itself (the call names no account): 403 is a restricted zrk_ key without Zernio's accounts group
        values = {"zernio_key_status": "invalid", "zernio_error": REFUSED if e.status == 401 else NO_ACCOUNTS}
        await s.execute(update(User).where(User.id == u.id, User.zernio_key_enc == u.zernio_key_enc).values(**values))
        await s.commit()
        u = await s.get(User, u.id, populate_existing=True)
        return KeyCheck(zernio=_zernio_out(u), accounts=[], skipped=[], over_limit=[])
    listed = {p["zernio_account_id"] for p in parsed}
    over_limit = [p["username"] for p in every if p["zernio_account_id"] not in listed]
    found = [p["username"] for p in parsed if p["username"] not in skipped]
    return KeyCheck(zernio=_zernio_out(u), accounts=found, skipped=skipped, over_limit=over_limit)


@router.put("/me/zernio-key")
async def put_zernio_key(body: ZernioKeyIn, user: CurrentUser, s: Db) -> KeyCheck:
    """Check the key with Zernio, store it, and pull its Instagram accounts. 422 ZERNIO_KEY_INVALID (not a key, or
    refused); 409 ZERNIO_USER_CLAIMED (another Clipper user has that Zernio account), ZERNIO_ACCOUNT_CHANGED (your
    accounts are another Zernio account's), KEY_IN_USE (a post is publishing); 503 when keys can't be stored."""
    key = body.key.strip()
    if not KEY_SHAPE.fullmatch(key):
        raise _err(422, "ZERNIO_KEY_INVALID", "a Zernio API key starts with sk_ or zrk_ (Zernio: Settings, API keys)")
    try:
        sealed = seal(key)
    except RuntimeError:
        raise _err(503, "SECRETS_KEY_MISSING", "this server can't store keys yet (SECRETS_KEY is not set)") from None
    if (who := await _verify(key)) is None:
        raise _err(422, "ZERNIO_KEY_INVALID", f"{REFUSED}: copy it again from Zernio's API keys page")
    await _not_elsewhere(s, user.id, who)
    u = await s.get(User, user.id, with_for_update=True)  # until the commit
    has_accounts = select(exists().where(Account.user_id == u.id))
    if u.zernio_user_id not in (None, who["userId"]) and await s.scalar(has_accounts):
        raise _err(409, "ZERNIO_ACCOUNT_CHANGED", "this key is another Zernio account's; your Instagram accounts and "
                   "posts belong to the Zernio account you connected first")  # fmt: skip
    await _not_publishing(s, u.id)
    if unseal(u.zernio_key_enc) != key:  # another credential: what may be live under the old one never replays
        u.zernio_key_gen += 1
    u.zernio_key_enc, u.zernio_key_last4 = sealed, key[-4:]
    for k, v in _verified(who).items():
        setattr(u, k, v)
    try:
        await s.commit()
    except IntegrityError:  # uq_users_zernio_user_id: another user connected it meanwhile
        raise _err(409, "ZERNIO_USER_CLAIMED", CLAIMED) from None
    return await _sync(s, u, key)


@router.post("/me/zernio-key/check")
async def check_zernio_key(user: CurrentUser, s: Db) -> KeyCheck:
    """The Re-check button: verify the stored key again and re-pull its accounts. A key Zernio refuses is marked
    invalid (200: zernio.status, zernio.error); a working one valid again, and a paused user's posts go out again
    (unless Zernio won't list the accounts with it)."""
    u = await s.get(User, user.id)
    if (key := unseal(u.zernio_key_enc)) is None:
        raise _err(409, "ZERNIO_KEY_MISSING", "there is no Zernio key to check: paste yours first")
    who = await _verify(key)
    if who is not None:
        await _not_elsewhere(s, u.id, who)
    refused = {"zernio_key_status": "invalid", "zernio_error": REFUSED, "zernio_checked_at": _now()}
    values = _verified(who) if who else refused
    # the key as read: a new one stored meanwhile has its own check
    await s.execute(update(User).where(User.id == u.id, User.zernio_key_enc == u.zernio_key_enc).values(**values))
    try:
        await s.commit()
    except IntegrityError:
        raise _err(409, "ZERNIO_USER_CLAIMED", CLAIMED) from None
    u = await s.get(User, user.id, populate_existing=True)
    if who is None:
        return KeyCheck(zernio=_zernio_out(u), accounts=[], skipped=[], over_limit=[])
    return await _sync(s, u, key)


@router.delete("/me/zernio-key", status_code=204)
async def delete_zernio_key(user: CurrentUser, s: Db) -> None:
    """Any time but while a post is publishing (409 KEY_IN_USE): a post that may be live never replays under a later
    key, so this can't make a second Reel. The Zernio account stays recorded while you have its Instagram accounts, so
    a later key must be that account's (ZERNIO_ACCOUNT_CHANGED); with none, another Clipper user may connect it."""
    await s.get(User, user.id, with_for_update=True)
    await _not_publishing(s, user.id)
    none = {"zernio_key_enc": None, "zernio_key_last4": None, "zernio_key_status": "none", "zernio_checked_at": None,
            "zernio_error": None}  # fmt: skip
    if not await s.scalar(select(exists().where(Account.user_id == user.id))):
        none |= {"zernio_user_id": None, "zernio_email": None, "zernio_name": None}
    await s.execute(update(User).where(User.id == user.id).values(**none))
    await s.commit()
