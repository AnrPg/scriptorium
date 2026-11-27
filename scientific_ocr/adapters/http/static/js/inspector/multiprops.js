// Properties of a SELECTION: every property of the segment card (flow, continuation, structure level, caption,
// machine tag, output, approve / image only) applied to all selected segments at once — ONE undoable step
// (POST /decide-many). Segments the change does not fit (e.g. a figure cannot be a caption) are skipped and named.
import { assignSelectionButton } from '../book/entities.js';
import { B, api } from '../core/api.js';
import { h } from '../core/dom.js';
import { S, kindLabel } from '../core/state.js';
import { CAPROLE_EL } from '../core/strings.js';
import { dropdown, openGroupMenu, openMergeMenu } from '../inspector/multi.js';
import { act, toast } from '../ui/feedback.js';
import { labelPicker } from '../ui/math.js';
import { excursusDialog } from '../inspector/excursus.js';
import { renderWorkspace } from '../workspace/workspace.js';

const FIG = ['figure', 'chart', 'molecular_structure', 'reaction_scheme', 'lewis_structure', 'table', 'math', 'chemical_reaction'];

export function selectionProps(ids, state) {
  const many = (action, extra, msg) => act(() => api(`${B(S.book.id)}/decide-many`, { body: { blocks: ids, action, ...extra } }).then(r => {
    if (r.skipped?.length) toast(`${r.skipped.length} από ${ids.length} δεν άλλαξαν: ${[...new Set(r.skipped.map(x => x.reason))].join('; ').slice(0, 160)}`);
    return r;
  }), `${msg} (${ids.length} τμήματα)`);
  const toggle = (k) => { state.prop = state.prop === k ? null : k; renderWorkspace(); };
  const btn = (k, label) => h('button', { class: 'small ghost prop-btn' + (state.prop === k ? ' on' : ''), 'aria-expanded': String(state.prop === k), onclick: () => toggle(k) }, state.prop === k ? 'Κλείσιμο' : label);
  const row = (k, label, value, action, editor) => [h('div', { class: 'prop-row' }, h('span', { class: 'prop-k' }, label), h('span', { class: 'prop-v' }, value), h('span', { class: 'prop-a' }, action || '')),
    state.prop === k && editor ? h('div', { class: 'prop-edit' }, editor()) : null];
  const choices = (label, opts, run) => h('div', { class: 'row small' }, label, opts.map(([k, l]) => h('button', { class: 'small', onclick: () => run(k, l) }, l)));
  // what the selection currently has (segments of this page; others are on other pages)
  const here = ids.map(id => S.page.blocks.find(b => b.id === id)).filter(Boolean);
  const summary = (f) => { const vals = [...new Set(here.map(f))]; return here.length < ids.length ? 'διάφορα' : vals.length === 1 ? vals[0] : 'διάφορα'; };
  const levelSel = h('select', { class: 'prop-select', 'aria-label': 'Επίπεδο στη δομή', onchange: () => { if (levelSel.value !== '') many('set_heading_level', { level: +levelSel.value }, 'Η δομή ενημερώθηκε'); } },
    h('option', { value: '' }, '—'), h('option', { value: 0 }, 'Κείμενο'), [1, 2, 3, 4, 5, 6].map(n => h('option', { value: n }, `Τίτλος H${n}`)));
  const figures = S.page.blocks.filter(b => FIG.includes(b.kind) && !ids.includes(b.id));
  const groups = (S.book.groups || []).filter(g => g.members.some(m => S.page.blocks.some(b => b.id === m)) && !g.members.some(m => ids.includes(m)));
  // merge needs one page (an unknown page counts as another one); grouping may span pages
  const pages = new Set(ids.map(id => S.multi.get(id)?.page ?? null)), multiPage = pages.size > 1 || pages.has(null);
  return h('div', { class: 'props card' }, h('div', { class: 'prop-title' }, `Ιδιότητες επιλογής (${ids.length})`),
    row('join', 'Μαζί', h('span', { class: 'mut small' }, 'σημείωση ομάδας / ένα νέο τμήμα'), h('span', { class: 'row' },
      h('button', { class: 'small', title: 'Τα τμήματα μένουν ως έχουν· σημειώνεται ότι ανήκουν μαζί (και σε πολλές σελίδες) — G', onclick: (e) => openGroupMenu(e.currentTarget) }, 'Ομαδοποίηση…'),
      h('button', { class: 'small', disabled: multiPage, title: multiPage ? 'Η συγχώνευση γίνεται μέσα σε μία σελίδα· σε πολλές σελίδες: ομαδοποίηση' : 'Ένα νέο τμήμα με τα κείμενά τους, με τη σειρά της επιλογής — M',
        onclick: (e) => openMergeMenu(e.currentTarget) }, 'Συγχώνευση σε ένα…'))),
    row('cat', 'Κατηγορία', h('span', { class: 'mut small' }, 'όλα μαζί = μία οντότητα'), assignSelectionButton(ids)),
    row('lvl', 'Δομή', h('span', { class: 'mut prop-line' }, summary(b => b.kind === 'heading' ? `H${b.heading_level || 1}` : 'κείμενο')), levelSel),
    row('cap', 'Λεζάντα', h('span', { class: 'prop-line' }, summary(b => b.kind === 'caption' ? 'λεζάντα' : 'όχι')), btn('cap', 'Αλλαγή ▾'), () => h('div', { class: 'col' },
      h('div', { class: 'small mut' }, 'Όλα τα επιλεγμένα γίνονται λεζάντες του ίδιου σχήματος ή της ίδιας ομάδας (σχήματα/πίνακες της επιλογής παραλείπονται).'),
      h('div', { class: 'actions' },
        figures.length ? h('button', { onclick: (e) => dropdown(e.currentTarget, [{ title: 'Σχήματα της σελίδας', items: figures.map(f => ({ label: kindLabel(f), hint: `#${f.reading_order}`, value: f.id })) }],
          (it) => many('associate_caption', { figure_id: it.value }, 'Έγιναν λεζάντες')) }, 'Λεζάντες του σχήματος…') : null,
        groups.length ? h('button', { onclick: (e) => dropdown(e.currentTarget, [{ title: 'Ομάδες της σελίδας', items: groups.map(g => ({ label: g.name || S.meta.group_types[g.type] || g.type, hint: `${g.figure ? 'σχήμα · ' : ''}${g.members.length} τμήματα`, value: g.id })) }],
          (it) => many('associate_group_caption', { group_id: it.value }, 'Έγιναν λεζάντες της ομάδας')) }, 'Λεζάντες της ομάδας…') : null,
        h('button', { class: 'ghost', onclick: () => many('detach_caption', {}, 'Οι λεζάντες αποσυνδέθηκαν') }, 'Αποσύνδεση')),
      choices('Ρόλος:', Object.entries(CAPROLE_EL), (k, l) => many('set_caption_role', { role: k }, `Ρόλος: ${l}`)))),
    row('flow', 'Ροή', h('span', { class: 'prop-line' }, summary(b => b.flow === 'insert' ? 'παρεμβολή' : b.flow === 'furniture' ? 'στοιχείο σελίδας' : 'κείμενο')), btn('flow', 'Αλλαγή ▾'), () => h('div', { class: 'col' },
      choices('Ρόλος:', [['main', 'Κείμενο'], ['insert', 'Παρεμβολή'], ['auto', 'Αυτόματα']], (k, l) => many('set_flow', { flow: k }, `Ροή: ${l}`)),
      choices('Συνέχεια της προηγούμενης παραγράφου:', [[true, 'Ναι'], [false, 'Όχι'], ['auto', 'Αυτόματα']], (k, l) => many('set_continuation', { continues: k }, `Συνέχεια: ${l}`)),
      h('div', { class: 'small mut' }, 'Η συνέχεια ισχύει μόνο για τμήματα κειμένου.'))),
    here.some(b => b.kind === 'table') ? row('tbl', 'Ανάγνωση πίνακα', h('span', { class: 'prop-line' }, summary(b => b.kind !== 'table' ? '—' : ({ cols: 'λίστα ανά στήλες', rows: 'λίστα ανά γραμμές', table: 'πίνακας' })[b.table_layout || (b.table_list && !b.table_list.candidate ? b.table_list.order : 'table')])), btn('tbl', 'Αλλαγή ▾'), () => h('div', { class: 'col' },
      h('div', { class: 'small mut' }, 'Μόνο για πίνακες με κείμενο (τα άλλα τμήματα παραλείπονται). Λίστα με επίπεδα: κύρια η 1η στήλη (ανά γραμμές) ή η 1η γραμμή (ανά στήλες) — άλλη κύρια στήλη/γραμμή διαλέγεται σε κάθε πίνακα χωριστά. «Ένα ανά κελί» = απλή λίστα των κελιών.'),
      choices('Λίστα:', [['rows:0', 'Ανά γραμμές (1η στήλη κύρια)'], ['cols:0', 'Ανά στήλες (1η γραμμή κύρια)'], ['rows', 'Ανά γραμμές, ένα ανά κελί'], ['cols', 'Ανά στήλες, ένα ανά κελί']],
        (k, l) => { const [layout, key] = k.split(':'); many('set_table_layout', { layout, ...(key != null ? { key: +key } : {}) }, `Ανάγνωση: ${l}`); }),
      choices('Ή:', [['table', 'Πίνακας'], ['auto', 'Αυτόματα']], (k, l) => many('set_table_layout', { layout: k }, `Ανάγνωση: ${l}`)))) : null,
    row('label', 'Ετικέτα μηχανής', h('span', { class: 'prop-line' }, summary(b => (b.engine_label || '—').replace(':', ' · '))), btn('label', 'Αλλαγή ▾'), () => h('div', {},
      h('div', { class: 'small mut' }, 'Κάθε επιλεγμένο τμήμα ξαναγίνεται OCR με τη νέα ετικέτα.'),
      labelPicker([], (l) => many('relabel', { label: l }, 'Νέα ετικέτα· τρέχει νέο OCR')))),
    row('out', 'Στην έξοδο', h('span', { class: 'prop-line' }, summary(b => b.omitted ? 'όχι' : 'ναι')), h('span', { class: 'row' },
      h('button', { class: 'small', onclick: () => act(() => api(`${B(S.book.id)}/output`, { body: { blocks: ids, include: true } }), `Στην έξοδο (${ids.length})`) }, 'Ναι'),
      h('button', { class: 'small', onclick: () => act(() => api(`${B(S.book.id)}/output`, { body: { blocks: ids, include: false } }), `Εκτός εξόδου (${ids.length})`) }, 'Όχι'))),
    row('exc', 'Παρέκβαση', h('span', { class: 'mut small' }, 'από το πρώτο έως το τελευταίο επιλεγμένο'),
      h('button', { class: 'small', onclick: () => excursusDialog({ blocks: ids }, `${ids.length} επιλεγμένα (και ό,τι υπάρχει ανάμεσα)`) }, 'Σήμανση…')),
    row('content', 'Περιεχόμενο', h('span', { class: 'mut small' }, 'όπως είναι / ως εικόνα'), h('span', { class: 'row' },
      h('button', { class: 'small good', onclick: () => many('approve', {}, 'Εγκρίθηκαν') }, 'Έγκριση'),
      h('button', { class: 'small', onclick: () => many('mark_image_only', {}, 'Μόνο εικόνα') }, 'Μόνο εικόνα'))));
}
