"""Revision-bound path indexes for Pear workspaces.

The filesystem layer validates individual operations.  This module supplies the
authorization snapshot above it: every entry exposed to a client receives an
opaque identifier bound to one workspace UUID, content revision, kind, and
relative path.  A later revision therefore cannot silently reuse an old file
identifier for a replacement at the same path.

This module is deliberately pure.  Git enumeration, Django authorization, and
cache invalidation belong to callers, which makes the completeness and paging
rules cheap to exercise with very large synthetic repositories.
"""

from __future__ import annotations

import base64
import hashlib
import heapq
import hmac
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from zerver.lib.armada_workspace_io import WorkspaceEntryIdentity, validate_workspace_relative_path

ROOT_ENTRY_PATH = ""
DEFAULT_TREE_PAGE_SIZE = 200
MAX_TREE_PAGE_SIZE = 500
DEFAULT_SEARCH_LIMIT = 100
MAX_SEARCH_LIMIT = 100


class WorkspaceIndexError(Exception):
    pass


class WorkspaceEntryIdError(WorkspaceIndexError):
    pass


class WorkspaceCursorError(WorkspaceIndexError):
    pass


@dataclass(frozen=True)
class WorkspacePath:
    path: str
    status: str = "clean"
    kind: Literal["file", "directory"] = "file"
    identity: WorkspaceEntryIdentity | None = None


@dataclass(frozen=True)
class WorkspaceIndexEntry:
    entry_id: str
    path: str
    name: str
    kind: str
    status: str
    has_children: bool
    identity: WorkspaceEntryIdentity | None


@dataclass(frozen=True)
class WorkspaceTreePage:
    entries: tuple[WorkspaceIndexEntry, ...]
    next_cursor: str | None


