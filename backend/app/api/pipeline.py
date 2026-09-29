"""Clips, brands, renders and the saved captions / covers (docs/PLAN.md section 5). Status changes are
compare-and-set (0 rows -> 409) and each job is deferred in the same transaction as its row change."""

import io
import mimetypes
import secrets
import struct
from pathlib import Path, PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, UploadFile
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.auth import Db, current_user
from app.core.config import settings
from app.models import Brand, Post, Render, SavedCaption, SavedCover, SourceClip, cas
from app.schemas import (
    BrandCreate,
    BrandOut,
    BrandPatch,
    CaptionCreate,
    CaptionOut,
    CaptionPatch,
    ClipFromUrl,
    ClipOut,
    ClipPatch,
    ClipsFromUrls,
    ClipsFromUrlsOut,
    CoverOut,
    CoverPatch,
    FoundLink,
    LinksOut,
    RenderCreate,
    RenderDetail,
    RenderOut,
)
from app.services import links, storage
from app.tasks.media import download_clip, probe_clip, render

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
MAX_LOGO_BYTES = 10 * 1024**2
MAX_DOCUMENT_BYTES = 20 * 1024**2
MAX_COVER_BYTES = 8 * 1024**2  # Instagram's image limit (Zernio: Instagram media requirements)
# A list import must not starve the rest: its downloads queue behind renders and single imports (priority)
# and run two at a time (lanes), which also keeps the sites' rate limits further away.
BULK_PRIORITY, BULK_LANES = -10, 2


async def _defer(s: AsyncSession, task, options: dict | None = None, **kwargs) -> None:
    """Queue a job inside the session's transaction: it exists iff the row change commits."""
    raw = await (await s.connection()).get_raw_connection()
    await task.configure(connection=raw.driver_connection, **(options or {})).defer_async(**kwargs)


async def _get(s: AsyncSession, model, id: int):
    if (row := await s.get(model, id)) is None:
        raise HTTPException(404, f"{model.__tablename__} {id} not found")
    return row


async def _cas(s: AsyncSession, model, id: int, from_statuses: list[str], **values) -> None:
    await _get(s, model, id)
    if (await s.execute(cas(model, id, from_statuses, **values))).rowcount != 1:
        raise HTTPException(409, f"{model.__tablename__} {id} is not {' or '.join(from_statuses)}")


async def _clear_default(s: AsyncSession, model, keep: int | None = None) -> None:
    """Before a row becomes its table's default: clear the old default (other than keep), same transaction.
    Call it before setting the flag, or autoflush writes the flag first and trips uq_<table>_default."""
    await s.execute(update(model).where(model.is_default, model.id.is_distinct_from(keep)).values(is_default=False))


async def _commit(s: AsyncSession) -> None:
    """Commit a default change. uq_<table>_default is these tables' only unique index, so an IntegrityError
    means another request set a default between our clear and this commit."""
    try:
        await s.commit()
    except IntegrityError:
        raise HTTPException(409, "another default was set at the same moment: try again") from None


# ---------------------------------------------------------------- clips

