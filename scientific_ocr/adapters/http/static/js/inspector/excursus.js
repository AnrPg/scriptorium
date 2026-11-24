// Excursus (παρέκβαση): intercalated stretches of the book — a historical note, applications, an appendix of
// another science — on one or many pages. Marked by its two ENDS (first / last segment, on any pages), by whole
// pages, or from a selection; read in its own flow (a course paragraph continues across it); placed in the output
// where the reviewer chooses (pipeline/excursus.py, pipeline/flow.py).
import { B, api } from '../core/api.js';
import { h } from '../core/dom.js';
import { S, go } from '../core/state.js';
import { act, modal, toast } from '../ui/feedback.js';
import { renderWorkspace, selectBlock } from '../workspace/workspace.js';

const START_KEY = (book) => `ocr.excursusStart.${book}`;
export const excTypeName = (t) => (S.meta.excursus_types || {})[t] || t || 'Παρέκβαση';
export const excName = (x) => x ? (x.name ? `${excTypeName(x.type)} «${x.name}»` : excTypeName(x.type)) : '';
export const placementName = (p) => (S.meta.excursus_placements || {})[p] || p;

// built excursus (members known) or, while the book waits for a rebuild, the one the reviewer just marked
export const excursusById = (id) => (S.book.excursus || []).find(x => x.id === id) || (S.book.excursus_marked || []).find(x => x.id === id);
export const excursusOf = (blk) => blk && blk.excursus ? excursusById(blk.excursus) : null;

// ---------------------------------------------------------------- start / end marking (spans pages)
export function pendingStart() {
  if (!S.book) return null;
  try { return JSON.parse(localStorage.getItem(START_KEY(S.book.id)) || 'null'); } catch { return null; }
}
function setStart(v) {
  try { v ? localStorage.setItem(START_KEY(S.book.id), JSON.stringify(v)) : localStorage.removeItem(START_KEY(S.book.id)); } catch {}
}
export function startHere(blk) {
  setStart({ id: blk.id, page: blk.page, text: (blk.chosen?.text || '').slice(0, 60) });
  toast(`Αρχή παρέκβασης: σ. ${blk.page}. Πήγαινε στο τελευταίο τμήμα της (σε όποια σελίδα) και πάτα «Τέλος παρέκβασης εδώ».`);
  renderWorkspace();
}
export function cancelStart() { setStart(null); renderWorkspace(); }
export function endHere(blk) {
  const st = pendingStart();
  if (!st) return;
  excursusDialog({ first: st.id, last: blk.id }, `σ. ${Math.min(st.page, blk.page)}–${Math.max(st.page, blk.page)}`, () => setStart(null));
}

// type, name and placement: one dialog for every way of marking
export function excursusDialog(target, where, done, existing) {
  const types = Object.entries(S.meta.excursus_types || {}), places = Object.entries(S.meta.excursus_placements || {});
  const typeSel = h('select', {}, types.map(([k, v]) => h('option', { value: k }, v)));
  const placeSel = h('select', {}, places.map(([k, v]) => h('option', { value: k }, v)));
  const name = h('input', { type: 'text', placeholder: 'π.χ. Ο Πάουλινγκ και η α-έλικα (προαιρετικό)', value: existing?.name || '' });
  typeSel.value = existing?.type || 'history'; placeSel.value = existing?.placement || 'inplace';
  modal(existing ? `Παρέκβαση · ${where}` : `Νέα παρέκβαση · ${where}`, h('div', { class: 'col' },
    h('p', { class: 'small mut' }, 'Ό,τι υπάρχει από το πρώτο έως το τελευταίο τμήμα ανήκει στην παρέκβαση, σε όσες σελίδες κι αν εκτείνεται. '
      + 'Διαβάζεται σε δική της ροή: η παράγραφος του κυρίως κειμένου που διακόπηκε συνεχίζει μετά από αυτήν και στην έξοδο εμφανίζεται ενωμένη.'),
    h('label', {}, 'Τύπος ', typeSel), h('label', {}, 'Όνομα ', name), h('label', {}, 'Στην έξοδο ', placeSel)),
    [{ label: 'Άκυρο' }, { label: existing ? 'Αποθήκευση' : 'Σήμανση', cls: 'primary', run: async () => {
      const body = { type: typeSel.value, name: name.value, placement: placeSel.value, ...target };
      const url = existing ? `${B(S.book.id)}/excursus/${existing.id}` : target.key ? `${B(S.book.id)}/excursus-suggestions/${target.key}/accept` : `${B(S.book.id)}/excursus`;
      await act(() => api(url, { body }), existing ? 'Η παρέκβαση ενημερώθηκε' : 'Η παρέκβαση σημειώθηκε');
      if (done) done();
    } }]);
}

