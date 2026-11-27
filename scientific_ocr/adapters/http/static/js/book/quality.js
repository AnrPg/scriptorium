import { cardActions, setLevel, viewButton } from '../inspector/suggestions.js';
import { openPage } from '../ui/page_overlay.js';
import { renderBook } from '../book/book.js';
import { ruleEditor } from '../book/structure.js';
import { B, api } from '../core/api.js';
import { kickPoll } from '../core/chrome.js';
import { $, clear, h, isActiveJob, section } from '../core/dom.js';
import { R, S, go, sym } from '../core/state.js';
import { SROLE_EL, infoTip } from '../core/strings.js';
import { act, modal, toast } from '../ui/feedback.js';
import { mathBox } from '../ui/math.js';
import { spellingCard } from '../book/spelling.js';

// ---------------------------------------------------------------- workload reduction: classes, bulk review, sampling
export async function tabQuality(body) {
  const C = await api(`${B(S.book.id)}/classes`), T = C.triage || {};
  body.append(h('p', { class: 'small mut' }, 'Τα ανοιχτά τμήματα ομαδοποιημένα ανά τύπο επισήμανσης, με την ακρίβεια που μετρήθηκε από τις αποφάσεις σου. ',
    'Ελέγχεις μαζικά ', infoTip('bulk'), ' ή με δειγματοληψία ', infoTip('sampling'), '. Η διαλογή ', infoTip('triage'),
    ` έχει ήδη χαμηλώσει ${T.lowered || 0} τμήματα, και ${T.accepted || 0} έγιναν δεκτά στατιστικά.`));
  const card = h('div', { class: 'card' });
  body.append(section(`Ανοιχτά τμήματα ανά επισήμανση (${C.classes.reduce((a, c) => a + c.required + c.recommended, 0)})`, card));
  if (!C.classes.length) card.append(h('div', { class: 'list-item mut' }, 'Δεν υπάρχουν ανοιχτά τμήματα. 🎉'));
  for (const c of C.classes) {
    const st = c.stats, tot = c.required + c.recommended;
    const acc = st ? `${st.n} ελέγχθηκαν · λάθος μηχανής ${(100 * st.error_rate).toFixed(0)}% (έως ${(100 * st.upper).toFixed(0)}% με 95%)` : 'δεν έχει ελεγχθεί ακόμα';
    card.append(h('div', { class: 'list-item', style: { cursor: 'default', alignItems: 'flex-start' } },
      h('div', { class: 'grow' }, h('div', { class: 'row' }, h('b', {}, R(c.reason).el), c.critical ? h('span', { class: 'chip rv-required' }, 'κρίσιμο') : null,
        c.accepted ? h('span', { class: 'chip rv-approved' }, 'δεκτό στατιστικά') : null),
        h('div', { class: 'small mut' }, `${tot} ανοιχτά (${c.required} απαιτούνται, ${c.recommended} συνιστώνται) · ${acc}`)),
      h('button', { class: 'small', onclick: () => bulkGrid(c) }, 'Μαζικός έλεγχος'),
      c.critical ? h('span', { class: 'small mut', title: 'Οι κρίσιμες επισημάνσεις ελέγχονται πάντα μία-μία' }, 'χωρίς δειγματοληψία')
        : h('button', { class: 'small', onclick: () => samplingFlow(c) }, 'Δειγματοληψία')));
  }
  await spellingCard(body);
  if ((C.rule_suggestions || []).length) {
    const rc = h('div', { class: 'card' });
    body.append(section([`Προτεινόμενοι κανόνες (${C.rule_suggestions.length}) `, infoTip('rules')], rc));
    for (const r of C.rule_suggestions) rc.append(h('div', { class: 'list-item', style: { cursor: 'default' } },
      h('span', { class: 'grow' }, sym(r.find), ' → ', sym(r.replace), h('span', { class: 'small mut' }, `  · διορθώθηκε ${r.corrections} φορές · θα άλλαζε ${r.would_change} ακόμη εμφανίσεις`)),
      h('button', { class: 'small primary', onclick: () => ruleEditor({ find: r.find, replace: r.replace, context: '', exclude: [], note: `από ${r.corrections} διορθώσεις σου` }) }, 'Προεπισκόπηση & κανόνας')));
  }
}
export function itemCard(it, checked) {
  const cb = h('input', { type: 'checkbox' }); cb.checked = checked;
  const latex = (it.chosen || {}).type === 'latex' || ['math', 'chemical_reaction'].includes(it.kind);
  const val = (it.chosen || {}).text || (it.chosen || {}).latex || '';
  const el = h('label', { class: 'grid-item' + (checked ? '' : ' off') }, h('div', { class: 'row small' }, cb, h('span', { class: 'mut' }, `σελ. ${it.page}`), h('span', { class: 'grow' }),
      h('a', { href: `#/b/${S.book.id}/p/${it.page}/${it.id}`, onclick: (e) => e.stopPropagation() }, 'άνοιγμα')),
    it.crop ? h('img', { src: `${B(S.book.id)}/${it.crop}`, loading: 'lazy' }) : null,
    val ? (latex ? mathBox(val) : h('div', { class: 'small', style: { maxHeight: '90px', overflow: 'hidden' } }, val.slice(0, 300))) : h('div', { class: 'small mut' }, 'μόνο εικόνα'));
  cb.onchange = () => el.classList.toggle('off', !cb.checked);
  el.checkbox = cb; el.itemId = it.id;
  return el;
}
export async function bulkGrid(c) {
  const items = await api(`${B(S.book.id)}/classes/${c.reason}/items?limit=300`);
  const grid = h('div', { class: 'grid2' }), cards = items.map(it => itemCard(it, true));
  cards.forEach(x => grid.append(x));
  const d = modal(`Μαζικός έλεγχος: ${R(c.reason).el} (${items.length})`, h('div', {},
    h('p', { class: 'small mut' }, 'Σύγκρινε κάθε εικόνα με το αποτέλεσμα. Ξετσέκαρε όσα είναι λάθος (θα τα διορθώσεις ξεχωριστά) και εγκρίνε τα υπόλοιπα. ', infoTip('bulk')), grid),
    [{ label: 'Άκυρο' },
     { label: 'Μόνο εικόνα στα επιλεγμένα', run: async () => { await act(() => api(`${B(S.book.id)}/bulk`, { body: { blocks: cards.filter(x => x.checkbox.checked).map(x => x.itemId), action: 'mark_image_only' } }), 'Καταγράφηκε'); renderBook(); } },
     { label: 'Εγκρίνω τα επιλεγμένα', cls: 'good', run: async () => { const r = await act(() => api(`${B(S.book.id)}/bulk`, { body: { blocks: cards.filter(x => x.checkbox.checked).map(x => x.itemId), action: 'approve' } }), null); toast(`Εγκρίθηκαν ${r.applied} τμήματα`, { undo: true }); renderBook(); } }]);
  d.style.width = 'min(1100px, 96vw)';
}
export async function samplingFlow(c) {
  const sm = await api(`${B(S.book.id)}/classes/${c.reason}/sample`, { body: { max_error: 0.05 } });
  const grid = h('div', { class: 'grid2' }), cards = sm.items.map(it => itemCard(it, true));
  cards.forEach(x => grid.append(x));
  const d = modal(`Δειγματοληψία: ${R(c.reason).el}`, h('div', {},
    h('p', { class: 'small' }, `Τυχαίο δείγμα ${sm.n} από ${sm.population} ${sm.note ? '(όλη η κατηγορία)' : ''}. Ξετσέκαρε κάθε τμήμα που είναι ΛΑΘΟΣ. `, infoTip('sampling')),
    h('p', { class: 'small mut' }, 'Αν δεν βρεις κανένα λάθος, η κατηγορία γίνεται δεκτή στατιστικά (ποσοστό λάθους ≤ 5% με 95% βεβαιότητα).'), grid),
    [{ label: 'Άκυρο' }, { label: 'Ολοκλήρωση δειγματοληψίας', cls: 'primary', run: async () => {
      const res = await act(() => api(`${B(S.book.id)}/classes/${c.reason}/accept`, { body: { results: cards.map(x => ({ block_id: x.itemId, ok: x.checkbox.checked })), max_error: 0.05 } }), null);
      if (res.passed) toast(`Δεκτή η κατηγορία: ${res.errors} λάθη σε ${res.n}, όριο λάθους ${(100 * res.upper).toFixed(1)}%`, { undo: true });
      else toast(`Δεν έγινε δεκτή: ${res.errors} λάθη σε ${res.n} (όριο ${(100 * res.upper).toFixed(1)}% > 5%). Τα λανθασμένα θέλουν διόρθωση και η κατηγορία πλήρη έλεγχο.`, { error: true });
      renderBook(); } }]);
  d.style.width = 'min(1100px, 96vw)';
}

