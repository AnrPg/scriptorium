import { api } from '../core/api.js';
import { $, clear, h } from '../core/dom.js';
import { S } from '../core/state.js';
import { CAT_EL } from '../core/strings.js';

// ------------------------------------------------------------------ math rendering (local KaTeX + mhchem)
// LaTeX fields carry no math delimiters (core.symbols.strip_math_delimiters, applied when a correction is saved);
// engine readings often do ("$$ … $$", "\\[ … \\]"): the same rule here, so drafts and previews never fail on them.
const DELIMS = [['$$', '$$'], ['\\[', '\\]'], ['\\(', '\\)'], ['$', '$']];
export function stripMathDelims(s) {
  const t = (s || '').trim();
  for (const [o, c] of DELIMS) {
    if (t.length > o.length + c.length && t.startsWith(o) && t.endsWith(c) && !t.endsWith('\\' + c)) return t.slice(o.length, -c.length).trim();
  }
  return s || '';
}
export function mathBox(latex, display = true) {
  const box = h('div', { class: 'math-view' });
  try { katex.render(stripMathDelims(latex), box, { displayMode: display, throwOnError: true, strict: 'ignore' }); }
  catch (e) { clear(box).append(h('span', { class: 'err' }, 'Δεν αποδίδεται: ' + e.message.replace(/^KaTeX parse error: /, ''))); }
  return box;
}
// A segment's text as the OUTPUT renders it: the server's inline renderer (pipeline.inline — the same function
// as the exports: sub/sup, bold/italic, tables; everything else escaped), then KaTeX for $...$ / $$...$$.
const renderedCache = new Map();
export function textMathBox(text) {
  const box = h('div', { class: 'math-view rendered-text', style: { whiteSpace: 'pre-wrap', fontSize: '14px' } }, text || '');
  if (!text) return box;
  let job = renderedCache.get(text);
  if (!job) {
    if (renderedCache.size > 200) renderedCache.clear();
    job = api('/api/render-inline', { body: { text } }).then(r => r.html).catch(() => null);
    renderedCache.set(text, job);
  }
  job.then(html => {
    if (html !== null) box.innerHTML = html;      // server-built: all values escaped there
    renderMath(box);
  });
  return box;
}
// two colour codes: dot = what the segment IS (type), row/box colour = review state (no colour = fine)
export const CAT = b => b.flow === 'furniture' || ['page_header', 'page_footer'].includes(b.role) ? 'furniture'
  : b.kind === 'heading' ? 'heading' : b.kind === 'caption' ? 'caption' : ['math', 'chemical_reaction'].includes(b.kind) ? 'math'
  : ['molecular_structure', 'lewis_structure', 'reaction_scheme'].includes(b.kind) ? 'chem' : ['figure', 'chart'].includes(b.kind) ? 'figure'
  : b.kind === 'table' ? 'table' : b.kind === 'excerpt' ? 'excerpt' : 'text';
export const rowState = rv => rv === 'required' ? 'required' : rv === 'recommended' ? 'recommended' : rv === 'unresolved' ? 'unresolved' : 'ok';
export function legend() {
  return h('div', { class: 'legend' },
    h('div', {}, h('b', {}, 'Τελεία = είδος τμήματος'), ...Object.entries(CAT_EL).map(([k, l]) => h('div', { class: 'row' }, h('span', { class: 'k-dot k-' + k }), l))),
    h('div', {}, h('b', {}, 'Χρώμα γραμμής/πλαισίου = έλεγχος'),
      h('div', { class: 'row' }, h('span', { class: 'sw', style: { background: 'var(--bad)' } }), 'Απαιτείται έλεγχος'),
      h('div', { class: 'row' }, h('span', { class: 'sw', style: { background: 'var(--warn)' } }), 'Συνιστάται έλεγχος'),
      h('div', { class: 'row' }, h('span', { class: 'sw', style: { background: '#2b2b2b' } }), 'Δυσανάγνωστο'),
      h('div', { class: 'row' }, h('span', { class: 'sw', style: { border: '1px solid var(--line2)' } }), 'Χωρίς χρώμα: εντάξει'),
      h('div', { class: 'row' }, h('span', { class: 'ok-mark' }, '✓'), 'Αποφασίστηκε από άνθρωπο (πράσινο πλαίσιο στη σελίδα)')));
}
export function labelPicker(current, onPick) {
  const cat = S.meta.labels, cur = new Set(current || []);
  const col = (eng, cls, title) => h('div', { class: cls }, h('h4', {}, title), cat[eng].map(l =>
    h('button', { class: 'lab' + (cur.has(`${eng}:${l.name}`) ? ' cur' : ''), onclick: () => onPick({ engine: eng, name: l.name }) },
      h('span', {}, l.el), h('code', {}, l.name))));
  return h('div', { class: 'picker' }, col('marker', 'm', 'Marker'), col('docling', 'd', 'Docling'));
}
export function currentLabels(blk) {              // the labels both engines gave this segment
  const out = [];
  if (blk.engine_label) out.push(blk.engine_label);
  for (const c of blk.candidates || []) if (c.label) out.push(`${c.engine}:${c.label}`);
  return out;
}
