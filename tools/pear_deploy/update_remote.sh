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
DEPLOY_RECORD_PATH="/var/lib/pear/deployed-sha"
SNAPSHOT_ROOT="/home/zulipdev/pear-deploy-snapshots"
WORKSPACE_HELPER_RUNTIME="$SCRIPT_DIR/workspace_helper_runtime.py"
PEAR_BUILD_SWAP_BYTES=$((8 * 1024 * 1024 * 1024))
YES=false
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
PEAR_GEMINI_API_KEY="${PEAR_GEMINI_INTERACTIONS_API_KEY:-${GEMINI_API_KEY:-${GEMINI_KEY:-${GOOGLE_API_KEY:-${GOOGLE_GENAI_API_KEY:-}}}}}"

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
  --runtime-smoke-admin-email EMAIL
                   Admin/devlogin email for the required live runtime smoke.
  --runtime-smoke-timeout SECONDS
                   Runtime smoke timeout in seconds (default: 150).
  --runtime-smoke-evidence PATH
                   Write runtime smoke JSON evidence to PATH.
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
    # Production asset builds can saturate this small VM long enough that the
    # sshd process does not answer three consecutive keepalives.  Keep probing,
    # but tolerate ten minutes of scheduler pressure before abandoning a
    # guarded build; the remote command still exits normally on real failures.
    ssh -F "$SSH_CONFIG" \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=20 \
        pear-staging-root "$@"
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

detect_remote_pear_schema_state() {
    ssh_zulip "$REMOTE_PATH/.venv/bin/python3 $REMOTE_PATH/manage.py shell -c 'from django.db import connection; cursor = connection.cursor(); cursor.execute(\"SELECT name FROM django_migrations WHERE app = %s AND name LIKE %s\", [\"zerver\", \"08%pear%\"]); names = {row[0] for row in cursor.fetchall()}; accepted = [{\"0805_pear_runtime\"}, {\"0805_pear_runtime\", \"0806_pear_workspace_scope_casefold\"}]; print(\"current\" if names in accepted else \"empty\" if not names else \"unexpected\")'"
}

preflight_remote_workspace_helper() {
    echo "Preflight: inspecting the isolated Workspace helper without mutation..."
    ssh_root python3 - preflight \
        --destination "$PEAR_WORKSPACE_HELPER_PATH" \
        --expected-sha256 "$LOCAL_WORKSPACE_HELPER_SHA" \
        <"$WORKSPACE_HELPER_RUNTIME"
}

preflight_remote_visual_runtime() {
    echo "Preflight: verifying the out-of-process scientific visual runtime..."
    ssh_root bash -s -- --check <"$SCRIPT_DIR/provision_visual_runtime.sh"
}

preflight_remote_codex_sandbox() {
    echo "Preflight: verifying the host Codex sandbox runtime..."
    if ! ssh_root bash -s -- --check <"$SCRIPT_DIR/codex_sandbox_runtime.sh"; then
        fail "remote Codex sandbox cannot create a Bubblewrap user namespace; rerun bootstrap sandbox provisioning"
    fi
}

install_and_verify_remote_codex_sandbox() {
    echo "Installing and verifying the host Codex sandbox runtime..."
    ssh_root bash \
        "$REMOTE_TRUSTED_RUNTIME/tools/pear_deploy/codex_sandbox_runtime.sh" \
        --install
}

install_and_verify_remote_workspace_helper() {
    local remote_runtime="$REMOTE_TRUSTED_RUNTIME/tools/pear_deploy/workspace_helper_runtime.py"
    echo "Installing the committed isolated Workspace helper atomically..."
    ssh_root python3 "$remote_runtime" install \
        --repo-root "$REMOTE_TRUSTED_RUNTIME" \
        --destination "$PEAR_WORKSPACE_HELPER_PATH" \
        --expected-sha256 "$LOCAL_WORKSPACE_HELPER_SHA"
    echo "Verifying the installed Workspace helper and its isolation-user protocol..."
    ssh_root python3 "$remote_runtime" verify \
        --repo-root "$REMOTE_TRUSTED_RUNTIME" \
        --destination "$PEAR_WORKSPACE_HELPER_PATH" \
        --expected-sha256 "$LOCAL_WORKSPACE_HELPER_SHA" \
        --smoke-existing-isolation-user
}

install_remote_runtime_config() {
    local remote_command
    local command=(
        python3
        "$REMOTE_TRUSTED_RUNTIME/tools/pear_deploy/runtime_config.py"
        install
        --repo-root "$REMOTE_TRUSTED_RUNTIME"
        --fqdn "$FQDN"
    )
    printf -v remote_command '%q ' "${command[@]}"
    ssh_root "$remote_command"
}

install_remote_gemini_config() {
    if [[ -z "$PEAR_GEMINI_API_KEY" ]]; then
        fail "Gemini Interactions API key is missing from the local deploy environment"
    fi
    echo "Installing the shared Gemini Interactions key without printing it..."
    printf 'PEAR_GEMINI_INTERACTIONS_API_KEY=%s\n' "$PEAR_GEMINI_API_KEY" |
        ssh_root 'set -euo pipefail
env_path=/home/zulipdev/pear.env
tmp="$(mktemp /home/zulipdev/.pear.env.XXXXXX)"
trap '\''rm -f "$tmp"'\'' EXIT
grep -Ev '\''^PEAR_(ADDRESSING_ROUTER_GEMINI_API_KEY|GEMINI_INTERACTIONS_API_KEY)='\'' "$env_path" >"$tmp" || true
cat >>"$tmp"
chown zulipdev:zulipdev "$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$env_path"
trap - EXIT'
}

