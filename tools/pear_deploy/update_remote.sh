#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PEAR_ROOT="$(cd "$REPO_ROOT/.." && pwd)"
# shellcheck source=tools/pear_deploy/deploy_common.sh
source "$SCRIPT_DIR/deploy_common.sh"
DEFAULT_STATE_PATH="$REPO_ROOT/output/pear-staging/server.json"
REMOTE_PATH="/home/zulipdev/zulip"
REMOTE_GIT_COMMON_DIR="/home/zulipdev/.git/modules/zulip"
LOCAL_TERMINAL_ROOT="$PEAR_ROOT/zulip-terminal"
LOCAL_PEAR_SKILLS_DIR="$PEAR_ROOT/pear/skills"
REMOTE_PEAR_DIR="/home/zulipdev/pear"
REMOTE_PEAR_SKILLS_DIR="$REMOTE_PEAR_DIR/skills"
DEPLOY_RECORD_PATH="/home/zulipdev/pear-deployed-sha"
SNAPSHOT_ROOT="/home/zulipdev/pear-deploy-snapshots"
REMOTE_AUTH_ENV="/etc/pear/caddy-basic-auth.env"
WORKSPACE_HELPER_RUNTIME="$SCRIPT_DIR/workspace_helper_runtime.py"
YES=false
RUN_TESTS=true
RUN_MIGRATE=true
RUN_COMPAT=true
RUN_RUNTIME_SMOKE=false
RUNTIME_SMOKE_ADMIN_EMAIL=""
RUNTIME_SMOKE_TIMEOUT=150
RUNTIME_SMOKE_EVIDENCE=""
STATE_PATH="$DEFAULT_STATE_PATH"

for env_file in "$PEAR_ROOT/env.local" "$REPO_ROOT/.env.local"; do
    if [[ -f "$env_file" ]]; then
        set -a
        # shellcheck source=/dev/null
        source "$env_file"
        set +a
    fi
done
CADDY_BASICAUTH_USER="${PEAR_CADDY_BASICAUTH_USER:-}"
CADDY_BASICAUTH_HASH_FILE="${PEAR_CADDY_BASICAUTH_HASH_FILE:-}"
CADDY_BASICAUTH_PASSWORD_FILE="${PEAR_CADDY_BASICAUTH_PASSWORD_FILE:-}"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

