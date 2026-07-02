#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEFAULT_STATE_PATH="$REPO_ROOT/output/pear-staging/server.json"
STATE_PATH="$DEFAULT_STATE_PATH"

DEPLOY_UNTRACKED_ALLOW_PREFIXES=(
    '.playwright-cli/'
    '.venv/'
    '.vagrant/'
    '.pnpm-store/'
    'node_modules/'
    'var/'
    'output/'
    'prod-static/'
    'staticfiles/'
    '.mypy_cache/'
    '.pytest_cache/'
    '.ruff_cache/'
    'web/.parcel-cache/'
    'web/coverage/'
)

filter_deploy_untracked_source_files() {
    local file
    local allowed_prefix
    local allowed
    while IFS= read -r file; do
        if [[ -z "$file" ]]; then
            continue
        fi
        allowed=false
        for allowed_prefix in "${DEPLOY_UNTRACKED_ALLOW_PREFIXES[@]}"; do
            if [[ "$file" == "$allowed_prefix"* ]]; then
                allowed=true
                break
            fi
        done
        if [[ "$allowed" != true ]]; then
            printf '%s\n' "$file"
        fi
    done
}

usage() {
    cat <<EOF
Usage: tools/pear_deploy/verify_remote.sh [options] [state.json]

Read-only verification for the hosted Pear/Zulip dev server.

Options:
  --state PATH  Use a specific pear_do.py state file.
  -h, --help    Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --state)
            if [[ $# -lt 2 ]]; then
                echo "Missing value for --state" >&2
                exit 2
            fi
            STATE_PATH="$2"
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            if [[ "$STATE_PATH" != "$DEFAULT_STATE_PATH" ]]; then
                echo "Unexpected argument: $1" >&2
                usage >&2
                exit 2
            fi
            STATE_PATH="$1"
            shift
            ;;
    esac
done

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
    "SSH_CONFIG": state["ssh_config"],
}
for key, value in values.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
)"

LOCAL_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
LOCAL_UNTRACKED_SOURCE="$(git -C "$REPO_ROOT" ls-files --others --exclude-standard | filter_deploy_untracked_source_files)"

echo "Deployment metadata:"
echo "local_head=$LOCAL_SHA"
if [[ -n "$LOCAL_UNTRACKED_SOURCE" ]]; then
    echo "local_untracked_source:"
    printf "%s\n" "$LOCAL_UNTRACKED_SOURCE"
else
    echo "local_untracked_source=clean"
fi
ssh -F "$SSH_CONFIG" pear-staging-root 'sudo -Hu zulipdev bash -lc '"'"'
set -euo pipefail
cd /home/zulipdev/zulip
echo "remote_head=$(git rev-parse HEAD)"
remote_status="$(git status --porcelain --untracked-files=no)"
if [[ -n "$remote_status" ]]; then
    echo "remote_tracked_status:"
    printf "%s\n" "$remote_status"
    exit 1
fi
echo "remote_tracked_status=clean"
remote_untracked_source="$(
    git ls-files --others --exclude-standard |
    while IFS= read -r file; do
        case "$file" in
            .playwright-cli/*|.venv/*|.vagrant/*|.pnpm-store/*|node_modules/*|var/*|output/*|prod-static/*|staticfiles/*|.mypy_cache/*|.pytest_cache/*|.ruff_cache/*|web/.parcel-cache/*|web/coverage/*)
                ;;
            *)
                printf "%s\n" "$file"
                ;;
        esac
    done
)"
if [[ -n "$remote_untracked_source" ]]; then
    echo "remote_untracked_source:"
    printf "%s\n" "$remote_untracked_source"
    exit 1
fi
echo "remote_untracked_source=clean"
if [[ -f /home/zulipdev/pear-deployed-sha ]]; then
    echo "deployed_record=$(cat /home/zulipdev/pear-deployed-sha)"
else
    echo "deployed_record=missing"
fi
'"'"

echo
echo "DNS:"
dig +short "$FQDN" A || true

echo
echo "HTTPS /devlogin:"
curl -fsSIL "https://$FQDN/devlogin" | sed -n '1,20p'

echo
echo "Systemd:"
ssh -F "$SSH_CONFIG" pear-staging-root \
    'systemctl --no-pager --full status caddy pear-zulip-dev pear-embedded-bots pear-worker | sed -n "1,220p"'

echo
echo "Pear config:"
ssh -F "$SSH_CONFIG" pear-staging-root \
    "sudo -Hu zulipdev bash -lc 'cd /home/zulipdev/zulip && set -a && . /home/zulipdev/pear.env && set +a && /home/zulipdev/zulip/.venv/bin/python3 /home/zulipdev/zulip/manage.py shell'" <<'PY'
from zerver.models.bots import Service
from zerver.models.users import UserProfile
from zerver.views.pear import build_pear_config_for_user
from django.conf import settings

user = (
    UserProfile.objects.filter(
        realm__string_id="zulip",
        is_active=True,
        is_bot=False,
        role=UserProfile.ROLE_REALM_OWNER,
    )
    .order_by("id")
    .first()
)
if user is None:
    user = (
        UserProfile.objects.filter(
            realm__string_id="zulip",
            is_active=True,
            is_bot=False,
        )
        .order_by("role", "id")
        .first()
    )
if user is None:
    raise SystemExit("No active human user found in the zulip realm.")

config = build_pear_config_for_user(user)
summary = {
    "configured": config["configured"],
    "enabled": config["enabled"],
    "bot_user_id": config["bot_user_id"],
    "bot_full_name": config["bot_full_name"],
    "bot_email": config["bot_email"],
    "worker_agent_runtime": config["worker_agent_runtime"],
    "worker_model": settings.PEAR_WORKER_MODEL,
    "worker_service_tier": config["worker_service_tier"],
    "worker_fast_mode": config["worker_fast_mode"],
    "worker_reasoning_effort": settings.PEAR_WORKER_REASONING_EFFORT,
    "worker_runtime_ready": config["worker_runtime_ready"],
    "worker_runtime_auth_ready": config["worker_runtime_auth_ready"],
    "addressing_router_runtime": settings.PEAR_ADDRESSING_ROUTER_RUNTIME,
    "addressing_router_model": settings.PEAR_ADDRESSING_ROUTER_MODEL,
    "addressing_router_gemini_model": settings.PEAR_ADDRESSING_ROUTER_GEMINI_MODEL,
    "addressing_router_gemini_key_ready": bool(
        settings.PEAR_ADDRESSING_ROUTER_GEMINI_API_KEY
    ),
}
print(summary)
print(
    list(
        Service.objects.filter(name="pear", user_profile__realm=user.realm).values(
            "id",
            "name",
            "user_profile_id",
            "interface",
            "base_url",
        )
    )
)
PY
