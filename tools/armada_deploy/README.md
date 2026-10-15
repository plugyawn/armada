# Pear staging deployment

This directory contains the reproducible path for hosting this checkout as a
friends-only Zulip development server at `https://armada.backpropaganda.net`.

The deployment is intentionally a normal VM, not a tunnel:

- DigitalOcean hosts an Ubuntu droplet.
- GoDaddy points `armada.backpropaganda.net` at that droplet.
- Caddy terminates public HTTPS without an outer username/password prompt.
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
- Gemini Interactions client: `ARMADA_GEMINI_INTERACTIONS_API_KEY`,
  `GEMINI_API_KEY`, `GEMINI_KEY`, `GOOGLE_API_KEY`, or
  `GOOGLE_GENAI_API_KEY`

Do not commit `env.local`, `output/armada-staging/`, or `.codex/auth.json`.

## Access boundary

The hosted scratch instance intentionally exposes Zulip's passwordless
`/devlogin` page directly through public HTTPS. There is no outer Caddy
username/password prompt. Anyone who discovers the URL can choose a listed
development user, including an administrator, and the development API-key
endpoint is also reachable.

Keep only disposable scratch data on this instance. `ARMADA_WORKER_TRUSTED_USER_IDS`
can limit which selected Zulip identities may start runs, but it is not an
authentication boundary while passwordless devlogin is public. Restore a real
identity provider or an outer access layer before storing sensitive data or
treating the site as private.

## Create or refresh the droplet and DNS

From the Zulip checkout:

```bash
python3 tools/armada_deploy/armada_do.py create
```

This is idempotent. It reuses the existing droplet named
`armada-backpropaganda` unless `--recreate` is passed. It writes:

- `output/armada-staging/server.json`
- `output/armada-staging/ssh_config`

## Bootstrap, sync, and start services

```bash
tools/armada_deploy/bootstrap_remote.sh
```

This installs base packages, syncs this checkout, provisions Zulip,
installs the same Codex CLI version as this Mac when possible, copies Codex
auth, builds and validates production assets, creates/selects the embedded
`Pear` bot in the dev realm, installs public Caddy/systemd config, and
starts:

- `armada-zulip-dev.service`
- `armada-embedded-bots.service`
- `armada-worker.service`
- `armada-worker-1.service`
- `armada-monitor-worker.service`
- `armada-thumbnail.service`
- `caddy.service`

The two Codex worker queues retain an unacknowledged RabbitMQ delivery while a
turn is running. Bootstrap and guarded updates therefore install the vhost `/`
policy `armada-worker-consumer-timeout` on `armada_worker` and `armada_worker_1`, with
a six-hour acknowledgement timeout, before either worker starts. The deploy
then verifies that both queues are classic, durable, and effectively carry the
policy. This policy is intentionally additive broker state: a code rollback
leaves it installed because it is also safe for the former single-worker
topology and prevents a healthy long turn from being redelivered at RabbitMQ's
30-minute default.

After it finishes, test:

```bash
tools/armada_deploy/verify_remote.sh
```

## Fresh demo data

To reset the hosted dev database to a quiet six-user devlogin realm, run:

```bash
tools/armada_deploy/reset_remote_dev_data.sh
tools/armada_deploy/reset_remote_dev_data.sh --yes
```

The first command is a read-only dry run. The `--yes` run removes generated
message history, Pear worker runs, topic contexts, sessions, drafts, scheduled
messages, and extra generated users; keeps only `plugyawn`, `sidbing`,
`jayesh`, `berry`, `glitchfix`, and `ritvi` as active human devlogin users; and
re-creates the embedded `Pear` bot.

## SSH

```bash
ssh -F output/armada-staging/ssh_config armada-staging
ssh -F output/armada-staging/ssh_config armada-staging-root
```

Useful service checks:

```bash
sudo systemctl status armada-zulip-dev armada-embedded-bots armada-worker armada-worker-1 caddy
sudo journalctl -u armada-zulip-dev -u armada-embedded-bots -u armada-worker -u armada-worker-1 -f
```

## Re-sync after local edits

Use the guarded update script instead of re-running the full bootstrap:

```bash
tools/armada_deploy/update_remote.sh
tools/armada_deploy/update_remote.sh --yes
```

The first command is a read-only gate. It verifies that the local tracked tree is
clean, the checkout is the main `zulip/` submodule rather than a linked Git
worktree, the remote tracked tree is clean, and the remote HEAD is not ahead of
the local `pear-main` history. The `--yes` run classifies the committed delta.
Frontend, template, asset-tooling, and unknown changes build production assets
in an isolated sibling checkout while the old app remains live. A narrow
allowlist of server-only Pear and test changes instead reuses the already
validated hashed graph; unknown paths fail closed to a rebuild. It then stops
app ingress, drains and stops the queue workers, copies only regular single-link
generated outputs into a root-owned frozen tree when rebuilding, and snapshots
the drained source, prior assets, Git metadata, Pear skills, Caddy auth/config, `armada.env`,
privileged helper, deployment record, wrapper, and systemd units. Privileged
deployment managers, templates, and helper source are staged directly from the
pinned local commit in a root-only directory; root never executes tooling from
the service-user-owned checkout. The cutover source-syncs with generated
outputs excluded, applies `zerver` migrations, restarts Caddy and all Pear
services, verifies the
backend tests, rejects webpack/autoreload processes and the legacy `/webpack/`
route, checks live worker/queue health, and records the coordinated SHA,
`asset_mode=production-hashed`, and exact `asset_source_zulip_sha` in root-owned
`/var/lib/armada/deployed-sha`. A pre-snapshot failure restarts the untouched
services; a post-snapshot failure restores every covered domain before
restarting the old services.

Snapshots are retained until explicitly pruned. The retention command is also
read-only by default:

```bash
tools/armada_deploy/prune_remote_snapshots.sh --keep 10
tools/armada_deploy/prune_remote_snapshots.sh --keep 10 --yes
```

## Cleanup

```bash
python3 tools/armada_deploy/armada_do.py destroy --yes --delete-dns
```
