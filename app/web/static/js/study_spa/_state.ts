// Mutable SPA context shared across modules. All fields are mutated
// in place; the binding is created once by createCtx and never reassigned.

import type { StudyCard, StudySpaConfig, StudyState } from './_types';

// Client-only UI mode, independent of the server's `phase`. Drives what
// `render` draws: the regular study UI, or a full-page replacement that
// asks the user to confirm deletion of a note. Switches back to 'study'
// on cancel/confirm/network-error.
export type SpaUiMode = 'study' | 'delete-confirm';

export interface SpaCtx {
  root: HTMLElement;
  cfg: StudySpaConfig;
  base: string;
  state: StudyState | null;
  busy: boolean;
  inflight: AbortController | null;
  confirmCard: StudyCard | null;
  uiMode: SpaUiMode;
}

export function createCtx(root: HTMLElement, cfg: StudySpaConfig): SpaCtx {
  return {
    root,
    cfg,
    base: '/deck/' + cfg.deckId + '/spa',
    state: null,
    busy: false,
    inflight: null,
    confirmCard: null,
    uiMode: 'study',
  };
}
