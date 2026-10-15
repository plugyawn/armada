---
orphan: true
---

# Deploying Armada

How to go from an Ubuntu 24.04 machine to a working Armada that a team uses
every day: the old interface at `/`, the beta at `/beta/`, real sign-in, and
the coding agent running on the server. The host chosen for the first beta
is this Mac, in a Linux VM of its own, shared with the team over Tailscale
([Hosting on this Mac](#hosting-on-this-mac)); everything else applies to
any Ubuntu 24.04 machine.

Armada is a Zulip server, so it installs the way Zulip does (a release
tarball and `scripts/setup/install`), plus the things this file adds for the
agent. Zulip's own production documentation (`docs/production/`) is right for
everything that is not marked as Armada's. The agent is named Onix in the
interface; the server, its settings and its bot account still say Pear
until the rename.

**What was tested and what was not.** On 14 October 2026, on the Armada dev
VM (Ubuntu 24.04.5, 6 vCPU, 10 GB): the production asset build of the
working tree (`tools/update-prod-static`, the step the tarball build runs),
with the licensed-font directory empty and with Klim's test files in it;
Django serving the built pages with `DEBUG = False`; the Codex Linux sandbox
with the sysctl below; Codex's read-deny permission profile
([Security](#security)); the supervisor configuration rendered from the
template; and a real agent run on `gpt-6-sol` with the `ultrafast` tier
([The base model](#the-base-model)). **Not tested anywhere:** building the
tarball itself (no commit was chosen for it),
`scripts/setup/install`, puppet, supervisor, nginx, the certificate,
Tailscale, `scripts/upgrade-zulip` and the backup tool on an Armada server.
They are stock Zulip and should behave as Zulip documents them, but no
Armada server has been installed yet. Each step below says which kind it is.

## Before you start

You need:

- **The work committed** ([Commit the work](#0-commit-the-work)). The
  tarball is built from a commit, so the state the team gets has to be
  chosen and committed first.
- **An Ubuntu 24.04 machine**: for this Mac, a second Lima VM (below). For a
  team of up to ten: 4 vCPU, 6 to 8 GB RAM, 2 to 4 GB swap and 25 to 40 GB
  of disk ([Sizing](#sizing)).
- **A name the team can reach over HTTPS**: a Tailscale name
  (`armada-beta.<tailnet>.ts.net`) for this Mac, or a public DNS name for a
  server on the internet.
- **A way to sign the team up.** Without SMTP, Zulip cannot send the
  confirmation email an invitation needs, so invitations do not work. Either
  configure an SMTP relay, or create each account yourself with a management
  command ([step 5](#5-create-the-organization-and-the-accounts)).
- **A build machine with an Armada development environment** (the `armada`
  dev VM is one) with about 5 GB of memory free while it builds. The dev VM
  rarely has that while its dev server runs.
- **One person who signs the server's Codex CLI in** to their own ChatGPT
  account. Every agent run spends that account's Codex quota.
- **The licensed Söhne web-font files**, if you have them ([Fonts](#fonts)).
  Without them the interface is set in Hanken Grotesk and nothing breaks.
- **To have read [Security](#security)**, which sets what the defaults
  below protect and what they cost.

What needs your own accounts (nobody else can do these steps for you):
Tailscale (a second account whose tailnet holds only the VM, or admin of
your own: MagicDNS and HTTPS certificates on, the access policy, the
share or the invitations); ChatGPT (the Codex device login, 6.3);
GitHub or your Git host (a deploy key, only for private repositories, 6.5);
an SMTP relay, if you want invitations and password-reset email.

## Security

Read this before choosing settings. The template's defaults are the safe
side of each choice; this section says what each one protects and what it
costs.

### What an agent can do

With the default isolation, `PEAR_TOPIC_ISOLATION_BACKEND = "codex-sandbox"`,
every agent runs as the `zulip` user inside Codex's sandbox. With
`PEAR_WORKER_SANDBOX = "workspace-write"` the sandbox stops writes outside the
topic's worktree and stops network access. **It does not stop reads.** An
agent can read anything `zulip` can:

- `/etc/zulip/zulip-secrets.conf`: the Django secret key, the database,
  RabbitMQ, Redis and memcached passwords. The secret key is enough to forge
  any session on the server.
- `/home/zulip/.codex/auth.json`, the ChatGPT tokens of the person who signed
  Codex in, and `/home/zulip/.codex/sessions/`, every agent's transcript.
- `/home/zulip/.ssh/`, the server's Git key, if you made one (6.5).
- Every other topic's worktree, private channels' repositories included.

Tested in the dev VM: from inside `codex sandbox`, the server's secrets file
is readable. The agent has no network, so it cannot send what it read
anywhere itself, but it posts its answers into the topic: one request, or a
prompt hidden in a cloned repository's own files, is enough to put a secret
in front of everyone who can read that channel.

The server itself (as `zulip`, outside any sandbox) fetches every repository
URL it is given, `https://`, `http://`, `git://`, `ssh://` or `git@host:`,
internal addresses included, with its own Git key. Inside the Mac's VM,
"internal" includes the Mac itself (`host.lima.internal`) and every device
the VM can reach on the tailnet.

### The configuration this guide sets, and what each part costs

1. **Only trusted people drive the agent.**
   `PEAR_WORKER_TRUSTED_USERS_ONLY = True`, which the template sets: only
   organization administrators and the user ids in
   `PEAR_WORKER_TRUSTED_USER_IDS` may start a run, steer one, answer its
   questions, set up a monitor, or give a new channel a repository URL.
   _Cost:_ everyone else can chat but not use Onix; when they address it,
   Onix answers that they are not allowed. In the beta, a message goes to
   Onix only when its author names it, turns on the composer's Onix switch
   (off by default, remembered per topic in that browser) or answers a
   question a run is waiting on; so an untrusted member's ordinary message
   draws no refusal. Add every teammate you trust with the server's secrets
   to the list (step 4). Turning it off (`False` and an empty list) lets
   every member do all of the above: say so to the team if you choose it.
2. **Only trusted people make the server fetch a URL.** The same rule
   covers the repository URL of a new channel; members may still create
   ordinary channels (Zulip's own channel-creation permission is unchanged).
   _Cost:_ an administrator or a listed person sets up each repository
   channel.
3. **No Git key, unless you need private repositories.** Public HTTPS
   repositories need nothing. If you need private ones, use a read-only
   deploy key per repository (GitHub allows one repository per deploy key),
   or a machine user that can read exactly those repositories. Never a
   personal key. _Cost:_ a key per private repository.
4. **The VM sees nothing of the Mac.** Create it with `--plain`
   ([Hosting on this Mac](#hosting-on-this-mac)). Lima's default mounts your
   Mac home directory into the VM, which every agent could then read, and
   forwards every port the VM listens on, PostgreSQL's and RabbitMQ's
   included, to the Mac's `localhost`. _Cost:_ files go in and out with
   `limactl copy`, not through a shared folder.
5. **The team reaches only the VM, and the VM reaches nothing on the
   tailnet.** Share the VM with them from a tailnet of its own, or tag it
   and write the access policy given in
   [Hosting on this Mac](#hosting-on-this-mac); never invite them into a
   tailnet whose policy is still the default allow-all, which lets every
   device reach every other. _Cost:_ a second Tailscale account, or a
   policy file to keep; open specific ports later if the server must reach
   a tailnet service.
6. **`workspace-write`, never `danger-full-access`.** The agent has no network
   and writes only in its worktree. (`danger-full-access` is downgraded to
   `workspace-write` anyway unless the isolation is `os-user`.) _Cost:_ an
   agent cannot install packages or fetch anything; work that needs the
   network is done by a person.
7. **Codex memories off.** `PEAR_WORKER_CODEX_MEMORIES = False`, which the
   template sets. With the default isolation, every topic's agent runs
   with one `CODEX_HOME`, `/home/zulip/.codex`, so to Codex they are all
   one user. With its
   memories feature on, Codex condenses past sessions into
   `memories/memory_summary.md` and `memories/MEMORY.md` there, and its own
   instructions for that step say the summary "will be injected at the
   beginning of every new session for the same user". What an agent read
   in a private channel could then appear in another channel's run without
   anyone asking for it. Read from Codex 0.157.1 (its feature list, its
   built-in prompts and the dev VM's store, which held no consolidated
   memory yet); not reproduced. With the setting off, the server passes
   `features.memories=false` to every app-server, whatever a `config.toml`
   in `CODEX_HOME` says. _Cost:_ an agent remembers nothing between topics
   beyond what is in the topic, its worktree and the Codex thread it
   resumes. The dev environment keeps memories on.

What the rule does not cover:

- **Anything posted in a topic reaches the next run there.** Every run's
  prompt carries the topic's last 30 messages, each cut to 1,200
  characters, from every sender, named as such. So anyone who can post in
  a repository topic can write instructions that a trusted person's next
  run in that topic reads, and the agent may follow them with everything
  it can do (above: read the server's secrets and post them into the
  topic). The trust rule does not help: the run is the trusted person's.
  What limits it: only people who may post in that channel can do it, and
  only the last 30 messages count. To close it, make each repository
  channel one that only trusted people may post in: a private channel
  with only them subscribed, or Zulip's "who can post" channel setting
  limited to a group of them. Read a topic before you address Onix in it.
  (Conversations linked from a trusted person's own message are read with
  that person's access, so they add nothing the person could not read.)
- **Any member who can read a repository channel may create, edit, rename
  and delete files in its topics' worktrees** through the Files view. A
  file planted that way can carry instructions in the same way. The server
  opens every worktree path without following symbolic links, so a link an
  agent makes does not let the Files view read outside the worktree.
- **Any member who can see a run may cancel, pause or resume it**
  (stopping the agent is open to everyone; resuming starts nothing new).
- **Any member may change a topic's model and effort**
  (`POST /json/pear/topic-context` has no trust check, and the model may be
  any short name, not only those in `PEAR_WORKER_MODEL_CHOICES`). The next
  run in that topic then uses it. That changes cost, quota and speed, not
  what an agent can reach.

What remains: a trusted person (or a prompt in a repository they cloned,
or in a message or a file a member wrote, above) can get an agent to read
the server's secrets, the Codex login, transcripts and other topics'
worktrees into a channel the team can read. For a beta used by
one team on a private network, that is the accepted risk of this setup. If a
secret is ever posted, treat it as exposed: delete the message, change the
secret (a new `secret_key` in `/etc/zulip/zulip-secrets.conf` and a restart
signs everyone out; a service password must also be changed in the service
itself), and for the Codex login run `codex logout` as `zulip` and sign in
again (6.3).

### A further layer to try: Codex's read deny list

Codex 0.157.1 can deny reads of named paths inside its sandbox. Tested in the
dev VM with a permission profile: a file under a path marked `"none"` gave
"Permission denied" inside `codex sandbox`, writes in the working directory
worked, writes elsewhere and network were refused. What has **not** been
tried is the form that applies to the agent's own turns whatever the
server asks for: Codex's managed requirements file, `/etc/codex/requirements.toml`,
written as root:

```toml
[permissions.filesystem]
deny_read = [
  "/etc/zulip",
  "/home/zulip/.codex/auth.json",
  "/home/zulip/.codex/sessions",
  "/home/zulip/.ssh",
]
```

The key names come from Codex 0.157.1's own messages, not from its
documentation. After writing it, restart the server (6.7) and check both:

```bash
sudo -u zulip -H /opt/codex/bin/codex login status    # still "Logged in"
```

and, in a test topic, `@**Pear** run cat /etc/zulip/zulip-secrets.conf >
/dev/null && echo readable || echo denied, and reply with that one word`.
Only "denied" means the layer works. If the login stops working, remove the
`auth.json` line. Do not rely on this layer until that test passes on your
server.

### Per-topic Unix users (`os-user`): not for this beta

`PEAR_TOPIC_ISOLATION_BACKEND = "os-user"` runs each topic's agent as its own
Unix user, created by a root helper (`PEAR_TOPIC_ISOLATION_HELPER`) through
sudo, so topics cannot read each other's worktrees or the server's secrets.
In this tree:

- The backend is in the code (`zerver/lib/pear_worker.py`) and its unit tests
  pass, with sudo and the helper mocked.
- The helper is not a file in the tree. It exists only as text inside
  `tools/pear_deploy/bootstrap_remote.sh`, the proof of concept's hosted dev
  server, written for the user `zulipdev`; `tools/` is not in the release
  tarball. It is not installed in the dev VM, and has never run on a
  production layout.
- With it, agents run **without Codex's sandbox** (`danger-full-access` is
  forced), so with full network access; the helper copies the Codex login
  into every topic user's home, where that topic's agent can read it and
  now also send it anywhere; and the sudoers rule lets `zulip` run any
  command as any topic user.

So it trades the read exposure for a network one, and needs packaging work
first. It is the only backend under which monitors can run their recipe
commands. Not recommended until it is adapted to production with Codex's
sandbox kept on.

## 0. Commit the work

_Needs a decision by you._ Deploying needs the work committed. On 14
October at 19:21 a checkpoint of `ui-overhaul` was committed
(`89fb6f54e4`, "Checkpoint the interface rebuild before the rename"); the
work since then, this guide's own changes included, is not, and several
people are still editing the tree. Decide:

- **The branch** the server is built from (`ui-overhaul`, or a deploy branch
  cut from it).
- **The commit**: which state of the tree the team gets. Pick a moment when
  no package is half-done; the beta check saw an unfinished edit take
  `/beta/` and the sign-in page down for four minutes.
- **Whether `.claude/` ships.** It is not excluded from the tarball; it holds
  developer notes that name dev logins and this Mac's paths (no secrets).

`tools/build-release-tarball` refuses tracked changes but silently leaves out
untracked files, and the server does not start without some of them
(`zerver/views/armada_beta.py`, `tools/setup/build_armada_fonts`,
`web/src/armada/`, `web/fonts/armada/`). So commit with `-A`, and check that
nothing is left:

```bash
git add -A
git status --short          # read it: everything listed will ship
git commit
test -z "$(git status --porcelain)" && echo clean
```

## Hosting on this Mac

_Untested: not done here, since it needs your accounts and disk space._ The
beta runs in a second Lima VM, `armada-beta`, separate from the dev VM. The
dev VM cannot double as the server: PostgreSQL, RabbitMQ, Redis, nginx and
the `zulip` user would collide with the dev environment's.

**Disk first.** On 14 October the Mac had between 14 and 19 GB free over
the day (14 GB at 20:50, `df -h /System/Volumes/Data`), and the dev VM
already takes 18 GB. That is below the floor this section sets: **free
space before you start.** Zulip asks for 10 GB free on top of the system
(about 25 GB of disk in all); Armada adds repositories,
worktrees and the Codex CLI. Estimated, not measured: the beta VM uses about
12 to 15 GB once installed, and grows with every repository, topic worktree
and upgrade (each upgrade keeps the previous version, about 2 GB). Lima's
disk only takes Mac space as it fills, but macOS itself needs room. **Free at
least 25 GB on the Mac before starting; 40 GB to be comfortable.** Do not
start with less: a full Mac disk stops both VMs.

**Memory.** The Mac has 24 GB and the dev VM takes 10 GB. Give the beta VM
6 GB (Zulip needs more than 3.8 GB to run each queue, and both agent slots,
as a process of its own) and 2 GB of swap inside it; 8 GB if you stop the
dev VM while the beta serves.

**Create the VM** (Lima downloads the Ubuntu 24.04 image if it is not already
cached, about 600 MB):

```bash
limactl create --name=armada-beta --cpus=4 --memory=6 --disk=40 --plain template:ubuntu-24.04
limactl start armada-beta
limactl autostart enable armada-beta     # start it again after a reboot
```

`--plain` matters ([Security](#security), item 4): it turns off Lima's
mounts of the Mac's home directory, its forwarding of the VM's ports to the
Mac, its guest agent and the containerd the Ubuntu template otherwise
installs. (Checked against Lima 2.2.1's own help and template; the VM itself
was not created here.) Run every server command below inside it:
`limactl shell armada-beta`, then `sudo -i`. Copy files in with
`limactl copy FILE armada-beta:/tmp/`.

**Tailscale** (_untested here_; from Tailscale's documentation, "Sharing
your machines" and "Access control"). Do **not** invite the team into your
own tailnet as it is: a new tailnet's policy lets every device reach every
other, so a teammate would reach your Mac and every other device of yours,
and the VM (where every agent runs) would too. Pick one of two ways.

_Recommended: share the VM from a tailnet of its own._ A shared machine is
the only thing its recipients reach, and Tailscale quarantines it: it
accepts their connections but cannot start one into their tailnets.

1. Make a second Tailscale account for the beta (any identity provider
   Tailscale accepts), which gives it a tailnet of its own. In that
   tailnet's admin console, turn on MagicDNS and HTTPS certificates.
   Machine names then appear in public certificate-transparency logs;
   name the machine accordingly.
2. In that tailnet's access controls, replace the default rule with this
   one, so that people it is shared with reach HTTPS and nothing else (not
   the VM's SSH server, for one):

   ```json
   {
     "grants": [{ "src": ["autogroup:shared"], "dst": ["*"], "ip": ["443"] }]
   }
   ```

3. In the VM, install Tailscale from its documented package source and run
   `tailscale up --hostname=armada-beta`; open the printed URL, sign in
   with the second account and approve the machine. Leave it untagged:
   alone in its tailnet, a tag adds nothing.
4. In the admin console, Machines, the machine's menu, **Share**: share it
   by email with each teammate, and with your own everyday account. Each
   needs a Tailscale account of their own (free) and accepts the share
   there; Tailscale lets only an owner or admin of a tailnet accept one, so
   a teammate whose only tailnet is their employer's may need a personal
   one.

The VM is then alone in its tailnet, so it reaches nothing of anyone's.

_Or, in your own tailnet: tag the VM and replace the policy._ Every
teammate joins your tailnet as a member. Write the whole policy file as
below (in the admin console, Access controls; your addresses in place of
the examples). It replaces the allow-all rule for the whole tailnet: your
own devices keep reaching everything, the team reaches the VM's port 443
only, and nothing allows a connection from the VM, so it can start none.
If your tailnet already has rules of its own, merge these into them.

```json
{
  "tagOwners": { "tag:armada-beta": ["you@example.com"] },
  "groups": { "group:armada-team": ["alex@example.com", "sam@example.com"] },
  "acls": [
    { "action": "accept", "src": ["you@example.com"], "dst": ["*:*"] },
    { "action": "accept", "src": ["group:armada-team"], "dst": ["tag:armada-beta:443"] }
  ]
}
```

Then, in the VM, `tailscale up --hostname=armada-beta
--advertise-tags=tag:armada-beta` (or apply the tag to the machine in the
admin console, Machines, "Edit ACL tags"), and invite the team from Users.
A tagged machine belongs to the tag, not to you, so `you@example.com`'s
rule does not let it reach your devices.

Either way the server's name is `armada-beta.<tailnet>.ts.net`, `<tailnet>`
being the tailnet the VM is in: that is `EXTERNAL_HOST`, the `--hostname`
of the Zulip install (step 3 below), and the address the team opens. With sharing, check from one
teammate's machine that the name resolves and the page opens before
telling everyone (Tailscale names shared machines by their own tailnet;
not tried here).

**The certificate.** Install Zulip with a self-signed certificate (step 3),
then replace it with Tailscale's, which every browser and the Electron app
trust:

```bash
tailscale cert --cert-file /etc/ssl/certs/zulip.combined-chain.crt \
    --key-file /etc/ssl/private/zulip.key armada-beta.<tailnet>.ts.net
chmod 600 /etc/ssl/private/zulip.key
systemctl reload nginx
```

The certificate lasts 90 days; renew it with the same commands, for example
from a weekly root cron job. (`tailscale serve` in front of Zulip is the
other way: it needs Zulip's `http_only` and `[loadbalancer]` settings,
`docs/production/reverse-proxies.md`, and was not tried.)

**When the Mac sleeps, the beta is unreachable.** Lima's VM is suspended
with the Mac and resumes when it wakes: its processes continue where they
stopped, nothing restarts. Seen on the dev VM (same Lima 2.2.1, `vz`): it
has run since 13 October through the Mac's sleeps on 14 October, and each
wake left its clock behind by up to 33 minutes until Lima's guest agent
set it again. What a run that was going at the time does after the wake
is **not verified**: Codex's connection to OpenAI may have been dropped
by the far end and be retried or fail; RabbitMQ may count the sleep
against the consumer timeout of 6.6 and deliver the run again. Check such
runs after a wake.

`--plain` turns the guest agent off, so in the beta VM the clock is set
only by Ubuntu's own time service: check that `timedatectl` says "System
clock synchronized: yes" (`systemd-timesyncd` is on by default), and after
a wake give it a few minutes. If the team relies on the beta during the
day, set the Mac not to sleep on power (System Settings, or `pmset`), and
keep it on power and online.

## 1. Build a release tarball (on the build machine)

_Stock Zulip, untested here (it needs the commit of step 0)._ In the dev VM,
the commit arrives in `/srv/armada/zulip` through the sync loop (`.git` is
synced). The build needs about 5 GB of free memory for webpack (measured
peak 4.7 to 5.2 GB): stop the dev server first, agreeing a moment with
anyone using it, or build on another machine. From the Mac:

```bash
limactl shell armada -- /home/progyan.guest/armada-stop.sh     # stops the dev server
limactl shell armada
```

Then, in the dev VM:

```bash
cd /srv/armada/zulip
test -z "$(git status --porcelain)" && echo clean    # must print clean
# Only with the licensed web fonts; see Fonts. Never the test files.
export ARMADA_FONT_DIR=/srv/fonts/soehne
# The build works in this directory (about 3 GB); keep it inside the VM.
export OUTPUT_DIR=/srv/armada/release-build
rm -rf "$OUTPUT_DIR" && mkdir -p "$OUTPUT_DIR"
./tools/build-release-tarball armada-$(date +%Y%m%d)-$(git rev-parse --short HEAD)
# The checkout is mounted from the Mac: copying the tarball there brings it out.
mkdir -p /Users/progyan/runix/zulip/var/release
cp "$OUTPUT_DIR"/zulip-server-armada-*.tar.gz /Users/progyan/runix/zulip/var/release/
rm -rf "$OUTPUT_DIR"
exit
```

Without `OUTPUT_DIR`, the script builds in a new directory under the dev
VM's own `/tmp`, which the Mac cannot see. It copies the committed tree,
installs its Python and production Node dependencies, runs
`tools/update-prod-static`, packs the code with the built assets, and
prints the tarball's path. `var/` is ignored by git and by the sync loop,
so the tarball neither ships nor travels back into the VM's copy. Back on
the Mac, start the dev server again and copy the tarball into the beta VM:

```bash
limactl shell armada -- /home/progyan.guest/armada-restart.sh
limactl copy var/release/zulip-server-armada-*.tar.gz armada-beta:/tmp/
```

(Run from the checkout, `/Users/progyan/runix/zulip`; to a remote
server, `scp` instead.) Delete the copy in `var/release/` once the server
runs: a tarball built with the licensed fonts contains them.

- **The fonts in `ARMADA_FONT_DIR` are shipped to every browser** that opens
  the server, under hashed names. Use only files whose licence covers web
  use. The dev VM's run script points `ARMADA_FONT_DIR` at
  `web/fonts/licensed/`, which holds Klim's **test** files today; the build
  accepts them without a word, so never build a server with that directory.
- **The shipped source maps name the font directory's absolute path.** Use a
  neutral directory such as `/srv/fonts/soehne`, not a path under your home
  directory.
- A tarball built with the licensed files contains them: keep it private.

## 2. Prepare the server

_Stock Ubuntu, untested here._ As root:

```bash
apt-get update && apt-get -y dist-upgrade
# Reboot if that installed a new kernel, before step 6.1's sysctl.
[ -f /var/run/reboot-required ] && reboot
# Swap, if the machine has none (2 GB in the VM; 4 GB on a larger server):
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

On a server on the internet, set its hostname and open 22, 80 and 443; the
Mac's VM needs neither (Tailscale carries the traffic).

## 3. Install Zulip from the tarball

_Stock Zulip, untested here._ As root:

```bash
cd /tmp
tar -xf zulip-server-armada-*.tar.gz
./zulip-server-armada-*/scripts/setup/install --self-signed-cert \
    --email=YOU@example.com --hostname=armada-beta.<tailnet>.ts.net
```

On the Mac's VM, then put Tailscale's certificate in place
([Hosting on this Mac](#hosting-on-this-mac)). On a server with a public
name, use `--certbot` instead of `--self-signed-cert`.

This installs PostgreSQL, RabbitMQ, Redis, memcached, nginx and supervisor,
creates the `zulip` user, puts the code in `/home/zulip/deployments/current`,
writes `/etc/zulip/settings.py` from `zproject/prod_settings_template.py` and
the secrets in `/etc/zulip/zulip-secrets.conf`, and creates the database,
Armada's tables included. It ends by printing a link to create the
organization (step 5).

The queue workers Armada adds run under supervisor like Zulip's own, in the
group `zulip-workers` (`puppet/zulip/templates/supervisor/zulip.conf.template.erb`).
`supervisorctl status` names them:

| Program                                                                                      | What it does                                                                                                    |
| -------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `zulip-workers:zulip_events_pear_worker_slot0`, `…_slot1`                                    | Agent runs. Two slots, each consuming queues `pear_worker` and `pear_worker_1`, so two topics' runs go at once. |
| `zulip-workers:zulip_events_pear_monitor_worker`                                             | Monitors (standing checks in a topic), on their own consumer.                                                   |
| `zulip-workers:zulip_events_pear_addressing`, `zulip-workers:zulip_events_pear_presentation` | The optional Gemini router and live summaries; idle when those are off.                                         |
| `zulip-workers:zulip_events_embedded_bots`                                                   | Delivers messages to the agent's bot account.                                                                   |

These separate programs exist only on a machine with more than 3.8 GB of
memory; below that, Zulip runs every queue as threads of one program,
`zulip-workers:zulip_events`, and only one agent slot runs. Logs are
`/var/log/zulip/events_pear_*.log`.

Puppet sets `HOME=/home/zulip` and `CODEX_HOME=/home/zulip/.codex` for the
web server and every queue program, so the agent finds the Codex login of 6.3
even when supervisord was started from a root shell. The queue programs have
no `stopasgroup`; on a restart, a worker that does not exit within 30
seconds is killed alone, and its Codex processes are left to exit when their
input closes (not tried).

## 4. Settings

_Partly tested: the settings load in the dev environment; the file has not
been used on a server._ Edit `/etc/zulip/settings.py`. Set `EXTERNAL_HOST`
(the Tailscale name on the Mac), `ZULIP_ADMINISTRATOR` and, if you have a
relay, the email settings (step 5). Then, in the section "Armada:
repositories, worktrees and the coding agent", which explains each setting:

```python
EMBEDDED_BOTS_ENABLED = True              # already set in a new install
PEAR_WORKER_AGENT_RUNTIME = "codex-app-server"
PEAR_WORKER_CODEX_BIN = "/opt/codex/bin/codex"
PEAR_TOPIC_WORKDIR_ROOT = "/srv/armada/worktrees"
PEAR_WORKER_WORKDIR = "/srv/armada/worktrees/_scratch"
PEAR_WORKER_SANDBOX = "workspace-write"
PEAR_WORKER_TRUSTED_USERS_ONLY = True     # already set in a new install; see Security
PEAR_WORKER_CODEX_MEMORIES = False        # already set in a new install; see Security
PEAR_WORKER_TRUSTED_USER_IDS = []         # fill in after step 5
PEAR_WORKER_MAX_ACTIVE_RUNS_PER_REALM = 3
PEAR_WORKER_MODEL = "gpt-6-astra"
PEAR_WORKER_MODEL_CHOICES = ["gpt-6-astra", "gpt-6-sol", "gpt-5.6-sol"]
PEAR_WORKER_SERVICE_TIER = "ultrafast"
PEAR_WORKER_REASONING_EFFORT = "medium"
```

`workspace-write` lets an agent change files only inside its topic's
worktree, with no network; the approval policy stays at its default,
`on-request`. Leave `PEAR_ADDRESSING_ROUTER_RUNTIME` off unless you want a
Gemini model to guess which unmentioned messages are meant for the agent;
without it, mention the agent (`@**Pear**`) to start a run.

The user ids exist only after step 5. Come back then and print them, as
`zulip`:

```bash
/home/zulip/deployments/current/manage.py shell -c "
from zerver.models import UserProfile
for user in UserProfile.objects.filter(is_bot=False, is_active=True):
    print(user.id, user.delivery_email, user.full_name)
"
```

Put the people who may drive Onix in `PEAR_WORKER_TRUSTED_USER_IDS` and
restart (6.7). Organization administrators may always.

Do not apply the settings yet; step 6 restarts the server once everything
is in place.

### The base model

Decided on 14 October: **`gpt-6-astra` on the `ultrafast` tier, effort
`medium`**, with `gpt-6-sol` and `gpt-5.6-sol` also in the model picker (the
settings above). The picker offers every model in
`PEAR_WORKER_MODEL_CHOICES`, and always `PEAR_WORKER_MODEL`; a topic keeps
the model someone picked for it.

The tier is sent with every turn, whichever model the topic uses. Only
`gpt-6-astra` offers `ultrafast` (Codex catalog of 14 October). Tried on the
dev VM with a real run: on `gpt-6-sol`, Codex 0.157.1 reports "Configured
service tier `ultrafast` is not advertised as supported for model
`gpt-6-sol` and will be omitted from requests", and the run completes at
standard speed. So the other two models work, without a speed tier; that
warning shows in their runs' transcripts. Read the catalog on the server
with `sudo -u zulip -H /opt/codex/bin/codex debug models`.

Quota: the runs spend the signed-in account's Codex allowance. On 14
October the dev VM's account reported its weekly window 100% used (resetting
20 October), with runs drawing on its credit balance.

## 5. Create the organization and the accounts

_Stock Zulip, untested here._ Real sign-in is Zulip's: email and password by
default, with Google, GitHub, SAML and others available
(`docs/production/authentication-methods.md`). The development login page
of the dev VM does not exist on a production server.

```bash
su zulip -c '/home/zulip/deployments/current/manage.py generate_realm_creation_link'
```

Open the link, create the organization and your owner account. That works
without email.

**Everyone else needs either SMTP or you.** An invitation link, even one you
send yourself ("Generate invite link"), asks the newcomer for their address
and sends a confirmation email to it; without SMTP the server's mail goes
nowhere and they never get in.

- **Without SMTP: create each account yourself.** As `zulip`:

  ```bash
  cd /home/zulip/deployments/current
  ./manage.py list_realms                                  # the realm's id
  ./manage.py create_user -r REALM_ID person@example.com "Their Name"
  ./manage.py change_password -r REALM_ID person@example.com   # prompts twice
  ```

  `create_user` without a password creates the account with sign-in
  disabled; `change_password` asks for a password at the prompt (nothing on
  the command line or in shell history). Give the person the password over a
  private channel and ask them to change it (Personal settings, "Account &
  privacy"). Password reset by email will not work for them either.

- **With an SMTP relay** (any provider that gives SMTP credentials): in
  `/etc/zulip/settings.py`, `EMAIL_HOST`, `EMAIL_HOST_USER`,
  `EMAIL_PORT = 587`, `EMAIL_USE_TLS = True`, and a `NOREPLY_EMAIL_ADDRESS`
  in a domain the relay may send for; in `/etc/zulip/zulip-secrets.conf`,
  `email_password`. Test with `./manage.py send_test_email you@example.com`
  (`docs/production/email.md`). Then invitations, password resets and
  notifications work.
- **Or single sign-on** (GitHub, Google), which needs no email from the
  server but an OAuth app in that provider (your account).

## 6. The agent runtime

_Tested in the dev VM (Ubuntu 24.04.5) as noted; untested on a production
server._ Everything in this section is what a normal Zulip install does not
have.

### 6.1 The sandbox

Codex runs every command an agent issues inside Bubblewrap. Ubuntu 24.04
blocks the unprivileged user namespaces Bubblewrap needs, so allow them:

```bash
apt-get install -y bubblewrap git
cat > /etc/sysctl.d/90-armada-codex-userns.conf <<'EOF'
kernel.unprivileged_userns_clone = 1
kernel.apparmor_restrict_unprivileged_userns = 0
EOF
sysctl --system
```

This turns off a hardening Ubuntu applies to every program on the machine;
the dev VM and the earlier hosted Pear server run with it. Tested here: with
it, `codex sandbox` runs, and a write outside the allowed directories fails
with "Read-only file system". The narrower way Ubuntu documents is an
AppArmor profile that allows user namespaces for `/usr/bin/bwrap` alone.
Codex looks for a `bwrap` on the `PATH` before its bundled copy (from Codex
0.157.1's own messages), so such a profile should cover it; not tried.

### 6.2 The Codex CLI

Install it in its own prefix: Zulip replaces `/srv/zulip-node` when it
upgrades Node, which would delete anything installed there.

```bash
npm install -g --prefix /opt/codex @openai/codex@0.157.1   # the version the dev VM runs
/opt/codex/bin/codex --version
```

`npm` and `node` are the ones Zulip installed (`/usr/local/bin`). The
launcher runs `node` from `PATH`, which supervisor's environment has.

### 6.3 Signing Codex in

The agent runs as the `zulip` user, so Codex must be signed in as `zulip`.
A person does this, once, with their own account, by the device-code flow.
The ChatGPT account (or, for a business workspace, its admin) must allow
device-code login.

```bash
sudo -u zulip -H /opt/codex/bin/codex login --device-auth
```

It prints a URL and a code; open the URL in your own browser, sign in to
ChatGPT and enter the code. Check it:

```bash
sudo -u zulip -H /opt/codex/bin/codex login status    # "Logged in using ChatGPT"
```

Never copy another machine's `~/.codex/auth.json` to the server, and never
put a key in a settings file. The login lives in
`/home/zulip/.codex/auth.json` (mode 600); everyone who can become `zulip`
or root can use it, and so can any agent (see [Security](#security)). To
hand the server to another account, run `codex logout` as `zulip` and sign
in again.

### 6.4 The worktree directory

Every channel that is a repository gets a bare clone under
`PEAR_TOPIC_WORKDIR_ROOT/projects/`, and every topic that has a worktree gets
a full checkout beside it. The web server and the workers both write there.

```bash
install -d -o zulip -g zulip -m 0750 /srv/armada /srv/armada/worktrees /srv/armada/worktrees/_scratch
```

Put it on the disk with the most room. It is not inside
`/home/zulip/deployments`, so upgrades leave it alone, and Zulip's backup
does not include it (see [Back up](#back-up)).

### 6.5 Git access to the repositories

When an administrator or a trusted person creates a channel with a
repository URL, the server (as `zulip`) fetches it into the bare clone.
Agents work in the clone's worktrees; with `workspace-write`, Codex gives
them no network, so they cannot fetch or push themselves, and the server
does not push either. Getting work out of a worktree is, for now, a person
pushing from the server or reading the Changes view.

- Public repositories over HTTPS need nothing.
- Private repositories: give the `zulip` user a key and add it to the
  repository as a read-only deploy key, then use the `git@…` URL. Check the
  host key against the one your Git host publishes (GitHub's are listed in
  its documentation, "GitHub's SSH key fingerprints") before trusting it:

  ```bash
  install -d -o zulip -g zulip -m 0700 /home/zulip/.ssh
  sudo -u zulip -H ssh-keygen -t ed25519 -N '' -f /home/zulip/.ssh/id_ed25519
  ssh-keyscan github.com > /tmp/github.keys
  ssh-keygen -lf /tmp/github.keys     # compare every line with the published fingerprints
  install -o zulip -g zulip -m 0644 /tmp/github.keys /home/zulip/.ssh/known_hosts
  cat /home/zulip/.ssh/id_ed25519.pub     # add this as a deploy key
  sudo -u zulip -H git ls-remote git@github.com:OWNER/REPO.git   # must list refs
  ```

**Large repositories: attach them from the shell.** The first fetch runs
inside the web request that creates the channel, and in production uwsgi
kills a request after 55 seconds (`harakiri` in
`puppet/zulip/templates/uwsgi.ini.template.erb`; nginx gives up at 60;
the dev server has neither limit). Git itself is allowed 180 seconds. A
public repository of a few hundred megabytes, Armada's own among them
(about 645 MB of history, every branch fetched), will likely take longer
than 55 seconds. What happens then, read from the code
(`add_subscriptions_backend` in `zerver/views/streams.py` runs in one
database transaction, and calls the fetch inside it), not reproduced:

- uwsgi kills the worker outright (SIGKILL; the template sets no graceful
  timeout), so no Python cleanup runs, and the browser gets an error
  (502).
- PostgreSQL rolls the whole request back: **the channel does not exist**,
  and neither does its repository record. Creating it again with the same
  URL fails the same way.
- The fetch itself is a separate `git` process: it keeps running until it
  ends, into a temporary directory named after the channel,
  `.<name>.git.<random>`, in
  `/srv/armada/worktrees/projects/`, which nothing removes. Each attempt
  leaves one.

So for a repository that may take more than about 40 seconds to fetch,
attach it from the shell, where the web server's limit does not apply and
you choose git's:

1. Create the channel **without** a repository URL, as a **private**
   channel with only you subscribed, so that nobody opens a topic in it
   first. (Any use of the agent in a channel with no repository, choosing a
   model included, gives it an empty repository of its own, after which
   attaching one is refused: "This channel already has local work.")
2. As `zulip`, attach the repository, here with an hour for the fetch:

   ```bash
   cd /home/zulip/deployments/current
   ./manage.py shell -c "
   from zerver.lib.pear_worker import configure_pear_channel_repository_workspace
   from zerver.models import UserProfile
   user = UserProfile.objects.get(delivery_email='YOU@example.com', realm_id=REALM_ID)
   configure_pear_channel_repository_workspace(
       user, stream='CHANNEL', repository_url='URL', fetch_timeout_seconds=3600
   )
   "
   ```

   This is what channel creation does with a URL (as `YOU`, who must be
   subscribed to the channel), with a timeout of your choosing: it records
   the URL, fetches every branch into the channel's bare clone and makes
   the channel's own worktree. It prints nothing when it succeeds. A
   traceback ending in an error such as "Could not clone the Git
   repository." means the fetch failed or ran out of time: check the URL
   with `git ls-remote` as `zulip`, then run it again.

3. Subscribe the team, or make the channel public, in the channel's
   settings.

Clean up after a killed request: once no `git fetch` from it is still
running (`pgrep -u zulip -a git`), remove what it left:

```bash
find /srv/armada/worktrees/projects -maxdepth 1 -type d -name '.*.git.*' -exec rm -rf {} +
```

(The `.….git.lock` files beside them are locks, not directories; the
command leaves them.) The same 55 seconds also bound creating a topic's
worktree, a local clone of the bare one: only a very large checkout comes
near it.

### 6.6 Long runs and RabbitMQ

A run keeps its queue message unacknowledged until it ends, so a dead
worker's run is retried. RabbitMQ cancels a consumer that holds a message
longer than its consumer timeout, 30 minutes by default; the message is then
delivered again and the run starts over (each run is tried twice at most,
`PEAR_WORKER_MAX_CLAIM_ATTEMPTS`). Pear itself sets no limit on a run's
length: `PEAR_WORKER_TIMEOUT_SECONDS` (5.5 hours) is an **idle** timeout, a
turn that produces nothing for that long. So the consumer timeout is the
longest a run may take. Raise it to six hours for the agent queues and the
monitor queue (monitor setup runs are agent runs too):

```bash
rabbitmqctl set_policy -p / --priority 10 --apply-to queues pear-consumer-timeout \
    '^(pear_worker(_1)?|pear_monitor_worker)$' '{"consumer-timeout":21600000}'
rabbitmqctl list_queues name effective_policy_definition | grep pear
```

The second command must show `consumer-timeout` on all three queues (the
queues appear once the workers have started, 6.7). If it does not (the
policy key is not tried on Ubuntu's RabbitMQ 3.12.1), the fallback is the
server-wide setting: add `{consumer_timeout, 21600000}` to the `rabbit`
section of `puppet/zulip/files/rabbitmq/rabbitmq.config` in the tree and
build a new tarball. Do not edit `/etc/rabbitmq/rabbitmq.config` on the
server: puppet rewrites it on every upgrade. A run that keeps working
for more than six hours is cut off and tried again once. The policy is
stored by RabbitMQ and survives restarts and upgrades.

### 6.7 Turn it on

```bash
su zulip -c '/home/zulip/deployments/current/scripts/restart-server'
supervisorctl status | grep pear
```

Expect `zulip-workers:zulip_events_pear_worker_slot0` and `…_slot1`,
`zulip-workers:zulip_events_pear_monitor_worker`, and the two Gemini
programs, all `RUNNING`. Then create the agent's bot account, once, as an
organization administrator. Your API key is in the old interface under
Personal settings, "Account & privacy", "Manage your API key":

```bash
curl -sS -X POST -u YOUR_EMAIL https://armada-beta.<tailnet>.ts.net/api/v1/pear/config
# curl asks for the password: paste the API key there
```

The answer's `pear_config` should say `"enabled": true`,
`"worker_runtime_ready": true` and `"worker_runtime_auth_ready": true`. The
bot is called Pear; the interface shows it as Onix.

### 6.8 Check it

- Make a test channel that is a repository (in the old interface, create a
  channel and fill in its repository URL), open a topic in it and post
  `@**Pear** say hello in one line`. A run should start within seconds and
  answer in the topic; its log is in
  `/var/log/zulip/events_pear_worker_slot*.log`.
- As a member who is not trusted, the same message gets "This user is not
  allowed to start Pear worker runs."
- Signed in, `/beta/` loads; signed out, it sends you to `/login/`. The old
  interface's top bar has "Try the beta", and the beta has "Old interface".

## Fonts

Söhne is licensed, so it is never in git. Put the four `.woff2` files named
in `web/fonts/licensed/README.md`, with a licence that covers web use, in a
neutral directory on the build machine (`/srv/fonts/soehne`) and point
`ARMADA_FONT_DIR` at it when building the tarball (step 1). Tested here:

- With the directory empty, the production build succeeds, writes no
  `@font-face` for Söhne, preloads only the open faces, and the interface
  falls back to Hanken Grotesk.
- With Klim's test files in it, the build bundles the four faces under
  hashed names and the pages preload them. That is why the test files must
  never be in the directory a server is built with.
- `web/fonts/licensed/` itself is never in the tarball: git ignores its
  contents and the tarball leaves out `web/` entirely. Only the hashed copies
  in the built static files are, which is why such a tarball stays private.

A server does not need the font files; to change fonts, rebuild the tarball
and upgrade. The tarball is always built in a fresh copy. A build run in
place (`tools/update-prod-static` in a checkout) does not empty
`static/webpack-bundles/`, so font files from an earlier build stay there and
are collected into `prod-static/` even when the new build no longer uses
them (seen here: a build without fonts after one with them).

## The two interfaces

- `/` is the old interface: everything Zulip and Armada do today, the
  settings and the administration included.
- `/beta/` is the new interface (interim, while the rebuild lands). It uses
  the same session: sign in at `/`, then open `/beta/`. Signed out, it sends
  you to the login page. The old interface's top bar has "Try the beta" and
  the beta has "Old interface".

Electron is the main target for the finished app; until Armada's own shell
exists, the Zulip desktop app can be pointed at the server.

## Update

_Stock Zulip, untested here._ Commit, build a new tarball (steps 0 and 1),
copy it over, and as root:

```bash
/home/zulip/deployments/current/scripts/upgrade-zulip /tmp/zulip-server-armada-NEW.tar.gz
```

It unpacks next to the running version, runs the database migrations
(Armada's included), rewrites the system configuration from puppet
(supervisor's included, so local edits to `/etc/supervisor` are lost; the
Codex environment of step 3 lives in puppet and stays), switches `current`
and restarts. Agent runs in progress are cut off by the restart; their
messages go back on the queue and each is tried again once, so expect a run
that was going to start over. Upgrade when nobody is mid-run. The previous
version stays in `/home/zulip/deployments/` for a rollback
(`docs/production/upgrade.md`); each takes about 2 GB, so on the Mac's small
disk remove old ones with `scripts/purge-old-deployments`.

After an upgrade, compare your settings with the new template
(`/home/zulip/deployments/current/zproject/prod_settings_template.py`) for
new Armada settings. Upgrade the Codex CLI separately, when you choose, with
the `npm install` line of 6.2 and a new version number.

## Back up

_Stock Zulip, untested here._ Zulip's backup holds the database, uploaded
files and `/etc/zulip` (settings and secrets). It runs as `zulip`, so write
it to a directory `zulip` owns:

```bash
install -d -o zulip -g zulip -m 0700 /home/zulip/backups
su zulip -c '/home/zulip/deployments/current/manage.py backup --output=/home/zulip/backups/armada-$(date +%F).tar.gz'
```

Armada keeps two more things outside it (as root):

- **The repositories and worktrees** (`PEAR_TOPIC_WORKDIR_ROOT`): every
  agent's uncommitted work and every topic branch that was not pushed.
  Back it up with the database, while no run is going:
  `tar -C /srv/armada -czf /home/zulip/backups/armada-worktrees-$(date +%F).tar.gz worktrees`.
- **The agent's Codex state** (`/home/zulip/.codex`, without `auth.json`):
  the conversation history Codex resumes a topic from.
  `tar -C /home/zulip --exclude=.codex/auth.json -czf /home/zulip/backups/armada-codex-$(date +%F).tar.gz .codex`.
  Leave the login out of backups; after a restore, sign in again (6.3). The
  session transcripts hold whatever agents read, secrets included
  ([Security](#security)): keep this archive as carefully as the secrets.

Store backups off the machine (for the Mac's VM: `limactl copy` them out to
an encrypted disk); they hold the secrets in `/etc/zulip`. To restore on a
new server: install from the same tarball (step 3, with the same PostgreSQL
version), then, as root,
`/home/zulip/deployments/current/scripts/setup/restore-backup BACKUP.tar.gz`
(`docs/production/export-and-import.md`); untar the two archives back in
place and give them to `zulip` (user ids differ between machines):

```bash
tar -C /srv/armada -xzf armada-worktrees-DATE.tar.gz && chown -R zulip:zulip /srv/armada
tar -C /home/zulip -xzf armada-codex-DATE.tar.gz && chown -R zulip:zulip /home/zulip/.codex
```

Then do section 6 again (6.4's directory already exists, and 6.6's policy
must be set again on the new RabbitMQ).

## Sizing

For up to about ten people with a few agent runs at once. These are
estimates, not load tests; only the figures marked measured were measured.

- **CPU: 4 vCPU.** Zulip, plus the commands agents run in their worktrees
  (tests, builds), which is where the load comes from.
- **Memory: 6 to 8 GB, plus 2 to 4 GB swap.** Each agent slot keeps up to
  four Codex app-servers warm for 30 minutes after their topic's last run
  (about 170 MB each when idle, measured in the dev VM), two slots and the
  monitor worker can each hold four, and a running agent's commands can
  need gigabytes. More than 3.8 GB is also what makes Zulip run each queue,
  and both agent slots, as a process of its own.
- **Disk: 25 to 40 GB.** Zulip asks for 10 GB free beyond the system; each
  repository is stored once, as a bare clone, plus one full checkout per
  topic with a worktree, plus whatever agents build in them, plus about
  2 GB per kept upgrade.

Two runs go at once, one per slot; the rest wait in the queue.
`PEAR_WORKER_MAX_ACTIVE_RUNS_PER_REALM` is how many runs may be queued or
running at once in the organization; beyond it a new request is refused.

## Troubleshooting

- `worker_runtime_auth_ready` is false: Codex is not signed in as `zulip`
  (6.3), or `PEAR_WORKER_CODEX_BIN` is not the path you installed.
- A run fails at once with a sandbox error: the sysctl of 6.1 is not applied
  (`sysctl kernel.apparmor_restrict_unprivileged_userns` must print 0).
- "This user is not allowed to start Pear worker runs" (or "to steer"): the
  person is not an administrator and not in `PEAR_WORKER_TRUSTED_USER_IDS`
  (step 4).
- "Could not clone the Git repository": as `zulip`, `git ls-remote` the URL
  (6.5).
- Creating a repository channel answers with an error after about a minute,
  and the channel does not exist: the repository took longer than 55
  seconds to fetch, and the whole request was rolled back. Attach it from
  the shell (6.5, "Large repositories").
- A run starts over after 30 minutes or six hours: the consumer timeout of
  6.6 (check the policy is in effect).
- Runs stay queued: `supervisorctl status | grep pear_worker`; both slots
  must be `RUNNING`.
- Nobody can reach the server: the Mac is asleep or the VM is stopped
  (`limactl list`), or the Tailscale rule does not let them in.
