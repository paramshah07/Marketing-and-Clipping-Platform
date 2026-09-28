# Clipper — implementation plan, revision 2 (2026-09-26): Zernio + localhost-first

Supersedes revision 1 (archived as `PLAN-v1-meta-direct.md`, keep for a future direct-Meta / production
path). Spec: `docs/spec.md`. Research: `.context/research/*.md`
(Zernio facts in `nometa-vendors.md`). Mockups: `docs/design/`.

## Operator decisions
- Rev 1 (all defaults approved): React 19 · every-minute dispatcher · httpx-transport fakes allowed ONLY
  for publish state-machine tests, ONLY with responses recorded in Phase 0, never as proof of integration ·
  crop locked to 9:16 · DRAFT + brands.auto_approve as approval gate · rights "none" = confirm dialog ·
  from-url best effort TikTok / Instagram / X · table thumbs 28x50 + hover preview, cards 36x64 ·
  share_to_feed=true · one branch + PR per phase into master.
- Rev 2: **publish via Zernio** (operator can't create a Meta developer account) · **localhost-first,
  not production-grade**: no Cloudflare Access/Tunnel, no VPS, no R2 for now.

## 0. Spec deviations still in force
| # | Change | Why |
|---|---|---|
| D1 | React 19.2+, TanStack Table v9, react-router 8, Vite 8, Tailwind v4 | current shadcn needs React 19 refs |
| D2 | Logo width = fraction of 1080 px output: `scale=round(w*1080):-1` | spec's `iw*scale` makes render ≠ preview |
| D3 | Crop fractions are of the **source** frame (after autorotate), even-rounded; overlay fractions of **output** | spec converts crop against the wrong frame |
| D4 | ffmpeg: tonemap only for HLG/PQ sources, `fps=30`, `setsar=1`, `-maxrate 20M -bufsize 40M`, closed GOP, `-ar 48000 -ac 2`, first audio track only, silent AAC via `anullsrc` if no audio, `-threads` setting, `+faststart` | Reels spec; iPhone HDR / slo-mo / no-audio sources |
| D5 | Probe stores display dims (rotation), `avg_frame_rate`, codec, color_transfer, thumbnail | rotated/VFR phone clips |
| D6 | Stalled-job sweeper (Procrastinate leaves SIGKILLed jobs in `doing`) | kill-resume acceptance |
| D7 | Every-minute dispatcher instead of per-post `schedule_at` jobs; all state writes compare-and-set | no cancel races, no orphans |
| D8 | Telegram = Bot API `sendMessage` for alerts, plus the interactive bot service (long polling, `docs/telegram-bot.md`); **optional** on localhost (in-app failed badge always on) | no webhook needed |
| D9 | Quota: Zernio `GET /v1/accounts/{id}/instagram/publishing-limit` (live `quotaTotal`), cached 5 min, next to our own `Today n/daily_cap` | Meta says 50 or 100 depending on page |
| D10 | Storage = local `./data` dir (bind mount), browser uploads = multipart POST to API with XHR progress, files served at `/media/*` | localhost-first; swap `storage.py` to R2 later |
| D11 | Tables 28×50 thumbs (15 rows), cards 36×64 | spec's 64 px-wide rows only fit 8 |
| D12 | Retry counts: network errors 3 retries (4 runs); render retries only transient errors, ffmpeg exit≠0 → FAILED | Procrastinate `max_attempts` counts retries |

Dropped from the spec for now (Meta-direct only): containers, 24 h container expiry, token storage/
refresh, Fernet, OAuth, `content_publishing_limit` calls, R2 presigning, Cloudflare Access.

## 1. Zernio facts (from docs; live-verified items marked ✔, see docs/phase-0.md)
- Base `https://zernio.com/api/v1`, header `Authorization: Bearer sk_…`. Free for 2 connected accounts,
  $6/mo per account for 3–10. Unlimited posts. API 60 req/min free / 600 paid.
- A **profile holds one account per platform** → one profile per Instagram account (profiles are free;
  connecting a 2nd IG account into a profile replaces the 1st). Business or Creator accounts; Instagram
  login, no Facebook Page.
- ✔ `GET /v1/accounts` → `accounts[]` with `_id`, `platform`, `username`, `isActive`, `profileId`.
- ✔ Media: `POST /v1/media/presign` `{filename, contentType, size}` → `uploadUrl` (valid 1 h) + `publicUrl`; PUT the bytes. Temp storage
  7 days; copied to permanent storage when a post publishes.
- Publish: `POST /v1/posts` `{content, mediaItems:[{type:'video', url}], platforms:[{platform:'instagram',
  accountId, platformSpecificData:{shareToFeed:true, instagramThumbnail?}}], publishNow:true}` with
  `Idempotency-Key` (24 h; ✔ replay returns 200 + the original post even when the body differs).
  201 = published · 207 = failed/partial (`platforms[].errorCategory`, `errorMessage`) · 409
  `idempotency_conflict` + Retry-After = first request still running · 409 content duplicate +
  `details.existingPostId` = GET it and check status · 429 + Retry-After.
- `GET /v1/posts/{id}`, `POST /v1/posts/{id}/retry` (retries failed platforms only).
- `errorCategory`: auth_expired, user_content, user_abuse, platform_rate_limit, quota_exhausted,
  account_issue, platform_rejected, platform_error, system_error, unknown.
- Limits: ✔ Meta 100 posts / rolling 24 h per account (live `quotaTotal`); Zernio 25 posts/hour per account; Reels documented
  as 3–90 s, but ✔ a 120 s Reel published live, so Clipper uses Meta's 3 s–15 min; ≤ 300 MB.
- ✔ For an Instagram video, `publishNow` returns **201 while `post.status` is still `publishing`**; the
  Reel goes live ~45 s later (poll `GET /v1/posts/{id}`). ✔ A same-key replay of a live post returns 200
  "Post already exists (idempotent retry)". ✔ Reels are publicly visible logged out.
- Webhooks exist (signed); **not used on localhost** (nothing public to receive them) → polling.
- Instagram posts cannot be unpublished/deleted via Zernio. `isPaidPartnership` needs a Facebook-Login
  connection. `trialParams` (MANUAL) = Trial Reel for non-followers only.

## 2. Architecture (localhost)
```
Browser ─ localhost:5173 (Vite dev server; proxies /api and /media → :8000)
            └─ api   (FastAPI :8000, uvicorn --reload)
                 ├─ postgres 16 (app tables + procrastinate_* tables)
                 └─ defers jobs ─> worker (Procrastinate; ffmpeg 7.1, ffprobe, yt-dlp; concurrency 4)
 ./data (bind mount shared by api + worker): raw/ thumbs/ renders/ logos/ covers/
 worker ─> zernio.com/api/v1 (upload render at publish time, publish now)
 worker ─> api.telegram.org (optional alerts)
Telegram ⇄ bot (long polling; optional) ─> api over HTTP, like the browser (docs/telegram-bot.md)
```
- `docker compose up`: postgres, migrate (one-shot: `alembic upgrade head` + guarded
  `procrastinate schema --apply`), api, worker. Ports bound to 127.0.0.1. No auth (single user, local).
- Frontend: `npm run dev` on the host (Node 22 installed).
- One Dockerfile, targets `api` and `worker` (worker adds apt ffmpeg + `yt-dlp[default,curl-cffi,deno]`).
  uv `--locked`. psycopg3 everywhere; jobs deferred in the same transaction as their row.
- Render/probe/download tasks are sync `def` (threads, `subprocess.run(timeout=…)`); publish/dispatch/
  sync tasks are `async def` with no blocking calls.

## 3. Data model (statuses as `text` + CHECK; FKs ON DELETE RESTRICT; `*_key` = path under ./data)
- **source_clips**: spec columns + `thumbnail_key`, `video_codec`, `color_transfer`, `size_bytes`,
  `content_type`, `created_at`; status adds `DOWNLOADING`; probe rejects < 3 s or > 15 min.
- **brands**: spec columns; `logo_key` nullable until uploaded (PNG, alpha checked); default overlay
  `{"x":0.72,"y":0.06,"w":0.22,"opacity":1}`.
- **renders**: spec columns + `caption`, `thumbnail_key`, `size_bytes`, `error_code`, `updated_at`,
  `superseded_at` (set by "Re-render and retry"; such a render never shows as unscheduled again),
  `cover_key` (1080x1920 JPEG Reel cover, null ⇒ Instagram's pick; changes only while no live post refers
  to the render); `brand_id` null ⇒ no logo; > 300 MB → FAILED `OUTPUT_TOO_LARGE`.
- **accounts**: `zernio_account_id` unique, `zernio_profile_id`, `username`, `avatar_url`,
  `connection_status` (connected | disconnected), `posting_slots` `{"times":[…]}` (account-local, DST
  gap → forward, overlap → fold=0), `daily_cap` 10, `timezone` NOT NULL, `min_gap_minutes` 30,
  `connected_at`, `last_publish_at`, `disabled_at`, `last_alerts` jsonb. (No tokens stored.)
- **posts**: `render_id`, `account_id`, `caption`, `scheduled_for`, `status` (DRAFT | SCHEDULED |
  PUBLISHING | PUBLISHED | FAILED | DEAD_LETTER | CANCELLED), `zernio_media_url` / `zernio_cover_url` (exact
  URLs reused on every retry), `zernio_post_id`, `ig_media_id`, `permalink`, `attempt_count`, `error_code`,
  `error_detail` jsonb, `alerted_at`, `published_at`, `created_at`, `updated_at`, `idempotency_key` =
  sha256(render_id, account_id, scheduled_for) set once at creation, sent as Zernio `Idempotency-Key`;
  partial unique index `WHERE status NOT IN ('CANCELLED','FAILED','DEAD_LETTER')`.

## 4. Publishing (must never post twice)
```
DRAFT --approve--> SCHEDULED --dispatcher--> PUBLISHING --> PUBLISHED
                     ^  ^                         |
                     |  +-- RATE_LIMITED / >30 min overdue: next free slot, alert once
                     +----- remedy <-- FAILED / DEAD_LETTER
DRAFT/SCHEDULED/FAILED/DEAD_LETTER --cancel / account disabled--> CANCELLED
```
- All writes compare-and-set; API guards: drag/caption → DRAFT|SCHEDULED, approve → DRAFT, cancel →
  DRAFT|SCHEDULED|FAILED|DEAD_LETTER, remedy → FAILED|DEAD_LETTER; 0 rows ⇒ 409.
- **Dispatcher** (every minute, `lock='dispatch'`, no-op unless `PUBLISHING_ENABLED`): SCHEDULED & due,
  or PUBLISHING with no live job (`NOT EXISTS procrastinate_jobs … queueing_lock='post:'||id AND status
  IN ('todo','doing')`). Render PENDING/RENDERING → skip; FAILED → post FAILED `RENDER_FAILED`. > 30 min
  overdue → next free slot + alert. Orphan re-defer `attempt_count += 1`, > 3 → DEAD_LETTER
  `WORKER_CRASHED`. Defer with `queueing_lock` and `lock` = `post:{id}`. Also: UPLOADING clips > 24 h →
  FAILED `UPLOAD_ABANDONED`; every 6 h `GET /v1/accounts` → mark disconnected + alert.
- **publish_post** (async; `RetryStrategy(max_attempts=3, retry_exceptions={NetworkError})`):
  1. Guard: SCHEDULED & due, or PUBLISHING. `ig_media_id` set → PUBLISHED. Account disabled →
     CANCELLED; disconnected → FAILED `ACCOUNT_DISCONNECTED`. Render > `ZERNIO_MAX_REEL_SECONDS` →
     FAILED `TOO_LONG`.
  2. CAS SCHEDULED → PUBLISHING.
  3. `zernio_post_id` set → GET it: published → PUBLISHED; failed → classify; processing → re-defer 1 min.
  4. `zernio_media_url` null → presign, PUT file, commit URL (crash before commit = orphan temp upload,
     auto-deleted by Zernio after 7 days). Then, while `first_post_at` is null, the render's cover (if any)
     the same way into `zernio_cover_url`, sent as `instagramThumbnail`.
  5. Quota exhausted → back to SCHEDULED at next free slot (clear media URL if > 6 days out), RATE_LIMITED.
  6. POST `/v1/posts` with `Idempotency-Key`, same media URL, `publishNow`, read timeout 300 s →
     201: store ids + permalink, PUBLISHED. 207: classify. 409 idempotency_conflict / 429 → re-defer after
     Retry-After. 409 duplicate → GET `existingPostId`, published ⇒ PUBLISHED. Timeout / 5xx / transport →
     NetworkError → retry with the **same key** (Zernio replays the original, no second post).
  7. PUBLISHING with no `zernio_post_id` for > 20 h → DEAD_LETTER (never re-POST outside the 24 h
     idempotency window).
- `classify(errorCategory)` (pure function + assert self-check): auth_expired, account_issue →
  ACCOUNT_DISCONNECTED · platform_rate_limit, quota_exhausted → RATE_LIMITED (auto next slot) ·
  user_content, platform_rejected → CONTENT_REJECTED · platform_error, system_error → NETWORK_ERROR
  (3 retries → DEAD_LETTER) · user_abuse, unknown → UNKNOWN (no retry).
- Remedies: ACCOUNT_DISCONNECTED → "Reconnect" (opens Zernio dashboard; a post failing with it also marks
  the account disconnected, so the next sync that lists it connected moves the account's failed posts to
  the next free slots) · CONTENT_REJECTED → "Re-render and retry" · RATE_LIMITED → automatic ·
  everything else → "Retry now" (if `zernio_post_id` set: GET first — published ⇒ PUBLISHED, failed ⇒
  `POST /v1/posts/{id}/retry`; else SCHEDULED at now).
- Alerts: in-app failed badge + `/recover/:id` always; Telegram when configured (deep link
  `APP_BASE_URL/recover/:id`). Per-post codes alert once; account-level dedupe per (account, code) 6 h.
- Stalled sweeper every minute (`get_stalled_jobs(30)` → `retry_job`, queueing-lock clash →
  `finish_job(FAILED)`, attempts ≥ 3 → FAILED + domain cleanup by task name).
- Debug hook `PUBLISH_DEBUG_PAUSE=after_upload|before_post|after_post` (sleep 60 s). `after_post` proves
  the idempotency replay: kill after Zernio accepted, restart, exactly one Reel.

## 5. API (under `/api`; files under `/media/*`)
| Area | Endpoints |
|---|---|
| Clips | `POST /clips` (multipart: file, rights_status, source_creator_handle?) · `POST /clips/from-url` · `POST /clips/links` (a document or text file → the video links in it) · `POST /clips/from-urls` (bulk; skips videos already in the library; downloads at priority -10, two at a time) · `GET /clips` · `GET /clips/{id}` · `PATCH /clips/{id}` · `POST /clips/{id}/retry` · `DELETE /clips/{id}` |
| Renders | `POST /renders` · `GET /renders?clip_id&status&unscheduled` · `GET /renders/{id}` · `POST /renders/{id}/retry` · `DELETE /renders/{id}` · `PUT`/`DELETE /renders/{id}/cover` |
| Brands | `GET /brands?archived` · `POST /brands` · `PATCH /brands/{id}` · `POST /brands/{id}/logo` (multipart) |
| Accounts | `GET /accounts` (+ cached quota) · `POST /accounts/sync` (pull from Zernio) · `PATCH /accounts/{id}` (slots, cap, tz, min gap, disable) |
| Posts | `GET /posts?from&to&account_id&brand_id&status` · `GET /posts/{id}` · `POST /posts` · `POST /posts/auto-schedule` · `PATCH /posts/{id}` · `POST /posts/{id}/approve` · `POST /posts/{id}/cancel` · `POST /posts/{id}/remedy` |
| System | `GET /health` · `GET /status` |

Validation: POST /posts + auto-schedule reject render not READY, render longer than
`ZERNIO_MAX_REEL_SECONDS`, disabled/disconnected account, a time already passed (now is allowed), rights "none" without
`rights_override` (409 `RIGHTS_NONE`). Drag = time change within a lane. Auto-schedule keeps input order,
skips slots < 10 min away, 30-day horizon, returns unplaced ids. Deletes only when no live post refers
to the row (a render's CANCELLED posts are deleted with it); files removed too.

## 6. Settings (`.env`)
`ZERNIO_API_KEY` (required to publish), `ZERNIO_BASE_URL=https://zernio.com/api/v1`,
`ZERNIO_MAX_REEL_SECONDS=900`, `APP_BASE_URL=http://localhost:5173`, `TELEGRAM_BOT_TOKEN` +
`TELEGRAM_CHAT_ID` (optional), `PUBLISHING_ENABLED=false`, `PUBLISH_DEBUG_PAUSE`, `FFMPEG_THREADS`,
`YTDLP_COOKIES_FILE` (optional), `MAX_UPLOAD_BYTES` (2 GB), `CLIPPER_API_URL` (the api as the bot sees it,
`http://api:8000`). `DATABASE_URL` / `DATA_DIR` set by compose. The Telegram token and chat id also turn on
the bot, which answers that chat only.

## 7. Phases (each ends with acceptance + `docs/phase-N.md` of what could not be verified)
- **Phase 0 — Zernio spike**: `backend/scripts/spike_zernio.py` (httpx; logs every request/response with
  the key redacted): list accounts → publishing limit → make a 9:16 test clip with ffmpeg in Docker →
  presign + upload → POST publishNow with an Idempotency-Key → re-POST the same key and confirm the same
  post id comes back → print permalink. Optional second run with a 2-min clip to test the 90 s limit.
  Responses saved (redacted) as test fixtures. Check: Reel visible logged out, in the grid, cover OK.
- **Phase 1 — skeleton**: compose (postgres, migrate, api, worker), models + Alembic, Procrastinate
  (explicit task names, guarded schema), no-op task proven, stalled sweeper, local storage, `notify()`,
  ffmpeg check at worker start, OpenAPI dump, CLAUDE.md with amended section 1. Acceptance: `docker
  compose up` from scratch → healthy API, worker runs a test job.
- **Phase 2 — ingest + render**: multipart upload, from-url (yt-dlp + error classifier test), probe,
  render (filter builder as a pure function + tests; logo bounding-box test on ffmpeg `testsrc` for
  16:9 / 9:16 / 4:3 ± crop), `python -m app.cli render <clip_id> <brand_id>`.
- **Phase 3 — frontend**: shadcn `init -t vite`, dark only, no localStorage, Geist, hey-api + TanStack
  Query (`npm run gen:api` from committed openapi.json), react-router; Library/Clips + upload, Editor,
  Brands; checked with headless-Chrome screenshots against the mockups.
- **Phase 4 — accounts + scheduling + calendar**: Zernio sync, account cards (connection chip, quota,
  slot editor), posts, auto-schedule, approve, calendar (CSS grid, native drag, quota bars, min-gap
  warning, Ready tray). Publishing disabled; acceptance ends by cancelling the test posts.
- **Phase 5 — publish + recovery**: §4, alerts, `/recover/:id`, remedies, Published tab (filters,
  permalink, "Re-render for…"), failed badge. Acceptance: a post 5 min out publishes unattended; three
  kill-resume runs (after_upload, before_post, after_post) each end with exactly one Reel.
- **Phase 6 — Telegram bot** (`docs/telegram-bot.md`): everything the web app does, from the operator's
  chat. A compose service that calls the api over HTTP (no DB access, no rules of its own); alerts gain
  buttons it answers.
- **Later (not now)**: production — R2 storage, Cloudflare Access + Tunnel, VPS, backups, Zernio
  webhooks.

## 8. UI / UX
Mockups in `docs/design/*.png` stand, with these changes: **Accounts** — the 3-step Meta drawer
becomes "Connect in Zernio (one profile per account) → Sync accounts"; token-expiry chips become
connection-status chips (also in calendar lane headers). **Editor / render queue** — warning when the
clip is longer than the 15 min Reel limit. **Recover** — remedy labels per §4.
Routes: `/library` (Clips | Published), `/editor/:clipId`, `/calendar`, `/accounts`, `/brands`,
`/recover/:postId`.
