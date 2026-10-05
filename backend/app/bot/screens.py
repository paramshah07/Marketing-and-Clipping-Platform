"""What every command, button and answer does (docs/telegram-bot.md section 6). Each change is the api call
the web app makes, so the api's guards apply unchanged: this module only asks and shows.

Callback data is 'verb:args' (64 bytes at most). Card buttons carry ids, so they work after a restart; forms
and lists (render editor, schedule form, /ready, /calendar...) keep their state in Bot.views under their
message id. A verb ending in ! is a confirmed action and runs once per message (core.Bot.on_callback)."""

import asyncio
import json
import re
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import PurePath
from zoneinfo import ZoneInfo

from app.bot import fmt
from app.bot.clients import ApiError, TelegramError
from app.bot.fmt import h
from app.core.config import settings
from app.services import links

COMMANDS, CALLBACKS, ANSWERS, CARDS, LISTS = {}, {}, {}, {}, {}
PAGE = 10
TG_DOWNLOAD_MAX = 20 * 1024**2  # getFile
TG_UPLOAD_MAX = 50 * 1024**2  # sendVideo / sendDocument
LOGO_MAX = 10 * 1024**2  # pipeline.MAX_LOGO_BYTES
# One process runs every user's bots: at most this many files (up to 50 MB each) in its memory at once; the rest wait
FILES = asyncio.Semaphore(3)
VIDEO_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}  # what POST /api/clips takes
DOCUMENTS = {".docx", ".txt", ".csv", ".md", ".rtf", ".html", ".htm", ".xlsx", ".pptx", ".odt"}  # utils.ts DOCUMENTS
ZERNIO_URL = "https://zernio.com"
NAMES = {"c": "Clip", "r": "Render", "p": "Post", "b": "Brand", "a": "Account", "t": "Saved caption", "i": "Saved cover"}
FAILED = ("FAILED", "DEAD_LETTER")
STATUS = {"DRAFT": "Draft", "SCHEDULED": "Scheduled", "PUBLISHING": "Publishing", "PUBLISHED": "Published",
          "FAILED": "Failed", "DEAD_LETTER": "Failed", "CANCELLED": "Cancelled"}  # fmt: skip
HEADLINE = {"DRAFT": "Waiting for approval", "SCHEDULED": "Scheduled to publish", "PUBLISHING": "Publishing now",
            "PUBLISHED": "Live on Instagram", "FAILED": "Publishing failed", "DEAD_LETTER": "Publishing failed",
            "CANCELLED": "Cancelled"}  # fmt: skip  (Recover.tsx)
TITLES = {  # Recover.tsx: a failure's short title; PostOut.cause has the sentence
    "ACCOUNT_DISCONNECTED": "Account disconnected", "CONTENT_REJECTED": "Instagram rejected the video",
    "RATE_LIMITED": "Instagram rate limit reached", "NETWORK_ERROR": "Couldn't reach Instagram",
    "TOO_LONG": "Video too long for a Reel", "TOO_SHORT": "Video too short for a Reel", "RENDER_FAILED": "Render failed",
    "WORKER_CRASHED": "Worker crashed while publishing", "MISSED": "Missed its slot",
    "NO_FREE_SLOT": "No free slot to move to", "WINDOW_EXPIRED": "Outcome unknown", "UNKNOWN": "Zernio reported a failure",
    "KEY_CHANGED": "Sent with your previous Zernio key", "ZERNIO_KEY_INVALID": "Zernio refused your key",
    "ZERNIO_PAYMENT_REQUIRED": "Zernio payment failed", "ZERNIO_KEY_MISSING": "No Zernio key",
    "PROFILE_OVER_LIMIT": "Beyond your Zernio plan's limit",
}  # fmt: skip
KEY_CODES = {"ZERNIO_KEY_INVALID", "ZERNIO_PAYMENT_REQUIRED", "ZERNIO_KEY_MISSING"}  # Recover.tsx: fixed in Settings
MAYBE_LIVE = {"NETWORK_ERROR", "WORKER_CRASHED", "WINDOW_EXPIRED", "KEY_CHANGED"}  # Recover.tsx: the Reel may be live already
NO_ACCOUNT = "No account can post: connect one in Zernio, then /accounts and Sync accounts."
TIME_Q = "Send the time in @{u}'s zone ({tz}): 18:30, 6:30pm, tomorrow 9am, fri 13:00, 2026-10-02 09:00 or now."
MENU = [
    ("status", "What's running and what's failing"), ("clips", "The library (add text to search)"),
    ("renders", "Recent renders"), ("ready", "Renders ready to schedule"), ("calendar", "An account's week, slot by slot"),
    ("drafts", "Drafts waiting for approval"), ("failed", "Failed posts to recover"),
    ("published", "Published posts (add text to search)"), ("brands", "Brands, logos and caption templates"),
    ("captions", "Saved captions (Customizations)"), ("covers", "Saved Reel covers (Customizations)"),
    ("accounts", "Instagram accounts and posting slots"), ("help", "What this bot does"), ("cancel", "Drop my question"),
]  # fmt: skip
HELP = """<b>Clipper</b>: everything the web app does, from here.

<b>Import</b>: send a video (up to 20 MB; a caption like @creator sets the creator), a link, several links, or a document full of links (docx, xlsx, pptx, odt, txt, csv, md, rtf, html).

/clips [text] the library · /renders · /ready renders to schedule
/calendar an account's week · /drafts approve · /failed recover
/published history · /brands · /captions · /covers · /accounts · /status
/cancel drops a question I asked.

Tap an id to open it: /c12 clip, /r34 render, /p56 post, /b4 brand, /t3 caption, /i5 cover, /a1 account. A clip's card has Render… (it starts from your default brand, caption and cover in Customizations), a render's card has Schedule… and Post now."""


class Alert(Exception):
    """Shown to the operator as is: an alert on a button, or a reply to a message."""


def _register(table: dict, *names):
    def deco(fn):
        for n in names:
            table[n] = fn
        return fn

    return deco


def command(*names):
    return _register(COMMANDS, *names)


def button(*verbs):
    return _register(CALLBACKS, *verbs)


def answer(kind):
    return _register(ANSWERS, kind)


def card(kind):
    return _register(CARDS, kind)


def listing(name):
    return _register(LISTS, name)


# ---------------------------------------------------------------- helpers


def now() -> datetime:
    return datetime.now(UTC)


def web(path: str) -> list:
    """An 'Open in Clipper' row when the web app has a public https address (a tunnel): Telegram refuses
    localhost buttons."""
    return [[("Open in Clipper", settings.APP_BASE_URL + path)]] if settings.APP_BASE_URL.startswith("https://") else []


def usable(accs: list[dict]) -> list[dict]:
    return [a for a in accs if a["connection_status"] == "connected" and not a["disabled_at"]]


def pick_account(accs: list[dict], account_id: int | None) -> dict | None:
    return next((a for a in accs if a["id"] == account_id), accs[0] if accs else None)


async def account(bot, account_id: int, fresh: bool = False) -> dict:
    if (a := next((a for a in await bot.accounts(fresh) if a["id"] == account_id), None)) is None:
        raise Alert(f"Account {account_id} not found.")
    return a


async def brands_by_id(bot) -> dict[int, dict]:
    both = [*await bot.api.get("/api/brands"), *await bot.api.get("/api/brands", archived="true")]
    return {b["id"]: b for b in both}


async def lookups(bot) -> tuple[dict[int, dict], dict[int, dict]]:
    return {c["id"]: c for c in await bot.api.get("/api/clips")}, await brands_by_id(bot)


def brand_name(brands: dict, brand_id: int | None) -> str:
    return "No logo" if brand_id is None else brands[brand_id]["name"] if brand_id in brands else f"Brand {brand_id}"


def default_of(items) -> dict | None:
    """The one marked default in Customizations (a brand, saved caption or saved cover), if any."""
    return next((x for x in items if x["is_default"]), None)


def text_of(m: dict) -> str:
    return (m.get("text") or m.get("caption") or "").strip()


def optional(text: str) -> str | None:
    """'-' clears an optional field."""
    return None if text.strip() == "-" else text.strip()


def caption_of(m: dict) -> str:
    cap = "" if text_of(m) == "-" else text_of(m)
    if len(cap) > fmt.CAPTION_MAX or fmt.hashtags(cap) > fmt.HASHTAG_MAX:
        raise Alert(f"Instagram refuses captions over {fmt.CAPTION_MAX} characters or {fmt.HASHTAG_MAX} hashtags "
                    f"(this one: {len(cap)}, {fmt.hashtags(cap)}).")  # fmt: skip
    return cap


def read_time(text: str, tz: str, day=None) -> datetime:
    at = fmt.parse_when(text, tz, now(), day)
    if at is None:
        raise Alert("I can't read that as a time. Try 18:30, 6:30pm, tomorrow 9am, fri 13:00 or 2026-10-02 09:00.")
    if at < now() - timedelta(minutes=1):
        raise Alert("That time has passed: pick now or later.")
    return at


# /api/status publishing_off: why publishing is off
OFF_WHY = {
    "switch": "PUBLISHING_ENABLED is off on the server",
    "no_key": "you have no Zernio key yet: add it in Settings",
    "key_invalid": "Zernio refused your key: update it in Settings",
}


async def publishing_on(bot) -> None:
    st = await bot.api.get("/api/status")
    if not st["publishing_enabled"]:
        why = OFF_WHY.get(st.get("publishing_off"), "see Settings")
        raise Alert(f"Publishing is off ({why}): nothing can post now.")


def post_time(p: dict) -> datetime:
    return fmt.iso(p["published_at"] or p["scheduled_for"])


async def post_line(bot, p: dict, extra: str = "") -> str:
    r, tz = p["render"], await bot.tz(p["account_id"])
    clip = fmt.short_url(r["clip_name"] or f"Clip {r['clip_id']}")
    bits = [f"/p{p['id']}", fmt.when(post_time(p), tz, zone=False), f"@{h(p['account_username'])}",
            h(r["brand_name"] or "No logo"), h(fmt.cut(clip, 32))]  # fmt: skip
    return " · ".join(bits) + extra


def import_rows(prefix: str) -> list:
    return [[("Import", f"{prefix}:go"), ("Cancel", f"{prefix}:x")]]


def send_video(bot, url: str | None, size: int | None, caption: str, dims: tuple = ()) -> str:
    """Send an MP4 (or a .mov / .webm source as a file) in the background, if Telegram takes its size."""
    if not url:
        raise Alert("There's no file to send.")
    if (size or 0) > TG_UPLOAD_MAX:
        raise Alert(f"It is {fmt.mb(size)}: Telegram takes up to 50 MB from bots. Download it in the web app.")
    name = PurePath(url).name
    mp4 = name.lower().endswith(".mp4")
    width, height, duration = (*dims, None, None, None)[:3]

    async def go():
        async with FILES:
            data = await bot.api.media(url)
            kind = VIDEO_TYPES.get(PurePath(name).suffix.lower(), "application/octet-stream")
            await bot.tg(
                "sendVideo" if mp4 else "sendDocument", files={"video" if mp4 else "document": (name, data, kind)},
                chat_id=bot.chat, caption=caption, supports_streaming=mp4 or None, width=width if mp4 else None,
                height=height if mp4 else None, duration=int(duration) if mp4 and duration else None,
            )  # fmt: skip

    bot.spawn(go())
    return "Sending the video…"


# ---------------------------------------------------------------- lists (one message, edited in place)


