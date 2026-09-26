# Phase 5: publish + recovery

**Status: built and tested, but live acceptance is PENDING.** Nothing has been published. The plan's
acceptance needs a real Reel on a real account: a post 5 min out publishes unattended, then three
kill-resume runs each end with exactly one Reel. That waits for the operator to pick the test account
(Instagram posts can't be deleted through Zernio). `PUBLISHING_ENABLED` is false in both the api and
worker containers.

## What was built

Backend (`backend/app/`):
- **`services/publisher.py`**: the only module that calls Zernio write endpoints. `upload` runs
  `POST /v1/media/presign`, then PUTs the bytes (file reads in a thread). `create_post` runs
  `POST /v1/posts` with `publishNow: true` and the post's `Idempotency-Key`; a 409 duplicate resolves to
  the existing post. `get_post` runs `GET /v1/posts/{id}` and `retry_post` runs
  `POST /v1/posts/{id}/retry`, which retries the same Zernio post rather than making a new one.
  `outcome()` branches on `post.status` and classifies only on `platforms[].errorCategory`. 5xx, timeouts
  and transport errors raise `NetworkError`; 409 in progress and 429 raise `Later(seconds)`; the rest
  raise `Rejected(code)`.
- **`tasks/publish.py`**, the state machine. Every change is a compare-and-set.
  - `dispatch`: every minute at priority 10. It returns straight away unless `PUBLISHING_ENABLED` and
    `ZERNIO_API_KEY` are both set. It defers due SCHEDULED posts, moves posts overdue by more than 30 min
    to the next free slot (`MISSED`), and re-defers PUBLISHING posts whose job vanished (up to 3, then
    DEAD_LETTER `WORKER_CRASHED`). Each post is handled in its own try.
  - `publish_post`: async, priority 10, one queued job per post (queueing_lock) and never two running at
    once (lock). It has the same guard. Before the first attempt it checks the render's length (3-90 s)
    and the cached Meta quota; if the quota is used up, the post moves to the next free slot as
    `RATE_LIMITED`. It then uploads, saves `zernio_media_url`, saves `first_post_at` and POSTs. It
    re-POSTs only with the same key and URL, and never more than 20 h after `first_post_at`
    (`WINDOW_EXPIRED`). A 207 `scheduled` result polls. `NetworkError` retries 3 times, then goes to
    DEAD_LETTER `NETWORK_ERROR`. Any unexpected exception becomes FAILED `UNKNOWN` with the error in
    the details.
  - Account alerts (Telegram) are deduplicated per account and code. `PUBLISH_DEBUG_PAUSE`
    (`after_upload` / `before_post` / `after_post`) is there for the kill-resume runs.
- **`services/errors.py`**: `CATEGORY` maps each errorCategory to an error_code. `explain()` gives the
  plain-language cause and one remedy (reconnect / rerender / retry / auto) for `PostOut`.
- **`api/recovery.py`**: `POST /api/posts/{id}/remedy`, where the only publish path is deferring
  `publish_post`. Before a rerender or a retry of an ambiguous failure it asks Zernio (a GET with a 15 s
  timeout). If the post turns out to be published, it is marked PUBLISHED. If the status can't be read,
  it answers 409 `MAYBE_PUBLISHED`. It also returns `PUBLISHING_DISABLED`, `ACCOUNT_UNAVAILABLE` and
  `STATE_CONFLICT` (a live post holds the key). Reconnect re-syncs the account and moves its
  disconnected posts to new slots.
- Migration `0002_posts_first_post_at` adds `posts.first_post_at`, which starts the 20 h clock. The
  config adds `ZERNIO_MIN_REEL_SECONDS=3`.

Frontend (`frontend/src/`):
- **`/recover/:postId`** (`routes/Recover.tsx`, mobile-first, no sidebar):
  - The top shows the status chip and title, the video, the account, the scheduled time in the account's
    zone, and the brand and clip.
  - Below them come the plain cause, one primary remedy button and a line on what it will do.
  - "Technical details" holds the error code, post, render and Zernio ids with Copy buttons, "Restarts
    n / 3" and the raw payload.
  - A re-render when the Reel may already be live asks the operator to check Instagram first. The page
    polls every 5 s while a post is publishing or due soon.
- **Published tab** in the Library (filters, permalink, "Re-render for…"), with the failed badge and
  "N rendering · M scheduled" in the sidebar. The badge opens the oldest failed post's recovery page.

## Acceptance without live publishing (2026-09-26)

1. **Publish test suites**: `docker compose run --rm worker pytest tests/test_publish.py tests/test_classify.py
   tests/test_zernio_parse.py tests/test_slots.py tests/test_scheduling_api.py` gives **90 passed**. The
   full suite gives **149 passed**. Zernio is faked only with `httpx.MockTransport`, serving bodies from
   `tests/fixtures/zernio/`. The tests cover:
   - the happy path, and crashes after upload, after the URL commit, and after a successful POST (the
     replay uses the same key);
   - 207 failed, partial and scheduled; 403 disconnected; 409 duplicate; 429 and `Later`; 5xx;
   - three network retries, then DEAD_LETTER; the 20 h guard; the quota reslot;
   - `test_publishing_disabled_does_nothing`, the dispatcher, and every remedy branch and refusal.
2. **The dispatcher does nothing while `PUBLISHING_ENABLED` is false** (evidence from the live stack):
   - Settings in both containers: `PUBLISHING_ENABLED False`, with the API key set.
   - A SCHEDULED post (id 117, render 78) was seeded via SQL, **2 h overdue**, at 19:28:21Z. With
     publishing on, the next tick would move it to a new slot as `MISSED`, which is visible and involves
     no Zernio call.
   - Two dispatch ticks ran after that, jobs 693 (19:29:00Z) and 695 (19:30:00Z), both at priority 10.
     The worker log shows `Job dispatch[693] ... ended with status: Success, lasted 0.003 s`, and the same
     for 695.
   - Post 117 was still `SCHEDULED` with its original time, `error_code` null, `attempt_count` 0 and
     `updated_at` unchanged.
   - `select count(*) from procrastinate_jobs where task_name='publish_post'` returns **0**: no publish
     job has ever been queued in this database.
   - Post 117 was then deleted.
3. **Recover screen for a FAILED post seeded via SQL**: post 118 (render 75, `CONTENT_REJECTED`) had
   `zernio_post_id` and `error_detail` taken from the 207-failed fixture.
   - `GET /api/posts/118` returns the cause "Instagram rejected the video or caption (format, length or
     policy)." and the remedy `{"action":"rerender","label":"Re-render and retry"}`.
   - The page (Chrome, 390x844, `/tmp/phase4/recover5.mjs`) shows that cause and the "Re-render and
     retry" button, and the expanded Technical details show the Zernio post id.
   - The page sent no non-GET request, and the remedy was never clicked.
   - Post 118 was then deleted. No posts are left apart from the five CANCELLED Phase 4 test posts.
   - Screenshots (real data): `docs/phase-5-recover.png` and `docs/phase-5-recover-expanded.png`.
     `docs/phase-5-published.png` comes from the frontend fixer's harness with mocked posts, because no
     post has ever been published.
