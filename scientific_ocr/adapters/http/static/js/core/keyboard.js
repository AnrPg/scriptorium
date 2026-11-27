import { B } from '../core/api.js';
import { stepReview } from '../inspector/reviewloop.js';
import { boundActive, boundDecide, boundEdit, boundEditing, boundSaveEdit, boundSetEdge, boundStep, boundResync, boundToggleEnd, boundUndo, stopBoundLoop } from '../inspector/boundloop.js';
import { stopTypoLoop, typoActive, typoDecide, typoStep } from '../inspector/typoloop.js';
import { doRedo, doUndo } from '../core/chrome.js';
import { $, h, isTyping } from '../core/dom.js';
import { navStep } from '../core/nav.js';
import { R, S, go } from '../core/state.js';
import { openMultiPanel, queueNeighbours } from '../inspector/inspector.js';
import { moveBySteps, nextPageWithWork } from '../inspector/lineage.js';
import { toggleSelectMode } from '../inspector/multi.js';
import { dialogOpen, modal } from '../ui/feedback.js';
import { EMBED } from '../ui/page_overlay.js';
import { VIEWS, clearSelection, omitTargets, renderWorkspace, selectBlock, setView, removeSegments, toggleContinuation, toggleDraw, toggleOmit, toolOn } from '../workspace/workspace.js';

// ------------------------------------------------------------------ keyboard: only what the current state allows
// One declarative table drives the handler AND the help: each shortcut names its key, exact modifiers, when it is
// available (current state) and what it does. Single-letter keys never fire with ⌘/Ctrl/Alt held.
export const K = (key, { mod = false, shift = null, alt = false } = {}) => (e) =>
  (Array.isArray(key) ? key : [key]).includes(e.key.length === 1 ? e.key.toLowerCase() : e.key)
  && (e.metaKey || e.ctrlKey) === mod && e.altKey === alt && (shift === null || e.shiftKey === shift);
