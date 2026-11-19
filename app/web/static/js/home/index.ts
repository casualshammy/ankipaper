import { getDecksFragmentHtmlAsync, getSyncStatusAsync } from './_api';
import { initDeckTree, markSyncRequired } from './_init';
import type { HomeApiError } from './_api';

(function () {
  const element = document.getElementById('decks-root');
  if (!element) return; // no collection yet - nothing to load
  const root: HTMLElement = element;

  function showFailure(): void {
    root.innerHTML = '';
    const p = document.createElement('p');
    p.className = 'error';
    p.textContent = 'Failed to load decks.';
    root.appendChild(p);
    const a = document.createElement('a');
    a.href = '/';
    a.textContent = 'Retry';
    root.appendChild(a);
  }

  function onDecks(html: string): void {
    root.innerHTML = html;
    const table = root.querySelector('#deck-tree');
    if (table) {
      initDeckTree(table as HTMLElement);
    }
    // The sync probe is a separate request and runs after the deck
    // list is on the screen.
    getSyncStatusAsync().then(
      function (required) {
        if (required) markSyncRequired();
      },
      function () {
        // The indicator is a nice-to-have - ignore probe failures.
      },
    );
  }

  function onError(e: HomeApiError): void {
    if (e && e.message === 'unauth') {
      location.assign('/login');
      return;
    }
    showFailure();
  }

  getDecksFragmentHtmlAsync().then(onDecks, onError);
})();
