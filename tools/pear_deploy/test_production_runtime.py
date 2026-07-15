from __future__ import annotations

import argparse
import hashlib
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

from tools.pear_deploy import runtime_config, workspace_helper_runtime
from tools.pear_deploy.asset_reuse_policy import asset_rebuild_trigger
from tools.pear_deploy.deployment_capacity import GIB, estimate_capacity
from tools.pear_deploy.deployment_record import (
    DeploymentRecordError,
    atomic_write_deployment_record,
)
from tools.pear_deploy.freeze_asset_build import AssetFreezeError, freeze_asset_build
from tools.pear_deploy.materialize_emoji_assets import (
    EmojiMaterializationError,
    materialize_generated_emoji,
)
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
from tools.pear_deploy.workspace_helper_runtime import (
    HELPER_MODE,
    HelperSource,
    WorkspaceHelperDeploymentError,
    atomic_install_helper,
    inspect_installed_helper,
    load_expected_helper,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_HASH = "$2b$12$" + "A" * 53
MATCHING_TEST_HASH = "$2b$04$fRycSErhIEsfAHfYRqtIDuSu8dmFES90MoaYCROKQo7yj7HaG2d3i"


class AssetReusePolicyTest(unittest.TestCase):
    def test_backend_only_pear_changes_reuse_assets(self) -> None:
        self.assertIsNone(
            asset_rebuild_trigger(
                [
                    "zerver/lib/pear_workspace_io.py",
                    "zerver/views/pear_workspace.py",
                    "zerver/tests/test_pear_workspace_io.py",
                    "tools/pear_deploy/runtime/Caddyfile",
                    "tools/pear_deploy/update_remote.sh",
                ]
            )
        )

    def test_unknown_or_frontend_change_forces_rebuild(self) -> None:
        self.assertEqual(
            asset_rebuild_trigger(
                ["zerver/lib/pear.py", "web/src/pear_panel.ts", "zerver/tests/test_pear.py"]
            ),
            "web/src/pear_panel.ts",
        )
        self.assertEqual(
            asset_rebuild_trigger(["tools/pear_deploy/production_assets.py"]),
            "tools/pear_deploy/production_assets.py",
        )


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
                return_value=SimpleNamespace(free=7 * GIB),
            ),
        ):
            estimate = estimate_capacity(
                remote_path=remote_path,
                git_common_dir=Path("/remote/git"),
                pear_skills_dir=Path("/remote/skills"),
                snapshot_root=Path("/remote/snapshots"),
                asset_build_dir=Path("/remote/asset-build"),
                build_swap_bytes=2 * GIB,
                runtime_paths=[Path("/etc/runtime")],
            )

        self.assertEqual(estimate.snapshot_bytes, 6_800)
        self.assertEqual(estimate.asset_build_bytes, 3 * GIB + 5_000)
        self.assertEqual(estimate.build_swap_bytes, 2 * GIB)
        self.assertEqual(estimate.safety_bytes, GIB)
        self.assertEqual(estimate.reclaimable_asset_build_bytes, 100)
        self.assertTrue(estimate.sufficient)

    def test_reused_assets_do_not_reserve_build_or_swap_space(self) -> None:
        with (
            patch("tools.pear_deploy.deployment_capacity.allocated_bytes", return_value=0),
            patch(
                "tools.pear_deploy.deployment_capacity.shutil.disk_usage",
                return_value=SimpleNamespace(free=2 * GIB),
            ),
        ):
            estimate = estimate_capacity(
                remote_path=Path("/remote/zulip"),
                git_common_dir=Path("/remote/git"),
                pear_skills_dir=Path("/remote/skills"),
                snapshot_root=Path("/remote/snapshots"),
                asset_build_dir=Path("/remote/asset-build"),
                build_swap_bytes=0,
                runtime_paths=[],
                build_assets=False,
            )

        self.assertEqual(estimate.asset_build_bytes, 0)
        self.assertEqual(estimate.build_swap_bytes, 0)
        self.assertTrue(estimate.sufficient)


