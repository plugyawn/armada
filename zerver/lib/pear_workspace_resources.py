"""Backend primitives for Pear's UUID-addressed workspace resources."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import selectors
import subprocess
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils.timezone import now as timezone_now

from zerver.lib.pear_workspace_index import WorkspaceIndex, WorkspacePath
from zerver.lib.pear_workspace_io import (
    WorkspaceDeleteResult,
    WorkspaceEntryMutationResult,
    WorkspaceFile,
    WorkspaceWriteResult,
    atomic_write_workspace_file,
    atomic_write_workspace_file_via_helper,
    create_workspace_directory,
    create_workspace_directory_via_helper,
    delete_workspace_entry,
    delete_workspace_entry_via_helper,
    read_workspace_file,
    read_workspace_file_via_helper,
    rename_workspace_entry,
    rename_workspace_entry_via_helper,
    validate_workspace_relative_path,
    workspace_rename_noreplace_supported,
)
from zerver.models.bots import PearWorkspaceNode

logger = logging.getLogger(__name__)

PEAR_WORKSPACE_TEXT_MAX_BYTES = 1_000_000
PEAR_WORKSPACE_RAW_MAX_BYTES = 16 * 1024 * 1024
PEAR_WORKSPACE_INDEX_CACHE_SECONDS = 60
PEAR_WORKSPACE_INDEX_LOCAL_ENTRIES = 4
PEAR_WORKSPACE_GIT_TIMEOUT_SECONDS = 15
PEAR_WORKSPACE_MUTATION_RECEIPT_SECONDS = 10 * 60
PEAR_WORKSPACE_FILE_OBSERVATION_SECONDS = 60 * 60
DEFAULT_WORKSPACE_HISTORY_LIMIT = 50
MAX_WORKSPACE_HISTORY_LIMIT = 200


class WorkspaceSnapshotError(Exception):
    pass


class WorkspaceMutationStaleError(Exception):
    def __init__(self, revision: int) -> None:
        super().__init__("Workspace mutation authority is stale.")
        self.revision = revision


class WorkspaceMutationReadOnlyError(Exception):
    def __init__(self, revision: int) -> None:
        super().__init__("Workspace is read-only.")
        self.revision = revision


class WorkspaceDestinationPolicyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorkspaceHistoryCursorError(Exception):
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
class WorkspaceMutationReceipt:
    state: str
    result_version: str
    result_revision: int | None
    result_entry_id: str | None


@dataclass(frozen=True)
class WorkspaceHistoryEntry:
    commit: str
    authored_at: str
    author_name: str
    subject: str


@dataclass(frozen=True)
class WorkspaceHistoryPage:
    entries: tuple[WorkspaceHistoryEntry, ...]
    next_cursor: str | None


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


def _file_observation_cache_key(
    workspace_uuid: str,
    revision: int,
    entry_id: str,
) -> str:
    message = json.dumps(
        [workspace_uuid, revision, entry_id],
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hmac.new(_workspace_index_secret(), message, hashlib.sha256).hexdigest()
    return f"pear:workspace-file-observation:v1:{digest}"


def get_workspace_file_observation(
    workspace_uuid: str,
    revision: int,
    entry_id: str,
) -> str | None:
    try:
        version = cache.get(_file_observation_cache_key(workspace_uuid, revision, entry_id))
    except Exception:
        logger.warning("Could not read Pear workspace file observation", exc_info=True)
        return None
    if not isinstance(version, str) or len(version) != 64:
        return None
    return version


def record_workspace_file_observation(
    workspace_uuid: str,
    revision: int,
    entry_id: str,
    version: str,
) -> None:
    try:
        cache.set(
            _file_observation_cache_key(workspace_uuid, revision, entry_id),
            version,
            timeout=PEAR_WORKSPACE_FILE_OBSERVATION_SECONDS,
        )
    except Exception:
        logger.warning("Could not store Pear workspace file observation", exc_info=True)


def _mutation_receipt_cache_key(
    *,
    actor_id: int,
    workspace_uuid: str,
    requested_revision: int,
    entry_id: str,
    expected_path: str,
    base_version: str,
    content: bytes,
    edit_revision: int,
) -> str:
    message = json.dumps(
        [
            actor_id,
            workspace_uuid,
            requested_revision,
            entry_id,
            expected_path,
            base_version,
            hashlib.sha256(content).hexdigest(),
            edit_revision,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hmac.new(_workspace_index_secret(), message, hashlib.sha256).hexdigest()
    return f"pear:workspace-mutation-receipt:v1:{digest}"


def _store_workspace_mutation_receipt(
    *,
    actor_id: int,
    workspace_uuid: str,
    requested_revision: int,
    entry_id: str,
    expected_path: str,
    base_version: str,
    content: bytes,
    edit_revision: int,
    receipt: WorkspaceMutationReceipt,
) -> None:
    key = _mutation_receipt_cache_key(
        actor_id=actor_id,
        workspace_uuid=workspace_uuid,
        requested_revision=requested_revision,
        entry_id=entry_id,
        expected_path=expected_path,
        base_version=base_version,
        content=content,
        edit_revision=edit_revision,
    )
    try:
        cache.set(
            key,
            {
                "state": receipt.state,
                "result_version": receipt.result_version,
                "result_revision": receipt.result_revision,
                "result_entry_id": receipt.result_entry_id,
            },
            timeout=PEAR_WORKSPACE_MUTATION_RECEIPT_SECONDS,
        )
    except Exception:
        # Losing a receipt makes a later retry fail closed as stale; it never
        # broadens mutation authority.
        logger.warning("Could not store Pear workspace mutation receipt", exc_info=True)


def record_pending_workspace_mutation(
    *,
    actor_id: int,
    workspace_uuid: str,
    requested_revision: int,
    entry_id: str,
    expected_path: str,
    base_version: str,
    content: bytes,
    edit_revision: int,
    result_version: str,
) -> None:
    _store_workspace_mutation_receipt(
        actor_id=actor_id,
        workspace_uuid=workspace_uuid,
        requested_revision=requested_revision,
        entry_id=entry_id,
        expected_path=expected_path,
        base_version=base_version,
        content=content,
        edit_revision=edit_revision,
        receipt=WorkspaceMutationReceipt(
            state="pending",
            result_version=result_version,
            result_revision=None,
            result_entry_id=None,
        ),
    )


def record_completed_workspace_mutation(
    *,
    actor_id: int,
    workspace_uuid: str,
    requested_revision: int,
    entry_id: str,
    expected_path: str,
    base_version: str,
    content: bytes,
    edit_revision: int,
    result_version: str,
    result_revision: int,
    result_entry_id: str,
) -> None:
    _store_workspace_mutation_receipt(
        actor_id=actor_id,
        workspace_uuid=workspace_uuid,
        requested_revision=requested_revision,
        entry_id=entry_id,
        expected_path=expected_path,
        base_version=base_version,
        content=content,
        edit_revision=edit_revision,
        receipt=WorkspaceMutationReceipt(
            state="complete",
            result_version=result_version,
            result_revision=result_revision,
            result_entry_id=result_entry_id,
        ),
    )


def get_workspace_mutation_receipt(
    *,
    actor_id: int,
    workspace_uuid: str,
    requested_revision: int,
    entry_id: str,
    expected_path: str,
    base_version: str,
    content: bytes,
    edit_revision: int,
) -> WorkspaceMutationReceipt | None:
    key = _mutation_receipt_cache_key(
        actor_id=actor_id,
        workspace_uuid=workspace_uuid,
        requested_revision=requested_revision,
        entry_id=entry_id,
        expected_path=expected_path,
        base_version=base_version,
        content=content,
        edit_revision=edit_revision,
    )
    try:
        payload = cache.get(key)
    except Exception:
        logger.warning("Could not read Pear workspace mutation receipt", exc_info=True)
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "state",
        "result_version",
        "result_revision",
        "result_entry_id",
    }:
        return None
    state = payload["state"]
    result_version = payload["result_version"]
    result_revision = payload["result_revision"]
    result_entry_id = payload["result_entry_id"]
    if state not in {"pending", "complete"} or not isinstance(result_version, str):
        return None
    if state == "pending":
        if result_revision is not None or result_entry_id is not None:
            return None
    elif (
        isinstance(result_revision, bool)
        or not isinstance(result_revision, int)
        or result_revision < 0
        or not isinstance(result_entry_id, str)
        or not result_entry_id
    ):
        return None
    return WorkspaceMutationReceipt(
        state=state,
        result_version=result_version,
        result_revision=result_revision,
        result_entry_id=result_entry_id,
    )


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


def validate_workspace_destination(node: PearWorkspaceNode, path: str) -> str:
    components = validate_workspace_relative_path(path)
    if any(component.startswith(".") for component in components):
        raise WorkspaceDestinationPolicyError(
            "hidden_destination",
            "Hidden workspace destinations are not allowed.",
        )
    normalized = "/".join(components)
    ignored_output = _run_git(
        node,
        ["check-ignore", "--no-index", "--", normalized],
        accepted_returncodes=frozenset({0, 1}),
    )
    if ignored_output:
        raise WorkspaceDestinationPolicyError(
            "ignored_destination",
            "Git-ignored workspace destinations are not allowed.",
        )
    return normalized


def _history_cursor_signature(
    snapshot: WorkspaceSnapshot,
    entry_id: str,
    offset: int,
) -> bytes:
    message = json.dumps(
        [snapshot.workspace_uuid, snapshot.revision, entry_id, offset],
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.digest(_workspace_index_secret(), message, hashlib.sha256)[:16]


def _encode_history_cursor(
    snapshot: WorkspaceSnapshot,
    entry_id: str,
    offset: int,
) -> str:
    payload = offset.to_bytes(8, "big") + _history_cursor_signature(
        snapshot,
        entry_id,
        offset,
    )
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_history_cursor(
    snapshot: WorkspaceSnapshot,
    entry_id: str,
    cursor: str | None,
) -> int:
    if cursor is None:
        return 0
    if not isinstance(cursor, str) or not cursor:
        raise WorkspaceHistoryCursorError("Workspace history cursor is invalid.")
    try:
        payload = base64.b64decode(
            cursor + "=" * (-len(cursor) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError, TypeError) as error:
        raise WorkspaceHistoryCursorError("Workspace history cursor is invalid.") from error
    if len(payload) != 24:
        raise WorkspaceHistoryCursorError("Workspace history cursor is invalid.")
    offset = int.from_bytes(payload[:8], "big")
    if not hmac.compare_digest(
        payload[8:],
        _history_cursor_signature(snapshot, entry_id, offset),
    ):
        raise WorkspaceHistoryCursorError("Workspace history cursor is stale or invalid.")
    return offset


def get_workspace_history(
    node: PearWorkspaceNode,
    snapshot: WorkspaceSnapshot,
    *,
    entry_id: str,
    path: str,
    cursor: str | None = None,
    limit: int = DEFAULT_WORKSPACE_HISTORY_LIMIT,
) -> WorkspaceHistoryPage:
    if isinstance(limit, bool) or not 1 <= limit <= MAX_WORKSPACE_HISTORY_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_WORKSPACE_HISTORY_LIMIT}")
    offset = _decode_history_cursor(snapshot, entry_id, cursor)
    output = _run_git(
        node,
        [
            "log",
            "-z",
            "--follow",
            f"--max-count={limit + 1}",
            f"--skip={offset}",
            "--format=%H%x00%aI%x00%an%x00%s",
            "--",
            path,
        ],
    )
    raw_fields = output.split(b"\0")
    if raw_fields and raw_fields[-1] == b"":
        raw_fields.pop()
    if len(raw_fields) % 4 != 0:
        raise WorkspaceSnapshotError("Git returned invalid workspace history.")
    entries: list[WorkspaceHistoryEntry] = []
    try:
        for field_offset in range(0, len(raw_fields), 4):
            commit = raw_fields[field_offset].decode("ascii", errors="strict")
            authored_at = raw_fields[field_offset + 1].decode("ascii", errors="strict")
            author_name = raw_fields[field_offset + 2].decode("utf-8", errors="strict")
            subject = raw_fields[field_offset + 3].decode("utf-8", errors="strict")
            if len(commit) != 40 or any(
                character not in "0123456789abcdef" for character in commit
            ):
                raise WorkspaceSnapshotError("Git returned invalid workspace history.")
            entries.append(
                WorkspaceHistoryEntry(
                    commit=commit,
                    authored_at=authored_at,
                    author_name=author_name,
                    subject=subject,
                )
            )
    except UnicodeDecodeError as error:
        raise WorkspaceSnapshotError("Git returned invalid workspace history.") from error
    has_more = len(entries) > limit
    page_entries = tuple(entries[:limit])
    return WorkspaceHistoryPage(
        entries=page_entries,
        next_cursor=(
            _encode_history_cursor(snapshot, entry_id, offset + limit) if has_more else None
        ),
    )


def _git_output_digest(node: PearWorkspaceNode, arguments: list[str]) -> bytes:
    if not node.workdir.strip():
        raise WorkspaceSnapshotError("Workspace storage is unavailable.")
    command = ["git", "-C", node.workdir.strip(), *arguments]
    isolation_username = node.isolation_username.strip()
    if isolation_username:
        command = ["sudo", "-n", "-u", isolation_username, "/usr/bin/env", *command]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError) as error:
        raise WorkspaceSnapshotError("Could not inspect workspace content state.") from error
    assert process.stdout is not None
    digest = hashlib.sha256()
    deadline = time.monotonic() + PEAR_WORKSPACE_GIT_TIMEOUT_SECONDS
    selector = selectors.DefaultSelector()
    try:
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, PEAR_WORKSPACE_GIT_TIMEOUT_SECONDS)
            events = selector.select(remaining)
            if not events:
                raise subprocess.TimeoutExpired(command, PEAR_WORKSPACE_GIT_TIMEOUT_SECONDS)
            for key, _mask in events:
                try:
                    chunk = os.read(key.fd, 64 * 1024)
                except BlockingIOError:  # nocoverage: selector reported readiness.
                    continue
                if chunk:
                    digest.update(chunk)
                else:
                    selector.unregister(key.fileobj)
        remaining = max(0.0, deadline - time.monotonic())
        returncode = process.wait(timeout=remaining)
    except (OSError, subprocess.TimeoutExpired) as error:
        process.kill()
        process.wait()
        raise WorkspaceSnapshotError("Could not inspect workspace content state.") from error
    finally:
        selector.close()
        process.stdout.close()
    if returncode != 0:
        raise WorkspaceSnapshotError("Could not inspect workspace content state.")
    return digest.digest()


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


def _parse_status(
    status_output: bytes,
    allowed_paths: set[str],
) -> tuple[dict[str, str], int]:
    status_by_path: dict[str, str] = {}
    dirty_count = 0
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
        dirty_count += 1
        path = _decode_git_path(raw_path)
        if path in allowed_paths:
            status_by_path[path] = status
    return status_by_path, dirty_count


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
    deleted_output = _run_git(node, ["ls-files", "-z", "--deleted"])
    for raw_path in deleted_output.split(b"\0"):
        if raw_path:
            allowed_paths.discard(_decode_git_path(raw_path))

    # Git does not version directories, but its untracked-directory mode is a
    # valuable policy boundary: it reports real empty directories while still
    # applying the repository's standard ignore rules.  We only retain
    # non-hidden directory names; hidden workspace state is never made into a
    # client-addressable mutation target merely because it is empty.
    directories_output = _run_git(
        node,
        ["ls-files", "-z", "--others", "--exclude-standard", "--directory"],
    )
    explicit_directories: set[str] = set()
    for raw_path in directories_output.split(b"\0"):
        if not raw_path or not raw_path.endswith(b"/"):
            continue
        path = _decode_git_path(raw_path[:-1])
        components = validate_workspace_relative_path(path)
        if any(component.startswith(".") for component in components):
            continue
        explicit_directories.add(path)

    status_output = _run_git(
        node,
        ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
    )
    status_by_path, dirty_count = _parse_status(status_output, allowed_paths)
    tracked_content_digest = _git_output_digest(
        node,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            "--no-renames",
            "--",
        ],
    )
    paths = tuple(
        [WorkspacePath(path, status_by_path.get(path, "clean")) for path in sorted(allowed_paths)]
        + [WorkspacePath(path, "untracked", "directory") for path in sorted(explicit_directories)]
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
        fingerprint.update(path.kind.encode("ascii"))
        fingerprint.update(b"\0")
    fingerprint.update(status_output)
    fingerprint.update(tracked_content_digest)
    fingerprint.update(branch.encode("utf-8"))
    fingerprint.update(b"\0")
    fingerprint.update(head.encode("ascii"))
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


@contextmanager
def lock_workspace_mutation(
    node: PearWorkspaceNode,
    *,
    requested_revision: int,
) -> Iterator[PearWorkspaceNode]:
    """Serialize filesystem mutation authority with the database revision.

    The row lock spans the contained filesystem operation and the revision
    bump.  A delayed PUT can therefore run either before a rename/delete or
    after its ID-invalidating bump, but never in the gap between them.
    """

    with transaction.atomic():
        locked = PearWorkspaceNode.objects.select_for_update().get(id=node.id)
        revision = int(locked.content_revision)
        if locked.lifecycle_status != PearWorkspaceNode.ACTIVE:
            raise WorkspaceMutationReadOnlyError(revision)
        if requested_revision != revision:
            raise WorkspaceMutationStaleError(revision)
        yield locked


def finish_workspace_entry_mutation(node: PearWorkspaceNode) -> WorkspaceSnapshot:
    node.content_revision = int(node.content_revision) + 1
    node.date_updated = timezone_now()
    node.save(update_fields=["content_revision", "date_updated"])
    snapshot = get_workspace_snapshot(node, force=True)
    _update_workspace_metadata(node, snapshot)
    return snapshot


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


def create_workspace_node_file(
    node: PearWorkspaceNode,
    path: str,
    data: bytes,
) -> WorkspaceWriteResult:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is not None:
        return atomic_write_workspace_file_via_helper(
            node.workdir.strip(),
            path,
            data,
            max_bytes=PEAR_WORKSPACE_TEXT_MAX_BYTES,
            expected_version=None,
            create=True,
            isolation_username=isolation_username,
        )
    return atomic_write_workspace_file(
        node.workdir.strip(),
        path,
        data,
        max_bytes=PEAR_WORKSPACE_TEXT_MAX_BYTES,
        expected_version=None,
        create=True,
    )


def create_workspace_node_directory(
    node: PearWorkspaceNode,
    path: str,
) -> WorkspaceEntryMutationResult:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is not None:
        return create_workspace_directory_via_helper(
            node.workdir.strip(),
            path,
            isolation_username=isolation_username,
        )
    return create_workspace_directory(node.workdir.strip(), path)


def rename_workspace_node_entry(
    node: PearWorkspaceNode,
    source_path: str,
    destination_path: str,
    *,
    expected_kind: Literal["file", "directory"],
) -> WorkspaceEntryMutationResult:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is not None:
        return rename_workspace_entry_via_helper(
            node.workdir.strip(),
            source_path,
            destination_path,
            expected_kind=expected_kind,
            isolation_username=isolation_username,
        )
    return rename_workspace_entry(
        node.workdir.strip(),
        source_path,
        destination_path,
        expected_kind=expected_kind,
    )


def delete_workspace_node_entry(
    node: PearWorkspaceNode,
    path: str,
    *,
    expected_kind: Literal["file", "directory"],
    recursive: bool,
) -> WorkspaceDeleteResult:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is not None:
        return delete_workspace_entry_via_helper(
            node.workdir.strip(),
            path,
            expected_kind=expected_kind,
            recursive=recursive,
            isolation_username=isolation_username,
        )
    return delete_workspace_entry(
        node.workdir.strip(),
        path,
        expected_kind=expected_kind,
        recursive=recursive,
    )


def workspace_entry_mutation_capabilities(node: PearWorkspaceNode) -> dict[str, bool]:
    can_mutate = node.lifecycle_status == PearWorkspaceNode.ACTIVE
    return {
        "create_entry": can_mutate,
        "rename_entry": can_mutate and workspace_rename_noreplace_supported(),
        "delete_entry": can_mutate,
        "history": True,
    }


def refresh_workspace_snapshot_after_conflict(
    node: PearWorkspaceNode,
    *,
    observed_revision: int,
) -> WorkspaceSnapshot:
    node.refresh_from_db()
    if node.content_revision == observed_revision:
        _advance_revision_if_current(node, observed_revision)
    snapshot = get_workspace_snapshot(node, force=True)
    _update_workspace_metadata(node, snapshot)
    return snapshot


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


def write_workspace_node_file_at_revision(
    node: PearWorkspaceNode,
    path: str,
    data: bytes,
    *,
    expected_version: str,
    requested_revision: int,
    before_finish: Callable[[WorkspaceWriteResult], None],
) -> tuple[WorkspaceWriteResult, WorkspaceSnapshot]:
    with lock_workspace_mutation(node, requested_revision=requested_revision) as locked:
        result = write_workspace_node_file(
            locked,
            path,
            data,
            expected_version=expected_version,
        )
        before_finish(result)
        snapshot = finish_workspace_write(
            locked,
            requested_revision=requested_revision,
            base_version=expected_version,
            result=result,
        )
    return result, snapshot


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
