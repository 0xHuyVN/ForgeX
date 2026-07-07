import { defineConfig } from 'vite';
import path from 'node:path';

/**
 * Vite config for the 0xForge frontend.
 *
 * The backend serves `/app.js` from the project root. We keep that path
 * for the legacy file but emit the new Vite bundle to `dist/app.modular.js`
 * and (eventually) swap the `<script src="app.js">` tag in `index.html` to
 * `<script type="module" src="/dist/app.modular.js">`.
 */
export default defineConfig({
  root: path.resolve(__dirname, 'frontend'),
  build: {
    outDir: path.resolve(__dirname, 'dist'),
    emptyOutDir: false,
    sourcemap: true,
    rollupOptions: {
      input: path.resolve(__dirname, 'frontend/src/main.js'),
      output: {
        entryFileNames: 'app.modular.js',
        format: 'iife',
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:7860',
      '/static': 'http://127.0.0.1:7860',
      '/style.css': 'http://127.0.0.1:7860',
      '/app.js': 'http://127.0.0.1:7860',
    },
  },
  test: {
    environment: 'happy-dom',
    globals: false,
    include: ['src/**/*.test.js'],
  },
});
