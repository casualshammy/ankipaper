"""Пакет storage: работа с локальными коллекциями Anki и секретами."""

from app.storage.account import (
    Account,
    AccountStore,
    get_account_store,
    sanitize_account_id,
)
from app.storage.collection import CollectionManager
from app.storage.secrets import (
    delete_secret,
    delete_secret_in,
    load_secret,
    load_secret_in,
    save_secret,
    save_secret_in,
)

__all__ = [
    "Account",
    "AccountStore",
    "CollectionManager",
    "delete_secret",
    "delete_secret_in",
    "get_account_store",
    "load_secret",
    "load_secret_in",
    "sanitize_account_id",
    "save_secret",
    "save_secret_in",
]
