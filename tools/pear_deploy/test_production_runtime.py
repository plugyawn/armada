from __future__ import annotations

import argparse
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.pear_deploy.production_assets import AssetValidationError, validate_production_assets
from tools.pear_deploy.runtime_config import (
    AUTH_ENV_PATH,
    PEAR_ENV_PATH,
    RuntimeConfigError,
    install,
    parse_auth_env,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_HASH = "$2b$12$" + "A" * 53


class ProductionAssetsTest(unittest.TestCase):
    def make_fixture(self, root: Path) -> None:
        bundle_root = root / "prod-static/serve/webpack-bundles"
        bundle_root.mkdir(parents=True)
        chunks = {
            "app": ["app.0123456789abcdef.js", "app.fedcba9876543210.css"],
            "common": ["common.1111111111111111.js"],
        }
        for filename in {filename for values in chunks.values() for filename in values}:
            (bundle_root / filename).write_text("asset", encoding="utf-8")
        (root / "webpack-stats-production.json").write_text(
            json.dumps(
                {
                    "status": "done",
                    "chunks": chunks,
                    "assets": {
                        filename: {"name": filename}
                        for values in chunks.values()
                        for filename in values
                    },
                }
            ),
            encoding="utf-8",
        )
        (root / "staticfiles.json").write_text(
            json.dumps(
                {
                    "version": "1.1",
                    "paths": {"webpack-bundles/app.js": "webpack-bundles/app.0123456789abcdef.js"},
                }
            ),
            encoding="utf-8",
        )

    def test_accepts_complete_hashed_asset_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.make_fixture(root)
            self.assertEqual(
                validate_production_assets(root),
                {"webpack_bundles": 3, "static_manifest_paths": 1},
            )

    def test_rejects_development_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.make_fixture(root)
            stats_path = root / "webpack-stats-production.json"
            stats = json.loads(stats_path.read_text())
            stats["publicPath"] = "http://localhost:9994/"
            stats_path.write_text(json.dumps(stats))
            with self.assertRaisesRegex(AssetValidationError, "development markers"):
                validate_production_assets(root)

    def test_rejects_missing_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.make_fixture(root)
            (root / "prod-static/serve/webpack-bundles/app.0123456789abcdef.js").unlink()
            with self.assertRaisesRegex(AssetValidationError, "missing or empty"):
                validate_production_assets(root)


class RuntimeConfigTest(unittest.TestCase):
    def make_args(self, root: Path, **overrides: object) -> argparse.Namespace:
        values: dict[str, object] = {
            "repo_root": REPO_ROOT,
            "root_prefix": root,
            "fqdn": "pear.example.com",
            "skip_caddy_validate": True,
            "bootstrap_auth_user": "pear-user",
            "bootstrap_auth_stdin": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_install_requires_auth_and_preserves_pear_env(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pear_env = root / PEAR_ENV_PATH.relative_to("/")
            pear_env.parent.mkdir(parents=True)
            pear_env.write_text("EXTERNAL_HOST=pear.example.com\nKEEP=value\n")
            auth_env = root / AUTH_ENV_PATH.relative_to("/")
            auth_env.parent.mkdir(parents=True)
            auth_env.write_text(
                "PEAR_CADDY_FQDN=pear.example.com\n"
                "PEAR_CADDY_BASICAUTH_USER=pear-user\n"
                f"PEAR_CADDY_BASICAUTH_HASH={TEST_HASH}\n"
            )
            auth_env.chmod(0o600)

            install(self.make_args(root))

            self.assertEqual(stat.S_IMODE(auth_env.stat().st_mode), 0o600)
            self.assertEqual(parse_auth_env(auth_env)["PEAR_CADDY_BASICAUTH_HASH"], TEST_HASH)
            pear_env_text = pear_env.read_text()
            self.assertIn("KEEP=value", pear_env_text)
            self.assertEqual(pear_env_text.count("ZULIP_DEV_PRODUCTION_ASSETS=1"), 1)
            self.assertIn(
                "--production-assets",
                (root / "usr/local/bin/pear-run-zulip-dev").read_text(),
            )
            self.assertEqual(
                stat.S_IMODE((root / "usr/local/bin/pear-run-zulip-dev").stat().st_mode),
                0o755,
            )

    def test_install_fails_when_auth_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pear_env = root / PEAR_ENV_PATH.relative_to("/")
            pear_env.parent.mkdir(parents=True)
            pear_env.write_text("EXTERNAL_HOST=pear.example.com\n")
            with self.assertRaisesRegex(RuntimeConfigError, "auth is absent"):
                install(self.make_args(root))

    def test_install_bootstraps_hash_from_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pear_env = root / PEAR_ENV_PATH.relative_to("/")
            pear_env.parent.mkdir(parents=True)
            pear_env.write_text("EXTERNAL_HOST=pear.example.com\n")
            args = self.make_args(root, bootstrap_auth_stdin=True)
            with patch("sys.stdin", io.StringIO(TEST_HASH + "\n")):
                install(args)
            auth_env = root / AUTH_ENV_PATH.relative_to("/")
            self.assertEqual(stat.S_IMODE(auth_env.stat().st_mode), 0o600)
            self.assertEqual(parse_auth_env(auth_env)["PEAR_CADDY_BASICAUTH_HASH"], TEST_HASH)


class SnapshotRetentionTest(unittest.TestCase):
    def test_command_is_dry_run_until_yes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            snapshots = [root / f"zulip-2026070{index}" for index in range(1, 4)]
            for index, snapshot in enumerate(snapshots):
                snapshot.mkdir()
                os.utime(snapshot, ns=(index + 1, index + 1))
            command = [
                str(REPO_ROOT / "tools/pear_deploy/snapshot_retention.py"),
                "--root",
                str(root),
                "--keep",
                "1",
            ]
            dry_run = subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertIn("Dry run: 2 snapshot(s)", dry_run.stdout)
            self.assertTrue(all(snapshot.exists() for snapshot in snapshots))

            mutation = subprocess.run(
                [*command, "--yes"], check=True, capture_output=True, text=True
            )
            self.assertIn("Pruned: 2 snapshot(s)", mutation.stdout)
            self.assertEqual([snapshot.exists() for snapshot in snapshots], [False, False, True])


class StaticRuntimeContractTest(unittest.TestCase):
    def test_runtime_has_no_open_caddy_or_dev_asset_fallback(self) -> None:
        run_dev = (REPO_ROOT / "tools/run-dev").read_text()
        wrapper = (REPO_ROOT / "tools/pear_deploy/runtime/pear-run-zulip-dev").read_text()
        reset = (REPO_ROOT / "tools/pear_deploy/reset_remote_dev_data.sh").read_text()
        common = (REPO_ROOT / "tools/pear_deploy/deploy_common.sh").read_text()
        caddyfile = (REPO_ROOT / "tools/pear_deploy/runtime/Caddyfile").read_text()
        caddy_drop_in = (
            REPO_ROOT / "tools/pear_deploy/runtime/caddy-pear-basic-auth.conf"
        ).read_text()
        self.assertIn("--production-assets", run_dev)
        self.assertIn("--production-assets", wrapper)
        self.assertNotIn("PEAR_CADDY_BASICAUTH_HASH:-", reset)
        self.assertIn("webpack-stats-production.json", common)
        self.assertIn("staticfiles.json", common)
        self.assertIn("--exclude '*.mo'", common)
        self.assertIn("basic_auth", caddyfile)
        self.assertIn("{$PEAR_CADDY_BASICAUTH_HASH}", caddyfile)
        self.assertIn("/etc/pear/caddy-basic-auth.env", caddy_drop_in)

    def test_shared_queue_validator_still_executes(self) -> None:
        script = (
            "source tools/pear_deploy/deploy_common.sh; "
            "printf '%s\\n' "
            "'pear_worker 0 0 1' "
            "'pear_monitor_worker 0 0 1' "
            "'embedded_bots 0 0 1' "
            "'thumbnail 0 0 1' | "
            "validate_pear_deploy_queue_snapshot"
        )
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_untracked_output_allowlist_uses_exact_file_names(self) -> None:
        script = (
            "source tools/pear_deploy/deploy_common.sh; "
            "printf '%s\\n' staticfiles.json staticfiles.json.evil var/generated | "
            "filter_deploy_untracked_source_files"
        )
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "staticfiles.json.evil\n")


if __name__ == "__main__":
    unittest.main()
