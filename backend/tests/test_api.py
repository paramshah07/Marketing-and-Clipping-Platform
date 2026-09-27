"""Clips / brands / renders API against the test database. Jobs are deferred for real (checked in
procrastinate_jobs) and then run inline, since no worker listens on clipper_test."""

import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import cli
from app.api.pipeline import png_alpha
from app.core.config import settings
from app.core.db import SyncSession, engine
from app.main import app
from app.models import Brand, Render, SourceClip, cas
from app.tasks import media as media_tasks
from app.tasks.media import download_clip, probe_clip, render
from conftest import needs_ffmpeg

PNG = b"\x89PNG\r\n\x1a\n"


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + b"\0\0\0\0"  # CRC unchecked


def png(color_type: int, *chunks: bytes) -> bytes:
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, color_type, 0, 0, 0))
    return PNG + ihdr + b"".join(chunks) + chunk(b"IDAT", b"") + chunk(b"IEND", b"")


@pytest.fixture(scope="module")
def client(db):
    with TestClient(app) as c:
        yield c
        c.portal.call(engine.dispose)  # its connections belong to this client's event loop


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    return tmp_path


def job(db, task: str, **kwargs) -> bool:
    with db.connect() as c:
        return c.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE task_name = :t AND args @> CAST(:a AS jsonb)"),
            {"t": task, "a": json.dumps(kwargs)},
        ).scalar() == 1


def upload(client, path, name=None, **fields):
    with open(path, "rb") as f:
        return client.post(
            "/api/clips", files={"file": (name or path.name, f)}, data={"rights_status": "own_content", **fields}
        )


def test_png_alpha():
    assert png_alpha(png(6)) is True  # RGBA
    assert png_alpha(png(4)) is True  # grey + alpha
    assert png_alpha(png(2, chunk(b"tRNS", b"\0\0\0\0\0\0"))) is True  # RGB with a transparent colour
    assert png_alpha(png(3, chunk(b"PLTE", b"\0" * 3), chunk(b"tRNS", b"\0"))) is True  # palette + tRNS
    assert png_alpha(png(2)) is False
    assert png_alpha(png(3, chunk(b"PLTE", b"\0" * 3))) is False
    assert png_alpha(b"\xff\xd8\xff\xe0 jpeg") is None
    assert png_alpha(PNG) is None
    for cut in (33, 40, len(png(6)) - 12, len(png(6)) - 1):  # after IHDR, inside IDAT, no IEND, half an IEND
        with pytest.raises(ValueError):
            png_alpha(png(6)[:cut])


def test_logo_upload_validation(client):
    brand = client.post("/api/brands", json={"name": "Acme"}).json()
    assert brand["default_overlay_config"] == {"x": 0.72, "y": 0.16, "w": 0.22, "opacity": 1}
    assert brand["logo_url"] is None
    url = f"/api/brands/{brand['id']}/logo"
    assert client.post(url, files={"file": ("a.jpg", b"\xff\xd8\xff\xe0 jpeg")}).status_code == 415
    assert client.post(url, files={"file": ("a.png", png(2))}).status_code == 422
    assert client.post(url, files={"file": ("a.png", png(6)[:40])}).json()["detail"] == "logo PNG is corrupt"
    first = client.post(url, files={"file": ("logo.png", png(6))})
    assert first.status_code == 200 and first.json()["logo_url"].startswith(f"/media/logos/{brand['id']}-")
    second = client.post(url, files={"file": ("logo.png", png(6))}).json()["logo_url"]
    assert second != first.json()["logo_url"]  # new name each time: no stale browser cache
    assert client.post("/api/brands/999999/logo", files={"file": ("logo.png", png(6))}).status_code == 404


def test_brands_archive(client):
    b = client.post("/api/brands", json={"name": "Old", "default_overlay_config": {"x": 0, "y": 0, "w": 0.3}}).json()
    assert b["default_overlay_config"] == {"x": 0, "y": 0, "w": 0.3, "opacity": 1}
    assert client.patch(f"/api/brands/{b['id']}", json={"archived": True}).json()["archived_at"]
    assert b["id"] in [x["id"] for x in client.get("/api/brands?archived=true").json()]
    assert b["id"] not in [x["id"] for x in client.get("/api/brands").json()]
    patched = client.patch(f"/api/brands/{b['id']}", json={"archived": False, "link": "https://acme.test"}).json()
    assert patched["archived_at"] is None and patched["link"] == "https://acme.test" and patched["name"] == "Old"
    assert client.patch(f"/api/brands/{b['id']}", json={"default_overlay_config": {"x": 2, "y": 0, "w": 0.3}}).status_code == 422


