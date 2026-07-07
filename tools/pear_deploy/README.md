# Pear staging deployment

This directory contains the reproducible path for hosting this checkout as a
friends-only Zulip development server at `https://pear.backpropaganda.net`.

The deployment is intentionally a normal VM, not a tunnel:

- DigitalOcean hosts an Ubuntu droplet.
- GoDaddy points `pear.backpropaganda.net` at that droplet.
- Caddy terminates HTTPS and forwards directly to Zulip devlogin.
- The current local checkout is synced to `/home/zulipdev/zulip`.
- `tools/run-dev` plus the Pear queue workers run under systemd.
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

Do not commit `env.local`, `output/pear-staging/`, or `.codex/auth.json`.

## Access hardening (optional)

The box runs Zulip's development server, so `/devlogin` and the dev
API-key endpoints are reachable by anyone who can hit the site. To put an
HTTP basic-auth wall in front of it, export before bootstrapping (or a
data reset):

- `PEAR_CADDY_BASICAUTH_USER` — login name (defaults to `pear`)
- `PEAR_CADDY_BASICAUTH_HASH` — a bcrypt hash from `caddy hash-password`

When `PEAR_CADDY_BASICAUTH_HASH` is unset the Caddyfile stays open (current
friends-demo behavior) and the scripts print a warning. To also limit who
can drive the agent, set `PEAR_WORKER_TRUSTED_USER_IDS` in `pear.env` to a
comma-separated list of trusted user ids (see the commented line in
`bootstrap_remote.sh`).

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

This installs base packages, syncs this dirty checkout, provisions Zulip,
installs the same Codex CLI version as this Mac when possible, copies Codex
auth, creates/selects the embedded `Pear` bot in the dev realm, writes
Caddy/systemd config, and starts:

- `pear-zulip-dev.service`
- `pear-embedded-bots.service`
- `pear-worker.service`
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
the local `pear-main` history. The `--yes` run snapshots the current remote
checkout, syncs this checkout with the same excludes as bootstrap, applies
`zerver` migrations, restarts `pear-worker`, `pear-embedded-bots`, and
`pear-zulip-dev` together, verifies the Pear backend tests, checks live Pear
worker health (`tools/pear-health-report`) and queue emptiness, and records the
deployed SHA in `/home/zulipdev/pear-deployed-sha`. If a post-snapshot step
fails, it restores the pre-deploy snapshot and restarts the Pear services.

## Cleanup

```bash
python3 tools/pear_deploy/pear_do.py destroy --yes --delete-dns
```
