import { openPage } from '../ui/page_overlay.js';
import { B, api } from '../core/api.js';
import { $, clear, h } from '../core/dom.js';
import { S, go, kindLabel } from '../core/state.js';
import { DOCFIX_EL, infoTip } from '../core/strings.js';
import { dropdown, segText } from '../inspector/multi.js';
import { act, toast } from '../ui/feedback.js';
import { hoverSeg } from '../workspace/hover.js';
import { renderWorkspace, setSelection } from '../workspace/workspace.js';

// ---------------------------------------------------------------- structure suggestions (rules + model), page groups
// A highlight of segments on the page always belongs to the element that made it (a suggestion card). It never
// outlives it: when that card goes away (accepted, rejected, panel redrawn, page changed) the highlight is dropped, as
// it is on Escape or a click anywhere outside the card. So a stale highlight can never look like a real group.
// A highlight shown with the eye button is PINNED: leaving the card with the mouse keeps it (hover ones go).
let hlOwner = null, hlPinned = false;
export const highlight = (ids, owner = null, pin = false) => {
  S.hl = new Set(ids || []); hlOwner = S.hl.size ? owner : null; hlPinned = pin && S.hl.size > 0; renderWorkspaceBoxesOnly();
};
const pinnedBy = (card) => hlPinned && hlOwner === card;
export function dropStaleHighlight() {
  if (S.hl && S.hl.size && !(hlOwner && hlOwner.isConnected)) highlight([]);
}
// Esc that only clears a highlight stops here: the app's Escape (clear selection, drop a draft) must not also run.
// (Registered at import, i.e. before main.js adds the shortcut handler on the same target.)
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && S.hl && S.hl.size) { highlight([]); e.stopImmediatePropagation(); } });
document.addEventListener('mousedown', (e) => {
  if (S.hl && S.hl.size && !(hlOwner && hlOwner.isConnected && hlOwner.contains(e.target))) highlight([]);
}, true);
new MutationObserver(() => dropStaleHighlight()).observe(document.documentElement, { childList: true, subtree: true });
export function renderWorkspaceBoxesOnly() {      // cheap re-highlight without rebuilding: scan boxes, output view, page list
  document.querySelectorAll('.canvas .ws-box:not(.sugg):not(.draft), .pv-body [data-seg], .seglist [data-seg]')
    .forEach(el => el.classList.toggle('hl-member', S.hl.has(el.dataset.seg)));
}
// ---------------------------------------------------------------- previews of structure operations
// A crop of the page scan, drawn from the page image already loaded (no extra files).
export function regionThumb(bb, maxW = 120, maxH = 64) {
  const d = S.page.dims || [600, 800], w = Math.max(1, bb[2] - bb[0]), hh = Math.max(1, bb[3] - bb[1]);
  const sc = Math.min(maxW / w, maxH / hh);
  return h('span', { class: 'rthumb', style: { width: Math.round(w * sc) + 'px', height: Math.round(hh * sc) + 'px',
    backgroundImage: `url(${B(S.book.id)}/pages/${S.page.page}/image?w=1400)`, backgroundSize: `${Math.round(d[0] * sc)}px auto`,
    backgroundPosition: `${-Math.round(bb[0] * sc)}px ${-Math.round(bb[1] * sc)}px` } });
}
export const unionBox = (bbs) => [Math.min(...bbs.map(b => b[0])), Math.min(...bbs.map(b => b[1])), Math.max(...bbs.map(b => b[2])), Math.max(...bbs.map(b => b[3]))];
export const labelName = (lab) => lab ? ((S.meta.labels[lab.engine] || []).find(x => x.name === lab.name) || { el: lab.name }).el : 'Κείμενο';
export function showOnPage(ids, owner = null) {        // pin the members on the scan and bring the first into view
  highlight(ids, owner, true);
  const sel = `[data-seg="${CSS.escape(ids[0] || '')}"]`;
  const el = document.querySelector(`.canvas ${sel}`) || document.querySelector(`.pv-body ${sel}`), pane = el && el.closest('.canvas-wrap, .output-wrap');
  if (el && pane) pane.scrollTop += el.getBoundingClientRect().top - pane.getBoundingClientRect().top - 80;
}
export function membersBlock(ids) {
  const byId = Object.fromEntries(S.page.blocks.map(b => [b.id, b]));
  return h('ol', { class: 'op-members' }, ids.map((id, i) => {
    const b = byId[id];
    return h('li', { onmouseenter: () => hoverSeg(id, 'list'), onmouseleave: () => hoverSeg(null) },
      h('span', { class: 'ord' }, String(i + 1)), b ? regionThumb(b.bbox) : null,
      h('div', { class: 'grow' }, h('div', { class: 'small mut' }, b ? kindLabel(b) : 'τμήμα που δεν υπάρχει πια'), h('div', { class: 'small op-text' }, b ? segText(b) : id)));
  }));
}
export function opExplanation(op) {
  const n = op.members.length, lab = labelName(op.label);
  if (op.kind === 'merge_join' || op.op === 'merge') return ['Συγχώνευση σε ένα', `Τα ${n} τμήματα θα αντικατασταθούν από ΕΝΑ τμήμα «${lab}» με τα κείμενά τους ενωμένα με τη σειρά που φαίνεται. Τα αρχικά φεύγουν από την έξοδο αλλά μένουν στο ιστορικό· η «Διάσπαση» τα επαναφέρει.`];
  if (op.kind === 'figure_assemble') return ['Σύνθεση εικόνας', `Τα ${n} κομμάτια (ετικέτες τμημάτων, κενές γραμμές, εικόνες) πάνω από τη λεζάντα χωρίς εικόνα θα αντικατασταθούν από ΜΙΑ περιοχή-εικόνα· η λεζάντα θα συνδεθεί μαζί της. Τα αρχικά μένουν στο ιστορικό.`];
  if (op.kind === 'merge_reocr') return ['Ένωση περιοχών & νέο OCR', `Τα ${n} πλαίσια θα αντικατασταθούν από ΜΙΑ νέα περιοχή που τα καλύπτει όλα και θα ξαναδιαβαστεί ως «${lab}». Το νέο κείμενο θα φανεί μόλις τελειώσει η επεξεργασία (λίγα δευτερόλεπτα).`];
  const gt = S.meta.group_types[op.type] || op.type || 'ομάδα';
  return ['Ομαδοποίηση (χωρίς αλλαγή τμημάτων)', `Τα ${n} τμήματα θα σημειωθούν ως «${gt}${op.name ? ' · ' + op.name : ''}». Δεν αλλάζει τίποτα σε αυτά· στην έξοδο θα εμφανιστούν μαζί, μέσα σε πλαίσιο.`];
}
export function opResult(op) {
  const byId = Object.fromEntries(S.page.blocks.map(b => [b.id, b]));
  const box = h('div', { class: 'op-result' }, h('div', { class: 'small mut' }, 'Αποτέλεσμα (προεπισκόπηση)'));
  if (op.kind === 'merge_join' || op.op === 'merge') {
    const out = h('div', { class: 'pv-text op-preview' }, h('span', { class: 'mut' }, 'Υπολογισμός…'));
    box.append(out);
    api(`${B(S.book.id)}/merge/preview`, { body: { blocks: op.members, label: op.label } }).then(r => {
      clear(out);
      if (r.html) { out.innerHTML = r.html; renderMath(out); }          // rendered by the server, exactly as exported
      else out.append(h('span', { class: 'badge-bad' }, 'Κάποιο τμήμα δεν έχει κείμενο: το αποτέλεσμα θα είναι εικόνα της ένωσης.'));
    }).catch(e => { clear(out).append(h('span', { class: 'badge-bad' }, e.message)); });
  } else if (op.kind === 'merge_reocr' || op.kind === 'figure_assemble') {
    const bbs = op.bbox ? [op.bbox] : op.members.map(id => byId[id]?.bbox).filter(Boolean);
    if (bbs.length) box.append(h('div', {}, regionThumb(unionBox(bbs), 360, 160)), h('div', { class: 'small mut' }, op.kind === 'figure_assemble' ? 'η περιοχή που θα γίνει μία εικόνα' : 'η περιοχή που θα διαβαστεί ξανά ως ένα τμήμα'));
  } else {
    box.append(h('div', { class: 'pv-box op-preview' }, op.members.map(id => byId[id] ? h('div', { class: 'small' }, segText(byId[id]).slice(0, 80)) : null)));
  }
  return box;
}
export function editInSelection(ids) {
  setSelection(ids.filter(m => S.page.blocks.some(x => x.id === m)), { open: true });
  S.leftView = 'scan'; renderWorkspace();
  toast('Τα μέλη είναι στην επιλογή: άλλαξε σειρά/μέλη και πάτησε Συγχώνευση ή Ομαδοποίηση');
}
// One action bar for every suggestion/proposal card: positive actions on the left (primary filled → secondary
// outlined); dismiss (text, red on hover) and a view-only helper icon on the right, apart from the primary action.
// "See it on the page": one button everywhere — an eye, on a tinted background so it reads as a control
const EYE = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1.5 12S5.5 4.5 12 4.5 22.5 12 22.5 12 18.5 19.5 12 19.5 1.5 12 1.5 12z"/><circle cx="12" cy="12" r="3.2"/></svg>';
export function viewButton(onclick, title = 'Εμφάνιση στη σελίδα') {
  const b = h('button', { class: 'small view-btn', title, 'aria-label': title, onclick });
  b.innerHTML = EYE;                                  // constant markup, no data
  return b;
}
export function cardActions(actions, helper) {
  const list = actions.filter(Boolean);
  const dismiss = list.filter(b => b.classList && b.classList.contains('dismiss'));   // away from the primary action
  return h('div', { class: 'card-actions' }, h('div', { class: 'ca-main' }, list.filter(b => !dismiss.includes(b))),
    h('div', { class: 'ca-help' }, dismiss, helper || null));
}
export function suggestionCard(op, actions) {
  const [title, what] = opExplanation(op);
  const card = h('div', { class: 'cand op-card', onmouseenter: () => { if (!pinnedBy(card)) highlight(op.members, card); }, onmouseleave: () => { if (!pinnedBy(card)) highlight([]); } },
    h('div', { class: 'row' }, h('b', { class: 'grow' }, title),
      h('span', { class: 'chip' }, op.source === 'model' ? 'μοντέλο' : op.source === 'auto' ? 'αυτόματη' : 'κανόνας')),
    h('div', { class: 'small op-what' }, what),
    op.reason ? h('div', { class: 'small mut' }, h('b', {}, 'Γιατί: '), op.reason) : null,
    h('div', { class: 'small', style: { marginTop: '6px' } }, h('b', {}, 'Τμήματα'), op.kind !== 'group' ? ' (με αυτή τη σειρά)' : ''),
    membersBlock(op.members), opResult(op),
    op.errors && op.errors.length ? h('div', { class: 'badge-bad' }, '✗ ' + op.errors.join(' · ')) : null,
    cardActions(actions, viewButton(() => showOnPage(op.members, card), 'Εμφάνιση των τμημάτων στη σελίδα')));
  return card;
}
export function suggestionsPanel(pg) {
  const book = S.book, rule = pg.info.suggestions || [];
  const modelBox = h('div');
  const showModel = (res) => {
    clear(modelBox).append(h('div', { class: 'said small' }, h('b', {}, 'Το μοντέλο: '), res.understanding || '—'));
    if (!res.operations.length) modelBox.append(h('div', { class: 'small mut' }, 'Καμία πρόταση για αυτή τη σελίδα.'));
    res.operations.forEach((op, i) => modelBox.append(suggestionCard(op, [
      h('button', { class: 'small primary', disabled: !op.valid, onclick: async () => { await act(() => api(`${B(book.id)}/structure/apply`, { body: op }), 'Εφαρμόστηκε'); res.operations.splice(i, 1); showModel(res); } }, 'Αποδοχή'),
      h('button', { class: 'small', onclick: () => editInSelection(op.members), title: 'Αλλαγή μελών και σειράς πριν την εφαρμογή' }, 'Επεξεργασία'),
      h('button', { class: 'small dismiss', onclick: () => { res.operations.splice(i, 1); showModel(res); } }, 'Απόρριψη')])));
  };
  const ask = async () => { clear(modelBox).append(h('div', { class: 'row small mut' }, h('span', { class: 'spin' }), `Το ${S.meta.note_model} εξετάζει τη σελίδα…`));
    try { showModel(await api(`${B(book.id)}/pages/${pg.page}/structure/propose`, { body: {} })); } catch (e) { clear(modelBox).append(h('div', { class: 'badge-bad' }, e.message)); } };
  // nothing to show: no section at all (the model can still be asked from the page summary line)
  pg.askModel = ask;
  if (!rule.length) return h('div', {}, modelBox);
  return h('details', { open: true }, h('summary', { title: 'Συγχωνεύσεις και ομάδες που προτείνουν οι κανόνες. Το ποντίκι πάνω σε μια πρόταση δείχνει τα μέλη της στη σελίδα.' }, `Προτάσεις δομής (${rule.length})`), h('div', {},
    rule.map(op => suggestionCard(op, [
      h('button', { class: 'small primary', onclick: () => act(() => api(`${B(book.id)}/pages/${pg.page}/suggestions/${op.key}/accept`, { body: {} }), 'Εφαρμόστηκε') }, 'Αποδοχή'),
      h('button', { class: 'small', onclick: () => editInSelection(op.members), title: 'Φόρτωσε τα μέλη στην επιλογή για αλλαγή σειράς ή μελών πριν τη συγχώνευση' }, 'Επεξεργασία'),
      h('button', { class: 'small dismiss', onclick: () => act(() => api(`${B(book.id)}/suggestions/${op.key}/dismiss`, { body: {} }), 'Η πρόταση απορρίφθηκε') }, 'Απόρριψη')])),
    rule.length > 1 ? h('button', { class: 'small', onclick: () => act(() => api(`${B(book.id)}/suggestions/accept-all`, { body: { page: pg.page } }), 'Εφαρμόστηκαν όλες οι προτάσεις της σελίδας') }, 'Αποδοχή όλων της σελίδας') : null,
    modelBox));
}
// Book-level: the first, rule-based pass over the whole document (pipeline.document + pipeline.furniture). It reads
// many pages at once, so it lives in the Structure screen, not in a page.
// neither the original nor the correction is right: set the level yourself (an ordinary reviewer decision)
// the extent is wrong: choose the group's last segment (shorter or longer); the group becomes the reviewer's own
const groupEnd = (f) => {
  const sel = h('select', { class: 'act-select', title: 'Πού τελειώνει πραγματικά η οντότητα;',
    onchange: () => sel.value && act(() => api(`${B(S.book.id)}/groups/${f.group}/end`, { body: { last: sel.value } }), 'Η έκταση της ομάδας διορθώθηκε') },
  h('option', { value: '' }, 'Σωστό τέλος'), (f.extent_options || []).map(o => h('option', { value: o.id }, `${o.member ? '● ' : '○ '}${o.text}`)));
  return sel;
};
export const setLevel = (id, current) => {
  const sel = h('select', { class: 'act-select', title: 'Ούτε το «πριν» ούτε το «μετά»; όρισε το σωστό επίπεδο',
    onchange: () => sel.value !== '' && act(() => api(`${B(S.book.id)}/decide`, { body: { block_id: id, action: 'set_heading_level', level: +sel.value } }), 'Το επίπεδο ορίστηκε') },
  h('option', { value: '' }, 'Σωστό επίπεδο'), h('option', { value: 0 }, 'Όχι τίτλος'), [1, 2, 3, 4, 5, 6].map(n => h('option', { value: n }, `Επίπεδο ${n}`)));
  return sel;
};
export function docStructureCard(card) {
  const book = S.book, ds = book.document_structure || { fixes: [], issues: [] };
  const fixes = ds.fixes.filter(f => f.kind !== 'furniture'), furniture = ds.fixes.filter(f => f.kind === 'furniture');
  const open = (page, id) => openPage(page, id);
  const undo = (f) => act(() => api(`${B(book.id)}/suggestions/${f.key}/dismiss`, { body: {} }), 'Η διόρθωση αναιρέθηκε');
  card.append(h('div', { class: 'row' }, h('h2', { class: 'grow', style: { margin: 0 } }, '1ο πέρασμα · αυτόματες διορθώσεις με κανόνες ', infoTip('docstructure'))),
    h('p', { class: 'small mut' }, `Διαβάζει όλες τις σελίδες μαζί μετά την επεξεργασία: ${ds.issues.length} ενδείξεις για έλεγχο · ${fixes.length} διορθώσεις δομής · ${furniture.length} στοιχεία σελίδας εξαιρέθηκαν.`));
  if (ds.issues.length) card.append(h('h3', {}, 'Για έλεγχο'), ds.issues.map(i => h('div', { class: 'cand' },
    h('div', { class: 'row' }, h('span', { class: 'chip warn' }, 'έλεγχος'), h('span', { class: 'grow small' }, i.explanation_el), h('span', { class: 'small mut' }, `σελ. ${i.page}`)),
    h('div', { class: 'actions' }, h('button', { class: 'small', onclick: () => open(i.page, i.block) }, 'Άνοιγμα στη σελίδα')))));
  const byKind = {};
  fixes.forEach(f => (byKind[f.kind] = byKind[f.kind] || []).push(f));
  for (const [kind, list] of Object.entries(byKind)) card.append(h('details', {}, h('summary', {}, `${DOCFIX_EL[kind] || kind} (${list.length})`),
    h('div', {}, list.map(f => h('div', { class: 'cand' },
      h('div', { class: 'row' }, h('b', { class: 'grow small link-like', title: 'Προεπισκόπηση στη σελίδα (παράθυρο)', onclick: () => open(f.pages[0], f.blocks[f.blocks.length - 1]) }, f.subject ? `«${f.subject}»` : f.explanation_el), h('span', { class: 'chip' }, 'αυτόματο'), f.ambiguous ? h('span', { class: 'chip warn', title: 'Το τέλος βγήκε από ασθενή ένδειξη (κενό/εσοχή): έλεγξέ το ή ρώτα το μοντέλο' }, 'αμφίβολο τέλος') : null, h('span', { class: 'small mut' }, `σελ. ${f.pages.join('–')}`)),
      f.before_el || f.after_el ? h('div', { class: 'fix-change' }, h('span', { class: 'fix-before' }, f.before_el || '—'), h('span', { class: 'fix-arrow' }, ' → '), h('span', { class: 'fix-after' }, f.after_el || '—')) : null,
      f.subject ? h('div', { class: 'small' }, f.explanation_el) : null,
      h('div', { class: 'small mut' }, `κανόνας: ${f.rule}`),
      cardActions([
        ['heading_style', 'level_jump', 'box_heading', 'level_shift'].includes(f.kind) ? setLevel(f.blocks[f.blocks.length - 1], f.after) : null,
        f.kind === 'auto_group' ? groupEnd(f) : null,
        h('button', { class: 'small dismiss', title: 'Επιστροφή στο «πριν»', onclick: () => undo(f) }, 'Αναίρεση')],
        viewButton(() => open(f.pages[0], f.blocks[f.blocks.length - 1]), 'Άνοιγμα στη σελίδα (παράθυρο)')))))));
  if (furniture.length) {
    const pages = [...new Set(furniture.map(f => f.pages[0]))].sort((a, b) => a - b);
    card.append(h('details', {}, h('summary', {}, `${DOCFIX_EL.furniture} — εκτός εξόδου (${furniture.length}) `, infoTip('furniture')),
      h('div', { class: 'small mut', style: { margin: '6px 0' } }, 'Ανά σελίδα. Η «Επαναφορά» γίνεται και από τη λίστα της σελίδας.'),
      h('div', {}, pages.map(p => h('div', { class: 'list-item', onclick: () => open(p) },
        h('span', { class: 'small mut', style: { width: '56px' } }, `σελ. ${p}`),
        h('span', { class: 'txt grow small' }, furniture.filter(f => f.pages[0] === p).map(f => f.explanation_el.split(':')[0]).join(' · ')))))));
  }
  if (!ds.issues.length && !ds.fixes.length) card.append(h('div', { class: 'list-item mut' }, 'Καμία διόρθωση ή ένδειξη.'));
}
export function pageGroupsPanel(pg) {
  const ids = new Set(pg.blocks.map(b => b.id));
  const gs = (S.book.groups || []).filter(g => g.members.some(m => ids.has(m)));
  if (!gs.length) return h('span');
  return h('details', { open: true }, h('summary', {}, `Ομάδες σελίδας (${gs.length})`), h('div', {}, gs.map(g => {
    const op = { kind: 'group', type: g.type, name: g.name, members: g.members.filter(m => ids.has(m)), source: g.source === 'auto' ? 'auto' : 'human',
                 reason: g.source === 'auto' ? `κανόνας: ${g.rule || ''}` : null };
    const actions = g.source === 'auto' ? [
      h('button', { class: 'small primary', title: 'Διάλεξε είδος· η ομάδα γίνεται δική σου (δεν αλλάζει πια αυτόματα)', onclick: (e) => { const t = e.currentTarget; dropdown(t, [{ title: 'Είδος ομάδας', items: Object.entries(S.meta.group_types).map(([k, v]) => ({ label: v, hint: k, value: k })) }],
        (it) => act(() => api(`${B(S.book.id)}/auto-groups/${g.key}/keep`, { body: { type: it.value } }), 'Η ομάδα έγινε δική σου')); } }, 'Επιβεβαίωση ▾'),
      h('button', { class: 'small', onclick: () => editInSelection(g.members.filter(m => ids.has(m))), title: 'Φόρτωσε τα μέλη στην επιλογή για αλλαγή μελών ή σειράς' }, 'Επεξεργασία'),
      h('button', { class: 'small dismiss', title: 'Η αυτόματη ομάδα αφαιρείται (αναιρείται)', onclick: () => act(() => api(`${B(S.book.id)}/suggestions/${g.key}/dismiss`, { body: {} }), 'Η αυτόματη ομάδα αφαιρέθηκε') }, 'Δεν είναι ομάδα')]
      : [h('button', { class: 'small', onclick: () => editInSelection(g.members.filter(m => ids.has(m))) }, 'Επεξεργασία'),
         h('button', { class: 'small dismiss', onclick: () => act(() => api(`${B(S.book.id)}/groups/${g.id}/remove`, { body: {} }), 'Η ομάδα διαλύθηκε') }, 'Διάλυση ομάδας')];
    const card = suggestionCard(op, actions);
    card.querySelector('.op-what').textContent = g.source === 'auto'
      ? `Αυτόματη ομάδα «${S.meta.group_types[g.type] || g.type}${g.name ? ' · ' + g.name : ''}» με ${op.members.length} τμήματα. Τα τμήματα δεν αλλάζουν· στην έξοδο εμφανίζονται μαζί, μέσα σε πλαίσιο.`
      : `Δική σου ομάδα «${S.meta.group_types[g.type] || g.type}${g.name ? ' · ' + g.name : ''}» με ${op.members.length} τμήματα σε αυτή τη σελίδα.`;
    return card;
  })));
}