// banner while an excursus is being marked across pages
export function excursusBanner() {
  const st = pendingStart();
  if (!st) return null;
  return h('div', { class: 'version v-pending excursus-banner row small' },
    h('span', { class: 'grow' }, h('b', {}, 'Παρέκβαση σε εξέλιξη '), `από σ. ${st.page}${st.text ? ` («${st.text}…»)` : ''}. Επίλεξε το τελευταίο της τμήμα → «Τέλος παρέκβασης εδώ».`),
    h('button', { class: 'small ghost', onclick: () => { go(`#/b/${encodeURIComponent(S.book.id)}/p/${st.page}/${st.id}`); } }, 'Στην αρχή'),
    h('button', { class: 'small ghost', onclick: cancelStart }, 'Άκυρο'));
}

// ---------------------------------------------------------------- the segment card row
export function excursusEditor(blk) {
  const x = excursusOf(blk), st = pendingStart();
  return h('div', { class: 'col' },
    h('div', { class: 'small mut' }, 'Παρέκβαση = σελίδες ή τμήματα που παρεμβάλλονται στο κυρίως κείμενο (ιστορικό σημείωμα, εφαρμογές, παράρτημα άλλης επιστήμης…). '
      + 'Ορίζεται με το πρώτο και το τελευταίο της τμήμα· μπορεί να εκτείνεται σε πολλές σελίδες.'),
    x ? h('div', { class: 'actions' },
      h('button', { onclick: () => excursusDialog({}, `σ. ${(x.pages || []).join('–') || '?'}`, null, x) }, 'Τύπος / όνομα / θέση…'),
      h('button', { onclick: () => act(() => api(`${B(S.book.id)}/excursus/${x.id}`, { body: { first: blk.id } }), 'Νέα αρχή παρέκβασης') }, 'Αρχίζει εδώ'),
      h('button', { onclick: () => act(() => api(`${B(S.book.id)}/excursus/${x.id}`, { body: { last: blk.id } }), 'Νέο τέλος παρέκβασης') }, 'Τελειώνει εδώ'),
      h('button', { class: 'ghost danger', onclick: () => act(() => api(`${B(S.book.id)}/excursus/${x.id}/remove`, { body: {} }), 'Η παρέκβαση αφαιρέθηκε') }, 'Δεν είναι παρέκβαση'))
    : h('div', { class: 'actions' },
      st && st.id !== blk.id ? h('button', { class: 'primary', onclick: () => endHere(blk) }, `Τέλος παρέκβασης εδώ (αρχή σ. ${st.page})`) : null,
      h('button', { onclick: () => startHere(blk) }, st ? 'Νέα αρχή εδώ' : 'Αρχή παρέκβασης εδώ'),
      h('button', { onclick: () => excursusDialog({ first: blk.id, last: blk.id }, `σ. ${blk.page}`) }, 'Μόνο αυτό το τμήμα…'),
      h('button', { onclick: () => excursusDialog({ pages: [blk.page, blk.page] }, `σ. ${blk.page}`) }, 'Όλη η σελίδα…')));
}
export const excursusValue = (blk) => {
  const x = excursusOf(blk), st = pendingStart();
  if (x) return h('span', { class: 'chip exc', title: `${placementName(x.placement)} · σ. ${(x.pages || []).join(', ')}` }, `⧉ ${excName(x)}`.slice(0, 40));
  return h('span', { class: 'mut' }, st ? `όχι · σήμανση σε εξέλιξη από σ. ${st.page}` : 'όχι');
};

// ---------------------------------------------------------------- page list: flow context
const strandName = (key) => {
  if (!key || key === 'main') return 'κυρίως κείμενο';
  if (key.startsWith('x:')) return excName(excursusById(key.slice(2))) || 'παρέκβαση';
  return 'ίδιο πλαίσιο';
};
// «↳ συνέχεια» says WHICH text it continues: the course text (maybe pages earlier, after an excursus) or the box /
// excursus it is in — a page can hold both
export function continuationMark(b) {
  const from = b.relations?.continues_from;
  if (!from) return null;
  const pg = +((/^p(\d+)_/.exec(from) || [])[1] || b.page);
  const key = b.relations.continues_strand, across = (b.relations.interrupted_by || []).filter(x => String(x).startsWith('excursus:'));
  const ctx = key && key !== 'main' ? strandName(key) : across.length ? `κυρίως κείμενο, μετά από ${excName(excursusById(across[0].slice(9))) || 'παρέκβαση'}` : null;
  return { text: `↳ συνέχεια${pg !== b.page ? ` σ. ${pg}` : ''}${ctx ? ` · ${ctx}` : ''}`, cls: 'lv-cont', title: `συνέχεια παραγράφου (${strandName(key)}) από σ. ${pg}` };
}
export function continuesInMark(b) {          // the broken end: where the paragraph goes on (another page)
  const to = b.relations?.continues_in;
  const pg = to && +((/^p(\d+)_/.exec(to) || [])[1] || b.page);
  return to && pg !== b.page ? h('a', { class: 'chip cont-to', href: `#/b/${encodeURIComponent(S.book.id)}/p/${pg}/${to}`, title: 'η παράγραφος συνεχίζει εκεί', onclick: (e) => e.stopPropagation() }, `⤼ σ. ${pg}`) : null;
}
export function excursusChip(b) {
  const x = excursusOf(b);
  if (!x) return null;
  const first = (x.first_id || x.first) === b.id;
  return h('span', { class: 'chip exc', title: `Παρέκβαση: ${excName(x)} · ${placementName(x.placement)}` }, first ? `⧉ ${excName(x).slice(0, 24)}` : '⧉');
}

