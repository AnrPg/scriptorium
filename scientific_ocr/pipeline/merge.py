"""Stage: reviewer merges. Several segments of one page become ONE entity (e.g. a paragraph the layout split into
pieces, or caption fragments re-tagged as one caption).

Runs after the reviewer's decisions on the members (so each member contributes its final, reviewed text) and
before decisions addressed to the merged segment itself. Members leave the reading flow; they stay recorded in
`merged_from` (content, source, review of each part) and in page info, and the event log keeps every version.
Parts are joined in the ORDER THE REVIEWER ARRANGED them (merge['ordered'] = True; merges recorded before
ordering existed keep reading order). A word hyphenated across parts is re-joined, a sentence that continues
gets a space, otherwise parts are separated by a line break. The merged entity takes the reading position of
the earliest member.
"""
from __future__ import annotations

from ..core import geometry as geo
from ..core.model import block_id, content_hash, new_block
from ..core.reasons import flag
from . import engine_json
from .flow import continues

SETTLED = ('approved', 'image_only')


def merged_id(doc_sha: str, merge: dict) -> str:
    return block_id(doc_sha, int(merge['page']), 'merge:' + merge['id'])


def join_texts(parts: list[str], kind: str) -> str:
    out = parts[0]
    for nxt in parts[1:]:
        prev = out.rstrip()
        if prev.endswith(('-', '‐')) and nxt[:1].isalpha():
            out = prev[:-1] + nxt.lstrip()                  # re-join a hyphenated word
        elif kind in ('caption', 'heading') or continues(prev, nxt):
            out = prev + ' ' + nxt.lstrip()
        else:
            out = prev + '\n\n' + nxt.lstrip()
    return out


def apply(blocks: list[dict], merges: list[dict], doc_sha: str, crop, pages: list[dict]) -> dict:
    """crop(page, block_id, bbox) -> source_crop record. Mutates `blocks` in place."""
    applied, stale = [], []
    for m in merges:
        by_id = {b['id']: b for b in blocks}
        members = [by_id[i] for i in dict.fromkeys(m['members']) if i in by_id]
        if not m.get('ordered'):
            members.sort(key=blocks.index)                  # legacy merge: reading order
        if len(members) < 2:
            stale.append({'merge': m['id'], 'why': 'fewer than two members still exist (layout or OCR changed)'})
            continue
        try:
            spec = engine_json.region_spec(m['label'])
        except ValueError as exc:                           # a label no longer known: stale, never a crash
            stale.append({'merge': m['id'], 'why': f'unknown label: {exc}'})
            continue
        kind = spec['kind'] if spec['kind'] != 'unknown' else 'paragraph'
        bid = merged_id(doc_sha, m)
        bbox = geo.union([b['geometry']['bbox'] for b in members])
        # LaTeX members keep their maths delimiters inside the joined text (the notation of mixed text: $…$)
        texts = [b['chosen'].get('text') or (f"${b['chosen']['latex']}$" if b['chosen'].get('latex') else None)
                 for b in members]
        blk = new_block(bid, int(m['page']), kind, bbox, source='merge', role=spec['role'], merge_id=m['id'],
                        engine_label=f'{m["label"]["engine"]}:{m["label"]["name"]}',
                        candidates=[{**c, 'member': b['id']} for b in members for c in b['candidates']])
        blk['merged_from'] = [{'id': b['id'], 'kind': b['kind'], 'text': t, 'content_source': b.get('content_source'),
                               'review': b['review'], 'source': b.get('source')} for b, t in zip(members, texts)]
        blk['source_crop'] = crop(int(m['page']), bid, bbox)
        if all(texts):
            blk['chosen'] = {'type': 'text', 'text': join_texts(texts, kind), 'provenance': f'merge:{m["id"]}'}
        else:
            blk['display_crop'] = True
            flag(blk, 'MERGE_PART_WITHOUT_TEXT', parts=[b['id'] for b, t in zip(members, texts) if not t])
        blk['machine_hash'] = blk['auto_hash'] = content_hash(blk['chosen'])
        flag(blk, 'MERGED_SEGMENT', members=[b['id'] for b in members])
        if all(b['review'] in SETTLED for b in members) and all(texts):
            blk['review'] = 'approved'                      # every part was already reviewed
        else:
            for b in members:
                if b['review'] in ('required', 'recommended'):
                    blk['review'] = 'required' if 'required' in (blk['review'], b['review']) else 'recommended'
        _carry_caption_links(blk, members, by_id)
        pos = min(blocks.index(b) for b in members)
        for b in members:
            blocks.remove(b)
        blocks.insert(pos, blk)
        info = next((p for p in pages if p['page'] == int(m['page'])), None)
        if info is not None:
            info.setdefault('merged', []).append({'merge': m['id'], 'into': bid, 'members': [b['id'] for b in members]})
        applied.append(m['id'])
    return {'applied': applied, 'stale': stale}


def _carry_caption_links(blk, members, by_id):
    """Captions of a merged caption point to its figure; a figure's caption list refers to the merged entity."""
    owner = next((b['relations'].get('caption_for') for b in members if b['relations'].get('caption_for')), None)
    if owner and blk['kind'] == 'caption':
        blk['relations'].update(caption_for=owner, inferred=False)
    ids = {b['id'] for b in members}
    for f in by_id.values():
        caps = f['relations'].get('captions')
        if caps and ids & set(caps):
            new = []
            for c in caps:
                c = blk['id'] if c in ids else c
                if c not in new:
                    new.append(c)
            f['relations']['captions'] = new
    own_caps = [c for b in members for c in b['relations'].get('captions', []) if c not in ids]
    if own_caps:
        blk['relations']['captions'] = list(dict.fromkeys(own_caps))
    for c in by_id.values():                          # BOTH sides: captions of the parts now belong to the merged block
        if c['relations'].get('caption_for') in ids and c['id'] not in ids:
            c['relations']['caption_for'] = blk['id']
