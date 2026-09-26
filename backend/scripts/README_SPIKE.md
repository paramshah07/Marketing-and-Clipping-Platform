# Zernio spike

1. Sign up at zernio.com. Create one profile per Instagram account and connect the account
   (Business or Creator). Create an API key and put it in `.env` as `ZERNIO_API_KEY=sk_...`.
2. From the repo root:
   ```
   uv run backend/scripts/spike_zernio.py accounts                      # note the account _id
   uv run backend/scripts/spike_zernio.py limit ACCOUNT_ID
   uv run backend/scripts/spike_zernio.py upload path/to/clip.mp4       # prints MEDIA_URL
   uv run backend/scripts/spike_zernio.py draft-idempotency ACCOUNT_ID MEDIA_URL   # never publishes
   uv run backend/scripts/spike_zernio.py publish ACCOUNT_ID MEDIA_URL --confirm-username USERNAME [--trial]
   ```
   `publish` posts a real Reel. Instagram posts can't be deleted through Zernio; delete test posts
   in the Instagram app. `--trial` posts a Trial Reel that only non-followers see.
3. Failures print the full response. Fixtures land in `backend/tests/fixtures/zernio/`.
