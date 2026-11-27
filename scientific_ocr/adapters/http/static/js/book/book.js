import { tabQuality } from '../book/quality.js';
import { tabHistory, tabRules, tabSettings, tabStructure } from '../book/structure.js';
import { api } from '../core/api.js';
import { $, clear, h, keepScroll, rememberDetails } from '../core/dom.js';
import { render } from '../core/nav.js';
import { S } from '../core/state.js';
import { BOOK_TABS, RELEASE_EL, STEP_EL } from '../core/strings.js';
import { ensureFresh, pendingOf } from '../ui/rebuild.js';
import { exportDialog, openNextRequired, processDialog, tabPages, tabReview } from '../export/wizard.js';
import { act } from '../ui/feedback.js';
import { profileChip } from '../book/profile.js';

// ------------------------------------------------------------------ Book
export function renderBook() {                    // the same screen (book + tab) keeps its scroll; another tab starts at the top
  const key = `${S.book?.id}|${S.tab}`;
  const same = S.bookScrollKey === key;
  S.bookScrollKey = key;
  if (same) keepScroll($('#view'), drawBook); else { drawBook(); $('#view').scrollTop = 0; }
}
function drawBook() {
  const b = S.book, v = clear($('#view')), page = h('div', { class: 'view-page' });
  v.append(page);
  const unprocessed = b.pages.filter(p => p.state === 'not_processed').length;
  const primary = b.required
    ? h('button', { class: 'primary', onclick: openNextRequired }, `Συνέχεια ελέγχου (${b.required})`)
    : h('button', { class: 'primary', onclick: () => processDialog() }, unprocessed ? 'Επεξεργασία σελίδων…' : 'Επανεπεξεργασία…');
  page.append(h('div', { class: 'book-head' },
    h('div', { class: 'grow' }, h('div', { class: 'row' }, h('h1', {}, b.title), profileChip(b), b.release_status ? h('span', { class: 'chip st-' + b.release_status }, RELEASE_EL[b.release_status]) : null),
      h('div', { class: 'kpis' }, h('span', {}, h('b', {}, `${b.processed}/${b.page_count}`), ' σελίδες επεξεργασμένες'),
        h('span', {}, h('b', {}, b.required), ' απαιτούν έλεγχο'), h('span', {}, h('b', {}, b.recommended), ' συνιστάται'),
        b.critical.length ? h('span', { class: 'kpi-bad' }, h('b', {}, b.critical.length), ' κρίσιμα') : null)),
    h('div', { class: 'row book-actions' }, b.required && unprocessed ? h('button', { onclick: () => processDialog() }, 'Επεξεργασία σελίδων…') : null, exportButton(), primary)));
  if (b.job) page.append(progressCard(b));
  if (b.stale && b.stale.length) page.append(h('div', { class: 'note' }, `${b.stale.length} παλαιότερες αποφάσεις δεν ταιριάζουν πια (άλλαξε το OCR ή η διάταξη)· τα αντίστοιχα τμήματα ξαναμπήκαν στον έλεγχο.`));
  const tabs = BOOK_TABS;
  page.append(h('nav', { class: 'tabs' }, tabs.map(([k, l]) => h('a', { href: `#/b/${b.id}/${k}`, class: S.tab === k ? 'on' : '' }, l))));
  const body = h('div'); page.append(body);
  if (GATED_TABS[S.tab] && pendingOf(b).count) {           // book-wide results: ask to rebuild first
    body.append(h('div', { class: 'empty' }, h('p', {}, `${GATED_TABS[S.tab]}: διαθέσιμο μετά την ενημέρωση του βιβλίου.`)));
    ensureFresh(GATED_TABS[S.tab]).then(() => { if (pendingOf(S.book).count) history.back(); });
    return;
  }
  Promise.resolve(({ pages: tabPages, review: tabReview, quality: tabQuality, structure: tabStructure, rules: tabRules, history: tabHistory, settings: tabSettings })[S.tab]?.(body))
    .then(() => rememberDetails(body));
}
/** Pages being re-examined by the automatic careful pass of the running job (empty when there is none). */
export function carefulPages(b) {
  const p = (b && b.progress) || {};
  return b && b.job && p.phase === 'diligent' && p.step !== 'done' ? new Set(p.phase_pages || []) : new Set();
}
export const carefulLabel = (n) => `Προσεκτική επανεξέταση ${n} σελίδων με προβλήματα διάταξης`;
// Tabs that show book-wide inference (hierarchy, groups, entities, classes): they need the banked actions applied.
const GATED_TABS = { structure: 'Η «Δομή»', quality: 'Ο «Μαζικός έλεγχος»' };
export function progressCard(b) {
  const p = b.progress || {}, j = b.job, steps = p.steps || ['prepare', 'extract', 'crossread', 'regions', 'compose', 'models', 'render'];
  const cur = steps.indexOf(p.step);
  const detail = p.step === 'extract' ? `σελ. ${p.page} · ${p.engine} (${(p.index || 0) + 1}/${p.total})` : (p.step === 'regions' || p.step === 'crossread') ? `σελ. ${p.page} (${(p.index || 0) + 1}/${p.total})`
    : p.step === 'compose' ? `σελ. ${p.page}` : '';
  const careful = carefulPages(b);
  const overall = steps.length ? Math.round(100 * (Math.max(cur, 0) + (p.total ? (p.index || 0) / p.total : 0)) / steps.length) : 0;
  return h('div', { class: 'card progress-card', style: { flexDirection: 'column', alignItems: 'stretch' } },
    h('div', { class: 'row' }, h('span', { class: 'spin' }), h('b', {}, careful.size ? carefulLabel(careful.size) : j.label), h('span', { class: 'mut' }, `${STEP_EL[p.step] || 'Αναμονή'} ${detail}`), h('span', { class: 'spacer' }),
      h('span', { class: 'small mut' }, overall + '%'),
      h('button', { onclick: () => act(() => api(`/api/jobs/${j.id}/cancel`, { body: {} }), 'Διακόπηκε· η επόμενη εκτέλεση συνεχίζει από εκεί που έμεινε', false) }, 'Διακοπή')),
    careful.size ? h('p', { class: 'small mut', style: { margin: '6px 0 0' } },
      `Δεύτερη φάση της «${j.label}»: σελίδες με 3+ σοβαρά προβλήματα διάταξης ξαναδιαβάζονται με πλήρες OCR σε μεγαλύτερη ανάλυση (σημειωμένες στη λίστα). `,
      'Οι υπόλοιπες σελίδες ελέγχονται ήδη· σε αυτές καλύτερα να περιμένεις, αλλιώς οι αποφάσεις σου θα ξαναζητηθούν όταν έρθει το νέο αποτέλεσμα.') : null,
    h('div', { class: 'steps' }, steps.map((st, i) => h('div', { class: 'step' + (i < cur ? ' done' : i === cur ? ' cur' : '') },
      h('div', { class: 'b' }, h('i', { style: { width: i === cur && p.total ? Math.round(100 * (p.index || 0) / p.total) + '%' : (i < cur ? '100%' : '0') } })), STEP_EL[st] || st))));
}
export function exportButton() {
  return h('button', { disabled: !S.book.release_status, onclick: exportDialog }, 'Εξαγωγή');
}
