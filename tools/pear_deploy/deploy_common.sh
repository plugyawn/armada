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