validate_relative_submodule_checkout() {
    local checkout="$1"
    local label="$2"
    local dotgit="$checkout/.git"
    local gitfile
    local gitdir
    local local_git_dir
    local local_git_common_dir

    if [[ ! -f "$dotgit" ]]; then
        fail "$label must be the canonical submodule checkout with a relative .git gitfile"
    fi
    IFS= read -r gitfile <"$dotgit" || gitfile=""
    if [[ "$gitfile" != gitdir:\ * ]]; then
        fail "$label .git file is not a gitdir file; refusing to deploy"
    fi
    gitdir="${gitfile#gitdir: }"
    if [[ -z "$gitdir" || "$gitdir" == /* ]]; then
        fail "$label .git points at an absolute gitdir ($gitdir); deploy from the main /Users/progyan/codack checkout, not a linked worktree"
    fi

    local_git_dir="$(git -C "$checkout" rev-parse --path-format=absolute --git-dir)"
    local_git_common_dir="$(git -C "$checkout" rev-parse --path-format=absolute --git-common-dir)"
    if [[ "$local_git_dir" != "$local_git_common_dir" ]]; then
        fail "$label is a linked Git worktree ($local_git_dir); deploy from the main checkout"
    fi
}

validate_local_deploy_git_metadata() {
    local parent_git_dir
    local parent_git_common_dir

    if [[ ! -d "$PEAR_ROOT/.git" ]]; then
        fail "parent checkout must be the canonical /Users/progyan/codack checkout with a .git directory"
    fi
    parent_git_dir="$(git -C "$PEAR_ROOT" rev-parse --path-format=absolute --git-dir)"
    parent_git_common_dir="$(git -C "$PEAR_ROOT" rev-parse --path-format=absolute --git-common-dir)"
    if [[ "$parent_git_dir" != "$parent_git_common_dir" ]]; then
        fail "parent checkout is a linked Git worktree ($parent_git_dir); deploy from the main checkout"
    fi

    validate_relative_submodule_checkout "$REPO_ROOT" "Zulip checkout"
    validate_relative_submodule_checkout "$LOCAL_TERMINAL_ROOT" "Zulip Terminal checkout"
}

usage() {
    cat <<EOF
Usage: tools/pear_deploy/update_remote.sh [options] [state.json]

Safely update an already-bootstrapped Pear VM from this clean pear-main
checkout. The script performs read-only gates by default. Mutating deploy steps
require --yes.

Options:
  --yes             Snapshot, stage/build assets, cut over, migrate, verify, record SHA.
  --skip-tests      Skip test-backend verification.
  --skip-migrate    Skip manage.py migrate zerver --noinput.
  --skip-compat     Skip the local contract compatibility checker.
  --run-runtime-smoke
                   Run an opt-in live Pear runtime smoke after queue health.
                   This posts a real @Pear request and spends a real Codex turn.
  --runtime-smoke-admin-email EMAIL
                   Admin/devlogin email for --run-runtime-smoke.
  --runtime-smoke-timeout SECONDS
                   Runtime smoke timeout in seconds (default: 150).
  --runtime-smoke-evidence PATH
                   Write runtime smoke JSON evidence to PATH.
  --auth-password-file PATH
                   Read the Caddy verification password from a local mode-0600
                   regular non-symlink file. The password is sent to curl only
                   through an stdin config and is never placed in argv or logs.
  --state PATH      Use a specific pear_do.py state file.
  -h, --help        Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes)
            YES=true
            shift
            ;;
        --skip-tests)
            RUN_TESTS=false
            shift
            ;;
        --skip-migrate)
            RUN_MIGRATE=false
            shift
            ;;
        --skip-compat)
            RUN_COMPAT=false
            shift
            ;;
        --run-runtime-smoke)
            RUN_RUNTIME_SMOKE=true
            shift
            ;;
        --runtime-smoke-admin-email)
            if [[ $# -lt 2 ]]; then
                echo "Missing value for --runtime-smoke-admin-email" >&2
                exit 2
            fi
            RUNTIME_SMOKE_ADMIN_EMAIL="$2"
            shift 2
            ;;
        --runtime-smoke-timeout)
            if [[ $# -lt 2 ]]; then
                echo "Missing value for --runtime-smoke-timeout" >&2
                exit 2
            fi
            RUNTIME_SMOKE_TIMEOUT="$2"
            shift 2
            ;;
        --runtime-smoke-evidence)
            if [[ $# -lt 2 ]]; then
                echo "Missing value for --runtime-smoke-evidence" >&2
                exit 2
            fi
            RUNTIME_SMOKE_EVIDENCE="$2"
            shift 2
            ;;
        --auth-password-file)
            if [[ $# -lt 2 ]]; then
                echo "Missing value for --auth-password-file" >&2
                exit 2
            fi
            CADDY_BASICAUTH_PASSWORD_FILE="$2"
            shift 2
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
    "IP_ADDRESS": state["ip_address"],
    "SSH_CONFIG": state["ssh_config"],
}
for key, value in values.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
)"

ssh_root() {
    ssh -F "$SSH_CONFIG" pear-staging-root "$@"
}

ssh_zulip() {
    local command="$1"
    local wrapped
    wrapped="$(printf '%q' "cd $REMOTE_PATH && set -a && . /home/zulipdev/pear.env && set +a && $command")"
    ssh_root "sudo -Hu zulipdev bash -lc $wrapped"
}

ssh_zulip_clean() {
    local command="$1"
    local wrapped
    wrapped="$(printf '%q' "cd $REMOTE_PATH && $command")"
    ssh_root "sudo -Hu zulipdev bash -lc $wrapped"
}

preflight_remote_workspace_helper() {
    echo "Preflight: inspecting the isolated Workspace helper without mutation..."
    ssh_root python3 - preflight \
        --destination "$PEAR_WORKSPACE_HELPER_PATH" \
        --expected-sha256 "$LOCAL_WORKSPACE_HELPER_SHA" \
        <"$WORKSPACE_HELPER_RUNTIME"
}

install_and_verify_remote_workspace_helper() {
    local remote_runtime="$REMOTE_PATH/tools/pear_deploy/workspace_helper_runtime.py"
    echo "Installing the committed isolated Workspace helper atomically..."
    ssh_root python3 "$remote_runtime" install \
        --repo-root "$REMOTE_PATH" \
        --destination "$PEAR_WORKSPACE_HELPER_PATH"
    echo "Verifying the installed Workspace helper and its isolation-user protocol..."
    ssh_root python3 "$remote_runtime" verify \
        --repo-root "$REMOTE_PATH" \
        --destination "$PEAR_WORKSPACE_HELPER_PATH" \
        --smoke-existing-isolation-user
}

validate_local_auth_bootstrap() {
    if ! validate_local_caddy_auth_pair \
        "$CADDY_BASICAUTH_USER" \
        "$CADDY_BASICAUTH_HASH_FILE" \
        "$CADDY_BASICAUTH_PASSWORD_FILE"; then
        fail "Caddy bootstrap hash/password inputs are invalid or do not match"
    fi
}

validate_local_auth_verification() {
    if ! validate_local_caddy_verification_auth \
        "$CADDY_BASICAUTH_USER" "$CADDY_BASICAUTH_PASSWORD_FILE"; then
        fail "Caddy verification requires the configured username and a valid local mode-0600 password file"
    fi
}

install_remote_runtime_config() {
    local remote_command
    local command=(
        python3
        "$REMOTE_PATH/tools/pear_deploy/runtime_config.py"
        install
        --repo-root "$REMOTE_PATH"
        --fqdn "$FQDN"
    )
    if [[ "$REMOTE_AUTH_READY" != true ]]; then
        command+=(--bootstrap-auth-user "$CADDY_BASICAUTH_USER" --bootstrap-auth-stdin)
        printf -v remote_command '%q ' "${command[@]}"
        # The bcrypt hash is sent only on stdin. It is never expanded into an
        # environment variable, command argument, or log message.
        ssh -F "$SSH_CONFIG" pear-staging-root "$remote_command" <"$CADDY_BASICAUTH_HASH_FILE"
    else
        printf -v remote_command '%q ' "${command[@]}"
        ssh_root "$remote_command"
    fi
}

remote_auth_env_valid() {
    ssh_root python3 - "$REMOTE_AUTH_ENV" "$FQDN" <<'PY'
import re
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
fqdn = sys.argv[2].lower()
try:
    lines = path.read_text(encoding="utf-8").splitlines()
except (OSError, UnicodeError):
    raise SystemExit(1)
path_stat = path.stat()
if stat.S_IMODE(path_stat.st_mode) != 0o600 or path_stat.st_uid != 0 or path_stat.st_gid != 0:
    raise SystemExit(1)
values = {}
for line in lines:
    key, separator, value = line.partition("=")
    if not separator or key in values:
        raise SystemExit(1)
    values[key] = value
if set(values) != {
    "PEAR_CADDY_FQDN",
    "PEAR_CADDY_BASICAUTH_USER",
    "PEAR_CADDY_BASICAUTH_HASH",
}:
    raise SystemExit(1)
if values["PEAR_CADDY_FQDN"] != fqdn:
    raise SystemExit(1)
if re.fullmatch(r"[A-Za-z0-9._@-]{1,128}", values["PEAR_CADDY_BASICAUTH_USER"]) is None:
    raise SystemExit(1)
if re.fullmatch(r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}", values["PEAR_CADDY_BASICAUTH_HASH"]) is None:
    raise SystemExit(1)
PY
}

remote_auth_username() {
    ssh_root python3 - "$REMOTE_AUTH_ENV" <<'PY'
import sys
from pathlib import Path

for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("=")
    if separator and key == "PEAR_CADDY_BASICAUTH_USER":
        print(value)
        raise SystemExit(0)
raise SystemExit(1)
PY
}

verify_existing_remote_caddy_auth() {
    local status
    status="$(caddy_authenticated_curl \
        "$CADDY_BASICAUTH_USER" \
        "$CADDY_BASICAUTH_PASSWORD_FILE" \
        --silent --show-error --max-time 10 --output /dev/null \
        --write-out '%{http_code}' --url "https://$FQDN/devlogin" || true)"
    if [[ "$status" != "200" && "$status" != "302" && "$status" != "303" ]]; then
        fail "installed Caddy rejected the supplied verification credential before snapshot (HTTP ${status:-none})"
    fi
    echo "Preflight: installed Caddy accepted the supplied verification credential through HTTPS."
}

verify_remote_quiescence() {
    local phase="$1"

    echo "$phase: Pear run health"
    if ! ssh_zulip "$REMOTE_PATH/tools/pear-health-report --max-active-runs 0 --max-stale-active-runs 0 --max-orphan-active-markers 0"; then
        fail "$phase failed: Pear has active, stale, or orphaned work"
    fi

    echo "$phase: service health"
    if ! ssh_root "systemctl is-active ${PEAR_DEPLOY_SERVICES[*]}"; then
        fail "$phase failed: one or more deploy-managed services are inactive"
    fi

    echo "$phase: queue quiescence"
    if ! ssh_root "rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers" |
        validate_pear_deploy_queue_snapshot; then
        fail "$phase failed: one or more deploy-managed queues are busy, missing, or unconsumed"
    fi
}

verify_remote_disk_capacity() {
    local remote_asset_build_dir="/home/zulipdev/pear-asset-build-${LOCAL_SHA:0:12}"
    local runtime_path
    local -a capacity_command=(
        python3
        -
        --remote-path "$REMOTE_PATH"
        --git-common-dir "$REMOTE_GIT_COMMON_DIR"
        --pear-skills-dir "$REMOTE_PEAR_SKILLS_DIR"
        --snapshot-root "$SNAPSHOT_ROOT"
        --asset-build-dir "$remote_asset_build_dir"
    )
    for runtime_path in "${PEAR_DEPLOY_RUNTIME_CONFIG_PATHS[@]}"; do
        capacity_command+=(--runtime-path "$runtime_path")
    done
    echo "Preflight: estimating free space for rollback snapshot and isolated asset build..."
    if ! ssh_root "${capacity_command[@]}" <"$SCRIPT_DIR/deployment_capacity.py"; then
        fail "remote disk capacity is insufficient for a rollback-safe deploy"
    fi
}

run_local_compat() {
    local checker="$PEAR_ROOT/tools/check_pear_compat.py"
    if [[ ! -f "$checker" ]]; then
        fail "local Pear compatibility checker is missing: $checker"
    fi
    echo "Running local Pear contract compatibility check..."
    (cd "$PEAR_ROOT" && python3 tools/check_pear_compat.py)
}

local_untracked_source_files() {
    git -C "$REPO_ROOT" ls-files --others --exclude-standard |
        filter_deploy_untracked_source_files
}

local_pear_skills_status() {
    git -C "$PEAR_ROOT" status --porcelain --untracked-files=all -- pear/skills
}

remote_untracked_source_files() {
    ssh_zulip_clean "git ls-files --others --exclude-standard" |
        filter_deploy_untracked_source_files
}

remote_dirty_tree_matches_local_head() {
    local file
    local local_sha
    local remote_sha
    local remote_path
    local quoted_remote_path
    while IFS= read -r file; do
        if [[ -z "$file" ]]; then
            continue
        fi
        remote_path="$REMOTE_PATH/$file"
        quoted_remote_path="$(printf '%q' "$remote_path")"
        if git -C "$REPO_ROOT" cat-file -e "$LOCAL_SHA:$file" 2>/dev/null; then
            local_sha="$(
                git -C "$REPO_ROOT" show "$LOCAL_SHA:$file" | shasum -a 256 | awk '{print $1}'
            )"
            remote_sha="$(
                ssh_root "if [[ -f $quoted_remote_path ]]; then sha256sum $quoted_remote_path | cut -d' ' -f1; else echo MISSING; fi"
            )"
            if [[ "$remote_sha" != "$local_sha" ]]; then
                echo "Remote dirty file differs from local HEAD: $file" >&2
                return 1
            fi
        else
            remote_sha="$(ssh_root "if [[ -e $quoted_remote_path ]]; then echo PRESENT; else echo MISSING; fi")"
            if [[ "$remote_sha" != "MISSING" ]]; then
                echo "Remote dirty file exists but local HEAD deletes it: $file" >&2
                return 1
            fi
        fi
    done < <(ssh_zulip "git diff --name-only HEAD --")
}

build_remote_production_assets() {
    REMOTE_ASSET_BUILD_DIR="/home/zulipdev/pear-asset-build-${LOCAL_SHA:0:12}"
    echo "Staging source for an isolated production asset build..."
    ssh_root "rm -rf $REMOTE_ASSET_BUILD_DIR && install -d -m 750 -o zulipdev -g zulipdev $REMOTE_ASSET_BUILD_DIR"
    rsync -az --delete \
        "${PEAR_DEPLOY_RSYNC_EXCLUDES[@]}" \
        -e "ssh -F $SSH_CONFIG" \
        "$REPO_ROOT/" \
        "root@$IP_ADDRESS:$REMOTE_ASSET_BUILD_DIR/"
    ssh_root "chown -R zulipdev:zulipdev $REMOTE_ASSET_BUILD_DIR; \
        sudo -Hu zulipdev ln -s $REMOTE_PATH/.venv $REMOTE_ASSET_BUILD_DIR/.venv; \
        sudo -Hu zulipdev ln -s $REMOTE_PATH/zproject/dev-secrets.conf $REMOTE_ASSET_BUILD_DIR/zproject/dev-secrets.conf; \
        sudo -Hu zulipdev mkdir -p $REMOTE_ASSET_BUILD_DIR/var/log"
    echo "Building production assets without touching the live asset graph..."
    ssh_root "sudo -Hu zulipdev bash -lc 'cd $REMOTE_ASSET_BUILD_DIR && ZULIP_DEV_PRODUCTION_ASSETS=1 ./tools/update-prod-static && .venv/bin/python3 tools/pear_deploy/production_assets.py --repo-root .'"
}

cut_over_remote_production_assets() {
    echo "Cutting over the validated production asset graph..."
    ssh_root bash -s -- "$REMOTE_ASSET_BUILD_DIR" "$REMOTE_PATH" <<'REMOTE'
set -euo pipefail
BUILD_ROOT="$1"
REMOTE_PATH="$2"

rm -rf \
    "$REMOTE_PATH/prod-static" \
    "$REMOTE_PATH/static/generated" \
    "$REMOTE_PATH/static/webpack-bundles" \
    "$REMOTE_PATH/static/images/landing-page/hello/generated" \
    "$REMOTE_PATH/web/generated"
rm -f \
    "$REMOTE_PATH/webpack-stats-production.json" \
    "$REMOTE_PATH/staticfiles.json" \
    "$REMOTE_PATH/locale/language_options.json" \
    "$REMOTE_PATH/locale/language_name_map.json"
find "$REMOTE_PATH/locale" -type f -name '*.mo' -delete

for generated_path in \
    prod-static \
    static/generated \
    static/webpack-bundles \
    static/images/landing-page/hello/generated \
    web/generated; do
    install -d -o zulipdev -g zulipdev "$(dirname "$REMOTE_PATH/$generated_path")"
    mv "$BUILD_ROOT/$generated_path" "$REMOTE_PATH/$generated_path"
done
for generated_file in \
    webpack-stats-production.json \
    staticfiles.json \
    locale/language_options.json \
    locale/language_name_map.json; do
    mv "$BUILD_ROOT/$generated_file" "$REMOTE_PATH/$generated_file"
done
rsync -a --include '*/' --include '*.mo' --exclude '*' \
    "$BUILD_ROOT/locale/" "$REMOTE_PATH/locale/"
chown -R zulipdev:zulipdev \
    "$REMOTE_PATH/prod-static" \
    "$REMOTE_PATH/static/generated" \
    "$REMOTE_PATH/static/webpack-bundles" \
    "$REMOTE_PATH/static/images/landing-page/hello/generated" \
    "$REMOTE_PATH/web/generated" \
    "$REMOTE_PATH/webpack-stats-production.json" \
    "$REMOTE_PATH/staticfiles.json" \
    "$REMOTE_PATH/locale"
REMOTE
}

verify_remote_production_assets() {
    local asset_paths
    local -a assets
    local asset
    ssh_zulip_clean "$REMOTE_PATH/.venv/bin/python3 $REMOTE_PATH/tools/pear_deploy/production_assets.py --repo-root $REMOTE_PATH"
    ssh_zulip "$REMOTE_PATH/.venv/bin/python3 $REMOTE_PATH/manage.py shell -c \"from django.conf import settings; assert settings.DEVELOPMENT is True; assert settings.DEBUG is False; assert settings.WEBPACK_STATS_FILE.endswith('webpack-stats-production.json'); print('Django production-asset settings valid.')\""

    asset_paths="$(ssh_zulip_clean "$REMOTE_PATH/.venv/bin/python3 $REMOTE_PATH/tools/pear_deploy/production_assets.py --repo-root $REMOTE_PATH --print-probe-paths")"
    while IFS= read -r asset; do
        [[ -n "$asset" ]] && assets+=("$asset")
    done <<<"$asset_paths"
    if [[ "${#assets[@]}" -ne 3 || -z "${assets[0]}" || -z "${assets[1]}" || -z "${assets[2]}" ]]; then
        fail "remote production asset discovery did not return JS, CSS, and general-static paths"
    fi

    if ! verify_caddy_https_runtime \
        "$FQDN" \
        "$CADDY_BASICAUTH_USER" \
        "$CADDY_BASICAUTH_PASSWORD_FILE" \
        "${assets[0]}" \
        "${assets[1]}" \
        "${assets[2]}"; then
        fail "authenticated Caddy production-runtime verification failed"
    fi

    if ssh_root "pgrep -af '[t]ools/webpack|[t]ornado\.autoreload'"; then
        fail "development webpack/autoreload process is running in production-assets mode"
    fi
}

SNAPSHOT_DIR=""
REMOTE_ASSET_BUILD_DIR=""
ROLLBACK_READY=false
ROLLBACK_DONE=false

rollback_on_error() {
    local status=$?
    if [[ $status -ne 0 && "$ROLLBACK_READY" == true && "$ROLLBACK_DONE" == false ]]; then
        echo
        echo "Update failed after snapshot; rolling back from $SNAPSHOT_DIR" >&2
        set +e
        ssh_root bash -s -- \
            "$SNAPSHOT_DIR" \
            "$REMOTE_PATH" \
            "$REMOTE_GIT_COMMON_DIR" \
            "$REMOTE_PEAR_DIR" \
            "$REMOTE_PEAR_SKILLS_DIR" \
            "${#PEAR_DEPLOY_RUNTIME_CONFIG_PATHS[@]}" \
            "${PEAR_DEPLOY_RUNTIME_CONFIG_PATHS[@]}" \
            "${#PEAR_DEPLOY_RSYNC_EXCLUDES[@]}" \
            "${PEAR_DEPLOY_RSYNC_EXCLUDES[@]}" <<'REMOTE'
set -euo pipefail
SNAPSHOT_DIR="$1"
REMOTE_PATH="$2"
REMOTE_GIT_COMMON_DIR="$3"
REMOTE_PEAR_DIR="$4"
REMOTE_PEAR_SKILLS_DIR="$5"
RUNTIME_CONFIG_COUNT="$6"
shift 6
RUNTIME_CONFIG_PATHS=("${@:1:RUNTIME_CONFIG_COUNT}")
shift "$RUNTIME_CONFIG_COUNT"
RSYNC_EXCLUDE_COUNT="$1"
shift
RSYNC_EXCLUDES=("$@")
if [[ "${#RSYNC_EXCLUDES[@]}" -ne "$RSYNC_EXCLUDE_COUNT" ]]; then
    echo "invalid rollback rsync exclude argument count" >&2
    exit 1
fi

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

for runtime_path in "${RUNTIME_CONFIG_PATHS[@]}"; do
    rm -rf "$runtime_path"
done
if [[ -s "$SNAPSHOT_DIR/runtime-config.paths" ]]; then
    tar -C / -xpf "$SNAPSHOT_DIR/runtime-config.tar"
fi

chown -R zulipdev:zulipdev "$REMOTE_PATH"
chown -R zulipdev:zulipdev /home/zulipdev/.git 2>/dev/null || true
chown -R zulipdev:zulipdev "$REMOTE_PEAR_DIR" 2>/dev/null || true
systemctl daemon-reload
systemctl restart caddy pear-worker pear-monitor-worker pear-embedded-bots pear-thumbnail pear-zulip-dev
REMOTE
        local rollback_status=$?
        set -e
        if [[ $rollback_status -eq 0 ]]; then
            ROLLBACK_DONE=true
            echo "Rollback restored the pre-deploy snapshot and restarted Pear services." >&2
        else
            echo "Rollback failed; inspect $SNAPSHOT_DIR on the box." >&2
        fi
    fi
    if [[ -n "$REMOTE_ASSET_BUILD_DIR" ]]; then
        ssh_root "rm -rf $REMOTE_ASSET_BUILD_DIR" >/dev/null 2>&1 || true
    fi
    exit "$status"
}
trap rollback_on_error EXIT

validate_local_deploy_git_metadata

LOCAL_PARENT_BRANCH="$(git -C "$PEAR_ROOT" branch --show-current)"
LOCAL_PARENT_SHA="$(git -C "$PEAR_ROOT" rev-parse HEAD)"
LOCAL_PARENT_STATUS="$(git -C "$PEAR_ROOT" status --porcelain --untracked-files=no)"
LOCAL_BRANCH="$(git -C "$REPO_ROOT" branch --show-current)"
LOCAL_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
LOCAL_GIT_COMMON_DIR="$(git -C "$REPO_ROOT" rev-parse --git-common-dir)"
LOCAL_STATUS="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)"
LOCAL_UNTRACKED_SOURCE="$(local_untracked_source_files)"
LOCAL_TERMINAL_BRANCH="$(git -C "$LOCAL_TERMINAL_ROOT" branch --show-current)"
LOCAL_TERMINAL_SHA="$(git -C "$LOCAL_TERMINAL_ROOT" rev-parse HEAD)"
LOCAL_TERMINAL_STATUS="$(git -C "$LOCAL_TERMINAL_ROOT" status --porcelain --untracked-files=all)"
LOCAL_PINNED_ZULIP_SHA="$(git -C "$PEAR_ROOT" rev-parse HEAD:zulip)"
LOCAL_PINNED_TERMINAL_SHA="$(git -C "$PEAR_ROOT" rev-parse HEAD:zulip-terminal)"
LOCAL_PEAR_SKILLS_STATUS="$(local_pear_skills_status)"
LOCAL_PEAR_SKILLS_TREE="$(git -C "$PEAR_ROOT" rev-parse HEAD:pear/skills)"
LOCAL_CONTRACT_VERSION="$(
    python3 - "$PEAR_ROOT/pear/contract.json" <<'PY'
import json
import sys

with open(sys.argv[1]) as contract_file:
    print(json.load(contract_file)["server_contract_version"])
PY
)"

if [[ "$LOCAL_PARENT_BRANCH" != "pear-main" ]]; then
    fail "parent branch is $LOCAL_PARENT_BRANCH, expected pear-main"
fi
if [[ "$LOCAL_BRANCH" != "pear-main" ]]; then
    fail "Zulip branch is $LOCAL_BRANCH, expected pear-main"
fi
if [[ "$LOCAL_TERMINAL_BRANCH" != "pear-main" ]]; then
    fail "Zulip Terminal branch is $LOCAL_TERMINAL_BRANCH, expected pear-main"
fi
if [[ "$LOCAL_PINNED_ZULIP_SHA" != "$LOCAL_SHA" ]]; then
    fail "parent pins Zulip at $LOCAL_PINNED_ZULIP_SHA, but the canonical checkout is $LOCAL_SHA"
fi
if [[ "$LOCAL_PINNED_TERMINAL_SHA" != "$LOCAL_TERMINAL_SHA" ]]; then
    fail "parent pins Zulip Terminal at $LOCAL_PINNED_TERMINAL_SHA, but the canonical checkout is $LOCAL_TERMINAL_SHA"
fi
if [[ -n "$LOCAL_PARENT_STATUS" ]]; then
    echo "$LOCAL_PARENT_STATUS" >&2
    fail "parent tracked tree or one of its submodules is dirty; commit and pin the coordinated stack before deploying"
fi
if [[ -n "$LOCAL_STATUS" ]]; then
    echo "$LOCAL_STATUS" >&2
    fail "Zulip tracked tree is dirty; commit or revert tracked changes before deploying"
fi
if [[ -n "$LOCAL_TERMINAL_STATUS" ]]; then
    echo "$LOCAL_TERMINAL_STATUS" >&2
    fail "Zulip Terminal tracked tree is dirty; commit or revert tracked changes before deploying"
fi
if [[ -n "$LOCAL_UNTRACKED_SOURCE" ]]; then
    echo "$LOCAL_UNTRACKED_SOURCE" >&2
    fail "local source tree has untracked files that rsync would omit; add, ignore, or remove them before deploying"
fi
if [[ ! -d "$LOCAL_PEAR_SKILLS_DIR" ]]; then
    fail "local Pear skills directory is missing: $LOCAL_PEAR_SKILLS_DIR"
fi
if [[ -n "$LOCAL_PEAR_SKILLS_STATUS" ]]; then
    echo "$LOCAL_PEAR_SKILLS_STATUS" >&2
    fail "local pear/skills tree is dirty; commit or revert skill changes before deploying"
fi

LOCAL_WORKSPACE_HELPER_SHA="$(
    git -C "$REPO_ROOT" cat-file blob "$LOCAL_SHA:$PEAR_WORKSPACE_HELPER_SOURCE_RELATIVE" |
        shasum -a 256 | awk '{print $1}'
)"
LOCAL_WORKSPACE_HELPER_WORKTREE_SHA="$(
    shasum -a 256 "$REPO_ROOT/$PEAR_WORKSPACE_HELPER_SOURCE_RELATIVE" | awk '{print $1}'
)"
if [[ "$LOCAL_WORKSPACE_HELPER_WORKTREE_SHA" != "$LOCAL_WORKSPACE_HELPER_SHA" ]]; then
    fail "local Workspace helper source does not match the committed $LOCAL_SHA blob"
