import { carefulLabel, carefulPages, renderBook } from '../book/book.js';
import { B, api } from '../core/api.js';
import { $, clear, h, isActiveJob, isTyping } from '../core/dom.js';
import { loadBook, loadPage, navRecord, renderNav, screenTitle } from '../core/nav.js';
import { S, go } from '../core/state.js';
import { renderLibrary } from '../library/library.js';
import { act, dialogOpen, modal, toast } from '../ui/feedback.js';
import { EMBED } from '../ui/page_overlay.js';
import { fmtEstimate, startRebuild } from '../ui/rebuild.js';
import { renderWorkspace } from '../workspace/workspace.js';

// ------------------------------------------------------------------ chrome: breadcrumbs, undo/redo, jobs
export function renderChrome() {
  const c = clear($('#crumbs')), r = S.route;
  navRecord(screenTitle(r));
  renderNav();
  c.append(h('a', { href: '#/' }, 'Βιβλιοθήκη'));
  if (S.book && r.view !== 'library') {
    c.append(h('span', { class: 'sep' }, '/'), r.view === 'ws' ? h('a', { href: `#/b/${S.book.id}` }, S.book.title) : h('span', { class: 'cur' }, S.book.title));
    if (r.view === 'ws') c.append(h('span', { class: 'sep' }, '/'), h('span', { class: 'cur' }, `Σελίδα ${r.page}`));
  }
  $('#undo-redo').hidden = !S.book || r.view === 'library';
  if (S.book) { $('#btn-undo').disabled = !S.book.can_undo; $('#btn-redo').disabled = !S.book.can_redo; }
  renderJobsPill();
  renderPendingPill();
  renderPausePill();
}
// Pause/resume of the per-page updates (only in books whose actions are banked). Paused: page-local actions are
// recorded and wait, like the book-wide ones, for «Ενημέρωση βιβλίου» — quick edits now, one rebuild at the end.
export function renderPausePill() {
  const pill = $('#pause-pill'), b = S.book;
  pill.hidden = !b || !S.route || S.route.view === 'library' || !b.banks_actions;
  if (pill.hidden) return;
  const paused = !!b.page_updates_paused;
  pill.classList.toggle('paused', paused);
  clear(pill).append(paused ? '▶ Ενημέρωση σελίδας σε παύση · Συνέχιση' : '⏸');
  pill.title = paused
    ? 'Οι αλλαγές σου καταγράφονται αλλά η σελίδα δεν ξαναϋπολογίζεται· φαίνονται με την «Ενημέρωση βιβλίου». Πάτα για συνέχιση (οι σελίδες που άλλαξαν ενημερώνονται αμέσως).'
    : 'Παύση της ενημέρωσης σελίδας: γρήγορες αλλαγές χωρίς ξαναϋπολογισμό μετά από κάθε κλικ, και μία «Ενημέρωση βιβλίου» στο τέλος';
  pill.setAttribute('aria-label', paused ? 'Συνέχιση ενημέρωσης σελίδας' : 'Παύση ενημέρωσης σελίδας');
  pill.onclick = async () => {
    try { await api(`${B(b.id)}/settings`, { body: { page_updates_paused: !paused } }); }
    catch (e) { toast(e.message, { error: true }); return; }
    b.page_updates_paused = !paused; renderPausePill(); kickPoll();
    toast(paused ? 'Η ενημέρωση σελίδας συνεχίζει' : 'Ενημέρωση σελίδας σε παύση· οι αλλαγές φαίνονται με την «Ενημέρωση βιβλίου»');
  };
}
/** «N αλλαγές σε αναμονή · Ενημέρωση (~X)»: banked actions of the open book; click = rebuild now. */
export function renderPendingPill() {
  const pill = $('#pending-pill'), p = (S.book && S.route && S.route.view !== 'library' && S.book.pending) || null;
  const rebuilding = S.book && S.jobs.some(j => j.book === S.book.id && j.kind === 'reproject' && isActiveJob(j));
  pill.hidden = !p || !p.count || rebuilding;
  if (pill.hidden) return;
  clear(pill).append(`${p.count} αλλαγ${p.count === 1 ? 'ή' : 'ές'} σε αναμονή · `, h('b', {}, `Ενημέρωση βιβλίου${p.estimate_s ? ' (' + fmtEstimate(p.estimate_s) + ')' : ''}`));
  pill.onclick = () => startRebuild();
}
export async function doUndo() { if (!S.book) return; const r = await act(() => api(`${B(S.book.id)}/undo`, { body: {} }), null, false); if (r && r.length) toast('Αναιρέθηκε'); await refresh(); }
export async function doRedo() { if (!S.book) return; const r = await act(() => api(`${B(S.book.id)}/redo`, { body: {} }), null, false); if (r && r.length) toast('Επαναλήφθηκε'); await refresh(); }
export let pollTimer = null, lastActive = 0;
export function kickPoll() { clearTimeout(pollTimer); pollTimer = setTimeout(poll, 600); }
export async function poll() {
  try {
    const jobs = await api('/api/jobs');
    const active = jobs.filter(isActiveJob);
    const finished = S.jobs.filter(j => isActiveJob(j) && !active.find(a => a.id === j.id));
    S.jobs = jobs;
    renderJobsPill(); renderPendingPill();
    for (const j of finished) { const now = jobs.find(x => x.id === j.id); if (now && now.state === 'failed') toast(`Απέτυχε: ${now.label}`, { error: true });
      else if (now && now.warning === 'integrity') toast(`${now.label}: ολοκληρώθηκε, αλλά ο έλεγχος ακεραιότητας βρήκε κρίσιμο εύρημα — πάτα για να το δεις`,
        { error: true, onclick: () => showCritical(now.book) }); }
    // Every view of the book is derived from its projection: when the book's revision changed (an edit was
    // re-projected — even one that finished before we ever saw its job), reload whatever is on screen.
    let busy = false;
    if (S.book && S.route.view !== 'library') {
      const r = await api(`${B(S.book.id)}/rev`);
      const typing = isTyping(document.activeElement) || dialogOpen();
      if (r.rev !== S.bookRev && !typing) await refresh(true);              // never under the reviewer's cursor: retried next poll
      // a book rebuild applies the actions recorded when it STARTED; ones made while it ran wait for the next one
      const rebuilt = finished.some(j => j.book === S.book.id && j.kind === 'reproject' && (jobs.find(x => x.id === j.id) || {}).state === 'done');
      if (rebuilt && r.pending > 0)
        toast(`Η ενημέρωση βιβλίου ολοκληρώθηκε. ${r.pending === 1 ? '1 αλλαγή που έκανες' : r.pending + ' αλλαγές που έκανες'} όσο έτρεχε περιμέν${r.pending === 1 ? 'ει' : 'ουν'} την επόμενη.`);
      else if (active.length && S.route.view === 'book' && S.tab === 'pages') {      // progress only: re-render when it changed
        const before = JSON.stringify(S.book);
        await loadBook(S.book.id);
        if (JSON.stringify(S.book) !== before) renderBook();
      }
      busy = r.busy;
    }
    lastActive = active.length || busy || (S.recentEdit && Date.now() - S.recentEdit < 15000) ? 1 : 0;   // poll fast right after an edit
  } catch (e) { /* server restarting */ }
  // an embedded page (overlay iframe) leaves the steady loop to its parent; it polls only to reflect its own edits
  if (!EMBED || lastActive) pollTimer = setTimeout(poll, lastActive ? 2000 : 8000);
}
export function renderJobsPill() {
  const active = S.jobs.filter(isActiveJob), pill = $('#jobs-pill');
  pill.hidden = !active.length; clear(pill);
  const label = (j) => (S.book && j.book === S.book.id && carefulPages(S.book).size) ? carefulLabel(carefulPages(S.book).size) : j.label;
  if (active.length) pill.append(h('span', { class: 'spin' }), active.length === 1 ? label(active[0]) : `${active.length} εργασίες`);
}
// a page update runs after every page-local click: listed only while it runs or if it failed
export const listed = (j) => j.kind !== 'pages' || isActiveJob(j) || j.state === 'failed';
export function renderJobsPop() {
  const p = clear($('#jobs-pop'));
  const finished = S.jobs.filter(j => !isActiveJob(j));
  p.append(h('div', { class: 'row', style: { padding: '4px 8px' } }, h('h2', { class: 'grow' }, 'Εργασίες'),
    finished.length ? h('button', { class: 'ghost small', title: 'Διαγραφή όλων των ολοκληρωμένων/αποτυχημένων εργασιών και των log τους', onclick: async () => {
      await api('/api/jobs/clear', { body: {} }); S.jobs = S.jobs.filter(isActiveJob); renderJobsPop(); } }, 'Καθαρισμός λίστας') : null));
  for (const j of S.jobs.filter(listed).slice(0, 12)) {
    const live = isActiveJob(j);
    p.append(h('div', { class: 'list-item', style: { cursor: 'default' } },
      live ? h('span', { class: 'spin' }) : h('span', { class: 'dot ' + (j.warning ? 'recommended' : j.state === 'done' ? 'approved' : j.state === 'failed' ? 'required' : 'not_needed') }),
      h('div', { class: 'grow' }, h('div', {}, j.label), h('div', { class: 'small mut' }, `${j.book} · ${j.warning === 'integrity' ? 'ολοκληρώθηκε · κρίσιμο εύρημα ακεραιότητας' : ({ queued: 'σε αναμονή', running: 'εκτελείται', done: 'ολοκληρώθηκε', failed: 'απέτυχε', cancelled: 'διακόπηκε', interrupted: 'διεκόπη' })[j.state] || j.state}`)),
      live ? h('button', { onclick: () => act(() => api(`/api/jobs/${j.id}/cancel`, { body: {} }), 'Η εργασία διακόπηκε· συνεχίζει από εκεί που έμεινε την επόμενη φορά', false) }, 'Διακοπή')
           : [h('button', { class: 'ghost small', onclick: () => showLog(j) }, 'Σύνοψη'), removeJobButton(j, renderJobsPop)]));
  }
}
/** ✕: delete a finished job (and its log) from the task list. */
export function removeJobButton(j, after) {
  return h('button', { class: 'ghost small', title: 'Διαγραφή από τη λίστα (και του log της)', 'aria-label': 'Διαγραφή εργασίας', onclick: async (e) => {
    e.stopPropagation();
    try { await api(`/api/jobs/${j.id}/remove`, { body: {} }); } catch (err) { toast(err.message, { error: true }); return; }
    S.jobs = S.jobs.filter(x => x.id !== j.id); if (S.book && S.book.jobs) S.book.jobs = S.book.jobs.filter(x => x.id !== j.id); after && after(); } }, '✕');
}
/** The book's «Έλεγχος» tab, scrolled to its critical findings, the ones the last build added highlighted. */
export function showCritical(bookId) { S.critFocus = bookId; go(`#/b/${encodeURIComponent(bookId)}/review`); }
export async function showLog(j) {             // the plain-language job report (raw log folded inside)
  const { jobSummaryDialog } = await import('../ui/joblog.js');
  jobSummaryDialog(j);
}
export async function refresh(keepSel) {
  if (!S.book) { if (S.route.view === 'library') await renderLibrary(); return; }
  await loadBook(S.book.id);
  if (S.route.view === 'ws') { await loadPage(S.route.page, keepSel ? S.sel : null); renderWorkspace(); }
  else if (S.route.view === 'book') renderBook();
  renderChrome();
}
