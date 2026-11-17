"""Request types package."""

from app.web.req_types.deck_dto import DeckCollapseReq
from app.web.req_types.study_dto import (
    StudyCardIntervals,
    StudyCardPayload,
    StudyFlagPayload,
    StudyMarkPayload,
    StudyStatePayload,
    StudyUndoPayload,
)

__all__ = [
    "DeckCollapseReq",
    "StudyCardIntervals",
    "StudyCardPayload",
    "StudyFlagPayload",
    "StudyMarkPayload",
    "StudyUndoPayload",
    "StudyStatePayload",
]
