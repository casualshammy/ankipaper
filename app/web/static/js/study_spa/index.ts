// Study SPA entry point. Wires up ctx + api + render + actions, attaches
// the delegated event listener, and kicks off the first /state fetch.
// Source-of-truth: this file plus study_spa/_*.ts. esbuild bundles
// everything into a single IIFE in app/web/static/js/study_spa.js.

import { showFatal } from './_dom';
import { createApi } from './_api';
import { createCtx } from './_state';
import { createRender } from './_render';
import { createActions } from './_actions';
import { attachEvents } from './_events';
import type { StudyApiError, StudySpaConfig, StudyState } from './_types';

(function () {
  const configEl = document.getElementById('spa-config');
  if (!configEl) {
    console.error('SPA config element not found.');
    return;
  }
  let cfg: StudySpaConfig;
  try {
    cfg = JSON.parse(configEl.textContent);
  } catch (e) {
    console.error('Failed to parse SPA config:', e);
    return;
  }
  if (!cfg) {
    console.error('SPA config is empty or invalid.');
    return;
  }
  const root = document.getElementById('study-root');
  if (!root) {
    console.error('Study root element not found.');
    return;
  }

  const ctx = createCtx(root, cfg);
  const api = createApi(ctx);
  const renderApi = createRender(ctx);
  const actions = createActions(
    ctx,
    api,
    renderApi.render,
    renderApi.setMarkOptimistic,
    renderApi.setFlagOptimistic,
    renderApi.setUndoState,
  );
  attachEvents(ctx, actions);

  api<StudyState>('GET', ctx.base + '/state', null).then(renderApi.render, (e: StudyApiError) => {
    if (e && (e.message === 'unauth' || e.message === 'csrf' || e.message === 'spa-disabled')) return;
    showFatal(root, 'Failed to load. Please refresh.');
  });
})();
