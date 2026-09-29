"""Operator commands. `render` runs the pipeline in this process, without the api or the queue (worker
container only: ffmpeg):

    docker compose run --rm worker python -m app.cli render <clip_id> <brand_id> [--x .. --y .. --w .. --opacity ..]

Probes the clip first if it isn't READY, renders it with the brand's logo (default overlay, overridden by
any --x/--y/--w/--opacity) and prints the output path.

Users (any backend container): set-password <username> (asks, or reads one line from stdin; signs the user out
everywhere), list-users, disable-user / enable-user <username>, set-quota <username> <GB|none>.
The migrate service runs db-grants (the api's database role) and bootstrap (user 1 from .env) every time.
"""

import argparse
import base64
import binascii
import getpass
import re
import sys
from datetime import UTC, datetime

import psycopg
from psycopg import sql
from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from app.api.auth import MAX_PASSWORD, MIN_PASSWORD, USERNAME, hash_password
from app.api.bots import TOKEN
from app.core import secrets
from app.core.config import settings
from app.core.db import SyncSession
from app.models import AuthSession, Brand, Render, SourceClip, TelegramBot, User, cas
from app.schemas import OverlayConfig
from app.services import storage
from app.tasks.media import probe_clip, render


def _run(task, model, id: int, in_progress: list[str]) -> None:
    """Run a job in this process. Ctrl-C (or any crash) must not strand the row in PROBING/RENDERING:
    no queued job exists to finish it, and the api only retries or deletes FAILED rows."""
    try:
        task(id)
    except BaseException:
        with SyncSession() as s, s.begin():
            s.execute(cas(model, id, in_progress, status="FAILED", error_code="INTERRUPTED"))
        raise


def db_grants() -> None:
    """clipper_app, the api's role: not a superuser, so row-level security applies to it. Rights on every table
    and sequence (Procrastinate's too: the api defers jobs) but alembic_version. Every migrate runs it (a
    restored dump brings no role, new tables no grants); needs the superuser."""
    with psycopg.connect(settings.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1), autocommit=True) as c:
        verb = "ALTER" if c.execute("SELECT 1 FROM pg_roles WHERE rolname = 'clipper_app'").fetchone() else "CREATE"
        c.execute(sql.SQL("{} ROLE clipper_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
            sql.SQL(verb), sql.Literal(settings.APP_DB_PASSWORD)))  # fmt: skip
        c.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO clipper_app")
        c.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO clipper_app")
        c.execute("REVOKE ALL ON alembic_version FROM clipper_app")
    print("db-grants: clipper_app ok")


def import_env(s, user: User) -> None:
    """The one-shot .env import into user 1, the operator (users.env_imported_at): ZERNIO_API_KEY becomes its sealed
    Zernio key, generation 1 (migration 0007 gave the posts it may already have sent key_gen 1, so those still
    replay), valid without a network call (a Re-check fills in the Zernio user). TELEGRAM_BOT_TOKEN / _CHAT_ID and
    the _2 and _3 pairs become its bots, already paired with those chats (the first with alerts on, as before; the
    bot service fills in their @names). The code never reads these .env values after this. It waits, unstamped,
    until a SECRETS_KEY can seal (whatever .env holds). Every .env secret user 1 takes over is imported here, in
    this one step."""
    if user.env_imported_at is not None:
        return
    if not secrets.ready():
        return print("bootstrap: SECRETS_KEY is not set: the .env import into user 1 waits for it")
    key = settings.ZERNIO_API_KEY.strip()
    if key and user.zernio_key_enc is None:
        user.zernio_key_enc, user.zernio_key_last4 = secrets.seal(key), key[-4:]
        user.zernio_key_status, user.zernio_key_gen = "valid", 1
    bots = 0
    for n, alerts in (("", True), ("_2", False), ("_3", False)):
        token, chat = getattr(settings, f"TELEGRAM_BOT_TOKEN{n}").strip(), getattr(settings, f"TELEGRAM_CHAT_ID{n}").strip()
        if not (token or chat):
            continue
        if not (TOKEN.fullmatch(token) and re.fullmatch(r"-?\d{1,15}", chat)):
            print(f"bootstrap: TELEGRAM_BOT_TOKEN{n} / TELEGRAM_CHAT_ID{n} is not a bot token and a chat id: skipped")
            continue
        q = insert(TelegramBot).values(user_id=user.id, bot_id=int(token.split(":")[0]), token_enc=secrets.seal(token),
                                       chat_id=int(chat), alerts=alerts)  # fmt: skip
        bots += s.scalar(q.on_conflict_do_nothing(index_elements=["bot_id"]).returning(TelegramBot.id)) is not None
    user.env_imported_at = datetime.now(UTC)
    print(f"bootstrap: .env imported into user 1 (Zernio key: {'yes' if key else 'none in .env'}, Telegram bots: {bots})")


