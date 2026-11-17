import { beforeEach, describe, expect, it } from 'vitest';
import { applyVisibility, collectRows, findRow, setCollapsed } from '@deck/_tree';

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
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  document.body.appendChild(table);
  return table;
}

describe('collectRows', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('reads depth, deck id and collapsed state from the attributes', () => {
    const table = makeTable([
      { deckId: 1, depth: 0 },
      { deckId: 2, depth: 1, collapsed: true },
    ]);

    const rows = collectRows(table);

    expect(rows.length).toBe(2);
    expect(rows[0].deckId).toBe(1);
    expect(rows[0].depth).toBe(0);
    expect(rows[0].collapsed).toBe(false);
    expect(rows[1].deckId).toBe(2);
    expect(rows[1].depth).toBe(1);
    expect(rows[1].collapsed).toBe(true);
  });
});

describe('findRow', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('finds a row by deck id', () => {
    const table = makeTable([
      { deckId: 7, depth: 0 },
      { deckId: 8, depth: 1 },
    ]);
    const rows = collectRows(table);

    const row = findRow(rows, 8);

    expect(row).not.toBeNull();
    expect(row?.deckId).toBe(8);
  });

  it('returns null for an unknown deck', () => {
    const table = makeTable([{ deckId: 7, depth: 0 }]);
    const rows = collectRows(table);

    expect(findRow(rows, 99)).toBeNull();
  });
});

describe('applyVisibility', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('hides the descendants of a collapsed deck, but not the deck itself', () => {
    const table = makeTable([
      { deckId: 1, depth: 0, collapsed: true },
      { deckId: 2, depth: 1 },
      { deckId: 3, depth: 2 },
    ]);
    const rows = collectRows(table);

    applyVisibility(rows);

    expect(rows[0].el.classList.contains('is-hidden')).toBe(false);
    expect(rows[1].el.classList.contains('is-hidden')).toBe(true);
    expect(rows[2].el.classList.contains('is-hidden')).toBe(true);
  });

  it('keeps the siblings of a collapsed deck visible', () => {
    const table = makeTable([
      { deckId: 1, depth: 0 },
      { deckId: 2, depth: 1, collapsed: true },
      { deckId: 3, depth: 1 },
      { deckId: 4, depth: 0 },
    ]);
    const rows = collectRows(table);

    applyVisibility(rows);

    expect(rows[0].el.classList.contains('is-hidden')).toBe(false);
    expect(rows[1].el.classList.contains('is-hidden')).toBe(false);
    expect(rows[2].el.classList.contains('is-hidden')).toBe(false);
    expect(rows[3].el.classList.contains('is-hidden')).toBe(false);
  });

  it('hides the descendants of a collapsed grand-parent', () => {
    const table = makeTable([
      { deckId: 1, depth: 0, collapsed: true },
      { deckId: 2, depth: 1 },
      { deckId: 3, depth: 2, collapsed: true },
      { deckId: 4, depth: 3 },
    ]);
    const rows = collectRows(table);

    applyVisibility(rows);

    expect(rows[0].el.classList.contains('is-hidden')).toBe(false);
    expect(rows[1].el.classList.contains('is-hidden')).toBe(true);
    expect(rows[2].el.classList.contains('is-hidden')).toBe(true);
    expect(rows[3].el.classList.contains('is-hidden')).toBe(true);
  });

  it('unhides the rows again when the parent is expanded', () => {
    const table = makeTable([
      { deckId: 1, depth: 0, collapsed: true },
      { deckId: 2, depth: 1 },
    ]);
    const rows = collectRows(table);

    applyVisibility(rows);
    expect(rows[1].el.classList.contains('is-hidden')).toBe(true);

    setCollapsed(rows[0], false);
    applyVisibility(rows);

    expect(rows[1].el.classList.contains('is-hidden')).toBe(false);
  });

  it('reflects the live DOM state, not a stale snapshot field', () => {
    const table = makeTable([
      { deckId: 1, depth: 0 },
      { deckId: 2, depth: 1 },
    ]);
    const stale = collectRows(table);

    // A newer snapshot collapses the parent after `stale` was taken.
    const fresh = collectRows(table);
    setCollapsed(fresh[0], true);

    applyVisibility(stale);

    expect(stale[1].el.classList.contains('is-hidden')).toBe(true);
  });
});

describe('setCollapsed', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('updates the data-collapsed attribute and the row state', () => {
    const table = makeTable([{ deckId: 1, depth: 0 }]);
    const rows = collectRows(table);

    setCollapsed(rows[0], true);

    expect(rows[0].collapsed).toBe(true);
    expect(rows[0].el.getAttribute('data-collapsed')).toBe('1');

    setCollapsed(rows[0], false);

    expect(rows[0].collapsed).toBe(false);
    expect(rows[0].el.getAttribute('data-collapsed')).toBe('0');
  });
});
