"""Logic on top of the Anki collection: deck statistics, card reviews, answers.

All requests to the local collection go through the backend API
(``col._backend.get_queued_cards``, ``backend.answer_card`` and similar) —
not through direct SQL.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import IntEnum
from typing import Literal

import anki.collection
import anki.errors
import anki.scheduler_pb2 as sp
from anki.cards import CardId
from anki.decks import DeckId
from anki.errors import UndoEmpty

logger = logging.getLogger(__name__)

# Anki user flags: 0 = none, 1 = red, 2 = orange, 3 = green, 4 = blue.
_FLAG_LABELS: dict[int, str] = {
    0: "none",
    1: "red",
    2: "yellow",
    3: "green",
    4: "blue",
}

# Card-type keys, in priority order used by ``_normal_to_card_type``.
# New / review / relearning / learning is also the order of fields on
# ``SchedulingState.Normal`` (see scheduler.proto).
_CARD_TYPE_FIELDS: tuple[Literal["new"], Literal["review"], Literal["relearning"], Literal["learning"]] = (
    "new", "review", "relearning", "learning")

# Tags stripped from card HTML for e-ink rendering.
_MEDIA_TAGS = ("audio", "video", "source", "iframe", "object", "embed", "canvas")

# URL prefixes that we do NOT rewrite into ``/ms/...``.
_ABSOLUTE_URL_PREFIXES = ("http://", "https://", "data:", "/ms/")


_RATING_LABELS = {
    "AGAIN": "Again",
    "HARD": "Hard",
    "GOOD": "Good",
    "EASY": "Easy",
}


class Rating(IntEnum):
    """Anki's 4-button answer scale."""

    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4

    @property
    def label(self) -> str:
        return _RATING_LABELS[self.name]


# Pre-compiled regexes for ``_sanitize_for_eink``. Module-level to avoid
# recompiling on every card render.
_RE_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_RE_MEDIA_PAIR = {
    tag: re.compile(rf"<{tag}\b[^>]*>.*?</{tag}>", re.DOTALL | re.IGNORECASE)
    for tag in _MEDIA_TAGS
}
_RE_MEDIA_SELF = {tag: re.compile(rf"<{tag}\b[^>]*/?>", re.IGNORECASE) for tag in _MEDIA_TAGS}
_RE_ANKI_PLAYBACK = re.compile(r"\[anki:(?:play|pause):[^\]]*\]")
_RE_SOUND = re.compile(r"\[sound:[^\]]*\]")
_RE_IMG_SRC = re.compile(r'<img\b[^>]*\bsrc="(?P<src>[^"]+)"[^>]*>', re.IGNORECASE)
_RE_LINK_HREF = re.compile(r'<link\b[^>]*\bhref="(?P<href>[^"]+)"[^>]*>', re.IGNORECASE)
_RE_STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_RE_STYLE_URL = re.compile(r"url\(\s*['\"]?(?P<url>[^'\")]+)['\"]?\s*\)", re.IGNORECASE)


@dataclass(slots=True)
class DeckStats:
    """Statistics for a single deck."""

    deck_id: int
    name: str
    new: int
    learning: int
    review: int
    is_filtered: bool = False


@dataclass(slots=True)
class DueBreakdown:
    """Breakdown of due cards for the current session."""

    new: int
    learning: int
    review: int

    @property
    def total(self) -> int:
        return self.new + self.learning + self.review


@dataclass(slots=True)
class CardIntervals:
    """Interval preview for the rating buttons."""

    again: str
    hard: str
    good: str
    easy: str


@dataclass(slots=True)
class CardView:
    """Card view prepared for review."""

    card_id: int
    deck_id: int
    question_html: str
    answer_html: str
    is_cloze: bool
    note_type_name: str
    fields: list[tuple[str, str]]
    card_type: str = "new"
    """Card type: ``new``, ``learning``, ``review`` or ``relearning``."""
    intervals: CardIntervals | None = None
    """Interval preview for the Again/Hard/Good/Easy buttons (or ``None``)."""
    flag: int = 0
    """User flag 0..4 (0 = none, 1 = red, 2 = orange, 3 = green, 4 = blue)."""

    is_marked: bool = False
    """True if the card's note carries Anki's ``marked`` tag (the "star")."""

    @property
    def css_class(self) -> str:
        """CSS class for the current card type (for highlighting in the template)."""

        return {
            "new": "tag-current-new",
            "learning": "tag-current-learning",
            "review": "tag-current-review",
            "relearning": "tag-current-learning",
        }.get(self.card_type, "tag-current-new")

    @property
    def flag_label(self) -> str:
        """Short e-ink-friendly label for the current flag."""

        return _FLAG_LABELS.get(self.flag, "none")


