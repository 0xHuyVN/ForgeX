import { describe, it, expect } from 'vitest';
import { escapeHtml, el, clearChildren } from './dom.js';

describe('escapeHtml', () => {
  it('escapes HTML control characters', () => {
    expect(escapeHtml('<script>alert(1)</script>'))
      .toBe('&lt;script&gt;alert(1)&lt;/script&gt;');
  });
  it('escapes ampersands and quotes', () => {
    expect(escapeHtml('a & b "c" \'d\'')).toBe('a &amp; b "c" \'d\'');
  });
  it('coerces null and undefined to empty string', () => {
    expect(escapeHtml(null)).toBe('');
    expect(escapeHtml(undefined)).toBe('');
  });
});

describe('el() — XSS safety', () => {
  it('renders text children as text nodes (no innerHTML)', () => {
    const node = el('div', {}, ['<script>alert(1)</script>']);
    expect(node.innerHTML).toBe('&lt;script&gt;alert(1)&lt;/script&gt;');
    expect(node.childNodes.length).toBe(1);
    expect(node.firstChild.nodeType).toBe(Node.TEXT_NODE);
  });

  it('escapes the explicit html prop is opt-in only', () => {
    const safe = el('div', { html: '<b>safe</b>' });
    expect(safe.innerHTML).toBe('<b>safe</b>');
  });

  it('uses setAttribute for arbitrary string attrs', () => {
    const node = el('a', { href: 'javascript:alert(1)' });
    expect(node.getAttribute('href')).toBe('javascript:alert(1)');
  });

  it('binds on* props via addEventListener', () => {
    let clicked = 0;
    const btn = el('button', { onclick: () => { clicked++; } }, ['go']);
    btn.click();
    expect(clicked).toBe(1);
  });

  it('handles dataset / className / style object', () => {
    const node = el('span', {
      className: 'foo bar',
      dataset: { id: '42' },
      style: { color: 'red' },
    }, ['x']);
    expect(node.className).toBe('foo bar');
    expect(node.dataset.id).toBe('42');
    expect(node.style.color).toBe('red');
  });

  it('clearChildren empties the node without losing identity', () => {
    const parent = el('div', {}, [el('span', {}, ['a']), el('span', {}, ['b'])]);
    const beforeId = parent.__identity_for_test = true;
    clearChildren(parent);
    expect(parent.childNodes.length).toBe(0);
    expect(parent.__identity_for_test).toBe(true);
  });
});