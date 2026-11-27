import { B, api } from '../core/api.js';
import { $, h } from '../core/dom.js';
import { S, kindLabel, profileHidden } from '../core/state.js';
import { CAT_EL, KIND_EL, REVIEW_EL, infoTip } from '../core/strings.js';
import { entName, entityChip } from '../book/entities.js';
import { dropInReadingOrder } from '../inspector/lineage.js';
import { segText, toggleMulti } from '../inspector/multi.js';
import { pageGroupsPanel, suggestionsPanel } from '../inspector/suggestions.js';
import { continuationMark, continuesInMark, excursusBanner, excursusChip } from '../inspector/excursus.js';
import { glossaryToggle } from '../inspector/glossary.js';
import { act } from '../ui/feedback.js';
import { CAT, legend, rowState } from '../ui/math.js';
import { hoverSeg } from '../workspace/hover.js';
import { clearSelection, renderWorkspace, selectBlock } from '../workspace/workspace.js';

// ---------------------------------------------------------------- page list as a structure tree
// Depth comes from the book-wide hierarchy (section_path + the TOC levels), i.e. exactly what the export uses, so
// the list is a quick visual check: a heading at the wrong level or a paragraph under the wrong section shows.
export const levelTag = (sec) => sec.source === 'labelled_element' ? 'Πλαίσιο' : sec.source === 'box_heading' ? 'Υπότ.' : `H${sec.level}`;
export function pageTree(blocks) {
  const toc = Object.fromEntries((S.book.sections || []).map(x => [x.id, x]));
  const levelOf = (b) => { const p = (b.section_path || []).filter(id => toc[id]); return p.length ? toc[p[p.length - 1]].level : 0; };
  const raw = blocks.map(b => {
    const own = toc[b.id];
    let depth = own ? own.level - 1 : levelOf(b), mark = null;
    if (own) mark = { text: levelTag(own), cls: own.source === 'labelled_element' || own.source === 'box_heading' ? 'lv-box' : '', title: `επίπεδο ${own.level}` };
    else if (b.kind === 'caption' && b.relations?.caption_for) { depth += 1; mark = { text: 'λεζάντα', cls: 'lv-cap', title: 'ανήκει στο σχήμα από πάνω' }; }
    else if (b.relations?.continues_from) mark = continuationMark(b);
    else if (b.flow === 'insert') mark = { text: 'παρεμβολή', cls: 'lv-ins', title: 'σχήμα/εξίσωση/πίνακας μέσα στη ροή του κειμένου' };
    return { b, depth, mark };
  });
  const min = Math.min(...raw.filter(r => !toc[r.b.id]).map(r => r.depth), ...raw.filter(r => toc[r.b.id]).map(r => r.depth), 99);
  const rows = raw.map(r => ({ ...r, depth: Math.max(0, Math.min(8, r.depth - (min === 99 ? 0 : min))) }));
  const first = blocks.find(b => b.kind !== 'heading');
  const context = first && (!blocks.length || blocks[0].kind !== 'heading') ? (first.section_path || []).filter(id => toc[id] && !blocks.some(b => b.id === id)).map(id => toc[id]) : [];
  return { rows, context };
}
// group membership in the list: a bracket on every member, the group's name on its first segment
export function groupMark(b) {
  const gs = (S.book.groups || []).filter(g => (b.groups || []).includes(g.id));
  if (!gs.length) return null;
  const g = gs.reduce((a, x) => (x.members.length > a.members.length ? x : a));
  const first = g.members[0] === b.id;
  return h('span', { class: 'chip grp' + (g.source === 'auto' ? ' grp-auto' : ''), title: `${g.source === 'auto' ? 'Αυτόματη ομάδα' : 'Δική σου ομάδα'}: ${g.name || g.type} · ${g.members.length} τμήματα — μετακίνηση/σειρά/έξοδος εφαρμόζονται σε όλη την ομάδα` },
    first ? `▣ ${(g.name || '').slice(0, 24) || 'ομάδα'} (${g.members.length})` : '│');
}
export function pagePanel(body) {
  const pg = S.page, info = pg.info || {};
  if (!info.state) { body.append(h('p', { class: 'mut' }, 'Η σελίδα δεν έχει επεξεργαστεί ακόμα.')); return; }
  const banner = excursusBanner();
  if (banner) body.append(banner);
  const sugg = suggestionsPanel(pg);
  body.append(h('div', { class: 'row small mut page-sum' }, h('span', { class: 'grow' }, `${pg.blocks.length} τμήματα · ${info.required || 0} απαιτούν έλεγχο · ${info.recommended || 0} συνιστάται`),
    S.meta.note_model ? h('button', { class: 'small ghost', title: 'Το τοπικό μοντέλο εξετάζει τη σελίδα και προτείνει συγχωνεύσεις/ομάδες (δεν εφαρμόζεται τίποτα χωρίς εσένα)', onclick: () => pg.askModel() }, 'Πρότεινε δομή') : null));
  body.append(sugg);
  const gl = pg.blocks.filter(x => x.glossary);
  if (gl.length) body.append(h('div', { class: 'row small gloss-note' }, h('span', { class: 'grow' }, h('b', {}, 'Σελίδα γλωσσαρίου'),
    ` · ${gl.reduce((n, x) => n + (x.glossary.terms || []).length, 0)} λήμματα· κάθε όρος με τον ορισμό του γίνεται ξεχωριστό λήμμα (στο RAG: ένα κομμάτι ανά λήμμα).`),
    glossaryToggle(pg)));
  body.append(pageGroupsPanel(pg));
  body.append(h('details', {}, h('summary', {}, 'Υπόμνημα χρωμάτων'), h('div', {}, legend())));
  body.append(h('h3', {}, 'Σειρά ανάγνωσης και δομή ', infoTip('pagetree')));
  const tree = pageTree(pg.blocks.filter(x => !x.excluded && !profileHidden(x)));
  if (tree.context.length) body.append(h('div', { class: 'tree-context small' }, h('span', { class: 'mut' }, 'Η σελίδα ξεκινά μέσα σε: '),
    tree.context.map((s2, i) => [i ? h('span', { class: 'mut' }, ' › ') : null, h('span', { class: 'chip lvl' }, levelTag(s2)), ' ', s2.title.slice(0, 40)])));
  const list = h('div', { class: 'seglist tree', 'aria-multiselectable': 'true' }); body.append(list);
  let dragId = null;
  const order = tree.rows.map(r => r.b);
  // list selection like a file manager: click = one, ⌘/Ctrl+click = add/remove, Shift+click = the contiguous range from
  // the last clicked row (⌘/Ctrl+Shift adds the range to the selection)
  const rangeTo = (b, additive) => {
    const ids = order.map(x => x.id), a = ids.indexOf(S.listAnchor ?? S.sel ?? b.id), z = ids.indexOf(b.id);
    const [i, j] = a < 0 ? [z, z] : [Math.min(a, z), Math.max(a, z)];
    if (!additive) S.multi.clear();
    for (const x of order.slice(i, j + 1)) S.multi.set(x.id, { page: x.page, text: segText(x), label: x.engine_label });
    S.inspTab = 'page';
    renderWorkspace();
  };
  const clickRow = (e, b) => {
    if (e.shiftKey) return rangeTo(b, e.metaKey || e.ctrlKey);          // the anchor stays: Shift+click again re-ranges
    S.listAnchor = b.id;
    if (e.metaKey || e.ctrlKey || S.selectMode) return toggleMulti(b);
    clearSelection(); selectBlock(b.id);
  };
  // dragging a selected row moves the WHOLE selection (in reading order, one undo); any other row moves alone
  const dragSet = (id) => (S.multi.has(id) && S.multi.size > 1 ? [...S.multi.keys()].filter(k => order.some(x => x.id === k)) : [id]);
  for (const { b, depth, mark } of tree.rows) {
    const txt = b.chosen && (b.chosen.text || b.chosen.latex) ? segText(b) : `[${kindLabel(b)} — εικόνα]`;
    const settled = ['approved', 'image_only'].includes(b.review);
    list.append(h('div', { 'data-seg': b.id, onmouseenter: () => hoverSeg(b.id, 'list'), onmouseleave: () => hoverSeg(null),
      class: 'list-item rv-row-' + rowState(b.review) + (S.multi.has(b.id) ? ' multi' : '') + (b.omitted ? ' omitted' : '') + ((b.groups || []).length ? ' in-group' : '') + (b.kind === 'heading' ? ' is-heading' : ''),
      title: `${CAT_EL[CAT(b)]} · ${REVIEW_EL[b.review]} · περιεχόμενο από: ${b.content_source || '—'} · Σύρε για αλλαγή σειράς (ή Alt+↑/↓) · ⌘/Ctrl+κλικ: προσθήκη στην επιλογή · Shift+κλικ: συνεχές εύρος`,
      'aria-selected': S.multi.has(b.id) || S.sel === b.id ? 'true' : 'false',
      onmousedown: (e) => { if (e.shiftKey) e.preventDefault(); },        // Shift+click selects rows, not text
      draggable: S.selectMode ? 'false' : 'true',
      ondragstart: (e) => { dragId = b.id; e.dataTransfer.effectAllowed = 'move'; e.currentTarget.classList.add('dragging');
        const n = dragSet(b.id).length; if (n > 1) e.dataTransfer.setData('text/plain', `${n} τμήματα`); },
      ondragend: (e) => e.currentTarget.classList.remove('dragging'),
      ondragover: (e) => { if (!dragId || dragSet(dragId).includes(b.id)) return; e.preventDefault(); const r = e.currentTarget.getBoundingClientRect();
        e.currentTarget.classList.toggle('drop-before', e.clientY < r.top + r.height / 2); e.currentTarget.classList.toggle('drop-after', e.clientY >= r.top + r.height / 2); },
      ondragleave: (e) => e.currentTarget.classList.remove('drop-before', 'drop-after'),
      ondrop: (e) => { e.preventDefault(); const before = e.currentTarget.classList.contains('drop-before'); e.currentTarget.classList.remove('drop-before', 'drop-after');
        if (!dragId || dragSet(dragId).includes(b.id)) return;
        const ids = dragSet(dragId);
        if (ids.length > 1) act(() => api(`${B(S.book.id)}/segments/move`, { body: { blocks: ids, ...(before ? { before: b.id } : { after: b.id }) } }), `Μετακινήθηκαν ${ids.length} τμήματα`);
        else dropInReadingOrder(dragId, b, before); },
      onclick: (e) => clickRow(e, b) },
      h('span', { class: 'k-dot k-' + CAT(b) }), h('span', { class: 'small mut', style: { width: '20px', flex: 'none' } }, b.reading_order),
      h('span', { class: 'rails', 'aria-hidden': 'true' }, Array.from({ length: depth }, () => h('i'))),
      mark ? h('span', { class: 'chip lvl' + (mark.cls ? ' ' + mark.cls : ''), title: mark.title || '' }, mark.text) : null,
      excursusChip(b), groupMark(b), h('span', { class: 'txt grow' }, txt), entityChip(b), continuesInMark(b),
      S.multi.has(b.id) ? h('span', { class: 'ord', title: 'Θέση στην επιλογή' }, String([...S.multi.keys()].indexOf(b.id) + 1)) : null,
      b.omitted ? h('span', { class: 'chip warn', title: 'Εκτός εξόδου (επιλογή σου) — μένει στο βιβλίο' }, 'εκτός εξόδου') : null,
      settled ? h('span', { class: 'ok-mark' }, '✓') : null));
  }
  const byProfile = pg.blocks.filter(x => !x.excluded && profileHidden(x));
  if (byProfile.length) {               // the output profile's exclusions: listed only here, without boxes on the page
    body.append(h('details', {}, h('summary', {}, `Κρυμμένα από την έξοδο — προφίλ (${byProfile.length}) `, infoTip('profilehidden')),
      byProfile.map(b => h('div', { class: 'list-item omitted', 'data-seg': b.id, onclick: () => selectBlock(b.id) },
        h('span', { class: 'chip ent' }, entName(b.profile_omitted.type.replace(/^excursus:/, ''))),
        b.profile_omitted.confirmed ? null : h('span', { class: 'chip warn', title: 'Αυτόματη εξαίρεση· επιβεβαίωσέ την στην καρτέλα «Έλεγχος»' }, 'προς επιβεβαίωση'),
        h('span', { class: 'txt grow' }, (b.chosen && (b.chosen.text || b.chosen.latex)) ? segText(b) : `[${kindLabel(b)} — εικόνα]`),
        h('button', { class: 'small ghost', title: 'Να μπει στην έξοδο παρά το προφίλ (μόνο αυτό το τμήμα)', onclick: (e) => { e.stopPropagation();
          act(() => api(`${B(S.book.id)}/output`, { body: { blocks: [b.id], include: true } }), 'Ξανά στην έξοδο'); } }, 'Στην έξοδο')))));
  }
  const furn = pg.blocks.filter(x => x.excluded);
  if (furn.length) {
    const FURN_EL = { background_fill: 'Φόντο τίτλου', empty_fragment: 'Κενό κομμάτι', no_text_ink: 'Μελάνι χωρίς κείμενο', duplicate_reading: 'Διπλή ανάγνωση', page_number: 'Αριθμός σελίδας', running_title: 'Τίτλος κεφαλίδας', running_text: 'Κεφαλίδα/υποσέλιδο' };
    body.append(h('details', {}, h('summary', {}, `Εξαιρέθηκαν ως στοιχεία σελίδας (${furn.length}) `, infoTip('furniture')),
      furn.map(b => h('div', { class: 'list-item excluded', 'data-seg': b.id, onmouseenter: () => hoverSeg(b.id, 'list'), onmouseleave: () => hoverSeg(null), onclick: () => selectBlock(b.id) },
        h('span', { class: 'txt grow' }, `${FURN_EL[b.excluded.as] || b.excluded.as}: ${(b.chosen && b.chosen.text) || '[εικόνα]'}`),
        h('button', { class: 'small ghost', title: 'Δεν είναι θόρυβος: να μπει ξανά στην έξοδο', onclick: (e) => { e.stopPropagation(); act(() => api(`${B(S.book.id)}/suggestions/${b.excluded.key}/dismiss`, { body: {} }), 'Το τμήμα επανήλθε στην έξοδο'); } }, 'Επαναφορά')))));
  }
  const hidden = [...(info.hidden || []), ...(info.replaced_by_region || [])];
  if (hidden.length) {
    body.append(h('h3', {}, 'Κρυμμένα / αντικατεστημένα πλαίσια'));
    for (const x of info.hidden || []) body.append(h('div', { class: 'list-item', style: { cursor: 'default' } }, h('span', { class: 'grow small' }, `${KIND_EL[x.kind] || x.kind} · ${x.block_id}`),
      h('button', { class: 'small', onclick: () => act(() => api(`${B(S.book.id)}/blocks/${x.block_id}/show`, { body: {} }), 'Το πλαίσιο επανήλθε') }, 'Επαναφορά')));
    for (const x of info.replaced_by_region || []) body.append(h('div', { class: 'small mut', style: { padding: '4px 0' } }, `${x.block_id} → αντικαταστάθηκε από περιοχή ${x.region}`));
  }
}