def bootstrap() -> None:
    """User 1, the operator, from the migrate service's env. Idempotent, and a missing or bad value is skipped
    with a message, never a failed migrate: CLIPPER_USER renames it; CLIPPER_PASSWORD_HASH (a bcrypt hash, or
    the base64 of one so compose doesn't interpolate its $) becomes its password while it has none; then the
    one-shot .env import (import_env)."""
    name, hashed = settings.CLIPPER_USER.strip().lower(), settings.CLIPPER_PASSWORD_HASH.strip()
    with SyncSession() as s, s.begin():
        if (user := s.get(User, 1, with_for_update=True)) is None:
            return print("bootstrap: there is no user 1")
        if name and name != user.username:
            if USERNAME.fullmatch(name) and not s.scalar(select(User.id).where(User.username == name)):
                user.username = name
            else:
                print(f"bootstrap: CLIPPER_USER {name!r} is not a valid free username; user 1 stays {user.username!r}")
        if hashed and user.password_hash is None:
            if not hashed.startswith("$2"):
                try:
                    hashed = base64.b64decode(hashed, validate=True).decode()
                except (binascii.Error, UnicodeDecodeError):
                    pass
            if re.fullmatch(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}", hashed):
                user.password_hash = hashed
            else:
                print("bootstrap: CLIPPER_PASSWORD_HASH is not a bcrypt hash (or the base64 of one): skipped")
        import_env(s, user)
    print(f"bootstrap: user 1 is {user.username!r}, password {'set' if user.password_hash else 'not set'}")


def _user(s, username: str, **values) -> int:
    """Update a user by name (case-insensitive) and return its id; exit if there is none."""
    uid = s.scalar(update(User).where(User.username == username.lower()).values(**values).returning(User.id))
    if uid is None:
        sys.exit(f"no user {username!r}")
    return uid


def users(args) -> None:
    if args.command == "list-users":
        with SyncSession() as s:
            for u in s.scalars(select(User).order_by(User.id)):
                quota = "unlimited" if u.quota_bytes is None else f"{u.quota_bytes / 1024**3:g} GB"
                print(f"{u.id:>4}  {u.username:<32} {u.created_at:%Y-%m-%d}  {'disabled' if u.disabled_at else 'active':<8}"
                      f"  zernio {u.zernio_key_status:<7}  quota {quota}")  # fmt: skip
        return
    if args.command == "set-password":
        pw = getpass.getpass("New password: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
        if not MIN_PASSWORD <= len(pw) <= MAX_PASSWORD:
            sys.exit(f"the password needs {MIN_PASSWORD} to {MAX_PASSWORD} characters")
        values = {"password_hash": hash_password(pw)}
    elif args.command == "disable-user":
        values = {"disabled_at": datetime.now(UTC)}
    elif args.command == "enable-user":
        values = {"disabled_at": None}
    else:  # set-quota
        values = {"quota_bytes": None if args.gb == "none" else int(float(args.gb) * 1024**3)}
    with SyncSession() as s, s.begin():
        uid = _user(s, args.username, **values)
        if args.command in ("set-password", "disable-user"):
            s.execute(delete(AuthSession).where(AuthSession.user_id == uid))  # signed out everywhere
    print(f"{args.username}: {args.command} done")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("render", help="probe if needed, render, print the output path")
    cmd.add_argument("clip_id", type=int)
    cmd.add_argument("brand_id", type=int)
    for name in ("x", "y", "w", "opacity"):
        cmd.add_argument(f"--{name}", type=float, help="overlay fraction (default: the brand's)")
    sub.add_parser("db-grants", help="create/update the api's database role and its rights (superuser)")
    sub.add_parser("bootstrap", help="user 1's username and password from CLIPPER_USER / CLIPPER_PASSWORD_HASH, and "
                   "its one-shot .env import (Zernio key, Telegram bots)")
    sub.add_parser("list-users")
    for name in ("set-password", "disable-user", "enable-user", "set-quota"):
        sub.add_parser(name).add_argument("username")
    sub.choices["set-quota"].add_argument("gb", help="storage cap in GB, or none (unlimited)")
    args = parser.parse_args()
    if args.command == "db-grants":
        return db_grants()
    if args.command == "bootstrap":
        return bootstrap()
    if args.command != "render":
        return users(args)

    with SyncSession() as s, s.begin():
        clip, brand = s.get(SourceClip, args.clip_id), s.get(Brand, args.brand_id)
        if clip is None or brand is None or brand.user_id != clip.user_id or not brand.logo_key:
            sys.exit("clip or brand not found (or they belong to different users), or the brand has no logo")
        if clip.status != "READY" and not (
            clip.raw_key and s.execute(cas(SourceClip, clip.id, ["FAILED", "PROBING"], status="PROBING")).rowcount
        ):
            sys.exit(f"clip {clip.id} is {clip.status} with no file to probe")
        overrides = {k: getattr(args, k) for k in ("x", "y", "w", "opacity") if getattr(args, k) is not None}
        try:
            overlay = OverlayConfig(**(brand.default_overlay_config | overrides)).model_dump()
        except ValidationError as e:  # exits before commit: the probe CAS above is rolled back
            err = e.errors()[0]
            sys.exit(f"bad overlay: {'.'.join(map(str, err['loc'])) or 'x, w'}: {err['msg']}")

    if clip.status != "READY":
        _run(probe_clip, SourceClip, clip.id, ["PROBING"])
        with SyncSession() as s:
            clip = s.get(SourceClip, clip.id)
        if clip.status != "READY":
            sys.exit(f"probe failed: {clip.error_code} {clip.error_detail}")

    with SyncSession() as s, s.begin():
        r = Render(source_clip_id=clip.id, brand_id=brand.id, overlay_config=overlay, user_id=clip.user_id)
        s.add(r)
        s.flush()
        render_id = r.id
    _run(render, Render, render_id, ["PENDING", "RENDERING"])
    with SyncSession() as s:
        r = s.get(Render, render_id)
    if r.status != "READY":
        sys.exit(f"render {render_id} {r.status} {r.error_code}\n{(r.ffmpeg_log or '')[-3000:]}")
    print(storage.path_for(r.output_key))


if __name__ == "__main__":
    main()
