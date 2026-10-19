import { describe, expect, it } from 'vitest';
import { clear, closestEl, el, showError, showFatal } from '@spa/_dom';

describe('el', () => {
  it('creates an element of the given tag', () => {
    const n = el('div');
    expect(n.tagName).toBe('DIV');
  });

  it('applies class via the special `class` key', () => {
    const n = el('div', { class: 'foo bar' });
    expect(n.className).toBe('foo bar');
  });

  it('applies text via the special `text` key as textContent', () => {
    const n = el('p', { text: 'hello' });
    expect(n.textContent).toBe('hello');
  });

  it('applies arbitrary attributes via setAttribute', () => {
    const n = el('button', { type: 'button', 'data-action': 'mark', 'aria-label': 'Mark' });
    expect(n.getAttribute('type')).toBe('button');
    expect(n.getAttribute('data-action')).toBe('mark');
    expect(n.getAttribute('aria-label')).toBe('Mark');
  });

  it('appends string kids as text nodes', () => {
    const n = el('div', null, 'hello', ' ', 'world');
    expect(n.childNodes.length).toBe(3);
    expect(n.textContent).toBe('hello world');
  });

  it('appends Node kids as real children', () => {
    const child = el('span', { text: 'x' });
    const n = el('div', null, child);
    expect(n.children.length).toBe(1);
    expect(n.firstElementChild).toBe(child);
  });

  it('skips null, false, undefined kids', () => {
    const n = el('div', null, 'a', null, false, undefined, 'b');
    expect(n.textContent).toBe('ab');
  });

  it('returns null when attrs is null', () => {
    const n = el('div', null, 'x');
    expect(n.textContent).toBe('x');
  });
});

describe('clear', () => {
  it('removes every child of the root', () => {
    const root = document.createElement('div');
    root.appendChild(el('span', { text: 'a' }));
    root.appendChild(el('span', { text: 'b' }));
    clear(root);
    expect(root.childNodes.length).toBe(0);
  });
});

describe('closestEl', () => {
  it('returns the element itself when it matches the selector', () => {
    const n = el('div', { class: 'foo' });
    expect(closestEl(n, '.foo')).toBe(n);
  });

  it('walks up to find a matching ancestor', () => {
    const root = el('div', { class: 'root' });
    const child = el('div', { class: 'child' });
    const grand = el('span', null, 'x');
    child.appendChild(grand);
    root.appendChild(child);
    expect(closestEl(grand, '.root')).toBe(root);
  });

  it('returns null when no ancestor matches', () => {
    const n = el('div', null, 'x');
    expect(closestEl(n, '.nope')).toBeNull();
  });

  it('returns null when the target is not an Element', () => {
    expect(closestEl(null, 'div')).toBeNull();
    const text = document.createTextNode('x');
    expect(closestEl(text, 'div')).toBeNull();
  });
});

describe('showError', () => {
  it('inserts a <p class="error"> as the first child of root', () => {
    const root = document.createElement('div');
    root.appendChild(el('span', { text: 'existing' }));
    showError(root, 'boom');
    const first = root.firstChild as HTMLElement;
    expect(first.tagName).toBe('P');
    expect(first.className).toBe('error');
    expect(first.textContent).toBe('boom');
  });

  it('removes a previous error before inserting a new one', () => {
    const root = document.createElement('div');
    showError(root, 'first');
    showError(root, 'second');
    const errors = root.querySelectorAll('.error');
    expect(errors.length).toBe(1);
    expect(errors[0].textContent).toBe('second');
  });

  it('removes the old error and inserts nothing when msg is empty', () => {
    const root = document.createElement('div');
    showError(root, 'first');
    showError(root, '');
    expect(root.querySelector('.error')).toBeNull();
  });
});

describe('showFatal', () => {
  it('clears the root and appends a single error', () => {
    const root = document.createElement('div');
    root.appendChild(el('span', { text: 'old' }));
    root.appendChild(el('span', { text: 'older' }));
    showFatal(root, 'fatal');
    expect(root.querySelectorAll('span').length).toBe(0);
    const e = root.querySelector('.error');
    expect(e).not.toBeNull();
    expect(e!.textContent).toBe('fatal');
  });
});
