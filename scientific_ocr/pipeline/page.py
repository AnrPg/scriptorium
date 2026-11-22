"""Stage: assemble one page's blocks from engine output, coverage analysis and reviewer layout edits.

Order of sources (all end up in one reading-ordered list):
  1. Marker blocks (primary reading order), each routed by kind and checked (pipeline.checks)
  2. Docling-only regions (Marker missed them)          -> visible excerpts with Docling text as candidate
  3. Printed ink no engine covered                      -> visible excerpts
  4. Conservative full-page excerpt if Marker failed
  5. Reviewer regions (layout corrections)              -> replace the engine boxes they cover
Hidden blocks (reviewer) are removed from the flow but listed in page info, so nothing disappears silently.
"""
from __future__ import annotations

import json
import os
import uuid

from ..core import chem
from ..core import geometry as geo
from ..core import rules as book_rules
from ..core.model import block_id, content_hash, new_block
from ..core.reasons import flag
from ..core.symbols import strip_math_delimiters
from ..core.text import plain_text, similarity
from . import engine_json
from .checks import Evidence, layout_splits, run_text_checks
from .context import PageContext
from .coverage import uncovered_ink_regions


def assemble(ctx: PageContext, regions: list[dict], region_index: dict, hidden: set) -> tuple[list, dict]:
    seq = [_from_marker(ctx, eb) for eb in ctx.marker]
    for blk in _docling_only(ctx):
        seq.insert(_index(seq, blk), blk)
    regs, cov = _coverage(ctx)
    for r in regs:
        blk = _uncovered(ctx, r)
        seq.insert(_index(seq, blk), blk)
    if not ctx.marker or ctx.engines['marker'].get('status') != 'OK':
        seq.insert(0, _full_page(ctx))
    info = {'page': ctx.page, 'engines': ctx.engines, 'coverage': cov, 'median_text_density': ctx.median_density,
            'components': [{k: c[k] for k in ('marker', 'docling', 'status', 'shape', 'text_similarity')}
                           for c in ctx.alignment['components']],
            'replaced_by_region': [], 'hidden': []}
    # Regions apply in event order: a later region supersedes whatever it covers, engine blocks AND earlier
    # reviewer regions (re-tagging or redrawing the same box never leaves duplicates behind), and takes their place.
    for reg in [r for r in regions if int(r['page']) == ctx.page]:
        rblk = _from_region(ctx, reg, region_index.get(reg['id']))
        keep, first = [], None
        for b in seq:
            if geo.covered_by(b['geometry']['bbox'], reg['bbox']) >= 0.6:
                info['replaced_by_region'].append({'block_id': b['id'], 'region': reg['id'],
                                                   'bbox': b['geometry']['bbox'],
                                                   'superseded_region': b.get('region_id')})
                first = len(keep) if first is None else first
            else:
                keep.append(b)
        seq = keep
        # Order preservation: a region that replaces segments takes the reading position of the first one it
        # replaces (re-tagging, redrawing or re-OCR never moves content); only a region over new ground is placed
        # by geometry.
        seq.insert(first if first is not None else _index(seq, rblk), rblk)
    # hidden by the reviewer: never in the output, but what was printed there stays EVIDENCE for book-wide
    # inference (a hidden chapter outline still tells which titles are siblings)
    info['hidden'] = [{'block_id': b['id'], 'bbox': b['geometry']['bbox'], 'kind': b['kind'],
                       'text': (b['chosen'].get('text') if b['chosen'].get('type') == 'text' else None)
                       or next((c['text'] for c in b.get('candidates', []) if (c.get('text') or '').strip()), '')}
                      for b in seq if b['id'] in hidden]
    seq = [b for b in seq if b['id'] not in hidden]
    info['layout_splits'] = layout_splits(seq, ctx.width)
    for i, b in enumerate(seq, 1):
        b['reading_order'] = i
        if _typography_relevant(b):
            ctx.typography(b)
    info['typography_ref'] = ctx.body_typography(seq)
    return seq, info


TYPO_MAX_CHARS = 220


def _typography_relevant(b) -> bool:
    """Headings, captions and short lines (possible unrecognised headings): their print size is evidence for the
    book-wide hierarchy (pipeline.sections)."""
    t = (b['chosen'].get('text') or '') if b['chosen'].get('type') == 'text' else ''
    return b['kind'] in ('heading', 'caption') or (b['kind'] == 'paragraph' and 0 < len(t) <= TYPO_MAX_CHARS * 3)


