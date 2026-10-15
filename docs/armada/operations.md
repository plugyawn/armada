---
orphan: true
---

# Operating Armada

How the hosted server is kept: its configuration, its backups, how it is
watched, and what to do when something breaks. `deploy.md` says how a
server is built; this file is what runs on it afterwards. Each check in
`armada-ops-check` names one of the sections below.

The hosted beta is the DigitalOcean droplet `armada-beta` (BLR1). Its
backups are in the Spaces bucket `armada-beta-backups` in SGP1, another
region, so a lost region loses neither.

## Configuration as code

Everything that makes a Zulip server an Armada server is the puppet module
`puppet/armada/`, shipped in the tarball like Zulip's own. Turn it on in
`/etc/zulip/zulip.conf`:

```ini
[machine]
puppet_classes = armada::profile::standalone
```

It includes `zulip::profile::standalone`, and adds:

| Class                   | What it keeps                                                                                                                                                                                    |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `armada::settings`      | Armada's settings, as a marked block at the end of `/etc/zulip/settings.py`                                                                                                                      |
| `armada::agent_runtime` | the Codex CLI at a pinned version in `/opt/codex`, Bubblewrap and its sysctl, `/srv/armada/worktrees` (RabbitMQ's consumer timeout for the agent's queues comes from `zulip::profile::rabbitmq`) |
| `armada::hooks`         | the deploy hook that links the sprites into each new deployment                                                                                                                                  |
| `armada::nginx_brotli`  | Ubuntu's brotli modules for nginx, for text assets only, and the deploy hook that writes `.br` files for new assets                                                                              |
| `armada::patching`      | daily security updates; `needrestart` lists services and never restarts them                                                                                                                     |
| `armada::backups`       | wal-g's endpoint and encryption key, `archive_timeout`, restic and its timers                                                                                                                    |
| `armada::ops_check`     | the ops check and its timer                                                                                                                                                                      |

Per-server values go in the `[armada]` section of `zulip.conf`; the
defaults are the beta's.

| Key                                                                                                                                                                                                                                   | Default                                  | Used by            |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- | ------------------ |
| `worker_agent_runtime`, `codex_bin`, `worktree_root`, `worker_sandbox`, `trusted_users_only`, `max_active_runs_per_realm`, `worker_model`, `worker_model_choices` (comma-separated), `worker_service_tier`, `worker_reasoning_effort` | the beta's (`templates/settings.py.erb`) | the settings block |
| `codex_version`                                                                                                                                                                                                                       | `0.157.1`                                | the Codex install  |
| `backup_restic_repository`                                                                                                                                                                                                            | none: no file backups                    | restic             |
| `backup_s3_endpoint`                                                                                                                                                                                                                  | none                                     | wal-g              |
| `ops_stream`, `ops_bot_email`, `ops_host`                                                                                                                                                                                             | `ops`, none, `EXTERNAL_HOST`             | the ops check      |

Secrets go in `/etc/zulip/zulip-secrets.conf`, `[secrets]`:
`s3_region`, `s3_backups_bucket` (`armada-beta-backups/postgresql`: the
bucket and the prefix), `s3_backups_key`, `s3_backups_secret_key` (Zulip's
own names, which turn on its wal-g support), `armada_backup_restic_password`,
`armada_backup_walg_libsodium_key` (64 hex characters) and
`armada_ops_bot_api_key`.

What a fresh server still needs by hand: `zulip.conf` and the secrets above
(from the offline key file), the organization, `armada-ops-bot-setup`, and a
person signing Codex in (`deploy.md`, 6.3).

**Upgrading onto the module.** The hand-written block of Armada settings at
the end of the beta's `settings.py` uses the names from before the rename;
delete it in the same upgrade that turns the module on, since the server
refuses to start with an old name in its settings. Keep your own settings
above the managed block, which wins over anything above it.

## Backups

| What                                                                                                       | How                                                                                                                                                         | How far behind | Kept                                     |
| ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- | ---------------------------------------- |
| PostgreSQL                                                                                                 | Zulip's wal-g support: every WAL segment, at least once a minute (`archive_timeout = 60`), and a base backup nightly at 02:00 UTC; encrypted with libsodium | ≤ 1 minute     | 30 days, point in time                   |
| Uploads, worktrees, `/etc/zulip`, the sprites, Codex's sessions (not its login), the zulip user's SSH keys | restic, hourly at a quarter past, at idle priority                                                                                                          | ≤ 1 hour       | 48 hourly, 14 daily, 8 weekly, 6 monthly |
| The database again, in Zulip's own format                                                                  | `manage.py backup --skip-uploads` into restic, nightly at 21:30 UTC (03:00 in India)                                                                        | ≤ 1 day        | 14 daily, 8 weekly, 6 monthly            |
| The whole disk                                                                                             | DigitalOcean's weekly droplet backup                                                                                                                        | ≤ 7 days       | 4 weeks                                  |

The bucket is versioned, and an old version expires after 30 days. The
server's Spaces key can read and write only this bucket; it can also
delete old versions, so a stolen server key could destroy the Spaces
copies. The droplet backup, which no Spaces key can reach, is the second
system for that case.