verify_remote_quiescence() {
    local phase="$1"
    local services="${2:-${PEAR_DEPLOY_SERVICES[*]}}"
    local queues="${3:-${PEAR_DEPLOY_QUEUES[*]}}"

    echo "$phase: Pear run health"
    if ! ssh_zulip "$REMOTE_PATH/tools/pear-health-report --max-active-runs 0 --max-stale-active-runs 0"; then
        fail "$phase failed: Pear has active or stale work"
    fi

    echo "$phase: service health"
    if ! ssh_root "systemctl is-active $services"; then
        fail "$phase failed: one or more deploy-managed services are inactive"
    fi

    echo "$phase: queue quiescence"
    if ! ssh_root "rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers" |
        validate_pear_deploy_queue_snapshot "$queues"; then
        fail "$phase failed: one or more deploy-managed queues are busy, missing, or unconsumed"
    fi
}

wait_for_remote_drain() {
    local attempt
    local worker_services="${1:-${PEAR_DEPLOY_WORKER_SERVICES[*]}}"
    local queues="${2:-${PEAR_DEPLOY_QUEUES[*]}}"
    echo "Maintenance drain: waiting for Pear runs and queues to become idle..."
    for attempt in $(seq 1 60); do
        if ssh_root "systemctl is-active $worker_services" >/dev/null 2>&1 &&
            ssh_zulip "$REMOTE_PATH/tools/pear-health-report --max-active-runs 0 --max-stale-active-runs 0" >/dev/null 2>&1 &&
            ssh_root "rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers" 2>/dev/null |
                validate_pear_deploy_queue_snapshot "$queues" >/dev/null; then
            echo "Maintenance drain: Pear runs and queues are idle."
            return 0
        fi
        sleep 2
    done

    ssh_root "systemctl is-active $worker_services" || true
    ssh_zulip "$REMOTE_PATH/tools/pear-health-report --max-active-runs 0 --max-stale-active-runs 0" || true
    ssh_root "rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers" |
        validate_pear_deploy_queue_snapshot "$queues" || true
    fail "maintenance drain did not become idle within 120 seconds"
}

verify_remote_stopped_quiescence() {
    local queues="${1:-${PEAR_DEPLOY_QUEUES[*]}}"
    echo "Stopped-state gate: Pear run health"
    if ! ssh_zulip "$REMOTE_PATH/tools/pear-health-report --max-active-runs 0 --max-stale-active-runs 0"; then
        fail "stopped-state gate failed: Pear has active or stale work"
    fi
    echo "Stopped-state gate: queue emptiness"
    if ! ssh_root "rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers" |
        validate_pear_deploy_queue_empty_snapshot "$queues"; then
        fail "stopped-state gate failed: one or more deploy-managed queues are busy or missing"
    fi
}

verify_remote_readiness() {
    local phase="$1"
    local services="${2:-${PEAR_DEPLOY_SERVICES[*]}}"
    local queues="${3:-${PEAR_DEPLOY_QUEUES[*]}}"
    local queue_snapshot

    echo "$phase: Pear stale-run health"
    if ! ssh_zulip "$REMOTE_PATH/tools/pear-health-report --allow-active-runs --max-stale-active-runs 0"; then
        fail "$phase failed: Pear has stale work"
    fi

    echo "$phase: service health"
    if ! ssh_root "systemctl is-active $services"; then
        fail "$phase failed: one or more deploy-managed services are inactive"
    fi

    echo "$phase: queue readiness"
    queue_snapshot="$(
        ssh_root "rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers"
    )"
    if ! printf '%s\n' "$queue_snapshot" |
        validate_pear_deploy_queue_ready_snapshot "$queues"; then
        fail "$phase failed: one or more deploy-managed queues are missing or unconsumed"
    fi
    if ! printf '%s\n' "$queue_snapshot" | validate_pear_worker_pool_snapshot; then
        fail "$phase failed: the two-slot Pear worker pool is not fully connected"
    fi

    echo "$phase: worker delivery policy"
    verify_remote_pear_worker_consumer_timeout_policy
}

configure_predeploy_runtime_expectations() {
    local index
    local service
    local queue
    local unit_path

    PREDEPLOY_WORKER_SERVICES=()
    PREDEPLOY_QUEUES=()
    for index in "${!PEAR_DEPLOY_WORKER_SERVICES[@]}"; do
        service="${PEAR_DEPLOY_WORKER_SERVICES[$index]}"
        queue="${PEAR_DEPLOY_QUEUES[$index]}"
        if ssh_root "systemctl cat $service >/dev/null 2>&1"; then
            PREDEPLOY_WORKER_SERVICES+=("$service")
            PREDEPLOY_QUEUES+=("$queue")
            continue
        fi

        unit_path="tools/pear_deploy/runtime/$service.service"
        if git -C "$REPO_ROOT" cat-file -e "$PREV_SHA:$unit_path" 2>/dev/null ||
            ! git -C "$REPO_ROOT" cat-file -e "$LOCAL_SHA:$unit_path" 2>/dev/null; then
            fail "pre-deploy service $service is missing and is not newly introduced by this deploy"
        fi
        echo "Preflight: newly introduced service $service and queue $queue are absent as expected before cutover."
    done
    PREDEPLOY_SERVICES=("${PREDEPLOY_WORKER_SERVICES[@]}" pear-zulip-dev)
}

