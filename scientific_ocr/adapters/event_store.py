"""Event store: per-book append-only log (events.jsonl), the single source of truth for human input.

Event sourcing: outputs (canonical.json, chapter.md) are a projection rebuilt from immutable raw OCR plus the
*effective* events. Nothing is deleted; undo/redo are compensating events, so history stays auditable.

Every event carries seq, ts, actor and an optional `group` (several events undone/redone together). The schema of
each event type — required fields, and the fields each decision action needs — is defined ONCE in
core.decisions (EVENT_FIELDS, DECISION_FIELDS) and enforced on append. Decisions are produced only by the
commands in pipeline.overlay (decision_event), which validate the reviewer's request first. Notable shapes:
  region_add    {region: {id, page, bbox, label: {engine, name}}}
  merge_add     {merge: {id, page, members, label, ordered}}    group_add {group: {id, type, name, members, ordered}}
                (ordered: members in the order the reviewer arranged; absent in old events = reading order)
  entity_confirm {key, entity_type}   ('entity_type' because 'type' is the event kind)
  entity_assign {key, members, entity_type}   the reviewer's own classification of ordered segments; entity_type
                is a type or 'family:<family>' (core.vocabulary); same key = same members, latest type wins
  entity_remove {key}                          withdraws an entity_assign
  page_remove   {pages}  pages left out of the book (raw OCR kept; page_restore {pages} brings them back)
  class_accept  {reason, n, errors, upper, threshold, sample: [{block_id, ok}]}
  profile_copy  {from, to, upto}  the decisions of output profile `from` up to seq `upto` also apply in `to`
Scopes (core.scopes): every reviewer event is stamped with the output profile in force (`scope`); the fold returns
only what applies in the current profile, and undo steps only through those. Global events: core.scopes.GLOBAL.
"""
from __future__ import annotations

import fcntl
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..core import scopes
from ..core.decisions import UNDOABLE, event_errors

_locks: dict[str, threading.Lock] = {}


class EventError(ValueError):
    pass


class EventStore:
    def __init__(self, book_dir: Path):
        self.dir = book_dir
        self.path = book_dir / 'events.jsonl'
        self._lock = _locks.setdefault(str(self.path.resolve()), threading.Lock())

    # ---------------------------------------------------------------- write side
    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        evs = []
        for n, line in enumerate(self.path.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EventError(f'events.jsonl line {n}: invalid JSON ({exc})')
            if ev.get('type') not in UNDOABLE | {'undo', 'redo'}:
                raise EventError(f'events.jsonl line {n}: unknown event type {ev.get("type")!r}')
            evs.append(ev)
        return evs

    def append(self, *events: dict, actor: str = 'reviewer') -> list[dict]:
        """Append one or more events atomically (same group => undone/redone together). Every event is checked
        against its schema (core.decisions) first; appends are serialised across threads AND processes."""
        return self._transact(lambda _evs: list(events), actor)

    def _transact(self, make, actor: str) -> list[dict]:
        """Under the cross-process lock: read the log, let `make(evs)` derive the events to add from that exact
        state (so an undo can never target a stale 'last action'), validate and append them."""
        with self._lock, open(self.dir / '.events.lock', 'w') as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            evs = self.load()
            events = make(evs)
            if not events:
                return []
            # every reviewer event belongs to the output profile in force (core.scopes)
            events = scopes.stamp(events, fold(evs)['scope'])
            bad = [e for ev in events for e in event_errors(ev)]
            if bad:
                raise EventError('; '.join(bad))
            seq = evs[-1]['seq'] if evs else 0
            ts = datetime.now(timezone.utc).isoformat(timespec='seconds')
            group = f'g{seq + 1}' if len(events) > 1 else None
            out = []
            for ev in events:
                seq += 1
                out.append({'seq': seq, 'ts': ts, 'actor': actor, **({'group': group} if group else {}), **ev})
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in out))
                f.flush()
                os.fsync(f.fileno())
            return out

    def undo(self, actor='reviewer') -> list[dict]:
        def make(evs):
            t = fold(evs)['undo_targets']
            return [{'type': 'undo', 'targets': t}] if t else []
        return self._transact(make, actor)

    def redo(self, actor='reviewer') -> list[dict]:
        def make(evs):
            t = fold(evs)['redo_targets']
            return [{'type': 'redo', 'targets': t}] if t else []
        return self._transact(make, actor)

    # ---------------------------------------------------------------- read side
    def state(self) -> dict:
        return fold(self.load())


