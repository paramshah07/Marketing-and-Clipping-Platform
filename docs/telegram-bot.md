# Telegram bot: spec (2026-09-27; many bots per user since 2026-09-29)

Each user's own Telegram bots (made with @BotFather, added in Settings) are a second front end for Clipper.
Everything the web app does can be done from the chat, and the bots run as part of the stack, attached to the
api. The operator's three bots (@Postyclipper_bot, which sends failure alerts, and the two interactive ones) were
imported once from `.env` into user 1 (`cli bootstrap`) and work as before.

## 1. Shape

```
Telegram ⇄ bot (compose service, long polling) ──HTTP──> api (:8000, same endpoints as the web app)
                                                          └─ /media/* for thumbnails and MP4s
worker ──sendMessage──> Telegram (alerts, now with buttons the bot answers)
```

- **One compose service `bot`**, the api's image, `python -m app.bot`: a supervisor that runs every user's
  bots as asyncio tasks in one process. Every 10 s it reports each bot's health and reads the list of bots to
  run (`/api/internal/bots`), starting new ones, stopping removed ones and restarting one whose token changed.
  No auto-reload: after a code change, `docker compose restart bot` (a crash restarts it, `restart: on-failure`).
  At most 3 files (videos, documents) are held in its memory at once, across all bots.
- **A client of the HTTP API**, as the browser is. It has no database access (a dummy `DATABASE_URL`, no
  `SECRETS_KEY`) and no business logic, so every guard (compare-and-set, slot lock, the 20 h rule, row-level
  security) applies unchanged. Each bot acts as its owner: `Authorization: Bearer $BOT_SERVICE_SECRET` plus
  `X-Clipper-User`. It reads files through `/media/*`. Setting: `CLIPPER_API_URL`, default `http://api:8000`.
- **Long polling** (`getUpdates`, 50 s timeout, `message` + `callback_query` only). There is no webhook
  (a token whose webhook is set is refused when added: `BOT_IN_USE`). Updates are handled one at a time, in
  order. Slow sends (videos) and imports run as background tasks.
- **Off unless configured.** Without `BOT_SERVICE_SECRET` the service logs why and exits 0 (`restart:
  on-failure`, so it stays down). A token Telegram refuses stops that bot only; Settings shows it as "Token
  rejected" until its owner pastes a new one.
- **One poller per token.** Telegram serves updates to one poller. A second stack polling the same token
  gets 409 Conflict, waits 30 s and tries again, so the two would take turns (the bot shows "Not responding").
  A bot belongs to one user (`telegram_bots.bot_id` is unique: `BOT_TAKEN`).
- **Health** (Settings): *Running* (polled Telegram within 90 s), *Waiting for Start* (not paired), *Token
  rejected*, *Not responding*. "Send test message" is sent by the api itself.
- **In-memory UI state only**: open editors, forms, list filters, pending prompts, watched jobs. A bot
  restart loses them (like closing a browser tab), and their buttons answer "This expired". Buttons on
  cards carry their ids in `callback_data`, so they keep working across restarts.

## 2. Security

- **One chat per bot, by pairing.** Adding a bot hands out a code (15 min, stored as its sha256) and the link
  `https://t.me/<bot>?start=<code>`. `/start <code>` from a *private* chat with the right, unexpired code makes
  that chat the bot's (`/api/internal/bots/{id}/pair`, as the owner). Re-pair issues a new code; the current
  chat keeps working until another chat uses it. Only updates from the paired chat are handled; everything
  else (group chats, wrong codes) is ignored without a reply and logged with its chat id.
- **Nothing stale runs.** At startup a paired bot drops updates that queued while it was down (a "Post now"
  tapped hours ago must not publish now). If any were dropped, it says so once. An unpaired bot keeps them:
  the `/start <code>` may be waiting there. Pairing doesn't restart the bot.
- **At most once.** Each batch is confirmed to Telegram before it is handled, so a crash can't replay a
  tap. A confirm button acts once: a double tap or an old message's button does nothing.
- **Same confirmations as the web app**: delete clip, delete render, cancel/dismiss post, disable account,
  post now, approve all drafts, and re-render when the Reel may be live.
- The token is never logged (httpx request logging stays at WARNING), and chat ids are not secrets.

## 3. Telegram limits that shape it (Bot API 10.3)

| Limit | Value | Consequence |
|---|---|---|
| Bot download (`getFile`) | 20 MB | Larger videos: upload in the web app or send the link. Documents for Import links: 20 MB, the same as the api's limit |
| Bot upload (`sendVideo`) | 50 MB | "Watch" sends renders/sources up to 50 MB; larger ones: download in the web app |
| `callback_data` | 1-64 bytes | Short verbs + ids (`p:123`), and in-memory state keyed by message id for forms |
| Message text / media caption | 4096 / 1024 chars | Captions are shown shortened on cards |
| Callback toast | 200 chars | Errors show as an alert dialog, cut to 200 |
| Photos sent as photos | recompressed to JPEG | Logos must be sent as a **file**, or the alpha channel is lost |

## 4. Parity with the web app

