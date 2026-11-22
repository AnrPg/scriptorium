"""Stage: reviewer groups (annotation). A group states that several segments belong together — an example box
and its solution, an exercise, a definition, a sidebar spanning two pages — WITHOUT changing them: every member
keeps its id, type and content. Groups are metadata for downstream chunking/RAG and LLM agents.

Members that were later merged are followed to the merged segment; members that no longer exist are listed as
missing (the group survives with the rest).
"""
from __future__ import annotations


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
    return out
