import { $ } from '../core/dom.js';

// ---------------------------------------------------------------- hover sync: page box <-> list item
// Hovering a segment on the page highlights its list row and vice versa (same id in data-seg); the counterpart
// is scrolled into view only when it is off-screen. No re-render: it must feel instant.
export function hoverSeg(id, from) {
  document.querySelectorAll('.hover-sync').forEach(el => el.classList.remove('hover-sync'));
  if (!id) return;
  const sel = `[data-seg="${CSS.escape(id)}"]`;
  const box = document.querySelector('.canvas ' + sel) || document.querySelector('.pv-body ' + sel), row = document.querySelector('.seglist ' + sel);
  [box, row].forEach(el => el && el.classList.add('hover-sync'));
  const other = from === 'page' ? row : box, pane = other && other.closest(from === 'page' ? '.insp' : '.canvas-wrap, .output-wrap');
  if (other && pane) {
    const r = other.getBoundingClientRect(), pr = pane.getBoundingClientRect();
    if (r.top < pr.top || r.bottom > pr.bottom) other.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }
}