verify_local_cutover_source() {
    local parent_head
    local zulip_head
    local terminal_head
    local parent_status
    local zulip_status
    local terminal_status
    local untracked_source
    local skills_status

    parent_head="$(git -C "$PEAR_ROOT" rev-parse HEAD)"
    zulip_head="$(git -C "$REPO_ROOT" rev-parse HEAD)"
    terminal_head="$(git -C "$LOCAL_TERMINAL_ROOT" rev-parse HEAD)"
    parent_status="$(git -C "$PEAR_ROOT" status --porcelain --untracked-files=no)"
    zulip_status="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)"
    terminal_status="$(git -C "$LOCAL_TERMINAL_ROOT" status --porcelain --untracked-files=all)"
    untracked_source="$(local_untracked_source_files)"
    skills_status="$(local_pear_skills_status)"
    if [[
        "$parent_head" != "$LOCAL_PARENT_SHA" ||
        "$zulip_head" != "$LOCAL_SHA" ||
        "$terminal_head" != "$LOCAL_TERMINAL_SHA" ||
        -n "$parent_status" ||
        -n "$zulip_status" ||
        -n "$terminal_status" ||
        -n "$untracked_source" ||
        -n "$skills_status" ||
        "$(git -C "$PEAR_ROOT" rev-parse HEAD:zulip)" != "$LOCAL_SHA" ||
        "$(git -C "$PEAR_ROOT" rev-parse HEAD:zulip-terminal)" != "$LOCAL_TERMINAL_SHA"
    ]]; then
        fail "local coordinated source changed after preflight; refusing cutover"
    fi
}

verify_remote_cutover_source() {
    local remote_head
    local tracked_status
    local untracked_source
    remote_head="$(ssh_zulip "git rev-parse HEAD")"
    tracked_status="$(ssh_zulip "git status --porcelain --untracked-files=no")"
    untracked_source="$(remote_untracked_source_files)"
    if [[ "$remote_head" != "$PREV_SHA" ]]; then
        fail "remote HEAD changed after preflight"
    fi
    if [[ -n "$tracked_status" ]] && ! remote_dirty_tree_matches_local_head; then
        echo "$tracked_status" >&2
        fail "remote tracked source changed after preflight"
    fi
    if [[ -n "$untracked_source" ]]; then
        echo "$untracked_source" >&2
        fail "remote untracked source appeared after preflight"
    fi
}

verify_remote_deployed_source_clean() {
    local remote_head
    local tracked_status
    local untracked_source
    remote_head="$(ssh_zulip_clean "git rev-parse HEAD")"
    tracked_status="$(ssh_zulip_clean "git status --porcelain --untracked-files=no")"
    untracked_source="$(remote_untracked_source_files)"
    if [[ "$remote_head" != "$LOCAL_SHA" ]]; then
        fail "deployed remote HEAD is $remote_head, expected $LOCAL_SHA"
    fi
    if [[ -n "$tracked_status" ]]; then
        echo "$tracked_status" >&2
        fail "deployed remote tracked tree is dirty"
    fi
    if [[ -n "$untracked_source" ]]; then
        echo "$untracked_source" >&2
        fail "deployed remote source tree has unexpected untracked files"
    fi
    echo "Post-deploy source gate: remote HEAD and tracked/untracked source are clean."
}