| Web app | Bot |
|---|---|
| Sidebar: API / database / worker / publishing state, rendering + scheduled counts, failed badge | `/status` |
| Library: clip table, search | `/clips [text]`, 10 per page, tap `/c12` to open |
| Upload videos (creator handle) | Send one or more videos (an album too) → Import. A caption starting with `@name` sets the handle |
| Import a URL (handle) | Send a message with one video link (optionally with an `@handle`) → Import |
| Import links from a document or pasted text | Send a document (docx, xlsx, pptx, odt, txt, csv, md, rtf, html) or a message with several links → summary → Import. One message when the whole import has finished |
| Retry · Remove | Clip card buttons |
| Published tab: account / brand / range filters, search, permalink, "Re-render for…" | `/published [text]` with filter buttons; post card: Instagram link, Re-render for… |
| Editor: brand, logo 3x3 snap grid, scale, opacity, crop, caption from the brand template, hashtag and length limits, Save as brand default, Render | Render editor (one message, edited in place). Margin is fixed at the web default 4%. No free drag: grid positions, ±2% size steps, opacity 100/75/50/25, crop window centre / left / right (top / bottom for tall sources) |
| Render queue: status, preview, download, retry, log, delete | `/renders`, render card: Watch (sends the MP4), Retry, Log, Delete. A render started from the bot reports when it finishes |
| Schedule popover: account, suggested slot, other time, caption, Schedule, Post now | Schedule form on the render card |
| Calendar week board per account, free slots, quota, cap and gap | `/calendar`: one account's week, day by day (its posts, then its free slots on one line), with week navigation |
| Drag a render onto a slot / drag a post to move it | Schedule form day + slot picker / post card Move… |
| Ready tray: select, Auto-schedule, unplaced reasons | `/ready`: select, Auto-schedule to an account |
| Approve, Approve all drafts | Post card Approve; `/drafts` Approve all (lists them first) |
| Post drawer: caption, time, approve, post now, cancel, player, permalink | Post card |
| Accounts: sync, timezone, slots, daily cap, min gap, disable / enable, reconnect link | `/accounts`, account card |
| Brands: list, archived, create, name, link, caption template, auto-approve, logo, default placement, archive | `/brands`, brand card, placement editor |
| Recover: cause, one remedy, dismiss, technical details, play | Post card of a failed post (from `/failed` or the alert) |
| Telegram alerts (link only) | Same alerts plus an **Open post** / **Sync accounts** button handled by the bot |

Not in the bot, by design: free-drag logo and crop placement, a live preview before rendering (the
render's thumbnail and video are the preview), videos over 20 MB from the phone, and upload progress
bars.

## 5. Commands

`/status` · `/clips [text]` · `/renders` · `/ready` · `/calendar` · `/drafts` · `/failed` ·
`/published [text]` · `/brands` · `/accounts` · `/help` (also `/start`) · `/cancel` (drops the pending
question). Registered with `setMyCommands` for the bot's paired chat. Lists end each line with a tappable
id command: `/c12` clip, `/r34` render, `/p56` post, `/b4` brand, `/a1` account.

## 6. Flows

**Import.** Video → "Import 1 video (12.3 MB)?" [Import] [Cancel].
Over 20 MB → a note saying why, and what to do instead. The clip card follows when probing ends. A link
already in the library → its card instead. Several links or a document → "Found 14 videos (TikTok 6 ·
Instagram 8): 3 already in the library, 2 repeats. Import 11?" [Import] → `POST /clips/from-urls` (low
priority, two at a time) → one summary when all 11 are Ready or Failed, with each failure's cause.

**Clip card** (thumbnail): name, source, handle, duration · size · fps · audio, status (cause if failed),
render count. Buttons: Render… · Renders (n) · Creator · Watch source · Retry (failed, retryable) · Remove
(Ready or Failed, no renders).

**Render editor** (clip thumbnail + settings, edited in place):

```
[ Brand: Northwind Coffee ]
[ ↖ ][ ↑ ][ ↗ ]      logo position (snaps inside the IG safe zone, 4% margin)
[ ← ][ · ][ → ]
[ ↙ ][ ↓ ][ ↘ ]
[ − ][ 22% ][ + ][ Opacity 100% ]
[ Crop: centre ][ Caption ]
[ Render ][ Save as default ][ Close ]
```

The default brand is the one this clip was last rendered with, else the operator picks one first. The
caption comes from the brand template (`{link}`, `{creator}`), editable, max 2200 characters and 30
hashtags. Render queues it; the editor stays open for another variant. When the render finishes, its card
arrives.

