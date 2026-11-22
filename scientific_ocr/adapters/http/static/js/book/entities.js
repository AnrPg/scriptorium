import { cardActions, viewButton } from '../inspector/suggestions.js';
import { openPage } from '../ui/page_overlay.js';
import { B, api } from '../core/api.js';
import { kickPoll } from '../core/chrome.js';
import { clear, h, isActiveJob } from '../core/dom.js';
import { S, go } from '../core/state.js';
import { infoTip } from '../core/strings.js';
import { dropdown } from '../inspector/multi.js';
import { act, toast } from '../ui/feedback.js';
import { clearSelection, renderWorkspace } from '../workspace/workspace.js';

// ---------------------------------------------------------------- entity classification (types in families)
// The taxonomy comes from the server (core.vocabulary via meta.entity_types): one source for every screen.
// A category is a type ('example') or a whole family ('family:practice') when only the coarse kind is known.
// Every verdict can be changed again at any time (the latest event wins; undo works as everywhere).
export const ENT = () => S.meta.entity_types;
export const entName = (t) => {
  if (!t) return '';
  if (t.startsWith('family:')) { const f = ENT().families[t.slice(7)]; return f ? `${f.el} (γενικά)` : t; }
  return (ENT().types[t] || { el: t }).el;
};
export const famOf = (t) => !t ? null : t.startsWith('family:') ? t.slice(7) : (ENT().types[t] || {}).family;
export const famName = (f) => (ENT().families[f] || { el: f || 'άλλο' }).el;

// one picker everywhere: per family, "the family in general" first, then its types; the current one marked
export function entityPicker(anchor, onPick, current) {
  dropdown(anchor, Object.entries(ENT().families).map(([f, fl]) => ({ title: fl.el,
    items: [{ label: `${fl.el} — γενικά (χωρίς λεπτομέρεια)`, hint: current === `family:${f}` ? '✓ τώρα' : `family:${f}`, value: `family:${f}` },
      ...Object.entries(ENT().types).filter(([, v]) => v.family === f)
        .map(([k, v]) => ({ label: v.el, hint: current === k ? '✓ τώρα' : k, value: k }))] })), (it) => onPick(it.value),
    h('div', { class: 'small mut', style: { padding: '4px 8px' } }, 'Κατηγορία (τύπος ή ολόκληρη οικογένεια)'));
}

const post = (path, body, msg) => act(() => api(`${B(S.book.id)}${path}`, { body }), msg);
export const setCategory = (keys, type) => post('/entities/confirm', { keys, type }, type ? `Κατηγορία: ${entName(type)}` : `Επιβεβαιώθηκαν ${keys.length}`);
export const assignCategory = (blocks, type) => post('/entities/assign', { blocks, type }, `Κατηγορία: ${entName(type)}`);
export const removeCategory = (key) => post(`/entities/${key}/remove`, {}, 'Η κατηγορία αφαιρέθηκε');

// compact chip for list rows: the innermost category of a segment (head = the segment that starts the entity)
export function entityChip(b) {
  // layout-engine kinds (figure, table, equation…) already show as the coloured dot: only categories add a chip
  const es = (b.entities || []).filter(e => !(e.family === 'insert' && e.source === 'detected' && !e.verified));
  const e = es[es.length - 1];
  if (!e) return null;
  return h('span', { class: `chip ent fam-${e.family || 'x'}${e.verified ? ' ent-ok' : ''}`,
    title: `${famName(e.family)} · ${entName(e.type)} · ${e.source === 'human' ? 'δική σου κατηγορία' : e.verified ? 'επιβεβαιωμένη' : 'εντοπίστηκε (μη επιβεβαιωμένη)'}` + (es.length > 1 ? ` · μέσα σε: ${es.slice(0, -1).map(x => entName(x.type)).join(' › ')}` : '') },
    (e.head ? '' : '· ') + entName(e.type) + (e.verified ? ' ✓' : ''));
}

