"""Use cases — ENTITY CLASSIFICATION (examples, exercises, definitions, asides… see core.vocabulary):
list with statistics, verify (confirm / correct / dismiss), the reviewer's own classification of segments
(a fine type or a whole family), bulk confirmation of statistically trusted rules, and the model's second pass
(proposals only; accepting one is an ordinary undoable event)."""
from __future__ import annotations

import re

from ...adapters.storage import read_json
from ...core.stats import wilson_upper
from ...core.vocabulary import family_of, valid_entity_type
from ...pipeline import entity_review
from ...pipeline.entities import human_key
from .base import ServiceBase, ServiceError


class EntityService(ServiceBase):
    def entities(self, book_id: str) -> dict:
        """Entities with what the reviewer needs (label, first text, pages, placement, source, trust, model view)
        and the learning statistics (detector rules; model agreement with the reviewer)."""
        b = self._book(book_id)
        c = b.canonical() or {}
        by_id = {x['id']: x for x in c.get('blocks', [])}
        model = self._model_views(b, c)
        out = []
        for e in c.get('entities', []):
            first = by_id.get(e['members'][0]) or {}
            txt = (first.get('chosen') or {}).get('text') or (first.get('chosen') or {}).get('latex') or ''
            out.append({**{k: e.get(k) for k in ('key', 'type', 'detected_type', 'granularity', 'label', 'number', 'pages',
                                                 'confidence', 'rule', 'rule_class', 'verified', 'trusted', 'placement',
                                                 'answers')},
                        'family': family_of(e['type']), 'source': 'human' if e['granularity'] == 'human' else 'detected',
                        'size': len(e['members']), 'block': e['members'][0], 'members': e['members'],
                        'text': re.sub(r'[*#]', '', txt)[:160], 'model': model.get(e['key'])})
        return {'entities': out, 'stats': c.get('entity_stats') or {}, 'model_stats': self._model_stats(b, c, model)}

    # ---------------------------------------------------------------- verdicts on detected entities
    def confirm_entities(self, book_id: str, keys: list[str], etype: str | None = None, actor='reviewer') -> dict:
        """Verify entities or CHANGE their category — any number of times, verified or not (the latest verdict
        wins): a detected entity gets entity_confirm (type, corrected type or only the family); the reviewer's
        own entity is re-assigned with the new type. One undoable step for the whole selection."""
        b = self._book(book_id)
        known = {e['key']: e for e in (b.canonical() or {}).get('entities', [])}
        if not keys or any(k not in known for k in keys):
            raise ServiceError('unknown entity')
        if etype is not None and not valid_entity_type(etype):
            raise ServiceError('unknown entity type')
        evs = []
        for k in keys:
            e = known[k]
            if e['granularity'] == 'human':
                evs.append({'type': 'entity_assign', 'key': k, 'members': e['members'], 'entity_type': etype or e['type']})
            else:
                evs.append({'type': 'entity_confirm', 'key': k, 'entity_type': etype or e['type']})
        b.events.append(*evs, actor=actor)
        self._reproject(b, f'Κατηγορία {len(keys)} οντοτήτων')
        return {'confirmed': len(evs)}

    def confirm_trusted(self, book_id: str, actor='reviewer') -> dict:
        """One click for every unverified entity whose detector rule the statistics trust (still a human act)."""
        c = self._book(book_id).canonical() or {}
        keys = [e['key'] for e in c.get('entities', []) if e.get('trusted') and not e['verified']]
        if not keys:
            raise ServiceError('no trusted unverified entities')
        return self.confirm_entities(book_id, keys, None, actor)

    # ---------------------------------------------------------------- the reviewer's own classification
    def assign_entity(self, book_id: str, members: list[str], etype: str, actor='reviewer') -> dict:
        """Classify segments (in the reviewer's order) as one entity of a type or of a whole family."""
        b = self._book(book_id)
        if not valid_entity_type(etype):
            raise ServiceError('unknown entity type')
        ids = {x['id'] for x in (b.canonical() or {}).get('blocks', [])}
        members = list(dict.fromkeys(members or []))
        if not members or any(m not in ids for m in members):
            raise ServiceError('unknown segment')
        ev = b.events.append({'type': 'entity_assign', 'key': human_key(members), 'members': members,
                              'entity_type': etype}, actor=actor)[0]
        self._reproject(b, 'Κατηγορία τμήματος')
        return ev

    def remove_entity(self, book_id: str, key: str, actor='reviewer') -> dict:
        """Withdraw the reviewer's own entity, or reject a detected one (remembered; counts as a wrong outcome)."""
        b = self._book(book_id)
        if key in b.events.state()['assigned']:
            ev = b.events.append({'type': 'entity_remove', 'key': key}, actor=actor)[0]
        elif any(e['key'] == key for e in (b.canonical() or {}).get('entities', [])):
            ev = b.events.append({'type': 'suggestion_dismiss', 'key': key}, actor=actor)[0]
        else:
            raise ServiceError('unknown entity')
        self._reproject(b, 'Αφαίρεση κατηγορίας')
        return ev

    # ---------------------------------------------------------------- model second pass
    def start_entity_review(self, book_id: str) -> dict:
        if self.interpreter() is None:
            raise ServiceError('no local model installed (see config.NOTE_MODELS)')
        b = self._book(book_id)
        if not b.canonical():
            raise ServiceError('process the book first')
        return self.jobs.submit(b.id, 'entities', ['propose-entities', '--book', str(b.dir)],
                                'Έλεγχος κατηγοριών με το μοντέλο', lane='heavy')

    def entity_proposals(self, book_id: str) -> dict:
        """Open proposals: not rejected, target still there and still different from the current classification."""
        b = self._book(book_id)
        res = read_json(b.dir / entity_review.FILE) or {}
        c = b.canonical() or {}
        ents = {e['key']: e for e in c.get('entities', [])}
        typed = {frozenset(e['members']) for e in ents.values()}
        dismissed = set(b.events.state()['dismissed'])
        open_ = []
        for p in res.get('proposals', []):
            if p['key'] in dismissed:
                continue
            d = p['decision']
            if p['mode'] == 'verify':
                e = ents.get(d['key'])
                if not e or e['verified']:
                    continue
                p = {**p, 'current': e['type']}
            elif frozenset(d['members']) in typed:
                continue
            open_.append(p)
        open_.sort(key=lambda p: (-p['votes'], p['page']))
        meta = {k: res.get(k) for k in ('status', 'error', 'built_utc', 'canonical_built', 'model', 'samples', 'asked',
                                        'agreed_with_current', 'invalid_answers', 'failed')}
        return {**meta, 'proposals': open_}

    def accept_entity_proposal(self, book_id: str, key: str, actor='reviewer') -> dict:
        p = next((x for x in self.entity_proposals(book_id)['proposals'] if x['key'] == key), None)
        if p is None:
            raise ServiceError('no such open proposal')
        d = p['decision']
        if d['event'] == 'dismiss':
            return self.remove_entity(book_id, d['key'], actor)
        if d['event'] == 'entity_confirm':
            return self.confirm_entities(book_id, [d['key']], d['entity_type'], actor)
        return self.assign_entity(book_id, d['members'], d['entity_type'], actor)

    # ---------------------------------------------------------------- statistics of the model
    def _model_views(self, b, c) -> dict:
        """entity key -> what the model said about it ({type, votes, samples, agrees})."""
        res = read_json(b.dir / entity_review.FILE) or {}
        out = {a['entity_key']: {'type': a['type'], 'votes': a['votes'], 'samples': a['samples'], 'agrees': True}
               for a in res.get('agreements', []) if a.get('entity_key')}
        for p in res.get('proposals', []):
            k = p['decision'].get('key')
            if k:
                out[k] = {'type': p['proposed'], 'votes': p['votes'], 'samples': p['samples'], 'agrees': False}
        return out

    def _model_stats(self, b, c, model: dict) -> dict:
        """How often the model's answer matched the reviewer's final verdict, per family and overall: the evidence
        for how much its suggestions can be relied on (shown to the reviewer; never applied automatically)."""
        final = {e['key']: e['type'] for e in c.get('entities', []) if e['verified'] and e['granularity'] != 'human'}
        final.update({k: 'none' for k in b.events.state()['dismissed'] if k.startswith('N')})
        agg: dict[str, dict] = {}
        for k, m in model.items():
            if k not in final:
                continue
            fam = family_of(final[k]) or 'none'
            for name in (fam, '*'):
                a = agg.setdefault(name, {'n': 0, 'wrong': 0})
                a['n'] += 1
                a['wrong'] += m['type'] != final[k]
        for a in agg.values():
            a['error_upper'] = round(wilson_upper(a['wrong'], a['n']), 3)
        return agg