fi

echo "Preflight: parent $LOCAL_PARENT_BRANCH $LOCAL_PARENT_SHA is tracked-clean."
echo "Preflight: parent pins Zulip $LOCAL_SHA and Zulip Terminal $LOCAL_TERMINAL_SHA."
echo "Preflight: local pear/skills is tracked-clean."
echo "Preflight: Pear contract version is $LOCAL_CONTRACT_VERSION."
echo "Preflight: committed Workspace helper SHA-256 is $LOCAL_WORKSPACE_HELPER_SHA."

PREV_SHA="$(ssh_zulip "git rev-parse HEAD")"
REMOTE_STATUS="$(ssh_zulip "git status --porcelain --untracked-files=no")"
REMOTE_UNTRACKED_SOURCE="$(remote_untracked_source_files)"

echo "Preflight: remote HEAD $PREV_SHA"
if [[ -n "$REMOTE_STATUS" ]]; then
    echo "$REMOTE_STATUS" >&2
    if remote_dirty_tree_matches_local_head; then
        echo "Preflight: remote tracked modifications already match local HEAD."
    else
        fail "remote tracked tree has box-only content; sync/commit it back before deploying"
    fi
fi
if [[ -n "$REMOTE_UNTRACKED_SOURCE" ]]; then
    echo "$REMOTE_UNTRACKED_SOURCE" >&2
    fail "remote source tree has untracked files that rsync --delete would remove; sync/commit them back before deploying"
