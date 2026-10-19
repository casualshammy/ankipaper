// Pure DOM helpers. No SPA state — operate on whatever root the
// caller passes via the ctx argument.

import type { ElChild } from './_types';

export function el(
  tag: string,
  attrs?: Record<string, string> | null,
  ...kids: ElChild[]
): HTMLElement {
  const n = document.createElement(tag);
  if (attrs) {
    for (const k in attrs) {
      if (!Object.prototype.hasOwnProperty.call(attrs, k)) continue;
      const v = attrs[k];
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else n.setAttribute(k, v);
    }
  }
  for (const c of kids) {
    if (c == null || c === false) continue;
    if (typeof c === 'string') n.appendChild(document.createTextNode(c));
    else n.appendChild(c);
  }
  return n;
}

export function closestEl(target: EventTarget | null, selector: string): Element | null {
  if (target instanceof Element) return target.closest(selector);
  return null;
}

export function clear(root: HTMLElement): void {
  while (root.firstChild) {
    root.removeChild(root.firstChild);
  }
}

export function scrollAndFocus(root: HTMLElement): void {
  const bar = document.getElementById('remaining-bar');
  if (bar) {
    const rect = bar.getBoundingClientRect();
    window.scrollTo(0, rect.top + window.pageYOffset);
  }
  const p = root.querySelector<HTMLElement>('.btn-primary');
  if (p && p.focus) {
    try {
      p.focus({ preventScroll: true });
    } catch (e) {
      p.focus();
    }
  }
}

export function showError(root: HTMLElement, msg: string): void {
  const old = root.querySelector('.error');
  if (old && old.parentNode) old.parentNode.removeChild(old);
  if (!msg) return;
  const p = el('p', { class: 'error', text: msg });
  root.insertBefore(p, root.firstChild);
}

export function showFatal(root: HTMLElement, msg: string): void {
  clear(root);
  root.appendChild(el('p', { class: 'error', text: msg }));
}
