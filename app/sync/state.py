"""State of the background media sync with AnkiWeb.

Lives in a separate module so that ``Account`` (``app/storage/account.py``)
can hold a per-account instance without circular imports with the routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.sync.client import FullSyncKind

SyncStatePhase = Literal["collection", "mediaChanges", "downloadFiles"]

ProgressUnit = Literal["bytes", "cards", "files"]
"""Types of progress units.

- 'bytes': full-sync of collection.
- 'cards': incremental sync of collection.
- 'files': media download.
"""


@dataclass(slots=True)
class SyncState:
    """State of the current/last sync with AnkiWeb.

    Updated by the background task, read by the JSON endpoint to display
    a progress bar on Kindle.

    The ``conflict_*`` fields hold the state of an unresolved full-sync
    conflict (or one-sided full download/upload) between the local and
    remote collections: ``conflict_pending=True`` means the user still
    needs to either pick a direction (``/sync/conflict``) or confirm the
    chosen one (``/sync/full/confirm``).
    """

    status: Literal["idle", "running", "done", "error"] = "idle"
    phase: SyncStatePhase | None = None
    started_at: float = 0.0
    finished_at: float | None = None
    error: str | None = None

    progress_unit: ProgressUnit | None = None
    progress_current: int = 0
    progress_total: int = 0
    skipped_existing: int = 0

    # Unresolved full sync (conflict or one-sided full upload/download).
    conflict_pending: bool = False
    conflict_new_endpoint: str | None = None
    conflict_server_message: str = ""
    # "upload" | "download" | "" — set once the user picks a direction.
    conflict_direction: str = ""

    # Set to True after a media-sync that hit the user's per-collection
    # size limit. Surfaced on the home page so the user knows why their
    # media stopped downloading. Cleared automatically when a subsequent
    # sync finishes without hitting the limit.
    media_collection_too_large: bool = False

    # -- Conflict (full-sync) lifecycle --------------------------------

    def begin_conflict(
        self,
        *,
        full_sync_kind: FullSyncKind,
        new_endpoint: str | None,
        server_message: str,
    ) -> None:
        """Initialises the per-account full-sync-conflict state.

        For one-sided full syncs (``DOWNLOAD`` / ``UPLOAD``) the direction
        is set up-front so the conflict page is bypassed in favour of an
        immediate confirm screen.
        """

        from app.sync.client import FullSyncKind  # local import: avoid cycle

        self.conflict_pending = True
        self.conflict_new_endpoint = new_endpoint
        self.conflict_server_message = server_message
        self.conflict_direction = (
            full_sync_kind.value
            if full_sync_kind in (FullSyncKind.DOWNLOAD, FullSyncKind.UPLOAD)
            else ""
        )

    def reset_conflict(self) -> None:
        """Clears all per-account conflict-resolution fields."""

        self.conflict_pending = False
        self.conflict_new_endpoint = None
        self.conflict_server_message = ""
        self.conflict_direction = ""

    # -- Derived properties ---------------------------------------------

    @property
    def percent(self) -> int:
        """Progress in 0..100, clamped."""

        if self.progress_total <= 0:
            return 0
        return max(0, min(100, int(100 * self.progress_current / self.progress_total)))

    @property
    def is_one_sided_conflict(self) -> bool:
        """Whether the user has already chosen (or been forced into) a direction.

        Used to bypass the conflict-choice page when the server already
        constrained the user to one direction.
        """

        return bool(self.conflict_direction)
