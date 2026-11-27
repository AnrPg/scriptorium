import { B, api } from '../core/api.js';
import { refresh } from '../core/chrome.js';
import { h, section } from '../core/dom.js';
import { S, go } from '../core/state.js';
import { infoTip } from '../core/strings.js';
import { entName } from '../book/entities.js';
import { act, modal } from '../ui/feedback.js';
import { startBoundLoop } from '../inspector/boundloop.js';
import { openPage } from '../ui/page_overlay.js';

// ---------------------------------------------------------------- output profile (core/profiles.py, pipeline/profile.py)
// Settings: choose what the book is prepared FOR. Review: confirm the BOUNDARIES of the profile's automatic exclusions
// (never their content) and accept suggested ones. Everything here is an ordinary undoable event.
export const PROFILE_EL = { reading: 'Ανάγνωση', rag: 'RAG' };
const typeName = (t) => t.startsWith('excursus:') ? `Παρέκβαση: ${entName(t.slice(9))}` : entName(t);
const pagesOf = (e) => (e.pages || []).length ? `σελ. ${e.pages[0]}${e.pages.length > 1 ? '–' + e.pages[e.pages.length - 1] : ''}` : '';

/** Book head: which profile the book is prepared with. */
export function profileChip(b) {
  const p = b.output_profile || 'reading';
  return h('a', { class: 'chip', href: `#/b/${b.id}/settings`, title: 'Προφίλ εξόδου (αλλαγή στις Ρυθμίσεις)' }, `Προφίλ: ${PROFILE_EL[p] || p}`);
}

export async function profileSettingsCard(body) {
  const b = S.book, card = h('div', { class: 'card', style: { padding: '16px', maxWidth: '560px', marginBottom: '16px' } });
  body.append(card);
  const st = await api(`${B(b.id)}/profile`);
  card.append(...[h('h2', {}, 'Προφίλ εξόδου ', infoTip('profile')),
    ...st.available.map(p => {
      const r = h('input', { type: 'radio', name: 'oprof' }); r.checked = st.current === p.name;
      r.onchange = () => switchProfile(st, p, () => { r.checked = false; card.querySelectorAll('input[name=oprof]').forEach(x => { x.checked = x.dataset.p === st.current; }); });
      r.dataset.p = p.name;
      const s = p.settings;
      const what = p.name === 'reading' ? 'Όλο το περιεχόμενο, με εικόνες· όλες οι μορφές εξαγωγής.'
        : `Εκτός: ${s.exclude.map(x => entName(x)).join(', ')}. Εικόνες: μόνο λεζάντες. Τίτλοι ως ${s.heading_depth} επίπεδα. Εξαγωγή: Markdown + κομμάτια JSONL.`;
      return h('label', { class: 'row small', style: { cursor: 'pointer', alignItems: 'flex-start', margin: '8px 0' } }, r,
        h('div', { style: { flex: '1', minWidth: '0' } }, h('b', {}, p.el), h('div', { class: 'mut' }, what)));
    }),
    decisionsByProfile(st),
    h('div', { class: 'row small', style: { marginTop: '10px', alignItems: 'flex-start' } },
      h('div', { style: { flex: '1', minWidth: '0' } }, h('b', {}, 'Προεπεξεργασία ανά προφίλ'),
        h('div', { class: 'mut' }, 'Κάθε προφίλ έχει έτοιμη τη δική του εκδοχή (π.χ. RAG: χωρίς μορφοποίηση, λεζάντες στο τέλος της ενότητας)· ο έλεγχος δείχνει την εκδοχή του τρέχοντος. Γίνεται μόνη της μετά το OCR και μετά από κάθε ενημέρωση· εδώ την ξανατρέχεις για όλα.')),
      h('button', { class: 'small', onclick: () => act(() => api(`${B(b.id)}/profile/preprocess`, { body: {} }), 'Προεπεξεργασία όλων των προφίλ: το τρέχον πρώτο, τα άλλα στο παρασκήνιο', false) }, 'Ξανά για όλα')),
    enginePolicy(st),
    st.awaiting_rebuild ? h('p', { class: 'note small' }, 'Η αλλαγή εφαρμόζεται με την επόμενη «Ενημέρωση βιβλίου».') : null,
    st.built === 'rag' ? h('p', { class: 'small mut' }, `Εκτός εξόδου λόγω προφίλ: ${st.excluded_segments} τμήματα · `,
      `${st.pending} αυτόματες εξαιρέσεις προς επιβεβαίωση · ${st.text_only_loss} τμήματα υπάρχουν μόνο ως εικόνα (θέλουν κείμενο).`) : null].filter(Boolean));
}

