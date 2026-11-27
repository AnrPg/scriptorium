// ------------------------------------------------------------------ tiny DOM helper (safe: text via textContent)
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'html') throw new Error('no raw html');
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat(Infinity)) if (c !== null && c !== undefined && c !== false)
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  // anything clickable that is not natively interactive is reachable and operable from the keyboard
  if (attrs && attrs.onclick && !NATIVE_INTERACTIVE.has(tag)) {
    if (!el.hasAttribute('role')) el.setAttribute('role', 'button');
    if (!el.hasAttribute('tabindex')) el.tabIndex = 0;
    el.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target === el) { e.preventDefault(); el.click(); } });
  }
  return el;
}
// "Typing" = a field that consumes letter/arrow keys. Checkboxes, radios, buttons and <select> do not, so shortcuts
// and background refreshes must still work while one of them has focus.
const TEXT_INPUTS = new Set(['text', 'search', 'number', 'email', 'url', 'password', 'tel']);
export const isTyping = (el) => !!el && (el.tagName === 'TEXTAREA' || el.isContentEditable
  || (el.tagName === 'INPUT' && TEXT_INPUTS.has((el.type || 'text').toLowerCase())));
export const isActiveJob = (j) => j.state === 'running' || j.state === 'queued';
export const NATIVE_INTERACTIVE = new Set(['button', 'a', 'input', 'select', 'textarea', 'label', 'summary', 'details', 'option']);
export const $ = s => document.querySelector(s);
// a page-space box [x0, y0, x1, y1] as CSS percentages of a page of size dims [w, h]
export const pctStyle = (bb, dims) => ({ left: (100 * bb[0] / dims[0]) + '%', top: (100 * bb[1] / dims[1]) + '%',
  width: (100 * (bb[2] - bb[0]) / dims[0]) + '%', height: (100 * (bb[3] - bb[1]) / dims[1]) + '%' });
export const clear = el => { while (el.firstChild) el.firstChild.remove(); return el; };
// a "Φόρτωση…" placeholder whose request failed: say so in place (never an endless "loading")
export const loadFailed = (el, e) => clear(el).append(h('div', { class: 'badge-bad', role: 'alert' },
  `Δεν φορτώθηκε: ${(e && e.message) || e}`));

// Re-rendering rebuilds DOM; a reviewer who acted (closed a section, clicked in a panel, closed an overlay) must stay
// where they were. keepScroll(root, render): remembers the scroll position of `root` and of every element marked
// [data-keep-scroll] inside it, renders, restores — and keeps restoring while content arriving asynchronously
// ("Φόρτωση…" cards) makes the page grow back, until the reviewer scrolls or clicks themselves.
const KEEP_MS = 3000;
const USER_INPUT = ['wheel', 'touchmove', 'keydown', 'mousedown'];
export function keepScroll(root, render) {
  if (!root) return render();
  const els = () => [root, ...root.querySelectorAll('[data-keep-scroll]')];
  const keyOf = (e) => (e === root ? '__root' : e.dataset.keepScroll);
  const saved = new Map(els().filter(e => e.scrollTop || e.scrollLeft).map(e => [keyOf(e), [e.scrollTop, e.scrollLeft]]));
  const out = render();
  if (!saved.size) return out;
  const apply = () => {
    for (const e of els()) {
      const s = saved.get(keyOf(e));
      if (s && (Math.abs(e.scrollTop - s[0]) > 1 || Math.abs(e.scrollLeft - s[1]) > 1)) { e.scrollTop = s[0]; e.scrollLeft = s[1]; }
    }
  };
  apply();
  // content keeps arriving after the render (images load, cards fetch their data) and changes heights WITHOUT a DOM
  // mutation: re-apply every frame until the position is reached and stable, or the reviewer takes over
  let alive = true, frames = 0;
  const stop = () => { alive = false; USER_INPUT.forEach(ev => window.removeEventListener(ev, stop, true)); };
  const t0 = performance.now();
  const tick = () => {
    if (!alive) return;
    apply();
    const done = [...saved].every(([k, [top]]) => { const e = els().find(x => keyOf(x) === k); return !e || Math.abs(e.scrollTop - top) <= 1; });
    frames = done ? frames + 1 : 0;
    if (frames > 20 || performance.now() - t0 > KEEP_MS) return stop();
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  USER_INPUT.forEach(ev => window.addEventListener(ev, stop, { capture: true, passive: true }));
  return out;
}

// Text cut with an ellipsis shows in full on hover, everywhere: the first element under the pointer (or one of its
// close ancestors) whose content overflows gets a title with its whole text (an existing title is kept).
export function installOverflowTitles() {
  document.addEventListener('mouseover', (e) => {
    let el = e.target instanceof Element ? e.target : null;
    for (let i = 0; el && i < 4; i++, el = el.parentElement) {
      if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).textOverflow === 'ellipsis') {
        if (!el.title || el.dataset.ovt) { el.title = el.textContent.replace(/\s+/g, ' ').trim(); el.dataset.ovt = '1'; }
        return;
      }
      // fits now (e.g. the panel was widened): drop a tooltip we added earlier, keep authored ones
      if (el.dataset.ovt) { el.removeAttribute('title'); delete el.dataset.ovt; }
    }
  }, { passive: true });
}

// Collapsible sections (<details>) keep the state the reviewer gave them across re-renders (a rebuild, another
// page): keyed by their title without counts ("Ομάδες σελίδας (3)" -> "Ομάδες σελίδας"), remembered per browser.
const DETAILS_KEY = 'ocr.details';
let detailsState = null;
const loadDetails = () => { if (!detailsState) { try { detailsState = JSON.parse(localStorage.getItem(DETAILS_KEY)) || {}; } catch { detailsState = {}; } } return detailsState; };
export function rememberDetails(root) {
  if (!root) return;
  const st = loadDetails();
  for (const d of root.querySelectorAll('details')) {
    const sum = d.querySelector(':scope > summary');
    const key = sum && sum.textContent.replace(/\(\s*[\d.,]+\s*\)|\d+/g, '').replace(/\s+/g, ' ').trim();
    if (!key || d.dataset.remembered) continue;
    d.dataset.remembered = '1';
    if (key in st) d.open = st[key];
    d.addEventListener('toggle', () => { st[key] = d.open; try { localStorage.setItem(DETAILS_KEY, JSON.stringify(st)); } catch { /* storage blocked */ } });
  }
}

/** A collapsible section of a tab (closed until the reviewer opens it; rememberDetails keeps the state). The title
 *  carries the count; an ⓘ in it shows its tip without toggling the section. */
export function section(title, ...kids) {
  const sum = h('summary', { onclick: (e) => { if (e.target.closest('.info')) e.preventDefault(); } }, ...[title].flat());
  return h('details', { class: 'section' }, sum, h('div', {}, ...kids));
}
