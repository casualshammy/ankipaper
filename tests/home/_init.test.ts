import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { initDeckTree, markSyncRequired } from '@home/_init';

interface RowSpec {
  deckId: number;
  depth: number;
  collapsed?: boolean;
}

/**
 * Builds a deck table with the given rows.
 * @param specs Rows to render (in DFS order).
 * @returns The table element.
 */
function makeTable(specs: RowSpec[]): HTMLTableElement {
  const table = document.createElement('table');
  const tbody = document.createElement('tbody');
  for (const spec of specs) {
    const tr = document.createElement('tr');
    tr.className = 'deck-row';
    tr.setAttribute('data-depth', String(spec.depth));
    tr.setAttribute('data-deck-id', String(spec.deckId));
    tr.setAttribute('data-collapsed', spec.collapsed ? '1' : '0');
    const td = document.createElement('td');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'deck-toggle';
    button.setAttribute('data-action', 'toggle-deck');
    button.setAttribute('data-deck-id', String(spec.deckId));
    td.appendChild(button);
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  table.setAttribute('data-csrf', 'test-token');
  document.body.appendChild(table);
  return table;
}

function makeErrorElement(): HTMLParagraphElement {
  const p = document.createElement('p');
  p.className = 'error';
  p.id = 'deck-tree-error';
  p.hidden = true;
  document.body.appendChild(p);
  return p;
}

/**
 * Builds a minimal fetch response stub.
 * @param ok Whether the response is successful.
 * @param body JSON body returned by `json()`.
 * @returns The response stub.
 */
function makeResponse(ok: boolean, body: object): Response {
  return {
    ok: ok,
    status: ok ? 200 : 400,
    json: function () {
      return Promise.resolve(body);
    },
  } as unknown as Response;
}

describe('initDeckTree', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('posts the collapse request with the CSRF token', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeResponse(true, {}));
    vi.stubGlobal('fetch', fetchMock);
    const table = makeTable([{ deckId: 1, depth: 0 }]);
    initDeckTree(table, makeErrorElement());

    const button = table.querySelector('[data-action="toggle-deck"]');
    (button as HTMLElement).click();

    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    const url = fetchMock.mock.calls[0][0] as string;
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(url).toBe('/deck/1/collapse');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('test-token');
    expect(init.body).toBe(JSON.stringify({ collapsed: true }));
  });

  it('rolls the row back and shows the error when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeResponse(false, { error_msg: 'No deck.' })));
    const table = makeTable([{ deckId: 1, depth: 0 }]);
    const errorEl = makeErrorElement();
    initDeckTree(table, errorEl);

    const button = table.querySelector('[data-action="toggle-deck"]');
    (button as HTMLElement).click();

    await vi.waitFor(() => {
      expect(errorEl.hidden).toBe(false);
    });
    expect(errorEl.textContent).toBe('No deck.');
    const row = table.querySelector('tr.deck-row');
    expect(row?.getAttribute('data-collapsed')).toBe('0');
  });

  it('marks the sync indicator after a successful collapse', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeResponse(true, {})));
    const indicator = document.createElement('span');
    indicator.id = 'sync-indicator';
    document.body.appendChild(indicator);
    const table = makeTable([{ deckId: 1, depth: 0 }]);
    initDeckTree(table, makeErrorElement());

    const button = table.querySelector('[data-action="toggle-deck"]');
    (button as HTMLElement).click();

    await vi.waitFor(() => {
      expect(indicator.textContent).toBe(' ●');
    });
  });
});

describe('markSyncRequired', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('sets the filled dot on the sync indicator', () => {
    const indicator = document.createElement('span');
    indicator.id = 'sync-indicator';
    document.body.appendChild(indicator);

    markSyncRequired();

    expect(indicator.textContent).toBe(' ●');
  });

  it('does nothing when the indicator is missing', () => {
    expect(() => markSyncRequired()).not.toThrow();
  });
});