def test_upload_rejections_leave_nothing(client, data_dir, monkeypatch, tmp_path):
    before = len(client.get("/api/clips").json())
    fake = tmp_path / "x.mp4"
    fake.write_bytes(b"0" * 1000)
    assert upload(client, fake, name="x.avi").status_code == 415
    r = client.post("/api/clips", files={"file": ("x.mp4", b"0" * 1000)}, data={"rights_status": "maybe"})
    assert r.status_code == 422
    assert client.post("/api/clips", data={"rights_status": "none"}).status_code == 415  # not multipart
    bad_utf8 = client.post("/api/clips", files={"file": ("x.mp4", b"0" * 1000)},
                           data={"rights_status": "none", "source_creator_handle": b"\xff\xfe@bad"})  # fmt: skip
    assert bad_utf8.status_code == 422, bad_utf8.text
    b = b"XyZ"  # a body cut off before its closing boundary is not a complete upload
    cut = (b"--XyZ\r\nContent-Disposition: form-data; name=\"rights_status\"\r\n\r\nnone\r\n--XyZ\r\n"
           b'Content-Disposition: form-data; name="file"; filename="cut.mp4"\r\n\r\n' + b"0" * 1000)  # fmt: skip
    r = client.post("/api/clips", content=cut, headers={"content-type": f"multipart/form-data; boundary={b.decode()}"})
    assert (r.status_code, r.json()["detail"]) == (400, "incomplete multipart body")
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 500)
    assert upload(client, fake).status_code == 413
    assert len(client.get("/api/clips").json()) == before
    assert not any((data_dir / "raw").glob("*"))


@needs_ffmpeg
def test_upload_probe_render(client, db, data_dir, media):
    r = upload(client, media["land"], source_creator_handle="@me")
    assert r.status_code == 201, r.text
    clip = r.json()
    assert (clip["status"], clip["origin"], clip["original_filename"]) == ("PROBING", "upload", "land.mp4")
    assert (clip["rights_status"], clip["source_creator_handle"], clip["content_type"]) == ("own_content", "@me", "video/mp4")
    assert (data_dir / f"raw/{clip['id']}.mp4").read_bytes() == media["land"].read_bytes()
    assert job(db, "probe_clip", clip_id=clip["id"])

    probe_clip(clip["id"])
    clip = client.get(f"/api/clips/{clip['id']}").json()
    assert clip["status"] == "READY", clip
    assert (clip["width"], clip["height"], clip["fps"], clip["has_audio"], clip["video_codec"]) == (1920, 1080, 30, True, "h264")
    assert clip["thumbnail_url"] == f"/media/thumbs/clip-{clip['id']}.jpg"
    assert (data_dir / f"thumbs/clip-{clip['id']}.jpg").stat().st_size > 1000
    assert client.get("/api/clips").json()[0]["id"] == clip["id"]  # newest first
    assert client.post(f"/api/clips/{clip['id']}/retry").status_code == 409  # only from FAILED

    brand = client.post("/api/brands", json={"name": "Logo Co"}).json()
    create = {"clip_id": clip["id"], "brand_id": brand["id"]}
    assert client.post("/api/renders", json=create).status_code == 409  # brand has no logo yet
    client.post(f"/api/brands/{brand['id']}/logo", files={"file": ("logo.png", media["logo"].read_bytes())})
    for bad in [
        {"overlay_config": {"x": 1.5, "y": 0, "w": 0.2}},
        {"overlay_config": {"x": 0.5, "y": 0, "w": 0}},
        {"overlay_config": {"x": 0.1, "y": 0.1, "w": 0.0004}},  # 0 px wide: ffmpeg would draw it full size
        {"overlay_config": {"x": 0.9, "y": 0, "w": 0.2}},  # past the right edge
        {"overlay_config": {"x": 0, "y": 1, "w": 0.2}},  # below the frame
        {"overlay_config": {"x": 0.5, "y": 0, "w": 0.2, "opacity": 2}},
        {"crop_config": {"x": 0, "y": 0, "w": 0, "h": 1}},
        {"crop_config": {"x": 0.5, "y": 0, "w": 0.6, "h": 1}},  # past the right edge
    ]:
        assert client.post("/api/renders", json=create | bad).status_code == 422, bad
    assert client.post("/api/renders", json=create | {"clip_id": 999999}).status_code == 409
    too_big = 2**31  # past int4: bad input, not a server error
    assert client.post("/api/renders", json=create | {"clip_id": too_big}).status_code == 422
    assert client.post("/api/renders", json=create | {"brand_id": too_big}).status_code == 422
    assert client.get(f"/api/clips/{too_big}").status_code == 422
    assert client.get(f"/api/renders/{too_big * 50}").status_code == 422

    crop = {"x": 0.341796875, "y": 0, "w": 0.31640625, "h": 1}
    r = client.post("/api/renders", json=create | {"crop_config": crop, "caption": "hi"})
    assert r.status_code == 201, r.text
    rendered = r.json()
    assert rendered["status"] == "PENDING" and rendered["overlay_config"] == brand["default_overlay_config"]
    assert job(db, "render", render_id=rendered["id"])
    render(rendered["id"])
    rendered = client.get(f"/api/renders/{rendered['id']}").json()
    assert rendered["status"] == "READY", rendered["ffmpeg_log"]
    assert rendered["output_url"] == f"/media/renders/{rendered['id']}.mp4" and rendered["ffmpeg_log"]
    assert abs(rendered["duration_s"] - 4) < 0.1 and rendered["size_bytes"] > 0
    assert [x["id"] for x in client.get(f"/api/renders?clip_id={clip['id']}&status=READY&unscheduled=true").json()] == [
        rendered["id"]
    ]

    assert client.delete(f"/api/clips/{clip['id']}").status_code == 409  # a render uses it
    assert client.delete(f"/api/renders/{rendered['id']}").status_code == 204
    assert not (data_dir / f"renders/{rendered['id']}.mp4").exists()
    assert client.delete(f"/api/clips/{clip['id']}").status_code == 204
    assert not (data_dir / f"raw/{clip['id']}.mp4").exists()
    assert client.get(f"/api/clips/{clip['id']}").status_code == 404


