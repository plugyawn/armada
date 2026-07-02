#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_PATH="${1:-$REPO_ROOT/output/pear-staging/server.json}"

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
