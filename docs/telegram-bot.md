# Telegram bots: the bot service (2026-09-27; many bots per user since 2026-09-28)

How Clipper runs its users' Telegram bots, for the operator and for developers: the supervisor, pairing, the
internal endpoints, health, where alerts go, and what each command does. Each user's own bots (made with @BotFather,
added in **Settings**) are a second front end for Clipper, run as part of the stack and attached to the api. The
user's side is the [Telegram bot guide](guide/08-telegram-bot.md); the data model and the threat model are in
[multi-user.md](multi-user.md#5-telegram-bots). The code is `backend/app/bot/` (`core.py`: the supervisor, polling,
pairing and routing; `screens.py`: every command, button and answer; `fmt.py`, `clients.py`), the user's endpoints are
`backend/app/api/bots.py`, alerts are `backend/app/services/notify.py`.

> [!IMPORTANT]
> Until the release pull request #20 (`dev` -> `prod`) is merged, production runs the old single-operator bots: one
> compose service per bot (`bot`, `bot2`, `bot3`), each reading its token and chat from `.env`. The release imports
> the three into the operator, user 1, already paired, alerts on for the first (`cli bootstrap`), and one `bot`
> service then runs them with everyone else's. The dev site runs this version now.

## 1. Shape

```
Telegram ⇄ bot (compose service, long polling) ──HTTP──> api (:8000, same endpoints as the web app)
                                                          └─ /media/* for thumbnails and MP4s
publisher, api ──sendMessage──> Telegram (alerts, to the user's bots with alerts on; buttons the bot answers)
```

- **One compose service `bot`**, the api's image, `python -m app.bot`: a supervisor that runs every user's bots as
  asyncio tasks in one process. Every 10 s it reports each bot's health (`/api/internal/bots/report`), reads the list
  of bots to run (`/api/internal/bots`), starts new ones, stops removed ones and restarts one whose token changed or
  whose task ended. It is keyed on (id, token), so pairing never restarts a bot. While the api can't be reached, the
  running bots keep running. No auto-reload: after a code change, `docker compose restart bot` (a deploy rebuilds it;
  a crash restarts it, `restart: on-failure`). At most 3 files (videos, documents) are held in its memory at once,
  across all bots.
- **A client of the HTTP API**, as the browser is. It has no database access (a dummy `DATABASE_URL`, no
  `SECRETS_KEY`) and no business logic, so every guard (compare-and-set, slot lock, the 20 h rule, row-level
  security) applies unchanged. Each bot acts as its owner: `Authorization: Bearer $BOT_SERVICE_SECRET` plus
  `X-Clipper-User`. It reads files through `/media/*`. Setting: `CLIPPER_API_URL`, default `http://api:8000`.
- **Long polling.** A bot starts with `getMe` (its @name), `getWebhookInfo` and `deleteWebhook`, then `getUpdates`
  (50 s timeout, `message` + `callback_query` only). There is no webhook: a token whose webhook is set is refused
  when added (`BOT_IN_USE`), since polling would delete another app's. Updates are handled one at a time, in order.
  Slow sends (videos) and imports run as background tasks.
- **Off unless configured.** Without `BOT_SERVICE_SECRET` the service logs "Telegram bots off: set
  BOT_SERVICE_SECRET in .env (api and bot), then docker compose up -d api bot" and exits 0 (`restart: on-failure`, so
  it stays down). A token Telegram refuses (401 or 404) stops that bot only.
- **One poller per token.** Telegram serves updates to one poller. A second process polling the same token (the other
  site, an old stack) gets 409 Conflict; the bot logs "another process polls this bot token", waits 30 s and tries
  again, so the two take turns and Settings shows **Not responding**. A bot belongs to one user
  (`telegram_bots.bot_id` is unique: `BOT_TAKEN`).
- **In-memory UI state only**: open editors, forms, list filters, pending prompts, watched jobs. A bot restart loses
  them (like closing a browser tab), and their buttons answer "This has expired: run the command again." Buttons on
  cards carry their ids in `callback_data`, so they keep working across restarts.

### Endpoints

The user's, behind the session cookie (**Settings** › **Telegram bots**) or the bot bearer:

| Route | Does |
|---|---|
| `GET /api/me/bots` | The user's bots with their health |
| `POST /api/me/bots {token}` | Checks the token's shape, then Telegram `getMe` and `getWebhookInfo`; seals it; hands out a pairing code. Errors: `TOKEN_REJECTED` 422, `TELEGRAM_ERROR` 502, `BOT_IN_USE` 409, `BOT_TAKEN` 409, `SECRETS_KEY_MISSING` 503. The user's own bot with a new token keeps its chat |
| `POST /api/me/bots/{id}/pair` | A new code (the old one stops working); the current chat answers until another chat uses it |
| `PATCH /api/me/bots/{id} {alerts}` | The **Alerts** switch |
| `POST /api/me/bots/{id}/test` | The api itself sends "Clipper test message: this bot works." to the paired chat (so it proves the token and the chat, not the bot service); `BOT_NOT_PAIRED` 409 before pairing |
| `DELETE /api/me/bots/{id}` | Removes it; the supervisor stops it within about 10 s |

