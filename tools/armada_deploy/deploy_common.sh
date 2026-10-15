#!/usr/bin/env bash

ARMADA_DEPLOY_UNTRACKED_ALLOW_PREFIXES=(
    '.playwright-cli/'
    '.venv/'
    '.vagrant/'
    '.pnpm-store/'
    'node_modules/'
    'var/'
    'output/'
    'prod-static/'
    'webpack-stats-production.json'
    'staticfiles.json'
    'staticfiles/'
    'static/generated/'
    'static/webpack-bundles/'
    'static/images/landing-page/hello/generated/'
    'locale/en/'
    'locale/language_options.json'
    'locale/language_name_map.json'
    '.mypy_cache/'
    '.pytest_cache/'
    '.ruff_cache/'
    'web/.parcel-cache/'
    'web/coverage/'
    'web/generated/'
    'zproject/dev-secrets.conf'
)

ARMADA_DEPLOY_RSYNC_EXCLUDES=()
for armada_deploy_allowed_prefix in "${ARMADA_DEPLOY_UNTRACKED_ALLOW_PREFIXES[@]}"; do
    ARMADA_DEPLOY_RSYNC_EXCLUDES+=(--exclude "/$armada_deploy_allowed_prefix")
done
unset armada_deploy_allowed_prefix

# These are build products, not source. They are generated on the target from
# the just-synced source and snapshotted/restored as a separate rollback
# domain. In particular, never let an ignored local stats/manifest file leak
# into the source rsync.
ARMADA_DEPLOY_RSYNC_EXCLUDES+=(
    --exclude 'node_modules/'
    --exclude '/locale/*/mobile.json'
    --exclude '*.mo'
)

ARMADA_DEPLOY_RUNTIME_CONFIG_PATHS=(
    /etc/caddy/Caddyfile
    /etc/armada/caddy-basic-auth.env
    /etc/systemd/system/caddy.service.d/armada-basic-auth.conf
    /etc/sysctl.d/90-armada-codex-userns.conf
    /usr/local/bin/armada-run-zulip-dev
    /usr/local/lib/armada-workspace-io
    /etc/systemd/system/armada-zulip-dev.service
    /etc/systemd/system/armada-addressing.service
    /etc/systemd/system/armada-presentation.service
    /etc/systemd/system/armada-worker.service
    /etc/systemd/system/armada-worker-1.service
    /home/zulipdev/armada.env
    /home/zulipdev/armada-deployed-sha
    /var/lib/armada
)

ARMADA_DEPLOY_TRUSTED_BUNDLE_PATHS=(
    tools/armada_deploy/codex_sandbox_runtime.sh
    tools/armada_deploy/deployment_record.py
    tools/armada_deploy/freeze_asset_build.py
    tools/armada_deploy/production_assets.py
    tools/armada_deploy/rollback_remote.sh
    tools/armada_deploy/runtime_config.py
    tools/armada_deploy/workspace_helper_runtime.py
    tools/armada_deploy/runtime/Caddyfile
    tools/armada_deploy/runtime/armada-run-zulip-dev
    tools/armada_deploy/runtime/armada-zulip-dev.service
    tools/armada_deploy/runtime/armada-addressing.service
    tools/armada_deploy/runtime/armada-presentation.service
    tools/armada_deploy/runtime/armada-worker.service
    tools/armada_deploy/runtime/armada-worker-1.service
    zerver/lib/armada_workspace_io.py
)

ARMADA_WORKSPACE_HELPER_SOURCE_RELATIVE="zerver/lib/armada_workspace_io.py"
ARMADA_WORKSPACE_HELPER_PATH="/usr/local/lib/armada-workspace-io"

ARMADA_DEPLOY_SERVICES=(
    armada-worker
    armada-worker-1
    armada-monitor-worker
    armada-addressing
    armada-presentation
    armada-embedded-bots
    armada-thumbnail
    armada-zulip-dev
)

ARMADA_DEPLOY_WORKER_SERVICES=(
    armada-worker
    armada-worker-1
    armada-monitor-worker
    armada-addressing
    armada-presentation
    armada-embedded-bots
    armada-thumbnail
)

