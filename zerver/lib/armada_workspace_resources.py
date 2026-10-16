"""Backend primitives for the agent's UUID-addressed workspace resources."""

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
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils.timezone import now as timezone_now

from zerver.lib.armada_workspace_index import WorkspaceIndex, WorkspacePath
from zerver.lib.armada_workspace_io import (
    WorkspaceDeleteResult,
    WorkspaceEntryIdentity,
    WorkspaceEntryMutationResult,
    WorkspaceFile,
    WorkspaceHelperRawFileStream,
    WorkspaceRawFileStream,
    WorkspaceStagedRawFileStream,
    WorkspaceWriteResult,
    atomic_write_workspace_file,
    atomic_write_workspace_file_via_helper,
    create_workspace_directory,
    create_workspace_directory_via_helper,
    delete_workspace_entry,
    delete_workspace_entry_via_helper,
    inspect_empty_workspace_directory,
    inspect_empty_workspace_directory_via_helper,
    inspect_workspace_entries,
    inspect_workspace_entries_via_helper,
    open_workspace_file_stream,
    open_workspace_file_stream_via_helper,
    read_workspace_file,
    read_workspace_file_via_helper,
    rename_workspace_entry,
    rename_workspace_entry_via_helper,
    stage_workspace_raw_stream,
    validate_workspace_relative_path,
    workspace_rename_noreplace_supported,
)
from zerver.models.bots import Workspace, WorkspaceEntryMutationReceipt

logger = logging.getLogger(__name__)

ARMADA_WORKSPACE_TEXT_MAX_BYTES = 1_000_000
ARMADA_WORKSPACE_RAW_MAX_BYTES = 64 * 1024 * 1024
ARMADA_WORKSPACE_INDEX_CACHE_SECONDS = 60
ARMADA_WORKSPACE_GIT_TIMEOUT_SECONDS = 15
ARMADA_WORKSPACE_MUTATION_RECEIPT_SECONDS = 10 * 60
ARMADA_WORKSPACE_FILE_OBSERVATION_SECONDS = 60 * 60
DEFAULT_WORKSPACE_HISTORY_LIMIT = 50
MAX_WORKSPACE_HISTORY_LIMIT = 200
ARMADA_WORKSPACE_INSPECTION_BATCH_BYTES = 512 * 1024
ARMADA_WORKSPACE_CREATE_STAGING_PREFIX = ".armada-create-"


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


class WorkspaceMutationReceiptMismatchError(Exception):
    pass


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
    result_identity: WorkspaceEntryIdentity
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
    path_count: int


@dataclass
class _SnapshotBuild:
    ready: threading.Event
    snapshot: WorkspaceSnapshot | None = None
    error: BaseException | None = None


_snapshot_cache: OrderedDict[tuple[str, int], _CachedSnapshot] = OrderedDict()
_snapshot_cache_lock = threading.RLock()
_snapshot_cache_path_count = 0
_snapshot_builds: dict[tuple[str, int], _SnapshotBuild] = {}
_workspace_metrics: Counter[str] = Counter()
_workspace_metrics_lock = threading.Lock()
_workspace_request_state = threading.local()


def record_workspace_metric(name: str) -> None:
    with _workspace_metrics_lock:
        _workspace_metrics[name] += 1


def workspace_metrics_snapshot() -> dict[str, int]:
    with _workspace_metrics_lock:
        return dict(_workspace_metrics)


def consume_workspace_cache_status() -> str | None:
    status = getattr(_workspace_request_state, "cache_status", None)
    _workspace_request_state.cache_status = None
    return status


def _set_workspace_cache_status(status: str) -> None:
    _workspace_request_state.cache_status = status


def _workspace_index_secret() -> bytes:
    secret_key = settings.SECRET_KEY
    if not secret_key:
        raise WorkspaceSnapshotError("Workspace index signing is unavailable.")
    return hashlib.sha256(
        b"armada-workspace-index-v1\0" + secret_key.encode("utf-8", errors="strict")
    ).digest()


def _local_cache_get(workspace_uuid: str, revision: int) -> WorkspaceSnapshot | None:
    global _snapshot_cache_path_count
    key = (workspace_uuid, revision)
    with _snapshot_cache_lock:
        cached = _snapshot_cache.get(key)
        if cached is None:
            record_workspace_metric("snapshot_local_miss")
            return None
        if cached.expires_at <= time.monotonic():
            del _snapshot_cache[key]
            _snapshot_cache_path_count -= cached.path_count
            record_workspace_metric("snapshot_local_expired")
            return None
        _snapshot_cache.move_to_end(key)
        record_workspace_metric("snapshot_local_hit")
        return cached.snapshot


