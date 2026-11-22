"""Use cases — content review: decisions (commands), undo/redo, book rules, natural-language notes."""
from __future__ import annotations

import uuid

from ...core import proposals
from ...core import rules as book_rules
from ...core.model import block_id as block_id_of
from ...pipeline import engine_json
from ...pipeline.overlay import (
    COMMANDS,
    CommandError,
    decision_event,
    heading_level_events,
)
from .base import PROFILES, ServiceBase, ServiceError, _now  # noqa: F401
from .structure import _pad


class ReviewService(ServiceBase):
    def decide(self, book_id: str, block_id: str, action: str, actor: str = 'reviewer', **params) -> dict:
        """Record one reviewer decision: the command validates the request into an event (pipeline.overlay).
        Moving a segment that belongs to a group (automatic or the reviewer's) moves the WHOLE group, in its
        order (one undo)."""
        b = self._book(book_id)
        blk = self._block(b, block_id)
        c = b.canonical() or {}
        by_id = {x['id']: x for x in c.get('blocks', [])}
        try:
            if action == 'move_after_block' and (group := self._group_of(c, block_id)):
                evs = self._move_group(group, blk, params, by_id)
            else:
                if action == 'set_heading_level' and isinstance(params.get('level'), int):
                    decision_event(action, blk, params, by_id.get)          # validate the request first
                    evs = heading_level_events(c, {blk['id']: params['level']}, by_id.get)
                else:
                    evs = [decision_event(action, blk, params, by_id.get)]
        except CommandError as exc:
            raise ServiceError(str(exc)) from None
        out = b.events.append(*evs, actor=actor)
        self._reproject(b)
        return out[0]

    def move_segments(self, book_id: str, block_ids: list[str], after: str | None = None, before: str | None = None,
                      actor: str = 'reviewer') -> dict:
        """Move several segments (a selection, contiguous or not) to one place in the reading order of their page:
        they land together, in their current reading order, ONE undo. A selected member of a group brings its whole
        group (as a single move does)."""
        if bool(after) == bool(before):
            raise ServiceError('give exactly one of after / before')
        b = self._book(book_id)
        c = b.canonical() or {}
        blocks = c.get('blocks', [])
        by_id, pos = {x['id']: x for x in blocks}, {x['id']: i for i, x in enumerate(blocks)}
        target = after or before
        ids = [i for i in dict.fromkeys(block_ids or []) if i in by_id]
        if not ids or target not in by_id:
            raise ServiceError('unknown segment(s) (the page may have been rebuilt; reload)')
        for i in list(ids):
            ids += self._group_of(c, i) or []
        ids = sorted(dict.fromkeys(ids), key=pos.get)               # they keep their reading order
        if target in ids:
            raise ServiceError('the drop target is part of the selection')
        if {by_id[i]['page'] for i in ids} != {by_id[target]['page']}:
            raise ServiceError('a selection is moved within one page')
        try:
            evs = self._move_group(ids, by_id[ids[0]], {'after': after} if after else {'before': before}, by_id)
        except CommandError as exc:
            raise ServiceError(str(exc)) from None
        b.events.append(*evs, actor=actor)
        self._reproject(b, f'Μετακίνηση {len(ids)} τμημάτων')
        return {'moved': len(ids)}

    @staticmethod
    def _group_of(c: dict, block_id: str) -> list[str] | None:
        """Members (in order) of the largest group containing the segment, if any."""
        gs = [g for g in c.get('groups', []) if block_id in g['members']]
        return max(gs, key=lambda g: len(g['members']))['members'] if gs else None

    @staticmethod
    def _move_group(members, blk, params, by_id) -> list[dict]:
        target = params.get('after') or params.get('before')
        if target in members:
            raise CommandError('cannot move a group relative to one of its own members')
        first = decision_event('move_after_block', by_id[members[0]], params, by_id.get)
        evs = [first]
        for prev, m in zip(members, members[1:]):
            evs.append(decision_event('move_after_block', by_id[m], {'after': prev}, by_id.get))
        return evs

    def set_output(self, book_id: str, block_ids: list[str] | None, include: bool, group: str | None = None,
                   actor: str = 'reviewer') -> dict:
        """Leave segments OUT of the reader exports (or put them back): segments, a selection, or a whole group
        (its members). They stay in the book model — review, hierarchy, entities, JSON exports. ONE undo."""
        b = self._book(book_id)
        c = b.canonical() or {}
        by_id = {x['id']: x for x in c.get('blocks', [])}
        ids = list(block_ids or [])
        for i in list(ids):                              # a segment of a group goes out (or back) with its group
            ids += self._group_of(c, i) or []
        if group:
            g = next((x for x in c.get('groups', []) if x['id'] == group), None)
            if g is None:
                raise ServiceError('unknown group')
            ids += g['members']
        ids = [i for i in dict.fromkeys(ids) if i in by_id and bool(by_id[i].get('omitted')) == include]
        if not ids:
            return {'applied': 0}
        try:
            evs = [decision_event('set_output', by_id[i], {'include': bool(include)}, by_id.get) for i in ids]
        except CommandError as exc:
            raise ServiceError(str(exc)) from None
        b.events.append(*evs, actor=actor)
        self._reproject(b, f'{"Στην" if include else "Εκτός"} έξοδο: {len(evs)} τμήματα')
        return {'applied': len(evs)}

    def undo(self, book_id: str, actor='reviewer') -> list[dict]:
        b = self._book(book_id)
        out = b.events.undo(actor)
        if out:
            self._after_undo_redo(b, out, 'Αναίρεση')
        return out

    def redo(self, book_id: str, actor='reviewer') -> list[dict]:
        b = self._book(book_id)
        out = b.events.redo(actor)
        if out:
            self._after_undo_redo(b, out, 'Επανάληψη')
        return out

    def _after_undo_redo(self, b, out: list[dict], label: str):
        """Undo/redo restores exactly: a page-profile change selects which OCR run a page uses, so those pages are
        re-processed (a cache hit: every profile's raw output is kept) instead of only re-projected."""
        targets = {t for ev in out for t in ev.get('targets') or []}
        pages = sorted({int(e['page']) for e in b.events.load() if e['seq'] in targets and e['type'] == 'page_profile'})
        if pages:
            self.jobs.submit(b.id, 'process', ['process', '--book', str(b.dir), '--pages', ','.join(map(str, pages))],
                             f'{label}: επανεξέταση σελ. {", ".join(map(str, pages))}')
        else:
            self._reproject(b, label)

    # ---------------------------------------------------------------- review (book rules)
    def preview_rule(self, book_id: str, rule: dict) -> list[dict]:
        rule = book_rules.validate({**rule, 'id': rule.get('id') or 'preview'})
        c = self._book(book_id).canonical() or {}
        out = []
        for x in c.get('blocks', []):
            base = next((k.get('text') for k in x['candidates'] if k['engine'] == 'marker' and k.get('text')), None)
            if not base or x['chosen']['type'] != 'text':
                continue
            for m in book_rules.matches(base, rule):
                out.append({'block_id': x['id'], 'page': x['page'], 'n': m['n'], 'key': f'{x["id"]}#{m["n"]}',
                            'context': m['context'], 'excluded': f'{x["id"]}#{m["n"]}' in rule['exclude']})
        return out

    def add_rule(self, book_id: str, rule: dict, actor='reviewer') -> dict:
        b = self._book(book_id)
        rule = book_rules.validate({**rule, 'id': rule.get('id') or 'r' + uuid.uuid4().hex[:8]})
        evs = []
        if any(r['id'] == rule['id'] for r in b.events.state()['rules']):
            evs.append({'type': 'rule_remove', 'rule_id': rule['id']})       # edit = replace, atomically
        evs.append({'type': 'rule_add', 'rule': rule})
        b.events.append(*evs, actor=actor)
        self._reproject(b, 'Εφαρμογή κανόνα')
        return rule

    def remove_rule(self, book_id: str, rule_id: str, actor='reviewer') -> dict:
        b = self._book(book_id)
        if not any(r['id'] == rule_id for r in b.events.state()['rules']):
            raise ServiceError('no such rule')
        ev = b.events.append({'type': 'rule_remove', 'rule_id': rule_id}, actor=actor)[0]
        self._reproject(b, 'Αφαίρεση κανόνα')
        return ev

    # ---------------------------------------------------------------- review (content)
    # ---------------------------------------------------------------- natural-language notes -> proposals
    def interpret_note(self, book_id: str, block_id: str, note: str) -> dict:
        """Local model proposes ONE structured action for a note; nothing is applied here."""
        it = self.interpreter()
        if it is None:
            raise ServiceError('no local note model installed (see config.NOTE_MODELS)')
        if not note.strip():
            raise ServiceError('empty note')
        b = self._book(book_id)
        blk = self._block(b, block_id)
        seg = _segment_context(blk)
        labels = engine_json.label_catalog()
        system, user = proposals.prompt(seg, note, labels)
        crop = (b.dir / blk['source_crop']['path']).read_bytes() if blk.get('source_crop') else None
        try:
            out = it.complete_json(system, user, proposals.schema(labels), crop)
        except Exception as exc:                   # model failures are reported, never silently ignored
            raise ServiceError(f'note model failed: {exc}')
        p = proposals.normalize(out['json'])
        errors = proposals.validate(p, seg, labels)
        result = {'note': note, 'block_id': blk['id'], 'proposal': p, 'valid': not errors, 'errors': errors,
                  'description': proposals.describe(p, seg, labels) if not errors else None,
                  'model': out['model'], 'seconds': out['seconds']}
        b.log_proposal({**result, 'raw': out['raw'], 'ts': _now()})
        return result

    def check_proposal(self, book_id: str, block_id: str, proposal: dict) -> dict:
        """Re-validate a (possibly hand-edited) proposal and describe it."""
        blk = self._block(self._book(book_id), block_id)
        seg, labels = _segment_context(blk), engine_json.label_catalog()
        proposal = proposals.normalize(proposal)
        errors = proposals.validate(proposal, seg, labels)
        return {'valid': not errors, 'errors': errors,
                'description': proposals.describe(proposal, seg, labels) if not errors else None}

    def apply_proposal(self, book_id: str, block_id: str, proposal: dict, note: str, model: str,
                       actor: str = 'reviewer') -> dict:
        chk = self.check_proposal(book_id, block_id, proposal)
        if not chk['valid']:
            raise ServiceError('; '.join(chk['errors']))
        via = {'note': note[:2000], 'proposed_by': model}
        p = proposals.normalize(proposal)
        a = p['action']
        if a in COMMANDS:                              # a decision: the same command path as a manual one
            params = {k: p[k] for k in ('value', 'level') if p.get(k) is not None}
            return self.decide(book_id, block_id, a, actor, via=via, **params)
        if a == 'add_rule':
            return self.add_rule(book_id, {**p['rule'], 'note': f'από σημείωση: {note[:120]}'}, actor)
        if a == 'relabel':
            return self.relabel(book_id, block_id, p['label'], actor)
        if a == 'reocr':
            return self.report_wrong(book_id, block_id, note, actor)
        raise ServiceError('nothing to apply')

    def report_wrong(self, book_id: str, block_id: str, note: str = '', actor='reviewer') -> dict:
        """Any segment, flagged or not, can be reported wrong: the note is kept with the block and the segment
        is re-OCR'd as one reviewer region (layout detection disabled), replacing the original box."""
        b = self._book(book_id)
        blk = self._block(b, block_id)
        label = _engine_label(blk)
        evs = []
        target = blk['id']
        if not engine_json.region_spec(label)['image_only'] and blk.get('source') != 'region':
            region = {'id': 'g' + uuid.uuid4().hex[:8], 'page': blk['page'], 'bbox': _pad(blk, b), 'label': label}
            evs += [{'type': 'block_hide', 'block_id': blk['id']}, {'type': 'region_add', 'region': region}]
            target = block_id_of(b.canonical()['source']['sha256'], blk['page'], 'region:' + region['id'])
        evs.append({**decision_event('report_wrong', {'id': target, 'candidates': [], 'decisions': []},
                                     {'note': note}, lambda _id: None), 'reported_block': blk['id']})
        out = b.events.append(*evs, actor=actor)
        self._reproject(b, f'Νέο OCR (δηλώθηκε λάθος, σελ. {blk["page"]})')
        return {'events': out, 'new_block': target}

    def set_profile(self, book_id: str, page: int, profile: str, actor='reviewer') -> dict:
        b = self._book(book_id)
        if profile not in PROFILES:
            raise ServiceError(f'profile must be one of {PROFILES}')
        b.events.append({'type': 'page_profile', 'page': int(page), 'profile': profile}, actor=actor)
        return self.jobs.submit(b.id, 'process', ['process', '--book', str(b.dir), '--pages', str(page)],
                                f'Προσεκτική επανεξέταση σελ. {page}' if profile == 'diligent' else f'Επανεξέταση σελ. {page}')


def _segment_context(blk: dict) -> dict:
    ch = blk['chosen']
    return {'kind': blk.get('original_kind') or blk['kind'], 'engine_label': blk.get('engine_label'),
            'chosen_type': ch['type'], 'text': ch.get('text') or ch.get('withheld_text') or
            next((c.get('text') for c in blk['candidates'] if c.get('text')), None),
            'latex': ch.get('latex') or ch.get('withheld_latex'),
            'candidates': '\n'.join(f'- {c["engine"]}: {(c.get("text") or c.get("latex") or "")[:600]}'
                                    for c in blk['candidates'] if c.get('text') or c.get('latex')),
            'reasons': blk['reasons']}


def _engine_label(blk: dict) -> dict:
    """The engine label a block came with (re-OCR keeps the original tagging unless the reviewer changes it)."""
    raw = blk.get('engine_label') or 'Text'
    if ':' in raw:
        eng, name = raw.split(':', 1)
        return {'engine': eng, 'name': name}
    eng = 'docling' if blk.get('source') == 'docling' else 'marker'
    try:
        engine_json.region_spec({'engine': eng, 'name': raw})
        return {'engine': eng, 'name': raw}
    except ValueError:
        return {'engine': 'marker', 'name': 'Text'}

