from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Concatenate, Final, TypeVar

import anki.collection

from app.storage import secrets
from app.sync.state import SyncState
from app.toolkit import ACCOUNTS_DIR

logger = logging.getLogger(__name__)

_HOSTKEY_FILE = "hostkey.enc"
_COLLECTION_FILE = "collection.anki21"
_LAST_USN_FILE = "media.last_usn"
_MEDIA_DIR_NAME = "collection.media"

T = TypeVar("T")

# A timing breakdown is logged when any of its parts exceeds this
# threshold (milliseconds). Below it the log would be pure noise.
_LOG_SLOW_MS: float = 200.0


class AccountBase:
    """
    A single AnkiWeb account: its on-disk data.

    Attributes:
        id: safe directory name.
        account_path: path to the account directory.
    """

    id: Final[str]
    account_path: Final[Path]

    def __init__(self, account_id: str) -> None:
        self.id = account_id
        self.account_path = ACCOUNTS_DIR / self.id
        self.account_path.mkdir(parents=True, exist_ok=True)

    def host_key(self) -> str | None:
        """Returns the decrypted hostKey, or ``None``."""

        return secrets.load_secret_in(self.account_path, _HOSTKEY_FILE)
    
    def save_host_key(self, host_key: str) -> None:
        """Encrypts and saves the hostKey (mode 0600)."""

        secrets.save_secret_in(self.account_path, _HOSTKEY_FILE, host_key)

    def delete_host_key(self) -> None:
        """Deletes the hostKey if it exists."""

        secrets.delete_secret_in(self.account_path, _HOSTKEY_FILE)

    def last_usn_path(self) -> Path:
        """Path to the ``media.last_usn`` file."""

        return self.account_path / _LAST_USN_FILE

    def media_dir(self) -> Path:
        """Path to the media directory"""

        return self.account_path / _MEDIA_DIR_NAME

    def get_deck_collection_file_path(self) -> Path:
        """Path to the collection file on disk."""

        return self.account_path / _COLLECTION_FILE

    def deck_collection_file_exists(self) -> bool:
        """True if the collection file exists on disk."""

        return self.get_deck_collection_file_path().exists()

class Account(AccountBase):
    """
    An in-memory representation of an AnkiWeb account, including its on-disk data and sync state.
    """

    _logger: logging.Logger
    _lock: asyncio.Lock
    _collection: anki.collection.Collection | None = None
    _last_access: float = 0.0
    
    sync_state: Final[SyncState]

    def __init__(self, account_id: str) -> None:
        super().__init__(account_id)

        self.sync_state = SyncState()
        self._logger = logging.getLogger(f"{__name__} [{self.id}]")
        self._lock = asyncio.Lock()

    def is_open(self) -> bool:
        """True if the underlying Anki collection is currently open in memory."""

        return self._collection is not None

    async def run(
        self,
        fn: Callable[Concatenate[anki.collection.Collection, ...], T],
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

        def _call(col: anki.collection.Collection) -> T:
            return fn(col, *args, **kwargs)

        return await self._execute(_call, getattr(fn, "__name__", "fn"))

    async def run_extended(
        self,
        fn: Callable[Concatenate[Account, anki.collection.Collection, ...], T],
        *args: Any,
        **kwargs: Any) -> T:
        """
        Runs the synchronous function ``fn`` with the collection open,
        passing the account and the collection as the first two arguments.
        """

        def _call(col: anki.collection.Collection) -> T:
            return fn(self, col, *args, **kwargs)

        return await self._execute(_call, getattr(fn, "__name__", "fn"))

    async def _execute(self, fn: Callable[[anki.collection.Collection], T], label: str) -> T:
        """Runs ``fn`` under the account lock and logs a timing breakdown.

        Three delays are measured separately, because they have completely
        different causes:

        * ``lock_wait`` — waiting for the per-account lock. Long waits mean
          someone else holds it: a running background sync, or an AnkiWeb
          probe (``is_sync_required_or_throw``) that does its network
          round-trip inside the lock.
        * ``worker_wait`` — waiting for a free worker of the default
          thread-pool executor (``min(32, cpu+4)`` workers). A long wait
          means the pool is exhausted: a cancelled AnkiWeb probe keeps its
          worker busy until the HTTP call actually returns.
        * ``call`` — the actual work of ``fn``.

        Args:
            fn: function ``(collection) -> T``, called in a worker thread.
            label: name used in the log line.
        """

        t_lock = time.monotonic()
        await self._lock.acquire()
        try:
            lock_wait_ms = (time.monotonic() - t_lock) * 1000.0

            t_open = time.monotonic()
            self._ensure_collection_open()
            open_ms = (time.monotonic() - t_open) * 1000.0

            self._last_access = time.monotonic()
            col = self._collection
            assert col is not None

            started: list[float] = []

            def _runner() -> T:
                started.append(time.monotonic())
                return fn(col)

            t_submit = time.monotonic()
            result = await asyncio.to_thread(_runner)
            worker_wait_ms = (started[0] - t_submit) * 1000.0 if started else -1.0
            call_ms = (time.monotonic() - started[0]) * 1000.0 if started else -1.0

            if max(lock_wait_ms, worker_wait_ms, call_ms) >= _LOG_SLOW_MS:
                self._logger.info(
                    "account.run[%s]: lock_wait=%.0fms worker_wait=%.0fms "
                    "open=%.0fms call=%.0fms",
                    label,
                    lock_wait_ms,
                    worker_wait_ms,
                    open_ms,
                    call_ms,
                )
            return result
        finally:
            self._lock.release()

    async def peek(
        self,
        fn: Callable[Concatenate[anki.collection.Collection, ...], T],
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

    async def close_collection(self) -> None:
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

    def _ensure_collection_open(self) -> None:
        """Opens the collection if it is not already open."""

        if self._collection is not None:
            return

        path = self.get_deck_collection_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        self._logger.info("Opening collection at %s...", path)
        self._collection = anki.collection.Collection(str(path))
        self._logger.info("Collection opened successfully")
