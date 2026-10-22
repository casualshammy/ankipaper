import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createActions } from '@spa/_actions';
import { makeApiMock, makeCard, makeCtx, makeState, makeUndo } from './helpers';

function makeActionDeps() {
  const ctx = makeCtx();
  const api = makeApiMock();
  const render = vi.fn();
  const setMarkOptimistic = vi.fn();
  const setFlagOptimistic = vi.fn();
  const setUndoState = vi.fn();
  const actions = createActions(ctx, api, render, setMarkOptimistic, setFlagOptimistic, setUndoState);
  return { ctx, api, render, setMarkOptimistic, setFlagOptimistic, setUndoState, actions };
}

describe('createActions', () => {
  describe('withBusy', () => {
    it('runs the fn and releases busy afterwards', async () => {
      const { ctx, actions } = makeActionDeps();
      const fn = vi.fn(() => Promise.resolve());
      await actions.withBusy(fn);
      expect(fn).toHaveBeenCalledTimes(1);
      expect(ctx.busy).toBe(false);
    });

    it('releases busy even if fn throws', async () => {
      const { ctx, actions } = makeActionDeps();
      const fn = vi.fn(() => Promise.reject(new Error('boom')));
      await expect(actions.withBusy(fn)).rejects.toThrow('boom');
      expect(ctx.busy).toBe(false);
    });

    it('skips the call when busy is already set', async () => {
      const { ctx, actions } = makeActionDeps();
      ctx.busy = true;
      const fn = vi.fn(() => Promise.resolve());
      await actions.withBusy(fn);
      expect(fn).not.toHaveBeenCalled();
    });
  });

  describe('onMark', () => {
    it('applies optimistic state, sends POST, and confirms with server response', async () => {
      const card = makeCard({ is_marked: false });
      const { ctx, api, actions, setMarkOptimistic, setUndoState, render } = makeActionDeps();
      ctx.state = makeState({ card });
      const undo = makeUndo({ label: 'Mark note' });
      api.mockResolvedValueOnce({ is_marked: true, undo });

      await actions.onMark();

      // First call: optimistic with the *target* (true)
      expect(setMarkOptimistic).toHaveBeenNthCalledWith(1, true);
      // Second call: confirm with server response
      expect(setMarkOptimistic).toHaveBeenNthCalledWith(2, true);
      expect(api).toHaveBeenCalledWith('POST', '/deck/42/spa/mark', expect.any(FormData));
      expect(card.is_marked).toBe(true);
      expect(setUndoState).toHaveBeenCalledWith(undo);
      expect(render).not.toHaveBeenCalled();
    });

    it('rolls back on api error', async () => {
      const card = makeCard({ is_marked: false });
      const { ctx, api, actions, setMarkOptimistic, setUndoState } = makeActionDeps();
      ctx.state = makeState({ card });
      api.mockRejectedValueOnce(new Error('network'));

      await actions.onMark();

      expect(setMarkOptimistic).toHaveBeenNthCalledWith(1, true);
      expect(setMarkOptimistic).toHaveBeenNthCalledWith(2, false);
      expect(card.is_marked).toBe(false);
      // Server state is unchanged on rollback: no undo patch.
      expect(setUndoState).not.toHaveBeenCalled();
    });

    it('tolerates an old-shape response without an undo block', async () => {
      const card = makeCard({ is_marked: false });
      const { ctx, api, actions, setUndoState } = makeActionDeps();
      ctx.state = makeState({ card });
      api.mockResolvedValueOnce({ is_marked: true });

      await expect(actions.onMark()).resolves.not.toThrow();

      expect(card.is_marked).toBe(true);
      expect(setUndoState).not.toHaveBeenCalled();
    });

    it('no-ops when there is no current card', async () => {
      const { ctx, api, actions } = makeActionDeps();
      ctx.state = makeState({ card: null });
      await actions.onMark();
      expect(api).not.toHaveBeenCalled();
    });
  });

  describe('onFlag', () => {
    it('toggles a clicked flag (sends 0 to clear)', async () => {
      const card = makeCard({ flag: 0 });
      const { ctx, api, actions, setFlagOptimistic, setUndoState } = makeActionDeps();
      ctx.state = makeState({ card });
      const undo = makeUndo({ label: 'Set flag' });
      api.mockResolvedValueOnce({ flag: 2, undo });

      await actions.onFlag(2);

      expect(setFlagOptimistic).toHaveBeenNthCalledWith(1, 2);
      const body = (api.mock.calls[0][2] as FormData);
      expect(body.get('flag')).toBe('2');
      expect(card.flag).toBe(2);
      expect(setUndoState).toHaveBeenCalledWith(undo);
    });

    it('clicking the same flag clears it (sends 0)', async () => {
      const card = makeCard({ flag: 2 });
      const { ctx, api, actions } = makeActionDeps();
      ctx.state = makeState({ card });
      api.mockResolvedValueOnce({ flag: 0, undo: makeUndo({ label: 'Clear flag' }) });

      await actions.onFlag(2);

      const body = api.mock.calls[0][2] as FormData;
      expect(body.get('flag')).toBe('0');
      expect(card.flag).toBe(0);
    });

    it('rolls back on api error', async () => {
      const card = makeCard({ flag: 0 });
      const { ctx, api, actions, setFlagOptimistic, setUndoState } = makeActionDeps();
      ctx.state = makeState({ card });
      api.mockRejectedValueOnce(new Error('fail'));

      await actions.onFlag(1);

      expect(setFlagOptimistic).toHaveBeenNthCalledWith(1, 1);
      expect(setFlagOptimistic).toHaveBeenNthCalledWith(2, 0);
      expect(card.flag).toBe(0);
      // Server state is unchanged on rollback: no undo patch.
      expect(setUndoState).not.toHaveBeenCalled();
    });
  });

  describe('onAnswer', () => {
    it('POSTs rate and re-renders the returned state', async () => {
      const { ctx, api, actions, render } = makeActionDeps();
      const newState = makeState({ phase: 'front' });
      ctx.state = makeState({ card: makeCard() });
      api.mockResolvedValueOnce(newState);

      await actions.onAnswer(3);

      expect(api).toHaveBeenCalledWith('POST', '/deck/42/spa/answer', expect.any(FormData));
      const body = api.mock.calls[0][2] as FormData;
      expect(body.get('rate')).toBe('3');
      expect(render).toHaveBeenCalledWith(newState);
    });
  });

  describe('onUndo', () => {
    it('POSTs to /undo and re-renders', async () => {
      const { api, actions, render } = makeActionDeps();
      const newState = makeState();
      api.mockResolvedValueOnce(newState);
      await actions.onUndo();
      expect(api).toHaveBeenCalledWith('POST', '/deck/42/spa/undo', null);
      expect(render).toHaveBeenCalledWith(newState);
    });
  });

  describe('onReveal', () => {
    it('POSTs to /reveal with the current card_id and re-renders', async () => {
      const { ctx, api, actions, render } = makeActionDeps();
      const card = makeCard({ card_id: 17 });
      ctx.state = makeState({ card });
      api.mockResolvedValueOnce(makeState({ phase: 'back' }));
      await actions.onReveal();
      const body = api.mock.calls[0][2] as FormData;
      expect(body.get('card_id')).toBe('17');
      expect(render).toHaveBeenCalled();
    });

    it('no-ops when there is no current card', async () => {
      const { ctx, api, actions } = makeActionDeps();
      ctx.state = makeState({ card: null });
      await actions.onReveal();
      expect(api).not.toHaveBeenCalled();
    });
  });

  describe('showDeleteConfirm', () => {
    it('sets uiMode to delete-confirm, stores the card, and re-renders the current state', () => {
      const { ctx, actions, render } = makeActionDeps();
      const card = makeCard({ card_id: 5 });
      ctx.state = makeState();
      actions.showDeleteConfirm(card);
      expect(ctx.uiMode).toBe('delete-confirm');
      expect(ctx.confirmCard).toBe(card);
      expect(render).toHaveBeenCalledWith(ctx.state);
    });

    it('does not re-render when state is null (e.g. before initial /state fetch)', () => {
      const { ctx, actions, render } = makeActionDeps();
      ctx.state = null;
      actions.showDeleteConfirm(makeCard());
      expect(ctx.uiMode).toBe('delete-confirm');
      expect(render).not.toHaveBeenCalled();
    });
  });

  describe('onDeleteConfirm', () => {
    it('resets uiMode to study, POSTs /delete with the confirm card id, and renders the new state', async () => {
      const { ctx, api, actions, render } = makeActionDeps();
      ctx.confirmCard = makeCard({ card_id: 9 });
      ctx.uiMode = 'delete-confirm';
      api.mockResolvedValueOnce(makeState({ phase: 'done' }));

      await actions.onDeleteConfirm();

      expect(ctx.uiMode).toBe('study');
      expect(ctx.confirmCard).toBeNull();
      const body = api.mock.calls[0][2] as FormData;
      expect(body.get('card_id')).toBe('9');
      expect(render).toHaveBeenCalled();
    });

    it('no-ops when there is no confirmCard', async () => {
      const { api, actions } = makeActionDeps();
      await actions.onDeleteConfirm();
      expect(api).not.toHaveBeenCalled();
    });

    it('on api error the showError hook receives the error_msg', async () => {
      const showErrorFn = vi.fn();
      const { ctx, api } = makeActionDeps();
      ctx.confirmCard = makeCard({ card_id: 9 });
      api.mockRejectedValueOnce({
        message: 'HTTP 500',
        code: 500,
        body: { error_msg: 'cannot delete right now' },
      });
      // Re-create with the showError override.
      const a2 = createActions(ctx, api, vi.fn(), vi.fn(), vi.fn(), vi.fn(), showErrorFn);
      await a2.onDeleteConfirm();
      expect(showErrorFn).toHaveBeenCalledWith('cannot delete right now');
    });
  });

  describe('onCancelDelete', () => {
    it('resets uiMode to study, clears confirmCard, and re-renders the current state', () => {
      const { ctx, actions, render } = makeActionDeps();
      ctx.uiMode = 'delete-confirm';
      ctx.confirmCard = makeCard();
      ctx.state = makeState();
      actions.onCancelDelete();
      expect(ctx.uiMode).toBe('study');
      expect(ctx.confirmCard).toBeNull();
      expect(render).toHaveBeenCalledWith(ctx.state);
    });
  });

  describe('onRebuild / onEmpty', () => {
    let originalLocation: Location;
    beforeEach(() => {
      originalLocation = globalThis.location;
      Object.defineProperty(globalThis, 'location', {
        value: { ...originalLocation, assign: vi.fn() },
        writable: true,
        configurable: true,
      });
    });
    afterEach(() => {
      Object.defineProperty(globalThis, 'location', {
        value: originalLocation,
        writable: true,
        configurable: true,
      });
    });

    it('onRebuild redirects to /?rebuild_ok=<count> on success', async () => {
      const { api, actions } = makeActionDeps();
      api.mockResolvedValueOnce({ ok: true, count: 5 });
      await actions.onRebuild();
      expect((globalThis.location as unknown as { assign: ReturnType<typeof vi.fn> }).assign).toHaveBeenCalledWith(
        '/?rebuild_ok=5',
      );
    });

    it('onRebuild shows error on failure', async () => {
      const showErrorFn = vi.fn();
      const { ctx, api } = makeActionDeps();
      const actions = createActions(ctx, api, vi.fn(), vi.fn(), vi.fn(), vi.fn(), showErrorFn);
      api.mockRejectedValueOnce({ body: { error_msg: 'busy' } });
      await actions.onRebuild();
      expect(showErrorFn).toHaveBeenCalledWith('busy');
    });

    it('onEmpty redirects to /?empty_ok=<count> on success', async () => {
      const { api, actions } = makeActionDeps();
      api.mockResolvedValueOnce({ ok: true, count: 12 });
      await actions.onEmpty();
      expect((globalThis.location as unknown as { assign: ReturnType<typeof vi.fn> }).assign).toHaveBeenCalledWith(
        '/?empty_ok=12',
      );
    });

    it('onEmpty falls back to "empty failed" when error has no error_msg', async () => {
      const showErrorFn = vi.fn();
      const { ctx, api } = makeActionDeps();
      const actions = createActions(ctx, api, vi.fn(), vi.fn(), vi.fn(), vi.fn(), showErrorFn);
      api.mockRejectedValueOnce({ body: {} });
      await actions.onEmpty();
      expect(showErrorFn).toHaveBeenCalledWith('empty failed');
    });
  });
});
