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
# - Accounts made on staging itself (ids above 10,000,000: production's stay below) survive every refresh, with their
#   password, Zernio key, quota and bots; their clips, posts and files don't (staging's data is production's).
#   Production's accounts arrive with the copy. A staging username production has taken since becomes <name>.dev
#   (<name>.dev<n> if that is taken too); a bot production also runs, or a Zernio user production's copy holds, is
#   dropped from the staging account.
# The accounts are saved to ~/staging-accounts/pending-*.copy before the drop, and that pair is only retired (renamed
# to the run's date; the last 10 kept) once they are back in: a refresh that fails half-way loses none, and the next
# run puts back the pending ones plus any made since.
# deploy.sh takes the same lock, so a push to dev waits for a refresh (and the other way round).
set -eu
cd "$(dirname "$0")"
exec 9>.git/deploy.lock
flock 9
PROD=${CLIPPER_PROD_DIR:-$HOME/clipper}
echo "staging refresh $(date -u +%FT%TZ) from $PROD"
USERS=id,username,password_hash,created_at,disabled_at,zernio_key_enc,zernio_key_last4,zernio_user_id,zernio_email,zernio_name,zernio_key_status,zernio_checked_at,zernio_error,zernio_key_gen,quota_bytes
BOTS=id,user_id,bot_id,username,token_enc,chat_id,chat_title,pair_sha256,pair_expires_at,alerts,error,last_seen_at,created_at
KEPT=$HOME/staging-accounts
umask 077
mkdir -p "$KEPT"
dump=$(mktemp) now_users=$(mktemp) now_bots=$(mktemp)
trap 'rm -f "$dump" "$now_users" "$now_bots"' EXIT
sql() { docker compose exec -T postgres psql -q -U clipper -d clipper -v ON_ERROR_STOP=1 "$@"; }

(cd "$PROD" && docker compose exec -T postgres pg_dump -U clipper -Fc clipper) > "$dump"
docker compose stop api worker publisher bot
docker compose up -d --wait postgres
# the staging accounts in the database now (a failing COPY stops the refresh here, before anything is dropped), after
# any still pending from a refresh that failed (the first copy of an id wins: COPY's text format starts with the id)
sql -c "COPY (SELECT $USERS FROM users WHERE id > 10000000) TO STDOUT" > "$now_users"
sql -c "COPY (SELECT $BOTS FROM telegram_bots WHERE user_id > 10000000) TO STDOUT" > "$now_bots"
touch "$KEPT/pending-users.copy" "$KEPT/pending-bots.copy"
cat "$KEPT/pending-users.copy" "$now_users" | awk -F '\t' '!seen[$1]++' > "$KEPT/pending-users.next"
cat "$KEPT/pending-bots.copy" "$now_bots" | awk -F '\t' '!seen[$1]++' > "$KEPT/pending-bots.next"
mv "$KEPT/pending-users.next" "$KEPT/pending-users.copy"
mv "$KEPT/pending-bots.next" "$KEPT/pending-bots.copy"
echo "keeping $(wc -l < "$KEPT/pending-users.copy") staging account(s), $(wc -l < "$KEPT/pending-bots.copy") bot(s)"

docker compose exec -T postgres psql -q -U clipper -d postgres -c 'DROP DATABASE IF EXISTS clipper WITH (FORCE)' -c 'CREATE DATABASE clipper'
docker compose exec -T postgres pg_restore -U clipper -d clipper --no-owner -x --exit-on-error --single-transaction < "$dump"
sql <<'SQL'
BEGIN;
UPDATE posts SET status = 'CANCELLED' WHERE status NOT IN ('PUBLISHED', 'CANCELLED');
SELECT setval(c.oid::regclass, coalesce(pg_sequence_last_value(c.oid::regclass), 0) + 10000000)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'S' AND n.nspname = 'public' AND c.relname NOT LIKE 'procrastinate%';
COMMIT;
SQL
sudo -n rsync -a --delete "$PROD/data/" data/ # the containers write as root
docker compose run --rm migrate

# the kept accounts load into their own schema (the api's role gets no rights there, even if this run stops half-way)
sql -c "DROP SCHEMA IF EXISTS kept CASCADE" -c "CREATE SCHEMA kept" \
    -c "CREATE TABLE kept.users AS SELECT $USERS FROM users WITH NO DATA" \
    -c "CREATE TABLE kept.bots AS SELECT $BOTS FROM telegram_bots WITH NO DATA"
sql -c "COPY kept.users FROM STDIN" < "$KEPT/pending-users.copy"
sql -c "COPY kept.bots FROM STDIN" < "$KEPT/pending-bots.copy"
sql <<SQL
BEGIN;
-- a bot production runs too would be polled from two places: the staging account loses it
SELECT 'dropping ' || count(*) || ' staging bot(s) production also runs'
  FROM kept.bots WHERE bot_id IN (SELECT bot_id FROM telegram_bots);
DELETE FROM kept.bots WHERE bot_id IN (SELECT bot_id FROM telegram_bots);
DELETE FROM sessions;
DELETE FROM telegram_bots;
UPDATE users SET zernio_key_enc = NULL, zernio_key_last4 = NULL, zernio_key_status = 'none', zernio_checked_at = NULL,
                 zernio_error = NULL, env_imported_at = NULL;
COMMIT;
SQL
# committed on its own: if putting the accounts back fails, the copy is still scrubbed (and they stay pending)
sql <<SQL
BEGIN;
-- a username production has taken since: <name>.dev, or <name>.dev<n> if that is taken too
UPDATE kept.users k SET username = CASE
         WHEN NOT EXISTS (SELECT 1 FROM users u WHERE u.username = left(k.username, 28) || '.dev')
          AND NOT EXISTS (SELECT 1 FROM kept.users o WHERE o.username = left(k.username, 28) || '.dev')
         THEN left(k.username, 28) || '.dev'
         ELSE left(k.username, 20) || '.dev' || (k.id - 10000000) END
 WHERE EXISTS (SELECT 1 FROM users u WHERE u.username = k.username);
-- a Zernio user production's copy holds: the staging account pastes a key again
UPDATE kept.users k SET zernio_key_enc = NULL, zernio_key_last4 = NULL, zernio_user_id = NULL, zernio_email = NULL,
                        zernio_name = NULL, zernio_key_status = 'none', zernio_checked_at = NULL, zernio_error = NULL
 WHERE EXISTS (SELECT 1 FROM users u WHERE u.zernio_user_id = k.zernio_user_id);
INSERT INTO users ($USERS) SELECT $USERS FROM kept.users;
INSERT INTO telegram_bots ($BOTS) SELECT $BOTS FROM kept.bots;
SELECT setval('users_id_seq', greatest((SELECT max(id) FROM users), 10000000));
SELECT setval('telegram_bots_id_seq', greatest((SELECT max(id) FROM telegram_bots), 10000000));
SELECT 'kept ' || count(*) || ' staging account(s): ' || coalesce(string_agg(username, ', ' ORDER BY id), '-')
  FROM users WHERE id > 10000000;
DROP SCHEMA kept CASCADE;
COMMIT;
SQL
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mv "$KEPT/pending-users.copy" "$KEPT/$stamp-users.copy"
mv "$KEPT/pending-bots.copy" "$KEPT/$stamp-bots.copy"
ls -1t "$KEPT"/*-users.copy | tail -n +11 | while read -r f; do rm -f "$f" "${f%-users.copy}-bots.copy"; done

docker compose run --rm migrate python -m app.cli bootstrap
docker compose up -d --remove-orphans
echo "staging refresh done: $(sql -Atc 'SELECT count(*) FROM source_clips') clips"
