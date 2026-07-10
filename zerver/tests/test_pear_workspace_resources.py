from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import orjson

from zerver.lib.pear_workspace_index import WorkspaceIndex, WorkspacePath
from zerver.lib.pear_workspace_io import (
    WorkspaceFile,
    WorkspaceVersionConflictError,
    WorkspaceWriteResult,
)
from zerver.lib.pear_workspace_resources import (
    PEAR_WORKSPACE_TEXT_MAX_BYTES,
    WorkspaceSnapshot,
    clear_workspace_snapshot_cache,
    read_workspace_node_file,
    record_pending_workspace_mutation,
    write_workspace_node_file,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models.bots import PearWorkspaceNode
from zerver.models.users import UserProfile


class PearWorkspaceResourceEndpointTest(ZulipTestCase):
    def setUp(self) -> None:
        super().setUp()
        clear_workspace_snapshot_cache()

    def make_workspace(self, base: Path) -> Path:
        repo = base / "repo"
        repo.mkdir()
        git = ["git", "-C", str(repo)]
        subprocess.run([*git, "init", "--quiet"], check=True, timeout=10)
        subprocess.run([*git, "checkout", "-b", "topic", "--quiet"], check=True, timeout=10)
        (repo / "README.md").write_bytes(b"\xef\xbb\xbf# Notes\r\n\r\ntrailing  \r\n")
        (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
        (repo / ".env").write_text("SECRET=hidden\n", encoding="utf-8")
        (repo / "raw.bin").write_bytes(b"\x00\xff\x10binary")
        docs = repo / "docs"
        docs.mkdir()
        (docs / "guide.md").write_text("guide\n", encoding="utf-8")
        subprocess.run([*git, "add", "-A"], check=True, timeout=10)
        subprocess.run(
            [
                *git,
                "-c",
                "user.email=pear@example.com",
                "-c",
                "user.name=Pear",
                "commit",
                "--quiet",
                "-m",
                "initial",
            ],
            check=True,
            timeout=10,
        )
        return repo

    def make_node(
        self,
        user: UserProfile,
        stream_name: str,
        repo: Path,
    ) -> PearWorkspaceNode:
        return PearWorkspaceNode.objects.create(
            realm=user.realm,
            stream_name=stream_name,
            topic_name="workspace",
            workdir=str(repo),
            branch_name="topic",
        )

    def descriptor(self, node: PearWorkspaceNode) -> dict[str, object]:
        result = self.client_get(
            f"/json/pear/workspaces/{node.uuid}",
            intentionally_undocumented=True,
        )
        return self.assert_json_success(result)["pear_workspace"]

    def tree(
        self,
        node: PearWorkspaceNode,
        *,
        directory_id: str | None = None,
        cursor: str | None = None,
        page_size: int = 200,
    ) -> dict[str, object]:
        parameters: dict[str, object] = {"page_size": page_size}
        if directory_id is not None:
            parameters["directory_id"] = directory_id
        if cursor is not None:
            parameters["cursor"] = cursor
        result = self.client_get(
            f"/json/pear/workspaces/{node.uuid}/tree",
            parameters,
            intentionally_undocumented=True,
        )
        return self.assert_json_success(result)["pear_workspace_tree"]

    def test_descriptor_tree_and_search_are_complete_opaque_and_relative(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            descriptor_result = self.client_get(
                f"/json/pear/workspaces/{node.uuid}",
                intentionally_undocumented=True,
            )
            descriptor = self.assert_json_success(descriptor_result)["pear_workspace"]
            root = self.tree(node, page_size=2)
            rest = self.tree(node, cursor=root["next_cursor"], page_size=20)
            search_result = self.client_get(
                f"/json/pear/workspaces/{node.uuid}/search",
                {"q": "guide", "limit": 10},
                intentionally_undocumented=True,
            )

        self.assertNotIn(str(repo), descriptor_result.content.decode())
        self.assertEqual(descriptor["uuid"], str(node.uuid))
        self.assertEqual(descriptor["default_path"], "README.md")
        self.assertEqual(descriptor["default_entry"]["path"], "README.md")
        self.assertEqual(descriptor["root"]["path"], "")
        self.assertFalse(descriptor["capabilities"]["create_entry"])
        entries = [*root["entries"], *rest["entries"]]
        paths = {entry["path"] for entry in entries}
        self.assertEqual(paths, {".gitignore", "README.md", "docs", "raw.bin"})
        self.assertNotIn(".env", paths)
        for entry in entries:
            self.assertNotEqual(entry["path"], entry["entry_id"])
            self.assertNotIn("/", entry["entry_id"])
        search = self.assert_json_success(search_result)["pear_workspace_search"]
        self.assertEqual([entry["path"] for entry in search["entries"]], ["docs/guide.md"])
        self.assertEqual(
            [ancestor["path"] for ancestor in search["entries"][0]["ancestors"]],
            ["", "docs"],
        )

    def test_descriptor_does_not_create_a_missing_readme(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            subprocess.run(
                ["git", "-C", str(repo), "rm", "--quiet", "README.md"],
                check=True,
                timeout=10,
            )
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)

            self.assertIsNone(descriptor["default_path"])
            self.assertIsNone(descriptor["default_entry"])
            self.assertFalse((repo / "README.md").exists())

    def test_snapshot_indexes_real_empty_directories_without_hidden_or_ignored_entries(
        self,
    ) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            (repo / "empty").mkdir()
            (repo / ".hidden-empty").mkdir()
            (repo / "ignored-empty").mkdir()
            with (repo / ".gitignore").open("a", encoding="utf-8") as ignore_file:
                ignore_file.write("ignored-empty/\n")
            node = self.make_node(user, stream.name, repo)
            entries = self.tree(node)["entries"]

        by_path = {entry["path"]: entry for entry in entries}
        self.assertEqual(by_path["empty"]["kind"], "directory")
        self.assertFalse(by_path["empty"]["has_children"])
        self.assertNotIn(".hidden-empty", by_path)
        self.assertNotIn("ignored-empty", by_path)

    def test_text_get_and_raw_preserve_exact_bytes_and_reject_binary_editing(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            readme_id = descriptor["default_entry"]["entry_id"]
            root = self.tree(node)
            raw_id = next(
                entry["entry_id"] for entry in root["entries"] if entry["path"] == "raw.bin"
            )
            text_result = self.client_get(
                f"/json/pear/workspaces/{node.uuid}/file",
                {"entry_id": readme_id},
                intentionally_undocumented=True,
            )
            binary_result = self.client_get(
                f"/json/pear/workspaces/{node.uuid}/file",
                {"entry_id": raw_id},
                intentionally_undocumented=True,
            )
            raw_result = self.client_get(
                f"/json/pear/workspaces/{node.uuid}/raw",
                {"entry_id": raw_id, "download": orjson.dumps(True).decode()},
                intentionally_undocumented=True,
            )

        text = self.assert_json_success(text_result)["pear_workspace_file"]
        self.assertEqual(text["content"].encode(), b"\xef\xbb\xbf# Notes\r\n\r\ntrailing  \r\n")
        self.assertTrue(text["editable"])
        self.assertTrue(text["rich_mode_eligible"])
        self.assert_json_error(
            binary_result,
            "Workspace file is not editable UTF-8 text.",
            status_code=415,
        )
        self.assertEqual(orjson.loads(binary_result.content)["error_code"], "unsupported_file")
        self.assertEqual(raw_result.content, b"\x00\xff\x10binary")
        self.assertEqual(raw_result["Content-Type"], "application/octet-stream")
        self.assertTrue(raw_result["Content-Disposition"].startswith("attachment"))
        self.assertEqual(raw_result["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", raw_result["Content-Security-Policy"])

    def test_warm_file_get_reuses_index_without_git_enumeration(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            with patch("zerver.lib.pear_workspace_resources._enumerate_workspace") as enumerate:
                result = self.client_get(
                    f"/json/pear/workspaces/{node.uuid}/file",
                    {"entry_id": descriptor["default_entry"]["entry_id"]},
                    intentionally_undocumented=True,
                )

        self.assert_json_success(result)
        enumerate.assert_not_called()

    def test_put_bumps_revision_conflicts_and_retries_idempotently(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            entry = descriptor["default_entry"]
            get_result = self.client_get(
                f"/json/pear/workspaces/{node.uuid}/file",
                {"entry_id": entry["entry_id"]},
                intentionally_undocumented=True,
            )
            original = self.assert_json_success(get_result)["pear_workspace_file"]
            desired = "# Exact\n\nno final newline  "
            request = {
                "entry_id": entry["entry_id"],
                "expected_path": "README.md",
                "content": desired,
                "base_version": original["version"],
                "workspace_revision": descriptor["workspace_revision"],
                "edit_revision": 7,
            }
            mismatched_path_result = self.client_put(
                f"/json/pear/workspaces/{node.uuid}/file",
                {**request, "expected_path": "docs/guide.md"},
                intentionally_undocumented=True,
            )
            self.assertEqual((repo / "README.md").read_bytes(), original["content"].encode())
            put_result = self.client_put(
                f"/json/pear/workspaces/{node.uuid}/file",
                request,
                intentionally_undocumented=True,
            )
            saved = self.assert_json_success(put_result)["pear_workspace_file"]
            node.refresh_from_db()
            revision_after_save = node.content_revision
            retry_result = self.client_put(
                f"/json/pear/workspaces/{node.uuid}/file",
                request,
                intentionally_undocumented=True,
            )
            retry = self.assert_json_success(retry_result)["pear_workspace_file"]
            node.refresh_from_db()
            stale_result = self.client_put(
                f"/json/pear/workspaces/{node.uuid}/file",
                {**request, "content": "must not land", "edit_revision": 8},
                intentionally_undocumented=True,
            )
            remote_content = "# Remote\n"
            (repo / "README.md").write_text(remote_content, encoding="utf-8")
            conflict_result = self.client_put(
                f"/json/pear/workspaces/{node.uuid}/file",
                {
                    "entry_id": saved["entry_id"],
                    "expected_path": "README.md",
                    "content": "# Local\n",
                    "base_version": saved["version"],
                    "workspace_revision": saved["workspace_revision"],
                    "edit_revision": 9,
                },
                intentionally_undocumented=True,
            )
            conflict_descriptor = self.descriptor(node)

            self.assertEqual((repo / "README.md").read_text(), remote_content)

        self.assertEqual(revision_after_save, descriptor["workspace_revision"] + 1)
        self.assertEqual(node.content_revision, revision_after_save)
        self.assertEqual(saved["content"], desired)
        self.assertEqual(saved["edit_revision"], 7)
        self.assertNotEqual(saved["entry_id"], entry["entry_id"])
        self.assertEqual(retry["entry_id"], saved["entry_id"])
        self.assertEqual(retry["workspace_revision"], saved["workspace_revision"])
        self.assertEqual(orjson.loads(mismatched_path_result.content)["error_code"], "stale_entry")
        self.assert_json_error(
            stale_result,
            "Workspace changed; refresh before saving.",
            status_code=409,
        )
        self.assertEqual(orjson.loads(stale_result.content)["error_code"], "stale_workspace")
        self.assert_json_error(
            conflict_result,
            "Workspace file changed; reload before saving.",
            status_code=409,
        )
        conflict = orjson.loads(conflict_result.content)
        self.assertEqual(conflict["error_code"], "version_conflict")
        self.assertEqual(conflict["remote_file"]["content"], remote_content)
        self.assertEqual(
            conflict["workspace_revision"],
            saved["workspace_revision"] + 1,
        )
        self.assertEqual(
            conflict["remote_file"]["entry_id"],
            conflict_descriptor["default_entry"]["entry_id"],
        )
        self.assertNotEqual(conflict["remote_file"]["entry_id"], saved["entry_id"])

    def test_idempotent_write_repairs_missed_content_revision_bump(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            entry = descriptor["default_entry"]
            get_result = self.client_get(
                f"/json/pear/workspaces/{node.uuid}/file",
                {"entry_id": entry["entry_id"]},
                intentionally_undocumented=True,
            )
            original = self.assert_json_success(get_result)["pear_workspace_file"]
            desired = b"saved before database bump\n"
            (repo / "README.md").write_bytes(desired)
            record_pending_workspace_mutation(
                actor_id=user.id,
                workspace_uuid=str(node.uuid),
                requested_revision=descriptor["workspace_revision"],
                entry_id=entry["entry_id"],
                expected_path="README.md",
                base_version=original["version"],
                content=desired,
                edit_revision=2,
                result_version=hashlib.sha256(desired).hexdigest(),
            )
            result = self.client_put(
                f"/json/pear/workspaces/{node.uuid}/file",
                {
                    "entry_id": entry["entry_id"],
                    "expected_path": "README.md",
                    "content": desired.decode(),
                    "base_version": original["version"],
                    "workspace_revision": descriptor["workspace_revision"],
                    "edit_revision": 2,
                },
                intentionally_undocumented=True,
            )
            payload = self.assert_json_success(result)["pear_workspace_file"]
            node.refresh_from_db()

        self.assertEqual(node.content_revision, descriptor["workspace_revision"] + 1)
        self.assertEqual(payload["workspace_revision"], node.content_revision)
        self.assertEqual(payload["version"], hashlib.sha256(desired).hexdigest())

    def test_matching_replacement_cannot_masquerade_as_idempotent_retry(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            # Start dirty so delete/recreate keeps the same Git status and
            # exercises the receipt gate rather than snapshot invalidation.
            (repo / "README.md").write_bytes(b"old dirty bytes\n")
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            entry = descriptor["default_entry"]
            get_result = self.client_get(
                f"/json/pear/workspaces/{node.uuid}/file",
                {"entry_id": entry["entry_id"]},
                intentionally_undocumented=True,
            )
            original = self.assert_json_success(get_result)["pear_workspace_file"]
            delayed_content = b"delayed request bytes\n"
            replacement = repo / ".replacement"
            replacement.write_bytes(delayed_content)
            os.replace(replacement, repo / "README.md")
            put_result = self.client_put(
                f"/json/pear/workspaces/{node.uuid}/file",
                {
                    "entry_id": entry["entry_id"],
                    "expected_path": "README.md",
                    "content": delayed_content.decode(),
                    "base_version": original["version"],
                    "workspace_revision": descriptor["workspace_revision"],
                    "edit_revision": 3,
                },
                intentionally_undocumented=True,
            )
            old_id_read = self.client_get(
                f"/json/pear/workspaces/{node.uuid}/file",
                {"entry_id": entry["entry_id"]},
                intentionally_undocumented=True,
            )

            self.assertEqual((repo / "README.md").read_bytes(), delayed_content)

        self.assertEqual(orjson.loads(put_result.content)["error_code"], "stale_entry")
        self.assertEqual(orjson.loads(old_id_read.content)["error_code"], "stale_entry")

    def test_conflict_after_agent_mutation_uses_current_revision_bound_entry(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            entry = descriptor["default_entry"]
            get_result = self.client_get(
                f"/json/pear/workspaces/{node.uuid}/file",
                {"entry_id": entry["entry_id"]},
                intentionally_undocumented=True,
            )
            original = self.assert_json_success(get_result)["pear_workspace_file"]
            remote_data = b"agent mutation\n"
            remote_file = WorkspaceFile(
                data=remote_data,
                version=hashlib.sha256(remote_data).hexdigest(),
                size=len(remote_data),
                mode=0o600,
            )

            def race_write(
                _node: PearWorkspaceNode,
                _path: str,
                _data: bytes,
                *,
                expected_version: str,
            ) -> WorkspaceWriteResult:
                self.assertEqual(expected_version, original["version"])
                (repo / "README.md").write_bytes(remote_data)
                PearWorkspaceNode.objects.filter(id=node.id).update(content_revision=1)
                raise WorkspaceVersionConflictError(
                    "agent won the race",
                    current=remote_file,
                )

            with patch(
                "zerver.views.pear_workspace.write_workspace_node_file",
                side_effect=race_write,
            ):
                conflict_result = self.client_put(
                    f"/json/pear/workspaces/{node.uuid}/file",
                    {
                        "entry_id": entry["entry_id"],
                        "expected_path": "README.md",
                        "content": "local mutation\n",
                        "base_version": original["version"],
                        "workspace_revision": descriptor["workspace_revision"],
                        "edit_revision": 4,
                    },
                    intentionally_undocumented=True,
                )
            current_descriptor = self.descriptor(node)

        conflict = orjson.loads(conflict_result.content)
        self.assertEqual(conflict["error_code"], "version_conflict")
        self.assertEqual(conflict["workspace_revision"], 1)
        self.assertEqual(conflict["remote_file"]["content"], remote_data.decode())
        self.assertNotEqual(conflict["remote_file"]["entry_id"], entry["entry_id"])
        self.assertEqual(
            conflict["remote_file"]["entry_id"],
            current_descriptor["default_entry"]["entry_id"],
        )

    def test_refresh_invalidates_ids_and_cursor_without_creating_readme(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            first = self.tree(node, page_size=1)
            old_entry_id = first["entries"][0]["entry_id"]
            old_cursor = first["next_cursor"]
            (repo / "new.txt").write_text("new\n", encoding="utf-8")
            refresh_result = self.client_post(
                f"/json/pear/workspaces/{node.uuid}/refresh",
                intentionally_undocumented=True,
            )
            refresh = self.assert_json_success(refresh_result)["pear_workspace_refresh"]
            stale_cursor_result = self.client_get(
                f"/json/pear/workspaces/{node.uuid}/tree",
                {"cursor": old_cursor, "page_size": 1},
                intentionally_undocumented=True,
            )
            stale_entry_result = self.client_get(
                f"/json/pear/workspaces/{node.uuid}/file",
                {"entry_id": old_entry_id},
                intentionally_undocumented=True,
            )
            second_refresh_result = self.client_post(
                f"/json/pear/workspaces/{node.uuid}/refresh",
                intentionally_undocumented=True,
            )
            second_refresh = self.assert_json_success(second_refresh_result)[
                "pear_workspace_refresh"
            ]

        self.assertTrue(refresh["changed"])
        self.assertEqual(refresh["workspace"]["workspace_revision"], 1)
        self.assertFalse(second_refresh["changed"])
        self.assertEqual(second_refresh["workspace"]["workspace_revision"], 1)
        self.assert_json_error(
            stale_cursor_result,
            "Workspace tree cursor is stale or invalid.",
            status_code=409,
        )
        self.assertEqual(orjson.loads(stale_cursor_result.content)["error_code"], "stale_cursor")
        self.assertEqual(orjson.loads(stale_entry_result.content)["error_code"], "stale_entry")

    def test_refresh_detects_repeated_change_to_already_dirty_tracked_file(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            (repo / "README.md").write_text("first dirty version\n", encoding="utf-8")
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            (repo / "README.md").write_text("second dirty version\n", encoding="utf-8")
            refresh_result = self.client_post(
                f"/json/pear/workspaces/{node.uuid}/refresh",
                intentionally_undocumented=True,
            )
            refresh = self.assert_json_success(refresh_result)["pear_workspace_refresh"]

        self.assertTrue(refresh["changed"])
        self.assertEqual(
            refresh["workspace"]["workspace_revision"],
            descriptor["workspace_revision"] + 1,
        )

    def test_workspace_uuid_still_requires_channel_access(self) -> None:
        owner = self.example_user("iago")
        outsider = self.example_user("hamlet")
        private_stream = self.make_stream("workspace-private", invite_only=True)
        self.subscribe(owner, private_stream.name)
        self.login_user(outsider)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(owner, private_stream.name, repo)
            result = self.client_get(
                f"/json/pear/workspaces/{node.uuid}",
                intentionally_undocumented=True,
            )

        self.assert_json_error(result, "Invalid channel name 'workspace-private'")

    def test_isolated_nodes_route_reads_and_writes_through_helper(self) -> None:
        username = "pear-0123456789abcdef01234567"
        data = b"hello\n"
        file = WorkspaceFile(
            data=data,
            version=hashlib.sha256(data).hexdigest(),
            size=len(data),
            mode=0o600,
        )
        node = PearWorkspaceNode(
            workdir="/canonical/workspace",
            isolation_username=username,
        )
        with (
            patch(
                "zerver.lib.pear_workspace_resources.read_workspace_file_via_helper",
                return_value=file,
            ) as helper_read,
            patch(
                "zerver.lib.pear_workspace_resources.atomic_write_workspace_file_via_helper",
                return_value=WorkspaceWriteResult(file=file, changed=False, created=False),
            ) as helper_write,
        ):
            self.assertEqual(
                read_workspace_node_file(node, "README.md", max_bytes=100),
                file,
            )
            self.assertEqual(
                write_workspace_node_file(
                    node,
                    "README.md",
                    data,
                    expected_version=file.version,
                ).file,
                file,
            )

        helper_read.assert_called_once_with(
            "/canonical/workspace",
            "README.md",
            max_bytes=100,
            isolation_username=username,
        )
        helper_write.assert_called_once_with(
            "/canonical/workspace",
            "README.md",
            data,
            max_bytes=PEAR_WORKSPACE_TEXT_MAX_BYTES,
            expected_version=file.version,
            create=False,
            isolation_username=username,
        )


class PearWorkspaceResourcePayloadTest(ZulipTestCase):
    def test_snapshot_ids_do_not_embed_absolute_paths(self) -> None:
        paths = (WorkspacePath("README.md"), WorkspacePath("src/main.py"))
        index = WorkspaceIndex.build(
            workspace_uuid="12345678-1234-5678-1234-567812345678",
            revision=4,
            paths=paths,
            secret=b"resource-payload-test-secret-32bytes",
        )
        snapshot = WorkspaceSnapshot(
            workspace_uuid="12345678-1234-5678-1234-567812345678",
            revision=4,
            paths=paths,
            index=index,
            fingerprint="f" * 64,
            branch="main",
            head="a" * 40,
            dirty_count=0,
        )

        self.assertNotIn("/", snapshot.index.entry_for_path("README.md").entry_id)
