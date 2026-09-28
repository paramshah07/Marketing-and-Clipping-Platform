# Clipper

An internal tool for one operator. It takes video files, overlays an advertiser logo, and publishes
them as Instagram Reels to the operator's own Instagram accounts on a schedule.

Single user. No multi-tenancy, no billing, no org model, no login. **This version runs on localhost
by default** (not production-grade): no Cloudflare Access, no VPS, no R2. A Cloudflare tunnel is
allowed temporarily for demos; there is still no login, so anyone with the link has full access —
tear the tunnel down right after. **Production** is the same stack on one Oracle Cloud Arm VM with
`compose.prod.yml` on top, public at an sslip.io name behind Caddy and one shared password (`docs/deploy.md`); every merge to master
deploys it (`.github/workflows/deploy.yml` -> `deploy.sh`). Publishing goes through **Zernio** (a third-party publishing API
with its own approved Meta app), not the Meta API directly.

Source of truth: `docs/PLAN.md` (implementation plan, rev 2). Original spec: `docs/spec.md`.
Telegram bot: `docs/telegram-bot.md`.
UI design contract: `docs/design/BRIEF.md` + mockups `docs/design/*.png` / `*.html`.
Zernio API reference: https://docs.zernio.com (append `.mdx` to a page URL for plain text;
`https://docs.zernio.com/llms.txt` is the index).

## Stack

Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 (async, psycopg3), Alembic, uv
Queue: Procrastinate (Postgres-backed, no Redis anywhere in this project)
Database: PostgreSQL 16
Storage: local `./data` directory (bind-mounted into api + worker), served at `/media/*`
Video: ffmpeg and ffprobe invoked with subprocess, no Python wrapper libraries
Publishing: Zernio REST API (`https://zernio.com/api/v1`) via httpx
Frontend: React 19, Vite, TypeScript, Tailwind v4, shadcn/ui, react-router
Tables: plain <table> + map() (add TanStack Table v9 when sorting/selection is needed). Data fetching: TanStack Query
API client: generated from the FastAPI OpenAPI schema with @hey-api/openapi-ts
Telegram: a long-polling bot service (`app/bot`, httpx, no bot framework) that calls the api like the browser
Run: `docker compose up` (postgres, migrate, api, worker, bot) + `npm run dev` in /frontend

## Repo layout

/backend
  /app
    /api          FastAPI routers
    /models       SQLAlchemy models
    /schemas      Pydantic schemas
    /tasks        Procrastinate tasks
    /services     business logic (zernio.py, render.py, storage.py)
    /core         config, db session
    /bot          Telegram bot service: a client of the api (python -m app.bot)
  /alembic
  /scripts        spike_zernio.py (Phase 0), dump_openapi.py
  /tests          fixtures/zernio/ holds real recorded Zernio responses
  openapi.json    committed; regenerate after any API change
/frontend
  /src
    /components   FracBox (drag/resize in fractions), bits, ui/ (shadcn slider, switch)
    /routes       Library, Editor, Customizations
    /api          generated client (never edit by hand)
    /lib          geometry.ts (+ test), utils.ts
  /e2e            accept.mjs (Playwright acceptance, Google Chrome)
/docs             PLAN.md, spec.md, phase-N.md, design/
compose.yml

## Commands

All backend commands run in containers (the host has no ffmpeg or psql). From the repo root:

```sh
docker compose up -d --build                                          # whole stack; api on 127.0.0.1:8000
./review.sh                                                           # this branch on a copy of production's data, publishing off, no bots (`./review.sh down` drops it)
docker compose run --rm worker pytest                                 # full test suite (worker has ffmpeg; own clipper_test db)
docker compose exec worker python -m app.cli render <clip_id> <brand_id>  # probe if needed + render, no queue; prints the path
docker compose run --rm migrate                                       # alembic upgrade + guarded procrastinate schema
docker compose run --rm --no-deps api alembic revision --autogenerate -m "..."
docker compose run --rm --no-deps api python scripts/dump_openapi.py  # refresh backend/openapi.json
docker compose exec api procrastinate defer ping                      # prove the worker consumes jobs
docker compose exec postgres psql -U clipper                          # SQL shell
docker compose kill worker && docker compose start worker             # reload worker code (no auto-reload)
docker compose restart bot                                            # reload bot code (no auto-reload)
docker compose logs -f bot                                            # the bot (an ignored chat logs its chat id)
CLIPPER_HOST=x CLIPPER_USER=x CLIPPER_PASSWORD_HASH=x CLIPPER_ACME_EMAIL=x docker compose -f compose.yml -f compose.prod.yml build api  # prod images on the Mac (dummies satisfy caddy's guards; the VM's .env sets them and COMPOSE_FILE)
```

Frontend, from `frontend/` on the host (Node 22):

