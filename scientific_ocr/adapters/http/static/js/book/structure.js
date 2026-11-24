import { structureReviewPanel } from '../book/quality.js';
import { renderBook } from '../book/book.js';
import { B, actor, api } from '../core/api.js';
import { doRedo, doUndo, listed, refresh, removeJobButton, showLog } from '../core/chrome.js';
import { $, clear, h, isActiveJob } from '../core/dom.js';
import { S, go, sym } from '../core/state.js';
import { EV_EL, SRC_EL, infoTip } from '../core/strings.js';
import { entitiesCard, entityReviewPanel } from '../book/entities.js';
import { extentReviewPanel, headingReviewCard, outlineCard } from '../book/hierarchy.js';
import { docStructureCard } from '../inspector/suggestions.js';
import { excursusCard } from '../inspector/excursus.js';
import { act, modal } from '../ui/feedback.js';

export function tabStructure(body) {
  const rules = h('div', { class: 'card', style: { padding: '14px 16px', marginBottom: '16px' } });
  docStructureCard(rules);
  const llm = h('div', { class: 'card', style: { padding: '14px 16px', marginBottom: '16px' } }, h('div', { class: 'small mut' }, 'Φόρτωση προτάσεων…'));
  const ents = h('div', { class: 'card', style: { padding: '14px 16px', marginBottom: '16px' } }, h('div', { class: 'small mut' }, 'Φόρτωση οντοτήτων…'));
  const entLlm = h('div', { class: 'card', style: { padding: '14px 16px', marginBottom: '16px' } }, h('div', { class: 'small mut' }, 'Φόρτωση προτάσεων…'));
  body.append(excursusCard(), rules, llm, ents, entLlm);
  entitiesCard(ents).catch(e => clear(ents).append(h('div', { class: 'badge-bad' }, e.message)));
  entityReviewPanel(entLlm).catch(e => clear(entLlm).append(h('div', { class: 'badge-bad' }, e.message)));
  structureReviewPanel(llm).catch(e => clear(llm).append(h('div', { class: 'badge-bad' }, e.message)));
  const hints = h('div', { class: 'card', style: { padding: '14px 16px', marginBottom: '16px' } }, h('div', { class: 'small mut' }, 'Φόρτωση…'));
  const tree = h('div', { class: 'card', style: { padding: '14px 16px', marginBottom: '16px' } }, h('div', { class: 'small mut' }, 'Φόρτωση ιεραρχίας…'));
  const ext = h('div', { class: 'card', style: { padding: '14px 16px', marginBottom: '16px' } }, h('div', { class: 'small mut' }, 'Φόρτωση…'));
  body.append(ext, hints, tree);
  extentReviewPanel(ext).catch(e => clear(ext).append(h('div', { class: 'badge-bad' }, e.message)));
  headingReviewCard(hints).catch(e => clear(hints).append(h('div', { class: 'badge-bad' }, e.message)));
  outlineCard(tree).catch(e => clear(tree).append(h('div', { class: 'badge-bad' }, e.message)));
}

