#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=tools/pear_deploy/deploy_common.sh
source "$SCRIPT_DIR/deploy_common.sh"
DEFAULT_STATE_PATH="$REPO_ROOT/output/pear-staging/server.json"
STATE_PATH="$DEFAULT_STATE_PATH"

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
REMOTE_UNTRACKED_SOURCE="$(
    ssh -F "$SSH_CONFIG" pear-staging-root 'sudo -Hu zulipdev bash -lc '"'"'
        set -euo pipefail
        cd /home/zulipdev/zulip
        git ls-files --others --exclude-standard
    '"'"'' |
        filter_deploy_untracked_source_files
)"

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
if [[ -f /home/zulipdev/pear-deployed-sha ]]; then
    echo "deployed_record=$(cat /home/zulipdev/pear-deployed-sha)"
else
    echo "deployed_record=missing"
fi
'"'"
if [[ -n "$REMOTE_UNTRACKED_SOURCE" ]]; then
    echo "remote_untracked_source:"
    printf "%s\n" "$REMOTE_UNTRACKED_SOURCE"
    exit 1
fi
echo "remote_untracked_source=clean"

echo
echo "DNS:"
dig +short "$FQDN" A || true

echo
echo "HTTPS auth wall:"
HTTPS_CODE="$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' "https://$FQDN/devlogin" || true)"
if [[ "$HTTPS_CODE" != "401" ]]; then
    echo "Expected unauthenticated HTTPS to return 401, got ${HTTPS_CODE:-none}" >&2
    exit 1
fi
echo "unauthenticated_https=401"

echo
echo "Production runtime config and assets:"
ssh -F "$SSH_CONFIG" pear-staging-root \
    "python3 /home/zulipdev/zulip/tools/pear_deploy/runtime_config.py verify --repo-root /home/zulipdev/zulip --fqdn $FQDN"
ssh -F "$SSH_CONFIG" pear-staging-root \
    'sudo -Hu zulipdev bash -lc '"'"'cd /home/zulipdev/zulip && .venv/bin/python3 tools/pear_deploy/production_assets.py --repo-root .'"'"''
INTERNAL_CODE="$(ssh -F "$SSH_CONFIG" pear-staging-root "curl -sS --max-time 10 -H 'Host: $FQDN' -o /dev/null -w '%{http_code}' http://127.0.0.1:9991/devlogin" || true)"
if [[ "$INTERNAL_CODE" != "200" && "$INTERNAL_CODE" != "302" && "$INTERNAL_CODE" != "303" ]]; then
    echo "Internal Zulip app is unhealthy: ${INTERNAL_CODE:-none}" >&2
    exit 1
fi
if ssh -F "$SSH_CONFIG" pear-staging-root "pgrep -af '[t]ools/webpack|[t]ornado\.autoreload'"; then
    echo "Development webpack/autoreload process is running" >&2
    exit 1
fi
if ssh -F "$SSH_CONFIG" pear-staging-root \
    "curl -fsS --max-time 10 -H 'Host: $FQDN' -o /dev/null http://127.0.0.1:9991/webpack/app.js 2>/dev/null"; then
    echo "Legacy webpack development route is live" >&2
    exit 1
fi
echo "internal_app=$INTERNAL_CODE; webpack_watcher=absent; legacy_webpack_route=absent"

echo
echo "Systemd:"
ssh -F "$SSH_CONFIG" pear-staging-root \
    "systemctl --no-pager --full status caddy ${PEAR_DEPLOY_SERVICES[*]}" | sed -n '1,260p'

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
    "development": settings.DEVELOPMENT,
    "debug": settings.DEBUG,
    "webpack_stats_file": settings.WEBPACK_STATS_FILE,
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
if settings.DEVELOPMENT is not True or settings.DEBUG is not False:
    raise SystemExit("Hosted runtime must keep DEVELOPMENT=True with DEBUG=False")
if not settings.WEBPACK_STATS_FILE.endswith("webpack-stats-production.json"):
    raise SystemExit("Hosted runtime is not using production webpack stats")
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

echo
echo "Pear live health:"
ssh -F "$SSH_CONFIG" pear-staging-root \
    "sudo -Hu zulipdev bash -lc 'cd /home/zulipdev/zulip && set -a && . /home/zulipdev/pear.env && set +a && /home/zulipdev/zulip/tools/pear-health-report --max-active-runs 0 --max-stale-active-runs 0 --max-orphan-active-markers 0'"

echo
echo "Pear queue health:"
ssh -F "$SSH_CONFIG" pear-staging-root \
    'rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers' |
    validate_pear_deploy_queue_snapshot
