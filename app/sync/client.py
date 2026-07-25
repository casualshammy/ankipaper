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
    """Сбой синхронизации коллекции с AnkiWeb."""


class AuthExpiredError(SyncError):
    """Сохранённый hostKey отвергнут сервером (смена пароля, блокировка)."""


@dataclass(slots=True)
class SyncResult:
    """Результат операции синхронизации."""

    required: bool
    """True, если sync фактически прошёл (на сервере были изменения)."""

    new_endpoint: str | None
    """Новый endpoint, если сервер запросил миграцию."""

    error: str | None = None
    """Человекочитаемое сообщение об ошибке, если sync не удался."""

    auth_expired: bool = False
    """True, если hostKey отвергнут и нужно заново залогиниться."""


def perform_sync(col: Collection, host_key: str, endpoint: str | None = None) -> SyncResult:
    """Синхронизирует локальную коллекцию с AnkiWeb (incremental).

    Args:
        col: открытая коллекция Anki.
        host_key: валидный hostKey пользователя.
        endpoint: URL конкретного sync-сервера (``sync20.ankiweb.net`` и т.п.).
            Получается из предыдущего sync-запроса; ``None`` — default.

    Raises:
        AuthExpiredError: если hostKey отвергнут.
        SyncError: на прочих сбоях.
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
        # AnkiDroid: ``withCol { _loadScheduler() }`` после NO_CHANGES —
        # версия планировщика могла измениться на сервере.
        col._load_scheduler()
        return SyncResult(required=False, new_endpoint=status.new_endpoint or None)

    logger.info("Starting collection sync with AnkiWeb")
    try:
        # ``sync_media=False`` — AnkiDroid делает то же самое, медиа
        # синхронизируется отдельным вызовом ``SyncMediaWorker.start()``.
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
    """Полностью скачивает коллекцию с AnkiWeb.

    Используется, когда локальная коллекция пустая (первый запуск),
    а ``sync_collection`` молча завершается без скачивания.

    Args:
        col: открытая (пустая) коллекция Anki.
        host_key: валидный hostKey пользователя.
        endpoint: URL конкретного sync-сервера (см. ``perform_sync``).
            **Критически важно**: AnkiWeb возвращает ``new_endpoint`` при
            первом sync — если его проигнорировать, download пойдёт на
            неправильный сервер и вернёт ``HttpError: missing original size``.

    Raises:
        AuthExpiredError: если hostKey отвергнут.
        SyncError: на прочих сбоях.
    """

    auth = make_auth(host_key, endpoint)
    logger.info(
        "Starting full download from AnkiWeb (endpoint=%s)",
        endpoint or "default",
    )

    # Референс AnkiDroid (``Sync.kt:handleDownload``):
    #   close(downgrade = false, forFullSync = true)   <-- НЕ закрывает Rust-бэкенд
    #   fullUploadOrDownload(auth, upload = false, serverUsn = mediaUsn)
    #   reopen(afterFullSync = true)                   <-- в finally
    #
    # В Python ``Collection.close()`` всегда вызывает ``close_collection``
    # на Rust-стороне (нет флага ``forFullSync``). Эмулируем поведение
    # AnkiDroid вручную: обнуляем Python-обёртку ``col.db = None`` через
    # ``close_for_full_sync()``, не закрывая бэкенд.
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
    """Best-effort sync: возвращает ``SyncResult.error`` вместо исключений."""

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
    """True, если BackendError сигнализирует о протухшем hostKey."""

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