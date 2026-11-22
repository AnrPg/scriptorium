"""Stage: structure suggestions. Risk decides what is automatic:

  automatic (annotation only, nothing changes):   auto GROUPS — every entity with its whole EXTENT (pipeline.extents:
      label, title, statement, sub-headings, paragraphs, equations, figures, solution; across a page break),
      delimited by shading or layout. Each records the rule that delimited it ('rule'). source='auto';
      reported; dismissable; editable (a reviewer group always wins).
  suggested only (changes entities, needs a human click):
      merge_join   a paragraph the layout split on the same page with nothing in between (flow.continues)
      merge_reocr  side-by-side boxes on the same lines with OCR trouble (pipeline.checks.layout_splits)
      figure_assemble  a picture the layout split into pieces (panel labels, empty or image-only lines) directly
                   above a caption that has no figure: one reviewer region (picture) over the pieces
Every suggestion has a stable key (kind + members) so a dismissal persists across rebuilds. A model may add
suggestions on demand (application.services.propose_structure); they follow the same path and are never applied
without the reviewer.
"""
from __future__ import annotations

import hashlib
import re

from ..core import geometry as geo


def key(kind: str, members: list[str]) -> str:
    return hashlib.sha1(f'{kind}|{",".join(sorted(members))}'.encode()).hexdigest()[:10]


def suggest(blocks: list[dict], toc: list[dict], pages: list[dict], dismissed: set) -> list[dict]:
    """Returns auto groups; writes merge suggestions into page info (info['suggestions'])."""
    by_id = {b['id']: b for b in blocks}
    for info in pages:
        sugg = []
        for s in info.get('layout_splits', []):
            sugg.append({'kind': 'merge_reocr', 'members': s['blocks'], 'bbox': s['bbox'],
                         'label': {'engine': 'marker', 'name': 'Text'}, 'source': 'rule',
                         'reason': 'boxes on the same lines with OCR trouble: one paragraph probably cut by the layout'})
        for b in blocks:
            nxt = by_id.get(b['relations'].get('continues_in'))
            if b['page'] == info['page'] and nxt and nxt['page'] == b['page'] and not nxt['relations'].get('interrupted_by'):
                lab = b.get('engine_label') or 'marker:Text'
                eng, name = lab.split(':', 1) if ':' in lab else ('marker', lab)
                sugg.append({'kind': 'merge_join', 'members': [b['id'], nxt['id']], 'label': {'engine': eng, 'name': name},
                             'source': 'rule', 'reason': 'the sentence continues in the next segment (layout split)'})
        sugg += _figure_assemblies([b for b in blocks if b['page'] == info['page']])
        for s in sugg:
            s['key'] = key(s['kind'], s['members'])
        info['suggestions'] = [s for s in sugg if s['key'] not in dismissed]
    from .extents import extents  # lazy: extents -> entities -> furniture -> structure
    groups = []
    labelled = {s['id'] for s in toc if s['source'] == 'labelled_element'}
    for x in extents(blocks, labelled, _page_units(pages)):
        k = key('group', x['members'])
        if k not in dismissed:
            groups.append({'id': 'A' + k, 'key': k, 'type': x['type'], 'name': x['name'], 'members': x['members'],
                           'source': 'auto', 'rule': x['rule'], 'head': x['head'], 'stop': x['stop'],
                           'ambiguous': x['ambiguous']})
    return groups


from .extents import SHADED, TONE_TOLERANCE  # noqa: E402,F401  one definition (shaded boxes), re-exported


def paper_tone(b):
    t = (b.get('source_crop') or {}).get('paper_tone')
    return t if isinstance(t, list) else None


def tone_distance(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _page_units(pages) -> dict[int, float]:
    return {p['page']: (p.get('coverage') or {}).get('unit', 1.0) for p in pages}


PANEL = re.compile(r'^\W{0,2}\(?[A-Za-zΑ-Ωα-ω]\)\s')     # "(A) …", "(Γ) …" panel labels of a composite figure
PIECE_CHARS = 60


def _figure_piece(b) -> bool:
    if b.get('flow') == 'furniture' or b['kind'] == 'caption' or b['relations'].get('caption_for'):
        return False
    t = (b['chosen'].get('text') or '').strip() if b['chosen'].get('type') == 'text' else ''
    if b['kind'] in ('excerpt',) or b['chosen'].get('type') in ('image_only', 'unresolved'):
        return True
    return b['kind'] in ('paragraph', 'heading') and (not t or len(t) <= PIECE_CHARS or bool(PANEL.match(t)))


def _figure_assemblies(page_blocks) -> list[dict]:
    """Captions without a figure, preceded (same column, nothing else between) by figure pieces."""
    out = []
    for i, cap in enumerate(page_blocks):
        if cap['kind'] != 'caption' or cap['relations'].get('caption_for'):
            continue
        cb, pieces = cap['geometry']['bbox'], []
        for b in reversed(page_blocks[:i]):
            bb = b['geometry']['bbox']
            if not _figure_piece(b) or not geo.same_column(bb, cb, 0.2):
                break
            pieces.insert(0, b)
        if pieces:
            box = geo.union([p['geometry']['bbox'] for p in pieces])
            if geo.covered_by(cb, box) < 0.3:
                out.append({'kind': 'figure_assemble', 'members': [p['id'] for p in pieces], 'bbox': box,
                            'caption': cap['id'], 'label': {'engine': 'marker', 'name': 'Picture'}, 'source': 'rule',
                            'reason': f'{len(pieces)} pieces above a caption without a figure: probably one picture split by the layout'})
    return out
