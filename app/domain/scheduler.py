"""Логика поверх коллекции Anki: статистика колод, ревью карточек, ответы.

Все запросы к локальной коллекции идут через backend API
(``col._backend.get_queued_cards``, ``backend.answer_card`` и т.п.) —
не через прямой SQL.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from enum import IntEnum

import anki.collection
import anki.scheduler_pb2 as sp

logger = logging.getLogger(__name__)


class Rating(IntEnum):
    """4-кнопочная шкала ответов Anki."""

    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4

    @property
    def label(self) -> str:
        return _RATING_LABELS[self]


_RATING_LABELS = {
    Rating.AGAIN: "Again",
    Rating.HARD: "Hard",
    Rating.GOOD: "Good",
    Rating.EASY: "Easy",
}


@dataclass(slots=True)
class DeckStats:
    """Статистика по одной колоде."""

    deck_id: int
    name: str
    new: int
    learning: int
    review: int


@dataclass(slots=True)
class DueBreakdown:
    """Разбивка due-карточек для текущей сессии."""

    new: int
    learning: int
    review: int

    @property
    def total(self) -> int:
        return self.new + self.learning + self.review


@dataclass(slots=True)
class CardView:
    """Подготовленное для ревью представление карточки."""

    card_id: int
    question_html: str
    answer_html: str
    is_cloze: bool
    note_type_name: str
    fields: list[tuple[str, str]]
    card_type: str = "new"
    """Тип карточки: ``new``, ``learning``, ``review`` или ``relearning``."""

    @property
    def css_class(self) -> str:
        """CSS-класс текущего типа карточки (для подсветки в шаблоне)."""

        return {
            "new": "tag-current-new",
            "learning": "tag-current-learning",
            "review": "tag-current-review",
            "relearning": "tag-current-learning",
        }.get(self.card_type, "tag-current-new")


@dataclass(slots=True)
class NextInterval:
    """Через сколько карточка снова будет due."""

    seconds: int
    label: str


@dataclass(slots=True)
class AnswerOutcome:
    """Результат применения ответа."""

    next_card_id: int | None
    next_interval: NextInterval | None


def _queued_card_for(
    col: anki.collection.Collection,
    deck_id: int,
) -> sp.QueuedCards | None:
    """Возвращает queued-карточки для колоды, или None если колода не выбрана."""

    col.decks.select(int(deck_id))
    return col._backend.get_queued_cards(  # type: ignore[attr-defined]
        fetch_limit=1,
        intraday_learning_only=False,
    )


def list_deck_stats(col: anki.collection.Collection) -> list[DeckStats]:
    """Возвращает статистику по всем колодам (new / learning / review).

    Использует ``get_queued_cards`` для каждой колоды — это даёт готовые
    счётчики без ручного SQL и без риска перепутать ``days_elapsed``,
    ``queue``/``type`` и т.п.
    """

    result: list[DeckStats] = []

    for deck in col.decks.all_names_and_ids():
        try:
            queued = _queued_card_for(col, int(deck.id))
        except Exception:  # noqa: BLE001
            new = learning = review = 0
        else:
            if queued is None:
                new = learning = review = 0
            else:
                new = int(queued.new_count)
                learning = int(queued.learning_count)
                review = int(queued.review_count)

        result.append(
            DeckStats(
                deck_id=int(deck.id),
                name=str(deck.name),
                new=new,
                learning=learning,
                review=review,
            )
        )

    return result


def get_deck_due_count(
    col: anki.collection.Collection,
    deck_id: int,
) -> int:
    """Сколько всего due-карточек в колоде (new + learning + review)."""

    return get_deck_due_breakdown(col, deck_id).total


def get_deck_due_breakdown(
    col: anki.collection.Collection,
    deck_id: int,
) -> "DueBreakdown":
    """Разбивка due-карточек по new/learning/review для текущего колоды.

    Использует ``col._backend.get_queued_cards``, который возвращает
    готовые счётчики без ручного SQL.
    """

    queued = _queued_card_for(col, deck_id)
    if queued is None:
        return DueBreakdown(new=0, learning=0, review=0)
    return DueBreakdown(
        new=int(queued.new_count),
        learning=int(queued.learning_count),
        review=int(queued.review_count),
    )


def get_next_card(
    col: anki.collection.Collection,
    deck_id: int,
) -> CardView | None:
    """Возвращает следующую due-карточку в колоде или None."""

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
    card_id = int(queued.cards[0].card.id)
    card_state = queued.cards[0].states.current
    card_type = _card_type_from_state(card_state)
    logger.info(
        "get_next_card: deck_id=%s next card_id=%s type=%s "
        "state(normal=%s filtered=%s) remaining new=%s learning=%s review=%s",
        deck_id,
        card_id,
        card_type,
        card_state.HasField("normal"),
        card_state.HasField("filtered"),
        queued.new_count,
        queued.learning_count,
        queued.review_count,
    )
    return _load_card_view(col, card_id, card_type)


def get_card_view(
    col: anki.collection.Collection,
    card_id: int,
    card_type: str = "new",
) -> CardView | None:
    """Возвращает рендер-вид конкретной карточки или None.

    Args:
        col: открытая коллекция.
        card_id: id карточки.
        card_type: тип карточки (``new``/``learning``/``review``/``relearning``).
            Определяется на стороне front и пробрасывается через форму,
            чтобы не терять эту информацию на back-странице (где
            повторный ``get_queued_cards`` уже не вернёт эту карточку).
    """

    if not col.get_card(card_id):
        return None
    return _load_card_view(col, card_id, card_type)


def answer_card(
    col: anki.collection.Collection,
    card_id: int,
    rating: Rating,
    new_state: sp.SchedulingState | None = None,
    current_state: sp.SchedulingState | None = None,
    deck_id: int | None = None,
) -> AnswerOutcome:
    """Применяет ответ пользователя и возвращает следующий шаг.

    States берутся из ``get_queued_cards`` для текущей колоды — это гарантирует,
    что переданные в ``answer_card`` состояния соответствуют позиции карточки
    в due-очереди. Без этого Anki может интерпретировать переход некорректно
    (например, после ``Good`` карточка уходит в ``review`` с ``due`` завтра,
    а в текущей колоде из-за этого ломается подсчёт ``new``/``review``).

    Args:
        col: открытая коллекция.
        card_id: id карточки.
        rating: выбранный рейтинг (1..4).
        new_state: готовое ``new_state`` (если None — берётся из
            ``get_queued_cards``).
        current_state: готовое ``current_state``.
        deck_id: id колоды, из которой берётся очередь. Если None —
            используется колода, в которой сейчас лежит карточка.
    """

    backend = col._backend  # type: ignore[attr-defined]

    if new_state is None or current_state is None:
        if deck_id is None:
            card = col.get_card(card_id)
            deck_id = int(card.did)
        queued = _queued_card_for(col, int(deck_id))
        head = queued.cards[0] if queued and queued.cards else None
        if head is not None and int(head.card.id) == int(card_id):
            current_state = head.states.current
            new_state = {
                Rating.AGAIN: head.states.again,
                Rating.HARD: head.states.hard,
                Rating.GOOD: head.states.good,
                Rating.EASY: head.states.easy,
            }[rating]
            logger.info(
                "answer_card: card_id=%s deck_id=%s using queued.states rating=%s",
                card_id,
                deck_id,
                int(rating),
            )
        else:
            # Карточки нет в due-очереди (например, она не due, или ответ
            # пришёл не из текущей колоды). Фоллбэк на get_scheduling_states.
            logger.warning(
                "answer_card: card_id=%s NOT in deck_id=%s queue (head=%s); using get_scheduling_states",
                card_id,
                deck_id,
                int(head.card.id) if head else None,
            )
            states = backend.get_scheduling_states(int(card_id))
            current_state = states.current
            new_state = {
                Rating.AGAIN: states.again,
                Rating.HARD: states.hard,
                Rating.GOOD: states.good,
                Rating.EASY: states.easy,
            }[rating]

    backend.answer_card(
        sp.CardAnswer(
            card_id=int(card_id),
            current_state=current_state,
            new_state=new_state,
            rating=int(rating),
            answered_at_millis=int(time.time() * 1000),
            milliseconds_taken=0,
        )
    )

    # ВАЖНО: для поиска следующей карточки используем ИСХОДНУЮ колоду
    # (переданную пользователем), а не ``card.did``. ``card.did`` — это leaf-deck
    # карточки, и в Anki parent-deck включает child-decks, но не наоборот:
    # если ``deck_id`` — parent, а карточка лежит в child, то после ответа
    # переключение на ``card.did`` сужает очередь только до child'а — там
    # может не быть due-карточек, хотя в parent'е они есть.
    next_deck_id = int(deck_id) if deck_id is not None else int(col.get_card(card_id).did)
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


def _load_card_view(
    col: anki.collection.Collection,
    card_id: int,
    card_type: str = "new",
) -> CardView:
    """Собирает CardView по id карточки.

    Args:
        col: открытая коллекция.
        card_id: id карточки.
        card_type: тип (``new`` / ``learning`` / ``review`` / ``relearning``),
            определённый из ``queued.cards[0].states.current``. При ``"new"``
            используется как fallback, если тип не был передан.
    """

    card = col.get_card(card_id)
    note = card.note()
    notetype = card.note_type()

    is_cloze = bool(notetype.get("type") == anki.collection.MODEL_CLOZE)

    fields: list[tuple[str, str]] = []
    for fld, fval in zip(notetype.get("flds", []), list(note.fields)):
        fname = fld["name"] if isinstance(fld, dict) else str(fld)
        fields.append((fname, _strip_html(fval)))

    return CardView(
        card_id=int(card_id),
        question_html=_sanitize_for_eink(card.question()),
        answer_html=_sanitize_for_eink(card.answer()),
        is_cloze=is_cloze,
        note_type_name=str(notetype.get("name", "")),
        fields=fields,
        card_type=card_type,
    )


def _normal_to_card_type(normal: sp.Normal) -> str:
    """Преобразует ``SchedulingState.Normal`` в пользовательский тип.

    ``Normal`` — это вложенный ``oneof``-вариант ``SchedulingState`` и
    одновременно отдельное поле ``ReschedulingFilter.original_state``.
    У него нет поля ``normal`` (как у ``SchedulingState``), поэтому
    разбираем напрямую.
    """

    if normal.HasField("new"):
        return "new"
    if normal.HasField("review"):
        return "review"
    if normal.HasField("relearning"):
        return "relearning"
    if normal.HasField("learning"):
        return "learning"
    return "new"


def _card_type_from_state(state: sp.SchedulingState) -> str:
    """Определяет пользовательский тип карточки (``SchedulingState.current``).

    Учитывает три варианта ``SchedulingState``:
    - ``normal`` — обычная колода; подтип по ``normal.kind`` (new /
      learning / review / relearning);
    - ``filtered.rescheduling`` — filtered deck в режиме rescheduling;
      ``original_state`` имеет тип ``Normal`` (см. ``scheduler.proto:114-115``),
      читаем через ``_normal_to_card_type``;
    - ``filtered.preview`` — preview-режим; трактуем как ``"new"``.
    """

    if state.HasField("normal"):
        return _normal_to_card_type(state.normal)

    if state.HasField("filtered"):
        filtered = state.filtered
        # Rescheduling: рекурсивно читаем оригинальный state.
        if filtered.HasField("rescheduling"):
            return _normal_to_card_type(filtered.rescheduling.original_state)
        # Preview (ещё не начали учить) — для UI показываем как ``new``.
        return "new"

    return "new"


def _interval_from_state(state: sp.SchedulingState) -> NextInterval | None:
    """Преобразует SchedulingState в человекочитаемый интервал.

    ``Relearning`` в актуальной версии прото содержит вложенные
    ``review`` (дни) и ``learning`` (секунды); см.
    ``_anki_repo/proto/anki/scheduler.proto:98-101``.
    """

    if state.HasField("normal"):
        normal = state.normal
        if normal.HasField("review"):
            days = int(normal.review.scheduled_days)
            return NextInterval(
                seconds=days * 86400,
                label=_format_interval(days * 86400),
            )
        if normal.HasField("learning"):
            secs = int(normal.learning.scheduled_secs or 600)
            return NextInterval(seconds=secs, label=_format_interval(secs))
        if normal.HasField("relearning"):
            rel = normal.relearning
            if rel.HasField("review"):
                days = int(rel.review.scheduled_days)
                return NextInterval(
                    seconds=days * 86400,
                    label=_format_interval(days * 86400),
                )
            if rel.HasField("learning"):
                secs = int(rel.learning.scheduled_secs or 600)
                return NextInterval(seconds=secs, label=_format_interval(secs))
        if normal.HasField("new"):
            return NextInterval(seconds=0, label="new")
    return None


def _format_interval(seconds: int) -> str:
    """Рендерит секунды в короткий e-ink-friendly формат."""

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
        return f"{days // 30}mo"
    return f"{days // 365}y"


def _strip_html(text: str) -> str:
    """Минимальный HTML-stripper для plain-рендера на Kindle."""

    cleaned = re.sub(r"<[^>]+>", "", text)
    return cleaned.replace("&nbsp;", " ").strip()


def _sanitize_for_eink(html: str) -> str:
    """Готовит HTML карточки к рендеру на e-ink Kindle.

    - Удаляет ``<script>`` (Kindle WebKit 1.x не поддерживает JS и это дыра
      в безопасности).
    - Удаляет ``<audio>``, ``<video>``, ``<source>``, ``<iframe>``,
      ``<object>``, ``<embed>``, ``<canvas>`` — Kindle их не воспроизведёт.
    - Переписывает ``<img src="filename">`` → ``<img src="/ms/filename">``,
      чтобы картинки грузились через наш media-роут
      (``app/web/routes/media.py``). Anki хранит файлы в
      ``collection.media/`` рядом с ``collection.anki21``.
    - Сохраняет ``<style>`` шаблона карточки — без него карточка теряет
      оригинальное оформление Anki. Проблема синего ``color: blue`` для
      cloze решается ``!important``-override в ``eink.css``.
    """

    html = re.sub(
        r"<script\b[^>]*>.*?</script>",
        "",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Удаляем целиком с содержимым (relearn_audio/[sound:...] Anki
    # рендерит как <audio>, видео — как <video> и т.п.).
    for tag in ("audio", "video", "source", "iframe", "object", "embed", "canvas"):
        html = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Самозакрывающиеся варианты: <tag ... />
        html = re.sub(
            rf"<{tag}\b[^>]*/?>",
            "",
            html,
            flags=re.IGNORECASE,
        )

    # <img src="filename"> → <img src="/ms/filename">. Работаем только с
    # атрибутом src; остальные атрибуты (alt, style, width, height) оставляем.
    def _rewrite_img(match: re.Match[str]) -> str:
        full = match.group(0)
        src = match.group("src")
        # Не трогаем абсолютные URL и data:-URI.
        if src.startswith(("http://", "https://", "data:", "/ms/")):
            return full
        return re.sub(
            r'src="[^"]*"',
            f'src="/ms/{src}"',
            full,
            count=1,
        )

    html = re.sub(
        r'<img\b[^>]*\bsrc="(?P<src>[^"]+)"[^>]*>',
        _rewrite_img,
        html,
        flags=re.IGNORECASE,
    )

    # <link href="filename.css"> → <link href="/ms/filename.css">. Anki
    # встраивает стили notetype через <link rel="stylesheet">.
    def _rewrite_link(match: re.Match[str]) -> str:
        full = match.group(0)
        href = match.group("href")
        if href.startswith(("http://", "https://", "data:", "/ms/")):
            return full
        return re.sub(
            r'href="[^"]*"',
            f'href="/ms/{href}"',
            full,
            count=1,
        )

    html = re.sub(
        r'<link\b[^>]*\bhref="(?P<href>[^"]+)"[^>]*>',
        _rewrite_link,
        html,
        flags=re.IGNORECASE,
    )

    # Внутри <style>: url(filename.ttf) → url(/ms/filename.ttf). Anki
    # встраивает шрифты через @font-face { src: url("_NotoSansJP.otf") }.
    def _rewrite_style_url(match: re.Match[str]) -> str:
        full = match.group(0)
        url = match.group("url")
        if url.startswith(("http://", "https://", "data:", "/ms/")):
            return full
        return re.sub(
            r"url\([^)]*\)",
            f"url(/ms/{url})",
            full,
            count=1,
        )

    def _rewrite_style_block(match: re.Match[str]) -> str:
        block = match.group(0)
        return re.sub(
            r"url\(\s*['\"]?(?P<url>[^'\")]+)['\"]?\s*\)",
            _rewrite_style_url,
            block,
            flags=re.IGNORECASE,
        )

    html = re.sub(
        r"<style\b[^>]*>.*?</style>",
        _rewrite_style_block,
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    return html