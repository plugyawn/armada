# Pear staging deployment

This directory contains the reproducible path for hosting this checkout as a
friends-only Zulip development server at `https://pear.backpropaganda.net`.

The deployment is intentionally a normal VM, not a tunnel:

- DigitalOcean hosts an Ubuntu droplet.
- GoDaddy points `pear.backpropaganda.net` at that droplet.
- Caddy terminates HTTPS behind a mandatory HTTP basic-auth wall.
- The current local checkout is synced to `/home/zulipdev/zulip`.
- `tools/run-dev --production-assets` serves prebuilt, content-hashed assets
  with `DEVELOPMENT=True` and `DEBUG=False`; webpack/HMR and web-process
  autoreload are not present on the hosted runtime.
- Codex auth is copied from this Mac to the `zulipdev` account, so Pear can use
  the same ChatGPT-authenticated Codex CLI session without an API key.

## Required secrets

The scripts load exported environment variables and also load
`/Users/progyan/codack/env.local` when it exists. Supported names:

- DigitalOcean: `DIGITALOCEAN_ACCESS_TOKEN`, `DO_API_TOKEN`, or
  `DIGITAL_OCEAN_API`
- GoDaddy key: `GODADDY_API_KEY` or `GODADDY_API_PROD_KEY`
- GoDaddy secret: `GODADDY_API_SECRET` or `GODADDY_API_PROD_SECRET`
- Gemini ambient router: `PEAR_ADDRESSING_ROUTER_GEMINI_API_KEY`,
  `GEMINI_API_KEY`, `GEMINI_KEY`, `GOOGLE_API_KEY`, or
  `GOOGLE_GENAI_API_KEY`
- Caddy login name: `PEAR_CADDY_BASICAUTH_USER`
- Caddy bcrypt hash file: `PEAR_CADDY_BASICAUTH_HASH_FILE`. The file must
  contain exactly one hash emitted by `caddy hash-password`, must be a readable
  regular non-symlink file, and is streamed to the box only on stdin. The hash
  is never accepted as a plaintext environment value or command argument.

Do not commit `env.local`, `output/pear-staging/`, or `.codex/auth.json`.

## Access boundary (required)

The box runs Zulip's development server, so `/devlogin` and the dev
API-key endpoints must never be internet-reachable without an HTTP basic-auth
wall. Before the first bootstrap, create the hash file without committing it:

```bash
umask 077
caddy hash-password > /absolute/private/path/pear-caddy.bcrypt
export PEAR_CADDY_BASICAUTH_USER=pear
export PEAR_CADDY_BASICAUTH_HASH_FILE=/absolute/private/path/pear-caddy.bcrypt
```

Bootstrap and update fail closed when `/etc/pear/caddy-basic-auth.env` is
absent or invalid. A guarded update can create that root-owned mode-0600 file
from the local hash file; subsequent updates reuse the remote file. The
checked-in Caddyfile, Caddy systemd drop-in, Zulip wrapper/unit, and `pear.env`
are installed and verified by `runtime_config.py`. To also limit who can drive
the agent, set `PEAR_WORKER_TRUSTED_USER_IDS` in `pear.env` to a comma-separated
list of trusted user ids.

## Create or refresh the droplet and DNS

From the Zulip checkout:

```bash
python3 tools/pear_deploy/pear_do.py create
```

This is idempotent. It reuses the existing droplet named
`pear-backpropaganda` unless `--recreate` is passed. It writes:

- `output/pear-staging/server.json`
- `output/pear-staging/ssh_config`

## Bootstrap, sync, and start services

```bash
tools/pear_deploy/bootstrap_remote.sh
```

This installs base packages, syncs this checkout, provisions Zulip,
installs the same Codex CLI version as this Mac when possible, copies Codex
auth, builds and validates production assets, creates/selects the embedded
`Pear` bot in the dev realm, installs authenticated Caddy/systemd config, and
starts:

- `pear-zulip-dev.service`
- `pear-embedded-bots.service`
- `pear-worker.service`
- `pear-monitor-worker.service`
- `pear-thumbnail.service`
- `caddy.service`

After it finishes, test:

```bash
tools/pear_deploy/verify_remote.sh
```

## Fresh demo data

To reset the hosted dev database to a quiet six-user devlogin realm, run:

```bash
tools/pear_deploy/reset_remote_dev_data.sh
tools/pear_deploy/reset_remote_dev_data.sh --yes
```

The first command is a read-only dry run. The `--yes` run removes generated
message history, Pear worker runs, topic contexts, sessions, drafts, scheduled
messages, and extra generated users; keeps only `plugyawn`, `sidbing`,
`jayesh`, `berry`, `glitchfix`, and `ritvi` as active human devlogin users; and
re-creates the embedded `Pear` bot.

## SSH

```bash
ssh -F output/pear-staging/ssh_config pear-staging
ssh -F output/pear-staging/ssh_config pear-staging-root
```

Useful service checks:

```bash
sudo systemctl status pear-zulip-dev pear-embedded-bots pear-worker caddy
sudo journalctl -u pear-zulip-dev -u pear-embedded-bots -u pear-worker -f
```

## Re-sync after local edits

Use the guarded update script instead of re-running the full bootstrap:

```bash
tools/pear_deploy/update_remote.sh
tools/pear_deploy/update_remote.sh --yes
```

The first command is a read-only gate. It verifies that the local tracked tree is
clean, the checkout is the main `zulip/` submodule rather than a linked Git
worktree, the remote tracked tree is clean, and the remote HEAD is not ahead of
the local `pear-main` history. The `--yes` run snapshots the current source,
generated assets, Git metadata, Pear skills, Caddy auth/config, `pear.env`,
wrapper, and systemd units. It source-syncs with generated outputs excluded,
builds production assets from the synced source, validates the hashed graph,
using an isolated sibling checkout while the old app remains live. It then
stops the coordinated services for the short source/asset cutover, applies
`zerver` migrations, restarts Caddy and all Pear services, verifies the
backend tests, rejects webpack/autoreload processes and the legacy `/webpack/`
route, checks live worker/queue health, and records the coordinated SHA and
`asset_mode=production-hashed`. A post-snapshot failure restores every covered
domain before restarting the old services.

Snapshots are retained until explicitly pruned. The retention command is also
read-only by default:

```bash
tools/pear_deploy/prune_remote_snapshots.sh --keep 10
tools/pear_deploy/prune_remote_snapshots.sh --keep 10 --yes
```

## Cleanup

```bash
python3 tools/pear_deploy/pear_do.py destroy --yes --delete-dns
```