4. **Only publish_post and remedy can reach the Zernio write calls** (grep over `backend/app`):
   - The strings `"/posts"` (POST), `"/media/presign"` and `/posts/{id}/retry` appear only in
     `app/services/publisher.py` (lines 86, 113 and 128). The only other POST in `app/` is Telegram in
     `services/notify.py`.
   - `services/zernio.py` makes only `GET /accounts` and `GET /accounts/{id}/instagram/publishing-limit`.
   - `publisher.upload`, `create_post` and `retry_post` are called only inside `tasks/publish.py::_publish`,
     and `_publish` is called only by `publish_post`.
   - `publish_post` is deferred only by `defer_publish` (from `dispatch` after its `PUBLISHING_ENABLED`
     check, from `_resolve` and from `publish_post` itself) and by `api/recovery.py::remedy_post`.
     `publish_post` checks `PUBLISHING_ENABLED` again first.
   - `recovery.py` otherwise calls only `publisher.get_post`, a GET.
   - The frontend never calls Zernio: its only `zernio.com` string is a dashboard link.
   - `scripts/spike_zernio.py` (Phase 0) makes its own presign and draft posts. It is a script the
     operator runs by hand, and nothing imports it.

## Review-fix round (summary)
Phase 5 items from the 29 backend and 28 frontend issues, all fixed:
- **20 h guard**: it counts from `first_post_at`, and the media URL is kept once a POST may have gone out.
- **Re-rendering a post that may be live**: Zernio is asked first, and the answer is 409
  `MAYBE_PUBLISHED` unless the code is `WINDOW_EXPIRED`. The Recover page confirms first.
- **Stale caption**: the post is reloaded after it is claimed.
- **Retry while publishing is off**: it answers 409 `PUBLISHING_DISABLED`.
- **Clean retry**: it is used only when nothing was ever POSTed.
- **Error handling**: an unexpected error becomes FAILED `UNKNOWN` instead of `WORKER_CRASHED`, and one bad
  post doesn't stop the dispatcher.
- **Reel length and quota**: Reels under 3 s fail with `TOO_SHORT`, and the quota is checked before the
  first POST.
- **Scheduling**: publish jobs run at priority 10, and a MISSED reslot no longer beats an operator's move.
- **Disabled accounts**: remedy refuses them with 409 `ACCOUNT_UNAVAILABLE`.
- **Recover page**: the first reconnect click opens Zernio and a second button checks the account. The
  remedy text, titles, "Restarts n / 3", the Zernio id row and named copy buttons were all updated.
- **Tests**: the inline 5xx body was replaced by the fixture `docs_5xx.json`. `Crash` is now a
  `BaseException`.

Integration fixes in the final acceptance: none were needed.

## Could not verify (PENDING the operator's choice of test account)
- **Unattended publish**: a post 5 min out goes from SCHEDULED to PUBLISHED with no one touching it,
  with a permalink and `published_at`, and exactly one Reel on Instagram.
- **Three kill-resume runs** (`PUBLISH_DEBUG_PAUSE` = `after_upload`, `before_post`, `after_post`, killing
  the worker in the pause): each must end with exactly one Reel and the same Idempotency-Key and media
  URL throughout.
- **Real Zernio bodies**: the error, 207 and 409 responses are copied from the docs (`docs_*.json`) and
  have not been confirmed against live responses. Only the account list and publishing-limit bodies are
  live recordings. `docs_5xx.json` is empty because the docs give no 5xx body.
- **Live remedy calls**: reconnect, retry and rerender against Zernio were not run, since each can lead to
  a publish.
- **Telegram alerts**: not sent from a real failure.

To run the live acceptance once an account is chosen: set `PUBLISHING_ENABLED=true` in `.env`, then
recreate the api and worker containers. Schedule one short render about 5 min out on the chosen account
and watch `/api/posts/{id}`. For each pause point, set `PUBLISH_DEBUG_PAUSE`, restart the worker,
`docker compose kill worker` during the 60 s pause, `start worker`, and check Instagram for exactly one
Reel.