fi
if ! git -C "$REPO_ROOT" cat-file -e "$PREV_SHA^{commit}" 2>/dev/null; then
    fail "remote HEAD $PREV_SHA is not present in the local checkout; refusing to clobber unknown box history"
fi
if ! git -C "$REPO_ROOT" merge-base --is-ancestor "$PREV_SHA" "$LOCAL_SHA"; then
    fail "remote HEAD $PREV_SHA is not an ancestor of local HEAD $LOCAL_SHA"
fi

REMOTE_AUTH_READY=false
if ssh_root "test -e $REMOTE_AUTH_ENV"; then
    if remote_auth_env_valid; then
        REMOTE_AUTH_READY=true
        echo "Preflight: remote Caddy basic-auth environment is present, valid, and mode 0600."
    else
        fail "remote Caddy basic-auth environment exists but is invalid"
    fi
else
    validate_local_auth_bootstrap
    echo "Preflight: remote Caddy auth is absent; validated a local bcrypt hash file for stdin-only bootstrap."
fi
validate_local_auth_verification
if [[ "$REMOTE_AUTH_READY" == true ]]; then
    REMOTE_CADDY_BASICAUTH_USER="$(remote_auth_username)"
    if [[ "$REMOTE_CADDY_BASICAUTH_USER" != "$CADDY_BASICAUTH_USER" ]]; then
        fail "local Caddy verification username does not match the installed remote username"
    fi
    verify_existing_remote_caddy_auth
