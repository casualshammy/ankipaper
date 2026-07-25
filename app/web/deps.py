"""FastAPI dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.storage.account import Account, AccountStore, get_account_store
from app.web.session import Session, read_session


def get_session(request: Request) -> Session:
    """Dependency, возвращающая текущую сессию пользователя."""

    return read_session(request)


def get_account_store_dep() -> AccountStore:
    """Dependency, возвращающая singleton :class:`AccountStore`."""

    return get_account_store()


def _resolve_account(session: Session, store: AccountStore) -> Account | None:
    """Возвращает текущий аккаунт по cookie или ``None``."""

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
    """Возвращает текущий аккаунт по cookie.

    Raises:
        HTTPException 401: если пользователь не аутентифицирован или
            аккаунт не найден на диске.
    """

    account = _resolve_account(session, store)
    if account is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return account


def get_current_account_optional(
    session: Session = Depends(get_session),
    store: AccountStore = Depends(get_account_store_dep),
) -> Account | None:
    """Возвращает текущий аккаунт или ``None``, если не аутентифицирован.

    Используется на страницах, которые сами редиректят на ``/login``.
    """

    return _resolve_account(session, store)