# ---------------------------------------------------------------- helpers
def _coverage(ctx: PageContext):
    """Uncovered ink, cached by content (source, page, engine boxes, analysis parameters): every re-projection after
    a reviewer click would otherwise re-rasterise every page."""
    import hashlib

    from . import coverage
    boxes = [[round(v, 1) for v in b['bbox']] for b in ctx.marker + ctx.docling]
    key = hashlib.sha1(json.dumps([ctx.doc_sha, ctx.page, boxes, round(ctx.unit, 4), coverage.CELL_UNITS,
                                   coverage.MIN_AREA_UNITS, coverage.BOX_PAD_UNITS, 'ink-v1']).encode()).hexdigest()[:20]
    f = ctx.out / 'cache' / 'coverage' / f'{key}.json'
    try:
        hit = json.loads(f.read_text())
        return hit['regions'], hit['info']
    except (OSError, ValueError, KeyError):
        pass
    regs, info = uncovered_ink_regions(ctx.imaging, ctx.pdf, ctx.page, boxes, ctx.unit)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + f'.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp')
    tmp.write_text(json.dumps({'regions': regs, 'info': info}))
    tmp.replace(f)
    return regs, info



def _index(seq, blk) -> int:
    return geo.insertion_index([b['geometry'].get('engine_bbox') or b['geometry']['bbox'] for b in seq],
                               blk['geometry']['bbox'])


def _set_text(ctx: PageContext, blk: dict, text: str, provenance: str, second_text: str = ''):
    """Machine text -> book rules -> chosen. machine_hash identifies the raw engine choice for decisions.
    Rules skip occurrences that the independent reading confirms (see core.rules.apply)."""
    blk['chosen'] = {'type': 'text', 'text': text, 'provenance': provenance}
    blk['machine_hash'] = content_hash(blk['chosen'])
    fixed, changes = book_rules.apply(text, ctx.rules, blk['id'], second_text)
    if changes:
        blk['chosen'] = {'type': 'text', 'text': fixed, 'provenance': provenance + ' + book rules'}
        blk['rule_corrections'] = changes
        flag(blk, 'BOOK_RULE_APPLIED')


def _from_marker(ctx: PageContext, eb: dict) -> dict:
    comp = ctx.component(eb['ref'])
    dref = ctx.docling_by_ref()
    dcs = [dref[r] for r in comp['docling']]
    bid = block_id(ctx.doc_sha, ctx.page, eb['ref'] + json.dumps([round(v) for v in eb['bbox']]))
    # Conservative geometry: union with matched Docling boxes (engines under-report extents); the engine's
    # own box is kept for column-aware placement.
    geom = eb['bbox']
    if comp['status'] in ('matched', 'matched_geometry_only') and len(comp['marker']) == 1 and dcs:
        geom = geo.union([eb['bbox']] + [d['bbox'] for d in dcs])
    blk = new_block(bid, ctx.page, eb['kind'] if eb['kind'] != 'unknown' else 'paragraph', geom, source='marker',
                    role=eb['role'], engine_label=f"marker:{eb['label']}",
                    candidates=[ctx.candidate(eb)] + [ctx.candidate(d) for d in dcs],
                    match={'status': comp['status'], 'shape': comp['shape'], 'text_similarity': comp['text_similarity']},
                    relations={'marker_group': eb['group']})
    blk['geometry']['engine_bbox'] = [round(v, 2) for v in eb['bbox']]
    levels = {d['engine']: d['extra']['heading_level'] for d in [eb] + dcs if d['extra'].get('heading_level')}
    if levels:
        blk['engine_heading_levels'] = levels
    blk['source_crop'] = ctx.crop(bid, geom)
    latex = ' '.join(eb['latex'])
    if eb['kind'] == 'molecular_structure' and chem.is_equation(latex):
        _as_equation(blk, latex, eb['ref'], chemical=True)
    elif eb['kind'] in ('figure', 'molecular_structure'):
        _as_figure(ctx, blk, eb, dcs)
    elif eb['kind'] == 'math':
        _as_equation(blk, latex or eb['text'], eb['ref'], chemical=False)
    elif not plain_text(eb['text']):
        _as_empty(blk, eb['role'])
    else:
        ev = _marker_evidence(ctx, eb, comp, dcs)
        _set_text(ctx, blk, eb['text'], eb['ref'], ev.second_text)
        run_text_checks(blk, ev)
    return blk


def _marker_evidence(ctx: PageContext, eb: dict, comp: dict, dcs: list) -> Evidence:
    density = None
    if ctx.median_density and eb['kind'] == 'paragraph' and geo.area(eb['bbox']) > ctx.min_paragraph_area:
        density = (len(plain_text(eb['text'])) / geo.area(eb['bbox'])) / ctx.median_density
    return Evidence(kind=eb['kind'], latex=eb['latex'], agreement=comp['status'], similarity=comp['text_similarity'],
                    second_text=' '.join(d['text'] for d in dcs if d['text']),
                    inside_figure=any(geo.covered_by(eb['bbox'], fb) > 0.6 for fb in ctx.picture_boxes
                                      if fb != eb['bbox']),
                    density_ratio=density)


