# Clipper

A small multi-user platform for Instagram clip pages. It takes video files, overlays an advertiser logo, and
publishes them as Instagram Reels, on a schedule, to each user's own Instagram accounts.

Anyone can sign up with a username and password (nothing else) until `MAX_USERS` (15) users are enabled, the
operator included. Each user brings their own Zernio API key (their own Zernio account, where they connect their
Instagram accounts) and as many of their own Telegram bots as they like. A user sees only their own clips, brands,
captions, covers, renders, accounts, posts, bots and files. There are no roles, orgs, sharing or billing. The
operator is user 1 (`clipper`): everything from before users belongs to it, its three Telegram bots and Zernio key
are imported once from `.env` (in production at the release below), and only its link imports use the server's yt-dlp
cookies. How it all works: `docs/multi-user.md`.

**Production** (https://145-241-239-46.sslip.io, the `prod` branch, `~/clipper`) is this compose stack on one Oracle
Cloud Arm VM with `compose.prod.yml` on top, behind Caddy (HTTPS; the app signs users in itself) (`docs/deploy.md`).
Until the release pull request #20 (`dev` -> `prod`) is merged, production still runs the single-operator version: a
shared browser password in Caddy, no users, one `worker` for every queue, and services `bot`, `bot2`, `bot3` reading
the operator's key and tokens from `.env`. Everything in this file describes `dev`. **Staging**, the dev site
(https://dev.145-241-239-46.sslip.io), is the `dev` branch on the same VM (`~/clipper-dev`, project `clipper-dev`,
`compose.staging.yml`, no Caddy of its own: production's serves it), with its own sign-in and no shared password, on
a copy of production refreshed every 5 days by cron (`staging-refresh.sh`). It publishes for real (the operator's
Zernio key, re-imported at every refresh; other users paste theirs there), to the same Instagram accounts; the
refresh cancels every copied unpublished post first thing, and staging's api salts every Idempotency-Key
(`IDEMPOTENCY_SALT`), so production's schedule never goes out twice and no key is shared. The refresh also wipes every
session, stored key and bot (staging has its own `SECRETS_KEY`; never a production bot token there). Accounts made on
staging (ids above 10,000,000) survive every refresh with their password, key and bots, and nothing else (no clips,
posts, files, brands or accounts); production's arrive with the copy. Its renders run one at a time at a low CPU weight (`cpu_shares: 256`).
Branches: `dev` is the default branch and takes every change through a pull request; every push to `dev` that passes
CI deploys staging (`.github/workflows/deploy-dev.yml` -> `deploy.sh`); a `dev` -> `prod` pull request is a release,
and every push to `prod` deploys production (`.github/workflows/deploy.yml` -> `deploy.sh`); `master` is legacy. CI
(`.github/workflows/ci.yml`) runs the backend suite and the frontend checks on every pull request to `dev` or `prod`
and every push to `dev`. Publishing goes through **Zernio** (a third-party publishing API with its own approved Meta
app), not the Meta API directly.

Source of truth: `docs/PLAN.md` (implementation plan, rev 2, later revisions listed at its top) and
`docs/multi-user.md` (users, tenancy, secrets, per-user Zernio and Telegram, threat model, operator runbook).
Original spec: `docs/spec.md` (historical). Telegram bots: `docs/telegram-bot.md`. Runbook: `docs/deploy.md`.
UI design contract: `docs/design/BRIEF.md` + mockups `docs/design/*.png` / `*.html`.
Zernio API reference: https://docs.zernio.com (append `.mdx` to a page URL for plain text;
`https://docs.zernio.com/llms.txt` is the index).

## Stack

Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 (async, psycopg3), Alembic, uv
Auth: username + password (bcrypt), server-side sessions in Postgres (an HttpOnly cookie), no email
Tenancy: Postgres row-level security; the api connects as `clipper_app`, which can't bypass it
Secrets: each user's Zernio key and bot tokens sealed with Fernet (`cryptography`) under `SECRETS_KEY`
Queue: Procrastinate (Postgres-backed, no Redis anywhere in this project): queue `media` (worker), `default` (publisher)
Database: PostgreSQL 16
Storage: local `./data` directory (bind-mounted into api, worker, publisher), a user's files under `u/{id}/`, served
at `/media/*` to their owner only
Video: ffmpeg and ffprobe invoked with subprocess, no Python wrapper libraries
Publishing: Zernio REST API (`https://zernio.com/api/v1`) via httpx, with the post owner's key
Frontend: React 19, Vite, TypeScript, Tailwind v4, shadcn/ui, react-router
Tables: plain <table> + map() (add TanStack Table v9 when sorting/selection is needed). Data fetching: TanStack Query
API client: generated from the FastAPI OpenAPI schema with @hey-api/openapi-ts
Telegram: one supervisor process runs every user's bots (`app/bot`, httpx, no bot framework, long polling); each bot
calls the api as its owner, like their browser
Run: `docker compose up` (postgres, migrate, api, worker, publisher, bot) + `npm run dev` in /frontend (read "Do not"
first: never with the Mac's `.env`)

## Repo layout

/backend
  /app
    /api          FastAPI routers: auth.py (sign-in, /api/me, the Zernio key), bots.py (Telegram bots, /api/internal),
                  pipeline.py, scheduling.py, recovery.py
    /models       SQLAlchemy models (Owned: the user_id column of every tenant table)
    /schemas      Pydantic schemas
    /tasks        Procrastinate tasks: media.py (queue media), publish.py, accounts.py, queue.py
    /services     business logic (zernio.py, publisher.py, render.py, storage.py, notify.py, errors.py, links.py)
    /core         config, db session (sets app.uid), secrets (seal / unseal)
    /bot          Telegram bot service: a client of the api (python -m app.bot)
    cli.py        operator commands (render, users, quotas, db-grants, bootstrap)
  /alembic        0007 users + row-level security, 0008 media queue, 0009 bot supervisor functions
  /scripts        spike_zernio.py (Phase 0), dump_openapi.py
  /tests          fixtures/zernio/ holds real recorded Zernio responses and copies from Zernio's docs;
                  test_tenancy.py runs the api as clipper_app with two users
  openapi.json    committed; regenerate after any API change
/frontend
  /src
    /components   FracBox (drag/resize in fractions), bits, ui/ (shadcn slider, switch)
    /routes       Library, Editor, Customizations, Calendar, Accounts, Recover, Login (+ Signup), Settings (+ Setup)
    /api          generated client (never edit by hand)
    /lib          geometry.ts (+ test), utils.ts (+ test), schedule.ts (+ test), cover.ts
  /e2e            accept.mjs (Playwright acceptance, Google Chrome), docs-screenshots.mjs
/docs             multi-user.md, deploy.md, telegram-bot.md, guide/, PLAN.md, spec.md, phase-N.md, design/
compose.yml       compose.prod.yml, compose.staging.yml, compose.review.yml, Caddyfile, deploy.sh, staging-refresh.sh, review.sh
/.github/workflows  ci.yml (CI), deploy.yml (push to prod -> production), deploy-dev.yml (CI passed on dev -> staging)

## Commands

All backend commands run in containers (the host has no ffmpeg or psql). From the repo root. Any checkout other
than the operator's own (a Conductor worktree, an agent) uses its own compose project, `-p <name>` (e.g.
`clipper-mt`), so it never touches the Mac's `clipper` stack or `clipper-review`. `-p` separates containers and the
database volume only: the env still comes from that checkout's `.env` (a worktree has none), and ports 8000 and 5432
are fixed, so one such stack at a time.

```sh
./review.sh                                                           # this branch on a copy of production (the operator's rows and files only), publishing off, no bots, no secrets (`./review.sh down` drops it)
docker compose -p <name> run --rm worker pytest                       # full test suite (worker has ffmpeg; own clipper_test db); runs migrate on the dev db first
docker compose -p <name> up -d --build                                # whole stack; api on 127.0.0.1:8000 (never the Mac's default project: see "Do not")
docker compose exec worker python -m app.cli render <clip_id> <brand_id>  # probe if needed + render, no queue, as the clip's owner; prints the path
docker compose run --rm migrate                                       # alembic upgrade + guarded procrastinate schema + db-grants + bootstrap
docker compose run --rm --no-deps migrate alembic revision --autogenerate --rev-id <NNNN> -m "..."  # stack up; the superuser: the api's role can't read alembic_version
docker compose run --rm --no-deps api python scripts/dump_openapi.py  # refresh backend/openapi.json
docker compose exec api procrastinate defer ping                      # prove the publisher consumes jobs (default queue)
docker compose exec postgres psql -U clipper                          # SQL shell (the superuser: row-level security doesn't apply)
docker compose exec api python -m app.cli list-users                  # id, username, created, active/disabled, Zernio key state, quota
docker compose exec api python -m app.cli set-password <username>     # asks (or reads one stdin line); signs them out everywhere
docker compose exec api python -m app.cli disable-user <username>     # signs out, stops their bots and publishing, frees a signup spot
docker compose exec api python -m app.cli enable-user <username>
docker compose exec api python -m app.cli set-quota <username> <GB|none>  # cap on their clips + renders; none = unlimited
docker compose kill worker publisher && docker compose start worker publisher  # reload worker code (no auto-reload)
docker compose restart bot                                            # reload bot code (no auto-reload)
docker compose logs -f bot                                            # the bot supervisor (an ignored chat logs its chat id)
CLIPPER_HOST=x CLIPPER_ACME_EMAIL=x SECRETS_KEY=x BOT_SERVICE_SECRET=x docker compose -f compose.yml -f compose.prod.yml build api  # prod images on the Mac (dummies satisfy compose.prod.yml's guards; the VM's .env sets them and COMPOSE_FILE)
```

`db-grants` (the api's role `clipper_app` and its rights) and `bootstrap` (user 1's name and password from
`CLIPPER_USER` / `CLIPPER_PASSWORD_HASH`, and the one-shot `.env` import) run in every `migrate`; both are idempotent.

Frontend, from `frontend/` on the host (Node 22):

```sh
npm run dev          # Vite on :5173, proxies /api and /media to 127.0.0.1:8000 (CLIPPER_API=http://127.0.0.1:<port> for another api)
npm test             # vitest (src/lib/geometry.test.ts is the preview-vs-render contract)
npm run typecheck    # tsc -b
npm run build        # tsc -b && vite build
npm run lint
npm run gen:api      # regenerate src/api from ../backend/openapi.json (never edit src/api by hand)
CLIPPER_E2E_USER=<user> CLIPPER_E2E_PASSWORD=<password> node e2e/accept.mjs  # stack + dev server up: Phase 3 acceptance in Google Chrome as that user (docs/phase-3.md); its `docker compose exec worker` follows COMPOSE_PROJECT_NAME / COMPOSE_FILE
```

Geometry lives in `frontend/src/lib/geometry.ts` and must keep matching `backend/app/services/render.py`
(overlay = fractions of the 1080x1920 output, crop = fractions of the source, cover-fit). Change both or
neither, and keep `geometry.test.ts` and `e2e/accept.mjs` passing.

Filters (Instagram-style looks; Instagram's API applies none) live only in `FILTERS` in `render.py`: the Editor
previews them by `GET /api/filters` (CSS `filter` over `mix-blend-mode` colour layers) and `filter_chain` replays that
CSS maths in ffmpeg. A new recipe needs solid-colour layers in a `BLEND` mode and a Chrome-measured row in
`test_render.py`'s `CHROME`.

Procrastinate tasks live in `app/tasks/`; add each new task module to `import_paths` in
`app/tasks/queue.py` (the workers only import that module, so a task defined elsewhere fails with
TaskNotFound). Always pass an explicit `name=`. `probe_clip`, `download_clip` and `render` run on queue `media`
(the `worker` service: 2 at a time, one per user, fair-share priority set by `pipeline._defer`); everything else
(dispatch, publish_post, the account sync, the sweeper) on `default` (the `publisher` service), so a render backlog
never holds up a post. The `retry_stalled_jobs` periodic task re-queues jobs a killed worker left in `doing`
(within about 30-90 s). `docker compose restart worker` (or `publisher`) blocks for the full 90 s
`stop_grace_period` when a sync job runs past the 60 s graceful timeout, then SIGKILLs it and the job re-runs from
the start via the sweeper; kill + start gets the same result without the wait. A Postgres restart makes the workers
stop themselves (their LISTEN connection drops); if a render is running it finishes that first, so the worker can
look offline for up to the render's length before Docker restarts it.

## Hard constraints the code must respect

1. Every rendered MP4 is 1080x1920 H.264 High yuv420p, 30 fps closed GOP, AAC 48 kHz stereo (silent
   track if the source has none), and `-movflags +faststart`.
2. Publishing is: upload the render to Zernio (`POST /v1/media/presign`, PUT the bytes), persist
   `zernio_media_url` (the cover, if any, the same way as `zernio_cover_url`), then `POST /v1/posts`
   with `publishNow: true` and an `Idempotency-Key` header, all with the post owner's Zernio key.
3. The `Idempotency-Key` is the post's `idempotency_key`, persisted before the first call. Every retry
   reuses the same key and the same media URL. Never re-POST without the key, and never re-POST more
   than 20 hours after the first attempt (Zernio's replay window is 24 h). Zernio replays a key per credential,
   so never re-POST under another Zernio key either: `posts.key_gen` is set in the same CAS as `first_post_at`, and
   a replay whose `key_gen` differs from the user's `zernio_key_gen` goes DEAD_LETTER `KEY_CHANGED`. Every change of a
   user's key bumps `zernio_key_gen`, takes the user row `FOR UPDATE` and is refused (`KEY_IN_USE`) while one of
   their posts is PUBLISHING; the publish claim and a recovery retry read the key `FOR SHARE`. This is what prevents
   a duplicate live Reel; Instagram posts cannot be deleted through Zernio.
4. HTTP 207 is not success: branch on `post.status` (`published` | `partial` | `failed` | `scheduled`).
   Classify failures on `platforms[].errorCategory` and the documented HTTP status, `code`, `required_group` and
   `type`, never on message strings. A refusal of the key itself (401, 403 with `required_group`, any 403 on presign,
   402) pauses the user (`zernio_key_status = 'invalid'`, one alert per flip), not only the post.
5. Every post state change is a compare-and-set (`UPDATE ... WHERE id = :id AND status IN (...)`).
6. Overlay geometry is stored as fractions of the 1080x1920 output frame; crop geometry as fractions
   of the source frame (after autorotate). Never absolute pixels.
7. Reels are 3 s–15 min (`ZERNIO_MAX_REEL_SECONDS` = 900). Zernio's docs say 90 s; a 120 s Reel
   published fine in the live run (2026-09-26), so the real limit is Meta's.
8. Blocking work (ffmpeg, ffprobe, yt-dlp, file I/O) runs in sync `def` tasks; `async def` tasks never
   block the event loop (a blocked loop stops Procrastinate heartbeats and the job runs twice).
9. Tenant isolation is the database's job. Every tenant table has `user_id` (the `Owned` mixin), an index, row-level
   security enabled and forced with the policy `tenant` (`user_id = app.uid`), and composite FKs `(x_id, user_id)` to
   the tenant rows it references (`owned_by`); `test_tenancy.py` fails for a `user_id` table without the policy.
   The api connects as `clipper_app`, and its request session (`Db`, made with `info={"uid": …}`) sets `app.uid` in
   every transaction, so a forgotten filter returns nothing and a foreign id is a 404. The sign-in lookup uses its
   own session. Api code takes the user from `s.info["uid"]`, never from `row.user_id` read back after an insert.
10. Superuser code (worker, publisher, CLI, migrate) bypasses row-level security. Work keyed by one id needs no
    filter; every read or write of many rows of a tenant table filters `user_id` explicitly, and every insert sets
    it. The api reaches across users only through SECURITY DEFINER functions with a fixed body,
    `SET search_path = public, pg_temp` and EXECUTE revoked from PUBLIC (`db-grants` gives it to `clipper_app`).
11. Users' secrets (Zernio keys, bot tokens) are stored sealed (`app.core.secrets.seal`) and never appear in api
    responses (the key's last 4 characters at most), logs (a Telegram URL holds its token: httpx logs at WARNING),
    job arguments, or error messages. Only api, publisher and migrate hold `SECRETS_KEY`; only api and bot hold
    `BOT_SERVICE_SECRET`; each compose service blanks what it must not hold.

## Do not

- Do not call the Meta / Instagram Graph API directly in this version.
- Do not fake Zernio anywhere except `httpx.MockTransport` in tests, and then only with response bodies from
  `backend/tests/fixtures/zernio/`. Never use a fake to claim the integration works.
- Do not publish to a real Instagram account from automated tests or scripts without the operator's
  explicit go-ahead for that specific run. Never call Zernio or Telegram with a real user's key or token from a test,
  a script or an agent.
- Do not invent Zernio endpoint names or parameters. Check the docs; if unsure, stop and ask.
- Do not use ffmpeg.wasm or any browser-side video encoding.
- Do not build a timeline editor, trimming UI, or multi-clip sequencing.
- Do not add Redis, Celery, or any broker other than Postgres.
- Do not run ffmpeg anywhere except the worker container.
- Do not use localStorage or sessionStorage for anything that matters (the session is an HttpOnly cookie).
- Do not add admin roles, orgs, teams, sharing between users or an admin UI. Users are equal and see only their own
  data; the operator manages users with the CLI.
- Do not add a route that answers without a user, other than `/api/health`, `/api/auth/*` and the SPA's files:
  routers take `dependencies=[Depends(current_user)]` and handlers the `Db` session.
- Do not give the api a superuser `DATABASE_URL` (it stays `clipper_app`, which row-level security binds), and do not
  add BYPASSRLS or a policy that skips `app.uid`.
- Do not give the bot database access, `SECRETS_KEY` or rules of its own: it calls the api as each bot's owner
  (`Authorization: Bearer $BOT_SERVICE_SECRET` + `X-Clipper-User`), so every guard applies once. The api honours
  `X-Clipper-User` only with that bearer.
- Do not write the operator's password or any hash of it, or any user's key or token, into the repo, tests,
  fixtures, docs or commit messages: the repo is public. Tests make throwaway bcrypt hashes.
- Do not commit `.env` or anything under `data/`.
- Do not expose Clipper except through Caddy: on the VM, Caddy (HTTPS, `Caddyfile`; it strips `Authorization` and
  `X-Clipper-User` and answers 404 for `/api/internal/*`) is the only published port. Never publish another port
  beyond 127.0.0.1 or bypass Caddy.
- Do not put `SECRETS_KEY` in the Mac's `.env`, or in any `.env` that still holds the operator's `ZERNIO_API_KEY` or
  `TELEGRAM_*`: the next migrate (a test run's too) imports them into that database for good, and its publisher and
  bot then act on production's accounts. (The VM's two are the deliberate exceptions: production's holds them for the
  release's one-shot import, staging's holds the key, which every refresh re-imports, and never `TELEGRAM_*`.) Never
  copy the VM's `SECRETS_KEY` off the VM, except into the operator's password manager.
- Do not start the Mac's own stack (`docker compose up` in the operator's checkout, Conductor's Run) while the VM runs
  production: its `.env` still holds the production Zernio key and bot tokens (the operator's choice), and any branch
  from before users reads them directly, so it would publish the same schedule and fight the VM's bots. Develop with
  `./review.sh`, or in a checkout without those values under its own project (`-p`), with throwaway secrets if a key
  or bot must work.

## Design direction

Dark-first. This is a tool used at night to queue tomorrow's posts, and video thumbnails read better
on dark surfaces.

Dense, not airy. This is a working surface, not a marketing page. Table rows are 56 px with a 28x50
9:16 thumbnail so fifteen fit on screen; cards (upload rows, render queue) use 36x64 thumbnails,
calendar slot tiles up to 72x128 (36x64 at 5+ slots; see docs/design/BRIEF.md). Resist the default shadcn spacing.

One accent colour (#4F8CFF), used only for primary actions and in-progress/scheduled state. ok / warn /
bad colours only where state demands it. No gradients, no glassmorphism, no rounded-3xl cards, no hero
sections, no illustrations.

Typography: Geist for UI, tabular numerals for anything showing times, counts or durations. Geist Mono
only for IDs and error codes.

Motion is limited to state transitions that communicate something, such as a row moving from
rendering to ready. No decorative animation.
