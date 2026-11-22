"""Stage: content checks as a Strategy registry over a uniform Evidence record.

A check is `check(blk, ev) -> None`; it only *observes* and calls `reasons.flag`. What a flag means (review
level, crop visibility, audit finding) is defined once in core.reasons. Evidence is built the same way for
engine blocks and for reviewer-drawn regions, so every source gets identical scrutiny.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core import geometry as geo
from ..core import symbols
from ..core.reasons import flag

INCOMPLETE_DENSITY_RATIO = 0.45
STRONG_CONFLICT_SIMILARITY = 0.35
EVIDENCE_MIN_SIMILARITY = 0.5           # a second reading counts as evidence only if broadly aligned


@dataclass(frozen=True)
class Evidence:
    kind: str                     # engine-level kind of the primary reading
    latex: list                   # inline/display LaTeX spans of the primary reading
    agreement: str                # matched | conflict | single_engine | matched_geometry_only
    similarity: float | None      # primary vs secondary text similarity
    second_text: str              # independent reading of the same pixels ('' if none)
    inside_figure: bool = False   # box lies within a picture region
    density_ratio: float | None = None   # chars/area relative to the page median (None = not applicable)


def figure_internal_text(blk, ev: Evidence):
    if ev.inside_figure and ev.kind in ('paragraph', 'heading', 'caption'):
        if ev.agreement != 'matched' or (ev.similarity or 0) < EVIDENCE_MIN_SIMILARITY:
            blk['chosen'] = {'type': 'image_only', 'provenance': 'source_crop', 'withheld_text': blk['chosen']['text']}
            flag(blk, 'FIGURE_INTERNAL_TEXT_WITHHELD')
        else:
            flag(blk, 'FIGURE_INTERNAL_TEXT')


def inline_notation(blk, ev: Evidence):
    if ev.latex:
        blk['inline_latex'] = ev.latex
        flag(blk, 'INLINE_NOTATION_UNVERIFIED')
        for issue in sorted({i for t in ev.latex for i in symbols.glyph_issues(t)}):
            flag(blk, issue, latex=ev.latex)


def symbol_readings(blk, ev: Evidence):
    """Cross-engine evidence first; the script-mix heuristic only when no usable second reading exists."""
    text = blk['chosen'].get('text') or ''
    if ev.second_text and (ev.similarity or 0) >= EVIDENCE_MIN_SIMILARITY:
        found = symbols.token_conflicts(text, ev.second_text)
        critical = [c for c in found if c['critical']]
        if critical:
            flag(blk, 'SYMBOL_READING_CONFLICT', conflicts=critical[:20])
        minor = [c for c in found if not c['critical'] and c['class'] != 'lookalike']
        if minor:                                   # evidence only: visible in review, does not escalate
            flag(blk, 'SYMBOL_READING_MINOR', conflicts=minor[:20])
        return
    mix = symbols.script_mix(text)
    if mix and mix['severe']:
        flag(blk, 'SCRIPT_MIX_UNVERIFIED', **mix)


def text_density(blk, ev: Evidence):
    if ev.density_ratio is not None:
        blk['text_density_ratio'] = round(ev.density_ratio, 2)
        if ev.density_ratio < INCOMPLETE_DENSITY_RATIO:
            flag(blk, 'TEXT_POSSIBLY_INCOMPLETE', ratio=round(ev.density_ratio, 2))


def engine_agreement(blk, ev: Evidence):
    if ev.agreement == 'conflict':
        strong = (ev.similarity or 0) < STRONG_CONFLICT_SIMILARITY
        flag(blk, 'ENGINE_TEXT_CONFLICT_STRONG' if strong else 'ENGINE_TEXT_CONFLICT')
    elif ev.agreement == 'single_engine':
        flag(blk, 'SINGLE_ENGINE_MARKER')


def table_structure(blk, ev: Evidence):
    if ev.kind == 'table':
        flag(blk, 'TABLE_STRUCTURE_UNVERIFIED')


TEXT_CHECKS = [figure_internal_text, inline_notation, symbol_readings, text_density, engine_agreement,
               table_structure]


def run_text_checks(blk: dict, ev: Evidence):
    for check in TEXT_CHECKS:
        check(blk, ev)


LAYOUT_TROUBLE = {'TEXT_POSSIBLY_INCOMPLETE', 'EMPTY_OCR_TEXT', 'ENGINE_TEXT_CONFLICT_STRONG',
                  'UNCOVERED_INK_REGION', 'SINGLE_ENGINE_DOCLING'}


def layout_splits(page_blocks: list[dict], page_width: float) -> list[dict]:
    """Page-level check: neighbouring boxes on the same text lines, at least one showing OCR trouble, means a
    paragraph was probably cut by the layout model (e.g. an L-shaped paragraph around a figure). Returns merge
    suggestions; the reviewer accepts one with a click (-> reviewer region + region OCR)."""
    in_figure = {'FIGURE_INTERNAL_TEXT', 'FIGURE_INTERNAL_TEXT_WITHHELD'}
    textual = [b for b in page_blocks if b['kind'] in ('paragraph', 'excerpt', 'heading') and b['source'] != 'region'
               and not in_figure & set(b['reasons'])]
    out = []
    for i, a in enumerate(textual):
        for b in textual[i + 1:]:
            if not (set(a['reasons']) | set(b['reasons'])) & LAYOUT_TROUBLE:
                continue
            ba, bb_ = a['geometry']['bbox'], b['geometry']['bbox']
            ha, hb = ba[3] - ba[1], bb_[3] - bb_[1]
            similar_height = min(ha, hb) / max(ha, hb, 1e-6) >= 0.6     # halves of one paragraph share its lines
            if similar_height and geo.side_by_side(ba, bb_, max_gap=0.04 * page_width):
                bbox = geo.union([a['geometry']['bbox'], b['geometry']['bbox']])
                out.append({'kind': 'merge', 'blocks': [a['id'], b['id']], 'bbox': [round(v, 1) for v in bbox]})
                for blk in (a, b):
                    flag(blk, 'LAYOUT_SPLIT_SUSPECTED', suggestion=[a['id'], b['id']])
    return out
