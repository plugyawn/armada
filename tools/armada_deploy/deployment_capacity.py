#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIB = 1024**3
EXCLUDED_RELATIVE_PATHS = (
    ".playwright-cli",
    ".venv",
    ".vagrant",
    ".pnpm-store",
    "node_modules",
    "var",
    "output",
    "prod-static",
    "static/generated",
    "static/webpack-bundles",
    "static/images/landing-page/hello/generated",
    "locale/en",
    "web/.parcel-cache",
    "web/coverage",
    "web/generated",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
)
GENERATED_RELATIVE_PATHS = (
    "prod-static",
    "static/generated",
    "static/webpack-bundles",
    "static/images/landing-page/hello/generated",
    "locale/en",
    "web/generated",
    "webpack-stats-production.json",
    "staticfiles.json",
    "locale/language_options.json",
    "locale/language_name_map.json",
)


@dataclass(frozen=True)
class CapacityEstimate:
    available_bytes: int
    reclaimable_asset_build_bytes: int
    snapshot_bytes: int
    asset_build_bytes: int
    build_swap_bytes: int
    safety_bytes: int

    @property
    def required_bytes(self) -> int:
        return (
            self.snapshot_bytes + self.asset_build_bytes + self.build_swap_bytes + self.safety_bytes
        )

    @property
    def effective_available_bytes(self) -> int:
        return self.available_bytes + self.reclaimable_asset_build_bytes

    @property
    def sufficient(self) -> bool:
        return self.effective_available_bytes >= self.required_bytes


def allocated_bytes(path: Path) -> int:
    if not path.exists() and not path.is_symlink():
        return 0
    result = subprocess.run(
        ["du", "-s", "-B1", "--", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.split()[0])


def estimate_capacity(
    *,
    remote_path: Path,
    git_common_dir: Path,
    armada_skills_dir: Path,
    snapshot_root: Path,
    asset_build_dir: Path,
    build_swap_bytes: int,
    runtime_paths: list[Path],
    build_assets: bool = True,
) -> CapacityEstimate:
    if build_swap_bytes < 0:
        raise ValueError("build_swap_bytes must be nonnegative")
    # The worktree snapshot excludes mutable/build-heavy paths and archives
    # generated outputs separately. These relative paths are disjoint, which
    # keeps subtraction conservative without charging for the live venv/node tree.
    excluded_bytes = sum(
        allocated_bytes(remote_path / relative) for relative in EXCLUDED_RELATIVE_PATHS
    )
    generated_bytes = sum(
        allocated_bytes(remote_path / relative) for relative in GENERATED_RELATIVE_PATHS
    )
    live_worktree_bytes = allocated_bytes(remote_path)
    source_bytes = max(live_worktree_bytes - excluded_bytes, 0)
    snapshot_bytes = (
        source_bytes
        + allocated_bytes(git_common_dir)
        + allocated_bytes(armada_skills_dir)
        + generated_bytes
        + sum(allocated_bytes(path) for path in runtime_paths)
    )

    # The isolated build duplicates source, can transiently hold old and new
    # compressed graphs, and is then copied once into a root-owned frozen tree.
    # Keep a 3 GiB output floor plus the separate 1 GiB filesystem margin.
    asset_build_bytes = source_bytes + max(generated_bytes * 3, 3 * GIB) if build_assets else 0
    return CapacityEstimate(
        available_bytes=shutil.disk_usage(snapshot_root.parent).free,
        reclaimable_asset_build_bytes=allocated_bytes(asset_build_dir),
        snapshot_bytes=snapshot_bytes,
        asset_build_bytes=asset_build_bytes,
        build_swap_bytes=build_swap_bytes,
        safety_bytes=GIB,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote-path", type=Path, required=True)
    parser.add_argument("--git-common-dir", type=Path, required=True)
    parser.add_argument("--armada-skills-dir", type=Path, required=True)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--asset-build-dir", type=Path, required=True)
    parser.add_argument("--build-swap-bytes", type=int, default=0)
    parser.add_argument("--reuse-assets", action="store_true")
    parser.add_argument("--runtime-path", type=Path, action="append", default=[])
    return parser


def main() -> None:
    args = build_parser().parse_args()
    estimate = estimate_capacity(
        remote_path=args.remote_path,
        git_common_dir=args.git_common_dir,
        armada_skills_dir=args.armada_skills_dir,
        snapshot_root=args.snapshot_root,
        asset_build_dir=args.asset_build_dir,
        build_swap_bytes=args.build_swap_bytes,
        runtime_paths=args.runtime_path,
        build_assets=not args.reuse_assets,
    )
    print(f"remote_disk_available_bytes={estimate.available_bytes}")
    print(f"remote_disk_reclaimable_asset_build_bytes={estimate.reclaimable_asset_build_bytes}")
    print(f"remote_disk_snapshot_estimate_bytes={estimate.snapshot_bytes}")
    print(f"remote_disk_asset_build_estimate_bytes={estimate.asset_build_bytes}")
    print(f"remote_disk_build_swap_bytes={estimate.build_swap_bytes}")
    print(f"remote_disk_safety_bytes={estimate.safety_bytes}")
    print(f"remote_disk_required_bytes={estimate.required_bytes}")
    if not estimate.sufficient:
        raise SystemExit(
            "insufficient remote disk space: "
            f"effective_available={estimate.effective_available_bytes}, "
            f"required={estimate.required_bytes}"
        )


if __name__ == "__main__":
    main()
