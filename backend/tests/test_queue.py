import asyncio

from sqlalchemy.orm import Session

from app.models import Render, SourceClip
from app.tasks.media import probe_clip, render
from app.tasks.queue import app, ping, retry_stalled_jobs


def test_retry_stalled_jobs(db):
    with Session(db) as s:
        clip = SourceClip(origin="upload", status="READY")
        s.add(clip)
        s.flush()
        row = Render(source_clip_id=clip.id, status="RENDERING")
        probing = SourceClip(origin="upload", status="PROBING")
        s.add_all([row, probing])
        s.commit()
        render_id, clip_id = row.id, probing.id
    ids, got = asyncio.run(stalled_scenario(render_id, clip_id))
    assert got == {
        ids["dead_worker"]: ("todo", 1),  # re-queued on the same row
        ids["poison"]: ("failed", 4),  # already ran 3 times: stop looping
        ids["poison_render"]: ("failed", 4),
        ids["poison_probe"]: ("failed", 4),
        ids["superseded"]: ("failed", 1),  # a newer 'todo' job holds its queueing_lock
        ids["replacement"]: ("todo", 0),
        ids["live_worker"]: ("doing", 0),  # its worker is still heartbeating: leave it alone
    }
    with Session(db) as s:  # giving up on a render or probe job fails its row too
        row = s.get(Render, render_id)
        assert (row.status, row.error_code, row.completed_at is not None) == ("FAILED", "WORKER_CRASHED", True)
        clip = s.get(SourceClip, clip_id)
        assert (clip.status, clip.error_code) == ("FAILED", "WORKER_CRASHED")


async def stalled_scenario(render_id: int, clip_id: int) -> tuple[dict, dict]:
    sql = app.connector
    async with app.open_async():
        ids = {name: await ping.defer_async() for name in ("dead_worker", "poison", "live_worker")}
        ids["poison_render"] = await render.defer_async(render_id=render_id)
        ids["poison_probe"] = await probe_clip.defer_async(clip_id=clip_id)
        ids["superseded"] = await ping.configure(queueing_lock="lock-a").defer_async()
        # what a SIGKILLed worker leaves behind: 'doing' jobs whose worker row is gone (worker_id NULL)
        await sql.execute_query_async(
            "UPDATE procrastinate_jobs SET status = 'doing' WHERE id = ANY(%(ids)s)", ids=list(ids.values())
        )
        await sql.execute_query_async(
            "UPDATE procrastinate_jobs SET attempts = 3 WHERE id = ANY(%(ids)s)",
            ids=[ids["poison"], ids["poison_render"], ids["poison_probe"]],
        )
        live = await app.job_manager.register_worker()
        await sql.execute_query_async(
            "UPDATE procrastinate_jobs SET worker_id = %(w)s WHERE id = %(id)s", w=live, id=ids["live_worker"]
        )
        ids["replacement"] = await ping.configure(queueing_lock="lock-a").defer_async()

        await retry_stalled_jobs(timestamp=0)

        rows = await sql.execute_query_all_async(
            "SELECT id, status::text, attempts FROM procrastinate_jobs WHERE id = ANY(%(ids)s)", ids=list(ids.values())
        )
    return ids, {r["id"]: (r["status"], r["attempts"]) for r in rows}
