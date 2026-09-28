> **Historical record**, kept as written and partly out of date. For current documentation, start at [docs/README.md](README.md).

# Clipper: Build Prompts

How to use this: paste section 1 into `CLAUDE.md` at the repo root so it persists across every session. Then run sections 2 through 7 one at a time, in order, as individual prompts. Do not skip ahead. Each phase has acceptance criteria and you should not move on until they pass.

---

## 1. CLAUDE.md (persistent context, paste once)

```markdown
# Clipper

An internal tool for one operator. It takes video files, overlays an advertiser
logo, and publishes them as Instagram Reels to the operator's own Instagram
accounts on a schedule.

Single user. No multi-tenancy, no billing, no org model, no third-party OAuth.
Runs self-hosted on one VPS behind Cloudflare Access.

## Stack

Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic
Queue: Procrastinate (Postgres-backed, no Redis anywhere in this project)
Database: PostgreSQL 16
Storage: Cloudflare R2 via boto3
Video: ffmpeg and ffprobe invoked with subprocess, no Python wrapper libraries
Frontend: React 18, Vite, TypeScript, Tailwind, shadcn/ui
Tables: TanStack Table. Data fetching: TanStack Query
API client: generated from the FastAPI OpenAPI schema with @hey-api/openapi-ts
Deploy: docker-compose with services api, worker, postgres, frontend, caddy

## Repo layout

/backend
  /app
    /api          FastAPI routers
    /models       SQLAlchemy models
    /schemas      Pydantic schemas
    /tasks        Procrastinate tasks
    /services     business logic (instagram.py, render.py, storage.py)
    /core         config, security, db session
  /alembic
  /tests
/frontend
  /src
    /components
    /routes
    /api          generated client
    /lib
/docker
  Dockerfile.api
  Dockerfile.worker
compose.yml

## Hard constraints the code must respect

1. Instagram fetches the video from a public URL. It does not accept uploaded
   bytes on the Instagram Login auth path. Every rendered file must be
   reachable at an HTTPS URL at publish time.

2. Every rendered MP4 must be encoded with `-movflags +faststart`. Without the
   moov atom at the front, Meta's fetch is slow and unreliable and produces
   container ERROR states that are hard to diagnose.

3. Publishing is a two-call sequence. First create a media container, then
   publish it. Persist the returned container ID to the database immediately,
   inside the same transaction boundary, before doing anything else. A crash
   between the two calls followed by a naive retry creates a second container
   and therefore a duplicate live post.

4. Media containers expire 24 hours after creation. On an EXPIRED status, fail
   the job cleanly. Never publish a stale container.

5. Long-lived access tokens expire after 60 days and must be refreshed while
   still valid. A token that lapses cannot be refreshed and requires full
   re-authentication.

6. Overlay and crop geometry is stored as fractions of frame dimensions, never
   absolute pixels. This keeps configs portable across source resolutions.

7. Signed R2 URLs must be generated at publish time, not at render time. A
   render made on Monday for a Thursday post will have a dead URL by then.

## Do not

- Do not mock or stub the Instagram API. If you cannot test a call, say so and
  stop rather than writing a fake that passes.
- Do not use ffmpeg.wasm or any browser-side video encoding.
- Do not build a timeline editor, trimming UI, or multi-clip sequencing.
- Do not add Redis, Celery, or any broker other than Postgres.
- Do not run ffmpeg anywhere except the long-running worker container.
- Do not use localStorage or sessionStorage for anything that matters.
- Do not add user accounts, login pages, roles or permissions.
- Do not retry a publish without first checking for an existing container ID on
  the row.
- Do not invent Meta API endpoint names or parameters. If you are unsure of the
  exact shape of a call, stop and ask.

## Design direction

Dark-first. This is a tool used at night to queue tomorrow's posts, and video
thumbnails read better on dark surfaces.

Dense, not airy. This is a working surface, not a marketing page. Table rows
should be compact enough that fifteen fit on screen. Resist the default
shadcn spacing, which is tuned for landing pages.

One accent colour, used only for state and primary actions. Everything else is
neutral. No gradients, no glassmorphism, no rounded-3xl cards, no hero
sections, no illustrations.

Typography: Inter Tight or Geist for UI, tabular numerals for anything showing
times, counts or durations. Monospace only for IDs and error codes.

Video is the content, so give thumbnails real size and keep the chrome around
them minimal. A 9:16 thumbnail at 64px wide in a table row is the unit.

Motion is limited to state transitions that communicate something, such as a
row moving from rendering to ready. No decorative animation.
```

---

## 2. Phase 0: Publish spike

This phase has no UI, no database and no web server. It exists to prove the
Meta integration works before you build anything around it.

