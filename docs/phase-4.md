# Phase 4: accounts, scheduling, calendar

Spec: "connect a real account, define its slots, auto-schedule five renders into them and see them laid
out correctly by lane". Publishing stays off (`PUBLISHING_ENABLED` false in api and worker) and the run
ends by cancelling every post it made.

## What was built

Backend (`backend/app/`):
- **Zernio read client** (`services/zernio.py`): `GET /v1/accounts` (Instagram accounts only;
  connected = `isActive` and not `needsReconnection`) and
  `GET /v1/accounts/{id}/instagram/publishing-limit` (cached 5 min, `quota: null` when Zernio can't be
  reached). There are no write calls in this module.
- **Account sync** (`tasks/accounts.py`): `POST /api/accounts/sync` plus a periodic `sync_accounts`
  every 6 h. An advisory lock stops the two from running at once. When an account comes back from
  disconnected to connected, its `ACCOUNT_DISCONNECTED` posts are moved to new slots.
- **Slot engine** (`services/slots.py`, pure and tested in `tests/test_slots.py`): "HH:MM" slot times
  in each account's IANA timezone. The engine handles DST, the daily cap on the account's local day and
  the min gap, with a 30-day horizon. Automatic placement skips slots less than 10 min away. Every time
  pick or move holds the account row lock (`SELECT ... FOR UPDATE`) until commit.
- **Scheduling API** (`api/scheduling.py`): `GET/PATCH /accounts` (slots, cap, timezone, min gap,
  disable, which cancels live, FAILED and DEAD_LETTER posts) and `GET /accounts/{id}/next-slot` (null
  for disabled or disconnected accounts). The post routes are `GET/POST /posts`, `GET/PATCH /posts/{id}`,
  `POST /posts/auto-schedule`, `/approve` and `/cancel`. Every state change is a compare-and-set. Error
  codes: `RIGHTS_NONE`, `SLOT_TAKEN`, `STATE_CONFLICT`, `RENDER_NOT_READY`, `TOO_LONG`, `TOO_SHORT`,
  `ACCOUNT_UNAVAILABLE` and `TOO_SOON`.
- `tzdata` was added so legacy zone names such as `Asia/Calcutta` resolve in the slim image.

Frontend (`frontend/src/`):
- **Accounts** (`routes/Accounts.tsx`): cards show the connection chip, Meta quota, today count/cap,
  next post, timezone select with UTC offset, slot chips (add/remove, 24 h HH:MM), daily cap, min gap and
  disable/enable. There is also a Sync button and a "Connect in Zernio, then Sync" drawer.
- **Calendar** (`routes/Calendar.tsx`, `components/CalendarBits.tsx`, `ScheduleTray.tsx`,
  `PostDrawer.tsx`): a week grid with one lane per account. Cells use the account's local date, and
  slot boxes and cards show times in the account's zone. Drafts use a dashed border. Cards can be dragged
  natively onto a slot or a day, with an optimistic update and rollback on error. There are a day
  meter (n/cap), min-gap warnings and "Approve all drafts" for the week. The Ready tray lists unscheduled
  READY renders and has an account picker, Auto-schedule and a next-free-slot line; a rights confirm lists
  clip names. The post drawer handles date, time, caption, Save, Approve and Cancel post.
- Shared helpers are in `lib/schedule.ts` (zoned time maths, week, drop time, too-close), tested in
  `schedule.test.ts`, including Auckland and Chatham DST cases.

## Acceptance (2026-09-26, from the current working tree)

### Build, types, tests
- `npm run gen:api`: no diff against the working tree. The only diff against HEAD is the intended
  contract change (`PostOut.zernio_post_id`, `SystemStatus.rendering_renders` / `scheduled_posts`).
- `npm run typecheck`, `npm run lint` and `npm run build` are clean. `npm test` passes 25 of 25.
- `docker compose up -d --build`, then `kill worker && start worker`. `docker compose run --rm worker
  pytest` gives **149 passed** (default `clipper_test` db).
- Phase 3 regression: `node e2e/accept.mjs` **PASS** (worst deviation 0.16 %). It overwrites the
  committed `docs/phase-3-*.png`, so those were restored from HEAD afterwards.

### Live run (Playwright, Google Chrome, 1440x900, real api + dev server, no mocks)
Script: `/tmp/phase4/accept4.mjs` (not committed; log in `/tmp/phase4/run1.log`).
1. **Live sync**: the Accounts page Sync button calls `POST /api/accounts/sync` and gets 200 with
   `@i.cant.de connected zernio=6ab77f6a7d5baf4adc07a655 quota={"used":0,"total":100,"duration_s":86400}`.
   The live quota shows Zernio's publishing-limit read works.
