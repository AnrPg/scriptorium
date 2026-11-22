import { renderBook } from '../book/book.js';
import { ENT, entName, famName, famOf } from '../book/entities.js';
import { B, api } from '../core/api.js';
import { refresh } from '../core/chrome.js';
import { $, clear, h } from '../core/dom.js';
import { R, S, go } from '../core/state.js';
import { EXPORT_EL, KIND_EL, STATE_EL, infoTip } from '../core/strings.js';
import { act, modal, openDialog, toast } from '../ui/feedback.js';

// ---------------------------------------------------------------- export wizard
// Steps: 1 format · 2 content (exclusions) · 3 move to the end of each unit · 4 separators, preview, download.
// Steps 2–3 apply to reader-facing formats (Markdown, PDF) only. Choices are remembered per book.
export const READER_FORMATS = new Set(['markdown_images', 'markdown_text', 'pdf']);
export const WIZ_STEPS = [['format', 'Μορφή'], ['content', 'Περιεχόμενο'], ['move', 'Μετακίνηση στο τέλος'], ['finish', 'Έλεγχος & λήψη']];
export function exportDialog() {
  const book = S.book, KEY = 'ocr.export.' + book.id;
  const saved = (() => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } })();
  const st = { fmt: saved.fmt || 'markdown_images', exclude: new Set(saved.exclude || []), move: new Set(saved.move || []),
    images: saved.images || 'keep', unit_level: saved.unit_level || 1,
    style: (S.meta.styles || []).includes(saved.style) ? saved.style : 'tags', step: 0, entities: null };
  const save = () => localStorage.setItem(KEY, JSON.stringify({ fmt: st.fmt, exclude: [...st.exclude], move: [...st.move], images: st.images, unit_level: st.unit_level, style: st.style }));
  const qs = () => `style=${st.style}&unit_level=${st.unit_level}&fig_images=${st.images}&exclude=${encodeURIComponent([...st.exclude].join(','))}&move=${encodeURIComponent([...st.move].join(','))}`;
  const steps = () => READER_FORMATS.has(st.fmt) ? WIZ_STEPS : [WIZ_STEPS[0], WIZ_STEPS[3]];
  const d = openDialog('wide');
  const head = h('div', { class: 'wiz-steps' }), body = h('div', { class: 'wiz-body' }), foot = h('div', { class: 'row end wiz-foot' });
  d.append(h('h2', {}, 'Εξαγωγή'), head, body, foot);
  const byType = () => { const m = {}; (st.entities || []).forEach(e => (m[e.type] = m[e.type] || []).push(e)); return m; };
  let renderToken = 0;          // a newer renderStep supersedes one still awaiting the entities: the stale one must not append
  const renderStep = async () => {
    const my = ++renderToken;
    save();
    const list = steps(), [key] = list[st.step];
    clear(head).append(...list.map(([k, l], i) => h('button', { class: 'wiz-step' + (i === st.step ? ' on' : i < st.step ? ' done' : ''),
      onclick: () => { st.step = i; renderStep(); } }, h('span', { class: 'n' }, String(i + 1)), l)));
    clear(body);
    if ((key === 'content' || key === 'move' || key === 'finish') && !st.entities) {
      const ents = (await api(`${B(book.id)}/entities`)).entities;
      st.entities = st.entities || ents;
      if (my !== renderToken) return;
    }
    ({ format: wizFormat, content: wizContent, move: wizMove, finish: wizFinish })[key](body, st, qs, renderStep, byType);
    clear(foot).append(...[h('button', { class: 'ghost', onclick: () => d.close() }, 'Κλείσιμο'), h('span', { class: 'spacer' }),
      st.step > 0 ? h('button', { onclick: () => { st.step--; renderStep(); } }, '← Πίσω') : null,
      st.step < list.length - 1 ? h('button', { class: 'primary', onclick: () => { st.step++; renderStep(); } }, 'Επόμενο →')
        : h('a', { class: 'btn primary', href: `${B(book.id)}/export/${st.fmt}?${qs()}`, download: '' }, '↓ Λήψη')].filter(Boolean));
  };
  d.showModal();
  renderStep();
}
export function wizFormat(body, st, qs, renderStep) {
  body.append(h('p', { class: 'small mut' }, 'Το Markdown και το PDF είναι το τελικό κείμενο του βιβλίου, χωρίς σημάνσεις ελέγχου ή προέλευσης. Τα JSON είναι δεδομένα, με όλη την προέλευση.'),
    h('div', { class: 'wiz-cards' }, Object.entries(EXPORT_EL).map(([k, [t, dsc]]) => h('label', { class: 'wiz-card' + (st.fmt === k ? ' on' : '') },
      (() => { const r = h('input', { type: 'radio', name: 'fmt' }); r.checked = st.fmt === k; r.onchange = () => { st.fmt = k; st.step = 0; renderStep(); }; return r; })(),
      h('div', {}, h('b', {}, t), h('div', { class: 'small mut' }, dsc))))));
}
export function wizContent(body, st, qs, renderStep, byType) {
  const types = byType();
  body.append(h('p', { class: 'small' }, 'Ξετσέκαρε ό,τι δεν θέλεις στην έξοδο. ', infoTip('entities'),
    h('span', { class: 'mut' }, ' Εξαιρούνται μόνο επιβεβαιωμένες οντότητες· ό,τι δεν έχεις επιβεβαιώσει μένει πάντα.')));
  if (!Object.keys(types).length) body.append(h('div', { class: 'small mut' }, 'Δεν εντοπίστηκαν οντότητες.'));
  const grid = h('div', { class: 'ent-grid' });
  const cb = (k, label, n, total, strong) => { const i = h('input', { type: 'checkbox', disabled: !n }); i.checked = !st.exclude.has(k);
    i.onchange = () => { i.checked ? st.exclude.delete(k) : st.exclude.add(k); renderStep(); };
    return h('label', { class: 'row small' + (n ? '' : ' mut'), title: n ? '' : 'Επιβεβαίωσε πρώτα στην καρτέλα Δομή' }, i, strong ? h('b', {}, label) : label, h('span', { class: 'mut' }, ` (${n}/${total} επιβεβ.)`)); };
  const fams = {};
  Object.entries(types).forEach(([t, list]) => { const f = famOf(t) || 'x'; (fams[f] = fams[f] || []).push([t, list]); });
  for (const [f, rows] of Object.entries(fams)) {
    const all = rows.flatMap(([, l]) => l);
    const famBox = h('div', { class: 'ent-type' }, f === 'x' ? null : cb(`family:${f}`, `${famName(f)} — όλη η οικογένεια`, all.filter(e => e.verified).length, all.length, true));
    for (const [t, list] of rows) {
      if (t.startsWith('family:')) continue;                                   // covered by the family row
      const places = [...new Set(list.map(e => e.placement))];
      const row = h('div', { style: { marginLeft: f === 'x' ? 0 : '22px' } }, cb(t, entName(t), list.filter(e => e.verified).length, list.length));
      if (places.length > 1) places.forEach(p => row.append(h('div', { style: { marginLeft: '22px' } },
        cb(`${t}@${p}`, ENT().placements[p] || p, list.filter(e => e.verified && e.placement === p).length, list.filter(e => e.placement === p).length))));
      famBox.append(row);
    }
    grid.append(famBox);
  }
  // same selectors as the server (pipeline.entities.selectors): type and family, each alone and @placement
  const selectorsOf = (e) => [e.type, ...(famOf(e.type) && !e.type.startsWith('family:') ? [`family:${famOf(e.type)}`] : [])].flatMap(x => [x, `${x}@${e.placement}`]);
  const n = (st.entities || []).filter(e => e.verified && selectorsOf(e).some(x => st.exclude.has(x))).length;
  body.append(grid, h('div', { class: 'small', style: { marginTop: '8px' } }, n ? h('span', { class: 'badge-bad' }, `Θα εξαιρεθούν ${n} επιβεβαιωμένες οντότητες.`) : h('span', { class: 'mut' }, 'Δεν εξαιρείται τίποτα.')),
    h('div', { class: 'small' }, h('a', { href: `#/b/${S.book.id}/structure`, onclick: (e) => e.currentTarget.closest('dialog').close() }, 'Επιβεβαίωση οντοτήτων στην καρτέλα Δομή →')));
}
export function wizMove(body, st, qs, renderStep, byType) {
  const types = byType();
  const lvl = h('select', { style: { width: 'auto' }, onchange: () => { st.unit_level = +lvl.value; renderStep(); } },
    [[1, 'κεφαλαίου (επίπεδο 1)'], [2, 'ενότητας (επίπεδο 2)'], [3, 'υποενότητας (επίπεδο 3)']].map(([v, l]) => h('option', { value: v }, l)));
  lvl.value = String(st.unit_level);
  body.append(h('p', { class: 'small' }, 'Ό,τι επιλέξεις φεύγει από τη ροή του κειμένου και μπαίνει στο τέλος κάθε ', lvl, ', ομαδοποιημένο ανά είδος. ', infoTip('figuresend')));
  const capCount = (st.entities || []).filter(e => e.granularity === 'block' && e.label && /^(σχημ|εικον|εικ|φωτογ|διαγρ|γραφ|fig|photo|diagram|chart|graph|plot|scheme)/i.test(e.label.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase())).length;
  const fcb = h('input', { type: 'checkbox' }); fcb.checked = st.move.has('figures');
  fcb.onchange = () => { fcb.checked ? st.move.add('figures') : st.move.delete('figures'); renderStep(); };
  const imgRadio = (v, l) => h('label', { class: 'row small' }, (() => { const r = h('input', { type: 'radio', name: 'imgs', disabled: !st.move.has('figures') }); r.checked = st.images === v; r.onchange = () => { st.images = v; renderStep(); }; return r; })(), l);
  body.append(h('div', { class: 'wiz-move-item' }, h('label', { class: 'row' }, fcb, h('b', {}, 'Εικόνες με λεζάντα'),
      h('span', { class: 'small mut' }, ` — μόνο οπτικά στοιχεία που έχουν λεζάντα (~${capCount}), με όλα τα μέρη της λεζάντας. Ποτέ εξισώσεις ή κείμενο που έμεινε ως εικόνα.`)),
    h('div', { style: { marginLeft: '26px' } }, imgRadio('keep', 'με τις εικόνες'), imgRadio('drop', 'μόνο οι λεζάντες (για RAG/LLM)'))));
  const allMargin = (st.entities || []).filter(e => e.placement === 'margin').length;
  const mcb = h('input', { type: 'checkbox', disabled: !allMargin }); mcb.checked = st.move.has('*@margin');
  mcb.onchange = () => { mcb.checked ? st.move.add('*@margin') : st.move.delete('*@margin'); renderStep(); };
  body.append(h('div', { class: 'wiz-move-item', style: { marginTop: '8px' } }, h('label', { class: 'row' + (allMargin ? '' : ' mut') }, mcb, h('b', {}, 'Ό,τι βρίσκεται στο περιθώριο'),
    h('span', { class: 'small mut' }, ` (${allMargin}) — σημειώσεις, παραδείγματα, ορισμοί κ.λπ. έξω από το πλαίσιο του κειμένου· η θέση αρκεί, δεν χρειάζεται λεζάντα ή επιβεβαίωση`))));
  body.append(h('div', { class: 'small', style: { margin: '10px 0 4px' } }, h('b', {}, 'Ανά τύπο'),
    h('span', { class: 'mut' }, ' — «από το περιθώριο»: όλα όσα εντοπίστηκαν εκεί · «από παντού»: μόνο επιβεβαιωμένα (ο τύπος χρειάζεται τον έλεγχό σου)')));
  const table = h('div', { class: 'wiz-move-table' });
  const order = Object.keys(types).sort((a, b) => (a === 'sidenote' ? -1 : b === 'sidenote' ? 1 : 0));
  for (const t of order) {
    if (['figure', 'chart', 'chemical_drawing', 'equation', 'chemical_equation', 'table'].includes(t)) continue;
    const list = types[t], ver = list.filter(e => e.verified).length, margin = list.filter(e => e.placement === 'margin').length;
    const cur = st.move.has(t) ? 'all' : st.move.has(`${t}@margin`) ? 'margin' : 'none';
    const sel = h('select', { style: { width: 'auto' }, disabled: !(ver || margin), onchange: () => { st.move.delete(t); st.move.delete(`${t}@margin`);
      if (sel.value === 'all') st.move.add(t); else if (sel.value === 'margin') st.move.add(`${t}@margin`); renderStep(); } },
      h('option', { value: 'none' }, 'Όχι'),
      h('option', { value: 'margin', disabled: !margin }, `Μόνο από το περιθώριο (${margin})`),
      t === 'sidenote' ? null : h('option', { value: 'all', disabled: !ver }, `Από παντού (${ver} επιβεβ.)`));
    sel.value = cur;
    table.append(h('div', { class: 'row small' + (ver || margin ? '' : ' mut'), title: ver || margin ? '' : 'Επιβεβαίωσε πρώτα στην καρτέλα Δομή' },
      h('span', { class: 'grow' }, entName(t), h('span', { class: 'mut' }, ` (${list.length} εντοπίστηκαν · ${margin} στο περιθώριο · ${ver} επιβεβ.)`)), sel));
  }
  if (!table.children.length) table.append(h('div', { class: 'small mut' }, 'Δεν υπάρχουν οντότητες για μετακίνηση.'));
  body.append(table);
}
export function wizFinish(body, st, qs, renderStep) {
  const reader = READER_FORMATS.has(st.fmt), md = st.fmt.startsWith('markdown');
  const pre = h('pre', { class: 'preview-pre', style: { maxHeight: '30vh' } }, 'Φόρτωση…');
  const lines = [h('div', {}, h('b', {}, 'Μορφή: '), EXPORT_EL[st.fmt][0])];
  if (reader) {
    lines.push(h('div', {}, h('b', {}, 'Εξαιρούνται: '), st.exclude.size ? [...st.exclude].map(x => { const [t, p] = x.split('@'); return entName(t) + (p ? ` (${ENT().placements[p]})` : ''); }).join(', ') : 'τίποτα'));
    lines.push(h('div', {}, h('b', {}, 'Στο τέλος κάθε ' + ['', 'κεφαλαίου', 'ενότητας', 'υποενότητας'][st.unit_level] + ': '),
      st.move.size ? [...st.move].map(x => x === 'figures' ? `εικόνες με λεζάντα (${st.images === 'drop' ? 'μόνο λεζάντες' : 'με εικόνες'})` : x === '*@margin' ? 'ό,τι βρίσκεται στο περιθώριο' : (() => { const [t, p] = x.split('@'); return entName(t) + (p ? ' (από το περιθώριο)' : ' (επιβεβαιωμένα)'); })()).join(', ') : 'τίποτα'));
  }
  body.append(h('div', { class: 'wiz-summary small' }, lines));
  if (md) {
    const radio = (v, l, dsc) => h('label', { class: 'row small', style: { cursor: 'pointer' } }, (() => { const r = h('input', { type: 'radio', name: 'st' }); r.checked = st.style === v; r.onchange = () => { st.style = v; renderStep(); }; return r; })(), h('b', {}, l), h('span', { class: 'mut' }, dsc));
    body.append(h('h3', {}, 'Διαχωριστικά'), radio('tags', 'Ετικέτες HTML', ' <figure> … <figcaption> · <aside> — τις καταλαβαίνει κάθε LLM και renderer'),
      radio('comments', 'Αόρατα σχόλια', ' <!-- figure --> … <!-- /figure -->'));
  }
  if (reader) {
    body.append(h('h3', {}, 'Προεπισκόπηση (αρχή του αρχείου)'), pre);
    api(`${B(S.book.id)}/preview?${qs()}&images=${st.fmt === 'markdown_text' ? 0 : 1}`).then(t => { pre.textContent = t; }).catch(e => { pre.textContent = e.message; });
  }
}
export function processDialog(pages) {
  const b = S.book;
  const spec = h('input', { type: 'text', value: pages || '', placeholder: `π.χ. 1-5, 8 (κενό = όλες οι ${b.page_count})` });
  const est = h('div', { class: 'small mut' });
  const upd = () => { const n = countSpec(spec.value, b.page_count); est.textContent = n ? `${n} σελίδες · περίπου ${Math.max(1, Math.round(n * 1))}–${n * 2} λεπτά. Οι ήδη επεξεργασμένες βγαίνουν από την cache.` : 'Μη έγκυρο εύρος'; };
  spec.oninput = upd; upd();
  modal('Επεξεργασία σελίδων', h('div', {}, h('label', { class: 'field' }, 'Σελίδες'), spec, est,
    h('p', { class: 'small mut' }, 'Μπορείς να ξεκινήσεις τον έλεγχο μόλις ολοκληρωθεί, ή να την διακόψεις οποιαδήποτε στιγμή.')),
    [{ label: 'Άκυρο' }, { label: 'Έναρξη', cls: 'primary', run: async () => { await act(() => api(`${B(b.id)}/process`, { body: { pages: spec.value } }), 'Η επεξεργασία μπήκε στην ουρά', false); await refresh(); } }]);
  spec.focus();
}
export function countSpec(s, max) {
  if (!s.trim()) return max; let n = new Set();
  for (const part of s.split(',')) { const t = part.trim(); if (!t) continue; const m = t.match(/^(\d+)(?:-(\d+))?$/); if (!m) return 0;
    const a = +m[1], z = +(m[2] || m[1]); if (a < 1 || z > max || a > z) return 0; for (let i = a; i <= z; i++) n.add(i); }
  return n.size;
}
export async function openNextRequired() {
  const q = await api(`${B(S.book.id)}/queue?level=required`);
  if (!q.length) return toast('Δεν υπάρχουν τμήματα που απαιτούν έλεγχο');
  go(`#/b/${S.book.id}/p/${q[0].page}/${q[0].id}`);
}

