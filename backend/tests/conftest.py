"""Tests run against the compose postgres, in a throwaway database (clipper_test) rebuilt at the
start of every session. Run them in the worker container, which has ffmpeg (in the api container the
ffmpeg tests are skipped):

    docker compose run --rm worker pytest
"""

import os

ADMIN_URL = os.environ["DATABASE_URL"]
TEST_DB = os.environ.get("CLIPPER_TEST_DB", "clipper_test")  # parallel runs: give each its own db
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + "/" + TEST_DB
os.environ["DATABASE_URL"] = TEST_URL  # before any app import: settings, engine and queue all use the test db

import shutil  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

import psycopg  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from procrastinate.schema import SchemaManager  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

ALEMBIC = Config(str(Path(__file__).parents[1] / "alembic.ini"))


def libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def db():
    """Fresh clipper_test with the Alembic migration and Procrastinate's schema; yields a sync engine."""
    with psycopg.connect(libpq(ADMIN_URL), autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {TEST_DB}")
    command.upgrade(ALEMBIC, "head")
    with psycopg.connect(libpq(TEST_URL), autocommit=True) as conn:
        conn.execute(SchemaManager.get_schema())
    engine = create_engine(TEST_URL)
    yield engine
    engine.dispose()


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg only in the worker container")


def ffmpeg(*args) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *args], check=True)


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> dict[str, Path]:
    """Generated sources (ffmpeg lavfi), made once per session."""
    d = tmp_path_factory.mktemp("media")
    out = {name: d / f"{name}.{ext}" for name, ext in [
        ("land", "mp4"), ("rotated", "mp4"), ("hlg", "mp4"), ("fast", "mov"), ("odd", "mp4"),
        ("short", "mp4"), ("long_audio", "mp4"), ("fullrange", "mp4"), ("logo", "png"),
    ]}  # fmt: skip

    def src(size, rate=30, seconds=4):
        return ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={seconds}"]

    def tone(freq=440):  # mono, 44.1 kHz: the render must make it stereo 48 kHz
        return ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration=4:sample_rate=44100"]

    # 16:9 with two audio tracks (the render must keep only the first)
    ffmpeg(*src("1920x1080"), *tone(), *tone(880),
           "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out["land"]))
    # phone portrait: coded 1920x1080 plus a display matrix, no re-encode
    ffmpeg("-display_rotation", "90", "-i", str(out["land"]), "-map", "0", "-c", "copy", str(out["rotated"]))
    # iPhone HDR: HEVC 10-bit HLG / bt2020 with HDR10-style mastering display + light level SEI, no audio
    ffmpeg(*src("1280x720"), "-vf", "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc",
           "-c:v", "libx265", "-x265-params", "log-level=error:master-display=G(13250,34500)B(7500,3000)R(34000,16000)"
           "WP(15635,16450)L(10000000,50):max-cll=1000,400", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1", str(out["hlg"]))
    # slo-mo: 120 fps
    ffmpeg(*src("1280x720", rate=120), *tone(), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out["fast"]))
    # odd size (4:4:4 so x264 accepts it), no audio
    ffmpeg(*src("1280x720"), "-vf", "format=yuv444p,crop=1279:719:0:0", "-c:v", "libx264", str(out["odd"]))
    ffmpeg(*src("1280x720", seconds=2), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out["short"]))
    # 4 s of video with 6 s of audio (the render must end with the picture)
    ffmpeg(*src("1280x720"), "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out["long_audio"]))
    # full range (yuvj420p / pc), as some Android phones, OBS and screen recorders write it
    ffmpeg(*src("1280x720"), *tone(), "-c:v", "libx264", "-pix_fmt", "yuvj420p", "-color_range", "pc", "-c:a", "aac",
           str(out["fullrange"]))
    # logo: solid green 2:1 PNG with an alpha channel
    ffmpeg("-f", "lavfi", "-i", "color=c=0x00ff00:size=200x100,format=rgba", "-frames:v", "1", str(out["logo"]))
    return out
