import struct
import subprocess

import pytest

from app.services.render import (
    build_ffmpeg_args,
    classify_ytdlp_error,
    crop_px,
    ffprobe,
    parse_probe,
    source_fields,
)
from conftest import ffmpeg, needs_ffmpeg

CLIP = {"width": 1920, "height": 1080, "has_audio": True, "color_transfer": "bt709"}
TOP_RIGHT = {"x": 0.72, "y": 0.06, "w": 0.22, "opacity": 1}


def graph(args: list[str]) -> str:
    return args[args.index("-filter_complex") + 1]


# ---------------------------------------------------------------- build_ffmpeg_args (pure)


def test_logo_width_is_a_fraction_of_the_output_frame():
    g = graph(build_ffmpeg_args(CLIP, TOP_RIGHT | {"opacity": 0.8}, None, "logo.png", "in.mp4", "out.mp4", 2))
    assert "[1:v]scale=238:-1,format=rgba,colorchannelmixer=aa=0.8[logo]" in g  # round(0.22 * 1080)
    assert "[bg][logo]overlay=x=778:y=115[v]" in g  # round(0.72 * 1080), round(0.06 * 1920)
    assert "iw*" not in g  # not relative to the logo's own size (PLAN D2)


def test_crop_is_even_and_inside_the_source():
    assert crop_px({"x": 0, "y": 0, "w": 1, "h": 1}, 1279, 719) == (1278, 718, 0, 0)
    assert crop_px({"x": 0.341796875, "y": 0, "w": 0.31640625, "h": 1}, 1920, 1080) == (606, 1080, 656, 0)
    assert crop_px({"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5}, 1279, 719) == (638, 358, 638, 358)
    for x in (0.0, 0.123, 0.5, 0.777):  # never past the edge, whatever the rounding
        w, h, cx, cy = crop_px({"x": x, "y": x, "w": 1 - x, "h": 1 - x}, 1279, 719)
        assert cx + w <= 1279 and cy + h <= 719 and not (w % 2 or h % 2 or cx % 2 or cy % 2)
    clip = {**CLIP, "width": 1279, "height": 719}
    g = graph(build_ffmpeg_args(clip, None, {"x": 0, "y": 0, "w": 1, "h": 1}, None, "in", "out", 2))
    assert g.startswith("[0:V:0]fps=30,crop=1278:718:0:0,scale=1080:1920:force_original_aspect_ratio=increase:out_range=tv")
    assert graph(build_ffmpeg_args(CLIP, None, None, None, "in", "out", 2)).startswith("[0:V:0]fps=30,scale=1080:1920:")


def test_tonemap_only_for_hlg_and_pq():
    for transfer, tonemapped in [("arib-std-b67", True), ("smpte2084", True), ("bt709", False), (None, False)]:
        g = graph(build_ffmpeg_args({**CLIP, "color_transfer": transfer}, None, None, None, "in", "out", 2))
        assert ("tonemap=" in g) is tonemapped
        assert ("zscale" in g) is tonemapped  # zscale errors on untagged input


def test_audio_first_track_only_or_silence():
    with_audio = build_ffmpeg_args(CLIP, TOP_RIGHT, None, "logo.png", "in", "out", 2)
    assert graph(with_audio).endswith("[v];[0:a:0]apad[a]")  # padded, so the video decides the length
    assert with_audio[with_audio.index("[v]") + 1 : with_audio.index("[v]") + 3] == ["-map", "[a]"]
    assert "anullsrc" not in " ".join(with_audio) and "-shortest" in with_audio

    silent = build_ffmpeg_args({**CLIP, "has_audio": False}, TOP_RIGHT, None, "logo.png", "in", "out", 2)
    assert silent[silent.index("anullsrc=channel_layout=stereo:sample_rate=48000") - 1] == "-i"
    assert "2:a" in silent and "-shortest" in silent  # inputs: 0 source, 1 logo, 2 silence
    no_logo = build_ffmpeg_args({**CLIP, "has_audio": False}, None, None, None, "in", "out", 2)
    assert "1:a" in no_logo


def test_encode_settings():
    a = " ".join(build_ffmpeg_args(CLIP, None, None, None, "in.mp4", "out.mp4", 3))
    for part in [
        "-c:v libx264 -profile:v high -pix_fmt yuv420p -crf 20 -preset medium -maxrate 20M -bufsize 40M",
        "-g 60 -keyint_min 60 -sc_threshold 0 -threads 3",
        "-c:a aac -b:a 128k -ar 48000 -ac 2",
        "-movflags +faststart -f mp4 out.mp4",
        "crop=1080:1920,setsar=1",
    ]:
        assert part in a
    assert "-noautorotate" not in a


# ---------------------------------------------------------------- parsers (pure)


def test_parse_probe_rotation_and_vfr():
    info = {
        "format": {"duration": "12.5"},
        "streams": [
            {"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080,
             "avg_frame_rate": "2997/100", "r_frame_rate": "120/1", "color_transfer": "arib-std-b67",
             "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]},
            {"codec_type": "audio"},
        ],
    }  # fmt: skip
    assert parse_probe(info) == {
        "width": 1080, "height": 1920, "fps": 29.97, "duration_s": 12.5,
        "video_codec": "hevc", "color_transfer": "arib-std-b67", "has_audio": True,
    }  # fmt: skip
    info["streams"][0]["duration"] = "2.0"  # the audio runs on: the clip is as long as its picture
    assert parse_probe(info)["duration_s"] == 2.0


YTDLP_STDERR = [
    ("ERROR: [youtube] aaaaaaaaaaa: Video unavailable", "REMOVED"),
    ("ERROR: [youtube] BaW_jenozKc: This video is unavailable", "REMOVED"),  # seen live 2026-09-26
    ("ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader", "REMOVED"),
    ("ERROR: [youtube] x: Video unavailable. This video is no longer available because the YouTube account "
     "associated with this video has been terminated.", "REMOVED"),
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access to this video", "PRIVATE"),
    ("ERROR: [vimeo] 1: This video is private", "PRIVATE"),
    ("ERROR: [twitter] 1: This content is only available for registered users. Use --cookies-from-browser "
     "or --cookies for the authentication.", "PRIVATE"),
    ("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate for some users. Use "
     "--cookies-from-browser or --cookies for the authentication.", "PRIVATE"),
    ("ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the "
     "authentication.", "PRIVATE"),
    ("ERROR: [Instagram] C1: Instagram sent an empty media response. Check if this post is accessible in your "
     "browser without being logged-in.", "PRIVATE"),
    ("ERROR: [TikTok] 7: Your IP address is blocked from accessing this post", "PRIVATE"),
    ("ERROR: [BBC] p1: This video is not available from your location due to geo restriction", "GEO_BLOCKED"),
    ("ERROR: [youtube] x: Video unavailable. The uploader has not made this video available in your country",
     "GEO_BLOCKED"),
    ("ERROR: [youtube] x: Unable to extract initial player response; please report this issue on  "
     "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template.", "EXTRACTOR_FAILED"),
    ("ERROR: Unsupported URL: https://example.com/", "EXTRACTOR_FAILED"),
]  # fmt: skip


@pytest.mark.parametrize(("line", "code"), YTDLP_STDERR)
def test_classify_ytdlp_error(line, code):
    stderr = f"WARNING: [youtube] something odd\n{line}\n"
    assert classify_ytdlp_error(stderr) == (code, line)


def test_classify_ytdlp_error_takes_the_last_error_line():
    stderr = "ERROR: [youtube] a: Private video\nERROR: [youtube] b: Video unavailable\n"
    assert classify_ytdlp_error(stderr)[0] == "REMOVED"
    assert classify_ytdlp_error("") == ("EXTRACTOR_FAILED", "yt-dlp failed without output")


def test_source_fields():
    assert source_fields({"extractor_key": "Youtube", "uploader_id": "@phihag", "webpage_url": "https://y/w"}) == {
        "platform": "Youtube", "source_creator_handle": "@phihag", "source_url": "https://y/w"
    }  # fmt: skip
    assert source_fields({"extractor_key": "Instagram", "channel": "natgeo"})["source_creator_handle"] == "@natgeo"
    assert source_fields({"extractor_key": "TikTok", "uploader": "khaby.lame"})["source_creator_handle"] == "@khaby.lame"
    assert source_fields({"extractor_key": "Generic", "uploader": "Some Name"})["source_creator_handle"] is None


# ---------------------------------------------------------------- real ffmpeg (worker container)


def run_render(clip_path, out, overlay=None, crop=None, logo=None):
    clip = parse_probe(ffprobe(clip_path))
    p = subprocess.run(build_ffmpeg_args(clip, overlay, crop, logo, clip_path, out, 2), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return clip


def frame_planes(path, at=0.5):
    """One output frame as planar G, B, R bytes."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gbrp", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    n = 1080 * 1920
    assert len(raw) == 3 * n
    return raw[:n], raw[n : 2 * n], raw[2 * n :]


BRIGHT = bytes(int(v >= 128) for v in range(256))


def bbox(plane: bytes, w=1080, h=1920) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) of the pixels >= 128 in one plane."""
    mask = plane.translate(BRIGHT)
    rows = [y for y in range(h) if 1 in mask[y * w : (y + 1) * w]]
    x0 = min(mask.find(1, y * w, (y + 1) * w) - y * w for y in rows)
    x1 = max(mask.rfind(1, y * w, (y + 1) * w) - y * w for y in rows)
    return x0, rows[0], x1 + 1, rows[-1] + 1


# display W, H, stored rotated (coded landscape + a display matrix, like a phone), and a crop: a 9:16 crop
# off-centre, a crop of a portrait frame, a non-9:16 crop (fill + centre crop still applies), and an
# asymmetric crop of a rotated portrait frame (PLAN D3: crop fractions are of the frame after autorotate)
SOURCES = {
    "16x9": (1920, 1080, False, {"x": 0.4, "y": 0, "w": 0.31640625, "h": 1}),
    "9x16": (1080, 1920, False, {"x": 0.1, "y": 0.2, "w": 0.6, "h": 0.6}),
    "4x3": (1440, 1080, False, {"x": 0.3, "y": 0.1, "w": 0.5, "h": 0.8}),
    "9x16-rotated": (1080, 1920, True, {"x": 0.3, "y": 0.35, "w": 0.6, "h": 0.6}),
}
MARK = (0.55, 0.6)  # red square centre, as fractions of the display frame: off-centre, so a wrong turn shows


@needs_ffmpeg
@pytest.mark.parametrize("cropped", [False, True], ids=["full", "crop"])
@pytest.mark.parametrize("source", list(SOURCES))
def test_logo_and_crop_land_where_configured(media, tmp_path, source, cropped):
    """Black source with an off-centre red square; green 2:1 logo top right. The logo box must match the
    overlay fractions and the red square must land where the crop maths says, both within 1% of the frame."""
    W, H, rotated, crop = SOURCES[source]
    m = H // 10
    src = tmp_path / "src.mp4"
    pattern = (f"color=c=black:size={W}x{H}:rate=30:duration=1,"
               f"drawbox=x={round(MARK[0] * W) - m // 2}:y={round(MARK[1] * H) - m // 2}:w={m}:h={m}:color=red:t=fill")
    if rotated:  # store it turned 90 degrees clockwise, with a matrix saying "turn it back"
        coded = tmp_path / "coded.mp4"
        ffmpeg("-f", "lavfi", "-i", pattern + ",transpose=clock", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(coded))
        ffmpeg("-display_rotation", "90", "-i", str(coded), "-c", "copy", str(src))
    else:
        ffmpeg("-f", "lavfi", "-i", pattern, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src))
    crop = crop if cropped else None
    out = tmp_path / "out.mp4"
    probed = run_render(src, out, TOP_RIGHT, crop, media["logo"])
    assert (probed["width"], probed["height"]) == (W, H)  # display dims, also when stored rotated
    g, _, r = frame_planes(out)

    x0, y0, x1, y1 = bbox(g)
    width = TOP_RIGHT["w"] * 1080
    expected = (TOP_RIGHT["x"] * 1080, TOP_RIGHT["y"] * 1920, width, width / 2)
    for got, want, frame in zip((x0, y0, x1 - x0, y1 - y0), expected, (1080, 1920, 1080, 1920)):
        assert abs(got - want) <= 0.01 * frame, (x0, y0, x1, y1)

    cx, cy, cw, ch = (crop["x"] * W, crop["y"] * H, crop["w"] * W, crop["h"] * H) if crop else (0, 0, W, H)
    s = max(1080 / cw, 1920 / ch)  # fill, then centre crop to 1080x1920
    want_x = (MARK[0] * W - cx) * s - (cw * s - 1080) / 2
    want_y = (MARK[1] * H - cy) * s - (ch * s - 1920) / 2
    rx0, ry0, rx1, ry1 = bbox(r)
    assert abs((rx0 + rx1) / 2 - want_x) <= 10.8 and abs((ry0 + ry1) / 2 - want_y) <= 19.2, (rx0, ry0, rx1, ry1)


def atoms(path) -> list[str]:
    """Top-level MP4 box types, in file order."""
    types, data, pos = [], path.read_bytes(), 0
    while pos + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[pos : pos + 8])
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
        types.append(kind.decode())
        pos += size or len(data)
    return types


@needs_ffmpeg
@pytest.mark.parametrize("name", ["land", "rotated", "hlg", "fast", "odd", "long_audio", "fullrange"])
def test_output_spec(media, tmp_path, name):
    out = tmp_path / "out.mp4"
    crop = {"x": 0.1, "y": 0, "w": 0.9, "h": 1} if name == "odd" else None
    src = run_render(media[name], out, TOP_RIGHT, crop, media["logo"])
    info = ffprobe(out)
    video = [s for s in info["streams"] if s["codec_type"] == "video"]
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    assert len(video) == 1 and len(audio) == 1  # only the first source audio track, or the silent one
    v, a = video[0], audio[0]
    assert (v["codec_name"], v["profile"], v["pix_fmt"], v["width"], v["height"]) == ("h264", "High", "yuv420p", 1080, 1920)
    assert v.get("color_range", "tv") == "tv"  # limited range, also from a full-range (yuvj420p) source
    assert v["avg_frame_rate"] == "30/1" and v.get("sample_aspect_ratio", "1:1") == "1:1"
    assert not v.get("side_data_list")  # no display matrix (upright) and no HDR mastering / light level metadata
    assert (a["codec_name"], a["sample_rate"], a["channels"]) == ("aac", "48000", 2)
    # as long as the source's picture, with no audio tail past the last frame
    assert abs(float(info["format"]["duration"]) - src["duration_s"]) < 0.1
    assert abs(float(v["duration"]) - src["duration_s"]) < 0.1
    order = atoms(out)
    assert order[0] == "ftyp" and order.index("moov") < order.index("mdat")  # faststart
    if name == "hlg":
        assert v.get("color_transfer") == "bt709"  # tonemapped, not HLG-tagged


@needs_ffmpeg
def test_probe_generated_media(media):
    names = ("land", "rotated", "hlg", "fast", "odd", "short", "long_audio")
    got = {name: parse_probe(ffprobe(media[name])) for name in names}
    assert (got["land"]["width"], got["land"]["height"], got["land"]["fps"], got["land"]["has_audio"]) == (1920, 1080, 30, True)
    assert abs(got["land"]["duration_s"] - 4) < 0.1
    assert (got["rotated"]["width"], got["rotated"]["height"]) == (1080, 1920)  # display dims, not coded
    assert (got["hlg"]["video_codec"], got["hlg"]["color_transfer"], got["hlg"]["has_audio"]) == ("hevc", "arib-std-b67", False)
    assert got["fast"]["fps"] == 120
    assert (got["odd"]["width"], got["odd"]["height"], got["odd"]["has_audio"]) == (1279, 719, False)
    assert abs(got["short"]["duration_s"] - 2) < 0.1
    assert abs(got["long_audio"]["duration_s"] - 4) < 0.1  # the video's 4 s, not the audio's 6 s
