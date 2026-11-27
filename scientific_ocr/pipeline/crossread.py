"""Stage (after extraction, before composition): CROSS-READING of regions only one detector found.

Marker and Docling each detect AND read a page in one run; the alignment (pipeline.align) then shows boxes only one
of them found. Each such box is read by the engine that missed it — the same crop OCR as a reviewer region
(adapters.extraction.regions_ocr: one batched run, content-addressed cache) — so when the reviewer takes over every
region already carries every engine's reading, and the agreement checks judge it like any other segment
(pipeline.page: _from_marker, _docling_only_read, _uncovered).

Who reads what: a box only Docling found -> Marker. A box only Marker found -> Tesseract directly on the crop (it is
Docling's OCR engine here; Docling run on a crop of one paragraph detects no layout and reads nothing — measured on
CHAPTER_5).

Only boxes an ENGINE found: ink no engine boxed (pipeline.coverage) is never read again — most of it is noise (dots,
rules, picture fragments) that the review load work deliberately keeps quiet (pipeline.fills).

Targets are keyed by the id of the segment composition will make of them, so composition finds their readings.
"""
from __future__ import annotations

from ..core import geometry as geo
from ..core.text import plain_text
from .context import PageContext
from .page import docling_id, docling_only_blocks, marker_id
from . import engine_json

PAD_UNITS = 2.0                     # engines under-report extents: the crop read gets a small margin
TEXTUAL = ('paragraph', 'heading', 'caption')


def _read_box(ctx: PageContext, bbox, engines) -> list[float]:
    """The box read: a layout engine (Marker) gets a margin; Tesseract reads exactly the box, as a margin brings
    in pieces of the neighbouring lines (measured: CHAPTER_5 p3)."""
    p = PAD_UNITS * ctx.unit if 'marker' in engines else 0.0
    return [round(max(0.0, bbox[0] - p), 2), round(max(0.0, bbox[1] - p), 2),
            round(min(ctx.width, bbox[2] + p), 2), round(min(ctx.height, bbox[3] + p), 2)]


def targets(ctx: PageContext) -> list[dict]:
    """Regions of the page one detector found, with the engines that should read them:
    [{id, page, bbox, read_bbox, source, engines, force_block}]."""
    out = []

    def add(bid, bbox, source, engines, force):
        out.append({'id': bid, 'page': ctx.page, 'bbox': [round(v, 2) for v in bbox], 'read_bbox': _read_box(ctx, bbox, engines),
                    'source': source, 'engines': list(engines), 'force_block': force})
    if ctx.engines.get('marker', {}).get('status') == 'OK':
        for d in docling_only_blocks(ctx):            # Marker missed it
            force = engine_json.DOCLING_TO_MARKER.get(d['label'], 'Text')
            if force not in engine_json.IMAGE_ONLY_LABELS:
                add(docling_id(ctx, d), d['bbox'], 'docling', ['marker'], force)
    if ctx.engines.get('docling', {}).get('status') == 'OK' and ctx.quick_ocr is not None:
        for eb in ctx.marker:                         # Docling missed it
            comp = ctx.component(eb['ref'])
            inside_figure = any(geo.covered_by(eb['bbox'], fb) > 0.6 for fb in ctx.picture_boxes if fb != eb['bbox'])
            if comp['status'] == 'single_engine' and eb['kind'] in TEXTUAL and plain_text(eb['text']) \
                    and not inside_figure and geo.area(eb['bbox']) >= 150 * ctx.unit ** 2:
                add(marker_id(ctx, eb), eb['bbox'], 'marker', ['tesseract'], eb['label'] if eb['label'] in engine_json.MARKER_KIND else 'Text')
    return out


def tesseract(ctx: PageContext, t: dict) -> dict:
    """Tesseract's reading of the target's crop (exactly its read box): an engine record like the others."""
    png = ctx.out / 'cache' / 'crossread' / f"{t['id']}.png"
    try:
        png.parent.mkdir(parents=True, exist_ok=True)
        ctx.imaging.crop(ctx.pdf, ctx.page, t['read_bbox'], png, pad=0)
        text = ctx.quick_ocr(png, ctx.ocr_lang)
    except (OSError, ValueError) as exc:
        return {'engine': 'tesseract', 'status': 'FAILED', 'error': str(exc)}
    finally:
        png.unlink(missing_ok=True)
    return {'engine': 'tesseract', 'status': 'OK' if text is not None else 'UNAVAILABLE', 'text': text or '',
            'lang': ctx.ocr_lang}
