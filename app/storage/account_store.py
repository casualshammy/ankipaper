import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from stat import S_ISDIR, S_ISREG

from app.storage import Account, AccountBase
from app.toolkit import ACCOUNTS_DIR, DATA_ROOT

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
        ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)

    def total_data_bytes(self) -> int:
        """Returns the total regular-file size under the data root."""

        return self._dir_size_bytes(DATA_ROOT)

    def can_create_new_account_if_not_exists(self, account_id: str, max_data_bytes: int) -> bool:
        """Returns whether a new account may be registered under the data limit."""

        path = ACCOUNTS_DIR / account_id
        if path.is_dir():
            return True
        if max_data_bytes == 0:  # 0 = unlimited
            return True
        return self.total_data_bytes() <= max_data_bytes

    def get_or_create(self, account_id: str) -> Account:
        """Returns the existing account or creates a new one from the account_id."""

        return self._cache_account(account_id, lambda: Account(account_id))

    def get_loaded_or_none(self, account_id: str) -> Account | None:
        """Returns the already-loaded account by ``account_id``, or ``None``."""

        with self._lock:
          return self._accounts.get(account_id)

    def try_load(self, account_id: str) -> Account | None:
      """Returns the account by account_id, loading it from disk if necessary.

      Args:
          account_id: sanitized account identifier.

      Returns:
          ``Account``, or ``None`` if no such directory exists on disk.
      """

      path = ACCOUNTS_DIR / account_id
      if not path.is_dir():
        return None

      return self._cache_account(account_id, lambda: Account(account_id))

    def list_loaded_accounts(self) -> list[Account]:
        """Returns a snapshot of all currently-loaded accounts (taken under lock)."""

        with self._lock:
          return list(self._accounts.values())

    def total_accounts_on_disk(self) -> int:
        """Returns the total number of account directories on disk."""

        return sum(1 for _ in ACCOUNTS_DIR.iterdir() if _.is_dir())

    def _cache_account(
      self, 
      account_id: str,
      account_func: Callable[[], Account]) -> Account:
      """
      Returns the cached account, storing it on first access.

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
          "Loaded account: id=%s dir=%s",
          account.id,
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