def _as_equation(blk, latex, ref, chemical: bool):
    blk['kind'] = 'chemical_reaction' if chemical else 'math'
    blk['chosen'] = {'type': 'latex', 'latex': strip_math_delimiters(latex), 'provenance': ref}
    blk['machine_hash'] = content_hash(blk['chosen'])
    blk['needs_unimernet'] = True
    blk['display_crop'] = True          # until independent agreement (enrich stage)
    if chemical:
        flag(blk, 'CHEM_EQUATION_NOTATION_UNVERIFIED')


def _as_figure(ctx, blk, eb, dcs):
    labels = [d['text'] for d in ctx.docling if d['kind'] != 'figure' and geo.covered_by(d['bbox'], eb['bbox']) > 0.8]
    cls = chem.classify_visual(labels + [eb['text']], [c for d in dcs for c in d['extra'].get('classification', [])])
    if eb['kind'] == 'molecular_structure' and cls['kind'] == 'figure':
        cls['kind'] = 'molecular_structure'     # Marker's own chemistry label wins over a generic picture class
    blk.update(kind=cls['kind'], classification=cls, figure_text_candidates=labels, display_crop=True)
    blk['chosen'] = {'type': 'image_only', 'provenance': 'source_crop'}
    blk['machine_hash'] = content_hash(blk['chosen'])
    if cls['kind'] == 'lewis_structure':
        flag(blk, 'LEWIS_DOTS_IMAGE_AUTHORITATIVE')
    elif cls['kind'] == 'molecular_structure':
        flag(blk, 'CHEM_STRUCTURE_IMAGE_AUTHORITATIVE')


def _as_empty(blk, role):
    blk['original_kind'] = blk['kind']
    blk['kind'] = 'excerpt'
    blk['machine_hash'] = content_hash(blk['chosen'])
    if role in ('page_header', 'page_footer'):
        flag(blk, 'RUNNING_HEADER_EMPTY')
    else:
        flag(blk, 'EMPTY_OCR_TEXT', review='required' if blk['source_crop']['ink_fraction'] > 0.005 else 'recommended')


def _docling_only(ctx: PageContext):
    for comp in ctx.alignment['components']:
        if comp['marker']:
            continue
        dref = ctx.docling_by_ref()
        for r in comp['docling']:
            d = dref[r]
            inside_figure = any(geo.covered_by(d['bbox'], fb) > 0.8 for fb in ctx.picture_boxes if fb != d['bbox'])
            if (inside_figure and d['kind'] != 'figure') or geo.area(d['bbox']) < 150 * ctx.unit ** 2:
                continue                       # figure labels are attached to the figure; specks ignored
            bid = block_id(ctx.doc_sha, ctx.page, r + json.dumps([round(v) for v in d['bbox']]))
            blk = new_block(bid, ctx.page, 'excerpt', d['bbox'], source='docling', role=d['role'],
                            engine_label=f"docling:{d['label']}", original_kind=d['kind'], candidates=[ctx.candidate(d)],
                            match={'status': 'single_engine', 'shape': '0:1', 'text_similarity': None})
            blk['source_crop'] = ctx.crop(bid, d['bbox'], pad=14)
            blk['machine_hash'] = content_hash(blk['chosen'])
            flag(blk, 'SINGLE_ENGINE_DOCLING')
            yield blk


def _uncovered(ctx, reg) -> dict:
    bid = block_id(ctx.doc_sha, ctx.page, 'uncovered' + json.dumps([round(v) for v in reg['bbox']]))
    blk = new_block(bid, ctx.page, 'excerpt', reg['bbox'], source='coverage', original_kind='unknown',
                    ink_cells=reg['ink_cells'])
    blk['source_crop'] = ctx.crop(bid, reg['bbox'], pad=16)
    blk['machine_hash'] = content_hash(blk['chosen'])
    flag(blk, 'UNCOVERED_INK_REGION')
    if reg['bbox'][1] > 0.75 * ctx.height:
        flag(blk, 'MISSING_BOTTOM_TEXT')
    return blk


