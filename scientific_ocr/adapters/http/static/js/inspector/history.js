import { B, api } from '../core/api.js';
import { $, clear, h } from '../core/dom.js';
import { S } from '../core/state.js';
import { ACTION_EL } from '../core/strings.js';
import { engChip } from '../inspector/lineage.js';
import { contentView, currentValue } from '../inspector/segment.js';
import { modal } from '../ui/feedback.js';

// ---------------------------------------------------------------- per-segment history of versions
export function historyPanel(blk, latex) {
  const box = h('div', {}, h('div', { class: 'small mut' }, 'Φόρτωση…'));
  const det = h('details', { ontoggle: async () => {
    if (!det.open || det.dataset.loaded) return; det.dataset.loaded = 1;
    const H = await api(`${B(S.book.id)}/blocks/${blk.id}/history`);
    const cur = currentValue(blk);
    const compare = (label, val) => modal(`Σύγκριση: ${label} ↔ τρέχουσα`, h('div', { class: 'cmp' },
      h('div', {}, h('b', {}, label), contentView(blk, val, latex)), h('div', {}, h('b', {}, 'Τρέχουσα'), contentView(blk, cur, latex))), [{ label: 'Κλείσιμο' }]);
    const rows = [];
    for (const m of H.machine) rows.push(h('div', { class: 'list-item' }, engChip(m.engine), h('span', { class: 'grow small mut' }, 'ανάγνωση μηχανής'), h('button', { class: 'ghost small', onclick: () => compare(m.engine, m.text || m.latex) }, 'Σύγκριση')));
    for (const p of H.proposals) rows.push(h('div', { class: 'list-item' }, engChip('μοντέλο'), h('span', { class: 'grow small' }, `«${(p.note || '').slice(0, 50)}» → ${p.proposal?.action}${p.valid ? '' : ' (άκυρη)'}`),
      p.proposal?.value ? h('button', { class: 'ghost small', onclick: () => compare('πρόταση', p.proposal.value) }, 'Σύγκριση') : null));
    for (const e of H.events) rows.push(h('div', { class: 'list-item' + (e.active ? '' : ' undone') }, engChip('human'), h('span', { class: 'grow small' },
      `${(e.ts || '').replace('T', ' ').slice(0, 16)} · ${e.type === 'decision' ? ACTION_EL[e.action] || e.action : e.type}${e.active ? '' : ' (αναιρέθηκε)'}`),
      e.value ? h('button', { class: 'ghost small', onclick: () => compare('απόφαση ' + e.seq, e.value) }, 'Σύγκριση') : null));
    clear(box).append(...(rows.length ? rows : [h('div', { class: 'small mut' }, 'Καμία εκδοχή.')]));
  } }, h('summary', {}, 'Ιστορικό εκδοχών'), box);
  return det;
}
