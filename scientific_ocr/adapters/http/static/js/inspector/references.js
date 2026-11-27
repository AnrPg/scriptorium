import { h } from '../core/dom.js';
import { S, kindLabel } from '../core/state.js';
import { pendingFor } from '../ui/rebuild.js';
import { openPage } from '../ui/page_overlay.js';

// ---------------------------------------------------------------- cross-references of a text segment
// pipeline/references.py links «βλ. Εικόνα 13-20» to the numbered element that carries that label. When several
// elements carry it (a figure read twice, a figure continued on the next page, two kinds sharing a prefix) the
// mention is AMBIGUOUS: the text is unaffected (it stays a paragraph, in the output), only the link is missing.
// The reviewer picks the element or says it is not a reference (decision link_reference, mention index n; target
// null = not a reference). The decision is book-wide: it applies with the next book update, and can be changed
// here again (the candidates are kept) or undone from the history.
const STATUS_EL = { resolved: 'συνδέθηκε', ambiguous: 'αμφίσημη', unresolved: 'χωρίς στόχο στις σελίδες του βιβλίου' };

const targetOf = id => (S.book.ref_targets || []).find(t => t.id === id);
const targetText = id => {
  const t = targetOf(id);
  return t ? `σ. ${t.page} · ${kindLabel({ kind: t.kind })} «${t.label}»` : id;
};

export function referenceSummary(blk) {
  const refs = blk.references || [], amb = refs.filter(r => r.status === 'ambiguous').length;
  const off = (blk.references_dismissed || []).length;
  return [refs.length ? `${refs.length}` : '', amb ? `${amb} αμφίσημη(ες)` : '', off ? `${off} όχι παραπομπή` : ''].filter(Boolean).join(' · ');
}

// `only`: 'ambiguous' shows just the mentions that need a decision (under the review reason)
export function referencePanel(blk, decide, { only = null } = {}) {
  const refs = [...(blk.references || []), ...(blk.references_dismissed || []).map(r => ({ ...r, status: 'not_a_reference' }))]
    .filter(r => !only || r.status === only).sort((a, b) => a.n - b.n);
  if (!refs.length) return null;
  const waiting = pendingFor(blk).filter(e => e.action === 'link_reference');
  return h('div', { class: 'col ref-list' }, refs.map(r => {
    const pend = waiting.filter(e => e.n === r.n).pop();
    const chosen = pend ? (pend.target ?? null) : r.status === 'not_a_reference' ? null : r.target;
    const decidedNow = pend || r.source === 'human';
    const cands = r.candidates || (r.target ? [r.target] : []);
    const pick = (target, msg) => decide('link_reference', { n: r.n, target }, msg);
    const on = (t) => decidedNow && chosen === t ? ' primary' : '';
    return h('div', { class: 'ref-item' },
      h('div', { class: 'row small wrap' }, h('b', {}, `«${r.text}»`),
        h('span', { class: 'mut' }, r.status === 'not_a_reference' ? 'δεν είναι παραπομπή (δική σου απόφαση)'
          : `${STATUS_EL[r.status] || r.status}${r.target ? ` → ${targetText(r.target)}` : ''}${r.source === 'human' ? ' (δική σου απόφαση)' : ''}`),
        pend ? h('span', { class: 'chip' }, 'σε αναμονή ενημέρωσης') : null),
      r.status === 'ambiguous' && !pend ? h('div', { class: 'small mut' }, `Ο αριθμός υπάρχει σε ${cands.length} στοιχεία· διάλεξε σε ποιο αναφέρεται. Το κείμενο δεν αλλάζει.`) : null,
      h('div', { class: 'row small wrap' },
        cands.map(id => {
          const t = targetOf(id);
          return h('span', { class: 'row ref-cand' },
            h('button', { class: 'small' + on(id), title: 'Η παραπομπή δείχνει σε αυτό το στοιχείο', onclick: () => pick(id, `Η παραπομπή συνδέθηκε (${targetText(id)})`) }, targetText(id)),
            t ? h('button', { class: 'small ghost', title: 'Δες το στοιχείο στη σελίδα του', 'aria-label': `Δες τη σελίδα ${t.page}`, onclick: () => openPage(t.page, id) }, '⧉') : null);
        }),
        h('button', { class: 'small ghost' + (decidedNow && chosen === null ? ' primary' : ''), title: 'Το κείμενο δεν παραπέμπει σε αριθμημένο στοιχείο (π.χ. αναφορά σε άλλο βιβλίο ή αριθμός που μοιάζει με ετικέτα)',
          onclick: () => pick(null, 'Δεν είναι παραπομπή') }, 'Δεν είναι παραπομπή')));
  }));
}
