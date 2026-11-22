import { renderBook } from '../book/book.js';
import { B, api } from '../core/api.js';
import { renderChrome } from '../core/chrome.js';
import { $, clear, h } from '../core/dom.js';
import { S, parseRoute } from '../core/state.js';
import { BOOK_TABS } from '../core/strings.js';
import { renderLibrary } from '../library/library.js';
import { EMBED } from '../ui/page_overlay.js';
import { clearSelection, leaveScanModes, renderWorkspace } from '../workspace/workspace.js';

// ------------------------------------------------------------------ navigation history (the path actually taken)
// Every screen is a route, so the browser history IS the user's pathway. Each browser entry is stamped with its
// position (history.state.nav): a new entry arrives unstamped and is appended (dropping any forward branch); an
// existing one — reached by our buttons, the browser's buttons, Alt+←/→ or a reload — carries its position. NAV
// (sessionStorage) keeps the titles so the buttons can say where they lead. Minor state inside a screen (the
// selected segment) updates the current entry in place (syncHash), so going back returns to it as it was left.
export const NAV = (() => { try { return JSON.parse(sessionStorage.getItem('ocr.nav')) || { entries: [], idx: -1 }; } catch { return { entries: [], idx: -1 }; } })();
export const navSave = () => sessionStorage.setItem('ocr.nav', JSON.stringify(NAV));
export function navRecord(title) {
  if (EMBED) return;          // an embedded page (overlay iframe) shares sessionStorage: never rewrite the parent's path
  const at = history.state && Number.isInteger(history.state.nav) ? history.state.nav : null;
  if (at !== null && NAV.entries[at]) NAV.idx = at;
  else {
    NAV.entries = NAV.entries.slice(0, NAV.idx + 1).concat({});
    NAV.idx = NAV.entries.length - 1;
    history.replaceState({ ...(history.state || {}), nav: NAV.idx }, '', location.hash || '#/');
  }
  Object.assign(NAV.entries[NAV.idx], { title, hash: location.hash || '#/' });
  navSave();
}
export function navStep(d) { if (NAV.entries[NAV.idx + d]) history.go(d); }
export function screenTitle(r) {
  const TAB_EL = Object.fromEntries(BOOK_TABS);
  if (r.view === 'library' || !S.book) return 'Βιβλιοθήκη';
  if (r.view === 'book') return `${S.book.title} · ${TAB_EL[r.tab] || r.tab}`;
  return `${S.book.title} · Σελίδα ${r.page}`;
}
export function renderNav() {
  const back = NAV.entries[NAV.idx - 1], fwd = NAV.entries[NAV.idx + 1];
  const bb = $('#btn-back'), fb = $('#btn-fwd');
  bb.disabled = !back; fb.disabled = !fwd;
  bb.title = back ? `Πίσω στη διαδρομή σου: ${back.title} (Alt+←)` : 'Δεν υπάρχει προηγούμενη οθόνη σε αυτή τη διαδρομή';
  fb.title = fwd ? `Εμπρός: ${fwd.title} (Alt+→)` : 'Δεν υπάρχει επόμενη οθόνη';
}
// Selections, modes and highlights refer to block/page ids of ONE book: entering another book must not carry them
// over (ids like p1-b3 repeat across books). The library in between does not count as a change.
let shownBook = null;
function enterBook(id) {
  if (id === shownBook) return;
  shownBook = id;
  S.selPages.clear(); S.lastPageClicked = null; S.listAnchor = null;
  clearSelection(); leaveScanModes();
  S.seg = null; S.sel = null; S.hl = null;
}
export async function render() {
  const r = parseRoute();
  S.route = r;
  if (r.view !== 'library') enterBook(r.book);
  $('#view').classList.toggle('fill', r.view === 'ws');
  try {
    if (!S.meta) S.meta = await api('/api/meta');
    if (r.view === 'library') { S.book = null; await renderLibrary(); }
    else if (r.view === 'book') { await loadBook(r.book); S.tab = r.tab; renderBook(); }
    else { await loadBook(r.book); await loadPage(r.page, r.block); renderWorkspace(); }
  } catch (e) { clear($('#view')).append(h('div', { class: 'view-page' }, h('div', { class: 'empty' }, h('h2', {}, 'Σφάλμα'), e.message, h('p', {}, h('a', { href: '#/' }, 'Βιβλιοθήκη'))))); }
  renderChrome();
}
export async function loadBook(id) { S.book = await api(B(id)); S.bookRev = S.book.rev; }
export async function loadPage(n, block) {
  if (S.page && S.page.page !== n) leaveScanModes();   // a draft region/link belongs to its page; the multi-selection may span pages
  S.page = await api(`${B(S.book.id)}/pages/${n}`);
  S.sel = block && S.page.blocks.find(b => b.id === block) ? block : (S.sel && S.page.blocks.find(b => b.id === S.sel) ? S.sel : null);
}