verify_remote_disk_capacity() {
    local remote_asset_build_dir="/var/lib/pear-builds/asset-build-${LOCAL_SHA:0:12}"
    local runtime_path
    local build_swap_bytes="$PEAR_BUILD_SWAP_BYTES"
    if [[ "$ASSET_BUILD_REQUIRED" != true ]]; then
        build_swap_bytes=0
    fi
    local -a capacity_command=(
        python3
        -
        --remote-path "$REMOTE_PATH"
        --git-common-dir "$REMOTE_GIT_COMMON_DIR"
        --pear-skills-dir "$REMOTE_PEAR_SKILLS_DIR"
        --snapshot-root "$SNAPSHOT_ROOT"
        --asset-build-dir "$remote_asset_build_dir"
        --build-swap-bytes "$build_swap_bytes"
    )
    if [[ "$ASSET_BUILD_REQUIRED" != true ]]; then
        capacity_command+=(--reuse-assets)
    fi
    for runtime_path in "${PEAR_DEPLOY_RUNTIME_CONFIG_PATHS[@]}"; do
        capacity_command+=(--runtime-path "$runtime_path")
    done
    echo "Preflight: estimating free space for rollback and selected asset strategy..."
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
    REMOTE_ASSET_BUILD_DIR="/var/lib/pear-builds/asset-build-${LOCAL_SHA:0:12}"
    local build_log="$REMOTE_ASSET_BUILD_DIR/.pear-production-build.log"
    local build_pid="$REMOTE_ASSET_BUILD_DIR/.pear-production-build.pid"
    echo "Staging source for an isolated production asset build..."
    ssh_root "install -d -m 711 -o root -g root /var/lib/pear-builds; \
        rm -rf $REMOTE_ASSET_BUILD_DIR; \
        install -d -m 750 -o zulipdev -g zulipdev $REMOTE_ASSET_BUILD_DIR"
    # The remote checkout is clean and remains read-only throughout the build.
    # Link unchanged files from it locally, so the constrained host link only
    # carries the actual source diff rather than every fixture and static asset.
    # All paths written by the production build are excluded below and seeded
    # separately, so no build output can share an inode with the live tree.
    rsync -az --delete \
        --link-dest="$REMOTE_PATH" \
        --exclude '/analytics/tests/' \
        --exclude '/corporate/tests/' \
        --exclude '/confirmation/tests/' \
        --exclude '/docs/' \
        --exclude '/api_docs/' \
        --exclude '/web/tests/' \
        --exclude '/zerver/tests/' \
        --exclude '/zilencer/tests/' \
        "${PEAR_DEPLOY_RSYNC_EXCLUDES[@]}" \
        --exclude '/.git' \
        -e "ssh -F $SSH_CONFIG" \
        "$REPO_ROOT/" \
        "pear-staging:$REMOTE_ASSET_BUILD_DIR/"
    ssh_root "sudo -Hu zulipdev ln -s $REMOTE_PATH/.venv $REMOTE_ASSET_BUILD_DIR/.venv; \
        sudo -Hu zulipdev ln -s $REMOTE_PATH/zproject/dev-secrets.conf $REMOTE_ASSET_BUILD_DIR/zproject/dev-secrets.conf; \
        sudo -Hu zulipdev bash -lc 'umask 022 && mkdir -p \
            $REMOTE_ASSET_BUILD_DIR/var/log \
            $REMOTE_ASSET_BUILD_DIR/static/generated'"
    echo "Building production assets without touching the live asset graph..."
    ssh_root bash -s -- "$REMOTE_ASSET_BUILD_DIR" "$build_log" "$build_pid" <<'REMOTE'
set -euo pipefail
BUILD_ROOT="$1"
BUILD_LOG="$2"
BUILD_PID="$3"
sudo -Hu zulipdev bash -s -- "$BUILD_ROOT" "$BUILD_LOG" "$BUILD_PID" <<'BUILD'
set -euo pipefail
BUILD_ROOT="$1"
BUILD_LOG="$2"
BUILD_PID="$3"
set -a
. /home/zulipdev/pear.env
set +a
unset PYTHONHOME PYTHONPATH
cd "$BUILD_ROOT"
export PATH="$BUILD_ROOT/.venv/bin:$PATH"
VENV_ROOT="$(readlink -f "$BUILD_ROOT/.venv")"
if [[ ! -x "$VENV_ROOT/bin/python3" ]]; then
    echo "isolated build venv is unavailable: $VENV_ROOT" >&2
    exit 1
fi
umask 022
nohup env \
    PATH="$VENV_ROOT/bin:$PATH" \
    VIRTUAL_ENV="$VENV_ROOT" \
    PYTHONNOUSERSITE=1 \
    bash -c "set -e; ZULIP_DEV_PRODUCTION_ASSETS=1 \"$VENV_ROOT/bin/python3\" -E ./tools/update-prod-static && \"$VENV_ROOT/bin/python3\" -E tools/pear_deploy/materialize_emoji_assets.py --build-root . && \"$VENV_ROOT/bin/python3\" -E tools/pear_deploy/production_assets.py --repo-root . && for generated_path in prod-static static/generated static/webpack-bundles static/images/landing-page/hello/generated web/generated locale; do [ ! -e \"\$generated_path\" ] || find \"\$generated_path\" -exec chmod go-w {} +; done && printf '\\nPEAR_PRODUCTION_ASSETS_OK\\n'" \
    >"$BUILD_LOG" 2>&1 < /dev/null &
printf '%s\n' "$!" >"$BUILD_PID"
BUILD
REMOTE
    echo "Waiting for the detached production asset build..."
    local build_state
    local attempt
    for attempt in $(seq 1 450); do
        build_state="$(ssh_root bash -s -- "$build_log" "$build_pid" <<'REMOTE'
set -euo pipefail
BUILD_LOG="$1"
BUILD_PID="$2"
if [[ ! -s "$BUILD_PID" ]]; then
    echo "MISSING_PID"
    exit 0
fi
pid="$(<"$BUILD_PID")"
if kill -0 "$pid" 2>/dev/null; then
    echo "RUNNING"
    exit 0
fi
if grep -q '^PEAR_PRODUCTION_ASSETS_OK$' "$BUILD_LOG"; then
    echo "COMPLETE"
    exit 0
fi
echo "FAILED"
tail -80 "$BUILD_LOG" >&2 || true
exit 1
REMOTE
        )"
        case "$build_state" in
            COMPLETE)
                return 0
                ;;
            RUNNING)
                sleep 2
                ;;
            MISSING_PID)
                fail "detached production asset build did not publish its PID"
                ;;
            *)
                fail "detached production asset build failed"
                ;;
        esac
    done
    fail "detached production asset build did not finish within 900 seconds"
}

enable_remote_build_swap() {
    REMOTE_BUILD_SWAP_PATH="/var/lib/pear-builds/asset-build-${LOCAL_SHA:0:12}-$$.swap"
    echo "Enabling bounded temporary swap for the isolated production build..."
    ssh_root bash -s -- "$REMOTE_BUILD_SWAP_PATH" "$PEAR_BUILD_SWAP_BYTES" <<'REMOTE'
set -euo pipefail
SWAP_PATH="$1"
SWAP_BYTES="$2"
if [[ ! "$SWAP_PATH" =~ ^/var/lib/pear-builds/asset-build-[0-9a-f]{12}-[0-9]+\.swap$ ]]; then
    echo "invalid temporary build swap path" >&2
    exit 1
fi
if [[ ! "$SWAP_BYTES" =~ ^[0-9]+$ ]] || (( SWAP_BYTES < 1073741824 )); then
    echo "invalid temporary build swap size" >&2
    exit 1
fi
if [[ -e "$SWAP_PATH" ]]; then
    echo "temporary build swap path already exists" >&2
    exit 1
fi
cleanup_failed_swap() {
    rm -f -- "$SWAP_PATH"
}
trap cleanup_failed_swap EXIT
umask 077
fallocate -l "$SWAP_BYTES" "$SWAP_PATH"
chmod 0600 "$SWAP_PATH"
mkswap --quiet "$SWAP_PATH"
swapon "$SWAP_PATH"
trap - EXIT
REMOTE
    REMOTE_BUILD_SWAP_ACTIVE=true
}

