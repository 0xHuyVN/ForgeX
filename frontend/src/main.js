/**
 * main.js — Vite entry point.
 *
 * Replaces the 5,130-line `app.js` for new development. The legacy `app.js`
 * is still served as a fallback so the existing bundled EXE keeps working
 * until the migration is complete.
 *
 * Migration plan:
 *   1. Each section of `app.js` is split into a module under src/views/*.
 *   2. Modules import shared DOM helpers from src/ui/dom.js (no more
 *      raw innerHTML interpolation).
 *   3. Once every section is migrated, this main.js becomes the single
 *      bootstrap and `app.js` is removed.
 */

import { escapeHtml, el, clearChildren } from './ui/dom.js';
import { apiGet, apiPost, apiPut, apiPatch, apiDelete } from './api/client.js';
import * as store from './state/store.js';

// Re-export on window for views that have not been migrated yet — this lets
// the new code coexist with the legacy script during the transition.
Object.assign(window, {
  escapeHtml, el, clearChildren,
  apiGet, apiPost, apiPut, apiPatch, apiDelete,
  store,
});

console.info('[0xForge] modular bootstrap loaded — using Vite bundle');