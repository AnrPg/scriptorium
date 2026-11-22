"""Stage: apply reviewer decisions (Command pattern) onto the freshly assembled machine blocks.

Each action is a Command in COMMANDS: its validator turns a request into an event, its handler replays it. Decisions are replayed on every build, so they must name the
machine output they judged: `prev_hash` = hash of the block's machine choice (before rules); `approve`
additionally pins `approved_hash` (the exact content approved). If the machine output changed since (re-OCR,
new engine version, a different layout), the decision is STALE: it is not applied, the block is re-flagged
for review, and the build continues. One stale decision never blocks a whole book.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..core.decisions import CONTENT_ACTIONS, VALUE_ACTIONS, VALUE_DECISIONS
from ..core.geometry import covered_by
from ..core.model import EQUATION_KINDS, MAX_HEADING_LEVEL, content_hash
from ..core.reasons import flag
from ..core.symbols import strip_math_delimiters


class Stale(Exception):
    pass


def _provenance(d):
    return {'provenance': f'human:{d.get("actor", "reviewer")}@{d.get("ts", "")}'}


def _approve(b, d, ctx):
    if d.get('approved_hash') and d['approved_hash'] != content_hash(b['chosen']):
        raise Stale('approved content changed')
    b['review'] = 'approved'
    if b['chosen']['type'] in ('text', 'latex'):
        b['display_crop'] = False     # the reviewer compared it with the source image: the text is the content now


def _approve_candidate(b, d, ctx):
    c = next((c for c in b['candidates'] if c['engine'] == d.get('engine')), None)
    if c is None:
        raise Stale(f'candidate {d.get("engine")!r} no longer exists')
    if b['kind'] in EQUATION_KINDS:
        lat = c.get('latex')
        b['chosen'] = {'type': 'latex', 'latex': lat if isinstance(lat, str) else ' '.join(lat or [])}
    else:
        b['chosen'] = {'type': 'text', 'text': c.get('text', '')}
    b['chosen'].update(_provenance(d), approved_candidate=c['engine'])
    b['review'], b['display_crop'] = 'approved', False


def _replace(field):
    def handler(b, d, ctx):
        value = strip_math_delimiters(d['value']) if field == 'latex' else d['value']   # events are immutable;
        b['chosen'] = {'type': 'latex' if field == 'latex' else 'text', field: value, **_provenance(d)}  # normalise on read
        b['review'], b['display_crop'] = 'approved', False
    return handler


def _image_only(b, d, ctx):
    b['chosen'] = {'type': 'image_only', **_provenance(d)}
    b['review'], b['display_crop'] = 'image_only', True


def _unreadable(b, d, ctx):
    b['chosen'] = {'type': 'unresolved', **_provenance(d)}
    b['review'], b['display_crop'] = 'unresolved', True


def _move_after(b, d, ctx):
    """Reading-order change: place the block right after `after` (or right before `before`) on its page."""
    target = ctx['by_id'].get(d.get('after') or d.get('before'))
    if target is None or target['page'] != b['page'] or target is b:
        raise Stale('move target missing')
    blocks = ctx['blocks']
    blocks.remove(b)
    blocks.insert(blocks.index(target) + (0 if d.get('before') else 1), b)


def _revert(b, d, ctx):
    """Back to the machine result (earlier value decisions are not selected, see select_content)."""


def _associate_caption(b, d, ctx):
    f = ctx['by_id'].get(d.get('figure_id'))
    if f is None or b['kind'] not in ('caption', 'paragraph', 'heading') or f['page'] != b['page']:
        raise Stale('caption/figure missing')
    b['kind'] = 'caption'
    old = ctx['by_id'].get(b['relations'].get('caption_for'))
    if old:
        old['relations']['captions'] = [x for x in old['relations'].get('captions', []) if x != b['id']]
    b['relations'].update(caption_for=f['id'], inferred=False)
    f['relations'].setdefault('captions', [])
    if b['id'] not in f['relations']['captions']:
        f['relations']['captions'].append(b['id'])
    _order_captions(f, ctx)
    if d.get('role'):
        b['caption_role'] = d['role']


def _order_captions(f, ctx):
    pos = {b['id']: i for i, b in enumerate(ctx['blocks'])}
    f['relations']['captions'] = [c for c in f['relations']['captions'] if c in pos]     # stale ids dropped
    f['relations']['captions'].sort(key=pos.get)


def _detach_caption(b, d, ctx):
    f = ctx['by_id'].get(b['relations'].get('caption_for'))
    if f:
        f['relations']['captions'] = [x for x in f['relations'].get('captions', []) if x != b['id']]
    b['relations'].update(caption_for=None, inferred=False)


def _set_caption_role(b, d, ctx):
    if b['kind'] != 'caption' or d.get('role') not in ('title', 'text'):
        raise Stale('not a caption or invalid role')
    b['caption_role'] = d['role']
    b['caption_role_source'] = 'human'


def _set_output(b, d, ctx):
    """In or out of the READER exports (core.model.in_reader_output); the segment stays in the model."""
    if d['include']:
        b.pop('omitted', None)
    else:
        b['omitted'] = {'seq': d.get('seq'), 'actor': d.get('actor'), 'note': d.get('note', '')}


def _v_output(blk, p, find):
    if not isinstance(p.get('include'), bool):
        raise CommandError('include must be true or false')
    return {'include': p['include']}


def _report_wrong(b, d, ctx):
    b.setdefault('human_notes', []).append({'seq': d.get('seq'), 'actor': d.get('actor'), 'note': d.get('note', '')})
    flag(b, 'HUMAN_REPORTED_WRONG', note=d.get('note', ''))


def _set_heading_level(b, d, ctx):
    lvl = int(d.get('level', 1))
    if lvl == 0:                                        # "not a heading": demote to body text
        if b['kind'] == 'heading':
            b['kind'] = 'paragraph'
        b.pop('heading_level', None)
    else:
        if b['kind'] not in ('heading', 'paragraph'):
            raise Stale('only text segments can become headings')
        b['kind'], b['heading_level'] = 'heading', max(1, min(MAX_HEADING_LEVEL, lvl))
    b['heading_level_source'] = 'human'


def _link_reference(b, d, ctx):
    """Applied by the references stage (pipeline.references) via overrides; validated here."""
    if d.get('target') and d['target'] not in ctx['by_id']:
        raise Stale('reference target no longer exists')


# ---------------------------------------------------------------- validation (request -> event fields)
class CommandError(ValueError):
    """The reviewer's request is invalid for this segment (nothing is recorded)."""


