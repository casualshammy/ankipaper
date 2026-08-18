"""Routes for synchronisation with AnkiWeb."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import Settings
from app.storage.account import Account
from app.sync.client import (
    AuthExpiredError,
    SyncError,
    full_download,
    full_upload,
    try_sync,
)
from app.sync.media_http import sync_media_direct
from app.sync.progress_poller import CollectionProgressPoller
from app.sync.state import SyncState, SyncStatePhase
from app.web.csrf import require_csrf
from app.web.deps import get_current_account_optional
from app.web.ratelimit import check_sync_rate_limit

_KNOWN_ERROR_CODES: frozenset[str] = frozenset({
    "auth_expired",
    "rate_limited",
    "full_download_failed",
    "full_upload_failed",
    "media_failed",
    "sync_failed",
})

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/sync", response_model=None)
async def sync_post(
    request: Request,
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> RedirectResponse:
    """Starts synchronisation with AnkiWeb for the current account.

    The actual work runs in a background task; this handler only validates
    the request, schedules the task and redirects to ``/sync/wait`` which
    renders the live progress.
    """

    if account is None:
        return RedirectResponse("/login", status_code=303)
    settings: Settings = request.app.state.settings

    host_key = account.host_key()
    if not host_key:
        return RedirectResponse("/login?reason=auth_expired", status_code=303)

    if not await check_sync_rate_limit(account.id):
        return RedirectResponse("/?sync_error=rate_limited", status_code=303)

    # If a sync is already running, jump straight to the wait screen.
    if account.sync_state.status == "running":
        return RedirectResponse("/sync/wait", status_code=303)

    # Unresolved full-sync conflict: route the user to the right screen
    # without starting a new sync.
    if account.sync_state.conflict_pending:
        if account.sync_state.is_one_sided_conflict:
            return RedirectResponse("/sync/full/confirm", status_code=303)
        return RedirectResponse("/sync/conflict", status_code=303)

    account.sync_state.status = "running"
    account.sync_state.phase = "collection"

    _start_collection_sync_background(account, host_key, settings)
    return RedirectResponse("/sync/wait", status_code=303)


@router.get("/sync/wait", response_model=None)
async def sync_wait_get(
    request: Request,
    account: Account | None = Depends(get_current_account_optional),
) -> HTMLResponse | RedirectResponse:
    """Renders the current sync state with a meta-refresh self-poll.

    Routes to ``/``, ``/login`` or the full-sync conflict pages depending
    on the terminal/branch state of ``account.sync_state``.
    """

    if account is None:
        return RedirectResponse("/login", status_code=303)

    state = account.sync_state

    # Conflict pending → the user must make a choice; no auto-refresh.
    if state.conflict_pending:
        if state.is_one_sided_conflict:
            return RedirectResponse("/sync/full/confirm", status_code=303)
        return RedirectResponse("/sync/conflict", status_code=303)

    if state.status == "error":
        if state.error == "auth_expired":
            return RedirectResponse("/login?reason=auth_expired", status_code=303)
        templates: Jinja2Templates = request.app.state.templates
        return templates.TemplateResponse(
            request,
            "sync_wait.html",
            {
                "version": __version__,
                "phase": "",
                "progress_unit": state.progress_unit,
                "percent": 0,
                "skipped_existing": state.skipped_existing,
                "error": state.error,
            },
        )

    if state.status == "done":
        # Brief "Done" screen, then bounce to home.
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request,
            "sync_wait.html",
            {
                "version": __version__,
                "phase": None,
                "progress_unit": state.progress_unit,
                "percent": 100,
                "skipped_existing": state.skipped_existing,
                "error": None,
            },
        )

    if state.status == "idle":
        return RedirectResponse("/", status_code=303)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "sync_wait.html",
        {
            "version": __version__,
            "phase": state.phase or "collection",
            "progress_unit": state.progress_unit,
            "percent": state.percent,
            "skipped_existing": state.skipped_existing,
            "error": None,
        },
    )


@router.get("/sync/conflict", response_class=HTMLResponse)
async def sync_conflict_get(
    request: Request,
    account: Account | None = Depends(get_current_account_optional),
) -> HTMLResponse:
    """Renders the direction-choice page for a FULL_SYNC conflict."""

    if account is None:
        return HTMLResponse(status_code=303, headers={"Location": "/login"})
    state = account.sync_state
    if not state.conflict_pending:
        return HTMLResponse(status_code=303, headers={"Location": "/"})
    if state.is_one_sided_conflict:
        # The user already picked a direction — straight to the confirm page.
        return HTMLResponse(
            status_code=303, headers={"Location": "/sync/full/confirm"}
        )

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "sync_conflict.html",
        {
            "server_message": state.conflict_server_message,
        },
    )


@router.post("/sync/conflict", response_model=None)
async def sync_conflict_post(
    direction: str = Form(...),
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> RedirectResponse:
    """Records the user's chosen direction (or cancellation)."""

    if account is None:
        return RedirectResponse("/login", status_code=303)
    state = account.sync_state
    if not state.conflict_pending:
        return RedirectResponse("/", status_code=303)

    if direction == "cancel":
        state.reset_conflict()
        return RedirectResponse("/", status_code=303)

    if direction in ("upload", "download"):
        state.conflict_direction = direction
        return RedirectResponse("/sync/full/confirm", status_code=303)

    return RedirectResponse("/sync/conflict", status_code=303)


