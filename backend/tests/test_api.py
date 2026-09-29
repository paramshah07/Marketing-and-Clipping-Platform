"""Clips / brands / renders API against the test database. Jobs are deferred for real (checked in
procrastinate_jobs) and then run inline, since no worker listens on clipper_test."""

import io
import json
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import cli
from app.api.pipeline import png_alpha
from app.core.config import settings
from app.core.db import SyncSession, engine
from app.main import SPA, app
from app.models import Brand, Render, SavedCaption, SourceClip, cas
from app.services import links
from app.tasks import media as media_tasks
from app.tasks.media import download_clip, probe_clip, render
from conftest import as_user, needs_ffmpeg

PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff\xe0" + bytes(100)  # the magic bytes are all the api checks


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + b"\0\0\0\0"  # CRC unchecked


def png(color_type: int, *chunks: bytes) -> bytes:
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, color_type, 0, 0, 0))
    return PNG + ihdr + b"".join(chunks) + chunk(b"IDAT", b"") + chunk(b"IEND", b"")


@pytest.fixture(scope="module")
def client(db):
    with TestClient(app, headers=as_user()) as c:
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
        return client.post("/api/clips", files={"file": (name or path.name, f)}, data=fields)


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


def defaults(client, path: str) -> list[int]:
    return [x["id"] for x in client.get(path).json() if x["is_default"]]


def test_brand_default(client):
    """One default brand at most: setting one clears the last, archiving clears it, an archived one can't be it."""
    a = client.post("/api/brands", json={"name": "Default A", "is_default": True}).json()
    b = client.post("/api/brands", json={"name": "Default B"}).json()
    assert (a["is_default"], b["is_default"], defaults(client, "/api/brands")) == (True, False, [a["id"]])
    url = f"/api/brands/{b['id']}"
    assert client.patch(url, json={"is_default": True}).json()["is_default"] and defaults(client, "/api/brands") == [b["id"]]
    client.patch(url, json={"is_default": True, "name": "Default B2"})  # already the default: stays it
    assert defaults(client, "/api/brands") == [b["id"]]
    assert client.patch(url, json={"archived": True}).json()["is_default"] is False
    assert defaults(client, "/api/brands") + defaults(client, "/api/brands?archived=true") == []
    assert client.patch(url, json={"is_default": True}).status_code == 409
    assert client.patch(f"/api/brands/{a['id']}", json={"archived": True, "is_default": True}).status_code == 409
    back = client.patch(url, json={"archived": False, "is_default": True}).json()
    assert (back["archived_at"], back["is_default"], defaults(client, "/api/brands")) == (None, True, [b["id"]])
    assert client.patch(url, json={"is_default": False}).json()["is_default"] is False
    assert defaults(client, "/api/brands") == []


def test_saved_captions(client, db):
    assert client.post("/api/captions", json={"name": "", "text": "x"}).status_code == 422
    assert client.post("/api/captions", json={"name": "Long", "text": "x" * 2201}).status_code == 422
    r = client.post("/api/captions", json={"name": "b plain", "text": "via {creator} {link}"})
    assert r.status_code == 201 and r.json()["is_default"] is False
    a = r.json()["id"]
    b = client.post("/api/captions", json={"name": "a promo", "text": "#ad", "is_default": True}).json()["id"]
    c = client.post("/api/captions", json={"name": "c empty", "text": "", "is_default": True}).json()["id"]
    ids = [c, b, a]  # the default first, then by name
    assert [x["id"] for x in client.get("/api/captions").json() if x["id"] in ids] == ids
    assert defaults(client, "/api/captions") == [c]
    r = client.patch(f"/api/captions/{a}", json={"text": "new", "is_default": True}).json()
    assert (r["name"], r["text"], r["is_default"], defaults(client, "/api/captions")) == ("b plain", "new", True, [a])
    assert client.patch(f"/api/captions/{a}", json={"name": ""}).status_code == 422
    assert client.patch(f"/api/captions/{a}", json={"is_default": False}).json()["is_default"] is False
    assert defaults(client, "/api/captions") == []
    assert client.patch("/api/captions/999999", json={"name": "x"}).status_code == 404
    assert client.delete(f"/api/captions/{b}").status_code == 204
    assert client.delete(f"/api/captions/{b}").status_code == 404
    with Session(db) as s, pytest.raises(IntegrityError, match="uq_saved_captions_default"):
        s.execute(update(SavedCaption).where(SavedCaption.id.in_([a, c])).values(is_default=True))


