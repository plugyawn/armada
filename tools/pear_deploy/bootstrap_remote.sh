#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PEAR_ROOT="$(cd "$REPO_ROOT/.." && pwd)"
# shellcheck source=tools/pear_deploy/deploy_common.sh
source "$SCRIPT_DIR/deploy_common.sh"
STATE_PATH="${1:-$REPO_ROOT/output/pear-staging/server.json}"
LOCAL_PEAR_SKILLS_DIR="$PEAR_ROOT/pear/skills"
REMOTE_PEAR_SKILLS_DIR="/home/zulipdev/pear/skills"
LOCAL_TERMINAL_ROOT="$PEAR_ROOT/zulip-terminal"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

validate_local_deploy_git_metadata() {
    local dotgit="$REPO_ROOT/.git"
    local gitfile
    local gitdir
    local local_git_dir
    local local_git_common_dir

    if [[ ! -f "$dotgit" ]]; then
        fail "local checkout must be the canonical Zulip submodule with a relative .git gitfile; refusing to deploy from a checkout with a .git directory or missing .git file"
    fi
    IFS= read -r gitfile <"$dotgit" || gitfile=""
    if [[ "$gitfile" != gitdir:\ * ]]; then
        fail "local .git file is not a gitdir file; refusing to deploy"
    fi
    gitdir="${gitfile#gitdir: }"
    if [[ -z "$gitdir" || "$gitdir" == /* ]]; then
        fail "local .git points at an absolute gitdir ($gitdir); deploy from the main /Users/progyan/codack/zulip checkout, not a git worktree"
    fi

    local_git_dir="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-dir)"
    local_git_common_dir="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir)"
    if [[ "$local_git_dir" != "$local_git_common_dir" ]]; then
        fail "local checkout is a linked Git worktree ($local_git_dir); deploy from the main checkout so the remote .git gitfile resolves to /home/zulipdev/.git/modules/zulip"
    fi
}

if [[ ! -f "$STATE_PATH" ]]; then
    echo "Missing state file: $STATE_PATH" >&2
    echo "Run: python3 tools/pear_deploy/pear_do.py create" >&2
    exit 1
fi

eval "$(
    python3 - "$STATE_PATH" <<'PY'
import json
import shlex
import sys

state = json.load(open(sys.argv[1]))
values = {
    "FQDN": state["fqdn"],
    "IP_ADDRESS": state["ip_address"],
    "SSH_CONFIG": state["ssh_config"],
}
for key, value in values.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
)"

LOCAL_CODEX_VERSION="$(
    if command -v codex >/dev/null 2>&1; then
        codex --version | awk '{print $2}'
    fi
)"
if [[ -z "$LOCAL_CODEX_VERSION" ]]; then
    LOCAL_CODEX_VERSION="0.139.0"
fi

for env_file in "$REPO_ROOT/../env.local" "$REPO_ROOT/.env.local"; do
    if [[ -f "$env_file" ]]; then
        set -a
        # shellcheck source=/dev/null
        source "$env_file"
        set +a
    fi
done
PEAR_GEMINI_API_KEY="${PEAR_GEMINI_INTERACTIONS_API_KEY:-${GEMINI_API_KEY:-${GEMINI_KEY:-${GOOGLE_API_KEY:-${GOOGLE_GENAI_API_KEY:-}}}}}"
ssh_root() {
    ssh -F "$SSH_CONFIG" pear-staging-root "$@"
}

validate_local_deploy_git_metadata
if [[ ! -d "$LOCAL_PEAR_SKILLS_DIR" ]]; then
    fail "local Pear skills directory is missing: $LOCAL_PEAR_SKILLS_DIR"
fi
LOCAL_GIT_COMMON_DIR="$(git -C "$REPO_ROOT" rev-parse --git-common-dir)"
LOCAL_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
LOCAL_PARENT_SHA="$(git -C "$PEAR_ROOT" rev-parse HEAD)"
LOCAL_TERMINAL_SHA="$(git -C "$LOCAL_TERMINAL_ROOT" rev-parse HEAD)"
LOCAL_PARENT_STATUS="$(git -C "$PEAR_ROOT" status --porcelain --untracked-files=no)"
LOCAL_STATUS="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)"
LOCAL_TERMINAL_STATUS="$(git -C "$LOCAL_TERMINAL_ROOT" status --porcelain --untracked-files=all)"
LOCAL_UNTRACKED_SOURCE="$(
    git -C "$REPO_ROOT" ls-files --others --exclude-standard |
        filter_deploy_untracked_source_files
)"
LOCAL_PEAR_SKILLS_STATUS="$(git -C "$PEAR_ROOT" status --porcelain --untracked-files=all -- pear/skills)"
if [[ "$(git -C "$PEAR_ROOT" branch --show-current)" != "pear-main" ]]; then
    fail "parent checkout must be on pear-main"
fi
if [[ "$(git -C "$REPO_ROOT" branch --show-current)" != "pear-main" ]]; then
    fail "Zulip checkout must be on pear-main"
fi
if [[ "$(git -C "$LOCAL_TERMINAL_ROOT" branch --show-current)" != "pear-main" ]]; then
    fail "Zulip Terminal checkout must be on pear-main"
fi
if [[ "$(git -C "$PEAR_ROOT" rev-parse HEAD:zulip)" != "$LOCAL_SHA" ]]; then
    fail "parent does not pin the canonical Zulip checkout"
fi
if [[ "$(git -C "$PEAR_ROOT" rev-parse HEAD:zulip-terminal)" != "$LOCAL_TERMINAL_SHA" ]]; then
    fail "parent does not pin the canonical Zulip Terminal checkout"
fi
if [[
    -n "$LOCAL_PARENT_STATUS" ||
    -n "$LOCAL_STATUS" ||
    -n "$LOCAL_TERMINAL_STATUS" ||
    -n "$LOCAL_UNTRACKED_SOURCE"
]]; then
    fail "bootstrap requires a clean coordinated source tree"
fi
if [[ -n "$LOCAL_PEAR_SKILLS_STATUS" ]]; then
    fail "bootstrap requires a clean pear/skills tree"
fi
LOCAL_WORKSPACE_HELPER_SHA="$(
    git -C "$REPO_ROOT" cat-file blob "$LOCAL_SHA:$PEAR_WORKSPACE_HELPER_SOURCE_RELATIVE" |
        shasum -a 256 | awk '{print $1}'
)"
LOCAL_PEAR_SKILLS_TREE="$(git -C "$PEAR_ROOT" rev-parse HEAD:pear/skills)"
LOCAL_CONTRACT_VERSION="$(
    python3 - "$PEAR_ROOT/pear/contract.json" <<'PY'
import json
import sys

with open(sys.argv[1]) as contract_file:
    print(json.load(contract_file)["server_contract_version"])
PY
)"
REMOTE_TRUSTED_RUNTIME=""

cleanup_trusted_runtime() {
    if [[ -n "$REMOTE_TRUSTED_RUNTIME" ]]; then
        remove_remote_pear_deploy_bundle "$REMOTE_TRUSTED_RUNTIME" >/dev/null 2>&1 || true
    fi
}
trap cleanup_trusted_runtime EXIT

echo "Waiting for SSH on $IP_ADDRESS..."
for _ in $(seq 1 90); do
    if ssh_root true >/dev/null 2>&1; then
        break
    fi
    sleep 5
done
ssh_root true

echo "Waiting for cloud-init..."
ssh_root 'cloud-init status --wait || true'

echo "Installing base packages and Caddy..."
ssh_root 'bash -s' <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

if ! id -u zulipdev >/dev/null 2>&1; then
    useradd -U -G sudo -m zulipdev -s /bin/bash
    echo "zulipdev ALL=(ALL) NOPASSWD:ALL" >/etc/sudoers.d/90-zulipdev
fi
chmod 711 /home/zulipdev
install -d -m 750 -o zulipdev -g zulipdev /home/zulipdev/zulip

apt-get update
apt-get install -y bubblewrap ca-certificates caddy curl git gnupg nodejs npm python3 python3-venv rsync ufw

ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
REMOTE

echo "Syncing checkout to /home/zulipdev/zulip..."
rsync -az --delete \
    "${PEAR_DEPLOY_RSYNC_EXCLUDES[@]}" \
    -e "ssh -F $SSH_CONFIG" \
    "$REPO_ROOT/" \
    "root@$IP_ADDRESS:/home/zulipdev/zulip/"

echo "Syncing Pear Codex skills to $REMOTE_PEAR_SKILLS_DIR..."
ssh_root "install -d -m 755 $REMOTE_PEAR_SKILLS_DIR"
rsync -az --delete \
    -e "ssh -F $SSH_CONFIG" \
    "$LOCAL_PEAR_SKILLS_DIR/" \
    "root@$IP_ADDRESS:$REMOTE_PEAR_SKILLS_DIR/"

if [[ -d "$LOCAL_GIT_COMMON_DIR" ]]; then
    echo "Syncing Git metadata for this worktree..."
    ssh_root 'install -d -m 755 /home/zulipdev/.git/modules/zulip'
    rsync -az --delete \
        -e "ssh -F $SSH_CONFIG" \
        "$LOCAL_GIT_COMMON_DIR/" \
        "root@$IP_ADDRESS:/home/zulipdev/.git/modules/zulip/"
fi

echo "Staging privileged deployment tooling from the pinned local commit..."
REMOTE_TRUSTED_RUNTIME="$(stage_remote_pear_deploy_bundle "$REPO_ROOT" "$LOCAL_SHA")"

echo "Installing and verifying the host Codex sandbox runtime..."
ssh_root bash \
    "$REMOTE_TRUSTED_RUNTIME/tools/pear_deploy/codex_sandbox_runtime.sh" \
    --install

echo "Installing and verifying the committed isolated Workspace helper..."
ssh_root 'install -d -m 755 -o root -g root /usr/local/lib'
ssh_root python3 "$REMOTE_TRUSTED_RUNTIME/tools/pear_deploy/workspace_helper_runtime.py" install \
    --repo-root "$REMOTE_TRUSTED_RUNTIME" \
    --destination "$PEAR_WORKSPACE_HELPER_PATH" \
    --expected-sha256 "$LOCAL_WORKSPACE_HELPER_SHA"
ssh_root python3 "$REMOTE_TRUSTED_RUNTIME/tools/pear_deploy/workspace_helper_runtime.py" verify \
    --repo-root "$REMOTE_TRUSTED_RUNTIME" \
    --destination "$PEAR_WORKSPACE_HELPER_PATH" \
    --expected-sha256 "$LOCAL_WORKSPACE_HELPER_SHA" \
    --smoke-existing-isolation-user

ssh_root 'chown -R zulipdev:zulipdev /home/zulipdev/zulip'
ssh_root 'chown -R zulipdev:zulipdev /home/zulipdev/pear'
ssh_root 'chmod 711 /home/zulipdev && chmod 750 /home/zulipdev/zulip'
ssh_root 'chown -R zulipdev:zulipdev /home/zulipdev/.git 2>/dev/null || true'
ssh_root 'sudo -Hu zulipdev git config --global --add safe.directory /home/zulipdev/zulip'
ssh_root 'sudo -Hu zulipdev bash -lc '"'"'
cd /home/zulipdev/zulip
if [[ -d .vagrant ]]; then
    echo "Removing copied host .vagrant marker before remote provisioning..."
    rm -rf .vagrant
fi
if [[ -f .venv/bin/activate ]] && grep -q "/Users/" .venv/bin/activate; then
    echo "Removing stale copied local virtualenv before remote provisioning..."
    rm -rf .venv
fi
'"'"''

echo "Provisioning Zulip on the server. This can take a while the first time..."
ssh_root 'sudo -Hu zulipdev bash -lc "cd /home/zulipdev/zulip && SKIP_VENV_SHELL_WARNING=1 ./tools/provision"'
ssh_root 'rm -f /etc/sudoers.d/90-zulipdev && gpasswd -d zulipdev sudo >/dev/null 2>&1 || true'

echo "Installing Codex CLI @openai/codex@$LOCAL_CODEX_VERSION..."
ssh_root "npm install -g @openai/codex@$LOCAL_CODEX_VERSION"

if [[ -f "$HOME/.codex/auth.json" ]]; then
    echo "Copying Codex auth to the server without printing credentials..."
    ssh_root 'install -d -m 700 -o zulipdev -g zulipdev /home/zulipdev/.codex'
    rsync -az -e "ssh -F $SSH_CONFIG" "$HOME/.codex/auth.json" \
        "root@$IP_ADDRESS:/home/zulipdev/.codex/auth.json"
    ssh_root 'chown zulipdev:zulipdev /home/zulipdev/.codex/auth.json && chmod 600 /home/zulipdev/.codex/auth.json'
else
    echo "WARNING: $HOME/.codex/auth.json is missing; run codex login on the server before using Pear." >&2
fi

echo "Writing Pear environment and queue-worker systemd config..."
ssh -F "$SSH_CONFIG" pear-staging-root bash -s <<'REMOTE'
set -euo pipefail

cat >/home/zulipdev/pear.env <<EOF
EXTERNAL_HOST=${FQDN}
BEHIND_HTTPS_PROXY=1
ZULIP_DATABASE_CONN_MAX_AGE=0
ZULIP_DEV_PRODUCTION_ASSETS=1
PEAR_WORKER_AGENT_RUNTIME=codex-app-server
PEAR_WORKER_CODEX_BIN=/srv/zulip-node/bin/codex
PEAR_WORKER_WORKDIR=/home/zulipdev/zulip
PEAR_CODEX_SKILLS_DIR=/home/zulipdev/pear/skills
PEAR_TOPIC_WORKDIR_ROOT=/srv/pear-workspaces
PEAR_TOPIC_ISOLATION_BACKEND=os-user
PEAR_TOPIC_ISOLATION_HELPER=/usr/local/sbin/pear-topic-isolate
PEAR_WORKER_MODEL=gpt-5.5
PEAR_WORKER_SERVICE_TIER=fast
PEAR_WORKER_FAST_MODE=true
PEAR_WORKER_REASONING_EFFORT=medium
PEAR_WORKER_SANDBOX=danger-full-access
PEAR_WORKER_APPROVAL_POLICY=never
# Restrict who can drive the agent.  With this unset, ANY realm user (and, via
# passwordless dev-login, any visitor) can start full-access runs.  Set it to a
# comma-separated list of trusted UserProfile ids to gate run starts to them
# plus realm admins, e.g. PEAR_WORKER_TRUSTED_USER_IDS=5,6
# PEAR_WORKER_TRUSTED_USER_IDS=
PEAR_WORKER_MAX_ACTIVE_RUNS_PER_TOPIC=1
PEAR_WORKER_MAX_ACTIVE_RUNS_PER_REALM=6
PEAR_ADDRESSING_ROUTER_RUNTIME=gemini-interactions
PEAR_ADDRESSING_ROUTER_GEMINI_MODEL=gemini-3.1-flash-lite
PEAR_ADDRESSING_ROUTER_GEMINI_THINKING_LEVEL=minimal
PEAR_PRESENTATION_GEMINI_MODEL=gemini-3.1-flash-lite
PEAR_PRESENTATION_GEMINI_THINKING_LEVEL=minimal
EOF
chown zulipdev:zulipdev /home/zulipdev/pear.env
chmod 600 /home/zulipdev/pear.env
groupadd --force pear-topic
install -d -m 711 -o zulipdev -g pear-topic /srv/pear-workspaces

cat >/usr/local/sbin/pear-topic-isolate <<'EOF'
#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def resolve(path: str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


parser = argparse.ArgumentParser()
parser.add_argument("--user", required=True)
parser.add_argument("--workdir", required=True)
parser.add_argument("--topic-root", required=True)
parser.add_argument("--codex-home-source", required=True)
args = parser.parse_args()

if os.geteuid() != 0:
    fail("pear-topic-isolate must run as root")
if re.fullmatch(r"pear-[0-9a-f]{24}", args.user) is None:
    fail("invalid Pear topic user")

topic_root = resolve(args.topic_root)
workdir = resolve(args.workdir)
if workdir != topic_root and topic_root not in workdir.parents:
    fail("workdir is outside topic root")

topic_root.mkdir(parents=True, exist_ok=True)
os.chmod(topic_root, 0o711)
Path(args.workdir).mkdir(parents=True, exist_ok=True)
strict_workdir = Path(args.workdir).resolve(strict=True)
if strict_workdir != topic_root and topic_root not in strict_workdir.parents:
    fail("resolved workdir is outside topic root")
if (strict_workdir / ".git").is_file():
    fail("refusing shared Git worktree; topic checkout must have a private .git directory")
if not (strict_workdir / ".git").exists():
    subprocess.run(
        ["git", "-C", str(strict_workdir), "init", "--quiet"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

subprocess.run(["groupadd", "--force", "pear-topic"], check=True)
try:
    subprocess.run(["id", "-u", args.user], check=True, stdout=subprocess.DEVNULL)
except subprocess.CalledProcessError:
    subprocess.run(
        [
            "useradd",
            "--system",
            "--create-home",
            "--gid",
            "pear-topic",
            "--home-dir",
            f"/home/{args.user}",
            "--shell",
            "/usr/sbin/nologin",
            args.user,
        ],
        check=True,
    )
else:
    subprocess.run(["usermod", "--gid", "pear-topic", args.user], check=True)

home = Path(f"/home/{args.user}")
home.mkdir(parents=True, exist_ok=True)
codex_source = Path(args.codex_home_source).expanduser()
codex_target = home / ".codex"
codex_target.mkdir(parents=True, exist_ok=True)
if codex_source.exists():
    for filename in ("auth.json", "config.toml", "models_cache.json"):
        source_file = codex_source / filename
        if source_file.exists():
            shutil.copy2(source_file, codex_target / filename)
    for dirname in ("plugins", "skills"):
        source_dir = codex_source / dirname
        target_dir = codex_target / dirname
        if source_dir.exists():
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(source_dir, target_dir, symlinks=False)

subprocess.run(["chown", "-R", f"{args.user}:pear-topic", str(home)], check=True)
os.chmod(home, 0o700)
os.chmod(codex_target, 0o700)
auth_json = codex_target / "auth.json"
if auth_json.exists():
    os.chmod(auth_json, 0o600)

subprocess.run(["chown", "-R", f"{args.user}:pear-topic", str(strict_workdir)], check=True)
for root, dirs, files in os.walk(strict_workdir):
    os.chmod(root, 0o700)
    for dirname in dirs:
        path = Path(root) / dirname
        if not path.is_symlink():
            os.chmod(path, 0o700)
    for filename in files:
        path = Path(root) / filename
        if not path.is_symlink():
            mode = path.stat().st_mode
            os.chmod(path, 0o700 if mode & 0o111 else 0o600)
EOF
chmod 755 /usr/local/sbin/pear-topic-isolate

cat >/etc/sudoers.d/91-pear-topic-isolation <<'EOF'
zulipdev ALL=(root) NOPASSWD: /usr/local/sbin/pear-topic-isolate
zulipdev ALL=(%pear-topic) NOPASSWD: /usr/bin/env *
EOF
chmod 440 /etc/sudoers.d/91-pear-topic-isolation

cat >/etc/systemd/system/pear-embedded-bots.service <<'EOF'
[Unit]
Description=Pear Zulip embedded bot queue
After=network-online.target pear-zulip-dev.service
Wants=pear-zulip-dev.service

[Service]
Type=simple
User=zulipdev
WorkingDirectory=/home/zulipdev/zulip
EnvironmentFile=/home/zulipdev/pear.env
ExecStart=/bin/bash -lc 'cd /home/zulipdev/zulip && exec /home/zulipdev/zulip/.venv/bin/python3 /home/zulipdev/zulip/manage.py process_queue --queue_name embedded_bots --automated'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/pear-thumbnail.service <<'EOF'
[Unit]
Description=Pear Zulip thumbnail queue
After=network-online.target pear-zulip-dev.service
Wants=pear-zulip-dev.service

[Service]
Type=simple
User=zulipdev
WorkingDirectory=/home/zulipdev/zulip
EnvironmentFile=/home/zulipdev/pear.env
ExecStart=/bin/bash -lc 'cd /home/zulipdev/zulip && exec /home/zulipdev/zulip/.venv/bin/python3 /home/zulipdev/zulip/manage.py process_queue --queue_name thumbnail --automated'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/pear-monitor-worker.service <<'EOF'
[Unit]
Description=Pear standing-monitor queue
After=network-online.target pear-zulip-dev.service
Wants=pear-zulip-dev.service

[Service]
Type=simple
User=zulipdev
WorkingDirectory=/home/zulipdev/zulip
EnvironmentFile=/home/zulipdev/pear.env
ExecStart=/bin/bash -lc 'cd /home/zulipdev/zulip && exec /home/zulipdev/zulip/.venv/bin/python3 /home/zulipdev/zulip/manage.py process_queue --queue_name pear_monitor_worker --automated'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

REMOTE

echo "Building and validating production assets..."
ssh_root 'rm -rf \
    /home/zulipdev/zulip/prod-static \
    /home/zulipdev/zulip/static/generated \
    /home/zulipdev/zulip/static/webpack-bundles \
    /home/zulipdev/zulip/static/images/landing-page/hello/generated \
    /home/zulipdev/zulip/web/generated; \
    rm -f \
    /home/zulipdev/zulip/webpack-stats-production.json \
    /home/zulipdev/zulip/staticfiles.json \
    /home/zulipdev/zulip/locale/language_options.json \
    /home/zulipdev/zulip/locale/language_name_map.json; \
    find /home/zulipdev/zulip/locale -type f -name '"'"'*.mo'"'"' -delete'
ssh_root 'sudo -Hu zulipdev bash -lc "cd /home/zulipdev/zulip && ZULIP_DEV_PRODUCTION_ASSETS=1 ./tools/update-prod-static && .venv/bin/python3 tools/pear_deploy/production_assets.py --repo-root ."'

echo "Installing public Caddy and production-assets service config..."
printf -v runtime_command '%q ' \
    python3 \
    "$REMOTE_TRUSTED_RUNTIME/tools/pear_deploy/runtime_config.py" \
    install \
    --repo-root "$REMOTE_TRUSTED_RUNTIME" \
    --fqdn "$FQDN"
ssh -F "$SSH_CONFIG" pear-staging-root "$runtime_command"
install_remote_pear_worker_consumer_timeout_policy
ssh_root "systemctl daemon-reload && systemctl enable caddy ${PEAR_DEPLOY_SERVICES[*]} && systemctl restart caddy ${PEAR_DEPLOY_SERVICES[*]}"
wait_for_remote_pear_worker_consumer_timeout_queues
wait_for_remote_pear_worker_pool_topology
verify_remote_pear_worker_consumer_timeout_policy

if [[ -n "$PEAR_GEMINI_API_KEY" ]]; then
    echo "Copying Gemini Interactions key to the server without printing it..."
    printf 'PEAR_GEMINI_INTERACTIONS_API_KEY=%s\n' "$PEAR_GEMINI_API_KEY" |
        ssh_root 'cat >>/home/zulipdev/pear.env && chown zulipdev:zulipdev /home/zulipdev/pear.env && chmod 600 /home/zulipdev/pear.env'
else
    echo "WARNING: No Gemini key found in local env; addressing and live summaries will fail closed." >&2
fi

echo "Ensuring Pear embedded bot is configured..."
ssh -F "$SSH_CONFIG" pear-staging-root \
    "sudo -Hu zulipdev bash -lc 'cd /home/zulipdev/zulip && set -a && . /home/zulipdev/pear.env && set +a && /home/zulipdev/zulip/.venv/bin/python3 /home/zulipdev/zulip/manage.py shell'" <<'PY'
from django.test import RequestFactory

from zerver.models.users import UserProfile
from zerver.views.pear import build_pear_config_for_user, post_pear_config_backend

owner = (
    UserProfile.objects.filter(
        realm__string_id="zulip",
        is_active=True,
        is_bot=False,
        role=UserProfile.ROLE_REALM_OWNER,
    )
    .order_by("id")
    .first()
)
if owner is None:
    owner = (
        UserProfile.objects.filter(
            realm__string_id="zulip",
            is_active=True,
            is_bot=False,
        )
        .order_by("role", "id")
        .first()
    )
if owner is None:
    raise SystemExit("No active human user found in the zulip realm.")

request = RequestFactory().post("/json/pear/config")
request.user = owner
response = post_pear_config_backend(request, owner)
if response.status_code != 200:
    raise SystemExit(f"Pear config failed with HTTP {response.status_code}: {response.content!r}")

config = build_pear_config_for_user(owner)
print(
    "Pear configured:",
    {
        "enabled": config["enabled"],
        "bot_user_id": config["bot_user_id"],
        "bot_full_name": config["bot_full_name"],
        "worker_agent_runtime": config["worker_agent_runtime"],
        "worker_runtime_ready": config["worker_runtime_ready"],
        "worker_runtime_auth_ready": config["worker_runtime_auth_ready"],
    },
)
PY

ssh_root "systemctl restart ${PEAR_DEPLOY_SERVICES[*]}"
wait_for_remote_pear_worker_pool_topology

DEPLOYED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
ssh_root 'install -d -m 755 -o root -g root /var/lib/pear'
{
    printf '%s %s\n' "$LOCAL_SHA" "$DEPLOYED_AT"
    printf 'record_version=2\n'
    printf 'parent_sha=%s\n' "$LOCAL_PARENT_SHA"
    printf 'zulip_sha=%s\n' "$LOCAL_SHA"
    printf 'zulip_terminal_sha=%s\n' "$LOCAL_TERMINAL_SHA"
    printf 'contract_version=%s\n' "$LOCAL_CONTRACT_VERSION"
    printf 'pear_skills_tree=%s\n' "$LOCAL_PEAR_SKILLS_TREE"
    printf 'workspace_helper_sha256=%s\n' "$LOCAL_WORKSPACE_HELPER_SHA"
    printf 'asset_mode=production-hashed\n'
    printf 'caddy_auth=public-devlogin\n'
    printf 'deployed_at=%s\n' "$DEPLOYED_AT"
} | ssh_root python3 \
    "$REMOTE_TRUSTED_RUNTIME/tools/pear_deploy/deployment_record.py" \
    --destination /var/lib/pear/deployed-sha

echo "Remote services:"
ssh_root "systemctl --no-pager --full status caddy ${PEAR_DEPLOY_SERVICES[*]}" | sed -n '1,260p'

echo
echo "Done. Verify with:"
echo "  tools/pear_deploy/verify_remote.sh $STATE_PATH"