disable_remote_build_swap() {
    if [[ "$REMOTE_BUILD_SWAP_ACTIVE" != true ]]; then
        return
    fi
    echo "Removing temporary production-build swap..."
    ssh_root bash -s -- "$REMOTE_BUILD_SWAP_PATH" <<'REMOTE'
set -euo pipefail
SWAP_PATH="$1"
if swapon --noheadings --show=NAME | grep -Fxq -- "$SWAP_PATH"; then
    swapoff "$SWAP_PATH"
fi
rm -f -- "$SWAP_PATH"
REMOTE
    REMOTE_BUILD_SWAP_ACTIVE=false
}

cut_over_remote_production_assets() {
    local build_root="${1:-$REMOTE_ASSET_BUILD_DIR}"
    echo "Cutting over the validated production asset graph..."
    ssh_root bash -s -- "$build_root" "$REMOTE_PATH" <<'REMOTE'
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
git -C "$REMOTE_PATH" checkout-index --force -- \
    static/generated/README.md \
    web/generated/README.md
rsync -a --include '*/' --include '*.mo' --include 'mobile.json' --exclude '*' \
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
        "${assets[0]}" \
        "${assets[1]}" \
        "${assets[2]}"; then
        fail "public Caddy production-runtime verification failed"
    fi

    if ssh_root "pgrep -af '[t]ools/webpack|[t]ornado\.autoreload'"; then
        fail "development webpack/autoreload process is running in production-assets mode"
    fi
}

SNAPSHOT_DIR=""
REMOTE_ASSET_BUILD_DIR=""
REMOTE_FROZEN_ASSET_DIR=""
REMOTE_TRUSTED_RUNTIME=""
REMOTE_BUILD_SWAP_PATH=""
REMOTE_BUILD_SWAP_ACTIVE=false
ROLLBACK_READY=false
ROLLBACK_DONE=false
SERVICE_RESTART_REQUIRED=false
ASSET_BUILD_REQUIRED=true
ASSET_REBUILD_TRIGGER=""
ASSET_SOURCE_SHA=""

classify_production_asset_requirement() {
    local policy_status
    if ASSET_REBUILD_TRIGGER="$(
        git -C "$REPO_ROOT" diff --name-only --no-renames -z "$PREV_SHA..$LOCAL_SHA" |
            python3 "$SCRIPT_DIR/asset_reuse_policy.py"
    )"; then
        ASSET_BUILD_REQUIRED=false
        return
    else
        policy_status=$?
    fi
    if [[ "$policy_status" -ne 10 || -z "$ASSET_REBUILD_TRIGGER" ]]; then
        fail "production asset reuse policy could not classify the source delta"
    fi
    ASSET_BUILD_REQUIRED=true
}

load_remote_asset_source_sha() {
    local recorded_sha
    recorded_sha="$(
        ssh_root "sed -n 's/^asset_source_zulip_sha=//p' $DEPLOY_RECORD_PATH | tail -1"
    )"
    if [[ -z "$recorded_sha" ]]; then
        # Records before the change-aware deploy lane always rebuilt assets.
        recorded_sha="$PREV_SHA"
    fi
    if [[ ! "$recorded_sha" =~ ^[0-9a-f]{40}$ ]]; then
        fail "recorded production asset source SHA is invalid"
    fi
    if ! git -C "$REPO_ROOT" cat-file -e "$recorded_sha^{commit}" 2>/dev/null; then
        fail "recorded production asset source SHA is absent locally"
    fi
    if ! git -C "$REPO_ROOT" merge-base --is-ancestor "$recorded_sha" "$PREV_SHA"; then
        fail "recorded production asset source SHA is not an ancestor of the live source"
    fi
    ASSET_SOURCE_SHA="$recorded_sha"
}

verify_reusable_remote_production_assets() {
    echo "Preflight: validating the production asset graph selected for reuse..."
    ssh_zulip_clean "$REMOTE_PATH/.venv/bin/python3 \
        $REMOTE_PATH/tools/pear_deploy/production_assets.py --repo-root $REMOTE_PATH"
}