def test_default_set_meanwhile_is_a_409(client, db):
    """Another transaction sets a default between the request's clear and its commit: the index makes it a 409."""
    a, b = (client.post("/api/captions", json={"name": n, "text": ""}).json()["id"] for n in ("race a", "race b"))
    waiting = text("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND datname = current_database()")
    with ThreadPoolExecutor() as pool, Session(db) as s, db.connect() as watch:  # s ends first: the request can finish
        s.execute(update(SavedCaption).where(SavedCaption.is_default).values(is_default=False))
        s.execute(update(SavedCaption).where(SavedCaption.id == a).values(is_default=True))  # not committed yet
        pending = pool.submit(client.patch, f"/api/captions/{b}", json={"is_default": True})
        for _ in range(100):  # until the request waits on a's index entry
            if watch.execute(waiting).scalar():
                break
            time.sleep(0.05)
        s.commit()
        r = pending.result()
    assert (r.status_code, defaults(client, "/api/captions")) == (409, [a])


def test_saved_covers(client, data_dir):
    def up(name="Summer", data=JPEG, **form):
        return client.post("/api/covers", files={"file": ("summer.jpg", data)}, data={"name": name, **form})

    def files():
        return sorted(p.name for p in data_dir.glob("cover-library/*"))

    assert up(data=PNG).status_code == 415
    assert up(data=JPEG + bytes(8 * 1024**2)).status_code == 413
    assert up(name="").status_code == 422
    assert client.post("/api/covers", files={"file": ("a.jpg", JPEG)}).status_code == 422  # no name
    assert files() == []  # refused uploads leave no file
    r = up()
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["name"], a["is_default"], "image_key" in a) == ("Summer", False, False)
    assert a["image_url"].startswith(f"/media/cover-library/{a['id']}-") and a["image_url"].endswith(".jpg")
    assert (data_dir / a["image_url"].removeprefix("/media/")).read_bytes() == JPEG
    b = up("Winter", is_default="true").json()
    c = up("Autumn").json()
    ids = [b["id"], c["id"], a["id"]]  # the default first, then the newest
    assert b["is_default"] and [x["id"] for x in client.get("/api/covers").json() if x["id"] in ids] == ids
    r = client.patch(f"/api/covers/{a['id']}", json={"name": "Spring", "is_default": True}).json()
    assert (r["name"], r["is_default"], defaults(client, "/api/covers")) == ("Spring", True, [a["id"]])
    assert client.patch(f"/api/covers/{a['id']}", json={"name": ""}).status_code == 422
    assert client.delete(f"/api/covers/{a['id']}").status_code == 204
    assert client.delete(f"/api/covers/{a['id']}").status_code == 404
    assert len(files()) == 2 and a["image_url"].rsplit("/", 1)[1] not in files()  # its file went with it
    assert defaults(client, "/api/covers") == []


