import { assignSelectionButton } from '../book/entities.js';
import { B, api } from '../core/api.js';
import { $, clear, h } from '../core/dom.js';
import { S, kindLabel } from '../core/state.js';
import { act, toast } from '../ui/feedback.js';
import { hoverSeg } from '../workspace/hover.js';
import { clearSelection, renderWorkspace, reorderSelection, setSelection } from '../workspace/workspace.js';

// ---------------------------------------------------------------- multi-selection: GROUP (annotate) or MERGE (replace)
// Selection survives page changes (groups may span pages; merges need one page).
export function toggleSelectMode() {
  S.selectMode = !S.selectMode;
  if (S.selectMode) { S.multi.clear(); S.sel = null; toast('Λειτουργία επιλογής: κάθε κλικ προσθέτει/αφαιρεί τμήμα (S για έξοδο)'); }   // start empty
  renderWorkspace();
}
export function toggleMulti(blk) {
  // ⌘/Ctrl-click outside selection mode extends the open segment (as in a file manager); in SELECTION MODE (S)
  // only what is clicked after entering it counts — the segment that was open is not selected by itself
  const cur = S.page.blocks.find(b => b.id === S.sel);
  if (!S.selectMode && cur && !S.multi.size && cur.id !== blk.id) S.multi.set(cur.id, { page: cur.page, text: segText(cur), label: cur.engine_label });
  S.multi.has(blk.id) ? S.multi.delete(blk.id) : S.multi.set(blk.id, { page: blk.page, text: segText(blk), label: blk.engine_label });
  if (!S.multiOpen) S.inspTab = 'page';             // keep selecting in the page list
  renderWorkspace();
}
export const segText = b => (b.chosen?.text ? b.chosen.text.replace(/\*+|<\/?(?:sub|sup|b|i)>/g, '') : b.chosen?.latex || `[${kindLabel(b)} — εικόνα]`).slice(0, 90);
// dropdown menu anchored to a button: type to filter, ↑/↓ to move, Enter to choose, Esc to close
export function dropdown(anchor, sections, onPick, header) {
  document.querySelector('.dd')?.remove();
  const r = anchor.getBoundingClientRect();
  const q = h('input', { type: 'search', placeholder: 'Αναζήτηση…' });
  const list = h('div', { class: 'dd-list' });
  const dd = h('div', { class: 'dd', style: { left: Math.max(8, Math.min(r.left, innerWidth - 340)) + 'px' } }, header || null, q, list);
  // open where there is room: below the anchor, or above it when the space below is short; the list scrolls inside
  const below = innerHeight - r.bottom - 12, above = r.top - 12;
  if (below >= 280 || below >= above) { dd.style.top = (r.bottom + 4) + 'px'; dd.style.maxHeight = Math.max(160, below) + 'px'; }
  else { dd.style.bottom = (innerHeight - r.top + 4) + 'px'; dd.style.maxHeight = Math.max(160, above) + 'px'; }
  let items = [], idx = 0;
  const paint = () => { clear(list); items = [];
    for (const sec of sections) {
      const its = sec.items.filter(it => !q.value || (it.label + ' ' + (it.hint || '')).toLowerCase().includes(q.value.toLowerCase()));
      if (!its.length) continue;
      list.append(h('div', { class: 'dd-sec', style: { color: sec.color || 'var(--mut)' } }, sec.title));
      for (const it of its) { const i = items.length; items.push(it);
        list.append(h('div', { class: 'dd-item' + (i === idx ? ' on' : ''), style: { borderLeftColor: sec.color || 'transparent' }, onmousedown: (e) => { e.preventDefault(); close(); onPick(it); } },
          h('span', {}, it.label), it.hint ? h('code', { class: 'mut small' }, it.hint) : null)); } }
    list.querySelector('.dd-item.on')?.scrollIntoView({ block: 'nearest' }); };
  const close = () => { dd.remove(); document.removeEventListener('mousedown', outside, true); };
  const outside = (e) => { if (!dd.contains(e.target)) close(); };
  q.addEventListener('input', () => { idx = 0; paint(); });
  q.addEventListener('keydown', (e) => { if (e.key === 'ArrowDown') { idx = Math.min(items.length - 1, idx + 1); paint(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { idx = Math.max(0, idx - 1); paint(); e.preventDefault(); }
    else if (e.key === 'Enter' && items[idx]) { close(); onPick(items[idx]); } else if (e.key === 'Escape') { close(); e.stopPropagation(); } });
  document.body.append(dd); paint(); q.focus();
  setTimeout(() => document.addEventListener('mousedown', outside, true), 0);
}
export function labelSections() {
  return [{ title: 'Marker', color: '#1d4ed8', items: S.meta.labels.marker.map(l => ({ label: l.el, hint: l.name, value: { engine: 'marker', name: l.name } })) },
          { title: 'Docling', color: '#7e22ce', items: S.meta.labels.docling.map(l => ({ label: l.el, hint: l.name, value: { engine: 'docling', name: l.name } })) }];
}
export function openMergeMenu(anchor) {
  const ids = [...S.multi.keys()], pages = new Set([...S.multi.values()].map(v => v.page));
  if (ids.length < 2) return toast('Επίλεξε τουλάχιστον δύο τμήματα', { error: true });
  if (pages.size > 1 || pages.has(null)) return toast('Η συγχώνευση γίνεται μέσα σε μία σελίδα· για τμήματα σε πολλές σελίδες χρησιμοποίησε ομαδοποίηση (G)', { error: true });
  let mode = 'join';
  const header = h('div', { class: 'dd-head' }, h('b', {}, `Συγχώνευση ${ids.length} τμημάτων ως…`),
    h('div', { class: 'row small' }, ['join', 'reocr'].map(m => h('label', { class: 'row' }, (() => { const r = h('input', { type: 'radio', name: 'mm' }); r.checked = m === mode; r.onchange = () => { mode = m; }; return r; })(),
      m === 'join' ? 'ένωση κειμένων' : 'νέο OCR περιοχής'))));
  dropdown(anchor || document.body, labelSections(), async (it) => {
    await act(() => api(`${B(S.book.id)}/merge`, { body: { blocks: ids, label: it.value, mode } }), mode === 'join' ? `Συγχωνεύτηκαν σε «${it.label}»` : 'Νέα περιοχή· τρέχει OCR');
    clearSelection();
  }, header);
}
export function openGroupMenu(anchor) {
  const ids = [...S.multi.keys()];
  if (ids.length < 2) return toast('Επίλεξε τουλάχιστον δύο τμήματα', { error: true });
  const name = h('input', { type: 'text', placeholder: 'όνομα (προαιρετικό), π.χ. Παράδειγμα 4.3' });
  const header = h('div', { class: 'dd-head' }, h('b', {}, `Ομαδοποίηση ${ids.length} τμημάτων ως…`), h('div', { class: 'small mut' }, 'Τα τμήματα μένουν ως έχουν· προστίθεται σημείωση ότι ανήκουν μαζί.'), name);
  const types = Object.entries(S.meta.group_types).map(([k, v]) => ({ label: v, hint: k, value: k }));
  dropdown(anchor || document.body, [{ title: 'Είδος ομάδας', items: types }], async (it) => {
    await act(() => api(`${B(S.book.id)}/groups`, { body: { blocks: ids, type: it.value, name: name.value } }), `Ομάδα «${it.label}» δημιουργήθηκε`);
    clearSelection();
  }, header);
}
// The selection is ORDERED (S.multi keeps insertion order): the order shown here is the order of the members
// of a group and the order in which a merge joins their texts. Reorder by dragging, ↑/↓, or Alt+↑/↓.
export function moveMulti(id, to) {
  const ids = [...S.multi.keys()], from = ids.indexOf(id);
  if (from < 0 || to < 0 || to >= ids.length || to === from) return;
  ids.splice(to, 0, ids.splice(from, 1)[0]);
  reorderSelection(ids);
  S.multiFocus = id; renderWorkspace();
}
export function multiReadingOrder() {
  const pos = (id) => { const b = S.page.blocks.find(x => x.id === id); return b ? [b.page, b.reading_order] : [S.multi.get(id).page, 1e9]; };
  const ids = [...S.multi.keys()].sort((a, b) => { const [pa, ra] = pos(a), [pb, rb] = pos(b); return pa - pb || ra - rb; });
  reorderSelection(ids); renderWorkspace();
}
export function multiPanel(body) {
  const entries = [...S.multi.entries()], pages = new Set(entries.map(([, v]) => v.page));
  const multiPage = pages.size > 1 || pages.has(null);     // an unknown page counts as another page: no merge
  let dragId = null;
  const list = h('ol', { class: 'seglist ordered-sel', 'aria-label': 'Σειρά επιλεγμένων τμημάτων' }, entries.map(([id, v], i) => {
    const row = h('li', { class: 'list-item sel-row' + (S.multiFocus === id ? ' focus' : ''), draggable: 'true', tabindex: '0', 'data-seg': id,
      onmouseenter: () => hoverSeg(id, 'list'), onmouseleave: () => hoverSeg(null),
      ondragstart: (e) => { dragId = id; e.dataTransfer.effectAllowed = 'move'; row.classList.add('dragging'); },
      ondragend: () => row.classList.remove('dragging'),
      ondragover: (e) => { e.preventDefault(); row.classList.add('drop'); }, ondragleave: () => row.classList.remove('drop'),
      ondrop: (e) => { e.preventDefault(); row.classList.remove('drop'); if (dragId) moveMulti(dragId, i); },
      onkeydown: (e) => { if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) { e.preventDefault(); e.stopPropagation(); moveMulti(id, i + (e.key === 'ArrowUp' ? -1 : 1)); } } },
      h('span', { class: 'drag', title: 'Σύρε για αλλαγή σειράς', 'aria-hidden': 'true' }, '⠿'), h('span', { class: 'ord' }, String(i + 1)),
      h('span', { class: 'small mut' }, `σ.${v.page ?? '?'}`), h('span', { class: 'txt grow' }, v.text),
      h('button', { class: 'ghost small', disabled: i === 0, title: 'Πιο πάνω (Alt+↑)', onclick: () => moveMulti(id, i - 1) }, '↑'),
      h('button', { class: 'ghost small', disabled: i === entries.length - 1, title: 'Πιο κάτω (Alt+↓)', onclick: () => moveMulti(id, i + 1) }, '↓'),
      h('button', { class: 'ghost small', title: 'Αφαίρεση από την επιλογή', onclick: () => { S.multi.delete(id); renderWorkspace(); } }, '×'));
    return row;
  }));
  body.append(h('button', { class: 'small ghost', style: { marginBottom: '8px' }, onclick: () => { S.multiOpen = false; renderWorkspace(); },
    title: 'Επιστροφή στη λίστα της σελίδας χωρίς απόφαση· η επιλογή μένει' }, '← Πίσω στη λίστα', h('kbd', {}, 'Esc')));
  body.append(h('div', { class: 'version v-pending' }, h('b', {}, `Επιλογή: ${entries.length} τμήματα`), pages.size > 1 ? ` σε ${pages.size} σελίδες` : '',
    h('div', { class: 'small mut' }, 'Shift+κλικ ή λειτουργία επιλογής (S) για προσθήκη/αφαίρεση · Esc καθαρισμός')),
    h('div', { class: 'row small', style: { margin: '8px 0 4px' } }, h('span', { class: 'grow' }, h('b', {}, 'Σειρά: '), 'έτσι θα ενωθούν τα κείμενα (συγχώνευση) ή θα καταγραφούν τα μέλη (ομάδα). Σύρε ⠿, ↑/↓ ή Alt+↑/↓.'),
      h('button', { class: 'small ghost', onclick: multiReadingOrder, title: 'Ταξινόμηση με τη σειρά που εμφανίζονται στη σελίδα' }, 'Σειρά ανάγνωσης')),
    list);
  if (S.multiFocus) requestAnimationFrame(() => list.querySelector('.sel-row.focus')?.focus());
  const gBtn = h('button', { class: 'primary', onclick: (e) => openGroupMenu(e.currentTarget) }, 'Ομαδοποίηση…', h('kbd', {}, 'G'));
  const mBtn = h('button', { disabled: multiPage, title: multiPage ? 'Η συγχώνευση γίνεται μέσα σε μία σελίδα' : '', onclick: (e) => openMergeMenu(e.currentTarget) }, 'Συγχώνευση σε ένα…', h('kbd', {}, 'M'));
  const cBtn = assignSelectionButton(entries.map(([id]) => id));
  body.append(h('div', { class: 'actions' }, gBtn, mBtn, cBtn, h('button', { class: 'ghost', onclick: () => { clearSelection(); renderWorkspace(); } }, 'Καθαρισμός', h('kbd', {}, 'Esc'))),
    h('div', { class: 'small mut', style: { marginTop: '8px' } },
      h('div', {}, h('b', {}, 'Ομαδοποίηση: '), 'κάθε τμήμα κρατά id, είδος και κείμενο· προστίθεται μια ομάδα (π.χ. «Παράδειγμα») ως μεταδεδομένα. Μπορεί να εκτείνεται σε σελίδες.'),
      h('div', {}, h('b', {}, 'Συγχώνευση: '), 'τα τμήματα φεύγουν από την έξοδο και δημιουργείται ένα νέο που τα περιέχει (μένουν στο ιστορικό· «Διάσπαση» τα επαναφέρει).')));
  S.segActions = { discard: () => { clearSelection(); renderWorkspace(); }, group: () => openGroupMenu(gBtn), category: () => cBtn.click(), merge: multiPage ? null : () => openMergeMenu(mBtn) };
}
// page of a group member not on the current page: the group's only page, else the page encoded in a block id (p<page>_…)
const memberPage = (g) => (id) => { if ((g.pages || []).length === 1) return g.pages[0]; const m = /^p(\d+)_/.exec(id); return m ? +m[1] : null; };
export function groupsPanel(blk) {
  const gs = (S.book.groups || []).filter(g => (blk.groups || []).includes(g.id));
  if (!gs.length) return h('span');
  return h('details', { open: true }, h('summary', {}, `Ομάδες (${gs.length})`), h('div', {}, gs.map(g => h('div', { class: 'cand' },
    h('div', { class: 'row' }, h('b', { class: 'grow' }, S.meta.group_types[g.type] || g.type, g.name ? ` · ${g.name}` : ''),
      h('span', { class: 'chip' }, g.source === 'auto' ? `αυτόματη · ${g.rule || ''}` : 'δική σου'), h('span', { class: 'small mut' }, `${g.members.length} μέλη · σελ. ${g.pages.join(', ')}`)),
    h('div', { class: 'actions' },
      h('button', { class: 'small', onclick: () => { setSelection(g.members, { pageOf: memberPage(g) }); renderWorkspace(); } }, 'Επιλογή μελών'),
      h('button', { class: 'small', title: 'Όλη η ομάδα εκτός Markdown/PDF (μένει στο βιβλίο)', onclick: () => act(() => api(`${B(S.book.id)}/output`, { body: { group: g.id, include: false } }), 'Η ομάδα βγήκε εκτός εξόδου') }, 'Εκτός εξόδου όλη η ομάδα'),
      h('button', { class: 'small ghost', onclick: () => act(() => api(`${B(S.book.id)}/output`, { body: { group: g.id, include: true } }), 'Η ομάδα ξανά στην έξοδο') }, 'Ξανά στην έξοδο'),
      g.source === 'auto' ? h('button', { class: 'small dismiss', onclick: () => act(() => api(`${B(S.book.id)}/suggestions/${g.key}/dismiss`, { body: {} }), 'Η αυτόματη ομάδα αφαιρέθηκε') }, 'Δεν είναι ομάδα')
        : h('button', { class: 'small ghost', onclick: () => act(() => api(`${B(S.book.id)}/groups/${g.id}/members`, { body: { blocks: g.members.filter(m => m !== blk.id) } }), 'Αφαιρέθηκε από την ομάδα') }, 'Αφαίρεση από την ομάδα'),
      g.source === 'auto' ? null : h('button', { class: 'small ghost danger', onclick: () => act(() => api(`${B(S.book.id)}/groups/${g.id}/remove`, { body: {} }), 'Η ομάδα διαλύθηκε') }, 'Διάλυση ομάδας'))))));
}
