// Delegated click handler. Single listener on ctx.root — every action
// button inside the SPA is identified by `data-action`, so we never
// bind/unbind handlers as the DOM is re-rendered.

import { closestEl } from './_dom';
import type { SpaCtx } from './_state';
import type { SpaActions } from './_actions';

export function attachEvents(ctx: SpaCtx, actions: SpaActions): void {
  ctx.root.addEventListener('click', function (ev) {
    const target = ev.target;
    const form = closestEl(target, 'form[data-spa-form="answer"]');
    if (form) {
      const btn = closestEl(target, 'button[data-action="answer"]');
      if (btn) {
        ev.preventDefault();
        const rate = Number(btn.getAttribute('data-rate'));
        actions.withBusy(() => actions.onAnswer(rate));
        return;
      }
    }
    const btn2 = closestEl(target, '[data-action]');
    if (!btn2) return;
    const a = btn2.getAttribute('data-action');
    if (a === 'cancel-delete') {
      if (ctx.busy) return;
      actions.withBusy(actions.onCancelDelete);
      return;
    }
    if (a === 'confirm-delete') {
      if (ctx.busy) return;
      actions.withBusy(actions.onDeleteConfirm);
      return;
    }
    if (ctx.busy) return;
    actions.withBusy(() => {
      switch (a) {
        case 'show-answer':
          return actions.onReveal();
        case 'undo':
          return actions.onUndo();
        case 'mark':
          return actions.onMark();
        case 'flag':
          return actions.onFlag(Number(btn2.getAttribute('data-flag')));
        case 'delete':
          if (ctx.state && ctx.state.card) actions.showDeleteConfirm(ctx.state.card);
          return;
        case 'rebuild':
          return actions.onRebuild();
        case 'empty':
          return actions.onEmpty();
      }
      return;
    });
  });
}