def has_human_value(blk: dict) -> bool:
    """True when a reviewer's value (edit, candidate choice, image-only, unreadable) is the segment's content."""
    return any(d.get('action') in VALUE_DECISIONS for d in blk.get('decisions') or [])


def _v_none(blk, p, find):
    return {}


def _v_approve(blk, p, find):
    if has_human_value(blk):
        raise CommandError('this segment already has your decision; edit it or revert it instead of approving')
    return {'approved_hash': blk.get('auto_hash')}


def _v_revert(blk, p, find):
    if not has_human_value(blk):
        raise CommandError('nothing to revert: the segment shows the machine result')
    return {}


def _v_replace(field):
    def v(blk, p, find):
        if not str(p.get('value') or '').strip():
            raise CommandError('empty correction')
        return {'value': strip_math_delimiters(p['value']) if field == 'latex' else p['value']}
    return v


def _v_candidate(blk, p, find):
    if not any(c['engine'] == p.get('engine') for c in blk['candidates']):
        raise CommandError('no such candidate')
    return {'engine': p['engine']}


def _v_move(blk, p, find):
    key = 'before' if p.get('before') else 'after'
    target = find(p.get(key) or '')
    if target is None or target['page'] != blk['page'] or target['id'] == blk['id']:
        raise CommandError('move within the same page, relative to another segment')
    return {key: target['id']}


def _v_associate(blk, p, find):
    if find(p.get('figure_id') or '') is None:
        raise CommandError('no such figure/insert')
    return {'figure_id': p['figure_id'], **({'role': p['role']} if p.get('role') in ('title', 'text') else {})}


