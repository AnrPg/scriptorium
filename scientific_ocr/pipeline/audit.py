"""Stage (publish): deterministic auditors. They detect and escalate; they never 'verify' images.

Block-level findings come generically from the reason registry (core.reasons: Reason.finding). The functions
here add only cross-cutting checks: page/engine integrity, coverage, reading order, duplicates, asset
integrity, export integrity, and the canonical JSON schema.
"""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

import jsonschema

from ..core.model import KINDS, RELEASE, REVIEW_STATES, chosen_text, in_output, in_reader_output
from ..core.reasons import REASONS
from ..core.text import match_norm, strip_markup
from . import tables


def finding(ftype, severity, page, block_ids, reason, action, evidence=None) -> dict:
    fid = hashlib.sha1(f'{ftype}|{page}|{",".join(block_ids)}|{reason}'.encode()).hexdigest()[:10]
    return {'id': f'F{fid}', 'type': ftype, 'severity': severity, 'page': page, 'block_ids': block_ids,
            'reason': reason, 'recommended_action': action, 'evidence': evidence or {}}


HUMAN_SETTLED = ('approved', 'image_only', 'unresolved')


def from_reasons(blocks: list[dict]) -> list[dict]:
    """Block findings from the registry. Once a reviewer has settled a block, its findings stay on record
    as 'info' (resolved) instead of counting as open warnings/criticals."""
    F = []
    for b in blocks:
        settled = b['review'] in HUMAN_SETTLED and (b.get('decisions') or b['relations'].get('resolved_by'))
        waived = set(b.get('waived') or ())             # by the book's engine policy (pipeline.profile)
        out_of_text = in_output(b) and not in_reader_output(b)     # left out by the reviewer or the profile
        for code in b['reasons']:
            r = REASONS.get(code)
            if r and r.finding:
                quiet = settled or code in waived or out_of_text
                f = finding(r.finding[0], 'info' if quiet else r.finding[1], b['page'], [b['id']], r.en,
                            'resolved by reviewer' if settled else 'waived by the book policy' if code in waived
                            else 'not in the output' if out_of_text else 'review in app', (b.get('evidence') or {}).get(code))
                f['resolved'] = bool(quiet)
                F.append(f)
        if b['review'] == 'required':
            F.append(finding('REVIEW_REQUIRED', 'warning', b['page'], [b['id']], ','.join(b['reasons']), 'review in app'))
    return F


def pre_render(c: dict, out: Path) -> list[dict]:
    blocks, src = c['blocks'], c['source']
    F = from_reasons(blocks)
    have = {b['page'] for b in blocks}
    # a page left without segments by the REVIEWER (every box hidden or replaced) is a decision, not a loss
    emptied = {pg['page'] for pg in c['pages'] if pg.get('hidden') or pg.get('replaced_by_region')}
    for p in src['pages_processed']:
        if p not in have and p not in emptied:
            F.append(finding('INTEGRITY_PAGE_MISSING', 'critical', p, [], 'page produced no blocks', 'rerun page'))
    for pg in c['pages']:
        for eng, rec in pg['engines'].items():
            if rec.get('status') != 'OK':
                F.append(finding('ENGINE_FAILED', 'critical', pg['page'], [], f'{eng}: {rec.get("status")} '
                                 f'{rec.get("error", "")}', f'inspect {rec.get("log")}', rec))
        cov = pg.get('coverage') or {}
        li, lb = cov.get('lowest_ink_y'), cov.get('lowest_block_y')
        if li and lb and li - lb > 40 * cov.get('unit', 1.0):
            F.append(finding('COVERAGE_BOTTOM_INK_BELOW_LAST_BLOCK', 'warning', pg['page'], [],
                             f'ink {round(li - lb)}pt below the lowest engine block', 'check bottom of page', cov))
        F.append(finding('COVERAGE_DISCLAIMER', 'info', pg['page'], [],
                         'automatic coverage detection cannot guarantee every printed element was found',
                         'draw a region in the app if something is missing'))
        F += _reading_order(pg)
    F += _duplicates(blocks) + _assets(blocks, src, out)
    return F


def _reading_order(pg) -> list[dict]:
    def order(ref):
        m = re.findall(r'(\d+)', ref)
        return int(m[-1]) if m else 0
    comps = [x for x in pg.get('components', []) if x['marker'] and x['docling']]
    rank = {r: i for i, r in enumerate(sorted({x['marker'][0] for x in comps}, key=order))}
    pairs = sorted((rank[x['marker'][0]], order(x['docling'][0])) for x in comps)
    inv = sum(1 for i in range(len(pairs)) for j in range(i + 1, len(pairs)) if pairs[i][1] > pairs[j][1])
    tot = len(pairs) * (len(pairs) - 1) / 2 or 1
    if inv / tot > 0.15:
        return [finding('READING_ORDER_DISAGREEMENT', 'warning', pg['page'], [],
                        f'Marker and Docling orders disagree on {inv}/{int(tot)} pairs; Marker order used',
                        'check column order', {'inversions': inv, 'pairs': int(tot)})]
    return []