class ProductionAssetsTest(unittest.TestCase):
    def make_fixture(self, root: Path) -> None:
        bundle_root = root / "prod-static/serve/webpack-bundles"
        bundle_root.mkdir(parents=True)
        chunks = {
            "app": ["app.0123456789abcdef.js", "app.fedcba9876543210.css"],
            "common": ["common.1111111111111111.js"],
            "dev-login": ["dev-login.2222222222222222.js"],
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
                {"webpack_bundles": 4, "static_manifest_paths": 2},
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

    def test_rejects_missing_dev_login_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.make_fixture(root)
            stats_path = root / "webpack-stats-production.json"
            stats = json.loads(stats_path.read_text())
            del stats["chunks"]["dev-login"]
            stats_path.write_text(json.dumps(stats))
            with self.assertRaisesRegex(AssetValidationError, "entrypoint is missing: dev-login"):
                validate_production_assets(root)


class RuntimeConfigTest(unittest.TestCase):
    def test_caddy_runtime_avoids_logging_the_auth_environment(self) -> None:
        service_override = (
            REPO_ROOT / "tools/pear_deploy/runtime/caddy-pear-basic-auth.conf"
        ).read_text(encoding="utf-8")

        self.assertIn("EnvironmentFile=/etc/pear/caddy-basic-auth.env", service_override)
        self.assertIn(
            "ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile", service_override
        )
        self.assertNotIn("--environ", service_override)

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
            pear_env.chmod(0o600)
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
            pear_env.chmod(0o600)
            with self.assertRaisesRegex(RuntimeConfigError, "auth is absent"):
                install(self.make_args(root))

    def test_install_bootstraps_hash_from_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pear_env = root / PEAR_ENV_PATH.relative_to("/")
            pear_env.parent.mkdir(parents=True)
            pear_env.write_text("EXTERNAL_HOST=pear.example.com\n")
            pear_env.chmod(0o600)
            args = self.make_args(root, bootstrap_auth_stdin=True)
            with patch("sys.stdin", io.StringIO(TEST_HASH + "\n")):
                install(args)
            auth_env = root / AUTH_ENV_PATH.relative_to("/")
            self.assertEqual(stat.S_IMODE(auth_env.stat().st_mode), 0o600)
            self.assertEqual(parse_auth_env(auth_env)["PEAR_CADDY_BASICAUTH_HASH"], TEST_HASH)

    def test_install_rejects_symlinked_pear_env_and_runtime_destination(self) -> None:
        for hostile_target in ("pear-env", "runtime-file"):
            with (
                self.subTest(hostile_target=hostile_target),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                root = Path(temporary_directory)
                victim = root / "victim"
                victim.write_text("do not read or overwrite\n", encoding="utf-8")
                victim.chmod(0o600)
                pear_env = root / PEAR_ENV_PATH.relative_to("/")
                pear_env.parent.mkdir(parents=True)
                if hostile_target == "pear-env":
                    pear_env.symlink_to(victim)
                else:
                    pear_env.write_text("EXTERNAL_HOST=pear.example.com\n", encoding="utf-8")
                    pear_env.chmod(0o600)
                    caddyfile = root / "etc/caddy/Caddyfile"
                    caddyfile.parent.mkdir(parents=True)
                    caddyfile.symlink_to(victim)
                auth_env = root / AUTH_ENV_PATH.relative_to("/")
                auth_env.parent.mkdir(parents=True)
                auth_env.write_text(
                    "PEAR_CADDY_FQDN=pear.example.com\n"
                    "PEAR_CADDY_BASICAUTH_USER=pear-user\n"
                    f"PEAR_CADDY_BASICAUTH_HASH={TEST_HASH}\n",
                    encoding="utf-8",
                )
                auth_env.chmod(0o600)

                with self.assertRaisesRegex(
                    RuntimeConfigError, "regular non-symlink|regular single-link"
                ):
                    install(self.make_args(root))
                self.assertEqual(victim.read_text(encoding="utf-8"), "do not read or overwrite\n")

    def test_runtime_file_reads_are_bounded_and_mutation_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            oversized = root / "oversized"
            oversized.write_bytes(b"x" * (runtime_config.MAX_RUNTIME_FILE_BYTES + 1))
            oversized.chmod(0o600)
            with self.assertRaisesRegex(RuntimeConfigError, "exceeds the size limit"):
                runtime_config._read_regular_file_nofollow(oversized, required_mode=0o600)

            changing = root / "changing"
            changing.write_bytes(b"stable")
            changing.chmod(0o600)
            real_fstat = os.fstat
            calls = 0

            def changed_fstat(descriptor: int) -> os.stat_result | SimpleNamespace:
                nonlocal calls
                result = real_fstat(descriptor)
                calls += 1
                if calls != 2:
                    return result
                return SimpleNamespace(
                    st_mode=result.st_mode,
                    st_nlink=result.st_nlink,
                    st_dev=result.st_dev,
                    st_ino=result.st_ino,
                    st_uid=result.st_uid,
                    st_gid=result.st_gid,
                    st_size=result.st_size,
                    st_ctime_ns=result.st_ctime_ns + 1,
                    st_mtime_ns=result.st_mtime_ns,
                )

            with (
                patch.object(runtime_config.os, "fstat", side_effect=changed_fstat),
                self.assertRaisesRegex(RuntimeConfigError, "changed while reading"),
            ):
                runtime_config._read_regular_file_nofollow(changing, required_mode=0o600)


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


class WorkspaceHelperRuntimeTest(unittest.TestCase):
    def test_preflight_command_is_inspection_only(self) -> None:
        expected_sha256 = "a" * 64
        installation = workspace_helper_runtime.HelperInstallation("absent", None, None, None, None)
        with (
            patch.object(
                workspace_helper_runtime,
                "inspect_installed_helper",
                return_value=installation,
            ) as inspect,
            patch.object(workspace_helper_runtime, "atomic_install_helper") as install,
            patch("builtins.print"),
        ):
            status = workspace_helper_runtime.main(
                [
                    "preflight",
                    "--destination",
                    "/usr/local/lib/pear-workspace-io",
                    "--expected-sha256",
                    expected_sha256,
                ]
            )

        self.assertEqual(status, 0)
        inspect.assert_called_once_with(
            Path("/usr/local/lib/pear-workspace-io"),
            expected_sha256=expected_sha256,
        )
        install.assert_not_called()

    def test_loads_only_the_exact_expected_helper_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "zerver/lib/pear_workspace_io.py"
            source_path.parent.mkdir(parents=True)
            source_path.write_bytes(b"committed helper\n")
            expected_sha256 = hashlib.sha256(b"committed helper\n").hexdigest()

            source = load_expected_helper(root, expected_sha256)
            self.assertEqual(source.payload, b"committed helper\n")
            self.assertEqual(source.sha256, expected_sha256)

            source_path.write_bytes(b"dirty helper\n")
            with self.assertRaisesRegex(
                WorkspaceHelperDeploymentError,
                "does not match the expected SHA-256",
            ):
                load_expected_helper(root, expected_sha256)

    def test_atomic_install_is_nonwritable_and_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / "pear-workspace-io"
            owner_uid = os.getuid()
            owner_gid = os.getgid()
            first_payload = b"first helper\n"
            first = HelperSource(
                payload=first_payload,
                sha256=hashlib.sha256(first_payload).hexdigest(),
            )

            absent = inspect_installed_helper(
                destination,
                expected_sha256=first.sha256,
                expected_uid=owner_uid,
                expected_gid=owner_gid,
            )
            self.assertEqual(absent.state, "absent")
            installed = atomic_install_helper(
                first,
                destination,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
            )
            self.assertEqual(installed.state, "current")
            self.assertEqual(destination.read_bytes(), first_payload)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), HELPER_MODE)
            self.assertFalse(stat.S_IMODE(destination.stat().st_mode) & 0o222)
            self.assertEqual(list(root.glob(".pear-workspace-io.tmp-*")), [])

            second_payload = b"second helper\n"
            second = HelperSource(
                payload=second_payload,
                sha256=hashlib.sha256(second_payload).hexdigest(),
            )
            replaced = atomic_install_helper(
                second,
                destination,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
            )
            self.assertEqual(replaced.state, "current")
            self.assertEqual(destination.read_bytes(), second_payload)
            self.assertEqual(list(root.glob(".pear-workspace-io.tmp-*")), [])

            with self.assertRaisesRegex(
                WorkspaceHelperDeploymentError,
                "does not match its SHA-256",
            ):
                atomic_install_helper(
                    HelperSource(payload=b"tampered", sha256=second.sha256),
                    destination,
                    owner_uid=owner_uid,
                    owner_gid=owner_gid,
                )

    def test_inspection_rejects_unsafe_destination_types_and_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / "pear-workspace-io"
            owner_uid = os.getuid()
            owner_gid = os.getgid()
            payload = b"helper\n"
            expected_sha256 = hashlib.sha256(payload).hexdigest()
            destination.write_bytes(payload)
            destination.chmod(0o755)
            with self.assertRaisesRegex(WorkspaceHelperDeploymentError, "mode 0555"):
                inspect_installed_helper(
                    destination,
                    expected_sha256=expected_sha256,
                    expected_uid=owner_uid,
                    expected_gid=owner_gid,
                )

            destination.unlink()
            target = root / "target"
            target.write_bytes(payload)
            destination.symlink_to(target)
            with self.assertRaisesRegex(WorkspaceHelperDeploymentError, "non-symlink"):
                inspect_installed_helper(
                    destination,
                    expected_sha256=expected_sha256,
                    expected_uid=owner_uid,
                    expected_gid=owner_gid,
                )

    def test_inspection_distinguishes_current_and_outdated_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / "pear-workspace-io"
            owner_uid = os.getuid()
            owner_gid = os.getgid()
            destination.write_bytes(b"old helper\n")
            destination.chmod(HELPER_MODE)
            old_sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
            new_sha256 = hashlib.sha256(b"new helper\n").hexdigest()

            current = inspect_installed_helper(
                destination,
                expected_sha256=old_sha256,
                expected_uid=owner_uid,
                expected_gid=owner_gid,
            )
            outdated = inspect_installed_helper(
                destination,
                expected_sha256=new_sha256,
                expected_uid=owner_uid,
                expected_gid=owner_gid,
            )
            self.assertEqual(current.state, "current")
            self.assertEqual(outdated.state, "outdated")
            self.assertEqual(outdated.sha256, old_sha256)

    def test_isolation_smoke_uses_the_production_sudo_protocol(self) -> None:
        response = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b'{"ok":true,"result":{"entries":[]}}',
            stderr=b"",
        )
        request = {
            "operation": "inspect",
            "root": "/srv/pear-workspaces/smoke",
            "entries": [],
            "skip_unsafe": False,
        }
        with patch(
            "tools.pear_deploy.workspace_helper_runtime.subprocess.run",
            return_value=response,
        ) as run:
            result = workspace_helper_runtime._run_smoke_request(
                "pear-" + "a" * 24,
                Path("/usr/local/lib/pear-workspace-io"),
                request,
            )

        self.assertEqual(result, {"entries": []})
        command = run.call_args.args[0]
        self.assertEqual(
            command,
            [
                "sudo",
                "-n",
                "-u",
                "pear-" + "a" * 24,
                "/usr/bin/env",
                "python3",
                "/usr/local/lib/pear-workspace-io",
                "--stdio",
            ],
        )
        self.assertEqual(
            json.loads(run.call_args.kwargs["input"]),
            request,
        )

    def test_runtime_snapshot_rollback_restores_present_and_removes_absent_helper(self) -> None:
        for initial_payload in (None, b"predeploy helper\n"):
            with (
                self.subTest(initial_state="absent" if initial_payload is None else "present"),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                root = Path(temporary_directory)
                snapshot = root / "snapshot"
                filesystem = root / "filesystem"
                helper = filesystem / "usr/local/lib/pear-workspace-io"
                helper.parent.mkdir(parents=True)
                snapshot.mkdir()
                paths_file = snapshot / "runtime-config.paths"
                paths_file.write_text("", encoding="utf-8")

                if initial_payload is not None:
                    helper.write_bytes(initial_payload)
                    helper.chmod(HELPER_MODE)
                    before = helper.stat()
                    paths_file.write_text(
                        "usr/local/lib/pear-workspace-io\n",
                        encoding="utf-8",
                    )
                    subprocess.run(
                        [
                            "tar",
                            "-C",
                            str(filesystem),
                            "-cpf",
                            str(snapshot / "runtime-config.tar"),
                            "-T",
                            str(paths_file),
                        ],
                        check=True,
                    )

                helper.unlink(missing_ok=True)
                helper.write_bytes(b"new deployed helper\n")
                helper.chmod(HELPER_MODE)

                # This is the exact remove-then-extract behavior guarded by
                # StaticRuntimeContractTest against update_remote.sh.
                helper.unlink()
                if paths_file.stat().st_size:
                    subprocess.run(
                        [
                            "tar",
                            "-C",
                            str(filesystem),
                            "-xpf",
                            str(snapshot / "runtime-config.tar"),
                        ],
                        check=True,
                    )

                if initial_payload is None:
                    self.assertFalse(helper.exists())
                else:
                    after = helper.stat()
                    self.assertEqual(helper.read_bytes(), initial_payload)
                    self.assertEqual(stat.S_IMODE(after.st_mode), stat.S_IMODE(before.st_mode))
                    self.assertEqual(after.st_uid, before.st_uid)
                    self.assertEqual(after.st_gid, before.st_gid)


class AssetBuildFreezeTest(unittest.TestCase):
    def make_build(self, root: Path) -> Path:
        build = root / "build"
        for relative in (
            "prod-static/serve/webpack-bundles",
            "static/generated",
            "static/webpack-bundles",
            "static/images/landing-page/hello/generated",
            "web/generated",
            "locale/en/LC_MESSAGES",
        ):
            (build / relative).mkdir(parents=True, exist_ok=True)
        files = {
            "prod-static/serve/webpack-bundles/app.js": b"app",
            "static/generated/generated.txt": b"generated",
            "static/webpack-bundles/app.js": b"bundle",
            "static/images/landing-page/hello/generated/hello.txt": b"hello",
            "web/generated/types.ts": b"types",
            "webpack-stats-production.json": b"{}",
            "staticfiles.json": b"{}",
            "locale/language_options.json": b"{}",
            "locale/language_name_map.json": b"{}",
            "locale/en/LC_MESSAGES/django.mo": b"mo",
            "locale/en/mobile.json": b"mobile",
            "locale/en/source.json": b"ignored",
        }
        for relative, payload in files.items():
            path = build / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            path.chmod(0o644)
        for directory, _, _ in os.walk(build):
            Path(directory).chmod(0o755)
        return build

    def test_freeze_copies_only_regular_generated_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            build = self.make_build(root)
            destination_parent = root / "frozen"
            destination_parent.mkdir(mode=0o700)
            frozen = freeze_asset_build(build, destination_parent)
            self.assertEqual(
                (frozen / "prod-static/serve/webpack-bundles/app.js").read_bytes(),
                b"app",
            )
            self.assertEqual(
                (frozen / "locale/en/LC_MESSAGES/django.mo").read_bytes(),
                b"mo",
            )
            self.assertEqual((frozen / "locale/en/mobile.json").read_bytes(), b"mobile")
            self.assertFalse((frozen / "locale/en/source.json").exists())

            ignored_locale_source = build / "locale/en/LC_MESSAGES/ignored.po"
            ignored_locale_source.write_bytes(b"source")
            os.link(
                ignored_locale_source,
                build / "locale/en/LC_MESSAGES/ignored-copy.po",
            )
            frozen = freeze_asset_build(build, destination_parent)
            self.assertFalse((frozen / "locale/en/LC_MESSAGES/ignored.po").exists())

    def test_freeze_rejects_symlinks_hardlinks_and_writable_entries(self) -> None:
        for hostile_kind in ("symlink", "hardlink", "writable"):
            with (
                self.subTest(hostile_kind=hostile_kind),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                root = Path(temporary_directory)
                build = self.make_build(root)
                target = build / "prod-static/serve/webpack-bundles/app.js"
                if hostile_kind == "symlink":
                    target.unlink()
                    target.symlink_to(root / "outside")
                elif hostile_kind == "hardlink":
                    os.link(target, build / "prod-static/serve/webpack-bundles/alias.js")
                else:
                    target.chmod(0o666)
                destination_parent = root / "frozen"
                destination_parent.mkdir(mode=0o700)
                with self.assertRaises(AssetFreezeError):
                    freeze_asset_build(build, destination_parent)
                self.assertEqual(list(destination_parent.iterdir()), [])


class EmojiAssetMaterializationTest(unittest.TestCase):
    def test_materializes_only_the_known_cache_link_without_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            build = root / "build"
            emoji_link = build / "static/generated/emoji"
            emoji_link.parent.mkdir(parents=True)
            cache_root = root / "emoji-cache"
            cache = cache_root / "cache-id/static/images"
            cache.mkdir(parents=True)
            (cache / "emoji.png").write_bytes(b"emoji")
            (cache / "alias.png").symlink_to("emoji.png")
            emoji_link.symlink_to(cache.parent)

            self.assertTrue(
                materialize_generated_emoji(build, emoji_cache_root=cache_root),
            )

            self.assertFalse(emoji_link.is_symlink())
            self.assertEqual((emoji_link / "images/alias.png").read_bytes(), b"emoji")
            self.assertFalse((emoji_link / "images/alias.png").is_symlink())
            self.assertFalse(materialize_generated_emoji(build, emoji_cache_root=cache_root))

    def test_rejects_an_emoji_link_outside_the_known_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            build = root / "build"
            emoji_link = build / "static/generated/emoji"
            emoji_link.parent.mkdir(parents=True)
            foreign = root / "foreign"
            foreign.mkdir()
            emoji_link.symlink_to(foreign)

            with self.assertRaisesRegex(EmojiMaterializationError, "must resolve inside"):
                materialize_generated_emoji(build, emoji_cache_root=root / "emoji-cache")
            self.assertTrue(emoji_link.is_symlink())


class DeploymentRecordTest(unittest.TestCase):
    def test_atomic_record_replaces_hostile_symlink_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / "pear-deployed-sha"
            victim = root / "victim"
            victim.write_bytes(b"do not overwrite\n")
            destination.symlink_to(victim)

            payload = b"abc123 2026-07-10T00:00:00Z\nrecord_version=2\n"
            atomic_write_deployment_record(
                destination,
                payload,
                owner_uid=os.getuid(),
                owner_gid=os.getgid(),
                parent_uid=os.getuid(),
                parent_gid=os.getgid(),
            )

            self.assertEqual(victim.read_bytes(), b"do not overwrite\n")
            self.assertFalse(destination.is_symlink())
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o644)

    def test_atomic_record_ignores_preplanted_predictable_temp_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / "pear-deployed-sha"
            victim = root / "victim"
            victim.write_bytes(b"safe\n")
            predictable = root / ".pear-deployed-sha.tmp"
            predictable.symlink_to(victim)

            atomic_write_deployment_record(
                destination,
                b"record\n",
                owner_uid=os.getuid(),
                owner_gid=os.getgid(),
                parent_uid=os.getuid(),
                parent_gid=os.getgid(),
            )

            self.assertEqual(victim.read_bytes(), b"safe\n")
            self.assertTrue(predictable.is_symlink())
            self.assertEqual(destination.read_bytes(), b"record\n")

    def test_atomic_record_rejects_symlink_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            real_parent = root / "real"
            real_parent.mkdir()
            linked_parent = root / "linked"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaisesRegex(DeploymentRecordError, "parent must be a real directory"):
                atomic_write_deployment_record(
                    linked_parent / "pear-deployed-sha",
                    b"record\n",
                    owner_uid=os.getuid(),
                    owner_gid=os.getgid(),
                    parent_uid=os.getuid(),
                    parent_gid=os.getgid(),
                )


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
        bootstrap = (REPO_ROOT / "tools/pear_deploy/bootstrap_remote.sh").read_text()
        visual_runtime = (REPO_ROOT / "tools/pear_deploy/provision_visual_runtime.sh").read_text()
        helper_runtime = (REPO_ROOT / "tools/pear_deploy/workspace_helper_runtime.py").read_text()
        backend_workflow = (REPO_ROOT / ".github/workflows/pear-backend.yml").read_text()
        self.assertIn("--production-assets", run_dev)
        self.assertIn(
            """runtornado_command = [
            "./manage.py",
            "runtornado",
            "--immediate-reloads",
            "--automated",
        ]""",
            run_dev,
        )
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
        self.assertIn("header_up -Authorization", caddyfile)
        self.assertIn("/etc/pear/caddy-basic-auth.env", caddy_drop_in)
        self.assertIn("ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile", caddy_drop_in)
        self.assertNotIn("--environ", caddy_drop_in)
        self.assertNotIn("127.0.0.1:9991", update)
        self.assertNotIn("127.0.0.1:9991", verify)
        self.assertIn("verify_caddy_https_runtime", update)
        self.assertIn("verify_caddy_https_runtime", verify)
        self.assertIn("checkout-index --force", update)
        self.assertIn("verify_remote_deployed_source_clean", update)
        self.assertIn("classify_production_asset_requirement", update)
        self.assertIn("asset_source_zulip_sha=", update)
        self.assertLess(
            update.rindex("classify_production_asset_requirement"),
            update.index('if [[ "$YES" != true ]]'),
        )
        self.assertLess(
            update.rindex("verify_remote_deployed_source_clean"),
            update.index('DEPLOYED_AT="$(date'),
        )
        self.assertIn("validate_local_caddy_auth_pair", update)
        self.assertIn("curl --config -", common)
        self.assertNotIn("curl --user", common)
        self.assertIn('"$app_status" != "200"', common)
        self.assertIn("--location --max-redirs 3", common)
        self.assertIn('"$status" != "301"', update)
        self.assertIn('"$AUTHENTICATED_SITE_CODE" == "200"', update)

        disk_preflight = "verify_remote_disk_capacity"
        self.assertLess(update.rindex(disk_preflight), update.index('if [[ "$YES" != true ]]'))
        self.assertLess(
            update.rindex(disk_preflight), update.index("Snapshotting the drained remote tree")
        )
        self.assertLess(
            update.rindex("verify_existing_remote_caddy_auth"),
            update.index("Snapshotting the drained remote tree"),
        )

        helper_path = "/usr/local/lib/pear-workspace-io"
        runtime_paths_start = common.index("PEAR_DEPLOY_RUNTIME_CONFIG_PATHS=(")
        runtime_paths_end = common.index("\n)", runtime_paths_start)
        self.assertIn(helper_path, common[runtime_paths_start:runtime_paths_end])
        self.assertIn('workspace_helper_runtime.py" install', bootstrap)
        self.assertIn("--smoke-existing-isolation-user", bootstrap)
        self.assertIn("preflight_remote_workspace_helper", update)
        self.assertIn("install_and_verify_remote_workspace_helper", update)
        self.assertIn("workspace_helper_sha256=", update)
        self.assertIn('workspace_helper_runtime.py" verify', verify)
        self.assertIn("os.replace(", helper_runtime)
        self.assertIn("os.fchown(", helper_runtime)
        self.assertIn("os.fchmod(", helper_runtime)
        self.assertIn("HELPER_MODE = 0o555", helper_runtime)
        self.assertIn('"operation": "inspect"', helper_runtime)
        self.assertIn('"operation": "read"', helper_runtime)
        self.assertLess(
            update.rindex("preflight_remote_workspace_helper"),
            update.index('if [[ "$YES" != true ]]'),
        )
        self.assertLess(
            update.rindex("build_remote_production_assets"),
            update.rindex("systemctl stop pear-zulip-dev"),
        )
        self.assertIn("$REMOTE_ASSET_BUILD_DIR/static/generated", update)
        self.assertLess(
            update.rindex("enable_remote_build_swap"),
            update.rindex("build_remote_production_assets"),
        )
        self.assertLess(
            update.rindex("build_remote_production_assets"),
            update.rindex("disable_remote_build_swap"),
        )
        self.assertLess(
            update.rindex("systemctl stop pear-zulip-dev"), update.rindex("wait_for_remote_drain")
        )
        self.assertLess(
            update.rindex("wait_for_remote_drain"),
            update.rindex("systemctl stop ${PREDEPLOY_WORKER_SERVICES[*]}"),
        )
        self.assertLess(
            update.rindex("systemctl stop ${PREDEPLOY_WORKER_SERVICES[*]}"),
            update.index("Snapshotting the drained remote tree"),
        )
        self.assertLess(
            update.rindex("SERVICE_RESTART_REQUIRED=true"),
            update.rindex("systemctl stop pear-zulip-dev"),
        )
        self.assertLess(
            update.index("Snapshotting the drained remote tree"),
            update.rindex("install_and_verify_remote_workspace_helper"),
        )
        self.assertLess(
            update.rindex("install_and_verify_remote_workspace_helper"),
            update.rindex("systemctl restart caddy ${PEAR_DEPLOY_SERVICES[*]}"),
        )
        for script in (update, verify, bootstrap, reset):
            self.assertIn("stage_remote_pear_deploy_bundle", script)
            self.assertNotIn(
                "python3 /home/zulipdev/zulip/tools/pear_deploy/runtime_config.py",
                script,
            )
            self.assertNotIn(
                "python3 /home/zulipdev/zulip/tools/pear_deploy/workspace_helper_runtime.py",
                script,
            )
        self.assertIn("--expected-sha256", update)
        self.assertIn("--expected-sha256", verify)
        self.assertIn("--expected-sha256", bootstrap)
        self.assertIn("deployment_record.py", update)
        self.assertNotIn("cat > $DEPLOY_RECORD_PATH.tmp", update)
        for suite in (
            "zerver.tests.test_pear_workspace_io",
            "zerver.tests.test_pear_workspace_index",
            "zerver.tests.test_pear_workspace_resources",
        ):
            self.assertIn(suite, update)
            self.assertIn(suite, backend_workflow)
        self.assertIn("tools.pear_deploy.test_production_runtime", backend_workflow)
        self.assertIn("-type d ! -perm -0005", visual_runtime)
        self.assertIn("-type f ! -perm -0004", visual_runtime)
        self.assertIn("-perm /022", visual_runtime)
        self.assertIn("-exec chmod a+rx,go-w {} +", visual_runtime)
        self.assertIn("-exec chmod a+r,go-w {} +", visual_runtime)

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

status = "200" if authenticated else "401"
if authenticated and url.endswith("/devlogin"):
    status = os.environ.get("FAKE_CURL_APP_STATUS", status)
headers = []
if authenticated and "/static/webpack-bundles/" in url:
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

            redirect = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                env={**environment, "FAKE_CURL_APP_STATUS": "301"},
            )
            self.assertNotEqual(redirect.returncode, 0)
            self.assertEqual(redirect.stderr.count("; retrying"), 3)
            self.assertIn("app check returned HTTP 301", redirect.stderr)

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
            "'pear_addressing 0 0 1' "
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

    def test_queue_validator_accepts_an_explicit_predeploy_subset(self) -> None:
        script = (
            "source tools/pear_deploy/deploy_common.sh; "
            "printf '%s\\n' "
            "'pear_worker 0 0 1' "
            "'pear_monitor_worker 0 0 1' "
            "'embedded_bots 0 0 1' "
            "'thumbnail 0 0 1' | "
            "validate_pear_deploy_queue_snapshot "
            "'pear_worker pear_monitor_worker embedded_bots thumbnail'"
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