export function tabRules(body) {
  const b = S.book;
  body.append(h('p', { class: 'small mut' }, 'Κανόνες βιβλίου: διορθώνουν ένα σύμβολο που το OCR αλλάζει συστηματικά (π.χ. το βιβλίο τυπώνει λατινικά G αλλά το OCR βγάζει Γ). Εφαρμόζονται μόνο σε απομονωμένα σύμβολα, ποτέ σε ανθρώπινες διορθώσεις, και φαίνονται πάντα στο τμήμα.'));
  const card = h('div', { class: 'card', style: { marginBottom: '16px' } });
  if (!b.rules.length) card.append(h('div', { class: 'list-item', style: { cursor: 'default' } }, h('span', { class: 'mut' }, 'Κανένας κανόνας ακόμα.')));
  for (const r of b.rules) card.append(h('div', { class: 'list-item', style: { cursor: 'default' } },
    h('div', { class: 'grow' }, h('div', {}, sym(r.find), '  →  ', sym(r.replace), r.note ? h('span', { class: 'mut small' }, '  · ' + r.note) : null),
      h('div', { class: 'small mut' }, r.context ? `μόνο σε προτάσεις που ταιριάζουν: ${r.context}` : 'σε όλο το βιβλίο', r.exclude.length ? ` · ${r.exclude.length} εξαιρέσεις` : '')),
    h('button', { class: 'ghost', onclick: () => ruleEditor(r) }, 'Επεξεργασία'),
    h('button', { class: 'ghost danger', onclick: async () => { await act(() => api(`${B(b.id)}/rules/${r.id}/remove`, { body: {} }), 'Ο κανόνας αφαιρέθηκε'); await refresh(); } }, 'Αφαίρεση')));
  body.append(card, h('button', { class: 'primary', onclick: () => ruleEditor() }, 'Νέος κανόνας'));
}
export function ruleEditor(rule) {
  rule = rule || { find: '', replace: '', context: '', exclude: [], note: '' };
  const f = h('input', { type: 'text', value: rule.find, placeholder: 'π.χ. Γ' }), rp = h('input', { type: 'text', value: rule.replace, placeholder: 'π.χ. G' });
  const cx = h('input', { type: 'text', value: rule.context, placeholder: 'προαιρετικό, π.χ. βάσ|DNA' }), nt = h('input', { type: 'text', value: rule.note, placeholder: 'γιατί (προαιρετικό)' });
  const exclude = new Set(rule.exclude), list = h('div', { style: { maxHeight: '40vh', overflow: 'auto', marginTop: '8px' } });
  const preview = async () => {
    clear(list);
    if (!f.value.trim()) return;
    try {
      const m = await api(`${B(S.book.id)}/rules/preview`, { body: { find: f.value, replace: rp.value, context: cx.value, exclude: [...exclude] } });
      list.append(h('div', { class: 'small mut' }, `${m.length} εμφανίσεις — ξετσέκαρε όσες ΔΕΝ πρέπει να αλλάξουν`));
      for (const x of m) { const c = h('input', { type: 'checkbox' }); c.checked = !exclude.has(x.key);
        c.onchange = () => c.checked ? exclude.delete(x.key) : exclude.add(x.key);
        list.append(h('label', { class: 'list-item', style: { cursor: 'pointer' } }, c, h('span', { class: 'small mut' }, `σ.${x.page}`), h('span', { class: 'small grow' }, '…' + x.context + '…'))); }
    } catch (e) { list.append(h('div', { class: 'small', style: { color: 'var(--bad)' } }, e.message)); }
  };
  [f, cx].forEach(i => i.addEventListener('change', preview));
  modal(rule.id ? 'Επεξεργασία κανόνα' : 'Νέος κανόνας', h('div', {},
    h('div', { class: 'row' }, h('div', { class: 'grow' }, h('label', { class: 'field' }, 'Όταν το OCR γράφει'), f), h('div', { class: 'grow' }, h('label', { class: 'field' }, 'το βιβλίο έχει'), rp)),
    h('label', { class: 'field' }, 'Μόνο σε προτάσεις που περιέχουν (regex)'), cx, h('label', { class: 'field' }, 'Σημείωση'), nt,
    h('div', { style: { marginTop: '10px' } }, h('button', { onclick: preview }, 'Προεπισκόπηση')), list),
    [{ label: 'Άκυρο' }, { label: 'Αποθήκευση', cls: 'primary', run: async () => {
      await act(() => api(`${B(S.book.id)}/rules`, { body: { id: rule.id, find: f.value, replace: rp.value, context: cx.value, note: nt.value, exclude: [...exclude] } }), 'Ο κανόνας αποθηκεύτηκε· ενημερώνεται το αποτέλεσμα'); await refresh(); } }]);
  if (rule.find) preview();
}

