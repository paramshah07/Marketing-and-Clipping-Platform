import asyncio

from app.tasks.queue import app, ping, retry_stalled_jobs


def test_retry_stalled_jobs(db):
    ids, got = asyncio.run(stalled_scenario())
    assert got == {
        ids["dead_worker"]: ("todo", 1),  # re-queued on the same row
        ids["poison"]: ("failed", 4),  # already ran 3 times: stop looping
        ids["superseded"]: ("failed", 1),  # a newer 'todo' job holds its queueing_lock
        ids["replacement"]: ("todo", 0),
        ids["live_worker"]: ("doing", 0),  # its worker is still heartbeating: leave it alone
    }


async def stalled_scenario() -> tuple[dict, dict]:
    sql = app.connector
    async with app.open_async():
        ids = {name: await ping.defer_async() for name in ("dead_worker", "poison", "live_worker")}
        ids["superseded"] = await ping.configure(queueing_lock="lock-a").defer_async()
        # what a SIGKILLed worker leaves behind: 'doing' jobs whose worker row is gone (worker_id NULL)
        await sql.execute_query_async(
            "UPDATE procrastinate_jobs SET status = 'doing' WHERE id = ANY(%(ids)s)", ids=list(ids.values())
        )
        await sql.execute_query_async("UPDATE procrastinate_jobs SET attempts = 3 WHERE id = %(id)s", id=ids["poison"])
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
