"""Per-account storage: collection, hostKey, sync state, media.

Each AnkiWeb account corresponds to a directory ``data/accounts/<id>/``:

    collection.anki21
    hostkey.enc
    media.last_usn
    collection.media/

``<id>`` is the AnkiWeb username after minimal sanitisation so that
it can be used as a directory name. The username is stored in the cookie
and is used for display in the UI.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import Any, TypeVar

import anki.collection

from app.storage import secrets
from app.sync.state import SyncState

logger = logging.getLogger(__name__)

_HOSTKEY_FILE = "hostkey.enc"
_COLLECTION_FILE = "collection.anki21"
_LAST_USN_FILE = "media.last_usn"

_DATA_ROOT = Path("/data")
_ACCOUNTS_DIR = _DATA_ROOT / "accounts"
_MEDIA_DIR_NAME = "collection.media"

T = TypeVar("T")


def _dir_size_bytes(root: Path) -> int:
    """Returns the total size of all regular files under ``root``. Symlinks are not followed."""

    total = 0
    stack = [str(root)]
    while len(stack) > 0:
        for entry in os.scandir(stack.pop()):
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if S_ISDIR(stat.st_mode):
                stack.append(entry.path)
            elif S_ISREG(stat.st_mode):
                total += stat.st_size

    return total


def sanitize_account_id_or_throw(username: str) -> str:
    """Returns a safe account directory name from an AnkiWeb username.

    The username is lowercased so that ``Alice`` and ``alice`` map to the
    same account — AnkiWeb treats usernames case-insensitively, and a
    case-different login would otherwise create a duplicate account and
    silently overwrite the hostKey.

    Raises:
        ValueError: if the username is empty, reserved, or contains
            invalid characters (``/``, ``\\``, NUL).
    """

    if not username:
        raise ValueError("Empty username")
    if username != username.strip():
        raise ValueError("Username has leading or trailing whitespace")
    cleaned = username.lower()
    if cleaned in (".", ".."):
        raise ValueError("Reserved account id")
    if any(c in cleaned for c in ("/", "\\", "\0")):
        raise ValueError("Invalid characters in username")
    if len(cleaned) > 64:
        raise ValueError("Username too long")
    return cleaned

def sanitize_account_id(username: str) -> str | None:
    """
    Returns a safe account directory name from an AnkiWeb username.

    Returns ``None`` if the username is invalid.
    """

    try:
        return sanitize_account_id_or_throw(username)
    except ValueError:
        return None


class AccountBase:
    """A single AnkiWeb account: its on-disk data.
    
    Attributes:
        id: safe directory name.
        username: original AnkiWeb username (for display).
        account_path: path to the account directory.
    """

    id: str
    username: str
    account_path: Path

    def __init__(self, username: str) -> None:
        self.id = sanitize_account_id_or_throw(username)
        self.username = username
        self.account_path = _ACCOUNTS_DIR / self.id
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
    _logger: logging.Logger
    _lock: asyncio.Lock
    _collection: anki.collection.Collection | None = None
    _last_access: float = 0.0
    
    sync_state: SyncState

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


class AccountStore:
    """In-process account registry.

    Accounts are lazily created on first access (login) and cached.
    The ``data/accounts/`` directory is created when the store is instantiated.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._accounts: dict[str, Account] = {}
        self._accounts_dir: Path = _ACCOUNTS_DIR
        _ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def accounts_dir(self) -> Path:
        return self._accounts_dir

    def _safe_account_dir(self, username: str) -> Path | None:
        """Returns the account dir for a sanitised id, or ``None`` if invalid."""

        try:
            return _ACCOUNTS_DIR / sanitize_account_id_or_throw(username)
        except ValueError:
            return None

    def account_exists_on_disk(self, username: str) -> bool:
        """Returns whether an account directory already exists on disk."""

        path = self._safe_account_dir(username)
        return path is not None and path.is_dir()

    def total_data_bytes(self) -> int:
        """Returns the total regular-file size under the data root."""

        return _dir_size_bytes(_DATA_ROOT)

    def can_create_account_on_disk(self, username: str, max_data_bytes: int) -> bool:
        """Returns whether a new account may be registered under the data limit."""

        if self.account_exists_on_disk(username):
            return True
        if max_data_bytes == 0:  # 0 = unlimited
            return True
        return self.total_data_bytes() <= max_data_bytes

    def get_or_create(self, username: str) -> Account:
        """Returns the existing account or creates a new one from the username."""

        account_id = sanitize_account_id_or_throw(username)
        return self._cache_account(account_id, lambda: Account(username))

    def get(self, username: str) -> Account | None:
        """Returns the already-loaded account by ``username``, or ``None``."""

        account_id = sanitize_account_id(username)
        if account_id is None:
            return None
        with self._lock:
            return self._accounts.get(account_id)

    def list_loaded_accounts(self) -> list[Account]:
        """Returns a snapshot of all currently-loaded accounts (taken under lock)."""

        with self._lock:
            return list(self._accounts.values())

    def list_all_accounts_on_disk(self) -> list[AccountBase]:
        """Returns a list of all accounts present on disk."""

        accounts = []
        for path in _ACCOUNTS_DIR.iterdir():
            if path.is_dir():
                account_id = path.name
                accounts.append(AccountBase(account_id))

        return accounts

    def total_accounts_on_disk(self) -> int:
        """Returns the total number of account directories on disk."""

        return sum(1 for _ in _ACCOUNTS_DIR.iterdir() if _.is_dir())

    def ensure(self, username: str) -> Account | None:
        """Returns the account by username, loading it from disk if necessary.

        Args:
            username: raw username of the account (as entered by the user).

        Returns:
            ``Account``, or ``None`` if the username is invalid or no such directory exists on disk.
        """

        account_id = sanitize_account_id(username)
        if account_id is None:
            return None
        path = _ACCOUNTS_DIR / account_id
        if path is None or not path.is_dir():
            return None
        
        return self._cache_account(account_id, lambda: Account(username)) 

    def _cache_account(
            self, 
            account_id: str,
            account_func: Callable[[], Account]) -> Account:
        """Returns the cached account, storing it on first access.

        Takes the registry lock, returns the existing entry if any, and
        otherwise stores ``account``. Both ``get_or_create`` and ``ensure``
        delegate here so the cache/lookup logic lives in one place.
        """

        with self._lock:
            existing = self._accounts.get(account_id)
            if existing is not None:
                return existing

            account = account_func()
            self._accounts[account_id] = account
            logger.info(
                "Loaded account: id=%s username=%s dir=%s",
                account.id,
                account.username,
                account.account_path,
            )
            return account


_store: AccountStore | None = None
_store_lock = threading.Lock()


def get_account_store() -> AccountStore:
    """Returns the singleton :class:`AccountStore` instance."""

    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = AccountStore()
    return _store
