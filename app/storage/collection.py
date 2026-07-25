"""Единая точка доступа к локальной коллекции Anki.

Коллекция открывается лениво при первом обращении. Все операции
выполняются в thread-pool через ``asyncio.to_thread``, чтобы не
блокировать event loop FastAPI.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

import anki.collection

from app.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

_COLLECTION_FILENAME = "collection.anki21"


class CollectionManager:
    """Singleton-менеджер локальной коллекции Anki."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._collection: anki.collection.Collection | None = None

    @property
    def collection_path(self) -> Path:
        """Путь к файлу коллекции."""

        return get_settings().data_dir / _COLLECTION_FILENAME

    def has_collection(self) -> bool:
        """True, если файл коллекции существует на диске."""

        return self.collection_path.exists()

    def is_open(self) -> bool:
        """True, если коллекция сейчас открыта в памяти."""

        return self._collection is not None

    async def run(
        self,
        fn: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Выполняет синхронную функцию ``fn`` с открытой коллекцией.

        Открывает коллекцию при необходимости, гарантирует последовательный
        доступ через ``asyncio.Lock``, выполняет вызов в thread-pool.

        Args:
            fn: функция ``(collection, *args, **kwargs) -> T``.
            *args: позиционные аргументы для ``fn``.
            **kwargs: именованные аргументы для ``fn``.
        """

        async with self._lock:
            self._ensure_open()
            assert self._collection is not None
            return await asyncio.to_thread(fn, self._collection, *args, **kwargs)

    async def close(self) -> None:
        """Закрывает коллекцию, если она открыта."""

        async with self._lock:
            if self._collection is not None:
                try:
                    await asyncio.to_thread(self._collection.close)
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to close collection")
                self._collection = None

    def _ensure_open(self) -> None:
        """Открывает коллекцию, если она ещё не открыта."""

        if self._collection is not None:
            return

        path = self.collection_path
        path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Opening collection at %s", path)
        self._collection = anki.collection.Collection(str(path))


_manager: CollectionManager | None = None


def get_collection_manager() -> CollectionManager:
    """Возвращает singleton-инстанс CollectionManager."""

    global _manager
    if _manager is None:
        _manager = CollectionManager()
    return _manager