// Decisions belong to the profile they were taken under (core/scopes.py). A switch asks whether the new profile
// starts with the current one's decisions or keeps its own; nothing is undone either way — switching back brings
// that profile's decisions back. Copying can also be done later, at any time (one undoable event).
const nameOf = (st, n) => (st.available.find(p => p.name === n) || {}).el || PROFILE_EL[n] || n;
function switchProfile(st, p, cancel) {
  const from = st.current, mine = (st.decisions || {})[from] || 0, theirs = (st.decisions || {})[p.name] || 0;
  const go = (carry) => act(() => api(`${B(S.book.id)}/profile`, { body: { profile: p.name, carry } }),
    `Προφίλ: ${p.el}${carry === 'copy' ? ' — με τις αποφάσεις του προηγούμενου' : ' — ξεχωριστές αποφάσεις'}`).then(refresh);
  const d = modal(`Αλλαγή προφίλ: «${nameOf(st, from)}» → «${p.el}»`, h('div', { class: 'col small' },
    h('p', {}, 'Κάθε απόφασή σου ανήκει στο προφίλ όπου την πήρες, και η εξαγωγή ακολουθεί τις αποφάσεις του προφίλ της. Τίποτα δεν αναιρείται: αν γυρίσεις στο «', nameOf(st, from), '», οι αποφάσεις του είναι όλες εκεί.'),
    h('ul', {},
      h('li', {}, h('b', {}, 'Μεταφορά: '), `το «${p.el}» ξεκινά με τις ${mine} αποφάσεις του «${nameOf(st, from)}»${theirs ? ` (και κρατά τις ${theirs} δικές του· στο ίδιο τμήμα ισχύει η πιο πρόσφατη)` : ''}.`),
      h('li', {}, h('b', {}, 'Ξεχωριστές: '), theirs ? `το «${p.el}» συνεχίζει με τις ${theirs} δικές του αποφάσεις.` : `το «${p.el}» ξεκινά χωρίς αποφάσεις (μόνο το αποτέλεσμα των μηχανών).`)),
    h('p', { class: 'mut' }, 'Μπορείς να αντιγράψεις αποφάσεις από άλλο προφίλ και αργότερα (Ρυθμίσεις → Προφίλ εξόδου).')),
    [{ label: 'Άκυρο', run: () => { cancel(); } },
     { label: 'Ξεχωριστές αποφάσεις', run: () => go('separate') },
     { label: `Μεταφορά (${mine})`, cls: 'primary', run: () => go('copy') }]);
  d.addEventListener('cancel', cancel);
}
function decisionsByProfile(st) {
  const others = st.available.filter(p => p.name !== st.current);
  return h('div', { class: 'small', style: { marginTop: '10px' } },
    h('b', {}, 'Αποφάσεις ανά προφίλ'),
    h('div', { class: 'mut' }, st.available.map(p => `${p.el}: ${(st.decisions || {})[p.name] || 0}`).join(' · '),
      ' — η εξαγωγή ακολουθεί τις αποφάσεις του τρέχοντος προφίλ.'),
    h('div', { class: 'row', style: { marginTop: '6px' } }, others.filter(p => (st.decisions || {})[p.name]).map(p =>
      h('button', { class: 'small', title: `Οι αποφάσεις του «${p.el}» ισχύουν και εδώ (στο ίδιο τμήμα ισχύει η πιο πρόσφατη). Αναιρείται.`,
        onclick: () => act(() => api(`${B(S.book.id)}/profile/copy`, { body: { from: p.name } }), `Αντιγράφηκαν οι αποφάσεις του «${p.el}»`).then(refresh) },
        `Αντιγραφή αποφάσεων από «${p.el}» (${st.decisions[p.name]})`))));
}

/** Marker-primary: disagreements of the engines about HOW a text reads are not asked about (lost text, equations,
 *  tables still are). A per-book override of the profile's default (undoable event). */
function enginePolicy(st) {
  const cur = (st.overrides || {}).engine_policy || (st.available.find(p => p.name === st.current) || {}).settings?.engine_policy;
  const cb = h('input', { type: 'checkbox' }); cb.checked = cur === 'marker_primary';
  cb.onchange = () => act(() => api(`${B(S.book.id)}/profile`, { body: { profile: st.current,
    overrides: { ...(st.overrides || {}), engine_policy: cb.checked ? 'marker_primary' : 'cross_check' } } }),
    cb.checked ? 'Το Marker κερδίζει στις διαφωνίες' : 'Διασταύρωση μηχανών').then(refresh);
  return h('label', { class: 'row small', style: { marginTop: '10px', alignItems: 'flex-start' } }, cb,
    h('div', { style: { flex: '1', minWidth: '0' } }, h('b', {}, 'Το Marker κερδίζει στις διαφωνίες κειμένου'),
      h('div', { class: 'mut' }, 'Διαφορές στο πώς διαβάστηκε μια λέξη ή ένα σύμβολο δεν ζητούν έλεγχο (τα τυπογραφικά τα πιάνει η Ορθογραφία). Ζητούν πάντα έλεγχο: κείμενο που μπορεί να χάθηκε, εξισώσεις, πίνακες.')));
}

