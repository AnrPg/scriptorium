import { cardActions, viewButton } from '../inspector/suggestions.js';
import { openPage } from '../ui/page_overlay.js';
import { entName } from '../book/entities.js';
import { B, api } from '../core/api.js';
import { clear, h, keepScroll } from '../core/dom.js';
import { S, go } from '../core/state.js';
import { KIND_EL, SRC_EL, infoTip } from '../core/strings.js';
import { setLevel } from '../inspector/suggestions.js';
import { act } from '../ui/feedback.js';

// ---------------------------------------------------------------- hierarchy: hints -> checked levels -> proposals
// Typography, running heads, chapter labels and outlines are HINTS (pipeline.sections). A hint applies on its own
// only when it passes a check (two independent kinds of evidence agree, or its source is calibrated on the
// reviewer's decisions); otherwise it is a proposal here. Accept = set_heading_level for the group (one undo);
// reject = dismissal, which also counts against the hint source in the calibration.
const EVID_EL = { style_of_checked: 'ίδιο στυλ με ελεγμένο τίτλο', human_style: 'ίδιο στυλ με τίτλο σου', style_of_numbered: 'στυλ αριθμημένων', typography: 'μέγεθος/στυλ γραμμάτων',
  running_head: 'κεφαλίδες σελίδων', chapter_label: 'ετικέτα κεφαλαίου', outline: 'λίστα περιεχομένων', engine_rank: 'ετικέτα Marker', model: 'τοπικό μοντέλο' };
const pct = (x) => `${Math.round(100 * x)}%`;
const levelSelect = (id, level) => {
  const sel = h('select', { style: { width: 'auto' }, onclick: e => e.stopPropagation(),
    onchange: () => act(() => api(`${B(S.book.id)}/decide`, { body: { block_id: id, action: 'set_heading_level', level: +sel.value } }), 'Η δομή ενημερώθηκε') },
  h('option', { value: 0 }, 'Όχι επικεφαλίδα'), Array.from({ length: Math.max(6, level || 1) }, (_, i) => i + 1).map(n => h('option', { value: n }, `Επίπεδο ${n}`)));
  sel.value = String(level || 1);
  return sel;
};

export async function headingReviewCard(card) {
  const r = await api(`${B(S.book.id)}/heading-review`);
  clear(card).append(h('div', { class: 'row' }, h('h2', { class: 'grow', style: { margin: 0 } }, 'Επίπεδα τίτλων · ενδείξεις ', infoTip('headinghints'))),
    h('p', { class: 'small mut' }, r.proposals.length ? `${r.proposals.length} προτάσεις: ενδείξεις που δεν πέρασαν τον έλεγχο μόνες τους. Αποδοχή = επίπεδο για όλη την ομάδα (μία αναίρεση).`
      : 'Καμία ανοιχτή πρόταση: ό,τι εφαρμόστηκε αυτόματα πέρασε τον έλεγχο (συμφωνία ανεξάρτητων ενδείξεων ή βαθμονομημένη πηγή).'));
  for (const p of r.proposals) {
    const sel = new Set(p.members);
    card.append(h('div', { class: 'cand' },
      h('div', { class: 'row' }, h('b', { class: 'grow' }, `Επίπεδο ${p.level} για ${p.items.length} τίτλο(υς)`), h('span', { class: 'small mut' }, 'από: ' + p.sources.map(s => EVID_EL[s] || s).join(' + '))),
      p.items.map(it => h('label', { class: 'row small', style: { margin: '2px 0' } },
        (() => { const c = h('input', { type: 'checkbox' }); c.checked = true; c.onchange = () => c.checked ? sel.add(it.id) : sel.delete(it.id); return c; })(),
        h('span', { class: 'grow link-like', title: 'Προεπισκόπηση στη σελίδα (παράθυρο)', onclick: (e) => { e.preventDefault(); openPage(it.page, it.id); } }, it.title), h('span', { class: 'mut' }, `σελ. ${it.page} · τώρα ${it.current ?? '—'} (${SRC_EL[it.source] || it.source})`),
        h('button', { class: 'small ghost', onclick: (e) => { e.preventDefault(); openPage(it.page, it.id); } }, 'Άνοιγμα'))),
      h('div', { class: 'actions' },
        h('button', { class: 'small primary', onclick: () => act(() => api(`${B(S.book.id)}/heading-review/${p.key}/accept`, { body: { members: [...sel] } }), 'Τα επίπεδα ενημερώθηκαν') }, 'Αποδοχή επιλεγμένων'),
        h('button', { class: 'small dismiss', onclick: () => act(() => api(`${B(S.book.id)}/suggestions/${p.key}/dismiss`, { body: {} }), 'Η πρόταση απορρίφθηκε') }, 'Απόρριψη'),
        p.items.length === 1 ? setLevel(p.items[0].id) : null)));
  }
  const stats = Object.entries(r.stats || {}), styles = Object.entries(r.styles || {});
  const det = h('details', {}, h('summary', {}, 'Βαθμονόμηση & στατιστικά στυλ ', infoTip('headingstats')));
  if (stats.length) det.append(h('table', { class: 'stats' }, h('tr', {}, ['Ένδειξη', 'Συγκρίσεις με εσένα', 'Λάθη', 'Άνω όριο σφάλματος', ''].map(x => h('th', {}, x))),
    stats.map(([s, a]) => h('tr', {}, h('td', {}, EVID_EL[s] || s), h('td', {}, a.n), h('td', {}, a.wrong), h('td', {}, pct(a.error_upper)),
      h('td', {}, a.trusted ? h('span', { class: 'chip ok' }, 'βαθμονομημένη · εφαρμόζεται μόνη') : h('span', { class: 'small mut' }, 'χρειάζεται συμφωνία με άλλη ένδειξη'))))));
  else det.append(h('div', { class: 'small mut' }, 'Δεν έχεις ορίσει ακόμα επίπεδα: η βαθμονόμηση ξεκινά από τις αποφάσεις σου.'));
  if (styles.length) det.append(h('h3', {}, 'Στυλ τίτλων (μετρημένο μέγεθος γραμμάτων)'), h('table', { class: 'stats' },
    h('tr', {}, ['Στυλ', 'Τίτλοι', 'Μέγεθος', 'Διαχωρισμός από γειτονικό', ''].map(x => h('th', {}, x))),
    styles.map(([k, v]) => h('tr', {}, h('td', {}, `#${+k + 1}`), h('td', {}, v.members), h('td', {}, v.size.join('–')), h('td', {}, pct(v.separation)),
      h('td', {}, v.distinct ? h('span', { class: 'chip ok' }, 'διακριτό') : h('span', { class: 'chip warn' }, 'θολό · δεν χρησιμοποιείται'))))));
  card.append(det);
}