async def _stream_multipart(request: Request, dest: Path) -> tuple[dict[str, str], str | None]:
    """Parse a multipart body as it arrives. The `file` part streams straight into dest (never into
    memory or /tmp); the other parts are short text fields. Returns (fields, filename)."""
    ctype, params = parse_options_header(request.headers.get("content-type"))
    if ctype != b"multipart/form-data" or b"boundary" not in params:
        raise HTTPException(415, "expected multipart/form-data")
    fields: dict[str, str] = {}
    st = {"filename": None, "size": 0, "complete": False}

    def on_part_begin():
        st.update(header=b"", value=b"", disposition=b"", data=bytearray())

    def on_header_field(data, start, end):
        st["header"] += data[start:end]

    def on_header_value(data, start, end):
        st["value"] += data[start:end]

    def on_header_end():
        if st["header"].lower() == b"content-disposition":
            st["disposition"] = st["value"]
        st["header"] = st["value"] = b""

    def on_headers_finished():
        _, opts = parse_options_header(st["disposition"])
        st["name"] = opts.get(b"name", b"").decode()
        if st["name"] == "file":
            if st["filename"] is not None:
                raise HTTPException(422, "one file per upload")
            st["filename"] = opts.get(b"filename", b"").decode("utf-8", "replace")
            if PurePath(st["filename"]).suffix.lower() not in VIDEO_EXTENSIONS:
                raise HTTPException(415, f"file must be one of {', '.join(sorted(VIDEO_EXTENSIONS))}")

    def on_part_data(data, start, end):
        if st["name"] == "file":
            st["size"] += end - start
            if st["size"] > settings.MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"file is larger than {settings.MAX_UPLOAD_BYTES} bytes")
            out.write(data[start:end])
        else:
            st["data"] += data[start:end]
            if len(st["data"]) > 10_000:
                raise HTTPException(422, f"form field {st['name']} is too long")

    def on_part_end():
        if st["name"] != "file":
            fields[st["name"]] = st["data"].decode()

    callbacks = {
        "on_part_begin": on_part_begin,
        "on_header_field": on_header_field,
        "on_header_value": on_header_value,
        "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished,
        "on_part_data": on_part_data,
        "on_part_end": on_part_end,
        "on_end": lambda: st.update(complete=True),  # the closing boundary arrived
    }
    parser = MultipartParser(params[b"boundary"], callbacks)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dest.open("wb") as out:  # on_part_data writes here
            async for chunk in request.stream():
                await run_in_threadpool(parser.write, chunk)  # callbacks write to disk: keep it off the loop
    except UnicodeDecodeError:
        raise HTTPException(422, "form field names and values must be UTF-8") from None
    if not st["complete"]:  # finalize() doesn't check this: a cut-off body would pass as a short file
        raise HTTPException(400, "incomplete multipart body")
    return fields, st["filename"]


UPLOAD_BODY = {  # the handler parses the body itself (to stream it), so describe it for OpenAPI here
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary", "description": ".mp4, .mov or .webm"},
                        "source_creator_handle": {"type": "string"},
                    },
                }
            }
        },
    }
}


@router.post("/clips", status_code=201, openapi_extra=UPLOAD_BODY)
async def upload_clip(request: Request, s: Db) -> ClipOut:
    """Multipart upload, streamed to raw/. The clip is UPLOADING while the bytes arrive, then PROBING."""
    if int(request.headers.get("content-length") or 0) > settings.MAX_UPLOAD_BYTES + 1024**2:
        raise HTTPException(413, f"file is larger than {settings.MAX_UPLOAD_BYTES} bytes")
    clip = SourceClip(origin="upload", status="UPLOADING")
    s.add(clip)
    await s.commit()
    part = storage.path_for(f"raw/{clip.id}.part")
    try:
        fields, filename = await _stream_multipart(request, part)
        if not filename:
            raise HTTPException(422, "file is required")
        key = f"raw/{clip.id}{PurePath(filename).suffix.lower()}"
        part.replace(storage.path_for(key))
    except Exception:  # bad request or client gone: leave no trace
        await run_in_threadpool(part.unlink, missing_ok=True)
        await s.delete(clip)
        await s.commit()
        raise
    await _cas(
        s, SourceClip, clip.id, ["UPLOADING"],
        status="PROBING", raw_key=key, original_filename=filename, content_type=mimetypes.guess_type(key)[0],
        source_creator_handle=fields.get("source_creator_handle") or None,
        uploaded_at=func.now(),
    )  # fmt: skip
    await _defer(s, probe_clip, clip_id=clip.id)
    await s.commit()
    await s.refresh(clip)
    return clip


@router.post("/clips/from-url", status_code=201)
async def create_clip_from_url(body: ClipFromUrl, s: Db) -> ClipOut:
    clip = SourceClip(
        origin="url",
        status="DOWNLOADING",
        source_url=str(body.url),
        source_creator_handle=body.source_creator_handle,
    )
    s.add(clip)
    await s.flush()
    await _defer(s, download_clip, clip_id=clip.id)
    await s.commit()
    await s.refresh(clip)
    return clip


