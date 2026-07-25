"""Роуты синхронизации с AnkiWeb."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, RedirectResponse

from app.storage.account import Account
from app.sync.client import (
    AuthExpiredError,
    SyncError as SyncClientError,
    full_download,
    try_sync,
)
from app.sync.media_http import sync_media_direct
from app.sync.state import SyncState
from app.web.deps import get_current_account_optional

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _count_cards(col: Any) -> int:
    """Возвращает количество карточек в коллекции (0 — коллекция пуста)."""

    return int(col.card_count())


async def _collection_is_empty(account: Account) -> bool:
    """True, если в локальной коллекции аккаунта нет ни одной карточки."""

    manager = account.manager
    if not manager.has_collection():
        return True
    try:
        count = await manager.run(_count_cards)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to count cards in collection")
        return True
    return count == 0


async def _run_media_sync_background(
    account: Account,
    host_key: str,
    endpoint: str | None,
    data_dir: Path,
    last_usn_path: Path,
) -> None:
    """Запускает media-sync в фоне, обновляя SyncState аккаунта."""

    state: SyncState = account.sync_state
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
    account: Account | None = Depends(get_current_account_optional),
) -> RedirectResponse:
    """Запускает синхронизацию с AnkiWeb для текущего аккаунта."""

    if account is None:
        return RedirectResponse("/login", status_code=303)

    host_key = account.host_key()
    if not host_key:
        return RedirectResponse("/login?reason=auth_expired", status_code=303)

    # Если sync уже идёт — повторно показываем индикатор.
    if account.sync_state.status == "running":
        return RedirectResponse("/", status_code=303)

    manager = account.manager

    try:
        result = await manager.run(try_sync, host_key)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled sync error")
        return RedirectResponse(f"/?sync_error={exc}", status_code=303)

    if result.auth_expired:
        account.delete_host_key()
        return RedirectResponse("/login?reason=auth_expired", status_code=303)

    if result.error:
        return RedirectResponse(f"/?sync_error={result.error}", status_code=303)

    is_empty = await _collection_is_empty(account)
    if is_empty:
        logger.info("Local collection is empty after sync, falling back to full download")
        try:
            full = await manager.run(full_download, host_key, result.new_endpoint)
        except AuthExpiredError:
            account.delete_host_key()
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
    asyncio.create_task(
        _run_media_sync_background(
            account=account,
            host_key=host_key,
            endpoint=result.new_endpoint,
            data_dir=account.data_dir,
            last_usn_path=account.last_usn_path(),
        )
    )

    # Возвращаемся на главную; индикатор покажет прогресс.
    return RedirectResponse("/", status_code=303)


@router.get("/sync/status.json")
async def sync_status_json(
    account: Account | None = Depends(get_current_account_optional),
) -> JSONResponse:
    """Лёгкий JSON-эндпоинт, который poll'ит индикатор в шапке (каждые 2 с)."""

    if account is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    return JSONResponse(account.sync_state.to_dict())
