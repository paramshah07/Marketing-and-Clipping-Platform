"""The Procrastinate app (imported by the api to defer, run by the worker) and its tasks.

Always give tasks an explicit name= so a module move never strands queued jobs (TaskNotFound).
"""

import logging
import time

from procrastinate import App, PsycopgConnector
from procrastinate.exceptions import UniqueViolation
from procrastinate.jobs import Status

from app.core.config import settings

logger = logging.getLogger(__name__)

app = App(
    connector=PsycopgConnector(
        conninfo=settings.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1),
        min_size=1,
        max_size=5,  # worker concurrency 4 + 1
    )
)

STALLED_MAX_ATTEMPTS = 3


@app.task(name="ping")
def ping() -> None:
    logger.info("pong")


@app.task(name="debug_sleep")
def debug_sleep(seconds: int = 60) -> None:
    """Debug only: a long job to SIGKILL the worker under (kill-resume check, docs/phase-1.md)."""
    time.sleep(seconds)


@app.periodic(cron="* * * * *", periodic_id="retry_stalled_jobs")
@app.task(name="retry_stalled_jobs", queueing_lock="retry_stalled_jobs")
async def retry_stalled_jobs(timestamp: int) -> None:
    """A SIGKILLed worker leaves its jobs in 'doing' forever; put them back to 'todo' (same job id).

    retry_job bypasses the task's RetryStrategy, so cap it here: a job that keeps killing the
    worker would otherwise loop forever.
    """
    for job in await app.job_manager.get_stalled_jobs(seconds_since_heartbeat=30):
        if job.attempts >= STALLED_MAX_ATTEMPTS:
            logger.error("stalled job %s (%s) hit %s attempts, marking failed", job.id, job.task_name, job.attempts)
            await app.job_manager.finish_job(job, status=Status.FAILED, delete_job=False)
            continue
        try:
            await app.job_manager.retry_job(job)
            logger.warning("stalled job %s (%s) re-queued", job.id, job.task_name)
        except UniqueViolation:  # another 'todo' job holds the same queueing_lock and will do the work
            logger.warning("stalled job %s (%s) superseded by a queued job, marking failed", job.id, job.task_name)
            await app.job_manager.finish_job(job, status=Status.FAILED, delete_job=False)
    # Keep procrastinate_jobs bounded (this task alone adds 1440 rows/day); failed jobs are kept.
    await app.job_manager.delete_old_jobs(nb_hours=72)
