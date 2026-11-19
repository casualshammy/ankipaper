import { afterEach, describe, expect, it, vi } from 'vitest';
import { getDecksFragmentHtmlAsync, getSyncStatusAsync } from '@home/_api';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('fetchDecksFragment', () => {
  it('returns the HTML fragment on success', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('<table id="deck-tree"></table>', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const html = await getDecksFragmentHtmlAsync();

    expect(html).toBe('<table id="deck-tree"></table>');
    expect(fetchMock).toHaveBeenCalledWith('/decks', { credentials: 'same-origin' });
  });

  it('throws unauth on 401', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 401 })));

    await expect(getDecksFragmentHtmlAsync()).rejects.toEqual({ message: 'unauth' });
  });

  it('throws HTTP <code> on a server error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('boom', { status: 500 })));

    await expect(getDecksFragmentHtmlAsync()).rejects.toEqual({ message: 'HTTP 500' });
  });

  it('throws network when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await expect(getDecksFragmentHtmlAsync()).rejects.toEqual({ message: 'network' });
  });
});

describe('fetchSyncStatus', () => {
  it('reads the sync_required flag', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ sync_required: true }), { status: 200 })));

    const required = await getSyncStatusAsync();

    expect(required).toBe(true);
  });

  it('reads a false sync_required flag', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ sync_required: false }), { status: 200 })));

    const required = await getSyncStatusAsync();

    expect(required).toBe(false);
  });

  it('throws unauth on 401', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 401 })));

    await expect(getSyncStatusAsync()).rejects.toEqual({ message: 'unauth' });
  });

  it('throws HTTP <code> on a server error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('boom', { status: 503 })));

    await expect(getSyncStatusAsync()).rejects.toEqual({ message: 'HTTP 503' });
  });

  it('throws network when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await expect(getSyncStatusAsync()).rejects.toEqual({ message: 'network' });
  });
});
