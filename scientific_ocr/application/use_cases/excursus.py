"""Use cases — excursus (παρέκβαση): intercalated stretches of the book (pipeline.excursus).

The reviewer marks the two ENDS (first and last segment, on any pages) or whole pages; changes of type, name or
placement rewrite the same excursus (same id). Every action is one undoable event.
"""
from __future__ import annotations

import uuid

from ...core.vocabulary import EXCURSUS_PLACEMENTS, EXCURSUS_TYPES
from .base import ServiceBase, ServiceError


class ExcursusService(ServiceBase):
    def _order(self, b) -> dict[str, tuple]:
        """Book reading order of every segment the reviewer sees (page previews included); furniture excluded."""
        return {x['id']: (x['page'], x.get('reading_order') or 0) for x in self._seen_list(b) if not x.get('excluded')}

    def _auto(self, b, excursus_id: str | None) -> dict | None:
        """An automatic excursus of the last build (pipeline.excursus.from_entities): changing it makes it yours."""
        return next(({k: x[k] for k in ('id', 'first', 'last', 'type', 'name', 'placement')}
                     for x in (b.canonical() or {}).get('excursus') or []
                     if excursus_id and x['id'] == excursus_id and x.get('source') == 'entity'), None)

    def set_excursus(self, book_id: str, first: str | None = None, last: str | None = None, pages: list | None = None,
                     type: str = 'other', name: str = '', placement: str = 'inplace', excursus_id: str | None = None,
                     actor: str = 'reviewer', blocks: list | None = None) -> dict:
        """Create (or, with excursus_id, change) an excursus from its first to its last segment, over whole pages
        [first page, last page], or over a selection (its first to its last segment in reading order)."""
        b = self._book(book_id)
        st = {x['id']: x for x in b.events.state()['excursus']}
        old = (st.get(excursus_id) or self._auto(b, excursus_id)) if excursus_id else None
        if excursus_id and old is None:
            raise ServiceError('no such excursus')
        order = self._order(b)
        if pages:
            a, z = int(pages[0]), int(pages[-1])
            on = sorted((o, i) for i, o in order.items() if a <= o[0] <= z)
            if not on:
                raise ServiceError('no segments on those pages')
            first, last = on[0][1], on[-1][1]
        if blocks:
            sel = sorted((order[i], i) for i in blocks if i in order)
            if not sel:
                raise ServiceError('unknown segment(s) (the page may have been rebuilt; reload)')
            first, last = sel[0][1], sel[-1][1]
        first = first or (old or {}).get('first')
        last = last or (old or {}).get('last')
        if first not in order or last not in order:
            raise ServiceError('unknown segment(s) (the page may have been rebuilt; reload)')
        if order[last] < order[first]:
            first, last = last, first                  # marked backwards: same stretch
        for x in st.values():                          # never overlapping another excursus
            if x['id'] == excursus_id or x.get('first') not in order or x.get('last') not in order:
                continue
            if not (order[last] < order[x['first']] or order[first] > order[x['last']]):
                raise ServiceError(f'overlaps the excursus «{x.get("name") or EXCURSUS_TYPES.get(x.get("type"), "")}» '
                                   f'(p. {order[x["first"]][0]}–{order[x["last"]][0]})')
        type = type if type in EXCURSUS_TYPES else (old or {}).get('type', 'other')
        placement = placement if placement in EXCURSUS_PLACEMENTS else (old or {}).get('placement', 'inplace')
        ex = {'id': excursus_id or 'X' + uuid.uuid4().hex[:8], 'first': first, 'last': last, 'type': type,
              'name': (name if name is not None else (old or {}).get('name', '')).strip()[:120], 'placement': placement}
        self._append(b, {'type': 'excursus_set', 'excursus': ex}, actor=actor)
        self._reproject(b, f'Παρέκβαση σ. {order[first][0]}–{order[last][0]}')
        return ex

    def update_excursus(self, book_id: str, excursus_id: str, actor: str = 'reviewer', **fields) -> dict:
        """Change type / name / placement / one end of an existing excursus."""
        b = self._book(book_id)
        old = next((x for x in b.events.state()['excursus'] if x['id'] == excursus_id), None) or self._auto(b, excursus_id)
        if old is None:
            raise ServiceError('no such excursus')
        merged = {**old, **{k: v for k, v in fields.items() if k in ('first', 'last', 'type', 'name', 'placement') and v is not None}}
        return self.set_excursus(book_id, merged['first'], merged['last'], None, merged['type'], merged.get('name', ''),
                                 merged['placement'], excursus_id, actor)

    def remove_excursus(self, book_id: str, excursus_id: str, actor: str = 'reviewer') -> dict:
        b = self._book(book_id)
        if not any(x['id'] == excursus_id for x in b.events.state()['excursus']):
            if self._auto(b, excursus_id) is None:
                raise ServiceError('no such excursus')
            ev = self._append(b, {'type': 'suggestion_dismiss', 'key': excursus_id}, actor=actor)[0]   # automatic: not one
            self._reproject(b, 'Η παρέκβαση αφαιρέθηκε')
            return ev
        ev = self._append(b, {'type': 'excursus_remove', 'excursus_id': excursus_id}, actor=actor)[0]
        self._reproject(b, 'Η παρέκβαση αφαιρέθηκε')
        return ev

    def place_excursus_type(self, book_id: str, type: str, placement: str, actor: str = 'reviewer') -> dict:
        """One placement for every excursus of a type (e.g. all historical notes at the end of the chapter): one undo."""
        if placement not in EXCURSUS_PLACEMENTS:
            raise ServiceError('unknown placement')
        b = self._book(book_id)
        own = b.events.state()['excursus']
        autos = [self._auto(b, x['id']) for x in (b.canonical() or {}).get('excursus') or [] if x.get('source') == 'entity']
        evs = [{'type': 'excursus_set', 'excursus': {**x, 'placement': placement}}
               for x in own + [a for a in autos if a and a['id'] not in {o['id'] for o in own}]
               if x.get('type') == type and x.get('placement') != placement]
        if evs:
            self._append(b, *evs, actor=actor)
            self._reproject(b, f'Θέση για {len(evs)} παρεκβάσεις')
        return {'changed': len(evs)}

    def accept_excursus_suggestion(self, book_id: str, key: str, type: str = 'other', name: str = '',
                                   placement: str = 'inplace', actor: str = 'reviewer') -> dict:
        b = self._book(book_id)
        s = next((x for x in (b.canonical() or {}).get('excursus_suggestions') or [] if x['key'] == key), None)
        if s is None:
            raise ServiceError('no such suggestion (the book may have been rebuilt; reload)')
        return self.set_excursus(book_id, s['first'], s['last'], None, type, name, placement, None, actor)
