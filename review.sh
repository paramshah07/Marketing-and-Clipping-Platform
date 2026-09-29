#!/bin/sh
# Try the checked-out branch on the Mac against a copy of production (compose.review.yml: no publishing, no bots):
#   ./review.sh        copy production's database and files (read-only on the VM), start the review stack
#   ./review.sh down   stop it and drop its database
# Then `cd frontend && npm run dev` and open http://localhost:5173 and sign in as the operator. Only the operator's
# rows and files reach the Mac: other users' are dropped from the copy, and no session is copied.
set -eu
cd "$(dirname "$0")"
VM=${CLIPPER_VM:-ubuntu@145.241.239.46}
dc() { docker compose -p clipper-review -f compose.yml -f compose.review.yml "$@"; }
if [ "${1:-}" = down ]; then dc down -v; exit 0; fi
# any container of the Mac's own stack, running or stopped, except the test run's postgres/migrate: its bots hold
# production tokens and a stopped on-failure bot comes back when Docker restarts, so it must be removed, not stopped
if docker ps -a --filter label=com.docker.compose.project=clipper --filter label=com.docker.compose.oneoff=False \
  --format '{{.Label "com.docker.compose.service"}}' | grep -Eqvx 'postgres|migrate'; then
  echo "the Mac's own Clipper stack exists (production keys): remove it first: docker compose -p clipper down" >&2; exit 1
fi
DUMP=/tmp/clipper-review.dump
trap 'rm -f "$DUMP"' EXIT  # every user's rows are in it until the scrub below
ssh "$VM" 'cd clipper && docker compose exec -T postgres pg_dump -U clipper -Fc --exclude-table-data=sessions clipper' > "$DUMP"
# the operator's files only: legacy (unprefixed) keys and u/1/; other users' media stays on the VM
rsync -a --include=/u/1/ --exclude='/u/*' "$VM:clipper/data/" data/
dc down -v
dc up -d --build --wait postgres
# -x: no GRANTs (the api's role doesn't exist here yet; migrate's db-grants makes it and grants afresh)
dc exec -T postgres pg_restore -U clipper -d clipper --no-owner -x --exit-on-error --single-transaction < "$DUMP"
# the operator's rows only (a dump from before users has no one else's yet); the superuser: row-level security aside
dc exec -T postgres psql -U clipper -d clipper -q -v ON_ERROR_STOP=1 -c "DO \$\$ BEGIN IF to_regclass('users') IS NOT NULL THEN
  DELETE FROM posts WHERE user_id <> 1; DELETE FROM renders WHERE user_id <> 1; DELETE FROM accounts WHERE user_id <> 1;
  DELETE FROM saved_captions WHERE user_id <> 1; DELETE FROM saved_covers WHERE user_id <> 1;
  DELETE FROM brands WHERE user_id <> 1; DELETE FROM source_clips WHERE user_id <> 1;
  DELETE FROM telegram_bots WHERE user_id <> 1; DELETE FROM users WHERE id <> 1; END IF; END \$\$"
dc up -d --build api worker publisher
echo "review stack up (publishing off, no bots): cd frontend && npm run dev, then open http://localhost:5173"
echo "sign in as the operator; a copy from before users has no password yet: set one with"
echo "  docker compose -p clipper-review -f compose.yml -f compose.review.yml exec api python -m app.cli set-password clipper"
