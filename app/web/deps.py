"""FastAPI dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.storage.account import Account, AccountStore, get_account_store
from app.web.session import Session, read_session


def get_session(request: Request) -> Session:
    """Dependency that returns the current user session."""

    return read_session(request)


def get_account_store_dep() -> AccountStore:
    """Dependency that returns the singleton :class:`AccountStore`."""

    return get_account_store()


def _resolve_account(session: Session, store: AccountStore) -> Account | None:
    """Returns the current account from the cookie, or ``None``."""

    if not session.is_authenticated or not session.account_id:
        return None
    cached = store.get(session.account_id)
    if cached is not None:
        return cached
    return store.ensure(session.account_id)


def get_current_account(
    session: Session = Depends(get_session),
    store: AccountStore = Depends(get_account_store_dep),
) -> Account:
    """Returns the current account from the cookie.

    Raises:
        HTTPException 401: if the user is not authenticated or the
            account is not found on disk.
    """

    account = _resolve_account(session, store)
    if account is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return account


def get_current_account_optional(
    session: Session = Depends(get_session),
    store: AccountStore = Depends(get_account_store_dep),
) -> Account | None:
    """Returns the current account, or ``None`` if not authenticated.

    Used on pages that themselves redirect to ``/login``.
    """

    return _resolve_account(session, store)
