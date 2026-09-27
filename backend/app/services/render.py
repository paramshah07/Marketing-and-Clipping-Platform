"""ffmpeg, ffprobe and yt-dlp: pure command builders / parsers (unit-tested in tests/test_render.py)
plus thin subprocess runners. Only the worker container has these binaries.

Geometry (PLAN D2/D3): overlay fractions are of the 1080x1920 output, crop fractions of the source
frame after autorotate (the probe stores display dims, and ffmpeg autorotates, so they agree).
"""

import json
import subprocess
from pathlib import Path

OUT_W, OUT_H = 1080, 1920
HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}  # HLG, PQ (iPhone HDR)
# HDR -> SDR bt709. zscale errors on untagged input, so only for sources tagged HLG/PQ. Then drop the
# source's HDR side data (mastering display, content light level), or x264 and the mp4 muxer copy it
# into the SDR output. (npl=100 + hable matched YouTube's own SDR versions of real iPhone HLG and PQ
# uploads better than npl=203 + mobius: docs/phase-2.md.)
TONEMAP = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,sidedata=mode=delete"
)


class Failed(Exception):
    """A known failure: the row goes to FAILED with this error code and detail."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code, detail)
        self.code, self.detail = code, detail


def crop_px(crop: dict, width: int, height: int) -> tuple[int, int, int, int]:
    """Crop fractions of a width x height frame -> even integer (w, h, x, y). Floored to even, so
    x + w never exceeds the frame, even for odd source sizes."""

    def even(v: float) -> int:
        return int(v) // 2 * 2

    return (
        max(2, even(crop["w"] * width)),
        max(2, even(crop["h"] * height)),
        even(crop["x"] * width),
        even(crop["y"] * height),
    )


def build_ffmpeg_args(
    clip: dict, overlay: dict | None, crop: dict | None, logo_path, in_path, out_path, threads: int
) -> list[str]:
    """The one render command. clip: display width/height, has_audio, color_transfer (probe output).
    Chain: fps 30 -> crop (source fractions) -> tonemap if HLG/PQ -> fill + centre-crop 1080x1920
    (limited range) -> logo (output fractions; its width is a fraction of 1080, not of the logo) -> H.264/AAC.
    The output is exactly as long as the video: audio is padded with silence and cut at the last frame."""
    inputs = ["-i", str(in_path)]
    v = "[0:V:0]fps=30"  # V: not cover art (attached_pic), the same stream parse_probe measured
    if crop:
        v += ",crop={}:{}:{}:{}".format(*crop_px(crop, clip["width"], clip["height"]))
    if clip.get("color_transfer") in HDR_TRANSFERS:
        v += "," + TONEMAP
    # out_range=tv: a full-range (yuvj420p / pc) source would otherwise stay full range
    v += f",scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase:out_range=tv,crop={OUT_W}:{OUT_H},setsar=1"
    if logo_path:
        inputs += ["-i", str(logo_path)]
        v += (
            f"[bg];[1:v]scale={round(overlay['w'] * OUT_W)}:-1,format=rgba,"
            f"colorchannelmixer=aa={overlay.get('opacity', 1)}[logo];"
            f"[bg][logo]overlay=x={round(overlay['x'] * OUT_W)}:y={round(overlay['y'] * OUT_H)}"
        )
    v += "[v]"
    if clip["has_audio"]:  # first audio track only, padded so a short track still spans the video
        v += ";[0:a:0]apad[a]"
        audio = "[a]"
    else:  # Reels need an audio track: silent stereo
        inputs += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        audio = f"{2 if logo_path else 1}:a"
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-y", *inputs,
        "-filter_complex", v, "-map", "[v]", "-map", audio,
        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium",
        "-maxrate", "20M", "-bufsize", "40M", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
        "-threads", str(threads),
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-shortest", "-movflags", "+faststart", "-f", "mp4", str(out_path),  # audio is endless: cut at the video's end
    ]  # fmt: skip


def _rate(r: str | None) -> float:
    num, _, den = (r or "0/0").partition("/")
    return float(num) / float(den or 1) if float(den or 1) else 0.0


def parse_probe(info: dict) -> dict:
    """ffprobe -show_streams -show_format JSON -> source_clips columns. Display dims (width/height
    swapped for a +-90 rotation), average fps (r_frame_rate misleads on VFR phone clips), and the video
    stream's duration (the format's is the longest stream; webm streams have none, so fall back)."""
    streams = info.get("streams", [])
    v = next(
        (s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    if v is None:
        raise Failed("PROBE_FAILED", "no video stream")
    rotation = next(
        (sd.get("rotation", 0) for sd in v.get("side_data_list", []) if sd.get("side_data_type") == "Display Matrix"),
        0,
    )
    width, height = v["width"], v["height"]
    if round(float(rotation)) % 180:  # +-90 / 270: ffprobe reports coded dims
        width, height = height, width
    return {
        "width": width,
        "height": height,
        "fps": round(_rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate")), 3),
        "duration_s": float(v.get("duration") or info.get("format", {}).get("duration") or 0),
        "video_codec": v.get("codec_name"),
        "color_transfer": v.get("color_transfer"),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def _run(args: list[str], code: str, timeout: int = 120) -> subprocess.CompletedProcess:
    p = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout)
    if p.returncode:
        raise Failed(code, p.stderr.strip()[-4000:] or f"{args[0]} exited {p.returncode}")
    return p


def ffprobe(path: Path) -> dict:
    args = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    return json.loads(_run(args, "PROBE_FAILED").stdout)


def thumbnail(in_path: Path, out_path: Path, at: float, hdr: bool = False) -> None:
    """One JPEG frame at `at` seconds, longest side 540 px."""
    vf = (TONEMAP + "," if hdr else "") + "scale=540:540:force_original_aspect_ratio=decrease:force_divisible_by=2"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{at:.3f}", "-i", str(in_path),
         "-frames:v", "1", "-vf", vf, "-q:v", "4", str(out_path)],
        "THUMBNAIL_FAILED",
    )  # fmt: skip


# yt-dlp exits 1 for every error, so classify its last "ERROR:" line. Lowercase substrings, first
# match wins. A login wall goes first: Instagram's (seen live 2026-09-27) also says "please report this
# issue", which is what an unexpected extractor crash says.
YTDLP_ERRORS = [
    ("--cookies", "LOGIN_REQUIRED"),  # yt-dlp's own hint: YTDLP_COOKIES_FILE fixes it, then Retry
    ("empty media response", "LOGIN_REQUIRED"),  # Instagram: login wall or rate limit
    ("please report this issue", "EXTRACTOR_FAILED"),
    ("not available from your location", "GEO_BLOCKED"),
    ("geo restriction", "GEO_BLOCKED"),
    ("available in your country", "GEO_BLOCKED"),
    ("private video", "PRIVATE"),
    ("this video is private", "PRIVATE"),
    ("only available for registered users", "PRIVATE"),
    ("login required", "PRIVATE"),
    ("log in", "PRIVATE"),
    ("sign in to confirm", "LOGIN_REQUIRED"),  # YouTube: "...your age" / "...you're not a bot"
    ("your ip address is blocked", "PRIVATE"),  # TikTok: ambiguous, needs cookies either way
    ("video unavailable", "REMOVED"),
    ("video is unavailable", "REMOVED"),  # YouTube, 2026
    ("this video has been removed", "REMOVED"),
    ("no longer available", "REMOVED"),
    ("does not exist", "REMOVED"),
    ("account has been terminated", "REMOVED"),
]


def classify_ytdlp_error(stderr: str) -> tuple[str, str]:
    """-> (error_code, the raw line for error_detail)."""
    lines = stderr.strip().splitlines()
    errors = [line for line in lines if line.startswith("ERROR:")]
    line = (errors or lines or ["yt-dlp failed without output"])[-1]
    return next((code for sub, code in YTDLP_ERRORS if sub in line.lower()), "EXTRACTOR_FAILED"), line


# where each extractor keeps the creator's handle
HANDLE_FIELD = {"Youtube": "uploader_id", "Instagram": "channel", "TikTok": "uploader", "Twitter": "uploader_id"}


def source_fields(info: dict) -> dict:
    """yt-dlp info JSON -> platform, source_creator_handle ('@name'), canonical source_url."""
    field = HANDLE_FIELD.get(info.get("extractor_key", ""))
    handle = info.get(field) if field else None
    return {
        "platform": info.get("extractor_key"),
        "source_creator_handle": "@" + handle.lstrip("@") if handle else None,
        "source_url": info.get("webpage_url"),
    }
