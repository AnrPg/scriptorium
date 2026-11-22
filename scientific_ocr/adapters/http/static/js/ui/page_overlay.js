import { refresh } from '../core/chrome.js';
import { h } from '../core/dom.js';
import { S, go } from '../core/state.js';
import { openDialog } from '../ui/feedback.js';

// ---------------------------------------------------------------- a page in an overlay, from any book screen
// The SAME page screen as the Σελίδες tab (all its tools and actions), embedded (?embed=1 hides the app chrome)
// in a large overlay, so a reviewer checking a list (structure fixes, proposals, entities, hierarchy) can act on
// a page and come back to where they were. "Πλήρης οθόνη" goes to the page itself; closing refreshes the list.
export function openPage(page, block) {
  const route = `#/b/${S.book.id}/p/${page}${block ? '/' + block : ''}`;
  const d = openDialog('page-overlay');
  const frame = h('iframe', { class: 'page-frame', src: `/${route}${route.includes('?') ? '&' : '?'}embed=1`, title: `Σελίδα ${page}` });
  d.append(h('div', { class: 'row page-overlay-bar' }, h('b', { class: 'grow' }, `Σελίδα ${page}`),
    h('button', { class: 'small', title: 'Άνοιγμα στην καρτέλα της σελίδας', onclick: () => { d.close(); go(route); } }, '⤢ Πλήρης οθόνη'),
    h('button', { class: 'small ghost', onclick: () => d.close() }, 'Κλείσιμο', h('kbd', {}, 'Esc'))), frame);
  // Esc pressed inside the iframe never reaches this document: the embedded page posts a message instead
  const onMsg = (e) => { if (e.origin === location.origin && e.source === frame.contentWindow && e.data?.type === 'ocr-embed-escape') d.close(); };
  window.addEventListener('message', onMsg);
  d.addEventListener('close', () => { window.removeEventListener('message', onMsg); refresh(true); });   // what was done in the page shows in the list
  d.showModal();
}
// embedded instance: no app chrome (set once at start-up from the URL)
export const EMBED = /[?&]embed=1\b/.test(location.hash);
if (EMBED) document.documentElement.classList.add('embed');