ARMADA_DEPLOY_QUEUES=(
    armada_worker
    armada_worker_1
    armada_monitor_worker
    armada_addressing
    armada_presentation
    embedded_bots
    thumbnail
)

# Pear keeps RabbitMQ deliveries unacknowledged for the duration of a Codex
# turn so a dead worker can be retried.  The broker's 30-minute default would
# otherwise redeliver a healthy, active long turn.  This policy is deliberately
# broker-owned additive state: code rollback leaves it in place, where it is
# also safe for the former single-worker topology.
ARMADA_WORKER_CONSUMER_TIMEOUT_POLICY_NAME="armada-worker-consumer-timeout"
ARMADA_WORKER_CONSUMER_TIMEOUT_POLICY_PATTERN='^armada_worker(_1)?$'
ARMADA_WORKER_CONSUMER_TIMEOUT_MS=21600000
ARMADA_WORKER_CONSUMER_TIMEOUT_POLICY_COMMAND=(
    rabbitmqctl
    set_policy
    -p
    /
    --priority
    10
    --apply-to
    queues
    "$ARMADA_WORKER_CONSUMER_TIMEOUT_POLICY_NAME"
    "$ARMADA_WORKER_CONSUMER_TIMEOUT_POLICY_PATTERN"
    "{\"consumer-timeout\":$ARMADA_WORKER_CONSUMER_TIMEOUT_MS}"
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
        for allowed_prefix in "${ARMADA_DEPLOY_UNTRACKED_ALLOW_PREFIXES[@]}"; do
            if [[
                ( "$allowed_prefix" == */ && "$file" == "$allowed_prefix"* ) ||
                ( "$allowed_prefix" != */ && "$file" == "$allowed_prefix" )
            ]]; then
                allowed=true
                break
            fi
        done
        if [[ "$allowed" != true ]]; then
            printf '%s\n' "$file"
        fi
    done
}

_validate_armada_deploy_queue_snapshot() {
    local require_consumers="$1"
    local require_quiescence="$2"
    local expected_queues="${3:-${ARMADA_DEPLOY_QUEUES[*]}}"
    awk -v expected_queues="$expected_queues" -v require_consumers="$require_consumers" -v require_quiescence="$require_quiescence" '
BEGIN {
    expected_count = split(expected_queues, expected, " ")
    for (i = 1; i <= expected_count; i += 1) {
        required[expected[i]] = 1
    }
}
$1 in required {
    seen[$1] = 1
    print
    if ($2 !~ /^[0-9]+$/ || $3 !~ /^[0-9]+$/ || $4 !~ /^[0-9]+$/) {
        print "invalid queue health row for " $1 > "/dev/stderr"
        bad = 1
        next
    }
    if (require_quiescence == "true" && ($2 != 0 || $3 != 0)) {
        print "queue is not quiescent: " $1 > "/dev/stderr"
        bad = 1
    }
    if (require_consumers == "true" && $4 < 1) {
        print "queue has no active consumer: " $1 > "/dev/stderr"
        bad = 1
    }
}
END {
    for (queue in required) {
        if (!(queue in seen)) {
            print "missing Pear queue: " queue > "/dev/stderr"
            bad = 1
        }
    }
    exit bad
}
'
}

validate_armada_deploy_queue_snapshot() {
    _validate_armada_deploy_queue_snapshot true true "${1:-${ARMADA_DEPLOY_QUEUES[*]}}"
}

validate_armada_deploy_queue_empty_snapshot() {
    _validate_armada_deploy_queue_snapshot false true "${1:-${ARMADA_DEPLOY_QUEUES[*]}}"
}

validate_armada_deploy_queue_ready_snapshot() {
    _validate_armada_deploy_queue_snapshot true false "${1:-${ARMADA_DEPLOY_QUEUES[*]}}"
}

validate_armada_worker_pool_snapshot() {
    awk '
$1 == "armada_worker" || $1 == "armada_worker_1" {
    seen[$1] += 1
    if ($2 !~ /^[0-9]+$/ || $3 !~ /^[0-9]+$/ || $4 !~ /^[0-9]+$/) {
        print "invalid queue health row for " $1 > "/dev/stderr"
        bad = 1
        next
    }
    if ($4 != 2) {
        print "Pear worker queue must have exactly two consumers: " $1 > "/dev/stderr"
        bad = 1
    }
}
END {
    for (i = 1; i <= 2; i += 1) {
        queue = i == 1 ? "armada_worker" : "armada_worker_1"
        if (seen[queue] != 1) {
            print "expected exactly one queue health row for " queue > "/dev/stderr"
            bad = 1
        }
    }
    exit bad
}
'
}

