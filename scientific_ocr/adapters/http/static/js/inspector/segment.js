import { entName, segmentEntities } from '../book/entities.js';
import { describePending, pendingFor, startRebuild } from '../ui/rebuild.js';
import { conflictRanges, conflictsOf, highlightTextarea, markedText } from '../ui/highlight.js';
import { B, api } from '../core/api.js';
import { $, clear, h } from '../core/dom.js';
import { K } from '../core/keyboard.js';
import { render } from '../core/nav.js';
import { R, S, kindLabel, outOfOutput } from '../core/state.js';
import { ACTION_EL, REVIEW_EL, SRC_EL, infoTip } from '../core/strings.js';
import { captionPanel } from '../inspector/captions.js';
import { excursusEditor, excursusValue } from '../inspector/excursus.js';
import { loopPosition, stepReview } from '../inspector/reviewloop.js';
import { historyPanel } from '../inspector/history.js';
import { queueNeighbours, renderInspector } from '../inspector/inspector.js';
import { engChip, lineageDialog, nextPageWithWork, reportWrong, sourceLabel } from '../inspector/lineage.js';
import { groupsPanel } from '../inspector/multi.js';
import { notePanel, proposalCard } from '../inspector/note.js';
import { act, actLater } from '../ui/feedback.js';
import { currentLabels, labelPicker, mathBox, stripMathDelims, textMathBox } from '../ui/math.js';
import { renderWorkspace, selectBlock, setSelection } from '../workspace/workspace.js';