export async function outlineCard(card) {
  const r = await api(`${B(S.book.id)}/outline`);
  let onlyHeadings = false;
  const collapsed = new Set();
  const tree = h('div', { class: 'outline' });
  const paint = () => keepScroll(document.getElementById('view'), () => {
    clear(tree);
    let hideBelow = null;
    for (const n of r.nodes) {
      if (hideBelow !== null) { if (n.level && n.depth <= hideBelow) hideBelow = null; else continue; }
      if (onlyHeadings && !n.level) continue;
      const isHead = !!n.level;
      if (isHead && collapsed.has(n.id)) hideBelow = n.depth;
      const kind = isHead ? `H${n.level}` : (KIND_EL[n.kind] || n.kind);
      const ents = (n.entities || []).filter(e => e.family !== 'insert').map(e => h('span', { class: `chip ent fam-${e.family || 'x'}${e.verified ? ' ent-ok' : ''}` }, entName(e.type)));
      tree.append(h('div', { class: 'list-item ol-row' + (isHead ? ' ol-head' : '') + (n.in_output ? '' : ' ol-out') + (n.group ? ' in-group' : ''), style: { paddingLeft: (8 + n.depth * 20) + 'px' },
        onclick: () => openPage(n.page, n.id), title: n.evidence ? 'επίπεδο από: ' + n.evidence.map(x => EVID_EL[x] || x).join(' + ') : '' },
        isHead ? h('button', { class: 'ghost small ol-caret', onclick: (e) => { e.stopPropagation(); collapsed.has(n.id) ? collapsed.delete(n.id) : collapsed.add(n.id); paint(); } }, collapsed.has(n.id) ? '▸' : '▾') : h('span', { class: 'ol-caret' }),
        h('span', { class: 'chip lvl' + (isHead ? '' : ' lv-ins') }, kind),
        n.group ? h('span', { class: 'chip grp' + (n.group.auto ? ' grp-auto' : ''), title: `${n.group.auto ? 'Αυτόματη' : 'Δική σου'} ομάδα · ${n.group.size} τμήματα` }, n.group.first ? `▣ ${n.group.name.slice(0, 24)} (${n.group.size})` : '│') : null,
        h('span', { class: 'grow txt' }, n.text || `[${KIND_EL[n.kind] || n.kind}]`), ents,
        n.in_output ? null : h('span', { class: 'chip warn', title: 'δεν μπαίνει στην έξοδο' }, n.why_not === 'page_number' ? 'αρ. σελίδας' : n.why_not === 'omitted' ? 'εκτός εξόδου (εσύ)' : 'κεφαλίδα/υποσέλιδο'),
        h('span', { class: 'small mut' }, `σελ. ${n.page}`),
        isHead ? h('span', { class: 'small mut' }, SRC_EL[n.level_source] || n.level_source) : null,
        isHead ? levelSelect(n.id, n.level) : null));
    }
    if (r.hidden.length && !onlyHeadings) tree.append(h('details', {}, h('summary', { class: 'small mut' }, `Κρυμμένα από εσένα (${r.hidden.length}) — εκτός εξόδου, μένουν μόνο ως ένδειξη`),
      r.hidden.map(x => h('div', { class: 'list-item small ol-out', onclick: () => openPage(x.page) }, h('span', { class: 'grow' }, x.text || `[${KIND_EL[x.kind] || x.kind}]`), h('span', { class: 'mut' }, `σελ. ${x.page}`)))));
  });
  const all = h('button', { class: 'small on' }, 'Όλα'), heads = h('button', { class: 'small' }, 'Μόνο τίτλοι');
  all.onclick = () => { onlyHeadings = false; all.classList.add('on'); heads.classList.remove('on'); paint(); };
  heads.onclick = () => { onlyHeadings = true; heads.classList.add('on'); all.classList.remove('on'); paint(); };
  clear(card).append(h('div', { class: 'row' }, h('h2', { class: 'grow', style: { margin: 0 } }, 'Πλήρης ιεραρχία του βιβλίου ', infoTip('outline')),
    h('div', { class: 'filters' }, all, heads)),
    h('p', { class: 'small mut' }, `${r.sections} τίτλοι · ${r.nodes.length} τμήματα. Όλο το βιβλίο όπως θα δομηθεί στην έξοδο (SECTION / section_path για chunking/RAG): κάθε τμήμα κάτω από την ενότητά του, λεζάντες κάτω από την εικόνα τους· ▾ κλείνει μια ενότητα.`),
    tree);
  paint();
}

