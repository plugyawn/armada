#!/usr/bin/env bash

# Restore a drained Pear deployment snapshot. This script is copied into the
# root-owned snapshot before cutover and executed by systemd, so its lifecycle
# does not depend on the SSH connection that requested the rollback.

# Optional pre-cutover entries may be absent on older snapshots. Rollback is
# the recovery path, so decode the positional vector without nounset aborts.
set -Eeo pipefail

ROLLBACK_STATUS_PATH="$1"
EXPECTED_PREV_SHA="$2"
EXPECTED_DEPLOY_RECORD_SHA256="$3"
DEPLOY_RECORD_PATH="$4"
SNAPSHOT_DIR="$5"
REMOTE_PATH="$6"
REMOTE_GIT_COMMON_DIR="$7"
REMOTE_PEAR_SKILLS_DIR="$8"
PREDEPLOY_SERVICES="$9"
PREDEPLOY_QUEUES="${10}"
FQDN="${11}"
RUNTIME_CONFIG_COUNT="${12}"
shift 12
RUNTIME_CONFIG_PATHS=("${@:1:RUNTIME_CONFIG_COUNT}")
shift "$RUNTIME_CONFIG_COUNT"
RSYNC_EXCLUDE_COUNT="$1"
shift
RSYNC_EXCLUDES=("$@")

if [[ "${#RSYNC_EXCLUDES[@]}" -ne "$RSYNC_EXCLUDE_COUNT" ]]; then
    echo "invalid rollback rsync exclude argument count" >&2
    exit 1
fi
if [[ ! "$EXPECTED_PREV_SHA" =~ ^[0-9a-f]{40}$ ]]; then
    echo "invalid expected pre-deploy SHA" >&2
    exit 1