fi
echo "Preflight: local Caddy verification password file is a current-user-owned mode-0600 regular non-symlink file."

echo "Preflight: remote tracked/untracked source tree is clean and not ahead of local pear-main."
if [[ "$PREV_SHA" != "$LOCAL_SHA" ]]; then
    echo
    echo "Deploy delta:"
    git -C "$REPO_ROOT" diff --stat "$PREV_SHA..$LOCAL_SHA" -- \
        zproject/default_settings.py \
        zerver/lib/pear.py \
        zerver/lib/pear_gate.py \
        zerver/lib/pear_agent.py \
        zerver/lib/pear_worker.py \
        zerver/lib/pear_workspace_index.py \
        zerver/lib/pear_workspace_io.py \
        zerver/lib/pear_workspace_resources.py \
        zerver/worker/pear_worker.py \
        zerver/views/pear.py \
        zerver/views/pear_workspace.py \
        zerver/views/users.py \
        zerver/models/bots.py \
        zerver/migrations \
        zerver/tests/test_service_bot_system.py \
        zerver/tests/test_pear.py \
        zerver/tests/test_pear_endpoint.py \
        web/src \
        web/styles \
        web/templates \
        tools/pear-health-report \
        tools/pear_deploy || true
else
    echo "Deploy delta: remote already has this HEAD."
