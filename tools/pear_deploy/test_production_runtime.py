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
from types import SimpleNamespace
from unittest.mock import patch

from tools.pear_deploy.deployment_capacity import GIB, estimate_capacity
from tools.pear_deploy.production_assets import (
    AssetValidationError,
    select_production_asset_probes,
    validate_production_assets,
)
from tools.pear_deploy.runtime_config import (
    AUTH_ENV_PATH,
    PEAR_ENV_PATH,
    RuntimeConfigError,
    install,
    parse_auth_env,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_HASH = "$2b$12$" + "A" * 53
MATCHING_TEST_HASH = "$2b$04$fRycSErhIEsfAHfYRqtIDuSu8dmFES90MoaYCROKQo7yj7HaG2d3i"


class DeploymentCapacityTest(unittest.TestCase):
    def test_reserves_snapshot_build_graph_and_safety_margin(self) -> None:
        remote_path = Path("/remote/zulip")
        mapping = {
            remote_path: 10_000,
            remote_path / ".venv": 4_000,
            remote_path / "prod-static": 1_000,
            Path("/remote/git"): 500,
            Path("/remote/skills"): 250,
            Path("/etc/runtime"): 50,
            Path("/remote/asset-build"): 100,
        }
        with (
            patch(
                "tools.pear_deploy.deployment_capacity.allocated_bytes",
                side_effect=lambda path: mapping.get(path, 0),
            ),
            patch(
                "tools.pear_deploy.deployment_capacity.shutil.disk_usage",
                return_value=SimpleNamespace(free=4 * GIB),
            ),
        ):
            estimate = estimate_capacity(
                remote_path=remote_path,
                git_common_dir=Path("/remote/git"),
                pear_skills_dir=Path("/remote/skills"),
                snapshot_root=Path("/remote/snapshots"),
                asset_build_dir=Path("/remote/asset-build"),
                runtime_paths=[Path("/etc/runtime")],
            )

        self.assertEqual(estimate.snapshot_bytes, 6_800)
        self.assertEqual(estimate.asset_build_bytes, 2 * GIB + 5_000)
        self.assertEqual(estimate.safety_bytes, GIB)
        self.assertEqual(estimate.reclaimable_asset_build_bytes, 100)
        self.assertTrue(estimate.sufficient)


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
                    "paths": {
                        "images/logo.svg": "images/logo.2222222222222222.svg",
                        "webpack-bundles/app.js": "webpack-bundles/app.0123456789abcdef.js",
                    },
                }
            ),
            encoding="utf-8",
        )
        general_asset = root / "prod-static/serve/images/logo.2222222222222222.svg"
        general_asset.parent.mkdir(parents=True)
        general_asset.write_text("<svg/>", encoding="utf-8")

    def test_accepts_complete_hashed_asset_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.make_fixture(root)
            self.assertEqual(
                validate_production_assets(root),
                {"webpack_bundles": 3, "static_manifest_paths": 2},
            )

    def test_selects_caddy_verification_probes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.make_fixture(root)
            self.assertEqual(
                select_production_asset_probes(root),
                (
                    "app.0123456789abcdef.js",
                    "app.fedcba9876543210.css",
                    "images/logo.2222222222222222.svg",
                ),
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
        update = (REPO_ROOT / "tools/pear_deploy/update_remote.sh").read_text()
        verify = (REPO_ROOT / "tools/pear_deploy/verify_remote.sh").read_text()
        self.assertIn("--production-assets", run_dev)
        self.assertIn("--production-assets", wrapper)
        self.assertNotIn("PEAR_CADDY_BASICAUTH_HASH:-", reset)
        self.assertIn("webpack-stats-production.json", common)
        self.assertIn("staticfiles.json", common)
        self.assertIn("--exclude '*.mo'", common)
        self.assertIn("basicauth", caddyfile)
        self.assertIn("{$PEAR_CADDY_BASICAUTH_HASH}", caddyfile)
        self.assertIn("handle_path /static/webpack-bundles/*", caddyfile)
        self.assertIn("root * /home/zulipdev/zulip/prod-static/serve/webpack-bundles", caddyfile)
        self.assertIn('header Cache-Control "public, max-age=31536000, immutable"', caddyfile)
        self.assertIn("handle_path /static/*", caddyfile)
        self.assertIn("root * /home/zulipdev/zulip/prod-static/serve", caddyfile)
        self.assertIn('header Cache-Control "no-cache"', caddyfile)
        self.assertEqual(caddyfile.count('header X-Content-Type-Options "nosniff"'), 2)
        self.assertEqual(caddyfile.count("precompressed gzip"), 2)
        self.assertIn("handle {", caddyfile)
        self.assertIn("reverse_proxy 127.0.0.1:9991", caddyfile)
        self.assertIn("/etc/pear/caddy-basic-auth.env", caddy_drop_in)
        self.assertNotIn("127.0.0.1:9991", update)
        self.assertNotIn("127.0.0.1:9991", verify)
        self.assertIn("verify_caddy_https_runtime", update)
        self.assertIn("verify_caddy_https_runtime", verify)
        self.assertIn("validate_local_caddy_auth_pair", update)
        self.assertIn("curl --config -", common)
        self.assertNotIn("curl --user", common)

        disk_preflight = "verify_remote_disk_capacity"
        self.assertLess(update.rindex(disk_preflight), update.index('if [[ "$YES" != true ]]'))
        self.assertLess(update.rindex(disk_preflight), update.index("Snapshotting remote tree"))
        self.assertLess(
            update.rindex("verify_existing_remote_caddy_auth"),
            update.index("Snapshotting remote tree"),
        )

    def test_verification_password_file_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            password_file = root / "password"
            password_file.write_text("dummy-secret\n", encoding="utf-8")
            password_file.chmod(0o600)
            command = [
                "bash",
                "-c",
                'source "$1"; validate_local_caddy_verification_auth pear-user "$2"',
                "bash",
                str(REPO_ROOT / "tools/pear_deploy/deploy_common.sh"),
                str(password_file),
            ]
            valid = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(valid.returncode, 0, valid.stderr)

            password_file.chmod(0o644)
            permissive = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertNotEqual(permissive.returncode, 0)
            self.assertIn("mode 0600", permissive.stderr)

            password_file.chmod(0o600)
            symlink = root / "password-link"
            symlink.symlink_to(password_file)
            symlink_command = [*command[:-1], str(symlink)]
            linked = subprocess.run(symlink_command, check=False, capture_output=True, text=True)
            self.assertNotEqual(linked.returncode, 0)
            self.assertIn("non-symlink", linked.stderr)

    def test_bootstrap_hash_must_match_verification_password(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            hash_file = root / "password.hash"
            hash_file.write_text(MATCHING_TEST_HASH + "\n", encoding="ascii")
            hash_file.chmod(0o600)
            password_file = root / "password"
            password_file.write_text("dummy-secret\n", encoding="utf-8")
            password_file.chmod(0o600)
            command = [
                "bash",
                "-c",
                'source "$1"; validate_local_caddy_auth_pair pear-user "$2" "$3"',
                "bash",
                str(REPO_ROOT / "tools/pear_deploy/deploy_common.sh"),
                str(hash_file),
                str(password_file),
            ]
            matching = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(matching.returncode, 0, matching.stderr)

            htpasswd_log = root / "htpasswd-argv.json"
            fake_htpasswd = root / "fake-htpasswd"
            fake_htpasswd.write_text(
                """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

if sys.stdin.read() != "dummy-secret\\n":
    raise SystemExit("password was not supplied on stdin")
Path(os.environ["FAKE_HTPASSWD_LOG"]).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
""",
                encoding="utf-8",
            )
            fake_htpasswd.chmod(0o755)
            mocked = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PEAR_DEPLOY_HTPASSWD": str(fake_htpasswd),
                    "FAKE_HTPASSWD_LOG": str(htpasswd_log),
                },
            )
            self.assertEqual(mocked.returncode, 0, mocked.stderr)
            self.assertNotIn("dummy-secret", htpasswd_log.read_text(encoding="utf-8"))

            password_file.write_text("wrong-secret\n", encoding="utf-8")
            mismatch = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("does not match", mismatch.stderr)

            password_file.write_text("dummy-secret\n", encoding="utf-8")
            hash_file.chmod(0o644)
            permissive_hash = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertNotEqual(permissive_hash.returncode, 0)
            self.assertIn("mode 0600", permissive_hash.stderr)

            hash_file.chmod(0o600)
            missing_tool = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PEAR_DEPLOY_HTPASSWD": str(root / "missing-htpasswd"),
                },
            )
            self.assertNotEqual(missing_tool.returncode, 0)
            self.assertIn("htpasswd is required", missing_tool.stderr)

    def test_authenticated_caddy_probe_keeps_password_out_of_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            password_file = root / "password"
            password_file.write_text("dummy-secret\n", encoding="utf-8")
            password_file.chmod(0o600)
            curl_log = root / "curl-argv.json"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
