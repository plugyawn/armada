from __future__ import annotations

import unittest

from zerver.lib.armada_workspace_index import (
    MAX_SEARCH_LIMIT,
    WorkspaceCursorError,
    WorkspaceEntryIdError,
    WorkspaceIndex,
    WorkspaceIndexError,
    WorkspacePath,
    _subsequence_score,
)
from zerver.lib.armada_workspace_io import WorkspaceEntryIdentity


class ArmadaWorkspaceIndexTest(unittest.TestCase):
    secret = b"test-workspace-index-secret-32bytes"

    def build(
        self,
        paths: list[WorkspacePath],
        *,
        revision: int = 7,
        workspace_uuid: str = "12345678-1234-5678-1234-567812345678",
    ) -> WorkspaceIndex:
        return WorkspaceIndex.build(
            workspace_uuid=workspace_uuid,
            revision=revision,
            paths=paths,
            secret=self.secret,
        )

    def test_tree_is_complete_lazy_and_directories_first(self) -> None:
        index = self.build(
            [
                WorkspacePath("z-last.txt"),
                WorkspacePath("src/z.py"),
                WorkspacePath("README.md", "changed"),
                WorkspacePath("src/a.py", "untracked"),
                WorkspacePath("docs/guide/start.md"),
            ]
        )

        root = index.tree_page(page_size=2)
        self.assertEqual([entry.path for entry in root.entries], ["docs", "src"])
        self.assertIsNotNone(root.next_cursor)
        rest = index.tree_page(cursor=root.next_cursor, page_size=20)
        self.assertEqual(
            [entry.path for entry in rest.entries],
            ["README.md", "z-last.txt"],
        )
        src = index.entry_for_path("src")
        self.assertTrue(src.has_children)
        self.assertEqual(src.status, "untracked")
        self.assertEqual(
            [entry.path for entry in index.tree_page(directory_id=src.entry_id).entries],
            ["src/a.py", "src/z.py"],
        )
        self.assertEqual(
            set(index.file_paths()),
            {"README.md", "docs/guide/start.md", "src/a.py", "src/z.py", "z-last.txt"},
        )

    def test_entry_ids_are_opaque_and_revision_bound(self) -> None:
        paths = [WorkspacePath("private/filename.md")]
        first = self.build(paths, revision=7)
        second = self.build(paths, revision=8)
        first_entry = first.entry_for_path("private/filename.md")
        second_entry = second.entry_for_path("private/filename.md")

        self.assertNotIn("private", first_entry.entry_id)
        self.assertNotEqual(first_entry.entry_id, second_entry.entry_id)
        with self.assertRaises(WorkspaceEntryIdError):
            second.entry_for_id(first_entry.entry_id)

    def test_explicit_empty_directories_are_revision_bound_entries(self) -> None:
        first = self.build(
            [
                WorkspacePath("empty", "untracked", "directory"),
                WorkspacePath("nested/also-empty", "untracked", "directory"),
            ],
            revision=3,
        )
        second = self.build(
            [
                WorkspacePath("empty", "untracked", "directory"),
                WorkspacePath("nested/also-empty", "untracked", "directory"),
            ],
            revision=4,
        )

        empty = first.entry_for_path("empty")
        self.assertEqual(empty.kind, "directory")
        self.assertFalse(empty.has_children)
        self.assertEqual(empty.status, "untracked")
        self.assertEqual(
            [entry.path for entry in first.tree_page().entries],
            ["empty", "nested"],
        )
        self.assertNotEqual(empty.entry_id, second.entry_for_path("empty").entry_id)
        with self.assertRaises(WorkspaceEntryIdError):
            second.entry_for_id(empty.entry_id)

    def test_entry_ids_are_workspace_bound(self) -> None:
        path = [WorkspacePath("README.md")]
        first = self.build(path, workspace_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        second = self.build(path, workspace_uuid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        self.assertNotEqual(
            first.entry_for_path("README.md").entry_id,
            second.entry_for_path("README.md").entry_id,
        )

    def test_entry_ids_bind_unexposed_filesystem_identity(self) -> None:
        first_identity = WorkspaceEntryIdentity(device=1, inode=2, ctime_ns=3)
        second_identity = WorkspaceEntryIdentity(device=1, inode=4, ctime_ns=5)
        first = self.build([WorkspacePath("scratch.txt", "untracked", "file", first_identity)])
        second = self.build([WorkspacePath("scratch.txt", "untracked", "file", second_identity)])

        first_entry = first.entry_for_path("scratch.txt")
        second_entry = second.entry_for_path("scratch.txt")
        self.assertEqual(first_entry.identity, first_identity)
        self.assertNotEqual(first_entry.entry_id, second_entry.entry_id)
        self.assertNotIn(str(first_identity.inode), first_entry.entry_id)

    def test_cursor_rejects_tamper_and_other_revision(self) -> None:
        paths = [WorkspacePath(f"file-{index:03}.txt") for index in range(10)]
        first = self.build(paths, revision=1)
        cursor = first.tree_page(page_size=2).next_cursor
        assert cursor is not None

        with self.assertRaises(WorkspaceCursorError):
            first.tree_page(cursor=f"{cursor[:-1]}x", page_size=2)
        with self.assertRaises(WorkspaceCursorError):
            self.build(paths, revision=2).tree_page(cursor=cursor, page_size=2)

    def test_search_prioritizes_exact_substring_then_subsequence(self) -> None:
        index = self.build(
            [
                WorkspacePath("readme"),
                WorkspacePath("README.md"),
                WorkspacePath("docs/readme-notes.md"),
                WorkspacePath("random-read-event-model.txt"),
                WorkspacePath("unrelated.txt"),
            ]
        )
        self.assertEqual(
            [entry.path for entry in index.search("readme")],
            ["readme", "README.md", "docs/readme-notes.md", "random-read-event-model.txt"],
        )
        self.assertEqual(index.search("  "), ())
        self.assertEqual(len(index.search("r", limit=2)), 2)
        with self.assertRaises(ValueError):
            index.search("r", limit=MAX_SEARCH_LIMIT + 1)

    def test_bounded_search_preserves_complete_sort_ranking(self) -> None:
        paths = [
            WorkspacePath(f"package-{number % 73:02}/ReadMe-{number:05}.md")
            for number in range(5000)
        ]
        paths.extend(
            [
                WorkspacePath("README.md"),
                WorkspacePath("readme.md"),
                WorkspacePath("docs/read-me.md"),
            ]
        )
        index = self.build(paths)
        query = "readme"
        expected = sorted(
            (
                entry
                for entry in index._entries.values()
                if entry.path
                and _subsequence_score(query, entry.path.casefold()) is not None
            ),
            key=lambda entry: (
                _subsequence_score(query, entry.path.casefold()),
                entry.path.casefold(),
                entry.path,
            ),
        )[:MAX_SEARCH_LIMIT]

        self.assertEqual(index.search(query), tuple(expected))

    def test_rejects_file_directory_collision(self) -> None:
        with self.assertRaises(WorkspaceIndexError):
            self.build([WorkspacePath("src"), WorkspacePath("src/main.py")])

    def test_duplicate_file_keeps_strongest_status(self) -> None:
        index = self.build(
            [WorkspacePath("README.md", "clean"), WorkspacePath("README.md", "conflict")]
        )
        self.assertEqual(index.entry_for_path("README.md").status, "conflict")

    def test_large_index_has_every_file_without_global_cutoff(self) -> None:
        path_count = 100_000
        index = self.build(
            [
                WorkspacePath(f"package-{number // 1000:03}/src/file-{number:06}.ts")
                for number in range(path_count)
            ]
        )
        self.assertEqual(len(index.file_paths()), path_count)
        directory = index.entry_for_path("package-099/src")
        first_page = index.tree_page(directory_id=directory.entry_id, page_size=500)
        second_page = index.tree_page(
            directory_id=directory.entry_id,
            cursor=first_page.next_cursor,
            page_size=500,
        )
        self.assertEqual(len(first_page.entries) + len(second_page.entries), 1000)
        self.assertIsNone(second_page.next_cursor)


if __name__ == "__main__":
    unittest.main()