def _local_cache_put(snapshot: WorkspaceSnapshot) -> None:
    global _snapshot_cache_path_count
    key = (snapshot.workspace_uuid, snapshot.revision)
    path_count = len(snapshot.paths)
    with _snapshot_cache_lock:
        previous = _snapshot_cache.pop(key, None)
        if previous is not None:
            _snapshot_cache_path_count -= previous.path_count
        _snapshot_cache[key] = _CachedSnapshot(
            snapshot=snapshot,
            expires_at=time.monotonic() + ARMADA_WORKSPACE_INDEX_CACHE_SECONDS,
            path_count=path_count,
        )
        _snapshot_cache_path_count += path_count
        _snapshot_cache.move_to_end(key)
        max_entries = max(1, int(settings.ARMADA_WORKSPACE_INDEX_LOCAL_MAX_ENTRIES))
        max_paths = max(1, int(settings.ARMADA_WORKSPACE_INDEX_LOCAL_MAX_PATHS))
        while len(_snapshot_cache) > max_entries or (
            len(_snapshot_cache) > 1 and _snapshot_cache_path_count > max_paths
        ):
            _, evicted = _snapshot_cache.popitem(last=False)
            _snapshot_cache_path_count -= evicted.path_count
            record_workspace_metric("snapshot_local_eviction")


def clear_workspace_snapshot_cache() -> None:
    global _snapshot_cache_path_count
    with _snapshot_cache_lock:
        _snapshot_cache.clear()
        _snapshot_cache_path_count = 0


def _join_or_start_snapshot_build(
    key: tuple[str, int],
) -> tuple[_SnapshotBuild, bool]:
    with _snapshot_cache_lock:
        build = _snapshot_builds.get(key)
        if build is not None:
            return build, False
        build = _SnapshotBuild(ready=threading.Event())
        _snapshot_builds[key] = build
        return build, True


def _finish_snapshot_build(
    key: tuple[str, int],
    build: _SnapshotBuild,
    *,
    snapshot: WorkspaceSnapshot | None = None,
    error: BaseException | None = None,
) -> None:
    with _snapshot_cache_lock:
        build.snapshot = snapshot
        build.error = error
        _snapshot_builds.pop(key, None)
        build.ready.set()


def _latest_fingerprint_cache_key(workspace_uuid: str) -> str:
    return f"armada:workspace-index-fingerprint:v1:{workspace_uuid}"


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
    return f"armada:workspace-file-observation:v1:{digest}"


def get_workspace_file_observation(
    workspace_uuid: str,
    revision: int,
    entry_id: str,
) -> str | None:
    try:
        version = cache.get(_file_observation_cache_key(workspace_uuid, revision, entry_id))
    except Exception:
        logger.warning("Could not read workspace file observation", exc_info=True)
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
            timeout=ARMADA_WORKSPACE_FILE_OBSERVATION_SECONDS,
        )
    except Exception:
        logger.warning("Could not store workspace file observation", exc_info=True)


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
    return f"armada:workspace-mutation-receipt:v1:{digest}"


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
                "result_identity": {
                    "device": receipt.result_identity.device,
                    "inode": receipt.result_identity.inode,
                    "ctime_ns": receipt.result_identity.ctime_ns,
                },
                "result_revision": receipt.result_revision,
                "result_entry_id": receipt.result_entry_id,
            },
            timeout=ARMADA_WORKSPACE_MUTATION_RECEIPT_SECONDS,
        )
    except Exception:
        # Losing a receipt makes a later retry fail closed as stale; it never
        # broadens mutation authority.
        logger.warning("Could not store workspace mutation receipt", exc_info=True)


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
    result_identity: WorkspaceEntryIdentity,
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
            result_identity=result_identity,
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
    result_identity: WorkspaceEntryIdentity,
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
            result_identity=result_identity,
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
        logger.warning("Could not read workspace mutation receipt", exc_info=True)
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "state",
        "result_version",
        "result_identity",
        "result_revision",
        "result_entry_id",
    }:
        return None
    state = payload["state"]
    result_version = payload["result_version"]
    result_identity_payload = payload["result_identity"]
    result_revision = payload["result_revision"]
    result_entry_id = payload["result_entry_id"]
    if (
        state not in {"pending", "complete"}
        or not isinstance(result_version, str)
        or not isinstance(result_identity_payload, dict)
        or set(result_identity_payload) != {"device", "inode", "ctime_ns"}
    ):
        return None
    identity_values = (
        result_identity_payload["device"],
        result_identity_payload["inode"],
        result_identity_payload["ctime_ns"],
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in identity_values
    ):
        return None
    result_identity = WorkspaceEntryIdentity(*identity_values)
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
        result_identity=result_identity,
        result_revision=result_revision,
        result_entry_id=result_entry_id,
    )


