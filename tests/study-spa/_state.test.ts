import { describe, expect, it } from 'vitest';
import { createCtx } from '@spa/_state';
import { makeCfg, makeRoot } from './helpers';

describe('createCtx', () => {
  it('builds the base path from deckId', () => {
    const root = makeRoot();
    const ctx = createCtx(root, makeCfg({ deckId: 7 }));
    expect(ctx.base).toBe('/deck/7/spa');
  });

  it('wires root and cfg through unchanged', () => {
    const root = makeRoot();
    const cfg = makeCfg({ csrfToken: 'abc', isFiltered: true });
    const ctx = createCtx(root, cfg);
    expect(ctx.root).toBe(root);
    expect(ctx.cfg).toBe(cfg);
  });

  it('initialises all stateful fields to their empty values', () => {
    const ctx = createCtx(makeRoot(), makeCfg());
    expect(ctx.state).toBeNull();
    expect(ctx.busy).toBe(false);
    expect(ctx.inflight).toBeNull();
    expect(ctx.confirmCard).toBeNull();
    expect(ctx.uiMode).toBe('study');
  });

  it('allows mutating the returned object in place', () => {
    const ctx = createCtx(makeRoot(), makeCfg());
    ctx.busy = true;
    ctx.state = { phase: 'front' } as never;
    ctx.uiMode = 'delete-confirm';
    expect(ctx.busy).toBe(true);
    expect(ctx.state).not.toBeNull();
    expect(ctx.uiMode).toBe('delete-confirm');
  });
});