@dataclass(slots=True)
class NextInterval:
    """Time after which the card is due again."""

    seconds: int
    label: str


@dataclass(slots=True)
class AnswerOutcome:
    """Result of applying an answer."""

    next_card_id: int | None
    next_interval: NextInterval | None
    stale: bool = False


@dataclass(slots=True)
class UndoInfo:
    """Snapshot of the collection's undo stack."""

    label: str | None
    """Localised name of the undoable operation, or ``None`` when the stack is empty."""
    can_undo: bool
    """``True`` if there is an op that can be reverted."""
    can_redo: bool
    """``True`` if a previously undone op can be reapplied."""


def get_undo_status(col: anki.collection.Collection) -> UndoInfo:
    """Return the current undo-stack state."""

    status = col.undo_status()
    label = status.undo or None
    return UndoInfo(
        label=label,
        can_undo=bool(status.undo),
        can_redo=bool(status.redo),
    )


def undo_last_op(col: anki.collection.Collection) -> bool:
    """Undo the last operation."""

    try:
        col.undo()
        return True
    except UndoEmpty:
        # undo stack empty
        return False


def _queued_card_for(
    col: anki.collection.Collection,
    deck_id: int,
) -> sp.QueuedCards | None:
    """Returns queued cards for the deck, or None if the deck is not selected."""

    col.decks.select(DeckId(deck_id))
    return col._backend.get_queued_cards(  # type: ignore[attr-defined]
        fetch_limit=1,
        intraday_learning_only=False,
    )


def _walk_deck_tree(nodes: Iterable) -> Iterator:
    """Yield every deck node from a deck tree, depth-first."""

    for node in nodes:
        yield node
        yield from _walk_deck_tree(node.children)


def list_deck_stats(col: anki.collection.Collection) -> list[DeckStats]:
    """Returns statistics for all decks (new / learning / review).

    Uses the backend deck tree so reading the home page does not change
    ``curDeck``. Selecting every deck before calling ``get_queued_cards``
    marked the collection as modified and caused ``sync_status`` to report
    pending changes immediately after a successful sync.
    """

    tree = col._backend.deck_tree(now=int(time.time()))  # type: ignore[attr-defined]
    result: list[DeckStats] = []

    def _append_nodes(nodes, parent_name: str = "") -> None:
        for node in nodes:
            name = f"{parent_name}::{node.name}" if parent_name else str(node.name)
            result.append(
                DeckStats(
                    deck_id=int(node.deck_id),
                    name=name,
                    new=int(node.new_count),
                    learning=int(node.learn_count),
                    review=int(node.review_count),
                    is_filtered=bool(node.filtered),
                )
            )
            _append_nodes(node.children, name)

    _append_nodes(tree.children)
    return result


def get_deck_due_count(
    col: anki.collection.Collection,
    deck_id: int,
) -> int:
    """How many due cards are in the deck in total (new + learning + review)."""

    return get_deck_due_breakdown(col, deck_id).total


def get_deck_due_breakdown(
    col: anki.collection.Collection,
    deck_id: int,
) -> DueBreakdown:
    """Breakdown of due cards by new/learning/review for the current deck.

    Uses ``col._backend.get_queued_cards``, which returns ready-made counts
    without manual SQL.
    """

    queued = _queued_card_for(col, deck_id)
    if queued is None:
        return DueBreakdown(new=0, learning=0, review=0)
    return DueBreakdown(
        new=int(queued.new_count),
        learning=int(queued.learning_count),
        review=int(queued.review_count),
    )


def rebuild_filtered_deck(
    col: anki.collection.Collection,
    deck_id: int,
) -> int:
    """Rebuilds a filtered deck using its search terms.

    Args:
        col: open collection.
        deck_id: id of the filtered deck.

    Returns:
        Number of cards that ended up in the deck after the rebuild.
    """

    result = col._backend.rebuild_filtered_deck(int(deck_id))  # type: ignore[attr-defined]
    return int(result.count)


