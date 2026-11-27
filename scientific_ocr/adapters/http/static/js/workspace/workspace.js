import { B, api } from '../core/api.js';
import { refresh } from '../core/chrome.js';
import { loopLevel, setLoopLevel, stepReview } from '../inspector/reviewloop.js';
import { $, clear, h, keepScroll, pctStyle } from '../core/dom.js';
import { NAV, navSave } from '../core/nav.js';
import { R, S, go, kindLabel, outOfOutput, parseRoute, profileHidden } from '../core/state.js';
import { REVIEW_EL, STATE_EL } from '../core/strings.js';
import { finishLink } from '../inspector/captions.js';
import { queueNeighbours, renderInspector } from '../inspector/inspector.js';
import { segText, toggleMulti, toggleSelectMode } from '../inspector/multi.js';
import { act, closeTopDialog, modal, toast } from '../ui/feedback.js';
import { labelPicker } from '../ui/math.js';
import { EMBED } from '../ui/page_overlay.js';
import { hoverSeg } from '../workspace/hover.js';
import { outputView } from '../workspace/output.js';
import { updateSelectionPalette } from '../ui/selpalette.js';
import { boundActive, boundBar, boundMembers, startBoundLoop } from '../inspector/boundloop.js';
import { honourReveal, revealPending } from '../workspace/reveal.js';
import { startTypoLoop, typoActive, typoBar } from '../inspector/typoloop.js';

// ------------------------------------------------------------------ Page workspace
// ------------------------------------------------------------------ page views and the tools each one supports
// One table drives the toolbar, the keyboard shortcuts and the help: a tool that makes no sense in a view is
// neither shown nor reachable by its key there.
export const VIEWS = [['scan', 'Σάρωση', 'Η σάρωση με τα πλαίσια', 'Σάρωση'], ['output', 'Αποτέλεσμα', 'Πώς διαβάζεται η σελίδα στην έξοδο (προσέγγιση)', 'Έξοδος'],
  ['markdown', 'Markdown', 'Το ακριβές κομμάτι του αρχείου εξόδου', 'MD']];
