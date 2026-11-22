"""Use cases — the book's HIERARCHY as the reviewer sees it: the whole document as a tree (every segment under
its section, with what will and will not reach the output), and the heading-level HINTS that did not pass their
check (pipeline.sections: proposals grouped by style and level, with the evidence and its calibration)."""
from __future__ import annotations

import re

from ...core.model import chosen_text, in_reader_output
from ...pipeline.document import EVIDENCE_EL
from ...pipeline.overlay import CommandError, heading_level_events
from .base import ServiceBase, ServiceError


def _snippet(b: dict, n: int = 90) -> str:
    t = chosen_text(b) or (b['chosen'].get('latex') if b['chosen'].get('type') == 'latex' else '') or ''
    t = re.sub(r'[*#]|\s+', ' ', t).strip()
    return t[:n] + ('…' if len(t) > n else '')


class HierarchyService(ServiceBase):
    def outline(self, book_id: str) -> dict:
        """Every segment of the book in reading order with its depth in the hierarchy: headings at their level,
        content one level below its innermost section, captions under their figure. Segments that do not reach
        the output say why (page furniture, hidden by the reviewer)."""
        c = self._book(book_id).canonical() or {}
        toc = {s['id']: s for s in c.get('sections', [])}
        nodes = []
        for b in c.get('blocks', []):
            path = [x for x in (b.get('section_path') or []) if x in toc and x != b['id']]
            base = toc[path[-1]]['level'] if path else 0
            if b['id'] in toc:
                depth = toc[b['id']]['level'] - 1
            else:
                depth = base + (1 if b['kind'] == 'caption' and b['relations'].get('caption_for') else 0)
            out = in_reader_output(b) and b.get('flow') != 'furniture'
            nodes.append({'id': b['id'], 'page': b['page'], 'kind': b['kind'], 'depth': depth, 'text': _snippet(b),
                          'level': toc[b['id']]['level'] if b['id'] in toc else None,
                          'level_source': toc[b['id']]['source'] if b['id'] in toc else None,
                          'evidence': b.get('heading_evidence'), 'hints': b.get('heading_hints'),
                          'flow': b.get('flow'), 'review': b.get('review'), 'in_output': out,
                          'why_not': None if out else ('omitted' if b.get('omitted') else (b.get('excluded') or {}).get('as')),
                          'entities': [{'type': e['type'], 'family': e['family'], 'verified': e['verified']}
                                       for e in b.get('entities') or []],
                          'caption_for': b['relations'].get('caption_for'),
                          'group': next(({'id': g['id'], 'name': g.get('name') or g['type'], 'size': len(g['members']),
                                          'first': g['members'][0] == b['id'], 'auto': g.get('source') == 'auto'}
                                         for g in sorted(c.get('groups', []), key=lambda g: -len(g['members']))
                                         if b['id'] in g['members']), None)})
        hidden = [{'id': x['block_id'], 'page': p['page'], 'kind': x.get('kind'), 'text': (x.get('text') or '')[:90]}
                  for p in c.get('pages', []) for x in p.get('hidden', [])]
        return {'nodes': nodes, 'hidden': hidden, 'sections': len(toc)}

    def heading_review(self, book_id: str) -> dict:
        """Heading-level hints that did not pass their check, as proposals, with the calibration per hint source
        and the measured styles (batch statistics)."""
        c = self._book(book_id).canonical() or {}
        hr = c.get('heading_review') or {}
        by_id = {b['id']: b for b in c.get('blocks', [])}
        props = []
        for p in hr.get('proposals', []):
            mem = [by_id[m] for m in p['members'] if m in by_id]
            if not mem:
                continue
            props.append({**p, 'items': [{'id': b['id'], 'page': b['page'], 'title': _snippet(b, 80),
                                          'current': b.get('heading_level'), 'source': b.get('heading_level_source'),
                                          'hints': b.get('heading_hints')} for b in mem],
                          'evidence_el': [EVIDENCE_EL.get(s, s) for s in p['sources']]})
        return {'proposals': props, 'stats': hr.get('stats') or {}, 'styles': hr.get('styles') or {}}

    def accept_heading_proposal(self, book_id: str, key: str, members: list[str] | None = None, actor='reviewer') -> dict:
        """Accept a proposal (or a subset of its headings): one set_heading_level decision per heading, ONE undo."""
        p = next((x for x in self.heading_review(book_id)['proposals'] if x['key'] == key), None)
        if p is None:
            raise ServiceError('no such open proposal')
        ids = [m for m in (members or p['members']) if m in p['members']]
        if not ids:
            raise ServiceError('no heading selected')
        b = self._book(book_id)
        by_id = {x['id']: x for x in (b.canonical() or {}).get('blocks', [])}
        try:
            evs = heading_level_events(b.canonical() or {}, {i: p['level'] for i in ids}, by_id.get, {'heading_hint': p['sources']})
        except CommandError as exc:
            raise ServiceError(str(exc)) from None
        b.events.append(*evs, actor=actor)
        self._reproject(b, f'Επίπεδο {p["level"]} σε {len(evs)} τίτλους')
        return {'applied': len(evs)}
