"""Stage: reviewer groups (annotation). A group states that several segments belong together — an example box
and its solution, an exercise, a definition, a sidebar spanning two pages — WITHOUT changing them: every member
keeps its id, type and content. Groups are metadata for downstream chunking/RAG and LLM agents.

Members that were later merged are followed to the merged segment; members that no longer exist are listed as
missing (the group survives with the rest).
"""
from __future__ import annotations

from ..core.model import CAPTIONABLE_KINDS
from ..core.reasons import flag

GENERIC_GROUP_TYPES = {'other', ''}       # types that say nothing about the group's nature: its members decide


def is_figure_group(g: dict, kinds: dict[str, str]) -> bool:
    """The nature of a group: a FIGURE (one outer figure in the export, may take a group caption) or a box.
    Text never changes it: a figure may hold parsed text (labels, values, important words in the picture) and its
    members' own captions. 'figure_group' is always a figure; a generic group is a figure when it holds a figure;
    a semantic type (example, sidebar…) is a figure only when everything in it besides text captions is visual."""
    if g['type'] == 'figure_group':
        return True
    body = [kinds[m] for m in g['members'] if m in kinds and kinds[m] != 'caption']
    visual = [k for k in body if k in CAPTIONABLE_KINDS]
    if (g.get('type') or '') in GENERIC_GROUP_TYPES:
        return bool(visual)
    return bool(body) and len(visual) == len(body)


def gaps(members: list[str], blocks: list[dict]) -> list[str]:
    """Segments of the reading flow that sit BETWEEN the first and last member but are not part of the group
    (captions of member figures and page furniture do not count). Empty = the group is contiguous."""
    flow = [b for b in blocks if not b.get('excluded') and b.get('flow') != 'furniture']
    ids = set(members)
    idx = [i for i, b in enumerate(flow) if b['id'] in ids]
    if len(idx) < 2:
        return []
    return [b['id'] for b in flow[idx[0]:idx[-1] + 1]
            if b['id'] not in ids and b['relations'].get('caption_for') not in ids]


def annotate(blocks: list[dict], groups: list[dict], merged_into: dict[str, str],
             auto: list[dict] | None = None) -> list[dict]:
    """Mutates members (block['groups']); returns the canonical group list. Automatic groups that overlap a
    reviewer group are dropped (the human version wins)."""
    human = set().union(*[{merged_into.get(m, m) for m in g['members']} for g in groups]) if groups else set()
    groups = [{**g, 'source': g.get('source', 'human')} for g in groups] + \
        [g for g in (auto or []) if not human & set(g['members'])]   # any overlap: the reviewer's grouping wins
    by_id = {b['id']: b for b in blocks}
    order = {b['id']: i for i, b in enumerate(blocks)}
    for b in blocks:
        b.pop('groups', None)
    out = []
    for g in groups:
        members, missing = [], []
        for mid in g['members']:
            mid = merged_into.get(mid, mid)
            if mid in by_id and mid not in members:
                members.append(mid)
            elif mid not in by_id:
                missing.append(mid)
        if not g.get('ordered'):
            members.sort(key=order.get)                  # automatic / legacy groups: reading order
                                                         # (reviewer groups keep the order the reviewer arranged)
        if len(members) < 2:                             # not a group any more: no dangling ids on its blocks
            continue
        for mid in members:
            by_id[mid].setdefault('groups', []).append(g['id'])
        out.append({'id': g['id'], 'type': g['type'], 'name': g.get('name') or '', 'members': members,
                    'missing': missing, 'pages': sorted({by_id[m]['page'] for m in members}),
                    'source': g['source'], 'key': g.get('key'), 'rule': g.get('rule'),
                    'stop': g.get('stop'), 'ambiguous': bool(g.get('ambiguous')),
                    'ordered': bool(g.get('ordered'))})
    _group_captions(blocks, out, by_id)
    kinds = {b['id']: b['kind'] for b in blocks}
    for g in out:
        g['figure'] = is_figure_group(g, kinds)
    return out


def _group_captions(blocks: list[dict], groups: list[dict], by_id: dict) -> None:
    """Captions of whole groups (decision associate_group_caption): listed on the group (g['captions']) and placed
    right after the group's last member when on the same page (the export renders them at the end of the group).
    A link to a group that no longer exists is dropped: the caption shows where it is and is flagged."""
    gid = {g['id']: g for g in groups}
    for g in groups:
        g['captions'] = []
    for b in blocks:
        k = b['relations'].get('caption_group')
        if not k:
            continue
        g = gid.get(k)
        if g is None or b['id'] in g['members']:
            b['relations'].pop('caption_group', None)
            flag(b, 'CAPTION_WITHOUT_FIGURE')
            continue
        g['captions'].append(b['id'])
    moved = False
    for g in groups:
        pos = {b['id']: i for i, b in enumerate(blocks)}
        last = max((by_id[m] for m in g['members']), key=lambda x: pos[x['id']], default=None)
        for cid in g['captions']:
            cap = by_id[cid]
            if last is None or cap['page'] != last['page']:
                continue
            blocks.remove(cap)
            at = blocks.index(last)                   # after the last member AND its own captions
            mem = set(g['members'])
            while at + 1 < len(blocks) and blocks[at + 1]['relations'].get('caption_for') in mem:
                at += 1
            blocks.insert(at + 1, cap)
            last, moved = cap, True
    if moved:
        order: dict[int, int] = {}
        for b in blocks:
            order[b['page']] = order.get(b['page'], 0) + 1
            b['reading_order'] = order[b['page']]
