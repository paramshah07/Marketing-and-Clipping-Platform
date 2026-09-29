# Clipper

**Turn viral clips into branded Instagram Reels, and have them published on schedule, from a browser or from
Telegram.**

> **Live app:** [https://145-241-239-46.sslip.io](https://145-241-239-46.sslip.io)
>
> Sign up there with a username and password while spots are left (15 users in all). You bring your own
> [Zernio](https://zernio.com) account for publishing. Questions: pjsrsns@gmail.com.

![Clipper's calendar: a week of branded Reels in each account's posting slots, with finished renders waiting in the Ready to schedule tray](docs/images/hero.png)

*The Calendar: a week of branded Reels in the account's posting slots, and finished renders waiting on the
right. Taken on a review copy of production's data, so the footer reads **Publishing off**.*

## The problem

Instagram clip pages repost short, popular videos, and advertisers pay them to carry their logo and link. The
pages live on volume: every night means dozens of short videos pulled from TikTok, YouTube, Instagram, X and
Facebook, each stamped with the right advertiser's logo, captioned with that advertiser's link and a credit to
the original creator, exported in a format Instagram accepts, and posted at the right times on the right
accounts.

By hand, that is a production line of small steps where any slip costs money or a slot:

- **Collecting** means downloading clips one by one from five different sites.
- **Branding** means opening every clip in a video editor to place a logo, guessing where Instagram's own
  buttons will cover it.
- **Exporting** means getting Reels' exact format right every time: 9:16 at 1080x1920, H.264 and AAC, and
  converting iPhone HDR footage so it doesn't look washed out.
- **Captions** get retyped for every post, and a missing advertiser link or creator credit can cost a payment
  or a creator's goodwill.
- **Posting** happens at odd hours, within Instagram's daily limits, with no undo: a Reel posted twice can't
  be deleted through the API.
- **Failures** are silent. A rejected upload at 2 AM is noticed the next day, after its slot has passed.
- **Publishing by API** normally needs an approved Meta developer app, which a one-person operation may not
  be able to get.

## Who it's for

A handful of people, each running their own Instagram pages and placing advertisers' logos and links on short
clips. Each user signs up with a username and password, brings their own Zernio API key and their own Telegram bots,
and sees only their own clips, brands, accounts and posts. There are no roles, teams or billing: it is a working tool
for the person doing the posting, made for the late-night session where tomorrow's posts get queued.

## How Clipper solves it

```mermaid
flowchart LR
  collect["Collect<br/>files, links,<br/>whole documents"] --> brand["Brand<br/>logo, crop,<br/>cover, caption"]
  brand --> render["Render<br/>a Reels-ready<br/>1080x1920 MP4"]
  render --> schedule["Schedule<br/>posting slots,<br/>Auto-schedule"]
  schedule --> publish["Publish<br/>at the slot,<br/>never twice"]
  publish --> recover["Recover<br/>alerts and<br/>one-tap fixes"]
```

| The pain | What Clipper does |
|---|---|
| Collecting clips from five sites | Upload files, paste a link, or drop in a whole document of links. Clipper finds every TikTok, Instagram, YouTube, X and Facebook video in it, skips the ones already in the library and downloads the rest in the background. |
| Placing logos by eye | Each brand keeps its logo and a default placement. The Editor shows the clip exactly as it will render, with Instagram's buttons and safe zone drawn on top, so it is easy to keep the logo clear of them; the position grid snaps it inside the safe zone. |
| Getting the format right | Every render is a Reels-ready 1080x1920 H.264 MP4 with AAC audio (a silent track when the source has none), and HDR phone footage is converted to normal colour. |
| Retyping captions | Brands carry caption templates whose `{link}` and `{creator}` are filled in for you. Saved captions and covers live in **Customizations**, each with a default the Editor starts from. |
| Posting on time, within limits | Each account has posting slots, a daily cap and a minimum gap in its own time zone. **Auto-schedule** fills the next free slots. Posts wait as drafts for your approval unless their brand is set to auto-approve. Before each post Clipper checks Instagram's daily quota, and moves the post to the next free slot rather than let it fail. |
| Never posting twice | Each post keeps the idempotency key of its first attempt, so Zernio recognises a retry after a network error as the same post and never creates a second Reel. |
| Silent failures | A failed post raises an alert from your Telegram bots and a badge in the app. Its **Recover** page offers the one fix that applies: retry, re-render, or reconnect the account. |
| No Meta developer app | Publishing goes through [Zernio](https://zernio.com), which holds its own approved Meta app, with each user's own Zernio key. Clipper never talks to Meta directly. |

## Two front doors, the same powers

Clipper can be run from the web app or from Telegram. Each user adds their own bots, made with @BotFather, in
**Settings**, as many as they like, and pairs each with one private chat. A bot calls the same API as the browser, as
its owner, so every rule and check applies the same way wherever a tap comes from, and it only ever sees its owner's
data. Failure alerts arrive in the chats of the bots with alerts on, with the fix one tap away. (The operator's
bots, **@Postyclipper_bot**, **@Clipspammerbot** and **@Autoposter_giftok_bot**, carried over as they were.)

```mermaid
flowchart LR
  web(["Web app<br/>any browser"])
  bots(["Your Telegram bots<br/>one chat each"])
  subgraph powers ["The same capabilities"]
    caps["Import clips<br/>Edit and render<br/>Schedule and approve<br/>Follow along<br/>Recover failed posts<br/>Manage accounts and brands"]
  end
  web --> caps
  bots --> caps
  caps --> api["Clipper API<br/>one set of rules,<br/>your data only"]
  api --> zernio["Zernio<br/>your key"] --> ig(["Instagram<br/>Reels"])
```

A few things stay in the browser:

- **Free placement and preview.** The Editor drags the logo and crop freely with a live preview; the bots use
  a position grid and size steps, and the render is the preview.
- **Covers and Customizations.** Choosing a Reel's cover, saved captions and covers, and the default brand,
  caption and cover the Editor starts from.
- **Big files.** Telegram bots can't receive videos over 20 MB, so send the link instead.
- **Settings.** Your Zernio key, your bots and your password are managed in the browser.

The full side-by-side is in [docs/telegram-bot.md](docs/telegram-bot.md#4-parity-with-the-web-app); how to use
the bots, with example chats, is in the [Telegram bot guide](docs/guide/08-telegram-bot.md).

## A night with Clipper

After [signing up and setting up](docs/guide/01-getting-started.md) once:

1. **Collect.** Paste tomorrow's list of links into **Import links** in the [Library](docs/guide/02-library.md),
   or send the document to a bot.
2. **Brand.** Open each clip in the [Editor](docs/guide/03-editor.md). The default brand, caption and cover
   are already in place: adjust the logo or crop if you like, then **Render**.
3. **Schedule.** Select the finished renders in the [Calendar](docs/guide/05-calendar.md) and
   **Auto-schedule** them into the account's slots. Approve the drafts.
4. **Sleep.** Each Reel goes out at its slot. If one fails, Telegram says which and offers the fix
   ([Publishing and recovery](docs/guide/07-publishing-and-recovery.md)).

Every step, with diagrams: [docs/workflows.md](docs/workflows.md).

## Documentation

| Read | For |
|---|---|
| [User guide](docs/guide/README.md) | Every page of the app, with annotated screenshots. Start at [Getting started](docs/guide/01-getting-started.md): sign up and set up. |
| [Workflows](docs/workflows.md) | The nightly routine, a failed post, adding an account, shipping a change, backups. |
| [Deploy runbook](docs/deploy.md) | The VM, branches and releases, the automatic deploy, backups and restore, trying a pull request. |
| [Multi-user](docs/multi-user.md) | Users, isolation, secrets, each user's Zernio key and bots, the threat model, the operator's commands. |
| [All documentation](docs/README.md) | The full index, with reference and historical records. |

---

## Engineering

### Architecture

```mermaid
flowchart LR
  browser(["Browser"]) -->|"HTTPS, sign-in"| caddy["Caddy"] --> api["api<br/>FastAPI + the React app"]
  tg(["Telegram"]) <-->|"long polling"| bots["bot service<br/>every user's bots,<br/>clients of the api"] -->|"HTTP, as each owner"| api
  api --> db[("Postgres<br/>data + job queue,<br/>row-level security")]
  db --> worker["worker<br/>ffmpeg, yt-dlp"]
  db --> publisher["publisher<br/>dispatcher every minute"]
  publisher -->|"upload + publish,<br/>each user's key"| zernio["Zernio API"] --> ig(["Instagram"])
  api -->|"key checks, sync, quota"| zernio
  publisher -.->|"alerts"| tg
```

Everything runs as one Docker Compose stack. Caddy is the only public entry point and adds HTTPS; the app signs users
in itself. The api keeps its records in Postgres and the video files in `./data`, one folder per user, and queues
renders, downloads and publishes as jobs in the same database. The worker runs renders and downloads, one job per
user at a time so no user waits behind another's batch; the publisher, every minute, publishes whatever is due.

Choices that keep it simple and safe:

- **One database, no broker.** Postgres holds the data and the job queue ([Procrastinate](https://procrastinate.readthedocs.io)).
  A job is queued in the same transaction as the row that needs it, so neither exists without the other.
- **Isolation in the database.** Every user-owned table has Postgres row-level security, and the api connects as a
  role that can't bypass it, so a query that forgets to filter by user returns nothing rather than someone else's
  rows. Each user's Zernio key and bot tokens are stored encrypted ([docs/multi-user.md](docs/multi-user.md)).
- **Publishing that can't double-post.** A post's idempotency key is saved before the first call to Zernio and
  reused on every retry, every post state change is a compare-and-set, nothing is re-sent more than 20 hours
  after the first attempt, well inside Zernio's 24-hour replay window, and nothing is re-sent under another Zernio key.
- **The preview is the render.** Logo and crop positions are stored as fractions of the frame, and the same
  math runs in the browser (`geometry.ts`) and in ffmpeg (`render.py`). Unit tests and an end-to-end run that
  compares a rendered frame with the on-screen preview keep the two in step.
- **Bots are clients, not a second backend.** They have no database access and call the api like the browser
  does, as their owner, so every rule lives in one place.

Stack: Python 3.12 · FastAPI · SQLAlchemy 2 · Procrastinate · PostgreSQL 16 · ffmpeg · yt-dlp · React 19 ·
Vite · Tailwind v4 · TanStack Query · Caddy · Docker Compose. More in [docs/PLAN.md](docs/PLAN.md).

### Deployment

- **Production** is one Oracle Cloud Always Free Arm VM (2 OCPU, 12 GB) running the stack with
  `compose.prod.yml` on top: code and the built frontend baked into the images, Caddy on ports 80 and 443,
  everything else on 127.0.0.1.
- **Branches.** Changes land on `dev` (the default branch) through pull requests, checked by CI (backend tests,
  frontend type check, lint, tests and build). A `dev` → `prod` pull request is a release.
- **Every push to `prod` deploys.** The **Deploy** GitHub Actions workflow SSHes into the VM with a key that
  can only run `git pull && sh deploy.sh`; `deploy.sh` makes the app's secrets on its first run, rebuilds what
  changed and fails the run unless the api answers within two minutes.
- **Backups:** a nightly database dump on the VM, the last seven kept.

The runbook, from server setup to restore: [docs/deploy.md](docs/deploy.md).

### Local development

> [!WARNING]
> Never run `docker compose up` with a `.env` that holds the production Zernio key and bot tokens (the
> operator's Mac has one): a local stack would publish the same schedule as the VM and fight its bots. Never put
> `SECRETS_KEY` in such a `.env` either. Use `./review.sh`.

`./review.sh` copies production's database and the operator's files to your machine (it only reads from the VM,
and drops every other user's rows) and runs your branch as a separate `clipper-review` stack with publishing off,
keys blanked and no bots. Sign in as the operator; the script prints how to set a password on the copy. You need
Docker, Node 22 and SSH access to the VM.

```sh
./review.sh                                   # api on http://127.0.0.1:8000
(cd frontend && npm install && npm run dev)   # the app on http://localhost:5173
./review.sh down                              # remove the review stack and its database
```

Tests:

```sh
docker compose run --rm worker pytest                         # backend and bots; starts only Postgres (+ migrate), no bots, no publishing
cd frontend && npm test && npm run typecheck && npm run lint  # frontend
```

Shipping a change: open a pull request into `dev`, try it with `gh pr checkout <number> && ./review.sh`, merge, then
release with a `dev` → `prod` pull request and check the live app ([workflows](docs/workflows.md#shipping-a-change)).
The rules the code must follow, and every command: [CLAUDE.md](CLAUDE.md).

### Project layout

```
backend/            FastAPI api, Procrastinate tasks (app/tasks), Telegram bot service (app/bot), Alembic, tests
  openapi.json      the API schema; the frontend client is generated from it
frontend/           React app: src/routes, src/components, src/lib, src/api (generated, never edited by hand)
  e2e/              Playwright: the acceptance run and the documentation screenshots
docs/               user guide, workflows, runbook, reference, historical records
compose.yml         the stack: postgres, migrate, api, worker, publisher, bot
compose.prod.yml    production overlay: Caddy, code baked into the images, the app's secrets required
compose.review.yml  review overlay: publishing off, keys and secrets blanked, no bots
Caddyfile           HTTPS in front of production; hides the bot service's internal routes
deploy.sh           run on the VM by the Deploy workflow; adds the app's secrets to .env once
review.sh           your branch on your machine, against a copy of production (the operator's data only)
.github/workflows/  ci.yml (tests on every pull request), deploy.yml (every push to prod)
```
