import { B, api } from '../core/api.js';
import { refresh } from '../core/chrome.js';
import { h } from '../core/dom.js';
import { S, go } from '../core/state.js';
import { actLater, toast } from '../ui/feedback.js';
import { loopCount } from '../ui/loopcount.js';
import { reveal } from '../workspace/reveal.js';
import { selectBlock } from '../workspace/workspace.js';

// ---------------------------------------------------------------- loop over the spelling proposals (pipeline/spelling.py)
// One proposal at a time, in book order, on its page: the segment selected and centred, a bar with the word in its
// context and the correction (editable; the candidates are offered). Keys (core/keyboard.js): ↵ διόρθωση · X σωστό
// ως έχει · J/K επόμενο/προηγούμενο · Esc έξοδος. A verdict is the same undoable event as in the «Ορθογραφία» list
// (spelling apply = replace_text / dismiss = suggestion_dismiss); it is remembered here until the book is rebuilt,
// so the loop never offers it again. Same shape as the exclusions loop (inspector/boundloop.js).
const WHY_EL = { lexicon: 'λεξικό', engine: 'άλλη μηχανή', book: 'βιβλίο', accent: 'τόνος', script: 'αλφάβητο' };

export const typoActive = () => !!(S.typo && S.typo.items.length && S.route?.view === 'ws');
export const typoCurrent = () => (S.typo ? S.typo.items[S.typo.i] : null);

export async function startTypoLoop() {
  const r = await api(`${B(S.book.id)}/spelling`);
  const items = r.items || [];
  if (!items.length) return toast('Δεν υπάρχουν προτάσεις ορθογραφίας 🎉');
  // start at the first proposal at or after the page you are on
  const here = S.page ? items.findIndex(x => x.page >= S.page.page) : 0;
  S.bound = null;                                 // one special loop at a time
  S.typo = { items, i: Math.max(0, here), done: new Set(), text: null };
  open();
}

export function stopTypoLoop() { S.typo = null; refresh(); }

function open() {
  const t = typoCurrent();
  S.typo.text = t.candidates[0]?.text || t.word;
  reveal(t.block_id);                             // centred, also when it is on the page already open
  if (S.page && S.page.page === t.page && S.route?.view === 'ws') selectBlock(t.block_id);
  else go(`#/b/${S.book.id}/p/${t.page}/${t.block_id}`);
}

export function typoStep(dir = 1) {
  const s = S.typo, n = s.items.length;
  for (let k = 1; k <= n; k++) {
    const i = (s.i + dir * k + n * k) % n;
    if (!s.done.has(s.items[i].key)) {
      if ((dir > 0 && i < s.i) || (dir < 0 && i > s.i)) toast(dir > 0 ? 'Τέλος της λίστας· συνέχεια από την αρχή' : 'Αρχή της λίστας· συνέχεια από το τέλος');
      s.i = i; open(); return;
    }
  }
  toast('Όλα τα τυπογραφικά κρίθηκαν 🎉');
  stopTypoLoop();
}

/** fix = true: correct the word to the text in the bar; false: the word is right as it is (not proposed again). */
export function typoDecide(fix) {
  const t = typoCurrent();
  if (!t || S.typo.done.has(t.key)) return;
  const text = (S.typo.text || '').trim();
  if (fix && !text) return toast('Γράψε τη διόρθωση', { error: true });
  S.typo.done.add(t.key);                         // the next proposal opens at once; the verdict is sent behind
  actLater(() => fix ? api(`${B(S.book.id)}/spelling/apply`, { body: { choices: [{ key: t.key, text }] } })
    : api(`${B(S.book.id)}/spelling/dismiss`, { body: { keys: [t.key] } }), null,
  { key: t.key, onError: () => { S.typo?.done.delete(t.key); toast('Η απόφαση δεν καταγράφηκε· η πρόταση ξαναμπήκε στη λίστα', { error: true }); } });
  typoStep(1);
}

/** The bar above the page while the loop is on. */
export function typoBar(lead = null) {   // lead: the loop selector (workspace), first in the bar
  const s = S.typo, t = typoCurrent();
  const left = s.items.length - s.done.size;
  const listId = 'typo-cands';
  const why = (v) => { const c = t.candidates.find(x => x.text === v); return c ? c.why.map(w => WHY_EL[w] || w).join(', ') : (v.trim() ? 'δική σου' : ''); };
  const ev = { set textContent(v) { input.title = v ? `Ένδειξη: ${v}` : ''; } };   // the evidence: the field's tooltip
  const input = h('input', { type: 'text', class: 'small typo-fix', list: listId, value: s.text || '', spellcheck: 'false', lang: 'el',
    'aria-label': `Διόρθωση για «${t.word}»`,
    oninput: (e) => { s.text = e.target.value; ev.textContent = why(s.text); },
    title: why(s.text || '') ? `Ένδειξη: ${why(s.text || '')}` : '',
    onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); typoDecide(true); } else if (e.key === 'Escape') e.target.blur(); } });
  return h('div', { class: 'ws-row bound-bar typo-bar', role: 'region', 'aria-label': 'Έλεγχος τυπογραφικών' },
      lead,
    h('b', { class: 'nowrap', title: `Τυπογραφικό ${s.i + 1} από ${s.items.length} · σελ. ${t.page}` }, `${s.i + 1} / ${s.items.length}`),
    loopCount(left, s.items.length, 'τυπογραφικά'),
    t.strength === 'strong' ? h('span', { class: 'chip rv-approved typo-strong', title: 'Ισχυρή: δύο ανεξάρτητες ενδείξεις' }, 'ισχυρή') : null,
    h('span', { class: 'small typo-ctx', title: `…${t.before} ${t.word} ${t.after}…` }, h('span', { class: 'mut ctx-side' }, '…' + t.before + ' '), h('b', { class: 'kpi-bad' }, t.word), h('span', { class: 'mut ctx-side' }, ' ' + t.after + '…')),
    h('span', {}, '→'), input, h('datalist', { id: listId }, t.candidates.map(c => h('option', { value: c.text }, c.why.map(w => WHY_EL[w] || w).join(', ')))),
    h('button', { class: 'small', onclick: () => typoStep(-1), title: 'Προηγούμενο (K)' }, '↑', h('kbd', {}, 'K')),
    h('button', { class: 'small ghost', onclick: () => typoDecide(false), title: 'Η λέξη είναι σωστή όπως είναι· δεν ξαναπροτείνεται (X)' }, 'Σωστό ως έχει', h('kbd', {}, 'X')),
    h('button', { class: 'small primary', onclick: () => typoDecide(true), title: 'Διόρθωση της λέξης (↵)' }, 'Διόρθωση', h('kbd', {}, '↵')),
    h('button', { class: 'small', onclick: () => typoStep(1), title: 'Επόμενο χωρίς απόφαση (J)' }, '↓', h('kbd', {}, 'J')),
    h('button', { class: 'small ghost', onclick: stopTypoLoop, title: 'Έξοδος (Esc)' }, '✕'));
}