def fold(evs: list[dict]) -> dict:
    """Pure fold of the log into the effective human input + undo/redo targets."""
    active, by_seq, redo_stack = {}, {}, []
    for ev in evs:
        by_seq[ev['seq']] = ev
        if ev['type'] in UNDOABLE:
            active[ev['seq']] = True
            if not str(ev.get('actor', '')).startswith('system:'):
                redo_stack = []                               # a new (reviewer) action invalidates redo
        elif ev['type'] == 'undo':
            targets = [t for t in ev.get('targets') or [ev.get('target')] if active.get(t)]
            for t in targets:
                active[t] = False
            if targets:
                redo_stack.append(targets)
        elif ev['type'] == 'redo' and redo_stack and sorted(redo_stack[-1]) == sorted(ev.get('targets') or []):
            for t in redo_stack.pop():
                active[t] = True
    live = [by_seq[s] for s, on in active.items() if on]
    # only what applies in the output profile in force (its own decisions + what was carried into it: core.scopes)
    # A profile switch never undoes anything: other profiles' events stay active (kept apart) and apply again
    # when their profile is back in force.
    counts = scopes.summary(live)
    not_undone = {e['seq'] for e in live}
    live, current, scope_of = scopes.visible(live)
    live = [{**ev, 'scope': scope_of[ev['seq']]} if ev['seq'] in scope_of and 'scope' not in ev else ev for ev in live]
    rules, regions, profiles, hidden, merges, groups, dismissed, accepts = {}, {}, {}, set(), {}, {}, set(), {}
    entities, assigned, removed, excursus = {}, {}, set(), {}
    output_profile = None
    for ev in live:
        t = ev['type']
        if t == 'rule_add':
            rules[ev['rule']['id']] = ev['rule']
        elif t == 'rule_remove':
            rules.pop(ev['rule_id'], None)
        elif t == 'region_add':
            regions[ev['region']['id']] = ev['region']
        elif t == 'region_remove':
            regions.pop(ev['region_id'], None)
        elif t == 'block_hide':
            hidden.add(ev['block_id'])
        elif t == 'block_show':
            hidden.discard(ev['block_id'])
        elif t == 'page_profile':
            profiles[int(ev['page'])] = ev['profile']
        elif t == 'merge_add':
            merges[ev['merge']['id']] = ev['merge']
        elif t == 'merge_remove':
            merges.pop(ev['merge_id'], None)
        elif t == 'group_add':
            groups[ev['group']['id']] = ev['group']
        elif t == 'group_remove':
            groups.pop(ev['group_id'], None)
        elif t == 'suggestion_dismiss':
            dismissed.add(ev['key'])
        elif t == 'class_accept':
            accepts[ev['reason']] = ev
        elif t == 'entity_confirm':
            entities[ev['key']] = ev
        elif t == 'entity_assign':
            assigned[ev['key']] = ev
        elif t == 'entity_remove':
            assigned.pop(ev['key'], None)
        elif t == 'page_remove':
            removed |= {int(p) for p in ev['pages']}
        elif t == 'page_restore':
            removed -= {int(p) for p in ev['pages']}
        elif t == 'excursus_set':                       # add or change (same id): the last one wins
            excursus[ev['excursus']['id']] = ev['excursus']
        elif t == 'excursus_remove':
            excursus.pop(ev['excursus_id'], None)
        elif t == 'output_profile':                     # the last one in force wins
            output_profile = {'profile': ev['profile'], 'overrides': ev.get('overrides') or {}, 'seq': ev['seq']}
    # The reviewer's undo steps through the reviewer's own actions: events written by the system itself (e.g. the
    # automatic careful re-examination after processing) are never the target of Ctrl-Z.
    undo_targets = []
    own = [e for e in live if not str(e.get('actor', '')).startswith('system:')]
    if own:
        last = own[-1]
        undo_targets = [e['seq'] for e in own if e.get('group') and e.get('group') == last.get('group')] \
            if last.get('group') else [last['seq']]
    return {'decisions': [e for e in live if e['type'] == 'decision'], 'rules': list(rules.values()),
            'regions': list(regions.values()), 'profiles': profiles, 'hidden': sorted(hidden),
            'merges': list(merges.values()), 'groups': list(groups.values()), 'dismissed': sorted(dismissed),
            'accepts': list(accepts.values()), 'entities': entities, 'assigned': assigned,
            'removed_pages': sorted(removed), 'excursus': list(excursus.values()), 'output_profile': output_profile,
            'undo_targets': undo_targets, 'redo_targets': redo_stack[-1] if redo_stack else [],
            'history': evs, 'active': not_undone, 'applies': {e['seq'] for e in live},
            'scope': current, 'scope_counts': counts, 'scope_of': scope_of}
