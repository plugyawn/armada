#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=tools/armada_deploy/deploy_common.sh
source "$SCRIPT_DIR/deploy_common.sh"
DEFAULT_STATE_PATH="$REPO_ROOT/output/armada-staging/server.json"
STATE_PATH="$DEFAULT_STATE_PATH"
YES=false

usage() {
    cat <<EOF
Usage: tools/armada_deploy/reset_remote_dev_data.sh [options] [state.json]

Reset the hosted Pear/Zulip dev database to a quiet six-user demo realm.
This is destructive and requires --yes.

Options:
  --yes         Stop Pear services, reset the dev database, recreate demo users, restart services.
  --state PATH  Use a specific armada_do.py state file.
  -h, --help    Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes)
            YES=true
            shift
            ;;
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
    echo "Run: python3 tools/armada_deploy/armada_do.py create" >&2
    exit 1
fi

# The demo-user roster (real names/emails) is deployment data, not source:
# it lives in an untracked file so the repository carries no personal
# identities. Copy armada_roster.example.py and fill in your deployment's
# accounts.
ROSTER_PATH="${ARMADA_DEPLOY_ROSTER:-$SCRIPT_DIR/armada_roster.local.py}"

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

ssh_root() {
    ssh -F "$SSH_CONFIG" armada-staging-root "$@"
}

if [[ "$YES" != true ]]; then
    if [[ -f "$ROSTER_PATH" ]]; then
        ROSTER_STATUS="$ROSTER_PATH"
    else
        ROSTER_STATUS="$ROSTER_PATH (MISSING — copy armada_roster.example.py before --yes)"
    fi
    cat <<EOF
Dry run complete. No services were stopped and no database was reset.

Target: https://$FQDN
State:  $STATE_PATH
Roster: $ROSTER_STATUS

This command will delete hosted dev conversation history, Pear worker runs,
workspace runtime state, sessions, drafts, scheduled messages, extra generated users,
all topic worktrees and project caches under /srv/armada-workspaces, and the
per-topic isolation OS users.
Re-run with --yes to reset the remote dev data.
EOF
    exit 0
fi

if [[ ! -f "$ROSTER_PATH" ]]; then
    echo "Missing demo-user roster: $ROSTER_PATH" >&2
    echo "Copy tools/armada_deploy/armada_roster.example.py to armada_roster.local.py" >&2
    echo "and fill in this deployment's account names and emails." >&2
    exit 1
fi

LOCAL_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
REMOTE_TRUSTED_RUNTIME="$(stage_remote_armada_deploy_bundle "$REPO_ROOT" "$LOCAL_SHA")"
cleanup_trusted_runtime() {
    remove_remote_armada_deploy_bundle "$REMOTE_TRUSTED_RUNTIME" >/dev/null 2>&1 || true
}
trap cleanup_trusted_runtime EXIT

echo "Verifying fail-closed Caddy and production-assets runtime config..."
ssh_root python3 "$REMOTE_TRUSTED_RUNTIME/tools/armada_deploy/runtime_config.py" verify \
    --repo-root "$REMOTE_TRUSTED_RUNTIME" \
    --fqdn "$FQDN"

echo "Stopping Pear/Zulip services..."
ssh_root "systemctl stop ${ARMADA_DEPLOY_SERVICES[*]}"

# Topic worktrees must not survive the database reset: workdir slugs are
# deterministic per (realm, stream, topic), so a recreated same-name topic
# would silently reuse a stale worktree — and keep its old branch, because
# provisioning short-circuits on an existing worktree. Project clone caches
# live under the same root. The per-topic isolation OS users are recreated
# on demand by the isolation helper, so they go too.
echo "Removing Pear topic worktrees, project caches, and isolation users..."
ssh_root 'rm -rf /srv/armada-workspaces/* || true'
ssh_root "getent passwd | awk -F: '/^armada-[0-9a-f]{24}:/ {print \$1}' | xargs -r -n1 userdel || true"

