#!/usr/bin/env bash

PEAR_DEPLOY_UNTRACKED_ALLOW_PREFIXES=(
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

PEAR_DEPLOY_RSYNC_EXCLUDES=()
for pear_deploy_allowed_prefix in "${PEAR_DEPLOY_UNTRACKED_ALLOW_PREFIXES[@]}"; do
    PEAR_DEPLOY_RSYNC_EXCLUDES+=(--exclude "/$pear_deploy_allowed_prefix")
done
unset pear_deploy_allowed_prefix

# These are build products, not source. They are generated on the target from
# the just-synced source and snapshotted/restored as a separate rollback
# domain. In particular, never let an ignored local stats/manifest file leak
# into the source rsync.
PEAR_DEPLOY_RSYNC_EXCLUDES+=(
    --exclude 'node_modules/'
    --exclude '/locale/*/mobile.json'
    --exclude '*.mo'
)

PEAR_DEPLOY_RUNTIME_CONFIG_PATHS=(
    /etc/caddy/Caddyfile
    /etc/pear/caddy-basic-auth.env
    /etc/systemd/system/caddy.service.d/pear-basic-auth.conf
    /usr/local/bin/pear-run-zulip-dev
    /etc/systemd/system/pear-zulip-dev.service
    /home/zulipdev/pear.env
)

PEAR_DEPLOY_SERVICES=(
    pear-worker
    pear-monitor-worker
    pear-embedded-bots
    pear-thumbnail
    pear-zulip-dev
)

PEAR_DEPLOY_QUEUES=(
    pear_worker
    pear_monitor_worker
    embedded_bots
    thumbnail
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
        for allowed_prefix in "${PEAR_DEPLOY_UNTRACKED_ALLOW_PREFIXES[@]}"; do
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

validate_pear_deploy_queue_snapshot() {
    local expected_queues="${PEAR_DEPLOY_QUEUES[*]}"
    awk -v expected_queues="$expected_queues" '
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
    if ($2 != 0 || $3 != 0 || $4 < 1) {
        print "queue is not quiescent: " $1 > "/dev/stderr"
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

validate_local_caddy_auth_bootstrap() {
    local username="$1"
    local hash_file="$2"
    if [[ -z "$username" || -z "$hash_file" ]]; then
        echo "PEAR_CADDY_BASICAUTH_USER and PEAR_CADDY_BASICAUTH_HASH_FILE are required" >&2
        return 1
    fi
    if [[ ! -f "$hash_file" || -L "$hash_file" || ! -r "$hash_file" ]]; then
        echo "PEAR_CADDY_BASICAUTH_HASH_FILE must be a readable regular non-symlink file" >&2
        return 1
    fi
    python3 - "$username" "$hash_file" <<'PY'
import re
import sys
from pathlib import Path

username, path = sys.argv[1:]
if re.fullmatch(r"[A-Za-z0-9._@-]{1,128}", username) is None:
    raise SystemExit(1)
lines = Path(path).read_text(encoding="utf-8").splitlines()
if len(lines) != 1 or re.fullmatch(r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}", lines[0]) is None:
    raise SystemExit(1)
PY
}