// inspector: the categories of the selected segment, each changeable / confirmable / removable, plus a new one
export function segmentEntities(blk) {
  const es = blk.entities || [];
  const wrap = h('div', { class: 'ent-card' });
  if (!es.length) wrap.append(h('span', { class: 'small mut' }, 'καμία ', infoTip('segentity')));
  for (const e of es) {
    const btn = h('button', { class: 'small', onclick: (ev) => entityPicker(ev.currentTarget, (t) => setCategory([e.key], t), e.type) }, 'Αλλαγή ▾');
    wrap.append(h('div', { class: 'cand' }, h('div', { class: 'row' },
      h('span', { class: `chip ent fam-${e.family || 'x'}${e.verified ? ' ent-ok' : ''}` }, entName(e.type)),
      h('span', { class: 'small mut grow' }, `${famName(e.family)} · ${e.source === 'human' ? 'δική σου' : e.verified ? 'επιβεβαιωμένη' : 'εντοπίστηκε'}${e.head ? '' : ' · μέλος'}`),
      e.verified ? null : h('button', { class: 'small primary', onclick: () => setCategory([e.key], null) }, 'Επιβεβαίωση'),
      btn, h('button', { class: 'small dismiss', title: e.source === 'human' ? 'Αφαίρεση της κατηγορίας σου' : 'Δεν είναι αυτό (μετράει στα στατιστικά του κανόνα)', onclick: () => removeCategory(e.key) }, e.source === 'human' ? 'Αφαίρεση' : 'Δεν είναι αυτό'))));
  }
  wrap.append(h('div', { class: 'actions' }, h('button', { class: 'small', onclick: (ev) => entityPicker(ev.currentTarget, (t) => assignCategory([blk.id], t)) },
    es.length ? '+ Κατηγορία ▾' : 'Ορισμός κατηγορίας ▾')));
  return wrap;
}

// multi-selection: the ordered selection becomes ONE entity of the chosen category
export const assignSelectionButton = (ids) => h('button', { onclick: (ev) => entityPicker(ev.currentTarget, (t) => assignCategory(ids, t).then(() => { clearSelection(); renderWorkspace(); })),
  title: 'Τα επιλεγμένα τμήματα (με αυτή τη σειρά) γίνονται μία οντότητα της κατηγορίας' }, 'Κατηγορία…', h('kbd', {}, 'C'));

// ---------------------------------------------------------------- Structure tab: all entities, statistics, model
const pct = (x) => `${Math.round(100 * x)}%`;
export async function entitiesCard(card) {
  const book = S.book, r = await api(`${B(book.id)}/entities`), entities = r.entities;
  const trusted = entities.filter(e => e.trusted && !e.verified);
  clear(card).append(h('div', { class: 'row' }, h('h2', { class: 'grow', style: { margin: 0 } }, 'Οντότητες βιβλίου ', infoTip('entities')),
    trusted.length ? h('button', { class: 'good', title: 'Κανόνες που τα στατιστικά δείχνουν αξιόπιστους· η επιβεβαίωση παραμένει δική σου πράξη',
      onclick: () => post('/entities/confirm-trusted', {}, `Επιβεβαιώθηκαν ${trusted.length}`) }, `Επιβεβαίωση αξιόπιστων (${trusted.length})`) : null),
    h('p', { class: 'small mut' }, `${entities.length} οντότητες · ${entities.filter(e => e.verified).length} επιβεβαιωμένες · ${entities.filter(e => e.source === 'human').length} δικές σου. `
      + 'Στην Εξαγωγή εξαιρούνται/μετακινούνται μόνο επιβεβαιωμένες. Η κατηγορία αλλάζει όσες φορές θέλεις.'));
  const byFam = {};
  entities.forEach(e => { const f = e.family || 'x'; (byFam[f] = byFam[f] || {}); (byFam[f][e.type] = byFam[f][e.type] || []).push(e); });
  const order = [...Object.keys(ENT().families), 'x'];
  for (const fam of order.filter(f => byFam[f])) {
    card.append(h('h3', {}, famName(fam)));
    for (const [t, list] of Object.entries(byFam[fam])) {
      const sel = new Set(), ver = list.filter(e => e.verified).length;
      const places = [...new Set(list.map(e => e.placement))].map(p => ENT().placements[p] || p).join(' · ');
      const rows = list.map(e => h('label', { class: 'list-item ent-row' + (e.verified ? ' verified' : '') },
        h('input', { type: 'checkbox', onchange: (ev) => ev.target.checked ? sel.add(e.key) : sel.delete(e.key) }),
        h('div', { class: 'grow' }, h('div', {}, h('b', {}, e.label || entName(e.type)), ' ', e.text && e.text.trim() !== (e.label || '').trim() ? h('span', { class: 'small mut' }, e.text.slice(0, 90)) : null),
          h('div', { class: 'small mut' }, `σελ. ${e.pages.join(', ')} · ${ENT().placements[e.placement] || e.placement} · ${e.size} τμήμα(τα) · ${e.source === 'human' ? 'δική σου' : e.rule}`,
            e.source !== 'human' && e.type !== e.detected_type ? ` · εντοπίστηκε ως ${entName(e.detected_type)}` : '',
            e.model ? ` · μοντέλο: ${entName(e.model.type)} (${e.model.votes}/${e.model.samples})${e.model.agrees ? ' — συμφωνεί' : ''}` : '',
            (e.answers || []).length ? ` · απάντηση: ${e.answers.length} τμήμα(τα) στη συλλογή απαντήσεων` : '')),
        e.source === 'human' ? h('span', { class: 'chip ok' }, '✓ δική σου') : e.verified ? h('span', { class: 'chip ok' }, '✓ επιβεβαιωμένη')
          : e.trusted ? h('span', { class: 'chip ok', title: 'Ο κανόνας έχει αποδειχθεί αξιόπιστος στα στατιστικά' }, 'αξιόπιστος κανόνας')
          : h('span', { class: 'chip' + (e.confidence === 'low' ? ' warn' : '') }, e.confidence === 'high' ? 'υψηλή βεβαιότητα' : e.confidence === 'medium' ? 'μέτρια' : 'χαμηλή'),
        h('button', { class: 'small ghost', onclick: (ev) => { ev.preventDefault(); entityPicker(ev.currentTarget, (nt) => setCategory([e.key], nt), e.type); } }, 'Αλλαγή ▾'),
        h('button', { class: 'small ghost', onclick: (ev) => { ev.preventDefault(); openPage(e.pages[0], e.block); } }, 'Άνοιγμα')));
      const keys = () => [...sel];
      const need = (fn) => () => keys().length ? fn(keys()) : toast('Διάλεξε πρώτα στοιχεία', { error: true });
      card.append(h('details', {}, h('summary', {}, `${entName(t)} `, h('span', { class: 'small mut' }, `${list.length} · ${ver} επιβεβαιωμένες · ${places}`)),
        h('div', {}, h('div', { class: 'actions', style: { margin: '6px 0' } },
          h('button', { class: 'small', onclick: (ev) => { ev.currentTarget.closest('details').querySelectorAll('input[type=checkbox]').forEach(i => { i.checked = true; i.dispatchEvent(new Event('change')); }); } }, 'Επιλογή όλων'),
          h('button', { class: 'small primary', onclick: need(k => setCategory(k, null)) }, 'Επιβεβαίωση επιλεγμένων'),
          h('button', { class: 'small', onclick: (ev) => { const tgt = ev.currentTarget; need(k => entityPicker(tgt, (nt) => setCategory(k, nt)))(); } }, 'Αλλαγή κατηγορίας ▾'),
          h('button', { class: 'small dismiss', onclick: need(k => Promise.all(k.map(x => api(`${B(book.id)}/entities/${x}/remove`, { body: {} }))).then(() => { toast('Αφαιρέθηκαν'); S.recentEdit = Date.now(); kickPoll(); })) }, 'Δεν είναι αυτό / αφαίρεση')),
          rows)));
    }
  }
  card.append(entityStats(r));
}

