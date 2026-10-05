"""Ingest and render jobs: download_clip (yt-dlp), probe_clip (ffprobe + thumbnail), render (ffmpeg), on the
`media` queue (the worker service; the publisher runs the default queue). A user's files go under their prefix
(storage.key).

Sync def tasks (they block on subprocesses; PLAN section 2). Every row change is a compare-and-set, so a
duplicate or late job is a no-op. A known failure (Failed) or any unexpected error inside the work ends the
row in FAILED with an error code (PLAN D12: ffmpeg exit != 0 is final). The one transient error is the DB
itself: a status write that hits OperationalError fails the job, which is retried (the CAS accepts the
in-progress status, so the re-run redoes the work) rather than leaving the row stuck. A worker crash is
handled by the stalled sweeper in queue.py.
"""

import json
import logging
import mimetypes
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from procrastinate import RetryStrategy
from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.db import SyncSession
from app.models import Brand, Render, SourceClip, cas
from app.services import storage
from app.services.render import (
    HDR_TRANSFERS,
    Failed,
    build_ffmpeg_args,
    classify_ytdlp_error,
    ffprobe,
    parse_probe,
    source_fields,
    thumbnail,
)
from app.tasks.queue import app

logger = logging.getLogger(__name__)

MIN_CLIP_S, MAX_CLIP_S = 3, 15 * 60
MAX_OUTPUT_BYTES = 300_000_000  # Reels / Zernio limit
RENDER_TIMEOUT_S = 3600
DOWNLOAD_TIMEOUT_S = 1800
LOG_MAX = 100_000  # ffmpeg_log keeps the tail
DB_RETRY = RetryStrategy(max_attempts=3, wait=30, retry_exceptions={OperationalError})


def _load(model, id: int):
    with SyncSession() as s:
        return s.get(model, id)


def _set(model, id: int, from_statuses: list[str], values: dict) -> bool:
    with SyncSession() as s, s.begin():
        return s.execute(cas(model, id, from_statuses, **values)).rowcount == 1


def _check_room(user_id: int) -> None:
    """The api checked the disk and the user's quota when it queued the job; it may run much later."""
    with SyncSession() as s:
        quota, used = s.execute(storage.USAGE, {"u": user_id}).one()
    if full := storage.room(used, quota):
        raise Failed(*full)


def _attempt(work: Callable[[], dict], detail_field: str) -> dict:
    try:
        return work()
    except Failed as e:
        return {"status": "FAILED", "error_code": e.code, detail_field: e.detail}
    except Exception as e:
        logger.exception("job failed unexpectedly")
        return {"status": "FAILED", "error_code": "INTERNAL_ERROR", detail_field: repr(e)}


@app.task(name="probe_clip", queue="media", retry=DB_RETRY)
def probe_clip(clip_id: int) -> None:
    if (clip := _load(SourceClip, clip_id)) and clip.status == "PROBING":
        _set(SourceClip, clip_id, ["PROBING"], _attempt(lambda: _probe(clip), "error_detail"))


def _probe(clip: SourceClip) -> dict:
    path = storage.path_for(clip.raw_key)
    meta = parse_probe(ffprobe(path)) | {"size_bytes": path.stat().st_size}
    if not MIN_CLIP_S <= meta["duration_s"] <= MAX_CLIP_S:
        detail = f"{meta['duration_s']:.1f} s; clips must be {MIN_CLIP_S} s to {MAX_CLIP_S // 60} min"
        return meta | {"status": "FAILED", "error_code": "DURATION_OUT_OF_RANGE", "error_detail": detail}
    thumb = storage.key(clip.user_id, f"thumbs/clip-{clip.id}.jpg")
    at = min(1.0, meta["duration_s"] / 2)
    thumbnail(path, storage.path_for(thumb), at, hdr=meta["color_transfer"] in HDR_TRANSFERS)
    return meta | {"thumbnail_key": thumb, "status": "READY", "error_code": None, "error_detail": None}


@app.task(name="download_clip", queue="media", retry=DB_RETRY)
def download_clip(clip_id: int) -> None:
    """yt-dlp into raw/, then probe in the same job. (A sync task can't defer inside its own DB
    transaction with the async connector, and probing here leaves no window with a PROBING clip and
    no job: if the worker dies mid-probe, the sweeper re-runs this job and it resumes at the probe.)"""
    clip = _load(SourceClip, clip_id)
    if clip is None or clip.status not in ("DOWNLOADING", "PROBING"):
        return
    if clip.status == "DOWNLOADING":
        values = _attempt(lambda: _download(clip), "error_detail")
        if not _set(SourceClip, clip_id, ["DOWNLOADING"], values) or values["status"] == "FAILED":
            return
    probe_clip(clip_id)


