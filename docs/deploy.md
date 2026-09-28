# Deploy: one Oracle Cloud VM behind Caddy and a shared password (PLAN rev 3)

Production is the same compose stack on one Oracle Cloud Always Free Arm VM (`VM.Standard.A1.Flex`,
2 OCPU / 12 GB, Ubuntu 24.04, 150 GB boot volume), with `compose.prod.yml` on top: code and the built
frontend are baked into the images, the api serves the app at `/`, logs are capped. It is public at
`https://<ip-with-dashes>.sslip.io` (a free name that resolves to the IP; Caddy gets a Let's Encrypt
certificate for it, or ZeroSSL's when sslip.io's shared Let's Encrypt quota is spent). There is no login,
so Caddy (`Caddyfile`) puts one shared password (HTTP basic auth) in front of everything. Caddy's 80/443
are the only published ports; the security list opens 22, 80, 443.

The account is Pay As You Go (still $0 inside the Always Free limits, $1 budget alert), which keeps Oracle
from reclaiming the VM as idle.

Run every block below as written: each is one `&&` chain (or `set -e`), so a failed step stops it.

## 1. Server setup (once)

As `ubuntu`, over SSH to the VM's public IP:

```sh
sudo apt-get update && sudo apt-get -y upgrade && sudo apt-get -y install git unattended-upgrades &&
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker ubuntu &&   # log out and in again
ssh-keygen -t ed25519 -N "" -f ~/.ssh/github &&
printf 'Host github.com\n  IdentityFile ~/.ssh/github\n  StrictHostKeyChecking accept-new\n' >> ~/.ssh/config
```

From the Mac (in the repo): the VM's key as a **read-only** deploy key (gh ties it to its login: logging gh
out removes it), then the secrets straight over SSH:

```sh
scp ubuntu@<vm>:.ssh/github.pub /tmp/clipper-vm.pub && gh repo deploy-key add /tmp/clipper-vm.pub -t clipper-vm
ssh ubuntu@<vm> git clone git@github.com:paramshah07/bajjo-marketing-clipping-platform.git clipper
scp .env ubuntu@<vm>:clipper/.env
```

On the VM, add to `~/clipper/.env` (the password itself is never stored, only its bcrypt hash; base64 so
compose has no `$` to interpolate; the leading space keeps the command out of the shell history):

```sh
 docker run --rm caddy:2 caddy hash-password --plaintext '<password>' | tr -d '\n' | base64 -w0; echo
```

```sh
COMPOSE_FILE=compose.yml:compose.prod.yml       # plain `docker compose ...` now means production here
CLIPPER_HOST=<ip-with-dashes>.sslip.io           # e.g. 129-146-12-34.sslip.io
CLIPPER_USER=clipper
CLIPPER_PASSWORD_HASH=<the base64 line above>
CLIPPER_ACME_EMAIL=<operator's email>            # certificate notices; enables the ZeroSSL fallback
APP_BASE_URL=https://<ip-with-dashes>.sslip.io   # Telegram deep links
PUBLISHING_ENABLED=false                         # true only at the cutover (section 3)
```

The VCN's default security list needs ingress TCP 80 and 443 from 0.0.0.0/0 (the certificate check uses
them). Then the nightly backup (section 5).

## 2. Dry run (publishing off, bots off)

Copy the Mac's data without stopping it, to check the app and to pre-seed the files:

```sh
# Mac
docker compose exec -T postgres pg_dump -U clipper -Fc clipper > /tmp/clipper.dump &&
rsync -a --rsync-path='sudo rsync' data/raw data/renders data/thumbs data/logos data/covers ubuntu@<vm>:clipper/data/ &&
scp /tmp/clipper.dump ubuntu@<vm>:/tmp/
# VM, in ~/clipper
docker compose up -d --build --wait postgres &&
docker compose exec -T postgres pg_restore -U clipper -d clipper --no-owner --exit-on-error --single-transaction < /tmp/clipper.dump &&
docker compose up -d --build migrate api worker caddy   # no bots: two pollers on one token fight
```

`--wait` is safe on a fresh volume: in production the healthcheck goes over TCP, which the first-boot init
server never opens. From the Mac, `curl -s -o /dev/null -w '%{http_code}' https://<host>/` must say 401
(the password is on); then open `https://<host>` and click around. Nothing publishes.

## 3. Cutover (keeps every scheduled post)

Posts are rows with their slot times, idempotency keys and upload state; the VM's dispatcher picks them up
the next minute. A post more than 30 min overdue at that point moves to the next free slot, so:

1. **Pick the window** on the Mac: no post due for the next 45 min, none PUBLISHING, no job running:
   ```sql
   select min(scheduled_for) from posts where status = 'SCHEDULED';
   select count(*) from posts where status = 'PUBLISHING';
   select count(*) from procrastinate_jobs where status = 'doing';
   ```
2. **Mac, stop everything but Postgres** (and the Vite dev server), then note the counts to compare:
   ```sh
   docker compose stop api worker bot bot2 bot3 &&
   docker compose exec -T postgres psql -U clipper -c "select status, count(*) from posts group by 1 order by 1"
   ```
3. **Copy**: section 2's Mac block again (rsync only sends what changed; `sudo rsync` because the VM's
   containers write files as root). `data/qa` is test leftovers: not copied.
4. **VM, replace the dry-run database**, publishing still off:
   ```sh
   docker compose stop &&
   docker compose up -d --wait postgres &&
   docker compose exec -T postgres dropdb -U clipper clipper &&
   docker compose exec -T postgres createdb -U clipper clipper &&
   docker compose exec -T postgres pg_restore -U clipper -d clipper --no-owner --exit-on-error --single-transaction < /tmp/clipper.dump &&
   docker compose exec -T postgres psql -U clipper -c "select status, count(*) from posts group by 1 order by 1"
   ```
5. **Only if the counts match step 2's**, turn publishing on and start everything:
   ```sh
   sed -i 's/^PUBLISHING_ENABLED=.*/PUBLISHING_ENABLED=true/' .env && docker compose up -d --build
   ```
   Then check: the calendar shows the same times, the worker logs `dispatch` every minute, the bots answer,
   and the next post publishes.
6. **Mac**: blank the production credentials so no local stack can publish or poll again, then stop it (the
   `pgdata` volume stays as a stale copy; the VM keeps the real `.env`):
   ```sh
   sed -i '' -E 's/^(ZERNIO_API_KEY|TELEGRAM_BOT_TOKEN(_[23])?|TELEGRAM_CHAT_ID(_[23])?)=.*/\1=/; s/^PUBLISHING_ENABLED=.*/PUBLISHING_ENABLED=false/' .env &&
   docker compose down
   ```

**Rollback**, only until the VM has published its first post (so before step 6):
`sed -i 's/^PUBLISHING_ENABLED=.*/PUBLISHING_ENABLED=false/' .env && docker compose stop` on the VM, then
`docker compose start` on the Mac. The VM's database is now stale: bring it up only with section 2's service
list, and cut over again from step 1 with a fresh dump. After the first post from the VM, fix forward.

## 4. Deploying an update

```sh
cd ~/clipper && git pull --ff-only && docker compose up -d --build &&
{ git diff --quiet 'HEAD@{1}' HEAD -- Caddyfile || docker compose restart caddy; }
```

Only services whose image changed are recreated; a changed `Caddyfile` needs the restart (the container
still holds the old file). The worker stops gracefully (90 s); a publish cut off re-runs with the same
Idempotency-Key, so a deploy never duplicates a Reel.

## 5. Backups

Install the nightly dump (one per weekday name, so the last 7 days are kept):

```sh
(crontab -l 2>/dev/null; echo '0 4 * * * cd /home/ubuntu/clipper && mkdir -p backups && docker compose exec -T postgres pg_dump -U clipper -Fc clipper > backups/clipper-$(date +\%a).dump') | crontab -
```

**Restoring** one puts back posts that have gone out since the dump, so publishing stays off until they are
reconciled:

```sh
sed -i 's/^PUBLISHING_ENABLED=.*/PUBLISHING_ENABLED=false/' .env &&
docker compose stop api worker bot bot2 bot3 &&
docker compose exec -T postgres dropdb -U clipper clipper &&
docker compose exec -T postgres createdb -U clipper clipper &&
docker compose exec -T postgres pg_restore -U clipper -d clipper --no-owner --exit-on-error --single-transaction < backups/clipper-Mon.dump &&
docker compose up -d migrate api worker caddy &&
docker compose exec -T postgres psql -U clipper -c "select id, account_id, scheduled_for, status from posts where status in ('SCHEDULED','PUBLISHING') and scheduled_for < now() order by 3"
```

Each listed post whose slot passed after the dump may be live already: check the account, and cancel the
ones that went out (`update posts set status = 'CANCELLED' where id in (...)`). Then
`sed -i 's/^PUBLISHING_ENABLED=.*/PUBLISHING_ENABLED=true/' .env && docker compose up -d`.

ponytail: the dumps live on the VM itself, which covers mistakes but not losing the VM; copy them off-box
(Oracle Object Storage, 20 GB free) when that matters. Renders can be re-made; raw clips are the other
thing worth keeping.
