"""Use cases — exports: reader-facing documents and data, with validated content options."""
from __future__ import annotations

import re

from ...core.vocabulary import FAMILIES, PLACEMENTS, TYPES, valid_entity_type
from ...pipeline import markdown
from .. import exports
from .base import PROFILES, ServiceBase, ServiceError, _now  # noqa: F401


class ExportService(ServiceBase):
    # ---------------------------------------------------------------- export
    def export(self, book_id: str, fmt: str, style: str = 'tags', options: dict | None = None) -> tuple[bytes, str, str]:
        b = self._book(book_id)
        c = b.canonical()
        if not c:
            raise ServiceError('nothing processed yet')
        if fmt not in exports.EXPORTERS:
            raise ServiceError(f'format must be one of {sorted(exports.EXPORTERS)}')
        if style not in markdown.STYLES:
            raise ServiceError(f'style must be one of {sorted(markdown.STYLES)}')
        data, ctype, ext = exports.export(b.dir, c, fmt, style, _export_options(options))
        return data, ctype, (re.sub(r'[^\w.-]+', '_', b.settings()['title'])[:60] or b.id) + ext


def _entity_types() -> dict:
    return {'types': {k: {'family': f, 'el': el, 'en': en} for k, (f, el, en) in TYPES.items()},
            'families': {k: {'el': el, 'en': en} for k, (el, en) in FAMILIES.items()},
            'placements': {k: el for k, (el, en) in PLACEMENTS.items()}}


def _export_options(o: dict | None) -> dict:
    """Validated reader-export options (see pipeline.reading): entity exclusions and moves by type or
    family:<family> [@placement],
    'figures' (captioned visuals) in moves, images kept or dropped for moved figures, unit level."""
    o = o or {}

    def entity_filters(vals, allow_figures=False):
        out = set()
        for x in vals or ():
            t, _, pl = str(x).partition('@')
            if not (allow_figures and x in ('figures', '*@margin')) and (not valid_entity_type(t) or (pl and pl not in PLACEMENTS)):
                raise ServiceError(f'unknown entity filter {x!r}')
            out.add(x)
        return out
    move = entity_filters(o.get('move'), allow_figures=True)
    images = o.get('images') or 'keep'
    if images not in ('keep', 'drop'):
        raise ServiceError('images must be keep or drop')
    level = int(o.get('unit_level') or 1)
    if not 1 <= level <= 6:
        raise ServiceError('unit_level 1..6')
    return {'exclude': entity_filters(o.get('exclude')), 'move': move, 'images': images, 'unit_level': level}