def paged(items: list, v: dict) -> tuple[list, list]:
    """This page's items and a ◀ n/N ▶ row (none when it all fits)."""
    pages = max(1, -(-len(items) // PAGE))
    page = v["page"] = min(max(v.get("page", 0), 0), pages - 1)
    nav = ([("◀", f"pg:{page - 1}")] if page else []) + [(f"{page + 1}/{pages}", "noop")]
    nav += [("▶", f"pg:{page + 1}")] if page < pages - 1 else []
    return items[page * PAGE : (page + 1) * PAGE], [nav] if pages > 1 else []


async def open_list(bot, name: str, **state) -> None:
    v = {"kind": "list", "list": name, "page": 0, "picking": None, **state}
    text, rows = await LISTS[name](bot, v)
    bot.keep(await bot.send(text, rows), v)


async def show_list(bot, msg: dict, v: dict) -> None:
    await bot.edit(msg, *await LISTS[v["list"]](bot, v))


@button("ls")
async def list_button(bot, msg, name):
    await COMMANDS[name](bot, "")


@button("pg")
async def page_button(bot, msg, n):
    v = bot.view(msg, "list")
    v["page"] = int(n)
    await show_list(bot, msg, v)


@button("f")
async def filter_button(bot, msg, key, value=""):
    """f:<key>:<value> sets a list filter; f:picking:<key> shows that filter's choices (f:picking: goes back)."""
    v = bot.view(msg, "list")
    v["page"], v["picking"] = 0, None
    if key == "picking":
        v["picking"] = value or None
    else:
        v[key] = int(value) if re.fullmatch(r"-?\d+", value) else value
        if key == "acc" and v[key]:
            bot.last_account = v[key]
    await show_list(bot, msg, v)


@button("noop")
async def noop(bot, msg, *args):
    return None


@command("clips", "library")
async def clips_cmd(bot, arg):
    await open_list(bot, "clips", q=arg)


@listing("clips")
async def clips_list(bot, v):
    clips, renders = await bot.api.get("/api/clips"), await bot.api.get("/api/renders")
    n = Counter(r["source_clip_id"] for r in renders)
    q = v["q"].lower()
    shown = [c for c in clips if not q or any(q in (s or "").lower() for s in (fmt.clip_name(c), c["source_creator_handle"], c["source_url"]))]
    items, nav = paged(shown, v)
    lines = [f"<b>Library</b> · {fmt.plural(len(shown), 'clip')}" + (f" matching “{h(v['q'])}”" if q else "")]
    for c in items:
        s = c["status"]
        state = (f"<b>Failed</b>: {h(fmt.CAUSES.get(c['error_code'], 'failed'))}" if s == "FAILED"
                 else fmt.mmss(c["duration_s"]) if s == "READY" else f"{s.capitalize()}…")  # fmt: skip
        bits = [f"/c{c['id']}", state, h(fmt.cut(fmt.clip_name(c), 44))]
        bits += [h(c["source_creator_handle"])] if c["source_creator_handle"] else []
        bits += [fmt.plural(n[c["id"]], "render")] if s == "READY" else []
        lines.append(" · ".join(bits))
    if not shown:
        lines.append("No clips match." if q else "No clips yet. Send a video, a link or a document to import.")
    return "\n".join(lines), nav


@command("renders")
async def renders_cmd(bot, arg):
    await open_list(bot, "renders", clip=None)


@listing("renders")
async def renders_list(bot, v):
    rs = await bot.api.get("/api/renders", clip_id=v["clip"])
    clips, brands = await lookups(bot)
    items, nav = paged(rs, v)
    lines = ["<b>Renders</b>" + (f" of clip {v['clip']}" if v["clip"] else "") + f" · {len(rs)}"]
    for r in items:
        state = {"READY": f"{fmt.mmss(r['duration_s'])} · {fmt.mb(r['size_bytes'])}", "PENDING": "Queued", "RENDERING": "Rendering…",
                 "FAILED": f"<b>Failed</b>: {h(fmt.CAUSES.get(r['error_code'], 'failed'))}"}[r["status"]]  # fmt: skip
        c = clips.get(r["source_clip_id"])
        name = fmt.clip_name(c) if c else f"Clip {r['source_clip_id']}"
        lines.append(f"/r{r['id']} · {state} · {h(brand_name(brands, r['brand_id']))} · {h(fmt.cut(name, 36))}")
    if not rs:
        lines.append("No renders yet: open a clip (/clips) and tap Render.")
    return "\n".join(lines), nav + ([[("Render another", f"ce:{v['clip']}")]] if v["clip"] else [])


@command("drafts")
async def drafts_cmd(bot, arg):
    await open_list(bot, "drafts")


@listing("drafts")
async def drafts_list(bot, v):
    posts = await bot.api.get("/api/posts", status=["DRAFT"])
    items, nav = paged(posts, v)
    lines = [f"<b>Drafts</b> · {len(posts)} waiting for approval"] + [await post_line(bot, p) for p in items]
    if not posts:
        lines.append("Nothing to approve.")
    return "\n".join(lines), nav + ([[(f"Approve all {len(posts)}", "da")]] if posts else [])


@button("da")
async def drafts_approve_ask(bot, msg):
    v = bot.view(msg, "list")
    posts = await bot.api.get("/api/posts", status=["DRAFT"])
    if not posts:
        raise Alert("No drafts left.")
    v["approve"] = [p["id"] for p in posts]  # exactly the ones listed, as the web app's confirm
    late = sum(fmt.iso(p["scheduled_for"]) < now() - timedelta(minutes=1) for p in posts)
    lines = [f"<b>Approve {fmt.plural(len(posts), 'draft')}?</b> Each one then publishes at its time."]
    lines += [await post_line(bot, p) for p in posts[:15]] + ([f"…and {len(posts) - 15} more"] if len(posts) > 15 else [])
    if late:
        lines.append(f"{late} past due: {'it moves' if late == 1 else 'they move'} to the next free slot.")
    await bot.edit(msg, "\n".join(lines), [[(f"Approve {len(posts)}", "da!"), ("Back", "pg:0")]])


@button("da!")
async def drafts_approve(bot, msg):
    v = bot.view(msg, "list")
    bad = []
    for pid in v.get("approve", []):
        try:
            await bot.api.post(f"/api/posts/{pid}/approve")
        except ApiError as e:
            bad.append(f"/p{pid}: {h(e.message)}")
    bot.views.pop(msg["message_id"], None)
    done = len(v.get("approve", [])) - len(bad)
    await bot.edit(msg, f"<b>Approved {fmt.plural(done, 'draft')}</b>" + "".join(f"\n{b}" for b in bad), [[("Calendar", "ls:calendar")]])
    return f"Approved {done}"


@command("failed")
async def failed_cmd(bot, arg):
    await open_list(bot, "failed")


@listing("failed")
async def failed_list(bot, v):
    posts = sorted(await bot.api.get("/api/posts", status=list(FAILED)), key=lambda p: fmt.iso(p["scheduled_for"]))
    items, nav = paged(posts, v)
    lines = [f"<b>Failed posts</b> · {len(posts)}, oldest first"]
    for p in items:
        lines.append(await post_line(bot, p, f" · <b>{h(TITLES.get(p['error_code'], p['error_code'] or 'Failed'))}</b>"))
    if not posts:
        lines.append("None: everything went out or is on its way.")
    return "\n".join(lines), nav


RANGES = [7, 30, 90, 365]


@command("published")
async def published_cmd(bot, arg):
    await open_list(bot, "published", q=arg, days=30, acc=0, brand=0)


@listing("published")
async def published_list(bot, v):
    accs, brands = await bot.accounts(), await bot.api.get("/api/brands")
    if v["picking"] == "acc":
        return "Show which account?", [[("All accounts", "f:acc:0")]] + [[(f"@{a['username']}", f"f:acc:{a['id']}")] for a in accs] + [[("Back", "f:picking:")]]
    if v["picking"] == "brand":
        return "Show which brand?", [[("All brands", "f:brand:0")]] + [[(b["name"], f"f:brand:{b['id']}")] for b in brands] + [[("Back", "f:picking:")]]
    since = now() - timedelta(days=v["days"])
    posts = await bot.api.get("/api/posts", status=["PUBLISHED"], account_id=v["acc"] or None, brand_id=v["brand"] or None, **{"from": since.isoformat()})
    q = v["q"].lower()
    posts = [p for p in posts if not q or any(q in (s or "").lower() for s in (p["caption"], p["render"]["clip_name"]))]
    posts.sort(key=post_time, reverse=True)
    items, nav = paged(posts, v)
    lines = [f"<b>Published</b> · {len(posts)} in the last {v['days']} days" + (f" matching “{h(v['q'])}”" if q else "")]
    for p in items:
        lines.append(await post_line(bot, p, f' · <a href="{h(p["permalink"])}">Instagram</a>' if p["permalink"] else ""))
    if not posts:
        lines.append("Nothing published in that range.")
    acc = next((f"@{a['username']}" for a in accs if a["id"] == v["acc"]), "all")
    brand = next((b["name"] for b in brands if b["id"] == v["brand"]), "all")
    days = RANGES[(RANGES.index(v["days"]) + 1) % len(RANGES)] if v["days"] in RANGES else 30
    rows = [[(f"{v['days']} days", f"f:days:{days}"), (f"Account: {acc}", "f:picking:acc"), (f"Brand: {fmt.cut(brand, 16)}", "f:picking:brand")]]
    return "\n".join(lines), rows + nav


@command("brands")
async def brands_cmd(bot, arg):
    await open_list(bot, "brands", archived=0)


@listing("brands")
async def brands_list(bot, v):
    brands = await bot.api.get("/api/brands") + (await bot.api.get("/api/brands", archived="true") if v["archived"] else [])
    items, nav = paged(brands, v)
    lines = [f"<b>Brands</b> · {len(brands)}" + (", archived included" if v["archived"] else "")]
    for b in items:
        bits = [f"/b{b['id']}", h(b["name"])] + (["default"] if b["is_default"] else []) + (["archived"] if b["archived_at"] else [])
        bits += (["auto-approve"] if b["auto_approve"] else []) + ([] if b["logo_url"] else ["<b>no logo</b>"])
        lines.append(" · ".join(bits))
    if not brands:
        lines.append("No brands yet. Create one with its logo to start rendering.")
    toggle = ("Hide archived", "f:archived:0") if v["archived"] else ("Show archived", "f:archived:1")
    return "\n".join(lines), nav + [[("New brand", "bn"), toggle]]


@command("captions")
async def captions_cmd(bot, arg):
    await open_list(bot, "captions")


@listing("captions")
async def captions_list(bot, v):
    caps = await bot.api.get("/api/captions")  # the default first, then by name
    items, nav = paged(caps, v)
    lines = [f"<b>Saved captions</b> · {len(caps)}"]
    for c in items:
        bits = [f"/t{c['id']}", h(c["name"])] + (["default"] if c["is_default"] else [])
        lines.append(" · ".join(bits + ([h(fmt.cut(" ".join(c["text"].split()), 50))] if c["text"].strip() else [])))
    if not caps:
        lines.append("None yet: save them in Customizations › Captions. The default one starts the caption of any brand without a template.")
    return "\n".join(lines), nav + web("/customizations/captions")


@command("covers")
async def covers_cmd(bot, arg):
    await open_list(bot, "covers")


@listing("covers")
async def covers_list(bot, v):
    covers = await bot.api.get("/api/covers")  # the default first, then the newest
    items, nav = paged(covers, v)
    lines = [f"<b>Saved covers</b> · {len(covers)}"] + [f"/i{c['id']} · {h(c['name'])}" + (" · default" if c["is_default"] else "") for c in items]
    if not covers:
        lines.append("None yet: upload them in Customizations › Covers. The default one becomes every new render's Reel cover.")
    return "\n".join(lines), nav + web("/customizations/covers")


@command("accounts")
async def accounts_cmd(bot, arg):
    await open_list(bot, "accounts")


@listing("accounts")
async def accounts_list(bot, v):
    accs = await bot.accounts(fresh=True)
    lines = [f"<b>Accounts</b> · {len(accs)}"]
    for a in accs:
        state = "disabled" if a["disabled_at"] else a["connection_status"]
        bits = [f"/a{a['id']}", f"@{h(a['username'])}", state if state == "connected" else f"<b>{state}</b>", a["timezone"],
                f"slots {fmt.slots_label(a['posting_slots']['times'])}", f"today {a['today_count']}/{a['daily_cap']}"]  # fmt: skip
        bits += [f"quota {a['quota']['used']}/{a['quota']['total']}"] if a["quota"] else []
        bits += [f"next {fmt.when(a['next_post_at'], a['timezone'], zone=False)}"] if a["next_post_at"] else []
        lines.append(" · ".join(bits))
    if not accs:
        lines.append("None yet. Connect an Instagram account in Zernio (one profile per account), then Sync accounts. "
                     "New accounts start on Europe/London with a slot every hour from 07:00 to 23:00.")  # fmt: skip
    return "\n".join(lines), [[("Sync accounts", "sync")]] + ([] if accs else [[("Open Zernio", ZERNIO_URL)]])


# ---------------------------------------------------------------- calendar and the ready tray


@command("calendar", "week")
async def calendar_cmd(bot, arg):
    accs = await bot.accounts()
    if not accs:
        return await bot.send("No accounts yet. Connect an Instagram account in Zernio, then /accounts and Sync accounts.")
    await open_list(bot, "calendar", acc=pick_account(accs, bot.last_account)["id"], week=0)


@listing("calendar")
async def calendar_list(bot, v):
    accs = await bot.accounts()
    if v["picking"] == "acc":
        return "Whose calendar?", [[(f"@{a['username']}", f"f:acc:{a['id']}")] for a in accs] + [[("Back", "f:picking:")]]
    a = pick_account(accs, v["acc"])
    tz, q, t_now = a["timezone"], a["quota"], now()
    today = fmt.local_day(t_now, tz)
    days = [today + timedelta(days=7 * v["week"] + i) for i in range(7)]
    start, end = fmt.day_bounds(days[0], tz)[0], fmt.day_bounds(days[-1], tz)[1]
    posts = await bot.api.get("/api/posts", account_id=a["id"], **{"from": start.isoformat(), "to": end.isoformat()})
    posts = [p for p in posts if p["status"] != "CANCELLED"]
    lines = [f"<b>@{h(a['username'])}</b> · {days[0]:%a} {days[0].day} {days[0]:%b} to {days[-1]:%a} {days[-1].day} {days[-1]:%b} · {tz}",
             f"Today {a['today_count']}/{a['daily_cap']} · min gap {a['min_gap_minutes']} min"
             + (f" · Zernio quota {q['used']}/{q['total']}" if q else "")]  # fmt: skip
    if a["disabled_at"] or a["connection_status"] != "connected":
        lines.append("<b>Disabled in Clipper.</b>" if a["disabled_at"] else "<b>Disconnected</b>: reconnect it in Zernio, then Sync accounts.")
    gap, times = timedelta(minutes=a["min_gap_minutes"]), a["posting_slots"]["times"]
    for d in days:
        mine = sorted((p for p in posts if fmt.local_day(post_time(p), tz) == d), key=post_time)
        day = [f"\n<b>{d:%a} {d.day} {d:%b}</b>" + (" · today" if d == today else "")]
        for p in mine:
            r = p["render"]
            clip = h(fmt.cut(fmt.short_url(r["clip_name"] or f"Clip {r['clip_id']}"), 28))
            day.append(f"{fmt.hm(post_time(p), tz)} {STATUS[p['status']]} · {h(r['brand_name'] or 'No logo')} · {clip} /p{p['id']}")
        # free as the web board counts it: ahead, not taken, not within the min gap of a post, day under its cap
        free = [fmt.hm(t, tz) for t in fmt.day_slots(times, d, tz)
                if t > t_now and not any(t == post_time(p) or abs(t - post_time(p)) < gap for p in posts)]  # fmt: skip
        if len(mine) >= a["daily_cap"]:
            day.append(f"<i>full: daily cap {a['daily_cap']}</i>")
        elif free:
            day.append(f"<i>free</i> {' '.join(free)}")
        elif not mine:
            day.append("no slots" if not times else "nothing left")
        if sum(map(len, lines + day)) > 3600:
            lines.append("\n…more on the web app's calendar.")
            break
        lines += day
    drafts = sum(p["status"] == "DRAFT" for p in posts)
    week = v["week"]
    rows = [[("◀", f"f:week:{week - 1}"), ("This week", "f:week:0"), ("▶", f"f:week:{week + 1}")]]
    rows += [[(f"Account: @{a['username']}", "f:picking:acc")]] if len(accs) > 1 else []
    rows += [[(f"Drafts ({drafts})", "ls:drafts"), ("Ready to schedule", "ls:ready")]] if drafts else [[("Ready to schedule", "ls:ready")]]
    return "\n".join(lines), rows


@command("ready", "queue")
async def ready_cmd(bot, arg):
    await open_list(bot, "ready", sel=[], acc=0)


@listing("ready")
async def ready_list(bot, v):
    accs = usable(await bot.accounts())
    if v["picking"] == "acc":
        return "Auto-schedule to which account?", [[(f"@{a['username']}", f"f:acc:{a['id']}")] for a in accs] + [[("Back", "f:picking:")]]
    rs = await bot.api.get("/api/renders", unscheduled="true", status="READY")
    clips, brands = await lookups(bot)
    v["ids"] = [r["id"] for r in rs]
    v["sel"] = [i for i in v["ids"] if i in v["sel"]]  # queue order, which auto-schedule keeps
    a = pick_account(accs, v["acc"] or bot.last_account)
    head = f"<b>Ready to schedule</b> · {fmt.plural(len(rs), 'render')}" + (f" · {len(v['sel'])} selected" if v["sel"] else "")
    if not rs:
        return head + "\nNothing ready. Finished renders land here: open a clip (/clips) and tap Render.", []
    items, nav = paged(rs, v)
    lines, rows = [head], []
    for r in items:
        c = clips.get(r["source_clip_id"])
        name, brand = fmt.clip_name(c) if c else f"Clip {r['source_clip_id']}", brand_name(brands, r["brand_id"])
        flags = " · <b>too long</b>" if (r["duration_s"] or 0) > settings.ZERNIO_MAX_REEL_SECONDS else ""
        lines.append(f"/r{r['id']} · {h(brand)} · {h(fmt.cut(name, 36))} · {fmt.mmss(r['duration_s'])}{flags}")
        rows.append([(("☑ " if r["id"] in v["sel"] else "☐ ") + f"#{r['id']} {fmt.cut(brand, 16)} · {fmt.cut(name, 22)}", f"q:t:{r['id']}")])
    rows += [[("Select all", "q:all"), ("Clear", "q:none")]] + nav
    if a is None:
        lines.append(f"\n{NO_ACCOUNT}")
    else:
        lines.append(f"\nAuto-schedule puts the selection, in this order, into @{h(a['username'])}'s next free slots (10 min from now or later).")
        rows += [[(f"Auto-schedule {len(v['sel'])} → @{a['username']}", "q:go")]] if v["sel"] else []
        rows += [[(f"Account: @{a['username']}", "f:picking:acc")]] if len(accs) > 1 else []
    return "\n".join(lines), rows


@button("q")
async def ready_op(bot, msg, op, arg=""):
    v = bot.view(msg, "list")
    if op == "t":
        rid = int(arg)
        v["sel"] = [i for i in v["sel"] if i != rid] if rid in v["sel"] else [*v["sel"], rid]
    elif op == "all":
        v["sel"] = list(v.get("ids", []))
    elif op == "none":
        v["sel"] = []
    elif op == "go":
        return await auto_schedule(bot, msg, v)
    await show_list(bot, msg, v)


async def auto_schedule(bot, msg, v):
    a = pick_account(usable(await bot.accounts()), v["acc"] or bot.last_account)
    if a is None or not v["sel"]:
        raise Alert("Select renders first.")
    out = await bot.api.post("/api/posts/auto-schedule", json={"render_ids": v["sel"], "account_id": a["id"]})
    bot.views.pop(msg["message_id"], None)
    tz, placed, unplaced = a["timezone"], out["placed"], out["unplaced"]
    lines = [f"<b>Placed {len(placed)} on @{h(a['username'])}</b>" + (f", {len(unplaced)} not placed" if unplaced else "")]
    lines += [f"#{x['render_id']} → {fmt.when(x['post']['scheduled_for'], tz, zone=False)} · {STATUS[x['post']['status']]} /p{x['post']['id']}" for x in placed]
    lines += [f"#{u['render_id']} not placed: {h(u['reason'])}" for u in unplaced]
    if drafts := sum(x["post"]["status"] == "DRAFT" for x in placed):
        lines.append(f"{fmt.plural(drafts, 'draft')} to approve: /drafts")
    await bot.edit(msg, "\n".join(lines), [[("Ready list", "ls:ready"), ("Calendar", f"kal:{a['id']}")]])
    return f"Placed {len(placed)}"


# ---------------------------------------------------------------- cards


async def open_card(bot, kind: str, id: int) -> None:
    try:
        text, rows, photo = await CARDS[kind](bot, id)
    except ApiError as e:
        if e.status != 404:
            raise
        return await bot.send(f"{NAMES[kind]} {id} not found.")
    await bot.send(text, rows, photo=photo)


async def refresh(bot, msg: dict, kind: str, id: int) -> None:
    text, rows, _ = await CARDS[kind](bot, id)
    await bot.edit(msg, text, rows)


for _kind in NAMES:  # c:12, r:34, p:56, b:4, a:1 open a card; re:<kind>:<id> redraws one in place
    CALLBACKS[_kind] = lambda bot, msg, id, _kind=_kind: open_card(bot, _kind, int(id))


@button("re")
async def refresh_button(bot, msg, kind, id):
    await refresh(bot, msg, kind, int(id))


@command("start", "help")
async def help_cmd(bot, arg):
    await bot.send(HELP)


@command("cancel")
async def cancel_cmd(bot, arg):
    await bot.send("OK: nothing is waiting for an answer now.")


@command("status")
async def status_cmd(bot, arg):
    try:
        st = await bot.api.get("/api/status")
    except ApiError as e:
        return await bot.send(f"<b>API offline.</b> {h(e.message)}")
    why = OFF_WHY.get(st.get("publishing_off"), "see Settings")
    label, hint = (
        ("Database offline", "Nothing renders or publishes until the database is back.") if not st["db"] else
        ("Worker offline", "Nothing renders or publishes until the workers are back.")
        if not (st["worker_alive"] or st["publisher_alive"]) else
        ("Publisher offline", "Nothing publishes and no alerts go out until the publisher is back. Renders run.")
        if not st["publisher_alive"] else
        ("Worker offline", "Nothing renders or downloads until the worker is back. Ready posts still publish.")
        if not st["worker_alive"] else
        ("Publishing off", f"Renders run, but {why}: scheduled posts stay scheduled and nothing reaches Instagram.")
        if not st["publishing_enabled"] else
        ("Publishing live", "Worker online. Scheduled posts go out at their time.")
    )  # fmt: skip
    text = (f"<b>{label}.</b> {hint}\n{st['rendering_renders']} rendering · {st['scheduled_posts']} scheduled · "
            f"{st['failed_posts']} failed")  # fmt: skip
    rows = [[(f"Failed posts ({st['failed_posts']})", "ls:failed")]] if st["failed_posts"] else []
    await bot.send(text, rows + [[("Calendar", "ls:calendar"), ("Ready", "ls:ready"), ("Drafts", "ls:drafts")]])


# clips


@card("c")
async def clip_card(bot, cid):
    c = await bot.api.get(f"/api/clips/{cid}")
    n = len(await bot.api.get("/api/renders", clip_id=cid))
    s, handle = c["status"], c["source_creator_handle"]
    lines = [f"<b>{h(fmt.cut(fmt.clip_name(c), 80))}</b> · clip {cid}"]
    if c["source_url"]:  # the name is already the URL: link the platform
        link = f'<a href="{h(c["source_url"])}">{h(c["platform"] or "Source")}</a>'
        lines.append(link + (f" · {h(handle)}" if handle else ""))
    elif handle:
        lines.append(h(handle))
    if s == "READY":
        fps = f"{round(c['fps'], 2):g}fps" if c["fps"] else "?fps"
        lines.append(f"{fmt.mmss(c['duration_s'])} · {c['width']}x{c['height']} · {fps} · "
                     f"{'audio' if c['has_audio'] else 'no audio'} · {fmt.mb(c['size_bytes'])} · {fmt.plural(n, 'render')}")  # fmt: skip
    elif s == "FAILED":
        lines.append(f"<b>Failed</b>: {h(fmt.CAUSES.get(c['error_code'], 'failed'))} <code>{h(c['error_code'] or '')}</code>")
        lines += [f"<i>{h(fmt.cut(c['error_detail'], 200))}</i>"] if c["error_detail"] else []
    else:
        lines.append(f"{s.capitalize()}…")
    rows = [[("Render…", f"ce:{cid}"), (f"Renders ({n})", f"crl:{cid}")]] if s == "READY" else []
    rows.append([("Creator", f"ch:{cid}")] + ([("Watch", f"cv:{cid}")] if s == "READY" else []))
    retryable = s == "FAILED" and c["error_code"] not in fmt.FINAL and (c["origin"] == "url" or c["raw_url"])
    last = ([("Retry", f"cy:{cid}")] if retryable else []) + ([("Remove", f"cd:{cid}")] if s in ("READY", "FAILED") and not n else [])
    rows += ([last] if last else []) + (web(f"/editor/{cid}") if s == "READY" else [])
    return "\n".join(lines), rows, c["thumbnail_url"]


@button("ch")
async def clip_handle_ask(bot, msg, cid):
    await bot.ask(f"Creator of clip {cid}? Send their @handle, or - to clear it.", "clip_handle", "@creator", clip=int(cid), card=msg)


@answer("clip_handle")
async def clip_handle(bot, m, p):
    t = optional(text_of(m))
    await bot.api.patch(f"/api/clips/{p['clip']}", {"source_creator_handle": t and "@" + t.lstrip("@")})
    await refresh(bot, p["card"], "c", p["clip"])
    await bot.send("Creator saved.", reply_to=p["card"]["message_id"])


@button("cy")
async def clip_retry(bot, msg, cid):
    await bot.api.post(f"/api/clips/{cid}/retry")
    bot.watch("clip", int(cid))
    await refresh(bot, msg, "c", int(cid))
    return "Retrying: its card follows when it's done"


@button("cd")
async def clip_remove_ask(bot, msg, cid):
    await bot.buttons(msg, [[("Remove the clip", f"cd!:{cid}"), ("Back", f"re:c:{cid}")]])
    return f"Remove clip {cid}? Its file is deleted too."


@button("cd!")
async def clip_remove(bot, msg, cid):
    await bot.api.delete(f"/api/clips/{cid}")
    await bot.edit(msg, f"Clip {cid} removed.")


@button("cv")
async def clip_watch(bot, msg, cid):
    c = await bot.api.get(f"/api/clips/{cid}")
    return send_video(bot, c["raw_url"], c["size_bytes"], f"Clip {cid} (source)", (c["width"], c["height"], c["duration_s"]))


@button("crl")
async def clip_renders(bot, msg, cid):
    await open_list(bot, "renders", clip=int(cid))


# renders


@card("r")
async def render_card(bot, rid):
    r = await bot.api.get(f"/api/renders/{rid}")
    c = await bot.api.get(f"/api/clips/{r['source_clip_id']}")
    s, long = r["status"], (r["duration_s"] or 0) > settings.ZERNIO_MAX_REEL_SECONDS
    brand = brand_name(await brands_by_id(bot), r["brand_id"])
    lines = [f"<b>Render #{rid}</b> · {h(brand)} · {fmt.placement(r)}" + (" · cover" if r["cover_url"] else ""),
             f"{h(fmt.cut(fmt.clip_name(c), 60))} /c{c['id']}"]  # fmt: skip
    if s == "READY":
        lines.append(f"Ready · {fmt.mmss(r['duration_s'])} · {fmt.mb(r['size_bytes'])} · 1080x1920")
        lines += [f"Longer than the {settings.ZERNIO_MAX_REEL_SECONDS // 60} min Reel limit: it can't be posted."] if long else []
    elif s == "FAILED":
        lines.append(f"<b>Failed</b>: {h(fmt.CAUSES.get(r['error_code'], 'render failed'))} <code>{h(r['error_code'] or '')}</code>")
    else:
        lines.append("Queued" if s == "PENDING" else f"Rendering, started {fmt.ago(r['updated_at'], now())}")
    lines += [f"<blockquote>{h(fmt.cut(r['caption'], 300))}</blockquote>"] if r["caption"] else []
    if s == "READY":
        rows = [[("Watch", f"rw:{rid}")] + ([] if long else [("Schedule…", f"rs:{rid}"), ("Post now", f"rn:{rid}")]), [("Delete", f"rd:{rid}")]]
    elif s == "FAILED":
        rows = [[("Log", f"rl:{rid}"), ("Retry", f"ry:{rid}"), ("Delete", f"rd:{rid}")]]
    else:
        rows = [[("Refresh", f"re:r:{rid}")] + ([("Delete", f"rd:{rid}")] if s == "PENDING" else [])]
    return "\n".join(lines), rows + web(f"/editor/{c['id']}"), r["thumbnail_url"] or c["thumbnail_url"]


@button("rw")
async def render_watch(bot, msg, rid):
    r = await bot.api.get(f"/api/renders/{rid}")
    return send_video(bot, r["output_url"], r["size_bytes"], f"Render #{rid}", (1080, 1920, r["duration_s"]))


@button("ry")
async def render_retry(bot, msg, rid):
    await bot.api.post(f"/api/renders/{rid}/retry")
    bot.watch("render", int(rid))
    await refresh(bot, msg, "r", int(rid))
    return "Queued again: its card follows when it's done"


@button("rl")
async def render_log(bot, msg, rid):
    r = await bot.api.get(f"/api/renders/{rid}")
    log = (r["ffmpeg_log"] or "No log.")[-3500:]
    await bot.send(f"<b>ffmpeg log</b> · render {rid} <code>{h(r['error_code'] or '')}</code>\n<pre>{h(log)}</pre>")


@button("rd")
async def render_delete_ask(bot, msg, rid):
    await bot.buttons(msg, [[(f"Delete render #{rid}", f"rd!:{rid}"), ("Back", f"re:r:{rid}")]])
    return f"Delete render {rid}? Its MP4 is deleted too."


@button("rd!")
async def render_delete(bot, msg, rid):
    await bot.api.delete(f"/api/renders/{rid}")
    await bot.edit(msg, f"Render #{rid} deleted.")


async def post_now(bot, rid: int, aid: int, caption: str | None, repost: bool = False) -> dict:
    """The post at this second, approved (Post now is the approval), watched until it goes out. repost: even if the
    video already went to the account (else ApiError ALREADY_POSTED)."""
    body = {"render_id": rid, "account_id": aid, "scheduled_for": now().replace(microsecond=0).isoformat(), "repost": repost}
    p = await bot.api.post("/api/posts", json=body | ({"caption": caption} if caption is not None else {}))
    if p["status"] == "DRAFT":
        p = await bot.api.post(f"/api/posts/{p['id']}/approve")
    bot.watch("post", p["id"], at=fmt.iso(p["scheduled_for"]))
    return p


@button("rn")
async def render_now_ask(bot, msg, rid):
    await publishing_on(bot)
    if not (accs := usable(await bot.accounts())):
        raise Alert(NO_ACCOUNT)
    rows = [[(f"Post to @{a['username']} now", f"rn!:{rid}:{a['id']}")] for a in accs]
    await bot.buttons(msg, rows + [[("Back", f"re:r:{rid}")]])
    return "It goes live on Instagram within about a minute and can't be deleted from here."


@button("rn!")
async def render_now(bot, msg, rid, aid, *_):  # *_: the rights flag older confirm buttons still carry
    return await render_now_as(bot, msg, int(rid), int(aid), repost=False)


@button("rr!")
async def render_repost_now(bot, msg, rid, aid):
    return await render_now_as(bot, msg, int(rid), int(aid), repost=True)


async def render_now_as(bot, msg, rid: int, aid: int, repost: bool) -> str:
    await publishing_on(bot)
    try:
        p = await post_now(bot, rid, aid, None, repost)
    except ApiError as e:
        if e.code != "ALREADY_POSTED":
            raise
        await bot.buttons(msg, [[("Post it again now", f"rr!:{rid}:{aid}"), ("Back", f"re:r:{rid}")]])
        return f"{e.message} Post it again?"
    await refresh(bot, msg, "r", rid)
    await bot.send(f"Posting render #{rid} on @{h(p['account_username'])} now: live within about a minute. /p{p['id']}")
    return "Posting now"


# posts


def post_rows(p: dict) -> list:
    pid, s = p["id"], p["status"]
    if s == "DRAFT":
        return [[("Approve", f"pa:{pid}"), ("Move…", f"pm:{pid}"), ("Caption", f"pe:{pid}")], [("Post now", f"pn:{pid}"), ("Cancel post", f"px:{pid}")]]
    if s == "SCHEDULED":
        return [[("Move…", f"pm:{pid}"), ("Caption", f"pe:{pid}")], [("Post now", f"pn:{pid}"), ("Cancel post", f"px:{pid}")]]
    if s == "PUBLISHING":
        return [[("Refresh", f"re:p:{pid}")]]
    if s == "PUBLISHED":
        link = [[("View on Instagram", p["permalink"])]] if (p["permalink"] or "").startswith("https://") else []
        return link + [[("Re-render for…", f"pr:{pid}")]]
    if s in FAILED:
        fix = None if p["error_code"] == "TOO_LONG" else p["remedy"]  # nothing Clipper can do: the clip is too long
        rows = []
        if fix and fix["action"] == "reconnect":
            rows = [[("Reconnect in Zernio", ZERNIO_URL)], [("I've reconnected: check now", f"pf:{pid}")]]
        elif fix and fix["action"] != "auto":
            rows = [[(fix["label"], f"pf:{pid}")]]
        return rows + [[("Details", f"pd:{pid}"), ("Dismiss", f"px:{pid}")]]
    return [[("Details", f"pd:{pid}")]]


@card("p")
async def post_card(bot, pid):
    p = await bot.api.get(f"/api/posts/{pid}")
    s, r, tz = p["status"], p["render"], await bot.tz(p["account_id"])
    clip = fmt.cut(fmt.short_url(r["clip_name"] or f"Clip {r['clip_id']}"), 50)
    lines = [f"<b>{HEADLINE[s]}</b> · post {pid}",
             f"@{h(p['account_username'])} · {fmt.when(post_time(p), tz)} ({fmt.rel(post_time(p), now())})",
             f"{h(r['brand_name'] or 'No logo')} · {h(clip)} · {fmt.mmss(r['duration_s'])} /r{r['id']}"]  # fmt: skip
    if s in FAILED:
        lines.append(f"<b>{h(TITLES.get(p['error_code'], p['error_code'] or 'Failed'))}.</b> {h(p['cause'] or '')}")
        said = (p["error_detail"] or {}).get("errorMessage")
        lines += [f"Zernio said: <i>{h(fmt.cut(said, 150))}</i>"] if isinstance(said, str) else []
        if (p["remedy"] or {}).get("action") == "auto":
            lines.append("Nothing to do: Clipper moves it to the next free slot on its own.")
    if s in ("DRAFT", "SCHEDULED") and not (await bot.api.get("/api/status"))["publishing_enabled"]:
        lines.append("Publishing is off: it won't go out until it is turned on.")
    lines += [f"<blockquote>{h(fmt.cut(p['caption'], 250))}</blockquote>"] if p["caption"] else []
    return "\n".join(lines), post_rows(p) + web("/settings" if p["error_code"] in KEY_CODES else f"/recover/{pid}"), r["thumbnail_url"]


@button("pa")
async def post_approve(bot, msg, pid):
    p = await bot.api.post(f"/api/posts/{pid}/approve")
    await refresh(bot, msg, "p", int(pid))
    return f"Approved: goes out {fmt.when(p['scheduled_for'], await bot.tz(p['account_id']))}"


def day_rows(a: dict, data) -> list:
    """The next 8 days in the account's zone."""
    today = fmt.local_day(now(), a["timezone"])
    days = [(fmt.day_label(d, today), data(d)) for d in (today + timedelta(days=i) for i in range(8))]
    return [days[:4], days[4:]]


async def slot_rows(bot, a: dict, d, data, exclude: int | None = None) -> list:
    """Day d's posting slots still ahead: free ones as buttons, taken ones marked."""
    tz = a["timezone"]
    start, end = fmt.day_bounds(d, tz)
    posts = await bot.api.get("/api/posts", account_id=a["id"], **{"from": start.isoformat(), "to": end.isoformat()})
    taken = {post_time(p) for p in posts if p["status"] != "CANCELLED" and p["id"] != exclude}
    slots = [((f"{fmt.hm(t, tz)} taken", "noop") if t in taken else (fmt.hm(t, tz), data(t)))
             for t in fmt.day_slots(a["posting_slots"]["times"], d, tz) if t > now()]  # fmt: skip
    width = 4 if len(slots) <= 16 else 5
    return [slots[i : i + width] for i in range(0, len(slots), width)]


async def warnings(bot, a: dict, at: datetime, exclude: int | None = None) -> str:
    """The calendar's heads-ups for a hand-picked time (the api only refuses the exact same instant)."""
    tz, gap = a["timezone"], timedelta(minutes=a["min_gap_minutes"])
    start, end = fmt.day_bounds(fmt.local_day(at, tz), tz)
    window = {"from": min(start, at - gap).isoformat(), "to": max(end, at + gap + timedelta(seconds=1)).isoformat()}
    times = [post_time(p) for p in await bot.api.get("/api/posts", account_id=a["id"], **window)
             if p["status"] != "CANCELLED" and p["id"] != exclude]  # fmt: skip
    out = []
    if near := [t for t in times if abs(t - at) < gap]:
        out.append(f"within {a['min_gap_minutes']} min of the {fmt.hm(near[0], tz)} post")
    if (n := sum(start <= t < end for t in times) + 1) > a["daily_cap"]:
        out.append(f"{n} posts that day, over the daily cap of {a['daily_cap']}")
    return "; ".join(out)


@button("pm")
async def post_move_days(bot, msg, pid):
    a = await account(bot, (await bot.api.get(f"/api/posts/{pid}"))["account_id"])
    await bot.buttons(msg, day_rows(a, lambda d: f"pmd:{pid}:{d:%Y%m%d}") + [[("Type a time…", f"pmx:{pid}"), ("Back", f"re:p:{pid}")]])
    return f"Pick a day ({a['timezone']})"


@button("pmd")
async def post_move_slots(bot, msg, pid, ymd):
    a = await account(bot, (await bot.api.get(f"/api/posts/{pid}"))["account_id"])
    d = date.fromisoformat(ymd)  # YYYYMMDD
    rows = await slot_rows(bot, a, d, lambda t: f"pmt:{pid}:{int(t.timestamp())}", exclude=int(pid))
    await bot.buttons(msg, rows + [[("Type a time…", f"pmx:{pid}"), ("Back", f"pm:{pid}")]])
    return None if rows else f"No posting slot left on {d:%a} {d.day}: type a time"


async def move_post(bot, card_msg: dict, pid: int, at: datetime) -> str:
    p = await bot.api.patch(f"/api/posts/{pid}", {"scheduled_for": at.isoformat()})
    a = await account(bot, p["account_id"])
    await refresh(bot, card_msg, "p", pid)
    warn = await warnings(bot, a, at, exclude=pid)
    return f"Moved to {fmt.when(at, a['timezone'])}" + (f". Heads-up: {warn}" if warn else "")


@button("pmt")
async def post_move(bot, msg, pid, epoch):
    return await move_post(bot, msg, int(pid), datetime.fromtimestamp(int(epoch), UTC))


@button("pmx")
async def post_move_type(bot, msg, pid):
    a = await account(bot, (await bot.api.get(f"/api/posts/{pid}"))["account_id"])
    await bot.ask(TIME_Q.format(u=h(a["username"]), tz=a["timezone"]), "post_time", "18:30", post=int(pid), card=msg, tz=a["timezone"])


@answer("post_time")
async def post_time_answer(bot, m, p):
    note = await move_post(bot, p["card"], p["post"], read_time(text_of(m), p["tz"]))
    await bot.send(f"{note}.", reply_to=p["card"]["message_id"])


@button("pe")
async def post_caption_ask(bot, msg, pid):
    await bot.ask(f"New caption for post {pid} (up to 2200 characters and 30 hashtags). Send - for none.", "post_caption", "Caption", post=int(pid), card=msg)


@answer("post_caption")
async def post_caption(bot, m, p):
    await bot.api.patch(f"/api/posts/{p['post']}", {"caption": caption_of(m)})
    await refresh(bot, p["card"], "p", p["post"])
    await bot.send("Caption saved.", reply_to=p["card"]["message_id"])


@button("pn")
async def post_now_ask(bot, msg, pid):
    await publishing_on(bot)
    p = await bot.api.get(f"/api/posts/{pid}")
    await bot.buttons(msg, [[(f"Post to @{p['account_username']} now", f"pn!:{pid}"), ("Back", f"re:p:{pid}")]])
    return "It goes live on Instagram within about a minute and can't be deleted from here."


@button("pn!")
async def post_now_go(bot, msg, pid):
    await publishing_on(bot)
    p = await bot.api.patch(f"/api/posts/{pid}", {"scheduled_for": now().replace(microsecond=0).isoformat()})
    if p["status"] == "DRAFT":
        p = await bot.api.post(f"/api/posts/{pid}/approve")  # Post now is the approval
    bot.watch("post", int(pid), at=fmt.iso(p["scheduled_for"]))
    await refresh(bot, msg, "p", int(pid))
    return "Posting now: live within about a minute"


@button("px")
async def post_cancel_ask(bot, msg, pid):
    failed = (await bot.api.get(f"/api/posts/{pid}"))["status"] in FAILED
    await bot.buttons(msg, [[("Dismiss the post" if failed else "Cancel the post", f"px!:{pid}"), ("Back", f"re:p:{pid}")]])
    return "Dismiss this post? It is cancelled and will not be published." if failed else f"Cancel post {pid}? It won't be published."


@button("px!")
async def post_cancel(bot, msg, pid):
    await bot.api.post(f"/api/posts/{pid}/cancel")
    await refresh(bot, msg, "p", int(pid))
    return "Cancelled"


@button("pf")
async def post_fix(bot, msg, pid):
    p = await bot.api.get(f"/api/posts/{pid}")
    if (p["remedy"] or {}).get("action") == "rerender" and p["error_code"] in MAYBE_LIVE:
        await bot.buttons(msg, [[("I checked: re-render", f"pf!:{pid}"), ("Back", f"re:p:{pid}")]])
        raise Alert(f"Check @{p['account_username']} on Instagram first. This Reel may already be live, and re-rendering "
                    "posts it again. Confirm only if you checked and it is not there.")  # fmt: skip
    return await remedy(bot, msg, int(pid))


@button("pf!")
async def post_fix_confirmed(bot, msg, pid):
    return await remedy(bot, msg, int(pid))


async def remedy(bot, msg: dict, pid: int) -> str:
    p = await bot.api.post(f"/api/posts/{pid}/remedy", json={})
    await bot.accounts(fresh=True)  # a reconnect syncs the accounts
    await refresh(bot, msg, "p", pid)
    if p["status"] in FAILED and p["error_code"] == "ACCOUNT_DISCONNECTED":
        raise Alert(f"@{p['account_username']} is still disconnected in Zernio.")
    if p["status"] in ("SCHEDULED", "PUBLISHING"):
        bot.watch("post", pid, at=fmt.iso(p["scheduled_for"]))
    return f"{STATUS[p['status']]}: {fmt.when(p['scheduled_for'], await bot.tz(p['account_id']))}"


@button("pd")
async def post_details(bot, msg, pid):
    p = await bot.api.get(f"/api/posts/{pid}")
    lines = [f"<b>Post {pid}</b> · {STATUS[p['status']]}" + (f" · <code>{h(p['error_code'])}</code>" if p["error_code"] else ""),
             f"Render #{p['render_id']} · clip {p['render']['clip_id']} · restarts {p['attempt_count']}/3"
             + (f" · Zernio <code>{h(p['zernio_post_id'])}</code>" if p["zernio_post_id"] else "")]  # fmt: skip
    if p["error_detail"]:
        lines.append(f"<pre>{h(fmt.cut(json.dumps(p['error_detail'], indent=1), 3000))}</pre>")
    await bot.send("\n".join(lines))


@button("pr")
async def post_rerender_pick(bot, msg, pid):
    p = await bot.api.get(f"/api/posts/{pid}")
    brands = [b for b in await bot.api.get("/api/brands") if b["logo_url"]]
    if not brands:
        raise Alert("No brand has a logo yet: /brands.")
    rows = [[(b["name"] + (" (original)" if b["id"] == p["render"]["brand_id"] else ""), f"pr!:{pid}:{b['id']}")] for b in brands]
    await bot.buttons(msg, rows + [[("Back", f"re:p:{pid}")]])
    return "Same clip and crop, with the brand's default logo placement and caption, and your default cover if you have one"


@button("pr!")
async def post_rerender(bot, msg, pid, bid):
    p = await bot.api.get(f"/api/posts/{pid}")
    orig = await bot.api.get(f"/api/renders/{p['render_id']}")
    clip = await bot.api.get(f"/api/clips/{orig['source_clip_id']}")
    if (b := (await brands_by_id(bot)).get(int(bid))) is None:
        raise Alert(f"Brand {bid} not found.")
    caps, covers = await bot.api.get("/api/captions"), await bot.api.get("/api/covers")
    text = b["caption_template"] or (default_of(caps) or {}).get("text")  # as the render editor's template()
    caption = fmt.fill_caption(text, b["link"], clip["source_creator_handle"]).strip() or None
    r = await bot.api.post("/api/renders", json={"clip_id": clip["id"], "brand_id": b["id"], "crop_config": orig["crop_config"], "caption": caption})
    bot.watch("render", r["id"])
    if cover := default_of(covers):
        bot.spawn(attach_cover(bot, r["id"], cover))
    await refresh(bot, msg, "p", int(pid))
    return f"Render #{r['id']} queued for {b['name']}: its card follows when it's done"


# accounts


@card("a")
async def account_card(bot, aid):
    try:
        a = await account(bot, aid, fresh=True)
    except Alert:
        raise ApiError(404, f"account {aid} not found") from None
    tz, q = a["timezone"], a["quota"]
    state = ("<b>Disabled in Clipper</b>: nothing is scheduled on it." if a["disabled_at"] else
             "<b>Disconnected</b>: reconnect it in Zernio, then Sync accounts." if a["connection_status"] != "connected" else
             f"Connected {fmt.ago(a['connected_at'], now())}")  # fmt: skip
    lines = [f"<b>@{h(a['username'])}</b> · account {aid}", state,
             f"{tz} ({datetime.now(ZoneInfo(tz)):%Z}) · slots {fmt.slots_label(a['posting_slots']['times'])}",
             f"Daily cap {a['daily_cap']} · min gap {a['min_gap_minutes']} min · today {a['today_count']}/{a['daily_cap']}",
             f"Zernio quota {q['used']}/{q['total']} per {q['duration_s'] // 3600} h" if q else "Zernio quota unknown",
             (f"Next post {fmt.when(a['next_post_at'], tz)}" if a["next_post_at"] else "Nothing queued") + " · "
             + (f"last published {fmt.ago(a['last_publish_at'], now())}" if a["last_publish_at"] else "nothing published yet")]  # fmt: skip
    toggle = ("Enable", f"ae:{aid}") if a["disabled_at"] else ("Disable", f"ax:{aid}")
    rows = [[("Slots", f"asp:{aid}"), ("Timezone", f"ak:{aid}:t")], [("Daily cap", f"ak:{aid}:c"), ("Min gap", f"ak:{aid}:g")],
            [("Calendar", f"kal:{aid}"), toggle]]  # fmt: skip
    rows += [[("Reconnect in Zernio", ZERNIO_URL)]] if a["connection_status"] != "connected" else []
    return "\n".join(lines), rows + [[("Sync accounts", f"sync:{aid}")]] + web("/accounts"), None


ACCOUNT_FIELDS = {
    "s": ("posting_slots", "Posting times for @{u}, every day in {tz}: send them like 09:00 13:00 19:00, or - for none. Now: {slots}"),
    "t": ("timezone", "Timezone for @{u}, as an IANA name like Europe/London or America/New_York. Now: {tz}"),
    "c": ("daily_cap", "Daily cap for @{u} (1-100 posts a day). Now: {cap}"),
    "g": ("min_gap_minutes", "Minimum gap between @{u}'s posts, in minutes (0-720). Now: {gap}"),
}


@button("ak")
async def account_ask(bot, msg, aid, f):
    a = await account(bot, int(aid))
    q = ACCOUNT_FIELDS[f][1].format(u=h(a["username"]), tz=a["timezone"], slots=fmt.slots_label(a["posting_slots"]["times"]),
                                    cap=a["daily_cap"], gap=a["min_gap_minutes"])  # fmt: skip
    await bot.ask(q, "account", "Reply here", account=int(aid), field=f, card=msg)


@answer("account")
async def account_answer(bot, m, p):
    field, t = ACCOUNT_FIELDS[p["field"]][0], text_of(m)
    if field == "posting_slots":
        times = [] if t == "-" else re.findall(r"\d{1,2}:\d{2}", t)
        if t != "-" and not times:
            raise Alert("Send the times like 09:00 13:00 19:00.")
        value = {"times": times}
    elif field == "timezone":
        value = t
    elif t.isdigit():
        value = int(t)
    else:
        raise Alert("Send a whole number.")
    await bot.api.patch(f"/api/accounts/{p['account']}", {field: value})
    await bot.accounts(fresh=True)
    await refresh(bot, p["card"], "a", p["account"])
    await bot.send("Saved.", reply_to=p["card"]["message_id"])


@button("asp")
async def account_slots_pick(bot, msg, aid):
    rows = [[(label, f"as:{aid}:{key}")] for key, (label, _) in fmt.SLOT_PRESETS.items()]
    await bot.buttons(msg, rows + [[("Type times…", f"ak:{aid}:s"), ("Back", f"re:a:{aid}")]])
    return "Pick a set of posting times, or type your own"


@button("as")
async def account_slots_preset(bot, msg, aid, key):
    label, times = fmt.SLOT_PRESETS[key]
    await bot.api.patch(f"/api/accounts/{aid}", {"posting_slots": {"times": times}})
    await bot.accounts(fresh=True)
    await refresh(bot, msg, "a", int(aid))
    return f"Posting times: {label}"


@button("ax")
async def account_disable_ask(bot, msg, aid):
    a = await account(bot, int(aid))
    await bot.buttons(msg, [[(f"Disable @{a['username']}", f"ax!:{aid}"), ("Back", f"re:a:{aid}")]])
    return f"Disable @{a['username']}? Its drafts and scheduled posts are cancelled."


@button("ax!")
async def account_disable(bot, msg, aid):
    await bot.api.patch(f"/api/accounts/{aid}", {"disabled": True})
    await refresh(bot, msg, "a", int(aid))
    return "Disabled: its drafts and scheduled posts are cancelled"


@button("ae")
async def account_enable(bot, msg, aid):
    await bot.api.patch(f"/api/accounts/{aid}", {"disabled": False})
    await refresh(bot, msg, "a", int(aid))
    return "Enabled"


@button("sync")
async def sync(bot, msg, aid=None):
    accs = await bot.api.post("/api/accounts/sync")
    bot._accounts = (time.monotonic(), accs)
    if aid:
        await refresh(bot, msg, "a", int(aid))
    elif (v := bot.views.get(msg["message_id"])) and v.get("list") == "accounts":
        await show_list(bot, msg, v)
    return ("Synced: " + ", ".join(f"@{a['username']} {a['connection_status']}" for a in accs)) if accs else "Synced: no Instagram accounts in Zernio yet"


@button("kal")
async def account_calendar(bot, msg, aid):
    bot.last_account = int(aid)
    await calendar_cmd(bot, "")


# brands


@card("b")
async def brand_card(bot, bid):
    if (b := (await brands_by_id(bot)).get(bid)) is None:
        raise ApiError(404, f"brand {bid} not found")
    o = b["default_overlay_config"]
    lines = [f"<b>{h(b['name'])}</b> · brand {bid}" + (" · default" if b["is_default"] else "") + (" · <b>archived</b>" if b["archived_at"] else ""),
             "Logo: set" if b["logo_url"] else "Logo: <b>none yet</b>. Tap Logo, then send a PNG with transparency as a file.",
             f"Default placement: {fmt.bucket(o)} · {round(o['w'] * 100)}% · opacity {round(o.get('opacity', 1) * 100)}%",
             "Auto-approve: " + ("on, its posts are scheduled straight away" if b["auto_approve"] else "off, its posts start as drafts"),
             f"Link: {h(b['link']) if b['link'] else 'none'}",
             "Caption template:" + (f"\n<blockquote>{h(fmt.cut(b['caption_template'], 400))}</blockquote>" if b["caption_template"] else " none")]  # fmt: skip
    rows = [[("Name", f"bk:{bid}:n"), ("Template", f"bk:{bid}:t"), ("Link", f"bk:{bid}:l")],
            [("Logo", f"bk:{bid}:g"), ("Placement", f"bo:{bid}"), (f"Auto-approve {'on' if b['auto_approve'] else 'off'}", f"ba:{bid}")],
            ([("View logo", f"bv:{bid}")] if b["logo_url"] else []) + [("Unarchive" if b["archived_at"] else "Archive", f"bz:{bid}")]]  # fmt: skip
    return "\n".join(lines), rows + web("/brands"), None


@button("bv")
async def brand_logo(bot, msg, bid):
    """The logo PNG as a file: a photo would lose its transparency."""
    if not (b := (await brands_by_id(bot)).get(int(bid))) or not b["logo_url"]:
        raise Alert("No logo yet: tap Logo, then send a PNG with transparency as a file.")

    async def go():
        async with FILES:
            data = await bot.api.media(b["logo_url"])
            await bot.tg("sendDocument", files={"document": (f"logo-{bid}.png", data, "image/png")}, chat_id=bot.chat,
                         caption=f"{h(b['name'])}: logo", parse_mode="HTML")  # fmt: skip

    bot.spawn(go())
    return "Sending the logo…"


BRAND_FIELDS = {
    "n": ("name", "New name for {name}?"),
    "t": ("caption_template", "Caption template for {name}. {{link}} becomes the brand's link and {{creator}} the clip's creator handle. Send - for none."),
    "l": ("link", "Link for {name} (what {{link}} becomes in captions). Send - for none."),
    "g": ("logo", "Send {name}'s logo: a PNG with transparency, as a file (paperclip, then File). Sent as a photo it loses its transparency."),
}


async def ask_brand(bot, bid: int, f: str, name: str, card_msg: dict | None = None) -> None:
    await bot.ask(BRAND_FIELDS[f][1].format(name=h(name)), "brand", "PNG file" if f == "g" else "Reply here", brand=bid, field=f, card=card_msg)


@button("bk")
async def brand_ask(bot, msg, bid, f):
    if (b := (await brands_by_id(bot)).get(int(bid))) is None:
        raise Alert(f"Brand {bid} not found.")
    await ask_brand(bot, int(bid), f, b["name"], msg)


@answer("brand")
async def brand_answer(bot, m, p):
    field, bid = BRAND_FIELDS[p["field"]][0], p["brand"]
    if field == "logo":
        d = m.get("document")
        if not d:
            raise Alert("That came as a photo: Telegram turns photos into JPEGs without transparency. Send the PNG as a file."
                        if m.get("photo") else "Send the PNG as a file (paperclip, then File).")  # fmt: skip
        if (d.get("file_size") or 0) > LOGO_MAX:
            raise Alert("The logo must be under 10 MB.")
        async with FILES:
            data = await bot.tg.download(d["file_id"])
            await bot.api.post(f"/api/brands/{bid}/logo", files={"file": (d.get("file_name") or "logo.png", data, "image/png")})
    else:
        if not (t := text_of(m)):
            raise Alert("Send it as text.")
        await bot.api.patch(f"/api/brands/{bid}", {field: t if field == "name" else optional(t)})
    if p.get("card"):
        await refresh(bot, p["card"], "b", bid)
        await bot.send("Saved.", reply_to=p["card"]["message_id"])
    else:
        await open_card(bot, "b", bid)


@button("bn")
async def brand_new_ask(bot, msg):
    await bot.ask("Name of the new brand?", "brand_new", "Brand name")


@answer("brand_new")
async def brand_new(bot, m, p):
    if not (name := text_of(m)):
        raise Alert("Send the name as text.")
    b = await bot.api.post("/api/brands", json={"name": name})
    await ask_brand(bot, b["id"], "g", b["name"])  # a brand renders only once it has a logo


@button("ba")
async def brand_auto_approve(bot, msg, bid):
    b = (await brands_by_id(bot))[int(bid)]
    await bot.api.patch(f"/api/brands/{bid}", {"auto_approve": not b["auto_approve"]})
    await refresh(bot, msg, "b", int(bid))
    return "Auto-approve off: its posts start as drafts" if b["auto_approve"] else "Auto-approve on: its posts are scheduled straight away"


@button("bz")
async def brand_archive(bot, msg, bid):
    b = (await brands_by_id(bot))[int(bid)]
    await bot.api.patch(f"/api/brands/{bid}", {"archived": not b["archived_at"]})
    await refresh(bot, msg, "b", int(bid))
    return "Unarchived" if b["archived_at"] else "Archived: it keeps its renders and posts, but can't be picked for new renders"


# saved captions and covers (Customizations: made and set as default in the web app)


@card("t")
async def caption_card(bot, tid):
    if (c := next((x for x in await bot.api.get("/api/captions") if x["id"] == tid), None)) is None:
        raise ApiError(404, f"caption {tid} not found")
    lines = [f"<b>{h(c['name'])}</b> · saved caption {tid}" + (" · default" if c["is_default"] else ""),
             "The default: it starts the caption of any brand without a template." if c["is_default"] else
             "Pick it in the render editor: Saved captions.",
             f"<blockquote>{h(c['text'])}</blockquote>" if c["text"].strip() else "No text."]  # fmt: skip
    return "\n".join(lines), web("/customizations/captions"), None


@card("i")
async def cover_card(bot, iid):
    if (c := next((x for x in await bot.api.get("/api/covers") if x["id"] == iid), None)) is None:
        raise ApiError(404, f"cover {iid} not found")
    text = (f"<b>{h(c['name'])}</b> · saved cover {iid}" + (" · default" if c["is_default"] else "") + "\n"
            + ("The default: every new render gets a copy as its Reel cover." if c["is_default"] else "Pick it in the render editor: Cover."))  # fmt: skip
    return text, web("/customizations/covers"), c["image_url"]


# ---------------------------------------------------------------- render editor and placement editor


async def aspect_of(bot, b: dict) -> float:
    """The logo box's h/w, from the PNG's header; a square until it can be read (as the web editor before
    the logo loads)."""
    url = b["logo_url"]
    if url not in bot.logos:
        try:
            bot.logos[url] = fmt.logo_aspect(fmt.png_size(await bot.api.media(url)))
        except ApiError:
            return fmt.logo_aspect(None)
    return bot.logos[url]


def logo_rows(v: dict) -> list:
    """The 3x3 snap grid, then size and opacity."""
    o = v["overlay"]
    grid = [[("●" if v["cell"] == i else fmt.ARROWS[i], f"e:g:{i}") for i in range(row, row + 3)] for row in (0, 3, 6)]
    return grid + [[("−", "e:w:-"), (f"{round(o['w'] * 100)}%", "noop"), ("+", "e:w:+"), (f"Opacity {round(o['opacity'] * 100)}%", "e:o")]]


def logo_line(v: dict) -> str:
    o = v["overlay"]
    where = fmt.SNAPS[v["cell"]] if v["cell"] is not None else fmt.bucket(o)
    return f"{where} · {round(o['w'] * 100)}% of the width · opacity {round(o['opacity'] * 100)}%"


def editor_brand(v: dict) -> dict | None:
    return v["brands"].get(v["brand"]) if v["kind"] == "editor" else v["brand"]


def edited(v: dict) -> bool:
    d = (editor_brand(v) or {}).get("default_overlay_config")
    return bool(d) and any(abs(d.get(k, 1) - v["overlay"].get(k, 1)) > 1e-4 for k in ("x", "y", "w", "opacity"))


def template(v: dict, brand_id: int | None) -> str:
    """The brand's caption template, else the default saved caption, filled in (Editor.tsx template)."""
    b = v["brands"].get(brand_id) or {}
    text = b.get("caption_template") or (default_of(v["captions"]) or {}).get("text")
    return fmt.fill_caption(text, b.get("link"), v["clip"]["source_creator_handle"])


def editor_view(v: dict) -> tuple[str, list]:
    c, cap, b = v["clip"], v["caption"], editor_brand(v)
    tags = fmt.hashtags(cap)
    lines = [f"<b>Render</b> · clip {c['id']} · {h(fmt.cut(fmt.clip_name(c), 60))}",
             f"{fmt.mmss(c['duration_s'])} · {c['width']}x{c['height']} · {'audio' if c['has_audio'] else 'no audio'}", ""]  # fmt: skip
    if not v["chosen"]:
        lines.append("Brand: <b>pick one below</b>, or No logo.")
    else:
        lines.append(f"Brand: <b>{h(b['name']) if b else 'No logo'}</b>")
        lines += [f"Logo: {logo_line(v)}" + ("" if edited(v) else " (brand default)")] if b else []
        lines.append(f"Crop: {v['crop']}" + (", the source fills the 9:16 frame" if v["crop"] == "centre" else " 9:16 window of the source"))
        if v["covers"]:
            x = v["cover"]
            lines.append("Cover: " + (h(x["name"]) + (" (default)" if x["is_default"] else "") if x else "none, Instagram picks a frame"))
        lines.append(f"Caption: {len(cap)}/{fmt.CAPTION_MAX} characters · {tags}/{fmt.HASHTAG_MAX} hashtags"
                     + (" · <b>too long for Instagram</b>" if len(cap) > fmt.CAPTION_MAX or tags > fmt.HASHTAG_MAX else ""))  # fmt: skip
        lines += [f"<blockquote>{h(fmt.cut(cap, 350))}</blockquote>"] if cap else []
    if (c["duration_s"] or 0) > settings.ZERNIO_MAX_REEL_SECONDS:
        lines.append(f"The clip is {fmt.mmss(c['duration_s'])}: Reels can be at most {settings.ZERNIO_MAX_REEL_SECONDS // 60} min. It still renders.")
    if v.get("last"):
        lines.append(f"Queued render #{v['last']}: its card follows when it's done.")
    if v["picking"] == "caption":  # an action, not a state: the pick replaces the caption (Editor.tsx)
        rows = [[(x["name"] + (" (default)" if x["is_default"] else ""), f"e:sc:{x['id']}")] for x in v["captions"]]
        return "\n".join(lines), rows + [[("Back", "e:bk")]]
    if v["picking"] == "cover":
        mine = (v["cover"] or {}).get("id")
        rows = [[(("● " if x["id"] == mine else "") + x["name"] + (" (default)" if x["is_default"] else ""), f"e:v:{x['id']}")]
                for x in v["covers"]]  # fmt: skip
        return "\n".join(lines), rows + [[(("● " if mine is None else "") + "None: Instagram picks", "e:v:0"), ("Back", "e:bk")]]
    if v["picking"] or not v["chosen"]:
        names = [(("● " if v["chosen"] and bid == v["brand"] else "") + x["name"], f"e:b:{bid}") for bid, x in v["brands"].items()]
        rows = [names[i : i + 2] for i in range(0, len(names), 2)]
        return "\n".join(lines), rows + [[("No logo", "e:b:0")] + ([("Back", "e:bk")] if v["chosen"] else [])]
    rows = [[(f"Brand: {b['name'] if b else 'No logo'}", "e:bp")]] + (logo_rows(v) if b else [])
    crop = [(f"Crop: {v['crop']}", "e:c")] if len(fmt.crop_options(c["width"], c["height"])) > 1 else []
    rows.append(crop + [("Caption", "e:t")] + ([("Template", "e:tr")] if cap != template(v, v["brand"]) else []))
    saved = [("Saved captions", "e:tp")] if v["captions"] else []
    saved += [(f"Cover: {fmt.cut(v['cover']['name'], 24) if v['cover'] else 'none'}", "e:vp")] if v["covers"] else []
    rows += [saved] if saved else []
    rows.append([("Render", "e:go")] + ([("Save as default", "e:s")] if b and edited(v) else []) + [("Close", "e:x")])
    return "\n".join(lines), rows


def overlay_view(v: dict) -> tuple[str, list]:
    text = (f"<b>Default placement</b> · {h(v['brand']['name'])}\nLogo: {logo_line(v)}\n"
            "New renders with this brand start here; the render editor can still move it.")  # fmt: skip
    return text, logo_rows(v) + [[("Save", "e:s"), ("Close", "e:x")]]


async def set_brand(bot, v: dict, brand_id: int | None) -> None:
    old = template(v, v["brand"]) if v["chosen"] else None
    b = v["brands"].get(brand_id)
    v.update(brand=brand_id if b else None, chosen=True, picking=False, cell=None,
             overlay=dict(b["default_overlay_config"]) if b else dict(fmt.DEFAULT_OVERLAY),
             aspect=await aspect_of(bot, b) if b else 1.0)  # fmt: skip
    v["overlay"].setdefault("opacity", 1)
    if old is None or v["caption"] == old:  # keep the operator's own caption edits
        v["caption"] = template(v, v["brand"])


@button("ce")
async def editor_open(bot, msg, cid):
    await open_editor(bot, int(cid))


async def open_editor(bot, cid: int) -> None:
    c = await bot.api.get(f"/api/clips/{cid}")
    if c["status"] != "READY" or not c["width"]:
        raise Alert(f"Clip {cid} is {c['status'].lower()}: the editor opens once it is ready.")
    brands = {b["id"]: b for b in await bot.api.get("/api/brands") if b["logo_url"]}
    # the brand this clip was last rendered with (newest first), else the default brand, else the user picks (Editor.tsx)
    first = next((r["brand_id"] for r in await bot.api.get("/api/renders", clip_id=cid) if r["brand_id"] in brands), None)
    first = first or (default_of(brands.values()) or {}).get("id")
    covers = await bot.api.get("/api/covers")
    v = {"kind": "editor", "clip": c, "brands": brands, "brand": None, "chosen": False, "picking": False, "cell": None,
         "overlay": dict(fmt.DEFAULT_OVERLAY), "aspect": 1.0, "crop": "centre", "caption": "",
         "captions": await bot.api.get("/api/captions"), "covers": covers, "cover": default_of(covers)}  # fmt: skip
    if first:
        await set_brand(bot, v, first)
    text, rows = editor_view(v)
    bot.keep(await bot.send(text, rows, photo=c["thumbnail_url"]), v)


@button("e")
async def editor_op(bot, msg, op, arg=""):
    v = bot.view(msg, "editor", "overlay")
    o, toast = v["overlay"], None
    if op in ("bp", "tp", "vp"):
        v["picking"] = {"bp": "brand", "tp": "caption", "vp": "cover"}[op]
    elif op == "bk":
        v["picking"] = False
    elif op == "b":
        await set_brand(bot, v, int(arg) or None)
    elif op == "sc":  # a saved caption, filled like the template
        text = next(x["text"] for x in v["captions"] if x["id"] == int(arg))
        v["caption"] = fmt.fill_caption(text, (editor_brand(v) or {}).get("link"), v["clip"]["source_creator_handle"])
        v["picking"] = False
    elif op == "v":
        v["cover"], v["picking"] = next((x for x in v["covers"] if x["id"] == int(arg)), None), False
    elif op == "g":
        v["cell"] = int(arg)
        v["overlay"] = fmt.place(o, v["cell"], v["aspect"])
    elif op == "w":
        v["overlay"] = fmt.place({**o, "w": o["w"] + (fmt.W_STEP if arg == "+" else -fmt.W_STEP)}, v["cell"], v["aspect"])
    elif op == "o":
        v["overlay"] = {**o, "opacity": next((x for x in fmt.OPACITIES if x < o["opacity"] - 1e-3), fmt.OPACITIES[0])}
    elif op == "c":
        options = fmt.crop_options(v["clip"]["width"], v["clip"]["height"])
        v["crop"] = options[(options.index(v["crop"]) + 1) % len(options)]
    elif op == "t":
        return await bot.ask("Send the caption for this render (up to 2200 characters and 30 hashtags). Send - for none.",
                             "editor_caption", "Caption", view=msg["message_id"])  # fmt: skip
    elif op == "tr":
        v["caption"] = template(v, v["brand"])
    elif op == "s":
        return await save_default(bot, msg, v)
    elif op == "go":
        toast = await render_go(bot, v)
    elif op == "x":
        bot.views.pop(msg["message_id"], None)
        return await bot.edit(msg, "Editor closed." if v["kind"] == "editor" else "Closed.")
    await bot.edit(msg, *(editor_view(v) if v["kind"] == "editor" else overlay_view(v)))
    return toast


async def save_default(bot, msg: dict, v: dict) -> str:
    if (b := editor_brand(v)) is None:
        raise Alert("Pick a brand first.")
    saved = await bot.api.patch(f"/api/brands/{b['id']}", {"default_overlay_config": v["overlay"]})
    if v["kind"] == "overlay":
        bot.views.pop(msg["message_id"], None)
        await bot.edit(msg, f"Default placement for {h(b['name'])} saved: {logo_line(v)}.")
    else:
        v["brands"][b["id"]] = saved
        await bot.edit(msg, *editor_view(v))
    return f"Saved as {b['name']}'s default"


async def render_go(bot, v: dict) -> str:
    if time.monotonic() - v.get("went", 0) < 3:  # a double tap is one render
        return "Already queued"
    if not v["chosen"]:
        raise Alert("Pick a brand, or No logo, first.")
    cap, c = v["caption"].strip(), v["clip"]
    if len(cap) > fmt.CAPTION_MAX or fmt.hashtags(cap) > fmt.HASHTAG_MAX:
        raise Alert("Instagram refuses captions over 2200 characters or 30 hashtags: shorten it first.")
    body = {"clip_id": c["id"], "brand_id": v["brand"], "overlay_config": v["overlay"] if v["brand"] else None,
            "crop_config": fmt.crop_box(c["width"], c["height"], v["crop"]), "caption": cap or None}  # fmt: skip
    r = await bot.api.post("/api/renders", json=body)
    v["went"], v["last"] = time.monotonic(), r["id"]
    bot.watch("render", r["id"])
    if v["cover"]:
        bot.spawn(attach_cover(bot, r["id"], v["cover"]))
    return f"Render #{r['id']} queued"


async def attach_cover(bot, rid: int, cover: dict) -> None:
    """A copy of a saved cover becomes the render's Reel cover (Editor.tsx: savedCover, then setRenderCover)."""
    try:
        async with FILES:
            data = await bot.api.media(cover["image_url"])
            await bot.api("PUT", f"/api/renders/{rid}/cover", files={"file": ("cover.jpg", data, "image/jpeg")})
    except ApiError as e:
        await bot.send(f"Render #{rid} queued without its cover: {h(e.message)}")


@answer("editor_caption")
async def editor_caption(bot, m, p):
    if (v := bot.views.get(p["view"])) is None or v["kind"] != "editor":
        return await bot.send("That editor has expired: open the clip again.")
    v["caption"] = caption_of(m)
    await bot.edit(v["msg"], *editor_view(v))
    await bot.send("Caption set. Tap Render in the editor.", reply_to=p["view"])


@button("bo")
async def brand_placement(bot, msg, bid):
    if (b := (await brands_by_id(bot)).get(int(bid))) is None:
        raise Alert(f"Brand {bid} not found.")
    if not b["logo_url"]:
        raise Alert("Upload a logo first: its shape decides how the placement fits.")
    v = {"kind": "overlay", "brand": b, "overlay": {"opacity": 1, **b["default_overlay_config"]}, "cell": None, "aspect": await aspect_of(bot, b)}
    text, rows = overlay_view(v)
    bot.keep(await bot.send(text, rows), v)


# ---------------------------------------------------------------- schedule form


async def suggest(bot, v: dict) -> None:
    at = (await bot.api.get(f"/api/accounts/{v['acc']['id']}/next-slot"))["scheduled_for"]
    v["at"], v["suggested"] = at and fmt.iso(at), True


@button("rs")
async def schedule_open(bot, msg, rid):
    r = await bot.api.get(f"/api/renders/{rid}")
    if r["status"] != "READY":
        raise Alert(f"Render #{rid} is {r['status'].lower()}, not ready.")
    if not (accs := usable(await bot.accounts())):
        raise Alert(NO_ACCOUNT)
    clip = await bot.api.get(f"/api/clips/{r['source_clip_id']}")
    v = {"kind": "schedule", "render": r, "clip": clip, "brand": brand_name(await brands_by_id(bot), r["brand_id"]), "accs": accs,
         "acc": pick_account(accs, bot.last_account), "caption": r["caption"] or "", "step": "form", "day": None}  # fmt: skip
    await suggest(bot, v)
    text, rows = await schedule_view(bot, v)
    bot.keep(await bot.send(text, rows, photo=r["thumbnail_url"]), v)


async def schedule_view(bot, v: dict) -> tuple[str, list]:
    r, a, step = v["render"], v["acc"], v["step"]
    tz = a["timezone"]
    lines = [f"<b>Schedule render #{r['id']}</b> · {h(v['brand'])} · {h(fmt.cut(fmt.clip_name(v['clip']), 40))} · {fmt.mmss(r['duration_s'])}",
             f"Account: @{h(a['username'])} ({tz})"]  # fmt: skip
    if v["at"]:
        lines.append(f"When: {fmt.when(v['at'], tz)} ({fmt.rel(v['at'], now())})" + (", the next free slot" if v["suggested"] else ""))
        if not v["suggested"] and (warn := await warnings(bot, a, v["at"])):
            lines.append(f"Heads-up: {warn}.")
    else:
        lines.append("When: no free slot in the next 30 days. Pick a time.")
    lines.append("Caption:" + (f"\n<blockquote>{h(fmt.cut(v['caption'], 300))}</blockquote>" if v["caption"] else " none"))
    if not (await bot.api.get("/api/status"))["publishing_enabled"]:
        lines.append("Publishing is off: nothing reaches Instagram until it is turned on.")
    if step == "accounts":
        rows = [[(f"@{x['username']}", f"s:a:{x['id']}")] for x in v["accs"]] + [[("Back", "s:bk")]]
    elif step == "days":
        rows = day_rows(a, lambda d: f"s:dd:{d:%Y%m%d}") + [[("Type a time…", "s:tt"), ("Back", "s:bk")]]
    elif step == "slots":
        rows = await slot_rows(bot, a, v["day"], lambda t: f"s:t:{int(t.timestamp())}")
        if not rows:
            lines.append(f"No posting slot left on {v['day']:%a} {v['day'].day}: type a time.")
        rows += [[("Type a time…", "s:tt"), ("Back", "s:d")]]
    elif step == "now":
        lines.append(f"<b>Post to @{h(a['username'])} now?</b> It goes live on Instagram within about a minute and can't be deleted from here.")
        rows = [[("Post now", "s:now!"), ("Back", "s:bk")]]
    elif step == "repost":  # the api's ALREADY_POSTED: the same video went (or is queued) there
        lines.append(f"<b>{h(v['twice'])}</b> {'Post' if v['again_now'] else 'Schedule'} it again?")
        rows = [[("Post it again now" if v["again_now"] else "Schedule it again", "s:rp"), ("Back", "s:bk")]]
    else:
        rows = [[(f"Account: @{a['username']}", "s:a")]] if len(v["accs"]) > 1 else []
        rows += [[(f"Schedule for {fmt.when(v['at'], tz, zone=False)}", "s:go")]] if v["at"] else []
        rows += [[("Other time…", "s:d"), ("Post now", "s:now")], [("Caption", "s:c"), ("Close", "s:x")]]
    return "\n".join(lines), rows


@button("s")
async def schedule_op(bot, msg, op, arg=""):
    v = bot.view(msg, "schedule")
    if op == "a" and arg:
        v["acc"] = next(x for x in v["accs"] if x["id"] == int(arg))
        bot.last_account, v["step"] = v["acc"]["id"], "form"
        await suggest(bot, v)
    elif op == "a":
        v["step"] = "accounts"
    elif op == "d":
        v["step"] = "days"
    elif op == "dd":
        v["day"], v["step"] = date.fromisoformat(arg), "slots"
    elif op == "t":
        v["at"], v["suggested"], v["step"] = datetime.fromtimestamp(int(arg), UTC), False, "form"
    elif op == "tt":
        a = v["acc"]
        return await bot.ask(TIME_Q.format(u=h(a["username"]), tz=a["timezone"]), "schedule_time", "18:30",
                             view=msg["message_id"], day=v["day"] if v["step"] == "slots" else None)  # fmt: skip
    elif op == "c":
        return await bot.ask("Caption for this post (up to 2200 characters and 30 hashtags). Send - for none.",
                             "schedule_caption", "Caption", view=msg["message_id"])  # fmt: skip
    elif op == "bk":
        v["step"] = "form"
    elif op == "x":
        bot.views.pop(msg["message_id"], None)
        return await bot.edit(msg, "Closed.")
    elif op == "go":
        return await schedule_submit(bot, msg, v, now_=False)
    elif op == "now":
        await publishing_on(bot)
        v["step"] = "now"
    elif op == "now!":
        return await schedule_submit(bot, msg, v, now_=True)
    elif op == "rp":
        return await schedule_submit(bot, msg, v, now_=v["again_now"], repost=True)
    await bot.edit(msg, *await schedule_view(bot, v))


async def schedule_submit(bot, msg: dict, v: dict, now_: bool, repost: bool = False) -> str | None:
    a, r = v["acc"], v["render"]
    try:
        if now_:
            await publishing_on(bot)
            p = await post_now(bot, r["id"], a["id"], v["caption"], repost)
        elif not v["at"]:
            raise Alert("Pick a time first.")
        else:
            body = {"render_id": r["id"], "account_id": a["id"], "scheduled_for": v["at"].isoformat(), "caption": v["caption"],
                    "repost": repost}  # fmt: skip
            p = await bot.api.post("/api/posts", json=body)
    except ApiError as e:
        if e.code != "ALREADY_POSTED":
            raise
        v["step"], v["twice"], v["again_now"] = "repost", e.message, now_
        await bot.edit(msg, *await schedule_view(bot, v))
        return None
    bot.views.pop(msg["message_id"], None)
    who, when = f"@{h(a['username'])}", fmt.when(p["scheduled_for"], a["timezone"])
    if now_:
        text = f"<b>Posting now</b> on {who}: live within about a minute. /p{p['id']}"
    elif p["status"] == "DRAFT":
        why = "This brand needs approval before it publishes." if r["brand_id"] else "Posts without a brand start as drafts."
        text = f"<b>Saved as a draft</b> for {when} on {who}. {why} /p{p['id']}"
    else:
        text = f"<b>Scheduled</b> for {when} on {who}. /p{p['id']}"
    rows = [[("Approve", f"pa:{p['id']}")]] if p["status"] == "DRAFT" else []
    await bot.edit(msg, text, rows + [[("Open post", f"p:{p['id']}")]])
    return "Posting now" if now_ else "Saved as a draft" if p["status"] == "DRAFT" else "Scheduled"


def form_of(bot, p: dict) -> dict:
    if (v := bot.views.get(p["view"])) is None or v["kind"] != "schedule":
        raise Alert("That form has expired: open the render again.")
    return v


@answer("schedule_time")
async def schedule_time(bot, m, p):
    v = form_of(bot, p)
    v["at"], v["suggested"], v["step"] = read_time(text_of(m), v["acc"]["timezone"], p.get("day")), False, "form"
    await bot.edit(v["msg"], *await schedule_view(bot, v))
    await bot.send(f"Set to {fmt.when(v['at'], v['acc']['timezone'])}. Tap Schedule in the form.", reply_to=p["view"])


@answer("schedule_caption")
async def schedule_caption(bot, m, p):
    v = form_of(bot, p)
    v["caption"] = caption_of(m)
    await bot.edit(v["msg"], *await schedule_view(bot, v))
    await bot.send("Caption set.", reply_to=p["view"])


# ---------------------------------------------------------------- imports: videos, links, documents

HANDLE = re.compile(r"(?<![\w/@.])@([A-Za-z0-9_.]{1,30})")


def handle_in(text: str) -> str | None:
    """'@creator' written in a caption or next to a link (not the @ inside a TikTok URL or an email)."""
    found = HANDLE.search(text or "")
    return "@" + found[1].rstrip(".") if found else None


def video_of(m: dict) -> dict | None:
    if v := m.get("video") or m.get("animation"):
        return v
    d = m.get("document") or {}
    if d.get("mime_type") in VIDEO_TYPES.values() or PurePath(d.get("file_name") or "").suffix.lower() in VIDEO_TYPES:
        return d
    return None


def upload_name(f: dict, m: dict) -> str:
    """The file's own name when it has a video extension the api takes, else telegram-<message id>.<ext>."""
    if PurePath(name := f.get("file_name") or "").suffix.lower() in VIDEO_TYPES:
        return name
    return f"telegram-{m['message_id']}" + next((e for e, t in VIDEO_TYPES.items() if t == f.get("mime_type")), ".mp4")


def upload_question(v: dict) -> str:
    return (f"Import {fmt.plural(len(v['files']), 'video')} ({fmt.mb(sum(f['size'] for f in v['files']))})"
            + (f" by {h(v['handle'])}" if v["handle"] else "") + "?")  # fmt: skip


async def on_message(bot, m: dict) -> None:
    """A message that is neither a command nor an answer: something to import."""
    if f := video_of(m):
        return await ask_upload(bot, m, f)
    if d := m.get("document"):
        suffix = PurePath(d.get("file_name") or "").suffix.lower()
        if suffix in DOCUMENTS or (d.get("mime_type") or "").startswith("text/"):
            return await import_document(bot, m, d)
        if suffix == ".png":
            return await bot.send("To set a brand's logo: /brands, open the brand, tap Logo, then send the PNG.")
        return await bot.send("I import videos (mp4, mov, webm) and the links in documents (docx, xlsx, pptx, odt, txt, csv, md, rtf, html).")
    if m.get("photo"):
        return await bot.send("That's a photo. Send a video to import it; a brand's logo goes in /brands (open the brand, tap Logo).")
    if text := text_of(m):
        return await import_text(bot, m, text)
    await bot.send("Send a video, a link or a document to import. /help lists the rest.")


async def ask_upload(bot, m: dict, f: dict) -> None:
    size = f.get("file_size") or 0
    if size > TG_DOWNLOAD_MAX:
        return await bot.send(f"That video is {fmt.mb(size)}. Telegram lets bots download files up to 20 MB: upload it in the "
                              "web app, or send the link to the post instead.", reply_to=m["message_id"])  # fmt: skip
    item = {"file_id": f["file_id"], "name": upload_name(f, m), "size": size}
    handle, group = handle_in(m.get("caption") or ""), m.get("media_group_id")
    if group and (v := bot.views.get(bot.albums.get(group, 0))) and v["kind"] == "upload":  # the rest of an album: one question
        v["files"].append(item)
        v["handle"] = v["handle"] or handle
        return await bot.edit(v["msg"], upload_question(v), import_rows("up"))
    v = {"kind": "upload", "files": [item], "handle": handle}
    bot.keep(await bot.send(upload_question(v), import_rows("up"), reply_to=m["message_id"]), v)
    if group:
        bot.albums[group] = v["msg"]["message_id"]


@button("up")
async def upload_go(bot, msg, op):
    v = bot.view(msg, "upload")
    bot.views.pop(msg["message_id"], None)
    if op == "x":
        return await bot.edit(msg, "Import cancelled.")
    await bot.edit(msg, f"Uploading {fmt.plural(len(v['files']), 'video')}…")
    bot.spawn(upload_all(bot, msg, v))


async def upload_all(bot, msg: dict, v: dict) -> None:
    ok, bad = [], []
    for f in v["files"]:
        fields = {"source_creator_handle": v["handle"]} if v["handle"] else {}
        try:
            async with FILES:
                data = await bot.tg.download(f["file_id"])
                kind = VIDEO_TYPES[PurePath(f["name"]).suffix.lower()]
                clip = await bot.api("POST", "/api/clips", data=fields, files={"file": (f["name"], data, kind)})
        except (ApiError, TelegramError) as e:
            bad.append(f"{h(f['name'])}: {h(str(e))}")
            continue
        bot.watch("clip", clip["id"])
        ok.append(f"/c{clip['id']}")
    text = f"Uploaded {fmt.plural(len(ok), 'video')}: {' '.join(ok)}. Checking them now; each card follows when it's ready." if ok else "Nothing uploaded."
    await bot.edit(msg, text + "".join(f"\n{b}" for b in bad))


async def find_clip(bot, url: str) -> dict | None:
    k = links.key(url)
    return next((c for c in await bot.api.get("/api/clips") if c["source_url"] and links.key(c["source_url"]) == k), None)


def links_summary(found: dict) -> tuple[str, list[str]]:
    vids = found["links"]
    fresh = [link["url"] for link in vids if not link["in_library"]]
    notes = [f"{len(vids) - len(fresh)} already in the library"] if len(fresh) < len(vids) else []
    notes += [fmt.plural(found["repeats"], "repeat")] if found["repeats"] else []
    notes += [f"{fmt.plural(found['other_count'], 'other link')} skipped"] if found["other_count"] else []
    per = " · ".join(f"{p} {n}" for p, n in Counter(link["platform"] for link in vids).items())
    text = f"Found {fmt.plural(len(vids), 'video')} ({per})" + (f": {', '.join(notes)}" if notes else "") + "."
    return text + (f"\nImport {len(fresh)}?" if fresh else "\nNothing new to import."), fresh


async def import_text(bot, m: dict, text: str) -> None:
    found = await bot.api.post("/api/clips/links", files={"file": ("message.txt", text.encode(), "text/plain")})
    vids = found["links"]
    if not vids and len(found["other"]) == 1:  # one link on another site: the web app's URL field hands any link to yt-dlp
        vids = [{"url": found["other"][0], "platform": "", "in_library": await find_clip(bot, found["other"][0]) is not None}]
    if not vids:
        other = f"No video link in that ({fmt.plural(found['other_count'], 'other link')}). " if found["other_count"] else ""
        return await bot.send(other + "Send a video, a link or a document to import. /help lists the rest.")
    if len(vids) > 1:
        q, urls = links_summary(found)
        if not urls:
            return await bot.send(q, reply_to=m["message_id"])
        v = {"kind": "links", "urls": urls}
    else:
        link = vids[0]
        if link["in_library"] and (clip := await find_clip(bot, link["url"])):
            await bot.send("Already in the library:", reply_to=m["message_id"])
            return await open_card(bot, "c", clip["id"])
        v = {"kind": "links", "urls": [link["url"]], "handle": handle_in(text), "single": True}
        q = (f"Import this {link['platform'] or 'link'}" + (f" by {h(v['handle'])}" if v["handle"] else "")
             + f"?\n{h(fmt.short_url(link['url']))}")  # fmt: skip
    bot.keep(await bot.send(q, import_rows("li"), reply_to=m["message_id"]), v)


async def import_document(bot, m: dict, d: dict) -> None:
    name = d.get("file_name") or "document"
    if (d.get("file_size") or 0) > TG_DOWNLOAD_MAX:
        return await bot.send(f"{h(name)} is {fmt.mb(d['file_size'])}: Telegram lets bots download files up to 20 MB. "
                              "Use Import links in the web app.", reply_to=m["message_id"])  # fmt: skip
    async with FILES:
        data = await bot.tg.download(d["file_id"])
        found = await bot.api.post("/api/clips/links", files={"file": (name, data, d.get("mime_type") or "application/octet-stream")})
    if not found["links"]:
        other = f" ({fmt.plural(found['other_count'], 'other link')})" if found["other_count"] else ""
        return await bot.send(f"No video links in {h(name)}{other}.", reply_to=m["message_id"])
    q, urls = links_summary(found)
    msg = await bot.send(f"<b>{h(name)}</b>\n{q}", import_rows("li") if urls else None, reply_to=m["message_id"])
    if urls:
        bot.keep(msg, {"kind": "links", "urls": urls})


@button("li")
async def links_go(bot, msg, op):
    v = bot.view(msg, "links")
    if op == "x":
        bot.views.pop(msg["message_id"], None)
        return await bot.edit(msg, "Import cancelled.")
    if v.get("single"):
        c = await bot.api.post("/api/clips/from-url", json={"url": v["urls"][0], "source_creator_handle": v["handle"]})
        bot.views.pop(msg["message_id"], None)
        bot.watch("clip", c["id"])
        return await bot.edit(msg, f"Importing {h(fmt.short_url(v['urls'][0]))} as /c{c['id']}: its card follows when it's ready.")
    ids = []
    for i in range(0, len(v["urls"]), 1000):  # the api takes up to 1000 per request
        ids += (await bot.api.post("/api/clips/from-urls", json={"urls": v["urls"][i : i + 1000]}))["ids"]
    bot.views.pop(msg["message_id"], None)
    if ids:
        bot.watch("batch", msg["message_id"], ids=ids)
    await bot.edit(msg, f"Importing {fmt.plural(len(ids), 'video')}, two at a time behind other work. "
                        "One message when they're all done; /clips shows progress.")  # fmt: skip


# ---------------------------------------------------------------- watches: report what the bot started, once


async def check_watches(bot) -> None:
    if not bot.watches:
        return
    kinds = {kind for kind, _ in bot.watches}
    clips = {c["id"]: c for c in await bot.api.get("/api/clips")} if kinds & {"clip", "batch"} else {}
    for (kind, id), w in list(bot.watches.items()):
        try:
            finished = await WATCHERS[kind](bot, id, w, clips)
        except ApiError as e:
            finished = e.status == 404  # gone; anything else (the api restarting): next time
        if finished or time.monotonic() - w["since"] > (3600 if kind == "post" else 7200):
            bot.watches.pop((kind, id), None)


async def watch_clip(bot, cid, w, clips) -> bool:
    if (c := clips.get(cid)) is None:
        return True
    if c["status"] not in ("READY", "FAILED"):
        return False
    await open_card(bot, "c", cid)
    return True


async def watch_batch(bot, mid, w, clips) -> bool:
    cs = [clips[i] for i in w["ids"] if i in clips]
    if any(c["status"] not in ("READY", "FAILED") for c in cs):
        return False
    failed = [c for c in cs if c["status"] == "FAILED"]
    lines = [f"<b>Import finished</b>: {len(cs) - len(failed)} of {len(w['ids'])} ready" + (f", {len(failed)} failed" if failed else "") + ". /clips"]
    lines += [f"/c{c['id']} {h(fmt.CAUSES.get(c['error_code'], c['error_code'] or 'failed'))} · {h(fmt.cut(fmt.clip_name(c), 40))}" for c in failed[:20]]
    await bot.send("\n".join(lines), reply_to=mid)
    return True


async def watch_render(bot, rid, w, clips) -> bool:
    if (await bot.api.get(f"/api/renders/{rid}"))["status"] not in ("READY", "FAILED"):
        return False
    await open_card(bot, "r", rid)
    return True


async def watch_post(bot, pid, w, clips) -> bool:
    p = await bot.api.get(f"/api/posts/{pid}")
    s = p["status"]
    if s == "PUBLISHED":
        link = p["permalink"] or ""
        await bot.send(f"<b>Live on Instagram</b>: @{h(p['account_username'])}, post {pid}.",
                       [[("View on Instagram", link)]] if link.startswith("https://") else None)  # fmt: skip
    elif s in FAILED:
        await open_card(bot, "p", pid)  # with its remedy
    elif s in ("DRAFT", "SCHEDULED") and fmt.iso(p["scheduled_for"]) > w["at"] + timedelta(minutes=5):  # moved on
        when = fmt.when(p["scheduled_for"], await bot.tz(p["account_id"]))
        await bot.send(f"Post {pid} on @{h(p['account_username'])} moved to {when}" + (f": {h(p['cause'])}" if p["cause"] else ".") + f" /p{pid}")
    else:
        return s == "CANCELLED"
    return True


WATCHERS = {"clip": watch_clip, "batch": watch_batch, "render": watch_render, "post": watch_post}
