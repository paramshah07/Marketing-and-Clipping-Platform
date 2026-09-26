# Phase 0 — Zernio spike

Script: `backend/scripts/spike_zernio.py` (run with `uv run`, reads `ZERNIO_API_KEY` from `.env`).
Recorded responses (key, upload signatures and bio redacted): `backend/tests/fixtures/zernio/`.

## Verified against the live API (2026-09-26)
| Check | Result |
|---|---|
| `GET /v1/accounts` | 200. One Instagram account (Instagram Login, `isActive: true`) in profile "Default". |
| `GET /v1/accounts/{id}/instagram/publishing-limit` | 200 `{"quotaUsage":0,"quotaTotal":100,"quotaDurationSeconds":86400}`: the real limit is **100 / rolling 24 h**. |
| `POST /v1/media/presign` + PUT | 200 / 200. `uploadUrl` is a Cloudflare R2 presigned PUT (1 h); `publicUrl` on `media.zernio.com/temp/…` serves the exact bytes (`video/mp4`). |
| Draft with `Idempotency-Key` | 201 "Draft saved successfully". |
| Same key, same body | **200 "Post already exists (idempotent retry)", same `_id`.** |
| Same key, different caption | **200, same `_id`**: the match is on the key alone, as documented. |
| `DELETE /v1/posts/{id}` (draft) | 200; a later GET returns 404. |
| Test clips | 8 s and 120 s, 1080x1920 H.264 High, 30 fps closed GOP, AAC 48 kHz stereo, faststart (made with the worker's ffmpeg 7.1.5). |

## Learned from the docs while building the spike
- Instagram posts **cannot be unpublished or deleted** via Zernio (`/unpublish` excludes Instagram).
- Reel max duration on Zernio: 90 s (docs). Feed video: 60 min.
- `isPaidPartnership` ("Paid partnership" label) works only for accounts connected with
  `loginMethod=facebook_login`; Instagram-Login accounts get a 400.
- `trialParams: {graduationStrategy: "MANUAL"}` posts a Trial Reel shown only to non-followers.
- 201 = created (and published when `publishNow`); 207 = not fully published (branch on `post.status`);
  403 codes `ACCOUNT_DISCONNECTED` / `ACCOUNT_NOT_ENABLED_FOR_POSTING` / `PROFILE_OVER_LIMIT`;
  429 covers the 25 posts/hour/account velocity limit.

## Live publish (2026-09-26, operator approved normal Reels on @i.cant.de)
| Check | Result |
|---|---|
| `publishNow` 8 s Reel | **201 "Post published successfully" but `post.status` = `publishing`** (platform `processing`); `GET /v1/posts/{id}` showed `published` ~45 s later: https://www.instagram.com/reel/DdwzLx_jozA/ |
| 120 s Reel (Zernio docs say 90 s max) | **Published** the same way: https://www.instagram.com/reel/DdwzdejjhjW/, so Zernio does not enforce 90 s; Clipper now uses Meta's 15 min |
| Same `Idempotency-Key` resent after the Reel was live | **200 "Post already exists (idempotent retry)"**, same `_id`, no second Reel |
| Visible logged out | Yes: the Reel page opens in a fresh logged-out browser with the caption |
| Telegram | Real alerts delivered (Phase 1); Telegram rejects `localhost` button URLs, so local links go in the text |

Recordings: `backend/tests/fixtures/zernio/live_*.json`.
