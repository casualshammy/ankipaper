"""Состояние фоновой синхронизации медиа с AnkiWeb.

Вынесено в отдельный модуль, чтобы ``Account`` (``app/storage/account.py``)
мог держать per-account инстанс без циклического импорта с роутами.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class SyncState:
    """Состояние текущей/последней синхронизации с AnkiWeb.

    Обновляется фоновым таском, читается JSON-эндпоинтом для
    отображения прогресс-бара на Kindle.
    """

    status: str = "idle"  # "idle" | "running" | "done" | "error"
    phase: str = ""  # "mediaChanges" | "downloadFiles" | ""
    current: int = 0
    total: int = 0
    downloaded: int = 0
    started_at: float = 0.0
    finished_at: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Возвращает словарь для JSON-сериализации."""

        d = asdict(self)
        d["elapsed"] = (
            (self.finished_at or time.time()) - self.started_at
            if self.started_at
            else 0.0
        )
        d["percent"] = self.percent
        return d

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return max(0, min(100, int(100 * self.current / self.total)))