def _entry_mutation_digest(namespace: bytes, payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(
        _workspace_index_secret(),
        namespace + b"\0" + encoded,
        hashlib.sha256,
    ).hexdigest()


def workspace_entry_mutation_lookup_payload(
    *,
    actor_id: int,
    workspace_uuid: str,
    requested_revision: int,
    operation: Literal["create", "rename", "delete"],
    source_entry_id: str,
    source_path: str,
    destination_path: str,
    options: dict[str, object],
) -> dict[str, object]:
    return {
        "actor_id": actor_id,
        "workspace_uuid": workspace_uuid,
        "requested_revision": requested_revision,
        "operation": operation,
        "source_entry_id": source_entry_id,
        "source_path": source_path,
        "destination_path": destination_path,
        "options": options,
    }


def workspace_entry_mutation_staging_path(lookup_payload: dict[str, object]) -> str:
    lookup_digest = _entry_mutation_digest(b"armada-entry-mutation-lookup-v1", lookup_payload)
    return f"{ARMADA_WORKSPACE_CREATE_STAGING_PREFIX}{lookup_digest}"


def get_workspace_entry_mutation_receipt(
    *,
    lookup_payload: dict[str, object],
) -> WorkspaceEntryMutationReceipt | None:
    lookup_digest = _entry_mutation_digest(b"armada-entry-mutation-lookup-v1", lookup_payload)
    actor_id = lookup_payload.get("actor_id")
    workspace_uuid = lookup_payload.get("workspace_uuid")
    if (
        not isinstance(actor_id, int)
        or isinstance(actor_id, bool)
        or not isinstance(workspace_uuid, str)
    ):
        raise ValueError("Workspace mutation lookup authority is invalid.")
    return WorkspaceEntryMutationReceipt.objects.filter(
        lookup_digest=lookup_digest,
        actor_id=actor_id,
        workspace_node__uuid=workspace_uuid,
    ).first()


def prepare_workspace_entry_mutation_receipt(
    *,
    node: Workspace,
    actor_id: int,
    lookup_payload: dict[str, object],
    source_kind: Literal["file", "directory"] | None,
    source_identity: WorkspaceEntryIdentity | None,
    recovery: dict[str, object] | None = None,
) -> tuple[WorkspaceEntryMutationReceipt, bool]:
    """Durably establish exact retry authority before touching the filesystem."""

    if lookup_payload.get("operation") == "create":
        expected_recovery: dict[str, object] = {
            "staging_path": workspace_entry_mutation_staging_path(lookup_payload),
        }
        if recovery is not None and recovery != expected_recovery:
            raise ValueError("Workspace create recovery metadata is invalid.")
        recovery = expected_recovery
    request_payload = {
        **lookup_payload,
        "source_kind": source_kind,
        "source_identity": (
            {
                "device": source_identity.device,
                "inode": source_identity.inode,
                "ctime_ns": source_identity.ctime_ns,
            }
            if source_identity is not None
            else None
        ),
        "recovery": recovery or {},
    }
    lookup_digest = _entry_mutation_digest(b"armada-entry-mutation-lookup-v1", lookup_payload)
    request_digest = _entry_mutation_digest(b"armada-entry-mutation-request-v1", request_payload)
    operation_value = lookup_payload["operation"]
    if operation_value == "create":
        operation: Literal["create", "rename", "delete"] = "create"
    elif operation_value == "rename":
        operation = "rename"
    elif operation_value == "delete":
        operation = "delete"
    else:
        raise ValueError("Workspace mutation operation is invalid.")
    requested_revision = lookup_payload["requested_revision"]
    if (
        not isinstance(requested_revision, int)
        or isinstance(requested_revision, bool)
        or requested_revision < 0
    ):
        raise ValueError("Workspace mutation revision is invalid.")
    try:
        with transaction.atomic():
            receipt = WorkspaceEntryMutationReceipt.objects.create(
                workspace_node=node,
                actor_id=actor_id,
                lookup_digest=lookup_digest,
                request_digest=request_digest,
                operation=operation,
                requested_revision=requested_revision,
                request_payload=request_payload,
            )
            return receipt, True
    except IntegrityError:
        receipt = WorkspaceEntryMutationReceipt.objects.get(lookup_digest=lookup_digest)
        if receipt.request_digest != request_digest or receipt.request_payload != request_payload:
            raise WorkspaceMutationReceiptMismatchError(
                "Workspace mutation retry does not match its durable receipt."
            )
        return receipt, False


@contextmanager
def lock_workspace_entry_mutation_receipt(
    node: Workspace,
    receipt: WorkspaceEntryMutationReceipt,
) -> Iterator[tuple[Workspace, WorkspaceEntryMutationReceipt]]:
    with transaction.atomic():
        locked_node = Workspace.objects.select_for_update().get(id=node.id)
        locked_receipt = WorkspaceEntryMutationReceipt.objects.select_for_update().get(
            id=receipt.id
        )
        if locked_node.lifecycle_status != Workspace.ACTIVE:
            raise WorkspaceMutationReadOnlyError(int(locked_node.content_revision))
        if (
            WorkspaceEntryMutationReceipt.objects.select_for_update()
            .filter(
                workspace_node=locked_node,
                state__in=(
                    WorkspaceEntryMutationReceipt.STAGED,
                    WorkspaceEntryMutationReceipt.APPLIED,
                ),
            )
            .exclude(id=locked_receipt.id)
            .exists()
        ):
            raise WorkspaceMutationStaleError(int(locked_node.content_revision))
        yield locked_node, locked_receipt


def workspace_has_recoverable_entry_mutation(node: Workspace, *, prepared_before: datetime) -> bool:
    """Whether lock_next_recoverable_workspace_entry_mutation has anything
    to lock, asked without a lock: every read of a worktree asks, and an
    interrupted mutation is rare. One that commits while a read runs is
    the read's to miss, as any write committing after it would be."""
    return WorkspaceEntryMutationReceipt.objects.filter(
        Q(
            state__in=(
                WorkspaceEntryMutationReceipt.STAGED,
                WorkspaceEntryMutationReceipt.APPLIED,
            )
        )
        | Q(
            operation="create",
            state=WorkspaceEntryMutationReceipt.PREPARED,
            date_updated__lte=prepared_before,
        ),
        workspace_node=node,
    ).exists()


@contextmanager
def lock_next_recoverable_workspace_entry_mutation(
    node: Workspace,
    *,
    prepared_before: datetime,
) -> Iterator[tuple[Workspace, WorkspaceEntryMutationReceipt | None]]:
    """Lock one receipt whose interrupted filesystem work can be reconciled.

    Blocking STAGED/APPLIED work is always preferred. Old PREPARED creates are
    considered one at a time so abandoned staging artifacts are bounded without
    turning every Workspace read into an unbounded receipt scan.
    """

    with transaction.atomic():
        locked_node = Workspace.objects.select_for_update().get(id=node.id)
        receipt = (
            WorkspaceEntryMutationReceipt.objects.select_for_update()
            .filter(
                workspace_node=locked_node,
                state__in=(
                    WorkspaceEntryMutationReceipt.STAGED,
                    WorkspaceEntryMutationReceipt.APPLIED,
                ),
            )
            .order_by("date_updated", "id")
            .first()
        )
        if receipt is None:
            receipt = (
                WorkspaceEntryMutationReceipt.objects.select_for_update()
                .filter(
                    workspace_node=locked_node,
                    operation="create",
                    state=WorkspaceEntryMutationReceipt.PREPARED,
                    date_updated__lte=prepared_before,
                )
                .order_by("date_updated", "id")
                .first()
            )
        yield locked_node, receipt


def record_workspace_entry_mutation_stage(
    receipt: WorkspaceEntryMutationReceipt,
    application_payload: dict[str, object],
) -> None:
    if receipt.state == WorkspaceEntryMutationReceipt.STAGED:
        if receipt.application_payload != application_payload:
            raise WorkspaceMutationReceiptMismatchError(
                "Workspace mutation receipt has a different staged result."
            )
        return
    if receipt.state != WorkspaceEntryMutationReceipt.PREPARED:
        raise WorkspaceMutationReceiptMismatchError(
            "Workspace mutation receipt is not awaiting filesystem staging."
        )
    receipt.state = WorkspaceEntryMutationReceipt.STAGED
    receipt.application_payload = application_payload
    receipt.date_updated = timezone_now()
    receipt.save(update_fields=["state", "application_payload", "date_updated"])


def record_workspace_entry_mutation_application(
    receipt: WorkspaceEntryMutationReceipt,
    application_payload: dict[str, object],
) -> None:
    if receipt.state == WorkspaceEntryMutationReceipt.APPLIED:
        if receipt.application_payload != application_payload:
            raise WorkspaceMutationReceiptMismatchError(
                "Workspace mutation receipt has a different applied result."
            )
        return
    if receipt.state == WorkspaceEntryMutationReceipt.STAGED:
        staged_identity = receipt.application_payload.get("identity")
        applied_identity = application_payload.get("identity")
        staged_metadata = {
            key: value for key, value in receipt.application_payload.items() if key != "identity"
        }
        applied_metadata = {
            key: value for key, value in application_payload.items() if key != "identity"
        }
        if (
            receipt.operation != "create"
            or not isinstance(staged_identity, dict)
            or set(staged_identity) != {"device", "inode", "ctime_ns"}
            or not isinstance(applied_identity, dict)
            or set(applied_identity) != {"device", "inode", "ctime_ns"}
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (*staged_identity.values(), *applied_identity.values())
            )
            or staged_identity["device"] != applied_identity["device"]
            or staged_identity["inode"] != applied_identity["inode"]
            or staged_metadata != applied_metadata
        ):
            raise WorkspaceMutationReceiptMismatchError(
                "Workspace mutation receipt has a different staged result."
            )
    elif receipt.state != WorkspaceEntryMutationReceipt.PREPARED:
        raise WorkspaceMutationReceiptMismatchError(
            "Workspace mutation receipt is not awaiting filesystem application."
        )
    receipt.state = WorkspaceEntryMutationReceipt.APPLIED
    receipt.application_payload = application_payload
    receipt.date_updated = timezone_now()
    receipt.save(update_fields=["state", "application_payload", "date_updated"])


