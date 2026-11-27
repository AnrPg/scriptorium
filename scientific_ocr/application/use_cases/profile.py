"""Use cases — the book's OUTPUT PROFILE (core.profiles; applied by pipeline.profile): choose it (one undoable,
book-wide event, optionally with per-book overrides), see what it excludes, and settle its automatic exclusions
with one confirmation each (or all at once) — the reviewer confirms BOUNDARIES, never re-reads the content.
Confirming = the existing entity verdicts (entity_confirm); "not this" = dismissing the detection (suggestion_dismiss),
which keeps the segments in the output and counts as a wrong outcome for the detector's rule.
An exclusion comes from an ENTITY (a question, a summary…) or from an EXCURSUS whose type the profile removes (a
historical note…): every verdict goes to what the exclusion is made of — an excursus is confirmed by becoming the
reviewer's, re-bounded with new ends, «not this» by being removed (all tagged `about`, so the review lists and
undoes them like the others)."""
from __future__ import annotations

from ...core import profiles, scopes
from ...pipeline import entities
from .. import build_history
from .base import ServiceBase, ServiceError, variants_job

ABOUT = 'exclusion'                     # tag of the verdicts given while reviewing the profile's exclusions
VERDICT_EVENTS = {'entity_confirm', 'entity_assign', 'entity_remove', 'suggestion_dismiss', 'excursus_set', 'excursus_remove'}


