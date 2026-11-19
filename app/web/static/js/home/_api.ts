/** Error shape thrown by the functions of this module. */
export interface HomeApiError {
  message: string;
}

/**
 * Fetches the deck list as an HTML fragment.
 * @returns The raw HTML of the fragment.
 */
export function getDecksFragmentHtmlAsync(): Promise<string> {
  return fetch('/decks', { credentials: 'same-origin' }).then(
    async function (response) {
      if (response.status === 401) {
        throw { message: 'unauth' } as HomeApiError;
      }
      if (!response.ok) {
        throw { message: 'HTTP ' + response.status } as HomeApiError;
      }
      try {
        return await response.text();
      } catch {
        throw { message: 'network' } as HomeApiError;
      }
    },
    function () {
      throw { message: 'network' } as HomeApiError;
    },
  );
}

/**
 * Asks the server whether AnkiWeb has changes we have not pulled yet.
 * @returns True when a sync is required.
 */
export function getSyncStatusAsync(): Promise<boolean> {
  return fetch('/sync-status', { credentials: 'same-origin' }).then(
    async function (response) {
      if (response.status === 401) {
        throw { message: 'unauth' } as HomeApiError;
      }
      if (!response.ok) {
        throw { message: 'HTTP ' + response.status } as HomeApiError;
      }
      try {
        const body = await response.json();
        return Boolean(body && body.sync_required);
      } catch {
        throw { message: 'network' } as HomeApiError;
      }
    },
    function () {
      throw { message: 'network' } as HomeApiError;
    },
  );
}