fi

if [[ "$RUN_COMPAT" == true ]]; then
    run_local_compat
else
    echo "Skipping compat check by request."
fi

verify_remote_quiescence "Preflight quiescence"
preflight_remote_workspace_helper
verify_remote_disk_capacity

if [[ "$YES" != true ]]; then
    echo
    echo "Dry run complete. No snapshot, rsync, migration, restart, or record write was performed."
    echo "Re-run with --yes to deploy $LOCAL_SHA to https://$FQDN."
    exit 0
fi

SNAPSHOT_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SNAPSHOT_DIR="$SNAPSHOT_ROOT/zulip-$SNAPSHOT_STAMP-${PREV_SHA:0:12}"
echo
echo "Snapshotting remote tree to $SNAPSHOT_DIR"
ssh_root bash -s -- \
    "$REMOTE_PATH" \
    "$REMOTE_GIT_COMMON_DIR" \
    "$REMOTE_PEAR_SKILLS_DIR" \
    "$SNAPSHOT_DIR" \
    "${#PEAR_DEPLOY_RUNTIME_CONFIG_PATHS[@]}" \
    "${PEAR_DEPLOY_RUNTIME_CONFIG_PATHS[@]}" \
    "${#PEAR_DEPLOY_RSYNC_EXCLUDES[@]}" \
    "${PEAR_DEPLOY_RSYNC_EXCLUDES[@]}" <<'REMOTE'
