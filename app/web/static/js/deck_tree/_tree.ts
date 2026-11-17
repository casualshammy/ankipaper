// Pure deck-tree logic — no network access. The DOM is the source of
// truth for the collapsed state; the toggle request lives in index.ts.

export interface DeckRow {
  el: HTMLTableRowElement;
  deckId: number;
  depth: number;
  collapsed: boolean;
}

/**
 * Reads the deck rows of a table (in DOM order).
 * @param root Container that holds the `tr.deck-row` elements.
 * @returns One entry per deck row.
 */
export function collectRows(root: ParentNode): DeckRow[] {
  const rows: DeckRow[] = [];
  const nodes = root.querySelectorAll('tr.deck-row');
  for (let i = 0; i < nodes.length; i++) {
    const el = nodes[i] as HTMLTableRowElement;
    rows.push({
      el: el,
      deckId: Number(el.getAttribute('data-deck-id')),
      depth: Number(el.getAttribute('data-depth') || '0'),
      collapsed: el.getAttribute('data-collapsed') === '1',
    });
  }
  return rows;
}

/**
 * Finds the row of a deck.
 * @param rows Rows as returned by collectRows.
 * @param deckId Id of the deck to look for.
 * @returns The row, or null when the deck is not in the table.
 */
export function findRow(rows: DeckRow[], deckId: number): DeckRow | null {
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].deckId === deckId) return rows[i];
  }
  return null;
}

/**
 * Hides the rows whose ancestors are collapsed.
 *
 * The rows come in depth-first order, so the ancestors of a row are the
 * last rows seen at the lower depths. A collapsed row itself stays
 * visible — only its descendants are hidden.
 * @param rows Rows as returned by collectRows.
 */
export function applyVisibility(rows: DeckRow[]): void {
  // collapsedByDepth[d] tells whether the closest ancestor seen at depth
  // d is collapsed.
  const collapsedByDepth: boolean[] = [];
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    let hidden = false;
    for (let d = 0; d < row.depth; d++) {
      if (collapsedByDepth[d]) {
        hidden = true;
        break;
      }
    }
    if (hidden) {
      row.el.classList.add('is-hidden');
    } else {
      row.el.classList.remove('is-hidden');
    }
    collapsedByDepth[row.depth] = row.el.getAttribute('data-collapsed') === '1';
    for (let d = row.depth + 1; d < collapsedByDepth.length; d++) {
      collapsedByDepth[d] = false;
    }
  }
}

/**
 * Marks a row as collapsed (or expanded).
 * @param row Row to change.
 * @param collapsed New state of the row.
 */
export function setCollapsed(row: DeckRow, collapsed: boolean): void {
  row.collapsed = collapsed;
  row.el.setAttribute('data-collapsed', collapsed ? '1' : '0');
}