```sh
npm run dev          # Vite on :5173, proxies /api and /media to 127.0.0.1:8000
npm test             # vitest (src/lib/geometry.test.ts is the preview-vs-render contract)
npm run typecheck    # tsc -b
npm run build        # tsc -b && vite build
npm run lint
npm run gen:api      # regenerate src/api from ../backend/openapi.json (never edit src/api by hand)
node e2e/accept.mjs  # stack + dev server up: Phase 3 acceptance in Google Chrome (docs/phase-3.md)
```

Geometry lives in `frontend/src/lib/geometry.ts` and must keep matching `backend/app/services/render.py`
(overlay = fractions of the 1080x1920 output, crop = fractions of the source, cover-fit). Change both or
neither, and keep `geometry.test.ts` and `e2e/accept.mjs` passing.

Procrastinate tasks live in `app/tasks/`; add each new task module to `import_paths` in
`app/tasks/queue.py` (the worker only imports that module, so a task defined elsewhere fails with
TaskNotFound). Always pass an explicit `name=`. The `retry_stalled_jobs` periodic task re-queues jobs a
killed worker left in `doing` (within about 30-90 s). `docker compose restart worker` blocks for the full
90 s `stop_grace_period` when a sync job runs past the 60 s graceful timeout, then SIGKILLs it and the
job re-runs from the start via the sweeper; kill + start gets the same result without the wait.
A Postgres restart makes the worker stop itself (its LISTEN connection drops); if a render is running it
finishes that first, so the worker can look offline for up to the render's length before Docker restarts it.

## Hard constraints the code must respect

1. Every rendered MP4 is 1080x1920 H.264 High yuv420p, 30 fps closed GOP, AAC 48 kHz stereo (silent
   track if the source has none), and `-movflags +faststart`.
2. Publishing is: upload the render to Zernio (`POST /v1/media/presign`, PUT the bytes), persist
   `zernio_media_url` (the cover, if any, the same way as `zernio_cover_url`), then `POST /v1/posts`
   with `publishNow: true` and an `Idempotency-Key` header.
3. The `Idempotency-Key` is the post's `idempotency_key`, persisted before the first call. Every retry
   reuses the same key and the same media URL. Never re-POST without the key, and never re-POST more
   than 20 hours after the first attempt (Zernio's replay window is 24 h). This is what prevents a
   duplicate live Reel; Instagram posts cannot be deleted through Zernio.
4. HTTP 207 is not success: branch on `post.status` (`published` | `partial` | `failed` | `scheduled`).
   Classify failures on `platforms[].errorCategory`, never on message strings.
5. Every post state change is a compare-and-set (`UPDATE ... WHERE id = :id AND status IN (...)`).
6. Overlay geometry is stored as fractions of the 1080x1920 output frame; crop geometry as fractions
   of the source frame (after autorotate). Never absolute pixels.
7. Reels are 3 s–15 min (`ZERNIO_MAX_REEL_SECONDS` = 900). Zernio's docs say 90 s; a 120 s Reel
   published fine in the live run (2026-09-26), so the real limit is Meta's.
8. Blocking work (ffmpeg, ffprobe, yt-dlp, file I/O) runs in sync `def` tasks; `async def` tasks never
   block the event loop (a blocked loop stops Procrastinate heartbeats and the job runs twice).

## Do not

- Do not call the Meta / Instagram Graph API directly in this version.
- Do not fake Zernio anywhere except `httpx.MockTransport` in publish state-machine tests, and then
  only with response bodies from `backend/tests/fixtures/zernio/`. Never use a fake to claim the
  integration works.
- Do not publish to a real Instagram account from automated tests or scripts without the operator's
  explicit go-ahead for that specific run.
- Do not invent Zernio endpoint names or parameters. Check the docs; if unsure, stop and ask.
- Do not use ffmpeg.wasm or any browser-side video encoding.
- Do not build a timeline editor, trimming UI, or multi-clip sequencing.
- Do not add Redis, Celery, or any broker other than Postgres.
- Do not run ffmpeg anywhere except the worker container.
- Do not use localStorage or sessionStorage for anything that matters.
- Do not add user accounts, login pages, roles or permissions. The bot answers `TELEGRAM_CHAT_ID` only.
- Do not give the bot database access or rules of its own: it calls the api, so every guard applies once.
- Do not commit `.env` or anything under `data/`.
- Do not expose Clipper without its password (it has no login): on the VM, Caddy (HTTPS + basic auth,
  `Caddyfile`) is the only published port. Never publish another port beyond 127.0.0.1 or bypass Caddy.
- Do not start the local stack (`docker compose up`, Conductor's Run) while the VM runs production: the Mac's
  `.env` still holds the production Zernio key and bot tokens (the operator's choice), so it would publish the
  same schedule and fight the VM's bots. Develop against a copy with those blanked, or with the stack's bots off
  and `PUBLISHING_ENABLED=false`.

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
