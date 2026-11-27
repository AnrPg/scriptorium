// How much of a loop is left: «απομένουν 12 από 40» with a thin progress bar. ONE surface for every loop mode —
// errors / warnings (inspector/reviewloop.js, whole book), typos (typoloop.js), the profile's exclusions
// (boundloop.js). `left` = items with no verdict yet (a verdict still waiting for the book update counts as done).
import { h } from '../core/dom.js';

const n = (x) => x.toLocaleString('el-GR');

export function loopCount(left, total, what = 'στοιχεία') {
  const done = Math.max(0, total - left), pct = total ? Math.round(100 * done / total) : 100;
  return h('span', { class: 'loop-count nowrap', role: 'status', title: `${n(left)} ${what} χωρίς απόφαση από ${n(total)} · ${n(done)} έγιναν (${pct}%)` },
    h('span', { class: 'loop-count-t' }, left ? `απομένουν ${n(left)} από ${n(total)}` : `τελείωσαν και τα ${n(total)}`),
    h('span', { class: 'loop-count-bar', 'aria-hidden': 'true' }, h('span', { style: { width: pct + '%' } })));
}

/** A count that is known only after a request: a placeholder now, filled in when `promise` resolves to {left, total}. */
export function loopCountLater(promise, what) {
  const slot = h('span', { class: 'loop-count-slot' });
  promise.then(r => { if (r && r.total) slot.replaceWith(loopCount(r.left, r.total, what)); }).catch(() => {});
  return slot;
}