@needs_ffmpeg
def test_render_failures_are_final(client, data_dir, media, monkeypatch):
    clip = upload(client, media["land"]).json()
    probe_clip(clip["id"])
    create = {"clip_id": clip["id"]}  # no brand: no logo

    monkeypatch.setattr(media_tasks, "MAX_OUTPUT_BYTES", 1000)
    big = client.post("/api/renders", json=create).json()
    render(big["id"])
    big = client.get(f"/api/renders/{big['id']}").json()
    assert (big["status"], big["error_code"], big["output_url"]) == ("FAILED", "OUTPUT_TOO_LARGE", None)
    assert not (data_dir / f"renders/{big['id']}.mp4").exists()

    (data_dir / f"raw/{clip['id']}.mp4").write_bytes(b"not a video")
    bad = client.post("/api/renders", json=create).json()
    render(bad["id"])
    bad = client.get(f"/api/renders/{bad['id']}").json()
    assert (bad["status"], bad["error_code"]) == ("FAILED", "FFMPEG_FAILED")
    assert "Invalid data found when processing input" in bad["ffmpeg_log"]
    assert not any((data_dir / "renders").glob("*"))  # no .part left behind
    assert client.post(f"/api/renders/{bad['id']}/retry").json()["status"] == "PENDING"
    assert client.post(f"/api/renders/{bad['id']}/retry").status_code == 409  # not FAILED any more


@needs_ffmpeg
def test_short_clip_rejected_then_retry(client, media):
    clip = upload(client, media["short"]).json()
    probe_clip(clip["id"])
    clip = client.get(f"/api/clips/{clip['id']}").json()
    assert (clip["status"], clip["error_code"]) == ("FAILED", "DURATION_OUT_OF_RANGE")
    assert clip["error_detail"].startswith("2.0 s")
    retried = client.post(f"/api/clips/{clip['id']}/retry").json()
    assert (retried["status"], retried["error_code"]) == ("PROBING", None)


