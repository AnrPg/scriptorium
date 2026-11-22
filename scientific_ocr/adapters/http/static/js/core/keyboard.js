import { B } from '../core/api.js';
import { doRedo, doUndo } from '../core/chrome.js';
import { $, h, isTyping } from '../core/dom.js';
import { navStep } from '../core/nav.js';
import { R, S, go } from '../core/state.js';
import { openMultiPanel, queueNeighbours } from '../inspector/inspector.js';
import { moveBySteps, nextPageWithWork } from '../inspector/lineage.js';
import { toggleSelectMode } from '../inspector/multi.js';
import { dialogOpen, modal } from '../ui/feedback.js';
import { EMBED } from '../ui/page_overlay.js';
import { VIEWS, clearSelection, omitTargets, renderWorkspace, selectBlock, setView, removeSegments, toggleDraw, toggleOmit, toolOn } from '../workspace/workspace.js';

// ------------------------------------------------------------------ keyboard: only what the current state allows
// One declarative table drives the handler AND the help: each shortcut names its key, exact modifiers, when it is
// available (current state) and what it does. Single-letter keys never fire with ⌘/Ctrl/Alt held.
export const K = (key, { mod = false, shift = null, alt = false } = {}) => (e) =>
  (Array.isArray(key) ? key : [key]).includes(e.key.length === 1 ? e.key.toLowerCase() : e.key)
  && (e.metaKey || e.ctrlKey) === mod && e.altKey === alt && (shift === null || e.shiftKey === shift);