**The keys.** The offline copy of every key is
`~/.config/armada/backup-keys` on the operator's Mac (mode 600): the
endpoint, the bucket, the server's key, a read-only drill key, the restic
password and the wal-g key. Keep a second copy in a password manager. A
restore needs only that file. Without the restic password or the wal-g
key the backups cannot be read by anyone.

Commands, as root on the server:

```bash
armada-backup restic snapshots                 # the file and database snapshots
armada-backup files                            # a file snapshot now
sudo -u postgres env-wal-g backup-list         # the database's base backups
sudo -u postgres psql -c 'select * from pg_stat_archiver'   # WAL archiving
```

## Restore drill

`armada-restore-drill KEYFILE` proves the backups restore with the offline
keys alone, and times each step. It fetches the latest base backup and
replays the archive into a throwaway PostgreSQL (port 5499, archiving
nothing), reads the row counts and the newest message, restores the latest
file snapshot and nightly dump, and removes everything. It uses the
read-only key, so it cannot touch the backups. Run it on a scratch droplet
built for the purpose, or beside the live server at idle priority:

```bash
ssh root@HOST 'umask 077; cat > /root/drill-keys' < ~/.config/armada/backup-keys
ssh root@HOST 'nice -n 19 ionice -c 3 armada-restore-drill /root/drill-keys; rm -f /root/drill-keys'
```

On the operator's Mac, `var/armada-overhaul/bin/armada-drill-droplet.sh`
does it on a scratch droplet in SGP1: creates it, installs PostgreSQL 17,
restic and the live server's wal-g, runs the drill, and destroys the
droplet whatever happened (about 3 minutes and a few cents).

On 15 October 2026, on a scratch droplet: every message (53 of 53) came
back, the replay reached a transaction written after the drill began,
the files and the nightly dump restored, and the drill took 32 seconds
(200 seconds with the droplet's creation and setup).

## Losing the droplet

1. Make a droplet (Ubuntu 24.04, `s-4vcpu-8gb`; SGP1 if BLR1 is down) and
   install the release the backups were taken from (`deploy.md`, 1 to 3),
   with `zulip.conf` and the secrets from the offline key file.
2. Stop the server (`scripts/stop-server`) and PostgreSQL, move
   `/var/lib/postgresql/17/main` aside, and restore the database as
   Zulip documents it (`docs/production/export-and-import.md`, "Restoring
   from wal-g backups"): `env-wal-g backup-fetch` into an empty data
   directory, then start PostgreSQL with a `recovery.signal` and let it
   replay to the end.
3. `armada-backup restic restore latest --tag files --target /`, then
   `chown -R zulip:zulip /srv/armada /home/zulip/uploads /home/zulip/.codex`.
4. Apply puppet (`scripts/zulip-puppet-apply`), which sets the RabbitMQ
   policy and the timers again, and start the server.
5. A person signs Codex in (`deploy.md`, 6.3).
6. Point the name at the new address, and check `/login/`, a message and
   an agent run.

Not yet timed end to end: the drill above restores the data, not a whole
server.

## Monitoring

Three layers, each reaching a person a different way:

- **From outside**: a DigitalOcean uptime check on `/login/` from three
  regions, with alerts for down (2 minutes), the certificate (under 21
  days) and latency (over 3 seconds for 10 minutes), mailed to the
  account's owner.
- **The machine**: DigitalOcean's metrics agent, with alerts for CPU
  (over 90% for 10 minutes), memory (over 90% for 5 minutes) and disk
  (over 80%), mailed the same way.
- **The application**: `armada-ops-check`, every minute, posts to the
  `#ops` channel when a check changes, and again when it recovers. A
  post that fails is tried again the next minute.

`armada-ops-check --print` prints every check now and posts nothing. Make
the bot and the channel once, after the organization exists:
`armada-ops-bot-setup OWNER_EMAIL`.

The sections below are the runbook for each check.

### processes

Every supervisor program must be `RUNNING`, and `/health` must answer.
`supervisorctl status` shows which one is not; its log is in
`/var/log/zulip/`. `supervisorctl restart <name>`, or
`su zulip -c '/home/zulip/deployments/current/scripts/restart-server'`
(this cuts agent runs: check the run list first).

### queues

A queue with messages and no consumer means its worker died; one with
more than 100 waiting is falling behind. `rabbitmqctl list_queues name
messages consumers`, then restart the worker as above.

### codex-login

Codex is signed out, so no agent run can start. A person signs in again
(`deploy.md`, 6.3), and runs wait in the queue meanwhile.

### certificate

Certbot renews on its own; under 21 days means renewal is failing.
`certbot renew --dry-run` says why.

### disk

`du -sh /srv/armada/worktrees/* /home/zulip/deployments/*` finds the
largest; `scripts/purge-old-deployments` removes old releases.

### database-backup

WAL archiving is failing, or there was no base backup in 26 hours.
`/var/log/postgresql/` and `/var/log/pg_backup_and_purge.log` say why; the
usual causes are the Spaces key and the network. Archiving that fails
keeps WAL on the disk, so this also fills it.

### file-backup

No restic snapshot in 2 hours, or no nightly in 26.
`journalctl -u armada-backup-files -u armada-backup-nightly` says why.

### patching

A security update has waited, or a reboot has been needed for more than a
week. Reboot in a quiet hour: every run is cut.
