import { B, api } from '../core/api.js';
import { refresh } from '../core/chrome.js';
import { h } from '../core/dom.js';
import { S, go } from '../core/state.js';
import { entName } from '../book/entities.js';
import { actLater, toast } from '../ui/feedback.js';
import { reveal } from '../workspace/reveal.js';
import { renderWorkspace } from '../workspace/workspace.js';

// ---------------------------------------------------------------- loop over the output profile's automatic exclusions
// (pipeline/profile.py: «Εξαιρέσεις του προφίλ: επιβεβαίωσε τα όρια»). One exclusion at a time on its page: its
// segments drawn hatched (workspace: .ws-box.bound), the first marked ΑΡΧΗ and the last ΤΕΛΟΣ, a bar with the
// position and the two verdicts. Keys (keyboard.js): ↵ σωστό · X όχι αυτό · J/K επόμενο/προηγούμενο · T αρχή/τέλος
// · Esc έξοδος. A verdict is an ordinary undoable event (entity_confirm / suggestion_dismiss); it is remembered
// here until the book is rebuilt, so the loop never offers it again.
const pageOfId = (id) => +(String(id).match(/^p(\d+)_/) || [])[1] || null;
const typeName = (t) => t.startsWith('excursus:') ? `Παρέκβαση: ${entName(t.slice(9))}` : entName(t);

export const boundActive = () => !!(S.bound && S.bound.items.length && S.route?.view === 'ws');
export function boundCurrent() { return S.bound ? S.bound.items[S.bound.i] : null; }

/** member id -> 'start' | 'end' | 'mid' for the exclusion being checked (empty when the loop is off). While the
 *  boundaries are being corrected: the NEW span on the page shown (same flow as the server's pipeline.entities.span). */
export function boundMembers() {
  const e = boundCurrent();
  if (!e) return new Map();
  const ed = S.bound.edit;
  if (ed && S.page) {
    const [a, z] = ordered(ed.first, ed.last), out = new Map();
    for (const b of S.page.blocks) {
      if (b.flow === 'furniture' || b.excluded) continue;
      const p = b.page, ro = b.reading_order;
      const afterA = p > a.page || (p === a.page && ro >= roOf(a)), beforeZ = p < z.page || (p === z.page && ro <= roOf(z));
      if (afterA && beforeZ) out.set(b.id, b.id === a.id ? 'start' : b.id === z.id ? 'end' : 'mid');
    }
    return out;
  }
  return new Map(e.members.map((m, k) => [m, k === 0 ? 'start' : k === e.members.length - 1 ? 'end' : 'mid']));
}

// ---- correcting the boundaries (right kind, wrong limits): the server makes it the reviewer's own entity
const roOf = (x) => x.ro ?? (S.page?.page === x.page ? (S.page.blocks.find(b => b.id === x.id) || {}).reading_order ?? 0 : 0);
const ordered = (a, b) => (a.page < b.page || (a.page === b.page && roOf(a) <= roOf(b)) ? [a, b] : [b, a]);
const at = (id) => { const b = S.page?.blocks.find(x => x.id === id); return { id, page: pageOfId(id), ro: b ? b.reading_order : null }; };
export const boundEditing = () => !!S.bound?.edit;
export function boundEdit() {
  const e = boundCurrent();
  if (!e) return;
  S.bound.edit = S.bound.edit ? null : { first: at(e.members[0]), last: at(e.members[e.members.length - 1]) };
  if (S.bound.edit) toast('Επίλεξε τμήμα και πάτα [ (αρχή εδώ) ή ] (τέλος εδώ)· ↵ αποθήκευση· αλλαγή σελίδας με ← →');
  refresh();
}
/** which = 'first' | 'last': the selected segment becomes that boundary. */
export function boundSetEdge(which) {
  if (!S.bound?.edit) return;
  if (!S.sel) return toast('Επίλεξε πρώτα ένα τμήμα στη σελίδα', { error: true });
  S.bound.edit[which] = at(S.sel);
  refresh();
}
export function boundSaveEdit() {
  const e = boundCurrent(), ed = S.bound?.edit;
  if (!e || !ed) return;
  const [a, z] = ordered(ed.first, ed.last);
  S.bound.done.add(e.key); S.bound.edit = null;
  actLater(() => api(`${B(S.book.id)}/profile/bounds`, { body: { key: e.key, first: a.id, last: z.id } }), 'Νέα όρια εξαίρεσης',
    { key: e.key, onError: () => { S.bound?.done.delete(e.key); toast('Τα νέα όρια δεν καταγράφηκαν· η εξαίρεση ξαναμπήκε στη λίστα', { error: true }); } });
  boundStep(1);
}

/** `edit`: open the boundary correction of `fromKey` at once (the «Όρια…» button of the exclusions list). */
export async function startBoundLoop(fromKey = null, edit = false) {
  const st = await api(`${B(S.book.id)}/profile`);
  const items = st.excluded.filter(e => !e.confirmed && (e.members || []).length);
  if (!items.length) return toast('Δεν υπάρχουν εξαιρέσεις προς επιβεβαίωση 🎉');
  S.typo = null;                                  // one special loop at a time
  S.bound = { items, i: Math.max(0, items.findIndex(e => e.key === fromKey)), done: new Set(), end: false };
  open();
  if (edit) boundEdit();
}

export function stopBoundLoop() { S.bound = null; refresh(); }

function open() {
  const e = boundCurrent();
  const id = S.bound.end ? e.members[e.members.length - 1] : e.members[0];
  reveal(id);                                     // centred on its page, even when it is the page already open
  if (S.page && S.page.page === pageOfId(id) && S.route?.view === 'ws') renderWorkspace();
  else go(`#/b/${S.book.id}/p/${pageOfId(id)}`);
}

