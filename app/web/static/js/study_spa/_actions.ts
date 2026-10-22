// Action handlers. Every state-changing operation lives here: the
// server calls, the optimistic mark/flag patches, the delete confirm.
// withBusy() prevents concurrent user actions from racing.

import { showError } from './_dom';
import { errorMessage } from './_api';
import type { SpaCtx } from './_state';
import type {
  StudyApiError,
  StudyCard,
  StudyFlagResponse,
  StudyMarkResponse,
  StudyOkResponse,
  StudyState,
  StudyUndo,
} from './_types';

type ApiFn = <T>(method: string, path: string, formData: FormData | null) => Promise<T>;
type RenderFn = (p: StudyState) => void;
type OptimisticFlag = (flag: number) => void;
type OptimisticMark = (isMarked: boolean) => void;
type SetUndoStateFn = (undo: StudyUndo) => void;
type ShowErrorFn = (msg: string) => void;
type ErrorMessageFn = (e: StudyApiError) => string | null;

export interface SpaActions {
  withBusy: (fn: () => void | Promise<unknown>) => Promise<void>;
  onReveal: () => Promise<void>;
  onAnswer: (rate: number) => Promise<void>;
  onUndo: () => Promise<void>;
  onMark: () => Promise<void>;
  onFlag: (clickedFlag: number) => Promise<void>;
  showDeleteConfirm: (card: StudyCard) => void;
  onCancelDelete: () => void;
  onDeleteConfirm: () => Promise<void>;
  onRebuild: () => Promise<void>;
  onEmpty: () => Promise<void>;
}

export function createActions(
  ctx: SpaCtx,
  api: ApiFn,
  render: RenderFn,
  setMarkOptimistic: OptimisticMark,
  setFlagOptimistic: OptimisticFlag,
  setUndoState: SetUndoStateFn,
  showErrorFn: ShowErrorFn = (m) => showError(ctx.root, m),
  errorMessageFn: ErrorMessageFn = errorMessage,
): SpaActions {
  function withBusy(fn: () => void | Promise<unknown>): Promise<void> {
    if (ctx.busy) return Promise.resolve();
    ctx.busy = true;
    return Promise.resolve()
      .then(fn)
      .then(
        () => {
          ctx.busy = false;
        },
        (e) => {
          ctx.busy = false;
          throw e;
        },
      );
  }

  function onReveal(): Promise<void> {
    if (!ctx.state || !ctx.state.card) return Promise.resolve();
    const card = ctx.state.card;
    const body = new FormData();
    body.append('card_id', String(card.card_id));
    return api<StudyState>('POST', ctx.base + '/reveal', body).then(render);
  }

  function onAnswer(rate: number): Promise<void> {
    if (!ctx.state || !ctx.state.card) return Promise.resolve();
    const card = ctx.state.card;
    const body = new FormData();
    body.append('card_id', String(card.card_id));
    body.append('rate', String(rate));
    return api<StudyState>('POST', ctx.base + '/answer', body).then(render);
  }

  function onUndo(): Promise<void> {
    return api<StudyState>('POST', ctx.base + '/undo', null).then(render);
  }

  function onMark(): Promise<void> {
    if (!ctx.state || !ctx.state.card) return Promise.resolve();
    const card = ctx.state.card;
    const target = !card.is_marked;
    setMarkOptimistic(target);
    const body = new FormData();
    body.append('card_id', String(card.card_id));
    body.append('marked', target ? 'true' : 'false');
    return api<StudyMarkResponse>('POST', ctx.base + '/mark', body).then(
      (r) => {
        card.is_marked = r.is_marked;
        setMarkOptimistic(r.is_marked);
        if (r.undo) 
          setUndoState(r.undo);
      },
      () => {
        setMarkOptimistic(!target);
      },
    );
  }

  function onFlag(clickedFlag: number): Promise<void> {
    if (!ctx.state || !ctx.state.card) return Promise.resolve();
    const card = ctx.state.card;
    const prev = card.flag;
    const target = prev === clickedFlag ? 0 : clickedFlag;
    setFlagOptimistic(target);
    const body = new FormData();
    body.append('card_id', String(card.card_id));
    body.append('flag', String(target));
    return api<StudyFlagResponse>('POST', ctx.base + '/flag', body).then(
      (r) => {
        card.flag = r.flag;
        setFlagOptimistic(r.flag);
        if (r.undo) 
          setUndoState(r.undo);
      },
      () => {
        setFlagOptimistic(prev);
      },
    );
  }

  // Switch the rendered screen from study to a full-page "are you sure?"
  // replacement. The card being confirmed lives in ctx.confirmCard so
  // render() and onDeleteConfirm() can find it without going through props.
  function showDeleteConfirm(card: StudyCard): void {
    ctx.confirmCard = card;
    ctx.uiMode = 'delete-confirm';
    if (ctx.state) render(ctx.state);
  }

  // User backed out — drop the confirm card and re-render the same study state.
  function onCancelDelete(): void {
    ctx.uiMode = 'study';
    ctx.confirmCard = null;
    if (ctx.state) render(ctx.state);
  }

  function onDeleteConfirm(): Promise<void> {
    if (!ctx.confirmCard)
      return Promise.resolve();

    const cardId = ctx.confirmCard.card_id;
    // Reset UI mode BEFORE the network call so that:
    // - on success, the new state is rendered as the study screen;
    // - on error, we're back on the study screen (the card was not deleted)
    //   and the user can retry or back out via the page refresh.
    ctx.uiMode = 'study';
    ctx.confirmCard = null;
    const body = new FormData();
    body.append('card_id', String(cardId));
    return api<StudyState>('POST', ctx.base + '/delete', body).then(render, (e) => {
      const msg = errorMessageFn(e);
      if (ctx.state)
        render(ctx.state);
      if (msg)
        showErrorFn(msg);
    });
  }

  function onRebuild(): Promise<void> {
    return api<StudyOkResponse>('POST', ctx.base + '/rebuild', null).then(
      (r) => {
        location.assign('/?rebuild_ok=' + r.count);
      },
      (e) => {
        const msg = errorMessageFn(e);
        showErrorFn(msg || 'rebuild failed');
      },
    );
  }

  function onEmpty(): Promise<void> {
    return api<StudyOkResponse>('POST', ctx.base + '/empty', null).then(
      (r) => {
        location.assign('/?empty_ok=' + r.count);
      },
      (e) => {
        const msg = errorMessageFn(e);
        showErrorFn(msg || 'empty failed');
      },
    );
  }

  return {
    withBusy,
    onReveal,
    onAnswer,
    onUndo,
    onMark,
    onFlag,
    showDeleteConfirm,
    onCancelDelete,
    onDeleteConfirm,
    onRebuild,
    onEmpty,
  };
}
