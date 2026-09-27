# Clipper

Internal tool: takes video clips, overlays an advertiser logo, publishes them as Instagram Reels via
Zernio on a schedule. Localhost only. See `CLAUDE.md` and `docs/PLAN.md`.

## Run

Needs Docker. Settings live in `.env` at the repo root (gitignored); `.env.example` says where each value
comes from. `ZERNIO_API_KEY` is required; `PUBLISHING_ENABLED` defaults to `false`, so scheduled posts never
go out until you set it to `true` (real Reels, which Zernio cannot delete). Telegram alerts are optional.
Other settings and their defaults: `backend/app/core/config.py`.

```sh
cp .env.example .env                  # then fill in ZERNIO_API_KEY
docker compose up -d --build          # postgres, migrate (one-shot), api, worker
curl http://127.0.0.1:8000/api/health # {"status":"ok"}
curl http://127.0.0.1:8000/api/status # db, worker heartbeat, job counts
```

Then the UI (needs Node 22 on the host):

```sh
cd frontend && npm install && npm run dev   # http://localhost:5173 (proxies /api and /media to :8000)
```

- API: http://127.0.0.1:8000 (docs at `/docs`), reloads on code changes in `backend/`.
- Files under `./data` are served at `/media/...`.
- Postgres: `127.0.0.1:5432`, user/password/db `clipper`.
- Worker code changes need `docker compose kill worker && docker compose start worker` (a running job
  re-runs from the start; `restart worker` does the same after waiting up to 90 s).

First run:

1. Connect the Instagram account in the Zernio dashboard (Clipper never talks to Meta directly).
2. Accounts → Sync accounts, then set the account's timezone, posting slots, daily cap and min gap.
3. Brands → New brand, with a transparent PNG logo.
4. Library → upload a clip (3 s to 15 min), open it in the Editor, render, then schedule the render.

## Test

```sh
docker compose run --rm worker pytest
```

Runs in the worker container (it has ffmpeg; in the api container the ffmpeg tests are skipped) against
the compose postgres, in a separate `clipper_test` database that is dropped and recreated on every run.

Frontend (from `frontend/`):

```sh
npm test                 # vitest: the preview-vs-render geometry (src/lib/geometry.test.ts)
npm run typecheck        # tsc -b
npm run build            # typecheck + production bundle in dist/
npm run lint
node e2e/accept.mjs      # stack + `npm run dev` up: drives the real UI in Google Chrome, renders,
                         # and checks the rendered frame against the preview (docs/phase-3.md)
```

## Common tasks

```sh
docker compose run --rm migrate                                          # apply migrations (idempotent)
docker compose run --rm --no-deps api alembic revision --autogenerate -m "..."
docker compose exec api procrastinate defer ping                          # no-op job, proves the worker runs
docker compose run --rm --no-deps api python scripts/dump_openapi.py      # refresh backend/openapi.json
docker compose exec worker python -m app.cli render <clip_id> <brand_id>  # render without the queue (--x --y --w --opacity)
(cd backend && uv lock --upgrade-package yt-dlp) && docker compose up -d --build worker  # yt-dlp breaks often: update it
docker compose down -v                                                    # stop and delete the database
(cd frontend && npm run gen:api)                                          # regenerate src/api after dump_openapi.py
```

Procrastinate upgrades that ship SQL migrations (`procrastinate schema --migrations-path`) need that SQL
applied by hand or wrapped in an Alembic revision; `migrate` only applies the full schema to an empty DB.
