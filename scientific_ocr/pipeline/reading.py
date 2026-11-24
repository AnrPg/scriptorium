"""Stage (publish): the READING MODEL — the canonical document linearised for readers (people, RAG, agents).

One list of nodes, serialised to Markdown (pipeline.markdown) and to HTML for the preview and the PDF
(pipeline.html_preview). It contains only the book's content and the boundaries a reader needs:
  heading   section title at its level
  para      body text; a paragraph the layout split around an insert or a page break is JOINED into one, and
            the inserts that interrupted it follow the paragraph (so no sentence is broken by a figure)
  equation  display formula (LaTeX)
  figure    an insert with its captions (pictures, drawings, charts, tables, captioned equations)
  image     source content kept as an image because it was not transcribed (nothing is dropped silently)
  box       box_start / box_end around a boxed element (example, exercise, definition…)
Export options (all optional; defaults reproduce the book as it is):
  exclude      {'example', 'exercise@margin', 'family:practice', …}: members of VERIFIED entities of these types (or type@placement)
               are left out, with the captions of excluded figures (pipeline.entities.excluded_blocks)
  move         {'figures', 'sidenote', 'example@margin', …}: content moved, grouped by kind, to the end of each
               unit — 'figures' = captioned visual elements with all their caption parts; '<type>[@placement]' =
               VERIFIED entities of that type (see _move_to_unit_end). Equations/tables/untranscribed text never
               move on their own.
  images       'keep' (default) | 'drop' — for moved figures: keep the picture or the captions only
  unit_level   heading level that closes a unit for 'move' (1 = chapter, 2 = section …)
Segments the reviewer OMITTED (decision set_output) are left out, with the captions of an omitted figure.
Nothing about provenance, review state, block ids or source pages: that lives in canonical.json and the
normalized JSON. Page furniture is excluded. Node 'blocks' (the canonical blocks behind a node) are for the
in-app preview only and are never serialised by the exporters.
"""
from __future__ import annotations

import re

from ..core.model import CAPTIONABLE_KINDS, FIGURE_KINDS, chosen_text, in_reader_output
from ..core.vocabulary import entity_label, family_of
from .entities import excluded_blocks, is_captioned_visual
from .groups import gaps, is_figure_group
from .merge import join_texts

LABELS = {'el': {'figure': 'Εικόνα', 'table': 'Πίνακας', 'figures': 'Εικόνες', 'source': 'απόσπασμα πηγής'},
          'en': {'figure': 'Figure', 'table': 'Table', 'figures': 'Figures', 'source': 'source excerpt'}}


def language(c: dict) -> str:
    greek = latin = 0
    for b in c['blocks'][:400]:
        for ch in (b['chosen'].get('text') or '')[:400]:
            if 'Ͱ' <= ch <= 'Ͽ' or 'ἀ' <= ch <= '῿':
                greek += 1
            elif 'a' <= ch.lower() <= 'z':
                latin += 1
    return 'el' if greek > latin else 'en'


def _image(b) -> str | None:
    return (b.get('source_crop') or {}).get('path')


def excerpt(n: dict) -> str | None:
    """The source crop that must travel WITH machine text that is not settled (display_crop: uncertain symbols,
    engine conflicts, unverified tables, chemical equations): every reader output shows the original next to it."""
    b = n['blocks'][0] if n.get('blocks') else None
    if b and b.get('display_crop') and b['chosen']['type'] in ('text', 'latex') and n['t'] in ('para', 'equation', 'figure', 'heading'):
        return _image(b)
    return None