class ProfileService(ServiceBase):
    def output_profile(self, book_id: str) -> dict:
        """Current profile (as chosen and as last built), the available profiles, and what the profile excludes."""
        b = self._book(book_id)
        ev = b.events.state().get('output_profile') or {}
        built = (b.canonical() or {}).get('profile') or {}
        name = ev.get('profile') or profiles.DEFAULT
        st = b.events.state()
        excluded, suggested, decided = self._live_verdicts(b, st, built)
        return {'current': name, 'overrides': ev.get('overrides') or {},
                # decisions kept per profile (core.scopes): how many each profile has of its own
                'decisions': {k: st['scope_counts'].get(k, 0) for k in profiles.PROFILES},
                'built': built.get('name'), 'awaiting_rebuild': built.get('name', profiles.DEFAULT) != name,
                'available': [{'name': k, 'el': v['el'], 'en': v['en'], 'settings': profiles.effective(k)}
                              for k, v in profiles.PROFILES.items()],
                'excluded': excluded, 'suggested': suggested, 'decided': decided,
                'pending': sum(1 for e in excluded if not e['confirmed'] and not e['verdict'] and e['members']), 'excluded_segments': built.get('excluded_segments', 0),
                'pictures_not_reviewed': built.get('pictures_not_reviewed', 0),
                'text_only_loss': built.get('text_only_loss', 0)}

    def set_output_profile(self, book_id: str, profile: str, overrides: dict | None = None, actor='reviewer',
                           carry: str | None = None) -> dict:
        """Choose the profile (or change its overrides). Decisions belong to the profile they were taken under
        (core.scopes): CHANGING the profile needs the reviewer's choice — carry='copy' (the new profile starts with
        the current one's decisions) or 'separate' (it keeps only its own). Nothing is undone either way: the other
        profile's decisions stay and apply again when it is back."""
        b = self._book(book_id)
        errs = profiles.errors(profile, overrides)
        if errs:
            raise ServiceError('; '.join(errs))
        current = b.events.state()['scope']
        if profile != current and carry not in scopes.CARRY:
            raise ServiceError(f'changing the profile: choose carry = {" or ".join(scopes.CARRY)}')
        ev = self._append(b, {'type': 'output_profile', 'profile': profile, **({'overrides': overrides} if overrides else {}),
                              **({'carry': carry} if profile != current else {})}, actor=actor)[0]
        label = f'Προφίλ εξόδου: {profiles.PROFILES[profile]["el"]}'
        if profile == current:                          # its settings changed: its version is rebuilt
            self._reproject(b, label, force=True)
            return ev
        # every profile keeps its pre-processed version (build_history.activate): shown at once; rebuilt only when
        # it is out of date (then shown meanwhile), missing, or a build holds the book
        state = build_history.activate(b.dir, profile)
        if state != 'current':
            self._reproject(b, label, force=True)
        else:
            self._update_pages(b)                       # page-local actions of that profile since its version
        return {**ev, 'version': state}

    def preprocess_profiles(self, book_id: str) -> dict:
        """Run the pre-processing of EVERY output profile again (the shown one first, the others in the background):
        the manual trigger; it also runs by itself after OCR and after every book update."""
        b = self._book(book_id)
        if not b.canonical():
            raise ServiceError('process the book first')
        return self.jobs.submit(b.id, 'reproject', ['reproject', '--book', str(b.dir)], 'Προεπεξεργασία όλων των προφίλ',
                                lane='light', then=variants_job(b, force=True))

    def copy_decisions(self, book_id: str, source: str, actor='reviewer') -> dict:
        """Carry every decision profile `source` has NOW into the profile in force (one undoable event; core.scopes).
        On the same segment the later decision wins, in the order they were taken."""
        b = self._book(book_id)
        st = b.events.state()
        if source not in profiles.PROFILES or source == st['scope']:
            raise ServiceError('choose another profile to copy from')
        if not st['scope_counts'].get(source):
            raise ServiceError('that profile has no decisions of its own')
        upto = max((e['seq'] for e in st['history']), default=0)
        ev = self._append(b, {'type': 'profile_copy', 'from': source, 'to': st['scope'], 'upto': upto}, actor=actor)[0]
        self._reproject(b, f'Αποφάσεις από «{profiles.PROFILES[source]["el"]}»')
        return ev

    # ---------------------------------------------------------------- live verdicts and their focused undo
    # The built summary (canonical 'profile') knows the verdicts the last build saw; the log knows them NOW. A
    # verdict given (or undone) since the build counts at once: the exclusion leaves (or re-enters) the pending
    # list, and every verdict on an exclusion is listed with its own «Αναίρεση».
    def _verdict_steps(self, st: dict, built: dict) -> list[list[dict]]:
        """The reviewer's live steps about the profile's exclusions, oldest first: each a list of events (a group
        is one step). An event belongs here when it was given from the exclusions review (`about`) or concerns an
        exclusion/suggestion the build knows."""
        keys = {e['key'] for e in (built.get('excluded') or []) + (built.get('suggested') or [])}
        live = [e for e in st['history'] if e['seq'] in st['applies'] and e['type'] in VERDICT_EVENTS
                and not str(e.get('actor', '')).startswith('system:')]
        hit = {e.get('group') or e['seq'] for e in live if e.get('about') == ABOUT or e.get('key') in keys}
        steps: dict = {}
        for e in live:
            g = e.get('group') or e['seq']
            if g in hit:
                steps.setdefault(g, []).append(e)
        return sorted(steps.values(), key=lambda step: step[-1]['seq'])

    @staticmethod
    def _verdict_of(step: list[dict], e: dict) -> str:
        if e.get('verdict') in ('confirmed', 'bounds', 'kept'):     # an excursus verdict says what it was
            return e['verdict']
        if len({x['type'] for x in step}) > 1:           # dismiss/withdraw + the reviewer's entity: new boundaries
            return 'bounds'
        return 'confirmed' if e['type'] in ('entity_confirm', 'entity_assign') else 'kept'                                     # «not this»: it stays in the output

    def _live_verdicts(self, b, st: dict, built: dict) -> tuple[list[dict], list[dict], list[dict]]:
        """(the built exclusions with `confirmed` as it is NOW and their `verdict`, the suggestions not yet judged,
        the verdicts listed newest first). Pending = neither confirmed nor judged."""
        latest: dict = {}
        for step in self._verdict_steps(st, built):
            for e in step:
                if e.get('key'):                          # (an excursus event of the step without one: not a verdict)
                    latest[e['key']] = (step, e)
        known = {e['key']: e for e in (built.get('excluded') or []) + (built.get('suggested') or [])}
        ents = {e['key']: e for e in (b.canonical() or {}).get('entities', [])}
        excursus = {x['id'] for x in st['excursus']}
        excluded = []
        for e in built.get('excluded') or []:
            hit = latest.get(e['key'])
            if hit:
                v = self._verdict_of(*hit)
                excluded.append({**e, 'confirmed': v == 'confirmed', 'verdict': v})
            elif e.get('confirmed') and e['key'] not in excursus and e['key'] not in st['assigned']:
                excluded.append({**e, 'confirmed': False, 'verdict': None})   # its confirmation was undone
            else:
                excluded.append({**e, 'verdict': None})
        decided, seen = [], set()
        for key, (step, e) in sorted(latest.items(), key=lambda kv: -kv[1][1]['seq']):
            if any(x['seq'] in seen for x in step) and len({x['type'] for x in step}) > 1:
                continue                                  # a boundary correction is ONE row (its two events)
            seen |= {x['seq'] for x in step}
            info = known.get(key) or ents.get(key) or next((known.get(x['key']) or ents.get(x['key']) for x in step
                                                             if known.get(x['key']) or ents.get(x['key'])), None) or {}
            decided.append({'key': key, 'verdict': self._verdict_of(step, e), 'seq': e['seq'], 'ts': e.get('ts'),
                            'type': info.get('type') or e.get('entity_type') or '', 'pages': info.get('pages') or [],
                            'first': info.get('first') or info.get('label') or '', 'n': info.get('n') or len(info.get('members') or []),
                            'members': info.get('members') or e.get('members') or []})
        suggested = [e for e in built.get('suggested') or [] if e['key'] not in latest]
        return excluded, suggested, decided

    def undo_exclusion(self, book_id: str, key: str | None = None, actor='reviewer') -> dict:
        """The undo of the exclusions review: the LAST step about the profile's exclusions (key None) — the same as
        the general undo, only focused — or the latest verdict on exclusion `key` (from a step that judged many
        at once, only that one; a boundary correction whole). Redo brings it back, like any undo."""
        b = self._book(book_id)
        built = (b.canonical() or {}).get('profile') or {}
        undone_keys: list[str] = []

        def pick(st):
            steps = self._verdict_steps(st, built)
            if key is None:
                step = steps[-1] if steps else []
                events = step
            else:
                step = next((s for s in reversed(steps) if any(e.get('key') == key for e in s)), [])
                events = step if len({e['type'] for e in step}) > 1 else [e for e in step if e.get('key') == key]
            undone_keys[:] = [e.get('key') for e in events]
            return [e['seq'] for e in events]

        out = b.events.undo_seqs(pick, actor)
        if not out:
            raise ServiceError('no exclusion verdict to undo')
        self._after_undo_redo(b, out, 'Αναίρεση απόφασης εξαίρεσης')
        return {'undone': out[0]['targets'], 'keys': undone_keys}

    def confirm_exclusions(self, book_id: str, keys: list[str] | None = None, actor='reviewer') -> dict:
        """Confirm the boundaries of automatic exclusions (all pending ones when keys is None): one undoable step."""
        pending = [e['key'] for e in self.output_profile(book_id)['excluded']
                   if not e['confirmed'] and not e['verdict'] and e.get('members')]
        keys = pending if keys is None else [k for k in keys if k in pending]
        if not keys:
            raise ServiceError('nothing to confirm')
        b = self._book(book_id)
        autos = {k: a for k in keys if (a := self._auto(b, k))}          # automatic excursus: they become yours
        if autos:
            self._append(b, *[{'type': 'excursus_set', 'excursus': a, 'key': k, 'about': ABOUT, 'verdict': 'confirmed'}
                              for k, a in autos.items()], actor=actor)
            self._reproject(b, f'Επιβεβαίωση {len(autos)} παρεκβάσεων')
        rest = [k for k in keys if k not in autos]
        out = self.confirm_entities(book_id, rest, None, actor, about=ABOUT) if rest else {'confirmed': 0}
        return {'confirmed': out['confirmed'] + len(autos)}

    def exclude_suggested(self, book_id: str, keys: list[str], actor='reviewer') -> dict:
        """A suggested exclusion (no independent boundary evidence) accepted by the reviewer: it is verified, so
        the profile excludes it."""
        built = (self._book(book_id).canonical() or {}).get('profile') or {}
        known = {e['key'] for e in built.get('suggested') or []}
        keys = [k for k in keys or [] if k in known]
        if not keys:
            raise ServiceError('unknown suggestion')
        return self.confirm_entities(book_id, keys, None, actor, about=ABOUT)

    def start_rag_context(self, book_id: str) -> dict:
        """Optional: situating sentences for the RAG chunks by the local model (application.rag_context), as a job."""
        if self.interpreter() is None:
            raise ServiceError('no local model installed (see config.NOTE_MODELS)')
        b = self._book(book_id)
        if not b.canonical():
            raise ServiceError('process the book first')
        return self.jobs.submit(b.id, 'rag-context', ['rag-context', '--book', str(b.dir)],
                                'Προτάσεις πλαισίου για τα κομμάτια RAG (τοπικό μοντέλο)', lane='heavy')

    def set_entity_bounds(self, book_id: str, key: str, first: str, last: str, actor='reviewer') -> dict:
        """Right kind, wrong boundaries: the entity becomes the segments from `first` to `last` in reading order (any
        pages; pipeline.entities.span). ONE undoable step: the detection is dismissed as wrongly bounded (a wrong
        outcome for its rule) — or the reviewer's own entity withdrawn — and the reviewer's entity of the same type
        takes its place; being the reviewer's, it is verified (the profile excludes it with no further question)."""
        b = self._book(book_id)
        x = self._excursus_of(b, key)
        if x is not None:                                 # an excursus removed by the profile: its new ends
            if (x.get('first'), x.get('last')) == (first, last):
                raise ServiceError('the boundaries are the same')
            ex = self.set_excursus(book_id, first, last, None, x.get('type'), x.get('name', ''), x.get('placement'), key, actor,
                                   tag={'key': key, 'about': ABOUT, 'verdict': 'bounds'})
            return {'key': key, 'first': ex['first'], 'last': ex['last']}
        known = {e['key']: e for e in (b.canonical() or {}).get('entities', [])}
        e = known.get(key)
        if e is None:
            raise ServiceError('unknown exclusion')
        try:
            members = entities.span(self._seen_list(b), first, last)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        if members == e['members']:
            raise ServiceError('the boundaries are the same')
        drop = {'type': 'entity_remove', 'key': key, 'about': ABOUT} if e['granularity'] == 'human' \
            else {'type': 'suggestion_dismiss', 'key': key, 'boundary': True, 'about': ABOUT}
        evs = self._append(b, drop, {'type': 'entity_assign', 'key': entities.human_key(members), 'members': members,
                                     'entity_type': e['type'], 'about': ABOUT}, actor=actor)      # one step (and inside a batch)
        self._reproject(b, f'Νέα όρια: {len(members)} τμήματα')
        return {'key': evs[1]['key'], 'members': members}

    def keep_in_output(self, book_id: str, key: str, actor='reviewer') -> dict:
        """«Not this»: the detection was wrong (not a question, a history note…) — it is dismissed and its segments
        stay in the output. (A right type with wrong boundaries: set_entity_bounds.) An excursus: removed."""
        if self._excursus_of(self._book(book_id), key) is not None:
            return self.remove_excursus(book_id, key, actor, tag={'key': key, 'about': ABOUT, 'verdict': 'kept'})
        return self.remove_entity(book_id, key, actor, about=ABOUT)

    @staticmethod
    def _excursus_of(b, key: str) -> dict | None:
        """The excursus (the reviewer's or automatic, as last built) an exclusion of the profile is made of."""
        return next((x for x in (b.canonical() or {}).get('excursus') or [] if x.get('id') == key), None)