validate_armada_worker_pool_topology_snapshot() {
    python3 -c '
import json
import sys

separator = "\n-- armada-worker-channels --\n"
payload = sys.stdin.read()
if payload.count(separator) != 1:
    raise SystemExit("invalid Pear worker topology snapshot envelope")
consumer_text, channel_text = payload.split(separator, 1)
try:
    consumers = json.loads(consumer_text)
    channels = json.loads(channel_text)
except (json.JSONDecodeError, UnicodeDecodeError) as error:
    raise SystemExit(f"invalid Pear worker topology JSON: {error}")
if not isinstance(consumers, list) or not isinstance(channels, list):
    raise SystemExit("Pear worker topology snapshots must be lists")

expected_queues = {"armada_worker", "armada_worker_1"}
worker_consumers = [
    row
    for row in consumers
    if isinstance(row, dict) and row.get("queue_name") in expected_queues
]
if len(worker_consumers) != 4:
    raise SystemExit(f"expected four Pear worker consumers, found {len(worker_consumers)}")

def arguments_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        try:
            return dict(value)
        except (TypeError, ValueError):
            return {}
    return {}

by_queue: dict[str, list[dict[str, object]]] = {name: [] for name in expected_queues}
by_channel: dict[str, list[dict[str, object]]] = {}
consumer_keys: set[tuple[str, str]] = set()
for row in worker_consumers:
    queue = row["queue_name"]
    channel_pid = row.get("channel_pid")
    consumer_tag = row.get("consumer_tag")
    if not isinstance(channel_pid, str) or not channel_pid:
        raise SystemExit(f"{queue} consumer has no channel PID")
    if not isinstance(consumer_tag, str) or not consumer_tag:
        raise SystemExit(f"{queue} consumer has no consumer tag")
    consumer_key = (channel_pid, consumer_tag)
    if consumer_key in consumer_keys:
        raise SystemExit("duplicate Pear worker consumer identity")
    consumer_keys.add(consumer_key)
    if row.get("ack_required") is not True:
        raise SystemExit(f"{queue} consumer is not using manual acknowledgements")
    if row.get("active") is not True:
        raise SystemExit(f"{queue} consumer is not active")
    if row.get("prefetch_count") != 1:
        raise SystemExit(f"{queue} consumer does not have prefetch 1")
    priority = arguments_dict(row.get("arguments")).get("x-priority")
    # RabbitMQ omits x-priority for the default-priority consumer; normalize
    # that wire representation to the explicit zero used by the topology
    # contract.
    if priority is None:
        priority = 0
    if priority not in (0, 10):
        raise SystemExit(f"{queue} consumer has invalid priority {priority!r}")
    row["_armada_priority"] = priority
    by_queue[queue].append(row)
    by_channel.setdefault(channel_pid, []).append(row)

for queue, rows in by_queue.items():
    if len(rows) != 2 or any(row["_armada_priority"] not in (0, 10) for row in rows):
        raise SystemExit(f"{queue} does not have two valid pool consumers")
if len(by_channel) != 2:
    raise SystemExit(f"expected two Pear worker channels, found {len(by_channel)}")
for channel_pid, rows in by_channel.items():
    if {row["queue_name"] for row in rows} != expected_queues:
        raise SystemExit(f"Pear worker channel {channel_pid} is not subscribed to both queues")
    if any(row["_armada_priority"] not in (0, 10) for row in rows):
        raise SystemExit(f"Pear worker channel {channel_pid} has invalid affinity priorities")

channel_rows: dict[str, dict[str, object]] = {}
for row in channels:
    if not isinstance(row, dict) or row.get("pid") not in by_channel:
        continue
    pid = row["pid"]
    if pid in channel_rows:
        raise SystemExit(f"duplicate channel snapshot row for {pid}")
    channel_rows[pid] = row
if set(channel_rows) != set(by_channel):
    raise SystemExit("missing Pear worker channel details")
for pid, row in channel_rows.items():
    if row.get("vhost") != "/":
        raise SystemExit(f"Pear worker channel {pid} is on the wrong vhost")
    if row.get("consumer_count") != 2:
        raise SystemExit(f"Pear worker channel {pid} does not have two consumers")
    if row.get("prefetch_count") != 1:
        raise SystemExit(f"Pear worker channel {pid} does not have consumer prefetch 1")
    if row.get("global_prefetch_count") != 1:
        raise SystemExit(f"Pear worker channel {pid} does not have global prefetch 1")
'
}