def _v_link(blk, p, find):
    if not isinstance(p.get('n'), int):
        raise CommandError('link_reference needs the mention index n')
    return {'n': p['n'], 'target': p.get('target') or None}


def _v_role(blk, p, find):
    if p.get('role') not in ('title', 'text'):
        raise CommandError('role must be title or text')
    return {'role': p['role']}


def _v_level(blk, p, find):
    if not isinstance(p.get('level'), int) or not 0 <= p['level'] <= MAX_HEADING_LEVEL:
        raise CommandError(f'level must be 0 (not a heading) .. {MAX_HEADING_LEVEL}')
    return {'level': p['level']}


def _v_report(blk, p, find):
    return {'note': str(p.get('note') or '')[:2000]}


@dataclass(frozen=True)
class Command:
    """One reviewer action: `validate(block, params, find) -> event fields` (raises CommandError) records it;
    `apply(block, event, ctx)` replays it on every build (raises Stale when the machine output changed)."""
    apply: Callable
    validate: Callable = _v_none

    def __call__(self, b, d, ctx):
        return self.apply(b, d, ctx)


COMMANDS: dict[str, Command] = {
    'approve': Command(_approve, _v_approve), 'approve_candidate': Command(_approve_candidate, _v_candidate),
    'replace_text': Command(_replace('text'), _v_replace('text')),
    'replace_latex': Command(_replace('latex'), _v_replace('latex')),
    'mark_image_only': Command(_image_only), 'mark_unreadable': Command(_unreadable),
    'move_after_block': Command(_move_after, _v_move), 'associate_caption': Command(_associate_caption, _v_associate),
    'report_wrong': Command(_report_wrong, _v_report), 'set_heading_level': Command(_set_heading_level, _v_level),
    'revert': Command(_revert, _v_revert), 'detach_caption': Command(_detach_caption),
    'set_caption_role': Command(_set_caption_role, _v_role), 'link_reference': Command(_link_reference, _v_link),
    'set_output': Command(_set_output, _v_output),
}


def decision_event(action: str, blk: dict, params: dict, find) -> dict:
    """A validated decision event for `action` on `blk` (find(id) -> block or None)."""
    cmd = COMMANDS.get(action)
    if cmd is None:
        raise CommandError(f'unknown action {action!r}')
    ev = {'type': 'decision', 'block_id': blk['id'], 'action': action, **cmd.validate(blk, params, find)}
    if action in CONTENT_ACTIONS:
        ev['prev_hash'] = blk.get('machine_hash')     # what the decision judged (stale detection)
    if params.get('via'):
        ev['via'] = params['via']                     # natural-language note + the model that proposed it
    return ev


def select_content(decisions: list[dict]) -> tuple[dict, list[dict]]:
    """Per block: the effective content decision. The latest VALUE decision sets the content; an `approve`
    counts only when no value decision is in force (it confirms the machine version and can never replace a
    human value). A `revert` clears earlier value decisions. Returns ({block_id: seq}, superseded decisions)."""
    value, approve, superseded = {}, {}, []
    for d in decisions:
        if d['action'] in VALUE_ACTIONS:
            value[d['block_id']] = d
        elif d['action'] == 'approve':
            approve[d['block_id']] = d
    chosen = {}
    for bid in set(value) | set(approve):
        v, a = value.get(bid), approve.get(bid)
        if v and v['action'] != 'revert':
            chosen[bid] = v['seq']
            if a:
                superseded.append({'seq': a['seq'], 'block_id': bid, 'action': 'approve',
                                   'why': 'a human value is in force; approval of the machine version ignored'})
        elif a and (not v or a['seq'] > v['seq']):
            chosen[bid] = a['seq']
        elif v:
            chosen[bid] = v['seq']                     # revert
    return chosen, superseded


