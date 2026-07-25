"""Единая точка доступа к локальной коллекции Anki.

Коллекция открывается лениво при первом обращении. Все операции
выполняются в thread-pool через ``asyncio.to_thread``, чтобы не
блокировать event loop FastAPI.

С версией с поддержкой нескольких аккаунтов (``app/storage/account.py``)
менеджер создаётся per-account: каждый инстанс привязан к конкретному
пути ``<account_dir>/collection.anki21``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, TypeVar

import anki.collection

logger = logging.getLogger(__name__)

T = TypeVar("T")

MEDIA_DIR_NAME = "collection.media"


class CollectionManager:
    """Менеджер локальной коллекции Anki для одного аккаунта."""

    def __init__(self, collection_path: Path) -> None:
        """Создаёт менеджер для коллекции по указанному пути.

        Args:
            collection_path: путь к ``collection.anki21`` (не к каталогу).
        """

        self._lock = asyncio.Lock()
        self._collection: anki.collection.Collection | None = None
        self._path = collection_path

    @property
    def collection_path(self) -> Path:
        """Путь к файлу коллекции."""

        return self._path

    def has_collection(self) -> bool:
        """True, если файл коллекции существует на диске."""

        return self._path.exists()

    def is_open(self) -> bool:
        """True, если коллекция сейчас открыта в памяти."""

        return self._collection is not None

    def media_dir(self) -> Path:
        """Путь к директории медиа (сиблинг ``collection.media``)."""

        return self._path.with_name(MEDIA_DIR_NAME)

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

        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Opening collection at %s", path)
        self._collection = anki.collection.Collection(str(path))