def _full_page(ctx) -> dict:
    bid = block_id(ctx.doc_sha, ctx.page, 'fullpage')
    blk = new_block(bid, ctx.page, 'excerpt', [0, 0, ctx.width, ctx.height], source='fallback', original_kind='page')
    blk['source_crop'] = ctx.crop(bid, [0, 0, ctx.width, ctx.height], pad=0)
    blk['machine_hash'] = content_hash(blk['chosen'])
    flag(blk, 'CONSERVATIVE_FULL_PAGE_EXCERPT')
    flag(blk, 'OCR_FAILED')
    return blk


def _from_region(ctx: PageContext, reg: dict, ocr: dict | None) -> dict:
    """Reviewer-drawn box. Its OCR ran with layout detection disabled, so it cannot be split again."""
    label = engine_json.region_label(reg)
    spec = engine_json.region_spec(label)
    kind = spec['kind'] if spec['kind'] != 'unknown' else 'paragraph'
    bid = block_id(ctx.doc_sha, ctx.page, 'region:' + reg['id'])
    blk = new_block(bid, ctx.page, kind, reg['bbox'], source='region', region_id=reg['id'], role=spec['role'],
                    engine_label=f'{label["engine"]}:{label["name"]}')
    blk['source_crop'] = ctx.crop(bid, reg['bbox'], pad=4)
    flag(blk, 'HUMAN_REGION')
    if spec['image_only'] or (kind in ('figure', 'molecular_structure') and not ocr):
        blk['display_crop'] = True
        blk['machine_hash'] = content_hash(blk['chosen'])
        if kind == 'molecular_structure':
            flag(blk, 'CHEM_STRUCTURE_IMAGE_AUTHORITATIVE')
        return blk
    texts = _region_texts(ctx, ocr) if ocr else None
    if texts is None:
        blk['machine_hash'] = content_hash(blk['chosen'])
        flag(blk, 'REGION_OCR_PENDING' if not ocr else 'REGION_OCR_FAILED')
        return blk
    blk['candidates'] = texts['candidates']
    mk, dl = texts['marker'], texts['docling']
    sim = similarity(mk['text'], dl['text']) if mk['text'] and dl['text'] else None
    blk['match'] = {'status': 'matched' if (sim or 0) >= 0.6 else ('conflict' if sim is not None else 'single_engine'),
                    'shape': 'region', 'text_similarity': sim}
    latex = ' '.join(mk['latex'])
    # a molecular drawing becomes an equation only when its LaTeX IS a chemical equation (as in _from_marker);
    # otherwise the drawing keeps image authority below
    if latex and (kind == 'math' or (kind == 'molecular_structure' and chem.is_equation(latex))):
        _as_equation(blk, latex, 'region:' + reg['id'], chemical=chem.is_equation(latex))
        return blk
    if kind == 'molecular_structure':                  # a drawing: the image is the content
        blk['display_crop'] = True
        blk['machine_hash'] = content_hash(blk['chosen'])
        flag(blk, 'CHEM_STRUCTURE_IMAGE_AUTHORITATIVE')
        return blk
    if not plain_text(mk['text']):
        blk['machine_hash'] = content_hash(blk['chosen'])
        flag(blk, 'REGION_OCR_FAILED')
        return blk
    _set_text(ctx, blk, mk['text'], 'region:' + reg['id'], dl['text'])
    # The reviewer drew this box deliberately: no figure-containment or density heuristics apply.
    run_text_checks(blk, Evidence(kind=kind, latex=mk['latex'], agreement=blk['match']['status'], similarity=sim,
                                  second_text=dl['text']))
    return blk


def _region_texts(ctx: PageContext, ocr: dict) -> dict | None:
    engines = ocr.get('engines', {})
    if any(engines.get(e, {}).get('status') != 'OK' for e in ('marker', 'docling')) and \
            engines.get('marker', {}).get('status') != 'OK':
        return None
    out, cands = {}, []
    for eng in ('marker', 'docling'):
        rec = engines.get(eng, {})
        texts, latex = [], []
        if rec.get('status') == 'OK':
            raw = json.loads((ctx.out / rec['raw_json']).read_text())
            blocks = engine_json.marker_blocks(raw, 1) if eng == 'marker' else \
                engine_json.docling_blocks(raw, 1, ocr['height'])
            for b in sorted(blocks, key=lambda b: b['order']):
                if b['text'] and b['kind'] != 'figure':
                    texts.append(b['text'])
                latex += b['latex']
        out[eng] = {'text': '\n'.join(texts), 'latex': latex}
        cands.append({'engine': eng, 'version': rec.get('version'), 'ref': f'region:{eng}', 'status': rec.get('status'),
                      'raw_file': rec.get('raw_json'), 'text': out[eng]['text'], 'latex': latex,
                      'bbox': None, 'confidence': None})
    return {**out, 'candidates': cands}
