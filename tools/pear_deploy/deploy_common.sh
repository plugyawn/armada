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
    'staticfiles/'
    'static/generated/'
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
