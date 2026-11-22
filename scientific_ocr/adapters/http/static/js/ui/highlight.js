import { h } from '../core/dom.js';

// ---------------------------------------------------------------- engine disagreements highlighted in the text
// A conflict (core.symbols.token_conflicts) carries the token Marker read, which occurrence of it in the text it
// is, and the words before/after. The spot is found again in ANY version of the text (current, or the draft the
// reviewer is editing): the n-th whole-token occurrence; if the draft changed the count, the occurrence whose
// preceding words match best.
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const tokenRe = (t) => new RegExp(`(?<![\\p{L}\\p{N}])${esc(t)}(?![\\p{L}\\p{N}])`, 'gu');
export function conflictsOf(blk) {
  return Object.values(blk.evidence || {}).flatMap(ev => (ev && ev.conflicts) || []);
}
export function conflictRanges(text, conflicts) {
  const out = [];
  for (const c of conflicts || []) {
    if (!c.primary || !text) continue;
    const hits = [...text.matchAll(tokenRe(c.primary))];
    if (!hits.length) continue;
    let m = hits[c.occurrence ?? 0];
    if (!m && c.before) {                               // the draft changed: pick the best preceding-context match
      const want = c.before.split(' ').slice(-3).join(' ');
      m = hits.reduce((best, x) => (text.slice(Math.max(0, x.index - 60), x.index).includes(want) ? x : best), hits[0]);
    }
    m = m || hits[0];
    out.push({ start: m.index, end: m.index + m[0].length, c });
  }
  return out.sort((a, b) => a.start - b.start).filter((r, i, a) => !i || r.start >= a[i - 1].end);
}
const title = (c) => `Marker: ${c.primary} · Docling: ${c.secondary} (${c.class})`;
export function markedText(text, ranges) {             // text nodes + <mark> spans (no innerHTML)
  const parts = [];
  let at = 0;
  for (const r of ranges) {
    parts.push(text.slice(at, r.start), h('mark', { class: 'conflict' + (r.c.critical ? ' crit' : ''), title: title(r.c) }, text.slice(r.start, r.end)));
    at = r.end;
  }
  parts.push(text.slice(at));
  return parts;
}
// a textarea cannot style its text: a backdrop with the same font/metrics shows the marks behind it
export function highlightTextarea(ta, conflicts) {
  const back = h('div', { class: 'hl-back', 'aria-hidden': 'true' });
  const wrap = h('div', { class: 'hl-wrap' }, back, ta);
  const paint = () => { back.replaceChildren(...markedText(ta.value + '\n', conflictRanges(ta.value, conflicts))); back.scrollTop = ta.scrollTop; };
  ta.addEventListener('input', paint);
  ta.addEventListener('scroll', () => { back.scrollTop = ta.scrollTop; back.scrollLeft = ta.scrollLeft; });
  // one observer per draft render: let it go once the textarea is detached (the panel was re-rendered), else they pile up
  const ro = new ResizeObserver(() => { if (!ta.isConnected) return ro.disconnect(); back.style.height = ta.offsetHeight + 'px'; });
  ro.observe(ta);
  paint();
  return wrap;
}