// ---------------------------------------------------------------- segment review: one current version, explicit modes
// State per selected segment:  version = machine | decided (a reviewer value is in force)
//                              mode    = view | draft (editing) | proposal (a model proposal awaits a decision)
// Each mode exposes ONE primary action; actions that make no sense in a mode are not shown.
export function segState(blk) {
  if (!S.seg || S.seg.id !== blk.id) S.seg = { id: blk.id, mode: 'view', draft: null, base: null, proposal: null, note: '' };
  const human = (blk.decisions || []).filter(d => S.meta.value_actions.includes(d.action));
  return { seg: S.seg, decided: human.length ? human[human.length - 1] : null };
}
export const isLatexBlock = blk => (blk.chosen || {}).type === 'latex' || ['math', 'chemical_reaction'].includes(blk.kind);
export const currentValue = blk => { const ch = blk.chosen || {}; return ch.text || ch.latex || ''; };
export function contentView(blk, value, latex, { codeOpen = null } = {}) {   // rendered first, then the source code
  if (!value) return h('div', { class: 'math-view mut' }, 'Μόνο εικόνα — το απόσπασμα της σελίδας είναι το περιεχόμενο.');
  const marks = conflictRanges(value, conflictsOf(blk));          // where the engines disagree, in the text itself
  const open = codeOpen ?? (marks.length > 0 || latex);
  const code = h('details', { class: 'code-box', open: open || null }, h('summary', { class: 'small mut' }, latex ? 'Κώδικας LaTeX' : 'Κώδικας (Markdown/LaTeX)', marks.length ? ` · ${marks.length} σημείο(α) διαφωνίας μηχανών` : ''),
    h('pre', { class: 'preview-pre', style: { maxHeight: '260px' } }, markedText(value, marks)));
  // shown as the outputs will read it: a table read as a list (pipeline/tables.py) is a list here too; the code
  // (the engine's grid) stays below. Only for the version in force: other versions are compared as they are
  const lst = blk.list_items && value === currentValue(blk) ? blk.list_items : null;
  const shown = lst ? h('div', {},
    h('div', { class: 'small mut' }, `Όπως μπαίνει στην έξοδο: ${listName(blk)} (${lst.items.length})`),
    lst.title ? h('div', { class: 'content-list-label' }, textMathBox(lst.title)) : null,
    lst.label ? h('div', { class: 'content-list-label' }, textMathBox(lst.label + ':')) : null,
    h('ul', { class: 'content-list' }, lst.items.map(t => typeof t === 'string' ? h('li', {}, textMathBox(t))
      : h('li', {}, textMathBox(t.key + ':'), t.fields.length ? h('ul', { class: 'content-kv' }, t.fields.map(([k, v]) =>
        h('li', {}, h('span', { class: 'mut' }, textMathBox(k + ':')), ' ', textMathBox(v)))) : null))))
    : latex ? mathBox(value) : textMathBox(value);
  // the output profile dropped the page's styling before review (pipeline/profile.py text_policy): say so
  const styled = blk.chosen?.profile_source && value === currentValue(blk)
    ? h('div', { class: 'small mut', title: `Όπως διαβάστηκε:\n${blk.chosen.profile_source}` }, 'Προεπεξεργασμένο για το προφίλ (μορφοποίηση, συλλαβισμός, σημειώσεις δικαιωμάτων), όπως θα βγει· το αρχικό κρατιέται (δείξε με το ποντίκι).') : null;
  return h('div', { class: 'content-view' }, styled, shown, code);
}
const NEEDS_REVIEW = ['required', 'recommended', 'unresolved'];
const DECIDING = new Set();                     // block ids whose decision request is in flight
// Why a decision was not applied (pipeline/overlay.py Stale messages), in plain words.
const STALE_WHY = {
  'approved content changed': 'Αυτό που είχες εγκρίνει δεν είναι πια το ίδιο με ό,τι δείχνει τώρα η εφαρμογή.',
  'machine output changed since the decision': 'Το αποτέλεσμα της μηχανής άλλαξε μετά την απόφασή σου (νέο OCR, νέα έκδοση μοντέλου ή άλλη διάταξη).',
  'block no longer exists (layout or OCR changed)': 'Το τμήμα δεν υπάρχει πια στην ίδια μορφή (άλλαξε η διάταξη ή το OCR).',
};
export function segmentPanel(body, blk) {
  const nb = queueNeighbours(blk), book = S.book, { seg, decided } = segState(blk);
  const latex = isLatexBlock(blk);
  const needs = NEEDS_REVIEW.includes(blk.review) && !decided;
  const rerender = () => renderInspector(body.parentElement);
  // act() waits for the rebuild (up to 120 s): the reviewer may have moved on meanwhile, so the follow-up (reset the
  // panel, advance to the next item) only applies if this block on this page is still the one shown. A block with a
  // decision in flight ignores further decisions (double click, repeated A / ⌘↵).
  const ADVANCES = new Set(['approve', 'replace_text', 'replace_latex', 'approve_candidate', 'mark_image_only', 'mark_unreadable']);
  const decide = (action, extra = {}, msg) => {
    if (DECIDING.has(blk.id)) return Promise.resolve();
    const id = blk.id, page = S.page.page, bookId = book.id;
    const here = () => S.route?.view === 'ws' && S.book?.id === bookId && S.page?.page === page && S.sel === id;
    DECIDING.add(id);
    const done = actLater(() => api(`${B(book.id)}/decide`, { body: { block_id: id, action, ...extra } }), msg || ACTION_EL[action], { key: id })
      .finally(() => { DECIDING.delete(id); if (here()) renderWorkspace(); });   // re-enable the actions
    if (ADVANCES.has(action)) { S.seg = null; stepReview(1); }   // the next one opens AT ONCE; the decision is sent behind
    else rerender();
    return done;
  };
  const pending = DECIDING.has(blk.id);
  S.segActions = {};                              // what the keyboard may do in the current state (see keydown)

  // 1. header: what it is, its state, the review queue
  body.append(h('div', { class: 'row seg-head' },
    h('div', { class: 'grow' }, h('div', { style: { fontWeight: 600 } }, kindLabel(blk), blk.source === 'region' ? h('span', { class: 'mut small' }, ' · δική σου περιοχή') : null),
      h('span', { class: 'chip rv-' + blk.review }, REVIEW_EL[blk.review])),
    h('button', { class: 'icon', onclick: () => stepReview(-1), title: 'Προηγούμενο προς έλεγχο, σε όλο το βιβλίο (K)' }, '↑'),
    (() => {      // place in the whole-book loop (filled when known); the page count stays as a hint
      const sp = h('span', { class: 'small mut', title: nb.pos ? `Σε αυτή τη σελίδα: ${nb.pos} από ${nb.total}` : '' }, nb.pos ? `${nb.pos}/${nb.total}` : '');
      loopPosition(blk.id).then(p => { if (p.i) { sp.textContent = `${p.i.toLocaleString('el-GR')}/${p.total.toLocaleString('el-GR')}`; sp.title = `Θέση στον έλεγχο όλου του βιβλίου${nb.pos ? ` · σε αυτή τη σελίδα ${nb.pos} από ${nb.total}` : ''}`; } }, () => {});
      return sp;
    })(),
    h('button', { class: 'icon', onclick: () => stepReview(1), title: 'Επόμενο προς έλεγχο, σε όλο το βιβλίο (J)' }, '↓')));
  // 2. ONE status sentence: what is needed and which version is in force
  const reasons = blk.reasons.filter(r => R(r).review || R(r).crop || R(r).finding || ['TRIAGE_DOWNGRADED', 'ACCEPTED_BY_SAMPLING'].includes(r));
  const info = blk.reasons.filter(r => !reasons.includes(r));
  const top = reasons.find(r => R(r).review === 'required') || reasons[0];
  const status = seg.mode === 'draft' ? ['v-pending', 'Επεξεργάζεσαι πρόχειρο (μη αποθηκευμένο)']
    : seg.mode === 'proposal' ? ['v-pending', 'Πρόταση μοντέλου — δεν έχει εφαρμοστεί']
    : decided ? ['v-human', `Η απόφασή σου: ${ACTION_EL[decided.action]} · ${(decided.ts || '').replace('T', ' ').slice(0, 16)}`]
    : needs ? ['v-need', `Χρειάζεται έλεγχο: ${top ? R(top).el : REVIEW_EL[blk.review]}`]
    : ['v-machine', blk.review === 'approved' ? 'Εγκεκριμένο από εσένα' : blk.review === 'accepted' ? 'Δεκτό στατιστικά — δεν χρειάζεται έλεγχο' : 'Δεν χρειάζεται έλεγχο'];
  body.append(h('div', { class: 'version ' + status[0] }, h('b', {}, status[1]),
    h('div', { class: 'small mut' }, decided ? (decided.via ? `από σημείωση: «${decided.via.note}» · πρόταση ${decided.via.proposed_by}` : 'περιεχόμενο: εσύ')
      : `περιεχόμενο: μηχανή (${sourceLabel(blk.content_source) || '—'})`)));
  { const sug = tableListSuggestion(blk, book); if (sug) body.append(sug); }
  if (blk.merged_from) body.append(h('div', { class: 'note' }, h('b', {}, `Ενωμένο από ${blk.merged_from.length} κομμάτια`),
    blk.merged_from.map((m, i) => h('div', { class: 'small' }, `${i + 1}. ${(m.text || '[εικόνα]').slice(0, 70)}`)),
    h('button', { class: 'small', style: { marginTop: '6px' }, onclick: () => act(() => api(`${B(book.id)}/merges/${blk.merge_id}/remove`, { body: {} }), 'Το τμήμα διασπάστηκε στα κομμάτια του') }, 'Διάσπαση')));
  // 3. the source: what is printed
  const waiting = pendingFor(blk);
  if (waiting.length) body.append(h('div', { class: 'note', title: 'Η αλλαγή καταγράφηκε· περνά στο βιβλίο με την επόμενη ενημέρωση' },
    h('b', {}, 'Σε αναμονή: '), waiting.map(describePending).join(' · '),
    ' ', h('button', { class: 'small', onclick: () => startRebuild() }, 'Ενημέρωση βιβλίου')));
  if (blk.crop) body.append(h('img', { class: 'crop', src: `${B(book.id)}/${blk.crop}`, alt: 'Απόσπασμα από τη σελίδα' }));
  // 4. why (only when something is asked of the reviewer); machine notes collapsed
  if (reasons.length && needs) {
    body.append(h('h3', {}, 'Γιατί επισημάνθηκε'));
    for (const r of reasons) {
      const crit = R(r).finding && R(r).finding[1] === 'critical';
      const why = { TRIAGE_DOWNGRADED: 'triage', ACCEPTED_BY_SAMPLING: 'sampling', FIGURE_INTERNAL_TEXT_WITHHELD: 'imagecontent', RUNNING_HEADER_EMPTY: 'imagecontent' }[r]
        || (r === 'SYMBOL_READING_CONFLICT' && ((blk.evidence || {})[r]?.conflicts || []).some(c => c.class === 'partial_reading') ? 'partial' : null);
      body.append(h('div', { class: 'reason' + (crit ? ' crit' : '') }, h('span', { class: 'ic' }, crit ? '⚠' : R(r).review === 'required' ? '●' : '○'), h('span', {}, R(r).el, why ? ' ' : '', why ? infoTip(why) : null)));
      const ev = (blk.evidence || {})[r];
      if (ev && ev.conflicts) body.append(h('div', { class: 'evidence' }, ev.conflicts.slice(0, 6).map(c => h('div', { class: 'conflict-row' },
        h('div', {}, 'Marker: ', h('b', {}, c.primary), '  ·  Docling: ', h('b', {}, c.secondary), h('span', { class: 'mut' }, `  (${c.class})`)),
        c.before !== undefined ? h('div', { class: 'conflict-ctx' }, '…' + c.before + ' ', h('mark', { class: 'conflict' + (c.critical ? ' crit' : '') }, c.primary), ' ' + c.after + '…')
          : h('div', { class: 'mut' }, `…${c.context}…`)))));
      if (ev && ev.note) body.append(h('div', { class: 'note' }, '«' + ev.note + '»'));
      if (r === 'DECISION_STALE' && ev) body.append(h('div', { class: 'note small' }, STALE_WHY[ev.why] || ev.why || '',
        ' Η απόφαση μένει στο ιστορικό· μια νέα απόφαση εδώ την αντικαθιστά.'));
    }
  }
  const notes = [...(needs ? [] : reasons), ...info];
  if (notes.length || blk.rule_corrections) body.append(h('details', { class: 'machine-notes' }, h('summary', { class: 'small mut' }, `Σημειώσεις μηχανής (${notes.length + (blk.rule_corrections ? 1 : 0)})`),
    notes.map(r => h('div', { class: 'small' }, '· ', R(r).el)),
    blk.rule_corrections ? h('div', { class: 'small' }, '· Κανόνες βιβλίου: ', blk.rule_corrections.map(c => `${c.from}→${c.to}`).join(', ')) : null));
  const cands = (blk.candidates || []).filter(c => c.text || c.latex || c.smiles || c.error);
  if (cands.length || (blk.figure_text_candidates || []).length) body.append(h('details', { class: 'machine-notes', open: seg.detail === 'engines' || null,
    ontoggle: (e) => { seg.detail = e.currentTarget.open ? 'engines' : null; } },
    h('summary', { class: 'small mut' }, `Αναγνώσεις μηχανών (${cands.length})`),
    h('div', { class: 'small mut' }, 'Μη επαληθευμένες. «Ως πρόχειρο» τη φέρνει για επεξεργασία, «Χρήση αυτής» την κάνει απόφασή σου.'),
    cands.map(c => h('div', { class: 'cand' }, h('div', { class: 'row' }, engChip(c.engine), h('span', { class: 'grow small mut' }, c.smiles ? 'SMILES' : '', c.status && c.status !== 'OK' ? ` · ${c.status}` : ''),
      (c.text || c.latex) && ['marker', 'docling', 'unimernet'].includes(c.engine) ? h('button', { class: 'ghost small', onclick: () => { seg.mode = 'draft'; seg.base = currentValue(blk); seg.draft = latex ? stripMathDelims(c.latex || c.text) : (c.text || c.latex); rerender(); } }, 'Ως πρόχειρο') : null,
      (c.text || c.latex) && ['marker', 'docling', 'unimernet'].includes(c.engine) && seg.mode === 'view' ? h('button', { class: 'small', onclick: () => decide('approve_candidate', { engine: c.engine }) }, 'Χρήση αυτής') : null),
      h('pre', {}, c.text || c.latex || c.smiles || c.error), c.latex && !c.text ? mathBox(c.latex) : null)),
    (blk.figure_text_candidates || []).length ? h('div', { class: 'cand' }, h('b', {}, 'Κείμενο μέσα στο σχήμα (Docling)'), h('pre', { class: 'small' }, blk.figure_text_candidates.join(' | '))) : null));
  (blk.human_notes || []).forEach(nn => body.append(h('div', { class: 'note' }, `Σημείωση ${nn.actor || ''}: «${nn.note}»`)));

  // 5. content + 6. the actions of THIS state (one primary)
  body.append(h('h3', {}, 'Περιεχόμενο'));
  const area = h('div'); body.append(area);
  if (seg.mode === 'proposal' && seg.proposal) {
    proposalCard(area, blk, seg, rerender, latex);
  } else if (seg.mode === 'draft') {
    const ta = h('textarea', { id: 'editor', spellcheck: 'false', 'aria-label': latex ? 'Πρόχειρο LaTeX' : 'Πρόχειρο κειμένου' }); ta.value = seg.draft ?? '';
    const taWrap = highlightTextarea(ta, conflictsOf(blk));
    const preview = h('div'), diff = h('div', { class: 'small mut', style: { margin: '6px 0' } });
    const save = h('button', { class: 'primary' }, 'Αποθήκευση της διόρθωσής μου', h('kbd', {}, '⌘↵'));
    const upd = () => { seg.draft = ta.value; clear(preview).append(latex ? mathBox(ta.value) : textMathBox(ta.value));
      const changed = ta.value.trim() !== (seg.base || '').trim(); save.disabled = !changed || pending;
      diff.textContent = changed ? 'Αλλαγή σε σχέση με την τρέχουσα εκδοχή.' : 'Καμία αλλαγή ακόμα.'; };
    ta.addEventListener('input', upd);
    const doSave = () => !save.disabled && decide(latex ? 'replace_latex' : 'replace_text', { value: ta.value }, 'Η διόρθωσή σου ισχύει πλέον');
    const discard = () => { S.seg = null; rerender(); };
    save.onclick = doSave;
    area.append(h('div', { class: 'small mut' }, latex ? 'Πρόχειρο (LaTeX):' : 'Πρόχειρο:'), taWrap,
      h('div', { class: 'small mut', style: { margin: '6px 0 2px' } }, 'Όπως θα αποδοθεί (ζωντανά):'), preview, diff,
      h('div', { class: 'actions' }, save, h('button', { onclick: discard }, 'Απόρριψη αλλαγών', h('kbd', {}, 'Esc'))),
      h('details', {}, h('summary', { class: 'small mut' }, 'Τρέχουσα εκδοχή (για σύγκριση)'), contentView(blk, seg.base, latex)));
    upd();
    // focus once when the draft opens; later re-renders (poll refresh) must not pull focus back from elsewhere
    if (!seg.focused) { seg.focused = true; setTimeout(() => ta.focus(), 30); }
    S.segActions = { save: pending ? null : doSave, discard };
  } else {
    area.append(contentView(blk, currentValue(blk), latex));
    const edit = () => { seg.mode = 'draft'; const raw = currentValue(blk) || (blk.chosen.withheld_latex || blk.chosen.withheld_text || ((blk.candidates || []).find(c => c.latex || c.text) || {})[latex ? 'latex' : 'text'] || ((blk.candidates || []).find(c => c.text) || {}).text || '');
      seg.base = latex ? stripMathDelims(raw) : raw; seg.draft = seg.base; rerender(); };
    const imageOnly = blk.chosen.type === 'image_only';
    const approve = () => decide('approve');
    if (decided) {
      // your decision is asked again (taken under other conditions, e.g. before a text-only output profile):
      // confirming it as it is is one action (A), and the review moves on
      const again = NEEDS_REVIEW.includes(blk.review);
      area.append(h('div', { class: 'actions' },
        again ? h('button', { class: 'good', disabled: pending, onclick: approve, title: 'Η απόφασή σου ισχύει όπως είναι· καταγράφεται ξανά με τη σημερινή ημερομηνία' },
          imageOnly ? '✓ Εγκρίνω ως έχει — μένει εικόνα' : '✓ Εγκρίνω ως έχει', h('kbd', {}, 'A')) : null,
        h('button', { class: again ? '' : 'primary', onclick: edit }, imageOnly ? 'Γράψε το κείμενο' : 'Επεξεργασία', h('kbd', {}, 'E')),
        h('button', { onclick: () => decide('revert', {}, 'Επέστρεψε στο μηχανικό αποτέλεσμα') }, 'Επαναφορά στο μηχανικό')));
      S.segActions = { edit, approve: again && !pending ? approve : null };
    } else if (needs) {                           // something is asked: confirm, correct, or keep the image
      area.append(h('div', { class: 'actions' },
        h('button', { class: 'good', disabled: pending, onclick: approve }, imageOnly ? '✓ Εγκρίνω — η εικόνα είναι το περιεχόμενο' : '✓ Εγκρίνω αυτή την εκδοχή', h('kbd', {}, 'A')),
        h('button', { onclick: edit }, imageOnly ? 'Γράψε το κείμενο' : 'Επεξεργασία', h('kbd', {}, 'E')),
        imageOnly ? null : h('button', { onclick: () => decide('mark_image_only') }, 'Μόνο εικόνα', h('kbd', {}, 'I'))));
      S.segActions = { approve: pending ? null : approve, edit, imageOnly: imageOnly ? null : () => decide('mark_image_only') };
    } else {                                      // nothing to resolve: no approval button — editing stays possible
      area.append(h('div', { class: 'actions' }, h('button', { onclick: edit }, imageOnly ? 'Γράψε το κείμενο' : 'Επεξεργασία', h('kbd', {}, 'E'))));
      S.segActions = { edit, approve: pending ? null : approve };            // A still records an explicit check (for a VERIFIED release)
    }
  }
  // 7. properties: one card, one row each (label · value · action)
  if (seg.mode === 'view') body.append(propertiesCard(blk, book, decide, seg, rerender));
  // 8. tools and details
  if (seg.mode === 'view') {
    S.segActions.wrong = () => reportWrong(blk); S.segActions.unreadable = () => decide('mark_unreadable');
    const tools = [
      { key: 'note', icon: '✍', label: 'Διόρθωση με λόγια', kbd: 'N', hint: S.meta.note_model ? 'περιέγραψε τι είναι λάθος όπως θα το έλεγες· το μοντέλο προτείνει τη διόρθωση και την εγκρίνεις εσύ' : 'δεν υπάρχει τοπικό μοντέλο', render: () => unwrap(notePanel(blk, seg, rerender)) },
      { key: 'more', icon: '⋯', label: 'Άλλα', hint: '', render: () => h('div', { class: 'menu-list', role: 'menu' },
        !needs && !decided ? menuItem('✓', 'Σημείωση ως ελεγμένο', 'A', () => decide('approve'), 'Καταγράφει ρητό έλεγχο (χρειάζεται για έκδοση «Επαληθευμένο»)') : null,
        menuItem('↻', 'Είναι λάθος — νέο OCR', 'W', () => reportWrong(blk), 'Ξαναδιαβάζει την περιοχή'),
        menuItem('◌', 'Δυσανάγνωστο', 'U', () => decide('mark_unreadable'), 'Το απόσπασμα μένει ως εικόνα'),
        h('div', { class: 'menu-sep', role: 'separator' }),
        blk.source === 'region'
          ? menuItem('✕', 'Αφαίρεση περιοχής', '⌫', () => act(() => api(`${B(book.id)}/regions/${blk.region_id}/remove`, { body: {} }), 'Η περιοχή αφαιρέθηκε'), '', true)
          : menuItem('✕', 'Αφαίρεση λάθος πλαισίου', '⌫', () => act(() => api(`${B(book.id)}/blocks/${blk.id}/hide`, { body: {} }), 'Το λάθος πλαίσιο αφαιρέθηκε'),
              'Για πλαίσιο που είναι ΛΑΘΟΣ (θόρυβος, διπλό, λάθος περίγραμμα). Για σωστό περιεχόμενο που δεν θέλεις στο κείμενο: «Στην έξοδο» στις Ιδιότητες.', true)) },
    ].filter(Boolean);
    body.append(toolStrip(tools, seg, rerender));
    S.segActions.note = () => { seg.tool = 'note'; rerender(); }; S.segActions.label = () => { seg.prop = 'label'; rerender(); };
  }
  body.append(detailTabs([
    { key: 'history', label: 'Ιστορικό αποφάσεων', count: (blk.decisions || []).length, render: () => { const d = historyPanel(blk, latex); d.open = true; return unwrap(d); } },
    { key: 'lineage', label: 'Προέλευση (γράφος)', count: (blk.lineage || []).length, open: () => lineageDialog(blk) },
  ], seg, rerender));
}
// Properties of the segment in the BOOK (not its content): one aligned row each — label · value (one line) · one
// action; a row's editor opens UNDER the row (one at a time). Output is a quiet switch at the end.
const LAYOUT_EL = { cols: 'λίστα ανά στήλες', rows: 'λίστα ανά γραμμές', table: 'πίνακας' };
// a table read as a list (pipeline/tables.py): flat (one item per cell) or with levels, named by the key column / row
export function listName(blk) {
  const order = blk.table_layout === 'rows' || blk.table_layout === 'cols' ? blk.table_layout : blk.table_list?.order;
  const key = blk.table_layout && blk.table_key != null ? blk.table_key : null;
  if (key == null) return `${LAYOUT_EL[order] || 'λίστα'}${blk.table_layout ? ' (ένα στοιχείο ανά κελί)' : ''}`;
  const lab = blk.table_axes?.[order]?.[key] ?? String(key + 1);
  return `${LAYOUT_EL[order]} · κύρι${order === 'rows' ? 'α στήλη' : 'α γραμμή'} «${lab}»`;
}
// one request: the server records what the choice implies (an image adopts the engine reading; the reading is
// confirmed) as one undoable step — the same from here, from the properties row and from a multi-selection
function setTableLayout(blk, book, layout, msg, key = null) {
  return act(() => api(`${B(book.id)}/decide`, { body: { block_id: blk.id, action: 'set_table_layout', layout, ...(key != null ? { key } : {}) } }), msg);
}
// a term grid kept as an image, while the engines read it and it looks like a list: offered where the decision is made
function tableListSuggestion(blk, book) {
  const s = blk.kind === 'table' && blk.chosen?.type !== 'text' ? blk.table_list : null;
  if (!s?.candidate) return null;
  return h('div', { class: 'note' }, h('b', {}, `Προτείνεται: ${LAYOUT_EL[s.order]} (${s.count} όροι, ανάγνωση ${s.candidate})`),
    h('div', { class: 'small mut' }, 'Ως εικόνα ο πίνακας δεν φτάνει σε έξοδο μόνο κειμένου (RAG). Ως λίστα: ένας όρος ανά γραμμή, οι όροι που συνεχίζουν σε εσοχή ενώνονται.'),
    h('div', { class: 'row', style: { marginTop: '6px' } },
      h('button', { class: 'small primary', onclick: () => setTableLayout(blk, book, s.order, `Λίστα όρων από ${s.candidate}`) }, `Ως ${LAYOUT_EL[s.order]}`),
      h('button', { class: 'small', onclick: () => setTableLayout(blk, book, s.order === 'cols' ? 'rows' : 'cols', `Λίστα όρων από ${s.candidate}`) }, `Ως ${LAYOUT_EL[s.order === 'cols' ? 'rows' : 'cols']}`)));
}
function propertiesCard(blk, book, decide, seg, rerender) {
  const toggle = (k) => { seg.prop = seg.prop === k ? null : k; rerender(); };
  // reading flow (pipeline/flow.py): body text or an insert placed into the text; a paragraph may CONTINUE the
  // previous one across an insert or a page break (joined in linear outputs). Automatic, overridable here.
  const flowRow = () => {
    if (blk.flow === 'furniture') return null;
    const cont = blk.relations?.continues_from, forced = blk.continues_override;
    // continuation applies to every TEXT segment, also inside a box or an excursus (it continues text of the same
    // box / excursus); only visual content (figures, tables, equations, captions) never continues a paragraph
    const textual = !['figure', 'chart', 'molecular_structure', 'reaction_scheme', 'lewis_structure', 'table', 'math', 'chemical_reaction', 'nuclear_reaction', 'caption', 'heading'].includes(blk.kind)
      && !(blk.kind === 'excerpt' && blk.chosen?.type !== 'text');
    const ctx = blk.relations?.continues_strand && blk.relations.continues_strand !== 'main' ? ' (μέσα στο ίδιο πλαίσιο/παρέκβαση)' : '';
    const val = [blk.flow === 'insert' ? 'παρεμβολή' : 'κείμενο', cont ? ` · ↳ συνεχίζει προηγούμενη παράγραφο${ctx}` : '',
      blk.flow_override || forced != null ? ' (δική σου επιλογή)' : ''].join('');
    const send = (action, extra, msg) => act(() => api(`${B(book.id)}/decide`, { body: { block_id: blk.id, action, ...extra } }), msg);
    return row('flow', 'Ροή', h('span', { class: 'prop-line' }, val), act1('flow', 'Αλλαγή ▾'), () => h('div', { class: 'col' },
      h('div', { class: 'small mut' }, 'Παρεμβολή = σχήμα/πίνακας/εξίσωση μέσα στο κείμενο. Συνέχεια = η παράγραφος συνεχίζει την προηγούμενη, που διακόπηκε από παρεμβολή ή αλλαγή σελίδας· στην εξαγωγή ενώνονται σε μία.'),
      h('div', { class: 'row small' }, 'Ρόλος:', [['main', 'Κείμενο'], ['insert', 'Παρεμβολή'], ['auto', 'Αυτόματα']].map(([k, l]) =>
        h('button', { class: 'small' + ((blk.flow_override || 'auto') === k ? ' primary' : ''), onclick: () => send('set_flow', { flow: k }, `Ροή: ${l}`) }, l))),
      textual ? h('div', { class: 'row small' }, 'Συνέχεια της προηγούμενης παραγράφου:', [[true, 'Ναι'], [false, 'Όχι'], ['auto', 'Αυτόματα']].map(([k, l]) =>
        h('button', { class: 'small' + ((forced == null ? 'auto' : forced) === k ? ' primary' : ''), onclick: () => send('set_continuation', { continues: k }, `Συνέχεια: ${l}`) }, l)))
        : h('div', { class: 'small mut' }, 'Συνέχεια παραγράφου: μόνο για κείμενο (όχι σχήματα, πίνακες, εξισώσεις, λεζάντες, τίτλους).'),
      textual ? h('div', { class: 'small mut' }, 'Η συνέχεια αναζητείται στο ίδιο πλαίσιο: κυρίως κείμενο με κυρίως κείμενο (και πάνω από παρεκβάσεις), κείμενο ενός πλαισίου/παρέκβασης με το ίδιο πλαίσιο/παρέκβαση.') : null));
  };
  // how a TABLE is read (pipeline/tables.py): a table, or a list of its cells by rows / by columns. A grid of terms
  // («Βασικοί όροι») is a list: automatic when the cells look like terms and independent evidence agrees
  const tableRow = () => {
    if (blk.kind !== 'table') return null;
    const hint = blk.table_list, over = blk.table_layout;
    const asText = blk.chosen?.type === 'text';
    const ORD = LAYOUT_EL;
    const eff = over || (hint && !hint.candidate ? hint.order : 'table');
    const listed = eff === 'rows' || eff === 'cols';
    const why = hint ? [hint.evidence.includes('alphabetical') ? `αλφαβητικά${hint.sorted != null ? ` (${Math.round(hint.sorted * 100)}%)` : ''}` : '',
      hint.evidence.some(e => e.startsWith('entity:')) ? 'κατηγορία «Βασικοί όροι»' : ''].filter(Boolean).join(' · ') : '';
    const val = !asText ? (hint?.candidate ? `εικόνα · προτείνεται ${ORD[hint.order]} (${hint.count} όροι) από ${hint.candidate}` : 'εικόνα')
      : `${listed ? listName(blk) : ORD[eff]}${over ? ' (δική σου επιλογή)' : hint ? ` · αυτόματα (${why})` : ''}`;
    const send = (layout, msg, key = null) => setTableLayout(blk, book, layout, msg, key);
    const ax = blk.table_axes || { rows: [], cols: [] };
    const cut = s => (s.length > 28 ? s.slice(0, 27) + '…' : s);
    const on = (layout, key) => over === layout && (blk.table_key ?? null) === key;
    // per row: which COLUMN names each item (its other columns become the item's keys); per column: which ROW
    const keyRow = (layout, title, what) => h('div', { class: 'row small wrap' }, h('b', {}, title), h('span', { class: 'mut' }, what),
      ax[layout].map((lab, i) => h('button', { class: 'small' + (on(layout, i) ? ' primary' : ''), title: lab, onclick: () => send(layout, `Ανάγνωση: ${LAYOUT_EL[layout]}, κύριο «${lab}»`, i) }, cut(lab))),
      h('button', { class: 'small ghost' + (on(layout, null) ? ' primary' : ''), title: 'Χωρίς επίπεδα: ένα στοιχείο ανά κελί (π.χ. «Βασικοί όροι»)', onclick: () => send(layout, `Ανάγνωση: ${LAYOUT_EL[layout]} (κελιά)`) }, 'ένα ανά κελί'));
    const opts = [['table', 'Πίνακας'], ['auto', 'Αυτόματα']];
    return row('tbl', 'Ανάγνωση', h('span', { class: 'prop-line' + (asText ? '' : ' mut') }, val),
      !asText && hint?.candidate ? h('button', { class: 'small primary prop-btn', title: `Χρήση της ανάγνωσης ${hint.candidate} ως ${ORD[hint.order]}`, onclick: () => send(hint.order, `Λίστα όρων από ${hint.candidate}`) }, 'Ως λίστα')
        : act1('tbl', 'Αλλαγή ▾'),
      () => h('div', { class: 'col' },
        h('div', { class: 'small mut' }, 'Λίστα ανά γραμμές: διάλεξε την κύρια στήλη — κάθε γραμμή γίνεται ένα στοιχείο με όνομα το κελί της σε αυτή τη στήλη, και οι άλλες στήλες γίνονται κλειδιά του (επικεφαλίδα: τιμή). Ανά στήλες: το ίδιο με κύρια γραμμή. «Ένα ανά κελί»: απλή λίστα των κελιών (π.χ. «Βασικοί όροι»· όρος που συνεχίζει σε εσοχή ενώνεται).'
          + (asText ? '' : ' Ο πίνακας είναι τώρα εικόνα: η λίστα χρησιμοποιεί την ανάγνωση της μηχανής.')),
        keyRow('rows', 'Ανά γραμμές', '· κύρια στήλη:'),
        keyRow('cols', 'Ανά στήλες', '· κύρια γραμμή:'),
        h('div', { class: 'row small' }, opts.map(([k, l]) => h('button', { class: 'small' + ((over || 'auto') === k ? ' primary' : ''), onclick: () => send(k, `Ανάγνωση: ${l}`) }, l)))));
  };
  const act1 = (k, label) => h('button', { class: 'small ghost prop-btn' + (seg.prop === k ? ' on' : ''), 'aria-expanded': String(seg.prop === k), onclick: () => toggle(k) }, seg.prop === k ? 'Κλείσιμο' : label);
  const row = (k, label, value, action, editor) => [h('div', { class: 'prop-row' }, h('span', { class: 'prop-k' }, label), h('span', { class: 'prop-v' }, value), h('span', { class: 'prop-a' }, action || '')),
    seg.prop === k && editor ? h('div', { class: 'prop-edit' }, editor()) : null];
  const grp = (S.book.groups || []).filter(g => (blk.groups || []).includes(g.id)).sort((a, b) => b.members.length - a.members.length)[0];
  const ents = (blk.entities || []).filter(e => !(e.family === 'insert' && e.source === 'detected' && !e.verified));
  const FIGURISH = ['figure', 'chart', 'molecular_structure', 'reaction_scheme', 'lewis_structure', 'table'];
  // text read as a paragraph/heading may really be a caption: offer the link when the page has something to caption
  // any text segment may be a caption (same rule as pipeline/overlay.py _associate_caption)
  const mayBeCaption = blk.kind !== 'caption' && !FIGURISH.includes(blk.kind) && !['math', 'chemical_reaction'].includes(blk.kind) && (S.page?.blocks || []).some(b => FIGURISH.includes(b.kind) || ['math', 'chemical_reaction'].includes(b.kind));
  const captionish = blk.kind === 'caption' || (blk.relations?.captions || []).length || FIGURISH.includes(blk.kind) || mayBeCaption;
  const owner = blk.relations?.caption_for && S.page.blocks.find(x => x.id === blk.relations.caption_for);
  const levelSel = ['heading', 'paragraph'].includes(blk.kind) ? (() => {
    const sel = h('select', { class: 'prop-select', 'aria-label': 'Επίπεδο στη δομή', onchange: () => decide('set_heading_level', { level: +sel.value }, 'Η δομή ενημερώθηκε') },
      h('option', { value: 0 }, 'Κείμενο'), [1, 2, 3, 4, 5, 6].map(n => h('option', { value: n }, `Τίτλος H${n}`)));
    sel.value = blk.kind === 'heading' ? String(blk.heading_level || 1) : '0'; return sel; })() : null;
  const out = h('input', { type: 'checkbox', role: 'switch', checked: !outOfOutput(blk) || null, 'aria-label': 'Στην έξοδο',
    onchange: () => act(() => api(`${B(book.id)}/output`, { body: { blocks: [blk.id], include: out.checked } }), out.checked ? 'Ξανά στην έξοδο' : 'Εκτός εξόδου') });
  return h('div', { class: 'props card' }, h('div', { class: 'prop-title' }, 'Ιδιότητες στο βιβλίο'),
    row('cat', 'Κατηγορία', ents.length ? ents.map(e => h('span', { class: `chip ent fam-${e.family || 'x'}${e.verified ? ' ent-ok' : ''}` }, entName(e.type))) : h('span', { class: 'mut' }, '—'),
      act1('cat', ents.length ? 'Αλλαγή ▾' : 'Ορισμός ▾'), () => segmentEntities(blk)),
    grp ? row('grp', 'Ομάδα', h('span', { class: 'chip grp' + (grp.source === 'auto' ? ' grp-auto' : ''), title: `${grp.name || grp.type} · ${grp.members.length} τμήματα · ${grp.source === 'auto' ? 'αυτόματη' : 'δική σου'}` },
      `▣ ${(grp.name || grp.type).slice(0, 22)} · ${grp.members.length}`),
      act1('grp', 'Επιλογές ▾'), () => h('div', {}, h('div', { class: 'small mut' }, `${grp.source === 'auto' ? 'Αυτόματη' : 'Δική σου'} ομάδα· μετακίνηση, σειρά και έξοδος εφαρμόζονται σε όλη.`),
        unwrap(groupsPanel(blk)))) : null,
    levelSel ? row('lvl', 'Δομή', h('span', { class: 'mut prop-line' }, blk.kind === 'heading' && blk.heading_level_source ? (SRC_EL[blk.heading_level_source] || blk.heading_level_source) : ''), levelSel) : null,
    captionish ? row('cap', 'Λεζάντα', h('span', { class: 'prop-line' + (mayBeCaption ? ' mut' : '') }, blk.kind === 'caption' ? (owner ? `ανήκει σε ${kindLabel(owner)}` : 'χωρίς σχήμα')
        : mayBeCaption ? 'όχι — μήπως είναι;' : `${(blk.relations?.captions || []).length} λεζάντα(ες)`),
      act1('cap', mayBeCaption ? 'Σύνδεση ▾' : 'Αλλαγή ▾'), () => unwrap(captionPanel(blk))) : null,
    flowRow(),
    tableRow(),
    blk.flow !== 'furniture' ? row('exc', 'Παρέκβαση', excursusValue(blk), act1('exc', 'Επιλογές ▾'), () => excursusEditor(blk)) : null,
    row('label', 'Ετικέτα μηχανής', h('span', { class: 'prop-line' }, (blk.engine_label || '—').replace(':', ' · ')), act1('label', 'Αλλαγή ▾'), () => labelStructure(blk)),
    h('div', { class: 'prop-row' }, h('span', { class: 'prop-k' }, 'Στην έξοδο'),
      h('span', { class: 'prop-v mut', title: (blk.omitted ? 'Δεν μπαίνει στο Markdown/PDF· μένει στο βιβλίο και στα JSON.' : 'Μπαίνει στο Markdown/PDF.') + (grp ? ' Ισχύει για όλη την ομάδα.' : '') },
        blk.omitted ? 'όχι · μένει στο βιβλίο' : 'ναι'),
      h('label', { class: 'prop-a switch', title: 'Εκτός Markdown/PDF· μένει στο βιβλίο, στον έλεγχο και στα JSON (O)' }, out, h('span', { class: 'slider' }))));
}
function menuItem(icon, label, kbd, run, title = '', danger = false) {
  return h('button', { class: 'menu-item' + (danger ? ' danger' : ''), role: 'menuitem', title, onclick: run },
    h('span', { class: 'mi-ic', 'aria-hidden': 'true' }, icon), h('span', { class: 'mi-label' }, label), kbd ? h('kbd', {}, kbd) : null);
}
export const unwrap = (det) => det && det.tagName === 'DETAILS' ? det.querySelector(':scope > div') : det;
export function toolStrip(tools, seg, rerender) {
  const open = tools.find(t => t.key === seg.tool);
  return h('div', { class: 'tools' },
    h('div', { class: 'tool-row' }, tools.map(t => h('button', { class: 'tool' + (open === t ? ' on' : ''), title: t.hint || '', 'aria-expanded': open === t ? 'true' : 'false',
      onclick: () => { seg.tool = open === t ? null : t.key; rerender(); } }, h('span', { class: 'ic' }, t.icon), h('span', {}, t.label), t.kbd ? h('kbd', {}, t.kbd) : null))),
    open ? h('div', { class: 'tool-panel' }, h('div', { class: 'tool-head' }, h('b', {}, open.label), open.hint ? h('span', { class: 'small mut' }, open.hint) : null,
      h('button', { class: 'icon', 'aria-label': 'Κλείσιμο', title: 'Κλείσιμο', onclick: () => { seg.tool = null; rerender(); } }, '×')), open.render()) : null);
}
export function detailTabs(tabs, seg, rerender) {        // vertical disclosure list: one row per detail, panel opens under its row
  return h('div', { class: 'details-list' }, h('div', { class: 'dl-head' }, 'Λεπτομέρειες'),
    tabs.map(t => {
      const on = seg.detail === t.key && !t.open;
      return [h('button', { class: 'dl-row' + (on ? ' on' : ''), 'aria-expanded': t.open ? null : String(on), title: t.open ? 'Ανοίγει σε παράθυρο' : '',
        onclick: () => { if (t.open) return t.open(); seg.detail = on ? null : t.key; rerender(); } },
        h('span', { class: 'dl-chev', 'aria-hidden': 'true' }, t.open ? '⧉' : on ? '▾' : '▸'), h('span', { class: 'grow' }, t.label),
        t.count != null ? h('span', { class: 'dl-count' }, String(t.count)) : null),
        on ? h('div', { class: 'dl-panel' }, t.render()) : null];
    }));
}
export function labelStructure(blk) {
  return h('div', {},
    h('div', { class: 'small mut' }, 'Η ετικέτα ορίζει το είδος του τμήματος και πώς ξαναγίνεται OCR. Επισημασμένες: όσες έδωσαν οι μηχανές. (Το επίπεδο στη δομή αλλάζει από τις Ιδιότητες.)'),
    labelPicker(currentLabels(blk), (l) => act(() => api(`${B(S.book.id)}/blocks/${blk.id}/relabel`, { body: { label: l } }), 'Νέα ετικέτα· τρέχει νέο OCR')));
}