validate_armada_worker_consumer_timeout_policy_snapshot() {
    python3 -c '
import json
import sys

name, pattern, timeout_text = sys.argv[1:]
timeout = int(timeout_text)
try:
    policies = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError) as error:
    raise SystemExit(f"invalid RabbitMQ policy JSON: {error}")
if not isinstance(policies, list):
    raise SystemExit("RabbitMQ policy snapshot is not a list")

matches = [policy for policy in policies if isinstance(policy, dict) and policy.get("name") == name]
if len(matches) != 1:
    raise SystemExit(f"expected exactly one {name!r} policy, found {len(matches)}")
policy = matches[0]
apply_to = policy.get("apply-to", policy.get("apply_to"))
definition = policy.get("definition")
if isinstance(definition, str):
    try:
        definition = json.loads(definition)
    except (json.JSONDecodeError, TypeError):
        definition = None
if policy.get("vhost") != "/":
    raise SystemExit(f"{name} policy is not scoped to vhost /")
if policy.get("pattern") != pattern:
    raise SystemExit(f"{name} policy has the wrong queue pattern")
if apply_to != "queues":
    raise SystemExit(f"{name} policy does not apply to queues")
if policy.get("priority") != 10:
    raise SystemExit(f"{name} policy has the wrong priority")
if not isinstance(definition, dict) or definition.get("consumer-timeout") != timeout:
    raise SystemExit(f"{name} policy has the wrong consumer timeout")
' "$ARMADA_WORKER_CONSUMER_TIMEOUT_POLICY_NAME" \
        "$ARMADA_WORKER_CONSUMER_TIMEOUT_POLICY_PATTERN" \
        "$ARMADA_WORKER_CONSUMER_TIMEOUT_MS"
}

validate_armada_worker_consumer_timeout_queue_snapshot() {
    python3 -c '
import json
import sys

policy_name, timeout_text = sys.argv[1:]
timeout = int(timeout_text)
expected = {"armada_worker", "armada_worker_1"}
try:
    rows = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError) as error:
    raise SystemExit(f"invalid RabbitMQ queue JSON: {error}")
if not isinstance(rows, list):
    raise SystemExit("RabbitMQ queue snapshot is not a list")

matched = [row for row in rows if isinstance(row, dict) and row.get("name") in expected]
names = [row["name"] for row in matched]
if set(names) != expected or len(names) != len(expected):
    raise SystemExit("RabbitMQ queue snapshot does not contain exactly both Pear worker queues")
for row in matched:
    name = row["name"]
    definition = row.get("effective_policy_definition")
    if isinstance(definition, str):
        try:
            definition = json.loads(definition)
        except (json.JSONDecodeError, TypeError):
            definition = None
    if isinstance(definition, list):
        try:
            definition = dict(definition)
        except (TypeError, ValueError):
            definition = None
    if row.get("type") != "classic":
        raise SystemExit(f"{name} is not a classic queue")
    if row.get("durable") is not True:
        raise SystemExit(f"{name} is not durable")
    if row.get("policy") != policy_name:
        raise SystemExit(f"{name} does not have policy {policy_name}")
    if not isinstance(definition, dict) or definition.get("consumer-timeout") != timeout:
        raise SystemExit(f"{name} does not have the effective consumer timeout")
' "$ARMADA_WORKER_CONSUMER_TIMEOUT_POLICY_NAME" "$ARMADA_WORKER_CONSUMER_TIMEOUT_MS"
}

