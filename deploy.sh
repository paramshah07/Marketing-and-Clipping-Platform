#!/bin/sh
# Production deploy on the VM (docs/deploy.md section 4). GitHub Actions runs it on every push to prod (the VM's checkout tracks origin/prod): its SSH
# key's forced command in ~/.ssh/authorized_keys is `cd ~/clipper && git pull --ff-only && exec sh deploy.sh`,
# so this file is always the version just pulled. Rebuilds what changed, restarts Caddy if its file changed
# (the container holds the old one), then waits for the api to answer.
set -eu
cd "$(dirname "$0")"
docker compose up -d --build --remove-orphans
git diff --quiet 'HEAD@{1}' HEAD -- Caddyfile 2>/dev/null || docker compose restart caddy
for _ in $(seq 60); do
  if curl -fsS -o /dev/null http://127.0.0.1:8000/api/health; then
    echo "deployed $(git log --oneline -1)"
    exit 0
  fi
  sleep 2
done
echo "api not healthy 2 min after the deploy" >&2
exit 1
