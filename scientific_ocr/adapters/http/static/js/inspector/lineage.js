import { B, api } from '../core/api.js';
import { $, h } from '../core/dom.js';
import { S, go, kindLabel } from '../core/state.js';
import { SOURCE_EL, STAGE_EL } from '../core/strings.js';
import { act, modal, toast } from '../ui/feedback.js';

// ---------------------------------------------------------------- lineage (which engine/person produced what)
export const ENGINE_COLOR = { marker: '#1d4ed8', docling: '#7e22ce', unimernet: '#0f8a7e', molscribe: '#c2410c', rxnscribe: '#c2410c', human: '#0f7b45' };
export const sourceLabel = n => SOURCE_EL[n] || (n || '').replace(/^human/, 'εσύ').replace('(region re-OCR)', '(νέο OCR περιοχής)');
export const engChip = (name) => { const k = (name || '').split(/[ :(]/)[0]; return h('span', { class: 'chip', style: { background: (ENGINE_COLOR[k] || '#6b6b70') + '1a', color: ENGINE_COLOR[k] || '#444' } }, sourceLabel(name) || '—'); };
export function lineageCard(st) {          // one lineage step; the same card in lists and in the DAG
  return h('div', { class: 'cand' + (st.final ? ' final' : '') }, h('div', { class: 'row' },
    h('b', { style: { width: '92px' } }, st.final ? 'Τελικό περιεχόμενο' : (STAGE_EL[st.stage] || st.stage)), engChip(st.by),
    st.version ? h('span', { class: 'small mut' }, 'v' + st.version) : null, st.status && st.status !== 'OK' ? h('span', { class: 'badge-bad' }, st.status) : null,
    h('span', { class: 'spacer' }),
    st.raw_file ? h('button', { class: 'ghost small', onclick: () => showRaw(st) }, 'raw') : null),
    st.detail || st.ref || st.label || st.type ? h('div', { class: 'small mut' }, [st.detail, st.label, st.ref, st.type && st.final ? `τύπος: ${st.type}` : null].filter(Boolean).join(' · ')) : null,
    st.text ? h('div', { class: 'small' }, st.text) : null);
}
// Lineage DAG (built server-side: core.lineage.dag). Layout: layer = longest path from a root, so parallel
// readings sit side by side and every arrow points downwards; arrows are drawn after layout from the cards' boxes.
export function lineageDialog(blk) {
  const g = blk.lineage_dag || { nodes: [], edges: [] };
  const parents = {}; g.nodes.forEach(n => parents[n.id] = []); g.edges.forEach(([a, z]) => parents[z].push(a));
  const layer = {}; const depth = id => layer[id] ?? (layer[id] = parents[id].length ? 1 + Math.max(...parents[id].map(depth)) : 0);
  g.nodes.forEach(n => depth(n.id));
  const rows = []; g.nodes.forEach(n => (rows[layer[n.id]] = rows[layer[n.id]] || []).push(n));
  const cards = {};
  const graph = h('div', { class: 'dag' }, rows.map(r => h('div', { class: 'dag-layer' }, r.map(n => (cards[n.id] = h('div', { class: 'dag-node' }, lineageCard(n)))))));
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('class', 'dag-edges');
  svg.innerHTML = '<defs><marker id="dag-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z"/></marker></defs>';
  const wrap = h('div', { class: 'dag-wrap' }, svg, graph);
  const draw = () => {
    const o = wrap.getBoundingClientRect();
    svg.setAttribute('width', wrap.scrollWidth); svg.setAttribute('height', wrap.scrollHeight);
    svg.querySelectorAll('path.e').forEach(p => p.remove());
    for (const [a, z] of g.edges) {
      const A = cards[a].getBoundingClientRect(), Z = cards[z].getBoundingClientRect();
      const x1 = A.left + A.width / 2 - o.left + wrap.scrollLeft, y1 = A.bottom - o.top + wrap.scrollTop;
      const x2 = Z.left + Z.width / 2 - o.left + wrap.scrollLeft, y2 = Z.top - o.top + wrap.scrollTop;
      const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      const my = (y1 + y2) / 2;
      p.setAttribute('d', `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2 - 2}`); p.setAttribute('class', 'e'); p.setAttribute('marker-end', 'url(#dag-arrow)');
      svg.append(p);
    }
  };
  const d = modal(`Προέλευση τμήματος · ${kindLabel(blk)} · σελ. ${blk.page}`, h('div', {},
    h('p', { class: 'small mut' }, 'Πώς προέκυψε το περιεχόμενο: από τον εντοπισμό, στις παράλληλες αναγνώσεις των μηχανών, στη σύγκριση, στους κανόνες και στις αποφάσεις σου, μέχρι το τελικό περιεχόμενο (κάτω). Τα βέλη δείχνουν από τι εξαρτάται κάθε βήμα.'),
    wrap), [{ label: 'Κλείσιμο' }]);
  d.classList.add('wide');
  requestAnimationFrame(() => requestAnimationFrame(draw));
  const ro = new ResizeObserver(draw); ro.observe(graph);
  d.addEventListener('close', () => ro.disconnect(), { once: true });
}
export async function showRaw(st) {
  const r = await api(`${B(S.book.id)}/raw?file=${encodeURIComponent(st.raw_file)}${st.raw_pointer ? '&pointer=' + encodeURIComponent(st.raw_pointer) : ''}`);
  const d = modal(`Raw έξοδος ${st.by}`, h('div', {}, h('div', { class: 'small mut' }, `${r.file}${r.pointer ? '  ·  ' + r.pointer : ''}`),
    h('pre', { class: 'preview-pre' }, JSON.stringify(r.fragment, null, 2).slice(0, 20000))), [{ label: 'Κλείσιμο' }]);
  d.style.width = 'min(760px, 94vw)';
}
export function reportWrong(blk, preset) {
  const note = h('textarea', { placeholder: 'Τι είναι λάθος; π.χ. «λείπει η δεύτερη γραμμή», «το βέλος είναι ισορροπίας ⇌», «Γ αντί για G»', style: { minHeight: '90px', fontFamily: 'inherit' } });
  if (preset) note.value = preset;
  modal('Δήλωση λάθους', h('div', {}, h('p', { class: 'small mut' }, 'Το τμήμα θα ξαναπεράσει OCR ως ενιαία περιοχή (χωρίς νέα ανάλυση διάταξης). Η σημείωσή σου μένει μαζί του και εμφανίζεται στον έλεγχο.'), note),
    [{ label: 'Άκυρο' }, { label: 'Νέο OCR', cls: 'primary', run: async () => {
      const r = await act(() => api(`${B(S.book.id)}/blocks/${blk.id}/wrong`, { body: { note: note.value } }), 'Καταγράφηκε· τρέχει νέο OCR');
      S.sel = r.new_block; } }]);
  setTimeout(() => note.focus(), 50);
}
export async function nextPageWithWork() {
  const q = await api(`${B(S.book.id)}/queue?level=required`);
  const nx = q.find(x => x.page > S.page.page) || q[0];
  if (nx) go(`#/b/${S.book.id}/p/${nx.page}/${nx.id}`); else toast('Ο έλεγχος ολοκληρώθηκε 🎉');
}
// Reading-order changes by the reviewer (drag in the page list or Alt+↑/↓): an explicit, undoable decision
// (move_after_block with after|before). Automatic stages never reorder content; this is the human override.
export function moveInReadingOrder(id, where) {
  return act(() => api(`${B(S.book.id)}/decide`, { body: { block_id: id, action: 'move_after_block', ...where } }), 'Η σειρά ανάγνωσης άλλαξε');
}
// A caption belongs to its figure and is rendered inside it wherever the figure is: dragging it only reorders the
// parts of that figure's caption (title/text); anywhere else it would have no effect, so it is explained instead.
export function dropInReadingOrder(id, target, before) {
  const d = S.page.blocks.find(x => x.id === id), fig = d && d.kind === 'caption' && d.relations?.caption_for;
  if (fig && !(target.id === fig || target.relations?.caption_for === fig)) {
    toast('Η λεζάντα ακολουθεί το σχήμα της. Για άλλη θέση μετακίνησε το σχήμα· για άλλο σχήμα χρησιμοποίησε «Λεζάντα» στο τμήμα.', { error: true });
    return;
  }
  return moveInReadingOrder(id, before ? { before: target.id } : { after: target.id });
}
export function moveBySteps(id, delta) {
  const rows = S.page.blocks.filter(x => !x.excluded), i = rows.findIndex(x => x.id === id), t = rows[i + delta];
  if (i < 0 || !t) return;
  dropInReadingOrder(id, t, delta < 0);
}
