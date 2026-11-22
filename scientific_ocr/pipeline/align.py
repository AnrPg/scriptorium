"""Stage: align Marker and Docling blocks of one page into components (union-find over box overlap).

Components handle 1:N splits and N:1 merges without greedy 1:1 assignment. Text similarity only labels the
component (matched / conflict / single_engine / matched_geometry_only); it never alters content.
"""
from __future__ import annotations

from ..core.geometry import containment, iou
from ..core.text import similarity as text_similarity

TEXTUAL = {'paragraph', 'heading', 'caption', 'math', 'table'}
VISUAL = {'figure', 'molecular_structure'}   # engine-level kinds (before refinement)


def align(mblocks: list[dict], dblocks: list[dict], min_contain=0.5, min_iou=0.25) -> dict:
    """Return {'components': [...], 'by_ref': {ref: component_index}}."""
    nodes = [b['ref'] for b in mblocks] + [b['ref'] for b in dblocks]
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    edges = []
    for m in mblocks:
        for d in dblocks:
            c, j = containment(m['bbox'], d['bbox']), iou(m['bbox'], d['bbox'])
            # A figure may contain many small text labels; don't fuse prose into a figure component.
            if (m['kind'] in VISUAL) != (d['kind'] in VISUAL) and j < 0.5:
                continue
            if c >= min_contain or j >= min_iou:
                edges.append((m['ref'], d['ref'], round(c, 3), round(j, 3)))
                parent[find(m['ref'])] = find(d['ref'])
    comps: dict[str, dict] = {}
    for b in mblocks + dblocks:
        root = find(b['ref'])
        comp = comps.setdefault(root, {'marker': [], 'docling': []})
        comp[b['engine']].append(b)
    components = []
    by_ref = {}
    for comp in comps.values():
        ms = sorted(comp['marker'], key=lambda b: b['order'])
        ds = sorted(comp['docling'], key=lambda b: b['order'])
        mt = ' '.join(b['text'] for b in ms if b['kind'] in TEXTUAL)
        dt = ' '.join(b['text'] for b in ds if b['kind'] in TEXTUAL)
        sim = text_similarity(mt, dt) if mt and dt else None
        if not ms or not ds:
            status = 'single_engine'
        elif sim is None:
            status = 'matched_geometry_only'     # e.g. figure vs figure, or one side has no text
        elif sim >= 0.6:
            status = 'matched'
        else:
            status = 'conflict'
        shape = f'{len(ms)}:{len(ds)}'
        idx = len(components)
        components.append({'marker': [b['ref'] for b in ms], 'docling': [b['ref'] for b in ds],
                           'status': status, 'shape': shape, 'text_similarity': sim,
                           'edges': [e for e in edges if e[0] in {b['ref'] for b in ms}]})
        for b in ms + ds:
            by_ref[b['ref']] = idx
    return {'components': components, 'by_ref': by_ref}
