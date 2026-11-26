# Upgrade and Rollback

How to move a running unbork install from one release to the next,
and how to back out cleanly when something goes wrong.

> **Read this first:** an upgrade is a destructive operation on the
> database — alembic migrations rewrite tables, and not every
> migration has a clean downgrade path. The pre-upgrade backup is
> non-negotiable in production. Without it, the only rollback story
> is "restore from yesterday's snapshot, lose a day of audit log".

- [Compatibility](#compatibility)
  - [Versioning](#versioning)
  - [Upgrading to 2.0.0: the rename](#upgrading-to-200-the-rename)
- [Pre-upgrade](#pre-upgrade)
  - [Docker: check the environment variables](#docker-check-the-environment-variables)
- [Upgrading](#upgrading)
  - [Docker](#docker)
  - [Debian / Ubuntu (.deb)](#debian--ubuntu-deb)
  - [RHEL / Fedora / Rocky (.rpm)](#rhel--fedora--rocky-rpm)
- [Verifying the upgrade](#verifying-the-upgrade)
- [Rolling back](#rolling-back)
  - [Pin the previous Docker tag](#pin-the-previous-docker-tag)
  - [Reinstall the previous package](#reinstall-the-previous-package)
  - [When alembic downgrade is not enough](#when-alembic-downgrade-is-not-enough)
- [References](#references)

---

## Compatibility

The alembic migration chain is forward-only and covers every schema
change since v0.1.0, so upgrading from any earlier release to the
latest follows the single procedure below. The current released line
is whatever the [latest GitHub Release](https://github.com/open-unbork/unbork/releases/latest)
says — this page deliberately does not name a version, because a
hardcoded one goes stale the moment it ships.

Each release notes in `CHANGELOG.md` whether it carries breaking
schema changes, deprecated config fields, or non-reversible
migrations. Read that section before upgrading.

### Versioning

From 1.0.0, unbork follows [Semantic Versioning](https://semver.org/).
A change that breaks existing use of anything below comes only in a new
major version (3.0.0). A minor release (2.1.0) only adds to them, and a
patch release (2.0.1) only fixes bugs. 2.0.0 was such a major release:
it renamed everything below from the old product name (see
[Upgrading to 2.0.0: the rename](#upgrading-to-200-the-rename)).

- **Configuration**: the keys in `unbork.toml`, the `UNBORK_*`
  environment variables, and the setting keys under **Settings**.
- **The pack format**: the manifest a pack declares its actions in, and
  the action keys and parameters it can use (see
  [Actions](ui/actions.md)).
- **The GitOps file format** (see [the GitOps guide](examples/gitops/README.md)).
- **The webhook endpoints** under `/api/webhooks/` (`github`, `gitlab`,
  `gitea`, `grafana-alerts`) and the payloads they accept, including the
  alert labels unbork reads: `unbork_action`, `unbork_ai`,
  `unbork_ai_automate`, `unbork_host_id` and `unbork_hostname` (see
  [Alerts](ui/alerts.md#labels)).
- **The Prometheus metrics**: names and labels in
  [Metrics export](metrics-export.md).
- **The REST calls these docs tell you to make**, such as
  `POST /api/hosts/{id}/trust-host-key`.
- **The upgrade path**: every release from 1.0.0 upgrades to every 2.x
  release by the procedure on this page, with the steps under
  "Upgrading to 2.0.0" when coming from 1.x.

Not covered, and free to change in a minor release: the web UI's layout,
the rest of `/api` (it is the UI's backend), the database schema (only
ever changed by migrations), log messages, and what the AI assistant is
told and allowed to run.

One exception: a fix that closes a security hole may refuse something
that used to work, in any release. Its upgrade notes say so.

### Upgrading to 2.0.0: the rename

2.0.0 is the first release under the name unbork. Every name an operator
types or sees changed with it, and **the old names are not read any
more**: there are no fallbacks, and nothing renames or removes old names
for you, on the server or on managed hosts (the one exception is the
seeded pack, below). Plan the upgrade as a short
maintenance window and work through this list before you start the new
version.

| What | Before 2.0.0 | From 2.0.0 |
|---|---|---|
| Environment variables | `LABDOG_*` (`LABDOG_SECURITY__SECRET_KEY`, `LABDOG_CONFIG`, `LABDOG_ROLE`, …) | `UNBORK_*` |
| Config file | `/etc/labdog/labdog.toml`, `<checkout>/labdog.toml` | `/etc/unbork/unbork.toml`, `<checkout>/unbork.toml` |
| Config key | `[security] labdog_server_ip` | `[security] unbork_server_ip` |
| Package, service, system user | `labdog`, `labdog.service`, user and group `labdog` | `unbork`, `unbork.service`, user and group `unbork` |
| Directories | `/usr/lib/labdog`, `/var/lib/labdog`, `/var/log/labdog`, `/run/labdog` | `/usr/lib/unbork`, `/var/lib/unbork`, `/var/log/unbork`, `/run/unbork` |
| Container volumes | `/var/lib/labdog/packs`, `/var/lib/labdog/claude-cli` | `/var/lib/unbork/packs`, `/var/lib/unbork/claude-cli` |
| Python distribution | `labdog-backend`, `labdog-lint` | `unbork-backend`, `unbork-lint` |
| Session cookies | `labdog_auth`, `labdog_csrf` | `unbork_auth`, `unbork_csrf` (everyone signs in again once) |
| Prometheus metrics | `labdog_*` | `unbork_*` |
| Alert labels | `labdog_action`, `labdog_ai`, `labdog_ai_automate`, `labdog_host_id`, `labdog_hostname` | `unbork_action`, `unbork_ai`, `unbork_ai_automate`, `unbork_host_id`, `unbork_hostname` |
| Variables handed to packs | `labdog_host_id`, `labdog_hostname` | `unbork_host_id`, `unbork_hostname` |
| iptables chains | `LABDOG-INPUT`, `LABDOG-OUTPUT` | `UNBORK-INPUT`, `UNBORK-OUTPUT` |
| Rule comments | `Managed by LabDog` | `Managed by unbork` |
| Files on hosts | `/etc/NetworkManager/conf.d/90-labdog-dns.conf`, `/etc/cloud/cloud.cfg.d/99-labdog-disable-network-config.cfg`, `/etc/systemd/system/<service>.service.d/labdog.conf`, CA files `labdog-<fingerprint>.crt` | the same with `unbork` |
| Proxmox snapshots | `labdog-…`, `labdog-ai-…` | `unbork-…`, `unbork-ai-…` |
| The seeded pack | `labdog-playbooks` | `unbork-playbooks` (renamed by a migration unless you renamed it yourself) |

Unchanged: the Postgres database and role keep whatever names your
`database.url` gives them (only the example defaults now say `unbork`),
and all data in the database is kept.

These steps are for 1.1.x (1.1.0 or 1.1.1). From an older release,
upgrade to 1.1.1 first by the procedure further down this page.

**Before you start:** back up the database and the config with its
encryption key. On 1.1.x they still have the old names:

```bash
sudo -u postgres pg_dump --format=custom --dbname=<your database> \
  --file=/var/backups/labdog-pre-2.0.0.dump
sudo install -m 0600 /etc/labdog/labdog.toml /var/backups/labdog.toml-pre-2.0.0
# Docker: the .env next to compose.yaml holds the LABDOG_* secrets.
```

The way back is that backup; see [Rolling back to 1.1.x](#rolling-back-to-11x)
below. Then upgrade the server for your install type.

**Docker.** The image is `unbork/unbork` now; 1.1.x was published as
`openlabdog/labdog` (1.1.1 also as `unbork/unbork:1.1.1`, still with the
old names inside). In the compose file or `.env`:

1. Set the image to `unbork/unbork:2.0.0`.
2. Rename every `LABDOG_` variable to `UNBORK_`, including
   `LABDOG_CONFIG` if you set it.
3. Move every volume mount target from `/var/lib/labdog/…` to
   `/var/lib/unbork/…`. Keep the volume names, or the data stays behind
   in the old volume.
4. Check the new image accepts the environment before you replace the
   container, as [Docker: check the environment
   variables](#docker-check-the-environment-variables) shows, then
   `docker compose up -d`.

**Packages (.deb, .rpm).** The `unbork` package is installed next to
`labdog`: it does not replace it, and nothing is copied for you. In this
order, because removing an `.rpm` renames an edited config file to
`labdog.toml.rpmsave`:

```bash
sudo systemctl stop labdog

sudo apt install ./unbork_2.0.0-1_amd64.deb      # Debian, Ubuntu
sudo dnf install ./unbork-2.0.0-1.x86_64.rpm     # RHEL, Rocky, Fedora

# The config, with its secrets and database URL; then its one renamed key
# and the paths under the old directories (packs_root_dir, by default).
sudo install -o root -g unbork -m 0640 /etc/labdog/labdog.toml /etc/unbork/unbork.toml
sudo sed -i -e 's/^labdog_server_ip/unbork_server_ip/' \
  -e 's|"/var/lib/labdog/|"/var/lib/unbork/|' -e 's|"/var/log/labdog/|"/var/log/unbork/|' \
  /etc/unbork/unbork.toml

# The data (packs, the Claude Code sign-in): copied, so the old copy stays
# for a rollback.
sudo cp -a /var/lib/labdog/. /var/lib/unbork/
sudo chown -R unbork:unbork /var/lib/unbork

# Remove, not purge: a purge deletes /etc/labdog and /var/lib/labdog.
sudo apt remove labdog                             # or: sudo dnf remove labdog

sudo systemctl enable --now unbork
sudo journalctl -u unbork -f                       # migrations run at start
```

**Tarball.** Stop `labdog`, extract the 2.0.0 tarball and run its
`./install.sh`, then do the copy steps above (`install`, `sed`, `cp -a`,
`chown`). Remove 1.1.x with the `./uninstall.sh` from its own tarball,
without `--purge`, and start the service with
`sudo systemctl enable --now unbork`.

**After the upgrade:** go through [Verifying the upgrade](#verifying-the-upgrade).
Everyone signs in again once, since the session cookies were renamed;
accounts and passwords are unchanged. Until `/etc/labdog/labdog.toml` is
gone, every start logs a warning that names it, as it does for any path
setting still under `/var/lib/labdog` or another old directory. When you
no longer want to roll back, delete what is left of 1.1.x: `sudo apt purge
labdog`, which also removes the `labdog` user; on `.rpm` and tarball
installs, `sudo rm -rf /etc/labdog /var/lib/labdog /var/log/labdog` and
`sudo userdel labdog`.

A server whose secrets are set only under the old names refuses to
start, and its error lists the old variables and config file it found.

**In Grafana and Prometheus:** rename the metric names in dashboards and
alert rules (`labdog_` to `unbork_`), and the labels in alert rules that
start actions or the assistant. Then rerun the `alloy-install` action
from the updated `unbork-playbooks` pack on every monitored host, so new
series carry `unbork_host_id`. History stored under the old names stays
under them. On a host where `alloy-install` added its files to the
operator's own Alloy directory (its `adopt_dir` mode), delete the old
`labdog_*.alloy` files in that directory before rerunning it; the new run
writes `unbork_*.alloy` next to them and does not remove the old ones.

**On managed hosts, after the first 2.0.0 sync of each host:** the sync
writes the new names but does not remove the old ones. Clean each host
up by hand:

```bash
# iptables hosts: drop the old jumps and chains (IPv4 and IPv6).
for t in iptables ip6tables; do
  while $t -D INPUT -j LABDOG-INPUT 2>/dev/null; do :; done
  while $t -D OUTPUT -j LABDOG-OUTPUT 2>/dev/null; do :; done
  $t -F LABDOG-INPUT 2>/dev/null; $t -X LABDOG-INPUT 2>/dev/null
  $t -F LABDOG-OUTPUT 2>/dev/null; $t -X LABDOG-OUTPUT 2>/dev/null
done
netfilter-persistent save 2>/dev/null || service iptables save

# Files the old version wrote, if the new ones are in place.
rm -f /etc/NetworkManager/conf.d/90-labdog-dns.conf \
      /etc/cloud/cloud.cfg.d/99-labdog-disable-network-config.cfg \
      /etc/systemd/system/*.service.d/labdog.conf
rm -f /usr/local/share/ca-certificates/labdog-*.crt /etc/pki/ca-trust/source/anchors/labdog-*.crt
update-ca-certificates 2>/dev/null || update-ca-trust
systemctl daemon-reload
```

nftables hosts need nothing for the firewall: the sync replaces the
whole `inet filter` table. Old Proxmox snapshots named `labdog-…` are no
longer cleaned up by unbork; delete them in Proxmox when you no longer
need them.

#### Rolling back to 1.1.x

2.0.0's migrations run when it first starts, and 1.1.x refuses a
database at a revision it does not know. So the way back is the backup:

1. Stop unbork and keep it from starting again: `sudo systemctl disable
   --now unbork`, or stop the container. Left enabled, it would start at
   the next boot and migrate the restored database to 2.0.0 again.
2. Restore the database from the pre-upgrade dump. Changes made in
   unbork since the upgrade are lost:

   ```bash
   sudo -u postgres dropdb <your database>
   sudo -u postgres createdb -O <its owner role> <your database>
   sudo -u postgres pg_restore --no-owner --role=<its owner role> \
     --dbname=<your database> /var/backups/labdog-pre-2.0.0.dump
   ```

3. Start 1.1.x again. Packages: `sudo apt install
   ./labdog_1.1.1-1_amd64.deb`, which finds its config and data where
   removing it left them (on `.rpm`: `sudo dnf install
   ./labdog-1.1.1-1.x86_64.rpm`, then `sudo mv
   /etc/labdog/labdog.toml.rpmsave /etc/labdog/labdog.toml`), then `sudo
   systemctl enable --now labdog`, and remove unbork with `sudo apt
   remove unbork` (`sudo dnf remove unbork`). Docker: put back the old
   compose file and image. Tarball: the 1.1.x tarball's `./install.sh`,
   then the 2.0.0 tarball's `./uninstall.sh` without `--purge`.

On managed hosts, a 2.0.0 sync wrote the new names and 1.1.x does not
know them: on iptables hosts both `UNBORK-*` and `LABDOG-*` chains are
then jumped from `INPUT` and `OUTPUT`. Remove the new ones with the
commands below, with `UNBORK` in place of `LABDOG`, and resync from
1.1.x. Files written under `unbork` names stay until you delete them.

#### The Claude Code program is no longer in the image

The image used to carry the Claude Code program for the "Claude agent"
and "Claude single-shot" providers. It no longer does: the first time
such a provider is turned on, tested or used, a worker downloads the
build that this release pins from `files.pythonhosted.org`, checks its
checksum and keeps it in `/var/lib/unbork/claude-code`. Until that
finishes, such a provider shows "installing" and a session started
meanwhile is refused with "being installed; try again in a few minutes".
It happens once while the directory is kept.

- Add a volume for `/var/lib/unbork/claude-code` (see the compose file in
  [production-deploy.md](production-deploy.md)), or the program is
  downloaded again after every container replacement. A volume on the
  whole of `/var/lib/unbork` already covers it.
- A server without outbound HTTPS to `files.pythonhosted.org` cannot
  download it. The provider then shows "unavailable" with the reason.
  Install a `claude` program on PATH for the `unbork` user yourself
  (a derived image, or a package install's system-wide install), and
  unbork uses that.
- When the API and the workers run in separate containers
  ([scaling-out.md](scaling-out.md)), share the `claude-code` volume
  between them, as the packs volume is. The providers page and Test read
  the API's copy; a worker that has none downloads its own on its first
  session.
- API-key providers need no program and are not affected.

#### Claude Code sign-in

The "Claude agent" and "Claude single-shot" providers take no token any
more; they use a Claude Code sign-in made on the server, kept in
`/var/lib/unbork/claude-cli` (see
[Signing in](ui/assistant.md#signing-in)). A provider that holds a token
saved by an earlier version keeps using it until you clear it on the AI
providers page.

If you signed in earlier in the service user's home directory
(`~/.claude`) instead, unbork keeps using that sign-in until one exists
in `/var/lib/unbork/claude-cli`. On Docker the home directory is not on a
volume, so sign in again as the docs describe before you replace the
container.

### Upgrading to 1.0.0

Nothing is required unless you deleted the seeded `labdog-playbooks`
pack or unbork cannot reach GitHub (see 1), or alerts are investigated
automatically today (see 6). Every new thing that can change a host or
send mail is off until you turn it on: alert investigations stay
read-only, no alert runs at full auto unless its Grafana rule is labelled
`labdog_ai_automate=true`, no alert runs an action until you allow that
action under **Operations → Alerts → Handling**, no mail server is
configured and nobody is subscribed, and schedules keep reading their
cron expressions in UTC. Six things are worth checking.

**1. The bundled action pack is gone.** The image and the `.deb` / `.rpm` /
`.tar.gz` packages no longer carry a copy of `labdog-playbooks`. Actions
come only from the packs under **Operations → Actions → Packs**, so:

- If you still have the seeded `labdog-playbooks` pack, nothing changes:
  it has been supplying the same actions all along, and the keys it
  shared with the bundled copy stop being contested. The **pinned** and
  **frozen** badges on them go away.
- A pin that chose **bundled** is deleted by the upgrade. If another pack
  declares that key, it now wins on its own; if two or more do, the key
  needs a winner again and its actions can't run until you pick one.
- If you deleted `labdog-playbooks`, or unbork cannot reach GitHub, its
  actions disappear until a pack provides them. Re-add it (repository
  `https://github.com/open-unbork/unbork-playbooks`, branch `main`, then a
  pack on that repository), point the repository at a mirror, or add a
  local-directory pack.

**2. The AI assistant refuses more at read-only.** A command now counts
as read-only only in a form listed in `backend/app/ai/command_policy.yaml`;
a subcommand or option the list does not name counts as a change. Before,
it was the other way round, so most of `qm`, `pct` and `pvesh`, container
runtimes, systemd tools, `ip`, package managers and storage tools ran in a
read-only session whatever the subcommand. `wget`, `man`, `xxd`, `initctl`
and `at` now need approval too. A read-only session, which is what
scheduled checks and alert investigations run as by default, will be
refused commands it used to run. The refusal names the read-only forms of
the command, so the assistant usually finds one; if a scheduled check
keeps failing on one, look at its refused calls in the session. See
[Autonomy levels](ui/assistant.md#autonomy-levels).

**3. Retention keys in `labdog.toml` now log a warning at startup.**
`audit_retention_days`, `run_retention_days` and `drift_retention_days`
under `[logging]`, or as `LABDOG_LOGGING__…` variables, never did
anything: retention is read only from **Settings › System**. Check that
the values there are what you meant to keep, then delete the keys. The
packaged `labdog.toml` no longer has them, so a `.deb` or `.rpm` upgrade
of a modified config may ask which version to keep.

**4. A pack playbook that imports a file next to it is no longer loaded.**
Only the playbook's own text and the pack's roles reach a run, so a
playbook using `import_tasks`, `include_tasks`, `vars_files` or
`import_playbook` with a relative path never got past its first task. It
is now refused when the pack loads: the action is missing from the
library, and the log says `pack '<name>': failed to load manifest …`,
naming the files. Move them into a role. No `labdog-playbooks` action is affected.

**5. If you rotated the encryption key on 0.10.0 or earlier**, re-enter
each Git repository's webhook secret. The rotation script skipped that
column, so signed push webhooks have answered 500 since, and GitOps has
not imported a push. See
[encryption-key-rotation.md](encryption-key-rotation.md).

**6. Alerts are investigated only when their labels ask for it.** With
`ai.auto_investigate_enabled` on, 0.10.0 investigated every alert at or
above `ai.auto_investigate_min_severity`. Now an alert is investigated
only when its Grafana rule labels it `labdog_ai=true`, or
`labdog_ai_automate=true` to let the AI fix what it finds. **Until you
add a label, alerts are recorded and nothing else happens.** Add
`labdog_ai=true` to the alert rules you want investigated.
`ai.auto_investigate_min_severity` is gone, and the upgrade deletes its
stored value. See [Alerts](ui/alerts.md#labels).

An alert labelled `labdog_ai_automate=true` runs at full auto, behind the
safeguards in [Alerts](ui/alerts.md#full-auto-safeguards). Nobody adds
that label by accident. But anyone who can post to the alert webhook with
its token could send an alert carrying it, and the webhook token of at
least 32 characters is the only lock. To keep that decision in unbork,
turn on `ai.alert_full_auto_rules_enabled` and list in
`ai.alert_full_auto_rules` what may act. An empty list allows nothing.

Smaller things to know:

- **Two alert settings were renamed**: `ai.alert_intake_enabled` is now
  `alerts.intake_enabled`, and `ai.alertmanager_poll_minutes` is
  `alerts.poll_minutes`, under Settings › Fleet defaults › Alert intake.
  The upgrade carries their values over; only scripts that set them
  through `/api/settings` need the new names.
- **The `security.allowed_origins` workaround for the web terminal** from
  the 0.10.0 notes can go: the terminal accepts its own origin again. Keep
  the setting only for a frontend served from another host.
- **Email** needs `notifications.public_url` set before it sends anything
  with a link; unbork will not build links from a request's `Host`
  header. See [Notifications](ui/notifications.md).
- **Alerts moved** from the Assistant to **Operations → Alerts**, and
  their API from `/api/ai/alerts` to `/api/alerts`. The old paths still
  answer, deprecated (2.0.0 keeps them), until a later major release.
- **Full auto** is for alerts whose Grafana rule is labelled
  `labdog_ai_automate=true`, or, with `ai.alert_full_auto_rules_enabled` on,
  those matching `ai.alert_full_auto_rules`. It needs Grafana's contact
  point to send resolved notifications, or every fix is judged as not
  working, and it is refused on the machine unbork itself runs on. See
  [Alerts](ui/alerts.md#which-alerts-may-run-at-full-auto).
- **Setting `scheduling.timezone`** keeps every schedule's clock time and
  moves it to the new zone: `0 3 * * *` then runs at 03:00 there. Check
  existing schedules after changing it.
- **The navigation was rebuilt.** Old URLs (`/dashboard`, `/schedules`,
  `/action-packs`, `/hosts/discover`, `/groups/{id}/rules` and the other
  module pages) redirect, so bookmarks keep working.
- **Docker:** no compose change is needed.

Eight migrations (`0039`–`0046`) apply with the normal step below. All
are forward-safe. Three repair data and leave it repaired on downgrade:
`0039` advances id sequences, `0040` empties host-run output that was
two quote characters, `0041` fills in each host's last sync time.
Downgrading past `0045` and `0046` drops the mail settings, outbox and
subscriptions, and the rollback history.

### Upgrading to 0.10.0

This release is mostly the fixes from a full security and correctness
audit. Three things can bite an existing install; check them **before**
restarting, not after.

**1. unbork now refuses to start on a weak `security.secret_key`.** It
must be at least 32 characters. `openssl rand -base64 32` — what the
install docs already told you to run — gives 44. If your key is shorter,
or is still the `CHANGE_ME` placeholder from the packaged
`labdog.toml`, the service will not come up until you replace it. Two
consequences of replacing it: every session is invalidated (see 2), and
if the old key was a placeholder, treat every account as having been
exposed — that key is published in this repository.

`security.allowed_origins = ["*"]` is refused for the same reason; list
your real origins. Plain HTTP on a trusted LAN still works and now logs
a warning about `cookie_secure` rather than failing.

On Docker the same rules apply to the environment variables:
`LABDOG_SECURITY__SECRET_KEY` must be at least 32 characters and not
`CHANGE_ME`, and `LABDOG_SECURITY__ALLOWED_ORIGINS` must not contain
`"*"`. A variable overrides the same key in a `labdog.toml`, so on a
Docker install the variable is the one to check. See
[Docker: check the environment variables](#docker-check-the-environment-variables)
for a check that runs the new release's own validation before you
switch over.

**2. Everyone is logged out once.** Sessions now carry a generation
number so that logging out or changing a password actually revokes the
cookie. Existing cookies have no generation and are rejected. From here
on, logging out or changing your password signs you out everywhere,
including the browser you did it in.

**3. Old history is deleted on the first scheduler tick.** Two things
combine here:

- Finished action runs and sync jobs are pruned for the first time ever,
  using the new `logging.run_retention_days` (default **90**; `0` keeps
  everything).
- The daily maintenance jobs — audit-log, SSH-transcript and AI-snapshot
  pruning — were registered in a way that meant they **never ran** on a
  deployment that restarts more than once a day. They now do, so the
  first audit-log prune may delete a lot at once, per
  `logging.audit_retention_days` (default 90).

Set both values first if you want to keep more. Both are in
**Settings** only: the same keys in `labdog.toml` have no effect.

Smaller things to know:

- **Settings that never took effect now do**, on the next restart:
  `ansible.playbook_timeout`, `ssh.connect_timeout`,
  `ssh.idle_timeout_seconds`, `discovery.max_concurrent`,
  `logging.audit_retention_days`, `actions.preflight_enabled` and
  `ai.wall_clock_seconds`. A value someone set long ago and forgot will
  now be honoured — lowering `ansible.playbook_timeout` below a slow
  playbook's real runtime will now actually kill it.
  `SELECT key, value FROM app_settings` shows what is stored.
- **`/docs`, `/redoc` and `/openapi.json` are no longer served** unless
  `server.expose_docs = true`.
- **Ansible runs and git syncs now verify SSH host keys** against the key
  unbork recorded on first contact. A host or git server that is
  legitimately re-keyed will start failing with a mismatch; clear it with
  `POST /api/hosts/{id}/trust-host-key` or
  `POST /api/git-repos/{id}/trust-host-key`.
- **Action packs that ship controller-side code** (`action_plugins/`,
  `library/`, `connection: local`, …) are now refused unless the pack is
  marked trusted. Packs that exist at upgrade time are marked trusted by
  the migration; packs added afterwards are not.
- Webhook secrets are encrypted in place; nothing to re-enter. The API
  reports `has_webhook_secret` instead of the value.
- Duplicate `host_groups.priority` values are renumbered by the
  migration (the older group keeps its number); every change is logged.
- `firewall_rules.is_system` and `hosts_entries.is_system` are dropped.
  Nothing ever wrote them; if you set one by hand the migration logs the
  row count before dropping it.
- **Docker:** Celery beat is now a third supervised subprocess, and
  `/health/ready` fails if it dies. No compose change is needed.
- **The web terminal fails with "Connection failed: Closed (1006)"**
  on 0.10.0 unless the UI's origin is listed in
  `security.allowed_origins`, which a deployment serving the UI and API
  from one host never needed before. Set it, for example
  `LABDOG_SECURITY__ALLOWED_ORIGINS='["https://labdog.example.com"]'`.
  From the release after 0.10.0 the terminal accepts its own origin
  without it (BUG-88), and the list is only needed for a frontend served
  from elsewhere.

Fourteen migrations (`0025`–`0038`) apply with the normal step below.
All are forward-safe; `0037` and `0038` are documented as lossy on
downgrade.

### Upgrading to 0.9.0

Nothing is required — the AI assistant is off by default and stays off
until you set `ai.enabled` and configure a provider. The release adds
schema for it, applied by the normal migration step below.

**If you intend to enable it on Docker**, add one volume before you do:

```yaml
volumes:
  - labdog_claude:/var/lib/labdog/claude-cli
```

This holds Claude Code's own session files, and only matters for the
Claude CLI / Agent SDK backends. Sessions that pause for approval resume
by their CLI session id, so without the volume an approval granted after
a container restart has nothing left to resume — and approvals can wait
up to `ai.approval_expiry_hours` (default 24). See
[production-deploy.md](production-deploy.md).

**If unbork connects to your hosts as a non-root user**, check that user
has passwordless sudo, or the verify step cannot read the system journal.
It will report the reading as `UNAVAILABLE` rather than pretending the
host was quiet, so nothing breaks silently — but you lose a signal.

**Releases are cut from `main`.** Merging a release PR (`dev` →
`main`) triggers `release-artifacts`, which auto-tags `vX.Y.Z` from
the repo-root [`VERSION`](https://github.com/open-unbork/unbork/blob/main/VERSION) file and publishes the
`.tar.gz` / `.deb` / `.rpm` / `SHA256SUMS` set as a GitHub Release.
There is no separate `git tag` step — the release artifacts on the
GitHub Releases page are what you install from. See
[CONTRIBUTING.md → Release process](pathname:///../CONTRIBUTING.md#release-process).

No action pack ships with a release. Actions come from packs synced
at runtime, the seeded `labdog-playbooks` pack among them, so new
playbooks arrive with a pack sync rather than an upgrade.

---

## Pre-upgrade

Take a complete backup *before touching anything*. See
[backup-restore.md](./backup-restore.md) for the full procedure; the
short form:

```bash
# 1. Database
pg_dump --dbname=unbork --format=custom \
  --file=/var/backups/unbork/unbork-pre-upgrade-$(date +%Y%m%d-%H%M%S).dump

# 2. Encryption key + config
sudo install -m 0600 /etc/unbork/unbork.toml \
  /var/backups/unbork/unbork.toml-pre-upgrade-$(date +%Y%m%d)
```

On Docker the keys are not in `unbork.toml`. They live in `UNBORK_*`
variables, usually in the `.env` file next to `compose.yaml` (see
[production-deploy.md](production-deploy.md#secrets-via-env)). Back
that file up too, along with any `unbork.toml` you mount:

```bash
sudo install -m 0600 /etc/unbork/.env \
  /var/backups/unbork/unbork.env-pre-upgrade-$(date +%Y%m%d)
```

If the variables are written straight into `compose.yaml` or come from
a secrets manager, back up wherever they actually live.

A backup set without the encryption key is unrecoverable ciphertext.
Both files together, in the same directory, on a host that's not the
one you're about to upgrade.

### Docker: check the environment variables

The upgrade notes above name `unbork.toml` keys. A Docker install sets
the same settings as `UNBORK_` variables, with the section and key
joined by `__`: `[security] secret_key` is
`UNBORK_SECURITY__SECRET_KEY`. A variable wins over the same key in a
`unbork.toml`, so check the variables. The ones the upgrade notes
mention:

| `unbork.toml` key | Variable | What to check |
|---|---|---|
| `[security] secret_key` | `UNBORK_SECURITY__SECRET_KEY` | At least 32 characters, and not `CHANGE_ME`, `change-me-in-production` or `change-me-32-bytes-base64-encoded`. Otherwise unbork does not start (0.10.0). |
| `[security] encryption_key` | `UNBORK_SECURITY__ENCRYPTION_KEY` | Set, not a placeholder, and the same key as before. A different key cannot decrypt the stored SSH keys. |
| `[security] allowed_origins` | `UNBORK_SECURITY__ALLOWED_ORIGINS` | A JSON list of your real origins, without `"*"`, which is refused (0.10.0). From 1.0.0 it is only needed for a frontend served from another host. |
| `[security] cookie_secure` | `UNBORK_SECURITY__COOKIE_SECURE` | `true` once unbork is behind TLS. With `true`, a `localhost` origin in the list is refused. |
| `[security] unbork_server_ip` | `UNBORK_SECURITY__UNBORK_SERVER_IP` | The address your hosts see unbork connect from. The SSH lockout rule allows SSH from it when unbork has not recorded a host's own view of that address. Unset, it is `127.0.0.1`, which no remote host's firewall will match. |
| `[logging] audit_retention_days`, `run_retention_days`, `drift_retention_days` | `UNBORK_LOGGING__AUDIT_RETENTION_DAYS` and so on | Never had an effect, and log a warning from 1.0.0. Set retention under **Settings › System** and delete the variables. |

To list which `UNBORK_` variables the running container has, without
printing their values:

```bash
docker compose -f /etc/unbork/compose.yaml exec unbork \
  sh -c 'env | cut -d= -f1 | grep ^UNBORK_ | sort'
```

To check the secret key's length without printing it:

```bash
docker compose -f /etc/unbork/compose.yaml exec unbork \
  sh -c 'printf %s "$UNBORK_SECURITY__SECRET_KEY" | wc -c'
```

The thorough check is to let the new release validate your
configuration before you switch over. Set the new image tag in
`compose.yaml` and pull it as in [Docker](#docker) below, then, before
`up -d`, start a throwaway container from it that only loads the
settings:

```bash
docker compose -f /etc/unbork/compose.yaml run --rm --no-deps unbork \
  python -c 'import app.config'
```

It gets the same variables as the real container and touches neither
the database nor the running container. When the configuration is
accepted it exits 0, printing at most a warning (such as the one about
`cookie_secure` on plain HTTP). When it is not, it exits 1 with
`FATAL: unbork cannot start:` and one line per problem, the same
message the real container would log in a restart loop. Fix those, run
it again, and then go on with `up -d`.

---

## Upgrading

Alembic migrations run automatically on container start (Docker) or
on service start (`unbork.service` → `python -m app`). You do not
run `alembic upgrade head` manually for a normal release upgrade.

### Docker

Pin the tag to a specific version — never run `:latest` in
production. Each release is tagged on Docker Hub as `X.Y.Z`, `X.Y` and
`X`: `unbork/unbork:2.0.0` never changes, while `2.0` follows the
patch releases of 2.0 and `2` every 2.x release. `latest` follows every
release, and the `dev-*` and `test-*` tags are unreleased builds.

```bash
# 1. Note the version you're on, in case rollback is needed.
docker inspect unbork | grep -i 'image\|unbork/unbork'

# 2. Set the new tag in compose.yaml (see below) and pull it.
docker compose -f /etc/unbork/compose.yaml pull unbork

# 3. Check the new release accepts your UNBORK_* variables
#    (see "Docker: check the environment variables" above).
docker compose -f /etc/unbork/compose.yaml run --rm --no-deps unbork \
  python -c 'import app.config'

# 4. Recreate the container.
docker compose -f /etc/unbork/compose.yaml up -d unbork

# 5. Watch the logs as alembic runs.
docker compose -f /etc/unbork/compose.yaml logs -f unbork
```

Update the image tag in `compose.yaml` to the new version so the
next `up -d` doesn't accidentally roll forward to whatever `latest`
points at:

```yaml
services:
  unbork:
    image: unbork/unbork:2.0.0
```

### Debian / Ubuntu (.deb)

```bash
VERSION=2.0.0
curl -LO https://github.com/open-unbork/unbork/releases/download/v${VERSION}/unbork_${VERSION}-1_amd64.deb
sudo apt install ./unbork_${VERSION}-1_amd64.deb

# systemd will restart unbork.service automatically; alembic runs
# at startup. Watch the logs:
sudo journalctl -u unbork -f
```

### RHEL / Fedora / Rocky (.rpm)

```bash
VERSION=2.0.0
curl -LO https://github.com/open-unbork/unbork/releases/download/v${VERSION}/unbork-${VERSION}-1.x86_64.rpm
sudo dnf install ./unbork-${VERSION}-1.x86_64.rpm

sudo systemctl restart unbork.service
sudo journalctl -u unbork -f
```

---

## Verifying the upgrade

Three checks, in order:

```bash
# 1. Version reported by the running process matches the tag.
curl -fsS http://127.0.0.1:8000/api/version
# → {"version":"0.3.0","commit_sha":"…","commit_sha_short":"…",
#    "build_date":"2026-05-12T09:14:37Z",
#    "license":"AGPL-3.0-or-later",
#    "repo_url":"https://github.com/open-unbork/unbork"}
# (Also visible in the UI at Settings › System, in the About panel.)

# 2. Health endpoint returns 200.
curl -fsS http://127.0.0.1:8000/health
# → {"status":"ok"}

# 3. End-to-end: trigger one sync against a known-good test host.
#    Use the UI (Operations → Plans, pick the host, apply) or the API.
#    A successful sync confirms SSH keys decrypt, packs load, and
#    the celery worker is healthy after the restart.
```

If `/api/version` reports an older `version` than the tag, the
package install succeeded but the service didn't restart. `sudo systemctl restart
unbork.service`. If `/health` returns 5xx, check
`journalctl -u unbork -f` — the most common cause is an alembic
failure on a column type change, which leaves the schema half-
migrated. Stop the service and restore from the pre-upgrade backup
before doing anything else.

---

## Rolling back

The canonical rollback is **restore the pre-upgrade backup**. It
covers every case, including migrations that are not cleanly
reversible.

If the migration was trivial and reversible (additive columns, new
tables, no data rewrites), pinning the previous binary version is
faster — but verify against the new release's `CHANGELOG.md` first.

### Pin the previous Docker tag

```bash
# Update compose.yaml to the previous version, then:
docker compose -f /etc/unbork/compose.yaml up -d unbork
```

If alembic on the older binary refuses to start because the schema
is at a newer revision than it knows about, you must restore from
backup — see [When alembic downgrade is not enough](#when-alembic-downgrade-is-not-enough).

### Reinstall the previous package

From 2.0.0 back to 1.1.x the package name differs, so a downgrade does
not apply: follow [Rolling back to 1.1.x](#rolling-back-to-11x).

```bash
# Debian / Ubuntu
sudo apt install ./unbork_0.1.0-1_amd64.deb

# RHEL / Fedora / Rocky
sudo dnf downgrade ./unbork-0.1.0-1.x86_64.rpm

sudo systemctl restart unbork.service
```

### When alembic downgrade is not enough

Alembic downgrades are written best-effort, but data-rewriting
migrations (column type changes, splits, merges, reseeds) are not
always reversible without data loss. **Treat the pre-upgrade
backup as the canonical rollback** and stop trying to be clever:

```bash
sudo systemctl stop unbork.service
sudo -u postgres dropdb unbork
sudo -u postgres createdb unbork
pg_restore --dbname=unbork --clean --if-exists --no-owner --no-acl \
  /var/backups/unbork/unbork-pre-upgrade-YYYYMMDD-HHMMSS.dump

# Reinstall the previous binary version (see above), then:
sudo systemctl start unbork.service
```

The encryption key in `/etc/unbork/unbork.toml` did not change, so
encrypted columns decrypt correctly against the restored DB.

---

## References

- [backup-restore.md](./backup-restore.md) — pre-upgrade backup
  procedure and full restore steps.
- [Release process in CONTRIBUTING.md](pathname:///../CONTRIBUTING.md#release-process)
  — how a new version gets cut, what's in the artifact set.
- [CHANGELOG.md](pathname:///../CHANGELOG.md) — per-release
  breaking-change notes; read before every upgrade.