def _download(clip: SourceClip) -> dict:
    _check_room(clip.user_id)
    work = storage.path_for(storage.key(clip.user_id, f"raw/dl-{clip.id}"))
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        with tempfile.TemporaryDirectory() as secret:  # the cookie copy stays outside DATA_DIR (served at /media)
            args = [
                # -I 1: a playlist, carousel or multi-video post URL gives its first item, not all of them
                "yt-dlp", "--no-playlist", "-I", "1", "--no-progress", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
                "--max-filesize", str(settings.MAX_UPLOAD_BYTES), "-o", str(work / "video.%(ext)s"),
                "--print", "after_move:%(.{filepath,extractor_key,webpage_url,uploader_id,uploader,channel})j",
            ]  # fmt: skip
            # the operator's own cookies, for the operator's downloads only; yt-dlp rewrites the file: hand it a copy
            if settings.YTDLP_COOKIES_FILE and clip.user_id == 1:
                shutil.copy(settings.YTDLP_COOKIES_FILE, Path(secret) / "cookies.txt")
                args += ["--cookies", str(Path(secret) / "cookies.txt")]
            try:
                p = subprocess.run(
                    [*args, "--", clip.source_url], capture_output=True, text=True, errors="replace", timeout=DOWNLOAD_TIMEOUT_S
                )
            except subprocess.TimeoutExpired:
                raise Failed("EXTRACTOR_FAILED", f"yt-dlp timed out after {DOWNLOAD_TIMEOUT_S} s") from None
        lines = p.stdout.strip().splitlines()
        if p.returncode:
            raise Failed(*classify_ytdlp_error(p.stderr))
        if not lines:  # exit 0 without a file: skipped by --max-filesize (stderr holds only warnings)
            raise Failed("EXTRACTOR_FAILED", "yt-dlp downloaded nothing (over MAX_UPLOAD_BYTES?)")
        info = json.loads(lines[-1])
        src = Path(info["filepath"])
        key = storage.key(clip.user_id, f"raw/{clip.id}{src.suffix.lower()}")
        src.replace(storage.path_for(key))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    fields = source_fields(info)
    # the operator's value wins, including one PATCHed while this job ran: decided in the UPDATE, not here
    fields["source_creator_handle"] = func.coalesce(SourceClip.source_creator_handle, fields["source_creator_handle"])
    return fields | {"status": "PROBING", "raw_key": key, "content_type": mimetypes.guess_type(key)[0]}


@app.task(name="render", queue="media", retry=DB_RETRY)
def render(render_id: int) -> None:
    # RENDERING too: the sweeper re-runs a job whose worker died mid-render
    if _set(Render, render_id, ["PENDING", "RENDERING"], {"status": "RENDERING"}):
        values = _attempt(lambda: _render(render_id), "ffmpeg_log")
        _set(Render, render_id, ["RENDERING"], values | {"completed_at": func.now()})


def _render(render_id: int) -> dict:
    with SyncSession() as s:
        r = s.get(Render, render_id)
        clip = s.get(SourceClip, r.source_clip_id)
        brand = s.get(Brand, r.brand_id) if r.brand_id else None
    _check_room(r.user_id)
    logo = storage.path_for(brand.logo_key) if brand and brand.logo_key else None
    out_key = storage.key(r.user_id, f"renders/{render_id}.mp4")
    thumb_key = storage.key(r.user_id, f"thumbs/render-{render_id}.jpg")
    out = storage.path_for(out_key)
    part = out.with_name(out.name + ".part")
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"width": clip.width, "height": clip.height, "has_audio": clip.has_audio, "color_transfer": clip.color_transfer}
    args = build_ffmpeg_args(
        meta, r.overlay_config, r.crop_config, logo, storage.path_for(clip.raw_key), part, settings.FFMPEG_THREADS, r.filter
    )
    try:
        p = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=RENDER_TIMEOUT_S)
        log = p.stderr[-LOG_MAX:]
        if p.returncode:
            raise Failed("FFMPEG_FAILED", log)
        part.replace(out)
    finally:
        part.unlink(missing_ok=True)
    size = out.stat().st_size
    if size > MAX_OUTPUT_BYTES:
        out.unlink()
        raise Failed("OUTPUT_TOO_LARGE", f"{log}\noutput {size} bytes > {MAX_OUTPUT_BYTES}")
    duration = parse_probe(ffprobe(out))["duration_s"]
    thumbnail(out, storage.path_for(thumb_key), min(1.0, duration / 2))
    return {
        "status": "READY",
        "output_key": out_key,
        "thumbnail_key": thumb_key,
        "size_bytes": size,
        "duration_s": duration,
        "error_code": None,
        "ffmpeg_log": log,
    }