def get_deck_card_count(
    col: anki.collection.Collection,
    deck_id: int,
) -> int:
    """Total cards currently in the deck (no children, no limits applied)."""

    tree = col._backend.deck_tree(now=int(time.time()))  # type: ignore[attr-defined]
    target = int(deck_id)
    for node in _walk_deck_tree(tree.children):
        if int(node.deck_id) == target:
            return int(node.total_in_deck)
    return 0


def empty_filtered_deck(
    col: anki.collection.Collection,
    deck_id: int,
) -> int:
    """Returns all cards from a filtered deck to their home decks.

    Args:
        col: open collection.
        deck_id: id of the filtered deck.

    Returns:
        Number of cards that were returned to their home decks.

    Raises:
        ValueError: if the deck is not a filtered (cram) deck.
    """

    if not col.decks.is_filtered(DeckId(deck_id)):
        raise ValueError(f"deck {deck_id} is not a filtered deck")
    count = get_deck_card_count(col, deck_id)
    col._backend.empty_filtered_deck(int(deck_id))  # type: ignore[attr-defined]
    return count


def _get_card_or_raise(col: anki.collection.Collection, card_id: int):
    """Return the card or raise ``ValueError`` if it does not exist."""

    try:
        return col.get_card(CardId(card_id))
    except anki.errors.NotFoundError as exc:
        raise ValueError(f"card not found: {card_id}") from exc


def card_deck_matches_or_descends(
    col: anki.collection.Collection,
    card_deck_id: int,
    target_deck_id: int,
) -> bool:
    """True if ``card_deck_id == target_deck_id`` or is a descendant of it.

    Walks the parent chain of ``card_deck_id`` upward and checks whether
    ``target_deck_id`` appears in it. A card in a child deck belongs to
    any ancestor deck it descends from.
    """

    if card_deck_id == target_deck_id:
        return True
    deck = col.decks.get(DeckId(card_deck_id))
    if deck is None:
        return False
    parents = col.decks.parents(DeckId(card_deck_id))
    return any(int(p["id"]) == target_deck_id for p in parents)


def set_card_flag(
    col: anki.collection.Collection,
    card_id: int,
    flag: int,
) -> int:
    """Sets the user flag on a card (0..4).

    Args:
        col: open collection.
        card_id: target card id.
        flag: 0 = no flag, 1 = red, 2 = orange, 3 = green, 4 = blue.

    Returns:
        Number of cards whose flag was actually changed (0 or 1).

    Raises:
        ValueError: if ``flag`` is outside 0..4 or the card does not exist.
    """

    if not 0 <= flag <= 4:
        raise ValueError(f"invalid flag: {flag!r}")
    _get_card_or_raise(col, card_id)
    result = col.set_user_flag_for_cards(flag, [CardId(card_id)])
    return int(result.count)


def delete_note_by_card(
    col: anki.collection.Collection,
    deck_id: int,
    card_id: int,
) -> None:
    """Remove the note behind the card (and all sibling cards).

    Args:
        col: open collection.
        deck_id: target deck id.
        card_id: target card id.

    Raises:
        ValueError: if the card does not exist, or not belongs to the specified deck.
    """

    card = _get_card_or_raise(col, card_id)
    if not card_deck_matches_or_descends(col, int(card.did), deck_id):
        raise ValueError(f"Card {card_id} (deck {int(card.did)}) is not in deck {deck_id}")
    col.remove_notes_by_card([CardId(card_id)])


def set_card_marked(
    col: anki.collection.Collection,
    card_id: int,
    marked: bool,
) -> bool:
    """Toggles Anki's ``marked`` (star) tag on the card's note.

    Args:
        col: open collection.
        card_id: target card id.
        marked: ``True`` to add the ``marked`` tag, ``False`` to remove it.

    Returns:
        The resulting marked state of the card.

    Raises:
        ValueError: if the card does not exist.
    """

    note = _get_card_or_raise(col, card_id).note()
    if marked != note.has_tag("marked"):
        if marked:
            note.add_tag("marked")
        else:
            note.remove_tag("marked")
        col.update_note(note)
    return bool(note.has_tag("marked"))


