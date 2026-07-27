"""Background sweeper that closes idle per-account collections.

Each ``CollectionManager`` tracks when it was last accessed via
``manager.run(...)``. The sweeper periodically walks every loaded
account and closes any open collection whose idle time exceeds the
threshold. The collection is reopened lazily on the next
``manager.run(...)`` call through the existing ``_ensure_open`` path.

Thresholds are intentionally hard-coded constants (see
``_IDLE_SECONDS`` and ``_EVICT_INTERVAL_SECONDS``) rather than
settings: tuning these is operational, not user-facing, and we do
not want self-hosted deployments to silently disable eviction.
"""

from __future__ import annotations

import asyncio
import logging

from app.storage.account import AccountStore

logger = logging.getLogger(__name__)

# Seconds of inactivity through CollectionManager.run(...) after which
# an open collection is closed. Media-sync does not count as activity.
_IDLE_SECONDS: float = 300.0

# How often the sweeper scans loaded accounts for collections to close.
_EVICT_INTERVAL_SECONDS: float = 60.0


async def _idle_collection_sweeper(store: AccountStore) -> None:
    """Periodically closes idle collections for every loaded account.

    Cancel the task to stop the loop. ``CancelledError`` is re-raised
    so callers (typically the FastAPI lifespan) can await the task
    cleanly during shutdown.
    """

    while True:
        try:
            await asyncio.sleep(_EVICT_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise

        closed = 0
        for manager in store.iter_managers():
            if manager.is_idle(_IDLE_SECONDS):
                try:
                    await manager.close()
                    closed += 1
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to close idle collection")
        if closed:
            logger.info("Idle eviction: closed %d collection(s)", closed)
