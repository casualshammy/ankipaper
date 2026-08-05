"""AnkiWeb sync client: incremental sync, full download, auth handling."""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Never

from anki import sync_pb2
from anki.collection import Collection
from anki.errors import BackendError

from app.sync.auth import is_auth_error, make_auth

logger = logging.getLogger(__name__)


class FullSyncKind(enum.Enum):
    """Reason the sync collection needs a full upload or download.

    Mirrors the three non-trivial values of
    ``sync_pb2.SyncCollectionResponse.ChangesRequired`` (FULL_SYNC,
    FULL_DOWNLOAD, FULL_UPLOAD). In all cases the caller must invoke
    ``full_upload`` or ``full_download`` to actually transfer data.
    """

    CONFLICT = "conflict"
    """Both sides have data with an incompatible schema (FULL_SYNC)."""

    DOWNLOAD = "download"
    """Local is empty; only download from AnkiWeb is possible (FULL_DOWNLOAD)."""

    UPLOAD = "upload"
    """Remote is empty; only upload to AnkiWeb is possible (FULL_UPLOAD)."""


# Maps ``SyncCollectionResponse.ChangesRequired`` to :class:`FullSyncKind`;
# ``NO_CHANGES`` / ``NORMAL_SYNC`` are absent and map to ``None``.
_FULL_SYNC_KIND_BY_VALUE: dict[int, FullSyncKind] = {
    sync_pb2.SyncCollectionResponse.FULL_SYNC: FullSyncKind.CONFLICT,
    sync_pb2.SyncCollectionResponse.FULL_DOWNLOAD: FullSyncKind.DOWNLOAD,
    sync_pb2.SyncCollectionResponse.FULL_UPLOAD: FullSyncKind.UPLOAD,
}


class SyncError(RuntimeError):
    """Failure to synchronise the collection with AnkiWeb."""


class AuthExpiredError(SyncError):
    """The stored hostKey was rejected by the server (password change, ban)."""


@dataclass(slots=True)
class SyncResult:
    """Result of a sync operation."""

    required: bool
    """True if the sync actually ran (there were changes on the server)."""

    new_endpoint: str | None
    """New endpoint if the server requested a migration."""

    error: str | None = None
    """Human-readable error message if the sync failed."""

    auth_expired: bool = False
    """True if the hostKey was rejected and the user needs to log in again."""

    full_sync_kind: FullSyncKind | None = None
    """If non-None, ``sync_collection`` returned a full-sync variant and
    the caller must either ask the user (``SyncPost`` flow) or apply the
    recommended direction automatically."""

    server_message: str = ""
    """AnkiWeb server message from ``SyncCollectionResponse.server_message``."""


def _raise_sync_error(exc: BackendError, action: str) -> Never:
    """Raises :class:`AuthExpiredError` or :class:`SyncError` for ``exc``.

    ``action`` is a short description of the failed step, used in the
    resulting :class:`SyncError` message (e.g. ``"Failed to fetch sync status"``).
    """

    if is_auth_error(exc):
        raise AuthExpiredError(
            "AnkiWeb rejected the stored hostKey, please sign in again"
        ) from exc
    raise SyncError(f"{action}: {exc}") from exc


def perform_sync(col: Collection, host_key: str, endpoint: str | None = None) -> SyncResult:
    """Synchronises the local collection with AnkiWeb (incremental).

    Args:
        col: open Anki collection.
        host_key: valid user hostKey.
        endpoint: URL of a specific sync server (``sync20.ankiweb.net`` etc.).
            Obtained from the previous sync request; ``None`` — default.

    Raises:
        AuthExpiredError: if the hostKey was rejected.
        SyncError: on other failures.
    """

    auth = make_auth(host_key, endpoint)

    try:
        status = col.sync_status(auth)
    except BackendError as exc:
        _raise_sync_error(exc, "Failed to fetch sync status")

    if not status.required:
        logger.info("Sync is not required, collection is up to date")
        # AnkiDroid: ``withCol { _loadScheduler() }`` after NO_CHANGES —
        # the scheduler version may have changed on the server.
        col._load_scheduler()
        return SyncResult(required=False, new_endpoint=status.new_endpoint or None)

    logger.info("Starting collection sync with AnkiWeb")
    try:
        # ``sync_media=False`` — AnkiDroid does the same; media is
        # synced separately via ``SyncMediaWorker.start()``.
        result = col.sync_collection(auth=auth, sync_media=False)
    except BackendError as exc:
        _raise_sync_error(exc, "Server rejected sync")

    if result.server_message:
        logger.info("AnkiWeb server message: %s", result.server_message)

    full_sync_kind = _full_sync_kind(result.required)
    if full_sync_kind is not None:
        logger.warning(
            "Sync required full %s (local cards=%d)",
            full_sync_kind.value,
            col.card_count(),
        )

    return SyncResult(
        required=True,
        new_endpoint=result.new_endpoint or None,
        full_sync_kind=full_sync_kind,
        server_message=result.server_message or "",
    )


