// Glossary pages (pipeline.glossary): the reviewer's word on a page — «Γλωσσάριο» (its entries become term +
// definition; in RAG one chunk each) or not (its paragraphs read as text). An undoable glossary_page event; the
// detection's own evidence is only a hint. Applies with the next book update.
import { B, api } from '../core/api.js';
import { h } from '../core/dom.js';
import { S } from '../core/state.js';
import { act } from '../ui/feedback.js';

export const isGlossaryPage = (pg) => (pg?.blocks || []).some(x => x.glossary || x.glossary_letter);

export function markGlossary(page, glossary) {
  return act(() => api(`${B(S.book.id)}/glossary-pages`, { body: { pages: [page], glossary } }),
    glossary ? `Σελ. ${page}: γλωσσάριο (με την επόμενη ενημέρωση)` : `Σελ. ${page}: όχι γλωσσάριο (με την επόμενη ενημέρωση)`);
}

/** The page's toggle: marks a page a glossary, or says a detected one is not. */
export function glossaryToggle(pg, cls = 'small ghost') {
  const on = isGlossaryPage(pg);
  return h('button', { class: cls, title: on ? 'Οι παράγραφοι της σελίδας διαβάζονται ως απλό κείμενο (αναιρείται· ισχύει με την επόμενη ενημέρωση)'
    : 'Η σελίδα είναι γλωσσάριο: κάθε έντονος όρος με τον ορισμό του γίνεται λήμμα (στο RAG ένα κομμάτι ανά λήμμα· αναιρείται)',
    onclick: () => markGlossary(pg.page, !on) }, on ? 'Δεν είναι γλωσσάριο' : 'Σελίδα γλωσσαρίου');
}
