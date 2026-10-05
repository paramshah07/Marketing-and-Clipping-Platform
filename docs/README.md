# Clipper documentation

Everything written about Clipper, grouped by who it's for. New here? Read the [project README](../README.md), then
[Getting started](guide/01-getting-started.md) to use Clipper or [CONTRIBUTING.md](../CONTRIBUTING.md) to work on it.

These docs follow the `dev` branch. Production (https://145-241-239-46.sslip.io, the `prod` branch) gets each change
at the next release, and has signed users in itself since the multi-user release of 2026-10-05. The two sites:
[README › Environments](../README.md#environments).

## For users

How to use the app, page by page, with annotated screenshots. Reading order: [guide/README.md](guide/README.md).

| Page | What it covers |
|---|---|
| [Getting started](guide/01-getting-started.md) | Which site to use, creating your account, signing in and out, **Set up Clipper** (Zernio key, Instagram, Telegram), the layout and status footer, first-time setup, a day in Clipper |
| [Library](guide/02-library.md) | Uploading, importing from a link or a document, clip statuses, search, deleting many clips at once, the **Published** tab and **Free up space** |
| [Editor](guide/03-editor.md) | The stage, logo placement, crop, filters, music, cover, caption, rendering and the render cards |
| [Customizations](guide/04-customizations.md) | Brands, saved captions, saved covers, songs, and how the Editor applies the defaults |
| [Calendar](guide/05-calendar.md) | The week board, scheduling, **Auto-schedule**, drafts and approval, the post drawer |
| [Accounts](guide/06-accounts.md) | Syncing from your Zernio account, time zone, posting slots, daily cap, minimum gap, disabling |
| [Publishing and recovery](guide/07-publishing-and-recovery.md) | What happens at slot time, post statuses, the Recover page, alerts, a refused Zernio key, limits |
| [Telegram bot](guide/08-telegram-bot.md) | Running Clipper from Telegram: commands, the render editor and its defaults, cards and alerts in the chat (adding a bot: Settings) |
| [Settings and your account](guide/09-settings.md) | Your Zernio API key, your Instagram accounts, your Telegram bots, storage, password and **Log out**, and what each message means |
| [Troubleshooting and FAQ](guide/10-troubleshooting.md) | Can't sign in, signups full, a refused key, no Instagram accounts, a silent bot, storage full, refused links, a video already posted, slow renders, the dev site |
| [Workflows](workflows.md) | End-to-end flows as diagrams with steps: your first day, the nightly routine (in the browser or from your phone), a failed post, cleaning up your library, adding an Instagram account or a Telegram bot, a new Zernio key (and, for maintainers, shipping a change and backups) |

## For developers

| Document | What it is |
|---|---|
| [CONTRIBUTING.md](../CONTRIBUTING.md) | From a fresh clone to a merged pull request: the safety rules, a local stack, tests, making changes, the branch workflow, the checklist |
| [CLAUDE.md](../CLAUDE.md) | The stack, the repo layout, every command, and the hard constraints and "Do not" list the code must follow |
| [PLAN.md](PLAN.md) | The implementation plan: Zernio facts, data model, the publishing state machine, API, settings. Its later revisions are listed at the top; multi-user is section 9 |
| [multi-user.md](multi-user.md) | Users, sessions, row-level security, secrets, each user's Zernio key and bots, quotas, fairness, threat model, capacity |
| [telegram-bot.md](telegram-bot.md) | The Telegram bots' design: the supervisor, pairing, security, Telegram's limits, parity with the web app |
| [design/BRIEF.md](design/BRIEF.md) | The UI design contract, with its mockups (`design/*.html`, `*.png`). It predates Zernio and users: where it describes Meta tokens, [PLAN.md](PLAN.md) section 8 wins, and the sign-in, setup and settings pages, the Editor's filters and music and the Music tab have no mockup |
| [backend/tests/fixtures/zernio/README.md](../backend/tests/fixtures/zernio/README.md) | The only Zernio responses tests may serve: which are live recordings and which are copies from Zernio's docs |
| [backend/openapi.json](../backend/openapi.json) | The API schema; `frontend/src/api` is generated from it |
| [.env.example](../.env.example) | The settings a stack reads from `.env`, each explained; every default is in `backend/app/core/config.py` |
| [Workflows › Shipping a change](workflows.md#shipping-a-change) | The branch, pull request and release flow as a diagram |

## For operators

Server access is the maintainers' alone.

| Document | Use it to |
|---|---|
| [deploy.md](deploy.md) | Set up the VM, branches and releases, CI, the automatic deploys, the multi-user release (its record, rollback), back up and restore, try a pull request with `./review.sh`, run the dev site (section 7) |
| [multi-user.md › Operator runbook](multi-user.md#11-operator-runbook) | Manage users, passwords, quotas and signups; rotate `SECRETS_KEY` and `BOT_SERVICE_SECRET`; check a user's setup |
| [Workflows › Backups and restore](workflows.md#backups-and-restore) | The nightly dump, and putting one back safely |

## Historical records

Kept as written when each was finished. They describe the project as it was then, so parts are out of date: most
assume the localhost-only, single-operator setup. The `docs/` ones start with a banner that points back here.

| Document | What it recorded |
|---|---|
| [spec.md](spec.md) | The original build prompts. Deviations are listed in [PLAN.md](PLAN.md) section 0 |
| [phase-0.md](phase-0.md) | The Zernio spike: live API checks and the recorded test fixtures |
| [backend/scripts/README_SPIKE.md](../backend/scripts/README_SPIKE.md) | How the Phase 0 spike script was run. It calls Zernio with a real key from `.env`: operator only, and never in a checkout whose `.env` has `SECRETS_KEY`, or the next `migrate` imports that key into user 1 |
| [phase-1.md](phase-1.md) | Backend skeleton: compose stack, models, the job queue |
| [phase-2.md](phase-2.md) | Ingest and render, backend only |
| [phase-3.md](phase-3.md) | Frontend: Library, upload, Editor, brands |
| [phase-4.md](phase-4.md) | Accounts, scheduling, the calendar |
| [phase-5.md](phase-5.md) | Publishing and recovery, with the live acceptance run |
| [demo.md](demo.md) | A 5-minute walkthrough of V1 on localhost |

The screenshots in `docs/` itself (`phase-*.png`) belong to these records and pull requests; `e2e/accept.mjs`
rewrites the `phase-3-*.png` ones. Current screenshots live in `docs/images/`.

## Keeping the docs current

- Every claim must match the code (`backend/app`, `frontend/src`). When the two disagree, the code wins: fix
  the page. UI labels are in **bold**, exactly as on screen.
- Never write a password or hash, a key or any part of one, a token, a chat id or a personal email address into a
  doc: the repo is public. The operator is "the operator", username `clipper`.
- Screenshots and recordings come from `frontend/e2e/docs-screenshots.mjs`, run by a maintainer against the review
  stack as the operator. It rewrites the `docs/images/*.png` and `*.gif` it lists and `docs/images/manifest.json`; the
  legend tables in the guide follow the callout numbers in that manifest. A GIF is Chrome's own frames of the page
  while the script clicks through a task, with a drawn pointer, encoded by ffmpeg in the review stack's worker (about
  10 seconds, under half a megabyte); a GIF has no callouts, so the page around it explains it. The images the script
  doesn't list were taken by a script that is not in the repo: `sign-up.png`, `sign-in.png`, `setup.png`,
  `settings.png`, `footer-no-key.png`, `zernio-key-error.png` and `telegram-token-error.png` on the dev site, as a
  throwaway account with a made-up key and bot token, and `settings-key-refused.png` on a throwaway test stack. Retake
  them by hand the same way, never with a real key or token.

  ```sh
  ./review.sh                                          # a fresh review copy (it prints how to give clipper a password)
  cd frontend && npm run dev                           # leave it running; the rest from frontend/, in a second terminal
  export CLIPPER_E2E_USER=clipper CLIPPER_E2E_PASSWORD=…  # the operator's password on the review copy
  node e2e/docs-screenshots.mjs                        # every screenshot and GIF
  ONLY=calendar,editor-filters node e2e/docs-screenshots.mjs  # just those (names without .png or .gif)
  ```

- The script changes data on the review copy to set up each screen (its header lists what): it replaces the
  operator's saved captions and covers with the docs' own, renders a dozen clips, and plans a week of posts. Nothing
  leaves the Mac, and `./review.sh` brings back a fresh copy.

[Project README](../README.md) · [Contributing](../CONTRIBUTING.md) · [Guide](guide/README.md) · [Workflows](workflows.md) · [Deploy runbook](deploy.md)
