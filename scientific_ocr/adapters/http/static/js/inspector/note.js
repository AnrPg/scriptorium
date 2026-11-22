import { B, api } from '../core/api.js';
import { $, clear, h } from '../core/dom.js';
import { S } from '../core/state.js';
import { reportWrong } from '../inspector/lineage.js';
import { contentView, currentValue } from '../inspector/segment.js';
import { act } from '../ui/feedback.js';

// ---------------------------------------------------------------- note -> local model -> proposal (never auto-applied)
export function notePanel(blk, seg, rerender) {
  const model = S.meta.note_model;
  const note = h('textarea', { placeholder: 'Περιέγραψε με δικά σου λόγια τι είναι λάθος, π.χ. «το βέλος είναι ισορροπίας ⇌», «λείπει η δεύτερη γραμμή», «σε όλο το βιβλίο το Γ είναι G», «αυτό είναι λεζάντα».', style: { minHeight: '70px', fontFamily: 'inherit' } });
  note.value = seg.note || '';
  const status = h('div');
  const run = async () => {
    seg.note = note.value;
    clear(status).append(h('div', { class: 'row small mut' }, h('span', { class: 'spin' }), `Το ${model} διαβάζει τη σημείωση και την εικόνα…`));
    try { seg.proposal = await api(`${B(S.book.id)}/blocks/${blk.id}/interpret`, { body: { note: note.value } }); seg.mode = 'proposal'; rerender(); }
    catch (e) { clear(status).append(h('div', { class: 'badge-bad' }, e.message)); }
  };
  return h('details', {}, h('summary', {}, 'Σημείωση με φυσική γλώσσα'), h('div', {},
    model ? h('p', { class: 'small mut' }, `Ένα τοπικό μοντέλο (${model}) βλέπει την εικόνα και τη σημείωσή σου και προτείνει ΜΙΑ ενέργεια. Τίποτα δεν εφαρμόζεται χωρίς την έγκρισή σου.`)
          : h('p', { class: 'small badge-bad' }, 'Δεν έχει εγκατασταθεί τοπικό μοντέλο ερμηνείας.'),
    note, h('div', { class: 'actions' }, h('button', { class: 'primary', disabled: !model, onclick: run }, 'Ερμηνεία'),
      h('button', { class: 'ghost', onclick: () => reportWrong(blk, note.value) }, 'Απλώς νέο OCR με τη σημείωση')), status));
}
export function proposalCard(area, blk, seg, rerender, latex) {
  const res = seg.proposal, p = res.proposal;
  const json = h('textarea', { class: 'json', spellcheck: 'false' }); json.value = JSON.stringify(p, null, 2);
  const status = h('div'), will = h('div', { class: 'will' }, res.description || '—');
  const apply = h('button', { class: 'good' }, 'Εφαρμογή πρότασης');
  const setStatus = (r) => { clear(status).append(r.valid ? h('span', { class: 'badge-ok' }, '✓ Έγκυρο σχήμα και πεδία') : h('span', { class: 'badge-bad' }, '✗ ' + r.errors.join(' · '))); will.textContent = r.description || '—'; apply.disabled = !r.valid; };
  const parse = () => { try { return JSON.parse(json.value); } catch (e) { setStatus({ valid: false, errors: ['μη έγκυρο JSON: ' + e.message] }); return null; } };
  apply.onclick = async () => { const prop = parse(); if (!prop) return;
    await act(() => api(`${B(S.book.id)}/blocks/${blk.id}/proposal/apply`, { body: { proposal: prop, note: seg.note, model: res.model } }), 'Η πρόταση εφαρμόστηκε');
    S.seg = null; };
  const editFirst = ['replace_text', 'replace_latex'].includes(p.action)
    ? h('button', { onclick: () => { seg.mode = 'draft'; seg.base = currentValue(blk); seg.draft = (parse() || p).value; seg.proposal = null; rerender(); } }, 'Επεξεργασία πριν την εφαρμογή') : null;
  const reject = () => { seg.mode = 'view'; seg.proposal = null; rerender(); };
  area.append(h('div', { class: 'proposal' },
    h('div', { class: 'small mut' }, `Πρόταση από ${res.model} · ${res.seconds}s · βεβαιότητα: ${({ high: 'υψηλή', medium: 'μέτρια', low: 'χαμηλή' })[p.confidence] || p.confidence}`),
    h('div', { class: 'said' }, h('b', {}, 'Κατάλαβα: '), p.understanding),
    p.clarification ? h('div', { class: 'note' }, h('b', {}, 'Ερώτηση του μοντέλου: '), p.clarification) : null,
    h('div', { class: 'small mut', style: { marginTop: '6px' } }, 'Τι θα κάνει ο κώδικας (από το JSON):'), will,
    p.value && ['replace_text', 'replace_latex'].includes(p.action) ? h('div', { class: 'cmp' },
      h('div', {}, h('div', { class: 'small mut' }, 'Τρέχουσα'), contentView(blk, currentValue(blk), latex)),
      h('div', {}, h('div', { class: 'small mut' }, 'Προτεινόμενη'), contentView(blk, p.value, p.action === 'replace_latex'))) : null,
    h('details', {}, h('summary', {}, 'JSON που θα εκτελεστεί (επεξεργάσιμο)'), h('div', {}, json,
      h('button', { class: 'small', onclick: async () => { const prop = parse(); if (prop) setStatus(await api(`${B(S.book.id)}/blocks/${blk.id}/proposal/check`, { body: { proposal: prop } })); } }, 'Έλεγχος σχήματος'))),
    status, h('div', { class: 'actions' }, apply, editFirst, h('button', { onclick: reject }, 'Απόρριψη', h('kbd', {}, 'Esc')))));
  setStatus(res);
  S.segActions = { discard: reject };
}
