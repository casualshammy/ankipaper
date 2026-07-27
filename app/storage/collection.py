"""Single point of access to the local Anki collection.

The collection is opened lazily on first access. All operations are run
in a thread pool via ``asyncio.to_thread`` so that the FastAPI event
loop is not blocked.

In the multi-account version (``app/storage/account.py``) the manager
is per-account: each instance is bound to a specific
``<account_dir>/collection.anki21`` path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

import anki.collection

logger = logging.getLogger(__name__)

T = TypeVar("T")

MEDIA_DIR_NAME = "collection.media"


class CollectionManager:
    """Manager of the local Anki collection for a single account."""

    def __init__(self, collection_path: Path) -> None:
        """Creates a manager for the collection at the given path.

        Args:
            collection_path: path to ``collection.anki21`` (not to the directory).
        """

        self._lock = asyncio.Lock()
        self._collection: anki.collection.Collection | None = None
        self._last_access: float = 0.0
        self._path = collection_path

    @property
    def collection_path(self) -> Path:
        """Path to the collection file."""

        return self._path

    def has_collection(self) -> bool:
        """True if the collection file exists on disk."""

        return self._path.exists()

    def is_open(self) -> bool:
        """True if the collection is currently open in memory."""

        return self._collection is not None

    def media_dir(self) -> Path:
        """Path to the media directory (sibling of ``collection.media``)."""

        return self._path.with_name(MEDIA_DIR_NAME)

    async def run(
        self,
        fn: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Runs the synchronous function ``fn`` with the collection open.

        Opens the collection if needed, guarantees serial access via
        ``asyncio.Lock``, and runs the call in a thread pool.

        Args:
            fn: function ``(collection, *args, **kwargs) -> T``.
            *args: positional arguments for ``fn``.
            **kwargs: keyword arguments for ``fn``.
        """

        async with self._lock:
            self._ensure_open()
            self._last_access = time.monotonic()
            assert self._collection is not None
            return await asyncio.to_thread(fn, self._collection, *args, **kwargs)

    async def close(self) -> None:
        """Closes the collection if it is open."""

        async with self._lock:
            if self._collection is not None:
                try:
                    await asyncio.to_thread(self._collection.close)
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to close collection")
                self._collection = None
                self._last_access = 0.0

    def is_idle(self, threshold_seconds: float) -> bool:
        """True if the collection is open and has not been accessed
        within the given number of seconds.

        A closed collection is never idle (there is nothing to close).
        """

        if self._collection is None:
            return False
        return (time.monotonic() - self._last_access) >= threshold_seconds

    def _ensure_open(self) -> None:
        """Opens the collection if it is not already open."""

        if self._collection is not None:
            return

        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Opening collection at %s", path)
        self._collection = anki.collection.Collection(str(path))
