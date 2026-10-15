#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


class RetentionError(RuntimeError):
    pass


def removable_snapshots(root: Path, keep: int) -> list[Path]:
    if keep < 1:
        raise RetentionError("--keep must be at least 1")
    if not root.is_dir() or root.is_symlink():
        raise RetentionError(f"snapshot root must be a real directory: {root}")
    snapshots = [
        path
        for path in root.iterdir()
        if path.is_dir() and not path.is_symlink() and path.name.startswith("zulip-")
    ]
    snapshots.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    return snapshots[keep:]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prune old Armada deploy snapshots; dry-run unless --yes is passed."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--keep", type=int, default=10)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    try:
        candidates = removable_snapshots(args.root, args.keep)
        root = args.root.resolve(strict=True)
        for candidate in candidates:
            resolved = candidate.resolve(strict=True)
            if resolved.parent != root:
                raise RetentionError(f"snapshot escaped retention root: {candidate}")
            if args.yes:
                shutil.rmtree(resolved)
                print(f"removed {candidate.name}")
            else:
                print(f"would remove {candidate.name}")
    except (OSError, RetentionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

    action = "Pruned" if args.yes else "Dry run"
    print(f"{action}: {len(candidates)} snapshot(s) beyond newest {args.keep}.")


if __name__ == "__main__":
    main()
