"""Pure helpers for the bot: text, times, keyboards, and the render editor's geometry, which mirrors
frontend/src/lib/geometry.ts (snapPosition, crop916) and utils.ts (fillCaption, CAUSES). No I/O."""

import html
import math
import re
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

h = html.escape

OUT_W, OUT_H = 1080, 1920
IG_TOP, IG_BOTTOM, IG_SIDE = 0.14, 0.35, 0.06  # geometry.ts IG: keep the Reels chrome clear
MARGIN = 0.04  # the editor's default snap margin, a fraction of the output width
MIN_W, MAX_W, W_STEP = 0.02, 0.6, 0.02
DEFAULT_OVERLAY = {"x": 0.72, "y": 0.16, "w": 0.22, "opacity": 1}  # the brands column default
SNAPS = ["Top left", "Top centre", "Top right", "Middle left", "Centre", "Middle right", "Bottom left", "Bottom centre",
         "Bottom right"]  # fmt: skip
ARROWS = ["↖", "↑", "↗", "←", "·", "→", "↙", "↓", "↘"]
OPACITIES = [1, 0.75, 0.5, 0.25]
VOLUMES = [100, 75, 50, 25, 0]  # the render editor's song and clip's-sound steps (a song never goes to 0)
CAPTION_MAX, HASHTAG_MAX = 2200, 30
# Clip and render error codes in plain words (utils.ts CAUSES)
CAUSES = {
    "PRIVATE": "Private video", "LOGIN_REQUIRED": "Needs login cookies", "REMOVED": "Video removed",
    "GEO_BLOCKED": "Blocked in this region", "EXTRACTOR_FAILED": "Import failed",
    "DURATION_OUT_OF_RANGE": "Must be 3 s to 15 min", "PROBE_FAILED": "Not a readable video",
    "THUMBNAIL_FAILED": "Thumbnail failed", "WORKER_CRASHED": "Worker crashed", "INTERRUPTED": "Interrupted",
    "INTERNAL_ERROR": "Internal error", "UPLOAD_ABANDONED": "Upload abandoned", "FFMPEG_FAILED": "ffmpeg exited with an error",
    "OUTPUT_TOO_LARGE": "Output file too large", "QUOTA_EXCEEDED": "Your storage is full",
    "DISK_FULL": "Server disk almost full",
}  # fmt: skip
FINAL = {"PRIVATE", "REMOVED", "GEO_BLOCKED", "DURATION_OUT_OF_RANGE", "PROBE_FAILED", "UPLOAD_ABANDONED"}  # retry can't fix


def clamp(v: float, lo: float, hi: float) -> float:
    """lo wins when hi < lo, as in geometry.ts."""
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- editor geometry


def png_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG's IHDR chunk."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20]), int.from_bytes(data[20:24])


def logo_aspect(size: tuple[int, int] | None) -> float:
    """h/w of the logo box in output fractions (geometry.ts logoAspect); a square logo if unknown."""
    w, hh = size or (1, 1)
    return hh / w * OUT_W / OUT_H