def conflict_workspace_entry_mutation_receipt(
    receipt: WorkspaceEntryMutationReceipt,
    result_payload: dict[str, object],
) -> None:
    if receipt.state == WorkspaceEntryMutationReceipt.CONFLICTED:
        if receipt.result_payload != result_payload:
            raise WorkspaceMutationReceiptMismatchError(
                "Workspace mutation receipt has a different conflict result."
            )
        return
    if receipt.state not in {
        WorkspaceEntryMutationReceipt.PREPARED,
        WorkspaceEntryMutationReceipt.STAGED,
        WorkspaceEntryMutationReceipt.APPLIED,
    }:
        raise WorkspaceMutationReceiptMismatchError(
            "Workspace mutation receipt cannot be marked conflicted."
        )
    receipt.state = WorkspaceEntryMutationReceipt.CONFLICTED
    receipt.result_payload = result_payload
    receipt.date_updated = timezone_now()
    receipt.save(update_fields=["state", "result_payload", "date_updated"])


def discard_prepared_workspace_entry_mutation_receipt(
    receipt: WorkspaceEntryMutationReceipt,
) -> None:
    if receipt.state != WorkspaceEntryMutationReceipt.PREPARED:
        raise WorkspaceMutationReceiptMismatchError(
            "Only an unapplied prepared workspace mutation can be discarded."
        )
    receipt.delete()


