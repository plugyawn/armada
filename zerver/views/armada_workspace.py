from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable
from datetime import timedelta
from functools import wraps
from typing import Annotated, Literal, ParamSpec, TypeVar, cast
from uuid import UUID

from django.http import HttpRequest, HttpResponse, HttpResponseBase, StreamingHttpResponse
from django.utils.http import content_disposition_header
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _
from pydantic import Json

from zerver.lib.armada_workspace_index import (
    WorkspaceCursorError,
    WorkspaceEntryIdError,
    WorkspaceIndexEntry,
)
from zerver.lib.armada_workspace_io import (
    InvalidWorkspacePathError,
    UnsafeWorkspaceEntryError,
    WorkspaceDirectoryNotEmptyError,
    WorkspaceEntryChangedError,
    WorkspaceEntryExistsError,
    WorkspaceEntryIdentity,
    WorkspaceEntryNotFoundError,
    WorkspaceFile,
    WorkspaceFileTooLargeError,
    WorkspaceInvalidUTF8Error,
    WorkspaceIOError,
    WorkspaceOperationUnsupportedError,
    WorkspaceVersionConflictError,
    WorkspaceWriteResult,
    decode_workspace_text,
    validate_workspace_relative_path,
)
from zerver.lib.armada_workspace_resources import (
    PEAR_WORKSPACE_RAW_MAX_BYTES,
    PEAR_WORKSPACE_TEXT_MAX_BYTES,
    WorkspaceDestinationPolicyError,
    WorkspaceHistoryCursorError,
    WorkspaceMutationReadOnlyError,
    WorkspaceMutationReceiptMismatchError,
    WorkspaceMutationStaleError,
    WorkspaceSnapshot,
    WorkspaceSnapshotError,
    complete_workspace_entry_mutation_receipt,
    conflict_workspace_entry_mutation_receipt,
    consume_workspace_cache_status,
    create_workspace_node_directory,
    create_workspace_node_file,
    delete_workspace_node_entry,
    discard_prepared_workspace_entry_mutation_receipt,
    finish_workspace_entry_mutation,
    get_workspace_entry_mutation_receipt,
    get_workspace_file_observation,
    get_workspace_history,
    get_workspace_mutation_receipt,
    get_workspace_snapshot,
    inspect_empty_workspace_node_directory,
    lock_next_recoverable_workspace_entry_mutation,
    lock_workspace_entry_mutation_receipt,
    open_workspace_node_file_stream,
    prepare_workspace_entry_mutation_receipt,
    read_workspace_node_file,
    record_completed_workspace_mutation,
    record_pending_workspace_mutation,
    record_workspace_entry_mutation_application,
    record_workspace_entry_mutation_stage,
    record_workspace_file_observation,
    record_workspace_metric,
    refresh_workspace_snapshot,
    refresh_workspace_snapshot_after_conflict,
    rename_workspace_node_entry,
    validate_workspace_destination,
    workspace_entry_mutation_capabilities,
    workspace_entry_mutation_lookup_payload,
    workspace_entry_mutation_staging_path,
    workspace_language,
    workspace_metrics_snapshot,
    workspace_mime_type,
    workspace_rich_mode_eligible,
    write_workspace_node_file_at_revision,
)
from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_response, json_success
from zerver.lib.streams import access_stream_by_name
from zerver.lib.typed_endpoint import INTENTIONALLY_UNDOCUMENTED, ApiParamConfig, typed_endpoint
from zerver.models.bots import Workspace, WorkspaceEntryMutationReceipt
from zerver.models.users import UserProfile

logger = logging.getLogger(__name__)

PEAR_WORKSPACE_PREPARED_RECOVERY_GRACE_SECONDS = 30
PEAR_WORKSPACE_RECOVERY_BATCH_SIZE = 8
_P = ParamSpec("_P")
_ResponseT = TypeVar("_ResponseT", bound=HttpResponseBase)


def workspace_server_timing(
    operation: str,
) -> Callable[[Callable[_P, _ResponseT]], Callable[_P, _ResponseT]]:
    def decorate(view: Callable[_P, _ResponseT]) -> Callable[_P, _ResponseT]:
        @wraps(view)
        def timed(*args: _P.args, **kwargs: _P.kwargs) -> _ResponseT:
            consume_workspace_cache_status()
            started = time.perf_counter()
            try:
                response = view(*args, **kwargs)
            except Exception:
                record_workspace_metric(f"response_{operation}_exception")
                raise
            duration_ms = (time.perf_counter() - started) * 1000
            cache_status = consume_workspace_cache_status()
            timings = [f'pear_workspace;dur={duration_ms:.2f};desc="{operation}"']
            if cache_status is not None:
                timings.append(f'pear_workspace_cache;desc="{cache_status}"')
            response["Server-Timing"] = ", ".join(timings)
            response_metric = f"response_{operation}_{response.status_code // 100}xx"
            record_workspace_metric(response_metric)
            response["X-Pear-Workspace-Operation-Count"] = str(
                workspace_metrics_snapshot()[response_metric]
            )
            return response

        return timed

    return decorate


def _workspace_error(
    *,
    code: str,
    message: str,
    status: int,
    revision: int | None = None,
    data: dict[str, object] | None = None,
) -> HttpResponse:
    payload: dict[str, object] = {"error_code": code}
    if revision is not None:
        payload["workspace_revision"] = revision
    if data is not None:
        payload.update(data)
    return json_response("error", msg=message, data=payload, status=status)


def _access_workspace_node(
    user_profile: UserProfile,
    workspace_uuid: UUID,
) -> Workspace:
    node = (
        Workspace.objects.select_related("project")
        .filter(realm=user_profile.realm, uuid=workspace_uuid)
        .first()
    )
    if node is None:
        raise JsonableError(_("Pear workspace not found."))
    if not node.stream_name:
        raise JsonableError(_("Pear workspace has no channel."))
    access_stream_by_name(user_profile, node.stream_name)
    if not node.workdir.strip():
        raise JsonableError(_("Pear workspace storage is unavailable."))
    return node


def _entry_payload(entry: WorkspaceIndexEntry) -> dict[str, object]:
    return {
        "entry_id": entry.entry_id,
        "path": entry.path,
        "name": entry.name,
        "kind": entry.kind,
        "status": entry.status,
        "has_children": entry.has_children,
        "size": None,
    }


def _workspace_descriptor_payload(
    node: Workspace,
    snapshot: WorkspaceSnapshot,
) -> dict[str, object]:
    default_entry: WorkspaceIndexEntry | None
    try:
        candidate = snapshot.index.entry_for_path("README.md")
        default_entry = (
            candidate if candidate.kind == "file" and candidate.status != "deleted" else None
        )
    except WorkspaceEntryIdError:
        default_entry = None
    can_write = node.lifecycle_status == Workspace.ACTIVE
    mutation_capabilities = workspace_entry_mutation_capabilities(node)
    topic_label = node.topic_name or _("general chat")
    return {
        "uuid": snapshot.workspace_uuid,
        "display_root": f"{node.stream_name} > {topic_label}",
        "stream": node.stream_name,
        "topic": node.topic_name,
        "branch": snapshot.branch,
        "head": snapshot.head,
        "dirty": snapshot.dirty_count > 0,
        "dirty_count": snapshot.dirty_count,
        "workspace_revision": snapshot.revision,
        "root": _entry_payload(snapshot.index.root),
        "default_path": default_entry.path if default_entry is not None else None,
        "default_entry": _entry_payload(default_entry) if default_entry is not None else None,
        "capabilities": {
            "read_file": True,
            "write_file": can_write,
            "refresh": True,
            **mutation_capabilities,
        },
        "limits": {
            "text_file_max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES,
            "raw_file_max_bytes": PEAR_WORKSPACE_RAW_MAX_BYTES,
        },
    }


def _snapshot_or_error(
    node: Workspace,
    *,
    force: bool = False,
) -> WorkspaceSnapshot | HttpResponse:
    try:
        recovered_snapshot = _reconcile_interrupted_entry_mutations(node)
        if recovered_snapshot is not None:
            return recovered_snapshot
        return get_workspace_snapshot(node, force=force)
    except (
        WorkspaceMutationReceiptMismatchError,
        WorkspaceSnapshotError,
        WorkspaceIOError,
    ):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not inspect this workspace.",
            status=503,
            revision=int(node.content_revision),
        )


def _resolve_file_entry(
    snapshot: WorkspaceSnapshot,
    entry_id: str,
) -> WorkspaceIndexEntry | HttpResponse:
    try:
        entry = snapshot.index.entry_for_id(entry_id)
    except WorkspaceEntryIdError:
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    if entry.kind != "file":
        return _workspace_error(
            code="unsupported_file",
            message="Workspace entry is not a file.",
            status=415,
            revision=snapshot.revision,
            data={"entry": _entry_payload(entry)},
        )
    if entry.identity is None:
        return _workspace_error(
            code="unsafe_entry",
            message="Workspace entry has no safe filesystem identity.",
            status=415,
            revision=snapshot.revision,
            data={"entry": _entry_payload(entry)},
        )
    return entry


def _resolve_mutable_entry(
    snapshot: WorkspaceSnapshot,
    entry_id: str,
) -> WorkspaceIndexEntry | HttpResponse:
    try:
        entry = snapshot.index.entry_for_id(entry_id)
    except WorkspaceEntryIdError:
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    if not entry.path or entry.kind not in {"file", "directory"}:
        return _workspace_error(
            code="invalid_entry",
            message="The workspace root cannot be mutated.",
            status=400,
            revision=snapshot.revision,
        )
    if entry.identity is None:
        return _workspace_error(
            code="unsafe_entry",
            message="Workspace entry has no safe filesystem identity.",
            status=415,
            revision=snapshot.revision,
            data={"entry": _entry_payload(entry)},
        )
    return entry