def get_next_card(
    col: anki.collection.Collection,
    deck_id: int,
) -> CardView | None:
    """Returns the next due card in the deck, or None."""

    queued = _queued_card_for(col, deck_id)
    if queued is None or not queued.cards:
        logger.info(
            "get_next_card: deck_id=%s no due cards (new=%s learning=%s review=%s)",
            deck_id,
            None if queued is None else queued.new_count,
            None if queued is None else queued.learning_count,
            None if queued is None else queued.review_count,
        )
        return None
    head = queued.cards[0]
    card_state = head.states.current
    intervals = _compute_intervals(head)
    card_type = _card_type_from_state(card_state)
    logger.info(
        "get_next_card: deck_id=%s next card_id=%s type=%s "
        "state(normal=%s filtered=%s) intervals(again=%s hard=%s good=%s easy=%s) "
        "remaining new=%s learning=%s review=%s",
        deck_id,
        int(head.card.id),
        card_type,
        card_state.HasField("normal"),
        card_state.HasField("filtered"),
        intervals.again,
        intervals.hard,
        intervals.good,
        intervals.easy,
        queued.new_count,
        queued.learning_count,
        queued.review_count,
    )
    return _load_card_view(col, int(head.card.id), card_type, intervals)


def get_card_view(
    col: anki.collection.Collection,
    card_id: int,
    card_type: str = "new",
    intervals: CardIntervals | None = None,
) -> CardView | None:
    """Returns the render view of a specific card, or None.

    Args:
        col: open collection.
        card_id: card id.
        card_type: card type (``new``/``learning``/``review``/``relearning``).
            Determined on the front side and passed through the form so
            that this information is not lost on the back page (where a
            repeated ``get_queued_cards`` will no longer return this card).
        intervals: interval preview; passed through the form from the
            front page.
    """

    try:
        return _load_card_view(col, card_id, card_type, intervals)
    except anki.errors.NotFoundError:
        return None


def answer_card(
    col: anki.collection.Collection,
    card_id: int,
    rating: Rating,
    new_state: sp.SchedulingState | None = None,
    current_state: sp.SchedulingState | None = None,
    deck_id: int | None = None,
) -> AnswerOutcome:
    """Applies the user's answer and returns the next step.

    States are taken from ``get_queued_cards`` for the current deck — this
    guarantees that the states passed to ``answer_card`` correspond to the
    card's position in the due queue. Without this, Anki may interpret the
    transition incorrectly (for example, after ``Good`` the card goes to
    ``review`` with ``due`` tomorrow, and the ``new``/``review`` count in
    the current deck breaks because of that).

    Args:
        col: open collection.
        card_id: card id.
        rating: chosen rating (1..4).
        new_state: ready ``new_state`` (if None — taken from
            ``get_queued_cards``).
        current_state: ready ``current_state``.
        deck_id: id of the deck from which the queue is taken. If None —
            the deck the card currently lives in is used.
    """

    backend = col._backend  # type: ignore[attr-defined]

    if new_state is None or current_state is None:
        deck_id = _resolve_deck_id(col, card_id, deck_id)
        current_state, new_state = _resolve_answer_states(col, backend, card_id, deck_id, rating)

    try:
        backend.answer_card(
            sp.CardAnswer(
                card_id=int(card_id),
                current_state=current_state,
                new_state=new_state,
                rating=int(rating),  # type: ignore[arg-type]
                answered_at_millis=int(time.time() * 1000),
                milliseconds_taken=0,
            )
        )
    except anki.errors.InvalidInput as exc:
        if "not at top of queue" not in str(exc):
            raise
        logger.warning(
            "answer_card: discarded stale/duplicate answer card_id=%s deck_id=%s rating=%s (err=%s)",
            card_id,
            deck_id,
            int(rating),
            exc,
        )
        return AnswerOutcome(
            next_card_id=None,
            next_interval=None,
            stale=True,
        )

    next_deck_id = _resolve_deck_id(col, card_id, deck_id)
    queued = _queued_card_for(col, next_deck_id)
    next_cid: int | None = None
    if queued is not None and queued.cards:
        next_cid = int(queued.cards[0].card.id)

    logger.info(
        "answer_card: card_id=%s rating=%s deck_id=%s remaining new=%s learning=%s review=%s next=%s",
        card_id,
        int(rating),
        next_deck_id,
        queued.new_count if queued else 0,
        queued.learning_count if queued else 0,
        queued.review_count if queued else 0,
        next_cid,
    )

    return AnswerOutcome(
        next_card_id=next_cid,
        next_interval=_interval_from_state(new_state),
    )


