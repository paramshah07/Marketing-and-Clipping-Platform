import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DataError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import auth, bots, pipeline, recovery, scheduling
from app.core.config import settings
from app.core.db import SessionLocal
from app.services import storage
from app.tasks.queue import app as queue_app

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await _abandon_orphan_uploads()
    async with queue_app.open_async():  # lets the api defer jobs
        yield


async def _abandon_orphan_uploads() -> None:
    """Uploads stream through this one api process, so an UPLOADING row at startup lost its request (e.g. a
    --reload mid-upload). Fail it and drop its partial file instead of leaving a spinner for 24 h. Every user's:
    a SECURITY DEFINER function (migration 0007), as row-level security shows this session no one's rows."""
    try:
        async with SessionLocal() as s:
            rows = (await s.execute(text("SELECT clip_id, owner FROM abandon_orphan_uploads()"))).all()
            await s.commit()
        for i, owner in rows:  # an upload the api took before users had prefixes has none
            for key in (storage.key(owner, f"raw/{i}.part"), f"raw/{i}.part"):
                storage.path_for(key).unlink(missing_ok=True)
    except SQLAlchemyError:
        logger.exception("could not clean up orphaned uploads")


# route.name as the operation id gives the generated TS client clean function names. Production (STATIC_DIR)
# serves no /docs or /openapi.json: only health and sign-in answer without a user (dump_openapi.py still works).
app = FastAPI(
    title="Clipper", lifespan=lifespan, generate_unique_id_function=lambda route: route.name,
    openapi_url=None if settings.STATIC_DIR else "/openapi.json",
)  # fmt: skip
app.add_middleware(
    CORSMiddleware, allow_origins=[settings.APP_BASE_URL], allow_methods=["*"], allow_headers=["*"]
)
app.add_middleware(auth.CSRF)


class Media(StaticFiles):
    """DATA_DIR at /media, each file for its owner only (storage.owner: another user's is a 404, like a missing one).
    Range and ETag as StaticFiles does them. `path` is StaticFiles' normalised one: u/1/../2/x arrives as u/2/x."""

    async def get_response(self, path: str, scope):
        user = await auth.signed_in(Request(scope))
        if storage.owner(path) != user.id:
            raise StarletteHTTPException(404)
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "private"  # a shared cache must never hand it to someone else
        return response


app.mount("/media", Media(directory=settings.DATA_DIR), name="media")
app.include_router(auth.router)
app.include_router(bots.router)
app.include_router(bots.internal)
app.include_router(pipeline.router)
app.include_router(scheduling.router)
app.include_router(recovery.router)


@app.exception_handler(DataError)
async def data_error(_, e: DataError) -> JSONResponse:
    """A value Postgres can't hold, e.g. an id past int4 (/api/clips/2147483648): the input is bad, not the server."""
    return JSONResponse({"detail": str(e.orig).split("\n")[0]}, status_code=422)


class Health(BaseModel):
    status: str


class SystemStatus(BaseModel):
    db: bool
    worker_alive: bool  # the media worker (renders, downloads): a heartbeat within 30 s from a worker not the publisher
    worker_last_heartbeat: datetime | None
    publisher_alive: bool  # the publisher (dispatch, publishing, alerts): the same, from one that runs its jobs
    failed_posts: int = 0  # FAILED + DEAD_LETTER posts (sidebar badge)
    rendering_renders: int = 0  # PENDING + RENDERING renders (sidebar footer)
    scheduled_posts: int = 0  # SCHEDULED posts (sidebar footer)
    publishing_enabled: bool  # PUBLISHING_ENABLED and your Zernio key is valid: off, your SCHEDULED posts never go out
    # why it is off: the server's switch (PUBLISHING_ENABLED), or your key (none yet, or Zernio refused it)
    publishing_off: Literal["switch", "no_key", "key_invalid"] | None = None
    instagram_music: bool = False  # INSTAGRAM_CATALOG_MUSIC: posts can take music from Instagram's catalog


KEY_OFF = {"none": "no_key", "invalid": "key_invalid"}  # users.zernio_key_status -> SystemStatus.publishing_off
# procrastinate_workers has no queue column: a live worker is the publisher once it has run a default-queue job (its
# dispatch every minute), else the media worker. ponytail: a publisher younger than a minute counts as the media worker.
WORKERS = text(
    "SELECT (SELECT max(last_heartbeat) FROM procrastinate_workers),"
    " count(*) FILTER (WHERE NOT publisher) > 0, count(*) FILTER (WHERE publisher) > 0"
    " FROM (SELECT EXISTS (SELECT 1 FROM procrastinate_jobs j WHERE j.worker_id = w.id AND j.queue_name = 'default')"
    "  AS publisher FROM procrastinate_workers w WHERE w.last_heartbeat > now() - interval '30 seconds') alive"
)


@app.get("/api/health")
async def health() -> Health:
    return Health(status="ok")


@app.get("/api/status")
async def status(request: Request, response: Response) -> SystemStatus:
    """The signed-in user's counts (401 when signed out). Database down: db false, no sign-in needed to say so."""
    try:
        user = await auth.current_user(request, response)
        off = "switch" if not settings.PUBLISHING_ENABLED else KEY_OFF.get(user.zernio_key_status)
        async with SessionLocal(info={"uid": user.id}) as s:
            last, alive, publisher = (await s.execute(WORKERS)).one()
            failed, scheduled = (
                await s.execute(
                    text(
                        "SELECT count(*) FILTER (WHERE status IN ('FAILED', 'DEAD_LETTER')),"
                        " count(*) FILTER (WHERE status = 'SCHEDULED') FROM posts"
                    )
                )
            ).one()
            rendering = await s.scalar(text("SELECT count(*) FROM renders WHERE status IN ('PENDING', 'RENDERING')"))
            return SystemStatus(
                db=True, worker_alive=alive, worker_last_heartbeat=last, publisher_alive=publisher, failed_posts=failed,
                rendering_renders=rendering, scheduled_posts=scheduled, publishing_enabled=off is None,
                publishing_off=off, instagram_music=settings.INSTAGRAM_CATALOG_MUSIC,
            )  # fmt: skip
    except SQLAlchemyError:
        logger.exception("status query failed")
        return SystemStatus(db=False, worker_alive=False, worker_last_heartbeat=None, publisher_alive=False,
                            publishing_enabled=False)  # fmt: skip


class SPA(StaticFiles):
    """The built frontend. A path that isn't a file gets index.html, so /editor/12 survives a reload; /api/*
    stays a JSON 404."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404 or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


if settings.STATIC_DIR:  # last: a mount at / would otherwise shadow the routes declared after it
    app.mount("/", SPA(directory=settings.STATIC_DIR, html=True), name="web")
