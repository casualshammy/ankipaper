import { describe, expect, it, vi } from 'vitest';
import { attachEvents } from '@spa/_events';
import { makeCard, makeCtx, makeState } from './helpers';

function buildActions() {
  return {
    withBusy: (fn: () => void | Promise<unknown>) => {
      const p = Promise.resolve().then(fn);
      return p.then(() => undefined);
    },
    onReveal: vi.fn(() => Promise.resolve()),
    onAnswer: vi.fn(() => Promise.resolve()),
    onUndo: vi.fn(() => Promise.resolve()),
    onMark: vi.fn(() => Promise.resolve()),
    onFlag: vi.fn(() => Promise.resolve()),
    showDeleteConfirm: vi.fn(),
    onCancelDelete: vi.fn(),
    onDeleteConfirm: vi.fn(() => Promise.resolve()),
    onRebuild: vi.fn(() => Promise.resolve()),
    onEmpty: vi.fn(() => Promise.resolve()),
  };
}

function click(el: Element): void {
  el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
}

describe('attachEvents', () => {
  it('routes clicks on [data-action] to the right action', async () => {
    const ctx = makeCtx();
    const actions = buildActions();
    attachEvents(ctx, actions);

    const btn = document.createElement('button');
    btn.setAttribute('data-action', 'mark');
    ctx.root.appendChild(btn);
    click(btn);
    await Promise.resolve();
    expect(actions.onMark).toHaveBeenCalled();
  });

  it('reads the rate from data-rate for answer buttons inside the form', async () => {
    const ctx = makeCtx();
    const actions = buildActions();
    attachEvents(ctx, actions);

    const form = document.createElement('form');
    form.setAttribute('data-spa-form', 'answer');
    const btn = document.createElement('button');
    btn.setAttribute('data-action', 'answer');
    btn.setAttribute('data-rate', '2');
    form.appendChild(btn);
    ctx.root.appendChild(form);

    click(btn);
    await Promise.resolve();
    expect(actions.onAnswer).toHaveBeenCalledWith(2);
  });

  it('passes the data-flag value to onFlag', async () => {
    const ctx = makeCtx();
    const actions = buildActions();
    attachEvents(ctx, actions);

    const btn = document.createElement('button');
    btn.setAttribute('data-action', 'flag');
    btn.setAttribute('data-flag', '3');
    ctx.root.appendChild(btn);

    click(btn);
    await Promise.resolve();
    expect(actions.onFlag).toHaveBeenCalledWith(3);
  });

  it('routes cancel-delete to onCancelDelete', async () => {
    const ctx = makeCtx();
    const actions = buildActions();
    attachEvents(ctx, actions);

    const btn = document.createElement('button');
    btn.setAttribute('data-action', 'cancel-delete');
    ctx.root.appendChild(btn);

    click(btn);
    await Promise.resolve();
    expect(actions.onCancelDelete).toHaveBeenCalled();
  });

  it('routes confirm-delete to onDeleteConfirm', async () => {
    const ctx = makeCtx();
    const actions = buildActions();
    attachEvents(ctx, actions);

    const btn = document.createElement('button');
    btn.setAttribute('data-action', 'confirm-delete');
    ctx.root.appendChild(btn);

    click(btn);
    await Promise.resolve();
    expect(actions.onDeleteConfirm).toHaveBeenCalled();
  });

  it('routes delete to showDeleteConfirm with the current card', async () => {
    const ctx = makeCtx();
    const card = makeCard({ card_id: 33 });
    ctx.state = makeState({ card });
    const actions = buildActions();
    attachEvents(ctx, actions);

    const btn = document.createElement('button');
    btn.setAttribute('data-action', 'delete');
    ctx.root.appendChild(btn);

    click(btn);
    await Promise.resolve();
    expect(actions.showDeleteConfirm).toHaveBeenCalledWith(card);
  });

  it('does nothing for delete when there is no current card', async () => {
    const ctx = makeCtx();
    ctx.state = makeState({ card: null });
    const actions = buildActions();
    attachEvents(ctx, actions);

    const btn = document.createElement('button');
    btn.setAttribute('data-action', 'delete');
    ctx.root.appendChild(btn);

    click(btn);
    await Promise.resolve();
    expect(actions.showDeleteConfirm).not.toHaveBeenCalled();
  });

  it('routes show-answer to onReveal', async () => {
    const ctx = makeCtx();
    const actions = buildActions();
    attachEvents(ctx, actions);

    const btn = document.createElement('button');
    btn.setAttribute('data-action', 'show-answer');
    ctx.root.appendChild(btn);

    click(btn);
    await Promise.resolve();
    expect(actions.onReveal).toHaveBeenCalled();
  });

  it('routes rebuild to onRebuild and empty to onEmpty', async () => {
    const ctx = makeCtx();
    const actions = buildActions();
    attachEvents(ctx, actions);

    for (const a of ['rebuild', 'empty', 'undo']) {
      const btn = document.createElement('button');
      btn.setAttribute('data-action', a);
      ctx.root.appendChild(btn);
      click(btn);
      await Promise.resolve();
    }
    expect(actions.onRebuild).toHaveBeenCalled();
    expect(actions.onEmpty).toHaveBeenCalled();
    expect(actions.onUndo).toHaveBeenCalled();
  });

  it('ignores clicks on elements without [data-action]', async () => {
    const ctx = makeCtx();
    const actions = buildActions();
    attachEvents(ctx, actions);

    const span = document.createElement('span');
    ctx.root.appendChild(span);
    click(span);
    await Promise.resolve();
    for (const k of Object.keys(actions) as Array<keyof typeof actions>) {
      if (k === 'withBusy' || k === 'showDeleteConfirm' || k === 'onCancelDelete') continue;
      const v = actions[k] as { mock?: { calls: unknown[] } } | ((...args: unknown[]) => unknown);
      if (v && typeof v === 'object' && 'mock' in v) {
        expect((v as { mock: { calls: unknown[] } }).mock.calls.length).toBe(0);
      }
    }
  });

  it('blocks new actions while ctx.busy is true (re-entrancy guard)', async () => {
    const ctx = makeCtx();
    ctx.busy = true;
    const actions = buildActions();
    attachEvents(ctx, actions);

    const btn = document.createElement('button');
    btn.setAttribute('data-action', 'mark');
    ctx.root.appendChild(btn);

    click(btn);
    await Promise.resolve();
    expect(actions.onMark).not.toHaveBeenCalled();
  });
});
