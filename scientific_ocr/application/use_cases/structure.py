"""Use cases — page/document structure: regions, merges, groups, suggestions, model proposals on the hierarchy."""
from __future__ import annotations

import re
import uuid

from ...adapters.storage import Book, read_json
from ...core import proposals
from ...core.geometry import union
from ...core.vocabulary import GROUP_TYPES
from ...pipeline import engine_json
from ...pipeline import structure_review as sr
from ...pipeline.inline import inline_html
from ...pipeline.merge import join_texts
from ...pipeline.overlay import CommandError, heading_level_events
from .base import PROFILES, ServiceBase, ServiceError, _now  # noqa: F401
from .views import _dims


class StructureService(ServiceBase):
    # ---------------------------------------------------------------- review (page structure)
    def add_region(self, book_id: str, page: int, bbox: list, label: dict, replaces: list[str] | None = None,
                   actor='reviewer', replaces_region: str | None = None) -> dict:
        """A reviewer region (re-OCR'd as one segment of `label`). It may replace engine boxes (hidden) and/or an
        earlier reviewer region (removed) — all in ONE undoable step."""
        b = self._book(book_id)
        try:
            engine_json.region_spec(label or {})
        except ValueError as exc:
            raise ServiceError(str(exc))
        w, h = _dims(b, page)
        x0, y0, x1, y1 = [float(v) for v in bbox]
        box = [max(0.0, min(x0, x1)), max(0.0, min(y0, y1)), min(w, max(x0, x1)), min(h, max(y0, y1))]
        if box[2] - box[0] < 6 or box[3] - box[1] < 6:
            raise ServiceError('region too small')
        region = {'id': 'g' + uuid.uuid4().hex[:8], 'page': int(page), 'bbox': [round(v, 1) for v in box],
                  'label': {'engine': label['engine'], 'name': label['name']}}
        evs = [{'type': 'block_hide', 'block_id': bid} for bid in (replaces or [])]
        if replaces_region:
            if not any(r['id'] == replaces_region for r in b.events.state()['regions']):
                raise ServiceError('no such region')
            evs.append({'type': 'region_remove', 'region_id': replaces_region})
        evs.append({'type': 'region_add', 'region': region})
        self._append(b, *evs, actor=actor)
        self._reproject(b, f'Νέο OCR περιοχής (σελ. {page})')
        return region

    def relabel(self, book_id: str, block_id: str, label: dict, actor='reviewer') -> dict:
        """Change a segment's tag as ONE undoable action: the old engine box is hidden or the old reviewer region
        removed, and a region with the new label takes its place (re-OCR'd as that type)."""
        b = self._book(book_id)
        blk = self._block(b, block_id)
        try:
            engine_json.region_spec(label or {})
        except ValueError as exc:
            raise ServiceError(str(exc))
        region = {'id': 'g' + uuid.uuid4().hex[:8], 'page': blk['page'], 'bbox': [round(v, 1) for v in blk['geometry']['bbox']],
                  'label': {'engine': label['engine'], 'name': label['name']}}
        old = {'type': 'region_remove', 'region_id': blk['region_id']} if blk.get('source') == 'region' \
            else {'type': 'block_hide', 'block_id': blk['id']}
        self._append(b, old, {'type': 'region_add', 'region': region}, actor=actor)
        self._reproject(b, f'Νέα ετικέτα (σελ. {blk["page"]})')
        return region

    def merge_preview(self, book_id: str, block_ids: list[str], label: dict | None = None) -> dict:
        """What a join-merge WOULD produce, with the same joining rules as the merge stage (nothing is changed)."""
        b = self._book(book_id)
        blks = [self._block(b, i) for i in dict.fromkeys(block_ids)]
        try:
            spec = engine_json.region_spec(label) if label else {'kind': 'paragraph'}
        except ValueError as exc:
            raise ServiceError(str(exc))
        kind = spec['kind'] if spec['kind'] != 'unknown' else 'paragraph'
        texts = [x['chosen'].get('text') or x['chosen'].get('latex') for x in blks]
        return {'kind': kind, 'text': join_texts(texts, kind) if texts and all(texts) else None,
                'html': inline_html(join_texts(texts, kind)) if texts and all(texts) else None,
                'missing_text': [x['id'] for x, t in zip(blks, texts) if not t],
                'same_page': len({x['page'] for x in blks}) == 1}

    def merge(self, book_id: str, block_ids: list[str], label: dict, mode: str = 'join', actor='reviewer') -> dict:
        """Turn several segments of one page into ONE entity.
        join: texts joined in the order given (the reviewer's arrangement; keeps each part's reviewed text);
        reocr: one region over the union of the parts, OCR'd as a single block of type `label`."""
        b = self._book(book_id)
        blks = [self._block(b, i) for i in dict.fromkeys(block_ids)]
        if len(blks) < 2:
            raise ServiceError('select at least two segments')
        if len({x['page'] for x in blks}) != 1:
            raise ServiceError('segments must be on the same page (merge per page)')
        try:
            engine_json.region_spec(label or {})
        except ValueError as exc:
            raise ServiceError(str(exc))
        lab = {'engine': label['engine'], 'name': label['name']}
        if mode == 'reocr':
            bbox = [round(v, 1) for v in union([x['geometry']['bbox'] for x in blks])]
            return self.add_region(book_id, blks[0]['page'], bbox, lab,
                                   [x['id'] for x in blks if x.get('source') != 'region'], actor)
        if mode != 'join':
            raise ServiceError('mode must be join or reocr')
        m = {'id': 'm' + uuid.uuid4().hex[:8], 'page': blks[0]['page'], 'members': [x['id'] for x in blks], 'label': lab,
             'ordered': True}
        self._append(b, {'type': 'merge_add', 'merge': m}, actor=actor)
        self._reproject(b, f'Ένωση {len(blks)} τμημάτων (σελ. {m["page"]})')
        return m

    def group(self, book_id: str, block_ids: list[str], gtype: str, name: str = '', actor='reviewer') -> dict:
        """Annotate segments (any pages) as belonging together; members keep their identity."""
        b = self._book(book_id)
        ids = list(dict.fromkeys(block_ids))
        for i in ids:
            self._block(b, i)
        if len(ids) < 2:
            raise ServiceError('select at least two segments')
        gtype = (gtype or '').strip()
        if not gtype or len(gtype) > 60:
            raise ServiceError('group type is required (max 60 characters)')
        g = {'id': 'G' + uuid.uuid4().hex[:7], 'type': gtype, 'name': (name or '')[:200], 'members': ids, 'ordered': True}
        self._append(b, {'type': 'group_add', 'group': g}, actor=actor)
        self._reproject(b, f'Ομάδα «{GROUP_TYPES.get(gtype, gtype)}» ({len(ids)} τμήματα)')
        return g

    def update_group(self, book_id: str, group_id: str, members: list[str], actor='reviewer') -> dict:
        """Change a group's members atomically (remove + add, one undo)."""
        b = self._book(book_id)
        old = next((g for g in b.events.state()['groups'] if g['id'] == group_id), None)
        if old is None:
            raise ServiceError('unknown group')
        ids = list(dict.fromkeys(members))
        evs = [{'type': 'group_remove', 'group_id': group_id}]
        if len(ids) >= 2:
            evs.append({'type': 'group_add', 'group': {**old, 'members': ids, 'ordered': True}})
        self._append(b, *evs, actor=actor)
        self._reproject(b)
        return {**old, 'members': ids}

    def ungroup(self, book_id: str, group_id: str, actor='reviewer') -> dict:
        b = self._book(book_id)
        _require(any(g['id'] == group_id for g in b.events.state()['groups']), 'no such reviewer group')
        ev = self._append(b, {'type': 'group_remove', 'group_id': group_id}, actor=actor)[0]
        self._reproject(b, 'Διάλυση ομάδας')
        return ev

    def unmerge(self, book_id: str, merge_id: str, actor='reviewer') -> dict:
        b = self._book(book_id)
        _require(any(m['id'] == merge_id for m in b.events.state()['merges']), 'no such merged segment')
        ev = self._append(b, {'type': 'merge_remove', 'merge_id': merge_id}, actor=actor)[0]
        self._reproject(b, 'Διάσπαση ενωμένου τμήματος')
        return ev

    def remove_region(self, book_id: str, region_id: str, actor='reviewer') -> dict:
        b = self._book(book_id)
        _require(any(r['id'] == region_id for r in b.events.state()['regions']), 'no such region')
        ev = self._append(b, {'type': 'region_remove', 'region_id': region_id}, actor=actor)[0]
        self._reproject(b)
        return ev

    def hide_block(self, book_id: str, block_id: str, actor='reviewer') -> dict:
        b = self._book(book_id)
        self._block(b, block_id)
        ev = self._append(b, {'type': 'block_hide', 'block_id': block_id}, actor=actor)[0]
        self._reproject(b)
        return ev

    def remove_segments(self, book_id: str, block_ids: list[str], actor='reviewer') -> dict:
        """Remove WRONG segments (noise, duplicates, bad boxes) — one or many, ONE undoable step. Per kind: an engine
        box is hidden, a reviewer region is removed, a merged segment is dissolved and its parts hidden. Content that
        is right but unwanted in the text belongs to set_output ("out of the output"), not here."""
        b = self._book(book_id)
        by_id = self._blocks_seen(b)
        ids = list(dict.fromkeys(block_ids or []))
        missing = [i for i in ids if i not in by_id]
        _require(ids and not missing, f'unknown segment(s): {", ".join(missing)}' if missing else 'no segments')
        evs = []
        for i in ids:
            blk = by_id[i]
            if blk.get('source') == 'region' and blk.get('region_id'):
                evs.append({'type': 'region_remove', 'region_id': blk['region_id']})
            elif blk.get('source') == 'merge' and blk.get('merge_id'):
                evs.append({'type': 'merge_remove', 'merge_id': blk['merge_id']})
                evs += [{'type': 'block_hide', 'block_id': p['id']} for p in blk.get('merged_from') or []]
            else:
                evs.append({'type': 'block_hide', 'block_id': i})
        self._append(b, *evs, actor=actor)
        self._reproject(b, f'Αφαίρεση {len(ids)} λάθος τμημάτων' if len(ids) > 1 else 'Αφαίρεση λάθος τμήματος')
        return {'removed': len(ids)}

    def show_block(self, book_id: str, block_id: str, actor='reviewer') -> dict:
        b = self._book(book_id)
        _require(block_id in b.events.state()['hidden'], 'this segment is not hidden')
        ev = self._append(b, {'type': 'block_show', 'block_id': block_id}, actor=actor)[0]
        self._reproject(b)
        return ev

    def accept_suggestion(self, book_id: str, page: int, key: str, actor='reviewer') -> dict:
        """Accept a rule suggestion of a page (by its stable key)."""
        info = self.page(book_id, page)['info']
        s = next((x for x in info.get('suggestions') or [] if x['key'] == key), None)
        if s is None:
            raise ServiceError('suggestion no longer exists')
        return self.apply_structure(book_id, s, actor)

    def apply_structure(self, book_id: str, op: dict, actor='reviewer') -> dict:
        """Execute one structure operation (from a rule, the model, or the reviewer)."""
        kind = op.get('kind') or op.get('op')
        if kind in ('merge_join', 'merge'):
            return self.merge(book_id, op['members'], op['label'], 'join', actor)
        if kind == 'merge_reocr':
            b = self._book(book_id)
            page = self._block(b, op['members'][0])['page']
            return self.add_region(book_id, page, op['bbox'], op.get('label') or {'engine': 'marker', 'name': 'Text'},
                                   op['members'], actor)
        if kind == 'figure_assemble':                 # one picture region over the pieces; the caption then attaches
            b = self._book(book_id)
            page = self._block(b, op['members'][0])['page']
            return self.add_region(book_id, page, op['bbox'], op.get('label') or {'engine': 'marker', 'name': 'Picture'},
                                   op['members'], actor)
        if kind == 'group':
            return self.group(book_id, op['members'], op['type'], op.get('name', ''), actor)
        raise ServiceError(f'unknown operation {kind!r}')

    def accept_all(self, book_id: str, page: int | None = None, actor='reviewer') -> dict:
        """Accept every rule suggestion of one page (or of the whole book). Merges stay reviewer-triggered: this
        is the reviewer pressing one button, not an automatic action."""
        b = self._book(book_id)
        c = b.canonical() or {}
        done, skipped, used = 0, 0, set()
        with self._batch() as evs:                    # ONE undoable step, one rebuild
            for info in c.get('pages', []):
                if page is not None and info['page'] != page:
                    continue
                for s in info.get('suggestions') or []:
                    members = set(s.get('members') or [])
                    if members & used:                # overlaps a suggestion accepted just before: stale now
                        skipped += 1
                        continue
                    try:
                        self.apply_structure(book_id, s, actor)
                    except ServiceError:
                        skipped += 1
                        continue
                    used |= members
                    done += 1
        if evs:
            b.events.append(*evs, actor=actor)
            self._reproject(b, f'Αποδοχή {done} προτάσεων δομής')
        return {'applied': done, 'skipped': skipped}

    def set_group_end(self, book_id: str, group_id: str, last_block: str, actor='reviewer') -> dict:
        """Correct the EXTENT of a group: its members become every segment of the reading flow from its first member
        to `last_block` (shorter or longer than before). An automatic group becomes the reviewer's own (which wins
        over automatic grouping); a reviewer group is updated. One undoable step."""
        c = self._book(book_id).canonical() or {}
        g = next((x for x in c.get('groups', []) if x['id'] == group_id), None)
        if g is None:
            raise ServiceError('unknown group')
        flow = [b['id'] for b in c.get('blocks', []) if b.get('flow') != 'furniture' and not b.get('excluded')]
        if g['members'][0] not in flow or last_block not in flow or flow.index(last_block) <= flow.index(g['members'][0]):
            raise ServiceError('the end must come after the first member')
        members = flow[flow.index(g['members'][0]):flow.index(last_block) + 1]
        if g.get('source') == 'auto':
            return self.group(book_id, members, g['type'], g.get('name') or '', actor)
        return self.update_group(book_id, g['id'], members, actor)

    # ---------------------------------------------------------------- model second pass on group extents
    def start_extent_review(self, book_id: str) -> dict:
        if self.interpreter() is None:
            raise ServiceError('no local model installed (see config.NOTE_MODELS)')
        b = self._book(book_id)
        if not b.canonical():
            raise ServiceError('process the book first')
        return self.jobs.submit(b.id, 'extents', ['propose-extents', '--book', str(b.dir)],
                                'Έλεγχος έκτασης ομάδων με το μοντέλο', lane='heavy')

    def extent_proposals(self, book_id: str) -> dict:
        """Open proposals: not rejected, the group still automatic and still ending where it ended."""
        from ...pipeline import extent_review
        b = self._book(book_id)
        res = read_json(b.dir / extent_review.FILE) or {}
        groups = {g['id']: g for g in (b.canonical() or {}).get('groups', [])}
        dismissed = set(b.events.state()['dismissed'])
        open_ = [p for p in res.get('proposals', []) if p['key'] not in dismissed and p['group'] in groups
                 and groups[p['group']]['members'][-1] == p['current_last']]
        meta = {k: res.get(k) for k in ('status', 'error', 'built_utc', 'canonical_built', 'model', 'samples', 'asked',
                                        'agreed_with_current', 'invalid_answers', 'failed')}
        return {**meta, 'proposals': sorted(open_, key=lambda p: (-p['votes'], p['page']))}

    def accept_extent_proposal(self, book_id: str, key: str, actor='reviewer') -> dict:
        p = next((x for x in self.extent_proposals(book_id)['proposals'] if x['key'] == key), None)
        if p is None:
            raise ServiceError('no such open proposal')
        ev = self.set_group_end(book_id, p['group'], p['proposed_last'], actor)
        b = self._book(book_id)
        if self.interpreter() is not None:            # re-run on the new projection (reprojects first)
            self.jobs.submit(b.id, 'extents', ['propose-extents', '--book', str(b.dir), '--reproject'],
                             'Έλεγχος έκτασης ομάδων (μετά την αποδοχή)', lane='heavy')
        return ev

    def keep_group(self, book_id: str, key: str, gtype: str | None = None, actor='reviewer') -> dict:
        """Turn an automatic group into the reviewer's own (optionally with another type)."""
        c = self._book(book_id).canonical() or {}
        g = next((x for x in c.get('groups', []) if x.get('key') == key and x.get('source') == 'auto'), None)
        if g is None:
            raise ServiceError('automatic group no longer exists')
        return self.group(book_id, g['members'], gtype or g['type'], g['name'], actor)

    def propose_structure(self, book_id: str, page: int) -> dict:
        """Local model proposes groups/merges for a page; validated; NEVER applied here."""
        it = self.interpreter()
        if it is None:
            raise ServiceError('no local model installed (see config.NOTE_MODELS)')
        b = self._book(book_id)
        pg = self.page(book_id, page)
        blocks = {x['id']: x for x in pg['blocks']}
        if len(blocks) < 2:
            raise ServiceError('nothing to structure on this page')
        segs = [{'id': x['id'], 'kind': x.get('original_kind') or x['kind'],
                 'text': re.sub(r'\s+', ' ', (x.get('chosen') or {}).get('text') or (x.get('chosen') or {}).get('latex')
                                or f'[{x["kind"]} image]')[:220]} for x in pg['blocks']]
        labels = engine_json.label_catalog()
        system, user = proposals.structure_prompt(segs, GROUP_TYPES, labels)
        image = self.page_image(book_id, page, 1100).read_bytes()
        try:
            out = it.complete_json(system, user, proposals.structure_schema(list(blocks), labels, list(GROUP_TYPES)),
                                   image)
        except Exception as exc:
            raise ServiceError(f'model failed: {exc}')
        ops = []
        for op in out['json'].get('operations', []):
            errs = proposals.validate_structure(op, blocks)
            ops.append({**op, 'kind': 'merge_join' if op['op'] == 'merge' else 'group', 'valid': not errs,
                        'errors': errs, 'source': 'model'})
        res = {'page': page, 'understanding': out['json'].get('understanding'), 'operations': ops,
               'model': out['model'], 'seconds': out['seconds']}
        b.log_proposal({**res, 'kind': 'structure', 'ts': _now(), 'raw': out['raw']})
        return res

    # ---------------------------------------------------------------- model second pass on the hierarchy
    def start_structure_review(self, book_id: str) -> dict:
        if self.interpreter() is None:
            raise ServiceError('no local model installed (see config.NOTE_MODELS)')
        b = self._book(book_id)
        if not b.canonical():
            raise ServiceError('process the book first')
        return self.jobs.submit(b.id, 'structure', ['propose-structure', '--book', str(b.dir)],
                                'Έλεγχος δομής με το μοντέλο', lane='heavy')

    def structure_proposals(self, book_id: str) -> dict:
        """Open model proposals: not rejected, block still present, and still different from the current
        structure (an accepted proposal disappears because the structure now matches it)."""
        b = self._book(book_id)
        res = read_json(b.dir / sr.FILE) or {}
        c = b.canonical() or {}
        by_id = {x['id']: x for x in c.get('blocks', [])}
        dismissed = set(b.events.state()['dismissed'])
        open_ = []
        for p in res.get('proposals', []):
            blk = by_id.get(p['block_id'])
            if not blk or p['key'] in dismissed:
                continue
            role, lvl = sr.current(blk)
            if (role, lvl) == (p['proposed']['role'], p['proposed']['level']) or (p['proposed']['level'] == 0 and role == 'not_heading'):
                continue
            if blk.get('heading_level_source') == 'human' and lvl == p['proposed']['level']:
                continue                               # accepted (a decision sets the level; the role explains it)
            open_.append({**p, 'current': {'role': role, 'level': lvl}})
        open_.sort(key=lambda p: (-p['votes'], p['page']))
        meta = {k: res.get(k) for k in ('status', 'error', 'built_utc', 'canonical_built', 'model', 'samples', 'asked',
                                        'agreed_with_current', 'invalid_answers', 'failed')}
        return {**meta, 'proposals': open_}

    def accept_structure_proposal(self, book_id: str, key: str, members: list[str] | None = None,
                                  actor='reviewer') -> dict:
        """Accept a model proposal for its whole same-style group (or a subset): one set_heading_level decision
        per heading, ONE undo. Then the pass re-runs on the new projection (the change may make other headings
        ambiguous or settle them)."""
        p = next((x for x in self.structure_proposals(book_id)['proposals'] if x['key'] == key), None)
        if p is None:
            raise ServiceError('no such open proposal')
        b = self._book(book_id)
        by_id = {x['id']: x for x in (b.canonical() or {}).get('blocks', [])}
        group = [m for m in (members or p.get('members') or [p['block_id']]) if m in by_id]
        via = {'proposed_by': p.get('model'), 'note': 'δομή: ' + ' | '.join(r for r in p['reasons'] if r)[:500],
               'votes': f'{p["votes"]}/{p["samples"]}', 'group': len(group)}
        try:
            evs = heading_level_events(b.canonical() or {}, {m: p['decision']['level'] for m in group}, by_id.get, via)
        except CommandError as exc:
            raise ServiceError(str(exc)) from None
        out = self._append(b, *evs, actor=actor)
        self._reproject(b)                                # the accepted change shows now (light lane)…
        if self.interpreter() is not None:                # …then the model re-checks the new projection
            self.jobs.submit(b.id, 'structure', ['propose-structure', '--book', str(b.dir), '--reproject'],
                             'Έλεγχος δομής με το μοντέλο (μετά την αποδοχή)', lane='heavy')
        return out[0]

    def dismiss_suggestion(self, book_id: str, key: str, actor='reviewer') -> dict:
        b = self._book(book_id)
        ev = self._append(b, {'type': 'suggestion_dismiss', 'key': key}, actor=actor)[0]
        self._reproject(b)
        return ev


def _pad(blk: dict, b: Book, pt: float = 4.0) -> list:
    w, h = _dims(b, blk['page'])
    x0, y0, x1, y1 = blk['geometry']['bbox']
    return [round(max(0, x0 - pt), 1), round(max(0, y0 - pt), 1), round(min(w, x1 + pt), 1), round(min(h, y1 + pt), 1)]


def _require(ok: bool, msg: str):
    """A removal must name something that exists: an unknown id would record a no-op event that then takes the
    reviewer's next undo step."""
    if not ok:
        raise ServiceError(msg)
