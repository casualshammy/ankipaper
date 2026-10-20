// All render functions. The single `render(state)` entry point replaces
// the contents of ctx.root; optimistic mark/flag and the delete modal
// patch existing nodes in place to avoid a full re-render.

import { el, clear, scrollAndFocus } from './_dom';
import type { SpaCtx } from './_state';
import type {
  StudyCardIntervals,
  StudyCardType,
  StudyState,
} from './_types';

export function createRender(ctx: SpaCtx) {
  function updateSyncIndicator(syncRequired: boolean): void {
    const ind = document.getElementById('sync-indicator');
    if (!ind) return;
    ind.textContent = syncRequired ? ' ●' : '';
  }

  function renderBar(p: StudyState): HTMLElement {
    if (!p.card) return el('div');
    const card = p.card;
    const cur = p.card_type;

    function chipClass(kind: StudyCardType): string {
      if (kind === cur) return 'chip chip-current';
      const n =
        kind === 'learning' ? p.remaining_learn : kind === 'review' ? p.remaining_review : p.remaining_new;
      return n === 0 ? 'chip chip-empty' : 'chip';
    }

    function flagBtn(action: 'mark' | 'flag', value: number, label: string, aria: string): HTMLElement {
      const active = action === 'mark' ? !!card.is_marked : card.flag === value;
      return el(
        'button',
        {
          type: 'button',
          class: 'flag-btn' + (active ? ' flag-btn-on' : ''),
          'data-action': action,
          'data-flag': String(value),
          'aria-label': aria,
        },
        label,
      );
    }

    const summary = el(
      'div',
      { class: 'summary' },
      el(
        'div',
        { class: 'summary-total' },
        String(p.remaining_total),
        el('span', { class: 'summary-total-label', text: 'remaining' }),
      ),
      el(
        'div',
        { class: 'breakdown' },
        el('span', { class: chipClass('new'), text: 'new ' + p.remaining_new }),
        el('span', { class: chipClass('learning'), text: 'learn ' + p.remaining_learn }),
        el('span', { class: chipClass('review'), text: 'review ' + p.remaining_review }),
      ),
    );

    const toolbar = el(
      'div',
      { class: 'flag-toolbar' },
      el(
        'div',
        { class: 'flag-buttons' },
        flagBtn('mark', 0, card.is_marked ? '★' : '☆', card.is_marked ? 'Unmark' : 'Mark'),
        flagBtn('flag', 1, 'R', 'Red'),
        flagBtn('flag', 2, 'O', 'Orange'),
        flagBtn('flag', 3, 'G', 'Green'),
      ),
    );

    return el('div', { class: 'remaining-bar', id: 'remaining-bar' }, summary, toolbar);
  }

  function renderCard(p: StudyState): HTMLElement {
    if (!p.card) return el('div');
    const article = el('article', { class: 'card', id: 'card-host' });
    // card.*_html comes sanitized server-side (scripts stripped).
    article.innerHTML = p.phase === 'back' ? p.card.answer_html : p.card.question_html;
    return article;
  }

  function renderPrimary(p: StudyState): HTMLElement {
    if (p.phase === 'front') {
      return el(
        'div',
        { class: 'actions-split' },
        el('a', { class: 'btn', href: '/' }, 'Back to decks'),
        el(
          'button',
          {
            type: 'button',
            class: 'btn btn-primary',
            'data-action': 'show-answer',
          },
          'Show answer',
        ),
      );
    }
    if (p.phase === 'back') {
      if (!p.card) return el('div');
      const card = p.card;
      const ratings = el('div', { class: 'ratings' });
      const labels = ['Again', 'Hard', 'Good', 'Easy'];
      const keys: Array<keyof StudyCardIntervals> = ['again', 'hard', 'good', 'easy'];
      for (let i = 0; i < 4; i++) {
        const rate = i + 1;
        const interval = (card.intervals && card.intervals[keys[i]]) || '—';
        ratings.appendChild(
          el(
            'button',
            {
              type: 'submit',
              name: 'rate',
              class: 'btn' + (rate === 3 ? ' btn-primary' : ''),
              'data-action': 'answer',
              'data-rate': String(rate),
            },
            labels[i],
            el('span', { class: 'rating-interval', text: ' (' + interval + ')' }),
          ),
        );
      }
      return el('form', { method: 'post', class: 'form', 'data-spa-form': 'answer' }, ratings);
    }
    return el('div');
  }

  // Undo button or null when undo is unavailable. Shared by the study and
  // done screens.
  function undoBtn(p: StudyState): HTMLElement | null {
    if (!p.undo || !p.undo.can_undo) return null;
    const label = p.undo.label ? 'Undo ' + p.undo.label.toLowerCase() : 'Undo';
    return el(
      'button',
      {
        type: 'button',
        class: 'btn',
        'data-action': 'undo',
        'aria-label': 'Undo',
      },
      label,
    );
  }

  function undoRow(p: StudyState): HTMLElement | null {
    const btn = undoBtn(p);
    if (!btn) return null;
    return el('div', { class: 'actions-row' }, btn);
  }

  // Deck-level actions (rebuild / empty) in their own row, or null for
  // non-filtered decks. Shared by the study and done screens.
  function deckActionsRow(p: StudyState): HTMLElement | null {
    if (!p.is_filtered) return null;
    const row = el('div', { class: 'actions-row' });
    row.appendChild(
      el(
        'button',
        {
          type: 'button',
          class: 'btn',
          'data-action': 'rebuild',
        },
        'Rebuild deck',
      ),
    );
    if (p.has_cards) {
      row.appendChild(
        el(
          'button',
          {
            type: 'button',
            class: 'btn',
            'data-action': 'empty',
          },
          'Empty deck',
        ),
      );
    }
    return row;
  }

  // Secondary actions split into dedicated rows: undo first, then deck
  // tools, then delete. Empty rows are not rendered.
  function renderSecondary(p: StudyState): HTMLElement {
    const wrap = el('div');
    const undo = undoRow(p);
    if (undo) wrap.appendChild(undo);
    const deck = deckActionsRow(p);
    if (deck) wrap.appendChild(deck);
    if (p.phase === 'back') {
      wrap.appendChild(
        el(
          'div',
          { class: 'actions-row' },
          el(
            'button',
            {
              type: 'button',
              class: 'btn',
              'data-action': 'delete',
            },
            'Delete note',
          ),
        ),
      );
    }
    return wrap;
  }

  function renderDone(p: StudyState): HTMLElement {
    const wrap = el('div');
    wrap.appendChild(el('h1', { text: 'Session complete' }));
    wrap.appendChild(el('p', { text: 'No more cards due in this deck right now.' }));
    wrap.appendChild(el('p', null, el('a', { class: 'btn', href: '/' }, 'Back to decks')));

    const undo = undoRow(p);
    if (undo) wrap.appendChild(undo);

    if (p.is_filtered) {
      const deck = deckActionsRow(p);
      if (deck) wrap.appendChild(deck);
    }
    return wrap;
  }

  function setMarkOptimistic(isMarked: boolean): void {
    const btn = ctx.root.querySelector('[data-action="mark"]');
    if (!btn) return;
    if (isMarked) {
      btn.className = btn.className.replace(/\s*flag-btn-on/, '') + ' flag-btn-on';
    } else {
      btn.className = btn.className.replace(/\s*flag-btn-on/, '');
    }
    btn.textContent = isMarked ? '★' : '☆';
    btn.setAttribute('aria-label', isMarked ? 'Unmark' : 'Mark');
  }

  function setFlagOptimistic(flag: number): void {
    const btns = ctx.root.querySelectorAll('[data-action="flag"]');
    for (let i = 0; i < btns.length; i++) {
      const b = btns[i] as HTMLElement;
      const v = Number(b.getAttribute('data-flag'));
      if (v === flag) {
        if (b.className.indexOf('flag-btn-on') === -1) b.className += ' flag-btn-on';
      } else {
        b.className = b.className.replace(/\s*flag-btn-on/, '');
      }
    }
  }

  function renderDeleteConfirm(_p: StudyState): HTMLElement {
    const card = ctx.confirmCard;
    if (!card) {
      // Defensive: uiMode is 'delete-confirm' but no card stored. Should
      // never happen in practice; render an empty placeholder so the user
      // isn't stuck on a broken screen.
      return el('div');
    }
    const preview = el('article', { class: 'card' });
    // card.question_html comes sanitized server-side (scripts stripped).
    preview.innerHTML = card.question_html;
    return el(
      'div',
      null,
      el('h1', { text: 'Delete this note?' }),
      el('p', {
        class: 'muted',
        text: 'This will permanently remove the note and all its cards. You can undo this with the Undo button afterwards.',
      }),
      preview,
      el(
        'div',
        { class: 'actions-row' },
        el(
          'button',
          {
            type: 'button',
            class: 'btn',
            'data-action': 'cancel-delete',
          },
          'Cancel',
        ),
        el(
          'button',
          {
            type: 'button',
            class: 'btn btn-primary',
            'data-action': 'confirm-delete',
          },
          'Confirm',
        ),
      ),
    );
  }

  function render(p: StudyState): void {
    ctx.state = p;
    clear(ctx.root);
    if (p.error_msg) {
      ctx.root.appendChild(el('p', { class: 'error', text: p.error_msg }));
    }
    if (ctx.uiMode === 'delete-confirm') {
      ctx.root.appendChild(renderDeleteConfirm(p));
    } else if (p.phase === 'done') {
      ctx.root.appendChild(renderDone(p));
    } else {
      ctx.root.appendChild(renderBar(p));
      ctx.root.appendChild(renderCard(p));
      ctx.root.appendChild(renderPrimary(p));
      ctx.root.appendChild(renderSecondary(p));
    }
    if (history.replaceState) {
      try {
        history.replaceState(null, '', ctx.base);
      } catch (e) {
        /* ignore */
      }
    }
    updateSyncIndicator(p.sync_required);
    scrollAndFocus(ctx.root);
  }

  return {
    render,
    updateSyncIndicator,
    setMarkOptimistic,
    setFlagOptimistic,
  };
}
