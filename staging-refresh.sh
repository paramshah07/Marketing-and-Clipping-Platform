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
# - Accounts made on staging itself (ids from 10,000,001 up: production's stay below) survive every refresh, with
#   their password, Zernio key, quota and bots; their clips, posts and files don't (staging's data is production's).
#   Production's accounts arrive with the copy; a staging account whose username production took since is renamed
#   <name>.dev.
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
USERS=id,username,password_hash,created_at,disabled_at,zernio_key_enc,zernio_key_last4,zernio_user_id,zernio_email,zernio_name,zernio_key_status,zernio_checked_at,zernio_error,zernio_key_gen,quota_bytes
BOTS=id,user_id,bot_id,username,token_enc,chat_id,chat_title,pair_sha256,pair_expires_at,alerts,error,last_seen_at,created_at
# kept on disk, not in a temp file: a refresh that fails between the drop and the restore loses nothing (the last 10
# stay in ~/staging-accounts; to restore by hand, load one pair the way the end of this script does)
umask 077
mkdir -p "$HOME/staging-accounts"
kept_users="$HOME/staging-accounts/$(date -u +%Y%m%dT%H%M%SZ)-users.copy" kept_bots="${kept_users%-users.copy}-bots.copy"
: > "$kept_users"
: > "$kept_bots"
ls -1t "$HOME"/staging-accounts/*-users.copy | tail -n +11 | while read -r f; do rm -f "$f" "${f%-users.copy}-bots.copy"; done
sql() { docker compose exec -T postgres psql -q -U clipper -d clipper -v ON_ERROR_STOP=1 "$@"; }
if [ "$(sql -Atc "SELECT to_regclass('public.users') IS NOT NULL")" = t ]; then
  sql -c "COPY (SELECT $USERS FROM users WHERE id > 10000000) TO STDOUT" > "$kept_users"
  sql -c "COPY (SELECT $BOTS FROM telegram_bots WHERE user_id > 10000000) TO STDOUT" > "$kept_bots"
fi
echo "keeping $(wc -l < "$kept_users") staging account(s), $(wc -l < "$kept_bots") bot(s)"
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
sql -c "CREATE TABLE kept_users AS SELECT $USERS FROM users WITH NO DATA" -c "CREATE TABLE kept_bots AS SELECT $BOTS FROM telegram_bots WITH NO DATA"
sql -c "COPY kept_users FROM STDIN" < "$kept_users"
sql -c "COPY kept_bots FROM STDIN" < "$kept_bots"
sql <<SQL
BEGIN;
INSERT INTO users ($USERS)
SELECT k.id,
       CASE WHEN EXISTS (SELECT 1 FROM users u WHERE u.username = k.username) THEN left(k.username, 28) || '.dev'
            ELSE k.username END,
       k.password_hash, k.created_at, k.disabled_at, k.zernio_key_enc, k.zernio_key_last4,
       CASE WHEN EXISTS (SELECT 1 FROM users u WHERE u.zernio_user_id = k.zernio_user_id) THEN NULL
            ELSE k.zernio_user_id END,
       k.zernio_email, k.zernio_name, k.zernio_key_status, k.zernio_checked_at, k.zernio_error, k.zernio_key_gen,
       k.quota_bytes
  FROM kept_users k
    ON CONFLICT DO NOTHING;
INSERT INTO telegram_bots ($BOTS) SELECT $BOTS FROM kept_bots WHERE user_id IN (SELECT id FROM users) ON CONFLICT DO NOTHING;
SELECT setval('users_id_seq', greatest((SELECT max(id) FROM users), 10000000));
SELECT setval('telegram_bots_id_seq', greatest((SELECT max(id) FROM telegram_bots), 10000000));
SELECT 'kept ' || (SELECT count(*) FROM users WHERE id > 10000000) || ' of ' || (SELECT count(*) FROM kept_users)
       || ' staging account(s) (' || coalesce((SELECT string_agg(username, ', ' ORDER BY id) FROM users WHERE id > 10000000), '')
       || '), ' || (SELECT count(*) FROM telegram_bots WHERE user_id > 10000000) || ' bot(s)';
DROP TABLE kept_users, kept_bots;
COMMIT;
SQL
docker compose run --rm migrate python -m app.cli bootstrap
docker compose up -d --remove-orphans
echo "staging refresh done: $(docker compose exec -T postgres psql -U clipper -d clipper -Atc 'SELECT count(*) FROM source_clips') clips"