export const VIEW_TOOLS = { scan: ['zoom', 'boxes', 'region', 'select', 'omit', 'diligent'], output: ['zoom', 'omit', 'diligent'], markdown: ['zoom', 'diligent'] };
export const toolsFor = (view) => VIEW_TOOLS[view] || VIEW_TOOLS.scan;
export const toolOn = (t) => !!S.page?.info?.state && toolsFor(S.leftView).includes(t);
// Selection transitions: the ONLY places that change the multi-selection and its modes.
export function clearSelection() { S.multi.clear(); S.multiOpen = false; S.selectMode = false; }
// Members may live on other pages (groups span pages): their page comes from the block when it is on this page, else
// from the existing selection entry, else from pageOf(id) supplied by the caller; null = unknown (never "this page",
// so the merge guard — one page only — is not fooled).
export function setSelection(ids, { open = false, pageOf = null } = {}) {
  const prev = new Map(S.multi);
  S.multi.clear();
  for (const id of ids) {
    const b = S.page.blocks.find(x => x.id === id), old = prev.get(id);
    S.multi.set(id, b ? { page: b.page, text: segText(b), label: b.engine_label }
      : { page: old?.page ?? pageOf?.(id) ?? null, text: old?.text ?? id, label: old?.label ?? '' });
  }
  S.selectMode = true; S.multiOpen = open && S.multi.size >= 2;
}
export function reorderSelection(ids) { const m = new Map(ids.map(k => [k, S.multi.get(k)])); S.multi.clear(); m.forEach((v, k) => S.multi.set(k, v)); }
export function leaveScanModes() { S.drawing = false; S.draft = null; S.selectMode = false; S.linking = null; }   // scan-only modes
export async function setView(k) {
  // page output views use the page preview: available at once (only book-wide views wait for a full rebuild)
  if (k !== 'scan') leaveScanModes(); S.leftView = k; localStorage.setItem('ocr.leftView', k); renderWorkspace();
}
// "Out of the output": real content the reviewer leaves out of the READER exports; it stays in the book model
// (review, hierarchy, entities, JSON). Not the same as removing a wrong box (inspector › layout).
export const omitTargets = () => S.multi.size ? [...S.multi.keys()] : (S.sel ? [S.sel] : []);
const blockById = (id) => (S.page?.blocks || []).find(b => b.id === id);
export const omitState = () => { const ids = omitTargets(); return ids.length && ids.every(id => outOfOutput(blockById(id))); };
export function toggleOmit() {
  const ids = omitTargets();
  if (!ids.length) return toast('Διάλεξε πρώτα τμήμα(τα)', { error: true });
  const include = omitState();
  act(() => api(`${B(S.book.id)}/output`, { body: { blocks: ids, include } }), include ? `Ξανά στην έξοδο: ${ids.length}` : `Εκτός εξόδου: ${ids.length} (μένουν στο μοντέλο)`);
}
// Delete / ⌫: remove WRONG segments (noise, duplicates, bad boxes) — the selected one or all selected, one undo.
// Right content that should not be in the text is "out of the output" (O), not this.
export async function removeSegments() {
  const ids = omitTargets();
  if (!ids.length) return toast('Διάλεξε πρώτα τμήμα(τα)', { error: true });
  const n = ids.length;
  const r = await act(() => api(`${B(S.book.id)}/segments/remove`, { body: { blocks: ids } }),
    n > 1 ? `Αφαιρέθηκαν ${n} λάθος τμήματα (⌘Z για αναίρεση)` : 'Αφαιρέθηκε το λάθος τμήμα (⌘Z για αναίρεση)');
  if (r) { clearSelection(); S.sel = null; S.seg = null; renderWorkspace(); }
}
export const TOOLS = {
  zoom: () => h('div', { class: 'seg-ctl tool-zoom' }, h('button', { onclick: () => zoom(-0.15), title: 'Σμίκρυνση' }, '−'),
    h('button', { onclick: () => { S.zoom = 1; renderWorkspace(); }, title: 'Προσαρμογή' }, Math.round(S.zoom * 100) + '%'), h('button', { onclick: () => zoom(0.15), title: 'Μεγέθυνση' }, '+')),
  boxes: () => h('button', { class: S.showBoxes ? 'on' : '', onclick: () => { S.showBoxes = !S.showBoxes; renderWorkspace(); }, title: 'Εμφάνιση πλαισίων' }, 'Πλαίσια', h('kbd', {}, 'B')),
  region: () => h('button', { class: S.drawing ? 'on' : '', onclick: toggleDraw, title: 'Σχεδίαση νέας περιοχής' }, '＋ Περιοχή', h('kbd', {}, 'R')),
  select: () => h('button', { class: S.selectMode ? 'on' : '', onclick: toggleSelectMode, title: 'Επιλογή πολλών τμημάτων για ομαδοποίηση ή συγχώνευση (ή Shift+κλικ)' }, 'Επιλογή', h('kbd', {}, 'S')),
  omit: () => h('button', { class: omitState() ? 'on' : '', disabled: !omitTargets().length, onclick: toggleOmit,
    title: 'Το επιλεγμένο (ή τα επιλεγμένα) δεν μπαίνουν στο Markdown/PDF· μένουν στο βιβλίο, στον έλεγχο και στα JSON. Για λάθος πλαίσιο χρησιμοποίησε «Αφαίρεση λάθος πλαισίου».' },
    omitState() ? 'Στην έξοδο' : 'Εκτός εξόδου', h('kbd', {}, 'O')),
  diligent: () => h('button', { onclick: diligentDialog, title: 'Νέα εξαγωγή της σελίδας με υψηλότερη ανάλυση και πλήρες OCR' }, 'Προσεκτική επανεξέταση'),
};

