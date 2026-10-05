# Zernio response fixtures

Each file is `{"status", "headers": {"Retry-After"}, "body"}`. Tests serve them through
`httpx.MockTransport` only (CLAUDE.md: never fake Zernio anywhere else, never use a fake to claim the
integration works).

## Live recordings (Phase 0, `scripts/spike_zernio.py`, secrets redacted)

| File | Call |
|---|---|
| `accounts.json` | `GET /v1/accounts` |
| `publishing_limit.json` | `GET /v1/accounts/{id}/instagram/publishing-limit` |
| `presign.json` | `POST /v1/media/presign` |
| `draft_create.json` | `POST /v1/posts` (draft, 201) |
| `draft_replay_same_body.json`, `draft_replay_diff_body.json` | `POST /v1/posts` replay with the same `Idempotency-Key` (200) |
| `draft_delete.json` | `DELETE /v1/posts/{id}` |
| `live_create_publishing.json` | `POST /v1/posts` publishNow, a real Instagram Reel (Phase 5 live run, 2026-09-26): **201 "Post published successfully" while `post.status` is still `publishing`** (platform `processing`) |
| `live_get_published.json` | `GET /v1/posts/{id}` for that same post ~45 s later: `published`, `platformPostUrl` https://www.instagram.com/reel/DdwzLx_jozA/ |
| `live_auth_verify.json` | `GET /v1/auth/verify` with a valid `sk_` key (2026-09-28): `authType: api_key`, `scope: null`; userId/name/email redacted |
| `live_auth_verify_401.json` | `GET /v1/auth/verify` with a well-formed but unknown key (2026-09-28) |
| `live_replay_published.json` | `POST /v1/posts` again with the same `Idempotency-Key` after the post was live: **200 "Post already exists (idempotent retry)"**, same `_id`, no second Reel |

## Docs examples, pending live confirmation (`docs_*.json`)

Copied from docs.zernio.com (local copies in `.context/zernio-docs/`). Where the docs describe a
response without a full example, the body holds only the documented fields, with no invented message text.
Replace each one with a live recording when a real publish produces that response. The live run
confirmed the create/replay/GET shapes (above); note that a real Instagram video create returns 201
with `status: publishing`, not `published` as `docs_create_published.json` shows.

| File | Source |
|---|---|
| `docs_create_published.json` | platforms/instagram "Response (201)" example, verbatim. Also served for `GET /v1/posts/{id}` and `POST /v1/posts/{id}/retry` (same `{post}` shape) |
| `docs_replay_published.json` | posts/create-post 200: the original post plus `message: "Post already exists (idempotent retry)"` (the live draft replay has the same message) |
| `docs_207_partial.json` | guides/post-lifecycle response example, verbatim (`status: partial`) |
| `docs_207_failed.json` | posts/create-post 207 `failed`; the lifecycle example's Instagram entry. Tests swap `errorCategory` across the documented enum |
| `docs_207_scheduled.json` | posts/create-post 207 `scheduled`: every platform reset to `pending` |
| `docs_409_idempotency_conflict.json` | posts/create-post 409 `code: idempotency_conflict`; the `Retry-After` value is ours (the docs give none) |
| `docs_409_duplicate.json` | guides/idempotency duplicate-content 409 example, verbatim |
| `docs_429.json` | guides/rate-limits 429 example, verbatim |
| `docs_403_disconnected.json` | posts/create-post 403 example `error` + `code: ACCOUNT_DISCONNECTED` |
| `docs_5xx.json` | posts/create-post: retry with the same key "after a 5xx". The docs show no 5xx body, so it is empty (the client never reads one) |
| `docs_401_unauthorized.json` | posts/create-post 401, the shared `Unauthorized` response: `{"error": "Unauthorized"}`, verbatim (the same body as `live_auth_verify_401.json`) |
| `docs_402_payment_required.json` | posts/retry-post 402 "the account owner has a failed payment": `error` has no example, so the body is empty |
| `docs_403_insufficient_permissions.json` | the `ResourceGroupForbidden` response (openapi.yaml; api-keys tag): a restricted `zrk_` key without the operation's group gets `code: insufficient_permissions` plus `required_group`. Only those two fields: the docs' example message is about another group |
| `docs_403_profile_over_limit.json` | posts/create-post 403 `code: PROFILE_OVER_LIMIT` ("a target account belongs to a profile beyond the plan's profile limit"); no example message |
| `docs_403_not_your_account.json` | posts/create-post 403 with no `code`: "a target accountId does not belong to the authenticated user (or is outside the API key's profile scope)"; no example message |
| `docs_audio_search.json` | platforms/instagram "Reels with catalog audio", the `GET /v1/accounts/{id}/instagram/audio` "Response (200)" example, verbatim |
| `docs_400_audio_requires_facebook_login.json` | platforms/instagram and the openapi spec: an Instagram Login account gets "a 400 with code `instagram_audio_requires_facebook_login`" from the audio search and from `POST /v1/posts` with `audioConfiguration`; no example message, so the body is that `code` alone |

The key tests (`test_zernio_key.py`) serve `accounts.json` for `GET /v1/accounts` and, for `includeOverLimit=true`,
the same body plus a copy of its account under another `_id` and `username`: the docs describe that parameter
("includes accounts from over-limit profiles") but show no such response.
