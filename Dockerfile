# unbork — single-image build
# Produces a container that runs the API, the Celery workers and beat, and serves
# the static frontend — all from `python -m app`.
#
# Every FROM is pinned by digest with the tag kept alongside it for
# readability. `python:3.12-slim` in particular is rebuilt continuously,
# so a tag-only pin meant two builds of one commit produced different
# images. The cost is that a pinned base goes stale: dependabot's `docker`
# ecosystem (.github/dependabot.yml) opens the bump PRs, and the trivy
# scan is the backstop that makes ignoring one loud.

# ── Stage 1: Build frontend static export ─────────────────────────────
FROM node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS frontend-builder
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci --silent
COPY frontend/ .
# The npm packages the pages ship, with their licences, for the notices
# file the final stage writes (S-010). Read from node_modules, so it lists
# exactly what this build installed.
RUN node scripts/licences.mjs > /tmp/frontend-licences.json
ENV NEXT_TELEMETRY_DISABLED=1
# WITH_ASSISTANT=0 builds an image without the AI assistant (C-026): its
# two pages are not built, the Agent SDK is not installed, and the server
# runs with [assistant] enabled = false. Nothing else imports from these
# directories.
ARG WITH_ASSISTANT=1
RUN if [ "$WITH_ASSISTANT" = "0" ]; then \
        rm -rf "app/(dashboard)/assistant" "app/(dashboard)/ai-providers"; \
    fi \
    && npm run build
# Output: /app/out/

# ── Stage 2: Build Python backend + install deps ──────────────────────
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS backend-builder
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY backend/pyproject.toml backend/uv.lock ./
COPY VERSION .
# Install the exact locked dependency versions (reproducible builds). The app
# package itself isn't installed — the runtime stage runs it from the copied
# source (WORKDIR /app, `python -m app`), so only the deps need to be present.
#
# --extra agent pulls the Claude Agent SDK, which is what drives an
# agentic session against a Claude *subscription* rather than metered API
# credit. Its Python code stays in the image (MIT). The Claude Code program
# its wheel carries does not: it is deleted here, before any layer of the
# final image can hold it, and downloaded on first use instead (S-009,
# app/ai/claude_code.py). The pin that download is checked against is
# written from uv.lock by scripts/claude_code_pin.py.
COPY backend/scripts/claude_code_pin.py /tmp/claude_code_pin.py
ARG WITH_ASSISTANT=1
RUN extra="--extra agent"; [ "$WITH_ASSISTANT" = "0" ] && extra=""; \
    uv export --frozen --no-emit-project $extra --format requirements-txt -o /tmp/req.txt \
    && uv pip install --no-cache-dir --system -r /tmp/req.txt \
    && rm -rf /usr/local/lib/python3.12/site-packages/claude_agent_sdk/_bundled \
    && python /tmp/claude_code_pin.py --lock uv.lock --out /tmp/claude_code_pin.json

# ── Stage 3: Runtime ──────────────────────────────────────────────────
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app

# Install the runtime tools we need, then fully upgrade every base
# package so Debian security fixes are applied (e.g. krb5 libs pulled in
# transitively by git/openssh-client). A blanket upgrade avoids the
# whack-a-mole of naming each CVE'd package as Trivy/Grype flag them.
#
# BUILD_DATE is declared here (and re-declared later for the version
# stamp) solely to bust this layer's BuildKit cache. CI builds with
# `cache-from/to: type=gha`, so without a per-build input the cached apt
# layer keeps serving packages from whenever the cache was populated —
# security fixes published afterwards (e.g. libssh2 +deb13u1) never land
# and Trivy gates the stale image. Referencing the per-build BUILD_DATE
# forces apt to refresh on every CI build. Local builds (no BUILD_DATE)
# keep the cached layer, which is fine — they aren't security-gated.
#
# Pinning the base image by digest (above) does NOT replace this, despite
# what it looks like. The digest freezes the base, so the layer cache
# above never invalidates on its own and `apt-get upgrade` here is the
# *only* route by which a patched libssl or libssh2 reaches the image.
# Removing BUILD_DATE alongside the digest pin would leave the image
# frozen at whatever apt served the day the cache was filled.
ARG BUILD_DATE=""
RUN echo "apt security refresh @ ${BUILD_DATE}" \
    && apt-get update \
    && apt-get install -y --no-install-recommends openssh-client git tini \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 unbork \
    && mkdir -p /var/lib/unbork/packs /var/lib/unbork/claude-cli /var/lib/unbork/claude-code \
    && chown -R unbork:unbork /var/lib/unbork

# Python packages from builder
COPY --from=backend-builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=backend-builder /usr/local/bin /usr/local/bin
# uv / uvx are install-time only — drop them from the runtime image.
# Their bundled rustls-webpki has produced HIGH-severity advisories
# (e.g. GHSA-82j2-j2ch-gfr8) that we'd otherwise need to track in
# .trivyignore for a binary that's never actually invoked at runtime.
RUN rm -f /usr/local/bin/uv /usr/local/bin/uvx

