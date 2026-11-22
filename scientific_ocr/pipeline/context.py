"""Per-page context shared by the page stages (immutable inputs + crop helper)."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import config
from ..core import geometry as geo
from ..core.text import plain_text
from . import engine_json
from .align import align


@dataclass
class PageContext:
    out: Path
    pdf: Path
    doc_sha: str
    page: int
    width: float
    height: float
    versions: dict
    rules: list
    marker: list = field(default_factory=list)          # engine blocks
    docling: list = field(default_factory=list)
    raw_files: dict = field(default_factory=dict)        # engine -> raw json path (relative)
    engines: dict = field(default_factory=dict)          # engine -> status record
    alignment: dict = field(default_factory=dict)
    median_density: float | None = None
    picture_boxes: list = field(default_factory=list)
    imaging: object = None                               # ports.PageImaging (injected by the application)

    @classmethod
    def load(cls, out: Path, pdf: Path, doc_sha: str, page: int, dims, ex_index: dict, versions: dict,
             rules: list, imaging) -> 'PageContext':
        ctx = cls(out, pdf, doc_sha, page, dims[0], dims[1], versions, rules)
        ctx.imaging = imaging
        raw = {}
        for eng in ('marker', 'docling'):
            rec = ex_index.get(eng, {}).get(str(page)) or ex_index.get(eng, {}).get(page)
            ctx.engines[eng] = ({k: rec.get(k) for k in ('status', 'exit_code', 'raw_json', 'log', 'seconds',
                                                       'cache_key', 'profile')} if rec else {'status': 'MISSING'})
            if rec and rec['status'] == 'OK':
                try:
                    raw[eng] = json.loads((out / rec['raw_json']).read_text())
                    ctx.raw_files[eng] = rec['raw_json']
                except (OSError, json.JSONDecodeError) as exc:
                    ctx.engines[eng].update(status='RAW_UNPARSEABLE', error=str(exc))
        for eng, fn in (('marker', lambda r: engine_json.marker_blocks(r, page)),
                        ('docling', lambda r: engine_json.docling_blocks(r, page, ctx.height))):
            if eng in raw:
                try:
                    setattr(ctx, eng, fn(raw[eng]))
                except (ValueError, KeyError, TypeError) as exc:
                    ctx.engines[eng].update(status='NORMALIZE_FAILED', error=str(exc))
            ctx.engines[eng]['blocks'] = len(getattr(ctx, eng))
        ctx.alignment = align(ctx.marker, ctx.docling)
        dens = [len(plain_text(b['text'])) / geo.area(b['bbox']) for b in ctx.marker
                if b['kind'] == 'paragraph' and b['text'] and geo.area(b['bbox']) > ctx.min_paragraph_area]
        ctx.median_density = float(np.median(dens)) if dens else None
        ctx.picture_boxes = [b['bbox'] for b in ctx.marker + ctx.docling if b['kind'] in ('figure', 'molecular_structure')]
        return ctx

    @property
    def unit(self) -> float:
        return geo.unit(self.width, self.height)

    @property
    def min_paragraph_area(self) -> float:          # paragraphs large enough for a density estimate
        return 20000 * self.unit ** 2

    def crop(self, bid: str, bbox, pad: float = config.CROP_PAD_PT) -> dict:
        """`pad` in page units (see geometry.unit). Content-addressed: the crop and its measurements are reused when
        (source, page, box, pad, render zoom) are unchanged — a re-projection after a reviewer click re-renders
        nothing that did not change."""
        dest = self.out / 'assets' / f'p{self.page:04d}' / f'{bid}.png'
        key = json.dumps([self.doc_sha, self.page, [round(v, 2) for v in bbox], round(pad * self.unit, 3),
                          config.RENDER_ZOOM, 'crop-v1'])
        meta = dest.with_suffix('.json')
        try:
            hit = json.loads(meta.read_text())
            if hit.get('key') == key and dest.exists():
                return hit['rec']
        except (OSError, ValueError):
            pass
        rec = self.imaging.crop(self.pdf, self.page, bbox, dest, pad=pad * self.unit)
        rec['path'] = str(Path(rec['path']).relative_to(self.out))
        rec['ink_fraction'] = round(self.imaging.ink_fraction(self.out / rec['path']), 4)
        rec['paper_tone'] = self.imaging.paper_tone(self.out / rec['path'])
        tmp = meta.with_name(meta.name + f'.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp')
        tmp.write_text(json.dumps({'key': key, 'rec': rec}))
        tmp.replace(meta)
        return rec

    def typography(self, blk: dict) -> None:
        """blk['typography'] measured on its crop; text_h also in page units (comparable across pages and books)."""
        sc = blk.get('source_crop') or {}
        if not sc.get('path') or not sc.get('zoom') or not hasattr(self.imaging, 'text_metrics'):
            return
        m = self.imaging.text_metrics(self.out / sc['path'], sc['zoom'])
        if m:
            m['text_u'] = round(m['text_h'] / self.unit, 2)
            blk['typography'] = m

    BODY_MIN_CHARS = 250          # a paragraph this long is body text: the page's typographic reference
    BODY_SAMPLES = 4

    def body_typography(self, blocks: list[dict]) -> dict | None:
        """The page's BODY text as measured on the scan (median of its longest paragraphs): line height, stroke
        weight inside lines, ink colour. Headings are judged relative to it, so a photo taken closer or further,
        a low-resolution scan, a small page or a book set in small type does not change what a style means."""
        body = sorted((b for b in blocks if b['kind'] == 'paragraph' and b['chosen'].get('type') == 'text'
                       and len(b['chosen'].get('text') or '') >= self.BODY_MIN_CHARS and not b.get('role')),
                      key=lambda b: -len(b['chosen']['text']))[:self.BODY_SAMPLES]
        ms = []
        for b in body:
            sc = b.get('source_crop') or {}
            if sc.get('path') and sc.get('zoom') and hasattr(self.imaging, 'text_metrics'):
                m = self.imaging.text_metrics(self.out / sc['path'], sc['zoom'])
                if m and m.get('lines', 0) >= 2:
                    ms.append(m)
        if not ms:
            return None
        med = lambda k: sorted(m[k] for m in ms)[len(ms) // 2]
        rgb = [sorted(m['ink_rgb'][i] for m in ms)[len(ms) // 2] for i in range(3)]
        return {'text_h': med('text_h'), 'text_u': round(med('text_h') / self.unit, 2), 'band_px': med('band_px'),
                'density': med('density'), 'ink_rgb': rgb, 'sat': med('sat'), 'samples': len(ms)}

    def component(self, ref: str) -> dict:
        return self.alignment['components'][self.alignment['by_ref'][ref]]

    def docling_by_ref(self) -> dict:
        return {b['ref']: b for b in self.docling}

    def candidate(self, b: dict) -> dict:
        eng = b['engine']
        c = {'engine': eng, 'version': self.versions.get(eng), 'ref': b['ref'], 'label': b['label'],
             'raw_file': self.raw_files.get(eng), 'raw_pointer': b['raw_pointer'], 'text': b['text'],
             'bbox': b['bbox'], 'confidence': None}
        if eng == 'marker':
            c['latex'] = b['latex']
        if b['extra'].get('classification'):
            c['classification'] = b['extra']['classification']
        return c
