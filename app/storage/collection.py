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
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import anki.collection

_common_logger = logging.getLogger(__name__)

T = TypeVar("T")

MEDIA_DIR_NAME = "collection.media"


class CollectionManager:
    """Manager of the local Anki collection for a single account."""

    def __init__(self, account_name, collection_path: Path) -> None:
        """Creates a manager for the collection at the given path.

        Args:
            collection_path: path to ``collection.anki21`` (not to the directory).
        """

        self._logger = _common_logger.getChild(account_name)
        self._lock = asyncio.Lock()
        self._collection: anki.collection.Collection | None = None
        self._last_access: float = 0.0
        self._path = collection_path

    def has_collection(self) -> bool:
        """True if the collection file exists on disk."""

        return self._path.exists()

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

    async def peek(
        self,
        fn: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T | None:
        """Runs ``fn`` against the collection without taking the run-lock.

        Returns ``None`` if the collection is closed so the caller can simply skip the tick.
        Does NOT bump ``_last_access`` — the collection is still
        considered idle while only ``peek`` calls come in.
        """

        if self._collection is None:
            return None
        return await asyncio.to_thread(fn, self._collection, *args, **kwargs)

    async def close(self) -> None:
        """Closes the collection if it is open."""

        async with self._lock:
            if self._collection is not None:
                try:
                    await asyncio.to_thread(self._collection.close)
                    self._logger.info("Collection closed successfully")
                except Exception:
                    self._logger.exception("Failed to close collection")
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

        self._logger.info("Opening collection at %s...", path)
        self._collection = anki.collection.Collection(str(path))
        self._logger.info("Collection opened successfully")
