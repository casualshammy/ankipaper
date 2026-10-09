import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from stat import S_ISDIR, S_ISREG

from app.storage import Account, AccountBase
from app.toolkit import ACCOUNTS_DIR, DATA_ROOT, sanitize_account_id, sanitize_account_id_or_throw

logger = logging.getLogger(__name__)


class AccountStore:
    """
    In-process account registry.

    Accounts are lazily created on first access (login) and cached.
    The ``data/accounts/`` directory is created when the store is instantiated.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._accounts: dict[str, Account] = {}
        self._accounts_dir: Path = ACCOUNTS_DIR
        ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def accounts_dir(self) -> Path:
        return self._accounts_dir

    def _safe_account_dir(self, username: str) -> Path | None:
        """Returns the account dir for a sanitised id, or ``None`` if invalid."""

        try:
            return ACCOUNTS_DIR / sanitize_account_id_or_throw(username)
        except ValueError:
            return None

    def account_exists_on_disk(self, username: str) -> bool:
        """Returns whether an account directory already exists on disk."""

        path = self._safe_account_dir(username)
        return path is not None and path.is_dir()

    def total_data_bytes(self) -> int:
        """Returns the total regular-file size under the data root."""

        return self._dir_size_bytes(DATA_ROOT)

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
        for path in ACCOUNTS_DIR.iterdir():
            if path.is_dir():
                account_id = path.name
                accounts.append(AccountBase(account_id))

        return accounts

    def total_accounts_on_disk(self) -> int:
        """Returns the total number of account directories on disk."""

        return sum(1 for _ in ACCOUNTS_DIR.iterdir() if _.is_dir())

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
        path = ACCOUNTS_DIR / account_id
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

    @staticmethod
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
