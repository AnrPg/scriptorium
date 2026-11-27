"""Domain policy: the reviewer's decisions belong to the OUTPUT PROFILE they were taken under (core.profiles).

What is right for one output is not necessarily right for another (an image kept for a reading edition is lost in
a text-only RAG export; a box left out of a RAG export belongs in the book as it is). So every reviewer event is
SCOPED to the profile in force when it was recorded, and a profile sees:
  * its own events, and
  * events of another profile up to a point, when the reviewer CARRIED them over:
      - at a switch: event `output_profile` with carry='copy' (the new profile starts with everything the previous
        one had); carry='separate' starts it with only its own earlier events (if it was used before), and
      - at any time: event `profile_copy` {from, to, upto}: the events of `from` up to seq `upto` apply in `to`.
    Carrying is transitive (what `from` itself carried is carried along, never past either point), and the events
    of all sources replay in their original order: on the same segment, the later decision wins.
GLOBAL events are about the book itself, not about an output: which pages it has, how a page is OCR'd, which
profile is in force, carrying, undo/redo.

History is never rewritten: an event without `scope` (recorded before scopes existed) belongs to the profile in
force at that point of the log, and a switch without `carry` (also from before) carried everything — exactly how
the app behaved then, so an existing book projects as before.

Pure functions over event dicts; the event store (adapters.event_store) stamps and filters with them.
"""
from __future__ import annotations

from .profiles import DEFAULT

GLOBAL = frozenset({'output_profile', 'profile_copy', 'page_remove', 'page_restore', 'page_profile', 'undo', 'redo'})
CARRY = ('copy', 'separate')
_ALL = float('inf')


def is_scoped(ev: dict) -> bool:
    return ev.get('type') not in GLOBAL


def stamp(events: list[dict], current: str) -> list[dict]:
    """Events about to be appended, each scoped event labelled with the profile in force (a switch inside the
    same batch moves the label for the events after it)."""
    out = []
    for ev in events:
        if ev.get('type') == 'output_profile':
            current = ev['profile']
        elif is_scoped(ev) and not ev.get('scope'):
            ev = {**ev, 'scope': current}
        out.append(ev)
    return out


def resolve(live: list[dict]) -> tuple[str, dict[int, str], list[tuple[str, str, float]]]:
    """(profile in force, {seq: scope of each scoped event}, carries [(from, to, upto)]) of the live events in
    log order."""
    current, scope, carries = DEFAULT, {}, []
    for ev in live:
        t = ev['type']
        if t == 'output_profile':
            new = ev['profile']
            if new != current and ev.get('carry', 'copy') == 'copy':   # no 'carry' = recorded before scopes: copy
                carries.append((current, new, ev['seq']))
            current = new
        elif t == 'profile_copy':
            carries.append((ev['from'], ev['to'], ev.get('upto', ev['seq'])))
        elif is_scoped(ev):
            scope[ev['seq']] = ev.get('scope') or current
    return current, scope, carries


def sources(profile: str, carries: list[tuple[str, str, float]]) -> dict[str, float]:
    """{scope: last seq whose events apply in `profile`} — its own events all, carried ones up to their point."""
    src: dict[str, float] = {profile: _ALL}
    changed = True
    while changed:                                          # fixpoint: carries of carries, never past either point
        changed = False
        for a, b, upto in carries:
            if b in src:
                bound = min(src[b], upto)
                if bound > src.get(a, -1):
                    src[a] = bound
                    changed = True
    return src


def visible(live: list[dict], profile: str | None = None) -> tuple[list[dict], str, dict[int, str]]:
    """(the live events that apply in `profile` — the one in force when None; global ones always —, that profile,
    {seq: scope})."""
    current, scope, carries = resolve(live)
    current = profile or current
    src = sources(current, carries)
    keep = [ev for ev in live if not is_scoped(ev) or ev['seq'] <= src.get(scope[ev['seq']], -1)]
    return keep, current, scope


# Events that say WHICH profile is shown, not what a profile contains: never part of a profile's inputs.
_SWITCHES = frozenset({'output_profile', 'profile_copy', 'undo', 'redo'})


def signature(live: list[dict], profile: str) -> dict:
    """Everything a profile's projection depends on among the reviewer's events: the events that apply in it (its
    own, the carried ones, the book-level ones) and its own settings. Equal signatures = the profile's built version
    is up to date as far as the reviewer is concerned (application.projection adds the machine inputs)."""
    keep, _, _ = visible(live, profile)
    own = [ev for ev in live if ev['type'] == 'output_profile' and ev['profile'] == profile]
    return {'profile': profile, 'events': sorted(ev['seq'] for ev in keep if ev['type'] not in _SWITCHES),
            'overrides': (own[-1].get('overrides') or {}) if own else {}}


def concerns(ev: dict, by_seq: dict[int, dict], profile: str) -> bool:
    """Whether a recorded event changes what `profile` shows (what is pending for it, which pages to update): its
    own and the book-level events, a copy into it, an undo/redo of one of those. A switch never does by itself
    (the profile's settings are compared separately: overrides in its signature). An event from before scopes
    (no `scope`) is conservatively counted."""
    t = ev.get('type')
    if t == 'output_profile':
        return False
    if t == 'profile_copy':
        return ev.get('to') == profile
    if t in ('undo', 'redo'):
        return any(concerns(by_seq[s], by_seq, profile) for s in ev.get('targets') or [] if s in by_seq)
    if not is_scoped(ev):
        return True
    return ev.get('scope') in (None, profile)


def summary(live: list[dict]) -> dict[str, int]:
    """Number of the reviewer's own scoped events per profile (what a switch would carry or leave behind)."""
    _, scope, _ = resolve(live)
    out: dict[str, int] = {}
    for ev in live:
        if ev['seq'] in scope and not str(ev.get('actor', '')).startswith('system:'):
            out[scope[ev['seq']]] = out.get(scope[ev['seq']], 0) + 1
    return out
