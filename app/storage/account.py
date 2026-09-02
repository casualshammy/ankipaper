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

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from app.storage import secrets
from app.storage.collection import CollectionManager
from app.sync.state import SyncState

logger = logging.getLogger(__name__)

_HOSTKEY_FILE = "hostkey.enc"
_COLLECTION_FILE = "collection.anki21"
_LAST_USN_FILE = "media.last_usn"

_DATA_ROOT = Path("/data")
_ACCOUNTS_DIR = _DATA_ROOT / "accounts"


def _dir_size_bytes(root: Path) -> int:
    """Returns the total size of all regular files under ``root``.

    Symlinks are not followed. Files that disappear mid-walk are skipped.
    """
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def sanitize_account_id(username: str) -> str:
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


@dataclass(slots=True)
class Account:
    """A single AnkiWeb account: its on-disk data and in-memory state.

    Attributes:
        id: safe directory name (see :func:`sanitize_account_id`).
        username: original AnkiWeb username (for display).
        data_dir: path to the account directory.
        manager: per-account ``CollectionManager``.
        sync_state: per-account media-sync state.
    """

    id: str
    username: str
    data_dir: Path
    manager: CollectionManager = field(init=False)
    sync_state: SyncState = field(init=False)

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.manager = CollectionManager(self.username, self.data_dir / _COLLECTION_FILE)
        self.sync_state = SyncState()

    def host_key(self) -> str | None:
        """Returns the decrypted hostKey, or ``None``."""

        return secrets.load_secret_in(self.data_dir, _HOSTKEY_FILE)

    def save_host_key(self, host_key: str) -> None:
        """Encrypts and saves the hostKey (mode 0600)."""

        secrets.save_secret_in(self.data_dir, _HOSTKEY_FILE, host_key)

    def delete_host_key(self) -> None:
        """Deletes the hostKey if it exists."""

        secrets.delete_secret_in(self.data_dir, _HOSTKEY_FILE)

    def has_host_key(self) -> bool:
        """Whether a hostKey file exists on disk (no decryption)."""

        return (self.data_dir / _HOSTKEY_FILE).exists()

    def last_usn_path(self) -> Path:
        """Path to the ``media.last_usn`` file."""

        return self.data_dir / _LAST_USN_FILE

    def media_dir(self) -> Path:
        """Path to the media directory (``collection.media/``)."""

        return self.manager.media_dir()


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

    def _safe_account_dir(self, username_or_id: str) -> Path | None:
        """Returns the account dir for a sanitised id, or ``None`` if invalid."""

        try:
            return _ACCOUNTS_DIR / sanitize_account_id(username_or_id)
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

        account_id = sanitize_account_id(username)
        return self._cache_account(
            Account(
                id=account_id,
                username=username,
                data_dir=_ACCOUNTS_DIR / account_id,
            )
        )

    def get(self, account_id: str) -> Account | None:
        """Returns the already-loaded account by ``account_id``, or ``None``."""

        with self._lock:
            return self._accounts.get(account_id)

    def iter_managers(self) -> list[CollectionManager]:
        """Snapshot of all currently-loaded managers (taken under lock)."""

        with self._lock:
            return [a.manager for a in self._accounts.values()]

    def ensure(self, account_id: str) -> Account | None:
        """Returns the account by id, loading it from disk if necessary.

        Args:
            account_id: identifier (subdirectory name under ``accounts/``).

        Returns:
            ``Account``, or ``None`` if the id is invalid or no such
            directory exists on disk.
        """

        # Without an on-disk directory there is no account to re-hydrate.
        # We check this before constructing an ``Account`` so we don't end
        # up with an in-memory object whose hostKey will never be loaded.
        path = self._safe_account_dir(account_id)
        if path is None or not path.is_dir():
            return None
        return self._cache_account(Account(id=path.name, username=path.name, data_dir=path))

    def _cache_account(self, account: Account) -> Account:
        """Returns the cached account, storing it on first access.

        Takes the registry lock, returns the existing entry if any, and
        otherwise stores ``account``. Both ``get_or_create`` and ``ensure``
        delegate here so the cache/lookup logic lives in one place.
        """

        with self._lock:
            existing = self._accounts.get(account.id)
            if existing is not None:
                return existing
            self._accounts[account.id] = account
            logger.info(
                "Loaded account: id=%s username=%s dir=%s",
                account.id,
                account.username,
                account.data_dir,
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