// ---------------------------------------------------------------- book: list of excursus + suggestions
export function excursusCard() {
  const book = S.book, list = book.excursus || [], marked = book.excursus_marked || [], sugg = book.excursus_suggestions || [];
  const built = new Set(list.map(x => x.id)), waiting = marked.filter(x => !built.has(x.id));
  const types = [...new Set(list.map(x => x.type))];
  const openAt = (id, page) => go(`#/b/${encodeURIComponent(book.id)}/p/${page}/${id}`);
  return h('div', { class: 'card' }, h('h2', {}, `Παρεκβάσεις (${list.length})`),
    h('p', { class: 'small mut' }, 'Σελίδες ή τμήματα που παρεμβάλλονται στο κυρίως κείμενο. Σήμανση: από το τμήμα (Ιδιότητες → Παρέκβαση), από πολλαπλή επιλογή ή από την καρτέλα «Σελίδες».'),
    types.length ? h('div', { class: 'row small' }, 'Θέση για όλες:', types.map(t => h('span', { class: 'row' }, h('b', {}, excTypeName(t)),
      Object.entries(S.meta.excursus_placements || {}).map(([k, v]) => h('button', { class: 'small ghost', onclick: () => act(() => api(`${B(book.id)}/excursus/place-type`, { body: { type: t, placement: k } }), `${excTypeName(t)}: ${v}`) }, v))))) : null,
    list.map(x => h('div', { class: 'list-item' + (x.broken ? ' bad' : '') },
      h('span', { class: 'chip exc' }, '⧉'), h('span', { class: 'grow' }, h('b', {}, excName(x)), ` · σ. ${x.pages.length ? x.pages[0] + (x.pages.length > 1 ? '–' + x.pages[x.pages.length - 1] : '') : '?'} · ${x.members.length} τμήματα`,
        x.broken ? h('span', { class: 'badge-bad' }, ' τα άκρα δεν βρέθηκαν') : null),
      h('span', { class: 'small mut' }, placementName(x.placement)),
      h('button', { class: 'small ghost', onclick: () => openAt(x.first_id || x.first, x.pages[0] || 1) }, 'Άνοιγμα'),
      h('button', { class: 'small ghost', onclick: () => excursusDialog({}, `σ. ${x.pages.join('–')}`, null, x) }, 'Αλλαγή…'),
      h('button', { class: 'small ghost danger', onclick: () => act(() => api(`${B(book.id)}/excursus/${x.id}/remove`, { body: {} }), 'Η παρέκβαση αφαιρέθηκε') }, 'Αφαίρεση'))),
    waiting.length ? h('p', { class: 'small mut' }, `${waiting.length} ακόμα σε αναμονή: φαίνονται με την επόμενη «Ενημέρωση βιβλίου».`) : null,
    sugg.length ? [h('h3', {}, `Προτάσεις (${sugg.length})`), sugg.map(s => h('div', { class: 'list-item' },
      h('span', { class: 'grow small' }, s.explanation_el, h('span', { class: 'mut' }, ` (${s.segments} τμήματα)`)),
      h('button', { class: 'small ghost', onclick: () => openAt(s.first, s.pages[0]) }, 'Άνοιγμα'),
      h('button', { class: 'small good', onclick: () => excursusDialog({ key: s.key }, `σ. ${s.pages.join('–')}`) }, 'Είναι παρέκβαση…'),
      h('button', { class: 'small dismiss', onclick: () => act(() => api(`${B(book.id)}/suggestions/${s.key}/dismiss`, { body: {} }), 'Η πρόταση απορρίφθηκε') }, 'Όχι')))] : null);
}
export { selectBlock };