def test_upload_rejections_leave_nothing(client, data_dir, monkeypatch, tmp_path):
    before = len(client.get("/api/clips").json())
    fake = tmp_path / "x.mp4"
    fake.write_bytes(b"0" * 1000)
    assert upload(client, fake, name="x.avi").status_code == 415
    assert client.post("/api/clips", data={"source_creator_handle": "@x"}).status_code == 415  # not multipart
    bad_utf8 = client.post("/api/clips", files={"file": ("x.mp4", b"0" * 1000)},
                           data={"source_creator_handle": b"\xff\xfe@bad"})  # fmt: skip
    assert bad_utf8.status_code == 422, bad_utf8.text
    b = b"XyZ"  # a body cut off before its closing boundary is not a complete upload
    cut = (b"--XyZ\r\nContent-Disposition: form-data; name=\"source_creator_handle\"\r\n\r\n@x\r\n--XyZ\r\n"
           b'Content-Disposition: form-data; name="file"; filename="cut.mp4"\r\n\r\n' + b"0" * 1000)  # fmt: skip
    r = client.post("/api/clips", content=cut, headers={"content-type": f"multipart/form-data; boundary={b.decode()}"})
    assert (r.status_code, r.json()["detail"]) == (400, "incomplete multipart body")
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 500)
    assert upload(client, fake).status_code == 413
    assert len(client.get("/api/clips").json()) == before
    assert not any((data_dir / "raw").glob("*"))


