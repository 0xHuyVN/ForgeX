/**
 * dom.js — XSS-safe DOM helpers (extracted from app.js).
 *
 * Used by every frontend module so we never interpolate user-controlled
 * strings into innerHTML.
 */

export function escapeHtml(text) {
  const d = document.createElement('div');
  d.textContent = text == null ? '' : String(text);
  return d.innerHTML;
}

/**
 * el(tag, props, children) — minimal element factory.
 *
 * - String children become text nodes (no HTML parsing).
 * - `html` prop is the only escape hatch and must be used with care.
 * - Event handlers (`onclick` etc.) are bound via addEventListener, never
 *   set via the on* shortcut which would be at-least-equally XSS-prone.
 */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null) continue;
    if (key === 'class' || key === 'className') {
      node.className = String(value);
    } else if (key === 'style' && typeof value === 'object') {
      Object.assign(node.style, value);
    } else if (key === 'dataset' && typeof value === 'object') {
      for (const [dk, dv] of Object.entries(value)) node.dataset[dk] = dv;
    } else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'html') {
      node.innerHTML = String(value);
    } else {
      node.setAttribute(key, String(value));
    }
  }
  for (const child of [].concat(children)) {
    if (child == null || child === false) continue;
    if (child instanceof Node) {
      node.appendChild(child);
    } else {
      node.appendChild(document.createTextNode(String(child)));
    }
  }
  return node;
}

export function clearChildren(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}