// ---------------------------------------------------------------- model second pass: where automatic groups end
export async function extentReviewPanel(box) {
  const book = S.book;
  const r = await api(`${B(book.id)}/extent-review`);
  const running = (S.jobs || []).some(j => j.book === book.id && j.kind === 'extents' && ['running', 'queued'].includes(j.state));
  clear(box).append(h('div', { class: 'row' }, h('h2', { class: 'grow', style: { margin: 0 } }, 'Έκταση ομάδων · προτάσεις μοντέλου ', infoTip('extentllm')),
    h('button', { class: 'primary', disabled: running || !S.meta.note_model, onclick: async () => { const j = await act(() => api(`${B(book.id)}/extent-review`, { body: {} }), 'Ο έλεγχος έκτασης μπήκε στην ουρά', false); if (j && j.id) { S.jobs.push(j); extentReviewPanel(box); } } },
      running ? 'Τρέχει…' : (r.status ? 'Νέος έλεγχος έκτασης' : 'Έλεγχος αμφίβολων τελών με το μοντέλο'))));
  if (r.status === 'UNAVAILABLE') { box.append(h('p', { class: 'badge-bad' }, 'Δεν υπάρχει τοπικό μοντέλο: ' + (r.error || ''))); return; }
  if (!r.status) { box.append(h('p', { class: 'small mut' }, 'Δεν έχει τρέξει ακόμα. Ρωτά μόνο για αυτόματες ομάδες με αμφίβολο τέλος· κάθε πρόταση την αποφασίζεις εσύ.')); return; }
  box.append(h('p', { class: 'small mut' }, `${r.model} · ρωτήθηκε για ${r.asked}, συμφώνησε σε ${r.agreed_with_current}` + (r.failed?.length ? ` · ${r.failed.length} απέτυχαν` : '')));
  if (!r.proposals.length) { box.append(h('div', { class: 'list-item mut' }, 'Καμία ανοιχτή πρόταση.')); return; }
  for (const p of r.proposals) {
    box.append(h('div', { class: 'cand' },
      h('div', { class: 'row' }, h('b', { class: 'grow link-like', onclick: () => openPage(p.page, p.block_id) }, p.name || 'ομάδα'),
        h('span', { class: 'chip' + (p.votes === p.samples ? '' : ' warn') }, `${p.votes}/${p.samples} συμφωνία`)),
      p.added.length ? h('div', { class: 'fix-change' }, h('span', { class: 'fix-after' }, '+ ' + p.added.join(' · '))) : null,
      p.removed.length ? h('div', { class: 'fix-change' }, h('span', { class: 'fix-before' }, '− ' + p.removed.join(' · '))) : null,
      h('div', { class: 'small' }, p.reasons.find(Boolean) || ''),
      cardActions([
        h('button', { class: 'small primary', onclick: () => act(() => api(`${B(book.id)}/extent-review/${p.key}/accept`, { body: {} }), 'Η έκταση διορθώθηκε· το μοντέλο ξανατρέχει') }, 'Αποδοχή'),
        h('button', { class: 'small dismiss', onclick: () => act(() => api(`${B(book.id)}/suggestions/${p.key}/dismiss`, { body: {} }), 'Η πρόταση απορρίφθηκε') }, 'Απόρριψη')],
        viewButton(() => openPage(p.page, p.block_id), 'Άνοιγμα στη σελίδα (παράθυρο)'))));
  }
}