def snap(cell: int, w: float, hh: float, margin: float = MARGIN) -> tuple[float, float]:
    """Grid cell 0..8 (row-major) -> the logo's top-left inside the IG safe zone (geometry.ts snapPosition)."""
    my = margin * OUT_W / OUT_H
    xs = [IG_SIDE + margin, 0.5 - w / 2, 1 - IG_SIDE - margin - w]
    ys = [IG_TOP + my, (IG_TOP + 1 - IG_BOTTOM) / 2 - hh / 2, 1 - IG_BOTTOM - my - hh]
    return clamp(xs[cell % 3], 0, 1 - w), clamp(ys[cell // 3], 0, 1 - hh)


def place(o: dict, cell: int | None, aspect: float) -> dict:
    """The overlay with its width kept in range and the logo inside the frame; re-snapped when a cell is set
    (Editor.tsx place)."""
    w = clamp(o["w"], MIN_W, min(MAX_W, 1 / aspect))
    x, y = snap(cell, w, w * aspect) if cell is not None else (clamp(o["x"], 0, 1 - w), clamp(o["y"], 0, 1 - w * aspect))
    return {**o, "x": x, "y": y, "w": w}


def bucket(o: dict) -> str:
    """Which grid cell the logo's centre is in (Editor.tsx placement: as if the logo were square)."""
    cy = o["y"] + o["w"] * OUT_W / OUT_H / 2
    col = clamp(math.floor((o["x"] + o["w"] / 2) * 3), 0, 2)
    row = clamp(math.floor((cy - IG_TOP) / (1 - IG_TOP - IG_BOTTOM) * 3), 0, 2)
    return SNAPS[row * 3 + col]


def placement(r: dict) -> str:
    """'Top right · 22% · 9:16 crop' / 'Full frame · Juno · ♫ Song' for a render (Editor.tsx placement)."""
    c, o = r.get("crop_config"), r.get("overlay_config")
    crop = "full frame" if not c or (c["w"] > 0.999 and c["h"] > 0.999) else "9:16 crop"
    look = (f" · {r['filter']}" if r.get("filter") else "") + (f" · ♫ {h(r['music']['name'] or '')}" if r.get("music") else "")
    if r.get("brand_id") is None or not o:
        return crop.capitalize() + look
    return f"{bucket(o)} · {round(o['w'] * 100)}% · {crop}{look}"


def crop_options(sw: int, sh: int) -> list[str]:
    """Where a 9:16 window can sit in a sw x sh source; 'centre' is no crop (the render's own centre fill)."""
    a = 16 / 9 * sw / sh  # h/w of a 9:16 region in source fractions (geometry.ts cropAspect)
    return ["centre", "left", "right"] if a > 1.001 else ["centre", "top", "bottom"] if a < 0.999 else ["centre"]


def crop_box(sw: int, sh: int, where: str) -> dict | None:
    """The largest 9:16 region of the source (geometry.ts crop916), slid to one edge. None for centre."""
    if where == "centre":
        return None
    a = 16 / 9 * sw / sh
    w = min(1, 1 / a)
    hh = w * a
    x = {"left": 0, "right": 1 - w}.get(where, (1 - w) / 2)
    y = {"top": 0, "bottom": 1 - hh}.get(where, (1 - hh) / 2)
    return {"x": x, "y": y, "w": w, "h": hh}


# ---------------------------------------------------------------- captions


def fill_caption(template: str | None, link: str | None, creator: str | None) -> str:
    """Brand template -> caption (utils.ts fillCaption). With no creator, the ' · ' part (or the whole line)
    holding {creator} is dropped instead of leaving 'clip by ' dangling."""
    lines = (template or "").split("\n")
    out = []
    for line in lines:
        kept = line if creator or "{creator}" not in line else " · ".join(p for p in line.split(" · ") if "{creator}" not in p)
        if kept.strip() or "{creator}" not in line or len(lines) == 1:
            out.append(kept)
    return "\n".join(out).replace("{link}", link or "").replace("{creator}", creator or "")


def hashtags(s: str) -> int:
    return len(re.findall(r"#\w+", s))


# ---------------------------------------------------------------- text


def cut(s: str | None, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def mmss(s: float | None) -> str:
    return "—" if s is None else f"{int(s // 60):02d}:{int(s % 60):02d}"


def mb(n: float | None) -> str:
    return f"{(n or 0) / 1e6:.1f} MB"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def short_url(s: str) -> str:
    """'tiktok.com/@creator/video/7612' for an http(s) URL: no scheme, www or tracking query (utils.ts shortUrl)."""
    if not re.match(r"https?://", s, re.IGNORECASE):
        return s
    u = urlsplit(s)
    v = parse_qs(u.query).get("v")
    return re.sub(r"^www\.", "", u.netloc) + u.path.rstrip("/") + (f"?v={v[0]}" if v else "")


def clip_name(c: dict) -> str:
    return c.get("original_filename") or (short_url(c["source_url"]) if c.get("source_url") else f"Clip {c['id']}")


def visible(text: str) -> int:
    """Length of Telegram HTML as the reader sees it (the caption and message limits count this)."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def shorten(text: str, limit: int) -> str:
    """Drop blockquotes (caption previews) until the text fits; the rest of a card is short by construction."""
    while visible(text) > limit and "<blockquote>" in text:
        text = re.sub(r"\n?<blockquote>.*?</blockquote>", "", text, count=1, flags=re.DOTALL)
    return text


def markup(rows: list[list[tuple[str, str]]]) -> dict:
    """Inline keyboard from rows of (label, data): an https data is a link button, anything else callback data
    (1-64 bytes, Telegram's limit)."""
    out = []
    for row in rows:
        buttons = []
        for label, data in row:
            if data.startswith("https://"):
                buttons.append({"text": label, "url": data})
            elif 1 <= len(data.encode()) <= 64:
                buttons.append({"text": label, "callback_data": data})
            else:
                raise ValueError(f"callback_data must be 1-64 bytes: {data!r}")
        if buttons:
            out.append(buttons)
    return {"inline_keyboard": out}


# ---------------------------------------------------------------- times


def every(step: int, start: str, end: str) -> list[str]:
    """'HH:MM' every `step` minutes from start to end, both included (schedule.ts everyN)."""
    a, b = (int(t[:2]) * 60 + int(t[3:]) for t in (start, end))
    return [f"{m // 60:02d}:{m % 60:02d}" for m in range(a, b + 1, step)]


SLOT_PRESETS = {  # one-tap posting-slot sets: the account card's, as the Accounts page's (schedule.ts SLOT_PRESETS)
    "h": ("Every hour, 07:00–23:00", every(60, "07:00", "23:00")),  # new accounts' default
    "m": ("Every 30 min, 07:00–23:30", every(30, "07:00", "23:30")),
    "2": ("Every 2 hours, 08:00–22:00", every(120, "08:00", "22:00")),
    "3": ("3 a day: 09:00, 13:00, 19:00", ["09:00", "13:00", "19:00"]),
}


def slots_label(times: list[str]) -> str:
    """'every hour, 07:00–23:00' for a preset's times, else the times themselves."""
    label = next((name for name, preset in SLOT_PRESETS.values() if times == preset), None)
    return label[0].lower() + label[1:] if label else " ".join(times) or "none"


def iso(s: str | datetime) -> datetime:
    return s if isinstance(s, datetime) else datetime.fromisoformat(s)


def when(t: str | datetime, tz: str, zone: bool = True) -> str:
    """'Sun 27 Sep 19:00 EDT', in tz."""
    lt = iso(t).astimezone(ZoneInfo(tz))
    return f"{lt:%a} {lt.day} {lt:%b %H:%M}" + (f" {lt.tzname()}" if zone else "")


def hm(t: str | datetime, tz: str) -> str:
    return f"{iso(t).astimezone(ZoneInfo(tz)):%H:%M}"


def ago(t: str | datetime, now: datetime) -> str:
    s = (now - iso(t)).total_seconds()
    if s < 45:
        return "just now"
    if s < 3600:
        return f"{max(1, round(s / 60))}m ago"
    if s < 86400:
        return f"{round(s / 3600)}h ago"
    return "yesterday" if s < 2 * 86400 else f"{int(s // 86400)}d ago"


def rel(t: str | datetime, now: datetime) -> str:
    s = (iso(t) - now).total_seconds()
    if s < 0:
        return ago(t, now)
    if s < 60:
        return "now"
    if s < 3600:
        return f"in {round(s / 60)}m"
    return f"in {round(s / 3600)}h" if s < 86400 else f"in {round(s / 86400)}d"


def local_day(t: datetime, tz: str) -> date:
    return t.astimezone(ZoneInfo(tz)).date()


def day_label(d: date, today: date) -> str:
    return "Today" if d == today else "Tomorrow" if d == today + timedelta(days=1) else f"{d:%a} {d.day}"


def day_bounds(d: date, tz: str) -> tuple[datetime, datetime]:
    """UTC [start, end) of local day d (23 or 25 h long on DST days), as slots.day_bounds."""
    start = datetime.combine(d, time(), tzinfo=ZoneInfo(tz))
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def day_slots(times: list[str], d: date, tz: str) -> list[datetime]:
    """The posting slots ('HH:MM') of local day d as UTC instants; a DST-gap time moves forward (fold=0), as
    slots.slot_instants."""
    z = ZoneInfo(tz)
    return sorted({datetime.combine(d, time(*map(int, t.split(":"))), tzinfo=z).astimezone(UTC) for t in times})


DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WHEN = re.compile(
    r"(?:(today|tomorrow|tmrw|mon|tue|wed|thu|fri|sat|sun)[a-z]*|(\d{4}-\d{2}-\d{2}))?\s*(?:at\s*)?"
    r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?"
)


def parse_when(text: str, tz: str, now: datetime, day: date | None = None) -> datetime | None:
    """A time typed in the account's zone -> UTC: 'now', '18:30', '6:30pm', 'tomorrow 9am', 'fri 13:00',
    '2026-10-02 09:00'. A bare time is on `day` (else today); a weekday is the next one with that time still
    ahead. None when it doesn't read as a time."""
    t = " ".join(text.lower().split())
    if t == "now":
        return now.astimezone(UTC).replace(microsecond=0)
    m = WHEN.fullmatch(t)
    if not m:
        return None
    word, iso_day, hh, mm, ampm = m.groups()
    if mm is None and ampm is None:  # "9" alone: not a time
        return None
    hour, minute = int(hh), int(mm or 0)
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    z = ZoneInfo(tz)
    today = now.astimezone(z).date()
    try:
        d = date.fromisoformat(iso_day) if iso_day else day or today
    except ValueError:
        return None
    if word in ("tomorrow", "tmrw"):
        d = today + timedelta(days=1)
    elif word in DAYS:
        d = today + timedelta(days=(DAYS.index(word) - today.weekday()) % 7)
    elif word == "today":
        d = today
    at = datetime.combine(d, time(hour, minute), tzinfo=z).astimezone(UTC)
    if word in DAYS and at < now:
        at = datetime.combine(d + timedelta(days=7), time(hour, minute), tzinfo=z).astimezone(UTC)
    return at
