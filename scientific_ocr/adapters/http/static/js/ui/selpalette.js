// Floating palette of the SELECTION's actions (every property of the segment card applied to all selected
// segments, inspector/multiprops.js). It appears at the cursor as soon as two or more segments are selected and
// moves with the cursor like a tooltip; moving the pointer TOWARDS it parks it so it can be used (it follows again
// once the pointer goes well away). It can also be pinned where it is, or dragged by its header. A click outside
// it that is not on a segment closes it unless pinned (like × : it opens again with the next selection).
// Positioning (viewport flip/shift) by Floating UI (vendored: static/vendor/floating-ui, window.FloatingUIDOM).
import { h } from '../core/dom.js';
import { S, parseRoute } from '../core/state.js';
import { selectionProps } from '../inspector/multiprops.js';
import { dialogOpen } from '../ui/feedback.js';

const OFFSET = 18, RELEASE_PX = 260;
let el = null, body = null, pointer = { x: 160, y: 160 }, anchor = { x: 160, y: 160 };
let parked = false, pinned = false, hiddenFor = null, dragging = null, lastMove = null;

const selKey = () => [...S.multi.keys()].join('|');
const busyElsewhere = () => dialogOpen() || !!document.querySelector('.dd');      // a dropdown/dialog of the palette is open
const virtualAt = (p) => ({ getBoundingClientRect: () => ({ x: p.x, y: p.y, left: p.x, top: p.y, right: p.x, bottom: p.y, width: 0, height: 0 }) });

function place() {
  if (!el || el.hidden) return;
  const F = window.FloatingUIDOM;
  if (!F) { el.style.left = Math.min(anchor.x + OFFSET, innerWidth - el.offsetWidth - 8) + 'px'; el.style.top = Math.min(anchor.y + OFFSET, innerHeight - el.offsetHeight - 8) + 'px'; return; }
  F.computePosition(virtualAt(anchor), el, { strategy: 'fixed', placement: 'bottom-start',
    // kept inside the viewport on BOTH axes (shift's cross axis is the vertical one for a bottom placement), and never
    // taller than the room there is (it scrolls inside)
    middleware: [F.offset(OFFSET), F.flip({ padding: 8 }), F.shift({ padding: 8, crossAxis: true }),
      F.size({ padding: 8, apply: ({ availableHeight }) => { el.style.maxHeight = `${Math.max(220, Math.min(560, availableHeight))}px`; } })] })
    .then(({ x, y }) => { el.style.left = `${x}px`; el.style.top = `${y}px`; });
}
function distanceToBox(p) {
  const r = el.getBoundingClientRect();
  const dx = Math.max(r.left - p.x, 0, p.x - r.right), dy = Math.max(r.top - p.y, 0, p.y - r.bottom);
  return Math.hypot(dx, dy);
}
function onMove(e) {
  pointer = { x: e.clientX, y: e.clientY };
  if (!el || el.hidden) return;
  if (dragging) { el.style.left = `${e.clientX - dragging.dx}px`; el.style.top = `${e.clientY - dragging.dy}px`; return; }
  if (pinned || busyElsewhere() || el.contains(e.target)) { parked = !pinned ? true : parked; lastMove = pointer; return; }
  if (parked) {                                          // follows again once the pointer is well away
    if (distanceToBox(pointer) > RELEASE_PX) parked = false; else { lastMove = pointer; return; }
  }
  // heading towards the palette: park it (otherwise it would slide away from the pointer)
  if (lastMove) {
    const r = el.getBoundingClientRect(), cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
    const mx = pointer.x - lastMove.x, my = pointer.y - lastMove.y, m = Math.hypot(mx, my);
    const tx = cx - lastMove.x, ty = cy - lastMove.y, t = Math.hypot(tx, ty);
    if (m > 3 && t > 0 && (mx * tx + my * ty) / (m * t) > 0.8) { parked = true; lastMove = pointer; return; }
  }
  lastMove = pointer;
  anchor = pointer;
  place();
}
function build() {
  el = h('div', { class: 'sel-palette', role: 'dialog', 'aria-label': 'Ενέργειες επιλογής', hidden: true });
  const head = h('div', { class: 'sel-palette-head' },
    h('span', { class: 'grow sel-palette-title' }),
    h('button', { class: 'icon small', title: 'Καρφίτσωμα εδώ (δεν ακολουθεί τον κέρσορα)', onclick: () => { pinned = !pinned; parked = false; paintHead(); } }, '📌'),
    h('button', { class: 'icon small', 'aria-label': 'Κλείσιμο', title: 'Κλείσιμο (ξανανοίγει με νέα επιλογή)', onclick: () => { hiddenFor = selKey(); el.hidden = true; } }, '×'));
  head.addEventListener('pointerdown', (e) => {               // drag by the header: pinned where dropped
    if (e.target.closest('button')) return;
    const r = el.getBoundingClientRect();
    dragging = { dx: e.clientX - r.left, dy: e.clientY - r.top };
    head.setPointerCapture(e.pointerId);
  });
  head.addEventListener('pointerup', () => { if (dragging) { dragging = null; pinned = true; paintHead(); } });
  body = h('div', { class: 'sel-palette-body' });
  el.append(head, body);
  document.body.append(el);
  document.addEventListener('pointermove', onMove, { passive: true });
  // a click anywhere else closes it (side panel, empty page area, a menu…) — except on a segment, which changes
  // the selection (a new selection opens it again), and inside its own dropdowns/dialogs
  document.addEventListener('pointerdown', (e) => {
    if (el.hidden || pinned || el.contains(e.target) || e.target.closest?.('[data-seg], .dd, dialog')) return;
    hiddenFor = selKey(); el.hidden = true;
  }, true);
  window.addEventListener('resize', place);
  window.addEventListener('hashchange', () => { if (parseRoute().view !== 'ws') el.hidden = true; });
}
function paintHead() {
  el.querySelector('.sel-palette-title').textContent = `${S.multi.size} επιλεγμένα${pinned ? ' · καρφιτσωμένο' : ''}`;
  el.classList.toggle('pinned', pinned);
}
// called after every workspace render
export function updateSelectionPalette() {
  if (!el) build();
  const show = parseRoute().view === 'ws' && S.multi.size >= 2 && hiddenFor !== selKey();
  if (!show) { el.hidden = true; if (S.multi.size < 2) { hiddenFor = null; pinned = false; parked = false; } return; }
  const wasHidden = el.hidden;
  body.replaceChildren(selectionProps([...S.multi.keys()], S.multiProps = S.multiProps || { prop: null }));
  el.hidden = false;
  paintHead();
  if (wasHidden) { anchor = pointer; parked = false; lastMove = null; }   // appears at the cursor
  if (!pinned) place();
}
