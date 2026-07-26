"""Routes for synchronisation with AnkiWeb."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.storage.account import Account
from app.sync.auth import make_auth
from app.sync.client import (
    AuthExpiredError,
    FullSyncKind,
    SyncError as SyncClientError,
    full_download,
    full_upload,
    try_sync,
)
from app.sync.media_http import sync_media_direct
from app.sync.state import SyncState
from app.web.csrf import require_csrf
from app.web.deps import get_current_account_optional
from app.web.ratelimit import check_sync_rate_limit
from app.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    except Exception:  # noqa: BLE001
        logger.exception("Failed to count cards in collection")
        return True
    return count == 0


def _clear_conflict_state(state: SyncState) -> None:
    """Resets all per-account conflict-resolution fields."""

    state.conflict_pending = False
    state.conflict_new_endpoint = None
    state.conflict_server_message = ""
    state.conflict_direction = ""


def _start_conflict_state(
    state: SyncState,
    *,
    full_sync_kind: FullSyncKind,
    new_endpoint: str | None,
    server_message: str,
) -> None:
    """Initialises the per-account conflict-resolution state."""

    state.conflict_pending = True
    state.conflict_new_endpoint = new_endpoint
    state.conflict_server_message = server_message
    # For one-sided cases we set the direction up-front so the conflict
    # page is bypassed in favour of an immediate confirm screen.
    state.conflict_direction = (
        full_sync_kind.value
        if full_sync_kind in (FullSyncKind.DOWNLOAD, FullSyncKind.UPLOAD)
        else ""
    )


async def _run_media_sync_background(
    account: Account,
    host_key: str,
    endpoint: str | None,
    data_dir: Path,
    last_usn_path: Path,
    settings: Settings,
) -> None:
    """Runs the media sync in the background, updating the account's SyncState."""

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
        # Persist the per-collection size-limit state for the UI banner.
        # A successful sync that did not hit the limit clears the flag.
        state.media_collection_too_large = bool(result.get("collection_too_large"))
        state.status = "done"
        state.phase = "done"
        state.finished_at = time.time()
        state.current = state.total = 100
        logger.info(
            "Media sync completed in %.1fs",
            state.finished_at - state.started_at,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Background media sync failed")
        state.status = "error"
        state.error = str(exc)
        state.finished_at = time.time()


def _start_media_sync_after_full(
    account: Account,
    host_key: str,
    endpoint: str | None,
    settings: Settings,
) -> None:
    """Schedules a background media sync after a successful full upload/download."""

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


def _probe_changes(
    col: Any,
    host_key: str,
    endpoint: str | None,
) -> tuple[bool, str | None]:
    """Calls ``col.sync_status`` and returns ``(required, new_endpoint)``."""

    auth = make_auth(host_key, endpoint)
    status = col.sync_status(auth)
    return bool(status.required), status.new_endpoint or None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/sync", response_model=None)
async def sync_post(
    request: Request,
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> RedirectResponse:
    """Starts synchronisation with AnkiWeb for the current account."""

    if account is None:
        return RedirectResponse("/login", status_code=303)
    settings: Settings = request.app.state.settings

    host_key = account.host_key()
    if not host_key:
        return RedirectResponse("/login?reason=auth_expired", status_code=303)

    if not await check_sync_rate_limit(account.id):
        return RedirectResponse("/?sync_error=rate_limited", status_code=303)

    # If a sync is already running — re-show the indicator.
    if account.sync_state.status == "running":
        return RedirectResponse("/", status_code=303)

    # If an unresolved full-sync conflict is pending, route the user back to the
    # choice page instead of starting a new sync.
    if account.sync_state.conflict_pending:
        if account.sync_state.conflict_direction:
            return RedirectResponse("/sync/full/confirm", status_code=303)
        return RedirectResponse("/sync/conflict", status_code=303)

    # Invalidate the pre-sync probe — the user has just kicked off a
    # sync, so any cached ``changes_pending`` value is now stale.
    account.sync_state.changes_pending = None

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

    if result.full_sync_kind is not None:
        _start_conflict_state(
            account.sync_state,
            full_sync_kind=result.full_sync_kind,
            new_endpoint=result.new_endpoint,
            server_message=result.server_message,
        )
        if result.full_sync_kind is FullSyncKind.CONFLICT:
            return RedirectResponse("/sync/conflict", status_code=303)
        # One-sided case (FULL_DOWNLOAD / FULL_UPLOAD): skip the choice page.
        return RedirectResponse("/sync/full/confirm", status_code=303)

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

    # Media sync in the background (it can take minutes). Pages poll
    # ``/sync/status.json`` and show the indicator in the top bar.
    _start_media_sync_after_full(
        account,
        host_key,
        result.new_endpoint,
        settings,
    )

    # Return to the home page; the indicator will show progress.
    return RedirectResponse("/", status_code=303)


@router.get("/sync/status.json")
async def sync_status_json(
    account: Account | None = Depends(get_current_account_optional),
) -> JSONResponse:
    """Lightweight JSON endpoint polled by the top-bar indicator (every 2s).

    Each call also probes AnkiWeb for pending changes (single HTTP
    round-trip, no data transfer) and surfaces the result as
    ``changes_pending``. The probe is skipped while a sync is in flight
    and capped at a 3-second timeout so a slow AnkiWeb cannot stall the
    poll.
    """

    if account is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    state = account.sync_state
    if state.status != "running":
        host_key = account.host_key()
        if host_key:
            try:
                required, new_endpoint = await asyncio.wait_for(
                    account.manager.run(
                        _probe_changes, host_key, state.conflict_new_endpoint
                    ),
                    timeout=3.0,
                )
                state.changes_pending = required
                if new_endpoint:
                    state.conflict_new_endpoint = new_endpoint
            except asyncio.TimeoutError:
                logger.debug("sync_status probe timed out for %s", account.id)
            except Exception:  # noqa: BLE001
                logger.exception("sync_status probe failed for %s", account.id)

    return JSONResponse(state.to_dict())


# --- Full-sync conflict resolution ---------------------------------------


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
    if state.conflict_direction:
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
        _clear_conflict_state(state)
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
    """Executes the previously-confirmed full upload or download."""

    if account is None:
        return RedirectResponse("/login", status_code=303)
    settings: Settings = request.app.state.settings
    state = account.sync_state
    if not state.conflict_pending or direction != state.conflict_direction or direction not in ("upload", "download"):
        return RedirectResponse("/", status_code=303)

    host_key = account.host_key()
    if not host_key:
        _clear_conflict_state(state)
        account.delete_host_key()
        return RedirectResponse("/login?reason=auth_expired", status_code=303)

    endpoint = state.conflict_new_endpoint
    is_upload = direction == "upload"

    # Capture and clear conflict state up-front so a later navigation to
    # /sync/conflict doesn't try to re-use the now-consumed decision.
    _clear_conflict_state(state)

    manager = account.manager
    try:
        if is_upload:
            result = await manager.run(full_upload, host_key, endpoint)
        else:
            result = await manager.run(full_download, host_key, endpoint)
    except AuthExpiredError:
        account.delete_host_key()
        return RedirectResponse("/login?reason=auth_expired", status_code=303)
    except SyncClientError as exc:
        return RedirectResponse(f"/?sync_error={exc}", status_code=303)
    except Exception:  # noqa: BLE001
        logger.exception("Full %s failed", direction)
        return RedirectResponse(f"/?sync_error=full_{direction}_failed", status_code=303)

    if result.error:
        return RedirectResponse(f"/?sync_error={result.error}", status_code=303)

    _start_media_sync_after_full(account, host_key, endpoint, settings)
    return RedirectResponse("/", status_code=303)
