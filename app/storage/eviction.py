"""Background sweeper that tries to keep the size of data under control."""

from __future__ import annotations

import asyncio
import logging

from app.storage.account import AccountStore

logger = logging.getLogger(__name__)

_IDLE_SECONDS_OPENED_COLLECTION: float = 300.0
_EVICT_INTERVAL_SECONDS: float = 60.0


async def idle_collection_sweeper(store: AccountStore) -> None:
  """
  Periodically closes idle collections for every loaded account.

  Cancel the task to stop the loop.
  """

  while True:
    await asyncio.sleep(_EVICT_INTERVAL_SECONDS)

    closed: int = 0
    for account in store.list_loaded_accounts():
      if account.is_idle(_IDLE_SECONDS_OPENED_COLLECTION):
        try:
          await account.close_collection()
          closed += 1
        except Exception:
          logger.exception("Failed to close idle collection")

    if closed > 0:
      logger.info("Idle eviction: closed %d collection(s)", closed)