// Re-rendering rebuilds the workspace DOM; scroll positions of its panes are kept when the screen is the same
// (same book, page and view), so clicks that only change state (multi-select, tools) never move the page.
export function renderWorkspace() {
  const key = `${S.book?.id}|${S.page?.page}|${S.leftView}`;
  const same = S.wsScrollKey === key;
  if (!same) { S.scrolledTo = null; S.outputScrolledTo = null; }   // new screen: bring the selected segment into view once
  S.wsScrollKey = key;
  // a loop step asked to show its item (workspace/reveal.js): that outranks keeping the old scroll position
  if (same && !revealPending()) keepScroll($('#view'), buildWorkspace); else buildWorkspace();
  updateSelectionPalette();
}
// The side panel is resizable like a deck. Wide screens: a vertical handle between page and panel (drag, ←/→ when
// focused, double-click resets). Narrow screens (≤900px, the panel sits below the page): the handle becomes a bottom
// sheet grip (drag, ↑/↓, a tap cycles peek → half → full). Sizes are remembered per browser and clamped to the window.
const STACKED = window.matchMedia('(max-width:900px)');
const KEY = { w: 'ocr.sideWidth', h: 'ocr.sideHeight' }, SIDE_MIN = 300, SIDE_MAX_FRAC = 0.7;
const SHEET_STOPS = [0.18, 0.46, 0.86];                     // peek, half, full (fractions of the workspace height)
const load = (k) => { try { return +localStorage.getItem(k) || null; } catch { return null; } };
const save = (k, v) => { try { v ? localStorage.setItem(k, String(Math.round(v))) : localStorage.removeItem(k); } catch { /* storage unavailable */ } };
let splitAbort = null;      // window/media listeners of the current workspace's splitter, dropped on each rebuild
function splitter(ws) {
  splitAbort?.abort(); splitAbort = new AbortController();
  const { signal } = splitAbort;
  const box = () => ws.getBoundingClientRect();
  const clampW = (w) => Math.max(SIDE_MIN, Math.min(window.innerWidth * SIDE_MAX_FRAC, w));
  const clampH = (v) => Math.max(box().height * SHEET_STOPS[0], Math.min(box().height * SHEET_STOPS[2], v));
  const set = (prop, v) => { v ? ws.style.setProperty(prop, Math.round(v) + 'px') : ws.style.removeProperty(prop); };
  const restore = () => {
    const w = load(KEY.w), hh = load(KEY.h);
    set('--side-w', w && clampW(w)); set('--side-h', hh && ws.isConnected && box().height ? clampH(hh) : hh);
  };
  const sizeNow = () => { const r = ws.querySelector('.ws-right')?.getBoundingClientRect(); return STACKED.matches ? r?.height : r?.width; };
  const el = h('div', { class: 'ws-split', role: 'separator', tabindex: '0', 'aria-label': 'Μέγεθος πάνελ' });
  const label = () => {
    el.setAttribute('aria-orientation', STACKED.matches ? 'horizontal' : 'vertical');
    el.title = STACKED.matches ? 'Σύρε για αλλαγή ύψους · πάτημα: μικρό / μισό / μεγάλο'
      : 'Σύρε για αλλαγή πλάτους · διπλό κλικ: επαναφορά · ←/→ με το πληκτρολόγιο';
  };
  label(); STACKED.addEventListener('change', label, { signal });
  requestAnimationFrame(restore);
  window.addEventListener('resize', () => { if (ws.isConnected) restore(); }, { signal });
  const cycle = () => {                                       // tap on the sheet grip: next stop up, wrapping to peek
    const H = box().height, cur = (sizeNow() || 0) / H;
    const next = SHEET_STOPS.find((f) => f > cur + 0.04) ?? SHEET_STOPS[0];
    set('--side-h', H * next); save(KEY.h, H * next);
  };
  el.addEventListener('pointerdown', (e) => {
    e.preventDefault(); el.setPointerCapture(e.pointerId);
    const b = box(), x0 = e.clientX, y0 = e.clientY; let moved = false;
    el.classList.add('drag'); ws.classList.add('resizing');
    const move = (ev) => {
      if (Math.abs(ev.clientX - x0) + Math.abs(ev.clientY - y0) > 4) moved = true;
      if (STACKED.matches) set('--side-h', clampH(b.bottom - ev.clientY));
      else set('--side-w', clampW(b.right - ev.clientX - 3));
    };
    const up = () => {               // drop BOTH end listeners: a leftover one would replay this gesture later
      el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up); el.removeEventListener('pointercancel', up);
      el.classList.remove('drag'); ws.classList.remove('resizing');
      if (STACKED.matches) { if (moved) save(KEY.h, sizeNow()); else cycle(); } else if (moved) save(KEY.w, sizeNow());
    };
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
  });
  el.addEventListener('dblclick', () => { if (STACKED.matches) return; set('--side-w', null); save(KEY.w, null); });
  el.addEventListener('keydown', (e) => {
    const k = e.key, stacked = STACKED.matches;
    const grow = stacked ? { ArrowUp: 1, ArrowDown: -1 }[k] : { ArrowLeft: 1, ArrowRight: -1 }[k];
    if (!grow && !(stacked && (k === 'Enter' || k === ' '))) return;
    e.preventDefault(); e.stopPropagation();
    if (!grow) return cycle();
    if (stacked) { const v = clampH(sizeNow() + grow * 40); set('--side-h', v); save(KEY.h, v); }
    else { const v = clampW(sizeNow() + grow * 24); set('--side-w', v); save(KEY.w, v); }
  });
  return el;
}
// Small screens: the tools of the view live behind one "⋯" button (a vertical menu) instead of a wall of buttons,
// so the page keeps the screen. The button is marked while a mode (region, selection) is on, so state stays visible.
function toolsMenu() {
  const active = S.drawing || S.selectMode || omitState();
  const d = h('details', { class: 'ws-more' + (active ? ' active' : '') },
    h('summary', { title: 'Εργαλεία: ζουμ, πλαίσια, περιοχή, επιλογή, εκτός εξόδου, επανεξέταση', 'aria-label': 'Εργαλεία' }, '⋯'),
    h('div', { class: 'ws-more-menu', role: 'menu' }, toolsFor(S.leftView).map(t => TOOLS[t]())));
  return d;
}
document.addEventListener('pointerdown', (e) => {
  document.querySelectorAll('details.ws-more[open]').forEach(d => { if (!d.contains(e.target)) d.open = false; });
}, true);
export function buildWorkspace() {
  const v = clear($('#view')), pg = S.page, b = S.book;
  const ws = h('div', { class: 'ws' }), left = h('div', { class: 'ws-left' }), right = h('div', { class: 'ws-right' });
  v.append(ws); ws.append(left, splitter(ws), right);
  const n = pg.page, N = pg.page_count, info = pg.info || {};
  const processed = !!info.state;
  left.append(h('div', { class: 'ws-tools' },
    h('div', { class: 'ws-row' },
      h('button', { class: 'icon', disabled: n <= 1, onclick: () => go(`#/b/${b.id}/p/${n - 1}`), title: 'Προηγούμενη σελίδα (←)' }, '‹'),
      h('span', { class: 'small nowrap pg-of', title: `Σελίδα ${n} από ${N}` }, h('span', { class: 'wide-only' }, 'Σελίδα '), `${n} / ${N}`),
      h('button', { class: 'icon', disabled: n >= N, onclick: () => go(`#/b/${b.id}/p/${n + 1}`), title: 'Επόμενη σελίδα (→)' }, '›'),
      (() => { const txt = processed ? (info.state === 'needs_review' ? `${info.required} για έλεγχο` : STATE_EL[info.state]) : STATE_EL.not_processed;
        const short = processed && info.state === 'needs_review' ? [String(info.required), h('span', { class: 'wide-only' }, ' για έλεγχο')] : [txt];
        return h('span', { class: 'chip page-state st-' + (processed ? info.state : 'not_processed'), title: txt, 'aria-label': txt }, ...short); })(),
      // the whole-book review loop, always reachable (also with no segment selected): errors and warnings, any page
      processed ? h('div', { class: 'seg-ctl review-loop' },
        h('button', { onclick: () => stepReview(-1), title: 'Προηγούμενο προς έλεγχο, σε όλο το βιβλίο (K)', 'aria-label': 'Προηγούμενο προς έλεγχο' }, '↑'),
        h('button', { class: 'primary', onclick: () => stepReview(1), title: `Επόμενο προς έλεγχο (${LOOP_EL[loopLevel()]}), σε όλο το βιβλίο· αλλάζει σελίδα μόνο του (J)` },
          h('span', { class: 'wide-only' }, 'Επόμενο για έλεγχο '), '↓', h('kbd', {}, 'J')),
        loopSelect()) : null,
      h('span', { class: 'spacer' }),
      processed ? h('div', { class: 'seg-ctl view-switch', role: 'tablist' }, VIEWS.map(([k, l, t], i) =>
        h('button', { class: S.leftView === k ? 'on' : '', role: 'tab', 'aria-selected': S.leftView === k ? 'true' : 'false', title: `${l} (${i + 1}) — ${t}`, 'aria-label': l,
          onclick: () => setView(k) }, h('span', { class: 'wide-only' }, l), h('span', { class: 'narrow-only' }, VIEWS[i][3]), h('kbd', {}, String(i + 1))))) : null,
      processed ? h('select', { class: 'view-select', 'aria-label': 'Προβολή', title: 'Προβολή: σάρωση, αποτέλεσμα ή Markdown', onchange: (e) => setView(e.target.value) },
        VIEWS.map(([k, l]) => h('option', { value: k, selected: S.leftView === k }, l))) : null,
      processed ? toolsMenu() : null),
    processed ? h('div', { class: 'ws-row ws-actions' }, toolsFor(S.leftView).map(t => TOOLS[t]())) : null,
    boundActive() ? boundBar() : null,
    typoActive() ? typoBar() : null));
  const wrap = h('div', { class: 'canvas-wrap', 'data-keep-scroll': 'canvas' }); left.append(wrap);
  if (processed && S.leftView !== 'scan') { outputView(wrap, b, n); renderInspector(right); return; }
  const dims = pg.dims || [600, 800];
  const baseW = Math.min(wrap.clientWidth || 900, 1000);
  const canvas = h('div', { class: 'canvas' + (S.drawing ? ' drawing' : '') + (S.showBoxes ? '' : ' hideboxes'), style: { width: `calc(${Math.round(S.zoom * 100)}% - 0px)`, maxWidth: Math.round(1000 * S.zoom) + 'px', aspectRatio: `${dims[0]} / ${dims[1]}` } });
  canvas.append(h('img', { src: `${B(b.id)}/pages/${n}/image?w=1400`, alt: `Σελίδα ${n}` }));
  wrap.append(canvas);
  const pct = (bb) => pctStyle(bb, dims);
  const selBlk = pg.blocks.find(b => b.id === S.sel);
  const groupMates = new Set((S.book.groups || []).filter(g => selBlk && (selBlk.groups || []).includes(g.id)).flatMap(g => g.members));
  const bound = boundMembers();                   // the profile exclusion being checked (inspector/boundloop.js)
  for (const blk of pg.blocks) {
    const edge = bound.get(blk.id);
    if (profileHidden(blk) && S.sel !== blk.id && !edge) continue;     // excluded by the output profile: no box
    if (edge) {                                    // hatched, never confused with an accepted segment's box
      canvas.append(h('div', { 'data-seg': blk.id, class: `ws-box bound bound-${edge}${S.sel === blk.id ? ' sel' : ''}`, style: pct(blk.bbox),
        title: `Εξαίρεση προφίλ — ${edge === 'start' ? 'ΑΡΧΗ' : edge === 'end' ? 'ΤΕΛΟΣ' : 'μέλος'}`, onclick: (e) => { e.stopPropagation(); selectBlock(blk.id); } },
        edge !== 'mid' ? h('span', { class: 'bound-tag' }, edge === 'start' ? 'ΑΡΧΗ' : 'ΤΕΛΟΣ') : null));
      continue;
    }
    const sel = S.sel === blk.id;
    const box = h('div', { 'data-seg': blk.id, onmouseenter: () => hoverSeg(blk.id, 'page'), onmouseleave: () => hoverSeg(null),
      class: `ws-box ${blk.review}${blk.excluded ? ' excluded' : ''}${blk.omitted ? ' omitted' : ''}${sel ? ' sel' : ''}${S.multi.has(blk.id) ? ' multi' : ''}${groupMates.has(blk.id) ? ' mate' : ''}${(S.hl || new Set()).has(blk.id) ? ' hl-member' : ''}`, style: pct(blk.bbox), title: `${kindLabel(blk)} · ${REVIEW_EL[blk.review]}`,
      onclick: (e) => { if (S.drawing) return; e.stopPropagation(); if (S.linking) return finishLink(blk); if (e.shiftKey || S.selectMode) return toggleMulti(blk); clearSelection(); selectBlock(blk.id); } }, h('span', { class: 'tag' }, kindLabel(blk)),
      S.multi.has(blk.id) ? h('span', { class: 'ord-badge', title: 'Θέση στην επιλογή' }, String([...S.multi.keys()].indexOf(blk.id) + 1)) : null);
    if (sel && !S.drawing) for (const c of ['nw', 'ne', 'sw', 'se']) box.append(h('span', { class: 'handle ' + c, onmousedown: (e) => startResize(e, canvas, blk, c, dims) }));
    canvas.append(box);
  }
  honourReveal(canvas);                           // a loop step's item (errors, warnings, typos, exclusions) is centred
  // caption ↔ figure connections (dashed lines), so groups are visible on the page itself
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'links'); svg.setAttribute('viewBox', `0 0 ${dims[0]} ${dims[1]}`); svg.setAttribute('preserveAspectRatio', 'none');
  const byId = Object.fromEntries(pg.blocks.map(x => [x.id, x]));
  for (const c of pg.blocks) {
    const f = byId[c.relations?.caption_for]; if (!f) continue;
    const cc = [(c.bbox[0] + c.bbox[2]) / 2, (c.bbox[1] + c.bbox[3]) / 2], fc = [(f.bbox[0] + f.bbox[2]) / 2, (f.bbox[1] + f.bbox[3]) / 2];
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    [['x1', cc[0]], ['y1', cc[1]], ['x2', fc[0]], ['y2', fc[1]]].forEach(([k, v]) => line.setAttribute(k, v));
    line.setAttribute('class', 'link' + (S.sel === c.id || S.sel === f.id ? ' sel' : '') + (c.relations.inferred ? ' inferred' : ''));
    svg.append(line);
  }
  if (S.showBoxes) canvas.append(svg);
  if (S.linking) canvas.classList.add('linking');
  (info.suggestions || []).forEach((s, i) => s.bbox && canvas.append(h('div', { class: 'ws-box sugg', style: pct(s.bbox) },
    h('button', { class: 'primary', onclick: (e) => { e.stopPropagation(); act(() => api(`${B(b.id)}/pages/${n}/suggestions/${s.key}/accept`, { body: {} }), 'Συγχώνευση πλαισίων· τρέχει νέο OCR για την περιοχή'); } }, 'Συγχώνευση & νέο OCR'))));
  if (S.draft) drawDraft(canvas, dims, pct);
  canvas.addEventListener('mousedown', (e) => { if (S.drawing && e.button === 0 && e.target.closest('.draft-menu') === null) startDraw(e, canvas, dims); });
  canvas.addEventListener('click', (e) => { if (!S.drawing && (e.target === canvas.querySelector('img') || e.target === canvas)) deselectBlock(); });
  if (!processed) {
    wrap.append(h('div', { class: 'card', style: { position: 'absolute', top: '40px', left: '50%', transform: 'translateX(-50%)', padding: '16px 20px', textAlign: 'center' } },
      h('div', { style: { marginBottom: '8px' } }, 'Αυτή η σελίδα δεν έχει επεξεργαστεί.'),
      h('button', { class: 'primary', onclick: () => act(() => api(`${B(b.id)}/process`, { body: { pages: String(n) } }), 'Η σελίδα μπήκε στην ουρά επεξεργασίας', false) }, 'Επεξεργασία αυτής της σελίδας')));
  }
  renderInspector(right);
  const nxt = S.sel && queueNeighbours(S.page.blocks.find(b => b.id === S.sel) || {}).next;
  if (nxt && nxt.crop) new Image().src = `${B(b.id)}/${nxt.crop}`;      // prefetch the next item's crop
  if (S.sel && S.scrolledTo !== S.sel) requestAnimationFrame(() => {   // only when the selection changed; scroll the canvas pane only
    if (S.scrolledTo === S.sel) return;            // decided when it RUNS: a loop step may have shown it meanwhile (reveal.js)
    S.scrolledTo = S.sel;
    const box = canvas.querySelector('.ws-box.sel'); if (!box) return;
    const wr = wrap.getBoundingClientRect(), br = box.getBoundingClientRect();
    if (br.top < wr.top || br.bottom > wr.bottom) wrap.scrollTop += br.top - wr.top - wr.height / 3;
  });
}
// What «Επόμενο για έλεγχο» and J/K walk through. Errors / warnings / both = the whole-book review loop
// (inspector/reviewloop.js); typos and the profile's exclusions are loops with their own bar (typoloop.js,
// boundloop.js). Every one of them centres its item on each step (workspace/reveal.js).
const LOOP_EL = { required: 'λάθη', all: 'λάθη και προειδοποιήσεις', recommended: 'προειδοποιήσεις' };
function loopSelect() {
  const cur = S.typo ? 'typos' : S.bound ? 'bound' : loopLevel();
  const sel = h('select', { class: 'loop-level small', 'aria-label': 'Τι διατρέχει ο έλεγχος', title: 'Τι διατρέχει το «Επόμενο για έλεγχο» (J/K)',
    onchange: () => {
      const v = sel.value;
      if (v === 'typos') return startTypoLoop();
      if (v === 'bound') return startBoundLoop();
      S.typo = null; S.bound = null;
      setLoopLevel(v); renderWorkspace();
    } },
    h('option', { value: 'required' }, 'Λάθη'), h('option', { value: 'all' }, 'Λάθη + προειδοποιήσεις'),
    h('option', { value: 'recommended' }, 'Προειδοποιήσεις'), h('option', { value: 'typos' }, 'Τυπογραφικά'),
    S.book.output_profile && S.book.output_profile !== 'reading' ? h('option', { value: 'bound' }, 'Εξαιρέσεις προφίλ') : null);
  sel.value = cur;
  return sel;
}
export function zoom(d) { S.zoom = Math.max(0.5, Math.min(3, S.zoom + d)); renderWorkspace(); }
export function syncHash() {
  const h2 = `#/b/${S.book.id}/p/${S.page.page}${S.sel ? '/' + S.sel : ''}`;
  history.replaceState(history.state, '', h2); S.route = parseRoute();
  if (!EMBED && NAV.entries[NAV.idx]) { NAV.entries[NAV.idx].hash = h2; navSave(); }      // same screen, refined state (never the parent's path from an overlay iframe)
}
export function selectBlock(id) { S.sel = id; S.inspTab = 'segment'; syncHash(); renderWorkspace(); }
export function deselectBlock() { S.sel = null; S.inspTab = 'page'; syncHash(); renderWorkspace(); }
export function toggleDraw() { S.drawing = !S.drawing; S.draft = null; renderWorkspace(); if (S.drawing) toast('Σύρε πάνω στη σελίδα για να ορίσεις την περιοχή'); }