def _resolve_deck_id(
    col: anki.collection.Collection,
    card_id: int,
    deck_id: int | None,
) -> int:
    """Resolve the deck id to use for queue lookups.

    Always prefers the ``deck_id`` passed by the caller: a parent deck in
    Anki includes its children, but not vice versa, so switching to
    ``card.did`` after an answer would narrow the queue to the leaf deck
    only — even though due cards may remain in siblings of that leaf
    under the originally selected parent.
    """

    if deck_id is not None:
        return int(deck_id)
    return int(col.get_card(CardId(card_id)).did)


def _resolve_answer_states(
    col: anki.collection.Collection,
    backend,
    card_id: int,
    deck_id: int,
    rating: Rating,
) -> tuple[sp.SchedulingState, sp.SchedulingState]:
    """Return ``(current_state, new_state)`` for the card being answered.

    Prefers states from ``get_queued_cards`` for the current deck; falls
    back to ``get_scheduling_states`` if the card is no longer at the
    head of the queue (it became not-due, or the answer came from a
    different deck).
    """

    queued = _queued_card_for(col, deck_id)
    head = queued.cards[0] if queued and queued.cards else None
    if head is not None and int(head.card.id) == int(card_id):
        logger.info(
            "answer_card: card_id=%s deck_id=%s using queued.states rating=%s",
            card_id,
            deck_id,
            int(rating),
        )
        return head.states.current, _select_new_state(head.states, rating)

    logger.warning(
        "answer_card: card_id=%s NOT in deck_id=%s queue (head=%s); using get_scheduling_states",
        card_id,
        deck_id,
        int(head.card.id) if head else None,
    )
    states = backend.get_scheduling_states(int(card_id))
    return states.current, _select_new_state(states, rating)


def _select_new_state(states: sp.SchedulingStates, rating: Rating) -> sp.SchedulingState:
    """Pick the new state for the given rating from a ``SchedulingStates`` block."""

    return getattr(states, rating.name.lower())


def _load_card_view(
    col: anki.collection.Collection,
    card_id: int,
    card_type: str = "new",
    intervals: CardIntervals | None = None,
) -> CardView:
    """Builds a CardView for a card by id.

    Args:
        col: open collection.
        card_id: card id.
        card_type: type (``new`` / ``learning`` / ``review`` / ``relearning``),
            determined from ``queued.cards[0].states.current``. ``"new"`` is
            used as a fallback if no type was passed.
        intervals: interval preview (see ``_compute_intervals``). If
            ``None`` — the ``intervals`` field will be ``None``, and the
            template will show ``"—"`` on the back page.
    """

    card = col.get_card(CardId(card_id))
    note = card.note()
    notetype = card.note_type()

    return CardView(
        card_id=int(card_id),
        deck_id=card.did,
        question_html=_sanitize_for_eink(card.question()),
        answer_html=_sanitize_for_eink(card.answer()),
        is_cloze=notetype.get("type") == anki.collection.MODEL_CLOZE,
        note_type_name=str(notetype.get("name", "")),
        fields=_extract_fields(notetype, note),
        card_type=card_type,
        intervals=intervals,
        flag=int(card.user_flag()),
        is_marked=note.has_tag("marked"),
    )


def _extract_fields(notetype, note) -> list[tuple[str, str]]:
    """Build a list of ``(name, plain_value)`` tuples for the note's fields."""

    fld_specs = notetype.get("flds", [])
    return [
        (_field_name(spec), _strip_html(value))
        for spec, value in zip(fld_specs, list(note.fields), strict=True)
    ]


def _field_name(spec) -> str:
    """Return the ``name`` from a notetype field spec (handles dict / str)."""

    return spec["name"] if isinstance(spec, dict) else str(spec)


