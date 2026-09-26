# Phase 1: backend skeleton

What exists: compose stack (`postgres`, one-shot `migrate`, `api`, `worker`), one Dockerfile with
`api` and `worker` targets (the worker adds Debian trixie's ffmpeg 7.1.5), settings from PLAN §6,
SQLAlchemy models plus one Alembic migration for all five tables, the Procrastinate app with `ping`,
`debug_sleep` (debug only) and the `retry_stalled_jobs` sweeper, local-disk `storage.py`, Telegram
`notify()`, `GET /api/health` and `GET /api/status`, `/media` static files, and `backend/openapi.json`.

Run on 2026-09-26, macOS arm64, Docker Desktop, compose v2.40. Re-run from scratch after the review
fixes (restart policies, migrate connect retry, httpx log level, `# comment` settings, CORS from
`APP_BASE_URL`); the evidence below is from that re-run.

## a) From scratch: `docker compose down -v && docker compose up -d --build`

```
$ docker compose ps -a
SERVICE    STATUS                     PORTS
api        Up 8 seconds               127.0.0.1:8000->8000/tcp
migrate    Exited (0) 8 seconds ago
postgres   Up 11 seconds (healthy)    127.0.0.1:5432->5432/tcp
worker     Up 8 seconds

$ docker compose logs migrate
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, initial: all Clipper tables (docs/PLAN.md section 3)
Applying schema
Done
migrate exit code: 0

$ curl http://127.0.0.1:8000/api/health
{"status":"ok"}
$ curl http://127.0.0.1:8000/api/status
{"db":true,"worker_alive":true,"worker_last_heartbeat":"2026-09-26T08:59:12.214782Z","jobs":{"succeeded":1}}

$ docker inspect --format '{{.Name}} {{.HostConfig.RestartPolicy.Name}}' ...
/clipper-api-1 unless-stopped
/clipper-worker-1 unless-stopped
/clipper-postgres-1 unless-stopped
/clipper-migrate-1 no

$ docker compose logs worker
startup check ok: ffmpeg version 7.1.5-0+deb13u1 Copyright (c) 2000-2026 the FFmpeg developers
startup check ok: ffprobe version 7.1.5-0+deb13u1 Copyright (c) 2007-2026 the FFmpeg developers
INFO:procrastinate.worker.worker:Starting worker on all queues
```

`\dt` afterwards: accounts, alembic_version, brands, posts, renders, source_clips and the four
`procrastinate_*` tables.

Fail-fast check: the api image has no ffmpeg, and running the worker entrypoint in it exits 1 with
`FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'`.

Other checks: a file written to `./data/media-check.txt` was served by `curl /media/media-check.txt` (200, same body). A CORS
preflight from `Origin: http://localhost:5173` returned 200 with
`access-control-allow-origin: http://localhost:5173` (the origin now comes from `APP_BASE_URL`). `docker compose stop worker` took 1 s, logged
`Stop requested` / `Stopped worker`, and exited with code 0. In the container, PID 1 is `docker-init`
and it runs `procrastinate worker` directly, because `app/worker.py` execs into it.

## b) Migrate again (idempotent)

```
$ docker compose run --rm migrate
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
procrastinate schema: already applied
second migrate exit=0
```

## c) No-op job

```
$ docker compose exec api procrastinate defer ping
Launching a job: ping()
INFO:procrastinate.jobs:Deferred 1 job

$ psql -c "SELECT id, task_name, status, attempts, queue_name FROM procrastinate_jobs ORDER BY id"
 id |     task_name      |  status   | attempts | queue_name
----+--------------------+-----------+----------+------------
  1 | retry_stalled_jobs | succeeded |        1 | default
  2 | ping               | succeeded |        1 | default

worker: Starting job ping[2]() / INFO:app.tasks.queue:pong / Job ping[2]() ended with status: Success
```

## d) Kill-resume

Deferred `debug_sleep(seconds=60)` and, 10 s into the job, SIGKILLed the `procrastinate worker` process
inside the container (what an OOM kill or crash looks like; no `docker stop`/`start`). Docker's
`restart: unless-stopped` brought the container back within a second, and the sweeper re-queued the job.
The script is `/tmp/killresume.sh` (not committed): `procrastinate defer`, `docker exec … os.kill(pid, 9)`,
then a poll of `procrastinate_jobs` and `docker inspect` every 10 s.

```
08:59:44 defer debug_sleep(seconds=60)          job id: 3
08:59:54 before kill: 3 | doing | 0 | 1          (id | status | attempts | worker_id)   restarts=0
08:59:55 after kill:  3 | doing | 0 | 1          container running again, restarts=1 (docker restarted it)
09:00:06 .. 09:00:57   3 | doing | 0 | 1          (dead worker's heartbeat not yet 30 s old at the 09:00 tick)
09:01:07 3 | doing | 1 | 2                         (same job id, new worker)
09:02:08 3 | succeeded | 2 | 2

events for job 3: 08:59:44 deferred, 08:59:44 started, 09:01:00 scheduled,
                  09:01:00 deferred_for_retry, 09:01:05 started, 09:02:05 succeeded
worker: WARNING:app.tasks.queue:stalled job 3 (debug_sleep) re-queued
        Job debug_sleep[3](seconds=60) ended with status: Success, lasted 60.002 s
```

Re-queued 65 s after the kill and succeeded 2 min 10 s after it, with no manual step. The job ran its
60 s again. Worst case to re-queue is about 30 s of heartbeat timeout plus one cron minute.

`docker kill --signal=KILL clipper-worker-1` counts as a manual stop, so Docker does not restart that
one (`exited exit=137 restarts=1` 4 s later); `docker compose start worker` brings it back and the
sweeper recovers its jobs the same way (this was the run in the previous version of this file).

`docker compose restart worker` while `debug_sleep(120)` ran took 90 s: procrastinate logged `1 jobs
still running after graceful timeout. Aborting them`, but it cannot stop a sync thread, so compose
SIGKILLed the container at `stop_grace_period` and the job was left in `doing` for the sweeper.
`docker compose kill worker && docker compose start worker` took 2 s; CLAUDE.md now documents that.

## e) Tests, from scratch

```
$ docker compose down -v && docker compose run --rm api pytest -v
tests/test_db.py::test_migration_downgrade_and_upgrade PASSED
tests/test_db.py::test_insert_every_table PASSED
tests/test_db.py::test_check_constraint_rejects_bad_status PASSED
tests/test_db.py::test_idempotency_key_unique_only_while_post_is_live PASSED
tests/test_queue.py::test_retry_stalled_jobs PASSED
tests/test_services.py::test_storage_round_trip PASSED
tests/test_services.py::test_notify_noop_without_config PASSED
tests/test_services.py::test_inline_env_comment_is_blank PASSED
tests/test_services.py::test_notify_never_logs_token PASSED
tests/test_services.py::test_notify_sends_and_never_raises PASSED
============================== 10 passed in 0.28s ==============================
```

`docker compose run` starts postgres and migrate first. The tests drop and recreate their own
`clipper_test` database, apply the Alembic migration and Procrastinate's schema, and never touch
`clipper`. `test_migration_downgrade_and_upgrade` also runs `alembic check` (models match the migration).
`test_retry_stalled_jobs` runs the sweeper against real rows and covers five cases:
- dead-worker job: re-queued
- job already at 3 attempts: failed
- job whose queueing_lock is now held by a newer queued job: failed
- the newer job: untouched
- job owned by a live, heartbeating worker: untouched

Removing the attempts cap makes the sweeper test fail, so the test does check the cap. Likewise,
removing notify()'s no-op guard fails `test_notify_noop_without_config` (it records calls instead of
raising inside the handler, which notify's `except Exception` used to swallow), and removing the httpx
log-level line fails `test_notify_never_logs_token`.

## f) Restarts: `docker compose restart` and `down` + `up` without `-v`

```
20 x docker compose restart   (wait for migrate to exit, then /api/health)
restart 1: migrate exit=0 health=200
...
restart 20: migrate exit=0 health=200
failures: 0/20

2 x docker compose down && docker compose up -d
cycle 1: migrate exit=0 (procrastinate schema: already applied) health=200   ping jobs: 2 all succeeded
cycle 2: migrate exit=0 (procrastinate schema: already applied) health=200   ping jobs: 3 all succeeded
```

`docker compose restart` ignores `depends_on`, so migrate could reach postgres before it accepted
connections (a review saw 2 failures in about 53 restarts, `Connection refused` 6 ms before postgres
was ready). `alembic/env.py` now retries the first connect for up to 30 s. That race is too rare to
show in 20 runs, so it was also checked directly: with postgres stopped, `docker compose run --rm
--no-deps migrate` was started, postgres was started 6 s later, and migrate finished with exit 0
(`procrastinate schema: already applied`).

## Decisions worth knowing
- Successful jobs are **not** auto-deleted (`delete_jobs` stays at its default), so job history is
  visible, as in (c). Instead, the sweeper calls `delete_old_jobs(nb_hours=72)` each minute: it removes
  succeeded jobs older than 72 h and keeps failed ones.
- The venv lives at `/venv` in the image, so the dev bind mount of `./backend` over `/app` doesn't hide it.
- `DATABASE_URL` and `DATA_DIR` are required settings, and compose sets them. Every other setting has
  the PLAN §6 default.
- httpx logs every request URL at INFO and the worker's root logger is INFO (procrastinate CLI);
  the Telegram URL contains the bot token, so `notify.py` sets the `httpx` logger to WARNING. Checked
  in the worker with `procrastinate.cli.configure_logging()` and a fake token: only notify's own
  `HTTP 400` warning is printed, no URL.
- Compose's `env_file` keeps `KEY=   # comment` as the literal value `# comment`. The optional secrets
  (`ZERNIO_API_KEY`, `TELEGRAM_*`, `YTDLP_COOKIES_FILE`) treat a value starting with `#` as unset.
- postgres, api and worker have `restart: unless-stopped`; migrate stays one-shot.
- IDs are integer serials. Nullable columns: probe outputs and `raw_key` (a clip row exists before its
  bytes do), and `renders.overlay_config` (no brand means no logo).

## Telegram (verified live after the workflow)
- `.env` comment moved onto its own line; chat id read from `getUpdates` and saved.
- Real `notify()` from the api container: plain alert delivered (True).
- **Telegram rejects `http://localhost` URLs in inline buttons** (`400 Wrong HTTP URL`) and drops the
  whole message. `notify()` now uses a button only for `https://` links and appends other links to
  the text; the localhost-link alert was then delivered (True). Covered by `test_notify_sends_and_never_raises`.

## Could not verify
- `/api/status`, `/media`, CORS and the ffmpeg fail-fast path were checked by hand (above), not by pytest.
- The sweeper's attempts cap and the queueing-lock branch were tested on DB rows, not with a real
  worker that crashes repeatedly.
- PLAN §4's "attempts ≥ 3 → FAILED + domain cleanup by task name" has no domain cleanup yet. Phase 1
  has no render/publish tasks, so a poison job only ends as a failed Procrastinate job. Phases 2 and 5
  must add the row-level cleanup (for example render → FAILED, post → DEAD_LETTER `WORKER_CRASHED`).
- Only built and run on arm64 (Apple silicon), not amd64.
- `uvicorn --reload` picking up code edits was not exercised. The worker does not reload; it needs
  `docker compose kill worker && docker compose start worker` (see d).
- The stack coming back on its own after a Docker Desktop restart (`unless-stopped`) was not tested;
  only an in-container crash of the worker process was.
- The original `docker compose restart` migrate race did not recur in 20 runs; the fix is proven by the
  forced late-postgres run in (f), not by the race itself.
- Postgres connection usage under load (API pool 1-5 + SQLAlchemy pool + worker pool 1-5 + LISTEN)
  was not measured.
- Future Procrastinate upgrades that ship SQL migrations are not handled: the guarded
  `schema --apply` only covers an empty database (noted in README).
