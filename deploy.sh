#!/bin/sh
# Deploy on the VM (docs/deploy.md sections 4 and 7). GitHub Actions runs it on every push to prod, in ~/clipper
# (production, tracking origin/prod), and on every push to dev, in ~/clipper-dev (staging, tracking origin/dev): each
# SSH key's forced command in ~/.ssh/authorized_keys is `cd <checkout> && git pull --ff-only && exec sh deploy.sh`,
# so this file is always the version just pulled. Rebuilds what changed, restarts Caddy if its file changed
# (the container holds the old one), then waits for the api to answer.
set -eu
cd "$(dirname "$0")"
exec 9>.git/deploy.lock # one at a time with staging-refresh.sh in the same checkout
flock 9
# The app's own secrets, made here once and never printed (compose.prod.yml refuses to start without them):
# SECRETS_KEY, a Fernet key, seals every user's Zernio key and bot tokens (keep a copy: losing it means every user
# pastes them again); BOT_SERVICE_SECRET is the bearer the bot service acts as users with.
[ -z "$(tail -c1 .env)" ] || echo >> .env  # a last line without its newline would swallow the first one added
grep -q '^SECRETS_KEY=.' .env || echo "SECRETS_KEY=$(openssl rand -base64 32 | tr '+/' '-_')" >> .env
grep -q '^BOT_SERVICE_SECRET=.' .env || echo "BOT_SERVICE_SECRET=$(openssl rand -hex 32)" >> .env
docker compose up -d --build --remove-orphans
# staging (~/clipper-dev, compose.staging.yml) runs no Caddy of its own, and its api answers on another port
if docker compose config --services | grep -qx caddy; then
  git diff --quiet 'HEAD@{1}' HEAD -- Caddyfile 2>/dev/null || docker compose restart caddy
fi
api=$(docker compose port api 8000) # 127.0.0.1:8000 in production, 127.0.0.1:8001 on staging
for _ in $(seq 60); do
  if curl -fsS -o /dev/null "http://$api/api/health"; then
    echo "deployed $(git log --oneline -1)"
    exit 0
  fi
  sleep 2
done
echo "api not healthy 2 min after the deploy" >&2
exit 1