def test_from_url_defers_download(client, db):
    assert client.post("/api/clips/from-url", json={"url": "notaurl", "rights_status": "none"}).status_code == 422
    r = client.post("/api/clips/from-url", json={"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "rights_status": "none"})
    assert r.status_code == 201
    clip = r.json()
    assert (clip["status"], clip["origin"], clip["source_url"]) == ("DOWNLOADING", "url", "https://www.youtube.com/watch?v=jNQXAC9IVRw")
    assert job(db, "download_clip", clip_id=clip["id"])
    assert client.delete(f"/api/clips/{clip['id']}").status_code == 409  # still downloading


@needs_ffmpeg
def test_download_clip(client, db, data_dir, media, monkeypatch, tmp_path):
    """yt-dlp faked at the subprocess boundary: cookies stay out of DATA_DIR, an operator handle PATCHed
    mid-download wins, and an exit 0 with no file keeps the size hint instead of a stray warning."""
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(settings, "YTDLP_COOKIES_FILE", str(cookies))
    real_run, seen = subprocess.run, {}

    def fake_ytdlp(args, **kwargs):
        if args[0] != "yt-dlp":
            return real_run(args, **kwargs)
        jar = seen["cookies"] = Path(args[args.index("--cookies") + 1])
        assert jar.read_text() == cookies.read_text() and not jar.resolve().is_relative_to(data_dir.resolve())
        if seen.get("empty"):
            return subprocess.CompletedProcess(args, 0, "", "WARNING: [youtube] x: some format is missing\n")
        client.patch(f"/api/clips/{clip['id']}", json={"source_creator_handle": "@operator"})
        out = Path(args[args.index("-o") + 1].replace("%(ext)s", "mp4"))
        shutil.copy(media["land"], out)
        info = {"filepath": str(out), "extractor_key": "Youtube", "uploader_id": "@uploader", "webpage_url": "https://y/w"}
        return subprocess.CompletedProcess(args, 0, json.dumps(info) + "\n", "")

    monkeypatch.setattr(media_tasks.subprocess, "run", fake_ytdlp)
    new = {"url": "https://www.youtube.com/watch?v=x", "rights_status": "none"}
    clip = client.post("/api/clips/from-url", json=new).json()
    download_clip(clip["id"])
    clip = client.get(f"/api/clips/{clip['id']}").json()
    assert (clip["status"], clip["platform"], clip["source_url"]) == ("READY", "Youtube", "https://y/w"), clip
    assert clip["source_creator_handle"] == "@operator"
    assert not seen["cookies"].exists() and not (data_dir / f"raw/dl-{clip['id']}").exists()

    seen["empty"] = True
    clip = client.post("/api/clips/from-url", json=new).json()
    download_clip(clip["id"])
    clip = client.get(f"/api/clips/{clip['id']}").json()
    assert (clip["status"], clip["error_code"]) == ("FAILED", "EXTRACTOR_FAILED")
    assert clip["error_detail"] == "yt-dlp downloaded nothing (over MAX_UPLOAD_BYTES?)"


def test_cli_interrupt_fails_the_render(db, monkeypatch):
    """Ctrl-C during `python -m app.cli render` must not leave the render RENDERING with no job behind it."""
    with Session(db) as s:
        clip = SourceClip(origin="upload", status="READY", raw_key="raw/x.mp4")
        brand = Brand(name="CLI", logo_key="logos/x.png")
        s.add_all([clip, brand])
        s.commit()
        clip_id, brand_id = clip.id, brand.id

    def interrupted(render_id):
        with SyncSession() as s, s.begin():
            s.execute(cas(Render, render_id, ["PENDING"], status="RENDERING"))
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "render", interrupted)
    monkeypatch.setattr(sys, "argv", ["app.cli", "render", str(clip_id), str(brand_id)])
    with pytest.raises(KeyboardInterrupt):
        cli.main()
    with Session(db) as s:
        row = s.scalars(select(Render).where(Render.source_clip_id == clip_id)).one()
        assert (row.status, row.error_code) == ("FAILED", "INTERRUPTED")
    monkeypatch.setattr(sys, "argv", ["app.cli", "render", str(clip_id), str(brand_id), "--w", "2"])
    with pytest.raises(SystemExit, match="bad overlay: w: Input should be less than or equal to 1"):
        cli.main()


def test_api_startup_fails_orphaned_uploads_and_drops_their_part_file(db, client, data_dir):
    """An UPLOADING row when the api starts lost its request (one api process): FAILED, .part removed."""
    with Session(db) as s:
        clip = SourceClip(origin="upload", status="UPLOADING", original_filename="half.mp4")
        s.add(clip)
        s.commit()
        cid = clip.id
    (data_dir / "raw").mkdir(exist_ok=True)
    part = data_dir / "raw" / f"{cid}.part"
    part.write_bytes(b"half an upload")
    from app.main import _abandon_orphan_uploads  # what the lifespan runs at startup

    client.portal.call(_abandon_orphan_uploads)  # on the client's loop, which owns the pooled connections
    with Session(db) as s:
        clip = s.get(SourceClip, cid)
        assert (clip.status, clip.error_code) == ("FAILED", "UPLOAD_ABANDONED")
    assert not part.exists()
