import { $, clear, h, keepScroll } from '../core/dom.js';
import { S } from '../core/state.js';
import { multiPanel } from '../inspector/multi.js';
import { pagePanel } from '../inspector/page.js';
import { segmentPanel } from '../inspector/segment.js';
import { clearSelection, omitState, removeSegments, renderWorkspace, toggleOmit } from '../workspace/workspace.js';

// ---------------------------------------------------------------- inspector
export function renderInspector(right) {
  keepScroll(right, () => drawInspector(right));
}
function drawInspector(right) {
  clear(right);
  S.segActions = {};                // shortcuts are rebuilt by the panel drawn now: never a previous segment's actions
  const blk = S.page.blocks.find(b => b.id === S.sel);
  const tab = blk ? S.inspTab : 'page';                // derived, never written back during rendering
  right.append(h('div', { class: 'insp-tabs', role: 'tablist' },
    h('button', { class: tab === 'segment' ? 'on' : '', role: 'tab', 'aria-selected': String(tab === 'segment'), disabled: !blk, onclick: () => { S.inspTab = 'segment'; renderInspector(right); } }, 'Τμήμα'),
    h('button', { class: tab === 'page' ? 'on' : '', role: 'tab', 'aria-selected': String(tab === 'page'), onclick: () => { S.inspTab = 'page'; renderInspector(right); } }, 'Σελίδα')));
  // Selecting several segments keeps you in the list; the group/merge screen opens only when you ask for it.
  const open = S.multiOpen && S.multi.size >= 2;
  if (S.multi.size && !open) right.append(selectionBar());
  const panel = open ? 'multi' : (tab === 'segment' ? 'segment:' + blk.id : 'page');
  const body = h('div', { class: 'insp', 'data-keep-scroll': 'insp:' + panel }); right.append(body);
  if (open) multiPanel(body);
  else if (tab === 'segment') segmentPanel(body, blk); else pagePanel(body);
}
export function openMultiPanel() { if (S.multi.size >= 2) { S.multiOpen = true; renderWorkspace(); } }
export function selectionBar() {
  const n = S.multi.size;
  return h('div', { class: 'sel-bar' }, h('b', { class: 'grow' }, `${n} επιλεγμένα`),
    h('button', { class: 'small primary', disabled: n < 2, onclick: openMultiPanel, title: 'Ομαδοποίηση, συγχώνευση ή κατηγορία για τα επιλεγμένα' }, 'Ομαδοποίηση/συγχώνευση →', h('kbd', {}, '↵')),
    h('button', { class: 'small', onclick: toggleOmit, title: 'Τα επιλεγμένα εκτός Markdown/PDF (μένουν στο βιβλίο) — ή ξανά στην έξοδο' }, omitState() ? 'Στην έξοδο' : 'Εκτός εξόδου', h('kbd', {}, 'O')),
    h('button', { class: 'small dismiss', onclick: removeSegments, title: 'Αφαίρεση ως λάθος πλαίσια (θόρυβος, διπλά) — ένα ⌘Z τα επαναφέρει' }, 'Αφαίρεση', h('kbd', {}, '⌫')),
    h('button', { class: 'small ghost', onclick: () => { clearSelection(); renderWorkspace(); } }, 'Καθαρισμός', h('kbd', {}, 'Esc')),
    h('div', { class: 'small mut hint' }, n < 2 ? 'Διάλεξε κι άλλα: ⌘/Ctrl+κλικ για προσθήκη, Shift+κλικ για συνεχές εύρος.'
      : 'Σύρε ένα επιλεγμένο στη λίστα για να μετακινηθούν όλα μαζί. ⌘/Ctrl+κλικ προσθήκη · Shift+κλικ εύρος.'));
}
export function queueNeighbours(blk) {
  const order = S.page.blocks.filter(b => b.review === 'required' || b.review === 'recommended');
  const i = order.findIndex(b => b.id === blk.id);
  return { prev: i > 0 ? order[i - 1] : null, next: i >= 0 && i < order.length - 1 ? order[i + 1] : (i < 0 ? order[0] : null), pos: i + 1, total: order.length };
}
