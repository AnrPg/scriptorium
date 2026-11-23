import { doUndo, kickPoll } from '../core/chrome.js';
import { $, h } from '../core/dom.js';
import { api } from '../core/api.js';
import { S } from '../core/state.js';

// ------------------------------------------------------------------ feedback
export function toast(msg, opts = {}) {
  const t = h('div', { class: 'toast' + (opts.error ? ' err' : '') }, msg,
    opts.undo ? h('button', { onclick: async () => { t.remove(); await doUndo(); } }, 'Αναίρεση') : null);
  $('#toasts').append(t);
  setTimeout(() => t.remove(), opts.error ? 8000 : 6000);
}
// An action is confirmed only when it is REFLECTED: the request is recorded (an event) and the book's outputs
// were rebuilt from it (a re-projection job). The confirmation waits for the rebuild; if none follows (an action
// that changes no output) it appears shortly after. In a large book the action is BANKED instead (recorded, no
// rebuild yet: rev.pending > 0 and nothing rebuilding): then it is confirmed at once, as waiting for the rebuild.
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function reflected(bookId, before) {
  const t0 = Date.now();
  while (Date.now() - t0 < 120000) {
    await sleep(500);
    let r;
    try { r = await api(`/api/books/${encodeURIComponent(bookId)}/rev`); } catch { return; }
    if (!r.rebuilding && (r.projection !== before || r.pending > 0 || Date.now() - t0 > 2500)) return r;
  }
}
export async function act(fn, okMsg, undoable = true) {
  const book = S.book && S.book.id;
  let before = null, rev0 = null;
  if (book) { try { rev0 = await api(`/api/books/${encodeURIComponent(book)}/rev`); before = rev0.projection; } catch { before = null; } }
  let r;
  try { r = await fn(); } catch (e) { toast(e.message, { error: true }); throw e; }
  S.recentEdit = Date.now(); kickPoll();
  const after = book && before !== null ? await reflected(book, before) : null;
  // page-local actions are already visible (their page was rebuilt); book-wide ones wait for «Ενημέρωση βιβλίου»
  const waiting = after && !after.rebuilding && (after.pending_waiting || 0) > ((rev0 && rev0.pending_waiting) || 0);
  if (okMsg) toast(waiting ? `${okMsg} · στο βιβλίο με την επόμενη «Ενημέρωση βιβλίου»` : okMsg, { undo: undoable && S.book });
  kickPoll();
  return r;
}
// Dialogs stack: each call opens its own <dialog> (removed when closed), so a dialog opened from another one
// (e.g. raw engine output from the lineage graph) appears on top instead of replacing it.
export function openDialog(cls = '') {
  const d = h('dialog', { class: cls });
  document.body.append(d);
  d.addEventListener('close', () => d.remove());
  // a press AND release on the backdrop (outside the box) closes it — a text selection that starts inside and ends
  // outside does not; the click that follows is swallowed so it never reaches the page underneath
  const outside = (e) => { const r = d.getBoundingClientRect(); return e.target === d && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom); };
  let pressedOutside = false;
  d.addEventListener('mousedown', (e) => { pressedOutside = outside(e); });
  d.addEventListener('mouseup', (e) => {
    if (pressedOutside && outside(e)) {
      const swallow = (ev) => { ev.preventDefault(); ev.stopPropagation(); };
      window.addEventListener('click', swallow, { capture: true, once: true });
      setTimeout(() => window.removeEventListener('click', swallow, true), 400);
      d.close();
    }
    pressedOutside = false;
  });
  return d;
}
export const dialogOpen = () => !!document.querySelector('dialog[open]');
export const closeTopDialog = () => [...document.querySelectorAll('dialog[open]')].pop()?.close();
export function modal(title, body, actions) {
  const d = openDialog();
  d.append(h('h2', {}, title), body, h('div', { class: 'row end' }, actions.map(a =>
    h('button', { class: a.cls || '', onclick: async () => { if (!a.run || (await a.run()) !== false) d.close(); } }, a.label))));
  d.showModal();
  return d;
}