def _validate_destination_against_snapshot(
    node: Workspace,
    snapshot: WorkspaceSnapshot,
    destination_path: str,
) -> str | HttpResponse:
    try:
        normalized = validate_workspace_destination(node, destination_path)
    except (InvalidWorkspacePathError, WorkspaceDestinationPolicyError) as error:
        code = error.code if isinstance(error, WorkspaceDestinationPolicyError) else "invalid_path"
        return _workspace_error(
            code=code,
            message=str(error),
            status=400,
            revision=snapshot.revision,
        )
    try:
        existing_entry = snapshot.index.entry_for_path(normalized)
    except WorkspaceEntryIdError:
        pass
    else:
        if existing_entry.status != "deleted":
            return _workspace_error(
                code="already_exists",
                message="Workspace destination already exists.",
                status=409,
                revision=snapshot.revision,
            )
    parent_path = normalized.rpartition("/")[0]
    if parent_path:
        try:
            parent = snapshot.index.entry_for_path(parent_path)
        except WorkspaceEntryIdError:
            return _workspace_error(
                code="destination_parent_missing",
                message="Workspace destination parent does not exist.",
                status=409,
                revision=snapshot.revision,
            )
        if parent.kind != "directory":
            return _workspace_error(
                code="destination_parent_missing",
                message="Workspace destination parent is not a directory.",
                status=409,
                revision=snapshot.revision,
            )
        if parent.identity is None:
            return _workspace_error(
                code="unsafe_entry",
                message="Workspace destination parent has no safe filesystem identity.",
                status=415,
                revision=snapshot.revision,
            )
    return normalized


def _locked_mutation_error_response(
    node: Workspace,
    error: WorkspaceMutationReadOnlyError | WorkspaceMutationStaleError,
) -> HttpResponse:
    if isinstance(error, WorkspaceMutationReadOnlyError):
        return _workspace_error(
            code="workspace_read_only",
            message="Workspace is read-only.",
            status=403,
            revision=error.revision,
        )
    node.refresh_from_db()
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    return _stale_workspace_response(snapshot)


def _entry_mutation_payload(
    snapshot: WorkspaceSnapshot,
    *,
    operation: Literal["create", "rename", "delete"],
    previous_entry_id: str | None,
    entry: WorkspaceIndexEntry | None,
    path: str,
    removed_entries: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "operation": operation,
        "workspace_uuid": snapshot.workspace_uuid,
        "workspace_revision": snapshot.revision,
        "previous_entry_id": previous_entry_id,
        "entry": _entry_payload(entry) if entry is not None else None,
        "path": path,
    }
    if removed_entries is not None:
        payload["removed_entries"] = removed_entries
    return payload


def _terminal_entry_mutation_response(
    request: HttpRequest,
    receipt: WorkspaceEntryMutationReceipt | None,
) -> HttpResponse | None:
    if receipt is None:
        return None
    if receipt.state == WorkspaceEntryMutationReceipt.CONFLICTED:
        revision = receipt.result_payload.get("workspace_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise WorkspaceMutationReceiptMismatchError(
                "Conflicted workspace mutation receipt has no valid revision."
            )
        return _workspace_error(
            code="stale_workspace",
            message="Workspace changed while the mutation was being completed.",
            status=409,
            revision=revision,
        )
    if receipt.state != WorkspaceEntryMutationReceipt.COMPLETE:
        return None
    if not isinstance(receipt.result_payload, dict) or not receipt.result_payload:
        raise WorkspaceMutationReceiptMismatchError(
            "Completed workspace mutation receipt has no result payload."
        )
    return json_success(
        request,
        data={"pear_workspace_mutation": receipt.result_payload},
    )


def _receipt_source_identity(
    receipt: WorkspaceEntryMutationReceipt,
) -> WorkspaceEntryIdentity | None:
    return _validated_receipt_identity(
        receipt.request_payload.get("source_identity"),
        label="source",
    )


def _receipt_application_identity(
    receipt: WorkspaceEntryMutationReceipt,
) -> WorkspaceEntryIdentity | None:
    return _validated_receipt_identity(
        receipt.application_payload.get("identity"),
        label="application",
    )


def _validated_receipt_identity(
    payload: object,
    *,
    label: str,
) -> WorkspaceEntryIdentity | None:
    if payload is None:
        return None
    if not isinstance(payload, dict) or set(payload) != {"device", "inode", "ctime_ns"}:
        raise WorkspaceMutationReceiptMismatchError(
            f"Workspace mutation receipt has invalid {label} identity."
        )
    values = (payload["device"], payload["inode"], payload["ctime_ns"])
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise WorkspaceMutationReceiptMismatchError(
            f"Workspace mutation receipt has invalid {label} identity."
        )
    return WorkspaceEntryIdentity(*values)


def _identity_payload(identity: WorkspaceEntryIdentity) -> dict[str, int]:
    return {
        "device": identity.device,
        "inode": identity.inode,
        "ctime_ns": identity.ctime_ns,
    }


def _same_workspace_inode(
    left: WorkspaceEntryIdentity,
    right: WorkspaceEntryIdentity,
) -> bool:
    return left.device == right.device and left.inode == right.inode


def _live_entry_for_path(
    snapshot: WorkspaceSnapshot,
    path: str,
) -> WorkspaceIndexEntry | None:
    try:
        entry = snapshot.index.entry_for_path(path)
    except WorkspaceEntryIdError:
        return None
    if entry.status == "deleted":
        return None
    return entry


def _has_live_descendants(snapshot: WorkspaceSnapshot, path: str) -> bool:
    prefix = f"{path}/"
    return any(item.status != "deleted" and item.path.startswith(prefix) for item in snapshot.paths)


def _finish_prepared_entry_mutation(
    node: Workspace,
    receipt: WorkspaceEntryMutationReceipt,
    observed_snapshot: WorkspaceSnapshot,
) -> WorkspaceSnapshot:
    revision = int(node.content_revision)
    requested_revision = int(receipt.requested_revision)
    if revision == requested_revision:
        return finish_workspace_entry_mutation(node)
    if revision == requested_revision + 1:
        if observed_snapshot.revision == revision:
            return observed_snapshot
        return get_workspace_snapshot(node, force=True)
    raise WorkspaceMutationStaleError(revision)


