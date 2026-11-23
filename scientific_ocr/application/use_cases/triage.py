"""Use cases — workload reduction: flag classes, bulk decisions, sampling audits."""
from __future__ import annotations

import random

from ...core.reasons import REASONS
from ...core.stats import sample_size, wilson_upper
from ...pipeline.overlay import CommandError, decision_event, has_human_value
from ...pipeline.triage import critical
from .base import PROFILES, ServiceBase, ServiceError, _now  # noqa: F401
from .views import _block_view


MAX_ERROR = 0.05          # the loosest error bound a class may be accepted with (CLAUDE.md rule 6: no blanket accept)


class TriageService(ServiceBase):
    # ---------------------------------------------------------------- workload reduction: classes, bulk, sampling
    def classes(self, book_id: str) -> dict:
        """Open review items per flag class, with the measured reliability of each flag."""
        c = self._book(book_id).canonical() or {}
        tri = c.get('triage') or {}
        out = {}
        for b in c.get('blocks', []):
            if b['review'] not in ('required', 'recommended') or b.get('decisions'):
                continue
            for r in b['reasons']:
                if REASONS.get(r) and REASONS[r].review in ('required', 'recommended'):
                    e = out.setdefault(r, {'reason': r, 'required': 0, 'recommended': 0, 'critical': critical(r)})
                    e[b['review']] += 1
        for r, e in out.items():
            e['stats'] = (tri.get('stats') or {}).get(r)
            e['accepted'] = r in (tri.get('accepted_classes') or [])
        return {'classes': sorted(out.values(), key=lambda e: (-e['critical'], -(e['required'] + e['recommended']))),
                'triage': {k: tri.get(k) for k in ('lowered', 'accepted', 'min_evidence', 'max_error', 'reliable')},
                'rule_suggestions': c.get('rule_suggestions', [])}

    def class_items(self, book_id: str, reason: str, limit: int = 200) -> list[dict]:
        c = self._book(book_id).canonical() or {}
        items = [x for x in c.get('blocks', []) if reason in x['reasons'] and x['review'] in ('required', 'recommended')
                 and not x.get('decisions')]
        return [_block_view(x) for x in items[:limit]]

    def bulk_decide(self, book_id: str, block_ids: list[str], action: str, actor='reviewer',
                    include_critical: bool = True) -> dict:
        """One reviewer action on many segments (one undo). Segments with a reviewer value are skipped; with
        include_critical=False so are segments with a CRITICAL flag (symbols, charges, numbers, equations…), which
        then stay in review — the project rule: those are never accepted in bulk without an explicit choice."""
        if action not in ('approve', 'mark_image_only'):
            raise ServiceError('bulk action must be approve or mark_image_only')
        b = self._book(book_id)
        by_id = self._blocks_seen(b)                    # what the reviewer sees (page previews included)
        evs, kept_critical = [], 0
        for i in dict.fromkeys(block_ids):
            blk = by_id.get(i)
            if not blk or has_human_value(blk):
                continue
            if not include_critical and any(critical(r) for r in blk['reasons']):
                kept_critical += 1
                continue
            try:
                evs.append({**decision_event(action, blk, {}, by_id.get), 'bulk': True})
            except CommandError:
                continue
        if evs:
            b.events.append(*evs, actor=actor)
            self._reproject(b, f'Μαζική απόφαση σε {len(evs)} τμήματα')
        return {'applied': len(evs), 'skipped': len(block_ids) - len(evs), 'kept_critical': kept_critical}

    def sample(self, book_id: str, reason: str, max_error: float = MAX_ERROR) -> dict:
        """A random audit sample of a flag class, sized so that zero errors proves error <= max_error (95%)."""
        if critical(reason):
            raise ServiceError('critical flags (symbols, equations, numbers) are never accepted by sampling')
        max_error = _bounded(max_error)
        pool = self.class_items(book_id, reason, limit=100000)
        n = sample_size(max_error)
        pick = pool if len(pool) <= n else random.Random(f'{book_id}|{reason}|{len(pool)}').sample(pool, n)
        return {'reason': reason, 'population': len(pool), 'n': len(pick), 'max_error': max_error, 'items': pick,
                'note': 'whole class (smaller than the sample size)' if len(pool) <= n else None}

    def accept_class(self, book_id: str, reason: str, results: list[dict], max_error: float = MAX_ERROR,
                     actor='reviewer') -> dict:
        """Record a sampling audit. Passes only if the 95% upper error bound is <= max_error. The threshold is
        bounded by the server and the results must cover exactly the sample the server draws (it is deterministic),
        so a client can never accept a class with a looser bound or a hand-picked sample."""
        if critical(reason):
            raise ServiceError('critical flags are never accepted by sampling')
        max_error = _bounded(max_error)
        drawn = {it['id'] if isinstance(it, dict) else it for it in self.sample(book_id, reason, max_error)['items']}
        given = {r.get('block_id') for r in results}
        if given != drawn or len(results) != len(given):
            raise ServiceError('the results must cover exactly the drawn sample (each item once)')
        n = len(results)
        errors = sum(1 for r in results if not r.get('ok'))
        upper = wilson_upper(errors, n)
        passed = n > 0 and upper <= max_error
        res = {'reason': reason, 'n': n, 'errors': errors, 'upper': round(upper, 4), 'threshold': max_error,
               'passed': passed, 'wrong': [r['block_id'] for r in results if not r.get('ok')]}
        if passed:
            b = self._book(book_id)
            b.events.append({'type': 'class_accept', 'reason': reason, 'n': n, 'errors': errors, 'upper': res['upper'],
                             'threshold': max_error, 'sample': [{'block_id': r['block_id'], 'ok': bool(r.get('ok'))}
                                                                for r in results]}, actor=actor)
            self._reproject(b, f'Αποδοχή κατηγορίας με δειγματοληψία ({n} δείγματα)')
        return res



def _bounded(max_error) -> float:
    try:
        v = float(max_error)
    except (TypeError, ValueError):
        raise ServiceError('max_error must be a number') from None
    if not 0 < v <= MAX_ERROR:
        raise ServiceError(f'max_error must be in (0, {MAX_ERROR}]')
    return v
