from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, TypedDict, cast
from unittest.mock import patch
from uuid import UUID

import orjson
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse, StreamingHttpResponse
from django.test.utils import CaptureQueriesContext
from typing_extensions import override

from zerver.lib.armada_workspace_index import WorkspaceIndex, WorkspacePath
from zerver.lib.armada_workspace_io import (
    WorkspaceDeleteResult,
    WorkspaceEntryIdentity,
    WorkspaceEntryMutationResult,
    WorkspaceFile,
    WorkspaceInspectedEntry,
    WorkspaceRawFileMetadata,
    WorkspaceVersionConflictError,
    WorkspaceWriteResult,
)
from zerver.lib.armada_workspace_resources import (
    ARMADA_WORKSPACE_RAW_MAX_BYTES,
    ARMADA_WORKSPACE_TEXT_MAX_BYTES,
    WorkspaceSnapshot,
    WorkspaceSnapshotError,
    _join_or_start_snapshot_build,
    _local_cache_get,
    _local_cache_put,
    _SnapshotBuild,
    clear_workspace_snapshot_cache,
    create_workspace_node_directory,
    create_workspace_node_file,
    delete_workspace_node_entry,
    get_workspace_snapshot,
    inspect_workspace_node_entries,
    open_workspace_node_file_stream,
    prepare_workspace_entry_mutation_receipt,
    read_workspace_node_file,
    record_pending_workspace_mutation,
    rename_workspace_node_entry,
    workspace_entry_mutation_lookup_payload,
    write_workspace_node_file,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models.bots import Workspace, WorkspaceEntryMutationReceipt
from zerver.models.users import UserProfile


class WorkspaceEntryPayload(TypedDict):
    entry_id: str
    path: str
    kind: str
    has_children: bool


class WorkspaceDescriptorPayload(TypedDict):
    workspace_revision: int
    default_path: str | None
    default_entry: WorkspaceEntryPayload | None
    dirty_count: int


class WorkspaceTreePayload(TypedDict):
    entries: list[WorkspaceEntryPayload]
    next_cursor: str | None


class WorkspaceMutationPayload(TypedDict):
    workspace_revision: int
    previous_entry_id: str | None
    entry: WorkspaceEntryPayload | None


class WorkspaceClientPut(Protocol):
    # client_put forwards this boolean through **extra despite its str-only annotation.
    def __call__(
        self,
        url: str,
        info: Mapping[str, object],
        *,
        intentionally_undocumented: bool,
    ) -> HttpResponse: ...


class ArmadaWorkspaceResourceEndpointTest(ZulipTestCase):
    @override
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
                "user.email=armada@example.com",
                "-c",
                "user.name=Onix",
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
    ) -> Workspace:
        return Workspace.objects.create(
            realm=user.realm,
            stream_name=stream_name,
            topic_name="workspace",
            workdir=str(repo),
            branch_name="topic",
        )

    def descriptor(self, node: Workspace) -> WorkspaceDescriptorPayload:
        result = self.client_get(
            f"/json/armada/workspaces/{node.uuid}",
            intentionally_undocumented=True,
        )
        return self.assert_json_success(result)["armada_workspace"]

    def tree(
        self,
        node: Workspace,
        *,
        directory_id: str | None = None,
        cursor: str | None = None,
        page_size: int = 200,
    ) -> WorkspaceTreePayload:
        parameters: dict[str, object] = {"page_size": page_size}
        if directory_id is not None:
            parameters["directory_id"] = directory_id
        if cursor is not None:
            parameters["cursor"] = cursor
        result = self.client_get(
            f"/json/armada/workspaces/{node.uuid}/tree",
            parameters,
            intentionally_undocumented=True,
        )
        return self.assert_json_success(result)["armada_workspace_tree"]

    def create_entry(
        self,
        node: Workspace,
        *,
        destination_path: str,
        kind: str,
        workspace_revision: int,
        content: str = "",
    ) -> WorkspaceMutationPayload:
        result = self.client_post(
            f"/json/armada/workspaces/{node.uuid}/entries",
            {
                "destination_path": destination_path,
                "kind": kind,
                "workspace_revision": workspace_revision,
                "content": content,
            },
            intentionally_undocumented=True,
        )
        return self.assert_json_success(result)["armada_workspace_mutation"]

    def rename_entry(
        self,
        node: Workspace,
        *,
        entry_id: str,
        expected_path: str,
        destination_path: str,
        workspace_revision: int,
    ) -> WorkspaceMutationPayload:
        result = self.client_patch(
            f"/json/armada/workspaces/{node.uuid}/entry",
            {
                "entry_id": entry_id,
                "expected_path": expected_path,
                "destination_path": destination_path,
                "workspace_revision": workspace_revision,
            },
            intentionally_undocumented=True,
        )
        return self.assert_json_success(result)["armada_workspace_mutation"]

    def delete_entry(
        self,
        node: Workspace,
        *,
        entry_id: str,
        expected_path: str,
        workspace_revision: int,
        recursive: bool = False,
        confirm_irreversible: bool = False,
    ) -> WorkspaceMutationPayload:
        result = self.client_delete(
            f"/json/armada/workspaces/{node.uuid}/entry",
            {
                "entry_id": entry_id,
                "expected_path": expected_path,
                "workspace_revision": workspace_revision,
                "recursive": orjson.dumps(recursive).decode(),
                "confirm_irreversible": orjson.dumps(confirm_irreversible).decode(),
            },
            intentionally_undocumented=True,
        )
        return self.assert_json_success(result)["armada_workspace_mutation"]

    def test_descriptor_tree_and_search_are_complete_opaque_and_relative(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            descriptor_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}",
                intentionally_undocumented=True,
            )
            descriptor = self.assert_json_success(descriptor_result)["armada_workspace"]
            root = self.tree(node, page_size=2)
            rest = self.tree(node, cursor=root["next_cursor"], page_size=20)
            search_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/search",
                {"q": "guide", "limit": 10},
                intentionally_undocumented=True,
            )

        self.assertNotIn(str(repo), descriptor_result.content.decode())
        self.assertRegex(
            descriptor_result.headers["Server-Timing"],
            r'armada_workspace;dur=[0-9.]+;desc="descriptor"',
        )
        self.assertIn(
            'armada_workspace_cache;desc="rebuild"', descriptor_result.headers["Server-Timing"]
        )
        self.assertGreaterEqual(
            int(descriptor_result.headers["X-Armada-Workspace-Operation-Count"]),
            1,
        )
        self.assertRegex(
            search_result.headers["Server-Timing"],
            r'armada_workspace;dur=[0-9.]+;desc="search"',
        )
        self.assertIn(
            'armada_workspace_cache;desc="local-hit"', search_result.headers["Server-Timing"]
        )
        self.assertEqual(descriptor["uuid"], str(node.uuid))
        self.assertEqual(descriptor["default_path"], "README.md")
        self.assertEqual(descriptor["default_entry"]["path"], "README.md")
        self.assertEqual(descriptor["root"]["path"], "")
        self.assertTrue(descriptor["capabilities"]["create_entry"])
        self.assertTrue(descriptor["capabilities"]["rename_entry"])
        self.assertTrue(descriptor["capabilities"]["delete_entry"])
        self.assertTrue(descriptor["capabilities"]["history"])
        self.assertEqual(
            descriptor["limits"]["raw_file_max_bytes"],
            ARMADA_WORKSPACE_RAW_MAX_BYTES,
        )
        entries = [*root["entries"], *rest["entries"]]
        paths = {entry["path"] for entry in entries}
        self.assertEqual(paths, {".gitignore", "README.md", "docs", "raw.bin"})
        self.assertNotIn(".env", paths)
        for entry in entries:
            self.assertNotEqual(entry["path"], entry["entry_id"])
            self.assertNotIn("/", entry["entry_id"])
        search = self.assert_json_success(search_result)["armada_workspace_search"]
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

    def test_snapshot_treats_an_untracked_embedded_repository_as_a_directory(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            embedded_repo = repo / "agent-1"
            embedded_repo.mkdir()
            subprocess.run(
                ["git", "-C", str(embedded_repo), "init", "--quiet"],
                check=True,
                timeout=10,
            )
            (embedded_repo / "notes.md").write_text("agent scratch\n", encoding="utf-8")
            node = self.make_node(user, stream.name, repo)

            descriptor = self.descriptor(node)
            entries = self.tree(node)["entries"]

        by_path = {entry["path"]: entry for entry in entries}
        self.assertEqual(by_path["agent-1"]["kind"], "directory")
        self.assertFalse(by_path["agent-1"]["has_children"])
        self.assertEqual(descriptor["dirty_count"], 1)

    def test_create_rename_delete_enforce_policy_revision_and_confirmation(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            with (repo / ".gitignore").open("a", encoding="utf-8") as ignore_file:
                ignore_file.write("private.txt\n")
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            revision = descriptor["workspace_revision"]
            assert isinstance(revision, int)

            hidden_result = self.client_post(
                f"/json/armada/workspaces/{node.uuid}/entries",
                {
                    "destination_path": ".secret",
                    "kind": "file",
                    "workspace_revision": revision,
                    "content": "secret",
                },
                intentionally_undocumented=True,
            )
            ignored_result = self.client_post(
                f"/json/armada/workspaces/{node.uuid}/entries",
                {
                    "destination_path": "private.txt",
                    "kind": "file",
                    "workspace_revision": revision,
                    "content": "secret",
                },
                intentionally_undocumented=True,
            )

            folder = self.create_entry(
                node,
                destination_path="notes",
                kind="directory",
                workspace_revision=revision,
            )
            folder_entry = folder["entry"]
            assert isinstance(folder_entry, dict)
            self.assertEqual(folder_entry["kind"], "directory")
            self.assertFalse(folder_entry["has_children"])
            file_mutation = self.create_entry(
                node,
                destination_path="draft.md",
                kind="file",
                workspace_revision=folder["workspace_revision"],
                content="scratch\n",
            )
            file_entry = file_mutation["entry"]
            assert isinstance(file_entry, dict)
            renamed = self.rename_entry(
                node,
                entry_id=file_entry["entry_id"],
                expected_path="draft.md",
                destination_path="notes/draft.md",
                workspace_revision=file_mutation["workspace_revision"],
            )
            renamed_entry = renamed["entry"]
            assert isinstance(renamed_entry, dict)
            self.assertNotEqual(renamed["previous_entry_id"], renamed_entry["entry_id"])
            self.assertEqual((repo / "notes/draft.md").read_text(), "scratch\n")

            stale_old_id = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/file",
                {"entry_id": file_entry["entry_id"]},
                intentionally_undocumented=True,
            )
            confirmation_result = self.client_delete(
                f"/json/armada/workspaces/{node.uuid}/entry",
                {
                    "entry_id": renamed_entry["entry_id"],
                    "expected_path": "notes/draft.md",
                    "workspace_revision": renamed["workspace_revision"],
                    "recursive": "false",
                    "confirm_irreversible": "false",
                },
                intentionally_undocumented=True,
            )
            deleted = self.delete_entry(
                node,
                entry_id=renamed_entry["entry_id"],
                expected_path="notes/draft.md",
                workspace_revision=renamed["workspace_revision"],
                confirm_irreversible=True,
            )
            current_tree = self.tree(node)
            self.assertFalse((repo / "notes/draft.md").exists())
            notes_entry = next(item for item in current_tree["entries"] if item["path"] == "notes")
            notes_tree = self.tree(node, directory_id=notes_entry["entry_id"])
            self.assertNotIn(
                "notes/draft.md",
                {item["path"] for item in notes_tree["entries"]},
            )

        self.assertEqual(orjson.loads(hidden_result.content)["error_code"], "hidden_destination")
        self.assertEqual(orjson.loads(ignored_result.content)["error_code"], "ignored_destination")
        self.assertEqual(orjson.loads(stale_old_id.content)["error_code"], "stale_entry")
        self.assertEqual(
            orjson.loads(confirmation_result.content)["error_code"],
            "confirmation_required",
        )
        self.assertIsNone(deleted["entry"])

    def test_history_is_bounded_cursor_signed_and_relative(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            git = ["git", "-C", str(repo)]
            for number in (2, 3):
                (repo / "README.md").write_text(f"revision {number}\n", encoding="utf-8")
                subprocess.run([*git, "add", "README.md"], check=True, timeout=10)
                subprocess.run(
                    [
                        *git,
                        "-c",
                        "user.email=armada@example.com",
                        "-c",
                        "user.name=Onix",
                        "commit",
                        "--quiet",
                        "-m",
                        f"revision {number}",
                    ],
                    check=True,
                    timeout=10,
                )
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            assert descriptor["default_entry"] is not None
            entry_id = descriptor["default_entry"]["entry_id"]
            first_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/history",
                {"entry_id": entry_id, "limit": 1},
                intentionally_undocumented=True,
            )
            first = self.assert_json_success(first_result)["armada_workspace_history"]
            second_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/history",
                {"entry_id": entry_id, "limit": 1, "cursor": first["next_cursor"]},
                intentionally_undocumented=True,
            )
            second = self.assert_json_success(second_result)["armada_workspace_history"]
            tampered_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/history",
                {
                    "entry_id": entry_id,
                    "limit": 1,
                    "cursor": f"{first['next_cursor']}x",
                },
                intentionally_undocumented=True,
            )

        self.assertEqual([commit["subject"] for commit in first["commits"]], ["revision 3"])
        self.assertEqual([commit["subject"] for commit in second["commits"]], ["revision 2"])
        self.assertNotIn(str(repo), first_result.content.decode())
        self.assertEqual(orjson.loads(tampered_result.content)["error_code"], "stale_cursor")

    def test_tracked_rename_and_delete_remove_phantom_source_paths(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            readme_entry = descriptor["default_entry"]
            assert readme_entry is not None
            renamed = self.rename_entry(
                node,
                entry_id=readme_entry["entry_id"],
                expected_path="README.md",
                destination_path="RENAMED.md",
                workspace_revision=descriptor["workspace_revision"],
            )
            renamed_entry = renamed["entry"]
            assert isinstance(renamed_entry, dict)
            after_rename = self.tree(node)
            after_rename_paths = {item["path"] for item in after_rename["entries"]}
            self.assertNotIn("README.md", after_rename_paths)
            self.assertIn("RENAMED.md", after_rename_paths)

            deleted = self.delete_entry(
                node,
                entry_id=renamed_entry["entry_id"],
                expected_path="RENAMED.md",
                workspace_revision=renamed["workspace_revision"],
                confirm_irreversible=True,
            )
            after_delete = self.tree(node)
            after_delete_paths = {item["path"] for item in after_delete["entries"]}

        self.assertIsNone(deleted["entry"])
        self.assertNotIn("README.md", after_delete_paths)
        self.assertNotIn("RENAMED.md", after_delete_paths)

    def test_delayed_put_cannot_follow_rename_or_delete_recreate(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            readme_entry = descriptor["default_entry"]
            assert readme_entry is not None
            read_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/file",
                {"entry_id": readme_entry["entry_id"]},
                intentionally_undocumented=True,
            )
            readme = self.assert_json_success(read_result)["armada_workspace_file"]
            delayed_rename_put = {
                "entry_id": readme_entry["entry_id"],
                "expected_path": "README.md",
                "content": "delayed rename write\n",
                "base_version": readme["version"],
                "workspace_revision": descriptor["workspace_revision"],
                "edit_revision": 90,
            }
            renamed = self.rename_entry(
                node,
                entry_id=readme_entry["entry_id"],
                expected_path="README.md",
                destination_path="RENAMED.md",
                workspace_revision=descriptor["workspace_revision"],
            )
            recreated = self.create_entry(
                node,
                destination_path="README.md",
                kind="file",
                workspace_revision=renamed["workspace_revision"],
                content="replacement after rename\n",
            )
            delayed_rename_result = cast(WorkspaceClientPut, self.client_put)(
                f"/json/armada/workspaces/{node.uuid}/file",
                delayed_rename_put,
                intentionally_undocumented=True,
            )

            scratch = self.create_entry(
                node,
                destination_path="scratch.txt",
                kind="file",
                workspace_revision=recreated["workspace_revision"],
                content="scratch original\n",
            )
            scratch_entry = scratch["entry"]
            assert isinstance(scratch_entry, dict)
            scratch_read_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/file",
                {"entry_id": scratch_entry["entry_id"]},
                intentionally_undocumented=True,
            )
            scratch_file = self.assert_json_success(scratch_read_result)["armada_workspace_file"]
            delayed_delete_put = {
                "entry_id": scratch_entry["entry_id"],
                "expected_path": "scratch.txt",
                "content": "delayed delete write\n",
                "base_version": scratch_file["version"],
                "workspace_revision": scratch["workspace_revision"],
                "edit_revision": 91,
            }
            deleted = self.delete_entry(
                node,
                entry_id=scratch_entry["entry_id"],
                expected_path="scratch.txt",
                workspace_revision=scratch["workspace_revision"],
                confirm_irreversible=True,
            )
            self.create_entry(
                node,
                destination_path="scratch.txt",
                kind="file",
                workspace_revision=deleted["workspace_revision"],
                content="replacement after delete\n",
            )
            delayed_delete_result = cast(WorkspaceClientPut, self.client_put)(
                f"/json/armada/workspaces/{node.uuid}/file",
                delayed_delete_put,
                intentionally_undocumented=True,
            )

            self.assertEqual((repo / "RENAMED.md").read_bytes(), readme["content"].encode())
            self.assertEqual((repo / "README.md").read_text(), "replacement after rename\n")
            self.assertEqual((repo / "scratch.txt").read_text(), "replacement after delete\n")

        self.assertEqual(
            orjson.loads(delayed_rename_result.content)["error_code"],
            "stale_workspace",
        )
        self.assertEqual(
            orjson.loads(delayed_delete_result.content)["error_code"],
            "stale_workspace",
        )

    def test_text_get_and_raw_preserve_exact_bytes_and_reject_binary_editing(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            assert descriptor["default_entry"] is not None
            readme_id = descriptor["default_entry"]["entry_id"]
            root = self.tree(node)
            raw_id = next(
                entry["entry_id"] for entry in root["entries"] if entry["path"] == "raw.bin"
            )
            text_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/file",
                {"entry_id": readme_id},
                intentionally_undocumented=True,
            )
            binary_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/file",
                {"entry_id": raw_id},
                intentionally_undocumented=True,
            )
            raw_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/raw",
                {"entry_id": raw_id, "download": orjson.dumps(True).decode()},
                intentionally_undocumented=True,
            )
            self.assertTrue(raw_result.streaming)
            raw_content = raw_result.getvalue()

        text = self.assert_json_success(text_result)["armada_workspace_file"]
        self.assertEqual(text["content"].encode(), b"\xef\xbb\xbf# Notes\r\n\r\ntrailing  \r\n")
        self.assertTrue(text["editable"])
        self.assertTrue(text["rich_mode_eligible"])
        self.assert_json_error(
            binary_result,
            "Workspace file is not editable UTF-8 text.",
            status_code=415,
        )
        self.assertEqual(orjson.loads(binary_result.content)["error_code"], "unsupported_file")
        self.assertEqual(raw_content, b"\x00\xff\x10binary")
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
            assert descriptor["default_entry"] is not None
            with patch("zerver.lib.armada_workspace_resources._enumerate_workspace") as enumerate:
                result = self.client_get(
                    f"/json/armada/workspaces/{node.uuid}/file",
                    {"entry_id": descriptor["default_entry"]["entry_id"]},
                    intentionally_undocumented=True,
                )

        self.assert_json_success(result)
        enumerate.assert_not_called()

    def test_raw_endpoint_streams_beyond_the_legacy_buffer_limit(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            size = 17 * 1024 * 1024 + 9
            with (repo / "raw.bin").open("wb") as raw_file:
                raw_file.seek(size - 1)
                raw_file.write(b"z")
            node = self.make_node(user, stream.name, repo)
            raw_entry = next(
                entry for entry in self.tree(node)["entries"] if entry["path"] == "raw.bin"
            )
            with patch("zerver.views.armada_workspace.read_workspace_node_file") as buffered_read:
                result = self.client_get(
                    f"/json/armada/workspaces/{node.uuid}/raw",
                    {"entry_id": raw_entry["entry_id"]},
                    intentionally_undocumented=True,
                )
                self.assertTrue(result.streaming)
                chunk_sizes = [len(chunk) for chunk in cast(StreamingHttpResponse, result)]
            with (repo / "raw.bin").open("rb") as raw_file:
                expected_etag = hashlib.file_digest(raw_file, "sha256").hexdigest()

        buffered_read.assert_not_called()
        self.assertEqual(sum(chunk_sizes), size)
        self.assertLessEqual(max(chunk_sizes), 64 * 1024)
        self.assertEqual(result["Content-Length"], str(size))
        self.assertEqual(result["ETag"], f'"{expected_etag}"')

    def test_raw_endpoint_rejects_files_above_the_finite_limit_without_iteration(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            with (repo / "oversized.bin").open("wb") as raw_file:
                raw_file.seek(ARMADA_WORKSPACE_RAW_MAX_BYTES)
                raw_file.write(b"z")
            node = self.make_node(user, stream.name, repo)
            raw_entry = next(
                entry for entry in self.tree(node)["entries"] if entry["path"] == "oversized.bin"
            )
            with patch(
                "zerver.lib.armada_workspace_io.WorkspaceRawFileStream.__next__"
            ) as stream_next:
                result = self.client_get(
                    f"/json/armada/workspaces/{node.uuid}/raw",
                    {"entry_id": raw_entry["entry_id"]},
                    intentionally_undocumented=True,
                )

        self.assertEqual(result.status_code, 413)
        self.assertEqual(orjson.loads(result.content)["error_code"], "file_too_large")
        self.assertEqual(
            orjson.loads(result.content)["max_bytes"],
            ARMADA_WORKSPACE_RAW_MAX_BYTES,
        )
        stream_next.assert_not_called()

    def test_entry_mutations_replay_exact_completed_receipts_once(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            revision = self.descriptor(node)["workspace_revision"]

            created = self.create_entry(
                node,
                destination_path="scratch.md",
                kind="file",
                workspace_revision=revision,
                content="receipt secret\n",
            )
            created_retry = self.create_entry(
                node,
                destination_path="scratch.md",
                kind="file",
                workspace_revision=revision,
                content="receipt secret\n",
            )
            self.assertEqual(created_retry, created)
            created_entry = created["entry"]
            assert isinstance(created_entry, dict)

            renamed = self.rename_entry(
                node,
                entry_id=created_entry["entry_id"],
                expected_path="scratch.md",
                destination_path="renamed.md",
                workspace_revision=created["workspace_revision"],
            )
            renamed_retry = self.rename_entry(
                node,
                entry_id=created_entry["entry_id"],
                expected_path="scratch.md",
                destination_path="renamed.md",
                workspace_revision=created["workspace_revision"],
            )
            self.assertEqual(renamed_retry, renamed)
            renamed_entry = renamed["entry"]
            assert isinstance(renamed_entry, dict)

            deleted = self.delete_entry(
                node,
                entry_id=renamed_entry["entry_id"],
                expected_path="renamed.md",
                workspace_revision=renamed["workspace_revision"],
                confirm_irreversible=True,
            )
            deleted_retry = self.delete_entry(
                node,
                entry_id=renamed_entry["entry_id"],
                expected_path="renamed.md",
                workspace_revision=renamed["workspace_revision"],
                confirm_irreversible=True,
            )

        self.assertEqual(deleted_retry, deleted)
        node.refresh_from_db()
        self.assertEqual(node.content_revision, revision + 3)
        receipts = list(
            WorkspaceEntryMutationReceipt.objects.filter(workspace_node=node).order_by("id")
        )
        self.assertEqual(len(receipts), 3)
        self.assertTrue(
            all(receipt.state == WorkspaceEntryMutationReceipt.COMPLETE for receipt in receipts)
        )
        serialized_receipts = orjson.dumps(
            [
                {
                    "request": receipt.request_payload,
                    "application": receipt.application_payload,
                    "result": receipt.result_payload,
                }
                for receipt in receipts
            ]
        )
        self.assertNotIn(b"receipt secret", serialized_receipts)
        self.assertIn(b"content_sha256", serialized_receipts)

    def test_applied_create_receipt_repairs_prepare_collision_once(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            revision = self.descriptor(node)["workspace_revision"]
            request_data = {
                "destination_path": "recovered.md",
                "kind": "file",
                "workspace_revision": revision,
                "content": "recover exactly\n",
            }
            with patch(
                "zerver.views.armada_workspace._finish_prepared_entry_mutation",
                side_effect=WorkspaceSnapshotError("simulated post-filesystem failure"),
            ):
                failed = self.client_post(
                    f"/json/armada/workspaces/{node.uuid}/entries",
                    request_data,
                    intentionally_undocumented=True,
                )

            self.assertEqual(failed.status_code, 503)
            self.assertEqual((repo / "recovered.md").read_text(), "recover exactly\n")
            node.refresh_from_db()
            self.assertEqual(node.content_revision, revision)
            receipt = WorkspaceEntryMutationReceipt.objects.get(workspace_node=node)
            self.assertEqual(receipt.state, WorkspaceEntryMutationReceipt.APPLIED)

            repaired = self.client_post(
                f"/json/armada/workspaces/{node.uuid}/entries",
                request_data,
                intentionally_undocumented=True,
            )
            mutation = self.assert_json_success(repaired)["armada_workspace_mutation"]

        self.assertEqual(mutation["workspace_revision"], revision + 1)
        node.refresh_from_db()
        receipt.refresh_from_db()
        self.assertEqual(node.content_revision, revision + 1)
        self.assertEqual(receipt.state, WorkspaceEntryMutationReceipt.COMPLETE)

    def test_applied_create_receipt_rejects_same_bytes_replacement(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            revision = self.descriptor(node)["workspace_revision"]
            request_data = {
                "destination_path": "replaced.md",
                "kind": "file",
                "workspace_revision": revision,
                "content": "same bytes\n",
            }
            with patch(
                "zerver.views.armada_workspace._finish_prepared_entry_mutation",
                side_effect=WorkspaceSnapshotError("simulated post-filesystem failure"),
            ):
                failed = self.client_post(
                    f"/json/armada/workspaces/{node.uuid}/entries",
                    request_data,
                    intentionally_undocumented=True,
                )
            self.assertEqual(failed.status_code, 503)
            created = repo / "replaced.md"
            created.unlink()
            created.write_text("same bytes\n")

            retry = self.client_post(
                f"/json/armada/workspaces/{node.uuid}/entries",
                request_data,
                intentionally_undocumented=True,
            )

        self.assertEqual(retry.status_code, 409)
        self.assertEqual(orjson.loads(retry.content)["error_code"], "stale_workspace")
        receipt = WorkspaceEntryMutationReceipt.objects.get(workspace_node=node)
        self.assertEqual(receipt.state, WorkspaceEntryMutationReceipt.CONFLICTED)

    def test_applied_directory_receipt_rejects_populated_directory(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            revision = self.descriptor(node)["workspace_revision"]
            request_data = {
                "destination_path": "created-directory",
                "kind": "directory",
                "workspace_revision": revision,
                "content": "",
            }
            with patch(
                "zerver.views.armada_workspace._finish_prepared_entry_mutation",
                side_effect=WorkspaceSnapshotError("simulated post-filesystem failure"),
            ):
                failed = self.client_post(
                    f"/json/armada/workspaces/{node.uuid}/entries",
                    request_data,
                    intentionally_undocumented=True,
                )
            self.assertEqual(failed.status_code, 503)
            (repo / "created-directory/attacker.txt").write_text("do not bless\n")

            retry = self.client_post(
                f"/json/armada/workspaces/{node.uuid}/entries",
                request_data,
                intentionally_undocumented=True,
            )

        self.assertEqual(retry.status_code, 409)
        self.assertEqual(orjson.loads(retry.content)["error_code"], "stale_workspace")
        receipt = WorkspaceEntryMutationReceipt.objects.get(workspace_node=node)
        self.assertEqual(receipt.state, WorkspaceEntryMutationReceipt.CONFLICTED)

    def test_applied_rename_and_delete_receipts_repair_once(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            revision = self.descriptor(node)["workspace_revision"]
            created = self.create_entry(
                node,
                destination_path="repair-source.md",
                kind="file",
                workspace_revision=revision,
                content="repair\n",
            )
            created_entry = created["entry"]
            assert isinstance(created_entry, dict)
            rename_request = {
                "entry_id": created_entry["entry_id"],
                "expected_path": "repair-source.md",
                "destination_path": "repair-destination.md",
                "workspace_revision": created["workspace_revision"],
            }
            with patch(
                "zerver.views.armada_workspace._finish_prepared_entry_mutation",
                side_effect=WorkspaceSnapshotError("simulated post-rename failure"),
            ):
                failed_rename = self.client_patch(
                    f"/json/armada/workspaces/{node.uuid}/entry",
                    rename_request,
                    intentionally_undocumented=True,
                )
            self.assertEqual(failed_rename.status_code, 503)
            rename_receipt = WorkspaceEntryMutationReceipt.objects.get(
                workspace_node=node,
                operation="rename",
            )
            self.assertEqual(rename_receipt.state, WorkspaceEntryMutationReceipt.APPLIED)
            repaired_rename_response = self.client_patch(
                f"/json/armada/workspaces/{node.uuid}/entry",
                rename_request,
                intentionally_undocumented=True,
            )
            repaired_rename = self.assert_json_success(repaired_rename_response)[
                "armada_workspace_mutation"
            ]
            renamed_entry = repaired_rename["entry"]
            assert isinstance(renamed_entry, dict)

            delete_request = {
                "entry_id": renamed_entry["entry_id"],
                "expected_path": "repair-destination.md",
                "workspace_revision": repaired_rename["workspace_revision"],
                "recursive": "false",
                "confirm_irreversible": "true",
            }
            with patch(
                "zerver.views.armada_workspace._finish_prepared_entry_mutation",
                side_effect=WorkspaceSnapshotError("simulated post-delete failure"),
            ):
                failed_delete = self.client_delete(
                    f"/json/armada/workspaces/{node.uuid}/entry",
                    delete_request,
                    intentionally_undocumented=True,
                )
            self.assertEqual(failed_delete.status_code, 503)
            delete_receipt = WorkspaceEntryMutationReceipt.objects.get(
                workspace_node=node,
                operation="delete",
            )
            self.assertEqual(delete_receipt.state, WorkspaceEntryMutationReceipt.APPLIED)
            repaired_delete_response = self.client_delete(
                f"/json/armada/workspaces/{node.uuid}/entry",
                delete_request,
                intentionally_undocumented=True,
            )
            repaired_delete = self.assert_json_success(repaired_delete_response)[
                "armada_workspace_mutation"
            ]

        self.assertIsNone(repaired_delete["entry"])
        rename_receipt.refresh_from_db()
        delete_receipt.refresh_from_db()
        self.assertEqual(rename_receipt.state, WorkspaceEntryMutationReceipt.COMPLETE)
        self.assertEqual(delete_receipt.state, WorkspaceEntryMutationReceipt.COMPLETE)

    def test_abandoned_applied_receipt_is_reconciled_by_descriptor(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            revision = self.descriptor(node)["workspace_revision"]
            request_data = {
                "destination_path": "abandoned.md",
                "kind": "file",
                "workspace_revision": revision,
                "content": "recover without request replay\n",
            }
            with patch(
                "zerver.views.armada_workspace._finish_prepared_entry_mutation",
                side_effect=WorkspaceSnapshotError("simulated abandoned request"),
            ):
                failed = self.client_post(
                    f"/json/armada/workspaces/{node.uuid}/entries",
                    request_data,
                    intentionally_undocumented=True,
                )
            self.assertEqual(failed.status_code, 503)
            receipt = WorkspaceEntryMutationReceipt.objects.get(workspace_node=node)
            self.assertEqual(receipt.state, WorkspaceEntryMutationReceipt.APPLIED)

            descriptor = self.descriptor(node)
            receipt.refresh_from_db()
            created = self.create_entry(
                node,
                destination_path="after-recovery.md",
                kind="file",
                workspace_revision=descriptor["workspace_revision"],
                content="workspace remains writable\n",
            )

        self.assertEqual(receipt.state, WorkspaceEntryMutationReceipt.COMPLETE)
        self.assertEqual(descriptor["workspace_revision"], revision + 1)
        self.assertEqual(created["workspace_revision"], revision + 2)

    def test_prepared_create_stage_replays_without_revision_noise_or_leak(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            before = self.descriptor(node)
            request_data = {
                "destination_path": "prepared.md",
                "kind": "file",
                "workspace_revision": before["workspace_revision"],
                "content": "staged exactly once\n",
            }
            with patch(
                "zerver.views.armada_workspace.record_workspace_entry_mutation_stage",
                side_effect=WorkspaceSnapshotError("simulated stage receipt failure"),
            ):
                failed = self.client_post(
                    f"/json/armada/workspaces/{node.uuid}/entries",
                    request_data,
                    intentionally_undocumented=True,
                )
            self.assertEqual(failed.status_code, 503)
            receipt = WorkspaceEntryMutationReceipt.objects.get(workspace_node=node)
            self.assertEqual(receipt.state, WorkspaceEntryMutationReceipt.PREPARED)
            staging_path = receipt.request_payload["recovery"]["staging_path"]
            self.assertEqual((repo / staging_path).read_text(), "staged exactly once\n")

            staged_descriptor = self.descriptor(node)
            with patch(
                "zerver.views.armada_workspace.ARMADA_WORKSPACE_PREPARED_RECOVERY_GRACE_SECONDS",
                0,
            ):
                repaired_descriptor = self.descriptor(node)
            receipt.refresh_from_db()
            mutation = receipt.result_payload

        self.assertEqual(
            staged_descriptor["workspace_revision"],
            before["workspace_revision"],
        )
        self.assertEqual(staged_descriptor["dirty_count"], before["dirty_count"])
        self.assertEqual(mutation["workspace_revision"], before["workspace_revision"] + 1)
        self.assertEqual(
            repaired_descriptor["workspace_revision"],
            before["workspace_revision"] + 1,
        )
        self.assertEqual(receipt.state, WorkspaceEntryMutationReceipt.COMPLETE)
        self.assertFalse((repo / staging_path).exists())

    def test_conflicted_staged_create_cleans_only_exact_staging_identity(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            revision = self.descriptor(node)["workspace_revision"]
            request_data = {
                "destination_path": "occupied.md",
                "kind": "file",
                "workspace_revision": revision,
                "content": "staged content\n",
            }
            with patch(
                "zerver.views.armada_workspace._publish_staged_create",
                side_effect=WorkspaceSnapshotError("simulated pre-publish stop"),
            ):
                failed = self.client_post(
                    f"/json/armada/workspaces/{node.uuid}/entries",
                    request_data,
                    intentionally_undocumented=True,
                )
            self.assertEqual(failed.status_code, 503)
            receipt = WorkspaceEntryMutationReceipt.objects.get(workspace_node=node)
            self.assertEqual(receipt.state, WorkspaceEntryMutationReceipt.STAGED)
            staging_path = receipt.request_payload["recovery"]["staging_path"]
            self.assertTrue((repo / staging_path).exists())
            (repo / "occupied.md").write_text("concurrent destination\n")

            descriptor = self.descriptor(node)
            receipt.refresh_from_db()
            self.assertEqual(receipt.state, WorkspaceEntryMutationReceipt.CONFLICTED)
            self.assertFalse((repo / staging_path).exists())
            self.assertEqual((repo / "occupied.md").read_text(), "concurrent destination\n")
            created = self.create_entry(
                node,
                destination_path="after-conflict.md",
                kind="file",
                workspace_revision=descriptor["workspace_revision"],
                content="not frozen\n",
            )

        self.assertEqual(created["workspace_revision"], descriptor["workspace_revision"] + 1)

    def test_conflicted_staged_create_never_deletes_replaced_staging_entry(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            revision = self.descriptor(node)["workspace_revision"]
            request_data = {
                "destination_path": "never-published.md",
                "kind": "file",
                "workspace_revision": revision,
                "content": "original stage\n",
            }
            with patch(
                "zerver.views.armada_workspace._publish_staged_create",
                side_effect=WorkspaceSnapshotError("simulated pre-publish stop"),
            ):
                failed = self.client_post(
                    f"/json/armada/workspaces/{node.uuid}/entries",
                    request_data,
                    intentionally_undocumented=True,
                )
            self.assertEqual(failed.status_code, 503)
            receipt = WorkspaceEntryMutationReceipt.objects.get(workspace_node=node)
            staging_path = receipt.request_payload["recovery"]["staging_path"]
            replacement = repo / ".replacement-stage"
            replacement.write_text("wrong replacement\n")
            os.replace(replacement, repo / staging_path)

            self.descriptor(node)
            receipt.refresh_from_db()
            replaced_staging_content = (repo / staging_path).read_text()

        self.assertEqual(receipt.state, WorkspaceEntryMutationReceipt.CONFLICTED)
        self.assertEqual(replaced_staging_content, "wrong replacement\n")

    def test_reads_take_no_lock_without_an_interrupted_mutation(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            self.descriptor(node)
            # Each read: the session (and the first, me and my client), the
            # worktree, the channel, my subscription, and one look for
            # interrupted mutations.
            with (
                self.assert_database_query_count(12),
                CaptureQueriesContext(connection) as queries,
            ):
                self.descriptor(node)
                self.tree(node)
        statements = [query["sql"] for query in queries.captured_queries]
        self.assertFalse(
            [sql for sql in statements if "FOR UPDATE" in sql or "SAVEPOINT" in sql], statements
        )

    def test_fresh_prepared_receipts_do_not_trigger_unbounded_recovery_work(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            revision = self.descriptor(node)["workspace_revision"]
            for index in range(24):
                lookup_payload = workspace_entry_mutation_lookup_payload(
                    actor_id=user.id,
                    workspace_uuid=str(node.uuid),
                    requested_revision=revision,
                    operation="create",
                    source_entry_id="",
                    source_path="",
                    destination_path=f"prepared-{index}.md",
                    options={
                        "kind": "file",
                        "content_sha256": hashlib.sha256(b"").hexdigest(),
                    },
                )
                prepare_workspace_entry_mutation_receipt(
                    node=node,
                    actor_id=user.id,
                    lookup_payload=lookup_payload,
                    source_kind=None,
                    source_identity=None,
                )

            with patch("zerver.views.armada_workspace._inspect_created_path") as inspect:
                descriptor = self.descriptor(node)

        inspect.assert_not_called()
        self.assertEqual(descriptor["workspace_revision"], revision)

    def test_put_bumps_revision_conflicts_and_retries_idempotently(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            entry = descriptor["default_entry"]
            assert entry is not None
            get_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/file",
                {"entry_id": entry["entry_id"]},
                intentionally_undocumented=True,
            )
            original = self.assert_json_success(get_result)["armada_workspace_file"]
            desired = "# Exact\n\nno final newline  "
            request = {
                "entry_id": entry["entry_id"],
                "expected_path": "README.md",
                "content": desired,
                "base_version": original["version"],
                "workspace_revision": descriptor["workspace_revision"],
                "edit_revision": 7,
            }
            mismatched_path_result = cast(WorkspaceClientPut, self.client_put)(
                f"/json/armada/workspaces/{node.uuid}/file",
                {**request, "expected_path": "docs/guide.md"},
                intentionally_undocumented=True,
            )
            self.assertEqual((repo / "README.md").read_bytes(), original["content"].encode())
            put_result = cast(WorkspaceClientPut, self.client_put)(
                f"/json/armada/workspaces/{node.uuid}/file",
                request,
                intentionally_undocumented=True,
            )
            saved = self.assert_json_success(put_result)["armada_workspace_file"]
            node.refresh_from_db()
            revision_after_save = node.content_revision
            retry_result = cast(WorkspaceClientPut, self.client_put)(
                f"/json/armada/workspaces/{node.uuid}/file",
                request,
                intentionally_undocumented=True,
            )
            retry = self.assert_json_success(retry_result)["armada_workspace_file"]
            node.refresh_from_db()
            stale_result = cast(WorkspaceClientPut, self.client_put)(
                f"/json/armada/workspaces/{node.uuid}/file",
                {**request, "content": "must not land", "edit_revision": 8},
                intentionally_undocumented=True,
            )
            remote_content = "# Remote\n"
            (repo / "README.md").write_text(remote_content, encoding="utf-8")
            conflict_result = cast(WorkspaceClientPut, self.client_put)(
                f"/json/armada/workspaces/{node.uuid}/file",
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
            assert conflict_descriptor["default_entry"] is not None

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
            assert entry is not None
            get_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/file",
                {"entry_id": entry["entry_id"]},
                intentionally_undocumented=True,
            )
            original = self.assert_json_success(get_result)["armada_workspace_file"]
            desired = b"saved before database bump\n"
            (repo / "README.md").write_bytes(desired)
            desired_stat = (repo / "README.md").stat()
            result_identity = WorkspaceEntryIdentity(
                desired_stat.st_dev,
                desired_stat.st_ino,
                desired_stat.st_ctime_ns,
            )
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
                result_identity=result_identity,
            )
            result = cast(WorkspaceClientPut, self.client_put)(
                f"/json/armada/workspaces/{node.uuid}/file",
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
            payload = self.assert_json_success(result)["armada_workspace_file"]
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
            assert entry is not None
            get_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/file",
                {"entry_id": entry["entry_id"]},
                intentionally_undocumented=True,
            )
            original = self.assert_json_success(get_result)["armada_workspace_file"]
            delayed_content = b"delayed request bytes\n"
            replacement = repo / ".replacement"
            replacement.write_bytes(delayed_content)
            os.replace(replacement, repo / "README.md")
            put_result = cast(WorkspaceClientPut, self.client_put)(
                f"/json/armada/workspaces/{node.uuid}/file",
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
                f"/json/armada/workspaces/{node.uuid}/file",
                {"entry_id": entry["entry_id"]},
                intentionally_undocumented=True,
            )

            self.assertEqual((repo / "README.md").read_bytes(), delayed_content)

        self.assertEqual(orjson.loads(put_result.content)["error_code"], "stale_entry")
        self.assertEqual(orjson.loads(old_id_read.content)["error_code"], "stale_entry")

    def test_first_read_rejects_unopened_untracked_same_path_replacement(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            (repo / "scratch.txt").write_bytes(b"same bytes\n")
            node = self.make_node(user, stream.name, repo)
            tree = self.tree(node)
            scratch_entry = next(
                entry for entry in tree["entries"] if entry["path"] == "scratch.txt"
            )
            replacement = repo / "replacement.tmp"
            replacement.write_bytes(b"same bytes\n")
            os.replace(replacement, repo / "scratch.txt")

            with patch("zerver.lib.armada_workspace_resources._enumerate_workspace") as enumerate:
                first_read = self.client_get(
                    f"/json/armada/workspaces/{node.uuid}/file",
                    {"entry_id": scratch_entry["entry_id"]},
                    intentionally_undocumented=True,
                )

            self.assertEqual((repo / "scratch.txt").read_bytes(), b"same bytes\n")

        enumerate.assert_not_called()
        self.assertEqual(orjson.loads(first_read.content)["error_code"], "stale_entry")
        self.assertNotIn("identity", repr(tree))

    def test_conflict_after_agent_mutation_uses_current_revision_bound_entry(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workspace-resource")
        self.login_user(user)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self.make_workspace(Path(temp_dir).resolve())
            node = self.make_node(user, stream.name, repo)
            descriptor = self.descriptor(node)
            entry = descriptor["default_entry"]
            assert entry is not None
            get_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/file",
                {"entry_id": entry["entry_id"]},
                intentionally_undocumented=True,
            )
            original = self.assert_json_success(get_result)["armada_workspace_file"]
            remote_data = b"agent mutation\n"
            remote_file = WorkspaceFile(
                data=remote_data,
                version=hashlib.sha256(remote_data).hexdigest(),
                size=len(remote_data),
                mode=0o600,
            )

            def race_write(
                _node: Workspace,
                _path: str,
                _data: bytes,
                *,
                expected_version: str,
                expected_identity: WorkspaceEntryIdentity | None,
            ) -> WorkspaceWriteResult:
                self.assertEqual(expected_version, original["version"])
                self.assertIsNotNone(expected_identity)
                (repo / "README.md").write_bytes(remote_data)
                Workspace.objects.filter(id=node.id).update(content_revision=1)
                raise WorkspaceVersionConflictError(
                    "agent won the race",
                    current=remote_file,
                )

            with patch(
                "zerver.lib.armada_workspace_resources.write_workspace_node_file",
                side_effect=race_write,
            ):
                conflict_result = cast(WorkspaceClientPut, self.client_put)(
                    f"/json/armada/workspaces/{node.uuid}/file",
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
            assert current_descriptor["default_entry"] is not None

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
                f"/json/armada/workspaces/{node.uuid}/refresh",
                intentionally_undocumented=True,
            )
            refresh = self.assert_json_success(refresh_result)["armada_workspace_refresh"]
            stale_cursor_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/tree",
                {"cursor": old_cursor, "page_size": 1},
                intentionally_undocumented=True,
            )
            stale_entry_result = self.client_get(
                f"/json/armada/workspaces/{node.uuid}/file",
                {"entry_id": old_entry_id},
                intentionally_undocumented=True,
            )
            second_refresh_result = self.client_post(
                f"/json/armada/workspaces/{node.uuid}/refresh",
                intentionally_undocumented=True,
            )
            second_refresh = self.assert_json_success(second_refresh_result)[
                "armada_workspace_refresh"
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
                f"/json/armada/workspaces/{node.uuid}/refresh",
                intentionally_undocumented=True,
            )
            refresh = self.assert_json_success(refresh_result)["armada_workspace_refresh"]

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
                f"/json/armada/workspaces/{node.uuid}",
                intentionally_undocumented=True,
            )

        self.assert_json_error(result, "Invalid channel name 'workspace-private'")

    def test_isolated_nodes_route_all_mutations_through_helper(self) -> None:
        username = "armada-0123456789abcdef01234567"
        data = b"hello\n"
        identity = WorkspaceEntryIdentity(1, 2, 3)
        file = WorkspaceFile(
            data=data,
            version=hashlib.sha256(data).hexdigest(),
            size=len(data),
            mode=0o600,
            identity=identity,
        )
        node = Workspace(
            workdir="/canonical/workspace",
            isolation_username=username,
        )

        class StubRawStream:
            def __init__(self) -> None:
                self.metadata = WorkspaceRawFileMetadata(
                    size=len(data),
                    mode=0o600,
                    cache_token="0" * 64,
                    identity=identity,
                )
                self.chunks = iter((data,))
                self.closed = False

            def __iter__(self) -> StubRawStream:
                return self

            def __next__(self) -> bytes:
                return next(self.chunks)

            def close(self) -> None:
                self.closed = True

        raw_stream = StubRawStream()
        with (
            patch(
                "zerver.lib.armada_workspace_resources.read_workspace_file_via_helper",
                return_value=file,
            ) as helper_read,
            patch(
                "zerver.lib.armada_workspace_resources.atomic_write_workspace_file_via_helper",
                return_value=WorkspaceWriteResult(file=file, changed=False, created=False),
            ) as helper_write,
            patch(
                "zerver.lib.armada_workspace_resources.create_workspace_directory_via_helper",
                return_value=WorkspaceEntryMutationResult("folder", "directory"),
            ) as helper_mkdir,
            patch(
                "zerver.lib.armada_workspace_resources.rename_workspace_entry_via_helper",
                return_value=WorkspaceEntryMutationResult("renamed.md", "file"),
            ) as helper_rename,
            patch(
                "zerver.lib.armada_workspace_resources.delete_workspace_entry_via_helper",
                return_value=WorkspaceDeleteResult("renamed.md", "file", 1),
            ) as helper_delete,
            patch(
                "zerver.lib.armada_workspace_resources.inspect_workspace_entries_via_helper",
                return_value=(WorkspaceInspectedEntry("README.md", "file", identity),),
            ) as helper_inspect,
            patch(
                "zerver.lib.armada_workspace_resources.open_workspace_file_stream_via_helper",
                return_value=raw_stream,
            ) as helper_stream,
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
                    expected_identity=identity,
                ).file,
                file,
            )
            self.assertFalse(create_workspace_node_file(node, "new.md", data).created)
            self.assertEqual(
                create_workspace_node_directory(node, "folder").kind,
                "directory",
            )
            self.assertEqual(
                rename_workspace_node_entry(
                    node,
                    "README.md",
                    "renamed.md",
                    expected_kind="file",
                    expected_identity=identity,
                ).path,
                "renamed.md",
            )
            self.assertEqual(
                delete_workspace_node_entry(
                    node,
                    "renamed.md",
                    expected_kind="file",
                    expected_identity=identity,
                    recursive=False,
                ).removed_entries,
                1,
            )
            self.assertEqual(
                inspect_workspace_node_entries(node, {"README.md": "file"}),
                {"README.md": identity},
            )
            staged_stream = open_workspace_node_file_stream(
                node,
                "README.md",
                expected_identity=identity,
            )
            self.assertEqual(b"".join(staged_stream), data)
            self.assertTrue(raw_stream.closed)

        helper_read.assert_called_once_with(
            "/canonical/workspace",
            "README.md",
            max_bytes=100,
            expected_identity=None,
            isolation_username=username,
        )
        self.assertEqual(helper_write.call_count, 2)
        helper_write.assert_any_call(
            "/canonical/workspace",
            "README.md",
            data,
            max_bytes=ARMADA_WORKSPACE_TEXT_MAX_BYTES,
            expected_version=file.version,
            expected_identity=identity,
            create=False,
            isolation_username=username,
        )
        helper_write.assert_any_call(
            "/canonical/workspace",
            "new.md",
            data,
            max_bytes=ARMADA_WORKSPACE_TEXT_MAX_BYTES,
            expected_version=None,
            create=True,
            isolation_username=username,
        )
        helper_mkdir.assert_called_once_with(
            "/canonical/workspace",
            "folder",
            isolation_username=username,
        )
        helper_rename.assert_called_once_with(
            "/canonical/workspace",
            "README.md",
            "renamed.md",
            expected_kind="file",
            expected_identity=identity,
            isolation_username=username,
        )
        helper_delete.assert_called_once_with(
            "/canonical/workspace",
            "renamed.md",
            expected_kind="file",
            expected_identity=identity,
            recursive=False,
            isolation_username=username,
        )
        helper_inspect.assert_called_once_with(
            "/canonical/workspace",
            {"README.md": "file"},
            skip_unsafe=True,
            isolation_username=username,
        )
        helper_stream.assert_called_once_with(
            "/canonical/workspace",
            "README.md",
            expected_identity=identity,
            isolation_username=username,
        )


class ArmadaWorkspaceResourcePayloadTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        clear_workspace_snapshot_cache()

    def make_snapshot(self, number: int, path_count: int = 1) -> WorkspaceSnapshot:
        workspace_uuid = str(UUID(int=number))
        paths = tuple(WorkspacePath(f"file-{index}.md") for index in range(path_count))
        return WorkspaceSnapshot(
            workspace_uuid=workspace_uuid,
            revision=1,
            paths=paths,
            index=WorkspaceIndex.build(
                workspace_uuid=workspace_uuid,
                revision=1,
                paths=paths,
                secret=b"resource-payload-test-secret-32bytes",
            ),
            fingerprint="f" * 64,
            branch="main",
            head="a" * 40,
            dirty_count=0,
        )

    def test_local_snapshot_cache_retains_more_than_four_workspaces(self) -> None:
        snapshots = [self.make_snapshot(number) for number in range(1, 7)]
        with self.settings(
            ARMADA_WORKSPACE_INDEX_LOCAL_MAX_ENTRIES=8,
            ARMADA_WORKSPACE_INDEX_LOCAL_MAX_PATHS=100,
        ):
            for snapshot in snapshots:
                _local_cache_put(snapshot)
            self.assertEqual(
                [_local_cache_get(snapshot.workspace_uuid, 1) for snapshot in snapshots],
                snapshots,
            )

    def test_local_snapshot_cache_evicts_by_path_budget(self) -> None:
        snapshots = [self.make_snapshot(number, path_count=2) for number in range(1, 4)]
        with self.settings(
            ARMADA_WORKSPACE_INDEX_LOCAL_MAX_ENTRIES=8,
            ARMADA_WORKSPACE_INDEX_LOCAL_MAX_PATHS=3,
        ):
            for snapshot in snapshots:
                _local_cache_put(snapshot)
            self.assertIsNone(_local_cache_get(snapshots[0].workspace_uuid, 1))
            self.assertIsNone(_local_cache_get(snapshots[1].workspace_uuid, 1))
            self.assertEqual(_local_cache_get(snapshots[2].workspace_uuid, 1), snapshots[2])

    def test_concurrent_snapshot_requests_share_one_rebuild(self) -> None:
        node = SimpleNamespace(
            uuid=UUID(int=42),
            content_revision=1,
            branch_name="main",
        )
        enumerate_started = threading.Event()
        second_joined = threading.Event()
        release_enumeration = threading.Event()
        enumerate_count = 0
        join_count = 0
        results: list[WorkspaceSnapshot] = []

        def enumerate_workspace(
            unused_node: object,
        ) -> tuple[tuple[WorkspacePath, ...], str, str, str, int]:
            nonlocal enumerate_count
            enumerate_count += 1
            enumerate_started.set()
            release_enumeration.wait(timeout=5)
            return ((WorkspacePath("README.md"),), "f" * 64, "main", "a" * 40, 0)

        def join_or_start(key: tuple[str, int]) -> tuple[_SnapshotBuild, bool]:
            nonlocal join_count
            join_count += 1
            result = _join_or_start_snapshot_build(key)
            if join_count == 2:
                second_joined.set()
            return result

        def load_snapshot() -> None:
            results.append(get_workspace_snapshot(node, force=True))  # type: ignore[arg-type]

        with (
            patch(
                "zerver.lib.armada_workspace_resources._enumerate_workspace",
                side_effect=enumerate_workspace,
            ),
            patch(
                "zerver.lib.armada_workspace_resources._join_or_start_snapshot_build",
                side_effect=join_or_start,
            ),
        ):
            first = threading.Thread(target=load_snapshot)
            second = threading.Thread(target=load_snapshot)
            first.start()
            self.assertTrue(enumerate_started.wait(timeout=5))
            second.start()
            self.assertTrue(second_joined.wait(timeout=5))
            release_enumeration.set()
            first.join(timeout=5)
            second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(enumerate_count, 1)
        self.assertEqual(len(results), 2)
        self.assertIs(results[0], results[1])

    def test_new_authoritative_revision_emits_one_workspace_event(self) -> None:
        node = SimpleNamespace(
            uuid=UUID(int=84),
            content_revision=1,
            branch_name="main",
        )
        cache.set(
            f"armada:workspace-index-fingerprint:v1:{node.uuid}",
            {"revision": 1, "fingerprint": "a" * 64},
            timeout=60,
        )

        def advance_revision(unused_node: object, revision: int) -> bool:
            self.assertEqual(revision, 1)
            node.content_revision = 2
            return True

        with (
            patch(
                "zerver.lib.armada_workspace_resources._enumerate_workspace",
                return_value=(
                    (WorkspacePath("README.md"),),
                    "b" * 64,
                    "main",
                    "a" * 40,
                    0,
                ),
            ),
            patch(
                "zerver.lib.armada_workspace_resources._advance_revision_if_current",
                side_effect=advance_revision,
            ),
            patch("zerver.actions.armada.send_armada_workspace_changed_event") as send_event,
        ):
            snapshot = get_workspace_snapshot(node, force=True)  # type: ignore[arg-type]

        self.assertEqual(snapshot.revision, 2)
        send_event.assert_called_once_with(node, 2)

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