def complete_workspace_entry_mutation_receipt(
    receipt: WorkspaceEntryMutationReceipt,
    result_payload: dict[str, object],
) -> None:
    if receipt.state == WorkspaceEntryMutationReceipt.COMPLETE:
        if receipt.result_payload != result_payload:
            raise WorkspaceMutationReceiptMismatchError(
                "Workspace mutation receipt has a different completed result."
            )
        return
    if receipt.state != WorkspaceEntryMutationReceipt.APPLIED:
        raise WorkspaceMutationReceiptMismatchError(
            "Workspace mutation receipt was not durably applied."
        )
    receipt.state = WorkspaceEntryMutationReceipt.COMPLETE
    receipt.result_payload = result_payload
    receipt.date_updated = timezone_now()
    receipt.save(update_fields=["state", "result_payload", "date_updated"])


def _run_git(
    node: Workspace,
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
            timeout=ARMADA_WORKSPACE_GIT_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        raise WorkspaceSnapshotError("Could not inspect workspace state.") from error
    if completed.returncode not in accepted_returncodes:
        raise WorkspaceSnapshotError("Could not inspect workspace state.")
    return completed.stdout


def validate_workspace_destination(node: Workspace, path: str) -> str:
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
    if not cursor:
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
    node: Workspace,
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


def _git_output_digest(node: Workspace, arguments: list[str]) -> bytes:
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
    deadline = time.monotonic() + ARMADA_WORKSPACE_GIT_TIMEOUT_SECONDS
    selector = selectors.DefaultSelector()
    try:
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, ARMADA_WORKSPACE_GIT_TIMEOUT_SECONDS)
            events = selector.select(remaining)
            if not events:
                raise subprocess.TimeoutExpired(command, ARMADA_WORKSPACE_GIT_TIMEOUT_SECONDS)
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


def _is_internal_staging_path(path: str) -> bool:
    return any(component.startswith(".armada-") for component in path.split("/"))


def _status_kind(xy: bytes) -> str:
    if b"U" in xy:
        return "conflict"
    if b"D" in xy:
        return "deleted"
    return "changed"


def _parse_status(
    status_output: bytes,
    allowed_paths: set[str],
) -> tuple[dict[str, str], int, bytes]:
    status_by_path: dict[str, str] = {}
    dirty_count = 0
    fingerprint_records: list[bytes] = []
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
        original_path_record: bytes | None = None
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
            original_path_record = records[offset]
            offset += 1  # The original rename path is the next NUL record.
        elif tag == b"u":
            fields = record.split(b" ", 10)
            if len(fields) != 11:
                raise WorkspaceSnapshotError("Git returned an invalid workspace status.")
            raw_path = fields[10]
            status = "conflict"
        else:
            raise WorkspaceSnapshotError("Git returned an invalid workspace status.")
        # Porcelain v2 uses the same trailing-slash marker for an untracked
        # embedded repository. It contributes to the workspace fingerprint
        # and dirty count, but the explicit-directory pass owns its kind.
        status_path = raw_path[:-1] if tag == b"?" and raw_path.endswith(b"/") else raw_path
        path = _decode_git_path(status_path)
        if _is_internal_staging_path(path):
            continue
        dirty_count += 1
        fingerprint_records.extend((record, b"\0"))
        if original_path_record is not None:
            fingerprint_records.extend((original_path_record, b"\0"))
        if path in allowed_paths:
            status_by_path[path] = status
    return status_by_path, dirty_count, b"".join(fingerprint_records)