rollback_on_error() {
    local status=$?
    if [[ $status -ne 0 && "$ROLLBACK_READY" == true && "$ROLLBACK_DONE" == false ]]; then
        echo
        echo "Update failed after snapshot; rolling back from $SNAPSHOT_DIR" >&2
        set +e
        rollback_command="bash -s --"
        rollback_args=(
            "$SNAPSHOT_DIR"
            "$REMOTE_PATH"
            "$REMOTE_GIT_COMMON_DIR"
            "$REMOTE_PEAR_SKILLS_DIR"
            "${PREDEPLOY_SERVICES[*]}"
            "${PREDEPLOY_QUEUES[*]}"
            "${#PEAR_DEPLOY_RUNTIME_CONFIG_PATHS[@]}"
            "${PEAR_DEPLOY_RUNTIME_CONFIG_PATHS[@]}"
            "${#PEAR_DEPLOY_RSYNC_EXCLUDES[@]}"
            "${PEAR_DEPLOY_RSYNC_EXCLUDES[@]}"
        )
        for rollback_arg in "${rollback_args[@]}"; do
            rollback_command+=" $(printf '%q' "$rollback_arg")"
        done
        ssh_root "$rollback_command" <<'REMOTE'
# Optional pre-cutover entries may be absent on older snapshots. Rollback is
# the recovery path, so decode the positional vector without nounset aborts.
set -eo pipefail
SNAPSHOT_DIR="$1"
REMOTE_PATH="$2"
REMOTE_GIT_COMMON_DIR="$3"
REMOTE_PEAR_SKILLS_DIR="$4"
PREDEPLOY_SERVICES="$5"
PREDEPLOY_QUEUES="$6"
RUNTIME_CONFIG_COUNT="$7"
shift 7
RUNTIME_CONFIG_PATHS=("${@:1:RUNTIME_CONFIG_COUNT}")
shift "$RUNTIME_CONFIG_COUNT"
RSYNC_EXCLUDE_COUNT="$1"
shift
RSYNC_EXCLUDES=("$@")
if [[ "${#RSYNC_EXCLUDES[@]}" -ne "$RSYNC_EXCLUDE_COUNT" ]]; then
    echo "invalid rollback rsync exclude argument count" >&2
    exit 1
fi

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
sudo -Hu zulipdev bash -lc "cd '$REMOTE_PATH' && set -a && . /home/zulipdev/pear.env && set +a && .venv/bin/python3 manage.py shell" <<'PY'
from django.db import transaction
from django.utils.timezone import now

from zerver.lib.pear_worker import settle_orphaned_pear_run_message
from zerver.models.bots import PearWorkerRun

rollback_error = "Run interrupted by deployment rollback."
with transaction.atomic():
    runs = list(
        PearWorkerRun.objects.select_for_update()
        .select_related("bot_profile")
        .filter(status__in=(PearWorkerRun.QUEUED, PearWorkerRun.RUNNING))
    )
    finished_at = now()
    for run in runs:
        run.status = PearWorkerRun.FAILED
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

rollback_ready=false
for _ in $(seq 1 30); do
    if systemctl is-active $PREDEPLOY_SERVICES >/dev/null 2>&1 &&
        sudo -Hu zulipdev bash -lc "cd '$REMOTE_PATH' && set -a && . /home/zulipdev/pear.env && set +a && tools/pear-health-report --max-active-runs 0 --max-stale-active-runs 0" >/dev/null 2>&1 &&
        rabbitmqctl list_queues -p / name messages_ready messages_unacknowledged consumers 2>/dev/null |
            validate_predeploy_queues; then
        rollback_ready=true
        break
    fi
    sleep 1
done
if [[ "$rollback_ready" != true ]]; then
    echo "restored Pear runtime did not become healthy after rollback" >&2
    systemctl is-active $PREDEPLOY_SERVICES || true
    sudo -Hu zulipdev bash -lc "cd '$REMOTE_PATH' && set -a && . /home/zulipdev/pear.env && set +a && tools/pear-health-report --max-active-runs 0 --max-stale-active-runs 0" || true
    rabbitmqctl list_queues -p / name messages_ready messages_unacknowledged consumers |
        validate_predeploy_queues || true
    exit 1
fi
REMOTE
        local rollback_status=$?
        set -e
        if [[ $rollback_status -eq 0 ]]; then
            ROLLBACK_DONE=true
            SERVICE_RESTART_REQUIRED=false
            echo "Rollback restored the pre-deploy snapshot and restarted Pear services." >&2
        else
            echo "Rollback failed; inspect $SNAPSHOT_DIR on the box." >&2
        fi
    fi
    if [[
        $status -ne 0 &&
        "$ROLLBACK_READY" != true &&
        "$SERVICE_RESTART_REQUIRED" == true
    ]]; then
        echo "Update failed before a rollback snapshot was ready; restarting the pre-deploy services." >&2
        if ssh_root "systemctl restart caddy ${PREDEPLOY_SERVICES[*]}"; then
            SERVICE_RESTART_REQUIRED=false
        else
            echo "Pre-snapshot service recovery failed; inspect the host immediately." >&2
        fi
    fi
    if [[ $status -ne 0 && "$ROLLBACK_READY" != true && -n "$SNAPSHOT_DIR" ]]; then
        ssh_root "rm -rf -- $SNAPSHOT_DIR" >/dev/null 2>&1 || true
    fi
    if [[ -n "$REMOTE_ASSET_BUILD_DIR" ]]; then
        ssh_root "rm -rf $REMOTE_ASSET_BUILD_DIR" >/dev/null 2>&1 || true
    fi
    if [[ "$REMOTE_BUILD_SWAP_ACTIVE" == true ]]; then
        disable_remote_build_swap >/dev/null 2>&1 || true
    fi
    if [[ -n "$REMOTE_FROZEN_ASSET_DIR" ]]; then
        ssh_root "rm -rf $REMOTE_FROZEN_ASSET_DIR" >/dev/null 2>&1 || true
    fi
    if [[ -n "$REMOTE_TRUSTED_RUNTIME" ]]; then
        remove_remote_pear_deploy_bundle "$REMOTE_TRUSTED_RUNTIME" >/dev/null 2>&1 || true
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

configure_predeploy_runtime_expectations

echo "Preflight: remote tracked/untracked source tree is clean and not ahead of local pear-main."
REMOTE_PEAR_SCHEMA_STATE="$(detect_remote_pear_schema_state | tail -n 1)"
case "$REMOTE_PEAR_SCHEMA_STATE" in
    current)
        echo "Preflight: Pear database already uses the current migration baseline."
        ;;
    empty)
        echo "Preflight: Pear database has no applied Pear migration; normal migrate will create it."
        ;;
    unexpected)
        fail "remote database contains a superseded or mixed Pear migration baseline"
        ;;
    *)
        fail "could not classify remote Pear migration baseline: $REMOTE_PEAR_SCHEMA_STATE"
        ;;