@needs_ffmpeg
def test_upload_probe_render(client, db, data_dir, media):
    r = upload(client, media["land"], source_creator_handle="@me", rights_status="own_content")  # an old client's field: ignored
    assert r.status_code == 201, r.text
    clip = r.json()
    assert (clip["status"], clip["origin"], clip["original_filename"]) == ("PROBING", "upload", "land.mp4")
    assert (clip["source_creator_handle"], clip["content_type"]) == ("@me", "video/mp4") and "rights_status" not in clip
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
    assert client.post("/api/clips/from-url", json={"url": "notaurl"}).status_code == 422
    r = client.post("/api/clips/from-url", json={"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "rights_status": "none"})  # extra: ignored
    assert r.status_code == 201
    clip = r.json()
    assert (clip["status"], clip["origin"], clip["source_url"]) == ("DOWNLOADING", "url", "https://www.youtube.com/watch?v=jNQXAC9IVRw")
    assert job(db, "download_clip", clip_id=clip["id"])
    assert client.delete(f"/api/clips/{clip['id']}").status_code == 409  # still downloading


def docx(document: str, rels: str = "") -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">{document}</w:document>')
        z.writestr("word/_rels/document.xml.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
    return out.getvalue()


def test_find_links():
    run = lambda t: f"<w:r><w:t>{t}</w:t></w:r>"  # noqa: E731
    rel = lambda u: f'<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="{u}" TargetMode="External"/>'  # noqa: E731
    reel = "https://www.instagram.com/reel/DcG-xX0JJ17/"
    doc = docx(
        # a hyperlink (text + target), the same reel with another tracking query, a URL Word split over two runs,
        # two links with nothing between them, a field code, a profile (not a video)
        f"<w:p>{run(reel + '?igsi=aaa')}</w:p><w:p>{run(reel + '?igsi=bbb')}</w:p>"
        f"<w:p>{run('see https://youtu.be/jNQXAC')}{run('9IVRw.')}</w:p>"
        f"<w:p>{run('https://www.tiktok.com/@a/video/7612?_t=1https://x.com/a/status/99?s=20')}</w:p>"
        '<w:p><w:r><w:instrText> HYPERLINK "https://www.youtube.com/shorts/abcdefghijk" </w:instrText></w:r></w:p>'
        f"<w:p>{run('https://www.instagram.com/someone/')}</w:p>",
        rel(reel + "?igsi=aaa&amp;x=1") + rel(reel + "?igsi=bbb"),
    )
    found, repeats, other = links.find(*links.read(doc))
    assert [(p, u) for p, _, u in found] == [
        ("Instagram", reel),
        ("YouTube", "https://youtu.be/jNQXAC9IVRw"),
        ("TikTok", "https://www.tiktok.com/@a/video/7612"),
        ("X", "https://x.com/a/status/99"),
        ("YouTube", "https://www.youtube.com/shorts/abcdefghijk"),
    ]
    assert (repeats, other) == (1, ["https://www.instagram.com/someone/"])  # the namespaces are no links
    # one video, however it is linked
    same = ["https://youtu.be/jNQXAC9IVRw?si=x", "https://m.youtube.com/watch?v=jNQXAC9IVRw&t=3", "http://www.youtube.com/embed/jNQXAC9IVRw"]
    assert {links.key(u) for u in same} == {"YouTube:jNQXAC9IVRw"}
    assert links.key("https://vimeo.com/123/?x=1") == "https://vimeo.com/123"
    # text: utf-16 (Notepad), a markdown autolink, html (the href and its text are one link)
    assert links.find(*links.read("\ufeffhttps://vm.tiktok.com/ZM8abc/ ok".encode("utf-16")))[0][0][1] == "TikTok:ZM8abc"
    assert [u for *_, u in links.find(*links.read(b"- <https://fb.watch/abc123/>, and (https://x.com/a/status/5)."))[0]] == ["https://fb.watch/abc123/", "https://x.com/a/status/5"]
    page = f'<html><a href="{reel}?a=1&amp;b=2">{reel}</a><br><a href="{reel}">again</a></html>'.encode()
    assert links.find(*links.read(page))[:2] == ([("Instagram", "Instagram:DcG-xX0JJ17", reel)], 1)


def test_import_links(client, db):
    seen = client.post("/api/clips/from-url", json={"url": "https://www.youtube.com/watch?v=inlibrary01"}).json()
    pasted = b"https://youtu.be/inlibrary01?si=abc https://www.instagram.com/reel/AAA111/?igsi=x https://www.instagram.com/p/AAA111/ https://example.com/page"
    r = client.post("/api/clips/links", files={"file": ("pasted.txt", pasted)})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "links": [
            {"url": "https://youtu.be/inlibrary01", "platform": "YouTube", "in_library": True},
            {"url": "https://www.instagram.com/reel/AAA111/", "platform": "Instagram", "in_library": False},
        ],
        "repeats": 1,
        "other": ["https://example.com/page"],
        "other_count": 1,
    }
    assert client.post("/api/clips/links", files={"file": ("big.txt", b"x" * (20 * 1024**2 + 1))}).status_code == 413

    urls = [link["url"] for link in r.json()["links"]] + ["https://www.instagram.com/reel/AAA111/?igsi=again", "https://vimeo.com/77"]
    assert client.post("/api/clips/from-urls", json={"urls": []}).status_code == 422
    r = client.post("/api/clips/from-urls", json={"urls": urls})
    assert (r.status_code, {k: r.json()[k] for k in ("created", "skipped")}) == (201, {"created": 2, "skipped": 2})
    new = [c for c in client.get("/api/clips").json() if c["id"] > seen["id"]]
    assert sorted(r.json()["ids"]) == sorted(c["id"] for c in new)
    assert {(c["source_url"], c["status"], c["source_creator_handle"]) for c in new} == {
        ("https://www.instagram.com/reel/AAA111/", "DOWNLOADING", None),
        ("https://vimeo.com/77", "DOWNLOADING", None),
    }
    with db.connect() as c:  # behind renders and single imports, in one of two lanes
        jobs = c.execute(
            text("SELECT priority, lock FROM procrastinate_jobs WHERE task_name = 'download_clip' AND args @> CAST(:a AS jsonb)"),
            {"a": json.dumps({"clip_id": new[0]["id"]})},
        ).all()
    assert jobs == [(-10, f"import-{new[0]['id'] % 2}")]
    assert client.post("/api/clips/from-urls", json={"urls": urls}).json() == {"created": 0, "skipped": 4, "ids": []}


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
    new = {"url": "https://www.youtube.com/watch?v=x"}
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


def test_spa_fallback(tmp_path):
    """Production serves the built frontend (compose.prod.yml): app routes get index.html, files are files,
    and an unknown /api path stays a 404 instead of a 200 page."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("app")
    (tmp_path / "assets" / "a.js").write_text("js")
    c = TestClient(Starlette(routes=[Mount("/", SPA(directory=tmp_path, html=True))]))
    assert [c.get(u).text for u in ("/", "/editor/12", "/assets/a.js")] == ["app", "app", "js"]
    assert c.get("/api/nope").status_code == 404
