#!/usr/bin/env python3
from __future__ import annotations

import fnmatch
import sys
from collections.abc import Iterable

# Keep this allowlist narrow: every unknown source path pays the full asset
# build cost. These paths are either excluded from the production asset staging
# tree or are server-only Armada modules with no generated/static output.
BACKEND_ONLY_PATTERNS = (
    "zerver/lib/armada.py",
    "zerver/lib/armada_*.py",
    "zerver/views/armada.py",
    "zerver/views/armada_*.py",
    "zerver/worker/armada_*.py",
    "zerver/models/bots.py",
    "zerver/migrations/*_armada*.py",
    "zerver/tests/*",
    "analytics/tests/*",
    "confirmation/tests/*",
    "corporate/tests/*",
    "web/tests/*",
    "zilencer/tests/*",
    ".github/workflows/armada-backend.yml",
    "tools/armada-health-report",
    "tools/armada_deploy/README.md",
    "tools/armada_deploy/asset_reuse_policy.py",
    "tools/armada_deploy/bootstrap_remote.sh",
    "tools/armada_deploy/deploy_common.sh",
    "tools/armada_deploy/deployment_capacity.py",
    "tools/armada_deploy/runtime/Caddyfile",
    "tools/armada_deploy/runtime/caddy-armada-basic-auth.conf",
    "tools/armada_deploy/runtime_config.py",
    "tools/armada_deploy/test_production_runtime.py",
    "tools/armada_deploy/update_remote.sh",
    "tools/armada_deploy/verify_remote.sh",
)


def asset_rebuild_trigger(changed_paths: Iterable[str]) -> str | None:
    for path in changed_paths:
        if not any(fnmatch.fnmatchcase(path, pattern) for pattern in BACKEND_ONLY_PATTERNS):
            return path
    return None


def main() -> None:
    paths = [
        raw_path.decode("utf-8", errors="surrogateescape")
        for raw_path in sys.stdin.buffer.read().split(b"\0")
        if raw_path
    ]
    trigger = asset_rebuild_trigger(paths)
    if trigger is None:
        return
    sys.stdout.write(trigger.encode("utf-8", errors="backslashreplace").decode("utf-8"))
    raise SystemExit(10)


if __name__ == "__main__":
    main()
