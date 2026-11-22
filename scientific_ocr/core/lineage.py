"""Lineage of a canonical block: how its content was detected, read, transformed and decided (pure).

trace(block) -> ordered steps, each {stage, by, detail, ref?, raw_file?, raw_pointer?}. Stored in canonical.json
(block.lineage) and summarised as block.content_source = the engine/person that supplied the final content.
"""
from __future__ import annotations

DETECTED_BY = {'merge': 'reviewer merge of several segments','marker': 'Marker layout', 'docling': 'Docling layout (Marker missed it)',
               'coverage': 'coverage check (printed ink no engine boxed)', 'region': 'reviewer-drawn region',
               'fallback': 'whole-page fallback (extraction failed)'}


def content_source(b: dict) -> str:
    ch = b['chosen']
    prov = ch.get('provenance') or ''
    if prov.startswith('human:'):
        return 'human' + (f' (candidate {ch["approved_candidate"]})' if ch.get('approved_candidate') else '')
    if ch['type'] in ('image_only', 'unresolved'):
        return 'source image'
    if prov.startswith('region:'):
        return 'marker (region re-OCR)'
    return prov.split(':', 1)[0] or 'unknown'


def trace(b: dict) -> list[dict]:
    steps = [{'stage': 'detected', 'by': b.get('source'), 'detail': DETECTED_BY.get(b.get('source'), b.get('source')),
              'label': b.get('engine_label')}]
    if b.get('merged_from'):
        steps.append({'stage': 'merged', 'by': 'human', 'detail': 'merged from ' + ', '.join(
            f'{m["id"]} ({m.get("content_source") or m.get("source")})' for m in b['merged_from'])})
    g = b['geometry']
    if g.get('engine_bbox') and g['engine_bbox'] != g['bbox']:
        steps.append({'stage': 'geometry', 'by': 'pipeline',
                      'detail': 'box widened to the union of the matching Marker and Docling boxes'})
    for c in b.get('candidates', []):
        steps.append({'stage': 'read', 'by': c['engine'], 'version': c.get('version'), 'status': c.get('status') or 'OK',
                      'ref': c.get('ref'), 'raw_file': c.get('raw_file'), 'raw_pointer': c.get('raw_pointer')})
    m = b.get('match') or {}
    if m.get('status') and m['status'] != 'no_engine':
        steps.append({'stage': 'compared', 'by': 'pipeline', 'detail': f'{m["status"]} (shape {m.get("shape")}, '
                      f'text similarity {m["text_similarity"]:.2f})' if m.get('text_similarity') is not None
                      else f'{m["status"]} (shape {m.get("shape")})'})
    ch = b['chosen']
    steps.append({'stage': 'chosen', 'by': content_source(b), 'detail': ch.get('provenance'),
                  'type': ch['type']})
    for r in b.get('rule_corrections', []):
        steps.append({'stage': 'book rule', 'by': f'rule {r["rule"]}', 'detail': f'{r["from"]} → {r["to"]}'})
    if ch.get('withheld_latex') or ch.get('withheld_text'):
        steps.append({'stage': 'withheld', 'by': 'pipeline',
                      'detail': 'machine reading withheld (engines disagree); the source image is the content'})
    for d in b.get('decisions') or []:
        steps.append({'stage': 'human', 'by': d.get('actor'), 'detail': d.get('action'), 'ts': d.get('ts'),
                      'seq': d.get('seq')})
    if b['relations'].get('resolved_by'):
        steps.append({'stage': 'human', 'by': 'pipeline', 'detail': f'resolved by decision on {b["relations"]["resolved_by"]}'})
    return steps


def dag(b: dict) -> dict:
    """Lineage as a DAG: {'nodes': [{id, ...step}], 'edges': [[from, to], ...]} (pure; derived from trace()).

    Semantics, not list order: engine readings happen in parallel and all feed the comparison; the parts of a
    merged segment are roots that converge on the merge; book rules, withheld readings and reviewer decisions
    (in event order) transform the compared result; the final content is the single sink.
    """
    steps = b.get('lineage') or trace(b)
    nodes, edges = [], []

    def add(step, parents):
        nid = f'n{len(nodes)}'
        nodes.append({'id': nid, **step})
        edges.extend([p, nid] for p in parents)
        return nid

    by = {s: [x for x in steps if x['stage'] == s] for s in
          ('detected', 'merged', 'geometry', 'read', 'compared', 'book rule', 'withheld', 'human', 'chosen')}
    tips = []
    if b.get('merged_from'):
        tips = [add({'stage': 'part', 'by': m.get('content_source') or m.get('source'), 'detail': m['id'],
                     'text': (m.get('text') or '')[:120]}, []) for m in b['merged_from']]
        for s in by['merged']:
            tips = [add(s, tips)]
    for s in by['detected'] + by['geometry']:
        tips = [add(s, tips)]
    if by['read']:
        tips = [add(s, tips) for s in by['read']]                 # parallel readings of the same box
    for s in by['compared'] + by['book rule'] + by['withheld'] + by['human']:
        tips = [add(s, tips)]
    for s in by['chosen']:
        add({**s, 'final': True}, tips)
    return {'nodes': nodes, 'edges': edges}
