#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEFAULT_STATE_PATH="$REPO_ROOT/output/pear-staging/server.json"
STATE_PATH="$DEFAULT_STATE_PATH"
KEEP=10
YES=false

usage() {
    cat <<EOF
Usage: tools/armada_deploy/prune_remote_snapshots.sh [options] [state.json]

List old deploy snapshots by default. Deletion requires --yes.

Options:
  --keep COUNT  Retain the newest COUNT snapshots (default: 10).
  --yes         Delete snapshots beyond the retention count.
  --state PATH  Use a specific armada_do.py state file.
  -h, --help    Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep)
            [[ $# -ge 2 ]] || { echo "Missing value for --keep" >&2; exit 2; }
            KEEP="$2"
            shift 2
            ;;
        --yes)
            YES=true
            shift
            ;;
        --state)
            [[ $# -ge 2 ]] || { echo "Missing value for --state" >&2; exit 2; }
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

if [[ ! "$KEEP" =~ ^[1-9][0-9]*$ ]]; then
    echo "--keep must be a positive integer" >&2
    exit 2
fi
if [[ ! -f "$STATE_PATH" ]]; then
    echo "Missing state file: $STATE_PATH" >&2
    exit 1
fi

SSH_CONFIG="$(python3 - "$STATE_PATH" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1]))["ssh_config"])
PY
)"

args=(
    /home/zulipdev/zulip/tools/armada_deploy/snapshot_retention.py
    --root /home/zulipdev/pear-deploy-snapshots
    --keep "$KEEP"
)
if [[ "$YES" == true ]]; then
    args+=(--yes)
fi
printf -v remote_command '%q ' "${args[@]}"
ssh -F "$SSH_CONFIG" pear-staging-root "$remote_command"
