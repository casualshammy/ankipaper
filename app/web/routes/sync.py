"""Роуты синхронизации с AnkiWeb."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import __version__
from app.config import get_settings
from app.storage import get_collection_manager, secrets
from app.sync.client import (
    AuthExpiredError,
    full_download,
    try_sync,
)
from app.sync.client import SyncError as SyncClientError
from app.sync.media_http import sync_media_direct
from app.web.deps import get_session
from app.web.session import Session

logger = logging.getLogger(__name__)

router = APIRouter()

HOSTKEY_SECRET_NAME = "ankiweb_hostkey"


# ---------------------------------------------------------------------------
# Состояние media-sync
# ---------------------------------------------------------------------------


@dataclass
class SyncState:
    """Состояние текущей/последней синхронизации с AnkiWeb.

    Обновляется фоновым таском, читается JSON-эндпоинтом для
    отображения прогресс-бара на Kindle.
    """

    status: str = "idle"  # "idle" | "running" | "done" | "error"
    phase: str = ""  # "mediaChanges" | "downloadFiles" | ""
    current: int = 0
    total: int = 0
    downloaded: int = 0
    started_at: float = 0.0
    finished_at: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Возвращает словарь для JSON-сериализации."""

        d = asdict(self)
        d["elapsed"] = (
            (self.finished_at or time.time()) - self.started_at
            if self.started_at
            else 0.0
        )
        d["percent"] = self.percent
        return d

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return max(0, min(100, int(100 * self.current / self.total)))


_state: SyncState = SyncState()


def get_sync_state() -> SyncState:
    """Singleton состояния синхронизации."""

    return _state


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _count_cards(col) -> int:
    """Возвращает количество карточек в коллекции (0 — коллекция пуста)."""

    return int(col.card_count())


async def _collection_is_empty(manager) -> bool:
    """True, если в локальной коллекции нет ни одной карточки."""

    if not manager.has_collection():
        return True
    try:
        count = await manager.run(_count_cards)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to count cards in collection")
        return True
    return count == 0


async def _run_media_sync_background(
    host_key: str,
    endpoint: str | None,
    data_dir,
    last_usn_path,
) -> None:
    """Запускает media-sync в фоне, обновляя SyncState для прогресс-бара."""

    state = get_sync_state()
    state.status = "running"
    state.phase = "mediaChanges"
    state.current = 0
    state.total = 0
    state.downloaded = 0
    state.started_at = time.time()
    state.finished_at = None
    state.error = None

    def _cb(phase: str, current: int, total: int, downloaded: int) -> None:
        state.phase = phase
        state.current = current
        state.total = total
        state.downloaded = downloaded

    try:
        await asyncio.to_thread(
            sync_media_direct,
            host_key=host_key,
            endpoint=endpoint,
            data_dir=data_dir,
            last_usn_path=last_usn_path,
            progress_callback=_cb,
        )
        state.status = "done"
        state.phase = "done"
        state.finished_at = time.time()
        state.current = state.total = 100
        logger.info(
            "Media sync completed in %.1fs", state.finished_at - state.started_at
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Background media sync failed")
        state.status = "error"
        state.error = str(exc)
        state.finished_at = time.time()


# ---------------------------------------------------------------------------
# Маршруты
# ---------------------------------------------------------------------------


@router.post("/sync", response_model=None)
async def sync_post(
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Запускает синхронизацию с AnkiWeb, редиректит на /sync/progress."""

    if not session.is_authenticated:
        return RedirectResponse("/login", status_code=303)

    host_key = secrets.load_secret(HOSTKEY_SECRET_NAME)
    if not host_key:
        return RedirectResponse("/login?reason=auth_expired", status_code=303)

    # Если sync уже идёт — переходим на прогресс-страницу.
    if get_sync_state().status == "running":
        return RedirectResponse("/sync/progress", status_code=303)

    manager = get_collection_manager()

    try:
        result = await manager.run(try_sync, host_key)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled sync error")
        return RedirectResponse(f"/?sync_error={exc}", status_code=303)

    if result.auth_expired:
        secrets.delete_secret(HOSTKEY_SECRET_NAME)
        return RedirectResponse("/login?reason=auth_expired", status_code=303)

    if result.error:
        return RedirectResponse(f"/?sync_error={result.error}", status_code=303)

    is_empty = await _collection_is_empty(manager)
    if is_empty:
        logger.info("Local collection is empty after sync, falling back to full download")
        try:
            full = await manager.run(full_download, host_key, result.new_endpoint)
        except AuthExpiredError:
            secrets.delete_secret(HOSTKEY_SECRET_NAME)
            return RedirectResponse("/login?reason=auth_expired", status_code=303)
        except SyncClientError as exc:
            return RedirectResponse(f"/?sync_error={exc}", status_code=303)
        except Exception:  # noqa: BLE001
            logger.exception("Full download failed")
            return RedirectResponse("/?sync_error=full_download_failed", status_code=303)

        if full.error:
            return RedirectResponse(f"/?sync_error={full.error}", status_code=303)

    # Media-sync в фоне (может занять минуты). Страницы poll'ят
    # ``/sync/status.json`` и показывают индикатор в шапке.
    settings = get_settings()
    last_usn_path = settings.data_dir / "media.last_usn"
    asyncio.create_task(
        _run_media_sync_background(
            host_key=host_key,
            endpoint=result.new_endpoint,
            data_dir=settings.data_dir,
            last_usn_path=last_usn_path,
        )
    )

    # Возвращаемся на главную; индикатор покажет прогресс.
    return RedirectResponse("/", status_code=303)


@router.get("/sync/status.json")
async def sync_status_json(
    session: Session = Depends(get_session),
) -> JSONResponse:
    """Лёгкий JSON-эндпоинт, который poll'ит индикатор в шапке (каждые 2 с)."""

    if not session.is_authenticated:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    return JSONResponse(get_sync_state().to_dict())
