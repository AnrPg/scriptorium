// After removing segments (a wrong box, a region of yours) the loop you are in goes on to its next item: the
// review loop (errors / warnings) when a removed segment was one of its items, the typo loop (the removed
// segments' proposals are done) or the exclusions loop. Removing segments outside any loop stays where it is.
import { S } from '../core/state.js';
import { boundActive, boundStep } from './boundloop.js';
import { inReviewLoop, markGone, stepReview } from './reviewloop.js';
import { typoActive, typoStep } from './typoloop.js';

/** Runs `remove` (a request through ui/feedback act(): it throws when it fails, and nothing moves), then continues
 *  the loop. `ids`: the segments it removes. */
export async function removeAndContinue(ids, remove) {
  const from = S.page?.blocks.find(b => ids.includes(b.id) && b.id === S.sel) || S.page?.blocks.find(b => ids.includes(b.id)) || null;
  const special = typoActive() || boundActive();
  const reviewing = !special && await inReviewLoop(ids);         // asked BEFORE the removal changes the queue
  const r = await remove();
  markGone(ids);
  if (typoActive()) {
    S.typo.items.forEach(t => { if (ids.includes(t.block_id)) S.typo.done.add(t.key); });
    typoStep(1);
  } else if (boundActive()) boundStep(1);
  else if (reviewing) { S.seg = null; stepReview(1, from); }
  return r;
}
