"""AnkiWeb sync client: incremental sync, full download, auth handling."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from anki import sync_pb2
from anki.collection import Collection
from anki.errors import BackendError

from app.sync.auth import make_auth

logger = logging.getLogger(__name__)


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
        if _is_auth_error(exc):
            raise AuthExpiredError(
                "AnkiWeb rejected the stored hostKey, please sign in again"
            ) from exc
        raise SyncError(f"Failed to fetch sync status: {exc}") from exc

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
        if _is_auth_error(exc):
            raise AuthExpiredError(
                "AnkiWeb rejected the stored hostKey, please sign in again"
            ) from exc
        raise SyncError(f"Server rejected sync: {exc}") from exc

    if result.server_message:
        logger.info("AnkiWeb server message: %s", result.server_message)

    return SyncResult(
        required=True,
        new_endpoint=result.new_endpoint or None,
    )


def full_download(
    col: Collection,
    host_key: str,
    endpoint: str | None = None,
) -> SyncResult:
    """Fully downloads the collection from AnkiWeb.

    Used when the local collection is empty (first run) and
    ``sync_collection`` silently finishes without downloading anything.

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

    auth = make_auth(host_key, endpoint)
    logger.info(
        "Starting full download from AnkiWeb (endpoint=%s)",
        endpoint or "default",
    )

    # AnkiDroid reference (``Sync.kt:handleDownload``):
    #   close(downgrade = false, forFullSync = true)   <-- does NOT close the Rust backend
    #   fullUploadOrDownload(auth, upload = false, serverUsn = mediaUsn)
    #   reopen(afterFullSync = true)                   <-- in finally
    #
    # In Python ``Collection.close()`` always calls ``close_collection``
    # on the Rust side (no ``forFullSync`` flag). We emulate AnkiDroid's
    # behaviour manually: drop the Python wrapper ``col.db = None`` via
    # ``close_for_full_sync()`` without closing the backend.
    col.close_for_full_sync()

    try:
        col._backend.full_upload_or_download(  # type: ignore[attr-defined]
            sync_pb2.FullUploadOrDownloadRequest(
                auth=auth,
                server_usn=None,
                upload=False,
            )
        )
    except BackendError as exc:
        try:
            col.reopen(after_full_sync=True)
        except Exception:  # noqa: BLE001
            logger.warning("col.reopen after failed full_download failed; continuing")
        if _is_auth_error(exc):
            raise AuthExpiredError(
                "AnkiWeb rejected the stored hostKey, please sign in again"
            ) from exc
        raise SyncError(f"Full download failed: {exc}") from exc
    except Exception:
        try:
            col.reopen(after_full_sync=True)
        except Exception:  # noqa: BLE001
            logger.warning("col.reopen after failed full_download failed; continuing")
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
    except AuthExpiredError as exc:
        return SyncResult(
            required=False,
            new_endpoint=None,
            error=str(exc),
            auth_expired=True,
        )
    except SyncError as exc:
        return SyncResult(
            required=False,
            new_endpoint=None,
            error=str(exc),
            auth_expired=False,
        )


def _is_auth_error(exc: BackendError) -> bool:
    """True if the BackendError indicates an expired hostKey."""

    message = str(exc).lower()
    markers = (
        "auth",
        "invalid",
        "credential",
        "token",
        "expired",
        "expire",
        "incorrect",
        "unauthorized",
    )
    return any(marker in message for marker in markers)