// learning: per detector rule (outcomes of the reviewer's verdicts) and the model's agreement with the reviewer
function entityStats(r) {
  const rules = Object.entries((r.stats || {}).rules || {}).sort((a, b) => b[1].n - a[1].n);
  const model = Object.entries(r.model_stats || {});
  const box = h('details', {}, h('summary', {}, 'Στατιστικά & εκμάθηση ', infoTip('entitystats')));
  const body = h('div', {}); box.append(body);
  if (!rules.length && !model.length) { body.append(h('div', { class: 'small mut' }, 'Δεν υπάρχουν ακόμα αποφάσεις σου πάνω σε εντοπισμούς.')); return box; }
  if (rules.length) body.append(h('table', { class: 'stats' }, h('tr', {}, ['Κανόνας', 'Αποφάσεις', 'Λάθη', 'Άνω όριο σφάλματος', ''].map(x => h('th', {}, x))),
    rules.map(([rc, a]) => { const [g, t] = rc.split(':'); return h('tr', {}, h('td', {}, `${entName(t)} · ${g}`), h('td', {}, a.n), h('td', {}, a.wrong), h('td', {}, pct(a.error_upper)),
      h('td', {}, a.trusted ? h('span', { class: 'chip ok' }, 'αξιόπιστος') : h('span', { class: 'small mut' }, a.n < r.stats.min_evidence ? `χρειάζεται ${r.stats.min_evidence - a.n} ακόμα` : 'πολλά λάθη'))); })));
  if (model.length) body.append(h('h3', {}, 'Μοντέλο έναντι των αποφάσεών σου'), h('table', { class: 'stats' },
    h('tr', {}, ['Οικογένεια', 'Συγκρίσεις', 'Διαφωνίες', 'Άνω όριο σφάλματος'].map(x => h('th', {}, x))),
    model.map(([f, a]) => h('tr', {}, h('td', {}, f === '*' ? 'Σύνολο' : f === 'none' ? 'Όχι οντότητα' : famName(f)), h('td', {}, a.n), h('td', {}, a.wrong), h('td', {}, pct(a.error_upper))))));
  return box;
}

