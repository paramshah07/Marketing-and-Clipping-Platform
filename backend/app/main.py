import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DataError, SQLAlchemyError

from app.api import pipeline, recovery, scheduling
from app.core.config import settings
from app.core.db import SessionLocal
from app.tasks.queue import app as queue_app

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with queue_app.open_async():  # lets the api defer jobs
        yield


# route.name as the operation id gives the generated TS client clean function names
app = FastAPI(title="Clipper", lifespan=lifespan, generate_unique_id_function=lambda route: route.name)
app.add_middleware(
    CORSMiddleware, allow_origins=[settings.APP_BASE_URL], allow_methods=["*"], allow_headers=["*"]
)
app.mount("/media", StaticFiles(directory=settings.DATA_DIR), name="media")
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
    worker_alive: bool  # a worker heartbeat within the last 30 s
    worker_last_heartbeat: datetime | None
    jobs: dict[str, int]  # procrastinate job counts by status
    failed_posts: int = 0  # FAILED + DEAD_LETTER posts (sidebar badge)
    rendering_renders: int = 0  # PENDING + RENDERING renders (sidebar footer)
    scheduled_posts: int = 0  # SCHEDULED posts (sidebar footer)


@app.get("/api/health")
async def health() -> Health:
    return Health(status="ok")


@app.get("/api/status")
async def status() -> SystemStatus:
    try:
        async with SessionLocal() as s:
            last, alive = (
                await s.execute(
                    text(
                        "SELECT max(last_heartbeat), coalesce(max(last_heartbeat) > now() - interval '30 seconds', false)"
                        " FROM procrastinate_workers"
                    )
                )
            ).one()
            jobs = await s.execute(text("SELECT status::text, count(*) FROM procrastinate_jobs GROUP BY status"))
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
                db=True, worker_alive=alive, worker_last_heartbeat=last, jobs=dict(jobs.all()), failed_posts=failed,
                rendering_renders=rendering, scheduled_posts=scheduled,
            )  # fmt: skip
    except SQLAlchemyError:
        logger.exception("status query failed")
        return SystemStatus(db=False, worker_alive=False, worker_last_heartbeat=None, jobs={})