def _normal_to_card_type(normal: sp.SchedulingState.Normal) -> str:
    """Converts ``SchedulingState.Normal`` to a user-facing type.

    ``Normal`` is a nested ``oneof`` variant of ``SchedulingState`` and
    at the same time a separate field ``ReschedulingFilter.original_state``.
    It has no ``normal`` field (unlike ``SchedulingState``), so we unpack
    it directly. Returns ``"new"`` if no recognised variant is set.
    """

    for kind in _CARD_TYPE_FIELDS:
        if normal.HasField(kind):
            return kind
    return "new"


def _card_type_from_state(state: sp.SchedulingState) -> str:
    """Determines the user-facing card type (``SchedulingState.current``).

    Handles three variants of ``SchedulingState``:
    - ``normal`` — a regular deck; subtype by ``normal.kind`` (new /
      learning / review / relearning);
    - ``filtered.rescheduling`` — a filtered deck in rescheduling mode;
      ``original_state`` has type ``Normal`` (see
      ``scheduler.proto:114-115``); we read it via ``_normal_to_card_type``;
    - ``filtered.preview`` — preview mode; treated as ``"new"``.
    """

    if state.HasField("normal"):
        return _normal_to_card_type(state.normal)

    if state.HasField("filtered"):
        filtered = state.filtered
        if filtered.HasField("rescheduling"):
            return _normal_to_card_type(filtered.rescheduling.original_state)
        # Preview (haven't started learning yet) — show as ``new`` for the UI.
        return "new"

    return "new"


def _interval_from_state(state: sp.SchedulingState) -> NextInterval | None:
    """Converts a SchedulingState into a human-readable interval.

    ``Relearning`` in the current version of the proto contains nested
    ``review`` (days) and ``learning`` (seconds); see
    ``_anki_repo/proto/anki/scheduler.proto:98-101``.
    """

    if not state.HasField("normal"):
        return None
    return _interval_from_normal(state.normal)


def _interval_from_normal(normal: sp.SchedulingState.Normal) -> NextInterval | None:
    """Convert a ``Normal`` variant to a ``NextInterval`` (or None)."""

    for kind in _CARD_TYPE_FIELDS:
        if normal.HasField(kind):
            return _interval_for_kind(getattr(normal, kind), kind)
    return None


def _interval_for_kind(msg, kind: str) -> NextInterval | None:
    """Convert one ``Normal`` sub-variant into a ``NextInterval``."""

    if kind == "new":
        return NextInterval(seconds=0, label="new")
    if kind == "review":
        return _days_interval(int(msg.scheduled_days))
    if kind == "learning":
        return _seconds_interval(int(msg.scheduled_secs or 600))
    # Relearning may contain nested review (days) or learning (seconds).
    if msg.HasField("review"):
        return _days_interval(int(msg.review.scheduled_days))
    if msg.HasField("learning"):
        return _seconds_interval(int(msg.learning.scheduled_secs or 600))
    return None


def _days_interval(days: int) -> NextInterval:
    """Build a ``NextInterval`` for a review interval measured in days."""

    seconds = days * 86400
    return NextInterval(seconds=seconds, label=_format_interval(seconds))


def _seconds_interval(seconds: int) -> NextInterval:
    """Build a ``NextInterval`` for a learning interval measured in seconds."""

    return NextInterval(seconds=seconds, label=_format_interval(seconds))


def _label_or_dash(state: sp.SchedulingState) -> str:
    """Short interval label, or ``"—"`` if the state is not recognised."""

    interval = _interval_from_state(state)
    return interval.label if interval is not None else "—"


def _compute_intervals(queued_card) -> CardIntervals:
    """Interval preview for the Again/Hard/Good/Easy buttons.

    From ``queued_card.states`` (type ``SchedulingStates``) we take the
    ``again``/``hard``/``good``/``easy`` variants — each is a
    ``SchedulingState`` from which ``_interval_from_state`` extracts the
    interval.

    For filtered decks (``rescheduling``) the states can be ``Filtered`` —
    ``_interval_from_state`` correctly returns ``"—"`` for them, and the
    preview shows dashes — which is correct: in cram mode the actual
    interval depends on the user's choice at the moment of the answer.
    """

    states = queued_card.states
    return CardIntervals(
        again=_label_or_dash(states.again),
        hard=_label_or_dash(states.hard),
        good=_label_or_dash(states.good),
        easy=_label_or_dash(states.easy),
    )