install_remote_armada_worker_consumer_timeout_policy() {
    local remote_command

    echo "Installing the Pear worker delivery timeout policy..."
    printf -v remote_command '%q ' "${ARMADA_WORKER_CONSUMER_TIMEOUT_POLICY_COMMAND[@]}"
    ssh_root "$remote_command"
    if ! ssh_root "rabbitmqctl list_policies -p / --formatter json" |
        validate_armada_worker_consumer_timeout_policy_snapshot; then
        fail "Pear worker delivery timeout policy verification failed"
    fi
}

wait_for_remote_armada_worker_consumer_timeout_queues() {
    local attempt
    local snapshot=""

    echo "Verifying the delivery timeout on both Pear worker queues..."
    for attempt in $(seq 1 30); do
        snapshot="$(
            ssh_root "rabbitmqctl list_queues -p / name type durable policy effective_policy_definition arguments --formatter json" 2>/dev/null || true
        )"
        if printf '%s\n' "$snapshot" |
            validate_armada_worker_consumer_timeout_queue_snapshot >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    printf '%s\n' "$snapshot" | validate_armada_worker_consumer_timeout_queue_snapshot || true
    fail "Pear worker queues did not acquire the required delivery timeout policy"
}

verify_remote_armada_worker_consumer_timeout_policy() {
    if ! ssh_root "rabbitmqctl list_policies -p / --formatter json" |
        validate_armada_worker_consumer_timeout_policy_snapshot; then
        fail "Pear worker delivery timeout policy verification failed"
    fi
    if ! ssh_root "rabbitmqctl list_queues -p / name type durable policy effective_policy_definition arguments --formatter json" |
        validate_armada_worker_consumer_timeout_queue_snapshot; then
        fail "Pear worker queues do not have the required delivery timeout policy"
    fi
}

remote_armada_worker_pool_topology_snapshot() {
    local consumer_snapshot
    local channel_snapshot

    consumer_snapshot="$(
        ssh_root "rabbitmqctl list_consumers -p / --formatter json"
    )"
    channel_snapshot="$(
        ssh_root "rabbitmqctl list_channels pid vhost consumer_count prefetch_count global_prefetch_count --formatter json"
    )"
    printf '%s\n-- armada-worker-channels --\n%s\n' "$consumer_snapshot" "$channel_snapshot"
}

wait_for_remote_armada_worker_pool_topology() {
    local attempt
    local snapshot=""

    echo "Verifying both consumers and channels in the Pear worker pool..."
    for attempt in $(seq 1 30); do
        snapshot="$(remote_armada_worker_pool_topology_snapshot 2>/dev/null || true)"
        if printf '%s' "$snapshot" |
            validate_armada_worker_pool_topology_snapshot >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    printf '%s' "$snapshot" | validate_armada_worker_pool_topology_snapshot || true
    fail "Pear worker pool did not reach the required two-slot topology"
}

stage_remote_armada_deploy_bundle() {
    local repo_root="$1"
    local revision="$2"
    local stage

    stage="$(
        git -C "$repo_root" archive --format=tar "$revision" -- \
            "${ARMADA_DEPLOY_TRUSTED_BUNDLE_PATHS[@]}" |
            ssh -F "$SSH_CONFIG" armada-staging-root '
                set -euo pipefail
                umask 077
                stage="$(mktemp -d /root/armada-deploy-runtime.XXXXXXXX)"
                complete=false
                cleanup() {
                    if [[ "$complete" != true ]]; then
                        rm -rf -- "$stage"
                    fi
                }
                trap cleanup EXIT
                tar --no-same-owner -xpf - -C "$stage"
                chown -R root:root "$stage"
                chmod -R go-rwx "$stage"
                if find "$stage" -type l -print -quit | grep -q .; then
                    echo "trusted deployment bundle contains a symlink" >&2
                    exit 1
                fi
                complete=true
                printf "%s\n" "$stage"
            '
    )"
    if [[ ! "$stage" =~ ^/root/armada-deploy-runtime\.[A-Za-z0-9]{8}$ ]]; then
        echo "remote trusted deployment bundle returned an invalid path" >&2
        return 1
    fi
    printf '%s\n' "$stage"
}

remove_remote_armada_deploy_bundle() {
    local stage="$1"
    if [[ ! "$stage" =~ ^/root/armada-deploy-runtime\.[A-Za-z0-9]{8}$ ]]; then
        echo "refusing to remove an invalid trusted deployment bundle path" >&2
        return 1
    fi
    ssh -F "$SSH_CONFIG" armada-staging-root "rm -rf -- $stage"
}