async def _library_keys(s: AsyncSession) -> set[str]:
    urls = await s.scalars(select(SourceClip.source_url).where(SourceClip.source_url.is_not(None)))
    return {links.key(u) for u in urls}


@router.post("/clips/links")
async def find_links(file: UploadFile, s: Db) -> LinksOut:
    """The video links in a document (docx, xlsx, pptx, odt) or a text file. Nothing is imported."""
    data = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise HTTPException(413, f"document is larger than {MAX_DOCUMENT_BYTES // 1024**2} MB")
    found, repeats, other = await run_in_threadpool(lambda: links.find(*links.read(data)))
    have = await _library_keys(s)
    return LinksOut(
        links=[FoundLink(url=url, platform=platform, in_library=k in have) for platform, k, url in found],
        repeats=repeats,
        other=other[:20],
        other_count=len(other),
    )


@router.post("/clips/from-urls", status_code=201)
async def create_clips_from_urls(body: ClipsFromUrls, s: Db) -> ClipsFromUrlsOut:
    """One DOWNLOADING clip per video that isn't in the library yet."""
    have, ids = await _library_keys(s), []
    for url in dict.fromkeys(links.clean(str(u)) for u in body.urls):
        if (k := links.key(url)) in have:
            continue
        have.add(k)
        clip = SourceClip(origin="url", status="DOWNLOADING", source_url=url)
        s.add(clip)
        await s.flush()
        options = {"priority": BULK_PRIORITY, "lock": f"import-{clip.id % BULK_LANES}"}
        await _defer(s, download_clip, options, clip_id=clip.id)
        ids.append(clip.id)
    await s.commit()
    return ClipsFromUrlsOut(created=len(ids), skipped=len(body.urls) - len(ids), ids=ids)


@router.get("/clips")
async def list_clips(s: Db) -> list[ClipOut]:
    return (await s.scalars(select(SourceClip).order_by(SourceClip.created_at.desc(), SourceClip.id.desc()))).all()


@router.get("/clips/{clip_id}")
async def get_clip(clip_id: int, s: Db) -> ClipOut:
    return await _get(s, SourceClip, clip_id)


