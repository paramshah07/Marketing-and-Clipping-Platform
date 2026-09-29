#!/bin/sh
# Staging's copy of production (docs/deploy.md section 7). Cron runs it on the VM every 5 days from ~/clipper-dev:
#   0 5 */5 * * cd /home/ubuntu/clipper-dev && sh staging-refresh.sh >> /home/ubuntu/staging-refresh.log 2>&1
# Production is only read (pg_dump, and its data dir). Staging's database and files are replaced by the copy, which
# staging's own migrate then brings to this branch's schema (a rehearsal of the next release's migrations).
# - Straight after the restore, before anything else can fail: every copied post that could still publish is
#   cancelled (production's schedule is production's to publish), and ids restart 10,000,000 above production's.
#   A refresh that stops half-way leaves nothing that a later deploy's publisher could send.
# - After migrate: nobody's session, stored Zernio key or Telegram bot survives (staging's SECRETS_KEY couldn't open
#   them, and a bot token polled from two places breaks the real bot); user 1 takes this checkout's .env
#   ZERNIO_API_KEY again, if any (compose.staging.yml blanks every TELEGRAM_* for migrate).
# deploy.sh takes the same lock, so a push to dev waits for a refresh (and the other way round).
set -eu
cd "$(dirname "$0")"
exec 9>.git/deploy.lock
flock 9
PROD=${CLIPPER_PROD_DIR:-$HOME/clipper}
echo "staging refresh $(date -u +%FT%TZ) from $PROD"
dump=$(mktemp)
trap 'rm -f "$dump"' EXIT
(cd "$PROD" && docker compose exec -T postgres pg_dump -U clipper -Fc clipper) > "$dump"
docker compose stop api worker publisher bot
docker compose up -d --wait postgres
docker compose exec -T postgres psql -q -U clipper -d postgres -c 'DROP DATABASE IF EXISTS clipper WITH (FORCE)' -c 'CREATE DATABASE clipper'
docker compose exec -T postgres pg_restore -U clipper -d clipper --no-owner -x --exit-on-error --single-transaction < "$dump"
docker compose exec -T postgres psql -q -U clipper -d clipper -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
UPDATE posts SET status = 'CANCELLED' WHERE status NOT IN ('PUBLISHED', 'CANCELLED');
SELECT setval(c.oid::regclass, coalesce(pg_sequence_last_value(c.oid::regclass), 0) + 10000000)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'S' AND n.nspname = 'public' AND c.relname NOT LIKE 'procrastinate%';
COMMIT;
SQL
sudo -n rsync -a --delete "$PROD/data/" data/ # the containers write as root
docker compose run --rm migrate
docker compose exec -T postgres psql -q -U clipper -d clipper -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
DELETE FROM sessions;
DELETE FROM telegram_bots;
UPDATE users SET zernio_key_enc = NULL, zernio_key_last4 = NULL, zernio_key_status = 'none', zernio_checked_at = NULL,
                 zernio_error = NULL, env_imported_at = NULL;
COMMIT;
SQL
docker compose run --rm migrate python -m app.cli bootstrap
docker compose up -d --remove-orphans
echo "staging refresh done: $(docker compose exec -T postgres psql -U clipper -d clipper -Atc 'SELECT count(*) FROM source_clips') clips"
