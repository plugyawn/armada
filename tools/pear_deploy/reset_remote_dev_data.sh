#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEFAULT_STATE_PATH="$REPO_ROOT/output/pear-staging/server.json"
STATE_PATH="$DEFAULT_STATE_PATH"
YES=false

usage() {
    cat <<EOF
Usage: tools/pear_deploy/reset_remote_dev_data.sh [options] [state.json]

Reset the hosted Pear/Zulip dev database to a quiet six-user demo realm.
This is destructive and requires --yes.

Options:
  --yes         Stop Pear services, reset the dev database, recreate demo users, restart services.
  --state PATH  Use a specific pear_do.py state file.
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

ssh_root() {
    ssh -F "$SSH_CONFIG" pear-staging-root "$@"
}

if [[ "$YES" != true ]]; then
    cat <<EOF
Dry run complete. No services were stopped and no database was reset.

Target: https://$FQDN
State:  $STATE_PATH

This command will delete hosted dev conversation history, Pear worker runs,
topic contexts, sessions, drafts, scheduled messages, and extra generated users.
Re-run with --yes to reset the remote dev data.
EOF
    exit 0
fi

echo "Removing HTTP basic auth from Caddy..."
ssh_root "cat >/etc/caddy/Caddyfile" <<EOF
${FQDN} {
    encode zstd gzip

    reverse_proxy 127.0.0.1:9991
}
EOF

echo "Stopping Pear/Zulip services..."
ssh_root 'systemctl stop pear-zulip-dev pear-embedded-bots pear-worker || true'

echo "Resetting the Zulip dev database and creating quiet demo data..."
ssh -F "$SSH_CONFIG" pear-staging-root \
    "sudo -Hu zulipdev bash -lc 'cd /home/zulipdev/zulip && set -a && . /home/zulipdev/pear.env && set +a && bash -s'" <<'REMOTE'
set -euo pipefail
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
from zerver.models.bots import PearTopicContext, PearWorkerRun, Service
from zerver.models.scheduled_jobs import ScheduledMessage
from zerver.models.user_topics import UserTopic
from zerver.models.users import get_user_by_delivery_email
from zerver.views.pear import build_pear_config_for_user, post_pear_config_backend

realm = Realm.objects.get(string_id="zulip")

# Keep generated channels/settings useful, but remove all generated conversation history.
Message.objects.all().delete()
PearWorkerRun.objects.all().delete()
PearTopicContext.objects.all().delete()
DirectMessageGroup.objects.all().delete()
Draft.objects.all().delete()
ScheduledMessage.objects.all().delete()
UserTopic.objects.all().delete()
Stream.objects.filter(realm=realm).update(first_message_id=None)
realm.first_visible_message_id = 0
realm.save(update_fields=["first_visible_message_id"])

users = [
    ("desdemona@zulip.com", "plugyawn", "plugyawn@pear.backpropaganda.net", UserProfile.ROLE_REALM_OWNER, False),
    ("iago@zulip.com", "sidbing", "sidbing@pear.backpropaganda.net", UserProfile.ROLE_REALM_ADMINISTRATOR, True),
    ("shiva@zulip.com", "jayesh", "jayesh@pear.backpropaganda.net", UserProfile.ROLE_MEMBER, False),
    ("ZOE@zulip.com", "berry", "berry@pear.backpropaganda.net", UserProfile.ROLE_MEMBER, False),
    ("AARON@zulip.com", "glitchfix", "glitchfix@pear.backpropaganda.net", UserProfile.ROLE_MEMBER, False),
    ("hamlet@zulip.com", "ritvi", "ritvi@pear.backpropaganda.net", UserProfile.ROLE_MEMBER, False),
]

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

owner = UserProfile.objects.get(realm=realm, delivery_email="plugyawn@pear.backpropaganda.net")
request = RequestFactory().post("/json/pear/config")
request.user = owner
response = post_pear_config_backend(request, owner)
if response.status_code != 200:
    raise SystemExit(f"Pear config failed with HTTP {response.status_code}: {response.content!r}")

Session.objects.all().delete()

config = build_pear_config_for_user(owner)
print(
    {
        "messages": Message.objects.count(),
        "active_humans": list(
            UserProfile.objects.filter(realm=realm, is_bot=False, is_active=True)
            .order_by("delivery_email")
            .values("id", "delivery_email", "full_name", "role")
        ),
        "pear": {
            "configured": config["configured"],
            "enabled": config["enabled"],
            "bot_user_id": config["bot_user_id"],
            "bot_email": config["bot_email"],
            "runtime": config["worker_agent_runtime"],
            "runtime_ready": config["worker_runtime_ready"],
            "auth_ready": config["worker_runtime_auth_ready"],
        },
        "pear_services": list(
            Service.objects.filter(name="pear", user_profile__realm=realm).values(
                "id",
                "name",
                "user_profile_id",
            )
        ),
    }
)
PY
REMOTE

echo "Starting services..."
ssh_root 'systemctl daemon-reload && systemctl restart caddy pear-zulip-dev pear-embedded-bots pear-worker'

echo "Done. Verify with:"
echo "  tools/pear_deploy/verify_remote.sh $STATE_PATH"