set -euo pipefail
umask 077
REMOTE_PATH="$1"
REMOTE_GIT_COMMON_DIR="$2"
REMOTE_PEAR_SKILLS_DIR="$3"
SNAPSHOT_DIR="$4"
RUNTIME_CONFIG_COUNT="$5"
shift 5
RUNTIME_CONFIG_PATHS=("${@:1:RUNTIME_CONFIG_COUNT}")
shift "$RUNTIME_CONFIG_COUNT"
RSYNC_EXCLUDE_COUNT="$1"
shift
RSYNC_EXCLUDES=("$@")
if [[ "${#RSYNC_EXCLUDES[@]}" -ne "$RSYNC_EXCLUDE_COUNT" ]]; then
    echo "invalid snapshot rsync exclude argument count" >&2
    exit 1
fi

install -d -m 700 "$SNAPSHOT_DIR" "$SNAPSHOT_DIR/worktree" "$SNAPSHOT_DIR/git-common"
rsync -a --delete \
    "${RSYNC_EXCLUDES[@]}" \
    "$REMOTE_PATH/" "$SNAPSHOT_DIR/worktree/"
if [[ -d "$REMOTE_GIT_COMMON_DIR" ]]; then
    rsync -a --delete "$REMOTE_GIT_COMMON_DIR/" "$SNAPSHOT_DIR/git-common/"
fi
if [[ -d "$REMOTE_PEAR_SKILLS_DIR" ]]; then
    install -d -m 755 "$SNAPSHOT_DIR/pear-skills"
    rsync -a --delete "$REMOTE_PEAR_SKILLS_DIR/" "$SNAPSHOT_DIR/pear-skills/"
    touch "$SNAPSHOT_DIR/pear-skills.exists"
fi

: >"$SNAPSHOT_DIR/generated-output.paths"
for generated_path in \
    prod-static \
    static/generated \
    static/webpack-bundles \
    static/images/landing-page/hello/generated \
    locale/en \
    locale/language_options.json \
    web/generated \
    webpack-stats-production.json \
    staticfiles.json \
    locale/language_name_map.json; do
    if [[ -e "$REMOTE_PATH/$generated_path" ]]; then
        printf '%s\n' "$generated_path" >>"$SNAPSHOT_DIR/generated-output.paths"
    fi
done
if [[ -d "$REMOTE_PATH/locale" ]]; then
    find "$REMOTE_PATH/locale" -type f -name '*.mo' -printf '%P\n' |
        sed 's#^#locale/#' >>"$SNAPSHOT_DIR/generated-output.paths"
    find "$REMOTE_PATH/locale" -type f -name 'mobile.json' -printf '%P\n' |
        sed 's#^#locale/#' >>"$SNAPSHOT_DIR/generated-output.paths"
fi
if [[ -s "$SNAPSHOT_DIR/generated-output.paths" ]]; then
    tar -C "$REMOTE_PATH" -cpf "$SNAPSHOT_DIR/generated-output.tar" \
        -T "$SNAPSHOT_DIR/generated-output.paths"
fi

: >"$SNAPSHOT_DIR/runtime-config.paths"
for runtime_path in "${RUNTIME_CONFIG_PATHS[@]}"; do
    if [[ -e "$runtime_path" ]]; then
        printf '%s\n' "${runtime_path#/}" >>"$SNAPSHOT_DIR/runtime-config.paths"
    fi
done
if [[ -s "$SNAPSHOT_DIR/runtime-config.paths" ]]; then
    tar -C / -cpf "$SNAPSHOT_DIR/runtime-config.tar" \
        -T "$SNAPSHOT_DIR/runtime-config.paths"
fi
REMOTE
ROLLBACK_READY=true

if [[ -d "$LOCAL_GIT_COMMON_DIR" ]]; then
    echo "Syncing Git metadata for this worktree..."
    ssh_root "install -d -m 755 $REMOTE_GIT_COMMON_DIR"
    rsync -az --delete \
        -e "ssh -F $SSH_CONFIG" \
        "$LOCAL_GIT_COMMON_DIR/" \
        "root@$IP_ADDRESS:$REMOTE_GIT_COMMON_DIR/"
fi

build_remote_production_assets

echo "Stopping the coordinated services for source and asset cutover..."
ssh_root "systemctl stop ${PEAR_DEPLOY_SERVICES[*]}"

echo "Syncing checkout to $REMOTE_PATH..."
rsync -az --delete \
    "${PEAR_DEPLOY_RSYNC_EXCLUDES[@]}" \
    -e "ssh -F $SSH_CONFIG" \
    "$REPO_ROOT/" \
    "root@$IP_ADDRESS:$REMOTE_PATH/"

echo "Syncing Pear Codex skills to $REMOTE_PEAR_SKILLS_DIR..."
ssh_root "install -d -m 755 $REMOTE_PEAR_SKILLS_DIR"
rsync -az --delete \
    -e "ssh -F $SSH_CONFIG" \
    "$LOCAL_PEAR_SKILLS_DIR/" \
    "root@$IP_ADDRESS:$REMOTE_PEAR_SKILLS_DIR/"

cut_over_remote_production_assets

ssh_root "chown -R zulipdev:zulipdev $REMOTE_PATH"
ssh_root 'chown -R zulipdev:zulipdev /home/zulipdev/.git 2>/dev/null || true'
ssh_root "chown -R zulipdev:zulipdev $REMOTE_PEAR_DIR"
ssh_root "sudo -Hu zulipdev git config --global --add safe.directory $REMOTE_PATH"

install_and_verify_remote_workspace_helper

echo "Installing authenticated Caddy and production-assets service configuration..."
install_remote_runtime_config
ssh_root "python3 $REMOTE_PATH/tools/pear_deploy/runtime_config.py verify --repo-root $REMOTE_PATH --fqdn $FQDN"
ssh_root "systemctl daemon-reload"