export function tabPages(body) {
  const b = S.book, filters = [['all', 'Όλες'], ['needs_review', 'Για έλεγχο'], ['not_processed', 'Μη επεξεργασμένες'], ['ready', 'Έτοιμες']];
  if (b.pages.some(p => p.state === 'removed')) filters.push(['removed', 'Αφαιρεμένες']);
  else if (S.filter === 'removed') S.filter = 'all';
  const show = b.pages.filter(p => S.filter === 'all' ? p.state !== 'removed' : p.state === S.filter || (S.filter === 'ready' && (p.state === 'llm_ready' || p.state === 'verified')));
  const allSel = show.length > 0 && show.every(p => S.selPages.has(p.page));
  body.append(h('div', { class: 'row' }, h('div', { class: 'filters grow' }, filters.map(([k, l]) => h('button', { class: S.filter === k ? 'on' : '', onclick: () => { S.filter = k; renderBook(); } }, l))),
    show.length ? h('button', { class: 'ghost', title: 'Επιλογή όλων των σελίδων που φαίνονται (με το τρέχον φίλτρο)', onclick: () => {
      for (const p of show) allSel ? S.selPages.delete(p.page) : S.selPages.add(p.page); renderBook(); } }, allSel ? 'Αποεπιλογή όλων' : `Επιλογή όλων (${show.length})`) : null));
  const grid = h('div', { class: 'grid' });
  for (const p of show) {
    const cb = h('input', { type: 'checkbox', 'aria-label': `Επιλογή σελίδας ${p.page}`, onclick: (e) => {
      e.stopPropagation();
      // the range anchor lives in S: renderBook() rebuilds this grid after every click
      if (e.shiftKey && S.lastPageClicked) { const [a, z] = [S.lastPageClicked, p.page].sort((x, y) => x - y); for (let i = a; i <= z; i++) S.selPages.add(i); }
      else cb.checked ? S.selPages.add(p.page) : S.selPages.delete(p.page);
      S.lastPageClicked = p.page; renderBook();
    } });
    cb.checked = S.selPages.has(p.page);
    grid.append(h('div', { class: 'thumb' + (S.selPages.has(p.page) ? ' sel' : '') + (p.state === 'removed' ? ' removed' : ''), onclick: () => go(`#/b/${b.id}/p/${p.page}`) },
      h('img', { src: `${B(b.id)}/pages/${p.page}/image?w=300`, loading: 'lazy', alt: `Σελίδα ${p.page}` }), cb,
      h('div', { class: 'meta' }, h('span', {}, `σελ. ${p.page}`),
        h('span', { class: 'chip st-' + p.state }, p.state === 'needs_review' ? `${p.required} για έλεγχο` : STATE_EL[p.state]))));
  }
  body.append(grid);
  if (S.selPages.size) {
    const state = new Map(b.pages.map(p => [p.page, p.state]));
    const sel = [...S.selPages].filter(n => state.has(n)).sort((a, c) => a - c);
    const kept = sel.filter(n => state.get(n) !== 'removed'), gone = sel.filter(n => state.get(n) === 'removed');
    const pagesAct = (path, pages, msg) => act(() => api(`${B(b.id)}/pages/${path}`, { body: { pages } }), msg).then(() => { S.selPages.clear(); refresh(); });
    body.append(h('div', { class: 'actionbar' }, `${sel.length} επιλεγμένες`,
      h('button', { class: 'ghost', style: { color: '#fff' }, onclick: () => { S.selPages.clear(); renderBook(); } }, 'Καθαρισμός'),
      kept.length ? h('button', { class: 'primary', onclick: () => { processDialog(kept.join(',')); } }, 'Επεξεργασία επιλεγμένων') : null,
      kept.length ? h('button', { class: 'danger', title: 'Οι σελίδες βγαίνουν από το βιβλίο και τις εξόδους· το PDF και το OCR μένουν, επαναφέρονται οποτεδήποτε', onclick: () =>
        modal(`Αφαίρεση ${kept.length} σελίδων;`, h('p', {}, `Οι σελίδες ${kept.length > 12 ? kept.length : kept.join(', ')} θα βγουν από το βιβλίο και από όλες τις εξόδους. Το πρωτότυπο PDF και το OCR τους κρατιούνται· επαναφέρονται από το φίλτρο «Αφαιρεμένες» ή με Αναίρεση.`),
          [{ label: 'Άκυρο' }, { label: 'Αφαίρεση', cls: 'danger', run: () => pagesAct('remove', kept, 'Οι σελίδες αφαιρέθηκαν') }]) }, 'Αφαίρεση από το βιβλίο') : null,
      gone.length ? h('button', { onclick: () => pagesAct('restore', gone, 'Οι σελίδες επανήλθαν') }, `Επαναφορά (${gone.length})`) : null));
  }
}

