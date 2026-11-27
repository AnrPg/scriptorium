"""Use cases — the book's OUTPUT PROFILE (core.profiles; applied by pipeline.profile): choose it (one undoable,
book-wide event, optionally with per-book overrides), see what it excludes, and settle its automatic exclusions
with one confirmation each (or all at once) — the reviewer confirms BOUNDARIES, never re-reads the content.
Confirming = the existing entity verdicts (entity_confirm); "not this" = dismissing the detection (suggestion_dismiss),
which keeps the segments in the output and counts as a wrong outcome for the detector's rule."""
from __future__ import annotations

from ...core import profiles, scopes
from .base import ServiceBase, ServiceError


class ProfileService(ServiceBase):
    def output_profile(self, book_id: str) -> dict:
        """Current profile (as chosen and as last built), the available profiles, and what the profile excludes."""
        b = self._book(book_id)
        ev = b.events.state().get('output_profile') or {}
        built = (b.canonical() or {}).get('profile') or {}
        name = ev.get('profile') or profiles.DEFAULT
        st = b.events.state()
        return {'current': name, 'overrides': ev.get('overrides') or {},
                # decisions kept per profile (core.scopes): how many each profile has of its own
                'decisions': {k: st['scope_counts'].get(k, 0) for k in profiles.PROFILES},
                'built': built.get('name'), 'awaiting_rebuild': built.get('name', profiles.DEFAULT) != name,
                'available': [{'name': k, 'el': v['el'], 'en': v['en'], 'settings': profiles.effective(k)}
                              for k, v in profiles.PROFILES.items()],
                'excluded': built.get('excluded') or [], 'suggested': built.get('suggested') or [],
                'pending': built.get('pending', 0), 'excluded_segments': built.get('excluded_segments', 0),
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
        self._reproject(b, f'Προφίλ εξόδου: {profiles.PROFILES[profile]["el"]}')
        return ev

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

    def confirm_exclusions(self, book_id: str, keys: list[str] | None = None, actor='reviewer') -> dict:
        """Confirm the boundaries of automatic exclusions (all pending ones when keys is None): one undoable step."""
        built = (self._book(book_id).canonical() or {}).get('profile') or {}
        pending = [e['key'] for e in built.get('excluded') or [] if not e.get('confirmed') and e.get('members')]
        keys = pending if keys is None else [k for k in keys if k in pending]
        if not keys:
            raise ServiceError('nothing to confirm')
        return self.confirm_entities(book_id, keys, None, actor)

    def exclude_suggested(self, book_id: str, keys: list[str], actor='reviewer') -> dict:
        """A suggested exclusion (no independent boundary evidence) accepted by the reviewer: it is verified, so
        the profile excludes it."""
        built = (self._book(book_id).canonical() or {}).get('profile') or {}
        known = {e['key'] for e in built.get('suggested') or []}
        keys = [k for k in keys or [] if k in known]
        if not keys:
            raise ServiceError('unknown suggestion')
        return self.confirm_entities(book_id, keys, None, actor)

    def start_rag_context(self, book_id: str) -> dict:
        """Optional: situating sentences for the RAG chunks by the local model (application.rag_context), as a job."""
        if self.interpreter() is None:
            raise ServiceError('no local model installed (see config.NOTE_MODELS)')
        b = self._book(book_id)
        if not b.canonical():
            raise ServiceError('process the book first')
        return self.jobs.submit(b.id, 'rag-context', ['rag-context', '--book', str(b.dir)],
                                'Προτάσεις πλαισίου για τα κομμάτια RAG (τοπικό μοντέλο)', lane='heavy')

    def keep_in_output(self, book_id: str, key: str, actor='reviewer') -> dict:
        """«Not this»: the detection was wrong (not a question, a history note…) — it is dismissed and its segments
        stay in the output. (A right type with a wrong boundary is corrected by classifying the right segments.)"""
        return self.remove_entity(book_id, key, actor)