export function boundStep(dir = 1) {
  const b = S.bound, n = b.items.length;
  for (let k = 1; k <= n; k++) {
    const i = (b.i + dir * k + n * k) % n;
    if (!b.done.has(b.items[i].key)) {
      if ((dir > 0 && i < b.i) || (dir < 0 && i > b.i)) toast(dir > 0 ? 'Τέλος της λίστας· συνέχεια από την αρχή' : 'Αρχή της λίστας· συνέχεια από το τέλος');
      b.i = i; b.end = false; b.edit = null; open(); return;
    }
  }
  toast('Όλες οι εξαιρέσεις κρίθηκαν 🎉 (εφαρμόζονται με την «Ενημέρωση βιβλίου»)');
  stopBoundLoop();
}

export function boundToggleEnd() { S.bound.end = !S.bound.end; open(); }

export function boundDecide(ok) {
  const e = boundCurrent();
  if (!e || S.bound.done.has(e.key)) return;
  S.bound.done.add(e.key);                        // the next exclusion opens at once; the verdict is sent behind
  actLater(() => ok ? api(`${B(S.book.id)}/profile/confirm`, { body: { keys: [e.key] } })
    : api(`${B(S.book.id)}/profile/keep`, { body: { key: e.key } }), null,
  { key: e.key, onError: () => { S.bound?.done.delete(e.key); toast('Η απόφαση δεν καταγράφηκε· η εξαίρεση ξαναμπήκε στη λίστα', { error: true }); } });
  boundStep(1);
}

/** The bar above the page while the loop is on. */
export function boundBar() {
  const b = S.bound, e = boundCurrent();
  const left = b.items.length - b.done.size;
  const p0 = pageOfId(e.members[0]), p1 = pageOfId(e.members[e.members.length - 1]);
  if (b.edit) {                                   // correcting the boundaries
    const [a, z] = ordered(b.edit.first, b.edit.last);
    return h('div', { class: 'ws-row bound-bar', role: 'region', 'aria-label': 'Διόρθωση ορίων εξαίρεσης' },
      h('b', {}, `Νέα όρια: ${typeName(e.type)}`),
      h('span', { class: 'small' }, `ΑΡΧΗ σελ. ${a.page} → ΤΕΛΟΣ σελ. ${z.page}`),
      h('span', { class: 'small mut' }, 'επίλεξε τμήμα (και σε άλλη σελίδα: ← →) και όρισέ το'),
      h('span', { class: 'spacer' }),
      h('button', { class: 'small', onclick: () => boundSetEdge('first'), title: 'Το επιλεγμένο τμήμα γίνεται η ΑΡΧΗ ([)' }, 'Αρχή εδώ', h('kbd', {}, '[')),
      h('button', { class: 'small', onclick: () => boundSetEdge('last'), title: 'Το επιλεγμένο τμήμα γίνεται το ΤΕΛΟΣ (])' }, 'Τέλος εδώ', h('kbd', {}, ']')),
      h('button', { class: 'small primary', onclick: boundSaveEdit, title: 'Αποθήκευση των νέων ορίων → επόμενη (↵). Αναιρείται.' }, 'Αποθήκευση ορίων', h('kbd', {}, '↵')),
      h('button', { class: 'small ghost', onclick: boundEdit, title: 'Ακύρωση (Esc)' }, 'Ακύρωση'));
  }
  return h('div', { class: 'ws-row bound-bar', role: 'region', 'aria-label': 'Έλεγχος ορίων εξαιρέσεων' },
    h('b', {}, `Όρια ${b.i + 1} / ${b.items.length}`), h('span', { class: 'small mut' }, `(${left} απομένουν)`),
    h('span', { class: 'chip ent' }, typeName(e.type)),
    h('span', { class: 'small' }, p0 === p1 ? `σελ. ${p0} · ${e.n} τμήματα` : `αρχή σελ. ${p0} → τέλος σελ. ${p1} · ${e.n} τμήματα`),
    p0 !== p1 ? h('button', { class: 'small', onclick: boundToggleEnd, title: 'Εναλλαγή σελίδας αρχής/τέλους (T)' },
      b.end ? '← Αρχή' : 'Τέλος →', h('kbd', {}, 'T')) : null,
    h('span', { class: 'spacer' }),
    h('button', { class: 'small', onclick: () => boundStep(-1), title: 'Προηγούμενη εξαίρεση (K)' }, '↑', h('kbd', {}, 'K')),
    h('button', { class: 'small ghost', onclick: () => boundDecide(false), title: 'Δεν είναι αυτό (π.χ. δεν είναι ερώτηση): μένει στην έξοδο (X)' }, 'Όχι αυτό', h('kbd', {}, 'X')),
    h('button', { class: 'small', onclick: boundEdit, title: 'Σωστό είδος, λάθος όρια: όρισε νέα αρχή/τέλος (D)' }, 'Όρια ✎', h('kbd', {}, 'D')),
    h('button', { class: 'small primary', onclick: () => boundDecide(true), title: 'Αρχή και τέλος σωστά (↵)' }, 'Σωστό', h('kbd', {}, '↵')),
    h('button', { class: 'small', onclick: () => boundStep(1), title: 'Επόμενη εξαίρεση χωρίς απόφαση (J)' }, '↓', h('kbd', {}, 'J')),
    h('button', { class: 'small ghost', onclick: stopBoundLoop, title: 'Έξοδος από τον έλεγχο ορίων (Esc)' }, '✕'));
}