def _create_receipt_metadata(
    receipt: WorkspaceEntryMutationReceipt,
) -> tuple[Literal["file", "directory"], str, str, str]:
    request_payload = receipt.request_payload
    options = request_payload.get("options")
    destination = request_payload.get("destination_path")
    recovery = request_payload.get("recovery")
    if (
        receipt.operation != "create"
        or not isinstance(options, dict)
        or not isinstance(destination, str)
        or not isinstance(recovery, dict)
    ):
        raise WorkspaceMutationReceiptMismatchError(
            "Workspace create receipt has invalid request metadata."
        )
    kind = options.get("kind")
    content_sha256 = options.get("content_sha256")
    staging_path = recovery.get("staging_path")
    if (
        kind not in {"file", "directory"}
        or not isinstance(content_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", content_sha256) is None
        or not isinstance(staging_path, str)
    ):
        raise WorkspaceMutationReceiptMismatchError(
            "Workspace create receipt has invalid staging metadata."
        )
    lookup_keys = (
        "actor_id",
        "workspace_uuid",
        "requested_revision",
        "operation",
        "source_entry_id",
        "source_path",
        "destination_path",
        "options",
    )
    if any(key not in request_payload for key in lookup_keys):
        raise WorkspaceMutationReceiptMismatchError(
            "Workspace create receipt has incomplete lookup metadata."
        )
    lookup_payload = {key: request_payload[key] for key in lookup_keys}
    if staging_path != workspace_entry_mutation_staging_path(lookup_payload):
        raise WorkspaceMutationReceiptMismatchError(
            "Workspace create receipt has an invalid staging path."
        )
    normalized_destination = "/".join(validate_workspace_relative_path(destination))
    validate_workspace_relative_path(staging_path)
    return (
        "file" if kind == "file" else "directory",
        normalized_destination,
        staging_path,
        content_sha256,
    )


def _inspect_created_path(
    node: Workspace,
    path: str,
    *,
    kind: Literal["file", "directory"],
    content_sha256: str,
    expected_identity: WorkspaceEntryIdentity | None,
) -> WorkspaceEntryIdentity | None:
    try:
        if kind == "file":
            file = read_workspace_node_file(
                node,
                path,
                max_bytes=PEAR_WORKSPACE_TEXT_MAX_BYTES,
                expected_identity=expected_identity,
            )
            if hashlib.sha256(file.data).hexdigest() != content_sha256:
                raise WorkspaceEntryChangedError(
                    "Workspace staged file content does not match its receipt."
                )
            if file.identity is None:
                raise WorkspaceEntryChangedError("Workspace staged file has no safe identity.")
            return file.identity
        return inspect_empty_workspace_node_directory(
            node,
            path,
            expected_identity=expected_identity,
        )
    except WorkspaceEntryNotFoundError:
        return None


def _staged_create_application_payload(
    receipt: WorkspaceEntryMutationReceipt,
    identity: WorkspaceEntryIdentity,
) -> dict[str, object]:
    kind, _destination, staging_path, content_sha256 = _create_receipt_metadata(receipt)
    return {
        "identity": _identity_payload(identity),
        "kind": kind,
        "staging_path": staging_path,
        "content_sha256": content_sha256,
    }


def _stage_prepared_create(
    node: Workspace,
    receipt: WorkspaceEntryMutationReceipt,
    content: bytes,
) -> None:
    kind, _destination, staging_path, content_sha256 = _create_receipt_metadata(receipt)
    if hashlib.sha256(content).hexdigest() != content_sha256:
        raise WorkspaceMutationReceiptMismatchError(
            "Workspace create retry content does not match its receipt."
        )
    identity = _inspect_created_path(
        node,
        staging_path,
        kind=kind,
        content_sha256=content_sha256,
        expected_identity=None,
    )
    if identity is None:
        if kind == "file":
            result = create_workspace_node_file(node, staging_path, content)
            if result.file.identity is None:
                raise WorkspaceEntryChangedError("Workspace staged file has no safe identity.")
            identity = result.file.identity
        else:
            create_workspace_node_directory(node, staging_path)
            identity = inspect_empty_workspace_node_directory(node, staging_path)
    record_workspace_entry_mutation_stage(
        receipt,
        _staged_create_application_payload(receipt, identity),
    )


def _validated_staged_create_identity(
    receipt: WorkspaceEntryMutationReceipt,
) -> WorkspaceEntryIdentity:
    kind, _destination, staging_path, content_sha256 = _create_receipt_metadata(receipt)
    payload = receipt.application_payload
    if (
        not isinstance(payload, dict)
        or payload.get("kind") != kind
        or payload.get("staging_path") != staging_path
        or payload.get("content_sha256") != content_sha256
    ):
        raise WorkspaceMutationReceiptMismatchError(
            "Workspace create receipt has invalid staged application metadata."
        )
    identity = _receipt_application_identity(receipt)
    if identity is None:
        raise WorkspaceMutationReceiptMismatchError(
            "Workspace create receipt has no staged identity."
        )
    return identity


def _publish_staged_create(
    node: Workspace,
    receipt: WorkspaceEntryMutationReceipt,
    observed_snapshot: WorkspaceSnapshot,
) -> None:
    kind, destination, staging_path, content_sha256 = _create_receipt_metadata(receipt)
    identity = _validated_staged_create_identity(receipt)
    staged_identity = _inspect_created_path(
        node,
        staging_path,
        kind=kind,
        content_sha256=content_sha256,
        expected_identity=identity,
    )
    destination_entry = _live_entry_for_path(observed_snapshot, destination)
    published_identity: WorkspaceEntryIdentity | None
    if staged_identity is not None:
        if (
            int(node.content_revision) != int(receipt.requested_revision)
            or destination_entry is not None
        ):
            raise WorkspaceMutationStaleError(int(node.content_revision))
        locked_destination = _validate_destination_against_snapshot(
            node,
            observed_snapshot,
            destination,
        )
        if isinstance(locked_destination, HttpResponse):
            raise WorkspaceMutationStaleError(int(node.content_revision))
        result = rename_workspace_node_entry(
            node,
            staging_path,
            locked_destination,
            expected_kind=kind,
            expected_identity=identity,
        )
        published_identity = result.identity or (
            _inspect_created_path(
                node,
                destination,
                kind=kind,
                content_sha256=content_sha256,
                expected_identity=None,
            )
        )
    else:
        # A crash can happen after the no-replace rename and before the STAGED
        # transaction records APPLIED. Only the exact staged inode is accepted.
        destination_identity = _inspect_created_path(
            node,
            destination,
            kind=kind,
            content_sha256=content_sha256,
            expected_identity=None,
        )
        if destination_identity is None or not _same_workspace_inode(
            destination_identity, identity
        ):
            raise WorkspaceMutationStaleError(int(node.content_revision))
        published_identity = destination_identity
    if published_identity is None or not _same_workspace_inode(published_identity, identity):
        raise WorkspaceMutationStaleError(int(node.content_revision))
    application_payload = dict(receipt.application_payload)
    application_payload["identity"] = _identity_payload(published_identity)
    record_workspace_entry_mutation_application(receipt, application_payload)


def _remove_exact_create_staging(
    node: Workspace,
    receipt: WorkspaceEntryMutationReceipt,
) -> None:
    try:
        kind, _destination, staging_path, content_sha256 = _create_receipt_metadata(receipt)
        identity = _validated_staged_create_identity(receipt)
        if (
            _inspect_created_path(
                node,
                staging_path,
                kind=kind,
                content_sha256=content_sha256,
                expected_identity=identity,
            )
            != identity
        ):
            return
        delete_workspace_node_entry(
            node,
            staging_path,
            expected_kind=kind,
            expected_identity=identity,
            recursive=False,
        )
    except (
        InvalidWorkspacePathError,
        UnsafeWorkspaceEntryError,
        WorkspaceDirectoryNotEmptyError,
        WorkspaceEntryChangedError,
        WorkspaceEntryNotFoundError,
        WorkspaceIOError,
        WorkspaceMutationReceiptMismatchError,
    ):
        # A replaced staging object belongs to the concurrent writer. It is
        # intentionally left untouched even though the receipt is released.
        return


def _advance_snapshot_for_receipt_conflict(
    node: Workspace,
    receipt: WorkspaceEntryMutationReceipt,
    observed_snapshot: WorkspaceSnapshot,
) -> WorkspaceSnapshot:
    if int(node.content_revision) == int(receipt.requested_revision):
        return finish_workspace_entry_mutation(node)
    if observed_snapshot.revision == int(node.content_revision):
        return observed_snapshot
    return get_workspace_snapshot(node, force=True)


def _conflict_interrupted_entry_mutation(
    node: Workspace,
    receipt: WorkspaceEntryMutationReceipt,
    observed_snapshot: WorkspaceSnapshot,
) -> WorkspaceSnapshot:
    original_state = receipt.state
    if receipt.operation == "create" and original_state == WorkspaceEntryMutationReceipt.STAGED:
        _remove_exact_create_staging(node, receipt)
    snapshot = observed_snapshot
    if original_state in {
        WorkspaceEntryMutationReceipt.PREPARED,
        WorkspaceEntryMutationReceipt.STAGED,
        WorkspaceEntryMutationReceipt.APPLIED,
    }:
        snapshot = _advance_snapshot_for_receipt_conflict(node, receipt, observed_snapshot)
    conflict_workspace_entry_mutation_receipt(
        receipt,
        {"workspace_revision": snapshot.revision},
    )
    return snapshot


def _complete_applied_entry_mutation(
    node: Workspace,
    receipt: WorkspaceEntryMutationReceipt,
    observed_snapshot: WorkspaceSnapshot,
) -> WorkspaceSnapshot:
    request_payload = receipt.request_payload
    if receipt.operation not in {"create", "rename", "delete"}:
        raise WorkspaceMutationReceiptMismatchError(
            "Workspace mutation receipt has an invalid operation."
        )
    operation = cast(Literal["create", "rename", "delete"], receipt.operation)
    source_path = request_payload.get("source_path")
    destination_path = request_payload.get("destination_path")
    source_entry_id = request_payload.get("source_entry_id")
    source_kind = request_payload.get("source_kind")
    if (
        not isinstance(source_path, str)
        or not isinstance(destination_path, str)
        or not isinstance(source_entry_id, str)
    ):
        raise WorkspaceMutationReceiptMismatchError(
            "Workspace mutation receipt has invalid path metadata."
        )

    previous_entry_id: str | None
    entry: WorkspaceIndexEntry | None
    path: str
    removed_entries: int | None = None
    if operation == "create":
        kind, destination, _staging_path, content_sha256 = _create_receipt_metadata(receipt)
        identity = _validated_staged_create_identity(receipt)
        if (
            _inspect_created_path(
                node,
                destination,
                kind=kind,
                content_sha256=content_sha256,
                expected_identity=identity,
            )
            != identity
        ):
            raise WorkspaceMutationStaleError(int(node.content_revision))
        live_entry = _live_entry_for_path(observed_snapshot, destination)
        if live_entry is None or live_entry.kind != kind or live_entry.identity != identity:
            raise WorkspaceMutationStaleError(int(node.content_revision))
        previous_entry_id = None
        entry = live_entry
        path = destination
    elif operation == "rename":
        if source_kind not in {"file", "directory"}:
            raise WorkspaceMutationReceiptMismatchError(
                "Workspace rename receipt has invalid source kind."
            )
        source_identity = _receipt_source_identity(receipt)
        application_identity = _receipt_application_identity(receipt)
        destination_entry = _live_entry_for_path(observed_snapshot, destination_path)
        if (
            source_identity is None
            or application_identity is None
            or not _same_workspace_inode(application_identity, source_identity)
            or _live_entry_for_path(observed_snapshot, source_path) is not None
            or destination_entry is None
            or destination_entry.kind != source_kind
            or destination_entry.identity != application_identity
        ):
            raise WorkspaceMutationStaleError(int(node.content_revision))
        previous_entry_id = source_entry_id
        entry = destination_entry
        path = destination_path
    elif operation == "delete":
        source_identity = _receipt_source_identity(receipt)
        application_identity = _receipt_application_identity(receipt)
        removed_entries = receipt.application_payload.get("removed_entries")
        if (
            source_identity is None
            or application_identity != source_identity
            or _live_entry_for_path(observed_snapshot, source_path) is not None
            or isinstance(removed_entries, bool)
            or not isinstance(removed_entries, int)
            or removed_entries < 1
        ):
            raise WorkspaceMutationStaleError(int(node.content_revision))
        previous_entry_id = source_entry_id
        entry = None
        path = source_path
    saved_snapshot = _finish_prepared_entry_mutation(node, receipt, observed_snapshot)
    if entry is not None:
        entry = saved_snapshot.index.entry_for_path(path)
    mutation_payload = _entry_mutation_payload(
        saved_snapshot,
        operation=operation,
        previous_entry_id=previous_entry_id,
        entry=entry,
        path=path,
        removed_entries=removed_entries,
    )
    complete_workspace_entry_mutation_receipt(receipt, mutation_payload)
    return saved_snapshot


def _reconcile_interrupted_entry_mutations(
    node: Workspace,
) -> WorkspaceSnapshot | None:
    prepared_before = timezone_now() - timedelta(
        seconds=PEAR_WORKSPACE_PREPARED_RECOVERY_GRACE_SECONDS
    )
    latest_snapshot: WorkspaceSnapshot | None = None
    for _attempt in range(PEAR_WORKSPACE_RECOVERY_BATCH_SIZE):
        with lock_next_recoverable_workspace_entry_mutation(
            node,
            prepared_before=prepared_before,
        ) as (locked, receipt):
            if receipt is None:
                return latest_snapshot
            observed_snapshot = get_workspace_snapshot(locked, force=True)
            try:
                if receipt.state == WorkspaceEntryMutationReceipt.PREPARED:
                    kind, _destination, staging_path, content_sha256 = _create_receipt_metadata(
                        receipt
                    )
                    identity = _inspect_created_path(
                        locked,
                        staging_path,
                        kind=kind,
                        content_sha256=content_sha256,
                        expected_identity=None,
                    )
                    if identity is None:
                        discard_prepared_workspace_entry_mutation_receipt(receipt)
                        latest_snapshot = observed_snapshot
                    else:
                        record_workspace_entry_mutation_stage(
                            receipt,
                            _staged_create_application_payload(receipt, identity),
                        )
                    continue
                if receipt.state == WorkspaceEntryMutationReceipt.STAGED:
                    _publish_staged_create(locked, receipt, observed_snapshot)
                    continue
                if receipt.state == WorkspaceEntryMutationReceipt.APPLIED:
                    latest_snapshot = _complete_applied_entry_mutation(
                        locked,
                        receipt,
                        observed_snapshot,
                    )
                    continue
                raise WorkspaceMutationReceiptMismatchError(
                    "Workspace recovery selected a terminal receipt."
                )
            except (
                InvalidWorkspacePathError,
                UnsafeWorkspaceEntryError,
                WorkspaceDirectoryNotEmptyError,
                WorkspaceEntryChangedError,
                WorkspaceEntryExistsError,
                WorkspaceEntryIdError,
                WorkspaceMutationReceiptMismatchError,
                WorkspaceMutationStaleError,
            ):
                latest_snapshot = _conflict_interrupted_entry_mutation(
                    locked,
                    receipt,
                    observed_snapshot,
                )
    raise WorkspaceSnapshotError(
        "Workspace has too many interrupted mutations to reconcile in one request."
    )


def _text_file_payload(
    snapshot: WorkspaceSnapshot,
    entry: WorkspaceIndexEntry,
    file: WorkspaceFile,
    *,
    edit_revision: int | None = None,
    include_content: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "workspace_uuid": snapshot.workspace_uuid,
        "workspace_revision": snapshot.revision,
        "entry_id": entry.entry_id,
        "path": entry.path,
        "version": file.version,
        "size": file.size,
        "mime_type": workspace_mime_type(entry.path),
        "language": workspace_language(entry.path),
        "editable": False,
        "rich_mode_eligible": workspace_rich_mode_eligible(entry.path),
        "max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES,
    }
    if edit_revision is not None:
        payload["edit_revision"] = edit_revision
    try:
        text_file = decode_workspace_text(file)
        if "\0" in text_file.text:
            raise WorkspaceInvalidUTF8Error("Workspace file contains NUL bytes.")
    except WorkspaceInvalidUTF8Error:
        return payload
    payload["editable"] = True
    if include_content:
        payload["content"] = text_file.text
    return payload


def _unsupported_file_response(
    snapshot: WorkspaceSnapshot,
    entry: WorkspaceIndexEntry,
    *,
    file: WorkspaceFile | None = None,
) -> HttpResponse:
    metadata: dict[str, object] = {
        "workspace_uuid": snapshot.workspace_uuid,
        "workspace_revision": snapshot.revision,
        "entry_id": entry.entry_id,
        "path": entry.path,
        "mime_type": workspace_mime_type(entry.path),
        "language": workspace_language(entry.path),
        "editable": False,
        "rich_mode_eligible": False,
        "max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES,
    }
    if file is not None:
        metadata.update({"version": file.version, "size": file.size})
    return _workspace_error(
        code="unsupported_file",
        message="Workspace file is not editable UTF-8 text.",
        status=415,
        revision=snapshot.revision,
        data={"file": metadata},
    )


def _read_text_file(
    node: Workspace,
    snapshot: WorkspaceSnapshot,
    entry: WorkspaceIndexEntry,
) -> tuple[WorkspaceFile, dict[str, object]] | HttpResponse:
    try:
        file = read_workspace_node_file(
            node,
            entry.path,
            max_bytes=PEAR_WORKSPACE_TEXT_MAX_BYTES,
            expected_identity=entry.identity,
        )
    except WorkspaceFileTooLargeError:
        return _workspace_error(
            code="file_too_large",
            message="Workspace file exceeds the editable size limit.",
            status=413,
            revision=snapshot.revision,
            data={"entry": _entry_payload(entry), "max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES},
        )
    except (WorkspaceEntryNotFoundError, WorkspaceEntryChangedError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry changed; refresh before retrying.",
            status=409,
            revision=snapshot.revision,
        )
    except (UnsafeWorkspaceEntryError, InvalidWorkspacePathError):
        return _unsupported_file_response(snapshot, entry)
    except WorkspaceIOError:
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not read this workspace file.",
            status=503,
            revision=snapshot.revision,
        )
    payload = _text_file_payload(snapshot, entry, file)
    if not payload["editable"]:
        return _unsupported_file_response(snapshot, entry, file=file)
    return file, payload


def _check_and_record_file_observation(
    node: Workspace,
    snapshot: WorkspaceSnapshot,
    entry: WorkspaceIndexEntry,
    file: WorkspaceFile,
) -> HttpResponse | None:
    observed_version = get_workspace_file_observation(
        snapshot.workspace_uuid,
        snapshot.revision,
        entry.entry_id,
    )
    if observed_version is not None and observed_version != file.version:
        try:
            current_snapshot = refresh_workspace_snapshot_after_conflict(
                node,
                observed_revision=snapshot.revision,
            )
        except (WorkspaceSnapshotError, WorkspaceIOError):
            return _workspace_error(
                code="workspace_unavailable",
                message="Could not refresh the replaced workspace file.",
                status=503,
                revision=int(node.content_revision),
            )
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry changed; refresh before retrying.",
            status=409,
            revision=current_snapshot.revision,
        )
    record_workspace_file_observation(
        snapshot.workspace_uuid,
        snapshot.revision,
        entry.entry_id,
        file.version,
    )
    return None


