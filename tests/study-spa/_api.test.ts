import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApi, errorMessage } from '@spa/_api';
import type { StudyApiError } from '@spa/_types';
import { flushMicrotasks, makeApiMock, makeCtx } from './helpers';

describe('errorMessage', () => {
  it('returns the body.error_msg from a bad error', () => {
    const e: StudyApiError = {
      message: 'HTTP 500',
      code: 500,
      body: { error_msg: 'database locked' },
    };
    expect(errorMessage(e)).toBe('database locked');
  });

  it('returns null when the body has no error_msg', () => {
    const e: StudyApiError = { message: 'HTTP 500', code: 500, body: {} };
    expect(errorMessage(e)).toBeNull();
  });

  it('returns null for an auth error (no body)', () => {
    const e: StudyApiError = { message: 'unauth' };
    expect(errorMessage(e)).toBeNull();
  });
});

describe('createApi', () => {
  let originalFetch: typeof fetch;
  let originalLocation: Location;
  let fetchMock: ReturnType<typeof vi.fn>;
  let assignMock: ReturnType<typeof vi.fn>;
  let reloadMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    originalLocation = globalThis.location;
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    assignMock = vi.fn();
    reloadMock = vi.fn();
    // happy-dom doesn't let us reassign location, so stub its methods.
    Object.defineProperty(globalThis, 'location', {
      value: { ...originalLocation, assign: assignMock, reload: reloadMock },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    Object.defineProperty(globalThis, 'location', {
      value: originalLocation,
      writable: true,
      configurable: true,
    });
  });

  it('returns parsed JSON on 2xx', async () => {
    fetchMock.mockResolvedValueOnce({ status: 200, ok: true, json: () => Promise.resolve({ x: 1 }) });
    const ctx = makeCtx();
    const api = createApi(ctx);
    const r = await api('GET', '/foo');
    expect(r).toEqual({ x: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, opts] = fetchMock.mock.calls[0];
    expect(path).toBe('/foo');
    expect(opts.method).toBe('GET');
    expect(opts.headers['X-CSRF-Token']).toBe('csrf-test');
    expect(opts.headers['X-Requested-With']).toBe('study-spa');
    expect(opts.credentials).toBe('same-origin');
  });

  it('sends CSRF token and the SPA marker header', async () => {
    fetchMock.mockResolvedValueOnce({ status: 200, ok: true, json: () => Promise.resolve({}) });
    const ctx = makeCtx({ csrfToken: 'tok' });
    const api = createApi(ctx);
    await api('POST', '/bar', new FormData());
    const opts = fetchMock.mock.calls[0][1];
    expect(opts.headers['X-CSRF-Token']).toBe('tok');
    expect(opts.method).toBe('POST');
    expect(opts.body).toBeInstanceOf(FormData);
  });

  it('on 401 redirects to /login and throws "unauth"', async () => {
    fetchMock.mockResolvedValueOnce({ status: 401, ok: false, json: () => Promise.resolve({}) });
    const ctx = makeCtx();
    const api = createApi(ctx);
    await expect(api('GET', '/foo')).rejects.toMatchObject({ message: 'unauth' });
    expect(assignMock).toHaveBeenCalledWith('/login');
  });

  it('on 403 shows fatal and schedules a reload, throws "csrf"', async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValueOnce({ status: 403, ok: false, json: () => Promise.resolve({}) });
      const ctx = makeCtx();
      const api = createApi(ctx);
      const p = api('GET', '/foo');
      // The error fires synchronously after the fetch resolves.
      await flushMicrotasks();
      await expect(p).rejects.toMatchObject({ message: 'csrf' });
      expect(ctx.root.querySelector('.error')).not.toBeNull();
      vi.advanceTimersByTime(2000);
      expect(reloadMock).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('on 404 redirects to the legacy study URL, throws "spa-disabled"', async () => {
    fetchMock.mockResolvedValueOnce({ status: 404, ok: false, json: () => Promise.resolve({}) });
    const ctx = makeCtx({ deckId: 99 });
    const api = createApi(ctx);
    await expect(api('GET', '/foo')).rejects.toMatchObject({ message: 'spa-disabled' });
    expect(assignMock).toHaveBeenCalledWith('/deck/99/study');
  });

  it('on 5xx with error_msg throws StudyApiBadError carrying the message', async () => {
    fetchMock.mockResolvedValueOnce({
      status: 500,
      ok: false,
      json: () => Promise.resolve({ error_msg: 'down for maintenance' }),
    });
    const ctx = makeCtx();
    const api = createApi(ctx);
    await expect(api('GET', '/foo')).rejects.toMatchObject({
      message: 'down for maintenance',
      code: 500,
      body: { error_msg: 'down for maintenance' },
    });
  });

  it('on 5xx with empty body throws StudyApiBadError with HTTP code as message', async () => {
    fetchMock.mockResolvedValueOnce({
      status: 500,
      ok: false,
      json: () => Promise.reject(new Error('no json')),
    });
    const ctx = makeCtx();
    const api = createApi(ctx);
    await expect(api('GET', '/foo')).rejects.toMatchObject({
      message: 'HTTP 500',
      code: 500,
    });
  });

  it('aborts any inflight request when a new one is started', async () => {
    const abortMock = vi.fn();
    fetchMock.mockImplementation(
      () =>
        new Promise(() => {
          /* never resolves; we only care about the abort signal */
        }),
    );
    const ctx = makeCtx();
    const api = createApi(ctx);
    // Override the first call so the AbortController is reachable
    const first = api('GET', '/a');
    // ctx.inflight now has the first controller; replace it manually
    // with one whose .abort we can spy on.
    const watched = new AbortController();
    const watchedAbort = vi.spyOn(watched, 'abort');
    ctx.inflight = watched;
    void first;
    fetchMock.mockResolvedValueOnce({ status: 200, ok: true, json: () => Promise.resolve({}) });
    await api('GET', '/b');
    expect(watchedAbort).toHaveBeenCalled();
  });
});

// Re-export a tiny smoke check that makeApiMock is wired correctly; this
// catches drift if helpers.ts is later refactored.
describe('makeApiMock (sanity)', () => {
  it('returns a vi.fn with a default Promise resolve', async () => {
    const m = makeApiMock();
    expect(typeof m).toBe('function');
    await expect(m('GET', '/x', null)).resolves.toEqual({});
  });
});