echo "Resetting the Zulip dev database and creating quiet demo data..."
{
    printf 'ARMADA_ROSTER_B64=%q\n' "$(base64 <"$ROSTER_PATH" | tr -d '\n')"
    cat <<'REMOTE'
set -euo pipefail
ARMADA_ROSTER_PATH=$(mktemp /tmp/armada-roster.XXXXXX.py)
printf '%s' "$ARMADA_ROSTER_B64" | base64 -d >"$ARMADA_ROSTER_PATH"
export ARMADA_ROSTER_PATH
trap 'rm -f "$ARMADA_ROSTER_PATH"' EXIT
MANAGE=(/home/zulipdev/zulip/.venv/bin/python3 /home/zulipdev/zulip/manage.py)

PGHOST=localhost PGUSER=zulip ./scripts/setup/terminate-psql-sessions zulip zulip_base
PGHOST=localhost PGUSER=zulip psql -v ON_ERROR_STOP=1 -e -h localhost postgres zulip <<'SQL'
DROP DATABASE IF EXISTS zulip;
CREATE DATABASE zulip TEMPLATE zulip_base;
SQL

./scripts/setup/flush-memcached
"${MANAGE[@]}" purge_queue --all
"${MANAGE[@]}" migrate --noinput
"${MANAGE[@]}" get_migration_status --settings=zproject.settings --output="migration_status_dev"
"${MANAGE[@]}" createcachetable third_party_api_results
"${MANAGE[@]}" populate_db \
    --num-messages=0 \
    --threads=1 \
    --direct-message-groups=0 \
    --personals=0 \
    --percent-direct-message-groups=0 \
    --percent-personals=0 \
    --percent-topic-links=0 \
    --percent-attachments=0 \
    --extra-users=0 \
    --extra-bots=0 \
    --extra-streams=0 \
    --max-topics=1 \
    --automated

"${MANAGE[@]}" shell <<'PY'
from django.contrib.sessions.models import Session
from django.test import RequestFactory

from zerver.actions.user_settings import do_change_full_name, do_change_user_delivery_email
from zerver.actions.users import do_change_user_role, do_deactivate_user
from zerver.models import DirectMessageGroup, Draft, Message, Realm, Stream, UserProfile
from zerver.models.bots import AgentRun, Service
from zerver.models.scheduled_jobs import ScheduledMessage
from zerver.models.user_topics import UserTopic
from zerver.models.users import get_user_by_delivery_email
from zerver.views.armada import build_armada_config_for_user, post_armada_config_backend

realm = Realm.objects.get(string_id="zulip")

# Keep generated channels/settings useful, but remove all generated conversation history.
Message.objects.all().delete()
AgentRun.objects.all().delete()
DirectMessageGroup.objects.all().delete()
Draft.objects.all().delete()
ScheduledMessage.objects.all().delete()
UserTopic.objects.all().delete()
Stream.objects.filter(realm=realm).update(first_message_id=None)
realm.first_visible_message_id = 0
realm.save(update_fields=["first_visible_message_id"])

import os

roster_namespace = {"UserProfile": UserProfile}
with open(os.environ["ARMADA_ROSTER_PATH"]) as roster_file:
    exec(roster_file.read(), roster_namespace)
users = roster_namespace["users"]

kept_user_ids = set()
for old_email, full_name, new_email, role, is_staff in users:
    user = get_user_by_delivery_email(old_email, realm)
    do_change_full_name(user, full_name, acting_user=None, notify=False)
    do_change_user_delivery_email(user, new_email, acting_user=None)
    do_change_user_role(user, role, acting_user=None, notify=False)
    user.is_staff = is_staff
    user.timezone = ""
    user.save(update_fields=["is_staff", "timezone"])
    kept_user_ids.add(user.id)

for user in UserProfile.objects.filter(realm=realm, is_bot=False, is_active=True).exclude(
    id__in=kept_user_ids
):
    do_deactivate_user(user, acting_user=None)

for user in UserProfile.objects.filter(is_bot=False, is_active=True).exclude(realm=realm):
    do_deactivate_user(user, acting_user=None)

owner_email = next(
    new_email for _, _, new_email, role, _ in users if role == UserProfile.ROLE_REALM_OWNER
)
owner = UserProfile.objects.get(realm=realm, delivery_email=owner_email)
request = RequestFactory().post("/json/armada/config")
request.user = owner
response = post_armada_config_backend(request, owner)
if response.status_code != 200:
    raise SystemExit(f"Pear config failed with HTTP {response.status_code}: {response.content!r}")

Session.objects.all().delete()

config = build_armada_config_for_user(owner)
print(
    {
        "messages": Message.objects.count(),
        "active_humans": list(
            UserProfile.objects.filter(realm=realm, is_bot=False, is_active=True)
            .order_by("delivery_email")
            .values("id", "delivery_email", "full_name", "role")
        ),
        "armada": {
            "configured": config["configured"],
            "enabled": config["enabled"],
            "bot_user_id": config["bot_user_id"],
            "bot_email": config["bot_email"],
            "runtime": config["worker_agent_runtime"],
            "runtime_ready": config["worker_runtime_ready"],
            "auth_ready": config["worker_runtime_auth_ready"],
        },
        "armada_services": list(
            Service.objects.filter(name="armada", user_profile__realm=realm).values(
                "id",
                "name",
                "user_profile_id",
            )
        ),
    }
)
PY
REMOTE
} | ssh -F "$SSH_CONFIG" armada-staging-root \
    "sudo -Hu zulipdev bash -lc 'cd /home/zulipdev/zulip && set -a && . /home/zulipdev/armada.env && set +a && bash -s'"

echo "Starting services..."
ssh_root "systemctl daemon-reload && systemctl restart caddy ${ARMADA_DEPLOY_SERVICES[*]}"

echo "Done. Verify with:"
echo "  tools/armada_deploy/verify_remote.sh $STATE_PATH"