@router.get("/sync/full/confirm", response_class=HTMLResponse)
async def sync_full_confirm_get(
    request: Request,
    account: Account | None = Depends(get_current_account_optional),
) -> HTMLResponse:
    """Renders the confirmation page before executing the full upload/download."""

    if account is None:
        return HTMLResponse(status_code=303, headers={"Location": "/login"})
    state = account.sync_state
    if not state.conflict_pending or state.conflict_direction not in ("upload", "download"):
        return HTMLResponse(status_code=303, headers={"Location": "/"})

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "sync_confirm_full.html",
        {
            "direction": state.conflict_direction,
            "server_message": state.conflict_server_message,
        },
    )


@router.post("/sync/full", response_model=None)
async def sync_full_post(
    request: Request,
    direction: str = Form(...),
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> RedirectResponse:
    """Executes the previously-confirmed full upload or download.

    The actual transfer runs in a background task; this handler only
    validates the request, schedules it and redirects to ``/sync/wait``.
    """

    if account is None:
        return RedirectResponse("/login", status_code=303)
    settings: Settings = request.app.state.settings
    state = account.sync_state
    if not state.conflict_pending or direction != state.conflict_direction or direction not in ("upload", "download"):
        return RedirectResponse("/", status_code=303)

    host_key = account.host_key()
    if not host_key:
        state.reset_conflict()
        account.delete_host_key()
        return RedirectResponse("/login?reason=auth_expired", status_code=303)

    # Capture and clear conflict state up-front so /sync/conflict doesn't
    # re-use a consumed decision. The background task doesn't touch conflict
    # state (it's not a sync, it's a full transfer).
    endpoint = state.conflict_new_endpoint
    state.reset_conflict()
    state.status = "running"
    state.phase = "collection"
    is_upload = direction == "upload"

    asyncio.create_task(
        _run_full_sync_background(
            account=account,
            host_key=host_key,
            endpoint=endpoint,
            is_upload=is_upload,
            settings=settings,
        )
    )
    return RedirectResponse("/sync/wait", status_code=303)


async def _run_full_sync_background(
    account: Account,
    host_key: str,
    endpoint: str | None,
    is_upload: bool,
    settings: Settings,
) -> None:
    """Runs a confirmed full upload/download, then chains into media sync."""

    state = account.sync_state
    state.status = "running"
    state.phase = "collection"
    state.progress_unit = None
    state.progress_current = 0
    state.progress_total = 0
    state.skipped_existing = 0
    state.started_at = time.time()
    state.finished_at = None
    state.error = None

    poller = CollectionProgressPoller(account, state)
    poller.start()
    try:
        manager = account.manager
        if is_upload:
            await manager.run(full_upload, host_key, endpoint)
        else:
            await manager.run(full_download, host_key, endpoint)
    except AuthExpiredError:
        _fail_sync(state, error="auth_expired", account=account)
        return
    except SyncError as exc:
        _fail_sync(state, error=str(exc), account=account)
        return
    except Exception:
        logger.exception("Full %s failed", "upload" if is_upload else "download")
        _fail_sync(state, error=f"full_{'upload' if is_upload else 'download'}_failed", account=account)
        return
    finally:
        await poller.stop()

    _start_media_sync_background(
        account=account,
        host_key=host_key,
        endpoint=endpoint,
        settings=settings,
    )


def _count_cards(col: Any) -> int:
    """Returns the number of cards in the collection (0 means empty)."""

    return int(col.card_count())


async def _collection_is_empty(account: Account) -> bool:
    """True if the local collection of the account contains no cards."""

    manager = account.manager
    if not manager.has_collection():
        return True
    try:
        count = await manager.run(_count_cards)
    except Exception:
        logger.exception("Failed to count cards in collection")
        return True
    return count == 0


def _safe_error_code(error: str) -> str:
    """Reduce an arbitrary error string to a known user-safe code."""

    raw = (error or "").strip().splitlines()[0] if error else ""
    if raw in _KNOWN_ERROR_CODES:
        return raw
    lowered = raw.lower()
    if "rate" in lowered and "limit" in lowered:
        return "rate_limited"
    if "media" in lowered:
        return "media_failed"
    if "full" in lowered and "download" in lowered:
        return "full_download_failed"
    if "full" in lowered and "upload" in lowered:
        return "full_upload_failed"
    return "sync_failed"


def _fail_sync(
    state: SyncState,
    *,
    error: str,
    account: Account,
) -> None:
    """Records a terminal sync error on ``state`` and finishes the run.

    For ``error == "auth_expired"`` the stored hostKey is deleted.
    """

    state.status = "error"
    state.phase = None
    state.error = _safe_error_code(error)
    state.finished_at = time.time()
    if state.error == "auth_expired":
        account.delete_host_key()


async def _run_collection_sync_background(
    account: Account,
    host_key: str,
    settings: Settings,
) -> None:
    """Runs the collection sync (incremental, or full download on empty).

    Sets ``account.sync_state`` to ``running``/``collection`` while it runs
    so ``/sync/wait`` can render progress. On success it hands off to the
    media sync background task. On a full-sync conflict it pauses and
    sets ``conflict_pending`` for ``/sync/wait`` to redirect.
    """

    state: SyncState = account.sync_state
    state.status = "running"
    state.phase = "collection"
    state.progress_unit = None
    state.progress_current = 0
    state.progress_total = 0
    state.skipped_existing = 0
    state.started_at = time.time()
    state.finished_at = None
    state.error = None

    poller = CollectionProgressPoller(account, state)
    poller.start()
    try:
        manager = account.manager
        try:
            result = await manager.run(try_sync, host_key)
        except Exception as exc:
            logger.exception("Background collection sync raised")
            _fail_sync(state, error=str(exc), account=account)
            return

        if result.auth_expired:
            _fail_sync(state, error="auth_expired", account=account)
            return

        if result.error:
            _fail_sync(state, error=result.error, account=account)
            return

        if result.full_sync_kind is not None:
            state.begin_conflict(
                full_sync_kind=result.full_sync_kind,
                new_endpoint=result.new_endpoint,
                server_message=result.server_message,
            )
            # No background activity while waiting for the user to resolve.
            state.status = "idle"
            state.phase = None
            state.finished_at = time.time()
            return

        is_empty = await _collection_is_empty(account)
        if is_empty:
            logger.info("Local collection empty, falling back to full download")
            try:
                full = await manager.run(full_download, host_key, result.new_endpoint)
            except AuthExpiredError:
                _fail_sync(state, error="auth_expired", account=account)
                return
            except SyncError as exc:
                _fail_sync(state, error=str(exc), account=account)
                return
            except Exception:
                logger.exception("Full download failed")
                _fail_sync(state, error="full_download_failed", account=account)
                return
            if full.error:
                _fail_sync(state, error=full.error, account=account)
                return
    finally:
        await poller.stop()

    # Collection done. Hand off to media sync.
    _start_media_sync_background(
        account=account,
        host_key=host_key,
        endpoint=result.new_endpoint,
        settings=settings,
    )


def _start_media_sync_background(
    account: Account,
    host_key: str,
    endpoint: str | None,
    settings: Settings,
) -> None:
    """Schedules a background media sync (preserves ``started_at``)."""

    state = account.sync_state
    # Reset only media-specific fields; keep ``started_at`` from collection phase.
    state.status = "running"
    state.phase = "mediaChanges"
    state.progress_unit = "files"
    state.progress_current = 0
    state.progress_total = 0
    state.skipped_existing = 0
    state.finished_at = None
    state.error = None

    asyncio.create_task(
        _run_media_sync_background(
            account=account,
            host_key=host_key,
            endpoint=endpoint,
            data_dir=account.data_dir,
            last_usn_path=account.last_usn_path(),
            settings=settings,
        )
    )


async def _run_media_sync_background(
    account: Account,
    host_key: str,
    endpoint: str | None,
    data_dir: Path,
    last_usn_path: Path,
    settings: Settings,
) -> None:
    """Runs the media sync, updating ``account.sync_state`` for the UI."""

    state: SyncState = account.sync_state
    state.status = "running"
    state.phase = "mediaChanges"
    state.progress_unit = "files"
    state.progress_current = 0
    state.progress_total = 0
    state.skipped_existing = 0
    if not state.started_at:
        state.started_at = time.time()
    state.finished_at = None
    state.error = None

    def _cb(
        phase: SyncStatePhase,
        current: int,
        total: int,
        skipped_existing: int) -> None:
        state.phase = phase
        state.progress_unit = "files"
        state.progress_current = current
        state.progress_total = total
        state.skipped_existing = skipped_existing

    try:
        result = await asyncio.to_thread(
            sync_media_direct,
            host_key=host_key,
            endpoint=endpoint,
            data_dir=data_dir,
            last_usn_path=last_usn_path,
            progress_callback=_cb,
            max_file_bytes=settings.media_max_file_bytes,
            max_collection_bytes=settings.media_max_collection_bytes,
        )
        state.media_collection_too_large = result.collection_too_large
        state.status = "done"
        state.phase = None
        state.finished_at = time.time()
        state.progress_unit = None
        state.progress_current = 100
        state.progress_total = 100
        logger.info(
            "Media sync completed in %.1fs",
            state.finished_at - state.started_at,
        )
    except Exception as exc:
        logger.exception("Background media sync failed")
        _fail_sync(state, error=str(exc), account=account)


def _start_collection_sync_background(
    account: Account,
    host_key: str,
    settings: Settings,
) -> None:
    """Schedules a background collection sync."""

    asyncio.create_task(
        _run_collection_sync_background(
            account=account,
            host_key=host_key,
            settings=settings,
        )
    )
