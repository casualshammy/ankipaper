from __future__ import annotations

from typing import Literal

import anki.collection
from pydantic import BaseModel, Field

from app.domain.scheduler import (
    CardIntervals,
    CardView,
    DueBreakdown,
    UndoInfo,
)

_CardTypeLabel = Literal["new", "learning", "review"]

class StudyCardIntervals(BaseModel):
    again: str
    hard: str
    good: str
    easy: str

    @classmethod
    def from_card_intervals(cls, intervals: CardIntervals) -> StudyCardIntervals:
        return cls(
            again=intervals.again,
            hard=intervals.hard,
            good=intervals.good,
            easy=intervals.easy,
        )


class StudyCardPayload(BaseModel):
    card_id: int
    deck_id: int
    question_html: str
    answer_html: str
    is_cloze: bool
    note_type_name: str
    flag: int = Field(ge=0, le=4)
    is_marked: bool
    intervals: StudyCardIntervals | None = None

    @classmethod
    def from_card_view(cls, card: CardView) -> StudyCardPayload:
        return cls(
            card_id=card.card_id,
            deck_id=card.deck_id,
            question_html=card.question_html,
            answer_html=card.answer_html,
            is_cloze=card.is_cloze,
            note_type_name=card.note_type_name,
            flag=card.flag,
            is_marked=card.is_marked,
            intervals=(
                StudyCardIntervals.from_card_intervals(card.intervals)
                if card.intervals is not None
                else None
            ),
        )


class StudyUndoPayload(BaseModel):
    label: str | None
    can_undo: bool
    can_redo: bool

    @classmethod
    def from_undo_info(cls, undo: UndoInfo) -> StudyUndoPayload:
        return cls(label=undo.label, can_undo=undo.can_undo, can_redo=undo.can_redo)


class StudyStatePayload(BaseModel):
  """Full SPA state for one review phase."""

  phase: Literal["front", "back", "done"]
  deck_id: int
  is_filtered: bool
  has_cards: bool
  undo: StudyUndoPayload
  error_msg: str
  card: StudyCardPayload | None
  remaining_total: int
  remaining_new: int
  remaining_learn: int
  remaining_review: int
  card_type: _CardTypeLabel
  sync_required: bool

  @classmethod
  def build(
    cls,
    *,
    phase: Literal["front", "back", "done"],
    deck_id: int,
    is_filtered: bool,
    has_cards: bool,
    undo: UndoInfo,
    error_msg: str,
    card: CardView | None,
    breakdown: DueBreakdown,
    sync_required: bool,
  ) -> StudyStatePayload:
    return cls(
      phase=phase,
      deck_id=deck_id,
      is_filtered=is_filtered,
      has_cards=has_cards,
      undo=StudyUndoPayload.from_undo_info(undo),
      error_msg=error_msg,
      card=StudyCardPayload.from_card_view(card) if card is not None else None,
      remaining_total=breakdown.total,
      remaining_new=breakdown.new,
      remaining_learn=breakdown.learning,
      remaining_review=breakdown.review,
      card_type=cls._card_type_label(card) if card is not None else "new",
      sync_required=sync_required,
    )

  @classmethod
  def for_done(
    cls,
    *,
    deck_id: int,
    is_filtered: bool,
    has_cards: bool,
    undo: UndoInfo,
    sync_required: bool,
  ) -> StudyStatePayload:
    return cls(
        phase="done",
        deck_id=deck_id,
        is_filtered=is_filtered,
        has_cards=has_cards,
        undo=StudyUndoPayload.from_undo_info(undo),
        error_msg="",
        card=None,
        remaining_total=0,
        remaining_new=0,
        remaining_learn=0,
        remaining_review=0,
        card_type="new",
        sync_required=sync_required,
    )

  @staticmethod
  def _card_type_label(card: CardView) -> _CardTypeLabel:
    """Map Anki's native card-type enum to the SPA's 3-way label."""

    if card.card_type_native == anki.collection.CARD_TYPE_REV:
      return "review"
    if card.card_type_native in (anki.collection.CARD_TYPE_LRN, anki.collection.CARD_TYPE_RELEARNING):
      return "learning"
    
    return "new"