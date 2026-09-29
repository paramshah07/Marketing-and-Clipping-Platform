# Clipper documentation

Everything written about Clipper, grouped by what you want to do. New here? Read the
[project README](../README.md), then [Getting started](guide/01-getting-started.md).

## Guide

How to use the app, page by page, with annotated screenshots. Reading order: [guide/README.md](guide/README.md).

| Page | What it covers |
|---|---|
| [Getting started](guide/01-getting-started.md) | Signing up and in, Set up Clipper (Zernio key, Instagram, Telegram), Settings, the layout and status footer, a day in Clipper |
| [Library](guide/02-library.md) | Uploading, importing from a link or a document, clip statuses, search, the Published tab |
| [Editor](guide/03-editor.md) | The stage, logo placement, crop, cover, caption, rendering and the render cards |
| [Customizations](guide/04-customizations.md) | Brands, saved captions, saved covers, and how the Editor applies the defaults |
| [Calendar](guide/05-calendar.md) | The week board, scheduling, **Auto-schedule**, drafts and approval, the post drawer |
| [Accounts](guide/06-accounts.md) | Syncing from your Zernio account, time zone, posting slots, daily cap, minimum gap, disabling |
| [Publishing and recovery](guide/07-publishing-and-recovery.md) | What happens at slot time, post statuses, the Recover page, alerts, a refused Zernio key, limits |
| [Telegram bot](guide/08-telegram-bot.md) | Adding and pairing your bots, their status, commands, cards and alerts in the chat |

## Workflows

[workflows.md](workflows.md): end-to-end flows as diagrams with steps. The nightly routine, handling a failed
post, adding an account, shipping a change (branch, pull request, `./review.sh`, merge, automatic deploy,
verify), and backups and restore.

## Runbooks

| Document | Use it to |
|---|---|
| [deploy.md](deploy.md) | Set up the VM, branches and releases, CI, deploy an update, release multi-user (checklist, rollback), restore a backup, try a pull request with `./review.sh` |
| [multi-user.md](multi-user.md#11-operator-runbook) | Manage users, quotas and signups; rotate `SECRETS_KEY` |

## Reference

| Document | What it is |
|---|---|
| [PLAN.md](PLAN.md) | The implementation plan: Zernio facts, data model, the publishing state machine, API, settings |
| [multi-user.md](multi-user.md) | Users, sessions, row-level security, secrets, each user's Zernio key and bots, quotas, fairness, threat model, capacity |
| [telegram-bot.md](telegram-bot.md) | The Telegram bots' design: the supervisor, pairing, security, Telegram's limits, parity with the web app |
| [design/BRIEF.md](design/BRIEF.md) | The UI design contract, with the mockups beside it in `design/` |
| [CLAUDE.md](../CLAUDE.md) | Stack, commands and the hard rules the code must follow |
| [backend/openapi.json](../backend/openapi.json) | The API schema; `frontend/src/api` is generated from it |

## Historical records

Kept as written when each phase finished. They describe the project as it was then, so parts are out of
date: most assume the localhost-only setup, and production now runs on the VM. Each starts with a banner
that points back here.

| Document | What it recorded |
|---|---|
| [spec.md](spec.md) | The original build prompts. Deviations are listed in [PLAN.md](PLAN.md) section 0 |
| [phase-0.md](phase-0.md) | The Zernio spike: live API checks and the recorded test fixtures |
| [phase-1.md](phase-1.md) | Backend skeleton: compose stack, models, the job queue |
| [phase-2.md](phase-2.md) | Ingest and render, backend only |
| [phase-3.md](phase-3.md) | Frontend: Library, upload, Editor, brands |
| [phase-4.md](phase-4.md) | Accounts, scheduling, the calendar |
| [phase-5.md](phase-5.md) | Publishing and recovery, with the live acceptance run |
| [demo.md](demo.md) | A 5-minute walkthrough of V1 on localhost |

The screenshots in `docs/` itself (`phase-*.png` and a few others) belong to these records and pull requests.
Current screenshots live in `docs/images/`.

## Keeping the docs current

- Every claim must match the code (`backend/app`, `frontend/src`). When the two disagree, the code wins: fix
  the page.
- Screenshots come from `frontend/e2e/docs-screenshots.mjs`, run against the review stack as the operator. It
  rewrites the `docs/images/*.png` it lists and `docs/images/manifest.json`; the legend tables in the guide follow the
  callout numbers in that manifest. `sign-in.png`, `sign-up.png`, `setup.png`, `settings.png`,
  `settings-key-refused.png` and `footer-no-key.png` are not in it: they came from the multi-user end-to-end run on a
  throwaway stack (test users, made-up keys).

  ```sh
  ./review.sh                                          # a fresh review copy (it prints how to give clipper a password)
  cd frontend && npm run dev                           # leave it running; the rest from frontend/, in a second terminal
  export CLIPPER_E2E_USER=clipper CLIPPER_E2E_PASSWORD=…  # the operator's password on the review copy
  node e2e/docs-screenshots.mjs                        # every screenshot
  ONLY=calendar,accounts node e2e/docs-screenshots.mjs # just those
  ```

- The script changes data on the review copy to set up each screen. `./review.sh` brings back a fresh copy.

[Project README](../README.md) · [Guide](guide/README.md) · [Workflows](workflows.md) · [Deploy runbook](deploy.md)
