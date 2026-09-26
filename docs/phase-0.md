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

## Not yet verified (needs the operator's go-ahead to post publicly)
- A real `publishNow` Reel appearing on Instagram, its `platformPostUrl`, and the replay of a
  *published* post with the same key.
- Whether Zernio actually rejects a 120 s Reel.
- Telegram alert delivery (bot is valid; the operator has not messaged it yet, so there is no chat id).