def nodes(c: dict, pages: set | None = None, options: dict | None = None) -> list[dict]:
    opt = options or {}
    gone = excluded_blocks(c.get('entities') or [], set(opt.get('exclude') or ()))
    omitted = {b['id'] for b in c['blocks'] if b.get('omitted')}
    blocks = [b for b in c['blocks'] if in_reader_output(b) and b.get('flow') != 'furniture'
              and (pages is None or b['page'] in pages) and b['id'] not in gone
              and b['relations'].get('caption_for') not in gone and b['relations'].get('caption_for') not in omitted]
    by_id = {b['id']: b for b in blocks}
    pos = {b['id']: i for i, b in enumerate(blocks)}
    boxes = _boxes(c, pos, blocks)
    starts, ends = {}, {}
    for g in boxes:
        starts.setdefault(g['first'], []).append(g)
        ends.setdefault(g['last'], []).append(g)
    out, done = [], set()
    group_caps = {cid for g in boxes for cid in g['captions'] if cid in by_id}   # rendered at the end of their group

    def emit(b):
        if b['id'] in done:
            return
        for g in starts.get(b['id'], []):
            out.append({'t': 'box_start', 'type': g['type'], 'name': g['name'], 'figure': g['figure'], 'blocks': []})
        done.add(b['id'])
        if b['id'] in group_caps:
            pass                                          # the caption of a whole group: rendered at its end
        elif b['kind'] == 'caption' and b['relations'].get('caption_for') in by_id:
            pass                                          # rendered inside its figure
        elif b['relations'].get('continues_in') and not b['relations'].get('continues_from'):
            chain = [b]
            while (nxt := by_id.get(chain[-1]['relations'].get('continues_in'))) and nxt['id'] not in done:
                chain.append(nxt)
            texts = [chosen_text(x) for x in chain]
            if all(texts):
                out.append({'t': 'para', 'text': join_texts(texts, 'paragraph'), 'blocks': chain})
                done.update(x['id'] for x in chain)
                first, last = pos[chain[0]['id']], pos[chain[-1]['id']]
                for mid in blocks[first + 1:last]:        # what interrupted the paragraph follows it
                    emit(mid)
                for x in chain[1:]:
                    _close(x)
            else:
                out.append(_node(b, by_id))
        else:
            out.append(_node(b, by_id))
        _close(b)

    def _close(b):
        for g in ends.get(b['id'], []):
            caps = [by_id[c] for c in g['captions'] if c in by_id]
            if caps:
                out.append({'t': 'group_caption', 'figure': g['figure'], 'blocks': caps,
                            'captions': [{'text': chosen_text(x), 'role': x.get('caption_role') or 'text',
                                          'label': x.get('caption_label')} for x in caps if chosen_text(x)],
                            'caption_images': [_image(x) for x in caps if not chosen_text(x) and _image(x)]})
                done.update(x['id'] for x in caps)
            out.append({'t': 'box_end', 'type': g['type'], 'figure': g['figure'], 'blocks': []})

    for b in blocks:
        emit(b)
    out = [n for n in out if n]
    move = set(opt.get('move') or ())
    return _move_to_unit_end(out, move, opt.get('images', 'keep'), int(opt.get('unit_level') or 1), c) if move else out


def _entity_routes(ents: list[dict], move: set) -> dict[str, str]:
    """block id -> selection that moves it. Placement criteria ('<type>@margin', '*@margin', margin notes) are
    geometric, so detected entities qualify; a whole type anywhere is a classification: verified entities only."""
    route = {}
    for e in ents:
        margin = e['placement'] == 'margin'
        fam = family_of(e['type'])
        cands = [(f"{e['type']}@{e['placement']}", margin), (f"family:{fam}@{e['placement']}", margin), ('*@margin', margin),
                 (e['type'], e['type'] == 'sidenote'), (f'family:{fam}', False)]
        for sel, by_placement in cands:
            if sel in move and sel != 'figures' and (by_placement or e.get('verified')):
                for m in e['members']:
                    route.setdefault(m, sel)
                break
    return route


def _move_to_unit_end(nodes_: list[dict], move: set, images: str, level: int, c: dict) -> list[dict]:
    """Selected kinds of content leave the text flow and are placed, grouped by kind, at the end of each unit
    (closed by a heading of level <= `level`):
      'figures'           captioned visual elements with ALL their caption parts (entities.is_captioned_visual);
                          images='drop' keeps the captions only
      '<type>@margin', '*@margin', 'sidenote'
                          entities in the margin (placement is geometric: detected entities qualify), whole
      '<type>'            a type anywhere: VERIFIED entities of that type only (classification needs the reviewer)
    Equations, tables and untranscribed text never move unless the reviewer verified them as a selected entity."""
    lang = language(c)
    ents = c.get('entities') or []
    etype = {m: e['type'] for e in ents if e['granularity'] == 'block' for m in e['members'][:1]}
    etype.update({e['members'][0]: e['type'] for e in ents if e['granularity'] == 'human'})   # the reviewer's type wins
    route = _entity_routes(ents, move)
    by_id = {b['id']: b for b in c['blocks']}
    for g in c.get('groups') or []:                     # a group moves WHOLE or not at all (never half an example)
        cats = {route[m] for m in g['members'] if m in route}
        if len(cats) == 1:
            cat = cats.pop()
            for m in g['members']:
                route.setdefault(m, cat)

    grouped = {m for g in c.get('groups') or [] if len(g['members']) >= 2 for m in g['members']}

    def category(n) -> str | None:
        b = n['blocks'][0] if n['blocks'] else None
        if b is None:
            return None
        if n['t'] == 'figure' and 'figures' in move and b['id'] not in route and b['id'] not in grouped:
            # (a picture inside an example/exercise group stays with it unless the whole group moves)
            caps = [by_id[x] for x in b['relations'].get('captions', []) if x in by_id]
            if is_captioned_visual(b, etype.get(b['id']), caps):
                return 'figures'
        return route.get(b['id'])

    def title(cat) -> str:
        if cat == 'figures':
            return LABELS[lang]['figures']
        if cat == '*@margin':
            return 'Περιθώριο' if lang == 'el' else 'Margin'
        return entity_label(cat.split('@')[0], lang)

    out, pending, order = [], {}, []
    box_route = None

    def push(cat, n):
        if cat not in pending:
            pending[cat] = []
            order.append(cat)
        pending[cat].append(n)

    def flush():
        for cat in order:
            out.append({'t': 'heading', 'level': min(level + 1, 6), 'text': title(cat), 'generated': True, 'blocks': []})
            out.extend(pending[cat])
        pending.clear()
        order.clear()
    for i, n in enumerate(nodes_):
        if n['t'] == 'heading' and n['level'] <= level and box_route is None:
            flush()
        if n['t'] == 'box_start':                         # a box moves as a whole with its first member
            nxt = next((m for m in nodes_[i + 1:] if m['blocks']), None)
            box_route = category(nxt) if nxt else None
            (push(box_route, n) if box_route else out.append(n))
            continue
        if n['t'] == 'box_end':
            (push(box_route, n) if box_route else out.append(n))
            box_route = None
            continue
        cat = box_route or category(n)
        if cat:
            if cat == 'figures' and images == 'drop' and n['t'] == 'figure':
                n = {**n, 'image': None}
            push(cat, n)
        else:
            out.append(n)
    flush()
    return out