def inspect_workspace_node_entries(
    node: Workspace,
    expectations: dict[str, Literal["file", "directory"]],
) -> dict[str, WorkspaceEntryIdentity]:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is None:
        inspected = inspect_workspace_entries(
            node.workdir.strip(),
            expectations,
            skip_unsafe=True,
        )
        return {entry.path: entry.identity for entry in inspected}

    batches: list[dict[str, Literal["file", "directory"]]] = []
    current_batch: dict[str, Literal["file", "directory"]] = {}
    current_bytes = 0
    for path, kind in sorted(expectations.items()):
        entry_bytes = len(path.encode("utf-8")) + 64
        if current_batch and current_bytes + entry_bytes > ARMADA_WORKSPACE_INSPECTION_BATCH_BYTES:
            batches.append(current_batch)
            current_batch = {}
            current_bytes = 0
        current_batch[path] = kind
        current_bytes += entry_bytes
    if current_batch:
        batches.append(current_batch)

    identities: dict[str, WorkspaceEntryIdentity] = {}
    for batch in batches:
        inspected = inspect_workspace_entries_via_helper(
            node.workdir.strip(),
            batch,
            skip_unsafe=True,
            isolation_username=isolation_username,
        )
        identities.update((entry.path, entry.identity) for entry in inspected)
    return identities


def inspect_empty_workspace_node_directory(
    node: Workspace,
    path: str,
    *,
    expected_identity: WorkspaceEntryIdentity | None = None,
) -> WorkspaceEntryIdentity:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is not None:
        return inspect_empty_workspace_directory_via_helper(
            node.workdir.strip(),
            path,
            expected_identity=expected_identity,
            isolation_username=isolation_username,
        )
    return inspect_empty_workspace_directory(
        node.workdir.strip(),
        path,
        expected_identity=expected_identity,
    )