export async function entityReviewPanel(box) {
  const book = S.book;
  const r = await api(`${B(book.id)}/entity-review`);
  const running = (S.jobs || []).some(j => j.book === book.id && j.kind === 'entities' && isActiveJob(j));
  clear(box).append(h('div', { class: 'row' }, h('h2', { class: 'grow', style: { margin: 0 } }, 'Κατηγορίες · προτάσεις μοντέλου ', infoTip('entityllm')),
    h('button', { class: 'primary', disabled: running || !S.meta.note_model, onclick: async () => { const j = await act(() => api(`${B(book.id)}/entity-review`, { body: {} }), 'Ο έλεγχος κατηγοριών μπήκε στην ουρά', false); if (j && j.id) { S.jobs.push(j); kickPoll(); entityReviewPanel(box); } } },
      running ? 'Τρέχει…' : (r.status ? 'Νέος έλεγχος κατηγοριών' : 'Έλεγχος κατηγοριών με το μοντέλο'))));
  if (r.status === 'UNAVAILABLE') { box.append(h('p', { class: 'badge-bad' }, 'Δεν υπάρχει τοπικό μοντέλο: ' + (r.error || ''))); return; }
  if (!r.status) { box.append(h('p', { class: 'small mut' }, 'Δεν έχει τρέξει ακόμα. Ρωτά μόνο για αμφίβολους εντοπισμούς και πλαίσια χωρίς κατηγορία· κάθε πρόταση την αποφασίζεις εσύ.')); return; }
  box.append(h('p', { class: 'small mut' }, `${r.model} · ${r.built_utc?.replace('T', ' ').slice(0, 16)} · ρωτήθηκε για ${r.asked}, συμφώνησε σε ${r.agreed_with_current}` +
    (r.invalid_answers ? ` · ${r.invalid_answers} άκυρες απαντήσεις` : '') + (r.failed?.length ? ` · ${r.failed.length} απέτυχαν` : '') +
    (r.canonical_built && book.built && r.canonical_built !== book.built ? ' · το βιβλίο άλλαξε από τότε: ξανατρέξε για ενημερωμένες προτάσεις' : '')));
  if (!r.proposals.length) { box.append(h('div', { class: 'list-item mut' }, 'Καμία ανοιχτή πρόταση.')); return; }
  for (const p of r.proposals) {
    box.append(h('div', { class: 'cand' },
      h('div', { class: 'row' }, h('b', { class: 'grow link-like', title: 'Προεπισκόπηση στη σελίδα (παράθυρο)', onclick: () => openPage(p.page, p.block_id) }, p.text || '(χωρίς κείμενο)'), h('span', { class: 'small mut' }, `σελ. ${p.page} · ${p.size} τμήμα(τα)`),
        h('span', { class: 'chip' + (p.votes === p.samples ? '' : ' warn') }, `${p.votes}/${p.samples} συμφωνία`)),
      h('div', { class: 'small' }, h('span', { class: 'mut' }, 'Τώρα: '), p.current ? entName(p.current) : 'χωρίς κατηγορία', h('span', { class: 'mut' }, '  →  Πρόταση: '),
        h('b', {}, p.proposed === 'none' ? 'όχι οντότητα (απλό κείμενο)' : entName(p.proposed))),
      h('div', { class: 'small', style: { marginTop: '4px' } }, p.reasons.find(Boolean) || ''),
      h('details', {}, h('summary', { class: 'small mut' }, 'Τι είδε το μοντέλο'), h('pre', { class: 'preview-pre' }, p.prompt),
        h('div', { class: 'small mut' }, 'Όλες οι απαντήσεις: ' + p.reasons.map((x, i) => `(${i + 1}) ${x} [${p.confidence[i]}]`).join('  '))),
      cardActions([
        h('button', { class: 'small primary', onclick: () => post(`/entity-review/${p.key}/accept`, {}, 'Η κατηγορία ενημερώθηκε') }, 'Αποδοχή'),
        h('button', { class: 'small', onclick: (ev) => entityPicker(ev.currentTarget, (t) => p.mode === 'verify' ? setCategory([p.decision.key], t) : assignCategory(p.decision.members, t), p.current) }, 'Άλλη κατηγορία ▾'),
        h('button', { class: 'small dismiss', onclick: () => post(`/suggestions/${p.key}/dismiss`, {}, 'Η πρόταση απορρίφθηκε') }, 'Απόρριψη')],
        viewButton(() => openPage(p.page, p.block_id), 'Άνοιγμα στη σελίδα (παράθυρο)'))));
  }
}