export const inWs = (c) => S.route?.view === 'ws' && !c.inDialog;
export const free = (c) => inWs(c) && !c.typing;                 // workspace, not typing in a field
export const SHORTCUTS = [
  { keys: '⌘Z', label: 'Αναίρεση', group: 'always', match: K('z', { mod: true, shift: false }), when: c => !c.typing && !c.inDialog, run: () => doUndo().then(() => boundResync()) },
  { keys: '⇧⌘Z', label: 'Επανάληψη', group: 'always', match: K('z', { mod: true, shift: true }), when: c => !c.typing && !c.inDialog, run: () => doRedo().then(() => boundResync()) },
  { keys: 'Alt+← / Alt+→', label: 'Πίσω / εμπρός στη διαδρομή σου (οθόνες που είδες)', group: 'always',
    match: K(['ArrowLeft', 'ArrowRight'], { alt: true }), when: c => !c.typing && !c.inDialog, run: (e) => navStep(e.key === 'ArrowLeft' ? -1 : 1) },
  { keys: '?', label: 'Συντομεύσεις', group: 'always', match: K('?'), when: c => !c.typing && !c.inDialog, run: () => showHelp() },
  { keys: 'Alt+↑ / Alt+↓', label: 'Μετακίνηση του επιλεγμένου τμήματος στη σειρά ανάγνωσης (ή σύρσιμο στη λίστα)', group: 'page',
    match: K(['ArrowUp', 'ArrowDown'], { alt: true }), when: c => free(c) && S.sel && !S.multi.size, run: (e) => moveBySteps(S.sel, e.key === 'ArrowUp' ? -1 : 1) },
  // the loop over the output profile's exclusions (inspector/boundloop.js) owns these keys while it is on
  // correcting an exclusion's boundaries (right kind, wrong limits): D (Δ) starts, [ ] set them, ↵ saves, Esc cancels
  { keys: 'D', label: 'Διόρθωση ορίων της εξαίρεσης', group: 'bound', match: K(['d', 'δ']), when: c => free(c) && boundActive() && !boundEditing(), run: () => boundEdit() },
  { keys: '[ / ]', label: 'Το επιλεγμένο τμήμα γίνεται η ΑΡΧΗ / το ΤΕΛΟΣ', group: 'bound', match: K(['[', ']']), when: c => free(c) && boundEditing(), run: (e) => boundSetEdge(e.key === '[' ? 'first' : 'last') },
  { keys: '↵', label: 'Αποθήκευση νέων ορίων → επόμενη', group: 'bound', match: K('Enter'), when: (c, e) => free(c) && boundEditing() && !enterOwnedBy(e), run: () => boundSaveEdit() },
  { keys: 'Esc', label: 'Ακύρωση διόρθωσης ορίων', group: 'bound', match: K('Escape'), when: c => free(c) && boundEditing() && !S.multi.size, run: () => boundEdit() },
  { keys: '↵', label: 'Όρια εξαίρεσης σωστά → επόμενη', group: 'bound', match: K('Enter'), when: (c, e) => free(c) && boundActive() && !boundEditing() && !S.multi.size && !enterOwnedBy(e), run: () => boundDecide(true) },
  { keys: 'X', label: 'Όχι αυτό (μένει στην έξοδο) → επόμενη', group: 'bound', match: K('x'), when: c => free(c) && boundActive() && !boundEditing(), run: () => boundDecide(false) },
  { keys: 'J / K', label: 'Επόμενη / προηγούμενη εξαίρεση', group: 'bound', match: K(['j', 'k']), when: c => free(c) && boundActive(), run: (e) => boundStep(e.key.toLowerCase() === 'j' ? 1 : -1) },
  { keys: 'U', label: 'Αναίρεση της τελευταίας απόφασης για εξαίρεση', group: 'bound', match: K(['u', 'θ']), when: c => free(c) && boundActive() && !boundEditing(), run: () => boundUndo() },
  { keys: 'T', label: 'Σελίδα αρχής / τέλους της εξαίρεσης', group: 'bound', match: K('t'), when: c => free(c) && boundActive() && !boundEditing(), run: () => boundToggleEnd() },
  { keys: 'Esc', label: 'Έξοδος από τον έλεγχο ορίων', group: 'bound', match: K('Escape'), when: c => free(c) && boundActive() && !S.sel && !S.multi.size, run: () => stopBoundLoop() },
  // the loop over the spelling proposals (inspector/typoloop.js) owns these keys while it is on
  { keys: '↵', label: 'Διόρθωση της λέξης → επόμενο τυπογραφικό', group: 'typo', match: K('Enter'), when: (c, e) => free(c) && typoActive() && !S.multi.size && !enterOwnedBy(e), run: () => typoDecide(true) },
  { keys: 'X', label: 'Σωστό ως έχει → επόμενο τυπογραφικό', group: 'typo', match: K('x'), when: c => free(c) && typoActive(), run: () => typoDecide(false) },
  { keys: 'J / K', label: 'Επόμενο / προηγούμενο τυπογραφικό', group: 'typo', match: K(['j', 'k']), when: c => free(c) && typoActive(), run: (e) => typoStep(e.key.toLowerCase() === 'j' ? 1 : -1) },
  { keys: 'Esc', label: 'Έξοδος από τον έλεγχο τυπογραφικών', group: 'typo', match: K('Escape'), when: c => free(c) && typoActive() && !S.multi.size, run: () => stopTypoLoop() },
  { keys: '⌘↵', label: 'Αποθήκευση της διόρθωσής μου', group: 'segment', match: K('Enter', { mod: true }), when: c => inWs(c) && c.A.save, run: (e, c) => c.A.save() },
  { keys: 'Esc', label: 'Πίσω / καθαρισμός επιλογής / ακύρωση / απόρριψη πρόχειρου', group: 'segment', match: K('Escape'), when: inWs, run: (e, c) => escapeKey(e, c) },
  { keys: '1 / 2 / 3', label: 'Σάρωση / Αποτέλεσμα / Markdown σελίδας', group: 'page', match: K(['1', '2', '3']), when: c => free(c) && S.page?.info?.state, run: (e) => setView(VIEWS[+e.key - 1][0]) },
  { keys: 'O', label: 'Εκτός εξόδου / ξανά στην έξοδο (το επιλεγμένο ή τα επιλεγμένα· μένουν στο βιβλίο)', group: 'multi', match: K('o'), when: c => free(c) && toolOn('omit') && omitTargets().length > 0, run: () => toggleOmit() },
  { keys: 'F', label: 'Συνέχεια της προηγούμενης παραγράφου (το επιλεγμένο ή τα επιλεγμένα κείμενα· ξανά: αυτόματα)', group: 'multi', match: K(['f', 'φ']), when: c => free(c) && omitTargets().length > 0, run: () => toggleContinuation() },
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
  { keys: 'J / K', label: 'Επόμενο / προηγούμενο προς έλεγχο (σε όλο το βιβλίο, χωρίς αλλαγή σελίδας με το χέρι)', group: 'page', match: K(['j', 'k']), when: free, run: (e) => queueStep(e.key.toLowerCase() === 'j' ? 1 : -1) },
  { keys: '← / → · PgUp / PgDn', label: 'Προηγούμενη / επόμενη σελίδα (σελίδες στη σειρά, χωρίς τον έλεγχο)', group: 'page', match: K(['ArrowLeft', 'ArrowRight', 'PageUp', 'PageDown']), when: free, run: (e) => pageStep(['ArrowLeft', 'PageUp'].includes(e.key) ? -1 : 1) },
  { keys: 'R', label: 'Σχεδίαση περιοχής (Σάρωση)', group: 'page', match: K('r'), when: c => free(c) && toolOn('region'), run: () => toggleDraw() },
  { keys: 'B', label: 'Εμφάνιση πλαισίων (Σάρωση)', group: 'page', match: K('b'), when: c => free(c) && toolOn('boxes'), run: () => { S.showBoxes = !S.showBoxes; renderWorkspace(); } },
];
export const SHORTCUT_GROUPS = [['bound', 'Έλεγχος ορίων εξαιρέσεων'], ['typo', 'Έλεγχος τυπογραφικών'], ['segment', 'Επιλεγμένο τμήμα'], ['multi', 'Πολλαπλή επιλογή'], ['page', 'Σελίδα & προβολή'], ['always', 'Πάντα']];
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
export function queueStep(dir) { return stepReview(dir); }     // the whole-book review loop (inspector/reviewloop.js)
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