def _duplicates(blocks) -> list[dict]:
    F, seen = [], {}
    for b in filter(in_output, blocks):
        if b['kind'] in ('paragraph', 'heading', 'caption') and b['chosen'].get('text'):
            k = match_norm(b['chosen']['text'])
            if len(k) > 40 and k in seen:
                F.append(finding('DUPLICATE_TEXT', 'warning', b['page'], [seen[k], b['id']], 'identical text twice',
                                 'verify not a double extraction'))
            seen.setdefault(k, b['id'])
    return F


def _assets(blocks, src, out: Path) -> list[dict]:
    F, root = [], out.resolve()
    for b in blocks:
        sc = b.get('source_crop')
        if not sc:
            if b['kind'] == 'excerpt' or b['chosen']['type'] == 'image_only':
                F.append(finding('INTEGRITY_EXCERPT_WITHOUT_PNG', 'critical', b['page'], [b['id']],
                                 'image-only block has no crop', 'rebuild'))
            continue
        p = (out / sc['path']).resolve()
        if root not in p.parents:
            F.append(finding('INTEGRITY_UNSAFE_PATH', 'critical', b['page'], [b['id']], sc['path'], 'fix path'))
        elif not p.exists():
            F.append(finding('INTEGRITY_ASSET_MISSING', 'critical', b['page'], [b['id']], sc['path'], 'rebuild'))
        elif hashlib.sha256(p.read_bytes()).hexdigest() != sc['sha256']:
            F.append(finding('INTEGRITY_ASSET_HASH', 'critical', b['page'], [b['id']], 'crop hash mismatch', 'rebuild'))
        W, H = src['page_dims_pt'][str(b['page'])]
        x0, y0, x1, y1 = b['geometry']['bbox']
        if x0 < -1 or y0 < -1 or x1 > W + 1 or y1 > H + 1:
            F.append(finding('BBOX_OUTSIDE_PAGE', 'warning', b['page'], [b['id']], str(b['geometry']['bbox']), 'check'))
    return F


_LEAK = re.compile(r'\bp\d+_[0-9a-f]{8}\b|BLOCK_START|BLOCK_END|EXCERPT_START|provenance=|review=')


def _norm(t: str) -> str:
    """Comparable text: HTML entities decoded first (inside <figure>/<aside> the export escapes '<', '>', '&'),
    then markup stripped. Applied to both sides, so a block's text and the export compare as readers see them."""
    return strip_markup(html.unescape(t or ''))


def _snippet(t: str, size: int = 20) -> str | None:
    """A stretch from the middle of the text: joins and hyphen repairs only touch the ends of a part."""
    n = _norm(t)
    if len(n) < size + 4:
        return n or None
    i = len(n) // 3
    return n[i:i + size]


