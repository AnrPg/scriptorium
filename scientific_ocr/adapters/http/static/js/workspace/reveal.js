import { S } from '../core/state.js';

// ------------------------------------------------------------------ "show me this item": one intent for every loop
// A loop step (errors, warnings, typos, exclusions; by button or by key) REQUESTS that its item be shown. The next
// render of the page honours it in whatever view is open (scan canvas, output sheet): the item is centred, even when
// it is on the same page and even when it was already visible. It outranks the "keep the scroll position" rule of
// re-renders (core/dom.js keepScroll), which otherwise restored the old position over it. A plain click on a segment
// never requests it: the page does not move under the pointer.
const TRIES_MS = 1500;                         // the target may appear after the render (output view fetches its HTML)

/** The element that actually scrolls around `el` (its pane), or the page. */
function scroller(el) {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const oy = getComputedStyle(p).overflowY;
    if ((oy === 'auto' || oy === 'scroll') && p.scrollHeight > p.clientHeight) return p;
  }
  return document.scrollingElement;
}
/** Centred when it fits in its pane; a segment taller than the pane (a long paragraph) is shown from its start. */
function show(el) {
  const pane = scroller(el), room = pane ? pane.clientHeight : window.innerHeight;
  const tall = el.getBoundingClientRect().height > 0.85 * room;
  el.style.scrollMarginTop = tall ? '12px' : '';
  el.scrollIntoView({ block: tall ? 'start' : 'center', inline: 'nearest' });
}

/** Ask the next render to bring segment `id` into view. */
export function reveal(id) { S.reveal = id || null; }
export const revealPending = () => !!S.reveal;

/** Called by a view once it has rendered into `container`: centre the requested segment when it is there. */
export function honourReveal(container) {
  const id = S.reveal;
  if (!id || !container) return;
  const t0 = performance.now();
  const sel = `[data-seg="${CSS.escape(id)}"]`;
  const tick = () => {
    if (S.reveal !== id || !container.isConnected) return;            // superseded by a newer request / view
    const el = container.querySelector(sel);
    if (el && el.getBoundingClientRect().height > 0) {
      S.reveal = null;
      // the views' own "bring a newly selected segment into view" must not move it again afterwards
      S.scrolledTo = id; S.outputScrolledTo = id;
      show(el);
      // a page image that finishes loading later may move it: settle once more
      const img = container.querySelector('img');
      if (img && !img.complete) img.addEventListener('load', () => el.isConnected && show(el), { once: true });
      return;
    }
    if (performance.now() - t0 < TRIES_MS) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
