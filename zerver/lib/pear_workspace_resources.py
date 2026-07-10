"""Backend primitives for Pear's UUID-addressed workspace resources."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.utils.timezone import now as timezone_now

from zerver.lib.pear_workspace_index import (
    WorkspaceEntryIdError,
    WorkspaceIndex,
    WorkspaceIndexEntry,
    WorkspacePath,
)
from zerver.lib.pear_workspace_io import (
    WorkspaceFile,
    WorkspaceWriteResult,
    atomic_write_workspace_file,
    atomic_write_workspace_file_via_helper,
    read_workspace_file,
    read_workspace_file_via_helper,
    validate_workspace_relative_path,
)
from zerver.models.bots import PearWorkspaceNode

logger = logging.getLogger(__name__)

PEAR_WORKSPACE_TEXT_MAX_BYTES = 1_000_000
PEAR_WORKSPACE_RAW_MAX_BYTES = 16 * 1024 * 1024
PEAR_WORKSPACE_INDEX_CACHE_SECONDS = 60
PEAR_WORKSPACE_INDEX_LOCAL_ENTRIES = 4
PEAR_WORKSPACE_GIT_TIMEOUT_SECONDS = 15


class WorkspaceSnapshotError(Exception):
    pass


@dataclass(frozen=True)
class WorkspaceSnapshot:
    workspace_uuid: str
    revision: int
    paths: tuple[WorkspacePath, ...]
    index: WorkspaceIndex
    fingerprint: str
    branch: str
    head: str
    dirty_count: int


@dataclass(frozen=True)
class _CachedSnapshot:
    snapshot: WorkspaceSnapshot
    expires_at: float


_snapshot_cache: OrderedDict[tuple[str, int], _CachedSnapshot] = OrderedDict()
_snapshot_cache_lock = threading.RLock()


def _workspace_index_secret() -> bytes:
    secret_key = settings.SECRET_KEY
    if not isinstance(secret_key, str) or not secret_key:
        raise WorkspaceSnapshotError("Workspace index signing is unavailable.")
    return hashlib.sha256(
        b"pear-workspace-index-v1\0" + secret_key.encode("utf-8", errors="strict")
    ).digest()


def _local_cache_get(workspace_uuid: str, revision: int) -> WorkspaceSnapshot | None:
    key = (workspace_uuid, revision)
    with _snapshot_cache_lock:
        cached = _snapshot_cache.get(key)
        if cached is None:
            return None
        if cached.expires_at <= time.monotonic():
            del _snapshot_cache[key]
            return None
        _snapshot_cache.move_to_end(key)
        return cached.snapshot


def _local_cache_put(snapshot: WorkspaceSnapshot) -> None:
    key = (snapshot.workspace_uuid, snapshot.revision)
    with _snapshot_cache_lock:
        _snapshot_cache[key] = _CachedSnapshot(
            snapshot=snapshot,
            expires_at=time.monotonic() + PEAR_WORKSPACE_INDEX_CACHE_SECONDS,
        )
        _snapshot_cache.move_to_end(key)
        while len(_snapshot_cache) > PEAR_WORKSPACE_INDEX_LOCAL_ENTRIES:
            _snapshot_cache.popitem(last=False)


def clear_workspace_snapshot_cache() -> None:
    with _snapshot_cache_lock:
        _snapshot_cache.clear()


def _latest_fingerprint_cache_key(workspace_uuid: str) -> str:
    return f"pear:workspace-index-fingerprint:v1:{workspace_uuid}"


def _run_git(
    node: PearWorkspaceNode,
    arguments: list[str],
    *,
    accepted_returncodes: frozenset[int] = frozenset({0}),
) -> bytes:
    if not node.workdir.strip():
        raise WorkspaceSnapshotError("Workspace storage is unavailable.")
    command = ["git", "-C", node.workdir.strip(), *arguments]
    isolation_username = node.isolation_username.strip()
    if isolation_username:
        command = ["sudo", "-n", "-u", isolation_username, "/usr/bin/env", *command]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=PEAR_WORKSPACE_GIT_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        raise WorkspaceSnapshotError("Could not inspect workspace state.") from error
    if completed.returncode not in accepted_returncodes:
        raise WorkspaceSnapshotError("Could not inspect workspace state.")
    return completed.stdout


def _decode_git_path(raw_path: bytes) -> str:
    try:
        path = raw_path.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise WorkspaceSnapshotError("Workspace contains a non-UTF-8 path.") from error
    validate_workspace_relative_path(path)
    return path


def _status_kind(xy: bytes) -> str:
    if b"U" in xy:
        return "conflict"
    if b"D" in xy:
        return "deleted"
    return "changed"


def _parse_status(status_output: bytes, allowed_paths: set[str]) -> dict[str, str]:
    status_by_path: dict[str, str] = {}
    records = status_output.split(b"\0")
    offset = 0
    while offset < len(records):
        record = records[offset]
        offset += 1
        if not record or record.startswith(b"# "):
            continue
        tag = record[:1]
        raw_path: bytes
        status: str
        if tag == b"?" and record.startswith(b"? "):
            raw_path = record[2:]
            status = "untracked"
        elif tag == b"1":
            fields = record.split(b" ", 8)
            if len(fields) != 9:
                raise WorkspaceSnapshotError("Git returned an invalid workspace status.")
            raw_path = fields[8]
            status = _status_kind(fields[1])
        elif tag == b"2":
            fields = record.split(b" ", 9)
            if len(fields) != 10 or offset >= len(records):
                raise WorkspaceSnapshotError("Git returned an invalid workspace status.")
            raw_path = fields[9]
            status = _status_kind(fields[1])
            offset += 1  # The original rename path is the next NUL record.
        elif tag == b"u":
            fields = record.split(b" ", 10)
            if len(fields) != 11:
                raise WorkspaceSnapshotError("Git returned an invalid workspace status.")
            raw_path = fields[10]
            status = "conflict"
        else:
            raise WorkspaceSnapshotError("Git returned an invalid workspace status.")
        path = _decode_git_path(raw_path)
        if path in allowed_paths:
            status_by_path[path] = status
    return status_by_path


def _enumerate_workspace(
    node: PearWorkspaceNode,
) -> tuple[tuple[WorkspacePath, ...], str, str, str, int]:
    paths_output = _run_git(
        node,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
    )
    allowed_paths: set[str] = set()
    for raw_path in paths_output.split(b"\0"):
        if raw_path:
            allowed_paths.add(_decode_git_path(raw_path))

    status_output = _run_git(
        node,
        ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
    )
    status_by_path = _parse_status(status_output, allowed_paths)
    paths = tuple(
        WorkspacePath(path, status_by_path.get(path, "clean")) for path in sorted(allowed_paths)
    )

    branch_output = _run_git(
        node,
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
        accepted_returncodes=frozenset({0, 1}),
    )
    head_output = _run_git(
        node,
        ["rev-parse", "--verify", "HEAD"],
        accepted_returncodes=frozenset({0, 128}),
    )
    try:
        branch = branch_output.decode("utf-8", errors="strict").strip()
        head = head_output.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise WorkspaceSnapshotError("Git returned invalid workspace metadata.") from error

    fingerprint = hashlib.sha256()
    for path in paths:
        fingerprint.update(path.path.encode("utf-8"))
        fingerprint.update(b"\0")
        fingerprint.update(path.status.encode("ascii"))
        fingerprint.update(b"\0")
    fingerprint.update(status_output)
    fingerprint.update(branch.encode("utf-8"))
    fingerprint.update(b"\0")
    fingerprint.update(head.encode("ascii"))
    dirty_count = sum(path.status != "clean" for path in paths)
    return paths, fingerprint.hexdigest(), branch, head, dirty_count


def _build_snapshot(
    node: PearWorkspaceNode,
    *,
    paths: tuple[WorkspacePath, ...],
    fingerprint: str,
    branch: str,
    head: str,
    dirty_count: int,
) -> WorkspaceSnapshot:
    workspace_uuid = str(node.uuid)
    revision = int(node.content_revision)
    return WorkspaceSnapshot(
        workspace_uuid=workspace_uuid,
        revision=revision,
        paths=paths,
        index=WorkspaceIndex.build(
            workspace_uuid=workspace_uuid,
            revision=revision,
            paths=paths,
            secret=_workspace_index_secret(),
        ),
        fingerprint=fingerprint,
        branch=branch or node.branch_name.strip(),
        head=head,
        dirty_count=dirty_count,
    )


def _advance_revision_if_current(node: PearWorkspaceNode, revision: int) -> bool:
    updated = PearWorkspaceNode.objects.filter(id=node.id, content_revision=revision).update(
        content_revision=F("content_revision") + 1,
        date_updated=timezone_now(),
    )
    node.refresh_from_db()
    return bool(updated == 1)


def _record_latest_snapshot(snapshot: WorkspaceSnapshot) -> None:
    cache.set(
        _latest_fingerprint_cache_key(snapshot.workspace_uuid),
        {"revision": snapshot.revision, "fingerprint": snapshot.fingerprint},
        timeout=24 * 60 * 60,
    )
    _local_cache_put(snapshot)


def get_workspace_snapshot(
    node: PearWorkspaceNode,
    *,
    force: bool = False,
) -> WorkspaceSnapshot:
    workspace_uuid = str(node.uuid)
    if not force:
        cached = _local_cache_get(workspace_uuid, int(node.content_revision))
        if cached is not None:
            return cached

    paths, fingerprint, branch, head, dirty_count = _enumerate_workspace(node)
    latest = cache.get(_latest_fingerprint_cache_key(workspace_uuid))
    if (
        isinstance(latest, dict)
        and latest.get("revision") == node.content_revision
        and isinstance(latest.get("fingerprint"), str)
        and latest["fingerprint"] != fingerprint
    ):
        previous_revision = int(node.content_revision)
        _advance_revision_if_current(node, previous_revision)

    snapshot = _build_snapshot(
        node,
        paths=paths,
        fingerprint=fingerprint,
        branch=branch,
        head=head,
        dirty_count=dirty_count,
    )
    _record_latest_snapshot(snapshot)
    return snapshot


def refresh_workspace_snapshot(
    node: PearWorkspaceNode,
) -> tuple[WorkspaceSnapshot, bool]:
    previous_revision = int(node.content_revision)
    snapshot = get_workspace_snapshot(node, force=True)
    _update_workspace_metadata(node, snapshot)
    return snapshot, snapshot.revision != previous_revision


def _update_workspace_metadata(node: PearWorkspaceNode, snapshot: WorkspaceSnapshot) -> None:
    update_fields: list[str] = []
    for field, value in (
        ("branch_name", snapshot.branch),
        ("head_commit", snapshot.head),
        ("dirty", snapshot.dirty_count > 0),
        ("changed_count", snapshot.dirty_count),
    ):
        if getattr(node, field) != value:
            setattr(node, field, value)
            update_fields.append(field)
    if update_fields:
        node.date_updated = timezone_now()
        node.save(update_fields=[*update_fields, "date_updated"])


def workspace_index_for_revision(
    snapshot: WorkspaceSnapshot,
    revision: int,
) -> WorkspaceIndex:
    if isinstance(revision, bool) or revision < 0:
        raise WorkspaceEntryIdError("Workspace revision is invalid.")
    return WorkspaceIndex.build(
        workspace_uuid=snapshot.workspace_uuid,
        revision=revision,
        paths=snapshot.paths,
        secret=_workspace_index_secret(),
    )


def resolve_stale_retry_entry(
    snapshot: WorkspaceSnapshot,
    *,
    requested_revision: int,
    entry_id: str,
    expected_path: str,
) -> WorkspaceIndexEntry:
    path = "/".join(validate_workspace_relative_path(expected_path))
    stale_index = workspace_index_for_revision(snapshot, requested_revision)
    stale_entry = stale_index.entry_for_id(entry_id)
    if stale_entry.path != path or stale_entry.kind != "file":
        raise WorkspaceEntryIdError("Workspace entry identifier is stale or invalid.")
    current_entry = snapshot.index.entry_for_path(path)
    if current_entry.kind != "file":
        raise WorkspaceEntryIdError("Workspace entry identifier is stale or invalid.")
    return current_entry


def read_workspace_node_file(
    node: PearWorkspaceNode,
    path: str,
    *,
    max_bytes: int,
) -> WorkspaceFile:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is not None:
        return read_workspace_file_via_helper(
            node.workdir.strip(),
            path,
            max_bytes=max_bytes,
            isolation_username=isolation_username,
        )
    return read_workspace_file(node.workdir.strip(), path, max_bytes=max_bytes)


def write_workspace_node_file(
    node: PearWorkspaceNode,
    path: str,
    data: bytes,
    *,
    expected_version: str,
) -> WorkspaceWriteResult:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is not None:
        return atomic_write_workspace_file_via_helper(
            node.workdir.strip(),
            path,
            data,
            max_bytes=PEAR_WORKSPACE_TEXT_MAX_BYTES,
            expected_version=expected_version,
            create=False,
            isolation_username=isolation_username,
        )
    return atomic_write_workspace_file(
        node.workdir.strip(),
        path,
        data,
        max_bytes=PEAR_WORKSPACE_TEXT_MAX_BYTES,
        expected_version=expected_version,
        create=False,
    )


def finish_workspace_write(
    node: PearWorkspaceNode,
    *,
    requested_revision: int,
    base_version: str,
    result: WorkspaceWriteResult,
) -> WorkspaceSnapshot:
    node.refresh_from_db()
    missed_bump = (
        not result.changed
        and result.file.version != base_version
        and node.content_revision == requested_revision
    )
    if result.changed:
        PearWorkspaceNode.objects.filter(id=node.id).update(
            content_revision=F("content_revision") + 1,
            date_updated=timezone_now(),
        )
        node.refresh_from_db()
    elif missed_bump:
        _advance_revision_if_current(node, requested_revision)
    snapshot = get_workspace_snapshot(node, force=True)
    _update_workspace_metadata(node, snapshot)
    return snapshot


def workspace_mime_type(path: str) -> str:
    guessed, _encoding = mimetypes.guess_type(path, strict=False)
    return guessed or "application/octet-stream"


def workspace_language(path: str) -> str:
    extension = path.rsplit(".", 1)[-1].casefold() if "." in path else ""
    return {
        "c": "c",
        "cc": "cpp",
        "cpp": "cpp",
        "css": "css",
        "go": "go",
        "h": "cpp",
        "html": "html",
        "java": "java",
        "js": "javascript",
        "json": "json",
        "jsx": "javascript",
        "md": "markdown",
        "mdx": "markdown",
        "py": "python",
        "rb": "ruby",
        "rs": "rust",
        "sh": "shell",
        "sql": "sql",
        "svg": "xml",
        "toml": "toml",
        "ts": "typescript",
        "tsx": "typescript",
        "xml": "xml",
        "yaml": "yaml",
        "yml": "yaml",
    }.get(extension, "plain")


def workspace_rich_mode_eligible(path: str) -> bool:
    return path.casefold().endswith((".md", ".markdown"))