The bot service's own, under `/api/internal` (not in the OpenAPI schema; Caddy answers 404 for them from outside):

| Route | Does |
|---|---|
| `GET /api/internal/bots` | Every bot to run: id, owner, token, paired chat (or none), and `ver`, a fingerprint of the sealed token. Leaves out rejected tokens and disabled users (`bots_for_supervisor()`, migration 0009) |
| `POST /api/internal/bots/report` | The tick's report: the bots polling fine (`last_seen_at`, their @name) and the ones Telegram refused (`TOKEN_REJECTED`), applied only where the sealed token still matches `ver` (`report_bots()`) |
| `POST /api/internal/bots/{id}/pair` | A `/start <code>` from a private chat, sent as the bot's owner: with the right, unexpired code that chat becomes the bot's; else 404 `PAIR_CODE_INVALID` |

Without the bearer: 401 `BOT_SERVICE_ONLY` (403 with a wrong one).

### Health

What **Settings** shows for each bot (`auth.bot_out`), checked in this order:

| Label | When | What to do |
|---|---|---|
| **Token rejected** | Telegram answered 401 or 404 for the token (`error`) | Its owner sends @BotFather `/token`, picks the bot and pastes the fresh token in **Add another bot**: it keeps its chat. Or **Remove** |
| **Waiting for Start** | No paired chat yet | Open the pairing link and tap **Start** |
| **Running** | The supervisor reported it polling within the last 90 s | – |
| **Not responding** | Anything else, never seen included | `docker compose logs bot`: the service is down or has no `BOT_SERVICE_SECRET`, or another process polls the token |

A paired bot reads **Running** within about 20 s of the service starting (the supervisor's first ticks, then
Settings' reload: every 10 s, every 2 s while a pairing code is out).

## 2. Pairing and security

- **One chat per bot, by pairing.** Adding a bot hands out a code (`token_urlsafe(16)`, 15 min, stored as its
  sha256) and the link `https://t.me/<bot>?start=<code>`. `/start <code>` from a *private* chat with the right,
  unexpired code makes that chat the bot's (`/api/internal/bots/{id}/pair`, as the owner). The bot answers
  "**Paired.** This chat runs your Clipper now. /help lists what I do." and sets its command menu for that chat.
  **Re-pair** issues a new code; the current chat keeps working until another chat uses it. Only updates from the
  paired chat are handled; everything else (group chats, wrong or expired codes, strangers) is ignored without a reply
  and logged with its chat id.
- **Nothing stale runs.** At startup a paired bot drops updates that queued while it was down (a "Post now"
  tapped hours ago must not publish now). If any were dropped, it says so once ("Back online. …"). An unpaired bot
  keeps them: the `/start <code>` may be waiting there. Pairing doesn't restart the bot.
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
| Sidebar: API / database / worker / publisher / publishing state (and why it is off), rendering + scheduled counts, failed badge | `/status` |
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

Not in the bot, by design: sign-in, **Setup** and **Settings** (the Zernio key, the bots themselves, the password,
storage), the **Captions** and **Covers** tabs of Customizations and a render's cover, free-drag logo and crop
placement, a live preview before rendering (the render's thumbnail and video are the preview), videos over 20 MB from
the phone, and upload progress bars. The card of a post that failed on the Zernio key links to **Settings** (**Open
in Clipper**).

## 5. Commands

`/status` · `/clips [text]` (also `/library`) · `/renders` · `/ready` (also `/queue`) · `/calendar` (also `/week`) ·
`/drafts` · `/failed` · `/published [text]` · `/brands` · `/accounts` · `/help` (also `/start`) · `/cancel` (drops
the pending question). Registered with `setMyCommands` for the bot's paired chat. Lists end each line with a tappable
id command: `/c12` clip, `/r34` render, `/p56` post, `/b4` brand, `/a1` account. Anything else: "I don't know that
command. /help lists them."

`/status` puts the sidebar's state into words, with the reason publishing is off: "PUBLISHING_ENABLED is off on the
server", "you have no Zernio key yet: add it in Settings" or "Zernio refused your key: update it in Settings".

## 6. Flows

**Import.** Video → "Import 1 video (12.3 MB)?" [Import] [Cancel].
Over 20 MB → a note saying why, and what to do instead. The clip card follows when probing ends. A link
already in the library → its card instead. Several links or a document → "Found 14 videos (TikTok 6 ·
Instagram 8): 3 already in the library, 2 repeats. Import 11?" [Import] → `POST /api/clips/from-urls` (behind every
single job, and one media job per user at a time) → one summary when all 11 are Ready or Failed, with each failure's
cause.

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

