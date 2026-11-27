import { B, api } from '../core/api.js';
import { refresh } from '../core/chrome.js';
import { h, section } from '../core/dom.js';
import { S, go } from '../core/state.js';
import { infoTip } from '../core/strings.js';
import { act } from '../ui/feedback.js';
import { startTypoLoop } from '../inspector/typoloop.js';

// ---------------------------------------------------------------- spelling proposals (pipeline/spelling.py)
// Every proposal: the word in its context, candidates with their evidence, a checkbox. «Εφαρμογή» = one undoable step
// (a replace_text decision per touched segment); «Απόρριψη» is remembered. Strong ones come pre-ticked.
const WHY_EL = { lexicon: 'λεξικό', engine: 'άλλη μηχανή', book: 'βιβλίο', accent: 'τόνος', script: 'αλφάβητο' };
const SHOW = 200;

export async function spellingCard(body) {
  const r = await api(`${B(S.book.id)}/spelling`);
  const items = r.items || [];
  if (!items.length) return;
  const sum = r.summary || {};
  const card = h('div', { class: 'card', style: { padding: '12px' } });
  body.append(section([`Ορθογραφία (${items.length}) `, infoTip('spelling')], card));
  const pick = new Map();                 // key -> chosen candidate text (absent = not selected)
  for (const it of items) if (it.strength === 'strong') pick.set(it.key, it.candidates[0].text);
  const count = h('span', { class: 'small mut grow' });
  const upd = () => { count.textContent = `${pick.size} επιλεγμένες · ${sum.strong || 0} με δύο ανεξάρτητες ενδείξεις${sum.lexicon ? '' : ' · χωρίς λεξικό (δες ⓘ)'}`; };
  const apply = () => act(() => api(`${B(S.book.id)}/spelling/apply`, { body: { choices: [...pick].map(([key, text]) => ({ key, text })) } }),
    `Διορθώθηκαν ${pick.size} λέξεις`).then(refresh);
  const dismiss = () => act(() => api(`${B(S.book.id)}/spelling/dismiss`, { body: { keys: [...pick.keys()] } }), `Απορρίφθηκαν ${pick.size}`).then(refresh);
  card.append(h('div', { class: 'row', style: { marginBottom: '8px' } }, count,
    h('button', { class: 'small', onclick: () => startTypoLoop(), title: 'Μία-μία στη σελίδα της: ↵ διόρθωση · X σωστό ως έχει · J/K · Esc' }, 'Ένα-ένα ▶'),
    h('button', { class: 'small ghost', onclick: dismiss, title: 'Οι επιλεγμένες λέξεις είναι σωστές όπως είναι (δεν ξαναπροτείνονται)' }, 'Απόρριψη επιλεγμένων'),
    h('button', { class: 'small primary', onclick: apply, title: 'Μία ενέργεια· αναιρείται με ⌘Z' }, 'Εφαρμογή επιλεγμένων')));
  for (const it of items.slice(0, SHOW)) {
    const cb = h('input', { type: 'checkbox' }); cb.checked = pick.has(it.key);
    // the correction is an editable field: the candidates are offered as suggestions, any spelling of the reviewer's own goes
    const why = (v) => { const c = it.candidates.find(x => x.text === v); return c ? c.why.map(w => WHY_EL[w] || w).join(', ') : (v.trim() ? 'δική σου' : ''); };
    const listId = `sp-${it.key}`.replace(/[^\w-]/g, '_');
    const sel = h('input', { type: 'text', class: 'small', list: listId, value: it.candidates[0].text, spellcheck: 'false', lang: 'el',
      'aria-label': `Διόρθωση για «${it.word}»`, style: { flex: 'none', width: '180px' } });
    const ev = h('span', { class: 'small mut', style: { flex: 'none', width: '110px' } }, why(sel.value));
    const opts = h('datalist', { id: listId }, it.candidates.map(c => h('option', { value: c.text }, c.why.map(w => WHY_EL[w] || w).join(', '))));
    cb.onchange = () => { cb.checked && sel.value.trim() ? pick.set(it.key, sel.value.trim()) : (cb.checked = false, pick.delete(it.key)); upd(); };
    sel.oninput = () => { ev.textContent = why(sel.value); cb.checked = !!sel.value.trim(); cb.onchange(); };
    card.append(h('label', { class: 'list-item' }, cb,
      h('span', { class: 'small mut', style: { width: '52px', flex: 'none' } }, `σελ. ${it.page}`),
      h('span', { class: 'small', style: { flex: '1', minWidth: '0' } }, h('span', { class: 'mut' }, '…' + it.before + ' '), h('b', { class: 'kpi-bad' }, it.word), h('span', { class: 'mut' }, ' ' + it.after + '…')),
      h('span', { style: { flex: 'none' } }, '→ '), sel, opts, ev, it.strength === 'strong' ? h('span', { class: 'chip rv-approved', title: 'Δύο ανεξάρτητες ενδείξεις' }, 'ισχυρή') : null,
      h('button', { class: 'small ghost', onclick: () => go(`#/b/${S.book.id}/p/${it.page}/${it.block_id}`) }, 'Δες')));
  }
  if (items.length > SHOW) card.append(h('div', { class: 'small mut' }, `… και ${items.length - SHOW} ακόμη (εμφανίζονται μετά την εφαρμογή/απόρριψη των πρώτων)`));
  upd();
}