esac
classify_production_asset_requirement
if [[ "$ASSET_BUILD_REQUIRED" == true ]]; then
    ASSET_SOURCE_SHA="$LOCAL_SHA"
    echo "Preflight: production assets require a rebuild (trigger: $ASSET_REBUILD_TRIGGER)."
else
    load_remote_asset_source_sha
    echo "Preflight: backend-only delta can reuse production assets from $ASSET_SOURCE_SHA."
    verify_reusable_remote_production_assets
fi
if [[ "$PREV_SHA" != "$LOCAL_SHA" ]]; then
    echo
    echo "Deploy delta:"
    git -C "$REPO_ROOT" diff --stat "$PREV_SHA..$LOCAL_SHA" -- \
        zproject/default_settings.py \
        zerver/lib/pear.py \
        zerver/lib/pear_gate.py \
        zerver/lib/pear_agent.py \
        zerver/lib/pear_artifact.py \
        zerver/lib/pear_artifact_contract.py \
        zerver/lib/pear_worker.py \
        zerver/lib/pear_workspace_index.py \
        zerver/lib/pear_workspace_io.py \
        zerver/lib/pear_workspace_resources.py \
        zerver/worker/pear_worker.py \
        zerver/worker/pear_addressing.py \
        zerver/worker/pear_presentation.py \
        zerver/views/pear.py \
        zerver/views/pear_workspace.py \
        zerver/views/users.py \
        zerver/models/bots.py \
        zerver/migrations \
        zerver/tests/test_service_bot_system.py \
        zerver/tests/test_pear.py \
        zerver/tests/test_pear_artifact.py \
        zerver/tests/test_pear_generated_vector.py \
        zerver/tests/test_pear_endpoint.py \
        web/src \
        web/styles \
        web/templates \
        tools/pear-health-report \
        tools/pear_deploy || true
else
    echo "Deploy delta: remote already has this HEAD."
fi

run_local_compat

verify_remote_quiescence \
    "Preflight quiescence" \
    "${PREDEPLOY_SERVICES[*]}" \
    "${PREDEPLOY_QUEUES[*]}"
preflight_remote_workspace_helper
preflight_remote_visual_runtime
preflight_remote_codex_sandbox
verify_remote_disk_capacity

if [[ "$YES" != true ]]; then
    echo
    echo "Dry run complete. No snapshot, rsync, migration, restart, or record write was performed."
    echo "Re-run with --yes to deploy $LOCAL_SHA to https://$FQDN."
    exit 0
fi

echo
if [[ "$ASSET_BUILD_REQUIRED" == true ]]; then
    enable_remote_build_swap
    build_remote_production_assets
    disable_remote_build_swap
else
    echo "Reusing the validated production asset graph; skipping swap and webpack build."
fi

echo "Staging privileged deployment tooling from the pinned local commit..."
REMOTE_TRUSTED_RUNTIME="$(stage_remote_pear_deploy_bundle "$REPO_ROOT" "$LOCAL_SHA")"

echo "Stopping app ingress before the final drain..."
SERVICE_RESTART_REQUIRED=true
ssh_root "systemctl stop pear-zulip-dev"
wait_for_remote_drain "${PREDEPLOY_WORKER_SERVICES[*]}" "${PREDEPLOY_QUEUES[*]}"

echo "Stopping queue workers after the drain..."
ssh_root "systemctl stop ${PREDEPLOY_WORKER_SERVICES[*]}"
verify_remote_stopped_quiescence "${PREDEPLOY_QUEUES[*]}"
verify_local_cutover_source
verify_remote_cutover_source

if [[ "$ASSET_BUILD_REQUIRED" == true ]]; then
    echo "Copying the isolated build into a no-follow root-owned asset tree..."
    REMOTE_FROZEN_ASSET_DIR="$(
        ssh_root python3 "$REMOTE_TRUSTED_RUNTIME/tools/pear_deploy/freeze_asset_build.py" \
            --source-root "$REMOTE_ASSET_BUILD_DIR"
    )"
    if [[ ! "$REMOTE_FROZEN_ASSET_DIR" =~ ^/root/pear-frozen-assets\.[A-Za-z0-9_-]+$ ]]; then
        fail "asset freezer returned an invalid root-owned path"
    fi
    ssh_root python3 "$REMOTE_TRUSTED_RUNTIME/tools/pear_deploy/production_assets.py" \
        --repo-root "$REMOTE_FROZEN_ASSET_DIR"
fi

SNAPSHOT_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SNAPSHOT_DIR="$SNAPSHOT_ROOT/zulip-$SNAPSHOT_STAMP-${PREV_SHA:0:12}"
echo "Snapshotting the drained remote tree to $SNAPSHOT_DIR"
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
    ssh -F "$SSH_CONFIG" pear-staging "install -d -m 755 $REMOTE_GIT_COMMON_DIR"
    rsync -az --delete \
        -e "ssh -F $SSH_CONFIG" \
        "$LOCAL_GIT_COMMON_DIR/" \
        "pear-staging:$REMOTE_GIT_COMMON_DIR/"
fi

