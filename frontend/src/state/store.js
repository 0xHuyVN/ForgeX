/**
 * state.js — single source of truth for app-wide state.
 *
 * Replaces the ~50 module-level `let`s scattered across app.js. Modules
 * subscribe to changes via `on()`; mutations go through `set()` so we have
 * a single place to hook persistence / devtools later.
 */

const _state = {
  currentProjectId: null,
  selectedMusicFolder: '',
  publishPlatform: '',
  executeCount: 0,
  rowCount: 0,
  subBoxVisible: false,
  uploadedVoiceSamplePath: '',
  allEdgeVoices: [],
  latestQueueData: null,
};

const _subscribers = new Map();
let _nextKey = 0;

export function get(key) { return _state[key]; }
export function set(key, value) {
  _state[key] = value;
  for (const fn of _subscribers.values()) {
    try { fn(key, value); } catch (e) { console.warn('state subscriber error', e); }
  }
}
export function on(fn) {
  const key = ++_nextKey;
  _subscribers.set(key, fn);
  return () => _subscribers.delete(key);
}