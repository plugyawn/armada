from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from zerver.lib import pear_workspace_io
from zerver.lib.pear_workspace_io import (
    InvalidWorkspacePathError,
    UnsafeWorkspaceEntryError,
    WorkspaceDirectoryNotEmptyError,
    WorkspaceEntryExistsError,
    WorkspaceEntryNotFoundError,
    WorkspaceFileTooLargeError,
    WorkspaceHelperError,
    WorkspaceInvalidUTF8Error,
    WorkspaceIOError,
    WorkspaceVersionConflictError,
    atomic_write_workspace_file,
    atomic_write_workspace_file_via_helper,
    create_workspace_directory,
    create_workspace_directory_via_helper,
    delete_workspace_entry,
    delete_workspace_entry_via_helper,
    read_workspace_file,
    read_workspace_file_via_helper,
    read_workspace_text_file,
    rename_workspace_entry,
    rename_workspace_entry_via_helper,
    validate_workspace_relative_path,
)


class PearWorkspaceIOTest(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        # macOS exposes /var through a symlink. The production helper refuses
        # symlinked root components, so tests use the canonical temporary root.
        self.root = Path(self.temporary_directory.name).resolve() / "workspace"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write(self, relative_path: str, data: bytes) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_read_preserves_exact_bytes_and_strict_utf8(self) -> None:
        content = b"\xef\xbb\xbfline one\r\nline two  \r\n"
        path = self.write("docs/README.md", content)
        path.chmod(0o640)

        file = read_workspace_file(self.root, "docs/README.md", max_bytes=len(content))
        text_file = read_workspace_text_file(
            self.root,
            "docs/README.md",
            max_bytes=len(content),
        )

        self.assertEqual(file.data, content)
        self.assertEqual(file.size, len(content))
        self.assertEqual(file.version, hashlib.sha256(content).hexdigest())
        self.assertEqual(file.mode, 0o640)
        self.assertEqual(text_file.text.encode("utf-8"), content)
        self.assertEqual(text_file.version, file.version)

        invalid_path = self.write("invalid.txt", b"before\xffafter")
        with self.assertRaises(WorkspaceInvalidUTF8Error):
            read_workspace_text_file(self.root, invalid_path.name, max_bytes=64)

    def test_empty_and_missing_files_are_distinct(self) -> None:
        self.write("empty.txt", b"")

        empty = read_workspace_file(self.root, "empty.txt", max_bytes=0)
        self.assertEqual(empty.data, b"")
        self.assertEqual(empty.version, hashlib.sha256(b"").hexdigest())

        with self.assertRaises(WorkspaceEntryNotFoundError):
            read_workspace_file(self.root, "missing.txt", max_bytes=0)

    def test_path_validation_rejects_aliases_metadata_and_controls(self) -> None:
        invalid_paths = (
            "",
            "/etc/passwd",
            "../secret",
            "docs/../secret",
            "docs//secret",
            "./secret",
            ".git/config",
            ".GIT/config",
            "windows\\path",
            "line\nbreak",
            "bidi\u202epath",
        )
        for invalid_path in invalid_paths:
            with self.subTest(path=invalid_path), self.assertRaises(InvalidWorkspacePathError):
                validate_workspace_relative_path(invalid_path)

        self.assertEqual(
            validate_workspace_relative_path("src/café.py"),
            ("src", "café.py"),
        )

    def test_symlink_escape_is_rejected_at_every_component(self) -> None:
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        (self.root / "nested-link").symlink_to(outside, target_is_directory=True)
        (self.root / "file-link").symlink_to(outside / "secret.txt")

        for path in ("nested-link/secret.txt", "file-link"):
            with (
                self.subTest(path=path),
                self.assertRaises((UnsafeWorkspaceEntryError, WorkspaceEntryNotFoundError)),
            ):
                read_workspace_file(self.root, path, max_bytes=100)

        target = self.write("target.txt", b"target")
        (self.root / "write-link").symlink_to(target)
        with self.assertRaises(UnsafeWorkspaceEntryError):
            atomic_write_workspace_file(
                self.root,
                "write-link",
                b"replacement",
                max_bytes=100,
                expected_version=hashlib.sha256(b"target").hexdigest(),
            )
        self.assertEqual(target.read_bytes(), b"target")

        linked_root = self.root.parent / "workspace-link"
        linked_root.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises((UnsafeWorkspaceEntryError, WorkspaceEntryNotFoundError)):
            read_workspace_file(linked_root, "target.txt", max_bytes=100)

        (self.root / "directory.txt").mkdir()
        with self.assertRaises(UnsafeWorkspaceEntryError):
            read_workspace_file(self.root, "directory.txt", max_bytes=100)

    def test_special_files_and_hardlinks_are_rejected_without_blocking(self) -> None:
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        with self.assertRaises(UnsafeWorkspaceEntryError):
            read_workspace_file(self.root, fifo.name, max_bytes=100)

        socket_path = self.root / "socket"
        unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            unix_socket.bind(str(socket_path))
            with self.assertRaises(UnsafeWorkspaceEntryError):
                read_workspace_file(self.root, socket_path.name, max_bytes=100)
        finally:
            unix_socket.close()

        original = self.write("original.txt", b"same inode")
        os.link(original, self.root / "hardlink.txt")
        for path in ("original.txt", "hardlink.txt"):
            with self.subTest(path=path), self.assertRaises(UnsafeWorkspaceEntryError):
                read_workspace_file(self.root, path, max_bytes=100)

    def test_oversize_sparse_file_is_rejected_before_read(self) -> None:
        path = self.root / "sparse.bin"
        with path.open("wb") as file:
            file.seek(1024 * 1024)
            file.write(b"x")

        with (
            patch("zerver.lib.pear_workspace_io.os.read") as read,
            self.assertRaises(WorkspaceFileTooLargeError),
        ):
            read_workspace_file(self.root, path.name, max_bytes=1024)
        read.assert_not_called()

    def test_atomic_replace_preserves_mode_and_checks_raw_version(self) -> None:
        path = self.write("README.md", b"old\r\n")
        path.chmod(0o750)
        old_version = hashlib.sha256(b"old\r\n").hexdigest()

        result = atomic_write_workspace_file(
            self.root,
            path.name,
            b"new\r\n",
            max_bytes=100,
            expected_version=old_version,
        )

        self.assertTrue(result.changed)
        self.assertFalse(result.created)
        self.assertEqual(result.file.data, b"new\r\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o750)
        self.assertEqual(path.read_bytes(), b"new\r\n")
        self.assertEqual(list(self.root.glob(".pear-write-*")), [])

        with self.assertRaises(WorkspaceVersionConflictError) as raised:
            atomic_write_workspace_file(
                self.root,
                path.name,
                b"stale",
                max_bytes=100,
                expected_version=old_version,
            )
        self.assertIsNotNone(raised.exception.current)
        assert raised.exception.current is not None
        self.assertEqual(raised.exception.current.data, b"new\r\n")
        self.assertEqual(path.read_bytes(), b"new\r\n")

        # Retrying a save whose bytes already landed is idempotent even if the
        # client did not receive the new version.
        retry = atomic_write_workspace_file(
            self.root,
            path.name,
            b"new\r\n",
            max_bytes=100,
            expected_version=old_version,
        )
        self.assertFalse(retry.changed)

    def test_atomic_create_never_overwrites_and_is_idempotent(self) -> None:
        created = atomic_write_workspace_file(
            self.root,
            "notes.txt",
            b"first",
            max_bytes=100,
            expected_version=None,
            create=True,
            create_mode=0o640,
        )
        self.assertTrue(created.changed)
        self.assertTrue(created.created)
        self.assertEqual(stat.S_IMODE((self.root / "notes.txt").stat().st_mode), 0o640)

        retry = atomic_write_workspace_file(
            self.root,
            "notes.txt",
            b"first",
            max_bytes=100,
            expected_version=None,
            create=True,
        )
        self.assertFalse(retry.changed)
        self.assertFalse(retry.created)

        with self.assertRaises(WorkspaceEntryExistsError):
            atomic_write_workspace_file(
                self.root,
                "notes.txt",
                b"second",
                max_bytes=100,
                expected_version=None,
                create=True,
            )
        self.assertEqual((self.root / "notes.txt").read_bytes(), b"first")

        with self.assertRaises(WorkspaceFileTooLargeError):
            atomic_write_workspace_file(
                self.root,
                "too-large.txt",
                b"x" * 101,
                max_bytes=100,
                expected_version=None,
                create=True,
            )
        self.assertFalse((self.root / "too-large.txt").exists())

    def test_directory_create_and_noreplace_rename_are_contained(self) -> None:
        created = create_workspace_directory(self.root, "notes", create_mode=0o750)
        self.assertEqual((created.path, created.kind), ("notes", "directory"))
        self.assertEqual(stat.S_IMODE((self.root / "notes").stat().st_mode), 0o750)
        with self.assertRaises(WorkspaceEntryExistsError):
            create_workspace_directory(self.root, "notes")

        source = self.write("draft.md", b"draft")
        destination = self.write("published.md", b"published")
        with self.assertRaises(WorkspaceEntryExistsError):
            rename_workspace_entry(
                self.root,
                source.name,
                destination.name,
                expected_kind="file",
            )
        self.assertEqual(source.read_bytes(), b"draft")
        self.assertEqual(destination.read_bytes(), b"published")

        result = rename_workspace_entry(
            self.root,
            source.name,
            "notes/draft.md",
            expected_kind="file",
        )
        self.assertEqual((result.path, result.kind), ("notes/draft.md", "file"))
        self.assertFalse(source.exists())
        self.assertEqual((self.root / result.path).read_bytes(), b"draft")

        with self.assertRaises(InvalidWorkspacePathError):
            rename_workspace_entry(
                self.root,
                "notes",
                "notes/archive",
                expected_kind="directory",
            )

    def test_delete_requires_recursive_intent_and_rejects_unsafe_descendants(self) -> None:
        self.write("tree/one.txt", b"one")
        self.write("tree/nested/two.txt", b"two")
        with self.assertRaises(WorkspaceDirectoryNotEmptyError):
            delete_workspace_entry(
                self.root,
                "tree",
                expected_kind="directory",
            )
        self.assertTrue((self.root / "tree/nested/two.txt").exists())

        deleted = delete_workspace_entry(
            self.root,
            "tree",
            expected_kind="directory",
            recursive=True,
        )
        self.assertEqual(deleted.removed_entries, 4)
        self.assertFalse((self.root / "tree").exists())

        outside = self.write("outside.txt", b"outside")
        (self.root / "unsafe").mkdir()
        (self.root / "unsafe/link").symlink_to(outside)
        with self.assertRaises(UnsafeWorkspaceEntryError):
            delete_workspace_entry(
                self.root,
                "unsafe",
                expected_kind="directory",
                recursive=True,
            )
        self.assertTrue((self.root / "unsafe/link").is_symlink())
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_mutation_helper_matches_direct_operations_and_typed_errors(self) -> None:
        helper = Path(pear_workspace_io.__file__)
        created = create_workspace_directory_via_helper(
            self.root,
            "folder",
            helper_path=helper,
            python_executable=sys.executable,
        )
        self.assertEqual(created.kind, "directory")
        self.write("source.txt", b"source")
        renamed = rename_workspace_entry_via_helper(
            self.root,
            "source.txt",
            "folder/renamed.txt",
            expected_kind="file",
            helper_path=helper,
            python_executable=sys.executable,
        )
        self.assertEqual(renamed.path, "folder/renamed.txt")
        with self.assertRaises(WorkspaceDirectoryNotEmptyError):
            delete_workspace_entry_via_helper(
                self.root,
                "folder",
                expected_kind="directory",
                helper_path=helper,
                python_executable=sys.executable,
            )
        deleted = delete_workspace_entry_via_helper(
            self.root,
            "folder",
            expected_kind="directory",
            recursive=True,
            helper_path=helper,
            python_executable=sys.executable,
        )
        self.assertEqual(deleted.removed_entries, 2)

    def test_failed_replace_keeps_original_and_cleans_temporary_file(self) -> None:
        path = self.write("notes.txt", b"original")
        version = hashlib.sha256(b"original").hexdigest()

        with (
            patch(
                "zerver.lib.pear_workspace_io.os.replace",
                side_effect=OSError("simulated failure"),
            ),
            self.assertRaises(WorkspaceIOError),
        ):
            atomic_write_workspace_file(
                self.root,
                path.name,
                b"replacement",
                max_bytes=100,
                expected_version=version,
            )

        self.assertEqual(path.read_bytes(), b"original")
        self.assertEqual(list(self.root.glob(".pear-write-*")), [])

    def test_cli_helper_matches_direct_read_write_and_typed_errors(self) -> None:
        path = self.write("README.md", b"old")
        direct = read_workspace_file(self.root, path.name, max_bytes=100)
        via_helper = read_workspace_file_via_helper(
            self.root,
            path.name,
            max_bytes=100,
            helper_path=Path(pear_workspace_io.__file__),
            python_executable=sys.executable,
        )
        self.assertEqual(via_helper, direct)

        saved = atomic_write_workspace_file_via_helper(
            self.root,
            path.name,
            b"new",
            max_bytes=100,
            expected_version=direct.version,
            helper_path=Path(pear_workspace_io.__file__),
            python_executable=sys.executable,
        )
        self.assertTrue(saved.changed)
        self.assertEqual(saved.file.data, b"new")

        with self.assertRaises(WorkspaceVersionConflictError) as raised:
            atomic_write_workspace_file_via_helper(
                self.root,
                path.name,
                b"stale",
                max_bytes=100,
                expected_version=direct.version,
                helper_path=Path(pear_workspace_io.__file__),
                python_executable=sys.executable,
            )
        assert raised.exception.current is not None
        self.assertEqual(raised.exception.current.data, b"new")

        os.mkfifo(self.root / "pipe")
        with self.assertRaises(UnsafeWorkspaceEntryError):
            read_workspace_file_via_helper(
                self.root,
                "pipe",
                max_bytes=100,
                helper_path=Path(pear_workspace_io.__file__),
                python_executable=sys.executable,
            )

    def test_helper_serializes_competing_process_writes(self) -> None:
        path = self.write("race.txt", b"base")
        version = hashlib.sha256(b"base").hexdigest()
        command = [sys.executable, str(Path(pear_workspace_io.__file__)), "--stdio"]

        requests = [
            json.dumps(
                {
                    "operation": "write",
                    "root": str(self.root),
                    "path": path.name,
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "max_bytes": 100,
                    "expected_version": version,
                    "create": False,
                    "create_mode": 0o600,
                }
            ).encode()
            for content in (b"first", b"second")
        ]

        processes = [
            subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _request in requests
        ]
        for process, request in zip(processes, requests, strict=True):
            assert process.stdin is not None
            process.stdin.write(request)
            process.stdin.close()

        responses = []
        for process in processes:
            assert process.stdout is not None
            assert process.stderr is not None
            stdout = process.stdout.read()
            stderr = process.stderr.read()
            process.stdout.close()
            process.stderr.close()
            returncode = process.wait(timeout=10)
            self.assertEqual(returncode, 0, stderr.decode(errors="replace"))
            responses.append(json.loads(stdout))

        self.assertEqual(sum(response["ok"] is True for response in responses), 1)
        errors = [response["result"] for response in responses if response["ok"] is False]
        self.assertEqual([error["code"] for error in errors], ["version_conflict"])
        self.assertIn(path.read_bytes(), {b"first", b"second"})

    def test_isolation_helper_command_is_fixed_and_username_is_validated(self) -> None:
        command = pear_workspace_io._helper_command(
            isolation_username="pear-0123456789abcdef01234567",
            helper_path="/usr/local/lib/pear-workspace-io",
            python_executable="python3",
        )
        self.assertEqual(
            command,
            [
                "sudo",
                "-n",
                "-u",
                "pear-0123456789abcdef01234567",
                "/usr/bin/env",
                "python3",
                "/usr/local/lib/pear-workspace-io",
                "--stdio",
            ],
        )
        with self.assertRaises(ValueError):
            pear_workspace_io._helper_command(
                isolation_username="root",
                helper_path="/usr/local/lib/invalid-pear-helper",
                python_executable="python3",
            )

    def test_helper_rejects_malformed_or_inconsistent_protocol_data(self) -> None:
        with self.assertRaises(WorkspaceHelperError):
            pear_workspace_io.invoke_workspace_io_helper(
                {"operation": "unknown"},
                helper_path=Path(pear_workspace_io.__file__),
                python_executable=sys.executable,
            )

        inconsistent = {
            "content_base64": base64.b64encode(b"data").decode("ascii"),
            "version": hashlib.sha256(b"other").hexdigest(),
            "size": 4,
            "mode": 0o600,
        }
        with self.assertRaises(WorkspaceHelperError):
            pear_workspace_io._workspace_file_from_payload(inconsistent)
