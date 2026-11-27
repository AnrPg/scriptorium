import { h } from '../core/dom.js';
import { render } from '../core/nav.js';
import { KIND_EL, ROLE_EL } from '../core/strings.js';
import { leaveScanModes, zoom } from '../workspace/workspace.js';

// ------------------------------------------------------------------ state & router
export const S = { leftView: localStorage.getItem('ocr.leftView') || 'scan', multi: new Map(), selectMode: false, meta: null, books: [], book: null, page: null, sel: null, jobs: [], tab: 'pages', filter: 'all',
            selPages: new Set(), lastPageClicked: null, zoom: 1, showBoxes: true, drawing: false, draft: null, inspTab: 'segment', level: 'required' };
export const R = (code) => (S.meta && S.meta.reasons[code]) || { el: code, review: null, crop: false, finding: null };
/** Left out by the book's OUTPUT PROFILE (e.g. questions in a RAG book) and not kept back by the reviewer:
 *  not drawn on the page, not listed in the structure, not reviewed — only listed under «Κρυμμένα από την έξοδο». */
export const profileHidden = b => !!(b && b.profile_omitted && !b.kept);
/** Out of the reader output for any reason (the reviewer's choice or the output profile). */
export const outOfOutput = b => !!(b && (b.omitted || profileHidden(b)));
// a paragraph may CONTINUE the previous one (pipeline/flow.py): every TEXT segment, also inside a box or an excursus;
// never visual content (figures, tables, equations, captions), titles or page furniture
const NOT_CONTINUABLE = ['figure', 'chart', 'molecular_structure', 'reaction_scheme', 'lewis_structure', 'table', 'math', 'chemical_reaction', 'nuclear_reaction', 'caption', 'heading'];
export const continuable = b => !!b && b.flow !== 'furniture' && !NOT_CONTINUABLE.includes(b.kind) && !(b.kind === 'excerpt' && b.chosen?.type !== 'text');
export const kindLabel = b => ROLE_EL[b.role] || KIND_EL[b.original_kind || b.kind] || b.kind;
export function scriptTag(ch) {             // show the alphabet when glyphs look identical (Α vs A)
  const c = (ch || '').codePointAt(0) || 0;
  return c >= 0x370 && c <= 0x3FF ? 'ελλ.' : c >= 0x400 && c <= 0x4FF ? 'κυρ.' : c < 0x250 ? 'λατ.' : '';
}
export const sym = t => h('span', {}, h('b', {}, t), t && t.length === 1 ? h('sub', { class: 'mut' }, ' ' + scriptTag(t)) : null);

export function parseRoute() {
  const [path, query] = location.hash.replace(/^#\/?/, '').split('?');
  const v = new URLSearchParams(query || '').get('view');
  if (['scan', 'output', 'markdown'].includes(v)) { if (v !== 'scan') leaveScanModes(); S.leftView = v; }
  const p = path.split('/').map(decodeURIComponent);
  if (p[0] === 'b' && p[1]) {
    if (p[2] === 'p' && p[3]) return { view: 'ws', book: p[1], page: +p[3], block: p[4] || null };
    return { view: 'book', book: p[1], tab: p[2] || 'pages' };
  }
  return { view: 'library' };
}
export const go = (hash) => { if (location.hash !== hash) location.hash = hash; else render(); };
