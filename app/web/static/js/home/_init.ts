import { applyVisibility, collectRows, findRow, setCollapsed } from './_tree';
import type { DeckRow } from './_tree';

/**
 * Marks the Sync button as "changes pending".
 */
export function markSyncRequired(): void {
  const indicator = document.getElementById('sync-indicator');
  if (!indicator) return;
  indicator.textContent = ' ●';
}

/**
 * Wires the collapse/expand buttons of a deck tree.
 *
 * @param table The `table#deck-tree` element holding the deck rows.
 * @param errorEl Element that shows POST errors. When omitted, the
 *   element is looked up next to the table (`#deck-tree-error`).
 */
export function initDeckTree(
  table: HTMLElement, 
  errorEl: HTMLElement | null = null
): void {
  const csrf = table.getAttribute('data-csrf') || '';
  const error = errorEl || table.parentElement?.querySelector<HTMLElement>('#deck-tree-error') || null;

  let busy = false;

  function showError(message: string): void {
    if (!error) return;
    error.textContent = message;
    error.hidden = false;
  }

  function clearError(): void {
    if (!error) return;
    error.textContent = '';
    error.hidden = true;
  }

  function rollback(rows: DeckRow[], row: DeckRow, collapsed: boolean): void {
    setCollapsed(row, !collapsed);
    applyVisibility(rows);
  }

  function persist(deckId: number, collapsed: boolean, rows: DeckRow[], row: DeckRow): void {
    busy = true;
    const url = '/deck/' + encodeURIComponent(String(deckId)) + '/collapse';
    fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrf,
      },
      body: JSON.stringify({ collapsed: collapsed }),
      credentials: 'same-origin',
    }).then(
      function (response) {
        if (response.status === 401) {
          busy = false;
          location.assign('/login');
          return;
        }
        if (!response.ok) {
          response
            .json()
            .catch(function () {
              return {};
            })
            .then(function (body) {
              busy = false;
              rollback(rows, row, collapsed);
              showError(body && body.error_msg ? body.error_msg : 'HTTP ' + response.status);
            });
          return;
        }
        busy = false;
        markSyncRequired();
      },
      function () {
        busy = false;
        rollback(rows, row, collapsed);
        showError('Network error.');
      },
    );
  }

  table.addEventListener('click', function (ev) {
    const target = ev.target as Element | null;
    if (!target || typeof target.closest !== 'function') return;
    const button = target.closest('[data-action="toggle-deck"]');
    if (!button) return;
    ev.preventDefault();

    if (busy)
      return;

    const deckId = Number(button.getAttribute('data-deck-id'));
    if (!deckId) return;

    const rows = collectRows(table);
    const row = findRow(rows, deckId);
    if (!row) return;

    const collapsed = !row.collapsed;
    setCollapsed(row, collapsed);
    applyVisibility(rows);
    clearError();
    persist(deckId, collapsed, rows, row);
  });

  // Sync the DOM with the server-rendered state.
  applyVisibility(collectRows(table));
}
