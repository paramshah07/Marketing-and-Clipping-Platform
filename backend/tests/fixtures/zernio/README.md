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

## Docs examples, pending live confirmation (`docs_*.json`)

Copied from docs.zernio.com (local copies in `.context/zernio-docs/`). Where the docs describe a
response without a full example, the body holds only the documented fields, with no invented message text.
Replace each one with a live recording when a real publish produces that response.

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