export function canvasPoint(e, canvas, dims) {
  const r = canvas.getBoundingClientRect();
  return [Math.max(0, Math.min(dims[0], (e.clientX - r.left) / r.width * dims[0])), Math.max(0, Math.min(dims[1], (e.clientY - r.top) / r.height * dims[1]))];
}
export function startDraw(e, canvas, dims) {
  e.preventDefault();
  const a = canvasPoint(e, canvas, dims);
  const ghost = h('div', { class: 'ws-box draft' }); canvas.append(ghost);
  const move = (ev) => { const b2 = canvasPoint(ev, canvas, dims); const bb = [Math.min(a[0], b2[0]), Math.min(a[1], b2[1]), Math.max(a[0], b2[0]), Math.max(a[1], b2[1])];
    Object.assign(ghost.style, pctStyle(bb, dims)); ghost.dataset.bb = JSON.stringify(bb); };
  const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up);
    const bb = ghost.dataset.bb && JSON.parse(ghost.dataset.bb); ghost.remove();
    if (bb && bb[2] - bb[0] > 6 && bb[3] - bb[1] > 6) { S.draft = { bbox: bb, label: { engine: 'marker', name: 'Text' }, replaces: [] }; renderWorkspace(); } };
  window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
}
export function startResize(e, canvas, blk, corner, dims) {
  e.preventDefault(); e.stopPropagation();
  const bb = [...blk.bbox], box = e.target.parentElement;
  const move = (ev) => { const p = canvasPoint(ev, canvas, dims);
    if (corner.includes('w')) bb[0] = Math.min(p[0], bb[2] - 6); if (corner.includes('e')) bb[2] = Math.max(p[0], bb[0] + 6);
    if (corner.includes('n')) bb[1] = Math.min(p[1], bb[3] - 6); if (corner.includes('s')) bb[3] = Math.max(p[1], bb[1] + 6);
    Object.assign(box.style, pctStyle(bb, dims)); };
  const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up);
    const [eng, name] = (blk.engine_label || 'marker:Text').split(':');
    S.draft = { bbox: bb.map(v => Math.round(v * 10) / 10), label: { engine: eng, name: name || 'Text' },
                replaces: blk.source === 'region' ? [] : [blk.id], replacesRegion: blk.source === 'region' ? blk.region_id : null };
    renderWorkspace(); };
  window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
}
export function drawDraft(canvas, dims, pct) {
  const d = S.draft;
  canvas.append(h('div', { class: 'ws-box draft', style: pct(d.bbox) }));
  const lab = d.label || { engine: 'marker', name: 'Text' };
  const info = (S.meta.labels[lab.engine] || []).find(x => x.name === lab.name) || { el: lab.name };
  const menu = h('div', { class: 'draft-menu', style: { width: '300px', left: Math.min(100 * d.bbox[0] / dims[0], 60) + '%', top: `calc(${100 * d.bbox[3] / dims[1]}% + 8px)` }, onmousedown: e => e.stopPropagation() },
    h('div', { class: 'small', style: { fontWeight: 600 } }, d.replaces.length || d.replacesRegion ? 'Νέο πλαίσιο για το τμήμα' : 'Νέα περιοχή'),
    h('div', { class: 'row small', style: { margin: '6px 0' } }, 'Ετικέτα:', h('b', {}, info.el), h('code', { class: 'mut' }, `${lab.engine}:${lab.name}`),
      h('button', { class: 'small', onclick: () => modal('Ετικέτα περιοχής', labelPicker([`${lab.engine}:${lab.name}`], (l) => { d.label = l; closeTopDialog(); renderWorkspace(); }), [{ label: 'Άκυρο' }]) }, 'Αλλαγή ▾')),
    h('div', { class: 'small mut', style: { margin: '4px 0 8px' } }, ['Picture', 'Figure', 'Diagram', 'ComplexRegion', 'picture', 'chart'].includes(lab.name)
      ? 'Θα κρατηθεί ως εικόνα.' : 'Νέο OCR μόνο σε αυτή την περιοχή, ως ενιαίο τμήμα αυτού του τύπου. Τα πλαίσια που καλύπτει αντικαθίστανται.'),
    h('div', { class: 'row' }, h('button', { onclick: () => { S.draft = null; S.drawing = false; renderWorkspace(); } }, 'Άκυρο'),
      h('button', { class: 'primary', onclick: async () => {
        await act(() => api(`${B(S.book.id)}/regions`, { body: { page: S.page.page, bbox: d.bbox, label: lab, replaces: d.replaces, replaces_region: d.replacesRegion } }), 'Η περιοχή προστέθηκε· τρέχει νέο OCR (~30 δευτ.)');
        S.draft = null; S.drawing = false; await refresh(); } }, 'Εφαρμογή')));
  canvas.append(menu);
}
export function diligentDialog() {
  modal('Προσεκτική επανεξέταση σελίδας', h('div', {}, h('p', {}, 'Η σελίδα θα ξαναπεράσει από ανάλυση διάταξης και OCR με υψηλότερη ανάλυση και πλήρες OCR σε όλη την επιφάνεια.'),
    h('p', { class: 'small mut' }, 'Χρήσιμο όταν πολλά πλαίσια είναι λάθος. Για ένα μόνο λάθος πλαίσιο, πιο γρήγορο είναι να το σχεδιάσεις ξανά (＋ Περιοχή ή σύρε τις γωνίες του). Οι αποφάσεις σε τμήματα που θα αλλάξουν θα ζητηθούν ξανά.')),
    [{ label: 'Άκυρο' }, { label: 'Επανεξέταση', cls: 'primary', run: () => act(() => api(`${B(S.book.id)}/pages/${S.page.page}/profile`, { body: { profile: 'diligent' } }), 'Η σελίδα μπήκε στην ουρά για προσεκτική επανεξέταση') }]);
}
