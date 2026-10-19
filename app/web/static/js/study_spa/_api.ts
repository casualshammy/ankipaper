// API client. `createApi` closes over cfg (for CSRF) and ctx (for inflight
// tracking). Returns the standard `api<T>` function plus `errorMessage`
// for decoding StudyApiError to a user-facing string.

import { showFatal } from './_dom';
import type { SpaCtx } from './_state';
import type { StudyApiError } from './_types';

export function createApi(ctx: SpaCtx) {
  return function api<T>(
    method: string,
    path: string,
    formData: FormData | null = null,
  ): Promise<T> {
    const ctrl = new AbortController();
    if (ctx.inflight) {
      try {
        ctx.inflight.abort();
      } catch (e) {
        /* ignore */
      }
    }
    ctx.inflight = ctrl;
    const opts: RequestInit = {
      method,
      signal: ctrl.signal,
      headers: {
        'X-CSRF-Token': ctx.cfg.csrfToken,
        'X-Requested-With': 'study-spa',
      },
      credentials: 'same-origin',
    };
    if (formData) opts.body = formData;
    return fetch(path, opts).then((r) => {
      if (r.status === 401) {
        location.assign('/login');
        throw { message: 'unauth' };
      }
      if (r.status === 403) {
        showFatal(ctx.root, 'Session expired — reloading…');
        setTimeout(() => {
          location.reload();
        }, 1500);
        throw { message: 'csrf' };
      }
      if (r.status === 404) {
        location.assign('/deck/' + ctx.cfg.deckId + '/study');
        throw { message: 'spa-disabled' };
      }
      if (!r.ok) {
        return r
          .json()
          .catch(() => ({}) as object)
          .then((body) => {
            throw {
              message: body && body.error_msg ? body.error_msg : 'HTTP ' + r.status,
              code: r.status,
              body,
            };
          });
      }
      return r.json();
    });
  };
}

export function errorMessage(e: StudyApiError): string | null {
  return 'body' in e && e.body && e.body.error_msg ? e.body.error_msg : null;
}