```
Write a single standalone Python script, backend/scripts/spike_publish.py,
that publishes one Instagram Reel using the Instagram API with Instagram
Login. Everything is hardcoded or read from environment variables. No
database, no queue, no framework.

The script must:

1. Read IG_USER_ID, IG_ACCESS_TOKEN and VIDEO_URL from environment variables.
   VIDEO_URL is an already-uploaded, publicly reachable MP4.

2. Call GET /{IG_USER_ID}/content_publishing_limit against graph.instagram.com
   and print the remaining quota. Abort if the quota is exhausted.

3. Create a media container: POST /{IG_USER_ID}/media with media_type=REELS,
   video_url and a caption. Print the returned container ID immediately.

4. Poll GET /{container_id}?fields=status_code with exponential backoff
   starting at 5 seconds, capping the interval at 30 seconds and the total
   wait at 10 minutes. Print each status transition. Handle all five status
   values: IN_PROGRESS, FINISHED, ERROR, EXPIRED, PUBLISHED.

5. On FINISHED, call POST /{IG_USER_ID}/media_publish with creation_id and
   print the returned media ID.

6. On ERROR or EXPIRED, print the full error payload and exit non-zero.

Use httpx. Log every request and response body. Do not swallow exceptions.
Do not add retry logic beyond the polling loop.

Also write backend/scripts/README_SPIKE.md documenting exactly how to get an
IG_ACCESS_TOKEN: creating the Meta app, assigning the Instagram Tester role,
accepting the invite inside the Instagram app, running Business Login for
Instagram, and exchanging the short-lived token for a long-lived one with
grant_type=ig_exchange_token.
```

**Acceptance:** a Reel appears on the target account. Nothing else in this project matters until that happens.

**If it fails:** the failure mode tells you what to fix. A permissions error means the Tester role or the OAuth scopes are wrong. A container ERROR means the video does not meet spec. A 400 on the container call means the URL is not reachable from Meta's side, which is usually a signed URL that has already expired.

---

## 3. Phase 1: Backend skeleton and data model

```
Set up the backend project structure and data model. No business logic yet.

1. FastAPI app with a health endpoint, CORS configured for the Vite dev server,
   and settings loaded from environment via pydantic-settings.

2. SQLAlchemy 2.0 models and an initial Alembic migration for these tables:

   source_clips: id, origin (enum: 'upload' | 'url'), source_url (nullable),
   original_filename (nullable), platform (nullable), source_creator_handle
   (nullable), rights_status (enum: 'permission_granted' | 'none' |
   'own_content', default 'none'), raw_key (R2 object key), duration_s,
   width, height, fps, has_audio, has_watermark (nullable), uploaded_at,
   status (enum: 'UPLOADING' | 'PROBING' | 'READY' | 'FAILED'), error_code
   (nullable), error_detail (nullable)

   brands: id, name, logo_key, default_overlay_config jsonb, caption_template
   (nullable), link (nullable), auto_approve boolean default false,
   created_at, archived_at (nullable)

   renders: id, source_clip_id FK, brand_id FK nullable, overlay_config jsonb,
   crop_config jsonb nullable, output_key nullable, status (enum: 'PENDING' |
   'RENDERING' | 'READY' | 'FAILED'), duration_s nullable, ffmpeg_log nullable,
   created_at, completed_at nullable

   accounts: id, ig_user_id unique, username, avatar_url, access_token_enc
   bytea, token_expires_at, last_refreshed_at nullable, posting_slots jsonb,
   daily_cap int default 10, timezone text, connected_at, last_publish_at
   nullable, disabled_at nullable

   posts: id, render_id FK, account_id FK, caption, scheduled_for timestamptz,
   status (enum: 'DRAFT' | 'SCHEDULED' | 'CREATING_CONTAINER' |
   'POLLING_CONTAINER' | 'PUBLISHING' | 'PUBLISHED' | 'FAILED' |
   'DEAD_LETTER'), ig_container_id nullable, ig_media_id nullable,
   container_created_at nullable, attempt_count int default 0, error_code
   nullable, error_detail nullable, published_at nullable, idempotency_key
   text unique not null

   Index posts on (status, scheduled_for) and on (account_id, scheduled_for).

3. Procrastinate configured against the same Postgres database, with its
   schema applied via its own migration command. Register one no-op task and
   prove the worker picks it up.

4. A storage service wrapping boto3 for R2: put_object, generate_presigned_url
   with configurable expiry, and delete_object. Read endpoint, keys and bucket
   from settings.

5. Token encryption helpers using cryptography Fernet, with the key from
   settings. Write a test that round-trips a token.

6. docker-compose with postgres, api and worker. The worker image must have
   ffmpeg and ffprobe installed and verifiable with a version check on startup.

Write pytest tests for the storage service against moto or a local MinIO, and
for the Fernet helpers. Do not write tests that hit R2 or Meta.
```

