#!/bin/sh
# Try the checked-out branch on the Mac against a copy of production (compose.review.yml: no publishing, no bots):
#   ./review.sh        copy production's database and files (read-only on the VM), start the review stack
#   ./review.sh down   stop it and drop its database
# Then `cd frontend && npm run dev` and open http://localhost:5173.
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
ssh "$VM" 'cd clipper && docker compose exec -T postgres pg_dump -U clipper -Fc clipper' > /tmp/clipper-review.dump
rsync -a "$VM:clipper/data/" data/
dc down -v
dc up -d --build --wait postgres
dc exec -T postgres pg_restore -U clipper -d clipper --no-owner --exit-on-error --single-transaction < /tmp/clipper-review.dump
dc up -d --build api worker
echo "review stack up (publishing off, no bots): cd frontend && npm run dev, then open http://localhost:5173"
