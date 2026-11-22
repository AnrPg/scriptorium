"""Canonical block model: kinds, states, identities and derived statuses.

Blocks are plain JSON-serialisable dicts (the canonical.json contract, validated by a JSON schema in
publish.audit) built only through `new_block` so every block has the same shape.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

KINDS = ('heading', 'paragraph', 'caption', 'table', 'figure', 'chart', 'math', 'chemical_reaction',
         'nuclear_reaction', 'molecular_structure', 'reaction_scheme', 'lewis_structure', 'excerpt', 'unknown')
FIGURE_KINDS = {'figure', 'chart', 'molecular_structure', 'reaction_scheme', 'lewis_structure'}
CAPTIONABLE_KINDS = FIGURE_KINDS | {'table'}
EQUATION_KINDS = {'math', 'chemical_reaction', 'nuclear_reaction'}   # content is LaTeX when transcribed
CHEMICAL_EQUATION_KINDS = {'chemical_reaction', 'nuclear_reaction'}
# Review priority (lower first): where errors matter most — formulas, then structure-bearing text, prose, drawings,
# and last what is already safe as an image. Defined for EVERY kind (tested).
REVIEW_PRIORITY = {**{k: 0 for k in EQUATION_KINDS}, 'table': 1, 'caption': 1, 'heading': 1, 'paragraph': 2,
                   **{k: 3 for k in FIGURE_KINDS}, 'excerpt': 4, 'unknown': 4}
# 'accepted' = accepted statistically (its class passed a sampling audit); never counts as individually VERIFIED
REVIEW_STATES = ('not_needed', 'recommended', 'required', 'approved', 'image_only', 'unresolved', 'accepted')
RELEASE = ('DRAFT', 'REVIEW_REQUIRED', 'LLM_READY_WITH_EXCERPTS', 'VERIFIED')
MAX_HEADING_LEVEL = 9            # true depth in canonical; renderers cap at their own limit (Markdown: h6)


def chosen_text(b: dict) -> str:
    """The segment's text content (stripped) in the book's ONE notation (pipeline.notation: scripts as LaTeX), or ''
    when its content is LaTeX or an image. The source text as written stays in b['chosen']['text']."""
    if b['chosen'].get('type') != 'text':
        return ''
    return (b.get('text_norm') or b['chosen'].get('text') or '').strip()


def block_id(doc_sha: str, page: int, key: str) -> str:
    """Stable id: same source, page and engine/region key -> same id across rebuilds."""
    return f'p{page}_' + hashlib.sha1(f'{doc_sha}|{page}|{key}'.encode()).hexdigest()[:8]


def content_hash(chosen: dict) -> str:
    return hashlib.sha256(json.dumps(chosen, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def new_block(bid: str, page: int, kind: str, bbox, *, source: str, role=None, candidates=None,
              chosen=None, match=None, **extra) -> dict:
    assert kind in KINDS, kind
    b = {'id': bid, 'page': page, 'kind': kind, 'role': role, 'source': source,
         'geometry': {'bbox': [round(float(v), 2) for v in bbox], 'origin': 'TOPLEFT', 'units': 'pt'},
         'candidates': candidates or [], 'chosen': chosen or {'type': 'image_only', 'provenance': 'source_crop'},
         'match': match or {'status': 'no_engine', 'shape': '0:0', 'text_similarity': None},
         'relations': {}, 'reasons': [], 'review': 'not_needed', 'display_crop': False}
    b.update(extra)
    return b


def in_output(b: dict) -> bool:
    """Page furniture (pipeline.furniture) is kept in canonical.json with its evidence but is not content:
    every export, preview and status computation uses only the blocks for which this is true."""
    return not b.get('excluded')


def in_reader_output(b: dict) -> bool:
    """Content that reaches the READER exports (Markdown, PDF): not furniture and not omitted by the reviewer.
    An omitted segment ('omitted', decision set_output include=False) is real book content kept in the model —
    hierarchy, entities, review history, JSON exports (flagged) — that the reviewer chose to leave out of the text.
    It is not the same as hiding a box (block_hide: the box itself is wrong and leaves the page model)."""
    return in_output(b) and not b.get('omitted')


def release_status(blocks: list[dict], findings: list[dict]) -> str:
    blocks = [b for b in blocks if in_reader_output(b)]          # omitted content needs no review for release
    if any(f['severity'] == 'critical' and (f['type'].startswith('INTEGRITY') or f['type'] == 'ENGINE_FAILED')
           for f in findings):
        return 'DRAFT'
    states = Counter(b['review'] for b in blocks)
    if states.get('required') or states.get('unresolved'):
        return 'REVIEW_REQUIRED'
    if blocks and all(b['review'] in ('approved', 'image_only') for b in blocks):
        return 'VERIFIED'
    return 'LLM_READY_WITH_EXCERPTS'


def page_summary(blocks: list[dict]) -> dict:
    excluded = sum(1 for b in blocks if not in_output(b))
    omitted = sum(1 for b in blocks if in_output(b) and b.get('omitted'))
    blocks = [b for b in blocks if in_reader_output(b)]
    rv = Counter(b['review'] for b in blocks)
    if rv.get('required') or rv.get('unresolved'):
        state = 'needs_review'
    elif blocks and all(b['review'] in ('approved', 'image_only') for b in blocks):
        state = 'verified'
    else:
        state = 'llm_ready'
    return {'state': state, 'blocks': len(blocks), 'required': rv.get('required', 0),
            'recommended': rv.get('recommended', 0), 'decided': sum(1 for b in blocks if b.get('decisions')), 'excluded': excluded, 'omitted': omitted}