**Acceptance:** `docker compose up` gives you a healthy API, a worker that consumes a test job, and migrations that apply cleanly from scratch.

---

## 4. Phase 2: Upload and render pipeline

```
Build the ingest and render pipeline. Still no frontend.

Upload flow:
1. POST /clips/upload-url returns a presigned R2 PUT URL plus a new
   source_clips row in UPLOADING status. The browser uploads directly to R2,
   so the file never passes through the API. Accept mp4, mov and webm.
2. POST /clips/{id}/complete marks the upload finished and enqueues a probe
   task.
3. Also implement POST /clips/from-url which accepts a video URL and enqueues
   a download task using yt-dlp as a subprocess. Keep this path fully separate
   from the upload path. Map yt-dlp failures to distinct error codes: PRIVATE,
   REMOVED, GEO_BLOCKED, EXTRACTOR_FAILED.

Probe task:
Run ffprobe -v quiet -print_format json -show_streams -show_format against the
R2 object via its presigned URL. Parse duration, width, height, fps, codec and
audio presence into the row. Generate a thumbnail at the 1 second mark, upload
it to R2, and store its key. Set status READY or FAILED.

Render task:
Given a render row with overlay_config and optional crop_config, build and run
a single ffmpeg command. Geometry in both configs is fractional, so convert to
pixels against the target 1080x1920 frame at render time.

The command shape is:

ffmpeg -y -i input.mp4 -i logo.png -filter_complex \
"[0:v]crop=<computed>,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg]; \
 [1:v]scale=iw*<logo_scale>:-1,format=rgba,colorchannelmixer=aa=<opacity>[logo]; \
 [bg][logo]overlay=x=<computed>:y=<computed>[v]" \
-map "[v]" -map 0:a? \
-c:v libx264 -profile:v high -pix_fmt yuv420p -crf 20 -preset medium \
-c:a aac -b:a 128k -ar 44100 \
-movflags +faststart output.mp4

Omit the first crop filter entirely when crop_config is null. Capture stderr
into ffmpeg_log on both success and failure. Upload the output to R2 and set
status READY. Set a Procrastinate retry strategy of 3 attempts with
exponential backoff.

Write a CLI entrypoint, python -m app.cli render <clip_id> <brand_id>, that
runs the whole pipeline end to end without the API. Use it to verify output
against real files before wiring up any UI.
```

**Acceptance:** you can upload a file with curl, probe it, render it with a logo and download an MP4 that plays correctly with the logo where you expect it.

---

## 5. Phase 3: Frontend shell, upload and the overlay editor

```
Build the React frontend. Vite, TypeScript, Tailwind, shadcn/ui, dark-first
per the design direction in CLAUDE.md.

Generate the API client from the FastAPI OpenAPI schema using
@hey-api/openapi-ts with the TanStack Query plugin. Add an npm script that
regenerates it, and run it now.

Routes: /library (default), /editor/:clipId, /calendar, /accounts, /brands

Upload screen:
A drop zone that accepts multiple files at once. Each file gets its own row
with a progress bar. Request a presigned URL per file, PUT directly to R2 with
XHR so you get progress events, then call the complete endpoint. Rows poll for
probe status and fill in with a thumbnail, duration and resolution as they
become ready. Failed rows show a distinct cause and a retry button.

Overlay editor, the important screen:
This is NOT a video editor. Nothing is encoded in the browser.

Layout is the video on the left, controls on the right.

The video plays in an HTML5 video element at 9:16, constrained to roughly 420px
tall. Over it, absolutely positioned:
- A draggable, resizable logo image. Store its position and size as fractions
  of the container, not pixels.
- An optional crop rectangle with drag handles, also stored fractionally.
- A non-interactive Instagram chrome overlay showing the caption block bottom
  left, the action rail right and the audio ticker along the bottom, at the
  correct proportions. Render it at roughly 40% opacity so the operator can see
  what will be occluded without it obstructing the edit. Add a toggle to hide
  it.

Controls panel: brand selector which loads that brand's default overlay config,
logo scale slider, opacity slider, nine-point position snap grid, aspect ratio
presets for the crop, and a caption field with the brand's template
pre-filled.

A Render button POSTs the config and creates a render row. The row appears in a
right-hand queue showing RENDERING, then READY with a playable output preview.

The preview must update instantly on every drag and slider change, because it
is pure DOM. Never call the server for a preview.
```

**Acceptance:** you can upload a video, drag a logo onto it, see the Instagram chrome overlay, hit render and get back a real encoded MP4 that matches the preview.

---

## 6. Phase 4: Accounts, scheduling and calendar