/** Review tab: the profile's automatic exclusions waiting for a boundary confirmation, and its suggestions. */
export async function profileReviewCard(body) {
  const b = S.book;
  if ((b.output_profile || 'reading') === 'reading') return;
  const st = await api(`${B(b.id)}/profile`);
  const pending = st.excluded.filter(e => !e.confirmed && e.n), sugg = st.suggested || [];
  if (!pending.length && !sugg.length) return;
  const card = h('div', { class: 'card', style: { padding: '12px' } });
  body.append(section(pending.length ? `Εξαιρέσεις του προφίλ: επιβεβαίωσε τα όρια (${pending.length})` : `Προτάσεις εξαίρεσης του προφίλ (${sugg.length})`, card));
  // a click on the row (not on its buttons) shows the exclusion's first segment selected on its page, in an overlay
  const peek = (e) => (ev) => { if (!ev.target.closest('button, a, input, select')) openPage((e.pages || [1])[0], (e.members || [])[0]); };
  const view = (e) => h('button', { class: 'small ghost', title: 'Άνοιγμα στη σελίδα με τα όρια σχεδιασμένα (και συνέχεια με J/K)',
    onclick: () => (e.members || []).length && !e.confirmed && pending.includes(e) ? startBoundLoop(e.key) : go(`#/b/${b.id}/p/${(e.pages || [1])[0]}/${(e.members || [])[0] || ''}`) }, 'Δες');
  if (pending.length) {
    card.append(...[h('div', { class: 'row' }, h('span', { class: 'grow small mut' }, 'Τι εξαιρεί το προφίλ ', infoTip('profile')),
      h('button', { class: 'primary', onclick: () => startBoundLoop(), title: 'Μία-μία στη σελίδα τους, με τα όρια σχεδιασμένα (↵ σωστό · X όχι αυτό · J/K)' }, 'Έλεγχος ορίων ▶'),
      h('button', { onclick: () => act(() => api(`${B(b.id)}/profile/confirm`, { body: {} }), `Επιβεβαιώθηκαν ${pending.length}`).then(refresh) }, `Όλα σωστά (${pending.length})`)),
      h('p', { class: 'small mut' }, 'Δεν χρειάζεται να διαβάσεις το περιεχόμενο: δες μόνο ότι η αρχή και το τέλος είναι της ερώτησης/του πλαισίου και όχι του κυρίως κειμένου.')]);
    for (const e of pending) {
      card.append(h('div', { class: 'list-item', style: { alignItems: 'flex-start' }, title: 'Προεπισκόπηση στη σελίδα (παράθυρο)', onclick: peek(e) },
        h('span', { class: 'chip ent' }, typeName(e.type)), h('span', { class: 'small mut', style: { width: '70px', flex: 'none' } }, pagesOf(e)),
        h('div', { class: 'grow small' }, h('div', {}, h('span', { class: 'mut' }, 'αρχή: '), (e.first || '').replace(/[*#]/g, '').slice(0, 110)),
          h('div', {}, h('span', { class: 'mut' }, 'τέλος: '), (e.last || '').replace(/[*#]/g, '').slice(-110)),
          h('div', { class: 'mut' }, `${e.n} τμήματα`)),
        view(e),
        h('button', { class: 'small', onclick: () => act(() => api(`${B(b.id)}/profile/confirm`, { body: { keys: [e.key] } }), 'Επιβεβαιώθηκε').then(refresh) }, 'Σωστό'),
        h('button', { class: 'small', title: 'Σωστό είδος, λάθος όρια: άνοιξε τη σελίδα και όρισε νέα αρχή/τέλος', onclick: () => startBoundLoop(e.key, true) }, 'Όρια…'),
        h('button', { class: 'small ghost', title: 'Δεν είναι αυτό (π.χ. δεν είναι ερώτηση): μένει στην έξοδο', onclick: () => act(() => api(`${B(b.id)}/profile/keep`, { body: { key: e.key } }), 'Μένει στην έξοδο').then(refresh) }, 'Όχι αυτό')));
    }
  }
  if (sugg.length) {
    card.append(h('details', {}, h('summary', {}, `Προτάσεις εξαίρεσης χωρίς βέβαιο όριο (${sugg.length}) — μένουν στην έξοδο μέχρι να τις δεχτείς`),
      sugg.map(e => h('div', { class: 'list-item', title: 'Προεπισκόπηση στη σελίδα (παράθυρο)', onclick: peek(e) },
        h('span', { class: 'chip ent' }, typeName(e.type)), h('span', { class: 'small mut', style: { width: '70px', flex: 'none' } }, pagesOf(e)),
        h('span', { class: 'grow small' }, (e.label || '').replace(/[*#]/g, '').slice(0, 80), h('span', { class: 'mut' }, ` · ${(e.members || []).length} τμήματα`)),
        view(e),
        h('button', { class: 'small', onclick: () => act(() => api(`${B(b.id)}/profile/exclude`, { body: { keys: [e.key] } }), 'Εξαιρέθηκε').then(refresh) }, 'Εξαίρεσε')))));
  }
}
