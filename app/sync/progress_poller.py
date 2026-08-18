"""Background polling of ``col._backend.latest_progress()``."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import anki.collection

from app.storage.collection import CollectionManager

if TYPE_CHECKING:
    from app.storage.account import Account
    from app.sync.state import SyncState

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 1

def _apply_to_state(progress: Any, state: SyncState) -> None:
    """Maps a Rust ``Progress`` message onto :class:`SyncState` fields."""

    if progress.HasField("full_sync"):
        fs = progress.full_sync
        state.progress_unit = "bytes"
        state.progress_current = int(fs.transferred)
        state.progress_total = int(fs.total)
    elif progress.HasField("normal_sync"):
        state.progress_unit = "cards"
        state.progress_current = 1
        state.progress_total = 1


def latest_progress(col: anki.collection.Collection) -> anki.collection.Progress:
    return col.latest_progress()


class CollectionProgressPoller:
    """Polls ``latest_progress`` on a background asyncio task."""

    def __init__(
        self,
        account: Account,
        state: SyncState,
        *,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
    ) -> None:
        self._account = account
        self._state = state
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Spins up the polling task if it is not already running."""

        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name=f"sync-progress-poller:{self._account.id}")

    async def stop(self) -> None:
        """Cancels and awaits the polling task."""

        task = self._task
        if task is None:
            return
        self._task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Progress poller raised on stop")

    async def _run(self) -> None:
        """Poll-until-cancelled loop. Swallows per-tick errors."""

        manager: CollectionManager = self._account.manager
        while True:
            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                raise
            try:
                progress = await manager.peek(latest_progress)
            except Exception:
                logger.debug("progress poll failed", exc_info=True)
                continue
            if progress is None:
                # Collection is closed (e.g. during close_for_full_sync).
                continue
            try:
                _apply_to_state(progress, self._state)
            except Exception:
                logger.exception("Failed to apply progress to state")

    