export const structLabel = (x) => x.role === 'not_heading' || !x.level ? SROLE_EL.not_heading : `${SROLE_EL[x.role]} · επίπεδο ${x.level}`;
export async function structureReviewPanel(box) {
  const book = S.book;
  const r = await api(`${B(book.id)}/structure-review`);
  const running = (S.jobs || []).some(j => j.book === book.id && j.kind === 'structure' && isActiveJob(j));
  const withAliases = (txt, p) => (txt || '').replace(/\[?(e\d+)\]?/g, (m, a) => p.aliases && p.aliases[a] ? `«${p.aliases[a]}»` : m);
  clear(box).append(h('div', { class: 'row' }, h('h2', { class: 'grow', style: { margin: 0 } }, '2ο πέρασμα · προτάσεις μοντέλου ', infoTip('structurellm')),
    h('button', { class: 'primary', disabled: running || !S.meta.note_model, onclick: async () => { const j = await act(() => api(`${B(book.id)}/structure-review`, { body: {} }), 'Ο έλεγχος δομής μπήκε στην ουρά', false); if (j && j.id) { S.jobs.push(j); kickPoll(); structureReviewPanel(box); } } },
      running ? 'Τρέχει…' : (r.status ? 'Νέος έλεγχος με το μοντέλο' : 'Έλεγχος δομής με το μοντέλο'))));
  if (r.status === 'UNAVAILABLE') { box.append(h('p', { class: 'badge-bad' }, 'Δεν υπάρχει τοπικό μοντέλο: ' + (r.error || ''))); return; }
  if (!r.status) { box.append(h('p', { class: 'small mut' }, 'Δεν έχει τρέξει ακόμα. Το μοντέλο ρωτιέται μόνο για ό,τι έμεινε αμφίβολο μετά τους κανόνες· κάθε πρόταση την εγκρίνεις ή την απορρίπτεις εσύ.'));  return; }
  box.append(h('p', { class: 'small mut' }, `${r.model} · ${r.built_utc?.replace('T', ' ').slice(0, 16)} · ρωτήθηκε για ${r.asked} στοιχεία, συμφώνησε με την τρέχουσα δομή σε ${r.agreed_with_current}` +
    (r.invalid_answers ? ` · ${r.invalid_answers} άκυρες απαντήσεις απορρίφθηκαν` : '') + (r.failed?.length ? ` · ${r.failed.length} απέτυχαν` : '') +
    (r.canonical_built && book.built && r.canonical_built !== book.built ? ' · το βιβλίο άλλαξε από τότε: ξανατρέξε για ενημερωμένες προτάσεις' : '')));
  if (!r.proposals.length) { box.append(h('div', { class: 'list-item mut' }, 'Καμία ανοιχτή πρόταση.')); return; }
  for (const p of r.proposals) {
    const unanimous = p.votes === p.samples;
    box.append(h('div', { class: 'cand' },
      h('div', { class: 'row' }, h('b', { class: 'grow link-like', title: 'Προεπισκόπηση στη σελίδα (παράθυρο)', onclick: () => openPage(p.page, p.block_id) }, p.text || '(χωρίς κείμενο)'), h('span', { class: 'small mut' }, `σελ. ${p.page}`),
        h('span', { class: 'chip' + (unanimous ? '' : ' warn'), title: 'Πόσες από τις ανεξάρτητες απαντήσεις του μοντέλου συμφώνησαν' }, `${p.votes}/${p.samples} συμφωνία`)),
      h('div', { class: 'small' }, h('span', { class: 'mut' }, 'Τώρα: '), structLabel(p.current), h('span', { class: 'mut' }, '  →  Πρόταση: '), h('b', {}, structLabel(p.proposed)),
        p.proposed.parent_text ? h('span', { class: 'mut' }, ` κάτω από «${p.proposed.parent_text}»`) : (p.proposed.level === 1 ? h('span', { class: 'mut' }, ' (ανώτατο επίπεδο)') : null)),
      h('div', { class: 'small', style: { marginTop: '4px' } }, withAliases(p.reasons.find(Boolean) || '', p)),
      (p.members || []).length > 1 ? h('div', { class: 'small', style: { marginTop: '6px' } }, h('b', {}, `Ίδιο στυλ — προτείνεται μαζί (${p.members.length}): `),
        p.members.map((m, i) => h('label', { class: 'row', style: { margin: '2px 0' } },
          (() => { const c = h('input', { type: 'checkbox', 'data-m': m }); c.checked = true; return c; })(), p.member_texts[i] || m))) : null,
      (p.consequences || []).length ? h('div', { class: 'small mut', style: { marginTop: '4px' } }, h('b', {}, 'Συνέπειες (προσομοίωση): '),
        p.consequences.map(x => `«${x.text}» ${x.before ?? '—'}→${x.after ?? '—'}`).join(' · ')) : null,
      p.round > 1 ? h('div', { class: 'small mut' }, `Γύρος ${p.round}: προέκυψε από τις προτάσεις του προηγούμενου γύρου.`) : null,
      h('details', {}, h('summary', { class: 'small mut' }, 'Τι είδε το μοντέλο'), h('pre', { class: 'preview-pre' }, p.prompt),
        h('div', { class: 'small mut' }, 'Όλες οι απαντήσεις: ' + p.reasons.map((x, i) => `(${i + 1}) ${withAliases(x, p)} [${p.confidence[i]}]`).join('  '))),
      cardActions([
        h('button', { class: 'small primary', onclick: async (ev) => {
          const card = ev.currentTarget.closest('.cand'), boxes = [...card.querySelectorAll('input[data-m]')];
          const members = boxes.length ? boxes.filter(c => c.checked).map(c => c.dataset.m) : null;
          await act(() => api(`${B(book.id)}/structure-review/${p.key}/accept`, { body: { members } }), 'Η δομή ενημερώθηκε· το μοντέλο ξανατρέχει αυτόματα'); } }, 'Αποδοχή'),
        setLevel(p.block_id),
        h('button', { class: 'small dismiss', onclick: async () => { await act(() => api(`${B(book.id)}/suggestions/${p.key}/dismiss`, { body: {} }), 'Η πρόταση απορρίφθηκε'); } }, 'Απόρριψη')],
        viewButton(() => openPage(p.page, p.block_id), 'Άνοιγμα στη σελίδα (παράθυρο)'))));
  }
}