The default brand is the one this clip was last rendered with, else the user picks one first. The
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
gap, Disable (asks first; cancels its drafts and scheduled posts, and its failed ones too) / Enable, Reconnect in
Zernio (link) and Calendar.

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
- **Alerts** go to the user the alert is about (`notify(user_id, …)`), through every bot of theirs that is paired,
  has **Alerts** on and a token Telegram takes, unless the user is disabled. A user with no such bot gets none (the
  web app's failed badge and Recover page still show everything). The bot service doesn't send them: the publisher
  (and the api, for an account sync it runs) calls Telegram's `sendMessage` with the bot's token directly, and the
  bot service answers the buttons. Alerts from one dispatch tick go out together at its end, so a slow Telegram never
  delays a post.

| Alert | When | How often | Buttons |
|---|---|---|---|
| "**@account** post 234 failed: *cause*" (or "dead letter", or "moved to a new slot") | A post fails, dead-letters, or moves to the next free slot | A failure once per post; account-wide causes (`ACCOUNT_DISCONNECTED`, `RATE_LIMITED`, `PROFILE_OVER_LIMIT`) once per account per 6 h; moves every time | **Open post** (the bot's post card, with the remedy), **Open in Clipper** (`/recover/<id>`) |
| "Publishing is paused: *reason*. Update your Zernio key in Settings, then retry the failed posts." | Zernio refuses the user's key while publishing or in the 6-hourly account sync | Once per valid → invalid flip | **Open in Clipper** (`/settings`) |
| "Instagram account **@account** is disconnected in Zernio. Reconnect it there, then Sync accounts in Clipper." | An account sync finds it disconnected | Once per account per 6 h | **Reconnect in Zernio**, **Sync accounts**, **Open in Clipper** (`/accounts`) |

**Open in Clipper** is a button only when `APP_BASE_URL` is https (Telegram refuses other button URLs); otherwise the
link is added to the text. Both sites are https; so on the dev site, alerts link to the dev site.

## 8. Running it

On the VM, in the site's checkout (`~/clipper` or `~/clipper-dev`). Each site runs its own bot service for its own
users' bots.

```sh
docker compose logs -f bot       # the supervisor and every bot: pairing, ignored chats (with their chat id), errors
docker compose restart bot       # after a code change on a dev stack (a deploy rebuilds it)
```

What the log says:

| Line | Means |
|---|---|
| `bot service: running every user's Telegram bots (the api's list, every 10 s)` | Started |
| `bot <id> @<name> answering chat <chat>` (or `none: waiting for /start <code>`) | That bot is polling |
| `bot <id> paired with chat <chat>` | A `/start <code>` worked |
| `bot <id>: pairing from chat <chat> refused: …` | A wrong or expired code |
| `bot <id> ignored an update from chat <chat> (it answers …)` | A chat other than the paired one wrote to it |
| `getUpdates: … (another process polls this bot token); again in 30 s` | The same token runs somewhere else too: the other site, or an old stack |
| `bot <id>: Telegram rejected the token (…): stopped until its owner pastes a new one` | **Token rejected** |
| `bot list: …` | The api can't be reached: the running bots carry on |

**One site per token.** Never give the dev site a token production uses: the two services would take turns and both
show **Not responding**. The dev site's refresh deletes every copied bot, and drops a bot of a dev site account when
production runs the same one.

**The operator checking a user's bots**: in `psql`, `select user_id, id, username, chat_id is not null as paired,
alerts, error, last_seen_at from telegram_bots order by 1, 2;` ([multi-user.md](multi-user.md#11-operator-runbook)).
The operator can't sign in as a user or pair their bot.

## 9. Tests

`backend/tests/test_bot.py` (pytest in the worker container) covers the pure helpers (time parsing, the snap grid and
caption template against the web app's `geometry.ts` / `utils.ts` rules, crop windows, `callback_data` ≤ 64 bytes),
and the flows end to end. The flows drive the bot with real api calls (httpx ASGI transport, the `clipper_test`
database) and a recorded fake Telegram. They cover: another chat ignored; link import → clip DOWNLOADING; video upload
→ clip PROBING; document import → bulk; render editor → render PENDING with the snapped overlay; schedule → draft →
approve; a typed time; Post now (refused while publishing is off; from a post card and a render card); move, caption
and cancel; remedy; account slots / timezone; brand create + logo PNG. Zernio is never called (no key); nothing
publishes (no worker on `clipper_test`). `test_telegram.py` covers the bots' endpoints, pairing and the supervisor.

When the bot was built (2026-09-27) every screen was also sent once to the operator's chat, silently, then deleted,
so Telegram's own HTML and keyboard validation passed. There are no live publishes without the operator's go-ahead.

## 10. Later (not now)

A preview frame (worker job) before rendering · a local Bot API server (2 GB files) · a daily digest ·
publish notifications for scheduled posts · webhook mode.