export const inWs = (c) => S.route?.view === 'ws' && !c.inDialog;
export const free = (c) => inWs(c) && !c.typing;                 // workspace, not typing in a field
export const SHORTCUTS = [
  { keys: '⌘Z', label: 'Αναίρεση', group: 'always', match: K('z', { mod: true, shift: false }), when: c => !c.typing && !c.inDialog, run: () => doUndo() },
  { keys: '⇧⌘Z', label: 'Επανάληψη', group: 'always', match: K('z', { mod: true, shift: true }), when: c => !c.typing && !c.inDialog, run: () => doRedo() },
  { keys: 'Alt+← / Alt+→', label: 'Πίσω / εμπρός στη διαδρομή σου (οθόνες που είδες)', group: 'always',
    match: K(['ArrowLeft', 'ArrowRight'], { alt: true }), when: c => !c.typing && !c.inDialog, run: (e) => navStep(e.key === 'ArrowLeft' ? -1 : 1) },
  { keys: '?', label: 'Συντομεύσεις', group: 'always', match: K('?'), when: c => !c.typing && !c.inDialog, run: () => showHelp() },
  { keys: 'Alt+↑ / Alt+↓', label: 'Μετακίνηση του επιλεγμένου τμήματος στη σειρά ανάγνωσης (ή σύρσιμο στη λίστα)', group: 'page',
    match: K(['ArrowUp', 'ArrowDown'], { alt: true }), when: c => free(c) && S.sel && !S.multi.size, run: (e) => moveBySteps(S.sel, e.key === 'ArrowUp' ? -1 : 1) },
  { keys: '⌘↵', label: 'Αποθήκευση της διόρθωσής μου', group: 'segment', match: K('Enter', { mod: true }), when: c => inWs(c) && c.A.save, run: (e, c) => c.A.save() },
  { keys: 'Esc', label: 'Πίσω / καθαρισμός επιλογής / ακύρωση / απόρριψη πρόχειρου', group: 'segment', match: K('Escape'), when: inWs, run: (e, c) => escapeKey(e, c) },
  { keys: '1 / 2 / 3', label: 'Σάρωση / Αποτέλεσμα / Markdown σελίδας', group: 'page', match: K(['1', '2', '3']), when: c => free(c) && S.page?.info?.state, run: (e) => setView(VIEWS[+e.key - 1][0]) },
  { keys: 'O', label: 'Εκτός εξόδου / ξανά στην έξοδο (το επιλεγμένο ή τα επιλεγμένα· μένουν στο βιβλίο)', group: 'multi', match: K('o'), when: c => free(c) && toolOn('omit') && omitTargets().length > 0, run: () => toggleOmit() },
  { keys: '⌫', label: 'Αφαίρεση λάθος τμήματος (το επιλεγμένο ή τα επιλεγμένα· ⌘Z για αναίρεση)', group: 'multi', match: K(['Delete', 'Backspace']), when: c => free(c) && toolOn('omit') && omitTargets().length > 0, run: () => removeSegments() },
  { keys: 'S', label: 'Λειτουργία επιλογής (κλικ = προσθήκη/αφαίρεση) — στη Σάρωση', group: 'multi', match: K('s'), when: c => free(c) && toolOn('select'), run: () => toggleSelectMode() },
  { keys: '↵', label: 'Ομαδοποίηση/συγχώνευση των επιλεγμένων →', group: 'multi', match: K('Enter'), when: (c, e) => free(c) && S.multi.size >= 2 && !S.multiOpen && !enterOwnedBy(e), run: () => openMultiPanel() },
  { keys: 'G', label: 'Ομαδοποίηση επιλεγμένων…', group: 'multi', match: K('g'), when: c => free(c) && (c.A.group || (S.multi.size >= 2 && !S.multiOpen)), run: () => multiAction('group') },
  { keys: 'C', label: 'Κατηγορία για τα επιλεγμένα (μία οντότητα)…', group: 'multi', match: K('c'), when: c => free(c) && (c.A.category || (S.multi.size >= 2 && !S.multiOpen)), run: () => multiAction('category') },
  { keys: 'M', label: 'Συγχώνευση επιλεγμένων σε ένα…', group: 'multi', match: K('m'), when: c => free(c) && (c.A.merge || (S.multi.size >= 2 && !S.multiOpen)), run: () => multiAction('merge') },
  { keys: 'A', label: 'Εγκρίνω αυτή την εκδοχή', group: 'segment', match: K('a'), when: c => free(c) && c.A.approve, run: (e, c) => c.A.approve() },
  { keys: 'E', label: 'Επεξεργασία (πρόχειρο)', group: 'segment', match: K('e'), when: c => free(c) && c.A.edit, run: (e, c) => c.A.edit() },
  { keys: 'I', label: 'Μόνο εικόνα', group: 'segment', match: K('i'), when: c => free(c) && c.A.imageOnly, run: (e, c) => c.A.imageOnly() },
  { keys: 'U', label: 'Δυσανάγνωστο', group: 'segment', match: K('u'), when: c => free(c) && c.A.unreadable, run: (e, c) => c.A.unreadable() },
  { keys: 'W', label: 'Είναι λάθος — νέο OCR', group: 'segment', match: K('w'), when: c => free(c) && c.A.wrong, run: (e, c) => c.A.wrong() },
  { keys: 'N', label: 'Σημείωση (μοντέλο)', group: 'segment', match: K('n'), when: c => free(c) && c.A.note, run: (e, c) => c.A.note() },
  { keys: 'L', label: 'Ετικέτα & δομή', group: 'segment', match: K('l'), when: c => free(c) && c.A.label, run: (e, c) => c.A.label() },
  { keys: 'J / K', label: 'Επόμενο / προηγούμενο προς έλεγχο', group: 'page', match: K(['j', 'k']), when: free, run: (e) => queueStep(e.key.toLowerCase() === 'j' ? 1 : -1) },
  { keys: '← / →', label: 'Προηγούμενη / επόμενη σελίδα', group: 'page', match: K(['ArrowLeft', 'ArrowRight']), when: free, run: (e) => pageStep(e.key === 'ArrowLeft' ? -1 : 1) },
  { keys: 'R', label: 'Σχεδίαση περιοχής (Σάρωση)', group: 'page', match: K('r'), when: c => free(c) && toolOn('region'), run: () => toggleDraw() },
  { keys: 'B', label: 'Εμφάνιση πλαισίων (Σάρωση)', group: 'page', match: K('b'), when: c => free(c) && toolOn('boxes'), run: () => { S.showBoxes = !S.showBoxes; renderWorkspace(); } },
];
export const SHORTCUT_GROUPS = [['segment', 'Επιλεγμένο τμήμα'], ['multi', 'Πολλαπλή επιλογή'], ['page', 'Σελίδα & προβολή'], ['always', 'Πάντα']];
// a focused control (button, link, summary, field) handles its own Enter: the multi-selection shortcut must not steal it
const enterOwnedBy = (e) => !!e?.target?.closest?.('button,a,[role=button],summary,input,select,textarea');
export function keyContext(e) {
  // a focused <select> owns its arrow keys (they change its value), but letter shortcuts still work there
  const selectArrows = e.target?.tagName === 'SELECT' && /^Arrow/.test(e.key || '');
  return { inDialog: dialogOpen(), typing: isTyping(e.target) || selectArrows, A: S.segActions || {} };
}
export function escapeKey(e, c) {
  if (S.multiOpen) { S.multiOpen = false; renderWorkspace(); }                    // back to the list, selection kept
  else if (S.multi.size || S.selectMode) { clearSelection(); renderWorkspace(); }
  else if (S.linking) { S.linking = null; renderWorkspace(); }
  else if (S.draft || S.drawing) { S.draft = null; S.drawing = false; renderWorkspace(); }
  else if (c.A.discard) c.A.discard();
  else if (c.typing) e.target.blur();
  else if (EMBED) parent.postMessage({ type: 'ocr-embed-escape' }, location.origin);   // nothing left to cancel: close the overlay
}
export function multiAction(kind) {
  if (S.multi.size >= 2 && !S.multiOpen) openMultiPanel();
  (S.segActions || {})[kind]?.();
}
export function queueStep(dir) {
  const blk = S.page && S.page.blocks.find(b => b.id === S.sel), nb = blk && queueNeighbours(blk);
  if (dir < 0) { if (nb && nb.prev) selectBlock(nb.prev.id); return; }
  if (nb && nb.next) selectBlock(nb.next.id);
  else if (!blk) { const f = S.page.blocks.find(b => b.review === 'required' || b.review === 'recommended'); f ? selectBlock(f.id) : nextPageWithWork(); }
  else nextPageWithWork();
}
export function pageStep(dir) {
  const n = S.page.page + dir;
  if (n >= 1 && n <= S.page.page_count) go(`#/b/${S.book.id}/p/${n}`);
}
export function onKeydown(e) {
  const c = keyContext(e);
  const sc = SHORTCUTS.find(x => x.match(e) && x.when(c, e));
  if (!sc) return;
  e.preventDefault();
  sc.run(e, c);
}
export function showHelp() {
  const c = { inDialog: false, typing: false, A: S.segActions || {} };
  modal('Συντομεύσεις', h('div', {}, SHORTCUT_GROUPS.map(([g, title]) => [h('h3', {}, title),
    h('table', { class: 'kbd-table' }, SHORTCUTS.filter(x => x.group === g).map(x => { const ok = !!x.when(c);
      return h('tr', { class: ok ? '' : 'off' }, h('td', {}, h('kbd', {}, x.keys)), h('td', {}, x.label + (ok ? '' : ' — όχι τώρα'))); }))])),
    [{ label: 'Κλείσιμο' }]);
}
