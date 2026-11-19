import { defineConfig } from 'vitest/config';
import path from 'node:path';

// Tests live in tests/study-spa/ and tests/home/ and import the
// sources via the `@spa` / `@home` aliases so we don't reach into app/
// with brittle relative paths.
export default defineConfig({
  test: {
    environment: 'happy-dom',
    include: ['tests/study-spa/**/*.test.ts', 'tests/home/**/*.test.ts'],
  },
  resolve: {
    alias: {
      '@spa': path.resolve(__dirname, 'app/web/static/js/study_spa'),
      '@home': path.resolve(__dirname, 'app/web/static/js/home'),
    },
  },
});