def _enumerate_workspace(
    node: Workspace,
) -> tuple[tuple[WorkspacePath, ...], str, str, str, int]:
    paths_output = _run_git(
        node,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
    )
    allowed_paths: set[str] = set()
    for raw_path in paths_output.split(b"\0"):
        if raw_path:
            # An untracked embedded Git repository is reported as an opaque
            # directory marker (for example, ``agent-1/``) even without
            # ``--directory``.  The explicit-directory pass below owns those
            # entries; treating the marker as a file both violates path
            # validation and would represent one path with two kinds.
            if raw_path.endswith(b"/"):
                continue
            path = _decode_git_path(raw_path)
            if not _is_internal_staging_path(path):
                allowed_paths.add(path)
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
    status_by_path, dirty_count, status_fingerprint = _parse_status(status_output, allowed_paths)
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
    directory_paths = set(explicit_directories)
    for path in [*allowed_paths, *explicit_directories]:
        components = validate_workspace_relative_path(path)
        directory_paths.update(
            "/".join(components[:offset]) for offset in range(1, len(components))
        )
    expectations: dict[str, Literal["file", "directory"]] = {}
    for path in allowed_paths:
        expectations[path] = "file"
    for path in directory_paths:
        existing_kind = expectations.get(path)
        if existing_kind == "file":
            raise WorkspaceSnapshotError(f"Workspace path is both a file and directory: {path}")
        expectations[path] = "directory"
    identities = inspect_workspace_node_entries(node, expectations)
    paths = tuple(
        [
            WorkspacePath(
                path,
                status_by_path.get(path, "clean"),
                "file",
                identities.get(path),
            )
            for path in sorted(allowed_paths)
        ]
        + [
            WorkspacePath(
                path,
                "untracked" if path in explicit_directories else "clean",
                "directory",
                identities.get(path),
            )
            for path in sorted(directory_paths)
        ]
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
    for workspace_path in paths:
        fingerprint.update(workspace_path.path.encode("utf-8"))
        fingerprint.update(b"\0")
        fingerprint.update(workspace_path.status.encode("ascii"))
        fingerprint.update(b"\0")
        fingerprint.update(workspace_path.kind.encode("ascii"))
        fingerprint.update(b"\0")
        if workspace_path.identity is not None:
            fingerprint.update(str(workspace_path.identity.device).encode("ascii"))
            fingerprint.update(b":")
            fingerprint.update(str(workspace_path.identity.inode).encode("ascii"))
            fingerprint.update(b":")
            fingerprint.update(str(workspace_path.identity.ctime_ns).encode("ascii"))
        fingerprint.update(b"\0")
    fingerprint.update(status_fingerprint)
    fingerprint.update(tracked_content_digest)
    fingerprint.update(branch.encode("utf-8"))
    fingerprint.update(b"\0")
    fingerprint.update(head.encode("ascii"))
    return paths, fingerprint.hexdigest(), branch, head, dirty_count


def _build_snapshot(
    node: Workspace,
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


def _advance_revision_if_current(node: Workspace, revision: int) -> bool:
    updated = Workspace.objects.filter(id=node.id, content_revision=revision).update(
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
    node: Workspace,
    *,
    force: bool = False,
) -> WorkspaceSnapshot:
    workspace_uuid = str(node.uuid)
    requested_revision = int(node.content_revision)
    if not force:
        cached = _local_cache_get(workspace_uuid, requested_revision)
        if cached is not None:
            _set_workspace_cache_status("local-hit")
            return cached
    else:
        record_workspace_metric("snapshot_forced")

    build_key = (workspace_uuid, requested_revision)
    build, is_builder = _join_or_start_snapshot_build(build_key)
    if not is_builder:
        record_workspace_metric("snapshot_singleflight_wait")
        _set_workspace_cache_status("shared-build")
        build.ready.wait()
        if build.error is not None:
            raise build.error
        if build.snapshot is None:
            raise WorkspaceSnapshotError("Workspace snapshot build produced no result.")
        return build.snapshot

    try:
        record_workspace_metric("snapshot_rebuild")
        _set_workspace_cache_status("rebuild")
        paths, fingerprint, branch, head, dirty_count = _enumerate_workspace(node)
        latest = cache.get(_latest_fingerprint_cache_key(workspace_uuid))
        previous_revision: int | None
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
        previous_revision = latest.get("revision") if isinstance(latest, dict) else None
        if (
            isinstance(previous_revision, int)
            and not isinstance(previous_revision, bool)
            and snapshot.revision > previous_revision
        ):
            from zerver.actions.armada import send_armada_workspace_changed_event

            send_armada_workspace_changed_event(node, snapshot.revision)
        _record_latest_snapshot(snapshot)
    except BaseException as error:
        _finish_snapshot_build(build_key, build, error=error)
        raise
    _finish_snapshot_build(build_key, build, snapshot=snapshot)
    return snapshot


def refresh_workspace_snapshot(
    node: Workspace,
) -> tuple[WorkspaceSnapshot, bool]:
    previous_revision = int(node.content_revision)
    snapshot = get_workspace_snapshot(node, force=True)
    _update_workspace_metadata(node, snapshot)
    return snapshot, snapshot.revision != previous_revision


def _update_workspace_metadata(node: Workspace, snapshot: WorkspaceSnapshot) -> None:
    """Note what a fresh look at the worktree found. Its git facts for the
    topic's header are read again too, in the background."""
    from zerver.lib.armada_workspace_git import request_workspace_git_refresh

    request_workspace_git_refresh(node.id)
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
    node: Workspace,
    *,
    requested_revision: int,
) -> Iterator[Workspace]:
    """Serialize filesystem mutation authority with the database revision.

    The row lock spans the contained filesystem operation and the revision
    bump.  A delayed PUT can therefore run either before a rename/delete or
    after its ID-invalidating bump, but never in the gap between them.
    """

    with transaction.atomic():
        locked = Workspace.objects.select_for_update().get(id=node.id)
        revision = int(locked.content_revision)
        if locked.lifecycle_status != Workspace.ACTIVE:
            raise WorkspaceMutationReadOnlyError(revision)
        if requested_revision != revision:
            raise WorkspaceMutationStaleError(revision)
        if WorkspaceEntryMutationReceipt.objects.filter(
            workspace_node=locked,
            state__in=(
                WorkspaceEntryMutationReceipt.STAGED,
                WorkspaceEntryMutationReceipt.APPLIED,
            ),
        ).exists():
            raise WorkspaceMutationStaleError(revision)
        yield locked


def finish_workspace_entry_mutation(node: Workspace) -> WorkspaceSnapshot:
    node.content_revision = int(node.content_revision) + 1
    node.date_updated = timezone_now()
    node.save(update_fields=["content_revision", "date_updated"])
    snapshot = get_workspace_snapshot(node, force=True)
    _update_workspace_metadata(node, snapshot)
    return snapshot


def read_workspace_node_file(
    node: Workspace,
    path: str,
    *,
    max_bytes: int,
    expected_identity: WorkspaceEntryIdentity | None = None,
) -> WorkspaceFile:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is not None:
        return read_workspace_file_via_helper(
            node.workdir.strip(),
            path,
            max_bytes=max_bytes,
            expected_identity=expected_identity,
            isolation_username=isolation_username,
        )
    return read_workspace_file(
        node.workdir.strip(),
        path,
        max_bytes=max_bytes,
        expected_identity=expected_identity,
    )


def open_workspace_node_file_stream(
    node: Workspace,
    path: str,
    *,
    expected_identity: WorkspaceEntryIdentity | None,
) -> WorkspaceStagedRawFileStream:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is not None:
        source: WorkspaceRawFileStream | WorkspaceHelperRawFileStream = (
            open_workspace_file_stream_via_helper(
                node.workdir.strip(),
                path,
                expected_identity=expected_identity,
                isolation_username=isolation_username,
            )
        )
    else:
        source = open_workspace_file_stream(
            node.workdir.strip(),
            path,
            expected_identity=expected_identity,
        )
    return stage_workspace_raw_stream(
        source,
        max_bytes=ARMADA_WORKSPACE_RAW_MAX_BYTES,
    )


def write_workspace_node_file(
    node: Workspace,
    path: str,
    data: bytes,
    *,
    expected_version: str,
    expected_identity: WorkspaceEntryIdentity | None,
) -> WorkspaceWriteResult:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is not None:
        return atomic_write_workspace_file_via_helper(
            node.workdir.strip(),
            path,
            data,
            max_bytes=ARMADA_WORKSPACE_TEXT_MAX_BYTES,
            expected_version=expected_version,
            expected_identity=expected_identity,
            create=False,
            isolation_username=isolation_username,
        )
    return atomic_write_workspace_file(
        node.workdir.strip(),
        path,
        data,
        max_bytes=ARMADA_WORKSPACE_TEXT_MAX_BYTES,
        expected_version=expected_version,
        expected_identity=expected_identity,
        create=False,
    )


def create_workspace_node_file(
    node: Workspace,
    path: str,
    data: bytes,
) -> WorkspaceWriteResult:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is not None:
        return atomic_write_workspace_file_via_helper(
            node.workdir.strip(),
            path,
            data,
            max_bytes=ARMADA_WORKSPACE_TEXT_MAX_BYTES,
            expected_version=None,
            create=True,
            isolation_username=isolation_username,
        )
    return atomic_write_workspace_file(
        node.workdir.strip(),
        path,
        data,
        max_bytes=ARMADA_WORKSPACE_TEXT_MAX_BYTES,
        expected_version=None,
        create=True,
    )


def create_workspace_node_directory(
    node: Workspace,
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
    node: Workspace,
    source_path: str,
    destination_path: str,
    *,
    expected_kind: Literal["file", "directory"],
    expected_identity: WorkspaceEntryIdentity | None,
) -> WorkspaceEntryMutationResult:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is not None:
        return rename_workspace_entry_via_helper(
            node.workdir.strip(),
            source_path,
            destination_path,
            expected_kind=expected_kind,
            expected_identity=expected_identity,
            isolation_username=isolation_username,
        )
    return rename_workspace_entry(
        node.workdir.strip(),
        source_path,
        destination_path,
        expected_kind=expected_kind,
        expected_identity=expected_identity,
    )


def delete_workspace_node_entry(
    node: Workspace,
    path: str,
    *,
    expected_kind: Literal["file", "directory"],
    expected_identity: WorkspaceEntryIdentity | None,
    recursive: bool,
) -> WorkspaceDeleteResult:
    isolation_username = node.isolation_username.strip() or None
    if isolation_username is not None:
        return delete_workspace_entry_via_helper(
            node.workdir.strip(),
            path,
            expected_kind=expected_kind,
            expected_identity=expected_identity,
            recursive=recursive,
            isolation_username=isolation_username,
        )
    return delete_workspace_entry(
        node.workdir.strip(),
        path,
        expected_kind=expected_kind,
        expected_identity=expected_identity,
        recursive=recursive,
    )


def workspace_entry_mutation_capabilities(node: Workspace) -> dict[str, bool]:
    can_mutate = node.lifecycle_status == Workspace.ACTIVE
    return {
        "create_entry": can_mutate,
        "rename_entry": can_mutate and workspace_rename_noreplace_supported(),
        "delete_entry": can_mutate,
        "history": True,
    }


def refresh_workspace_snapshot_after_conflict(
    node: Workspace,
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
    node: Workspace,
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
        Workspace.objects.filter(id=node.id).update(
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
    node: Workspace,
    path: str,
    data: bytes,
    *,
    expected_version: str,
    expected_identity: WorkspaceEntryIdentity | None,
    requested_revision: int,
    before_finish: Callable[[WorkspaceWriteResult], None],
) -> tuple[WorkspaceWriteResult, WorkspaceSnapshot]:
    with lock_workspace_mutation(node, requested_revision=requested_revision) as locked:
        result = write_workspace_node_file(
            locked,
            path,
            data,
            expected_version=expected_version,
            expected_identity=expected_identity,
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
