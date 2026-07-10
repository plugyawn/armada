#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HASHED_BUNDLE_RE = re.compile(r"(?:^|\.)[0-9a-f]{16,}(?:\.|$)")
FORBIDDEN_MARKERS = (
    "hot-update",
    "webpack-dev-server",
    "webpack/hot/",
    "localhost:9994",
    "127.0.0.1:9994",
)


class AssetValidationError(RuntimeError):
    pass


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AssetValidationError(f"missing production asset file: {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetValidationError(f"invalid production asset JSON: {path}") from exc
    if not isinstance(value, dict):
        raise AssetValidationError(f"production asset JSON must be an object: {path}")
    return value


def validate_production_assets(repo_root: Path) -> dict[str, int]:
    repo_root = repo_root.resolve()
    stats_path = repo_root / "webpack-stats-production.json"
    manifest_path = repo_root / "staticfiles.json"
    static_root = repo_root / "prod-static" / "serve"

    stats_text = stats_path.read_text(encoding="utf-8") if stats_path.is_file() else ""
    lowered_stats = stats_text.lower()
    found_markers = [marker for marker in FORBIDDEN_MARKERS if marker in lowered_stats]
    if found_markers:
        raise AssetValidationError(
            "production webpack stats contain development markers: " + ", ".join(found_markers)
        )

    stats = read_json_object(stats_path)
    if stats.get("status") != "done":
        raise AssetValidationError("production webpack status is not done")
    chunks = stats.get("chunks")
    if not isinstance(chunks, dict):
        raise AssetValidationError("production webpack chunks are missing")
    for entrypoint in ("app", "common"):
        entrypoint_chunks = chunks.get(entrypoint)
        if not isinstance(entrypoint_chunks, list) or not entrypoint_chunks:
            raise AssetValidationError(f"production webpack entrypoint is missing: {entrypoint}")

    assets = stats.get("assets")
    if not isinstance(assets, dict) or not assets:
        raise AssetValidationError("production webpack assets are missing")

    referenced_bundles: set[str] = {
        filename
        for filename in assets
        if isinstance(filename, str) and filename.endswith((".js", ".css"))
    }
    for entrypoint, filenames in chunks.items():
        if not isinstance(entrypoint, str) or not isinstance(filenames, list):
            raise AssetValidationError("production webpack chunks have an invalid shape")
        for filename in filenames:
            if not isinstance(filename, str):
                raise AssetValidationError("production webpack chunk filename is not a string")
            if not filename.endswith((".js", ".css")):
                continue
            if Path(filename).name != filename:
                raise AssetValidationError("production webpack chunk filename is not basename-only")
            if not HASHED_BUNDLE_RE.search(filename):
                raise AssetValidationError(f"production webpack bundle is not hashed: {filename}")
            referenced_bundles.add(filename)

    for filename in referenced_bundles:
        if Path(filename).name != filename:
            raise AssetValidationError("production webpack asset filename is not basename-only")
        if not HASHED_BUNDLE_RE.search(filename):
            raise AssetValidationError(f"production webpack asset is not hashed: {filename}")

    if not any(filename.endswith(".js") for filename in referenced_bundles):
        raise AssetValidationError("production webpack has no JavaScript bundles")
    if not any(filename.endswith(".css") for filename in referenced_bundles):
        raise AssetValidationError("production webpack has no CSS bundles")

    missing_or_empty = [
        filename
        for filename in sorted(referenced_bundles)
        if not (static_root / "webpack-bundles" / filename).is_file()
        or (static_root / "webpack-bundles" / filename).stat().st_size == 0
    ]
    if missing_or_empty:
        raise AssetValidationError(
            "production webpack bundles are missing or empty: " + ", ".join(missing_or_empty[:8])
        )

    manifest = read_json_object(manifest_path)
    paths = manifest.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise AssetValidationError("staticfiles manifest paths are missing")
    manifest_name = manifest.get("version")
    if manifest_name not in ("1.0", "1.1"):
        raise AssetValidationError("staticfiles manifest version is unsupported")
    missing_manifest_targets: list[str] = []
    for target in paths.values():
        if not isinstance(target, str) or Path(target).is_absolute() or ".." in Path(target).parts:
            raise AssetValidationError("staticfiles manifest contains an invalid target")
        if not (static_root / target).is_file():
            missing_manifest_targets.append(target)
    if missing_manifest_targets:
        raise AssetValidationError(
            "staticfiles manifest targets are missing: "
            + ", ".join(sorted(missing_manifest_targets)[:8])
        )

    return {
        "webpack_bundles": len(referenced_bundles),
        "static_manifest_paths": len(paths),
    }


def select_production_asset_probes(repo_root: Path) -> tuple[str, str, str]:
    repo_root = repo_root.resolve()
    stats = read_json_object(repo_root / "webpack-stats-production.json")
    manifest = read_json_object(repo_root / "staticfiles.json")
    chunks = stats.get("chunks")
    paths = manifest.get("paths")
    if not isinstance(chunks, dict) or not isinstance(chunks.get("app"), list):
        raise AssetValidationError("production webpack app entrypoint is missing")
    if not isinstance(paths, dict):
        raise AssetValidationError("staticfiles manifest paths are missing")

    app = chunks["app"]
    try:
        javascript = next(
            value for value in app if isinstance(value, str) and value.endswith(".js")
        )
        stylesheet = next(
            value for value in app if isinstance(value, str) and value.endswith(".css")
        )
    except StopIteration as exc:
        raise AssetValidationError("production webpack app probes are incomplete") from exc

    general_static = next(
        (
            target
            for target in sorted(paths.values())
            if isinstance(target, str) and not target.startswith("webpack-bundles/")
        ),
        None,
    )
    if general_static is None:
        raise AssetValidationError(
            "no non-webpack static asset is available for Caddy verification"
        )
    return javascript, stylesheet, general_static


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--print-probe-paths", action="store_true")
    args = parser.parse_args()
    try:
        summary = validate_production_assets(args.repo_root)
        probes = select_production_asset_probes(args.repo_root) if args.print_probe_paths else None
    except (OSError, AssetValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if probes is not None:
        print(*probes, sep="\n")
        return
    print(
        "Production assets valid: "
        f"{summary['webpack_bundles']} bundles, "
        f"{summary['static_manifest_paths']} static manifest paths."
    )


if __name__ == "__main__":
    main()