@router.patch("/clips/{clip_id}")
async def update_clip(clip_id: int, body: ClipPatch, s: Db) -> ClipOut:
    clip = await _get(s, SourceClip, clip_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(clip, k, v)
    await s.commit()
    return clip


@router.post("/clips/{clip_id}/retry")
async def retry_clip(clip_id: int, s: Db) -> ClipOut:
    """FAILED -> PROBING again if the bytes are here, else DOWNLOADING again (url clips)."""
    clip = await _get(s, SourceClip, clip_id)
    if clip.raw_key:
        status, task = "PROBING", probe_clip
    elif clip.origin == "url":
        status, task = "DOWNLOADING", download_clip
    else:
        raise HTTPException(409, "upload never finished: upload the file again")
    await _cas(s, SourceClip, clip_id, ["FAILED"], status=status, error_code=None, error_detail=None)
    await _defer(s, task, clip_id=clip_id)
    await s.commit()
    await s.refresh(clip)
    return clip


@router.delete("/clips/{clip_id}", status_code=204)
async def delete_clip(clip_id: int, s: Db) -> Response:
    """Only READY/FAILED clips with no renders (delete the renders first). Files go too."""
    clip = await _get(s, SourceClip, clip_id)
    deleted = await s.execute(
        delete(SourceClip).where(
            SourceClip.id == clip_id,
            SourceClip.status.in_(["READY", "FAILED"]),
            ~exists().where(Render.source_clip_id == clip_id),
        )
    )
    if deleted.rowcount != 1:
        raise HTTPException(409, "clip is still processing or has renders")
    await s.commit()
    for key in (clip.raw_key, clip.thumbnail_key, f"raw/{clip_id}.part"):  # .part: an upload that never finished
        if key:
            await run_in_threadpool(storage.delete, key)
    return Response(status_code=204)


# ---------------------------------------------------------------- brands

def png_alpha(data: bytes) -> bool | None:
    """None if data is not a PNG, else whether it has transparency: colour type 4/6 (grey/RGB + alpha)
    or a tRNS chunk before the image data. ValueError if the chunks stop before IDAT..IEND (truncated)."""
    if len(data) < 26 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    alpha, idat, pos = data[25] in (4, 6), False, 8
    while pos + 12 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        if pos + 12 + length > len(data):
            break  # chunk runs past the end
        alpha |= kind == b"tRNS" and not idat
        idat |= kind == b"IDAT"
        if kind == b"IEND" and idat:
            return alpha
        pos += length + 12  # length, type, data, crc
    raise ValueError("truncated PNG")  # ponytail: structure only; CRCs and the zlib stream are not checked


@router.get("/brands")
async def list_brands(s: Db, archived: bool = False) -> list[BrandOut]:
    q = select(Brand).where(Brand.archived_at.is_not(None) if archived else Brand.archived_at.is_(None))
    return (await s.scalars(q.order_by(Brand.name, Brand.id))).all()


@router.post("/brands", status_code=201)
async def create_brand(body: BrandCreate, s: Db) -> BrandOut:
    values = body.model_dump(exclude_none=True)
    if body.is_default:
        await _clear_default(s, Brand)
    brand = Brand(**values)
    s.add(brand)
    await _commit(s)
    await s.refresh(brand)
    return brand


@router.patch("/brands/{brand_id}")
async def update_brand(brand_id: int, body: BrandPatch, s: Db) -> BrandOut:
    # FOR UPDATE: an archive racing a make-default must see the other's commit, never leave an archived default
    if (brand := await s.get(Brand, brand_id, with_for_update=True)) is None:
        raise HTTPException(404, f"brands {brand_id} not found")
    values = body.model_dump(exclude_unset=True)
    archived = values.get("archived", brand.archived_at is not None)
    if values.get("is_default"):
        if archived:
            raise HTTPException(409, "an archived brand can't be the default")
        await _clear_default(s, Brand, brand.id)
    elif archived:
        values["is_default"] = False  # archiving clears the default
    if "archived" in values:
        brand.archived_at = (brand.archived_at or func.now()) if values.pop("archived") else None
    for k, v in values.items():
        setattr(brand, k, v)
    await _commit(s)
    await s.refresh(brand)
    return brand


@router.post("/brands/{brand_id}/logo")
async def upload_brand_logo(brand_id: int, file: UploadFile, s: Db) -> BrandOut:
    """A PNG with transparency. Stored under a new name each time, so browsers never show a stale logo."""
    brand = await _get(s, Brand, brand_id)
    data = await file.read(MAX_LOGO_BYTES + 1)
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(413, f"logo is larger than {MAX_LOGO_BYTES} bytes")
    try:
        alpha = png_alpha(data)
    except ValueError:
        raise HTTPException(422, "logo PNG is corrupt") from None
    if alpha is None:
        raise HTTPException(415, "logo must be a PNG")
    if not alpha:
        raise HTTPException(422, "logo PNG has no transparency (alpha channel)")
    old, brand.logo_key = brand.logo_key, f"logos/{brand.id}-{secrets.token_hex(4)}.png"
    await run_in_threadpool(storage.save, brand.logo_key, io.BytesIO(data))
    await s.commit()
    if old:
        await run_in_threadpool(storage.delete, old)
    return brand


# ---------------------------------------------------------------- renders

@router.post("/renders", status_code=201)
async def create_render(body: RenderCreate, s: Db) -> RenderOut:
    clip = await s.get(SourceClip, body.clip_id)
    if clip is None or clip.status != "READY":
        raise HTTPException(409, f"clip {body.clip_id} is not READY")
    overlay = None
    if body.brand_id is not None:
        brand = await s.get(Brand, body.brand_id)
        if brand is None or brand.archived_at or not brand.logo_key:
            raise HTTPException(409, f"brand {body.brand_id} is missing, archived or has no logo")
        overlay = body.overlay_config.model_dump() if body.overlay_config else brand.default_overlay_config
    r = Render(
        source_clip_id=clip.id,
        brand_id=body.brand_id,
        overlay_config=overlay,
        crop_config=body.crop_config and body.crop_config.model_dump(),
        caption=body.caption,
    )
    s.add(r)
    await s.flush()
    await _defer(s, render, render_id=r.id)
    await s.commit()
    await s.refresh(r)
    return r


@router.get("/renders")
async def list_renders(
    s: Db, clip_id: int | None = None, status: str | None = None, unscheduled: bool = False
) -> list[RenderOut]:
    """unscheduled: renders with no post other than CANCELLED ones, and not superseded by a re-render."""
    q = select(Render).order_by(Render.created_at.desc(), Render.id.desc())
    if clip_id is not None:
        q = q.where(Render.source_clip_id == clip_id)
    if status:
        q = q.where(Render.status == status)
    if unscheduled:
        q = q.where(~exists().where(Post.render_id == Render.id, Post.status != "CANCELLED"))
        q = q.where(Render.superseded_at.is_(None))
    return (await s.scalars(q)).all()


@router.get("/renders/{render_id}")
async def get_render(render_id: int, s: Db) -> RenderDetail:
    return await _get(s, Render, render_id)


@router.post("/renders/{render_id}/retry")
async def retry_render(render_id: int, s: Db) -> RenderOut:
    await _cas(s, Render, render_id, ["FAILED"], status="PENDING", error_code=None, completed_at=None)
    await _defer(s, render, render_id=render_id)
    await s.commit()
    return await s.get(Render, render_id, populate_existing=True)


@router.delete("/renders/{render_id}", status_code=204)
async def delete_render(render_id: int, s: Db) -> Response:
    """Not while RENDERING, and not while a live (non-CANCELLED) post refers to it. Its CANCELLED posts
    and files go too. FOR UPDATE: a cover change in flight lands first, so its file is the one deleted."""
    if (r := await s.get(Render, render_id, with_for_update=True)) is None:
        raise HTTPException(404, f"renders {render_id} not found")
    await s.execute(delete(Post).where(Post.render_id == render_id, Post.status == "CANCELLED"))  # FK RESTRICT
    deleted = await s.execute(
        delete(Render).where(
            Render.id == render_id,
            Render.status.in_(["PENDING", "READY", "FAILED"]),
            ~exists().where(Post.render_id == render_id),
        )
    )
    if deleted.rowcount != 1:
        raise HTTPException(409, "render is RENDERING or a live post refers to it")  # no commit: cancelled posts stay
    await s.commit()
    for key in (r.output_key, r.thumbnail_key, r.cover_key):
        if key:
            await run_in_threadpool(storage.delete, key)
    return Response(status_code=204)


async def _cover_jpeg(file: UploadFile) -> bytes:
    data = await file.read(MAX_COVER_BYTES + 1)
    if len(data) > MAX_COVER_BYTES:
        raise HTTPException(413, f"cover is larger than {MAX_COVER_BYTES // 1024**2} MB (Instagram's image limit)")
    if not data.startswith(b"\xff\xd8\xff"):
        raise HTTPException(415, "cover must be a JPEG")
    return data


async def _set_cover(s: AsyncSession, render_id: int, key: str | None) -> Render:
    """Point the render at a new cover file (or none) and delete the old one. Only while no post other than a
    CANCELLED one refers to the render: a post uploads the cover once, with the video, so it never changes under
    one. FOR UPDATE serialises cover changes and holds off new posts (their FK check locks this row)."""
    try:
        r = await s.get(Render, render_id, with_for_update=True)
        if r is None:
            raise HTTPException(404, f"renders {render_id} not found")
        if await s.scalar(select(exists().where(Post.render_id == render_id, Post.status != "CANCELLED"))):
            raise HTTPException(409, "the render has posts: cancel them to change its cover")
    except HTTPException:
        if key:
            await run_in_threadpool(storage.delete, key)
        raise
    old, r.cover_key = r.cover_key, key
    await s.commit()
    if old:
        await run_in_threadpool(storage.delete, old)
    await s.refresh(r)  # updated_at is set by the database
    return r


@router.put("/renders/{render_id}/cover")
async def set_render_cover(render_id: int, file: UploadFile, s: Db) -> RenderOut:
    """The Reel cover (Zernio instagramThumbnail): a JPEG, ideally 1080x1920 (the Editor sends exactly that).
    Stored under a new name each time, so browsers never show a stale cover. 409 once the render has posts."""
    data = await _cover_jpeg(file)
    key = f"covers/{render_id}-{secrets.token_hex(4)}.jpg"
    await run_in_threadpool(storage.save, key, io.BytesIO(data))
    return await _set_cover(s, render_id, key)


@router.delete("/renders/{render_id}/cover")
async def delete_render_cover(render_id: int, s: Db) -> RenderOut:
    """Back to Instagram's own pick. 409 once the render has posts."""
    return await _set_cover(s, render_id, None)


# ---------------------------------------------------------------- saved captions and covers (Customizations)

@router.get("/captions")
async def list_captions(s: Db) -> list[CaptionOut]:
    """The default first, then by name."""
    q = select(SavedCaption).order_by(SavedCaption.is_default.desc(), SavedCaption.name, SavedCaption.id)
    return (await s.scalars(q)).all()


@router.post("/captions", status_code=201)
async def create_caption(body: CaptionCreate, s: Db) -> CaptionOut:
    if body.is_default:
        await _clear_default(s, SavedCaption)
    caption = SavedCaption(**body.model_dump())
    s.add(caption)
    await _commit(s)
    await s.refresh(caption)
    return caption


@router.patch("/captions/{caption_id}")
async def update_caption(caption_id: int, body: CaptionPatch, s: Db) -> CaptionOut:
    caption = await _get(s, SavedCaption, caption_id)
    values = body.model_dump(exclude_unset=True)
    if values.get("is_default"):
        await _clear_default(s, SavedCaption, caption.id)
    for k, v in values.items():
        setattr(caption, k, v)
    await _commit(s)
    return caption


@router.delete("/captions/{caption_id}", status_code=204)
async def delete_caption(caption_id: int, s: Db) -> Response:
    await s.delete(await _get(s, SavedCaption, caption_id))
    await s.commit()
    return Response(status_code=204)


@router.get("/covers")
async def list_covers(s: Db) -> list[CoverOut]:
    """The default first, then the newest."""
    q = select(SavedCover).order_by(SavedCover.is_default.desc(), SavedCover.created_at.desc(), SavedCover.id.desc())
    return (await s.scalars(q)).all()


@router.post("/covers", status_code=201)
async def upload_cover(
    file: UploadFile,
    name: Annotated[str, Form(min_length=1, max_length=100)],
    s: Db,
    is_default: Annotated[bool, Form()] = False,
) -> CoverOut:
    """A JPEG up to 8 MB (the web app sends 1080x1920). The Editor uploads a copy as a render's cover, so
    renaming or deleting this one never changes a render."""
    data = await _cover_jpeg(file)
    cover = SavedCover(name=name, image_key="")
    s.add(cover)
    await s.flush()  # the key carries the id
    key = cover.image_key = f"cover-library/{cover.id}-{secrets.token_hex(4)}.jpg"
    if is_default:
        await _clear_default(s, SavedCover, cover.id)
        cover.is_default = True
    await run_in_threadpool(storage.save, key, io.BytesIO(data))
    try:
        await _commit(s)
    except Exception:  # no row, no file
        await run_in_threadpool(storage.delete, key)
        raise
    await s.refresh(cover)
    return cover


@router.patch("/covers/{cover_id}")
async def update_cover(cover_id: int, body: CoverPatch, s: Db) -> CoverOut:
    cover = await _get(s, SavedCover, cover_id)
    values = body.model_dump(exclude_unset=True)
    if values.get("is_default"):
        await _clear_default(s, SavedCover, cover.id)
    for k, v in values.items():
        setattr(cover, k, v)
    await _commit(s)
    return cover


@router.delete("/covers/{cover_id}", status_code=204)
async def delete_cover(cover_id: int, s: Db) -> Response:
    """The file goes too (after the commit). Renders keep their own copies."""
    cover = await _get(s, SavedCover, cover_id)
    await s.delete(cover)
    await s.commit()
    await run_in_threadpool(storage.delete, cover.image_key)
    return Response(status_code=204)
