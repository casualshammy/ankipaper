// Test helpers for the study SPA suite. Builds a real SpaCtx against a
// happy-dom document and exposes small fakes for the api / render hooks
// that other modules depend on.

import { vi } from 'vitest';
import { createCtx } from '@spa/_state';
import type { SpaCtx } from '@spa/_state';
import type { StudyCard, StudySpaConfig, StudyState } from '@spa/_types';

export function makeCfg(overrides: Partial<StudySpaConfig> = {}): StudySpaConfig {
  return {
    deckId: 42,
    isFiltered: false,
    csrfToken: 'csrf-test',
    ...overrides,
  };
}

export function makeRoot(): HTMLElement {
  const root = document.createElement('div');
  root.id = 'study-root';
  document.body.appendChild(root);
  return root;
}

export function makeCtx(overrides: Partial<StudySpaConfig> = {}): SpaCtx {
  const ctx = createCtx(makeRoot(), makeCfg(overrides));
  return ctx;
}

export function makeCard(overrides: Partial<StudyCard> = {}): StudyCard {
  return {
    card_id: 1001,
    deck_id: 42,
    question_html: '<p>front</p>',
    answer_html: '<p>back</p>',
    is_cloze: false,
    note_type_name: 'Basic',
    flag: 0,
    is_marked: false,
    intervals: { again: '1m', hard: '6m', good: '10m', easy: '4d' },
    ...overrides,
  };
}

export function makeState(overrides: Partial<StudyState> = {}): StudyState {
  return {
    phase: 'front',
    deck_id: 42,
    is_filtered: false,
    has_cards: true,
    undo: { label: null, can_undo: false, can_redo: false },
    error_msg: '',
    card: makeCard(),
    remaining_total: 10,
    remaining_new: 3,
    remaining_learn: 2,
    remaining_review: 5,
    card_type: 'review',
    sync_required: false,
    ...overrides,
  };
}

// Resolves all microtasks and any queued microtasks added during them.
// Useful after kicking off an action() that returns a promise to wait
// for the api<T>() call to fire and settle.
export async function flushMicrotasks(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

// `createActions` takes the api as an injection point, so tests can pass
// a vi.fn that returns whatever the test wants. `errorMessage` is also
// injected but defaults to the real one.
export type ApiMock = ReturnType<typeof vi.fn>;

export function makeApiMock(impl?: ApiMock): ApiMock {
  return impl ?? vi.fn(() => Promise.resolve({} as unknown));
}