export async function tabReview(body) {
  const lv = h('div', { class: 'filters' }, [['required', `Απαιτείται (${S.book.required})`], ['recommended', `Συνιστάται (${S.book.recommended})`]].map(([k, l]) =>
    h('button', { class: S.level === k ? 'on' : '', onclick: () => { S.level = k; renderBook(); } }, l)));
  body.append(lv, h('p', { class: 'small mut' }, 'Η σειρά είναι κατά σπουδαιότητα ', infoTip('priority'), '. Μπορείς επίσης να ανοίξεις οποιαδήποτε σελίδα και να ελέγξεις κάθε τμήμα, επισημασμένο ή όχι. Για πολλά όμοια τμήματα δες τον «Μαζικό έλεγχο».'));
  const card = h('div', { class: 'card' }); body.append(card);
  const q = await api(`${B(S.book.id)}/queue?level=${S.level}`);
  if (!q.length) { card.append(h('div', { class: 'empty', style: { border: 0 } }, 'Τίποτα εδώ. 🎉')); return; }
  for (const it of q) {
    card.append(h('div', { class: 'list-item', onclick: () => go(`#/b/${S.book.id}/p/${it.page}/${it.id}`) },
      h('span', { class: 'dot ' + it.review }), h('span', { class: 'small mut', style: { width: '52px' } }, `σελ. ${it.page}`),
      it.critical ? h('span', { class: 'chip rv-required' }, 'κρίσιμο') : null, h('div', { class: 'grow' }, h('div', {}, KIND_EL[it.kind] || it.kind),
        h('div', { class: 'small mut' }, it.reasons.map(r => R(r).el).join(' · '))), '›'));
  }
}