2. **Slots**: in the slot editor, removing 13:00 and adding it back each send a PATCH. The API then
   returns `["09:00","13:00","19:00"]` (Europe/London).
3. **Auto-schedule five renders** (78, 75, 62, 59, 48; each 8 s, so 3-90 s; five different brands): ticking
   them in the tray and pressing "Auto-schedule 5" returns "Placed 5" with nothing unplaced. The first post
   matches `next-slot`, all five are DRAFT, and they fill consecutive free slots:
   ```
   post 112 render 78  2026-09-27T08:00:00Z = Sun 27 09:00 Europe/London
   post 113 render 75  2026-09-27T12:00:00Z = Sun 27 13:00
   post 114 render 62  2026-09-27T18:00:00Z = Sun 27 19:00
   post 115 render 59  2026-09-28T08:00:00Z = Mon 28 09:00
   post 116 render 48  2026-09-28T12:00:00Z = Mon 28 13:00
   ```
   The run was at 20:26 London time, so Saturday's 19:00 slot had already passed and was skipped.
4. **Layout by lane**: for each post, the card sits in cell `[data-cell="<account>|<local date>"]` of
   the account's lane in the right week, and its time label equals the local slot time from the API.
5. **Drag + reload**: post 116 was dragged from Mon 28 13:00 onto the Tue 29 19:00 slot box. The PATCH
   returned 200, and after a page reload the card is in the Tue 29 cell showing 19:00; the API gives
   `2026-09-29T18:00:00Z`. The layout check passes again.
6. **Approve**: post 112 was approved from the drawer, then "Approve all drafts" was used on each week.
   All five are SCHEDULED at unchanged times, and the layout check passes again.
7. **Cleanup**: post 112 was cancelled from the drawer ("Cancel post" and confirm), the other four through
   `POST /api/posts/{id}/cancel`. All five (112-116) are **CANCELLED**, and their renders are back in the
   tray. Nothing was deferred to `publish_post` (no `publish_post` job has ever existed in this database).

Screenshots (real data, 1440x900): `docs/phase-4-calendar.png` (week of 21 Sep, the three Sunday posts
after approve), `docs/phase-4-calendar-next-week.png` (Mon 28 09:00 and the dragged Tue 29 19:00) and
`docs/phase-4-accounts.png` (the account after the run: quota 0/100, "Next tomorrow 09:00", slots).
`docs/phase-4-accounts-connect.png` is from the frontend fixer's screenshot harness, with accounts
mocked in the browser.

## Review-fix round (summary)
The review raised 29 backend issues and 28 frontend issues, and all were fixed or handled. Phase 4 items:
- **Double-booking**: parallel creates and time moves now get 409 `SLOT_TAKEN` under the account lock.
- **Idempotency key**: a moved post now keeps a unique key (a counter is added on collision), and
  `POST /posts` replays only on an exact match of render, account and time.
- **Other backend fixes**: repeated render ids are placed once, `next_post_at` ignores past drafts, and
  `next-slot` and approve refuse unusable accounts. `tzdata` was added for legacy zone names. MISSED
  reslots no longer overwrite an operator's move, disabling an account also cancels FAILED and
  DEAD_LETTER posts, and sync reslots reconnected accounts.
- **Frontend fixes**: the drawer re-seeds its fields and loads its own post, so it survives cross-week
  moves. Published cards fill their slot, and the rights confirm lists clip names. Legacy Chrome zone
  names are mapped, and `zonedToUtc` probes ±1 day. Lanes size to their content, drawers are overlays
  with Escape and a backdrop, and focus rings, copy and tabular numbers were fixed.
- **Contract additions**: `PostOut.zernio_post_id`, `SystemStatus.rendering_renders` and
  `scheduled_posts`. The sidebar footer reads "N rendering · M scheduled".

Integration fixes in the final acceptance: none were needed.

## Could not verify
- **More than one lane**: Zernio has one connected Instagram account (`@i.cant.de`), so the layout was
  checked on one lane with real data. Multi-lane layout, including a lane in a different timezone, is
  covered only by the mocked screenshot harness and `schedule.test.ts`.
- **A disconnected account coming back**: the reslot on sync is covered by tests only, because the live
  account stayed connected.
- **Manual moves**: the daily cap and min gap are not enforced for manual moves (by design; the
  calendar warns instead).
