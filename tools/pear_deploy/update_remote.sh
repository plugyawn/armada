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
LOCAL_PEAR_SKILLS_DIR="$PEAR_ROOT/pear/skills"
REMOTE_PEAR_DIR="/home/zulipdev/pear"
REMOTE_PEAR_SKILLS_DIR="$REMOTE_PEAR_DIR/skills"
DEPLOY_RECORD_PATH="/home/zulipdev/pear-deployed-sha"
SNAPSHOT_ROOT="/home/zulipdev/pear-deploy-snapshots"
YES=false
RUN_TESTS=true
RUN_MIGRATE=true
RUN_COMPAT=true
RUN_RUNTIME_SMOKE=false
RUNTIME_SMOKE_ADMIN_EMAIL=""
RUNTIME_SMOKE_TIMEOUT=150
RUNTIME_SMOKE_EVIDENCE=""
STATE_PATH="$DEFAULT_STATE_PATH"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

validate_local_deploy_git_metadata() {
    local dotgit="$REPO_ROOT/.git"
    local gitfile
    local gitdir
    local local_git_dir
    local local_git_common_dir

    if [[ ! -f "$dotgit" ]]; then
        fail "local checkout must be the canonical Zulip submodule with a relative .git gitfile; refusing to deploy from a checkout with a .git directory or missing .git file"
    fi
    IFS= read -r gitfile <"$dotgit" || gitfile=""
    if [[ "$gitfile" != gitdir:\ * ]]; then
        fail "local .git file is not a gitdir file; refusing to deploy"
    fi
    gitdir="${gitfile#gitdir: }"
    if [[ -z "$gitdir" || "$gitdir" == /* ]]; then
        fail "local .git points at an absolute gitdir ($gitdir); deploy from the main /Users/progyan/codack/zulip checkout, not a git worktree"
    fi

    local_git_dir="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-dir)"
    local_git_common_dir="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir)"
    if [[ "$local_git_dir" != "$local_git_common_dir" ]]; then
        fail "local checkout is a linked Git worktree ($local_git_dir); deploy from the main checkout so the remote .git gitfile resolves to /home/zulipdev/.git/modules/zulip"
    fi
}

usage() {
    cat <<EOF
Usage: tools/pear_deploy/update_remote.sh [options] [state.json]

Safely update an already-bootstrapped Pear VM from this clean pear-main
checkout. The script performs read-only gates by default. Mutating deploy steps
require --yes.

Options:
  --yes             Take the snapshot, rsync, migrate, restart, verify, record SHA.
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

run_local_compat() {
    local checker="$PEAR_ROOT/tools/check_pear_compat.py"
    if [[ ! -f "$checker" ]]; then
        echo "Skipping compat check: $checker not found."
        return
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

remote_webpack_stats_summary() {
    ssh_zulip_clean "$REMOTE_PATH/.venv/bin/python3 - <<'PY'
import json
from pathlib import Path

path = Path('var/webpack-stats-dev.json')
if not path.exists():
    print('missing 0 false false')
    raise SystemExit

data = json.loads(path.read_text())
assets = data.get('assets', {})
print(
    data.get('status', 'unknown'),
    int(path.stat().st_mtime),
    str('app.js' in assets).lower(),
    str('app.css' in assets).lower(),
)
PY"
}

wait_for_frontend_assets() {
    local restart_epoch="$1"
    local summary
    local status
    local stats_mtime
    local has_app_js
    local has_app_css

    echo "Waiting for webpack dev assets to rebuild..."
    for _ in $(seq 1 60); do
        summary="$(remote_webpack_stats_summary 2>/dev/null || true)"
        read -r status stats_mtime has_app_js has_app_css <<<"$summary"
        if [[
            "$status" == "done" &&
            "$has_app_js" == "true" &&
            "$has_app_css" == "true" &&
            "$stats_mtime" =~ ^[0-9]+$ &&
            "$stats_mtime" -ge "$restart_epoch"
        ]]; then
            if
                curl -kfsSI --max-time 10 "https://$FQDN/webpack/app.js" >/dev/null &&
                    curl -kfsSI --max-time 10 "https://$FQDN/webpack/app.css" >/dev/null
            then
                echo "Frontend assets ready: webpack-stats-dev.json status=$status mtime=$stats_mtime"
                return
            fi
        fi
        sleep 5
    done

    fail "webpack dev assets did not rebuild after service restart; last status: ${summary:-none}"
}

SNAPSHOT_DIR=""
ROLLBACK_READY=false
ROLLBACK_DONE=false

rollback_on_error() {
    local status=$?
    if [[ $status -ne 0 && "$ROLLBACK_READY" == true && "$ROLLBACK_DONE" == false ]]; then
        echo
        echo "Update failed after snapshot; rolling back from $SNAPSHOT_DIR" >&2
        set +e
        ssh_root bash -s -- "$SNAPSHOT_DIR" "$REMOTE_PATH" "$REMOTE_GIT_COMMON_DIR" "$REMOTE_PEAR_DIR" "$REMOTE_PEAR_SKILLS_DIR" "${PEAR_DEPLOY_RSYNC_EXCLUDES[@]}" <<'REMOTE'
set -euo pipefail
SNAPSHOT_DIR="$1"
REMOTE_PATH="$2"
REMOTE_GIT_COMMON_DIR="$3"
REMOTE_PEAR_DIR="$4"
REMOTE_PEAR_SKILLS_DIR="$5"
shift 5
RSYNC_EXCLUDES=("$@")

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

chown -R zulipdev:zulipdev "$REMOTE_PATH"
chown -R zulipdev:zulipdev /home/zulipdev/.git 2>/dev/null || true
chown -R zulipdev:zulipdev "$REMOTE_PEAR_DIR" 2>/dev/null || true
systemctl restart pear-worker pear-embedded-bots pear-thumbnail pear-zulip-dev
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
    exit "$status"
}
trap rollback_on_error EXIT

LOCAL_BRANCH="$(git -C "$REPO_ROOT" branch --show-current)"
LOCAL_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
LOCAL_GIT_COMMON_DIR="$(git -C "$REPO_ROOT" rev-parse --git-common-dir)"
LOCAL_STATUS="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)"
LOCAL_UNTRACKED_SOURCE="$(local_untracked_source_files)"
LOCAL_PEAR_SKILLS_STATUS="$(local_pear_skills_status)"

validate_local_deploy_git_metadata

if [[ "$LOCAL_BRANCH" != "pear-main" ]]; then
    fail "local branch is $LOCAL_BRANCH, expected pear-main"
fi
if [[ -n "$LOCAL_STATUS" ]]; then
    echo "$LOCAL_STATUS" >&2
    fail "local tracked tree is dirty; commit or revert tracked changes before deploying"
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

echo "Preflight: local $LOCAL_BRANCH $LOCAL_SHA is tracked-clean."
echo "Preflight: local pear/skills is tracked-clean."

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
        zerver/worker/pear_worker.py \
        zerver/views/pear.py \
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
ssh_root bash -s -- "$REMOTE_PATH" "$REMOTE_GIT_COMMON_DIR" "$REMOTE_PEAR_SKILLS_DIR" "$SNAPSHOT_DIR" "${PEAR_DEPLOY_RSYNC_EXCLUDES[@]}" <<'REMOTE'
set -euo pipefail
REMOTE_PATH="$1"
REMOTE_GIT_COMMON_DIR="$2"
REMOTE_PEAR_SKILLS_DIR="$3"
SNAPSHOT_DIR="$4"
shift 4
RSYNC_EXCLUDES=("$@")

install -d -m 755 "$SNAPSHOT_DIR/worktree" "$SNAPSHOT_DIR/git-common"
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
REMOTE
ROLLBACK_READY=true

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

if [[ -d "$LOCAL_GIT_COMMON_DIR" ]]; then
    echo "Syncing Git metadata for this worktree..."
    ssh_root "install -d -m 755 $REMOTE_GIT_COMMON_DIR"
    rsync -az --delete \
        -e "ssh -F $SSH_CONFIG" \
        "$LOCAL_GIT_COMMON_DIR/" \
        "root@$IP_ADDRESS:$REMOTE_GIT_COMMON_DIR/"
fi

ssh_root "chown -R zulipdev:zulipdev $REMOTE_PATH"
ssh_root 'chown -R zulipdev:zulipdev /home/zulipdev/.git 2>/dev/null || true'
ssh_root "chown -R zulipdev:zulipdev $REMOTE_PEAR_DIR"
ssh_root "sudo -Hu zulipdev git config --global --add safe.directory $REMOTE_PATH"

if [[ "$RUN_MIGRATE" == true ]]; then
    echo "Applying zerver migrations..."
    ssh_zulip "$REMOTE_PATH/.venv/bin/python3 $REMOTE_PATH/manage.py migrate zerver --noinput"
else
    echo "Skipping migration by request."
fi

echo "Restarting Pear services..."
RESTART_EPOCH="$(ssh_root 'date +%s')"
ssh_root 'systemctl restart pear-worker pear-embedded-bots pear-thumbnail pear-zulip-dev'

echo "Waiting for https://$FQDN/ to serve..."
SITE_CODE=""
for _ in $(seq 1 60); do
    SITE_CODE="$(curl -kfsS --max-time 10 -o /dev/null -w '%{http_code}' "https://$FQDN/" || true)"
    if [[ "$SITE_CODE" == "200" || "$SITE_CODE" == "302" || "$SITE_CODE" == "303" ]]; then
        break
    fi
    sleep 5
done
if [[ "$SITE_CODE" != "200" && "$SITE_CODE" != "302" && "$SITE_CODE" != "303" ]]; then
    fail "site did not become healthy; last HTTP code: ${SITE_CODE:-none}"
fi
wait_for_frontend_assets "$RESTART_EPOCH"

if [[ "$RUN_TESTS" == true ]]; then
    echo "Running Pear backend tests on the box..."
    ssh_zulip_clean "./tools/test-backend zerver.tests.test_pear zerver.tests.test_pear_endpoint"
else
    echo "Skipping test-backend by request."
fi

echo "Pear live health:"
ssh_zulip "$REMOTE_PATH/tools/pear-health-report --max-active-runs 0 --max-stale-active-runs 0 --max-orphan-active-markers 0"

echo "Pear queue health:"
ssh_root 'rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers | awk '"'"'
$1 == "pear_worker" || $1 == "embedded_bots" {
    seen += 1
    print
    if ($2 != 0 || $3 != 0 || $4 < 1) {
        bad = 1
    }
}
END {
    if (seen < 2) {
        print "missing Pear queues" > "/dev/stderr"
        exit 1
    }
    exit bad
}
'"'"

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
ssh_root 'systemctl --no-pager --full status pear-worker pear-embedded-bots pear-thumbnail pear-zulip-dev | sed -n "1,120p"'

echo "pear-worker log tail:"
ssh_root 'journalctl -u pear-worker -n 40 --no-pager'

echo "pear-zulip-dev recent log tail:"
ssh_root 'journalctl -u pear-zulip-dev -n 80 --no-pager'

DEPLOYED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '%s %s\n' "$LOCAL_SHA" "$DEPLOYED_AT" |
    ssh_root "cat > $DEPLOY_RECORD_PATH && chown zulipdev:zulipdev $DEPLOY_RECORD_PATH"

ROLLBACK_READY=false
echo
echo "Deployed $LOCAL_SHA at $DEPLOYED_AT"
echo "Recorded deployment in $DEPLOY_RECORD_PATH"