echo "Syncing checkout to $REMOTE_PATH..."
rsync -az --delete \
    "${PEAR_DEPLOY_RSYNC_EXCLUDES[@]}" \
    -e "ssh -F $SSH_CONFIG" \
    "$REPO_ROOT/" \
    "pear-staging:$REMOTE_PATH/"

echo "Syncing Pear Codex skills to $REMOTE_PEAR_SKILLS_DIR..."
ssh -F "$SSH_CONFIG" pear-staging "install -d -m 755 $REMOTE_PEAR_SKILLS_DIR"
rsync -az --delete \
    -e "ssh -F $SSH_CONFIG" \
    "$LOCAL_PEAR_SKILLS_DIR/" \
    "pear-staging:$REMOTE_PEAR_SKILLS_DIR/"

if [[ "$ASSET_BUILD_REQUIRED" == true ]]; then
    cut_over_remote_production_assets "$REMOTE_FROZEN_ASSET_DIR"
else
    echo "Keeping the validated production asset graph from $ASSET_SOURCE_SHA."
fi

ssh -F "$SSH_CONFIG" pear-staging "git config --global --add safe.directory $REMOTE_PATH"

install_and_verify_remote_workspace_helper
install_and_verify_remote_codex_sandbox

echo "Installing public Caddy and production-assets service configuration..."
install_remote_runtime_config
ssh_root python3 "$REMOTE_TRUSTED_RUNTIME/tools/pear_deploy/runtime_config.py" verify \
    --repo-root "$REMOTE_TRUSTED_RUNTIME" \
    --fqdn "$FQDN"
install_remote_gemini_config
install_remote_pear_worker_consumer_timeout_policy
ssh_root "systemctl daemon-reload"
ssh_root "systemctl enable ${PEAR_DEPLOY_SERVICES[*]}"

echo "Applying zerver migrations..."
ssh_zulip "$REMOTE_PATH/.venv/bin/python3 $REMOTE_PATH/manage.py migrate zerver --noinput"

echo "Restarting Pear services..."
ssh_root "systemctl restart caddy ${PEAR_DEPLOY_SERVICES[*]}"
SERVICE_RESTART_REQUIRED=false
wait_for_remote_pear_worker_consumer_timeout_queues
wait_for_remote_pear_worker_pool_topology

echo "Waiting for the public Caddy app route to serve..."
SITE_CODE=""
for _ in $(seq 1 60); do
    SITE_CODE="$(curl \
        --silent --show-error --location --max-redirs 3 --max-time 10 --output /dev/null \
        --write-out '%{http_code}' --url "https://$FQDN/devlogin" || true)"
    if [[ "$SITE_CODE" == "200" ]]; then
        break
    fi
    sleep 5
done
if [[ "$SITE_CODE" != "200" ]]; then
    fail "public app route through Caddy did not become healthy; last HTTP code: ${SITE_CODE:-none}"
fi
verify_remote_production_assets

echo "Running Pear backend tests on the box..."
ssh_zulip_clean "./tools/test-backend \
    zerver.tests.test_pear \
    zerver.tests.test_pear_artifact \
    zerver.tests.test_pear_generated_vector \
    zerver.tests.test_pear_endpoint \
    zerver.tests.test_pear_presentation \
    zerver.tests.test_pear_workspace_io \
    zerver.tests.test_pear_workspace_index \
    zerver.tests.test_pear_workspace_resources \
    zerver.tests.test_submessage"

# Ingress is live again at this point. New ambient runs are ordinary traffic,
# not evidence of an unsafe cutover; only the pre-cutover drain must be empty.
# The post-cutover gate therefore requires healthy consumers and no stale work
# while allowing fresh messages and runs to be in flight.
verify_remote_readiness "Post-deploy readiness"
verify_remote_deployed_source_clean

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
echo "Running required Pear runtime smoke..."
(cd "$REPO_ROOT/.." && python3 "${runtime_smoke_args[@]}")

echo "Migration tail:"
ssh_zulip "$REMOTE_PATH/.venv/bin/python3 $REMOTE_PATH/manage.py showmigrations zerver | tail"

echo "Service status:"
ssh_root "systemctl --no-pager --full status ${PEAR_DEPLOY_SERVICES[*]}" | sed -n "1,120p"

echo "Pear worker pool log tail:"
ssh_root 'journalctl -u pear-worker -u pear-worker-1 -n 80 --no-pager'

echo "pear-addressing log tail:"
ssh_root 'journalctl -u pear-addressing -n 40 --no-pager'

echo "pear-presentation log tail:"
ssh_root 'journalctl -u pear-presentation -n 40 --no-pager'

echo "pear-zulip-dev recent log tail:"
ssh_root 'journalctl -u pear-zulip-dev -n 80 --no-pager'

DEPLOYED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
ssh_root 'install -d -m 755 -o root -g root /var/lib/pear'
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
    printf 'asset_source_zulip_sha=%s\n' "$ASSET_SOURCE_SHA"
    printf 'caddy_auth=public-devlogin\n'
    printf 'deployed_at=%s\n' "$DEPLOYED_AT"
} | ssh_root python3 \
    "$REMOTE_TRUSTED_RUNTIME/tools/pear_deploy/deployment_record.py" \
    --destination "$DEPLOY_RECORD_PATH"
ssh_root 'rm -f -- /home/zulipdev/pear-deployed-sha'

ROLLBACK_READY=false
echo
echo "Deployed parent $LOCAL_PARENT_SHA / Zulip $LOCAL_SHA / terminal $LOCAL_TERMINAL_SHA at $DEPLOYED_AT"
echo "Recorded coordinated deployment metadata in $DEPLOY_RECORD_PATH"
