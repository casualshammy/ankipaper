"""State of the background media sync with AnkiWeb.

Lives in a separate module so that ``Account`` (``app/storage/account.py``)
can hold a per-account instance without circular imports with the routes.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class SyncState:
    """State of the current/last sync with AnkiWeb.

    Updated by the background task, read by the JSON endpoint to display
    a progress bar on Kindle.
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
        """Returns a dict ready for JSON serialisation."""

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
