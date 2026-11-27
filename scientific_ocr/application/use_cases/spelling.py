"""Use cases — SPELLING PROPOSALS (pipeline.spelling): list them with their evidence, accept chosen candidates
(each touched segment gets ONE replace_text decision with all its accepted words; the whole acceptance is one
undoable step), or dismiss them (remembered). Nothing is ever corrected without the reviewer."""
from __future__ import annotations

from ...core.text import strip_markup
from ...pipeline.overlay import CommandError, decision_event
from ...pipeline.spelling import apply_fixes
from .base import ServiceBase, ServiceError

CONTEXT = 48                      # characters of context shown on each side of the word


class SpellingService(ServiceBase):
    def spelling(self, book_id: str, strength: str = 'all') -> dict:
        if strength not in ('all', 'strong', 'weak'):
            raise ServiceError('strength: all|strong|weak')
        c = self._book(book_id).canonical() or {}
        items = []
        for b in c.get('blocks', []):
            text = (b.get('chosen') or {}).get('text') or ''
            for p in b.get('spelling') or []:
                if strength != 'all' and p['strength'] != strength:
                    continue
                items.append({**p, 'block_id': b['id'], 'page': b['page'],
                              'before': strip_markup(text[max(0, p['start'] - CONTEXT):p['start']]),
                              'after': strip_markup(text[p['end']:p['end'] + CONTEXT])})
        return {'items': items, 'summary': c.get('spelling') or {}}

    def apply_spelling(self, book_id: str, choices: list[dict], actor='reviewer') -> dict:
        """choices: [{key, text}] — the candidate (or the reviewer's own spelling) for each proposal."""
        b = self._book(book_id)
        props = {p['key']: (x['id'], p) for x in (b.canonical() or {}).get('blocks', []) for p in x.get('spelling') or []}
        per_block: dict[str, list] = {}
        for ch in choices or []:
            hit = props.get(ch.get('key'))
            new = ch.get('text')
            if hit is None or not isinstance(new, str) or not new.strip() or len(new) > 200:
                raise ServiceError('unknown proposal or empty text')
            bid, p = hit
            per_block.setdefault(bid, []).append((p['start'], p['end'], p['word'], new.strip()))
        seen = self._blocks_seen(b)
        evs, skipped = [], 0
        for bid, fixes in per_block.items():
            blk = seen.get(bid)
            text = apply_fixes(((blk or {}).get('chosen') or {}).get('text') or '', fixes) if blk else None
            if text is None:                              # the segment changed since: its proposals are stale
                skipped += len(fixes)
                continue
            try:
                evs.append(decision_event('replace_text', blk, {'value': text}, seen.get))
            except CommandError as exc:
                raise ServiceError(str(exc)) from None
        if evs:
            self._append(b, *evs, actor=actor)
            self._reproject(b, f'Ορθογραφία: {sum(len(f) for f in per_block.values()) - skipped} διορθώσεις')
        return {'applied': len(evs), 'words': sum(len(f) for f in per_block.values()) - skipped, 'skipped': skipped}

    def dismiss_spelling(self, book_id: str, keys: list[str], actor='reviewer') -> dict:
        b = self._book(book_id)
        known = {p['key'] for x in (b.canonical() or {}).get('blocks', []) for p in x.get('spelling') or []}
        keys = [k for k in dict.fromkeys(keys or []) if k in known]
        if not keys:
            raise ServiceError('unknown proposal')
        self._append(b, *[{'type': 'suggestion_dismiss', 'key': k} for k in keys], actor=actor)
        self._reproject(b, f'Ορθογραφία: {len(keys)} απορρίψεις')
        return {'dismissed': len(keys)}