verify_caddy_https_runtime() (
    local fqdn="$1"
    local javascript_asset="$2"
    local stylesheet_asset="$3"
    local general_static_asset="$4"
    local temporary_directory
    local headers
    local status
    local app_status
    local asset

    temporary_directory="$(mktemp -d)"
    trap 'rm -rf "$temporary_directory"' EXIT

    app_status=""
    for attempt in $(seq 1 4); do
        app_status="$(curl \
            --silent --show-error --location --max-redirs 3 \
            --connect-timeout 5 --max-time 20 --output /dev/null \
            --write-out '%{http_code}' --url "https://$fqdn/devlogin" || true)"
        if [[ "$app_status" == "200" ]]; then
            break
        fi
        if [[ "$attempt" -lt 4 ]]; then
            echo "public Caddy app check attempt $attempt returned HTTP ${app_status:-none}; retrying" >&2
            sleep 2
        fi
    done
    if [[ "$app_status" != "200" ]]; then
        echo "public Caddy app check returned HTTP ${app_status:-none}" >&2
        return 1
    fi

    for asset in "$javascript_asset" "$stylesheet_asset"; do
        headers="$temporary_directory/webpack-$(basename "$asset").headers"
        status="$(curl \
            --silent --show-error --max-time 10 --dump-header "$headers" \
            --output /dev/null --write-out '%{http_code}' \
            --url "https://$fqdn/static/webpack-bundles/$asset")"
        if [[ "$status" != "200" ]]; then
            echo "Caddy webpack asset check returned HTTP ${status:-none}: $asset" >&2
            return 1
        fi
        if ! grep -Eiq '^cache-control:[[:space:]]*public,[[:space:]]*max-age=31536000,[[:space:]]*immutable[[:space:]]*\r?$' "$headers"; then
            echo "Caddy webpack asset is missing immutable cache policy: $asset" >&2
            return 1
        fi
        if ! grep -Eiq '^x-content-type-options:[[:space:]]*nosniff[[:space:]]*\r?$' "$headers"; then
            echo "Caddy webpack asset is missing nosniff: $asset" >&2
            return 1
        fi
    done

    headers="$temporary_directory/general-static.headers"
    status="$(curl \
        --silent --show-error --max-time 10 --dump-header "$headers" \
        --output /dev/null --write-out '%{http_code}' \
        --url "https://$fqdn/static/$general_static_asset")"
    if [[ "$status" != "200" ]]; then
        echo "Caddy general static asset check returned HTTP ${status:-none}: $general_static_asset" >&2
        return 1
    fi
    if ! grep -Eiq '^cache-control:[[:space:]]*no-cache[[:space:]]*\r?$' "$headers"; then
        echo "Caddy general static asset is missing no-cache policy: $general_static_asset" >&2
        return 1
    fi
    if ! grep -Eiq '^x-content-type-options:[[:space:]]*nosniff[[:space:]]*\r?$' "$headers"; then
        echo "Caddy general static asset is missing nosniff: $general_static_asset" >&2
        return 1
    fi

    headers="$temporary_directory/gzip.headers"
    status="$(curl \
        --silent --show-error --max-time 10 --header 'Accept-Encoding: gzip' \
        --dump-header "$headers" --output /dev/null --write-out '%{http_code}' \
        --url "https://$fqdn/static/webpack-bundles/$javascript_asset")"
    if [[ "$status" != "200" ]] ||
        ! grep -Eiq '^content-encoding:[[:space:]]*gzip[[:space:]]*\r?$' "$headers"; then
        echo "Caddy webpack asset is not served with gzip encoding: $javascript_asset" >&2
        return 1
    fi

    status="$(curl \
        --silent --show-error --max-time 10 --output /dev/null \
        --write-out '%{http_code}' --url "https://$fqdn/webpack/app.js")"
    if [[ "$status" != "404" ]]; then
        echo "legacy /webpack route returned HTTP ${status:-none}, expected 404" >&2
        return 1
    fi

    echo "public_caddy_app=$app_status; static_public=200; static_cache=valid; nosniff=valid; gzip=valid; legacy_webpack=404"
)