```
Account connection:
A three-step guided flow with checkmarks. Step one links out to the Meta app
dashboard for assigning the Instagram Tester role. Step two explains accepting
the invite inside Instagram under Settings, Website Permissions, Tester
Invites. Step three runs Business Login for Instagram, exchanges the
short-lived token for a long-lived one server-side, and verifies by calling
/me and content_publishing_limit before saving. Store the token Fernet
encrypted.

Account cards show username, avatar, a token expiry chip coloured green above
14 days, amber from 7 to 14 and red below 7, posts remaining today from the
live quota endpoint and the last successful publish time.

Add a Procrastinate periodic task that runs daily and refreshes any token
expiring within 15 days using grant_type=ig_refresh_token. Jitter the schedule
so accounts connected on the same day do not all refresh in the same minute.
Log every refresh. Alert on failure.

Posting slots:
Each account has a slots config, for example three fixed times per day in that
account's timezone, plus a daily cap. Build a small editor for it on the
account card.

Scheduling:
POST /posts creates a scheduled post from a render, an account, a caption and
a time. Generate the idempotency_key deterministically from render_id,
account_id and scheduled_for. Enqueue a Procrastinate job with schedule_at set
to scheduled_for.

Add POST /posts/auto-schedule which takes a list of render IDs and an account,
then assigns each to the next free slot on that account respecting the daily
cap and a minimum gap.

Calendar:
Week view with one horizontal swimlane per account. Use FullCalendar's resource
timeline, which requires a commercial licence, or build the equivalent on a CSS
grid if you would rather not buy one. Cards show the thumbnail, time and
status. Dragging a card cancels the existing job and enqueues a new one at the
new time. Show a per-day quota bar per lane and warn when two posts on the same
account fall within 30 minutes of each other.
```

**Acceptance:** you can connect a real account, define its slots, auto-schedule five renders into them and see them laid out correctly by lane.

---

## 7. Phase 5: Publish worker and failure recovery

```
Implement the publish task as an explicit state machine over the posts table.
Transitions are SCHEDULED, CREATING_CONTAINER, POLLING_CONTAINER, PUBLISHING,
PUBLISHED, with FAILED and DEAD_LETTER as terminals.

The task must:

1. Re-check the token expiry before doing anything. If expired, transition to
   FAILED with error_code TOKEN_EXPIRED and alert immediately.

2. Check content_publishing_limit. If exhausted, transition to FAILED with
   error_code RATE_LIMITED and reschedule to the account's next free slot
   automatically.

3. Generate a fresh presigned R2 URL now, at publish time, with a 24 hour
   expiry.

4. If ig_container_id is already set on the row, skip creation and resume
   polling. This is the idempotency guard and it is the single most important
   line in the file.

5. Create the container, then write ig_container_id and container_created_at
   to the database before any other operation.

6. Poll with exponential backoff. Abort if container_created_at is more than 23
   hours ago.

7. Publish, store ig_media_id and published_at, transition to PUBLISHED.

Error handling. Map failures to error codes and remedies:

TOKEN_EXPIRED       -> notify, remedy is Reconnect account
CONTAINER_ERROR     -> notify, remedy is Re-render and retry
CONTAINER_EXPIRED   -> notify, remedy is Re-render and retry
RATE_LIMITED        -> notify, remedy is Reschedule, applied automatically
MEDIA_UNREACHABLE   -> notify, remedy is Retry now
NETWORK_ERROR       -> no notification, auto-retry up to 3 times with backoff
UNKNOWN             -> notify, remedy is Retry now

After exhausting retries a post moves to DEAD_LETTER.

Alerts go to a Telegram bot webhook. The message contains the account username,
the plain-language cause and a deep link to the recovery screen. Do not send
alerts for NETWORK_ERROR.

Recovery screen at /recover/:postId:
One screen, mobile-first because it is opened from a phone notification at
night. It shows the thumbnail, the account, the scheduled time, the cause
written in plain language rather than a raw API payload, and exactly one
primary button carrying the mapped remedy. Put the raw error payload behind a
collapsed disclosure for debugging.

Library screen:
The published log, filterable by account, brand and date range, with links out
to the live Instagram posts. Each row offers Re-render for a different brand,
which creates a new render from the same source clip without re-uploading.
```

**Acceptance:** a post scheduled five minutes out publishes unattended. Kill the worker mid-publish and restart it, and confirm it resumes from the stored container ID rather than creating a second one.

---

## Notes on running these

Run Phase 0 yourself rather than delegating it. It is thirty lines of code and
the entire project depends on the answer, so you want to see the failure
messages directly.

After each phase, before moving on, ask the agent to write down what it could
not verify. That list is where your bugs will be.

The agent will want to build the UI first because it is the most visible
progress. Do not let it.