def _full_sync_kind(changes_required: int) -> FullSyncKind | None:
    """Maps ``SyncCollectionResponse.ChangesRequired`` to a :class:`FullSyncKind`."""

    return _FULL_SYNC_KIND_BY_VALUE.get(changes_required)


def full_download(
    col: Collection,
    host_key: str,
    endpoint: str | None = None,
) -> SyncResult:
    """Fully downloads the collection from AnkiWeb.

    Used when the local collection is empty (first run) and
    ``sync_collection`` silently finishes without downloading anything,
    or when the user picks "Download from AnkiWeb" in a sync conflict.

    Args:
        col: open (empty) Anki collection.
        host_key: valid user hostKey.
        endpoint: URL of a specific sync server (see ``perform_sync``).
            **Critically important**: AnkiWeb returns ``new_endpoint`` on
            the first sync — if you ignore it, the download will go to
            the wrong server and return ``HttpError: missing original size``.

    Raises:
        AuthExpiredError: if the hostKey was rejected.
        SyncError: on other failures.
    """

    return _do_full_sync(col, host_key, upload=False, endpoint=endpoint)


def full_upload(
    col: Collection,
    host_key: str,
    endpoint: str | None = None,
) -> SyncResult:
    """Fully uploads the local collection to AnkiWeb.

    Used when the user picks "Upload to AnkiWeb" in a sync conflict or
    when AnkiWeb reports that the remote collection is empty.

    Args:
        col: open Anki collection.
        host_key: valid user hostKey.
        endpoint: URL of a specific sync server (see ``perform_sync``).

    Raises:
        AuthExpiredError: if the hostKey was rejected.
        SyncError: on other failures.
    """

    return _do_full_sync(col, host_key, upload=True, endpoint=endpoint)


def _safe_reopen_after_full_sync(col: Collection, action: str) -> None:
    """Reopens ``col`` after a failed full sync, swallowing secondary errors.

    A second failure here must never mask the original exception, so any
    error from ``reopen`` is only logged.
    """

    try:
        col.reopen(after_full_sync=True)
    except Exception:  # noqa: BLE001
        logger.warning("col.reopen after failed full %s failed; continuing", action)


def _do_full_sync(
    col: Collection,
    host_key: str,
    *,
    upload: bool,
    endpoint: str | None,
) -> SyncResult:
    """Shared implementation of :func:`full_download` and :func:`full_upload`."""

    action = "upload" if upload else "download"
    auth = make_auth(host_key, endpoint)
    logger.info(
        "Starting full %s with AnkiWeb (endpoint=%s)", action, endpoint or "default"
    )

    # AnkiDroid keeps the Rust backend alive across full-sync (its
    # ``close(forFullSync=true)`` only drops the Kotlin wrapper); Python's
    # ``Collection.close()`` instead always closes the backend, so we use
    # ``close_for_full_sync()`` to drop just the wrapper and let
    # ``full_upload_or_download`` take ownership of the file.
    col.close_for_full_sync()

    try:
        col._backend.full_upload_or_download(  # type: ignore[attr-defined]
            sync_pb2.FullUploadOrDownloadRequest(
                auth=auth,
                server_usn=None,
                upload=upload,
            )
        )
    except Exception as exc:
        _safe_reopen_after_full_sync(col, action)
        if isinstance(exc, BackendError):
            _raise_sync_error(exc, f"Full {action} failed")
        raise

    col.reopen(after_full_sync=True)
    return SyncResult(required=True, new_endpoint=None)


def try_sync(
    col: Collection,
    host_key: str,
    endpoint: str | None = None,
) -> SyncResult:
    """Best-effort sync: returns ``SyncResult.error`` instead of raising."""

    try:
        return perform_sync(col, host_key, endpoint)
    except (AuthExpiredError, SyncError) as exc:
        return SyncResult(
            required=False,
            new_endpoint=None,
            error=str(exc),
            auth_expired=isinstance(exc, AuthExpiredError),
        )