**Render card** (render thumbnail, so the logo shows): brand, placement ("Top right · 22% · 9:16
crop"), duration · size, status, caption. Buttons: Watch · Schedule… · Post now · Retry + Log (failed) ·
Delete.

**Schedule form**: account (connected, enabled; picker if more than one), suggested time (`next-slot`),
caption. [Schedule for Sun 27 19:00] [Other time…] [Post now]. Other time → day buttons (next 8 days in
the account's zone) → that day's posting slots (taken ones marked) + "Type a time" (`18:30`, `tomorrow
6:30pm`, `2026-10-02 09:00`, `fri 13:00`, `now`). It warns about the min gap and daily cap as the
calendar does. A draft result offers Approve. Post now needs publishing on, asks
once, creates the post at the current second and approves it, then reports "Live on Instagram" with the
link (or the failure).

**Post card** (render thumbnail): status, @account, time in the account's zone, brand · clip ·
duration, caption, and cause + Zernio's message when failed, or the Instagram link when published.
Buttons by status:
- DRAFT: Approve · Move… · Caption · Post now · Cancel post
- SCHEDULED: Move… · Caption · Post now · Cancel post
- FAILED / DEAD_LETTER: the remedy (Reconnect in Zernio + "I've reconnected: check now" · Re-render and
  retry · Retry now) · Details · Dismiss. `TOO_LONG` and `auto` get no remedy button, as on the web.
- PUBLISHED: View on Instagram · Re-render for…

**`/calendar`**: one account (the picker remembers the last one), a week from today in its zone. Each day
lists its posts in time order (status, brand, clip, `/p56`), then its free slots on one line (free as the
web board counts them: ahead, not taken, outside the min gap, or "full" at the daily cap), and the header
shows Today n / cap and the Zernio quota. Buttons: ◀ · This week · ▶ · account.

**`/ready`**: READY renders with no live post. Toggle each; Select all / Clear; Auto-schedule n →
account (picker). Result: placed times, drafts to approve, and unplaced reasons.

**`/drafts`**: every draft, oldest first. Approve all lists up to 15, says how many are past due (they
move to the next free slot), then approves one by one and reports failures.

**`/failed`**, **`/published`**: lists of post cards. `/published` filters: 7 / 30 / 90 / 365 days,
account, brand, text.

**Accounts**: `/accounts` lists them with connection, zone, slots, today n / cap, quota, next post. Sync
accounts. The account card has Slots (one tap: every hour 07:00–23:00, the default for new accounts; every
30 min; every 2 hours; 3 a day; or type your own, `09:00 13:00 19:00`), Timezone (IANA name), Daily cap, Min
gap, Disable (confirm: cancels its drafts and scheduled posts) / Enable, Reconnect in Zernio (link) and
Calendar.

**Brands**: `/brands` (Show archived toggle, New brand). The brand card has the logo, template, link,
auto-approve and default placement. Buttons: Name · Template · Link · Auto-approve on/off · Logo (then
send the PNG as a file) · Default placement (the editor's grid, size and opacity) · Archive / Unarchive.

**Free-text answers** use a ForceReply prompt. The next plain text message answers the latest prompt (for
10 min); `-` clears an optional field; a command or `/cancel` drops it.

## 7. Notifications

- **Watches** (in memory): the bot follows what it started. It polls every 3 s and reports once:
  - clip Ready / Failed (its card);
  - render Ready / Failed (its card);
  - a bulk import when every clip has finished (one summary);
  - a "Post now" post: Published with the link, Failed with its card, or moved to a new slot.
  Watches expire after 2 h (posts after 1 h).
- **Worker alerts** keep their text and link and gain buttons: a post alert gets **Open post** (the post
  card, where the remedy is); an account disconnect gets **Sync accounts** and **Reconnect in Zernio**. They go
  to every paired bot of the user the alert is about with its alerts switch on (`notify(user_id, …)`).

## 8. Changes outside the bot

- `compose.yml`: the `bot` service.
- `config.py`: `CLIPPER_API_URL`.
- `notify()`: optional callback button rows. `publish._finish` and the account sync pass them.
- `ClipsFromUrlsOut` gains `ids` (the created clips), so the bot can follow a bulk import. This needs
  openapi.json and `npm run gen:api` again.
- Docs: PLAN.md (architecture, settings), CLAUDE.md, README.md, .env.example.

## 9. Tests and acceptance

- `backend/tests/test_bot.py` (pytest in the worker container): the pure helpers (time parsing, the
  snap grid and caption template against the web app's `geometry.ts` / `utils.ts` rules, crop windows,
  `callback_data` ≤ 64 bytes), and the flows end to end. The flows drive the bot with real api calls
  (httpx ASGI transport, the `clipper_test` database) and a recorded fake Telegram. They cover: another
  chat ignored; link import → clip DOWNLOADING; video upload → clip PROBING; document import → bulk;
  render editor → render PENDING with the snapped overlay; schedule → draft → approve; a typed time; Post
  now (refused while publishing is off; from a post card and a render card); move, caption and cancel;
  remedy; account slots / timezone; brand create + logo PNG. Zernio is never called (no key); nothing
  publishes (no worker on `clipper_test`).
- Live: the stack with the bot, `getMe` / `setMyCommands` OK, and every screen sent once to the operator
  chat silently (then deleted), so Telegram's own HTML and keyboard validation passes. There are no live
  publishes without the operator's go-ahead.

## 10. Later (not now)

A preview frame (worker job) before rendering · a local Bot API server (2 GB files) · a daily digest ·
publish notifications for scheduled posts · webhook mode when Clipper leaves localhost.