def _urlsafe_digest(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _status_rank(status: str) -> int:
    return {
        "clean": 0,
        "ignored": 0,
        "untracked": 1,
        "changed": 2,
        "deleted": 3,
        "conflict": 4,
    }.get(status, 2)


def _stronger_status(left: str, right: str) -> str:
    return left if _status_rank(left) >= _status_rank(right) else right


def _subsequence_score(query: str, candidate: str) -> tuple[int, int, int] | None:
    """Rank exact/substring/subsequence matches without an all-path sort."""

    if query == candidate:
        return (0, 0, len(candidate))
    substring_at = candidate.find(query)
    if substring_at >= 0:
        return (1, substring_at, len(candidate))

    query_offset = 0
    first_at = -1
    last_at = -1
    for offset, character in enumerate(candidate):
        if character != query[query_offset]:
            continue
        if first_at < 0:
            first_at = offset
        last_at = offset
        query_offset += 1
        if query_offset == len(query):
            return (2, last_at - first_at, len(candidate))
    return None


class WorkspaceIndex:
    def __init__(
        self,
        *,
        workspace_uuid: str,
        revision: int,
        entries: Mapping[str, WorkspaceIndexEntry],
        secret: bytes,
    ) -> None:
        if not workspace_uuid:
            raise ValueError("workspace_uuid must not be empty")
        if isinstance(revision, bool) or revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        if len(secret) < 16:
            raise ValueError("workspace index secret must contain at least 16 bytes")
        self.workspace_uuid = workspace_uuid
        self.revision = revision
        self._entries = dict(entries)
        self._entries_by_id = {entry.entry_id: entry for entry in entries.values()}
        if len(self._entries_by_id) != len(self._entries):
            raise ValueError("workspace entry identifiers collided")
        self._secret = secret
        self._children: dict[str, tuple[str, ...]] = {}

    @classmethod
    def build(
        cls,
        *,
        workspace_uuid: str,
        revision: int,
        paths: Iterable[WorkspacePath],
        secret: bytes,
    ) -> WorkspaceIndex:
        statuses: dict[str, str] = {ROOT_ENTRY_PATH: "clean"}
        kinds: dict[str, Literal["file", "directory"]] = {ROOT_ENTRY_PATH: "directory"}
        children: dict[str, set[str]] = {ROOT_ENTRY_PATH: set()}
        identities: dict[str, WorkspaceEntryIdentity | None] = {ROOT_ENTRY_PATH: None}

        for item in paths:
            if item.kind not in {"file", "directory"}:
                raise WorkspaceIndexError("Workspace path has an invalid kind.")
            if item.identity is not None and not isinstance(item.identity, WorkspaceEntryIdentity):
                raise WorkspaceIndexError("Workspace path has invalid identity metadata.")
            components = validate_workspace_relative_path(item.path)
            parent = ROOT_ENTRY_PATH
            for offset, component in enumerate(components):
                child_path = "/".join(components[: offset + 1])
                children.setdefault(parent, set()).add(child_path)
                is_terminal = offset == len(components) - 1
                kind = item.kind if is_terminal else "directory"
                existing_kind = kinds.get(child_path)
                if existing_kind is not None and existing_kind != kind:
                    raise WorkspaceIndexError(
                        f"Workspace path is both a file and directory: {child_path}"
                    )
                kinds[child_path] = kind
                if is_terminal and item.identity is not None:
                    existing_identity = identities.get(child_path)
                    if existing_identity is not None and existing_identity != item.identity:
                        raise WorkspaceIndexError(
                            f"Workspace path has conflicting identities: {child_path}"
                        )
                    identities[child_path] = item.identity
                else:
                    identities.setdefault(child_path, None)
                statuses[child_path] = _stronger_status(
                    statuses.get(child_path, "clean"), item.status
                )
                if kind == "directory":
                    children.setdefault(child_path, set())
                parent = child_path

        def entry_id(
            path: str,
            kind: Literal["file", "directory"],
            identity: WorkspaceEntryIdentity | None,
        ) -> str:
            identity_payload = (
                [identity.device, identity.inode, identity.ctime_ns]
                if identity is not None
                else None
            )
            message = json.dumps(
                [workspace_uuid, revision, kind, path, identity_payload],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            return _urlsafe_digest(hmac.digest(secret, message, hashlib.sha256))

        entries: dict[str, WorkspaceIndexEntry] = {}
        for path, kind in kinds.items():
            name = path.rsplit("/", 1)[-1] if path else ""
            entries[path] = WorkspaceIndexEntry(
                entry_id=entry_id(path, kind, identities.get(path)),
                path=path,
                name=name,
                kind=kind,
                status=statuses.get(path, "clean"),
                has_children=bool(children.get(path)),
                identity=identities.get(path),
            )

        index = cls(
            workspace_uuid=workspace_uuid,
            revision=revision,
            entries=entries,
            secret=secret,
        )
        index._children = {
            parent: tuple(
                sorted(
                    child_paths,
                    key=lambda path: (
                        entries[path].kind != "directory",
                        entries[path].name.casefold(),
                        entries[path].name,
                    ),
                )
            )
            for parent, child_paths in children.items()
        }
        return index

    @property
    def root(self) -> WorkspaceIndexEntry:
        return self._entries[ROOT_ENTRY_PATH]

    def entry_for_id(self, entry_id: str) -> WorkspaceIndexEntry:
        if not entry_id:
            raise WorkspaceEntryIdError("Workspace entry identifier is invalid.")
        try:
            return self._entries_by_id[entry_id]
        except KeyError as error:
            raise WorkspaceEntryIdError(
                "Workspace entry identifier is stale or invalid."
            ) from error

    def entry_for_path(self, path: str) -> WorkspaceIndexEntry:
        try:
            return self._entries[path]
        except KeyError as error:
            raise WorkspaceEntryIdError("Workspace entry does not exist.") from error

    def _cursor_signature(self, offset: int) -> bytes:
        message = json.dumps(
            [self.workspace_uuid, self.revision, offset], separators=(",", ":")
        ).encode("utf-8")
        return hmac.digest(self._secret, message, hashlib.sha256)[:16]

    def _encode_cursor(self, offset: int) -> str:
        payload = offset.to_bytes(8, "big") + self._cursor_signature(offset)
        return _urlsafe_digest(payload)

    def _decode_cursor(self, cursor: str | None) -> int:
        if cursor is None:
            return 0
        if not cursor:
            raise WorkspaceCursorError("Workspace tree cursor is invalid.")
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            payload = base64.b64decode(padded, altchars=b"-_", validate=True)
        except (ValueError, TypeError) as error:
            raise WorkspaceCursorError("Workspace tree cursor is invalid.") from error
        if len(payload) != 24:
            raise WorkspaceCursorError("Workspace tree cursor is invalid.")
        offset = int.from_bytes(payload[:8], "big")
        if not hmac.compare_digest(payload[8:], self._cursor_signature(offset)):
            raise WorkspaceCursorError("Workspace tree cursor is stale or invalid.")
        return offset

    def tree_page(
        self,
        *,
        directory_id: str | None = None,
        cursor: str | None = None,
        page_size: int = DEFAULT_TREE_PAGE_SIZE,
    ) -> WorkspaceTreePage:
        if isinstance(page_size, bool) or not 1 <= page_size <= MAX_TREE_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_TREE_PAGE_SIZE}")
        directory = self.root if directory_id is None else self.entry_for_id(directory_id)
        if directory.kind != "directory":
            raise WorkspaceEntryIdError("Workspace entry is not a directory.")
        child_paths = self._children.get(directory.path, ())
        offset = self._decode_cursor(cursor)
        if offset > len(child_paths):
            raise WorkspaceCursorError("Workspace tree cursor is past the directory end.")
        next_offset = min(len(child_paths), offset + page_size)
        page = tuple(self._entries[path] for path in child_paths[offset:next_offset])
        next_cursor = self._encode_cursor(next_offset) if next_offset < len(child_paths) else None
        return WorkspaceTreePage(entries=page, next_cursor=next_cursor)

    def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> tuple[WorkspaceIndexEntry, ...]:
        if not isinstance(query, str):
            raise TypeError("query must be text")
        if isinstance(limit, bool) or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        normalized_query = query.strip().casefold()
        if not normalized_query:
            return ()

        ranked = (
            (score, entry.path.casefold(), entry.path, entry)
            for entry in self._entries.values()
            if entry.path != ROOT_ENTRY_PATH
            if (score := _subsequence_score(normalized_query, entry.path.casefold())) is not None
        )
        best = heapq.nsmallest(limit, ranked, key=lambda item: item[:3])
        return tuple(item[3] for item in best)

    def file_paths(self) -> Sequence[str]:
        return tuple(entry.path for entry in self._entries.values() if entry.kind == "file")
