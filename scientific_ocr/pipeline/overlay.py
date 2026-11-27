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
from ..core.model import CAPTIONABLE_KINDS, EQUATION_KINDS, MAX_HEADING_LEVEL, content_hash
from ..core.reasons import flag
from ..core.symbols import strip_math_delimiters
from . import tables


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


TEXT_KINDS = {'paragraph', 'heading', 'caption'}


def _restore_kind(b, content: str) -> None:
    """A segment kept as an excerpt only because no reading was safe (pipeline.page: empty / single-engine) gets
    its content from the reviewer: it is again what it was detected as (a paragraph, a heading, an equation…), so
    every stage reads it as such — a continuation resumes it, entries and excursus see its text. A region that was
    never typed (coverage, full page) stays an excerpt with text."""
    orig = b.get('original_kind')
    if b['kind'] == 'excerpt' and orig in (EQUATION_KINDS if content == 'latex' else TEXT_KINDS):
        b['kind'] = orig
        b.pop('original_kind', None)


def _approve_candidate(b, d, ctx):
    c = next((c for c in b['candidates'] if c['engine'] == d.get('engine')), None)
    if c is None:
        raise Stale(f'candidate {d.get("engine")!r} no longer exists')
    _restore_kind(b, 'latex' if b.get('original_kind') in EQUATION_KINDS and c.get('latex') else 'text')
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
        _restore_kind(b, field)
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
    # any text segment may be a caption (paragraph, heading, list item, an excerpt the reviewer transcribed…),
    # but not a figure/table/formula itself
    if f is None or b['kind'] in CAPTIONABLE_KINDS | EQUATION_KINDS or f is b or f['page'] != b['page']:
        raise Stale('caption/figure missing')
    b['kind'] = 'caption'
    b['relations'].pop('caption_group', None)           # a figure's caption, not a group's
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
    b['relations'].pop('caption_group', None)


def _associate_group_caption(b, d, ctx):
    """The caption of a whole GROUP (e.g. several figures, each with its own caption, and one caption for all).
    The group is resolved by the groups stage (pipeline.groups); a group that no longer exists drops the link."""
    if b['kind'] in CAPTIONABLE_KINDS | EQUATION_KINDS:
        raise Stale('a figure/table/formula cannot be a caption')
    old = ctx['by_id'].get(b['relations'].get('caption_for'))
    if old:
        old['relations']['captions'] = [x for x in old['relations'].get('captions', []) if x != b['id']]
    b['kind'] = 'caption'
    b['relations'].update(caption_for=None, inferred=False, caption_group=d['group_id'])


def _v_group_caption(blk, p, find):
    if not isinstance(p.get('group_id'), str) or not p['group_id']:
        raise CommandError('group_id required')
    return {'group_id': p['group_id']}


def _set_caption_role(b, d, ctx):
    if b['kind'] != 'caption' or d.get('role') not in ('title', 'text'):
        raise Stale('not a caption or invalid role')
    b['caption_role'] = d['role']
    b['caption_role_source'] = 'human'


def _set_output(b, d, ctx):
    """In or out of the READER exports (core.model.in_reader_output); the segment stays in the model."""
    if d['include']:
        b.pop('omitted', None)
        b['kept'] = {'seq': d.get('seq'), 'actor': d.get('actor')}   # also overrides the output profile's exclusion
    else:
        b.pop('kept', None)
        b['omitted'] = {'seq': d.get('seq'), 'actor': d.get('actor'), 'note': d.get('note', '')}


def _set_flow(b, d, ctx):
    """The reviewer's reading-flow role (pipeline.flow): 'main' body text, 'insert' (placed into the text, like a
    figure), or 'auto' (back to the automatic role)."""
    b['flow_override'] = None if d['flow'] == 'auto' else d['flow']


def _v_flow(blk, p, find):
    if p.get('flow') not in ('main', 'insert', 'auto'):
        raise CommandError('flow must be main, insert or auto')
    return {'flow': p['flow']}


def _set_table_layout(b, d, ctx):
    """How a table is read (pipeline.tables): 'rows' / 'cols' = a list by rows / by columns — with `key` (the key
    column / row) a list with levels, without it a flat list of the cells; 'table' = a table, 'auto' = the hint."""
    b['table_layout'] = None if d['layout'] == 'auto' else d['layout']
    b['table_key'] = d.get('key') if d['layout'] in ('rows', 'cols') else None


def _v_table_layout(blk, p, find):
    if p.get('layout') not in ('rows', 'cols', 'table', 'auto'):
        raise CommandError('layout must be rows, cols, table or auto')
    if blk.get('kind') != 'table':
        raise CommandError('only a table can be read as a list')
    key = p.get('key')
    if key is None or p['layout'] not in ('rows', 'cols'):
        return {'layout': p['layout']}
    if not isinstance(key, int) or isinstance(key, bool) or key < 0:
        raise CommandError('key must be a column / row number (0-based)')
    ch = blk.get('chosen') or {}
    eng = None if ch.get('type') == 'text' else _table_reading(blk)
    html = ch.get('text') if eng is None else next(c['text'] for c in blk['candidates'] if c['engine'] == eng)
    ax = tables.axes(html or '')
    if ax is None or key >= len(ax[p['layout']]):
        raise CommandError(f'the table has no {"column" if p["layout"] == "rows" else "row"} {key + 1}')
    return {'layout': p['layout'], 'key': key}


