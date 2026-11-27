import { B, api } from '../core/api.js';
import { S, go } from '../core/state.js';
import { inFlight, toast } from '../ui/feedback.js';
import { pendingOf } from '../ui/rebuild.js';
import { reveal } from '../workspace/reveal.js';
import { selectBlock } from '../workspace/workspace.js';

// ------------------------------------------------------------------ the review loop (whole book)
// One loop over everything that needs a human (review required = errors, recommended = warnings), in book order
// (page, then reading order), across pages, wrapping at the end. Segments you already decided — even if the
// decision is still banked for the next rebuild — are skipped. Used by J/K, the ↑/↓ of the segment panel and the
// automatic advance after a decision.

// What the loop walks (server queue levels): 'required' = errors, 'recommended' = warnings, 'all' = both.
// A per-reviewer preference (this browser). Typos and the profile's exclusions are loops of their own
// (inspector/typoloop.js, inspector/boundloop.js), chosen in the same selector (inspector/loops.js).
export const LEVELS = ['required', 'recommended', 'all'];
export function loopLevel() { try { const v = localStorage.getItem('ocr.loopLevel'); return LEVELS.includes(v) ? v : 'all'; } catch { return 'all'; } }
export function setLoopLevel(v) { try { localStorage.setItem('ocr.loopLevel', v); } catch { /* storage blocked */ } cache.key = null; }

let cache = { key: null, items: [] };
async function bookQueue() {
  const key = `${S.book.id}|${S.book.rev}|${loopLevel()}`;
  if (cache.key !== key) cache = { key, items: await api(`${B(S.book.id)}/queue?level=${loopLevel()}&order=book`) };
  return cache.items;
}
// decided = recorded but not yet rebuilt (pending) or still being sent (feedback.inFlight: you moved on at once)
// removed (a wrong box, a region of yours): until the rebuild it is still in the server's queue
const gone = new Set();
export function markGone(ids) { ids.forEach(id => gone.add(id)); }
const decidedNow = () => new Set([...pendingOf().events.filter(e => e.type === 'decision' || e.type === 'block_hide').map(e => e.block_id), ...inFlight, ...gone]);
/** Some of these segments are items of the review loop (as it stands now). */
export async function inReviewLoop(ids) { try { return (await bookQueue()).some(x => ids.includes(x.id)); } catch { return false; } }

/** The loop with already-decided segments removed (`keep`: a segment kept even if decided, e.g. the current one). */
async function loop(keep) {
  const done = decidedNow();
  return (await bookQueue()).filter(x => x.id === keep || !done.has(x.id));
}

/** Where the current segment (or page) sits in the loop: index of the item at or after it. `from`: a segment that
 *  is no longer on the page (just removed) — the loop goes on from where it was. */
function position(q, dir, from = null) {
  const cur = from || (S.sel && S.page ? S.page.blocks.find(b => b.id === S.sel) : null);
  const i = cur ? q.findIndex(x => x.id === cur.id) : -1;
  if (i >= 0) return i + dir;
  const page = from ? from.page : S.page ? S.page.page : 0, ro = cur ? cur.reading_order : -1;
  const after = q.findIndex(x => x.page > page || (x.page === page && x.reading_order > ro));
  if (dir > 0) return after < 0 ? q.length : after;
  return (after < 0 ? q.length : after) - 1;
}

/** Go to the next (dir 1) or previous (dir -1) segment that needs review, anywhere in the book. */
export async function stepReview(dir = 1, from = null) {
  if (!S.book) return;
  let q;
  try { q = await loop(from ? from.id : S.sel); } catch (e) { toast(e.message, { error: true }); return; }
  const open = q.filter(x => x.id !== S.sel || !decidedNow().has(x.id));
  if (!open.length || (open.length === 1 && open[0].id === S.sel && decidedNow().has(S.sel))) {
    return toast(pendingOf().count ? 'Δεν έμεινε τίποτα για έλεγχο (κάποιες αποφάσεις περιμένουν την ενημέρωση του βιβλίου) 🎉' : 'Ο έλεγχος ολοκληρώθηκε 🎉');
  }
  let i = position(q, dir, from);
  if (i >= q.length) { i = 0; toast('Τέλος του βιβλίου· συνέχεια από την αρχή'); }
  if (i < 0) { i = q.length - 1; toast('Αρχή του βιβλίου· συνέχεια από το τέλος'); }
  const t = q[i];
  reveal(t.id);                                   // centred, also on the page already open (workspace/reveal.js)
  if (S.page && t.page === S.page.page) selectBlock(t.id);
  else go(`#/b/${S.book.id}/p/${t.page}/${t.id}`);
}

/** "k / N" for the segment panel: its place in the whole-book loop (null if it is not in the loop). */
export async function loopPosition(blockId) {
  const q = await loop(blockId);
  const i = q.findIndex(x => x.id === blockId);
  return { i: i + 1, total: q.length };
}
