import { B, api } from '../core/api.js';
import { $, h } from '../core/dom.js';
import { S, kindLabel } from '../core/state.js';
import { CAPROLE_EL } from '../core/strings.js';
import { act, toast } from '../ui/feedback.js';
import { renderWorkspace, selectBlock } from '../workspace/workspace.js';

// ---------------------------------------------------------------- caption groups (link, role, detach)
export function captionPanel(blk) {
  const book = S.book, byId = Object.fromEntries(S.page.blocks.map(b => [b.id, b]));
  const decide = (action, extra, msg) => act(() => api(`${B(book.id)}/decide`, { body: { block_id: blk.id, action, ...extra } }), msg);
  const startLink = (mode) => { S.linking = { mode, from: blk.id }; toast(mode === 'caption->figure' ? 'Κάνε κλικ στο σχήμα ή στην εξίσωση που ανήκει η λεζάντα (Esc: άκυρο)' : 'Κάνε κλικ στη λεζάντα που ανήκει σε αυτό (Esc: άκυρο)'); renderWorkspace(); };
  if (blk.kind === 'caption' || ['heading', 'paragraph'].includes(blk.kind)) {
    const owner = byId[blk.relations?.caption_for];
    if (blk.kind !== 'caption' && !owner) return h('details', {}, h('summary', {}, 'Λεζάντα'), h('div', {},
      h('p', { class: 'small mut' }, 'Αν αυτό το τμήμα είναι λεζάντα (π.χ. «Σχήμα 4.9»), σύνδεσέ το με το σχήμα του.'),
      h('button', { onclick: () => startLink('caption->figure') }, 'Σύνδεση με σχήμα…')));
    return h('details', { open: blk.kind === 'caption' }, h('summary', {}, 'Λεζάντα'), h('div', {},
      h('div', { class: 'row small' }, 'Ανήκει σε:', owner ? h('a', { href: '#', onclick: (e) => { e.preventDefault(); selectBlock(owner.id); } }, kindLabel(owner)) : h('span', { class: 'badge-bad' }, 'κανένα σχήμα'),
        blk.relations?.inferred ? h('span', { class: 'mut' }, '(συμπεράνθηκε)') : null),
      h('div', { class: 'row small', style: { margin: '6px 0' } }, 'Ρόλος:',
        ['title', 'text'].map(r => h('button', { class: 'small' + (blk.caption_role === r ? ' primary' : ''), onclick: () => decide('set_caption_role', { role: r }, 'Ο ρόλος ενημερώθηκε') }, CAPROLE_EL[r])),
        blk.caption_label ? h('span', { class: 'mut' }, `ετικέτα: ${blk.caption_label}`) : null),
      h('div', { class: 'actions' }, h('button', { onclick: () => startLink('caption->figure') }, owner ? 'Σύνδεση με άλλο σχήμα…' : 'Σύνδεση με σχήμα…'),
        owner ? h('button', { class: 'ghost', onclick: () => decide('detach_caption', {}, 'Η λεζάντα αποσυνδέθηκε') }, 'Αποσύνδεση') : null)));
  }
  const caps = (blk.relations?.captions || []).map(id => byId[id]).filter(Boolean);
  if (!['figure', 'chart', 'molecular_structure', 'reaction_scheme', 'lewis_structure', 'table', 'math', 'chemical_reaction'].includes(blk.kind) && !caps.length) return h('span');
  return h('details', { open: !!caps.length }, h('summary', {}, `Λεζάντες (${caps.length})`), h('div', {},
    caps.length ? caps.map((c, i) => h('div', { class: 'list-item', onclick: () => selectBlock(c.id) }, h('span', { class: 'small mut' }, i + 1),
      h('span', { class: 'chip' }, CAPROLE_EL[c.caption_role] || 'Κείμενο λεζάντας'), h('span', { class: 'txt grow small' }, (c.chosen?.text || '').slice(0, 80))))
      : h('p', { class: 'small mut' }, 'Καμία λεζάντα συνδεδεμένη.'),
    h('button', { style: { marginTop: '6px' }, onclick: () => startLink('figure->caption') }, 'Προσθήκη λεζάντας…')));
}
export async function finishLink(target) {
  const L = S.linking; S.linking = null;
  const [caption, figure] = L.mode === 'caption->figure' ? [L.from, target.id] : [target.id, L.from];
  if (caption === figure) return renderWorkspace();
  await act(() => api(`${B(S.book.id)}/decide`, { body: { block_id: caption, action: 'associate_caption', figure_id: figure } }), 'Η λεζάντα συνδέθηκε');
}
