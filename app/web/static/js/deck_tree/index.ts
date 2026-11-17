// Deck-tree bootstrap for the home page. Wires the toggle buttons of
// the deck tree to POST /deck/{id}/collapse and keeps the DOM in sync
// with the state stored in the collection.
//
// esbuild bundles this into app/web/static/js/deck_tree.js.

import { applyVisibility, collectRows, findRow, setCollapsed } from './_tree';
import type { DeckRow } from './_tree';

(function () {
  const table = document.getElementById('deck-tree');
  if (!table) return;

  const csrf = table.getAttribute('data-csrf') || '';
  const errorEl = document.getElementById('deck-tree-error');

  let busy = false;

  function showError(message: string): void {
    if (!errorEl) return;
    errorEl.textContent = message;
    errorEl.hidden = false;
  }

  function clearError(): void {
    if (!errorEl) return;
    errorEl.textContent = '';
    errorEl.hidden = true;
  }

  // Writing to the collection raises the USN — the decks have to be
  // synced, exactly like in Anki Desktop.
  function markSyncRequired(): void {
    const indicator = document.getElementById('sync-indicator');
    if (!indicator) return;
    indicator.textContent = ' ●';
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
})();
