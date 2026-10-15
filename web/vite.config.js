import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

import { localizeHtml } from './src/lib/page-metadata.ts';

const config = {
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'localized-page-metadata',
      transformIndexHtml: {
        handler: (html) => localizeHtml(html),
        order: 'pre',
      },
    },
  ],
  resolve: {
    alias: {
      '@': resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    host: '::',
    port: 5_173,
    proxy: {
      '/config.json': 'http://127.0.0.1:8080',
      '/hub': 'http://127.0.0.1:8080',
      '/user': {
        target: 'http://127.0.0.1:8080',
        ws: true,
      },
    },
  },
  test: {
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary', 'html', 'lcov'],
      thresholds: {
        branches: 65.58,
        functions: 79,
        lines: 84.47,
        statements: 82.04,
      },
    },
  },
};

export default config;
