import { B, api } from '../core/api.js';
import { kickPoll } from '../core/chrome.js';
import { h } from '../core/dom.js';
import { S } from '../core/state.js';
import { EV_EL } from '../core/strings.js';
import { entName, famName } from '../book/entities.js';
import { modal, toast } from './feedback.js';

// ------------------------------------------------------------------ banked rebuilds
// Large books are not rebuilt after every action: actions are recorded at once and BANKED (S.book.pending) until
// the reviewer rebuilds. Views that need book-wide results ask first (ensureFresh); actions that need new OCR say
// that the new reading comes with the rebuild.

export const pendingOf = (b = S.book) => (b && b.pending) || { count: 0, ocr: 0, events: [], estimate_s: null };
export function fmtEstimate(s) {
  if (s == null) return '';
  if (s < 60) return `~${Math.max(5, Math.round(s / 5) * 5)} δευτ.`;
  return `~${Math.round(s / 60)} λεπτ${Math.round(s / 60) === 1 ? 'ό' : 'ά'}`;
}

export async function startRebuild() {
  const b = S.book;
  if (!b) return;
  await api(`${B(b.id)}/rebuild`, { body: {} });
  toast(`Ενημέρωση βιβλίου σε εξέλιξη (${fmtEstimate(pendingOf().estimate_s)}). Μπορείς να συνεχίσεις τον έλεγχο σελίδων.`);
  kickPoll();
}

/** The two-button dialog: «Πίσω» (resolve false) or «Ενημέρωση τώρα» (starts the rebuild, resolve false: the view
 *  becomes available when it finishes). */
export function rebuildDialog(why) {
  const p = pendingOf();
  return new Promise((resolve) => modal('Πρέπει πρώτα να ενημερωθεί το βιβλίο',
    h('div', {}, h('p', {}, why),
      h('p', { class: 'small mut' }, `${p.count} αλλαγ${p.count === 1 ? 'ή' : 'ές'} σε αναμονή · η ενημέρωση διαρκεί ${fmtEstimate(p.estimate_s) || 'λίγο'}. `,
        'Στο μεταξύ μπορείς να συνεχίσεις τον έλεγχο σελίδων· οι νέες αλλαγές μπαίνουν κι αυτές στην αναμονή.')),
    [{ label: 'Πίσω', run: () => resolve(false) },
     { label: 'Ενημέρωση τώρα', cls: 'primary', run: async () => { await startRebuild(); resolve(false); } }]));
}

const busyRebuilding = () => (S.jobs || []).some(j => S.book && j.book === S.book.id && j.kind === 'reproject' && ['queued', 'running'].includes(j.state));

/** true when `what` can be shown now (nothing banked); otherwise asks to rebuild first and resolves false. */
export async function ensureFresh(what) {
  const p = pendingOf();
  if (!p.count) return true;
  if (busyRebuilding()) {
    await new Promise((resolve) => modal('Η ενημέρωση του βιβλίου τρέχει', h('p', {}, `${what}: θα είναι διαθέσιμο μόλις τελειώσει η ενημέρωση (δες την πρόοδο επάνω δεξιά).`),
      [{ label: 'Πίσω', run: () => resolve() }]));
    return false;
  }
  return rebuildDialog(`${what}: δείχνει αποτελέσματα που εξαρτώνται από όλο το βιβλίο (δομή, ομάδες, κατηγορίες, έξοδο), οπότε πρέπει να περιλαμβάνει τις αλλαγές σου.`);
}

/** Pending actions that touch a segment (shown in its panel, so a banked change is visibly recorded). */
export function pendingFor(blk) {
  const id = blk.id, keys = new Set((blk.entities || []).map(x => x.key));
  const touches = (e) => e.block_id === id || (e.members || []).includes(id) || (e.block_ids || []).includes(id) || keys.has(e.key)
    || (e.merge && (e.merge.members || []).includes(id)) || (e.group && (e.group.members || []).includes(id));
  const shown = new Set(pendingOf().page_applied || []);     // already visible in the page preview
  return pendingOf().events.filter(e => !shown.has(e.seq) && touches(e));
}
const typeName = (t) => (t || '').startsWith('family:') ? famName(t.slice(7)) : entName(t);
const PENDING_EL = {
  entity_confirm: e => `Κατηγορία: ${typeName(e.entity_type)}`, entity_assign: e => `Κατηγορία: ${typeName(e.entity_type)}`,
  entity_remove: () => 'Αφαίρεση κατηγορίας', merge_add: () => 'Ένωση τμημάτων', merge_remove: () => 'Διάσπαση ενωμένου',
  group_add: e => `Ομάδα «${(e.group && (e.group.name || e.group.type)) || ''}»`, group_remove: () => 'Διάλυση ομάδας',
  class_accept: () => 'Μαζική αποδοχή', suggestion_dismiss: () => 'Απόρριψη πρότασης',
};
export function describePending(e) {
  const f = PENDING_EL[e.type] || EV_EL[e.type];
  try { return f ? String(f(e)).replace(` · ${e.block_id}`, '') : e.type; } catch { return e.type; }   // the panel IS that segment
}