export async function tabHistory(body) {
  const hist = await api(`${B(S.book.id)}/history`);
  body.append(h('div', { class: 'row', style: { marginBottom: '8px' } }, h('h2', { class: 'grow' }, 'Ενέργειες ελέγχου'),
    h('button', { disabled: !S.book.can_undo, onclick: doUndo }, '↶ Αναίρεση'), h('button', { disabled: !S.book.can_redo, onclick: doRedo }, '↷ Επανάληψη')));
  const card = h('div', { class: 'card history' }); body.append(card);
  if (!hist.length) card.append(h('div', { class: 'list-item' }, h('span', { class: 'mut' }, 'Καμία ενέργεια ακόμα.')));
  for (const e of hist.slice(0, 300)) card.append(h('div', { class: 'list-item' + ((e.type === 'undo' || e.type === 'redo') ? '' : (e.active ? '' : ' undone')) },
    h('span', { class: 'small mut', style: { width: '150px' } }, (e.ts || '').replace('T', ' ').slice(0, 19)), h('span', { class: 'small mut', style: { width: '90px' } }, e.actor),
    h('span', { class: 'grow' }, (EV_EL[e.type] || (x => x.type))(e))));
  body.append(h('h2', { style: { marginTop: '24px' } }, 'Εργασίες'));
  const jc = h('div', { class: 'card' }); body.append(jc);
  for (const j of S.book.jobs.filter(listed)) jc.append(h('div', { class: 'list-item', style: { cursor: 'default' } }, h('span', { class: 'grow' }, j.label),
    h('span', { class: 'small mut' }, new Date(j.created * 1000).toLocaleString('el-GR')), h('span', { class: 'chip' }, j.state), h('button', { class: 'ghost', onclick: () => showLog(j) }, 'Σύνοψη'),
    isActiveJob(j) ? null : removeJobButton(j, () => renderBook())));
  if (S.book.jobs.some(j => !isActiveJob(j))) jc.append(h('div', { class: 'row end', style: { padding: '8px' } }, h('button', { class: 'ghost small', onclick: async () => {
    await api('/api/jobs/clear', { body: { book: S.book.id } }); S.book.jobs = S.book.jobs.filter(isActiveJob); renderBook(); } }, 'Καθαρισμός ολοκληρωμένων')));
}
export async function tabSettings(body) {
  const b = S.book;
  if (!S.books.length) S.books = await api('/api/books');
  const t = h('input', { type: 'text', value: b.title }), l = h('input', { type: 'text', value: b.ocr_lang });
  const who = h('input', { type: 'text', value: actor() });
  const ad = h('input', { type: 'checkbox' }); ad.checked = b.auto_diligent !== false;
  const calib = h('select', { multiple: true, size: 4 }, (S.books.length ? S.books : []).filter(x => x.id !== b.id).map(x => { const o = h('option', { value: x.id }, x.title); o.selected = (b.calibrate_with || []).includes(x.id); return o; }));
  body.append(h('div', { class: 'card', style: { padding: '16px', maxWidth: '560px' } },
    h('label', { class: 'field' }, 'Τίτλος'), t,
    h('label', { class: 'field' }, 'Γλώσσες OCR (κωδικοί Tesseract, π.χ. ell,eng · deu,eng)'), l,
    h('p', { class: 'small mut' }, 'Αλλαγή γλωσσών επηρεάζει μόνο σελίδες που θα επεξεργαστούν (ή ξανα-επεξεργαστούν) από εδώ και πέρα.'),
    h('label', { class: 'field' }, 'Όνομα ελεγκτή (καταγράφεται σε κάθε απόφαση, σε αυτόν τον browser)'), who,
    h('label', { class: 'row small', style: { marginTop: '12px' } }, ad, 'Αυτόματη προσεκτική επανεξέταση σελίδων με πολλά προβλήματα διάταξης ', infoTip('autodiligent')),
    h('label', { class: 'field' }, h('span', {}, 'Βαθμονόμηση από ελεγμένα βιβλία της ίδιας σειράς ', infoTip('calibration'))), calib,
    h('div', { class: 'row', style: { marginTop: '14px' } }, h('button', { class: 'primary', onclick: async () => {
      localStorage.setItem('ocr.actor', who.value.trim() || 'reviewer');
      await act(() => api(`${B(b.id)}/settings`, { body: { title: t.value, ocr_lang: l.value, auto_diligent: ad.checked, calibrate_with: [...calib.selectedOptions].map(o => o.value) } }), 'Αποθηκεύτηκε', false); await refresh(); } }, 'Αποθήκευση'))));
  body.append(h('div', { class: 'card', style: { padding: '16px', maxWidth: '560px', marginTop: '16px', borderColor: '#f3c3bd' } },
    h('h2', {}, 'Διαγραφή βιβλίου'), h('p', { class: 'small mut' }, 'Το βιβλίο μεταφέρεται στον κάδο της βιβλιοθήκης και μπορεί να επαναφερθεί.'),
    h('button', { class: 'danger', onclick: () => modal('Διαγραφή βιβλίου;', h('p', {}, `«${b.title}» θα μεταφερθεί στον κάδο.`), [{ label: 'Άκυρο' }, { label: 'Διαγραφή', cls: 'danger', run: async () => {
      const r = await act(() => api(`${B(b.id)}/trash`, { body: {} }), null, false);
      const t2 = h('div', { class: 'toast' }, 'Διαγράφηκε', h('button', { onclick: async () => { t2.remove(); await api(`/api/trash/${encodeURIComponent(r.trash_name)}/restore`, { body: {} }); go(`#/b/${b.id}`); } }, 'Επαναφορά'));
      $('#toasts').append(t2); setTimeout(() => t2.remove(), 10000); go('#/'); } }]) }, 'Μεταφορά στον κάδο')));
}
