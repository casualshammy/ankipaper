from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar, Final

import anki.collection

from app.storage import secrets
from app.sync.state import SyncState
from app.toolkit import ACCOUNTS_DIR, sanitize_account_id_or_throw

logger = logging.getLogger(__name__)

_HOSTKEY_FILE = "hostkey.enc"
_COLLECTION_FILE = "collection.anki21"
_LAST_USN_FILE = "media.last_usn"
_MEDIA_DIR_NAME = "collection.media"

T = TypeVar("T")


class AccountBase:
    """
    A single AnkiWeb account: its on-disk data.
    
    Attributes:
        id: safe directory name.
        username: original AnkiWeb username (for display).
        account_path: path to the account directory.
    """

    id: Final[str]
    username: Final[str]
    account_path: Final[Path]

    def __init__(self, username: str) -> None:
        self.id = sanitize_account_id_or_throw(username)
        self.username = username
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

    def __init__(
        self, 
        username: str) -> None:
        """Creates an account instance.

        Args:
            username: name of the account this collection belongs to (raw, as entered by the user).
        """
        super().__init__(username)

        self.sync_state = SyncState()
        self._logger = logging.getLogger(f"{__name__} [{self.id}]")
        self._lock = asyncio.Lock()

    def is_open(self) -> bool:
        """True if the underlying Anki collection is currently open in memory."""

        return self._collection is not None

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
            self._ensure_collection_open()
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