def _node(b, by_id) -> dict | None:
    ch = b['chosen']
    caps = [by_id[cid] for cid in b['relations'].get('captions', []) if cid in by_id]
    if b['kind'] == 'heading' and ch['type'] == 'text' and b.get('toc_entry'):    # a contents entry: text, not a section
        return {'t': 'para', 'text': chosen_text(b), 'blocks': [b]}
    if b['kind'] == 'heading' and ch['type'] == 'text':
        return {'t': 'heading', 'level': b.get('heading_level') or 1, 'text': re.sub(r'^#+\s*', '', chosen_text(b)), 'blocks': [b]}
    if b['kind'] in FIGURE_KINDS or b['kind'] in CAPTIONABLE_KINDS or caps or b['kind'] == 'caption':
        body = None
        if ch['type'] == 'latex':
            body = {'latex': ch['latex']}
        elif ch['type'] == 'text' and b['kind'] != 'caption':
            body = {'text': chosen_text(b)}
        cap_blocks = caps if b['kind'] != 'caption' else [b]
        return {'t': 'figure', 'kind': 'table' if b['kind'] == 'table' else 'figure',
                'image': None if body or b['kind'] == 'caption' else _image(b), 'body': body,
                'captions': [{'text': chosen_text(x), 'role': x.get('caption_role') or 'text', 'label': x.get('caption_label')}
                             for x in cap_blocks if chosen_text(x)],
                # a caption without usable text (image-only, unreadable, withheld) keeps its crop: never lost
                'caption_images': [_image(x) for x in cap_blocks if not chosen_text(x) and _image(x)],
                'blocks': [b] + (caps if b['kind'] != 'caption' else [])}
    if ch['type'] == 'latex':
        return {'t': 'equation', 'latex': ch['latex'], 'blocks': [b]}
    if ch['type'] == 'text' and chosen_text(b):
        return {'t': 'para', 'text': chosen_text(b), 'blocks': [b]}
    if _image(b):
        return {'t': 'image', 'image': _image(b), 'blocks': [b]}
    return None


def _boxes(c, pos, flow_blocks) -> list[dict]:
    """Non-overlapping, contiguous boxed elements (groups) in reading order; nested, crossing or broken groups are
    not boxed (broken ones are reported by the document stage)."""
    out, end, ids = [], -1, list(pos)
    cands, kinds = [], {b['id']: b['kind'] for b in flow_blocks}
    for g in c.get('groups', []):
        idx = sorted(pos[m] for m in g['members'] if m in pos)
        if len(idx) >= 2:
            cands.append((idx[0], idx[-1], g))
    for a, z, g in sorted(cands, key=lambda x: (x[0], -x[1])):
        if a <= end or gaps(g['members'], flow_blocks):
            continue
        figure = is_figure_group(g, kinds)
        out.append({'first': ids[a], 'last': ids[z], 'type': g['type'], 'name': g.get('name') or '', 'id': g.get('id'),
                    'figure': figure, 'captions': list(g.get('captions') or [])})
        end = z
    return out
