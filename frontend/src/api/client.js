/**
 * api.js — thin wrapper around fetch() for the FastAPI backend.
 *
 * Lives in its own module so views never construct URLs by hand. Centralising
 * the fetch wrapper means we can add CSRF token injection, telemetry, retry
 * logic, etc. in exactly one place.
 */

const API_BASE = (typeof window !== 'undefined' && window.location)
  ? window.location.origin + '/api'
  : '/api';

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(API_BASE + path, opts);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data && data.detail) msg += `: ${data.detail}`;
    } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return res.json();
  return res.text();
}

export const apiGet    = (path)        => request('GET',    path);
export const apiPost   = (path, body)  => request('POST',   path, body);
export const apiPut    = (path, body)  => request('PUT',    path, body);
export const apiPatch  = (path, body)  => request('PATCH',  path, body);
export const apiDelete = (path)        => request('DELETE', path);