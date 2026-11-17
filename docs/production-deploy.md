# Production Deploy (Docker Compose)

A reference Docker Compose layout for running unbork in production
behind a TLS-terminating reverse proxy, with persistent volumes
and env-sourced secrets.

> **Canonical alternative:** the `.deb` / `.rpm` install with the
> systemd unit at [`packaging/systemd/unbork.service`](../packaging/systemd/unbork.service)
> is the canonical production deployment for non-container hosts.
> It is documented in the main [README](./README.md#installation).
> This compose example is for shops that already run Docker and
> want one consistent deployment shape.

- [Layout](#layout)
- [compose.yaml](#composeyaml)
- [Caddyfile](#caddyfile)
- [Secrets via .env](#secrets-via-env)
- [unbork.toml](#unborktoml)
- [Sizing Postgres connections](#sizing-postgres-connections)
- [Bring it up](#bring-it-up)
- [Upgrading PostgreSQL 16 → 18](#upgrading-postgresql-16--18)
- [Differences from `dev/docker-compose.yml`](#differences-from-devdocker-composeyml)

---

## Layout

```
/srv/unbork/
├── compose.yaml
├── Caddyfile
├── .env                    # secrets — chmod 0600, NOT in git
├── unbork.toml             # non-secret config
└── caddy_data/             # ACME certs (auto-managed by Caddy)
```

Volumes managed by the compose file:

- `postgres_data` — PostgreSQL 18 data directory. Mounted at
  `/var/lib/postgresql` (PG 18 keeps the cluster in a version-specific
  subdirectory below that — do **not** mount at `.../data`).
- `unbork_packs` — DB-backed action-pack checkouts. Mounts onto
  `ansible.packs_root_dir` (default `/var/lib/unbork/packs` —
  see `backend/app/config.py`). Every action pack is synced into it,
  including the seeded `unbork-playbooks` one; without the volume they
  are cloned again on every start. A site without GitHub access edits
  the `unbork-playbooks` repository (**Settings › Git repositories**) to
  point at an internal mirror, or adds a local-directory pack.
- `caddy_data` — Caddy's ACME state.

---

## compose.yaml

```yaml
services:
  postgres:
    image: postgres:18-alpine
    environment:
      POSTGRES_USER: unbork
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: unbork
    volumes:
      # PG 18: mount the parent dir, not .../data (see "Upgrading" note below).
      - postgres_data:/var/lib/postgresql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U unbork"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    # 8.x line carries the fix for CVE-2026-23479 (also patched in 7.4.9).
    image: redis:8-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  unbork:
    image: unbork/unbork:1.0.0    # pin a real version
    environment:
      UNBORK_DATABASE__URL: postgresql+asyncpg://unbork:${POSTGRES_PASSWORD}@postgres:5432/unbork
      UNBORK_REDIS__URL: redis://redis:6379/0
      UNBORK_SECURITY__SECRET_KEY: ${UNBORK_SECURITY__SECRET_KEY}
      UNBORK_SECURITY__ENCRYPTION_KEY: ${UNBORK_SECURITY__ENCRYPTION_KEY}
      UNBORK_SECURITY__UNBORK_SERVER_IP: ${UNBORK_SECURITY__UNBORK_SERVER_IP}
      UNBORK_SECURITY__COOKIE_SECURE: "true"
      UNBORK_SECURITY__ALLOWED_ORIGINS: '["https://unbork.example.com"]'
      UNBORK_TLS__FORCE_HTTPS: "true"
      UNBORK_SERVER__TRUSTED_PROXIES: '["caddy"]'
      UNBORK_SERVER__FORWARDED_ALLOW_IPS: "*"
    volumes:
      - unbork_packs:/var/lib/unbork/packs
      # Claude Code's own session files. Only needed if you use a Claude
      # subscription provider — but then it is load-bearing: an AI session
      # paused waiting for your approval resumes from here, and a pause can
      # last a day. Without the volume, upgrading the image mid-approval
      # loses everything the assistant established before it stopped.
      - unbork_claude:/var/lib/unbork/claude-cli
      # Optional: mount a host-side TOML for non-secret tunables.
      - ./unbork.toml:/etc/unbork/unbork.toml:ro
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    expose:
      - "8000"        # not published to the host — Caddy reaches it via the docker network
    restart: unless-stopped

  caddy:
    image: caddy:2-alpine
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"   # HTTP/3
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - unbork
    restart: unless-stopped

volumes:
  postgres_data:
  unbork_packs:
  unbork_claude:
  caddy_data:
  caddy_config:
```

---

## Caddyfile

```caddy
unbork.example.com {
    encode gzip

    @ws path /api/ssh-terminal/ws/*
    reverse_proxy @ws unbork:8000

    reverse_proxy unbork:8000 {
        header_up X-Real-IP {remote}
        header_up X-Forwarded-For {remote}
        header_up X-Forwarded-Proto {scheme}
    }

    request_body {
        max_size 10MB
    }
}
```

Caddy provisions and renews the certificate via ACME on first
start. For internal deployments where ACME isn't reachable, swap
in a local CA cert via `tls /etc/ssl/certs/unbork.crt
/etc/ssl/private/unbork.key` and bind-mount the cert files.

---

## Secrets via .env

**Do NOT bake secrets into `unbork.toml`.** Source them from the
environment, where they're easier to rotate and won't end up in a
container image, a git repo, or a backup snapshot tarball by
accident.

```bash
# /srv/unbork/.env  — chmod 0600, owned by root or the deploy user
POSTGRES_PASSWORD=<run: openssl rand -base64 24>
UNBORK_SECURITY__SECRET_KEY=<run: openssl rand -base64 32>
UNBORK_SECURITY__ENCRYPTION_KEY=<run: openssl rand -base64 32>
UNBORK_SECURITY__UNBORK_SERVER_IP=10.0.0.42
```

Generate the keys once, on the deploy host:

```bash
openssl rand -base64 32      # SECRET_KEY
openssl rand -base64 32      # ENCRYPTION_KEY (32 bytes, base64)
openssl rand -base64 24      # POSTGRES_PASSWORD
```

`docker compose` picks `.env` up automatically (it has to be next
to `compose.yaml`). Lock the file down:

```bash
sudo chown root:root /srv/unbork/.env
sudo chmod 0600 /srv/unbork/.env
```

For an extra layer, hand the keys to the container as Docker
secrets and read them via the `_FILE` convention or a small
entrypoint shim — but `.env` with `chmod 0600` is the minimum bar.

---

## unbork.toml

The non-secret subset. Mount this into the container at
`/etc/unbork/unbork.toml`. Anything sensitive belongs in the
environment (see above) — env values override TOML values.

```toml
[server]
host = "0.0.0.0"   # listening inside the container; Caddy is upstream
port = 8000

[logging]
level  = "info"
format = "json"     # easier to scrape from a centralised log store

[ssh]
max_sessions_per_user = 5
max_total_sessions    = 50

[discovery]
min_prefix    = 22
max_bulk_add  = 100

[ansible]
packs_root_dir = "/var/lib/unbork/packs"

[rate_limit]
enabled = true
login   = "5/minute"
api     = "100/minute"
```

How long the audit log, action runs and drift samples are kept is not a
`unbork.toml` setting. Set it once unbork is up, under
[Settings › System](ui/settings.md#logging-and-retention); a production
install usually wants the audit log kept longer than the 90-day default.

---

## Sizing Postgres connections

Every Celery task opens its own short-lived database engine (asyncpg
connections cannot cross a fork), and a host sync, an action step or a
sweeper may hold a few at once. Budget **about 6 Postgres connections per
worker slot**, counting both pools — `[celery] concurrency` plus
`orchestrator_concurrency` — and add the API's pool
(`[database] pool_size + max_overflow`, 5 + 10 by default) and a margin
for `psql` and backups.

The defaults (4 + 4 slots) need about 70, which fits stock Postgres
(`max_connections = 100`). At 16 + 12 slots unbork measured 90–130
connections idle and 183 at peak; on a stock server it logged hundreds of
`TooManyConnectionsError` within minutes, and failed syncs with them. So
before raising either concurrency, raise `max_connections` to match
(in the compose file above: `command: postgres -c max_connections=300`),
or put a pooler such as PgBouncer in transaction mode in front of
Postgres.

---

## Bring it up

```bash
cd /srv/unbork
docker compose pull
docker compose up -d

# First-boot logs — alembic creates the schema, lifespan syncs packs
docker compose logs -f unbork
```

Open `https://unbork.example.com`, register the first user, and
proceed with [post-install configuration](./README.md#post-install-configuration)
in the main README.

For backups, see [backup-restore.md](./backup-restore.md). The
backup script needs the Postgres credentials and read access to
`unbork.toml` (or whichever env source holds the encryption key) —
it does not need to run inside the container.

---

## Upgrading PostgreSQL 16 → 18

Existing deployments on `postgres:16-alpine` cannot simply bump the
image tag: PostgreSQL 18's Docker image changed the data-directory
layout (data now lives in a version-specific subdirectory, and the
volume is mounted at `/var/lib/postgresql` instead of `.../data`), and
a major version won't start on a cluster written by an older major.
Because unbork's database is small, a **logical dump/restore** is the
right method (not in-place `pg_upgrade`).

> **Keep `UNBORK_SECURITY__ENCRYPTION_KEY` (and `SECRET_KEY`)
> unchanged.** unbork encrypts stored SSH credentials at the app layer,
> so the dump holds ciphertext — restoring under a different encryption
> key makes every credential undecryptable.

```bash
cd /srv/unbork

# 1. Belt-and-suspenders: back up the raw PG 16 volume.
docker run --rm -v unbork_postgres_data:/data -v "$PWD":/backup alpine \
  tar czf /backup/pg16-volume-$(date +%F).tgz -C /data .

# 2. Quiesce the app (stop writes; leave PG 16 running).
docker compose stop unbork

# 3. Dump the database from the running PG 16 container.
docker compose exec -T postgres pg_dump -U unbork -Fc unbork > unbork-$(date +%F).dump

# 4. Stop the stack.
docker compose down
```

5. Edit `compose.yaml`: set `image: postgres:18-alpine`, change the
   mount to `postgres_data:/var/lib/postgresql` (drop `/data`), and use
   a **new, empty volume name** (e.g. `postgres_data_v18`) — do not
   remount the PG 16 volume, or PG 18 will trip over the legacy files.

```bash
# 6. Bring up only Postgres so a fresh PG 18 cluster initializes.
docker compose up -d postgres
docker compose exec postgres pg_isready -U unbork   # wait until ready

# 7. Restore into the fresh cluster.
docker compose exec -T postgres pg_restore -U unbork -d unbork \
  --clean --if-exists < unbork-$(date +%F).dump

# 8. Start the app (Alembic is a no-op — the dump is already at head).
docker compose up -d
docker compose logs -f unbork

# 9. Once verified (log in, check hosts + stored SSH creds work),
#    reclaim the old volume. Keep the .dump + tarball as a rollback path.
docker volume rm unbork_postgres_data
```

**Redis needs none of this** — Redis 8 reads 7.x RDB/AOF and unbork's
Redis holds only transient broker/schedule state, so
`redis:7.4-alpine` → `redis:8-alpine` is a plain tag swap.

---

## Differences from `dev/docker-compose.yml`

The dev compose file is intentionally minimal — it is not a
production blueprint. Concrete differences:

| Concern | `dev/docker-compose.yml` | This file |
|---|---|---|
| TLS | None — plain HTTP on `:8000` | Caddy sidecar with ACME |
| `cookie_secure` | `false` | `true` (via env) |
| `force_https` | unset (`false`) | `true` (via env) |
| Postgres port | Published on host `:5432` | Internal docker network only |
| Backend port | Published on host `:8000` | Internal docker network only |
| Pack volume | None — DB-backed packs lost on restart | `unbork_packs` volume |
| Postgres data | `postgres_data` named volume | Same — kept |
| Secret source | Inline `${VAR:-default}` with insecure fallbacks | `.env`, no defaults |
| Allowed origins | `http://localhost:3000` (config default) | `https://unbork.example.com` |
| Logging format | `text` | `json` |

If you're starting from `dev/docker-compose.yml`, working through
the table left-to-right is a reasonable hardening checklist.