def post_render(c: dict, md: str, out: Path, images: bool = True) -> list[dict]:
    """Audit of the reader-facing Markdown: complete (every text, formula and image of the content is in it),
    clean (no ids, review marks or internal markers leak) and well-bounded (captions inside their figure,
    separators balanced). Excluded furniture must be recorded and must not appear."""
    F = []
    flat = _norm(md)
    by_id = {b['id']: b for b in c['blocks']}
    for b in c['blocks']:
        if not in_output(b):
            if not (b['excluded'].get('signals') and b.get('source_crop')):
                F.append(finding('INTEGRITY_EXCLUSION_UNRECORDED', 'critical', b['page'], [b['id']],
                                 'excluded without evidence or crop', 'fix furniture stage'))
            continue
        cap_of = by_id.get(b['relations'].get('caption_for'))
        if b.get('flow') == 'furniture' or not in_reader_output(b) or (cap_of is not None and not in_reader_output(cap_of)):
            continue                                       # the reviewer (or the output profile) left it out: not a loss
        ch = b['chosen']
        items = tables.as_list(b, chosen_text(b)) if ch['type'] == 'text' and b['kind'] == 'table' else None
        if items:                                          # a term grid read as a list: EVERY item must be there
            lost = [t for t in items if (sn := _norm(t).strip()) and sn not in flat]
            if lost:
                F.append(finding('INTEGRITY_CONTENT_NOT_EXPORTED', 'critical', b['page'], [b['id']],
                                 f'list items not in the export: {lost[:3]!r}', 'fix renderer'))
        elif ch['type'] == 'text':
            sn = _snippet(chosen_text(b))                  # the exported notation (pipeline.notation)
            if sn and sn not in flat:
                F.append(finding('INTEGRITY_CONTENT_NOT_EXPORTED', 'critical', b['page'], [b['id']],
                                 f'text not in the export: {sn!r}', 'fix renderer'))
        elif ch['type'] == 'latex':
            if (ch.get('latex') or '').strip() not in md:
                F.append(finding('INTEGRITY_CONTENT_NOT_EXPORTED', 'critical', b['page'], [b['id']], 'formula not in the export',
                                 'fix renderer'))
        elif images and b.get('source_crop') and b['source_crop']['path'] not in md:
            F.append(finding('INTEGRITY_CONTENT_NOT_EXPORTED', 'critical', b['page'], [b['id']], 'image not in the export',
                             'fix renderer'))
    for path in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', md):
        if path.startswith(('/', 'http:', 'https:')) or '..' in Path(path).parts or not (out / path).exists():
            F.append(finding('INTEGRITY_MD_IMAGE_UNRESOLVED', 'critical', None, [], f'image path {path}', 'fix'))
    for m in _LEAK.finditer(re.sub(r'!\[[^\]]*\]\([^)]+\)', '', md)):   # image targets are file paths of the book
        F.append(finding('EXPORT_INTERNAL_MARKUP', 'critical', None, [], f'internal marker in the export: {m.group(0)}',
                         'fix renderer'))
        break
    # Text outside every figure. Figures may NEST (a group of figures with its own caption) and carry attributes,
    # so innermost figures are removed repeatedly; a non-greedy <figure>.*?</figure> would stop at the inner
    # figure's end and leave the rest of the group "outside".
    outside, prev = md, None
    while outside != prev:
        prev = outside
        outside = re.sub(r'<figure\b[^>]*>(?:(?!<figure\b).)*?</figure>|<!-- figure -->(?:(?!<!-- figure -->).)*?<!-- /figure -->',
                         ' ', outside, flags=re.S)
    outside = _norm(outside)
    for b in c['blocks']:
        if b['kind'] == 'caption' and in_output(b) and b['relations'].get('caption_for'):
            sn = _snippet(b['chosen'].get('text'), 60)     # long enough not to be an ordinary phrase of the body
            if sn and len(sn) >= 40 and sn in outside:
                F.append(finding('CAPTION_BLEED', 'critical', b['page'], [b['id']], 'caption outside its figure', 'fix renderer'))
    for tag, o, e in (('figure', r'<figure\b[^>]*>', '</figure>'), ('aside', r'<aside\b[^>]*>', '</aside>'),
                      ('<!-- figure -->', r'<!-- figure -->', '<!-- /figure -->')):
        n_open, n_close = len(re.findall(o, md)), md.count(e)          # openings may carry attributes
        if n_open != n_close:
            F.append(finding('INTEGRITY_UNBALANCED_BOUNDARY', 'critical', None, [], f'{tag}: {n_open} vs {n_close}', 'fix'))
    return F


SCHEMA = {
    'type': 'object',
    'required': ['schema_version', 'document_id', 'source', 'run', 'pages', 'blocks', 'findings', 'release_status'],
    'properties': {
        'release_status': {'enum': list(RELEASE)},
        'blocks': {'type': 'array', 'items': {
            'type': 'object',
            'required': ['id', 'page', 'kind', 'source', 'reading_order', 'geometry', 'chosen', 'candidates', 'review',
                         'reasons'],
            'properties': {
                'page': {'type': 'integer', 'minimum': 1},
                'kind': {'enum': list(KINDS)},
                'reading_order': {'type': 'integer', 'minimum': 1},
                'geometry': {'type': 'object', 'required': ['bbox', 'origin'],
                             'properties': {'bbox': {'type': 'array', 'minItems': 4, 'maxItems': 4},
                                            'origin': {'const': 'TOPLEFT'}}},
                'chosen': {'type': 'object', 'required': ['type'],
                           'properties': {'type': {'enum': ['text', 'latex', 'image_only', 'structure', 'unresolved']}}},
                'review': {'enum': list(REVIEW_STATES)},
                'reasons': {'type': 'array', 'items': {'enum': list(REASONS)}},
            },
            'allOf': [{'if': {'properties': {'kind': {'const': 'excerpt'}}}, 'then': {'required': ['source_crop']}}],
        }},
    },
}


def validate_schema(c: dict):
    jsonschema.validate(c, SCHEMA)