@workspace_server_timing("descriptor")
@typed_endpoint
def get_pear_workspace_descriptor_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    return json_success(
        request,
        data={"pear_workspace": _workspace_descriptor_payload(node, snapshot)},
    )


@workspace_server_timing("tree")
@typed_endpoint
def get_pear_workspace_tree_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    directory_id: Annotated[
        str | None,
        ApiParamConfig("directory_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    cursor: Annotated[
        str | None,
        ApiParamConfig("cursor", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    page_size: Annotated[
        Json[int],
        ApiParamConfig("page_size", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = 200,
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    try:
        page = snapshot.index.tree_page(
            directory_id=directory_id,
            cursor=cursor,
            page_size=page_size,
        )
        directory = (
            snapshot.index.root
            if directory_id is None
            else snapshot.index.entry_for_id(directory_id)
        )
    except WorkspaceCursorError:
        return _workspace_error(
            code="stale_cursor",
            message="Workspace tree cursor is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    except WorkspaceEntryIdError:
        return _workspace_error(
            code="stale_entry",
            message="Workspace directory is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    except ValueError as error:
        raise JsonableError(str(error))
    return json_success(
        request,
        data={
            "pear_workspace_tree": {
                "workspace_uuid": snapshot.workspace_uuid,
                "workspace_revision": snapshot.revision,
                "directory": _entry_payload(directory),
                "entries": [_entry_payload(entry) for entry in page.entries],
                "next_cursor": page.next_cursor,
            }
        },
    )


def _ancestor_payloads(
    snapshot: WorkspaceSnapshot,
    entry: WorkspaceIndexEntry,
) -> list[dict[str, object]]:
    components = entry.path.split("/")[:-1]
    ancestors = [snapshot.index.root]
    ancestors.extend(
        snapshot.index.entry_for_path("/".join(components[: offset + 1]))
        for offset in range(len(components))
    )
    return [_entry_payload(ancestor) for ancestor in ancestors]


@workspace_server_timing("search")
@typed_endpoint
def get_pear_workspace_search_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    query: Annotated[
        str,
        ApiParamConfig("q", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    limit: Annotated[
        Json[int],
        ApiParamConfig("limit", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = 100,
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    try:
        matches = snapshot.index.search(query, limit=limit)
    except ValueError as error:
        raise JsonableError(str(error))
    entries: list[dict[str, object]] = []
    for entry in matches:
        payload = _entry_payload(entry)
        payload["ancestors"] = _ancestor_payloads(snapshot, entry)
        entries.append(payload)
    return json_success(
        request,
        data={
            "pear_workspace_search": {
                "workspace_uuid": snapshot.workspace_uuid,
                "workspace_revision": snapshot.revision,
                "query": query,
                "entries": entries,
            }
        },
    )


@workspace_server_timing("file_read")
@typed_endpoint
def get_pear_workspace_resource_file_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    entry_id: Annotated[
        str,
        ApiParamConfig("entry_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    entry = _resolve_file_entry(snapshot, entry_id)
    if isinstance(entry, HttpResponse):
        return entry
    result = _read_text_file(node, snapshot, entry)
    if isinstance(result, HttpResponse):
        return result
    file, payload = result
    observation_error = _check_and_record_file_observation(node, snapshot, entry, file)
    if observation_error is not None:
        return observation_error
    return json_success(request, data={"pear_workspace_file": payload})


def _stale_workspace_response(snapshot: WorkspaceSnapshot) -> HttpResponse:
    return _workspace_error(
        code="stale_workspace",
        message="Workspace changed; refresh before saving.",
        status=409,
        revision=snapshot.revision,
    )


def _workspace_version_conflict_response(
    node: Workspace,
    snapshot: WorkspaceSnapshot,
    entry: WorkspaceIndexEntry,
) -> HttpResponse:
    try:
        conflict_snapshot = refresh_workspace_snapshot_after_conflict(
            node,
            observed_revision=snapshot.revision,
        )
        conflict_entry = conflict_snapshot.index.entry_for_path(entry.path)
        if conflict_entry.kind != "file":
            raise WorkspaceEntryIdError("Workspace entry is no longer a file.")
        if (
            entry.identity is None
            or conflict_entry.identity is None
            or entry.identity.device != conflict_entry.identity.device
            or entry.identity.inode != conflict_entry.identity.inode
        ):
            raise WorkspaceEntryIdError("Workspace entry was replaced.")
        remote_file = read_workspace_node_file(
            node,
            conflict_entry.path,
            max_bytes=PEAR_WORKSPACE_TEXT_MAX_BYTES,
            expected_identity=conflict_entry.identity,
        )
    except (WorkspaceEntryIdError, WorkspaceEntryNotFoundError, WorkspaceEntryChangedError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry changed; refresh before retrying.",
            status=409,
            revision=int(node.content_revision),
        )
    except WorkspaceFileTooLargeError:
        return _workspace_error(
            code="file_too_large",
            message="Workspace file exceeds the editable size limit.",
            status=413,
            revision=int(node.content_revision),
            data={"max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES},
        )
    except (WorkspaceSnapshotError, WorkspaceIOError):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not refresh the conflicting workspace file.",
            status=503,
            revision=int(node.content_revision),
        )
    data: dict[str, object] = {}
    remote_payload = _text_file_payload(conflict_snapshot, conflict_entry, remote_file)
    record_workspace_file_observation(
        conflict_snapshot.workspace_uuid,
        conflict_snapshot.revision,
        conflict_entry.entry_id,
        remote_file.version,
    )
    if remote_payload["editable"]:
        data["remote_file"] = remote_payload
    return _workspace_error(
        code="version_conflict",
        message="Workspace file changed; reload before saving.",
        status=409,
        revision=conflict_snapshot.revision,
        data=data,
    )


@workspace_server_timing("file_write")
@typed_endpoint
def put_pear_workspace_resource_file_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    entry_id: Annotated[
        str,
        ApiParamConfig("entry_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    expected_path: Annotated[
        str,
        ApiParamConfig("expected_path", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    content: Annotated[
        str,
        ApiParamConfig("content", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    base_version: Annotated[
        str,
        ApiParamConfig("base_version", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    workspace_revision: Annotated[
        Json[int],
        ApiParamConfig("workspace_revision", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    edit_revision: Annotated[
        Json[int],
        ApiParamConfig("edit_revision", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    if node.lifecycle_status != Workspace.ACTIVE:
        return _workspace_error(
            code="workspace_read_only",
            message="Workspace is read-only.",
            status=403,
            revision=snapshot.revision,
        )
    if edit_revision < 0:
        raise JsonableError(_("Invalid edit revision."))
    try:
        content_bytes = content.encode("utf-8", errors="strict")
        normalized_path = "/".join(validate_workspace_relative_path(expected_path))
    except (UnicodeEncodeError, InvalidWorkspacePathError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry path is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    if len(content_bytes) > PEAR_WORKSPACE_TEXT_MAX_BYTES:
        return _workspace_error(
            code="file_too_large",
            message="Workspace file exceeds the editable size limit.",
            status=413,
            revision=snapshot.revision,
            data={"max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES},
        )
    if re.fullmatch(r"[0-9a-f]{64}", base_version) is None:
        raise JsonableError(_("Invalid base version."))

    if workspace_revision != snapshot.revision:
        if workspace_revision < 0 or workspace_revision > snapshot.revision:
            return _stale_workspace_response(snapshot)
        receipt = get_workspace_mutation_receipt(
            actor_id=user_profile.id,
            workspace_uuid=snapshot.workspace_uuid,
            requested_revision=workspace_revision,
            entry_id=entry_id,
            expected_path=normalized_path,
            base_version=base_version,
            content=content_bytes,
            edit_revision=edit_revision,
        )
        if receipt is None:
            return _stale_workspace_response(snapshot)
        try:
            if receipt.state == "complete":
                if receipt.result_revision != snapshot.revision or receipt.result_entry_id is None:
                    return _stale_workspace_response(snapshot)
                current_entry = snapshot.index.entry_for_id(receipt.result_entry_id)
            elif receipt.state == "pending" and snapshot.revision == workspace_revision + 1:
                current_entry = snapshot.index.entry_for_path(normalized_path)
            else:
                return _stale_workspace_response(snapshot)
            if current_entry.kind != "file" or current_entry.path != normalized_path:
                return _stale_workspace_response(snapshot)
            if current_entry.identity != receipt.result_identity:
                return _stale_workspace_response(snapshot)
            current_file = read_workspace_node_file(
                node,
                current_entry.path,
                max_bytes=PEAR_WORKSPACE_TEXT_MAX_BYTES,
                expected_identity=receipt.result_identity,
            )
        except (
            WorkspaceEntryIdError,
            WorkspaceIOError,
            WorkspaceFileTooLargeError,
        ):
            return _stale_workspace_response(snapshot)
        if current_file.data != content_bytes or current_file.version != receipt.result_version:
            return _stale_workspace_response(snapshot)
        payload = _text_file_payload(
            snapshot,
            current_entry,
            current_file,
            edit_revision=edit_revision,
        )
        if not payload["editable"]:
            return _unsupported_file_response(snapshot, current_entry, file=current_file)
        observation_error = _check_and_record_file_observation(
            node,
            snapshot,
            current_entry,
            current_file,
        )
        if observation_error is not None:
            return observation_error
        if receipt.state == "pending":
            record_completed_workspace_mutation(
                actor_id=user_profile.id,
                workspace_uuid=snapshot.workspace_uuid,
                requested_revision=workspace_revision,
                entry_id=entry_id,
                expected_path=normalized_path,
                base_version=base_version,
                content=content_bytes,
                edit_revision=edit_revision,
                result_version=current_file.version,
                result_identity=receipt.result_identity,
                result_revision=snapshot.revision,
                result_entry_id=current_entry.entry_id,
            )
        return json_success(request, data={"pear_workspace_file": payload})

    entry = _resolve_file_entry(snapshot, entry_id)
    if isinstance(entry, HttpResponse):
        return entry
    if entry.path != normalized_path:
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry path is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )

    pending_repair_receipt = get_workspace_mutation_receipt(
        actor_id=user_profile.id,
        workspace_uuid=snapshot.workspace_uuid,
        requested_revision=workspace_revision,
        entry_id=entry_id,
        expected_path=normalized_path,
        base_version=base_version,
        content=content_bytes,
        edit_revision=edit_revision,
    )
    write_identity = entry.identity
    if pending_repair_receipt is not None and pending_repair_receipt.state == "pending":
        write_identity = pending_repair_receipt.result_identity

    def before_finish(result: WorkspaceWriteResult) -> None:
        if result.file.identity is None:
            raise WorkspaceEntryChangedError("Saved workspace file identity is unavailable.")
        if result.changed:
            record_pending_workspace_mutation(
                actor_id=user_profile.id,
                workspace_uuid=snapshot.workspace_uuid,
                requested_revision=workspace_revision,
                entry_id=entry_id,
                expected_path=normalized_path,
                base_version=base_version,
                content=content_bytes,
                edit_revision=edit_revision,
                result_version=result.file.version,
                result_identity=result.file.identity,
            )
            return
        if result.file.version == base_version:
            return
        pending_receipt = get_workspace_mutation_receipt(
            actor_id=user_profile.id,
            workspace_uuid=snapshot.workspace_uuid,
            requested_revision=workspace_revision,
            entry_id=entry_id,
            expected_path=normalized_path,
            base_version=base_version,
            content=content_bytes,
            edit_revision=edit_revision,
        )
        if (
            pending_receipt is None
            or pending_receipt.state != "pending"
            or pending_receipt.result_version != result.file.version
            or pending_receipt.result_identity != result.file.identity
        ):
            raise WorkspaceEntryChangedError(
                "Workspace entry changed before the save revision was recorded."
            )

    try:
        result, saved_snapshot = write_workspace_node_file_at_revision(
            node,
            entry.path,
            content_bytes,
            expected_version=base_version,
            expected_identity=write_identity,
            requested_revision=workspace_revision,
            before_finish=before_finish,
        )
        saved_entry = saved_snapshot.index.entry_for_path(entry.path)
    except WorkspaceMutationReadOnlyError as error:
        return _workspace_error(
            code="workspace_read_only",
            message="Workspace is read-only.",
            status=403,
            revision=error.revision,
        )
    except WorkspaceMutationStaleError:
        node.refresh_from_db()
        latest_snapshot = _snapshot_or_error(node)
        if isinstance(latest_snapshot, HttpResponse):
            return latest_snapshot
        return _stale_workspace_response(latest_snapshot)
    except (WorkspaceVersionConflictError, WorkspaceEntryChangedError):
        return _workspace_version_conflict_response(node, snapshot, entry)
    except WorkspaceFileTooLargeError:
        return _workspace_error(
            code="file_too_large",
            message="Workspace file exceeds the editable size limit.",
            status=413,
            revision=snapshot.revision,
            data={"max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES},
        )
    except WorkspaceEntryNotFoundError:
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry changed; refresh before retrying.",
            status=409,
            revision=snapshot.revision,
        )
    except UnsafeWorkspaceEntryError:
        return _unsupported_file_response(snapshot, entry)
    except (WorkspaceIOError, WorkspaceSnapshotError, WorkspaceEntryIdError, ValueError):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not save and refresh this workspace file.",
            status=503,
            revision=int(node.content_revision),
        )
    assert result.file.identity is not None
    payload = _text_file_payload(
        saved_snapshot,
        saved_entry,
        result.file,
        edit_revision=edit_revision,
    )
    record_workspace_file_observation(
        saved_snapshot.workspace_uuid,
        saved_snapshot.revision,
        saved_entry.entry_id,
        result.file.version,
    )
    record_completed_workspace_mutation(
        actor_id=user_profile.id,
        workspace_uuid=snapshot.workspace_uuid,
        requested_revision=workspace_revision,
        entry_id=entry_id,
        expected_path=normalized_path,
        base_version=base_version,
        content=content_bytes,
        edit_revision=edit_revision,
        result_version=result.file.version,
        result_identity=result.file.identity,
        result_revision=saved_snapshot.revision,
        result_entry_id=saved_entry.entry_id,
    )
    logger.info(
        "Pear workspace file saved workspace=%s revision=%s entry=%s actor=%s changed=%s",
        saved_snapshot.workspace_uuid,
        saved_snapshot.revision,
        saved_entry.entry_id,
        user_profile.id,
        result.changed,
    )
    return json_success(request, data={"pear_workspace_file": payload})


@workspace_server_timing("entry_create")
@typed_endpoint
def post_pear_workspace_entry_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    destination_path: Annotated[
        str,
        ApiParamConfig("destination_path", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    kind: Annotated[
        Literal["file", "directory"],
        ApiParamConfig("kind", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    workspace_revision: Annotated[
        Json[int],
        ApiParamConfig("workspace_revision", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    content: Annotated[
        str,
        ApiParamConfig("content", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = "",
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    if kind == "directory" and content:
        raise JsonableError(_("A directory cannot have file content."))
    try:
        content_bytes = content.encode("utf-8", errors="strict")
        normalized_destination = "/".join(validate_workspace_relative_path(destination_path))
    except UnicodeEncodeError:
        raise JsonableError(_("Workspace file content must be valid UTF-8."))
    except InvalidWorkspacePathError:
        normalized_destination = ""
    lookup_payload = workspace_entry_mutation_lookup_payload(
        actor_id=user_profile.id,
        workspace_uuid=str(node.uuid),
        requested_revision=workspace_revision,
        operation="create",
        source_entry_id="",
        source_path="",
        destination_path=normalized_destination,
        options={
            "kind": kind,
            "content_sha256": hashlib.sha256(content_bytes).hexdigest(),
        },
    )
    receipt = get_workspace_entry_mutation_receipt(lookup_payload=lookup_payload)
    replay = _terminal_entry_mutation_response(request, receipt)
    if replay is not None:
        return replay

    snapshot = _snapshot_or_error(node, force=receipt is not None)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    if receipt is not None:
        receipt.refresh_from_db()
        replay = _terminal_entry_mutation_response(request, receipt)
        if replay is not None:
            return replay
    if receipt is None and workspace_revision != snapshot.revision:
        return _stale_workspace_response(snapshot)
    destination = _validate_destination_against_snapshot(
        node,
        snapshot,
        normalized_destination or destination_path,
    )
    if isinstance(destination, HttpResponse):
        if receipt is None:
            return destination
        destination = normalized_destination
    if len(content_bytes) > PEAR_WORKSPACE_TEXT_MAX_BYTES:
        return _workspace_error(
            code="file_too_large",
            message="Workspace file exceeds the editable size limit.",
            status=413,
            revision=snapshot.revision,
            data={"max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES},
        )
    if receipt is None:
        receipt, _created = prepare_workspace_entry_mutation_receipt(
            node=node,
            actor_id=user_profile.id,
            lookup_payload=lookup_payload,
            source_kind=None,
            source_identity=None,
        )

    try:
        with lock_workspace_entry_mutation_receipt(node, receipt) as (
            locked,
            locked_receipt,
        ):
            replay = _terminal_entry_mutation_response(request, locked_receipt)
            if replay is not None:
                return replay
            observed_snapshot = get_workspace_snapshot(locked, force=True)
            existing_entry = _live_entry_for_path(observed_snapshot, destination)
            if locked_receipt.state == WorkspaceEntryMutationReceipt.PREPARED:
                if existing_entry is not None or int(locked.content_revision) != workspace_revision:
                    raise WorkspaceMutationStaleError(int(locked.content_revision))
                locked_destination = _validate_destination_against_snapshot(
                    locked,
                    observed_snapshot,
                    destination,
                )
                if isinstance(locked_destination, HttpResponse):
                    return locked_destination
                _stage_prepared_create(locked, locked_receipt, content_bytes)
            elif locked_receipt.state not in {
                WorkspaceEntryMutationReceipt.STAGED,
                WorkspaceEntryMutationReceipt.APPLIED,
            }:
                raise WorkspaceMutationReceiptMismatchError(
                    "Workspace create receipt has an invalid state."
                )

        with lock_workspace_entry_mutation_receipt(node, receipt) as (
            locked,
            locked_receipt,
        ):
            replay = _terminal_entry_mutation_response(request, locked_receipt)
            if replay is not None:
                return replay
            observed_snapshot = get_workspace_snapshot(locked, force=True)
            if locked_receipt.state == WorkspaceEntryMutationReceipt.STAGED:
                _publish_staged_create(locked, locked_receipt, observed_snapshot)
            elif locked_receipt.state != WorkspaceEntryMutationReceipt.APPLIED:
                raise WorkspaceMutationReceiptMismatchError(
                    "Workspace create receipt has no staged application."
                )

        with lock_workspace_entry_mutation_receipt(node, receipt) as (
            locked,
            locked_receipt,
        ):
            replay = _terminal_entry_mutation_response(request, locked_receipt)
            if replay is not None:
                return replay
            observed_snapshot = get_workspace_snapshot(locked, force=True)
            if locked_receipt.state != WorkspaceEntryMutationReceipt.APPLIED:
                raise WorkspaceMutationReceiptMismatchError(
                    "Workspace create receipt has no durable application."
                )
            saved_snapshot = _complete_applied_entry_mutation(
                locked,
                locked_receipt,
                observed_snapshot,
            )
            saved_entry = saved_snapshot.index.entry_for_path(destination)
            mutation_payload = dict(locked_receipt.result_payload)
    except (WorkspaceMutationReadOnlyError, WorkspaceMutationStaleError) as error:
        return _locked_mutation_error_response(node, error)
    except WorkspaceEntryExistsError:
        return _workspace_error(
            code="already_exists",
            message="Workspace destination already exists.",
            status=409,
            revision=int(node.content_revision),
        )
    except WorkspaceFileTooLargeError:
        return _workspace_error(
            code="file_too_large",
            message="Workspace file exceeds the editable size limit.",
            status=413,
            revision=int(node.content_revision),
            data={"max_bytes": PEAR_WORKSPACE_TEXT_MAX_BYTES},
        )
    except (WorkspaceEntryNotFoundError, WorkspaceEntryChangedError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace destination changed; refresh before retrying.",
            status=409,
            revision=int(node.content_revision),
        )
    except (
        WorkspaceMutationReceiptMismatchError,
        WorkspaceSnapshotError,
        WorkspaceIOError,
        WorkspaceEntryIdError,
    ):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not create and index the workspace entry.",
            status=503,
            revision=int(node.content_revision),
        )
    logger.info(
        "Pear workspace entry created workspace=%s revision=%s entry=%s actor=%s kind=%s",
        saved_snapshot.workspace_uuid,
        saved_snapshot.revision,
        saved_entry.entry_id,
        user_profile.id,
        kind,
    )
    return json_success(
        request,
        data={"pear_workspace_mutation": mutation_payload},
    )


@workspace_server_timing("entry_rename")
@typed_endpoint
def patch_pear_workspace_entry_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    entry_id: Annotated[
        str,
        ApiParamConfig("entry_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    expected_path: Annotated[
        str,
        ApiParamConfig("expected_path", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    destination_path: Annotated[
        str,
        ApiParamConfig("destination_path", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    workspace_revision: Annotated[
        Json[int],
        ApiParamConfig("workspace_revision", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    try:
        normalized_expected_path = "/".join(validate_workspace_relative_path(expected_path))
        normalized_destination_path = "/".join(validate_workspace_relative_path(destination_path))
    except InvalidWorkspacePathError:
        normalized_expected_path = ""
        normalized_destination_path = ""
    lookup_payload = workspace_entry_mutation_lookup_payload(
        actor_id=user_profile.id,
        workspace_uuid=str(node.uuid),
        requested_revision=workspace_revision,
        operation="rename",
        source_entry_id=entry_id,
        source_path=normalized_expected_path,
        destination_path=normalized_destination_path,
        options={},
    )
    receipt = get_workspace_entry_mutation_receipt(lookup_payload=lookup_payload)
    replay = _terminal_entry_mutation_response(request, receipt)
    if replay is not None:
        return replay

    snapshot = _snapshot_or_error(node, force=receipt is not None)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    if receipt is not None:
        receipt.refresh_from_db()
        replay = _terminal_entry_mutation_response(request, receipt)
        if replay is not None:
            return replay
    if receipt is None and workspace_revision != snapshot.revision:
        return _stale_workspace_response(snapshot)
    if receipt is None:
        entry = _resolve_mutable_entry(snapshot, entry_id)
        if isinstance(entry, HttpResponse):
            return entry
    else:
        entry = None
    if entry is not None and entry.path != normalized_expected_path:
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry path is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    destination = _validate_destination_against_snapshot(
        node,
        snapshot,
        normalized_destination_path or destination_path,
    )
    if isinstance(destination, HttpResponse) and receipt is None:
        return destination
    if receipt is None:
        assert entry is not None
        entry_kind: Literal["file", "directory"] = "file" if entry.kind == "file" else "directory"
        receipt, _created = prepare_workspace_entry_mutation_receipt(
            node=node,
            actor_id=user_profile.id,
            lookup_payload=lookup_payload,
            source_kind=entry_kind,
            source_identity=entry.identity,
        )
    else:
        source_kind = receipt.request_payload.get("source_kind")
        if source_kind not in {"file", "directory"}:
            raise WorkspaceMutationReceiptMismatchError(
                "Workspace rename receipt has invalid source kind."
            )
        entry_kind = source_kind
    assert isinstance(destination, str) or receipt is not None
    destination = normalized_destination_path
    try:
        with lock_workspace_entry_mutation_receipt(node, receipt) as (
            locked,
            locked_receipt,
        ):
            replay = _terminal_entry_mutation_response(request, locked_receipt)
            if replay is not None:
                return replay
            source_identity = _receipt_source_identity(locked_receipt)
            if source_identity is None:
                raise WorkspaceMutationReceiptMismatchError(
                    "Workspace rename receipt has no source identity."
                )
            observed_snapshot = get_workspace_snapshot(locked, force=True)
            observed_source = _live_entry_for_path(observed_snapshot, normalized_expected_path)
            observed_destination = _live_entry_for_path(observed_snapshot, destination)
            applied = (
                observed_source is None
                and observed_destination is not None
                and observed_destination.kind == entry_kind
                and observed_destination.identity is not None
                and _same_workspace_inode(observed_destination.identity, source_identity)
            )
            application_identity: WorkspaceEntryIdentity | None = None
            if locked_receipt.state == WorkspaceEntryMutationReceipt.APPLIED:
                application_identity = _receipt_application_identity(locked_receipt)
                if (
                    application_identity is None
                    or not _same_workspace_inode(application_identity, source_identity)
                    or observed_destination is None
                    or observed_destination.identity != application_identity
                    or not applied
                ):
                    raise WorkspaceMutationStaleError(int(locked.content_revision))
            elif locked_receipt.state == WorkspaceEntryMutationReceipt.PREPARED and not applied:
                if (
                    int(locked.content_revision) != workspace_revision
                    or observed_source is None
                    or observed_source.kind != entry_kind
                    or observed_source.identity != source_identity
                    or observed_destination is not None
                ):
                    raise WorkspaceMutationStaleError(int(locked.content_revision))
                locked_destination = _validate_destination_against_snapshot(
                    locked,
                    observed_snapshot,
                    destination,
                )
                if isinstance(locked_destination, HttpResponse):
                    return locked_destination
                result = rename_workspace_node_entry(
                    locked,
                    normalized_expected_path,
                    locked_destination,
                    expected_kind=entry_kind,
                    expected_identity=source_identity,
                )
                application_identity = result.identity
            elif locked_receipt.state != WorkspaceEntryMutationReceipt.PREPARED:
                raise WorkspaceMutationReceiptMismatchError(
                    "Workspace rename receipt has an invalid state."
                )
            if locked_receipt.state == WorkspaceEntryMutationReceipt.PREPARED:
                if applied:
                    assert observed_destination is not None
                    application_identity = observed_destination.identity
                if application_identity is None or not _same_workspace_inode(
                    application_identity, source_identity
                ):
                    raise WorkspaceEntryChangedError(
                        "Renamed workspace entry has no safe resulting identity."
                    )
                record_workspace_entry_mutation_application(
                    locked_receipt,
                    {
                        "identity": _identity_payload(application_identity),
                        "kind": entry_kind,
                    },
                )

        with lock_workspace_entry_mutation_receipt(node, receipt) as (
            locked,
            locked_receipt,
        ):
            replay = _terminal_entry_mutation_response(request, locked_receipt)
            if replay is not None:
                return replay
            source_identity = _receipt_source_identity(locked_receipt)
            application_identity = _receipt_application_identity(locked_receipt)
            if (
                locked_receipt.state != WorkspaceEntryMutationReceipt.APPLIED
                or source_identity is None
                or application_identity is None
                or not _same_workspace_inode(application_identity, source_identity)
            ):
                raise WorkspaceMutationReceiptMismatchError(
                    "Workspace rename receipt has no durable application."
                )
            observed_snapshot = get_workspace_snapshot(locked, force=True)
            observed_source = _live_entry_for_path(observed_snapshot, normalized_expected_path)
            observed_destination = _live_entry_for_path(observed_snapshot, destination)
            if (
                observed_source is not None
                or observed_destination is None
                or observed_destination.kind != entry_kind
                or observed_destination.identity != application_identity
            ):
                raise WorkspaceMutationStaleError(int(locked.content_revision))
            saved_snapshot = _finish_prepared_entry_mutation(
                locked,
                locked_receipt,
                observed_snapshot,
            )
            saved_entry = saved_snapshot.index.entry_for_path(destination)
            mutation_payload = _entry_mutation_payload(
                saved_snapshot,
                operation="rename",
                previous_entry_id=entry_id,
                entry=saved_entry,
                path=saved_entry.path,
            )
            complete_workspace_entry_mutation_receipt(locked_receipt, mutation_payload)
    except (WorkspaceMutationReadOnlyError, WorkspaceMutationStaleError) as error:
        return _locked_mutation_error_response(node, error)
    except WorkspaceEntryExistsError:
        return _workspace_error(
            code="already_exists",
            message="Workspace destination already exists.",
            status=409,
            revision=int(node.content_revision),
        )
    except (InvalidWorkspacePathError, WorkspaceEntryNotFoundError, WorkspaceEntryChangedError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry changed; refresh before retrying.",
            status=409,
            revision=int(node.content_revision),
        )
    except WorkspaceOperationUnsupportedError:
        return _workspace_error(
            code="unsupported_operation",
            message="No-overwrite rename is unavailable for this workspace.",
            status=501,
            revision=int(node.content_revision),
        )
    except (
        WorkspaceMutationReceiptMismatchError,
        WorkspaceSnapshotError,
        WorkspaceIOError,
        WorkspaceEntryIdError,
    ):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not rename and index the workspace entry.",
            status=503,
            revision=int(node.content_revision),
        )
    logger.info(
        "Pear workspace entry renamed workspace=%s revision=%s old_entry=%s new_entry=%s actor=%s",
        saved_snapshot.workspace_uuid,
        saved_snapshot.revision,
        entry_id,
        saved_entry.entry_id,
        user_profile.id,
    )
    return json_success(
        request,
        data={"pear_workspace_mutation": mutation_payload},
    )


@workspace_server_timing("entry_delete")
@typed_endpoint
def delete_pear_workspace_entry_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    entry_id: Annotated[
        str,
        ApiParamConfig("entry_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    expected_path: Annotated[
        str,
        ApiParamConfig("expected_path", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    workspace_revision: Annotated[
        Json[int],
        ApiParamConfig("workspace_revision", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    recursive: Annotated[
        Json[bool],
        ApiParamConfig("recursive", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = False,
    confirm_irreversible: Annotated[
        Json[bool],
        ApiParamConfig("confirm_irreversible", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = False,
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    try:
        normalized_expected_path = "/".join(validate_workspace_relative_path(expected_path))
    except InvalidWorkspacePathError:
        normalized_expected_path = ""
    lookup_payload = workspace_entry_mutation_lookup_payload(
        actor_id=user_profile.id,
        workspace_uuid=str(node.uuid),
        requested_revision=workspace_revision,
        operation="delete",
        source_entry_id=entry_id,
        source_path=normalized_expected_path,
        destination_path="",
        options={
            "recursive": bool(recursive),
            "confirm_irreversible": bool(confirm_irreversible),
        },
    )
    receipt = get_workspace_entry_mutation_receipt(lookup_payload=lookup_payload)
    replay = _terminal_entry_mutation_response(request, receipt)
    if replay is not None:
        return replay

    snapshot = _snapshot_or_error(node, force=receipt is not None)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    if receipt is not None:
        receipt.refresh_from_db()
        replay = _terminal_entry_mutation_response(request, receipt)
        if replay is not None:
            return replay
    if receipt is None and workspace_revision != snapshot.revision:
        return _stale_workspace_response(snapshot)
    if receipt is None:
        entry = _resolve_mutable_entry(snapshot, entry_id)
        if isinstance(entry, HttpResponse):
            return entry
        if entry.path != normalized_expected_path:
            return _workspace_error(
                code="stale_entry",
                message="Workspace entry path is stale or invalid.",
                status=409,
                revision=snapshot.revision,
            )
        if entry.kind == "file" and entry.status == "untracked" and not confirm_irreversible:
            return _workspace_error(
                code="confirmation_required",
                message="Deleting an untracked file is irreversible and requires confirmation.",
                status=409,
                revision=snapshot.revision,
                data={"confirmation": "untracked_file"},
            )
        if recursive and not confirm_irreversible:
            return _workspace_error(
                code="confirmation_required",
                message="Recursive deletion is irreversible and requires confirmation.",
                status=409,
                revision=snapshot.revision,
                data={"confirmation": "recursive_delete"},
            )
        entry_kind: Literal["file", "directory"] = "file" if entry.kind == "file" else "directory"
        removed_entries_hint = max(
            1,
            sum(
                path.status != "deleted"
                and (
                    path.path == normalized_expected_path
                    or path.path.startswith(f"{normalized_expected_path}/")
                )
                for path in snapshot.paths
            ),
        )
        receipt, _created = prepare_workspace_entry_mutation_receipt(
            node=node,
            actor_id=user_profile.id,
            lookup_payload=lookup_payload,
            source_kind=entry_kind,
            source_identity=entry.identity,
            recovery={"removed_entries": removed_entries_hint},
        )
    else:
        source_kind = receipt.request_payload.get("source_kind")
        if source_kind not in {"file", "directory"}:
            raise WorkspaceMutationReceiptMismatchError(
                "Workspace delete receipt has invalid source kind."
            )
        entry_kind = "file" if source_kind == "file" else "directory"
        recovery_payload = receipt.request_payload.get("recovery")
        if not isinstance(recovery_payload, dict):
            raise WorkspaceMutationReceiptMismatchError(
                "Workspace delete receipt has invalid recovery metadata."
            )
        raw_removed_entries_hint = recovery_payload.get("removed_entries")
        if (
            isinstance(raw_removed_entries_hint, bool)
            or not isinstance(raw_removed_entries_hint, int)
            or raw_removed_entries_hint < 1
        ):
            raise WorkspaceMutationReceiptMismatchError(
                "Workspace delete receipt has invalid removed-entry metadata."
            )
        removed_entries_hint = raw_removed_entries_hint
    try:
        with lock_workspace_entry_mutation_receipt(node, receipt) as (
            locked,
            locked_receipt,
        ):
            replay = _terminal_entry_mutation_response(request, locked_receipt)
            if replay is not None:
                return replay
            source_identity = _receipt_source_identity(locked_receipt)
            if source_identity is None:
                raise WorkspaceMutationReceiptMismatchError(
                    "Workspace delete receipt has no source identity."
                )
            observed_snapshot = get_workspace_snapshot(locked, force=True)
            observed_source = _live_entry_for_path(observed_snapshot, normalized_expected_path)
            if locked_receipt.state == WorkspaceEntryMutationReceipt.APPLIED:
                if (
                    _receipt_application_identity(locked_receipt) != source_identity
                    or observed_source is not None
                ):
                    raise WorkspaceMutationStaleError(int(locked.content_revision))
            elif locked_receipt.state == WorkspaceEntryMutationReceipt.PREPARED:
                removed_entries = removed_entries_hint
                if observed_source is not None:
                    if (
                        int(locked.content_revision) != workspace_revision
                        or observed_source.kind != entry_kind
                        or observed_source.identity != source_identity
                    ):
                        raise WorkspaceMutationStaleError(int(locked.content_revision))
                    result = delete_workspace_node_entry(
                        locked,
                        normalized_expected_path,
                        expected_kind=entry_kind,
                        expected_identity=source_identity,
                        recursive=bool(recursive),
                    )
                    removed_entries = result.removed_entries
                record_workspace_entry_mutation_application(
                    locked_receipt,
                    {
                        "identity": _identity_payload(source_identity),
                        "kind": entry_kind,
                        "removed_entries": removed_entries,
                    },
                )
            else:
                raise WorkspaceMutationReceiptMismatchError(
                    "Workspace delete receipt has an invalid state."
                )

        with lock_workspace_entry_mutation_receipt(node, receipt) as (
            locked,
            locked_receipt,
        ):
            replay = _terminal_entry_mutation_response(request, locked_receipt)
            if replay is not None:
                return replay
            source_identity = _receipt_source_identity(locked_receipt)
            application_identity = _receipt_application_identity(locked_receipt)
            removed_entries = locked_receipt.application_payload.get("removed_entries")
            if (
                locked_receipt.state != WorkspaceEntryMutationReceipt.APPLIED
                or source_identity is None
                or application_identity != source_identity
                or isinstance(removed_entries, bool)
                or not isinstance(removed_entries, int)
                or removed_entries < 1
            ):
                raise WorkspaceMutationReceiptMismatchError(
                    "Workspace delete receipt has no durable application."
                )
            observed_snapshot = get_workspace_snapshot(locked, force=True)
            if _live_entry_for_path(observed_snapshot, normalized_expected_path) is not None:
                raise WorkspaceMutationStaleError(int(locked.content_revision))
            saved_snapshot = _finish_prepared_entry_mutation(
                locked,
                locked_receipt,
                observed_snapshot,
            )
            remaining_entry = _live_entry_for_path(saved_snapshot, normalized_expected_path)
            if remaining_entry is not None:
                raise WorkspaceEntryChangedError(
                    "Deleted workspace path remained live in the refreshed index."
                )
            mutation_payload = _entry_mutation_payload(
                saved_snapshot,
                operation="delete",
                previous_entry_id=entry_id,
                entry=None,
                path=normalized_expected_path,
                removed_entries=removed_entries,
            )
            complete_workspace_entry_mutation_receipt(locked_receipt, mutation_payload)
    except (WorkspaceMutationReadOnlyError, WorkspaceMutationStaleError) as error:
        return _locked_mutation_error_response(node, error)
    except WorkspaceDirectoryNotEmptyError:
        return _workspace_error(
            code="directory_not_empty",
            message="Workspace directory is not empty; recursive deletion must be explicit.",
            status=409,
            revision=int(node.content_revision),
            data={"requires_recursive": True, "requires_confirmation": True},
        )
    except (WorkspaceEntryNotFoundError, WorkspaceEntryChangedError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry changed; refresh before retrying.",
            status=409,
            revision=int(node.content_revision),
        )
    except (WorkspaceMutationReceiptMismatchError, WorkspaceSnapshotError, WorkspaceIOError):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not delete and refresh the workspace entry.",
            status=503,
            revision=int(node.content_revision),
        )
    logger.info(
        "Pear workspace entry deleted workspace=%s revision=%s old_entry=%s actor=%s kind=%s recursive=%s removed=%s",
        saved_snapshot.workspace_uuid,
        saved_snapshot.revision,
        entry_id,
        user_profile.id,
        entry_kind,
        bool(recursive),
        removed_entries,
    )
    return json_success(
        request,
        data={"pear_workspace_mutation": mutation_payload},
    )


@workspace_server_timing("history")
@typed_endpoint
def get_pear_workspace_history_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    entry_id: Annotated[
        str,
        ApiParamConfig("entry_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    cursor: Annotated[
        str | None,
        ApiParamConfig("cursor", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    limit: Annotated[
        Json[int],
        ApiParamConfig("limit", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = 50,
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    entry = _resolve_file_entry(snapshot, entry_id)
    if isinstance(entry, HttpResponse):
        return entry
    try:
        page = get_workspace_history(
            node,
            snapshot,
            entry_id=entry.entry_id,
            path=entry.path,
            cursor=cursor,
            limit=limit,
        )
    except WorkspaceHistoryCursorError:
        return _workspace_error(
            code="stale_cursor",
            message="Workspace history cursor is stale or invalid.",
            status=409,
            revision=snapshot.revision,
        )
    except ValueError as error:
        raise JsonableError(str(error))
    except WorkspaceSnapshotError:
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not load workspace history.",
            status=503,
            revision=snapshot.revision,
        )
    return json_success(
        request,
        data={
            "pear_workspace_history": {
                "workspace_uuid": snapshot.workspace_uuid,
                "workspace_revision": snapshot.revision,
                "entry": _entry_payload(entry),
                "commits": [
                    {
                        "commit": item.commit,
                        "authored_at": item.authored_at,
                        "author_name": item.author_name,
                        "subject": item.subject,
                    }
                    for item in page.entries
                ],
                "next_cursor": page.next_cursor,
            }
        },
    )


@workspace_server_timing("raw")
@typed_endpoint
def get_pear_workspace_raw_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
    entry_id: Annotated[
        str,
        ApiParamConfig("entry_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    download: Annotated[
        Json[bool] | None,
        ApiParamConfig("download", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = False,
) -> HttpResponseBase:
    node = _access_workspace_node(user_profile, workspace_uuid)
    snapshot = _snapshot_or_error(node)
    if isinstance(snapshot, HttpResponse):
        return snapshot
    entry = _resolve_file_entry(snapshot, entry_id)
    if isinstance(entry, HttpResponse):
        return entry
    try:
        stream = open_workspace_node_file_stream(
            node,
            entry.path,
            expected_identity=entry.identity,
        )
    except (WorkspaceEntryNotFoundError, WorkspaceEntryChangedError):
        return _workspace_error(
            code="stale_entry",
            message="Workspace entry changed; refresh before retrying.",
            status=409,
            revision=snapshot.revision,
        )
    except (UnsafeWorkspaceEntryError, InvalidWorkspacePathError):
        return _unsupported_file_response(snapshot, entry)
    except WorkspaceFileTooLargeError:
        return _workspace_error(
            code="file_too_large",
            message="Workspace file exceeds the raw download size limit.",
            status=413,
            revision=snapshot.revision,
            data={"max_bytes": PEAR_WORKSPACE_RAW_MAX_BYTES},
        )
    except WorkspaceIOError:
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not read this workspace file.",
            status=503,
            revision=snapshot.revision,
        )
    response = StreamingHttpResponse(
        streaming_content=stream,
        content_type=workspace_mime_type(entry.path),
    )
    response["Content-Disposition"] = content_disposition_header(bool(download), entry.name) or (
        "attachment" if download else "inline"
    )
    response["Content-Length"] = str(stream.metadata.size)
    response["ETag"] = f'"{stream.metadata.cache_token}"'
    response["Cache-Control"] = "private, max-age=0, must-revalidate"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Pear-Workspace-Revision"] = str(snapshot.revision)
    return response


@workspace_server_timing("refresh")
@typed_endpoint
def post_pear_workspace_refresh_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    workspace_uuid: Annotated[UUID, ApiParamConfig("workspace_uuid", path_only=True)],
) -> HttpResponse:
    node = _access_workspace_node(user_profile, workspace_uuid)
    previous_revision = int(node.content_revision)
    try:
        recovered_snapshot = _reconcile_interrupted_entry_mutations(node)
        if recovered_snapshot is not None:
            node.refresh_from_db()
        snapshot, changed = refresh_workspace_snapshot(node)
        changed = changed or snapshot.revision != previous_revision
    except (
        WorkspaceMutationReceiptMismatchError,
        WorkspaceSnapshotError,
        WorkspaceIOError,
    ):
        return _workspace_error(
            code="workspace_unavailable",
            message="Could not refresh this workspace.",
            status=503,
            revision=int(node.content_revision),
        )
    return json_success(
        request,
        data={
            "pear_workspace_refresh": {
                "changed": changed,
                "workspace": _workspace_descriptor_payload(node, snapshot),
            }
        },
    )
