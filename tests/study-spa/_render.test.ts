import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRender } from '@spa/_render';
import { makeCard, makeCtx, makeState, makeUndo } from './helpers';

describe('createRender', () => {
  describe('updateSyncIndicator', () => {
    afterEach(() => {
      const ind = document.getElementById('sync-indicator');
      if (ind && ind.parentNode) ind.parentNode.removeChild(ind);
    });

    it('appends " ●" when syncRequired is true', () => {
      const ind = document.createElement('span');
      ind.id = 'sync-indicator';
      document.body.appendChild(ind);
      const ctx = makeCtx();
      const { updateSyncIndicator } = createRender(ctx);
      updateSyncIndicator(true);
      expect(ind.textContent).toBe(' ●');
    });

    it('clears the text when syncRequired is false', () => {
      const ind = document.createElement('span');
      ind.id = 'sync-indicator';
      ind.textContent = ' ●';
      document.body.appendChild(ind);
      const ctx = makeCtx();
      const { updateSyncIndicator } = createRender(ctx);
      updateSyncIndicator(false);
      expect(ind.textContent).toBe('');
    });

    it('no-ops if the indicator is not in the DOM', () => {
      const ctx = makeCtx();
      const { updateSyncIndicator } = createRender(ctx);
      expect(() => updateSyncIndicator(true)).not.toThrow();
    });
  });

  describe('setMarkOptimistic', () => {
    it('toggles the flag-btn-on class, text, and aria-label', () => {
      const ctx = makeCtx();
      const { setMarkOptimistic } = createRender(ctx);
      const btn = document.createElement('button');
      btn.setAttribute('data-action', 'mark');
      ctx.root.appendChild(btn);

      setMarkOptimistic(true);
      expect(btn.classList.contains('flag-btn-on')).toBe(true);
      expect(btn.textContent).toBe('★');
      expect(btn.getAttribute('aria-label')).toBe('Unmark');

      setMarkOptimistic(false);
      expect(btn.classList.contains('flag-btn-on')).toBe(false);
      expect(btn.textContent).toBe('☆');
      expect(btn.getAttribute('aria-label')).toBe('Mark');
    });

    it('no-ops if the mark button is not present', () => {
      const ctx = makeCtx();
      const { setMarkOptimistic } = createRender(ctx);
      expect(() => setMarkOptimistic(true)).not.toThrow();
    });
  });

  describe('setFlagOptimistic', () => {
    function addFlagButtons(ctx: { root: HTMLElement }, flags: number[]): void {
      for (const f of flags) {
        const b = document.createElement('button');
        b.setAttribute('data-action', 'flag');
        b.setAttribute('data-flag', String(f));
        ctx.root.appendChild(b);
      }
    }

    it('adds flag-btn-on to the matching flag, removes from others', () => {
      const ctx = makeCtx();
      const { setFlagOptimistic } = createRender(ctx);
      addFlagButtons(ctx, [1, 2, 3]);
      const btns = ctx.root.querySelectorAll<HTMLElement>('[data-action="flag"]');

      setFlagOptimistic(2);
      expect(btns[0].classList.contains('flag-btn-on')).toBe(false);
      expect(btns[1].classList.contains('flag-btn-on')).toBe(true);
      expect(btns[2].classList.contains('flag-btn-on')).toBe(false);
    });

    it('clears all when called with 0', () => {
      const ctx = makeCtx();
      const { setFlagOptimistic } = createRender(ctx);
      addFlagButtons(ctx, [1, 2, 3]);
      const btns = ctx.root.querySelectorAll<HTMLElement>('[data-action="flag"]');
      // Pre-mark all of them
      for (const b of btns) b.className = 'flag-btn flag-btn-on';

      setFlagOptimistic(0);
      for (const b of btns) {
        expect(b.classList.contains('flag-btn-on')).toBe(false);
      }
    });
  });

  describe('render', () => {
    let originalScrollTo: typeof window.scrollTo;
    let originalReplaceState: typeof history.replaceState;

    beforeEach(() => {
      originalScrollTo = window.scrollTo;
      originalReplaceState = history.replaceState;
      window.scrollTo = vi.fn();
      history.replaceState = vi.fn();
    });

    afterEach(() => {
      window.scrollTo = originalScrollTo;
      history.replaceState = originalReplaceState;
    });

    it('clears the root and re-populates it with the bar + card + actions on front', () => {
      const ctx = makeCtx();
      const { render } = createRender(ctx);
      // Seed the root with junk that should be cleared.
      ctx.root.appendChild(document.createElement('span'));
      const state = makeState({ phase: 'front' });
      render(state);
      expect(ctx.root.querySelector('#remaining-bar')).not.toBeNull();
      expect(ctx.root.querySelector('#card-host')).not.toBeNull();
      expect(ctx.root.querySelector('[data-action="show-answer"]')).not.toBeNull();
    });

    it('renders the rating buttons on the back', () => {
      const ctx = makeCtx();
      const { render } = createRender(ctx);
      render(makeState({ phase: 'back' }));
      const ratings = ctx.root.querySelectorAll<HTMLElement>('[data-action="answer"]');
      expect(ratings.length).toBe(4);
      expect(ratings[0].getAttribute('data-rate')).toBe('1');
      expect(ratings[3].getAttribute('data-rate')).toBe('4');
    });

    it('shows the "Delete note" button only on back', () => {
      const ctx = makeCtx();
      const { render } = createRender(ctx);
      render(makeState({ phase: 'front' }));
      expect(ctx.root.querySelector('[data-action="delete"]')).toBeNull();
      render(makeState({ phase: 'back' }));
      expect(ctx.root.querySelector('[data-action="delete"]')).not.toBeNull();
    });

    it('skips the bar on the done phase', () => {
      const ctx = makeCtx();
      const { render } = createRender(ctx);
      render(makeState({ phase: 'done', card: null }));
      expect(ctx.root.querySelector('#remaining-bar')).toBeNull();
      expect(ctx.root.querySelector('h1')?.textContent).toBe('Session complete');
    });

    it('shows rebuild/empty buttons only for filtered decks', () => {
      const ctx = makeCtx();
      const { render } = createRender(ctx);
      render(makeState({ phase: 'front', is_filtered: true, has_cards: true }));
      expect(ctx.root.querySelector('[data-action="rebuild"]')).not.toBeNull();
      expect(ctx.root.querySelector('[data-action="empty"]')).not.toBeNull();

      render(makeState({ phase: 'front', is_filtered: false }));
      expect(ctx.root.querySelector('[data-action="rebuild"]')).toBeNull();
      expect(ctx.root.querySelector('[data-action="empty"]')).toBeNull();
    });

    it('splits secondary actions into rows: undo, deck tools, delete', () => {
      const ctx = makeCtx();
      const { render } = createRender(ctx);
      render(
        makeState({
          phase: 'back',
          is_filtered: true,
          has_cards: true,
          undo: makeUndo({ label: 'Answer card' }),
        }),
      );
      // setUndoState() patches the undo row through this anchor.
      expect(ctx.root.querySelector('#secondary-actions')).not.toBeNull();
      const rows = ctx.root.querySelectorAll<HTMLElement>('.actions-row');
      expect(rows.length).toBe(3);
      expect(rows[0].querySelector('[data-action="undo"]')).not.toBeNull();
      expect(rows[1].querySelector('[data-action="rebuild"]')).not.toBeNull();
      expect(rows[1].querySelector('[data-action="empty"]')).not.toBeNull();
      expect(rows[2].querySelector('[data-action="delete"]')).not.toBeNull();
    });

    it('renders no empty secondary rows when conditions are off', () => {
      const ctx = makeCtx();
      const { render } = createRender(ctx);
      render(makeState({ phase: 'front', is_filtered: false }));
      expect(ctx.root.querySelectorAll('.actions-row').length).toBe(0);
    });

    it('renders a top error paragraph when error_msg is set', () => {
      const ctx = makeCtx();
      const { render } = createRender(ctx);
      render(makeState({ phase: 'front', error_msg: 'something went wrong' }));
      const first = ctx.root.firstChild as HTMLElement;
      expect(first.className).toBe('error');
      expect(first.textContent).toBe('something went wrong');
    });

    it('replaces history state with the deck base path', () => {
      const ctx = makeCtx({ deckId: 5 });
      const { render } = createRender(ctx);
      render(makeState());
      expect(history.replaceState).toHaveBeenCalledWith(null, '', '/deck/5/spa');
    });
  });

  describe('setUndoState', () => {
    let originalScrollTo: typeof window.scrollTo;
    let originalReplaceState: typeof history.replaceState;

    beforeEach(() => {
      originalScrollTo = window.scrollTo;
      originalReplaceState = history.replaceState;
      window.scrollTo = vi.fn();
      history.replaceState = vi.fn();
    });

    afterEach(() => {
      window.scrollTo = originalScrollTo;
      history.replaceState = originalReplaceState;
    });

    it('updates the label of the existing undo button and syncs ctx.state.undo', () => {
      const ctx = makeCtx();
      const { render, setUndoState } = createRender(ctx);
      render(makeState({ phase: 'back', undo: makeUndo({ label: 'Answer card' }) }));

      const next = makeUndo({ label: 'Mark note' });
      setUndoState(next);

      const btn = ctx.root.querySelector<HTMLElement>('[data-action="undo"]');
      expect(btn?.textContent).toBe("Undo 'mark note'");
      expect(ctx.state?.undo).toBe(next);
    });

    it('renders a bare "Undo" when the label is null but can_undo is true', () => {
      const ctx = makeCtx();
      const { render, setUndoState } = createRender(ctx);
      render(makeState({ phase: 'back', undo: makeUndo({ label: 'Answer card' }) }));

      setUndoState(makeUndo({ label: null, can_undo: true }));

      const btn = ctx.root.querySelector<HTMLElement>('[data-action="undo"]');
      expect(btn?.textContent).toBe('Undo');
    });

    it('inserts the undo row first when there was none', () => {
      const ctx = makeCtx();
      const { render, setUndoState } = createRender(ctx);
      render(
        makeState({
          phase: 'back',
          is_filtered: true,
          has_cards: true,
          undo: makeUndo({ label: null, can_undo: false }),
        }),
      );
      expect(ctx.root.querySelector('[data-action="undo"]')).toBeNull();

      setUndoState(makeUndo({ label: 'Set flag' }));

      const rows = ctx.root.querySelectorAll<HTMLElement>('.actions-row');
      expect(rows.length).toBe(3);
      const undoBtnEl = rows[0].querySelector<HTMLElement>('[data-action="undo"]');
      expect(undoBtnEl?.textContent).toBe("Undo 'set flag'");
      // Same construction site as the render path (makeUndoBtn): pin the
      // attributes the delegated click handler and a11y rely on.
      expect(undoBtnEl?.className).toBe('btn');
      expect(undoBtnEl?.getAttribute('aria-label')).toBe('Undo');
      expect(undoBtnEl?.getAttribute('type')).toBe('button');
      // Deck tools and delete stay in their original order after it.
      expect(rows[1].querySelector('[data-action="rebuild"]')).not.toBeNull();
      expect(rows[2].querySelector('[data-action="delete"]')).not.toBeNull();
    });

    it('removes the undo row when can_undo becomes false (defensive branch)', () => {
      const ctx = makeCtx();
      const { render, setUndoState } = createRender(ctx);
      render(makeState({ phase: 'back', undo: makeUndo({ label: 'Answer card' }) }));
      expect(ctx.root.querySelector('[data-action="undo"]')).not.toBeNull();
      // Back phase: undo row + delete row.
      expect(ctx.root.querySelectorAll('.actions-row').length).toBe(2);

      setUndoState(makeUndo({ label: null, can_undo: false }));

      expect(ctx.root.querySelector('[data-action="undo"]')).toBeNull();
      // No orphan empty .actions-row: only the delete row remains.
      const rows = ctx.root.querySelectorAll<HTMLElement>('.actions-row');
      expect(rows.length).toBe(1);
      expect(rows[0].querySelector('[data-action="delete"]')).not.toBeNull();
      expect(ctx.state?.undo.can_undo).toBe(false);
    });

    it('still syncs ctx.state.undo when #secondary-actions is absent (done screen)', () => {
      const ctx = makeCtx();
      const { render, setUndoState } = createRender(ctx);
      render(makeState({ phase: 'done', card: null }));
      expect(ctx.root.querySelector('#secondary-actions')).toBeNull();

      const next = makeUndo({ label: 'Mark note' });
      expect(() => setUndoState(next)).not.toThrow();
      expect(ctx.state?.undo).toBe(next);
    });
  });

  describe('renderDeleteConfirm (via render with uiMode)', () => {
    it('replaces the study screen with a full-page delete confirmation when uiMode is delete-confirm', () => {
      const ctx = makeCtx();
      ctx.confirmCard = makeCard({ card_id: 7, question_html: '<p>front of card</p>' });
      ctx.uiMode = 'delete-confirm';
      const { render } = createRender(ctx);
      render(makeState({ phase: 'front' }));

      // Header and copy
      expect(ctx.root.querySelector('h1')?.textContent).toBe('Delete this note?');
      const desc = ctx.root.querySelector('p.muted')?.textContent || '';
      expect(desc).toMatch(/permanently remove the note/);
      expect(desc).toMatch(/Undo button afterwards/);

      // Card preview uses the confirm card's question_html
      const preview = ctx.root.querySelector('article.card');
      expect(preview).not.toBeNull();
      expect(preview?.innerHTML).toBe('<p>front of card</p>');

      // Cancel and Confirm buttons
      const cancel = ctx.root.querySelector('[data-action="cancel-delete"]');
      const confirm = ctx.root.querySelector('[data-action="confirm-delete"]');
      expect(cancel?.textContent).toBe('Cancel');
      expect(confirm?.textContent).toBe('Confirm');
      expect(confirm?.classList.contains('btn-primary')).toBe(true);

      // No study UI left over
      expect(ctx.root.querySelector('#remaining-bar')).toBeNull();
      expect(ctx.root.querySelector('#card-host')).toBeNull();
      expect(ctx.root.querySelector('[data-action="show-answer"]')).toBeNull();
      expect(ctx.root.querySelector('[data-action="answer"]')).toBeNull();
    });

    it('takes precedence over phase=back (still renders delete UI, not ratings)', () => {
      const ctx = makeCtx();
      ctx.confirmCard = makeCard();
      ctx.uiMode = 'delete-confirm';
      const { render } = createRender(ctx);
      render(makeState({ phase: 'back' }));
      expect(ctx.root.querySelector('[data-action="answer"]')).toBeNull();
      expect(ctx.root.querySelector('h1')?.textContent).toBe('Delete this note?');
    });

    it('falls back to an empty placeholder if uiMode is delete-confirm but confirmCard is null', () => {
      const ctx = makeCtx();
      ctx.uiMode = 'delete-confirm';
      // intentionally not setting confirmCard
      const { render } = createRender(ctx);
      render(makeState({ phase: 'front' }));
      expect(ctx.root.querySelector('h1')).toBeNull();
    });
  });
});
