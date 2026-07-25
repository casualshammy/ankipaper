"""FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from app.web.session import Session, read_session


def get_session(request: Request) -> Session:
    """Dependency, возвращающая текущую сессию пользователя."""

    return read_session(request)