# No Claude Code program in the image: a worker downloads the pinned build
# into /var/lib/unbork/claude-code the first time a provider needs it
# (S-009). CI fails the build if a `claude` binary reaches this image.

# Backend source (app + alembic). Action packs are not part of the image:
# they are synced from git at runtime (the seeded unbork-playbooks pack).
COPY --chown=unbork:unbork backend/app/ app/
# The pin as the lock says it, in case the committed copy lags.
COPY --from=backend-builder --chown=unbork:unbork /tmp/claude_code_pin.json app/ai/claude_code_pin.json
# Without the assistant the server must not load it either (C-026). A
# marker rather than ENV UNBORK_ASSISTANT__ENABLED: an environment
# variable would outrank the operator's config file in every image.
ARG WITH_ASSISTANT=1
RUN if [ "$WITH_ASSISTANT" = "0" ]; then touch app/_built_without_assistant; fi
COPY --chown=unbork:unbork backend/alembic/ alembic/
COPY --chown=unbork:unbork backend/alembic.ini alembic.ini
# The image runs from source with deps installed via --no-emit-project, so the
# unbork-backend .dist-info is absent and importlib.metadata can't report the
# version. Ship the VERSION file at the app root so /api/version (and thus the
# healthcheck) resolves it — see app/api/version.py:_resolve_version().
COPY --chown=unbork:unbork VERSION VERSION

# Frontend static files
COPY --from=frontend-builder --chown=unbork:unbork /app/out/ /usr/lib/unbork/frontend/out/

# Licence, third-party notices and the source offer (S-010), in
# /usr/share/doc/unbork as Debian does it. The notices are written from
# what this image actually holds: its Python packages, the npm packages
# the pages ship, and its Debian packages. The build fails on a Python or
# npm component whose licence is unknown or not compatible with
# AGPL-3.0-or-later (packaging/licence-overrides.toml for reviewed
# exceptions).
COPY --from=frontend-builder /tmp/frontend-licences.json /tmp/frontend-licences.json
COPY backend/scripts/third_party_notices.py /tmp/third_party_notices.py
COPY packaging/licence-overrides.toml /tmp/licence-overrides.toml
COPY LICENSE SOURCE-OFFER.md /usr/share/doc/unbork/
RUN python /tmp/third_party_notices.py \
        --frontend /tmp/frontend-licences.json \
        --dpkg-status /var/lib/dpkg/status \
        --overrides /tmp/licence-overrides.toml \
        --out /usr/share/doc/unbork/THIRD-PARTY-NOTICES \
    && rm /tmp/frontend-licences.json /tmp/third_party_notices.py /tmp/licence-overrides.toml

# Build metadata. CI passes both via --build-arg in the build-image and
# build-test-image jobs; a local `docker build` without them yields empty
# values and /api/version reports a "dev build".
ARG GIT_SHA=""
ARG BUILD_DATE=""
ENV UNBORK_COMMIT_SHA=$GIT_SHA \
    UNBORK_BUILD_DATE=$BUILD_DATE
LABEL org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.source="https://github.com/open-unbork/unbork" \
      org.opencontainers.image.revision=$GIT_SHA \
      org.opencontainers.image.documentation="https://github.com/open-unbork/unbork/tree/main/docs"

USER unbork
EXPOSE 8000

# Lets container orchestrators (Docker, k8s, compose) detect a stuck
# process. /api/version is a no-auth endpoint that exercises the
# FastAPI app at a minimum. Python is used instead of curl to avoid
# adding an extra runtime dep -- python is already in the image.
# /health/ready, not /api/version: the old probe only proved that uvicorn
# was answering. It never touched the database, Redis or the Celery
# children, so a container whose worker had died stayed "healthy" forever
# while nothing executed a single task (BUG-72). start-period covers
# startup migrations and the worker boot; a 503 names the failing
# component in its body.
# app.healthcheck does exactly that for the default "all" and the "api"
# role, and the matching check for the split worker / beat roles
# (UNBORK_ROLE), which serve no HTTP.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -m app.healthcheck || exit 1

# BUG-55: an init as PID 1, so orphaned grandchildren get reaped.
#
# The app shells out to git; git spawns ssh for SSH remotes and exits
# first; the orphaned ssh re-parents to PID 1. When PID 1 was `python -m
# app` — which never wait()s on children it did not spawn — each one
# stayed a zombie holding a task slot for the life of the container. One
# instance reached 115 of them (114 ssh, 1 git) at roughly 26 a day, and
# the count only ever grows: the end state is a host that cannot fork(),
# which takes a reboot to clear.
#
# Deployments could set `init: true` themselves, and the one that found
# this did. That is the wrong place for it — the published image is run
# by people who will not know to. tini forwards signals to the app, so
# SIGTERM shutdown is unchanged.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "app"]