def _format_interval(seconds: int) -> str:
    """Renders seconds in a short e-ink-friendly format.

    Months are shown as ``X.Y mo`` with a single decimal place
    (for example, ``4.3 mo``), so that fractional values like
    4½ months (``4.5 mo``) are visible.
    """

    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    days = seconds // 86400
    if days < 30:
        return f"{days}d"
    if days < 365:
        months = days / 30
        return f"{months:.1f} mo"
    return f"{days // 365}y"


def _strip_html(text: str) -> str:
    """Minimal HTML stripper for plain rendering on Kindle."""

    cleaned = re.sub(r"<[^>]+>", "", text)
    return cleaned.replace("&nbsp;", " ").strip()


def _is_absolute_url(url: str) -> bool:
    """True if ``url`` already points outside the media dir or is a data URI."""

    return url.startswith(_ABSOLUTE_URL_PREFIXES)


def _rewrite_img_src(match: re.Match[str]) -> str:
    """Rewrite ``<img src="name">`` → ``<img src="/ms/name">`` if relative."""

    full = match.group(0)
    src = match["src"]
    if _is_absolute_url(src):
        return full
    return re.sub(r'src="[^"]*"', f'src="/ms/{src}"', full, count=1)


def _rewrite_link_href(match: re.Match[str]) -> str:
    """Rewrite ``<link href="name">`` → ``<link href="/ms/name">`` if relative."""

    full = match.group(0)
    href = match["href"]
    if _is_absolute_url(href):
        return full
    return re.sub(r'href="[^"]*"', f'href="/ms/{href}"', full, count=1)


def _rewrite_style_url(match: re.Match[str]) -> str:
    """Rewrite ``url(name)`` inside ``<style>`` → ``url(/ms/name)`` if relative."""

    url = match["url"]
    if _is_absolute_url(url):
        return match.group(0)
    return f"url(/ms/{url})"


def _sanitize_for_eink(html: str) -> str:
    """Prepares card HTML for rendering on e-ink Kindle.

    - Removes ``<script>`` (Kindle WebKit 1.x does not support JS and it is
      a security hole).
    - Removes ``<audio>``, ``<video>``, ``<source>``, ``<iframe>``,
      ``<object>``, ``<embed>``, ``<canvas>`` — Kindle cannot play them.
    - Removes Anki's audio/video playback markers (``[anki:play:a:0]``,
      ``[anki:pause:a:0]``, ``[anki:play:v:0]``) and the legacy
      ``[sound:filename.mp3]`` shorthand.
    - Rewrites ``<img src="filename">`` → ``<img src="/ms/filename">``, so
      that images are loaded through our media route
      (``app/web/routes/media.py``). Anki stores files in
      ``collection.media/`` next to ``collection.anki21``.
    - Preserves ``<style>`` of the card template — without it the card
      loses Anki's original styling. The blue ``color: blue`` issue for
      cloze is handled by an ``!important`` override in ``eink.css``.
    """

    html = _RE_SCRIPT.sub("", html)

    # Remove the whole tag with contents (Anki renders relearn_audio/[sound:...]
    # as <audio>, video as <video>, etc.), then any self-closing variants.
    for tag in _MEDIA_TAGS:
        html = _RE_MEDIA_PAIR[tag].sub("", html)
        html = _RE_MEDIA_SELF[tag].sub("", html)

    # Strip Anki playback markers: [anki:play:a:0], [anki:pause:v:1] and the legacy [sound:foo.mp3].
    html = _RE_ANKI_PLAYBACK.sub("", html)
    html = _RE_SOUND.sub("", html)

    # <img src="filename"> → <img src="/ms/filename">.
    html = _RE_IMG_SRC.sub(_rewrite_img_src, html)

    # <link href="filename.css"> → <link href="/ms/filename.css">. Anki
    # embeds notetype styles via <link rel="stylesheet">.
    html = _RE_LINK_HREF.sub(_rewrite_link_href, html)

    # Inside <style>: url(filename.ttf) → url(/ms/filename.ttf). Anki
    # embeds fonts via @font-face { src: url("_NotoSansJP.otf") }.
    def _rewrite_style_block(match: re.Match[str]) -> str:
        return _RE_STYLE_URL.sub(_rewrite_style_url, match.group(0))

    html = _RE_STYLE_BLOCK.sub(_rewrite_style_block, html)

    return html