authenticated = "--config" in arguments
config = sys.stdin.read() if authenticated else ""
if authenticated and config != 'user = "pear-user:dummy-secret"\\n':
    raise SystemExit("unexpected stdin curl config")
Path(os.environ["FAKE_CURL_LOG"]).write_text(json.dumps(arguments), encoding="utf-8")

url = arguments[arguments.index("--url") + 1]
headers_path = None
if "--dump-header" in arguments:
    headers_path = Path(arguments[arguments.index("--dump-header") + 1])

status = "200"
headers = []
if not authenticated:
    status = "401"
elif "/static/webpack-bundles/" in url:
    headers.extend(
        [
            "Cache-Control: public, max-age=31536000, immutable",
            "X-Content-Type-Options: nosniff",
        ]
    )
    if "Accept-Encoding: gzip" in arguments:
        headers.append("Content-Encoding: gzip")
elif "/static/" in url:
    headers.extend(["Cache-Control: no-cache", "X-Content-Type-Options: nosniff"])
elif url.endswith("/webpack/app.js"):
    status = "404"
if os.environ.get("FAKE_CURL_OMIT_NOSNIFF"):
    headers = [value for value in headers if not value.lower().startswith("x-content-type")]
if headers_path is not None:
    headers_path.write_text("HTTP/2 200\\r\\n" + "\\r\\n".join(headers) + "\\r\\n\\r\\n")
sys.stdout.write(status)
""",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            script = (
                'source "$1"; verify_caddy_https_runtime '
                'pear.example.test pear-user "$2" '
                "app.0123456789abcdef.js app.fedcba9876543210.css "
                "images/logo.2222222222222222.svg"
            )
            environment = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "FAKE_CURL_LOG": str(curl_log),
            }
            command = [
                "bash",
                "-c",
                script,
                "bash",
                str(REPO_ROOT / "tools/pear_deploy/deploy_common.sh"),
                str(password_file),
            ]
            valid = subprocess.run(
                command, check=False, capture_output=True, text=True, env=environment
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertIn("static_cache=valid", valid.stdout)
            self.assertNotIn("dummy-secret", curl_log.read_text(encoding="utf-8"))

            missing_header = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                env={**environment, "FAKE_CURL_OMIT_NOSNIFF": "1"},
            )
            self.assertNotEqual(missing_header.returncode, 0)
            self.assertIn("missing nosniff", missing_header.stderr)

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