fi
if [[ ! "$EXPECTED_DEPLOY_RECORD_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "invalid expected deployment-record SHA-256" >&2
    exit 1
fi
if [[ "$ROLLBACK_STATUS_PATH" != "$SNAPSHOT_DIR/rollback.status" ]]; then
    echo "rollback status path is outside the selected snapshot" >&2
    exit 1
fi

ROLLBACK_STAGE="starting"

write_rollback_status() {
    local state="$1"
    local exit_code="$2"
    local temporary_path="${ROLLBACK_STATUS_PATH}.tmp.$$"

    umask 077
    printf 'state=%s exit_code=%s stage=%s updated_at=%s\n' \
        "$state" "$exit_code" "$ROLLBACK_STAGE" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        >"$temporary_path"
    chmod 600 "$temporary_path"
    mv -f "$temporary_path" "$ROLLBACK_STATUS_PATH"
}

record_rollback_exit() {
    local status=$?
    local state="failed"

    trap - EXIT
    if [[ $status -eq 0 ]]; then
        state="success"
    fi
    write_rollback_status "$state" "$status"
    exit "$status"
}

set_rollback_stage() {
    ROLLBACK_STAGE="$1"
    write_rollback_status running -
}

trap record_rollback_exit EXIT
write_rollback_status running -

set_rollback_stage stop-services
systemctl stop caddy pear-worker pear-monitor-worker pear-embedded-bots pear-thumbnail pear-zulip-dev
systemctl stop pear-worker-1 2>/dev/null || true
if [[ " $PREDEPLOY_SERVICES " != *" pear-worker-1 "* ]]; then
    systemctl disable pear-worker-1 2>/dev/null || true
fi
systemctl stop pear-addressing 2>/dev/null || true
systemctl stop pear-presentation 2>/dev/null || true

# The pre-release Workspace is disposable, but queue deliveries must never be
# left pointing at RUNNING rows that the restored single-slot runtime cannot
# consume. Settle every active row while the new source is still present, then
# purge both worker queues before restoring code and units.
set_rollback_stage settle-runs
sudo -Hu zulipdev bash -lc "cd '$REMOTE_PATH' && set -a && . /home/zulipdev/pear.env && set +a && .venv/bin/python3 manage.py shell" <<'PY'
from django.db import transaction
from django.utils.timezone import now

from zerver.lib.armada_worker import settle_orphaned_pear_run_message
from zerver.models.bots import AgentRun

rollback_error = "Run interrupted by deployment rollback."
with transaction.atomic():
    runs = list(
        AgentRun.objects.select_for_update()
        .select_related("bot_profile")
        .filter(status__in=(AgentRun.QUEUED, AgentRun.RUNNING))
    )
    finished_at = now()
    for run in runs:
        run.status = AgentRun.FAILED
        run.error = rollback_error
        run.current_activity = rollback_error
        run.summary = rollback_error
        run.heartbeat_at = None
        run.date_updated = finished_at
        run.date_finished = finished_at
        run.save(
            update_fields=[
                "status",
                "error",
                "current_activity",
                "summary",
                "heartbeat_at",
                "date_updated",
                "date_finished",
            ]
        )
for run in runs:
    settle_orphaned_pear_run_message(run)
print(f"rollback_settled_runs={len(runs)}")
PY

set_rollback_stage purge-queues
worker_queues="$(
    rabbitmqctl list_queues -p / name --formatter json |
        python3 -c 'import json, sys; expected = {"pear_worker", "pear_worker_1"}; rows = json.load(sys.stdin); print("\n".join(row["name"] for row in rows if isinstance(row, dict) and row.get("name") in expected))'
)"
while IFS= read -r queue; do
    if [[ -n "$queue" ]]; then
        rabbitmqctl purge_queue -p / "$queue"
        if [[ "$queue" == "pear_worker_1" ]]; then
            rabbitmqctl delete_queue -p / "$queue"
        fi
    fi
done <<<"$worker_queues"

set_rollback_stage restore-source
rsync -a --delete \
    "${RSYNC_EXCLUDES[@]}" \
    "$SNAPSHOT_DIR/worktree/" "$REMOTE_PATH/"

if [[ -d "$SNAPSHOT_DIR/git-common" ]]; then
    install -d -m 755 "$(dirname "$REMOTE_GIT_COMMON_DIR")"
    rsync -a --delete "$SNAPSHOT_DIR/git-common/" "$REMOTE_GIT_COMMON_DIR/"
fi
if [[ -f "$SNAPSHOT_DIR/pear-skills.exists" ]]; then
    install -d -m 755 "$REMOTE_PEAR_SKILLS_DIR"
    rsync -a --delete "$SNAPSHOT_DIR/pear-skills/" "$REMOTE_PEAR_SKILLS_DIR/"
else
    rm -rf "$REMOTE_PEAR_SKILLS_DIR"
fi

set_rollback_stage restore-generated-output
rm -rf \
    "$REMOTE_PATH/prod-static" \
    "$REMOTE_PATH/static/generated" \
    "$REMOTE_PATH/static/webpack-bundles" \
    "$REMOTE_PATH/static/images/landing-page/hello/generated" \
    "$REMOTE_PATH/locale/en" \
    "$REMOTE_PATH/web/generated"
rm -f \
    "$REMOTE_PATH/webpack-stats-production.json" \
    "$REMOTE_PATH/staticfiles.json" \
    "$REMOTE_PATH/locale/language_options.json" \
    "$REMOTE_PATH/locale/language_name_map.json"
find "$REMOTE_PATH/locale" -type f -name '*.mo' -delete
find "$REMOTE_PATH/locale" -type f -name 'mobile.json' -delete
if [[ -s "$SNAPSHOT_DIR/generated-output.paths" ]]; then
    tar -C "$REMOTE_PATH" -xpf "$SNAPSHOT_DIR/generated-output.tar"
fi

set_rollback_stage restore-runtime-config
for runtime_path in "${RUNTIME_CONFIG_PATHS[@]}"; do
    rm -rf "$runtime_path"
done
if [[ -s "$SNAPSHOT_DIR/runtime-config.paths" ]]; then
    tar -C / -xpf "$SNAPSHOT_DIR/runtime-config.tar"
fi

# Source and deployment-record agreement is checked only after every snapshot
# domain, including /var/lib/pear, has been restored.
set_rollback_stage verify-restored-source
restored_sha="$(sudo -Hu zulipdev git -C "$REMOTE_PATH" rev-parse HEAD)"
if [[ "$restored_sha" != "$EXPECTED_PREV_SHA" ]]; then
    echo "rollback restored source $restored_sha, expected $EXPECTED_PREV_SHA" >&2
    exit 1
fi
if [[ -n "$(sudo -Hu zulipdev git -C "$REMOTE_PATH" status --porcelain --untracked-files=no)" ]]; then
    echo "rollback restored a dirty tracked source tree" >&2
    sudo -Hu zulipdev git -C "$REMOTE_PATH" status --short --untracked-files=no >&2
    exit 1
fi
restored_record_sha256="$(sha256sum "$DEPLOY_RECORD_PATH" | awk '{print $1}')"
if [[ "$restored_record_sha256" != "$EXPECTED_DEPLOY_RECORD_SHA256" ]]; then
    echo "rollback did not restore the deployment record byte-for-byte" >&2
    exit 1
fi

set_rollback_stage restart-services
systemctl daemon-reload
ROLLBACK_SERVICES=()
for service in caddy pear-worker pear-worker-1 pear-monitor-worker pear-embedded-bots pear-thumbnail pear-zulip-dev; do
    if systemctl cat "$service" >/dev/null 2>&1; then
        ROLLBACK_SERVICES+=("$service")
    fi
done
if systemctl cat pear-addressing >/dev/null 2>&1; then
    ROLLBACK_SERVICES+=(pear-addressing)
fi
if systemctl cat pear-presentation >/dev/null 2>&1; then
    ROLLBACK_SERVICES+=(pear-presentation)
fi
if [[ "${#ROLLBACK_SERVICES[@]}" -gt 0 ]]; then
    systemctl restart "${ROLLBACK_SERVICES[@]}"
fi

validate_predeploy_queues() {
    awk -v expected_queues="$PREDEPLOY_QUEUES" '
BEGIN {
    count = split(expected_queues, expected, " ")
    for (i = 1; i <= count; i += 1) {
        required[expected[i]] = 1
    }
}
$1 in required {
    seen[$1] = 1
    if ($2 !~ /^[0-9]+$/ || $3 !~ /^[0-9]+$/ || $4 !~ /^[0-9]+$/ || $2 != 0 || $3 != 0 || $4 < 1) {
        bad = 1
    }
}
END {
    for (queue in required) {
        if (!(queue in seen)) {
            bad = 1
        }
    }
    exit bad
}
'
}

rollback_service_generation() {
    local service
    for service in caddy $PREDEPLOY_SERVICES; do
        printf '%s=' "$service"
        systemctl show \
            --property=ActiveEnterTimestampMonotonic \
            --property=NRestarts \
            --value \
            "$service" | tr '\n' ':'
        printf '\n'
    done
}

set_rollback_stage verify-runtime
rollback_ready=false
rollback_stable_samples=0
rollback_service_generation_snapshot=""
rollback_public_status=""
for _ in $(seq 1 45); do
    if systemctl is-active caddy $PREDEPLOY_SERVICES >/dev/null 2>&1 &&
        sudo -Hu zulipdev bash -lc "cd '$REMOTE_PATH' && set -a && . /home/zulipdev/pear.env && set +a && tools/armada-health-report --max-active-runs 0 --max-stale-active-runs 0" >/dev/null 2>&1 &&
        rabbitmqctl list_queues -p / name messages_ready messages_unacknowledged consumers 2>/dev/null |
            validate_predeploy_queues; then
        rollback_public_status="$(
            curl \
                --silent --show-error --location --max-redirs 3 \
                --connect-timeout 2 --max-time 4 --output /dev/null \
                --write-out '%{http_code}' --url "https://$FQDN/devlogin" 2>/dev/null || true
        )"
        current_service_generation="$(rollback_service_generation)"
        if [[ "$rollback_public_status" == "200" ]] &&
            systemctl is-active caddy $PREDEPLOY_SERVICES >/dev/null 2>&1; then
            if [[ "$current_service_generation" == "$rollback_service_generation_snapshot" ]]; then
                rollback_stable_samples=$((rollback_stable_samples + 1))
            else
                rollback_service_generation_snapshot="$current_service_generation"
                rollback_stable_samples=1
            fi
            if [[ "$rollback_stable_samples" -ge 10 ]]; then
                rollback_ready=true
                break
            fi
        else
            rollback_stable_samples=0
            rollback_service_generation_snapshot=""
        fi
    else
        rollback_stable_samples=0
        rollback_service_generation_snapshot=""
    fi
    sleep 2
done
if [[ "$rollback_ready" != true ]]; then
    echo "restored Pear runtime did not remain healthy after rollback" >&2
    echo "stable rollback samples: $rollback_stable_samples/10; public HTTPS: ${rollback_public_status:-unavailable}" >&2
    systemctl --no-pager --full status caddy $PREDEPLOY_SERVICES || true
    sudo -Hu zulipdev bash -lc "cd '$REMOTE_PATH' && set -a && . /home/zulipdev/pear.env && set +a && tools/armada-health-report --max-active-runs 0 --max-stale-active-runs 0" || true
    rabbitmqctl list_queues -p / name messages_ready messages_unacknowledged consumers |
        validate_predeploy_queues || true
    exit 1
fi

ROLLBACK_STAGE="complete"