def apply(blocks: list[dict], decisions: list[dict]) -> dict:
    """Effective content decision per block (see select_content); structure decisions apply in sequence."""
    by_id = {b['id']: b for b in blocks}
    ctx = {'blocks': blocks, 'by_id': by_id}
    latest_content, ignored = select_content(decisions)
    applied, stale = [], []
    for d in decisions:
        if d['action'] in CONTENT_ACTIONS and latest_content.get(d['block_id']) != d['seq']:
            continue
        b = by_id.get(d['block_id'])
        try:
            if d['action'] not in COMMANDS:
                raise Stale(f'unknown action {d["action"]!r}')
            if b is None:
                raise Stale('block no longer exists (layout or OCR changed)')
            if d['action'] in CONTENT_ACTIONS and d.get('prev_hash') and d['prev_hash'] != b.get('machine_hash'):
                raise Stale('machine output changed since the decision')
            COMMANDS[d['action']](b, d, ctx)
            b.setdefault('decisions', []).append({k: d[k] for k in ('seq', 'ts', 'actor', 'action', 'via') if k in d})
            applied.append(d['seq'])
        except Stale as exc:
            stale.append({'seq': d['seq'], 'block_id': d['block_id'], 'action': d['action'], 'why': str(exc)})
            if b is not None:
                flag(b, 'DECISION_STALE', seq=d['seq'], why=str(exc))
    resolved = _propagate(blocks)
    return {'applied': applied, 'stale': stale, 'resolved_by_overlap': resolved, 'ignored': ignored}


SETTLED = ('approved', 'image_only', 'unresolved')


def _propagate(blocks: list[dict], min_cover: float = 0.8) -> list[dict]:
    """A human decision is ground truth for its region: open segments of the same page lying (almost) entirely
    inside a human-settled segment (e.g. a coverage excerpt or a second engine's box over the same print) are
    resolved by it instead of being reviewed again. Recorded per block (relations.resolved_by + reason)."""
    settled = [b for b in blocks if b['review'] in SETTLED and b.get('decisions')]
    out = []
    for b in blocks:
        if b['review'] not in ('required', 'recommended') or b.get('decisions'):
            continue
        by = next((s for s in settled if s['page'] == b['page'] and s['id'] != b['id']
                   and covered_by(b['geometry']['bbox'], s['geometry']['bbox']) >= min_cover), None)
        if by:
            b['review'] = 'approved'
            b['relations']['resolved_by'] = by['id']
            flag(b, 'RESOLVED_BY_HUMAN_DECISION', by=by['id'])
            out.append({'block_id': b['id'], 'resolved_by': by['id']})
    return out


def heading_level_events(c: dict, changes: dict[str, int], find, via: dict | None = None) -> list[dict]:
    """set_heading_level for {heading id: new level} PLUS its consequence: moving a heading by N levels moves its
    SECTION, so the sub-headings whose level the REVIEWER had set (relative to the parent as it was) move by the
    same N. Sub-headings that are themselves in `changes` are not moved twice; inferred ones follow in the
    projection (pipeline.sections.shift_subtrees). One list of events = one undoable step."""
    evs = []
    missing = [bid for bid in changes if find(bid) is None]
    if missing:
        raise CommandError(f'unknown segment(s): {", ".join(missing)} (the page may have been rebuilt; reload)')
    for bid, level in changes.items():
        b = find(bid)
        evs.append({**decision_event('set_heading_level', b, {'level': level}, find), **({'via': via} if via else {})})
    carried: dict[str, int] = {}
    for bid, level in changes.items():
        b = find(bid)
        old = b.get('heading_level') if b['kind'] == 'heading' else None
        if not level or not old or level == old:
            continue
        for d in c.get('blocks', []):
            if d['id'] in changes or d['kind'] != 'heading' or d.get('heading_level_source') != 'human' \
                    or bid not in (d.get('section_path') or []) or d['id'] == bid or not d.get('heading_level'):
                continue
            carried[d['id']] = carried.get(d['id'], d['heading_level']) + (level - old)
    for did, new in carried.items():
        d = find(did)
        new = max(1, min(MAX_HEADING_LEVEL, new))
        if new != d['heading_level']:
            evs.append({**decision_event('set_heading_level', d, {'level': new}, find), 'via': {'carried_with_section': True}})
    return evs