def _table_reading(blk) -> str | None:
    """The engine whose reading has a table grid (the one the list hint was computed on, else Marker first)."""
    hint = (blk.get('table_list') or {}).get('candidate')
    engines = [c['engine'] for c in blk.get('candidates') or [] if '<table' in (c.get('text') or '').lower()]
    return hint if hint in engines else next((e for e in engines if e == 'marker'), engines[0] if engines else None)


def _i_table_layout(blk, p):
    """Choosing how a table READS is a judgement of its content: a list needs text (a table kept as an image adopts
    the engine reading first), and the reading in force is confirmed (it is what the reviewer just judged).
    'auto' hands the choice back to the machine and confirms nothing."""
    layout = p.get('layout')
    if layout == 'auto':
        return (), ()
    if (blk.get('chosen') or {}).get('type') != 'text':
        if layout == 'table':
            return (), ()                                  # stays an image: nothing to read
        eng = _table_reading(blk)
        if eng is None:
            raise CommandError('no engine read this table as text: it can only stay an image')
        return [('approve_candidate', {'engine': eng})], ()     # a value decision: approves what it sets
    if blk.get('review') != 'approved' and not has_human_value(blk):
        return (), [('approve', {})]
    return (), ()


def _set_continuation(b, d, ctx):
    """Whether this paragraph CONTINUES the previous body paragraph (interrupted by an insert or a page break):
    True links them (one paragraph in linear outputs), False never links them, 'auto' = the automatic test."""
    b['continues_override'] = None if d['continues'] == 'auto' else bool(d['continues'])


def _v_continuation(blk, p, find):
    if p.get('continues') not in (True, False, 'auto'):
        raise CommandError('continues must be true, false or "auto"')
    return {'continues': p['continues']}


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
    `apply(block, event, ctx)` replays it on every build (raises Stale when the machine output changed);
    `implies(block, params) -> (before, after)`: decisions the action entails, as [(action, params)] recorded with it
    as ONE step (one undo), whatever the entry point (one segment, a selection, any button). The server, not the
    interface, expands them, so every path behaves alike."""
    apply: Callable
    validate: Callable = _v_none
    implies: Callable = lambda blk, p: ((), ())

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
    'set_flow': Command(_set_flow, _v_flow), 'set_continuation': Command(_set_continuation, _v_continuation),
    'set_table_layout': Command(_set_table_layout, _v_table_layout, _i_table_layout),
    'associate_group_caption': Command(_associate_group_caption, _v_group_caption),
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


def reconfirm_event(blk: dict, find) -> dict:
    """«Approve as it is» on a segment whose content is the reviewer's OWN decision: that decision is recorded again,
    unchanged, now. A decision is asked again when it was taken under other conditions (e.g. an image kept for an
    output with pictures, before a text-only profile); confirming it must be one action, and replaying the same
    command keeps every rule of the overlay (stale detection, what it sets) exactly as for the original."""
    d = next((x for x in reversed(blk.get('decisions') or []) if x.get('action') in VALUE_DECISIONS), None)
    if d is None:
        raise CommandError('no decision of yours to confirm')
    ch = blk.get('chosen') or {}
    params = {'replace_text': {'value': ch.get('text')}, 'replace_latex': {'value': ch.get('latex')},
              'approve_candidate': {'engine': d.get('engine')}}.get(d['action'], {})
    if any(v is None for v in params.values()):
        raise CommandError('this decision cannot be confirmed as it is (its value is not available): edit it instead')
    return {**decision_event(d['action'], blk, params, find), 'reconfirm': True}


def decision_events(action: str, blk: dict, params: dict, find) -> list[dict]:
    """The validated events of `action` on `blk` WITH the decisions it implies (Command.implies), in order: what
    every entry point records, as one step."""
    cmd = COMMANDS.get(action)
    if cmd is None:
        raise CommandError(f'unknown action {action!r}')
    if action == 'approve' and has_human_value(blk) and blk.get('review') in ('required', 'recommended', 'unresolved'):
        return [reconfirm_event(blk, find)]          # your decision is asked again: confirm it as it is
    # (a decision of yours that is NOT asked again cannot be "approved over": _v_approve refuses it)
    main = decision_event(action, blk, params, find)
    before, after = cmd.implies(blk, params)
    return ([decision_event(a, blk, p, find) for a, p in before] + [main]
            + [decision_event(a, blk, p, find) for a, p in after])


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


def apply(blocks: list[dict], decisions: list[dict], superseded=lambda d: False) -> dict:
    """Effective content decision per block (see select_content); structure decisions apply in sequence.
    `superseded(d)`: the decision's segment (or target) was removed by a LATER reviewer action — such a decision
    is recorded as superseded, never flagged stale (nothing goes back to review because of it)."""
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
            b.setdefault('decisions', []).append({k: d[k] for k in ('seq', 'ts', 'actor', 'action', 'via', 'engine', 'reconfirm',
                                                                   'scope')
                                                  if k in d})
            applied.append(d['seq'])
        except Stale as exc:
            rec = {'seq': d['seq'], 'block_id': d['block_id'], 'action': d['action'], 'why': str(exc)}
            if superseded(d):
                ignored.append({**rec, 'why': 'superseded by a later reviewer action'})
                continue
            stale.append(rec)
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
