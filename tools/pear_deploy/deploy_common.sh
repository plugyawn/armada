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
    /etc/sysctl.d/90-pear-codex-userns.conf
    /usr/local/bin/pear-run-zulip-dev
    /usr/local/lib/pear-workspace-io
    /etc/systemd/system/pear-zulip-dev.service
    /etc/systemd/system/pear-addressing.service
    /etc/systemd/system/pear-presentation.service
    /home/zulipdev/pear.env
    /home/zulipdev/pear-deployed-sha
    /var/lib/pear
)

PEAR_DEPLOY_TRUSTED_BUNDLE_PATHS=(
    tools/pear_deploy/codex_sandbox_runtime.sh
    tools/pear_deploy/deployment_record.py
    tools/pear_deploy/freeze_asset_build.py
    tools/pear_deploy/production_assets.py
    tools/pear_deploy/runtime_config.py
    tools/pear_deploy/workspace_helper_runtime.py
    tools/pear_deploy/runtime/Caddyfile
    tools/pear_deploy/runtime/pear-run-zulip-dev
    tools/pear_deploy/runtime/pear-zulip-dev.service
    tools/pear_deploy/runtime/pear-addressing.service
    tools/pear_deploy/runtime/pear-presentation.service
    zerver/lib/pear_workspace_io.py
)

PEAR_WORKSPACE_HELPER_SOURCE_RELATIVE="zerver/lib/pear_workspace_io.py"
PEAR_WORKSPACE_HELPER_PATH="/usr/local/lib/pear-workspace-io"

PEAR_DEPLOY_SERVICES=(
    pear-worker
    pear-monitor-worker
    pear-addressing
    pear-presentation
    pear-embedded-bots
    pear-thumbnail
    pear-zulip-dev
)

PEAR_DEPLOY_WORKER_SERVICES=(
    pear-worker
    pear-monitor-worker
    pear-addressing
    pear-presentation
    pear-embedded-bots
    pear-thumbnail
)

PEAR_DEPLOY_QUEUES=(
    pear_worker
    pear_monitor_worker
    pear_addressing
    pear_presentation
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

_validate_pear_deploy_queue_snapshot() {
    local require_consumers="$1"
    local expected_queues="${2:-${PEAR_DEPLOY_QUEUES[*]}}"
    awk -v expected_queues="$expected_queues" -v require_consumers="$require_consumers" '
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
    if ($2 != 0 || $3 != 0 || (require_consumers == "true" && $4 < 1)) {
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

validate_pear_deploy_queue_snapshot() {
    _validate_pear_deploy_queue_snapshot true "${1:-${PEAR_DEPLOY_QUEUES[*]}}"
}

validate_pear_deploy_queue_empty_snapshot() {
    _validate_pear_deploy_queue_snapshot false "${1:-${PEAR_DEPLOY_QUEUES[*]}}"
}

stage_remote_pear_deploy_bundle() {
    local repo_root="$1"
    local revision="$2"
    local stage

    stage="$(
        git -C "$repo_root" archive --format=tar "$revision" -- \
            "${PEAR_DEPLOY_TRUSTED_BUNDLE_PATHS[@]}" |
            ssh -F "$SSH_CONFIG" pear-staging-root '
                set -euo pipefail
                umask 077
                stage="$(mktemp -d /root/pear-deploy-runtime.XXXXXXXX)"
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
    if [[ ! "$stage" =~ ^/root/pear-deploy-runtime\.[A-Za-z0-9]{8}$ ]]; then
        echo "remote trusted deployment bundle returned an invalid path" >&2
        return 1
    fi
    printf '%s\n' "$stage"
}

remove_remote_pear_deploy_bundle() {
    local stage="$1"
    if [[ ! "$stage" =~ ^/root/pear-deploy-runtime\.[A-Za-z0-9]{8}$ ]]; then
        echo "refusing to remove an invalid trusted deployment bundle path" >&2
        return 1
    fi
    ssh -F "$SSH_CONFIG" pear-staging-root "rm -rf -- $stage"
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