if [[ "$RUN_MIGRATE" == true ]]; then
    echo "Applying zerver migrations..."
    ssh_zulip "$REMOTE_PATH/.venv/bin/python3 $REMOTE_PATH/manage.py migrate zerver --noinput"
else
    echo "Skipping migration by request."
fi

echo "Restarting Pear services..."
ssh_root "systemctl restart caddy ${PEAR_DEPLOY_SERVICES[*]}"

echo "Waiting for the Caddy auth wall and authenticated app route to serve..."
SITE_CODE=""
AUTHENTICATED_SITE_CODE=""
for _ in $(seq 1 60); do
    SITE_CODE="$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' "https://$FQDN/devlogin" || true)"
    AUTHENTICATED_SITE_CODE="$(caddy_authenticated_curl \
        "$CADDY_BASICAUTH_USER" \
        "$CADDY_BASICAUTH_PASSWORD_FILE" \
        --silent --show-error --max-time 10 --output /dev/null \
        --write-out '%{http_code}' --url "https://$FQDN/devlogin" || true)"
    if [[ "$SITE_CODE" == "401" && ( "$AUTHENTICATED_SITE_CODE" == "200" || "$AUTHENTICATED_SITE_CODE" == "302" || "$AUTHENTICATED_SITE_CODE" == "303" ) ]]; then
        break
    fi
    sleep 5
done
if [[ "$SITE_CODE" != "401" ]]; then
    fail "Caddy basic-auth wall did not become healthy; last HTTP code: ${SITE_CODE:-none}"
fi
if [[ "$AUTHENTICATED_SITE_CODE" != "200" && "$AUTHENTICATED_SITE_CODE" != "302" && "$AUTHENTICATED_SITE_CODE" != "303" ]]; then
    fail "authenticated app route through Caddy did not become healthy; last HTTP code: ${AUTHENTICATED_SITE_CODE:-none}"
fi
verify_remote_production_assets

if [[ "$RUN_TESTS" == true ]]; then
    echo "Running Pear backend tests on the box..."
    ssh_zulip_clean "./tools/test-backend \
        zerver.tests.test_pear \
        zerver.tests.test_pear_endpoint \
        zerver.tests.test_pear_workspace_io \
        zerver.tests.test_pear_workspace_index \
        zerver.tests.test_pear_workspace_resources"
else
    echo "Skipping test-backend by request."
fi

verify_remote_quiescence "Post-deploy quiescence"

if [[ "$RUN_RUNTIME_SMOKE" == true ]]; then
    runtime_smoke="$REPO_ROOT/../tools/pear_dev_smoke.py"
    if [[ ! -f "$runtime_smoke" ]]; then
        fail "runtime smoke helper not found: $runtime_smoke"
    fi
    if [[ -z "$RUNTIME_SMOKE_EVIDENCE" ]]; then
        RUNTIME_SMOKE_EVIDENCE="$REPO_ROOT/../var/pear-deploy-runtime-smoke-result.json"
    fi
    runtime_smoke_args=(
        "$runtime_smoke"
        --use-existing-server
        --site "https://$FQDN"
        --smoke-timeout "$RUNTIME_SMOKE_TIMEOUT"
        --evidence-json "$RUNTIME_SMOKE_EVIDENCE"
    )
    if [[ -n "$RUNTIME_SMOKE_ADMIN_EMAIL" ]]; then
        runtime_smoke_args+=(--admin-email "$RUNTIME_SMOKE_ADMIN_EMAIL")
    fi
    echo "Running opt-in Pear runtime smoke..."
    (cd "$REPO_ROOT/.." && python3 "${runtime_smoke_args[@]}")
else
    echo "Skipping opt-in Pear runtime smoke. Pass --run-runtime-smoke to post a real @Pear smoke request."
fi

echo "Migration tail:"
ssh_zulip "$REMOTE_PATH/.venv/bin/python3 $REMOTE_PATH/manage.py showmigrations zerver | tail"

echo "Service status:"
ssh_root "systemctl --no-pager --full status ${PEAR_DEPLOY_SERVICES[*]}" | sed -n "1,120p"

echo "pear-worker log tail:"
ssh_root 'journalctl -u pear-worker -n 40 --no-pager'

echo "pear-zulip-dev recent log tail:"
ssh_root 'journalctl -u pear-zulip-dev -n 80 --no-pager'

DEPLOYED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
{
    # Preserve the historical first line for humans and any lightweight
    # consumers, then identify every coordinated source that shaped the deploy.
    printf '%s %s\n' "$LOCAL_SHA" "$DEPLOYED_AT"
    printf 'record_version=2\n'
    printf 'parent_sha=%s\n' "$LOCAL_PARENT_SHA"
    printf 'zulip_sha=%s\n' "$LOCAL_SHA"
    printf 'zulip_terminal_sha=%s\n' "$LOCAL_TERMINAL_SHA"
    printf 'contract_version=%s\n' "$LOCAL_CONTRACT_VERSION"
    printf 'pear_skills_tree=%s\n' "$LOCAL_PEAR_SKILLS_TREE"
    printf 'workspace_helper_sha256=%s\n' "$LOCAL_WORKSPACE_HELPER_SHA"
    printf 'asset_mode=production-hashed\n'
    printf 'caddy_auth=basic-auth-required\n'
    printf 'deployed_at=%s\n' "$DEPLOYED_AT"
} | ssh_root "set -euo pipefail; umask 022; cat > $DEPLOY_RECORD_PATH.tmp; chown zulipdev:zulipdev $DEPLOY_RECORD_PATH.tmp; chmod 644 $DEPLOY_RECORD_PATH.tmp; mv -f $DEPLOY_RECORD_PATH.tmp $DEPLOY_RECORD_PATH"

ROLLBACK_READY=false
echo
echo "Deployed parent $LOCAL_PARENT_SHA / Zulip $LOCAL_SHA / terminal $LOCAL_TERMINAL_SHA at $DEPLOYED_AT"
echo "Recorded coordinated deployment metadata in $DEPLOY_RECORD_PATH"
