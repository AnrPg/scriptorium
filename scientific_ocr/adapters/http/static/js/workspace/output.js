import { B, api } from '../core/api.js';
import { $, clear, h, loadFailed } from '../core/dom.js';
import { S } from '../core/state.js';
import { toggleMulti } from '../inspector/multi.js';
import { toast } from '../ui/feedback.js';
import { hoverSeg } from '../workspace/hover.js';
import { honourReveal } from '../workspace/reveal.js';
import { clearSelection, selectBlock, zoom } from '../workspace/workspace.js';

// ---------------------------------------------------------------- page output preview (final rendering, approximate)
// The rendered page is a pure function of (book, page, revision, style): cached, so re-selecting a segment or
// toggling views does not re-request it; a newer call supersedes an older one still in flight (race token).
export const outputCache = new Map();
export let outputToken = 0;
export async function pageOutput(bookId, n, style) {
  const key = `${bookId}|${n}|${S.bookRev}|${style}`;
  if (!outputCache.has(key)) {
    if (outputCache.size > 40) outputCache.clear();
    outputCache.set(key, api(`${B(bookId)}/pages/${n}/output?style=${style}`).catch(e => { outputCache.delete(key); throw e; }));
  }
  return outputCache.get(key);
}
export async function outputView(wrap, b, n) {
  wrap.classList.add('output-wrap');
  const sheet = h('div', { class: 'pv-sheet', style: { zoom: S.zoom } }, h('div', { class: 'small mut' }, 'Φόρτωση…'));
  wrap.append(sheet);
  const style = (S.meta.styles || []).includes(localStorage.getItem('ocr.style')) ? localStorage.getItem('ocr.style') : 'tags';
  const token = ++outputToken;
  let o;
  try { o = await pageOutput(b.id, n, style); } catch (e) {
    if (token === outputToken && sheet.isConnected) loadFailed(sheet, e);
    return;
  }
  if (token !== outputToken || !sheet.isConnected) return;     // superseded by a newer render
  clear(sheet);
  if (S.leftView === 'markdown') {
    sheet.append(h('div', { class: 'row small mut', style: { marginBottom: '8px' } }, `Ακριβές κομμάτι του Markdown εξόδου που προέρχεται από τη σελ. ${n} (διαχωριστικά: ${style === 'tags' ? 'ετικέτες HTML' : 'σχόλια'}).`,
      h('span', { class: 'spacer' }), h('button', { class: 'small', onclick: () => { navigator.clipboard.writeText(o.markdown); toast('Αντιγράφηκε'); } }, 'Αντιγραφή')),
      h('pre', { class: 'preview-pre', style: { maxHeight: 'none' } }, o.markdown));
    return;
  }
  sheet.append(h('div', { class: 'pv-note small' }, 'Προσέγγιση της τελικής εξόδου, από το ίδιο JSON με τις εξαγωγές. Οι ενδείξεις ελέγχου δεν περιλαμβάνονται στο αρχείο. Κλικ σε τμήμα = επιλογή.'));
  const body = h('div', { class: 'pv-body' });
  body.innerHTML = o.html;                         // server-built from canonical; all values escaped there
  sheet.append(body);
  renderMath(body);
  body.querySelectorAll('[data-seg]').forEach(el => {
    const id = el.getAttribute('data-seg');
    el.addEventListener('mouseenter', (e) => { e.stopPropagation(); hoverSeg(id, 'page'); });
    el.addEventListener('mouseleave', () => hoverSeg(null));
    el.addEventListener('click', (e) => { e.stopPropagation(); if (e.shiftKey || S.selectMode) { const blk = S.page.blocks.find(x => x.id === id); if (blk) toggleMulti(blk); } else { clearSelection(); selectBlock(id); } });
    if (id === S.sel) el.classList.add('pv-sel');
  });
  honourReveal(body);                               // a loop step's item is centred here too
  const selEl = body.querySelector('.pv-sel');
  if (selEl && S.outputScrolledTo !== S.sel && !S.reveal) {          // only when the selection changed, never after an action
    S.outputScrolledTo = S.sel;
    selEl.scrollIntoView({ block: 'center' });
  }
}
